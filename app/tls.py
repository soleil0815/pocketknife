"""Zertifikat der Weboberfläche: selbstsigniert, eigenes Zertifikat (PFX/PEM) oder Zertifikatsanforderung (CSR).

Dateien unter /data/tls:
  server.crt   Serverzertifikat inkl. Zwischenzertifikate (PEM, Reihenfolge: Server → Zwischen-CAs)
  server.key   privater Schlüssel (PEM, nur für root lesbar)
  pending.key  Schlüssel einer offenen Zertifikatsanforderung (bis das signierte Zertifikat eingespielt ist)
  pending.csr  die offene Anforderung
  backup/      die letzten Zertifikate vor einem Wechsel

Ein neues Zertifikat wird ohne Neustart aktiv: der laufende TLS-Kontext von uvicorn lädt die Dateien neu
(gilt für alle neuen Verbindungen).
"""
import datetime
import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import ssl

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from . import config, pklog

CERT = os.path.join(config.TLS_DIR, "server.crt")
KEY = os.path.join(config.TLS_DIR, "server.key")
PENDING_KEY = os.path.join(config.TLS_DIR, "pending.key")
PENDING_CSR = os.path.join(config.TLS_DIR, "pending.csr")
META = os.path.join(config.TLS_DIR, "source.json")
BACKUP_DIR = os.path.join(config.TLS_DIR, "backup")
SELF_ORG = "POCKET KNIFE"
MAX_UPLOAD = 256 * 1024

_ctx = None      # ssl.SSLContext, mit dem uvicorn lauscht (von run.py registriert)
_active = None   # aktuell ausgelieferter Kontext (nach einem Wechsel ein neuer)


class CertError(ValueError):
    pass


def _new_context():
    c = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    c.minimum_version = ssl.TLSVersion.TLSv1_2
    c.load_cert_chain(CERT, KEY)
    return c


def register(ctx):
    """Der Kontext des Webservers bleibt bestehen; bei jedem Handshake wird per SNI-Rückruf auf den
    aktuellen Kontext umgeschaltet. So wird ein neues Zertifikat sofort verwendet – auch beim Wechsel
    zwischen RSA und ECDSA (ein bestehender Kontext würde den alten Schlüsseltyp zusätzlich behalten)."""
    global _ctx, _active
    _ctx = ctx
    _active = None
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2

    def _sni(sslobj, _name, _orig):
        if _active is not None:
            sslobj.context = _active
    ctx.sni_callback = _sni


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def default_names():
    names = [config.PUBLIC_HOSTNAME]
    short = config.PUBLIC_HOSTNAME.split(".")[0]
    if config.DOMAIN and "." not in config.PUBLIC_HOSTNAME:
        names.append(f"{config.PUBLIC_HOSTNAME}.{config.DOMAIN}")
    if short and short != config.PUBLIC_HOSTNAME:
        names.append(short)
    return [n for i, n in enumerate(names) if n and n not in names[:i]]


def _san(names):
    out = []
    for n in names:
        n = n.strip()
        if not n:
            continue
        try:
            out.append(x509.IPAddress(ipaddress.ip_address(n)))
        except ValueError:
            if not re.match(r"^(\*\.)?[A-Za-z0-9]([A-Za-z0-9-]{0,62})(\.[A-Za-z0-9]([A-Za-z0-9-]{0,62}))*$", n):
                raise CertError(f"Ungültiger Name: {n}")
            out.append(x509.DNSName(n))
    if not out:
        raise CertError("Mindestens ein DNS-Name oder eine IP-Adresse angeben.")
    return out


def _write(path, data, private=False):
    tmp = path + ".tmp"
    try:
        os.remove(tmp)
    except OSError:
        pass
    # Schlüssel gleich mit 0600 anlegen (nicht erst schreiben und dann einschränken)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600 if private else 0o644)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def _key_pem(key):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def _meta(source, **extra):
    _write(META, json.dumps({"source": source, "installed": _now().isoformat(timespec="seconds"), **extra}).encode())


# ---------------------------------------------------------------------------
# Selbstsigniert
# ---------------------------------------------------------------------------
def _self_signed():
    pk = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    names = default_names() + [n for n in (socket.gethostname(), "localhost") if n]
    names = [n for i, n in enumerate(names) if n not in names[:i]]
    san = _san(names) + [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, config.PUBLIC_HOSTNAME[:64]),
                         x509.NameAttribute(NameOID.ORGANIZATION_NAME, SELF_ORG)])
    now = _now()
    crt = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(pk.public_key())
           .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
           .not_valid_after(now + datetime.timedelta(days=3650))
           .add_extension(x509.SubjectAlternativeName(san), critical=False)
           .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
           .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
           .sign(pk, hashes.SHA256()))
    return pk, crt


