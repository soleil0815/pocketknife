"""Direkte Aktionen auf Clients: Dienste steuern, Prozesse beenden, Live-Abfragen, BitLocker vom Client."""
import json
import re

from . import db, remote, util

SERVICE_RE = re.compile(r"^[A-Za-z0-9_.\-$@ ]{1,256}$")
PROC_RE = re.compile(r"^[A-Za-z0-9_.\-+ ()]{1,260}$")
START_MODES = {"auto": "Automatic", "manual": "Manual", "disabled": "Disabled"}


def _host(cid):
    c = db.q1("SELECT id, name, dns_name FROM computers WHERE id=?", (cid,))
    if not c:
        raise ValueError("Computer nicht gefunden")
    return c, c["dns_name"] or c["name"]


def _json(out):
    i = out.find("PKJSON:")
    if i < 0:
        raise remote.RemoteError("Keine Antwort vom Computer erhalten.")
    text = out[i + 7:].strip()
    return json.loads(text.splitlines()[0]) if text else []


def _L(x):
    return x if isinstance(x, list) else ([] if x is None else [x])


# --- Dienste -----------------------------------------------------------------
SERVICES_PS = r"""
'PKJSON:' + (ConvertTo-Json -Depth 3 -Compress -InputObject @(Get-CimInstance Win32_Service | ForEach-Object {
  [ordered]@{ name = $_.Name; display = $_.DisplayName; state = $_.State; start = $_.StartMode; account = $_.StartName
              description = $_.Description; path = $_.PathName } }))
"""


def _save_services(cid, rows):
    with db.tx():
        db.ex("DELETE FROM services WHERE computer_id=?", (cid,))
        db.exmany("INSERT INTO services(computer_id, name, display_name, state, start_mode, account, description, path) "
                  "VALUES(?,?,?,?,?,?,?,?)",
                  [(cid, r.get("name"), r.get("display"), r.get("state"), r.get("start"), r.get("account"),
                    (r.get("description") or "")[:2000], (r.get("path") or "")[:2000]) for r in rows])


def live_services(cid):
    _c, host = _host(cid)
    out, err, _ = remote.ps(host, SERVICES_PS)
    rows = _L(_json(out))
    _save_services(cid, rows)
    return len(rows)


def service_action(cid, name, action, start_mode=None):
    if not SERVICE_RE.match(name or ""):
        raise ValueError("Ungültiger Dienstname")
    if action not in ("start", "stop", "restart", "startmode"):
        raise ValueError("Ungültige Aktion")
    _c, host = _host(cid)
    cmd = {"start": "Start-Service -InputObject $s -ErrorAction Stop",
           "stop": "Stop-Service -InputObject $s -Force -ErrorAction Stop",
           "restart": "Restart-Service -InputObject $s -Force -ErrorAction Stop",
           "startmode": ""}[action]
    if action == "startmode":
        mode = START_MODES.get(start_mode)
        if not mode:
            raise ValueError("Ungültiger Starttyp")
        cmd = f"Set-Service -Name $n -StartupType {mode} -ErrorAction Stop"
    script = f"""$ErrorActionPreference = 'Stop'
$n = {util.psq(name)}
$s = Get-Service -Name $n
{cmd}
Start-Sleep -Milliseconds 800
$w = Get-CimInstance Win32_Service -Filter ("Name='" + $n + "'")
'PKJSON:' + ([ordered]@{{ state = $w.State; start = $w.StartMode }} | ConvertTo-Json -Compress)
"""
    out, err, had = remote.ps(host, script)
    if "PKJSON:" not in out:
        raise remote.RemoteError(util.first_line(err, "Aktion fehlgeschlagen")[:400])
    res = _json(out)
    db.ex("UPDATE services SET state=?, start_mode=? WHERE computer_id=? AND name=?",
          (res.get("state"), res.get("start"), cid, name))
    return res


# --- Prozesse ----------------------------------------------------------------
PROCESSES_PS = r"""
function D($d) { if ($d) { try { ([datetime]$d).ToString('yyyy-MM-dd HH:mm:ss') } catch { $null } } }
try { $pl = Get-Process -IncludeUserName -ErrorAction Stop } catch { $pl = Get-Process }
'PKJSON:' + (ConvertTo-Json -Depth 3 -Compress -InputObject @($pl | Sort-Object ProcessName | ForEach-Object { [ordered]@{
  name = $_.ProcessName; pid = $_.Id; user = $_.UserName; memory = [int64]$_.WorkingSet64
  cpu_s = $(if ($_.CPU) { [math]::Round($_.CPU, 1) } else { $null }); start = $(try { D $_.StartTime } catch { $null })
  path = $(try { $_.Path } catch { $null }); company = $(try { $_.Company } catch { $null }) } }))
"""


def _save_inv(cid, cat, rows):
    db.ex("INSERT INTO inventory_data(computer_id, category, json, updated) VALUES(?,?,?,?) "
          "ON CONFLICT(computer_id, category) DO UPDATE SET json=excluded.json, updated=excluded.updated",
          (cid, cat, json.dumps(rows, ensure_ascii=False), util.now()))


