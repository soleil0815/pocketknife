"""2.36: Freigabe neuer winget-Versionen (Zurückstellung).

Der winget-Katalog nennt kein Erscheinungsdatum – POCKET KNIFE merkt sich, wann es eine Version zum ersten Mal sieht
(„first_seen“). Einstellung „winget_release_mode“:
  off    – neue Versionen sofort (wie bisher); gesperrte Versionen bleiben gesperrt
  days   – neue Versionen erst nach „winget_release_days“ Tagen, einzeln vorzeitig freigebbar
  manual – neue Versionen nur nach Freigabe im Paket

Wirkung (target): ist die neueste Version freigegeben, ändert sich nichts. Sonst bekommen winget-Schritte in Paketen,
Kiosk und Zeitplänen gezielt die zuletzt freigegebene Version (winget --version), der winget-Cache lädt nur diese, und
Zeitpläne „Software aktuell halten“ aktualisieren nur Computer mit einer älteren Version. Gibt es keine freigegebene
Version (alles gesperrt), passiert nichts. Der Knopf „Aktualisieren“ (Software) installiert bewusst die neueste.

Beim ersten Kontakt mit einer winget-ID gilt deren aktuelle Version als freigegeben („baseline“) – sonst stünden nach
dem Einschalten alle Programme still.
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
    """winget-IDs, deren Versionen verfolgt werden: Pakete (auch verschachtelte winget-Schritte), sicher zugeordnete
    Software und der winget-Cache."""
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
    # base: Ausgangsstand (erste Sichtung der ID) – bleibt freigegeben, auch nach „Sperre aufheben“
    db.ex("INSERT OR IGNORE INTO winget_releases(winget_id, version, first_seen, status, base) VALUES(?,?,?,NULL,?)",
          (wid, ver, util.now(), 1 if base else 0))


def sync():
    """Nach dem Laden des Index: neue Katalogversionen der verfolgten IDs vermerken (erste Sichtung)."""
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
    """Zeitpunkt der automatischen Freigabe (Text) oder None (manuell/gesperrt/schon frei)."""
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
    """{"state": "latest"} – neueste Version ist frei (nichts ändern); {"state": "pin", "version": v} – stattdessen die
    zuletzt freigegebene Version; {"state": "hold"} – nichts freigegeben. Unbekannte IDs: „latest“."""
    wid = str(wid or "").strip()
    latest = _latest(wid) if wid else None
    if not latest:
        return {"state": "latest"}
    rows = _rows(wid)
    if not rows:
        return {"state": "latest", "latest": latest}
    cur = next((r for r in rows if r["version"] == latest), None)
    if cur is None:
        # Index neuer als der letzte Abgleich: jetzt als erste Sichtung vermerken
        _insert(wid, latest)
        cur = next((r for r in _rows(wid) if r["version"] == latest), None)
    if cur is None or released(cur):
        return {"state": "latest", "latest": latest}
    ok = [r["version"] for r in rows if r["version"] != latest and released(r) and util.vcmp(r["version"], latest) < 0]
    if not ok:
        return {"state": "hold", "latest": latest, "reason": _why(cur)}
    best = ok[0]
    for v in ok[1:]:
        if util.vcmp(v, best) > 0:
            best = v
    return {"state": "pin", "version": best, "latest": latest, "reason": _why(cur)}


def _why(r):
    if r["status"] == "blocked":
        return f"Version {r['version']} gesperrt"
    ra = release_at(r)
    if ra:
        d = f"{ra[8:10]}.{ra[5:7]}.{ra[0:4]} {ra[11:16]}"
        return f"Version {r['version']} zurückgestellt bis {d}"
    return f"Version {r['version']} wartet auf Freigabe"


def apply_step(st, pkg=None):
    """winget-Schritt (Installieren/Aktualisieren ohne feste Version) → Schritt mit der freigegebenen Version.
    Rückgabe (schritt, None) oder (None, Meldung) wenn nichts freigegeben ist."""
    if st.get("type") != "install" or st.get("source") != "winget":
        return st, None
    if (st.get("winget_action") or "install") == "uninstall" or str(st.get("winget_version") or "").strip():
        return st, None
    if pkg is not None and pkg.get("release_bypass"):
        return st, None
    t = target(st.get("winget_id"))
    if t["state"] == "pin":
        return {**st, "winget_version": t["version"], "release_note": t["reason"]}, None
    if t["state"] == "hold":
        return None, f"{t['reason']} – keine freigegebene Version"
    return st, None


def version_ok(wid, version):
    """Darf diese Version verteilt werden? (winget-Cache) – unbekannte Versionen: ja."""
    r = db.q1("SELECT * FROM winget_releases WHERE winget_id=? AND version=?", (str(wid or ""), str(version or "")))
    return r is None or released(r)


def allowed_update(wid, installed):
    """Zeitpläne: darf auf diesem Computer aktualisiert werden? (installierte Version älter als die freigegebene)"""
    t = target(wid)
    if t["state"] == "latest":
        return True
    if t["state"] == "hold":
        return False
    return bool(installed) and util.vcmp(str(installed), t["version"]) < 0


def overview():
    """Für die Paketliste: je winget-ID (klein) der Stand der Freigabe."""
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
    """status: approved (freigeben), blocked (sperren), "" (zurück auf die Regel)."""
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
