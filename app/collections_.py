"""Collections (static / dynamic) with filters.

A dynamic filter is a tree of groups and conditions:

    {"type": "group", "match": "all|any|not_all|none", "items": [
        {"type": "cond", "table": "software", "column": "name", "op": "contains", "value": "Firefox"},
        {"type": "group", ...}
    ]}

- all      = all conditions match (AND)
- any      = at least one matches (OR)
- not_all  = not all match (NOT AND)
- none     = none matches (NOT OR)   -> e.g. "computers without Firefox"

Conditions on the same table within an AND group refer to the same row
(application "Firefox" AND version < 130 = the same application).
"""
import json
import re
import time

from . import db, util

# ---------------------------------------------------------------------------
# Operators per data type
# ---------------------------------------------------------------------------
OPS = {
    "text": ["contains", "not_contains", "equals", "not_equals", "starts", "ends", "wildcard", "regex", "empty", "not_empty"],
    "version": ["v_lt", "v_lte", "v_eq", "v_gte", "v_gt", "contains", "equals", "starts", "empty", "not_empty"],
    "number": ["eq", "ne", "gt", "gte", "lt", "lte", "empty", "not_empty"],
    "date": ["within_days", "older_days", "before", "after", "empty", "not_empty"],
    "bool": ["is_true", "is_false"],
    "collection": ["in", "not_in"],
    "bytes": ["gte", "lte", "gt", "lt", "eq", "ne", "empty", "not_empty"],
    "any": ["contains", "not_contains", "equals", "not_equals", "starts", "ends", "wildcard", "regex",
            "v_lt", "v_lte", "v_eq", "v_gte", "v_gt", "gt", "gte", "lt", "lte", "empty", "not_empty"],
}
OP_LABELS = {
    "contains": "enthält", "not_contains": "enthält nicht", "equals": "ist gleich", "not_equals": "ist ungleich",
    "starts": "beginnt mit", "ends": "endet mit", "wildcard": "entspricht Muster (* ?)", "regex": "entspricht RegEx",
    "empty": "ist leer", "not_empty": "ist nicht leer",
    "v_lt": "Version kleiner als", "v_lte": "Version kleiner/gleich", "v_eq": "Version gleich",
    "v_gte": "Version größer/gleich", "v_gt": "Version größer als",
    "eq": "=", "ne": "≠", "gt": ">", "gte": "≥", "lt": "<", "lte": "≤",
    "within_days": "innerhalb der letzten … Tage", "older_days": "älter als … Tage", "before": "vor dem", "after": "nach dem",
    "is_true": "ja", "is_false": "nein", "in": "ist Mitglied von", "not_in": "ist nicht Mitglied von",
}
NO_VALUE = {"empty", "not_empty", "is_true", "is_false"}
MATCH_LABELS = {"all": "Alle", "any": "Eine", "not_all": "Nicht alle", "none": "Keine"}


def _x(key):
    return f"json_extract(c.extra, '$.{key}')"


def _jb(expr):
    return f"(CASE WHEN {expr} IN (1, 'True', 'true', 'Ja') THEN 1 ELSE 0 END)"


