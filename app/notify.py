"""Reports by e-mail (subscriptions) and notifications on events.

Report delivery: send one or more existing reports (with their parameters) to recipients at fixed times –
as an HTML table in the body and as a CSV attachment. Triggers as with scan profiles/schedules
(once, interval, daily, weekly, monthly).

Notifications: fixed event types, each can be switched off individually (default: off). Each message is sent only once per
item (e.g. computer + drive); optionally a reminder after N hours and an all-clear when the condition is resolved.
"""
import datetime
import json
import math
import threading
import time

from . import collections_, db, guard, mailer, pklog, profiles, reports, util

TRIGGER_TYPES = ("once", "interval", "daily", "weekly", "monthly")
DEFAULT_OPTIONS = {"csv": True, "skip_empty": False, "row_limit": 200}
_send_lock = threading.Lock()
_busy = set()

E = mailer.E


# =================================================================================================
# Reports by e-mail
# =================================================================================================
def _parse(row):
    if not row:
        return None
    s = dict(row)
    for k, d in (("reports", []), ("triggers", []), ("options", {}), ("state", {})):
        try:
            s[k] = json.loads(s.get(k) or "null") or d
        except ValueError:
            s[k] = d
    s["options"] = {**DEFAULT_OPTIONS, **s["options"]}
    s["enabled"] = bool(s.get("enabled"))
    s["trigger_texts"] = [profiles.describe(t) for t in s["triggers"]]
    names = {r["key"]: r["title"] for r in reports.definitions()}
    s["report_titles"] = [names.get(r.get("key"), r.get("key")) for r in s["reports"]]
    return s


def get(sid):
    return _parse(db.q1("SELECT * FROM mail_reports WHERE id=?", (sid,)))


def all_subscriptions():
    out = [_parse(r) for r in db.q("SELECT * FROM mail_reports ORDER BY name COLLATE NOCASE")]
    now = datetime.datetime.now()
    for s in out:
        s["next"] = next_run(s, now)
        s.pop("state", None)
    return out


def _clean_params(defn, params):
    out = {}
    for p in defn["params"]:
        v = (params or {}).get(p["name"], p.get("default", ""))
        v = "" if v is None else str(v).strip()
        if p["type"] == "number" and v != "":
            try:
                float(v.replace(",", "."))
            except ValueError:
                raise ValueError(f"{defn['title']}: „{p['label']}“ muss eine Zahl sein.")
            v = v.replace(",", ".")
        if p["type"] == "collection" and v:
            if not v.isdigit() or not db.q1("SELECT id FROM collections WHERE id=?", (int(v),)):
                raise ValueError(f"{defn['title']}: Sammlung existiert nicht.")
        out[p["name"]] = v[:200]
    return out


def clean(data):
    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("Name fehlt.")
    reps = []
    for r in data.get("reports") or []:
        defn = reports.get_def((r or {}).get("key"))
        if not defn:
            raise ValueError("Unbekannter Bericht.")
        reps.append({"key": defn["key"], "params": _clean_params(defn, r.get("params"))})
    if not reps:
        raise ValueError("Mindestens einen Bericht auswählen.")
    if len(reps) > 20:
        raise ValueError("Höchstens 20 Berichte je Versand.")
    rec = ", ".join(mailer.parse_recipients(data.get("recipients"), required=False))
    trig = []
    for t in data.get("triggers") or []:
        if t.get("type") not in TRIGGER_TYPES:
            raise ValueError("Für den Berichtsversand sind nur zeitgesteuerte Auslöser möglich.")
        trig.append(profiles.clean_trigger(t))
    o = data.get("options") or {}
    try:
        limit = int(o.get("row_limit", DEFAULT_OPTIONS["row_limit"]))
    except (TypeError, ValueError):
        raise ValueError("Zeilen im Mailtext: Zahl angeben.")
    if not 0 <= limit <= 2000:
        raise ValueError("Zeilen im Mailtext: 0 bis 2000.")
    opts = {"csv": bool(o.get("csv", True)), "skip_empty": bool(o.get("skip_empty")), "row_limit": limit}
    if not opts["csv"] and limit == 0:
        raise ValueError("Ohne CSV-Anhang muss der Mailtext Zeilen enthalten.")
    return {"name": name[:120], "description": (data.get("description") or "").strip()[:1000],
            "enabled": bool(data.get("enabled", True)), "reports": reps, "recipients": rec, "triggers": trig,
            "options": opts}


def _baseline(old, new_triggers, reenabled):
    """New/changed/re-enabled triggers start from now (no immediate sending for past appointments)."""
    state = dict((old or {}).get("state") or {})
    olds = {t["id"]: t for t in (old or {}).get("triggers") or []}
    strip = lambda x: {k: v for k, v in x.items() if k != "id"}  # noqa: E731
    now = util.now()
    out = {}
    for t in new_triggers:
        o = olds.get(t["id"])
        if reenabled or o is None or strip(o) != strip(t) or t["id"] not in state:
            out[t["id"]] = now
        else:
            out[t["id"]] = state[t["id"]]
    return out


