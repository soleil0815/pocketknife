"""2.57.0: export and import tools, custom scanners, scan profiles and collections (file *.pkconfig.json) - e.g. into a
second POCKET KNIFE installation, like packages (pkgexport.py).

The file contains no IDs of this installation. References are matched by name on import:
- collections ("ist Mitglied von", targets of scan profiles) by name and path of the parent collections,
- custom scanners by name (scanners used by exported collections and profiles are taken along automatically),
- user-defined fields by name, computers (static collections, single computers of profiles) by name.
What cannot be matched is listed in the check BEFORE the import - never dropped silently. A collection keeps the
condition with the missing reference ("missing"): it is then not evaluable (no members, error instead of a wrong
result), carries the yellow "!" and the reference can be re-assigned in its editor.
Only for main administrators (scanners, tools and profiles run with the service account on every computer).
"""
import copy
import json

from fastapi import HTTPException

from . import collections_, config, db, profiles, scanners, tools, util

FORMAT = "pocketknife-config"
FORMAT_VERSION = 1
KINDS = ("scanners", "collections", "profiles", "tools")  # import order: what others reference comes first
LABELS = {"scanners": "Eigener Scanner", "collections": "Sammlung", "profiles": "Scanprofil", "tools": "Tool"}
MAX_ITEMS = 2000
TOOL_FIELDS = ("name", "folder", "type", "script", "run_as", "confirm", "description", "repo_path", "scan_after",
               "timeout_s", "success_codes")
SCANNER_EXTRA = ("recurse", "namespace", "bytes_mode", "bytes_cols")


class ImportError_(ValueError):
    pass


class _DryRun(Exception):
    pass


def _ids(v):
    return [int(i) for i in (v or []) if str(i).strip().isdigit()]


def _coll_ref(cid):
    """Reference to a collection outside the file: name + path of the parent names (+ AD DN)."""
    from .pkgexport import _coll_path
    if cid == profiles.ALL_COMPUTERS:
        return {"all": True}
    row = db.q1("SELECT name, ad_dn FROM collections WHERE id=?", (cid,))
    if not row:
        return {"name": f"#{cid}", "deleted": True}
    e = {"name": row["name"], "path": _coll_path(cid)}
    if row.get("ad_dn"):
        e["ad_dn"] = row["ad_dn"]
    return e


