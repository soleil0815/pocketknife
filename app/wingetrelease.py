"""2.36: Release of new winget versions (deferral).

The winget catalog gives no release date - POCKET KNIFE remembers when it sees a version for the first time
("first_seen"). Setting "winget_release_mode":
  off    - new versions immediately (as before); blocked versions stay blocked
  days   - new versions only after "winget_release_days" days, individually releasable early
  manual - new versions only after approval in the package

Effect (target): if the newest version is released, nothing changes. Otherwise winget steps in packages,
kiosk and schedules specifically get the most recently released version (winget --version), the winget cache loads only that one, and
schedules "Software aktuell halten" update only computers with an older version. If there is no released
version (everything blocked), nothing happens. The "Aktualisieren" button (Software) deliberately installs the newest - unless
it is blocked (2.36.2: a block applies everywhere).

2.36.2: When the rule is changed (freeze), the versions released until then stay released - otherwise e.g. the
switch from "sofort" to "nur nach Freigabe" would put all programs back to the initial state of the first sighting.

On first contact with a winget ID, its current version counts as released ("baseline") - otherwise all programs
would stand still after switching on.
"""
import datetime
import threading

from . import db, pklog, util

MODES = {"off": "Sofort freigeben", "days": "Zurückstellen (Tage)", "manual": "Nur nach Freigabe"}
_lock = threading.Lock()


def mode():
    m = db.setting("winget_release_mode") or "off"
    return m if m in MODES else "off"


def days():
    try:
        return max(0, int(db.setting("winget_release_days") or 0))
    except (TypeError, ValueError):
        return 0


def _latest(wid):
    from . import winget
    pk = winget.package(wid)
    return str(pk["latest"]) if pk and pk.get("latest") else None


def tracked_ids():
    """winget IDs whose versions are tracked: packages (also nested winget steps), safely matched
    software and the winget cache."""
    from . import steps
    ids = {}
    for r in db.q("SELECT winget_id FROM software WHERE winget_id IS NOT NULL AND COALESCE(winget_sure, 1) = 1 "
                  "UNION SELECT winget_id FROM winget_cache UNION SELECT winget_id FROM packages WHERE winget_id IS NOT NULL"):
        if r["winget_id"]:
            ids.setdefault(r["winget_id"].lower(), r["winget_id"])
    for r in db.q("SELECT * FROM packages WHERE steps LIKE '%winget%'"):
        try:
            p = steps.normalize(dict(r))
        except Exception:
            continue
        for _ph, st in steps.iter_steps(p):
            w = st.get("winget_id") if st.get("source") == "winget" else None
            if w:
                ids.setdefault(str(w).lower(), str(w))
    return list(ids.values())


def _rows(wid):
    return db.q("SELECT * FROM winget_releases WHERE winget_id=? ORDER BY first_seen, id", (wid,))


def _insert(wid, ver, base=False):
    # base: initial state (first sighting of the ID) - stays released, even after "lift block"
    db.ex("INSERT OR IGNORE INTO winget_releases(winget_id, version, first_seen, status, base) VALUES(?,?,?,NULL,?)",
          (wid, ver, util.now(), 1 if base else 0))


def sync():
    """After loading the index: note new catalog versions of the tracked IDs (first sighting)."""
    if not _lock.acquire(blocking=False):
        return 0
    n = 0
    try:
        known = {r["wid"] for r in db.q("SELECT DISTINCT lower(winget_id) wid FROM winget_releases")}
        for wid in tracked_ids():
            ver = _latest(wid)
            if not ver:
                continue
            if wid.lower() not in known:
                _insert(wid, ver, base=True)
            elif not db.val("SELECT 1 FROM winget_releases WHERE winget_id=? AND version=?", (wid, ver)):
                _insert(wid, ver)
                n += 1
        return n
    finally:
        _lock.release()


