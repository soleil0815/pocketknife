"""Sessions and login."""
import datetime
import hashlib
import hmac
import json
import re
import secrets
import threading
import time

from fastapi import HTTPException, Request

from . import ad, config, db, pklog, util

COOKIE = "pk_session"
_fails = {}
_flock = threading.Lock()


def _h(token):
    return hashlib.sha256(token.encode()).hexdigest()


IP_LIMIT = 30  # failed attempts per IP in 15 minutes (all user names together)


def _norm_user(username):
    """sAMAccountName as in the AD login - "a\\admin", "b\\admin", "admin@x" count together."""
    u = str(username or "").strip().lower()
    if "\\" in u:
        u = u.split("\\", 1)[1]
    return u.split("@", 1)[0]


def _throttle(keys):
    """Check AND note the attempt immediately (atomic) - concurrent requests cannot bypass the limit.
    Returns the timestamp; on success _ok() takes the attempt out again."""
    with _flock:
        now = time.time()
        for k in [k for k, v in _fails.items() if not v or now - v[-1] >= 900]:
            _fails.pop(k, None)  # remove old entries so the memory does not grow
        for key, limit in keys:
            lst = [t for t in _fails.get(key, []) if now - t < 900]
            if lst:
                _fails[key] = lst
            if len(lst) >= limit:
                raise HTTPException(429, "Zu viele Fehlversuche. Bitte 15 Minuten warten.")
        for key, _limit in keys:
            _fails.setdefault(key, []).append(now)
        return now


def _ok(keys, stamp):
    """Successful login: remove the noted attempt again."""
    with _flock:
        for key, _limit in keys:
            lst = _fails.get(key)
            if lst and stamp in lst:
                lst.remove(stamp)


INFRA_FAIL = "Anmeldung gerade nicht möglich – der Verzeichnisdienst ist nicht erreichbar. Bitte später erneut versuchen."
_INFRA = ("LDAPS-Verbindung", "Verbindung zu", "nicht erreichbar", "Dienstkonto", ":636", ":389", "Timeout", "timed out")
_NAME_OK = re.compile(r"^[\w.\-@\\ ]{1,64}$")


def _log_name(username):
    """For the log: a password mistakenly typed into the name field must not appear there in clear text."""
    u = str(username or "")
    if _NAME_OK.match(u) and not re.search(r"[^\w.\-@\\ ]", u):
        return f"'{u}'"
    return f"(ungültiger Benutzername, {len(u)} Zeichen)"


def _eq(a, b):
    """Constant-time comparison - also for umlauts (compare_digest on str allows only ASCII)."""
    return hmac.compare_digest(str(a or "").encode("utf-8"), str(b or "").encode("utf-8"))


# Messages that are only possible after a correct password or that confirm the account: do not reveal to the client
_PRIVATE = ("Kein Zugriff", "abgelaufen", "deaktiviert", "gesperrt", "Benutzerobjekt", "Benutzername oder Passwort falsch",
            "Zugriffsgruppe", "LDAP-Suche", "Notfallzugang")  # the last three only possible after a correct password
GENERIC_FAIL = "Anmeldung fehlgeschlagen: Benutzername oder Passwort falsch, oder kein Zugriff auf POCKET KNIFE."