def _pair_ok(cert, key):
    try:
        ssl.create_default_context(ssl.Purpose.CLIENT_AUTH).load_cert_chain(cert, key)
        return True
    except (ssl.SSLError, OSError, ValueError):
        return False


def ensure():
    """Beim Start: vorhandenes Zertifikat verwenden oder ein selbstsigniertes erzeugen."""
    config.ensure_dirs()
    if os.path.exists(CERT) and os.path.exists(KEY):
        if _pair_ok(CERT, KEY):
            return CERT, KEY
        # Zertifikat und Schlüssel passen nicht (z. B. Absturz während des Austauschs): letzte gute Sicherung nehmen
        for d in sorted(os.listdir(BACKUP_DIR), reverse=True) if os.path.isdir(BACKUP_DIR) else []:
            c, k = os.path.join(BACKUP_DIR, d, "server.crt"), os.path.join(BACKUP_DIR, d, "server.key")
            if os.path.exists(c) and os.path.exists(k) and _pair_ok(c, k):
                shutil.copy2(k, KEY)
                os.chmod(KEY, 0o600)
                shutil.copy2(c, CERT)
                print(f"HTTPS-Zertifikat war unbrauchbar – Sicherung {d} wiederhergestellt.", flush=True)
                return CERT, KEY
        print("HTTPS-Zertifikat war unbrauchbar – neues selbstsigniertes Zertifikat erzeugt.", flush=True)
    pk, crt = _self_signed()
    _write(KEY, _key_pem(pk), private=True)
    _write(CERT, crt.public_bytes(serialization.Encoding.PEM))
    _meta("self")
    return CERT, KEY


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------
def _load_certs(data):
    """PEM (auch mehrere) oder DER."""
    if not data:
        return []
    if b"-----BEGIN" in data:
        try:
            return x509.load_pem_x509_certificates(data)
        except ValueError as e:
            raise CertError(f"Zertifikat nicht lesbar: {e}")
    try:
        return [x509.load_der_x509_certificate(data)]
    except ValueError:
        try:  # PKCS#7 (.p7b) als DER
            from cryptography.hazmat.primitives.serialization import pkcs7
            return pkcs7.load_der_pkcs7_certificates(data)
        except Exception:
            raise CertError("Zertifikat nicht lesbar (erwartet PEM, DER/CER oder P7B).")


def _load_key(data, password):
    pw = password.encode() if password else None
    try:
        if b"-----BEGIN" in data:
            return serialization.load_pem_private_key(data, pw)
        return serialization.load_der_private_key(data, pw)
    except TypeError:
        raise CertError("Der Schlüssel ist verschlüsselt – Kennwort angeben." if not pw else "Kennwort für den Schlüssel ist falsch.")
    except ValueError:
        raise CertError("Schlüssel nicht lesbar oder Kennwort falsch.")


def _pub(k):
    return k.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


def _cert_pub(c):
    return c.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


def _names(c):
    try:
        ext = c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        return [str(v) for v in ext.get_values_for_type(x509.DNSName)] + [str(v) for v in ext.get_values_for_type(x509.IPAddress)]
    except x509.ExtensionNotFound:
        return []


def _cn(name):
    v = name.get_attributes_for_oid(NameOID.COMMON_NAME)
    return v[0].value if v else name.rfc4514_string()


def _matches(host, names):
    host = host.lower()
    for n in names:
        n = n.lower()
        if n == host or (n.startswith("*.") and host.count(".") >= 2 and host.split(".", 1)[1] == n[2:]):
            return True
    return False


def _after(c):
    return getattr(c, "not_valid_after_utc", None) or c.not_valid_after.replace(tzinfo=datetime.timezone.utc)


def _before(c):
    return getattr(c, "not_valid_before_utc", None) or c.not_valid_before.replace(tzinfo=datetime.timezone.utc)