# ---------------------------------------------------------------------------
# Tables and columns
# ---------------------------------------------------------------------------
COMPUTER_COLS = [
    ("name", "Name", "text", "c.name"),
    ("dns_name", "Hostname", "text", "c.dns_name"),
    ("description", "Beschreibung", "text", "c.description"),
    ("os", "Betriebssystem", "text", "COALESCE(c.os_name, c.ad_os)"),
    ("os_display_version", "OS-Release (z. B. 24H2)", "text", "c.os_display_version"),
    ("os_build", "OS-Build", "version", "c.os_build"),
    ("os_arch", "Architektur", "text", "c.os_arch"),
    ("os_install_date", "OS installiert", "date", "c.os_install_date"),
    # 2.40: update status (updstatus.py) - compared with the latest state of the computer's own Windows build
    ("win_update", "Windows-Update-Status (aktuell/veraltet/ohne Support/unbekannt)", "text", "c.win_update"),
    ("os_edition", "Windows-Edition (z. B. Professional, Enterprise, EnterpriseS = LTSC)", "text", _x("os_edition")),
    ("office_update", "Office-Update-Status (aktuell/veraltet/ohne Support/unbekannt/kein Office)", "text", "c.office_update"),
    ("ou", "Distinguished Name / OU", "text", "c.dn"),
    ("location", "AD-Standort", "text", "c.ad_location"),
    ("domain", "Domäne", "text", _x("domain")),
    ("manufacturer", "Hersteller", "text", "c.manufacturer"),
    ("model", "Modell", "text", "c.model"),
    ("serial", "Seriennummer", "text", "c.serial"),
    ("chassis", "Gehäuse", "text", _x("chassis_name")),
    # 2.34: role from the scan (Win32_OperatingSystem.ProductType) and virtualization (Hyper-V, VMware, KVM/QEMU …)
    ("role", "Rolle (Arbeitsplatz/Server/Domänencontroller)", "text",
     f"CASE CAST({_x('product_type')} AS INTEGER) WHEN 1 THEN 'Arbeitsplatz' WHEN 2 THEN 'Domänencontroller' WHEN 3 THEN 'Server' ELSE NULL END"),
    ("virtual", "Virtualisierung", "text", _x("virtual")),
    ("family", "Familie", "text", _x("family")),
    ("sku", "SKU", "text", _x("sku")),
    ("cpu", "Prozessor", "text", "c.cpu"),
    ("cores", "CPU-Kerne", "number", "c.cores"),
    ("ram_gb", "Arbeitsspeicher (GB)", "number", "ROUND(c.ram_bytes / 1073741824.0, 1)"),
    ("free_c_gb", "Freier Speicher C: (GB)", "number",
     "(SELECT ROUND(d.free / 1073741824.0, 1) FROM disks d WHERE d.computer_id = c.id AND d.drive = 'C:')"),
    ("memory_modules", "Anzahl Speichermodule", "number",
     "(SELECT json_array_length(d.json) FROM inventory_data d WHERE d.computer_id = c.id AND d.category = 'memory')"),
    ("bios_version", "BIOS-Version", "text", "c.bios_version"),
    ("bios_manufacturer", "BIOS-Hersteller", "text", _x("bios_manufacturer")),
    ("bios_mode", "BIOS-Modus (UEFI/Legacy)", "text", _x("bios_mode")),
    ("secure_boot", "Secure Boot", "bool", f"COALESCE({_x('secure_boot')}, 0)"),
    ("tpm_version", "TPM-Version", "text", _x("tpm.version")),
    ("smb1_server", "SMBv1-Server aktiviert", "bool", f"COALESCE({_x('smb1_server')}, 0)"),
    ("smb1_client", "SMBv1-Client aktiviert", "bool", f"COALESCE({_x('smb1_client')}, 0)"),
    ("dotnet", ".NET-Versionen", "text", _x("dotnet")),
    ("ps_version", "PowerShell-Version", "version", _x("ps_version")),
    ("winget_version", "App-Installer (winget)", "version", _x("winget")),
    ("defender_enabled", "Defender aktiv", "bool", f"COALESCE({_x('defender.enabled')}, 0)"),
    ("defender_realtime", "Defender Echtzeitschutz", "bool", f"COALESCE({_x('defender.realtime')}, 0)"),
    ("signature_age", "Defender-Signaturalter (Tage)", "number", _x("defender.signature_age")),
    ("last_wu_install", "Letztes Windows-Update", "date", _x("last_wu_install")),
    ("pending_reboot", "Neustart erforderlich", "bool", f"COALESCE({_x('pending_reboot')}, 0)"),
    ("fast_startup", "Windows-Schnellstart aktiv", "bool", _x("fast_startup")),
    # 2.31: at least one wired adapter with Magic Packet "on" that may wake the computer, and no fast startup
    ("wol_ready", "Wake-on-LAN bereit", "bool",
     "(SELECT CASE WHEN COUNT(json_extract(j.value, '$.wol_magic')) = 0 THEN NULL WHEN SUM(CASE WHEN json_extract(j.value, '$.wol_magic') = 'an' "
     "AND COALESCE(json_extract(j.value, '$.wake_armed'), 1) = 1 AND COALESCE(json_extract(j.value, '$.wired'), 1) = 1 THEN 1 ELSE 0 END) > 0 "
     f"AND COALESCE({_x('fast_startup')}, 0) = 0 THEN 1 ELSE 0 END FROM inventory_data d, json_each(d.json) j "
     "WHERE d.computer_id = c.id AND d.category = 'nics')"),
    ("current_user", "Angemeldeter Benutzer", "text", "c.current_user"),
    ("ip", "IP-Adresse", "text", "c.ip_addresses"),
    ("mac", "MAC-Adresse", "text", "c.mac_addresses"),
    ("online", "Online", "bool", "c.online"),
    ("addr_problem", "Adressproblem (anderes Gerät, keine Domäne …)", "text", "c.addr_problem"),
    ("resolved_ip", "Aufgelöste Adresse (DNS)", "text", "c.resolved_ip"),
    ("ad_enabled", "Im AD aktiviert", "bool", "c.ad_enabled"),
    ("ad_present", "Im AD vorhanden", "bool", "c.ad_present"),
    ("ad_last_logon", "Letzte AD-Anmeldung", "date", "c.ad_last_logon"),
    ("ad_created", "Im AD erstellt", "date", "c.ad_created"),
    ("created_at", "Hinzugefügt", "date", "c.created_at"),
    ("last_boot", "Letzter Start", "date", "c.last_boot"),
    ("last_scan", "Letzter erfolgreicher Scan", "date", "c.last_scan"),
    ("scan_status", "Scanstatus (OK/Fehler)", "text", "c.scan_status"),
    # older names (for existing collections)
    ("days_since_scan", "Tage seit letztem Scan", "number",
     "CAST(julianday('now','localtime') - julianday(c.last_scan) AS INTEGER)"),
    ("days_since_boot", "Tage seit Neustart", "number",
     "CAST(julianday('now','localtime') - julianday(c.last_boot) AS INTEGER)"),
    ("days_since_logon", "Tage seit AD-Anmeldung", "number",
     "CAST(julianday('now','localtime') - julianday(c.ad_last_logon) AS INTEGER)"),
    ("collection", "Sammlung", "collection", None),
]

# Row tables: (key, label, FROM, join, columns)
ROW_TABLES = {
    "software": ("Anwendungen", "software s", "s.computer_id = c.id", [
        ("name", "Name", "text", "s.name"), ("version", "Version", "version", "s.version"),
        ("publisher", "Hersteller", "text", "s.publisher"), ("install_date", "Installiert (JJJJMMTT)", "text", "s.install_date"),
        ("arch", "Architektur", "text", "s.arch"), ("scope", "Bereich", "text", "s.scope"),
        ("winget_id", "winget-ID", "text", "s.winget_id"), ("winget_latest", "Aktuelle winget-Version", "version", "s.winget_latest"),
        ("outdated", "Veraltet", "bool", "s.outdated"),
        ("uninstall_string", "Deinstallationsbefehl", "text", "s.uninstall_string"),
        ("quiet_uninstall", "Stille Deinstallation", "text", "s.quiet_uninstall"),
        ("install_location", "Installationsort", "text", "s.install_location"),
        ("size_mb", "Größe (MB)", "number", "ROUND(s.size_kb / 1024.0, 1)"),
        ("hive", "Registry-Hive", "text", "s.hive"), ("reg_path", "Registry-Pfad", "text", "s.reg_path"),
        ("modified", "Installiert/geändert (Zeitpunkt)", "date", "s.modified"),
        ("product_code", "Produktcode / Schlüssel", "text", "s.product_code")]),
    "hotfixes": ("Hotfixes", "hotfixes h", "h.computer_id = c.id", [
        ("hotfix_id", "KB", "text", "h.hotfix_id"), ("description", "Art", "text", "h.description"),
        ("installed_on", "Installiert am", "date", "h.installed_on"), ("installed_by", "Installiert von", "text", "h.installed_by")]),
    "services": ("Dienste", "services v", "v.computer_id = c.id", [
        ("name", "Name", "text", "v.name"), ("display_name", "Anzeigename", "text", "v.display_name"),
        ("state", "Status (Running/Stopped)", "text", "v.state"), ("start_mode", "Starttyp (Auto/Manual/Disabled)", "text", "v.start_mode"),
        ("account", "Konto", "text", "v.account"), ("path", "Pfad", "text", "v.path")]),
    "admins": ("Lokale Administratoren", "local_admins a", "a.computer_id = c.id", [
        ("name", "Mitglied", "text", "a.name"), ("type", "Typ (User/Group)", "text", "a.type")]),
    "disks": ("Laufwerke", "disks k", "k.computer_id = c.id", [
        ("drive", "Laufwerk", "text", "k.drive"), ("label", "Bezeichnung", "text", "k.label"), ("fs", "Dateisystem", "text", "k.fs"),
        ("size_gb", "Größe (GB)", "number", "ROUND(k.size / 1073741824.0, 1)"),
        ("free_gb", "Frei (GB)", "number", "ROUND(k.free / 1073741824.0, 1)"),
        ("free_pct", "Frei (%)", "number", "ROUND(100.0 * k.free / NULLIF(k.size, 0), 1)")]),
    "deployments": ("Bereitstellungen", "deployment_targets t JOIN deployments dp ON dp.id = t.deployment_id",
                    "t.computer_id = c.id", [
        ("name", "Bereitstellung", "text", "dp.name"), ("package", "Paket", "text", "json_extract(dp.package_json, '$.name')"),
        ("winget_id", "winget-ID", "text", "json_extract(dp.package_json, '$.winget_id')"),
        ("status", "Status (success/failed/running)", "text", "t.status"), ("exit_code", "Exitcode", "number", "t.exit_code"),
        ("finished_at", "Beendet", "date", "t.finished_at")]),
    "changes": ("Softwareänderungen", "software_changes x", "x.computer_id = c.id", [
        ("name", "Software", "text", "x.name"), ("change", "Änderung (installiert/aktualisiert/entfernt)", "text", "x.change"),
        ("new_version", "Neue Version", "version", "x.new_version"), ("ts", "Zeitpunkt", "date", "x.ts")]),
}

