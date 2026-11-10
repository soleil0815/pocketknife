"""Export and import packages (file *.pkpackages.json) - e.g. into a second POCKET KNIFE installation.

The file contains no IDs of this installation: collections and scan profiles are matched by their name,
nested packages are exported automatically and re-linked on import.
Paths in the repository are always relative (e.g. "7zip\\24.08") - the repository server is set only under
Einstellungen → Repository. A UNC path hard-coded into commands/scripts is reported on export.

Packages contain no passwords (accounts are stored encrypted under Einstellungen). Scripts are exported
as they are in the package.
"""
import copy
import json
import re

from fastapi import HTTPException

from . import access, config, db, deploy, repofiles, steps, util

FORMAT = "pocketknife-packages"
FORMAT_VERSION = 1
MAX_PACKAGES = 500
FIELDS = ("name", "version", "description", "run_as", "timeout_min", "reboot", "rescan", "notify_user", "folder_name")
UNC_RE = re.compile(r"\\\\[A-Za-z0-9._$-]+\\[^\s'\"`;|<>]*")
TEXT_KEYS = ("command", "script", "arguments", "message", "target", "path")


class ImportError_(ValueError):
    pass


# --- Export -----------------------------------------------------------------------------------
def _conds_out(c):
    c = copy.deepcopy(c or steps.default_conditions())
    out = []
    for cc in c.get("collections") or []:
        if cc.get("id") == 0:
            out.append({"all": True, "mode": cc.get("mode", "member")})
        else:
            row = db.q1("SELECT name, ad_dn FROM collections WHERE id=?", (cc.get("id"),))
            if row:
                e = {"name": row["name"], "mode": cc.get("mode", "member"), "path": _coll_path(cc.get("id"))}
                if row.get("ad_dn"):
                    e["ad_dn"] = row["ad_dn"]
                out.append(e)
            else:
                out.append({"missing_id": cc.get("id"), "mode": cc.get("mode")})
    c["collections"] = out
    return c


def _coll_path(cid):
    """Names of the parent collections (topmost first) - names alone are not unique (Audit 2.29)."""
    names, seen = [], set()
    p = db.val("SELECT parent_id FROM collections WHERE id=?", (cid,))
    while p and p not in seen and len(names) < 20:
        seen.add(p)
        r = db.q1("SELECT name, parent_id FROM collections WHERE id=?", (p,))
        if not r:
            break
        names.insert(0, r["name"])
        p = r["parent_id"]
    return names


def _find_collection(cc, label):
    """Collection for an imported condition - only if unambiguous (identical names are common: "Server", "Computers").
    A wrongly matched "not member of" condition would let the package run on more computers."""
    name = str(cc.get("name") or "").strip()
    dn = str(cc.get("ad_dn") or "").strip()
    if dn:
        hit = [r["id"] for r in db.q("SELECT id FROM collections WHERE lower(ad_dn)=lower(?)", (dn,))]
        if len(hit) == 1:
            return hit[0]
    cand = [r["id"] for r in db.q("SELECT id FROM collections WHERE name=? COLLATE NOCASE", (name,))] if name else []
    path = cc.get("path")
    if len(cand) > 1 and isinstance(path, list):
        want = [str(x).strip().lower() for x in path]
        cand = [c for c in cand if [n.strip().lower() for n in _coll_path(c)] == want]
    if not cand:
        raise ImportError_(f"{label}: Sammlung „{name or '?'}“ gibt es hier nicht – bitte zuerst anlegen.")
    if len(cand) > 1:
        raise ImportError_(f"{label}: Sammlung „{name}“ gibt es hier mehrfach – nicht eindeutig. Bitte eine der Sammlungen "
                           "umbenennen oder die Bedingung nach dem Import setzen.")
    return cand[0]


def _profile_name(pid):
    return db.val("SELECT name FROM scan_profiles WHERE id=?", (pid,)) if pid else None


def _texts(st):
    for k in TEXT_KEYS:
        v = st.get(k)
        if isinstance(v, str) and v:
            yield v
    for e in st.get("entries") or []:
        if isinstance(e, dict):
            yield str(e.get("data") or "")


