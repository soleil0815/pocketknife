"""E-Mail-Versand über SMTP (nur Standardbibliothek).

Einstellungen werden in der Oberfläche gepflegt (Einstellungen → E-Mail); das Kennwort liegt verschlüsselt
im Tresor (pocketknife.key). Mails werden als HTML mit Textalternative verschickt, ohne externe Inhalte.
"""
import html
import re
import smtplib
import socket
import ssl
import threading
import time
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from . import config, db, pklog, util

SECURITY = {"starttls": "STARTTLS (meist Port 587)", "ssl": "SSL/TLS (meist Port 465)", "none": "Unverschlüsselt (Port 25)"}
DEFAULT_PORT = {"starttls": 587, "ssl": 465, "none": 25}
KEYS = ("mail_enabled", "mail_host", "mail_port", "mail_security", "mail_verify", "mail_user", "mail_from",
        "mail_from_name", "mail_to", "mail_subject_prefix")
ADDR_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9-]{1,63})*$")
MAX_RECIPIENTS = 50
MAX_PER_HOUR = 120  # Schutz gegen Mailfluten (z. B. falsch eingestellte Benachrichtigungen)
MAX_ATTACH = 20 * 1024 * 1024

_lock = threading.Lock()
_sent = []  # Zeitstempel der letzten Sendungen (Ratenbegrenzung)


class MailError(Exception):
    pass


# --- Einstellungen ------------------------------------------------------------------------------
def settings():
    s = {k: db.setting_raw(k) for k in KEYS}
    sec = s["mail_security"] if s["mail_security"] in SECURITY else "starttls"
    try:
        port = int(s["mail_port"] or 0)
    except ValueError:
        port = 0
    return {"enabled": s["mail_enabled"] == "1", "host": s["mail_host"], "port": port or DEFAULT_PORT[sec],
            "security": sec, "verify": s["mail_verify"] != "0", "user": s["mail_user"],
            "password_set": bool(db.setting_raw("mail_password")), "from": s["mail_from"],
            "from_name": s["mail_from_name"] or "POCKET KNIFE", "to": s["mail_to"],
            "subject_prefix": s["mail_subject_prefix"] if s["mail_subject_prefix"] else "[POCKET KNIFE]",
            "last": db.setting_raw("mail_last"), "securities": SECURITY}


def configured():
    s = settings()
    return bool(s["enabled"] and s["host"] and s["from"])


def parse_recipients(text, label="Empfänger", required=True):
    """'a@x.de; b@y.de' → ['a@x.de', 'b@y.de'] (geprüft, ohne Doppelte)."""
    out, seen = [], set()
    # „Max Muster <max@firma.de>“ → nur die Adresse
    text = re.sub(r'"[^"]*"', " ", text or "")
    text = re.sub(r"[^,;<>]*<([^<>]+)>", r" \1 ", text)
    for part in re.split(r"[,;\s]+", text):
        part = part.strip().strip("<>")
        if not part:
            continue
        if not ADDR_RE.match(part) or len(part) > 254:
            raise ValueError(f"{label}: „{part}“ ist keine gültige E-Mail-Adresse.")
        if part.lower() not in seen:
            seen.add(part.lower())
            out.append(part)
    if len(out) > MAX_RECIPIENTS:
        raise ValueError(f"{label}: höchstens {MAX_RECIPIENTS} Adressen.")
    if required and not out:
        raise ValueError(f"{label}: mindestens eine E-Mail-Adresse angeben.")
    return out


