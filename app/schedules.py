"""Zeitpläne – automatische Softwareverteilung an einer Stelle.

Zwei Arten:
  packages  Pakete nacheinander an Ziele verteilen (Computer und Sammlungen)
  winget    veraltete Software der Ziele per „winget upgrade“ aktualisieren (früher „Regeln“)

Gemeinsam: Auslöser (einmalig, Intervall, täglich, wöchentlich, monatlich, Heartbeat), optionales
Zeitfenster (Wochentage + Uhrzeit, auch über Mitternacht), Umgang mit offline Computern (vormerken und beim
Heartbeat versorgen), Wartezeit nach Fehlschlag, maximale Fehlversuche und Zielverlauf je Computer.
"""
import datetime
import json
import threading

from . import db, pklog, profiles, util

TRIGGER_TYPES = {k: v for k, v in profiles.TRIGGER_TYPES.items() if k != "scan_age"}
KINDS = {"packages": "Pakete verteilen", "winget": "winget-Updates"}
DEFAULT_OPTIONS = {"stop_on_fail": True, "offline": "wait", "skip_successful": True, "max_attempts": 0,
                   "only_online": True, "retry_wait_hours": 0, "renew_after_hours": 0, "retry_failed": True,
                   "wake_offline": False}  # 2.31: ausgeschaltete Ziele vorher per Wake-on-LAN wecken (Standard: nein)
DEFAULT_WINGET = {"mode": "all", "ids": "", "exclude": "", "scope": ""}
DEFAULT_WINDOW = {"enabled": False, "days": [1, 2, 3, 4, 5, 6, 7], "start": "00:00", "end": "23:59"}
DEFERRED = "__window"  # Merker in schedule_trigger_state: Lauf fällig, wartet auf das Zeitfenster
RETRY = "__retry"  # Merker: Lauf konnte nicht gestartet werden (Fehler) – wird nachgeholt
RETRY_MINUTES = 15  # Abstand der Wiederholungen eines nicht gestarteten Laufs
AUTO_RETRY_MIN_HOURS = 1  # fehlgeschlagene Ziele frühestens nach dieser Zeit automatisch nachholen
AUTO_RETRY_DEFAULT_MAX = 3  # … höchstens so oft, wenn „Max. Fehlversuche“ 0 (unbegrenzt) ist


def _parse(row):
    if not row:
        return None
    s = dict(row)
    for k, default in (("triggers", []), ("targets", {}), ("packages", []), ("options", {}), ("winget", {}), ("window", {})):
        try:
            s[k] = json.loads(s.get(k) or "null") or default
        except ValueError:
            s[k] = default
    s["kind"] = s.get("kind") if s.get("kind") in KINDS else "packages"
    s["targets"] = {"computers": [int(i) for i in s["targets"].get("computers") or []],
                    "collections": [int(i) for i in s["targets"].get("collections") or []]}
    s["options"] = {**DEFAULT_OPTIONS, **s["options"]}
    s["winget"] = {**DEFAULT_WINGET, **s["winget"]}
    s["window"] = {**DEFAULT_WINDOW, **s["window"]}
    return s


def get(sid):
    return _parse(db.q1("SELECT * FROM schedules WHERE id=?", (sid,)))


def all_schedules():
    return [_parse(r) for r in db.q("SELECT * FROM schedules ORDER BY name COLLATE NOCASE")]


def _hours(v, label):
    try:
        n = int(v or 0)
    except (TypeError, ValueError):
        raise ValueError(f"{label}: ganze Zahl angeben.")
    if n < 0 or n > 24 * 365:
        raise ValueError(f"{label}: 0 bis 8760 Stunden.")
    return n


def _ids(s):
    return {x.strip().lower() for x in (s or "").replace(";", ",").replace("\n", ",").split(",") if x.strip()}


def _list(s):
    """IDs in Originalschreibweise, ohne Doppelte (Groß/klein egal)."""
    out, seen = [], set()
    for x in (s or "").replace(";", ",").replace("\n", ",").split(","):
        x = x.strip()
        if x and x.lower() not in seen:
            seen.add(x.lower())
            out.append(x)
    return out


def _int_list(v, label):
    out = []
    for i in v or []:
        try:
            out.append(int(i))
        except (TypeError, ValueError):
            raise ValueError(f"{label}: ungültiger Eintrag „{i}“.")
    return out


def clean(data):
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("Name fehlt.")
    kind = data.get("kind") if data.get("kind") in KINDS else "packages"
    pk = []
    if kind == "packages":
        for i in _int_list(data.get("packages"), "Pakete"):
            if not db.val("SELECT 1 FROM packages WHERE id=?", (i,)):
                raise ValueError(f"Paket #{i} existiert nicht.")
            pk.append(i)
        if not pk:
            raise ValueError("Mindestens ein Paket auswählen.")
    w = {**DEFAULT_WINGET, **(data.get("winget") or {})}
    wg = {"mode": "specific" if w.get("mode") == "specific" else "all",
          "ids": ", ".join(_list(w.get("ids"))), "exclude": ", ".join(_list(w.get("exclude"))),
          "scope": w.get("scope") if w.get("scope") in ("machine", "user") else ""}
    if kind == "winget" and wg["mode"] == "specific" and not wg["ids"]:
        raise ValueError("Mindestens eine winget-ID angeben (oder „alle veralteten“ wählen).")
    for wid in _ids(wg["ids"]) | _ids(wg["exclude"]):
        if not util.WINGET_ID_RE.match(wid):
            raise ValueError(f"Ungültige winget-ID: {wid}")
    tg = data.get("targets") or {}
    comps = sorted(set(_int_list(tg.get("computers"), "Computer")))
    cols = sorted(set(_int_list(tg.get("collections"), "Sammlungen")))
    if not comps and not cols:
        raise ValueError("Mindestens ein Ziel (Computer oder Sammlung) auswählen.")
    trig = []
    for t in data.get("triggers") or []:
        if t.get("type") not in TRIGGER_TYPES:
            raise ValueError("Unbekannter Auslöser.")
        trig.append(profiles.clean_trigger(t))
    win = {**DEFAULT_WINDOW, **(data.get("window") or {})}
    days = sorted({int(d) for d in win.get("days") or [] if str(d).isdigit() and 1 <= int(d) <= 7})
    window = {"enabled": bool(win.get("enabled")), "days": days or [1, 2, 3, 4, 5, 6, 7],
              "start": profiles._hm(win.get("start") or "00:00", "Zeitfenster (von)"),
              "end": profiles._hm(win.get("end") or "23:59", "Zeitfenster (bis)")}
    o = {**DEFAULT_OPTIONS, **(data.get("options") or {})}
    try:
        maxa = max(0, min(100, int(o.get("max_attempts") or 0)))
    except (TypeError, ValueError):
        maxa = 0
    opts = {"stop_on_fail": bool(o.get("stop_on_fail")), "offline": "wait" if o.get("offline") == "wait" else "skip",
            "skip_successful": bool(o.get("skip_successful")), "max_attempts": maxa, "only_online": True,
            "retry_wait_hours": _hours(o.get("retry_wait_hours"), "Wartezeit nach Fehlschlag"),
            "renew_after_hours": _hours(o.get("renew_after_hours"), "Erneut versorgen nach"),
            "retry_failed": bool(o.get("retry_failed", True)), "wake_offline": bool(o.get("wake_offline"))}
    if opts["wake_offline"]:
        opts["offline"] = "wait"  # geweckte Computer werden versorgt, sobald sie hochgefahren sind
    tenant = data.get("tenant_id")
    tenant = int(tenant) if tenant not in (None, "", 0, "0") else None
    return {"name": name[:200], "description": (data.get("description") or "").strip()[:2000], "kind": kind,
            "enabled": 1 if data.get("enabled", True) else 0, "tenant_id": tenant, "triggers": trig,
            "targets": {"computers": comps, "collections": cols}, "packages": pk, "options": opts,
            "winget": wg, "window": window}


