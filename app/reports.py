"""Reports (table, CSV export, print view)."""
import csv
import re
import io

from . import collections_, db, util

COLL = {"name": "collection", "label": "Sammlung", "type": "collection", "default": ""}


def _scope(params, col="c.id"):
    """2.30: OU admin - only their computers (set by the server, never from the request: main removes parameters with "_")."""
    sc = params.get("_scope")
    if sc is None:
        return "1=1", []
    return f"{col} IN (SELECT value FROM json_each(?))", [sc]


def _cw(params):
    cid = params.get("collection")
    sw, sp = _scope(params)
    if cid:
        w, a = collections_.where_for(int(cid))
        return f"({w}) AND {sw}", list(a) + sp
    return sw, sp


def _computers(p):
    w, a = _cw(p)
    return (f"""SELECT c.name, COALESCE(c.os_name, c.ad_os) os, c.os_display_version, c.os_build, c.manufacturer,
               c.model, c.serial, c.cpu, ROUND(c.ram_bytes/1073741824.0,1) ram_gb, c.current_user, c.ip_addresses,
               c.last_boot, c.last_scan, c.scan_status FROM computers c WHERE {w} ORDER BY c.name""", a,
            [("name", "Computer"), ("os", "Betriebssystem"), ("os_display_version", "Version"), ("os_build", "Build"),
             ("manufacturer", "Hersteller"), ("model", "Modell"), ("serial", "Seriennr."), ("cpu", "CPU"),
             ("ram_gb", "RAM (GB)"), ("current_user", "Benutzer"), ("ip_addresses", "IP"),
             ("last_boot", "Letzter Start"), ("last_scan", "Letzter Scan"), ("scan_status", "Scan")])


def _software(p):
    w, a = _cw(p)
    flt = ""
    if p.get("name"):
        flt = " AND s.name LIKE ? ESCAPE '\\'"
        a = a + [util.like_pattern(p["name"])]
    return (f"""SELECT s.name, s.version, s.publisher, COUNT(DISTINCT s.computer_id) installs, s.winget_id, s.winget_latest
               FROM software s JOIN computers c ON c.id=s.computer_id WHERE {w}{flt}
               GROUP BY s.name, s.version, s.publisher ORDER BY s.name, s.version""", a,
            [("name", "Software"), ("version", "Version"), ("publisher", "Hersteller"), ("installs", "Installationen"),
             ("winget_id", "winget-ID"), ("winget_latest", "Aktuell")])


def _software_computers(p):
    w, a = _cw(p)
    a = a + [util.like_pattern(p.get("name") or "")]
    return (f"""SELECT c.name computer, s.name, s.version, s.publisher, s.install_date, s.winget_latest,
               CASE WHEN s.outdated=1 THEN 'ja' ELSE '' END outdated
               FROM software s JOIN computers c ON c.id=s.computer_id WHERE {w} AND s.name LIKE ? ESCAPE '\\'
               ORDER BY s.name, c.name""", a,
            [("computer", "Computer"), ("name", "Software"), ("version", "Version"), ("publisher", "Hersteller"),
             ("install_date", "Installiert"), ("winget_latest", "Aktuell"), ("outdated", "Veraltet")])


def _outdated(p):
    w, a = _cw(p)
    return (f"""SELECT c.name computer, s.name, s.version, s.winget_latest, s.winget_id, c.online
               FROM software s JOIN computers c ON c.id=s.computer_id WHERE s.outdated=1 AND {w}
               ORDER BY s.name, c.name""", a,
            [("computer", "Computer"), ("name", "Software"), ("version", "Installiert"),
             ("winget_latest", "Verfügbar"), ("winget_id", "winget-ID"), ("online", "Online")])


def _os(p):
    w, a = _cw(p)
    return (f"""SELECT COALESCE(c.os_name, c.ad_os, 'unbekannt') os, COALESCE(c.os_display_version,'') version,
               COUNT(*) anzahl FROM computers c WHERE {w} GROUP BY 1, 2 ORDER BY anzahl DESC""", a,
            [("os", "Betriebssystem"), ("version", "Version"), ("anzahl", "Anzahl")])


