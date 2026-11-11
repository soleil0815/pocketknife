"""Packages made of steps: preparation, steps, completion - with conditions,
"Ausführen als" (run as) and per-step error behavior. Execution per target computer via the Runner class."""
import base64
import json
import re
import secrets
import time

from . import config, db, deploy, remote, util

OS_KEYS = [("win10", "Windows 10"), ("win11", "Windows 11"), ("srv2016", "Windows Server 2016"),
           ("srv2019", "Windows Server 2019"), ("srv2022", "Windows Server 2022"), ("srv2025", "Windows Server 2025")]
ARCH_KEYS = [("x86", "32-Bit (x86)"), ("x64", "64-Bit (x64)"), ("arm64", "ARM64")]
LOGON = {"always": "Immer", "logged_on": "Nur wenn ein Benutzer angemeldet ist",
         "not_logged_on": "Nur wenn kein Benutzer angemeldet ist"}
PS_VERSIONS = ["", "3.0", "4.0", "5.0", "5.1", "7.0"]
FILE_MODES = {"exists": "existiert", "not_exists": "existiert nicht", "version_ge": "Version ≥", "version_lt": "Version <"}
REG_MODES = {"exists": "existiert", "not_exists": "existiert nicht", "equals": "Wert =", "not_equals": "Wert ≠",
             "contains": "Wert enthält", "version_ge": "Version ≥", "version_lt": "Version <"}
# 2.39: packages run with the account chosen in the settings ("default": Local System as standard, or the service
# account through a short-lived scheduled task with a local logon) or as the logged-on user. What the editor shows is
# exactly what runs - no silent switching. Internal values: "system", "user", "service" (service account, local logon)
# and "deploy" (service account, directly over WinRM - tools and very old deployment snapshots only).
RUN_AS = {"default": "Wie in den Einstellungen", "user": "Angemeldeter Benutzer"}
STEP_RUN_AS = {"inherit": "Wie Paket", **RUN_AS}
# winget never runs as the service account (the App Installer is not registered for it): only these two
WINGET_RUN_AS = {"system": "Lokales System", "user": "Angemeldeter Benutzer"}
PKG_RUN_AS_SETTING = {"system": "Lokales System", "deploy": "Dienstkonto"}
ENGINE_RUN_AS = ("system", "user", "service", "deploy")
STEP_TYPES = {"install": "Installieren", "uninstall": "Deinstallieren", "command": "Befehl", "powershell": "PowerShell",
              "nested": "Verschachteltes Paket", "filecopy": "Dateikopie", "registry": "Registry", "scan": "Scannen", "reboot": "Neustart",
              "sleep": "Warten", "message": "Nachricht", "logoff": "Abmelden"}
PHASES = [("pre", "Vorbereitung"), ("main", "Schritte"), ("post", "Abschluss")]
INSTALL_TYPES = {".msi": "msi", ".msp": "msp", ".msu": "msu", ".exe": "exe", ".cmd": "cmd", ".bat": "cmd", ".ps1": "ps1", ".reg": "reg"}
# Registry step
REG_ACTIONS = {"set": "Wert setzen", "delete_value": "Wert löschen", "delete_key": "Schlüssel löschen", "create_key": "Schlüssel anlegen"}
REG_KINDS = {"REG_SZ": "String", "REG_EXPAND_SZ": "ExpandString", "REG_DWORD": "DWord", "REG_QWORD": "QWord",
             "REG_MULTI_SZ": "MultiString", "REG_BINARY": "Binary"}
REG_HIVES = {"HKLM": "HKLM", "HKEY_LOCAL_MACHINE": "HKLM", "HKCU": "HKCU", "HKEY_CURRENT_USER": "HKCU", "HKU": "HKU",
             "HKEY_USERS": "HKU", "HKCR": "HKCR", "HKEY_CLASSES_ROOT": "HKCR", "HKCC": "HKCC", "HKEY_CURRENT_CONFIG": "HKCC"}
MAX_DEPTH = 5



def reboot_text(st):
    """Message of the "Neustart" (restart) step: the step's own text (placeholders allowed) or the configured default."""
    from . import rebootmsg
    try:
        d = max(0, int(st.get("delay_sec", 60)))
    except (TypeError, ValueError):
        d = 60
    if (st.get("message") or "").strip():
        return rebootmsg.render(rebootmsg.clean(st["message"], rebootmsg.MAX_TEXT), sekunden=d, minuten=round(d / 60, 1))
    return rebootmsg.text("step", sekunden=d)

def _id():
    return secrets.token_hex(4)


# --- Conditions --------------------------------------------------------------------------
def default_conditions():
    """Everything is selected by default - deselect if needed."""
    return {"os": [k for k, _ in OS_KEYS], "arch": [k for k, _ in ARCH_KEYS], "ps_min": "", "logged_on": "always",
            "files": [], "registry": [], "collections": []}


def clean_conditions(c, label="Bedingungen"):
    d = default_conditions()
    c = c or {}
    if "os" in c:
        d["os"] = [k for k, _ in OS_KEYS if k in (c.get("os") or [])]
    if "arch" in c:
        d["arch"] = [k for k, _ in ARCH_KEYS if k in (c.get("arch") or [])]
    if not d["os"]:
        raise ValueError(f"{label}: mindestens ein Betriebssystem auswählen.")
    if not d["arch"]:
        raise ValueError(f"{label}: mindestens eine Architektur auswählen.")
    d["ps_min"] = c.get("ps_min") if c.get("ps_min") in PS_VERSIONS else ""
    d["logged_on"] = c.get("logged_on") if c.get("logged_on") in LOGON else "always"
    for f in c.get("files") or []:
        path = (f.get("path") or "").strip()
        if not path:
            continue
        mode = f.get("mode") if f.get("mode") in FILE_MODES else "exists"
        ver = (f.get("version") or "").strip()
        if mode.startswith("version") and not re.match(r"^\d+(\.\d+){0,3}$", ver):
            raise ValueError(f"{label}: Dateiversion als Zahl angeben (z. B. 24.8.0.0).")
        d["files"].append({"path": path[:500], "mode": mode, "version": ver})
    for r in c.get("registry") or []:
        path = (r.get("path") or "").strip()
        if not path:
            continue
        if not re.match(r"^(HKLM|HKCU|HKCR|HKU|HKEY_[A-Z_]+):?\\", path, re.I):
            raise ValueError(f"{label}: Registry-Pfad muss mit HKLM:\\ oder HKCU:\\ beginnen.")
        mode = r.get("mode") if r.get("mode") in REG_MODES else "exists"
        d["registry"].append({"path": path[:500], "value": (r.get("value") or "").strip()[:200], "mode": mode,
                              "data": (r.get("data") or "").strip()[:500]})
    for cc in c.get("collections") or []:
        try:
            cid = int(cc.get("id"))
        except (TypeError, ValueError):
            continue
        d["collections"].append({"id": cid, "mode": "not_member" if cc.get("mode") == "not_member" else "member"})
    return d


def client_checks(c):
    """Do conditions have to be checked on the client?"""
    full = default_conditions()
    return bool(set(c["os"]) != set(full["os"]) or set(c["arch"]) != set(full["arch"]) or c["ps_min"]
                or c["logged_on"] != "always" or c["files"] or c["registry"])


def describe_conditions(c):
    out = []
    if set(c["os"]) != {k for k, _ in OS_KEYS}:
        out.append("Betriebssystem: " + ", ".join(lbl for k, lbl in OS_KEYS if k in c["os"]))
    if set(c["arch"]) != {k for k, _ in ARCH_KEYS}:
        out.append("Architektur: " + ", ".join(lbl for k, lbl in ARCH_KEYS if k in c["arch"]))
    if c["ps_min"]:
        out.append(f"PowerShell ≥ {c['ps_min']}")
    if c["logged_on"] != "always":
        out.append(LOGON[c["logged_on"]])
    for f in c["files"]:
        out.append(f"Datei {f['path']} {FILE_MODES[f['mode']]} {f['version']}".strip())
    for r in c["registry"]:
        out.append(f"Registry {r['path']}{(' → ' + r['value']) if r['value'] else ''} {REG_MODES[r['mode']]} {r['data']}".strip())
    for cc in c["collections"]:
        n = "Alle Computer" if cc["id"] == 0 else db.val("SELECT name FROM collections WHERE id=?", (cc["id"],), f"#{cc['id']}")
        out.append(f"{'Mitglied' if cc['mode'] == 'member' else 'Nicht Mitglied'} der Sammlung „{n}“")
    return out


