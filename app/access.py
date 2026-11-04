"""OU admins (tenant admins): restricted management for the computers of one tenant.

- Main admins = members of the access group (PK_ACCESS_GROUP, e.g. POCKETKNIFE-Admins) and the emergency access:
  may do everything, as before.
- OU admins = members of a tenant's AD group (Settings → Mandanten → "OU-Admins"), NOT in the
  access group. The tenant's group allows login - but only for the computers of its collection (e.g. an
  OU). Someone in several tenant groups manages all of those tenants.

Enforced on the server, never only in the UI:
1. Allow list ROUTES: for OU admins every endpoint that is not explicitly listed here is blocked (a new,
   forgotten endpoint is therefore closed, not open). The self-test checks that every route is deliberately classified.
2. Path check: routes with a computer in the path ({cid}) check the computer against the scope (otherwise 404 - foreign
   computers are not even confirmed).
3. The handlers themselves filter lists (computers, software, log ...) and check computer IDs in the request body.
"""
import json
import re
import threading
import time

from fastapi import HTTPException

from . import db

_cache = {}
_clock = threading.Lock()
TTL = 30  # seconds - new computers in the OU are visible at the latest then


def scoped(user):
    """True if the user is an OU admin (restricted)."""
    return bool(user) and user.get("scope") is not None


def tenant_ids(user):
    """Tenants of the OU admin (list) - None for main admins (all)."""
    if not scoped(user):
        return None
    return [int(t) for t in user["scope"]]


def tenants(user):
    """Rows of the OU admin's tenants."""
    ids = tenant_ids(user) or []
    if not ids:
        return []
    return db.q(f"SELECT * FROM tenants WHERE id IN ({','.join('?' * len(ids))}) ORDER BY name COLLATE NOCASE", ids)


def may_build(user):
    """May create own packages/schedules? Main admins always; OU admins if allowed for one of their tenants."""
    if not scoped(user):
        return True
    return any(t.get("allow_packages") for t in tenants(user))


def may_script(user, tenant_id=None):
    """May write own code into packages (command, PowerShell, custom uninstall command)? Main admins always;
    OU admins only if the tenant allows both "own packages" AND "script steps" (2.29.1). Scripts as a file
    in the tenant's repository folder (.ps1/.cmd via "Installieren") do not need this."""
    if not scoped(user):
        return True
    return any(t.get("allow_packages") and t.get("allow_scripts") and (tenant_id is None or t["id"] == tenant_id)
               for t in tenants(user))


def build_tenants(user):
    """Tenants for which the OU admin may build packages/schedules."""
    return [t for t in tenants(user) if t.get("allow_packages")]


def tenant_cols(t):
    """Collections of a tenant (2.29.9: several possible) - list of IDs. Older versions: only collection_id."""
    raw = (t or {}).get("collection_ids")
    out = []
    if raw:
        try:
            out = [int(x) for x in json.loads(raw) if str(x).lstrip("-").isdigit()]
        except (ValueError, TypeError):
            out = []
    if not out and (t or {}).get("collection_id"):
        out = [int(t["collection_id"])]
    return list(dict.fromkeys(out))


def computer_ids(user):
    """Allowed computer IDs (set) - None for main admins (all)."""
    if not scoped(user):
        return None
    key = tuple(sorted(tenant_ids(user)))
    now = time.time()
    with _clock:
        hit = _cache.get(key)
        if hit and now - hit[0] < TTL:
            return set(hit[1])
    from . import collections_
    ids = set()
    for t in tenants(user):
        for col in tenant_cols(t):  # tenant without a collection: no computers (never "all")
            try:
                ids.update(collections_.member_ids(col))
            except Exception:
                continue
    with _clock:
        _cache[key] = (now, frozenset(ids))
    return ids


def flush():
    with _clock:
        _cache.clear()


def can_computer(user, cid):
    allowed = computer_ids(user)
    return allowed is None or int(cid or 0) in allowed


def need_computer(user, cid):
    """404 if the computer is not in scope (foreign computers are not confirmed)."""
    try:
        cid = int(cid or 0)
    except (TypeError, ValueError):
        raise HTTPException(404, "Computer nicht gefunden")
    if not can_computer(user, cid):
        raise HTTPException(404, "Computer nicht gefunden")
    return cid


