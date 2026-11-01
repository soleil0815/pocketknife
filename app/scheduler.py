"""Hintergrundaufgaben: AD-Abgleich, Heartbeat, Scans, geplante Bereitstellungen, Regeln, Wartung."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import ad, backup, config, db, presence, deploy, guard, mailer, notify, pklog, profiles, remote, schedules, util, winget

_last = {}
_running = set()
state = {"started": None, "last_tick": None, "heartbeat": None, "ad_sync": None}


def _due(key, minutes):
    t = _last.get(key)
    return t is None or time.time() - t >= minutes * 60


_bg_lock = threading.Lock()


def _bg(key, fn):
    with _bg_lock:  # Prüfen und Eintragen atomar – sonst starten Minuten-Thread und Knopfdruck dieselbe Aufgabe doppelt
        if key in _running:
            return
        _running.add(key)

    def run():
        from . import loadstats
        try:
            with loadstats.task(key.split("-")[0]):  # „start-17“ → „start“: Bereitstellungsstarts zusammen
                fn()
        except Exception as e:
            pklog.error("system", f"Hintergrundaufgabe '{key}' fehlgeschlagen: {e}")
        finally:
            _running.discard(key)
    threading.Thread(target=run, name=f"bg-{key}", daemon=True).start()


def heartbeat():
    comps = db.q(f"SELECT {presence.COLUMNS} FROM computers")
    if not comps:
        return
    with ThreadPoolExecutor(max_workers=48) as ex:
        results = list(ex.map(lambda c: (c, presence.probe(c)), comps))  # inkl. Prüfung, ob wirklich dieser Computer antwortet
    now = util.now()
    came_online = []
    with db.tx():
        for c, (online, winrm) in results:
            # Vergleich mit dem aktuellen Stand in der DB (ein Scan kann ihn inzwischen geändert haben)
            if presence.update(c["id"], online, winrm, None, now):
                came_online.append(c["id"])
    state["heartbeat"] = now
    # Heartbeat-Auslöser der Scanprofile und Zeitpläne: Computer, deren WinRM gerade erreichbar geworden ist
    presence.notify(came_online)
    presence.after_heartbeat([c["id"] for c, (online, winrm) in results if winrm])


def _heartbeat_only():
    heartbeat()


def _profiles():
    # Jeder Schritt für sich: ein Fehler (z. B. „database is locked“) darf die anderen nicht auslassen
    for name, fn in (("Scanprofile", profiles.evaluate), ("Scan-Verteilung", profiles.dispatch), ("Zeitpläne", schedules.evaluate)):
        try:
            fn()
        except Exception as e:
            pklog.error("system", f"{name}: {e}")


def _ad_sync():
    ad.sync_computers()
    state["ad_sync"] = util.now()


def _cleanup():
    days = db.setting("log_retention_days")
    if days and days > 0:  # 0 = nie löschen (wie bei den anderen Aufbewahrungsfristen)
        db.ex("DELETE FROM logs WHERE ts < ?", (util.ago(days=days),))
    db.ex("DELETE FROM sessions WHERE expires < ?", (util.now(),))
    days = db.setting("scan_log_retention_days")
    if days and days > 0:
        # Scan-Protokoll (Verlauf) – die gescannten Daten selbst bleiben erhalten
        db.ex("DELETE FROM scan_history WHERE ts < ?", (util.ago(days=days),))
        db.ex("DELETE FROM logs WHERE category='scan' AND ts < ?", (util.ago(days=days),))
    days = db.setting("software_changes_retention_days")
    if days and days > 0:
        db.ex("DELETE FROM software_changes WHERE ts < ?", (util.ago(days=days),))
    days = db.setting("deployment_retention_days")
    if days and days > 0:
        db.ex("DELETE FROM deployments WHERE created_at < ? AND status NOT IN ('running','queued','scheduled')",
              (util.ago(days=days),))
        # 2.36.3 (Audit, Datenschutz): Kiosk-Anfragen (Benutzer, Anzeigename, Computer) mit derselben Frist –
        # offene Anfragen bleiben, bis sie entschieden sind
        db.ex("DELETE FROM kiosk_requests WHERE created_at < ? AND COALESCE(status,'') NOT IN ('pending','starting')",
              (util.ago(days=days),))
    # Reste ohne Bezug (z. B. aus Vorabversionen oder nach Abbrüchen)
    db.ex("DELETE FROM deployment_targets WHERE deployment_id NOT IN (SELECT id FROM deployments)")
    db.ex("DELETE FROM schedule_targets WHERE schedule_id NOT IN (SELECT id FROM schedules)")
    db.ex("DELETE FROM schedule_trigger_state WHERE schedule_id NOT IN (SELECT id FROM schedules)")
    db.ex("DELETE FROM inventory_data WHERE computer_id NOT IN (SELECT id FROM computers)")
    db.ex("DELETE FROM kiosk_requests WHERE computer_id IS NOT NULL AND computer_id NOT IN (SELECT id FROM computers)")
    notify.cleanup()
    _tmp_cleanup()
    try:
        remote.close_idle_smb()
    except Exception:
        pass


def _tmp_cleanup():
    """Liegengebliebene Zwischendateien (abgebrochene Downloads/Uploads, fehlgeschlagene Index-Updates) entfernen."""
    import glob
    import os
    import tempfile
    limit = time.time() - 3600
    pats = [os.path.join(tempfile.gettempdir(), p) for p in ("pk-dl-*.zip", "pk-up-*")]
    pats += [os.path.join(config.WINGET_DIR, p) for p in ("upload-*.msix", "tmp*.db", "tmp*.msix")]
    try:
        bdir = backup.directory()
        pats += [os.path.join(bdir, "*.zip.part"), os.path.join(bdir, "pk-backup-*")]
    except Exception:
        pass
    for pat in pats:
        for f in glob.glob(pat):
            try:
                if os.path.getmtime(f) < limit:
                    if os.path.isdir(f):
                        import shutil
                        shutil.rmtree(f, ignore_errors=True)
                    else:
                        os.remove(f)
            except OSError:
                pass


def db_maintenance(vacuum=None, user=None):
    """Aufräumen + Datenbankwartung (nachts automatisch, oder manuell unter Einstellungen → Wartung)."""
    _cleanup()
    busy = ""
    if vacuum is None:
        # VACUUM sperrt die Datenbank; nicht während laufender Bereitstellungen oder Scans (sonst „database is locked“)
        from . import inventory
        if db.val("SELECT COUNT(*) FROM deployments WHERE status='running'", (), 0) or inventory.scanning_ids():
            vacuum, busy = False, "VACUUM verschoben: Bereitstellungen oder Scans laufen"
    r = db.maintenance(vacuum)
    if busy and not r["note"]:
        r["note"] = busy
    mb = lambda b: f"{b / 1048576:.1f} MB"
    msg = (f"Datenbankwartung: {mb(r['before']['size'])} → {mb(r['after']['size'])}"
           + (f" (VACUUM, {r['seconds']} s)" if r["vacuum"] else f", {r['before']['free_pct']} % frei – kein VACUUM nötig")
           + (f" – {r['note']}" if r["note"] else ""))
    pklog.info("system", msg, user=user)
    db.set_setting("db_maintenance_last", util.now() + " · " + msg.replace("Datenbankwartung: ", ""))
    state["db_maintenance"] = util.now()
    return r


def _backup():
    try:
        backup.create(label="auto")
        db.set_setting("backup_last_auto", util.now())
    except Exception as e:
        db.set_setting("backup_last", util.now() + " · FEHLGESCHLAGEN: " + str(e)[:200])
        pklog.error("system", f"Automatische Sicherung fehlgeschlagen: {e}")


def _nightly_due():
    """Einmal täglich ab der eingestellten Stunde (Standard 03:00) – auch wenn die Stunde durch Zeitumstellung
    oder Neustart verpasst wurde, dann eben später am Tag."""
    import datetime
    now = datetime.datetime.now()
    h = int(db.setting("db_maintenance_hour"))
    if not 0 <= now.hour - h < 3:  # nur in den 3 Stunden ab der Wartungsstunde (VACUUM sperrt kurz die Datenbank)
        return False
    last = (db.setting_raw("db_maintenance_last") or "")[:10]
    return last != now.strftime("%Y-%m-%d")


def _mail():
    """Berichtsversand und Benachrichtigungen (nur Datenbank + SMTP, kein Zugriff auf Clients)."""
    for name, fn in (("Berichtsversand", notify.evaluate), ("Benachrichtigungen", notify.check_alerts)):
        try:
            fn()
        except Exception as e:
            pklog.error("mail", f"{name}: {e}")


def tick():
    state["last_tick"] = util.now()
    try:  # 2.31: Lastspitzen mit dem, was gerade lief (Einstellungen → Wartung)
        from . import inventory, loadstats
        loadstats.sample(running(), len(inventory.scanning_ids()),
                         db.val("SELECT COUNT(*) FROM deployments WHERE status='running'", (), 0))
    except Exception:
        pass
    if _due("idle", 1):
        _last["idle"] = time.time()
        try:
            from . import auth
            auth.expire_idle()  # Abmeldung bei Inaktivität (auch für offene Fernsitzungen)
        except Exception as e:
            pklog.warn("auth", f"Prüfung auf inaktive Sitzungen fehlgeschlagen: {e}")
        # Angemeldete Sitzungen gegen das AD prüfen (Gruppen geändert, Konto deaktiviert) – im Hintergrund, die einzelne
        # Sitzung nur alle 2 Minuten; der Dienstkonto-Schutz greift wie überall (bei Störung: keine Anmeldeversuche)
        from . import auth as _auth
        _bg("sessions", _auth.recheck_all)
    # E-Mail auch bei aktivem Dienstkonto-Schutz – genau dann soll die Benachrichtigung kommen
    if _due("mail", 1) and mailer.configured():
        _last["mail"] = time.time()
        _bg("mail", _mail)
    if guard.paused():
        # Dienstkonto-Schutz aktiv: keine Anmeldungen erzeugen, nur Heartbeat (TCP) und winget-Index
        if _due("hb", db.setting("heartbeat_minutes")):
            _last["hb"] = time.time()
            _bg("hb", _heartbeat_only)
        return
    if config.ldap_configured() and db.setting("ad_auto_sync") and _due("ad", db.setting("ad_sync_minutes")):
        _last["ad"] = time.time()
        _bg("ad", _ad_sync)
    if _due("hb", db.setting("heartbeat_minutes")):
        _last["hb"] = time.time()
        _bg("hb", heartbeat)
    if _due("profiles", 1):
        _last["profiles"] = time.time()
        _bg("profiles", _profiles)
    for d in db.q("SELECT id FROM deployments WHERE status='scheduled' AND scheduled_at <= ?", (util.now(),)):
        # im Hintergrund: vor dem Start werden die Repository-Dateien geprüft (2.26), das darf den Takt nicht aufhalten
        _bg(f"start-{d['id']}", lambda did=d["id"]: deploy.start(did))
    if _due("stuck", 2):
        _last["stuck"] = time.time()
        _bg("stuck", deploy.restart_stuck)
    if _due("integrity", 5):
        _last["integrity"] = time.time()
        from . import integrity
        _bg("integrity", integrity.background)
    if _due("update_check", 60):
        _last["update_check"] = time.time()
        from . import news
        _bg("update_check", news.check)  # fragt selbst nur einmal täglich bei GitHub (CHECK_HOURS)
    if _due("repo_idents", 10):
        _last["repo_idents"] = time.time()
        from . import repofiles
        _bg("repo_idents", repofiles.backfill)  # Kennzeichen für die Duplikatsperre nachtragen
    if _due("repo_uploads", 60):
        _last["repo_uploads"] = time.time()
        from . import repofiles as _rf
        _bg("repo_uploads", _rf.cleanup_uploads)  # abgebrochene Uploads nach 24 Stunden aufräumen
    if _due("integrity_full", 10):
        _last["integrity_full"] = time.time()
        from . import integrity as _ig
        if db.setting("integrity_full_enabled") and _ig.in_window():
            _bg("integrity_full", _ig.full_check)
    if db.setting("winget_auto_refresh") and _due("winget", db.setting("winget_refresh_hours") * 60):
        _last["winget"] = time.time()
        _bg("winget", winget.refresh)
    if db.setting("winget_bootstrap") and _due("wingetboot", 24 * 60):
        # 2.37: App-Installer für Computer ohne winget einmal täglich aktuell halten (nur bei neuem Release laden)
        _last["wingetboot"] = time.time()
        from . import wingetboot
        _bg("wingetboot", lambda: wingetboot.refresh())
    if db.setting("winget_cache_auto") and _due("wingetcache", 24 * 60):
        # 2.35: winget-Cache einmal täglich auf neue Versionen prüfen (nur wenn Einträge da sind)
        _last["wingetcache"] = time.time()
        from . import wingetcache
        if db.val("SELECT COUNT(*) FROM winget_cache", default=0):
            _bg("wingetcache", wingetcache.refresh_all)
    if _due("client_cleanup", 5) and db.val("SELECT COUNT(*) FROM client_cleanup", default=0):
        # Reste unterbrochener Läufe (Neustart von POCKET KNIFE) auf erreichbaren Computern entfernen
        _last["client_cleanup"] = time.time()
        _bg("client_cleanup", deploy.process_cleanups)
    if _due("cleanup", 60):
        _last["cleanup"] = time.time()
        _bg("cleanup", _cleanup)
    if _due("backup_check", 10) and backup.due():
        _last["backup_check"] = time.time()
        _bg("backup", _backup)
    if _due("dbmaint_check", 10) and _nightly_due():
        _last["dbmaint_check"] = time.time()
        _bg("dbmaint", db_maintenance)


def _loop():
    time.sleep(5)
    while True:
        try:
            tick()
        except Exception as e:
            pklog.error("system", f"Scheduler-Fehler: {e}")
        time.sleep(20)


def start():
    state["started"] = util.now()
    # Index nicht sofort neu laden, wenn er jünger als das Intervall ist
    st = winget.status()
    if st["updated"]:
        age = time.time() - time.mktime(time.strptime(st["updated"], "%Y-%m-%d %H:%M:%S"))
        _last["winget"] = time.time() - age
    _last["wingetboot"] = time.time() - (24 * 60 - 40) * 60  # 2.37: erste Prüfung 40 Minuten nach dem Start
    _last["wingetcache"] = time.time() - (24 * 60 - 30) * 60  # 2.35: erste Prüfung des winget-Caches 30 Minuten nach dem Start
    threading.Thread(target=_loop, name="scheduler", daemon=True).start()


def run_now(key, user=None):
    if key == "ad":
        _bg("ad", _ad_sync)
    elif key == "heartbeat":
        _bg("hb", heartbeat)
    elif key == "winget":
        _bg("winget", winget.refresh)
    elif key == "rematch":
        _bg("rematch", winget.rematch_all)
    elif key == "backup":
        _bg("backup", lambda: backup.create(user=user))
    elif key == "dbmaint":
        _bg("dbmaint", lambda: db_maintenance(True, user))


def running():
    return sorted(_running)
