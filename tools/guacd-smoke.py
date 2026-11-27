#!/usr/bin/env python3
"""POCKET KNIFE – connection test for guacd (2.62.2, .github/workflows/guacd.yml before a new guacd is taken over).

Opens a connection like POCKET KNIFE does and asks for RDP and VNC ("select"); guacd must answer with the parameter
list ("args"). That shows the image starts, listens and still has both protocols - without a real client.

Usage: python3 tools/guacd-smoke.py <host> <port> [seconds to wait for the start]
"""
import socket
import sys
import time


def ask(host, port, proto, timeout=10):
    msg = f"6.select,{len(proto)}.{proto};".encode()
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall(msg)
        buf = b""
        while b";" not in buf:
            x = s.recv(4096)
            if not x:
                break
            buf += x
    return buf.split(b";", 1)[0].decode("utf-8", "replace")


def main(host, port, wait):
    deadline = time.time() + wait
    while True:
        try:
            first = ask(host, port, "rdp")
            break
        except OSError as e:
            if time.time() > deadline:
                print(f"guacd {host}:{port} not reachable: {e}")
                return 1
            time.sleep(1)
    ok = True
    for proto, ans in (("rdp", first), ("vnc", ask(host, port, "vnc"))):
        good = ans.startswith("4.args,") and "hostname" in ans
        ok &= good
        print(f"{proto}: {'ok' if good else 'NOT OK'} – {ans[:120]}")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]) if len(sys.argv) == 4 else 30))