# JSON tables from the scans: (label, [(column, label, type, expression_or_None)])
J = "j.value"
JSON_TABLES = {
    "ad_groups": ("Active-Directory-Gruppen", [("name", "Gruppe", "text"), ("dn", "Distinguished Name", "text")]),
    "cpus": ("CPU", [("name", "Name", "text"), ("manufacturer", "Hersteller", "text"), ("cores", "Kerne", "number"),
                     ("logical", "Threads", "number"), ("max_mhz", "Max. MHz", "number"), ("socket", "Sockel", "text"),
                     ("virtualization", "Virtualisierung", "bool")]),
    "disks_physical": ("Laufwerke (physisch)", [("model", "Modell", "text"), ("media", "Typ (SSD/HDD)", "text"),
                                                ("bus", "Bus (NVMe/SATA/USB)", "text"), ("health", "Zustand", "text"),
                                                ("size_gb", "Größe (GB)", "number", f"ROUND(CAST(json_extract({J}, '$.size') AS REAL) / 1073741824.0, 1)"),
                                                ("serial", "Seriennummer", "text"), ("firmware", "Firmware", "text")]),
    "displays": ("Bildschirme", [("manufacturer", "Hersteller", "text"), ("model", "Modell", "text"),
                                 ("serial", "Seriennummer", "text"), ("year", "Baujahr", "number")]),
    "environment": ("Umgebungsvariablen", [("name", "Variable", "text"), ("value", "Wert", "text")]),
    "devices": ("Hardware", [("name", "Gerät", "text"), ("class", "Klasse", "text"), ("manufacturer", "Hersteller", "text"),
                             ("status", "Status", "text"), ("problem", "Fehlercode (0 = OK)", "number")]),
    "local_groups": ("Lokale Gruppen", [("name", "Gruppe", "text"), ("members", "Mitglieder", "text"), ("count", "Anzahl Mitglieder", "number")]),
    "local_users": ("Lokale Benutzer", [("name", "Benutzer", "text"), ("full_name", "Name", "text"), ("enabled", "Aktiv", "bool"),
                                        ("last_logon", "Letzte Anmeldung", "date"), ("password_last_set", "Passwort gesetzt", "date"),
                                        ("description", "Beschreibung", "text")]),
    "memory": ("Speichermodule", [("slot", "Steckplatz", "text"),
                                  ("capacity_gb", "Größe (GB)", "number", f"ROUND(CAST(json_extract({J}, '$.capacity') AS REAL) / 1073741824.0, 1)"),
                                  ("speed", "MHz", "number"), ("manufacturer", "Hersteller", "text"), ("part", "Teilenummer", "text")]),
    "nics": ("Netzwerkkarten", [("name", "Adapter", "text"), ("connection", "Verbindung", "text"), ("status", "Status", "text"),
                                ("ip", "IP-Adresse", "text"), ("mac", "MAC", "text"), ("dhcp", "DHCP", "bool"),
                                ("dns", "DNS-Server", "text"), ("gateway", "Gateway", "text"),
                                ("speed_mbit", "Geschwindigkeit (Mbit/s)", "number", f"ROUND(CAST(json_extract({J}, '$.speed') AS REAL) / 1000000)"),
                                # 2.31: Wake-on-LAN readiness
                                ("wired", "Kabelgebunden", "bool"), ("wol_magic", "Wake-on-LAN: Magic Packet (an/aus)", "text"),
                                ("wake_armed", "Darf den Computer aufwecken", "bool"), ("wol_driver", "WoL-Einstellungen des Treibers", "text")]),
    "printers": ("Drucker (lokal)", [("name", "Drucker", "text"), ("driver", "Treiber", "text"), ("port", "Anschluss", "text"),
                                     ("default", "Standarddrucker", "bool"), ("network", "Netzwerkdrucker", "bool"), ("shared", "Freigegeben", "bool")]),
    "processes": ("Prozesse", [("name", "Prozess", "text"), ("user", "Benutzer", "text"), ("path", "Pfad", "text"),
                               ("company", "Firma", "text"),
                               ("memory_mb", "Arbeitsspeicher (MB)", "number", f"ROUND(CAST(json_extract({J}, '$.memory') AS REAL) / 1048576)")]),
    "product_keys": ("Produktschlüssel", [("product", "Produkt", "text"), ("channel", "Kanal", "text"), ("status", "Status", "text"),
                                          ("key", "Schlüssel", "text")]),
    "shares": ("Freigaben", [("name", "Freigabe", "text"), ("path", "Pfad", "text"), ("type", "Typ", "text"), ("admin", "Administrativ", "bool")]),
    "features": ("Windows-Features", [("name", "Name", "text"), ("caption", "Bezeichnung", "text"),
                                      ("state", "Status (Aktiviert/Deaktiviert)", "text"), ("kind", "Art", "text")]),
    # 2.40: Office installations with their update status (computed by updstatus.py)
    "office": ("Office-Installationen", [("product", "Produkt (z. B. Microsoft 365 Apps, Office LTSC 2024)", "text"),
                                         ("version", "Version", "version"), ("channel", "Update-Kanal", "text"),
                                         ("status", "Update-Status (aktuell/veraltet/ohne Support/unbekannt)", "text"),
                                         ("kind", "Art (Klick-und-Los/MSI)", "text"), ("support_end", "Support bis", "date")]),
    "tasks": ("Geplante Aufgaben", [("name", "Aufgabe", "text"), ("path", "Ordner", "text"), ("state", "Status", "text"),
                                    ("run_as", "Ausführen als", "text"), ("action", "Aktion", "text"),
                                    ("last_run", "Letzte Ausführung", "date"), ("last_result", "Ergebnis", "number"),
                                    ("microsoft", "Microsoft-Aufgabe", "bool")]),
}
JSON_CATEGORY = {"disks_physical": "physical_disks"}  # table name -> category in inventory_data