def _describe(c):
    after = _after(c)
    try:
        eku = [o.dotted_string for o in c.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value]
    except x509.ExtensionNotFound:
        eku = None
    pk = c.public_key()
    if isinstance(pk, rsa.RSAPublicKey):
        ktype = f"RSA {pk.key_size} Bit"
    elif isinstance(pk, ec.EllipticCurvePublicKey):
        ktype = f"ECDSA {pk.curve.name}"
    else:
        ktype = type(pk).__name__
    return {
        "subject": c.subject.rfc4514_string(), "cn": _cn(c.subject), "issuer": c.issuer.rfc4514_string(), "issuer_cn": _cn(c.issuer),
        "names": _names(c), "not_before": _before(c).isoformat(timespec="seconds"), "not_after": after.isoformat(timespec="seconds"),
        "days_left": (after - _now()).days, "serial": format(c.serial_number, "X"),
        "sha256": hashlib.sha256(c.public_bytes(serialization.Encoding.DER)).hexdigest().upper(),
        "server_auth": eku is None or ExtendedKeyUsageOID.SERVER_AUTH.dotted_string in eku, "key": ktype,
        "self_signed": c.issuer == c.subject,
    }


def _order(leaf, others):
    """Kette ab dem Serverzertifikat aufbauen (Wurzel-CA wird mitgeliefert, schadet aber nicht)."""
    chain, cur, rest = [leaf], leaf, [o for o in others if o != leaf]
    while rest:
        nxt = next((o for o in rest if o.subject == cur.issuer), None)
        if not nxt or nxt.subject == cur.subject:
            break
        chain.append(nxt); rest.remove(nxt); cur = nxt
    return chain, rest


def info():
    out = {"https": config.HTTPS, "port": config.PORT, "hostname": config.PUBLIC_HOSTNAME, "live_reload": _ctx is not None,
           "default_names": default_names()}
    try:
        meta = json.load(open(META))
    except (OSError, ValueError):
        meta = {}
    try:
        certs = _load_certs(open(CERT, "rb").read())
    except (OSError, CertError) as e:
        out["error"] = str(e)
        certs = []
    if certs:
        d = _describe(certs[0])
        is_self = d["self_signed"] and SELF_ORG in d["subject"]
        out.update(cert=d, chain=[_describe(c) for c in certs[1:]],
                   source=meta.get("source") or ("self" if is_self else "custom"), installed=meta.get("installed"),
                   hostname_ok=_matches(config.PUBLIC_HOSTNAME, d["names"]) if d["names"] else d["cn"].lower() == config.PUBLIC_HOSTNAME.lower())
    if os.path.exists(PENDING_CSR):
        try:
            req = x509.load_pem_x509_csr(open(PENDING_CSR, "rb").read())
            try:
                ext = req.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
                n = [str(v) for v in ext.get_values_for_type(x509.DNSName)] + [str(v) for v in ext.get_values_for_type(x509.IPAddress)]
            except x509.ExtensionNotFound:
                n = []
            out["pending"] = {"subject": req.subject.rfc4514_string(), "names": n, "csr": open(PENDING_CSR).read(),
                              "created": datetime.datetime.fromtimestamp(os.path.getmtime(PENDING_CSR)).isoformat(timespec="seconds")}
        except (OSError, ValueError):
            pass
    try:
        out["backups"] = sorted(os.listdir(BACKUP_DIR), reverse=True)[:5]
    except OSError:
        out["backups"] = []
    return out


def current_pem():
    return open(CERT, "rb").read()


