"""Software kiosk: users install approved software themselves – only on the machine they are currently
logged on to (checked live via WinRM). Configurable per package: immediately or only after approval by an
administrator. Uninstalling (via a configured uninstall package) and scheduling a restart.

Who may use the kiosk is determined by AD groups and/or OUs (Einstellungen → Kiosk, see ad.kiosk_match).
Kiosk sessions have no access at all to the administration (auth.current only allows the role "admin").
"""
import concurrent.futures
import datetime
import fnmatch
import re
import socket
import threading
import time

from . import ad, collections_, config, db, deploy, guard, mailer, pklog, remote, steps, util

LIVE_TTL = 90  # seconds: how long "user is logged on to computer X" counts as verified
MAX_CANDIDATES = 6
_live = {}
_live_lock = threading.Lock()
_req_lock = threading.Lock()
GROUP_TTL = 300  # seconds: how long the result "user is in group X" is valid (do not query AD on every click)
_grp_cache = {}
_grp_lock = threading.Lock()  # check + create a request atomically (double click, two tabs)
OPEN_DEPLOY = ("queued", "running", "scheduled")
REBOOT_WHEN = ("now", "1h", "12:00", "18:00", "20:00")


class KioskError(ValueError):
    pass


def enabled():
    return bool(db.setting("kiosk_enabled"))


def _sam(u):
    u = (u or "").strip().lower().split("\\")[-1]
    return u.split("@", 1)[0]


def _domain_sam(entry, computer_name):
    """Logged-on user "DOMAIN\\name" → name – domain accounts only. Local accounts (DOMAIN = computer name) and
    accounts of other domains do not count: otherwise "PC-B\\max" or "FREMD\\max" would count as the kiosk user "max"."""
    e = (entry or "").strip()
    if "\\" in e:
        dom = e.split("\\", 1)[0].strip().lower()
        if dom == (computer_name or "").split(".")[0].lower():
            return None
        if config.NETBIOS and dom != config.NETBIOS.lower():
            return None
    elif "@" in e and config.DOMAIN and e.split("@", 1)[1].lower() != config.DOMAIN.lower():
        return None
    return _sam(e) or None


# --- Which computer belongs to the user? -------------------------------------------------------
def _logged_on(c, fresh=False):
    """Live logged-on users (sAMAccountNames, lowercase) – or None if not checkable (offline, error)."""
    now = time.time()
    with _live_lock:
        hit = _live.get(c["id"])
    if hit and not fresh and now - hit[0] < LIVE_TTL:
        return hit[1]
    from . import presence
    try:
        host = presence.direct_host(c)
    except ValueError:
        return None  # 2.36.3: the address (probably) belongs to a different device
    if guard.paused() or not remote.is_online(host)[1]:
        return None
    try:
        out, _e, _ = remote.ps(host, remote.LOGGEDON_PS, timeout=40)
    except remote.RemoteError:
        return None
    rest = out.split("PKUSERS:", 1)[1] if "PKUSERS:" in out else ""
    line = (rest.splitlines() or [""])[0].strip()
    users = {u for u in (_domain_sam(x, c["name"]) for x in line.split(",") if x.strip()) if u}
    db.ex("UPDATE computers SET current_user=? WHERE id=?", (line or None, c["id"]))
    with _live_lock:
        _live[c["id"]] = (now, users)
    return users


def _reverse_name(ip):
    """Computer name for the browser's address (reverse DNS lookup, at most 2 seconds)."""
    if not ip or ip in ("127.0.0.1", "::1", "?"):
        return ""
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        return ex.submit(socket.gethostbyaddr, ip).result(timeout=2)[0].split(".")[0].lower()
    except Exception:
        return ""
    finally:
        ex.shutdown(wait=False)


def candidates(username, ip):
    """Possible computers of the user: last logged on there (inventory) or the browser's address/name matches."""
    sam = _sam(username)
    found = {}
    # Browser address and name first – the computer the user is sitting at must never be dropped because of the maximum count
    if ip and ip not in ("?",):
        for c in db.q("SELECT * FROM computers WHERE ip_addresses LIKE ? ESCAPE '\\'", (util.like_pattern(ip),)):
            if ip in re.split(r"[\s,;]+", c["ip_addresses"] or ""):
                found[c["id"]] = c
    rn = _reverse_name(ip)
    if rn:
        for c in db.q("SELECT * FROM computers WHERE lower(name)=? OR lower(dns_name) LIKE ?", (rn, rn + ".%")):
            found[c["id"]] = c
    for c in db.q("SELECT * FROM computers WHERE lower(current_user) LIKE ? ESCAPE '\\' ORDER BY last_scan DESC",
                  (util.like_pattern(sam),)):
        if sam in {_sam(x) for x in (c["current_user"] or "").split(",")}:
            found.setdefault(c["id"], c)
    return list(found.values())[:MAX_CANDIDATES]


