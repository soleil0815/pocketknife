"""Automatische Sicherung: Datenbank (konsistente Online-Kopie), Schlüssel und Zertifikat als ZIP.

Ablage: /backup (in der docker-compose.yml als PK_BACKUP_PATH eingebunden), sonst /data/backup.
Es bleiben die neuesten N Sicherungen erhalten (Einstellung „backup_keep“, Standard 10).
"""
import datetime
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import zipfile

from . import config, db, pklog, util

NAME_RE = re.compile(r"^pocketknife-backup-\d{8}-\d{6}(-[a-z0-9.-]{1,40})?\.zip$")
_lock = threading.Lock()


def directory():
    """Sicherungsordner: /backup, wenn eingebunden und beschreibbar, sonst im Datenverzeichnis."""
    for d in (os.environ.get("PK_BACKUP_DIR", "/backup"), os.path.join(config.DATA_DIR, "backup")):
        try:
            os.makedirs(d, exist_ok=True)
            test = os.path.join(d, ".pk-write-test")
            with open(test, "w") as f:
                f.write("x")
            os.remove(test)
            return d
        except OSError:
            continue
    raise OSError("Kein beschreibbarer Sicherungsordner gefunden.")


def on_data_disk(d):
    """True, wenn der Sicherungsordner auf demselben Datenträger wie die Datenbank liegt."""
    try:
        return os.stat(d).st_dev == os.stat(config.DATA_DIR).st_dev
    except OSError:
        return True


def listing():
    try:
        d = directory()
    except OSError:
        return []
    out = []
    for n in os.listdir(d):
        if NAME_RE.match(n):
            p = os.path.join(d, n)
            st = os.stat(p)
            out.append({"name": n, "size": st.st_size,
                        "created": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                        "label": (re.search(r"-\d{8}-\d{6}-(.+)\.zip$", n) or [None, ""])[1]})
    return sorted(out, key=lambda x: x["name"], reverse=True)


def path_of(name):
    if not NAME_RE.match(name or ""):
        raise ValueError("Ungültiger Dateiname.")
    p = os.path.join(directory(), name)
    if not os.path.exists(p):
        raise ValueError("Sicherung nicht gefunden.")
    return p


def _rotate(keep):
    items = listing()
    removed = []
    # Die jüngste Sicherung „vor Update“ bleibt immer erhalten (Rückweg nach einem Update);
    # ältere zählen mit, damit der Ordner nicht unbegrenzt wächst
    newest_pre = next((x["name"] for x in items if "-vor-" in x["name"]), None)
    for x in [x for x in items if x["name"] != newest_pre][max(1, keep - (1 if newest_pre else 0)):]:
        try:
            os.remove(os.path.join(directory(), x["name"]))
            removed.append(x["name"])
        except OSError:
            pass
    return removed