def login(username, password, ip, kiosk=False):
    if not isinstance(username, str) or not isinstance(password, str):
        raise HTTPException(400, "Benutzername und Passwort angeben.")
    if config.BREAKGLASS_USER and _eq(username, config.BREAKGLASS_USER):
        # Own counter: failed attempts of other accounts behind the same address (NAT, terminal server) do not lock out
        # the emergency access - and it is itself still limited to 8 attempts in 15 minutes
        # No limit across all addresses: otherwise anyone could lock out the emergency access permanently - precisely when
        # the AD is down. Instead the notification "Notfallzugang benutzt" reports every failed attempt (Audit 2.29).
        key = [(f"bg|{ip}", 8)]
    else:
        key = [(f"{ip}|{_norm_user(username)}", 8), (f"ip|{ip}", IP_LIMIT)]
    stamp = _throttle(key)
    try:
        if (config.BREAKGLASS_USER and config.BREAKGLASS_PASSWORD and
                _eq(username, config.BREAKGLASS_USER) and _eq(password, config.BREAKGLASS_PASSWORD)):
            info = {"username": config.BREAKGLASS_USER, "display_name": "Notfallzugang", "_breakglass": True}
            pklog.warn("auth", f"Notfallzugang: Anmeldung von {ip}", user=username)
        else:
            info = ad.authenticate(username, password, allow_kiosk=kiosk)
            info["_domain_login"] = True
            if _norm_user(info["username"]) == _norm_user(config.BREAKGLASS_USER or "\0"):
                # An AD account named like the emergency access must not inherit its special rights (no AD re-check)
                raise ad.ADError("Dieses Konto hat denselben Namen wie der Notfallzugang (PK_BREAKGLASS_USER) – bitte dort einen anderen Namen wählen.")
    except ad.ADError as e:
        msg = str(e)
        if config.BREAKGLASS_USER and _eq(username, config.BREAKGLASS_USER):
            pklog.warn("auth", f"Notfallzugang: Fehlversuch von {ip}")
        pklog.warn("auth", f"Anmeldung fehlgeschlagen für {_log_name(username)} von {ip}: {msg}")
        # Details (password correct, but no access / expired ...) only in the log - otherwise one could verify whether a password is right.
        # Audit 2.29: connection errors equally generic (otherwise they would reveal names/ports of the domain controllers)
        if any(x in msg for x in _PRIVATE):
            raise HTTPException(401, GENERIC_FAIL)
        if any(x in msg for x in _INFRA):
            raise HTTPException(401, INFRA_FAIL)
        raise HTTPException(401, msg)
    _ok(key, stamp)
    domain_login = info.pop("_domain_login", False)
    token = _new_session(info, ip, breakglass=info.pop("_breakglass", False))
    if domain_login and info["role"] == "admin":
        from . import remote_desktop
        remote_desktop.remember(_h(token), username, password)  # in RAM only, for remote sessions
    extra = " (Kiosk)" if info["role"] == "kiosk" else ""
    if info.get("scope"):
        names = [r["name"] for r in db.q(f"SELECT name FROM tenants WHERE id IN ({','.join('?' * len(info['scope']))})",
                                          info["scope"])]
        extra = " (OU-Admin: " + ", ".join(names) + ")"
    pklog.info("auth", f"Anmeldung von {ip}" + extra, user=info["username"])
    return token, info


def _new_session(info, ip, breakglass=False):
    token = secrets.token_urlsafe(32)
    exp = datetime.datetime.now() + datetime.timedelta(hours=config.SESSION_HOURS)
    role = info.get("role") or "admin"
    info["role"] = role
    # OU admin: list of their tenants; main admin, emergency access and kiosk: NULL
    scope = info.get("scope") if role == "admin" and not breakglass else None
    scope = json.dumps(sorted({int(t) for t in scope})) if scope is not None else None  # [] stays restricted
    db.ex("INSERT INTO sessions(token_hash, username, display_name, created, expires, ip, last_active, role, scope, confirmed_at) "
          "VALUES(?,?,?,?,?,?,?,?,?,?)",
          (_h(token), info["username"], info["display_name"], util.now(), util.fmt(exp), ip, util.now(), role, scope, time.time()))
    _active[_h(token)] = time.time()
    if breakglass:
        _breakglass_tokens.add(_h(token))
    _checked[_h(token)] = time.time()
    return token


def login_sso(header, ip):
    """Software kiosk: login with Windows (Kerberos). Always yields a kiosk session.
    Returns (token, info, response token for "WWW-Authenticate: Negotiate ..." or None); errors as sso.SSOError."""
    from . import sso
    key = [(f"sso|{ip}", IP_LIMIT)]
    stamp = _throttle(key)
    try:
        try:
            user, realm, out = sso.accept(header)
        except sso.SSOError:
            raise
        except Exception as e:  # e.g. /tmp not writable - never as a 500
            pklog.warn("auth", f"Windows-Anmeldung am Kiosk von {ip}: {e}")
            raise sso.SSOError("Windows-Anmeldung gerade nicht möglich – bitte mit Benutzername und Kennwort anmelden.")
        try:
            info = ad.sso_user(user)
        except Exception as e:
            # Ticket was valid - missing permission or an AD fault does not count as a failed attempt of this address
            _ok(key, stamp)
            pklog.warn("auth", f"Windows-Anmeldung am Kiosk von {ip}: {e}")
            raise sso.SSOError(INFRA_FAIL if any(x in str(e) for x in _INFRA) else str(e))
        if _norm_user(info["username"]) == _norm_user(config.BREAKGLASS_USER or "\0"):
            raise sso.SSOError("Dieses Konto hat denselben Namen wie der Notfallzugang – Windows-Anmeldung nicht möglich.")
    except sso.SSOError as e:
        pklog.warn("auth", f"Windows-Anmeldung am Kiosk fehlgeschlagen von {ip}: {e}")
        raise
    _ok(key, stamp)
    info["role"] = "kiosk"
    info["sso"] = True
    token = _new_session(info, ip)
    pklog.info("auth", f"Anmeldung von {ip} (Kiosk, Windows-Anmeldung {user}@{realm})", user=info["username"])
    return token, info, out


