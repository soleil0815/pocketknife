"""Schutz des Dienstkontos vor einer Kontosperre.

Sobald eine Anmeldung des Dienstkontos eindeutig an Passwort/Konto scheitert (LDAP, SMB oder
WinRM), werden alle automatischen Zugriffe pausiert, statt weiter falsche Anmeldungen zu
erzeugen. Bei mehrdeutigen WinRM-Fehlern (401 kann auch „kein lokaler Admin“ heißen) wird
das Konto einmal per LDAP geprüft, um zu unterscheiden, ob das Konto oder nur der Computer
das Problem ist.
"""
import threading
import time

from . import util

PAUSE_MINUTES = 15

# NTSTATUS / LDAP-Datencodes -> (Erklärung, betrifft das Konto selbst?)
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
    # LDAP (AD liefert „data XXX“ im Fehlertext)
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
    """(Erklärung, betrifft_konto) für einen Fehlertext, sonst (None, False)."""
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
    """Vor jedem Zugriff mit dem Dienstkonto aufrufen."""
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
    """Wertet einen Anmeldefehler aus. Gibt eine verständliche Meldung zurück."""
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


# 2.32.5: nur diese Fehler sind mehrdeutig (ein fremdes Gerät antwortet genauso) – „gesperrt“, „deaktiviert“,
# „abgelaufen“ usw. betreffen immer das Konto und lösen den Schutz sofort aus
AMBIGUOUS = ("0xc000006d", "0xc000006a", "logon_failure", "wrong_password")
VERIFY_SECONDS = 60  # „Konto in Ordnung“ höchstens so lange merken – ein im AD geändertes Kennwort fällt schnell auf


def report_remote(msg, source):
    """2.31: Anmeldefehler bei einem einzelnen Computer (WinRM, SMB). Ein Gerät, das nicht zur Domäne gehört – z. B. weil
    der DNS-Eintrag auf ein anderes Gerät zeigt –, antwortet auf jede Anmeldung mit „Benutzername oder Kennwort falsch“.
    Das sagt nichts über das Dienstkonto: erst per LDAP prüfen und den Schutz nur auslösen, wenn das Konto wirklich nicht
    geht. Sonst lähmte ein einziger falscher DNS-Eintrag alle Scans und Bereitstellungen.
    2.32.5: Eindeutige Kontofehler (gesperrt, deaktiviert, abgelaufen …) lösen sofort aus; ist das AD gerade nicht
    erreichbar, bleibt es offen (kein „Kennwort falsch“, kein Schutz wegen eines einzelnen Geräts)."""
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
    trip(text, source)  # verify_account hat den Schutz meist schon ausgelöst – trip ist dann ohne Wirkung
    return text


def verify_account():
    """Prüft das Dienstkonto per LDAP – höchstens alle VERIFY_SECONDS. True = Konto in Ordnung, False = abgelehnt
    (Schutz ausgelöst), None = nicht prüfbar (Domänencontroller nicht erreichbar, Zertifikat …)."""
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
        # abgelehnt → service_conn hat den Schutz ausgelöst; sonst (Netzwerk, Zertifikat) bleibt es offen
        ok = False if paused() else None
    _verify["ok"] = ok
    if ok is None:
        _verify["at"] = 0.0  # nicht merken – beim nächsten Fehler erneut prüfen
    return ok