def computers_for(username, ip, fresh=False):
    """Computers the user is currently logged on to (checked live). (list, hint)"""
    sam = _sam(username)
    out, unknown = [], 0
    for c in candidates(username, ip):
        users = _logged_on(c, fresh)
        if users is None:
            unknown += 1
            continue
        if sam in users:
            out.append({"id": c["id"], "name": c["name"], "model": c.get("model") or "", "os": c.get("os_name") or ""})
    hint = ""
    if not out:
        if guard.paused():
            hint = "POCKET KNIFE kann die Computer gerade nicht prüfen (Dienstkonto-Schutz aktiv). Bitte später erneut versuchen."
        elif unknown:
            hint = "Ihr Computer ist gerade nicht erreichbar. Bitte in einer Minute erneut versuchen."
        else:
            hint = ("Kein Computer gefunden, an dem Sie angemeldet sind. Öffnen Sie den Kiosk bitte an dem Rechner, "
                    "auf dem die Software installiert werden soll.")
    return sorted(out, key=lambda x: x["name"].lower()), hint


def check_computer(username, ip, cid):
    """The user must be logged on to exactly this computer – otherwise no request."""
    if not any(c["id"] == cid for c in computers_for(username, ip)[0]):
        # Check once afresh (the login may have changed in the last 90 seconds)
        if not any(c["id"] == cid for c in computers_for(username, ip, fresh=True)[0]):
            raise KioskError("Sie sind an diesem Computer nicht (mehr) angemeldet.")


# --- Catalog ------------------------------------------------------------------------------------
def _item(pid):
    return db.q1("""SELECT k.*, p.name, p.version, p.description AS pkg_description FROM kiosk_items k
                    JOIN packages p ON p.id = k.package_id WHERE k.package_id=?""", (pid,))


def _detect(row):
    """(winget_id, display name pattern) of the first installation step – to detect whether it is installed."""
    p = steps.normalize(deploy.row_to_package(row))
    for _k, s in steps.iter_steps(p):
        if s.get("type") == "install":
            if s.get("source") == "winget" and s.get("winget_id"):
                return s["winget_id"].lower(), ""
            if s.get("detect_name"):
                return "", s["detect_name"].lower()
    return (row.get("winget_id") or "").lower(), (row.get("detect_name") or "").lower()


def _needs_winget(row):
    """Does the package need winget (install or uninstall via winget)? On Windows before 1809 / Server 2019
    that would not work."""
    if not row:
        return False
    p = steps.normalize(deploy.row_to_package(row))
    return (row.get("type") == "winget"
            or any(s.get("source") == "winget" and s.get("type") in ("install", "uninstall") for _k, s in steps.iter_steps(p)))


def _cache_covers(row, cid):
    """2.35.1: Are all winget installation steps of the package available in the winget cache for this computer?"""
    from . import wingetcache
    c = db.q1("SELECT * FROM computers WHERE id=?", (cid,)) or {}
    p = steps.normalize(deploy.row_to_package(row))
    ws = [s for _k, s in steps.iter_steps(p) if s.get("source") == "winget" and s.get("type") == "install"]
    if row.get("type") == "winget" and not ws:
        ws = [{"type": "install", "source": "winget", "winget_id": row.get("winget_id"),
               "winget_action": row.get("winget_action") or "install", "winget_version": row.get("winget_version")}]
    try:
        return bool(ws) and all(wingetcache.step_for(s, c, p) for s in ws)
    except Exception:
        return False


def _installed(row, software):
    wid, pat = _detect(row)
    for s in software:
        if wid and (s["winget_id"] or "").lower() == wid:
            return s["version"] or True
        if pat and fnmatch.fnmatchcase((s["name"] or "").lower(), pat if any(ch in pat for ch in "*?[") else pat + "*"):
            return s["version"] or True
    return None


def item_groups(item):
    return ad._lines((item or {}).get("groups"))


