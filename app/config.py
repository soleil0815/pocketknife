"""Central configuration for POCKET KNIFE (from environment variables)."""
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
    """Reads a variable; KEY_FILE (Docker secrets) takes precedence."""
    fpath = os.environ.get(key + "_FILE")
    if fpath and os.path.exists(fpath):
        with open(fpath, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    return os.environ.get(key, default)


def _bool(value, default=False):
    if value is None or value == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on", "ja")


# --- Paths ---------------------------------------------------------------
DATA_DIR = _env("PK_DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "pocketknife.db")
LOG_DIR = os.path.join(DATA_DIR, "logs")
WINGET_DIR = os.path.join(DATA_DIR, "winget")
TLS_DIR = os.path.join(DATA_DIR, "tls")
REPORT_DIR = os.path.join(DATA_DIR, "reports")
TMP_DIR = os.path.join(DATA_DIR, "tmp")  # temporary files (TMPDIR in the container) - the root file system is read-only
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# --- Domain / LDAPS --------------------------------------------------------
DOMAIN = _env("PK_DOMAIN").strip()                      # e.g. firma.local
NETBIOS = _env("PK_NETBIOS").strip()                    # e.g. FIRMA
LDAP_SERVERS = [s.strip() for s in _env("PK_LDAP_SERVERS", DOMAIN).split(",") if s.strip()]
LDAP_PORT = int(_env("PK_LDAP_PORT", "636") or 636)
BASE_DN = _env("PK_BASE_DN").strip() or ",".join("DC=" + p for p in DOMAIN.split(".") if p)
# separate multiple search bases for computers with ";"
COMPUTER_BASE_DNS = [s.strip() for s in (_env("PK_COMPUTER_BASE_DN") or BASE_DN).split(";") if s.strip()]
ACCESS_GROUP = _env("PK_ACCESS_GROUP", "POCKETKNIFE-Admins").strip()

# --- Dienstkonto (LDAP-Bind + lokaler Admin auf den Clients) ---------------
# Preferably maintained in the UI (Einstellungen -> Dienstkonto, encrypted in the DB).
# The values from the .env only apply as long as nothing is configured there.
ENV_SERVICE_USER = _env("PK_SERVICE_USER").strip()
ENV_SERVICE_PASSWORD = _env("PK_SERVICE_PASSWORD")

# --- WinRM -----------------------------------------------------------------
WINRM_AUTH = _env("PK_WINRM_AUTH", "ntlm").strip().lower()     # ntlm | negotiate | kerberos (credssp: no longer supported since 2.29)
WINRM_HTTPS = _bool(_env("PK_WINRM_HTTPS"), False)
WINRM_PORT = int(_env("PK_WINRM_PORT", "5986" if WINRM_HTTPS else "5985"))
# Optional with HTTPS: CA of the domain (PEM) - then the clients' WinRM certificates are verified
WINRM_CA_FILE = _env("PK_WINRM_CA_FILE").strip()

# --- Software repository ---------------------------------------------------
ENV_REPO_USER = _env("PK_REPO_USER").strip()
ENV_REPO_PASSWORD = _env("PK_REPO_PASSWORD")

# --- Web server -------------------------------------------------------------
HTTPS = _bool(_env("PK_HTTPS"), True)
PORT = int(_env("PK_PORT", "8443"))
PUBLIC_HOSTNAME = _env("PK_PUBLIC_HOSTNAME", "pocketknife").strip()
SESSION_HOURS = int(_env("PK_SESSION_HOURS", "10"))
SECURE_COOKIE = _bool(_env("PK_SECURE_COOKIE"), HTTPS)
# Optional emergency access (only if both are set)
BREAKGLASS_USER = _env("PK_BREAKGLASS_USER").strip()
BREAKGLASS_PASSWORD = _env("PK_BREAKGLASS_PASSWORD")


# --- Remote sessions (RDP/VNC in the browser via Apache Guacamole guacd) ---------
GUACD_HOST = _env("PK_GUACD_HOST", "guacd").strip()
GUACD_PORT = int(_env("PK_GUACD_PORT", "4822") or 4822)


def _setting(key):
    try:
        from . import db
        return db.val("SELECT value FROM settings WHERE key=?", (key,)) or ""
    except Exception:
        return ""


def service_source():
    """'ui' (user interface), 'env' (.env) or '' (not set up)."""
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
    """Name for the LDAP bind (UPN preferred)."""
    return _qualify(service_user(), prefer_upn=True)


def service_login():
    """Name for WinRM/SMB (DOMAIN\\user preferred)."""
    return _qualify(service_user())


def repo_login():
    return _qualify(repo_user())


def ensure_dirs():
    for d in (DATA_DIR, LOG_DIR, WINGET_DIR, TLS_DIR, REPORT_DIR, TMP_DIR):
        os.makedirs(d, exist_ok=True)