# --- Export -----------------------------------------------------------------------------------
def export(sel):
    """sel = {scanners|collections|profiles|tools: [ids]} → (file content, notes, taken along automatically)."""
    sel = sel if isinstance(sel, dict) else {}
    want = {k: _ids(sel.get(k)) for k in KINDS}
    if not any(want.values()):
        raise HTTPException(400, "Nichts gewählt.")
    warnings, extra = [], []
    # Collections: the chosen ones with everything below them (folders as a whole)
    col_ids, todo = [], list(want["collections"])
    while todo:
        cid = todo.pop(0)
        if cid in col_ids:
            continue
        row = db.q1("SELECT id, name, builtin, ad_kind FROM collections WHERE id=?", (cid,))
        if not row:
            continue
        if row["builtin"] or row.get("ad_kind"):
            warnings.append(f"„{row['name']}“: Standard- und AD-Sammlungen werden nicht exportiert (gibt es in jeder "
                            "Installation bzw. kommen aus dem AD) – Verweise darauf werden über den Namen zugeordnet.")
            continue
        col_ids.append(cid)
        todo += [r["id"] for r in db.q("SELECT id FROM collections WHERE parent_id=? ORDER BY sort, name", (cid,))]
    in_file = set(col_ids)
    scanner_ids = list(want["scanners"])

    def need_scanner(sid, why):
        if sid not in scanner_ids and db.val("SELECT 1 FROM custom_scanners WHERE id=?", (sid,)):
            scanner_ids.append(sid)
            extra.append(f"{db.val('SELECT name FROM custom_scanners WHERE id=?', (sid,))} ({why})")
    fields = {f"cf_{r['id']}": {"name": r["name"], "type": r["type"]}
              for r in db.q("SELECT id, name, type FROM custom_fields")}

    def tree_out(node, cname):
        node = copy.deepcopy(node)
        for it in collections_.cond_items(node):
            if it.get("missing"):
                continue
            t, col = str(it.get("table") or "computer"), str(it.get("column") or "")
            if t == "computer" and col == "collection":
                v = str(it.get("value") or "").strip()
                ref = int(v) if v.isdigit() else None
                it.pop("value", None)
                it["collection_ref"] = {"key": f"c{ref}"} if ref in in_file else _coll_ref(ref or -1)
                if it["collection_ref"].get("deleted"):
                    warnings.append(f"„{cname}“: Bedingung mit gelöschter Sammlung #{ref} – beim Import fehlt der Verweis.")
            elif t.startswith("custom:") and t[7:].isdigit():
                sid = int(t[7:])
                name = db.val("SELECT name FROM custom_scanners WHERE id=?", (sid,))
                if name:
                    need_scanner(sid, f"geprüft von Sammlung „{cname}“")
                    it["table"] = "custom"
                    it["scanner_ref"] = {"key": f"s{sid}"}
                else:
                    it["table"] = "custom"
                    it["scanner_ref"] = {"name": f"#{sid}", "deleted": True}
                    warnings.append(f"„{cname}“: Bedingung mit gelöschtem Scanner #{sid} – beim Import fehlt der Verweis.")
            elif t == "computer" and col.startswith("cf_"):
                f = fields.get(col)
                it.pop("column", None)
                it["field_ref"] = f or {"name": col, "deleted": True}
        return node

    out_cols = []
    for cid in col_ids:
        c = db.q1("SELECT * FROM collections WHERE id=?", (cid,))
        e = {"key": f"c{cid}", "name": c["name"], "type": c["type"], "description": c.get("description") or "",
             "parent": f"c{c['parent_id']}" if c.get("parent_id") in in_file else None}
        if c["type"] == "dynamic":
            e["match"] = c.get("match") or "all"
            e["filters"] = tree_out(collections_.normalize(c["filters"], c["match"]), c["name"])
        elif c["type"] == "static":
            e["members"] = [r["name"] for r in db.q("""SELECT c.name FROM collection_members m JOIN computers c ON c.id = m.computer_id
                                                       WHERE m.collection_id=? ORDER BY c.name""", (cid,))]
        out_cols.append(e)

    out_prof = []
    for pid in want["profiles"]:
        p = profiles.get(pid)
        if not p:
            continue
        if p.get("builtin_key"):
            warnings.append(f"„{p['name']}“: feste Scanprofile gibt es in jeder Installation – nicht exportiert.")
            continue
        for sid in p["scanners"]:
            need_scanner(sid, f"im Scanprofil „{p['name']}“")
        trig = [{k: v for k, v in t.items() if k != "id"} for t in p.get("triggers") or []]
        out_prof.append({"key": f"p{pid}", "name": p["name"], "description": p.get("description") or "",
                         "modules": p["modules"], "scanners": [f"s{s}" for s in p["scanners"]],
                         "scanner_types": p.get("scanner_types") or [], "enabled": bool(p.get("enabled")),
                         "targets": [{"key": f"c{c}"} if c in in_file else _coll_ref(c) for c in p.get("collections") or []],
                         "computers": [r["name"] for r in db.q(
                             f"SELECT name FROM computers WHERE id IN ({','.join('?' * len(p.get('computers') or []))}) ORDER BY name",
                             tuple(p.get("computers") or []))] if p.get("computers") else [],
                         "triggers": trig})

    out_tools = []
    for tid in want["tools"]:
        t = db.q1("SELECT * FROM tools WHERE id=?", (tid,))
        if not t:
            continue
        e = {"key": f"t{tid}", **{k: t.get(k) for k in TOOL_FIELDS}}
        e["confirm"] = bool(t.get("confirm"))
        e["scan_profile"] = db.val("SELECT name FROM scan_profiles WHERE id=?", (t["scan_profile_id"],)) if t.get("scan_profile_id") else None
        out_tools.append(e)

    out_sc = []
    for sid in scanner_ids:
        sc = db.q1("SELECT * FROM custom_scanners WHERE id=?", (sid,))
        if not sc:
            continue
        try:
            ex = json.loads(sc.get("extra") or "{}")
        except ValueError:
            ex = {}
        out_sc.append({"key": f"s{sid}", "name": sc["name"], "type": sc["type"], "target": sc["target"],
                       "extra": {k: ex.get(k) for k in SCANNER_EXTRA if k in ex}, "enabled": bool(sc["enabled"])})
    total = len(out_sc) + len(out_cols) + len(out_prof) + len(out_tools)
    if not total:
        raise HTTPException(400, "Nichts zu exportieren." + (" " + warnings[0] if warnings else ""))
    if total > MAX_ITEMS:
        raise HTTPException(400, f"Höchstens {MAX_ITEMS} Einträge auf einmal.")
    data = {"format": FORMAT, "format_version": FORMAT_VERSION, "app_version": config.VERSION, "exported_at": util.now(),
            "scanners": out_sc, "collections": out_cols, "profiles": out_prof, "tools": out_tools}
    return data, list(dict.fromkeys(warnings)), extra