def _ints(ids):
    """2.32.5: computer IDs as numbers - nonsense in the request is a 400, not an internal error."""
    if ids is None:
        return []
    if not isinstance(ids, (list, tuple, set)):
        raise HTTPException(400, "Ungültige Computer-IDs.")
    try:
        return [int(i) for i in ids]
    except (TypeError, ValueError):
        raise HTTPException(400, "Ungültige Computer-IDs.")


def check_ids(user, ids):
    """Computer IDs from a request: all must be in scope (otherwise 403 - never silently more)."""
    ids = _ints(ids)
    allowed = computer_ids(user)
    if allowed is None:
        return ids
    bad = [i for i in ids if i not in allowed]
    if bad:
        raise HTTPException(403, f"{len(bad)} der gewählten Computer gehören nicht zu Ihrem Bereich.")
    return ids


def limit_ids(user, ids):
    """Trim computer IDs to the scope (for "all members of a collection")."""
    allowed = computer_ids(user)
    ids = _ints(ids)
    return ids if allowed is None else [i for i in ids if i in allowed]


def sql_in(user, col):
    """SQL condition "col is in scope" + parameters - ("1=1", []) for main admins.
    Passed as a JSON list: no limit on SQL variables with many computers."""
    allowed = computer_ids(user)
    if allowed is None:
        return "1=1", []
    return f"{col} IN (SELECT value FROM json_each(?))", [json.dumps(sorted(allowed))]


# --- Packages --------------------------------------------------------------------------------
def can_see_package(user, row):
    """View and distribute: main admins everything; OU admins (2.37) central packages and those of their own tenants -
    packages of other tenants (including steps, paths, scripts) are not visible to them. Only their own can be
    changed (can_edit_package)."""
    if not row:
        return False
    if not scoped(user):
        return True
    t = row.get("tenant_id")
    return not t or int(t) in tenant_ids(user)


def can_edit_package(user, row):
    """Change/delete: only packages of an own tenant with "own packages allowed" - never central ones.
    2.30: and only if OU admins may change them (created by them or released by the main admin)."""
    if not scoped(user):
        return True
    t = row.get("tenant_id") if row else None
    return bool(t) and int(t) in [x["id"] for x in build_tenants(user)] and bool(row.get("ou_edit"))


def need_package(user, pid):
    row = db.q1("SELECT * FROM packages WHERE id=?", (int(pid or 0),))
    if not row or not can_see_package(user, row):
        raise HTTPException(404, "Paket nicht gefunden")
    return row


def own_tenant_for(user, requested):
    """Tenant for a new/changed package or schedule of an OU admin: must be one of their tenants with
    "own packages allowed"; without a value, the first of them."""
    ok = [t["id"] for t in build_tenants(user)]
    if not ok:
        raise HTTPException(403, "Eigene Pakete und Zeitpläne sind für Ihren Mandanten nicht freigegeben.")
    try:
        req = int(requested) if requested not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        req = None
    if req is None:
        return ok[0]
    if req not in ok:
        raise HTTPException(403, "Dieser Mandant gehört nicht zu Ihrem Bereich.")
    return req


def check_nested(user, pkg_steps, tenant_id=None):
    """Nested packages: only central ones or those of the same tenant as the outer package - for everyone, including
    main admins. Otherwise a (different) tenant could change what runs along inside this package - for a central package on
    all computers. This applies only to embedding (viewing: can_see_package)."""
    from . import steps as stp
    own = int(tenant_id or 0)
    for _k, s in stp.iter_steps(stp.normalize({"steps": pkg_steps})):
        if s.get("type") == "nested" and s.get("package_id"):
            row = need_package(user, s["package_id"])
            t = int(row.get("tenant_id") or 0)
            if t and t != own:
                raise HTTPException(403, f"Verschachteltes Paket „{row['name']}“ gehört einem anderen Mandanten – einbauen "
                                         "lassen sich nur zentrale Pakete und Pakete desselben Mandanten.")