def export(user, ids):
    """File content (dict) + notes. Nested packages are included."""
    ids = [int(i) for i in (ids or []) if str(i).isdigit()]
    if not ids:
        raise HTTPException(400, "Keine Pakete gewählt.")
    if len(ids) > MAX_PACKAGES:
        raise HTTPException(400, f"Höchstens {MAX_PACKAGES} Pakete auf einmal.")
    rows, order, todo = {}, [], list(ids)
    while todo:
        pid = todo.pop(0)
        if pid in rows:
            continue
        try:
            r = access.need_package(user, pid)  # OU admin: only visible ones (central and own)
        except HTTPException:
            if pid in ids:
                raise
            outer = next((rows[o]["name"] for o in order
                          if any(s.get("type") == "nested" and s.get("package_id") == pid
                                 for _k, s in steps.iter_steps(steps.normalize(deploy.row_to_package(rows[o]))))), "?")
            raise HTTPException(400, f"„{outer}“ verwendet ein verschachteltes Paket, das Sie nicht sehen – "
                                     "dieses Paket lässt sich nicht exportieren.")
        rows[pid] = r
        order.append(pid)
        for _k, s in steps.iter_steps(steps.normalize(deploy.row_to_package(r))):
            if s.get("type") == "nested" and s.get("package_id") and s["package_id"] not in rows:
                todo.append(s["package_id"])
        if len(rows) > MAX_PACKAGES:
            raise HTTPException(400, f"Höchstens {MAX_PACKAGES} Pakete auf einmal (mit verschachtelten).")
    root = str(db.setting("repo_path") or "").strip().rstrip("\\/").lower()
    warnings, out = [], []
    for pid in order:
        p = steps.normalize(deploy.row_to_package(rows[pid]))
        e = {"key": f"p{pid}", **{k: p.get(k) for k in FIELDS},
             "rescan_profile": _profile_name(p.get("rescan_profile_id")),
             "conditions": _conds_out(p.get("conditions")), "steps": {}}
        for ph, _label in steps.PHASES:
            lst = []
            for i, s in enumerate(p["steps"].get(ph) or []):
                s = copy.deepcopy(s)
                s.pop("package", None)
                if s.get("type") == "nested":
                    s["package_key"] = f"p{s.pop('package_id', 0)}"
                if s.get("type") == "scan":
                    s["profile"] = _profile_name(s.pop("profile_id", None))
                s["conditions"] = _conds_out(s.get("conditions"))
                for t in _texts(s):
                    for m in UNC_RE.findall(t):
                        why = "Pfad des Repositorys" if root and m.lower().startswith(root) else "fester Netzwerkpfad"
                        warnings.append(f"„{p['name']}“, {steps.step_title(s)[:60]}: {why} {m[:120]} – wird nicht angepasst.")
                lst.append(s)
            e["steps"][ph] = lst
        for cc in e["conditions"]["collections"] + [c for st in sum(e["steps"].values(), []) for c in st["conditions"]["collections"]]:
            if cc.get("missing_id"):
                warnings.append(f"„{p['name']}“: Bedingung mit gelöschter Sammlung #{cc['missing_id']} – der Import lehnt das Paket ab.")
        out.append(e)
    extra = [rows[i]["name"] for i in order if i not in ids]
    data = {"format": FORMAT, "format_version": FORMAT_VERSION, "app_version": config.VERSION,
            "exported_at": util.now(), "packages": out}
    return data, sorted(set(warnings)), extra


# --- Import -----------------------------------------------------------------------------------
class _DryRun(Exception):
    pass


def _conds_in(c, label):
    if not isinstance(c, dict):
        return steps.default_conditions()
    c = copy.deepcopy(c)
    out = []
    for cc in c.get("collections") or []:
        if not isinstance(cc, dict):
            raise ImportError_(f"{label}: ungültige Sammlungs-Bedingung.")
        mode = "not_member" if cc.get("mode") == "not_member" else "member"
        if cc.get("all"):
            out.append({"id": 0, "mode": mode})
            continue
        # Omitting it would cancel the condition (the package would then run on more computers) - therefore reject
        out.append({"id": _find_collection(cc, label), "mode": mode})
    c["collections"] = out
    return c


