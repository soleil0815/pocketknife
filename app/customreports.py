"""Eigene Berichte: Spalten, Filter, Sortierung und Zusammenfassung frei aus allen Inventardaten wählen.

Definition (JSON):
    {"source": "computer" | "software" | "disks" | "printers" | "custom:7" …,   – eine Zeile je …
     "columns": [{"t": "computer", "c": "name", "label": ""}, {"t": "software", "c": "version"}],
     "filter": <Filterbaum wie bei Sammlungen – welche Computer>,
     "rows": <Filterbaum nur über die Quelle – welche Zeilen>,
     "collection": "" | id,
     "group": false,            – gleiche Zeilen zusammenfassen und zählen
     "sort": {"i": 0, "dir": "asc"},  – Spaltennummer, „count“ = Anzahl
     "limit": 5000}

Alle Spalten und Operatoren kommen aus dem Katalog der Sammlungsfilter (collections_), Werte werden als
Parameter übergeben – es entsteht nie SQL aus Benutzereingaben.
"""
import json

from . import collections_, db, pklog, util

MAX_COLUMNS = 30
MAX_LIMIT = 100000
DEFAULT_LIMIT = 5000
PREFIX = "my_"


class _T:
    """Zwischenspeicher der Tabellendefinitionen für einen Lauf."""
    def __init__(self):
        self.cache = {}

    def get(self, t):
        if t not in self.cache:
            self.cache[t] = collections_._table(t)
        return self.cache[t]


def _tree(node):
    if isinstance(node, dict) and node.get("type") == "group":
        return node
    return {"type": "group", "match": "all", "items": []}


def clean(data):
    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("Name fehlt.")
    d = data.get("definition") or {}
    source = d.get("source") or "computer"
    tabs = _T()
    try:
        tabs.get(source)
    except ValueError:
        raise ValueError("Unbekannte Datenquelle.")
    cols = []
    for c in d.get("columns") or []:
        t, col = c.get("t") or "computer", c.get("c") or ""
        if t not in ("computer", source):
            raise ValueError("Spalten nur aus „Computer“ oder der gewählten Datenquelle.")
        if t == "computer" and col == "collection":
            raise ValueError("„Sammlung“ ist keine Spalte.")
        collections_._col(t, tabs.get(t), col)  # prüft, ob es die Spalte gibt
        cols.append({"t": t, "c": col, "label": (c.get("label") or "").strip()[:80]})
    if not cols:
        raise ValueError("Mindestens eine Spalte wählen.")
    if len(cols) > MAX_COLUMNS:
        raise ValueError(f"Höchstens {MAX_COLUMNS} Spalten.")
    sort = d.get("sort") or {}
    si = sort.get("i", 0)
    if si != "count":
        try:
            si = int(si)
        except (TypeError, ValueError):
            si = 0
        if not 0 <= si < len(cols):
            si = 0
    elif not d.get("group"):
        si = 0
    try:
        limit = int(d.get("limit") or DEFAULT_LIMIT)
    except (TypeError, ValueError):
        raise ValueError("Höchstzahl Zeilen: Zahl angeben.")
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"Höchstzahl Zeilen: 1 bis {MAX_LIMIT}.")
    coll = str(d.get("collection") or "")
    if coll and (not coll.isdigit() or not db.q1("SELECT id FROM collections WHERE id=?", (int(coll),))):
        raise ValueError("Sammlung existiert nicht.")
    out = {"source": source, "columns": cols, "filter": _tree(d.get("filter")), "rows": _tree(d.get("rows")),
           "collection": coll, "group": bool(d.get("group")),
           "sort": {"i": si, "dir": "desc" if sort.get("dir") == "desc" else "asc"}, "limit": limit}
    if source == "computer":
        out["rows"] = _tree(None)
    _sql(out, {})  # Filter prüfen (ungültige Operatoren, Werte, RegEx …)
    return {"name": name[:120], "description": (data.get("description") or "").strip()[:1000], "definition": out}


def _row_tree(node, source, tdef, depth=0):
    """Bedingungen nur über die Quellzeile (keine EXISTS – die Zeile selbst muss passen)."""
    if depth > 8:
        raise ValueError("Zu tief verschachtelt.")
    match = node.get("match", "all")
    if match not in collections_.MATCH_LABELS:
        match = "all"  # wie bei den Sammlungen
    parts = []
    for it in node.get("items") or []:
        if not isinstance(it, dict):
            continue
        if it.get("type") == "group":
            parts.append(_row_tree(it, source, tdef, depth + 1))
        else:
            parts.append(collections_._row_cond(source, tdef, it))
    if not parts:
        return "1=1", []
    joiner = " AND " if match in ("all", "not_all") else " OR "
    sql = "(" + joiner.join(p[0] for p in parts) + ")"
    if match in ("not_all", "none"):
        sql = "NOT " + sql
    return sql, [x for p in parts for x in p[1]]