# --- Deployments / schedules -------------------------------------------------------------
def deployment_targets_in_scope(user, did):
    """(all targets in scope?, at least one in scope?)"""
    allowed = computer_ids(user)
    ids = [r["computer_id"] for r in db.q("SELECT computer_id FROM deployment_targets WHERE deployment_id=?", (int(did),))]
    if allowed is None:
        return True, True
    inside = [i for i in ids if i in allowed]
    return (bool(ids) and len(inside) == len(ids)), bool(inside)


def need_deployment(user, did, write=False):
    """Read: at least one target in scope. Change (cancel, repeat, delete): ALL targets in scope."""
    if not db.val("SELECT 1 FROM deployments WHERE id=?", (int(did or 0),)):
        raise HTTPException(404)
    if not scoped(user):
        return
    full, some = deployment_targets_in_scope(user, did)
    if not some:
        raise HTTPException(404)
    if write and not full:
        raise HTTPException(403, "Diese Bereitstellung betrifft auch Computer außerhalb Ihres Bereichs.")


def can_see_schedule(user, s):
    """2.30: OU admins see the schedules of their tenant and - read-only, only with their computers - the central ones
    that reach at least one of their computers (the others are none of their business)."""
    if not scoped(user):
        return True
    if not s:
        return False
    if s.get("tenant_id"):
        return int(s["tenant_id"]) in tenant_ids(user)
    from . import schedules
    return bool(schedules.members(s) & computer_ids(user))


def can_edit_schedule(user, s):
    """Change/delete: main admins everything; OU admins only schedules of their tenant that they may change (2.30)."""
    if not scoped(user):
        return True
    return bool(s and s.get("tenant_id") and int(s["tenant_id"]) in [t["id"] for t in build_tenants(user)] and s.get("ou_edit"))


def need_schedule(user, sid, write=False, run=False):
    from . import schedules
    s = schedules.get(int(sid or 0))
    if not s or not can_see_schedule(user, s):
        raise HTTPException(404, "Zeitplan nicht gefunden")
    if write and not can_edit_schedule(user, s):
        raise HTTPException(403, "Diesen Zeitplan darf nur ein Hauptadmin ändern – als Vorlage kopieren geht.")
    if run and scoped(user) and not s.get("tenant_id"):
        raise HTTPException(403, "Zentrale Zeitpläne startet nur ein Hauptadmin.")
    return s


# --- Allow list of endpoints for OU admins ---------------------------------------------------
def _p_cid(user, pp):
    need_computer(user, pp.get("cid"))


def _p_software(user, pp):
    cid = db.val("SELECT computer_id FROM software WHERE id=?", (int(pp.get("sid") or 0),))
    if cid is None or not can_computer(user, cid):
        raise HTTPException(404)


def _p_du_scan(user, pp):
    cid = db.val("SELECT computer_id FROM disk_usage WHERE id=?", (int(pp.get("sid") or 0),))
    if cid is None or not can_computer(user, cid):
        raise HTTPException(404, "Analyse nicht gefunden")


def _p_du_job(user, pp):
    from . import diskusage
    j = diskusage.job(pp.get("jid"))
    if not j or not can_computer(user, j.get("computer_id")):
        raise HTTPException(404, "Auftrag nicht gefunden")


def _p_tooljob(user, pp):
    from . import tools
    j = tools.job(pp.get("jid"))
    if not j or j.get("user") != user["username"]:
        raise HTTPException(404, "Job nicht (mehr) vorhanden")


def _p_pkg(user, pp):
    need_package(user, pp.get("pid"))


def _p_pkg_edit(user, pp):
    if not can_edit_package(user, need_package(user, pp.get("pid"))):
        raise HTTPException(403, "Zentrale Pakete und Pakete anderer Mandanten können Sie nicht ändern.")


def _p_build(user, pp):
    if not may_build(user):
        raise HTTPException(403, "Eigene Pakete und Zeitpläne sind für Ihren Mandanten nicht freigegeben.")


def _p_dep(user, pp):
    need_deployment(user, pp.get("did"))


def _p_dep_w(user, pp):
    need_deployment(user, pp.get("did"), write=True)


def _p_sched(user, pp):
    need_schedule(user, pp.get("sid"))


def _p_sched_w(user, pp):
    need_schedule(user, pp.get("sid"), write=True)


def _p_sched_run(user, pp):
    need_schedule(user, pp.get("sid"), run=True)


