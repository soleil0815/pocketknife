"""Detect integrity breaks in packages (2.26).

For every repository file a package uses, POCKET KNIFE remembers the confirmed state: size, modification date
and SHA-256 (table repo_baseline, key = path in the repository, lowercase – a file used by several packages
has one shared state). For whole package folders the file list is confirmed as well
(repo_baseline_dirs), so that added files stand out.

Checks:
- Quick check every 5 minutes from the repository index in memory (only name, size, date – no file content);
  without a valid index at most every 30 minutes directly on the share.
- If only the modification date changed, the hash is recomputed (in the background, throttled): same →
  merely "touched", the state is silently updated; different → break.
- Before every deployment (also schedule, kiosk, repeat) directly on the share. Break → the run does not start
  (IntegrityError) – "interrupt + warning" (operator's decision, 24.10.2026).
- Full check: every file once per week, at night within the time window, throttled (Settings → Repository & winget).

Levels: broken (red: file/folder missing, installation file or source of a file copy changed) – the run is
interrupted; warn (yellow: other files in the package folder changed or added, check still running) – the run continues,
note in the log; unknown (grey: repository not reachable) – no break; ok; "" (package without
repository files, e.g. winget).
"""
import bisect
import datetime
import json
import threading
import time

from . import db, pklog, remote, util

TOL = 2                # seconds of deviation in the modification date (time resolution of file systems)
MAX_ISSUES = 60        # deviations stored per package
LIVE_EVERY = 30 * 60   # without index: how often to check directly on the share (seconds)
PRECHECK_HASH_SECONDS = 120  # before a run, compute hashes for at most this long
FULL_DAYS = 7          # full check: every file after this many days at the latest

_q_lock = threading.Lock()
_hash_queue = {}       # key → None (dict = ordered set): compute the hash as soon as the worker has time
_state = {"live_at": 0.0, "worker": False, "night_live": "", "moves": 0}


def moved():
    """Repository is being reorganized (repofiles: move/rename/repair) – running full checks
    discard their state (they may have read the packages with the old paths)."""
    with _q_lock:
        _state["moves"] += 1
_locks_lock = threading.Lock()
_pkg_locks = {}        # one lock per package (check/adopt) – never a global one that holds up all starts
_check_times = {}      # (user, package) → last "Jetzt prüfen" request (throttle)


class IntegrityError(ValueError):
    """Break – the run is interrupted. issues: list of deviations (for display per user)."""

    def __init__(self, msg, name="", issues=None):
        super().__init__(msg)
        self.name = name
        self.issues = issues or []


def norm(rel):
    return "\\".join(x for x in str(rel or "").replace("/", "\\").split("\\") if x not in ("", "."))


def key(rel):
    return norm(rel).lower()


# --- Which repository paths does a package use? ---------------------------------------------------------
def refs(p, _depth=0):
    """[(path, kind, main)] – kind "dir" (whole folder with subfolders), "file" or "auto" (file copy: folder or
    file). main = installation file or source of a file copy (a change to it is a break).
    Disabled steps do not count; nested packages are checked too."""
    from . import deploy, steps
    out = {}
    if _depth > steps.MAX_DEPTH + 1 or not p:
        return []
    try:
        p = steps.normalize(dict(p))
    except Exception:
        return []

    def add(rel, kind, main):
        k = key(rel)
        if not k:
            return
        if k in out:
            r, kd, m = out[k]
            out[k] = (r, kd if kd != "auto" else kind, m or main)
        else:
            out[k] = (norm(rel), kind, main)
    for _ph, st in steps.iter_steps(p):
        if not isinstance(st, dict) or not st.get("enabled", True):
            continue
        t = st.get("type")
        if t == "install" and st.get("source") == "file":
            folder = norm(st.get("folder"))
            f = (st.get("install_file") or "").strip()
            if st.get("copy_folder", True) and folder:
                add(folder, "dir", False)
            if f:
                add(f"{folder}\\{f}" if folder else f, "file", True)
        elif t == "filecopy" and st.get("source"):
            add(st["source"], "auto", True)
        elif t == "nested":
            sub = st.get("package")
            if not sub and st.get("package_id"):
                row = db.q1("SELECT * FROM packages WHERE id=?", (st["package_id"],))
                sub = deploy.row_to_package(row) if row else None
            for r, kd, m in refs(sub, _depth + 1):
                add(r, kd, m)
    return list(out.values())


