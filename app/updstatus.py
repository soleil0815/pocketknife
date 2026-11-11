"""2.40: Update status for Windows and Office - per computer and as ready-made collections ("Windows Updates",
"Microsoft Office") that fill themselves from the inventory.

Principle (wish of the owner): no fixed version or channel. Every installation is compared with the latest state of
ITS OWN Windows build or Office channel:

- Windows: build + UBR from the scan (e.g. 26100.7171). Reference per build:
    * Microsoft (MSRC security update guide, CVRF): the patched build of every patch day ("FixedBuild"), fetched by
      the server once a day - optional, nothing is sent from the browser or about the computers;
    * the environment itself: the highest UBR seen on own computers, from the day it was first seen.
  A newer UBR only counts after the grace period (default 7 days after its release or first sighting) - until then
  computers that still have the previous one are "aktuell".
- Office Click-to-Run (Microsoft 365 Apps, LTSC 2024/2021, 2024/2021 retail, Visio/Project): version and update channel
  from the registry; reference = highest version of the same channel in the environment (plus grace period).
  MSI Office (2016/2019 volume licence and older) from the software list.
- Support end: Microsoft lifecycle dates (table below) - "ohne Support" always wins.

Statuses: "aktuell", "veraltet", "ohne Support", "unbekannt" (no data yet) - Office per installation, the computer
gets the worst one ("kein Office" if there is none).
"""
import datetime
import json
import re
import threading
import urllib.request

from . import db, pklog, util

STATUSES = ("aktuell", "veraltet", "ohne Support", "unbekannt")
RANK = {"ohne Support": 3, "veraltet": 2, "unbekannt": 1, "aktuell": 0}
MSRC_UPDATES = "https://api.msrc.microsoft.com/cvrf/v3.0/updates"
FIXED_RX = re.compile(r"^\s*10\.0\.(\d{4,6})\.(\d{1,6})\s*$")
MAX_DOC = 80 * 1024 * 1024
_lock = threading.Lock()

# --- Microsoft lifecycle (end of support; verify the newest entries once a year) ---------------------------------
# Windows 11 / 10 feature updates: (Home/Pro/Pro Education/Pro for Workstations, Enterprise/Education/IoT Enterprise)
WIN_CLIENT_END = {
    ("Windows 11", "21H2"): ("2023-10-10", "2024-10-08"),
    ("Windows 11", "22H2"): ("2024-10-08", "2025-10-14"),
    ("Windows 11", "23H2"): ("2025-11-11", "2026-11-10"),
    ("Windows 11", "24H2"): ("2026-10-13", "2027-10-12"),
    ("Windows 11", "25H2"): ("2027-10-12", "2028-10-10"),
    ("Windows 10", "22H2"): ("2025-10-14", "2025-10-14"),
}
WIN10_LAST = "2025-10-14"          # every Windows 10 feature update (non-LTSC) ended at the latest with 22H2
# LTSC/LTSB (EditionID EnterpriseS/IoTEnterpriseS) by build: (Enterprise LTSC, IoT Enterprise LTSC)
LTSC_END = {10240: ("2025-10-14", "2025-10-14"), 14393: ("2026-10-13", "2026-10-13"), 17763: ("2029-01-09", "2029-01-09"),
            19044: ("2027-01-12", "2032-01-13"), 26100: ("2029-10-09", "2034-10-10")}
SERVER_END = {"2008": "2020-01-14", "2012": "2023-10-10", "2016": "2027-01-12", "2019": "2029-01-09",
              "2022": "2031-10-14", "2025": "2034-10-10"}
OFFICE_END = {"2007": "2017-10-10", "2010": "2020-10-13", "2013": "2023-04-11", "2016": "2025-10-14", "2019": "2025-10-14",
              "2021": "2026-10-13", "2024": "2029-10-09"}

