"""Remote sessions (RDP and VNC) in the browser via Apache Guacamole.

Flow:
  1. Browser: POST /api/remote/connect  →  parameters are checked here and combined with credentials
     (from the RAM vault or the input) into a one-time token (60 s).
  2. Browser: WebSocket /api/remote/tunnel?token=…  →  POCKET KNIFE connects to guacd,
     runs the Guacamole handshake with the parameters and afterwards only forwards.
Credentials never leave the server towards the browser and are never stored in the database.
"""
import asyncio
import codecs
import re
import secrets
import socket
import threading
import time
import uuid

from cryptography.fernet import Fernet

from . import config, db, pklog, util

PROTOCOLS = {"rdp": "Remotedesktop (RDP)", "vnc": "VNC"}
DEFAULT_PORTS = {"rdp": 3389, "vnc": 5900}
LAYOUTS = {
    "de-de-qwertz": "Deutsch (Deutschland)", "de-ch-qwertz": "Deutsch (Schweiz)", "en-us-qwerty": "Englisch (USA)",
    "en-gb-qwerty": "Englisch (UK)", "fr-fr-azerty": "Französisch", "fr-ch-qwertz": "Französisch (Schweiz)",
    "it-it-qwerty": "Italienisch", "es-es-qwerty": "Spanisch", "failsafe": "Unicode (Failsafe)",
}
COLOR_DEPTHS = {"": "Automatisch", "8": "256 Farben", "16": "16 Bit", "24": "24 Bit", "32": "32 Bit"}
TOKEN_TTL = 60
GUAC_VERSION = "VERSION_1_5_0"

# Guacamole status codes (for errors that occur before guacd)
UPSTREAM_NOT_FOUND = 519   # 0x0207
SERVER_ERROR = 512         # 0x0200
CLIENT_UNAUTHORIZED = 769  # 0x0301


# --- Vault for the operators' credentials (in memory only) ---------------
_fernet = Fernet(Fernet.generate_key())  # the key exists only in this process
_vault = {}  # sha256(session cookie) -> (user, domain, encrypted password, expiry)
_vlock = threading.Lock()


def split_login(name):
    """Login name → (user, domain) for RDP."""
    name = (name or "").strip()
    if "\\" in name:
        dom, user = name.split("\\", 1)
        return user, dom
    if "@" in name:
        user, suffix = name.split("@", 1)
        if config.DOMAIN and suffix.lower() == config.DOMAIN.lower():
            return user, config.NETBIOS or config.DOMAIN
        return name, ""  # foreign UPN suffix: pass as UPN without a domain
    return name, config.NETBIOS or config.DOMAIN


def remember(token_hash, login, password):
    if not db.setting("remote_sso") or not password:
        return
    user, dom = split_login(login)
    with _vlock:
        _vault[token_hash] = (user, dom, _fernet.encrypt(password.encode("utf-8")),
                              time.time() + config.SESSION_HOURS * 3600)


def forget(token_hash):
    with _vlock:
        _vault.pop(token_hash, None)


def forget_all():
    with _vlock:
        _vault.clear()


def own_credentials(token_hash):
    with _vlock:
        now = time.time()
        for k in [k for k, v in _vault.items() if v[3] < now]:
            _vault.pop(k, None)
        v = _vault.get(token_hash)
    if not v:
        return None
    return {"username": v[0], "domain": v[1], "password": _fernet.decrypt(v[2]).decode("utf-8")}


def own_login(token_hash):
    with _vlock:
        v = _vault.get(token_hash)
    if not v or v[3] < time.time():
        return None
    return f"{v[1]}\\{v[0]}" if v[1] else v[0]


# --- One-time token --------------------------------------------------------------
_tokens = {}
_tlock = threading.Lock()


def _clean(v, n=256):
    v = "" if v is None else str(v)
    if any(ord(ch) < 32 for ch in v):
        raise ValueError("Ungültige Steuerzeichen in der Eingabe.")
    return v[:n]


def vnc_password():
    """Central VNC password (encrypted in the database, empty = not set)."""
    from . import vault
    raw = db.setting_raw("vnc_password")
    if not raw:
        return ""
    try:
        return vault.decrypt(raw) or ""
    except Exception:
        return ""


