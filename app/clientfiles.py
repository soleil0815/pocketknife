"""Dateizugriff auf einen Client über die administrativen Freigaben (\\host\\C$ …).

Das Dienstkonto ist lokaler Administrator, also sind die Laufwerke über SMB als C$, D$ … erreichbar –
ohne Skript auf dem Client. Damit lässt sich im Browser durchsuchen, umbenennen, kopieren, verschieben,
löschen sowie herunter- und hochladen. Alle Pfade werden streng geprüft (kein Ausbruch, nur Laufwerksfreigaben).
"""
import datetime
import ntpath
import re
import stat as _stat

from . import config, kerberos, remote, util

# Ein einzelner Pfadbestandteil: keine Sonderzeichen, nicht „.“/„..“, nicht mit Punkt/Leerzeichen am Ende (Windows)
NAME_RE = re.compile(r'^(?!\.{1,2}$)[^<>:"/\\|?*\x00-\x1f]{0,254}[^<>:"/\\|?*\x00-\x1f. ]$')
MAX_DOWNLOAD = 2 * 1024 ** 3  # 2 GB Schutzgrenze für Downloads über den Browser


class FileError(Exception):
    pass


def _host(cid):
    from . import db
    c = db.q1("SELECT id, name, dns_name, online FROM computers WHERE id=?", (cid,))
    if not c:
        raise FileError("Computer nicht gefunden")
    return c, kerberos.host(c["dns_name"] or c["name"])


def _parse(path):
    """'C:\\Users\\x' → ('C', 'Users\\x'); nur Laufwerksbuchstaben, kein .. , kein UNC."""
    p = (path or "").strip().replace("/", "\\")
    m = re.match(r"^([A-Za-z]):\\?(.*)$", p)
    if not m:
        raise FileError("Bitte einen lokalen Pfad angeben, z. B. C:\\Users.")
    drive = m.group(1).upper()
    rel = [x for x in m.group(2).split("\\") if x not in ("", ".")]
    if any(x == ".." for x in rel):
        raise FileError("Ungültiger Pfad.")
    for x in rel:
        if not NAME_RE.match(x):
            raise FileError(f"Ungültiger Name im Pfad: {x}")
    return drive, "\\".join(rel)


def _unc(host, drive, rel=""):
    base = f"\\\\{host}\\{drive}$"
    return ntpath.join(base, rel) if rel else base + "\\"


def _session(cid):
    import smbclient
    c, host = _host(cid)
    remote._smb_session(host, config.service_login(), config.service_password())
    return smbclient, c, host


def _smb_errors():
    """OSError und die Fehler von smbprotocol (z. B. SMBLinkRedirectionError ist KEIN OSError)."""
    try:
        from smbprotocol.exceptions import SMBException
        return (OSError, SMBException)
    except ImportError:  # pragma: no cover
        return (OSError,)


def _link_target(e):
    """Ziel eines Windows-Links aus SMBLinkRedirectionError (z. B. „C:\\ProgramData“) als lokaler Pfad oder None."""
    t = str(getattr(e, "target", "") or "")
    if t.startswith("\\??\\"):
        t = t[4:]
    return t if re.match(r"^[A-Za-z]:(\\|$)", t) else None


def _is_link_error(e):
    return type(e).__name__ == "SMBLinkRedirectionError"


def drives(cid):
    """Laufwerksbuchstaben des Computers aus dem letzten Scan (für die Startauswahl)."""
    from . import db
    rows = db.q("SELECT drive, label, size, free FROM disks WHERE computer_id=? AND drive LIKE '_:' ORDER BY drive", (cid,))
    return [{"drive": r["drive"], "label": r["label"], "size": r["size"], "free": r["free"]} for r in rows]