def logout(token):
    if token:
        from . import remote_desktop
        remote_desktop.forget(_h(token))
        remote_desktop.kill_for_session(_h(token))
        _checked.pop(_h(token), None)
        _active.pop(_h(token), None)
        _breakglass_tokens.discard(_h(token))
        db.ex("DELETE FROM sessions WHERE token_hash=?", (_h(token),))


# --- Logout on inactivity -----------------------------------------------------------------
# Only input in the browser counts as activity (mouse, keyboard - also in a remote session), which the UI reports
# at most once per minute (POST /api/session/active). Automatic refreshes of open pages
# do not count - otherwise a forgotten browser window would stay logged in forever.
IDLE_DEFAULT = 30
IDLE_MIN, IDLE_MAX = 5, 720
_active = {}  # token_hash → time of the last activity (seconds); database as fallback after a restart
_TOUCH_WRITE = 30  # write to the database at most every 30 s
_written = {}
IDLE_MSG = "Wegen Inaktivität abgemeldet – bitte erneut anmelden."


def idle_minutes():
    try:
        m = int(db.setting("session_idle_minutes") or IDLE_DEFAULT)
    except (TypeError, ValueError):
        m = IDLE_DEFAULT
    return max(IDLE_MIN, min(IDLE_MAX, m))  # always active - it cannot be switched off


def _last_active(token_hash, row):
    t = _active.get(token_hash)
    if t is None:
        dt = util.parse(row.get("last_active") or row.get("created"))
        t = dt.timestamp() if dt else time.time()
        _active[token_hash] = t
    return t


def idle_remaining(token_hash, row=None):
    """Seconds until logout due to inactivity (≤ 0 = expired)."""
    if row is None:
        row = db.q1("SELECT created, last_active FROM sessions WHERE token_hash=?", (token_hash,))
        if not row:
            return 0
    return idle_minutes() * 60 - (time.time() - _last_active(token_hash, dict(row)))


def touch(token_hash):
    """User is active: remember the time (and occasionally save it so it survives a restart)."""
    now = time.time()
    _active[token_hash] = now
    if now - _written.get(token_hash, 0) >= _TOUCH_WRITE:
        _written[token_hash] = now
        db.ex("UPDATE sessions SET last_active=? WHERE token_hash=?", (util.now(), token_hash))


def end_session(token_hash, reason="beendet"):
    """End a session on the server (e.g. tenant of an OU admin changed) - incl. remote sessions. Returns whether
    the session still existed. The reason is given to the user on the next request."""
    from . import remote_desktop
    if len(_ended) > 5000:
        _ended.clear()
    _ended[token_hash] = reason
    remote_desktop.forget(token_hash)
    remote_desktop.kill_for_session(token_hash, reason)
    for d in (_checked, _active, _written):
        d.pop(token_hash, None)
    _breakglass_tokens.discard(token_hash)
    return bool(db.ex_count("DELETE FROM sessions WHERE token_hash=?", (token_hash,)))


_ended = {}  # token_hash → reason (for the message to the user)


def _end_idle(token_hash, username):
    from . import remote_desktop
    remote_desktop.forget(token_hash)
    remote_desktop.kill_for_session(token_hash, "Abmeldung wegen Inaktivität")
    for d in (_checked, _active, _written):
        d.pop(token_hash, None)
    _breakglass_tokens.discard(token_hash)
    if db.ex_count("DELETE FROM sessions WHERE token_hash=?", (token_hash,)):
        pklog.info("auth", f"Sitzung wegen Inaktivität beendet (nach {idle_minutes()} Minuten).", user=username)


