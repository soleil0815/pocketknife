"""Erreichbarkeit der Computer: online/WinRM speichern und „gerade erreichbar geworden“ melden.

Ein Computer gilt als neu erreichbar, sobald WinRM vorher nicht erreichbar war und jetzt ist – egal, ob das der
Heartbeat, ein Scan oder ein manueller Heartbeat feststellt. Beim Hochfahren öffnet Windows SMB oft früher als
WinRM; dann ist der Computer schon „online“, aber erst mit WinRM für Scans und Bereitstellungen nutzbar.
"""
import datetime
import threading

from . import config, db, guard, pklog, util


def update(cid, online, winrm, prev_winrm=None, now=None):
    """Status speichern. True, wenn WinRM gerade erreichbar geworden ist."""
    if prev_winrm is None:
        prev_winrm = db.val("SELECT winrm FROM computers WHERE id=?", (cid,), 0)
    now = now or util.now()
    came = bool(winrm) and not prev_winrm
    db.ex("UPDATE computers SET online=?, winrm=?, last_seen=CASE WHEN ? THEN ? ELSE last_seen END, "
          "online_since=CASE WHEN ? THEN ? WHEN ? THEN NULL ELSE online_since END WHERE id=?",
          (1 if online else 0, 1 if winrm else 0, 1 if online else 0, now, 1 if came else 0, now, 0 if winrm else 1, cid))
    return came


def after_heartbeat(online_ids, background=False):
    """Nach einem Heartbeat: Zeitpläne für erreichbare, noch nicht versorgte bzw. vorgemerkte Ziele."""
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
    """Heartbeat-Auslöser der Scanprofile und Zeitpläne für neu erreichbare Computer ausführen."""
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


# --- Antwortet unter der Adresse wirklich dieser Computer? ---------------------------------------
# Ein veralteter DNS-Eintrag zeigt oft auf eine Adresse, die DHCP längst einem anderen Gerät gegeben hat (Drucker, Handy,
# NAS, fremder PC). Dann antwortet „irgendetwas“ und der Computer erschiene online – Scans und Bereitstellungen landeten
# im schlimmsten Fall auf dem falschen Gerät. Deshalb: neue Adresse (oder lange nicht bestätigt) → kurz nachfragen.
PROBLEM_TEXT = {
    "other": "Adresse gehört zu einem anderen Gerät",
    "nodomain": "Gerät unter dieser Adresse ist nicht Mitglied der Domäne",
    "unverified": "Anmeldung abgelehnt – Gerät nicht bestätigt",
    "unconfirmed": "Unter der Adresse antwortet vermutlich ein anderes Gerät",
}
PROBLEM_RECHECK_MIN = 60  # Problemfälle höchstens stündlich erneut fragen (keine Anmeldeversuche bei fremden Geräten im Minutentakt)
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
    # Nur Geändertes schreiben – der Heartbeat prüft viele Computer parallel, unnötige Schreibzugriffe sperren SQLite
    if _row is not None:
        kw = {k: v for k, v in kw.items() if k not in _row or _row.get(k) != v}
    if kw:
        db.ex(f"UPDATE computers SET {', '.join(k + '=?' for k in kw)} WHERE id=?", tuple(kw.values()) + (cid,))


def probe(c, now=None, force=False):
    """Erreichbarkeit samt Identitätsprüfung. Gibt (online, winrm) zurück und speichert resolved_ip/addr_problem.
    Ohne Prüfung (Einstellung aus) wie bisher nur TCP."""
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
        # Nur SMB/RPC: nicht prüfbar. War eine andere Adresse bestätigt, ist das sehr wahrscheinlich ein anderes Gerät.
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
        # Nicht prüfbar (Dienstkonto-Schutz, Zeitüberschreitung): beim bisherigen Ergebnis bleiben
        _set_addr(c["id"], c, **fields)
        if prob:
            return _verdict(prob)
        # Audit 2.29: neue, noch nie bestätigte Adresse → nicht als bestätigt behandeln (erreichbar, aber nicht nutzbar,
        # bis die Prüfung beim nächsten Durchlauf klappt)
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
    """2.36.3 (Audit): Adresse für Direktaktionen (Dateien, Dienste, Prozesse, Tools, Speicherplatz, Fernsteuerung, Neustart …).
    Wie Bereitstellung und Scan nie mit dem Dienstkonto (oder eigenen Anmeldedaten) zu einem Gerät verbinden, das die
    Identitätsprüfung als fremd/nicht bestätigt markiert hat – sonst erreicht z. B. ein geänderter dNSHostName einen
    anderen Server (\\server\C$, WinRM). Bei ausgeschalteter Prüfung gilt ein alter Vermerk nicht mehr."""
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
    """(online, winrm) für einen bekannten Problemfall: falsches Gerät/keine Domäne → offline; nicht bestätigt →
    erreichbar, aber nicht nutzbar (keine Scans/Bereitstellungen)."""
    if prob in ("other", "nodomain", "unconfirmed"):
        return False, False
    if prob == "unverified":
        return True, False
    return True, True


def _log_problem(c, old, new, detail):
    if new and new != old:
        pklog.warn("system", f"{c['name']}: {PROBLEM_TEXT.get(new, new)}. {detail}", computer_id=c["id"])


COLUMNS = "id, name, dns_name, online, winrm, resolved_ip, verified_ip, verified_at, addr_problem, addr_detail, addr_ip, addr_checked_at"
