"""2.57.0: what is wrong with own scanners, tools, scan profiles, collections and packages - shown everywhere as the same
yellow "!" (tooltip = reason, click = the place to fix it).

Scanner health compares the stored results of a custom scanner (inventory_data, category custom:<id>):
- errors on many computers (rows with "fehler"),
- since the last change of type/target: no values any more, or columns that are no longer delivered,
- columns that a collection checks but the scanner does not deliver,
- PowerShell code that Windows would log as a warning or error (pslint).
Collections: filter not evaluable (missing reference after an import, deleted scanner/field, …), checked columns that the
scanner does not deliver, scanner switched off.
Only counts and names of the own configuration - no computer data leaves this module.
"""
import json
import threading
import time

from . import collections_, db

TTL = 30  # seconds - the lists reload every minute; the checks read all custom scanner results
ERR_MIN = 5          # errors on at least this many computers ...
ERR_SHARE = 0.2      # ... or on at least this share of the scanned computers
EMPTY_MIN = 3        # "delivers nothing anywhere" only from this many computers on
IGNORE_COLS = {"fehler", "warnung"}
_cache: dict = {}
_lock = threading.Lock()


def invalidate():
    _cache.clear()


def _cached(key, fn):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < TTL:
        return hit[1]
    with _lock:
        val = fn()
        _cache[key] = (time.time(), val)
    return val


def scanner_changed(old, new):
    """Did type, target or extra (path options, namespace) change? Only then the "before/after" comparison starts anew."""
    def ex(v):
        try:
            d = json.loads(v or "{}") if isinstance(v, str) else (v or {})
        except ValueError:
            d = {}
        return {k: d.get(k) for k in ("recurse", "namespace")}
    return (old.get("type") != new.get("type") or (old.get("target") or "").strip() != (new.get("target") or "").strip()
            or ex(old.get("extra")) != ex(new.get("extra")))


def _day(ts):
    d = str(ts or "")[:10]
    return f"{d[8:10]}.{d[5:7]}.{d[:4]}" if len(d) == 10 else d


def _columns(cat, where="", params=()):
    return {r["k"] for r in db.q(
        f"""SELECT DISTINCT k.key AS k FROM inventory_data d, json_each(d.json) j, json_each(j.value) k
            WHERE d.category = ? AND json_type(j.value) = 'object' {where} LIMIT 200""", (cat, *params))
            if r["k"] and r["k"] not in IGNORE_COLS}


def _checked_columns():
    """{scanner_id: {column: [collection names]}} - columns of custom scanners that collection filters check."""
    out: dict = {}
    for c in db.q("SELECT name, filters, match FROM collections WHERE type='dynamic'"):
        for it in collections_.cond_items(collections_.normalize(c["filters"], c["match"])):
            t = str(it.get("table") or "")
            col = str(it.get("column") or "")
            if t.startswith("custom:") and t[7:].isdigit() and col and not it.get("missing"):
                out.setdefault(int(t[7:]), {}).setdefault(col, []).append(c["name"])
    return out


def _scanner_issues():
    from . import pslint
    out: dict = {}
    checked = _checked_columns()
    for sc in db.q("SELECT id, name, type, target, enabled, updated_at FROM custom_scanners"):
        sid, issues = sc["id"], []
        cat = f"custom:{sid}"
        if sc["type"] == "powershell":
            n = len(pslint.check(sc.get("target") or "", count=False))
            if n:
                issues.append(f"Warnung im PowerShell-Protokoll ({n} Hinweis(e)) – Windows würde den Code beim Scannen protokollieren.")
        rows = db.q("""SELECT d.updated AS updated, json_array_length(d.json) AS n,
                              (SELECT json_extract(j.value, '$.fehler') FROM json_each(d.json) j
                               WHERE json_type(j.value) = 'object' AND json_extract(j.value, '$.fehler') IS NOT NULL LIMIT 1) AS err
                       FROM inventory_data d WHERE d.category = ?""", (cat,))
        changed = sc.get("updated_at")
        after = [r for r in rows if changed and (r["updated"] or "") >= changed]
        before = [r for r in rows if changed and (r["updated"] or "") < changed]
        cur = after if after else ([] if changed else rows)
        day = _day(changed) if after else ""
        if sc["enabled"] and cur:
            errs = [r for r in cur if r["err"] is not None]
            if errs and (len(errs) >= ERR_MIN or len(errs) >= ERR_SHARE * len(cur)):
                msg = str(errs[0]["err"])[:160]
                issues.append(f"Fehler auf {len(errs)} von {len(cur)} Computern seit der Änderung am {day}: {msg}" if day
                              else f"Fehler auf {len(errs)} von {len(cur)} Computern: {msg}")
            empty = [r for r in cur if not r["n"]]
            if after and len(empty) == len(after) and any(r["n"] for r in before):
                issues.append(f"Liefert seit der Änderung am {day} auf keinem Computer mehr Werte (vorher schon).")
            elif not after and sc["type"] in ("powershell", "wmi") and len(empty) == len(cur) >= EMPTY_MIN:
                issues.append(f"Liefert auf keinem der {len(cur)} gescannten Computer Werte.")
            if after and before:
                lost = sorted(_columns(cat, "AND d.updated < ?", (changed,)) - _columns(cat, "AND d.updated >= ?", (changed,)))
                if lost:
                    issues.append(f"Liefert seit der Änderung am {day} die Spalte(n) {', '.join(lost[:6])} nicht mehr "
                                  f"({len(before)} Computer noch mit altem Stand).")
        have = _columns(cat) if rows else set()
        for col, names in sorted((checked.get(sid) or {}).items()):
            if rows and col not in have:
                issues.append(f"Die Sammlung „{names[0]}“ prüft die Spalte „{col}“ – die liefert der Scanner nicht."
                              + (f" Weitere Sammlungen mit dieser Spalte: {len(names) - 1}" if len(names) > 1 else ""))
            elif not sc["enabled"]:
                issues.append(f"Ausgeschaltet, aber die Sammlung „{names[0]}“ prüft seine Werte – die werden nicht mehr aktualisiert.")
                break
        if issues:
            out[sid] = issues
    return out


