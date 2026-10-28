"""Active Directory / Samba über LDAPS: Anmeldung, Gruppenprüfung, Computer-Abgleich.

Das Zertifikat jedes Domänencontrollers wird beim ersten Kontakt vertraut (TOFU) und VOR dem Senden des
Kennworts verglichen. Änderung: protokollieren (Standard) oder ablehnen (Einstellung „strikt“).
Mit PK_LDAP_CA_FILE wird stattdessen gegen die CA der Domäne geprüft.
"""
import datetime
import hashlib
import json
import re
import ssl
import time
import uuid

from ldap3 import FIRST, LEVEL, NONE, SIMPLE, SUBTREE, Connection, Server, ServerPool, Tls
from ldap3.utils.conv import escape_filter_chars

from . import config, db, guard, pklog, util


class ADError(Exception):
    pass


class GroupConfigError(ADError):
    """Gruppe nicht gefunden oder mehrdeutig – eine Konfigurationsfrage, keine Störung des AD."""


CA_FILE = config._env("PK_LDAP_CA_FILE").strip()  # optional: CA der Domäne (PEM) – dann echte Zertifikatsprüfung


def _pool():
    if CA_FILE:
        tls = Tls(validate=ssl.CERT_REQUIRED, ca_certs_file=CA_FILE)
    else:
        tls = Tls(validate=ssl.CERT_NONE)
    servers = [Server(h, port=config.LDAP_PORT, use_ssl=True, tls=tls, get_info=NONE, connect_timeout=8)
               for h in config.LDAP_SERVERS]
    if not servers:
        raise ADError("Kein LDAP-Server konfiguriert (PK_LDAP_SERVERS / PK_DOMAIN).")
    return ServerPool(servers, FIRST, active=2, exhaust=60)


def _fingerprints():
    """{server: fingerprint} – je Domänencontroller ein eigenes Zertifikat."""
    raw = db.setting_raw("ldap_cert_fingerprints") or ""
    try:
        d = json.loads(raw) if raw else {}
    except ValueError:
        d = {}
    return d if isinstance(d, dict) else {}


def _check_fingerprint(c):
    """Vor dem Senden des Kennworts: Zertifikat des DC mit dem gespeicherten vergleichen (Trust on first use).
    Strikt (Einstellung ldap_cert_strict): bei Änderung abbrechen; sonst protokollieren und übernehmen.
    Mit PK_LDAP_CA_FILE prüft TLS selbst gegen die CA – dann wird nur noch protokolliert."""
    strict = bool(db.setting("ldap_cert_strict")) and not CA_FILE
    try:
        sock = c.socket
        der = sock.getpeercert(binary_form=True) if sock else None
        host = (c.server.host if c.server else "") or "?"
    except Exception:
        der, host = None, "?"
    if not der:
        if strict:  # strikt heißt: ohne geprüftes Zertifikat geht kein Kennwort hinaus
            raise ADError("Das LDAPS-Zertifikat des Domänencontrollers ließ sich nicht lesen – Verbindung abgelehnt (strikte Prüfung).")
        return
    fp = hashlib.sha256(der).hexdigest().upper()
    fp = ":".join(fp[i:i + 2] for i in range(0, len(fp), 2))
    known = _fingerprints()
    olds = _fp_list(known.get(host))
    if fp in olds:
        return
    old = ", ".join(olds)
    if old and strict:
        db.set_setting("ldap_cert_pending", json.dumps({"host": host, "fingerprint": fp, "old": old, "seen": util.now()}))
        pklog.error("ldap", f"LDAPS-Zertifikat von {host} hat sich geändert – Verbindung abgelehnt (strikte Prüfung). "
                            f"Alt: {old} Neu: {fp}. Unter Einstellungen → Verbindung & Konten prüfen und vertrauen.")
        raise ADError(f"Das LDAPS-Zertifikat von {host} hat sich geändert und wurde abgelehnt. Ein Administrator muss es "
                      f"unter Einstellungen → Verbindung & Konten bestätigen (Notfallzugang oder bestehende Sitzung).")
    if old:
        pklog.warn("ldap", f"LDAPS-Zertifikat von {host} hat sich geändert. Alt: {old} Neu: {fp}")
    else:
        pklog.info("ldap", f"LDAPS-Zertifikat von {host} automatisch vertraut: {fp}")
    # Audit 2.29: je Name mehrere Zertifikate merken – hinter dem Domänennamen antworten oft mehrere DCs mit eigenem
    # Zertifikat; ersetzen hieße, dass die strikte Prüfung bei jedem Wechsel ablehnt (und Admins sie abschalten)
    known[host] = (olds + [fp])[-FP_MAX:]
    db.set_setting("ldap_cert_fingerprints", json.dumps(known))
    db.set_setting("ldap_cert_fingerprint", fp)


FP_MAX = 16


def _fp_list(v):
    if isinstance(v, list):
        return [str(x) for x in v if x]
    return [str(v)] if v else []


def trust_pending():
    """Vom Administrator bestätigtes neues Zertifikat übernehmen."""
    try:
        p = json.loads(db.setting_raw("ldap_cert_pending") or "{}")
    except ValueError:
        p = {}
    if not p.get("host"):
        return None
    known = _fingerprints()
    known[p["host"]] = (_fp_list(known.get(p["host"])) + [p["fingerprint"]])[-FP_MAX:]
    db.set_setting("ldap_cert_fingerprints", json.dumps(known))
    db.set_setting("ldap_cert_fingerprint", p["fingerprint"])
    db.set_setting("ldap_cert_pending", "")
    return p


def bind(user, password, result=None):
    """Gibt eine gebundene Verbindung zurück oder None bei abgelehnter Anmeldung.
    Ist `result` ein dict, wird dort die LDAP-Antwort des Servers abgelegt."""
    if not password:
        return None
    c = Connection(_pool(), user=user, password=password, authentication=SIMPLE,
                   auto_bind=False, raise_exceptions=False, receive_timeout=30)
    try:
        if not c.open() and c.closed:
            raise ADError("LDAPS-Verbindung fehlgeschlagen: " + str(c.result))
    except ADError:
        raise
    except Exception as e:
        raise ADError(f"LDAPS-Verbindung zu {', '.join(config.LDAP_SERVERS)}:{config.LDAP_PORT} fehlgeschlagen: {e}")
    try:
        _check_fingerprint(c)  # VOR dem Bind – das Kennwort geht nur an einen bekannten Server
    except ADError:
        try:
            c.unbind()
        except Exception:
            pass
        raise
    try:
        ok = c.bind()
    except Exception as e:  # Netzwerkfehler (z. B. LDAPSocketReceiveError) kommen trotz raise_exceptions=False
        try:
            c.unbind()
        except Exception:
            pass
        raise ADError(f"LDAPS-Verbindung unterbrochen: {e}")
    if not ok:
        if result is not None:
            result.update(c.result or {})
        c.unbind()
        return None
    return c


def service_conn():
    if not config.ldap_configured():
        raise ADError("Dienstkonto/LDAP ist nicht konfiguriert.")
    try:
        guard.check()
    except PermissionError as e:
        raise ADError(str(e))
    res = {}
    c = bind(config.service_upn(), config.service_password(), res)
    if c is None:
        detail = f"{res.get('description', '')} {res.get('message', '')}".strip()
        text, _acc = guard.explain(detail)
        text = text or "Anmeldung des Dienstkontos am LDAP abgelehnt (Benutzer/Passwort prüfen)."
        guard.trip(text, "LDAP")
        raise ADError(text)
    return c


