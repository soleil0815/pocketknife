"""Reachability of the computers: store online/WinRM and report "just became reachable".

A computer counts as newly reachable as soon as WinRM was unreachable before and is reachable now - no matter whether
the heartbeat, a scan or a manual heartbeat detects it. At boot, Windows often opens SMB earlier than
WinRM; the computer is then already "online" but only usable for scans and deployments once WinRM is up.
"""
import datetime
import threading

from . import config, db, guard, pklog, util


def update(cid, online, winrm, prev_winrm=None, now=None):
    """Store the status. True if WinRM just became reachable."""
    if prev_winrm is None:
        prev_winrm = db.val("SELECT winrm FROM computers WHERE id=?", (cid,), 0)
    now = now or util.now()
    came = bool(winrm) and not prev_winrm
    db.ex("UPDATE computers SET online=?, winrm=?, last_seen=CASE WHEN ? THEN ? ELSE last_seen END, "
          "online_since=CASE WHEN ? THEN ? WHEN ? THEN NULL ELSE online_since END WHERE id=?",
          (1 if online else 0, 1 if winrm else 0, 1 if online else 0, now, 1 if came else 0, now, 0 if winrm else 1, cid))
    return came


def after_heartbeat(online_ids, background=False):
    """After a heartbeat: schedules for reachable targets that are not yet served or are queued."""
    ids = sorted(set(online_ids))
    if not ids or guard.paused():
        return

    def run():
        from . import schedules
        try:
            schedules.on_heartbeat(ids)
        except Exception as e:
            pklog.error("deploy", f"Heartbeat-Auslöser (Zeitpläne) fehlgeschlagen: {e}")
    if background:
        threading.Thread(target=run, name="presence-hb", daemon=True).start()
    else:
        run()


def notify(ids, background=False):
    """Run the heartbeat triggers of the scan profiles and schedules for newly reachable computers."""
    ids = sorted(set(ids))
    if not ids or guard.paused():
        return

    def run():
        from . import profiles, schedules
        try:
            profiles.on_online(ids)
        except Exception as e:
            pklog.error("scan", f"Heartbeat-Auslöser (Scanprofile) fehlgeschlagen: {e}")
        try:
            schedules.on_online(ids)
        except Exception as e:
            pklog.error("deploy", f"Heartbeat-Auslöser (Zeitpläne) fehlgeschlagen: {e}")
    if background:
        threading.Thread(target=run, name="presence", daemon=True).start()
    else:
        run()


# --- Does this computer really answer at the address? ---------------------------------------
# A stale DNS entry often points to an address that DHCP has long since given to another device (printer, phone,
# NAS, foreign PC). Then "something" answers and the computer would appear online - scans and deployments would, in
# the worst case, end up on the wrong device. Hence: new address (or not confirmed for a long time) -> ask briefly.
PROBLEM_TEXT = {
    "other": "Adresse gehört zu einem anderen Gerät",
    "nodomain": "Gerät unter dieser Adresse ist nicht Mitglied der Domäne",
    "unverified": "Anmeldung abgelehnt – Gerät nicht bestätigt",
    "unconfirmed": "Unter der Adresse antwortet vermutlich ein anderes Gerät",
}
PROBLEM_RECHECK_MIN = 60  # ask problem cases again at most hourly (no logon attempts at foreign devices every minute)
_ident_sem = threading.Semaphore(8)


def _short(n):
    return (n or "").strip().split(".")[0].lower()


def _domain_ok(dom):
    want = (config.DOMAIN or "").strip().lower()
    d = (dom or "").strip().lower()
    return not want or d == want or d.endswith("." + want) or want.endswith("." + d) or _short(d) == _short(want)


def _age_min(ts, now_dt):
    t = util.parse(ts) if ts else None
    return (now_dt - t).total_seconds() / 60 if t else None


def _set_addr(cid, _row=None, **kw):
    # Write only what changed - the heartbeat checks many computers in parallel, needless writes lock SQLite
    if _row is not None:
        kw = {k: v for k, v in kw.items() if k not in _row or _row.get(k) != v}
    if kw:
        db.ex(f"UPDATE computers SET {', '.join(k + '=?' for k in kw)} WHERE id=?", tuple(kw.values()) + (cid,))


