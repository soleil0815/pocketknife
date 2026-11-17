"""2.54.0: check mode "PowerShell-Protokoll" - after every call POCKET KNIFE makes on a computer, it looks into that
computer's PowerShell log for the entries monitoring software reacts to:
- event 4100 (error while executing - PowerShell logs every terminating error, even a caught one)
- event 4104 with level "Warnung" (script block with "suspicious" content)
and remembers each with the POCKET KNIFE function that caused it. Off by default; while on, every call to a computer
is followed by one short extra call (a separate WinRM session, so the production scripts stay exactly as they are).

Attribution: per computer the check remembers the computer's own time of the last look. Everything logged since then
belongs to the call that just ran - including what a background task of a previous call (deployment, tool as SYSTEM)
logged in the meantime; the previous call is noted for that.

"Prüflauf": runs every reading function of POCKET KNIFE once against one computer (scan, services, processes,
BitLocker, every event log, Group Policy history and result, disk space, files, the shipped read-only tools, a command
as the service account and as SYSTEM) - nothing on the computer is changed.
"""
import inspect
import json
import os
import re
import threading
import time

from . import config, db, util

SETTING = "pscheck_on"
MAX_FINDINGS = 2000
_lock = threading.Lock()
_host_locks: dict = {}
STATE: dict = {"since": {}, "last_op": {}, "calls": {}, "findings": [], "started": None}
RUN: dict = {}  # Prüflauf: {"cid", "name", "steps": [...], "running", "started", "ended"}
_loaded = False

# One look into the log: everything since the computer's own time of the last look (clean for 4104, no terminating
# error: -ErrorAction SilentlyContinue, Constrained Language safe). The first look only fixes the start time.
PROBE = r"""Start-Sleep -Milliseconds 1500
$pkN = Get-Date
'PKCHK_NOW:' + $pkN.ToString('o')
# who runs this look (the service account): its SID marks POCKET KNIFE's own sessions
$pkMe = @(& (Join-Path $env:SystemRoot 'System32\whoami.exe') /user /fo csv /nh 2>$null)
if ($pkMe.Count -and ("" + $pkMe[0]) -match '"(S-1-[0-9-]+)"') { 'PKCHK_ME:' + $matches[1] }
if ('__SINCE__') {
  $pkS = Get-Date -Date '__SINCE__'
  foreach ($x in @(Get-WinEvent -FilterHashtable @{ LogName = 'Microsoft-Windows-PowerShell/Operational'; Id = 4100, 4104; StartTime = $pkS } -MaxEvents 300 -ErrorAction SilentlyContinue)) {
    if ($x.Id -eq 4104 -and [int]$x.Level -ne 3) { continue }
    if ($x.TimeCreated -ge $pkN) { continue }
    $m = [string]$x.Message
    if ($m.Length -gt 6000) { $m = $m.Substring(0, 6000) }
    'PKCHK_EV:' + (ConvertTo-Json -Compress -InputObject @{ t = $x.TimeCreated.ToString('o'); id = $x.Id; lvl = [int]$x.Level; u = [string]$x.UserId; m = $m })
  }
}
"""


def _path():
    return os.path.join(config.DATA_DIR, "pscheck.json")


def _load():
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        with open(_path(), encoding="utf-8") as f:
            d = json.load(f)
        STATE["calls"] = dict(d.get("calls") or {})
        STATE["other"] = int(d.get("other") or 0)
        STATE["findings"] = list(d.get("findings") or [])[-MAX_FINDINGS:]
        STATE["started"] = d.get("started")
    except (OSError, ValueError):
        pass


def _save():
    try:
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"calls": STATE["calls"], "findings": STATE["findings"], "started": STATE["started"],
                       "other": STATE.get("other", 0)}, f, ensure_ascii=False)
        os.replace(tmp, _path())
    except OSError:
        pass


_on_cache = [0.0, False]


def enabled():
    """Cached for 5 seconds - asked before every call to a computer."""
    if time.monotonic() - _on_cache[0] < 5:
        return _on_cache[1]
    try:
        on = str(db.setting_raw(SETTING)) == "1"
    except Exception:
        on = False
    _on_cache[0], _on_cache[1] = time.monotonic(), on
    return on


def set_enabled(on, user=""):
    from . import pklog
    with _lock:
        _load()
        db.set_setting(SETTING, "1" if on else "0")
        _on_cache[0], _on_cache[1] = time.monotonic(), bool(on)
        if on and not STATE["started"]:
            STATE["started"] = util.now()
        STATE["since"].clear()  # a fresh start time per computer
        _save()
    pklog.info("system", f"Prüfmodus PowerShell-Protokoll {'eingeschaltet' if on else 'ausgeschaltet'}.", user=user)


