"""Software-Kiosk: Anmeldung mit Windows (Kerberos/SPNEGO, „Single Sign-on“) – optional, Standard aus.

Ablauf
- Der Browser ruft POST /api/kiosk/sso auf. Ohne „Authorization: Negotiate …“ antwortet POCKET KNIFE mit 401 und
  „WWW-Authenticate: Negotiate“. Ein Browser in der Domäne holt sich dann vom DC ein Ticket für HTTP/<name> und
  schickt es mit. POCKET KNIFE prüft das Ticket mit der Keytab (dem Schlüssel des Kontos, auf dem der SPN liegt).
  Dafür braucht es keine Verbindung zum DC.
- Die Berechtigung prüft POCKET KNIFE danach wie bei der Anmeldung mit Kennwort, mit dem Dienstkonto per LDAP:
  Konto aktiv, Mitglied einer Kiosk-Gruppe oder Konto in einer Kiosk-OU (oder Mitglied der Zugriffsgruppe).
- Eine Windows-Anmeldung ergibt immer eine Kiosk-Sitzung, nie Zugriff auf die Verwaltung (dort bleibt das Kennwort Pflicht).

Die Keytab ist so geheim wie ein Kennwort. Sie liegt verschlüsselt in der Datenbank (wie die anderen Kennwörter, also
auch in jeder Sicherung) und wird nur für die Prüfung entschlüsselt in den Arbeitsspeicher geschrieben (/tmp ist im
Container ein tmpfs).
"""
import base64
import binascii
import os
import socket
import struct
import threading

from . import config, db, pklog, util, vault

KEY = "sso_keytab"          # verschlüsselt, nicht in DEFAULT_SETTINGS → nie über /api/settings sichtbar
KEY_AT = "sso_keytab_at"
RUN_DIR = "/tmp"            # tmpfs im Container (Arbeitsspeicher), nicht /data
MAX_KEYTAB = 64 * 1024
_lock = threading.Lock()
_cache = {"stamp": None, "path": None}

ENCTYPES = {1: "des-cbc-crc", 3: "des-cbc-md5", 17: "aes128-cts-hmac-sha1-96", 18: "aes256-cts-hmac-sha1-96",
            19: "aes128-cts-hmac-sha256-128", 20: "aes256-cts-hmac-sha384-192", 23: "rc4-hmac", 24: "rc4-hmac-exp"}
STRONG = {17, 18, 19, 20}


class SSOError(Exception):
    pass


# --- Keytab -------------------------------------------------------------------------------------
def parse_keytab(data):
    """Einträge einer MIT-Keytab (Version 0x0502, wie ktpass sie erzeugt) – ohne die Schlüssel selbst."""
    if not isinstance(data, (bytes, bytearray)) or len(data) < 6:
        raise SSOError("Keine Keytab-Datei (zu klein).")
    if len(data) > MAX_KEYTAB:
        raise SSOError("Keytab-Datei ist zu groß (höchstens 64 KB).")
    if data[0] != 5 or data[1] not in (1, 2):
        raise SSOError("Keine Keytab-Datei (falscher Dateianfang). Bitte die mit ktpass erzeugte .keytab-Datei wählen.")
    big = data[1] == 2
    fmt = ">" if big else "<"
    pos, out = 2, []

    def take(n):
        nonlocal pos
        if pos + n > end:
            raise SSOError("Keytab-Datei ist beschädigt.")
        b = data[pos:pos + n]
        pos += n
        return b

    while pos + 4 <= len(data):
        size = struct.unpack(fmt + "i", data[pos:pos + 4])[0]
        pos += 4
        if size == 0:
            break
        if size < 0:  # gelöschter Eintrag („Loch“)
            pos += -size
            continue
        end = pos + size
        if end > len(data):
            raise SSOError("Keytab-Datei ist beschädigt.")
        start = pos
        ncomp = struct.unpack(fmt + "H", take(2))[0]
        if data[1] == 1:
            ncomp -= 1
        realm = take(struct.unpack(fmt + "H", take(2))[0]).decode("utf-8", "replace")
        comps = [take(struct.unpack(fmt + "H", take(2))[0]).decode("utf-8", "replace") for _ in range(ncomp)]
        if data[1] == 2:
            take(4)  # name_type
        take(4)  # Zeitstempel
        kvno = take(1)[0]
        etype = struct.unpack(fmt + "H", take(2))[0]
        klen = struct.unpack(fmt + "H", take(2))[0]
        take(klen)
        if end - pos >= 4:
            v32 = struct.unpack(fmt + "I", data[pos:pos + 4])[0]
            if v32:
                kvno = v32
        pos = end
        out.append({"principal": "/".join(comps) + "@" + realm, "service": comps[0] if comps else "",
                    "host": comps[1] if len(comps) > 1 else "", "realm": realm, "kvno": kvno, "enctype": etype,
                    "enctype_name": ENCTYPES.get(etype, f"Typ {etype}"), "strong": etype in STRONG})
        if pos <= start:
            break
    if not out:
        raise SSOError("Die Keytab enthält keine Schlüssel.")
    return out