# --- Current state in the repository ------------------------------------------------------------------------
class _Snap:
    """Files in the repository: from the index in memory (fast) or directly on the share (live)."""

    def __init__(self, live):
        self.live = live
        self.idx = None if live else remote.repo_index_files()
        if self.idx:
            self.files, self.dirs = self.idx
            self.keys = sorted(self.files)

    def ok(self):
        return self.live or self.idx is not None

    def kind(self, rel):
        if self.live:
            return remote.repo_stat(rel)
        k = key(rel)
        return "file" if k in self.files else ("dir" if k in self.dirs else None)

    def file(self, rel):
        if self.live:
            r = remote.repo_fstat(rel)
            return (norm(rel), r[0], r[1]) if r else None
        return self.files.get(key(rel))

    def tree(self, rel):
        """{key: (path, size, mtime)} of all files under the folder – None if it does not exist."""
        if self.live:
            lst = remote.repo_tree(rel)
            return None if lst is None else {key(x[0]): (norm(x[0]), x[1], x[2]) for x in lst}
        k = key(rel)
        if k not in self.dirs:
            return None
        pre = k + "\\"
        out = {}
        i = bisect.bisect_left(self.keys, pre)
        while i < len(self.keys) and self.keys[i].startswith(pre):
            out[self.keys[i]] = self.files[self.keys[i]]
            i += 1
        return out


def _rows(keys):
    out = {}
    keys = list(keys)
    for i in range(0, len(keys), 400):
        part = keys[i:i + 400]
        for r in db.q(f"SELECT * FROM repo_baseline WHERE path IN ({','.join('?' * len(part))})", part):
            out[r["path"]] = r
    return out


def _rows_under(dkey):
    # Range instead of LIKE: all keys that start with "folder\" (']' follows '\' directly in ASCII)
    return {r["path"]: r for r in db.q("SELECT * FROM repo_baseline WHERE path >= ? AND path < ?", (dkey + "\\", dkey + "]"))}


def _pkg_lock(pid):
    with _locks_lock:
        return _pkg_locks.setdefault(pid, threading.Lock())


def _adopt(k, cur, who, queue=False):
    """Record the state of a file for the first time. The nightly full check computes the hash for it (full_at empty
    → first) – only when saving a package (queue=True) immediately, throttled in the background."""
    db.ex("""INSERT OR IGNORE INTO repo_baseline(path, disp, size, mtime, sha256, confirmed_at, confirmed_by, cur_size, cur_mtime)
             VALUES(?,?,?,?,NULL,?,?,?,?)""", (k, cur[0], cur[1], cur[2], util.now(), who, cur[1], cur[2]))
    if queue:
        enqueue(k)


def enqueue(k):
    with _q_lock:
        _hash_queue.setdefault(k, None)
    _ensure_worker()


def _ensure_worker():
    """Exactly ONE thread computes hashes (one file after another, shared throttle)."""
    with _q_lock:
        if _state["worker"] or not _hash_queue:
            return
        _state["worker"] = True

    def run():
        try:
            process_queue(seconds=None)
        except Exception as e:  # pragma: no cover
            pklog.warn("deploy", f"Fingerabdrücke der Repository-Dateien: {e}")
        finally:
            with _q_lock:
                _state["worker"] = False
            with _q_lock:
                again = bool(_hash_queue)
            if again:
                _ensure_worker()
    threading.Thread(target=run, name="pk-integrity-hash", daemon=True).start()


# --- Checking -----------------------------------------------------------------------------------------------
def _issue(kind, path, main, level, old=None, new=None, note=""):
    return {"kind": kind, "path": path, "main": bool(main), "level": level, "old": old or {}, "new": new or {}, "note": note}


def _foreign_folder(path, tenant_id):
    """Is path in the repository folder of a tenant that the package does not belong to?"""
    k = key(path)
    own = int(tenant_id or 0)
    for t in db.q("SELECT id, repo_folder FROM tenants WHERE COALESCE(repo_folder, '') != ''"):
        f = key(t["repo_folder"])
        if f and (k == f or k.startswith(f + "\\")) and t["id"] != own:
            return True
    return False


