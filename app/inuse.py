"""2.56.0: update lock while a program is in use.

Before a schedule installs, updates or removes a program on a computer, POCKET KNIFE asks the computer whether the
program is open in a user session. If it is, the computer is not touched: the target is postponed ("Zurückgestellt")
and tried again later - usually it works in the evening, after the user has closed the program or logged off.

Which program a deployment touches comes from its steps: winget IDs, MSI product codes, display names (detection of a
file installation, uninstallation) and process names entered in the package. For each of them the inventory knows
the uninstall key on that computer; the client reads it live (InstallLocation, DisplayIcon, UninstallString) and lists
the processes of the user sessions (Win32_Process, SessionId <> 0). The server decides: a process whose file lies in
the program's folder - or carries one of the entered process names - means "in use". Services (session 0) never count.

Programs that are known to update safely while open (browsers with their own update mechanism) are exempt by default;
the list is a setting. A package can also switch the check off ("auch während der Benutzung installieren").
"""
import datetime
import fnmatch
import json
import re
import threading

from . import db, pklog, util

PREFIX = "Zurückgestellt – "  # message prefix of a deployment target that was skipped because the program is open
DEFAULT_EXEMPT = "Google.Chrome*\nMicrosoft.Edge*\nBrave.Brave*"
MAX_KEYS = 40      # uninstall keys sent to a computer per check
MAX_PROCS = 30     # process names entered in a package
CHECK_TIMEOUT = 60

# Folders that are never "the folder of a program" (too general - every process below them would count)
_GENERIC = {"program files\\common files", "program files (x86)\\common files", "programdata\\microsoft",
            "programdata\\package cache", "program files\\microsoft", "program files (x86)\\microsoft",
            "program files\\windowsapps", "program files (x86)\\windowsapps", "program files\\windows nt",
            "program files (x86)\\windows nt"}
_USER_GENERIC = {"appdata", "appdata\\local", "appdata\\roaming", "appdata\\locallow", "appdata\\local\\programs",
                 "appdata\\local\\microsoft", "appdata\\roaming\\microsoft", "appdata\\local\\temp", "desktop",
                 "documents", "downloads"}

_stats_lock = threading.Lock()
STATS = {"checks": 0, "busy": 0, "free": 0, "unknown": 0, "errors": 0, "retry_checks": 0, "retry_busy": 0,
         "manual_checks": 0}


def _count(key, n=1):
    with _stats_lock:
        STATS[key] = STATS.get(key, 0) + n


def stats():
    with _stats_lock:
        return dict(STATS)


# --- Settings ---------------------------------------------------------------------------------------
def enabled():
    return bool(db.setting("inuse_enabled"))


def retry_minutes():
    return max(5, int(db.setting("inuse_retry_minutes") or 30))


def warn_days():
    return max(1, int(db.setting("inuse_warn_days") or 7))


def exempt_patterns():
    raw = db.setting("inuse_exempt")
    return [x.strip() for x in re.split(r"[\r\n,;]+", str(raw if raw is not None else DEFAULT_EXEMPT)) if x.strip()]


def clean_exempt(v):
    """Setting value: winget IDs or patterns with *, one per line."""
    parts = [x.strip() for x in re.split(r"[\r\n,;]+", str(v or "")) if x.strip()]
    if len(parts) > 200:
        raise ValueError("Ausnahmen der Updatesperre: höchstens 200 Einträge.")
    for x in parts:
        if not re.match(r"^[A-Za-z0-9.*?_+-]{1,128}$", x):
            raise ValueError(f"Ausnahmen der Updatesperre: „{x[:60]}“ ist keine winget-ID (Platzhalter * erlaubt).")
    return "\n".join(dict.fromkeys(parts))


def is_exempt(wid, patterns=None):
    w = (wid or "").lower()
    return any(fnmatch.fnmatchcase(w, p.lower()) for p in (exempt_patterns() if patterns is None else patterns))


def clean_procs(v):
    """Package field: process names (7zFM.exe or 7zFM), separated by comma or line break."""
    parts = [x.strip() for x in re.split(r"[\r\n,;]+", str(v or "")) if x.strip()]
    if len(parts) > MAX_PROCS:
        raise ValueError(f"Updatesperre: höchstens {MAX_PROCS} Prozessnamen.")
    for x in parts:
        if not re.match(r"^[A-Za-z0-9 ._()+-]{1,100}$", x) or x in (".", ".."):
            raise ValueError(f"Updatesperre: „{x[:60]}“ ist kein Prozessname (z. B. 7zFM.exe).")
    return ", ".join(dict.fromkeys(parts))