def _raw():
    enc = db.setting_raw(KEY)
    if not enc:
        return b""
    try:
        return base64.b64decode(vault.decrypt(enc) or "")
    except (binascii.Error, ValueError):
        return b""


def present():
    return bool(db.setting_raw(KEY))


def save_keytab(data, user=None):
    entries = parse_keytab(bytes(data))
    http = [e for e in entries if e["service"].upper() == "HTTP" and e["host"]]
    if not http:
        names = ", ".join(sorted({e["principal"] for e in entries})[:5])
        raise SSOError(f"Die Keytab enthält keinen HTTP-Dienstnamen (gefunden: {names}). Erzeugen mit "
                       "ktpass -princ HTTP/<name>@<REALM> …")
    realm = config_realm()
    if realm and not any(e["realm"].upper() == realm for e in http):
        raise SSOError(f"Die Keytab gehört zum Realm {http[0]['realm']}, die Domäne ist aber {realm}.")
    db.set_setting(KEY, vault.encrypt(base64.b64encode(bytes(data)).decode("ascii")))
    db.set_setting(KEY_AT, util.now())
    _drop_runtime()
    pklog.info("kiosk", "Keytab für die Windows-Anmeldung hinterlegt: "
               + ", ".join(sorted({e["principal"] for e in http})), user=user)
    return info()


def clear_keytab(user=None):
    db.ex("DELETE FROM settings WHERE key IN (?, ?)", (KEY, KEY_AT))
    _drop_runtime()
    pklog.info("kiosk", "Keytab für die Windows-Anmeldung entfernt.", user=user)


def _drop_runtime():
    with _lock:
        p = _cache.get("path")
        _cache.update(stamp=None, path=None)
    if p:
        try:
            os.remove(p)
        except OSError:
            pass


def _keytab_path():
    """Entschlüsselte Keytab im Arbeitsspeicher (tmpfs) – neu geschrieben, sobald sich die gespeicherte ändert."""
    enc = db.setting_raw(KEY)
    if not enc:
        raise SSOError("Keine Keytab hinterlegt.")
    stamp = hash(enc)
    with _lock:
        p = _cache.get("path")
        if _cache.get("stamp") == stamp and p and os.path.exists(p):
            return p
        raw = _raw()
        if not raw:
            raise SSOError("Die gespeicherte Keytab lässt sich nicht entschlüsseln (pocketknife.key geändert?) – bitte neu hochladen.")
        p = os.path.join(RUN_DIR, "pocketknife-http.keytab")
        tmp = p + f".{os.getpid()}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        os.replace(tmp, p)
        _cache.update(stamp=stamp, path=p)
        return p


def entries():
    raw = _raw()
    if not raw:
        return []
    try:
        return parse_keytab(raw)
    except SSOError:
        return []


def hosts():
    """Rechnernamen aus den HTTP-SPNs der Keytab (unter diesen Namen muss der Kiosk aufgerufen werden)."""
    seen, out = set(), []
    for e in entries():
        if e["service"].upper() == "HTTP" and e["host"] and e["host"].lower() not in seen:
            seen.add(e["host"].lower())
            out.append(e["host"].lower())
    return out


def info():
    es = entries()
    return {"present": present(), "uploaded_at": db.setting_raw(KEY_AT),
            "entries": [{k: e[k] for k in ("principal", "kvno", "enctype_name", "strong")} for e in es],
            "hosts": hosts(), "enabled": bool(db.setting("kiosk_sso")), "active": enabled()}


def config_realm():
    return (config._env("PK_KERBEROS_REALM") or config.DOMAIN or "").strip().upper()


