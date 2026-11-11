"""Diagnostic package (Einstellungen → Wartung): a ZIP file for troubleshooting – it is only downloaded,
never sent on its own.

Contents: version and environment, settings (without passwords, keytab, keys), log of the period,
errors with technical details, statistics on scans and deployments, failed targets with step results.

Anonymize (recommended): computer, user and server names, domain, IP/MAC/mail addresses and AD paths are replaced
by placeholders. The placeholders are permanently the same (table diag_alias) – PC-017 is the same computer in every package.
Features that errors can depend on remain visible: length over 15 characters ("-LANG"), umlauts ("-UML"),
special characters ("-SZ"), short name vs. full DNS name. The mapping stays on the server; it can be looked up in the
UI or downloaded as CSV.
"""
import bisect
import collections
import csv
import html
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

# Settings whose values contain names/addresses (replaced or shortened as well when anonymizing)
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
            except Exception:  # noqa: BLE001 – e.g. index with placeholders that were already assigned twice (before 2.23)
                pass


# --- Version history --------------------------------------------------------------------------
def record_start():
    """At startup: since when which version has been running. The first time, filled in from the startup messages in the log."""
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
    """Version that was running at a point in time (entries before the first known version: "vor X")."""

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
    """CHANGELOG sections from the oldest version in the period until today (older ones do not count)."""
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
        if len(out) > 300:  # weekly security updates: many small versions
            break
    return "".join(out)


def _fixed_list():
    """Self-test checks = list of fixed errors that must not come back (with version)."""
    try:
        src = open(os.path.join(ROOT, "tools", "selftest.py"), encoding="utf-8").read()
    except OSError:
        return "tools/selftest.py nicht gefunden.\n"
    names = re.findall(r'@test\("((?:[^"\\]|\\.)*)"\)', src)
    return ("Behobene Fehler, die der Selbsttest bei jeder Version erneut prüft (Version = seit wann behoben):\n\n"
            + "\n".join(f"- {n}" for n in names) + "\n")


# --- Placeholders ---------------------------------------------------------------------------------
PREFIX = {"dc": "dom", "computer": "PC", "user": "USER", "server": "SRV", "ip": "IP", "mac": "MAC", "mail": "MAIL", "ou": "OU", "group": "GRUPPE",
          "display": "Person", "tenant": "MANDANT"}
ANON_DOMAIN, ANON_NETBIOS = "firma.local", "FIRMA"