def _sig(size, mtime, sha=None):
    return {"size": size, "mtime": util.fmt(datetime.datetime.fromtimestamp(mtime)) if mtime else None, "sha256": sha}


def _repo_errors():
    """Error types when accessing the repository (smbprotocol errors are not OSError)."""
    try:
        from .clientfiles import _smb_errors
        return (remote.RemoteError,) + tuple(_smb_errors())
    except Exception:  # pragma: no cover
        return (remote.RemoteError, OSError)


def evaluate(p, live=False, adopt=True, hash_now=False, deadline=None, who="automatisch", snap=None, queue_new=False):
    """Check a package against the confirmed state. Result: {state, issues, at, live, keys, total}.
    adopt: record files without a confirmed state for the first time (new packages, packages from before 2.26).
    hash_now: resolve changed modification dates immediately via hash (before a run), otherwise in the background.
    keys: all paths whose state this package needs (also missing ones) – for cleanup.
    An error while accessing affects only that one path ("nicht prüfbar"); a reliably detected break remains a break."""
    rs = refs(p)
    res = {"state": "", "issues": [], "at": util.now(), "live": live, "keys": [], "total": 0}
    if not rs:
        return res
    snap = snap or _Snap(live)
    if not snap.ok():
        res.update(state="unknown", issues=[_issue("unreachable", "", False, "unknown", note="Repository-Index noch nicht eingelesen")])
        return res
    errs = _repo_errors()
    issues, keep = [], set()
    expected = {}  # key → (path, main)
    current = {}
    for rel, kind, main in rs:
        k = key(rel)
        try:
            if kind == "auto":
                kind = snap.kind(rel)
                if kind is None:
                    keep.add(k)
                    keep.update(_rows_under(k))
                    issues.append(_issue("missing", rel, main, "broken", note="Quelle der Dateikopie fehlt"))
                    continue
            if kind == "dir":
                cur = snap.tree(rel)
                if cur is None:
                    keep.update(_rows_under(k))  # keep the state – if the folder comes back changed, that is a break
                    issues.append(_issue("missing", rel, main, "broken", note="Ordner fehlt"))
                    continue
                current.update(cur)
                base = _rows_under(k)
                confirmed = db.val("SELECT 1 FROM repo_baseline_dirs WHERE path=?", (k,))
                if not confirmed and adopt:
                    for ck, c in cur.items():
                        if ck not in base:
                            _adopt(ck, c, who, queue_new)
                    db.ex("INSERT OR IGNORE INTO repo_baseline_dirs(path, confirmed_at) VALUES(?,?)", (k, util.now()))
                    base, confirmed = _rows_under(k), 1
                for bk, b in base.items():
                    prev = expected.get(bk)
                    # source of a file copy: every file in the folder counts like the installation file
                    expected[bk] = (b["disp"] or bk, main or (prev[1] if prev else False))
                if confirmed:
                    for ck, c in cur.items():
                        if ck not in base:
                            # Placed by someone else in a tenant folder (2.27): break – otherwise a
                            # tenant could e.g. put a DLL next to the installation file of a central package
                            foreign = _foreign_folder(c[0], p.get("tenant_id"))
                            issues.append(_issue("added", c[0], foreign, "broken" if foreign else "warn", new=_sig(c[1], c[2]),
                                                 note="neu im Paketordner – liegt im Ordner eines anderen Mandanten" if foreign
                                                 else "neu im Paketordner"))
            else:
                c = snap.file(rel)
                if c:
                    current[k] = c
                    if adopt and not db.val("SELECT 1 FROM repo_baseline WHERE path=?", (k,)):
                        _adopt(k, c, who, queue_new)
                prev = expected.get(k)
                expected[k] = (rel, main or (prev[1] if prev else False))
        except errs as e:
            keep.add(k)
            keep.update(_rows_under(k))
            issues.append(_issue("unreachable", rel, main, "unknown", note=f"nicht prüfbar: {str(e)[:200]}"))
    base = _rows(expected)
    t_end = deadline or (time.time() + PRECHECK_HASH_SECONDS)
    for k, (disp, main) in expected.items():
        b, c = base.get(k), current.get(k)
        lvl = "broken" if main else "warn"
        if c is None:
            issues.append(_issue("missing", disp, main, "broken",
                                 old=_sig(b["size"], b["mtime"], b["sha256"]) if b else None, note="Datei fehlt"))
            continue
        if b is None:
            issues.append(_issue("unconfirmed", c[0], main, "warn", new=_sig(c[1], c[2]), note="noch kein bestätigter Stand"))
            continue
        same = c[1] == b["size"] and abs((c[2] or 0) - (b["mtime"] or 0)) <= TOL
        if same:
            # did the full check find a different hash with the same size/date?
            if (b["cur_sha256"] and b["sha256"] and b["cur_sha256"] != b["sha256"] and b["cur_size"] == c[1]
                    and abs((b["cur_mtime"] or 0) - (c[2] or 0)) <= TOL):
                issues.append(_issue("changed", c[0], main, lvl, old=_sig(b["size"], b["mtime"], b["sha256"]),
                                     new=_sig(c[1], c[2], b["cur_sha256"]), note="Inhalt ausgetauscht (gleiche Größe und gleiches Datum)"))
            continue
        if c[1] != b["size"]:
            issues.append(_issue("changed", c[0], main, lvl, old=_sig(b["size"], b["mtime"], b["sha256"]),
                                 new=_sig(c[1], c[2]), note="Größe geändert"))
            continue
        # Only the date is different: the hash decides
        sha = None
        if b["cur_sha256"] and b["cur_size"] == c[1] and abs((b["cur_mtime"] or 0) - (c[2] or 0)) <= TOL:
            sha = b["cur_sha256"]
        elif hash_now and b["sha256"] and time.time() < t_end:
            try:
                sha = remote.repo_sha256(c[0], deadline=t_end)
            except errs:
                sha = None
            if sha:
                db.ex("UPDATE repo_baseline SET cur_size=?, cur_mtime=?, cur_sha256=?, cur_at=? WHERE path=?",
                      (c[1], c[2], sha, util.now(), k))
        if not b["sha256"]:
            # No hash to compare yet (e.g. shortly after the first adoption): same size, only the date
            # differs – not a certain break; the full check or the background job catches up on the comparison
            issues.append(_issue("unverified", c[0], main, "warn", old=_sig(b["size"], b["mtime"]), new=_sig(c[1], c[2], sha),
                                 note="Änderungsdatum anders – kein früherer Fingerabdruck zum Vergleich (bitte prüfen und übernehmen)"))
            continue
        if sha and sha == b["sha256"]:
            # merely touched (e.g. copied with a new date) – silently update the state
            db.ex("UPDATE repo_baseline SET size=?, mtime=? WHERE path=?", (c[1], c[2], k))
            continue
        if sha is None:
            enqueue(k)
            issues.append(_issue("pending", c[0], main, "warn", old=_sig(b["size"], b["mtime"]), new=_sig(c[1], c[2]),
                                 note="Änderungsdatum anders – Fingerabdruck wird geprüft"))
            continue
        issues.append(_issue("changed", c[0], main, lvl, old=_sig(b["size"], b["mtime"], b["sha256"]),
                             new=_sig(c[1], c[2], sha), note="Inhalt geändert"))
    order = {"broken": 0, "warn": 1, "unknown": 2}
    issues.sort(key=lambda x: (order.get(x["level"], 3), not x["main"], x["path"].lower()))
    levels = {x["level"] for x in issues}
    state = "broken" if "broken" in levels else ("warn" if "warn" in levels else ("unknown" if "unknown" in levels else "ok"))
    res.update(state=state, issues=issues[:MAX_ISSUES], total=len(issues), keys=sorted(set(expected) | keep))
    return res


