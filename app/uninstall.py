"""Uninstall software (determine the silent command and run it as a deployment).

Order of detection:
  1. own, stored uninstall command for this software (uninstall_overrides)
  2. MSI product code            → msiexec /x {GUID} /qn /norestart
  3. QuietUninstallString       → stored by the vendor
  4. known installer types   → Inno Setup, NSIS, Mozilla/Zotero (helper.exe), Office C2R, sdbinst ...
  5. winget uninstall           → only if the winget assignment is safe
  6. UninstallString unchanged (marked for review)
Software from HKEY_USERS (per-user installation) is uninstalled in the context of the respective user.
"""
import fnmatch
import re

from . import db, deploy, util

GUID_RE = re.compile(r"^\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}$")
MSI_IN_STR = re.compile(r"msiexec(\.exe)?\s+.*?/[IiXx]\s*(\{[0-9A-Fa-f-]{36}\})", re.I)
SID_IN_PATH = re.compile(r"^(S-1-5-21(?:-\d+)+)\\", re.I)
SUCCESS = "0,3010,1641,1605,1614"

SCHEMA = """
CREATE TABLE IF NOT EXISTS uninstall_overrides(
  id INTEGER PRIMARY KEY, pattern TEXT NOT NULL, command TEXT NOT NULL, run_as TEXT DEFAULT 'auto',
  created_by TEXT, created_at TEXT);
"""


def init():
    db.conn().executescript(SCHEMA)


def _has_switch(cmd, *switches):
    return any(re.search(r"(^|\s)" + re.escape(sw) + r"(\s|$)", cmd, re.I) for sw in switches)


def _override(sw):
    name = (sw.get("name") or "").strip()
    for o in db.q("SELECT * FROM uninstall_overrides ORDER BY length(pattern) DESC"):
        if fnmatch.fnmatch(name.lower(), o["pattern"].lower()):
            return o
    return None


def user_sid(sw):
    """SID of the user for installations from HKEY_USERS, otherwise None."""
    if (sw.get("hive") or "").upper() != "HKEY_USERS":
        return None
    m = SID_IN_PATH.match(sw.get("reg_path") or "")
    return m.group(1) if m else None


def _expand(cmd, sw):
    """Replace {GUID} etc. in the stored command."""
    return (cmd.replace("{UninstallString}", sw.get("uninstall_string") or "")
               .replace("{QuietUninstallString}", sw.get("quiet_uninstall") or "")
               .replace("{ProductCode}", sw.get("product_code") or "")
               .replace("{InstallLocation}", (sw.get("install_location") or "").rstrip("\\")))


def _known(un):
    """Known uninstallers → (method, command, review?) or None."""
    low = un.lower()
    if re.search(r"unins\d{3}\.exe", low):
        cmd = un if _has_switch(un, "/verysilent") else un + " /VERYSILENT /SUPPRESSMSGBOXES /NORESTART"
        return "Inno Setup", cmd, False
    if "officeclicktorun.exe" in low:
        return "Office Click-to-Run", (un if "displaylevel=" in low else un + " DisplayLevel=False"), False
    if "sdbinst.exe" in low:
        return "Kompatibilitätsdatenbank (sdbinst)", (un if _has_switch(un, "-q") else re.sub(r"(?i)sdbinst\.exe", "sdbinst.exe -q", un, count=1)), False
    if re.search(r"\\uninstall\\helper\.exe", low):
        # Firefox, Thunderbird, Zotero and other Mozilla-based programs (NSIS)
        return "Mozilla-Uninstaller (helper.exe)", (un if _has_switch(un, "/s") else un + " /S"), False
    if re.search(r"\\installer\\setup\.exe.*--uninstall", low):
        # Chromium-based browsers (Chrome, Edge, Brave, Vivaldi)
        cmd = un
        for sw in ("--force-uninstall",):
            if sw not in low:
                cmd += " " + sw
        return "Chromium-Uninstaller", cmd, False
    if re.search(r"(uninst(all)?|uninstaller|uninst-?\w*|au_)\.exe", low):
        return "NSIS / Standard-Uninstaller", (un if _has_switch(un, "/s") else un + " /S"), not _has_switch(un, "/s")
    if re.search(r"\.exe\"?\s+.*(/uninstall|/x\b|-uninstall)", low) and not _has_switch(un, "/quiet", "/q", "/s", "/silent"):
        # e.g. WiX bundles and many bootstrappers: "/uninstall /quiet"
        return "Bootstrapper (/uninstall)", un + " /quiet /norestart", True
    return None