def set_vnc_password(pw):
    from . import vault
    db.set_setting("vnc_password", vault.encrypt(pw) if pw else "")


def tenant_names(cid):
    """2.37: Tenants whose scope the computer belongs to (notice before a main admin's "own login")."""
    from . import access, collections_
    out = []
    for t in db.q("SELECT * FROM tenants ORDER BY name COLLATE NOCASE"):
        if not (t.get("admin_group") or "").strip():
            continue  # without OU admins nobody else manages the computer
        for col in access.tenant_cols(t):
            try:
                if int(cid) in collections_.member_ids(col):
                    out.append(t["name"])
                    break
            except Exception:
                continue
    return out


def prepare(computer, user, token_hash, data):
    """Checks the request and creates a one-time token with all connection parameters."""
    proto = data.get("protocol") or "rdp"
    if proto not in PROTOCOLS:
        raise ValueError("Unbekanntes Protokoll.")
    from . import presence
    host = presence.direct_host(computer)  # 2.36.3: never send credentials to a device under a foreign address
    if not re.match(r"^[A-Za-z0-9._-]{1,253}$", host):
        raise ValueError("Ungültiger Hostname.")
    try:
        port = int(data.get("port") or DEFAULT_PORTS[proto])
    except (TypeError, ValueError):
        raise ValueError("Ungültiger Port.")
    if not 1 <= port <= 65535:
        raise ValueError("Ungültiger Port.")
    mode = data.get("auth") or "own"
    p = {"hostname": host, "port": str(port)}
    login_label = ""
    if proto == "rdp":
        if mode == "own":
            cred = own_credentials(token_hash)
            if not cred:
                raise ValueError("Ihre Anmeldedaten liegen nicht (mehr) vor – z. B. nach einem Neustart von POCKET KNIFE, "
                                 "beim Notfallzugang oder wenn „Eigene Anmeldung verwenden“ ausgeschaltet ist. "
                                 "Bitte neu anmelden oder die Anmeldedaten eingeben.")
            p.update(cred)
        elif mode == "manual":
            u, d = split_login(_clean(data.get("username")))
            if data.get("domain") is not None and "\\" not in (data.get("username") or "") and "@" not in (data.get("username") or ""):
                d = _clean(data.get("domain"))
            p.update({"username": u, "domain": d, "password": _clean(data.get("password"), 1024)})
            if not u:
                raise ValueError("Benutzername fehlt.")
        elif mode != "prompt":
            raise ValueError("Unbekannte Anmeldeart.")
        login_label = f"{p.get('domain')}\\{p.get('username')}" if p.get("domain") else p.get("username", "")
        layout = data.get("keyboard") or db.setting("remote_keyboard")
        depth = str(data.get("color_depth") or "")
        # 2.37: if a password is supplied, only with NLA - then Windows checks the login before a desktop
        # is created, and the password does not go to a login screen that another device could imitate
        p.update({
            "security": "nla" if mode in ("own", "manual") else "any", "ignore-cert": "true", "server-layout": layout if layout in LAYOUTS else "de-de-qwertz",
            "resize-method": "display-update", "enable-font-smoothing": "true",
            "enable-wallpaper": "true" if data.get("wallpaper") else "false",
            "enable-theming": "true", "enable-full-window-drag": "false", "enable-menu-animations": "false",
            "enable-desktop-composition": "true" if data.get("wallpaper") else "false",
            "console": "true" if data.get("console") else "false",
            "disable-audio": "false" if data.get("audio", True) else "true",
            "disable-copy": "false" if data.get("clipboard", True) else "true",
            "disable-paste": "false" if data.get("clipboard", True) else "true",
            "client-name": "POCKETKNIFE",
        })
        if depth in COLOR_DEPTHS and depth:
            p["color-depth"] = depth
        if data.get("files"):
            p.update({"enable-drive": "true", "drive-name": "POCKET KNIFE", "create-drive-path": "true",
                      "drive-path": f"/tmp/pocketknife-drive/{secrets.token_hex(8)}"})
    else:
        if mode == "prompt":
            pass
        elif mode == "central":
            pw = vnc_password()
            if not pw:
                raise ValueError("Es ist kein zentrales VNC-Kennwort hinterlegt (Einstellungen → Fernsitzungen).")
            p["password"] = pw
            login_label = "zentrales VNC-Kennwort"
        else:
            pw = _clean(data.get("password"), 256)
            if pw:
                p["password"] = pw
            if data.get("username"):
                p["username"] = _clean(data.get("username"))
        depth = str(data.get("color_depth") or "")
        if depth in ("8", "16", "24", "32"):
            p["color-depth"] = depth
        p.update({"read-only": "true" if data.get("read_only") else "false",
                  "disable-copy": "false" if data.get("clipboard", True) else "true",
                  "disable-paste": "false" if data.get("clipboard", True) else "true"})
        login_label = p.get("username", "") or login_label
    token = secrets.token_urlsafe(24)
    with _tlock:
        now = time.time()
        for k in [k for k, v in _tokens.items() if v["expires"] < now]:
            _tokens.pop(k, None)
        _tokens[token] = {"protocol": proto, "params": p, "user": user["username"], "session": token_hash,
                          "computer_id": computer["id"], "computer": computer["name"], "login": login_label,
                          "expires": now + TOKEN_TTL}
    return token


