"""Diagnosepaket (Einstellungen → Wartung): eine ZIP-Datei zur Fehlersuche – wird nur heruntergeladen,
nie von selbst verschickt.

Inhalt: Version und Umgebung, Einstellungen (ohne Kennwörter, Keytab, Schlüssel), Protokoll des Zeitraums,
Fehler mit technischen Details, Statistik zu Scans und Bereitstellungen, fehlgeschlagene Ziele mit Schrittergebnissen.

Anonymisieren (empfohlen): Rechner-, Benutzer- und Servernamen, Domäne, IP-/MAC-/Mail-Adressen und AD-Pfade werden
durch Platzhalter ersetzt. Die Platzhalter sind dauerhaft gleich (Tabelle diag_alias) – PC-017 ist in jedem Paket derselbe
Rechner. Merkmale, an denen Fehler hängen können, bleiben sichtbar: Länge über 15 Zeichen („-LANG“), Umlaute („-UML“),
Sonderzeichen („-SZ“), kurzer Name vs. vollständiger DNS-Name. Die Zuordnung bleibt auf dem Server; sie lässt sich in der
Oberfläche nachschlagen oder als CSV herunterladen.
"""
import bisect
import collections
import csv
import io
import json
import os
import platform
import re
import sys
import threading
import zipfile

from . import config, db, util

MAX_LOG_ROWS = 100000
MAX_TARGETS = 3000
MAX_OUTPUT = 3000
DAYS_MAX = 30

SCHEMA = """CREATE TABLE IF NOT EXISTS diag_alias(kind TEXT NOT NULL, real TEXT NOT NULL, alias TEXT NOT NULL, created TEXT,
  PRIMARY KEY(kind, real));
CREATE TABLE IF NOT EXISTS version_history(started_at TEXT PRIMARY KEY, version TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS diag_alias_unique ON diag_alias(kind, alias);"""
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
START_RX = re.compile(r"^POCKET KNIFE (\S+) gestartet\.$")

# Einstellungen, deren Werte Namen/Adressen enthalten (werden beim Anonymisieren mit ersetzt bzw. gekürzt)
FREE_TEXT_SETTINGS = {"toast_sender", "ldap_cert_pending", "ad_last_status", "proxy_url", "proxy_user", "repo_path", "smtp_host", "smtp_user", "smtp_from", "mail_default_to", "kiosk_notify_to", "kiosk_groups",
                      "kiosk_ous", "dns_servers", "ad_filter", "ad_containers", "public_url", "base_url"}
COMMON_WORDS = {"admin", "administrator", "administrators", "administratoren", "test", "user", "users", "system", "guest",
                "gast", "service", "kiosk", "default", "local", "domain", "server", "online", "offline", "windows", "share"}
NEVER_SETTINGS = re.compile(r"(password|passwort|secret|token|key|keytab|fingerprint)", re.I)


def _ensure():
    for stmt in SCHEMA.split(";"):
        if stmt.strip():
            try:
                db.ex(stmt)
            except Exception:  # noqa: BLE001 – z. B. Index bei schon doppelt vergebenen Platzhaltern (vor 2.23)
                pass


# --- Versionsgeschichte --------------------------------------------------------------------------
def record_start():
    """Beim Start: ab wann welche Version läuft. Beim ersten Mal aus den Startmeldungen im Protokoll nachgetragen."""
    _ensure()
    if not db.val("SELECT COUNT(*) FROM version_history", default=0):
        last = None
        for r in db.q("SELECT ts, message FROM logs WHERE category='system' AND message LIKE 'POCKET KNIFE % gestartet.' ORDER BY id"):
            m = START_RX.match(r["message"] or "")
            if m and m.group(1) != last:
                db.ex("INSERT OR IGNORE INTO version_history(started_at, version) VALUES(?,?)", (r["ts"], m.group(1)))
                last = m.group(1)
    cur = db.q1("SELECT version FROM version_history ORDER BY started_at DESC LIMIT 1")
    if not cur or cur["version"] != config.VERSION:
        db.ex("INSERT OR REPLACE INTO version_history(started_at, version) VALUES(?,?)", (util.now(), config.VERSION))


def history():
    _ensure()
    return db.q("SELECT started_at, version FROM version_history ORDER BY started_at")


class _VersionAt:
    """Version, die zu einem Zeitpunkt lief (Einträge vor der ersten bekannten Version: „vor X“)."""

    def __init__(self, hist):
        self.ts = [h["started_at"] for h in hist]
        self.v = [h["version"] for h in hist]

    def __call__(self, ts):
        if not self.ts:
            return config.VERSION
        if not ts:
            return "?"
        i = bisect.bisect_right(self.ts, str(ts)) - 1
        return self.v[i] if i >= 0 else f"vor {self.v[0]}"


