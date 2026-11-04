"""Entry point: provide the certificate (self-signed or own from /data/tls) and start the web server."""
import os

import uvicorn

from app import config


if __name__ == "__main__":
    # Started as root (Docker): hand over the data folders and drop privileges - the web server never runs as root
    os.umask(0o077)  # Audit 2.29: database, logs and folders never readable by others - even without starting as root
    from app import privileges
    backup_dir = os.environ.get("PK_BACKUP_DIR", "/backup")
    privileges.drop([config.DATA_DIR] + ([backup_dir] if os.path.isdir(backup_dir) else []))
    if os.environ.get("HOME", "/nonexistent") == "/nonexistent":
        os.environ["HOME"] = "/tmp"  # without starting as root (privileges.drop sets it otherwise)
    # 2.30: check write permission in /data (incl. subfolders) and /backup beforehand - otherwise only a traceback in the log
    _bad = privileges.unwritable([config.DATA_DIR, config.LOG_DIR, config.WINGET_DIR, config.TLS_DIR, config.REPORT_DIR,
                                  config.TMP_DIR])
    _bad_backup = privileges.unwritable([backup_dir]) if os.path.isdir(backup_dir) else []
    if _bad:
        raise SystemExit(privileges.write_hint(_bad + _bad_backup, config.DATA_DIR))
    if _bad_backup:
        print(privileges.write_hint(_bad_backup, config.DATA_DIR).replace("Start abgebrochen", "Warnung – Sicherungen werden "
              "nicht gespeichert"), flush=True)
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
        # Remember the TLS context: a new certificate from the UI is loaded without a restart
        tls.register(cfg.ssl)
    uvicorn.Server(cfg).run()
