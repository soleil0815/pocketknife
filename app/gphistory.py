"""2.47.0: Group Policy history of a computer (like the well-known "GPAnalyser" scripts, but in the browser).

Reads the log Microsoft-Windows-GroupPolicy/Operational live via WinRM on the client itself (no remote event log
access needed) and groups the events by activity ID into processing runs:
start 4000-4007, end 8000-8007 (ok) / 6000-6007 (warning) / 7000-7007 (error).
Details per run: domain controller (5308), account/domain (5310), time until all GPOs were determined (5312),
applied and filtered GPOs (5312/5313), extensions with their run time (5016/6016/7016) and their GPO list (4016).
For runs with warnings/errors the Group Policy errors of the System log are added (e.g. 1058, 1129, 1085).
The client script is Constrained-Language-safe: no ToXml() (method calls on non-core types are blocked there),
only properties of the events.
"""
import re
from datetime import datetime, timedelta

from . import actions, remote, util

HOURS = (12, 24, 48, 168)
MAX_EVENTS = 8000
# Messages needed in full (GPO lists, DC details); all others are shortened
FULL_MSG_IDS = (4016, 5308, 5310, 5312, 5313, 5016, 6016, 7016)
REASONS = {4000: "Computerstart", 4001: "Anmeldung", 4002: "Benutzer (Netzwerkänderung)", 4003: "Computer (Netzwerkänderung)",
           4004: "Computer (manuell)", 4005: "Benutzer (manuell)", 4006: "Computer (periodisch)", 4007: "Benutzer (periodisch)"}
COMPUTER_STARTS = (4000, 4003, 4004, 4006)


def script(hours):
    return f"""$ErrorActionPreference = 'Stop'
$since = (Get-Date).AddHours(-{int(hours)})
function PK-Ev($filter, $max) {{
  try {{ @(Get-WinEvent -FilterHashtable $filter -MaxEvents $max -ErrorAction Stop) }}
  catch {{ if ("$($_.FullyQualifiedErrorId)" -like 'NoMatchingEventsFound*' -or "$($_.Exception.Message)" -like '*No events*') {{ @() }} else {{ throw }} }}
}}
function PK-Row($ev, $full) {{
  $m = "" + $ev.Message
  if (-not $full -and $m.Length -gt 600) {{ $m = $m.Substring(0, 600) }}
  if ($m.Length -gt 6000) {{ $m = $m.Substring(0, 6000) }}
  $p = @(); foreach ($x in @($ev.Properties)) {{ $v = "" + $x.Value; if ($v.Length -gt 300) {{ $v = $v.Substring(0, 300) }}; $p += $v }}
  [ordered]@{{ t = $ev.TimeCreated.ToString('yyyy-MM-ddTHH:mm:ss.fff'); id = [int]$ev.Id; lvl = [int]$ev.Level;
    act = "$($ev.ActivityId)"; msg = $m; p = $p }}
}}
$full = @({", ".join(str(i) for i in FULL_MSG_IDS)})
$ops = PK-Ev @{{ LogName = 'Microsoft-Windows-GroupPolicy/Operational'; StartTime = $since }} {MAX_EVENTS}
$sys = PK-Ev @{{ LogName = 'System'; ProviderName = 'Microsoft-Windows-GroupPolicy'; Level = 1, 2, 3; StartTime = $since }} 300
'PKJSON:' + (ConvertTo-Json -Depth 4 -Compress -InputObject ([ordered]@{{
  ops = @($ops | ForEach-Object {{ PK-Row $_ ($full -contains [int]$_.Id) }});
  sys = @($sys | ForEach-Object {{ PK-Row $_ $true }}) }}))
"""


def _ts(s):
    try:
        return datetime.fromisoformat(str(s)[:23])
    except ValueError:
        return None


def _lines_after_header(msg):
    """GPO names from a 4016/5312/5313 message: the lines after the first line ending in ':' (list header).
    Indented lines are the reason for the GPO above (5313: "Nicht angewendet (leer)") and are appended to it."""
    raw = str(msg or "").replace("\r", "").split("\n")
    for i, ln in enumerate(raw):
        if re.search(r":\s*(\(.*\))?\s*$", ln.strip()):
            rest = raw[i + 1:]
            break
    else:
        rest = raw[1:]
    out: list[str] = []
    for ln in rest:
        t = ln.strip()
        if not t or t.endswith(":"):
            continue
        if ln[:1] in (" ", "\t") and out:
            out[-1] = f"{out[-1]} – {t}"
        else:
            out.append(t)
    return out[:200]


def _principal(ev):
    for v in ev.get("p") or []:
        if "\\" in v and not v.startswith("\\\\") and len(v) < 200:
            return v
    m = re.search(r"\b([\w.\- ]+\\[^\s.]+)", ev.get("msg") or "")
    return m.group(1) if m else ""