def cond_script(c):
    """PowerShell that outputs PK_COND_OK or PK_COND_FAIL:<reason> (runs via WinRM, without a scheduled task)."""
    q = util.psq
    lines = [r"""$ErrorActionPreference = 'SilentlyContinue'
function PKV($v) { if ("$v" -match '(\d+)(\.\d+)?(\.\d+)?(\.\d+)?') { $s = $matches[1] + $(if ($matches[2]) { $matches[2] } else { '.0' }) + $matches[3] + $matches[4]; try { [version]$s } catch { $null } } else { $null } }
function PKFail($t) { throw ('PKFAIL:' + $t) }
""" + util.PS_EXPAND + r"""try { & {"""]
    if set(c["os"]) != {k for k, _ in OS_KEYS}:
        lines.append(r"""$o = Get-CimInstance Win32_OperatingSystem; $b = [int]$o.BuildNumber
$k = if ([int]$o.ProductType -eq 1) { if ($b -ge 22000) { 'win11' } elseif ($b -ge 10240) { 'win10' } else { 'alt' } }
     else { if ($b -ge 26100) { 'srv2025' } elseif ($b -ge 20348) { 'srv2022' } elseif ($b -ge 17763) { 'srv2019' } elseif ($b -ge 14393) { 'srv2016' } else { 'alt' } }
if (@(""" + ",".join(q(x) for x in c["os"]) + r""") -notcontains $k) { PKFail ('Betriebssystem ' + $o.Caption + ' (Build ' + $b + ') ist nicht ausgewählt') }""")
    if set(c["arch"]) != {k for k, _ in ARCH_KEYS}:
        lines.append(r"""$a = $env:PROCESSOR_ARCHITEW6432; if (-not $a) { $a = $env:PROCESSOR_ARCHITECTURE }
$ak = switch ($a) { 'AMD64' { 'x64' } 'ARM64' { 'arm64' } default { 'x86' } }
if (@(""" + ",".join(q(x) for x in c["arch"]) + r""") -notcontains $ak) { PKFail ('Architektur ' + $ak + ' ist nicht ausgewählt') }""")
    if c["ps_min"]:
        lines.append(f"""if ($PSVersionTable.PSVersion -lt [version]{q(c['ps_min'])}) {{ PKFail ('PowerShell ' + $PSVersionTable.PSVersion + ' ist älter als {c['ps_min']}') }}""")
    if c["logged_on"] != "always":
        lines.append(r"""$lo = @(Get-CimInstance Win32_Process -Filter "Name='explorer.exe'").Count -gt 0""")
        if c["logged_on"] == "logged_on":
            lines.append("if (-not $lo) { PKFail 'Es ist kein Benutzer angemeldet' }")
        else:
            lines.append("if ($lo) { PKFail 'Es ist ein Benutzer angemeldet' }")
    for f in c["files"]:
        p = q(f["path"])
        lines.append(f"$fp = PK-Exp {p}; $fx = Test-Path -LiteralPath $fp")
        if f["mode"] == "exists":
            lines.append(f"if (-not $fx) {{ PKFail ('Datei fehlt: ' + $fp) }}")
        elif f["mode"] == "not_exists":
            lines.append(f"if ($fx) {{ PKFail ('Datei vorhanden: ' + $fp) }}")
        else:
            op = "-lt" if f["mode"] == "version_ge" else "-ge"
            lines.append(f"""$fv = if ($fx) {{ PKV (Get-Item -LiteralPath $fp).VersionInfo.FileVersion }} else {{ $null }}
if ({'-not $fv -or ' if f['mode'] == 'version_ge' else ''}($fv -and $fv {op} (PKV {q(f['version'])}))) {{ PKFail ('Dateiversion ' + $fp + ': ' + $fv) }}""")
    for r in c["registry"]:
        path = "Registry::" + re.sub(r"^(HK[A-Z_]+):?\\", lambda m: HIVES.get(m.group(1).upper(), m.group(1).upper()) + "\\", r["path"], flags=re.I)
        lines.append(f"$rp = {q(path)}; $rx = Test-Path -LiteralPath $rp; $rv = $null")
        if r["value"]:
            lines.append(f"if ($rx) {{ $ri = Get-ItemProperty -LiteralPath $rp -Name {q(r['value'])}; if ($ri -ne $null) {{ $rv = '' + $ri.{q(r['value'])} }} else {{ $rx = $false }} }}")
        else:
            lines.append("if ($rx) { $rv = '' + (Get-Item -LiteralPath $rp).GetValue('') }")
        m, data = r["mode"], q(r["data"])
        what = q(path + ((" → " + r["value"]) if r["value"] else ""))
        if m == "exists":
            lines.append(f"if (-not $rx) {{ PKFail ('Registry fehlt: ' + {what}) }}")
        elif m == "not_exists":
            lines.append(f"if ($rx) {{ PKFail ('Registry vorhanden: ' + {what}) }}")
        elif m == "equals":
            lines.append(f"if (-not $rx -or $rv -ne {data}) {{ PKFail ('Registry ' + {what} + ' = ' + $rv) }}")
        elif m == "not_equals":
            lines.append(f"if ($rx -and $rv -eq {data}) {{ PKFail ('Registry ' + {what} + ' = ' + $rv) }}")
        elif m == "contains":
            lines.append(f"if (-not $rx -or $rv -notlike ('*' + {data} + '*')) {{ PKFail ('Registry ' + {what} + ' = ' + $rv) }}")
        else:
            op = "-lt" if m == "version_ge" else "-ge"
            lines.append(f"""$vv = PKV $rv
if ({'-not $vv -or ' if m == 'version_ge' else ''}($vv -and $vv {op} (PKV {data}))) {{ PKFail ('Registry-Version ' + {what} + ': ' + $rv) }}""")
    lines.append(r"""} } catch {
  $m = "$($_.Exception.Message)"
  if ($m -like 'PKFAIL:*') { 'PK_COND_FAIL:' + $m.Substring(7) } else { 'PK_COND_FAIL:Fehler bei der Prüfung: ' + $m }
  return
}
'PK_COND_OK'""")
    return "\n".join(lines)


HIVES = {"HKLM": "HKEY_LOCAL_MACHINE", "HKCU": "HKEY_CURRENT_USER", "HKCR": "HKEY_CLASSES_ROOT", "HKU": "HKEY_USERS",
         "HKCC": "HKEY_CURRENT_CONFIG"}


# --- Steps --------------------------------------------------------------------------------
def install_type_for(filename):
    m = re.search(r"\.[A-Za-z0-9]+$", filename or "")
    return INSTALL_TYPES.get(m.group(0).lower()) if m else None


def _int(v, lo, hi, default):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _is_winget(s):
    return s.get("type") == "install" and s.get("source") == "winget"


def _step_run_as(s):
    """Run as of a step in the 2.39 model. winget: only "system"/"user" ("inherit" stays and is resolved against the
    package in clean_package). Others: "inherit", "default" or "user"; older values ("system"/"deploy") mean
    "not as the user" = "default"."""
    ra = s.get("run_as")
    if _is_winget(s):
        return ra if ra in ("system", "user") else "inherit"
    if ra in STEP_RUN_AS:
        return ra
    return "default" if ra in ("system", "deploy") else "inherit"


def pkg_run_as(v):
    """Run as of a package in the 2.39 model: "user" or "default"."""
    return "user" if v == "user" else "default"


def pkg_run_as_label(ra):
    """What a package really runs as - for lists and the deployment view."""
    if ra == "user":
        return "Angemeldeter Benutzer"
    if ra == "system":
        return "Lokales System"
    return PKG_RUN_AS_SETTING.get(db.setting("pkg_run_as"), "Lokales System")


def setting_run_as():
    """Engine value for "Wie in den Einstellungen": "system" (standard) or "service" (service account, local logon)."""
    return "service" if db.setting("pkg_run_as") == "deploy" else "system"


UNC_RX = re.compile(r"\\\\[A-Za-z0-9][A-Za-z0-9._$-]*\\[^\s\"'<>|]+")


def migrate_steps_run_as(pkg_ra, steps_):
    """2.39: steps of one package into the new model (see migrate_run_as). Returns (steps, changed)."""
    changed = False
    for key, _l in PHASES:
        for st in (steps_ or {}).get(key) or []:
            if not isinstance(st, dict):
                continue
            ra = st.get("run_as") or "inherit"
            if _is_winget(st):
                new = ra if ra in ("system", "user") else ("user" if ra == "inherit" and pkg_ra == "user" else "system")
            elif ra in ("system", "deploy"):
                new = "default" if pkg_ra == "user" else "inherit"
            else:
                new = ra if ra in STEP_RUN_AS else "inherit"
            if new != st.get("run_as"):
                st["run_as"] = new
                changed = True
    return steps_, changed


def unc_hints(steps_):
    """Places in own commands that open a network path themselves - as Local System the computer account
    (e.g. PC-0001$) accesses it, not the service account."""
    out = []
    for key, _l in PHASES:
        for st in (steps_ or {}).get(key) or []:
            if not isinstance(st, dict) or st.get("type") not in ("command", "powershell", "install", "uninstall"):
                continue
            text = " ".join(str(st.get(k) or "") for k in ("command", "script", "arguments"))
            m = UNC_RX.search(text)
            if m:
                out.append(f"{step_title(st)}: {m.group(0)[:80]}")
    return out


def migrate_run_as():
    """2.39 (one-time): packages run with the account from the settings (standard: Local System). Packages that ran as
    the service account or as Local System get "Wie in den Einstellungen"; "Angemeldeter Benutzer" stays. Steps:
    explicit service account/Local System -> "Wie Paket" (in user packages "Wie in den Einstellungen"), winget steps
    explicitly Local System or user. Waiting deployments are converted the same way, finished ones stay as history.
    Packages whose own commands open a network path are listed in the log."""
    if db.val("SELECT value FROM settings WHERE key='run_as_migrated'"):
        return
    n_pkg, hints = 0, []
    with db.tx():
        for r in db.q("SELECT id, name, run_as, steps FROM packages"):
            pra = "user" if r["run_as"] == "user" else "default"
            try:
                st = json.loads(r["steps"]) if r["steps"] else None
            except ValueError:
                st = None
            changed = pra != r["run_as"]
            if isinstance(st, dict):
                st, ch = migrate_steps_run_as(pra, st)
                changed = changed or ch
                if r["run_as"] != "user":
                    hints += [f"„{r['name']}“ – {x}" for x in unc_hints(st)]
            if changed:
                db.ex("UPDATE packages SET run_as=?, steps=? WHERE id=?",
                      (pra, json.dumps(st, ensure_ascii=False) if isinstance(st, dict) else r["steps"], r["id"]))
                n_pkg += 1
        for d in db.q("SELECT id, package_json FROM deployments WHERE status IN ('queued','scheduled')"):
            try:
                pj = json.loads(d["package_json"] or "{}")
            except ValueError:
                continue
            if not isinstance(pj, dict) or not isinstance(pj.get("steps"), dict):
                continue
            pra = "user" if pj.get("run_as") == "user" else "default"
            st, ch = migrate_steps_run_as(pra, pj["steps"])
            if ch or pra != pj.get("run_as"):
                pj.update(run_as=pra, steps=st)
                db.ex("UPDATE deployments SET package_json=? WHERE id=?", (json.dumps(pj, ensure_ascii=False), d["id"]))
        db.ex("INSERT OR REPLACE INTO settings(key, value) VALUES('run_as_migrated', '1')")
    from . import pklog
    if n_pkg:
        pklog.info("system", f"Umstellung 2.39: {n_pkg} Paket(e) laufen jetzt mit dem Konto aus den Einstellungen "
                             f"(Standard: Lokales System).")
    if hints:
        pklog.warn("system", "Umstellung 2.39: Diese Pakete öffnen in eigenen Befehlen selbst einen Netzwerkpfad. Als Lokales "
                             "System greift dabei das Computerkonto zu (z. B. PC-0001$) – bitte Leserecht für „Domänencomputer“ "
                             "auf der Freigabe prüfen oder die Datei über einen Repository-Schritt kopieren: " + "; ".join(hints[:30])
                   + (f" … und {len(hints) - 30} weitere" if len(hints) > 30 else ""))