def summary(res, limit=4):
    """Short text of the deviations ("7zip\\7z.msi: Inhalt geändert; …")."""
    parts = [f"{x['path'] or 'Repository'}: {x['note'] or x['kind']}" for x in res.get("issues") or [] if x["level"] != "unknown"]
    more = (res.get("total") or len(parts)) - min(len(parts), limit)
    return "; ".join(parts[:limit]) + (f" (+{more} weitere)" if more > 0 else "")


def public(res):
    """Only the fields for the UI (never the list of all paths of a package)."""
    if not res:
        return res
    return {k: res.get(k) for k in ("state", "issues", "at", "total")}


def store(pid, res):
    """Store the result on the package; report new breaks once in the log."""
    row = db.q1("SELECT name, integrity FROM packages WHERE id=?", (pid,))
    if not row:
        return
    try:
        old = json.loads(row["integrity"] or "{}")
    except ValueError:
        old = {}
    db.ex("UPDATE packages SET integrity=? WHERE id=?", (json.dumps(public(res), ensure_ascii=False), pid))
    if res["state"] in ("broken", "warn") and (old.get("state") != res["state"] or summary(old).lower() != summary(res).lower()):
        fn = pklog.warn if res["state"] == "broken" else pklog.info
        fn("deploy", f"Paket „{row['name']}“: {'Bruch' if res['state'] == 'broken' else 'Abweichung'} im Repository – "
                     f"{summary(res)}.")
    elif res["state"] == "ok" and old.get("state") in ("broken", "warn"):
        pklog.info("deploy", f"Paket „{row['name']}“: Repository-Dateien wieder in Ordnung.")