def _marks(real):
    """Features that errors can depend on – remain visible in the placeholder."""
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
        self.text = {}      # real text (lower) → alias, for replacement in free text
        self._pairs = None
        self._spaced = None
        if not enabled:
            return
        _ensure()
        self.used = collections.defaultdict(set)   # assigned placeholders per kind
        self.pending = []                          # new mappings – saved in bulk (flush)
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
        # Assign the number in memory (build() never runs in parallel) – exactly one name per placeholder
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

    # Collect known names (before replacing in free text)
    def learn(self):
        if not self.enabled:
            return
        dom = (config.DOMAIN or "").lower()
        for c in db.q("SELECT name, dns_name, current_user, ip_addresses, mac_addresses, resolved_ip, verified_ip, addr_ip, "
                      "description FROM computers"):
            a = self.alias("computer", c["name"])
            if c["dns_name"] and c["dns_name"].lower() != (c["name"] or "").lower():
                self.text[c["dns_name"].lower()] = f"{a.lower()}.{ANON_DOMAIN}"  # full DNS name stays recognizable as such
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
        # AD groups from kiosk approvals (name or DN per line) – group names often reveal the organization
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
        # Audit 2.29: tenants (names reveal customers), their admin groups, display names of the sessions and
        # computer descriptions (often person names)
        try:
            for t in db.q("SELECT name, admin_group, admin_group_dn FROM tenants"):
                if t["name"]:
                    a = self.alias("tenant", t["name"])
                    esc = html.escape(t["name"], quote=False)
                    if esc != t["name"]:
                        self.text[esc.lower()] = a
                for g in (t["admin_group"], t["admin_group_dn"]):
                    g = str(g or "").strip()
                    m = re.match(r"^CN=((?:[^,\\]|\\.)+)", g, re.I)
                    g = m.group(1) if m else g.split("\\")[-1]
                    if g:
                        self.alias("group", g)
        except Exception:
            pass
        try:
            for r in db.q("SELECT DISTINCT display_name FROM sessions WHERE COALESCE(display_name,'') <> ''"):
                if " " in r["display_name"].strip():
                    self.alias("display", r["display_name"])
        except Exception:
            pass
        for c in db.q("SELECT description FROM computers WHERE COALESCE(description,'') <> ''"):
            d = c["description"].strip()
            if " " in d and 5 <= len(d) <= 120:  # single words ("Empfang") are mostly roles, not names
                self.alias("display", d)
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

    # Replacing in free text: extract words and look them up in the dictionary (instead of a huge regex that would have to be
    # recompiled for each new placeholder with thousands of names). Only entries with spaces (persons,
    # groups) go through a small regex.
    def _add_pair(self, kind, real, a):
        if self._pairs is None or kind in ("ip", "mac", "mail", "ou") or len(real) < 3:
            return
        if real in COMMON_WORDS or real in _vocabulary():  # never replace "admin", "Anmeldung" … as a name
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
            # Names that are not recognized as a "word" (spaces, ' & + brackets …), via a small regex
            sp = sorted((k for k in pairs if not WORD_RX.fullmatch(k)), key=len, reverse=True)
            self._spaced = re.compile(r"(?<![\wÄÖÜäöüß-])(" + "|".join(re.escape(k) for k in sp) + r")(?![\wÄÖÜäöüß-])",
                                      re.I) if sp else False
        if self._spaced:
            t = self._spaced.sub(lambda m: pairs.get(m.group(1).lower(), m.group(1)), t)

        def word(m):
            w = m.group(0)
            segs = re.split(r"([._])", w)  # pc7.firma.local, BUCHH-PC7_install.log: replace the longest known part
            out, i = [], 0
            while i < len(segs):
                if segs[i] in (".", "_"):
                    out.append(segs[i])
                    i += 1
                    continue
                hit = None
                for j in range(len(segs), i, -1):
                    if (j - i) % 2 == 0:
                        continue  # must end on a word, not on a separator
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
        """Anonymize free text. Passwords in command lines are always masked – even without anonymization."""
        if text is None:
            return text
        t = _mask_secrets(str(text))
        if not self.enabled:
            return t
        dn_parts = list(config.BASE_DN.split(",")) if config.BASE_DN else []
        # AD paths: DC=beispiel,DC=lan → DC=firma,DC=local; OU=…/CN=… → placeholder
        if dn_parts:
            t = re.sub(re.escape(config.BASE_DN), ",".join("DC=" + p for p in ANON_DOMAIN.split(".")), t, flags=re.I)
        t = re.sub(r"\b(DC)=([^,=\s\\]+)", lambda m: m.group(0) if m.group(2).lower() in ANON_DOMAIN.split(".")
                   else f"{m.group(1)}={self.alias('dc', m.group(2)).lower()}", t, flags=re.I)  # partial domains (DC=sub,…)
        t = re.sub(r"\b(OU)=((?:\\.|[^,\\])+)", lambda m: f"{m.group(1)}={self.alias('ou', m.group(2))}", t)
        t = re.sub(r"\b(CN)=((?:\\.|[^,\\])+)", lambda m: f"{m.group(1)}={self.alias('group', m.group(2))}", t)
        t = re.sub(r"[A-Za-z0-9._%+\-ÄÖÜäöüß]+@[A-Za-z0-9.\-ÄÖÜäöüß]+\.[A-Za-z]{2,}", lambda m: self.alias("mail", m.group(0)), t)
        t = re.sub(r"(?<![\d.])((?:\d{1,3}\.){3}\d{1,3})(?!\d|\.\d)", lambda m: m.group(1) if _keep_ip(m.group(1)) else self.alias("ip", m.group(1)), t)
        t = re.sub(r"(?<![0-9A-Fa-f:.\-])([0-9A-Fa-f]{2}([:-])[0-9A-Fa-f]{2}(?:\2[0-9A-Fa-f]{2}){4})(?![0-9A-Fa-f:\-]|\.[0-9A-Fa-f])", _mac_sub(self), t)
        t = re.sub(r"(?<![0-9A-Za-z:.])((?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?:%\w+)?)(?![0-9A-Za-z:]|\.\d)",
                   lambda m: m.group(1) if _keep_ip6(m.group(1)) else self.alias("ip", m.group(1).lower()), t)
        # DOMAIN\user – also unknown users (in JSON also with a double backslash)
        if config.NETBIOS:
            t = re.sub(re.escape(config.NETBIOS) + r"(\\+)([A-Za-z0-9._\-$ÄÖÜäöüß]+)",
                       lambda m: f"{ANON_NETBIOS}{m.group(1)}{self.alias('user', m.group(2))}", t, flags=re.I)
        # Failed login: the typed name can be anything (even an accidentally typed password)
        t = re.sub(r"(Anmeldung fehlgeschlagen für ')([^']{1,200})(')", lambda m: m.group(1) + self.alias("user", _user_part(m.group(2)) or "?") + m.group(3), t)
        return self._replace_words(t)