def _labels(d, tabs):
    src_label = tabs.get(d["source"])[1]
    out, seen = [], {}
    for c in d["columns"]:
        known = tabs.get(c["t"])[4]
        lab = c.get("label") or (known[c["c"]][0] if c["c"] in known else c["c"])
        seen[lab] = seen.get(lab, 0) + 1
        out.append(lab)
    # Gleiche Bezeichnungen (z. B. „Name“ des Computers und der Anwendung) unterscheidbar machen
    for i, c in enumerate(d["columns"]):
        if seen[out[i]] > 1 and not c.get("label"):
            out[i] = f"{out[i]} ({'Computer' if c['t'] == 'computer' else src_label})"
    return out


def _sql(d, params):
    tabs = _T()
    src = d["source"]
    sdef = tabs.get(src)
    sel, sp, types = [], [], []
    for i, c in enumerate(d["columns"]):
        typ, expr, ep = collections_._col(c["t"], tabs.get(c["t"]), c["c"])
        sel.append(f"({expr}) AS c{i}")
        sp += ep
        types.append(typ)
    where, wp = [], []
    if src == "computer":
        frm, fp = "computers c", []
    else:
        kind, _label, sfrm, link, _cols, cat = sdef
        frm, fp = f"computers c, {sfrm}", []
        where.append(link)
        if kind == "json":
            wp.append(cat)
        s, p = _row_tree(d["rows"], src, sdef)
        where.append(s)
        wp += p
    s, p = collections_.build_tree(d["filter"])
    where.append(s)
    wp += p
    for coll in (d.get("collection"), params.get("collection")):
        if coll:
            s, p = collections_.where_for(int(coll))
            where.append(f"({s})")
            wp += p
    if params.get("_scope") is not None:  # 2.30: OU-Admin – nur seine Computer (vom Server gesetzt)
        where.append("c.id IN (SELECT value FROM json_each(?))")
        wp.append(params["_scope"])
    group = d.get("group")
    if group:
        cnt = "COUNT(DISTINCT c.id)" if src != "computer" else "COUNT(*)"
        sel.append(f"{cnt} AS cnt")
    else:
        sel.append("c.id AS _cid")
    sql = f"SELECT {', '.join(sel)} FROM {frm} WHERE {' AND '.join(where) or '1=1'}"
    if group:
        sql += " GROUP BY " + ", ".join(str(i + 1) for i in range(len(d["columns"])))
    si = d["sort"]["i"]
    order = "cnt" if si == "count" and group else f"c{si}"
    direction = "DESC" if d["sort"]["dir"] == "desc" else "ASC"
    # Zahlen numerisch, Texte ohne Groß/klein sortieren; leere Werte ans Ende
    stype = None if order == "cnt" else types[si]
    okey = f"CAST({order} AS REAL)" if stype in ("number", "bytes") or order == "cnt" else (f"{order} COLLATE NOCASE" if stype in ("text", "any") else order)
    sql += f" ORDER BY ({order} IS NULL OR {order} = ''), {okey} {direction}"
    sql += " LIMIT ?"
    return sql, sp + fp + wp + [int(d["limit"]) + 1], types


def execute(d, params=None, limit=None):
    """→ {columns, rows, truncated}. rows enthalten zusätzlich _cid (nicht zusammengefasst)."""
    params = params or {}
    if limit:
        d = {**d, "limit": min(int(d["limit"]), int(limit))}
    sql, args, types = _sql(d, params)
    rows = db.q(sql, args)
    truncated = len(rows) > d["limit"]
    rows = rows[:d["limit"]]
    tabs = _T()
    labels = _labels(d, tabs)
    columns = [{"key": f"c{i}", "label": labels[i], "type": types[i]} for i in range(len(d["columns"]))]
    bools = [f"c{i}" for i, t in enumerate(types) if t == "bool"]
    for r in rows:
        for k in bools:
            v = r.get(k)
            r[k] = "ja" if v in (1, "1", True, "True", "true") else ("nein" if v not in (None, "") else "")
    if d.get("group"):
        columns.append({"key": "cnt", "label": "Anzahl Computer" if d["source"] != "computer" else "Anzahl", "type": "number"})
    return {"columns": columns, "rows": rows, "truncated": truncated}