def listing(cid, path):
    smbclient, _c, host = _session(cid)
    drive, rel = _parse(path)
    unc = _unc(host, drive, rel)
    out = []
    try:
        for e in smbclient.scandir(unc):
            try:
                # Links (Symlinks, Junctions wie „All Users“ → C:\ProgramData) NICHT verfolgen: der SMB-Server
                # leitet nicht auf lokale Ziele weiter. Art und Größe stehen schon im Verzeichniseintrag.
                st = e.stat(follow_symlinks=False)
                attr = getattr(st, "st_file_attributes", 0) or 0
                isdir = bool(attr & 0x10)
                reparse = _is_link(st, attr)  # nur Junctions/Symlinks – OneDrive-Platzhalter u. Ä. sind normale Einträge
                out.append({"name": e.name, "dir": isdir,
                            "size": 0 if isdir or reparse else st.st_size,
                            "modified": util.fmt(datetime.datetime.fromtimestamp(st.st_mtime)) if st.st_mtime else None,
                            "hidden": bool(attr & 0x2), "system": bool(attr & 0x4),
                            "reparse": reparse, "readonly": bool(attr & 0x1)})
            except _smb_errors():
                out.append({"name": e.name, "dir": False, "size": 0, "denied": True})
    except _smb_errors() as e:
        target = _link_target(e) if _is_link_error(e) else None
        if target:
            # Der Ordner selbst ist eine Verknüpfung – dorthin weiterleiten (z. B. C:\Users\All Users → C:\ProgramData)
            return {"redirect": target, "path": f"{drive}:\\{rel}" if rel else f"{drive}:\\"}
        raise FileError(_explain(e, host))
    out.sort(key=lambda x: (not x["dir"], x["name"].lower()))
    return {"path": f"{drive}:\\{rel}" if rel else f"{drive}:\\", "drive": drive, "items": out}


import threading as _threading

_search_sem = _threading.BoundedSemaphore(4)  # höchstens 4 Dateisuchen gleichzeitig (alle Clients)
_search_lock = _threading.Lock()
_search_busy = {"users": set(), "cids": set()}  # je Benutzer und je Computer höchstens eine Suche

# Suche AUF dem Client (per WinRM): der Computer durchsucht seine Platte selbst, zurück kommen nur die Treffer.
# Ein einziger Get-ChildItem -Recurse -Filter: Windows filtert die Namen selbst (FindFirstFile), PowerShell erzeugt
# nur für Treffer Objekte – um ein Vielfaches schneller als Ordner für Ordner (2.25.6; vorher reichte die Zeit oft nur
# für die obersten Ebenen). -like prüft danach genau nach (Windows-Filter sind großzügiger, z. B. Kurznamen 8.3).
# Constrained Language Mode: nur Cmdlets, -like, Hashtabellen, Funktionen, ConvertTo-Json, DateTime.ToString.
# Windows PowerShell 5.1 folgt bei -Recurse Verknüpfungen: Treffer unterhalb einer Junction/eines Symlinks
# (z. B. C:\Users\All Users → C:\ProgramData) werden verworfen, damit nichts doppelt erscheint; Fehlermeldungen
# für Verknüpfungen (die „Zugriff verweigert“-Junctions wie „Anwendungsdaten“) zählen nicht als nicht lesbar.
SEARCH_PS = r"""$ErrorActionPreference = 'Stop'
$start = __START__; $flt = __FLT__; $pat = __PAT__; $limit = __LIMIT__
if (-not (Test-Path -LiteralPath $start -PathType Container)) { 'PKJSON:{"error":"notfound"}'; return }
try { $null = @(Get-ChildItem -LiteralPath $start -Force -ErrorAction Stop | Select-Object -First 1) }
catch { 'PKJSON:' + (ConvertTo-Json -Compress -InputObject @{ error = 'denied'; message = ('' + $_.Exception.Message) }); return }
$ev = $null
$found = @(Get-ChildItem -LiteralPath $start -Filter $flt -Recurse -Force __FOLLOW__-ErrorAction SilentlyContinue -ErrorVariable ev |
  Where-Object { $_.Name -like $pat } | Select-Object -First ($limit * 2))
$lc = @{}
function PK-IsLink([string]$p) {
  if (-not $lc.ContainsKey($p)) {
    $it = Get-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue
    $lt = ''; if ($it) { $lt = '' + $it.LinkType }
    $lc[$p] = ($lt -eq 'Junction' -or $lt -eq 'SymbolicLink')
  }
  return $lc[$p]
}
function PK-UnderLink([string]$full) {
  $p = Split-Path -Path $full -Parent
  while ($p -and $p.Length -gt $start.TrimEnd('\').Length) {
    if (PK-IsLink $p) { return $true }
    $p = Split-Path -Path $p -Parent
  }
  return $false
}
$hits = @{}; $n = 0; $trunc = $false
foreach ($i in $found) {
  if (PK-UnderLink $i.FullName) { continue }
  if ($n -ge $limit) { $trunc = $true; break }
  $rp = $false
  if ((([int]$i.Attributes) -band 1024) -ne 0) { $lt = '' + $i.LinkType; $rp = ($lt -eq 'Junction' -or $lt -eq 'SymbolicLink') }
  $isDir = [bool]$i.PSIsContainer
  $size = 0; if (-not $isDir -and -not $rp) { $size = $i.Length }
  $hits[$n] = @{ f = (Split-Path -Path $i.FullName -Parent); n = $i.Name; d = $isDir; r = $rp; s = $size; m = $i.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss') }
  $n++
}
if ($found.Count -ge $limit * 2) { $trunc = $true }
$errs = 0; $k = 0
foreach ($e in @($ev)) {
  $k++; if ($k -gt 500) { $errs += 1; continue }
  $t = '' + $e.TargetObject
  if ($t -and (Test-Path -LiteralPath $t) -and (PK-IsLink $t)) { continue }
  if ($t -and (PK-UnderLink $t)) { continue }
  $errs++
}
'PKJSON:' + (ConvertTo-Json -Depth 3 -Compress -InputObject @{ items = @($hits.Values); dirs = -1; skipped = 0; errors = $errs; truncated = $trunc })
"""


