"""Container without root: at start (as root) hand the data folders to the service user and drop privileges.

From 2.14 on, POCKET KNIFE runs as user "pocketknife" (UID/GID 10001, changeable with PK_UID/PK_GID). Older
installations have files owned by root in /data - they are converted once at the first start. After that
the process keeps no special privileges at all (all capabilities are dropped with the UID change).
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
        return v if v > 0 else default  # never root again
    return num("PK_UID", DEFAULT_UID), num("PK_GID", DEFAULT_GID)


# Folders whose owner is never changed (relative to the data folder). 2.32.12: data/update/host of the former
# update helper (2.32.9-2.32.12) belongs to root - update.sh --cleanup removes it.
KEEP_OWNER = (os.path.join("update", "host"),)


def chown_tree(path, uid, gid, keep=KEEP_OWNER):
    """Give everything below path (without following links) to uid:gid - except the folders in keep (relative to
    path, including their contents). Returns (changed, errors)."""
    changed, errors = 0, []
    if not os.path.isdir(path):
        return changed, errors
    skip = {os.path.normpath(os.path.join(path, k)) for k in keep or ()}

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
        dirs[:] = [n for n in dirs if os.path.normpath(os.path.join(root, n)) not in skip]
        for n in dirs + files:
            fix(os.path.join(root, n))
    return changed, errors


def _probe(d):
    """May the running user create and delete in d? Raises OSError if not."""
    import tempfile
    fd, p = tempfile.mkstemp(prefix=".pk-probe-", dir=d)
    os.close(fd)
    os.remove(p)


def unwritable(dirs, probe=None):
    """2.30: Folders the service may not write to - [(folder, error text)]. Missing folders are created."""
    probe = probe or _probe
    out = []
    for d in dirs:
        if not d:
            continue
        try:
            os.makedirs(d, exist_ok=True)
            probe(d)
        except OSError as e:
            out.append((d, e.strerror or str(e)))
    return out


def write_hint(problems, data_dir):
    """Readable message instead of a traceback when the service may not write (typical: NAS with its own share
    permissions such as Synology, or a folder someone created by hand as another user)."""
    uid, gid = os.getuid(), os.getgid()
    lines = [f"POCKET KNIFE: Start abgebrochen – Benutzer {uid}:{gid} darf hier nicht schreiben:"]
    lines += [f"  - {d}: {err}" for d, err in problems]
    lines += [
        "  So beheben Sie es:",
        "  - NAS mit eigenen Freigabe-Rechten (z. B. Synology): einen Benutzer für POCKET KNIFE anlegen, ihm Lesen/Schreiben",
        "    auf den Daten- und Sicherungsordner geben (auf Unterordner und Dateien anwenden), seine IDs (id <benutzer>)",
        "    als PK_UID und PK_GID in die .env eintragen und den Container neu starten.",
        f"  - Linux-Server: chown -R {uid}:{gid} auf den Datenordner (PK_DATA_PATH) und den Sicherungsordner (PK_BACKUP_PATH),",
        "    oder einen lokalen Ordner statt einer Netzfreigabe verwenden.",
        f"  Unterordner in {data_dir} legt POCKET KNIFE selbst an – bitte nicht von Hand als anderer Benutzer anlegen.",
        "  Siehe Handbuch → Installation → Auf einem NAS.",
    ]
    return "\n".join(lines)


def drop(dirs):
    """Started as root: hand over dirs, then switch permanently to the service user. Otherwise do nothing."""
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
    try:  # probe: switching back to root must no longer work
        os.setuid(0)
    except PermissionError:
        pass
    else:
        sys.exit("POCKET KNIFE: Rechte konnten nicht abgegeben werden – Start abgebrochen.")
    os.umask(0o077)
    os.environ["HOME"] = "/tmp"
    return True