def check_package(pid, live=True, who="automatisch", queue_new=False):
    from . import deploy
    row = db.q1("SELECT * FROM packages WHERE id=?", (pid,))
    if not row:
        return None
    with _pkg_lock(pid):
        res = evaluate(deploy.row_to_package(row), live=live, adopt=True, hash_now=live, who=who, queue_new=queue_new)
        store(pid, res)
    return res


def throttle_check(user, pid, seconds=10):
    """"Jetzt prüfen" at most every 10 seconds per user and package (reads on the share)."""
    k = (str(user), int(pid))
    now = time.time()
    if now - _check_times.get(k, 0) < seconds:
        return False
    _check_times[k] = now
    if len(_check_times) > 5000:
        _check_times.clear()
    return True


def check_all(live=None):
    """Check all packages (background). With index: breaks and deviations are confirmed directly on the
    share before saving (a stale index would otherwise cause false alarms). Cleanup only happens on a direct check."""
    from . import deploy
    if not (db.setting("repo_path") or "").strip():
        return
    if live is None:
        live = remote.repo_index_files() is None
        if live:
            if time.time() - _state["live_at"] < LIVE_EVERY:
                return
            _state["live_at"] = time.time()
    try:
        snap = _Snap(live) if not live else None
    except Exception:
        snap = None
    if not live and (snap is None or not snap.ok()):
        return
    used, t0 = set(), util.now()
    m0 = _state["moves"]
    for row in db.q("SELECT * FROM packages"):
        pkg = deploy.row_to_package(row)
        with _pkg_lock(row["id"]):
            res = evaluate(pkg, live=live, adopt=live, snap=snap)
            if not live and res["state"] in ("broken", "warn"):
                res = evaluate(pkg, live=True, adopt=True)
            if _state["moves"] != m0:
                return  # reorganized in the meantime: store nothing stale, clean up nothing (the next run checks again)
            used.update(res.get("keys") or [])
            store(row["id"], res)
    if live and _state["moves"] == m0:
        _prune(used, t0)


def _prune(used, before):
    """Remove states of files that no package needs any more (table stays small). Only those older than the start of the
    pass – a package that was just saved may only have recorded its state in the meantime."""
    # Audit 2.29: also keep files that only stored deployments still need (scheduled, "Erneut verteilen",
    # "Schritt wiederholen") – otherwise a file replaced afterwards would be adopted as the new state on the next run
    snap_files, snap_dirs = _snapshot_refs()
    for r in db.q("SELECT path FROM repo_baseline WHERE confirmed_at < ?", (before,)):
        p = r["path"]
        if p not in used and p not in snap_files and not any(p.startswith(d + "\\") for d in snap_dirs):
            db.ex("DELETE FROM repo_baseline WHERE path=?", (p,))
    dirs = set(snap_dirs)
    from . import deploy
    for row in db.q("SELECT * FROM packages"):
        for rel, kind, _m in refs(deploy.row_to_package(row)):
            if kind in ("dir", "auto"):
                dirs.add(key(rel))
    for r in db.q("SELECT path FROM repo_baseline_dirs WHERE confirmed_at < ?", (before,)):
        if r["path"] not in dirs:
            db.ex("DELETE FROM repo_baseline_dirs WHERE path=?", (r["path"],))


