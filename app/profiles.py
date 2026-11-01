"""Scanprofile: Auswahl aus Standard- und eigenen Scannern, Sammlungen und Auslöser.

Auslöser (JSON-Liste im Profil, jeder mit eigener id):
  once      {at: "YYYY-MM-DD HH:MM"}
  interval  {every: n, unit: "m"|"h"|"d", start: "YYYY-MM-DD HH:MM"}
  daily     {time: "HH:MM", every: n}                      jeden n-ten Tag
  weekly    {time: "HH:MM", days: [0..6] (Mo=0), every: n}   jede n-te Woche
  monthly   {time: "HH:MM", days: [1..31, "last"]}
  scan_age  {days, hours, minutes, only_at: bool, from: "HH:MM", to: "HH:MM"}
  heartbeat {}                                             wenn ein Computer online kommt
Gemeinsam: enabled, catch_up (offline Computer nachholen, sobald sie online sind), end (optional).
"""
import datetime
import json
import secrets

from . import db, util

TRIGGER_TYPES = {
    "once": "Einmalig", "interval": "Intervall", "daily": "Täglich", "weekly": "Wöchentlich",
    "monthly": "Monatlich", "scan_age": "Scan-Alter", "heartbeat": "Heartbeat",
}
WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
ALL_COMPUTERS = 0  # Pseudo-Sammlung „Alle Computer“
RETRY_MINUTES = 60


# --- Laden / Speichern -------------------------------------------------------------
def _parse(row):
    if not row:
        return None
    p = dict(row)
    for k, default in (("modules", []), ("scanners", []), ("collections", []), ("computers", []), ("triggers", []), ("scanner_types", [])):
        try:
            p[k] = json.loads(p.get(k) or "null") or default
        except ValueError:
            p[k] = default
    valid = {r["id"] for r in db.q("SELECT id FROM custom_scanners")}
    p["scanners"] = [i for i in p["scanners"] if i in valid]  # gelöschte eigene Scanner ausblenden
    return p


SCANNER_TYPES = {"registry": "Registry", "files": "Dateien & Ordner", "wmi": "WMI", "powershell": "PowerShell"}


def custom_ids(p):
    """Eigene Scanner eines Profils: fest gewählte + alle (aktiven) Scanner der gewählten Typen."""
    ids = list(p.get("scanners") or [])
    types = [t for t in (p.get("scanner_types") or []) if t in SCANNER_TYPES]
    if types:
        ph = ",".join("?" * len(types))
        ids += [r["id"] for r in db.q(f"SELECT id FROM custom_scanners WHERE enabled=1 AND type IN ({ph})", types)]
    return sorted(set(ids))