def can_edit_collection(user, row):
    """2.30: main admins everything (except default/AD collections, which the handler checks); OU admins only collections that
    their tenant created - and only with "own packages allowed"."""
    if not scoped(user):
        return True
    t = (row or {}).get("tenant_id")
    return bool(t) and int(t) in [x["id"] for x in build_tenants(user)]


def _p_coll_w(user, pp):
    row = db.q1("SELECT * FROM collections WHERE id=?", (int(pp.get("cid") or 0),))
    if not row:
        raise HTTPException(404, "Sammlung nicht gefunden")
    if not can_edit_collection(user, row):
        raise HTTPException(403, "Diese Sammlung darf nur ein Hauptadmin ändern – als Kopie für Ihren Mandanten geht.")


def _p_coll_r(user, pp):
    from . import main as _main
    vis = _main._visible_collections(user)
    if vis is not None and int(pp.get("cid") or 0) not in vis:
        raise HTTPException(404, "Sammlung nicht gefunden")


def _p_creport(user, pp):
    pass  # own reports: reading allowed, results restricted to own computers (handler)


def _p_repo(user, pp):
    """Repository: only with "own packages allowed" and a repository folder of the tenant (the handler restricts
    the path to it)."""
    if not [t for t in build_tenants(user) if (t.get("repo_folder") or "").strip()]:
        # 2.27: folder automatically ("Mandanten\\<Name>") - as soon as the tenant may build own packages
        from . import repofiles
        for t in build_tenants(user):
            try:
                repofiles.ensure_tenant_folder(t["id"])
            except Exception:
                pass
        flush()
    if not [t for t in build_tenants(user) if (t.get("repo_folder") or "").strip()]:
        raise HTTPException(403, "Für Ihren Mandanten ist kein Repository-Ordner eingerichtet.")


