"""Background tasks: AD sync, heartbeat, scans, scheduled deployments, rules, maintenance."""
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
    with _bg_lock:  # check and register atomically - otherwise the minute thread and a button press start the same task twice
        if key in _running:
            return
        _running.add(key)

    def run():
        from . import loadstats
        try:
            with loadstats.task(key.split("-")[0]):  # "start-17" → "start": count deployment starts together
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
        results = list(ex.map(lambda c: (c, presence.probe(c)), comps))  # incl. check that this computer really answers
    now = util.now()
    came_online = []
    with db.tx():
        for c, (online, winrm) in results:
            # Compare with the current state in the DB (a scan may have changed it in the meantime)
            if presence.update(c["id"], online, winrm, None, now):
                came_online.append(c["id"])
    state["heartbeat"] = now
    # Heartbeat triggers of the scan profiles and schedules: computers whose WinRM has just become reachable
    presence.notify(came_online)
    presence.after_heartbeat([c["id"] for c, (online, winrm) in results if winrm])


def _heartbeat_only():
    heartbeat()


def _profiles():
    # Each step on its own: an error (e.g. "database is locked") must not make the others skip
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
    if days and days > 0:  # 0 = never delete (as with the other retention periods)
        db.ex("DELETE FROM logs WHERE ts < ?", (util.ago(days=days),))
    db.ex("DELETE FROM sessions WHERE expires < ?", (util.now(),))
    days = db.setting("scan_log_retention_days")
    if days and days > 0:
        # Scan log (history) - the scanned data itself is kept
        db.ex("DELETE FROM scan_history WHERE ts < ?", (util.ago(days=days),))
        db.ex("DELETE FROM logs WHERE category='scan' AND ts < ?", (util.ago(days=days),))
    days = db.setting("software_changes_retention_days")
    if days and days > 0:
        db.ex("DELETE FROM software_changes WHERE ts < ?", (util.ago(days=days),))
    days = db.setting("deployment_retention_days")
    if days and days > 0:
        db.ex("DELETE FROM deployments WHERE created_at < ? AND status NOT IN ('running','queued','scheduled')",
              (util.ago(days=days),))
        # 2.36.3 (Audit, privacy): kiosk requests (user, display name, computer) with the same period -
        # open requests stay until they are decided
        db.ex("DELETE FROM kiosk_requests WHERE created_at < ? AND COALESCE(status,'') NOT IN ('pending','starting')",
              (util.ago(days=days),))
    # Leftovers without a reference (e.g. from pre-releases or after aborts)
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
    """Remove leftover intermediate files (aborted downloads/uploads, failed index updates)."""
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
    """Clean-up + database maintenance (automatically at night, or manually under Einstellungen → Wartung)."""
    _cleanup()
    busy = ""
    if vacuum is None:
        # VACUUM locks the database; not during running deployments or scans (otherwise "database is locked")
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
    """Once a day from the configured hour (default 03:00) - even if the hour was missed due to a clock change
    or restart, then simply later in the day."""
    import datetime
    now = datetime.datetime.now()
    h = int(db.setting("db_maintenance_hour"))
    if not 0 <= now.hour - h < 3:  # only in the 3 hours from the maintenance hour (VACUUM briefly locks the database)
        return False
    last = (db.setting_raw("db_maintenance_last") or "")[:10]
    return last != now.strftime("%Y-%m-%d")


def _mail():
    """Report sending and notifications (database + SMTP only, no access to clients)."""
    for name, fn in (("Berichtsversand", notify.evaluate), ("Benachrichtigungen", notify.check_alerts)):
        try:
            fn()
        except Exception as e:
            pklog.error("mail", f"{name}: {e}")


def tick():
    state["last_tick"] = util.now()
    try:  # 2.31: load peaks with what was running at the time (Einstellungen → Wartung)
        from . import inventory, loadstats
        loadstats.sample(running(), len(inventory.scanning_ids()),
                         db.val("SELECT COUNT(*) FROM deployments WHERE status='running'", (), 0))
    except Exception:
        pass
    if _due("idle", 1):
        _last["idle"] = time.time()
        try:
            from . import auth
            auth.expire_idle()  # logout on inactivity (also for open remote sessions)
        except Exception as e:
            pklog.warn("auth", f"Prüfung auf inaktive Sitzungen fehlgeschlagen: {e}")
        # Check logged-in sessions against the AD (groups changed, account disabled) - in the background, each
        # session only every 2 minutes; the service account protection applies as everywhere (on a fault: no login attempts)
        from . import auth as _auth
        _bg("sessions", _auth.recheck_all)
    # E-mail also while the service account protection is active - that is exactly when the notification should come
    if _due("mail", 1) and mailer.configured():
        _last["mail"] = time.time()
        _bg("mail", _mail)
    if guard.paused():
        # Service account protection active: no logins generated, only heartbeat (TCP) and winget index
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
        # in the background: before the start the repository files are checked (2.26), which must not hold up the cycle
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
        _bg("update_check", news.check)  # itself asks GitHub only once a day (CHECK_HOURS)
    if _due("repo_idents", 10):
        _last["repo_idents"] = time.time()
        from . import repofiles
        _bg("repo_idents", repofiles.backfill)  # add the identifiers for the duplicate lock retroactively
    if _due("repo_uploads", 60):
        _last["repo_uploads"] = time.time()
        from . import repofiles as _rf
        _bg("repo_uploads", _rf.cleanup_uploads)  # clean up aborted uploads after 24 hours
    if _due("integrity_full", 10):
        _last["integrity_full"] = time.time()
        from . import integrity as _ig
        if db.setting("integrity_full_enabled") and _ig.in_window():
            _bg("integrity_full", _ig.full_check)
    if db.setting("winget_auto_refresh") and _due("winget", db.setting("winget_refresh_hours") * 60):
        _last["winget"] = time.time()
        _bg("winget", winget.refresh)
    if _due("updstatus", 24 * 60):
        # 2.40: update status - Microsoft data (optional), evaluate all computers, ready-made collections
        _last["updstatus"] = time.time()
        from . import updstatus
        _bg("updstatus", updstatus.refresh)
    if db.setting("winget_bootstrap") and _due("wingetboot", 24 * 60):
        # 2.37: keep the app installer for computers without winget up to date once a day (download only on a new release)
        _last["wingetboot"] = time.time()
        from . import wingetboot
        _bg("wingetboot", lambda: wingetboot.refresh())
    if db.setting("winget_cache_auto") and _due("wingetcache", 24 * 60):
        # 2.35: check the winget cache for new versions once a day (only if there are entries)
        _last["wingetcache"] = time.time()
        from . import wingetcache
        if db.val("SELECT COUNT(*) FROM winget_cache", default=0):
            _bg("wingetcache", wingetcache.refresh_all)
    if _due("client_cleanup", 5) and db.val("SELECT COUNT(*) FROM client_cleanup", default=0):
        # Remove leftovers of interrupted runs (restart of POCKET KNIFE) on reachable computers
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
    # Do not reload the index immediately if it is younger than the interval
    st = winget.status()
    if st["updated"]:
        age = time.time() - time.mktime(time.strptime(st["updated"], "%Y-%m-%d %H:%M:%S"))
        _last["winget"] = time.time() - age
    _last["wingetboot"] = time.time() - (24 * 60 - 40) * 60  # 2.37: first check 40 minutes after startup
    _last["updstatus"] = time.time() - (24 * 60 - 3) * 60   # 2.40: update status 3 minutes after startup
    _last["wingetcache"] = time.time() - (24 * 60 - 30) * 60  # 2.35: first check of the winget cache 30 minutes after startup
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