# order as in the list in the computer view
TABLE_ORDER = ["computer", "ad_groups", "software", "cpus", "custom_fields", "deployments", "disks", "disks_physical",
               "displays", "environment", "devices", "hotfixes", "local_groups", "local_users", "admins", "memory", "nics",
               "printers", "processes", "product_keys", "services", "shares", "changes", "features", "tasks", "office"]

MAX_SQL = 200_000  # characters – nested collection references are inlined completely
COL_RE = re.compile(r"^[A-Za-z0-9_ .\-]{1,80}$")


def _custom_fields():
    return [(f"cf_{f['id']}", f["name"], {"number": "number", "date": "date"}.get(f["type"], "text"),
             f"(SELECT v.value FROM custom_field_values v WHERE v.computer_id = c.id AND v.field_id = {int(f['id'])})")
            for f in db.q("SELECT id, name, type FROM custom_fields ORDER BY sort, name")]


SCANNER_LABELS = {"registry": "Registry", "files": "Dateien & Ordner", "wmi": "WMI", "powershell": "PowerShell"}


# Columns of custom scanners with byte values – same rule as in the UI (app.js: bytesColumns):
# the name suggests a size, all values are numbers and at least one is ≥ 1024; or set explicitly.
BYTES_NAME = re.compile(r"(size|bytes|length|capacity|größe|groesse|kapazit|speicher|space|^used|^free|frei$|belegt|volumen)", re.I)
BYTES_NOT = re.compile(r"(count|anzahl|percent|prozent|pct|%|ratio|quote|anteil|index|nummer|^id$|_id$|mhz|rpm|time|dauer|(kb|mb|gb|tb)$)", re.I)
_NUMERIC = re.compile(r"^\d+(\.\d+)?$")
_bytes_cache = {}
BYTES_TTL = 60


def _scanner_columns(sid):
    """[(column, type)] of a custom scanner; type "bytes" for sizes, otherwise "any"."""
    now = time.time()
    hit = _bytes_cache.get(sid)
    if hit and now - hit[0] < BYTES_TTL:
        return hit[1]
    cat = f"custom:{sid}"
    sc = db.q1("SELECT extra FROM custom_scanners WHERE id=?", (sid,))
    try:
        extra = json.loads((sc or {}).get("extra") or "{}")
    except ValueError:
        extra = {}
    cols = [r["k"] for r in db.q(
        """SELECT DISTINCT k.key AS k FROM inventory_data d, json_each(d.json) j, json_each(j.value) k
           WHERE d.category = ? LIMIT 60""", (cat,)) if COL_RE.match(r["k"] or "")]
    mode = extra.get("bytes_mode") or "auto"
    want = {x.strip().lower() for x in str(extra.get("bytes_cols") or "").split(",") if x.strip()}
    out = []
    for c in cols:
        typ = "any"
        if mode == "list":
            typ = "bytes" if c.lower() in want else "any"
        elif mode == "auto" and BYTES_NAME.search(c) and not BYTES_NOT.search(c):
            vals = [r["v"] for r in db.q(
                """SELECT json_extract(j.value, ?) v FROM inventory_data d, json_each(d.json) j
                   WHERE d.category = ? AND json_extract(j.value, ?) IS NOT NULL AND json_extract(j.value, ?) <> '' LIMIT 500""",
                (f'$."{c}"', cat, f'$."{c}"', f'$."{c}"'))]
            if vals and all(_NUMERIC.match(str(v).strip()) for v in vals) and any(float(v) >= 1024 for v in vals):
                typ = "bytes"
        out.append((c, typ))
    _bytes_cache[sid] = (now, out)
    return out


def invalidate_scanner(sid=None):
    if sid is None:
        _bytes_cache.clear()
    else:
        _bytes_cache.pop(sid, None)


def _scanner_tables():
    out = {}
    for sc in db.q("SELECT id, name, type FROM custom_scanners ORDER BY name"):
        label = SCANNER_LABELS.get(sc["type"], sc["type"])
        out[f"custom:{sc['id']}"] = (f"{label}: {sc['name']}", [(c, c, t) for c, t in _scanner_columns(sc["id"])])
    return out


def _table(table):
    """Returns (kind, label, from, link, {column: (label, type, expression, extra_params)}, category)."""
    if table == "computer":
        cols = {k: (l, t, e, []) for k, l, t, e in COMPUTER_COLS}
        cols.update({k: (l, t, e, []) for k, l, t, e in _custom_fields()})
        return ("computer", "Computer", None, None, cols, None)
    if table in ROW_TABLES:
        label, frm, link, cols = ROW_TABLES[table]
        return ("rows", label, frm, link, {k: (l, t, e, []) for k, l, t, e in cols}, None)
    if table in JSON_TABLES or table.startswith("custom:"):
        if table.startswith("custom:"):
            if not re.match(r"^custom:\d+$", table):
                raise ValueError("Ungültige Tabelle")
            sc = db.q1("SELECT name, type FROM custom_scanners WHERE id=?", (int(table.split(":")[1]),))
            label = (f"{SCANNER_LABELS.get(sc['type'], sc['type'])}: {sc['name']}" if sc else table)
            # Known columns with their type (sizes as "bytes"), others can be entered freely
            cols = {c: (c, t, None, []) for c, t in (_scanner_columns(int(table.split(":")[1])) if sc else [])}
            is_custom = True
        else:
            label, defs = JSON_TABLES[table]
            cols = {}
            for d in defs:
                k, l, t = d[0], d[1], d[2]
                if len(d) > 3:
                    cols[k] = (l, t, d[3], [])
                else:
                    cols[k] = (l, t, None, [])
            is_custom = False
        return ("json", label, "inventory_data d, json_each(d.json) j", "d.computer_id = c.id AND d.category = ?",
                cols, table if is_custom else JSON_CATEGORY.get(table, table))
    raise ValueError(f"Unbekannte Tabelle: {table}")


def _col(table, tdef, column):
    kind, _label, _f, _l, cols, _cat = tdef
    if column in cols:
        label, typ, expr, params = cols[column]
        if expr is None:  # simple JSON column
            return typ, f"json_extract({J}, ?)", [f'$."{column}"']
        return typ, expr, list(params)
    if kind == "json" and table.startswith("custom:") and COL_RE.match(column or ""):
        return "any", f"json_extract({J}, ?)", [f'$."{column}"']
    raise ValueError(f"Unbekannte Spalte {column} in {table}")