_build_lock = threading.Lock()
# "Word" for the name search in free text: letters of all languages (é, ø, ñ …), digits, dot, underscore, hyphen
WORD_RX = re.compile(r"[^\W_][\w.\-]*")

# Passwords in command lines (step titles, outputs): PASSWORD=…, /p:…, net use … /user:x PASSWORD, -AsPlainText
_SECRET_RXS = [
    (re.compile(r"(?i)\b([\w-]*(?:password|passwort|passwd|kennwort|pwd|secret|token|apikey|api_key)[\w-]*\s*[=:]\s*)"
                r"(\"[^\"]*\"|'[^']*'|[^\s;,]+)"), lambda m: m.group(1) + "***"),
    (re.compile(r"(?i)((?<![\w-])-(?:password|passwort|kennwort|pwd|secret|token)\s+)(\"[^\"]*\"|'[^']*'|\S+)"), lambda m: m.group(1) + "***"),
    (re.compile(r"(?i)(/user:\S+\s+)(?!/)(\"[^\"]*\"|\S+)"), lambda m: m.group(1) + "***"),
    (re.compile(r"(?i)(ConvertTo-SecureString\s+(?:-String\s+)?)(\"[^\"]*\"|'[^']*')"), lambda m: m.group(1) + "'***'"),
    # Audit 2.29: cmdkey /pass:, psexec -p, sqlcmd -P, net user <name> <password>, HTTP credentials, GitHub tokens
    (re.compile(r"(?i)(/pass(?:word)?:)(\"[^\"]*\"|\S+)"), lambda m: m.group(1) + "***"),
    (re.compile(r"(?<=\s)(-[pP]\s+)(?!-)(?![\d:]+(?:\s|$))(\"[^\"]*\"|'[^']*'|\S+)"), lambda m: m.group(1) + "***"),  # ports (-p 8443:8443) are kept
    (re.compile(r"(?i)(\bnet(?:\.exe)?\s+user\s+(?:\"[^\"]*\"|\S+)\s+)(?!/)(\"[^\"]*\"|\S+)"), lambda m: m.group(1) + "***"),
    (re.compile(r"\b([Bb]earer|[Bb]asic|BEARER|BASIC)\s+(?=\S*[\d+/=]|\S*[A-Z]\S*[a-z]|\S*[a-z]\S*[A-Z])[A-Za-z0-9._~+/=\-]{12,}"), lambda m: m.group(1) + " ***"),
    (re.compile(r"\b(?:gh[opusr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"), lambda m: "***"),
]


def _mask_secrets(t):
    for rx, fn in _SECRET_RXS:
        t = rx.sub(fn, t)
    return t


def _keep_ip6(a):
    """Not an IPv6 address: times (12:30:45), MAC-like strings, loopback ::1."""
    if re.fullmatch(r"\d{1,2}(:\d{2}){1,3}", a) or a in ("::", "::1") or re.fullmatch(r"([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", a):
        return True
    core = a.split("%")[0]
    if not re.search(r"[0-9A-Fa-f]", core):
        return True  # only colons, e.g. "[System.IO.File]::"
    return core.count(":") < 3 and "::" not in core


