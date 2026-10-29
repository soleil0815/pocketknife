"""Fernzugriff auf Windows-Computer: WinRM/PSRP (PowerShell) und SMB (Dateikopie).

Schritte als Dienstkonto laufen direkt als Prozess des Dienstkontos (über WMI gestartet, damit sie eine
getrennte WinRM-Verbindung überstehen) – ohne geplante Aufgabe und ohne Kennwort auf dem Client.
Als SYSTEM bzw. angemeldeter Benutzer dient eine temporäre geplante Aufgabe ohne gespeichertes Kennwort.
Die Paketdateien werden vorher per SMB nach ADMIN$\\Temp kopiert (kein Double-Hop nötig).
"""
import base64
import concurrent.futures
import re
import threading
import hashlib
import ntpath
import os
import shutil
import time

from pypsrp.client import Client

from . import config, guard, kerberos, util


class RemoteError(Exception):
    pass


class RemoteTimeout(RemoteError):
    """2.32.7: Zeitlimit überschritten (eigener Typ – nicht am Meldungstext erkennen)."""


def dur_text(s):
    """Dauer für Meldungen: unter 2 Minuten in Sekunden, sonst in Minuten."""
    s = int(s or 0)
    return f"{s} Sekunden" if s < 120 else f"{round(s / 60)} Minuten"


class RemoteConnectError(RemoteError):
    """WinRM gar nicht erreichbar (Verbindungsaufbau gescheitert: Port zu, Name unbekannt, Zeitüberschreitung beim
    Verbinden) – im Unterschied zu Anmelde- oder Skriptfehlern. Die Meldung ist dieselbe wie bei RemoteError."""


def _is_connect_error(e):
    try:
        import requests
        if isinstance(e, requests.exceptions.ConnectTimeout):
            return True
        if isinstance(e, requests.exceptions.ConnectionError) and not isinstance(e, requests.exceptions.ReadTimeout):
            return True
    except ImportError:  # pragma: no cover
        pass
    low = str(e).lower()
    return any(x in low for x in ("max retries exceeded", "failed to establish a new connection", "connection refused",
                                  "name or service not known", "no route to host", "connecttimeout", "getaddrinfo failed"))


class Cancelled(Exception):
    pass


def is_online(host, ip=None):
    """(online, winrm) – erreichbar über WinRM oder SMB/RPC. Der Name wird über dnsres aufgelöst (einstellbarer
    Zwischenspeicher, wahlweise direkt bei den DNS-Servern der Domäne)."""
    from . import dnsres
    addr = ip or dnsres.resolve(host)
    if not addr:
        return False, False
    if util.tcp_open(addr, config.WINRM_PORT):
        return True, True
    if util.tcp_open(addr, 445) or util.tcp_open(addr, 135):
        return True, False
    return False, False


IDENTIFY_PS = r"""$cs = Get-CimInstance Win32_ComputerSystem -ErrorAction SilentlyContinue
'PKID:' + $env:COMPUTERNAME + '|' + [string]$cs.Domain + '|' + [string]$cs.PartOfDomain"""


def identify(host):
    """Wer antwortet unter diesem Namen? Kurze, angemeldete WinRM-Abfrage mit dem Dienstkonto.
    {"name", "domain", "joined"} – oder {"error": "auth"|"krb_modified"|"paused"|"other", "detail"}. Eine abgelehnte
    Anmeldung bei einem fremden Gerät ist ein erwartetes Ergebnis („auth“) und löst keinen Dienstkonto-Schutz aus. Ist
    dagegen das Dienstkonto selbst das Problem (Kennwort falsch, gesperrt – laut Fehlertext oder LDAP-Prüfung), wird
    der Schutz ausgelöst und „paused“ gemeldet; KDC-/Uhrzeitfehler gelten als „other“ (nichts über das Gerät gesagt)."""
    if guard.paused():
        return {"error": "paused", "detail": "Dienstkonto-Schutz aktiv"}
    try:
        c = Client(kerberos.host(host, kerberos.mode()), username=kerberos.login(config.service_login(), kerberos.mode()),
                   password=config.service_password(), ssl=config.WINRM_HTTPS, port=config.WINRM_PORT, auth=kerberos.mode(),
                   cert_validation=(config.WINRM_CA_FILE or False) if config.WINRM_HTTPS else False,
                   connection_timeout=10, operation_timeout=15, read_timeout=20)
    except Exception as e:
        return {"error": "other", "detail": str(e)[:200]}
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        out, _streams, _had = ex.submit(c.execute_ps, IDENTIFY_PS).result(timeout=40)
    except concurrent.futures.TimeoutError:
        return {"error": "other", "detail": "keine Antwort"}
    except Exception as e:
        low = str(e).lower()
        kind = "auth" if ("401" in low or "credentials" in low or "authenticat" in low or "kerberos" in low
                          or "gss" in low or "spnego" in low or "access is denied" in low) else "other"
        if "message stream modified" in low or "krb_ap_err_modified" in low:
            kind = "krb_modified"  # Ticket für einen anderen Rechner: falsches Gerät oder defekte Vertrauensstellung
        elif any(k in low for k in ("cannot contact any kdc", "cannot find kdc", "clock skew", "cannot resolve network address")):
            kind = "other"  # Problem auf Seite von POCKET KNIFE (KDC, Uhrzeit, DNS) – sagt nichts über das Gerät
        elif kerberos._account_problem(e):
            guard.trip(kerberos.explain(e) or str(e)[:200], "WinRM/Kerberos")
            kind = "paused"  # der KDC sagt: Kennwort falsch/Konto gesperrt – Schutz auslösen statt Geräte zu „verdächtigen“
        elif guard.explain(str(e))[1]:
            # 2.31: „Kennwort falsch“ von einem Gerät heißt nicht, dass das Konto falsch ist (fremdes Gerät, falscher
            # DNS-Eintrag) – per LDAP prüfen; nur wenn das Konto wirklich nicht geht, ist der Schutz aktiv
            kind = "paused" if guard.verify_account() is False else "auth"  # None = AD nicht erreichbar: offen lassen
        elif kind == "auth" and guard.verify_account() is False:
            kind = "paused"  # das Dienstkonto selbst ist das Problem (Prüfung per LDAP)
        return {"error": kind, "detail": str(e)[:200]}
    finally:
        try:
            c.close()
        except Exception:
            pass
        ex.shutdown(wait=False)
    line = next((x for x in (out or "").splitlines() if x.startswith("PKID:")), "")
    if not line:
        return {"error": "other", "detail": "keine Kennung erhalten"}
    parts = (line[5:].split("|") + ["", "", ""])[:3]
    return {"name": parts[0].strip(), "domain": parts[1].strip(), "joined": parts[2].strip().lower() == "true"}


def _client(host, auth=None):
    m = auth or kerberos.mode()
    return Client(kerberos.host(host, m), username=kerberos.login(config.service_login(), m), password=config.service_password(),
                  ssl=config.WINRM_HTTPS, port=config.WINRM_PORT, auth=m,
                  cert_validation=(config.WINRM_CA_FILE or False) if config.WINRM_HTTPS else False,
                  connection_timeout=15, operation_timeout=20, read_timeout=30)


def _guard_check():
    try:
        guard.check()
    except PermissionError as e:
        raise RemoteError(str(e))


PS_TIMEOUT = 1800  # Sekunden: Obergrenze für einen einzelnen WinRM-Aufruf (Timeouts von pypsrp gelten nur je Abfrage)


def ps(host, script, auth=None, timeout=PS_TIMEOUT):
    """Führt PowerShell aus. Gibt (stdout, fehlertext, had_errors) zurück. auth: Verfahren erzwingen (Test).
    Hängt der Client (z. B. defekter WMI-Anbieter), bricht der Aufruf nach `timeout` Sekunden ab."""
    import concurrent.futures
    _guard_check()
    try:
        c = _client(host, auth)
    except Exception as e:
        raise RemoteConnectError(f"WinRM-Verbindung fehlgeschlagen: {e}")
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(c.execute_ps, script)
    try:
        out, streams, had_errors = fut.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        raise RemoteTimeout(f"WinRM: keine Antwort von {host} nach {dur_text(timeout)} – Aufruf abgebrochen.")
    except Exception as e:
        if _is_connect_error(e):
            raise RemoteConnectError("WinRM: " + _winrm_error(str(e)))
        raise RemoteError("WinRM: " + _winrm_error(str(e)))
    finally:
        try:
            c.close()  # beendet bei Zeitüberschreitung auch die Verbindung des hängenden Aufrufs
        except Exception:
            pass
        ex.shutdown(wait=False)
    errors = "\n".join(str(e) for e in (streams.error or []))
    return out or "", errors, had_errors


def ps_params(host, script, params, timeout=300):
    """Wie ps(), übergibt aber Werte als Parameter statt im Skripttext. So landen Kennwörter nicht in der
    PowerShell-Skriptblockprotokollierung (Ereignis 4104) des Clients."""
    _guard_check()
    from pypsrp.powershell import PowerShell, RunspacePool
    try:
        c = _client(host)
    except Exception as e:
        raise RemoteError(f"WinRM-Verbindung fehlgeschlagen: {e}")
    def call():
        with RunspacePool(c.wsman) as pool:
            p = PowerShell(pool)
            p.add_script(script)
            for k, v in params.items():
                p.add_parameter(k, v)
            p.add_cmdlet("Out-String").add_parameter("Stream")
            p.invoke()
            out = "\n".join(str(x) for x in p.output)
            errors = "\n".join(str(e) for e in (p.streams.error or []))
            return out, errors, p.had_errors
    # Mit Zeitgrenze wie ps(): ein hängender Client darf keinen Bereitstellungs-Worker dauerhaft blockieren
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(call)
    try:
        return fut.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        raise RemoteTimeout(f"WinRM: keine Antwort von {host} nach {dur_text(timeout)} – Aufruf abgebrochen.")
    except Exception as e:
        raise RemoteError("WinRM: " + _winrm_error(str(e)))
    finally:
        try:
            c.close()
        except Exception:
            pass
        ex.shutdown(wait=False)


def _winrm_error(msg):
    """Übersetzt WinRM-Anmeldefehler. Ein 401 kann „falsches Passwort/gesperrt“ ODER
    „auf diesem Computer kein Admin“ bedeuten – das wird über eine LDAP-Prüfung unterschieden."""
    text = guard.report_remote(msg, "WinRM")
    if text:
        return text
    kt = kerberos.explain(msg) if kerberos.uses_kerberos() else None
    if kt:
        if kerberos._account_problem(msg):
            guard.trip(kt, "WinRM/Kerberos")  # wie bei NTLM: weitere Fehlanmeldungen vermeiden
        return f"{kt} ({msg})"
    low = msg.lower()
    if "failed to authenticate" in low or "401" in low or "credentials" in low:
        v = guard.verify_account()
        if v:
            return ("Das Dienstkonto ist in Ordnung, wird aber von diesem Computer abgelehnt: Es ist dort nicht in der "
                    "lokalen Gruppe „Administratoren“, oder die Netzwerkanmeldung ist per Richtlinie verweigert.")
        if v is None:
            return ("Dieser Computer lehnt die Anmeldung des Dienstkontos ab; das Konto ließ sich gerade nicht per LDAP "
                    f"prüfen (Domänencontroller nicht erreichbar). ({msg})")
        st = guard.status()
        return st["reason"] or "Anmeldung des Dienstkontos abgelehnt."
    return msg


# --- Repository / SMB --------------------------------------------------------
def _is_unc(p):
    return p.startswith("\\\\") or p.startswith("//")


def _unc(p):
    return p.replace("/", "\\")


_DIALECTS = {0x0202: "SMB 2.0.2", 0x0210: "SMB 2.1", 0x0300: "SMB 3.0", 0x0302: "SMB 3.0.2", 0x0311: "SMB 3.1.1"}


_smb_used = {}  # server → (name, zuletzt benutzt) – für das Aufräumen ungenutzter Verbindungen
SMB_IDLE = 3 * 3600  # großzügig: eine laufende große Kopie darf nicht getrennt werden


def close_idle_smb():
    """Jede SMB-Verbindung hält einen Socket und einen Empfangs-Thread. Bei vielen Clients nach Leerlauf schließen
    (die Repository-Verbindung baut smbclient beim nächsten Zugriff einfach neu auf)."""
    import smbclient
    from . import db
    if db.val("SELECT COUNT(*) FROM deployments WHERE status='running'", (), 0):
        return  # während Bereitstellungen nichts trennen
    now = time.time()
    for key, (server, t) in list(_smb_used.items()):
        if now - t > SMB_IDLE:
            try:
                smbclient.delete_session(server)
            except Exception:
                pass
            _smb_used.pop(key, None)