def _snapshot_refs():
    """Repository files and folders from the package states of stored deployments."""
    files, dirs = set(), set()
    for d in db.q("SELECT DISTINCT package_json FROM deployments WHERE package_json IS NOT NULL"):
        try:
            pkg = json.loads(d["package_json"] or "{}")
            for rel, kind, _m in refs(pkg):
                k = key(rel)
                if k:
                    if kind in ("dir", "auto"):
                        dirs.add(k)
                    if kind != "dir":
                        files.add(k)
        except Exception:  # noqa: BLE001 – a broken old state must not prevent the cleanup
            continue
    return files, dirs


def process_queue(seconds=240):
    """Compute hashes from the queue (throttled, one file after another). A file that has been started
    is computed to the end – so even large files get their hash. seconds=None: until the queue is empty."""
    mbps = max(5, int(db.setting("integrity_full_mbps") or 50))
    t_end = time.time() + seconds if seconds else None
    errs = _repo_errors()
    done = 0
    while not t_end or time.time() < t_end:
        with _q_lock:
            if not _hash_queue:
                break
            k = next(iter(_hash_queue))
            del _hash_queue[k]
        b = db.q1("SELECT * FROM repo_baseline WHERE path=?", (k,))
        if not b:
            continue
        try:
            st = remote.repo_fstat(b["disp"])
            if st is None:
                continue
            sha = remote.repo_sha256(b["disp"], mbps=mbps)
        except errs:
            continue
        if sha:
            _record_hash(b, st, sha)
            done += 1
    return done


def _record_hash(b, st, sha):
    now = util.now()
    db.ex("UPDATE repo_baseline SET cur_size=?, cur_mtime=?, cur_sha256=?, cur_at=?, full_at=? WHERE path=?",
          (st[0], st[1], sha, now, now, b["path"]))
    if not b["sha256"] and st[0] == b["size"] and abs((st[1] or 0) - (b["mtime"] or 0)) <= TOL:
        db.ex("UPDATE repo_baseline SET sha256=? WHERE path=? AND sha256 IS NULL", (sha, b["path"]))  # first hash
    elif b["sha256"] and sha == b["sha256"] and st[0] == b["size"]:
        db.ex("UPDATE repo_baseline SET mtime=? WHERE path=?", (st[1], b["path"]))  # merely touched


def _hours():
    def h(k, d):
        try:
            v = int(db.setting(k))
        except (TypeError, ValueError):
            v = d
        return max(0, min(24, v))
    return h("integrity_full_start", 1), h("integrity_full_end", 6)


def in_window(now=None):
    """Time window of the full check (hours, end exclusive; across midnight possible, e.g. 22–5)."""
    now = now or datetime.datetime.now()
    s, e = _hours()
    h = now.hour
    if s == e:
        return False
    return (s <= h < e) if s < e else (h >= s or h < e)


def _window_end(now=None):
    now = now or datetime.datetime.now()
    _s, e = _hours()
    end = now.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1)
    while end.hour != e % 24:
        end += datetime.timedelta(hours=1)
    return end.timestamp()


