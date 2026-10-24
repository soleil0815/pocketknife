"""OU-Admins (Mandanten-Admins): eingeschränkte Verwaltung für die Computer eines Mandanten.

- Hauptadmins = Mitglieder der Zugriffsgruppe (PK_ACCESS_GROUP, z. B. POCKETKNIFE-Admins) und der Notfallzugang:
  dürfen alles, wie bisher.
- OU-Admins = Mitglieder der AD-Gruppe eines Mandanten (Einstellungen → Mandanten → „OU-Admins“), NICHT in der
  Zugriffsgruppe. Die Gruppe des Mandanten erlaubt die Anmeldung – aber nur für die Computer seiner Sammlung (z. B. eine
  OU). Ist jemand in mehreren Mandanten-Gruppen, betreut er alle diese Mandanten.

Durchsetzung auf dem Server, nie nur in der Oberfläche:
1. Freigabeliste ROUTES: Für OU-Admins ist jede Schnittstelle gesperrt, die hier nicht ausdrücklich steht (eine neue,
   vergessene Schnittstelle ist also zu, nicht offen). Der Selbsttest prüft, dass jede Route bewusst eingeordnet ist.
2. Pfad-Prüfung: Routen mit Computer im Pfad ({cid}) prüfen den Computer gegen den Bereich (sonst 404 – fremde
   Computer werden nicht einmal bestätigt).
3. Die Handler selbst filtern Listen (Computer, Software, Protokoll …) und prüfen Computer-IDs im Inhalt der Anfrage.
"""
import json
import re
import threading
import time

from fastapi import HTTPException

from . import db

_cache = {}
_clock = threading.Lock()
TTL = 30  # Sekunden – neue Computer in der OU sind spätestens dann sichtbar


def scoped(user):
    """True, wenn der Benutzer ein OU-Admin ist (eingeschränkt)."""
    return bool(user) and user.get("scope") is not None


def tenant_ids(user):
    """Mandanten des OU-Admins (Liste) – None für Hauptadmins (alle)."""
    if not scoped(user):
        return None
    return [int(t) for t in user["scope"]]


def tenants(user):
    """Zeilen der Mandanten des OU-Admins."""
    ids = tenant_ids(user) or []
    if not ids:
        return []
    return db.q(f"SELECT * FROM tenants WHERE id IN ({','.join('?' * len(ids))}) ORDER BY name COLLATE NOCASE", ids)


def may_build(user):
    """Darf eigene Pakete/Zeitpläne anlegen? Hauptadmins immer; OU-Admins, wenn bei einem ihrer Mandanten erlaubt."""
    if not scoped(user):
        return True
    return any(t.get("allow_packages") for t in tenants(user))


def may_script(user, tenant_id=None):
    """Darf eigenen Code in Pakete schreiben (Befehl, PowerShell, eigener Deinstallationsbefehl)? Hauptadmins immer;
    OU-Admins nur, wenn beim Mandanten „eigene Pakete“ UND „Skript-Schritte“ erlaubt sind (2.29.1). Skripte als Datei
    im Repository-Ordner des Mandanten (.ps1/.cmd über „Installieren“) brauchen das nicht."""
    if not scoped(user):
        return True
    return any(t.get("allow_packages") and t.get("allow_scripts") and (tenant_id is None or t["id"] == tenant_id)
               for t in tenants(user))


def build_tenants(user):
    """Mandanten, für die der OU-Admin Pakete/Zeitpläne bauen darf."""
    return [t for t in tenants(user) if t.get("allow_packages")]


def computer_ids(user):
    """Erlaubte Computer-IDs (set) – None für Hauptadmins (alle)."""
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
        if not t.get("collection_id"):
            continue  # Mandant ohne Sammlung: keine Computer (nie „alle“)
        try:
            ids.update(collections_.member_ids(int(t["collection_id"])))
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
    """404, wenn der Computer nicht im Bereich liegt (fremde Computer werden nicht bestätigt)."""
    try:
        cid = int(cid or 0)
    except (TypeError, ValueError):
        raise HTTPException(404, "Computer nicht gefunden")
    if not can_computer(user, cid):
        raise HTTPException(404, "Computer nicht gefunden")
    return cid


