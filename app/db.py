"""SQLite-Zugriff (eine Verbindung pro Thread, WAL-Modus)."""
import json
import sqlite3
import threading

from . import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS sessions(
  token_hash TEXT PRIMARY KEY, username TEXT, display_name TEXT,
  created TEXT, expires TEXT, ip TEXT);

CREATE TABLE IF NOT EXISTS computers(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, dns_name TEXT, ad_guid TEXT UNIQUE, dn TEXT, description TEXT,
  ad_os TEXT, ad_os_version TEXT, ad_enabled INTEGER DEFAULT 1, ad_present INTEGER DEFAULT 1,
  ad_last_logon TEXT, ad_created TEXT,
  online INTEGER DEFAULT 0, winrm INTEGER DEFAULT 0, last_seen TEXT,
  last_scan TEXT, last_scan_attempt TEXT, scan_status TEXT, scan_error TEXT,
  manufacturer TEXT, model TEXT, serial TEXT, bios_version TEXT, bios_date TEXT,
  cpu TEXT, cores INTEGER, threads INTEGER, ram_bytes INTEGER,
  os_name TEXT, os_version TEXT, os_build TEXT, os_display_version TEXT, os_arch TEXT,
  os_install_date TEXT, last_boot TEXT, current_user TEXT,
  ip_addresses TEXT, mac_addresses TEXT, extra TEXT, notes TEXT, created_at TEXT);
CREATE INDEX IF NOT EXISTS ix_comp_name ON computers(name);

CREATE TABLE IF NOT EXISTS disks(
  id INTEGER PRIMARY KEY, computer_id INTEGER REFERENCES computers(id) ON DELETE CASCADE,
  drive TEXT, label TEXT, fs TEXT, size INTEGER, free INTEGER);
CREATE INDEX IF NOT EXISTS ix_disks_c ON disks(computer_id);

CREATE TABLE IF NOT EXISTS network(
  id INTEGER PRIMARY KEY, computer_id INTEGER REFERENCES computers(id) ON DELETE CASCADE,
  description TEXT, mac TEXT, ip TEXT, gateway TEXT, dns TEXT, dhcp INTEGER);
CREATE INDEX IF NOT EXISTS ix_net_c ON network(computer_id);