def filename(data):
    kinds = [k for k in KINDS if data.get(k)]
    word = {"scanners": "scanner", "collections": "sammlungen", "profiles": "scanprofile", "tools": "tools"}
    part = word[kinds[0]] if len(kinds) == 1 else "konfiguration"
    return f"pocketknife-{part}-{util.now()[:10]}.pkconfig.json"


# --- Import -----------------------------------------------------------------------------------
def _find_coll(ref):
    """(id, None) or (None, reason) - only if unambiguous (identical names are common: "Server", "Computers")."""
    from .pkgexport import _coll_path
    if ref.get("all"):
        return profiles.ALL_COMPUTERS, None
    name = str(ref.get("name") or "").strip()
    if ref.get("deleted"):
        return None, "war schon in der exportierten Installation gelöscht"
    dn = str(ref.get("ad_dn") or "").strip()
    if dn:
        hit = [r["id"] for r in db.q("SELECT id FROM collections WHERE lower(ad_dn)=lower(?)", (dn,))]
        if len(hit) == 1:
            return hit[0], None
    cand = [r["id"] for r in db.q("SELECT id FROM collections WHERE name=? COLLATE NOCASE AND type != 'folder'", (name,))] if name else []
    path = ref.get("path")
    if len(cand) > 1 and isinstance(path, list):
        want = [str(x).strip().lower() for x in path]
        cand = [c for c in cand if [n.strip().lower() for n in _coll_path(c)] == want]
    if not cand:
        return None, "gibt es hier nicht"
    if len(cand) > 1:
        return None, "gibt es hier mehrfach (nicht eindeutig)"
    return cand[0], None


def _ref_name(ref):
    p = [str(x) for x in (ref.get("path") or []) if str(x).strip()]
    return " › ".join(p + [str(ref.get("name") or "?")])  # not " / ": the translation would split the text there