def filter_pattern(q):
    """Suchbegriff → Filter für Get-ChildItem -Filter (Windows-Platzhalter * und ?; [ ] gelten dort wörtlich).
    Ohne Platzhalter „Teil des Namens“. Großzügig – like_pattern prüft danach genau."""
    remote.name_pattern(q, FileError)
    q = re.sub(r"\*{2,}", "*", str(q).strip())
    return q if any(ch in q for ch in "*?") else f"*{q}*"


def like_pattern(q):
    """Suchbegriff → Muster für PowerShell -like (gleiche Regeln wie remote.name_pattern): * und ? als Platzhalter,
    [ ] und ` wörtlich (mit ` maskiert); ohne Platzhalter „Teil des Namens“. Der Wert geht über util.psq ins Skript."""
    remote.name_pattern(q, FileError)  # gleiche Prüfung (Länge, mindestens 2 Zeichen, höchstens 8 *)
    q = re.sub(r"\*{2,}", "*", str(q).strip())
    esc = "".join("`" + ch if ch in "[]`" else ch for ch in q)
    return esc if any(ch in q for ch in "*?") else f"*{esc}*"


def search_script(start, q, limit, follow=False):
    """follow=True nur für den Test unter PowerShell 7 (verhält sich dann wie 5.1 und folgt Verknüpfungen)."""
    return (SEARCH_PS.replace("__START__", util.psq(start)).replace("__FLT__", util.psq(filter_pattern(q)))
            .replace("__PAT__", util.psq(like_pattern(q))).replace("__LIMIT__", str(int(limit)))
            .replace("__FOLLOW__", "-FollowSymlink " if follow else ""))


def _fmt_folder(d):
    d = str(d or "")
    return d if re.match(r"^[A-Za-z]:\\$", d) else d.rstrip("\\")


def _is_link(st, attr):
    """Echte Verknüpfung (Junction/Symlink)? Mit Reparse-Tag genau, sonst am Attribut 0x400."""
    if not attr or not attr & 0x400:
        return False
    tag = getattr(st, "st_reparse_tag", None)
    if not tag:  # Tag unbekannt → vorsichtshalber als Verknüpfung behandeln (wie bisher)
        return True
    return tag in (0xA0000003, 0xA000000C)  # IO_REPARSE_TAG_MOUNT_POINT, IO_REPARSE_TAG_SYMLINK


