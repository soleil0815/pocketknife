"""Docker healthcheck: checks whether the web interface responds.

Runs as root in the container - hence with "python -I" (no user site-packages, no PYTHON* variables,
no script directory in the path) and with the same rules for PK_HTTPS/PK_PORT as app/config.py (empty = default).
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
ctx = ssl._create_unverified_context() if https else None  # own certificate, local only
try:
    with urllib.request.urlopen(url, timeout=8, context=ctx) as r:
        sys.exit(0 if r.status == 200 else 1)
except Exception:
    sys.exit(1)