def _attr(entry, name, default=None):
    v = entry.get("attributes", {}).get(name)
    if isinstance(v, list):
        return v[0] if v else default
    return v if v not in (None, "") else default


def _raw(entry, name):
    v = entry.get("raw_attributes", {}).get(name)
    if isinstance(v, list):
        return v[0] if v else None
    return v


def _filetime(v):
    try:
        v = int(v)
        if v <= 0 or v >= 0x7FFFFFFFFFFFFFFF:
            return None
        utc = datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc) + datetime.timedelta(microseconds=v // 10)
        return util.fmt(utc.astimezone().replace(tzinfo=None))
    except (TypeError, ValueError):
        return None


def _utc_offset():
    return datetime.datetime.now().astimezone().utcoffset() or datetime.timedelta(0)


def _gentime(v):
    try:
        s = str(v)[:14]
        dt = datetime.datetime.strptime(s, "%Y%m%d%H%M%S").replace(tzinfo=datetime.timezone.utc)
        return util.fmt(dt.astimezone().replace(tzinfo=None))
    except (TypeError, ValueError):
        return None


def _search(c, base, flt, attrs):
    """Seitenweise Suche. Bricht ab, wenn der Server einen Fehler meldet – ein unvollständiges Ergebnis
    darf nie als „Computer verschwunden“ gewertet werden (raise_exceptions=False liefert sonst still weniger)."""
    try:
        out = [e for e in c.extend.standard.paged_search(base, flt, SUBTREE, attributes=attrs,
                                                          paged_size=500, generator=True)
               if e.get("type") == "searchResEntry"]
    except ADError:
        raise
    except Exception as e:  # Verbindungsabbruch mitten in der Suche
        raise ADError(f"LDAP-Suche in {base} fehlgeschlagen: {e}")
    res = c.result or {}
    code = res.get("result")
    if code not in (0, None):
        raise ADError(f"LDAP-Suche in {base} unvollständig: {res.get('description') or code} {res.get('message') or ''}".strip())
    return out


# --- Anmeldung -------------------------------------------------------------
IN_CHAIN = "1.2.840.113556.1.4.1941"  # LDAP_MATCHING_RULE_IN_CHAIN (AD und Samba AD)


def norm_dn(dn):
    """Vergleichbare Form eines DN (Groß/Klein und Leerzeichen um , und = egal)."""
    return re.sub(r"\s*([,=+])\s*", r"\1", (dn or "").strip()).lower()


def _sid_str(raw):
    """Binäre objectSid -> 'S-1-5-21-…'."""
    b = bytes(raw)
    auth = int.from_bytes(b[2:8], "big")
    subs = [int.from_bytes(b[8 + 4 * i:12 + 4 * i], "little") for i in range(b[1])]
    return "S-%d-%d" % (b[0], auth) + "".join("-%d" % x for x in subs)


def _find_groups(c, g, attrs=("distinguishedName",)):
    """Gruppen zu einem Namen: zuerst nach sAMAccountName (im ganzen AD eindeutig), nur ohne Treffer nach cn (der ist
    nur je Container eindeutig). So kann eine gleichnamige Gruppe in einer anderen OU eine echte nicht verdrängen."""
    esc = escape_filter_chars(g)
    res = _search(c, config.BASE_DN, f"(&(objectClass=group)(sAMAccountName={esc}))", list(attrs))
    if res:
        return res
    return _search(c, config.BASE_DN, f"(&(objectClass=group)(cn={esc}))", list(attrs))


def _group_dn(c):
    """Kanonischen DN der Zugriffsgruppe ermitteln (Name, sAMAccountName oder DN)."""
    g = config.ACCESS_GROUP
    if "=" in g:
        c.search(g, "(objectClass=group)", "BASE", attributes=["distinguishedName"])
        hit = [e for e in (c.response or []) if e.get("type") == "searchResEntry"]
        if hit:
            return hit[0]["dn"]
        raise GroupConfigError(f"Zugriffsgruppe '{g}' wurde im AD nicht gefunden (DN prüfen).")
    res = _find_groups(c, g)
    if not res:
        raise GroupConfigError(f"Zugriffsgruppe '{g}' wurde im AD nicht gefunden.")
    if len({norm_dn(r["dn"]) for r in res}) > 1:
        # cn ist nur je Container eindeutig – eine gleichnamige Gruppe woanders darf nie Zugriff geben
        raise GroupConfigError(f"Zugriffsgruppe '{g}' ist mehrdeutig (mehrere Gruppen heißen so) – bitte in PK_ACCESS_GROUP den "
                      "vollständigen DN eintragen.")
    return res[0]["dn"]


def _member_of(c, dn, strict=False):
    c.search(dn, "(objectClass=*)", "BASE", attributes=["memberOf"])
    if strict:
        _strict_ok(c)
    for e in c.response or []:
        if e.get("type") == "searchResEntry":
            v = e["attributes"].get("memberOf") or []
            return v if isinstance(v, list) else [v]
    return []


def _primary_group_dn(c, user_entry, strict=False):
    """DN der primären Gruppe (steht nicht in memberOf, z. B. Domänen-Benutzer)."""
    try:
        rid = int(_attr(user_entry, "primaryGroupID", 0) or 0)
        sid = _raw(user_entry, "objectSid")
        if not rid or not sid:
            return None
        dom = _sid_str(sid).rsplit("-", 1)[0]
        res = _search(c, config.BASE_DN, f"(objectSid={dom}-{rid})", ["distinguishedName"])
        return res[0]["dn"] if res else None
    except Exception:
        if strict:
            raise
        return None


def _strict_ok(c):
    """Nachprüfung laufender Sitzungen: eine gestörte Suche (Zeitüberschreitung, DC beschäftigt …) ist „nicht prüfbar“,
    nie „kein Mitglied“ – sonst meldete eine kurze Störung Benutzer ab. 32 = Objekt gibt es nicht."""
    code = (c.result or {}).get("result")
    if code not in (0, None, 32):
        raise ADError(f"LDAP-Suche gestört: {(c.result or {}).get('description') or code}")


def is_member(c, user_entry, group_dn, strict=False):
    """Prüft (auch verschachtelte) Mitgliedschaft. Gibt (True/False, Methode) zurück."""
    target = norm_dn(group_dn)
    user_dn = user_entry["dn"]
    # 1) Serverseitig über die gesamte Kette (schnell, AD und aktuelles Samba)
    try:
        c.search(user_dn, f"(memberOf:{IN_CHAIN}:={escape_filter_chars(group_dn)})", "BASE",
                 attributes=["distinguishedName"])
        if any(e.get("type") == "searchResEntry" for e in (c.response or [])):
            return True, "in-chain"
    except Exception:
        pass
    # 2) Clientseitig: memberOf rekursiv + primäre Gruppe (funktioniert überall)
    mo = user_entry["attributes"].get("memberOf") or []
    queue = list(mo if isinstance(mo, list) else [mo])
    pg = _primary_group_dn(c, user_entry, strict)
    if pg:
        queue.append(pg)
    seen = set()
    depth = 0
    while queue and depth < 15:
        nxt = []
        for g in queue:
            gl = norm_dn(g)
            if gl == target:
                return True, f"rekursiv (Ebene {depth + 1})"
            if gl in seen:
                continue
            seen.add(gl)
            nxt.extend(_member_of(c, g, strict))
        queue = nxt
        depth += 1
    return False, "rekursiv"


# --- Kiosk: wer darf Software für seinen eigenen Rechner anfordern? ---------------------------
def _lines(v):
    return [x.strip() for x in str(v or "").replace(";", "\n").splitlines() if x.strip()]


def kiosk_rules():
    """(Gruppen, OUs) aus den Einstellungen. Gruppen: Name oder DN (auch verschachtelt); OUs: DN."""
    return _lines(db.setting("kiosk_groups")), _lines(db.setting("kiosk_ous"))


def _resolve_group(c, g, strict=False):
    """DN zu Name/DN – None, wenn nicht gefunden oder mehrdeutig. strict: Störungen des AD als Fehler weitergeben."""
    try:
        if "=" in g:
            c.search(g, "(objectClass=group)", "BASE", attributes=["distinguishedName"])
            hit = [e for e in (c.response or []) if e.get("type") == "searchResEntry"]
            return hit[0]["dn"] if hit else None
        res = _find_groups(c, g)
        if len({norm_dn(r["dn"]) for r in res}) != 1:
            return None  # nicht gefunden oder mehrdeutig (gleicher cn in anderem Container) → nie raten
        return res[0]["dn"]
    except Exception:
        if strict:
            raise
        return None


def resolve_group_strict(c, g):
    """Gruppe eindeutig auflösen → (DN, SID); ADError mit verständlicher Meldung bei „nicht gefunden“ oder „mehrdeutig“.
    Die SID bleibt einer Gruppe für immer – eine gelöschte und gleichnamig neu angelegte Gruppe erbt sie nicht."""
    g = str(g or "").strip()
    if "=" in g:
        c.search(g, "(objectClass=group)", "BASE", attributes=["distinguishedName", "objectSid"])
        res = [e for e in (c.response or []) if e.get("type") == "searchResEntry"]
    else:
        res = _find_groups(c, g, ("distinguishedName", "objectSid"))
    if not res:
        raise ADError(f"Gruppe „{g}“ wurde im AD nicht gefunden.")
    if len({norm_dn(r["dn"]) for r in res}) > 1:
        raise ADError(f"Gruppe „{g}“ ist mehrdeutig ({len(res)} Gruppen) – bitte den vollständigen DN eintragen.")
    raw = _raw(res[0], "objectSid")
    return res[0]["dn"], (_sid_str(raw) if raw else None)


def group_dn_by_sid(c, sid):
    """Aktueller DN einer Gruppe zu ihrer SID (übersteht Umbenennen/Verschieben) – oder None."""
    if not sid or not re.match(r"^S-1-[0-9-]+$", sid):
        return None
    res = _search(c, config.BASE_DN, f"(&(objectClass=group)(objectSid={sid}))", ["distinguishedName"])
    return res[0]["dn"] if len(res) == 1 else None


def kiosk_match(c, user_entry, strict=False, cache=None):
    """Darf dieser Benutzer den Kiosk verwenden? (Kiosk eingeschaltet und Benutzer in einer der OUs bzw. Gruppen)
    strict/cache: Nachprüfung laufender Sitzungen (Störung → Fehler; Gruppen-DNs je Durchlauf nur einmal auflösen)."""
    if not db.setting("kiosk_enabled"):
        return False
    groups, ous = kiosk_rules()
    udn = norm_dn(user_entry["dn"])
    for ou in ous:
        o = norm_dn(ou)
        if o and udn.endswith("," + o):
            return True
    for g in groups:
        if cache is not None and g in cache:
            gdn = cache[g]
        else:
            gdn = _resolve_group(c, g, strict=True) if strict else _resolve_group(c, g)
            if cache is not None:
                cache[g] = gdn
        if gdn and (is_member(c, user_entry, gdn, strict=True) if strict else is_member(c, user_entry, gdn))[0]:
            return True
    return False


def member_of_any(sam, groups):
    """Ist der Benutzer (sAMAccountName) Mitglied einer dieser Gruppen (Name oder DN, auch verschachtelt)?
    True/False – oder None, wenn das AD gerade nicht prüfbar ist (mit dem Dienstkonto)."""
    groups = [g for g in (groups or []) if g]
    if not groups:
        return True
    try:
        c = service_conn()
    except Exception:
        return None
    try:
        found = _search(c, config.BASE_DN, f"(&(objectClass=user)(objectCategory=person)(sAMAccountName={escape_filter_chars(sam)}))",
                        ["distinguishedName", "memberOf", "primaryGroupID", "objectSid"])
        if not found:
            return False
        for g in groups:
            gdn = _resolve_group(c, g)
            if gdn and is_member(c, found[0], gdn)[0]:
                return True
        return False
    except Exception:
        return None
    finally:
        try:
            c.unbind()
        except Exception:
            pass


def authenticate(username, password, allow_kiosk=False):
    """Prüft Benutzer + Passwort + Gruppenmitgliedschaft mit den Daten des Benutzers selbst.
    Das Dienstkonto wird dafür nicht benötigt – die Anmeldung funktioniert also auch,
    wenn das Dienstkonto gesperrt ist. Rolle „admin“ (Zugriffsgruppe) oder – nur bei allow_kiosk – „kiosk“."""
    username = (username or "").strip()
    if not username or not password:
        raise ADError("Benutzername und Passwort angeben.")
    if "\\" in username:
        username = username.split("\\", 1)[1]
    if "@" in username:
        upn, sam = username, username.split("@", 1)[0]
    else:
        sam = username
        upn = f"{sam}@{config.DOMAIN}" if config.DOMAIN else sam
    res = {}
    uc = bind(upn, password, res)
    if uc is None:
        text, _acc = guard.explain(f"{res.get('description', '')} {res.get('message', '')}")
        if text and "gesperrt" in text:
            raise ADError("Ihr Benutzerkonto ist gesperrt.")
        if text and "deaktiviert" in text:
            raise ADError("Ihr Benutzerkonto ist deaktiviert.")
        if text and ("abgelaufen" in text or "geändert" in text):
            raise ADError("Ihr Passwort ist abgelaufen oder muss geändert werden.")
        raise ADError("Benutzername oder Passwort falsch.")
    try:
        # Wer ist wirklich angemeldet? Ein eingegebener Name kann als UPN auf ein anderes Konto passen als der gleich-
        # namige sAMAccountName (z. B. nach Umbenennungen) – die Sitzung muss dem Konto gehören, dessen Kennwort stimmte.
        bound = _whoami_sam(uc)
        attrs = ["distinguishedName", "displayName", "sAMAccountName", "userPrincipalName", "memberOf",
                 "userAccountControl", "primaryGroupID", "objectSid"]
        if bound:
            found = _search(uc, config.BASE_DN, f"(&(objectClass=user)(objectCategory=person)"
                                                f"(sAMAccountName={escape_filter_chars(bound)}))", attrs)
        else:
            flt = (f"(&(objectClass=user)(objectCategory=person)(|(sAMAccountName={escape_filter_chars(sam)})"
                   f"(userPrincipalName={escape_filter_chars(upn)})))")
            found = _search(uc, config.BASE_DN, flt, attrs)
            by_upn = [x for x in found if str(_attr(x, "userPrincipalName", "") or "").lower() == upn.lower()]
            found = by_upn or [x for x in found if str(_attr(x, "sAMAccountName", "") or "").lower() == sam.lower()]
            if len(found) > 1:
                raise ADError("Benutzerobjekt nicht eindeutig (mehrere Konten passen) – bitte als DOMÄNE\\benutzer anmelden.")
        if not found:
            raise ADError("Benutzerobjekt nicht gefunden (Basis-DN prüfen).")
        e = found[0]
        group_err = None
        try:
            ok, how = is_member(uc, e, _group_dn(uc))
        except ADError as ge:
            group_err = ge
            ok, how = False, ""
        role, scope = "admin", None
        if not ok:
            # Anmeldung am Kiosk: Kiosk-Rolle vor OU-Admin (dort will man Software für den eigenen Rechner)
            tids = [] if allow_kiosk and kiosk_match(uc, e) else tenant_scope(uc, e)
            if tids:
                # OU-Admin: Mitglied der AD-Gruppe eines Mandanten → Verwaltung nur für dessen Computer
                scope, how = tids, "mandant"
            elif allow_kiosk and kiosk_match(uc, e):
                role, how = "kiosk", "kiosk"
            elif group_err is not None:
                raise group_err
            else:
                raise ADError("Kein Zugriff: Sie sind nicht Mitglied der Gruppe für POCKET KNIFE "
                              "(auch nicht über verschachtelte Gruppen) und keiner Mandanten-Gruppe.")
        return {"username": _attr(e, "sAMAccountName", sam), "display_name": _attr(e, "displayName", sam),
                "dn": e["dn"], "via": how, "role": role, "scope": scope}
    finally:
        uc.unbind()


def tenant_scope(c, user_entry):
    """IDs der Mandanten, in deren OU-Admin-Gruppe der Benutzer ist (auch verschachtelt). Nur Mandanten mit Sammlung –
    ohne Sammlung gäbe es keine Computer (und nie „alle“)."""
    ids = []
    for t in db.q("SELECT id, admin_group, admin_group_dn, admin_group_sid FROM tenants WHERE COALESCE(admin_group, '') != '' "
                  "AND collection_id IS NOT NULL ORDER BY id"):
        # Nur die beim Speichern eindeutig ermittelte Gruppe zählt (SID; ältere Stände: DN) – nie erneut nach Namen suchen
        gdn = group_dn_by_sid(c, t.get("admin_group_sid")) if t.get("admin_group_sid") else t.get("admin_group_dn")
        if not gdn:
            continue
        try:
            if gdn and is_member(c, user_entry, gdn)[0]:
                ids.append(int(t["id"]))
        except Exception:
            continue
    return ids


def _whoami_sam(c):
    """sAMAccountName des gebundenen Kontos (LDAP „Who am I?“, RFC 4532) – oder None, wenn der Server es nicht liefert."""
    try:
        w = c.extend.standard.who_am_i()
    except Exception:
        return None
    w = str(w or "").strip()
    if w.lower().startswith("u:"):
        w = w[2:]
    if "\\" in w:
        return w.split("\\", 1)[1] or None
    return None  # andere Formen (dn:…) nicht raten – dann gilt der genaue Abgleich über UPN/sAMAccountName


_cfg_warned = {}


def check_sessions(items):
    """Nachprüfung laufender Sitzungen in EINEM Durchlauf (eine Verbindung des Dienstkontos, Gruppen nur einmal aufgelöst).
    items: [(schlüssel, sAMAccountName, rolle)] → {schlüssel: Ergebnis}
      True       = Hauptadmin (Zugriffsgruppe) bzw. Kiosk weiter erlaubt
      [ids]      = OU-Admin dieser Mandanten
      False      = Konto weg/deaktiviert bzw. in keiner passenden Gruppe mehr → abmelden
      None       = gerade nicht prüfbar (AD gestört, Dienstkonto-Schutz) → Sitzung bleibt, bald erneut prüfen
    Eine Störung zählt nie als „kein Mitglied“. „Zugriffsgruppe fehlt/mehrdeutig“ ist dagegen eine Konfigurationsfrage:
    dann ist niemand Hauptadmin (wie bei der Anmeldung) – die Mandanten-Gruppen werden trotzdem geprüft."""
    items = list(items)
    if not items:
        return {}
    try:
        c = service_conn()
    except Exception:
        return {k: None for k, _s, _r in items}
    out = {}
    try:
        try:
            acc = _group_dn(c)
        except GroupConfigError as e:
            acc = None
            if time.time() - _cfg_warned.get("acc", 0) > 900:  # höchstens alle 15 Minuten ins Protokoll
                _cfg_warned["acc"] = time.time()
                pklog.warn("auth", f"Nachprüfung der Sitzungen: {e} – bis das behoben ist, ist niemand Hauptadmin.")
        tenants = []
        for t in db.q("SELECT id, admin_group_dn, admin_group_sid FROM tenants WHERE COALESCE(admin_group, '') != '' "
                      "AND collection_id IS NOT NULL ORDER BY id"):
            gdn = group_dn_by_sid(c, t.get("admin_group_sid")) if t.get("admin_group_sid") else t.get("admin_group_dn")
            if gdn:  # Gruppe im AD gelöscht (SID unbekannt) → dieser Mandant gibt niemandem mehr Zugang
                tenants.append((int(t["id"]), gdn))
        kcache = {}
        for key, sam, role in items:
            try:
                found = _search(c, config.BASE_DN, f"(&(objectClass=user)(objectCategory=person)"
                                                   f"(sAMAccountName={escape_filter_chars(sam)}))",
                                ["distinguishedName", "memberOf", "userAccountControl", "primaryGroupID", "objectSid"])
                if not found:
                    out[key] = False
                    continue
                e = found[0]
                if int(_attr(e, "userAccountControl", 0) or 0) & 2:
                    out[key] = False
                    continue
                is_acc = bool(acc) and is_member(c, e, acc, strict=True)[0]
                if role == "kiosk":
                    out[key] = bool(is_acc or kiosk_match(c, e, strict=True, cache=kcache))
                    continue
                if is_acc:
                    out[key] = True
                    continue
                tids = [tid for tid, gdn in tenants if is_member(c, e, gdn, strict=True)[0]]
                out[key] = tids if tids else False
            except Exception:
                out[key] = None  # nur diese Sitzung gerade nicht prüfbar
    except Exception:
        for k, _s, _r in items:
            out.setdefault(k, None)
    finally:
        try:
            c.unbind()
        except Exception:
            pass
    for k, _s, _r in items:
        out.setdefault(k, None)
    return out


def still_allowed(sam, role="admin"):
    """Einzelne Sitzung nachprüfen (siehe check_sessions)."""
    return check_sessions([(0, sam, role)]).get(0)


def _admin_member(c, e):
    """Mitglied der Zugriffsgruppe? (Administratoren dürfen den Kiosk für ihren eigenen Rechner auch nutzen.)"""
    try:
        return bool(is_member(c, e, _group_dn(c))[0])
    except ADError:
        return False


def sso_user(sam):
    """Windows-Anmeldung am Kiosk: Benutzer aus einem gültigen Kerberos-Ticket mit dem Dienstkonto nachschlagen.
    Ergibt immer die Rolle „kiosk“ – die Verwaltung verlangt weiterhin Benutzername und Kennwort."""
    sam = str(sam or "").strip()
    if not sam or len(sam) > 256:
        raise ADError("Kein Benutzername im Ticket.")
    try:
        c = service_conn()
    except Exception as e:
        raise ADError(f"AD nicht erreichbar (Dienstkonto): {e}")
    try:
        found = _search(c, config.BASE_DN, f"(&(objectClass=user)(objectCategory=person)(|(sAMAccountName={escape_filter_chars(sam)})"
                        f"(userPrincipalName={escape_filter_chars(sam)}@*)))",
                        ["distinguishedName", "displayName", "sAMAccountName", "memberOf", "userAccountControl",
                         "primaryGroupID", "objectSid"])
        found = [e for e in found if str(_attr(e, "sAMAccountName", "") or "").lower() == sam.lower()]  # nur genau dieses Konto
        if not found:
            raise ADError("Benutzerobjekt nicht gefunden (Basis-DN prüfen).")
        e = found[0]
        if int(_attr(e, "userAccountControl", 0) or 0) & 2:
            raise ADError("Ihr Benutzerkonto ist deaktiviert.")
        if kiosk_match(c, e):
            how = "kiosk"
        elif _admin_member(c, e):
            how = "admin"
        else:
            raise ADError("Kein Zugriff: Sie gehören keiner Kiosk-Gruppe oder -OU an.")
        s2 = _attr(e, "sAMAccountName", sam)
        return {"username": s2, "display_name": _attr(e, "displayName", s2) or s2, "dn": e["dn"], "via": how, "role": "kiosk"}
    finally:
        try:
            c.unbind()
        except Exception:
            pass


def _cn(dn):
    m = re.match(r"\s*CN=((?:\\.|[^,])+)", dn or "", re.I)
    return m.group(1).replace("\\", "") if m else dn


def display_name(sam):
    """Anzeigename eines Benutzers (für „Angemeldeter Benutzer“)."""
    c = service_conn()
    try:
        res = _search(c, config.BASE_DN, f"(&(objectClass=user)(sAMAccountName={escape_filter_chars(sam)}))",
                      ["displayName"])
        return _attr(res[0], "displayName") if res else None
    finally:
        c.unbind()


# --- LAPS und BitLocker -------------------------------------------------------
LAPS_ATTRS = ["msLAPS-Password", "msLAPS-EncryptedPassword", "msLAPS-PasswordExpirationTime",
              "ms-Mcs-AdmPwd", "ms-Mcs-AdmPwdExpirationTime"]
BL_ATTRS = ["msFVE-RecoveryPassword", "msFVE-RecoveryGuid", "msFVE-VolumeGuid", "whenCreated", "cn"]


def _computer_dn(c, dn, name):
    if dn:
        return dn
    res = _search(c, config.BASE_DN, f"(&(objectClass=computer)(cn={escape_filter_chars(name)}))", ["distinguishedName"])
    if not res:
        raise ADError(f"Computerobjekt {name} wurde im AD nicht gefunden.")
    return res[0]["dn"]


def _read_attrs(c, dn, attrs):
    """Liest Attribute einzeln – unbekannte (Schema nicht erweitert) oder gesperrte werden übersprungen."""
    out = {}
    for a in attrs:
        try:
            ok = c.search(dn, "(objectClass=*)", "BASE", attributes=[a])
        except Exception:
            continue
        if not ok:
            continue
        for e in c.response or []:
            if e.get("type") == "searchResEntry":
                raw = e.get("raw_attributes", {}).get(a)
                if raw:
                    out[a] = raw[0] if isinstance(raw, list) else raw
    return out


def _txt(v):
    return v.decode("utf-8", "replace") if isinstance(v, (bytes, bytearray)) else str(v)


def laps(dn, name):
    """LAPS-Kennwort (Windows LAPS im Klartext oder Legacy LAPS). Verschlüsselte Kennwörter
    (Windows LAPS mit Verschlüsselung) können außerhalb von Windows nicht entschlüsselt werden."""
    c = service_conn()
    try:
        dn = _computer_dn(c, dn, name)
        a = _read_attrs(c, dn, LAPS_ATTRS)
    finally:
        c.unbind()
    res = {"dn": dn, "found": False}
    if "msLAPS-Password" in a:
        try:
            j = json.loads(_txt(a["msLAPS-Password"]))
            res.update({"found": True, "kind": "Windows LAPS", "account": j.get("n"), "password": j.get("p"),
                        "updated": _filetime(int(j.get("t", "0"), 16)) if j.get("t") else None})
        except (ValueError, TypeError):
            res.update({"found": True, "kind": "Windows LAPS", "password": _txt(a["msLAPS-Password"])})
        if "msLAPS-PasswordExpirationTime" in a:
            res["expires"] = _filetime(_txt(a["msLAPS-PasswordExpirationTime"]))
    elif "ms-Mcs-AdmPwd" in a:
        res.update({"found": True, "kind": "LAPS (Legacy)", "account": "Administrator", "password": _txt(a["ms-Mcs-AdmPwd"])})
        if "ms-Mcs-AdmPwdExpirationTime" in a:
            res["expires"] = _filetime(_txt(a["ms-Mcs-AdmPwdExpirationTime"]))
    elif "msLAPS-EncryptedPassword" in a:
        res.update({"found": True, "kind": "Windows LAPS (verschlüsselt)", "encrypted": True,
                    "hint": "Das Kennwort ist im AD verschlüsselt gespeichert und kann nur unter Windows "
                            "(Get-LapsADPassword) entschlüsselt werden. Für POCKET KNIFE die LAPS-Verschlüsselung "
                            "für diese OU deaktivieren oder das Kennwort direkt in AD-Benutzer und -Computer ansehen."})
        if "msLAPS-PasswordExpirationTime" in a:
            res["expires"] = _filetime(_txt(a["msLAPS-PasswordExpirationTime"]))
    if not res["found"]:
        res["hint"] = ("Kein LAPS-Kennwort lesbar: LAPS ist für diesen Computer nicht aktiv, oder das Dienstkonto hat "
                       "kein Leserecht (Windows LAPS: Set-LapsADReadPasswordPermission; Legacy: "
                       "Set-AdmPwdReadPasswordPermission auf die OU).")
    return res


def bitlocker_keys(dn, name):
    """BitLocker-Wiederherstellungsschlüssel aus den msFVE-RecoveryInformation-Objekten unter dem Computer."""
    c = service_conn()
    try:
        dn = _computer_dn(c, dn, name)
        try:
            entries = [e for e in c.extend.standard.paged_search(dn, "(objectClass=msFVE-RecoveryInformation)", "LEVEL",
                                                                 attributes=BL_ATTRS, paged_size=100, generator=True)
                       if e.get("type") == "searchResEntry"]
        except Exception:
            entries = []
    finally:
        c.unbind()
    keys = []
    for e in entries:
        raw = e.get("raw_attributes", {})
        pw = raw.get("msFVE-RecoveryPassword")
        guid = raw.get("msFVE-RecoveryGuid")
        cn = _txt((raw.get("cn") or [b""])[0])
        wc = (raw.get("whenCreated") or [None])[0]
        created = cn.split("{")[0] if "{" in cn else (_gentime(_txt(wc)) if wc else "")
        kid = ""
        if guid:
            g = guid[0]
            kid = "{" + str(uuid.UUID(bytes_le=bytes(g))).upper() + "}" if len(g) == 16 else _txt(g)
        keys.append({"id": kid, "password": _txt(pw[0]) if pw else None, "created": created.replace("T", " ")[:19]})
    keys.sort(key=lambda k: k["created"] or "", reverse=True)
    res = {"dn": dn, "keys": keys}
    if not keys:
        res["hint"] = ("Keine BitLocker-Schlüssel im AD lesbar: Die Sicherung ins AD ist nicht aktiviert, oder das "
                       "Dienstkonto darf msFVE-RecoveryInformation nicht lesen (Recht „Vollzugriff“ bzw. "
                       "„Lesen“ auf msFVE-RecoveryInformation-Objekte delegieren).")
    elif any(k["password"] is None for k in keys):
        res["hint"] = "Einträge vorhanden, aber das Dienstkonto darf das Attribut msFVE-RecoveryPassword nicht lesen."
    return res


# Nur Windows-Computer (andere Betriebssysteme werden nicht unterstützt)
WINDOWS_FILTER = "(&(objectClass=computer)(operatingSystem=Windows*))"


# --- Computer-Abgleich -------------------------------------------------------
COMPUTER_ATTRS = ["cn", "dNSHostName", "operatingSystem", "operatingSystemVersion", "distinguishedName",
                  "lastLogonTimestamp", "whenCreated", "userAccountControl", "description", "objectGUID", "location",
                  "memberOf"]


def _guid(e):
    raw_guid = _raw(e, "objectGUID")
    if not raw_guid:
        return None
    return str(uuid.UUID(bytes_le=bytes(raw_guid))) if len(raw_guid) == 16 else str(_attr(e, "objectGUID"))


def _upsert(e, now):
    """Computerobjekt aus dem AD übernehmen. Gibt (computer_id, neu?) zurück."""
    guid = _guid(e)
    name = _attr(e, "cn") or ""
    dns = _attr(e, "dNSHostName") or (f"{name}.{config.DOMAIN}" if config.DOMAIN else name)
    uac = int(_attr(e, "userAccountControl", 0) or 0)
    row = (name, dns, e["dn"], _attr(e, "description"), _attr(e, "operatingSystem"),
           _attr(e, "operatingSystemVersion"), 0 if uac & 2 else 1,
           _filetime(_raw(e, "lastLogonTimestamp")), _gentime(_raw(e, "whenCreated")))
    ex = db.q1("SELECT id FROM computers WHERE ad_guid=?", (guid,))
    if not ex:
        ex = db.q1("SELECT id FROM computers WHERE (ad_guid IS NULL OR ad_guid='') AND lower(name)=lower(?)", (name,))
        if ex:
            db.ex("UPDATE computers SET ad_guid=? WHERE id=?", (guid, ex["id"]))
    new = False
    if ex:
        db.ex("""UPDATE computers SET name=?, dns_name=?, dn=?, description=?, ad_os=?, ad_os_version=?,
                 ad_enabled=?, ad_last_logon=?, ad_created=?, ad_present=1 WHERE id=?""", row + (ex["id"],))
        cid = ex["id"]
    else:
        cid = db.ex("""INSERT INTO computers(name, dns_name, dn, description, ad_os, ad_os_version, ad_enabled,
                 ad_last_logon, ad_created, ad_guid, ad_present, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,1,?)""",
                    row + (guid, now))
        new = True
    mo = e["attributes"].get("memberOf") or []
    mo = mo if isinstance(mo, list) else [mo]
    groups = [{"name": _cn(g), "dn": g} for g in mo]
    db.ex("UPDATE computers SET ad_location=? WHERE id=?", (_attr(e, "location"), cid))
    db.ex("INSERT INTO inventory_data(computer_id, category, json, updated) VALUES(?,?,?,?) "
          "ON CONFLICT(computer_id, category) DO UPDATE SET json=excluded.json, updated=excluded.updated",
          (cid, "ad_groups", json.dumps(sorted(groups, key=lambda x: x["name"].lower()), ensure_ascii=False), now))
    return cid, new


def refresh_computer(cid):
    """Scanner „Active Directory“: AD-Daten eines einzelnen Computers neu lesen."""
    comp = db.q1("SELECT name, dn, ad_guid FROM computers WHERE id=?", (cid,))
    if not comp:
        return False
    c = service_conn()
    try:
        flt = f"(&(objectClass=computer)(cn={escape_filter_chars(comp['name'])}))"
        res = _search(c, config.BASE_DN, flt, COMPUTER_ATTRS)
    finally:
        c.unbind()
    if comp.get("ad_guid"):
        res = [e for e in res if _guid(e) == comp["ad_guid"]] or res
    if not res:
        db.ex("UPDATE computers SET ad_present=0 WHERE id=?", (cid,))
        return False
    with db.tx():
        _upsert(res[0], util.now())
    return True


# --- Container (OUs) ein-/ausschließen ---------------------------------------------------
def split_dn(dn):
    """DN in RDNs zerlegen (beachtet maskierte Kommas „\\,“)."""
    parts, cur, esc = [], "", False
    for ch in dn or "":
        if esc:
            cur += ch
            esc = False
        elif ch == "\\":
            cur += ch
            esc = True
        elif ch == ",":
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return parts


def parent_dn(dn):
    p = split_dn(dn)
    return ",".join(p[1:]) if len(p) > 1 else ""


def rdn_name(dn):
    p = split_dn(dn)
    if not p:
        return dn
    v = p[0].split("=", 1)[-1]
    return re.sub(r"\\(.)", r"\1", v)


def dn_path(dn):
    """„OU=MOBILE,OU=WORKSTATION,DC=firma,DC=local“ → „firma.local/WORKSTATION/MOBILE“"""
    parts = split_dn(dn)
    dcs = [p.split("=", 1)[1] for p in parts if p.upper().startswith("DC=")]
    rest = [re.sub(r"\\(.)", r"\1", p.split("=", 1)[1]) for p in parts if not p.upper().startswith("DC=")]
    return "/".join([".".join(dcs)] + list(reversed(rest)))


def _is_under(dn, base):
    a, b = norm_dn(dn), norm_dn(base)
    return a == b or a.endswith("," + b)


def container_rules():
    """[{dn, include}] – Standard: die Basis(en) aus der .env einschließen."""
    raw = db.setting("ad_containers")
    try:
        rules = json.loads(raw) if raw else None
    except ValueError:
        rules = None
    if not rules:
        rules = [{"dn": b, "include": True} for b in config.COMPUTER_BASE_DNS]
    return [{"dn": r["dn"], "include": bool(r.get("include", True))} for r in rules if r.get("dn")]


def rule_for(dn, rules):
    """Nächstgelegene Regel (längster passender Container) entscheidet."""
    best = None
    for r in rules:
        if _is_under(dn, r["dn"]) and (best is None or len(norm_dn(r["dn"])) > len(norm_dn(best["dn"]))):
            best = r
    return best


def list_containers():
    """Alle OUs und Container der Domäne (für die Auswahl in den Einstellungen)."""
    c = service_conn()
    try:
        res = _search(c, config.BASE_DN, "(|(objectClass=organizationalUnit)(&(objectClass=container)(!(isCriticalSystemObject=FALSE))))",
                      ["distinguishedName", "description"])
        res = [e for e in res if not re.match(r"(?i)^CN=(System|Program Data|ForeignSecurityPrincipals|Managed Service Accounts|"
                                               r"Keys|LostAndFound|NTDS Quotas|TPM Devices|Infrastructure|Builtin|Users)(,|$)", e["dn"])
               or e["dn"].upper().startswith("CN=COMPUTERS,")]
    finally:
        c.unbind()
    counts = {}
    for r in db.q("SELECT dn FROM computers WHERE dn IS NOT NULL AND ad_present=1"):
        p = parent_dn(r["dn"])
        while p:
            counts[norm_dn(p)] = counts.get(norm_dn(p), 0) + 1
            if norm_dn(p) == norm_dn(config.BASE_DN):
                break
            p = parent_dn(p)
    out = [{"dn": config.BASE_DN, "path": dn_path(config.BASE_DN), "count": counts.get(norm_dn(config.BASE_DN), 0)}]
    for e in res:
        out.append({"dn": e["dn"], "path": dn_path(e["dn"]), "count": counts.get(norm_dn(e["dn"]), 0)})
    out.sort(key=lambda x: x["path"].lower())
    return out


# --- AD durchsuchen (Baumauswahl für OUs und Gruppen) -------------------------------------------
_HIDDEN = re.compile(r"(?i)^CN=(System|Program Data|ForeignSecurityPrincipals|Managed Service Accounts|Keys|LostAndFound|"
                     r"NTDS Quotas|TPM Devices|Infrastructure)(,|$)")
_GROUP_TYPE = {2: "global", 4: "lokal (Domäne)", 8: "universell"}


def _under_base(dn):
    b = norm_dn(config.BASE_DN)
    d = norm_dn(dn)
    return d == b or d.endswith("," + b)


def _oc_list(e):
    """objectClass als Liste kleingeschriebener Namen – ldap3 liefert ohne Schema-Abruf (get_info=NONE) Listen."""
    v = (e.get("attributes") or {}).get("objectClass") or []
    if not isinstance(v, (list, tuple)):
        v = [v]
    return [str(x).lower() for x in v]


def _browse_item(e):
    oc = _oc_list(e)
    name = str(_attr(e, "name") or rdn_name(e["dn"]))
    desc = str(_attr(e, "description") or "")[:200]
    if "group" in oc:
        try:
            gt = int(_attr(e, "groupType") or 0)
        except (TypeError, ValueError):
            gt = 0
        return {"dn": e["dn"], "name": name, "kind": "group", "sam": str(_attr(e, "sAMAccountName") or name),
                "desc": desc, "scope": _GROUP_TYPE.get(gt & 0xE, ""), "security": bool(gt & 0x80000000)}
    return {"dn": e["dn"], "name": name, "kind": "ou" if "organizationalunit" in oc else "container", "desc": desc}


def browse(dn=None, groups=False):
    """Eine Ebene des AD: OUs/Container (aufklappbar) und – bei groups=True – die Gruppen darin."""
    base = dn or config.BASE_DN
    if not _under_base(base):
        raise ADError("Nur Einträge innerhalb der eigenen Domäne.")
    flt = "(|(objectClass=organizationalUnit)(objectClass=container)(objectClass=builtinDomain)" + \
          ("(objectClass=group)" if groups else "") + ")"
    c = service_conn()
    try:
        c.search(base, flt, LEVEL, attributes=["objectClass", "name", "description", "sAMAccountName", "groupType"],
                 paged_size=1000)
        rows = [e for e in (c.response or []) if e.get("type") == "searchResEntry"]
    finally:
        c.unbind()
    out = []
    for e in rows:
        if _HIDDEN.match(e["dn"]) or (not groups and e["dn"].upper().startswith("CN=BUILTIN,")):
            continue
        out.append(_browse_item(e))
    out.sort(key=lambda x: (x["kind"] == "group", x["name"].lower()))
    return {"dn": base, "path": dn_path(base), "items": out}


def search_groups(q, limit=50):
    q = (q or "").strip()
    if len(q) < 2:
        return []
    esc = escape_filter_chars(q)
    c = service_conn()
    try:
        c.search(config.BASE_DN, f"(&(objectClass=group)(|(cn=*{esc}*)(sAMAccountName=*{esc}*)(description=*{esc}*)))", SUBTREE,
                 attributes=["objectClass", "name", "sAMAccountName", "description", "groupType"], size_limit=limit)
        rows = [e for e in (c.response or []) if e.get("type") == "searchResEntry"]
    finally:
        c.unbind()
    out = []
    for e in rows:
        it = _browse_item(e)
        it["kind"] = "group"
        it["path"] = dn_path(parent_dn(e["dn"]) or e["dn"])
        out.append(it)
    return sorted(out, key=lambda x: x["name"].lower())


AD_DELETE_GRACE_DAYS = 7  # „aus POCKET KNIFE löschen“: erst nach so vielen Tagen durchgehender Abwesenheit


def sync_computers(user=None):
    flt = WINDOWS_FILTER  # fest: nur Windows-Computer
    attrs = COMPUTER_ATTRS
    rules = container_rules()
    bases = [r["dn"] for r in rules if r["include"]]
    # Basen, die unter einer anderen eingeschlossenen Basis liegen, nicht doppelt durchsuchen
    bases = [b for b in bases if not any(o != b and _is_under(b, o) for o in bases)]
    sync_disabled = bool(db.setting("ad_sync_disabled"))
    if not bases:
        # Ohne eingeschlossenen Container würde „nichts gefunden“ als „alle verschwunden“ gelten
        db.set_setting("ad_last_sync", util.now())
        db.set_setting("ad_last_status", "Fehler: kein Container eingeschlossen (Einstellungen → Active Directory)")
        raise ADError("Kein AD-Container eingeschlossen – Abgleich übersprungen (Einstellungen → Active Directory).")
    try:
        c = service_conn()
    except ADError as e:
        db.set_setting("ad_last_sync", util.now())
        db.set_setting("ad_last_status", f"Fehler: {e}")
        raise
    found = []
    try:
        for base in bases:
            found.extend(_search(c, base, flt, attrs))
    except Exception as e:
        db.set_setting("ad_last_sync", util.now())
        db.set_setting("ad_last_status", f"Fehler: {e}")
        raise
    finally:
        c.unbind()
    keep = []
    disabled = set()  # deaktiviert und nicht abgeglichen – aber weiterhin im AD, also nicht „verschwunden“
    for e in found:
        r = rule_for(e["dn"], rules)
        if not r or not r["include"]:
            continue
        if not sync_disabled and int(_attr(e, "userAccountControl", 0) or 0) & 2:
            g = _guid(e)
            if g:
                disabled.add(g)
            continue
        keep.append(e)
    seen, added, updated, new_ids = set(), 0, 0, []
    now = util.now()
    with db.tx():
        for e in keep:
            guid = _guid(e)
            if not guid or guid in seen:
                continue
            seen.add(guid)
            cid, new = _upsert(e, now)
            if new:
                added += 1
                new_ids.append(cid)
            else:
                updated += 1
        known = db.q("SELECT id, ad_guid, COALESCE(ad_present, 1) present, ad_missing_since FROM computers "
                     "WHERE ad_guid IS NOT NULL AND ad_guid != ''")
        dis_ids = [r["id"] for r in known if r["ad_guid"] in disabled]
        if dis_ids:
            ph = ",".join("?" * len(dis_ids))
            db.ex(f"UPDATE computers SET ad_enabled=0, ad_present=1, ad_missing_since=NULL WHERE id IN ({ph})", dis_ids)
        db.ex("UPDATE computers SET ad_missing_since=NULL WHERE ad_present=1 AND ad_missing_since IS NOT NULL")
        present = [r for r in known if r["present"]]
        # Nur bisher vorhandene zählen – bereits als fehlend markierte würden den Schutz sonst dauerhaft auslösen
        missing = [r["id"] for r in present if r["ad_guid"] not in seen and r["ad_guid"] not in disabled]
        # Schutz: verschwindet auf einmal ein großer Teil (oder alles), ist eher die Suche/der Container falsch als der Bestand
        if missing and ((len(present) >= 10 and len(missing) > max(5, len(present) * 0.25))
                        or (len(present) >= 3 and len(missing) == len(present))):
            pklog.warn("ad", f"AD-Abgleich: {len(missing)} von {len(present)} Computern wären nicht mehr im Abgleich – "
                             f"zur Sicherheit nicht als fehlend markiert/gelöscht. Container-Einstellungen prüfen.", user=user)
            missing = []
        if missing:
            ph = ",".join("?" * len(missing))
            db.ex(f"UPDATE computers SET ad_present=0, ad_missing_since=? WHERE id IN ({ph})", [now] + missing)
        # Ältere „nicht im AD“-Einträge (vor dieser Version) starten ihre Karenzzeit jetzt
        db.ex("UPDATE computers SET ad_missing_since=? WHERE ad_present=0 AND ad_missing_since IS NULL", (now,))
        removed = 0
        if db.setting("ad_remove_missing"):
            # Löschen erst nach einer Karenzzeit: ein Computer muss AD_DELETE_GRACE_DAYS durchgehend fehlen
            # (schützt vor fehlenden Leserechten auf eine OU, kurzzeitigen LDAP-Problemen o. Ä.)
            gone = [r["id"] for r in db.q("SELECT id FROM computers WHERE ad_present=0 AND ad_missing_since IS NOT NULL "
                                          "AND ad_missing_since < ?", (util.ago(days=AD_DELETE_GRACE_DAYS),))]
            if gone:
                db.ex(f"DELETE FROM computers WHERE id IN ({','.join('?' * len(gone))})", gone)
                removed = len(gone)
    try:
        sync_ad_collections()
    except Exception as e:  # Sammlungen dürfen den Abgleich nicht scheitern lassen
        pklog.warn("ad", f"AD-Sammlungen konnten nicht aktualisiert werden: {e}")
    if new_ids:
        # Neue Computer: mit dem Standardprofil scannen, sobald sie online sind
        from . import profiles
        try:
            profiles.mark_new(new_ids)
        except Exception:
            pass
    status = (f"Erfolgreich: {len(seen)} Computer ({added} neu, {updated} aktualisiert, "
              f"{len(missing)} nicht mehr im Abgleich" + (f", {removed} nach {AD_DELETE_GRACE_DAYS} Tagen gelöscht" if removed else "") + ")")
    if removed:
        pklog.info("ad", f"AD-Abgleich: {removed} Computer gelöscht (seit über {AD_DELETE_GRACE_DAYS} Tagen nicht mehr im AD).", user=user)
    db.set_setting("ad_last_sync", now)
    db.set_setting("ad_last_status", status)
    pklog.info("ad", f"AD-Abgleich: {len(found)} Computer gefunden, {added} neu, {updated} aktualisiert, "
                     f"{len(missing)} nicht mehr im Abgleich.", user=user)
    return {"found": len(seen), "added": added, "updated": updated, "missing": len(missing)}


def _referenced_collections():
    ids = set()
    for table, col in (("schedules", "targets"), ("scan_profiles", "collections")):
        for r in db.q(f"SELECT {col} AS v FROM {table}"):
            try:
                v = json.loads(r["v"] or "null")
            except ValueError:
                continue
            if isinstance(v, dict):
                v = v.get("collections") or []
            ids.update(int(x) for x in (v or []) if str(x).lstrip("-").isdigit())
    try:
        from . import access
        for r in db.q("SELECT collection_id, collection_ids FROM tenants"):
            ids.update(access.tenant_cols(r))
    except Exception:
        pass
    try:
        from . import collections_
        ids.update(collections_.referenced_ids())
    except Exception:
        pass
    return ids


# --- Automatische Sammlungen aus OUs und AD-Gruppen ------------------------------------------
AD_BUILTIN = 2  # builtin=2: von POCKET KNIFE aus dem AD gepflegt (nicht änderbar, aber kopierbar)


def _ad_coll(kind, dn, name, parent, ctype, flt, sort):
    row = db.q1("SELECT id FROM collections WHERE ad_kind=? AND lower(ad_dn)=lower(?)", (kind, dn))
    fj = json.dumps(flt, ensure_ascii=False)
    if row:
        db.ex("UPDATE collections SET name=?, parent_id=?, type=?, match='all', filters=?, builtin=?, sort=? WHERE id=?",
              (name, parent, ctype, fj, AD_BUILTIN, sort, row["id"]))
        return row["id"]
    return db.ex("INSERT INTO collections(name, parent_id, type, match, filters, builtin, sort, ad_dn, ad_kind) "
                 "VALUES(?,?,?,'all',?,?,?,?,?)", (name, parent, ctype, fj, AD_BUILTIN, sort, dn, kind))


def _ends(dn):
    return {"type": "group", "match": "any", "items": [
        {"type": "cond", "table": "computer", "column": "ou", "op": "ends", "value": "," + dn}]}


def sync_ad_collections():
    """Spiegelt die OU-Struktur (nur Container mit Computern) und die Computergruppen als Sammlungen."""
    want_ou = bool(db.setting("ad_ou_collections"))
    want_grp = bool(db.setting("ad_group_collections"))
    keep = set()
    with db.tx():
        root = None
        if want_ou or want_grp:
            root = _ad_coll("root", config.BASE_DN, config.DOMAIN or dn_path(config.BASE_DN), None, "dynamic",
                            _ends(config.BASE_DN), -10)
            keep.add(root)
        if want_ou:
            base = norm_dn(config.BASE_DN)
            containers = set()
            for r in db.q("SELECT dn FROM computers WHERE dn IS NOT NULL AND dn != '' AND ad_present=1"):
                p = parent_dn(r["dn"])
                while p and norm_dn(p) != base and _is_under(p, config.BASE_DN):
                    containers.add(p)
                    p = parent_dn(p)
            ids = {base: root}
            for dn in sorted(containers, key=lambda d: len(split_dn(d))):
                par = ids.get(norm_dn(parent_dn(dn)), root)
                ids[norm_dn(dn)] = _ad_coll("ou", dn, rdn_name(dn), par, "dynamic", _ends(dn), 0)
                keep.add(ids[norm_dn(dn)])
        if want_grp:
            groups = {}
            for r in db.q("SELECT json FROM inventory_data WHERE category='ad_groups'"):
                try:
                    for g in json.loads(r["json"] or "[]"):
                        if g.get("dn"):
                            groups[norm_dn(g["dn"])] = g
                except ValueError:
                    continue
            if groups:
                folder = _ad_coll("groups", "groups:" + config.BASE_DN, "Gruppen", root, "folder",
                                  {"type": "group", "match": "all", "items": []}, 99)
                keep.add(folder)
                for g in sorted(groups.values(), key=lambda x: x["name"].lower()):
                    flt = {"type": "group", "match": "all", "items": [
                        {"type": "cond", "table": "ad_groups", "column": "dn", "op": "equals", "value": g["dn"]}]}
                    keep.add(_ad_coll("group", g["dn"], g["name"], folder, "dynamic", flt, 0))
        # Von Zeitplänen, Scanprofilen oder Mandanten verwendete AD-Sammlungen bleiben (dann eben leer) erhalten,
        # damit deren Ziele nicht stillschweigend verschwinden, wenn eine OU/Gruppe vorübergehend leer ist
        used = _referenced_collections()
        allc = {r["id"]: r["parent_id"] for r in db.q("SELECT id, parent_id FROM collections")}
        for cid in list(used):
            p = cid
            while p in allc and p not in keep:
                if db.val("SELECT builtin FROM collections WHERE id=?", (p,)) == AD_BUILTIN:
                    keep.add(p)
                p = allc.get(p)
        old = [r["id"] for r in db.q("SELECT id FROM collections WHERE builtin=? AND ad_kind IS NOT NULL", (AD_BUILTIN,))
               if r["id"] not in keep]
        for oid in old:
            db.ex("UPDATE collections SET parent_id=NULL WHERE parent_id=? AND builtin != ?", (oid, AD_BUILTIN))
            db.ex("DELETE FROM collections WHERE id=?", (oid,))
    return len(keep)