def _proc_key(name):
    n = (name or "").strip().lower()
    return n[:-4] if n.endswith(".exe") else n


# --- Which programs does a package touch? --------------------------------------------------------------
def programs(p):
    """{"winget": [...], "codes": [...], "names": [...], "procs": [...], "exempt": [...]} of an (expanded) package.
    Nested packages count too. Exempt winget IDs are listed separately and not checked."""
    from . import steps
    out: dict[str, list[str]] = {"winget": [], "codes": [], "names": [], "procs": [], "exempt": []}
    pats = exempt_patterns()

    def add(k, v):
        v = (v or "").strip()
        if v and v.lower() not in [x.lower() for x in out[k]]:
            out[k].append(v)

    def walk(pkg, depth=0):
        if depth > 6 or not isinstance(pkg, dict):
            return
        for x in re.split(r"[\r\n,;]+", str(pkg.get("in_use_procs") or "")):
            add("procs", x)
        st = pkg.get("steps")
        if not isinstance(st, dict):
            if pkg.get("winget_id"):   # package without steps (old format)
                (add("exempt", pkg["winget_id"]) if is_exempt(pkg["winget_id"], pats) else add("winget", pkg["winget_id"]))
            return
        for ph, _l in steps.PHASES:
            for s in st.get(ph) or []:
                if not isinstance(s, dict) or not s.get("enabled", True):
                    continue
                t = s.get("type")
                if t == "install" and s.get("source") == "winget" and s.get("winget_id"):
                    (add("exempt", s["winget_id"]) if is_exempt(s["winget_id"], pats) else add("winget", s["winget_id"]))
                elif t == "install":
                    add("codes", s.get("product_code"))
                    add("names", s.get("detect_name"))
                elif t == "uninstall":
                    add("names", s.get("match_name"))
                elif t == "nested":
                    walk(s.get("package"), depth + 1)
    walk(p)
    return out


def ignored(p):
    """Package says: install even while the program is open (also any nested package that says so for itself is
    still checked - only the top level decides for the whole deployment)."""
    return bool((p or {}).get("in_use_ignore"))


def _sw_rows(cid, prog):
    wids = {w.lower() for w in prog["winget"]}
    codes = {c.lower().strip("{}") for c in prog["codes"]}
    names = [n.lower() for n in prog["names"]]
    if not (wids or codes or names):
        return []
    out = []
    for r in db.q("""SELECT name, winget_id, product_code, hive, reg_path, install_location, uninstall_string
                     FROM software WHERE computer_id=?""", (cid,)):
        nm = (r["name"] or "").lower()
        if ((r["winget_id"] or "").lower() in wids or (r["product_code"] or "").lower().strip("{}") in codes
                or any(fnmatch.fnmatchcase(nm, n) for n in names)):
            out.append(dict(r))
    return out


# --- Folders of a program --------------------------------------------------------------------------------
def _norm_dir(path):
    """Lower-case folder without trailing backslash - or None if it is not a usable program folder."""
    d = (path or "").strip().strip('"').strip().replace("/", "\\").rstrip("\\").lower()
    if not re.match(r"^[a-z]:\\.+", d) or "\\..\\" in d + "\\":
        return None
    parts = [x for x in d[3:].split("\\") if x]
    if not parts or parts[0] == "windows" or "package cache" in parts or "temp" in parts or parts[-1] == "installer":
        return None
    rel = "\\".join(parts)
    if parts[0] in ("program files", "program files (x86)", "programdata"):
        if len(parts) < 2 or rel in _GENERIC:
            return None
    elif parts[0] == "users":
        tail = "\\".join(parts[2:])
        if len(parts) < 3 or tail in _USER_GENERIC:
            return None
    return d[:3] + rel


def _file_dir(value):
    """Folder of a file path in a registry value (DisplayIcon "C:\\x\\a.exe",0 / UninstallString "C:\\x\\u.exe" /S)."""
    v = (value or "").strip()
    if not v or re.match(r"(?i)^\"?[a-z]:\\windows\\system32\\msiexec(\.exe)?", v) or v.lower().startswith("msiexec"):
        return None
    if v.startswith('"'):
        path = v[1:].split('"', 1)[0]
    else:
        m = re.match(r"(?i)^(.+?\.(exe|ico|dll))(\b|,|\s|$)", v)
        path = m.group(1) if m else re.sub(r",\s*-?\d+$", "", v)
    path = path.strip()
    if "\\" not in path:
        return None
    d = path.rsplit("\\", 1)[0]
    last = d.rsplit("\\", 1)[-1].lower()
    if "unins" in last or "uninstall" in last:
        d = d.rsplit("\\", 1)[0]
    return d