def clean_step(s, label="Schritt"):
    typ = s.get("type")
    if typ not in STEP_TYPES:
        raise ValueError(f"{label}: unbekannter Schritttyp.")
    o = {"id": str(s.get("id") or _id())[:16], "type": typ, "title": (s.get("title") or "").strip()[:200],
         "enabled": bool(s.get("enabled", True)),
         "run_as": _step_run_as(s),
         "on_error": "continue" if s.get("on_error") == "continue" else "stop",
         "timeout_min": _int(s.get("timeout_min"), 0, 1440, 0),
         "conditions": clean_conditions(s.get("conditions"), label)}
    if typ == "install":
        src = "winget" if s.get("source") == "winget" else "file"
        o["source"] = src
        if src == "winget":
            wid = (s.get("winget_id") or "").strip()
            if not util.WINGET_ID_RE.match(wid):
                raise ValueError(f"{label}: gültige winget-ID angeben (z. B. Mozilla.Firefox).")
            ver = (s.get("winget_version") or "").strip()
            if ver and not util.WINGET_ID_RE.match(ver):
                raise ValueError(f"{label}: ungültige Version.")
            o.update({"winget_id": wid, "winget_version": ver,
                      "winget_action": s.get("winget_action") if s.get("winget_action") in ("install", "upgrade", "uninstall") else "install",
                      "winget_scope": s.get("winget_scope") if s.get("winget_scope") in ("machine", "user", "") else "machine",
                      "extra_args": (s.get("extra_args") or "").strip()[:500]})
        else:
            folder = (s.get("folder") or "").strip().strip("\\/")
            f = (s.get("install_file") or "").strip()
            if not f:
                raise ValueError(f"{label}: Installationsdatei aus dem Repository wählen.")
            if ".." in folder.replace("/", "\\").split("\\") or ".." in f or "\\" in f or "/" in f:
                raise ValueError(f"{label}: ungültiger Pfad.")
            it = s.get("install_type") if s.get("install_type") in set(INSTALL_TYPES.values()) else install_type_for(f)
            if not it:
                raise ValueError(f"{label}: Dateityp nicht unterstützt (MSI, MSP, MSU, EXE, CMD/BAT, PS1, REG).")
            o.update({"folder": folder, "install_file": f, "install_type": it,
                      "arguments": (s.get("arguments") or "").strip()[:2000],
                      "copy_folder": bool(s.get("copy_folder", True)),
                      "detect_name": (s.get("detect_name") or "").strip()[:200],
                      "detect_version": (s.get("detect_version") or "").strip()[:50],
                      "product_code": (s.get("product_code") or "").strip()[:60]})
        o["success_codes"] = _codes(s.get("success_codes"), "0,3010,1641")
    elif typ == "uninstall":
        name = (s.get("match_name") or "").strip()
        if not name:
            raise ValueError(f"{label}: Anzeigenamen der Software angeben (Platzhalter * erlaubt, z. B. Zotero*).")
        o.update({"match_name": name[:300], "match_version": (s.get("match_version") or "").strip()[:100],
                  "command": (s.get("command") or "").strip()[:2000], "cleanup": bool(s.get("cleanup")),
                  "all_matches": bool(s.get("all_matches", True)),
                  "missing_ok": bool(s.get("missing_ok", True))})
    elif typ == "command":
        if not (s.get("command") or "").strip():
            raise ValueError(f"{label}: Befehl angeben.")
        o["command"] = s["command"].strip()[:20000]
        o["success_codes"] = _codes(s.get("success_codes"), "0")
    elif typ == "powershell":
        if not (s.get("script") or "").strip():
            raise ValueError(f"{label}: PowerShell-Code angeben.")
        o["script"] = s["script"].strip()[:50000]
        o["success_codes"] = _codes(s.get("success_codes"), "0")
        # 2.39.3: errors PowerShell reports count even with exit code 0 - unless deliberately ignored
        o["ignore_errors"] = bool(s.get("ignore_errors"))
    elif typ == "nested":
        pid = _int(s.get("package_id"), 0, 10 ** 9, 0)
        if not pid or not db.val("SELECT 1 FROM packages WHERE id=?", (pid,)):
            raise ValueError(f"{label}: Paket für den verschachtelten Schritt wählen.")
        o["package_id"] = pid
    elif typ == "filecopy":
        src = (s.get("source") or "").strip().strip("\\/")
        dst = (s.get("target") or "").strip()
        if not src or ".." in src.replace("/", "\\").split("\\"):
            raise ValueError(f"{label}: Quelle im Repository wählen.")
        if not re.match(r"^([A-Za-z]:\\|%[A-Za-z_]+%)", dst) or ".." in dst:
            raise ValueError(f"{label}: Ziel als absoluten Pfad angeben, z. B. C:\\ProgramData\\Firma oder %ProgramData%\\Firma.")
        o.update({"source": src, "target": dst[:500], "overwrite": bool(s.get("overwrite", True))})
    elif typ == "registry":
        o["entries"] = [clean_reg_entry(e, f"{label}, Eintrag {i + 1}") for i, e in enumerate((s.get("entries") or [])[:200])]
        if not o["entries"]:
            raise ValueError(f"{label}: mindestens einen Registry-Eintrag angeben.")
        o["view"] = "32" if str(s.get("view")) == "32" else "64"
        o["success_codes"] = _codes(s.get("success_codes"), "0")
    elif typ == "scan":
        o["profile_id"] = _int(s.get("profile_id"), 0, 10 ** 9, 0) or None
    elif typ == "reboot":
        o["delay_sec"] = _int(s.get("delay_sec"), 0, 3600, 60)
        o["wait_minutes"] = _int(s.get("wait_minutes"), 2, 240, 20)
        o["message"] = (s.get("message") or "").strip()[:500]
    elif typ == "sleep":
        o["seconds"] = _int(s.get("seconds"), 1, 86400, 30)
    elif typ == "message":
        if not (s.get("text") or "").strip():
            raise ValueError(f"{label}: Nachrichtentext angeben.")
        o["text"] = s["text"].strip()[:1000]
        o["timeout_sec"] = _int(s.get("timeout_sec"), 0, 86400, 0)
    elif typ == "logoff":
        o["only_disconnected"] = bool(s.get("only_disconnected"))
    return o


def split_reg_path(path):
    """'HKEY_LOCAL_MACHINE\\SOFTWARE\\Firma' -> ('HKLM', 'SOFTWARE\\Firma')"""
    p = (path or "").strip().replace("/", "\\").strip("\\")
    if p.lower().startswith("registry::"):
        p = p[10:]
    hive, _, sub = p.partition("\\")
    hive = REG_HIVES.get(hive.rstrip(":").upper())
    sub = re.sub(r"\\{2,}", lambda _m: "\\", sub.strip("\\"))
    return hive, sub


def clean_reg_entry(e, label):
    if not isinstance(e, dict):
        raise ValueError(f"{label}: ungültig.")
    action = e.get("action") if e.get("action") in REG_ACTIONS else "set"
    hive, sub = split_reg_path(e.get("path"))
    if not hive:
        raise ValueError(f"{label}: Pfad muss mit HKLM\\, HKCU\\, HKU\\, HKCR\\ oder HKCC\\ beginnen.")
    if not sub:
        raise ValueError(f"{label}: Unterschlüssel angeben (ein ganzer Hive kann nicht bearbeitet werden).")
    if len(sub) > 1000 or any(ord(c) < 32 for c in sub):
        raise ValueError(f"{label}: ungültiger Pfad.")
    o = {"action": action, "path": f"{hive}\\{sub}"}
    if action in ("set", "delete_value"):
        name = str(e.get("name") or "")
        if name in ("@", "(Standard)", "(Default)"):
            name = ""
        if len(name) > 255 or any(ord(c) < 32 for c in name):
            raise ValueError(f"{label}: ungültiger Wertname.")
        o["name"] = name
    if action == "set":
        kind = e.get("kind") if e.get("kind") in REG_KINDS else "REG_SZ"
        data = e.get("data")
        data = "" if data is None else str(data)
        if kind in ("REG_DWORD", "REG_QWORD"):
            d = data.strip().lower()
            try:
                n = int(d, 16) if d.startswith("0x") else int(d or "0")
            except ValueError:
                raise ValueError(f"{label}: {kind} erwartet eine Zahl (dezimal oder 0x…).")
            if n < 0 or n >= (2 ** 32 if kind == "REG_DWORD" else 2 ** 64):
                raise ValueError(f"{label}: Zahl außerhalb des Bereichs für {kind}.")
            data = str(n)
        elif kind == "REG_BINARY":
            d = re.sub(r"[\s,]", "", data)
            if len(d) % 2 or not re.match(r"^[0-9A-Fa-f]*$", d):
                raise ValueError(f"{label}: REG_BINARY als Hex-Bytes angeben, z. B. 01,ff,0a.")
            data = ",".join(d[i:i + 2].lower() for i in range(0, len(d), 2))
        elif len(data) > 32000:
            raise ValueError(f"{label}: Wert zu lang.")
        o.update({"kind": kind, "data": data})
    return o


def reg_line(e, view="64"):
    """Equivalent reg.exe call (for the preview)."""
    v = "" if view == "64" else " /reg:32"
    path = f'"{e["path"]}"'
    nm = (f'/v "{e["name"]}"' if e.get("name") else "/ve")
    if e["action"] == "delete_key":
        return f"reg delete {path} /f{v}"
    if e["action"] == "create_key":
        return f"reg add {path} /f{v}"
    if e["action"] == "delete_value":
        return f"reg delete {path} {nm} /f{v}"
    data = e["data"]
    if e["kind"] == "REG_MULTI_SZ":
        data = data.replace("\r\n", "\n").replace("\n", "\\0")
    if e["kind"] == "REG_BINARY":
        data = data.replace(",", "")
    data = data.replace('"', '\\"')
    return f'reg add {path} {nm} /t {e["kind"]} /d "{data}" /f{v}'


def _codes(v, default):
    v = (v or default).strip()
    parts = [x.strip() for x in v.split(",") if x.strip()]
    if not parts or not all(re.match(r"^-?\d+$", x) for x in parts):
        raise ValueError("Erfolgs-Exitcodes als kommagetrennte Zahlen angeben (z. B. 0,3010).")
    return ",".join(parts)


def step_title(st, packages=None):
    if st.get("title"):
        return st["title"]
    t = st["type"]
    if t == "install":
        if st.get("source") == "winget":
            return f"winget {st['winget_action']} {st['winget_id']}"
        return f"Installieren: {st['install_file']}"
    if t == "uninstall":
        return f"Deinstallieren: {st['match_name']}" + (f" {st['match_version']}" if st.get("match_version") else "")
    if t == "command":
        return "Befehl: " + ((st.get("command") or "").splitlines() or [""])[0][:60]
    if t == "powershell":
        return "PowerShell: " + ((st.get("script") or "").splitlines() or [""])[0][:60]
    if t == "nested":
        n = (packages or {}).get(st["package_id"]) or db.val("SELECT name FROM packages WHERE id=?", (st["package_id"],), "?")
        return f"Paket: {n}"
    if t == "filecopy":
        return f"Dateikopie → {st['target']}"
    if t == "registry":
        e = st["entries"][0]
        more = f" (+{len(st['entries']) - 1})" if len(st["entries"]) > 1 else ""
        return f"Registry: {e['path']}" + (f"\\{e['name']}" if e.get("name") else "") + more
    if t == "scan":
        n = db.val("SELECT name FROM scan_profiles WHERE id=?", (st.get("profile_id"),), None) if st.get("profile_id") else None
        return f"Scannen ({n or 'Standard'})"
    if t == "reboot":
        return "Neustart"
    if t == "sleep":
        return f"Warten ({st['seconds']} s)"
    if t == "message":
        return "Nachricht: " + st["text"][:60]
    if t == "logoff":
        return "Benutzer abmelden"
    return STEP_TYPES[t]


# --- Packages -----------------------------------------------------------------------------
def legacy_steps(p):
    """Represent packages from versions before 2.3 (a single install command) as a step package."""
    st = {"id": "s1", "type": "install", "enabled": True, "run_as": "inherit", "on_error": "stop", "timeout_min": 0,
          "conditions": default_conditions(), "title": ""}
    if p.get("type") == "winget":
        st.update({"source": "winget", "winget_id": p.get("winget_id") or "", "winget_action": p.get("winget_action") or "install",
                   "winget_version": p.get("winget_version") or "", "winget_scope": p.get("winget_scope") or "",
                   "extra_args": p.get("extra_args") or "", "success_codes": "0,3010,1641"})
    elif p.get("install_type") == "script":
        st = {**st, "type": "powershell", "script": p.get("script") or "", "success_codes": p.get("success_codes") or "0"}
    else:
        st.update({"source": "file", "folder": p.get("folder") or "", "install_file": p.get("install_file") or "",
                   "install_type": p.get("install_type") or install_type_for(p.get("install_file")) or "exe",
                   "arguments": p.get("arguments") or "", "copy_folder": True, "detect_name": p.get("detect_name") or "",
                   "detect_version": p.get("version") or "", "success_codes": p.get("success_codes") or "0,3010,1641"})
    return {"pre": [], "main": [st], "post": []}