def _user_part(u):
    """"Kiosk: FIRMA\\max" → "max" (log and deployments name the trigger with a prefix)."""
    u = re.sub(r"^(Kiosk|Zeitplan|Regel|Tool):\s*", "", str(u or "").strip(), flags=re.I)
    return u.split("\\")[-1].split("@")[0].strip()


def _mac_sub(an):
    def sub(m):
        mac = m.group(1)
        if mac.upper().startswith("02:00:00:00:"):
            return mac  # already a placeholder
        if m.group(2) == "-" and re.fullmatch(r"[\d-]+", mac):
            return mac  # only digits with hyphens: date/time (e.g. in log file names), not a MAC address
        return an.alias("mac", mac.upper().replace("-", ":"))
    return sub


_vocab = None


def _vocabulary():
    """Words from the POCKET KNIFE texts (from 5 letters) – never replaced as a name in free text. Otherwise a
    user or computer that happens to be called "Anmeldung" would turn every message into "USER-004 fehlgeschlagen"."""
    global _vocab
    if _vocab is None:
        words = set()
        try:
            en = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "i18n", "en.json"), encoding="utf-8"))
            for k, v in en.items():
                if not v:
                    continue
                low_v = v.lower()
                # only words that were translated – names in examples ("FIRMA\\mueller") also appear in the translation
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


# --- Contents --------------------------------------------------------------------------------------
def _csv(rows, cols):
    from . import reports
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(cols)
    for r in rows:
        w.writerow([reports._safe(r.get(c)) for c in cols])  # Audit 2.29: defuse formula injection
    return "\ufeff" + buf.getvalue()


def _norm_msg(m):
    """Message without numbers/names in quotation marks – for counting similar errors."""
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
            v = an.alias("user", _user_part(v))  # the whole value is an account – also replace short names
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