def _changelog(oldest):
    """CHANGELOG-Abschnitte von der ältesten Version im Zeitraum bis heute (ältere zählen nicht)."""
    try:
        text = open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8").read()
    except OSError:
        return "CHANGELOG.md nicht gefunden.\n"
    parts = re.split(r"(?m)^(?=## pocketknife-)", text)
    out = [parts[0]]
    for sec in parts[1:]:
        out.append(sec)
        m = re.match(r"## pocketknife-(\S+)", sec)
        if oldest and m and m.group(1) == oldest:
            break
        if len(out) > 40:
            break
    return "".join(out)


def _fixed_list():
    """Selbsttest-Prüfungen = Liste der behobenen Fehler, die nicht zurückkommen dürfen (mit Version)."""
    try:
        src = open(os.path.join(ROOT, "tools", "selftest.py"), encoding="utf-8").read()
    except OSError:
        return "tools/selftest.py nicht gefunden.\n"
    names = re.findall(r'@test\("((?:[^"\\]|\\.)*)"\)', src)
    return ("Behobene Fehler, die der Selbsttest bei jeder Version erneut prüft (Version = seit wann behoben):\n\n"
            + "\n".join(f"- {n}" for n in names) + "\n")


# --- Platzhalter ---------------------------------------------------------------------------------
PREFIX = {"dc": "dom", "computer": "PC", "user": "USER", "server": "SRV", "ip": "IP", "mac": "MAC", "mail": "MAIL", "ou": "OU", "group": "GRUPPE",
          "display": "Person"}
ANON_DOMAIN, ANON_NETBIOS = "firma.local", "FIRMA"


def _marks(real):
    """Merkmale, an denen Fehler hängen können – bleiben im Platzhalter sichtbar."""
    short = real.split(".")[0]
    m = ""
    if len(short) > 15:
        m += "-LANG"
    if re.search(r"[äöüÄÖÜß]|[^\x00-\x7f]", real):
        m += "-UML"
    if re.search(r"[^A-Za-z0-9.\-_ äöüÄÖÜß@\\]", real):
        m += "-SZ"
    return m