# Feste Scanprofile: Schlüssel, Name, Beschreibung, Standard-Scanner, Typen eigener Scanner.
# Sie können nicht gelöscht und in ihren Scannern nicht verändert werden; Auslöser und Sammlungen sind frei.
BUILTIN_PROFILES = [
    ("active_directory", "Active Directory", "AD-Daten des Computers (OU, Gruppen, Beschreibung, letzte Anmeldung) – ohne Zugriff auf den Client.", ["active_directory"], []),
    ("applications", "Anwendungen", "Installierte Software (Computer und Benutzer).", ["applications"], []),
    ("computer", "Computer-Details", "Allgemein, Betriebssystem, System, CPU, Sicherheit, Produktschlüssel und Prozesse.",
     ["computer", "cpu", "security", "product_keys", "processes"], []),
    ("disks", "Laufwerke", "Logische und physische Laufwerke.", ["disk_drives"], []),
    ("displays", "Bildschirme", "Angeschlossene Monitore.", ["displays"], []),
    ("environment", "Umgebungsvariablen", "System-Umgebungsvariablen.", ["environment"], []),
    ("files", "Dateien & Ordner", "Alle eigenen Scanner vom Typ „Dateien & Ordner“.", [], ["files"]),
    ("hardware", "Hardware", "Geräte-Manager.", ["hardware"], []),
    ("hotfixes", "Hotfixes", "Installierte Windows-Updates.", ["hotfixes"], []),
    ("memory", "Speichermodule", "RAM-Module.", ["memory"], []),
    ("nics", "Netzwerkkarten", "Netzwerkadapter, IP- und MAC-Adressen.", ["nics"], []),
    ("powershell", "PowerShell", "Alle eigenen Scanner vom Typ „PowerShell“.", [], ["powershell"]),
    ("printers", "Drucker (lokal)", "Installierte Drucker.", ["printers"], []),
    ("registry", "Registry", "Alle eigenen Scanner vom Typ „Registry“.", [], ["registry"]),
    ("services", "Dienste", "Windows-Dienste.", ["services"], []),
    ("shares", "Freigaben", "Freigegebene Ordner.", ["shares"], []),
    ("storage", "Speicherplatz", "Ordnergrößen der festen Laufwerke als Diagramm (wie das Tool „Speicherplatz analysieren“). Belastet den Client stärker und ist daher nicht im Standardscan – über Auslöser und Sammlungen gezielt einplanen.", ["disk_usage"], []),
    ("users_groups", "Benutzer & Gruppen", "Lokale Benutzer, Gruppen und Administratoren.", ["local_groups", "local_users"], []),
    ("features", "Windows-Features", "Optionale Features und Serverrollen.", ["features"], []),
    ("tasks", "Geplante Aufgaben", "Aufgabenplanung.", ["tasks"], []),
    ("wmi", "WMI", "Alle eigenen Scanner vom Typ „WMI“.", [], ["wmi"]),
]
# Standard: alle Scanner außer Dateien & Ordner, PowerShell, Registry und WMI
STANDARD_KEYS = ["active_directory", "applications", "computer", "disks", "displays", "environment", "hardware",
                 "hotfixes", "memory", "nics", "printers", "services", "shares", "users_groups", "features", "tasks"]
BUILTIN = {k: (n, d, m, t) for k, n, d, m, t in BUILTIN_PROFILES}
BUILTIN["standard"] = ("Standard", "Standard-Scan für die grundlegenden Inventardaten.",
                       [m for k in STANDARD_KEYS for m in BUILTIN[k][2]], [])


def builtin_def(key):
    return BUILTIN.get(key)


def _keep_custom(pid, key, mods, types, now):
    """Wurde ein bisheriges Profil gleichen Namens angepasst, eine Kopie mit dem alten Inhalt behalten."""
    from . import inventory
    old = _parse(db.q1("SELECT * FROM scan_profiles WHERE id=?", (pid,)))
    if not old:
        return
    known = [set(mods)]
    if key == "standard":
        known.append(set(inventory.MODULE_ORDER))       # Standard bis 2.2: alle Scanner
    if key == "computer":
        known.append(set(mods) - {"processes"})         # Computer-Details bis 2.2
    same_mods = set(old["modules"]) in known
    same_types = set(old.get("scanner_types") or []) == set(types)
    extra_scanners = old["scanners"] and key != "standard"
    if same_mods and same_types and not extra_scanners:
        return
    db.ex("INSERT INTO scan_profiles(name, description, modules, scanners, scanner_types, collections, triggers, is_default, "
          "enabled, created_at, updated_at) VALUES(?,?,?,?,?,?,'[]',0,1,?,?)",
          (old["name"] + " (eigene Anpassung)", (old.get("description") or "") + " – Kopie vor der Umstellung auf feste Profile.",
           json.dumps(old["modules"]), json.dumps(old["scanners"]), json.dumps(old.get("scanner_types") or []),
           json.dumps(old["collections"]), now, now))