def _group_ok(username, groups):
    """May the user see this software? Without groups: yes. If AD cannot be checked right now: no (safe way)."""
    if not groups:
        return True
    key = (_sam(username), tuple(sorted(g.lower() for g in groups)))
    now = time.time()
    with _grp_lock:
        hit = _grp_cache.get(key)
    if hit and now - hit[0] < GROUP_TTL:
        return hit[1]
    ok = ad.member_of_any(key[0], groups)
    if ok is None:
        return False  # do not cache – try again on the next call
    with _grp_lock:
        if len(_grp_cache) > 5000:
            _grp_cache.clear()
        _grp_cache[key] = (now, ok)
    return ok


def _available(item, cid, username=None):
    """Software approved for this computer (collection) and – if groups are configured – for this user?"""
    if item["collection_id"] and cid not in collections_.member_ids(item["collection_id"]):
        return False
    return username is None or _group_ok(username, item_groups(item))


def catalog(cid, username=None):
    software = db.q("SELECT name, version, winget_id FROM software WHERE computer_id=?", (cid,))
    last_scan = db.val("SELECT last_scan FROM computers WHERE id=?", (cid,)) or ""
    wok = deploy.winget_ok(db.val("SELECT os_build FROM computers WHERE id=?", (cid,)))
    reqs = {}
    for r in db.q("SELECT * FROM kiosk_requests WHERE computer_id=? AND action IN ('install','uninstall') ORDER BY id", (cid,)):
        reqs[r["package_id"]] = r  # always the latest request
    out = []
    for it in db.q("""SELECT p.*, k.package_id, k.enabled, k.approval, k.category, k.description AS k_description,
                             k.uninstall_package_id, k.collection_id, k.groups, k.icon_at FROM kiosk_items k JOIN packages p ON p.id = k.package_id
                      WHERE k.enabled=1 ORDER BY COALESCE(k.category, ''), p.name COLLATE NOCASE"""):
        if not _available(it, cid, username):
            continue
        if not wok and _needs_winget(it) and not _cache_covers(it, cid):
            continue  # 2.34: winget does not exist on this Windows – do not offer it (2.35.1: except from the winget cache)
        inst = _installed(it, software)
        r = reqs.get(it["package_id"])
        rs = status(r) if r else None
        # Result of the last kiosk request only as long as no newer scan exists – otherwise the inventory applies
        # (otherwise "Neu installieren" would keep showing in the kiosk after an uninstall via the administration)
        if rs and rs["status"] == "success" and rs.get("finished_at") and rs["finished_at"] > last_scan:
            inst = (inst or True) if rs["action"] == "install" else None
        out.append({"package_id": it["package_id"], "name": it["name"], "version": it["version"] or "",
                    "category": it["category"] or "", "description": it["k_description"] or it["description"] or "",
                    "approval": bool(it["approval"]), "can_uninstall": bool(it["uninstall_package_id"]) and (wok or not _needs_winget(
                        db.q1("SELECT * FROM packages WHERE id=?", (it["uninstall_package_id"],)))),
                    "icon": icon_url(it["package_id"], it["icon_at"]),
                    "installed": bool(inst), "installed_version": inst if isinstance(inst, str) else "",
                    "request": rs})
    return out


# --- Status of a request ----------------------------------------------------------------------
STATUS_TEXT = {"pending": "Wartet auf Freigabe", "rejected": "Abgelehnt", "withdrawn": "Zurückgezogen",
               "failed_start": "Konnte nicht gestartet werden", "queued": "In Warteschlange", "running": "Wird ausgeführt",
               "success": "Fertig", "failed": "Fehlgeschlagen", "skipped": "Übersprungen", "cancelled": "Abgebrochen",
               "scheduled": "Geplant"}