def search(cid, path, q, limit=300, seconds=300, user_key=None):
    """Dateien und Ordner ab „path“ (mit allen Unterordnern) finden – wie die Suche im Windows-Explorer.
    Läuft auf dem Client selbst (WinRM). Ist WinRM gar nicht erreichbar (Verbindung), wird über die Freigabe C$
    gesucht (langsamer, deshalb enger begrenzt) – „via“ sagt, welcher Weg benutzt wurde.
    Je Benutzer und je Computer läuft höchstens eine Suche, insgesamt höchstens vier."""
    pat = like_pattern(q)
    drive, rel = _parse(path)
    c, host = _host(cid)
    start = f"{drive}:\\{rel}" if rel else f"{drive}:\\"
    with _search_lock:
        if user_key is not None and user_key in _search_busy["users"]:
            raise FileError("Ihre vorige Suche läuft noch – bitte warten, bis sie fertig ist.")
        if cid in _search_busy["cids"]:
            raise FileError("Auf diesem Computer läuft gerade schon eine Suche – bitte einen Moment warten.")
        _search_busy["cids"].add(cid)
        if user_key is not None:
            _search_busy["users"].add(user_key)
    try:
        if not _search_sem.acquire(timeout=3):
            raise FileError("Gerade laufen schon mehrere Suchen – bitte einen Moment warten.")
        try:
            script = search_script(start, q, limit)
            try:
                out, err, _ = remote.ps(c["dns_name"] or c["name"], script, timeout=seconds)
            except remote.RemoteConnectError:
                try:
                    res = _search_smb(cid, drive, rel, q)
                except remote.RemoteError as e:
                    raise FileError(str(e))
                res["via"] = "smb"
                return res
            except remote.RemoteError as e:
                if "keine Antwort" in str(e):
                    raise FileError(f"Die Suche hat nach {seconds // 60} Minuten nicht geendet – bitte in einem Unterordner "
                                    "beginnen oder genauer suchen.")
                raise FileError(str(e))
            i = out.find("PKJSON:")
            if i < 0:
                raise FileError(util.first_line(err, "Keine Antwort vom Computer erhalten.")[:400])
            import json
            data = json.loads(out[i + 7:].strip().splitlines()[0])
            if data.get("error") == "notfound":
                raise FileError("Nicht gefunden.")
            if data.get("error"):
                m = str(data.get("message") or "")
                low = m.lower()
                if "denied" in low or "verweigert" in low:
                    raise FileError("Zugriff verweigert.")
                if "too long" in low or "zu lang" in low:
                    raise FileError("Pfad zu lang (über 260 Zeichen) – bitte in einem Unterordner suchen.")
                raise FileError(m[:300] or "Ordner nicht lesbar.")
            items = data.get("items") or []
            if isinstance(items, dict):
                items = [items]
            hits = [{"folder": _fmt_folder(x.get("f")), "name": str(x.get("n") or ""), "dir": bool(x.get("d")),
                     "reparse": bool(x.get("r")), "denied": False, "size": int(x.get("s") or 0), "modified": x.get("m")}
                    for x in items if isinstance(x, dict)][:limit]
            hits.sort(key=lambda x: (not x["dir"], (x["folder"] + "\\" + x["name"]).lower()))
            return {"path": start, "items": hits, "truncated": bool(data.get("truncated")), "dirs": int(data.get("dirs") or 0),
                    "skipped": int(data.get("skipped") or 0), "errors": int(data.get("errors") or 0), "via": "winrm"}
        finally:
            _search_sem.release()
    finally:
        with _search_lock:
            _search_busy["cids"].discard(cid)
            if user_key is not None:
                _search_busy["users"].discard(user_key)