def program_dirs(live, inventory_rows=()):
    """Program folders from the live registry values (and the inventory as a fallback)."""
    dirs = []
    for x in list(live or []) + [{"loc": r.get("install_location"), "un": r.get("uninstall_string")} for r in inventory_rows]:
        for cand in (x.get("loc"), _file_dir(x.get("icon")), _file_dir(x.get("un"))):
            d = _norm_dir(cand)
            if d and d not in dirs:
                dirs.append(d)
    return dirs


def busy_procs(procs, dirs, names):
    """Processes (dicts n/p/s from the client) of the user sessions that belong to the program."""
    want = {_proc_key(n) for n in names if n}
    hits = []
    for pr in procs or []:
        if not isinstance(pr, dict):
            continue
        try:
            if int(pr.get("s") or 0) == 0:
                continue  # services and session 0 never block
        except (TypeError, ValueError):
            continue
        n = str(pr.get("n") or "")
        path = str(pr.get("p") or "").replace("/", "\\").lower()
        if _proc_key(n) in want or any(path.startswith(d + "\\") for d in dirs):
            if n not in hits:
                hits.append(n)
    return hits


# --- Client query -------------------------------------------------------------------------------------------
def script(keys):
    """CLM-safe, only cmdlets and CIM; reads the uninstall keys and the processes of the user sessions."""
    arr = ", ".join(util.psq("Registry::" + k) for k in keys[:MAX_KEYS])
    return ("$pkKeys = @(" + arr + ")\n"
            "$pkHit = @(foreach ($pkK in $pkKeys) {\n"
            "  if (Test-Path -LiteralPath $pkK) {\n"
            "    $pkV = Get-ItemProperty -LiteralPath $pkK -ErrorAction SilentlyContinue -ErrorVariable pkErrK\n"
            "    if ($pkV) { [ordered]@{ loc = ('' + $pkV.InstallLocation); icon = ('' + $pkV.DisplayIcon); un = ('' + $pkV.UninstallString) } }\n"
            "  }\n"
            "})\n"
            "$pkProc = @(Get-CimInstance -ClassName Win32_Process -Filter 'SessionId <> 0' -ErrorAction SilentlyContinue "
            "-ErrorVariable pkErrP | ForEach-Object { [ordered]@{ n = ('' + $_.Name); p = ('' + $_.ExecutablePath); s = $_.SessionId } })\n"
            "'PKJSON:' + (ConvertTo-Json -Compress -Depth 3 -InputObject ([ordered]@{ keys = $pkHit; procs = $pkProc; "
            "perr = ('' + $pkErrP) }))\n")


def _parse(out):
    m = re.search(r"PKJSON:(.*)", out or "")
    if not m:
        raise ValueError("keine Antwort")
    d = json.loads(m.group(1))
    keys, procs = d.get("keys") or [], d.get("procs") or []
    return (keys if isinstance(keys, list) else [keys]), (procs if isinstance(procs, list) else [procs]), d.get("perr") or ""


def label(prog, rows):
    names = []
    for r in rows:
        n = (r.get("name") or "").strip()
        if n and n not in names:
            names.append(n)
    if not names:
        names = prog["winget"] + prog["names"] or [", ".join(prog["procs"])]
    return ", ".join(names[:3]) + (" …" if len(names) > 3 else "")


def check(c, prog, ps=None):
    """Asks the computer whether the program is open. Returns
    {"state": "busy"|"free"|"unknown"|"error", "procs": [...], "label": "...", "error": "..."}.
    "unknown": nothing about the program is known (not in the inventory, no process names) - nothing to check."""
    from . import remote
    rows = _sw_rows(c["id"], prog)
    keys: list[str] = []
    for r in rows:
        if r.get("hive") and r.get("reg_path") and len(keys) < MAX_KEYS:
            k = f"{r['hive']}\\{r['reg_path']}"
            if k not in keys:
                keys.append(k)
    lab = label(prog, rows)
    if not keys and not rows and not prog["procs"]:
        _count("unknown")
        return {"state": "unknown", "procs": [], "label": lab, "error": ""}
    host = c.get("dns_name") or c.get("name")
    try:
        out, err, _x = (ps or remote.ps)(host, script(keys), timeout=CHECK_TIMEOUT)
        live, procs, perr = _parse(out)
    except Exception as e:  # noqa: BLE001 - a failed check never blocks; the caller decides
        _count("errors")
        return {"state": "error", "procs": [], "label": lab, "error": str(e)[:300]}
    if perr and not procs:
        _count("errors")
        return {"state": "error", "procs": [], "label": lab, "error": f"Prozessliste nicht lesbar: {perr[:200]}"}
    hits = busy_procs(procs, program_dirs(live, rows), prog["procs"])
    _count("checks")
    _count("busy" if hits else "free")
    return {"state": "busy" if hits else "free", "procs": hits[:8], "label": lab, "error": ""}