def status(r):
    st, msg = r["status"], r["note"] or ""
    t = None
    if st == "started" and r["deployment_id"]:
        t = db.q1("SELECT status, message, finished_at FROM deployment_targets WHERE deployment_id=? ORDER BY id LIMIT 1", (r["deployment_id"],))
        d = db.q1("SELECT status, scheduled_at FROM deployments WHERE id=?", (r["deployment_id"],))
        if d and d["status"] == "scheduled":
            st, msg = "scheduled", d["scheduled_at"] or ""
        elif t:
            st, msg = t["status"], t["message"] or ""
        else:
            st = "cancelled"
    elif st == "started":
        st = "cancelled"
    if st == "starting":
        st = "queued"  # is being started right now (approval/start in progress)
    open_ = st in ("pending", "queued", "running", "scheduled")
    return {"id": r["id"], "action": r["action"], "status": st, "text": STATUS_TEXT.get(st, st), "message": msg[:300],
            "open": open_, "created_at": r["created_at"], "scheduled_at": r["scheduled_at"], "package_name": r["package_name"],
            "computer_name": r["computer_name"], "username": r["username"], "display_name": r["display_name"],
            "decided_by": r["decided_by"], "decided_at": r["decided_at"], "deployment_id": r["deployment_id"],
            "computer_id": r["computer_id"], "package_id": r["package_id"],
            "finished_at": (t or {}).get("finished_at") if r["deployment_id"] else None,
            "reboot_needed": st == "success" and "Neustart erforderlich" in msg}


def my_requests(username, limit=50):
    return [status(r) for r in db.q("SELECT * FROM kiosk_requests WHERE lower(username)=? ORDER BY id DESC LIMIT ?",
                                    (username.lower(), limit))]


# --- Requests ----------------------------------------------------------------------------------
def _limit_check(username):
    mx = db.setting("kiosk_max_per_day") or 10
    n = db.val("SELECT COUNT(*) FROM kiosk_requests WHERE lower(username)=? AND created_at >= ?",
               (username.lower(), util.ago(hours=24)), 0)
    if n >= mx:
        raise KioskError(f"Höchstens {mx} Anfragen in 24 Stunden – bitte wenden Sie sich an die IT.")


def _open_for(cid, pid=None, action=None):
    q = "SELECT * FROM kiosk_requests WHERE computer_id=?" + (" AND package_id=?" if pid else "") + \
        (" AND action=?" if action else "") + " ORDER BY id DESC LIMIT 20"
    args = (cid,) + ((pid,) if pid else ()) + ((action,) if action else ())
    return [r for r in db.q(q, args) if status(r)["open"]]