def release_at(r):
    """Time of the automatic release (text) or None (manual/blocked/already free)."""
    if r["status"] or r["base"] or mode() != "days":
        return None
    try:
        t = datetime.datetime.strptime(r["first_seen"][:19], "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None
    return util.fmt(t + datetime.timedelta(days=days()))


def released(r):
    if r["status"] == "approved":
        return True
    if r["status"] == "blocked":
        return False
    if r["base"]:
        return True
    m = mode()
    if m == "off":
        return True
    if m == "days":
        ra = release_at(r)
        return bool(ra) and ra <= util.now()
    return False


def target(wid):
    """{"state": "latest"} - newest version is free (change nothing); {"state": "pin", "version": v} - instead the
    most recently released version; {"state": "hold"} - nothing released. Unknown IDs: "latest"."""
    wid = str(wid or "").strip()
    latest = _latest(wid) if wid else None
    if not latest:
        return {"state": "latest"}
    rows = _rows(wid)
    if not rows:
        return {"state": "latest", "latest": latest}
    cur = next((r for r in rows if r["version"] == latest), None)
    if cur is None:
        # Index newer than the last comparison: note it now as the first sighting
        _insert(wid, latest)
        cur = next((r for r in _rows(wid) if r["version"] == latest), None)
    if cur is None or released(cur):
        return {"state": "latest", "latest": latest}
    blocked = cur["status"] == "blocked"
    ok = [r["version"] for r in rows if r["version"] != latest and released(r) and util.vcmp(r["version"], latest) < 0]
    if not ok:
        return {"state": "hold", "latest": latest, "reason": _why(cur), "blocked": blocked}
    best = ok[0]
    for v in ok[1:]:
        if util.vcmp(v, best) > 0:
            best = v
    return {"state": "pin", "version": best, "latest": latest, "reason": _why(cur), "blocked": blocked}


def _why(r):
    if r["status"] == "blocked":
        return f"Version {r['version']} gesperrt"
    ra = release_at(r)
    if ra:
        d = f"{ra[8:10]}.{ra[5:7]}.{ra[0:4]} {ra[11:16]}"
        return f"Version {r['version']} zurückgestellt bis {d}"
    return f"Version {r['version']} wartet auf Freigabe"


def apply_step(st, pkg=None):
    """winget step (install/update without a fixed version) → step with the released version.
    Returns (step, None) or (None, message) if nothing is released."""
    if st.get("type") != "install" or st.get("source") != "winget":
        return st, None
    if (st.get("winget_action") or "install") == "uninstall" or str(st.get("winget_version") or "").strip():
        return st, None
    t = target(st.get("winget_id"))
    if pkg is not None and pkg.get("release_bypass") and not t.get("blocked"):
        return st, None  # "Aktualisieren" by hand: deferred versions yes, blocked ones never
    if t["state"] == "pin":
        if not util.WINGET_ID_RE.match(str(t["version"])):
            return None, f"{t['reason']} – freigegebene Version {str(t['version'])[:40]!r} ungültig"
        return {**st, "winget_version": t["version"], "release_note": t["reason"]}, None
    if t["state"] == "hold":
        return None, f"{t['reason']} – keine freigegebene Version"
    return st, None


def freeze(new_mode):
    """2.36.2: before the rule is changed - what is released now stays released (as the initial state).
    Blocked and still deferred versions stay as they are."""
    if new_mode not in MODES or new_mode == mode():
        return 0
    with _lock:
        ids = [r["id"] for r in db.q("SELECT * FROM winget_releases WHERE status IS NULL AND COALESCE(base, 0) = 0") if released(r)]
        for i in ids:
            db.ex("UPDATE winget_releases SET base=1 WHERE id=?", (i,))
    if ids:
        pklog.info("winget", f"winget-Freigabe: Regel {mode()} → {new_mode}, {len(ids)} bisher freigegebene Version(en) bleiben freigegeben.")
    return len(ids)


def version_ok(wid, version):
    """May this version be distributed? (winget cache) - unknown versions: yes."""
    r = db.q1("SELECT * FROM winget_releases WHERE winget_id=? AND version=?", (str(wid or ""), str(version or "")))
    return r is None or released(r)


def allowed_update(wid, installed):
    """Schedules: may this computer be updated? (installed version older than the released one)"""
    t = target(wid)
    if t["state"] == "latest":
        return True
    if t["state"] == "hold":
        return False
    return bool(installed) and util.vcmp(str(installed), t["version"]) < 0


def overview():
    """For the package list: the release state per winget ID (lower case)."""
    out = {}
    by = {}
    for r in db.q("SELECT * FROM winget_releases ORDER BY first_seen, id"):
        by.setdefault(r["winget_id"].lower(), []).append(r)
    for wl, rows in by.items():
        wid = rows[-1]["winget_id"]
        t = target(wid)
        latest = t.get("latest")
        vers = []
        for r in rows:
            vers.append({"version": r["version"], "first_seen": r["first_seen"],
                         "status": r["status"] or ("baseline" if r["base"] else ""),
                         "released": released(r), "release_at": release_at(r), "by": r["by_user"], "at": r["at"],
                         "latest": r["version"] == latest})
        out[wl] = {"winget_id": wid, "latest": latest, "state": t["state"], "version": t.get("version"),
                   "reason": t.get("reason"), "versions": vers[-20:]}
    return {"mode": mode(), "days": days(), "items": out}


def set_status(wid, version, status, user=None):
    """status: approved (release), blocked (block), "" (back to the rule)."""
    wid, version = str(wid or "").strip(), str(version or "").strip()
    if not wid or not version or len(wid) > 128 or len(version) > 64:
        raise ValueError("winget-ID und Version angeben.")
    if status not in ("approved", "blocked", ""):
        raise ValueError("Ungültiger Status.")
    if not db.val("SELECT 1 FROM winget_releases WHERE winget_id=? AND version=?", (wid, version)):
        if version != _latest(wid):
            raise ValueError("Diese Version ist nicht bekannt.")
        _insert(wid, version)
    db.ex("UPDATE winget_releases SET status=?, by_user=?, at=? WHERE winget_id=? AND version=?",
          (status or None, user, util.now() if status else None, wid, version))
    what = {"approved": "freigegeben", "blocked": "gesperrt", "": "zurück auf die Regel gesetzt"}[status]
    pklog.info("winget", f"winget-Freigabe: {wid} {version} {what}.", user=user)
    return target(wid)