def take(token, token_hash):
    with _tlock:
        t = _tokens.pop(token or "", None)
    if not t or t["expires"] < time.time() or t["session"] != token_hash:
        return None
    return t


# --- Guacamole protocol -------------------------------------------------------
def encode(*elements):
    return ",".join(f"{len(str(e))}.{e}" for e in elements) + ";"


class Parser:
    """Splits a Guacamole data stream into complete instructions (lengths in Unicode characters)."""

    def __init__(self):
        self.buf = ""

    def feed(self, text):
        self.buf += text
        out, start, pos, buf = [], 0, 0, self.buf
        while True:
            dot = buf.find(".", pos)
            if dot < 0:
                break
            ln = buf[pos:dot]
            if not ln.isdigit() or len(ln) > 9:
                raise ValueError("Ungültige Guacamole-Daten")
            end = dot + 1 + int(ln)
            if end >= len(buf):
                break
            term = buf[end]
            if term == ";":
                out.append(buf[start:end + 1])
                start = pos = end + 1
            elif term == ",":
                pos = end + 1
            else:
                raise ValueError("Ungültige Guacamole-Daten")
        self.buf = buf[start:]
        if len(self.buf) > 8 * 1024 * 1024:
            raise ValueError("Guacamole-Anweisung zu groß")
        return out


def elements(instr):
    """'4.args,3.foo;' → ['args', 'foo']"""
    out, pos = [], 0
    while pos < len(instr):
        dot = instr.index(".", pos)
        n = int(instr[pos:dot])
        out.append(instr[dot + 1:dot + 1 + n])
        pos = dot + 1 + n + 1
    return out


# --- Active sessions ----------------------------------------------------------
_sessions = {}
_slock = threading.Lock()


class Session:
    def __init__(self, t, loop):
        self.id = str(uuid.uuid4())
        self.t = t
        self.loop = loop
        self.started = time.time()
        self.writer = None
        self.ws = None
        self.closed_by = None
        self.bytes_in = 0
        self.bytes_out = 0
        self.last_input = time.time()   # 2.44.1: last keyboard/mouse input of the operator

    def info(self):
        return {"id": self.id, "user": self.t["user"], "computer": self.t["computer"], "computer_id": self.t["computer_id"],
                "protocol": self.t["protocol"], "login": self.t["login"], "started": util.fmt(
                    __import__("datetime").datetime.fromtimestamp(self.started)),
                "seconds": int(time.time() - self.started), "bytes": self.bytes_in + self.bytes_out,
                "idle_seconds": int(time.time() - self.last_input)}

    async def _kill(self, by):
        self.closed_by = by
        try:
            if self.ws:
                await self.ws.send_text(encode("error", f"Sitzung wurde von {by} beendet.", "521"))
        except Exception:
            pass
        if self.writer:
            self.writer.close()