def _performance(an):
    """2.44.3: where does time go? Load peaks, duration of the main queries, sizes of the large columns - only names
    of fields and counts, never their contents."""
    import time as _t
    from . import collections_, loadstats, main
    out: dict = {}
    try:
        out["load"] = loadstats.view()
    except Exception as e:  # noqa: BLE001
        out["load"] = str(e)
    adm = {"username": "diagnose", "scope": None, "role": "admin"}
    q: dict = {}
    for name, fn in (("dashboard", lambda: main.api_dashboard(adm)), ("computers", lambda: main.api_computers(0, adm)),
                     ("collections", lambda: main.api_collections(adm)), ("software", lambda: main.api_software(0, adm))):
        a = _t.time()
        try:
            fn()
            q[name] = round((_t.time() - a) * 1000)
        except Exception as e:  # noqa: BLE001
            q[name] = f"Fehler: {an(str(e))[:200]}"
    out["queries_ms"] = q
    tim = []
    for r in db.q("SELECT id, name, type, builtin FROM collections WHERE type='dynamic'"):
        a = _t.time()
        try:
            collections_.count(r["id"])
        except Exception:  # noqa: BLE001
            pass
        tim.append((round((_t.time() - a) * 1000, 1), r["name"] if r["builtin"] else an(r["name"])))
    tim.sort(reverse=True)
    out["collections_ms"] = {"count": len(tim), "total": round(sum(x[0] for x in tim)), "slowest": tim[:10]}
    cols = [r["name"] for r in db.q("PRAGMA table_info(computers)")]
    sizes = sorted(((db.val(f'SELECT SUM(length("{c}")) FROM computers', default=0) or 0, c) for c in cols), reverse=True)
    out["computer_columns_kb"] = {c: round(n / 1024) for n, c in sizes[:10]}
    big = []
    for r in db.q("SELECT name, length(extra) n, extra FROM computers ORDER BY n DESC LIMIT 5"):
        try:
            d = json.loads(r["extra"] or "{}")
        except ValueError:
            d = {}
        keys = sorted(((len(json.dumps(v, default=str)), k) for k, v in d.items()) if isinstance(d, dict) else [], reverse=True)[:8]
        big.append({"computer": an.alias("computer", r["name"]) if an.enabled else r["name"], "kb": round((r["n"] or 0) / 1024),
                    "keys_kb": {k: round(n / 1024, 1) for n, k in keys}})
    out["largest_extras"] = big
    out["inventory_data_kb"] = {r["category"] if not str(r["category"]).startswith("custom:") else "custom": round((r["s"] or 0) / 1024)
                                for r in db.q("SELECT category, SUM(length(json)) s FROM inventory_data GROUP BY category ORDER BY s DESC LIMIT 15")}
    # 2.44.4: what really happened since start - not only the measurement while the package is built
    out["requests_since_start_ms"] = loadstats.durations(25)
    from . import inventory, remote_desktop, scheduler
    out["scan_sizes_since_start"] = {k: dict(v, max_computer=an.alias("computer", v["max_computer"]) if an.enabled and v.get("max_computer")
                                             else v.get("max_computer")) for k, v in list(inventory.scan_sizes().items())[:25]}
    out["database"] = _db_info()
    out["remote_sessions"] = remote_desktop.history()
    tasks = {}
    with loadstats._lock:
        raw = {k: dict(v) for k, v in loadstats._tasks.items()}
    for k, v in raw.items():
        tasks[k] = {"runs": v.get("runs"), "last_at": v.get("last_at"), "last_seconds": v.get("last_wall"),
                    "cpu_seconds": round(v.get("cpu") or 0, 1), "errors": v.get("errors", 0),
                    "last_error": an(v.get("last_error")) if v.get("last_error") else None}
    out["background_tasks"] = tasks
    st = lambda k: db.setting_raw(k)  # noqa: E731
    out["daily"] = {"update_status": st("update_status_at"), "db_maintenance": st("db_maintenance_last"),
                    "backup": an(st("backup_last")), "backup_auto": st("backup_last_auto"),
                    "scheduler_last": {k: util.fmt(__import__("datetime").datetime.fromtimestamp(v)) for k, v in
                                       dict(getattr(scheduler, "_last", {})).items() if isinstance(v, (int, float)) and v > 0}}
    return out


def _db_info():
    """Size per table (dbstat, if available), log file (WAL), free space."""
    info: dict = {}
    try:
        info.update(db.stats())
    except Exception as e:  # noqa: BLE001
        info["error"] = str(e)[:200]
    try:
        info["tables_kb"] = {r["name"]: round(r["s"] / 1024) for r in db.q(
            "SELECT name, SUM(pgsize) s FROM dbstat GROUP BY name ORDER BY s DESC LIMIT 25")}
    except Exception:  # noqa: BLE001 - SQLite without dbstat: number of rows instead
        cnt = {}
        for t in [r["name"] for r in db.q("SELECT name FROM sqlite_master WHERE type='table'")][:80]:
            try:
                cnt[t] = db.val(f'SELECT COUNT(*) FROM "{t}"', default=0)
            except Exception:  # noqa: BLE001
                pass
        info["table_rows"] = dict(sorted(cnt.items(), key=lambda x: -(x[1] or 0))[:25])
    return info