def command_for(sw):
    """Determines the silent uninstall. Returns: dict(method, command, review, run_as, override_id)."""
    un = (sw.get("uninstall_string") or "").strip()
    quiet = (sw.get("quiet_uninstall") or "").strip()
    code = (sw.get("product_code") or "").strip()
    sid = user_sid(sw)
    base = {"run_as": "user" if sid else "system", "user_sid": sid}
    # 1) own command
    o = _override(sw)
    if o:
        r = {**base, "method": f"eigener Befehl („{o['pattern']}“)", "command": _expand(o["command"], sw), "review": False,
             "override_id": o["id"]}
        if o["run_as"] in ("system", "user"):
            r["run_as"] = o["run_as"]
        return r
    # 2) MSI
    m = MSI_IN_STR.search(un)
    if GUID_RE.match(code) and (not un or "msiexec" in un.lower()):
        return {**base, "method": "MSI (Produktcode)", "command": f"msiexec.exe /x {code} /qn /norestart", "review": False}
    if m:
        return {**base, "method": "MSI (aus Deinstallationsbefehl)", "command": f"msiexec.exe /x {m.group(2)} /qn /norestart",
                "review": False}
    # 3) vendor
    if quiet:
        return {**base, "method": "QuietUninstallString (Hersteller)", "command": quiet, "review": False}
    # 4) known installers
    k = _known(un) if un else None
    if k and not k[2]:
        return {**base, "method": k[0], "command": k[1], "review": False}
    # 5) winget (only with a safe assignment)
    wid = sw.get("winget_id")
    if wid and sw.get("winget_sure", 1):
        return {**base, "method": "winget uninstall", "command": f"winget uninstall --id {wid} --exact --silent",
                "review": False, "winget_id": wid}
    if k:
        return {**base, "method": k[0], "command": k[1], "review": True}
    if not un:
        return {**base, "method": "kein Deinstallationsbefehl", "command": "", "review": True}
    return {**base, "method": "Deinstallationsbefehl – Silent-Schalter prüfen!", "command": un, "review": True}


def _script(command, hive, reg_path, cleanup):
    key = f"Registry::{hive}\\{reg_path}" if hive and reg_path else ""
    return f"""$cmd = {util.psq(command)}
$key = {util.psq(key)}
$cleanup = {'$true' if cleanup else '$false'}
$code = 0
if ($cmd) {{
  Write-Output ('> ' + $cmd)
  $p = Start-Process -FilePath 'cmd.exe' -ArgumentList ('/c "' + $cmd + '"') -Wait -PassThru -WindowStyle Hidden
  $code = $p.ExitCode
  Write-Output ('Exitcode: ' + $code)
}}
$ok = ($code -eq 0 -or $code -eq 3010 -or $code -eq 1641 -or $code -eq 1605 -or $code -eq 1614)
if ($key -and $ok -and $cmd) {{
  # Some uninstallers (e.g. NSIS) restart themselves as a copy and return immediately – wait for the removal
  for ($i = 0; $i -lt 60 -and (Test-Path -LiteralPath $key); $i++) {{ Start-Sleep -Seconds 3 }}
}}
if ($key -and (Test-Path -LiteralPath $key)) {{
  if ($cleanup) {{
    Remove-Item -LiteralPath $key -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $key) {{ Write-Output 'Registry-Eintrag konnte nicht entfernt werden.' }} else {{ Write-Output 'Verwaister Registry-Eintrag entfernt.' }}
    if ($ok -or -not $cmd) {{ $code = 0 }} else {{ Write-Output ('Der Uninstaller ist fehlgeschlagen (Exitcode ' + $code + ') – die Software ist evtl. noch installiert.') }}
  }} elseif ($ok) {{
    Write-Output 'Hinweis: Der Registry-Eintrag ist nach 3 Minuten noch vorhanden – evtl. wartet der Uninstaller auf eine Eingabe.'
  }}
}} elseif ($key -and $ok) {{ Write-Output 'Registry-Eintrag entfernt.' }}
exit $code
"""