INPUT_OPCODES = ("mouse", "key", "touch", "clipboard")   # real operator input (not sync/ping/size)


def is_input(ins):
    """'5.mouse,...;' - an instruction caused by the operator?"""
    dot = ins.find(".")
    try:
        n = int(ins[:dot])
    except ValueError:
        return False
    return ins[dot + 1:dot + 1 + n] in INPUT_OPCODES


def idle_limit():
    """Seconds without input after which a remote session ends (0 = never)."""
    try:
        return max(0, int(db.setting("remote_idle_minutes") or 0)) * 60
    except (TypeError, ValueError):
        return 0


def active():
    with _slock:
        return [s.info() for s in _sessions.values()]


def kill(sid, by):
    with _slock:
        s = _sessions.get(sid)
    if not s:
        return False
    asyncio.run_coroutine_threadsafe(s._kill(by), s.loop)
    return True


def kill_for_session(token_hash, by="Abmeldung"):
    """End all remote sessions of a login (logout, session expired)."""
    with _slock:
        mine = [s for s in _sessions.values() if s.t.get("session") == token_hash]
    for s in mine:
        asyncio.run_coroutine_threadsafe(s._kill(by), s.loop)
    return len(mine)


def guacd_status():
    try:
        with socket.create_connection((config.GUACD_HOST, config.GUACD_PORT), timeout=2):
            return {"ok": True, "host": config.GUACD_HOST, "port": config.GUACD_PORT}
    except OSError as e:
        return {"ok": False, "host": config.GUACD_HOST, "port": config.GUACD_PORT, "error": str(e)}


def _int(v, lo, hi, default):
    try:
        return max(lo, min(hi, int(float(v))))
    except (TypeError, ValueError):
        return default


async def _read_instruction(reader, parser, dec, pending, timeout=15):
    while not pending:
        data = await asyncio.wait_for(reader.read(65536), timeout)
        if not data:
            raise ConnectionError("guacd hat die Verbindung geschlossen")
        pending.extend(parser.feed(dec.decode(data)))
    return pending.pop(0)


