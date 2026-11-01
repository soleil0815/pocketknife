"""Kerberos statt NTLM für WinRM und SMB.

- erzeugt beim Start eine krb5.conf aus PK_DOMAIN (KDC per DNS-SRV oder PK_KDC);
  eine eigene /data/krb5.conf hat Vorrang
- Anmeldeverfahren: Einstellung „auth_mode“ (Oberfläche) vor PK_WINRM_AUTH (.env)
  ntlm      – wie bisher (WinRM und SMB nur NTLM)
  negotiate – Kerberos, wenn möglich, sonst automatisch NTLM
  kerberos  – nur Kerberos (kein NTLM-Rückfall)
- test(): Schritt-für-Schritt-Prüfung inkl. Anzeige, welches Verfahren der Client tatsächlich sieht
"""
import ipaddress
import os
import socket
import time

from . import config, util

MODES = ("ntlm", "negotiate", "kerberos")
MODE_LABELS = {"ntlm": "NTLM (wie bisher)", "negotiate": "Negotiate – Kerberos, sonst NTLM", "kerberos": "Nur Kerberos"}
KDC_ENV = [s.strip() for s in config._env("PK_KDC").split(",") if s.strip()]
CUSTOM_CONF = os.path.join(config.DATA_DIR, "krb5.conf")
GEN_CONF = "/tmp/pocketknife-krb5.conf"


def realm():
    return (config._env("PK_KERBEROS_REALM") or config.DOMAIN).strip().upper()


def conf_path():
    return CUSTOM_CONF if os.path.exists(CUSTOM_CONF) else GEN_CONF