OPEN = None
ROUTES = {
    # Session
    ("GET", "/api/me"): OPEN,
    ("GET", "/api/news"): OPEN,             # Handler: OU admins only news for everyone, never update notices
    ("POST", "/api/news/seen"): OPEN,
    # Overview and computers (handlers filter lists)
    ("GET", "/api/dashboard"): OPEN,
    ("GET", "/api/computers"): OPEN,
    ("GET", "/api/computers/{cid}"): _p_cid,
    ("GET", "/api/computers/{cid}/cat/{cat}"): _p_cid,
    ("PUT", "/api/computers/{cid}/fields"): _p_cid,
    ("PUT", "/api/computers/{cid}"): _p_cid,              # notes on the computer
    ("POST", "/api/computers/{cid}/loggedon"): _p_cid,
    ("POST", "/api/computers/{cid}/service"): _p_cid,
    ("POST", "/api/computers/{cid}/process/kill"): _p_cid,
    ("GET", "/api/computers/{cid}/files"): _p_cid,
    ("GET", "/api/computers/{cid}/files/search"): _p_cid,
    ("POST", "/api/computers/{cid}/files/mkdir"): _p_cid,
    ("POST", "/api/computers/{cid}/files/rename"): _p_cid,
    ("POST", "/api/computers/{cid}/files/delete"): _p_cid,
    ("POST", "/api/computers/{cid}/files/transfer"): _p_cid,
    ("GET", "/api/computers/{cid}/files/download"): _p_cid,
    ("POST", "/api/computers/{cid}/files/upload"): _p_cid,
    ("GET", "/api/computers/{cid}/eventlog"): _p_cid,
    ("POST", "/api/computers/{cid}/live/{what}"): _p_cid,
    ("POST", "/api/computers/{cid}/secrets/{what}"): _p_cid,
    ("GET", "/api/computers/{cid}/diskusage"): _p_cid,
    ("POST", "/api/computers/{cid}/diskusage"): _p_cid,
    ("GET", "/api/diskusage/jobs/{jid}"): _p_du_job,
    ("GET", "/api/diskusage/{sid}"): _p_du_scan,
    ("DELETE", "/api/diskusage/{sid}"): _p_du_scan,
    ("POST", "/api/computers/{cid}/test"): _p_cid,
    ("POST", "/api/computers/{cid}/action"): _p_cid,
    ("POST", "/api/computers/heartbeat"): OPEN,        # handler checks the IDs
    ("POST", "/api/computers/power-check"): OPEN,      # 2.34: handler checks the IDs
    ("POST", "/api/computers/scan"): OPEN,             # handler checks the IDs
    ("GET", "/api/software/{sid}/uninstall"): _p_software,
    ("POST", "/api/uninstall"): OPEN,                  # handler checks installations/computers
    # Tools (only main admins create/change)
    ("GET", "/api/tools"): OPEN,
    ("POST", "/api/tools/{tid}/run"): OPEN,            # handler checks the computer
    ("POST", "/api/tools/builtin/{what}"): OPEN,       # handler checks the computer
    ("GET", "/api/computers/{cid}/wake"): _p_cid,      # 2.31: result of Wake-on-LAN
    ("POST", "/api/tools/{tid}/jobs"): OPEN,           # handler checks the IDs
    ("GET", "/api/tooljobs/{jid}"): _p_tooljob,
    # Read only (for the UI)
    ("GET", "/api/scanners"): OPEN,
    ("GET", "/api/scanprofiles"): OPEN,
    ("GET", "/api/fields"): OPEN,
    ("GET", "/api/collections"): OPEN,                 # handler counts only own computers
    # 2.30: own collections (only with "own packages allowed") - never contain more than the tenant's computers
    ("GET", "/api/collections/schema"): OPEN,
    ("GET", "/api/collections/values"): OPEN,
    ("POST", "/api/collections"): _p_build,
    ("POST", "/api/collections/preview"): OPEN,       # handler: only own computers
    ("PUT", "/api/collections/{cid}"): _p_coll_w,
    ("DELETE", "/api/collections/{cid}"): _p_coll_w,
    ("POST", "/api/collections/{cid}/move"): _p_coll_w,
    ("POST", "/api/collections/{cid}/rename"): _p_coll_w,
    ("POST", "/api/collections/{cid}/members"): _p_coll_w,
    ("POST", "/api/collections/{cid}/duplicate"): _p_coll_r,  # handler: the copy belongs to the own tenant
    # 2.30: read reports - results only with own computers; create/change and mail sending only main admins
    ("GET", "/api/reports"): OPEN,
    ("GET", "/api/reports/{key}"): OPEN,
    ("GET", "/api/reports/{key}/csv"): OPEN,
    ("GET", "/api/customreports/{rid}"): _p_creport,
    # Software (handlers filter)
    ("GET", "/api/software"): OPEN,
    ("GET", "/api/software/detail"): OPEN,
    ("POST", "/api/software/update"): OPEN,
    ("GET", "/api/winget/status"): OPEN,
    ("GET", "/api/wingetcache/ready"): OPEN,      # 2.35: only which winget IDs are ready (button "Aktualisieren")
    ("GET", "/api/winget/search"): OPEN,
    # Packages: view and distribute all (central ones and those of other tenants); change only own, only with permission
    ("GET", "/api/packages"): OPEN,
    ("GET", "/api/packages/meta"): OPEN,
    ("GET", "/api/packages/{pid}"): _p_pkg,
    ("POST", "/api/packages"): _p_build,
    ("POST", "/api/packages/merge"): _p_build,       # 2.32: handler checks each package (visible) and the tenant
    ("PUT", "/api/packages/{pid}"): _p_pkg_edit,
    ("POST", "/api/packages/export"): OPEN,      # handler: only visible packages (need_package per package)
    ("POST", "/api/packages/import"): _p_build,  # like "Paket anlegen"; tenant enforced, rules from _pkg_values
    ("POST", "/api/packages/{pid}/duplicate"): _p_pkg,                 # handler: copy into the own tenant, only with permission
    ("POST", "/api/packages/{pid}/integrity/check"): _p_pkg,        # handler: details only for own tenant folders
    ("POST", "/api/packages/{pid}/integrity/accept"): _p_pkg_edit,  # handler: only files in own tenant folders
    ("DELETE", "/api/packages/{pid}"): _p_pkg_edit,
    ("POST", "/api/packages/preview"): OPEN,
    ("GET", "/api/repository"): _p_repo,
    ("GET", "/api/repository/msi"): _p_repo,
    ("GET", "/api/repository/search"): _p_repo,       # handler restricts the path to the own folder
    # 2.27: repository view - handler (repofiles.need_scope) allows only the own tenant folder
    ("GET", "/api/repository/info"): _p_repo,
    ("GET", "/api/repository/usage"): _p_repo,
    ("POST", "/api/repository/mkdir"): _p_repo,
    ("POST", "/api/repository/rename"): _p_repo,
    ("POST", "/api/repository/move"): _p_repo,
    ("POST", "/api/repository/delete"): _p_repo,
    ("POST", "/api/repository/upload"): _p_repo,
    ("PUT", "/api/repository/upload/{token}"): _p_repo,           # handler: only own uploads
    ("POST", "/api/repository/upload/{token}/finish"): _p_repo,
    ("DELETE", "/api/repository/upload/{token}"): _p_repo,
    ("POST", "/api/packages/{pid}/repair/suggest"): _p_pkg_edit,
    ("POST", "/api/packages/{pid}/repair/apply"): _p_pkg_edit,   # handler: only paths in the own tenant folder
    # Deployments
    ("GET", "/api/deployments"): OPEN,
    ("POST", "/api/deployments"): OPEN,                # handler checks package and computers
    ("GET", "/api/deployments/{did}"): _p_dep,
    ("GET", "/api/deployments/{did}/failed_steps"): _p_dep_w,   # step titles: only if all targets are own
    ("POST", "/api/deployments/{did}/cancel"): _p_dep_w,
    ("POST", "/api/deployments/{did}/redeploy"): _p_dep_w,
    ("POST", "/api/deployments/{did}/retry"): _p_dep_w,
    ("GET", "/api/deployments/{did}/steps"): _p_dep_w,          # titles contain commands - never for foreign targets
    ("POST", "/api/deployments/{did}/rerun"): _p_dep_w,
    ("DELETE", "/api/deployments/{did}"): _p_dep_w,
    # Schedules of the own tenant
    ("GET", "/api/schedules"): OPEN,
    ("GET", "/api/schedules/{sid}"): _p_sched,
    ("GET", "/api/schedules/{sid}/preview"): _p_sched,
    ("POST", "/api/schedules"): _p_build,
    ("PUT", "/api/schedules/{sid}"): _p_sched_w,
    ("DELETE", "/api/schedules/{sid}"): _p_sched_w,
    ("POST", "/api/schedules/{sid}/run"): _p_sched_run,     # central ones: only main admins
    ("POST", "/api/schedules/{sid}/duplicate"): _p_sched,    # handler: the copy belongs to the own tenant
    ("POST", "/api/schedules/{sid}/reset"): _p_sched_run,  # only own computers (handler), central ones: only main admins
    # Tenants: see only your own
    ("GET", "/api/tenants"): OPEN,
    # Log of the own computers
    ("GET", "/api/logs"): OPEN,
    ("GET", "/api/logs/csv"): OPEN,
    # Remote sessions on own computers
    ("GET", "/api/remote/status"): OPEN,
    ("POST", "/api/remote/connect"): OPEN,             # handler checks the computer
    ("GET", "/api/remote/sessions"): OPEN,             # handler: only own
    ("DELETE", "/api/remote/sessions/{sid}"): OPEN,    # handler: only own
}


def route_path(request):
    """Path pattern of the called route ("/api/computers/{cid}") - via the endpoint function that Starlette writes into
    the scope when matching (scope["route"] does not exist in every FastAPI version)."""
    route = request.scope.get("route")
    path = getattr(route, "path", None) if route is not None else None
    if not path:
        ep = request.scope.get("endpoint")
        method = request.method.upper()
        for r in getattr(request.app, "routes", []):
            if getattr(r, "endpoint", None) is ep and method in (getattr(r, "methods", None) or ()):
                path = r.path
                break
    # "{cid:int}" → "{cid}" (Starlette converter)
    return re.sub(r"\{(\w+):[^}]+\}", r"{\1}", path) if path else None


def enforce(request, user):
    """For OU admins: the route must be allowed; path parameters are checked."""
    if not scoped(user):
        return
    key = (request.method.upper(), route_path(request))
    if key not in ROUTES:
        raise HTTPException(403, "Für OU-Admins nicht freigegeben – bitte an einen Hauptadmin wenden.")
    chk = ROUTES[key]
    if chk is not None:
        chk(user, dict(request.path_params or {}))