def _low_disk(p):
    w, a = _cw(p)
    gb = float(p.get("gb") or db.setting("low_disk_gb"))
    return (f"""SELECT c.name computer, d.drive, d.label, ROUND(d.size/1073741824.0,1) size_gb,
               ROUND(d.free/1073741824.0,1) free_gb, ROUND(100.0*d.free/NULLIF(d.size,0),1) free_pct
               FROM disks d JOIN computers c ON c.id=d.computer_id WHERE {w} AND d.free < ?
               ORDER BY d.free""", a + [gb * 1073741824],
            [("computer", "Computer"), ("drive", "Laufwerk"), ("label", "Bezeichnung"), ("size_gb", "Größe (GB)"),
             ("free_gb", "Frei (GB)"), ("free_pct", "Frei (%)")])


def _not_scanned(p):
    w, a = _cw(p)
    days = int(float(p.get("days") or 7))
    return (f"""SELECT c.name, c.online, c.last_seen, c.last_scan, c.scan_status, c.scan_error FROM computers c
               WHERE {w} AND (c.last_scan IS NULL OR c.last_scan < ?) ORDER BY c.last_scan""",
            a + [util.ago(days=days)],
            [("name", "Computer"), ("online", "Online"), ("last_seen", "Zuletzt gesehen"), ("last_scan", "Letzter Scan"),
             ("scan_status", "Status"), ("scan_error", "Fehler")])


def _deployments(p):
    days = int(float(p.get("days") or 30))
    sw, sp = _scope(p, "t.computer_id")
    return (f"""SELECT d.id, d.name, d.source, d.created_by, t.computer_name, t.status, t.exit_code, t.message,
              t.finished_at FROM deployment_targets t JOIN deployments d ON d.id=t.deployment_id
              WHERE d.created_at >= ? AND {sw} ORDER BY d.id DESC, t.computer_name""", [util.ago(days=days)] + sp,
            [("id", "#"), ("name", "Bereitstellung"), ("source", "Quelle"), ("created_by", "Von"),
             ("computer_name", "Computer"), ("status", "Status"), ("exit_code", "Exitcode"), ("message", "Meldung"),
             ("finished_at", "Beendet")])


def _changes(p):
    w, a = _cw(p)
    days = int(float(p.get("days") or 30))
    return (f"""SELECT x.ts, c.name computer, x.change, x.name, x.old_version, x.new_version FROM software_changes x
               JOIN computers c ON c.id=x.computer_id WHERE {w} AND x.ts >= ? ORDER BY x.ts DESC""",
            a + [util.ago(days=days)],
            [("ts", "Zeitpunkt"), ("computer", "Computer"), ("change", "Änderung"), ("name", "Software"),
             ("old_version", "Alt"), ("new_version", "Neu")])


def _admins(p):
    w, a = _cw(p)
    return (f"""SELECT c.name computer, l.name member, l.type FROM local_admins l JOIN computers c ON c.id=l.computer_id
               WHERE {w} ORDER BY c.name, l.name""", a,
            [("computer", "Computer"), ("member", "Mitglied"), ("type", "Typ")])


def _hotfix(p):
    w, a = _cw(p)
    return (f"""SELECT c.name computer, c.os_build, MAX(h.installed_on) last_hotfix, COUNT(h.id) hotfixes,
               CAST(julianday('now','localtime') - julianday(MAX(h.installed_on)) AS INTEGER) days
               FROM computers c LEFT JOIN hotfixes h ON h.computer_id=c.id WHERE {w}
               GROUP BY c.id ORDER BY last_hotfix""", a,
            [("computer", "Computer"), ("os_build", "Build"), ("last_hotfix", "Letztes Update"),
             ("hotfixes", "Anzahl Hotfixes"), ("days", "Tage her")])