def normalize(p):
    """Bring a package (DB row, deployment snapshot or ad-hoc package) into step form."""
    p = dict(p)
    steps = p.get("steps")
    if isinstance(steps, str):
        try:
            steps = json.loads(steps)
        except ValueError:
            steps = None
    if not steps or not any(steps.get(k) for k, _ in PHASES):
        steps = legacy_steps(p)
        # Old packages always ran as SYSTEM (or as defined for the uninstall)
        p["run_as"] = p.get("run_as") if p.get("run_as") in RUN_AS else "system"
    conds = p.get("conditions")
    if isinstance(conds, str):
        try:
            conds = json.loads(conds)
        except ValueError:
            conds = None
    p["steps"] = {k: list(steps.get(k) or []) for k, _ in PHASES}
    # Missing or duplicate step IDs (very old snapshots, imports): add them deterministically by position - the same
    # deployment therefore always gets the same IDs (retry, result per step)
    seen = set()
    for pi, (k, _l) in enumerate(PHASES):
        for i, st in enumerate(p["steps"][k]):
            if isinstance(st, dict) and (not st.get("id") or st.get("id") in seen):
                p["steps"][k][i] = dict(st, id=f"{st.get('id') or 'p'}{pi}-{i}")
            if isinstance(st, dict):
                seen.add(p["steps"][k][i]["id"])
    p["conditions"] = conds or default_conditions()
    # 2.39: "default"/"user"; "system" stays as it was (internal packages, old snapshots - shown truthfully);
    # "deploy" (before 2.39 the standard) and empty values follow the setting
    p["run_as"] = p.get("run_as") if p.get("run_as") in ("default", "user", "system") else "default"
    return p


def clean_package(data):
    """Validates a package from the editor and returns the values to save."""
    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("Name fehlt.")
    steps = {}
    n = 0
    seen_ids = set()
    for key, label in PHASES:
        steps[key] = []
        for s in (data.get("steps") or {}).get(key) or []:
            n += 1
            cs = clean_step(s, f"{label}, Schritt {len(steps[key]) + 1}")
            if cs["id"] in seen_ids:
                cs["id"] = _id()  # duplicate ID (import, API): make it unique - "Schritt wiederholen" (retry step) needs it
            seen_ids.add(cs["id"])
            steps[key].append(cs)
    if not n:
        raise ValueError("Das Paket hat keine Schritte.")
    tenant = data.get("tenant_id")
    tenant = int(tenant) if tenant not in (None, "", 0, "0") else None
    if tenant and not db.val("SELECT 1 FROM tenants WHERE id=?", (tenant,)):
        raise ValueError("Mandant nicht gefunden.")
    if tenant:
        # Packages of a tenant may only use files from that tenant's repository folder
        base = (db.val("SELECT repo_folder FROM tenants WHERE id=?", (tenant,)) or "").replace("/", "\\").strip("\\").lower()
        base = "\\".join(x for x in base.split("\\") if x not in ("", "."))
        if base:
            for key, _label in PHASES:
                for s in steps[key]:
                    paths = []
                    if s["type"] == "install" and s.get("source") == "file":
                        paths.append(s.get("folder") or "")
                    if s["type"] == "filecopy":
                        paths.append(s.get("source") or "")
                    if s["type"] == "install" and s.get("source") == "file" and re.search(r"[\\/]|\.\.", s.get("install_file") or ""):
                        raise ValueError(f"„{step_title(s)}“: Installationsdatei ohne Pfadangabe (nur der Dateiname im Paketordner).")
                    for pth in paths:
                        parts = [x for x in pth.replace("/", "\\").split("\\") if x not in ("", ".")]
                        rel = "\\".join(parts).lower()
                        if ".." in parts or not (rel == base or rel.startswith(base + "\\")):
                            raise ValueError(f"„{step_title(s)}“: Dateien müssen im Repository-Ordner des Mandanten "
                                             f"liegen ({base}), nicht in „{pth or '(Hauptordner)'}“.")
    p = {"name": name[:200], "version": (data.get("version") or "").strip()[:60],
         "description": (data.get("description") or "").strip()[:2000],
         "run_as": pkg_run_as(data.get("run_as")),
         "timeout_min": _int(data.get("timeout_min"), 1, 1440, db.setting("deploy_timeout_minutes")),
         "reboot": data.get("reboot") if data.get("reboot") in ("never", "if_required", "always") else "never",
         "rescan": 1 if data.get("rescan", 1) else 0,
         "rescan_profile_id": _int(data.get("rescan_profile_id"), 0, 10 ** 9, 0) or None,
         # Windows notification for the logged-on user: "" = setting, "on" = always, "off" = never
         "notify_user": data.get("notify_user") if data.get("notify_user") in ("on", "off") else "",
         "conditions": clean_conditions(data.get("conditions"), "Paket-Bedingungen"),
         "steps": steps, "tenant_id": tenant, "folder_name": (data.get("folder_name") or "").strip()[:120]}
    for key, _label in PHASES:
        for s in steps[key]:
            if _is_winget(s) and s["run_as"] == "inherit":
                # 2.39: winget steps always show their real account: the user of a user package, otherwise Local System
                s["run_as"] = "user" if p["run_as"] == "user" else "system"
    # Compatibility fields from the first install step (package list, rules, version display)
    first = next((s for k, _ in PHASES for s in steps[k] if s["type"] == "install"), None)
    legacy = {"type": "custom", "winget_id": None, "winget_action": None, "winget_version": None, "winget_scope": None,
              "extra_args": None, "folder": None, "install_type": None, "install_file": None, "arguments": None,
              "script": None, "success_codes": None, "detect_name": None}
    if first and first["source"] == "winget":
        legacy.update({k: first.get(k) for k in ("winget_id", "winget_action", "winget_version", "winget_scope", "extra_args")})
        legacy["type"] = "winget"
    elif first:
        legacy.update({k: first.get(k) for k in ("folder", "install_type", "install_file", "arguments", "success_codes",
                                                 "detect_name")})
    elif steps["main"] and steps["main"][0]["type"] == "powershell":
        legacy.update({"install_type": "script", "script": steps["main"][0]["script"]})
    p.update(legacy)
    return p


def check_nesting(pid, p):
    """Prevent loops (a package containing itself) and nesting that is too deep."""
    def walk(steps, stack):
        for k, _ in PHASES:
            for s in steps.get(k) or []:
                if s["type"] != "nested":
                    continue
                if s["package_id"] in stack:
                    raise ValueError("Verschachtelung bildet eine Schleife (Paket enthält sich selbst).")
                if len(stack) >= MAX_DEPTH:
                    raise ValueError(f"Höchstens {MAX_DEPTH} Ebenen verschachtelter Pakete.")
                row = db.q1("SELECT * FROM packages WHERE id=?", (s["package_id"],))
                if row:
                    walk(normalize(row)["steps"], stack + [s["package_id"]])
    walk(p["steps"], [pid] if pid else [])


def expand(p, stack=None):
    """Resolve nested packages for the deployment (snapshot - later changes have no effect)."""
    stack = stack or []
    p = normalize(p)
    for k, _ in PHASES:
        out = []
        for s in p["steps"][k]:
            s = dict(s)
            if s.get("type") == "nested" and "package" not in s:
                if s["package_id"] in stack or len(stack) >= MAX_DEPTH:
                    raise ValueError("Verschachtelte Pakete bilden eine Schleife oder sind zu tief.")
                row = db.q1("SELECT * FROM packages WHERE id=?", (s["package_id"],))
                if not row:
                    raise ValueError(f"Verschachteltes Paket #{s['package_id']} existiert nicht mehr.")
                s["package"] = expand(row, stack + [s["package_id"]])
            out.append(s)
        p["steps"][k] = out
    return p


def iter_steps(p):
    for k, _ in PHASES:
        for s in p["steps"][k]:
            yield k, s


# --- Run as and preview --------------------------------------------------------------------
def effective_run_as(st, pkg):
    """Account a step really runs with: (engine value, note). Engine values: see ENGINE_RUN_AS."""
    ra = st.get("run_as") or "inherit"
    if ra == "inherit":
        ra = pkg.get("run_as") or "default"
    if _is_winget(st):
        # winget never as the service account - the editor offers only Local System and the logged-on user
        return ("user" if ra == "user" else "system"), ""
    if ra in ("default", "deploy"):
        # "deploy": very old snapshots (before 2.39) - like the packages, they follow the setting
        ra = setting_run_as()
    return (ra if ra in ENGINE_RUN_AS else "system"), ""


def uninstall_run_as(st):
    """Account chosen in an uninstall step: "user", the setting's account for "default" (and older "system"/"deploy"),
    None for "Wie Paket" (automatic: Local System, entries in a user profile as that user)."""
    ra = st.get("run_as")
    if ra == "user":
        return "user"
    if ra in ("default", "system", "deploy"):
        return setting_run_as()
    return None


def run_as_label(ra):
    if ra == "mixed":
        return "verschiedene Konten (siehe Ausgabe)"
    if ra in ("deploy", "service"):
        return f"Dienstkonto {config.service_login() or '(nicht eingerichtet)'}"
    if ra == "user":
        return "Angemeldeter Benutzer"
    return "Lokales System (NT AUTHORITY\\SYSTEM)"


RUN_DIR = "C:\\Windows\\Temp\\PocketKnife\\<Lauf>"