CREATE TABLE IF NOT EXISTS software(
  id INTEGER PRIMARY KEY, computer_id INTEGER REFERENCES computers(id) ON DELETE CASCADE,
  name TEXT, version TEXT, publisher TEXT, install_date TEXT, product_code TEXT,
  arch TEXT, scope TEXT, uninstall_string TEXT, quiet_uninstall TEXT,
  winget_id TEXT, winget_latest TEXT, outdated INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_sw_c ON software(computer_id);
CREATE INDEX IF NOT EXISTS ix_sw_name ON software(name);
CREATE INDEX IF NOT EXISTS ix_sw_out ON software(outdated);

CREATE TABLE IF NOT EXISTS hotfixes(
  id INTEGER PRIMARY KEY, computer_id INTEGER REFERENCES computers(id) ON DELETE CASCADE,
  hotfix_id TEXT, description TEXT, installed_on TEXT, installed_by TEXT);
CREATE INDEX IF NOT EXISTS ix_hf_c ON hotfixes(computer_id);

CREATE TABLE IF NOT EXISTS services(
  id INTEGER PRIMARY KEY, computer_id INTEGER REFERENCES computers(id) ON DELETE CASCADE,
  name TEXT, display_name TEXT, state TEXT, start_mode TEXT, account TEXT);
CREATE INDEX IF NOT EXISTS ix_svc_c ON services(computer_id);

CREATE TABLE IF NOT EXISTS local_admins(
  id INTEGER PRIMARY KEY, computer_id INTEGER REFERENCES computers(id) ON DELETE CASCADE,
  name TEXT, type TEXT);
CREATE INDEX IF NOT EXISTS ix_adm_c ON local_admins(computer_id);

CREATE TABLE IF NOT EXISTS software_changes(
  id INTEGER PRIMARY KEY, computer_id INTEGER REFERENCES computers(id) ON DELETE CASCADE,
  ts TEXT, change TEXT, name TEXT, old_version TEXT, new_version TEXT);
CREATE INDEX IF NOT EXISTS ix_chg_c ON software_changes(computer_id);
CREATE INDEX IF NOT EXISTS ix_chg_ts ON software_changes(ts);

CREATE TABLE IF NOT EXISTS collections(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, parent_id INTEGER, type TEXT DEFAULT 'dynamic',
  match TEXT DEFAULT 'all', filters TEXT DEFAULT '[]', description TEXT,
  builtin INTEGER DEFAULT 0, sort INTEGER DEFAULT 100);

CREATE TABLE IF NOT EXISTS collection_members(
  collection_id INTEGER REFERENCES collections(id) ON DELETE CASCADE,
  computer_id INTEGER REFERENCES computers(id) ON DELETE CASCADE,
  PRIMARY KEY(collection_id, computer_id));

CREATE TABLE IF NOT EXISTS packages(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, version TEXT, description TEXT, type TEXT,
  winget_id TEXT, winget_action TEXT, winget_version TEXT, winget_scope TEXT, extra_args TEXT,
  folder TEXT, install_type TEXT, install_file TEXT, arguments TEXT, script TEXT,
  success_codes TEXT, detect_name TEXT, timeout_min INTEGER, reboot TEXT, rescan INTEGER DEFAULT 1,
  created_at TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS deployments(
  id INTEGER PRIMARY KEY, name TEXT, package_id INTEGER, package_json TEXT, source TEXT,
  rule_id INTEGER, status TEXT, created_by TEXT, created_at TEXT, scheduled_at TEXT,
  started_at TEXT, finished_at TEXT);
CREATE INDEX IF NOT EXISTS ix_dep_status ON deployments(status);

CREATE TABLE IF NOT EXISTS deployment_targets(
  id INTEGER PRIMARY KEY, deployment_id INTEGER REFERENCES deployments(id) ON DELETE CASCADE,
  computer_id INTEGER REFERENCES computers(id) ON DELETE SET NULL, computer_name TEXT,
  status TEXT, exit_code INTEGER, message TEXT, output TEXT, started_at TEXT, finished_at TEXT);
CREATE INDEX IF NOT EXISTS ix_dt_dep ON deployment_targets(deployment_id);
CREATE INDEX IF NOT EXISTS ix_dt_c ON deployment_targets(computer_id);

CREATE TABLE IF NOT EXISTS tenants(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT, collection_id INTEGER, repo_folder TEXT,
  color TEXT, created_at TEXT);

CREATE TABLE IF NOT EXISTS schedules(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT, enabled INTEGER DEFAULT 1, tenant_id INTEGER,
  triggers TEXT, targets TEXT, packages TEXT, options TEXT, created_by TEXT, created_at TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS schedule_trigger_state(
  schedule_id INTEGER REFERENCES schedules(id) ON DELETE CASCADE, trigger_id TEXT, last_fired TEXT,
  PRIMARY KEY(schedule_id, trigger_id));

CREATE TABLE IF NOT EXISTS schedule_targets(
  schedule_id INTEGER REFERENCES schedules(id) ON DELETE CASCADE,
  computer_id INTEGER REFERENCES computers(id) ON DELETE CASCADE,
  status TEXT, last_run TEXT, last_success TEXT, attempts INTEGER DEFAULT 0, deployment_id INTEGER,
  message TEXT, pending INTEGER DEFAULT 0, pending_since TEXT,
  PRIMARY KEY(schedule_id, computer_id));

CREATE TABLE IF NOT EXISTS update_rules(
  id INTEGER PRIMARY KEY, name TEXT, enabled INTEGER DEFAULT 1, mode TEXT DEFAULT 'all',
  winget_ids TEXT, exclude_ids TEXT, collection_id INTEGER, days TEXT DEFAULT '1,2,3,4,5,6,7',
  window_start TEXT DEFAULT '00:00', window_end TEXT DEFAULT '23:59', interval_hours INTEGER DEFAULT 24,
  scope TEXT, last_run TEXT, created_at TEXT);

CREATE TABLE IF NOT EXISTS winget_overrides(
  name TEXT PRIMARY KEY COLLATE NOCASE, winget_id TEXT, ignore INTEGER DEFAULT 0);

-- 2.35: winget-Cache (aktuelle Installer ausgewählter winget-Pakete im Repository, für Computer ohne winget)
CREATE TABLE IF NOT EXISTS winget_cache(
  id INTEGER PRIMARY KEY, winget_id TEXT NOT NULL UNIQUE COLLATE NOCASE, arch TEXT DEFAULT 'x64',
  scope TEXT DEFAULT 'machine', status TEXT DEFAULT 'new', message TEXT, name TEXT, version TEXT, folder TEXT,
  file TEXT, size INTEGER, sha256 TEXT, installer_type TEXT, min_os TEXT, package_id INTEGER, detect_version TEXT,
  ignore_hash INTEGER DEFAULT 0, remote_sig TEXT,
  checked_at TEXT, updated_at TEXT, created_at TEXT, created_by TEXT);

CREATE TABLE IF NOT EXISTS disk_usage(
  id INTEGER PRIMARY KEY, computer_id INTEGER REFERENCES computers(id) ON DELETE CASCADE, path TEXT, ts TEXT,
  total INTEGER, seconds INTEGER, created_by TEXT, json TEXT);
CREATE INDEX IF NOT EXISTS ix_du_c ON disk_usage(computer_id);

CREATE TABLE IF NOT EXISTS update_exclusions(
  id INTEGER PRIMARY KEY, field TEXT NOT NULL DEFAULT 'name', pattern TEXT NOT NULL, note TEXT,
  created_by TEXT, created_at TEXT);

CREATE TABLE IF NOT EXISTS custom_reports(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT, definition TEXT, created_by TEXT, created_at TEXT,
  updated_at TEXT);

CREATE TABLE IF NOT EXISTS mail_reports(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT, enabled INTEGER DEFAULT 1, reports TEXT,
  recipients TEXT, triggers TEXT, options TEXT, state TEXT, last_run TEXT, last_sent TEXT, last_status TEXT,
  created_by TEXT, created_at TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS mail_alerts(
  key TEXT PRIMARY KEY, enabled INTEGER DEFAULT 0, recipients TEXT, params TEXT, repeat_hours INTEGER DEFAULT 0,
  resolved INTEGER DEFAULT 1, since TEXT, last_sent TEXT, last_status TEXT);

CREATE TABLE IF NOT EXISTS mail_alert_state(
  alert TEXT, item TEXT, label TEXT, first_seen TEXT, last_sent TEXT, PRIMARY KEY(alert, item));

CREATE TABLE IF NOT EXISTS inventory_data(
  computer_id INTEGER REFERENCES computers(id) ON DELETE CASCADE, category TEXT, json TEXT, updated TEXT,
  PRIMARY KEY(computer_id, category));

CREATE TABLE IF NOT EXISTS scan_history(
  id INTEGER PRIMARY KEY, computer_id INTEGER REFERENCES computers(id) ON DELETE CASCADE,
  ts TEXT, status TEXT, duration_ms INTEGER, error TEXT, user TEXT, warnings TEXT);
CREATE INDEX IF NOT EXISTS ix_sh_c ON scan_history(computer_id);
CREATE INDEX IF NOT EXISTS ix_inv_cat ON inventory_data(category);

CREATE TABLE IF NOT EXISTS custom_scanners(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, type TEXT, target TEXT, extra TEXT, enabled INTEGER DEFAULT 1, created_at TEXT);

CREATE TABLE IF NOT EXISTS scan_profiles(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT, modules TEXT, scanners TEXT, collections TEXT,
  computers TEXT, triggers TEXT, is_default INTEGER DEFAULT 0, enabled INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS profile_scans(
  computer_id INTEGER REFERENCES computers(id) ON DELETE CASCADE,
  profile_id INTEGER REFERENCES scan_profiles(id) ON DELETE CASCADE,
  last_scan TEXT, last_attempt TEXT, status TEXT, pending INTEGER DEFAULT 0, pending_since TEXT,
  PRIMARY KEY(computer_id, profile_id));

CREATE TABLE IF NOT EXISTS profile_trigger_state(
  profile_id INTEGER REFERENCES scan_profiles(id) ON DELETE CASCADE, trigger_id TEXT, last_fired TEXT,
  PRIMARY KEY(profile_id, trigger_id));

CREATE TABLE IF NOT EXISTS custom_fields(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, type TEXT DEFAULT 'text', sort INTEGER DEFAULT 100);

CREATE TABLE IF NOT EXISTS custom_field_values(
  computer_id INTEGER REFERENCES computers(id) ON DELETE CASCADE,
  field_id INTEGER REFERENCES custom_fields(id) ON DELETE CASCADE, value TEXT,
  PRIMARY KEY(computer_id, field_id));

CREATE TABLE IF NOT EXISTS logs(
  id INTEGER PRIMARY KEY, ts TEXT, level TEXT, category TEXT, message TEXT,
  username TEXT, computer_id INTEGER);
CREATE INDEX IF NOT EXISTS ix_logs_ts ON logs(ts);

CREATE TABLE IF NOT EXISTS kiosk_items(
  package_id INTEGER PRIMARY KEY REFERENCES packages(id) ON DELETE CASCADE, enabled INTEGER DEFAULT 1,
  approval INTEGER DEFAULT 0, category TEXT, description TEXT, uninstall_package_id INTEGER, collection_id INTEGER,
  updated_at TEXT);
CREATE TABLE IF NOT EXISTS kiosk_requests(
  id INTEGER PRIMARY KEY, created_at TEXT, username TEXT, display_name TEXT, computer_id INTEGER, computer_name TEXT,
  package_id INTEGER, package_name TEXT, action TEXT, status TEXT, deployment_id INTEGER, scheduled_at TEXT,
  decided_by TEXT, decided_at TEXT, note TEXT);
CREATE INDEX IF NOT EXISTS ix_kr_user ON kiosk_requests(username);
CREATE INDEX IF NOT EXISTS ix_kr_comp ON kiosk_requests(computer_id);

-- 2.18: Reste unterbrochener Läufe auf den Clients (Neustart des Containers) – werden aufgeräumt, sobald erreichbar
CREATE TABLE IF NOT EXISTS client_cleanup(
  id INTEGER PRIMARY KEY, computer_id INTEGER, computer_name TEXT, host TEXT, prefix TEXT NOT NULL, max_minutes INTEGER,
  deployment_id INTEGER, target_id INTEGER, created TEXT, last_try TEXT, attempts INTEGER DEFAULT 0, last_result TEXT);
CREATE INDEX IF NOT EXISTS ix_cc_comp ON client_cleanup(computer_id);

-- 2.21: dauerhafte Platzhalter für anonymisierte Diagnosepakete (PC-017 bleibt derselbe Rechner)
CREATE TABLE IF NOT EXISTS diag_alias(kind TEXT NOT NULL, real TEXT NOT NULL, alias TEXT NOT NULL, created TEXT,
  PRIMARY KEY(kind, real));
CREATE TABLE IF NOT EXISTS version_history(started_at TEXT PRIMARY KEY, version TEXT NOT NULL);

-- 2.26: Bruchprüfung – bestätigter Stand der Repository-Dateien, die Pakete benutzen (Schlüssel: Pfad klein,
-- relativ zum Repository). base_* = bestätigt, cur_* = zuletzt gesehen; full_at = letzte Prüfung des Fingerabdrucks
CREATE TABLE IF NOT EXISTS repo_baseline(
  path TEXT PRIMARY KEY, disp TEXT, size INTEGER, mtime INTEGER, sha256 TEXT, confirmed_at TEXT, confirmed_by TEXT,
  cur_size INTEGER, cur_mtime INTEGER, cur_sha256 TEXT, cur_at TEXT, full_at TEXT);
-- Ordner, deren Dateiliste bestätigt ist (sonst gälte jede Datei darin als „neu hinzugekommen“)
CREATE TABLE IF NOT EXISTS repo_baseline_dirs(path TEXT PRIMARY KEY, confirmed_at TEXT);

-- 2.27: Upload in Stücken (Fortsetzen nach Abbruch), Fingerabdrücke/MSI-Daten von Repository-Dateien,
-- Kennzeichen je Paket für die Duplikatsperre (winget-ID, MSI-Produktcode, SHA-256 der Installationsdatei)
CREATE TABLE IF NOT EXISTS repo_uploads(token TEXT PRIMARY KEY, username TEXT, folder TEXT, name TEXT, size INTEGER,
  received INTEGER DEFAULT 0, overwrite INTEGER DEFAULT 0, allow_dup INTEGER DEFAULT 0, created TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS repo_hash(path TEXT PRIMARY KEY, disp TEXT, size INTEGER, mtime INTEGER, sha256 TEXT,
  msi_product TEXT, msi_version TEXT, at TEXT);
CREATE INDEX IF NOT EXISTS ix_repo_hash_sha ON repo_hash(sha256);
CREATE TABLE IF NOT EXISTS pkg_ident(package_id INTEGER NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL,
  PRIMARY KEY(package_id, kind, value));
CREATE INDEX IF NOT EXISTS ix_pkg_ident_v ON pkg_ident(kind, value);

-- 2.28: Neuigkeiten in der App – je Benutzer die zuletzt angesehene Version
CREATE TABLE IF NOT EXISTS news_seen(username TEXT PRIMARY KEY, version TEXT, at TEXT);

-- 2.32.3: Einstellungen je Benutzer (z. B. Tastenkürzel für Tools) – folgen ihm auf jeden Rechner
CREATE TABLE IF NOT EXISTS user_prefs(username TEXT NOT NULL, key TEXT NOT NULL, value TEXT, at TEXT,
  PRIMARY KEY(username, key));
"""

DEFAULT_SETTINGS = {
    "ad_sync_minutes": 60,
    "ad_filter": "(&(objectClass=computer)(operatingSystem=Windows*))",
    "ad_remove_missing": 0,
    "ad_auto_sync": 1,
    "ad_sync_disabled": 1,
    "ad_ou_collections": 1,
    "ad_group_collections": 1,
    "ad_containers": "",
    "ad_last_sync": "",
    "ad_last_status": "",
    "heartbeat_minutes": 5,
    "scan_online_delay_minutes": 3,  # 2.32.8: Scans für gerade hochgefahrene/aufgewachte Computer erst nach dieser Zeit
    # 2.31: Wake-on-LAN – Pakete je Versuch, Wartezeit auf das Aufwachen (0 = nicht warten), feste Relays (Computernamen)
    "wol_repeat": 3, "wol_wait_seconds": 240, "wol_relays": "",
    "scan_interval_hours": 12,
    "scan_concurrency": 6,
    "deploy_concurrency": 8,
    "deploy_timeout_minutes": 60,
    "rescan_after_deploy": 1,
    # 2.24: Windows-Benachrichtigung für angemeldete Benutzer nach erfolgreicher Installation/Aktualisierung
    "reboot_sender": "", "reboot_msg_restart": "", "reboot_msg_shutdown": "", "reboot_msg_deploy": "", "reboot_msg_step": "",
    "reboot_msg_kiosk": "",
    # 2.26 Bruchprüfung: Vollprüfung der Fingerabdrücke – jede Datei einmal pro Woche, nachts im Zeitfenster, gedrosselt
    # 2.27 Repository-Upload: größte Datei, Standard-Kontingent je Mandant (0 = unbegrenzt), erlaubte Dateitypen für
    # OU-Admins (Hauptadmins dürfen alles hochladen)
    "repo_upload_max_mb": 4096, "repo_tenant_quota_mb": 0,
    # 2.28: Hinweis auf neue Versionen (der Server fragt einmal täglich bei GitHub; Token verschlüsselt als „update_token“)
    "update_check_enabled": 1, "update_repo": "soleil0815/pocketknife",
    "repo_upload_types": "msi,msp,msu,mst,exe,cmd,bat,ps1,psm1,psd1,vbs,reg,zip,7z,cab,msix,msixbundle,appx,appxbundle,"
                         "ini,inf,cat,sys,dll,xml,json,txt,cfg,config,conf,lic,cer,crt,iso,wim,dat,bin,ico,png",
    "integrity_full_enabled": 1, "integrity_full_start": 1, "integrity_full_end": 6, "integrity_full_mbps": 50,
    "toast_enabled": 1, "toast_sender": "Softwareverteilung", "toast_language": "de", "toast_compat": 0,
    "winget_auto_refresh": 1,
    "winget_refresh_hours": 24,
    "winget_scope": "machine",
    "winget_bootstrap": 1,
    "winget_bootstrap_folder": "",
    "winget_cache_folder": "winget-cache",   # 2.35: winget-Cache im Repository (für Computer ohne winget)
    "winget_cache_locale": "de-DE",
    "winget_cache_max_mb": 2048,
    "winget_cache_auto": 1,                  # täglich auf neue Versionen prüfen
    "winget_cache_all": 1,                   # 2.35.2: auch Computer MIT winget installieren aus dem Cache (schneller, LAN)
    "rule_retry_hours": 24,
    "repo_path": "",
    "log_retention_days": 90,
    "deployment_retention_days": 180,
    "software_changes_retention_days": 365,
    "db_maintenance_hour": 3,
    "du_scan_depth": 4,
    "backup_enabled": 1,
    "backup_keep": 10,
    "backup_hour": 2,
    "auth_mode": "",  # leer = PK_WINRM_AUTH aus der .env
    "scan_log_retention_days": 14,
    "low_disk_gb": 10,
    "ldap_cert_fingerprint": "",
    "ldap_cert_fingerprints": "",
    "ldap_cert_pending": "",
    "session_idle_minutes": 30,
    # Software-Kiosk: Nutzer installieren freigegebene Software selbst (nur auf dem Rechner, an dem sie angemeldet sind)
    "kiosk_enabled": 0, "kiosk_groups": "", "kiosk_ous": "", "kiosk_notify": 1, "kiosk_notify_to": "",
    "kiosk_max_per_day": 10, "kiosk_title": "Software-Kiosk", "kiosk_sso": 0,  # automatische Abmeldung bei Inaktivität (5–720 Minuten, immer aktiv)
    "ldap_cert_strict": 1,  # neue Installationen: geänderte DC-Zertifikate erst nach Bestätigung (bestehende behalten ihren Wert)
    "remote_sso": 1,
    "proxy_url": "", "proxy_user": "", "proxy_clients": 0,  # 2.23: Proxyserver (Kennwort verschlüsselt: proxy_password)
    "dns_cache_minutes": 5, "dns_direct": 0, "dns_servers": "",  # 2.20: Namensauflösung (Zwischenspeicher, direkt beim DC)
    "identity_check": 1, "identity_recheck_hours": 24,  # 2.20: antwortet unter der Adresse wirklich dieser Computer?
    "remote_keyboard": "de-de-qwertz",
}

BUILTIN_COLLECTIONS = [
    ("Alle Computer", "all", []),
    ("Online", "all", [{"field": "online", "op": "is_true"}]),
    ("Offline", "all", [{"field": "online", "op": "is_false"}]),
    ("Workstations", "all", [{"field": "os", "op": "not_contains", "value": "Server"}]),
    ("Server", "all", [{"field": "os", "op": "contains", "value": "Server"}]),
    ("Domänencontroller", "all", [{"field": "role", "op": "equals", "value": "Domänencontroller"}]),  # 2.34
    ("Veraltete Software", "all", [{"field": "software_outdated", "op": "is_true"}]),
    ("Neustart ausstehend", "all", [{"field": "pending_reboot", "op": "is_true"}]),
    ("Scan fehlgeschlagen", "all", [{"field": "scan_status", "op": "equals", "value": "Fehler"}]),
    ("Nie gescannt", "all", [{"field": "last_scan", "op": "empty"}]),
    ("Wenig Speicher (C: < 10 GB)", "all", [{"field": "free_c_gb", "op": "lt", "value": "10"}]),
    ("Im AD deaktiviert", "all", [{"field": "ad_enabled", "op": "is_false"}]),
    ("Falsche Adresse", "all", [{"field": "addr_problem", "op": "not_empty"}]),
]


_re_cache = {}


def _pk_regex(value, pattern):
    if value is None or pattern is None:
        return 0
    rx = _re_cache.get(pattern)
    if rx is None:
        import re
        from . import util
        if util.regex_problem(pattern):
            return 0  # gefährliches/ungültiges Muster (z. B. aus älteren Versionen gespeichert) nie ausführen
        try:
            rx = re.compile(pattern, re.I)
        except re.error:
            return 0
        if len(_re_cache) > 500:
            _re_cache.clear()
        _re_cache[pattern] = rx
    return 1 if rx.search(str(value)[:4000]) else 0


def _pk_vcmp(a, b):
    from . import util
    try:
        return util.vcmp(str(a), str(b))
    except Exception:
        return 0


def _register_functions(c):
    """Eigene SQL-Funktionen für Sammlungsfilter (Versionsvergleich, reguläre Ausdrücke)."""
    c.create_function("pk_vcmp", 2, _pk_vcmp, deterministic=True)
    c.create_function("pk_regex", 2, _pk_regex, deterministic=True)


def conn():
    c = getattr(_local, "c", None)
    if c is None:
        c = sqlite3.connect(config.DB_PATH, timeout=30, isolation_level=None, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=30000")
        c.execute("PRAGMA synchronous=NORMAL")
        _register_functions(c)
        _local.c = c
    return c


def q(sql, params=()):
    return [dict(r) for r in conn().execute(sql, params).fetchall()]


def q1(sql, params=()):
    r = conn().execute(sql, params).fetchone()
    return dict(r) if r else None


def val(sql, params=(), default=None):
    r = conn().execute(sql, params).fetchone()
    return r[0] if r and r[0] is not None else default


def ex(sql, params=()):
    return conn().execute(sql, params).lastrowid


def ex_count(sql, params=()):
    """Wie ex(), liefert aber die Anzahl geänderter Zeilen (für bedingte Updates)."""
    return conn().execute(sql, params).rowcount


def exmany(sql, rows):
    conn().executemany(sql, rows)


class tx:
    """Transaktion: with db.tx(): ..."""

    def __enter__(self):
        conn().execute("BEGIN IMMEDIATE")
        return self

    def __exit__(self, et, ev, tb):
        if et is None:
            conn().execute("COMMIT")
        else:
            conn().execute("ROLLBACK")
        return False


# Spalten, die in späteren Versionen hinzugekommen sind (Tabelle, Spalte, Typ)
MIGRATIONS = [
    # 2.25: OU-Admins – Sitzung eines OU-Admins kennt ihre Mandanten (JSON-Liste; leer/NULL = Hauptadmin)
    ("sessions", "scope", "TEXT"),
    ("sessions", "confirmed_at", "REAL"),   # 2.25.1: zuletzt vom AD bestätigt (Unix-Zeit)
    ("tenants", "admin_group", "TEXT"),
    ("tenants", "allow_packages", "INTEGER DEFAULT 0"),
    ("tenants", "admin_group_dn", "TEXT"),     # beim Speichern eindeutig aufgelöst – nie wieder nach Namen suchen
    ("tenants", "ou_ever", "INTEGER DEFAULT 0"),
    ("tenants", "admin_group_sid", "TEXT"),    # SID der Gruppe – übersteht Umbenennen, eine Neuanlage erbt sie nicht  # hatte je OU-Admins → ohne Sammlung keine Computer (nie „alle“)
    ("sessions", "last_active", "TEXT"),
    ("sessions", "role", "TEXT"),
    ("kiosk_items", "icon", "BLOB"),
    ("kiosk_items", "icon_type", "TEXT"),
    ("kiosk_items", "icon_at", "TEXT"),
    ("kiosk_items", "groups", "TEXT"),
    # 2.20: Antwortet unter der Adresse wirklich dieser Computer? (veraltete DNS-Einträge, fremde Geräte)
    ("computers", "resolved_ip", "TEXT"),
    ("computers", "verified_ip", "TEXT"),
    ("computers", "verified_at", "TEXT"),
    ("computers", "addr_problem", "TEXT"),
    ("computers", "addr_detail", "TEXT"),
    ("computers", "addr_ip", "TEXT"),
    ("computers", "addr_checked_at", "TEXT"),  # 2.17: nur für bestimmte AD-Gruppen (eine je Zeile, leer = alle Kiosk-Nutzer)
    ("computers", "ad_missing_since", "TEXT"),
    ("computers", "ad_location", "TEXT"),
    ("services", "description", "TEXT"),
    ("services", "path", "TEXT"),
    ("update_rules", "rule_type", "TEXT DEFAULT 'winget'"),
    ("update_rules", "package_id", "INTEGER"),
    ("software", "hive", "TEXT"),
    ("software", "reg_path", "TEXT"),
    ("software", "install_location", "TEXT"),
    ("software", "size_kb", "INTEGER"),
    ("software", "modified", "TEXT"),
    ("software", "modify_path", "TEXT"),
    ("software", "no_remove", "INTEGER DEFAULT 0"),
    ("scan_history", "profile_id", "INTEGER"),
    ("collections", "ad_dn", "TEXT"),
    ("collections", "ad_kind", "TEXT"),
    ("computers", "scan_allowed", "INTEGER DEFAULT 1"),
    ("scan_history", "profile_name", "TEXT"),
    ("software", "winget_sure", "INTEGER DEFAULT 1"),
    ("winget_cache", "detect_version", "TEXT"),
    ("winget_cache", "ignore_hash", "INTEGER DEFAULT 0"),   # 2.35.3: Prüfsumme des Katalogs nicht verlangen (je Eintrag)
    ("winget_cache", "remote_sig", "TEXT"),                 # 2.35.3: ETag/Datum/Größe der Download-Adresse
    ("software", "excluded", "INTEGER DEFAULT 0"),
    ("scan_profiles", "scanner_types", "TEXT"),
    ("scan_profiles", "builtin_key", "TEXT"),
    ("scan_profiles", "computers", "TEXT"),
    ("packages", "steps", "TEXT"),
    ("packages", "conditions", "TEXT"),
    ("packages", "run_as", "TEXT"),
    ("packages", "tenant_id", "INTEGER"),
    ("packages", "folder_name", "TEXT"),
    ("deployment_targets", "steps", "TEXT"),
    ("deployments", "schedule_id", "INTEGER"),
    ("deployments", "tenant_id", "INTEGER"),
    ("packages", "rescan_profile_id", "INTEGER"),
    ("packages", "notify_user", "TEXT"),
    ("schedules", "kind", "TEXT DEFAULT 'packages'"),
    ("schedules", "winget", "TEXT"),
    ("schedules", "window", "TEXT"),
    ("update_rules", "migrated", "INTEGER DEFAULT 0"),
    ("deployments", "target_label", "TEXT"),
    ("packages", "integrity", "TEXT"),  # 2.26: Ergebnis der Bruchprüfung (JSON: state, issues, at)
    ("packages", "ident_at", "TEXT"),   # 2.27: Kennzeichen für die Duplikatsperre zuletzt berechnet
    ("tenants", "allow_scripts", "INTEGER DEFAULT 0"),  # 2.29.1
    ("tenants", "collection_ids", "TEXT"),
    ("packages", "ou_edit", "INTEGER"),     # 2.30: 1 = OU-Admins des Mandanten dürfen ändern (von ihnen angelegt)
    ("schedules", "ou_edit", "INTEGER"),    # 2.30: wie bei Paketen
    ("collections", "tenant_id", "INTEGER"),  # 2.30: von einem OU-Admin angelegt – gehört diesem Mandanten  # 2.29.9: mehrere Sammlungen je Mandant (JSON-Liste); collection_id = erste  # 2.29.1: OU-Admins dürfen Befehls-/PowerShell-Schritte schreiben
    ("tenants", "quota_mb", "INTEGER"), # 2.27: Speicherkontingent im Repository (leer = Standard aus den Einstellungen)
    ("computers", "online_since", "TEXT"),  # 2.32.8: seit wann WinRM (wieder) erreichbar ist – Scan erst nach kurzer Wartezeit
]


def _migrate(c):
    for table, col, typ in MIGRATIONS:
        cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})")]
        if col not in cols:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")


def _expected_tables():
    """Soll-Aufbau aller Tabellen aus SCHEMA (in einer leeren In-Memory-Datenbank ermittelt)."""
    m = sqlite3.connect(":memory:")
    m.executescript(SCHEMA)
    out = {}
    for (name, sql) in m.execute("SELECT name, sql FROM sqlite_master WHERE type='table'"):
        cols = [r[1] for r in m.execute(f"PRAGMA table_info({name})")]
        fks = sorted((r[3], r[2]) for r in m.execute(f"PRAGMA foreign_key_list({name})"))
        out[name] = (sql, cols, fks)
    m.close()
    return out


def _repair(c):
    """Tabellen reparieren, deren Fremdschlüssel auf falsche Tabellen zeigen oder denen Spalten fehlen
    (z. B. aus Vorabversionen). Die Tabelle wird im Soll-Aufbau neu angelegt, vorhandene Daten werden
    übernommen; Zeilen ohne gültigen Bezug entfallen."""
    from . import pklog
    rebuilt = False
    for name, (sql, cols, fks) in _expected_tables().items():
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone():
            continue
        have_cols = [r[1] for r in c.execute(f"PRAGMA table_info({name})")]
        have_fks = sorted((r[3], r[2]) for r in c.execute(f"PRAGMA foreign_key_list({name})"))
        missing = [x for x in cols if x not in have_cols]
        if have_fks == fks and not missing:
            continue
        tmp = f"_old_{name}"
        c.execute("PRAGMA foreign_keys=OFF")
        c.execute("PRAGMA legacy_alter_table=ON")  # Verweise anderer Tabellen beim Umbenennen nicht umschreiben
        try:
            c.execute("BEGIN")
            c.execute(f"DROP TABLE IF EXISTS {tmp}")
            c.execute(f"ALTER TABLE {name} RENAME TO {tmp}")
            c.execute(sql)
            for table, col, typ in MIGRATIONS:  # später hinzugekommene Spalten wieder ergänzen
                if table == name and col not in [r[1] for r in c.execute(f"PRAGMA table_info({name})")]:
                    c.execute(f"ALTER TABLE {name} ADD COLUMN {col} {typ}")
            new_cols = [r[1] for r in c.execute(f"PRAGMA table_info({name})")]
            common = [x for x in new_cols if x in have_cols]
            if common:
                cl = ", ".join(common)
                c.execute(f"INSERT OR IGNORE INTO {name}({cl}) SELECT {cl} FROM {tmp}")
            c.execute(f"DROP TABLE {tmp}")
            bad = c.execute(f"PRAGMA foreign_key_check({name})").fetchall()
            for row in bad:
                c.execute(f"DELETE FROM {name} WHERE rowid=?", (row[1],))
            c.execute("COMMIT")
            rebuilt = True
            msg = (f"Datenbank repariert: Tabelle „{name}“ neu aufgebaut"
                   + (f" (Bezüge vorher: {', '.join(t for _c, t in have_fks)})" if have_fks != fks else "")
                   + (f", ergänzte Spalten: {', '.join(missing)}" if missing else "")
                   + (f", {len(bad)} Zeile(n) ohne gültigen Bezug entfernt" if bad else "") + ".")
            try:
                pklog.warn("system", msg)
            except Exception:
                print(msg, flush=True)
        except Exception as e:
            c.execute("ROLLBACK")
            print(f"Reparatur der Tabelle {name} fehlgeschlagen: {e}", flush=True)
        finally:
            c.execute("PRAGMA legacy_alter_table=OFF")
            c.execute("PRAGMA foreign_keys=ON")
    if rebuilt:
        c.executescript(SCHEMA)  # Indizes der neu aufgebauten Tabellen


def init():
    config.ensure_dirs()
    c = conn()
    c.executescript(SCHEMA)
    _migrate(c)
    _repair(c)
    c.execute("DELETE FROM inventory_data WHERE category IN ('video', 'profiles', 'startup')")
    for k, v in DEFAULT_SETTINGS.items():
        c.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (k, str(v)))
    if not val("SELECT COUNT(*) FROM collections WHERE builtin=1", default=0):
        folder = ex("INSERT INTO collections(name, type, builtin, sort) VALUES('Standard', 'folder', 1, 0)")
        for i, (name, match, flt) in enumerate(BUILTIN_COLLECTIONS):
            ex("INSERT INTO collections(name, parent_id, type, match, filters, builtin, sort) VALUES(?,?,?,?,?,1,?)",
               (name, folder, "dynamic", match, json.dumps(flt), i))
    else:
        # Standardsammlungen, die mit späteren Versionen hinzugekommen sind, ergänzen
        folder = val("SELECT id FROM collections WHERE builtin=1 AND type='folder' ORDER BY id LIMIT 1")
        for i, (name, match, flt) in enumerate(BUILTIN_COLLECTIONS):
            if folder and not val("SELECT COUNT(*) FROM collections WHERE builtin=1 AND name=?", (name,), 0):
                ex("INSERT INTO collections(name, parent_id, type, match, filters, builtin, sort) VALUES(?,?,?,?,?,1,?)",
                   (name, folder, "dynamic", match, json.dumps(flt), i))
    from . import profiles
    profiles.init()
    profiles.seed_builtin()
    from . import schedules
    schedules.migrate_rules()
    schedules.ensure_baselines()
    _migrate_ownership()


def _migrate_ownership():
    """2.30 (einmalig): Wer darf vorhandene Mandanten-Pakete und -Zeitpläne ändern? Bisher jeder OU-Admin des Mandanten.
    Jetzt nur, was ein OU-Admin selbst angelegt oder geändert hat – erkannt am Protokoll (Anmeldung „(OU-Admin: …)“,
    „Paket 'X' angelegt/geändert.“) bzw. am Ersteller des Zeitplans. Alles andere gilt als vom Hauptadmin angelegt
    (nur lesbar für OU-Admins); der Hauptadmin kann es je Paket/Zeitplan mit „OU-Admins dürfen ändern“ freigeben."""
    if val("SELECT value FROM settings WHERE key='ou_owner_migrated'"):
        return
    ou = {r["username"].lower() for r in q("SELECT DISTINCT username FROM logs WHERE category='auth' AND message LIKE '%(OU-Admin:%' "
                                           "AND username IS NOT NULL")}
    ou |= {r["username"].lower() for r in q("SELECT DISTINCT username FROM sessions WHERE scope IS NOT NULL AND username IS NOT NULL")}
    with tx():
        if ou:
            for r in q("SELECT id, created_by FROM schedules WHERE tenant_id IS NOT NULL AND ou_edit IS NULL"):
                if (r["created_by"] or "").lower() in ou:
                    ex("UPDATE schedules SET ou_edit=1 WHERE id=?", (r["id"],))
            touched = {}
            for r in q("SELECT username, message FROM logs WHERE category='deploy' AND (message LIKE 'Paket ''%'' angelegt.' "
                       "OR message LIKE 'Paket ''%'' geändert.') AND username IS NOT NULL"):
                if r["username"].lower() in ou:
                    m = r["message"]
                    touched[m[len("Paket '"):m.rfind("'")].lower()] = True
            for r in q("SELECT id, name FROM packages WHERE tenant_id IS NOT NULL AND ou_edit IS NULL"):
                if (r["name"] or "").lower() in touched:
                    ex("UPDATE packages SET ou_edit=1 WHERE id=?", (r["id"],))
        ex("UPDATE schedules SET ou_edit=0 WHERE ou_edit IS NULL")
        ex("UPDATE packages SET ou_edit=0 WHERE ou_edit IS NULL")
        ex("INSERT OR REPLACE INTO settings(key, value) VALUES('ou_owner_migrated', '1')")


# --- Einstellungen --------------------------------------------------------
def setting(key):
    raw = val("SELECT value FROM settings WHERE key=?", (key,))
    default = DEFAULT_SETTINGS.get(key)
    if raw is None:
        return default
    if isinstance(default, int):
        try:
            return int(float(raw))
        except ValueError:
            return default
    return raw


def all_settings():
    out = {}
    for k in DEFAULT_SETTINGS:
        out[k] = setting(k)
    return out


def stats():
    """Größe der Datenbank und Anteil freier (wiederverwendbarer) Seiten."""
    import os
    c = conn()
    page = c.execute("PRAGMA page_size").fetchone()[0]
    pages = c.execute("PRAGMA page_count").fetchone()[0]
    free = c.execute("PRAGMA freelist_count").fetchone()[0]
    wal = config.DB_PATH + "-wal"
    return {"size": page * pages, "free": page * free, "free_pct": round(100.0 * free / pages, 1) if pages else 0,
            "wal": os.path.getsize(wal) if os.path.exists(wal) else 0}


def maintenance(vacuum=None):
    """Statistiken aktualisieren, WAL zurückschreiben und – bei viel freiem Platz oder auf Wunsch – VACUUM
    (verkleinert die Datei). vacuum=None: automatisch ab 20 % freien Seiten."""
    import os
    import shutil
    import time
    before = stats()
    c = conn()
    c.execute("PRAGMA optimize")
    do_vac = vacuum if vacuum is not None else before["free_pct"] >= 20
    note = ""
    if do_vac:
        free_disk = shutil.disk_usage(os.path.dirname(config.DB_PATH)).free
        if free_disk < before["size"] * 1.2:
            do_vac, note = False, "VACUUM übersprungen: zu wenig freier Speicher im Datenverzeichnis"
    t0 = time.time()
    if do_vac:
        c.execute("VACUUM")
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    after = stats()
    return {"before": before, "after": after, "vacuum": bool(do_vac), "seconds": round(time.time() - t0, 1), "note": note}


def setting_raw(key):
    """Wert ohne Standardwert/Typumwandlung (für Einträge außerhalb von DEFAULT_SETTINGS)."""
    return val("SELECT value FROM settings WHERE key=?", (key,), "") or ""


def set_setting(key, value):
    ex("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
       (key, str(value)))