def _inventory_info(since, an):
    """2.44.4: scan errors per area, winget assignment, clients (PowerShell version/language mode, winget) - counts only."""
    errs: dict = {}
    for r in db.q("SELECT warnings FROM scan_history WHERE ts >= ? AND warnings IS NOT NULL", (since,)):
        for part in str(r["warnings"]).split("; "):
            m = re.match(r"^\s*([A-Za-z_][\w ]{0,30}):\s*(.*)$", part)
            area = m.group(1) if m else "sonstige"
            msg = _norm_msg(an(m.group(2) if m else part))[:160]
            e = errs.setdefault(area, {"count": 0, "top": collections.Counter()})
            e["count"] += 1
            e["top"][msg] += 1
    scan_errors = {k: {"count": v["count"], "top": v["top"].most_common(5)} for k, v in sorted(errs.items(), key=lambda x: -x[1]["count"])}
    v = lambda sql: db.val(sql, default=0)  # noqa: E731
    from . import winget
    st = winget.status()
    wg = {"index_entries": st.get("count"), "index_updated": st.get("updated"), "index_error": st.get("error"),
          "software_rows": v("SELECT COUNT(*) FROM software"), "assigned": v("SELECT COUNT(*) FROM software WHERE winget_id IS NOT NULL"),
          "assigned_sure": v("SELECT COUNT(*) FROM software WHERE winget_id IS NOT NULL AND winget_sure=1"),
          "outdated": v("SELECT COUNT(*) FROM software WHERE outdated=1"),
          "msix": v("SELECT COUNT(*) FROM software WHERE hive='MSIX'"),
          "msix_builtin_hidden": bool(db.setting("msix_hide_builtin")),
          "overrides": v("SELECT COUNT(*) FROM winget_overrides"),
          "update_packages": v("SELECT COUNT(*) FROM winget_update_packages")}
    cl = lambda expr: {str(r["k"]): r["n"] for r in db.q(  # noqa: E731
        f"SELECT {expr} k, COUNT(*) n FROM computers WHERE last_scan IS NOT NULL GROUP BY 1 ORDER BY n DESC LIMIT 15")}
    clients = {"powershell": cl("json_extract(extra, '$.ps_version')"), "language_mode": cl("json_extract(extra, '$.ps_language')"),
               "winget": cl("CASE WHEN COALESCE(json_extract(extra, '$.winget'), '') = '' THEN 'fehlt' ELSE 'vorhanden' END"),
               "os_build": cl("os_build")}
    return {"since": since, "scan_errors_by_area": scan_errors, "winget": wg, "clients": clients}


def _update_status():
    """2.44.3: update status at a glance - Microsoft data, statuses, Office channels/versions (no computer names)."""
    from . import updstatus
    st = lambda k: db.val("SELECT value FROM settings WHERE key=?", (k,))  # noqa: E731
    out = {"grace_days": db.setting("update_grace_days"), "microsoft": bool(db.setting("update_msrc")),
           "at": st("update_status_at"), "msrc_builds": st("update_status_msrc"), "office_channels": st("update_status_office"),
           "windows": {str(r["s"]): r["n"] for r in db.q("SELECT win_update s, COUNT(*) n FROM computers GROUP BY 1")},
           "office": {str(r["s"]): r["n"] for r in db.q("SELECT office_update s, COUNT(*) n FROM computers GROUP BY 1")},
           "windows_builds": {f"{r['os_build']}": r["n"] for r in db.q(
               "SELECT os_build, COUNT(*) n FROM computers WHERE os_build IS NOT NULL GROUP BY 1 ORDER BY n DESC LIMIT 20")}}
    inst: dict = {}
    for c in db.q("SELECT id, extra FROM computers"):
        try:
            for o in updstatus.office_installs(c):
                k = f"{o['product']} | {o['channel']} | {o['version']}"
                inst[k] = inst.get(k, 0) + 1
        except Exception:  # noqa: BLE001
            pass
    out["office_installations"] = dict(sorted(inst.items(), key=lambda x: -x[1])[:30])
    try:
        ms = json.loads(st("update_office_ms") or "{}")
        out["office_microsoft"] = {ch: e.get("latest") for ch, e in (ms.get("channels") or {}).items()}
    except ValueError:
        pass
    out["refs"] = db.q("SELECT kind, key, MAX(value) top, COUNT(*) n, MAX(released) last, GROUP_CONCAT(DISTINCT source) src "
                       "FROM update_refs GROUP BY kind, key ORDER BY kind, key LIMIT 60")
    return out


def _per_version(logs, targets):
    """Per version: warnings/errors in the log and results of the deployments."""
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
  leistung.json        Rechenlast und Lastspitzen, Dauer der wichtigsten Abfragen, langsamste Sammlungen, größte Felder
                       (nur Feldnamen und Größen, keine Inhalte)
  update_status.json   Update-Status: Microsoft-Abgleich, Stände je Status, Office-Installationen je Produkt/Kanal/Version
                       leistung.json enthält außerdem: Dauer jeder Seite seit dem Start, Größe der Scan-Ergebnisse je Bereich,
                       Datenbank je Tabelle, Fernsitzungen, Hintergrundaufgaben und tägliche Läufe (mit Fehlern)
  inventar.json        Scan-Fehler je Bereich, winget-Zuordnung, Clients (PowerShell-Version, Sprachmodus, winget)
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
    """ZIP as bytes. days: 1–30, 0 = since the last update. Only one package at a time."""
    with _build_lock:
        return _build(days, anonymize, user)


