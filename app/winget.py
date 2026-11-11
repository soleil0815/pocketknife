"""winget catalog: loads the official index (source2.msix) and matches installed software."""
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import urllib.request
import zipfile

from . import config, db, pklog, proxy, util

URLS = ["https://cdn.winget.microsoft.com/cache/source2.msix",
        "https://cdn.winget.microsoft.com/cache/source.msix"]

_lock = threading.RLock()
_idx = {"loaded": False, "schema": None, "count": 0, "updated": None, "error": None,
        "pkgs": {}, "by_id": {}, "by_pc": {}, "by_name": {}}
_overrides = {"data": None}
_refreshing = {"on": False}

_LEGAL = re.compile(r"\b(inc|incorporated|corp|corporation|llc|ltd|limited|gmbh|co|company|ag|sa|bv|srl|plc)\b")
_ARCH = re.compile(r"\b(x64|x86|x86_64|amd64|arm64|64-bit|32-bit|64 bit|32 bit|64bit|32bit)\b")


def norm(s):
    s = (s or "").lower()
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s)
    s = _ARCH.sub(" ", s)
    s = re.sub(r"\bv?\d+(\.\d+)+\S*", " ", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def norm_pub(s):
    s = (s or "").lower()
    s = _LEGAL.sub(" ", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def index_path():
    return os.path.join(config.WINGET_DIR, "index.db")


def status():
    p = index_path()
    return {"loaded": _idx["loaded"], "schema": _idx["schema"], "count": _idx["count"],
            "updated": _idx["updated"], "error": _idx["error"], "refreshing": _refreshing["on"],
            "file": os.path.exists(p)}


# --- Loading -------------------------------------------------------------------
def _cols(con, t):
    return [r[1] for r in con.execute(f"PRAGMA table_info({t})")]


def load():
    p = index_path()
    if not os.path.exists(p):
        _idx["error"] = "Noch kein winget-Index geladen."
        return False
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        pkgs = {}
        maps = {"pc": [], "name": [], "pub": []}  # pc: product codes and (2.40.1) MSIX package family names
        if "packages" in tables and "latest_version" in _cols(con, "packages"):
            schema = "v2"
            pc = _cols(con, "packages")
            arp = "arp_max_version" if "arp_max_version" in pc else "NULL"
            for rowid, pid, name, latest, arpmax in con.execute(
                    f"SELECT rowid, id, name, latest_version, {arp} FROM packages"):
                pkgs[rowid] = {"id": pid, "name": name, "latest": latest, "arp": arpmax, "pub": ""}
            for tbl, key in (("productcodes2", "pc"), ("pfns2", "pc"), ("norm_names2", "name"), ("norm_publishers2", "pub")):
                if tbl not in tables:
                    continue
                cols = _cols(con, tbl)
                if "package" not in cols:
                    continue
                vcol = [c for c in cols if c not in ("rowid", "package")]
                if not vcol:
                    continue
                maps[key] += list(con.execute(f"SELECT {vcol[0]}, package FROM {tbl}"))
        elif "manifest" in tables:
            schema = "v1"
            q = ("SELECT i.id, n.name, v.version FROM manifest m JOIN ids i ON m.id=i.rowid "
                 "JOIN names n ON m.name=n.rowid JOIN versions v ON m.version=v.rowid")
            for pid, name, ver in con.execute(q):
                cur = pkgs.get(pid)
                if not cur or util.vcmp(ver, cur["latest"]) > 0:
                    pkgs[pid] = {"id": pid, "name": name, "latest": ver, "arp": None, "pub": ""}
        else:
            raise ValueError("Unbekanntes Index-Format")
        con.close()
        by_id, by_pc, by_name = {}, {}, {}
        for k, pk in pkgs.items():
            by_id[pk["id"].lower()] = k
            n = norm(pk["name"])
            if n:
                by_name.setdefault(n, set()).add(k)
        for v, k in maps["pc"]:
            if v and k in pkgs:
                by_pc.setdefault(str(v).lower().strip("{}"), set()).add(k)
        for v, k in maps["name"]:
            n = norm(v)
            if n and k in pkgs:
                by_name.setdefault(n, set()).add(k)
        for v, k in maps["pub"]:
            if k in pkgs and v and not pkgs[k]["pub"]:
                pkgs[k]["pub"] = norm_pub(v)
        with _lock:
            _idx.update({"loaded": True, "schema": schema, "count": len(pkgs), "pkgs": pkgs, "by_id": by_id,
                         "by_pc": by_pc, "by_name": by_name, "error": None,
                         "updated": util.fmt(__import__("datetime").datetime.fromtimestamp(os.path.getmtime(p)))})
        try:
            from . import wingetrelease
            wingetrelease.sync()  # 2.36: first sighting of new versions (deferral)
        except Exception as e:  # noqa: BLE001
            pklog.warn("winget", f"Freigabe neuer Versionen: Abgleich fehlgeschlagen: {e}")
        return True
    except Exception as e:
        _idx["error"] = f"Index konnte nicht gelesen werden: {e}"
        pklog.error("winget", _idx["error"])
        return False


def install_msix(path):
    """Unpacks Public/index.db from a source*.msix and activates it."""
    with zipfile.ZipFile(path) as z:
        member = next((m for m in z.namelist() if m.lower().endswith("public/index.db")), None)
        if not member:
            raise ValueError("Die Datei enthält keinen winget-Index (Public/index.db).")
        if z.getinfo(member).file_size > 2 * 1024 ** 3:
            raise ValueError("Der winget-Index in der Datei ist unplausibel groß.")
        fd, tmp = tempfile.mkstemp(dir=config.WINGET_DIR, suffix=".db")
        os.close(fd)
        with z.open(member) as src, open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst)
    con = sqlite3.connect(tmp)
    con.execute("SELECT name FROM sqlite_master").fetchall()
    con.close()
    os.replace(tmp, index_path())
    load()
    rematch_all()


MAX_MSIX = 1024 ** 3  # 2.36.3 (Audit): safety limit - the source is usually only a few MB


def _copy_limited(src, dst, limit=None):
    """Download to /data (same disk as the database) only up to the safety limit - a faulty or
    intercepting proxy must not fill up the disk."""
    limit = MAX_MSIX if limit is None else limit
    n = 0
    while True:
        b = src.read(1024 * 1024)
        if not b:
            return n
        n += len(b)
        if n > limit:
            raise ValueError(f"Die Datei ist unplausibel groß (über {limit // 1024 ** 2} MB) – abgebrochen.")
        dst.write(b)


def refresh(user=None):
    if _refreshing["on"]:
        return False
    _refreshing["on"] = True
    try:
        last = None
        for url in URLS:
            fd, tmp = tempfile.mkstemp(dir=config.WINGET_DIR, suffix=".msix")
            os.close(fd)
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "POCKETKNIFE/" + config.VERSION})
                with proxy.urlopen(req, timeout=180) as r, open(tmp, "wb") as f:
                    _copy_limited(r, f)
                install_msix(tmp)
                pklog.info("winget", f"winget-Index aktualisiert ({_idx['count']} Pakete, Format {_idx['schema']}).",
                           user=user)
                return True
            except Exception as e:
                last = proxy.safe_error(e)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
        _idx["error"] = f"Download fehlgeschlagen: {last}"
        pklog.error("winget", _idx["error"], user=user)
        return False
    finally:
        _refreshing["on"] = False


