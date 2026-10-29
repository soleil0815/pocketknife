"""POCKET KNIFE – Web-API und Auslieferung der Oberfläche."""
import base64
import json
import secrets
import shutil
import sqlite3
import re
import os
import time
import urllib.parse
from contextlib import asynccontextmanager

from fastapi import Body, Depends, FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from . import (access, backup, clientfiles, loadstats, integrity, kiosk, news, pkgexport, repofiles, sso, customreports, mailer, notify, diskusage, kerberos, presence, tls, actions, ad, auth, guard, profiles, remote_desktop, scanners, tools, uninstall, collections_, config, db, deploy, inventory, pklog, remote, reports, scheduler,
               schedules, steps, util, winget)


@asynccontextmanager
async def lifespan(_app):
    kerberos.setup()
    try:
        backup.before_update()  # Stand vor dem Update sichern, bevor Migrationen laufen
    except Exception as e:  # pragma: no cover
        print(f"Sicherung vor dem Update: {e}", flush=True)
    db.init()
    backup.mark_version()
    try:
        from . import diagnostics
        diagnostics.record_start()  # Versionsgeschichte für das Diagnosepaket
    except Exception as e:  # pragma: no cover
        print(f"Versionsgeschichte: {e}", flush=True)
    pklog.setup()
    pklog.info("system", f"{config.APP_NAME} {config.VERSION} gestartet.")
    if not config.ldap_configured():
        pklog.warn("system", "LDAP/Dienstkonto nicht vollständig konfiguriert – Anmeldung nur per Notfallzugang möglich.")
    if config.BREAKGLASS_USER and config.BREAKGLASS_PASSWORD and len(config.BREAKGLASS_PASSWORD) < 16:
        # Audit 2.29: der Notfallzugang unterliegt keiner AD-Sperre – ein kurzes Kennwort ist leicht zu erraten
        pklog.warn("system", "Das Kennwort des Notfallzugangs (PK_BREAKGLASS_PASSWORD) ist kürzer als 16 Zeichen – bitte ein "
                             "langes, zufälliges Kennwort setzen und die Benachrichtigung „Notfallzugang benutzt“ einschalten.")
    tools.init()
    uninstall.init()
    winget.load()
    deploy.recover()
    if config.HTTPS:
        tls.startup_check()
    scheduler.start()
    import threading as _th
    _th.Thread(target=_repo_startup, name="pk-repo-start", daemon=True).start()
    yield


def _repo_startup():
    """Nach dem Start im Hintergrund: Mandantenordner anlegen, alte Uploads aufräumen, Kennzeichen nachtragen."""
    for fn in (repofiles.ensure_all_tenant_folders, repofiles.cleanup_uploads, repofiles.backfill):
        try:
            fn()
        except Exception as e:  # pragma: no cover
            pklog.warn("repo", f"Start: {getattr(fn, '__name__', fn)}: {e}")


app = FastAPI(title=config.APP_NAME, version=config.VERSION, lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)

CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; "
       "font-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
PREVIEW_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'self'; sandbox"


class _TooBig(Exception):
    pass


class BodyLimit:
    """Größe von Anfragen begrenzen (vor dem Einlesen – auch ohne Anmeldung, z. B. /api/login).
    Uploads haben eigene, größere Grenzen und prüfen selbst mit."""
    DEFAULT = 2 * 1024 * 1024
    LARGE = (("/api/computers/", "/files/upload", 2 * 1024 ** 3 + 1024 * 1024), ("/api/winget/upload", "", 310 * 1024 * 1024),
             ("/api/tls/", "", 20 * 1024 * 1024), ("/api/packages", "", 20 * 1024 * 1024),
             ("/api/repository/upload/", "", 9 * 1024 * 1024))  # ein Upload-Stück (repofiles.CHUNK = 8 MB)

    def __init__(self, app):
        self.app = app

    def _limit(self, path):
        for prefix, suffix, lim in self.LARGE:
            if path.startswith(prefix) and path.endswith(suffix):
                return lim
        return self.DEFAULT

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = self._limit(scope.get("path", ""))
        for k, v in scope.get("headers") or []:
            if k == b"content-length" and v.isdigit() and int(v) > limit:
                return await self._reject(send, limit)
        seen = 0
        too_big = False

        async def recv():
            nonlocal seen, too_big
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body") or b"")
                if seen > limit:
                    too_big = True
                    raise _TooBig()
            return msg
        try:
            await self.app(scope, recv, send)
        except _TooBig:
            return await self._reject(send, limit)

    @staticmethod
    async def _reject(send, limit):
        body = json.dumps({"detail": f"Anfrage zu groß (höchstens {limit // 1048576} MB)."}).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


app.add_middleware(BodyLimit)