def seed_builtin():
    """Feste Profile anlegen bzw. auf den vorgegebenen Inhalt setzen (bei jedem Start, idempotent)."""
    now = util.now()
    rows = db.q("SELECT id, name, builtin_key, is_default FROM scan_profiles")
    by_key = {r["builtin_key"]: r for r in rows if r["builtin_key"]}
    by_name = {r["name"].lower(): r for r in rows if not r["builtin_key"]}
    for key, (name, desc, mods, types) in BUILTIN.items():
        r = by_key.get(key)
        if not r:
            if key == "standard":
                r = by_name.get("standard") or next((x for x in rows if x["is_default"] and not x["builtin_key"]), None)
            else:
                r = by_name.get(name.lower())
        if r:
            if not r.get("builtin_key"):
                _keep_custom(r["id"], key, mods, types, now)
            db.ex("UPDATE scan_profiles SET builtin_key=?, name=?, description=?, modules=?, scanners='[]', scanner_types=? "
                  "WHERE id=?", (key, name, desc, json.dumps(mods), json.dumps(types), r["id"]))
        else:
            db.ex("INSERT INTO scan_profiles(name, description, modules, scanners, scanner_types, collections, triggers, "
                  "is_default, enabled, created_at, updated_at, builtin_key) VALUES(?,?,?,?,?,?,?,0,1,?,?,?)",
                  (name, desc, json.dumps(mods), "[]", json.dumps(types), json.dumps([ALL_COMPUTERS]), "[]", now, now, key))
    if not db.val("SELECT 1 FROM scan_profiles WHERE is_default=1"):
        db.ex("UPDATE scan_profiles SET is_default=1 WHERE builtin_key='standard'")


def mark_new(cids):
    """Neu gefundene Computer mit dem Standardprofil scannen, sobald sie online sind."""
    p = default()
    if p and cids:
        _mark_pending(p["id"], cids, util.now())


def get(pid):
    return _parse(db.q1("SELECT * FROM scan_profiles WHERE id=?", (pid,)))


def default():
    p = _parse(db.q1("SELECT * FROM scan_profiles ORDER BY is_default DESC, id LIMIT 1"))
    if p:
        return p
    init()
    return _parse(db.q1("SELECT * FROM scan_profiles ORDER BY is_default DESC, id LIMIT 1"))


def all_profiles():
    return [_parse(r) for r in db.q("SELECT * FROM scan_profiles ORDER BY is_default DESC, name")]


def init():
    """Beim ersten Start: Standardprofil; Intervall aus der alten Einstellung übernehmen."""
    if db.val("SELECT COUNT(*) FROM scan_profiles", default=0):
        return
    from . import inventory
    hours = db.setting("scan_interval_hours")
    triggers = [{"id": _tid(), "type": "heartbeat", "enabled": True}]
    if hours and hours > 0:
        triggers.insert(0, {"id": _tid(), "type": "scan_age", "enabled": True, "days": hours // 24,
                            "hours": hours % 24, "minutes": 0, "only_at": False})
    now = util.now()
    all_id = db.val("SELECT id FROM collections WHERE builtin=1 AND name='Alle Computer'") or ALL_COMPUTERS
    name, desc, mods, _t = BUILTIN["standard"]
    pid = db.ex("INSERT INTO scan_profiles(name, description, modules, scanners, scanner_types, collections, triggers, "
                "is_default, enabled, created_at, updated_at, builtin_key) VALUES(?,?,?,'[]','[]',?,?,1,1,?,?,'standard')",
                (name, desc, json.dumps(mods), json.dumps([all_id]), json.dumps(triggers), now, now))
    db.ex("INSERT OR IGNORE INTO profile_scans(computer_id, profile_id, last_scan, last_attempt, status) "
          "SELECT id, ?, last_scan, last_scan_attempt, scan_status FROM computers", (pid,))
    _baseline(pid, triggers)
    seed_builtin()


def _tid():
    return secrets.token_hex(4)


def _baseline(pid, triggers, old_triggers=None, reenabled=False):
    """Neue, geänderte oder wieder aktivierte Auslöser starten „ab jetzt“ – verpasste Termine lösen nicht aus."""
    now = util.now()
    old = {t.get("id"): t for t in (old_triggers or [])}
    strip = lambda x: {k: v for k, v in x.items() if k != "id"}  # noqa: E731
    for t in triggers:
        o = old.get(t["id"])
        if reenabled or (o is not None and (strip(o) != strip(t) or (not o.get("enabled", True) and t.get("enabled", True)))):
            db.ex("INSERT INTO profile_trigger_state(profile_id, trigger_id, last_fired) VALUES(?,?,?) "
                  "ON CONFLICT(profile_id, trigger_id) DO UPDATE SET last_fired=excluded.last_fired", (pid, t["id"], now))
        else:
            db.ex("INSERT OR IGNORE INTO profile_trigger_state(profile_id, trigger_id, last_fired) VALUES(?,?,?)",
                  (pid, t["id"], now))
    ids = [t["id"] for t in triggers] or [""]
    marks = ",".join("?" * len(ids))
    db.ex(f"DELETE FROM profile_trigger_state WHERE profile_id=? AND trigger_id NOT IN ({marks})", (pid, *ids))