def enabled():
    """Windows-Anmeldung angeboten? Kiosk an, Schalter an, Keytab vorhanden."""
    from . import kiosk
    return bool(kiosk.enabled() and db.setting("kiosk_sso") and present())


# --- Prüfen eines Tickets -----------------------------------------------------------------------
def _gss():
    try:
        import gssapi
        return gssapi
    except Exception as e:  # pragma: no cover – im Container immer vorhanden
        raise SSOError(f"Kerberos-Bibliothek (gssapi) fehlt im Container: {e}")


def _prepare_env():
    # Replay-Cache: Standard wäre /var/tmp – im schreibgeschützten Container nicht beschreibbar
    os.environ.setdefault("KRB5RCACHEDIR", RUN_DIR)
    from . import kerberos
    kerberos.setup()


def accept(header):
    """Negotiate-Token prüfen. Gibt (Benutzername ohne Realm, Realm, Antwort-Token oder None) zurück."""
    parts = str(header or "").split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "negotiate":
        raise SSOError("Kein Negotiate-Token.")
    try:
        token = base64.b64decode(parts[1].strip(), validate=True)
    except (binascii.Error, ValueError):
        raise SSOError("Ungültiges Negotiate-Token.")
    if not token or len(token) > 64 * 1024:
        raise SSOError("Ungültiges Negotiate-Token.")
    if token.startswith(b"NTLMSSP"):
        raise SSOError("Der Browser hat NTLM statt Kerberos geschickt. Das passiert, wenn der Kiosk über eine IP-Adresse "
                       "oder einen Namen ohne SPN aufgerufen wird, oder der Rechner kein Ticket bekommt. Bitte über "
                       + (" bzw. ".join(f"https://{h}" for h in hosts()) or "den Namen aus der Keytab") + " aufrufen.")
    gssapi = _gss()
    _prepare_env()
    path = _keytab_path()
    try:
        creds = gssapi.Credentials(usage="accept", store={"keytab": path})
        ctx = gssapi.SecurityContext(creds=creds, usage="accept")
        out = ctx.step(token)
    except Exception as e:
        raise SSOError(explain(e))
    if not ctx.complete:
        raise SSOError("Kerberos-Anmeldung nicht abgeschlossen (mehrstufiges Verfahren wird nicht unterstützt).")
    # Nur Tickets für den HTTP-Namen des Kiosks – nicht für andere Dienste, deren Schlüssel zufällig in der Keytab stehen
    try:
        tgt = str(getattr(ctx, "target_name", None) or "")
    except Exception:
        tgt = ""
    svc = tgt.split("@", 1)[0]
    if "/" in svc:
        s0, h0 = svc.split("/", 1)
        if s0.upper() != "HTTP" or (hosts() and h0.lower() not in hosts()):
            raise SSOError(f"Das Ticket gilt für einen anderen Dienst ({tgt}), nicht für den Kiosk.")
    name = str(ctx.initiator_name or "")
    if "@" not in name:
        raise SSOError(f"Unerwarteter Benutzername im Ticket: {name!r}")
    user, realm = name.rsplit("@", 1)
    if "/" in user:
        raise SSOError(f"Das Ticket gehört zu einem Dienst ({name}), nicht zu einem Benutzer.")
    want = config_realm()
    if want and realm.upper() != want:
        raise SSOError(f"Ticket aus einer anderen Domäne ({realm}) – erwartet wird {want}.")
    return user, realm.upper(), out


def explain(e):
    """GSSAPI-Fehler in eine verständliche Meldung übersetzen."""
    t = str(e)
    low = t.lower()
    if "no key table entry" in low or "cannot find key of appropriate type" in low or "key version" in low or "kvno" in low:
        return ("Das Ticket passt nicht zur Keytab (anderer Schlüssel oder Verschlüsselungstyp). Meist wurde das Kennwort "
                "des SPN-Kontos nach dem Erzeugen der Keytab geändert, oder das Konto hat AES nicht aktiviert – Keytab neu "
                f"mit ktpass erzeugen und hochladen. ({t[:200]})")
    if "clock skew" in low or "skew" in low:
        return f"Die Uhrzeit von Docker-Host und Domäne weicht zu stark ab (max. 5 Minuten). ({t[:200]})"
    if "replay" in low:
        return f"Das Ticket wurde bereits benutzt (Replay) – Seite neu laden. ({t[:200]})"
    if "defective token" in low or "invalid token" in low:
        return f"Der Browser hat kein gültiges Kerberos-Ticket geschickt. ({t[:200]})"
    return f"Kerberos-Prüfung fehlgeschlagen: {t[:300]}"