@app.middleware("http")
async def security(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        loadstats.request(request.url.path)  # 2.31: welche Anfragen in einer Lastspitze kamen
    if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get("x-pk-request") != "1":
            return JSONResponse({"detail": "CSRF-Prüfung fehlgeschlagen"}, status_code=403)
    resp = await call_next(request)
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith(("/api/collections", "/api/computers", "/api/fields")):
        collections_.invalidate()  # Mitgliedschaften sofort neu berechnen
    if request.url.path.startswith("/api/mail/view/"):
        # Mail-Vorschau im iframe: nur Inline-Styles, keine Skripte, kein Nachladen
        resp.headers["Content-Security-Policy"] = PREVIEW_CSP
        resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    else:
        if "content-security-policy" not in resp.headers:  # Audit 2.29: strengere Vorgabe der Route (Symbole) behalten
            resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    elif request.url.path.startswith("/static/") and "Cache-Control" not in resp.headers:
        # Oberfläche (app.js, i18n.js, CSS) bei jedem Laden kurz prüfen lassen (ETag → meist nur „304 unverändert“):
        # sonst zeigt der Browser nach einem Update tagelang die alte Oberfläche aus seinem Zwischenspeicher
        resp.headers["Cache-Control"] = "no-cache"
    if config.HTTPS:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000"
    return resp


def _from_request_parsing(exc):
    """True, wenn der Fehler direkt im Endpunkt (main.py) beim Umwandeln von Eingaben entstand – nicht tiefer im Programm."""
    tb = exc.__traceback__
    last = None
    while tb:
        last = tb
        tb = tb.tb_next
    if not last:
        return False
    frame = last.tb_frame
    return frame.f_code.co_filename == __file__ and frame.f_code.co_name.startswith("api_")


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    """Unerwartete Fehler: vollständige Meldung ins Protokoll und in die Container-Ausgabe, kurze Meldung an die Oberfläche."""
    import traceback
    if isinstance(exc, (ValueError, TypeError, KeyError)) and _from_request_parsing(exc):
        # Unbrauchbare Eingabe (z. B. Text statt Zahl) – kein interner Fehler
        return JSONResponse({"detail": f"Ungültige Eingabe: {str(exc)[:200]}"}, status_code=400)
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    print(f"Interner Fehler bei {request.method} {request.url.path}:\n{tb}", flush=True)
    try:
        pklog.error("system", f"Interner Fehler bei {request.method} {request.url.path}: {type(exc).__name__}: {exc}\n"
                              + tb[-3000:])
    except Exception:
        pass
    try:
        sess = auth.session_for(request.cookies.get(auth.COOKIE))
    except Exception:
        sess = None
    if request.url.path in ("/api/login", "/api/logout") or not sess or sess.get("role") != "admin" or sess.get("scope") is not None:
        # Details nur für angemeldete Hauptadmins – nicht für Kiosk-Nutzer, OU-Admins oder ein beliebiges Cookie
        return JSONResponse({"detail": "Interner Fehler – Details im Protokoll"}, status_code=500)
    return JSONResponse({"detail": f"Interner Fehler ({type(exc).__name__}: {str(exc)[:300]}) – Details im Protokoll"},
                        status_code=500)


U = Depends(auth.current)
K = Depends(auth.current_kiosk)  # Software-Kiosk: Kiosk-Benutzer und Hauptadmins
A = Depends(auth.any_session)    # nur Sitzungsdinge (Inaktivität) – jede gültige Sitzung


def _err(e):
    raise HTTPException(400, str(e))


def _rerr(user, e):
    """Fehler aus repofiles/remote als HTTP-Antwort: detail = {message, …} (Pakete, die einen Pfad benutzen; Duplikate)."""
    if isinstance(e, repofiles.RepoError):
        status = e.info.get("status") or e.status
        info = {k: v for k, v in e.info.items() if k != "status"}
        raise HTTPException(status, {"message": str(_repo_err(user, e)), **info} if info else str(_repo_err(user, e)))
    raise HTTPException(400, str(_repo_err(user, e)))


def _dmsg(user, e):
    """Fehlermeldung beim Starten einer Bereitstellung: bei einem Bruch im Repository (2.26) bekommen OU-Admins Details
    nur zu Dateien in ihren Mandanten-Ordnern, sonst „derzeit nicht verfügbar“."""
    if isinstance(e, integrity.IntegrityError) and access.scoped(user):
        return integrity.public_message(e, _ou_prefixes(user))
    return e


def _ou_prefixes(user):
    """Repository-Ordner der Mandanten eines OU-Admins (klein, ohne Backslash am Ende) – None für Hauptadmins."""
    if not access.scoped(user):
        return None
    return [integrity.key(t.get("repo_folder") or "") for t in access.tenants(user) if (t.get("repo_folder") or "").strip()]


# --- Anmeldung -----------------------------------------------------------------
@app.post("/api/login")
def api_login(request: Request, response: Response, data: dict = Body(...)):
    ip = request.client.host if request.client else "?"
    token, info = auth.login(data.get("username"), data.get("password"), ip,
                             kiosk=bool(data.get("kiosk")) and kiosk.enabled())
    response.set_cookie(auth.COOKIE, token, httponly=True, secure=config.SECURE_COOKIE, samesite="strict",
                        max_age=config.SESSION_HOURS * 3600, path="/")
    return info


@app.post("/api/logout")
def api_logout(request: Request, response: Response):
    auth.logout(request.cookies.get(auth.COOKIE))
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@app.get("/api/me")
def api_me(user=U):
    me = {"username": user["username"], "display_name": user["display_name"], "app": config.APP_NAME,
          "version": config.VERSION, "idle_minutes": auth.idle_minutes(), "scoped": access.scoped(user),
}
    if access.scoped(user):
        # OU-Admin: die Oberfläche blendet alles aus, was nur Hauptadmins dürfen (der Server sperrt es ohnehin)
        me["tenants"] = [{"id": t["id"], "name": t["name"], "allow_packages": bool(t.get("allow_packages")),
                          "repo_folder": t.get("repo_folder") or ""} for t in access.tenants(user)]
        me["may_build"] = access.may_build(user)
        me["may_script"] = access.may_script(user)
        for t in me["tenants"]:
            t["allow_scripts"] = access.may_script(user, t["id"])
        me["computers"] = len(access.computer_ids(user))
    return me


@app.get("/api/news")
def api_news(user=U):
    """Neuigkeiten in der App (Seitenleiste „Neu“); für Hauptadmins dazu eine neuere Version, falls verfügbar."""
    return news.for_user(user)


@app.post("/api/news/seen")
def api_news_seen(data: dict = Body(default={}), user=U):
    return news.mark_seen(user, (data or {}).get("version"))


@app.get("/api/system/updates")
def api_updates(user=U):
    return news.settings()


@app.put("/api/system/updates")
def api_updates_save(data: dict = Body(...), user=U):
    try:
        out = news.save(data)
    except ValueError as e:
        _err(e)
    pklog.info("system", "Einstellungen „Neuigkeiten und Updates“ geändert.", user=user["username"])
    return out


@app.post("/api/system/updates/check")
def api_updates_check(user=U):
    """Jetzt bei GitHub nach einer neuen Version fragen (sonst einmal täglich im Hintergrund)."""
    return {**news.check(force=True, who=user["username"]), "settings": news.settings()}


@app.post("/api/session/active")
def api_session_active(user=A):
    """Die Oberfläche meldet Eingaben des Benutzers (höchstens einmal pro Minute)."""
    auth.touch(user["token_hash"])
    return {"remaining": int(auth.idle_remaining(user["token_hash"])), "idle_minutes": auth.idle_minutes()}


@app.get("/api/session/idle")
def api_session_idle(user=A):
    """Restzeit bis zur Abmeldung wegen Inaktivität – zählt selbst nicht als Aktivität."""
    return {"remaining": int(auth.idle_remaining(user["token_hash"])), "idle_minutes": auth.idle_minutes()}


# --- Dashboard -----------------------------------------------------------------
@app.get("/api/dashboard")
def api_dashboard(user=U):
    if access.scoped(user):
        return _dashboard_scoped(user)
    v = db.val
    return {
        "computers": v("SELECT COUNT(*) FROM computers", default=0),
        "online": v("SELECT COUNT(*) FROM computers WHERE online=1", default=0),
        "scanned": v("SELECT COUNT(*) FROM computers WHERE scan_status='OK'", default=0),
        "scan_failed": v("SELECT COUNT(*) FROM computers WHERE scan_status='Fehler'", default=0),
        "never_scanned": v("SELECT COUNT(*) FROM computers WHERE last_scan IS NULL", default=0),
        "outdated_installs": v("SELECT COUNT(*) FROM software WHERE outdated=1", default=0),
        "outdated_computers": v("SELECT COUNT(DISTINCT computer_id) FROM software WHERE outdated=1", default=0),
        "pending_reboot": v("SELECT COUNT(*) FROM computers WHERE json_extract(extra,'$.pending_reboot')=1", default=0),
        "deploy_running": v("SELECT COUNT(*) FROM deployments WHERE status IN ('running','queued')", default=0),
        "deploy_failed_7d": v("SELECT COUNT(*) FROM deployment_targets t JOIN deployments d ON d.id=t.deployment_id "
                              "WHERE t.status='failed' AND d.created_at >= ?", (util.ago(days=7),), 0),
        "scanning": len(inventory.scanning_ids()),
        "packages_broken": v("SELECT COUNT(*) FROM packages WHERE json_extract(integrity, '$.state')='broken'", default=0),
        "os": db.q("SELECT COALESCE(os_name, ad_os, 'unbekannt') os, COUNT(*) n FROM computers GROUP BY 1 "
                   "ORDER BY n DESC LIMIT 8"),
        "top_outdated": db.q("SELECT name, winget_id, winget_latest, COUNT(DISTINCT computer_id) n FROM software "
                             "WHERE outdated=1 GROUP BY winget_id ORDER BY n DESC LIMIT 8"),
        "deployments": db.q("""SELECT d.id, d.name, d.status, d.created_at,
                               (SELECT COUNT(*) FROM deployment_targets t WHERE t.deployment_id=d.id) total,
                               (SELECT COUNT(*) FROM deployment_targets t WHERE t.deployment_id=d.id AND t.status='success') ok
                               FROM deployments d ORDER BY d.id DESC LIMIT 6"""),
        "logs": db.q("SELECT ts, level, category, message FROM logs WHERE level IN ('WARN','ERROR') "
                     "ORDER BY id DESC LIMIT 8"),
        "winget": winget.status(),
        "guard": guard.status(),
    }


def _dashboard_scoped(user):
    """Übersicht eines OU-Admins: dieselben Kennzahlen, nur über seine Computer."""
    w, p = access.sql_in(user, "c.id")
    ws, ps = access.sql_in(user, "computer_id")
    v = lambda sql, prm=(): db.val(sql, tuple(prm), 0)  # noqa: E731
    mine = access.computer_ids(user)
    deps = [d for d in db.q("""SELECT d.id, d.name, d.status, d.created_at FROM deployments d WHERE EXISTS (
                               SELECT 1 FROM deployment_targets t WHERE t.deployment_id=d.id AND t.computer_id IN
                               (SELECT value FROM json_each(?))) ORDER BY d.id DESC LIMIT 6""", (json.dumps(sorted(mine)),))]
    for d in deps:
        d["total"] = v(f"SELECT COUNT(*) FROM deployment_targets WHERE deployment_id=? AND {ws}", [d["id"]] + ps)
        d["ok"] = v(f"SELECT COUNT(*) FROM deployment_targets WHERE deployment_id=? AND status='success' AND {ws}", [d["id"]] + ps)
    return {
        "computers": v(f"SELECT COUNT(*) FROM computers c WHERE {w}", p),
        "online": v(f"SELECT COUNT(*) FROM computers c WHERE online=1 AND {w}", p),
        "scanned": v(f"SELECT COUNT(*) FROM computers c WHERE scan_status='OK' AND {w}", p),
        "scan_failed": v(f"SELECT COUNT(*) FROM computers c WHERE scan_status='Fehler' AND {w}", p),
        "never_scanned": v(f"SELECT COUNT(*) FROM computers c WHERE last_scan IS NULL AND {w}", p),
        "outdated_installs": v(f"SELECT COUNT(*) FROM software WHERE outdated=1 AND {ws}", ps),
        "outdated_computers": v(f"SELECT COUNT(DISTINCT computer_id) FROM software WHERE outdated=1 AND {ws}", ps),
        "pending_reboot": v(f"SELECT COUNT(*) FROM computers c WHERE json_extract(extra,'$.pending_reboot')=1 AND {w}", p),
        "deploy_running": v(f"""SELECT COUNT(DISTINCT t.deployment_id) FROM deployment_targets t JOIN deployments d
                                ON d.id=t.deployment_id WHERE d.status IN ('running','queued') AND {ws.replace('computer_id', 't.computer_id')}""", ps),
        "deploy_failed_7d": v(f"""SELECT COUNT(*) FROM deployment_targets t JOIN deployments d ON d.id=t.deployment_id
                                  WHERE t.status='failed' AND d.created_at >= ? AND {ws.replace('computer_id', 't.computer_id')}""",
                              [util.ago(days=7)] + ps),
        "scanning": len([i for i in inventory.scanning_ids() if i in mine]),
        "os": db.q(f"SELECT COALESCE(os_name, ad_os, 'unbekannt') os, COUNT(*) n FROM computers c WHERE {w} GROUP BY 1 "
                   "ORDER BY n DESC LIMIT 8", p),
        "top_outdated": db.q(f"SELECT name, winget_id, winget_latest, COUNT(DISTINCT computer_id) n FROM software "
                             f"WHERE outdated=1 AND {ws} GROUP BY winget_id ORDER BY n DESC LIMIT 8", ps),
        "deployments": deps,
        "logs": db.q(f"SELECT ts, level, category, message FROM logs WHERE level IN ('WARN','ERROR') AND {ws} "
                     "ORDER BY id DESC LIMIT 8", ps),
        "winget": winget.status(),
        "guard": None,
    }


# --- Computer --------------------------------------------------------------------
@app.get("/api/computers")
def api_computers(collection: int = 0, user=U):
    where, params = ("1=1", [])
    sw, sp = access.sql_in(user, "c.id")  # OU-Admin: nur seine Computer
    if collection:
        try:
            where, params = collections_.where_for(collection)
        except ValueError as e:
            _err(e)
    rows = db.q(f"""SELECT c.id, c.name, c.dns_name, COALESCE(c.os_name, c.ad_os) os, c.os_display_version, c.os_build,
                    c.manufacturer, c.model, c.serial, c.current_user, c.ip_addresses, c.online, c.winrm, c.last_seen,
                    c.addr_problem, c.addr_detail, c.resolved_ip,
                    c.last_scan, c.scan_status, c.scan_error, c.ad_enabled, c.ad_present, c.dn, c.description,
                    c.last_boot, c.cpu, c.os_arch, c.mac_addresses, c.ad_last_logon, c.bios_version, c.created_at,
                    COALESCE(c.scan_allowed, 1) scan_allowed, json_extract(c.extra, '$.chassis_name') chassis,
                    json_extract(c.extra, '$.winget') winget, json_extract(c.extra, '$.user_states') user_states,
                    ROUND(c.ram_bytes/1073741824.0, 1) ram_gb,
                    (SELECT COUNT(*) FROM software s WHERE s.computer_id=c.id AND s.outdated=1) outdated
                    FROM computers c WHERE {where} AND {sw} ORDER BY c.name COLLATE NOCASE""", params + sp)
    scanning = set(inventory.scanning_ids())
    for r in rows:
        r["scanning"] = r["id"] in scanning
    return rows


LOGGEDON_PS = remote.LOGGEDON_PS


@app.post("/api/computers/{cid}/loggedon")
def api_computer_loggedon(cid: int, user=U):
    """Angemeldete Benutzer live abfragen (WinRM) und beim Computer speichern."""
    c = db.q1("SELECT * FROM computers WHERE id=?", (cid,))
    if not c:
        raise HTTPException(404)
    host = c["dns_name"] or c["name"]
    if not remote.is_online(host)[1]:
        return {"ok": False, "user": c["current_user"], "live": False}
    try:
        out, _e, _ = remote.ps(host, LOGGEDON_PS)
    except remote.RemoteError as e:
        return {"ok": False, "user": c["current_user"], "live": False, "error": str(e)}
    # „PKUSERS:“ ohne Namen = niemand angemeldet
    rest = out.split("PKUSERS:", 1)[1] if "PKUSERS:" in out else ""
    users = (rest.splitlines() or [""])[0].strip()
    states = remote.parse_states(out)  # 2.32.6: gesperrt/getrennt
    db.ex("UPDATE computers SET current_user=?, extra=json_set(COALESCE(extra,'{}'), '$.user_states', json(?)) WHERE id=?",
          (users or None, json.dumps(states), cid))
    first = users.split(",")[0].strip() if users else ""
    if first:
        inventory._display_name(cid, first)
    elif c["current_user"]:
        db.ex("UPDATE computers SET extra=json_remove(COALESCE(extra,'{}'), '$.user_display_name') WHERE id=?", (cid,))
    extra = db.val("SELECT json_extract(extra, '$.user_display_name') FROM computers WHERE id=?", (cid,))
    return {"ok": True, "user": users, "display_name": extra if first else None, "live": True, "states": states}


@app.post("/api/computers")
def api_computer_add(data: dict = Body(...), user=U):
    name = (data.get("name") or "").strip()
    if not name or any(ch in name for ch in " \\/'\"`;"):
        _err("Ungültiger Computername.")
    if db.q1("SELECT id FROM computers WHERE lower(name)=lower(?) OR lower(dns_name)=lower(?)", (name, name)):
        _err("Computer existiert bereits.")
    short = name.split(".")[0]
    dns = name if "." in name else (f"{name}.{config.DOMAIN}" if config.DOMAIN else name)
    cid = db.ex("INSERT INTO computers(name, dns_name, ad_present, created_at) VALUES(?,?,0,?)",
                (short.upper(), dns, util.now()))
    pklog.info("system", f"Computer {name} manuell hinzugefügt.", user=user["username"], computer_id=cid)
    inventory.queue_scan(cid, user["username"])
    return {"id": cid}


def _member_collections(cid):
    out = []
    for col in db.q("SELECT id, name, type, builtin FROM collections WHERE type != 'folder' ORDER BY builtin DESC, sort, name"):
        try:
            w, p = collections_.where_for(col["id"])
            if db.val(f"SELECT 1 FROM computers c WHERE c.id = ? AND {w}", [cid] + p):
                out.append({"id": col["id"], "name": col["name"], "type": col["type"], "builtin": col["builtin"]})
        except Exception:
            continue
    return out


def _inv(cid, cat):
    raw = db.val("SELECT json FROM inventory_data WHERE computer_id=? AND category=?", (cid, cat))
    return json.loads(raw) if raw else []


@app.get("/api/computers/{cid}")
def api_computer(cid: int, user=U):
    c = db.q1("SELECT * FROM computers WHERE id=?", (cid,))
    if not c:
        raise HTTPException(404, "Computer nicht gefunden")
    c["extra"] = json.loads(c["extra"]) if c.get("extra") else {}
    c["scanning"] = inventory.is_scanning(cid)
    c["scan_user"] = config.service_login() if not access.scoped(user) else "Dienstkonto"  # Audit 2.29: Name nur für Hauptadmins
    sub = lambda sql: db.q(sql, (cid,))  # noqa: E731
    c["disks"] = sub("SELECT drive, label, fs, size, free FROM disks WHERE computer_id=? ORDER BY drive")
    c["network"] = sub("SELECT description, mac, ip, gateway, dns, dhcp FROM network WHERE computer_id=?")
    c["cpus"] = _inv(cid, "cpus")
    c["last_scan_info"] = db.q1("SELECT * FROM scan_history WHERE computer_id=? ORDER BY id DESC LIMIT 1", (cid,))
    # Anzahl je Kategorie für die Seitenleiste
    counts = {r["category"]: r["n"] for r in db.q(
        "SELECT category, json_array_length(json) n FROM inventory_data WHERE computer_id=?", (cid,))}
    v = lambda sql: db.val(sql, (cid,), 0)  # noqa: E731
    counts.update({
        "software": v("SELECT COUNT(*) FROM software WHERE computer_id=?"),
        "software_outdated": v("SELECT COUNT(*) FROM software WHERE computer_id=? AND outdated=1"),
        "hotfixes": v("SELECT COUNT(*) FROM hotfixes WHERE computer_id=?"),
        "services": v("SELECT COUNT(*) FROM services WHERE computer_id=?"),
        "admins": v("SELECT COUNT(*) FROM local_admins WHERE computer_id=?"),
        "changes": v("SELECT COUNT(*) FROM software_changes WHERE computer_id=?"),
        "deployments": v("SELECT COUNT(*) FROM deployment_targets WHERE computer_id=?"),
        "scans": v("SELECT COUNT(*) FROM scan_history WHERE computer_id=?"),
        "disks": v("SELECT COUNT(*) FROM disks WHERE computer_id=?"),
        "device_problems": v("SELECT COUNT(*) FROM inventory_data d, json_each(d.json) j WHERE d.computer_id=? "
                             "AND d.category='devices' AND CAST(json_extract(j.value,'$.problem') AS INTEGER) > 0"),
    })
    c["counts"] = counts
    c["scan_profile"] = inventory._scanning.get(cid)
    c["custom_scanners"] = [{"id": x["id"], "name": x["name"], "type": x["type"]}
                            for x in db.q("SELECT id, name, type FROM custom_scanners WHERE enabled=1 ORDER BY name")]
    return c


DEVICE_PROBLEMS = {1: "Nicht richtig konfiguriert", 3: "Treiber beschädigt", 10: "Gerät kann nicht starten",
                   12: "Keine freien Ressourcen", 18: "Treiber neu installieren", 22: "Deaktiviert",
                   24: "Gerät nicht vorhanden", 28: "Kein Treiber installiert", 31: "Funktioniert nicht richtig",
                   43: "Gerät hat Fehler gemeldet", 45: "Nicht angeschlossen", 52: "Treibersignatur ungültig"}


HIDDEN_DEVICE_CLASSES = {"VolumeSnapshot", "LegacyDriver", "SoftwareComponent"}


@app.get("/api/computers/{cid}/cat/{cat}")
def api_computer_category(cid: int, cat: str, user=U):
    sub = lambda sql: db.q(sql, (cid,))  # noqa: E731
    if cat == "software":
        return sub("""SELECT id, name, version, publisher, install_date, modified, arch, scope, product_code, uninstall_string,
                      quiet_uninstall, modify_path, install_location, size_kb, hive, reg_path, no_remove,
                      winget_id, winget_latest, outdated FROM software WHERE computer_id=? ORDER BY name COLLATE NOCASE""")
    if cat == "hotfixes":
        return sub("SELECT hotfix_id, description, installed_on, installed_by FROM hotfixes WHERE computer_id=? "
                   "ORDER BY installed_on DESC")
    if cat == "services":
        return sub("SELECT name, display_name, state, start_mode, account, description, path FROM services "
                   "WHERE computer_id=? ORDER BY display_name COLLATE NOCASE")
    if cat == "admins":
        return sub("SELECT name, type FROM local_admins WHERE computer_id=? ORDER BY name")
    if cat == "changes":
        return sub("SELECT ts, change, name, old_version, new_version FROM software_changes WHERE computer_id=? "
                   "ORDER BY id DESC LIMIT 1000")
    if cat == "deployments":
        return sub("""SELECT t.id, t.deployment_id, d.name, t.status, t.exit_code, t.message, t.started_at,
                      t.finished_at FROM deployment_targets t JOIN deployments d ON d.id=t.deployment_id
                      WHERE t.computer_id=? ORDER BY t.id DESC LIMIT 200""")
    if cat == "logs":
        return sub("SELECT ts, level, category, message, username FROM logs WHERE computer_id=? ORDER BY id DESC LIMIT 500")
    if cat == "scans":
        return {"history": sub("SELECT ts, status, duration_ms, user, error, warnings, profile_name FROM scan_history "
                               "WHERE computer_id=? ORDER BY id DESC"),
                "profiles": _computer_profiles(cid)}
    if cat == "collections":
        return _member_collections(cid)
    if cat == "storage":
        return {"scans": diskusage.listing(cid)}
    if cat == "disks":
        return {"volumes": _inv(cid, "volumes") or sub("SELECT drive, label, fs, size, free FROM disks WHERE computer_id=?"),
                "physical": _inv(cid, "physical_disks")}
    if cat == "custom_fields":
        return db.q("""SELECT f.id, f.name, f.type, v.value FROM custom_fields f
                       LEFT JOIN custom_field_values v ON v.field_id=f.id AND v.computer_id=?
                       ORDER BY f.sort, f.name""", (cid,))
    if cat == "devices":
        rows = [r for r in _inv(cid, "devices") if r.get("class") not in HIDDEN_DEVICE_CLASSES]
        for r in rows:
            try:
                code = int(r.get("problem") or 0)
            except (TypeError, ValueError):
                code = 0
            r["problem_text"] = DEVICE_PROBLEMS.get(code, f"Code {code}") if code else ""
        return rows
    if cat in ("registry", "files", "wmi", "powershell"):
        out = []
        prof = db.q1("SELECT id, name FROM scan_profiles WHERE builtin_key=?", (cat,))
        for sc in db.q("SELECT id, name, target, extra FROM custom_scanners WHERE type=? AND enabled=1 ORDER BY name", (cat,)):
            upd = db.val("SELECT updated FROM inventory_data WHERE computer_id=? AND category=?", (cid, f"custom:{sc['id']}"))
            out.append({"id": sc["id"], "name": sc["name"], "target": sc["target"], "rows": _inv(cid, f"custom:{sc['id']}"),
                        "extra": json.loads(sc["extra"] or "{}"),
                        "updated": upd, "profile_id": prof["id"] if prof else None, "profile": prof["name"] if prof else None})
        return out
    if cat in inventory.CATEGORIES or cat == "ad_groups":
        return _inv(cid, cat)
    _err("Unbekannte Kategorie")


@app.put("/api/computers/{cid}/fields")
def api_computer_fields(cid: int, data: dict = Body(...), user=U):
    with db.tx():
        for fid, val in (data.get("values") or {}).items():
            val = (str(val) if val is not None else "").strip()[:2000]
            if val:
                db.ex("INSERT INTO custom_field_values(computer_id, field_id, value) VALUES(?,?,?) "
                      "ON CONFLICT(computer_id, field_id) DO UPDATE SET value=excluded.value", (cid, int(fid), val))
            else:
                db.ex("DELETE FROM custom_field_values WHERE computer_id=? AND field_id=?", (cid, int(fid)))
    return {"ok": True}


# --- Aktionen auf dem Client ------------------------------------------------------------
def _act(fn, *a):
    try:
        return fn(*a)
    except (ValueError, remote.RemoteError, ad.ADError) as e:
        _err(e)


@app.get("/api/software/{sid}/uninstall")
def api_uninstall_info(sid: int, user=U):
    sw = db.q1("SELECT s.*, c.name computer FROM software s JOIN computers c ON c.id=s.computer_id WHERE s.id=?", (sid,))
    if not sw:
        raise HTTPException(404)
    return {**uninstall.command_for(sw), "name": sw["name"], "version": sw["version"], "computer": sw["computer"],
            "uninstall_string": sw["uninstall_string"], "quiet_uninstall": sw["quiet_uninstall"],
            "scope": sw["scope"], "no_remove": sw.get("no_remove"), "hive": sw.get("hive"), "reg_path": sw.get("reg_path"),
            "pattern": re.sub(r"\s*\d+([.\-]\d+)+.*$", "", sw["name"] or "").strip() + "*"}


@app.get("/api/uninstall/overrides")
def api_uninstall_overrides(user=U):
    return db.q("SELECT * FROM uninstall_overrides ORDER BY pattern COLLATE NOCASE")


@app.delete("/api/uninstall/overrides/{oid}")
def api_uninstall_override_delete(oid: int, user=U):
    db.ex("DELETE FROM uninstall_overrides WHERE id=?", (oid,))
    return {"ok": True}


@app.post("/api/uninstall")
def api_uninstall(data: dict = Body(...), user=U):
    items = data.get("items") or []
    if access.scoped(user):
        for it in items:
            if not isinstance(it, dict) or not it.get("software_id"):
                raise HTTPException(403, "Nur Installationen auf Ihren Computern.")
            if it.get("remember"):
                # „Für alle merken“ gilt auf ALLEN Computern – nur Hauptadmins
                raise HTTPException(403, "Einen Deinstallationsbefehl für alle Computer merken dürfen nur Hauptadmins.")
            if (it.get("command") or "").strip() and not access.may_script(user):
                # Audit 2.29: ein GEÄNDERTER Befehl ist eigener Code – wie Pakete nur mit „eigene Pakete erlaubt“;
                # der erkannte Befehl (vorausgefüllt) bleibt erlaubt
                sw = db.q1("SELECT * FROM software WHERE id=?", (int(it["software_id"]),)) or {}
                if it["command"].strip() != (uninstall.command_for(sw).get("command") or "").strip():
                    raise HTTPException(403, "Eigene Deinstallationsbefehle sind nur erlaubt, wenn für Ihren Mandanten „eigene Pakete“ "
                                             "und „Skript-Schritte“ freigegeben sind.")
            if it.get("run_as") == "deploy" or (it.get("command") and it.get("run_as") not in ("system", "user")):
                it["run_as"] = "system"  # OU-Admin: Deinstallation nie als Dienstkonto (auch nicht mit UninstallString)
            cid = db.val("SELECT computer_id FROM software WHERE id=?", (int(it["software_id"]),))
            if cid is None or not access.can_computer(user, cid):
                raise HTTPException(403, "Nur Installationen auf Ihren Computern.")
    if data.get("name") and data.get("computer_ids"):
        ids = access.check_ids(user, data["computer_ids"])
        ph = ",".join("?" * len(ids))
        items += [{"software_id": r["id"]} for r in db.q(
            f"SELECT id FROM software WHERE name=? AND computer_id IN ({ph})", [data["name"]] + ids)]
    deps = _act(uninstall.run, items, user["username"])
    pklog.info("deploy", f"Deinstallation gestartet ({len(items)} Installation(en)).", user=user["username"])
    return {"deployments": deps}


@app.post("/api/computers/{cid}/service")
def api_service(cid: int, data: dict = Body(...), user=U):
    res = _act(actions.service_action, cid, data.get("name"), data.get("action"), data.get("start_mode"))
    pklog.info("system", f"Dienst '{data.get('name')}': {data.get('action')} {data.get('start_mode') or ''} → {res.get('state')}",
               user=user["username"], computer_id=cid)
    return res


@app.post("/api/computers/{cid}/process/kill")
def api_kill(cid: int, data: dict = Body(...), user=U):
    _act(actions.kill_process, cid, data.get("pid"), data.get("name"))
    pklog.info("system", f"Prozess {data.get('name')} (PID {data.get('pid')}) beendet.", user=user["username"], computer_id=cid)
    return {"ok": True}


# --- Dateien auf dem Client (über die administrativen Freigaben C$, D$ …) ------------------
@app.get("/api/computers/{cid}/files")
def api_files_list(cid: int, path: str = "", user=U):
    try:
        if not path:
            dv = clientfiles.drives(cid)
            return {"drives": dv, "path": "", "items": []}
        return clientfiles.listing(cid, path)
    except clientfiles.FileError as e:
        _err(e)


@app.get("/api/computers/{cid}/files/search")
def api_files_search(cid: int, path: str = "", q: str = "", user=U):
    try:
        res = clientfiles.search(cid, path, q, user_key=user["username"].lower())
    except clientfiles.FileError as e:
        _err(e)
    pklog.info("file", f"Dateisuche „{q.strip()[:100]}“ in {res['path']}: {len(res['items'])} Treffer"
               + (" (über die Freigabe)" if res.get("via") == "smb" else ""), user=user["username"], computer_id=cid)
    return res


@app.post("/api/computers/{cid}/files/mkdir")
def api_files_mkdir(cid: int, data: dict = Body(...), user=U):
    try:
        clientfiles.mkdir(cid, data.get("path"), data.get("name"))
    except clientfiles.FileError as e:
        _err(e)
    pklog.info("file", f"Ordner angelegt: {data.get('path')}\\{data.get('name')}", user=user["username"], computer_id=cid)
    return {"ok": True}


@app.post("/api/computers/{cid}/files/rename")
def api_files_rename(cid: int, data: dict = Body(...), user=U):
    try:
        clientfiles.rename(cid, data.get("path"), data.get("name"), data.get("new"))
    except clientfiles.FileError as e:
        _err(e)
    pklog.info("file", f"Umbenannt: {data.get('name')} → {data.get('new')} in {data.get('path')}", user=user["username"], computer_id=cid)
    return {"ok": True}


@app.post("/api/computers/{cid}/files/delete")
def api_files_delete(cid: int, data: dict = Body(...), user=U):
    try:
        ok, errs = clientfiles.delete(cid, data.get("items"))
    except clientfiles.FileError as e:
        _err(e)
    pklog.warn("file", f"{ok} Eintrag/Einträge gelöscht auf Computer {cid}" + (f"; {len(errs)} Fehler" if errs else ""),
               user=user["username"], computer_id=cid)
    return {"ok": ok, "errors": errs}


@app.post("/api/computers/{cid}/files/transfer")
def api_files_transfer(cid: int, data: dict = Body(...), user=U):
    action = data.get("action") if data.get("action") in ("copy", "move") else None
    if not action:
        _err("Unbekannte Aktion.")
    try:
        ok, errs = clientfiles.transfer(cid, action, data.get("src"), data.get("items"), data.get("dst"))
    except clientfiles.FileError as e:
        _err(e)
    pklog.info("file", f"{ok} Eintrag/Einträge {'kopiert' if action == 'copy' else 'verschoben'} nach {data.get('dst')} "
               f"auf Computer {cid}", user=user["username"], computer_id=cid)
    return {"ok": ok, "errors": errs}


@app.get("/api/computers/{cid}/files/download")
def api_files_download(cid: int, path: str, name: str, check: int = 0, user=U):
    try:
        gen, size = clientfiles.open_read(cid, path, name)
    except clientfiles.FileError as e:
        _err(e)
    if check:
        # Vorabprüfung der Oberfläche: Fehler als Meldung zeigen statt auf eine Fehlerseite zu wechseln
        gen.close()
        return {"ok": True, "size": size}
    pklog.info("file", f"Datei heruntergeladen: {path}\\{name}", user=user["username"], computer_id=cid)
    import urllib.parse
    fn = urllib.parse.quote(name)
    return StreamingResponse(gen, media_type="application/octet-stream",
                             headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fn}", "Content-Length": str(size)})


@app.post("/api/computers/{cid}/files/upload")
async def api_files_upload(cid: int, request: Request, path: str, name: str, user=U):
    import tempfile
    from starlette.concurrency import run_in_threadpool
    limit = 2 * 1024 ** 3
    fd, tmp = tempfile.mkstemp(prefix="pk-up-")
    size = 0
    try:
        with os.fdopen(fd, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    _err("Datei zu groß (höchstens 2 GB).")
                await run_in_threadpool(f.write, chunk)
        with open(tmp, "rb") as f:
            written = await run_in_threadpool(clientfiles.write_file, cid, path, name, f)
    except clientfiles.FileError as e:
        _err(e)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    pklog.info("file", f"Datei hochgeladen: {path}\\{name} ({written} Bytes)", user=user["username"], computer_id=cid)
    return {"ok": True, "size": written}


@app.get("/api/computers/{cid}/eventlog")
def api_eventlog(cid: int, log: str = "System", level: int = 0, count: int = 100, q: str = "", user=U):
    if not db.q1("SELECT id FROM computers WHERE id=?", (cid,)):
        raise HTTPException(404)
    res = _act(actions.event_log, cid, log, level, count, q)
    res["logs"] = [{"key": k, "label": v} for k, v in actions.EVENT_LOGS.items()]
    return res


@app.post("/api/computers/{cid}/live/{what}")
def api_live(cid: int, what: str, user=U):
    fn = {"processes": actions.live_processes, "services": actions.live_services}.get(what)
    if not fn:
        _err("Unbekannt")
    return {"count": _act(fn, cid)}


@app.post("/api/computers/{cid}/secrets/{what}")
def api_secrets(cid: int, what: str, user=U):
    c = db.q1("SELECT id, name, dn FROM computers WHERE id=?", (cid,))
    if not c:
        raise HTTPException(404)
    if what == "laps":
        res = _act(ad.laps, c["dn"], c["name"])
        label = "LAPS-Kennwort"
    elif what == "bitlocker":
        res = _act(ad.bitlocker_keys, c["dn"], c["name"])
        label = "BitLocker-Schlüssel (AD)"
    elif what == "bitlocker_client":
        res = {"keys": _act(actions.bitlocker_from_client, cid)}
        label = "BitLocker-Schlüssel (vom Client)"
    else:
        _err("Unbekannt")
    pklog.warn("secret", f"{label} von {c['name']} angezeigt.", user=user["username"], computer_id=cid)
    return res


# --- Tools ---------------------------------------------------
TOOL_FIELDS = ["name", "folder", "type", "script", "run_as", "confirm", "description", "hotkey", "repo_path", "scan_after",
               "scan_profile_id", "timeout_s", "success_codes"]


@app.get("/api/tools")
def api_tools(user=U):
    return {"tools": db.q("SELECT * FROM tools ORDER BY folder COLLATE NOCASE, sort, name COLLATE NOCASE"),
            "types": tools.TYPES, "run_as": tools.RUN_AS, "builtin_hotkeys": tools.builtin_hotkeys(),
            "scan_after": tools.SCAN_AFTER, "default_timeout": tools.DEFAULT_TIMEOUT,
            "profiles": [{"id": p["id"], "name": p["name"], "is_default": bool(p.get("is_default"))} for p in profiles.all_profiles()]}


@app.put("/api/tools-hotkeys")
def api_tools_hotkeys(data: dict = Body(default={}), user=U):
    """2.32.4: Tastenkürzel der eingebauten Tools (für alle gleich)."""
    try:
        out = tools.save_builtin_hotkeys((data or {}).get("builtin") or {})
    except ValueError as e:
        _err(e)
    pklog.info("tool", "Tastenkürzel der eingebauten Tools geändert.", user=user["username"])
    return {"builtin_hotkeys": out}


def _tool_values(data, tid=None):
    d = {k: data.get(k) for k in TOOL_FIELDS}
    d["name"] = (d["name"] or "").strip()
    d["folder"] = (d["folder"] or "").strip().upper()[:60]
    d["run_as"] = d.get("run_as") or "service"
    d["confirm"] = 1 if d.get("confirm") else 0
    try:
        tools.validate(d)
        d["hotkey"] = tools.norm_hotkey(d.get("hotkey")) or None
        tools.check_hotkey(d["hotkey"], f"tool:{tid}" if tid else None)
    except ValueError as e:
        _err(e)
    return d


@app.post("/api/tools")
def api_tool_create(data: dict = Body(...), user=U):
    d = _tool_values(data)
    tid = db.ex(f"INSERT INTO tools({', '.join(TOOL_FIELDS)}, created_at) VALUES({', '.join('?' * len(TOOL_FIELDS))}, ?)",
                tuple(d[k] for k in TOOL_FIELDS) + (util.now(),))
    pklog.info("tool", f"Tool '{d['name']}' angelegt.", user=user["username"])
    return {"id": tid}


@app.put("/api/tools/{tid}")
def api_tool_update(tid: int, data: dict = Body(...), user=U):
    d = _tool_values(data, tid)
    db.ex(f"UPDATE tools SET {', '.join(k + '=?' for k in TOOL_FIELDS)} WHERE id=?", tuple(d[k] for k in TOOL_FIELDS) + (tid,))
    pklog.info("tool", f"Tool '{d['name']}' geändert.", user=user["username"])
    return {"ok": True}


@app.delete("/api/tools/{tid}")
def api_tool_delete(tid: int, user=U):
    db.ex("DELETE FROM tools WHERE id=?", (tid,))
    return {"ok": True}


@app.post("/api/tools/{tid}/run")
def api_tool_run(tid: int, data: dict = Body(...), user=U):
    t = db.q1("SELECT * FROM tools WHERE id=?", (tid,))
    if not t:
        raise HTTPException(404)
    cid = access.need_computer(user, data.get("computer_id"))
    name = db.val("SELECT name FROM computers WHERE id=?", (cid,), "?")
    if t["type"] == "url":
        c = db.q1("SELECT * FROM computers WHERE id=?", (cid,))
        if not c:
            raise HTTPException(404, "Computer nicht gefunden")
        try:
            return {"url": tools.substitute(t["script"], c, mode="url")}
        except ValueError as e:
            _err(e)
    res = _act(tools.run, t, cid)
    pklog.info("tool", f"Tool '{t['name']}' auf {name} ausgeführt (Exitcode {res['exit']}, {res['seconds']} s).",
               user=user["username"], computer_id=cid)
    tools.scan_after(t, cid, user["username"], res.get("ok", not res["exit"]), res)  # 2.32.7: wie im Auftrag
    return res


# --- Speicherplatz-Analyse ------------------------------------------------------------------
@app.get("/api/computers/{cid}/diskusage")
def api_du_list(cid: int, user=U):
    drives = db.q("SELECT drive, size, free FROM disks WHERE computer_id=? ORDER BY drive", (cid,))
    return {"scans": diskusage.listing(cid), "drives": drives}


@app.post("/api/computers/{cid}/diskusage")
def api_du_start(cid: int, data: dict = Body(default={}), user=U):
    try:
        jid = diskusage.start(cid, data.get("path") or "C:\\", data.get("depth") or 4, user["username"])
    except ValueError as e:
        _err(e)
    return {"job": jid}


@app.get("/api/diskusage/jobs/{jid}")
def api_du_job(jid: str, user=U):
    j = diskusage.job(jid)
    if not j:
        raise HTTPException(404, "Auftrag nicht gefunden (Server neu gestartet?)")
    return j


@app.get("/api/diskusage/{sid}")
def api_du_get(sid: int, user=U):
    r = diskusage.get(sid)
    if not r:
        raise HTTPException(404, "Analyse nicht gefunden")
    return r


@app.delete("/api/diskusage/{sid}")
def api_du_delete(sid: int, user=U):
    db.ex("DELETE FROM disk_usage WHERE id=?", (sid,))
    return {"ok": True}


@app.post("/api/tools/builtin/{what}")
def api_tool_builtin(what: str, data: dict = Body(...), user=U):
    cid = access.need_computer(user, data.get("computer_id"))
    c = db.q1("SELECT * FROM computers WHERE id=?", (cid,))
    if not c:
        raise HTTPException(404)
    if what == "ping":
        return tools.ping(c)
    if what == "wake":
        res = tools.wake(c)
        via = ", ".join(res.get("relay") or []) or "nur Container"
        pklog.info("tool", f"Wake-on-LAN an {c['name']} gesendet (über {via}).", user=user["username"], computer_id=cid)
        if res.get("exit") == 0:
            res["watch"] = tools.watch_wake(c, res.get("relay") or [], user["username"])  # 2.31: Aufwachen abwarten
        return res
    _err("Unbekanntes Tool")


@app.get("/api/computers/{cid}/wake")
def api_wake_state(cid: int, user=U):
    """2.31: Ist der Computer nach Wake-on-LAN aufgewacht? (wartet / aufgewacht nach … s / nicht aufgewacht)"""
    return tools.wake_state(cid) or {"state": "none"}


@app.post("/api/tools/{tid}/jobs")
def api_tool_job(tid: int, data: dict = Body(...), user=U):
    t = db.q1("SELECT * FROM tools WHERE id=?", (tid,))
    if not t:
        raise HTTPException(404)
    if t["type"] == "url":
        _err("URL-Tools werden im Browser geöffnet.")
    ids = access.check_ids(user, data.get("ids"))[:500]
    if not ids:
        _err("Keine Computer ausgewählt.")
    return {"job": tools.start_job(t, ids, user["username"])}


@app.post("/api/tools-command")
def api_tool_command(data: dict = Body(...), user=U):
    """2.34: Befehl (PowerShell/CMD) direkt auf den gewählten Computern ausführen – ohne Tool anzulegen. Wie Tools
    anlegen nur für Hauptadmins (OU-Admins: Route nicht freigegeben)."""
    if access.scoped(user):
        raise HTTPException(403, "Nur für Hauptadmins.")
    try:
        t = tools.adhoc_tool(data or {})
    except ValueError as e:
        _err(e)
    ids = access.check_ids(user, data.get("ids"))[:500]
    if not ids:
        _err("Keine Computer ausgewählt.")
    as_text = " als SYSTEM" if t["run_as"] == "system" else ""
    pklog.info("tool", f"Befehl ({tools.TYPES[t['type']]}{as_text}) auf {len(ids)} Computer(n) gestartet:\n{t['script'][:2000]}",
               user=user["username"])
    return {"job": tools.start_job(t, ids, user["username"]), "name": t["name"]}


@app.get("/api/tooljobs/{jid}")
def api_tool_job_status(jid: str, user=U):
    j = tools.job(jid)
    if not j:
        raise HTTPException(404, "Job nicht (mehr) vorhanden")
    return j


@app.post("/api/computers/heartbeat")
def api_heartbeat_now(data: dict = Body(...), user=U):
    """Sofort prüfen, ob die ausgewählten Computer erreichbar sind."""
    from concurrent.futures import ThreadPoolExecutor
    ids = access.check_ids(user, data.get("ids"))[:1000]
    comps = [db.q1(f"SELECT {presence.COLUMNS} FROM computers WHERE id=?", (i,)) for i in ids]
    comps = [c for c in comps if c]
    with ThreadPoolExecutor(max_workers=32) as ex:
        # Von Hand: Adresse frisch auflösen und Identität sofort neu prüfen (nicht auf den Zwischenspeicher warten)
        res = list(ex.map(lambda c: (c, presence.probe(c, force=True)), comps))
    now = util.now()
    came = [c["id"] for c, (online, winrm) in res if presence.update(c["id"], online, winrm, None, now)]
    reachable = [c["id"] for c, (online, winrm) in res if winrm]
    import threading
    # Nacheinander in einem Hintergrund-Thread: erst „gerade erreichbar geworden“, dann „noch nicht versorgt“
    threading.Thread(target=lambda: (presence.notify(came), presence.after_heartbeat(reachable)),
                     name="presence", daemon=True).start()
    return {"online": sum(1 for _c, (o, _w) in res if o), "offline": sum(1 for _c, (o, _w) in res if not o)}


@app.post("/api/computers/scan_allowed")
def api_scan_allowed(data: dict = Body(...), user=U):
    ids = [int(i) for i in data.get("ids") or []]
    allowed = 1 if data.get("allowed") else 0
    with db.tx():
        for i in ids:
            db.ex("UPDATE computers SET scan_allowed=? WHERE id=?", (allowed, i))
    pklog.info("scan", f"Automatisches Scannen für {len(ids)} Computer {'erlaubt' if allowed else 'gesperrt'}.",
               user=user["username"])
    return {"ok": True}


# --- Eigene Scanner und benutzerdefinierte Felder ------------------------------------
@app.get("/api/scanners")
def api_scanners(user=U):
    return scanners.list_all()


def _scanner_values(data):
    d = {"name": (data.get("name") or "").strip(), "type": data.get("type"), "target": (data.get("target") or "").strip(),
         "extra": json.dumps({"recurse": bool(data.get("recurse")), "namespace": (data.get("namespace") or "").strip(),
                              "bytes_mode": data.get("bytes_mode") if data.get("bytes_mode") in ("auto", "list", "off") else "auto",
                              "bytes_cols": ", ".join(x.strip() for x in str(data.get("bytes_cols") or "").split(",") if x.strip())[:500]}),
         "enabled": 1 if data.get("enabled", True) else 0}
    try:
        scanners.validate(d)
    except ValueError as e:
        _err(e)
    return d


@app.post("/api/scanners")
def api_scanner_create(data: dict = Body(...), user=U):
    d = _scanner_values(data)
    sid = db.ex("INSERT INTO custom_scanners(name, type, target, extra, enabled, created_at) VALUES(?,?,?,?,?,?)",
                (d["name"], d["type"], d["target"], d["extra"], d["enabled"], util.now()))
    profiles.add_scanner(sid, [int(i) for i in data.get("profiles") or []])
    pklog.info("system", f"Eigener Scanner '{d['name']}' angelegt.", user=user["username"])
    return {"id": sid}


@app.put("/api/scanners/{sid}")
def api_scanner_update(sid: int, data: dict = Body(...), user=U):
    d = _scanner_values(data)
    collections_.invalidate_scanner(sid)  # Größenspalten (bytes_mode) neu bestimmen
    db.ex("UPDATE custom_scanners SET name=?, type=?, target=?, extra=?, enabled=? WHERE id=?",
          (d["name"], d["type"], d["target"], d["extra"], d["enabled"], sid))
    if "profiles" in data:
        profiles.add_scanner(sid, [int(i) for i in data.get("profiles") or []])
    return {"ok": True}


@app.post("/api/scanners/test")
def api_scanner_test(data: dict = Body(...), user=U):
    """Eigenen Scanner sofort auf einem Computer ausführen und das Ergebnis zeigen (bei gespeicherten Scannern
    wird es auch beim Computer eingetragen)."""
    d = _scanner_values(data)
    c = db.q1("SELECT * FROM computers WHERE id=?", (int(data.get("computer_id") or 0),))
    if not c:
        _err("Bitte einen Computer wählen.")
    sid = int(data.get("id") or 0)
    sc = {**d, "id": sid or 0}
    script = "\n".join([inventory.PRELUDE, scanners.script_for([sc]), inventory.SCAN_END])
    t0 = time.time()
    try:
        out, err, _ = remote.ps(c["dns_name"] or c["name"], script)
    except remote.RemoteError as e:
        _err(e)
    idx = out.find("PKJSON:")
    if idx < 0:
        _err("Keine Daten erhalten. " + (err or "")[:500])
    res = json.loads(util.first_line(out[idx + 7:], "{}"))
    rows = (res.get("custom") or {}).get(str(sc["id"])) or []
    rows = rows if isinstance(rows, list) else [rows]
    if sid and db.val("SELECT 1 FROM custom_scanners WHERE id=?", (sid,)):
        inventory._inv(c["id"], f"custom:{sid}", rows, util.now())
    pklog.info("scan", f"Scanner '{d['name']}' auf {c['name']} getestet ({len(rows)} Zeilen).", user=user["username"],
               computer_id=c["id"])
    return {"rows": rows, "ms": int((time.time() - t0) * 1000), "computer": c["name"], "saved": bool(sid)}


@app.delete("/api/scanners/{sid}")
def api_scanner_delete(sid: int, user=U):
    db.ex("DELETE FROM custom_scanners WHERE id=?", (sid,))
    db.ex("DELETE FROM inventory_data WHERE category=?", (f"custom:{sid}",))
    profiles.remove_scanner(sid)
    return {"ok": True}


# --- Scanprofile ------------------------------------------------------------------------
def _computer_profiles(cid):
    out = []
    ps = {r["profile_id"]: r for r in db.q("SELECT * FROM profile_scans WHERE computer_id=?", (cid,))}
    for p in profiles.all_profiles():
        r = ps.get(p["id"]) or {}
        member = cid in profiles.members(p) if profiles.has_targets(p) else False
        if not member and not r:
            continue
        out.append({"id": p["id"], "name": p["name"], "is_default": p["is_default"], "member": member,
                    "last_scan": r.get("last_scan"), "last_attempt": r.get("last_attempt"),
                    "status": r.get("status"), "pending": r.get("pending")})
    return out


def _visible_collections(user, rows=None, counts=None):
    """2.29.9: Welche Sammlungen ein OU-Admin sieht – nur die seines Bereichs, nicht die ganze Domäne:
    - die Sammlungen seiner Mandanten samt Unterordnern,
    - jede andere Sammlung nur, wenn sie mindestens einen seiner Computer enthält (z. B. eine Ausnahme-Gruppe),
    - die Standardsammlungen („Online“, „Veraltete Software“ … – zeigen ohnehin nur seine Computer),
    - dazu die übergeordneten Ordner, damit der Baum lesbar bleibt.
    Die Sammlungen anderer Mandanten bleiben immer verborgen. None = alle (Hauptadmin)."""
    mine = access.computer_ids(user)
    if mine is None:
        return None
    rows = rows if rows is not None else db.q("SELECT id, parent_id, type, builtin, tenant_id FROM collections")
    kids, par = {}, {}
    for r in rows:
        kids.setdefault(r["parent_id"], []).append(r["id"])
        par[r["id"]] = r["parent_id"]

    def tree(ids):
        out, todo = set(), list(ids)
        while todo:
            x = todo.pop()
            if x in out:
                continue
            out.add(x)
            todo.extend(kids.get(x, []))
        return out
    own_ids = {c for t in access.tenants(user) for c in access.tenant_cols(t)}
    # 2.30: von OU-Admins angelegte Sammlungen – die des eigenen Mandanten gehören dazu, fremde bleiben verborgen
    tids = access.tenant_ids(user)
    own_ids |= {r["id"] for r in rows if r.get("tenant_id") and r["tenant_id"] in tids}
    other = {r["id"] for r in rows if r.get("tenant_id") and r["tenant_id"] not in tids}
    own = tree(own_ids)
    foreign = tree(({c for t in db.q("SELECT collection_id, collection_ids FROM tenants") for c in access.tenant_cols(t)}
                    | other) - own_ids) - own
    keep = set(own)
    for r in rows:
        if r["id"] in foreign or r["type"] == "folder":
            continue
        if r["builtin"] == 1:
            keep.add(r["id"])
            continue
        n = (counts or {}).get(r["id"])
        if n is None:
            try:
                n = len(set(collections_.member_ids(r["id"])) & mine)
            except Exception:
                n = 0
        if n:
            keep.add(r["id"])
    for x in list(keep):  # übergeordnete Ordner/Sammlungen für den Baum
        p, n = par.get(x), 0
        while p and p not in foreign and n < 50:
            keep.add(p)
            p, n = par.get(p), n + 1
    return keep


def _collection_choices(user=None):
    """Sammlungen zur Auswahl (ohne Ordner) mit Pfad der übergeordneten Ordner/Sammlungen – für eine gegliederte,
    alphabetische Auswahlliste (Zeitpläne, Scanprofile, Kiosk, Bedingungen)."""
    rows = db.q("SELECT id, name, parent_id, type, builtin, ad_kind FROM collections")
    by_id = {r["id"]: r for r in rows}

    def parts(r):
        out, seen, p = [], set(), r["parent_id"]
        while p and p in by_id and p not in seen and len(out) < 20:
            seen.add(p)
            out.append([by_id[p]["name"], by_id[p]["builtin"] or 0])
            p = by_id[p]["parent_id"]
        return list(reversed(out))
    items = []
    for r in rows:
        if r["type"] == "folder":
            continue
        pp = parts(r)
        # path: zum Sortieren/Anzeigen; pparts: [Name, builtin] je Ebene – eingebaute Namen übersetzt die Oberfläche
        items.append({"id": r["id"], "name": r["name"], "path": " › ".join(x[0] for x in pp), "pparts": pp,
                      "builtin": r["builtin"] or 0, "ad": bool(r["ad_kind"])})
    items.sort(key=lambda x: (x["path"].lower(), x["name"].lower()))
    vis = _visible_collections(user) if user is not None else None
    if vis is not None:
        items = [x for x in items if x["id"] in vis]
    all_name = "Alle Computer (gesamter Bestand)" if vis is None else "Alle Computer Ihres Bereichs"
    return [{"id": profiles.ALL_COMPUTERS, "name": all_name, "path": "", "builtin": 1, "ad": False}] + items


@app.get("/api/scanprofiles")
def api_profiles(user=U):
    out = []
    for p in profiles.all_profiles():
        p.update(profiles.summary(p))
        out.append(p)
    return {"profiles": out, "modules": inventory.module_list(), "scanners": scanners.list_all(),
            "collections": _collection_choices(user), "trigger_types": profiles.TRIGGER_TYPES}


@app.get("/api/scanprofiles/{pid}")
def api_profile(pid: int, user=U):
    p = profiles.get(pid)
    if not p:
        raise HTTPException(404, "Scanprofil nicht gefunden")
    p.update(profiles.summary(p))
    rows = db.q("""SELECT c.id, c.name, c.online, ps.last_scan, ps.last_attempt, ps.status, ps.pending
                   FROM computers c LEFT JOIN profile_scans ps ON ps.computer_id=c.id AND ps.profile_id=?
                   ORDER BY c.name""", (pid,))
    mem = profiles.members(p) if profiles.has_targets(p) else set()
    p["members"] = [r for r in rows if r["id"] in mem]
    return p


@app.post("/api/scanprofiles")
def api_profile_create(data: dict = Body(...), user=U):
    try:
        pid = profiles.save(None, data)
    except ValueError as e:
        _err(e)
    pklog.info("system", f"Scanprofil '{data.get('name')}' angelegt.", user=user["username"])
    return {"id": pid}


@app.put("/api/scanprofiles/{pid}")
def api_profile_update(pid: int, data: dict = Body(...), user=U):
    if not profiles.get(pid):
        raise HTTPException(404)
    try:
        profiles.save(pid, data)
    except ValueError as e:
        _err(e)
    pklog.info("system", f"Scanprofil '{data.get('name')}' geändert.", user=user["username"])
    return {"ok": True}


@app.post("/api/scanprofiles/{pid}/duplicate")
def api_profile_dup(pid: int, user=U):
    p = profiles.get(pid)
    if not p:
        raise HTTPException(404)
    p["name"] = p["name"] + " (Kopie)"
    for t in p["triggers"]:
        t.pop("id", None)
    return {"id": profiles.save(None, p)}


@app.post("/api/scanprofiles/{pid}/default")
def api_profile_default(pid: int, user=U):
    if not profiles.get(pid):
        raise HTTPException(404)
    profiles.set_default(pid)
    return {"ok": True}


@app.delete("/api/scanprofiles/{pid}")
def api_profile_delete(pid: int, user=U):
    try:
        profiles.delete(pid)
    except ValueError as e:
        _err(e)
    pklog.info("system", f"Scanprofil #{pid} gelöscht.", user=user["username"])
    return {"ok": True}


@app.post("/api/scanprofiles/{pid}/scan")
def api_profile_scan(pid: int, data: dict = Body(...), user=U):
    """Profil sofort ausführen: auf angegebene Computer oder alle Online-Mitglieder der Sammlungen."""
    p = profiles.get(pid)
    if not p:
        raise HTTPException(404)
    ids = [int(i) for i in data.get("ids") or []]
    if not ids:
        mem = profiles.members(p)
        ids = [r["id"] for r in db.q("SELECT id FROM computers WHERE online=1 AND winrm=1") if r["id"] in mem]
    n = sum(1 for i in ids if inventory.queue_scan(i, user["username"], pid))
    return {"queued": n, "busy": len(ids) - n}


@app.get("/api/fields")
def api_fields(user=U):
    return db.q("""SELECT f.*, (SELECT COUNT(*) FROM custom_field_values v WHERE v.field_id=f.id) used
                   FROM custom_fields f ORDER BY sort, name""")


@app.post("/api/fields")
def api_field_create(data: dict = Body(...), user=U):
    name = (data.get("name") or "").strip()
    if not name:
        _err("Name fehlt")
    typ = data.get("type") if data.get("type") in ("text", "number", "date", "bool") else "text"
    return {"id": db.ex("INSERT INTO custom_fields(name, type) VALUES(?,?)", (name[:100], typ))}


@app.delete("/api/fields/{fid}")
def api_field_delete(fid: int, user=U):
    key = f'"cf_{int(fid)}"'
    used = [f"Sammlung „{r['name']}“" for r in db.q("SELECT name, filters FROM collections") if key in (r["filters"] or "")]
    used += [f"Eigener Bericht „{r['name']}“" for r in db.q("SELECT name, definition FROM custom_reports") if key in (r["definition"] or "")]
    if used:
        _err("Das Feld wird noch verwendet: " + ", ".join(used[:6]) + ". Bitte dort zuerst entfernen.")
    db.ex("DELETE FROM custom_fields WHERE id=?", (fid,))
    return {"ok": True}


@app.put("/api/computers/{cid}")
def api_computer_update(cid: int, data: dict = Body(...), user=U):
    db.ex("UPDATE computers SET notes=? WHERE id=?", ((data.get("notes") or "")[:4000], cid))
    return {"ok": True}


@app.delete("/api/computers/{cid}")
def api_computer_delete(cid: int, user=U):
    name = db.val("SELECT name FROM computers WHERE id=?", (cid,))
    db.ex("DELETE FROM computers WHERE id=?", (cid,))
    pklog.info("system", f"Computer {name} gelöscht.", user=user["username"])
    return {"ok": True}


@app.post("/api/computers/scan")
def api_scan(data: dict = Body(...), user=U):
    ids = access.check_ids(user, data.get("ids"))
    pid = int(data["profile_id"]) if data.get("profile_id") else None
    if pid and not profiles.get(pid):
        _err("Scanprofil nicht gefunden.")
    n = sum(1 for i in ids if inventory.queue_scan(i, user["username"], pid))
    return {"queued": n, "busy": len(ids) - n}


@app.post("/api/computers/{cid}/test")
def api_test(cid: int, user=U):
    c = db.q1("SELECT name, dns_name FROM computers WHERE id=?", (cid,))
    if not c:
        raise HTTPException(404)
    return remote.test_connection(c["dns_name"] or c["name"])


def server_role(c):
    """2.34: „dc“ (Domänencontroller), „server“ oder None – aus dem Scan (ProductType 2/3), sonst aus dem Betriebssystem."""
    try:
        ex = json.loads(c.get("extra") or "{}")
    except ValueError:
        ex = {}
    pt = ex.get("product_type")
    try:
        pt = int(pt) if pt not in (None, "") else None
    except (TypeError, ValueError):
        pt = None
    if pt == 2:
        return "dc"
    if pt == 3 or (pt is None and "server" in str(c.get("os_name") or c.get("ad_os") or "").lower()):
        return "server"
    return None


@app.post("/api/computers/power-check")
def api_power_check(data: dict = Body(...), user=U):
    """2.34: Vor Neustart/Herunterfahren – welche Ziele sind Server oder Domänencontroller, wie viele Benutzer sind angemeldet?"""
    ids = access.check_ids(user, data.get("ids"))[:5000]
    out = []
    for i in ids:
        c = db.q1("SELECT id, name, os_name, ad_os, extra, current_user FROM computers WHERE id=?", (i,))
        if not c:
            continue
        role = server_role(c)
        if role:
            users = [u for u in str(c.get("current_user") or "").split(",") if u.strip()]
            out.append({"id": c["id"], "name": c["name"], "role": role, "users": len(users)})
    return {"servers": out}


@app.post("/api/computers/{cid}/action")
def api_action(cid: int, data: dict = Body(...), user=U):
    c = db.q1("SELECT name, dns_name FROM computers WHERE id=?", (cid,))
    if not c:
        raise HTTPException(404)
    act = data.get("action")
    from . import rebootmsg
    # 2.32.3: Wartezeit wählbar (0 = sofort … 24 h, Standard 60 s). /t > 0 schließt Programme ohnehin erzwungen (/f);
    # bei 0 ausdrücklich /f, sonst hält ein Programm mit ungespeicherten Daten den Neustart auf
    try:
        secs = int(data.get("seconds", 60))
    except (TypeError, ValueError):
        _err("Ungültige Wartezeit")
    if not 0 <= secs <= rebootmsg.MAX_DELAY:
        _err("Die Wartezeit muss zwischen 0 Sekunden und 24 Stunden liegen.")
    force = " /f" if secs == 0 else ""
    cmds = {"restart": f"& shutdown.exe /r /t {secs}{force} /c " + util.psq(rebootmsg.text_delay("restart", secs)),
            "shutdown": f"& shutdown.exe /s /t {secs}{force} /c " + util.psq(rebootmsg.text_delay("shutdown", secs)),
            "abort": "shutdown.exe /a",
            # 2.32: das normale „gpupdate /force“ (Computer- und Benutzerrichtlinien). Fragt gpupdate nach Abmelden oder
            # Neustart (z. B. Ordnerumleitung, Softwareinstallation), antwortet „N“ – sonst hinge der Aufruf ohne Konsole
            "gpupdate": "cmd.exe /c 'echo N| gpupdate.exe /force' | Out-String"}
    if act not in cmds:
        _err("Unbekannte Aktion")
    try:
        out, err, _ = remote.ps(c["dns_name"] or c["name"], cmds[act])
    except remote.RemoteError as e:
        _err(e)
    when = (" sofort" if secs == 0 else f" in {secs} s") if act in ("restart", "shutdown") else ""
    pklog.info("system", f"Aktion '{act}'{when} auf {c['name']} ausgeführt.", user=user["username"], computer_id=cid)
    return {"output": (out + "\n" + err).strip()}


# --- Sammlungen ----------------------------------------------------------------------
@app.get("/api/collections")
def api_collections(user=U):
    rows = db.q("SELECT * FROM collections ORDER BY builtin DESC, sort, name COLLATE NOCASE")
    mine = access.computer_ids(user)  # OU-Admin: Anzahl nur seiner Computer
    for r in rows:
        r["editable"] = not r["builtin"] and access.can_edit_collection(user, r)
        # OU-Admin: Regeln nur der eigenen Sammlungen (fremde verraten sonst Namen/OUs anderer Bereiche)
        r["filters"] = collections_.normalize(r["filters"], r["match"]) if mine is None or r["editable"] else []
        if r["type"] == "folder":
            r["count"] = None
        elif mine is None:
            r["count"] = collections_.count(r["id"])
        else:
            try:
                r["count"] = len(set(collections_.member_ids(r["id"])) & mine)
            except Exception:
                r["count"] = 0
    if mine is not None:
        vis = _visible_collections(user, rows, counts={r["id"]: r["count"] for r in rows})
        rows = [r for r in rows if r["id"] in vis]
    return rows


@app.get("/api/collections/schema")
def api_collection_schema(user=U):
    return collections_.schema()


@app.get("/api/collections/values")
def api_collection_values(table: str, column: str, q: str = "", limit: int = 40, user=U):
    try:
        return collections_.values(table, column, q, max(1, min(int(limit), 500)), scope=access.sql_in(user, "c.id"))
    except ValueError as e:
        _err(e)


def _coll_data(data):
    t = data.get("type", "dynamic")
    if t not in ("dynamic", "static", "folder"):
        _err("Ungültiger Typ")
    name = (data.get("name") or "").strip()
    if not name:
        _err("Name fehlt")
    flt = collections_.normalize(data.get("filters") or [], data.get("match", "all"))
    if t == "dynamic":
        try:
            w, p = collections_.build(flt)
            if len(w) > collections_.MAX_SQL:
                raise ValueError("Filter zu umfangreich (zu viele verschachtelte Sammlungsverweise) – bitte vereinfachen.")
            db.val(f"SELECT COUNT(*) FROM computers c WHERE {w}", p)
        except Exception as e:
            _err(e)
    return (name, data.get("parent_id") or None, t, flt.get("match", "all"),
            json.dumps(flt, ensure_ascii=False), data.get("description") or "")


def _coll_scope(user, data, cid=None):
    """2.30: OU-Admin – übergeordneter Ordner nur eine eigene Sammlung (oder oberste Ebene), „ist Mitglied von“ nur
    Sammlungen, die er sieht. Der Inhalt bleibt ohnehin auf die Computer seines Mandanten begrenzt (where_for)."""
    if not access.scoped(user):
        return
    par = data.get("parent_id")
    if par not in (None, "", 0, "0"):
        prow = db.q1("SELECT * FROM collections WHERE id=?", (int(par),))
        if not prow or not access.can_edit_collection(user, prow):
            raise HTTPException(403, "Ablegen nur in Ihren eigenen Ordnern oder auf der obersten Ebene.")
    refs = collections_._tree_refs(collections_.normalize(data.get("filters") or [], data.get("match", "all")), set())
    vis = _visible_collections(user) or set()
    if refs - vis:
        raise HTTPException(403, "„Ist Mitglied von“ ist nur mit Sammlungen möglich, die Sie sehen.")


def _own_tenant_col(user, data):
    """Mandant, dem eine neue Sammlung eines OU-Admins gehört (None = Hauptadmin, zentral)."""
    if not access.scoped(user):
        return None
    par = data.get("parent_id")
    if par not in (None, "", 0, "0"):
        t = db.val("SELECT tenant_id FROM collections WHERE id=?", (int(par),))
        if t:
            return access.own_tenant_for(user, t)
    return access.own_tenant_for(user, data.get("tenant_id"))


@app.post("/api/collections")
def api_collection_create(data: dict = Body(...), user=U):
    _coll_scope(user, data)
    tid = _own_tenant_col(user, data)
    cid = db.ex("INSERT INTO collections(name, parent_id, type, match, filters, description, tenant_id) VALUES(?,?,?,?,?,?,?)",
                _coll_data(data) + (tid,))
    if tid:
        pklog.info("system", f"Sammlung '{data.get('name')}' angelegt (Mandant #{tid}).", user=user["username"])
    return {"id": cid}


@app.put("/api/collections/{cid}")
def api_collection_update(cid: int, data: dict = Body(...), user=U):
    if db.val("SELECT builtin FROM collections WHERE id=?", (cid,)):
        _err("Standardsammlungen können nicht geändert werden.")
    if data.get("parent_id") and (int(data["parent_id"]) == cid or int(data["parent_id"]) in _descendants(cid)):
        _err("Ungültiger übergeordneter Ordner")
    _coll_scope(user, data, cid)
    try:
        collections_.check_cycle(cid, collections_.normalize(data.get("filters") or [], data.get("match", "all")),
                                 data.get("parent_id"))
    except ValueError as e:
        _err(e)
    values = _coll_data(data)
    before = collections_.broken(collections_.dependents(cid))
    try:
        with db.tx():  # Sammlungen, die auf dieser aufbauen, dürfen dadurch nicht kaputtgehen – sonst zurückrollen
            db.ex("UPDATE collections SET name=?, parent_id=?, type=?, match=?, filters=?, description=? WHERE id=?", values + (cid,))
            collections_.check_dependents(cid, before)
    except ValueError as e:
        _err(e)
    collections_.invalidate()
    return {"ok": True}


@app.delete("/api/collections/{cid}")
def api_collection_delete(cid: int, user=U):
    if db.val("SELECT builtin FROM collections WHERE id=?", (cid,)):
        _err("Standardsammlungen können nicht gelöscht werden.")
    ids = [cid] + _descendants(cid)
    if any(db.val("SELECT builtin FROM collections WHERE id=?", (i,)) for i in ids):
        _err("Enthält Standard- oder AD-Sammlungen und kann nicht gelöscht werden.")
    if access.scoped(user) and not all(access.can_edit_collection(user, db.q1("SELECT * FROM collections WHERE id=?", (i,)))
                                       for i in ids):
        raise HTTPException(403, "Enthält Sammlungen, die nur ein Hauptadmin löschen darf.")
    if any(i in access.tenant_cols(t) for t in db.q("SELECT collection_id, collection_ids FROM tenants") for i in ids):
        _err("Ist einem Mandanten zugeordnet und kann deshalb nicht gelöscht werden – bitte zuerst beim Mandanten entfernen.")
    used = collections_.used_by(ids, exclude_self=True)
    if used:
        _err("Wird noch verwendet und kann deshalb nicht gelöscht werden: " + ", ".join(used[:6])
             + (f" und {len(used) - 6} weitere" if len(used) > 6 else "") + ". Bitte dort zuerst entfernen.")
    with db.tx():
        for i in reversed(ids):
            db.ex("DELETE FROM collections WHERE id=?", (i,))
    return {"ok": True, "deleted": len(ids)}


def _descendants(cid):
    out, todo = [], [cid]
    while todo:
        cur = todo.pop()
        for r in db.q("SELECT id FROM collections WHERE parent_id=?", (cur,)):
            out.append(r["id"])
            todo.append(r["id"])
    return out


@app.post("/api/collections/{cid}/move")
def api_collection_move(cid: int, data: dict = Body(...), user=U):
    """In einen Ordner/eine Sammlung verschieben (parent_id=None: oberste Ebene), optional vor eine andere Sammlung."""
    col = db.q1("SELECT * FROM collections WHERE id=?", (cid,))
    if not col:
        raise HTTPException(404)
    if col["builtin"]:
        _err("Standard- und AD-Sammlungen können nicht verschoben werden – bitte kopieren und einfügen.")
    parent = data.get("parent_id") or None
    if parent is not None:
        parent = int(parent)
        prow = db.q1("SELECT id, builtin, ad_kind FROM collections WHERE id=?", (parent,))
        if not prow:
            _err("Ziel nicht gefunden")
        if parent == cid or parent in _descendants(cid):
            _err("Eine Sammlung kann nicht in sich selbst verschoben werden.")
        if prow["builtin"]:
            _err("In Standard- und AD-Sammlungen kann nichts verschoben werden.")
    _coll_scope(user, {"parent_id": parent})
    before = collections_.broken(collections_.dependents(cid))
    with db.tx():
        db.ex("UPDATE collections SET parent_id=? WHERE id=?", (parent, cid))
        try:
            collections_.check_dependents(cid, before)  # unter einer Sammlung zählt deren Filter mit
        except ValueError as e:
            _err(e)
        before = data.get("before_id")
        sibs = [r["id"] for r in db.q("SELECT id FROM collections WHERE builtin=0 AND "
                                      + ("parent_id IS NULL" if parent is None else "parent_id=?")
                                      + " AND id != ? ORDER BY sort, name COLLATE NOCASE",
                                      ((cid,) if parent is None else (parent, cid)))]
        pos = sibs.index(int(before)) if before and int(before) in sibs else len(sibs)
        sibs.insert(pos, cid)
        for i, sid in enumerate(sibs):
            db.ex("UPDATE collections SET sort=? WHERE id=?", (100 + i, sid))
    collections_.invalidate()
    access.flush()  # Audit 2.29: Mandanten-Mitgliedschaft sofort neu
    return {"ok": True}


def _copy_collection(cid, parent, depth=0, rename=True, tenant=None, vis=None, only=None, user=None):
    """tenant/vis/only (2.30, OU-Admin): Kopie gehört dem Mandanten, nur sichtbare Unterelemente, statische Mitglieder nur
    eigene Computer."""
    col = db.q1("SELECT * FROM collections WHERE id=?", (cid,))
    if not col or depth > 20 or (vis is not None and cid not in vis):
        return None
    name = col["name"] + (" (Kopie)" if rename else "")
    filters = col["filters"]
    if col.get("ad_kind") and col["type"] == "dynamic":
        filters = json.dumps(collections_.normalize(filters, col["match"]), ensure_ascii=False)
    if vis is not None and col["type"] == "dynamic":
        # Regeln mit Verweisen auf Sammlungen, die der OU-Admin nicht sieht, nicht mitnehmen
        if collections_._tree_refs(collections_.normalize(filters, col["match"]), set()) - vis:
            filters = json.dumps(collections_.normalize([], "all"), ensure_ascii=False)
        # 2.32.5: Regeln fremder Sammlungen (Hauptadmin, andere Mandanten) zeigt die Liste dem OU-Admin nicht – die Kopie
        # darf sie auch nicht verraten (Computernamen, OUs, IP-Bereiche anderer Mandanten): stattdessen „ist Mitglied von“
        elif user is not None and not access.can_edit_collection(user, col):
            filters = json.dumps(collections_.normalize(
                [{"type": "cond", "table": "computer", "column": "collection", "op": "in", "value": str(cid)}], "all"), ensure_ascii=False)
    new = db.ex("INSERT INTO collections(name, parent_id, type, match, filters, description, builtin, sort, tenant_id) "
                "VALUES(?,?,?,?,?,?,0,?,?)", (name, parent, col["type"], col["match"], filters, col.get("description"),
                                              (col.get("sort") or 100) + 1, tenant if tenant else col.get("tenant_id")))
    if col["type"] == "static":
        for r in db.q("SELECT computer_id FROM collection_members WHERE collection_id=?", (cid,)):
            if only is None or r["computer_id"] in only:
                db.ex("INSERT INTO collection_members(collection_id, computer_id) VALUES(?,?)", (new, r["computer_id"]))
    for ch in db.q("SELECT id FROM collections WHERE parent_id=? ORDER BY sort, name", (cid,)):
        _copy_collection(ch["id"], new, depth + 1, rename=False, tenant=tenant, vis=vis, only=only, user=user)
    return new


@app.post("/api/collections/{cid}/duplicate")
def api_collection_duplicate(cid: int, data: dict = Body(default={}), user=U):
    """Kopie inkl. Unterelementen (auch von Standard- und AD-Sammlungen) – für „Duplizieren“ und „Kopieren/Einfügen“."""
    col = db.q1("SELECT * FROM collections WHERE id=?", (cid,))
    if not col:
        raise HTTPException(404)
    if "parent_id" in data:
        parent = int(data["parent_id"]) if data.get("parent_id") else None
    else:
        parent = None if col["builtin"] else col["parent_id"]
    if parent is not None:
        prow = db.q1("SELECT builtin FROM collections WHERE id=?", (parent,))
        if not prow:
            _err("Ziel nicht gefunden")
        if prow["builtin"]:
            parent = None
        if parent == cid or parent in _descendants(cid):
            _err("Eine Sammlung kann nicht in sich selbst eingefügt werden.")
    tenant = vis = only = None
    if access.scoped(user):
        # 2.30: OU-Admin – Kopie (auch einer Hauptadmin-Sammlung, als Vorlage) gehört seinem Mandanten
        if not access.may_build(user):
            raise HTTPException(403, "Eigene Sammlungen sind für Ihren Mandanten nicht freigegeben.")
        prow = db.q1("SELECT * FROM collections WHERE id=?", (parent,)) if parent else None
        if prow and not access.can_edit_collection(user, prow):
            parent = None
        tenant = _own_tenant_col(user, {"parent_id": parent, "tenant_id": col.get("tenant_id")})
        vis, only = _visible_collections(user) or set(), access.computer_ids(user)
    with db.tx():
        new = _copy_collection(cid, parent, tenant=tenant, vis=vis, only=only, user=user if vis is not None else None)
    pklog.info("system", f"Sammlung '{col['name']}' kopiert.", user=user["username"])
    return {"id": new}


@app.post("/api/collections/{cid}/rename")
def api_collection_rename(cid: int, data: dict = Body(...), user=U):
    if db.val("SELECT builtin FROM collections WHERE id=?", (cid,)):
        _err("Standard- und AD-Sammlungen können nicht umbenannt werden.")
    name = (data.get("name") or "").strip()
    if not name:
        _err("Name fehlt")
    db.ex("UPDATE collections SET name=? WHERE id=?", (name[:200], cid))
    return {"ok": True}


@app.post("/api/collections/{cid}/members")
def api_collection_members(cid: int, data: dict = Body(...), user=U):
    if db.val("SELECT type FROM collections WHERE id=?", (cid,)) != "static":
        _err("Mitglieder können nur zu statischen Sammlungen hinzugefügt werden.")
    access.check_ids(user, data.get("add"))  # 2.30: OU-Admin – nur eigene Computer
    with db.tx():
        for i in data.get("add") or []:
            db.ex("INSERT OR IGNORE INTO collection_members(collection_id, computer_id) VALUES(?,?)", (cid, int(i)))
        for i in data.get("remove") or []:
            db.ex("DELETE FROM collection_members WHERE collection_id=? AND computer_id=?", (cid, int(i)))
    collections_.invalidate()
    access.flush()  # Audit 2.29: Mandanten-Mitgliedschaft sofort neu
    return {"ok": True}


@app.post("/api/collections/preview")
def api_collection_preview(data: dict = Body(...), user=U):
    try:
        _coll_scope(user, {"filters": data.get("filters"), "match": data.get("match", "all")})
        w, p = collections_.build(data.get("filters") or [], data.get("match", "all"))
        sw, sp = access.sql_in(user, "c.id")  # 2.30: OU-Admin – nur eigene Computer
        w, p = f"({w}) AND {sw}", list(p) + list(sp)
        rows = db.q(f"""SELECT c.id, c.name, COALESCE(c.os_name, c.ad_os) os, c.online FROM computers c
                        WHERE {w} ORDER BY c.name LIMIT 200""", p)
        return {"count": db.val(f"SELECT COUNT(*) FROM computers c WHERE {w}", p, 0), "sample": rows}
    except Exception as e:
        _err(e)


# --- Software -------------------------------------------------------------------------
@app.get("/api/software")
def api_software(collection: int = 0, user=U):
    where, params = ("1=1", [])
    sw, sp = access.sql_in(user, "c.id")
    if collection:
        try:
            where, params = collections_.where_for(collection)
        except ValueError as e:
            _err(e)
    rows = db.q(f"""SELECT s.name, MAX(s.publisher) publisher, COUNT(DISTINCT s.computer_id) installs,
                    COUNT(DISTINCT s.version) versions, MAX(s.winget_id) winget_id, MAX(s.winget_latest) winget_latest,
                    SUM(s.outdated) outdated, MAX(o.ignore) ignored, MAX(COALESCE(s.excluded, 0)) excluded
                    FROM software s JOIN computers c ON c.id=s.computer_id
                    LEFT JOIN winget_overrides o ON o.name = s.name
                    WHERE {where} AND {sw} GROUP BY s.name ORDER BY s.name COLLATE NOCASE""", params + sp)
    return rows


@app.get("/api/software/detail")
def api_software_detail(name: str, user=U):
    sw, sp = access.sql_in(user, "c.id")
    return {
        "computers": db.q(f"""SELECT c.id, c.name, c.online, c.os_build, s.version, s.arch, s.scope, s.install_date, s.outdated,
                             s.winget_id, s.winget_latest FROM software s JOIN computers c ON c.id=s.computer_id
                             WHERE s.name=? AND {sw} ORDER BY c.name""", [name] + sp),
        "override": db.q1("SELECT * FROM winget_overrides WHERE name=?", (name,)),
        "exclusion": _exclusion_for(name),
    }


def _exclusion_for(name):
    r = db.q1("SELECT name, publisher, MAX(winget_id) winget_id FROM software WHERE name=? GROUP BY name", (name,))
    hit = winget.excluded_by(name, r["publisher"] if r else "", r["winget_id"] if r else "") if r else None
    return {k: v for k, v in hit.items() if not k.startswith("_")} if hit else None


@app.post("/api/software/update")
def api_software_update(data: dict = Body(...), user=U):
    """Mehrfachauswahl in der Softwareübersicht: alle veralteten Installationen der gewählten Programme per
    winget aktualisieren (je winget-ID eine Bereitstellung; optional nur bestimmte Computer)."""
    names = [n for n in data.get("names") or [] if isinstance(n, str)]
    only = set(access.check_ids(user, data.get("computer_ids")))
    mine = access.computer_ids(user)
    if not names:
        _err("Keine Programme ausgewählt.")
    groups, skipped = {}, []
    for n in names:
        rows = db.q("""SELECT s.computer_id, s.winget_id FROM software s JOIN computers c ON c.id=s.computer_id
                       WHERE s.name=? AND s.winget_id IS NOT NULL AND (s.outdated=1 OR ?)
                       AND (c.os_build IS NULL OR c.os_build='' OR CAST(c.os_build AS INTEGER) >= ?)""",
                    (n, 1 if only else 0, deploy.WINGET_MIN_BUILD))
        rows = [r for r in rows if (not only or r["computer_id"] in only) and (mine is None or r["computer_id"] in mine)]
        if not rows:
            skipped.append(n)
        for r in rows:
            groups.setdefault(r["winget_id"], set()).add(r["computer_id"])
    out = []
    for wid, cids in groups.items():
        try:
            pkg = deploy.adhoc_winget(wid, "upgrade")
            pkg["name"] = f"Aktualisieren: {wid}"
            out.append(deploy.create(pkg, sorted(cids), user["username"]))
        except ValueError as e:
            _err(e)
    return {"deployments": out, "skipped": skipped, "computers": sum(len(v) for v in groups.values())}


# --- Ausnahmen der Aktualitätsprüfung -------------------------------------------------------
def _excl_clean(data):
    field = data.get("field") if data.get("field") in winget.EXCL_FIELDS else "name"
    pattern = re.sub(r"\s+", " ", str(data.get("pattern") or "")).strip()
    if not pattern:
        _err("Muster angeben, z. B. „Microsoft Visual C++*“.")
    if pattern.strip("*? ") == "":
        _err("Das Muster würde alle Programme ausnehmen.")
    if re.sub(r"\*+", "*", pattern).count("*") > 12:
        _err("Muster: höchstens 12 Platzhalter *.")
    return field, pattern[:300], str(data.get("note") or "").strip()[:300]


def _excl_matches(field, pattern, limit=200):
    col = {"name": "s.name", "publisher": "s.publisher", "winget_id": "s.winget_id"}[field]
    rows = db.q(f"SELECT {col} v, COUNT(DISTINCT s.computer_id) n FROM software s WHERE {col} IS NOT NULL AND {col} != '' GROUP BY {col}")
    rx = winget.wildcard(pattern)
    hits = [{"value": r["v"], "computers": r["n"]} for r in rows if rx.match(r["v"])]
    hits.sort(key=lambda x: x["value"].lower())
    return hits[:limit], len(hits)


@app.get("/api/software/exclusions")
def api_exclusions(user=U):
    rules = db.q("SELECT * FROM update_exclusions ORDER BY lower(pattern)")
    for r in rules:
        r["field_label"] = winget.EXCL_FIELDS.get(r["field"], r["field"])
        r["matches"] = _excl_matches(r["field"], r["pattern"], 0)[1]
    ignored = db.q("SELECT name, winget_id FROM winget_overrides WHERE ignore=1 ORDER BY name COLLATE NOCASE")
    return {"rules": rules, "ignored": ignored, "fields": [{"key": k, "label": v} for k, v in winget.EXCL_FIELDS.items()]}


@app.post("/api/software/exclusions/preview")
def api_exclusions_preview(data: dict = Body(...), user=U):
    field, pattern, _note = _excl_clean(data)
    hits, total = _excl_matches(field, pattern, 100)
    return {"hits": hits, "total": total}


@app.post("/api/software/exclusions")
def api_exclusions_add(data: dict = Body(...), user=U):
    field, pattern, note = _excl_clean(data)
    if db.val("SELECT 1 FROM update_exclusions WHERE field=? AND lower(pattern)=lower(?)", (field, pattern)):
        _err("Diese Ausnahme gibt es schon.")
    rid = db.ex("INSERT INTO update_exclusions(field, pattern, note, created_by, created_at) VALUES(?,?,?,?,?)",
                (field, pattern, note, user["username"], util.now()))
    winget.exclusions(reload=True)
    scheduler.run_now("rematch")
    pklog.info("winget", f"Ausnahme der Aktualitätsprüfung hinzugefügt: {winget.EXCL_FIELDS[field]} „{pattern}“",
               user=user["username"])
    return {"id": rid}


@app.delete("/api/software/exclusions/{rid}")
def api_exclusions_delete(rid: int, user=U):
    r = db.q1("SELECT * FROM update_exclusions WHERE id=?", (rid,))
    if not r:
        raise HTTPException(404, "Ausnahme nicht gefunden")
    db.ex("DELETE FROM update_exclusions WHERE id=?", (rid,))
    winget.exclusions(reload=True)
    scheduler.run_now("rematch")
    pklog.info("winget", f"Ausnahme der Aktualitätsprüfung entfernt: {winget.EXCL_FIELDS.get(r['field'], r['field'])} „{r['pattern']}“",
               user=user["username"])
    return {"ok": True}


@app.post("/api/software/override")
def api_software_override(data: dict = Body(...), user=U):
    name = (data.get("name") or "").strip()
    wid = (data.get("winget_id") or "").strip()
    ignore = 1 if data.get("ignore") else 0
    if not name:
        _err("Name fehlt")
    if data.get("remove"):
        db.ex("DELETE FROM winget_overrides WHERE name=?", (name,))
    else:
        if wid and not util.WINGET_ID_RE.match(wid):
            _err("Ungültige winget-ID")
        db.ex("INSERT INTO winget_overrides(name, winget_id, ignore) VALUES(?,?,?) "
              "ON CONFLICT(name) DO UPDATE SET winget_id=excluded.winget_id, ignore=excluded.ignore", (name, wid, ignore))
    winget.overrides(reload=True)
    scheduler.run_now("rematch")
    pklog.info("winget", f"Zuordnung für '{name}' geändert: {wid or '-'}{' (ignoriert)' if ignore else ''}",
               user=user["username"])
    return {"ok": True}


# --- winget ---------------------------------------------------------------------------
@app.get("/api/winget/status")
def api_winget_status(user=U):
    return winget.status()


@app.post("/api/winget/refresh")
def api_winget_refresh(user=U):
    scheduler.run_now("winget")
    return {"ok": True}


@app.post("/api/winget/upload")
async def api_winget_upload(request: Request, user=U):
    import tempfile
    from starlette.concurrency import run_in_threadpool
    limit = 300 * 1024 * 1024
    fd, tmp = tempfile.mkstemp(prefix="upload-", suffix=".msix", dir=config.WINGET_DIR)
    size = 0
    try:
        # In Stücken auf die Platte schreiben (nicht alles im Speicher) und Größe begrenzen
        with os.fdopen(fd, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    _err("Datei zu groß (höchstens 300 MB).")
                await run_in_threadpool(f.write, chunk)
        if size < 1000:
            _err("Leere Datei")
        try:
            # Entpacken und Abgleich laufen im Thread-Pool, damit Oberfläche und Fernsitzungen nicht einfrieren
            await run_in_threadpool(winget.install_msix, tmp)
        except Exception as e:
            _err(e)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    pklog.info("winget", "winget-Index manuell hochgeladen.", user=user["username"])
    return winget.status()


@app.get("/api/winget/search")
def api_winget_search(q: str = "", user=U):
    return winget.search(q)


# --- Pakete -----------------------------------------------------------------------------
def _pkg_values(data, pid=None, user=None):
    try:
        p = steps.clean_package(data)
        steps.check_nesting(pid, p)
    except ValueError as e:
        _err(e)
    access.check_nested(user, p["steps"], p.get("tenant_id"))  # verschachtelt nur zentrale oder desselben Mandanten
    if not p.get("tenant_id"):
        # 2.27: zentrale Pakete nie mit Dateien aus einem Mandantenordner – dessen Admins könnten sie ändern oder
        # etwas danebenlegen, das dann auf allen Computern mitläuft
        tf = [(t["name"], integrity.key(t["repo_folder"])) for t in db.q("SELECT name, repo_folder FROM tenants WHERE COALESCE(repo_folder, '') != ''")]
        for _k, st in steps.iter_steps({"steps": p["steps"]}):
            paths = [st.get("folder") or ""] if st.get("type") == "install" and st.get("source") == "file" else (
                [st.get("source") or ""] if st.get("type") == "filecopy" else [])
            for pth in paths:
                k = integrity.key(pth)
                for tn, f in tf:
                    if k and f and (k == f or k.startswith(f + "\\")):
                        _err(f"Zentrale Pakete dürfen keine Dateien aus dem Ordner von Mandant „{tn}“ benutzen ({pth}) – "
                             "bitte die Dateien in einen zentralen Ordner legen oder das Paket dem Mandanten zuordnen.")
    if access.scoped(user):
        # Code eines OU-Admins läuft nie als das domänenweite Dienstkonto – „Lokales System“ statt „Dienstkonto“
        if p.get("run_as") == "deploy":
            p["run_as"] = "system"
        for _k, st in steps.iter_steps(p):
            if st.get("run_as") == "deploy":
                st["run_as"] = "system"
        # 2.29.1: eigener Code (Befehl, PowerShell, eigener Deinstallationsbefehl) nur, wenn beim Mandanten erlaubt –
        # unverändert übernommene Schritte (z. B. von einem Hauptadmin geschrieben) bleiben möglich
        if not access.may_script(user, p.get("tenant_id")):
            old = db.q1("SELECT steps FROM packages WHERE id=?", (pid,)) if pid else None
            had = [_script_sig(st) for _k, st in steps.iter_steps(steps.normalize({"steps": old["steps"]}))] if old else []
            for _k, st in steps.iter_steps(p):
                sig = _script_sig(st)
                if sig is None:
                    continue
                if sig in had:
                    had.remove(sig)
                    continue
                raise HTTPException(403, "Befehls- und PowerShell-Schritte (und eigene Deinstallationsbefehle) sind für Ihren "
                                         "Mandanten nicht freigegeben. Skripte bitte als Datei (.ps1/.cmd) in Ihren "
                                         "Repository-Ordner legen und mit „Installieren“ ausführen – oder einen Hauptadmin fragen.")
        # Dateien aus dem Repository nur mit einem Repository-Ordner des Mandanten (sonst wäre jeder Ordner erreichbar)
        if not (db.val("SELECT repo_folder FROM tenants WHERE id=?", (p.get("tenant_id") or 0,)) or "").strip():
            for _k, st in steps.iter_steps(p):
                if (st.get("type") == "install" and st.get("source") == "file") or st.get("type") == "filecopy":
                    raise HTTPException(403, "Dateien aus dem Repository: für Ihren Mandanten ist kein Repository-Ordner eingerichtet "
                                             "(bitte einen Hauptadmin fragen). winget, Befehle und Skripte gehen ohne.")
    p["steps"] = json.dumps(p["steps"], ensure_ascii=False)
    p["conditions"] = json.dumps(p["conditions"], ensure_ascii=False)
    return p


def _script_sig(st):
    """Eigener Code in einem Schritt – (Art, Text) oder None."""
    t = st.get("type")
    if t == "command":
        return ("command", (st.get("command") or "").strip())
    if t == "powershell":
        return ("powershell", (st.get("script") or "").strip())
    if t == "uninstall" and (st.get("command") or "").strip():
        return ("uninstall", st["command"].strip())
    return None


# Hinweis (KEINE Sicherheitsgrenze – lässt sich umschreiben): Zugriffe auf Freigaben oder Downloads in Mandanten-Skripten
NET_HINT_RX = re.compile(r"(\\\\[A-Za-z0-9_.$-]+\\|\bnet(?:\.exe)?\s+use\b|New-PSDrive|Invoke-WebRequest|\biwr\b|"
                         r"Invoke-RestMethod|\birm\b|Start-BitsTransfer|bitsadmin|Net\.WebClient|DownloadFile|DownloadString|"
                         r"\bcurl(?:\.exe)?\b|\bwget\b|certutil[^\n]*-urlcache|https?://)", re.I)


def net_hints(p):
    """Stellen in Skripten eines Mandantenpakets, die auf Freigaben oder ins Internet zugreifen – nur als Hinweis."""
    out = []
    for _k, st in steps.iter_steps(steps.normalize(p)):
        sig = _script_sig(st)
        texts = [sig[1]] if sig else []
        if st.get("type") == "install" and st.get("source") == "file":
            texts.append(st.get("arguments") or "")
        for t in texts:
            m = NET_HINT_RX.search(t or "")
            if m:
                out.append(f"{steps.step_title(st)}: „{m.group(0)[:60]}“")
                break
    return out


def _pkg_out(r, stats=None, names=None):
    p = steps.normalize(deploy.row_to_package(r))
    p.update({k: r.get(k) for k in ("created_at", "updated_at")})
    all_steps = [s for _k, s in steps.iter_steps(p)]
    p["step_count"] = len(all_steps)
    p["step_types"] = sorted({s["type"] for s in all_steps})
    p["conditions_text"] = steps.describe_conditions(steps.clean_conditions(p["conditions"]))
    p["run_as_label"] = steps.RUN_AS.get(p["run_as"], p["run_as"])
    p["tenant_name"] = (names or {}).get(p.get("tenant_id"))
    p["ou_edit"] = 1 if r.get("ou_edit") else 0  # 2.30: OU-Admins des Mandanten dürfen ändern
    # Windows-Benachrichtigung am Client: dieselbe Regel wie toast.classify (Häkchen in der Paketliste)
    from . import toast
    mode = p.get("notify_user") or ""
    installs = any(toast._real_install(s, mode) for s in all_steps if s.get("type") != "nested")
    uninstall = bool(re.match(r"^(Deinstallation|Uninstall)\b", p.get("name") or "", re.I))
    try:
        p["integrity"] = json.loads(r.get("integrity") or "null")
    except ValueError:
        p["integrity"] = None
    p["notify_mode"] = mode
    p["notify_effective"] = mode == "on" or (mode == "" and bool(db.setting("toast_enabled")) and installs and not uninstall)
    if p.get("type") == "winget" and p.get("winget_id"):
        pk = winget.package(p["winget_id"])
        p["latest"] = pk["latest"] if pk else None
        p["catalog_name"] = pk["name"] if pk else None
        st = (stats or {}).get(p["winget_id"].lower()) or {}
        p["installs"] = st.get("installs", 0)
        p["outdated_installs"] = st.get("outdated", 0)
        # Ohne feste Version wird immer die neueste installiert – die angezeigte Version folgt dem Katalog
        p["effective_version"] = p.get("winget_version") or p["latest"]
    return p


@app.get("/api/packages")
def api_packages(user=U):
    rows = [r for r in db.q("SELECT * FROM packages ORDER BY name COLLATE NOCASE") if access.can_see_package(user, r)]
    ws, ps = access.sql_in(user, "computer_id")  # OU-Admin: Installationszahlen nur seiner Computer
    stats = {r["wid"]: r for r in db.q(f"""SELECT lower(winget_id) wid, COUNT(DISTINCT computer_id) installs,
                                          COUNT(DISTINCT CASE WHEN outdated=1 THEN computer_id END) outdated
                                          FROM software WHERE winget_id IS NOT NULL AND {ws} GROUP BY lower(winget_id)""", ps)}
    names = {t["id"]: t["name"] for t in db.q("SELECT id, name FROM tenants")}
    out = [_pkg_out(r, stats, names) for r in rows]
    pref = _ou_prefixes(user)
    for p in out:
        p["editable"] = access.can_edit_package(user, p)
        p["integrity"] = integrity.scope_view(p.get("integrity"), pref)
    return out


@app.get("/api/packages/meta")
def api_packages_meta(user=U):
    return {"os": steps.OS_KEYS, "arch": steps.ARCH_KEYS, "logon": steps.LOGON, "ps_versions": steps.PS_VERSIONS,
            "file_modes": steps.FILE_MODES, "reg_modes": steps.REG_MODES, "run_as": steps.RUN_AS,
            "step_run_as": steps.STEP_RUN_AS, "step_types": steps.STEP_TYPES, "phases": steps.PHASES,
            "default_conditions": steps.default_conditions(), "service_login": config.service_login() if not access.scoped(user) else "verborgen",
            "tenants": ([{"id": t["id"], "name": t["name"], "repo_folder": t["repo_folder"]} for t in access.build_tenants(user)]
                        if access.scoped(user) else db.q("SELECT id, name, repo_folder FROM tenants ORDER BY name COLLATE NOCASE")),
            "profiles": [{"id": p["id"], "name": p["name"], "is_default": p["is_default"]} for p in profiles.all_profiles()],
            "collections": _collection_choices(user),
            "packages": [{"id": r["id"], "name": r["name"]} for r in db.q("SELECT id, name, tenant_id FROM packages ORDER BY name COLLATE NOCASE")
                         if access.can_see_package(user, r)]}


@app.get("/api/packages/{pid}")
def api_package(pid: int, user=U):
    r = access.need_package(user, pid)
    out = _pkg_out(r)
    out["editable"] = access.can_edit_package(user, r)
    out["integrity"] = integrity.scope_view(out.get("integrity"), _ou_prefixes(user))
    return out


@app.post("/api/packages/{pid}/integrity/check")
def api_package_integrity_check(pid: int, user=U):
    """Repository-Dateien des Pakets jetzt direkt auf der Freigabe prüfen (mit Fingerabdruck bei geändertem Datum)."""
    access.need_package(user, pid)
    if not integrity.throttle_check(user["username"], pid):
        raise HTTPException(429, "Gerade eben geprüft – bitte einige Sekunden warten.")
    res = integrity.check_package(pid, live=True, who=user["username"])
    return integrity.scope_view(res, _ou_prefixes(user))


@app.post("/api/packages/{pid}/integrity/accept")
def api_package_integrity_accept(pid: int, user=U):
    """„Neuen Stand übernehmen“: der aktuelle Inhalt der Repository-Dateien gilt als bestätigt (bewusste Änderung).
    OU-Admins: nur Dateien in ihren Mandanten-Ordnern (zentrale Dateien bestätigt nur ein Hauptadmin)."""
    r = access.need_package(user, pid)
    if not access.can_edit_package(user, r):
        raise HTTPException(403, "Nur wer das Paket ändern darf, kann den neuen Stand übernehmen.")
    try:
        out = integrity.accept(deploy.row_to_package(r), user["username"], _ou_prefixes(user), pid=pid)
    except (remote.RemoteError, OSError) as e:
        _err(_repo_err(user, e))
    res = integrity.check_package(pid, live=True, who=user["username"])
    pklog.info("deploy", f"Paket „{r['name']}“: neuer Stand der Repository-Dateien übernommen ({out['count']} Datei(en)).",
               user=user["username"])
    msi = out.get("msi")
    if msi and msi.get("version") and (r.get("version") or "").strip() != msi["version"]:
        out["version_hint"] = msi["version"]
    out["integrity"] = integrity.scope_view(res, _ou_prefixes(user))
    return out


@app.post("/api/packages")
def api_package_create(data: dict = Body(...), user=U):
    if access.scoped(user):
        data = {**data, "tenant_id": access.own_tenant_for(user, data.get("tenant_id"))}
    p = _pkg_values(data, user=user)
    ids, pending = _dup_guard(p, None, user, data)
    cols = deploy.PKG_FIELDS + ["created_at", "updated_at"]
    pid = db.ex(f"INSERT INTO packages({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
                tuple(p[k] for k in deploy.PKG_FIELDS) + (util.now(), util.now()))
    db.ex("UPDATE packages SET ou_edit=? WHERE id=?", (_ou_edit_flag(user, data), pid))
    repofiles.store_idents(pid, ids, pending)
    pklog.info("deploy", f"Paket '{p['name']}' angelegt.", user=user["username"])
    integrity.after_save(pid)  # Stand der Repository-Dateien festhalten
    return {"id": pid, "warnings": _net_warn(p, user)}


@app.put("/api/packages/{pid}")
def api_package_update(pid: int, data: dict = Body(...), user=U):
    if not db.val("SELECT 1 FROM packages WHERE id=?", (pid,)):
        raise HTTPException(404)
    if access.scoped(user):
        data = {**data, "tenant_id": access.own_tenant_for(user, data.get("tenant_id"))}
    p = _pkg_values(data, pid, user=user)
    ids, pending = _dup_guard(p, pid, user, data)
    db.ex(f"UPDATE packages SET {', '.join(k + '=?' for k in deploy.PKG_FIELDS)}, updated_at=? WHERE id=?",
          tuple(p[k] for k in deploy.PKG_FIELDS) + (util.now(), pid))
    db.ex("UPDATE packages SET ou_edit=? WHERE id=?",
          (_ou_edit_flag(user, data, db.q1("SELECT ou_edit FROM packages WHERE id=?", (pid,))), pid))
    repofiles.store_idents(pid, ids, pending)
    pklog.info("deploy", f"Paket '{p['name']}' geändert.", user=user["username"])
    integrity.after_save(pid)  # neue Dateien festhalten – geänderte bleiben Bruch, bis jemand bestätigt
    return {"ok": True, "warnings": _net_warn(p, user)}


def _ou_edit_flag(user, data, old=None):
    """2.30: Darf ein OU-Admin das Paket/den Zeitplan ändern? Selbst angelegt: ja. Vom Hauptadmin: nur mit Häkchen
    „OU-Admins dürfen ändern“ (Standard: nein – sonst ändert ein Mandant, was der Hauptadmin für ihn gebaut hat)."""
    if access.scoped(user):
        return 1
    if "ou_edit" in data:
        return 1 if data.get("ou_edit") else 0
    return 1 if (old or {}).get("ou_edit") else 0


def _net_warn(p, user):
    """2.29.1: Mandantenpaket mit Zugriff auf Freigaben/Internet im Skript → Hinweis an die Oberfläche und ins Protokoll."""
    if not p.get("tenant_id"):
        return []
    try:
        hits = net_hints({"steps": json.loads(p["steps"]) if isinstance(p["steps"], str) else p["steps"]})
    except Exception:  # noqa: BLE001 – ein Hinweis darf das Speichern nie verhindern
        return []
    if hits:
        found = "; ".join(hits[:5])
        pklog.warn("deploy", f"Paket '{p['name']}' (Mandant) greift im Skript auf Freigaben oder das Internet zu: {found}",
                   user=user["username"])
        return [f"Zugriff auf Freigaben/Internet im Skript: {x}" for x in hits[:10]]
    return []


def _dup_guard(p, pid, user, data):
    """Duplikatsperre (2.27): gleiche Software (winget-ID, MSI-Produktcode, SHA-256 der Installationsdatei) in einem
    anderen Bereich – zentral oder ein anderer Mandant – wird abgewiesen; Hauptadmins dürfen übersteuern."""
    try:
        return repofiles.guard_package(p, pid, user, allow=bool(data.get("allow_duplicate")))
    except repofiles.RepoError as e:
        _rerr(user, e)


@app.post("/api/packages/export")
def api_packages_export(data: dict = Body(...), user=U):
    """Pakete als Datei (*.pkpackages.json) – der Browser speichert sie. Verschachtelte Pakete werden mitgenommen."""
    out, warnings, extra = pkgexport.export(user, data.get("ids") or [])
    pklog.info("deploy", f"{len(out['packages'])} Paket(e) exportiert.", user=user["username"])
    return {"filename": pkgexport.filename(), "data": out, "warnings": warnings, "extra": extra}


@app.post("/api/packages/import")
def api_packages_import(data: dict = Body(...), user=U):
    """Paket-Datei prüfen (dry_run, Standard) oder importieren. Dieselben Regeln wie beim Speichern im Editor."""
    dry = data.get("dry_run", True) is not False
    res = pkgexport.run_import(user, data.get("data"), data.get("tenant_id"), data.get("conflict") or "skip",
                               dry_run=dry, pkg_values=_pkg_values, allow_dup=bool(data.get("allow_duplicate")))
    if not dry:
        for x in res["items"]:
            if x.get("id"):
                integrity.after_save(x["id"])
        # Fingerabdrücke/MSI-Codes wurden in der Transaktion nicht von der Freigabe gelesen – jetzt nachtragen
        import threading as _th
        _th.Thread(target=repofiles.backfill, name="pk-idents", daemon=True).start()
        c = res["count"]
        pklog.info("deploy", f"Paket-Import: {c['new'] + c['copy']} angelegt, {c['skip']} übersprungen, {c['error']} mit Fehler.",
                   user=user["username"])
    return res


@app.post("/api/packages/{pid}/duplicate")
def api_package_duplicate(pid: int, user=U):
    r = access.need_package(user, pid)
    cols = deploy.PKG_FIELDS + ["created_at", "updated_at"]
    vals = [r[k] for k in deploy.PKG_FIELDS]
    vals[deploy.PKG_FIELDS.index("name")] = r["name"] + " (Kopie)"
    if access.scoped(user):
        # OU-Admin: die Kopie gehört seinem Mandanten (z. B. ein zentrales Paket als Vorlage) – Dateien nur aus dessen Ordner
        tid = access.own_tenant_for(user, r.get("tenant_id") if r.get("tenant_id") in
                                    [t["id"] for t in access.build_tenants(user)] else None)
        data = {**deploy.row_to_package(r), "tenant_id": tid, "name": r["name"] + " (Kopie)"}
        p = _pkg_values(data, user=user)
        vals = [p[k] for k in deploy.PKG_FIELDS]
    ids, pending = _dup_guard(dict(zip(deploy.PKG_FIELDS, vals)), None, user, {})
    nid = db.ex(f"INSERT INTO packages({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})", tuple(vals) + (util.now(), util.now()))
    db.ex("UPDATE packages SET ou_edit=? WHERE id=?", (_ou_edit_flag(user, {}, r), nid))
    repofiles.store_idents(nid, ids, pending)
    integrity.after_save(nid)
    return {"id": nid}


@app.post("/api/packages/merge")
def api_packages_merge(data: dict = Body(...), user=U):
    """2.32: Mehrere Pakete zu einem neuen zusammenfassen – verschachtelt (bleibt mit den Originalen verbunden, deren
    Bedingungen, „Ausführen als“ und Neustart gelten weiter) oder als Kopie der Schritte (unabhängig)."""
    ids = []
    if not isinstance(data.get("ids"), list):
        _err("Bitte Pakete auswählen.")
    for x in data["ids"]:
        try:
            x = int(x)
        except (TypeError, ValueError):
            _err("Ungültige Paket-ID.")
        if x not in ids:
            ids.append(x)
    if len(ids) < 2:
        _err("Mindestens zwei Pakete auswählen.")
    if len(ids) > 50:
        _err("Höchstens 50 Pakete auf einmal.")
    rows = [access.need_package(user, i) for i in ids]
    mode = "copy" if data.get("mode") == "copy" else "nested"
    tenants = {int(r["tenant_id"]) for r in rows if r.get("tenant_id")}
    if len(tenants) > 1:
        _err("Pakete verschiedener Mandanten lassen sich nicht zusammenfassen – zentrale Pakete gehen mit jedem Mandanten.")
    tid = next(iter(tenants), None)
    if access.scoped(user):
        tid = access.own_tenant_for(user, tid if tid in [t["id"] for t in access.build_tenants(user)] else None)
    pkgs = [steps.normalize(deploy.row_to_package(r)) for r in rows]
    on_error = "continue" if data.get("continue_on_error") else "stop"
    nid = lambda: secrets.token_hex(4)  # noqa: E731
    if mode == "nested":
        main = [{"id": nid(), "type": "nested", "package_id": r["id"], "title": "", "enabled": True, "run_as": "inherit",
                 "on_error": on_error, "timeout_min": 0, "conditions": steps.default_conditions()} for r in rows]
        st = {"pre": [], "main": main, "post": []}
    else:
        st = {"pre": [], "main": [], "post": []}
        for r, p in zip(rows, pkgs):
            if steps.client_checks(steps.clean_conditions(p.get("conditions") or steps.default_conditions())) or \
                    (p.get("conditions") or {}).get("collections"):
                _err(f"Paket „{r['name']}“ hat eigene Bedingungen – die gingen beim Kopieren der Schritte verloren. "
                     "Bitte „verschachtelt“ wählen.")
            for ph in ("pre", "main", "post"):
                for s in p["steps"].get(ph) or []:
                    s = json.loads(json.dumps(s))
                    s["id"] = nid()
                    if s.get("run_as") in (None, "", "inherit") and p.get("run_as") and s.get("type") in (
                            "install", "command", "powershell", "filecopy", "registry", "uninstall"):
                        s["run_as"] = p["run_as"]  # wie im Original ausführen, auch wenn das neue Paket anders eingestellt ist
                    if ph == "main" and on_error == "continue":
                        s["on_error"] = "continue"
                    st[ph].append(s)
    name = str(data.get("name") or "").strip() or " + ".join(r["name"] for r in rows)[:200]
    new = {"name": name[:200], "version": "", "description": str(data.get("description") or "").strip()[:2000]
           or "Zusammengefasst aus: " + ", ".join(r["name"] for r in rows),
           "run_as": "deploy" if not access.scoped(user) else "system", "tenant_id": tid, "steps": st,
           "conditions": steps.default_conditions(),
           # wie bei Zeitplänen mit mehreren Paketen: die stärkste Neustart-Einstellung gilt für das ganze Paket
           "reboot": deploy.strongest_reboot(p.get("reboot") for p in pkgs),
           "rescan": 1 if any(p.get("rescan") for p in pkgs) else 0,
           "timeout_min": max(int(p.get("timeout_min") or 0) for p in pkgs) or db.setting("deploy_timeout_minutes"),
           "notify_user": "", "rescan_profile_id": None,
           "allow_duplicate": bool(data.get("allow_duplicate"))}  # 2.32.5: wie beim Speichern (nur Hauptadmins)
    # Danach scannen: wie die Einzelpakete – ein gemeinsames Profil direkt, verschiedene als Schritte „Scannen“ am Ende
    prof = []
    for p in pkgs:
        x = deploy.rescan_profiles({**p, "composite": False})
        if x and x[0] not in prof:
            prof.append(x[0])
    apps = profiles.apps_profile_id()
    new["rescan"] = 1 if prof else 0
    new["rescan_profile_id"] = prof[0] if len(prof) == 1 and prof[0] != apps else None
    if len(prof) > 1:
        for x in prof:
            if x != apps:
                st["post"].append({"id": nid(), "type": "scan", "profile_id": x, "title": "", "enabled": True, "run_as": "inherit",
                                   "on_error": "continue", "timeout_min": 0, "conditions": steps.default_conditions()})
    res = api_package_create(new, user=user)
    pklog.info("deploy", f"Paket '{name}' aus {len(rows)} Paketen zusammengefasst ({'verschachtelt' if mode == 'nested' else 'Kopie der Schritte'}).",
               user=user["username"])
    return res


@app.delete("/api/packages/{pid}")
def api_package_delete(pid: int, user=U):
    users = [r["name"] for r in db.q("SELECT id, name, steps FROM packages WHERE steps LIKE '%nested%'")
             if any(s.get("type") == "nested" and s.get("package_id") == pid
                    for _k, s in steps.iter_steps(steps.normalize(deploy.row_to_package(r))))]
    if users:
        _err("Das Paket wird als verschachteltes Paket verwendet in: " + ", ".join(users))
    used = [r["name"] for r in db.q("SELECT name, packages FROM schedules") if pid in json.loads(r["packages"] or "[]")]
    if used:
        _err("Das Paket wird in Zeitplänen verwendet: " + ", ".join(used))
    db.ex("DELETE FROM packages WHERE id=?", (pid,))
    repofiles.forget_idents(pid)
    return {"ok": True}


@app.post("/api/repository/test")
def api_repository_test(user=U):
    try:
        res = remote.test_repository()
    except Exception as e:
        _err(e)
    lvl = pklog.info if res["ok"] else pklog.warn
    lvl("system", "Repository-Test " + ("erfolgreich" if res["ok"] else "fehlgeschlagen") + ": "
        + "; ".join(f"{s['step']}: {s['detail']}" for s in res["steps"]), user=user["username"])
    return res


@app.post("/api/packages/preview")
def api_package_preview(data: dict = Body(...), user=U):
    """Was ein Schritt auf dem Client tatsächlich ausführt."""
    pkg = {"run_as": data.get("run_as") or "deploy", "version": data.get("version") or ""}
    try:
        st = steps.clean_step(data.get("step") or {})
    except ValueError as e:
        return {"command": "", "error": str(e), "notes": [], "run_as_label": ""}
    return steps.preview(st, pkg)


def _repo_scope(user, path):
    """OU-Admin: nur innerhalb der Repository-Ordner seiner Mandanten (mit „eigene Pakete erlaubt“). Leerer Pfad →
    der (erste) eigene Ordner."""
    if not access.scoped(user):
        return path
    roots = [(t.get("repo_folder") or "").replace("/", "\\").strip("\\") for t in access.build_tenants(user)]
    roots = [r for r in roots if r]
    rel = (path or "").replace("/", "\\").strip("\\")
    if not rel:
        return roots[0] if roots else ""
    parts = [x for x in rel.split("\\") if x not in ("", ".")]
    if ".." in parts:
        raise HTTPException(403, "Ungültiger Pfad.")
    norm = "\\".join(parts).lower()
    if not any(norm == r.lower() or norm.startswith(r.lower() + "\\") for r in roots):
        raise HTTPException(403, "Nur der Repository-Ordner Ihres Mandanten ist freigegeben.")
    return "\\".join(parts)


@app.get("/api/repository/msi")
def api_repository_msi(path: str, user=U):
    """MSI-Eigenschaften (ProductName, Version, Hersteller, Produktcode, Plattform) aus dem Repository lesen."""
    from . import msi
    path = _repo_scope(user, path)
    if not path.lower().endswith((".msi", ".msp")):
        _err("Keine MSI-Datei")
    try:
        with remote.repo_open_rel(path) as fh:
            return msi.read(fh)
    except msi.MsiError as e:
        _err(e)
    except Exception as e:
        _err(f"MSI konnte nicht gelesen werden: {e}")


@app.get("/api/repository/search")
def api_repository_search(q: str = "", path: str = "", refresh: int = 0, user=U):
    """Im Repository nach Datei- oder Ordnernamen suchen (ab path, alle Unterordner). OU-Admins ohne Pfad: in allen
    Repository-Ordnern ihrer Mandanten. Gesucht wird im Index im Arbeitsspeicher (remote.repo_search);
    refresh=1 liest ihn neu ein (nur Hauptadmins – das Einlesen betrifft das ganze Repository)."""
    if access.scoped(user) and not (path or "").strip():
        roots = [(t.get("repo_folder") or "").strip() for t in access.build_tenants(user) if (t.get("repo_folder") or "").strip()]
    else:
        roots = [path]
    out = {"items": [], "truncated": False, "cached": True, "age": 0}
    fresh = bool(refresh) and not access.scoped(user)
    limit = 200
    try:
        for i, r in enumerate(roots):
            res = remote.repo_search(_repo_scope(user, r), q, limit=limit, refresh=fresh and i == 0)
            out["items"] += res["items"]
            out["truncated"] = out["truncated"] or res["truncated"]
            out["cached"] = out["cached"] and bool(res.get("cached"))
            out["age"] = max(out["age"], int(res.get("age") or 0))
    except Exception as e:
        _err(_repo_err(user, e))
    # Mehrere Mandanten-Ordner (OU-Admin): gemeinsam sortieren, doppelte entfernen, eine Grenze für alle
    seen, items = set(), []
    for it in sorted(out["items"], key=lambda x: (not x["dir"], (x["folder"] + "\\" + x["name"]).lower())):
        k = (it["folder"].lower(), it["name"].lower())
        if k not in seen:
            seen.add(k)
            items.append(it)
    if len(items) > limit:
        items, out["truncated"] = items[:limit], True
    out["items"] = items
    if not out["cached"]:
        out["age"] = None
    out["index"] = remote.repo_cache_status(full=not access.scoped(user))
    return out


def _repo_err(user, e):
    """Fehlermeldung des Repositorys – für OU-Admins ohne Pfad, Server und Konto der Freigabe."""
    msg = str(e)
    if not access.scoped(user):
        return msg
    root = str(db.setting("repo_path") or "").strip()
    secrets = []
    if root:
        for v in (root, root.replace("/", "\\"), root.replace("\\", "/")):
            secrets += [v, v.rstrip("\\/")]
        try:
            secrets.append(remote._repo_server(root))
        except Exception:
            pass
    try:
        login = config.repo_login() or ""
        if login:
            secrets += [login, login.split("\\")[-1].split("@")[0]]
    except Exception:
        pass
    for v in sorted({x for x in secrets if x and len(x) >= 3}, key=len, reverse=True):
        msg = re.sub(re.escape(v), "…", msg, flags=re.I)
    return msg


@app.get("/api/repository")
def api_repository(path: str = "", used: int = 0, user=U):
    path = _repo_scope(user, path)
    try:
        out = {"path": path, "root": "" if access.scoped(user) else db.setting("repo_path"), "items": remote.repo_list(path)}
    except Exception as e:
        _err(_repo_err(user, e))
    if used:
        # Repository-Ansicht (2.27): welche Einträge Pakete benutzen
        u = repofiles.used_map(path)
        for it in out["items"]:
            it["used_by"] = u.get(it["name"].lower()) or []
    return out


@app.get("/api/repository/info")
def api_repository_info(path: str = "", user=U):
    """Grenzen für den Upload und Hinweise für die Repository-Ansicht."""
    scoped = access.scoped(user)
    roots = repofiles.roots(user)
    out = {"max_mb": db.setting("repo_upload_max_mb"), "chunk": repofiles.CHUNK, "scoped": bool(scoped),
           "types": db.setting("repo_upload_types") if scoped else "", "roots": roots, "tenants": []}
    if scoped:
        for t in access.build_tenants(user):
            f = repofiles.norm(t.get("repo_folder"))
            if not f:
                continue
            q = repofiles.quota_bytes(t["id"])
            used = None
            try:
                used = repofiles.used_bytes(f) if q else None
            except Exception:
                pass
            out["tenants"].append({"name": t["name"], "folder": f, "quota_mb": q // 1048576 if q else 0,
                                   "used_mb": round(used / 1048576, 1) if used is not None else None})
    else:
        out["repo_path"] = db.setting("repo_path")
    return out


@app.get("/api/repository/usage")
def api_repository_usage(path: str, user=U):
    path = _repo_scope(user, path)
    hits = repofiles.usage(path)
    return {"path": path, "used_by": sorted({repofiles._owner_text(h[4]) for h in hits})}


def _repo_call(user, fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except (repofiles.RepoError, remote.RemoteError, OSError) as e:
        _rerr(user, e)
    except Exception as e:  # smbprotocol-Fehler sind keine OSError
        if type(e).__module__.startswith(("smbprotocol", "smbclient")):
            _rerr(user, remote.RemoteError(f"Repository: {e}"))
        raise


@app.post("/api/repository/mkdir")
def api_repository_mkdir(data: dict = Body(...), user=U):
    if data.get("dirs") is not None:
        return _repo_call(user, repofiles.mkdirs, user, data.get("folder") or "", list(data.get("dirs") or [])[:5000])
    return _repo_call(user, repofiles.mkdir, user, data.get("folder") or "", data.get("name") or "")


@app.post("/api/repository/rename")
def api_repository_rename(data: dict = Body(...), user=U):
    return _repo_call(user, repofiles.rename, user, data.get("path") or "", data.get("name") or "",
                      dry_run=bool(data.get("dry_run")))


@app.post("/api/repository/move")
def api_repository_move(data: dict = Body(...), user=U):
    """Verschieben in einen Ordner (auch der Assistent „Repository umstrukturieren“): dry_run = Vorschau mit den
    betroffenen Paketen; Ausführen mit Sicherung vorher und Zurückdrehen bei einem Fehler."""
    items = [str(x) for x in (data.get("items") or [])][:2000]
    return _repo_call(user, repofiles.move_into, user, items, data.get("dest") or "", dry_run=data.get("dry_run", True) is not False,
                      create_dest=bool(data.get("create_dest")))


@app.post("/api/repository/delete")
def api_repository_delete(data: dict = Body(...), user=U):
    items = [str(x) for x in (data.get("items") or [])][:2000]
    return _repo_call(user, repofiles.delete, user, items)


@app.post("/api/repository/upload")
def api_repository_upload_start(data: dict = Body(...), user=U):
    return _repo_call(user, repofiles.upload_start, user, data.get("folder") or "", data.get("name") or "", data.get("size"),
                      overwrite=bool(data.get("overwrite")), allow_dup=bool(data.get("allow_duplicate")))


@app.put("/api/repository/upload/{token}")
async def api_repository_upload_chunk(token: str, request: Request, offset: int = 0, user=U):
    from starlette.concurrency import run_in_threadpool
    body = await request.body()
    if len(body) > repofiles.CHUNK:
        _err("Stück zu groß.")
    return await run_in_threadpool(_repo_call, user, repofiles.upload_chunk, user, token, offset, body)


@app.post("/api/repository/upload/{token}/finish")
def api_repository_upload_finish(token: str, data: dict = Body(default={}), user=U):
    return _repo_call(user, repofiles.upload_finish, user, token, allow_dup=bool((data or {}).get("allow_duplicate")))


@app.delete("/api/repository/upload/{token}")
def api_repository_upload_cancel(token: str, user=U):
    return _repo_call(user, repofiles.upload_cancel, user, token)


@app.post("/api/packages/{pid}/repair/suggest")
def api_package_repair_suggest(pid: int, user=U):
    """Reparatur per Fingerabdruck: fehlende Dateien/Ordner des Pakets im Repository wiederfinden."""
    r = access.need_package(user, pid)
    if not access.can_edit_package(user, r):
        raise HTTPException(403, "Nur wer das Paket ändern darf, kann es reparieren.")
    return _repo_call(user, repofiles.repair_suggest, user, deploy.row_to_package(r))


@app.post("/api/packages/{pid}/repair/apply")
def api_package_repair_apply(pid: int, data: dict = Body(...), user=U):
    r = access.need_package(user, pid)
    if not access.can_edit_package(user, r):
        raise HTTPException(403, "Nur wer das Paket ändern darf, kann es reparieren.")
    pairs = [(str(x.get("old") or ""), str(x.get("new") or "")) for x in (data.get("items") or []) if isinstance(x, dict)][:200]
    return _repo_call(user, repofiles.repair_apply, user, pairs)


# --- Bereitstellungen ---------------------------------------------------------------------
@app.get("/api/deployments")
def api_deployments(limit: int = 300, user=U):
    if access.scoped(user):
        # OU-Admin: nur Bereitstellungen mit mindestens einem seiner Computer; Zahlen und Namen nur seiner Ziele
        mine = json.dumps(sorted(access.computer_ids(user)))
        return db.q("""SELECT d.id, d.name, d.source, d.status, d.created_by, d.created_at, d.scheduled_at, d.started_at,
                       d.finished_at, NULL target_label,
                       (SELECT group_concat(computer_name, ', ') FROM (SELECT computer_name FROM deployment_targets t
                          WHERE t.deployment_id=d.id AND t.computer_id IN (SELECT value FROM json_each(?1))
                          ORDER BY computer_name LIMIT 4)) target_names,
                       (SELECT COUNT(*) FROM deployment_targets t WHERE t.deployment_id=d.id
                          AND t.computer_id IN (SELECT value FROM json_each(?1))) total,
                       (SELECT COUNT(*) FROM deployment_targets t WHERE t.deployment_id=d.id AND t.status='success'
                          AND t.computer_id IN (SELECT value FROM json_each(?1))) ok,
                       (SELECT COUNT(*) FROM deployment_targets t WHERE t.deployment_id=d.id AND t.status='failed'
                          AND t.computer_id IN (SELECT value FROM json_each(?1))) failed
                       FROM deployments d WHERE EXISTS (SELECT 1 FROM deployment_targets t WHERE t.deployment_id=d.id
                          AND t.computer_id IN (SELECT value FROM json_each(?1)))
                       ORDER BY d.id DESC LIMIT ?2""", (mine, max(1, min(limit, 2000))))
    return db.q("""SELECT d.id, d.name, d.source, d.status, d.created_by, d.created_at, d.scheduled_at, d.started_at,
                   d.finished_at, d.target_label,
                   (SELECT group_concat(computer_name, ', ') FROM (SELECT computer_name FROM deployment_targets t
                      WHERE t.deployment_id=d.id ORDER BY computer_name LIMIT 4)) target_names,
                   (SELECT COUNT(*) FROM deployment_targets t WHERE t.deployment_id=d.id) total,
                   (SELECT COUNT(*) FROM deployment_targets t WHERE t.deployment_id=d.id AND t.status='success') ok,
                   (SELECT COUNT(*) FROM deployment_targets t WHERE t.deployment_id=d.id AND t.status='failed') failed
                   FROM deployments d ORDER BY d.id DESC LIMIT ?""", (max(1, min(limit, 2000)),))


@app.post("/api/deployments")
def api_deploy(data: dict = Body(...), user=U):
    ids = access.check_ids(user, data.get("computer_ids"))
    label = None
    if data.get("collection_id"):
        ids += access.limit_ids(user, collections_.member_ids(int(data["collection_id"]), fresh=True))
        cname = db.val("SELECT name FROM collections WHERE id=?", (int(data["collection_id"]),))
        label = f"Sammlung: {cname or 'Alle Computer'}"
    sched = (data.get("scheduled_at") or "").replace("T", " ")
    if sched and len(sched) == 16:
        sched += ":00"
    try:
        pids = [int(x) for x in data.get("package_ids") or []]
        for x in pids + ([int(data["package_id"])] if data.get("package_id") else []):
            access.need_package(user, x)  # OU-Admin: nur zentrale und eigene Pakete
        if access.scoped(user) and not ids:
            _err("Keine Computer aus Ihrem Bereich ausgewählt.")
        if len(pids) > 1:
            # Mehrere Pakete: eine Bereitstellung, je Computer nacheinander
            pkg = deploy.composite(pids, stop_on_fail=bool(data.get("stop_on_fail", True)))
            from . import steps as _steps
            _ids, ignored = deploy.tenant_filter(_steps.expand(pkg), sorted(set(ids)))
            did = deploy.create(pkg, ids, user["username"], sched or None, target_label=label)
            return {"id": did, "ignored": ignored}
        if len(pids) == 1:
            data["package_id"] = pids[0]
        if data.get("package_id"):
            row = db.q1("SELECT * FROM packages WHERE id=?", (int(data["package_id"]),))
            if not row:
                _err("Paket nicht gefunden")
            pkg = deploy.row_to_package(row)
            from . import steps as _steps
            _ids, ignored = deploy.tenant_filter(_steps.expand(pkg), sorted(set(ids)))
            did = deploy.create(pkg, ids, user["username"], sched or None, package_id=row["id"], target_label=label)
            return {"id": did, "ignored": ignored}
        elif data.get("winget_bootstrap"):
            did = deploy.create(deploy.adhoc_bootstrap(), ids, user["username"], sched or None, target_label=label)
        elif data.get("winget_id"):
            pkg = deploy.adhoc_winget(data["winget_id"].strip(), data.get("action") or "upgrade")
            did = deploy.create(pkg, ids, user["username"], sched or None, target_label=label)
        else:
            _err("Paket oder winget-ID angeben")
    except ValueError as e:
        _err(_dmsg(user, e))
    return {"id": did}


@app.get("/api/deployments/{did}")
def api_deployment(did: int, user=U):
    d = db.q1("SELECT * FROM deployments WHERE id=?", (did,))
    if not d:
        raise HTTPException(404)
    d["package"] = json.loads(d.pop("package_json") or "{}")
    d["targets"] = db.q("SELECT * FROM deployment_targets WHERE deployment_id=? ORDER BY computer_name", (did,))
    mine = access.computer_ids(user)
    if mine is not None:
        full = all(t["computer_id"] in mine for t in d["targets"])
        d["targets"] = [t for t in d["targets"] if t["computer_id"] in mine]  # OU-Admin: nur seine Ziele
        d["target_label"] = None
        if not full:
            d["package"] = {"name": d["package"].get("name"), "steps": {}}  # Inhalt eines fremden Pakets nicht zeigen
    return d


@app.post("/api/deployments/{did}/cancel")
def api_deployment_cancel(did: int, user=U):
    deploy.cancel(did, user["username"])
    return {"ok": True}


@app.post("/api/deployments/{did}/redeploy")
def api_deployment_redeploy(did: int, data: dict = Body(default={}), user=U):
    try:
        return {"id": deploy.redeploy(did, bool(data.get("failed_only")), user["username"])}
    except ValueError as e:
        _err(_dmsg(user, e))


@app.get("/api/deployments/{did}/failed_steps")
def api_deployment_failed_steps(did: int, user=U):
    try:
        res = deploy.failed_steps(did)
    except ValueError as e:
        _err(e)
    mine = access.computer_ids(user)
    if mine is not None:
        for e in res:
            e["targets"] = [t for t in e["targets"] if t["computer_id"] in mine]
        res = [e for e in res if e["targets"]]
    return res


@app.get("/api/deployments/{did}/steps")
def api_deployment_steps(did: int, user=U):
    """Schritte der Bereitstellung mit dem Ergebnis je Ziel – für „Schritte wählen …“."""
    try:
        return deploy.steps_overview(did, access.computer_ids(user))
    except ValueError as e:
        _err(e)


@app.post("/api/deployments/{did}/rerun")
def api_deployment_rerun(did: int, data: dict = Body(...), user=U):
    """Mit frei gewählten Schritten wiederholen (alle, fehlgeschlagene oder ausgewählte Ziele)."""
    try:
        return {"id": deploy.rerun_steps(did, data.get("step_ids") or [], data.get("target") or "failed",
                                         data.get("target_ids") or [], user["username"], access.computer_ids(user),
                                         reboot="never" if data.get("reboot") == "never" else None)}
    except ValueError as e:
        _err(_dmsg(user, e))


@app.post("/api/deployments/{did}/retry")
def api_deployment_retry(did: int, data: dict = Body(...), user=U):
    try:
        return {"id": deploy.retry_step(did, str(data.get("step_id") or ""), data.get("mode"), data.get("target_ids") or [],
                                        user["username"])}
    except ValueError as e:
        _err(_dmsg(user, e))


@app.delete("/api/deployments/{did}")
def api_deployment_delete(did: int, user=U):
    if db.val("SELECT status FROM deployments WHERE id=?", (did,)) in ("running", "queued") or \
            db.val("SELECT COUNT(*) FROM deployment_targets WHERE deployment_id=? AND status='running'", (did,), 0):
        _err("Die Bereitstellung läuft noch auf mindestens einem Computer – bitte warten, bis der Abbruch abgeschlossen ist.")
    db.ex("DELETE FROM deployments WHERE id=?", (did,))
    return {"ok": True}


# --- Zeitpläne ----------------------------------------------------------------
@app.get("/api/schedules")
def api_schedules(user=U):
    out = []
    mine = access.computer_ids(user)
    vis = (_visible_collections(user) or set()) if mine is not None else None
    for s in [x for x in schedules.all_schedules() if access.can_see_schedule(user, x)]:
        s.update(schedules.summary(s, mine))
        _sched_view(user, s, mine, vis)
        out.append(s)
    return out


def _sched_view(user, s, mine, vis=None):
    """2.30: editable/runnable für die Oberfläche; OU-Admin sieht bei zentralen Zeitplänen nur seine Ziele.
    vis: sichtbare Sammlungen, einmal je Anfrage berechnet (2.32.5 – in der Liste nicht je Zeitplan neu)."""
    s["editable"] = access.can_edit_schedule(user, s)
    s["runnable"] = not access.scoped(user) or bool(s.get("tenant_id"))
    s["ou_edit"] = 1 if s.get("ou_edit") else 0
    if mine is not None and not s.get("tenant_id"):
        if vis is None:
            vis = _visible_collections(user) or set()
        s["targets"] = {"computers": [i for i in s["targets"]["computers"] if i in mine],
                        "collections": [c for c in s["targets"]["collections"] if c in vis or c == profiles.ALL_COMPUTERS]}
    return s


@app.get("/api/schedules/{sid}")
def api_schedule(sid: int, user=U):
    s = schedules.get(sid)
    if not s:
        raise HTTPException(404, "Zeitplan nicht gefunden")
    mine = access.computer_ids(user)
    s.update(schedules.summary(s, mine))
    _sched_view(user, s, mine)
    s["history"] = schedules.history(sid)
    if mine is not None:
        s["history"] = [h for h in s["history"] if h["id"] in mine]
    s["deployments"] = db.q("SELECT id, status, created_at, finished_at FROM deployments WHERE schedule_id=? "
                            "ORDER BY id DESC LIMIT 50", (sid,))
    if mine is not None:  # nur Bereitstellungen mit eigenen Computern
        s["deployments"] = [d for d in s["deployments"] if access.deployment_targets_in_scope(user, d["id"])[1]]
    return s


def _sched_scope(user, data):
    """OU-Admin: Zeitplan gehört immer einem seiner Mandanten; Pakete nur zentrale/eigene; Computer nur eigene."""
    if not access.scoped(user):
        return data
    data = {**data, "tenant_id": access.own_tenant_for(user, data.get("tenant_id"))}
    for x in data.get("packages") or []:
        access.need_package(user, x)
    tg = data.get("targets") or {}
    access.check_ids(user, tg.get("computers"))
    own_cols = {c for t in access.tenants(user) for c in access.tenant_cols(t)}
    tids = access.tenant_ids(user)
    own_cols |= {r["id"] for r in db.q("SELECT id, tenant_id FROM collections WHERE tenant_id IS NOT NULL") if r["tenant_id"] in tids}
    for c in tg.get("collections") or []:
        try:
            c = int(c)
        except (TypeError, ValueError):
            c = -1
        if c not in own_cols:
            raise HTTPException(403, "Als Sammlung sind nur die Ihres Mandanten und Ihre eigenen möglich – einzelne Computer wählen Sie unter „Computer“.")
    return data


@app.post("/api/schedules")
def api_schedule_create(data: dict = Body(...), user=U):
    data = _sched_scope(user, data)
    try:
        sid = schedules.save(None, data, user["username"])
    except ValueError as e:
        _err(e)
    db.ex("UPDATE schedules SET ou_edit=? WHERE id=?", (_ou_edit_flag(user, data), sid))
    pklog.info("deploy", f"Zeitplan '{data.get('name')}' angelegt.", user=user["username"])
    return {"id": sid}


@app.put("/api/schedules/{sid}")
def api_schedule_update(sid: int, data: dict = Body(...), user=U):
    old = schedules.get(sid)
    if not old:
        raise HTTPException(404)
    data = _sched_scope(user, data)
    try:
        schedules.save(sid, data, user["username"])
    except ValueError as e:
        _err(e)
    db.ex("UPDATE schedules SET ou_edit=? WHERE id=?", (_ou_edit_flag(user, data, old), sid))
    pklog.info("deploy", f"Zeitplan '{data.get('name')}' geändert.", user=user["username"])
    return {"ok": True}


@app.delete("/api/schedules/{sid}")
def api_schedule_delete(sid: int, user=U):
    db.ex("DELETE FROM schedules WHERE id=?", (sid,))
    pklog.info("deploy", f"Zeitplan #{sid} gelöscht.", user=user["username"])
    return {"ok": True}


@app.post("/api/schedules/{sid}/run")
def api_schedule_run(sid: int, data: dict = Body(default={}), user=U):
    s = schedules.get(sid)
    if not s:
        raise HTTPException(404)
    try:
        return schedules.fire(s, access.check_ids(user, data.get("ids")) or None, reason="manuell", user=user["username"],
                              force=bool(data.get("force")))
    except ValueError as e:
        _err(e)


@app.get("/api/schedules/{sid}/preview")
def api_schedule_preview(sid: int, user=U):
    s = schedules.get(sid)
    if not s:
        raise HTTPException(404)
    return schedules.preview(s, access.computer_ids(user))


@app.post("/api/schedules/{sid}/duplicate")
def api_schedule_dup(sid: int, user=U):
    s = schedules.get(sid)
    if not s:
        raise HTTPException(404)
    s["name"] += " (Kopie)"
    s["enabled"] = 0
    for t in s["triggers"]:
        t.pop("id", None)
    if access.scoped(user):
        # 2.30: OU-Admin kopiert als Vorlage – die Kopie gehört seinem Mandanten, nur mit seinen Paketen und Computern
        if not access.may_build(user):
            raise HTTPException(403, "Eigene Pakete und Zeitpläne sind für Ihren Mandanten nicht freigegeben.")
        mine = access.computer_ids(user)
        _sched_view(user, s, mine)
        s["tenant_id"] = access.own_tenant_for(user, s.get("tenant_id") if s.get("tenant_id") in
                                               [t["id"] for t in access.build_tenants(user)] else None)
        s["packages"] = [p for p in s["packages"] if (r := db.q1("SELECT * FROM packages WHERE id=?", (p,))) and access.can_see_package(user, r)]
        s["targets"]["collections"] = [c for c in s["targets"]["collections"] if c != profiles.ALL_COMPUTERS]
        try:
            s = _sched_scope(user, s)
        except HTTPException:
            s["targets"]["collections"] = []
        if not s["targets"]["computers"] and not s["targets"]["collections"]:
            t = db.q1("SELECT collection_id, collection_ids FROM tenants WHERE id=?", (s["tenant_id"],))
            s["targets"]["collections"] = access.tenant_cols(t)[:1] if t else []
    try:
        nid = schedules.save(None, s, user["username"])
    except ValueError as e:
        _err(e)
    db.ex("UPDATE schedules SET ou_edit=? WHERE id=?", (_ou_edit_flag(user, {}, s), nid))
    return {"id": nid}


@app.post("/api/schedules/{sid}/reset")
def api_schedule_reset(sid: int, data: dict = Body(default={}), user=U):
    ids = access.check_ids(user, data.get("ids"))
    if access.scoped(user) and not ids:
        ids = sorted(access.computer_ids(user))  # OU-Admin: „alle“ = alle seine Computer
        if not ids:
            return {"ok": True}
    if ids:
        db.ex(f"DELETE FROM schedule_targets WHERE schedule_id=? AND computer_id IN ({','.join('?' * len(ids))})", (sid, *ids))
    else:
        db.ex("DELETE FROM schedule_targets WHERE schedule_id=?", (sid,))
    return {"ok": True}


# --- Mandanten -------------------------------------------------------------------------------
def _tenant_cols_in(data):
    """2.29.9: Sammlungen des Mandanten aus der Anfrage – collection_ids (Liste) oder wie bisher collection_id."""
    raw = data.get("collection_ids")
    if raw is None:
        raw = [data.get("collection_id")]
    if not isinstance(raw, list):
        _err("Sammlungen als Liste angeben.")
    cols = []
    for c in raw:
        if c in (None, "", 0, "0"):
            continue
        try:
            c = int(c)
        except (TypeError, ValueError):
            _err("Ungültige Sammlung.")
        if not db.val("SELECT 1 FROM collections WHERE id=? AND type != 'folder'", (c,)):
            _err("Sammlung nicht gefunden.")
        if db.val("SELECT tenant_id FROM collections WHERE id=?", (c,)):
            _err("Diese Sammlung hat ein OU-Admin angelegt – als Bereich eines Mandanten bitte eine Sammlung des Hauptadmins wählen.")
        if c not in cols:
            cols.append(c)
    if len(cols) > 50:
        _err("Höchstens 50 Sammlungen je Mandant.")
    return cols  # leer = keine Einschränkung (nur ohne OU-Admins zulässig)


def _tenant_values(data, tid=None):
    """→ (name, description, collection_id, repo_folder, admin_group, allow_packages, admin_group_dn, admin_group_sid)"""
    name = (data.get("name") or "").strip()
    if not name:
        _err("Name fehlt.")
    cols = _tenant_cols_in(data)
    col = cols[0] if cols else None
    folder = (data.get("repo_folder") or "").strip().strip("\\/")
    folder = "\\".join(x for x in folder.replace("/", "\\").split("\\") if x not in ("", "."))
    if ".." in folder.split("\\"):
        _err("Ungültiger Repository-Ordner.")
    try:
        repofiles.check_tenant_folder(folder, tid)
    except repofiles.RepoError as e:
        _err(e)
    group = re.sub(r"[\x00-\x1f]", "", str(data.get("admin_group") or "")).strip()[:400]
    had_ou = bool(tid and db.val("SELECT COALESCE(ou_ever, 0) OR COALESCE(admin_group, '') != '' FROM tenants WHERE id=?", (tid,)))
    if (group or had_ou) and (not cols or profiles.ALL_COMPUTERS in cols):
        _err("Für OU-Admins bitte eine Sammlung wählen (z. B. die OU) – sonst wüssten sie nicht, welche Computer sie betreuen."
             + (" Dieser Mandant hatte bereits OU-Admins: ohne Sammlung erreichten seine Pakete und Zeitpläne keinen Computer."
                if had_ou and not group else ""))
    dn = sid = None
    old = db.q1("SELECT admin_group, admin_group_dn, admin_group_sid FROM tenants WHERE id=?", (tid,)) if tid else None
    if group and old and (old.get("admin_group") or "") == group and old.get("admin_group_sid"):
        dn, sid = old.get("admin_group_dn"), old["admin_group_sid"]  # Gruppe unverändert: ohne AD speicherbar
    elif group:
        if group.lower() == str(config.ACCESS_GROUP or "").strip().lower():
            _err("Das ist die Gruppe der Hauptadmins – für OU-Admins eine eigene Gruppe wählen.")
        # Die Gruppe wird JETZT eindeutig aufgelöst (DN + SID); bei der Anmeldung zählt nur diese Gruppe
        try:
            c = ad.service_conn()
        except Exception as e:
            _err(f"Das AD ist gerade nicht erreichbar – die Gruppe lässt sich nicht prüfen ({e}). Bitte später erneut speichern.")
        try:
            dn, sid = ad.resolve_group_strict(c, group)
        except ad.ADError as e:
            _err(e)
        finally:
            try:
                c.unbind()
            except Exception:
                pass
        acc = str(config.ACCESS_GROUP or "")
        if acc and "=" in acc and ad.norm_dn(acc) == ad.norm_dn(dn):
            _err("Das ist die Gruppe der Hauptadmins – für OU-Admins eine eigene Gruppe wählen.")
    allow = 1 if (data.get("allow_packages") and group) else 0
    return (name[:120], (data.get("description") or "").strip()[:1000], col, folder[:300], group or None, allow, dn, sid)


@app.get("/api/tenants")
def api_tenants(user=U):
    rows = access.tenants(user) if access.scoped(user) else db.q("SELECT * FROM tenants ORDER BY name COLLATE NOCASE")
    for r in rows:
        r["packages"] = db.val("SELECT COUNT(*) FROM packages WHERE tenant_id=?", (r["id"],), 0)
        r["schedules"] = db.val("SELECT COUNT(*) FROM schedules WHERE tenant_id=?", (r["id"],), 0)
        cols = access.tenant_cols(r)
        r["collection_ids"] = cols
        names = [db.val("SELECT name FROM collections WHERE id=?", (c,)) for c in cols]
        r["collection"] = ", ".join(n for n in names if n) or None
    return rows


@app.post("/api/tenants")
def api_tenant_create(data: dict = Body(...), user=U):
    v = _tenant_values(data)
    tid = db.ex("INSERT INTO tenants(name, description, collection_id, repo_folder, admin_group, allow_packages, admin_group_dn, "
                "admin_group_sid, ou_ever, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (*v, 1 if v[4] else 0, util.now()))
    pklog.info("system", f"Mandant '{v[0]}' angelegt" + (f" (OU-Admins: {v[4]})" if v[4] else "") + ".", user=user["username"])
    _tenant_quota(tid, data)
    _tenant_scripts(tid, data, user)
    db.ex("UPDATE tenants SET collection_ids=? WHERE id=?", (json.dumps(_tenant_cols_in(data)), tid))
    _tenants_changed()
    _tenant_folder_bg(tid)
    return {"id": tid}


def _tenant_quota(tid, data):
    q = data.get("quota_mb")
    if q in (None, ""):
        q = None
    else:
        try:
            q = max(0, min(10 ** 7, int(q)))
        except (TypeError, ValueError):
            _err("Speicherkontingent: bitte eine Zahl in MB (0 = unbegrenzt, leer = Standard).")
    db.ex("UPDATE tenants SET quota_mb=? WHERE id=?", (q, tid))


def _tenant_scripts(tid, data, user, old=None):
    """2.29.1: Skript-Schritte (Befehl, PowerShell, eigener Deinstallationsbefehl) nur mit „eigene Pakete“ – Standard aus."""
    on = 1 if (data.get("allow_scripts") and db.val("SELECT allow_packages FROM tenants WHERE id=?", (tid,))) else 0
    db.ex("UPDATE tenants SET allow_scripts=? WHERE id=?", (on, tid))
    if on != int((old or {}).get("allow_scripts") or 0):
        name = db.val("SELECT name FROM tenants WHERE id=?", (tid,))
        if on:
            pklog.warn("system", f"Mandant '{name}': Skript-Schritte für OU-Admins erlaubt.", user=user["username"])
        else:
            pklog.info("system", f"Mandant '{name}': Skript-Schritte für OU-Admins nicht erlaubt.", user=user["username"])


def _tenant_folder_bg(tid):
    """Mandant darf eigene Pakete bauen: Repository-Ordner zuweisen (sofort) und anlegen (im Hintergrund – SMB)."""
    try:
        folder = repofiles.ensure_tenant_folder(tid, create=False)
    except Exception:
        folder = None
    if folder:
        import threading as _th
        _th.Thread(target=repofiles.ensure_tenant_folder, args=(tid,), name="pk-tenant-folder", daemon=True).start()


def _tenants_changed(tid=None):
    """Mandanten geändert: Bereiche neu berechnen. Geänderte/gelöschte Mandanten: deren OU-Admin-Sitzungen sofort beenden
    (neu anmelden = neu prüfen) – auch, damit eine wiederverwendete Mandanten-ID nie an eine alte Sitzung geht."""
    access.flush()
    auth._checked.clear()
    if tid:
        gone = [r["token_hash"] for r in db.q(
            "SELECT token_hash FROM sessions WHERE scope IS NOT NULL AND EXISTS (SELECT 1 FROM json_each(sessions.scope) j "
            "WHERE j.value = ?)", (int(tid),))]
        for th in gone:
            auth.end_session(th, "Mandant geändert")


@app.put("/api/tenants/{tid}")
def api_tenant_update(tid: int, data: dict = Body(...), user=U):
    old = db.q1("SELECT * FROM tenants WHERE id=?", (tid,))
    if not old:
        raise HTTPException(404)
    v = _tenant_values(data, tid)
    db.ex("UPDATE tenants SET name=?, description=?, collection_id=?, repo_folder=?, admin_group=?, allow_packages=?, "
          "admin_group_dn=?, admin_group_sid=?, ou_ever=MAX(COALESCE(ou_ever, 0), ?) WHERE id=?", (*v, 1 if v[4] else 0, tid))
    pklog.info("system", f"Mandant '{v[0]}' geändert" + (f" (OU-Admins: {v[4]})" if v[4] else "") + ".", user=user["username"])
    _tenant_quota(tid, data)
    _tenant_scripts(tid, data, user, old)
    new_cols = _tenant_cols_in(data)
    db.ex("UPDATE tenants SET collection_ids=? WHERE id=?", (json.dumps(new_cols), tid))
    _tenant_folder_bg(tid)
    changed = (old.get("admin_group_sid") != v[7] or old.get("admin_group_dn") != v[6] or access.tenant_cols(old) != new_cols or int(old.get("allow_packages") or 0) != v[5]
               or (old.get("repo_folder") or "") != v[3])
    _tenants_changed(tid if changed else None)
    return {"ok": True}


@app.delete("/api/tenants/{tid}")
def api_tenant_delete(tid: int, user=U):
    n = db.val("SELECT COUNT(*) FROM packages WHERE tenant_id=?", (tid,), 0)
    if n:
        _err(f"Dem Mandanten sind noch {n} Paket(e) zugeordnet – bitte erst umhängen oder löschen.")
    n = db.val("SELECT COUNT(*) FROM schedules WHERE tenant_id=?", (tid,), 0)
    if n:
        # Nicht stillschweigend „zentral“ machen – ein Zeitplan eines OU-Admins liefe sonst auf allen Computern
        _err(f"Dem Mandanten sind noch {n} Zeitplan/Zeitpläne zugeordnet – bitte erst umhängen oder löschen.")
    db.ex("DELETE FROM tenants WHERE id=?", (tid,))
    _tenants_changed(tid)
    return {"ok": True}


# --- Berichte ------------------------------------------------------------------------------
@app.get("/api/reports")
def api_reports(user=U):
    return reports.definitions()


def _report_params(user, request):
    """2.30: Berichtsparameter – interne (mit „_“) nie aus der Anfrage; OU-Admin: nur eigene Computer und nur
    Sammlungen, die er sieht."""
    params = {k: v for k, v in request.query_params.items() if not k.startswith("_")}
    mine = access.computer_ids(user)
    if mine is not None:
        params["_scope"] = json.dumps(sorted(mine))
        c = str(params.get("collection") or "")
        if c and (not c.isdigit() or int(c) not in (_visible_collections(user) or set())):
            raise HTTPException(404, "Sammlung nicht gefunden")
    return params


# --- Eigene Berichte ---------------------------------------------------------------------------
@app.get("/api/customreports/{rid}")
def api_customreport(rid: int, user=U):
    r = customreports.get(rid)
    if not r:
        raise HTTPException(404, "Bericht nicht gefunden")
    if access.scoped(user):
        return {**r, "usage": []}  # OU-Admin: nur lesen – wo der Bericht verwendet wird, ist Sache des Hauptadmins
    return {**r, "usage": customreports.usage(rid)}


@app.post("/api/customreports")
def api_customreport_new(data: dict = Body(...), user=U):
    return _act(customreports.save, None, data, user["username"])


@app.put("/api/customreports/{rid}")
def api_customreport_save(rid: int, data: dict = Body(...), user=U):
    return _act(customreports.save, rid, data, user["username"])


@app.delete("/api/customreports/{rid}")
def api_customreport_delete(rid: int, user=U):
    _act(customreports.delete, rid, user["username"])
    return {"ok": True}


@app.post("/api/customreports/{rid}/duplicate")
def api_customreport_dup(rid: int, user=U):
    return _act(customreports.duplicate, rid, user["username"])


@app.post("/api/customreports/preview")
def api_customreport_preview(data: dict = Body(...), user=U):
    """Vorschau der (noch nicht gespeicherten) Definition: erste 200 Zeilen."""
    def run_():
        c = customreports.clean({**data, "name": data.get("name") or "Vorschau"})
        t0 = time.time()
        res = customreports.execute(c["definition"], limit=200)
        for row in res["rows"]:
            row.pop("_cid", None)
        return {**res, "ms": int((time.time() - t0) * 1000)}
    try:
        return run_()
    except sqlite3.OperationalError as e:
        _err(f"Abfrage nicht möglich: {e}")
    except ValueError as e:
        _err(e)


@app.get("/api/reports/{key}")
def api_report(key: str, request: Request, user=U):
    params = _report_params(user, request)
    try:
        return reports.run(key, params)
    except ValueError as e:
        _err(e)


@app.get("/api/reports/{key}/csv")
def api_report_csv(key: str, request: Request, user=U):
    params = _report_params(user, request)
    try:
        res = reports.run(key, params)
    except ValueError as e:
        _err(e)
    pklog.info("report", f"Bericht '{res['title']}' exportiert.", user=user["username"])
    fn = f"pocketknife_{notify._slug(res['title']) if key.startswith(customreports.PREFIX) else key}_{util.now()[:10]}.csv"
    return PlainTextResponse(reports.to_csv(res["columns"], res["rows"]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{fn}"'})


# --- E-Mail: SMTP, Berichtsversand, Benachrichtigungen ----------------------------------------
_PREVIEWS = {}  # token → (zeit, html, benutzer) – kurzlebige Mail-Vorschauen


def _mail(fn, *a):
    try:
        return fn(*a)
    except (ValueError, mailer.MailError) as e:
        _err(e)


@app.get("/api/mail/settings")
def api_mail_settings(user=U):
    return mailer.settings()


@app.put("/api/mail/settings")
def api_mail_settings_save(data: dict = Body(...), user=U):
    return _mail(mailer.save, data, user["username"])


@app.post("/api/mail/test")
def api_mail_test(data: dict = Body(default={}), user=U):
    return _mail(mailer.test, data.get("to") or "", user["username"])


@app.get("/api/mail/reports")
def api_mail_reports(user=U):
    return {"subscriptions": notify.all_subscriptions(), "configured": mailer.configured(),
            "default_to": mailer.settings()["to"]}


@app.post("/api/mail/reports")
def api_mail_report_new(data: dict = Body(...), user=U):
    return _mail(notify.save, None, data, user["username"])


@app.put("/api/mail/reports/{sid}")
def api_mail_report_save(sid: int, data: dict = Body(...), user=U):
    return _mail(notify.save, sid, data, user["username"])


@app.delete("/api/mail/reports/{sid}")
def api_mail_report_delete(sid: int, user=U):
    _mail(notify.delete, sid, user["username"])
    return {"ok": True}


@app.post("/api/mail/reports/{sid}/send")
def api_mail_report_send(sid: int, data: dict = Body(default={}), user=U):
    to = _mail(mailer.parse_recipients, data.get("to"), "Empfänger") if (data.get("to") or "").strip() else None
    r = _mail(notify.send_subscription, sid, "manuell", user["username"], to)
    return {"ok": True, **(r or {})}


@app.post("/api/mail/reports/preview")
def api_mail_report_preview(data: dict = Body(...), user=U):
    """Vorschau aus den (noch nicht gespeicherten) Eingaben des Editors."""
    s = _mail(notify.clean, {**data, "name": data.get("name") or "Vorschau"})
    subject, body, _t, files, total = _mail(notify.build, s)
    now = time.time()
    for k in [k for k, v in _PREVIEWS.items() if now - v[0] > 600]:
        _PREVIEWS.pop(k, None)
    token = secrets.token_urlsafe(16)
    _PREVIEWS[token] = (now, body, user["username"])
    return {"subject": subject, "url": f"/api/mail/view/{token}", "rows": total,
            "attachments": [{"name": n, "size": len(d)} for n, d, _m in files]}


@app.get("/api/mail/view/{token}")
def api_mail_view(token: str, user=U):
    v = _PREVIEWS.get(token)
    if not v or v[2] != user["username"]:
        raise HTTPException(404, "Vorschau abgelaufen")
    return HTMLResponse(v[1])


@app.get("/api/mail/alerts")
def api_mail_alerts(user=U):
    return {"alerts": notify.alerts_overview(), "configured": mailer.configured(), "default_to": mailer.settings()["to"]}


@app.put("/api/mail/alerts/{key}")
def api_mail_alert_save(key: str, data: dict = Body(...), user=U):
    return _mail(notify.save_alert, key, data, user["username"])


@app.post("/api/mail/alerts/{key}/preview")
def api_mail_alert_preview(key: str, data: dict = Body(default={}), user=U):
    if not notify.alert_def(key):
        raise HTTPException(404, "Nicht gefunden")
    return _mail(notify.preview_alert, key, data)


@app.post("/api/mail/alerts/check")
def api_mail_alerts_check(user=U):
    if not mailer.configured():
        _err("E-Mail ist nicht eingerichtet oder deaktiviert.")
    res = notify.check_alerts()
    return {"sent": sum(1 for r in res.values() if r.get("sent")), "checked": len(res),
            "errors": [r["error"] for r in res.values() if r.get("error")]}


# --- Protokoll ------------------------------------------------------------------------------
def _log_query(level, category, q, limit, user=None):
    sql, p = "SELECT l.*, c.name computer FROM logs l LEFT JOIN computers c ON c.id=l.computer_id WHERE 1=1", []
    if access.scoped(user):
        w, wp = access.sql_in(user, "l.computer_id")  # OU-Admin: nur Einträge zu seinen Computern
        sql += " AND " + w
        p += wp
    if level:
        sql += " AND l.level=?"
        p.append(level)
    if category:
        sql += " AND l.category=?"
        p.append(category)
    if q:
        sql += " AND (l.message LIKE ? ESCAPE '\\' OR l.username LIKE ? ESCAPE '\\' OR c.name LIKE ? ESCAPE '\\')"
        p += [util.like_pattern(q)] * 3
    sql += " ORDER BY l.id DESC LIMIT ?"
    p.append(max(1, min(int(limit), 20000)))
    return db.q(sql, p)


@app.get("/api/logs")
def api_logs(level: str = "", category: str = "", q: str = "", limit: int = 1000, user=U):
    return _log_query(level, category, q, limit, user)


@app.get("/api/logs/csv")
def api_logs_csv(level: str = "", category: str = "", q: str = "", limit: int = 20000, user=U):
    rows = _log_query(level, category, q, limit, user)
    cols = [{"key": k, "label": l} for k, l in (("ts", "Zeit"), ("level", "Stufe"), ("category", "Kategorie"),
                                                 ("computer", "Computer"), ("username", "Benutzer"), ("message", "Meldung"))]
    return PlainTextResponse(reports.to_csv(cols, rows), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="pocketknife_log_{util.now()[:10]}.csv"'})


# --- Einstellungen / System ------------------------------------------------------------------
# Über PUT /api/settings änderbar – AD-Einstellungen und interne Zustände nur über ihre eigenen Endpunkte
INTERNAL_SETTINGS = {"ldap_cert_fingerprint", "ldap_cert_fingerprints", "ldap_cert_pending", "ad_filter", "ad_containers",
                     "ad_last_sync", "ad_last_status", "ad_sync_minutes", "ad_remove_missing", "ad_auto_sync",
                     "ad_sync_disabled", "ad_ou_collections", "ad_group_collections",
                     "proxy_url", "proxy_user", "proxy_clients",
                     "update_check_enabled", "update_repo"}  # 2.28: nur über /api/system/updates (geprüft)  # Proxy: nur über /api/system/proxy (geprüft, Kennwort im Tresor)
def rebootmsg_clean(v, limit):
    from . import rebootmsg
    return rebootmsg.clean(v, limit)


EDITABLE = [k for k in db.DEFAULT_SETTINGS if k not in INTERNAL_SETTINGS]
# Grenzen für Zahlenwerte (min, max)
SETTING_BOUNDS = {"repo_upload_max_mb": (1, 102400), "repo_tenant_quota_mb": (0, 10 ** 7), "integrity_full_start": (0, 23), "integrity_full_end": (0, 24), "integrity_full_mbps": (5, 1000),
                  "integrity_full_enabled": (0, 1), "heartbeat_minutes": (1, 1440), "scan_online_delay_minutes": (0, 120), "scan_concurrency": (1, 64), "deploy_concurrency": (1, 64),
                  "deploy_timeout_minutes": (1, 1440), "winget_refresh_hours": (1, 720), "backup_keep": (1, 365),
                  "backup_hour": (0, 23), "db_maintenance_hour": (0, 23), "scan_interval_hours": (1, 8760), "du_scan_depth": (1, 8),
                  "rule_retry_hours": (1, 8760), "session_idle_minutes": (5, 720), "kiosk_max_per_day": (1, 500),
                  "dns_cache_minutes": (0, 1440), "identity_recheck_hours": (1, 168),
                  "wol_repeat": (1, 10), "wol_wait_seconds": (0, 900)}


# --- Fernsitzungen (RDP/VNC im Browser) -------------------------------------------------
@app.get("/api/remote/status")
def api_remote_status(user=U):
    return {"guacd": remote_desktop.guacd_status(), "own_login": remote_desktop.own_login(user["token_hash"]),
            "sso_enabled": bool(db.setting("remote_sso")), "keyboard": db.setting("remote_keyboard"),
            "layouts": remote_desktop.LAYOUTS, "color_depths": remote_desktop.COLOR_DEPTHS,
            "ports": remote_desktop.DEFAULT_PORTS, "vnc_password_set": bool(remote_desktop.vnc_password())}


@app.put("/api/remote/vnc_password")
def api_remote_vnc_password(data: dict = Body(...), user=U):
    pw = data.get("password") or ""
    if data.get("clear"):
        pw = ""
    elif not pw:
        _err("Bitte ein Kennwort eingeben (oder „Entfernen“ wählen).")
    if len(pw) > 256 or any(ord(ch) < 32 for ch in pw):
        _err("Ungültiges Kennwort.")
    remote_desktop.set_vnc_password(pw)
    pklog.info("system", "Zentrales VNC-Kennwort " + ("gesetzt." if pw else "entfernt."), user=user["username"])
    return {"ok": True, "vnc_password_set": bool(pw)}


@app.post("/api/remote/connect")
def api_remote_connect(data: dict = Body(...), user=U):
    c = db.q1("SELECT * FROM computers WHERE id=?", (access.need_computer(user, data.get("computer_id")),))
    if access.scoped(user):
        if data.get("auth") == "central":
            # Das zentrale VNC-Kennwort gilt auch für fremde Computer – nicht an OU-Admins
            raise HTTPException(403, "Das zentrale VNC-Kennwort ist nur für Hauptadmins.")
        data = {**data, "port": None}  # Standard-Port: Anmeldedaten gehen nie an einen beliebigen Dienst
    if not c:
        raise HTTPException(404, "Computer nicht gefunden")
    try:
        token = remote_desktop.prepare(c, user, user["token_hash"], data)
    except ValueError as e:
        _err(e)
    return {"token": token, "computer": c["name"], "host": c["dns_name"] or c["name"], "protocol": data.get("protocol") or "rdp"}


@app.get("/api/remote/sessions")
def api_remote_sessions(user=U):
    rows = remote_desktop.active()
    if access.scoped(user):
        rows = [r for r in rows if r.get("user") == user["username"]]  # OU-Admin: nur eigene Sitzungen
    return rows


@app.delete("/api/remote/sessions/{sid}")
def api_remote_kill(sid: str, user=U):
    if access.scoped(user) and not any(r["id"] == sid and r.get("user") == user["username"] for r in remote_desktop.active()):
        raise HTTPException(404, "Sitzung nicht gefunden")
    if not remote_desktop.kill(sid, user["username"]):
        raise HTTPException(404, "Sitzung nicht gefunden")
    pklog.info("remote", f"Fernsitzung {sid} beendet.", user=user["username"])
    return {"ok": True}


@app.websocket("/api/remote/tunnel")
async def ws_remote_tunnel(websocket: WebSocket):
    s = auth.session_for(websocket.cookies.get(auth.COOKIE))
    origin = (websocket.headers.get("origin") or "").split("://", 1)[-1]
    if not s or s.get("role") != "admin" or origin != websocket.headers.get("host"):
        await websocket.close(code=1008)
        return
    await remote_desktop.tunnel(websocket, s["token_hash"])


# --- Zertifikat der Weboberfläche ------------------------------------------------
def _b64(v):
    import base64
    if not v:
        return b""
    try:
        return base64.b64decode(v.split(",", 1)[1] if v.startswith("data:") else v, validate=False)
    except ValueError:
        _err("Datei nicht lesbar")


def _cert_args(data):
    kind = data.get("kind") if data.get("kind") in ("pfx", "pem") else "pem"
    return (kind, _b64(data.get("data")), data.get("password") or "", _b64(data.get("key")), data.get("key_password") or "")


@app.get("/api/tls")
def api_tls(user=U):
    return tls.info()


@app.get("/api/tls/cert")
def api_tls_cert(user=U):
    return Response(tls.current_pem(), media_type="application/x-pem-file",
                    headers={"Content-Disposition": f'attachment; filename="{config.PUBLIC_HOSTNAME}.crt"'})


@app.post("/api/tls/check")
def api_tls_check(data: dict = Body(...), user=U):
    try:
        return tls.preview(*_cert_args(data))
    except tls.CertError as e:
        _err(e)


@app.post("/api/tls/install")
def api_tls_install(data: dict = Body(...), user=U):
    try:
        return tls.install(*_cert_args(data), user=user["username"])
    except tls.CertError as e:
        _err(e)


@app.post("/api/tls/csr")
def api_tls_csr(data: dict = Body(...), user=U):
    names = [n.strip() for n in re.split(r"[\s,;]+", data.get("names") or "") if n.strip()]
    try:
        return {"csr": tls.create_csr(data.get("cn"), names, data.get("org") or "", data.get("ou") or "", data.get("country") or "",
                                      data.get("key_type") or "rsa3072", user=user["username"])}
    except tls.CertError as e:
        _err(e)


@app.delete("/api/tls/csr")
def api_tls_csr_drop(user=U):
    tls.drop_csr(user=user["username"])
    return {"ok": True}


@app.post("/api/tls/selfsigned")
def api_tls_selfsigned(user=U):
    return tls.reset_self_signed(user=user["username"])


@app.post("/api/tls/restore")
def api_tls_restore(data: dict = Body(...), user=U):
    try:
        return tls.restore(data.get("name"), user=user["username"])
    except (tls.CertError, OSError) as e:
        _err(e)


@app.get("/api/settings")
def api_settings(user=U):
    return db.all_settings()


def _unc_share(path):
    """(server, freigabe) eines UNC-Pfads in Kleinbuchstaben, sonst None (lokaler Ordner unter /repo)."""
    p = str(path or "").strip().replace("/", "\\")
    if not p.startswith("\\\\"):
        return None
    parts = [x for x in p.strip("\\").split("\\") if x]
    return (parts[0].lower() if parts else "", parts[1].lower() if len(parts) > 1 else "")


REPO_CONFIRM_MSG = ("Server oder Freigabe des Repositorys geändert – bitte zur Bestätigung das Kennwort des "
                    "Zugriffskontos ({0}) eingeben. So wird es nie versehentlich an einen anderen Server geschickt.")


def _check_repo_change(data):
    """Neuer Server/neue Freigabe: Das Kennwort des Repository-Kontos muss bestätigt werden – sonst schickte
    POCKET KNIFE die gespeicherte Anmeldung an einen Server, den jemand nur eingetippt (oder vertippt) hat."""
    from . import auth
    new = _unc_share(data.get("repo_path"))
    if new is None or new == _unc_share(db.setting("repo_path")):
        return
    stored = config.repo_password()
    if not stored:
        return  # nichts gespeichert, das abfließen könnte
    given = data.get("repo_password_confirm")
    if not given:
        _err(REPO_CONFIRM_MSG.format(config.repo_login() or "?"))
    if not auth._eq(given, stored):
        _err("Das Kennwort passt nicht zum gespeicherten Repository-/Dienstkonto – Repository-Pfad nicht geändert.")


@app.put("/api/settings")
def api_settings_save(data: dict = Body(...), user=U):
    changed = []
    if "repo_path" in data and str(data.get("repo_path") or "").strip() != str(db.setting("repo_path") or "").strip():
        _check_repo_change(data)
    for k, v in data.items():
        if k not in EDITABLE:
            continue
        if isinstance(db.DEFAULT_SETTINGS[k], int):
            try:
                v = int(float(v))
            except (TypeError, ValueError):
                _err(f"Ungültiger Wert für {k}")
            if v < 0:
                _err(f"Ungültiger Wert für {k}")
            lo, hi = SETTING_BOUNDS.get(k, (0, 10 ** 9))
            if not lo <= v <= hi:
                _err(f"Wert für {k} muss zwischen {lo} und {hi} liegen.")
        if k == "remote_keyboard" and v not in remote_desktop.LAYOUTS:
            _err("Unbekanntes Tastaturlayout.")
        if k == "remote_sso" and not v:
            remote_desktop.forget_all()
        if k.startswith("kiosk_"):
            try:
                kiosk.check_settings(k, v)
            except ValueError as e:
                _err(e)
        if k == "toast_language" and v not in ("de", "en"):
            _err("Sprache der Benachrichtigung: de oder en.")
        if k == "reboot_sender":
            v = rebootmsg_clean(v, 60)
        if k.startswith("reboot_msg_"):
            v = rebootmsg_clean(v, 400)
        if k == "wol_relays":
            # 2.31: feste Relays – Computernamen, durch Komma oder Zeilenumbruch getrennt
            names = [x.strip() for x in re.split(r"[,;\s]+", str(v or "")) if x.strip()]
            if any(not re.match(r"^[A-Za-z0-9._-]{1,253}$", x) for x in names) or len(names) > 50:
                _err("Feste Relays: Computernamen, durch Komma getrennt (höchstens 50).")
            v = ", ".join(dict.fromkeys(names))
        if k == "toast_sender":
            v = re.sub(r"[\x00-\x1f<>]", "", str(v or "")).strip()[:60]
        if k == "repo_upload_types":
            # 2.27: erlaubte Dateitypen für Uploads von OU-Admins – Endungen ohne Punkt, durch Komma getrennt
            parts = [x.strip().lower().lstrip(".") for x in re.split(r"[,;\s]+", str(v or "")) if x.strip()]
            if any(not re.match(r"^[a-z0-9]{1,12}$", x) for x in parts) or len(parts) > 200:
                _err("Dateitypen: Endungen wie msi, exe, zip – durch Komma getrennt.")
            v = ",".join(dict.fromkeys(parts))
        if k == "dns_servers":
            v = ", ".join(x.strip() for x in str(v or "").replace(";", ",").split(",") if x.strip())
            if len(v) > 500 or any(not re.match(r"^[A-Za-z0-9.\-:]+$", x) for x in v.split(", ") if x):
                _err("DNS-Server: Namen oder IP-Adressen, durch Komma getrennt.")
        if k in ("dns_cache_minutes", "dns_direct", "dns_servers"):
            from . import dnsres
            dnsres.flush()  # neue Regeln sofort anwenden
        if k == "ad_filter" and not (str(v).startswith("(") and str(v).endswith(")")):
            _err("LDAP-Filter muss in Klammern stehen.")
        if k == "repo_path" and str(v or "").strip():
            pth = str(v).strip()
            if not (pth.startswith("\\\\") or pth.startswith("//")):
                norm = os.path.normpath(pth)
                if not (norm == "/repo" or norm.startswith("/repo/")):
                    _err("Repository: UNC-Pfad (\\\\server\\freigabe) oder ein Ordner unter /repo (PK_REPO_MOUNT) angeben.")
        if k == "ldap_cert_strict" and not v and db.setting("ldap_cert_strict"):
            pklog.warn("system", "Strikte Prüfung des LDAPS-Zertifikats ausgeschaltet – ein geändertes Zertifikat wird dann "
                                 "ohne Bestätigung übernommen (Kennwörter gingen an jeden, der sich als DC ausgibt). "
                                 "Empfohlen: PK_LDAP_CA_FILE (CA der Domäne).", user=user["username"])
        if k == "auth_mode":
            v = str(v or "").strip().lower()
            if v and v not in kerberos.MODES:
                _err("Unbekanntes Anmeldeverfahren.")
            if v == "kerberos" and not kerberos.available()[0]:
                _err("Die Kerberos-Bibliotheken fehlen im Container – Image neu bauen (docker compose build).")
        if str(db.setting(k)) != str(v):
            db.set_setting(k, v)
            changed.append(k)
    if "auth_mode" in changed:
        _reset_connections()  # bestehende SMB-Sitzungen mit dem alten Verfahren verwerfen
        pklog.info("system", f"Anmeldeverfahren für WinRM/SMB: {kerberos.MODE_LABELS.get(kerberos.mode(), kerberos.mode())}",
                   user=user["username"])
    if changed:
        pklog.info("system", "Einstellungen geändert: " + ", ".join(changed), user=user["username"])
    return {"changed": changed}


@app.get("/api/system")
def api_system(user=U):
    from . import diagnostics
    hist = diagnostics.history()
    return {
        "version": config.VERSION, "version_since": hist[-1]["started_at"] if hist else None,
        "domain": config.DOMAIN, "netbios": config.NETBIOS, "ldap_servers": config.LDAP_SERVERS,
        "ldap_port": config.LDAP_PORT, "base_dn": config.BASE_DN, "computer_base_dns": config.COMPUTER_BASE_DNS,
        "access_group": config.ACCESS_GROUP, "service_user": config.service_login(),
        "ldap_configured": config.ldap_configured(), "ldap_fingerprint": db.setting("ldap_cert_fingerprint"),
        "ldap_fingerprints": ad._fingerprints(), "ldap_pending": json.loads(db.setting_raw("ldap_cert_pending") or "{}"),
        "ldap_strict": bool(db.setting("ldap_cert_strict")), "ldap_ca": bool(ad.CA_FILE),
        "winrm": {"auth": kerberos.mode(), "env_auth": config.WINRM_AUTH, "set_mode": db.setting_raw("auth_mode") or "", "https": config.WINRM_HTTPS, "port": config.WINRM_PORT},
        "kerberos": {"available": kerberos.available()[0], "version": kerberos.available()[1] if kerberos.available()[0] else "",
                     "realm": kerberos.realm(), "conf": kerberos.conf_path(), "kdc": kerberos.KDC_ENV,
                     "principal": kerberos.principal(config.service_user()) if config.service_user() else "",
                     "modes": [{"key": k, "label": v} for k, v in kerberos.MODE_LABELS.items()]},
        "repo_user": config.repo_login(), "data_dir": config.DATA_DIR,
        "db_size": os.path.getsize(config.DB_PATH) if os.path.exists(config.DB_PATH) else 0,
        "db": db.stats(), "db_maintenance_last": db.setting_raw("db_maintenance_last"),
        "scheduler": scheduler.state, "background": scheduler.running(), "winget": winget.status(),
        "scanning": len(inventory.scanning_ids()),
        "guard": guard.status(),
        "load": loadstats.view(),
    }


# --- Sicherungen ---------------------------------------------------------------------------
@app.get("/api/backups")
def api_backups(user=U):
    try:
        d = backup.directory()
        free = shutil.disk_usage(d).free
    except OSError as e:
        return {"dir": None, "error": str(e), "items": []}
    return {"dir": d, "free": free, "same_disk": backup.on_data_disk(d), "items": backup.listing(),
            "last": db.setting_raw("backup_last")}


@app.post("/api/backups")
def api_backup_create(user=U):
    try:
        return backup.create(user=user["username"])
    except (OSError, sqlite3.Error) as e:
        _err(f"Sicherung fehlgeschlagen: {e}")


@app.get("/api/backups/{name}")
def api_backup_download(name: str, user=U):
    try:
        p = backup.path_of(name)
    except ValueError as e:
        raise HTTPException(404, str(e))
    # Zum Herunterladen ohne Schlüsseldateien: Datenbank und Schlüssel getrennt halten (sonst ließen sich die
    # verschlüsselten Kennwörter mit dem Download entschlüsseln). Die vollständige Sicherung bleibt im Sicherungsordner.
    tmp = backup.download_copy(p)
    pklog.warn("system", f"Sicherung heruntergeladen (ohne Schlüssel): {name}", user=user["username"])
    return FileResponse(tmp, media_type="application/zip", filename=name.replace(".zip", "-ohne-schluessel.zip"),
                        background=BackgroundTask(os.remove, tmp))


@app.delete("/api/backups/{name}")
def api_backup_delete(name: str, user=U):
    try:
        backup.delete(name, user=user["username"])
    except (ValueError, OSError) as e:
        _err(e)
    return {"ok": True}


@app.post("/api/system/guard/reset")
def api_guard_reset(user=U):
    guard.reset(user["username"])
    ok = guard.verify_account()
    return {"ok": ok, "guard": guard.status()}


@app.post("/api/system/run/{key}")
def api_system_run(key: str, user=U):
    if key not in ("ad", "heartbeat", "winget", "rematch", "dbmaint"):
        _err("Unbekannte Aufgabe")
    scheduler.run_now(key, user["username"])
    pklog.info("system", f"Aufgabe '{key}' manuell gestartet.", user=user["username"])
    return {"ok": True}


@app.post("/api/system/ldaptest")
def api_ldaptest(user=U):
    try:
        c = ad.service_conn()
        c.unbind()
        return {"ok": True, "fingerprint": db.setting("ldap_cert_fingerprint")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# --- Active Directory: Container, Abgleich, AD-Sammlungen ------------------------------------
AD_KEYS = ["ad_auto_sync", "ad_sync_minutes", "ad_remove_missing", "ad_sync_disabled", "ad_ou_collections",
           "ad_group_collections"]


@app.get("/api/ad/settings")
def api_ad_settings(user=U):
    out = {k: db.setting(k) for k in AD_KEYS}
    out.update({"rules": ad.container_rules(), "last_sync": db.setting("ad_last_sync"),
                "last_status": db.setting("ad_last_status"), "base_dn": config.BASE_DN, "domain": config.DOMAIN,
                "rules_paths": {r["dn"]: ad.dn_path(r["dn"]) for r in ad.container_rules()}})
    return out


@app.put("/api/ad/settings")
def api_ad_settings_save(data: dict = Body(...), user=U):
    for k in AD_KEYS:
        if k in data:
            v = int(float(data[k] or 0))
            if k == "ad_sync_minutes" and not 5 <= v <= 100000:
                _err("Abgleich-Intervall: mindestens 5 Minuten.")
            db.set_setting(k, v)
    if "rules" in data:
        rules = []
        for r in data["rules"] or []:
            dn = (r.get("dn") or "").strip()
            if not dn or not ad._is_under(dn, config.BASE_DN):
                _err(f"Container liegt nicht in der Domäne: {dn}")
            if dn.lower() not in {x["dn"].lower() for x in rules}:
                rules.append({"dn": dn, "include": bool(r.get("include", True))})
        if not any(r["include"] for r in rules):
            _err("Mindestens ein Container muss eingeschlossen sein.")
        db.set_setting("ad_containers", json.dumps(rules, ensure_ascii=False))
    pklog.info("ad", "AD-Abgleich-Einstellungen geändert.", user=user["username"])
    if "ad_ou_collections" in data or "ad_group_collections" in data:
        try:
            ad.sync_ad_collections()
        except Exception as e:
            pklog.warn("ad", f"AD-Sammlungen: {e}")
    return api_ad_settings(user)


@app.get("/api/ad/browse")
def api_ad_browse(dn: str = "", groups: int = 0, user=U):
    """Baumauswahl: eine Ebene des AD (OUs/Container und optional Gruppen)."""
    try:
        return ad.browse(dn or None, bool(groups))
    except ad.ADError as e:
        _err(e)
    except Exception as e:
        _err(f"AD nicht erreichbar: {e}")


@app.get("/api/ad/groups")
def api_ad_groups(q: str = "", user=U):
    try:
        return ad.search_groups(q)
    except ad.ADError as e:
        _err(e)
    except Exception as e:
        _err(f"AD nicht erreichbar: {e}")


@app.get("/api/ad/containers")
def api_ad_containers(user=U):
    try:
        return ad.list_containers()
    except ad.ADError as e:
        _err(e)


# --- Dienstkonto / Repository-Konto (in der Oberfläche gepflegt) ------------------------------
def _accounts():
    from . import vault  # noqa: F401
    svc_ui = bool(db.setting_raw("svc_user"))
    repo_ui = bool(db.setting_raw("repo_user"))
    return {
        "service": {"user": config.service_user(), "login": config.service_login(), "source": config.service_source(),
                    "password_set": bool(config.service_password())},
        "repo": {"user": config.repo_user() if (repo_ui or config.ENV_REPO_USER) else "", "login": config.repo_login(),
                 "source": "ui" if repo_ui else ("env" if config.ENV_REPO_USER else "service"),
                 "password_set": bool(config.repo_password())},
        "env_service": bool(config.ENV_SERVICE_USER), "env_repo": bool(config.ENV_REPO_USER), "ui_service": svc_ui,
    }


def _reset_connections():
    try:
        import smbclient
        smbclient.reset_connection_cache()
    except Exception:
        pass
    guard.reset(None)


@app.post("/api/system/ldap/trust")
def api_ldap_trust(user=U):
    p = ad.trust_pending()
    if not p:
        _err("Kein geändertes Zertifikat zur Bestätigung vorhanden.")
    pklog.warn("ldap", f"Neues LDAPS-Zertifikat von {p['host']} bestätigt: {p['fingerprint']}", user=user["username"])
    return {"ok": True, **p}


@app.post("/api/system/kerberos/test")
def api_kerberos_test(data: dict = Body(default={}), user=U):
    c = None
    if data.get("computer_id"):
        c = db.q1("SELECT * FROM computers WHERE id=?", (int(data["computer_id"]),))
        if not c:
            raise HTTPException(404, "Computer nicht gefunden")
    m = data.get("mode") if data.get("mode") in kerberos.MODES else "kerberos"
    res = kerberos.test(c, m)
    pklog.info("system", f"Kerberos-Test ({m}){' mit ' + c['name'] if c else ''}: "
                         + ("erfolgreich" if res["ok"] else "fehlgeschlagen – " + next((s["step"] for s in res["steps"] if not s["ok"]), "")),
               user=user["username"])
    return res


@app.get("/api/system/accounts")
def api_accounts(user=U):
    return _accounts()


@app.put("/api/system/accounts")
def api_accounts_save(data: dict = Body(...), user=U):
    """Speichert Dienst- und/oder Repository-Konto. Das Dienstkonto wird vorher per LDAP geprüft,
    damit ein falsches Kennwort nicht durch automatische Zugriffe zur Kontosperre führt."""
    from . import vault
    which = data.get("which")
    if which not in ("service", "repo"):
        _err("Unbekanntes Konto")
    name = (data.get("user") or "").strip()
    pw = data.get("password")
    if data.get("reset"):
        db.ex("DELETE FROM settings WHERE key IN (?, ?)", ("svc_user", "svc_password") if which == "service"
              else ("repo_user", "repo_password"))
        _reset_connections()
        pklog.info("system", f"{'Dienstkonto' if which == 'service' else 'Repository-Konto'} zurückgesetzt "
                             f"(es gilt wieder die .env bzw. das Dienstkonto).", user=user["username"])
        return _accounts()
    if not name or any(ch in name for ch in "\r\n\t\"'<>"):
        _err("Benutzername fehlt oder ist ungültig.")
    key_u, key_p = ("svc_user", "svc_password") if which == "service" else ("repo_user", "repo_password")
    if not pw:
        # Nur der Name ändert sich – vorhandenes Kennwort behalten (nur wenn bereits in der Oberfläche gepflegt)
        if not db.setting_raw(key_u):
            _err("Bitte das Kennwort angeben.")
        pw = vault.decrypt(db.setting_raw(key_p))
    if which == "service" and not data.get("skip_test"):
        upn = config._qualify(name, prefer_upn=True)
        res = {}
        try:
            c = ad.bind(upn, pw, res)
        except ad.ADError as e:
            _err(f"LDAPS nicht erreichbar, Konto nicht geprüft: {e}")
        if c is None:
            text, _acc = guard.explain(f"{res.get('description', '')} {res.get('message', '')}")
            _err("Anmeldung mit diesem Konto abgelehnt – nicht gespeichert. " + (text or "Benutzer/Kennwort prüfen."))
        c.unbind()
    db.set_setting(key_u, name)
    db.set_setting(key_p, vault.encrypt(pw))
    _reset_connections()
    pklog.info("system", f"{'Dienstkonto' if which == 'service' else 'Repository-Konto'} auf '{name}' gesetzt.",
               user=user["username"])
    return _accounts()


@app.get("/api/system/diagnostics")
def api_diagnostics(days: int = 7, anon: int = 1, user=U):
    """Diagnosepaket als ZIP herunterladen (wird nie von selbst verschickt)."""
    from . import diagnostics
    data, name = diagnostics.build(days, bool(anon), user=user["username"])
    return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"',
                                                                 "Cache-Control": "no-store"})


@app.get("/api/system/diagnostics/lookup")
def api_diagnostics_lookup(alias: str, user=U):
    from . import diagnostics
    if not alias or len(alias) > 100:
        _err("Platzhalter angeben, z. B. PC-017.")
    return {"items": diagnostics.lookup(alias)}


@app.get("/api/system/diagnostics/mapping")
def api_diagnostics_mapping(user=U):
    """Zuordnung Platzhalter → echte Namen (bleibt bei Ihnen – nicht mitschicken)."""
    from . import diagnostics
    pklog.info("system", "Zuordnung der Diagnose-Platzhalter heruntergeladen.", user=user["username"])
    return PlainTextResponse(diagnostics.mapping_csv(), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": 'attachment; filename="pocketknife-diagnose-zuordnung.csv"',
                                      "Cache-Control": "no-store"})


@app.post("/api/system/toast/test")
def api_toast_test(data: dict = Body(...), user=U):
    """Test-Benachrichtigung an die angemeldeten Benutzer eines bekannten Computers."""
    from . import toast
    name = str(data.get("host") or "").strip().lower()
    c = db.q1("SELECT * FROM computers WHERE lower(name)=? OR lower(dns_name)=?", (name, name)) if name else None
    if not c:
        _err("Computer nicht gefunden – Namen aus der Computerliste angeben.")
    try:
        return toast.test(c, user["username"])
    except Exception as e:  # noqa: BLE001
        _err(f"Nicht gesendet: {e}")


@app.get("/api/system/proxy")
def api_proxy_get(user=U):
    from . import proxy
    return proxy.settings()


@app.put("/api/system/proxy")
def api_proxy_save(data: dict = Body(...), user=U):
    from . import proxy
    try:
        r = proxy.save(data)
    except ValueError as e:
        _err(e)
    pklog.info("system", f"Proxyserver gespeichert: {r['url'] or '–'} (Anmeldung: {r['user'] or 'keine'}, "
               f"Clients: {'ja' if r['clients'] else 'nein'}).", user=user["username"])
    return r


@app.post("/api/system/proxy/test")
def api_proxy_test(user=U):
    """Lädt die ersten Bytes des winget-Katalogs – über den eingetragenen Proxy (oder direkt, wenn keiner eingetragen ist)."""
    from . import proxy, winget
    return proxy.test(winget.URLS[0]) | {"url": winget.URLS[0]}


@app.get("/api/system/dns")
def api_dns_status(user=U):
    from . import dnsres
    return dnsres.status()


@app.post("/api/system/dns/flush")
def api_dns_flush(user=U):
    """„DNS-Cache leeren“: gespeicherte Namensauflösungen verwerfen – der nächste Heartbeat fragt neu."""
    from . import dnsres
    n = dnsres.flush()
    pklog.info("system", f"DNS-Zwischenspeicher geleert ({n} Einträge).", user=user["username"])
    return {"flushed": n}


@app.post("/api/system/dns/test")
def api_dns_test(data: dict = Body(...), user=U):
    """Einen Namen auflösen (über die eingestellte Methode, ohne Zwischenspeicher) – zum Prüfen der Einstellungen."""
    from . import dnsres
    name = str(data.get("name") or "").strip()
    if not name or len(name) > 255 or not re.match(r"^[A-Za-z0-9.\-]+$", name):
        _err("Bitte einen Rechnernamen angeben.")
    t0 = time.time()
    ip = dnsres.resolve(name, fresh=True)
    sysip = dnsres._resolve_system(name)
    return {"name": name, "ip": ip, "system_ip": sysip, "ms": int((time.time() - t0) * 1000),
            "direct": bool(db.setting("dns_direct")), "servers": dnsres.servers() if db.setting("dns_direct") else []}


@app.post("/api/system/hosttest")
def api_hosttest(data: dict = Body(...), user=U):
    host = (data.get("host") or "").strip()
    if not host or any(ch in host for ch in " \\/'\"`;"):
        _err("Ungültiger Hostname")
    # Das Dienstkonto meldet sich dabei an – nur an bekannten Computern oder Namen der eigenen Domäne,
    # nicht an beliebigen Adressen (sonst ließe sich die Anmeldung an einen fremden Server umleiten)
    h = host.lower()
    known = db.q1("SELECT 1 FROM computers WHERE lower(name)=? OR lower(dns_name)=? OR ip_addresses LIKE ? ESCAPE '\\'",
                  (h, h, util.like_pattern(host)))
    dom = (config.DOMAIN or "").lower()
    if not known and not ("." not in h and not h.replace(".", "").isdigit()) and not (dom and h.endswith("." + dom)):
        _err(f"Nur Computer aus der Liste oder Namen der Domäne {config.DOMAIN or ''} – keine fremden Adressen.")
    return remote.test_connection(host)


# --- Software-Kiosk -------------------------------------------------------------------------
def _ip(request):
    return request.client.host if request.client else "?"


def _kerr(fn, *a):
    try:
        return fn(*a)
    except ValueError as e:
        _err(e)


@app.get("/api/kiosk/info")
def api_kiosk_info():
    """Für die Anmeldeseite des Kiosks (ohne Anmeldung): eingeschaltet? Titel."""
    on = sso.enabled()
    return {"enabled": kiosk.enabled(), "title": db.setting("kiosk_title") or "Software-Kiosk", "app": config.APP_NAME,
            "sso": on, "sso_hosts": sso.hosts() if on else []}


@app.post("/api/kiosk/sso")
def api_kiosk_sso(request: Request):
    """Anmeldung mit Windows (Kerberos/SPNEGO). Ohne Ticket: 401 mit „WWW-Authenticate: Negotiate“ – der Browser
    fragt dann beim DC ein Ticket an und wiederholt die Anfrage. Fehler danach: 403 (ohne neue Aufforderung, damit
    der Browser nicht in einer Schleife fragt oder ein Anmeldefenster zeigt)."""
    if not sso.enabled():
        return JSONResponse({"detail": "Die Windows-Anmeldung ist nicht eingeschaltet."}, status_code=404)
    hdr = request.headers.get("authorization") or ""
    if not hdr.lower().startswith("negotiate "):
        return JSONResponse({"detail": "Windows-Anmeldung: Der Browser hat kein Kerberos-Ticket geschickt."},
                            status_code=401, headers={"WWW-Authenticate": "Negotiate", "Cache-Control": "no-store"})
    try:
        token, info, out = auth.login_sso(hdr, _ip(request))
    except sso.SSOError as e:
        return JSONResponse({"detail": str(e)}, status_code=403, headers={"Cache-Control": "no-store"})
    resp = JSONResponse({k: info[k] for k in ("username", "display_name", "role")}, headers={"Cache-Control": "no-store"})
    if out:
        resp.headers["WWW-Authenticate"] = "Negotiate " + base64.b64encode(out).decode("ascii")
    resp.set_cookie(auth.COOKIE, token, httponly=True, secure=config.SECURE_COOKIE, samesite="strict",
                    max_age=config.SESSION_HOURS * 3600, path="/")
    return resp


@app.get("/api/kiosk/me")
def api_kiosk_me(request: Request, user=K):
    comps, hint = kiosk.computers_for(user["username"], _ip(request))
    return {"username": user["username"], "display_name": user["display_name"], "role": user.get("role"),
            "computers": comps, "hint": hint, "idle_minutes": auth.idle_minutes(), "enabled": kiosk.enabled(),
            "title": db.setting("kiosk_title") or "Software-Kiosk", "version": config.VERSION}


@app.get("/api/kiosk/catalog")
def api_kiosk_catalog(request: Request, computer_id: int, user=K):
    if not kiosk.enabled():
        _err("Der Software-Kiosk ist ausgeschaltet.")
    _kerr(kiosk.check_computer, user["username"], _ip(request), computer_id)
    return {"items": kiosk.catalog(computer_id, user["username"]), "reboot": kiosk.reboot_info(computer_id)}


@app.post("/api/kiosk/request")
def api_kiosk_request(request: Request, data: dict = Body(...), user=K):
    try:
        cid, pid = int(data.get("computer_id")), int(data.get("package_id"))
    except (TypeError, ValueError):
        _err("Ungültige Anfrage.")
    return _kerr(kiosk.request, user, _ip(request), cid, pid, data.get("action") or "install")


@app.post("/api/kiosk/requests/{rid}/withdraw")
def api_kiosk_withdraw(rid: int, user=K):
    return {"ok": _kerr(kiosk.withdraw, user, rid)}


@app.get("/api/kiosk/requests")
def api_kiosk_requests(user=K):
    return kiosk.my_requests(user["username"])


@app.post("/api/kiosk/reboot")
def api_kiosk_reboot(request: Request, data: dict = Body(...), user=K):
    try:
        cid = int(data.get("computer_id"))
    except (TypeError, ValueError):
        _err("Ungültige Anfrage.")
    return _kerr(kiosk.plan_reboot, user, _ip(request), cid, str(data.get("when") or ""))


# Verwaltung des Kiosks (nur Administratoren)
@app.get("/api/kiosk/admin")
def api_kiosk_admin(user=U):
    return {"items": kiosk.admin_items(), "requests": kiosk.admin_requests(),
            "packages": [{"id": r["id"], "name": r["name"], "version": r["version"]}
                         for r in db.q("SELECT id, name, version FROM packages ORDER BY name COLLATE NOCASE")],
            "collections": _collection_choices(user), "enabled": kiosk.enabled(),
            "settings": {k: db.setting(k) for k in db.DEFAULT_SETTINGS if k.startswith("kiosk_")},
            "url": (mailer.base_url() or "").rstrip("/") + "/kiosk", "sso": sso.info()}


@app.put("/api/kiosk/admin/items/{pid}")
def api_kiosk_item_save(pid: int, data: dict = Body(...), user=U):
    _kerr(kiosk.save_item, pid, data)
    pklog.info("kiosk", f"Kiosk-Eintrag für Paket #{pid} gespeichert.", user=user["username"])
    return {"ok": True}


@app.get("/api/kiosk/icon/{pid}")
def api_kiosk_icon(pid: int, user=K):
    data, typ = kiosk.get_icon(pid)
    if not data:
        raise HTTPException(404)
    return Response(content=bytes(data), media_type=typ,
                    headers={"Cache-Control": "private, max-age=604800", "Content-Security-Policy": "default-src 'none'"})


@app.put("/api/kiosk/admin/items/{pid}/icon")
async def api_kiosk_icon_set(pid: int, request: Request, user=U):
    data = await request.body()
    try:
        kiosk.set_icon(pid, data)
    except ValueError as e:
        _err(e)
    pklog.info("kiosk", f"Symbol für Paket #{pid} hinterlegt.", user=user["username"])
    return {"ok": True}


@app.put("/api/kiosk/admin/sso/keytab")
async def api_kiosk_keytab_set(request: Request, user=U):
    data = await request.body()
    try:
        return sso.save_keytab(data, user=user["username"])
    except sso.SSOError as e:
        _err(e)


@app.delete("/api/kiosk/admin/sso/keytab")
def api_kiosk_keytab_clear(user=U):
    sso.clear_keytab(user=user["username"])
    return sso.info()


@app.post("/api/kiosk/admin/sso/test")
def api_kiosk_sso_test(user=U):
    return {"steps": sso.test(), "info": sso.info()}


@app.delete("/api/kiosk/admin/items/{pid}/icon")
def api_kiosk_icon_clear(pid: int, user=U):
    kiosk.clear_icon(pid)
    return {"ok": True}


@app.delete("/api/kiosk/admin/items/{pid}")
def api_kiosk_item_delete(pid: int, user=U):
    db.ex("DELETE FROM kiosk_items WHERE package_id=?", (pid,))
    pklog.info("kiosk", f"Paket #{pid} aus dem Kiosk entfernt.", user=user["username"])
    return {"ok": True}


@app.post("/api/kiosk/admin/requests/{rid}/{what}")
def api_kiosk_decide(rid: int, what: str, data: dict = Body(default={}), user=U):
    if what not in ("approve", "reject"):
        raise HTTPException(404)
    return _kerr(kiosk.decide, rid, what == "approve", user["username"], data.get("note") or "")


# --- Oberfläche ------------------------------------------------------------------------------
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")


_PAGES = {}


def _page(name):
    """HTML-Seite mit Versionsangabe an Skripten und Stylesheets (…/app.js?v=2.23.4): Nach einem Update lädt jeder
    Browser garantiert die neue Oberfläche – auch wenn er die alte Datei noch im Zwischenspeicher hat."""
    html = _PAGES.get(name)
    if html is None:
        with open(os.path.join(config.STATIC_DIR, name), encoding="utf-8") as fh:
            html = re.sub(r'((?:src|href)="/static/[^"?]+\.(?:js|css))"',
                          lambda m: f'{m.group(1)}?v={urllib.parse.quote(config.VERSION)}"', fh.read())
        _PAGES[name] = html
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/")
def index():
    return _page("index.html")


@app.get("/kiosk")
def kiosk_page():
    """Software-Kiosk für Nutzer (eigene, einfache Seite)."""
    return _page("kiosk.html")


@app.get("/remote")
def remote_page():
    """Fernsitzung (RDP/VNC) in einem eigenen Browser-Tab."""
    return _page("remote.html")


@app.get("/favicon.ico")
def favicon():
    return FileResponse(os.path.join(config.STATIC_DIR, "logo.svg"), media_type="image/svg+xml")