def _visible_by_name(user, tenant):
    """Existing packages that count as "already exists" and may be nested: central ones and those of the
    target tenant - never those of another tenant (its OU admin could change them; code would then run on
    foreign computers). With the same name, the target tenant's package takes precedence."""
    m = {}
    for r in db.q("SELECT id, name, tenant_id FROM packages ORDER BY (tenant_id IS NULL OR tenant_id = 0), id"):
        t = r.get("tenant_id") or None
        if access.can_see_package(user, r) and (t is None or t == tenant):
            m.setdefault(r["name"].strip().lower(), r["id"])
    return m


def _order(pkgs):
    """Nested ones first (depth-first search); loops → error."""
    by_key = {p["key"]: p for p in pkgs}
    done, out, active = set(), [], set()

    def visit(p, depth=0):
        k = p["key"]
        if k in done:
            return
        if k in active or depth > 20:
            raise ImportError_("Die Datei enthält Pakete, die sich gegenseitig verschachteln (Schleife).")
        active.add(k)
        for ph in ("pre", "main", "post"):
            for s in (p.get("steps") or {}).get(ph) or []:
                if isinstance(s, dict) and s.get("type") == "nested" and s.get("package_key") in by_key:
                    visit(by_key[s["package_key"]], depth + 1)
        active.discard(k)
        done.add(k)
        out.append(p)
    for p in pkgs:
        visit(p)
    return out