def full_check():
    """Full check: hash of every file whose last check was more than a week ago (new ones first) – at night within the
    time window, throttled, one file after another. If the time window ends, it continues the next night.
    Once per night additionally a direct check of all packages (also cleans up states no longer used)."""
    if not db.setting("integrity_full_enabled") or not in_window():
        return 0
    night = datetime.date.today().isoformat()
    if _state["night_live"] != night:
        _state["night_live"] = night
        check_all(live=True)
    mbps = max(5, int(db.setting("integrity_full_mbps") or 50))
    limit = util.fmt(datetime.datetime.now() - datetime.timedelta(days=FULL_DAYS))
    t_end = _window_end()
    n = 0
    for b in db.q("SELECT * FROM repo_baseline WHERE full_at IS NULL OR full_at < ? ORDER BY full_at IS NOT NULL, full_at", (limit,)):
        if time.time() >= t_end or not in_window():
            break
        try:
            st = remote.repo_fstat(b["disp"])   # error of the whole repository → RemoteError → continue next night
        except remote.RemoteError:
            break
        if st is None:
            db.ex("UPDATE repo_baseline SET full_at=? WHERE path=?", (util.now(), b["path"]))
            continue
        try:
            sha = remote.repo_sha256(b["disp"], mbps=mbps, deadline=t_end, cancel=lambda: not in_window())
        except remote.RemoteError:
            break
        except Exception:
            # single file not readable (locked, permissions) – do not get stuck on it every night
            db.ex("UPDATE repo_baseline SET full_at=? WHERE path=?", (util.now(), b["path"]))
            continue
        if sha is None:
            break
        _record_hash(b, st, sha)
        n += 1
    if n:
        pklog.info("deploy", f"Vollprüfung der Repository-Dateien: {n} Fingerabdruck/Fingerabdrücke geprüft.")
        check_all(live=remote.repo_index_files() is None)
    return n


def background():
    """Every 5 minutes (scheduler): refresh the index (for the quick check), then check all packages."""
    remote.repo_cache_refresh()
    check_all()


# --- Before a run -----------------------------------------------------------------------------------------
NEUTRAL = "Unterbrochen – Dateien des Pakets im Repository haben sich geändert (Details im Paket, Spalte „Zustand“)."


def guard(pkg, package_id=None, name=None):
    """Before a deployment: check directly on the share. Break → IntegrityError (the run does not start).
    Returns the result (deviations of level "warn" allow the run). The result is NOT stored on the
    package – pkg may be a subset (repeating individual steps) or an old state."""
    res = evaluate(pkg, live=True, adopt=True, hash_now=True)
    nm = name or pkg.get("name") or "?"
    # Audit 2.29: main file with a different modification date whose hash was not finished before the start → do not
    # simply let it run (same-size, swapped file); the hash is finished in the background
    # Only the installation file itself (not every file of a copied folder – a freshly copied folder would otherwise have
    # blocked every run)
    inst = {key(r) for r, kd, m in refs(pkg) if kd == "file" and m}
    for x in res.get("issues") or []:
        if x["kind"] == "pending" and x["main"] and key(x["path"]) in inst:
            x["level"] = "broken"
            x["note"] = ("Änderungsdatum der Installationsdatei anders – Fingerabdruck noch nicht fertig (wird im Hintergrund "
                         "berechnet); danach erneut starten")
            res["state"] = "broken"
        elif x["kind"] == "unreachable" and x["main"] and key(x["path"]) in inst:
            # 2.37: installation file not readable → do not let it run unchecked (previously "unbekannt" = run allowed)
            x["level"] = "broken"
            x["note"] = "Installationsdatei nicht lesbar – nicht geprüft; " + (x.get("note") or "")
            res["state"] = "broken"
    if res["state"] == "broken":
        bad = {"issues": [x for x in res["issues"] if x["level"] == "broken"], "total": None}
        raise IntegrityError(f"Unterbrochen – Paket „{nm}“: Dateien im Repository haben sich geändert ({summary(bad)}). "
                             "Bitte prüfen und im Paket „Neuen Stand übernehmen“ oder das Paket anpassen.", nm, res["issues"])
    return res


def public_message(err, prefixes):
    """Message for an OU admin: details only for files in their tenant folders, otherwise generic."""
    own = [x for x in err.issues if x["level"] == "broken" and _inside(x["path"], prefixes)]
    other = [x for x in err.issues if x["level"] == "broken" and not _inside(x["path"], prefixes)]
    if other or not own:
        return (f"Unterbrochen – Paket „{err.name}“ ist derzeit nicht verfügbar (Dateien im Repository geändert). "
                "Bitte einen Hauptadmin informieren.")
    return str(err)


def _inside(path, prefixes):
    k = key(path)
    return bool(k) and any(k == p or k.startswith(p + "\\") for p in prefixes or [])


