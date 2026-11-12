"""Tools: custom commands in folders, executable on selected computers.

Variables in the script/URL (replaced before execution):
  $(Computer)  computer name        $(Host)  DNS name
  $(IP)        first IP address     $(MAC)   first MAC address
  $(User)      logged-on user       $(Domain) domain
"""
import base64
import ipaddress
import json
import re
import secrets
import socket
import subprocess
import time
import threading

from . import config, db, pklog, remote, util

TYPES = {"powershell": "PowerShell", "cmd": "Befehl (CMD)", "url": "URL öffnen"}
RUN_AS = {"service": "Dienstkonto (schnell)", "system": "SYSTEM (geplante Aufgabe)"}
MAX_OUT = 200_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS tools(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, folder TEXT, type TEXT, script TEXT, run_as TEXT DEFAULT 'service',
  confirm INTEGER DEFAULT 0, description TEXT, sort INTEGER DEFAULT 100, created_at TEXT);
"""

# Example tools (freely editable or deletable)
SEED = [
    ("BITLOCKER", "TPM-Status", "powershell", "Get-Tpm | Format-List", "service", 0, "Zeigt den Zustand des TPM-Chips."),
    ("BITLOCKER", "BitLocker C: Status", "cmd", "manage-bde -status C:", "service", 0, ""),
    ("BITLOCKER", "BitLocker C: anhalten (1 Neustart)", "powershell",
     "Suspend-BitLocker -MountPoint 'C:' -RebootCount 1 | Format-List MountPoint, ProtectionStatus", "service", 1,
     "Hält den Schutz für einen Neustart an, z. B. vor einem BIOS-Update."),
    ("BITLOCKER", "BitLocker C: fortsetzen", "powershell", "Resume-BitLocker -MountPoint 'C:' | Format-List MountPoint, ProtectionStatus", "service", 1, ""),
    ("GPO", "GPUPDATE /force", "cmd", "echo N| gpupdate /target:computer /force", "service", 0,
     "Fragen nach Abmelden oder Neustart beantwortet das Tool immer mit Nein."),
    ("GPO", "GPRESULT (Computer)", "cmd", "gpresult /r /scope computer", "service", 0, ""),
    ("NETZWERK", "IP-Konfiguration", "cmd", "ipconfig /all", "service", 0, ""),
    ("NETZWERK", "DNS-Cache leeren und neu registrieren", "cmd", "ipconfig /flushdns\r\nipconfig /registerdns", "service", 0, ""),
    ("NETZWERK", "MAC-Adressen", "cmd", "getmac /v /fo list", "service", 0, ""),
    ("NETZWERK", "Offene Verbindungen", "powershell",
     "Get-NetTCPConnection -State Established | Sort-Object RemoteAddress | Format-Table LocalPort, RemoteAddress, RemotePort, OwningProcess -AutoSize",
     "service", 0, ""),
    ("DRUCKER", "Druckwarteschlange neu starten", "powershell",
     "Restart-Service -Name Spooler -Force; Get-Service Spooler | Format-List Name, Status", "service", 1, ""),
    ("DRUCKER", "Hängende Druckaufträge löschen", "powershell",
     "Stop-Service Spooler -Force\r\nRemove-Item \"$env:SystemRoot\\System32\\spool\\PRINTERS\\*\" -Force -ErrorAction SilentlyContinue\r\nStart-Service Spooler\r\n'Druckaufträge gelöscht, Spooler läuft wieder.'",
     "service", 1, ""),
    ("WINDOWS UPDATE", "Update-Verlauf (letzte 20)", "powershell",
     "$s = New-Object -ComObject Microsoft.Update.Session\r\n$h = $s.CreateUpdateSearcher()\r\n$n = $h.GetTotalHistoryCount()\r\n"
     "$h.QueryHistory(0, $(if ($n -lt 20) { $n } else { 20 })) | Select-Object Date, Title, @{n='Ergebnis';e={switch($_.ResultCode){2{'OK'}3{'OK mit Fehlern'}4{'Fehler'}5{'Abgebrochen'}default{$_.ResultCode}}}} | Format-Table -AutoSize -Wrap",
     "service", 0, ""),
    ("WINDOWS UPDATE", "Windows-Update-Dienste zurücksetzen", "powershell",
     "Stop-Service wuauserv, bits -Force\r\nRemove-Item \"$env:SystemRoot\\SoftwareDistribution\\Download\\*\" -Recurse -Force -ErrorAction SilentlyContinue\r\n"
     "Start-Service bits, wuauserv\r\n'Update-Cache geleert, Dienste neu gestartet.'", "service", 1, ""),
    ("SYSTEM", "Angemeldete Benutzer", "cmd", "quser", "service", 0, ""),
    ("SYSTEM", "Anwendungsfehler (24 Std.)", "powershell",
     "Get-WinEvent -FilterHashtable @{LogName='Application'; Level=2; StartTime=(Get-Date).AddDays(-1)} -MaxEvents 50 -ErrorAction SilentlyContinue | "
     "Format-Table TimeCreated, ProviderName, Id, Message -AutoSize -Wrap", "service", 0, ""),
    ("SYSTEM", "CPU-Last (5 Sekunden)", "powershell",
     "(Get-Counter '\\Processor(_Total)\\% Processor Time' -SampleInterval 1 -MaxSamples 5).CounterSamples | Format-Table Timestamp, @{n='CPU %';e={[int]($_.CookedValue * 10) / 10}}\r\n"
     "Get-Process | Sort-Object CPU -Descending | Select-Object -First 10 Name, Id, CPU, @{n='RAM MB';e={[int]($_.WorkingSet64/1MB)}} | Format-Table -AutoSize",
     "service", 0, ""),
    ("SYSTEM", "Datenträger prüfen (nur lesen)", "cmd", "chkdsk C: /scan", "service", 0, "Online-Prüfung ohne Reparatur."),
    ("SYSTEM", "Systemdateien prüfen (SFC)", "cmd", "sfc /scannow", "system", 1, "Dauert mehrere Minuten."),
    ("SYSTEM", "Uptime", "powershell", "$b = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime\r\n\"Gestartet: $b\"\r\n\"Laufzeit:  $((Get-Date) - $b)\"", "service", 0, ""),
    ("SYSTEM", "Batteriezustand", "powershell",
     "Get-CimInstance -Namespace root\\wmi -ClassName BatteryFullChargedCapacity -ErrorAction SilentlyContinue | ForEach-Object { $f = $_.FullChargedCapacity; "
     "$d = (Get-CimInstance -Namespace root\\wmi -ClassName BatteryStaticData).DesignedCapacity; \"Kapazität: $f von $d mWh ($([int](100*$f/$d))%)\" }",
     "service", 0, ""),
    ("WEB", "Web-Oberfläche des Computers (Port 80)", "url", "http://$(Host)/", "service", 0, ""),
]


# 2.32.4: keyboard shortcuts belong to the tool (the same for everyone, like menu entries): for a custom tool in the tool dialog,
# for built-in tools under Tools → "Tastenkürzel …" (with sensible defaults). Always Ctrl+Alt, optionally with
# Shift, plus A–Z, 0–9 or F1–F12. Stored in a fixed notation, e.g. "Ctrl+Alt+P".
BUILTIN_KEYS = ("ping", "rdpweb", "vncweb", "wake", "power", "abort", "gpupdate", "winget", "scan",
                "diskusage", "files", "eventlog", "command")
BUILTIN_NAMES = {"ping": "Ping & Ports", "rdpweb": "Remotedesktop im Browser", "vncweb": "VNC im Browser",
                 "wake": "Wake-on-LAN", "power": "Neustart / Herunterfahren …", "abort": "Neustart/Herunterfahren abbrechen",
                 "gpupdate": "gpupdate /force", "winget": "winget prüfen / nachinstallieren", "scan": "Scannen mit Profil …",
                 "diskusage": "Speicherplatz analysieren …", "files": "Dateien (Explorer) …", "eventlog": "Ereignisanzeige …",
                 "command": "Befehl ausführen …"}
BUILTIN_HOTKEY_DEFAULTS = {"ping": "Ctrl+Alt+P", "rdpweb": "Ctrl+Alt+R", "vncweb": "Ctrl+Alt+V", "wake": "Ctrl+Alt+W",
                           "power": "Ctrl+Alt+B", "gpupdate": "Ctrl+Alt+G", "files": "Ctrl+Alt+D", "eventlog": "Ctrl+Alt+E",
                           "command": "Ctrl+Alt+C"}
HOTKEY_RE = re.compile(r"^Ctrl\+Alt\+(Shift\+)?([A-Z]|[0-9]|F[1-9]|F1[0-2])$")


def norm_hotkey(v):
    """Normalize the notation; "" = no shortcut; ValueError for anything else."""
    t = re.sub(r"\s+", "", str(v or ""))
    if not t:
        return ""
    parts = [x for x in t.split("+") if x]
    alias = {"strg": "ctrl", "control": "ctrl", "umschalt": "shift", "umsch": "shift"}
    mods = {alias.get(x.lower(), x.lower()) for x in parts[:-1]}
    key = parts[-1].upper() if parts else ""
    if not mods <= {"ctrl", "alt", "shift"} or len(mods) != len(parts) - 1 or not {"ctrl", "alt"} <= mods:
        raise ValueError("Tastenkürzel: immer Strg+Alt, wahlweise mit Umschalt, dazu ein Buchstabe, eine Ziffer oder F1–F12")
    out = "Ctrl+Alt+" + ("Shift+" if "shift" in mods else "") + key
    if not HOTKEY_RE.match(out):
        raise ValueError("Tastenkürzel: immer Strg+Alt, wahlweise mit Umschalt, dazu ein Buchstabe, eine Ziffer oder F1–F12")
    return out


def builtin_hotkeys():
    """{"ping": "Ctrl+Alt+P", …} – defaults, overridden by the setting (empty = deliberately without a shortcut)."""
    out = dict(BUILTIN_HOTKEY_DEFAULTS)
    try:
        saved = json.loads(db.setting_raw("builtin_hotkeys") or "{}")
    except (ValueError, TypeError):
        saved = {}
    # 2.34: no new default (e.g. Ctrl+Alt+C for "Befehl ausführen") if a custom tool already has the shortcut
    taken = {r["hotkey"] for r in db.q("SELECT hotkey FROM tools WHERE hotkey IS NOT NULL AND hotkey != ''")}
    for k in list(out):
        if out[k] in taken and not (isinstance(saved, dict) and k in saved):
            out[k] = ""
    for k, v in (saved.items() if isinstance(saved, dict) else ()):
        if k in BUILTIN_KEYS:
            try:
                out[k] = norm_hotkey(v)
            except ValueError:
                pass
    return {k: v for k, v in out.items() if v}


def hotkey_owner(combo, ref=None):
    """Name of whoever already has combo (except ref itself, e.g. "tool:5" or "builtin:ping") – otherwise None."""
    if not combo:
        return None
    for k, v in builtin_hotkeys().items():
        if v == combo and ref != f"builtin:{k}":
            return BUILTIN_NAMES.get(k, k)
    for r in db.q("SELECT id, name FROM tools WHERE hotkey=?", (combo,)):
        if ref != f"tool:{r['id']}":
            return r["name"]
    return None


def check_hotkey(combo, ref=None):
    owner = hotkey_owner(combo, ref)
    if owner:
        raise ValueError(f"Tastenkürzel {combo.replace('Ctrl', 'Strg').replace('Shift', 'Umschalt')} ist bereits vergeben an „{owner}“.")


def save_builtin_hotkeys(data):
    if not isinstance(data, dict):
        raise ValueError("Ungültige Tastenkürzel")
    cur = builtin_hotkeys()
    new = {}
    for k in BUILTIN_KEYS:
        new[k] = norm_hotkey(data[k]) if k in data else cur.get(k, "")
    seen = {}
    for k, v in new.items():
        if not v:
            continue
        if v in seen:
            raise ValueError(f"Tastenkürzel {v.replace('Ctrl', 'Strg').replace('Shift', 'Umschalt')} ist doppelt vergeben "
                             f"(„{BUILTIN_NAMES[seen[v]]}“ und „{BUILTIN_NAMES[k]}“).")
        seen[v] = k
        t = db.q1("SELECT name FROM tools WHERE hotkey=?", (v,))
        if t:
            raise ValueError(f"Tastenkürzel {v.replace('Ctrl', 'Strg').replace('Shift', 'Umschalt')} ist bereits vergeben an „{t['name']}“.")
    db.set_setting("builtin_hotkeys", json.dumps(new))
    return builtin_hotkeys()


def _migrate_power_b():
    """2.32.5: the default for restart/shutdown is Ctrl+Alt+B. Anyone who saved the overview in 2.32.4 still has the
    old default N there – switch to B once, if B is free."""
    if db.setting_raw("hotkeys_power_b"):
        return
    try:
        saved = json.loads(db.setting_raw("builtin_hotkeys") or "{}")
    except (ValueError, TypeError):
        saved = {}
    if not isinstance(saved, dict):
        saved = {}
    if saved.get("power") == "Ctrl+Alt+N" and not hotkey_owner("Ctrl+Alt+B", "builtin:power"):
        saved["power"] = "Ctrl+Alt+B"
        db.set_setting("builtin_hotkeys", json.dumps(saved))
    elif "power" not in saved and db.q1("SELECT id FROM tools WHERE hotkey='Ctrl+Alt+B'"):
        # a custom tool already has B (it was free in 2.32.4): restart/shutdown keeps N or stays without a shortcut
        saved["power"] = "" if hotkey_owner("Ctrl+Alt+N", "builtin:power") else "Ctrl+Alt+N"
        db.set_setting("builtin_hotkeys", json.dumps(saved))
    db.set_setting("hotkeys_power_b", "1")


def _migrate_user_hotkeys():
    """2.32.3 stored Ctrl+Alt+digit per user – adopt it once into the tool, where free."""
    if db.setting_raw("hotkeys_migrated"):
        return
    try:
        rows = db.q("SELECT username, value FROM user_prefs WHERE key='tool_hotkeys' ORDER BY at")
    except Exception:
        rows = []
    for r in rows:
        try:
            d = json.loads(r["value"] or "{}")
        except ValueError:
            continue
        for slot, ref in (d.items() if isinstance(d, dict) else ()):
            if slot not in "1234567890" or len(slot) != 1 or not isinstance(ref, str):
                continue
            combo = f"Ctrl+Alt+{slot}"
            if hotkey_owner(combo):
                continue
            if ref.startswith("tool:") and ref[5:].isdigit():  # built-in tools now have their own defaults
                db.ex("UPDATE tools SET hotkey=? WHERE id=? AND COALESCE(hotkey, '')=''", (combo, int(ref[5:])))
    db.set_setting("hotkeys_migrated", "1")


# 2.46.1: gpupdate asks "log off / restart? (J/N)" when a policy needs it - a tool must never wait for an answer
# and never log off or restart. Plain gpupdate lines of cmd tools get "echo N|" in front (also the 2.32 example tool),
# in PowerShell tools "'N' |".
_GP_LINE = re.compile(r"^(\s*)(gpupdate(?:\.exe)?(?:\s+/(?!logoff\b|boot\b)[a-z]+(?::[a-z0-9]+)?)*)\s*$", re.I)


def gpupdate_no(script, typ="cmd"):
    """Script with every plain gpupdate line answered with "N" (no log-off, no restart)."""
    pre = "'N' | " if typ == "powershell" else "echo N| "
    out = []
    for line in (script or "").split("\n"):
        cr = line.endswith("\r")
        m = _GP_LINE.match(line[:-1] if cr else line)
        if m:
            line = m.group(1) + pre + m.group(2) + ("\r" if cr else "")
        out.append(line)
    return "\n".join(out)


def _migrate_gpupdate():
    for r in db.q("SELECT id, type, script FROM tools WHERE type IN ('cmd', 'powershell') AND script LIKE '%gpupdate%'"):
        new = gpupdate_no(r["script"], r["type"])
        if new != r["script"]:
            db.ex("UPDATE tools SET script=? WHERE id=?", (new, r["id"]))


def init():
    db.conn().executescript(SCHEMA)
    have = {r["name"] for r in db.q("PRAGMA table_info(tools)")}
    # 2.32.4 keyboard shortcuts; 2.32.7 files from the repository, scan afterwards, time limit, success codes
    for col, typ in (("hotkey", "TEXT"), ("repo_path", "TEXT"), ("scan_after", "TEXT DEFAULT 'never'"),
                     ("scan_profile_id", "INTEGER"), ("timeout_s", "INTEGER"), ("success_codes", "TEXT")):
        if col not in have:
            db.ex(f"ALTER TABLE tools ADD COLUMN {col} {typ}")
    if not db.val("SELECT value FROM settings WHERE key='tools_seeded'"):
        if not db.val("SELECT COUNT(*) FROM tools", default=0):
            for i, (folder, name, typ, script, run_as, confirm, desc) in enumerate(SEED):
                db.ex("INSERT INTO tools(name, folder, type, script, run_as, confirm, description, sort, created_at) "
                      "VALUES(?,?,?,?,?,?,?,?,?)", (name, folder, typ, script, run_as, confirm, desc, i, util.now()))
        db.ex("INSERT OR REPLACE INTO settings(key, value) VALUES('tools_seeded', '1')")
    try:
        _migrate_user_hotkeys()
        _migrate_power_b()
    except Exception as e:  # pragma: no cover
        pklog.warn("tool", f"Tastenkürzel aus 2.32.3 nicht übernommen: {e}")
    try:
        _migrate_gpupdate()
    except Exception as e:  # pragma: no cover
        pklog.warn("tool", f"gpupdate-Tools nicht angepasst: {e}")


def validate(d):
    if not (d.get("name") or "").strip():
        raise ValueError("Name fehlt.")
    if d.get("type") not in TYPES:
        raise ValueError("Ungültiger Typ.")
    if d.get("run_as") not in RUN_AS:
        raise ValueError("Ungültige Ausführung.")
    if not (d.get("script") or "").strip():
        raise ValueError("Befehl bzw. URL fehlt.")
    if d["type"] == "url" and not re.match(r"^[a-z][a-z0-9+.\-]*:", d["script"].strip(), re.I):
        raise ValueError("Die URL muss mit einem Schema beginnen, z. B. http://, https:// oder vnc://")
    if d["type"] == "url" and re.match(r"^\s*(javascript|data|vbscript|blob)\s*:", d["script"], re.I):
        raise ValueError("Dieses URL-Schema ist nicht erlaubt.")
    if d["type"] in ("cmd", "powershell"):
        d["script"] = gpupdate_no(d["script"], d["type"])  # 2.46.1: never wait for "log off / restart?" - always "N"
    clean_extras(d)


SCAN_AFTER = {"never": "Nicht scannen", "success": "Nach Erfolg", "always": "Immer"}
DEFAULT_TIMEOUT = 1800  # seconds, if nothing is entered in the tool – as before (e.g. SFC as SYSTEM takes a long time)
MAX_TIMEOUT = 4 * 3600


def clean_extras(d):
    """2.32.7: files from the repository, scan afterwards, time limit, success codes – validate and normalize."""
    from . import remote
    rp = str(d.get("repo_path") or "").strip().replace("/", "\\").strip("\\")
    if d.get("type") == "url":
        rp = ""
    if rp:
        parts = [x for x in rp.split("\\") if x not in ("", ".")]
        if ".." in parts or ":" in rp or rp.startswith("\\"):
            raise ValueError("Dateien aus dem Repository: bitte einen Pfad im Repository angeben (z. B. Tools\\iperf).")
        rp = "\\".join(parts)
        # never tenant folders: OU admins may place files there – a tool runs for everyone with the service account
        bad = tenant_conflict(rp)
        if bad:
            raise ValueError(f"Dateien aus dem Repository: „{rp}“ gehört zum Mandanten „{bad}“ (bzw. enthält seinen "
                             "Ordner) – Tools dürfen keine Dateien aus Mandanten-Ordnern verwenden.")
        try:
            kind = remote.repo_stat(rp)
        except remote.RemoteError as e:
            raise ValueError(f"Dateien aus dem Repository: {e}")
        if kind is None:
            raise ValueError(f"Dateien aus dem Repository: „{rp}“ gibt es nicht.")
    d["repo_path"] = rp or None
    sa = d.get("scan_after") or "never"
    if sa not in SCAN_AFTER:
        raise ValueError("Danach scannen: ungültige Auswahl.")
    d["scan_after"] = sa
    pid = d.get("scan_profile_id")
    if sa != "never" and pid not in (None, "", 0, "0"):
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            raise ValueError("Danach scannen: ungültiges Scanprofil.")
        if not db.q1("SELECT id FROM scan_profiles WHERE id=?", (pid,)):
            raise ValueError("Danach scannen: Scanprofil nicht gefunden.")
        d["scan_profile_id"] = pid
    else:
        d["scan_profile_id"] = None
    to = d.get("timeout_s")
    if to in (None, "", 0, "0"):
        d["timeout_s"] = None
    else:
        try:
            to = int(to)
        except (TypeError, ValueError):
            raise ValueError("Zeitlimit: bitte Sekunden als Zahl angeben.")
        if not 10 <= to <= MAX_TIMEOUT:
            raise ValueError(f"Zeitlimit: 10 bis {MAX_TIMEOUT} Sekunden.")
        d["timeout_s"] = to
    d["success_codes"] = ",".join(str(c) for c in parse_codes(d.get("success_codes"))) if str(d.get("success_codes") or "").strip() else None


def _rkey(p):
    return "\\".join(x for x in str(p or "").replace("/", "\\").lower().split("\\") if x not in ("", "."))


def tenant_conflict(rp):
    """Name of the tenant whose repository folder contains rp or lies inside rp – otherwise None. 2.32.7: also checked before every
    run (a tenant may have received the folder after the tool was saved)."""
    k = _rkey(rp)
    if not k:
        return None
    for t in db.q("SELECT name, repo_folder FROM tenants WHERE COALESCE(repo_folder, '') != ''"):
        o = _rkey(t["repo_folder"])
        if o and (k == o or k.startswith(o + "\\") or o.startswith(k + "\\")):
            return t["name"]
    return None


def parse_codes(v):
    """"0, 3010" → [0, 3010]; empty → [0]."""
    out = []
    for x in str(v or "").replace(";", ",").split(","):
        x = x.strip()
        if not x:
            continue
        if not re.match(r"^-?\d{1,10}$", x):
            raise ValueError(f"Erfolgscodes: „{x}“ ist keine Zahl (mehrere mit Komma trennen, z. B. 0, 3010).")
        out.append(int(x))
    return out or [0]


def variables(c):
    first = lambda v: (v or "").split(",")[0].strip()  # noqa: E731
    extra = json.loads(c.get("extra") or "{}") if c.get("extra") else {}
    return {"Computer": c.get("name") or "", "Host": c.get("dns_name") or c.get("name") or "",
            "IP": first(c.get("ip_addresses")), "MAC": first(c.get("mac_addresses")),
            "User": c.get("current_user") or "", "Domain": extra.get("domain") or config.DOMAIN}


SAFE_VALUE = re.compile(r"^[\w.,\- @:\\]*$")  # letters (including umlauts), digits, . , - _ space @ : \


def substitute(text, c, mode=None):
    """Substitute $(Computer) …. mode="url": values URL-encoded (user names with spaces, & or #)."""
    import urllib.parse
    vals = variables(c)

    def enc(name, v):
        if mode == "url":
            return urllib.parse.quote(v, safe=".:-_\\")
        # Audit 2.29: a value that starts with "-" or contains " -" is also considered unsafe (unquoted it would become a
        # further parameter, e.g. "max -Force")
        if mode in ("powershell", "cmd") and (not SAFE_VALUE.match(v) or v.startswith("-") or " -" in v):
            # Values from the client (e.g. user name) could contain code: "x&calc", "$(…)", "O'Brien"
            if mode == "powershell":
                return f"$($PK_{name})"  # safely quoted variable (ps_variables) instead of the text
            raise ValueError(f"$({name}) enthält Zeichen, die in CMD nicht sicher eingesetzt werden können ({v!r}). "
                             f"Bitte ein PowerShell-Tool mit $PK_{name} verwenden.")
        return v
    out = re.sub(r"\$\((Computer|Host|IP|MAC|User|Domain)\)", lambda m: enc(m.group(1), vals.get(m.group(1), "")), text or "")
    if mode == "url" and re.match(r"^\s*(javascript|data|vbscript|blob)\s*:", out, re.I):
        raise ValueError("Dieses URL-Schema ist nicht erlaubt.")
    return out


def ps_variables(c):
    """Values as safely quoted PowerShell variables ($PK_User …) – independent of apostrophes or $ in the name."""
    return "".join(f"$PK_{k} = {util.psq(v)}\n" for k, v in variables(c).items())


def ps_tool_winrm(script, workdir=None):
    """PowerShell tool directly via WinRM. 2.38: the script runs as a file (& file.ps1) – so "exit N" ends only the
    script and arrives as the exit code, as does the exit code of a last failed program ($LASTEXITCODE).
    If a group policy enforces AllSigned/Restricted or software restriction/AppLocker blocks scripts in %TEMP%
    (2.38.1: a probe script shows this beforehand), it runs directly as before (then without "exit N").
    Without .NET calls (Constrained Language Mode). Errors only count if they are actually output – errors suppressed with
    -ErrorAction SilentlyContinue do land in $Error, but are intended."""
    fmt = ("*>&1 | ForEach-Object { if ($_ -is [System.Management.Automation.ErrorRecord]) { $global:pkErr = 1 }; $_ }"
           " | Out-String -Width 220 -Stream")
    return ("$ErrorActionPreference = 'Continue'\n$global:pkErr = 0\n"
            + (f"Set-Location -LiteralPath {util.psq(workdir)}\n" if workdir else "")
            + "$pkF = Join-Path $env:TEMP ('pk_tool_' + [guid]::NewGuid().ToString('N') + '.ps1')\n"
            + util.ps_write("$pkF", script)
            + "try { Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force -ErrorAction Stop } catch {}\n"
            # 2.38.1: may a script run from %TEMP% here at all? (software restriction/AppLocker by path)
            + "$pkP = Join-Path $env:TEMP ('pk_probe_' + [guid]::NewGuid().ToString('N') + '.ps1')\n"
            + util.ps_write("$pkP", "'PKPROBE'")
            + "$pkFileOk = $false\n"
            + "try { $pkFileOk = @(& $pkP 2>$null) -contains 'PKPROBE' } catch {}\n"
            + "Remove-Item -LiteralPath $pkP -Force -ErrorAction SilentlyContinue\n"
            + "$global:LASTEXITCODE = 0\n"
            + "if (-not $pkFileOk -or @('Restricted', 'AllSigned') -contains [string](Get-ExecutionPolicy)) {\n"
            + "  & {\n" + script + "\n} " + fmt + "\n"
            + "} else {\n  & $pkF " + fmt + "\n}\n"
            + "$pkCode = $LASTEXITCODE\n"
            + "Remove-Item -LiteralPath $pkF -Force -ErrorAction SilentlyContinue\n"
            + "'PKEXIT:' + $(if ($pkCode) { $pkCode } elseif ($global:pkErr) { 1 } else { 0 })")


def _cmd_ps(script, workdir=None):
    """Run CMD commands as a temporary .cmd file (multi-line possible).
    2.32.7: read the output via a file in the console code page (OEM) – otherwise umlauts arrive as "?";
    workdir: working folder (files from the repository)."""
    b = script.replace("\r\n", "\n").replace("\n", "\r\n")
    cd = f'cd /d "{workdir}"\r\n' if workdir else ""
    wr = util.ps_write("$f", "@echo off\r\n" + cd + b, cmd=True).rstrip()  # 2.38: without .NET (Constrained Language Mode)
    return f"""$f = Join-Path $env:TEMP ('pk_tool_' + [guid]::NewGuid().ToString('N') + '.cmd')
$of = $f + '.out'
{wr}
& cmd.exe /d /c ('"' + $f + '" > "' + $of + '" 2>&1')
$code = $LASTEXITCODE
if (Test-Path -LiteralPath $of) {{
  $pkEnc = 'UTF8'
  if (([string](Get-Content -LiteralPath $of -Raw -Encoding UTF8)) -match '\\uFFFD') {{ $pkEnc = 'Oem' }}
  Get-Content -LiteralPath $of -Encoding $pkEnc | ForEach-Object {{ "$_" }}
}}
Remove-Item $f, $of -Force -ErrorAction SilentlyContinue
'PKEXIT:' + $code
"""


def run(tool, cid):
    c = db.q1("SELECT * FROM computers WHERE id=?", (cid,))
    if not c:
        raise ValueError("Computer nicht gefunden")
    from . import presence
    host = presence.direct_host(c)
    t0 = time.time()
    name = f"tool_{tool['id']}_{secrets.token_hex(3)}"
    rp = tool.get("repo_path")
    note = ""
    if rp:
        bad = tenant_conflict(rp)
        if bad:
            raise ValueError(f"Dateien aus dem Repository: „{rp}“ gehört inzwischen zum Mandanten „{bad}“ – das Tool läuft "
                             "nicht (Dateien aus Mandanten-Ordnern könnten dessen Admins ändern). Bitte im Tool einen anderen "
                             "Ordner wählen.")
    try:
        if rp:
            # 2.32.7: POCKET KNIFE copies files itself via the admin share – no password on the client,
            # and no double-hop problem (a WinRM session cannot reach further shares)
            kind = remote.repo_stat(rp)
            if kind is None:
                raise ValueError(f"Dateien aus dem Repository: „{rp}“ gibt es nicht (mehr).")
            if kind == "file":
                parent, _, fn = rp.rpartition("\\")
                remote.copy_package(parent, host, name, single_file=fn)
            else:
                remote.copy_package(rp, host, name)
            note = f"Dateien aus dem Repository: {rp}"
        res = _run(tool, c, host, name, bool(rp))
    finally:
        if rp:
            remote.remove_package_copy(host, name)  # always – also after an aborted copy (doing it twice does no harm)
    codes = parse_codes(tool.get("success_codes"))
    res["ok"] = res["exit"] in codes
    if note:
        res["output"] = f"{note}\n\n{res['output']}".strip()
    res["seconds"] = round(time.time() - t0, 1)
    return res


def _run(tool, c, host, name, with_files):
    script = substitute(tool["script"], c, mode="powershell" if tool["type"] == "powershell" else "cmd")
    if tool["type"] == "powershell":
        script = ps_variables(c) + script
    t0 = time.time()
    timeout = int(tool.get("timeout_s") or DEFAULT_TIMEOUT)
    workdir = f"C:\\Windows\\Temp\\PocketKnife\\{name}" if with_files else None
    # 2.32.7: with files also as the service account via the wrapper (own process) – only then is the program ended
    # at the time limit and can the folder be deleted afterwards (WinRM only cancels the waiting)
    if tool["run_as"] == "system" or with_files:
        # As SYSTEM via a temporary scheduled task (as with deployments)
        sc = script.replace("\r\n", "\n")
        # 2.38: without .NET (Constrained Language Mode) – util.ps_write
        if tool["type"] == "powershell":
            body = ("$sf = Join-Path $PKDir 'pk_tool.ps1'\n" + util.ps_write("$sf", sc) + "PK-RunPs1 $sf ''")
        else:
            body = ("$cf = Join-Path $PKDir 'pk_tool.cmd'\n" + util.ps_write("$cf", "@echo off\r\n" + sc.replace("\n", "\r\n"), cmd=True)
                    + "PK-Run 'cmd.exe' ('/c \"' + $cf + '\"')")
        try:
            code, out = remote.run_as_system(host, body, name, timeout_s=timeout,
                                             run_as="system" if tool["run_as"] == "system" else "deploy")
        except remote.RemoteTimeout:
            raise remote.RemoteTimeout(f"Zeitlimit von {remote.dur_text(timeout)} überschritten – abgebrochen.")
        hard = ""
        n_err, out = remote.ps_errors(out)
        if n_err and code in parse_codes(tool.get("success_codes")):
            code = 1  # 2.39.3: like a tool directly over WinRM - reported errors count
        if code not in parse_codes(tool.get("success_codes")):
            code, hard = remote.apply_hard(code, out)  # 2.32.5: hardened Windows in plain language
        out = "\n".join(l for l in (out or "").splitlines() if not l.startswith("> ") and not l.startswith("Exitcode: "))
        if hard:
            out = f"POCKET KNIFE: {hard}\n\n{out}"
    else:
        # Directly as the service account via WinRM
        if tool["type"] == "cmd":
            ps = _cmd_ps(script, workdir)
        else:
            # Only count errors if they are actually output – errors suppressed with -ErrorAction SilentlyContinue
            # do land in $Error, but are intended
            ps = ps_tool_winrm(script, workdir)
        try:
            out, err, _ = remote.ps(host, ps, timeout=timeout)
        except remote.RemoteTimeout:
            raise remote.RemoteTimeout(f"Zeitlimit von {remote.dur_text(timeout)} überschritten – POCKET KNIFE wartet nicht "
                                       "länger; der Befehl kann auf dem Computer noch weiterlaufen.")
        code = 0
        m = re.search(r"PKEXIT:(-?\d+)\s*$", out.strip())
        if m:
            code = int(m.group(1))
            out = out.strip()[:m.start()].rstrip()
        if err.strip():
            out = (out + "\n\nFehler:\n" + err.strip()).strip()
            if code == 0:
                code = 1
    return {"exit": code, "output": (out or "")[-MAX_OUT:], "seconds": round(time.time() - t0, 1)}


# --- Built-in tools --------------------------------------------------------------
def ping(c):
    host = c["dns_name"] or c["name"]
    lines = []
    try:
        ip = socket.gethostbyname(host)
        lines.append(f"{host} → {ip}")
    except OSError:
        return {"exit": 1, "output": f"{host} ist nicht per DNS auflösbar.", "seconds": 0}
    t0 = time.time()
    try:
        p = subprocess.run(["ping", "-c", "4", "-W", "1", ip], capture_output=True, text=True, timeout=15)
        lines.append(p.stdout.strip() or p.stderr.strip())
        code = p.returncode
    except FileNotFoundError:
        lines.append("ICMP-Ping im Container nicht verfügbar.")
        code = 0
    except subprocess.TimeoutExpired:
        lines.append("Zeitüberschreitung beim Ping.")
        code = 1
    lines.append("")
    for port, name in ((5985, "WinRM HTTP"), (5986, "WinRM HTTPS"), (445, "SMB"), (3389, "RDP"), (135, "RPC")):
        lines.append(f"TCP {port:<5} {name:<12} {'offen' if util.tcp_open(ip, port, 1.0) else 'geschlossen'}")
    return {"exit": code, "output": "\n".join(lines), "seconds": round(time.time() - t0, 1)}


# --- Wake-on-LAN ------------------------------------------------------------------------
# The magic packet is sent by a computer that is currently reachable in the same subnet
# (broadcasts do not leave their subnet). In addition, the container sends it itself.
WOL_RELAYS = 3


def _ipv4_nets(c):
    """IPv4 networks of a computer from the network adapter inventory (ip/subnet), otherwise /24 from ip_addresses."""
    nets = []
    raw = db.val("SELECT json FROM inventory_data WHERE computer_id=? AND category='nics'", (c["id"],))
    try:
        for n in json.loads(raw or "[]") if raw else []:
            ips = [x.strip() for x in (n.get("ip") or "").split(",")]
            masks = [x.strip() for x in (n.get("subnet") or "").split(",")]
            for i, ip in enumerate(ips):
                if re.match(r"^\d+\.\d+\.\d+\.\d+$", ip):
                    mask = masks[i] if i < len(masks) and "." in masks[i] else "255.255.255.0"
                    try:
                        nets.append(ipaddress.IPv4Interface(f"{ip}/{mask}").network)
                    except ValueError:
                        pass
    except (ValueError, AttributeError):
        pass
    if not nets:
        for ip in (c.get("ip_addresses") or "").split(","):
            ip = ip.strip()
            if re.match(r"^\d+\.\d+\.\d+\.\d+$", ip) and not ip.startswith("169.254."):
                nets.append(ipaddress.IPv4Network(f"{ip}/24", strict=False))
    return [n for n in dict.fromkeys(nets) if not n.is_loopback and not n.is_link_local]


def _ipv4s(c):
    return [ipaddress.IPv4Address(x.strip()) for x in (c.get("ip_addresses") or "").split(",")
            if re.match(r"^\d+\.\d+\.\d+\.\d+$", x.strip())]


def _fixed_relays():
    """2.31: fixed relays from the settings (computer names) – e.g. a server that is always on."""
    names = [x.strip().lower() for x in (db.setting("wol_relays") or "").split(",") if x.strip()]
    return names


def wol_relays(c, limit=WOL_RELAYS, exclude=()):
    """Online computers with WinRM, preferably in the same subnet as the target. → [(computer, same_subnet)]
    2.31: fixed relays (Settings → Wake-on-LAN) come first, if online; exclude: relays already used
    (second attempt via other computers)."""
    nets = _ipv4_nets(c)
    fixed = _fixed_relays()
    same, other, pinned = [], [], []
    for r in db.q("SELECT * FROM computers WHERE online=1 AND winrm=1 AND id != ? ORDER BY last_seen DESC", (c["id"],)):
        if r["name"] in exclude:
            continue
        sub = any(ip in n for ip in _ipv4s(r) for n in nets)
        if r["name"].lower() in fixed or (r.get("dns_name") or "").lower() in fixed:
            pinned.append((r, sub or not nets))
        else:
            (same if sub else other).append(r)
    out = pinned[:len(fixed)]
    if same:
        return out + [(r, True) for r in same[:limit]]
    if exclude and not any(s for _r, s in out):
        # 2.32.5: second attempt – if there is no other computer in the target network, use the previous ones again
        # (a relay from a foreign network or just the container would usually not reach the target)
        again = wol_relays(c, limit, ())
        if any(s for _r, s in again):
            return again
    return out + ([(r, False) for r in other[:1]] if not out else [])


def _packets(c):
    """Magic packets for all known MAC addresses – since 2.31 also those of wired network adapters from the inventory
    that had no IP address at the last scan (e.g. docking station or USB network adapter)."""
    macs = [m.strip() for m in (c.get("mac_addresses") or "").split(",") if m.strip()]
    raw = db.val("SELECT json FROM inventory_data WHERE computer_id=? AND category='nics'", (c.get("id"),)) if c.get("id") else None
    try:
        for n in json.loads(raw or "[]") if raw else []:
            if n.get("mac") and (n.get("wired") is not False):
                macs.append(str(n["mac"]).strip())
    except (ValueError, AttributeError, TypeError):
        pass
    out, seen = [], set()
    for mac in macs:
        b = bytes.fromhex(re.sub(r"[^0-9A-Fa-f]", "", mac) or "00")
        if len(b) == 6 and b not in seen and b != b"\x00" * 6:
            seen.add(b)
            out.append((mac, b"\xff" * 6 + b * 16))
    return out


WOL_PS = r"""
$ErrorActionPreference = 'Stop'
# 2.32.5: there is no UdpClient in restricted language mode – say so instead of silently sending nothing
if ($ExecutionContext.SessionState.LanguageMode -ne 'FullLanguage') { 'PK_CLM'; return }
$u = New-Object System.Net.Sockets.UdpClient
$u.EnableBroadcast = $true
try {
  for ($i = 1; $i -le %(repeat)s; $i++) {
    foreach ($h in @(%(packets)s)) {
      $p = [byte[]]($h -split '(..)' | Where-Object { $_ } | ForEach-Object { [Convert]::ToByte($_, 16) })
      foreach ($t in @(%(targets)s)) {
        foreach ($port in 7, 9) {
          try { [void]$u.Send($p, $p.Length, $t, $port); "OK $t`:$port" } catch { "FEHLER $t`:$port " + $_.Exception.Message }
        }
      }
    }
    if ($i -lt %(repeat)s) { Start-Sleep -Milliseconds 700 }
  }
} finally { $u.Close() }
"""


def _readiness(c):
    """What the last scan knows about Wake-on-LAN readiness (2.31) – as hint lines."""
    try:
        nics = json.loads(db.val("SELECT json FROM inventory_data WHERE computer_id=? AND category='nics'", (c.get("id"),)) or "[]")
        fast = json.loads(db.val("SELECT extra FROM computers WHERE id=?", (c.get("id"),)) or "{}").get("fast_startup")
    except (ValueError, TypeError, AttributeError):
        return []
    known = [n for n in nics if isinstance(n, dict) and n.get("wol_magic")]
    if not known:
        return []
    parts = [f"{n.get('name')}: Magic Packet {n.get('wol_magic')}"
             + ("" if n.get("wake_armed") is None else (", darf wecken" if n.get("wake_armed") else ", darf NICHT wecken"))
             for n in known if n.get("wired") is not False]
    out = ["Laut letztem Scan: " + ("; ".join(parts) or "keine kabelgebundene Karte")]
    if fast:
        out.append("Achtung: Windows-Schnellstart ist an – viele Karten wecken dann aus „Herunterfahren“ nicht auf.")
    return out


def wake(c, exclude=(), round_no=1):
    pk = _packets(c)
    if not pk:
        return {"exit": 1, "output": "Keine MAC-Adresse bekannt (Computer noch nie mit Netzwerkkarten gescannt).",
                "seconds": 0}
    t0 = time.time()
    repeat = max(1, min(10, int(db.setting("wol_repeat") or 3)))
    nets = _ipv4_nets(c)
    targets = ["255.255.255.255"] + [str(n.broadcast_address) for n in nets if n.prefixlen < 31]
    lines = [f"Ziel: {c['name']} – MAC {', '.join(m for m, _ in pk)}", *_readiness(c),
             "Subnetz: " + (", ".join(str(n) for n in nets) or "unbekannt"),
             f"Jedes Paket {repeat}× (UDP kann verloren gehen)" + (f" – Versuch {round_no}" if round_no > 1 else ""), ""]
    ok_relay = False
    relays = wol_relays(c, exclude=exclude)
    if not relays:
        lines.append("Kein anderer Computer ist gerade online – Versand nur aus dem Container.")
    for r, same in relays:
        host = r["dns_name"] or r["name"]
        # A relay in a foreign network can only send directed broadcasts
        tg = targets if same else targets[1:] or targets
        script = WOL_PS % {"packets": ",".join(util.psq(p.hex()) for _, p in pk),
                           "targets": ",".join(util.psq(t) for t in tg), "repeat": repeat}
        try:
            out, err, _ = remote.ps(host, script)
            sent = [x for x in out.splitlines() if x.startswith("OK ")]
            ok_relay = ok_relay or bool(sent)
            pinned = r["name"].lower() in _fixed_relays()
            if "PK_CLM" in out:
                lines.append(f"Relay {r['name']}: kann nicht senden – PowerShell läuft dort im eingeschränkten Sprachmodus "
                             "(AppLocker/WDAC). Anderen Computer als festes Relay eintragen.")
                continue
            lines.append(f"Relay {r['name']}{' (fest)' if pinned else ''} ({'gleiches Subnetz' if same else 'anderes Subnetz, gerichteter Broadcast'}): "
                         f"{len(sent)} Pakete gesendet" + (f" – {err.strip()[:300]}" if err.strip() else ""))
            lines += ["   " + x for x in out.splitlines() if x.startswith("FEHLER")]
        except Exception as e:
            lines.append(f"Relay {r['name']}: fehlgeschlagen – {e}")
    # additionally from the container (helps if the container is in the same network, e.g. network_mode: host)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    n_local = 0
    try:
        for i in range(repeat):
            for _, pkt in pk:
                for t in targets:
                    for port in (7, 9):
                        try:
                            s.sendto(pkt, (t, port))
                            n_local += 1
                        except OSError:
                            pass
            if i < repeat - 1:
                time.sleep(0.3)
    finally:
        s.close()
    lines.append(f"Container: {n_local} Pakete an {', '.join(targets)} gesendet.")
    if not ok_relay:
        lines += ["", "Hinweis: Ohne Relay im Zielnetz erreicht das Paket den Computer nur, wenn der Router gerichtete "
                      "Broadcasts weiterleitet oder der Container im selben Netz läuft. Ein festes Relay (ein Computer, der "
                      "immer läuft) lässt sich unter Einstellungen → Wake-on-LAN eintragen."]
    return {"exit": 0 if (ok_relay or n_local) else 1, "seconds": round(time.time() - t0, 1),
            "output": "\n".join(lines), "relay": [r["name"] for r, _ in relays]}


# --- 2.31: Waiting for wake-up ---------------------------------------------------------------------
_wake = {}  # computer_id → {"state": "waiting"|"awake"|"timeout", "started", "seconds", "rounds", "detail"}
_wake_lock = threading.Lock()
WAKE_POLL = 10  # seconds between two checks


def wake_state(cid):
    with _wake_lock:
        st = _wake.get(int(cid))
        if not st:
            return None
        out = dict(st)
    if out["state"] == "waiting":
        out["seconds"] = int(time.time() - out["started"])
    out.pop("started", None)
    return out


def watch_wake(c, used_relays, user=None):
    """Check in the background whether the computer wakes up (heartbeat on this one computer). After half of the waiting time,
    send once more – via other relays, if there are any. Result in the log and via wake_state()."""
    wait = int(db.setting("wol_wait_seconds") or 0)
    if wait <= 0:
        return False
    cid = int(c["id"])
    with _wake_lock:
        cur = _wake.get(cid)
        if cur and cur["state"] == "waiting":
            return True  # already running – the new send counts too
        _wake[cid] = {"state": "waiting", "started": time.time(), "seconds": 0, "rounds": 1, "detail": ""}

    def run():
        from . import presence
        t0 = time.time()
        resent = False
        used = set(used_relays or [])
        try:
            while time.time() - t0 < wait:
                time.sleep(WAKE_POLL)
                row = db.q1(f"SELECT {presence.COLUMNS} FROM computers WHERE id=?", (cid,))
                if not row:
                    return
                online, winrm = presence.probe(row, force=True)
                if online:
                    if presence.update(cid, online, winrm):
                        presence.notify([cid], background=True)  # heartbeat triggers (scans, schedules) as usual
                    secs = int(time.time() - t0)
                    with _wake_lock:
                        _wake[cid].update(state="awake", seconds=secs,
                                          detail=f"aufgewacht nach {secs} s" + ("" if winrm else " (WinRM noch nicht bereit)"))
                    pklog.info("tool", f"Wake-on-LAN: {row['name']} ist nach {secs} s aufgewacht.", user=user, computer_id=cid)
                    return
                if not resent and time.time() - t0 >= wait / 2:
                    resent = True
                    try:
                        full = db.q1("SELECT * FROM computers WHERE id=?", (cid,))
                        res = wake(full, exclude=used, round_no=2)
                        used |= set(res.get("relay") or [])
                        with _wake_lock:
                            _wake[cid]["rounds"] = 2
                    except Exception as e:  # noqa: BLE001 – the second attempt must not end the wait
                        pklog.warn("tool", f"Wake-on-LAN: zweiter Versuch für {row['name']} fehlgeschlagen: {e}", computer_id=cid)
            with _wake_lock:
                _wake[cid].update(state="timeout", seconds=wait,
                                  detail=f"nach {wait} s nicht aufgewacht (2 Versuche)")
            pklog.warn("tool", f"Wake-on-LAN: {c['name']} ist nach {wait} s nicht aufgewacht (2 Versuche). Im Inventar "
                               "unter Netzwerkkarten steht, ob die Karte das Wecken erlaubt.", user=user, computer_id=cid)
        except Exception as e:  # noqa: BLE001
            with _wake_lock:
                _wake[cid].update(state="timeout", detail=f"Prüfung fehlgeschlagen: {e}")
    threading.Thread(target=run, name=f"wol-{cid}", daemon=True).start()
    return True


# --- Tool executions as server jobs (they keep running even if the browser is closed) ---
import threading as _th
from concurrent.futures import ThreadPoolExecutor as _TPE

_jobs = {}
_jlock = _th.Lock()
_jpool = _TPE(max_workers=8, thread_name_prefix="tool")


def scan_after(tool, cid, user, ok, res):
    """2.32.7: scan afterwards (default: no) – if a scan is running right now, it is made up for afterwards."""
    sa = tool.get("scan_after") or "never"
    if not (sa == "always" or (sa == "success" and ok)):
        return
    from . import inventory, profiles
    prof = (profiles.get(tool["scan_profile_id"]) if tool.get("scan_profile_id") else None) or profiles.default()
    try:
        inventory.queue_scan(cid, user, prof["id"], follow=True)
        res["output"] = (res.get("output") or "") + f"\n\nDanach: Scan mit „{prof['name']}“ gestartet."
    except Exception as e:  # noqa: BLE001
        res["output"] = (res.get("output") or "") + f"\n\nDanach scannen nicht möglich: {e}"


# --- 2.34: Run a command directly (without creating a tool) -------------------------------------------
ADHOC_MAX = 20_000  # characters


def adhoc_tool(data):
    """Free command as a one-off tool – same execution as a custom tool (WinRM or SYSTEM via a
    scheduled task), without files from the repository. Main admins only (like creating tools)."""
    typ = str(data.get("type") or "")
    if typ not in ("powershell", "cmd"):
        raise ValueError("Typ: PowerShell oder CMD.")
    run_as = str(data.get("run_as") or "service")
    if run_as not in RUN_AS:
        raise ValueError("Ungültige Ausführung.")
    script = str(data.get("script") or "").replace("\r\n", "\n").strip("\n")
    if not script.strip():
        raise ValueError("Bitte einen Befehl eingeben.")
    if len(script) > ADHOC_MAX:
        raise ValueError(f"Befehl zu lang (höchstens {ADHOC_MAX} Zeichen) – dafür besser ein Tool oder Paket anlegen.")
    d = {"name": "Befehl", "type": typ, "run_as": run_as, "script": script.replace("\n", "\r\n"),
         "timeout_s": data.get("timeout_s"), "success_codes": data.get("success_codes")}
    clean_extras(d)  # validate time limit and success codes as in the tool
    # Name without the command text: it appears per computer in the log (visible to OU admins too), and the command may
    # contain a password
    d.update(id="adhoc", repo_path="", scan_after="never", scan_profile_id=None,
             name=f"Befehl ({'PowerShell' if typ == 'powershell' else 'CMD'}{' als SYSTEM' if run_as == 'system' else ''})")
    return d


def start_job(tool, ids, user):
    from . import pklog
    ids = list(dict.fromkeys(ids))  # 2.34: do not run duplicate IDs twice
    jid = secrets.token_hex(6)
    names = {r["id"]: r["name"] for r in db.q("SELECT id, name FROM computers")}
    job = {"id": jid, "tool": tool["name"], "user": user, "started": util.now(),
           "targets": {cid: {"id": cid, "name": names.get(cid, "?"), "status": "wartet", "res": None} for cid in ids}}
    with _jlock:
        _jobs[jid] = job
        for old in sorted(_jobs, key=lambda k: _jobs[k]["started"])[:-60]:
            _jobs.pop(old, None)

    def one(cid):
        t = job["targets"][cid]
        t["status"] = "läuft"
        try:
            res = run(tool, cid)
            status = "OK" if res.get("ok", not res["exit"]) else "Fehler"
        except Exception as e:
            res = {"exit": 1, "output": str(e), "seconds": 0}
            status = "Fehler"
        pklog.info("tool", f"Tool '{tool['name']}' auf {t['name']}: {status} (Exitcode {res['exit']}).",
                   user=user, computer_id=cid)
        scan_after(tool, cid, user, status == "OK", res)
        t["res"], t["status"] = res, status  # only now finished (the UI polls the state)
    for cid in ids:
        _jpool.submit(one, cid)
    return jid


def job(jid):
    with _jlock:
        j = _jobs.get(jid)
        if not j:
            return None
        return {**{k: v for k, v in j.items() if k != "targets"}, "targets": list(j["targets"].values())}