def _smb_session(server, user, password):
    """Registriert die SMB-Sitzung. smbclient verwendet eine bestehende, noch offene
    Verbindung wieder und baut sie neu auf, wenn sie abgebrochen ist (z. B. nach einem
    Neustart des Fileservers oder Leerlauf-Timeout).
    Bei Anmeldefehlern wird NICHT erneut versucht (jeder Versuch zählt für die Kontosperre)."""
    import smbclient
    _guard_check()
    _smb_used[(server or "").lower()] = (server, time.time())
    proto = kerberos.smb_protocol()
    is_svc = (user or "").lower() == config.service_login().lower()
    user = kerberos.login(user)
    try:
        return smbclient.register_session(server, username=user, password=password, connection_timeout=15, auth_protocol=proto)
    except Exception as e:
        text, _acc = guard.explain(str(e))
        if not text and proto != "ntlm":
            text = kerberos.explain(str(e))
        if text or "auth" in type(e).__name__.lower():
            msg = (guard.report_remote(str(e), "SMB") if is_svc else None) or text or str(e)
            if is_svc and proto != "ntlm" and kerberos._account_problem(str(e)):
                guard.trip(text or str(e), "SMB/Kerberos")
            raise RemoteError(f"SMB-Anmeldung an {server} als {user}: {msg}")
        # Nur bei Verbindungsfehlern: defekte Verbindung verwerfen und einmal neu aufbauen
        try:
            smbclient.delete_session(server)
        except Exception:
            pass
        try:
            return smbclient.register_session(server, username=user, password=password, connection_timeout=15, auth_protocol=proto)
        except Exception as e2:
            raise RemoteError(f"SMB-Verbindung zu {server}: " + (guard.explain(str(e2))[0] or str(e2)))


def _session_info(sess):
    info = {}
    try:
        conn = sess.connection
        info["dialect"] = _DIALECTS.get(conn.dialect, hex(conn.dialect))
        info["signing"] = bool(getattr(sess, "signing_required", False) or getattr(conn, "require_signing", False))
        info["encryption"] = bool(getattr(sess, "encrypt_data", False))
    except Exception:
        pass
    return info


def _repo_server(path):
    return _unc(path).lstrip("\\").split("\\")[0]


def repo_root():
    from . import db
    root = (db.setting("repo_path") or "").strip()
    if not root:
        raise RemoteError("Kein Software-Repository konfiguriert (Einstellungen → Repository).")
    return root


