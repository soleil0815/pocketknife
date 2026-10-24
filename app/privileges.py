"""Container ohne root: Beim Start (als root) die Datenordner dem Dienstbenutzer übergeben und die Rechte abgeben.

Ab 2.14 läuft POCKET KNIFE als Benutzer „pocketknife“ (UID/GID 10001, änderbar mit PK_UID/PK_GID). Ältere
Installationen haben Dateien von root in /data – sie werden beim ersten Start einmalig umgestellt. Danach
behält der Prozess keinerlei Sonderrechte (alle Capabilities fallen mit dem Wechsel der UID weg).
"""
import os
import sys

DEFAULT_UID = 10001
DEFAULT_GID = 10001


def _ids():
    def num(name, default):
        try:
            v = int(os.environ.get(name, default))
        except ValueError:
            v = default
        return v if v > 0 else default  # nie wieder root
    return num("PK_UID", DEFAULT_UID), num("PK_GID", DEFAULT_GID)


def chown_tree(path, uid, gid):
    """Alles unterhalb von path (ohne Verknüpfungen zu folgen) uid:gid geben. Gibt (geändert, fehler) zurück."""
    changed, errors = 0, []
    if not os.path.isdir(path):
        return changed, errors

    def fix(p):
        nonlocal changed
        try:
            st = os.lstat(p)
            if st.st_uid != uid or st.st_gid != gid:
                os.lchown(p, uid, gid)
                changed += 1
        except OSError as e:
            if len(errors) < 5:
                errors.append(f"{p}: {e.strerror or e}")

    fix(path)
    for root, dirs, files in os.walk(path, onerror=lambda e: errors.append(str(e)) if len(errors) < 5 else None):
        for n in dirs + files:
            fix(os.path.join(root, n))
    return changed, errors


def drop(dirs):
    """Als root gestartet: dirs übergeben, dann dauerhaft zum Dienstbenutzer wechseln. Sonst nichts tun."""
    if os.name != "posix" or os.geteuid() != 0:
        return False
    uid, gid = _ids()
    for d in dirs:
        if not d:
            continue
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
        n, errs = chown_tree(d, uid, gid)
        if n:
            print(f"POCKET KNIFE: {n} Einträge in {d} an Benutzer {uid}:{gid} übergeben.", flush=True)
        for e in errs:
            print(f"POCKET KNIFE: Warnung – Besitzer nicht änderbar: {e}", flush=True)
    os.setgroups([])
    os.setgid(gid)
    os.setuid(uid)
    try:  # Probe: zurück zu root darf nicht mehr gehen
        os.setuid(0)
    except PermissionError:
        pass
    else:
        sys.exit("POCKET KNIFE: Rechte konnten nicht abgegeben werden – Start abgebrochen.")
    os.umask(0o077)
    os.environ["HOME"] = "/tmp"
    return True