# Office Click-to-Run update channels (GUID in UpdateChannel/CDNBaseUrl)
CHANNELS = {
    "492350f6-3a01-4f97-b9c0-c7c6ddf67d60": "Current Channel",
    "64256afe-f5d9-4f86-8936-8840a6a4f5be": "Current Channel (Preview)",
    "55336b82-a18d-4dd6-b5f6-9e5095c314a6": "Monthly Enterprise Channel",
    "7ffbc6bf-bc32-4f92-8982-f9dd17fd3114": "Semi-Annual Enterprise Channel",
    "b8f9b850-328d-4355-9145-c59439a0c4cf": "Semi-Annual Enterprise Channel (Preview)",
    "5440fd1f-7ecb-4221-8110-145efaa6372f": "Beta Channel",
    "f2e724c1-748f-4b47-8fb8-8e0d210e9208": "Office 2019 Perpetual (PerpetualVL2019)",
    "5030841d-c919-4594-8d2d-84ae4f96e58e": "Office LTSC 2021 (PerpetualVL2021)",
    "7983bac0-e531-40cf-be00-fd24fe66619c": "Office LTSC 2024 (PerpetualVL2024)",
}
GUID_RX = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
MSI_OFFICE_RX = re.compile(r"^Microsoft (Office|Visio|Project)\b(?!.*(Proof|MUI|Language|Sprach|Shared|Components|Komponenten|"
                           r"Click-to-Run|Klick-und-Los|Viewer|Web Components|Live Meeting|Interop|Runtime|Access database engine))"
                           r".*?\b(20(?:07|10|13|16|19))\b", re.I)
C2R_NAME_RX = re.compile(r" - [a-z]{2}-[a-z]{2,4}$", re.I)   # "Microsoft 365 Apps for enterprise - de-de" (Click-to-Run)

# Known versions: the ready-made collections show them even without computers ("(0)"); newer ones come by themselves
KNOWN_WIN = {"Windows 11": ["21H2", "22H2", "23H2", "24H2", "25H2", "26H2"], "Windows 10": ["22H2"]}
KNOWN_SERVER = ["2016", "2019", "2022", "2025"]
KNOWN_OFFICE = ["Microsoft 365 Apps", "Office LTSC 2024", "Office 2024", "Office LTSC 2021", "Office 2021", "Office 2019",
                "Office 2016"]


def _today():
    return datetime.date.today().isoformat()


def grace_days():
    return int(db.setting("update_grace_days") or 0)


# --- What the computer runs ----------------------------------------------------------------------------------------
def _extra(c):
    try:
        return json.loads(c.get("extra") or "{}") or {}
    except (TypeError, ValueError):
        return {}


def windows_info(c):
    """{family, version, build, ubr, server, ltsc, enterprise, edition} or None (no Windows data yet)."""
    name = str(c.get("os_name") or "")
    m = re.match(r"^(\d+)(?:\.(\d+))?", str(c.get("os_build") or ""))
    if not name or not m:
        return None
    x = _extra(c)
    edition = str(x.get("os_edition") or "")
    pt = str(x.get("product_type") or "")
    server = "Server" in name or pt in ("2", "3")
    if server:
        sm = re.search(r"Server (\d{4})", name)
        family = f"Windows Server {sm.group(1)}" if sm else "Windows Server"
    else:
        fm = re.search(r"Windows (11|10|8\.1|8|7)\b", name)
        family = f"Windows {fm.group(1)}" if fm else "Windows"
    return {"family": family, "version": str(c.get("os_display_version") or "").upper(), "build": int(m.group(1)),
            "ubr": int(m.group(2)) if m.group(2) else None, "server": server, "edition": edition,
            "ltsc": bool(re.search(r"(?i)enterprises(n|eval)?$", edition)),  # EnterpriseS, IoTEnterpriseS, EnterpriseSN
            "enterprise": bool(re.match(r"(?i)(enterprise|education|iotenterprise|servers?)", edition)) or not edition}


def windows_end(w):
    """End of support (ISO date) or None (unknown = no statement)."""
    fam = w["family"]
    if w["server"]:
        y = fam.rsplit(" ", 1)[-1]
        return SERVER_END.get(y)
    if fam in ("Windows 7", "Windows 8", "Windows 8.1"):
        return "2023-01-10"
    if w["ltsc"]:
        e = LTSC_END.get(w["build"])
        return (e[1] if "iot" in w["edition"].lower() else e[0]) if e else None
    e = WIN_CLIENT_END.get((fam, w["version"]))
    if e:
        return e[1] if w["enterprise"] else e[0]
    if fam == "Windows 10":
        return WIN10_LAST
    return None


def _office_product(rid):
    """ProductReleaseId (e.g. ProPlus2024Volume, O365ProPlusRetail, VisioPro2021Volume) -> (name, year or None)."""
    r = rid.strip()
    low = r.lower()
    if not r:
        return None, None
    y = re.search(r"(20(?:16|19|21|24))", r)
    year = y.group(1) if y else None
    if low.startswith(("o365", "m365")):
        return "Microsoft 365 Apps", None
    app = "Visio" if low.startswith("visio") else "Project" if low.startswith("project") else "Office"
    if not year:
        return (f"{app} (Microsoft 365)" if app != "Office" else "Microsoft 365 Apps"), None
    if app != "Office":
        return f"{app} {year}", year
    ltsc = "volume" in low and year in ("2021", "2024")
    return (f"Office LTSC {year}" if ltsc else f"Office {year}"), year