def preview(st, pkg):
    """What is actually executed on the client - for the display in the package editor."""
    st = dict(st)
    ra, note = effective_run_as(st, pkg)
    lines, notes = [], [note] if note else []
    t = st["type"]
    if t == "install" and st.get("source") == "winget":
        lines.append("C:\\Program Files\\WindowsApps\\Microsoft.DesktopAppInstaller_<Version>_x64__8wekyb3d8bbwe\\winget.exe "
                     + deploy.command_preview({**st, "type": "winget"}).replace("winget.exe ", "", 1))
        if not st.get("winget_version"):
            from . import wingetrelease
            if wingetrelease.mode() == "off":
                notes.append("Ohne feste Version wird immer die aktuelle Version aus dem winget-Katalog installiert.")
            else:
                notes.append("Ohne feste Version wird die neueste freigegebene Version installiert (Einstellungen → winget → „Neue Versionen“).")
            if st.get("winget_id") and (st.get("winget_action") or "install") != "uninstall":
                t = wingetrelease.target(st["winget_id"])
                if t["state"] == "pin":
                    notes.append(f"Zurzeit: {t['reason']} – installiert wird {t['version']} (--version {t['version']}).")
                elif t["state"] == "hold":
                    notes.append(f"Zurzeit: {t['reason']} – der Schritt wird übersprungen.")
        if db.setting("winget_bootstrap"):
            notes.append("Fehlt winget auf dem Computer (z. B. Windows Server), wird der App-Installer vorher automatisch nachinstalliert. "
                         "Vor Windows 10 1809 / Server 2019 wird der Schritt übersprungen.")
        else:
            notes.append("Fehlt winget, schlägt der Schritt fehl (automatisches Nachinstallieren ist in den Einstellungen aus).")
    elif t == "install":
        it, f = st["install_type"], st["install_file"]
        path = f"{RUN_DIR}\\{f}"
        args, has_log = deploy.clean_msi_args(st.get("arguments") or "") if it in ("msi", "msp", "msu") else (st.get("arguments") or "", False)
        if it in ("msi", "msp"):
            log = "" if has_log else f' /l*v "{RUN_DIR}\\pk_msi.log"'
            lines.append(f'msiexec.exe {"/i" if it == "msi" else "/p"} "{path}" /qn /norestart {args}'.rstrip() + log)
        elif it == "msu":
            lines.append(f'wusa.exe "{path}" /quiet /norestart {args}'.rstrip())
        elif it == "exe":
            lines.append(f'"{path}" {args}'.rstrip())
        elif it == "cmd":
            lines.append(f'cmd.exe /c ""{path}" {args}"')
        elif it == "ps1":
            lines.append(f'{util.PS_EXE} {util.ps_file_arg(path, args)}')
            notes.append(PS_NOTE)
        elif it == "reg":
            lines.append(f'reg.exe import "{path}" {args}'.rstrip())
            notes.append("Wie „regedit.exe /s“, meldet aber Fehler über den Exitcode. Es wird das 64-Bit-reg.exe verwendet; "
                         "„/reg:32“ als Parameter schreibt in die 32-Bit-Ansicht (WOW6432Node).")
            if ra != "user":
                notes.append("Einträge unter HKEY_CURRENT_USER landen im Profil des ausführenden Kontos – für Benutzereinstellungen "
                             "„Ausführen als: Angemeldeter Benutzer“ wählen.")
        if st.get("detect_name"):
            lines.insert(0, f"Vorher: Prüfen, ob „{st['detect_name']}“ in Version ≥ {st.get('detect_version') or pkg.get('version') or 'beliebig'} installiert ist – dann überspringen")
        src = ((st.get("folder") or "") + "\\" + f).strip("\\")
        what = ("der Ordner „" + (st.get("folder") or "\\") + "“") if st.get("copy_folder", True) else ("die Datei „" + src + "“")
        notes.append(f"Vorher wird {what} aus dem Repository nach {RUN_DIR} kopiert und nach dem Schritt wieder gelöscht.")
    elif t == "uninstall":
        lines.append(f"Sucht auf dem Computer in der Registry (Programme und Features) nach „{st['match_name']}“"
                     + (f" in Version „{st['match_version']}“" if st.get("match_version") else "") + " und führt je Treffer aus:")
        if st.get("command"):
            lines.append("  " + st["command"] + "   (eigener Befehl; {ProductCode}, {UninstallString}, {QuietUninstallString}, {InstallLocation} werden ersetzt)")
        else:
            lines += ["  MSI:        msiexec.exe /x {ProductCode} /qn /norestart",
                      "  sonst:      QuietUninstallString des Herstellers",
                      "  sonst:      bekannter Uninstaller mit Silent-Schalter (Inno /VERYSILENT, NSIS /S, Mozilla helper.exe /S, …)",
                      "  sonst:      winget uninstall (bei sicherer Zuordnung)"]
        notes.append("Installationen im Benutzerprofil (HKEY_USERS) werden automatisch als der jeweilige Benutzer deinstalliert, "
                     "sofern „Ausführen als“ auf „Wie Paket“ steht. Gespeicherte Deinstallations-Vorlagen haben Vorrang.")
        if st.get("cleanup"):
            notes.append("Bleibt der Registry-Eintrag danach übrig, wird er entfernt.")
        notes.append("Nicht installiert: Schritt gilt als " + ("erfolgreich (übersprungen)." if st.get("missing_ok", True) else "fehlgeschlagen."))
    elif t == "command":
        lines.append(f'cmd.exe /c "{RUN_DIR}\\pk_command.cmd"')
        lines += ["  " + x for x in st["command"].splitlines()[:30]]
    elif t == "powershell":
        lines.append(f'{util.PS_EXE} {util.ps_file_arg(RUN_DIR + chr(92) + "pk_script.ps1")}')
        notes.append(PS_NOTE)
        lines += ["  " + x for x in st["script"].splitlines()[:30]]
    elif t == "filecopy":
        tgt = st["target"]
        lines.append(f'robocopy.exe "{RUN_DIR}\\payload" "{tgt}" /E /XJ /R:1 /W:2 /NP{" /XC /XN /XO" if not st.get("overwrite", True) else ""}')
        notes.append(f"Vorher wird „{st['source']}“ aus dem Repository nach {RUN_DIR}\\payload kopiert.")
    elif t == "registry":
        lines += [reg_line(e, st.get("view", "64")) for e in st["entries"][:60]]
        if len(st["entries"]) > 60:
            lines.append(f"… und {len(st['entries']) - 60} weitere")
        notes.append("Ausgeführt per PowerShell/.NET in der 32-Bit-Ansicht (WOW6432Node); die reg.exe-Zeilen zeigen die gleichwertige Wirkung. "
                     "Fehlt ein Schlüssel, wird er beim Setzen angelegt." if st.get("view") == "32" else
                     "Ausgeführt per PowerShell/.NET in der 64-Bit-Ansicht; die reg.exe-Zeilen zeigen die gleichwertige Wirkung. "
                     "Fehlt ein Schlüssel, wird er beim Setzen angelegt.")
        if ra != "user" and any(e["path"].startswith("HKCU") for e in st["entries"]):
            notes.append("HKCU-Einträge landen im Profil des ausführenden Kontos – für Benutzereinstellungen „Ausführen als: Angemeldeter Benutzer“ wählen.")
    elif t == "nested":
        pname = db.val("SELECT name FROM packages WHERE id=?", (st["package_id"],), "?")
        lines.append(f"Führt die Schritte des Pakets „{pname}“ an dieser Stelle aus (mit dessen Bedingungen).")
    elif t == "scan":
        lines.append("Inventar-Scan durch POCKET KNIFE (WinRM) – " + step_title(st))
    elif t == "reboot":
        lines.append(f'shutdown.exe /r /t {st.get("delay_sec", 60)} /c "{reboot_text(st)}"')
        notes.append(f"Danach wartet POCKET KNIFE bis zu {st.get('wait_minutes', 20)} Minuten, bis der Computer wieder erreichbar ist, und macht mit dem nächsten Schritt weiter.")
    elif t == "sleep":
        lines.append(f"Warten: {st['seconds']} Sekunden (auf dem Server, ohne Verbindung zum Client)")
    elif t == "message":
        lines.append(f'msg.exe * {"/TIME:" + str(st["timeout_sec"]) + " " if st.get("timeout_sec") else ""}"{st["text"]}"')
    elif t == "logoff":
        lines.append("logoff.exe <Sitzung> für jede " + ("getrennte " if st.get("only_disconnected") else "") + "Benutzersitzung (ermittelt mit quser.exe)")
    if t == "uninstall":
        ura = uninstall_run_as(st)
        acct = run_as_label(ura) if ura and (ura == "user" or (st.get("command") or "").strip()) else \
            "Lokales System – bei Benutzer-Installationen als der jeweilige Benutzer"
        lines.insert(0, f"Je Treffer als {acct}:")
    elif t in ("install", "command", "powershell", "filecopy", "registry"):
        acct = run_as_label(ra)
        lines.insert(0, f"Direkt als {acct} (eigener Prozess, keine geplante Aufgabe):" if ra == "deploy"
                     else f"Kurzlebige geplante Aufgabe als {acct} (lokale Anmeldung, Kennwort nur während des Schritts):" if ra == "service"
                     else f"Geplante Aufgabe (ohne Kennwort) als {acct}:")
    else:
        lines.insert(0, "Direkt über WinRM (Dienstkonto):" if t in ("message", "logoff", "reboot") else "Auf dem POCKET KNIFE-Server:")
    return {"command": "\n".join(lines), "run_as": ra, "run_as_label": run_as_label(ra), "notes": notes}


# --- Scripts per step ----------------------------------------------------------------------
PS_NOTE = ("Verweigert Windows die Skriptdatei (Ausführungsrichtlinie per GPO, Signatur, Sicherheitszone), lädt POCKET KNIFE "
           "ihren Inhalt und führt ihn aus – Parameter, $PSScriptRoot und Exitcode bleiben gleich.")


def _write_file(name, text, encoding="UTF8"):
    # 2.38: without .NET (Constrained Language Mode) - see util.ps_write; .cmd in the console code page
    return f"$pkF = Join-Path $PKDir '{name}'\n" + util.ps_write("$pkF", text, cmd=(encoding == "OEM"))


def body_for(st, pkg):
    t = st["type"]
    if t == "install" and st["source"] == "winget":
        return deploy._winget_body({**st, "type": "winget"})
    if t == "install":
        legacy = {"install_type": st["install_type"], "install_file": st["install_file"], "arguments": st.get("arguments"),
                  "detect_name": st.get("detect_name"), "version": st.get("detect_version") or pkg.get("version") or ""}
        return deploy._custom_body(legacy)
    if t == "command":
        return _write_file("pk_command.cmd", "@echo off\r\n" + st["command"].replace("\r\n", "\n").replace("\n", "\r\n") + "\r\n", "OEM") + \
            "PK-Run 'cmd.exe' ('/c \"' + $pkF + '\"')\n"
    if t == "powershell":
        return _write_file("pk_script.ps1", st["script"]) + \
            "PK-RunPs1 $pkF ''\n"
    if t == "filecopy":
        tgt = util.psq(st["target"])
        extra = " /XC /XN /XO" if not st.get("overwrite", True) else ""
        return (util.PS_EXPAND + f"$pkT = PK-Exp {tgt}\n"
                "New-Item -ItemType Directory -Path $pkT -Force | Out-Null\n"
                # robocopy reads \" as a quotation mark: remove the trailing \ (drive: C:\ -> C:\.)
                "$pkT = $pkT.TrimEnd('\\'); if ($pkT -match '^[A-Za-z]:$') { $pkT += '\\.' }\n"
                f"PK-Run 'robocopy.exe' ('\"' + (Join-Path $PKDir 'payload') + '\" \"' + $pkT + '\" /E /XJ /R:1 /W:2 /NP /NJH /NJS{extra}')\n"
                "if ($global:PKExit -lt 8) { Write-Output 'Kopieren erfolgreich.'; $global:PKExit = 0 }\n")
    if t == "registry":
        return reg_body(st)
    raise ValueError("Kein Client-Schritt")