def _hm(v, label):
    v = (v or "").strip()
    try:
        datetime.datetime.strptime(v, "%H:%M")
    except ValueError:
        raise ValueError(f"{label}: Uhrzeit im Format HH:MM angeben.")
    return v


def _dt(v, label, required=True):
    v = (v or "").strip().replace("T", " ")
    if not v and not required:
        return None
    d = util.parse(v)
    if not d:
        raise ValueError(f"{label}: Datum/Uhrzeit ungültig.")
    return util.fmt(d)[:16]


def _int(v, lo, hi, label):
    try:
        n = int(v or 0)
    except (TypeError, ValueError):
        raise ValueError(f"{label}: Zahl erwartet.")
    if not lo <= n <= hi:
        raise ValueError(f"{label}: Wert zwischen {lo} und {hi}.")
    return n


def clean_trigger(t):
    typ = t.get("type")
    if typ not in TRIGGER_TYPES:
        raise ValueError("Unbekannter Auslöser.")
    label = TRIGGER_TYPES[typ]
    o = {"id": str(t.get("id") or _tid())[:16], "type": typ, "enabled": bool(t.get("enabled", True)),
         "catch_up": bool(t.get("catch_up")), "end": _dt(t.get("end"), f"{label} (Ende)", False)}
    if typ == "once":
        o["at"] = _dt(t.get("at"), label)
    elif typ == "interval":
        o["every"] = _int(t.get("every"), 1, 100000, label)
        o["unit"] = t.get("unit") if t.get("unit") in ("m", "h", "d") else "h"
        if o["unit"] == "m" and o["every"] < 5:
            raise ValueError("Intervall: mindestens 5 Minuten.")
        o["start"] = _dt(t.get("start") or util.now(), f"{label} (Start)")
    elif typ in ("daily", "weekly", "monthly"):
        o["time"] = _hm(t.get("time"), label)
        if typ == "daily":
            o["every"] = _int(t.get("every") or 1, 1, 365, label)
            o["start"] = _dt(t.get("start") or util.now()[:10], f"{label} (Start)")[:10]
        if typ == "weekly":
            o["days"] = sorted({_int(d, 0, 6, label) for d in t.get("days") or []})
            if not o["days"]:
                raise ValueError("Wöchentlich: mindestens einen Wochentag wählen.")
            o["every"] = _int(t.get("every") or 1, 1, 52, label)
            o["start"] = _dt(t.get("start") or util.now()[:10], f"{label} (Start)")[:10]
        if typ == "monthly":
            days = set()
            for d in t.get("days") or []:
                days.add("last" if d == "last" else _int(d, 1, 31, label))
            if not days:
                raise ValueError("Monatlich: mindestens einen Tag wählen.")
            o["days"] = sorted(days, key=lambda x: 99 if x == "last" else x)
    elif typ == "scan_age":
        o["days"] = _int(t.get("days"), 0, 3650, label)
        o["hours"] = _int(t.get("hours"), 0, 23, label)
        o["minutes"] = _int(t.get("minutes"), 0, 59, label)
        if o["days"] * 1440 + o["hours"] * 60 + o["minutes"] < 15:
            raise ValueError("Scan-Alter: mindestens 15 Minuten.")
        o["only_at"] = bool(t.get("only_at"))
        if o["only_at"]:
            o["from"] = _hm(t.get("from"), "Nur zwischen (von)")
            o["to"] = _hm(t.get("to"), "Nur zwischen (bis)")
    return o