def office_installs(c, software=None):
    """Office installations of a computer: [{product, version, channel, kind, year}] - Click-to-Run from the scan,
    MSI from the software list."""
    out = []
    c2r = _extra(c).get("office_c2r") or {}
    if isinstance(c2r, dict) and c2r.get("version"):
        g = GUID_RX.search(str(c2r.get("channel") or ""))
        ch = CHANNELS.get(g.group(0).lower(), "Kanal " + g.group(0).lower()) if g else "unbekannter Kanal"
        seen = set()
        for rid in str(c2r.get("products") or "").split(","):
            name, year = _office_product(rid)
            if name and name not in seen:
                seen.add(name)
                out.append({"product": name, "version": str(c2r["version"]), "channel": ch, "kind": "Klick-und-Los", "year": year})
    if software is None:
        software = db.q("SELECT name, version FROM software WHERE computer_id=?", (c["id"],))
    have = {o["product"] for o in out}
    for s in software:
        n = str(s.get("name") or "")
        if C2R_NAME_RX.search(n):
            continue
        m = MSI_OFFICE_RX.match(n)
        if not m:
            continue
        app = {"office": "Office", "visio": "Visio", "project": "Project"}[m.group(1).lower()]
        prod = f"{app} {m.group(3)}"
        if prod in have:
            continue
        have.add(prod)
        out.append({"product": prod, "version": str(s.get("version") or ""), "channel": "MSI", "kind": "MSI", "year": m.group(3)})
    return out


def office_end(o):
    if o["product"].startswith("Microsoft 365") or o["product"].endswith("(Microsoft 365)"):
        return None
    return OFFICE_END.get(o.get("year") or "")


# --- References: what is current ----------------------------------------------------------------------------------
def _vkey(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v or "")))


def note_ref(kind, key, value, released=None, source="env"):
    """Remembers a state (e.g. kind "win", key "26100", value "7171") - first sighting/release date stays."""
    if key in (None, "") or value in (None, ""):
        return
    db.ex("INSERT OR IGNORE INTO update_refs(kind, key, value, released, source) VALUES(?,?,?,?,?)",
          (kind, str(key), str(value), released or _today(), source))
    if source != "env":
        # Microsoft's date is the real one - also for a value the environment had seen first
        db.ex("UPDATE update_refs SET released=MIN(released, ?), source=? WHERE kind=? AND key=? AND value=?",
              (released or _today(), source, kind, str(key), str(value)))


def target(kind, key, days=None):
    """(highest value released at least `days` ago, its row) - (None, None) if none; refs exist -> see has_refs."""
    days = grace_days() if days is None else days
    cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    rows = db.q("SELECT value, released, source FROM update_refs WHERE kind=? AND key=? AND released<=?", (kind, str(key), cutoff))
    if not rows:
        return None, None
    best = max(rows, key=lambda r: _vkey(r["value"]))
    return best["value"], best


def has_refs(kind, key):
    return bool(db.val("SELECT 1 FROM update_refs WHERE kind=? AND key=? LIMIT 1", (kind, str(key))))


def _compare(kind, key, have):
    if have in (None, ""):
        return "unbekannt", ""
    if not has_refs(kind, key):
        return "unbekannt", ""
    tgt, row = target(kind, key)
    if tgt is None or _vkey(have) >= _vkey(tgt):
        return "aktuell", tgt or ""
    return "veraltet", tgt


def evaluate(cid):
    """Status of one computer: computers.win_update/office_update and the Office table (inventory_data 'office')."""
    c = db.q1("SELECT * FROM computers WHERE id=?", (cid,))
    if not c:
        return None
    today = _today()
    w = windows_info(c)
    win, info = "unbekannt", ""
    if w:
        end = windows_end(w)
        if w["ubr"] is not None:
            note_ref("win", w["build"], w["ubr"])
        if end and end < today:
            win, info = "ohne Support", f"Support endete am {'.'.join(reversed(end.split('-')))}"
        else:
            win, tgt = _compare("win", w["build"], w["ubr"])
            if tgt:
                info = f"{w['build']}.{w['ubr']} – aktuell ist {w['build']}.{tgt}"
    rows = []
    for o in office_installs(c):
        end = office_end(o)
        if o["kind"] != "MSI":
            note_ref("office", o["channel"], o["version"])
        if end and end < today:
            st, tgt = "ohne Support", ""
        elif o["kind"] == "MSI":
            st, tgt = "unbekannt", ""   # MSI patches come as single KBs - no build to compare
        else:
            st, tgt = _compare("office", o["channel"], o["version"])
        rows.append({"product": o["product"], "version": o["version"], "channel": o["channel"], "kind": o["kind"],
                     "status": st, "current": tgt, "support_end": end or ""})
    office = max((r["status"] for r in rows), key=lambda s: RANK[s]) if rows else "kein Office"
    db.ex("UPDATE computers SET win_update=?, win_update_info=?, office_update=? WHERE id=?", (win, info, office, cid))
    db.ex("INSERT INTO inventory_data(computer_id, category, json, updated) VALUES(?,?,?,?) "
          "ON CONFLICT(computer_id, category) DO UPDATE SET json=excluded.json, updated=excluded.updated",
          (cid, "office", json.dumps(rows, ensure_ascii=False), util.now()))
    return {"win": win, "info": info, "office": office, "office_rows": rows, "windows": w}