def _search_smb(cid, drive, rel, q, limit=300, max_dirs=5000, seconds=20):
    """Ersatzweg über die Freigabe C$ (jeder Ordner einzeln übers Netz – deshalb enger begrenzt).
    Nicht lesbare Unterordner werden übersprungen (gezählt in „skipped“)."""
    import time as _time
    rx = remote.name_pattern(q, FileError)
    smbclient, _c, host = _session(cid)
    todo, hits, dirs, skipped, truncated = [rel], [], 0, 0, False
    t0 = _time.time()
    while todo:
        if dirs >= max_dirs or _time.time() - t0 > seconds:
            truncated = True
            break
        cur = todo.pop(0)
        dirs += 1
        try:
            entries = list(smbclient.scandir(_unc(host, drive, cur)))
        except _smb_errors() as e:
            if dirs == 1:
                target = _link_target(e) if _is_link_error(e) else None
                if target:
                    raise FileError(f"Der Ordner ist eine Verknüpfung auf {target} – bitte dort suchen.")
                raise FileError(_explain(e, host))
            skipped += 1
            continue
        local = f"{drive}:\\{cur}" if cur else f"{drive}:\\"
        for e in entries:
            try:
                st = e.stat(follow_symlinks=False)
                attr = getattr(st, "st_file_attributes", 0) or 0
            except _smb_errors():
                attr, st = None, None
            isdir = bool(attr and attr & 0x10)
            link = bool(attr) and _is_link(st, attr)
            if rx.search(e.name):
                if len(hits) >= limit:
                    truncated = True
                    break
                hits.append({"folder": local, "name": e.name, "dir": isdir, "reparse": link,
                             "denied": st is None,
                             "size": 0 if (isdir or link or st is None) else st.st_size,
                             "modified": util.fmt(datetime.datetime.fromtimestamp(st.st_mtime)) if st is not None and st.st_mtime else None})
            if isdir and not link:
                todo.append(f"{cur}\\{e.name}" if cur else e.name)
        if truncated:
            break
    hits.sort(key=lambda x: (not x["dir"], (x["folder"] + "\\" + x["name"]).lower()))
    return {"path": f"{drive}:\\{rel}" if rel else f"{drive}:\\", "items": hits, "truncated": truncated,
            "dirs": dirs, "skipped": skipped, "errors": 0}


def _explain(e, host):
    s = str(e)
    if _is_link_error(e):
        t = _link_target(e) or getattr(e, "target", "") or "?"
        return f"„{ntpath.basename(str(getattr(e, 'path', '')) or '')}“ ist eine Verknüpfung auf {t} – bitte dort öffnen."
    if "STATUS_ACCESS_DENIED" in s or "Access is denied" in s:
        return "Zugriff verweigert – das Dienstkonto ist auf diesem Computer kein lokaler Administrator, oder der Ordner ist gesperrt."
    if "STATUS_OBJECT_NAME_NOT_FOUND" in s or "STATUS_OBJECT_PATH_NOT_FOUND" in s or "No such file" in s:
        return "Nicht gefunden."
    if "STATUS_SHARING_VIOLATION" in s:
        return "Die Datei ist auf dem Computer in Benutzung."
    if "STATUS_BAD_NETWORK_NAME" in s:
        return f"Laufwerk auf {host} nicht gefunden (administrative Freigabe C$ deaktiviert?)."
    t, _ = remote.guard.explain(s)
    return t or f"SMB-Fehler: {s}"


def _info(smbclient, unc):
    """(ist_ordner, ist_verknüpfung) ohne einem Link zu folgen; (None, False) wenn nicht vorhanden."""
    try:
        st = smbclient.stat(unc, follow_symlinks=False)
    except _smb_errors():
        return None, False
    attr = getattr(st, "st_file_attributes", 0) or 0
    if attr:
        return bool(attr & 0x10), bool(attr & 0x400)
    return _stat.S_ISDIR(st.st_mode), False


def _is_dir(smbclient, unc):
    return _info(smbclient, unc)[0]


def mkdir(cid, path, name):
    smbclient, _c, host = _session(cid)
    drive, rel = _parse(path)
    if not NAME_RE.match(name or ""):
        raise FileError("Ungültiger Ordnername.")
    unc = _unc(host, drive, ntpath.join(rel, name))
    try:
        smbclient.mkdir(unc)
    except _smb_errors() as e:
        raise FileError(_explain(e, host))


def rename(cid, path, name, new):
    smbclient, _c, host = _session(cid)
    drive, rel = _parse(path)
    if not NAME_RE.match(name or "") or not NAME_RE.match(new or ""):
        raise FileError("Ungültiger Name.")
    src = _unc(host, drive, ntpath.join(rel, name))
    dst = _unc(host, drive, ntpath.join(rel, new))
    if _is_dir(smbclient, dst) is not None:
        raise FileError(f"„{new}“ gibt es bereits.")
    try:
        smbclient.rename(src, dst)
    except _smb_errors() as e:
        raise FileError(_explain(e, host))