def _security(p):
    w, a = _cw(p)
    return (f"""SELECT c.name computer,
               CASE json_extract(c.extra,'$.defender.enabled') WHEN 1 THEN 'ja' WHEN 0 THEN 'NEIN' ELSE '' END defender,
               CASE json_extract(c.extra,'$.defender.realtime') WHEN 1 THEN 'ja' WHEN 0 THEN 'NEIN' ELSE '' END realtime,
               json_extract(c.extra,'$.defender.signature_age') sig_age,
               (SELECT group_concat(json_extract(b.value,'$.mount') || ' ' || json_extract(b.value,'$.protection'), ', ')
                  FROM json_each(c.extra, '$.bitlocker') b) bitlocker,
               json_extract(c.extra,'$.tpm.version') tpm,
               CASE json_extract(c.extra,'$.pending_reboot') WHEN 1 THEN 'ja' ELSE '' END reboot
               FROM computers c WHERE {w} AND c.extra IS NOT NULL ORDER BY c.name""", a,
            [("computer", "Computer"), ("defender", "Defender"), ("realtime", "Echtzeitschutz"),
             ("sig_age", "Signaturalter (Tage)"), ("bitlocker", "BitLocker"), ("tpm", "TPM"),
             ("reboot", "Neustart ausstehend")])


def _json_report(cat, cols, where_extra="", order=None):
    """Report over an inventory category (JSON per computer)."""
    def fn(p):
        w, a = _cw(p)
        sel = ", ".join(f"json_extract(j.value, '$.{k}') AS \"{k}\"" for k, _l in cols)
        sql = (f"SELECT c.name AS computer, {sel} FROM inventory_data d JOIN computers c ON c.id = d.computer_id, "
               f"json_each(d.json) j WHERE d.category = '{cat}' AND {w} {where_extra} "
               f"ORDER BY {order or 'c.name'}")
        return sql, a, [("computer", "Computer")] + cols
    return fn


def _system_props(p):
    w, a = _cw(p)
    x = lambda k: f"json_extract(c.extra, '$.{k}')"  # noqa: E731
    return (f"""SELECT c.name computer, c.manufacturer, c.model, {x('chassis_name')} chassis, c.serial,
               {x('bios_manufacturer')} bios_manufacturer, c.bios_version, {x('bios_mode')} bios_mode,
               CASE {x('secure_boot')} WHEN 1 THEN 'ja' WHEN 0 THEN 'nein' ELSE '' END secure_boot,
               {x('tpm.version')} tpm, CASE {x('smb1_server')} WHEN 1 THEN 'JA' WHEN 0 THEN 'nein' ELSE '' END smb1_server,
               CASE {x('smb1_client')} WHEN 1 THEN 'JA' WHEN 0 THEN 'nein' ELSE '' END smb1_client,
               {x('dotnet')} dotnet, c.ad_location location
               FROM computers c WHERE {w} AND c.last_scan IS NOT NULL ORDER BY c.name""", a,
            [("computer", "Computer"), ("manufacturer", "Hersteller"), ("model", "Modell"), ("chassis", "Gehäuse"),
             ("serial", "Seriennr."), ("bios_manufacturer", "BIOS-Hersteller"), ("bios_version", "BIOS"),
             ("bios_mode", "BIOS-Modus"), ("secure_boot", "Secure Boot"), ("tpm", "TPM"), ("smb1_server", "SMBv1-Server"),
             ("smb1_client", "SMBv1-Client"), ("dotnet", ".NET"), ("location", "Standort")])