def _check(data):
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise HTTPException(400, "Das ist keine Export-Datei von POCKET KNIFE für Tools, Scanner, Scanprofile oder "
                                 "Sammlungen (*.pkconfig.json).")
    if int(data.get("format_version") or 0) > FORMAT_VERSION:
        raise HTTPException(400, "Die Datei stammt aus einer neueren Version von POCKET KNIFE – bitte erst aktualisieren.")
    keys, total = set(), 0
    for k in KINDS:
        lst = data.get(k) or []
        if not isinstance(lst, list):
            raise HTTPException(400, "Die Datei ist beschädigt.")
        for i, e in enumerate(lst):
            if not isinstance(e, dict) or not isinstance(e.get("name"), str) or not str(e.get("key") or ""):
                raise HTTPException(400, f"{LABELS[k]} {i + 1} in der Datei ist ungültig.")
            e["key"] = str(e["key"])
            if e["key"] in keys:
                raise HTTPException(400, "Die Datei ist beschädigt (doppelte Schlüssel).")
            keys.add(e["key"])
        total += len(lst)
    if not total:
        raise HTTPException(400, "Die Datei enthält nichts zum Importieren.")
    if total > MAX_ITEMS:
        raise HTTPException(400, f"Höchstens {MAX_ITEMS} Einträge auf einmal.")


def _order_collections(cols):
    """Parents and referenced collections of the file first; a loop → error."""
    by = {c["key"]: c for c in cols}
    done, out, active = set(), [], set()

    def deps(c):
        d = [c.get("parent")] if c.get("parent") in by else []
        for it in collections_.cond_items(c.get("filters") or {}):
            k = (it.get("collection_ref") or {}).get("key") if isinstance(it.get("collection_ref"), dict) else None
            if k in by:
                d.append(k)
        return d

    def visit(c, depth=0):
        k = c["key"]
        if k in done:
            return
        if k in active or depth > 60:
            raise ImportError_("Die Datei enthält Sammlungen, die aufeinander verweisen (Schleife).")
        active.add(k)
        for d in deps(c):
            visit(by[d], depth + 1)
        active.discard(k)
        done.add(k)
        out.append(c)
    for c in cols:
        visit(c)
    return out


def _unique(name, taken, limit=180):
    base = name[:limit] + " (Import)"
    cand, n = base, 2
    while cand.lower() in taken:
        cand, n = f"{base} {n}", n + 1
    return cand