def delete(cid, items):
    """items: [{path, name, dir}] – Dateien und (rekursiv) Ordner löschen. Gibt (gelöscht, [fehler]) zurück."""
    import smbclient.shutil as smbshutil
    smbclient, _c, host = _session(cid)
    ok, errs = 0, []
    for it in items or []:
        try:
            drive, rel = _parse(it.get("path"))
            name = it.get("name")
            if not NAME_RE.match(name or ""):
                raise FileError("Ungültiger Name.")
            unc = _unc(host, drive, ntpath.join(rel, name))
            isdir, link = _info(smbclient, unc)
            if isdir is None:
                raise FileError("Nicht gefunden.")
            if link:
                # Nur die Verknüpfung entfernen – niemals ihr Ziel (z. B. C:\ProgramData) rekursiv löschen
                (smbclient.rmdir if isdir else smbclient.remove)(unc)
            elif isdir:
                smbshutil.rmtree(unc)
            else:
                smbclient.remove(unc)
            ok += 1
        except _smb_errors() as e:
            errs.append(f"{it.get('name')}: {_explain(e, host)}")
        except FileError as e:
            errs.append(f"{it.get('name')}: {e}")
    return ok, errs


def transfer(cid, action, src_dir, items, dst_dir):
    """Kopieren/Verschieben innerhalb desselben Computers. action: 'copy' | 'move'."""
    import smbclient
    import smbclient.shutil as smbshutil
    smbclient, _c, host = _session(cid)
    sdrive, srel = _parse(src_dir)
    ddrive, drel = _parse(dst_dir)
    ok, errs = 0, []
    for name in items or []:
        try:
            if not NAME_RE.match(name or ""):
                raise FileError("Ungültiger Name.")
            s = _unc(host, sdrive, ntpath.join(srel, name))
            d = _unc(host, ddrive, ntpath.join(drel, name))
            if ntpath.normcase(s) == ntpath.normcase(d):
                raise FileError("Quelle und Ziel sind gleich.")
            if _is_dir(smbclient, d) is not None:
                raise FileError(f"„{name}“ gibt es im Ziel bereits.")
            isdir, link = _info(smbclient, s)
            if isdir is None:
                raise FileError("Nicht gefunden.")
            if link and action != "move":
                raise FileError("Verknüpfungen lassen sich nicht kopieren – bitte das Ziel der Verknüpfung öffnen und dort kopieren.")
            if action == "move":
                smbclient.rename(s, d)
            elif isdir:
                smbshutil.copytree(s, d)
            else:
                smbshutil.copyfile(s, d)
            ok += 1
        except _smb_errors() as e:
            errs.append(f"{name}: {_explain(e, host)}")
        except FileError as e:
            errs.append(f"{name}: {e}")
    return ok, errs


def open_read(cid, path, name, _hops=0):
    smbclient, _c, host = _session(cid)
    drive, rel = _parse(path)
    if not NAME_RE.match(name or ""):
        raise FileError("Ungültiger Name.")
    unc = _unc(host, drive, ntpath.join(rel, name))
    isdir, link = _info(smbclient, unc)
    if isdir is None:
        raise FileError("Nicht gefunden.")
    if isdir:
        raise FileError("Ordner lassen sich nicht herunterladen.")
    try:
        size = smbclient.stat(unc).st_size
    except _smb_errors() as e:
        target = _link_target(e) if _is_link_error(e) else None
        if target and _hops < 5 and ntpath.basename(target):
            # Verknüpfung auf eine Datei: wie im Explorer das Ziel herunterladen
            return open_read(cid, ntpath.dirname(target) or target[:3], ntpath.basename(target), _hops + 1)
        raise FileError(_explain(e, host))
    if size > MAX_DOWNLOAD:
        raise FileError(f"Datei zu groß für den Download über den Browser ({size // 1048576} MB, Grenze {MAX_DOWNLOAD // 1048576} MB).")

    def gen():
        with smbclient.open_file(unc, mode="rb") as f:
            while True:
                chunk = f.read(1024 * 256)
                if not chunk:
                    break
                yield chunk
    return gen(), size


def write_file(cid, path, name, fileobj):
    smbclient, _c, host = _session(cid)
    drive, rel = _parse(path)
    if not NAME_RE.match(name or ""):
        raise FileError("Ungültiger Dateiname.")
    unc = _unc(host, drive, ntpath.join(rel, name))
    try:
        with smbclient.open_file(unc, mode="wb") as f:
            total = 0
            while True:
                chunk = fileobj.read(1024 * 512)
                if not chunk:
                    break
                total += len(chunk)
                f.write(chunk)
        return total
    except _smb_errors() as e:
        raise FileError(_explain(e, host))