async def tunnel(ws, token_hash):
    """WebSocket endpoint: handshake with guacd and forwarding in both directions."""
    q = ws.query_params
    t = take(q.get("token"), token_hash)
    if not t:
        await ws.close(code=1008)
        return
    await ws.accept(subprotocol="guacamole")
    sess = Session(t, asyncio.get_running_loop())
    sess.ws = ws
    await ws.send_text(encode("", sess.id))  # tunnel UUID (first instruction, expected by guacamole-common-js)

    async def fail(msg, code):
        try:
            await ws.send_text(encode("error", msg, str(code)))
            await ws.close()
        except Exception:
            pass

    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(config.GUACD_HOST, config.GUACD_PORT), 8)
    except (OSError, asyncio.TimeoutError) as e:
        pklog.warn("remote", f"guacd ({config.GUACD_HOST}:{config.GUACD_PORT}) nicht erreichbar: {e}",
                   user=t["user"], computer_id=t["computer_id"])
        await fail("Der Guacamole-Dienst (guacd) ist nicht erreichbar. Läuft der Container „pocketknife-guacd“?",
                   UPSTREAM_NOT_FOUND)
        return
    sess.writer = writer
    parser, dec, pending = Parser(), codecs.getincrementaldecoder("utf-8")(errors="replace"), []
    width, height = _int(q.get("width"), 320, 8192, 1280), _int(q.get("height"), 240, 8192, 800)
    dpi = _int(q.get("dpi"), 48, 384, 96)
    tz = q.get("tz") or ""
    if not re.match(r"^[A-Za-z0-9_+\-/]{1,64}$", tz):
        tz = ""
    try:
        writer.write(encode("select", t["protocol"]).encode())
        await writer.drain()
        args = elements(await _read_instruction(reader, parser, dec, pending))
        if not args or args[0] != "args":
            raise ConnectionError(f"Unerwartete Antwort von guacd: {args[:1]}")
        names = args[1:]
        hs = [encode("size", width, height, dpi), encode("audio", "audio/L8", "audio/L16"), encode("video"),
              encode("image", "image/png", "image/jpeg", "image/webp")]
        if tz:
            hs.append(encode("timezone", tz))
        if names and names[0].startswith("VERSION_"):
            hs.append(encode("name", t["user"]))
        values = []
        for n in names:
            if n.startswith("VERSION_"):
                values.append(n if n <= GUAC_VERSION else GUAC_VERSION)
            else:
                values.append(t["params"].get(n, ""))
        hs.append(encode("connect", *values))
        writer.write("".join(hs).encode())
        await writer.drain()
    except Exception as e:
        writer.close()
        pklog.warn("remote", f"Handshake mit guacd fehlgeschlagen: {e}", user=t["user"], computer_id=t["computer_id"])
        await fail(f"Handshake mit guacd fehlgeschlagen: {e}", SERVER_ERROR)
        return

    with _slock:
        _sessions[sess.id] = sess
    pklog.info("remote", f"{PROTOCOLS[t['protocol']]}-Sitzung zu {t['computer']} gestartet"
               + (f" als {t['login']}" if t["login"] else ""), user=t["user"], computer_id=t["computer_id"])

    async def guacd_to_browser():
        if pending:
            await ws.send_text("".join(pending))
        while True:
            data = await reader.read(65536)
            if not data:
                break
            sess.bytes_in += len(data)
            out = parser.feed(dec.decode(data))
            if out:
                await ws.send_text("".join(out))

    async def browser_to_guacd():
        bp = Parser()
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            text = msg.get("text")
            if text is None:
                continue
            fwd = []
            for ins in bp.feed(text):
                if ins.startswith("0.,"):  # internal tunnel instruction (ping)
                    el = elements(ins)
                    if len(el) >= 2 and el[1] == "ping":
                        await ws.send_text(ins)
                    continue
                if is_input(ins):
                    sess.last_input = time.time()
                fwd.append(ins)
            if fwd:
                raw = "".join(fwd).encode()
                sess.bytes_out += len(raw)
                writer.write(raw)
                await writer.drain()

    async def watchdog():
        # Logout or an expired login also ends the remote session
        from . import auth
        while True:
            await asyncio.sleep(60)
            if not await asyncio.get_running_loop().run_in_executor(None, auth.session_valid, token_hash):
                await sess._kill("Sitzungsende (abgemeldet oder abgelaufen)")
                return
            # 2.44.1: a forgotten session (no keyboard/mouse input) ends - it keeps relaying the screen and
            # slows POCKET KNIFE down for everyone
            lim = await asyncio.get_running_loop().run_in_executor(None, idle_limit)
            if lim and time.time() - sess.last_input > lim:
                pklog.info("remote", f"{PROTOCOLS[t['protocol']]}-Sitzung zu {t['computer']} nach {lim // 60} Minuten ohne "
                           "Eingabe beendet.", user=t["user"], computer_id=t["computer_id"])
                await sess._kill(f"POCKET KNIFE nach {lim // 60} Minuten ohne Eingabe")
                return

    tasks = [asyncio.ensure_future(guacd_to_browser()), asyncio.ensure_future(browser_to_guacd()),
             asyncio.ensure_future(watchdog())]
    try:
        done, rest = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for x in rest:
            x.cancel()
        for x in done:
            if x.exception() and not isinstance(x.exception(), (ConnectionError, asyncio.CancelledError)):
                pklog.warn("remote", f"Fernsitzung {t['computer']}: {x.exception()}", user=t["user"],
                           computer_id=t["computer_id"])
    finally:
        with _slock:
            _sessions.pop(sess.id, None)
        try:
            writer.close()
        except Exception:
            pass
        try:
            await ws.close()
        except Exception:
            pass
        mins = (time.time() - sess.started) / 60
        pklog.info("remote", f"{PROTOCOLS[t['protocol']]}-Sitzung zu {t['computer']} beendet nach {mins:.0f} Min."
                   + (f" (getrennt von {sess.closed_by})" if sess.closed_by else ""), user=t["user"],
                   computer_id=t["computer_id"])
