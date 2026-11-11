"""Optional proxy server for internet access (Einstellungen -> Updates & Aktualität -> Proxyserver).

- POCKET KNIFE itself: download of the winget catalog. Optionally with user and password (Basic authentication at the
  proxy); the password is stored encrypted in the vault (setting "proxy_password", never in plain text and never in the
  diagnostics package).
- Clients (optional, "proxy_clients"): winget downloads through the proxy (winget administrator setting "DefaultProxy"),
  as does the installation of winget. Credentials are NEVER sent to the clients - if the proxy requires it, they
  authenticate with the computer account (Windows authentication/Kerberos/NTLM at the proxy).
"""
import re
import time
import urllib.parse
import urllib.request

from . import db, vault

SECRET = "proxy_password"
URL_RX = re.compile(r"^http://[A-Za-z0-9.\-]+(:\d{1,5})?/?$", re.I)


def settings():
    return {"url": str(db.setting("proxy_url") or "").strip(), "user": str(db.setting("proxy_user") or "").strip(),
            "password_set": bool(db.setting_raw(SECRET)), "clients": bool(db.setting("proxy_clients"))}


def clean_url(url):
    """Validate the proxy address: http(s)://name:port - without credentials in the address (they belong in the vault)."""
    u = str(url or "").strip()
    if not u:
        return ""
    if "://" not in u:
        u = "http://" + u
    sch, rest = u.split("://", 1)
    u = sch.lower() + "://" + rest
    if "@" in u:
        raise ValueError("Benutzer und Kennwort bitte nicht in die Adresse schreiben, sondern in die Felder darunter – "
                         "das Kennwort wird dort verschlüsselt gespeichert.")
    if u.lower().startswith("https://"):
        raise ValueError("Bitte die Adresse mit http:// angeben – auch für HTTPS-Ziele (der Proxy baut die verschlüsselte "
                         "Verbindung per CONNECT auf).")
    if not URL_RX.match(u):
        raise ValueError("Proxy-Adresse wie http://proxy.firma.local:8080 angeben.")
    m = re.search(r":(\d+)/?$", u)
    if m and not 1 <= int(m.group(1)) <= 65535:
        raise ValueError("Ungültiger Port (1–65535).")
    return u.rstrip("/")


def save(data):
    url = clean_url(data.get("url"))
    user = str(data.get("user") or "").strip()
    if len(user) > 200 or any(ch in user for ch in "\r\n"):
        raise ValueError("Ungültiger Benutzername.")
    old = settings()
    if url and old["password_set"] and user and not data.get("password") and not data.get("clear_password") and \
            (url.lower(), user.lower()) != (str(old["url"] or "").lower(), str(old["user"] or "").lower()):
        # Audit 2.29: never send a stored password to a different proxy/user (as with SMTP)
        raise ValueError("Proxy-Adresse oder Benutzer geändert – bitte das Kennwort erneut eingeben.")
    db.set_setting("proxy_url", url)
    db.set_setting("proxy_user", user)
    db.set_setting("proxy_clients", 1 if (data.get("clients") and url) else 0)
    pw = data.get("password")
    if data.get("clear_password") or not user:
        db.ex("DELETE FROM settings WHERE key=?", (SECRET,))
    elif pw:
        db.set_setting(SECRET, vault.encrypt(str(pw)))
    return settings()


def _server_proxy():
    """Proxy URL for accesses by POCKET KNIFE itself (with credentials) or None."""
    s = settings()
    if not s["url"]:
        return None
    if not s["user"]:
        return s["url"]
    pw = vault.decrypt(db.setting_raw(SECRET)) if s["password_set"] else ""
    parts = urllib.parse.urlsplit(s["url"])
    cred = urllib.parse.quote(s["user"], safe="") + ":" + urllib.parse.quote(pw or "", safe="")
    return f"{parts.scheme}://{cred}@{parts.netloc}"


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Redirect to another host: do not forward credentials (Authorization) - urllib would do so
    (Audit 2.29: otherwise a token for GitHub would go to every redirect target)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and (urllib.parse.urlsplit(newurl).hostname or "").lower() != \
                (urllib.parse.urlsplit(req.full_url).hostname or "").lower():
            for k in [k for k in new.headers if k.lower() == "authorization"]:
                new.headers.pop(k, None)
            new.unredirected_hdrs.pop("Authorization", None)
        return new


def opener():
    """urllib opener: through the proxy if configured - otherwise as before (environment variables of the container)."""
    p = _server_proxy()
    if not p:
        return urllib.request.build_opener(SafeRedirect)
    return urllib.request.build_opener(urllib.request.ProxyHandler({"http": p, "https": p}), SafeRedirect)


def urlopen(req, timeout):
    return opener().open(req, timeout=timeout)


def client_proxy():
    """Proxy address for the clients (without credentials) or ""."""
    s = settings()
    return s["url"] if s["url"] and s["clients"] else ""


def safe_error(e):
    """Error message without credentials (urllib sometimes names the proxy URL in exceptions)."""
    return re.sub(r"(https?://)[^/@\s]+@", r"\1***@", str(e))


def test(url):
    """Does POCKET KNIFE reach the address through the configured proxy? (first bytes only)"""
    from . import config
    t0 = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": "POCKETKNIFE/" + config.VERSION, "Range": "bytes=0-1023"})
    try:
        with urlopen(req, timeout=20) as r:
            r.read(1024)
            return {"ok": True, "status": r.status, "ms": int((time.time() - t0) * 1000), "via": settings()["url"] or None}
    except urllib.error.HTTPError as e:
        hint = ""
        if e.code == 407:
            hint = "Der Proxy verlangt eine Anmeldung (407) – Benutzer und Kennwort prüfen."
        return {"ok": False, "status": e.code, "error": safe_error(e), "hint": hint, "ms": int((time.time() - t0) * 1000),
                "via": settings()["url"] or None}
    except Exception as e:  # noqa: BLE001
        err = safe_error(e)
        hint = ""
        if "407" in err:  # HTTPS target: the rejection arrives as a URLError during tunnel setup (CONNECT)
            hint = "Der Proxy verlangt eine Anmeldung (407) – Benutzer und Kennwort prüfen."
        elif settings()["url"] and ("name or service not known" in err.lower() or "getaddrinfo" in err.lower()
                                    or "nodename nor servname" in err.lower()):
            hint = "Der Name des Proxys lässt sich nicht auflösen – Adresse und DNS prüfen."
        elif "timed out" in err.lower():
            hint = "Keine Antwort – Adresse/Port des Proxys und Firewall prüfen."
        return {"ok": False, "error": err, "hint": hint, "ms": int((time.time() - t0) * 1000), "via": settings()["url"] or None}