class Anonymizer:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.map = {}       # (kind, real_lower) → alias
        self.text = {}      # real text (lower) → alias, für die Ersetzung in Freitext
        self._pairs = None
        self._spaced = None
        if not enabled:
            return
        _ensure()
        self.used = collections.defaultdict(set)   # vergebene Platzhalter je Art
        self.pending = []                          # neue Zuordnungen – gesammelt gespeichert (flush)
        for r in db.q("SELECT kind, real, alias FROM diag_alias"):
            self.map[(r["kind"], r["real"].lower())] = r["alias"]
            self.used[r["kind"]].add(r["alias"])
        self.count = {k: len(v) for k, v in self.used.items()}

    def alias(self, kind, real):
        real = (real or "").strip()
        if not self.enabled or not real:
            return real
        key = (kind, real.lower())
        a = self.map.get(key)
        if a:
            return a
        # Nummer im Speicher vergeben (build() läuft nie parallel) – je Platzhalter genau ein Name
        n = self.count.get(kind, 0)
        while True:
            n += 1
            if kind == "ip":
                a = f"10.99.{n // 250}.{n % 250 + 1}"
            elif kind == "mac":
                a = "02:00:00:00:%02X:%02X" % (n // 256 % 256, n % 256)
            elif kind == "mail":
                a = f"user{n:02d}@{ANON_DOMAIN}"
            else:
                a = f"{PREFIX.get(kind, kind.upper())}-{n:03d}{_marks(real) if kind in ('computer', 'user', 'server') else ''}"
            if a not in self.used[kind]:
                break
        self.count[kind] = n
        self.used[kind].add(a)
        self.pending.append((kind, real, a, util.now()))
        self.map[key] = a
        self._add_pair(kind, key[1], a)
        return a

    # Bekannte Namen einsammeln (vor dem Ersetzen in Freitext)
    def learn(self):
        if not self.enabled:
            return
        dom = (config.DOMAIN or "").lower()
        for c in db.q("SELECT name, dns_name, current_user, ip_addresses, mac_addresses, resolved_ip, verified_ip, addr_ip, "
                      "description FROM computers"):
            a = self.alias("computer", c["name"])
            if c["dns_name"] and c["dns_name"].lower() != (c["name"] or "").lower():
                self.text[c["dns_name"].lower()] = f"{a.lower()}.{ANON_DOMAIN}"  # vollständiger DNS-Name bleibt als solcher erkennbar
            for u in re.split(r"[,;]\s*", c["current_user"] or ""):
                if u.strip():
                    self.alias("user", u.split("\\")[-1].split("@")[0])
        for r in db.q("SELECT DISTINCT username FROM logs WHERE username IS NOT NULL"):
            if _user_part(r["username"]):
                self.alias("user", _user_part(r["username"]))
        for r in db.q("SELECT DISTINCT created_by FROM deployments WHERE created_by IS NOT NULL"):
            u = _user_part(r["created_by"])
            if u and not re.match(r"^(Zeitplan|Regel|Kiosk|system)$", u, re.I):
                self.alias("user", u)
        try:
            for r in db.q("SELECT DISTINCT username, display_name FROM kiosk_requests"):
                if r["username"]:
                    self.alias("user", r["username"])
                if r["display_name"] and r["display_name"] != r["username"]:
                    self.alias("display", r["display_name"])
        except Exception:
            pass
        # AD-Gruppen aus Kiosk-Freigaben (Name oder DN je Zeile) – Gruppennamen verraten oft die Organisation
        group_lines = str(db.setting("kiosk_groups") or "").splitlines()
        try:
            for r in db.q("SELECT groups FROM kiosk_items WHERE COALESCE(groups,'')<>''"):
                group_lines += str(r["groups"]).splitlines()
        except Exception:
            pass
        for g in group_lines:
            g = g.strip()
            if g and not re.match(r"^(CN|OU|DC)=", g, re.I):
                self.alias("group", g.split("\\")[-1])
        for s in list(config.LDAP_SERVERS or []) + [config.PUBLIC_HOSTNAME] + _setting_hosts():
            if s and s.lower() not in ("localhost", "127.0.0.1") and s.lower() != dom:
                self.alias("server", s)
        for u in (config.service_user() if hasattr(config, "service_user") else "", config.BREAKGLASS_USER, config.ACCESS_GROUP,
                  db.setting("proxy_user"), config.repo_user() if hasattr(config, "repo_user") else ""):
            if u:
                self.alias("user" if u != config.ACCESS_GROUP else "group", str(u).split("\\")[-1].split("@")[0])

    def flush(self):
        if self.enabled and self.pending:
            db.exmany("INSERT OR IGNORE INTO diag_alias(kind, real, alias, created) VALUES(?,?,?,?)", self.pending)
            self.pending = []

    # Ersetzen im Freitext: Wörter herauslösen und im Wörterbuch nachschlagen (statt einer riesigen Regex, die bei
    # tausenden Namen bei jedem neuen Platzhalter neu übersetzt werden müsste). Nur Einträge mit Leerzeichen (Personen,
    # Gruppen) laufen über eine kleine Regex.
    def _add_pair(self, kind, real, a):
        if self._pairs is None or kind in ("ip", "mac", "mail", "ou") or len(real) < 3:
            return
        if real in COMMON_WORDS or real in _vocabulary():  # „admin“, „Anmeldung“ … nie als Name ersetzen
            return
        self._pairs[real] = a
        if not WORD_RX.fullmatch(real):
            self._spaced = None

    def _build(self):
        if self._pairs is not None:
            return
        self._pairs, self._spaced = {}, None
        for (kind, real), a in self.map.items():
            self._add_pair(kind, real, a)
        for real, a in self.text.items():
            self._pairs[real] = a
        if (config.DOMAIN or "").strip():
            self._pairs[config.DOMAIN.lower()] = ANON_DOMAIN
        if config.NETBIOS and len(config.NETBIOS) >= 2:
            self._pairs[config.NETBIOS.lower()] = ANON_NETBIOS

    def _replace_words(self, t):
        self._build()
        pairs = self._pairs
        if self._spaced is None:
            # Namen, die nicht als ein „Wort“ erkannt werden (Leerzeichen, ' & + Klammern …), über eine kleine Regex
            sp = sorted((k for k in pairs if not WORD_RX.fullmatch(k)), key=len, reverse=True)
            self._spaced = re.compile(r"(?<![\wÄÖÜäöüß-])(" + "|".join(re.escape(k) for k in sp) + r")(?![\wÄÖÜäöüß-])",
                                      re.I) if sp else False
        if self._spaced:
            t = self._spaced.sub(lambda m: pairs.get(m.group(1).lower(), m.group(1)), t)

        def word(m):
            w = m.group(0)
            segs = re.split(r"([._])", w)  # pc7.firma.local, BUCHH-PC7_install.log: längsten bekannten Teil ersetzen
            out, i = [], 0
            while i < len(segs):
                if segs[i] in (".", "_"):
                    out.append(segs[i])
                    i += 1
                    continue
                hit = None
                for j in range(len(segs), i, -1):
                    if (j - i) % 2 == 0:
                        continue  # muss auf einem Wort enden, nicht auf einem Trenner
                    cand = "".join(segs[i:j]).lower()
                    if cand in pairs:
                        hit = (j, pairs[cand])
                        break
                if hit:
                    out.append(hit[1])
                    i = hit[0]
                else:
                    out.append(segs[i])
                    i += 1
            return "".join(out)
        return WORD_RX.sub(word, t)

    def __call__(self, text):
        """Freitext anonymisieren. Kennwörter in Befehlszeilen werden immer maskiert – auch ohne Anonymisierung."""
        if text is None:
            return text
        t = _mask_secrets(str(text))
        if not self.enabled:
            return t
        dn_parts = list(config.BASE_DN.split(",")) if config.BASE_DN else []
        # AD-Pfade: DC=aap,DC=dom → DC=firma,DC=local; OU=…/CN=… → Platzhalter
        if dn_parts:
            t = re.sub(re.escape(config.BASE_DN), ",".join("DC=" + p for p in ANON_DOMAIN.split(".")), t, flags=re.I)
        t = re.sub(r"\b(DC)=([^,=\s\\]+)", lambda m: m.group(0) if m.group(2).lower() in ANON_DOMAIN.split(".")
                   else f"{m.group(1)}={self.alias('dc', m.group(2)).lower()}", t, flags=re.I)  # Teil-Domänen (DC=sub,…)
        t = re.sub(r"\b(OU)=((?:\\.|[^,\\])+)", lambda m: f"{m.group(1)}={self.alias('ou', m.group(2))}", t)
        t = re.sub(r"\b(CN)=((?:\\.|[^,\\])+)", lambda m: f"{m.group(1)}={self.alias('group', m.group(2))}", t)
        t = re.sub(r"[A-Za-z0-9._%+\-ÄÖÜäöüß]+@[A-Za-z0-9.\-ÄÖÜäöüß]+\.[A-Za-z]{2,}", lambda m: self.alias("mail", m.group(0)), t)
        t = re.sub(r"(?<![\d.])((?:\d{1,3}\.){3}\d{1,3})(?!\d|\.\d)", lambda m: m.group(1) if _keep_ip(m.group(1)) else self.alias("ip", m.group(1)), t)
        t = re.sub(r"(?<![0-9A-Fa-f:.\-])([0-9A-Fa-f]{2}([:-])[0-9A-Fa-f]{2}(?:\2[0-9A-Fa-f]{2}){4})(?![0-9A-Fa-f:\-]|\.[0-9A-Fa-f])", _mac_sub(self), t)
        t = re.sub(r"(?<![0-9A-Za-z:.])((?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?:%\w+)?)(?![0-9A-Za-z:]|\.\d)",
                   lambda m: m.group(1) if _keep_ip6(m.group(1)) else self.alias("ip", m.group(1).lower()), t)
        # DOMÄNE\benutzer – auch unbekannte Benutzer (in JSON auch mit doppeltem Backslash)
        if config.NETBIOS:
            t = re.sub(re.escape(config.NETBIOS) + r"(\\+)([A-Za-z0-9._\-$ÄÖÜäöüß]+)",
                       lambda m: f"{ANON_NETBIOS}{m.group(1)}{self.alias('user', m.group(2))}", t, flags=re.I)
        # Fehlgeschlagene Anmeldung: der eingetippte Name kann alles sein (auch ein versehentlich eingetipptes Kennwort)
        t = re.sub(r"(Anmeldung fehlgeschlagen für ')([^']{1,200})(')", lambda m: m.group(1) + self.alias("user", _user_part(m.group(2)) or "?") + m.group(3), t)
        return self._replace_words(t)


_build_lock = threading.Lock()
# „Wort“ für die Namenssuche im Freitext: Buchstaben aller Sprachen (é, ø, ñ …), Ziffern, Punkt, Unterstrich, Bindestrich
WORD_RX = re.compile(r"[^\W_][\w.\-]*")

# Kennwörter in Befehlszeilen (Schritttitel, Ausgaben): PASSWORD=…, /p:…, net use … /user:x KENNWORT, -AsPlainText
_SECRET_RXS = [
    (re.compile(r"(?i)\b([\w-]*(?:password|passwort|passwd|kennwort|pwd|secret|token|apikey|api_key)[\w-]*\s*[=:]\s*)"
                r"(\"[^\"]*\"|'[^']*'|[^\s;,]+)"), lambda m: m.group(1) + "***"),
    (re.compile(r"(?i)(-(?:password|passwort|kennwort|pwd|secret|token)\s+)(\"[^\"]*\"|'[^']*'|\S+)"), lambda m: m.group(1) + "***"),
    (re.compile(r"(?i)(/user:\S+\s+)(?!/)(\"[^\"]*\"|\S+)"), lambda m: m.group(1) + "***"),
    (re.compile(r"(?i)(ConvertTo-SecureString\s+(?:-String\s+)?)(\"[^\"]*\"|'[^']*')"), lambda m: m.group(1) + "'***'"),
]


def _mask_secrets(t):
    for rx, fn in _SECRET_RXS:
        t = rx.sub(fn, t)
    return t


def _keep_ip6(a):
    """Keine IPv6-Adresse: Uhrzeiten (12:30:45), MAC-ähnliches, Schleife ::1."""
    if re.fullmatch(r"\d{1,2}(:\d{2}){1,3}", a) or a in ("::", "::1") or re.fullmatch(r"([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", a):
        return True
    core = a.split("%")[0]
    if not re.search(r"[0-9A-Fa-f]", core):
        return True  # nur Doppelpunkte, z. B. „[System.IO.File]::“
    return core.count(":") < 3 and "::" not in core


def _user_part(u):
    """„Kiosk: FIRMA\\max“ → „max“ (Protokoll und Bereitstellungen nennen den Auslöser mit Vorsatz)."""
    u = re.sub(r"^(Kiosk|Zeitplan|Regel|Tool):\s*", "", str(u or "").strip(), flags=re.I)
    return u.split("\\")[-1].split("@")[0].strip()


def _mac_sub(an):
    def sub(m):
        mac = m.group(1)
        if mac.upper().startswith("02:00:00:00:"):
            return mac  # schon ein Platzhalter
        if m.group(2) == "-" and re.fullmatch(r"[\d-]+", mac):
            return mac  # nur Ziffern mit Bindestrich: Datum/Uhrzeit (z. B. in Dateinamen von Protokollen), keine MAC-Adresse
        return an.alias("mac", mac.upper().replace("-", ":"))
    return sub


_vocab = None


def _vocabulary():
    """Wörter aus den Texten von POCKET KNIFE (ab 5 Buchstaben) – nie als Name im Freitext ersetzen. Sonst wird aus einem
    Benutzer oder Rechner, der zufällig „Anmeldung“ heißt, „USER-004 fehlgeschlagen“ in jeder Meldung."""
    global _vocab
    if _vocab is None:
        words = set()
        try:
            en = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "i18n", "en.json"), encoding="utf-8"))
            for k, v in en.items():
                if not v:
                    continue
                low_v = v.lower()
                # nur Wörter, die übersetzt wurden – Namen in Beispielen („FIRMA\\mueller“) stehen auch in der Übersetzung
                words.update(w.lower() for w in re.findall(r"[A-Za-zÄÖÜäöüß]{5,}", k) if w.lower() not in low_v)
        except (OSError, ValueError):
            pass
        _vocab = words
    return _vocab