def clear(user=""):
    from . import pklog
    with _lock:
        STATE["calls"], STATE["findings"], STATE["since"], STATE["other"] = {}, [], {}, 0
        STATE["started"] = util.now() if enabled() else None
        _save()
    pklog.info("system", "Ergebnisse des Prüfmodus PowerShell-Protokoll gelöscht.", user=user)


def caller():
    """The POCKET KNIFE function that made the call: the first frame outside the remote layer."""
    skip = ("remote.py", "pscheck.py", "concurrent", "threading.py")
    for fr in inspect.stack(0)[2:]:
        fn = fr.filename.replace("\\", "/")
        if "/app/" not in fn or any(s in fn for s in skip):
            continue
        return f"{os.path.splitext(os.path.basename(fn))[0]}.{fr.function}"
    return "unbekannt"


def _host_lock(host):
    with _lock:
        return _host_locks.setdefault(str(host).lower(), threading.Lock())


def _field(m, *names):
    for n in names:
        mm = re.search(r"(?mi)^[ \t]*" + re.escape(n) + r"[ \t]*[:=][ \t]*([^\r\n]*)", m)
        if mm and mm.group(1).strip():
            return mm.group(1).strip()
    return ""


def parse_event(e):
    """One event from the probe -> finding (short texts; for 4104 the flagged spots by pslint)."""
    from . import pslint
    if e.get("crash"):
        app, _, why = str(e["crash"]).partition(" – ")
        return {"time": str(e.get("t") or "")[:19].replace("T", " "), "event": 1000, "message": f"{app}: {why}"[:500],
                "host_app": "", "user": ""}
    m = str(e.get("m") or "")
    eid = int(e.get("id") or 0)
    f = {"time": str(e.get("t") or "")[:19].replace("T", " "), "event": eid,
         "host_app": _field(m, "Hostanwendung", "Host Application")[:300],
         "user": _field(m, "Benutzer", "User")[:120]}
    if eid == 4100:
        f["message"] = (_field(m, "Fehlermeldung", "Error Message") or m.splitlines()[0] if m else "")[:500]
        f["error_id"] = _field(m, "Vollständig qualifizierte Fehler-ID", "Vollqualifizierte Fehler-ID", "Fully Qualified Error ID")[:200]
        f["command"] = _field(m, "Befehlsname", "Command Name")[:120]
        f["script"] = _field(m, "Skriptname", "Script Name")[:300]
    else:
        body = m.split("\n", 1)[1] if "\n" in m else m
        body = re.split(r"(?m)^\s*(?:ScriptBlock-ID|Skriptblock-ID|ScriptBlock ID)\s*:", body)[0].strip()
        f["script"] = _field(m, "Pfad", "Path")[:300]
        if "cmdletization" in body.lower():
            # 2.55.3: code of a generated (CDXML) Windows module - PowerShell writes the whole module as dozens of
            # warnings when it is loaded. One finding per module instead of one per piece of its code
            mm = re.search(r"ClassName = '([^']+)'", body)
            mod = mm.group(1).split("/")[-2] if mm and mm.group(1).count("/") >= 2 else ""
            f["message"] = "Generiertes Windows-Modul (CDXML) geladen" + (f": {mod}" if mod else "") + \
                " – es protokolliert seinen eigenen Code als Warnungen"
            f["cdxml"] = mod or "?"
            return f
        f["message"] = body[:400]
        f["flagged"] = [x["what"] for x in pslint.check(body)][:10]
    return f


def probe(host, run_raw):
    """One look into the log. run_raw(host, script) -> stdout. Returns (now, [events of POCKET KNIFE]) - entries of other
    software and users are left out (see is_pk)."""
    with _lock:
        since = STATE["since"].get(str(host).lower(), "")
    out = run_raw(host, PROBE.replace("__SINCE__", since.replace("'", "")))
    now, evs, me = "", [], ""
    for ln in str(out or "").splitlines():
        ln = ln.strip()
        if ln.startswith("PKCHK_NOW:"):
            now = ln[len("PKCHK_NOW:"):]
        elif ln.startswith("PKCHK_ME:"):
            me = ln[len("PKCHK_ME:"):]
        elif ln.startswith("PKCHK_EV:"):
            try:
                evs.append(json.loads(ln[len("PKCHK_EV:"):]))
            except ValueError:
                pass
    keep = [e for e in evs if is_pk(e, me)]
    with _lock:
        STATE["other"] = STATE.get("other", 0) + len(evs) - len(keep)
    return now, keep