# --- Exceptions of the up-to-date check (own list) ---------------------------------------
# Programs that should never count as "outdated" - e.g. runtime libraries, specialist applications with a fixed version.
# Patterns with * and ?, case-insensitive; field: name, publisher or winget_id.
EXCL_FIELDS = {"name": "Name", "publisher": "Hersteller", "winget_id": "winget-ID"}
_excl = {"data": None}


def wildcard(pattern):
    """Only * and ? as wildcards (square brackets as in "Tool [x64]" are literal), case-insensitive."""
    pat = re.sub(r"\*{2,}", "*", pattern or "")  # ** → * (otherwise needless backtracking)
    rx = "".join(".*" if ch == "*" else "." if ch == "?" else re.escape(ch) for ch in pat)
    return re.compile(f"^{rx}$", re.I | re.S)


def exclusions(reload=False):
    if reload or _excl["data"] is None:
        _excl["data"] = [dict(r, _rx=wildcard(r["pattern"])) for r in db.q("SELECT * FROM update_exclusions ORDER BY id")]
    return _excl["data"]


def excluded_by(name, publisher, wid, rules=None):
    """First matching exception or None."""
    vals = {"name": name or "", "publisher": publisher or "", "winget_id": wid or ""}
    for r in (exclusions() if rules is None else rules):
        v = vals.get(r["field"], "")
        if v and r["_rx"].match(v):
            return r
    return None


# --- Matching -------------------------------------------------------------------
def overrides(reload=False):
    if reload or _overrides["data"] is None:
        _overrides["data"] = {r["name"].lower(): r for r in db.q("SELECT * FROM winget_overrides")}
    return _overrides["data"]


def package(wid):
    k = _idx["by_id"].get((wid or "").lower())
    return _idx["pkgs"].get(k) if k is not None else None


_VARIANTS = {"esr", "beta", "nightly", "dev", "developer", "canary", "insider", "insiders", "preview", "lts", "portable",
             "ptb", "enterprise", "extended", "unstable", "alpha", "rc"}