# --- Test für die Einstellungsseite -------------------------------------------------------------
def test():
    """Schritt-für-Schritt-Prüfung ohne Browser. Liste von {ok: True/False/None, text}."""
    from . import kiosk
    steps = []

    def add(ok, text):
        steps.append({"ok": ok, "text": text})

    add(bool(kiosk.enabled()), "Kiosk eingeschaltet" if kiosk.enabled() else "Der Kiosk ist ausgeschaltet.")
    add(bool(db.setting("kiosk_sso")) or None, "Windows-Anmeldung eingeschaltet" if db.setting("kiosk_sso")
        else "Windows-Anmeldung ist noch ausgeschaltet (Häkchen setzen und speichern).")
    try:
        g = _gss()
        add(True, f"Kerberos-Bibliothek vorhanden (gssapi {getattr(g, '__version__', '')})".replace(" )", ")"))
    except SSOError as e:
        add(False, str(e))
        return steps
    es = entries()
    if not es:
        add(False, "Keine Keytab hinterlegt." if not present() else "Die gespeicherte Keytab ist unlesbar – bitte neu hochladen.")
        return steps
    http = [e for e in es if e["service"].upper() == "HTTP"]
    add(True, "Keytab: " + ", ".join(sorted({f"{e['principal']} (kvno {e['kvno']})" for e in http})))
    if not any(e["strong"] for e in http):
        add(False, "Die Keytab enthält nur veraltete Verschlüsselung (" + ", ".join(sorted({e["enctype_name"] for e in http}))
            + "). Bitte mit -crypto AES256-SHA1 neu erzeugen und im AD beim Konto „AES 256“ aktivieren.")
    else:
        add(True, "Verschlüsselung: " + ", ".join(sorted({e["enctype_name"] for e in http})))
    realm = config_realm()
    bad = [e for e in http if realm and e["realm"].upper() != realm]
    if bad:
        add(False, f"Realm der Keytab ({bad[0]['realm']}) passt nicht zur Domäne ({realm}).")
    try:
        _prepare_env()
        path = _keytab_path()
        import gssapi
        gssapi.Credentials(usage="accept", store={"keytab": path})
        add(True, "Schlüssel aus der Keytab für die Prüfung geladen")
    except Exception as e:
        add(False, f"Schlüssel lassen sich nicht laden: {str(e)[:300]}")
    from . import mailer
    base = (mailer.base_url() or "").lower()
    for h in hosts():
        try:
            ips = sorted({a[4][0] for a in socket.getaddrinfo(h, None)})
            add(True, f"DNS: {h} → {', '.join(ips)}")
        except OSError:
            add(False, f"DNS: {h} lässt sich nicht auflösen – A-Eintrag auf den Docker-Host anlegen.")
        try:
            cname = socket.gethostbyname_ex(h)[0].lower()
            if cname and cname != h.lower():
                add(False, f"{h} ist ein Alias (CNAME auf {cname}). Browser fragen dann ein Ticket für {cname} an – "
                           "bitte einen A-Eintrag verwenden oder den SPN auf diesen Namen setzen.")
        except OSError:
            pass
    if base and hosts() and not any(f"//{h}" in base for h in hosts()):
        add(None, f"Die Adresse des Programms (E-Mail-Einstellungen: {mailer.base_url()}) verwendet einen anderen Namen "
                  f"als die Keytab ({', '.join(hosts())}). Verknüpfungen für Nutzer müssen auf "
                  f"https://{hosts()[0]}/kiosk zeigen.")
    add(None, "Ob ein Browser wirklich ein Ticket schickt, zeigt erst der Aufruf von einem Domänen-PC: "
              + " bzw. ".join(f"https://{h}{_port()}/kiosk" for h in hosts()))
    return steps


def _port():
    try:
        from . import mailer
        b = mailer.base_url() or ""
        hostport = b.split("//", 1)[-1].split("/", 1)[0]
        return ":" + hostport.rsplit(":", 1)[1] if ":" in hostport else ""
    except Exception:
        return ""
