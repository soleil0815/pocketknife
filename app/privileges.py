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


# Ordner, deren Besitzer nie geändert wird (relativ zum Datenordner). 2.32.12: data/update/host des früheren
# Update-Helfers (2.32.9–2.32.12) gehört root – update.sh --cleanup räumt ihn weg.
KEEP_OWNER = (os.path.join("update", "host"),)


def chown_tree(path, uid, gid, keep=KEEP_OWNER):
    """Alles unterhalb von path (ohne Verknüpfungen zu folgen) uid:gid geben – außer den Ordnern in keep (relativ zu
    path, samt Inhalt). Gibt (geändert, fehler) zurück."""
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
    """Darf der laufende Benutzer in d anlegen und löschen? Wirft OSError, wenn nicht."""
    import tempfile
    fd, p = tempfile.mkstemp(prefix=".pk-probe-", dir=d)
    os.close(fd)
    os.remove(p)


def unwritable(dirs, probe=None):
    """2.30: Ordner, in die der Dienst nicht schreiben darf – [(Ordner, Fehlertext)]. Fehlende Ordner werden angelegt."""
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
    """Verständliche Meldung statt eines Tracebacks, wenn der Dienst nicht schreiben darf (typisch: NAS mit eigenen
    Freigabe-Rechten wie Synology, oder ein Ordner, den jemand von Hand als anderer Benutzer angelegt hat)."""
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