def expire_idle():
    """From the scheduler every minute: end inactive sessions (also open remote sessions without browser activity)."""
    for r in db.q("SELECT token_hash, username, created, last_active FROM sessions"):
        if idle_remaining(r["token_hash"], r) <= 0:
            _end_idle(r["token_hash"], r["username"])


def session_valid(token_hash):
    s = db.q1("SELECT expires, created, last_active FROM sessions WHERE token_hash=?", (token_hash,))
    return bool(s and s["expires"] >= util.now() and idle_remaining(token_hash, s) > 0)


def _lookup(token):
    """(session, reason) for a cookie value; session None if invalid."""
    if not token:
        return None, "Nicht angemeldet"
    th = _h(token)
    s = db.q1("SELECT username, display_name, expires, created, last_active, role, scope, confirmed_at FROM sessions "
              "WHERE token_hash=?", (th,))
    if not s or s["expires"] < util.now():
        return None, _ended.pop(th, "Sitzung abgelaufen")
    if idle_remaining(th, s) <= 0:
        _end_idle(th, s["username"])
        return None, IDLE_MSG
    scope = None
    if s.get("scope"):
        try:
            scope = [int(t) for t in json.loads(s["scope"])]
        except (TypeError, ValueError):
            scope = []  # unreadable → no computers (never "everything")
    return {"username": s["username"], "display_name": s["display_name"], "token_hash": th, "role": s.get("role") or "admin",
            "scope": scope, "confirmed_at": s.get("confirmed_at")}, ""


def session_for(token):
    """Session for a cookie value (also for WebSockets) or None."""
    return _lookup(token)[0]


_breakglass_tokens = set()
RECHECK_SECONDS = 120   # check account and groups of running sessions every 2 minutes in the background
RETRY_SECONDS = 60      # AD currently not reachable: retry after 1 minute
MAX_UNCONFIRMED = 1800  # OU admins: at most 30 minutes without confirmation by the AD (outage, fault, protection active)
_checked = {}
_bg_lock = threading.Lock()
_unreach = {"warned": 0}


def _skip_check(s):
    """Emergency access and operation without LDAP are not checked against the AD."""
    if s["token_hash"] in _breakglass_tokens or not config.ldap_configured():
        return True
    return bool(config.BREAKGLASS_USER and s["username"] == config.BREAKGLASS_USER and s.get("display_name") == "Notfallzugang")


def _apply(s, ok):
    """Apply the result of the AD check to a session (see ad.check_sessions)."""
    th = s["token_hash"]
    now = time.time()
    if ok is None:
        _checked[th] = now - RECHECK_SECONDS + RETRY_SECONDS
        conf = s.get("confirmed_at") or 0
        if s.get("scope") is not None and conf and now - conf > MAX_UNCONFIRMED:
            # OU admin unconfirmed for 30 minutes: better have them log in again than delay revocations indefinitely
            if end_session(th, "Abgemeldet – die Berechtigung ließ sich 30 Minuten lang nicht im AD bestätigen."):
                pklog.warn("auth", "Sitzung beendet: Berechtigung seit 30 Minuten nicht im AD bestätigt (AD nicht erreichbar?).",
                           user=s["username"])
        return
    _checked[th] = now
    if ok is False or (isinstance(ok, list) and not ok):
        if end_session(th, "Zugriff entzogen – bitte erneut anmelden."):
            pklog.warn("auth", "Sitzung beendet: Konto deaktiviert oder nicht mehr in der berechtigten Gruppe.", user=s["username"])
        return
    if (s.get("role") or "admin") == "admin":
        new = sorted({int(t) for t in ok}) if isinstance(ok, list) else None
        old = sorted(s["scope"]) if s.get("scope") is not None else None
        if new != old:
            widened = new is None or (old is not None and set(new) >= set(old))
            if not widened:
                # Fewer rights (tenant gone, main admin → OU admin): log in completely anew - also ends remote sessions
                # on computers that are now foreign
                if end_session(th, "Ihre Berechtigung wurde geändert – bitte erneut anmelden."):
                    pklog.info("auth", "Sitzung beendet: Berechtigung im AD verringert.", user=s["username"])
                return
            db.ex("UPDATE sessions SET scope=? WHERE token_hash=?", (json.dumps(new) if new is not None else None, th))
            s["scope"] = new
            pklog.info("auth", "Berechtigung der Sitzung erweitert (" + ("Hauptadmin" if new is None else "OU-Admin") + ").",
                       user=s["username"])
    db.ex("UPDATE sessions SET confirmed_at=? WHERE token_hash=?", (now, th))


