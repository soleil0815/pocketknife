"""Zentrale Konfiguration für POCKET KNIFE (aus Umgebungsvariablen)."""
import os

APP_NAME = "POCKET KNIFE"


def _read_version():
    for p in (os.path.join(os.path.dirname(__file__), "..", "VERSION"), "/opt/pocketknife/VERSION"):
        try:
            with open(p, "r", encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError:
            continue
    return "0.0"


VERSION = _read_version()


def _env(key, default=""):
    """Liest eine Variable; KEY_FILE (Docker Secrets) hat Vorrang."""
    fpath = os.environ.get(key + "_FILE")
    if fpath and os.path.exists(fpath):
        with open(fpath, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    return os.environ.get(key, default)


def _bool(value, default=False):
    if value is None or value == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on", "ja")


# --- Pfade ---------------------------------------------------------------
DATA_DIR = _env("PK_DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "pocketknife.db")
LOG_DIR = os.path.join(DATA_DIR, "logs")
WINGET_DIR = os.path.join(DATA_DIR, "winget")
TLS_DIR = os.path.join(DATA_DIR, "tls")
REPORT_DIR = os.path.join(DATA_DIR, "reports")
UPDATE_DIR = os.path.join(DATA_DIR, "update")  # 2.32.9: Update-Auftrag an den Helfer auf dem Docker-Host (app/selfupdate.py)
TMP_DIR = os.path.join(DATA_DIR, "tmp")  # Zwischendateien (TMPDIR im Container) – das Root-Dateisystem ist schreibgeschützt
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# --- Domäne / LDAPS --------------------------------------------------------
DOMAIN = _env("PK_DOMAIN").strip()                      # z.B. firma.local
NETBIOS = _env("PK_NETBIOS").strip()                    # z.B. FIRMA
LDAP_SERVERS = [s.strip() for s in _env("PK_LDAP_SERVERS", DOMAIN).split(",") if s.strip()]
LDAP_PORT = int(_env("PK_LDAP_PORT", "636") or 636)
BASE_DN = _env("PK_BASE_DN").strip() or ",".join("DC=" + p for p in DOMAIN.split(".") if p)
# Mehrere Such-Basen für Computer mit ";" trennen
COMPUTER_BASE_DNS = [s.strip() for s in (_env("PK_COMPUTER_BASE_DN") or BASE_DN).split(";") if s.strip()]
ACCESS_GROUP = _env("PK_ACCESS_GROUP", "POCKETKNIFE-Admins").strip()

# --- Dienstkonto (LDAP-Bind + lokaler Admin auf den Clients) ---------------
# Wird bevorzugt in der Oberfläche gepflegt (Einstellungen → Dienstkonto, verschlüsselt in der DB).
# Die Werte aus der .env gelten nur, solange dort nichts eingetragen ist.
ENV_SERVICE_USER = _env("PK_SERVICE_USER").strip()
ENV_SERVICE_PASSWORD = _env("PK_SERVICE_PASSWORD")

# --- WinRM -----------------------------------------------------------------
WINRM_AUTH = _env("PK_WINRM_AUTH", "ntlm").strip().lower()     # ntlm | negotiate | kerberos (credssp: seit 2.29 nicht mehr)
WINRM_HTTPS = _bool(_env("PK_WINRM_HTTPS"), False)
WINRM_PORT = int(_env("PK_WINRM_PORT", "5986" if WINRM_HTTPS else "5985"))
# Optional bei HTTPS: CA der Domäne (PEM) – dann werden die WinRM-Zertifikate der Clients geprüft
WINRM_CA_FILE = _env("PK_WINRM_CA_FILE").strip()

# --- Software-Repository ---------------------------------------------------
ENV_REPO_USER = _env("PK_REPO_USER").strip()
ENV_REPO_PASSWORD = _env("PK_REPO_PASSWORD")

# --- Webserver -------------------------------------------------------------
HTTPS = _bool(_env("PK_HTTPS"), True)
PORT = int(_env("PK_PORT", "8443"))
PUBLIC_HOSTNAME = _env("PK_PUBLIC_HOSTNAME", "pocketknife").strip()
SESSION_HOURS = int(_env("PK_SESSION_HOURS", "10"))
SECURE_COOKIE = _bool(_env("PK_SECURE_COOKIE"), HTTPS)
# Optionaler Notfallzugang (nur wenn beide gesetzt sind)
BREAKGLASS_USER = _env("PK_BREAKGLASS_USER").strip()
BREAKGLASS_PASSWORD = _env("PK_BREAKGLASS_PASSWORD")


# --- Fernsitzungen (RDP/VNC im Browser über Apache Guacamole guacd) ---------
GUACD_HOST = _env("PK_GUACD_HOST", "guacd").strip()
GUACD_PORT = int(_env("PK_GUACD_PORT", "4822") or 4822)


def _setting(key):
    try:
        from . import db
        return db.val("SELECT value FROM settings WHERE key=?", (key,)) or ""
    except Exception:
        return ""


def service_source():
    """'ui' (Oberfläche), 'env' (.env) oder '' (nicht eingerichtet)."""
    if _setting("svc_user"):
        return "ui"
    return "env" if ENV_SERVICE_USER else ""


def service_user():
    return (_setting("svc_user") or ENV_SERVICE_USER).strip()


def service_password():
    if _setting("svc_user"):
        from . import vault
        return vault.decrypt(_setting("svc_password"))
    return ENV_SERVICE_PASSWORD


def repo_user():
    return (_setting("repo_user") or ENV_REPO_USER or service_user()).strip()


def repo_password():
    if _setting("repo_user"):
        from . import vault
        return vault.decrypt(_setting("repo_password"))
    if ENV_REPO_USER:
        return ENV_REPO_PASSWORD
    return service_password()


def ldap_configured():
    return bool(LDAP_SERVERS and BASE_DN and service_user() and service_password())


def _qualify(u, prefer_upn=False):
    if not u or "@" in u or "\\" in u:
        return u
    if prefer_upn and DOMAIN:
        return f"{u}@{DOMAIN}"
    if NETBIOS:
        return f"{NETBIOS}\\{u}"
    return f"{u}@{DOMAIN}" if DOMAIN else u


def service_upn():
    """Name für den LDAP-Bind (UPN bevorzugt)."""
    return _qualify(service_user(), prefer_upn=True)


def service_login():
    """Name für WinRM/SMB (DOMÄNE\\Benutzer bevorzugt)."""
    return _qualify(service_user())


def repo_login():
    return _qualify(repo_user())


def ensure_dirs():
    for d in (DATA_DIR, LOG_DIR, WINGET_DIR, TLS_DIR, REPORT_DIR, TMP_DIR, UPDATE_DIR):
        os.makedirs(d, exist_ok=True)