def package_for(sw, command=None, run_as=None, cleanup=False):
    info = command_for(sw)
    cmd = (command if command is not None else info["command"]).strip()
    if not cmd and not cleanup:
        raise ValueError(f"Für „{sw['name']}“ ist kein Deinstallationsbefehl bekannt.")
    name = f"Deinstallation: {sw['name']}"
    run_as = run_as if run_as in ("system", "user", "deploy") else info["run_as"]
    if run_as == "deploy" and not command:
        # Command from the client's registry (UninstallString) - never as the domain-wide service account
        run_as = "system"
    forced = False
    from_registry = command is None or (command or "").strip() == (info.get("command") or "").strip()
    if info.get("user_sid") and (from_registry or "{" in (command or "") or info.get("override_id")):
        # Entry from a user's profile (HKEY_USERS\SID): any user may write it themselves.
        # NEVER run its uninstall command/product code as SYSTEM or the service account - otherwise a
        # standard user could have code started with full rights via a forged entry.
        run_as, forced = "user", True
    m = re.match(r"^winget uninstall --id ([A-Za-z0-9.\-_+]+)", cmd)
    if m and not cleanup:
        pkg = deploy.adhoc_winget(m.group(1), "uninstall")
        pkg.update({"name": name, "run_as": run_as, "run_user_sid": info.get("user_sid"), "run_as_forced": forced})
        return pkg
    # Registry entry in the user context: HKEY_USERS\SID\... is reachable as the user under HKCU
    hive, path = sw.get("hive"), sw.get("reg_path")
    if run_as == "user" and (hive or "").upper() == "HKEY_USERS":
        hive, path = "HKEY_CURRENT_USER", SID_IN_PATH.sub("", path or "")
    return {"name": name, "version": sw.get("version") or "", "type": "custom", "install_type": "script",
            "script": _script(cmd, hive, path, cleanup), "arguments": "", "success_codes": SUCCESS,
            "reboot": "never", "rescan": 1, "timeout_min": db.setting("deploy_timeout_minutes"),
            "run_as": run_as, "run_user_sid": info.get("user_sid") if run_as == "user" else None, "run_as_forced": forced}


def remember(pattern, command, run_as, user):
    pattern = (pattern or "").strip()
    command = (command or "").strip()
    if not pattern or not command:
        raise ValueError("Muster und Befehl angeben.")
    db.ex("DELETE FROM uninstall_overrides WHERE lower(pattern)=lower(?)", (pattern,))
    db.ex("INSERT INTO uninstall_overrides(pattern, command, run_as, created_by, created_at) VALUES(?,?,?,?,?)",
          (pattern[:300], command[:2000], run_as if run_as in ("auto", "system", "user") else "auto", user, util.now()))


def run(items, user):
    """items: [{software_id, command?, run_as?, cleanup?, remember?}] -> deployments."""
    groups = {}
    for it in items:
        sw = db.q1("SELECT * FROM software WHERE id=?", (int(it["software_id"]),))
        if not sw:
            continue
        if it.get("remember") and it.get("command"):
            remember(it.get("remember"), it["command"], it.get("run_as") or "auto", user)
        pkg = package_for(sw, it.get("command"), it.get("run_as"), bool(it.get("cleanup")))
        key = (pkg["name"], pkg.get("script") or pkg.get("winget_id"), pkg.get("run_as"), pkg.get("run_user_sid"))
        groups.setdefault(key, (pkg, []))[1].append(sw["computer_id"])
    if not groups:
        raise ValueError("Keine passende Software gefunden.")
    return [deploy.create(pkg, cids, user, name=pkg["name"]) for (pkg, cids) in groups.values()]