def _keep_ip(ip):
    return ip.startswith("127.") or ip in ("0.0.0.0", "255.255.255.255") or not all(0 <= int(x) <= 255 for x in ip.split("."))


def _setting_hosts():
    out = []
    for k in ("smtp_host", "dns_servers", "repo_path"):
        v = str(db.setting(k) or "")
        if k == "repo_path":
            m = re.match(r"^[\\/]{2}([^\\/]+)", v)
            v = m.group(1) if m else ""
        out += [x.strip() for x in v.replace(";", ",").split(",") if x.strip() and not re.match(r"^\d+\.\d+\.\d+\.\d+$", x.strip())]
    m = re.match(r"^https?://([^/:@]+)", str(db.setting("proxy_url") or ""))
    if m and not re.match(r"^\d+\.\d+\.\d+\.\d+$", m.group(1)):
        out.append(m.group(1))
    return out


# --- Inhalte --------------------------------------------------------------------------------------
def _csv(rows, cols):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(cols)
    for r in rows:
        w.writerow(["" if r.get(c) is None else r.get(c) for c in cols])
    return "\ufeff" + buf.getvalue()


def _norm_msg(m):
    """Meldung ohne Zahlen/Namen in Anführungszeichen – zum Zählen gleichartiger Fehler."""
    m = re.sub(r"„[^“]*“|\"[^\"]*\"|'[^']*'", "„…“", str(m or ""))
    m = re.sub(r"\d+", "#", m)
    return m[:160]