# --- Speicherung -------------------------------------------------------------------------------
def _parse(row):
    if not row:
        return None
    r = dict(row)
    try:
        r["definition"] = json.loads(r.get("definition") or "{}")
    except ValueError:
        r["definition"] = {}
    r["key"] = f"{PREFIX}{r['id']}"
    return r


def get(rid):
    return _parse(db.q1("SELECT * FROM custom_reports WHERE id=?", (rid,)))


def all_reports():
    return [_parse(r) for r in db.q("SELECT * FROM custom_reports ORDER BY name COLLATE NOCASE")]


def save(rid, data, user=None):
    c = clean(data)
    if rid:
        old = get(rid)
        if not old:
            raise ValueError("Bericht nicht gefunden.")
        db.ex("UPDATE custom_reports SET name=?, description=?, definition=?, updated_at=? WHERE id=?",
              (c["name"], c["description"], json.dumps(c["definition"]), util.now(), rid))
        if old["definition"] != c["definition"]:
            try:
                from . import notify
                notify.rebaseline_report(rid)
            except Exception as e:
                pklog.warn("report", f"Benachrichtigung zu „{c['name']}“ nicht neu eingelesen: {e}")
    else:
        rid = db.ex("INSERT INTO custom_reports(name, description, definition, created_by, created_at, updated_at) "
                    "VALUES(?,?,?,?,?,?)", (c["name"], c["description"], json.dumps(c["definition"]), user, util.now(), util.now()))
    pklog.info("report", f"Eigener Bericht „{c['name']}“ gespeichert.", user=user)
    return get(rid)


def usage(rid):
    """Wo wird der Bericht verwendet? (Berichtsversand, Benachrichtigung)"""
    key = f"{PREFIX}{rid}"
    subs = [s["name"] for s in db.q("SELECT name, reports FROM mail_reports")
            if any(r.get("key") == key for r in (json.loads(s["reports"] or "[]") or []))]
    alert = db.val("SELECT enabled FROM mail_alerts WHERE key=?", (f"report:{rid}",), 0)
    return {"subscriptions": subs, "alert": bool(alert)}


def delete(rid, user=None):
    r = get(rid)
    if not r:
        raise ValueError("Bericht nicht gefunden.")
    key = r["key"]
    with db.tx():
        for s in db.q("SELECT id, reports FROM mail_reports"):
            reps = json.loads(s["reports"] or "[]") or []
            keep = [x for x in reps if x.get("key") != key]
            if len(keep) != len(reps):
                # Leer gewordene Versände deaktivieren statt löschen (Einstellungen bleiben erhalten)
                db.ex("UPDATE mail_reports SET reports=?, enabled=CASE WHEN ? THEN enabled ELSE 0 END WHERE id=?",
                      (json.dumps(keep), 1 if keep else 0, s["id"]))
        db.ex("DELETE FROM mail_alerts WHERE key=?", (f"report:{rid}",))
        db.ex("DELETE FROM mail_alert_state WHERE alert=?", (f"report:{rid}",))
        db.ex("DELETE FROM custom_reports WHERE id=?", (rid,))
    pklog.info("report", f"Eigener Bericht „{r['name']}“ gelöscht.", user=user)


def duplicate(rid, user=None):
    r = get(rid)
    if not r:
        raise ValueError("Bericht nicht gefunden.")
    return save(None, {"name": f"{r['name']} (Kopie)", "description": r.get("description"), "definition": r["definition"]}, user)


# --- Anbindung an Berichte/Benachrichtigungen ----------------------------------------------------
def definitions():
    return [{"key": r["key"], "title": r["name"], "desc": r.get("description") or "Eigener Bericht", "custom": True,
             "id": r["id"], "params": [{"name": "collection", "label": "Sammlung", "type": "collection", "default": ""}]}
            for r in all_reports()]


def run_key(key, params):
    try:
        rid = int(key[len(PREFIX):])
    except ValueError:
        raise ValueError("Unbekannter Bericht")
    r = get(rid)
    if not r:
        raise ValueError("Unbekannter Bericht")
    res = execute(r["definition"], params)
    for row in res["rows"]:
        row.pop("_cid", None)
    note = f" (gekürzt auf {r['definition']['limit']} Zeilen)" if res["truncated"] else ""
    return {"title": r["name"] + note, "columns": res["columns"], "rows": res["rows"], "generated": util.now(),
            "custom": True, "id": rid}