def run_import(user, data, conflict="skip", parent_id=None, dry_run=True):
    """Checks (dry_run) or imports. Per entry {kind, name, status: new|copy|skip|error, new_name, message, warnings,
    missing}. "missing" = references that could not be matched (shown before the import)."""
    _check(data)
    conflict = "copy" if conflict == "copy" else "skip"
    parent = int(parent_id) if str(parent_id or "").isdigit() and int(parent_id) else None
    if parent:
        prow = db.q1("SELECT id, type, builtin FROM collections WHERE id=?", (parent,))
        if not prow or prow["builtin"] or prow["type"] == "static":
            raise HTTPException(400, "Ablageort nicht gefunden (nur Ordner oder eigene dynamische Sammlungen).")
    try:
        cols_ordered = _order_collections([c for c in data.get("collections") or []])
    except ImportError_ as err:
        raise HTTPException(400, str(err))
    results: dict = {}
    new_sc: dict = {}   # file key → scanner id
    new_col: dict = {}  # file key → collection id
    new_prof: dict = {}
    try:
        with db.tx():
            # --- custom scanners
            taken = {r["name"].strip().lower(): r["id"] for r in db.q("SELECT id, name FROM custom_scanners ORDER BY id DESC")}
            for ent in data.get("scanners") or []:
                res = _res("scanners", ent)
                results[ent["key"]] = res
                low = res["name"].lower()
                if low in taken:
                    if conflict == "skip":
                        res.update(status="skip", message="Gibt es schon – übersprungen (der vorhandene Scanner wird verwendet).")
                        new_sc[ent["key"]] = taken[low]
                        continue
                    res.update(status="copy", new_name=_unique(res["name"], taken))
                try:
                    ex = ent.get("extra") if isinstance(ent.get("extra"), dict) else {}
                    d = {"name": res["new_name"], "type": ent.get("type"), "target": str(ent.get("target") or "").strip(),
                         "extra": json.dumps({"recurse": bool(ex.get("recurse")), "namespace": str(ex.get("namespace") or "").strip(),
                                              "bytes_mode": ex.get("bytes_mode") if ex.get("bytes_mode") in ("auto", "list", "off") else "auto",
                                              "bytes_cols": str(ex.get("bytes_cols") or "")[:500]}),
                         "enabled": 1 if ent.get("enabled", True) else 0}
                    scanners.validate(d)
                    sid = db.ex("INSERT INTO custom_scanners(name, type, target, extra, enabled, created_at) VALUES(?,?,?,?,?,?)",
                                (d["name"], d["type"], d["target"], d["extra"], d["enabled"], util.now()))
                    new_sc[ent["key"]] = sid
                    taken[res["new_name"].lower()] = sid
                    if not dry_run:
                        res["id"] = sid
                except (ValueError, TypeError, KeyError) as ex_:
                    res.update(status="error", message=str(ex_) or "ungültige Daten")

            # --- collections
            fields = {r["name"].strip().lower(): r["id"] for r in db.q("SELECT id, name FROM custom_fields")}
            names = {ent["key"]: ent["name"] for k in ("scanners", "collections") for ent in data.get(k) or []}
            comps = {r["name"].strip().lower(): r["id"] for r in db.q("SELECT id, name FROM computers")}
            for ent in cols_ordered:
                res = _res("collections", ent)
                results[ent["key"]] = res
                typ = ent.get("type") if ent.get("type") in ("dynamic", "static", "folder") else None
                if not typ:
                    res.update(status="error", message="Ungültiger Typ.")
                    continue
                pk = ent.get("parent")
                if pk:
                    if pk not in new_col:
                        res.update(status="error", message="Übergeordnete Sammlung nicht importierbar.")
                        continue
                    par = new_col[pk]
                else:
                    par = parent
                same = db.q("SELECT id, name FROM collections WHERE name=? COLLATE NOCASE AND "
                            + ("parent_id=?" if par else "parent_id IS NULL") + " AND builtin=0", (res["name"], par) if par else (res["name"],))
                if same:
                    if conflict == "skip" or typ == "folder":
                        # Folders are merged: what is inside is imported into the existing folder
                        res.update(status="skip", message="Ordner gibt es schon – der Inhalt kommt hinein." if typ == "folder"
                                   else "Gibt es schon – übersprungen (die vorhandene wird verwendet).")
                        new_col[ent["key"]] = same[0]["id"]
                        continue
                    sibl = {r["name"].strip().lower() for r in db.q(
                        "SELECT name FROM collections WHERE " + ("parent_id=?" if par else "parent_id IS NULL"), (par,) if par else ())}
                    res.update(status="copy", new_name=_unique(res["name"], sibl))
                try:
                    flt: dict = {"type": "group", "match": "all", "items": []}
                    match = "all"
                    if typ == "dynamic":
                        flt = collections_.normalize(copy.deepcopy(ent.get("filters") or {}), ent.get("match") or "all")
                        match = flt.get("match") or "all"
                        _tree_in(flt, new_col, new_sc, fields, res, names)
                    cid = db.ex("INSERT INTO collections(name, parent_id, type, match, filters, description) VALUES(?,?,?,?,?,?)",
                                (res["new_name"][:200], par, typ, match, json.dumps(flt, ensure_ascii=False),
                                 str(ent.get("description") or "")[:1000]))
                    new_col[ent["key"]] = cid
                    if typ == "static":
                        found = [comps[str(n).strip().lower()] for n in ent.get("members") or [] if str(n).strip().lower() in comps]
                        miss = len(ent.get("members") or []) - len(found)
                        for i in sorted(set(found)):
                            db.ex("INSERT OR IGNORE INTO collection_members(collection_id, computer_id) VALUES(?,?)", (cid, i))
                        if miss:
                            res["warnings"].append(f"{miss} von {len(ent.get('members') or [])} Computer(n) gibt es hier nicht – "
                                                   "nicht aufgenommen.")
                    if not dry_run:
                        res["id"] = cid
                except (ImportError_, ValueError, TypeError, KeyError, AttributeError) as ex_:
                    res.update(status="error", message=str(ex_) or "ungültige Daten")

            # --- scan profiles
            ptaken = {r["name"].strip().lower(): r["id"] for r in db.q("SELECT id, name FROM scan_profiles")}
            for ent in data.get("profiles") or []:
                res = _res("profiles", ent)
                results[ent["key"]] = res
                low = res["name"].lower()
                if low in ptaken:
                    if conflict == "skip":
                        res.update(status="skip", message="Gibt es schon – übersprungen.")
                        new_prof[ent["key"]] = ptaken[low]
                        continue
                    res.update(status="copy", new_name=_unique(res["name"], ptaken))
                try:
                    scs = []
                    for k in ent.get("scanners") or []:
                        if str(k) in new_sc:
                            scs.append(new_sc[str(k)])
                        else:
                            res["missing"].append(f"Eigener Scanner ({results.get(str(k), {}).get('message') or 'fehlt in der Datei'})")
                    targets = []
                    for t in ent.get("targets") or []:
                        t = t if isinstance(t, dict) else {}
                        if t.get("key"):
                            if t["key"] in new_col:
                                targets.append(new_col[t["key"]])
                            else:
                                res["missing"].append("Ziel-Sammlung aus der Datei (nicht importierbar)")
                            continue
                        cid, why = _find_coll(t)
                        if cid is None:
                            res["missing"].append(f"Ziel-Sammlung „{_ref_name(t)}“ {why} – nicht als Ziel übernommen")
                        else:
                            targets.append(cid)
                    cmp = [comps[str(n).strip().lower()] for n in ent.get("computers") or [] if str(n).strip().lower() in comps]
                    if len(cmp) < len(ent.get("computers") or []):
                        res["warnings"].append(f"{len(ent['computers']) - len(cmp)} einzeln gewählte(r) Computer gibt es hier nicht.")
                    trig = [t for t in ent.get("triggers") or [] if isinstance(t, dict)]
                    pid = profiles.save(None, {"name": res["new_name"], "description": ent.get("description") or "",
                                               "modules": ent.get("modules") or [], "scanners": scs,
                                               "scanner_types": ent.get("scanner_types") or [], "collections": targets,
                                               "computers": cmp, "triggers": trig, "enabled": bool(ent.get("enabled", True))})
                    new_prof[ent["key"]] = pid
                    ptaken[res["new_name"].lower()] = pid
                    if not dry_run:
                        res["id"] = pid
                except (ValueError, TypeError, KeyError, AttributeError) as ex_:
                    res.update(status="error", message=str(ex_) or "ungültige Daten")

            # --- tools
            ttaken = {(r["folder"] or "").upper() + "\x00" + r["name"].strip().lower(): r["id"]
                      for r in db.q("SELECT id, name, folder FROM tools")}
            for ent in data.get("tools") or []:
                res = _res("tools", ent)
                results[ent["key"]] = res
                folder = str(ent.get("folder") or "").strip().upper()[:60]
                k = folder + "\x00" + res["name"].lower()
                if k in ttaken:
                    if conflict == "skip":
                        res.update(status="skip", message="Gibt es schon – übersprungen.")
                        continue
                    tnames = {x.split("\x00", 1)[1] for x in ttaken if x.startswith(folder + "\x00")}
                    res.update(status="copy", new_name=_unique(res["name"], tnames))
                try:
                    d = {f: ent.get(f) for f in TOOL_FIELDS}
                    d.update(name=res["new_name"], folder=folder, run_as=d.get("run_as") or "service",
                             confirm=1 if ent.get("confirm") else 0, hotkey=None)
                    pn = str(ent.get("scan_profile") or "").strip()
                    d["scan_profile_id"] = (new_prof.get(next((x["key"] for x in data.get("profiles") or [] if x.get("name") == pn), ""))
                                            or (db.val("SELECT id FROM scan_profiles WHERE name=? COLLATE NOCASE", (pn,)) if pn else None))
                    if pn and not d["scan_profile_id"]:
                        res["missing"].append(f"Scanprofil „{pn}“ gibt es hier nicht – „Danach scannen“ nutzt das Standardprofil")
                    tools.validate(d)
                    cols = list(TOOL_FIELDS) + ["scan_profile_id", "hotkey", "created_at"]
                    tid = db.ex(f"INSERT INTO tools({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
                                tuple(d[c] for c in TOOL_FIELDS) + (d["scan_profile_id"], None, util.now()))
                    ttaken[folder + "\x00" + res["new_name"].lower()] = tid
                    if not dry_run:
                        res["id"] = tid
                except (ValueError, TypeError, KeyError, AttributeError) as ex_:
                    res.update(status="error", message=str(ex_) or "ungültige Daten")
            if dry_run:
                raise _DryRun()
    except _DryRun:
        pass
    items = [results[ent["key"]] for k in KINDS for ent in data.get(k) or [] if ent["key"] in results]
    if not dry_run:
        collections_.invalidate()
        collections_.invalidate_scanner()
        from . import health
        health.invalidate()
    return {"items": items, "dry_run": dry_run,
            "count": {s: sum(1 for x in items if x["status"] == s) for s in ("new", "copy", "skip", "error")},
            "missing": sum(len(x["missing"]) for x in items if x["status"] in ("new", "copy"))}