def recheck_all(force=False):
    """From the scheduler every minute: check due sessions against the AD - in ONE pass with one connection.
    This way group changes take effect on their own, even for users who are not clicking anything right now (open remote session)."""
    if not config.ldap_configured() or not _bg_lock.acquire(blocking=False):
        return
    try:
        now = time.time()
        due = []
        for r in db.q("SELECT token_hash, username, display_name, role, scope, confirmed_at FROM sessions WHERE expires >= ?",
                      (util.now(),)):
            th = r["token_hash"]
            scope = None
            if r.get("scope"):
                try:
                    scope = [int(t) for t in json.loads(r["scope"])]
                except (TypeError, ValueError):
                    scope = []
            s = {"username": r["username"], "display_name": r["display_name"], "token_hash": th,
                 "role": r.get("role") or "admin", "scope": scope, "confirmed_at": r.get("confirmed_at")}
            if _skip_check(s) or (not force and now - _checked.get(th, 0) < RECHECK_SECONDS):
                continue
            due.append(s)
        if not due:
            return
        res = ad.check_sessions([(s["token_hash"], s["username"], s["role"]) for s in due])
        if all(v is None for v in res.values()) and now - _unreach["warned"] > 900:
            _unreach["warned"] = now
            pklog.warn("auth", f"Nachprüfung der Sitzungen: AD gerade nicht erreichbar – {len(due)} Sitzung(en) unbestätigt; "
                               "OU-Admins werden nach 30 Minuten ohne Bestätigung abgemeldet.")
        for s in due:
            try:
                _apply(s, res.get(s["token_hash"]))
            except Exception as e:  # noqa: BLE001 – one session must never stop the check of the others
                pklog.warn("auth", f"Prüfung der Sitzung fehlgeschlagen: {e}", user=s["username"])
    finally:
        _bg_lock.release()


def _session(request):
    token = request.cookies.get(COOKIE)
    s, why = _lookup(token)
    if not s:
        raise HTTPException(401, why)
    # No AD query in the request itself (a slow AD would otherwise slow down the UI) - recheck_all does that
    # in the background. Safety net in case the background does not run: OU admins at most 30 minutes
    # without confirmation.
    if s.get("scope") is not None and not _skip_check(s) and s.get("confirmed_at") \
            and time.time() - float(s["confirmed_at"]) > MAX_UNCONFIRMED + 120:
        end_session(s["token_hash"], "Abgemeldet – die Berechtigung ließ sich nicht im AD bestätigen.")
        raise HTTPException(401, "Abgemeldet – die Berechtigung ließ sich nicht im AD bestätigen. Bitte erneut anmelden.")
    return s


def current(request: Request):
    """Administration: main admins (access group) and OU admins (tenant group, only allowed endpoints and only
    their computers - app/access.py). Kiosk sessions never get access here."""
    s = _session(request)
    if s.get("role") != "admin":
        raise HTTPException(403, "Kein Zugriff auf die Verwaltung – diese Anmeldung gilt nur für den Software-Kiosk.")
    from . import access
    access.enforce(request, s)
    return s


def any_session(request: Request):
    """Any valid session (administration, OU admin or kiosk) - only for session matters such as the inactivity clock."""
    return _session(request)


def current_kiosk(request: Request):
    """Software kiosk: kiosk users and main admins. OU admins not via their administration session (otherwise they could
    use the kiosk on any machine, even foreign ones, without the kiosk group) - logging in as a kiosk user still works."""
    s = _session(request)
    if s.get("role") == "admin" and s.get("scope") is not None:
        raise HTTPException(403, "Mit einer OU-Admin-Anmeldung ist der Kiosk nicht verfügbar – bitte im Kiosk als Kiosk-Benutzer anmelden.")
    return s