def message(res):
    procs = ", ".join(res["procs"][:5]) + (" …" if len(res["procs"]) > 5 else "")
    return (f"{PREFIX}{res['label']} ist geöffnet ({procs}) – wird nicht beendet, "
            f"neuer Versuch in {retry_minutes()} Minuten")[:1000]


def applies(d, p):
    """Does the update lock apply to this deployment? Only to deployments of schedules (if the schedule did not switch
    it off), only if switched on in the settings, and not if the package says "auch während der Benutzung"."""
    if not enabled() or ignored(p) or not d.get("schedule_id"):
        return False
    row = db.q1("SELECT options FROM schedules WHERE id=?", (d["schedule_id"],))
    try:
        opts = json.loads((row or {}).get("options") or "{}")
    except ValueError:
        opts = {}
    return bool(opts.get("in_use_wait", True))


# --- Postponed targets ------------------------------------------------------------------------------------------
def prog_key(d, p):
    """Postponement per schedule, computer and program: winget ID for winget schedules, otherwise the schedule.
    Packages from the winget cache carry no winget ID - the deployment name ends with it ("<schedule>: <winget ID>")."""
    kind = db.val("SELECT kind FROM schedules WHERE id=?", (d.get("schedule_id"),))
    if kind != "winget":
        return "packages"
    wid = (p or {}).get("winget_id") or (d.get("name") or "").rsplit(": ", 1)[-1].strip()
    return wid.lower() if wid and util.WINGET_ID_RE.match(wid) else "packages"


def postpone(sid, cid, key, lab, procs, prog):
    now = util.now()
    nxt = util.fmt(util.parse(now) + datetime.timedelta(minutes=retry_minutes()))
    db.ex("""INSERT INTO inuse_postponed(schedule_id, computer_id, prog, label, procs, spec, since, last_check, next_try, checks)
             VALUES(?,?,?,?,?,?,?,?,?,1) ON CONFLICT(schedule_id, computer_id, prog) DO UPDATE SET label=excluded.label,
             procs=excluded.procs, spec=excluded.spec, last_check=excluded.last_check, next_try=excluded.next_try,
             checks=checks + 1""",
          (sid, cid, key, lab[:300], ", ".join(procs)[:300], json.dumps(prog)[:20000], now, now, nxt))


def clear(sid, cid, key=None):
    if key is None:
        db.ex("DELETE FROM inuse_postponed WHERE schedule_id=? AND computer_id=?", (sid, cid))
    else:
        db.ex("DELETE FROM inuse_postponed WHERE schedule_id=? AND computer_id=? AND prog=?", (sid, cid, key))


def not_due(sid):
    """{(computer_id, prog)} whose next attempt is still waiting."""
    return {(r["computer_id"], r["prog"]) for r in db.q(
        "SELECT computer_id, prog FROM inuse_postponed WHERE schedule_id=? AND next_try > ?", (sid, util.now()))}


def due(sid):
    return [dict(r) for r in db.q("""SELECT p.* FROM inuse_postponed p JOIN computers c ON c.id = p.computer_id
                                      WHERE p.schedule_id=? AND p.next_try <= ? AND c.online=1 AND c.winrm=1""",
                                   (sid, util.now()))]


def long_waiting(days=None):
    lim = util.ago(days=days or warn_days())
    return [dict(r) for r in db.q("""SELECT p.*, c.name computer, s.name schedule FROM inuse_postponed p
                                      JOIN computers c ON c.id = p.computer_id JOIN schedules s ON s.id = p.schedule_id
                                      WHERE p.since <= ? ORDER BY p.since""", (lim,))]


def all_postponed():
    return [dict(r) for r in db.q("""SELECT p.schedule_id, p.computer_id, p.prog, p.label, p.procs, p.since, p.last_check,
                                            p.next_try, p.checks, c.name computer, s.name schedule
                                     FROM inuse_postponed p JOIN computers c ON c.id = p.computer_id
                                     JOIN schedules s ON s.id = p.schedule_id ORDER BY p.since""")]


_retrying = set()
_retry_lock = threading.Lock()


