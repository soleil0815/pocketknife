"""Update aus der Oberfläche (2.32.9) – ohne Zugriff des Containers auf Docker.

POCKET KNIFE selbst kann (und soll) keinen Container austauschen: dafür bräuchte es den Docker-Socket, und wer den hat,
ist root auf dem Host. Stattdessen legt ein Hauptadmin nur einen AUFTRAG in den Datenordner (<daten>/update/request).
Auf dem Docker-Host läuft alle 5 Minuten  sh update.sh --watch  (systemd-Timer, cron oder Synology-Aufgabenplanung,
richtet jedes Update mit update.sh automatisch ein; von Hand: sh update.sh --watch-install). Findet er einen Auftrag, führt er das gewohnte
sh update.sh --pull <Version> -y  aus – mit Sicherung der Programmdateien, Prüfsumme, Gesundheitscheck, Rückkehr zur
vorherigen Version und Selbsttest – und schreibt Stand und Protokoll zurück in den Datenordner:

  update/helper     Lebenszeichen des Host-Helfers (nur der Zeitstempel zählt)
  update/status     state=running|done|current|failed|rolledback|refused, from, to, target, by, started, finished
  update/update.log Ausgabe des letzten Updates

Der Host-Helfer vertraut dem Auftrag nicht: nur eine Versionsnummer (oder „latest“), nie älter als die laufende
Version, aus dem in der .env eingetragenen Registry. Er folgt keinen symbolischen Links im Ordner.
"""
import os
import re
import time

from . import config, db, news, pklog, util

UPDATE_DIR = config.UPDATE_DIR
REQ = "request"
STATUS = "status"
HELPER = "helper"
LOG = "update.log"
HELPER_MAX_AGE = 20 * 60       # Helfer alle 5 Minuten – verpasste Läufe (Host ausgelastet) verzeiht das
REQ_STALE = 30 * 60            # nicht abgeholt → Hinweis
STATES = ("running", "done", "current", "failed", "rolledback", "refused")
LOGGED_KEY = "selfupdate_logged"  # nicht in DEFAULT_SETTINGS
_KEY_RX = re.compile(r"^[a-z_]{1,20}$")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _p(name):
    return os.path.join(UPDATE_DIR, name)


def ensure_dir():
    try:
        os.makedirs(UPDATE_DIR, exist_ok=True)
    except OSError:
        pass


def _read(name, limit):
    """Kleine Datei lesen – nie einem Link folgen (der Ordner gehört dem Container, der Host schreibt hinein)."""
    p = _p(name)
    try:
        if os.path.islink(p) or not os.path.isfile(p):
            return None
        with open(p, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - limit))
            return f.read(limit).decode("utf-8", "replace")
    except OSError:
        return None


def _kv(text):
    out = {}
    for line in (text or "").splitlines()[:40]:
        k, sep, v = line.partition("=")
        if sep and _KEY_RX.match(k.strip()):
            out[k.strip()] = v.strip()[:300]
    return out


def helper_age():
    """Sekunden seit dem letzten Lebenszeichen des Host-Helfers (None = noch nie)."""
    try:
        st = os.lstat(_p(HELPER))
    except OSError:
        return None
    return max(0, int(time.time() - st.st_mtime))


def read_status():
    s = _kv(_read(STATUS, 4096))
    if s.get("state") not in STATES:
        return None
    for k in ("started", "finished"):
        try:
            s[k] = int(s.get(k) or 0) or None
        except ValueError:
            s[k] = None
    for k in ("from", "to", "target"):
        if s.get(k) and not (news.VER_RX.match(s[k]) or s[k] == "latest"):
            s[k] = ""
    return s


def read_request():
    r = _kv(_read(REQ, 1024))
    if not r.get("version"):
        return None
    try:
        r["at"] = int(r.get("at") or 0) or None
    except ValueError:
        r["at"] = None
    r["stale"] = bool(r["at"] and time.time() - r["at"] > REQ_STALE)
    return r


def log_tail(lines=80):
    t = _read(LOG, 32 * 1024)
    if not t:
        return ""
    t = _ANSI.sub("", t).replace("\r", "")
    return "\n".join(t.splitlines()[-lines:])


def state():
    age = helper_age()
    ns = news.status() or {}
    latest = ns.get("latest") if ns.get("ok") and ns.get("latest") and util.vcmp(ns["latest"], config.VERSION) > 0 else None
    return {"current": config.VERSION, "latest": latest,
            "helper": {"active": age is not None and age <= HELPER_MAX_AGE, "age": age, "max_age": HELPER_MAX_AGE},
            "request": read_request(), "status": read_status(), "log": log_tail(),
            "install": "sudo sh update.sh --pull"}


def request(version, user):
    v = str(version or "").strip()
    if not (v == "latest" or news.VER_RX.match(v)):
        raise ValueError("Ungültige Versionsnummer.")
    if v != "latest" and util.vcmp(v, config.VERSION) <= 0:
        raise ValueError(f"Version {v} ist nicht neuer als die laufende Version {config.VERSION}.")
    age = helper_age()
    if age is None or age > HELPER_MAX_AGE:
        raise ValueError("Der Update-Helfer auf dem Docker-Host läuft nicht – er wird beim nächsten Update auf dem Host "
                         "(sudo sh update.sh --pull) automatisch eingerichtet, oder von Hand: sudo sh update.sh --watch-install.")
    st = read_status()
    if st and st.get("state") == "running" and (time.time() - (st.get("started") or 0)) < 3600:
        raise ValueError("Es läuft bereits ein Update.")
    ensure_dir()
    who = re.sub(r"[^A-Za-z0-9._@\\-]", "", str(user.get("username") or ""))[:64]
    tmp = _p(f".{REQ}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(f"version={v}\nby={who}\nat={int(time.time())}\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, _p(REQ))
    pklog.info("system", f"Update auf POCKET KNIFE {v} angefordert – der Update-Helfer auf dem Docker-Host führt es aus.",
               user=user.get("username"))
    return state()


def cancel(user):
    p = _p(REQ)
    if os.path.lexists(p):
        os.remove(p)
        pklog.info("system", "Update-Auftrag zurückgenommen.", user=user.get("username"))
    return state()


def poll():
    """Scheduler (jede Minute): Ergebnis eines abgeschlossenen Updates einmal ins Systemprotokoll schreiben."""
    st = read_status()
    if not st or st["state"] == "running" or not st.get("finished"):
        return
    mark = f"{st['finished']}:{st['state']}"
    if db.setting_raw(LOGGED_KEY) == mark:
        return
    db.set_setting(LOGGED_KEY, mark)
    frm, to = st.get("from") or "?", st.get("to") or "?"
    msg = {"done": (pklog.info, f"Update erledigt: {frm} → {to}."),
           "current": (pklog.info, f"Update: {to} ist bereits aktuell."),
           "rolledback": (pklog.error, f"Update auf {st.get('target') or '?'} fehlgeschlagen – zurück auf {to}. Protokoll: Einstellungen → Wartung."),
           "failed": (pklog.error, f"Update auf {st.get('target') or '?'} fehlgeschlagen ({st.get('message') or 'siehe Protokoll'}). Läuft: {to}."),
           "refused": (pklog.warn, "Update-Auftrag abgelehnt: " + {"invalid": "ungültiger Auftrag.", "older": "Version nicht neuer als die laufende."}.get(st.get("message"), st.get("message") or ""))}[st["state"]]
    msg[0]("system", msg[1], user=st.get("by") or None)