def save(sid, data, user=None):
    c = clean(data)
    old = get(sid) if sid else None
    if sid and not old:
        raise ValueError("Berichtsversand nicht gefunden.")
    state = _baseline(old, c["triggers"], bool(old and not old["enabled"] and c["enabled"]))
    vals = (c["name"], c["description"], int(c["enabled"]), json.dumps(c["reports"]), c["recipients"],
            json.dumps(c["triggers"]), json.dumps(c["options"]), json.dumps(state), util.now())
    if sid:
        db.ex("""UPDATE mail_reports SET name=?, description=?, enabled=?, reports=?, recipients=?, triggers=?, options=?,
                 state=?, updated_at=? WHERE id=?""", (*vals, sid))
    else:
        sid = db.ex("""INSERT INTO mail_reports(name, description, enabled, reports, recipients, triggers, options, state,
                       updated_at, created_by, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (*vals, user, util.now()))
    pklog.info("mail", f"Berichtsversand „{c['name']}“ gespeichert.", user=user)
    return get(sid)


def delete(sid, user=None):
    s = get(sid)
    if not s:
        raise ValueError("Berichtsversand nicht gefunden.")
    db.ex("DELETE FROM mail_reports WHERE id=?", (sid,))
    pklog.info("mail", f"Berichtsversand „{s['name']}“ gelöscht.", user=user)


def next_run(s, now):
    """Next appointment (text) – earliest future point in time of the active triggers."""
    if not s["enabled"]:
        return None
    from . import schedules
    best = None
    for t in s["triggers"]:
        if not t.get("enabled", True):
            continue
        try:
            cand = schedules.next_occurrence(t, now)
        except Exception:
            cand = None
        if cand and (best is None or cand < best):
            best = cand
    return util.fmt(best)[:16] if best else None


def build(s):
    """Build the mail from the reports of a delivery → (subject, html, text, attachments, total_rows)."""
    parts, texts, files, total = [], [], [], 0
    limit = s["options"]["row_limit"]
    for r in s["reports"]:
        res = reports.run(r["key"], r["params"])
        total += len(res["rows"])
        ptxt = _param_text(r)
        parts.append(f'<h3 style="font-size:15px;margin:18px 0 2px">{E(res["title"])}</h3>'
                     f'<p style="color:#667;font-size:12px;margin:0 0 4px">{len(res["rows"])} Zeilen'
                     f'{" · " + E(ptxt) if ptxt else ""}</p>')
        if not res["rows"]:
            parts.append('<p style="color:#667;margin:4px 0">Keine Einträge.</p>')
            texts.append(f"== {res['title']} ==\nKeine Einträge.")
        elif limit:
            h, t = mailer.html_table(res["columns"], res["rows"], limit)
            parts.append(h)
            texts.append(f"== {res['title']} ({len(res['rows'])} Zeilen) ==\n{t}")
        else:
            parts.append('<p style="color:#667;margin:4px 0">Siehe CSV-Anhang.</p>')
            texts.append(f"== {res['title']} ({len(res['rows'])} Zeilen) – siehe CSV-Anhang ==")
        if s["options"]["csv"]:
            csv = reports.to_csv(res["columns"], res["rows"]).encode("utf-8")
            files.append((f"pocketknife_{_slug(res['title']) or r['key']}_{util.now()[:10]}.csv", csv, "text/csv"))
    if s.get("description"):
        parts.insert(0, f'<p style="margin:0 0 6px">{E(s["description"])}</p>')
    body = mailer.wrap(s["name"], "".join(parts))
    return s["name"], body, "\n\n".join(texts), files, total


def _slug(t):
    import re as _re
    t = (t or "").lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    return _re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:50]


def _param_text(r):
    defn = reports.get_def(r["key"])
    out = []
    for p in defn["params"] if defn else []:
        v = r["params"].get(p["name"])
        if not v:
            continue
        if p["type"] == "collection":
            v = db.val("SELECT name FROM collections WHERE id=?", (int(v),), v)
        out.append(f"{p['label']}: {v}")
    return ", ".join(out)


def send_subscription(sid, reason="Zeitplan", user=None, to_override=None):
    s = get(sid)
    if not s:
        raise ValueError("Berichtsversand nicht gefunden.")
    with _send_lock:
        if sid in _busy:
            raise ValueError("Dieser Versand läuft gerade.")
        _busy.add(sid)
    try:
        subject, body, text, files, total = build(s)
        if total == 0 and s["options"]["skip_empty"] and not to_override:
            msg = "Übersprungen – alle Berichte leer"
            db.ex("UPDATE mail_reports SET last_status=?, last_run=? WHERE id=?", (msg, util.now(), sid))
            pklog.info("mail", f"Berichtsversand „{s['name']}“: {msg}.", user=user)
            return {"skipped": True}
        to = to_override or mailer.recipients_or_default(s["recipients"])
        r = mailer.send(to, subject, body, text, files, user=user)
        db.ex("UPDATE mail_reports SET last_status=?, last_run=?, last_sent=? WHERE id=?",
              (f"Gesendet an {len(r['to'])} Empfänger ({reason})", util.now(), util.now(), sid))
        return r
    except Exception as e:
        db.ex("UPDATE mail_reports SET last_status=?, last_run=? WHERE id=?", (f"FEHLER: {e}"[:500], util.now(), sid))
        pklog.error("mail", f"Berichtsversand „{s['name']}“ fehlgeschlagen: {e}", user=user)
        raise
    finally:
        _busy.discard(sid)


def evaluate():
    """Every minute from the scheduler: start due deliveries."""
    if not mailer.configured():
        return
    now = datetime.datetime.now()
    for row in db.q("SELECT * FROM mail_reports WHERE enabled=1"):
        s = _parse(row)
        due = None
        state = dict(s["state"])
        for t in s["triggers"]:
            if not t.get("enabled", True):
                continue
            end = util.parse(t.get("end"))
            if end and now > end:
                continue
            occ = profiles.last_occurrence(t, now)
            last = util.parse(state.get(t["id"]))
            if profiles.fires(t, occ, last, now):
                state[t["id"]] = util.fmt(now)
                due = due or t
        if due:
            db.ex("UPDATE mail_reports SET state=? WHERE id=?", (json.dumps(state), s["id"]))
            threading.Thread(target=_send_bg, args=(s["id"], profiles.describe(due)), daemon=True,
                             name=f"mail-{s['id']}").start()


RETRY_MINUTES = (5, 15, 30)  # 2.37: retry report delivery after an error (e.g. SMTP briefly unavailable)


def _send_bg(sid, reason, sleep=time.sleep):
    for n in range(len(RETRY_MINUTES) + 1):
        try:
            send_subscription(sid, reason if not n else f"{reason}, {n + 1}. Versuch")
            return
        except ValueError as e:  # e.g. "läuft gerade" (already running) – not logged anywhere else
            pklog.warn("mail", f"Berichtsversand #{sid} ({reason}) übersprungen: {e}")
            return
        except Exception as e:  # already logged
            if n >= len(RETRY_MINUTES) or not db.val("SELECT enabled FROM mail_reports WHERE id=?", (sid,)):
                return
            wait = RETRY_MINUTES[n]
            db.ex("UPDATE mail_reports SET last_status=? WHERE id=?",
                  (f"FEHLER: {e} – neuer Versuch in {wait} Minuten"[:500], sid))
            sleep(wait * 60)


# =================================================================================================
# Notifications
# =================================================================================================
ALERTS = [
    {"key": "low_disk", "title": "Wenig Speicherplatz",
     "desc": "Ein Laufwerk unterschreitet den freien Speicher (Werte aus dem letzten Inventar-Scan).",
     "params": [{"name": "gb", "label": "Frei weniger als (GB)", "type": "number", "default": "10"},
                {"name": "pct", "label": "oder weniger als (%)", "type": "number", "default": "0", "hint": "0 = nicht prüfen"},
                {"name": "drives", "label": "Nur Laufwerke", "type": "text", "default": "", "hint": "z. B. C: – leer = alle"},
                {"name": "collection", "label": "Sammlung", "type": "collection", "default": ""}],
     "resolvable": True},
    {"key": "offline", "title": "Computer lange offline",
     "desc": "Ein Computer wurde seit einigen Tagen nicht mehr im Netz gesehen (Heartbeat).",
     "params": [{"name": "days", "label": "Offline seit mindestens (Tage)", "type": "number", "default": "14"},
                {"name": "collection", "label": "Sammlung", "type": "collection", "default": ""}],
     "resolvable": True},
    {"key": "scan_failed", "title": "Scan fehlgeschlagen",
     "desc": "Der letzte Inventar-Scan eines Computers ist fehlgeschlagen.",
     "params": [{"name": "collection", "label": "Sammlung", "type": "collection", "default": ""}],
     "resolvable": True},
    {"key": "deploy_failed", "title": "Bereitstellung fehlgeschlagen",
     "desc": "Eine Bereitstellung ist ganz oder teilweise fehlgeschlagen (eine Mail je Bereitstellung).",
     "params": [{"name": "partial", "label": "Auch teilweise fehlgeschlagene", "type": "bool", "default": "1"}],
     "resolvable": False},
    {"key": "guard", "title": "Dienstkonto-Schutz ausgelöst",
     "desc": "Automatische Zugriffe wurden pausiert, weil das Dienstkonto abgelehnt wurde (Sperrgefahr).",
     "params": [], "resolvable": True},
    {"key": "breakglass", "title": "Notfallzugang benutzt",
     "desc": "Jede Anmeldung über den Notfallzugang (PK_BREAKGLASS_USER) und gehäufte Fehlversuche dafür.",
     "params": [], "resolvable": False},
    {"key": "backup_failed", "title": "Sicherung fehlgeschlagen",
     "desc": "Die automatische Sicherung der Datenbank ist fehlgeschlagen.",
     "params": [], "resolvable": False},
]
ALERTS_BY_KEY = {a["key"]: a for a in ALERTS}
REPORT_ALERT = "report:"
_report_checked = {}  # key → time of the last check (custom reports not every minute)


def all_alerts():
    """Fixed notifications + one per custom report ("Mail, wenn der Bericht Zeilen liefert")."""
    from . import customreports
    out = list(ALERTS)
    for r in customreports.all_reports():
        out.append({"key": f"{REPORT_ALERT}{r['id']}", "title": f"Eigener Bericht: {r['name']}", "custom": True, "report": r["key"],
                    "desc": "Mail, sobald der Bericht Zeilen liefert – jede Zeile wird einmal gemeldet "
                            "(Zahlen und Datumswerte einer Zeile dürfen sich ändern, ohne dass sie erneut gemeldet wird).",
                    "params": [{"name": "interval", "label": "Prüfen alle (Minuten)", "type": "number", "default": "15",
                                "hint": "Eigene Berichte können aufwendig sein – nicht öfter als nötig prüfen (mind. 5)."},
                               {"name": "collection", "label": "Nur Sammlung", "type": "collection", "default": ""}],
                    "resolvable": True})
    return out


def alert_def(key):
    if key in ALERTS_BY_KEY:
        return ALERTS_BY_KEY[key]
    if str(key).startswith(REPORT_ALERT):
        return next((a for a in all_alerts() if a["key"] == key), None)
    return None
DEFAULT_ALERT = {"enabled": False, "recipients": "", "params": {}, "repeat_hours": 0, "resolved": True}
MAX_ITEMS = 200  # rows per notification


def alert_config(key):
    a = alert_def(key)
    if not a:
        raise ValueError("Unbekannte Benachrichtigung.")
    row = db.q1("SELECT * FROM mail_alerts WHERE key=?", (key,)) or {}
    try:
        params = json.loads(row.get("params") or "{}")
    except ValueError:
        params = {}
    defaults = {p["name"]: p["default"] for p in a["params"]}
    if key == "low_disk":
        defaults["gb"] = str(db.setting("low_disk_gb"))
    return {"key": key, "enabled": bool(row.get("enabled")), "recipients": row.get("recipients") or "",
            "params": {**defaults, **{k: v for k, v in params.items() if k in defaults}},
            "repeat_hours": int(row.get("repeat_hours") or 0), "resolved": bool(row.get("resolved", 1)),
            "since": row.get("since"), "last_sent": row.get("last_sent"), "last_status": row.get("last_status"),
            "active": db.val("SELECT COUNT(*) FROM mail_alert_state WHERE alert=?", (key,), 0) if a["resolvable"] else None}


def alerts_overview():
    return [{**{k: v for k, v in a.items() if k != "params"}, "param_defs": a["params"], **alert_config(a["key"])}
            for a in all_alerts()]


def save_alert(key, data, user=None):
    a = alert_def(key)
    if not a:
        raise ValueError("Unbekannte Benachrichtigung.")
    old = alert_config(key)
    params = _clean_alert_params(a, data, old["params"])
    try:
        rep = int(data.get("repeat_hours") or 0)
    except (TypeError, ValueError):
        raise ValueError("Erinnerung: ganze Stunden angeben.")
    if not 0 <= rep <= 24 * 365:
        raise ValueError("Erinnerung: 0 bis 8760 Stunden.")
    rec = ", ".join(mailer.parse_recipients(data.get("recipients"), required=False))
    enabled = bool(data.get("enabled"))
    since = old["since"] if old["enabled"] and enabled else (util.now() if enabled else None)
    scope = lambda pp: {k: v for k, v in pp.items() if k not in ("interval", "partial")}  # noqa: E731
    if scope(old["params"]) != scope(params):
        # Different limits: re-evaluate open messages (otherwise stale entries stay "active")
        db.ex("DELETE FROM mail_alert_state WHERE alert=?", (key,))
    db.ex("""INSERT INTO mail_alerts(key, enabled, recipients, params, repeat_hours, resolved, since) VALUES(?,?,?,?,?,?,?)
             ON CONFLICT(key) DO UPDATE SET enabled=excluded.enabled, recipients=excluded.recipients, params=excluded.params,
             repeat_hours=excluded.repeat_hours, resolved=excluded.resolved, since=excluded.since""",
          (key, int(enabled), rec, json.dumps(params), rep, int(bool(data.get("resolved", True))), since))
    if not enabled:
        db.ex("DELETE FROM mail_alert_state WHERE alert=?", (key,))
    pklog.info("mail", f"Benachrichtigung „{a['title']}“ {'aktiviert' if enabled else 'deaktiviert'}.", user=user)
    return alert_config(key)


def preview_alert(key, data):
    """What would be reported now with these (not yet saved) settings?"""
    a = alert_def(key)
    if not a:
        raise ValueError("Unbekannte Benachrichtigung.")
    cfg = alert_config(key)
    cfg["params"] = _clean_alert_params(a, data, cfg["params"])
    if not cfg["since"]:
        cfg["since"] = util.ago(days=7)  # preview for events: the last 7 days
    return _check_one(a, cfg, True)


def _clean_alert_params(a, data, base=None):
    key = a["key"]
    params = {}
    for p in a["params"]:
        v = str((data.get("params") or {}).get(p["name"], (base or {}).get(p["name"], p["default"])) or "").strip()
        if p["type"] == "number":
            v = v.replace(",", ".") or "0"
            try:
                n = float(v)
            except ValueError:
                raise ValueError(f"{p['label']}: Zahl angeben.")
            if not math.isfinite(n) or n < 0 or n > 100000:
                raise ValueError(f"{p['label']}: ungültiger Wert.")
        if p["type"] == "bool":
            v = "1" if v in ("1", "true", "True", "on") else "0"
        if p["type"] == "collection" and v and (not v.isdigit() or not db.q1("SELECT id FROM collections WHERE id=?", (int(v),))):
            raise ValueError("Sammlung existiert nicht.")
        if p["name"] == "drives":
            v = ", ".join(sorted({d.strip().upper().rstrip(":\\") + ":" for d in v.replace(";", ",").split(",") if d.strip()}))
            if any(len(d) != 2 or not d[0].isalpha() for d in v.split(", ") if d):
                raise ValueError("Laufwerke: Buchstaben angeben, z. B. C:, D:")
        params[p["name"]] = v[:200]
    if key == "low_disk" and float(params["gb"] or 0) <= 0 and float(params["pct"] or 0) <= 0:
        raise ValueError("GB- oder Prozent-Grenze angeben.")
    if key == "offline" and float(params["days"] or 0) < 1:
        raise ValueError("Mindestens 1 Tag.")
    if a.get("custom") and float(params.get("interval") or 0) < 5:
        raise ValueError("Prüfen: mindestens alle 5 Minuten.")
    return params


def _coll(params):
    cid = params.get("collection")
    if cid:
        return collections_.where_for(int(cid))
    return "1=1", []


# Each check returns {item_key: {column: value}} – the items that currently apply
def _check_low_disk(p, cfg):
    w, a = _coll(p)
    gb = float(p.get("gb") or 0)
    pct = float(p.get("pct") or 0)
    drives = [d for d in (p.get("drives") or "").split(", ") if d]
    conds, args = [], []
    if gb > 0:
        conds.append("d.free < ?")
        args.append(gb * 1073741824)
    if pct > 0:
        conds.append("100.0*d.free/NULLIF(d.size,0) < ?")
        args.append(pct)
    dsql = f" AND UPPER(d.drive) IN ({','.join('?' * len(drives))})" if drives else ""
    rows = db.q(f"""SELECT c.id cid, c.name computer, d.drive, d.label, d.size, d.free, c.last_scan
                   FROM disks d JOIN computers c ON c.id=d.computer_id
                   WHERE {w} AND d.size > 0 AND ({' OR '.join(conds)}){dsql} ORDER BY d.free""",
                a + args + drives)
    return {f"{r['cid']}:{(r['drive'] or '').upper()}": {
        "computer": r["computer"], "drive": r["drive"], "label": r["label"] or "",
        "free": util_fmt_gb(r["free"]), "size": util_fmt_gb(r["size"]),
        "pct": f"{100.0 * r['free'] / r['size']:.1f} %".replace(".", ","), "scan": (r["last_scan"] or "")[:16]} for r in rows}


def _resolved_low_disk(p, key):
    """Hysteresis: only treat as resolved once back 10 % above the limit (avoids flapping around the threshold)."""
    cid, drive = key.split(":", 1)
    r = db.q1("SELECT size, free FROM disks WHERE computer_id=? AND UPPER(drive)=?", (int(cid), drive))
    if not r or not r["size"]:
        return True  # drive/computer no longer exists
    gb, pct = float(p.get("gb") or 0), float(p.get("pct") or 0)
    ok_gb = gb <= 0 or r["free"] >= gb * 1.1 * 1073741824
    ok_pct = pct <= 0 or 100.0 * r["free"] / r["size"] >= pct * 1.1
    return ok_gb and ok_pct


def util_fmt_gb(b):
    return f"{(b or 0) / 1073741824:.1f} GB".replace(".", ",")


def _check_offline(p, cfg):
    w, a = _coll(p)
    days = float(p.get("days") or 14)
    rows = db.q(f"""SELECT c.id, c.name, c.last_seen, c.ip_addresses, c.current_user FROM computers c
                   WHERE {w} AND c.online=0 AND c.last_seen IS NOT NULL AND c.last_seen < ?
                   AND COALESCE(c.ad_enabled,1)=1 AND COALESCE(c.ad_present,1)=1 ORDER BY c.last_seen""",
                a + [util.ago(days=days)])
    return {str(r["id"]): {"computer": r["name"], "last_seen": (r["last_seen"] or "")[:16],
                           "user": r["current_user"] or "", "ip": r["ip_addresses"] or ""} for r in rows}


def _check_scan_failed(p, cfg):
    w, a = _coll(p)
    rows = db.q(f"""SELECT c.id, c.name, c.last_scan_attempt, c.scan_error FROM computers c
                   WHERE {w} AND (c.scan_status='Fehler' OR (c.scan_status='Läuft' AND c.scan_error IS NOT NULL))
                   ORDER BY c.name""", a)  # do not report "Behoben" (resolved) during the retry
    return {str(r["id"]): {"computer": r["name"], "attempt": (r["last_scan_attempt"] or "")[:16],
                           "error": (r["scan_error"] or "")[:300]} for r in rows}


def _check_deploy_failed(p, cfg):
    st = ("failed", "partial") if p.get("partial", "1") == "1" else ("failed",)
    rows = db.q(f"""SELECT d.id, d.name, d.status, d.finished_at, d.created_by,
                    (SELECT COUNT(*) FROM deployment_targets t WHERE t.deployment_id=d.id AND t.status='failed') failed,
                    (SELECT COUNT(*) FROM deployment_targets t WHERE t.deployment_id=d.id) total,
                    (SELECT GROUP_CONCAT(computer_name, ', ') FROM (SELECT computer_name FROM deployment_targets t
                       WHERE t.deployment_id=d.id AND t.status='failed' LIMIT 15)) names
                    FROM deployments d WHERE d.status IN ({','.join('?' * len(st))}) AND d.finished_at >= ?
                    AND d.finished_at >= ? ORDER BY d.finished_at""", (*st, cfg["since"] or util.now(), util.ago(days=170)))
    return {str(r["id"]): {"name": r["name"] or f"#{r['id']}", "status": {"failed": "fehlgeschlagen", "partial": "teilweise"}[r["status"]],
                           "result": f"{r['failed']} von {r['total']} fehlgeschlagen", "computers": r["names"] or "",
                           "finished": (r["finished_at"] or "")[:16], "by": r["created_by"] or ""} for r in rows}


def _check_guard(p, cfg):
    s = guard.status()
    if not s["paused"]:
        return {}
    return {"guard": {"reason": s["reason"] or "", "source": s["source"] or "", "since": s["since"] or "",
                      "until": s["until"] or ""}}


def _check_backup_failed(p, cfg):
    last = db.setting_raw("backup_last")
    if "FEHLGESCHLAGEN" not in last:
        return {}
    ts = last[:19]
    if cfg["since"] and ts < cfg["since"][:19]:
        return {}
    # One entry per day: the automatic backup is retried every 10 minutes – otherwise a mail would be sent every time
    return {ts[:10]: {"time": ts[:16], "error": last.split("FEHLGESCHLAGEN:", 1)[-1].strip()[:300]}}


def _check_breakglass(p, cfg):
    """Audit 2.29: use and failed attempts of the emergency access (from the log)."""
    rows = db.q("""SELECT id, ts, message FROM logs WHERE category='auth' AND message LIKE 'Notfallzugang:%' AND ts >= ?
                   ORDER BY id LIMIT 200""", (cfg["since"] or util.now(),))
    return {str(r["id"]): {"time": (r["ts"] or "")[:16], "event": r["message"][len("Notfallzugang:"):].strip()[:300]} for r in rows}


CHECKS = {
    "low_disk": (_check_low_disk, [("computer", "Computer"), ("drive", "Laufwerk"), ("label", "Bezeichnung"),
                                   ("free", "Frei"), ("pct", "Frei %"), ("size", "Größe"), ("scan", "Stand (Scan)")]),
    "offline": (_check_offline, [("computer", "Computer"), ("last_seen", "Zuletzt gesehen"), ("user", "Letzter Benutzer"),
                                 ("ip", "IP")]),
    "scan_failed": (_check_scan_failed, [("computer", "Computer"), ("attempt", "Versuch"), ("error", "Fehler")]),
    "deploy_failed": (_check_deploy_failed, [("name", "Bereitstellung"), ("status", "Status"), ("result", "Ergebnis"),
                                             ("computers", "Fehlgeschlagen auf"), ("finished", "Beendet"), ("by", "Gestartet von")]),
    "guard": (_check_guard, [("reason", "Grund"), ("source", "Auslöser"), ("since", "Seit"), ("until", "Pausiert bis")]),
    "backup_failed": (_check_backup_failed, [("time", "Zeit"), ("error", "Fehler")]),
    "breakglass": (_check_breakglass, [("time", "Zeit"), ("event", "Ereignis")]),
}


def _check_report(a, p):
    """Custom report: each row is a case. Identifier = computer + text/yes-no columns (numbers, dates and sizes
    may change). Same identifier multiple times → numbered so that no row is swallowed.
    → (cases, columns, truncated)."""
    import hashlib
    from . import customreports
    r = customreports.get(int(a["key"][len(REPORT_ALERT):]))
    if not r:
        return {}, [], False
    # Without the row limit of the report – otherwise rows "disappear" as soon as new ones are sorted in before them
    d = {**r["definition"], "limit": customreports.MAX_LIMIT}
    res = customreports.execute(d, {"collection": p.get("collection") or ""})
    cols = [(c["key"], c["label"]) for c in res["columns"]]
    stable = [c["key"] for c in res["columns"] if c["type"] not in ("number", "date", "bytes") and c["key"] != "cnt"]
    out, seen = {}, {}
    for row in res["rows"]:
        base = json.dumps([row.get("_cid")] + [row.get(k) for k in stable], ensure_ascii=False, default=str)
        n = seen.get(base, 0)
        seen[base] = n + 1
        ident = base if n == 0 else f"{base}#{n}"
        out[hashlib.sha1(ident.encode()).hexdigest()[:20]] = {k: ("" if row.get(k) is None else row.get(k)) for k, _l in cols}
    return out, cols, res["truncated"]


def rebaseline_report(rid):
    """After a change to the custom report: silently adopt the current rows as known (no flood of
    "neu" and "behoben" just because the rows' identifier changed through other columns)."""
    key = f"{REPORT_ALERT}{rid}"
    if not db.val("SELECT enabled FROM mail_alerts WHERE key=?", (key,), 0):
        return
    a = alert_def(key)
    if not a:
        return
    current, _cols, _trunc = _check_report(a, alert_config(key)["params"])
    now = util.now()
    with db.tx():
        db.ex("DELETE FROM mail_alert_state WHERE alert=?", (key,))
        db.exmany("INSERT INTO mail_alert_state(alert, item, label, first_seen, last_sent) VALUES(?,?,?,?,?)",
                  [(key, k, _label(key, v), now, now) for k, v in current.items()])


def _in_scope_low_disk(p, item):
    """Does the drive still belong to the scope of the notification (collection, drive filter)?"""
    cid, drive = item.split(":", 1)
    drives = [d for d in (p.get("drives") or "").split(", ") if d]
    if drives and drive not in drives:
        return False
    if p.get("collection"):
        try:
            return int(cid) in collections_.member_ids(int(p["collection"]))
        except Exception:
            return True
    return True


def _is_resolved(key, p, item, current):
    if key == "low_disk":
        return item not in current and (_resolved_low_disk(p, item) or not _in_scope_low_disk(p, item))
    return item not in current


_check_lock = threading.Lock()
_backoff = {}  # key → (paused until, failed attempts) after send errors
_last_error = {}  # key → last logged error text (do not log the same thing every minute)


def check_alerts(only=None, dry=False):
    """Check all active notifications and send new/reminded/resolved messages.
    dry=True: send/save nothing, only return the result (preview in the UI).
    Only one run at a time (minute tick and "Jetzt prüfen") – otherwise the same cases would go out twice."""
    if dry:
        return _check_alerts(only, True)
    with _check_lock:
        return _check_alerts(only, False)


def _check_alerts(only, dry):
    if not dry and not mailer.configured():
        return {}
    out = {}
    enabled = {r["key"] for r in db.q("SELECT key FROM mail_alerts WHERE enabled=1")}
    for a in all_alerts():
        key = a["key"]
        if only and key != only:
            continue
        if key not in enabled and not dry:
            continue
        cfg = alert_config(key)
        if a.get("custom") and not dry and not only:
            iv = max(5, int(float(cfg["params"].get("interval") or 15)))
            if time.time() - _report_checked.get(key, 0) < iv * 60:
                continue
            _report_checked[key] = time.time()
        if not dry and not only and _backoff.get(key, (0, 0))[0] > time.time():
            continue  # pause briefly after send errors (1, 2, 4 … up to 60 minutes)
        try:
            out[key] = _check_one(a, cfg, dry)
            if not dry:
                if out[key].get("error"):
                    n = _backoff.get(key, (0, 0))[1] + 1
                    _backoff[key] = (time.time() + min(60, 2 ** (n - 1)) * 60, n)
                else:
                    _backoff.pop(key, None)
                    _last_error.pop(key, None)
        except Exception as e:
            if _last_error.get(key) != str(e):
                pklog.error("mail", f"Benachrichtigung „{a['title']}“: Prüfung fehlgeschlagen: {e}")
                _last_error[key] = str(e)
            if not dry:
                db.ex("UPDATE mail_alerts SET last_status=? WHERE key=?", (f"FEHLER: {e}"[:500], key))
    return out


def _check_one(a, cfg, dry):
    key = a["key"]
    p = cfg["params"]
    truncated = False
    if a.get("custom"):
        current, cols, truncated = _check_report(a, p)
    else:
        fn, cols = CHECKS[key]
        current = fn(p, cfg)
    state = {r["item"]: r for r in db.q("SELECT * FROM mail_alert_state WHERE alert=?", (key,))}
    now = util.now()
    new = {k: v for k, v in current.items() if k not in state}
    remind = {}
    if cfg["repeat_hours"] and a["resolvable"]:
        lim = util.ago(hours=cfg["repeat_hours"])
        remind = {k: v for k, v in current.items() if k in state and (state[k]["last_sent"] or "") < lim}
    # Truncated result: missing rows are not "behoben" (resolved), just no longer counted
    resolved = [] if truncated else [k for k in state if a["resolvable"] and _is_resolved(key, p, k, current)]
    if dry:
        return {"current": len(current), "new": len(new), "remind": len(remind), "resolved": len(resolved),
                "rows": list(current.values())[:50], "columns": [{"key": k, "label": l} for k, l in cols]}
    if not (new or remind or (resolved and cfg["resolved"])):
        for k in resolved:
            db.ex("DELETE FROM mail_alert_state WHERE alert=? AND item=?", (key, k))
        return {"sent": False}
    columns = [{"key": k, "label": l} for k, l in cols]
    parts, texts = [], []
    if new:
        rows = list(new.values())
        h, t = mailer.html_table(columns, rows, MAX_ITEMS)
        parts.append(f'<h3 style="font-size:15px;margin:12px 0 2px;color:#c0392b">Neu ({len(rows)})</h3>{h}')
        texts.append(f"NEU ({len(rows)}):\n{t}")
    if remind:
        rows = list(remind.values())
        h, t = mailer.html_table(columns, rows, MAX_ITEMS)
        parts.append(f'<h3 style="font-size:15px;margin:12px 0 2px;color:#b9770e">Weiterhin offen ({len(rows)})</h3>{h}')
        texts.append(f"WEITERHIN OFFEN ({len(rows)}):\n{t}")
    if resolved and cfg["resolved"]:
        names = [_resolved_label(key, k, state[k]) for k in resolved]
        parts.append(f'<h3 style="font-size:15px;margin:12px 0 2px;color:#1e8449">Behoben ({len(names)})</h3>'
                     f'<p style="margin:2px 0">{E(", ".join(names[:MAX_ITEMS]))}</p>')
        texts.append(f"BEHOBEN ({len(names)}): " + ", ".join(names))
    n_new, n_rem, n_res = len(new), len(remind), len(resolved) if cfg["resolved"] else 0
    bits = [f"{n_new} neu" if n_new else "", f"{n_rem} offen" if n_rem else "", f"{n_res} behoben" if n_res else ""]
    subject = f"{a['title']}: " + ", ".join(b for b in bits if b)
    body = mailer.wrap(a["title"], f'<p style="margin:0 0 6px;color:#445">{E(a["desc"])}</p>{"".join(parts)}'
                       f'<p style="color:#8a93a0;font-size:12px;margin-top:12px">Diese Benachrichtigung lässt sich unter '
                       f'Einstellungen → E-Mail abschalten.</p>')
    try:
        to = mailer.recipients_or_default(cfg["recipients"])
        mailer.send(to, subject, body, "\n\n".join(texts), category="mail")
    except Exception as e:
        db.ex("UPDATE mail_alerts SET last_status=? WHERE key=?", (f"FEHLER: {e}"[:500], key))
        if _last_error.get(key) != str(e):
            pklog.error("mail", f"Benachrichtigung „{a['title']}“ nicht gesendet: {e} (weitere Versuche mit wachsendem Abstand)")
            _last_error[key] = str(e)
        return {"sent": False, "error": str(e)}
    with db.tx():
        for k, v in {**new, **remind}.items():
            db.ex("""INSERT INTO mail_alert_state(alert, item, label, first_seen, last_sent) VALUES(?,?,?,?,?)
                     ON CONFLICT(alert, item) DO UPDATE SET last_sent=excluded.last_sent, label=excluded.label""",
                  (key, k, _label(key, v), now, now))
        for k in resolved:
            db.ex("DELETE FROM mail_alert_state WHERE alert=? AND item=?", (key, k))
    db.ex("UPDATE mail_alerts SET last_sent=?, last_status=? WHERE key=?", (now, subject, key))
    return {"sent": True, "subject": subject}


def _label(key, v):
    if str(key).startswith(REPORT_ALERT):
        return " · ".join(str(x) for x in list(v.values())[:3] if x not in (None, ""))[:200]
    if key == "low_disk":
        return f"{v['computer']} {v['drive']}"
    if key in ("offline", "scan_failed"):
        return v["computer"]
    if key == "deploy_failed":
        return v["name"]
    return v.get("reason") or v.get("time") or key


def _resolved_label(key, item, st):
    return st.get("label") or item


def cleanup():
    """Remove old entries of messages that cannot be resolved (deployments, backups) after 180 days."""
    db.ex("DELETE FROM mail_alert_state WHERE alert IN ('deploy_failed','backup_failed','breakglass') AND first_seen < ?",
          (util.ago(days=180),))
    db.ex("DELETE FROM mail_alert_state WHERE alert NOT IN (SELECT key FROM mail_alerts WHERE enabled=1)")