def is_pk(e, me_sid):
    """Only what POCKET KNIFE causes: the service account's own sessions (same SID as the look itself), or a script /
    host application in POCKET KNIFE's work folder (tasks as SYSTEM or in the user's session). Other software (e.g. a
    monitoring agent as SYSTEM) and users' own PowerShell stay out."""
    if me_sid and str(e.get("u") or "") == me_sid:
        return True
    return "pocketknife" in str(e.get("m") or "").lower()


def wrap(host, fn, run_raw, op=None):
    """Runs fn() (one call to the computer) with a look into the log before (first call only) and after."""
    op = op or caller()
    with _host_lock(host):
        key = str(host).lower()
        if key not in STATE["since"]:
            try:
                now, _ = probe(host, run_raw)
                with _lock:
                    STATE["since"][key] = now
            except Exception:
                pass
        err = None
        result = None
        try:
            result = fn()
        except Exception as e:
            err = e
        try:
            crashes = _crash_lines(result)
        except Exception:
            crashes = []
        # not reachable / no answer: nothing ran there, and a look would only wait for the same timeout again
        if type(err).__name__ not in ("RemoteConnectError", "RemoteTimeout"):
            try:
                now, evs = probe(host, run_raw)
                record(host, op, now, evs + crashes)
            except Exception:
                pass
        if err is not None:
            raise err
        return result


def _crash_lines(result):
    """2.54.1: a program that POCKET KNIFE started crashed (line of PK-CrashInfo in the call's output) -> pseudo event."""
    from . import remote
    out = result[0] if isinstance(result, tuple) and result else ""
    return [{"id": 1000, "crash": ln.strip()[len(remote.CRASH_PREFIX):], "t": util.now()}
            for ln in str(out or "").splitlines() if ln.strip().startswith(remote.CRASH_PREFIX)]


def record(host, op, now, evs):
    key = str(host).lower()
    with _lock:
        _load()
        prev = STATE["last_op"].get(key, "")
        if now:
            STATE["since"][key] = now
        STATE["last_op"][key] = op
        c = STATE["calls"].setdefault(op, {"calls": 0, "e4100": 0, "e4104": 0, "crash": 0})
        c["calls"] += 1
        for e in evs:
            f = parse_event(e)
            f.update(op=op, prev_op=prev if prev != op else "", host=str(host), seen=util.now())
            k = {4100: "e4100", 4104: "e4104"}.get(f["event"], "crash")
            c[k] = c.get(k, 0) + 1
            _add(f)
        if evs or c["calls"] % 20 == 1:
            _save()


def _fkey(f):
    return (f.get("op"), f.get("event"), str(f.get("message", ""))[:200], f.get("command", ""), f.get("error_id", ""))


def _add(f):
    """2.55.3: identical findings are counted, not stored again - before, a flood of one kind (2821 warnings of one
    module) pushed the rare ones (4100, crashes) out of the 2000 kept entries and they were missing in the report.
    Caller holds _lock."""
    k = _fkey(f)
    for g in STATE["findings"]:
        if _fkey(g) == k:
            g["n"] = int(g.get("n") or 1) + 1
            hs = g.setdefault("hosts", [g.get("host")])
            if f["host"] not in hs and len(hs) < 100:
                hs.append(f["host"])
            g["seen"], g["time"] = f["seen"], f.get("time") or g.get("time")
            return
    f["n"], f["hosts"] = 1, [f["host"]]
    STATE["findings"].append(f)
    if len(STATE["findings"]) > MAX_FINDINGS:
        # full: drop the oldest single warning (4104 seen once) - errors, crashes, module notes and frequent findings stay
        fs = STATE["findings"]
        drop = min(range(len(fs)), key=lambda i: (fs[i]["event"] != 4104, int(fs[i].get("n") or 1), i))
        del fs[drop]


def note_modules(host, op, areas):
    """2.55.3: which part of a scan loaded which Windows modules (marks in the scan script while the check mode is on):
    {area: [module, ...]} -> one finding per area and module - names the cause of the warnings of generated modules."""
    if not areas:
        return
    with _lock:
        _load()
        for area, mods in areas.items():
            for mod in mods:
                _add({"time": util.now()[:19].replace("T", " "), "event": 0, "op": op, "prev_op": "", "host": str(host),
                      "seen": util.now(), "message": f"Bereich „{area}“ lädt das Modul „{mod}“", "area": area, "module": mod,
                      "host_app": "", "user": ""})
        _save()