def repo_join(root, rel):
    rel = (rel or "").replace("/", "\\").strip("\\")
    parts = [p for p in rel.split("\\") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise RemoteError("Ungültiger Pfad im Repository.")
    if _is_unc(root):
        return ntpath.join(_unc(root), *parts) if parts else _unc(root)
    return os.path.join(root, *parts) if parts else root


def repo_list(rel=""):
    """Listet Ordner und Dateien eines Repository-Pfads."""
    root = repo_root()
    path = repo_join(root, rel)
    items = []
    if _is_unc(root):
        import smbclient
        _smb_session(_repo_server(root), config.repo_login(), config.repo_password())
        from .clientfiles import _smb_errors
        for e in smbclient.scandir(path):
            # Verknüpfungen nicht verfolgen (SMB leitet nicht auf lokale Ziele um) – ein Link darf die Liste nicht abbrechen
            try:
                st = e.stat(follow_symlinks=False)
                attr = getattr(st, "st_file_attributes", 0) or 0
                isdir = bool(attr & 0x10) if attr else e.is_dir(follow_symlinks=False)
                items.append({"name": e.name, "dir": isdir, "size": 0 if isdir or attr & 0x400 else st.st_size,
                              "link": bool(attr & 0x400), "mtime": int(st.st_mtime or 0)})
            except _smb_errors():
                continue
    else:
        if not os.path.isdir(path):
            raise RemoteError(f"Ordner nicht gefunden: {path}")
        for e in os.scandir(path):
            try:
                # Verknüpfungen nicht verfolgen (wie bei SMB und wie _repo_walk beim Kopieren)
                link = e.is_symlink()
                isdir = e.is_dir(follow_symlinks=not link) if not link else os.path.isdir(e.path)
                st = e.stat(follow_symlinks=False)
                items.append({"name": e.name, "dir": isdir, "size": 0 if isdir or link else st.st_size,
                              "mtime": int(st.st_mtime or 0), "link": link})
            except OSError:
                continue  # z. B. defekter Link
    if not (rel or "").replace("/", "\\").strip("\\"):
        items = [x for x in items if x["name"].lower() != UPLOAD_DIR]
    items.sort(key=lambda x: (not x["dir"], x["name"].lower()))
    return items


_search_sem = threading.BoundedSemaphore(3)  # höchstens 3 Suchen gleichzeitig (große SMB-Freigaben)


MAX_STARS = 8  # mehr * im Suchbegriff ergeben keinen Sinn


class _Glob:
    """Name-Vergleich ohne Regex (kein Backtracking, Laufzeit höchstens Name × Muster): ohne Platzhalter „Teil des
    Namens“, mit * und ? der ganze Name. Groß-/Kleinschreibung egal. Gleiche Schnittstelle wie ein Regex (.search)."""

    def __init__(self, q):
        self.anchored = any(ch in q for ch in "*?")
        q = re.sub(r"\*{2,}", "*", q.casefold())
        self.parts = q.split("*") if self.anchored else [q]

    @staticmethod
    def _at(name, seg, i):
        if i + len(seg) > len(name):
            return False
        for k, ch in enumerate(seg):
            if ch != "?" and name[i + k] != ch:
                return False
        return True

    def _find(self, name, seg, start, end):
        for i in range(start, end - len(seg) + 1):
            if self._at(name, seg, i):
                return i
        return -1

    def search(self, name):
        name = str(name).casefold()
        if not self.anchored:
            return self.parts[0] in name
        parts = self.parts
        if len(parts) == 1:  # nur ? – ganzer Name, gleiche Länge
            return len(name) == len(parts[0]) and self._at(name, parts[0], 0)
        first, last, mid = parts[0], parts[-1], parts[1:-1]
        if len(first) + len(last) > len(name) or not self._at(name, first, 0) or not self._at(name, last, len(name) - len(last)):
            return False
        pos, end = len(first), len(name) - len(last)
        for seg in mid:  # jedes Mittelstück so weit links wie möglich – bei nur * und ? immer richtig
            if not seg:
                continue
            i = self._find(name, seg, pos, end)
            if i < 0:
                return False
            pos = i + len(seg)
        return True


def name_pattern(q, err=None):
    """Suchbegriff prüfen → Vergleicher mit .search(name): ohne Platzhalter „Teil des Namens“, mit * und ? der ganze
    Name (alle anderen Zeichen wörtlich, auch [ ] und Punkte); ohne Groß-/Kleinschreibung. Für Repository- und
    Client-Dateisuche (dort dieselben Regeln mit -like, siehe clientfiles.like_pattern)."""
    err = err or RemoteError
    q = str(q or "").strip()
    if len(q) > 100:
        raise err("Suchbegriff zu lang (höchstens 100 Zeichen).")
    if len(q.replace("*", "").replace("?", "")) < 2:
        raise err("Bitte mindestens 2 Zeichen eingeben.")
    if re.sub(r"\*{2,}", "*", q).count("*") > MAX_STARS:
        raise err(f"Höchstens {MAX_STARS} Sternchen im Suchbegriff.")
    if re.search(r"[\x00-\x1f]", q):
        raise err("Ungültige Zeichen im Suchbegriff.")
    return _Glob(q)


# --- Repository-Index im Arbeitsspeicher des Servers (nicht in der Datenbank) ----------------------
# Für die Suche: Ordner + Name + Größe aller Einträge. 10 000 Dateien ≈ 2–5 MB. Über REPO_CACHE_MAX Einträgen
# wird nicht zwischengespeichert (dann sucht jede Anfrage direkt auf der Freigabe, wie bisher).
REPO_CACHE_MAX = 200_000
REPO_CACHE_TTL = 300        # Sekunden: so lange gilt der Index als aktuell
REPO_CACHE_STALE = 3600     # älter als das: nicht mehr verwenden, direkt suchen
REPO_CACHE_SECONDS = 600    # Einlesen höchstens so lange
REPO_CACHE_RETRY = 1800     # zu groß/fehlgeschlagen: so lange nicht erneut versuchen (außer „Neu einlesen“)
_rc_lock = threading.Lock()
_rc = {"key": None, "at": 0.0, "entries": None, "dirs": None, "building": None, "failed": None, "again": None}


def _repo_key():
    # Pfad + Konto (+ Prüfsumme des Kennworts, nur im Speicher): nach einer Änderung wird neu eingelesen
    import hashlib
    pw = hashlib.sha256((config.repo_password() or "").encode()).hexdigest()[:16]
    return (repo_root(), (config.repo_login() or "").lower(), pw)


def repo_cache_clear():
    with _rc_lock:
        _rc.update(key=None, at=0.0, entries=None, dirs=None, failed=None, again=None, ops=[])


def _repo_index(key):
    """Liest das ganze Repository ein (Breitensuche, Verknüpfungen nicht verfolgt). Läuft im Hintergrund."""
    import time as _time
    t0 = _time.time()
    entries, dirs, todo, count = [], {""}, [""], 0
    ok = True
    try:
        while todo:
            if _time.time() - t0 > REPO_CACHE_SECONDS:
                ok = False
                break
            cur = todo.pop(0)
            try:
                items = repo_list(cur)
            except Exception:
                if cur == "":
                    ok = False
                    break
                continue
            for it in items:
                count += 1
                if count > REPO_CACHE_MAX:
                    ok = False
                    break
                entries.append((cur, it["name"], bool(it["dir"]), int(it.get("size") or 0), int(it.get("mtime") or 0)))
                if it["dir"] and not it.get("link"):
                    sub = f"{cur}\\{it['name']}" if cur else it["name"]
                    dirs.add(sub.lower())
                    todo.append(sub)
            if not ok:
                break
    except Exception:
        ok = False
    again = False
    with _rc_lock:
        if _rc["building"] != key:
            _rc["ops"] = []
            return  # inzwischen anderes Repository eingestellt oder geleert
        _rc["building"] = None
        again = _rc["again"] == key  # während des Einlesens „Neu einlesen“ gedrückt: noch ein Durchlauf
        _rc["again"] = None
        try:
            still = _repo_key() == key
        except Exception:
            still = False
        ops, _rc["ops"] = _rc.get("ops") or [], []
        if not still:
            return
        if ok:
            # Eigene Änderungen während des Einlesens nachtragen (das Einlesen hat sie evtl. nicht mehr gesehen)
            for args in ops:
                entries, dirs = _cache_op(entries, dirs, *args)
            # Austausch als Ganzes unter dem Lock: Suchen sehen den alten oder den neuen Stand, nie einen halben
            _rc.update(key=key, at=_time.time(), entries=entries, dirs=dirs, failed=None)
        else:
            _rc.update(failed=(key, _time.time()))
            if _rc["key"] != key:
                _rc.update(key=None, entries=None, dirs=None)
    if again:
        _repo_cache_start(key, force=True)


def _repo_cache_start(key, force=False):
    """Einlesen im Hintergrund anstoßen (höchstens einmal gleichzeitig). Gibt True zurück, wenn es läuft."""
    import time as _time
    with _rc_lock:
        if _rc["building"] == key:
            if force:
                _rc["again"] = key
            return True
        f = _rc["failed"]
        if not force and f and f[0] == key and _time.time() - f[1] < REPO_CACHE_RETRY:
            return False
        _rc["building"] = key
    threading.Thread(target=_repo_index, args=(key,), name="pk-repo-index", daemon=True).start()
    return True


def repo_cache_status(full=True):
    """Stand des Index. full=False (OU-Admins): nur ob eingelesen wird und wie alt – nichts über das ganze Repository."""
    import time as _time
    with _rc_lock:
        st = {"age": int(_time.time() - _rc["at"]) if _rc["entries"] is not None else None,
              "building": _rc["building"] is not None}
        if full:
            st.update(entries=len(_rc["entries"]) if _rc["entries"] is not None else None, too_big_or_failed=bool(_rc["failed"]))
        return st


def _repo_cache_search(key, rel, rx, limit):
    """Suche im Index – None, wenn kein passender Index da ist (dann direkt suchen)."""
    import time as _time
    with _rc_lock:
        if _rc["key"] != key or _rc["entries"] is None:
            return None
        age = _time.time() - _rc["at"]
        if age > REPO_CACHE_STALE:
            return None
        entries, dirs = _rc["entries"], _rc["dirs"]
    start = (rel or "").replace("/", "\\").strip("\\")
    low = start.lower()
    if low and low not in dirs:
        return None  # Ordner (noch) nicht im Index – direkt suchen, das meldet auch „nicht gefunden“
    pre = low + "\\"
    hits, truncated = [], False
    for folder, name, isdir, size, _mtime in entries:
        if low:
            fl = folder.lower()
            if fl != low and not fl.startswith(pre):
                continue
        if rx.search(name):
            if len(hits) >= limit:
                truncated = True
                break
            hits.append({"folder": folder, "name": name, "dir": isdir, "size": size})
    hits.sort(key=lambda x: (not x["dir"], (x["folder"] + "\\" + x["name"]).lower()))
    return {"items": hits, "truncated": truncated, "cached": True, "age": int(age)}


# --- Schreiben im Repository (2.27: Upload und Ordnerverwaltung über die Oberfläche) ---------------------------------
# Immer mit dem Repository-/Dienstkonto, nie mit den Rechten des Bedieners. Pfade laufen durch repo_join (kein „..“);
# lokal wird zusätzlich geprüft, dass der aufgelöste Pfad im Repository bleibt (keine Verknüpfung nach draußen).
UPLOAD_DIR = ".pk-uploads"  # Zwischenablage für laufende Uploads (oberste Ebene, in Listen ausgeblendet)


def _repo_target(rel):
    root = repo_root()
    path = repo_join(root, rel)
    if _is_unc(root):
        _smb_session(_repo_server(root), config.repo_login(), config.repo_password())
        # Keine Verknüpfung (Junction/symbolischer Link) im Pfad: sie würde der Server auf SEINER Seite auflösen –
        # Schreiben oder Löschen könnte so außerhalb der Freigabe landen
        import smbclient
        parts = [x for x in (rel or "").replace("/", "\\").split("\\") if x not in ("", ".")]
        cur = _unc(root)
        for x in parts:
            cur = ntpath.join(cur, x)
            try:
                st = _smb_call(smbclient.lstat, cur)
            except FileNotFoundError:
                break
            if (getattr(st, "st_file_attributes", 0) or 0) & 0x400:
                raise RemoteError("Im Pfad liegt eine Verknüpfung (Junction/Link) – dort schreibt POCKET KNIFE nicht.")
        return path, True
    if not os.path.isdir(root):
        raise RemoteError(f"Repository nicht erreichbar: {root}")
    real_root = os.path.realpath(root)
    real = os.path.realpath(path)
    if real != real_root and not real.startswith(real_root + os.sep):
        raise RemoteError("Pfad liegt außerhalb des Repositorys (Verknüpfung).")
    return path, False


def _smb_call(fn, *a):
    from .clientfiles import _smb_errors
    try:
        return fn(*a)
    except _smb_errors() as e:
        if _not_found(e):
            raise FileNotFoundError(str(e))
        raise RemoteError(f"Repository: {e}")


def repo_mkdir(rel):
    """Ordner anlegen (auch mehrere Ebenen); vorhanden ist kein Fehler."""
    parts = [x for x in (rel or "").replace("/", "\\").split("\\") if x]
    cur = ""
    for x in parts:
        cur = f"{cur}\\{x}" if cur else x
        kind = repo_stat(cur)
        if kind == "dir":
            continue
        if kind == "file":
            raise RemoteError(f"„{cur}“ ist eine Datei, kein Ordner.")
        path, unc = _repo_target(cur)
        if unc:
            import smbclient
            _smb_call(smbclient.mkdir, path)
        else:
            os.mkdir(path)


def repo_rename(src, dst):
    """Datei oder Ordner verschieben/umbenennen (innerhalb des Repositorys). Ziel darf es noch nicht geben."""
    if repo_stat(dst) is not None and src.lower() != dst.lower():  # nur Groß-/Kleinschreibung ändern ist erlaubt
        raise RemoteError(f"„{dst}“ gibt es bereits.")
    if repo_stat(src) is None:
        raise RemoteError(f"„{src}“ gibt es nicht (mehr).")
    a, unc = _repo_target(src)
    b, _ = _repo_target(dst)
    if unc:
        import smbclient
        _smb_call(smbclient.rename, a, b)
    else:
        os.rename(a, b)


def repo_delete(rel):
    """Datei oder Ordner (mit Inhalt) löschen. Verknüpfungen werden nur entfernt, nie ihr Ziel."""
    kind = repo_stat(rel)
    if kind is None:
        return False
    path, unc = _repo_target(rel)
    if unc:
        import smbclient
        if kind == "dir":
            _smb_rmtree(path)
        else:
            _smb_call(smbclient.remove, path)
    else:
        if os.path.islink(path):
            os.remove(path)
        elif kind == "dir":
            shutil.rmtree(path)
        else:
            os.remove(path)
    return True


def _smb_rmtree(path):
    """Ordner auf der Freigabe löschen, OHNE Verknüpfungen zu folgen: eine Junction/ein Link darin wird nur selbst
    entfernt (smbclient.shutil.rmtree ginge in Junctions hinein – der Dateiserver löste sie auf SEINER Seite auf und
    löschte deren Ziel)."""
    import smbclient
    st = _smb_call(smbclient.lstat, path)
    if (getattr(st, "st_file_attributes", 0) or 0) & 0x400:
        _smb_call(smbclient.rmdir, path)
        return
    for e in list(_smb_call(smbclient.scandir, path)):
        sub = ntpath.join(path, e.name)
        est = _smb_call(smbclient.lstat, sub)
        attr = getattr(est, "st_file_attributes", 0) or 0
        if attr & 0x400:
            _smb_call(smbclient.rmdir if attr & 0x10 else smbclient.remove, sub)  # nur die Verknüpfung selbst
        elif attr & 0x10:
            _smb_rmtree(sub)
        else:
            _smb_call(smbclient.remove, sub)
    _smb_call(smbclient.rmdir, path)


def repo_write_at(rel, offset, data):
    """Stück an genau diese Stelle schreiben (Upload): ein zuvor halb geschriebenes Stück wird überschrieben statt
    dahinter angehängt."""
    path, unc = _repo_target(rel)
    if unc:
        import smbclient
        fh = _smb_call(smbclient.open_file, path, "r+b")
    else:
        fh = open(path, "r+b")
    with fh:
        fh.seek(int(offset))
        fh.write(data)


def repo_open_write(rel, mode="ab"):
    """Datei zum Schreiben öffnen (mode „wb“ oder „ab“) – für den Upload in Stücken."""
    path, unc = _repo_target(rel)
    if unc:
        import smbclient
        return _smb_call(smbclient.open_file, path, mode)
    return open(path, mode)


def _cache_op(entries, dirs, op, path, new=None, isdir=False, size=0, mtime=0):
    """Eine eigene Änderung auf eine Kopie des Index anwenden: „add“, „remove“ (mit Inhalt), „move“ (mit Inhalt)."""
    def clean(p):
        return "\\".join(x for x in (p or "").replace("/", "\\").split("\\") if x)

    def split(p):
        p = clean(p)
        i = p.rfind("\\")
        return (p[:i], p[i + 1:]) if i >= 0 else ("", p)

    def under(folder, low):
        fl = folder.lower()
        return fl == low or fl.startswith(low + "\\")
    parent, name = split(path)
    pl, nl = parent.lower(), name.lower()
    low = clean(path).lower()
    if op == "add":
        entries = [e for e in entries if not (e[0].lower() == pl and e[1].lower() == nl)]
        entries.append((parent, name, bool(isdir), int(size or 0), int(mtime or 0)))
        if isdir:
            dirs = dirs | {low}
    elif op == "remove":
        entries = [e for e in entries if not ((e[0].lower() == pl and e[1].lower() == nl) or under(e[0], low))]
        dirs = {d for d in dirs if not (d == low or d.startswith(low + "\\"))}
    elif op == "move":
        np_, nn = split(new)
        nclean = clean(new)
        depth = len(low.split("\\"))
        out = []
        for e in entries:
            if e[0].lower() == pl and e[1].lower() == nl:
                out.append((np_, nn) + tuple(e[2:]))
            elif under(e[0], low):
                out.append(("\\".join([nclean] + e[0].split("\\")[depth:]),) + tuple(e[1:]))
            else:
                out.append(e)
        entries = out
        dirs = {("\\".join([nclean.lower()] + d.split("\\")[depth:]) if (d == low or d.startswith(low + "\\")) else d)
                for d in dirs}
    return entries, dirs


def repo_cache_apply(op, path, new=None, isdir=False, size=0, mtime=0):
    """Eigene Änderungen (Upload, Ordner, Verschieben, Löschen) gleich im Index nachtragen – sonst fände die Suche sie
    erst nach dem nächsten Einlesen. Läuft gerade ein Einlesen, wird die Änderung auch auf dessen Ergebnis angewendet."""
    args = (op, path, new, isdir, size, mtime)
    with _rc_lock:
        if _rc["building"] is not None:
            _rc.setdefault("ops", []).append(args)
        if _rc["entries"] is None:
            return
        entries, dirs = _cache_op(_rc["entries"], _rc["dirs"], *args)
        _rc.update(entries=entries, dirs=dirs)


def repo_cache_refresh():
    """Index auffrischen, wenn er älter als REPO_CACHE_TTL ist (Scheduler, für die Bruchprüfung alle 5 Minuten)."""
    import time as _time
    try:
        key = _repo_key()
    except Exception:
        return False
    with _rc_lock:
        fresh = _rc["key"] == key and _rc["entries"] is not None and _time.time() - _rc["at"] < REPO_CACHE_TTL
    return True if fresh else _repo_cache_start(key)


def repo_index_files():
    """Für die Bruchprüfung (integrity): {schlüssel: (pfad, größe, mtime)} aller Dateien und die Menge der Ordner –
    aus dem Index im Arbeitsspeicher, solange er gültig ist (sonst None: dann direkt nachsehen)."""
    import time as _time
    try:
        key = _repo_key()
    except Exception:
        return None
    with _rc_lock:
        if _rc["key"] != key or _rc["entries"] is None or _time.time() - _rc["at"] > REPO_CACHE_TTL * 2:
            return None
        entries, dirs = _rc["entries"], _rc["dirs"]
    files = {}
    for folder, name, isdir, size, mtime in entries:
        if not isdir:
            full = f"{folder}\\{name}" if folder else name
            files[full.lower()] = (full, size, mtime)
    return files, dirs


def repo_fstat(rel):
    """(größe, mtime) einer Datei im Repository – None, wenn es sie nicht gibt (oder sie ein Ordner ist).
    Fehler beim Zugriff auf das Repository selbst (nicht erreichbar, Anmeldung) werden weitergegeben."""
    root = repo_root()
    path = repo_join(root, rel)
    if _is_unc(root):
        import smbclient
        import stat as _st
        from .clientfiles import _smb_errors
        _smb_session(_repo_server(root), config.repo_login(), config.repo_password())
        try:
            st = smbclient.stat(path)
        except _smb_errors() as e:
            if _not_found(e):
                return None
            raise RemoteError(f"Repository: {e}")
        return None if _st.S_ISDIR(st.st_mode) else (int(st.st_size), int(st.st_mtime or 0))
    if not os.path.isfile(path):
        if not os.path.isdir(root):
            raise RemoteError(f"Repository nicht erreichbar: {root}")
        return None
    st = os.stat(path)
    return int(st.st_size), int(st.st_mtime or 0)


def repo_tree(rel, max_files=50000):
    """Alle Dateien unter einem Repository-Ordner: [(pfad, größe, mtime)] – None, wenn es den Ordner nicht gibt.
    Verknüpfungen werden nicht verfolgt; nicht lesbare Unterordner brechen ab (lieber „nicht prüfbar“ als ein falsches
    „fehlt“)."""
    start = "\\".join(x for x in (rel or "").replace("/", "\\").split("\\") if x not in ("", "."))
    if start and repo_stat(start) != "dir":
        repo_root()  # Repository selbst nicht konfiguriert → Fehler statt „fehlt“
        return None
    out, todo = [], [start]
    while todo:
        cur = todo.pop()
        for it in repo_list(cur):
            sub = f"{cur}\\{it['name']}" if cur else it["name"]
            if it["dir"]:
                if not it.get("link"):
                    todo.append(sub)
            else:
                out.append((sub, int(it.get("size") or 0), int(it.get("mtime") or 0)))
                if len(out) > max_files:
                    raise RemoteError(f"Zu viele Dateien in {rel} (über {max_files}).")
    return out


def repo_sha256(rel, mbps=None, deadline=None, cancel=None):
    """SHA-256 einer Repository-Datei, in Stücken gelesen (kein RAM-Bedarf). mbps: höchstens so viele MB/s
    (Last auf dem Dateiserver begrenzen). Gibt None zurück, wenn deadline (time.time()) oder cancel() greift."""
    import time as _time
    h = hashlib.sha256()
    t0 = _time.time()
    done = 0
    with repo_open_rel(rel) as fh:
        while True:
            if (deadline and _time.time() > deadline) or (cancel and cancel()):
                return None
            buf = fh.read(1024 * 1024)
            if not buf:
                break
            h.update(buf)
            done += len(buf)
            if mbps:
                ahead = done / (mbps * 1024 * 1024) - (_time.time() - t0)
                if ahead > 0:
                    _time.sleep(min(ahead, 1.0))
    return h.hexdigest()


def repo_search(rel, q, limit=200, max_dirs=3000, seconds=15, refresh=False):
    """Suche mit Index im Arbeitsspeicher: ist er da, kommen die Treffer sofort (und er wird nach REPO_CACHE_TTL
    im Hintergrund aufgefrischt); sonst direkt auf der Freigabe (repo_search_live). refresh=True: neu einlesen
    und diese Suche direkt ausführen."""
    import time as _time
    rx = name_pattern(q, RemoteError)
    key = _repo_key()
    if refresh:
        # Alten Stand behalten, bis der neue fertig ist (sonst suchten bis zu 10 Minuten lang alle direkt auf der Freigabe)
        with _rc_lock:
            _rc["failed"] = None
        _repo_cache_start(key, force=True)
    else:
        with _rc_lock:
            fresh = _rc["key"] == key and _rc["entries"] is not None and _time.time() - _rc["at"] < REPO_CACHE_TTL
        if not fresh:
            _repo_cache_start(key)
        r = _repo_cache_search(key, rel, rx, limit)
        if r is not None:
            return r
    r = repo_search_live(rel, q, limit, max_dirs, seconds)
    r["cached"] = False
    return r


def repo_search_live(rel, q, limit=200, max_dirs=3000, seconds=15):
    """Dateien und Ordner im Repository finden (ab rel, alle Unterordner). q: Teil des Namens, ohne Groß-/Kleinschreibung;
    * und ? als Platzhalter (alle anderen Zeichen gelten wörtlich, auch [ ]). Verknüpfungen werden nicht verfolgt.
    Grenzen: limit Treffer, max_dirs Ordner, seconds Sekunden – „truncated“ sagt, ob abgebrochen wurde. Ein Fehler im
    Startordner (Repository nicht erreichbar, Anmeldung, Ordner fehlt) wird gemeldet, nicht als „keine Treffer“."""
    import time as _time
    rx = name_pattern(q, RemoteError)
    if not _search_sem.acquire(timeout=3):
        raise RemoteError("Gerade laufen schon mehrere Suchen – bitte einen Moment warten.")
    try:
        start = (rel or "").replace("/", "\\").strip("\\")
        todo, hits, dirs, truncated = [start], [], 0, False
        t0 = _time.time()
        while todo:
            if dirs >= max_dirs or _time.time() - t0 > seconds:
                truncated = True
                break
            cur = todo.pop(0)
            dirs += 1
            try:
                items = repo_list(cur)
            except Exception:
                if dirs == 1:
                    raise  # Startordner: der Benutzer soll den Grund sehen
                continue  # nicht lesbarer Unterordner: überspringen
            for it in items:
                if rx.search(it["name"]):
                    if len(hits) >= limit:
                        truncated = True
                        break
                    hits.append({"folder": cur, "name": it["name"], "dir": it["dir"], "size": it.get("size", 0)})
                if it["dir"] and not it.get("link"):
                    todo.append(f"{cur}\\{it['name']}" if cur else it["name"])
            if truncated:
                break
        hits.sort(key=lambda x: (not x["dir"], (x["folder"] + "\\" + x["name"]).lower()))
        return {"items": hits, "truncated": truncated, "dirs": dirs}
    finally:
        _search_sem.release()


def _repo_walk(path):
    """Liefert (relativer_pfad, absoluter_pfad) aller Dateien. Nicht lesbare Ordner brechen ab (statt ein
    unvollständiges Paket zu installieren); Verknüpfungen im Paketordner werden nicht verfolgt."""
    if _is_unc(path):
        import smbclient
        from .clientfiles import _smb_errors
        todo = [path]
        while todo:
            cur = todo.pop()
            try:
                entries = list(smbclient.scandir(cur))
            except _smb_errors() as e:
                raise RemoteError(f"Paketordner nicht vollständig lesbar ({cur}): {e}")
            for e in entries:
                st = e.stat(follow_symlinks=False)
                attr = getattr(st, "st_file_attributes", 0) or 0
                if attr & 0x400:
                    raise RemoteError(f"Verknüpfung im Paketordner nicht unterstützt: {ntpath.join(cur, e.name)}")
                full = ntpath.join(cur, e.name)
                if attr & 0x10:
                    todo.append(full)
                else:
                    yield ntpath.relpath(full, path), full
    else:
        def fail(e):
            raise RemoteError(f"Paketordner nicht vollständig lesbar: {e}")
        for dirpath, _dirs, files in os.walk(path, onerror=fail):
            for f in files:
                full = os.path.join(dirpath, f)
                yield os.path.relpath(full, path).replace("/", "\\"), full


def _repo_open(path):
    if _is_unc(path):
        import smbclient
        return smbclient.open_file(path, mode="rb")
    return open(path, "rb")


def _not_found(e):
    s = str(e)
    return isinstance(e, FileNotFoundError) or "OBJECT_NAME_NOT_FOUND" in s or "OBJECT_PATH_NOT_FOUND" in s \
        or "No such file" in s


def repo_stat(rel):
    """'dir', 'file' oder None (gibt es nicht) für einen Pfad im Repository. Ist das Repository selbst nicht erreichbar
    (Freigabe weg, Anmeldung, Zeitüberschreitung, lokaler Ordner fehlt), kommt RemoteError – nie „gibt es nicht“
    (2.26: sonst würde ein Netzproblem zum „Bruch“)."""
    root = repo_root()
    path = repo_join(root, rel)
    if _is_unc(root):
        import smbclient
        _smb_session(_repo_server(root), config.repo_login(), config.repo_password())
        from .clientfiles import _smb_errors
        try:
            st = smbclient.stat(path)
        except _smb_errors() as e:
            if _not_found(e):
                return None
            raise RemoteError(f"Repository: {e}")
        import stat as _st
        return "dir" if _st.S_ISDIR(st.st_mode) else "file"
    if not os.path.isdir(root):
        raise RemoteError(f"Repository nicht erreichbar: {root}")
    return "dir" if os.path.isdir(path) else ("file" if os.path.isfile(path) else None)


def repo_open_rel(rel):
    """Öffnet eine Datei aus dem Repository zum Lesen (z. B. für die MSI-Erkennung)."""
    root = repo_root()
    path = repo_join(root, rel)
    if _is_unc(root):
        _smb_session(_repo_server(root), config.repo_login(), config.repo_password())
    return _repo_open(path)


def copy_package(folder, host, name, single_file=None):
    """Kopiert einen Paketordner (oder nur eine Datei daraus) aus dem Repository nach
    \\\\host\\ADMIN$\\Temp\\PocketKnife\\name."""
    import smbclient
    host = kerberos.host(host)
    secure_workdir(host)
    root = repo_root()
    src = repo_join(root, folder)
    if _is_unc(root):
        _smb_session(_repo_server(root), config.repo_login(), config.repo_password())
    _smb_session(host, config.service_login(), config.service_password())
    dest = f"\\\\{host}\\ADMIN$\\Temp\\PocketKnife\\{name}"
    smbclient.makedirs(dest, exist_ok=True)
    count, total = 0, 0
    items = [(ntpath.basename(single_file), repo_join(root, ntpath.join(folder or "", single_file)))] if single_file \
        else _repo_walk(src)
    for rel, full in items:
        target = ntpath.join(dest, rel)
        parent = ntpath.dirname(target)
        if parent and parent != dest:
            smbclient.makedirs(parent, exist_ok=True)
        with _repo_open(full) as fsrc, smbclient.open_file(target, mode="wb") as fdst:
            shutil.copyfileobj(fsrc, fdst, 1024 * 1024)
            total += fsrc.tell() if hasattr(fsrc, "tell") else 0
        count += 1
    if count == 0:
        raise RemoteError(f"Paketordner ist leer oder nicht vorhanden: {src}")
    return count, total


def remove_package_copy(host, name):
    """Entfernt kopierte Paketdateien (falls die Ausführung gar nicht erst gestartet wurde)."""
    try:
        host = kerberos.host(host)
        _smb_session(host, config.service_login(), config.service_password())
        _smb_rmtree(f"\\\\{host}\\ADMIN$\\Temp\\PocketKnife\\{name}")  # folgt keinen Verknüpfungen
    except Exception:
        pass


def _smb_roundtrip(folder, size=4 * 1024 * 1024):
    """Schreibt eine Testdatei, liest sie zurück, vergleicht und löscht sie. Misst den Durchsatz."""
    import hashlib
    import smbclient
    data = os.urandom(size)
    path = ntpath.join(folder, f".pocketknife-test-{int(time.time())}.tmp")
    res = {"write": False, "read": False, "verified": False, "deleted": False}
    t0 = time.time()
    with smbclient.open_file(path, mode="wb") as f:
        f.write(data)
    t1 = time.time()
    res["write"] = True
    res["write_mb_s"] = round(size / 1048576 / max(t1 - t0, 0.001), 1)
    with smbclient.open_file(path, mode="rb") as f:
        back = f.read()
    t2 = time.time()
    res["read"] = True
    res["read_mb_s"] = round(size / 1048576 / max(t2 - t1, 0.001), 1)
    res["verified"] = hashlib.sha256(back).digest() == hashlib.sha256(data).digest()
    smbclient.remove(path)
    res["deleted"] = True
    return res


def test_repository():
    """Prüft das Software-Repository Schritt für Schritt."""
    steps = []

    def step(name, fn):
        t = time.time()
        try:
            detail = fn()
            steps.append({"step": name, "ok": True, "detail": detail, "ms": int((time.time() - t) * 1000)})
            return True
        except Exception as e:
            steps.append({"step": name, "ok": False, "detail": str(e), "ms": int((time.time() - t) * 1000)})
            return False

    root = repo_root()
    if not _is_unc(root):
        ok = step("Ordner im Container lesen", lambda: f"{len(repo_list(''))} Einträge in {root}")
        if ok:
            def local_rw():
                p = os.path.join(root, ".pocketknife-test.tmp")
                try:
                    with open(p, "wb") as f:
                        f.write(b"x")
                    os.remove(p)
                    return "beschreibbar"
                except OSError:
                    return "nur lesbar (für Pakete ausreichend)"
            step("Schreibzugriff", local_rw)
        return {"path": root, "type": "Ordner", "steps": steps, "ok": all(s["ok"] for s in steps)}

    import socket
    server = _repo_server(root)
    info = {}
    def resolve():
        try:
            return f"{server} → {socket.gethostbyname(server)}"
        except OSError:
            raise RemoteError(f"{server} ist nicht auflösbar – DNS-Server in der .env (PK_DNS_1) prüfen")
    if not step("DNS-Auflösung", resolve):
        return {"path": root, "type": "SMB", "steps": steps, "ok": False}
    def port445():
        if not util.tcp_open(server, 445, 5):
            raise RemoteError("Port 445 nicht erreichbar (Firewall?)")
        return "offen"
    if not step("TCP 445 erreichbar", port445):
        return {"path": root, "type": "SMB", "steps": steps, "ok": False}

    def login():
        sess = _smb_session(server, config.repo_login(), config.repo_password())
        info.update(_session_info(sess))
        txt = f"angemeldet als {config.repo_login()}"
        if info:
            txt += f" · {info.get('dialect', '?')} · Signierung {'ja' if info.get('signing') else 'nein'}" \
                   f" · Verschlüsselung {'ja' if info.get('encryption') else 'nein'}"
        return txt
    if not step("Anmeldung", login):
        return {"path": root, "type": "SMB", "steps": steps, "ok": False}
    if not step("Freigabe auflisten", lambda: f"{len(repo_list(''))} Einträge"):
        return {"path": root, "type": "SMB", "steps": steps, "ok": False}
    rt = {}

    def roundtrip():
        rt.update(_smb_roundtrip(_unc(root)))
        if not rt["verified"]:
            raise RemoteError("Zurückgelesene Daten stimmen nicht überein!")
        return f"4 MB geschrieben ({rt['write_mb_s']} MB/s), gelesen ({rt['read_mb_s']} MB/s), geprüft, gelöscht"
    step("Schreiben / Lesen / Löschen", roundtrip)
    return {"path": root, "type": "SMB", "info": info, "steps": steps, "ok": all(s["ok"] for s in steps)}


def test_target_smb(host):
    """Prüft den SMB-Zugriff auf ADMIN$ des Zielcomputers (für Paketkopien)."""
    host = kerberos.host(host)
    sess = _smb_session(host, config.service_login(), config.service_password())
    import smbclient
    folder = f"\\\\{host}\\ADMIN$\\Temp\\PocketKnife"
    smbclient.makedirs(folder, exist_ok=True)
    rt = _smb_roundtrip(folder, 1024 * 1024)
    return {**_session_info(sess), **rt}


def smb_probe(server, auth, user=None, password=None, share="ADMIN$"):
    """Eigene SMB-Verbindung mit erzwungenem Verfahren (ohne den Sitzungs-Cache von smbclient) –
    für den Kerberos-Test. Öffnet die Freigabe und trennt wieder."""
    import uuid
    from smbprotocol.connection import Connection
    from smbprotocol.session import Session
    from smbprotocol.tree import TreeConnect
    _guard_check()
    user = user or config.service_login()
    password = config.service_password() if password is None else password
    conn = Connection(uuid.uuid4(), server, 445)
    conn.connect(timeout=15)
    try:
        sess = Session(conn, kerberos.login(user, auth), password, require_encryption=False,
                       auth_protocol=kerberos.smb_protocol(auth))
        try:
            sess.connect()
        except Exception as e:
            text, _acc = guard.explain(str(e))
            raise RemoteError((text or kerberos.explain(str(e)) or "") + f" ({e})")
        tree = TreeConnect(sess, f"\\\\{server}\\{share}")
        tree.connect()
        info = _session_info(sess)
        tree.disconnect()
        sess.disconnect()
        return info
    finally:
        try:
            conn.disconnect(True)
        except Exception:
            pass


# --- Arbeitsordner absichern ------------------------------------------------------
# C:\Windows\Temp\PocketKnife: normale Benutzer dürfen in C:\Windows\Temp Ordner anlegen. Ein vorab angelegter
# „PocketKnife“-Ordner mit eigenen Rechten würde es erlauben, Skripte/Installer vor dem Start als SYSTEM auszutauschen.
# Deshalb: fremden Ordner beiseiteschieben und die Rechte auf SYSTEM + Administratoren beschränken.
SECURE_BASE = r"""$ErrorActionPreference = 'Stop'
$b = Join-Path $env:windir 'Temp\PocketKnife'
# Nur Cmdlets, Eigenschaften und icacls – läuft auch im Constrained Language Mode (SRP/AppLocker) unverändert sicher.
# Besitzer und Berechtigungen kommen aus der SDDL (sprachunabhängig, keine .NET-Methoden).
$me = ''
try { $me = ((@(& whoami.exe /user /fo csv /nh) | Select-Object -Last 1) -split ',')[-1].Trim().Trim('"') } catch {}
$okSids = @('SY', 'BA', 'S-1-5-18', 'S-1-5-32-544')
if ($me -like 'S-1-*') { $okSids += $me }
function PkTrusted([string]$p) {
  try { $s = [string](Get-Acl -LiteralPath $p).Sddl } catch { return 'Rechte nicht lesbar' }
  if ($s -notmatch '^O:(.+?)G:') { return 'kein Besitzer' }
  $o = $Matches[1]
  if ($okSids -notcontains $o) { return $o }
  # jede Erlaubnis (A) darf nur SYSTEM/Administratoren gelten – sonst hat ein Benutzer Rechte hinterlegt
  foreach ($m in @(Select-String -InputObject $s -Pattern '\(A;[^;]*;[^;]*;[^;]*;[^;]*;([^)]+)\)' -AllMatches | ForEach-Object { $_.Matches })) {
    if ($okSids -notcontains $m.Groups[1].Value) { return 'Recht für ' + $m.Groups[1].Value }
  }
  return ''
}
function PkAside([string]$why) {
  Rename-Item -LiteralPath $b -NewName ('PocketKnife.untrusted-' + (Get-Date -Format 'yyyyMMddHHmmss') + '-' + (Get-Random))
  'PK_MOVED:' + $why
}
if (Test-Path -LiteralPath $b) {
  $why = PkTrusted $b
  if ($why) { PkAside $why }
}
$new = $false
if (-not (Test-Path -LiteralPath $b)) {
  $new = $true
  if ($ExecutionContext.SessionState.LanguageMode -eq 'FullLanguage') {
    # Gleich mit den richtigen Rechten anlegen – sonst erbt der Ordner kurz die Rechte von C:\Windows\Temp
    $sec = New-Object System.Security.AccessControl.DirectorySecurity
    $sec.SetAccessRuleProtection($true, $false)
    foreach ($sid in 'S-1-5-18', 'S-1-5-32-544') {
      $sec.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule((New-Object System.Security.Principal.SecurityIdentifier($sid)), 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')))
    }
    try { [void][IO.Directory]::CreateDirectory($b, $sec) } catch { New-Item -ItemType Directory -Path $b | Out-Null }
  } else {
    New-Item -ItemType Directory -Path $b | Out-Null  # ohne -Force: hat ihn gerade jemand angelegt → Fehler, nächster Versuch
  }
}
& icacls.exe $b /setowner '*S-1-5-32-544' /Q | Out-Null
& icacls.exe $b /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' /Q | Out-Null
if ($LASTEXITCODE -ne 0) { 'PK_ACLFAIL:' + $LASTEXITCODE; return }
# Audit 2.29: danach erneut prüfen (Besitzer, Rechte) – ein im selben Moment angelegter fremder Ordner fällt so auf
$why = PkTrusted $b
if (-not $why -and $new -and @(Get-ChildItem -LiteralPath $b -Force).Count -gt 0) { $why = 'Inhalt beim Anlegen' }
if ($why) { PkAside $why; 'PK_UNTRUSTED'; return }
'PK_BASE_OK'
"""
_secured = {}


def secure_workdir(host, force=False):
    """Vor jeder Ausführung/Kopie (höchstens alle 10 Minuten je Computer)."""
    key = (host or "").lower()
    if not force and time.time() - _secured.get(key, 0) < 600:
        return
    out, err, _ = ps(host, SECURE_BASE)
    if "PK_MOVED:" in out:
        from . import pklog
        pklog.warn("deploy", f"{host}: fremder Ordner C:\\Windows\\Temp\\PocketKnife (Grund: {out.split('PK_MOVED:', 1)[1].split()[0] if out.split('PK_MOVED:', 1)[1].split() else '?'}) "
                             "wurde beiseitegeschoben und neu angelegt.")
    if "PK_BASE_OK" not in out:
        raise RemoteError("Arbeitsordner C:\\Windows\\Temp\\PocketKnife konnte nicht abgesichert werden: " + (err or out).strip()[:500])
    _secured[key] = time.time()


# Angemeldete Benutzer live (Computeransicht, Kiosk)
SESSIONS_PS = r"""# @sessions-begin (2.32.6: gleich in remote.SESSIONS_PS – der Selbsttest prüft das)
# Sitzungszustand je angemeldetem Benutzer: getrennt (Remotedesktop/Benutzerwechsel, quser) oder gesperrt (LogonUI.exe
# läuft in seiner Sitzung). Nur Cmdlets, Hashtables und quser.exe – läuft auch im eingeschränkten Sprachmodus.
$pkLui = @(Get-CimInstance Win32_Process -Filter "Name='LogonUI.exe'" | ForEach-Object { [int]$_.SessionId })
$pkQ = @{}
$pkQo = @()
try { $pkQo = @(& (Join-Path $env:SystemRoot 'System32\quser.exe') 2>$null) } catch {}
# ID steht rechtsbündig unter „ID“ (Kopfzeile in jeder Sprache) – ab dort lesen, Benutzer-/Sitzungsnamen davor egal
$pkIp = -1
if ($pkQo.Count) { $pkIp = ("" + $pkQo[0]).IndexOf(' ID ') }
foreach ($l in @($pkQo | Select-Object -Skip 1)) {
  $t = "" + $l
  if ($pkIp -gt 4 -and $t.Length -gt $pkIp) { $t = $t.Substring($pkIp - 4) } elseif ($t -match '^\s*>?\S+\s+(?:\S+\s+)?(\d+\s+\S+.*)$') { $t = $matches[1] }
  if ($t -match '^\s*(\d+)\s+(\S+)') { $pkQ[[int]$matches[1]] = $matches[2] }
}
$pkSt = @{}
$pkExp = @(Get-CimInstance Win32_Process -Filter "Name='explorer.exe'" | ForEach-Object {
  $o = Invoke-CimMethod -InputObject $_ -MethodName GetOwner
  if ($o -and $o.User) {
    $n = $o.Domain + '\' + $o.User
    $sid = [int]$_.SessionId
    $s = 'active'
    if ("" + $pkQ[$sid] -match '^(Disc|Getr|D.co|Desc|Disco|Getrennt)') { $s = 'disconnected' } elseif ($pkLui -contains $sid) { $s = 'locked' }
    # mehrere Sitzungen eines Benutzers: aktiv vor gesperrt vor getrennt
    $rank = @{ active = 3; locked = 2; disconnected = 1 }
    if (-not $pkSt.ContainsKey($n) -or $rank[$s] -gt $rank[$pkSt[$n]]) { $pkSt[$n] = $s }
    $n
  } })
# 2.34: Server Core (kein Explorer) – angemeldete Benutzer aus quser (Name ohne Domäne)
if ($pkExp.Count -eq 0 -and $pkQo.Count -gt 1) {
  $pkExp = @(foreach ($l in @($pkQo | Select-Object -Skip 1)) {
    $t = "" + $l
    if ($pkIp -gt 4 -and $t.Length -gt $pkIp) { $nm = $t.Substring(0, $pkIp - 4); $rest = $t.Substring($pkIp - 4) } else { $nm = $t; $rest = '' }
    if ($nm -match '^\s*>?(\S+)') {
      $n = $matches[1]; $s = 'active'
      if ($rest -match '^\s*(\d+)\s+(\S+)') { if ($matches[2] -match '^(Disc|Getr|D.co|Desc|Disco|Getrennt)') { $s = 'disconnected' } elseif ($pkLui -contains [int]$matches[1]) { $s = 'locked' } }
      if (-not $pkSt.ContainsKey($n)) { $pkSt[$n] = $s }
      $n
    } })
}
# @sessions-end
"""
LOGGEDON_PS = r"""$ErrorActionPreference = 'SilentlyContinue'
$u = @()
$cs = (Get-CimInstance Win32_ComputerSystem).UserName
if ($cs) { $u += $cs }
""" + SESSIONS_PS + r"""# 2.34: quser (Server Core) liefert Namen ohne Domäne – nicht doppelt zu „DOMÄNE\name“ aus Win32_ComputerSystem
foreach ($x in $pkExp) {
  $full = @($u | Where-Object { $_ -eq $x -or ("" + $_).Split('\')[-1] -eq $x })
  if ($full.Count) { if ($pkSt.ContainsKey($x) -and -not $pkSt.ContainsKey($full[0])) { $pkSt[$full[0]] = $pkSt[$x] } } else { $u += $x }
}
'PKUSERS:' + ((@($u | Where-Object { $_ }) | Select-Object -Unique) -join ', ')
'PKSTATES:' + ((@($pkSt.GetEnumerator() | Where-Object { $_.Value -ne 'active' } | ForEach-Object { $_.Key + '=' + $_.Value })) -join ';')
"""


def parse_states(out):
    """2.32.6: {"DOM\\user": "locked"|"disconnected"} aus der Zeile PKSTATES: (aktive Sitzungen fehlen)."""
    # nur die Zeile mit PKSTATES: – sie ist leer, wenn alle Sitzungen aktiv sind (2.32.11: ohne Zeilenende danach kein Fehler)
    line = (out.split("PKSTATES:", 1)[1].splitlines() or [""])[0] if "PKSTATES:" in (out or "") else ""
    res = {}
    for part in line.split(";"):
        k, _, v = part.strip().partition("=")
        if k.strip() and v.strip() in ("locked", "disconnected"):
            res[k.strip()] = v.strip()
    return res


# --- Ausführung als SYSTEM ---------------------------------------------------
WRAPPER = r"""$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$PKDir = '__DIR__'
$PKLog = Join-Path $PKDir 'pk_output.log'
$global:PKExit = 0
function PK-Run([string]$File, [string]$Arguments) {
  $global:PKOut = @()  # Ausgabe des letzten Aufrufs – für Auswertungen danach (z. B. winget „keine passende Aktualisierung“)
  $global:PKStdN = 0; $global:PKErrTxt = ''
  $so = Join-Path $PKDir ('pk_so_' + [guid]::NewGuid().ToString('N') + '.txt')
  $se = $so + '.err'
  $sp = @{ FilePath = $File; Wait = $true; PassThru = $true; NoNewWindow = $true; WorkingDirectory = $PKDir;
           RedirectStandardOutput = $so; RedirectStandardError = $se }
  if ($Arguments) { $sp.ArgumentList = $Arguments }
  Write-Output ('> ' + $File + ' ' + $Arguments)
  $p = $null
  try { $p = Start-Process @sp -ErrorAction Stop }
  catch {
    # 2.32.2: Programm startet gar nicht (z. B. von AppLocker/Softwareeinschränkung gesperrt) – Grund nennen statt leerem Exitcode
    Write-Output ('Konnte nicht gestartet werden: ' + $_.Exception.Message)
    $global:PKExit = 1; Write-Output 'Exitcode: 1'; return
  }
  foreach ($f in @($so, $se)) {
    if (Test-Path $f) {
      # 2.32.7: erst als UTF-8 lesen; enthält das Ersatzzeichen (Ausgabe in der Konsolen-Codepage, z. B. cmd.exe ohne
      # Konsole), als OEM lesen – sonst kommen Umlaute als „?“ an
      $pkEnc = 'UTF8'
      if (([string](Get-Content -Path $f -Raw -Encoding UTF8)) -match '\uFFFD') { $pkEnc = 'Oem' }
      $pkL = @(Get-Content -Path $f -Encoding $pkEnc | Where-Object { $_ -notmatch '^\s*[-\\|/]?\s*$' -and $_ -notmatch '[▀-▟]' })
      $global:PKOut += $pkL
      if ($f -eq $so) { $global:PKStdN = $pkL.Count } else { $global:PKErrTxt = ($pkL -join '') }
      $pkL
      Remove-Item $f -Force -ErrorAction SilentlyContinue
    }
  }
  $global:PKExit = $p.ExitCode
  Write-Output ('Exitcode: ' + $p.ExitCode)
}
# 2.32.2: PowerShell-Skript (eigene Schritte, Tools, .ps1 aus dem Repository) erst ganz normal mit -File starten. Verweigert
# Windows genau diese Datei (Ausführungsrichtlinie per GPO, Signatur, Sicherheitszone: Exitcode 1, keine Ausgabe, Fehler
# „UnauthorizedAccess“ mit dem Dateinamen), lädt POCKET KNIFE ihren Inhalt – das Skript lief dann noch nicht, es läuft
# also nie doppelt. Parameter werden übergeben, $PSScriptRoot/$PSCommandPath zeigen weiter auf die Originaldatei.
function PK-RunPs1([string]$Path, [string]$Arguments) {
  PK-Run "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" (('__PSFLAGS__ -File "' + $Path + '" ' + $Arguments).Trim())
  if ($global:PKExit -ne 1 -or $global:PKStdN -gt 0) { return }
  $pkE = $global:PKErrTxt -replace '\s', ''
  if (-not ($pkE -match 'FullyQualifiedErrorId:UnauthorizedAccess(?!Exception)' -and $pkE.Contains(((Split-Path -Leaf $Path) -replace '\s', '')))) { return }
  # 2.32.5: Fehler mit Positionsangabe (… .ps1:4 Zeichen:1) kommt aus dem laufenden Skript (z. B. ein verweigertes
  # Unterskript) – das Skript lief schon, also nie ein zweites Mal. Softwareeinschränkung/AppLocker: bewusste Sperre,
  # die POCKET KNIFE nicht aushebelt.
  if ($pkE -match '\.ps1:\d+(char|Zeichen):' -or $pkE -match '(?i)Softwareeinschr|softwarerestriction|Gruppenrichtlinieblockiert|blockedbygrouppolicy|AppLocker') { return }
  $global:PKLoaded = $true
  Write-Output 'Windows lässt die Skriptdatei nicht zu (Ausführungsrichtlinie oder Sicherheitszone) – POCKET KNIFE lädt ihren Inhalt.'
  $pkC = [string](Get-Content -LiteralPath $Path -Raw)
  # „using namespace/module/assembly“ muss ganz vorne stehen – nicht in den Block, sondern davor
  $pkU = (@(($pkC -split "`r?`n") | Where-Object { $_ -match '^\s*using\s+(namespace|module|assembly)\s' }) -join "`r`n")
  if ($pkU) { $pkC = $pkC -replace '(?im)^\s*using\s+(namespace|module|assembly)\s.*$', '' }
  $pkC = $pkC -replace '(?i)\$PSScriptRoot\b|\$\{PSScriptRoot\}', '$$PKScriptRoot' -replace '(?i)\$PSCommandPath\b|\$\{PSCommandPath\}|\$MyInvocation\.MyCommand\.Path\b', '$$PKScriptPath'
  # $PKLoadOk: nur gesetzt, wenn PowerShell den Inhalt übersetzen konnte – sonst Exitcode 1 statt „Erfolgreich“
  $pkH = $(if ($pkU) { $pkU + "`r`n" } else { '' }) + '$PKLoadOk = $true' + "`r`n" + '$PKScriptRoot = ''' + (Split-Path -Parent $Path).Replace("'", "''") + "'`r`n" + '$PKScriptPath = ''' + $Path.Replace("'", "''") + "'`r`n"
  $pkLf = Join-Path $PKDir ('pk_load_' + [guid]::NewGuid().ToString('N') + '.ps1')
  Set-Content -LiteralPath $pkLf -Value ($pkH + '& {' + "`r`n" + $pkC + "`r`n} " + $Arguments) -Encoding UTF8
  PK-Run "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" ('__PSFLAGS__ __PSLOADPRE__' + $pkLf + '__PSLOADPOST__')
}
if ($ExecutionContext.SessionState.LanguageMode -ne 'FullLanguage') {
  ('Hinweis: PowerShell läuft auf diesem Computer im eingeschränkten Sprachmodus (' + $ExecutionContext.SessionState.LanguageMode + ', AppLocker/WDAC).') | Out-File -FilePath $PKLog -Encoding utf8 -Append
}
try {
  & {
__BODY__
  } *>&1 | ForEach-Object { "$_" } | Out-File -FilePath $PKLog -Encoding utf8 -Append -Width 500
} catch {
  ('FEHLER: ' + $_.Exception.Message) | Out-File -FilePath $PKLog -Encoding utf8 -Append
  if ($global:PKExit -eq 0) { $global:PKExit = 1 }
}
Set-Content -Path (Join-Path $PKDir 'pk_exit.txt') -Value ([string]$global:PKExit) -Encoding Ascii
"""

REGISTER = r"""$ErrorActionPreference = 'Stop'
$dir = '__DIR__'
New-Item -ItemType Directory -Path $dir -Force | Out-Null
Remove-Item (Join-Path $dir 'pk_exit.txt') -ErrorAction SilentlyContinue
$w = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__B64__'))
[System.IO.File]::WriteAllText((Join-Path $dir 'pk_run.ps1'), $w, (New-Object System.Text.UTF8Encoding $true))
__EXTRA__
# Windows PowerShell mit vollem Pfad und festen Schaltern (siehe util.PS_FLAGS)
$pkRunF = Join-Path $dir 'pk_run.ps1'
$a = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument __PKSTART__ -WorkingDirectory $dir
$pkRunAs = '__RUNAS__'
if ($pkRunAs -eq 'user') {
  # Als angemeldeter Benutzer (z. B. für Software, die nur im Benutzerprofil installiert ist)
  $pkSid = '__RUNSID__'
  $sess = @(Get-CimInstance Win32_Process -Filter "Name='explorer.exe'" | ForEach-Object {
    $o = Invoke-CimMethod -InputObject $_ -MethodName GetOwner -ErrorAction SilentlyContinue
    $q = Invoke-CimMethod -InputObject $_ -MethodName GetOwnerSid -ErrorAction SilentlyContinue
    if ($o.User) { [pscustomobject]@{ Name = ($o.Domain + '\' + $o.User); Sid = $q.Sid } } })
  if ($pkSid) { $sess = @($sess | Where-Object { $_.Sid -eq $pkSid }) }
  if (-not $sess) {
    $who = $pkSid
    try { if ($pkSid) { $who = (New-Object System.Security.Principal.SecurityIdentifier($pkSid)).Translate([System.Security.Principal.NTAccount]).Value } } catch {}
    'PK_NOUSER:' + $who
    return
  }
  $u = $sess[0].Name
  & icacls.exe $dir /grant ($u + ':(OI)(CI)M') /T /Q | Out-Null
  $p = New-ScheduledTaskPrincipal -UserId $u -LogonType Interactive -RunLevel Highest
} else {
  $p = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
}
$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes __TIMEOUT__) -MultipleInstances IgnoreNew
# Nie mit Kennwort: Prinzipal SYSTEM bzw. angemeldeter Benutzer (Interactive) – auf dem Client wird nichts gespeichert
Register-ScheduledTask -TaskName '__TASK__' -TaskPath '\POCKETKNIFE\' -Action $a -Principal $p -Settings $s -Force | Out-Null
Start-ScheduledTask -TaskName '__TASK__' -TaskPath '\POCKETKNIFE\'
'PK_STARTED'
"""
WRAPPER = WRAPPER.replace("__PSFLAGS__", util.PS_FLAGS).replace("__PSLOADPRE__", util.PS_LOAD_PRE_Q).replace("__PSLOADPOST__", util.PS_LOAD_POST_Q)
REGISTER = REGISTER.replace("__PKSTART__", util.ps_start_expr("$pkRunF"))

# Dienstkonto: direkt als Prozess des Dienstkontos starten. Win32_Process.Create läuft mit der Identität der
# WinRM-Sitzung (volle Administratorrechte), der Prozess gehört aber nicht zur WinRM-Sitzung und überlebt
# deshalb Verbindungsabbrüche. Keine geplante Aufgabe, kein Kennwort auf dem Client.
START_DIRECT = r"""$ErrorActionPreference = 'Stop'
$dir = '__DIR__'
New-Item -ItemType Directory -Path $dir -Force | Out-Null
Remove-Item (Join-Path $dir 'pk_exit.txt') -ErrorAction SilentlyContinue
$w = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__B64__'))
[System.IO.File]::WriteAllText((Join-Path $dir 'pk_run.ps1'), $w, (New-Object System.Text.UTF8Encoding $true))
__EXTRA__
# 2.31: über cmd.exe, damit Fehlermeldungen von PowerShell selbst (Skript nicht ladbar, Richtlinie) in pk_err.txt landen –
# sonst endet der Prozess „ohne Ergebnis“, und niemand sieht warum
$pkRunF = Join-Path $dir 'pk_run.ps1'
$cmd = $env:SystemRoot + '\System32\cmd.exe /d /c ""' + $env:SystemRoot + '\System32\WindowsPowerShell\v1.0\powershell.exe" ' + __PKSTART__ + ' 2>"' + (Join-Path $dir 'pk_err.txt') + '""'
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd; CurrentDirectory = $dir }
if ($r.ReturnValue -ne 0) { 'PK_STARTFAIL:' + $r.ReturnValue; return }
Set-Content -Path (Join-Path $dir 'pk_pid.txt') -Value ([string]$r.ProcessId) -Encoding Ascii
'PK_STARTED'
"""
START_DIRECT = START_DIRECT.replace("__PKSTART__", util.ps_start_expr("$pkRunF"))

POLL = r"""$dir = '__DIR__'
$f = Join-Path $dir 'pk_exit.txt'
$pf = Join-Path $dir 'pk_pid.txt'
$st = $null
if (-not (Test-Path $f) -and (Test-Path $pf)) {
  # Direkt gestarteter Prozess: läuft er noch? (PID nur gelten lassen, wenn die Befehlszeile zum Lauf passt)
  $id = 0; [void][int]::TryParse((Get-Content $pf -Raw).Trim(), [ref]$id)
  $p = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $id) -ErrorAction SilentlyContinue
  if ($p -and $p.CommandLine -and $p.CommandLine.Contains($dir)) { $st = 'PK_PROC:running' }
  elseif (-not (Test-Path $f)) { $st = 'PK_PROC:gone' }
}
if ($st -eq 'PK_PROC:gone') {
  # 2.31: Warum? Fehlerausgabe von PowerShell, letzte Zeilen der Ausgabe, Sperren durch Defender (ASR) oder AppLocker
  $st
  $ef = Join-Path $dir 'pk_err.txt'
  if (Test-Path $ef) { $t = ((Get-Content $ef -Raw) -replace '\s+', ' ').Trim(); if ($t) { 'PK_ERR:' + ($t -replace '^(.{0,600}).*$', '$1') } }
  $l = Join-Path $dir 'pk_output.log'
  if (Test-Path $l) { $t = ((Get-Content $l -Tail 5 -Encoding UTF8) -join ' | ').Trim(); if ($t) { 'PK_LAST:' + ($t -replace '^(.{0,600}).*$', '$1') } }
  # 2.32.1: nur Ereignisse seit dem Start dieses Laufs. Defender 1121 = blockiert, 1122 = nur überwacht (blockiert NICHT –
  # bei „Überwachen“ entsteht so ein Eintrag bei jedem Start über WMI, er ist nie der Grund)
  $since = (Get-Date).AddMinutes(-30)
  if (Test-Path $pf) { $since = (Get-Item $pf).LastWriteTime.AddMinutes(-1) }
  $rx = 'ID:\s*([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})'
  # 2.32.5: nur Ereignisse, die zu diesem Lauf passen können (PowerShell, cmd, WMI-Start, Arbeitsordner) – nicht z. B.
  # die ständigen Blockaden der lsass-Regel oder ein Virenfund irgendwo auf dem Rechner
  $rel = 'powershell|cmd\.exe|wmiprvse|PocketKnife|amsi:'
  try { Get-WinEvent -FilterHashtable @{ LogName = 'Microsoft-Windows-Windows Defender/Operational'; Id = 1121, 1122; StartTime = $since } -MaxEvents 20 -ErrorAction Stop |
    Where-Object { [string]$_.Message -match $rel } | Select-Object -First 6 | ForEach-Object {
    $m = [string]$_.Message; $rid = $(if ($m -match $rx) { $matches[1] } else { '?' })
    $(if ($_.Id -eq 1121) { 'PK_ASRBLOCK:' } else { 'PK_ASRAUDIT:' }) + $rid } } catch {}
  # Virenschutz hat das Skript als Bedrohung erkannt (AMSI) – 1116 erkannt, 1117 Aktion ausgeführt
  try { Get-WinEvent -FilterHashtable @{ LogName = 'Microsoft-Windows-Windows Defender/Operational'; Id = 1116; StartTime = $since } -MaxEvents 10 -ErrorAction Stop |
    Where-Object { [string]$_.Message -match $rel } | Select-Object -First 1 | ForEach-Object { 'PK_AV:' + ((($_.Message -replace '\s+', ' ').Trim()) -replace '^(.{0,400}).*$', '$1') } } catch {}
  foreach ($q in @(@{ LogName = 'Microsoft-Windows-AppLocker/EXE and DLL'; Id = 8004 }, @{ LogName = 'Microsoft-Windows-AppLocker/MSI and Script'; Id = 8007 })) {
    try { $q.StartTime = $since; Get-WinEvent -FilterHashtable $q -MaxEvents 10 -ErrorAction Stop | Where-Object { [string]$_.Message -match $rel } |
      Select-Object -First 2 | ForEach-Object { 'PK_BLOCK:AppLocker: ' + ((($_.Message -replace '\s+', ' ').Trim()) -replace '^(.{0,300}).*$', '$1') } } catch {}
  }
  # abgestürzt? (Anwendungsfehler 1000 für powershell.exe/cmd.exe)
  try { Get-WinEvent -FilterHashtable @{ LogName = 'Application'; Id = 1000; StartTime = $since } -MaxEvents 5 -ErrorAction Stop |
    Where-Object { $_.Message -match 'powershell\.exe|cmd\.exe' } | Select-Object -First 1 | ForEach-Object { 'PK_CRASH:' + ((($_.Message -replace '\s+', ' ').Trim()) -replace '^(.{0,300}).*$', '$1') } } catch {}
}
elseif ($st) { $st }
elseif (Test-Path $f) {
  'PK_DONE:' + (Get-Content $f -Raw).Trim()
  '---PKLOG---'
  $l = Join-Path $dir 'pk_output.log'
  if (Test-Path $l) { $c = Get-Content $l -Raw -Encoding UTF8; if ($c -and $c.Length -gt 60000) { $c = $c.Substring($c.Length - 60000) }; $c }
} else {
  $t = Get-ScheduledTask -TaskName '__TASK__' -TaskPath '\POCKETKNIFE\' -ErrorAction SilentlyContinue
  if (-not $t) { 'PK_MISSING' } else { $i = $t | Get-ScheduledTaskInfo; 'PK_STATE:' + $t.State + ':' + $i.LastTaskResult }
}
"""

CLEANUP = r"""$pf = Join-Path '__DIR__' 'pk_pid.txt'
if (Test-Path $pf) {
  # Abbruch/Zeitüberschreitung: direkt gestarteten Prozess samt Unterprozessen (Installer) beenden
  $id = 0; [void][int]::TryParse((Get-Content $pf -Raw).Trim(), [ref]$id)
  $p = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $id) -ErrorAction SilentlyContinue
  if ($id -gt 0 -and $p -and $p.CommandLine -and $p.CommandLine.Contains('__DIR__')) { & taskkill.exe /PID $id /T /F 2>$null | Out-Null; Start-Sleep -Seconds 2 }
}
Stop-ScheduledTask -TaskName '__TASK__' -TaskPath '\POCKETKNIFE\' -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName '__TASK__' -TaskPath '\POCKETKNIFE\' -Confirm:$false -ErrorAction SilentlyContinue
# rmdir /s folgt keinen Verknüpfungen (Junctions) – Remove-Item -Recurse in Windows PowerShell 5.1 kann es,
# und im Benutzerkontext hätte der Benutzer eine Verknüpfung auf fremde Ordner anlegen können
if (Test-Path -LiteralPath '__DIR__') { & cmd.exe /c rmdir /s /q "__DIR__" 2>$null }
"""


# Reste eines unterbrochenen Laufs (POCKET KNIFE wurde neu gestartet, während auf dem Client etwas lief):
# alle Arbeitsordner und Aufgaben, deren Name mit dem Präfix „<Bereitstellung>_<Ziel>_“ beginnt.
# Läuft der Installer noch und ist jünger als die Zeitgrenze, bleibt er unangetastet (PK_BUSY) – ein Installer, den man
# mittendrin beendet, kann mehr Schaden anrichten als einer, der zu Ende läuft. Älter als die Zeitgrenze: beenden
# (wie bei einer normalen Zeitüberschreitung). Danach Aufgaben und Ordner entfernen.
CLEANUP_RUN = r"""$ErrorActionPreference = 'SilentlyContinue'
$b = Join-Path $env:windir 'Temp\PocketKnife'
$pre = '__PREFIX__'
$max = __MAXMIN__
$busy = 0; $killed = 0; $removed = 0; $codes = @()
$dirs = @(Get-ChildItem -LiteralPath $b -Directory -Force -Filter ($pre + '*'))
$tasks = @(Get-ScheduledTask -TaskPath '\POCKETKNIFE\' | Where-Object { $_.TaskName -like ('PK_' + $pre + '*') })
foreach ($d in $dirs) {
  $ef = Join-Path $d.FullName 'pk_exit.txt'
  if (Test-Path -LiteralPath $ef) { $codes += ((Get-Content -LiteralPath $ef -Raw).Trim()) }
  $pf = Join-Path $d.FullName 'pk_pid.txt'
  if (Test-Path -LiteralPath $pf) {
    $id = 0; [void][int]::TryParse((Get-Content -LiteralPath $pf -Raw).Trim(), [ref]$id)
    $p = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $id)
    # Ordnername vergleichen, ohne Groß/Klein (C:\WINDOWS vs. C:\Windows) – er enthält Lauf-Nummer und Zufallsteil
    if ($id -gt 0 -and $p -and $p.CommandLine -and $p.CommandLine.ToLower().Contains('\' + $d.Name.ToLower() + '\')) {
      $age = ((Get-Date) - $p.CreationDate).TotalMinutes
      if ($age -lt $max) { $busy++; continue }
      & taskkill.exe /PID $id /T /F 2>$null | Out-Null; $killed++; Start-Sleep -Seconds 2
    }
  }
}
foreach ($t in $tasks) {
  if ("$($t.State)" -eq 'Running') {
    $i = $t | Get-ScheduledTaskInfo
    if ($i -and ((Get-Date) - $i.LastRunTime).TotalMinutes -lt $max) { $busy++; continue }
    Stop-ScheduledTask -TaskName $t.TaskName -TaskPath '\POCKETKNIFE\'; $killed++; Start-Sleep -Seconds 2
  }
}
if ($busy -gt 0) { 'PK_BUSY:' + $busy; return }
foreach ($t in $tasks) { Unregister-ScheduledTask -TaskName $t.TaskName -TaskPath '\POCKETKNIFE\' -Confirm:$false }
foreach ($d in $dirs) { & cmd.exe /c rmdir /s /q ('"' + $d.FullName + '"') 2>$null; $removed++ }
'PK_CLEAN:' + $removed + ':' + $tasks.Count + ':' + $killed + ':' + ($codes -join ',')
"""


def cleanup_run(host, prefix, max_minutes):
    """Reste eines unterbrochenen Laufs entfernen. {"busy": n} solange der Installer noch läuft, sonst
    {"busy": 0, "dirs": n, "tasks": n, "killed": n, "codes": [...]}. Fehler als RemoteError."""
    if not re.match(r"^\d+_\d+_$", prefix or ""):
        raise ValueError("Ungültiges Präfix.")
    script = CLEANUP_RUN.replace("__PREFIX__", prefix).replace("__MAXMIN__", str(max(5, int(max_minutes or 60))))
    out, err, _ = ps(host, script, timeout=180)
    m = re.search(r"PK_BUSY:(\d+)", out or "")
    if m:
        return {"busy": int(m.group(1))}
    m = re.search(r"PK_CLEAN:(\d+):(\d+):(\d+):([-\d,]*)", out or "")
    if not m:
        raise RemoteError("Aufräumen ohne Ergebnis: " + (err or out or "").strip()[:300])
    return {"busy": 0, "dirs": int(m.group(1)), "tasks": int(m.group(2)), "killed": int(m.group(3)),
            "codes": [c for c in m.group(4).split(",") if c]}


ASR_WMI = "d1e49aac-8f56-4280-b9ba-993a6d77406c"
ASR_RULES = {  # die üblichen Regeln – Name wie in der Gruppenrichtlinie
    ASR_WMI: "Prozesserstellungen durch PSExec- und WMI-Befehle blockieren",
    "5beb7efe-fd9a-4556-801d-275e5ffc04cc": "Ausführung potenziell verborgener Skripts blockieren",
    "01443614-cd74-433a-b99e-2ecdc07bfc25": "Ausführung von ausführbaren Dateien blockieren, wenn sie nicht ein Verbreitungs-, Alters- oder vertrauenswürdige Listenkriterium erfüllen",
    "c1db55ab-c21a-4637-bb3f-a12568109d35": "Erweiterten Schutz vor Ransomware verwenden",
    "d3e037e1-3eb8-44c8-a917-57927947596d": "JavaScript und VBScript am Starten heruntergeladener ausführbarer Inhalte hindern",
    "e6db77e5-3df2-4cf1-b95a-636979351e5b": "Persistenz über WMI-Ereignisabonnement blockieren",
    "9e6c4e1f-7d60-472f-ba1a-a39ef669e4b2": "Diebstahl von Anmeldeinformationen aus dem Subsystem für die lokale Sicherheitsautorität (lsass.exe) blockieren",
    "b2b3f03d-6a65-4f7b-a9c7-1c7ef74a9ba4": "Nicht vertrauenswürdige und nicht signierte Prozesse blockieren, die von USB ausgeführt werden",
    "56a863a9-875e-4185-98a7-b882c64b5ce5": "Missbrauch von ausgenutzten anfälligen signierten Treibern blockieren",
    "33ddedf1-c6e0-47cb-833e-de6133960387": "Neustart des Computers im abgesicherten Modus blockieren",
    "c0033c00-d16d-4114-a5a0-dc9b3a7d2ceb": "Verwendung kopierter oder imitierter Systemtools blockieren",
}


HARD_PATTERNS = (
    (re.compile(r"malicious content|sch\S{1,2}dliche Inhalte|ScriptContainedMaliciousContent", re.I), 9014),
    (re.compile(r"blocked by group policy|durch eine Gruppenrichtlinie blockiert|Softwareeinschr\S{1,2}nkungsrichtlinie|"
                r"software restriction polic", re.I), 9016),
    (re.compile(r"language mode|diesem Sprachmodus", re.I), 9015),
    (re.compile(r"FullyQualifiedErrorId\s*:\s*UnauthorizedAccess\b(?!Exception)|PSSecurityException", re.I), 9013),
)


LOADED_MARK = "lädt ihren Inhalt"   # Zeile von PK-RunPs1, wenn nach einer Verweigerung geladen wurde


def hard_code(text):
    """2.32.5: Zeigt das Protokoll, dass Windows die Ausführung verhindert hat? → 9013–9016, sonst 0. Wird nur bei einem
    fehlgeschlagenen Schritt ausgewertet (Erfolgscodes des Schritts, winget-Meldungen gehen vor) – nicht mehr im
    Rahmen auf dem Client, der die Erfolgscodes nicht kennt."""
    t = text or ""
    for rx, code in HARD_PATTERNS:
        if code == 9013 and LOADED_MARK in t:
            continue  # die Verweigerung wurde durch Laden umgangen – nicht der Grund
        if rx.search(t):
            return code
    return 0


def hard_hint(text, start=False):
    """2.32.2: Klartext, wenn eine Meldung von Windows auf ein gehärtetes System zeigt (sonst "").
    start: Fehler schon beim Einrichten über WinRM – dann betrifft der eingeschränkte Sprachmodus die Remotesitzung."""
    from . import deploy
    code = hard_code(text)
    if not code:
        return ""
    if start and code == 9015:
        return (" Schon die WinRM-Sitzung läuft auf diesem Computer im eingeschränkten Sprachmodus (AppLocker/WDAC) – "
                "POCKET KNIFE kann dort nichts einrichten. Den Computer von dieser Härtung ausnehmen.")
    return " Windows ist auf diesem Computer so eingestellt, dass es POCKET KNIFE blockiert: " + deploy.HARD_MESSAGES[code] + "."


def apply_hard(code, out):
    """Fehlgeschlagener Schritt/Tool: (Code, Klartext) – bei gehärtetem Windows 9013–9016, sonst unverändert."""
    from . import deploy
    h = hard_code(out)
    if not h:
        return code, ""
    return h, f"{deploy.HARD_MESSAGES[h]} (Code {h}, Exitcode {code})"


def gone_message(out, direct):
    """2.31: „ohne Ergebnis beendet“ – mit dem, was der Client über den Grund weiß.
    2.32.1: Defender unterscheidet „blockiert“ (1121) und „nur überwacht“ (1122) – nur ein Block ist ein Grund; die Regel
    wird mit Namen genannt statt des langen Ereignistexts; die Fehlerausgabe von PowerShell steht immer dabei."""
    det = {}
    for line in (out or "").splitlines():
        for k in ("PK_ERR:", "PK_LAST:", "PK_BLOCK:", "PK_ASRBLOCK:", "PK_ASRAUDIT:", "PK_CRASH:", "PK_AV:"):
            if line.startswith(k):
                v = line[len(k):].strip()
                if v not in det.setdefault(k, []):
                    det[k].append(v)
    name = lambda r: f"„{ASR_RULES.get(r.lower(), r)}“"  # noqa: E731
    msg = "Der Prozess wurde ohne Ergebnis beendet"
    blocks = det.get("PK_ASRBLOCK:") or []
    if blocks:
        msg += " – blockiert durch die Defender-ASR-Regel " + ", ".join(name(r) for r in blocks[:3]) + "."
        if direct and any(r.lower() == ASR_WMI for r in blocks):
            msg += (" Als Dienstkonto startet POCKET KNIFE einen eigenen Prozess über WMI, genau das verhindert diese Regel. "
                    "Abhilfe: Schritt auf „Lokales System“ stellen oder die Regel auf „Überwachen“.")
    elif det.get("PK_AV:"):
        msg += " – der Virenschutz hat das Skript als Bedrohung eingestuft: " + det["PK_AV:"][0]
    elif det.get("PK_BLOCK:"):
        msg += " – gesperrt durch " + " / ".join(det["PK_BLOCK:"][:2])
    elif det.get("PK_CRASH:"):
        msg += " – PowerShell ist abgestürzt: " + det["PK_CRASH:"][0]
    if det.get("PK_ERR:"):
        msg += " Fehlermeldung von PowerShell: " + det["PK_ERR:"][0]
    if not (blocks or det.get("PK_BLOCK:") or det.get("PK_CRASH:") or det.get("PK_ERR:") or det.get("PK_AV:")):
        msg += " (z. B. auf dem Client abgebrochen, Neustart oder von einer Schutzsoftware beendet)."
        if direct:
            msg += (" Als Dienstkonto startet POCKET KNIFE einen eigenen Prozess über WMI – blockiert eine Schutzsoftware das, "
                    "den Schritt auf „Lokales System“ stellen.")
        if det.get("PK_ASRAUDIT:"):
            msg += (" Defender-ASR hat den Start nur protokolliert (Regel " + ", ".join(name(r) for r in det["PK_ASRAUDIT:"][:2])
                    + " auf „Überwachen“) – das blockiert nicht und ist nicht der Grund.")
    if det.get("PK_LAST:"):
        msg += " Letzte Ausgabe: " + det["PK_LAST:"][0]
    if not blocks and not det.get("PK_AV:"):
        msg += hard_hint(" ".join(det.get("PK_ERR:", []) + det.get("PK_LAST:", []) + det.get("PK_BLOCK:", [])))
    return msg


def run_as_system(host, body, name, timeout_s=3600, cancel=None, extra_ps="", run_as="system", run_user_sid=None):
    """Startet body (PowerShell) auf dem Client – als Dienstkonto (run_as="deploy": direkt als Prozess, ohne
    geplante Aufgabe), als SYSTEM (Standard) oder als angemeldeter Benutzer (run_as="user", optional nur der
    Benutzer mit run_user_sid) über eine temporäre geplante Aufgabe ohne Kennwort. Gibt (exitcode, ausgabe) zurück."""
    sid = run_user_sid or ""
    if sid and not re.match(r"^S-1-5-21(-\d+)+$", sid):
        sid = ""
    d = f"C:\\Windows\\Temp\\PocketKnife\\{name}"
    task = f"PK_{name}"
    wrapper = WRAPPER.replace("__DIR__", d.replace("'", "''")).replace("__BODY__", body)
    b64 = base64.b64encode(wrapper.encode("utf-8")).decode()

    def fill(t):
        return t.replace("__DIR__", d).replace("__TASK__", task)

    direct = run_as == "deploy"
    reg = fill(START_DIRECT if direct else REGISTER).replace("__B64__", b64).replace("__TIMEOUT__", str(max(5, int(timeout_s / 60) + 5))) \
        .replace("__EXTRA__", extra_ps or "").replace("__RUNAS__", run_as if run_as in ("user", "deploy") else "system") \
        .replace("__RUNSID__", sid)
    try:
        secure_workdir(host)
        out, err, _ = ps(host, reg)
        if "PK_NOUSER:" in out:
            who = util.first_line(out.split("PK_NOUSER:", 1)[1], "")
            raise RemoteError(f"Kein angemeldeter Benutzer{(' ' + who) if who else ''} – die Aktion muss im Benutzerkontext "
                              "laufen und geht nur, solange der Benutzer angemeldet ist.")
        if "PK_STARTED" not in out:
            why = (err or out).strip()[:1500]
            if direct:
                raise RemoteError("Prozess als Dienstkonto konnte nicht gestartet werden: " + why + hard_hint(why, start=True))
            raise RemoteError("Geplante Aufgabe konnte nicht gestartet werden: " + why + hard_hint(why, start=True))
    except BaseException:
        # Auch wenn Registrierung/Start scheitert: Aufgabe und Arbeitsordner entfernen
        try:
            ps(host, fill(CLEANUP), timeout=120)
        except Exception:
            pass
        raise
    start = time.time()
    fails, ready_hits, gone_hits = 0, 0, 0
    try:
        while True:
            time.sleep(4)
            if cancel and cancel():
                raise Cancelled("Abgebrochen")
            if time.time() - start > timeout_s:
                raise RemoteTimeout(f"Zeitüberschreitung nach {dur_text(timeout_s)}.")
            try:
                out, err, _ = ps(host, fill(POLL), timeout=90)  # kurze Abfrage – nicht 30 Minuten auf einen hängenden Client warten
                fails = 0
            except RemoteError:
                fails += 1
                if fails > 15:
                    raise RemoteError("Verbindung zum Computer während der Ausführung verloren.")
                continue
            out = out.strip()
            if out.startswith("PK_DONE:"):
                head, _, log = out.partition("---PKLOG---")
                code_txt = util.first_line(head.replace("PK_DONE:", ""), "1")
                try:
                    code = int(code_txt)
                except ValueError:
                    code = 1
                return code, log.strip()
            if out.startswith("PK_PROC:gone"):
                gone_hits += 1
                if gone_hits >= 2:
                    raise RemoteError(gone_message(out, direct))
                continue
            gone_hits = 0
            if out.startswith("PK_MISSING"):
                raise RemoteError("Geplante Aufgabe wurde auf dem Zielcomputer entfernt.")
            if out.startswith("PK_STATE:Ready") and time.time() - start > 20:
                ready_hits += 1
                if ready_hits >= 4:
                    raise RemoteError("Aufgabe wurde ohne Ergebnis beendet (" + out + ").")
    finally:
        try:
            ps(host, fill(CLEANUP), timeout=120)
        except Exception:
            pass


def test_connection(host):
    online, winrm = is_online(host)
    res = {"host": host, "online": online, "winrm": winrm}
    if not winrm:
        res["error"] = f"WinRM-Port {config.WINRM_PORT} nicht erreichbar."
        return res
    try:
        out, err, _ = ps(host, "$env:COMPUTERNAME; whoami; $PSVersionTable.PSVersion.ToString(); "
                               "([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent())"
                               ".IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)")
        lines = out.strip().splitlines()
        res.update({"computer": lines[0] if lines else "", "user": lines[1] if len(lines) > 1 else "",
                    "powershell": lines[2] if len(lines) > 2 else "",
                    "admin": (lines[3].strip().lower() == "true") if len(lines) > 3 else False})
    except RemoteError as e:
        res["error"] = str(e)
    try:
        t = test_target_smb(host)
        res["smb_admin$"] = (f"ok · {t.get('dialect', '?')} · Signierung {'ja' if t.get('signing') else 'nein'}"
                             f" · Verschlüsselung {'ja' if t.get('encryption') else 'nein'}"
                             f" · {t['write_mb_s']} MB/s schreiben" if t.get("verified") else "Daten fehlerhaft")
    except Exception as e:
        res["smb_admin$"] = f"FEHLER: {e}"
    return res