REG_FUNCS = r"""
$global:PKRegFail = 0
# 2.38.2: Values with names like Password/Kennwort/Secret/Token never go into the output (it ends up in the log of the
# deployment) - e.g. the VNC password is encrypted only with a known key
function PK-RegShow([string]$Name, $Data) {
  if ($Name -match '__SECRET_RX__') { return '******** (verborgen)' }
  if ($Data -is [array]) { ($Data -join '; ') } else { [string]$Data }
}
function PK-RegBase([string]$Hive, [string]$View) {
  $h = @{ HKLM = 'LocalMachine'; HKCU = 'CurrentUser'; HKU = 'Users'; HKCR = 'ClassesRoot'; HKCC = 'CurrentConfig' }[$Hive]
  [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]$h, $(if ($View -eq '32') { 'Registry32' } else { 'Registry64' }))
}
# 2.38: Constrained Language Mode (software restriction, AppLocker, App Control) - no .NET registry classes there,
# but the registry cmdlets. 32-bit view: HKLM\SOFTWARE -> HKLM\SOFTWARE\WOW6432Node (only there does Windows redirect).
function PK-RegPath([string]$Hive, [string]$Path, [string]$View) {
  $root = @{ HKLM = 'HKEY_LOCAL_MACHINE'; HKCU = 'HKEY_CURRENT_USER'; HKU = 'HKEY_USERS'; HKCR = 'HKEY_CLASSES_ROOT'; HKCC = 'HKEY_CURRENT_CONFIG' }[$Hive]
  $p = $Path
  if ($View -eq '32' -and $Hive -eq 'HKLM' -and $p -match '^(?i)software(\\|$)' -and $p -notmatch '^(?i)software\\wow6432node(\\|$)') {
    $p = 'SOFTWARE\WOW6432Node' + $p.Substring(8)
  }
  'Registry::' + $root + '\' + $p
}
function PK-RegClm([string]$Action, [string]$Hive, [string]$Path, [string]$Name, [string]$Kind, $Data, [string]$View, [string]$label) {
  $rp = PK-RegPath $Hive $Path $View
  $n = if ($Name) { $Name } else { '(default)' }
  switch ($Action) {
    'set' {
      if (-not (Test-Path -LiteralPath $rp)) { New-Item -Path $rp -Force -ErrorAction Stop | Out-Null }
      New-ItemProperty -LiteralPath $rp -Name $n -Value $Data -PropertyType $Kind -Force -ErrorAction Stop | Out-Null
      Write-Output ('OK      gesetzt     ' + $label + ' = ' + (PK-RegShow $Name $Data))
    }
    'create_key' {
      if (-not (Test-Path -LiteralPath $rp)) { New-Item -Path $rp -Force -ErrorAction Stop | Out-Null }
      Write-Output ('OK      angelegt    ' + $label)
    }
    'delete_value' {
      $have = (Test-Path -LiteralPath $rp) -and (@((Get-Item -LiteralPath $rp).Property) -contains $(if ($Name) { $Name } else { '(default)' }))
      if ($have) { Remove-ItemProperty -LiteralPath $rp -Name $n -ErrorAction Stop; Write-Output ('OK      gelöscht    ' + $label) }
      else { Write-Output ('OK      nicht vorh. ' + $label) }
    }
    'delete_key' {
      if (Test-Path -LiteralPath $rp) { Remove-Item -LiteralPath $rp -Recurse -Force -ErrorAction Stop; Write-Output ('OK      gelöscht    ' + $label) }
      else { Write-Output ('OK      nicht vorh. ' + $label) }
    }
  }
}
function PK-Reg([string]$Action, [string]$Hive, [string]$Path, [string]$Name, [string]$Kind, $Data, [string]$View) {
  $label = $Hive + '\' + $Path + $(if ($Action -in 'set', 'delete_value') { ' → ' + $(if ($Name) { $Name } else { '(Standard)' }) } else { '' })
  if ($ExecutionContext.SessionState.LanguageMode -ne 'FullLanguage') {
    try { PK-RegClm $Action $Hive $Path $Name $Kind $Data $View $label }
    catch { $global:PKRegFail++; Write-Output ('FEHLER  ' + $label + ': ' + $_.Exception.Message) }
    return
  }
  $b = $null; $k = $null
  try {
    $b = PK-RegBase $Hive $View
    switch ($Action) {
      'set' {
        $k = $b.CreateSubKey($Path)
        $k.SetValue($Name, $Data, [Microsoft.Win32.RegistryValueKind]$Kind)
        Write-Output ('OK      gesetzt     ' + $label + ' = ' + (PK-RegShow $Name $Data))
      }
      'create_key' { $k = $b.CreateSubKey($Path); Write-Output ('OK      angelegt    ' + $label) }
      'delete_value' {
        $k = $b.OpenSubKey($Path, $true)
        if ($k -and ($k.GetValueNames() -contains $Name)) { $k.DeleteValue($Name); Write-Output ('OK      gelöscht    ' + $label) }
        else { Write-Output ('OK      nicht vorh. ' + $label) }
      }
      'delete_key' {
        $k = $b.OpenSubKey($Path)
        if ($k) { $k.Close(); $k = $null; $b.DeleteSubKeyTree($Path); Write-Output ('OK      gelöscht    ' + $label) }
        else { Write-Output ('OK      nicht vorh. ' + $label) }
      }
    }
  } catch {
    $global:PKRegFail++
    $m = if ($_.Exception.InnerException) { $_.Exception.InnerException.Message } else { $_.Exception.Message }
    Write-Output ('FEHLER  ' + $label + ': ' + $m)
  } finally {
    if ($k) { $k.Close() }
    if ($b) { $b.Close() }
  }
}
"""


# Names of registry values whose content never appears in output, log or preview (2.38.2)
SECRET_VALUE_RX = r"(?i)pass(word|wd|phrase)?|pwd|kennwort|secret|token|credential|private.?key|api.?key"
REG_FUNCS = REG_FUNCS.replace("__SECRET_RX__", SECRET_VALUE_RX)


def _ps_data(e):
    k, d = e["kind"], e["data"]
    # 2.38: convert signed values here already (instead of [BitConverter] - not allowed in Constrained Language Mode)
    if k == "REG_DWORD":
        v = int(d) & 0xFFFFFFFF
        return f"([int]{v - (1 << 32) if v >= (1 << 31) else v})"
    if k == "REG_QWORD":
        v = int(d) & 0xFFFFFFFFFFFFFFFF
        return f"([long]{v - (1 << 64) if v >= (1 << 63) else v})"
    if k == "REG_BINARY":
        return "([byte[]]@(" + ",".join("0x" + b for b in d.split(",") if b) + "))" if d else "([byte[]]@())"
    if k == "REG_MULTI_SZ":
        items = [x for x in d.replace("\r\n", "\n").split("\n")]
        return "([string[]]@(" + ",".join(util.psq(x) for x in items) + "))"
    return util.psq(d)


def reg_body(st):
    view = st.get("view", "64")
    out = [REG_FUNCS]
    for e in st["entries"]:
        hive, sub = split_reg_path(e["path"])
        kind = REG_KINDS.get(e.get("kind"), "String")
        data = _ps_data(e) if e["action"] == "set" else "$null"
        out.append(f"PK-Reg {util.psq(e['action'])} {util.psq(hive)} {util.psq(sub)} {util.psq(e.get('name') or '')} "
                   f"{util.psq(kind)} {data} {util.psq(view)}")
    out.append("if ($global:PKRegFail) { Write-Output ([string]$global:PKRegFail + ' Eintrag/Einträge fehlgeschlagen.'); $global:PKExit = 1 } "
               "else { $global:PKExit = 0 }")
    return "\n".join(out) + "\n"


def evaluate(st, code):
    """(success, restart_needed)"""
    if st["type"] == "install" and st.get("source") == "winget":
        return deploy.evaluate({"type": "winget"}, code)
    codes = {int(x) for x in (st.get("success_codes") or "0").split(",") if re.match(r"^-?\d+$", x.strip())}
    return code in codes, code in deploy.REBOOT_CODES