def request(user, ip, cid, pid, action):
    """New request (install/uninstall). Starts immediately or waits for approval."""
    if not enabled():
        raise KioskError("Der Software-Kiosk ist ausgeschaltet.")
    if action not in ("install", "uninstall"):
        raise KioskError("Unbekannte Aktion.")
    it = _item(pid)
    if not it or not it["enabled"]:
        raise KioskError("Diese Software wird im Kiosk nicht angeboten.")
    c = db.q1("SELECT * FROM computers WHERE id=?", (cid,))
    if not c:
        raise KioskError("Computer nicht gefunden.")
    if not _available(it, cid):
        raise KioskError("Diese Software ist für Ihren Computer nicht freigegeben.")
    if not _group_ok(user["username"], item_groups(it)):
        raise KioskError("Diese Software ist für Sie nicht freigegeben.")
    if not deploy.winget_ok(c["os_build"]):
        upid = pid if action == "install" else it["uninstall_package_id"]
        urow = db.q1("SELECT * FROM packages WHERE id=?", (upid,)) if upid else None
        if urow and _needs_winget(urow) and not (action == "install" and _cache_covers(urow, cid)):
            raise KioskError("Diese Software wird per winget installiert – das gibt es erst ab Windows 10 1809 / Windows Server 2019.")
    if action == "uninstall" and not it["uninstall_package_id"]:
        raise KioskError("Diese Software lässt sich im Kiosk nicht deinstallieren.")
    check_computer(user["username"], ip, cid)
    with _req_lock:  # double click/second tab: checking and creating must not overtake each other
        if _open_for(cid, pid):
            raise KioskError("Für diese Software läuft auf Ihrem Computer bereits eine Anfrage.")
        _limit_check(user["username"])
        rid = db.ex("""INSERT INTO kiosk_requests(created_at, username, display_name, computer_id, computer_name, package_id,
                       package_name, action, status) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (util.now(), user["username"], user.get("display_name") or user["username"], cid, c["name"], pid,
                     it["name"], action, "pending" if it["approval"] else "starting"))
    what = "Installation" if action == "install" else "Deinstallation"
    if it["approval"]:
        pklog.info("kiosk", f"{what} von „{it['name']}“ auf {c['name']} angefragt – wartet auf Freigabe.",
                   user=user["username"], computer_id=cid)
        _notify(rid)
    else:
        _start(rid, None)
    return status(db.q1("SELECT * FROM kiosk_requests WHERE id=?", (rid,)))


def _start(rid, decided_by):
    r = db.q1("SELECT * FROM kiosk_requests WHERE id=?", (rid,))
    it = _item(r["package_id"])
    pid = r["package_id"] if r["action"] == "install" else (it["uninstall_package_id"] if it else None)
    row = db.q1("SELECT * FROM packages WHERE id=?", (pid,)) if pid else None
    what = "Installation" if r["action"] == "install" else "Deinstallation"
    try:
        if not row:
            raise KioskError("Das zugehörige Paket gibt es nicht mehr.")
        did = deploy.create(deploy.row_to_package(row), [r["computer_id"]], f"Kiosk: {r['username']}", source="kiosk",
                            name=f"Kiosk: {r['package_name']} ({r['username']})", package_id=row["id"],
                            target_label=r["computer_name"])
    except Exception as e:
        from . import integrity
        # Kiosk users see the note: on a break in the repository no paths, only "derzeit nicht verfügbar"
        note = ("Die Software ist derzeit nicht verfügbar – die IT ist informiert." if isinstance(e, integrity.IntegrityError)
                else str(e))
        db.ex("UPDATE kiosk_requests SET status='failed_start', note=?, decided_by=COALESCE(?, decided_by), "
              "decided_at=COALESCE(decided_at, ?) WHERE id=?", (note[:300], decided_by, util.now() if decided_by else None, rid))
        if isinstance(e, integrity.IntegrityError):
            # Details (paths) only without a computer reference – OU admins can also see a computer's log
            pklog.warn("kiosk", f"{what} von „{r['package_name']}“: {e}", user=r["username"])
            e = integrity.NEUTRAL
        pklog.warn("kiosk", f"{what} von „{r['package_name']}“ auf {r['computer_name']} konnte nicht gestartet werden: {e}",
                   user=r["username"], computer_id=r["computer_id"])
        return None
    db.ex("UPDATE kiosk_requests SET status='started', deployment_id=?, decided_by=COALESCE(?, decided_by), "
          "decided_at=CASE WHEN ? IS NULL THEN decided_at ELSE ? END WHERE id=?", (did, decided_by, decided_by, util.now(), rid))
    pklog.info("kiosk", f"{what} von „{r['package_name']}“ auf {r['computer_name']} gestartet (Bereitstellung #{did})"
                        + (f", freigegeben von {decided_by}" if decided_by else "") + ".",
               user=r["username"], computer_id=r["computer_id"])
    return did


def withdraw(user, rid):
    r = db.q1("SELECT * FROM kiosk_requests WHERE id=? AND lower(username)=?", (rid, user["username"].lower()))
    if not r:
        raise KioskError("Anfrage nicht gefunden.")
    if r["status"] == "pending":
        db.ex("UPDATE kiosk_requests SET status='withdrawn' WHERE id=? AND status='pending'", (rid,))
        return True
    if r["status"] == "started" and r["deployment_id"] and \
            db.val("SELECT status FROM deployments WHERE id=?", (r["deployment_id"],)) == "scheduled":
        deploy.cancel(r["deployment_id"], f"Kiosk: {user['username']}")  # e.g. scheduled restart
        return True
    raise KioskError("Diese Anfrage läuft bereits und lässt sich nicht mehr zurückziehen.")


def decide(rid, approve, admin, note=""):
    r = db.q1("SELECT * FROM kiosk_requests WHERE id=?", (rid,))
    if not r or r["status"] != "pending":
        raise KioskError("Anfrage nicht gefunden oder bereits entschieden.")
    if approve:
        # Take over atomically: two simultaneous approvals or a withdrawal during approval start nothing twice
        if db.ex_count("UPDATE kiosk_requests SET status='starting' WHERE id=? AND status='pending'", (rid,)) != 1:
            raise KioskError("Anfrage nicht gefunden oder bereits entschieden.")
        if _start(rid, admin) is None:
            raise KioskError(db.val("SELECT note FROM kiosk_requests WHERE id=?", (rid,)) or "Start fehlgeschlagen.")
    else:
        if db.ex_count("UPDATE kiosk_requests SET status='rejected', decided_by=?, decided_at=?, note=? WHERE id=? AND status='pending'",
                       (admin, util.now(), (note or "").strip()[:300] or None, rid)) != 1:
            raise KioskError("Anfrage nicht gefunden oder bereits entschieden.")
        pklog.info("kiosk", f"Anfrage „{r['package_name']}“ von {r['username']} auf {r['computer_name']} abgelehnt.",
                   user=admin, computer_id=r["computer_id"])
    return status(db.q1("SELECT * FROM kiosk_requests WHERE id=?", (rid,)))


def _notify(rid):
    if not db.setting("kiosk_notify") or not mailer.configured():
        return
    r = db.q1("SELECT * FROM kiosk_requests WHERE id=?", (rid,))

    def go():
        try:
            to = mailer.recipients_or_default(db.setting("kiosk_notify_to"))
            what = "installieren" if r["action"] == "install" else "deinstallieren"
            link = (mailer.base_url() or "").rstrip("/") + "/#/kiosk"
            from html import escape as esc
            html = mailer.wrap("Software-Kiosk: Freigabe erbeten",
                               f"<p><b>{esc(r['display_name'] or r['username'])}</b> ({esc(r['username'])}) möchte "
                               f"<b>{esc(r['package_name'])}</b> auf <b>{esc(r['computer_name'])}</b> {what}.</p>"
                               f"<p><a href=\"{esc(link)}\">In POCKET KNIFE freigeben oder ablehnen</a></p>")
            mailer.send(to, f"Kiosk: {r['package_name']} für {r['computer_name']} ({r['username']})", html, category="kiosk")
        except Exception as e:
            pklog.warn("kiosk", f"Mail zur Kiosk-Anfrage #{rid} nicht gesendet: {e}")
    threading.Thread(target=go, daemon=True, name="kiosk-mail").start()


# --- Restart ----------------------------------------------------------------------------------
def reboot_info(cid):
    c = db.q1("SELECT extra, last_boot, last_scan FROM computers WHERE id=?", (cid,)) or {}
    # A successful restart from the kiosk satisfies the need – even before the next scan updates last_boot/pending_reboot
    # (otherwise the kiosk would ask for a restart again right after the restart)
    done = db.val("""SELECT MAX(t.finished_at) FROM kiosk_requests r JOIN deployment_targets t ON t.deployment_id = r.deployment_id
                     WHERE r.computer_id=? AND r.action='reboot' AND t.status='success'""", (cid,)) or ""
    pending = False
    if not (done and done > (c.get("last_scan") or "")):
        try:
            import json
            pending = bool(json.loads(c.get("extra") or "{}").get("pending_reboot"))
        except ValueError:
            pass
    since = max(c.get("last_boot") or "", done)
    for r in db.q("SELECT * FROM kiosk_requests WHERE computer_id=? AND action IN ('install','uninstall') AND status='started' "
                  "AND created_at >= ? ORDER BY id DESC LIMIT 20", (cid, since)):
        if status(r)["reboot_needed"]:
            pending = True
            break
    planned = next((status(r) for r in db.q("SELECT * FROM kiosk_requests WHERE computer_id=? AND action='reboot' "
                                             "ORDER BY id DESC LIMIT 5", (cid,)) if status(r)["open"]), None)
    return {"needed": pending, "planned": planned}


def _when(code, now=None):
    now = now or datetime.datetime.now()
    if code == "now":
        return None
    if code == "1h":
        return now + datetime.timedelta(hours=1)
    if re.match(r"^\d{2}:\d{2}$", code or ""):
        hh, mm = (int(x) for x in code.split(":"))
        t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if t <= now + datetime.timedelta(minutes=5):
            raise KioskError(f"{code} Uhr ist heute schon vorbei – bitte eine andere Zeit wählen.")
        return t
    raise KioskError("Ungültige Zeit.")


def plan_reboot(user, ip, cid, when):
    if not enabled():
        raise KioskError("Der Software-Kiosk ist ausgeschaltet.")
    if when not in REBOOT_WHEN:
        raise KioskError("Ungültige Zeit.")
    c = db.q1("SELECT * FROM computers WHERE id=?", (cid,))
    if not c:
        raise KioskError("Computer nicht gefunden.")
    check_computer(user["username"], ip, cid)
    # Audit 2.29: never restart servers/terminal servers and never if someone else is still logged on
    if "server" in str(c.get("os_name") or "").lower():
        raise KioskError("Server lassen sich über den Kiosk nicht neu starten – bitte an die IT wenden.")
    # count all logins – also local accounts and those of other domains (which the kiosk otherwise ignores)
    raw = db.val("SELECT current_user FROM computers WHERE id=?", (cid,)) or ""
    me = _sam(user["username"])
    others = {x.strip().lower() for x in raw.split(",") if x.strip() and _domain_sam(x, c["name"]) != me}
    if others:
        raise KioskError("An diesem Computer sind noch andere Benutzer angemeldet – Neustart über den Kiosk nicht möglich.")
    at = _when(when)
    delay = 120 if at is None else 300
    from . import rebootmsg
    msg = rebootmsg.text("kiosk", sekunden=delay, benutzer=user.get("display_name") or user["username"])
    pkg = steps.clean_package({"name": "Neustart (Kiosk)", "rescan": 0,
                               "steps": {"main": [{"type": "reboot", "delay_sec": delay, "wait_minutes": 15, "message": msg}]}})
    with _req_lock:
        if _open_for(cid, action="reboot"):
            raise KioskError("Für diesen Computer ist bereits ein Neustart geplant.")
        _limit_check(user["username"])
        rid = db.ex("""INSERT INTO kiosk_requests(created_at, username, display_name, computer_id, computer_name, package_name,
                       action, status, scheduled_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (util.now(), user["username"], user.get("display_name") or user["username"], cid, c["name"], "Neustart",
                     "reboot", "starting", util.fmt(at) if at else None))
    try:
        did = deploy.create(pkg, [cid], f"Kiosk: {user['username']}", scheduled_at=util.fmt(at) if at else None, source="kiosk",
                            name=f"Kiosk: Neustart ({user['username']})", target_label=c["name"])
    except Exception as e:
        # Otherwise an open request would remain that blocks every further restart
        db.ex("UPDATE kiosk_requests SET status='failed_start', note=? WHERE id=?", (str(e)[:300], rid))
        pklog.warn("kiosk", f"Neustart von {c['name']} konnte nicht geplant werden: {e}", user=user["username"], computer_id=cid)
        raise KioskError(f"Neustart konnte nicht geplant werden: {e}")
    db.ex("UPDATE kiosk_requests SET status='started', deployment_id=? WHERE id=?", (did, rid))
    pklog.info("kiosk", f"Neustart von {c['name']} " + (f"geplant für {util.fmt(at)}" if at else "in 2 Minuten") + ".",
               user=user["username"], computer_id=cid)
    return status(db.q1("SELECT * FROM kiosk_requests WHERE id=?", (rid,)))


# --- Icons ---------------------------------------------------------------------------
ICON_MAX = 512 * 1024


def icon_type(data):
    """Detect the image format from the file content – only real raster images (no SVG: could contain scripts)."""
    b = bytes(data[:16])
    if b.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if b.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    if b[:4] == b"\x00\x00\x01\x00":
        return "image/x-icon"
    return None


def set_icon(pid, data):
    if not db.val("SELECT 1 FROM kiosk_items WHERE package_id=?", (pid,)):
        raise KioskError("Die Software ist nicht im Kiosk.")
    if not data:
        raise KioskError("Keine Bilddatei erhalten.")
    if len(data) > ICON_MAX:
        raise KioskError(f"Bild zu groß (höchstens {ICON_MAX // 1024} KB) – am besten ein PNG mit 128 × 128 Pixeln.")
    typ = icon_type(data)
    if not typ:
        head = bytes(data[:300]).lower()
        if b"<svg" in head or b"<?xml" in head:
            raise KioskError("SVG wird aus Sicherheitsgründen nicht angenommen – bitte als PNG speichern.")
        raise KioskError("Unbekanntes Bildformat – erlaubt sind PNG, JPEG, GIF, WebP und ICO.")
    db.ex("UPDATE kiosk_items SET icon=?, icon_type=?, icon_at=? WHERE package_id=?", (bytes(data), typ, util.now(), pid))
    return typ


def clear_icon(pid):
    db.ex("UPDATE kiosk_items SET icon=NULL, icon_type=NULL, icon_at=NULL WHERE package_id=?", (pid,))


def get_icon(pid):
    r = db.q1("SELECT icon, icon_type FROM kiosk_items WHERE package_id=? AND icon IS NOT NULL", (pid,))
    return (r["icon"], r["icon_type"]) if r else (None, None)


def icon_url(pid, icon_at):
    return f"/api/kiosk/icon/{pid}?v={re.sub(r'[^0-9]', '', icon_at or '')}" if icon_at else None


# --- Administration --------------------------------------------------------------------------------
def admin_items():
    rows = db.q("""SELECT k.package_id, k.enabled, k.approval, k.category, k.description, k.uninstall_package_id,
                          k.collection_id, k.groups, k.updated_at, k.icon_at, p.name, p.version, u.name AS uninstall_name, c.name AS collection_name
                   FROM kiosk_items k JOIN packages p ON p.id = k.package_id
                   LEFT JOIN packages u ON u.id = k.uninstall_package_id LEFT JOIN collections c ON c.id = k.collection_id
                   ORDER BY COALESCE(k.category, ''), p.name COLLATE NOCASE""")
    stats = {r["package_id"]: r for r in db.q("""SELECT package_id, COUNT(*) n, SUM(status='pending') pend FROM kiosk_requests
                                                 WHERE action IN ('install','uninstall') GROUP BY package_id""")}
    for r in rows:
        r["icon"] = icon_url(r["package_id"], r.pop("icon_at"))
        s = stats.get(r["package_id"]) or {}
        r["requests"] = s.get("n") or 0
        r["pending"] = s.get("pend") or 0
    return rows


def save_item(pid, data):
    if not db.val("SELECT 1 FROM packages WHERE id=?", (pid,)):
        raise KioskError("Paket nicht gefunden.")
    un = data.get("uninstall_package_id")
    un = int(un) if str(un or "").isdigit() and int(un) else None
    if un and not db.val("SELECT 1 FROM packages WHERE id=?", (un,)):
        raise KioskError("Deinstallationspaket nicht gefunden.")
    if un == pid:
        raise KioskError("Das Deinstallationspaket muss ein anderes Paket sein.")
    col = data.get("collection_id")
    col = int(col) if str(col or "").isdigit() and int(col) else None
    if col and not db.val("SELECT 1 FROM collections WHERE id=?", (col,)):
        raise KioskError("Sammlung nicht gefunden.")
    groups = clean_groups(data.get("groups"))
    db.ex("""INSERT INTO kiosk_items(package_id, enabled, approval, category, description, uninstall_package_id, collection_id,
             groups, updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(package_id) DO UPDATE SET enabled=excluded.enabled,
             approval=excluded.approval, category=excluded.category, description=excluded.description,
             uninstall_package_id=excluded.uninstall_package_id, collection_id=excluded.collection_id,
             groups=excluded.groups, updated_at=excluded.updated_at""",
          (pid, 1 if data.get("enabled", True) else 0, 1 if data.get("approval") else 0,
           (data.get("category") or "").strip()[:60] or None, (data.get("description") or "").strip()[:1000] or None,
           un, col, groups, util.now()))
    with _grp_lock:
        _grp_cache.clear()  # changed groups take effect immediately


def clean_groups(value):
    """Groups per line (name or DN), without duplicates; None = all kiosk users."""
    if isinstance(value, (list, tuple)):
        value = "\n".join(str(x) for x in value)
    out, seen = [], set()
    for g in ad._lines(value):
        if len(g) > 300 or any(ord(ch) < 32 for ch in g):
            raise KioskError(f"Ungültiger Gruppenname: „{g[:60]}“")
        if g.lower() not in seen:
            seen.add(g.lower())
            out.append(g)
    if len(out) > 50:
        raise KioskError("Höchstens 50 Gruppen je Software.")
    return "\n".join(out) or None


def admin_requests(limit=300):
    rows = db.q("SELECT * FROM kiosk_requests ORDER BY (status='pending') DESC, id DESC LIMIT ?", (limit,))
    return [status(r) for r in rows]


def check_settings(key, value):
    """Validation of the kiosk settings (saving settings)."""
    if key == "kiosk_ous":
        for ln in [x.strip() for x in str(value or "").splitlines() if x.strip()]:
            if "=" not in ln or not re.search(r"(?i)\b(ou|dc|cn)=", ln):
                raise KioskError(f"Keine gültige OU: „{ln}“ (z. B. OU=Mitarbeiter,DC=firma,DC=local).")
    if key in ("kiosk_groups", "kiosk_ous") and len(str(value or "")) > 4000:
        raise KioskError("Zu viele Einträge.")
    if key == "kiosk_notify_to" and str(value or "").strip():
        mailer.parse_recipients(value, "Empfänger")