def _settings(an):
    out = {}
    for k in db.DEFAULT_SETTINGS:
        if NEVER_SETTINGS.search(k):
            continue
        v = db.setting(k)
        if k == "proxy_user" and an.enabled and v:
            v = an.alias("user", _user_part(v))  # ganzer Wert ist ein Konto – auch kurze Namen ersetzen
        elif k in FREE_TEXT_SETTINGS and an.enabled:
            v = an(v)
        out[k] = v
    return out


def _environment(an):
    from . import scheduler
    env = {
        "version": config.VERSION, "python": sys.version.split()[0], "platform": platform.platform(),
        "uid": os.getuid() if hasattr(os, "getuid") else None, "created": util.now(),
        "domain": an(config.DOMAIN), "netbios": an(config.NETBIOS), "ldap_servers": [an(s) for s in config.LDAP_SERVERS],
        "base_dn": an(config.BASE_DN), "winrm": {"https": config.WINRM_HTTPS, "port": config.WINRM_PORT, "auth_env": config.WINRM_AUTH},
        "https": config.HTTPS, "session_hours": config.SESSION_HOURS,
        "scheduler": {k: v for k, v in (getattr(scheduler, "state", {}) or {}).items()},
    }
    try:
        from . import kerberos
        env["kerberos"] = {"mode": kerberos.mode(), "available": kerberos.available()[0]}
    except Exception:
        pass
    try:
        from . import guard
        g = guard.status()
        env["guard"] = {"paused": g.get("paused"), "reason": an(g.get("reason")), "since": g.get("since")}
    except Exception:
        pass
    try:
        env["db"] = db.stats()
    except Exception:
        pass
    counts = {}
    for t in ("computers", "packages", "deployments", "deployment_targets", "schedules", "collections", "scan_profiles",
              "custom_scanners", "kiosk_items", "kiosk_requests", "logs", "software", "client_cleanup"):
        try:
            counts[t] = db.val(f"SELECT COUNT(*) FROM {t}", default=0)
        except Exception:
            counts[t] = None
    env["counts"] = counts
    env["computers"] = {
        "online": db.val("SELECT COUNT(*) FROM computers WHERE online=1", default=0),
        "winrm": db.val("SELECT COUNT(*) FROM computers WHERE winrm=1", default=0),
        "address_problems": {r["addr_problem"]: r["n"] for r in db.q(
            "SELECT addr_problem, COUNT(*) n FROM computers WHERE COALESCE(addr_problem,'')<>'' GROUP BY addr_problem")},
        "scan_status": {str(r["scan_status"]): r["n"] for r in db.q("SELECT scan_status, COUNT(*) n FROM computers GROUP BY scan_status")},
        "os": {str(r["os"]): r["n"] for r in db.q("SELECT COALESCE(os_name, ad_os, '?') os, COUNT(*) n FROM computers GROUP BY 1 ORDER BY 2 DESC LIMIT 20")},
    }
    return env