def _build(days, anonymize, user):
    hist = history()
    vat = _VersionAt(hist)
    days = int(7 if days is None else days)
    if days <= 0:  # 0 = since the start of the running version (at most DAYS_MAX days)
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
        # even without anonymization through an(): passwords in command lines are always masked
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
                        s[k] = an(v)  # title (first command line, paths), message …
            failed.append({k: r[k] for k in ("version", "deployment_id", "deployment", "source", "computer_name", "exit_code", "message",
                                              "started_at", "finished_at")} | {"steps": steps, "output_tail": an((r["output"] or "")[-MAX_OUTPUT:])})
    scans = db.q("""SELECT h.ts, c.name AS computer, h.status, h.duration_ms, h.profile_name, h.error, h.warnings FROM scan_history h
                    LEFT JOIN computers c ON c.id = h.computer_id WHERE h.ts >= ? ORDER BY h.id DESC LIMIT 20000""", (since,))
    for r in scans:
        r["version"] = vat(r["ts"])
        if anonymize:
            r["computer"] = an.alias("computer", r["computer"]) if r["computer"] else r["computer"]
        r["error"], r["warnings"], r["profile_name"] = an(r["error"]), an(r["warnings"]), an(r["profile_name"])
    # First anonymize messages, then aggregate numbers; separately: whole period / running version only
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
        for fname, fn in (("leistung.json", lambda: _performance(an)), ("update_status.json", _update_status),
                          ("inventar.json", lambda: _inventory_info(since, an))):
            try:
                z.writestr(fname, json.dumps(fn(), ensure_ascii=False, indent=1, default=str))
            except Exception as e:  # noqa: BLE001 - the package itself must always work
                z.writestr(fname, json.dumps({"error": an(str(e))[:500]}, ensure_ascii=False))
        z.writestr("protokoll.csv", _csv(logs, ["ts", "version", "level", "category", "computer", "username", "message"]))
        z.writestr("fehler.txt", "\n".join(errors) or "Keine Warnungen oder Fehler im Zeitraum.\n")
        z.writestr("bereitstellungen.csv", _csv(targets, ["version", "deployment_id", "deployment", "source", "created_by", "computer_name",
                                                          "status", "exit_code", "message", "started_at", "finished_at"]))
        z.writestr("fehlgeschlagen.json", json.dumps(failed, ensure_ascii=False, indent=1, default=str))
        z.writestr("scans.csv", _csv(scans, ["ts", "version", "computer", "status", "duration_ms", "profile_name", "error", "warnings"]))
    an.flush()  # remember new placeholders permanently (lookup, same placeholders in the next package)
    from . import pklog
    pklog.info("system", f"Diagnosepaket erstellt ({f'{days} Tage' if days > 0 else 'seit dem Update'}, {'anonymisiert' if anonymize else 'nicht anonymisiert'}).", user=user)
    return buf.getvalue(), f"pocketknife-diagnose-{config.VERSION}-{util.now()[:10]}-{label}{'-anonym' if anonymize else ''}.zip"


def lookup(alias):
    """Placeholder → real name (only in the UI, for administrators)."""
    _ensure()
    a = (alias or "").strip()
    rows = db.q("SELECT kind, real, alias FROM diag_alias WHERE lower(alias)=lower(?) OR lower(alias) LIKE lower(?)",
                (a, a + "-%"))
    return [{"kind": r["kind"], "real": r["real"], "alias": r["alias"]} for r in rows]


def mapping_csv():
    _ensure()
    rows = db.q("SELECT kind, alias, real, created FROM diag_alias ORDER BY kind, alias")
    return _csv(rows, ["kind", "alias", "real", "created"])