def save(data, user=None):
    from . import vault
    sec = data.get("security") if data.get("security") in SECURITY else "starttls"
    host = (data.get("host") or "").strip()
    if host and not re.match(r"^[A-Za-z0-9.\-:\[\]]{1,253}$", host):
        raise ValueError("SMTP-Server: ungültiger Hostname.")
    try:
        port = int(data.get("port") or DEFAULT_PORT[sec])
    except (TypeError, ValueError):
        raise ValueError("Port: Zahl angeben.")
    if not 1 <= port <= 65535:
        raise ValueError("Port: 1 bis 65535.")
    sender = (data.get("from") or "").strip()
    if sender and not ADDR_RE.match(sender):
        raise ValueError("Absender: keine gültige E-Mail-Adresse.")
    name = (data.get("from_name") or "").strip()
    prefix = (data.get("subject_prefix") if data.get("subject_prefix") is not None else "[POCKET KNIFE]").strip()
    usr = (data.get("user") or "").strip()
    for label, v in (("Absendername", name), ("Betreff-Präfix", prefix), ("Benutzer", usr)):
        if any(ch in v for ch in "\r\n"):
            raise ValueError(f"{label}: ungültige Zeichen.")
    to = ", ".join(parse_recipients(data.get("to"), "Standard-Empfänger", required=False))
    enabled = bool(data.get("enabled"))
    if enabled and (not host or not sender):
        raise ValueError("Zum Aktivieren SMTP-Server und Absender angeben.")
    if usr and sec == "none":
        raise ValueError("Anmeldung nur mit Verschlüsselung (STARTTLS oder SSL/TLS) – sonst ginge das Kennwort im Klartext übers Netz.")
    old = settings()
    # Audit 2.29: auch das Abschalten der Zertifikatsprüfung zählt (sonst ginge das Kennwort an einen Server mit falschem Zertifikat)
    moved = old["password_set"] and ((host.lower(), port, sec, usr.lower()) != (old["host"].lower(), old["port"], old["security"], old["user"].lower())
                                     or (old.get("verify", True) and not data.get("verify", True)))
    if moved and not data.get("password") and usr:
        # Gespeichertes Kennwort nie an einen anderen Server/Port/Benutzer schicken – neu eingeben lassen
        raise ValueError("Server, Port, Verschlüsselung oder Benutzer geändert – bitte das Kennwort erneut eingeben.")
    for k, v in (("mail_enabled", "1" if enabled else "0"), ("mail_host", host), ("mail_port", port),
                 ("mail_security", sec), ("mail_verify", "1" if data.get("verify", True) else "0"),
                 ("mail_user", usr), ("mail_from", sender), ("mail_from_name", name[:80]),
                 ("mail_to", to), ("mail_subject_prefix", prefix[:60])):
        db.set_setting(k, v)
    if data.get("clear_password") or not usr:
        db.ex("DELETE FROM settings WHERE key='mail_password'")
    elif data.get("password"):
        db.set_setting("mail_password", vault.encrypt(str(data["password"])))
    pklog.info("mail", f"E-Mail-Einstellungen gespeichert ({host or 'kein Server'}:{port}, {sec}"
                       f"{', aktiv' if enabled else ', deaktiviert'}).", user=user)
    return settings()


# --- HTML-Bausteine -----------------------------------------------------------------------------
E = html.escape
CSS_TABLE = "border-collapse:collapse;font-size:13px;margin:6px 0 4px"
CSS_TH = "text-align:left;background:#eef2f7;border:1px solid #d5dbe3;padding:4px 8px;white-space:nowrap"
CSS_TD = "border:1px solid #e1e5eb;padding:3px 8px;vertical-align:top"


def html_table(columns, rows, limit=None):
    """columns: [{key,label}] – rows: [dict]. Gibt (html, text) zurück."""
    shown = rows if limit is None else rows[:limit]
    head = "".join(f'<th style="{CSS_TH}">{E(str(c["label"]))}</th>' for c in columns)
    body = []
    for r in shown:
        cells = "".join(f'<td style="{CSS_TD}{";text-align:right" if c.get("type") in ("number", "bytes") else ""}">'
                        f'{E(_cell(r.get(c["key"]), c.get("type")))}</td>' for c in columns)
        body.append(f"<tr>{cells}</tr>")
    more = ""
    if limit is not None and len(rows) > limit:
        more = f'<p style="color:#667;font-size:12px;margin:2px 0 0">… und {len(rows) - limit} weitere Zeilen</p>'
    h = f'<table style="{CSS_TABLE}"><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>{more}'
    text = "\n".join(" | ".join(_cell(r.get(c["key"]), c.get("type")) for c in columns) for r in shown)
    if limit is not None and len(rows) > limit:
        text += f"\n… und {len(rows) - limit} weitere Zeilen"
    return h, " | ".join(str(c["label"]) for c in columns) + "\n" + text