# ---------------------------------------------------------------------------
# SQL conditions
# ---------------------------------------------------------------------------
_UNITS = {"b": 1, "kb": 1024, "kib": 1024, "mb": 1024 ** 2, "mib": 1024 ** 2, "gb": 1024 ** 3, "gib": 1024 ** 3,
          "tb": 1024 ** 4, "tib": 1024 ** 4}


def _num(v):
    """Number; sizes such as "20 GB" or "1,5 TB" are converted to bytes (1 GB = 1024³ bytes)."""
    s = str(v if v is not None else "").strip().replace(" ", "")
    m = re.match(r"^(-?[\d.,]+)([kmgt]i?b|b)$", s, re.I)
    try:
        if m:
            return float(m.group(1).replace(",", ".")) * _UNITS[m.group(2).lower()]
        return float(s.replace(",", "."))
    except (TypeError, ValueError, KeyError):
        raise ValueError(f"„{v}“ ist keine Zahl")


def _cond(typ, op, expr, eparams, value):
    """(sql, params) for an expression; eparams belong to every occurrence of expr."""
    if typ == "bytes" and op not in OPS["bytes"] and op in OPS["any"]:
        typ = "any"  # condition saved before 2.12.1 (e.g. "enthält") – keep evaluating unchanged
    if op not in OPS.get(typ, []):
        raise ValueError(f"Operator „{OP_LABELS.get(op, op)}“ passt nicht zum Datentyp")
    E = f"({expr})"

    def out(sql, *vals):
        # Each placeholder {e} gets the expression parameters, followed by the values
        n = sql.count("{e}")
        params = []
        parts = sql.split("{e}")
        res = parts[0]
        for i in range(n):
            res += E + parts[i + 1]
        # assemble parameters in order of occurrence
        seq = []
        idx = 0
        for piece in re.split(r"(\{e\}|\?)", sql):
            if piece == "{e}":
                seq.extend(eparams)
            elif piece == "?":
                seq.append(vals[idx])
                idx += 1
        return res, seq

    v = "" if value is None else str(value)
    if op == "contains":
        return out("{e} LIKE ? ESCAPE '\\'", util.like_pattern(v, "contains"))
    if op == "not_contains":
        return out("({e} IS NULL OR {e} NOT LIKE ? ESCAPE '\\')", util.like_pattern(v, "contains"))
    if op == "equals":
        return out("{e} = ? COLLATE NOCASE", v)
    if op == "not_equals":
        return out("({e} IS NULL OR {e} <> ? COLLATE NOCASE)", v)
    if op == "starts":
        return out("{e} LIKE ? ESCAPE '\\'", util.like_pattern(v, "starts"))
    if op == "ends":
        return out("{e} LIKE ? ESCAPE '\\'", util.like_pattern(v, "ends"))
    if op == "wildcard":
        pat = v.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_").replace("*", "%").replace("?", "_")
        return out("{e} LIKE ? ESCAPE '\\'", pat)
    if op == "regex":
        why = util.regex_problem(v)
        if why:
            raise ValueError(f"Ungültiger regulärer Ausdruck: {why}")
        return out("pk_regex({e}, ?) = 1", v)
    if op == "empty":
        return out("({e} IS NULL OR {e} = '')")
    if op == "not_empty":
        return out("({e} IS NOT NULL AND {e} <> '')")
    if op in ("v_lt", "v_lte", "v_eq", "v_gte", "v_gt"):
        sym = {"v_lt": "< 0", "v_lte": "<= 0", "v_eq": "= 0", "v_gte": ">= 0", "v_gt": "> 0"}[op]
        return out("({e} IS NOT NULL AND {e} <> '' AND pk_vcmp({e}, ?) " + sym + ")", v)
    if op in ("eq", "ne", "gt", "gte", "lt", "lte"):
        sym = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[op]
        # Numeric columns (e.g. "Arbeitsspeicher (GB)"): "16 GB" means 16 – convert units only for size/free-form columns
        num = _num(re.sub(r"\s*[kmgt]i?b\s*$|\s*b\s*$", "", str(v), flags=re.I)) if typ == "number" else _num(v)
        # Empty and non-numeric values are not 0; treat a decimal comma ("2,5") as on the filter page
        return out("({e} IS NOT NULL AND CAST({e} AS TEXT) GLOB '*[0-9]*' AND "
                   "CAST(REPLACE(CAST({e} AS TEXT), ',', '.') AS REAL) " + sym + " ?)", num)
    if op == "within_days":
        return out("({e} IS NOT NULL AND julianday('now','localtime') - julianday({e}) <= ?)", _num(v))
    if op == "older_days":
        return out("({e} IS NOT NULL AND julianday('now','localtime') - julianday({e}) > ?)", _num(v))
    if op == "before":
        return out("({e} IS NOT NULL AND date({e}) < date(?))", v)
    if op == "after":
        return out("({e} IS NOT NULL AND date({e}) > date(?))", v)
    if op == "is_true":
        return out("COALESCE({e}, 0) = 1")
    if op == "is_false":
        return out("COALESCE({e}, 0) = 0")
    raise ValueError(f"Unbekannter Operator {op}")


def _row_cond(table, tdef, item):
    typ, expr, eparams = _col(table, tdef, item.get("column"))
    return _cond(typ, item.get("op"), expr, eparams, item.get("value"))


def _exists(table, items):
    tdef = _table(table)
    kind, _label, frm, link, _cols, cat = tdef
    sql, params = f"SELECT 1 FROM {frm} WHERE {link}", []
    if kind == "json":
        params.append(cat)
    for it in items:
        s, p = _row_cond(table, tdef, it)
        sql += f" AND {s}"
        params += p
    return f"EXISTS({sql})", params


# Two separate limits (up to 2.18 a shared counter – long reference chains with subgroups at the end failed):
GROUP_DEPTH_MAX = 10  # subgroups within ONE collection
HOPS_MAX = 12         # chained "ist Mitglied von" references or parent collections
# (check_cycle rejects circular references on save – the limits are only the safeguard behind it)