def summary():
    with _lock:
        _load()
        calls = {k: dict({"crash": 0}, **v) for k, v in STATE["calls"].items()}
        return {"enabled": enabled(), "started": STATE["started"], "calls": calls, "other": STATE.get("other", 0),
                "findings": list(STATE["findings"][-500:]), "total": len(STATE["findings"]), "run": dict(RUN) if RUN else None}


def stats():
    """Diagnostic package: counts only (function names are POCKET KNIFE's own code names)."""
    s = summary()
    return {"enabled": s["enabled"], "functions_checked": len(s["calls"]),
            "calls": sum(v["calls"] for v in s["calls"].values()),
            "event_4100": sum(v["e4100"] for v in s["calls"].values()),
            "event_4104": sum(v["e4104"] for v in s["calls"].values()),
            "crashed_programs": sum(v["crash"] for v in s["calls"].values()),
            "other_software_left_out": s["other"],
            "per_function": {k: [v["calls"], v["e4100"], v["e4104"], v["crash"]] for k, v in sorted(s["calls"].items())}}


def report(anon_hosts=True, hostname=None):
    """Text to copy and send: per function the calls and every distinct finding (host names replaced).
    hostname: own replacement (the diagnostic package uses its placeholders, the same as in its other files)."""
    s = summary()
    hosts: dict = {}

    def hn(h):
        if hostname:
            return hostname(h)
        if not anon_hosts:
            return h
        return hosts.setdefault(str(h).lower(), f"PC-{len(hosts) + 1:03d}")
    lines = [f"POCKET KNIFE {config.VERSION} – Prüfmodus PowerShell-Protokoll",
             f"Aktiv seit: {s['started'] or '—'} · Funktionen geprüft: {len(s['calls'])} · "
             f"Aufrufe: {sum(v['calls'] for v in s['calls'].values())}", ""]
    lines.append("Funktion | Aufrufe | 4100 | 4104 | Absturz")
    for k, v in sorted(s["calls"].items(), key=lambda kv: (-(kv[1]["e4100"] + kv[1]["e4104"] + kv[1]["crash"]), kv[0])):
        lines.append(f"{k} | {v['calls']} | {v['e4100']} | {v['e4104']} | {v['crash']}")
    if s["other"]:
        lines.append(f"(Einträge anderer Software oder Benutzer ausgelassen: {s['other']})")
    seen: dict = {}
    with _lock:
        allf = [dict(f) for f in STATE["findings"]]
    for f in allf:
        k = _fkey(f)
        hs = {hn(h) for h in (f.get("hosts") or [f.get("host")]) if h}
        if k in seen:  # entries from before 2.55.3 (one per event)
            seen[k]["n"] += int(f.get("n") or 1)
            seen[k]["hosts"] |= hs
            continue
        seen[k] = dict(f, n=int(f.get("n") or 1), hosts=hs)
    # modules loaded per scan area first (they name the cause), then by frequency
    seen = dict(sorted(seen.items(), key=lambda kv: (kv[1]["event"] != 0, -kv[1]["n"])))
    if seen:
        lines += ["", "Funde (gleiche zusammengefasst):"]
    for f in seen.values():
        lines.append("")
        kind = "Absturz" if f["event"] == 1000 else "Modul" if f["event"] == 0 else f["event"]
        lines.append(f"[{kind}] {f['op']}" + (f" (seit dem vorigen Aufruf: {f['prev_op']})" if f.get("prev_op") else "")
                     + f" · {f['n']}× auf {', '.join(sorted(f['hosts']))}")
        lines.append(f"  Meldung: {f.get('message', '')}")
        for lab, key in (("Fehler-ID", "error_id"), ("Befehl", "command"), ("Skript", "script"), ("Hostanwendung", "host_app"),
                         ("Benutzer", "user")):
            if f.get(key):
                lines.append(f"  {lab}: {f[key]}")
        if f.get("flagged"):
            lines.append(f"  Auffällig laut Prüfung: {', '.join(f['flagged'])}")
    if not seen:
        lines += ["", "Keine Einträge 4100 oder 4104 (Warnung) und keine Abstürze durch POCKET KNIFE."]
    return "\n".join(lines) + "\n"