def _stats(since, an):
    t = db.q("""SELECT t.status, t.exit_code, t.message, t.started_at, t.finished_at, d.name, d.source
                FROM deployment_targets t JOIN deployments d ON d.id = t.deployment_id WHERE COALESCE(t.finished_at, t.started_at, d.created_at) >= ?""",
             (since,))
    by_pkg = collections.defaultdict(lambda: {"targets": 0, "success": 0, "failed": 0, "seconds": []})
    for r in t:
        p = by_pkg[an(r["name"]) or "?"]
        p["targets"] += 1
        if r["status"] in ("success", "failed"):
            p[r["status"]] += 1
        a, b = util.parse(r["started_at"]), util.parse(r["finished_at"])
        if a and b:
            p["seconds"].append(int((b - a).total_seconds()))
    pkgs = {}
    for k, v in by_pkg.items():
        s = sorted(v["seconds"])
        pkgs[k] = {"targets": v["targets"], "success": v["success"], "failed": v["failed"],
                   "seconds_median": s[len(s) // 2] if s else None, "seconds_max": s[-1] if s else None}
    sh = db.q("SELECT status, duration_ms, error, profile_name FROM scan_history WHERE ts >= ?", (since,))
    dur = sorted(r["duration_ms"] or 0 for r in sh if r["duration_ms"])
    return {
        "since": since,
        "deployment_targets": dict(collections.Counter(r["status"] for r in t)),
        "deployment_sources": dict(collections.Counter(r["source"] for r in t)),
        "exit_codes": dict(collections.Counter(str(r["exit_code"]) for r in t if r["status"] == "failed").most_common(20)),
        "top_failures": collections.Counter(_norm_msg(an(r["message"])) for r in t if r["status"] == "failed").most_common(25),
        "packages": pkgs,
        "scans": dict(collections.Counter(r["status"] for r in sh)),
        "scan_ms": {"median": dur[len(dur) // 2] if dur else None, "p95": dur[int(len(dur) * 0.95)] if dur else None, "max": dur[-1] if dur else None},
        "scan_profiles": dict(collections.Counter(an(r["profile_name"]) for r in sh)),
        "top_scan_errors": collections.Counter(_norm_msg(an(r["error"])) for r in sh if r["status"] != "OK" and r["error"]).most_common(25),
        "log_levels": {r["level"]: r["n"] for r in db.q("SELECT level, COUNT(*) n FROM logs WHERE ts >= ? GROUP BY level", (since,))},
        "log_top_warnings": collections.Counter(_norm_msg(an(r["message"])) for r in db.q(
            "SELECT message FROM logs WHERE ts >= ? AND level IN ('WARN','ERROR')", (since,))).most_common(40),
        "kiosk_requests": {r["status"]: r["n"] for r in db.q("SELECT status, COUNT(*) n FROM kiosk_requests WHERE created_at >= ? GROUP BY status", (since,))},
    }


def _per_version(logs, targets):
    """Je Version: Warnungen/Fehler im Protokoll und Ergebnisse der Bereitstellungen."""
    out = collections.OrderedDict()
    for r in logs:
        v = out.setdefault(r["version"], {"log_warn": 0, "log_error": 0, "targets_success": 0, "targets_failed": 0})
        if r["level"] == "WARN":
            v["log_warn"] += 1
        elif r["level"] == "ERROR":
            v["log_error"] += 1
    for r in targets:
        v = out.setdefault(r["version"], {"log_warn": 0, "log_error": 0, "targets_success": 0, "targets_failed": 0})
        if r["status"] == "success":
            v["targets_success"] += 1
        elif r["status"] == "failed":
            v["targets_failed"] += 1
    return out


def _readme(days, anon, since):
    return f"""POCKET KNIFE – Diagnosepaket
Version {config.VERSION}, erstellt {util.now()}, Zeitraum: {f"letzte {days} Tage" if isinstance(days, int) else days} (ab {since})
Anonymisiert: {"ja" if anon else "NEIN – enthält echte Namen und Adressen"}

Versionen
  Jede Zeile in protokoll.csv, bereitstellungen.csv, scans.csv, fehler.txt und fehlgeschlagen.json trägt die Version,
  unter der sie entstanden ist. statistik.json enthält „zeitraum“ (alles) und „aktuelle_version“ (nur seit dem Start
  der laufenden Version) – ein Fehler, der nur unter einer älteren Version auftrat, ist vermutlich behoben:
  in aenderungen.md und behobene_fehler.txt nachsehen.

Dateien
  versionen.json       Versionsgeschichte (ab wann welche Version lief), Versionen im Zeitraum
  aenderungen.md       Änderungen von der ältesten Version im Zeitraum bis heute
  behobene_fehler.txt  Behobene Fehler, die der Selbsttest bei jeder Version prüft
  umgebung.json        Version, Umgebung, Zähler, Zustand der Hintergrundaufgaben (ohne Kennwörter/Schlüssel)
  einstellungen.json   Einstellungen (ohne Kennwörter, Keytab, Schlüssel)
  statistik.json       Bereitstellungen und Scans im Zeitraum: Ergebnisse, Exitcodes, häufigste Fehler, Dauer
  protokoll.csv        Protokoll des Zeitraums (höchstens {MAX_LOG_ROWS} Einträge, neueste zuerst)
  fehler.txt           Warnungen und Fehler mit technischen Details
  bereitstellungen.csv Ziele der Bereitstellungen im Zeitraum
  fehlgeschlagen.json  Fehlgeschlagene Ziele mit Schrittergebnissen und gekürzter Ausgabe
  scans.csv            Scan-Verlauf im Zeitraum

{"Anonymisierung: Rechner-, Benutzer-, Server- und Gruppennamen, Domäne, AD-Pfade, IP-, MAC- und Mail-Adressen sind durch "
 "Platzhalter ersetzt (PC-001, USER-01, SRV-001, 10.99.x.y …). Sie bleiben in jedem Paket gleich. -LANG = Name länger als "
 "15 Zeichen, -UML = Umlaut/Sonderbuchstabe, -SZ = Sonderzeichen. Paket-, Software- und Sammlungsnamen bleiben lesbar. "
 "Die Zuordnung liegt nur auf Ihrem Server (Einstellungen → Wartung → Diagnosepaket)." if anon else ""}
Nichts davon wird automatisch verschickt.
"""


def build(days=7, anonymize=True, user=None):
    """ZIP als Bytes. days: 1–30, 0 = seit dem letzten Update. Immer nur ein Paket gleichzeitig."""
    with _build_lock:
        return _build(days, anonymize, user)


def _build(days, anonymize, user):
    hist = history()
    vat = _VersionAt(hist)
    days = int(7 if days is None else days)
    if days <= 0:  # 0 = seit dem Start der laufenden Version (höchstens DAYS_MAX Tage)
        since = max(hist[-1]["started_at"] if hist else util.ago(days=DAYS_MAX), util.ago(days=DAYS_MAX))
        label = "seit-update"
    else:
        days = min(DAYS_MAX, days)
        since = util.ago(days=days)
        label = f"{days}T"
    cur_start = hist[-1]["started_at"] if hist else since
    an = Anonymizer(anonymize)
    an.learn()
    logs = db.q("SELECT l.ts, l.level, l.category, l.message, l.username, c.name AS computer FROM logs l "
                "LEFT JOIN computers c ON c.id = l.computer_id WHERE l.ts >= ? ORDER BY l.id DESC LIMIT ?", (since, MAX_LOG_ROWS))
    for r in logs:
        r["message"] = an(r["message"])
        if anonymize and r["username"]:
            pre = re.match(r"^(Kiosk|Zeitplan|Regel|Tool):\s*", r["username"], re.I)
            r["username"] = (pre.group(0) if pre else "") + an.alias("user", _user_part(r["username"]))
        r["computer"] = an.alias("computer", r["computer"]) if anonymize and r["computer"] else r["computer"]
        r["version"] = vat(r["ts"])
    errors = [f"{r['ts']} [{r['version']}] {r['level']} [{r['category']}] {r['computer'] or ''} {r['message']}"
              for r in logs if r["level"] in ("WARN", "ERROR")]
    targets = db.q("""SELECT t.id, t.deployment_id, d.name AS deployment, d.source, d.created_by, t.computer_name, t.status, t.exit_code,
                             t.message, t.started_at, t.finished_at, t.steps, t.output, d.created_at
                      FROM deployment_targets t JOIN deployments d ON d.id = t.deployment_id
                      WHERE COALESCE(t.finished_at, t.started_at, d.created_at) >= ? ORDER BY t.id DESC LIMIT ?""", (since, MAX_TARGETS))
    failed = []
    for r in targets:
        r["version"] = vat(r["started_at"] or r["created_at"])
        if anonymize:
            r["computer_name"] = an.alias("computer", r["computer_name"]) if r["computer_name"] else r["computer_name"]
        # auch ohne Anonymisierung durch an(): Kennwörter in Befehlszeilen werden immer maskiert
        r["created_by"], r["message"], r["deployment"] = an(r["created_by"]), an(r["message"]), an(r["deployment"])
        if r["status"] == "failed":
            try:
                steps = json.loads(r["steps"] or "[]")
            except ValueError:
                steps = []
            steps = [x for x in steps if isinstance(x, dict)] if isinstance(steps, list) else []
            for s in steps:
                s.pop("output", None)
                for k, v in list(s.items()):
                    if isinstance(v, str) and k not in ("phase", "type", "status", "run_as", "started", "finished", "sid"):
                        s[k] = an(v)  # Titel (erste Befehlszeile, Pfade), Meldung …
            failed.append({k: r[k] for k in ("version", "deployment_id", "deployment", "source", "computer_name", "exit_code", "message",
                                              "started_at", "finished_at")} | {"steps": steps, "output_tail": an((r["output"] or "")[-MAX_OUTPUT:])})
    scans = db.q("""SELECT h.ts, c.name AS computer, h.status, h.duration_ms, h.profile_name, h.error, h.warnings FROM scan_history h
                    LEFT JOIN computers c ON c.id = h.computer_id WHERE h.ts >= ? ORDER BY h.id DESC LIMIT 20000""", (since,))
    for r in scans:
        r["version"] = vat(r["ts"])
        if anonymize:
            r["computer"] = an.alias("computer", r["computer"]) if r["computer"] else r["computer"]
        r["error"], r["warnings"], r["profile_name"] = an(r["error"]), an(r["warnings"]), an(r["profile_name"])
    # Meldungen erst anonymisieren, dann Zahlen zusammenfassen; getrennt: ganzer Zeitraum / nur laufende Version
    stats = {"zeitraum": _stats(since, an),
             "aktuelle_version": {"version": config.VERSION, "seit": max(cur_start, since), "update_im_zeitraum": cur_start > since,
                                  **_stats(max(cur_start, since), an)},
             "je_version": _per_version(logs, targets)}
    in_period = sorted({r["version"] for r in logs} | {r["version"] for r in targets} | {config.VERSION},
                       key=lambda v: next((h["started_at"] for h in hist if h["version"] == v), "0" if v.startswith("vor") else "9"))
    oldest = next((v for v in in_period if not v.startswith("vor") and v != "?"), config.VERSION)
    versions = {"aktuell": config.VERSION, "laeuft_seit": cur_start if hist else None, "im_zeitraum": in_period,
                "geschichte": hist,
                "hinweis": "Einträge vor der ersten bekannten Version: „vor X“ (Versionsgeschichte wird seit 2.22 geführt, "
                           "ältere Starts aus dem Protokoll nachgetragen, soweit es noch reicht)."}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr("LIESMICH.txt", _readme(days if days > 0 else "seit dem Update", anonymize, since))
        z.writestr("versionen.json", json.dumps(versions, ensure_ascii=False, indent=1, default=str))
        z.writestr("aenderungen.md", _changelog(oldest))
        z.writestr("behobene_fehler.txt", _fixed_list())
        z.writestr("umgebung.json", json.dumps(_environment(an), ensure_ascii=False, indent=1, default=str))
        z.writestr("einstellungen.json", json.dumps(_settings(an), ensure_ascii=False, indent=1, default=str))
        z.writestr("statistik.json", json.dumps(stats, ensure_ascii=False, indent=1, default=str))
        z.writestr("protokoll.csv", _csv(logs, ["ts", "version", "level", "category", "computer", "username", "message"]))
        z.writestr("fehler.txt", "\n".join(errors) or "Keine Warnungen oder Fehler im Zeitraum.\n")
        z.writestr("bereitstellungen.csv", _csv(targets, ["version", "deployment_id", "deployment", "source", "created_by", "computer_name",
                                                          "status", "exit_code", "message", "started_at", "finished_at"]))
        z.writestr("fehlgeschlagen.json", json.dumps(failed, ensure_ascii=False, indent=1, default=str))
        z.writestr("scans.csv", _csv(scans, ["ts", "version", "computer", "status", "duration_ms", "profile_name", "error", "warnings"]))
    an.flush()  # neue Platzhalter dauerhaft merken (Nachschlagen, gleiche Platzhalter im nächsten Paket)
    from . import pklog
    pklog.info("system", f"Diagnosepaket erstellt ({f'{days} Tage' if days > 0 else 'seit dem Update'}, {'anonymisiert' if anonymize else 'nicht anonymisiert'}).", user=user)
    return buf.getvalue(), f"pocketknife-diagnose-{config.VERSION}-{util.now()[:10]}-{label}{'-anonym' if anonymize else ''}.zip"


def lookup(alias):
    """Platzhalter → echter Name (nur in der Oberfläche, für Administratoren)."""
    _ensure()
    a = (alias or "").strip()
    rows = db.q("SELECT kind, real, alias FROM diag_alias WHERE lower(alias)=lower(?) OR lower(alias) LIKE lower(?)",
                (a, a + "-%"))
    return [{"kind": r["kind"], "real": r["real"], "alias": r["alias"]} for r in rows]


def mapping_csv():
    _ensure()
    rows = db.q("SELECT kind, alias, real, created FROM diag_alias ORDER BY kind, alias")
    return _csv(rows, ["kind", "alias", "real", "created"])