def _wol(p):
    """2.31: Wake-on-LAN readiness per network card - why a computer does not wake up is often found here."""
    w, a = _cw(p)
    x = lambda k: f"json_extract(j.value, '$.{k}')"  # noqa: E731
    return (f"""SELECT c.name computer, {x('name')} adapter, {x('status')} status,
               CASE {x('wired')} WHEN 1 THEN 'ja' WHEN 0 THEN 'nein' ELSE '' END wired,
               {x('wol_magic')} wol_magic, CASE {x('wake_armed')} WHEN 1 THEN 'ja' WHEN 0 THEN 'NEIN' ELSE '' END wake_armed,
               CASE json_extract(c.extra, '$.fast_startup') WHEN 1 THEN 'AN' WHEN 0 THEN 'aus' ELSE '' END fast_startup,
               {x('wol_driver')} wol_driver, {x('mac')} mac
               FROM inventory_data d JOIN computers c ON c.id = d.computer_id, json_each(d.json) j
               WHERE d.category = 'nics' AND {w} ORDER BY c.name, {x('name')}""", a,
            [("computer", "Computer"), ("adapter", "Netzwerkkarte"), ("status", "Status"), ("wired", "Kabel"),
             ("wol_magic", "Magic Packet"), ("wake_armed", "Darf wecken"), ("fast_startup", "Schnellstart"),
             ("wol_driver", "Einstellungen des Treibers"), ("mac", "MAC")])


REPORTS = [
    {"key": "computers", "title": "Computerübersicht", "desc": "Hardware, Betriebssystem und Status aller Computer.",
     "params": [COLL], "fn": _computers},
    {"key": "software", "title": "Software-Inventar", "desc": "Installierte Software mit Anzahl der Installationen.",
     "params": [COLL, {"name": "name", "label": "Name enthält", "type": "text", "default": ""}], "fn": _software},
    {"key": "software_computers", "title": "Software je Computer", "desc": "Auf welchen Computern ist eine Software installiert?",
     "params": [COLL, {"name": "name", "label": "Software (Name enthält)", "type": "text", "default": ""}],
     "fn": _software_computers},
    {"key": "outdated", "title": "Veraltete Software", "desc": "Software mit neuerer Version im winget-Repository.",
     "params": [COLL], "fn": _outdated},
    {"key": "os", "title": "Betriebssysteme", "desc": "Verteilung der Betriebssysteme und Versionen.",
     "params": [COLL], "fn": _os},
    {"key": "low_disk", "title": "Wenig Speicherplatz", "desc": "Laufwerke mit wenig freiem Speicher.",
     "params": [COLL, {"name": "gb", "label": "Frei weniger als (GB)", "type": "number", "default": "10"}], "fn": _low_disk},
    {"key": "not_scanned", "title": "Nicht gescannt", "desc": "Computer ohne aktuellen Scan.",
     "params": [COLL, {"name": "days", "label": "Älter als (Tage)", "type": "number", "default": "7"}], "fn": _not_scanned},
    {"key": "hotfix", "title": "Windows-Updates", "desc": "Datum des letzten installierten Hotfixes je Computer.",
     "params": [COLL], "fn": _hotfix},
    {"key": "security", "title": "Sicherheitsstatus", "desc": "Defender, BitLocker, TPM und ausstehende Neustarts.",
     "params": [COLL], "fn": _security},
    {"key": "admins", "title": "Lokale Administratoren", "desc": "Mitglieder der lokalen Administratorengruppe.",
     "params": [COLL], "fn": _admins},
    {"key": "changes", "title": "Softwareänderungen", "desc": "Installierte, aktualisierte und entfernte Software.",
     "params": [COLL, {"name": "days", "label": "Zeitraum (Tage)", "type": "number", "default": "30"}], "fn": _changes},
    {"key": "system_props", "title": "Systemeigenschaften", "desc": "Gehäuse, BIOS-Modus, Secure Boot, TPM, SMBv1, .NET.",
     "params": [COLL], "fn": _system_props},
    {"key": "memory", "title": "Speichermodule", "desc": "RAM-Module je Steckplatz mit Größe, Takt und Teilenummer.",
     "params": [COLL], "fn": _json_report("memory", [("slot", "Steckplatz"), ("capacity", "Größe (Byte)"), ("speed", "MHz"),
                                                      ("manufacturer", "Hersteller"), ("part", "Teilenummer"), ("serial", "Seriennr.")])},
    {"key": "displays", "title": "Bildschirme", "desc": "Angeschlossene Monitore mit Seriennummer und Baujahr.",
     "params": [COLL], "fn": _json_report("displays", [("manufacturer", "Hersteller"), ("model", "Modell"),
                                                        ("serial", "Seriennr."), ("year", "Baujahr")])},
    {"key": "printers", "title": "Drucker", "desc": "Installierte Drucker je Computer.",
     "params": [COLL], "fn": _json_report("printers", [("name", "Drucker"), ("driver", "Treiber"), ("port", "Anschluss"),
                                                        ("network", "Netzwerk"), ("default", "Standard")])},
    {"key": "local_users", "title": "Lokale Benutzer", "desc": "Lokale Konten mit Status und letzter Anmeldung.",
     "params": [COLL], "fn": _json_report("local_users", [("name", "Benutzer"), ("full_name", "Name"), ("enabled", "Aktiv"),
                                                           ("last_logon", "Letzte Anmeldung"),
                                                           ("password_last_set", "Passwort gesetzt")])},
    {"key": "product_keys", "title": "Produktschlüssel & Aktivierung", "desc": "Windows- und Office-Lizenzen.",
     "params": [COLL], "fn": _json_report("product_keys", [("product", "Produkt"), ("key", "Schlüssel"), ("channel", "Kanal"),
                                                            ("status", "Status")])},
    {"key": "device_problems", "title": "Geräte mit Problemen", "desc": "Geräte-Manager-Einträge mit Fehlercode.",
     "params": [COLL], "fn": _json_report("devices", [("name", "Gerät"), ("class", "Klasse"), ("problem", "Fehlercode"),
                                                       ("status", "Status")],
                                          "AND CAST(json_extract(j.value, '$.problem') AS INTEGER) > 0")},
    {"key": "wol", "title": "Wake-on-LAN-Bereitschaft", "desc": "Je Netzwerkkarte: Magic Packet erlaubt, darf den Computer "
     "wecken, Einstellungen des Treibers, Windows-Schnellstart (ab Scan mit 2.31). Die Einstellung im BIOS/UEFI kann "
     "Windows nicht auslesen.", "params": [COLL], "fn": _wol},
    {"key": "deployments", "title": "Bereitstellungsverlauf", "desc": "Ergebnisse aller Bereitstellungen.",
     "params": [{"name": "days", "label": "Zeitraum (Tage)", "type": "number", "default": "30"}], "fn": _deployments},
]