_LOCALE = re.compile(r"^[a-z]{2}(-[a-z]{2,4})?$")


def _variants(text):
    return {t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t in _VARIANTS}


def _locale_of(display):
    """Language from the display name, e.g. "Mozilla Firefox ESR (x64 de)" → "de"."""
    for grp in re.findall(r"\(([^)]*)\)", display or ""):
        for t in re.split(r"[\s,]+", grp.lower()):
            if t and _LOCALE.match(t) and t not in ("x64", "x86") and not _ARCH.match(t):
                return t
    return None


def _id_locale(pid):
    last = pid.split(".")[-1].lower()
    return last if _LOCALE.match(last) and len(pid.split(".")) > 2 else None


def _score(p, name, pub, via_pc):
    want = _variants(name)
    have = _variants(p["id"].replace(".", " ")) | _variants(p["name"])
    sc = 4 if via_pc else 0
    sc -= 5 * len(want ^ have)
    loc, iloc = _locale_of(name), _id_locale(p["id"])
    if iloc:
        sc += 2 if loc and (iloc == loc or iloc.split("-")[0] == loc.split("-")[0]) else -3
    if pub and p["pub"] and (p["pub"] in pub or pub in p["pub"]):
        sc += 2
    return sc


def match(name, publisher, key, msix=False):
    """Returns (winget_id, latest, comparison version, safe) or None.
    msix (2.41.2): MSIX apps only via their package family name - a name match would compare e.g. the
    context-menu package "Notepad++" 1.0.0.0 with Notepad++ 8.x and report it as outdated."""
    ov = overrides().get((name or "").lower())
    if ov:
        if ov["ignore"]:
            return None
        p = package(ov["winget_id"])
        return (ov["winget_id"], p["latest"] if p else None, (p["arp"] or p["latest"]) if p else None, True)
    if not _idx["loaded"]:
        return None
    by_pc = _idx["by_pc"].get(key.lower().strip("{}"), set()) if key else set()
    cands = set(by_pc) if msix else set(by_pc) | set(_idx["by_name"].get(norm(name), set()))
    if not cands:
        return None
    pkgs = _idx["pkgs"]
    pub = norm_pub(publisher)
    ranked = sorted(cands, key=lambda k: (-_score(pkgs[k], name, pub, k in by_pc), len(pkgs[k]["id"]), pkgs[k]["id"]))
    best = pkgs[ranked[0]]
    s0 = _score(best, name, pub, ranked[0] in by_pc)
    s1 = _score(pkgs[ranked[1]], name, pub, ranked[1] in by_pc) if len(ranked) > 1 else -99
    # "safe": product code match, single candidate or clear gap - and no variant deviation (ESR, Beta ...)
    sure = s0 > -5 and (ranked[0] in by_pc or len(ranked) == 1 or s0 - s1 >= 2)
    return (best["id"], best["latest"], best["arp"] or best["latest"], sure)


def _apply(rows):
    memo, upd = {}, []
    rules = exclusions()
    for r in rows:
        k = (r["name"], r["publisher"], r["product_code"], r.get("hive") == "MSIX")
        if k not in memo:
            m = match(*k)
            ex = 1 if rules and excluded_by(r["name"], r["publisher"], m[0] if m else None, rules) else 0
            memo[k] = (m, ex)
        m, ex = memo[k]
        if m:
            # Excluded: winget assignment stays visible, but never counts as outdated
            out = 0 if ex else (1 if util.is_outdated(r["version"], m[2]) else 0)
            upd.append((m[0], m[1], out, 1 if m[3] else 0, ex, r["id"]))
        else:
            upd.append((None, None, 0, 0, ex, r["id"]))
    with db.tx():
        db.exmany("UPDATE software SET winget_id=?, winget_latest=?, outdated=?, winget_sure=?, excluded=? WHERE id=?", upd)
    return len(upd)


def match_computer(cid):
    with _lock:
        return _apply(db.q("SELECT id, name, publisher, product_code, version, hive FROM software WHERE computer_id=?", (cid,)))


def rematch_all():
    overrides(reload=True)
    exclusions(reload=True)
    with _lock:
        return _apply(db.q("SELECT id, name, publisher, product_code, version, hive FROM software"))


def search(q, limit=50):
    q = (q or "").strip().lower()
    if not q or not _idx["loaded"]:
        return []
    res = []
    for p in _idx["pkgs"].values():
        i, n = p["id"].lower(), (p["name"] or "").lower()
        if q in i or q in n:
            score = 0 if i == q else 1 if i.startswith(q) or n.startswith(q) else 2
            res.append((score, len(i), p))
    res.sort(key=lambda x: (x[0], x[1]))
    return [{"id": p["id"], "name": p["name"], "latest": p["latest"]} for _, _, p in res[:limit]]
