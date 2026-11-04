"""Name resolution of the clients with a configurable cache ("DNS-Cache leeren").

- Cache: a resolved address is valid for at most "dns_cache_minutes" minutes (0 = ask anew every time).
  "Jetzt leeren" in the settings discards it immediately.
- Optionally POCKET KNIFE queries the domain's DNS servers directly (setting "dns_direct") - bypassing caches
  on the Docker host. Servers: setting "dns_servers", otherwise the domain controllers (PK_LDAP_SERVERS).
  If none answers, the name is resolved through the system as before.

Stale entries in the domain's own DNS (missing cleanup/"scavenging") are not fixed by this - for that,
presence.identify() checks whether the expected computer really answers at the address.
"""
import ipaddress
import secrets
import socket
import struct
import threading
import time

from . import config, db, util

_cache = {}  # name(lower) -> (timestamp, ip or None)
_lock = threading.Lock()
_servers_cache = {"t": 0, "ips": []}


def flush():
    """Clear the cache. Returns the number of discarded entries."""
    with _lock:
        n = len(_cache)
        _cache.clear()
        _servers_cache.update(t=0, ips=[])
    return n


def _ttl():
    try:
        return max(0, int(db.setting("dns_cache_minutes") or 0)) * 60
    except (TypeError, ValueError):
        return 0


def _is_ip(host):
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _fqdn(host):
    h = (host or "").strip().rstrip(".")
    if h and "." not in h and config.DOMAIN:
        h = f"{h}.{config.DOMAIN}"
    return h


def servers():
    """IP addresses of the DNS servers for the direct query (remembered for 5 minutes)."""
    now = time.time()
    with _lock:
        if now - _servers_cache["t"] < 300 and _servers_cache["ips"]:
            return list(_servers_cache["ips"])
    names = [x.strip() for x in str(db.setting("dns_servers") or "").replace(";", ",").split(",") if x.strip()]
    if not names:
        names = list(config.LDAP_SERVERS or [])
    ips = []
    for n in names:
        if _is_ip(n):
            ips.append(n)
            continue
        try:
            for a in socket.getaddrinfo(n, 53, socket.AF_INET, socket.SOCK_DGRAM):
                if a[4][0] not in ips:
                    ips.append(a[4][0])
        except OSError:
            pass
    with _lock:
        _servers_cache.update(t=now, ips=ips)
    return ips


# --- Minimal DNS client (A records only) --------------------------------------------------------
def _query(name, server, timeout=2.0):
    """A query to one server. ip (str), "" (name does not exist/no A record) or None (no answer)."""
    qid = secrets.randbits(16)  # Audit 2.29: unpredictable (random.randint is not)
    q = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)  # standard query, recursion desired
    for label in name.split("."):
        b = label.encode("idna") if label else b""
        if not b or len(b) > 63:
            return ""
        q += bytes([len(b)]) + b
    q += b"\x00" + struct.pack(">HH", 1, 1)  # type A, class IN
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    end = time.time() + timeout
    try:
        # connect(): the kernel only accepts replies from exactly this server (sender of forged replies)
        s.connect((server, 53))
        s.send(q)
        while True:
            if time.time() > end:
                return None
            data = s.recv(4096)
            # same ID AND same question (name, type, class) - otherwise it is not the answer to this query
            if len(data) >= len(q) and struct.unpack(">H", data[:2])[0] == qid and data[12:len(q)].lower() == q[12:].lower():
                break
    except OSError:
        return None
    finally:
        s.close()
    return _parse(data, qid)


def _skip_name(data, pos):
    while True:
        if pos >= len(data):
            raise ValueError("kurz")
        n = data[pos]
        if n == 0:
            return pos + 1
        if n & 0xC0 == 0xC0:
            return pos + 2
        pos += 1 + n


def _parse(data, qid):
    hdr = struct.unpack(">HHHHHH", data[:12])
    if hdr[0] != qid:
        return None
    rcode = hdr[1] & 0x000F
    if rcode == 3:  # NXDOMAIN
        return ""
    if rcode != 0:
        return None
    pos = 12
    try:
        for _ in range(hdr[2]):
            pos = _skip_name(data, pos) + 4
        for _ in range(hdr[3]):
            pos = _skip_name(data, pos)
            typ, cls, _ttl_, rdlen = struct.unpack(">HHIH", data[pos:pos + 10])
            pos += 10
            if typ == 1 and cls == 1 and rdlen == 4:
                return socket.inet_ntoa(data[pos:pos + 4])
            pos += rdlen
    except (ValueError, struct.error):
        return None
    return ""


def _resolve_direct(name):
    for srv in servers():
        r = _query(name, srv)
        if r is not None:
            return r or None, True
    return None, False


def _resolve_system(name):
    try:
        for a in socket.getaddrinfo(name, None, socket.AF_INET, socket.SOCK_STREAM):
            return a[4][0]
    except OSError:
        return None
    return None


def resolve(host, fresh=False):
    """IP address of the host name (IPv4) or None. An IP address is returned unchanged."""
    h = (host or "").strip()
    if not h or _is_ip(h):
        return h or None
    key = h.lower()
    ttl = _ttl()
    now = time.time()
    if ttl and not fresh:
        with _lock:
            hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    ip = None
    if db.setting("dns_direct"):
        ip, answered = _resolve_direct(_fqdn(h))
        if not answered:
            ip = _resolve_system(h)
    else:
        ip = _resolve_system(h)
    if ttl:
        with _lock:
            if len(_cache) > 20000:
                _cache.clear()
            _cache[key] = (now, ip)
    return ip


def status():
    with _lock:
        n = len(_cache)
    return {"entries": n, "ttl_minutes": _ttl() // 60, "direct": bool(db.setting("dns_direct")),
            "servers": servers() if db.setting("dns_direct") else [], "checked": util.now()}
