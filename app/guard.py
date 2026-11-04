"""Protection of the service account against an account lockout.

As soon as a logon of the service account clearly fails because of the password/account (LDAP, SMB or
WinRM), all automatic accesses are paused instead of producing further wrong logons. For ambiguous
WinRM errors (401 can also mean "not a local admin") the account is checked once via LDAP to tell
whether the account or just the computer is the problem.
"""
import threading
import time

from . import util

PAUSE_MINUTES = 15

# NTSTATUS / LDAP data codes -> (explanation, concerns the account itself?)
CODES = {
    "0xc0000234": ("Das Dienstkonto ist im AD gesperrt (zu viele Fehlanmeldungen).", True),
    "0xc000006d": ("Anmeldung abgelehnt: Benutzername oder Passwort des Dienstkontos falsch.", True),
    "0xc000006a": ("Falsches Passwort für das Dienstkonto.", True),
    "0xc0000064": ("Das Dienstkonto existiert nicht.", True),
    "0xc0000072": ("Das Dienstkonto ist deaktiviert.", True),
    "0xc0000071": ("Das Passwort des Dienstkontos ist abgelaufen.", True),
    "0xc0000193": ("Das Dienstkonto ist abgelaufen.", True),
    "0xc0000224": ("Das Passwort des Dienstkontos muss geändert werden.", True),
    "0xc000006f": ("Anmeldezeiten des Dienstkontos verbieten die Anmeldung.", True),
    "0xc0000070": ("Das Dienstkonto darf sich an diesem Computer nicht anmelden (Arbeitsstationen).", False),
    "0xc000015b": ("Netzwerkanmeldung für das Dienstkonto per Richtlinie verweigert "
                   "(„Zugriff vom Netzwerk auf diesen Computer verweigern“).", False),
    "0xc0000022": ("Zugriff verweigert – fehlende Berechtigung auf Freigabe oder Ordner.", False),
    "0xc00000cc": ("Freigabe nicht gefunden – Name im Repository-Pfad prüfen.", False),
    # LDAP (AD returns "data XXX" in the error text)
    "data 775": ("Das Dienstkonto ist im AD gesperrt (zu viele Fehlanmeldungen).", True),
    "data 52e": ("Anmeldung abgelehnt: Benutzername oder Passwort des Dienstkontos falsch.", True),
    "data 533": ("Das Dienstkonto ist deaktiviert.", True),
    "data 532": ("Das Passwort des Dienstkontos ist abgelaufen.", True),
    "data 773": ("Das Passwort des Dienstkontos muss geändert werden.", True),
    "data 701": ("Das Dienstkonto ist abgelaufen.", True),
    "account_locked_out": ("Das Dienstkonto ist im AD gesperrt (zu viele Fehlanmeldungen).", True),
    "logon_failure": ("Anmeldung abgelehnt: Benutzername oder Passwort des Dienstkontos falsch.", True),
    "wrong_password": ("Falsches Passwort für das Dienstkonto.", True),
}

_lock = threading.Lock()
_state = {"until": 0.0, "reason": None, "since": None, "source": None}
_verify = {"at": 0.0, "ok": None}


def explain(msg):
    """(explanation, concerns_account) for an error text, otherwise (None, False)."""
    low = str(msg or "").lower()
    for code, (text, account) in CODES.items():
        if code in low:
            return text, account
    return None, False


def paused():
    with _lock:
        return _state["until"] > time.time()


def status():
    with _lock:
        active = _state["until"] > time.time()
        return {"paused": active, "reason": _state["reason"] if active else None,
                "since": _state["since"] if active else None,
                "until": util.fmt(__import__("datetime").datetime.fromtimestamp(_state["until"])) if active else None,
                "source": _state["source"] if active else None}


def check():
    """Call before every access with the service account."""
    with _lock:
        if _state["until"] > time.time():
            raise PermissionError("Zugriffe mit dem Dienstkonto sind pausiert: " + (_state["reason"] or "")
                                  + " – nach der Behebung unter Einstellungen → Wartung fortsetzen.")


def trip(reason, source):
    from . import pklog
    with _lock:
        already = _state["until"] > time.time()
        _state.update({"until": time.time() + PAUSE_MINUTES * 60, "reason": reason, "source": source,
                       "since": _state["since"] if already else util.now()})
    if not already:
        pklog.error("system", f"Dienstkonto-Schutz aktiv ({source}): {reason} Alle automatischen Zugriffe sind "
                              f"für {PAUSE_MINUTES} Minuten pausiert, um eine (weitere) Kontosperre zu vermeiden.")


def reset(user=None):
    from . import pklog
    with _lock:
        _state.update({"until": 0.0, "reason": None, "since": None, "source": None})
        _verify["at"] = 0.0
    pklog.info("system", "Dienstkonto-Schutz manuell aufgehoben.", user=user)


def report(msg, source):
    """Evaluates a logon error. Returns a readable message."""
    text, account = explain(msg)
    if text and account:
        trip(text, source)
        return text
    if text:
        return text
    return None


HOST_REJECT = ("Dieses Gerät lehnt die Anmeldung des Dienstkontos ab – das Konto selbst ist in Ordnung (Prüfung per LDAP). "
               "Unter der Adresse antwortet vermutlich ein anderes Gerät (DNS-Eintrag veraltet) oder eines, das nicht zur "
               "Domäne gehört.")


# 2.32.5: only these errors are ambiguous (a foreign device answers the same way) - "locked", "disabled",
# "expired" etc. always concern the account and trigger the protection immediately
AMBIGUOUS = ("0xc000006d", "0xc000006a", "logon_failure", "wrong_password")
VERIFY_SECONDS = 60  # remember "account OK" at most this long - a password changed in AD is noticed quickly


def report_remote(msg, source):
    """2.31: Logon error on a single computer (WinRM, SMB). A device that does not belong to the domain - e.g. because
    the DNS entry points to another device - answers every logon with "wrong user name or password".
    That says nothing about the service account: check via LDAP first and trigger the protection only if the account
    really does not work. Otherwise a single wrong DNS entry would paralyse all scans and deployments.
    2.32.5: Unambiguous account errors (locked, disabled, expired ...) trigger immediately; if AD is currently not
    reachable, the outcome stays open (no "wrong password", no protection because of a single device)."""
    text, account = explain(msg)
    if not text or not account:
        return text
    low = str(msg or "").lower()
    if not any(c in low for c in AMBIGUOUS):
        trip(text, source)
        return text
    ok = verify_account()
    if ok:
        return f"{HOST_REJECT} ({text})"
    if ok is None:
        return ("Dieses Gerät lehnt die Anmeldung des Dienstkontos ab; das Konto ließ sich gerade nicht per LDAP prüfen "
                f"(Domänencontroller nicht erreichbar). ({text})")
    trip(text, source)  # verify_account has usually triggered the protection already - trip then has no effect
    return text


def verify_account():
    """Checks the service account via LDAP - at most every VERIFY_SECONDS. True = account OK, False = rejected
    (protection triggered), None = cannot be checked (domain controller unreachable, certificate ...)."""
    now = time.time()
    with _lock:
        if now - _verify["at"] < VERIFY_SECONDS and _verify["ok"] is not None:
            return _verify["ok"]
        _verify["at"] = now
    from . import ad
    try:
        c = ad.service_conn()
        c.unbind()
        ok = True
    except Exception:
        # rejected -> service_conn has triggered the protection; otherwise (network, certificate) it stays open
        ok = False if paused() else None
    _verify["ok"] = ok
    if ok is None:
        _verify["at"] = 0.0  # do not remember - check again on the next error
    return ok