def save(sid, data, user=None):
    d = clean(data)
    now = util.now()
    vals = (d["name"], d["description"], d["kind"], d["enabled"], d["tenant_id"], json.dumps(d["triggers"]),
            json.dumps(d["targets"]), json.dumps(d["packages"]), json.dumps(d["options"]), json.dumps(d["winget"]),
            json.dumps(d["window"]), now)
    if sid:
        old = get(sid)
        db.ex("UPDATE schedules SET name=?, description=?, kind=?, enabled=?, tenant_id=?, triggers=?, targets=?, packages=?, "
              "options=?, winget=?, window=?, updated_at=? WHERE id=?", (*vals, sid))
        if old and (old["packages"] != d["packages"] or old["kind"] != d["kind"]):
            # Andere Pakete: bisherige Erfolge gelten nicht mehr
            db.ex("UPDATE schedule_targets SET status=NULL, attempts=0, last_success=NULL WHERE schedule_id=?", (sid,))
        _baseline(sid, d["triggers"], old["triggers"] if old else None, bool(old and not old["enabled"] and d["enabled"]))
    else:
        sid = db.ex("INSERT INTO schedules(name, description, kind, enabled, tenant_id, triggers, targets, packages, options, "
                    "winget, window, updated_at, created_by, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (*vals, user, now))
        try:
            _baseline(sid, d["triggers"])
        except Exception:
            db.ex("DELETE FROM schedules WHERE id=?", (sid,))  # kein halb gespeicherter Zeitplan
            raise
    return sid


def ensure_baselines():
    """Beim Start: Auslöser ohne Ausgangspunkt bekommen „jetzt“ – sonst würde ein vergangener Termin sofort ausgeführt."""
    for s in all_schedules():
        try:
            for t in s["triggers"]:
                db.ex("INSERT OR IGNORE INTO schedule_trigger_state(schedule_id, trigger_id, last_fired) VALUES(?,?,?)",
                      (s["id"], t["id"], util.now()))
        except Exception as e:
            pklog.warn("deploy", f"Zeitplan „{s['name']}“: Ausgangspunkt der Auslöser nicht gesetzt: {e}")


def _changed(t, old, reenabled):
    """Auslöser wurde geändert oder wieder aktiviert → Ausgangspunkt neu setzen (sonst löst ein vergangener Termin sofort aus)."""
    if reenabled or old is None:
        return reenabled
    strip = lambda x: {k: v for k, v in x.items() if k != "id"}  # noqa: E731
    return strip(t) != strip(old) or (not old.get("enabled", True) and t.get("enabled", True))


def _baseline(sid, triggers, old_triggers=None, reenabled=False):
    now = util.now()
    old = {t.get("id"): t for t in (old_triggers or [])}
    for t in triggers:
        if _changed(t, old.get(t["id"]), reenabled):
            db.ex("INSERT INTO schedule_trigger_state(schedule_id, trigger_id, last_fired) VALUES(?,?,?) "
                  "ON CONFLICT(schedule_id, trigger_id) DO UPDATE SET last_fired=excluded.last_fired", (sid, t["id"], now))
        else:
            db.ex("INSERT OR IGNORE INTO schedule_trigger_state(schedule_id, trigger_id, last_fired) VALUES(?,?,?)",
                  (sid, t["id"], now))
    ids = [t["id"] for t in triggers] + [DEFERRED, RETRY]
    db.ex(f"DELETE FROM schedule_trigger_state WHERE schedule_id=? AND trigger_id NOT IN ({','.join('?' * len(ids))})",
          (sid, *ids))


def members(s):
    from . import collections_
    ids = set(s["targets"]["computers"])
    for col in s["targets"]["collections"]:
        if col == profiles.ALL_COMPUTERS:
            ids.update(r["id"] for r in db.q("SELECT id FROM computers"))
            continue
        try:
            ids.update(collections_.member_ids(col))
        except Exception:
            continue
    existing = {r["id"] for r in db.q("SELECT id FROM computers")}
    ids &= existing
    # Zeitplan eines Mandanten mit Sammlung: nur deren Computer (wie beim Verteilen) – auch für Vorschau und Zielverlauf
    if s.get("tenant_id"):
        from . import deploy
        mem = deploy.tenant_members(s["tenant_id"])
        if mem is not None:
            ids &= mem
    return ids


# --- Zeitfenster ------------------------------------------------------------------------------
def in_window(s, now=None):
    w = s.get("window") or DEFAULT_WINDOW
    if not w.get("enabled"):
        return True
    now = now or datetime.datetime.now()
    cur = now.strftime("%H:%M")
    start, end = w.get("start") or "00:00", w.get("end") or "23:59"
    days = set(w.get("days") or [1, 2, 3, 4, 5, 6, 7])
    if start <= end:
        return now.isoweekday() in days and start <= cur <= end
    # über Mitternacht: der Teil nach Mitternacht gehört zum Vortag
    if cur >= start:
        return now.isoweekday() in days
    if cur <= end:
        return ((now - datetime.timedelta(days=1)).isoweekday()) in days
    return False


def window_text(s):
    w = s.get("window") or DEFAULT_WINDOW
    if not w.get("enabled"):
        return ""
    names = {1: "Mo", 2: "Di", 3: "Mi", 4: "Do", 5: "Fr", 6: "Sa", 7: "So"}
    days = w.get("days") or []
    d = "täglich" if len(days) == 7 else ("Mo–Fr" if days == [1, 2, 3, 4, 5] else " ".join(names[x] for x in days))
    return f"{d} {w['start']}–{w['end']}"


def _defer(sid):
    db.ex("INSERT INTO schedule_trigger_state(schedule_id, trigger_id, last_fired) VALUES(?,?,?) "
          "ON CONFLICT(schedule_id, trigger_id) DO UPDATE SET last_fired=excluded.last_fired", (sid, DEFERRED, util.now()))


def _deferred(sid):
    return db.val("SELECT last_fired FROM schedule_trigger_state WHERE schedule_id=? AND trigger_id=?", (sid, DEFERRED))


def _undefer(sid):
    db.ex("DELETE FROM schedule_trigger_state WHERE schedule_id=? AND trigger_id=?", (sid, DEFERRED))


def next_occurrence(t, now):
    typ = t["type"]
    if typ == "heartbeat":
        return None
    end = util.parse(t.get("end"))
    if typ == "once":
        at = util.parse(t["at"])
        res = at if at and at > now else None
    elif typ == "interval":
        start = util.parse(t["start"])
        if not start:
            return None
        step = datetime.timedelta(**{dict(m="minutes", h="hours", d="days")[t["unit"]]: t["every"]})
        res = start if start > now else start + step * (int((now - start) / step) + 1)
    else:
        hh, mm = (int(x) for x in t["time"].split(":"))
        res = None
        for ahead in range(0, 400):
            cand = (now + datetime.timedelta(days=ahead)).replace(hour=hh, minute=mm, second=0, microsecond=0)
            if cand <= now:
                continue
            if profiles.occurs_on(t, cand):
                res = cand
                break
    if res and end and res > end:
        return None
    return res


def summary(s, only=None):
    """only: Menge von Computer-IDs (OU-Admin) – Zahlen nur für diese Computer."""
    now = datetime.datetime.now()
    rows = db.q("SELECT * FROM schedule_targets WHERE schedule_id=?", (s["id"],))
    if only is not None:
        rows = [r for r in rows if r["computer_id"] in only]
    nxt = [n for n in (next_occurrence(t, now) for t in s["triggers"] if t.get("enabled", True)) if n]
    names = {r["id"]: r["name"] for r in db.q("SELECT id, name FROM packages")}
    w = s["winget"]
    if s["kind"] == "winget":
        what = ["winget: " + ("alle veralteten" if w["mode"] == "all" else w["ids"])
                + (f" (außer {w['exclude']})" if w["exclude"] else "")]
    else:
        what = [names.get(i, f"#{i}") for i in s["packages"]]
    return {"triggers_text": [profiles.describe(t) for t in s["triggers"]],
            "window_text": window_text(s), "in_window": in_window(s), "deferred": bool(_deferred(s["id"])),
            "next_run": util.fmt(min(nxt)) if nxt and s["enabled"] else None,
            "package_names": what,
            "members": len(members(s) if only is None else members(s) & only),
            "ok": sum(1 for r in rows if r["status"] == "success"),
            "failed": sum(1 for r in rows if r["status"] == "failed"),
            "pending": sum(1 for r in rows if r["pending"]),
            "last_run": max((r["last_run"] for r in rows if r["last_run"]), default=None)}


def composite_package(s):
    """Die Pakete des Zeitplans als ein Paket mit verschachtelten Schritten (in Reihenfolge)."""
    from . import steps
    main = []
    for i, pid in enumerate(s["packages"]):
        main.append({"id": f"p{i}", "type": "nested", "package_id": pid, "title": "", "enabled": True,
                     "run_as": "inherit", "on_error": "stop" if s["options"]["stop_on_fail"] else "continue",
                     "timeout_min": 0, "conditions": steps.default_conditions()})
    from . import deploy
    reb = deploy.strongest_reboot(db.val("SELECT reboot FROM packages WHERE id=?", (pid,)) for pid in s["packages"])
    return {"name": s["name"], "version": "", "run_as": "deploy", "rescan": 1, "reboot": reb, "composite": True,
            "timeout_min": db.setting("deploy_timeout_minutes"), "conditions": steps.default_conditions(),
            "steps": {"pre": [], "main": main, "post": []}, "tenant_id": s.get("tenant_id")}


# --- winget-Updates ---------------------------------------------------------------------------
def winget_candidates(s, cids=None, online_only=True):
    """{winget_id: [computer_ids]} – veraltete Software der Ziele mit sicherer winget-Zuordnung."""
    targets = members(s) if cids is None else (set(cids) & members(s))
    if not targets:
        return {}
    w = s["winget"]
    q = ",".join("?" * len(targets))
    rows = db.q(f"""SELECT DISTINCT s.computer_id, s.winget_id FROM software s JOIN computers c ON c.id = s.computer_id
                    WHERE s.outdated = 1 AND s.winget_id IS NOT NULL AND COALESCE(s.winget_sure, 1) = 1
                      {"AND c.online = 1 AND c.winrm = 1" if online_only else ""}
                      AND (c.os_build IS NULL OR c.os_build = '' OR CAST(c.os_build AS INTEGER) >= 17763)
                      AND c.id IN ({q})""", tuple(targets))
    only = _ids(w["ids"]) if w["mode"] == "specific" else set()
    excl = _ids(w["exclude"])
    wait = s["options"].get("retry_wait_hours") or 0
    since = util.ago(hours=wait) if wait else util.now()
    busy = {(r["computer_id"], (r["wid"] or "").lower()) for r in db.q(
        """SELECT t.computer_id, json_extract(d.package_json, '$.winget_id') wid FROM deployment_targets t
           JOIN deployments d ON d.id = t.deployment_id
           WHERE t.status IN ('queued','running')
              OR (d.schedule_id = ? AND t.status = 'failed' AND t.finished_at >= ?)""", (s["id"], since))}
    # „Maximale Fehlversuche“ auch für winget: aufeinanderfolgende Fehlschläge je Computer und Programm zählen
    # (ein dauerhaft scheiterndes Update – z. B. weil das Programm läuft – nicht bei jedem Auslöser erneut starten)
    fails = {}
    maxa = s["options"].get("max_attempts") or 0
    if maxa:
        for r in db.q("""SELECT t.computer_id cid, lower(json_extract(d.package_json, '$.winget_id')) wid, t.status
                         FROM deployment_targets t JOIN deployments d ON d.id = t.deployment_id
                         WHERE d.schedule_id = ? AND t.status IN ('failed','success') ORDER BY t.finished_at""", (s["id"],)):
            k = (r["cid"], r["wid"] or "")
            fails[k] = fails.get(k, 0) + 1 if r["status"] == "failed" else 0
    out = {}
    for r in rows:
        wl = r["winget_id"].lower()
        if (only and wl not in only) or wl in excl or (r["computer_id"], wl) in busy:
            continue
        if maxa and fails.get((r["computer_id"], wl), 0) >= maxa:
            continue
        out.setdefault(r["winget_id"], []).append(r["computer_id"])
    return out


def target_label(s):
    """Kurztext der Zeitplan-Ziele für die Bereitstellungsliste."""
    names = []
    for col in s["targets"]["collections"]:
        names.append("Alle Computer" if col == profiles.ALL_COMPUTERS else
                     (db.val("SELECT name FROM collections WHERE id=?", (col,)) or f"#{col}"))
    n = len(s["targets"]["computers"])
    parts = [("Sammlung: " if len(names) == 1 else "Sammlungen: ") + ", ".join(names)] if names else []
    if n:
        parts.append(f"{n} Computer")
    return " + ".join(parts)


def _fire_winget(s, cids, reason, user):
    from . import deploy
    o = s["options"]
    targets = members(s) if cids is None else (set(cids) & members(s))
    online = {r["id"] for r in db.q("SELECT id FROM computers WHERE online=1 AND winrm=1")}
    now = util.now()
    if o["offline"] == "wait" or o.get("wake_offline"):
        # Offline Ziele mit veralteter Software vormerken – sie werden beim Heartbeat versorgt
        offline_cand = winget_candidates(s, [c for c in targets if c not in online], online_only=False)
        _wake_offline(s, o, sorted({c for ids in offline_cand.values() for c in ids}), reason)
        for cid in {c for ids in offline_cand.values() for c in ids}:
            db.ex("""INSERT INTO schedule_targets(schedule_id, computer_id, pending, pending_since, message) VALUES(?,?,1,?,?)
                     ON CONFLICT(schedule_id, computer_id) DO UPDATE SET pending=1,
                     pending_since=COALESCE(pending_since, excluded.pending_since), message=excluded.message""",
                  (s["id"], cid, now, "Offline – Updates folgen, sobald der Computer online ist"))
    first = None
    n = 0
    for wid, ids in sorted(winget_candidates(s, [c for c in targets if c in online]).items()):
        try:
            pkg = deploy.adhoc_winget(wid, "upgrade", s["winget"].get("scope") or None)
            pkg["name"] = f"Auto-Update {wid}"
            pkg["tenant_id"] = s.get("tenant_id")
            did = deploy.create(pkg, ids, user or "Zeitplan", source="schedule", name=f"{s['name']}: {wid}",
                                schedule_id=s["id"], start_now=False, target_label=target_label(s))
            for r in db.q("SELECT computer_id FROM deployment_targets WHERE deployment_id=?", (did,)):
                db.ex("""INSERT INTO schedule_targets(schedule_id, computer_id, status, deployment_id, pending, message)
                         VALUES(?,?,?,?,0,?) ON CONFLICT(schedule_id, computer_id) DO UPDATE SET status=excluded.status,
                         deployment_id=excluded.deployment_id, pending=0, pending_since=NULL, message=excluded.message""",
                      (s["id"], r["computer_id"], "queued", did, f"winget upgrade {wid} (#{did})"))
            deploy.start(did, checked=True)
            first = first or did
            n += 1
        except Exception as e:
            pklog.warn("deploy", f"Zeitplan „{s['name']}“ – {wid}: {e}")
    # Vorgemerkte, inzwischen online Ziele ohne (weitere) Updates: Merker entfernen
    db.ex(f"""UPDATE schedule_targets SET pending=0, pending_since=NULL, message='Keine veraltete Software'
              WHERE schedule_id=? AND pending=1 AND computer_id IN ({','.join('?' * len(online & targets)) or 'NULL'})""",
          (s["id"], *sorted(online & targets)))
    if n:
        pklog.info("deploy", f"Zeitplan „{s['name']}“ ({reason}): {n} winget-Update(s) gestartet.", user=user)
    return {"deployment_id": first, "count": n}


# 2.31: nur bei einem echten Lauf (Termin, Zeitfenster, manuell) wecken – nicht bei jedem Heartbeat oder Nachholen
# 2.32.5: „Wiederholung – Lauf konnte nicht gestartet werden“ holt einen echten Termin nach – dort wird geweckt
_NO_WAKE = ("Heartbeat", "Computer wieder online", "vorgemerkte Ziele", "fehlgeschlagene Ziele")
WAKE_MAX = 200  # höchstens so viele Computer je Lauf wecken
_waking = set()  # Zeitpläne, für die gerade geweckt wird (je Zeitplan nur ein Durchlauf gleichzeitig)
_waking_lock = threading.Lock()


def _wake_offline(s, o, ids, reason):
    if not o.get("wake_offline") or not ids or any(str(reason or "").startswith(x) for x in _NO_WAKE):
        return
    # 2.32.5: nur wirklich ausgeschaltete Computer wecken – eingeschaltete ohne WinRM brauchen kein Magic Packet
    off = {r["id"] for r in db.q("SELECT id FROM computers WHERE COALESCE(online, 0)=0")}
    ids = [c for c in ids if c in off]
    if not ids:
        return
    with _waking_lock:
        if s["id"] in _waking:
            return  # der vorige Weck-Durchlauf dieses Zeitplans läuft noch
        _waking.add(s["id"])

    def run():
        try:
            _wake_run(s, ids, reason)
        finally:
            with _waking_lock:
                _waking.discard(s["id"])
    threading.Thread(target=run, name=f"wake-sched-{s['id']}", daemon=True).start()


def _wake_run(s, ids, reason):
    from . import tools
    n = 0
    for cid in list(ids)[:WAKE_MAX]:
        c = db.q1("SELECT * FROM computers WHERE id=?", (cid,))
        if not c:
            continue
        try:
            res = tools.wake(c)
            if res.get("exit") == 0:
                tools.watch_wake(c, res.get("relay") or [], "Zeitplan")
                n += 1
        except Exception as e:  # noqa: BLE001
            pklog.warn("deploy", f"Zeitplan „{s['name']}“: Wake-on-LAN für {c['name']} fehlgeschlagen: {e}", computer_id=cid)
    if n:
        pklog.info("deploy", f"Zeitplan „{s['name']}“ ({reason}): {n} ausgeschaltete(n) Computer per Wake-on-LAN geweckt – "
                             "sie werden versorgt, sobald sie hochgefahren sind.")


# --- Ausführen --------------------------------------------------------------------------------
_locks = {}
_locks_guard = threading.Lock()


def _lock(sid):
    with _locks_guard:
        return _locks.setdefault(sid, threading.Lock())


def fire(s, cids=None, reason="Zeitplan", user=None, force=False):
    """Zeitplan für alle (oder die angegebenen) Ziele ausführen. Gibt {deployment_id, count} zurück.
    Je Zeitplan nur ein Lauf gleichzeitig – Heartbeat- und Minuten-Thread könnten sonst dasselbe Ziel doppelt versorgen."""
    with _lock(s["id"]):
        return _fire(s, cids, reason, user, force)


def _fire(s, cids=None, reason="Zeitplan", user=None, force=False):
    if s["kind"] == "winget":
        return _fire_winget(s, cids, reason, user)
    from . import deploy
    targets = members(s) if cids is None else (set(cids) & members(s))
    if not targets:
        return {"deployment_id": None, "count": 0}
    hist = {r["computer_id"]: r for r in db.q("SELECT * FROM schedule_targets WHERE schedule_id=?", (s["id"],))}
    o = s["options"]
    renew = util.ago(hours=o["renew_after_hours"]) if o.get("renew_after_hours") else None
    wait = util.ago(hours=o["retry_wait_hours"]) if o.get("retry_wait_hours") else None
    # „queued/running“ nur glauben, wenn die Bereitstellung noch läuft – sonst (gelöscht, abgebrochen, Absturz) neu versorgen
    live = {r["id"] for r in db.q("SELECT id FROM deployments WHERE status IN ('queued','running','scheduled')")}
    todo = []
    for cid in targets:
        h = hist.get(cid) or {}
        if h.get("status") in ("queued", "running") and h.get("deployment_id") in live:
            continue
        if not force and o["skip_successful"] and h.get("status") in ("success", "skipped"):
            if not renew or (h.get("last_run") or "") >= renew:
                continue  # erfolgreich – und (falls eingestellt) noch nicht alt genug für eine erneute Versorgung
        if not force and h.get("status") == "failed":
            if o["max_attempts"] and (h.get("attempts") or 0) >= o["max_attempts"]:
                continue
            if wait and (h.get("last_run") or "") >= wait:
                continue  # Wartezeit nach Fehlschlag läuft noch
        todo.append(cid)
    skipped = sorted(targets - set(todo))
    if skipped:
        # Vorgemerkte Ziele, die nichts (mehr) brauchen, nicht weiter vormerken
        db.ex(f"UPDATE schedule_targets SET pending=0, pending_since=NULL WHERE schedule_id=? AND pending=1 "
              f"AND computer_id IN ({','.join('?' * len(skipped))})", (s["id"], *skipped))
    if not todo:
        return {"deployment_id": None, "count": 0}
    online = {r["id"] for r in db.q("SELECT id FROM computers WHERE online=1 AND winrm=1")}
    now = util.now()
    run = [c for c in todo if c in online]
    off = [c for c in todo if c not in online]
    _wake_offline(s, o, off, reason)
    for cid in off:
        if o["offline"] == "wait" or o.get("wake_offline"):
            db.ex("""INSERT INTO schedule_targets(schedule_id, computer_id, pending, pending_since, message) VALUES(?,?,1,?,?)
                     ON CONFLICT(schedule_id, computer_id) DO UPDATE SET pending=1,
                     pending_since=COALESCE(pending_since, excluded.pending_since), message=excluded.message""",
                  (s["id"], cid, now, "Offline – wird versorgt, sobald der Computer online ist"))
        else:
            db.ex("""INSERT INTO schedule_targets(schedule_id, computer_id, status, last_run, message) VALUES(?,?,?,?,?)
                     ON CONFLICT(schedule_id, computer_id) DO UPDATE SET status=excluded.status, last_run=excluded.last_run,
                     message=excluded.message""", (s["id"], cid, "offline", now, "Offline – übersprungen"))
    if not run:
        return {"deployment_id": None, "count": 0}
    from . import integrity
    try:
        did = deploy.create(composite_package(s), run, user or "Zeitplan", source="schedule", name=s["name"],
                            schedule_id=s["id"], start_now=False, target_label=target_label(s))
    except integrity.IntegrityError as e:
        # Bruch im Repository: Lauf unterbrechen, Ziele bleiben offen (nächster Lauf versucht es wieder) –
        # gemeldet nur bei neuer Ursache, sonst stünde bei jedem Heartbeat dieselbe Warnung im Protokoll
        if _blocked.get(s["id"]) != str(e):
            _blocked[s["id"]] = str(e)
            pklog.warn("deploy", f"Zeitplan „{s['name']}“ ({reason}): {e}", user=user)
        return {"deployment_id": None, "count": 0, "blocked": integrity.NEUTRAL}
    _blocked.pop(s["id"], None)
    # Erst den Zielverlauf schreiben, dann starten – sonst kann ein schnell fertiges Ziel überschrieben werden
    for r in db.q("SELECT computer_id FROM deployment_targets WHERE deployment_id=?", (did,)):
        db.ex("""INSERT INTO schedule_targets(schedule_id, computer_id, status, deployment_id, pending, message) VALUES(?,?,?,?,0,?)
                 ON CONFLICT(schedule_id, computer_id) DO UPDATE SET status=excluded.status, deployment_id=excluded.deployment_id,
                 pending=0, pending_since=NULL, message=excluded.message""",
              (s["id"], r["computer_id"], "queued", did, f"Bereitstellung #{did}"))
    deploy.start(did, checked=True)
    pklog.info("deploy", f"Zeitplan „{s['name']}“ ({reason}): Bereitstellung #{did} für {len(run)} Computer"
                         + (f", {len(off)} offline" if off else "") + ".", user=user)
    return {"deployment_id": did, "count": 1}


_blocked = {}  # Zeitplan-ID → zuletzt gemeldete Unterbrechung (Bruch im Repository)


def preview(s, only=None):
    """Was ein Lauf jetzt tun würde (ohne etwas zu starten). only: nur diese Computer (OU-Admin)."""
    names = {r["id"]: r["name"] for r in db.q("SELECT id, name FROM computers")}
    online = {r["id"] for r in db.q("SELECT id FROM computers WHERE online=1 AND winrm=1")}
    if s["kind"] == "winget":
        cand = winget_candidates(s, online_only=False)
        if only is not None:
            cand = {k: [i for i in v if i in only] for k, v in cand.items()}
            cand = {k: v for k, v in cand.items() if v}
        return {"kind": "winget", "items": [{"winget_id": k, "computers": sorted(names.get(i, "?") for i in v),
                                             "offline": sorted(names.get(i, "?") for i in v if i not in online)}
                                            for k, v in sorted(cand.items())]}
    mem = members(s) if only is None else members(s) & only
    return {"kind": "packages", "members": len(mem), "online": len(mem & online)}


def target_finished(sid, did, tid):
    t = db.q1("SELECT * FROM deployment_targets WHERE id=?", (tid,))
    if not t or not t["computer_id"]:
        return
    st = t["status"]
    status = st if st in ("success", "skipped", "cancelled") else "failed"
    now = util.now()
    s = get(sid)
    if status == "failed" and s and s["options"]["offline"] == "wait" and (t["message"] or "").startswith("Computer nicht erreichbar"):
        # Beim Start doch offline: vormerken statt als Fehlversuch zählen
        db.ex("""INSERT INTO schedule_targets(schedule_id, computer_id, pending, pending_since, message, deployment_id)
                 VALUES(?,?,1,?,?,?) ON CONFLICT(schedule_id, computer_id) DO UPDATE SET status=NULL, pending=1,
                 pending_since=excluded.pending_since, message=excluded.message, deployment_id=excluded.deployment_id""",
              (sid, t["computer_id"], now, "Nicht erreichbar – wird versorgt, sobald der Computer online ist", did))
        return
    # Als Fehlversuch zählt nur „fehlgeschlagen“ – Abbrechen oder Überspringen verbraucht keine Versuche
    db.ex("""INSERT INTO schedule_targets(schedule_id, computer_id, status, last_run, last_success, attempts, deployment_id,
             message, pending) VALUES(?,?,?,?,?,?,?,?,0)
             ON CONFLICT(schedule_id, computer_id) DO UPDATE SET status=excluded.status, last_run=excluded.last_run,
             last_success=COALESCE(excluded.last_success, last_success), deployment_id=excluded.deployment_id,
             message=excluded.message, pending=0, pending_since=NULL,
             attempts=CASE WHEN excluded.status='success' THEN 0 WHEN excluded.status='failed' THEN COALESCE(attempts, 0) + 1
                           ELSE attempts END""",
          (sid, t["computer_id"], status, now, now if status == "success" else None, 1 if status == "failed" else 0, did,
           t["message"]))


_retry_count = {}


def _mark_retry(sid):
    db.ex("INSERT INTO schedule_trigger_state(schedule_id, trigger_id, last_fired) VALUES(?,?,?) "
          "ON CONFLICT(schedule_id, trigger_id) DO UPDATE SET last_fired=excluded.last_fired", (sid, RETRY, util.now()))


def _clear_retry(sid):
    _retry_count.pop(sid, None)
    db.ex("DELETE FROM schedule_trigger_state WHERE schedule_id=? AND trigger_id=?", (sid, RETRY))


def _fire_safe(s, cids=None, reason="Zeitplan"):
    """Lauf starten; scheitert schon der Start (z. B. Datenbank gesperrt, Paket fehlerhaft), geht er nicht
    verloren: ein ganzer Lauf wird alle 15 Minuten wiederholt, einzelne Ziele werden vorgemerkt."""
    try:
        res = fire(s, cids, reason=reason)
    except ValueError as e:
        # Fehler in der Einrichtung (Paket fehlt, kein Ziel im Mandanten …): wird durch Wiederholen nicht besser –
        # einmal melden statt endlos alle 15 Minuten bzw. bei jedem Heartbeat
        if cids is None:
            _clear_retry(s["id"])
        pklog.error("deploy", f"Zeitplan „{s['name']}“ konnte nicht gestartet werden: {e} – bitte den Zeitplan prüfen.")
        return False
    except Exception as e:
        n = _retry_count.get(s["id"], 0) + 1 if cids is None else 1
        if cids is None:
            _retry_count[s["id"]] = n
            _mark_retry(s["id"])
        else:
            now = util.now()
            for cid in cids:
                db.ex("""INSERT INTO schedule_targets(schedule_id, computer_id, pending, pending_since, message) VALUES(?,?,1,?,?)
                         ON CONFLICT(schedule_id, computer_id) DO UPDATE SET pending=1,
                         pending_since=COALESCE(pending_since, excluded.pending_since), message=excluded.message""",
                      (s["id"], cid, now, "Lauf konnte nicht gestartet werden – wird nachgeholt"))
        if cids is not None:
            pklog.error("deploy", f"Zeitplan „{s['name']}“ fehlgeschlagen: {e} – wird für diese Computer nachgeholt.")
        elif n == 1 or n % 4 == 0:
            pklog.error("deploy", f"Zeitplan „{s['name']}“ fehlgeschlagen ({n}. Versuch): {e} – "
                                  f"wird alle {RETRY_MINUTES} Minuten erneut versucht.")
        return False
    if cids is None:
        _clear_retry(s["id"])
    # Audit 2.29: vom Bruch im Repository unterbrochen = nichts geprüft/gestartet → kein „erledigt“ (sonst wären die
    # automatischen Wiederholungen eines vorübergehenden Bruchs auf einen Schlag aufgebraucht)
    return not (isinstance(res, dict) and res.get("blocked"))


def retry_candidates(s):
    """Fehlgeschlagene, erreichbare Ziele, die automatisch nachgeholt werden (Option „Fehlgeschlagene nachholen“):
    nach der Wartezeit (mindestens 1 Stunde), höchstens „Max. Fehlversuche“ (bei 0: 3) Versuche."""
    o = s["options"]
    if not o.get("retry_failed", True):
        return []
    hours = max(AUTO_RETRY_MIN_HOURS, o.get("retry_wait_hours") or 0)
    limit = o.get("max_attempts") or AUTO_RETRY_DEFAULT_MAX
    rows = db.q("""SELECT st.computer_id FROM schedule_targets st JOIN computers c ON c.id = st.computer_id
                   WHERE st.schedule_id=? AND st.status='failed' AND COALESCE(st.pending, 0)=0
                     AND COALESCE(st.attempts, 0) < ? AND st.last_run IS NOT NULL AND st.last_run <= ?
                     AND c.online=1 AND c.winrm=1""", (s["id"], limit, util.ago(hours=hours)))
    mem = members(s)
    return sorted(r["computer_id"] for r in rows if r["computer_id"] in mem)


def evaluate():
    """Vom Scheduler jede Minute aufgerufen: fällige Auslöser ausführen, auf das Zeitfenster wartende Läufe
    und vorgemerkte (inzwischen online) Ziele im Zeitfenster nachholen."""
    now = datetime.datetime.now()
    nows = util.fmt(now)
    state = {(r["schedule_id"], r["trigger_id"]): r["last_fired"] for r in db.q("SELECT * FROM schedule_trigger_state")}
    for s in all_schedules():
        if not s["enabled"]:
            continue
        try:
            _evaluate_one(s, now, nows, state)
        except Exception as e:
            pklog.error("deploy", f"Zeitplan „{s['name']}“: Auswertung fehlgeschlagen: {e}")


def _evaluate_one(s, now, nows, state):
    due = None
    for t in s["triggers"]:
        if not t.get("enabled", True) or t["type"] == "heartbeat":
            continue
        end = util.parse(t.get("end"))
        if end and now > end:
            continue
        occ = profiles.last_occurrence(t, now)
        last = util.parse(state.get((s["id"], t["id"])))
        if occ and (last is None or occ > last):
            db.ex("INSERT INTO schedule_trigger_state(schedule_id, trigger_id, last_fired) VALUES(?,?,?) "
                  "ON CONFLICT(schedule_id, trigger_id) DO UPDATE SET last_fired=excluded.last_fired",
                  (s["id"], t["id"], nows))
            due = due or t
    win = in_window(s, now)
    if due and not win:
        _defer(s["id"])  # außerhalb des Zeitfensters: zu Beginn des Fensters nachholen
        pklog.info("deploy", f"Zeitplan „{s['name']}“ fällig ({profiles.describe(due)}) – wartet auf das Zeitfenster "
                             f"({window_text(s)}).")
        return
    if not win:
        return
    if due or (s["id"], DEFERRED) in state:
        _undefer(s["id"])
        _fire_safe(s, reason=profiles.describe(due) if due else "Zeitfenster geöffnet")
        return
    failed_at = util.parse(state.get((s["id"], RETRY)))
    if failed_at and now - failed_at >= datetime.timedelta(minutes=RETRY_MINUTES):
        # Lauf konnte nicht gestartet werden – erneut versuchen (bis er klappt oder der nächste Termin kommt)
        _fire_safe(s, reason="Wiederholung – Lauf konnte nicht gestartet werden")
        return
    retry = retry_candidates(s)
    if retry:
        before = {r["computer_id"]: r["deployment_id"] for r in db.q(
            f"SELECT computer_id, deployment_id FROM schedule_targets WHERE schedule_id=? "
            f"AND computer_id IN ({','.join('?' * len(retry))})", (s["id"], *retry))}
        if _fire_safe(s, retry, reason="fehlgeschlagene Ziele nachholen"):
            # Ziele, für die nichts gestartet wurde (z. B. Update inzwischen erledigt), nicht jede Minute erneut prüfen
            limit = s["options"].get("max_attempts") or AUTO_RETRY_DEFAULT_MAX
            for cid, dep in before.items():
                db.ex("UPDATE schedule_targets SET attempts=MAX(COALESCE(attempts, 0), ?) WHERE schedule_id=? AND computer_id=? "
                      "AND status='failed' AND COALESCE(pending, 0)=0 AND deployment_id IS ?", (limit, s["id"], cid, dep))
    if s["window"].get("enabled"):
        # Im Fenster: vorgemerkte Ziele, die außerhalb des Fensters online kamen
        pend = [r["computer_id"] for r in db.q(
            """SELECT st.computer_id FROM schedule_targets st JOIN computers c ON c.id = st.computer_id
               WHERE st.schedule_id=? AND st.pending=1 AND c.online=1 AND c.winrm=1""", (s["id"],))]
        if pend:
            _fire_safe(s, pend, reason="vorgemerkte Ziele im Zeitfenster")


def on_online(cids):
    """Heartbeat: offline vorgemerkte Ziele versorgen und Heartbeat-Auslöser ausführen (im Zeitfenster;
    außerhalb werden die Computer vorgemerkt und beim Öffnen des Fensters versorgt)."""
    if not cids:
        return
    cset = set(cids)
    now = util.now()
    for s in all_schedules():
        if not s["enabled"]:
            continue
        hb = any(t["type"] == "heartbeat" and t.get("enabled", True) for t in s["triggers"])
        pend = {r["computer_id"] for r in db.q("SELECT computer_id FROM schedule_targets WHERE schedule_id=? AND pending=1",
                                                (s["id"],))}
        hit = cset & (members(s) if hb else pend)
        if not hit:
            continue
        if not in_window(s):
            for cid in hit:
                db.ex("""INSERT INTO schedule_targets(schedule_id, computer_id, pending, pending_since, message) VALUES(?,?,1,?,?)
                         ON CONFLICT(schedule_id, computer_id) DO UPDATE SET pending=1,
                         pending_since=COALESCE(pending_since, excluded.pending_since), message=excluded.message""",
                      (s["id"], cid, now, f"Online – wird im Zeitfenster ({window_text(s)}) versorgt"))
            continue
        _fire_safe(s, sorted(hit), reason="Heartbeat" if hb else "Computer wieder online")


def on_heartbeat(online_ids):
    """Nach jedem Heartbeat (automatisch oder manuell): erreichbare Ziele versorgen, die noch nie dran waren
    (Zeitplan mit Heartbeat-Auslöser, z. B. neu angelegt oder neu in einer dynamischen Sammlung) oder die
    vorgemerkt sind. Bereits versuchte Ziele werden hier nicht wiederholt – das regeln die übrigen Auslöser,
    die Wartezeit nach Fehlschlag und „Auswahl erneut versorgen“."""
    online = set(online_ids)
    if not online:
        return
    for s in all_schedules():
        if not s["enabled"] or not in_window(s):
            continue
        rows = db.q("SELECT computer_id, pending, status FROM schedule_targets WHERE schedule_id=?", (s["id"],))
        seen = {r["computer_id"] for r in rows if not r["pending"]}
        pend = {r["computer_id"] for r in rows if r["pending"]}
        hb = any(t["type"] == "heartbeat" and t.get("enabled", True) for t in s["triggers"])
        mem = members(s)
        # Vormerkungen aufräumen: nicht mehr Mitglied oder seit über 14 Tagen offline (wie bei den Scanprofilen)
        stale = pend - mem
        if stale:
            db.ex(f"UPDATE schedule_targets SET pending=0, pending_since=NULL, message='Nicht mehr Ziel des Zeitplans' "
                  f"WHERE schedule_id=? AND computer_id IN ({','.join('?' * len(stale))})", (s["id"], *stale))
            pend -= stale
        db.ex("UPDATE schedule_targets SET pending=0, pending_since=NULL, message='Vormerkung nach 14 Tagen offline verworfen' "
              "WHERE schedule_id=? AND pending=1 AND pending_since < ?", (s["id"], util.ago(days=14)))
        todo = (online & pend & mem) | ((online & mem) - seen - pend if hb else set())
        if todo:
            _fire_safe(s, sorted(todo), reason="Heartbeat (erreichbar, noch nicht versorgt)")


def history(sid):
    return db.q("""SELECT c.id, c.name, c.online, st.status, st.last_run, st.last_success, st.attempts, st.deployment_id,
                   st.message, st.pending FROM schedule_targets st JOIN computers c ON c.id=st.computer_id
                   WHERE st.schedule_id=? ORDER BY c.name""", (sid,))


# --- Übernahme der früheren „Regeln“ ------------------------------------------------------------
def migrate_rules():
    """Regeln (bis Version 2.4.5) einmalig in Zeitpläne umwandeln."""
    try:
        rows = db.q("SELECT * FROM update_rules WHERE COALESCE(migrated, 0) = 0")
    except Exception:
        return
    retry = db.setting("rule_retry_hours") or 24
    for r in rows:
        try:
            col = r.get("collection_id")
            days = [int(x) for x in (r.get("days") or "1,2,3,4,5,6,7").split(",") if x.strip().isdigit()]
            window = {"enabled": not (len(set(days)) == 7 and (r.get("window_start") or "00:00") == "00:00"
                                      and (r.get("window_end") or "23:59") >= "23:59"),
                      "days": days or [1, 2, 3, 4, 5, 6, 7], "start": r.get("window_start") or "00:00",
                      "end": r.get("window_end") or "23:59"}
            trig = [{"type": "interval", "every": max(1, int(r.get("interval_hours") or 24)), "unit": "h",
                     "start": util.now()[:14] + "00", "enabled": True}]
            data = {"name": r.get("name") or f"Regel {r['id']}", "enabled": bool(r.get("enabled")),
                    "description": "Aus einer Regel übernommen.", "triggers": trig, "window": window,
                    "targets": {"computers": [], "collections": [int(col) if col else profiles.ALL_COMPUTERS]},
                    "options": {"stop_on_fail": True, "offline": "wait", "skip_successful": True, "max_attempts": 0,
                                "retry_wait_hours": retry, "renew_after_hours": retry if r.get("rule_type") == "package" else 0}}
            if r.get("rule_type") == "package":
                if not r.get("package_id") or not db.val("SELECT 1 FROM packages WHERE id=?", (r["package_id"],)):
                    raise ValueError("Paket existiert nicht mehr")
                data.update(kind="packages", packages=[r["package_id"]])
            else:
                data.update(kind="winget", winget={"mode": r.get("mode") or "all", "ids": r.get("winget_ids") or "",
                                                     "exclude": r.get("exclude_ids") or "", "scope": r.get("scope") or ""})
            sid = save(None, data, "Übernahme")
            db.ex("UPDATE update_rules SET migrated=? WHERE id=?", (sid, r["id"]))
            pklog.info("deploy", f"Regel „{data['name']}“ wurde in den Zeitplan #{sid} übernommen.")
        except Exception as e:
            db.ex("UPDATE update_rules SET migrated=-1 WHERE id=?", (r["id"],))
            pklog.warn("deploy", f"Regel „{r.get('name')}“ konnte nicht übernommen werden: {e}")