def scanners():
    """{scanner_id: [reasons]}"""
    return _cached("scanners", _scanner_issues)


def _collection_issues():
    out: dict = {}
    dyn = [r["id"] for r in db.q("SELECT id FROM collections WHERE type='dynamic'")]
    for cid, err in collections_.broken(dyn).items():
        out.setdefault(cid, []).append(f"Nicht auswertbar: {err}")
    sc = {r["id"]: r for r in db.q("SELECT id, name, enabled FROM custom_scanners")}
    have: dict = {}
    for c in db.q("SELECT id, filters, match FROM collections WHERE type='dynamic'"):
        if c["id"] in out:
            continue
        for it in collections_.cond_items(collections_.normalize(c["filters"], c["match"])):
            t = str(it.get("table") or "")
            if not (t.startswith("custom:") and t[7:].isdigit()) or int(t[7:]) not in sc:
                continue
            s = sc[int(t[7:])]
            if s["id"] not in have:
                have[s["id"]] = _columns(t) if db.val("SELECT 1 FROM inventory_data WHERE category=? LIMIT 1", (t,)) else None
            col = str(it.get("column") or "")
            if have[s["id"]] is not None and col and col not in have[s["id"]]:
                out.setdefault(c["id"], []).append(f"Spalte „{col}“ liefert der Scanner „{s['name']}“ nicht.")
            if not s["enabled"]:
                out.setdefault(c["id"], []).append(f"Scanner „{s['name']}“ ist ausgeschaltet – die Werte werden nicht mehr aktualisiert.")
        if c["id"] in out:
            out[c["id"]] = list(dict.fromkeys(out[c["id"]]))
    return out


def collections():
    """{collection_id: [reasons]}"""
    return _cached("collections", _collection_issues)


def _lint():
    from . import pslint
    out: dict = {"scanner": {}, "tool": {}, "package": {}}
    for r in pslint.overview():
        out[r["kind"]][r["id"]] = len(r["findings"])
    return out


def lint():
    return _cached("lint", _lint)


LINT_TEXT = "Warnung im PowerShell-Protokoll ({0} Hinweis(e))"


def profiles(sc_issues=None, col_issues=None):
    """{profile_id: [reasons]} - a profile with a problematic scanner or target collection."""
    from . import profiles as prof
    sc_issues = scanners() if sc_issues is None else sc_issues
    col_issues = collections() if col_issues is None else col_issues
    names = {r["id"]: r["name"] for r in db.q("SELECT id, name FROM custom_scanners")}
    cnames = {r["id"]: r["name"] for r in db.q("SELECT id, name FROM collections")}
    out: dict = {}
    for p in prof.all_profiles():
        why = [f"Scanner „{names.get(s, s)}“: {sc_issues[s][0]}" for s in prof.custom_ids(p) if s in sc_issues]
        why += [f"Ziel „{cnames.get(c, c)}“: {col_issues[c][0]}" for c in p.get("collections") or [] if c in col_issues]
        if why:
            out[p["id"]] = why
    return out


def marks(main_admin=True, package_ok=None, collection_ok=None):
    """For the UI: {scanner|tool|profile|collection|package: {id: [reasons]}}. OU admins: only packages and collections of
    their area (package_ok / collection_ok filter)."""
    ln = lint()
    col = {k: v for k, v in collections().items() if collection_ok is None or collection_ok(k)}
    pkg: dict = {}
    for pid, n in ln["package"].items():
        if package_ok is None or package_ok(pid):
            pkg[pid] = [LINT_TEXT.format(n)]
    out = {"scanner": {}, "tool": {}, "profile": {}, "collection": col, "package": pkg}
    if main_admin:
        sc = scanners()
        out["scanner"] = sc
        out["tool"] = {tid: [LINT_TEXT.format(n)] for tid, n in ln["tool"].items()}
        out["profile"] = profiles(sc, collections())
    out["names"] = _names(out)
    return out


_NAME_SQL = {"scanner": "custom_scanners", "tool": "tools", "profile": "scan_profiles", "collection": "collections", "package": "packages"}


def _names(m):
    """Names of the marked objects - for the overview list ("Warnzeichen")."""
    out: dict = {}
    for kind, table in _NAME_SQL.items():
        ids = [int(i) for i in m.get(kind) or {}]
        out[kind] = {r["id"]: r["name"] for r in db.q(f"SELECT id, name FROM {table} WHERE id IN ({','.join('?' * len(ids))})",
                                                      tuple(ids))} if ids else {}
    return out


def counts():
    """Diagnostic package: how many objects carry the yellow "!" - counts only."""
    m = marks()
    return {k: len(v) for k, v in m.items() if k != "names"}
