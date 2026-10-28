"""Tools: eigene Befehle in Ordnern, ausführbar auf ausgewählten Computern.

Variablen im Skript/URL (werden vor dem Ausführen ersetzt):
  $(Computer)  Computername         $(Host)  DNS-Name
  $(IP)        erste IP-Adresse     $(MAC)   erste MAC-Adresse
  $(User)      angemeldeter Benutzer $(Domain) Domäne
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

# Beispiel-Tools (frei änderbar oder löschbar)
SEED = [
    ("BITLOCKER", "TPM-Status", "powershell", "Get-Tpm | Format-List", "service", 0, "Zeigt den Zustand des TPM-Chips."),
    ("BITLOCKER", "BitLocker C: Status", "cmd", "manage-bde -status C:", "service", 0, ""),
    ("BITLOCKER", "BitLocker C: anhalten (1 Neustart)", "powershell",
     "Suspend-BitLocker -MountPoint 'C:' -RebootCount 1 | Format-List MountPoint, ProtectionStatus", "service", 1,
     "Hält den Schutz für einen Neustart an, z. B. vor einem BIOS-Update."),
    ("BITLOCKER", "BitLocker C: fortsetzen", "powershell", "Resume-BitLocker -MountPoint 'C:' | Format-List MountPoint, ProtectionStatus", "service", 1, ""),
    ("GPO", "GPUPDATE /force", "cmd", "gpupdate /target:computer /force", "service", 0, ""),
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
     "$h.QueryHistory(0, [Math]::Min(20, $n)) | Select-Object Date, Title, @{n='Ergebnis';e={switch($_.ResultCode){2{'OK'}3{'OK mit Fehlern'}4{'Fehler'}5{'Abgebrochen'}default{$_.ResultCode}}}} | Format-Table -AutoSize -Wrap",
     "service", 0, ""),
    ("WINDOWS UPDATE", "Windows-Update-Dienste zurücksetzen", "powershell",
     "Stop-Service wuauserv, bits -Force\r\nRemove-Item \"$env:SystemRoot\\SoftwareDistribution\\Download\\*\" -Recurse -Force -ErrorAction SilentlyContinue\r\n"
     "Start-Service bits, wuauserv\r\n'Update-Cache geleert, Dienste neu gestartet.'", "service", 1, ""),
    ("SYSTEM", "Angemeldete Benutzer", "cmd", "quser", "service", 0, ""),
    ("SYSTEM", "Anwendungsfehler (24 Std.)", "powershell",
     "Get-WinEvent -FilterHashtable @{LogName='Application'; Level=2; StartTime=(Get-Date).AddDays(-1)} -MaxEvents 50 -ErrorAction SilentlyContinue | "
     "Format-Table TimeCreated, ProviderName, Id, Message -AutoSize -Wrap", "service", 0, ""),
    ("SYSTEM", "CPU-Last (5 Sekunden)", "powershell",
     "(Get-Counter '\\Processor(_Total)\\% Processor Time' -SampleInterval 1 -MaxSamples 5).CounterSamples | Format-Table Timestamp, @{n='CPU %';e={[math]::Round($_.CookedValue,1)}}\r\n"
     "Get-Process | Sort-Object CPU -Descending | Select-Object -First 10 Name, Id, CPU, @{n='RAM MB';e={[math]::Round($_.WorkingSet64/1MB)}} | Format-Table -AutoSize",
     "service", 0, ""),
    ("SYSTEM", "Datenträger prüfen (nur lesen)", "cmd", "chkdsk C: /scan", "service", 0, "Online-Prüfung ohne Reparatur."),
    ("SYSTEM", "Systemdateien prüfen (SFC)", "cmd", "sfc /scannow", "system", 1, "Dauert mehrere Minuten."),
    ("SYSTEM", "Uptime", "powershell", "$b = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime\r\n\"Gestartet: $b\"\r\n\"Laufzeit:  $((Get-Date) - $b)\"", "service", 0, ""),
    ("SYSTEM", "Batteriezustand", "powershell",
     "Get-CimInstance -Namespace root\\wmi -ClassName BatteryFullChargedCapacity -ErrorAction SilentlyContinue | ForEach-Object { $f = $_.FullChargedCapacity; "
     "$d = (Get-CimInstance -Namespace root\\wmi -ClassName BatteryStaticData).DesignedCapacity; \"Kapazität: $f von $d mWh ($([math]::Round(100*$f/$d))%)\" }",
     "service", 0, ""),
    ("WEB", "Web-Oberfläche des Computers (Port 80)", "url", "http://$(Host)/", "service", 0, ""),
]


# 2.32.3: Tastenkürzel Strg+Alt+1 … 9 und 0 je Benutzer – Ziel ist ein eingebautes oder eigenes Tool
HOTKEY_SLOTS = tuple("1234567890")
BUILTIN_KEYS = ("ping", "rdpweb", "vncweb", "wake", "power", "abort", "gpupdate", "winget", "scan",
                "diskusage", "files", "eventlog")
_HOTKEY_RE = re.compile(r"^(tool:[1-9]\d{0,9}|builtin:[a-z]{2,20})$")


def _uname(user):
    return (user.get("username") or "").strip().lower()


def hotkeys(user):
    """{"1": "tool:5", "2": "builtin:ping", …} – nur gültige Einträge (gelöschte Tools fallen weg)."""
    raw = db.val("SELECT value FROM user_prefs WHERE username=? AND key='tool_hotkeys'", (_uname(user),))
    try:
        d = json.loads(raw) if raw else {}
    except ValueError:
        d = {}
    if not d or not isinstance(d, dict):
        return {}
    ids = {r["id"] for r in db.q("SELECT id FROM tools")}
    out = {}
    for k, v in (d.items() if isinstance(d, dict) else ()):
        if k in HOTKEY_SLOTS and isinstance(v, str) and _HOTKEY_RE.match(v):
            if (v.startswith("tool:") and int(v[5:]) in ids) or v[8:] in BUILTIN_KEYS:
                out[k] = v
    return out


def save_hotkeys(user, data):
    if not isinstance(data, dict):
        raise ValueError("Ungültige Tastenkürzel")
    out, seen = {}, set()
    for k, v in data.items():
        if v in (None, ""):
            continue
        if k not in HOTKEY_SLOTS or not isinstance(v, str) or not _HOTKEY_RE.match(v):
            raise ValueError("Ungültiges Tastenkürzel")
        if v.startswith("builtin:") and v[8:] not in BUILTIN_KEYS:
            raise ValueError("Unbekanntes eingebautes Tool")
        if v.startswith("tool:") and not db.q1("SELECT id FROM tools WHERE id=?", (int(v[5:]),)):
            raise ValueError("Tool nicht gefunden")
        if v in seen:
            raise ValueError("Ein Tool kann nur ein Tastenkürzel haben")
        seen.add(v)
        out[k] = v
    db.ex("INSERT INTO user_prefs(username, key, value, at) VALUES(?, 'tool_hotkeys', ?, ?) ON CONFLICT(username, key) "
          "DO UPDATE SET value=excluded.value, at=excluded.at", (_uname(user), json.dumps(out), util.now()))
    return hotkeys(user)


def init():
    db.conn().executescript(SCHEMA)
    if not db.val("SELECT value FROM settings WHERE key='tools_seeded'"):
        if not db.val("SELECT COUNT(*) FROM tools", default=0):
            for i, (folder, name, typ, script, run_as, confirm, desc) in enumerate(SEED):
                db.ex("INSERT INTO tools(name, folder, type, script, run_as, confirm, description, sort, created_at) "
                      "VALUES(?,?,?,?,?,?,?,?,?)", (name, folder, typ, script, run_as, confirm, desc, i, util.now()))
        db.ex("INSERT OR REPLACE INTO settings(key, value) VALUES('tools_seeded', '1')")


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


def variables(c):
    first = lambda v: (v or "").split(",")[0].strip()  # noqa: E731
    extra = json.loads(c.get("extra") or "{}") if c.get("extra") else {}
    return {"Computer": c.get("name") or "", "Host": c.get("dns_name") or c.get("name") or "",
            "IP": first(c.get("ip_addresses")), "MAC": first(c.get("mac_addresses")),
            "User": c.get("current_user") or "", "Domain": extra.get("domain") or config.DOMAIN}


SAFE_VALUE = re.compile(r"^[\w.,\- @:\\]*$")  # Buchstaben (auch Umlaute), Ziffern, . , - _ Leerzeichen @ : \


def substitute(text, c, mode=None):
    """$(Computer) … einsetzen. mode="url": Werte URL-kodiert (Benutzernamen mit Leerzeichen, & oder #)."""
    import urllib.parse
    vals = variables(c)

    def enc(name, v):
        if mode == "url":
            return urllib.parse.quote(v, safe=".:-_\\")
        # Audit 2.29: auch ein Wert, der mit „-“ beginnt oder „ -“ enthält, gilt als unsicher (würde unzitiert zu einem
        # weiteren Parameter, z. B. „max -Force“)
        if mode in ("powershell", "cmd") and (not SAFE_VALUE.match(v) or v.startswith("-") or " -" in v):
            # Werte vom Client (z. B. Benutzername) könnten Code enthalten: „x&calc“, „$(…)“, „O'Brien“
            if mode == "powershell":
                return f"$($PK_{name})"  # sicher zitierte Variable (ps_variables) statt des Textes
            raise ValueError(f"$({name}) enthält Zeichen, die in CMD nicht sicher eingesetzt werden können ({v!r}). "
                             f"Bitte ein PowerShell-Tool mit $PK_{name} verwenden.")
        return v
    out = re.sub(r"\$\((Computer|Host|IP|MAC|User|Domain)\)", lambda m: enc(m.group(1), vals.get(m.group(1), "")), text or "")
    if mode == "url" and re.match(r"^\s*(javascript|data|vbscript|blob)\s*:", out, re.I):
        raise ValueError("Dieses URL-Schema ist nicht erlaubt.")
    return out


def ps_variables(c):
    """Werte als sicher zitierte PowerShell-Variablen ($PK_User …) – unabhängig von Apostrophen oder $ im Namen."""
    return "".join(f"$PK_{k} = {util.psq(v)}\n" for k, v in variables(c).items())


def _cmd_ps(script):
    """CMD-Befehle als temporäre .cmd-Datei ausführen (mehrzeilig möglich)."""
    b = script.replace("\r\n", "\n").replace("\n", "\r\n")
    return f"""$f = Join-Path $env:TEMP ('pk_tool_' + [guid]::NewGuid().ToString('N') + '.cmd')
[System.IO.File]::WriteAllText($f, '@echo off' + [Environment]::NewLine + {util.psq(b)}, {util.PS_CMD_ENCODING})
$o = & cmd.exe /c $f 2>&1
$code = $LASTEXITCODE
Remove-Item $f -Force -ErrorAction SilentlyContinue
$o | ForEach-Object {{ "$_" }}
'PKEXIT:' + $code
"""


def run(tool, cid):
    c = db.q1("SELECT * FROM computers WHERE id=?", (cid,))
    if not c:
        raise ValueError("Computer nicht gefunden")
    host = c["dns_name"] or c["name"]
    script = substitute(tool["script"], c, mode="powershell" if tool["type"] == "powershell" else "cmd")
    if tool["type"] == "powershell":
        script = ps_variables(c) + script
    t0 = time.time()
    if tool["run_as"] == "system":
        # Als SYSTEM über eine temporäre geplante Aufgabe (wie bei Bereitstellungen)
        b64 = base64.b64encode(script.replace("\r\n", "\n").encode("utf-8")).decode()
        if tool["type"] == "powershell":
            body = (f"$sf = Join-Path $PKDir 'pk_tool.ps1'\n"
                    f"[System.IO.File]::WriteAllText($sf, [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{b64}')), "
                    f"(New-Object System.Text.UTF8Encoding $true))\n"
                    "PK-RunPs1 $sf ''")
        else:
            body = (f"$cf = Join-Path $PKDir 'pk_tool.cmd'\n"
                    f"$txt = '@echo off' + [Environment]::NewLine + [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{b64}')).Replace(\"`n\", \"`r`n\")\n"
                    f"[System.IO.File]::WriteAllText($cf, $txt, {util.PS_CMD_ENCODING})\n"
                    f"PK-Run 'cmd.exe' ('/c \"' + $cf + '\"')")
        code, out = remote.run_as_system(host, body, f"tool_{tool['id']}_{secrets.token_hex(3)}", timeout_s=1800)
        out = "\n".join(l for l in (out or "").splitlines() if not l.startswith("> ") and not l.startswith("Exitcode: "))
    else:
        # Direkt als Dienstkonto über WinRM
        if tool["type"] == "cmd":
            ps = _cmd_ps(script)
        else:
            # Fehler nur zählen, wenn sie tatsächlich ausgegeben werden – mit -ErrorAction SilentlyContinue
            # unterdrückte Fehler landen zwar in $Error, sind aber gewollt
            ps = ("$ErrorActionPreference = 'Continue'\n$global:pkErr = 0\n& {\n" + script +
                  "\n} *>&1 | ForEach-Object { if ($_ -is [System.Management.Automation.ErrorRecord]) { $global:pkErr = 1 }; $_ }"
                  " | Out-String -Width 220 -Stream\n'PKEXIT:' + $global:pkErr")
        out, err, _ = remote.ps(host, ps)
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


# --- Eingebaute Tools --------------------------------------------------------------
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
# Das Magic Packet wird von einem gerade erreichbaren Computer im selben Subnetz gesendet
# (Broadcasts verlassen ihr Subnetz nicht). Zusätzlich sendet der Container selbst.
WOL_RELAYS = 3


def _ipv4_nets(c):
    """IPv4-Netze eines Computers aus dem Netzwerkkarten-Inventar (ip/subnet), sonst /24 aus ip_addresses."""
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
    """2.31: feste Relays aus den Einstellungen (Computernamen) – z. B. ein Server, der immer läuft."""
    names = [x.strip().lower() for x in (db.setting("wol_relays") or "").split(",") if x.strip()]
    return names


def wol_relays(c, limit=WOL_RELAYS, exclude=()):
    """Online-Computer mit WinRM, bevorzugt im selben Subnetz wie das Ziel. → [(computer, same_subnet)]
    2.31: feste Relays (Einstellungen → Wake-on-LAN) kommen zuerst, sofern online; exclude: schon benutzte Relays
    (zweiter Versuch über andere Computer)."""
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
    return out + ([(r, False) for r in other[:1]] if not out else [])


def _packets(c):
    """Magic Packets für alle bekannten MAC-Adressen – 2.31 auch die kabelgebundener Netzwerkkarten aus dem Inventar,
    die beim letzten Scan keine IP-Adresse hatten (z. B. Dockingstation oder USB-Netzwerkadapter)."""
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
    """Was der letzte Scan über die Wake-on-LAN-Bereitschaft weiß (2.31) – als Hinweiszeilen."""
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
        # Ein Relay im fremden Netz kann nur gerichtete Broadcasts senden
        tg = targets if same else targets[1:] or targets
        script = WOL_PS % {"packets": ",".join(util.psq(p.hex()) for _, p in pk),
                           "targets": ",".join(util.psq(t) for t in tg), "repeat": repeat}
        try:
            out, err, _ = remote.ps(host, script)
            sent = [x for x in out.splitlines() if x.startswith("OK ")]
            ok_relay = ok_relay or bool(sent)
            pinned = r["name"].lower() in _fixed_relays()
            lines.append(f"Relay {r['name']}{' (fest)' if pinned else ''} ({'gleiches Subnetz' if same else 'anderes Subnetz, gerichteter Broadcast'}): "
                         f"{len(sent)} Pakete gesendet" + (f" – {err.strip()[:300]}" if err.strip() else ""))
            lines += ["   " + x for x in out.splitlines() if x.startswith("FEHLER")]
        except Exception as e:
            lines.append(f"Relay {r['name']}: fehlgeschlagen – {e}")
    # zusätzlich aus dem Container (hilft, wenn der Container im selben Netz hängt, z. B. network_mode: host)
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


# --- 2.31: Aufwachen abwarten ---------------------------------------------------------------------
_wake = {}  # computer_id → {"state": "waiting"|"awake"|"timeout", "started", "seconds", "rounds", "detail"}
_wake_lock = threading.Lock()
WAKE_POLL = 10  # Sekunden zwischen zwei Prüfungen


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
    """Im Hintergrund prüfen, ob der Computer aufwacht (Heartbeat auf diesen einen Computer). Nach der Hälfte der Wartezeit
    noch einmal senden – über andere Relays, falls es welche gibt. Ergebnis im Protokoll und über wake_state()."""
    wait = int(db.setting("wol_wait_seconds") or 0)
    if wait <= 0:
        return False
    cid = int(c["id"])
    with _wake_lock:
        cur = _wake.get(cid)
        if cur and cur["state"] == "waiting":
            return True  # läuft schon – der neue Versand zählt mit
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
                        presence.notify([cid], background=True)  # Heartbeat-Auslöser (Scans, Zeitpläne) wie sonst auch
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
                    except Exception as e:  # noqa: BLE001 – der zweite Versuch darf das Warten nicht beenden
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


# --- Tool-Ausführungen als Server-Jobs (laufen weiter, auch wenn der Browser geschlossen wird) ---
import threading as _th
from concurrent.futures import ThreadPoolExecutor as _TPE

_jobs = {}
_jlock = _th.Lock()
_jpool = _TPE(max_workers=8, thread_name_prefix="tool")


def start_job(tool, ids, user):
    from . import pklog
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
            t["res"] = run(tool, cid)
            t["status"] = "Fehler" if t["res"]["exit"] else "OK"
        except Exception as e:
            t["res"] = {"exit": 1, "output": str(e), "seconds": 0}
            t["status"] = "Fehler"
        pklog.info("tool", f"Tool '{tool['name']}' auf {t['name']}: {t['status']} (Exitcode {t['res']['exit']}).",
                   user=user, computer_id=cid)
    for cid in ids:
        _jpool.submit(one, cid)
    return jid


def job(jid):
    with _jlock:
        j = _jobs.get(jid)
        if not j:
            return None
        return {**{k: v for k, v in j.items() if k != "targets"}, "targets": list(j["targets"].values())}