def _cell(v, typ=None):
    if v is None:
        return ""
    if typ == "bytes" and str(v).strip().replace(".", "", 1).isdigit():
        from .collections_ import fmt_bytes
        return fmt_bytes(v)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def base_url():
    host = config.PUBLIC_HOSTNAME
    if not host:
        return ""
    scheme = "https" if config.HTTPS else "http"
    port = "" if (config.HTTPS and config.PORT == 443) or (not config.HTTPS and config.PORT == 80) else f":{config.PORT}"
    return f"{scheme}://{host}{port}/"


def wrap(title, inner_html, footer=None):
    url = base_url()
    foot = footer or f"Gesendet von POCKET KNIFE {config.VERSION} am {util.now()[:16]}."
    if url:
        foot += f' <a href="{E(url)}" style="color:#2a78d6">{E(url)}</a>'
    return (f'<!doctype html><html><body style="margin:0;padding:16px;background:#f6f7f9;font-family:Segoe UI,Arial,sans-serif;'
            f'color:#1d2430"><div style="max-width:1100px;background:#fff;border:1px solid #dde2e8;border-radius:6px;padding:16px 20px">'
            f'<h2 style="margin:0 0 10px;font-size:18px">{E(title)}</h2>{inner_html}'
            f'<p style="color:#8a93a0;font-size:11px;margin:18px 0 0;border-top:1px solid #eee;padding-top:8px">{foot}</p>'
            f'</div></body></html>')


# --- Versand ------------------------------------------------------------------------------------
def _rate_ok():
    now = time.time()
    with _lock:
        while _sent and now - _sent[0] > 3600:
            _sent.pop(0)
        return len(_sent) < MAX_PER_HOUR


def _rate_add():
    """Nur zugestellte Mails zählen – Fehlversuche (Server weg) dürfen das Kontingent nicht aufbrauchen."""
    with _lock:
        _sent.append(time.time())


def helo_name():
    """Name für EHLO/HELO: voll qualifiziert (pocketknife.firma.local) statt der zufälligen Container-ID –
    viele Mailserver und Spamfilter werten einen nicht auflösbaren HELO-Namen ab oder verwerfen die Mail."""
    host = (config.PUBLIC_HOSTNAME or "").strip()
    if host and "." not in host and config.DOMAIN:
        host = f"{host}.{config.DOMAIN}"
    return host or socket.getfqdn()


def _r(resp):
    return resp.decode(errors="replace") if isinstance(resp, bytes) else str(resp or "")


def _connect(s, password, log):
    timeout = 30
    ctx = ssl.create_default_context()
    if not s["verify"]:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    helo = helo_name()
    c = None
    try:
        # Host im Konstruktor übergeben: smtplib braucht ihn als server_hostname für die Zertifikatsprüfung
        # (STARTTLS und SSL/TLS) – ein späteres connect() setzt ihn nicht.
        if s["security"] == "ssl":
            c = smtplib.SMTP_SSL(s["host"], s["port"], timeout=timeout, context=ctx, local_hostname=helo)
        else:
            c = smtplib.SMTP(s["host"], s["port"], timeout=timeout, local_hostname=helo)
        log.append(f"Verbunden mit {s['host']}:{s['port']}{' (SSL/TLS)' if s['security'] == 'ssl' else ''}")
        code, resp = c.ehlo()
        log.append(f"EHLO {helo} → {code} {util.first_line(_r(resp)) if resp else ''}")
        if s["security"] == "starttls":
            if not c.has_extn("starttls"):
                c.quit()
                raise MailError("Der Server bietet kein STARTTLS an – Verschlüsselung prüfen.")
            code, resp = c.starttls(context=ctx)
            log.append(f"STARTTLS → {code} {_r(resp)}")
            c.ehlo()
        if s["user"]:
            if not c.has_extn("auth"):
                c.quit()
                raise MailError("Der Server bietet keine Anmeldung (AUTH) an – Benutzer leer lassen oder Verschlüsselung aktivieren.")
            code, resp = c.login(s["user"], password)
            log.append(f"Anmeldung als {s['user']} → {code} {_r(resp)}")
        else:
            log.append("Ohne Anmeldung (Relay)")
        return c
    except BaseException:
        if c is not None:
            try:
                c.close()  # Verbindung nach Fehler (STARTTLS, Anmeldung …) nicht offen lassen
            except Exception:
                pass
        _translate_raise(s)


