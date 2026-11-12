"""Inventory via PowerShell over WinRM - split into standard scanners (modules)
that can be freely combined with own scanners in scan profiles."""
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import db, pklog, remote, util

_PS_FILE = os.path.join(os.path.dirname(__file__), "scan_modules.ps1")


def _load_modules():
    with open(_PS_FILE, "r", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    prelude, modules, order = [], {}, []
    cur = None  # None = head, "" = prelude, otherwise module key
    for line in lines:
        m = re.match(r"^# @module (\w+) \| (.+)$", line)
        if line.strip() == "# @prelude":
            cur = ""
            continue
        if m:
            cur = m.group(1)
            order.append(cur)
            modules[cur] = [m.group(2).strip(), []]
            continue
        if cur == "":
            prelude.append(line)
        elif cur:
            modules[cur][1].append(line)
    return "\n".join(prelude), {k: (v[0], "\n".join(v[1])) for k, v in modules.items()}, order


PRELUDE, MODULES, MODULE_ORDER = _load_modules()

SCAN_END = r"""
# Clean-up: leftovers of aborted deployments (restart of POCKET KNIFE, connection loss) - older than 2 days
try {
  $pkOld = (Get-Date).AddDays(-2)
  Get-ScheduledTask -TaskPath '\POCKETKNIFE\' -ErrorAction SilentlyContinue | Where-Object {
    $i = $_ | Get-ScheduledTaskInfo -ErrorAction SilentlyContinue; $_.State -ne 'Running' -and $i -and $i.LastRunTime -lt $pkOld } |
    Unregister-ScheduledTask -Confirm:$false -ErrorAction SilentlyContinue
  # Remove tasks with the service account's stored password immediately if they are not running (older versions up to 2.13;
  # 2.39 service account tasks left over after an interrupted run)
  Get-ScheduledTask -TaskPath '\POCKETKNIFE\' -ErrorAction SilentlyContinue | Where-Object {
    $_.State -ne 'Running' -and "$($_.Principal.LogonType)" -eq 'Password' } |
    Unregister-ScheduledTask -Confirm:$false -ErrorAction SilentlyContinue
  $pkBase = Join-Path $env:windir 'Temp\PocketKnife'
  Get-ChildItem -LiteralPath $pkBase -Directory -Force -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -lt $pkOld } |
    ForEach-Object { & cmd.exe /c rmdir /s /q "$($_.FullName)" 2>$null }
} catch {}
$r.extra = $e
'PKJSON:' + ($r | ConvertTo-Json -Depth 6 -Compress)
"""

# JSON categories (inventory_data) per standard scanner
MODULE_INV = {
    "cpu": ["cpus"], "memory": ["memory"], "disk_drives": ["physical_disks", "volumes"], "displays": ["displays"],
    "environment": ["environment"], "hardware": ["devices"], "local_groups": ["local_groups"],
    "local_users": ["local_users"], "nics": ["nics"], "printers": ["printers"], "processes": ["processes"],
    "product_keys": ["product_keys"], "shares": ["shares"], "features": ["features"], "tasks": ["tasks"],
}
CATEGORIES = [c for cats in MODULE_INV.values() for c in cats]
# Extra values (computers.extra) that a module sets completely anew
MODULE_EXTRA = {
    "computer": ["winget", "pending_reboot", "timezone", "ps_version", "chassis", "chassis_name", "bios_manufacturer",
                 "asset_tag", "family", "product_version", "sku", "uuid", "domain", "os_serial", "system_drive",
                 "product_type", "locale", "bios_mode", "secure_boot", "smb1_server", "smb1_client", "ie_version",
                 "domain_controller", "dotnet", "last_wu_install", "user_states", "virtual",
                 "os_edition", "os_install_type", "office_c2r",  # 2.40: update status
                 "os_esu",   # 2.43: Windows 10 ESU year (0 = none)
                 "ps_language",   # 2.44.4: PowerShell language mode on the client
                 "board", "entra"],  # 2.45: mainboard, Entra ID join and MDM enrollment
    "security": ["defender", "bitlocker", "tpm", "firewall", "antivirus"],
    "memory": ["memory_slots", "memory_max"],
    "nics": ["fast_startup"],  # 2.31: Wake-on-LAN readiness
}

CHASSIS = {1: "Sonstiges", 2: "Unbekannt", 3: "Desktop", 4: "Low-Profile-Desktop", 5: "Pizza-Box", 6: "Mini-Tower",
           7: "Tower", 8: "Tragbar", 9: "Laptop", 10: "Notebook", 11: "Handheld", 12: "Dockingstation",
           13: "All-in-One", 14: "Sub-Notebook", 15: "Platzsparend", 16: "Lunch-Box", 17: "Hauptgehäuse",
           18: "Erweiterungsgehäuse", 21: "Peripheriegehäuse", 23: "Rack", 24: "Sealed-Case-PC", 28: "Blade",
           30: "Tablet", 31: "Convertible", 32: "Detachable", 33: "IoT-Gateway", 34: "Embedded-PC", 35: "Mini-PC",
           36: "Stick-PC"}


def module_list():
    return [{"key": k, "label": MODULES[k][0]} for k in MODULE_ORDER]


def build_script(modules=None, custom_ids=None):
    """Prelude + selected standard scanners + own scanners. None = all."""
    from . import scanners
    mods = [m for m in MODULE_ORDER if modules is None or m in modules]
    return "\n".join([PRELUDE] + [MODULES[m][1] for m in mods] + [scanners.script(custom_ids), SCAN_END])


# 2.40.1: MSIX packages that come with Windows (calculator, photos, Xbox, Store, codecs ...) - hidden by default,
# otherwise every computer lists dozens of entries nobody manages. Matched against the package family name.
MSIX_BUILTIN = re.compile(
    r"^(windows\.|microsoftwindows\.|microsoft\.windows(?!terminal)|microsoft\.xbox|microsoft\.bing|microsoft\.zune|"
    r"microsoft\.microsoftedge|microsoft\.edge\.|microsoft\.gaming|microsoft\.gethelp|microsoft\.getstarted|"
    r"microsoft\.people|microsoft\.microsoftsolitairecollection|microsoft\.microsoftstickynotes|microsoft\.paint|"
    r"microsoft\.mspaint|microsoft\.screensketch|microsoft\.yourphone|microsoft\.storepurchaseapp|"
    r"microsoft\.microsoftofficehub|microsoft\.office\.onenote|microsoft\.todos|microsoft\.powerautomatedesktop|"
    r"microsoft\.sechealthui|microsoft\.(heif|vp9|webmedia|webp|raw|av1|hevc|mpeg2)[a-z]*extension|"
    r"microsoft\.copilot|microsoft\.outlookforwindows|microsoft\.549981c3f5f10|microsoft\.wallet|"
    r"microsoft\.mixedreality|microsoft\.microsoft3dviewer|microsoft\.messaging|microsoft\.oneconnect|"
    r"microsoft\.print3d|microsoft\.skypeapp|microsoft\.applicationcompatibilityenhancements|"
    r"microsoft\.desktopappinstaller|microsoft\.languageexperiencepack|microsoft\.ink\.|"
    r"microsoft\.widgetsplatformruntime|microsoft\.startexperiencesapp|microsoft\.microsoftjournal|"
    r"microsoft\.family|microsoftcorporationii\.|clipchamp\.|microsoft\.devhome|microsoft\.aad\.|"
    r"microsoft\.accountscontrol|microsoft\.lockapp|microsoft\.ecapp|microsoft\.creddialoghost|"
    r"microsoft\.asynctextservice|microsoft\.bioenrollment|ncsiuwpapp|"
    r"[^_]*_cw5n1h2txyewy$)", re.I)


def msix_builtin(pfn):
    return bool(MSIX_BUILTIN.match((pfn or "").strip()))


def _L(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def _s(v, n=1000):
    if v is None:
        return None
    v = str(v).strip()
    return v[:n] if v else None


# --- Queue --------------------------------------------------------------------------
_pool = None
_pool_lock = threading.Lock()
_scanning = {}  # computer_id -> profile name


def pool():
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=max(1, db.setting("scan_concurrency")), thread_name_prefix="scan")
        return _pool


def is_scanning(cid):
    return cid in _scanning


def scanning_ids():
    return list(_scanning)


_follow = {}  # 2.32: computer_id → [(profile, user)] - scans that are next after the running scan


def queue_scan(cid, user=None, profile_id=None, follow=False):
    """Queue a scan with a scan profile (without a value: default profile).
    follow=True (scan after a deployment, 2.32): if a scan is currently running for the computer, it is
    repeated afterwards instead of discarded - the running one may still have read the state before the installation."""
    from . import profiles
    prof = (profiles.get(profile_id) if profile_id else None) or profiles.default()
    with _pool_lock:
        if cid in _scanning:
            if follow:
                q = _follow.setdefault(cid, [])
                if prof["id"] not in [p["id"] for p, _u in q]:
                    q.append((prof, user))
                return True
            return False
        _scanning[cid] = prof["name"]
    pool().submit(_scan_safe, cid, user, prof)
    return True


def _release(cid):
    """Scan for cid finished: start a scan that is to be caught up (2.32), otherwise release the computer."""
    with _pool_lock:
        q = _follow.get(cid)
        if q:
            prof, user = q.pop(0)
            if not q:
                _follow.pop(cid, None)
            _scanning[cid] = prof["name"]
        else:
            _scanning.pop(cid, None)
            return
    pool().submit(_scan_safe, cid, user, prof)


def scan_now(cid, user=None, prof=None, wait_s=600):
    """Scan immediately in the calling thread (e.g. step "Scannen" in a package). Returns the status."""
    from . import profiles
    prof = prof or profiles.default()
    end = time.time() + wait_s
    while True:
        with _pool_lock:
            if cid not in _scanning:
                _scanning[cid] = prof["name"]
                break
        if time.time() > end:
            return "Es läuft bereits ein anderer Scan"
        time.sleep(3)
    try:
        scan(cid, user, prof)
    finally:
        _release(cid)
    st = db.val("SELECT status FROM profile_scans WHERE computer_id=? AND profile_id=?", (cid, prof["id"]))
    if st == "OK":
        return "OK"
    err = db.val("SELECT error FROM scan_history WHERE computer_id=? ORDER BY id DESC LIMIT 1", (cid,)) or \
        db.val("SELECT scan_error FROM computers WHERE id=?", (cid,))
    return f"{st or 'Fehler'}: {err or 'unbekannt'}"


def _scan_safe(cid, user, prof):
    try:
        scan(cid, user, prof)
    except Exception as e:  # pragma: no cover
        pklog.error("scan", f"Unerwarteter Fehler beim Scan: {e}", computer_id=cid)
    finally:
        _release(cid)


def scan(cid, user=None, prof=None):
    from . import profiles
    prof = prof or profiles.default()
    modules, custom_ids = prof["modules"], profiles.custom_ids(prof)
    ps_modules = [m for m in modules if m not in ("active_directory", "disk_usage")]
    do_disk = "disk_usage" in modules
    c = db.q1("SELECT * FROM computers WHERE id=?", (cid,))
    if not c:
        return
    host = c["dns_name"] or c["name"]
    now = util.now()
    db.ex("UPDATE computers SET last_scan_attempt=?, scan_status='Läuft' WHERE id=?", (now, cid))
    ad_warn = None
    if "active_directory" in modules:
        try:
            from . import ad
            ad.refresh_computer(cid)
        except Exception as e:
            ad_warn = f"Active Directory: {e}"
    if not ps_modules and not custom_ids and not do_disk:
        # Only the scanner "Active Directory" - no access to the client needed
        st = "Fehler" if ad_warn else "OK"
        db.ex("INSERT INTO scan_history(computer_id, ts, status, duration_ms, error, user, warnings, profile_id, "
              "profile_name) VALUES(?,?,?,?,?,?,?,?,?)", (cid, now, st, 0, ad_warn, user or "Zeitplan", None, prof["id"],
                                                         prof["name"]))
        db.ex("UPDATE computers SET scan_status=? WHERE id=?", ("OK" if not ad_warn else c["scan_status"], cid))
        profiles.record(cid, prof["id"], st, now)
        return
    from . import presence
    online, winrm = presence.probe(c, now)  # never scan a foreign device under an outdated address
    if presence.update(cid, online, winrm, None, now):
        # Computer is reachable again for the first time through this scan: do not miss the heartbeat trigger
        presence.notify([cid], background=True)
    if not winrm:
        prob = db.q1("SELECT addr_problem, addr_detail FROM computers WHERE id=?", (cid,)) or {}
        msg = prob.get("addr_detail") if prob.get("addr_problem") else (
            "Computer nicht erreichbar" if not online else "WinRM nicht erreichbar (Port geschlossen/Dienst aus)")
        db.ex("UPDATE computers SET scan_status='Fehler', scan_error=? WHERE id=?", (msg, cid))
        profiles.record(cid, prof["id"], "Offline", now)
        return
    t0 = time.time()
    status, error, warnings = "Fehler", None, None
    try:
        warn_list = []
        if ps_modules or custom_ids:
            out, err, _ = remote.ps(host, build_script(ps_modules, custom_ids), timeout=1200)
            idx = out.find("PKJSON:")
            if idx < 0:
                raise remote.RemoteError("Keine Inventardaten erhalten. " + err[:500])
            raw_json = util.first_line(out[idx + 7:], "{}")
            data = json.loads(raw_json)
            note_scan_size(cid, c["name"], data, len(raw_json))
            warn_list = [str(x) for x in _L(data.get("errors"))]
            if data.get("software_error"):
                warn_list.insert(0, "Software: " + str(data["software_error"]))
            save(cid, data, ps_modules)
            if "computer" in modules:
                _display_name(cid, (data.get("system") or {}).get("user"))
            if "applications" in modules:
                from . import winget
                winget.match_computer(cid)
            if "computer" in modules or "applications" in modules:
                _update_status(cid)
        if do_disk:
            from . import diskusage
            warn_list += diskusage.scan_fixed(cid, user=user or "Zeitplan", host=host)
        if ad_warn:
            warn_list.insert(0, ad_warn)
        warnings = "; ".join(warn_list)[:4000] or None
        db.ex("UPDATE computers SET scan_status='OK', scan_error=NULL, last_scan=? WHERE id=?", (util.now(), cid))
        status = "OK"
        pklog.info("scan", f"Scan von {c['name']} mit Profil „{prof['name']}“ erfolgreich ({time.time() - t0:.0f} s).",
                   user=user, computer_id=cid)
    except Exception as e:
        error = str(e)[:1000]
        db.ex("UPDATE computers SET scan_status='Fehler', scan_error=? WHERE id=?", (error, cid))
        pklog.warn("scan", f"Scan von {c['name']} (Profil „{prof['name']}“) fehlgeschlagen: {e}", user=user,
                   computer_id=cid)
    finally:
        db.ex("INSERT INTO scan_history(computer_id, ts, status, duration_ms, error, user, warnings, profile_id, "
              "profile_name) VALUES(?,?,?,?,?,?,?,?,?)",
              (cid, now, status, int((time.time() - t0) * 1000), error, user or "Zeitplan", warnings, prof["id"],
               prof["name"]))
        db.ex("DELETE FROM scan_history WHERE computer_id=? AND id NOT IN "
              "(SELECT id FROM scan_history WHERE computer_id=? ORDER BY id DESC LIMIT 200)", (cid, cid))
        profiles.record(cid, prof["id"], status, now)


def _update_status(cid):
    """2.40: update status after a scan; a Windows version/Office product not known yet gets its collection right away."""
    from . import updstatus
    try:
        before = {r["name"] for r in db.q("SELECT name FROM collections WHERE builtin=1")}
        res = updstatus.evaluate(cid) or {}
        w = res.get("windows") or {}
        names = ({f"{w.get('family')} {w.get('version')}"} if w and not w.get("server") and w.get("version") else set()) \
            | {o["product"] for o in res.get("office_rows") or []}
        if names - before:
            updstatus.sync_tree()
    except Exception as e:  # noqa: BLE001 - the scan itself stays successful
        pklog.warn("scan", f"Update-Status nicht berechnet: {e}", computer_id=cid)


_dn_cache = {}


def _display_name(cid, user):
    """Display name of the logged-on user from the AD (cached)."""
    user = (user or "").split(",")[0].strip()
    if not user or "\\" not in user:
        db.ex("UPDATE computers SET extra=json_remove(COALESCE(extra,'{}'), '$.user_display_name') WHERE id=?", (cid,))
        return
    sam = user.split("\\", 1)[1]
    hit = _dn_cache.get(sam.lower())
    if not hit or time.time() - hit[1] > 6 * 3600:
        try:
            from . import ad
            hit = (ad.display_name(sam), time.time())
            _dn_cache[sam.lower()] = hit
        except Exception:
            return
    if hit[0]:
        db.ex("UPDATE computers SET extra=json_set(COALESCE(extra,'{}'), '$.user_display_name', ?) WHERE id=?", (hit[0], cid))


def _merge_extra(cid, data, modules):
    """Replace the extra values of the scanned modules, keep all others."""
    raw = db.val("SELECT extra FROM computers WHERE id=?", (cid,))
    try:
        ex = json.loads(raw) if raw else {}
    except ValueError:
        ex = {}
    for mod, keys in MODULE_EXTRA.items():
        if mod in modules:
            for k in keys:
                ex.pop(k, None)
    new = dict(data.get("extra") or {})
    # 2.44.2: never store a PowerShell object instead of the extras (registry key: PSProvider alone was 33 MB) -
    # every query over computers reads this column
    clean_extra(ex, cid)
    clean_extra(new, cid)
    for k in ("firewall", "antivirus", "bitlocker"):
        if k in new:
            # 2.33.4: omit empty entries ("{}" from an empty PowerShell output)
            new[k] = [x for x in _L(new[k]) if not (isinstance(x, dict) and not x)]
    if "computer" in modules:
        try:
            new["chassis_name"] = CHASSIS.get(int(new.get("chassis") or 0), "")
        except (TypeError, ValueError):
            new["chassis_name"] = ""
    ex.update(new)
    return ex


# Properties of a PowerShell provider item (registry key, file) - never extras of POCKET KNIFE
PS_OBJECT_KEYS = {"PSPath", "PSParentPath", "PSChildName", "PSDrive", "PSProvider", "PSIsContainer",
                  "SubKeyCount", "ValueCount", "Property", "Handle", "View", "Name"}
EXTRA_VALUE_MAX = 256 * 1024   # one extras value larger than this is dropped (normal: a few KB in total)


def clean_extra(d, cid=None):
    """2.44.2: removes PowerShell object properties and oversized values from an extras dict (in place); returns the
    names of the removed keys."""
    gone = [k for k in list(d) if k in PS_OBJECT_KEYS]
    for k in list(d):
        if k in gone:
            continue
        try:
            if len(json.dumps(d[k], ensure_ascii=False)) > EXTRA_VALUE_MAX:
                gone.append(k)
        except (TypeError, ValueError):
            gone.append(k)
    for k in gone:
        d.pop(k, None)
    big = [k for k in gone if k not in PS_OBJECT_KEYS]
    if big and cid:
        pklog.warn("scan", f"Zusatzdaten zu groß und verworfen: {', '.join(big)[:200]}", computer_id=cid)
    return gone


def repair_extras():
    """2.44.2, once at start: extras that 2.41.3 overwrote with a registry object (tens of MB per computer, every
    computer query slow) are cleaned up. Returns the number of repaired computers."""
    n = 0
    for r in db.q("SELECT id, name FROM computers WHERE length(extra) > ? OR instr(extra, '\"PSProvider\"') > 0", (EXTRA_VALUE_MAX,)):
        raw = db.val("SELECT extra FROM computers WHERE id=?", (r["id"],))
        try:
            d = json.loads(raw or "{}")
        except ValueError:
            d = {}
        if not isinstance(d, dict):
            d = {}
        before = len(raw or "")
        gone = clean_extra(d)
        db.ex("UPDATE computers SET extra=? WHERE id=?", (json.dumps(d, ensure_ascii=False), r["id"]))
        n += 1
        pklog.info("system", f"Zusatzdaten von {r['name']} bereinigt: {before // 1024} KB → {len(json.dumps(d)) // 1024} KB "
                             f"({', '.join(sorted(gone))[:200]}). Der nächste Scan füllt fehlende Werte wieder.", computer_id=r["id"])
    return n


# 2.44.4: size of the scan results per area since start (diagnostic package) - a part that suddenly becomes huge
# (like the extras overwritten in 2.41.3) shows up here before everything gets slow
_scan_sizes: dict = {}
_scan_sizes_lock = threading.Lock()
SCAN_PART_WARN = 5 * 1024 * 1024


def note_scan_size(cid, name, data, total):
    parts = {"_gesamt": total}
    if isinstance(data, dict):
        for k, v in data.items():
            try:
                parts[k] = len(json.dumps(v, ensure_ascii=False, default=str))
            except (TypeError, ValueError):
                continue
    with _scan_sizes_lock:
        for k, n in parts.items():
            d = _scan_sizes.setdefault(k, {"n": 0, "total": 0, "max": 0, "max_computer": None})
            d["n"] += 1
            d["total"] += n
            if n > d["max"]:
                d["max"], d["max_computer"] = n, name
    big = [k for k, n in parts.items() if k != "_gesamt" and n > SCAN_PART_WARN]
    if big:
        pklog.warn("scan", f"Ungewöhnlich große Scan-Ergebnisse: {', '.join(f'{k} {parts[k] // 1048576} MB' for k in big)}",
                   computer_id=cid)


def scan_sizes():
    """{area: {n, avg_kb, max_kb, max_computer}} sorted by the largest value."""
    with _scan_sizes_lock:
        items = [(k, dict(v)) for k, v in _scan_sizes.items()]
    items.sort(key=lambda x: -x[1]["max"])
    return {k: {"n": v["n"], "avg_kb": round(v["total"] / v["n"] / 1024, 1) if v["n"] else 0,
                "max_kb": round(v["max"] / 1024, 1), "max_computer": v["max_computer"]} for k, v in items}


def _inv(cid, cat, rows, now):
    db.ex("INSERT INTO inventory_data(computer_id, category, json, updated) VALUES(?,?,?,?) "
          "ON CONFLICT(computer_id, category) DO UPDATE SET json=excluded.json, updated=excluded.updated",
          (cid, cat, json.dumps(_L(rows), ensure_ascii=False), now))


def save(cid, data, modules=None):
    """Saves only the areas that the scan profile actually scanned."""
    modules = set(MODULE_ORDER if modules is None else modules)
    now = util.now()
    # Was there already an application scan? (old full scans without a profile count)
    old_sw = db.q("SELECT name, version FROM software WHERE computer_id=?", (cid,)) if "applications" in modules else []
    # Was there already an application scan? Existing software always counts (the scan history is trimmed after 14 days)
    had_apps = bool(old_sw) or db.val("SELECT 1 FROM scan_history h LEFT JOIN scan_profiles p ON p.id=h.profile_id "
                                      "WHERE h.computer_id=? AND h.status='OK' AND (h.profile_id IS NULL "
                                      "OR p.modules LIKE '%\"applications\"%') LIMIT 1", (cid,))
    if "applications" in modules and data.get("software_error"):
        n_new = len([x for x in _L(data.get("software")) if (x or {}).get("name")])
        if n_new == 0 or n_new < len(old_sw) * 0.5:
            # Software part of the scan failed (e.g. registry not readable): keep the previous list
            # instead of logging everything as "removed" and losing the winget assignment
            modules = modules - {"applications"}
            pklog.warn("scan", f"Softwareliste nicht aktualisiert (Scanfehler: {str(data['software_error'])[:200]}).", computer_id=cid)
    with db.tx():
        db.ex("UPDATE computers SET extra=? WHERE id=?",
              (json.dumps(_merge_extra(cid, data, modules), ensure_ascii=False), cid))
        if "computer" in modules:
            s = data.get("system") or {}
            db.ex("""UPDATE computers SET manufacturer=?, model=?, serial=?, bios_version=?, bios_date=?, cpu=?, cores=?,
                     threads=?, ram_bytes=?, os_name=?, os_version=?, os_build=?, os_display_version=?, os_arch=?,
                     os_install_date=?, last_boot=?, current_user=? WHERE id=?""",
                  (_s(s.get("manufacturer")), _s(s.get("model")), _s(s.get("serial")), _s(s.get("bios")),
                   _s(s.get("bios_date")), _s(s.get("cpu")), s.get("cores"), s.get("threads"), s.get("ram"),
                   _s(s.get("os_name")), _s(s.get("os_version")), _s(s.get("os_build")), _s(s.get("display_version")),
                   _s(s.get("os_arch")), _s(s.get("install_date")), _s(s.get("last_boot")), _s(s.get("user")), cid))
        if "nics" in modules:
            nets = _L(data.get("network"))
            db.ex("UPDATE computers SET ip_addresses=?, mac_addresses=? WHERE id=?",
                  (", ".join(n.get("ip") for n in nets if n.get("ip")),
                   ", ".join(n.get("mac") for n in nets if n.get("mac")), cid))
            db.ex("DELETE FROM network WHERE computer_id=?", (cid,))
            db.exmany("INSERT INTO network(computer_id, description, mac, ip, gateway, dns, dhcp) VALUES(?,?,?,?,?,?,?)",
                      [(cid, _s(n.get("description")), _s(n.get("mac")), _s(n.get("ip")), _s(n.get("gateway")),
                        _s(n.get("dns")), 1 if n.get("dhcp") else 0) for n in nets])
        if "disk_drives" in modules:
            db.ex("DELETE FROM disks WHERE computer_id=?", (cid,))
            db.exmany("INSERT INTO disks(computer_id, drive, label, fs, size, free) VALUES(?,?,?,?,?,?)",
                      [(cid, _s(d.get("drive")), _s(d.get("label")), _s(d.get("fs")), d.get("size"), d.get("free"))
                       for d in _L(data.get("disks"))])
        if "hotfixes" in modules:
            db.ex("DELETE FROM hotfixes WHERE computer_id=?", (cid,))
            db.exmany("INSERT INTO hotfixes(computer_id, hotfix_id, description, installed_on, installed_by) "
                      "VALUES(?,?,?,?,?)",
                      [(cid, _s(h.get("id")), _s(h.get("description")), _s(h.get("installed_on")), _s(h.get("by")))
                       for h in _L(data.get("hotfixes")) if h.get("id")])
        if "services" in modules:
            db.ex("DELETE FROM services WHERE computer_id=?", (cid,))
            db.exmany("INSERT INTO services(computer_id, name, display_name, state, start_mode, account, description, "
                      "path) VALUES(?,?,?,?,?,?,?,?)",
                      [(cid, _s(v.get("name")), _s(v.get("display")), _s(v.get("state")), _s(v.get("start")),
                        _s(v.get("account")), _s(v.get("description"), 2000), _s(v.get("path"), 2000))
                       for v in _L(data.get("services"))])
        if "local_groups" in modules:
            db.ex("DELETE FROM local_admins WHERE computer_id=?", (cid,))
            db.exmany("INSERT INTO local_admins(computer_id, name, type) VALUES(?,?,?)",
                      [(cid, _s(a.get("name")), _s(a.get("type"))) for a in _L(data.get("admins")) if a.get("name")])
        for mod, cats in MODULE_INV.items():
            if mod in modules:
                for cat in cats:
                    _inv(cid, cat, data.get(cat), now)
        for key, rows in (data.get("custom") or {}).items():
            _inv(cid, f"custom:{key}", rows, now)
        if "applications" in modules:
            _save_software(cid, data, old_sw if had_apps else None, now)


def _save_software(cid, data, old_sw, now):
    db.ex("DELETE FROM software WHERE computer_id=?", (cid,))
    seen, sw_rows = set(), []
    hide_builtin = bool(db.setting("msix_hide_builtin"))
    for x in _L(data.get("software")):
        name = _s(x.get("name"), 400)
        if not name:
            continue
        if x.get("hive") == "MSIX" and hide_builtin and msix_builtin(x.get("key")):
            continue
        key = (name.lower(), (x.get("version") or "").lower(), x.get("arch"), x.get("scope"))
        if key in seen:
            continue
        seen.add(key)
        sw_rows.append((cid, name, _s(x.get("version"), 100), _s(x.get("publisher"), 300),
                        _s(x.get("install_date"), 30), _s(x.get("key"), 300), _s(x.get("arch")),
                        _s(x.get("scope")), _s(x.get("uninstall"), 2000), _s(x.get("quiet"), 2000),
                        _s(x.get("hive"), 40), _s(x.get("reg_path"), 600), _s(x.get("location"), 600),
                        x.get("size_kb") if isinstance(x.get("size_kb"), int) else None, _s(x.get("modified"), 30),
                        _s(x.get("modify"), 1000), 1 if x.get("no_remove") else 0))
    db.exmany("""INSERT INTO software(computer_id, name, version, publisher, install_date, product_code, arch,
                 scope, uninstall_string, quiet_uninstall, hive, reg_path, install_location, size_kb, modified,
                 modify_path, no_remove) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", sw_rows)
    if old_sw is None:  # first application scan: do not log changes
        return
    om, nm = {}, {}
    for r in old_sw:
        om.setdefault(r["name"], set()).add(r["version"] or "")
    for r in sw_rows:
        nm.setdefault(r[1], set()).add(r[2] or "")
    changes = []
    for n in nm:
        if n not in om:
            changes.append((cid, now, "installiert", n, None, ", ".join(sorted(nm[n]))))
        elif nm[n] != om[n]:
            changes.append((cid, now, "aktualisiert", n, ", ".join(sorted(om[n])), ", ".join(sorted(nm[n]))))
    for n in om:
        if n not in nm:
            changes.append((cid, now, "entfernt", n, ", ".join(sorted(om[n])), None))
    db.exmany("INSERT INTO software_changes(computer_id, ts, change, name, old_version, new_version) "
              "VALUES(?,?,?,?,?,?)", changes)