# ---------------------------------------------------------------------------
# Prüfen und installieren
# ---------------------------------------------------------------------------
def _check(key, certs):
    if not certs:
        raise CertError("Kein Zertifikat gefunden.")
    kp = _pub(key)
    leaf = next((c for c in certs if _cert_pub(c) == kp), None)
    if not leaf:
        raise CertError("Der private Schlüssel passt zu keinem der Zertifikate.")
    chain, extra = _order(leaf, certs)
    d = _describe(leaf)
    warn = []
    now = _now()
    if _after(leaf) < now:
        raise CertError(f"Das Zertifikat ist abgelaufen (gültig bis {d['not_after'][:10]}).")
    if _before(leaf) > now + datetime.timedelta(minutes=5):
        raise CertError(f"Das Zertifikat ist erst ab {d['not_before'][:10]} gültig.")
    if not d["server_auth"]:
        raise CertError("Das Zertifikat ist nicht für die Serverauthentifizierung (Server Authentication) ausgestellt.")
    if isinstance(key, rsa.RSAPrivateKey) and key.key_size < 2048:
        raise CertError("RSA-Schlüssel mit weniger als 2048 Bit werden von Browsern abgelehnt.")
    if d["days_left"] < 30:
        warn.append(f"Läuft in {d['days_left']} Tagen ab.")
    if d["names"] and not _matches(config.PUBLIC_HOSTNAME, d["names"]):
        warn.append(f"PK_PUBLIC_HOSTNAME „{config.PUBLIC_HOSTNAME}“ ist nicht im Zertifikat enthalten ({', '.join(d['names'][:6])}) – "
                    "Browser warnen, wenn die Seite unter diesem Namen aufgerufen wird.")
    if not d["names"]:
        warn.append("Das Zertifikat enthält keine alternativen Namen (SAN) – aktuelle Browser akzeptieren nur den SAN, nicht den CN.")
    if not d["self_signed"] and len(chain) == 1:
        warn.append("Keine Zwischenzertifikate mitgeliefert – falls die ausstellende CA eine Zwischen-CA ist, diese mit hochladen.")
    # Probe: lässt sich die Kombination in einen TLS-Kontext laden?
    pem = b"".join(c.public_bytes(serialization.Encoding.PEM) for c in chain)
    tmpc, tmpk = CERT + ".probe", KEY + ".probe"
    try:
        _write(tmpc, pem); _write(tmpk, _key_pem(key), private=True)
        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(tmpc, tmpk)
    except ssl.SSLError as e:
        raise CertError(f"TLS kann das Zertifikat nicht laden: {e}")
    finally:
        for f in (tmpc, tmpk):
            try:
                os.remove(f)
            except OSError:
                pass
    return leaf, chain, pem, d, warn


def parse(kind, data, password="", key_data=None, key_password=""):
    """kind = pfx | pem. data/key_data = Bytes. Liefert (key, certs)."""
    if len(data or b"") > MAX_UPLOAD or len(key_data or b"") > MAX_UPLOAD:
        raise CertError("Datei zu groß.")
    if kind == "pfx":
        try:
            key, cert, more = pkcs12.load_key_and_certificates(data, password.encode() if password else None)
        except ValueError:
            raise CertError("PFX-Datei nicht lesbar oder Kennwort falsch.")
        if not key or not cert:
            raise CertError("Die PFX-Datei enthält kein Zertifikat mit privatem Schlüssel.")
        return key, [cert] + list(more or [])
    certs = _load_certs(data)
    if key_data and key_data.strip():
        key = _load_key(key_data, key_password)
    elif b"PRIVATE KEY" in data:
        key = _load_key(data, key_password)
    elif os.path.exists(PENDING_KEY):
        key = _load_key(open(PENDING_KEY, "rb").read(), "")
    else:
        raise CertError("Privaten Schlüssel angeben (oder vorher eine Zertifikatsanforderung erstellen).")
    if not certs:
        raise CertError("Kein Zertifikat gefunden.")
    return key, certs


def preview(kind, data, password="", key_data=None, key_password=""):
    key, certs = parse(kind, data, password, key_data, key_password)
    _leaf, chain, _pem, d, warn = _check(key, certs)
    return {"cert": d, "chain": [_describe(c) for c in chain[1:]], "warnings": warn}


def _backup():
    if not (os.path.exists(CERT) and os.path.exists(KEY)):
        return
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(BACKUP_DIR, ts)
    n = 1
    while os.path.exists(dst):
        n += 1
        dst = os.path.join(BACKUP_DIR, f"{ts}-{n}")
    os.makedirs(dst, exist_ok=True)
    os.chmod(BACKUP_DIR, 0o700)
    shutil.copy2(CERT, os.path.join(dst, "server.crt"))
    shutil.copy2(KEY, os.path.join(dst, "server.key"))
    os.chmod(os.path.join(dst, "server.key"), 0o600)
    for old in sorted(os.listdir(BACKUP_DIR))[:-5]:
        shutil.rmtree(os.path.join(BACKUP_DIR, old), ignore_errors=True)


def reload():
    """Neues Zertifikat im laufenden Webserver aktivieren. True = sofort aktiv."""
    global _active
    if _ctx is None:
        return False
    _active = _new_context()
    return True


def _activate(key, pem, source, user):
    _backup()
    _write(KEY, _key_pem(key), private=True)
    _write(CERT, pem)
    _meta(source, user=user)
    live = False
    try:
        live = reload()
    except ssl.SSLError as e:
        pklog.error("system", f"Neues Zertifikat konnte nicht live geladen werden: {e}")
    return live


