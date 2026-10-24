"""Docker-Healthcheck: prüft, ob die Weboberfläche antwortet.

Läuft als root im Container – deshalb mit „python -I“ (keine Benutzer-site-packages, keine PYTHON*-Variablen,
nicht das Skriptverzeichnis im Pfad) und mit denselben Regeln für PK_HTTPS/PK_PORT wie app/config.py (leer = Standard).
"""
import os
import ssl
import sys
import urllib.request


def _env(key, default=""):
    f = os.environ.get(key + "_FILE")
    if f and os.path.isfile(f):
        with open(f, encoding="utf-8") as fh:
            return fh.read().strip()
    return os.environ.get(key, default)


def _bool(value, default):
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on", "ja")


https = _bool(_env("PK_HTTPS"), True)
port = (_env("PK_PORT", "8443") or "8443").strip()
url = f"{'https' if https else 'http'}://127.0.0.1:{port}/"
ctx = ssl._create_unverified_context() if https else None  # eigenes Zertifikat, nur lokal
try:
    with urllib.request.urlopen(url, timeout=8, context=ctx) as r:
        sys.exit(0 if r.status == 200 else 1)
except Exception:
    sys.exit(1)
