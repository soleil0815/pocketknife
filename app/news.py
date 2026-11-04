"""News in the app (2.28) and notice of new versions.

1. News: app/news.json is shipped with the program (short, understandable points per version, de/en,
   kind security/new/improved/fixed, audience all/admin). POCKET KNIFE remembers the last seen version per user
   (table news_seen) - the sidebar discreetly shows "Neu" until they have looked at it. A new user
   sees only the current version as new, not the whole history.

2. New version available (main admins only): the SERVER (never the browser) asks GitHub once a day for the
   latest release and fetches its app/news.json - so even security updates of a new version can be recognized in advance.
   Private repository: read token encrypted in the vault (setting "update_token", never to the UI, never in the
   diagnostic package). Without internet/token: off or "nicht erreichbar" - never an error in the UI.
"""
import functools
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import access, config, db, pklog, proxy, util, vault

KINDS = ("security", "new", "improved", "fixed")
AUDIENCES = ("all", "admin")
TOKEN_KEY = "update_token"          # encrypted, not in DEFAULT_SETTINGS (→ never visible via /api/settings)
STATUS_KEY = "update_status"        # JSON of the last comparison
CHECK_HOURS = 24
VER_RX = re.compile(r"^\d+(\.\d+){1,3}(-s\d+)?$")  # 2.29.6 or 2.29.6-s3 (security update)
REPO_RX = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")
API = "https://api.github.com"
_lock = threading.Lock()
_cache = {"mtime": None, "data": None}
NEWS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "news.json")


# --- News from news.json ------------------------------------------------------------------------------------
def validate(data):
    """Checks the structure (also for news from GitHub) and returns the cleaned list of versions."""
    out = []
    for v in (data or {}).get("versions") or []:
        ver = str(v.get("version") or "").strip()
        if not VER_RX.match(ver):
            raise ValueError(f"news.json: ungültige Version „{ver}“")
        items = []
        for it in v.get("items") or []:
            kind, aud = it.get("kind"), it.get("audience", "all")
            if kind not in KINDS or aud not in AUDIENCES:
                raise ValueError(f"news.json {ver}: Art/Zielgruppe ungültig ({kind}/{aud})")
            de, en = str(it.get("de") or "").strip(), str(it.get("en") or "").strip()
            if not de or not en:
                raise ValueError(f"news.json {ver}: Text auf Deutsch und Englisch nötig")
            items.append({"kind": kind, "audience": aud, "de": de[:1000], "en": en[:1000]})
        out.append({"version": ver, "date": str(v.get("date") or "")[:10], "items": items})
    out.sort(key=functools.cmp_to_key(lambda a, b: util.vcmp(a["version"], b["version"])), reverse=True)
    return out


def versions():
    """News of all versions (newest first) - from the shipped file, cached."""
    with _lock:
        try:
            mt = os.path.getmtime(NEWS_FILE)
        except OSError:
            return []
        if _cache["mtime"] != mt:
            try:
                with open(NEWS_FILE, encoding="utf-8") as fh:
                    _cache["data"] = validate(json.load(fh))
            except (OSError, ValueError) as e:
                pklog.warn("system", f"Neuigkeiten (news.json) nicht lesbar: {e}")
                _cache["data"] = []
            _cache["mtime"] = mt
        return _cache["data"]


def _visible(items, user):
    adm = not access.scoped(user)
    return [i for i in items if i["audience"] == "all" or adm]


def _order(items):
    rank = {k: n for n, k in enumerate(KINDS)}  # security first
    return sorted(items, key=lambda i: rank.get(i["kind"], 9))


def seen_version(username):
    return db.val("SELECT version FROM news_seen WHERE username=?", (username,))