def retry_due(s, fire, ps=None, background=True):
    """Retry postponed targets of a schedule whose wait is over: first ask the computer again (no deployment while
    the program is still open), then start the schedule for the computers where it is closed now.
    Runs in the background - the minute tick must not wait for WinRM."""
    rows = due(s["id"])
    if not rows:
        return
    with _retry_lock:
        if s["id"] in _retrying:
            return
        _retrying.add(s["id"])
    # the next attempt is not before the interval - even if the check below fails
    nxt = util.fmt(util.parse(util.now()) + datetime.timedelta(minutes=retry_minutes()))
    for r in rows:
        db.ex("UPDATE inuse_postponed SET next_try=? WHERE schedule_id=? AND computer_id=? AND prog=?",
              (nxt, s["id"], r["computer_id"], r["prog"]))

    def run():
        try:
            free = []
            for r in rows:
                c = db.q1("SELECT * FROM computers WHERE id=?", (r["computer_id"],))
                if not c:
                    continue
                try:
                    prog = json.loads(r.get("spec") or "{}")
                except ValueError:
                    prog = {}
                prog = {k: prog.get(k) or [] for k in ("winget", "codes", "names", "procs")}
                res = check(c, prog, ps=ps)
                _count("retry_checks")
                if res["state"] == "busy":
                    _count("retry_busy")
                    db.ex("""UPDATE inuse_postponed SET procs=?, last_check=?, checks=checks + 1
                             WHERE schedule_id=? AND computer_id=? AND prog=?""",
                          (", ".join(res["procs"])[:300], util.now(), s["id"], r["computer_id"], r["prog"]))
                else:
                    # closed now (or not determinable): the run itself checks again right before installing
                    db.ex("DELETE FROM inuse_postponed WHERE schedule_id=? AND computer_id=? AND prog=?",
                          (s["id"], r["computer_id"], r["prog"]))
                    if c["id"] not in free:
                        free.append(c["id"])
            if free:
                fire(s, free)
        except Exception as e:  # noqa: BLE001
            pklog.warn("deploy", f"Zeitplan „{s['name']}“: zurückgestellte Ziele nicht geprüft: {e}")
        finally:
            with _retry_lock:
                _retrying.discard(s["id"])
    if background:
        threading.Thread(target=run, name=f"inuse-{s['id']}", daemon=True).start()
    else:
        run()


def cleanup():
    """Postponements of schedules that no longer exist or are switched off, or of computers no longer targeted."""
    from . import schedules
    for s in schedules.all_schedules():
        rows = db.q("SELECT computer_id FROM inuse_postponed WHERE schedule_id=?", (s["id"],))
        if not rows:
            continue
        if not s["enabled"] or not s["options"].get("in_use_wait", True) or not enabled():
            db.ex("DELETE FROM inuse_postponed WHERE schedule_id=?", (s["id"],))
            continue
        mem = schedules.members(s)
        gone = {r["computer_id"] for r in rows} - mem
        if gone:
            db.ex(f"DELETE FROM inuse_postponed WHERE schedule_id=? AND computer_id IN ({','.join('?' * len(gone))})",
                  (s["id"], *sorted(gone)))
        if s["kind"] == "winget":
            # updated in the meantime (by hand, by the program itself): nothing to wait for any more
            db.ex("""DELETE FROM inuse_postponed WHERE schedule_id=? AND prog <> 'packages' AND NOT EXISTS (
                       SELECT 1 FROM software sw WHERE sw.computer_id = inuse_postponed.computer_id
                       AND lower(sw.winget_id) = inuse_postponed.prog AND sw.outdated = 1)""", (s["id"],))


# --- Manual deployment: who has the program open right now? -------------------------------------------------------
def check_many(p, computer_ids, limit=50, ps=None):
    """For the start dialog of a manual deployment: computers (online, at most `limit`) where the program is open."""
    from concurrent.futures import ThreadPoolExecutor
    prog = programs(p)
    if not (prog["winget"] or prog["codes"] or prog["names"] or prog["procs"]):
        return {"checked": 0, "busy": [], "skipped": 0, "exempt": prog["exempt"]}
    ids = [int(i) for i in computer_ids][:500]
    comps = [dict(r) for r in db.q(f"SELECT * FROM computers WHERE online=1 AND winrm=1 AND id IN ({','.join('?' * len(ids)) or 'NULL'})",
                                   tuple(ids))]
    todo = comps[:limit]
    _count("manual_checks", len(todo))
    busy = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for c, res in zip(todo, ex.map(lambda c: check(c, prog, ps=ps), todo)):
            if res["state"] == "busy":
                busy.append({"id": c["id"], "name": c["name"], "label": res["label"], "procs": res["procs"]})
    return {"checked": len(todo), "busy": busy, "skipped": len(comps) - len(todo), "exempt": prog["exempt"]}