def _translate_raise(s):
    import sys
    e = sys.exc_info()[1]
    try:
        raise e
    except MailError:
        raise
    except smtplib.SMTPAuthenticationError as e:
        raise MailError(f"Anmeldung am SMTP-Server abgelehnt ({e.smtp_code}): Benutzer/Kennwort prüfen.")
    except ssl.SSLCertVerificationError as e:
        raise MailError(f"Zertifikat des SMTP-Servers nicht vertrauenswürdig ({e.verify_message}). "
                        f"Hostnamen prüfen oder „Zertifikat prüfen“ abschalten.")
    except ssl.SSLError as e:
        raise MailError(f"TLS-Fehler: {e.reason or e} – passt die Verschlüsselung zum Port?")
    except (socket.timeout, TimeoutError):
        raise MailError(f"Zeitüberschreitung bei {s['host']}:{s['port']}.")
    except (socket.gaierror,):
        raise MailError(f"SMTP-Server „{s['host']}“ nicht gefunden (DNS).")
    except ConnectionRefusedError:
        raise MailError(f"Verbindung zu {s['host']}:{s['port']} abgelehnt.")
    except (smtplib.SMTPException, OSError) as e:
        raise MailError(f"SMTP-Fehler: {e}")


def _transfer(c, sender, to, msg, log):
    """MAIL FROM / RCPT TO / DATA einzeln, damit die Antwort des Servers (Queue-ID) protokolliert wird."""
    code, resp = c.mail(sender)
    log.append(f"MAIL FROM:<{sender}> → {code} {_r(resp)}")
    if code != 250:
        raise MailError(f"Absender {sender} abgelehnt ({code}): {_r(resp)[:200]}")
    ok, refused = [], {}
    for a in to:
        code, resp = c.rcpt(a)
        log.append(f"RCPT TO:<{a}> → {code} {_r(resp)}")
        if code in (250, 251):
            ok.append(a)
        else:
            refused[a] = f"{code} {_r(resp)}"
    if not ok:
        c.rset()
        raise MailError("Alle Empfänger abgelehnt: " + "; ".join(f"{a} ({r[:120]})" for a, r in refused.items()))
    data = msg.as_bytes(policy=msg.policy.clone(linesep="\r\n"))
    code, resp = c.data(data)
    log.append(f"DATA ({len(data) // 1024 + 1} KB) → {code} {_r(resp)}")
    if code != 250:
        raise MailError(f"Server hat die Mail abgelehnt ({code}): {_r(resp)[:200]}")
    return ok, refused, f"{code} {_r(resp)}"