def _item(it, depth, hops=0):
    if it.get("type") == "group":
        return build_tree(it, depth, hops)
    table = it.get("table") or "computer"
    if table == "computer":
        tdef = _table("computer")
        col = it.get("column")
        if col == "collection":
            try:
                sub = int(it.get("value"))
            except (TypeError, ValueError):
                raise ValueError("Sammlung auswählen")
            w, p = where_for(sub, hops + 1)
            neg = it.get("op") == "not_in"
            return f"c.id {'NOT ' if neg else ''}IN (SELECT c.id FROM computers c WHERE {w})", p
        typ, expr, eparams = _col("computer", tdef, col)
        return _cond(typ, it.get("op"), expr, eparams, it.get("value"))
    return _exists(table, [it])


def build_tree(node, depth=0, hops=0):
    if depth > GROUP_DEPTH_MAX:
        raise ValueError(f"Filtergruppen sind zu tief verschachtelt (mehr als {GROUP_DEPTH_MAX} Ebenen in einer Sammlung).")
    match = node.get("match", "all")
    if match not in MATCH_LABELS:
        match = "all"
    parts = []
    items = [i for i in (node.get("items") or []) if isinstance(i, dict)]
    if match in ("all", "not_all"):
        buckets = {}
        order = []
        for it in items:
            t = it.get("table") or "computer"
            if it.get("type") != "group" and t != "computer":
                if t not in buckets:
                    buckets[t] = []
                    order.append(("rows", t))
                buckets[t].append(it)
            else:
                order.append(("item", it))
        for kind, x in order:
            parts.append(_exists(x, buckets[x]) if kind == "rows" else _item(x, depth + 1, hops))
        joiner = " AND "
    else:
        parts = [_item(it, depth + 1, hops) for it in items]
        joiner = " OR "
    if not parts:
        return "1=1", []
    sql = "(" + joiner.join(p[0] for p in parts) + ")"
    params = [x for p in parts for x in p[1]]
    if match in ("not_all", "none"):
        sql = "NOT " + sql
    return sql, params


# ---------------------------------------------------------------------------
# Migration of old filters (versions 1.0–1.5)
# ---------------------------------------------------------------------------
_LEGACY_LIST = {  # old field -> (table, column, extra condition)
    "software": ("software", "name", None), "software_publisher": ("software", "publisher", None),
    "hotfix": ("hotfixes", "hotfix_id", None), "local_admin": ("admins", "name", None),
    "service": ("services", "name", {"column": "state", "op": "equals", "value": "Running"}),
    "ad_group": ("ad_groups", "name", None), "printer": ("printers", "name", None), "process": ("processes", "name", None),
    "local_user": ("local_users", "name", None), "share": ("shares", "name", None), "task": ("tasks", "name", None),
    "device": ("devices", "name", None), "display": ("displays", "model", None),
    "feature_on": ("features", "name", {"column": "state", "op": "regex", "value": "^(Aktiviert|Installiert)$"}),
}
_LEGACY_OPS = {"gt": "gt", "lt": "lt", "gte": "gte", "lte": "lte", "equals": "eq", "not_equals": "ne"}


def _legacy_item(f):
    key, op, val = f.get("field"), f.get("op"), f.get("value", "")
    if key in _LEGACY_LIST:
        table, col, extra = _LEGACY_LIST[key]
        pos = {"not_contains": "contains", "not_equals": "equals"}.get(op, op)
        conds = [{"type": "cond", "table": table, "column": col, "op": pos, "value": val}]
        if extra:
            conds.append({"type": "cond", "table": table, **extra})
        grp = {"type": "group", "match": "all", "items": conds}
        if op in ("not_contains", "not_equals"):
            return {"type": "group", "match": "none", "items": [grp]}
        return grp if len(conds) > 1 else conds[0]
    if key == "software_outdated":
        conds = [{"type": "cond", "table": "software", "column": "outdated", "op": "is_true"}]
        if op in ("contains", "not_contains"):
            conds.append({"type": "cond", "table": "software", "column": "name", "op": "contains", "value": val})
        grp = {"type": "group", "match": "all", "items": conds}
        return {"type": "group", "match": "none", "items": [grp]} if op in ("is_false", "not_contains") else grp
    if key == "device_problem":
        cond = {"type": "cond", "table": "devices", "column": "problem", "op": "gt", "value": "0"}
        return cond if op == "is_true" else {"type": "group", "match": "none", "items": [cond]}
    if key == "collection":
        return {"type": "cond", "table": "computer", "column": "collection", "op": op, "value": val}
    # computer column
    typ = next((t for k, _l, t, _e in COMPUTER_COLS if k == key), "text")
    if key.startswith("cf_"):
        typ = "text"
    if typ == "number":
        op = _LEGACY_OPS.get(op, op)
    if typ == "date" and op in ("contains", "equals", "not_equals", "starts", "ends", "not_contains"):
        typ_op = op  # ignore old text comparisons on date fields
        op = "not_empty" if typ_op != "not_contains" else "empty"
    if typ == "version" and op in ("not_contains", "not_equals", "ends"):
        op = {"not_contains": "contains", "not_equals": "equals", "ends": "contains"}[op]
    return {"type": "cond", "table": "computer", "column": key, "op": op, "value": val}


def normalize(filters, match="all"):
    """Always returns a filter tree (old flat lists are converted)."""
    if isinstance(filters, str):
        try:
            filters = json.loads(filters or "[]")
        except ValueError:
            filters = []
    if isinstance(filters, dict) and filters.get("type") == "group":
        return filters
    items = []
    for f in filters or []:
        if isinstance(f, dict) and f.get("type") in ("cond", "group"):
            items.append(f)
        elif isinstance(f, dict) and f.get("field"):
            items.append(_legacy_item(f))
    return {"type": "group", "match": "any" if match == "any" else "all", "items": items}


def build(filters, match="all", depth=0, hops=0):
    return build_tree(normalize(filters, match), depth, hops)


def where_for(cid, hops=0):
    col = db.q1("SELECT * FROM collections WHERE id=?", (cid,))
    if not col:
        raise ValueError("Sammlung nicht gefunden")
    if hops > HOPS_MAX:
        raise ValueError(f"Zu viele Sammlungen bauen aufeinander auf (mehr als {HOPS_MAX} Verweise „ist Mitglied von“ "
                         "bzw. übergeordnete Sammlungen hintereinander).")
    if col["type"] == "static":
        return _tenant_limit(col, "c.id IN (SELECT computer_id FROM collection_members WHERE collection_id = ?)", [cid], hops)
    if col["type"] == "folder":
        return "1=0", []
    w, p = build(col["filters"], col["match"], 0, hops)  # subgroups are counted anew per collection
    # A dynamic collection under another collection (not under a folder)
    # contains only computers of the parent collection.
    par = db.q1("SELECT id, type, ad_kind FROM collections WHERE id=?", (col["parent_id"],)) if col.get("parent_id") else None
    if par and par["type"] in ("dynamic", "static") and not col.get("ad_kind"):
        pw, pp = where_for(par["id"], hops + 1)
        w, p = f"({w}) AND ({pw})", p + pp
    return _tenant_limit(col, w, p, hops)