def create(label="", user=None):
    """Sicherung anlegen. Gibt {name, size, seconds, removed} zurück."""
    with _lock:
        t0 = datetime.datetime.now()
        d = directory()
        free = shutil.disk_usage(d).free
        dbsize = os.path.getsize(config.DB_PATH) if os.path.exists(config.DB_PATH) else 0
        if free < dbsize * 1.5 + 50 * 1024 * 1024:
            raise OSError(f"Zu wenig freier Speicher im Sicherungsordner ({free // 1048576} MB frei).")
        label = re.sub(r"[^a-z0-9.-]+", "-", (label or "").lower()).strip("-")[:40]
        name = f"pocketknife-backup-{t0.strftime('%Y%m%d-%H%M%S')}" + (f"-{label}" if label else "") + ".zip"
        tmpdir = tempfile.mkdtemp(prefix="pk-backup-", dir=d)
        try:
            # Konsistente Kopie im laufenden Betrieb (SQLite-Online-Backup, auch mit WAL)
            snap = os.path.join(tmpdir, "pocketknife.db")
            src = sqlite3.connect(config.DB_PATH, timeout=60)
            dst = sqlite3.connect(snap)
            with dst:
                src.backup(dst)  # in einem Schritt – schrittweise würde bei laufenden Schreibzugriffen immer neu beginnen
            dst.close()
            src.close()
            target = os.path.join(d, name)
            part = target + ".part"
            with zipfile.ZipFile(part, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
                z.write(snap, "pocketknife.db")
                key = os.path.join(config.DATA_DIR, "pocketknife.key")
                if os.path.exists(key):
                    z.write(key, "pocketknife.key")
                for fn in ("server.crt", "server.key", "source.json"):
                    p = os.path.join(config.TLS_DIR, fn)
                    if os.path.exists(p):
                        z.write(p, f"tls/{fn}")
                z.writestr("INFO.txt", f"POCKET KNIFE {config.VERSION}\nErstellt: {util.now()}\n"
                                       f"Wiederherstellen: Container stoppen, Inhalt in das Datenverzeichnis (PK_DATA_PATH) "
                                       f"entpacken (vorhandene pocketknife.db-wal und -shm vorher löschen), Container starten.\n")
            os.chmod(part, 0o600)
            os.replace(part, target)
        except BaseException:
            try:
                os.remove(target + ".part")  # unvollständiges Archiv nicht liegen lassen
            except OSError:
                pass
            raise
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        removed = _rotate(int(db.setting("backup_keep") or 10))
        secs = round((datetime.datetime.now() - t0).total_seconds(), 1)
        size = os.path.getsize(target)
        db.set_setting("backup_last", f"{util.now()} · {name} · {size // 1024} KB")
        pklog.info("system", f"Sicherung erstellt: {name} ({size / 1048576:.1f} MB, {secs} s)"
                             + (f"; {len(removed)} alte Sicherung(en) entfernt" if removed else ""), user=user)
        return {"name": name, "size": size, "seconds": secs, "removed": removed}


SECRET_MEMBERS = {"pocketknife.key", "tls/server.key"}


def download_copy(path):
    """Kopie einer Sicherung ohne pocketknife.key und TLS-Schlüssel (für den Download über die Oberfläche)."""
    fd, tmp = tempfile.mkstemp(prefix="pk-dl-", suffix=".zip")
    os.close(fd)
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as dst:
        for item in src.infolist():
            if item.filename in SECRET_MEMBERS:
                continue
            if item.filename == "INFO.txt":
                dst.writestr("INFO.txt", src.read(item).decode("utf-8", "replace") +
                             "\nDieser Download enthält NICHT pocketknife.key und den HTTPS-Schlüssel. Nach einer "
                             "Wiederherstellung damit Dienstkonto, Repository-Konto und VNC-Kennwort neu eintragen – oder die "
                             "vollständige Sicherung direkt aus dem Sicherungsordner auf dem Docker-Host verwenden.\n")
                continue
            with src.open(item) as fsrc, dst.open(item, "w") as fdst:
                shutil.copyfileobj(fsrc, fdst, 1024 * 1024)
    return tmp


def delete(name, user=None):
    os.remove(path_of(name))
    pklog.info("system", f"Sicherung gelöscht: {name}", user=user)


def due(now=None):
    """Tägliche Sicherung zur eingestellten Stunde (einmal pro Tag)."""
    if not db.setting("backup_enabled"):
        return False
    now = now or datetime.datetime.now()
    # „ab der eingestellten Stunde, einmal am Tag“ statt „genau in dieser Stunde“ – sonst fällt die Sicherung
    # bei der Zeitumstellung (02:00 gibt es im Frühjahr nicht) oder nach einem Neustart in dieser Stunde aus
    if now.hour < int(db.setting("backup_hour")):
        return False
    return (db.setting_raw("backup_last_auto") or "")[:10] != now.strftime("%Y-%m-%d")


# 2.33: Die Datenbank stammt von einer neueren Version (zurück auf eine ältere ohne die Sicherung „vor-…“) – Hinweis
NEWER_DB = None


def _base(v):
    """2.32.12-s3 → 2.32.12 (ein Sicherheitsupdate ändert die Datenbank nicht)."""
    return str(v or "").split("-s")[0]


def before_update():
    """Beim Start nach einem Versionswechsel einmal sichern – vor den Datenbank-Migrationen,
    also wirklich der Stand vor dem Update."""
    global NEWER_DB
    if not os.path.exists(config.DB_PATH):
        return
    try:
        c = sqlite3.connect(config.DB_PATH, timeout=30)
        rows = dict(c.execute("SELECT key, value FROM settings WHERE key IN ('last_version', 'backup_enabled', 'db_version_max')").fetchall())
        c.close()
    except sqlite3.Error:
        return
    from . import util
    top = rows.get("db_version_max") or rows.get("last_version") or ""
    NEWER_DB = top if top and util.vcmp(_base(top), _base(config.VERSION)) > 0 else None
    if NEWER_DB:
        print(f"POCKET KNIFE {config.VERSION}: Die Datenbank wurde schon von Version {NEWER_DB} benutzt. Läuft etwas nicht, "
              f"die Sicherung „vor-{NEWER_DB}“ zurückspielen (Hilfe → Sicherung) oder wieder {NEWER_DB} starten.", flush=True)
    if rows.get("last_version") == config.VERSION or str(rows.get("backup_enabled", "1")) in ("0", "False", "false"):
        return
    try:
        create(label=f"vor-{config.VERSION}")
    except Exception as e:
        print(f"Sicherung vor dem Update fehlgeschlagen: {e}", flush=True)


def mark_version():
    from . import util
    db.set_setting("last_version", config.VERSION)
    top = db.setting_raw("db_version_max")
    if not top or util.vcmp(config.VERSION, top) > 0:
        db.set_setting("db_version_max", config.VERSION)  # höchste Version, die diese Datenbank benutzt hat