def scope_view(res, prefixes):
    """Result for the UI; for an OU admin (prefixes = their tenant folders) without paths and details
    outside those folders."""
    res = public(res)
    if prefixes is None or not res:
        return res
    out = dict(res)
    iss = []
    hidden = 0
    for x in res.get("issues") or []:
        if _inside(x.get("path") or "", prefixes):
            iss.append(x)
        else:
            hidden += 1
    if hidden:
        iss.append(_issue("hidden", "", False, "broken" if res.get("state") == "broken" else "warn",
                          note=f"{hidden} Abweichung(en) in zentralen Dateien – bitte einen Hauptadmin informieren"))
    out["issues"] = iss
    return out


# --- Adopting a new state ---------------------------------------------------------------------------------
def accept(p, user, prefixes=None, pid=None):
    """Adopt the current content of a package's repository files as the confirmed state (deliberate change).
    prefixes: OU admin – only files in their tenant folders. The hash is recomputed afterwards (an
    older one is never adopted). Returns {count, msi}."""
    rs = refs(p)
    snap = _Snap(True)
    now = util.now()
    n = 0
    msi_info = None
    lk = _pkg_lock(pid) if pid else threading.Lock()
    with lk:
        for rel, kind, main in rs:
            if prefixes is not None and not _inside(rel, prefixes):
                continue
            k = key(rel)
            if kind == "auto":
                kind = snap.kind(rel)
                if kind is None:
                    continue
            if kind == "dir":
                cur = snap.tree(rel)
                if cur is None:
                    continue
                for bk in _rows_under(k):
                    if bk not in cur:
                        db.ex("DELETE FROM repo_baseline WHERE path=?", (bk,))
                        n += 1
                for ck, c in cur.items():
                    n += _confirm(ck, c, user, now)
                db.ex("INSERT OR REPLACE INTO repo_baseline_dirs(path, confirmed_at) VALUES(?,?)", (k, now))
            else:
                c = snap.file(rel)
                if c:
                    n += _confirm(k, c, user, now)
                    if main and c[0].lower().endswith(".msi"):
                        try:
                            from . import msi
                            with remote.repo_open_rel(c[0]) as fh:
                                info = msi.read(fh)
                            msi_info = {"file": c[0], "version": info.get("version"), "product_code": info.get("product_code"),
                                        "name": info.get("name")}
                        except Exception:
                            pass
    return {"count": n, "msi": msi_info}


def _confirm(k, c, user, now):
    b = db.q1("SELECT * FROM repo_baseline WHERE path=?", (k,))
    if b and b["size"] == c[1] and abs((b["mtime"] or 0) - (c[2] or 0)) <= TOL and not (
            b["cur_sha256"] and b["sha256"] and b["cur_sha256"] != b["sha256"]):
        return 0
    # Always have the hash recomputed – a stored one could belong to content that has since been swapped again
    db.ex("""INSERT INTO repo_baseline(path, disp, size, mtime, sha256, confirmed_at, confirmed_by, cur_size, cur_mtime, cur_sha256, full_at)
             VALUES(?,?,?,?,NULL,?,?,?,?,NULL,NULL) ON CONFLICT(path) DO UPDATE SET disp=excluded.disp, size=excluded.size,
             mtime=excluded.mtime, sha256=NULL, confirmed_at=excluded.confirmed_at, confirmed_by=excluded.confirmed_by,
             cur_size=excluded.cur_size, cur_mtime=excluded.cur_mtime, cur_sha256=NULL, full_at=NULL""",
          (k, c[0], c[1], c[2], now, user, c[1], c[2]))
    enqueue(k)
    return 1


def after_save(pid):
    """After saving a package: record the state of new files and check them (in the background); the hashes
    of the newly recorded files are computed by the single background worker, throttled."""
    def run():
        try:
            check_package(pid, live=True, queue_new=True)
        except Exception as e:  # pragma: no cover
            pklog.warn("deploy", f"Prüfung des Pakets #{pid} fehlgeschlagen: {e}")
    threading.Thread(target=run, name=f"pk-integrity-{pid}", daemon=True).start()