def evaluate_all():
    ids = [r["id"] for r in db.q("SELECT id FROM computers")]
    for cid in ids:     # references from all computers first, then the status (independent of the order)
        c = db.q1("SELECT * FROM computers WHERE id=?", (cid,))
        w = windows_info(c)
        if w and w["ubr"] is not None:
            note_ref("win", w["build"], w["ubr"])
        for o in office_installs(c):
            if o["kind"] != "MSI":
                note_ref("office", o["channel"], o["version"])
    for cid in ids:
        evaluate(cid)
    try:
        from . import collections_
        collections_.invalidate()
    except Exception:  # noqa: BLE001
        pass
    return len(ids)


# --- Microsoft (MSRC CVRF): patched builds of the last patch days ---------------------------------------------------
def _get_json(url, timeout=120):
    from . import proxy
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "POCKET-KNIFE"})
    with proxy.urlopen(req, timeout=timeout) as r:
        data = r.read(MAX_DOC + 1)
    if len(data) > MAX_DOC:
        raise ValueError("Antwort zu groß")
    return json.loads(data.decode("utf-8-sig"))


def parse_cvrf(doc):
    """{build: (ubr, released)} from one CVRF document (Remediations[].FixedBuild like 10.0.26100.7171)."""
    rel = str(((doc.get("DocumentTracking") or {}).get("InitialReleaseDate") or ""))[:10] or _today()
    out: dict[int, tuple[int, str]] = {}
    for v in doc.get("Vulnerability") or []:
        for r in v.get("Remediations") or []:
            m = FIXED_RX.match(str(r.get("FixedBuild") or ""))
            if m:
                b, u = int(m.group(1)), int(m.group(2))
                if b not in out or u > out[b][0]:
                    out[b] = (u, rel)
    return out


def fetch_msrc(months=2, get=None):
    """Latest patched builds from Microsoft -> update_refs. Returns the number of builds (0 = nothing usable)."""
    get = get or _get_json
    idx = get(MSRC_UPDATES)
    docs = [d for d in (idx.get("value") or []) if d.get("CvrfUrl") and str(d.get("CvrfUrl")).startswith("https://api.msrc.microsoft.com/")]
    docs.sort(key=lambda d: str(d.get("InitialReleaseDate") or ""), reverse=True)
    builds: dict[int, int] = {}
    for d in docs[:months]:
        for b, (u, rel) in parse_cvrf(get(d["CvrfUrl"])).items():
            note_ref("win", b, u, rel, source="msrc")
            builds[b] = max(builds.get(b, 0), u)
    return len(builds)


def refresh(user="System"):
    """Daily: Microsoft data (if enabled), evaluate all computers, update the ready-made collections."""
    if not _lock.acquire(blocking=False):
        return {"busy": True}
    try:
        n_ms, err = 0, ""
        if db.setting("update_msrc"):
            try:
                n_ms = fetch_msrc()
            except Exception as e:  # noqa: BLE001 - offline/proxy: the environment's state still works
                from . import proxy
                err = proxy.safe_error(e) if hasattr(proxy, "safe_error") else str(e)
                pklog.warn("system", f"Update-Status: Microsoft-Daten nicht abrufbar ({err[:200]}) – Vergleich mit dem Stand "
                                     "der eigenen Umgebung.", user=user)
        n = evaluate_all()
        sync_tree()
        db.ex("INSERT OR REPLACE INTO settings(key, value) VALUES('update_status_at', ?)", (util.now(),))
        db.ex("INSERT OR REPLACE INTO settings(key, value) VALUES('update_status_msrc', ?)", (str(n_ms),))
        return {"computers": n, "msrc_builds": n_ms, "error": err}
    finally:
        _lock.release()