def check_ids(user, ids):
    """Computer-IDs aus einer Anfrage: alle müssen im Bereich liegen (sonst 403 – nie stillschweigend mehr)."""
    ids = [int(i) for i in ids or []]
    allowed = computer_ids(user)
    if allowed is None:
        return ids
    bad = [i for i in ids if i not in allowed]
    if bad:
        raise HTTPException(403, f"{len(bad)} der gewählten Computer gehören nicht zu Ihrem Bereich.")
    return ids


def limit_ids(user, ids):
    """Computer-IDs auf den Bereich beschneiden (für „alle Mitglieder einer Sammlung“)."""
    allowed = computer_ids(user)
    ids = [int(i) for i in ids or []]
    return ids if allowed is None else [i for i in ids if i in allowed]


def sql_in(user, col):
    """SQL-Bedingung „col liegt im Bereich“ + Parameter – (\"1=1\", []) für Hauptadmins.
    Als JSON-Liste übergeben: keine Begrenzung der SQL-Variablen bei vielen Computern."""
    allowed = computer_ids(user)
    if allowed is None:
        return "1=1", []
    return f"{col} IN (SELECT value FROM json_each(?))", [json.dumps(sorted(allowed))]


# --- Pakete ----------------------------------------------------------------------------------
def can_see_package(user, row):
    """Sehen und verteilen dürfen alle alle Pakete (zentrale und die jedes Mandanten) – ändern nur die eigenen
    (can_edit_package)."""
    return bool(row) or not scoped(user)


def can_edit_package(user, row):
    """Ändern/Löschen: nur Pakete eines eigenen Mandanten mit „eigene Pakete erlaubt“ – nie zentrale."""
    if not scoped(user):
        return True
    t = row.get("tenant_id") if row else None
    return bool(t) and int(t) in [x["id"] for x in build_tenants(user)]


def need_package(user, pid):
    row = db.q1("SELECT * FROM packages WHERE id=?", (int(pid or 0),))
    if not row or not can_see_package(user, row):
        raise HTTPException(404, "Paket nicht gefunden")
    return row


def own_tenant_for(user, requested):
    """Mandant für ein neues/geändertes Paket oder einen Zeitplan eines OU-Admins: muss einer seiner Mandanten mit
    „eigene Pakete erlaubt“ sein; ohne Angabe der erste davon."""
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
    """Verschachtelte Pakete: nur zentrale oder solche desselben Mandanten wie das äußere Paket – für alle, auch
    Hauptadmins. Sonst könnte ein (anderer) Mandant ändern, was in diesem Paket mitläuft – bei einem zentralen Paket auf
    allen Computern. Sehen und verteilen dürfen alle alle Pakete; das gilt nur fürs Einbauen."""
    from . import steps as stp
    own = int(tenant_id or 0)
    for _k, s in stp.iter_steps(stp.normalize({"steps": pkg_steps})):
        if s.get("type") == "nested" and s.get("package_id"):
            row = need_package(user, s["package_id"])
            t = int(row.get("tenant_id") or 0)
            if t and t != own:
                raise HTTPException(403, f"Verschachteltes Paket „{row['name']}“ gehört einem anderen Mandanten – einbauen "
                                         "lassen sich nur zentrale Pakete und Pakete desselben Mandanten.")


# --- Bereitstellungen / Zeitpläne --------------------------------------------------------
def deployment_targets_in_scope(user, did):
    """(alle Ziele im Bereich?, mindestens eines im Bereich?)"""
    allowed = computer_ids(user)
    ids = [r["computer_id"] for r in db.q("SELECT computer_id FROM deployment_targets WHERE deployment_id=?", (int(did),))]
    if allowed is None:
        return True, True
    inside = [i for i in ids if i in allowed]
    return (bool(ids) and len(inside) == len(ids)), bool(inside)


def need_deployment(user, did, write=False):
    """Lesen: mindestens ein Ziel im Bereich. Ändern (abbrechen, wiederholen, löschen): ALLE Ziele im Bereich."""
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
    return not scoped(user) or (s and s.get("tenant_id") and int(s["tenant_id"]) in tenant_ids(user))


def need_schedule(user, sid, write=False):
    from . import schedules
    s = schedules.get(int(sid or 0))
    if not s or not can_see_schedule(user, s):
        raise HTTPException(404, "Zeitplan nicht gefunden")
    if write and scoped(user) and int(s["tenant_id"]) not in [t["id"] for t in build_tenants(user)]:
        raise HTTPException(403, "Eigene Zeitpläne sind für Ihren Mandanten nicht freigegeben.")
    return s