def live_processes(cid):
    _c, host = _host(cid)
    out, err, _ = remote.ps(host, PROCESSES_PS)
    rows = _L(_json(out))
    _save_inv(cid, "processes", rows)
    return len(rows)


def kill_process(cid, pid, name):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        raise ValueError("Ungültige Prozess-ID")
    if pid <= 4:
        raise ValueError("Systemprozesse können nicht beendet werden.")
    if not PROC_RE.match(name or ""):
        raise ValueError("Ungültiger Prozessname")
    _c, host = _host(cid)
    script = f"""$ErrorActionPreference = 'Stop'
$p = Get-Process -Id {pid}
if ($p.ProcessName -ne {util.psq(name)}) {{ throw ('Die PID {pid} gehört inzwischen zu ' + $p.ProcessName + ' – bitte Liste aktualisieren.') }}
Stop-Process -Id {pid} -Force
'PKOK'
"""
    out, err, _ = remote.ps(host, script)
    if "PKOK" not in out:
        msg = (err or "Prozess konnte nicht beendet werden").strip().splitlines()
        raise remote.RemoteError(msg[0][:400] if msg else "Prozess konnte nicht beendet werden")
    raw = db.val("SELECT json FROM inventory_data WHERE computer_id=? AND category='processes'", (cid,))
    if raw:
        rows = [r for r in json.loads(raw) if r.get("pid") != pid]
        _save_inv(cid, "processes", rows)
    return True


# --- BitLocker direkt vom Client ------------------------------------------------
BITLOCKER_PS = r"""
'PKJSON:' + (ConvertTo-Json -Compress -InputObject @(Get-BitLockerVolume -ErrorAction Stop | ForEach-Object {
  $v = $_
  @($v.KeyProtector) | Where-Object { "$($_.KeyProtectorType)" -eq 'RecoveryPassword' } | ForEach-Object {
    [ordered]@{ volume = $v.MountPoint; id = "$($_.KeyProtectorId)"; password = "$($_.RecoveryPassword)"; status = "$($v.ProtectionStatus)" } } }))
"""


def bitlocker_from_client(cid):
    _c, host = _host(cid)
    out, err, _ = remote.ps(host, BITLOCKER_PS)
    if "PKJSON:" not in out:
        raise remote.RemoteError(util.first_line(err, "BitLocker konnte nicht gelesen werden")[:400])
    return _L(_json(out))


# --- Ereignisanzeige (Live über WinRM) ------------------------------------------
EVENT_LOGS = {"System": "System", "Application": "Anwendung", "Security": "Sicherheit",
              "Setup": "Setup", "Microsoft-Windows-Windows Defender/Operational": "Defender"}
EVENT_LEVELS = {1: "Kritisch", 2: "Fehler", 3: "Warnung", 4: "Information", 0: "Information", 5: "Ausführlich"}


def event_log(cid, log="System", level=0, count=100, query=""):
    """Liest die neuesten Ereignisse eines Protokolls. level: 0=alle, sonst nur <= level (1 Kritisch … 3 Warnung)."""
    _c, host = _host(cid)
    if log not in EVENT_LOGS:
        raise ValueError("Unbekanntes Protokoll")
    count = max(1, min(int(count or 100), 500))
    filt = {"LogName": log}
    lvl = int(level or 0)
    if lvl in (1, 2, 3):
        filt_levels = [str(x) for x in range(1, lvl + 1)]
        hashparts = f"LogName={util.psq(log)}; Level={','.join(filt_levels)}"
    else:
        hashparts = f"LogName={util.psq(log)}"
    q = util.psq(query.strip()) if query and query.strip() else "$null"
    script = f"""$ErrorActionPreference = 'Stop'
try {{ $ev = Get-WinEvent -FilterHashtable @{{ {hashparts} }} -MaxEvents {count} -ErrorAction Stop }}
catch {{ if ($_.Exception.Message -like '*No events*') {{ $ev = @() }} else {{ throw }} }}
$needle = {q}
if ($needle) {{ $ev = $ev | Where-Object {{ $_.Message -like ('*' + $needle + '*') -or "$($_.Id)" -eq $needle -or $_.ProviderName -like ('*' + $needle + '*') }} }}
'PKJSON:' + (ConvertTo-Json -Depth 3 -Compress -InputObject @($ev | ForEach-Object {{
  [ordered]@{{ time = $_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss'); level = [int]$_.Level; levelName = "$($_.LevelDisplayName)";
    id = $_.Id; provider = "$($_.ProviderName)"; message = (("" + $_.Message) -replace '\\s+', ' ').Trim() }} }}))
"""
    out, err, _ = remote.ps(host, script, timeout=120)
    if "PKJSON:" not in out:
        raise remote.RemoteError(util.first_line(err, "Protokoll konnte nicht gelesen werden")[:400])
    rows = _L(_json(out))
    for r in rows:
        if not r.get("levelName"):
            r["levelName"] = EVENT_LEVELS.get(r.get("level"), "Information")
    return {"log": log, "rows": rows}