def setup():
    """krb5.conf schreiben und KRB5_CONFIG setzen – vor der ersten Kerberos-Nutzung."""
    if os.environ.get("KRB5_CONFIG") and os.environ["KRB5_CONFIG"] not in (GEN_CONF, CUSTOM_CONF):
        return  # vom Betreiber gesetzt
    if os.path.exists(CUSTOM_CONF):
        os.environ["KRB5_CONFIG"] = CUSTOM_CONF
        return
    r = realm()
    if not r:
        return
    dom = (config.DOMAIN or r).lower()
    lines = ["[libdefaults]", f"  default_realm = {r}",
             f"  dns_lookup_kdc = {'false' if KDC_ENV else 'true'}", "  dns_lookup_realm = false",
             "  rdns = false", "  dns_canonicalize_hostname = fallback",
             "  udp_preference_limit = 0",  # TCP: große Tickets bei vielen Gruppen
             "  forwardable = true", "  ticket_lifetime = 10h", "  default_ccache_name = MEMORY:pocketknife", ""]
    if KDC_ENV:
        lines += ["[realms]", f"  {r} = {{"] + [f"    kdc = {k}" for k in KDC_ENV] + ["  }", ""]
    lines += ["[domain_realm]", f"  .{dom} = {r}", f"  {dom} = {r}", ""]
    try:
        with open(GEN_CONF, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        os.environ["KRB5_CONFIG"] = GEN_CONF
    except OSError:
        pass


def available():
    """(True, Version) wenn die Kerberos-Bibliotheken im Container vorhanden sind."""
    try:
        import gssapi  # noqa: F401
        try:
            from importlib.metadata import version
            return True, version("gssapi")
        except Exception:
            return True, ""
    except Exception as e:
        return False, str(e)


def mode():
    # Audit 2.29: CredSSP gibt das Kennwort des Dienstkontos an jeden Client weiter (dort von jedem lokalen Admin
    # auslesbar, z. B. OU-Admins) – wird nicht mehr unterstützt; „credssp“ in der .env gilt als Negotiate.
    m = (_setting("auth_mode") or config.WINRM_AUTH or "ntlm").strip().lower()
    if m == "credssp":
        _warn_credssp()
        return "negotiate"
    return m if m in MODES else "ntlm"


_credssp_warned = []


def _warn_credssp():
    if not _credssp_warned:
        _credssp_warned.append(1)
        try:
            from . import pklog
            pklog.warn("system", "PK_WINRM_AUTH=credssp wird nicht mehr unterstützt (gibt das Kennwort des Dienstkontos an die "
                                 "Clients weiter) – es gilt „Negotiate“. Empfehlung: „Nur Kerberos“.")
        except Exception:
            pass


def _setting(key):
    try:
        from . import db
        return db.setting_raw(key) or ""
    except Exception:
        return ""


def uses_kerberos(m=None):
    return (m or mode()) in ("negotiate", "kerberos")


def smb_protocol(m=None):
    m = m or mode()
    return "kerberos" if m == "kerberos" else "negotiate" if m == "negotiate" else "ntlm"


def principal(user):
    """benutzer@REALM – Kerberos versteht „DOMÄNE\\benutzer“ nicht."""
    u = (user or "").strip()
    if "\\" in u:
        u = u.split("\\", 1)[1]
    if "@" in u:
        name, dom = u.rsplit("@", 1)
        return f"{name}@{dom.upper()}"
    return f"{u}@{realm()}" if realm() else u


def login(user, m=None):
    return principal(user) if uses_kerberos(m) else user


def is_ip(host):
    try:
        ipaddress.ip_address((host or "").strip("[]"))
        return True
    except ValueError:
        return False


def host(h, m=None):
    """Für Kerberos den vollständigen DNS-Namen verwenden (SPN HOST/HTTP/cifs/<fqdn>)."""
    h = (h or "").strip()
    if not uses_kerberos(m) or not h or is_ip(h) or "." in h or not config.DOMAIN:
        return h
    return f"{h}.{config.DOMAIN}".lower()


# --- Fehlertexte -------------------------------------------------------------------
HINTS = [
    ("clock skew", "Die Uhrzeit von Container/Host und Domänencontroller weicht mehr als 5 Minuten ab. "
                   "Zeitsynchronisation (NTP) des Docker-Hosts prüfen."),
    ("cannot find kdc", "Kein Domänencontroller (KDC) für den Realm gefunden. DNS-SRV-Einträge _kerberos._tcp.<domäne> "
                        "prüfen oder in der .env PK_KDC=dc1.firma.local,dc2.firma.local setzen."),
    ("cannot contact any kdc", "Domänencontroller nicht erreichbar (TCP/UDP 88). Firewall zwischen Docker-Host und DCs prüfen "
                               "oder PK_KDC in der .env setzen."),
    ("cannot resolve network address", "Name des KDC nicht auflösbar – DNS (PK_DNS_1) prüfen."),
    ("preauthentication failed", "Kennwort des Dienstkontos falsch."),
    ("password incorrect", "Kennwort des Dienstkontos falsch."),
    ("revoked", "Das Dienstkonto ist gesperrt oder deaktiviert."),
    ("locked out", "Das Dienstkonto ist gesperrt."),
    ("password has expired", "Das Kennwort des Dienstkontos ist abgelaufen."),
    ("client not found in kerberos database", "Das Dienstkonto ist im Realm unbekannt – Benutzername prüfen (Kerberos verwendet benutzer@REALM)."),
    ("not found in kerberos database", "Für diesen Computernamen gibt es keinen Dienstprinzipalnamen (SPN). Mit vollständigem DNS-Namen "
                                       "verbinden (nicht IP-Adresse), Computerkonto im AD und DNS-Eintrag prüfen."),
    ("server not found", "Für diesen Computernamen gibt es keinen Dienstprinzipalnamen (SPN). Mit vollständigem DNS-Namen verbinden."),
    ("kdc has no support for encryption type", "Verschlüsselungstyp nicht unterstützt – AES für das Dienstkonto aktivieren "
                                               "(„Dieses Konto unterstützt Kerberos AES 256“) und das Kennwort einmal neu setzen."),
    ("no supported encryption", "Verschlüsselungstyp nicht unterstützt – AES für das Dienstkonto aktivieren und das Kennwort neu setzen."),
    ("message stream modified", "Kerberos: Das Gerät unter dieser Adresse kann das Ticket nicht lesen („Message stream modified“). Meist zeigt der DNS-Eintrag auf ein anderes Gerät (veraltete Adresse, doppelter Name) – oder die Vertrauensstellung dieses Computers zur Domäne ist defekt (Computerkonto-Kennwort nicht synchron; auf dem Client: Test-ComputerSecureChannel -Repair)."),
    ("krb_ap_err_modified", "Kerberos: Das Gerät unter dieser Adresse kann das Ticket nicht lesen („Message stream modified“). Meist zeigt der DNS-Eintrag auf ein anderes Gerät (veraltete Adresse, doppelter Name) – oder die Vertrauensstellung dieses Computers zur Domäne ist defekt (Computerkonto-Kennwort nicht synchron; auf dem Client: Test-ComputerSecureChannel -Repair)."),
    ("realm", "Realm unbekannt oder falsch – PK_DOMAIN bzw. PK_KERBEROS_REALM prüfen."),
]


def explain(msg):
    low = str(msg or "").lower()
    for key, text in HINTS:
        if key in low:
            return text
    return None


def _account_problem(msg):
    low = str(msg or "").lower()
    return any(k in low for k in ("preauthentication failed", "password incorrect", "revoked", "locked out",
                                  "password has expired", "client not found"))


# --- Ticket holen ------------------------------------------------------------------------
def tgt(user, password):
    """Holt ein Ticket-Granting-Ticket (prüft KDC-Erreichbarkeit und Kennwort)."""
    import gssapi
    from . import guard
    guard.check()
    name = gssapi.Name(principal(user), gssapi.NameType.user)
    try:
        res = gssapi.raw.acquire_cred_with_password(name, password.encode("utf-8"), usage="initiate")
    except Exception as e:
        if _account_problem(e):
            guard.trip(explain(e) or str(e), "Kerberos")
        raise
    try:
        return gssapi.Credentials(base=res.creds)
    except Exception:
        return res.creds


def service_ticket(creds, service, fqdn):
    import gssapi
    target = gssapi.Name(f"{service}@{fqdn}", gssapi.NameType.hostbased_service)
    ctx = gssapi.SecurityContext(name=target, creds=creds, usage="initiate")
    ctx.step()
    return True


def _kdcs():
    if KDC_ENV:
        return KDC_ENV, "PK_KDC"
    dom = (config.DOMAIN or "").lower()
    if not dom:
        return [], ""
    try:
        ips = sorted({a[4][0] for a in socket.getaddrinfo(dom, 88, proto=socket.IPPROTO_TCP)})
        return ips, f"DNS ({dom})"
    except OSError:
        return [], f"DNS ({dom})"


def test(computer=None, m="kerberos"):
    """Prüfung Schritt für Schritt. m = zu testendes Verfahren (Standard: nur Kerberos)."""
    from . import remote
    steps = []
    state = {}

    def step(name, fn, hint_on_fail=True):
        t = time.time()
        try:
            detail = fn()
            steps.append({"step": name, "ok": True, "detail": detail, "ms": int((time.time() - t) * 1000)})
            return True
        except Exception as e:
            msg = str(e).strip() or type(e).__name__
            steps.append({"step": name, "ok": False, "detail": msg, "hint": explain(msg) if hint_on_fail else None,
                          "ms": int((time.time() - t) * 1000)})
            return False

    def done():
        return {"mode": m, "realm": realm(), "conf": conf_path(), "steps": steps,
                "ok": bool(steps) and all(s["ok"] for s in steps), "auth_type": state.get("auth_type")}

    ok, ver = available()
    if not step("Kerberos-Bibliotheken im Container", lambda: f"gssapi {ver}" if ok else (_ for _ in ()).throw(
            RuntimeError("fehlen – Image mit Version 2.6 oder neuer neu bauen (docker compose build)"))):
        return done()
    if not step("Realm & Konfiguration", lambda: f"{realm()} · {conf_path()}" if realm() else (_ for _ in ()).throw(
            RuntimeError("PK_DOMAIN ist nicht gesetzt"))):
        return done()

    def kdc():
        hosts, src = _kdcs()
        if not hosts:
            raise RuntimeError(f"keine Domänencontroller gefunden über {src or 'DNS'} – PK_KDC in der .env setzen")
        reach = [h for h in hosts if util.tcp_open(h, 88, 3)]
        if not reach:
            raise RuntimeError(f"{', '.join(hosts)} ({src}): Port 88 nicht erreichbar")
        return f"{', '.join(reach)} erreichbar (Port 88, Quelle {src})"
    step("Domänencontroller (KDC) erreichbar", kdc)

    user = config.service_user()

    def get_tgt():
        state["creds"] = tgt(user, config.service_password())
        return f"Ticket für {principal(user)} erhalten"
    if not step("Anmeldung am KDC (Ticket-Granting-Ticket)", get_tgt):
        return done()

    if computer:
        fq = host(computer.get("dns_name") or computer.get("name"), "kerberos")
        if is_ip(fq):
            steps.append({"step": "Computername", "ok": False, "detail": fq,
                          "hint": "Kerberos braucht einen DNS-Namen – der Computer ist nur per IP-Adresse bekannt."})
            return done()
        if not step(f"Dienstticket HTTP/{fq} (WinRM)", lambda: service_ticket(state["creds"], "HTTP", fq) and "erhalten"):
            return done()
        step(f"Dienstticket cifs/{fq} (SMB)", lambda: service_ticket(state["creds"], "cifs", fq) and "erhalten")

        def winrm():
            # 2.38: im Constrained Language Mode gibt es WindowsIdentity nicht – dann „unbekannt“ und whoami
            out, err, _ = remote.ps(fq, "if ($ExecutionContext.SessionState.LanguageMode -eq 'FullLanguage') { "
                                         "$i = [Security.Principal.WindowsIdentity]::GetCurrent(); $i.AuthenticationType; $i.Name } "
                                         "else { 'unbekannt (eingeschränkter Sprachmodus)'; whoami.exe }; "
                                         "(Get-Date).ToUniversalTime().ToString('o')",
                                    auth=m)
            lines = [x.strip() for x in (out or "").splitlines() if x.strip()]
            if len(lines) < 2:
                raise RuntimeError(err or "keine Antwort")
            state["auth_type"] = lines[0]
            skew = ""
            try:
                import datetime
                t = datetime.datetime.fromisoformat(lines[2].replace("Z", "+00:00"))
                d = abs((datetime.datetime.now(datetime.timezone.utc) - t).total_seconds())
                skew = f" · Zeitabweichung {int(d)} s" + (" (zu groß für Kerberos!)" if d > 300 else "")
            except Exception:
                pass
            if lines[0].lower() != "kerberos" and m == "kerberos" and not lines[0].startswith("unbekannt"):
                raise RuntimeError(f"Client meldet Anmeldung per {lines[0]} statt Kerberos")
            return f"angemeldet als {lines[1]} per {lines[0]}{skew}"
        step(f"WinRM-Anmeldung an {fq}", winrm)

        def smb():
            info = remote.smb_probe(fq, m)
            return f"ADMIN$ geöffnet per {smb_protocol(m)} · {info.get('dialect', '?')}"
        step(f"SMB-Anmeldung an {fq} (ADMIN$)", smb)

    try:
        root = remote.repo_root()
    except Exception:
        root = ""
    if root and remote._is_unc(root):
        srv = remote._repo_server(root)

        def repo():
            try:
                info = remote.smb_probe(srv, m, user=config.repo_login(), password=config.repo_password(), share=remote._unc(root).lstrip("\\").split("\\")[1])
            except Exception as e:
                # Kurzname/IP: Hinweis nur, wenn es tatsächlich scheitert (Kurznamen löst Kerberos meist über DNS auf)
                if is_ip(srv):
                    raise RuntimeError(f"{e} – Kerberos funktioniert nicht mit IP-Adressen: im Repository-Pfad den DNS-Namen eintragen")
                if "." not in srv:
                    raise RuntimeError(f"{e} – im Repository-Pfad den vollständigen DNS-Namen eintragen (z. B. \\\\{srv}.{config.DOMAIN}\\…)")
                raise
            return f"Freigabe geöffnet per {smb_protocol(m)} · {info.get('dialect', '?')}"
        step(f"Repository \\\\{srv} (SMB)", repo)
    return done()
