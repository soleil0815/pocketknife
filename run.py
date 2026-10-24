"""Startpunkt: Zertifikat bereitstellen (selbstsigniert oder eigenes aus /data/tls) und den Webserver starten."""
import os

import uvicorn

from app import config


if __name__ == "__main__":
    # Als root gestartet (Docker): Datenordner übergeben und Rechte abgeben – der Webserver läuft nie als root
    os.umask(0o077)  # Audit 2.29: Datenbank, Protokolle und Ordner nie für andere lesbar – auch ohne Start als root
    from app import privileges
    backup_dir = os.environ.get("PK_BACKUP_DIR", "/backup")
    privileges.drop([config.DATA_DIR] + ([backup_dir] if os.path.isdir(backup_dir) else []))
    if os.environ.get("HOME", "/nonexistent") == "/nonexistent":
        os.environ["HOME"] = "/tmp"  # ohne Start als root (privileges.drop setzt es sonst)
    config.ensure_dirs()
    if os.path.isdir("/repo"):
        try:
            os.listdir("/repo")
        except PermissionError:
            print(f"POCKET KNIFE: Warnung – /repo ist für Benutzer {os.getuid()} nicht lesbar. Rechte auf dem Docker-Host "
                  f"anpassen (z. B. chmod -R o+rX oder chown -R {os.getuid()} auf PK_REPO_MOUNT).", flush=True)
    kw = {}
    if config.HTTPS:
        from app import tls
        c, k = tls.ensure()
        kw = {"ssl_certfile": c, "ssl_keyfile": k}
    cfg = uvicorn.Config("app.main:app", host="0.0.0.0", port=config.PORT, proxy_headers=True,
                         forwarded_allow_ips=os.environ.get("PK_TRUSTED_PROXIES", "127.0.0.1"),
                         log_level="warning", access_log=False, **kw)
    cfg.load()
    if config.HTTPS:
        # Den TLS-Kontext merken: ein neues Zertifikat aus der Oberfläche wird ohne Neustart geladen
        tls.register(cfg.ssl)
    uvicorn.Server(cfg).run()