def probe(c, now=None, force=False):
    """Reachability including the identity check. Returns (online, winrm) and stores resolved_ip/addr_problem.
    Without the check (setting off) only TCP as before."""
    from . import dnsres, remote
    now = now or util.now()
    now_dt = util.parse(now) or datetime.datetime.now()
    host = c["dns_name"] or c["name"]
    ip = dnsres.resolve(host, fresh=force)
    online, winrm = remote.is_online(host, ip=ip) if ip else (False, False)
    fields = {"resolved_ip": ip}
    if not db.setting("identity_check") or not online:
        _set_addr(c["id"], c, **fields)
        return online, winrm
    prob = c.get("addr_problem") or ""
    verified_ip = c.get("verified_ip") or ""
    if not winrm:
        # SMB/RPC only: cannot be verified. If a different address was confirmed, this is very likely another device.
        if verified_ip and verified_ip != ip:
            fields.update(addr_problem="unconfirmed", addr_detail=f"Unter {ip} antwortet ein Gerät ohne WinRM – bestätigt "
                                                                  f"war zuletzt {verified_ip}. DNS-Eintrag veraltet oder kein Windows-Gerät?")
            _set_addr(c["id"], c, **fields)
            return False, False
        if prob == "unconfirmed":
            fields.update(addr_problem=None, addr_detail=None)
        _set_addr(c["id"], c, **fields)
        return online, winrm
    recheck_h = max(1, int(db.setting("identity_recheck_hours") or 24))
    age = _age_min(c.get("verified_at"), now_dt)
    if not force and not prob and verified_ip == ip and age is not None and age < recheck_h * 60:
        _set_addr(c["id"], c, **fields)
        return True, True
    checked = _age_min(c.get("addr_checked_at"), now_dt)
    if not force and prob and c.get("addr_ip") == ip and checked is not None and checked < PROBLEM_RECHECK_MIN:
        _set_addr(c["id"], c, **fields)
        return _verdict(prob)
    with _ident_sem:
        r = remote.identify(host)
    fields.update(addr_checked_at=now, addr_ip=ip)
    if r.get("error") in ("paused", "other"):
        # Cannot be verified (service account protection, timeout): stay with the previous result
        _set_addr(c["id"], c, **fields)
        if prob:
            return _verdict(prob)
        # Audit 2.29: new, never confirmed address -> do not treat as confirmed (reachable but not usable
        # until the check succeeds on the next pass)
        return (True, False) if verified_ip and verified_ip != ip else (True, True)
    if r.get("error") in ("auth", "krb_modified"):
        fields.update(addr_problem="unverified",
                      addr_detail=(f"Unter {ip} kann das Gerät das Kerberos-Ticket für {c['name']} nicht lesen – der DNS-Eintrag "
                                   "zeigt vermutlich auf ein anderes Gerät, oder die Vertrauensstellung des Computers zur Domäne "
                                   "ist defekt (auf dem Client: Test-ComputerSecureChannel -Repair)."
                                   if r.get("error") == "krb_modified" else
                                   f"Unter {ip} lehnt das Gerät die Anmeldung des Dienstkontos ab – es gehört vermutlich nicht "
                                   "zur Domäne (oder das Dienstkonto ist dort kein Administrator)."))
        _set_addr(c["id"], c, **fields)
        _log_problem(c, prob, "unverified", fields["addr_detail"])
        return _verdict("unverified")
    if _short(r["name"]) != _short(c["name"]):
        fields.update(addr_problem="other", addr_detail=f"Unter {ip} antwortet „{r['name']}“ statt {c['name']} – "
                                                         "der DNS-Eintrag ist vermutlich veraltet.")
    elif not r.get("joined") or not _domain_ok(r.get("domain")):
        fields.update(addr_problem="nodomain", addr_detail=f"Unter {ip} antwortet {r['name']}, aber nicht als Mitglied der "
                                                            f"Domäne {config.DOMAIN or ''} ({r.get('domain') or 'Arbeitsgruppe'}).".replace(" )", ")"))
    else:
        fields.update(addr_problem=None, addr_detail=None, verified_ip=ip, verified_at=now)
        _set_addr(c["id"], c, **fields)
        if prob:
            pklog.info("system", f"{c['name']}: Adresse {ip} bestätigt – Hinweis „{PROBLEM_TEXT.get(prob, prob)}“ aufgehoben.",
                       computer_id=c["id"])
        return True, True
    _set_addr(c["id"], c, **fields)
    _log_problem(c, prob, fields["addr_problem"], fields["addr_detail"])
    return _verdict(fields["addr_problem"])


def direct_host(c):
    """2.36.3 (Audit): Address for direct actions (files, services, processes, tools, disk space, remote control, restart ...).
    As with deployment and scan, never connect with the service account (or custom credentials) to a device that the
    identity check has marked as foreign/unconfirmed - otherwise e.g. a changed dNSHostName reaches another
    server (\\\\server\\C$, WinRM). With the check switched off, an old note no longer applies."""
    host = (c.get("dns_name") or c.get("name") or "").strip()
    prob = c.get("addr_problem") if "addr_problem" in c else (
        (db.q1("SELECT addr_problem, addr_detail FROM computers WHERE id=?", (c["id"],)) or {}).get("addr_problem")
        if c.get("id") else None)
    if prob and db.setting("identity_check"):
        detail = c.get("addr_detail")
        if detail is None and c.get("id"):
            detail = (db.q1("SELECT addr_detail FROM computers WHERE id=?", (c["id"],)) or {}).get("addr_detail")
        raise ValueError(f"Nicht ausgeführt – {detail or PROBLEM_TEXT.get(prob, prob)}")
    return host


def _verdict(prob):
    """(online, winrm) for a known problem case: wrong device/no domain -> offline; unconfirmed ->
    reachable but not usable (no scans/deployments)."""
    if prob in ("other", "nodomain", "unconfirmed"):
        return False, False
    if prob == "unverified":
        return True, False
    return True, True


def _log_problem(c, old, new, detail):
    if new and new != old:
        pklog.warn("system", f"{c['name']}: {PROBLEM_TEXT.get(new, new)}. {detail}", computer_id=c["id"])


COLUMNS = "id, name, dns_name, online, winrm, resolved_ip, verified_ip, verified_at, addr_problem, addr_detail, addr_ip, addr_checked_at"
