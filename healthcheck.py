"""Docker-Healthcheck: prüft, ob die Weboberfläche antwortet."""
import os
import ssl
import sys
import urllib.request

https = os.environ.get("PK_HTTPS", "true").strip().lower() in ("1", "true", "yes", "on", "ja")
port = os.environ.get("PK_PORT", "8443")
url = f"{'https' if https else 'http'}://127.0.0.1:{port}/"
ctx = ssl._create_unverified_context() if https else None  # eigenes Zertifikat, nur lokal
try:
    with urllib.request.urlopen(url, timeout=8, context=ctx) as r:
        sys.exit(0 if r.status == 200 else 1)
except Exception:
    sys.exit(1)