# --- Prüflauf -------------------------------------------------------------------------------------------------
def _steps(cid):
    """(label, function) - only reading functions; nothing on the computer is changed."""
    from . import actions, clientfiles, diskusage, gphistory, inventory, profiles, tools

    def scan():
        # every area and every active own scanner, saved as a normal scan
        prof = dict(profiles.default())
        prof["modules"] = list(inventory.MODULE_ORDER)
        prof["scanner_types"] = list(profiles.SCANNER_TYPES)
        st_ = inventory.scan_now(cid, user="Prüflauf", prof=prof)
        if st_ != "OK":
            raise RuntimeError(st_)
    st: list = [("Scan (alle Bereiche, eigene Scanner)", scan),
          ("Dienste", lambda: actions.live_services(cid)),
          ("Prozesse", lambda: actions.live_processes(cid)),
          ("BitLocker", lambda: actions.bitlocker_from_client(cid))]
    for log in actions.EVENT_LOGS:
        st.append((f"Ereignisanzeige: {actions.EVENT_LOGS[log]}", lambda log=log: actions.event_log(cid, log, count=20)))
    st += [("Ereignisanzeige: nur Kritisch", lambda: actions.event_log(cid, "Setup", level=1, count=20)),
           ("GPO-Verlauf", lambda: gphistory.history(cid, 24)),
           ("GPO-Ergebnis (Computer)", lambda: gphistory.rsop(cid, "computer")),
           ("Speicherplatz (Laufwerke)", lambda: diskusage.scan_fixed(cid, depth=1, user="Prüflauf")),
           ("Dateien: Laufwerke", lambda: clientfiles.drives(cid)),
           ("Dateien: C:\\", lambda: clientfiles.listing(cid, "C:\\")),
           ("Dateien: Suche ohne Treffer", lambda: clientfiles.search(cid, "C:\\Windows\\Temp", "pk-pruefung-nichts", limit=10, seconds=30))]
    for typ, run_as, cmd in (("powershell", "service", "Get-Date"), ("powershell", "system", "Get-Date"),
                             ("cmd", "service", "echo POCKET KNIFE"), ("cmd", "system", "echo POCKET KNIFE")):
        t = tools.adhoc_tool({"type": typ, "run_as": run_as, "script": cmd, "timeout_s": 120})
        st.append((f"Befehl ({typ}, {'SYSTEM' if run_as == 'system' else 'Dienstkonto'})", lambda t=t: tools.run(t, cid)))
    for name in READONLY_TOOLS:
        try:
            row = db.q1("SELECT * FROM tools WHERE name=?", (name,))
        except Exception:
            row = None
        if row:
            st.append((f"Tool: {name}", lambda row=row: tools.run(dict(row), cid)))
    return st


# shipped tools that only read (the others change something: GPUPDATE, DNS, spooler, BitLocker pause ...)
READONLY_TOOLS = ("TPM-Status", "BitLocker C: Status", "GPRESULT (Computer)", "IP-Konfiguration", "MAC-Adressen",
                  "Offene Verbindungen", "Update-Verlauf (letzte 20)", "Angemeldete Benutzer", "Anwendungsfehler (24 Std.)",
                  "CPU-Last (5 Sekunden)", "Uptime", "Batteriezustand")


def start_run(cid, user=""):
    from . import pklog
    c = db.q1("SELECT id, name FROM computers WHERE id=?", (cid,))
    if not c:
        raise ValueError("Computer nicht gefunden")
    if not enabled():
        raise ValueError("Bitte zuerst den Prüfmodus einschalten.")
    with _lock:
        if RUN.get("running"):
            raise ValueError(f"Es läuft bereits ein Prüflauf ({RUN.get('name')}).")
        steps = _steps(cid)
        RUN.clear()
        RUN.update(cid=cid, name=c["name"], running=True, started=util.now(), ended=None,
                   steps=[{"label": lab, "status": "wartet", "seconds": None, "note": ""} for lab, _ in steps])
    pklog.info("system", f"Prüflauf PowerShell-Protokoll auf {c['name']} gestartet.", user=user, computer_id=cid)

    def go():
        from . import remote
        unreachable = 0
        try:
            for i, (lab, fn) in enumerate(steps):
                s = RUN["steps"][i]
                if unreachable >= 2:
                    s["status"], s["note"] = "übersprungen", "Computer nicht erreichbar"
                    continue
                s["status"] = "läuft"
                t0 = time.time()
                try:
                    fn()
                    s["status"] = "OK"
                    unreachable = 0
                except Exception as e:
                    s["status"], s["note"] = "Fehler", str(e)[:300]
                    unreachable += 1 if isinstance(e, remote.RemoteConnectError) else 0
                s["seconds"] = round(time.time() - t0, 1)
                time.sleep(1)
        finally:
            RUN["running"], RUN["ended"] = False, util.now()
            pklog.info("system", f"Prüflauf PowerShell-Protokoll auf {c['name']} beendet.", user=user, computer_id=cid)
    threading.Thread(target=go, daemon=True, name="pscheck-run").start()
    return dict(RUN)