def clean(data, existing=None):
    from . import inventory
    if existing and existing.get("builtin_key"):
        # Feste Profile: Name und Scanner bleiben, wie sie sind
        data = {**data, "name": existing["name"], "modules": existing["modules"], "scanners": [],
                "scanner_types": existing["scanner_types"], "description": existing["description"]}
    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("Name fehlt.")
    mods = [m for m in inventory.MODULE_ORDER if m in (data.get("modules") or [])]
    valid_sc = {r["id"] for r in db.q("SELECT id FROM custom_scanners")}
    scs = [int(i) for i in data.get("scanners") or [] if int(i) in valid_sc]
    types = [t for t in data.get("scanner_types") or [] if t in SCANNER_TYPES]
    if not mods and not scs and not types:
        raise ValueError("Mindestens einen Scanner auswählen.")
    valid_col = {r["id"] for r in db.q("SELECT id FROM collections")} | {ALL_COMPUTERS}
    cols = [int(i) for i in data.get("collections") or [] if int(i) in valid_col]
    valid_cmp = {r["id"] for r in db.q("SELECT id FROM computers")}
    cmps = sorted({int(i) for i in data.get("computers") or [] if int(i) in valid_cmp})
    trig = [clean_trigger(t) for t in data.get("triggers") or []]
    return {"name": name[:120], "description": (data.get("description") or "").strip()[:1000], "modules": mods,
            "scanners": scs, "scanner_types": types, "collections": cols, "computers": cmps, "triggers": trig,
            "enabled": bool(data.get("enabled", True))}