# --- Execution per target computer ---------------------------------------------------------
class Runner:
    def __init__(self, did, tid, computer, cancel, set_status, user=None):
        self.did, self.tid, self.c = did, tid, computer
        self.host = computer["dns_name"] or computer["name"]
        self.cancel = cancel
        self.set_status = set_status
        self.user = user
        # Random part in the working folder name: not predictable (no pre-creation by users)
        self.token = __import__("secrets").token_hex(4)
        self.n = 0
        self.results = []
        self.log = []
        self.reboot_needed = False
        self.last_code = 0

    # helpers
    def _check_cancel(self):
        if self.cancel():
            raise remote.Cancelled("Abgebrochen")

    def _record(self, phase, st, status, msg, code=None, out="", t0=None, ra=None, depth=0):
        title = ("  " * depth) + step_title(st)
        r = {"n": len(self.results) + 1, "phase": phase, "type": st["type"], "title": title, "status": status,
             "message": msg, "exit": code, "run_as": ra, "started": t0, "finished": util.now(),
             "sid": st.get("id"), "depth": depth,
             # 2.39.2: account as text for the "Ausgeführt als" column (what really ran - empty if nothing ran on the client)
             "run_as_label": run_as_label(ra) if ra else ""}
        self.results.append(r)
        head = f"=== {r['n']}. {dict(PHASES)[phase]}: {title}" + (f" (als {run_as_label(ra)})" if ra else "") + " ==="
        self.log.append(head + ("\n" + out.strip() if out and out.strip() else "") + (f"\n→ {msg}\n" if status != "running" else "\n"))
        return r

    def check(self, conds):
        """(ok, reason)"""
        from . import collections_
        for cc in conds.get("collections") or []:
            if cc["id"] == 0:  # pseudo collection "Alle Computer"
                mem, name = True, "Alle Computer"
            else:
                # Audit 2.29: if the collection is missing or cannot be evaluated right now, the condition counts as NOT
                # met (both kinds) - otherwise "not a member of exceptions" would silently become "install everywhere"
                name = db.val("SELECT name FROM collections WHERE id=?", (cc["id"],))
                if name is None:
                    return False, f"Sammlung #{cc['id']} aus der Bedingung gibt es nicht mehr – Paket-Bedingung anpassen"
                try:
                    mem = self.c["id"] in collections_.member_ids(cc["id"])
                except Exception as e:  # noqa: BLE001
                    return False, f"Mitgliedschaft in „{name}“ nicht prüfbar ({str(e)[:120]}) – sicherheitshalber übersprungen"
            if cc["mode"] == "member" and not mem:
                return False, f"nicht Mitglied der Sammlung „{name}“"
            if cc["mode"] == "not_member" and mem:
                return False, f"Mitglied der Sammlung „{name}“"
        if client_checks(conds):
            out, err, _ = remote.ps(self.host, cond_script(conds))
            if "PK_COND_FAIL:" in out:
                return False, util.first_line(out.split("PK_COND_FAIL:", 1)[1], "Bedingung nicht erfüllt")
            if "PK_COND_OK" not in out:
                return False, "Bedingungen konnten nicht geprüft werden: " + (err or out)[:300]
        return True, ""

    def run_package(self, pkg, depth=0, phase_label="main"):
        """'success' | 'failed' | 'skipped'"""
        pkg = normalize(pkg)
        ok, why = self.check(pkg["conditions"])
        if not ok:
            self.log.append(f"=== Paket „{pkg['name']}“ übersprungen: {why} ===\n")
            return "skipped", why
        failed, fail_msg = False, ""
        soft = []  # several packages (schedule/multi-selection) with "continue after error": still "failed" at the end
        for phase in ("pre", "main"):
            for st in pkg["steps"][phase]:
                if failed:
                    self._record(phase, st, "notrun", "Nicht ausgeführt (vorheriger Schritt fehlgeschlagen)", depth=depth)
                    continue
                res, msg = self.run_step(st, pkg, phase, depth)
                if res == "failed" and st.get("on_error", "stop") == "stop":
                    failed, fail_msg = True, f"{step_title(st)}: {msg}"
                elif res == "failed" and pkg.get("composite") and st.get("type") == "nested":
                    soft.append(f"{step_title(st)}: {msg}")
        for st in pkg["steps"]["post"]:
            if failed and st.get("type") == "reboot":
                # 2.39.3: completion steps run after an error too (clean-up) - but never a restart
                self._record("post", st, "notrun", "Nicht ausgeführt – kein Neustart nach einem fehlgeschlagenen Schritt", depth=depth)
                continue
            res, msg = self.run_step(st, pkg, "post", depth)
            if res == "failed" and st.get("on_error", "stop") == "stop" and not failed:
                failed, fail_msg = True, f"{step_title(st)}: {msg}"
        if not failed and soft:
            # Otherwise the computer would count as supplied: no new attempt, list green although a package is missing
            return "failed", f"{len(soft)} Paket(e) fehlgeschlagen – " + "; ".join(soft)
        return ("failed", fail_msg) if failed else ("success", "")

    def run_step(self, st, pkg, phase, depth=0):
        self._check_cancel()
        t0 = util.now()
        if not st.get("enabled", True):
            self._record(phase, st, "skipped", "Deaktiviert", t0=t0, depth=depth)
            return "skipped", ""
        self.n += 1
        self.set_status(f"Schritt {self.n}: {step_title(st)}")
        try:
            ok, why = self.check(st.get("conditions") or default_conditions())
        except remote.RemoteError as e:
            self._record(phase, st, "failed", f"Bedingungen nicht prüfbar: {e}", t0=t0, depth=depth)
            return "failed", str(e)
        if not ok:
            self._record(phase, st, "skipped", f"Übersprungen – Bedingung: {why}", t0=t0, depth=depth)
            return "skipped", why
        t = st["type"]
        try:
            if t in ("install", "command", "powershell", "filecopy", "registry"):
                return self._client_step(st, pkg, phase, depth, t0)
            if t == "uninstall":
                return self._uninstall(st, pkg, phase, depth, t0)
            if t == "nested":
                sub = st.get("package") or expand(db.q1("SELECT * FROM packages WHERE id=?", (st["package_id"],)) or {})
                rec = self._record(phase, st, "running", "läuft …", t0=t0, depth=depth)
                try:
                    res, msg = self.run_package(sub, depth + 1)
                except remote.Cancelled:
                    rec["status"], rec["message"] = "failed", "Abgebrochen"
                    raise
                except Exception as e:
                    rec["status"], rec["message"], rec["finished"] = "failed", str(e)[:1000], util.now()
                    return "failed", str(e)[:300]
                rec["status"] = res
                rec["message"] = {"success": "Erfolgreich", "skipped": f"Übersprungen – {msg}", "failed": f"Fehlgeschlagen – {msg}"}[res]
                rec["finished"] = util.now()
                self.log.append(f"=== Ende {step_title(st)} → {rec['message']} ===\n")
                return res, msg
            if t == "scan":
                return self._scan(st, phase, depth, t0)
            if t == "reboot":
                return self._reboot(st, phase, depth, t0)
            if t == "sleep":
                end = time.time() + st["seconds"]
                while time.time() < end:
                    self._check_cancel()
                    time.sleep(min(2, max(0, end - time.time())))
                self._record(phase, st, "success", f"{st['seconds']} Sekunden gewartet", t0=t0, depth=depth)
                return "success", ""
            if t == "message":
                tm = f"/TIME:{st['timeout_sec']} " if st.get("timeout_sec") else ""
                # Audit 2.29: text sanitized (no " - Windows PowerShell 5.1 otherwise splits the argument when passing it to msg.exe)
                txt = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", st["text"]).replace('"', "'").rstrip().rstrip("\\") or "-"
                out, err, _ = remote.ps(self.host, f"& msg.exe * {tm}{util.psq(txt)} 2>&1; 'Exitcode: ' + $LASTEXITCODE")
                mrc = re.search(r"Exitcode:\s*(-?\d+)", out or "")
                if mrc and int(mrc.group(1)) != 0:
                    # msg.exe reports e.g. 1 if nobody is logged on - no reason to abort the package
                    self._record(phase, st, "success", f"Nachricht nicht zugestellt (Exitcode {mrc.group(1)}, evtl. niemand angemeldet)",
                                 out=out, t0=t0, ra="deploy", depth=depth)
                    return "success", ""
                self._record(phase, st, "success", "Nachricht gesendet", out=out, t0=t0, ra="deploy", depth=depth)
                return "success", ""
            if t == "logoff":
                return self._logoff(st, phase, depth, t0)
        except remote.Cancelled:
            raise
        except Exception as e:
            self._record(phase, st, "failed", str(e)[:1000], t0=t0, depth=depth)
            return "failed", str(e)[:300]
        self._record(phase, st, "failed", "Unbekannter Schritt", t0=t0, depth=depth)
        return "failed", "Unbekannter Schritt"

    def _client_step(self, st, pkg, phase, depth, t0):
        # 2.36: new winget versions held back -> the last approved version (before the winget cache: it keeps
        # exactly this version ready)
        if st["type"] == "install" and st.get("source") == "winget":
            from . import wingetrelease
            try:
                st2, why = wingetrelease.apply_step(st, pkg)
            except Exception as e:
                # 2.36.2: not checkable -> do not simply install the latest (possibly blocked) version
                why = f"Freigabe der winget-Version nicht prüfbar: {str(e)[:200]}"
                self._record(phase, st, "failed", why, t0=t0, depth=depth)
                return "failed", why
            if st2 is None:
                self._record(phase, st, "skipped", why, t0=t0, depth=depth)
                return "skipped", why
            if st2 is not st:
                pin = st2["winget_version"]
                # never downgrade: if the same or a newer version is already installed, do nothing
                have = [r["version"] for r in db.q("SELECT version FROM software WHERE computer_id=? AND lower(winget_id)=?",
                                                    (self.c.get("id"), str(st2.get("winget_id") or "").lower())) if r["version"]]
                if any(util.vcmp(str(v), pin) >= 0 for v in have):
                    why = f"{st2.get('release_note')} – installiert ist schon {max(have, key=__import__('functools').cmp_to_key(lambda a, b: util.vcmp(str(a), str(b))))}"
                    self._record(phase, st, "skipped", why, t0=t0, depth=depth)
                    return "skipped", why
                st = {**st2, "title": f"{step_title(st)} – freigegebene Version {pin} ({st2.get('release_note')})"}
        # 2.35.1: computer without winget - winget step from the winget cache, if it is available there
        if st["type"] == "install" and st.get("source") == "winget":
            try:
                from . import wingetcache
                st = wingetcache.step_for(st, self.c, pkg) or st
            except Exception:
                pass
        ra, note = effective_run_as(st, pkg)
        name = f"{self.did}_{self.tid}_{self.n}_{self.token}"
        try:
            if st["type"] == "install" and st.get("source") == "file":
                self.set_status(f"Schritt {self.n}: kopiere Dateien …")
                if st.get("copy_folder", True) and (st.get("folder") or "").strip("\\/"):
                    remote.copy_package(st.get("folder") or "", self.host, name)
                else:
                    remote.copy_package(st.get("folder") or "", self.host, name, single_file=st["install_file"])
            elif st["type"] == "install" and st.get("source") == "winget":
                self._winget_offline_copy(name)
            elif st["type"] == "filecopy":
                self.set_status(f"Schritt {self.n}: kopiere Dateien …")
                kind = remote.repo_stat(st["source"])
                if kind is None:
                    raise remote.RemoteError(f"Quelle im Repository nicht gefunden: {st['source']}")
                if kind == "dir":
                    remote.copy_package(st["source"], self.host, name + "\\payload")
                else:
                    parent, _, fn = st["source"].replace("/", "\\").rpartition("\\")
                    remote.copy_package(parent, self.host, name + "\\payload", single_file=fn)
            self.set_status(f"Schritt {self.n}: {step_title(st)} …")
            timeout = (st.get("timeout_min") or pkg.get("timeout_min") or db.setting("deploy_timeout_minutes")) * 60
            code, out = remote.run_as_system(self.host, body_for(st, pkg), name, timeout_s=timeout, cancel=self.cancel,
                                             run_as=ra, run_user_sid=pkg.get("run_user_sid"))
        except BaseException:
            # Even on abort or a half-finished copy: do not leave copied files on the target
            remote.remove_package_copy(self.host, name)
            raise
        if st["type"] == "install" and st.get("source") == "winget" and code == 9010:
            self._record(phase, st, "skipped", deploy.WINGET_MESSAGES[9010], code=code, out=out, t0=t0, ra=ra, depth=depth)
            return "skipped", "winget wird auf diesem Betriebssystem nicht unterstützt"
        ok, reboot = evaluate(st, code)
        self.last_code = code
        n_err, out = remote.ps_errors(out)
        ps_fail = ""
        if ok and n_err and st["type"] == "powershell" and not st.get("ignore_errors"):
            # 2.39.3: PowerShell reported errors, the script still ended with 0 - not a success (before: "Erfolgreich",
            # e.g. Windows Update with "Zugriff verweigert" followed by a restart)
            ok, reboot = False, False
            ps_fail = (f"Fehlgeschlagen – PowerShell hat {n_err} Fehler gemeldet, das Skript endete aber mit Exitcode {code} "
                       "(im Skript $ErrorActionPreference = 'Stop' oder exit 1 verwenden; bewusst ignorieren: Option am Schritt)")
        if reboot:
            self.reboot_needed = True
        msg = deploy.WINGET_MESSAGES.get(code, "") if st["type"] == "install" and st.get("source") == "winget" else ""
        if msg and not ok:
            msg = f"Fehlgeschlagen – {msg} (Exitcode {code})"  # plain text and code: both are needed for troubleshooting
        elif not ok:
            # 2.32.5: hardened Windows (9013-9016) - only on failure, the step's success codes take precedence
            code, hard = remote.apply_hard(code, out)
            if hard:
                self.last_code = code
                msg = f"Fehlgeschlagen – {hard}"
        if ps_fail:
            msg = ps_fail
        if not msg:
            msg = "Erfolgreich" if ok else f"Fehlgeschlagen (Exitcode {code})"
        if not ok:
            msg += remote.wu_hint(out, ra)
        if ok and "Bereits installiert" in (out or ""):
            msg = "Bereits installiert – übersprungen"
        if ok and reboot and "Neustart erforderlich" not in msg:
            msg += " – Neustart erforderlich"
        if note:
            out = f"Hinweis: {note}\n{out or ''}"
        if ra == "user":
            # 2.37: working folder and result file then belong to the logged-on user - he could change them
            msg += " (Ergebnis vom angemeldeten Benutzer gemeldet)"
        self._record(phase, st, "success" if ok else "failed", msg, code=code, out=out, t0=t0, ra=ra, depth=depth)
        return ("success" if ok else "failed"), msg

    def _find_installed(self, st):
        """Matching entries in "Programme und Features" - live via WinRM, otherwise from the last scan."""
        import fnmatch
        from . import uninstall
        script = r"""$ErrorActionPreference = 'SilentlyContinue'
$roots = @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall', 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall')
foreach ($u in (Get-ChildItem Registry::HKEY_USERS | Where-Object { $_.PSChildName -match '^S-1-5-21-[\d-]+$' })) {
  $roots += "Registry::HKEY_USERS\$($u.PSChildName)\Software\Microsoft\Windows\CurrentVersion\Uninstall" }
$out = foreach ($r in $roots) { Get-ChildItem -Path $r | ForEach-Object {
  $k = $_; $dn = "" + $k.GetValue('DisplayName'); if (-not $dn) { return }
  $parts = $k.Name -split '\\', 2
  [ordered]@{ name = $dn.Trim(); version = ("" + $k.GetValue('DisplayVersion')).Trim(); key = $k.PSChildName
    uninstall = ("" + $k.GetValue('UninstallString')).Trim(); quiet = ("" + $k.GetValue('QuietUninstallString')).Trim()
    location = ("" + $k.GetValue('InstallLocation')).Trim(); hive = $parts[0]; reg_path = $parts[1] } } }
'PKJSON:' + (ConvertTo-Json -InputObject @($out) -Depth 3 -Compress)"""
        rows, source = [], "live"
        try:
            out, _e, _ = remote.ps(self.host, script)
            i = out.find("PKJSON:")
            if i < 0:
                raise remote.RemoteError("keine Daten")
            data = json.loads(util.first_line(out[i + 7:], "[]"))
            data = data if isinstance(data, list) else [data]
            for x in data:
                rows.append({"name": x.get("name"), "version": x.get("version"), "product_code": x.get("key"),
                             "uninstall_string": x.get("uninstall"), "quiet_uninstall": x.get("quiet"),
                             "install_location": x.get("location"), "hive": x.get("hive"), "reg_path": x.get("reg_path")})
        except (remote.RemoteError, ValueError):
            source = "Scan"
            rows = db.q("SELECT * FROM software WHERE computer_id=?", (self.c["id"],))
        pat, ver = st["match_name"].lower(), (st.get("match_version") or "").lower()
        hits = [r for r in rows if fnmatch.fnmatch((r.get("name") or "").lower(), pat)
                and (not ver or fnmatch.fnmatch((r.get("version") or "").lower(), ver))]
        # add the winget mapping from the inventory (for the winget path)
        inv = {r["name"]: r for r in db.q("SELECT name, winget_id, winget_sure FROM software WHERE computer_id=?", (self.c["id"],))}
        seen, out = set(), []
        for r in hits:
            k = (r.get("hive"), r.get("reg_path"), r.get("name"))
            if k in seen:
                continue
            seen.add(k)
            r = dict(r)
            if r["name"] in inv and not r.get("winget_id"):
                r["winget_id"], r["winget_sure"] = inv[r["name"]]["winget_id"], inv[r["name"]]["winget_sure"]
            out.append(r)
        return out, source, uninstall

    def _uninstall(self, st, pkg, phase, depth, t0):
        hits, source, uninstall = self._find_installed(st)
        if not hits:
            msg = f"Nicht installiert ({'live geprüft' if source == 'live' else 'laut letztem Scan'})"
            self._record(phase, st, "skipped" if st.get("missing_ok", True) else "failed", msg, t0=t0, depth=depth)
            return ("skipped" if st.get("missing_ok", True) else "failed"), msg
        if not st.get("all_matches", True):
            hits = hits[:1]
        results, outs, accts = [], [], []
        explicit = uninstall_run_as(st)
        for sw in hits:
            self._check_cancel()
            p2 = uninstall.package_for(sw, st.get("command") or None, explicit, bool(st.get("cleanup")))
            # User entries always run in the context of this user (see uninstall.package_for)
            ra = p2.get("run_as") if p2.get("run_as_forced") else (explicit or p2.get("run_as") or "system")
            if p2.get("type") == "winget":
                ra = "system" if ra in ("deploy", "service") else ra
            if ra in ("deploy", "service") and not (st.get("command") or "").strip():
                # The command comes from the client's registry (UninstallString) - whoever is administrator there (e.g. an
                # OU admin) determines it. Never run it as the domain-wide service account, but as Local System.
                ra = "system"
            # 2.32.5: body matching the kind (winget uninstall or command) - the two were swapped before
            body = deploy.build_body(p2)
            self.n += 1
            name = f"{self.did}_{self.tid}_{self.n}_{self.token}"
            self.set_status(f"Schritt: deinstalliere {sw['name']} …")
            timeout = (st.get("timeout_min") or pkg.get("timeout_min") or db.setting("deploy_timeout_minutes")) * 60
            code, out = remote.run_as_system(self.host, body, name, timeout_s=timeout, cancel=self.cancel, run_as=ra,
                                             run_user_sid=p2.get("run_user_sid"))
            ok, _rb = deploy.evaluate(p2, code)
            hard = ""
            if not ok and p2.get("type") != "winget":
                code, hard = remote.apply_hard(code, out)
            self.last_code = code
            results.append(ok)
            accts.append(ra)
            outs.append(f"--- {sw['name']} {sw.get('version') or ''} ({run_as_label(ra)}) ---\n{out or ''}\n→ "
                        + ("Erfolgreich" if ok else f"Fehlgeschlagen – {hard}" if hard else f"Fehlgeschlagen (Exitcode {code})"))
        n_ok = sum(1 for x in results if x)
        msg = f"{n_ok} von {len(results)} deinstalliert" if len(results) > 1 else ("Deinstalliert" if n_ok else f"Fehlgeschlagen (Exitcode {self.last_code})")
        status = "success" if n_ok == len(results) else "failed"
        # 2.39.2: account for the "Ausgeführt als" column - one account for all hits, otherwise "mixed" (each in the output)
        self._record(phase, st, status, msg, code=self.last_code, out="\n".join(outs), t0=t0,
                     ra=accts[0] if len(set(accts)) == 1 else "mixed", depth=depth)
        return status, msg

    def _winget_offline_copy(self, name):
        """If winget is missing, first copy the App Installer files from the repository to the client (2.37: the
        client never downloads from the internet itself; if they are not there yet, POCKET KNIFE loads them now)."""
        from . import wingetboot
        if not db.setting("winget_bootstrap"):
            return
        folder = wingetboot.folder()
        try:
            out, _e, _ = remote.ps(self.host, "if (Get-AppxPackage -AllUsers -Name Microsoft.DesktopAppInstaller -ErrorAction SilentlyContinue) "
                                              "{ 'PK_WG_OK' } else { 'PK_WG_MISSING' }")
        except remote.RemoteError:
            return
        if "PK_WG_MISSING" in out:
            if not wingetboot.present():
                self.set_status(f"Schritt {self.n}: lade den App-Installer (winget) einmal in das Repository …")
                try:
                    wingetboot.refresh(self.user)
                except Exception:
                    return  # the step reports "liegt noch nicht im Repository" (is not in the repository yet)
            self.set_status(f"Schritt {self.n}: kopiere den App-Installer (winget) aus dem Repository …")
            remote.copy_package(folder, self.host, name + "\\winget")

    def _scan(self, st, phase, depth, t0):
        from . import inventory, profiles
        prof = (profiles.get(st["profile_id"]) if st.get("profile_id") else None) or profiles.default()
        status = inventory.scan_now(self.c["id"], self.user, prof)
        ok = status == "OK"
        self._record(phase, st, "success" if ok else "failed", f"Scan mit „{prof['name']}“: {status}", t0=t0, ra="deploy", depth=depth)
        return ("success" if ok else "failed"), status

    def _reboot(self, st, phase, depth, t0):
        msg = reboot_text(st)
        boot_q = "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToString('yyyy-MM-dd HH:mm:ss')"
        before = remote.ps(self.host, boot_q)[0].strip()
        out, _e, _ = remote.ps(self.host, f"& shutdown.exe /r /t {int(st.get('delay_sec', 60))} /c {util.psq(msg)} 2>&1; 'PK_RC:' + $LASTEXITCODE")
        rc = out.rsplit("PK_RC:", 1)[-1].strip() if "PK_RC:" in out else "?"
        if rc not in ("0", "?"):
            self._record(phase, st, "failed", f"shutdown.exe meldet Fehler {rc} (z. B. bereits ein Herunterfahren geplant)", out=out,
                         t0=t0, ra="deploy", depth=depth)
            return "failed", f"shutdown.exe Fehler {rc}"
        self.set_status(f"Schritt {self.n}: Neustart – warte auf den Computer …")
        went_down = False
        limit = time.time() + int(st.get("delay_sec", 60)) + 300
        while time.time() < limit:
            self._check_cancel()
            time.sleep(5)
            if not remote.is_online(self.host)[1]:
                went_down = True
                break
        back = time.time() + st.get("wait_minutes", 20) * 60
        while time.time() < back:
            self._check_cancel()
            time.sleep(10)
            if remote.is_online(self.host)[1]:
                try:
                    out, _e, _ = remote.ps(self.host, "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToString('yyyy-MM-dd HH:mm:ss')")
                except remote.RemoteError:
                    continue
                if before and out.strip() == before:
                    continue  # not restarted yet
                time.sleep(20)  # let services start
                self.reboot_needed = False
                note = "" if went_down else " (Herunterfahren wurde nicht beobachtet)"
                self._record(phase, st, "success", f"Neu gestartet, wieder erreichbar (Start {out.strip()}){note}", t0=t0, ra="deploy", depth=depth)
                return "success", ""
        self._record(phase, st, "failed", f"Computer ist nach {st.get('wait_minutes', 20)} Minuten nicht wieder erreichbar", t0=t0, ra="deploy", depth=depth)
        return "failed", "nicht wieder erreichbar"

    def _logoff(self, st, phase, depth, t0):
        only = "$true" if st.get("only_disconnected") else "$false"
        script = r"""$only = __ONLY__
$n = 0
$lines = @(& quser.exe 2>$null | Select-Object -Skip 1)
foreach ($l in $lines) {
  $tok = @($l.Trim().TrimStart('>') -split '\s+')
  $id = $tok | Where-Object { $_ -match '^\d+$' } | Select-Object -First 1
  if (-not $id) { continue }
  $disc = $l -match '\s(Disc|Getr|Getrennt)\s'
  if ($only -and -not $disc) { continue }
  & logoff.exe $id 2>&1 | Out-Null
  'Abgemeldet: ' + $tok[0] + ' (Sitzung ' + $id + ')'
  $n++
}
'Sitzungen abgemeldet: ' + $n""".replace("__ONLY__", only)
        out, _err, _ = remote.ps(self.host, script)
        self._record(phase, st, "success", out.strip().splitlines()[-1] if out.strip() else "Keine Sitzungen", out=out,
                     t0=t0, ra="deploy", depth=depth)
        return "success", ""