def _tenant_limit(col, w, p, hops):
    """2.30: an OU admin's collection never contains more than the computers of their tenant – whatever the filter says."""
    if not col.get("tenant_id"):
        return w, p
    from . import access
    t = db.q1("SELECT collection_id, collection_ids FROM tenants WHERE id=?", (col["tenant_id"],))
    cols = [c for c in access.tenant_cols(t) if c != col["id"]] if t else []
    if not cols:
        return "1=0", []
    parts, pp = [], []
    for c in cols:
        cw, cp = where_for(c, hops + 1)
        parts.append(f"({cw})")
        pp += cp
    return f"({w}) AND ({' OR '.join(parts)})", p + pp


_member_cache = {}
MEMBER_TTL = 15  # seconds – otherwise a heartbeat/minute run queries the same collection many times


def invalidate():
    _member_cache.clear()


def member_ids(cid, fresh=False):
    now = time.time()
    hit = _member_cache.get(cid)
    if hit and not fresh and now - hit[0] < MEMBER_TTL:
        return list(hit[1])
    w, p = where_for(cid)
    ids = [r["id"] for r in db.q(f"SELECT c.id FROM computers c WHERE {w}", p)]
    _member_cache[cid] = (now, ids)
    return list(ids)


def count(cid):
    try:
        w, p = where_for(cid)
        return db.val(f"SELECT COUNT(*) FROM computers c WHERE {w}", p, 0)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# For the UI
# ---------------------------------------------------------------------------
def schema():
    tables = []
    custom = _scanner_tables()
    keys = TABLE_ORDER + sorted(custom)
    for t in keys:
        if t == "custom_fields":
            fields = _custom_fields()
            if fields:
                tables.append({"key": "computer", "label": "Benutzerdefinierte Felder", "alias": True,
                               "columns": [{"key": k, "label": l, "type": ty} for k, l, ty, _e in fields]})
            continue
        if t in custom:
            label, cols = custom[t]
            tables.append({"key": t, "label": label, "columns": [{"key": k, "label": l, "type": ty} for k, l, ty in cols],
                           "free_columns": True})
            continue
        tdef = _table(t)
        cols = [{"key": k, "label": v[0], "type": v[1]} for k, v in tdef[4].items() if not k.startswith("cf_")]
        tables.append({"key": t, "label": tdef[1], "columns": cols})
    return {"tables": tables, "ops": OPS, "op_labels": OP_LABELS, "no_value": sorted(NO_VALUE), "match_labels": MATCH_LABELS}


def fmt_bytes(n, digits=1):
    """1536 → "1,5 KB" (1 KB = 1024 bytes) – notation that the numeric comparisons understand again (_num)."""
    try:
        v = float(n)
    except (TypeError, ValueError):
        return "" if n is None else str(n)
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    i = 0
    while abs(v) >= 1024 and i < len(units) - 1:
        v /= 1024
        i += 1
    txt = f"{v:.0f}" if i == 0 else f"{v:.{digits}f}".rstrip("0").rstrip(".")
    return f"{txt.replace('.', ',')} {units[i]}"


def _bytes_values(tdef, expr, eparams):
    """Suggestions for size columns: typical thresholds from the existing values, readable (e.g. "10 GB")."""
    kind, _l, frm, link, _c, cat = tdef
    sql = (f"SELECT MIN(CAST(({expr}) AS REAL)) lo, MAX(CAST(({expr}) AS REAL)) hi FROM {frm} JOIN computers c ON {link} "
           f"WHERE ({expr}) IS NOT NULL AND ({expr}) <> ''")
    r = db.q1(sql, eparams + eparams + ([cat] if kind == "json" else []) + eparams + eparams) or {}
    lo, hi = r.get("lo"), r.get("hi")
    out = []
    for gb in (0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1024, 2048):
        b = gb * 1024 ** 3
        if lo is None or (lo <= b <= hi * 1.5):
            out.append(fmt_bytes(b))
    for mb in (100, 500):
        b = mb * 1024 ** 2
        if lo is not None and lo <= b <= hi:
            out.insert(0, fmt_bytes(b))
    return out


def values(table, column, q="", limit=40, scope=("1=1", [])):
    """Existing values of a column (suggestions in the editor). scope: (SQL, parameters) on c.id – OU admin only their own."""
    sw, sp = scope
    tdef = _table(table)
    typ, expr, eparams = _col(table, tdef, column)
    if typ in ("bool", "collection"):
        return []
    if typ == "bytes":
        return _bytes_values(tdef, expr, eparams)
    kind, _l, frm, link, _c, cat = tdef
    like = util.like_pattern(q or "", "contains")
    if kind == "computer":
        sql = f"SELECT DISTINCT ({expr}) v FROM computers c WHERE ({expr}) IS NOT NULL AND ({expr}) LIKE ? ESCAPE '\\' AND {sw} ORDER BY 1 LIMIT ?"
        return [r["v"] for r in db.q(sql, eparams + eparams + eparams + [like] + list(sp) + [limit])]
    sql = (f"SELECT DISTINCT ({expr}) v FROM {frm} JOIN computers c ON {link} "
           f"WHERE ({expr}) IS NOT NULL AND ({expr}) LIKE ? ESCAPE '\\' AND {sw} ORDER BY 1 LIMIT ?")
    params = eparams + ([cat] if kind == "json" else []) + eparams + eparams + [like] + list(sp) + [limit]
    return [r["v"] for r in db.q(sql, params)]


# ---------------------------------------------------------------------------
# Usage (check before deleting – otherwise reports, notifications and schedules silently end up empty)
# ---------------------------------------------------------------------------
def _tree_refs(node, out):
    """Collection IDs that a filter tree references with "ist Mitglied von"."""
    if isinstance(node, dict):
        if node.get("type") == "group":
            for it in node.get("items") or []:
                _tree_refs(it, out)
        elif node.get("column") == "collection" and str(node.get("value") or "").strip().isdigit():
            out.add(int(str(node["value"]).strip()))
    elif isinstance(node, list):
        for it in node:
            _tree_refs(it, out)
    return out


def _jl(v, default):
    try:
        return json.loads(v or "null") or default
    except ValueError:
        return default