def _res(kind, e):
    name = str(e.get("name") or "").strip()[:200] or "?"
    return {"kind": kind, "label": LABELS[kind], "name": name, "new_name": name, "status": "new", "message": "",
            "warnings": [], "missing": []}


def _tree_in(flt, new_col, new_sc, fields, res, names):
    """Translate the references of an imported filter; what cannot be matched stays as "missing" (yellow "!")."""
    for it in collections_.cond_items(flt):
        if it.get("missing"):
            res["missing"].append(collections_.missing_text(it["missing"]))
            continue
        if isinstance(it.get("collection_ref"), dict):
            ref = it.pop("collection_ref")
            it["table"], it["column"] = "computer", "collection"
            if ref.get("key"):
                cid = new_col.get(ref["key"])
                why = None if cid else "aus der Datei nicht importierbar"
                name = names.get(ref["key"], ref["key"])
            else:
                cid, why = _find_coll(ref)
                name = _ref_name(ref)
            if cid:
                it["value"] = str(cid)
            else:
                it["value"] = ""
                it["missing"] = {"kind": "collection", "name": name}
                res["missing"].append(f"Sammlung „{name}“ {why}")
        elif isinstance(it.get("scanner_ref"), dict):
            ref = it.pop("scanner_ref")
            sid = new_sc.get(ref.get("key") or "")
            if sid:
                it["table"] = f"custom:{sid}"
            else:
                it["table"] = "custom:0"
                it["missing"] = {"kind": "scanner", "name": ref.get("name") or names.get(ref.get("key") or "") or "?"}
                res["missing"].append(f"Eigener Scanner „{it['missing']['name']}“ nicht importierbar")
        elif isinstance(it.get("field_ref"), dict):
            ref = it.pop("field_ref")
            fid = fields.get(str(ref.get("name") or "").strip().lower())
            it["table"] = "computer"
            if fid:
                it["column"] = f"cf_{fid}"
            else:
                it["column"] = "cf_0"
                it["missing"] = {"kind": "field", "name": ref.get("name") or "?"}
                res["missing"].append(f"Feld „{it['missing']['name']}“ gibt es hier nicht – bitte unter Einstellungen → "
                                      "Felder anlegen und dann neu zuordnen")
        elif it.get("table") == "custom":
            it["table"] = "custom:0"
            it["missing"] = {"kind": "scanner", "name": "?"}
            res["missing"].append("Eigener Scanner ohne Angabe")