def for_user(user):
    """News for the UI: all versions up to the running one; unseen = newer than last seen."""
    cur = config.VERSION
    allv = [v for v in versions() if util.vcmp(v["version"], cur) <= 0]
    seen = seen_version(user["username"])
    if seen is None:
        # New user: only the running version counts as new (not the whole history)
        older = [v["version"] for v in allv if util.vcmp(v["version"], cur) < 0]
        seen = older[0] if older else "0"
    out = []
    for v in allv:
        items = _order(_visible(v["items"], user))
        if not items:
            continue
        out.append({"version": v["version"], "date": v["date"], "items": items, "unseen": util.vcmp(v["version"], seen) > 0})
    unseen = [v for v in out if v["unseen"]]
    res = {"current": cur, "seen": seen, "versions": out, "unseen": len(unseen),
           "security": any(i["kind"] == "security" for v in unseen for i in v["items"])}
    if not access.scoped(user):
        res["update"] = update_info(user)
        res["hints"] = hints()
    return res


def mark_seen(user, version=None):
    v = str(version or config.VERSION)
    if not VER_RX.match(v) or util.vcmp(v, config.VERSION) > 0:
        v = config.VERSION
    db.ex("INSERT INTO news_seen(username, version, at) VALUES(?,?,?) ON CONFLICT(username) DO UPDATE SET "
          "version=excluded.version, at=excluded.at", (user["username"], v, util.now()))
    return {"seen": v}


# --- New version available (GitHub) --------------------------------------------------------------------------------
def settings():
    return {"enabled": bool(db.setting("update_check_enabled")), "repo": str(db.setting("update_repo") or "").strip(),
            "token_set": bool(db.setting_raw(TOKEN_KEY)), "status": status()}


def save(data):
    repo = str(data.get("repo") if data.get("repo") is not None else db.setting("update_repo") or "").strip()
    repo = re.sub(r"^https?://github\.com/", "", repo).strip("/")
    if repo.endswith(".git"):
        repo = repo[:-4]
    if repo and not REPO_RX.match(repo):
        raise ValueError("Repository bitte als „besitzer/name“ angeben, z. B. soleil0815/pocketknife.")
    old = (db.setting("update_repo") or "", bool(db.setting_raw(TOKEN_KEY)))
    db.set_setting("update_repo", repo)
    if "enabled" in data:
        db.set_setting("update_check_enabled", 1 if data.get("enabled") else 0)
    tok = str(data.get("token") or "").strip()
    if data.get("clear_token"):
        db.ex("DELETE FROM settings WHERE key=?", (TOKEN_KEY,))
    elif tok:
        if len(tok) > 400 or not re.match(r"^[A-Za-z0-9_\-.]+$", tok):
            raise ValueError("Das Token enthält ungültige Zeichen.")
        db.set_setting(TOKEN_KEY, vault.encrypt(tok))
    if (repo, bool(db.setting_raw(TOKEN_KEY))) != old or tok:
        db.ex("DELETE FROM settings WHERE key=?", (STATUS_KEY,))  # the old state no longer fits
    return settings()


def status():
    try:
        return json.loads(db.setting_raw(STATUS_KEY) or "null")
    except ValueError:
        return None