# --- Freigabeliste der Schnittstellen für OU-Admins ------------------------------------------
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


def _p_repo(user, pp):
    """Repository: nur mit „eigene Pakete erlaubt“ und einem Repository-Ordner des Mandanten (der Handler beschränkt
    den Pfad darauf)."""
    if not [t for t in build_tenants(user) if (t.get("repo_folder") or "").strip()]:
        # 2.27: Ordner automatisch („Mandanten\\<Name>“) – sobald der Mandant eigene Pakete bauen darf
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
    # Sitzung
    ("GET", "/api/me"): OPEN,
    ("GET", "/api/news"): OPEN,             # Handler: OU-Admins nur Neuigkeiten für alle, nie Update-Hinweise
    ("POST", "/api/news/seen"): OPEN,
    # Übersicht und Computer (Handler filtern Listen)
    ("GET", "/api/dashboard"): OPEN,
    ("GET", "/api/computers"): OPEN,
    ("GET", "/api/computers/{cid}"): _p_cid,
    ("GET", "/api/computers/{cid}/cat/{cat}"): _p_cid,
    ("PUT", "/api/computers/{cid}/fields"): _p_cid,
    ("PUT", "/api/computers/{cid}"): _p_cid,              # Notizen zum Computer
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
    ("POST", "/api/computers/heartbeat"): OPEN,        # Handler prüft die IDs
    ("POST", "/api/computers/scan"): OPEN,             # Handler prüft die IDs
    ("GET", "/api/software/{sid}/uninstall"): _p_software,
    ("POST", "/api/uninstall"): OPEN,                  # Handler prüft Installationen/Computer
    # Tools (anlegen/ändern nur Hauptadmins)
    ("GET", "/api/tools"): OPEN,
    ("POST", "/api/tools/{tid}/run"): OPEN,            # Handler prüft den Computer
    ("POST", "/api/tools/builtin/{what}"): OPEN,       # Handler prüft den Computer
    ("POST", "/api/tools/{tid}/jobs"): OPEN,           # Handler prüft die IDs
    ("GET", "/api/tooljobs/{jid}"): _p_tooljob,
    # Nur lesen (für die Oberfläche)
    ("GET", "/api/scanners"): OPEN,
    ("GET", "/api/scanprofiles"): OPEN,
    ("GET", "/api/fields"): OPEN,
    ("GET", "/api/collections"): OPEN,                 # Handler zählt nur eigene Computer
    # Software (Handler filtern)
    ("GET", "/api/software"): OPEN,
    ("GET", "/api/software/detail"): OPEN,
    ("POST", "/api/software/update"): OPEN,
    ("GET", "/api/winget/status"): OPEN,
    ("GET", "/api/winget/search"): OPEN,
    # Pakete: alle sehen und verteilen (zentrale und die anderer Mandanten); ändern nur eigene, nur mit Freigabe
    ("GET", "/api/packages"): OPEN,
    ("GET", "/api/packages/meta"): OPEN,
    ("GET", "/api/packages/{pid}"): _p_pkg,
    ("POST", "/api/packages"): _p_build,
    ("PUT", "/api/packages/{pid}"): _p_pkg_edit,
    ("POST", "/api/packages/export"): OPEN,      # Handler: nur sichtbare Pakete (need_package je Paket)
    ("POST", "/api/packages/import"): _p_build,  # wie „Paket anlegen“; Mandant erzwungen, Regeln aus _pkg_values
    ("POST", "/api/packages/{pid}/duplicate"): _p_pkg,                 # Handler: Kopie in den eigenen Mandanten, nur mit Freigabe
    ("POST", "/api/packages/{pid}/integrity/check"): _p_pkg,        # Handler: Details nur zu eigenen Mandanten-Ordnern
    ("POST", "/api/packages/{pid}/integrity/accept"): _p_pkg_edit,  # Handler: nur Dateien in eigenen Mandanten-Ordnern
    ("DELETE", "/api/packages/{pid}"): _p_pkg_edit,
    ("POST", "/api/packages/preview"): OPEN,
    ("GET", "/api/repository"): _p_repo,
    ("GET", "/api/repository/msi"): _p_repo,
    ("GET", "/api/repository/search"): _p_repo,       # Handler beschränkt den Pfad auf den eigenen Ordner
    # 2.27: Repository-Ansicht – Handler (repofiles.need_scope) erlaubt nur den eigenen Mandantenordner
    ("GET", "/api/repository/info"): _p_repo,
    ("GET", "/api/repository/usage"): _p_repo,
    ("POST", "/api/repository/mkdir"): _p_repo,
    ("POST", "/api/repository/rename"): _p_repo,
    ("POST", "/api/repository/move"): _p_repo,
    ("POST", "/api/repository/delete"): _p_repo,
    ("POST", "/api/repository/upload"): _p_repo,
    ("PUT", "/api/repository/upload/{token}"): _p_repo,           # Handler: nur eigene Uploads
    ("POST", "/api/repository/upload/{token}/finish"): _p_repo,
    ("DELETE", "/api/repository/upload/{token}"): _p_repo,
    ("POST", "/api/packages/{pid}/repair/suggest"): _p_pkg_edit,
    ("POST", "/api/packages/{pid}/repair/apply"): _p_pkg_edit,   # Handler: nur Pfade im eigenen Mandantenordner
    # Bereitstellungen
    ("GET", "/api/deployments"): OPEN,
    ("POST", "/api/deployments"): OPEN,                # Handler prüft Paket und Computer
    ("GET", "/api/deployments/{did}"): _p_dep,
    ("GET", "/api/deployments/{did}/failed_steps"): _p_dep_w,   # Schritt-Titel: nur, wenn alle Ziele eigene sind
    ("POST", "/api/deployments/{did}/cancel"): _p_dep_w,
    ("POST", "/api/deployments/{did}/redeploy"): _p_dep_w,
    ("POST", "/api/deployments/{did}/retry"): _p_dep_w,
    ("GET", "/api/deployments/{did}/steps"): _p_dep_w,          # Titel enthalten Befehle – nie bei fremden Zielen
    ("POST", "/api/deployments/{did}/rerun"): _p_dep_w,
    ("DELETE", "/api/deployments/{did}"): _p_dep_w,
    # Zeitpläne des eigenen Mandanten
    ("GET", "/api/schedules"): OPEN,
    ("GET", "/api/schedules/{sid}"): _p_sched,
    ("GET", "/api/schedules/{sid}/preview"): _p_sched,
    ("POST", "/api/schedules"): _p_build,
    ("PUT", "/api/schedules/{sid}"): _p_sched_w,
    ("DELETE", "/api/schedules/{sid}"): _p_sched_w,
    ("POST", "/api/schedules/{sid}/run"): _p_sched,
    ("POST", "/api/schedules/{sid}/duplicate"): _p_sched_w,
    ("POST", "/api/schedules/{sid}/reset"): _p_sched_w,
    # Mandanten: nur die eigenen sehen
    ("GET", "/api/tenants"): OPEN,
    # Protokoll der eigenen Computer
    ("GET", "/api/logs"): OPEN,
    ("GET", "/api/logs/csv"): OPEN,
    # Fernsitzungen auf eigene Computer
    ("GET", "/api/remote/status"): OPEN,
    ("POST", "/api/remote/connect"): OPEN,             # Handler prüft den Computer
    ("GET", "/api/remote/sessions"): OPEN,             # Handler: nur eigene
    ("DELETE", "/api/remote/sessions/{sid}"): OPEN,    # Handler: nur eigene
}


def route_path(request):
    """Pfadmuster der aufgerufenen Route („/api/computers/{cid}“) – über die Endpunkt-Funktion, die Starlette beim
    Zuordnen in den Scope schreibt (scope["route"] gibt es nicht in jeder FastAPI-Version)."""
    route = request.scope.get("route")
    path = getattr(route, "path", None) if route is not None else None
    if not path:
        ep = request.scope.get("endpoint")
        method = request.method.upper()
        for r in getattr(request.app, "routes", []):
            if getattr(r, "endpoint", None) is ep and method in (getattr(r, "methods", None) or ()):
                path = r.path
                break
    # „{cid:int}“ → „{cid}“ (Starlette-Konverter)
    return re.sub(r"\{(\w+):[^}]+\}", r"{\1}", path) if path else None


def enforce(request, user):
    """Für OU-Admins: Route muss freigegeben sein; Pfad-Parameter werden geprüft."""
    if not scoped(user):
        return
    key = (request.method.upper(), route_path(request))
    if key not in ROUTES:
        raise HTTPException(403, "Für OU-Admins nicht freigegeben – bitte an einen Hauptadmin wenden.")
    chk = ROUTES[key]
    if chk is not None:
        chk(user, dict(request.path_params or {}))
