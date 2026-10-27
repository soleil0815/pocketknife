"""Inventarisierung per PowerShell über WinRM – aufgeteilt in Standard-Scanner (Module),
die in Scanprofilen frei mit eigenen Scannern kombiniert werden."""
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
    cur = None  # None = Kopf, "" = Prelude, sonst Modulschlüssel
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
# Aufräumen: Reste abgebrochener Bereitstellungen (Neustart von POCKET KNIFE, Verbindungsabbruch) – älter als 2 Tage
try {
  $pkOld = (Get-Date).AddDays(-2)
  Get-ScheduledTask -TaskPath '\POCKETKNIFE\' -ErrorAction SilentlyContinue | Where-Object {
    $i = $_ | Get-ScheduledTaskInfo -ErrorAction SilentlyContinue; $_.State -ne 'Running' -and $i -and $i.LastRunTime -lt $pkOld } |
    Unregister-ScheduledTask -Confirm:$false -ErrorAction SilentlyContinue
  # Aufgaben älterer Versionen (bis 2.13) mit gespeichertem Kennwort des Dienstkontos sofort entfernen, wenn sie nicht laufen
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

# JSON-Kategorien (inventory_data) je Standard-Scanner
MODULE_INV = {
    "cpu": ["cpus"], "memory": ["memory"], "disk_drives": ["physical_disks", "volumes"], "displays": ["displays"],
    "environment": ["environment"], "hardware": ["devices"], "local_groups": ["local_groups"],
    "local_users": ["local_users"], "nics": ["nics"], "printers": ["printers"], "processes": ["processes"],
    "product_keys": ["product_keys"], "shares": ["shares"], "features": ["features"], "tasks": ["tasks"],
}
CATEGORIES = [c for cats in MODULE_INV.values() for c in cats]
# Zusatzwerte (computers.extra), die ein Modul vollständig neu setzt
MODULE_EXTRA = {
    "computer": ["winget", "pending_reboot", "timezone", "ps_version", "chassis", "chassis_name", "bios_manufacturer",
                 "asset_tag", "family", "product_version", "sku", "uuid", "domain", "os_serial", "system_drive",
                 "product_type", "locale", "bios_mode", "secure_boot", "smb1_server", "smb1_client", "ie_version",
                 "domain_controller", "dotnet", "last_wu_install"],
    "security": ["defender", "bitlocker", "tpm", "firewall", "antivirus"],
    "memory": ["memory_slots", "memory_max"],
    "nics": ["fast_startup"],  # 2.31: Wake-on-LAN-Bereitschaft
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
    """Prelude + gewählte Standard-Scanner + eigene Scanner. None = alle."""
    from . import scanners
    mods = [m for m in MODULE_ORDER if modules is None or m in modules]
    return "\n".join([PRELUDE] + [MODULES[m][1] for m in mods] + [scanners.script(custom_ids), SCAN_END])


def _L(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def _s(v, n=1000):
    if v is None:
        return None
    v = str(v).strip()
    return v[:n] if v else None


# --- Warteschlange ------------------------------------------------------------------
_pool = None
_pool_lock = threading.Lock()
_scanning = {}  # computer_id -> Profilname


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


def queue_scan(cid, user=None, profile_id=None):
    """Scan mit Scanprofil einreihen (ohne Angabe: Standardprofil)."""
    from . import profiles
    prof = (profiles.get(profile_id) if profile_id else None) or profiles.default()
    with _pool_lock:
        if cid in _scanning:
            return False
        _scanning[cid] = prof["name"]
    pool().submit(_scan_safe, cid, user, prof)
    return True


def scan_now(cid, user=None, prof=None, wait_s=600):
    """Scan sofort im aufrufenden Thread (z. B. Schritt „Scannen“ in einem Paket). Gibt den Status zurück."""
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
        _scanning.pop(cid, None)
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
        _scanning.pop(cid, None)


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
        # Nur der Scanner „Active Directory“ – kein Zugriff auf den Client nötig
        st = "Fehler" if ad_warn else "OK"
        db.ex("INSERT INTO scan_history(computer_id, ts, status, duration_ms, error, user, warnings, profile_id, "
              "profile_name) VALUES(?,?,?,?,?,?,?,?,?)", (cid, now, st, 0, ad_warn, user or "Zeitplan", None, prof["id"],
                                                         prof["name"]))
        db.ex("UPDATE computers SET scan_status=? WHERE id=?", ("OK" if not ad_warn else c["scan_status"], cid))
        profiles.record(cid, prof["id"], st, now)
        return
    from . import presence
    online, winrm = presence.probe(c, now)  # nie auf einem fremden Gerät unter einer veralteten Adresse scannen
    if presence.update(cid, online, winrm, None, now):
        # Computer ist durch diesen Scan erstmals wieder erreichbar: Heartbeat-Auslöser nicht verpassen
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
            data = json.loads(util.first_line(out[idx + 7:], "{}"))
            warn_list = [str(x) for x in _L(data.get("errors"))]
            if data.get("software_error"):
                warn_list.insert(0, "Software: " + str(data["software_error"]))
            save(cid, data, ps_modules)
            if "computer" in modules:
                _display_name(cid, (data.get("system") or {}).get("user"))
            if "applications" in modules:
                from . import winget
                winget.match_computer(cid)
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


_dn_cache = {}


def _display_name(cid, user):
    """Anzeigename des angemeldeten Benutzers aus dem AD (zwischengespeichert)."""
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
    """Zusatzwerte der gescannten Module ersetzen, alle anderen behalten."""
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
    for k in ("firewall", "antivirus", "bitlocker"):
        if k in new:
            new[k] = _L(new[k])
    if "computer" in modules:
        try:
            new["chassis_name"] = CHASSIS.get(int(new.get("chassis") or 0), "")
        except (TypeError, ValueError):
            new["chassis_name"] = ""
    ex.update(new)
    return ex


def _inv(cid, cat, rows, now):
    db.ex("INSERT INTO inventory_data(computer_id, category, json, updated) VALUES(?,?,?,?) "
          "ON CONFLICT(computer_id, category) DO UPDATE SET json=excluded.json, updated=excluded.updated",
          (cid, cat, json.dumps(_L(rows), ensure_ascii=False), now))


def save(cid, data, modules=None):
    """Speichert nur die Bereiche, die das Scanprofil tatsächlich gescannt hat."""
    modules = set(MODULE_ORDER if modules is None else modules)
    now = util.now()
    # Gab es schon einen Anwendungs-Scan? (alte Vollscans ohne Profil zählen mit)
    old_sw = db.q("SELECT name, version FROM software WHERE computer_id=?", (cid,)) if "applications" in modules else []
    # Gab es schon einen Anwendungs-Scan? Vorhandene Software zählt immer (der Scanverlauf wird nach 14 Tagen gekürzt)
    had_apps = bool(old_sw) or db.val("SELECT 1 FROM scan_history h LEFT JOIN scan_profiles p ON p.id=h.profile_id "
                                      "WHERE h.computer_id=? AND h.status='OK' AND (h.profile_id IS NULL "
                                      "OR p.modules LIKE '%\"applications\"%') LIMIT 1", (cid,))
    if "applications" in modules and data.get("software_error"):
        n_new = len([x for x in _L(data.get("software")) if (x or {}).get("name")])
        if n_new == 0 or n_new < len(old_sw) * 0.5:
            # Software-Teil des Scans fehlgeschlagen (z. B. Registry nicht lesbar): bisherige Liste behalten,
            # statt alles als „entfernt“ zu protokollieren und die winget-Zuordnung zu verlieren
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
    for x in _L(data.get("software")):
        name = _s(x.get("name"), 400)
        if not name:
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
    if old_sw is None:  # erster Anwendungs-Scan: keine Änderungen protokollieren
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