def _get(url, token, raw=False, timeout=20):
    hdr = {"User-Agent": f"POCKETKNIFE/{config.VERSION}", "X-GitHub-Api-Version": "2022-11-28",
           "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json"}
    if token:
        hdr["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=hdr)
    with proxy.urlopen(req, timeout=timeout) as r:
        return r.read(2 * 1024 * 1024)


def _fetch(repo, token):
    """Latest release + its news. Replaceable in the self-test."""
    rel = json.loads(_get(f"{API}/repos/{repo}/releases/latest", token))
    tag = str(rel.get("tag_name") or "")
    news = None
    try:
        news = json.loads(_get(f"{API}/repos/{repo}/contents/app/news.json?ref={urllib.parse.quote(tag)}", token, raw=True))
    except Exception:  # news are nice, but not necessary
        news = None
    return {"tag": tag, "url": str(rel.get("html_url") or ""), "published": str(rel.get("published_at") or "")[:19],
            "news": news}


def check(force=False, who=None):
    """Once a day (scheduler) or "Jetzt prüfen": query the latest release and save the state."""
    s = settings()
    st = s["status"] or {}
    if not s["enabled"] or not s["repo"]:
        return st
    if not force and st.get("checked_ts") and time.time() - st["checked_ts"] < CHECK_HOURS * 3600:
        return st
    token = vault.decrypt(db.setting_raw(TOKEN_KEY)) if s["token_set"] else ""
    out = {"checked": util.now(), "checked_ts": time.time(), "repo": s["repo"], "ok": False}
    try:
        r = _fetch(s["repo"], token)
        ver = r["tag"].lstrip("vV")
        if not VER_RX.match(ver):
            raise ValueError(f"Release-Tag „{r['tag']}“ ist keine Versionsnummer")
        newer = []
        if r.get("news"):
            try:
                newer = [v for v in validate(r["news"]) if util.vcmp(v["version"], config.VERSION) > 0]
            except ValueError:
                newer = []
        out.update(ok=True, latest=ver, url=r["url"] if r["url"].startswith("https://github.com/") else "",
                   published=r["published"], newer=newer[:20])
    except urllib.error.HTTPError as e:
        out["error"] = {401: "Token ungültig oder abgelaufen.",
                        403: "Zugriff verweigert oder zu viele Anfragen – später erneut.",
                        404: "Repository nicht gefunden – bei einem privaten Repository ein Lese-Token hinterlegen."}.get(
            e.code, f"GitHub antwortet mit {e.code}.")
    except Exception as e:  # noqa: BLE001 – no internet, proxy, DNS ...
        out["error"] = f"GitHub nicht erreichbar: {proxy.safe_error(e)[:200]}"
    if token:
        for k in ("error",):
            if out.get(k):
                out[k] = out[k].replace(token, "***")
    prev = st.get("latest")
    db.set_setting(STATUS_KEY, json.dumps(out, ensure_ascii=False))
    if out.get("ok") and out.get("latest") and util.vcmp(out["latest"], config.VERSION) > 0 and out["latest"] != prev:
        sec = any(i["kind"] == "security" for v in out.get("newer") or [] for i in v["items"])
        (pklog.warn if sec else pklog.info)("system", f"POCKET KNIFE {out['latest']} ist verfügbar"
                                            + (" (mit Sicherheitsupdates)" if sec else "") + " – Update: Container-Verwaltung oder sudo sh update.sh",
                                            user=who)
    return out


def update_info(user):
    """For the sidebar (main admins only): newer version, its news, security update yes/no."""
    s = settings()
    st = s["status"] or {}
    if not s["enabled"] or not st.get("ok") or not st.get("latest") or util.vcmp(st["latest"], config.VERSION) <= 0:
        return None
    vers = [{"version": v["version"], "date": v["date"], "items": _order(_visible(v["items"], user))} for v in st.get("newer") or []]
    return {"latest": st["latest"], "url": st.get("url") or "", "published": st.get("published") or "",
            "versions": [v for v in vers if v["items"]],
            "security": any(i["kind"] == "security" for v in vers for i in v["items"]),
            "command": "sudo sh update.sh"}


# --- Notices for main admins (2.33) ----------------------------------------------------------------------------
COMPOSE_VERSION = 2  # state of the docker-compose.yml that this program expects (PK_COMPOSE_VERSION in the file)


def compose_version():
    try:
        return int(str(os.environ.get("PK_COMPOSE_VERSION") or "0").strip() or 0)
    except ValueError:
        return 0


def hints():
    from . import backup
    out = []
    if compose_version() < COMPOSE_VERSION:
        out.append({"id": "compose", "title": "Neue docker-compose.yml nötig",
                    "text": "Die docker-compose.yml auf dem Docker-Host ist älter als dieses Programm (z. B. guacd kommt jetzt aus "
                            "einem eigenen Image). Einmal auf dem Docker-Host im Ordner mit der docker-compose.yml ausführen – das "
                            "legt die neue Datei hin und behält die alte als docker-compose.yml.alt:",
                    "command": "sudo sh update.sh"})
    if backup.NEWER_DB:
        out.append({"id": "newer_db", "title": "Datenbank stammt von einer neueren Version",
                    "text": f"Diese Datenbank wurde schon von POCKET KNIFE {backup.NEWER_DB} benutzt. Läuft etwas nicht richtig, "
                            f"die Sicherung „vor-{backup.NEWER_DB}“ zurückspielen (Hilfe → Sicherung) oder wieder Version "
                            f"{backup.NEWER_DB} starten."})
    return out
