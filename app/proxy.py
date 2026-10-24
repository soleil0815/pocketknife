"""Optionaler Proxyserver für Internetzugriffe (Einstellungen → Repository & winget → Proxyserver).

- POCKET KNIFE selbst: Download des winget-Katalogs. Wahlweise mit Benutzer und Kennwort (Basic-Anmeldung am Proxy);
  das Kennwort liegt verschlüsselt im Tresor (Einstellung „proxy_password“, nie im Klartext und nie im Diagnosepaket).
- Clients (optional, „proxy_clients“): winget lädt über den Proxy (winget-Administratoreinstellung „DefaultProxy“),
  ebenso das Nachinstallieren von winget. Anmeldedaten gehen NIE an die Clients – sie melden sich, falls der Proxy das
  verlangt, mit dem Computerkonto an (Windows-Anmeldung/Kerberos/NTLM am Proxy).
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
    """Proxy-Adresse prüfen: http(s)://name:port – ohne Anmeldedaten in der Adresse (die gehören in den Tresor)."""
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
    """Proxy-URL für Zugriffe von POCKET KNIFE selbst (mit Anmeldedaten) oder None."""
    s = settings()
    if not s["url"]:
        return None
    if not s["user"]:
        return s["url"]
    pw = vault.decrypt(db.setting_raw(SECRET)) if s["password_set"] else ""
    parts = urllib.parse.urlsplit(s["url"])
    cred = urllib.parse.quote(s["user"], safe="") + ":" + urllib.parse.quote(pw or "", safe="")
    return f"{parts.scheme}://{cred}@{parts.netloc}"


def opener():
    """urllib-Opener: über den Proxy, wenn eingetragen – sonst wie bisher (Umgebungsvariablen des Containers)."""
    p = _server_proxy()
    if not p:
        return urllib.request.build_opener()
    return urllib.request.build_opener(urllib.request.ProxyHandler({"http": p, "https": p}))


def urlopen(req, timeout):
    return opener().open(req, timeout=timeout)


def client_proxy():
    """Proxy-Adresse für die Clients (ohne Anmeldedaten) oder ""."""
    s = settings()
    return s["url"] if s["url"] and s["clients"] else ""


def safe_error(e):
    """Fehlermeldung ohne Anmeldedaten (urllib nennt in Ausnahmen mitunter die Proxy-URL)."""
    return re.sub(r"(https?://)[^/@\s]+@", r"\1***@", str(e))


def test(url):
    """Erreicht POCKET KNIFE die Adresse über den eingetragenen Proxy? (nur die ersten Bytes)"""
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
        if "407" in err:  # HTTPS-Ziel: die Ablehnung kommt beim Tunnelaufbau (CONNECT) als URLError
            hint = "Der Proxy verlangt eine Anmeldung (407) – Benutzer und Kennwort prüfen."
        elif settings()["url"] and ("name or service not known" in err.lower() or "getaddrinfo" in err.lower()
                                    or "nodename nor servname" in err.lower()):
            hint = "Der Name des Proxys lässt sich nicht auflösen – Adresse und DNS prüfen."
        elif "timed out" in err.lower():
            hint = "Keine Antwort – Adresse/Port des Proxys und Firewall prüfen."
        return {"ok": False, "error": err, "hint": hint, "ms": int((time.time() - t0) * 1000), "via": settings()["url"] or None}