def _status(end_id):
    if end_id is None:
        return "open"  # no end event (still running, or ended without a log entry, e.g. switched off)
    return {8: "ok", 6: "warn", 7: "err"}.get(end_id // 1000, "unknown")


def build(data):
    """Groups the raw events into runs (newest first). Pure function (tested without a client)."""
    ops = [e for e in actions._L((data or {}).get("ops")) if isinstance(e, dict)]
    sysev = [e for e in actions._L((data or {}).get("sys")) if isinstance(e, dict)]
    by_act: dict[str, list[dict]] = {}
    for e in ops:
        by_act.setdefault(e.get("act") or "", []).append(e)
    runs = []
    for act, evs in by_act.items():
        if not act:
            continue
        evs.sort(key=lambda e: (e.get("t") or "", e.get("id") or 0))
        start = next((e for e in evs if 4000 <= (e.get("id") or 0) <= 4007), None)
        if not start:
            continue  # started before the time window
        ends = [e for e in evs if (e.get("id") or 0) in range(6000, 6008) or (e.get("id") or 0) in range(7000, 7008)
                or (e.get("id") or 0) in range(8000, 8008)]
        end = ends[-1] if ends else None
        t0, t1 = _ts(start.get("t")), _ts(end.get("t")) if end else None
        sid = start["id"]
        run = {"act": act, "start": start.get("t"), "end": end.get("t") if end else None,
               "seconds": round((t1 - t0).total_seconds(), 2) if t0 and t1 else None,
               "status": _status(end.get("id") if end else None), "end_id": end.get("id") if end else None,
               "end_message": (end.get("msg") or "") if end and end.get("id", 0) < 8000 else "",
               "reason": REASONS.get(sid, str(sid)), "target": "computer" if sid in COMPUTER_STARTS else "user",
               "principal": _principal(start), "dc": "", "dc_ip": "", "account": "", "domain": "",
               "discover_seconds": None, "gpos": [], "filtered": [], "extensions": [], "events": [], "system": []}
        gp_lists: dict[str, list[str]] = {}
        for e in evs:
            i = e.get("id") or 0
            p = e.get("p") or []
            if i == 5308:
                run["dc"] = next((v for v in p if v.startswith("\\\\")), run["dc"])
                run["dc_ip"] = next((v for v in p if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", v)
                                     or (":" in v and re.fullmatch(r"[0-9a-fA-F:]+(%\d+)?", v))), run["dc_ip"])
            elif i == 5310:
                run["account"] = next((v for v in p if v.upper().startswith("CN=")), run["account"])
                run["domain"] = next((v for v in p if re.fullmatch(r"[\w-]+(\.[\w-]+)+", v) and not v.upper().startswith("CN=")), run["domain"])
            elif i == 5312:
                if t0 and _ts(e.get("t")):
                    run["discover_seconds"] = round((_ts(e.get("t")) - t0).total_seconds(), 2)
                run["gpos"] = _lines_after_header(e.get("msg"))
            elif i == 5313:
                run["filtered"] = _lines_after_header(e.get("msg"))
            elif i == 4016:
                name = p[1] if len(p) > 1 else ""
                gp_lists[name] = _lines_after_header(e.get("msg"))
            elif i in (5016, 6016, 7016):
                try:
                    ms = float(p[0]) if p else 0.0
                except ValueError:
                    ms = 0.0
                name = p[2] if len(p) > 2 else (p[1] if len(p) > 1 else "")
                run["extensions"].append({"name": name, "seconds": round(ms / 1000, 2),
                                          "status": {5016: "ok", 6016: "warn", 7016: "err"}[i],
                                          "message": (e.get("msg") or "") if i != 5016 else ""})
            run["events"].append({"t": e.get("t"), "id": i, "lvl": e.get("lvl"), "msg": e.get("msg") or ""})
        for x in run["extensions"]:
            x["gpos"] = gp_lists.get(x["name"], [])
        run["ext_seconds"] = round(sum(x["seconds"] for x in run["extensions"]), 2) if run["extensions"] else None
        if run["extensions"]:
            slow = max(run["extensions"], key=lambda x: x["seconds"])
            slow["slowest"] = True
        # Errors of the System log: same activity ID, otherwise within the run (+30 s)
        lim = (t1 or t0) + timedelta(seconds=30) if t0 else None
        for s in sysev:
            ts = _ts(s.get("t"))
            if (s.get("act") and s.get("act") == act) or (not s.get("act") and t0 and ts and t0 <= ts <= lim):
                run["system"].append({"t": s.get("t"), "id": s.get("id"), "lvl": s.get("lvl"), "msg": s.get("msg") or ""})
        runs.append(run)
    runs.sort(key=lambda r: r["start"] or "", reverse=True)
    return runs


def history(cid, hours=24):
    hours = int(hours or 24)
    if hours not in HOURS:
        raise ValueError("Zeitraum nicht erlaubt")
    _c, host = actions._host(cid)
    out, err, _ = remote.ps(host, script(hours), timeout=180)
    if "PKJSON:" not in out:
        raise remote.RemoteError(util.first_line(err, "Gruppenrichtlinien-Protokoll konnte nicht gelesen werden")[:400])
    runs = build(actions._json(out))
    return {"hours": hours, "runs": runs}