def send(to, subject, html_body, text_body="", attachments=None, category="mail", force=False, user=None):
    """Mail senden. to: Liste von Adressen. attachments: [(dateiname, bytes, mimetype)]."""
    from . import vault
    s = settings()
    if not s["host"] or not s["from"]:
        raise MailError("E-Mail ist nicht eingerichtet (Einstellungen → E-Mail).")
    if not s["enabled"] and not force:
        raise MailError("E-Mail-Versand ist deaktiviert (Einstellungen → E-Mail).")
    to = [a for a in to if a]
    if not to:
        raise MailError("Keine Empfänger – unter Einstellungen → E-Mail Standard-Empfänger eintragen.")
    if not _rate_ok():
        raise MailError(f"Mehr als {MAX_PER_HOUR} Mails in einer Stunde – Versand vorübergehend gestoppt.")
    msg = EmailMessage()
    prefix = s["subject_prefix"]
    msg["Subject"] = (f"{prefix} {subject}" if prefix else subject).replace("\r", " ").replace("\n", " ")
    msg["From"] = formataddr((s["from_name"], s["from"]))
    msg["To"] = ", ".join(to)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=(s["from"].split("@")[-1] or None))
    msg["Auto-Submitted"] = "auto-generated"
    msg["X-Mailer"] = f"POCKET KNIFE {config.VERSION}"
    # quoted-printable statt 8bit: auch Server ohne 8BITMIME übertragen Umlaute unverändert
    msg.set_content(text_body or re.sub(r"<[^>]+>", "", html_body), cte="quoted-printable")
    msg.add_alternative(html_body, subtype="html", cte="quoted-printable")
    total = 0
    for name, data, mime in attachments or []:
        total += len(data)
        if total > MAX_ATTACH:
            raise MailError(f"Anhänge zu groß (über {MAX_ATTACH // 1048576} MB).")
        maintype, subtype = (mime or "application/octet-stream").split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    pw = vault.decrypt(db.setting_raw("mail_password")) if s["user"] else ""
    log = []
    c = _connect(s, pw, log)
    try:
        ok, refused, reply = _transfer(c, s["from"], to, msg, log)
    except smtplib.SMTPDataError as e:
        raise MailError(f"Server hat die Mail abgelehnt ({e.smtp_code}): {_r(e.smtp_error)[:200]}")
    except MailError:
        raise
    except (smtplib.SMTPException, OSError) as e:
        raise MailError(f"SMTP-Fehler beim Senden: {e}")
    finally:
        try:
            c.quit()
        except Exception:
            pass
    _rate_add()
    note = f" ({len(refused)} Empfänger abgelehnt: {', '.join(refused)})" if refused else ""
    db.set_setting("mail_last", f"{util.now()} · {subject[:120]} → {len(ok)} Empfänger{note} · Server: {reply[:150]}")
    pklog.info(category, f"E-Mail „{subject}“ an {', '.join(ok)} übergeben{note} – Server: {reply[:200]}", user=user)
    return {"to": ok, "refused": list(refused), "reply": reply, "transcript": log, "message_id": msg["Message-ID"],
            "helo": helo_name(), "from": s["from"]}


def recipients_or_default(text):
    lst = parse_recipients(text, required=False) if text else []
    return lst or parse_recipients(settings()["to"], required=False)


def test(to_text, user=None):
    to = parse_recipients(to_text, "Testempfänger") if (to_text or "").strip() else recipients_or_default("")
    if not to:
        raise MailError("Bitte einen Testempfänger angeben.")
    s = settings()
    body = wrap("Testnachricht", f'<p>Diese Nachricht bestätigt, dass POCKET KNIFE Mails über <b>{E(s["host"])}:{s["port"]}</b> '
                                 f'({E(SECURITY[s["security"]])}) versenden kann.</p>'
                                 f'<p style="color:#667">Ausgelöst von {E(user or "?")}.</p>')
    r = send(to, "Testnachricht", body, "POCKET KNIFE kann Mails versenden.", force=True, user=user)
    r["warnings"] = warnings(s)
    return r


def warnings(s=None):
    """Einstellungen, die oft zu Spam-Einstufung oder stillem Verwerfen führen."""
    s = s or settings()
    out = []
    usr, frm = (s["user"] or "").lower(), (s["from"] or "").lower()
    if "@" in usr and frm and usr != frm:
        out.append(f"Absender {s['from']} weicht vom Anmeldekonto {s['user']} ab. Microsoft 365/Exchange Online und die meisten "
                   f"Provider verlangen dieselbe Adresse oder ein „Senden als“-Recht – sonst wird die Mail abgelehnt oder als Spam eingestuft.")
    if not usr:
        out.append("Ohne Anmeldung (Relay): nur über den eigenen Mailserver der Firma verwenden, der den Docker-Host ausdrücklich "
                   "als Relay zulässt – nie direkt an fremde Mailserver (z. B. das MX von Empfängern). Fremde Server erkennen den "
                   "Docker-Host nicht als berechtigten Absender (SPF) und stufen die Mail als Spam ein.")
    if s["security"] == "none":
        out.append("Unverschlüsselt: nur für einen internen Relay im eigenen Netz. Für Mailserver außerhalb immer STARTTLS oder SSL/TLS.")
    elif not s["verify"]:
        out.append("Zertifikatsprüfung ist aus – nur für interne Server mit selbst signiertem Zertifikat.")
    return out