def install(kind, data, password="", key_data=None, key_password="", user=""):
    key, certs = parse(kind, data, password, key_data, key_password)
    leaf, _chain, pem, d, warn = _check(key, certs)
    used_pending = os.path.exists(PENDING_KEY) and _pub(_load_key(open(PENDING_KEY, "rb").read(), "")) == _pub(key)
    live = _activate(key, pem, "csr" if used_pending else "custom", user)
    if used_pending:
        for f in (PENDING_KEY, PENDING_CSR):
            try:
                os.remove(f)
            except OSError:
                pass
    pklog.info("system", f"Eigenes Zertifikat installiert: {d['cn']} (Aussteller {d['issuer_cn']}, gültig bis {d['not_after'][:10]}, "
                         f"SHA-256 {d['sha256'][:16]}…)" + ("" if live else " – aktiv nach dem nächsten Start"), user=user)
    return {"ok": True, "live": live, "cert": d, "warnings": warn}


def reset_self_signed(user=""):
    pk, crt = _self_signed()
    live = _activate(pk, crt.public_bytes(serialization.Encoding.PEM), "self", user)
    pklog.info("system", "Neues selbstsigniertes Zertifikat erzeugt.", user=user)
    return {"ok": True, "live": live}


def restore(name, user=""):
    if not re.match(r"^\d{8}-\d{6}(-\d+)?$", name or ""):
        raise CertError("Ungültige Sicherung")
    d = os.path.join(BACKUP_DIR, name)
    key = _load_key(open(os.path.join(d, "server.key"), "rb").read(), "")
    certs = _load_certs(open(os.path.join(d, "server.crt"), "rb").read())
    _leaf, _chain, pem, info_, _w = _check(key, certs)
    live = _activate(key, pem, "self" if info_["self_signed"] and SELF_ORG in info_["subject"] else "custom", user)
    pklog.info("system", f"Zertifikat aus Sicherung {name} wiederhergestellt ({info_['cn']}).", user=user)
    return {"ok": True, "live": live}


# ---------------------------------------------------------------------------
# Zertifikatsanforderung (z. B. für die Active-Directory-Zertifizierungsstelle)
# ---------------------------------------------------------------------------
def create_csr(cn, names, org="", ou="", country="", key_type="rsa3072", user=""):
    cn = (cn or "").strip() or config.PUBLIC_HOSTNAME
    if len(cn) > 64:
        raise CertError("Der allgemeine Name (CN) darf höchstens 64 Zeichen haben.")
    san = _san(names or [cn])
    attrs = [x509.NameAttribute(NameOID.COMMON_NAME, cn)]
    if org.strip():
        attrs.append(x509.NameAttribute(NameOID.ORGANIZATION_NAME, org.strip()[:64]))
    if ou.strip():
        attrs.append(x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, ou.strip()[:64]))
    if country.strip():
        if not re.match(r"^[A-Za-z]{2}$", country.strip()):
            raise CertError("Land als zweistelliger Code, z. B. DE.")
        attrs.append(x509.NameAttribute(NameOID.COUNTRY_NAME, country.strip().upper()))
    if key_type == "ec256":
        key = ec.generate_private_key(ec.SECP256R1())
    elif key_type == "rsa2048":
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    else:
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    csr = (x509.CertificateSigningRequestBuilder().subject_name(x509.Name(attrs))
           .add_extension(x509.SubjectAlternativeName(san), critical=False)
           .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
           .sign(key, hashes.SHA256()))
    _write(PENDING_KEY, _key_pem(key), private=True)
    pem = csr.public_bytes(serialization.Encoding.PEM)
    _write(PENDING_CSR, pem)
    pklog.info("system", f"Zertifikatsanforderung für {cn} erstellt.", user=user)
    return pem.decode()


def drop_csr(user=""):
    for f in (PENDING_KEY, PENDING_CSR):
        try:
            os.remove(f)
        except OSError:
            pass
    pklog.info("system", "Offene Zertifikatsanforderung verworfen.", user=user)


def startup_check():
    try:
        i = info()
    except Exception:
        return
    c = i.get("cert")
    if c and i.get("source") != "self" and c["days_left"] < 30:
        pklog.warn("system", f"Das Zertifikat der Weboberfläche ({c['cn']}) läuft in {c['days_left']} Tagen ab.")