def run_import(user, data, tenant_id=None, conflict="skip", dry_run=True, pkg_values=None, allow_dup=False):
    """Checks (dry_run) or imports. Returns {name, status, new_name, message, warnings} per package.
    status: new | copy | skip | error. Packages with an error (and those that nest them) are skipped.
    pkg_values: check/conversion function from main (_pkg_values) - the same rules as when saving in the editor."""
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise HTTPException(400, "Das ist keine Paket-Datei von POCKET KNIFE (*.pkpackages.json).")
    if int(data.get("format_version") or 0) > FORMAT_VERSION:
        raise HTTPException(400, "Die Datei stammt aus einer neueren Version von POCKET KNIFE – bitte erst aktualisieren.")
    pkgs = data.get("packages")
    if not isinstance(pkgs, list) or not pkgs:
        raise HTTPException(400, "Die Datei enthält keine Pakete.")
    if len(pkgs) > MAX_PACKAGES:
        raise HTTPException(400, f"Höchstens {MAX_PACKAGES} Pakete auf einmal.")
    keys = set()
    for i, p in enumerate(pkgs):
        if not isinstance(p, dict) or not isinstance(p.get("name"), str) or not str(p.get("key") or ""):
            raise HTTPException(400, f"Paket {i + 1} in der Datei ist ungültig.")
        p["key"] = str(p["key"])
        if p["key"] in keys:
            raise HTTPException(400, "Die Datei ist beschädigt (doppelte Schlüssel).")
        keys.add(p["key"])
    try:
        ordered = _order(pkgs)
    except ImportError_ as e:
        raise HTTPException(400, str(e))
    if access.scoped(user):
        tenant = access.own_tenant_for(user, tenant_id)
    else:
        tenant = int(tenant_id) if str(tenant_id or "").isdigit() and int(tenant_id) else None
        if tenant and not db.val("SELECT 1 FROM tenants WHERE id=?", (tenant,)):
            raise HTTPException(400, "Mandant nicht gefunden.")
    conflict = "copy" if conflict == "copy" else "skip"
    results, new_ids = {}, {}
    try:
        with db.tx():
            names = _visible_by_name(user, tenant)
            taken = {r["name"].strip().lower() for r in db.q("SELECT name FROM packages")}
            for p in ordered:
                name = p["name"].strip()[:200]
                res = {"name": name, "status": "new", "new_name": name, "message": "", "warnings": []}
                results[p["key"]] = res
                low = name.lower()
                if low in names:
                    if conflict == "skip":
                        res.update(status="skip", message="Gibt es schon – übersprungen (das vorhandene Paket wird verwendet).")
                        new_ids[p["key"]] = names[low]
                        continue
                    n, base = 2, name[:180] + " (Import)"
                    cand = base
                    while cand.lower() in taken:
                        cand, n = f"{base} {n}", n + 1
                    res.update(status="copy", new_name=cand)
                try:
                    pdata = {k: p.get(k) for k in FIELDS}
                    pdata["name"] = res["new_name"]
                    pdata["tenant_id"] = tenant
                    pdata["conditions"] = _conds_in(p.get("conditions"), "Paket-Bedingungen")
                    prof = str(p.get("rescan_profile") or "").strip()
                    pdata["rescan_profile_id"] = db.val("SELECT id FROM scan_profiles WHERE name=? COLLATE NOCASE", (prof,)) if prof else None
                    if prof and not pdata["rescan_profile_id"]:
                        res["warnings"].append(f"Scanprofil „{prof}“ fehlt – es wird das Standardprofil verwendet.")
                    st_in = p.get("steps") if isinstance(p.get("steps"), dict) else {}
                    pdata["steps"] = {}
                    for ph, label in steps.PHASES:
                        lst = []
                        for i, s in enumerate(st_in.get(ph) or []):
                            if not isinstance(s, dict):
                                raise ImportError_(f"{label}, Schritt {i + 1}: ungültig.")
                            s = copy.deepcopy(s)
                            s["conditions"] = _conds_in(s.get("conditions"), f"{label}, Schritt {i + 1}")
                            if s.get("type") == "nested":
                                k = str(s.pop("package_key", ""))
                                if k not in new_ids:
                                    why = results.get(k, {}).get("message") if k in results else "fehlt in der Datei"
                                    raise ImportError_(f"{label}, Schritt {i + 1}: verschachteltes Paket nicht importierbar ({why}).")
                                s["package_id"] = new_ids[k]
                            if s.get("type") == "scan":
                                pn = str(s.pop("profile", None) or "").strip()
                                s["profile_id"] = db.val("SELECT id FROM scan_profiles WHERE name=? COLLATE NOCASE", (pn,)) if pn else None
                                if pn and not s["profile_id"]:
                                    res["warnings"].append(f"Scanprofil „{pn}“ fehlt – Schritt „Scannen“ nutzt das Standardprofil.")
                            if s.get("type") != "nested":
                                s.pop("package_id", None)  # only nested steps refer to packages
                            lst.append(s)
                        pdata["steps"][ph] = lst
                    vals = pkg_values(pdata, user=user)
                    # Duplicate lock (2.27): the same software in another scope is not imported
                    ids, pending = repofiles.guard_package(vals, None, user, allow=allow_dup, io=False)  # in the transaction: without release
                    cols = deploy.PKG_FIELDS + ["created_at", "updated_at"]
                    pid = db.ex(f"INSERT INTO packages({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
                                tuple(vals[k] for k in deploy.PKG_FIELDS) + (util.now(), util.now()))
                    repofiles.store_idents(pid, ids, pending)
                    if access.scoped(user):  # 2.30: imported by the OU admin - they may change it
                        db.ex("UPDATE packages SET ou_edit=1 WHERE id=?", (pid,))
                    new_ids[p["key"]] = pid
                    if not dry_run:
                        res["id"] = pid
                    taken.add(res["new_name"].lower())
                    names.setdefault(res["new_name"].lower(), pid)  # same name twice in the file
                    if vals.get("tenant_id"):
                        from .main import net_hints  # 2.29.1: notice, no block
                        for x in net_hints({"steps": json.loads(vals["steps"])})[:5]:
                            res["warnings"].append(f"Zugriff auf Freigaben/Internet im Skript: {x}")
                except (ImportError_, ValueError, TypeError, KeyError, AttributeError) as e:
                    res.update(status="error", message=str(e) or "ungültige Daten")
                except HTTPException as e:
                    res.update(status="error", message=str(e.detail))
            if dry_run:
                raise _DryRun()
    except _DryRun:
        pass
    order = [results[p["key"]] for p in pkgs if p["key"] in results]
    return {"items": order, "dry_run": dry_run,
            "count": {s: sum(1 for x in order if x["status"] == s) for s in ("new", "copy", "skip", "error")}}


def filename():
    return f"pocketknife-pakete-{util.now()[:10]}.pkpackages.json"


def dumps(data):
    return json.dumps(data, ensure_ascii=False, indent=1)