BY_KEY = {r["key"]: r for r in REPORTS}


def definitions():
    from . import customreports
    return customreports.definitions() + [{k: v for k, v in r.items() if k != "fn"} for r in REPORTS]


def get_def(key):
    """Definition of a fixed or custom report (for report mailing)."""
    if key in BY_KEY:
        return BY_KEY[key]
    from . import customreports
    if str(key).startswith(customreports.PREFIX):
        return next((d for d in customreports.definitions() if d["key"] == key), None)
    return None


def run(key, params):
    from . import customreports
    if str(key).startswith(customreports.PREFIX):
        return customreports.run_key(key, params)
    r = BY_KEY.get(key)
    if not r:
        raise ValueError("Unbekannter Bericht")
    sql, args, cols = r["fn"](params)
    rows = db.q(sql, args)
    return {"title": r["title"], "columns": [{"key": k, "label": l} for k, l in cols], "rows": rows,
            "generated": util.now()}


_NUM = re.compile(r"^[-+]?\d+([.,]\d+)?$")


def _safe(v):
    """Protection against formula injection in Excel: neutralize texts starting with = + - @ (except pure numbers)."""
    if v is None:
        return ""
    s = str(v)
    t = s.lstrip(" \t\r\n")
    if t and (t[0] in "=+-@" or s[0] in "\t\r") and not _NUM.match(s.strip()):
        return "'" + s
    return v


def to_csv(columns, rows):
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    w.writerow([_safe(c["label"]) for c in columns])  # neutralize column names too (custom reports)
    for r in rows:
        w.writerow([_safe(r.get(c["key"])) for c in columns])
    return buf.getvalue()