# --- Ready-made collections ----------------------------------------------------------------------------------------
def _cond(table, column, op, value):
    return {"type": "cond", "table": table, "column": column, "op": op, "value": value}


def _grp(*items, match="all"):
    return {"type": "group", "match": match, "items": list(items)}


def _node(parent, name, typ="folder", filters=None, sort=0):
    """Ready-made collection (builtin=1) - created once, filters kept up to date; returns its id."""
    row = db.q1("SELECT id, filters FROM collections WHERE builtin=1 AND name=? AND COALESCE(parent_id, 0)=?", (name, parent or 0))
    f = json.dumps(filters or {"type": "group", "match": "all", "items": []}, ensure_ascii=False)
    if row:
        if typ != "folder" and row["filters"] != f:
            db.ex("UPDATE collections SET filters=?, match='all' WHERE id=?", (f, row["id"]))
        return row["id"]
    return db.ex("INSERT INTO collections(name, parent_id, type, match, filters, builtin, sort) VALUES(?,?,?,?,?,1,?)",
                 (name, parent, typ, "all", f if typ != "folder" else None, sort))


def _status_children(parent, label_ok, label_old, table="computer", column="win_update", product=None):
    for i, (label, st) in enumerate(((label_ok, "aktuell"), (label_old, "veraltet"))):
        if product:
            f = _grp(_cond("office", "product", "equals", product), _cond("office", "status", "equals", st))
        else:
            f = _grp(_cond(table, column, "equals", st))
        _node(parent, label, "dynamic", f, i)


def sync_tree():
    """'Windows Updates' and 'Microsoft Office' with a node per version/product - known ones plus all in the inventory."""
    if not db.setting("update_tree"):
        return
    root = _node(None, "Windows Updates", sort=90)
    ws = _node(root, "Arbeitsstationen", sort=0)
    srv = _node(root, "Server", sort=1)
    seen: dict[str, set] = {}
    for c in db.q("SELECT os_name, os_display_version, os_build, extra FROM computers WHERE os_name IS NOT NULL"):
        w = windows_info(c)
        if w and not w["server"] and w["family"] in KNOWN_WIN and w["version"]:
            seen.setdefault(w["family"], set()).add(w["version"])
    for i, fam in enumerate(("Windows 11", "Windows 10")):
        fnode = _node(ws, fam, sort=i)
        for j, ver in enumerate(sorted(set(KNOWN_WIN[fam]) | seen.get(fam, set()))):
            vnode = _node(fnode, f"{fam} {ver}", "dynamic", _grp(
                _cond("computer", "role", "equals", "Arbeitsplatz"), _cond("computer", "os", "contains", fam),
                _cond("computer", "os_display_version", "equals", ver)), j)
            _status_children(vnode, "Aktuell", "Veraltet")
    years = set(KNOWN_SERVER)
    for c in db.q("SELECT os_name FROM computers WHERE os_name LIKE '%Server%'"):
        m = re.search(r"Server (\d{4})", c["os_name"] or "")
        if m:
            years.add(m.group(1))
    for j, y in enumerate(sorted(years)):
        snode = _node(srv, f"Windows Server {y}", "dynamic", _grp(_cond("computer", "os", "contains", f"Server {y}")), j)
        _status_children(snode, "Aktuell", "Veraltet")
    _node(root, "Ohne Support", "dynamic", _grp(_cond("computer", "win_update", "equals", "ohne Support")), 2)
    _node(root, "Neustart ausstehend", "dynamic", _grp(_cond("computer", "pending_reboot", "is_true", "")), 3)
    oroot = _node(None, "Microsoft Office", sort=91)
    prods = set(KNOWN_OFFICE)
    for r in db.q("SELECT json FROM inventory_data WHERE category='office'"):
        try:
            prods.update(x.get("product") for x in json.loads(r["json"] or "[]") if x.get("product"))
        except ValueError:
            pass
    for j, p in enumerate(sorted(prods, key=lambda x: (not x.startswith("Microsoft 365"), x))):
        pnode = _node(oroot, p, "dynamic", _grp(_cond("office", "product", "equals", p)), j)
        _status_children(pnode, "Aktuell", "Veraltet", product=p)
    _node(oroot, "Office ohne Support", "dynamic", _grp(_cond("office", "status", "equals", "ohne Support")), 900)
    _node(oroot, "Kein Office installiert", "dynamic", _grp(_cond("computer", "office_update", "equals", "kein Office")), 901)
    try:
        from . import collections_
        collections_.invalidate()
    except Exception:  # noqa: BLE001
        pass