def _cond_refs(conditions, steps):
    """Collection IDs from package and step conditions (JSON texts or already parsed)."""
    refs = set()

    def add(c):
        for cc in (c or {}).get("collections") or [] if isinstance(c, dict) else []:
            v = str((cc or {}).get("id") if isinstance(cc, dict) else "").strip()
            if v.isdigit() and int(v) > 0:
                refs.add(int(v))
    add(_jl(conditions, {}) if isinstance(conditions, (str, type(None))) else conditions)
    st = _jl(steps, {}) if isinstance(steps, (str, type(None))) else steps
    for ph in (st or {}).values() if isinstance(st, dict) else []:
        for x in ph or []:
            if isinstance(x, dict):
                add(x.get("conditions"))
    return refs


def usages():
    """[(collection_id, "Art: Name")] of all references to collections."""
    out = []
    for c in db.q("SELECT id, name, filters, match FROM collections"):
        for ref in _tree_refs(normalize(c["filters"], c["match"]), set()):
            if ref != c["id"]:
                out.append((ref, f"Sammlung „{c['name']}“", c["id"]))
    for r in db.q("SELECT name, definition FROM custom_reports"):
        d = _jl(r["definition"], {})
        refs = _tree_refs(d.get("filter"), set())
        if str(d.get("collection") or "").isdigit():
            refs.add(int(d["collection"]))
        out += [(x, f"Eigener Bericht „{r['name']}“") for x in refs]
    for r in db.q("SELECT name, reports FROM mail_reports"):
        for rep in _jl(r["reports"], []):
            v = str(((rep or {}).get("params") or {}).get("collection") or "")
            if v.isdigit():
                out.append((int(v), f"Berichtsversand „{r['name']}“"))
    for r in db.q("SELECT key, params FROM mail_alerts WHERE enabled=1"):
        v = str(_jl(r["params"], {}).get("collection") or "")
        if v.isdigit():
            out.append((int(v), f"Benachrichtigung „{r['key']}“"))
    for r in db.q("SELECT name, targets FROM schedules"):
        for x in (_jl(r["targets"], {}) or {}).get("collections") or []:
            if str(x).isdigit():
                out.append((int(x), f"Zeitplan „{r['name']}“"))
    for r in db.q("SELECT name, collections FROM scan_profiles"):
        for x in _jl(r["collections"], []):
            if str(x).isdigit() and int(x) > 0:
                out.append((int(x), f"Scanprofil „{r['name']}“"))
    from . import access
    for r in db.q("SELECT name, collection_id, collection_ids FROM tenants"):
        for col in access.tenant_cols(r):
            out.append((int(col), f"Mandant „{r['name']}“"))
    # Audit 2.29: conditions in packages (package and individual steps) – otherwise an exception collection could be deleted
    for r in db.q("SELECT name, conditions, steps FROM packages"):
        for x in _cond_refs(r["conditions"], r["steps"]):
            out.append((x, f"Paket „{r['name']}“ (Bedingung)"))
    try:
        for r in db.q("""SELECT p.name, k.collection_id FROM kiosk_items k LEFT JOIN packages p ON p.id = k.package_id
                         WHERE k.collection_id IS NOT NULL"""):
            out.append((int(r["collection_id"]), f"Kiosk „{r['name'] or '?'}“"))
    except Exception:
        pass  # table does not (yet) exist
    return out


def referenced_ids():
    return {u[0] for u in usages()}


def used_by(ids, exclude_self=False):
    """Labels of all usages. exclude_self: do not count references from collections that are themselves in ids (they are
    deleted too) – by ID, not by name (copies with the same name still count)."""
    ids = set(ids)
    return sorted({u[1] for u in usages() if u[0] in ids and not (exclude_self and len(u) > 2 and u[2] in ids)})


def dependents(cid):
    """This collection and all that build on it (also across several levels): via "ist Mitglied von" or as a
    child collection (a dynamic collection under a collection contains only its computers)."""
    rev = {}
    for c in db.q("SELECT id, filters, match, parent_id FROM collections"):
        for ref in _tree_refs(normalize(c["filters"], c["match"]), set()):
            rev.setdefault(ref, set()).add(c["id"])
        if c["parent_id"]:
            rev.setdefault(c["parent_id"], set()).add(c["id"])
    out, stack = set(), [cid]
    while stack:
        x = stack.pop()
        if x in out:
            continue
        out.add(x)
        stack.extend(rev.get(x, ()))
    return out


def broken(ids):
    """{id: error message} of the dynamic collections among ids whose filter can no longer be evaluated."""
    out = {}
    for c in db.q(f"SELECT id, type FROM collections WHERE id IN ({','.join('?' * len(ids))})", tuple(ids)) if ids else []:
        if c["type"] != "dynamic":
            continue
        try:
            w, p = where_for(c["id"])
            if len(w) > MAX_SQL:
                raise ValueError("Filter zu umfangreich (zu viele verschachtelte Sammlungsverweise).")
        except Exception as e:
            out[c["id"]] = str(e)
    return out


def check_dependents(cid, before):
    """After a change (call within the transaction): collections that would become unusable as a result – ones that were
    already broken before do not count. Raises ValueError with the name of the affected collection."""
    new = {k: v for k, v in broken(dependents(cid)).items() if k not in before}
    if not new:
        return
    names = {r["id"]: r["name"] for r in db.q(f"SELECT id, name FROM collections WHERE id IN ({','.join('?' * len(new))})",
                                              tuple(new))}
    first = next(iter(new))
    other = f" (und {len(new) - 1} weitere)" if len(new) > 1 else ""
    if first == cid:
        raise ValueError(new[first])
    raise ValueError(f"Nicht gespeichert: Die Sammlung „{names.get(first, first)}“{other} baut auf dieser auf und wäre danach "
                     f"nicht mehr auswertbar – {new[first]}")


def check_cycle(cid, tree, parent_id=None):
    """Reject references A → B → … → A (also via parent collections) on save."""
    graph = {}
    for c in db.q("SELECT id, filters, match, parent_id FROM collections"):
        refs = _tree_refs(normalize(c["filters"], c["match"]), set())
        if c["parent_id"]:
            refs.add(c["parent_id"])
        graph[c["id"]] = refs
    mine = _tree_refs(tree, set())
    if parent_id:
        mine.add(int(parent_id))
    graph[cid] = mine
    names = {c["id"]: c["name"] for c in db.q("SELECT id, name FROM collections")}
    stack, seen = [(x, [x]) for x in mine], set()
    while stack:
        cur, path = stack.pop()
        if cur == cid:
            chain = " → ".join(names.get(x, str(x)) for x in [cid] + path)
            raise ValueError(f"Zirkelbezug zwischen Sammlungen: {chain}")
        if cur in seen:
            continue
        seen.add(cur)
        stack += [(n, path + [n]) for n in graph.get(cur, ())]