def save(pid, data):
    prev = get(pid) if pid else None
    d = clean(data, prev)
    now = util.now()
    vals = (d["name"], d["description"], json.dumps(d["modules"]), json.dumps(d["scanners"]), json.dumps(d["scanner_types"]),
            json.dumps(d["collections"]), json.dumps(d["computers"]), json.dumps(d["triggers"]), 1 if d["enabled"] else 0, now)
    if pid:
        db.ex("UPDATE scan_profiles SET name=?, description=?, modules=?, scanners=?, scanner_types=?, collections=?, "
              "computers=?, triggers=?, enabled=?, updated_at=? WHERE id=?", (*vals, pid))
    else:
        pid = db.ex("INSERT INTO scan_profiles(name, description, modules, scanners, scanner_types, collections, computers, "
                    "triggers, enabled, updated_at, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (*vals, now))
    _baseline(pid, d["triggers"], prev["triggers"] if prev else None, bool(prev and not prev["enabled"] and d["enabled"]))
    return pid


def set_default(pid):
    with db.tx():
        db.ex("UPDATE scan_profiles SET is_default=0")
        db.ex("UPDATE scan_profiles SET is_default=1 WHERE id=?", (pid,))


def delete(pid):
    p = get(pid)
    if not p:
        return
    if p.get("builtin_key"):
        raise ValueError(f"„{p['name']}“ ist ein festes Scanprofil und kann nicht gelöscht werden. "
                         "Zum Anpassen „Duplizieren“ verwenden.")
    if p["is_default"]:
        raise ValueError("Das Standardprofil kann nicht gelöscht werden. Erst ein anderes Profil als Standard festlegen.")
    db.ex("DELETE FROM scan_profiles WHERE id=?", (pid,))


def remove_scanner(sid):
    """Eigenen Scanner aus allen Profilen entfernen (beim Löschen)."""
    for p in all_profiles():
        if sid in p["scanners"]:
            db.ex("UPDATE scan_profiles SET scanners=? WHERE id=?",
                  (json.dumps([i for i in p["scanners"] if i != sid]), p["id"]))


def add_scanner(sid, profile_ids):
    """Eigenen Scanner in eigene Profile aufnehmen. Feste Profile enthalten ihren Typ ohnehin automatisch."""
    for p in all_profiles():
        if p.get("builtin_key"):
            continue
        want = p["id"] in profile_ids
        has = sid in p["scanners"]
        if want != has:
            sc = [i for i in p["scanners"] if i != sid] + ([sid] if want else [])
            db.ex("UPDATE scan_profiles SET scanners=? WHERE id=?", (json.dumps(sc), p["id"]))


def record(cid, pid, status, ts):
    ok = status == "OK"
    db.ex("""INSERT INTO profile_scans(computer_id, profile_id, last_scan, last_attempt, status) VALUES(?,?,?,?,?)
             ON CONFLICT(computer_id, profile_id) DO UPDATE SET last_attempt=excluded.last_attempt,
             status=excluded.status, last_scan=COALESCE(excluded.last_scan, last_scan)""",
          (cid, pid, ts if ok else None, ts, status))


def has_targets(p):
    """Profil wirkt auf mindestens eine Sammlung oder einen einzelnen Computer."""
    return bool(p.get("collections") or p.get("computers"))


def members(p):
    """Ziel-Computer: Mitglieder der Sammlungen plus einzeln gewählte Computer."""
    from . import collections_
    if ALL_COMPUTERS in p["collections"]:
        return {r["id"] for r in db.q("SELECT id FROM computers")}
    existing = {r["id"] for r in db.q("SELECT id FROM computers")} if p.get("computers") else set()
    ids = {int(i) for i in p.get("computers") or [] if int(i) in existing}
    for col in p["collections"]:
        try:
            ids.update(collections_.member_ids(col))
        except Exception:
            continue
    return ids


def describe(t):
    """Kurztext eines Auslösers für die Oberfläche."""
    typ = t["type"]
    if typ == "once":
        s = f"Einmalig am {t['at']}"
    elif typ == "interval":
        s = f"Alle {t['every']} {dict(m='Min.', h='Std.', d='Tage')[t['unit']]} ab {t['start']}"
    elif typ == "daily":
        s = f"Täglich um {t['time']}" if t.get("every", 1) == 1 else f"Alle {t['every']} Tage um {t['time']}"
    elif typ == "weekly":
        s = ("Wöchentlich" if t.get("every", 1) == 1 else f"Alle {t['every']} Wochen") + \
            f" ({', '.join(WEEKDAYS[d] for d in t['days'])}) um {t['time']}"
    elif typ == "monthly":
        s = "Monatlich am " + ", ".join("letzten" if d == "last" else f"{d}." for d in t["days"]) + f" um {t['time']}"
    elif typ == "scan_age":
        parts = [f"{t[k]} {lbl}" for k, lbl in (("days", "T"), ("hours", "Std"), ("minutes", "Min")) if t.get(k)]
        s = "Scan älter als " + " ".join(parts)
        if t.get("only_at"):
            s += f", nur {t['from']}–{t['to']}"
    else:
        s = "Wenn der Computer online kommt (Heartbeat)"
    if t.get("end"):
        s += f", bis {t['end']}"
    return s


# --- Zeitberechnung ---------------------------------------------------------------
def _last_month_day(d):
    nxt = (d.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
    return (nxt - datetime.timedelta(days=1)).day


def last_occurrence(t, now):
    """Letzter planmäßiger Zeitpunkt ≤ now (oder None)."""
    typ = t["type"]
    if typ == "once":
        at = util.parse(t["at"])
        return at if at and at <= now else None
    if typ == "interval":
        start = util.parse(t["start"])
        if not start or start > now:
            return None
        step = datetime.timedelta(**{dict(m="minutes", h="hours", d="days")[t["unit"]]: t["every"]})
        return start + step * int((now - start) / step)
    if typ in ("daily", "weekly", "monthly"):
        hh, mm = (int(x) for x in t["time"].split(":"))
        anchor = util.parse(t.get("start") or "2000-01-03")  # 2000-01-03 ist ein Montag
        for back in range(0, 400):
            day = (now - datetime.timedelta(days=back)).replace(hour=hh, minute=mm, second=0, microsecond=0)
            if day > now:
                continue
            if typ == "daily":
                if anchor and day.date() < anchor.date():
                    return None
                if (day.date() - (anchor.date() if anchor else day.date())).days % t.get("every", 1) == 0:
                    return day
            elif typ == "weekly":
                if anchor and day.date() < anchor.date():
                    return None  # nicht vor dem Startdatum (die Woche des Starts bestimmt nur den Rhythmus)
                if day.weekday() in t["days"]:
                    a = anchor.date() - datetime.timedelta(days=anchor.weekday())
                    if ((day.date() - a).days // 7) % t.get("every", 1) == 0:
                        return day
            else:
                last = _last_month_day(day)
                if day.day in t["days"] or ("last" in t["days"] and day.day == last):
                    return day
        return None
    return None


def occurs_on(t, day):
    """Liegt ein Termin des Auslösers (täglich/wöchentlich/monatlich) auf diesem Tag? – schnell, ohne Rückwärtssuche."""
    typ = t["type"]
    anchor = util.parse(t.get("start") or "2000-01-03")
    d = day.date() if hasattr(day, "date") else day
    if typ == "daily":
        return (not anchor or d >= anchor.date()) and (d - (anchor.date() if anchor else d)).days % t.get("every", 1) == 0
    if typ == "weekly":
        if anchor and d < anchor.date():
            return False
        if d.weekday() not in t["days"]:
            return False
        a = anchor.date() - datetime.timedelta(days=anchor.weekday())
        return ((d - a).days // 7) % t.get("every", 1) == 0
    if typ == "monthly":
        return d.day in t["days"] or ("last" in t["days"] and d.day == _last_month_day(day if hasattr(day, "date") else
                                                                                       datetime.datetime.combine(d, datetime.time())))
    return False


def in_window(t, now):
    if not t.get("only_at"):
        return True
    cur = now.strftime("%H:%M")
    a, b = t["from"], t["to"]
    return a <= cur < b if a <= b else (cur >= a or cur < b)  # Fenster über Mitternacht


# --- Auswertung durch den Scheduler -------------------------------------------------
def _mark_pending(pid, cids, now):
    db.exmany("""INSERT INTO profile_scans(computer_id, profile_id, pending, pending_since) VALUES(?,?,1,?)
                 ON CONFLICT(computer_id, profile_id) DO UPDATE SET pending=1,
                 pending_since=COALESCE(pending_since, excluded.pending_since)""",
              [(cid, pid, now) for cid in cids])


def evaluate():
    """Zeitgesteuerte Auslöser und Scan-Alter prüfen; fällige Scans als „ausstehend“ markieren."""
    now = datetime.datetime.now()
    nows = util.fmt(now)
    state = {(r["profile_id"], r["trigger_id"]): r["last_fired"] for r in db.q("SELECT * FROM profile_trigger_state")}
    online = {r["id"] for r in db.q("SELECT id FROM computers WHERE online=1 AND winrm=1")}
    for p in all_profiles():
        if not p["enabled"] or not has_targets(p):
            continue
        try:
            _evaluate_profile(p, now, nows, state, online)
        except Exception as e:  # ein fehlerhafter Auslöser darf die übrigen Profile nicht aufhalten
            from . import pklog
            pklog.error("scan", f"Scanprofil „{p['name']}“: Auswertung fehlgeschlagen: {e}")


def _evaluate_profile(p, now, nows, state, online):
    mem = None
    for t in p["triggers"]:
        if not t.get("enabled", True) or t["type"] == "heartbeat":
            continue
        end = util.parse(t.get("end"))
        if end and now > end:
            continue
        if mem is None:
            mem = members(p)
        if t["type"] == "scan_age":
            if not in_window(t, now):
                continue
            age = util.fmt(now - datetime.timedelta(days=t["days"], hours=t["hours"], minutes=t["minutes"]))
            retry = util.fmt(now - datetime.timedelta(minutes=min(RETRY_MINUTES, max(15, t["days"] * 1440 + t["hours"] * 60 + t["minutes"]))))
            ps = {r["computer_id"]: r for r in db.q("SELECT * FROM profile_scans WHERE profile_id=?", (p["id"],))}
            due = []
            for cid in mem & online:
                r = ps.get(cid) or {}
                if r.get("pending"):
                    continue
                if (not r.get("last_scan") or r["last_scan"] < age) and \
                        (not r.get("last_attempt") or r["last_attempt"] < retry):
                    due.append(cid)
            _mark_pending(p["id"], due, nows)
            continue
        occ = last_occurrence(t, now)
        last = util.parse(state.get((p["id"], t["id"])))
        if occ and (last is None or occ > last):
            targets = mem if t.get("catch_up") else (mem & online)
            _mark_pending(p["id"], targets, nows)
            db.ex("INSERT INTO profile_trigger_state(profile_id, trigger_id, last_fired) VALUES(?,?,?) "
                  "ON CONFLICT(profile_id, trigger_id) DO UPDATE SET last_fired=excluded.last_fired",
                  (p["id"], t["id"], nows))
            from . import pklog
            pklog.info("scan", f"Scanprofil „{p['name']}“: Auslöser „{describe(t)}“ – {len(targets)} Computer eingeplant.")


def on_online(cids):
    """Heartbeat-Auslöser: Computer ist (wieder) online gekommen."""
    if not cids:
        return
    now = util.now()
    for p in all_profiles():
        if not p["enabled"] or not any(t["type"] == "heartbeat" and t.get("enabled", True) for t in p["triggers"]):
            continue
        hit = members(p) & set(cids)
        if hit:
            _mark_pending(p["id"], hit, now)


def dispatch():
    """Ausstehende Scans starten, sobald der Computer online und frei ist."""
    from . import inventory
    db.ex("UPDATE profile_scans SET pending=0, pending_since=NULL WHERE pending=1 AND pending_since < ?",
          (util.ago(days=14),))
    # 2.32.8: gerade hochgefahrene oder aufgewachte Computer erst nach einer kurzen Wartezeit scannen – Netzwerkkarte
    # (Geschwindigkeit), Dienste und angemeldete Benutzer sind dann fertig; manuelle Scans laufen sofort
    delay = int(db.setting("scan_online_delay_minutes") or 0)
    ready = util.ago(minutes=delay) if delay > 0 else "9999"
    rows = db.q("""SELECT ps.computer_id, ps.profile_id FROM profile_scans ps JOIN computers c ON c.id=ps.computer_id
                   JOIN scan_profiles p ON p.id=ps.profile_id
                   WHERE ps.pending=1 AND c.online=1 AND c.winrm=1 AND p.enabled=1 AND COALESCE(c.scan_allowed, 1)=1
                     AND (c.online_since IS NULL OR c.online_since <= ?)
                   ORDER BY ps.pending_since""", (ready,))
    for r in rows:
        if inventory.is_scanning(r["computer_id"]):
            continue
        if inventory.queue_scan(r["computer_id"], None, r["profile_id"]):
            db.ex("UPDATE profile_scans SET pending=0, pending_since=NULL WHERE computer_id=? AND profile_id=?",
                  (r["computer_id"], r["profile_id"]))


def summary(p, only=None):
    """Kennzahlen eines Profils für die Liste. only: nur diese Computer zählen (OU-Admin: sein Bereich)."""
    mem = members(p) if has_targets(p) else set()
    if only is not None:
        mem &= set(only)
    rows = db.q("SELECT computer_id, last_scan, status, pending FROM profile_scans WHERE profile_id=?", (p["id"],))
    rows = [r for r in rows if r["computer_id"] in mem]
    return {"members": len(mem), "scanned": sum(1 for r in rows if r["last_scan"]),
            "failed": sum(1 for r in rows if r["status"] == "Fehler"), "pending": sum(1 for r in rows if r["pending"]),
            "last_scan": max((r["last_scan"] for r in rows if r["last_scan"]), default=None),
            "triggers_text": [describe(t) for t in p["triggers"]]}


def apps_profile_id():
    """Profil für den Scan nach einer Bereitstellung: das feste Profil „Anwendungen“ (sonst das schlankeste mit Anwendungen)."""
    fixed = db.val("SELECT id FROM scan_profiles WHERE builtin_key='applications'")
    if fixed:
        return fixed
    best = None
    for p in all_profiles():
        if p["enabled"] and "applications" in p["modules"]:
            size = len(p["modules"]) + len(p["scanners"])
            if best is None or size < best[0]:
                best = (size, p["id"])
    return best[1] if best else None
