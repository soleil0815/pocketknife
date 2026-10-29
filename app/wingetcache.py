"""2.35: winget-Cache – die aktuelle Version ausgewählter winget-Pakete liegt als normales Paket im Repository.

Für Computer, auf denen winget nicht läuft (vor Windows 10 1809 / Windows Server 2019): POCKET KNIFE lädt für jede
winget-ID der Liste das Manifest aus dem winget-Katalog (wie winget selbst: cdn.winget.microsoft.com, Index v2), wählt
den passenden Installer (Architektur, Bereich, Sprache), lädt ihn, prüft die SHA-256 aus dem Manifest und legt ihn im
Repository ab (<Ordner>\\<winget-ID>\\<Version>\\). Daraus entsteht ein Paket mit stillen Schaltern, Erfolgscodes und
Erkennung. Kommt eine neue Version, wird sie geladen, das Paket umgestellt und die alte Version gelöscht – im Cache
liegt je Programm immer nur die aktuelle. Entfernen (aus der Liste oder durch Löschen des Pakets) räumt Ordner und
Paket mit weg.

Aktualisieren über winget (Softwareübersicht, Computeransicht, Zeitplan „winget-Updates“) nimmt für Computer ohne
winget automatisch das Paket aus dem Cache. Die Clients brauchen dafür weder winget noch Internet.
"""
import hashlib
import json
import os
import re
import tempfile
import threading
import urllib.parse
import urllib.request
import zlib

from . import config, db, pklog, proxy, remote, util

BASE = "https://cdn.winget.microsoft.com/cache"
ARCHS = {"x64": "x64 (64 Bit)", "x86": "x86 (32 Bit)", "arm64": "ARM64"}
SCOPES = {"machine": "Computer (alle Benutzer)"}
STATUS = {"new": "Noch nicht geladen", "ok": "Aktuell", "error": "Fehler", "unsupported": "Nicht möglich",
          "checking": "Wird geprüft …"}
# Stille Schalter je Installer-Typ, wie winget sie verwendet, wenn das Manifest keine nennt. MSI: POCKET KNIFE ruft
# msiexec selbst mit /qn /norestart auf – hier nur Eigenschaften.
SILENT = {"inno": "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-", "nullsoft": "/S", "burn": "/quiet /norestart"}
SUPPORTED = ("msi", "wix", "inno", "nullsoft", "burn", "exe")
INSTALLER_KEYS = ("InstallerLocale", "Platform", "MinimumOSVersion", "InstallerType", "NestedInstallerType", "Scope",
                  "InstallerSwitches", "InstallerSuccessCodes", "ExpectedReturnCodes", "ProductCode",
                  "AppsAndFeaturesEntries", "Dependencies", "ElevationRequirement")
# Rückgabewerte, die als Erfolg gelten (ExpectedReturnCodes → ReturnResponse)
OK_RESPONSES = ("rebootRequiredToFinish", "rebootRequiredForInstall", "rebootInitiated", "alreadyInstalled")
MAX_YAML = 20 * 1024 * 1024
_lock = threading.Lock()       # immer nur ein Lauf (Laden in das Repository)
_elock = threading.RLock()     # ein Eintrag: Prüfen/Laden und Entfernen nie gleichzeitig
_state = {"running": False, "current": None}
TMP_PREFIX = "pk-wc-"


class CacheError(ValueError):
    pass


def init():
    """Tabelle legt db.init an (winget_cache); hier: beim Start hängengebliebene „wird geprüft“ zurücksetzen und Reste
    abgebrochener Downloads löschen."""
    db.ex("UPDATE winget_cache SET status='error', message='Prüfung durch Neustart unterbrochen' WHERE status='checking'")
    try:
        for f in os.listdir(config.WINGET_DIR):
            if f.startswith(TMP_PREFIX) and f.endswith(".part"):
                os.remove(os.path.join(config.WINGET_DIR, f))
    except OSError:
        pass


def _norm_folder(v):
    return "\\".join(x for x in str(v or "").replace("/", "\\").split("\\") if x not in ("", "."))


def folder_root():
    """Cache-Ordner im Repository – nie leer (sonst wäre es das ganze Repository) und nie mit „..“."""
    v = _norm_folder(db.setting("winget_cache_folder") or "winget-cache")
    if not v or ".." in v.split("\\") or ":" in v:
        raise CacheError("Der Ordner des winget-Caches ist ungültig (Einstellungen des winget-Caches).")
    return v


# --- MSZIP (Windows Compression API, Puffer-Modus) -----------------------------------------------
def mszip_decompress(data, limit=MAX_YAML):
    """versionData.mszyml entpacken: 24 Byte Kopf (Kennung 0a51e5c01800, Prüfbyte, Verfahren 02 = MSZIP, Größe
    entpackt, Größe des ersten Stücks), danach Stücke: 4 Byte Länge, „CK“, Deflate-Daten – jedes Stück mit dem bisher
    Entpackten als Wörterbuch."""
    data = bytes(data or b"")
    if len(data) < 24 or data[:6] != bytes.fromhex("0a51e5c01800") or data[7] != 2:
        raise CacheError("Unbekanntes Format (kein MSZIP).")
    total = int.from_bytes(data[8:16], "little")
    if total > limit:
        raise CacheError("Entpackte Daten unplausibel groß.")
    out = bytearray()
    pos = 24
    while pos < len(data) and len(out) < total:
        if pos + 6 > len(data):
            raise CacheError("MSZIP: Stück abgeschnitten.")
        n = int.from_bytes(data[pos:pos + 4], "little")
        pos += 4
        chunk = data[pos:pos + n]
        pos += n
        if len(chunk) != n or chunk[:2] != b"CK":
            raise CacheError("MSZIP: Stück beschädigt.")
        d = zlib.decompressobj(-15, zdict=bytes(out[-32768:])) if out else zlib.decompressobj(-15)
        part = d.decompress(chunk[2:], total - len(out) + 1)
        out += part
        if len(out) > total:
            raise CacheError("MSZIP: mehr Daten als angegeben.")
    if len(out) != total:
        raise CacheError("MSZIP: Größe stimmt nicht.")
    return bytes(out)


_LOADER = None


def _loader():
    """SafeLoader ohne Umwandlung in Zahlen/Wahrheitswerte: Versionen wie 1.10 oder 2.50 bleiben Text (wie bei winget)."""
    global _LOADER
    if _LOADER is None:
        import yaml

        class StrLoader(yaml.SafeLoader):
            pass
        StrLoader.yaml_implicit_resolvers = {
            k: [(tag, rx) for tag, rx in v if tag not in ("tag:yaml.org,2002:int", "tag:yaml.org,2002:float",
                                                          "tag:yaml.org,2002:bool", "tag:yaml.org,2002:timestamp")]
            for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()}
        _LOADER = StrLoader
    return _LOADER


def _int(v):
    try:
        return int(str(v).strip(), 0) if str(v).strip().lower().startswith(("0x", "-0x")) else int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _yaml(text):
    import yaml
    try:
        return yaml.load(text, Loader=_loader())  # noqa: S506 – SafeLoader-Unterklasse
    except yaml.YAMLError as e:
        raise CacheError(f"Manifest nicht lesbar: {str(e).splitlines()[0]}")


# --- Herunterladen ----------------------------------------------------------------------------
def _get(url, limit):
    req = urllib.request.Request(url, headers={"User-Agent": "POCKETKNIFE/" + config.VERSION})
    try:
        with proxy.urlopen(req, timeout=60) as r:
            data = r.read(limit + 1)
    except Exception as e:
        raise CacheError(f"Download fehlgeschlagen ({url.split('?')[0]}): {proxy.safe_error(e)}")
    if len(data) > limit:
        raise CacheError("Datei unplausibel groß.")
    return data


def _download(url, sha, max_bytes):
    """Installer in eine temporäre Datei laden und die SHA-256 prüfen. Gibt (pfad, größe) zurück."""
    if not re.match(r"^https?://", url or "", re.I):
        raise CacheError("Download-Adresse fehlt oder ist ungültig.")
    fd, tmp = tempfile.mkstemp(dir=config.WINGET_DIR, prefix=TMP_PREFIX, suffix=".part")
    os.close(fd)
    h, size = hashlib.sha256(), 0
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "POCKETKNIFE/" + config.VERSION})
        with proxy.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
            while True:
                b = r.read(1024 * 1024)
                if not b:
                    break
                size += len(b)
                if size > max_bytes:
                    raise CacheError(f"Installer größer als {max_bytes // 1048576} MB (Einstellung im winget-Cache).")
                h.update(b)
                f.write(b)
        got = h.hexdigest()
        if not sha or got.lower() != str(sha).lower():
            raise CacheError(f"Prüfsumme stimmt nicht (Manifest {str(sha)[:16]}…, geladen {got[:16]}…) – der Hersteller "
                             "hat die Datei vermutlich ausgetauscht. Nicht übernommen; nächster Versuch beim nächsten Lauf.")
        return tmp, size
    except CacheError:
        os.remove(tmp)
        raise
    except Exception as e:
        os.remove(tmp)
        raise CacheError(f"Download fehlgeschlagen: {proxy.safe_error(e)}")


def index_entry(wid):
    """(id, name, latest_version, hash-hex) aus dem winget-Index (Format v2) – oder CacheError."""
    import sqlite3
    from . import winget
    p = winget.index_path()
    if not os.path.exists(p):
        raise CacheError("Kein winget-Index geladen (Einstellungen → winget).")
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(packages)")]
        if "hash" not in cols or "latest_version" not in cols:
            raise CacheError("Der geladene winget-Index ist im alten Format (v1) – bitte den Index neu laden.")
        r = con.execute("SELECT id, name, latest_version, hash FROM packages WHERE id = ? COLLATE NOCASE", (wid,)).fetchone()
    finally:
        con.close()
    if not r:
        raise CacheError(f"„{wid}“ gibt es im winget-Katalog nicht (mehr).")
    hx = r[3].hex() if isinstance(r[3], (bytes, bytearray)) else str(r[3] or "").strip().lower()
    if not re.match(r"^[0-9a-f]{64}$", hx):
        raise CacheError("Im winget-Index fehlt die Prüfsumme des Pakets.")
    return r[0], r[1], r[2], hx


def fetch_manifest(wid):
    """Manifest der neuesten Version (dict) – wie winget: versionData.mszyml, dann das Manifest, beides mit Prüfsumme."""
    pid, name, latest, hx = index_entry(wid)
    vd_raw = _get(f"{BASE}/packages/{urllib.parse.quote(pid, safe='+.-_')}/{hx[:8]}/versionData.mszyml", 5 * 1024 * 1024)
    if hashlib.sha256(vd_raw).hexdigest() != hx:
        raise CacheError("Versionsliste: Prüfsumme stimmt nicht – Index evtl. veraltet (Index neu laden).")
    vd = _yaml(mszip_decompress(vd_raw).decode("utf-8", "replace")) or {}
    vers = [v for v in (vd.get("vD") or []) if isinstance(v, dict) and v.get("v") and v.get("rP") and v.get("s256H")]
    if not vers:
        raise CacheError("Versionsliste leer.")
    pick = next((v for v in vers if str(v["v"]) == str(latest)), None)
    if not pick:
        pick = vers[0]
        for v in vers[1:]:
            if util.vcmp(str(v["v"]), str(pick["v"])) > 0:
                pick = v
    rp = str(pick["rP"]).replace("\\", "/").lstrip("/")
    if ".." in rp.split("/"):
        raise CacheError("Ungültiger Manifest-Pfad.")
    raw = _get(f"{BASE}/{urllib.parse.quote(rp, safe='/+.-_')}", MAX_YAML)
    if hashlib.sha256(raw).hexdigest() != str(pick["s256H"]).lower():
        raise CacheError("Manifest: Prüfsumme stimmt nicht.")
    man = _yaml(raw.decode("utf-8-sig", "replace"))
    if not isinstance(man, dict) or str(man.get("PackageIdentifier") or "").lower() != pid.lower():
        raise CacheError("Manifest gehört nicht zu diesem Paket.")
    return man


# --- Installer wählen ---------------------------------------------------------------------------
def _merged(man, inst):
    out = {k: man[k] for k in INSTALLER_KEYS if k in man}
    for k, v in inst.items():
        if k == "InstallerSwitches" and isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = {**out[k], **v}
        else:
            out[k] = v
    return out


def _ver_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v or ""))[:4])


def choose(man, arch="x64", scope="machine", locale=None):
    """Passenden Installer aus dem Manifest – oder CacheError mit dem Grund."""
    insts = [_merged(man, i) for i in (man.get("Installers") or []) if isinstance(i, dict)]
    if not insts:
        raise CacheError("Das Manifest enthält keinen Installer.")
    locale = (locale or db.setting("winget_cache_locale") or "de-DE").lower()
    found_types = sorted({str(i.get("InstallerType") or "").lower() for i in insts})
    cand = []
    for i in insts:
        typ = str(i.get("InstallerType") or "").lower()
        a = str(i.get("Architecture") or "").lower()
        if typ not in SUPPORTED or i.get("NestedInstallerType"):
            continue
        plat = [str(x) for x in (i.get("Platform") or [])]
        if plat and "Windows.Desktop" not in plat:
            continue
        if a == arch:
            ra = 0
        elif a == "neutral":
            ra = 1
        elif arch == "x64" and a == "x86":
            ra = 2
        else:
            continue
        sc = str(i.get("Scope") or "").lower()
        if sc and sc != scope:
            continue
        if str(i.get("ElevationRequirement") or "") == "elevationProhibited":
            continue  # darf nicht mit Administratorrechten laufen – als SYSTEM ungeeignet
        il = str(i.get("InstallerLocale") or "").lower()
        rl = 0 if il == locale else 1 if il and il.split("-")[0] == locale.split("-")[0] else 2 if not il else 3 if il == "en-us" else 4
        cand.append((ra, rl, 0 if sc else 1, i))
    if not cand:
        what = ", ".join(t for t in found_types if t) or "unbekannt"
        raise CacheError(f"Kein passender Installer ({ARCHS.get(arch, arch)}, für alle Benutzer, Typ MSI/EXE). Im Manifest: {what}.")
    cand.sort(key=lambda x: x[:3])
    return cand[0][3]


def plan(man, inst, scope="machine"):
    """Installationsschritt aus Manifest + Installer: Typ, Schalter, Erfolgscodes, Erkennung, Hinweise."""
    typ = str(inst.get("InstallerType") or "").lower()
    sw = inst.get("InstallerSwitches") if isinstance(inst.get("InstallerSwitches"), dict) else {}
    custom = str(sw.get("Custom") or "").strip()
    hints = []
    if typ in ("msi", "wix"):
        it = "msi"
        args = " ".join(x for x in (custom, str(sw.get("Upgrade") or "").strip()) if x)
        if scope == "machine" and "allusers" not in args.lower():
            args = (args + " ALLUSERS=1").strip()
    else:
        it = "exe"
        silent = str(sw.get("Silent") or SILENT.get(typ) or "").strip()
        if not silent:
            raise CacheError("Der Installer (EXE) nennt im Manifest keine Schalter für die stille Installation – dafür "
                             "bitte selbst ein Paket anlegen.")
        args = " ".join(x for x in (silent, custom, str(sw.get("Upgrade") or "").strip()) if x)
        if typ == "inno" and scope == "machine" and "/allusers" not in args.lower() and "/currentuser" not in args.lower():
            args += " /ALLUSERS"
    codes = [0, 3010, 1641]
    for c in inst.get("InstallerSuccessCodes") or []:
        c = _int(c)
        if c is not None and c not in codes:
            codes.append(c)
    for e in inst.get("ExpectedReturnCodes") or []:
        c = _int(e.get("InstallerReturnCode")) if isinstance(e, dict) else None
        if c is not None and e.get("ReturnResponse") in OK_RESPONSES and c not in codes:
            codes.append(c)
    arp = [x for x in (inst.get("AppsAndFeaturesEntries") or []) if isinstance(x, dict)]
    dn = str((arp[0].get("DisplayName") if arp else "") or man.get("PackageName") or "").strip()
    # Version im Anzeigenamen (z. B. „7-Zip 24.09 (x64 edition)“) durch * ersetzen – sonst fände die Erkennung ältere nie
    detect = re.sub(r"\s*\bv?\d+(\.\d+)+\S*", " *", dn).strip()
    detect = re.sub(r"(\s*\*)+", " *", detect).strip()
    detect = detect.replace("[", "?").replace("]", "?")  # [ ] wären unter -like Platzhalter
    dver = str((arp[0].get("DisplayVersion") if arp else "") or man.get("PackageVersion") or "").strip()
    deps = (inst.get("Dependencies") or {}) if isinstance(inst.get("Dependencies"), dict) else {}
    pk = [str(d.get("PackageIdentifier")) for d in (deps.get("PackageDependencies") or []) if isinstance(d, dict)]
    if pk:
        hints.append("braucht außerdem: " + ", ".join(pk[:5]) + " (ggf. ebenfalls in den Cache aufnehmen)")
    pc = str(inst.get("ProductCode") or (arp[0].get("ProductCode") if arp else "") or "").strip()
    return {"install_type": it, "arguments": args, "success_codes": ",".join(str(c) for c in codes),
            "detect_name": detect[:200], "detect_version": dver[:50], "product_code": pc[:60] if pc.startswith("{") else "",
            "min_os": str(inst.get("MinimumOSVersion") or "").strip(), "type": typ, "hints": hints}


def _file_name(url, wid, version, it):
    ext = ".msi" if it == "msi" else ".exe"
    base = urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1])
    base = re.sub(r"[^A-Za-z0-9._+-]+", "_", base).strip("._")[:120]
    if not base.lower().endswith(ext):
        base = re.sub(r"[^A-Za-z0-9._+-]+", "_", f"{wid}_{version}")[:120] + ext
    return base


def _safe_dir(s):
    s = re.sub(r"[^A-Za-z0-9._+-]+", "_", str(s or "")).strip("._ ")
    return s[:100] or "_"


# --- Einträge ------------------------------------------------------------------------------------
def entries():
    return db.q("SELECT * FROM winget_cache ORDER BY winget_id COLLATE NOCASE")


def entry(eid):
    return db.q1("SELECT * FROM winget_cache WHERE id=?", (eid,))


def add(wid, arch="x64", user=None):
    wid = str(wid or "").strip()
    if not util.WINGET_ID_RE.match(wid):
        raise CacheError("Gültige winget-ID angeben (z. B. 7zip.7zip).")
    if arch not in ARCHS:
        raise CacheError("Ungültige Architektur.")
    folder_root()
    pid, name, _latest, _hx = index_entry(wid)
    if db.val("SELECT 1 FROM winget_cache WHERE winget_id=? COLLATE NOCASE", (pid,)):
        raise CacheError(f"„{pid}“ ist schon im Cache.")
    eid = db.ex("INSERT INTO winget_cache(winget_id, arch, scope, status, name, created_at, created_by) VALUES(?,?,?,?,?,?,?)",
                (pid, arch, "machine", "new", name, util.now(), user))
    pklog.info("winget", f"winget-Cache: „{pid}“ aufgenommen.", user=user)
    return eid


def _usage(pid):
    """Wo wird das Cache-Paket außerdem benutzt? (Zeitpläne, Regeln, verschachtelt, Kiosk) – Liste von Texten."""
    from . import deploy, steps
    out = [f"Zeitplan „{r['name']}“" for r in db.q("SELECT name, packages FROM schedules")
           if pid in json.loads(r["packages"] or "[]")]
    try:
        out += [f"Regel „{r['name']}“" for r in db.q("SELECT name FROM update_rules WHERE package_id=?", (pid,))]
    except Exception:
        pass
    for r in db.q("SELECT id, name, steps FROM packages WHERE steps LIKE '%nested%'"):
        if any(s.get("type") == "nested" and s.get("package_id") == pid
               for _k, s in steps.iter_steps(steps.normalize(deploy.row_to_package(r)))):
            out.append(f"Paket „{r['name']}“")
    try:
        if db.val("SELECT 1 FROM kiosk_items WHERE package_id=? OR uninstall_package_id=?", (pid, pid)):
            out.append("Software-Kiosk")
    except Exception:
        pass
    return out


def _users(rel, own_pid=None):
    """Wer benutzt den Ordner außer dem eigenen Cache-Paket? (Pakete, geplante/laufende Bereitstellungen, Mandanten)"""
    from . import repofiles
    hits = [h for h in repofiles.usage(rel) if not (h[4][0] == "pkg" and own_pid and h[4][1] == own_pid)]
    return repofiles.usage_summary(hits) if hits else ""


def _lock_entry(timeout=3):
    if not _elock.acquire(timeout=timeout):
        raise CacheError("Der Eintrag wird gerade geprüft bzw. geladen – bitte gleich noch einmal.")


def remove(eid, user=None, package_gone=False):
    """Eintrag entfernen und alles dazu aufräumen: Ordner im Repository und das erzeugte Paket. package_gone: das Paket
    wurde gerade gelöscht (Paketliste) – nur noch Ordner und Eintrag."""
    _lock_entry(1800 if package_gone else 3)
    try:
        e = entry(eid)
        if not e:
            raise CacheError("Eintrag nicht gefunden.")
        folder = f"{folder_root()}\\{_safe_dir(e['winget_id'])}"
        own = e["package_id"]
        if own and not package_gone and db.val("SELECT 1 FROM packages WHERE id=?", (own,)):
            used = _usage(own)
            if used:
                raise CacheError("Das Paket aus dem Cache wird noch verwendet: " + ", ".join(used) + " – dort zuerst entfernen.")
        busy = _users(folder, own)
        if busy and not package_gone:
            raise CacheError(f"Der Ordner wird noch benutzt ({busy}) – bitte danach entfernen.")
        if own and not package_gone:
            db.ex("DELETE FROM packages WHERE id=?", (own,))
            try:
                from . import repofiles
                repofiles.forget_idents(own)
            except Exception:
                pass
        gone = False
        if not busy:
            try:
                if remote.repo_stat(folder) == "dir":
                    remote.repo_delete(folder)
                    remote.repo_cache_apply("remove", folder)
                gone = True
            except Exception as ex:
                pklog.warn("winget", f"winget-Cache: Ordner „{folder}“ ließ sich nicht löschen: {ex}", user=user)
        db.ex("DELETE FROM winget_cache WHERE id=?", (eid,))
        pklog.info("winget", f"winget-Cache: „{e['winget_id']}“ entfernt (Paket und Ordner{'' if gone else ' – Ordner noch vorhanden, siehe Aufräumen'}).",
                   user=user)
        return {"ok": True, "folder_removed": gone}
    finally:
        _elock.release()


def check_package_delete(pid):
    """Vor dem Löschen eines Pakets aus der Paketliste: Ist es ein Cache-Paket, dessen Ordner noch eine geplante oder
    laufende Bereitstellung braucht? Dann nicht löschen (sonst fehlten ihr die Dateien)."""
    for e in db.q("SELECT * FROM winget_cache WHERE package_id=?", (pid,)):
        try:
            busy = _users(f"{folder_root()}\\{_safe_dir(e['winget_id'])}", pid)
        except CacheError:
            continue
        if busy:
            raise CacheError(f"Paket aus dem winget-Cache: Ordner wird noch benutzt ({busy}) – bitte danach löschen.")


def package_deleted(pid, user=None):
    """Aus der Paketliste gelöscht: Cache-Eintrag und Ordner mit wegräumen."""
    for e in db.q("SELECT id FROM winget_cache WHERE package_id=?", (pid,)):
        try:
            remove(e["id"], user, package_gone=True)
        except Exception as ex:  # pragma: no cover
            pklog.warn("winget", f"winget-Cache: Aufräumen nach dem Löschen des Pakets fehlgeschlagen: {ex}", user=user)


def _list_dir(rel):
    try:
        return remote.repo_list(rel)
    except Exception:
        return []


def _cleanup_versions(e, keep, own_pid):
    """Ältere Versionsordner dieses Programms löschen – nicht, solange etwas anderes sie noch braucht (geplante oder
    laufende Bereitstellung, anderes Paket)."""
    base = f"{folder_root()}\\{_safe_dir(e['winget_id'])}"
    left = []
    for it in _list_dir(base):
        if not it.get("dir") or it["name"].lower() == keep.lower() or it.get("link"):
            continue
        rel = f"{base}\\{it['name']}"
        if _users(rel, own_pid):
            left.append(it["name"])
            continue
        try:
            remote.repo_delete(rel)
            remote.repo_cache_apply("remove", rel)
        except Exception as ex:
            left.append(it["name"])
            pklog.warn("winget", f"winget-Cache: alte Version „{rel}“ ließ sich nicht löschen: {ex}")
    return left


def _save_package(e, man, p, folder, file):
    from . import deploy, integrity, steps
    name = str(man.get("PackageName") or e["name"] or e["winget_id"])
    step = {"type": "install", "source": "file", "folder": folder, "install_file": file, "install_type": p["install_type"],
            "arguments": p["arguments"], "copy_folder": True, "detect_name": p["detect_name"],
            "detect_version": p["detect_version"], "product_code": p["product_code"], "success_codes": p["success_codes"],
            "title": f"{name} {man.get('PackageVersion')}"}
    data = {"name": f"{name} (winget-Cache)", "version": str(man.get("PackageVersion") or ""),
            "description": f"Automatisch aus dem winget-Katalog ({e['winget_id']}) – wird bei jeder neuen Version neu "
                           "erzeugt; Änderungen hier gehen dabei verloren.",
            "steps": {"pre": [], "main": [step], "post": []}, "reboot": "never", "rescan": 1, "run_as": "deploy"}
    v = steps.clean_package(data)
    v["type"] = "custom"
    v["winget_id"] = e["winget_id"]  # für Zeitpläne: „läuft schon / zuletzt fehlgeschlagen“ je winget-ID
    for k in ("steps", "conditions"):
        if not isinstance(v.get(k), str):
            v[k] = json.dumps(v.get(k))
    pid = e["package_id"]
    if pid and not db.val("SELECT 1 FROM packages WHERE id=?", (pid,)):
        return None  # Paket wurde gerade gelöscht (Paketliste) – nicht neu anlegen, der Eintrag wird mit entfernt
    if pid:
        db.ex(f"UPDATE packages SET {', '.join(k + '=?' for k in deploy.PKG_FIELDS)}, updated_at=? WHERE id=?",
              tuple(v.get(k) for k in deploy.PKG_FIELDS) + (util.now(), pid))
    else:
        cols = deploy.PKG_FIELDS + ["created_at", "updated_at"]
        pid = db.ex(f"INSERT INTO packages({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
                    tuple(v.get(k) for k in deploy.PKG_FIELDS) + (util.now(), util.now()))
    integrity.after_save(pid)
    return pid


def refresh_entry(eid, force=False, user=None):
    """Eine winget-ID prüfen und bei Bedarf die neue Version in den Cache laden. Gibt den Eintrag zurück (None, wenn er
    inzwischen entfernt wurde)."""
    with _elock:
        e = entry(eid)
        if not e:
            return None
        now = util.now()
        db.ex("UPDATE winget_cache SET status='checking', message=NULL WHERE id=?", (eid,))
        try:
            root = folder_root()
            man = fetch_manifest(e["winget_id"])
            inst = choose(man, e["arch"] or "x64", e["scope"] or "machine")
            p = plan(man, inst, e["scope"] or "machine")
            ver = str(man.get("PackageVersion") or "")
            sha = str(inst.get("InstallerSha256") or "").lower()
            if not re.match(r"^[0-9a-f]{64}$", sha):
                raise CacheError("Im Manifest fehlt die Prüfsumme des Installers.")
            # Ordner je Version UND Prüfsumme: tauscht der Katalog bei gleicher Version die Datei, entsteht ein neuer
            # Ordner – der bestätigte Stand der alten Datei (Bruchprüfung) bleibt stimmig
            vdir = f"{_safe_dir(ver)}_{sha[:8]}"
            folder = f"{root}\\{_safe_dir(e['winget_id'])}\\{vdir}"
            file = _file_name(inst.get("InstallerUrl"), e["winget_id"], ver, p["install_type"])
            target = f"{folder}\\{file}"
            exists = remote.repo_stat(target) == "file"
            msg = []
            size = e["size"]
            if not exists or force:
                mx = max(16, int(db.setting("winget_cache_max_mb") or 2048)) * 1024 * 1024
                tmp, size = _download(inst.get("InstallerUrl"), sha, mx)
                try:
                    same = False
                    if exists:
                        try:
                            same = (remote.repo_sha256(target) or "").lower() == sha
                        except Exception:
                            same = False
                    if exists and not same and _users(folder, e["package_id"]):
                        raise CacheError("Die Datei im Cache ist beschädigt, wird aber gerade von einer Bereitstellung "
                                         "benutzt – nächster Versuch beim nächsten Lauf.")
                    if not same:
                        remote.repo_mkdir(folder)
                        part = f"{target}.pk-part"
                        remote.repo_delete(part)
                        with open(tmp, "rb") as src, remote.repo_open_write(part, "wb") as dst:
                            while True:
                                b = src.read(1024 * 1024)
                                if not b:
                                    break
                                dst.write(b)
                        remote.repo_delete(target)
                        remote.repo_rename(part, target)
                        remote.repo_cache_apply("add", target, size=size)
                finally:
                    os.remove(tmp)
            if not entry(eid):
                return None
            pid = _save_package({**e, "name": man.get("PackageName") or e["name"]}, man, p, folder, file)
            if pid is None:
                # Paket wurde während des Ladens in der Paketliste gelöscht → Eintrag und Ordner mit weg
                remove(eid, user, package_gone=True)
                return None
            left = _cleanup_versions(e, vdir, pid)
            if left:
                msg.append("alte Version wird noch verwendet, wird beim nächsten Lauf gelöscht: " + ", ".join(left))
            msg += p["hints"]
            db.ex("""UPDATE winget_cache SET status='ok', message=?, name=?, version=?, detect_version=?, folder=?, file=?,
                     size=?, sha256=?, installer_type=?, min_os=?, package_id=?, checked_at=?,
                     updated_at=CASE WHEN COALESCE(version, '') = ? THEN updated_at ELSE ? END WHERE id=?""",
                  ("; ".join(msg) or None, man.get("PackageName") or e["name"], ver, p["detect_version"] or ver, folder, file,
                   size, sha, p["type"], p["min_os"] or None, pid, now, ver, now, eid))
            if not exists or (e["version"] != ver):
                pklog.info("winget", f"winget-Cache: {e['winget_id']} {ver} geladen ({(size or 0) // 1048576} MB, {p['type']}) – "
                                     f"Paket „{man.get('PackageName') or e['winget_id']} (winget-Cache)“ aktualisiert.", user=user)
        except CacheError as ex:
            st = "unsupported" if str(ex).startswith(("Kein passender Installer", "Der Installer (EXE)", "Das Manifest enthält keinen")) else "error"
            db.ex("UPDATE winget_cache SET status=?, message=?, checked_at=? WHERE id=?", (st, str(ex)[:500], now, eid))
            if st == "error":
                pklog.warn("winget", f"winget-Cache: {e['winget_id']}: {ex}", user=user)
        except Exception as ex:
            db.ex("UPDATE winget_cache SET status='error', message=?, checked_at=? WHERE id=?",
                  (proxy.safe_error(ex)[:500], now, eid))
            pklog.warn("winget", f"winget-Cache: {e['winget_id']}: {proxy.safe_error(ex)}", user=user)
        return entry(eid)


def refresh_all(user=None, ids=None, force=False):
    """Alle (bzw. die gewählten) Einträge nacheinander prüfen – immer nur ein Lauf gleichzeitig. Einträge, die während
    des Laufs aufgenommen werden (Status „neu“), werden am Ende mitgenommen."""
    if not _lock.acquire(blocking=False):
        return False
    _state["running"] = True
    done = set()
    try:
        while True:
            todo = [e for e in entries() if e["id"] not in done
                    and ((not ids or e["id"] in ids) if not done else e["status"] == "new")]
            if not todo:
                break
            for e in todo:
                done.add(e["id"])
                _state["current"] = e["winget_id"]
                refresh_entry(e["id"], force=force, user=user)
        return True
    finally:
        _state.update(running=False, current=None)
        _lock.release()


def status():
    return {"running": _state["running"], "current": _state["current"]}


def orphans():
    """Ordner im Cache-Ordner, zu denen kein Eintrag (mehr) gehört und die nichts anderes benutzt."""
    known = {_safe_dir(e["winget_id"]).lower() for e in entries()}
    root = folder_root()
    return [it["name"] for it in _list_dir(root) if it.get("dir") and not it.get("link") and it["name"].lower() not in known
            and not it["name"].startswith(".") and not _users(f"{root}\\{it['name']}")]


def cleanup(user=None):
    """Verwaiste Ordner im Cache-Ordner löschen und alte Versionen nachholen – nie außerhalb des Cache-Ordners und nie,
    was ein Paket oder eine Bereitstellung benutzt."""
    root = folder_root()
    gone = []
    for n in orphans():
        rel = f"{root}\\{n}"
        try:
            remote.repo_delete(rel)
            remote.repo_cache_apply("remove", rel)
            gone.append(n)
        except Exception as ex:
            pklog.warn("winget", f"winget-Cache: „{rel}“ ließ sich nicht löschen: {ex}", user=user)
    for e in entries():
        if e["folder"] and e["package_id"]:
            _cleanup_versions(e, e["folder"].rsplit("\\", 1)[-1], e["package_id"])
    if gone:
        pklog.info("winget", f"winget-Cache aufgeräumt: {', '.join(gone)}.", user=user)
    return gone


def set_folder(value, user=None):
    v = _norm_folder(value)
    if not v or ".." in v.split("\\") or ":" in v:
        raise CacheError("Ordner im Repository angeben, z. B. winget-cache.")
    from . import repofiles, tools
    for part in v.split("\\"):
        repofiles.valid_name(part)
    bad = tools.tenant_conflict(v)
    if bad:
        raise CacheError(f"„{v}“ gehört zum Mandanten „{bad}“ – der Cache braucht einen eigenen Ordner.")
    if v.lower() == folder_root().lower():
        return v
    if db.val("SELECT COUNT(*) FROM winget_cache", default=0):
        raise CacheError("Der Ordner lässt sich nur ändern, solange der Cache leer ist – zuerst alle Einträge entfernen.")
    kind = remote.repo_stat(v)
    if kind == "file" or (kind == "dir" and _list_dir(v)):
        raise CacheError(f"„{v}“ gibt es schon und ist nicht leer – der Cache braucht einen eigenen, neuen Ordner "
                         "(beim Aufräumen löscht er dort alles, was zu keinem Eintrag gehört).")
    if _users(v):
        raise CacheError(f"„{v}“ wird schon benutzt – der Cache braucht einen eigenen Ordner.")
    db.set_setting("winget_cache_folder", v)
    pklog.info("winget", f"winget-Cache: Ordner „{v}“.", user=user)
    return v


# --- Für Computer ohne winget ---------------------------------------------------------------------
def ready():
    """{winget-id klein: Eintrag} – Paket aus dem Cache bereit (auch während einer Prüfung oder nach einem Fehler beim
    Laden einer neueren Version: dann gilt die bisherige)."""
    out = {}
    for e in db.q("SELECT * FROM winget_cache WHERE package_id IS NOT NULL AND COALESCE(version, '') != ''"):
        if db.val("SELECT 1 FROM packages WHERE id=?", (e["package_id"],)):
            out[e["winget_id"].lower()] = e
    return out


def os_ok(c, min_os):
    """Erfüllt der Computer die Mindestversion des Installers (MinimumOSVersion, z. B. 10.0.17763.0)? Unbekannt → ja."""
    need = _ver_tuple(min_os)
    mm = _ver_tuple(c.get("os_version"))[:2]
    b = _ver_tuple(str(c.get("os_build") or "").split(".")[0])
    if not need or len(mm) < 2 or not b:
        return True
    return (mm[0], mm[1], b[0]) >= tuple((list(need) + [0, 0, 0])[:3])


def _arch_ok(c, sw_rows, arch):
    """32-Bit-Windows nur x86; installiertes x86-Programm nicht durch x64 ersetzen (und umgekehrt); Installation nur
    für einen Benutzer nicht durch eine für den Computer."""
    osa = str(c.get("os_arch") or "").lower()
    if "32" in osa and "64" not in osa and arch != "x86":
        return False
    archs = {str(r.get("arch") or "").lower() for r in sw_rows if r.get("arch")}
    if archs and arch not in archs and not (arch == "x64" and archs <= {"x64", "amd64"}):
        return False
    scopes = {str(r.get("scope") or "").lower() for r in sw_rows}
    if scopes and scopes <= {"benutzer", "user"}:
        return False
    return True


def _newer(e, sw_rows):
    """Ist die Version im Cache neuer als die installierte? (sonst nichts zu tun – keine Leerläufe)"""
    inst = [str(r.get("version") or "") for r in sw_rows if r.get("version")]
    if not inst:
        return True
    have = inst[0]
    for v in inst[1:]:
        if util.vcmp(v, have) > 0:
            have = v
    cv = e.get("detect_version") or e.get("version") or ""  # Anzeigeversion (wie im Inventar), sonst Paketversion
    return util.vcmp(cv, have) > 0


def split(wid, ids):
    """Computer für ein Update dieser winget-ID aufteilen: (per winget, aus dem Cache, ausgelassen, Cache-Eintrag)."""
    from . import deploy
    ids = list(dict.fromkeys(ids))
    q = ",".join("?" * len(ids)) or "NULL"
    rows = {r["id"]: r for r in db.q(f"SELECT id, os_build, os_version, os_arch FROM computers WHERE id IN ({q})", tuple(ids))}
    e = ready().get(str(wid or "").lower())
    sw = {}
    if e:
        for r in db.q(f"""SELECT computer_id, version, arch, scope FROM software
                          WHERE lower(winget_id) = ? AND computer_id IN ({q})""", (str(wid).lower(), *ids)):
            sw.setdefault(r["computer_id"], []).append(r)
    wg, cache, skip = [], [], []
    for i in ids:
        c = rows.get(i) or {}
        if deploy.winget_ok(c.get("os_build")):
            wg.append(i)
        elif e and os_ok(c, e["min_os"]) and _arch_ok(c, sw.get(i, []), e["arch"] or "x64") and _newer(e, sw.get(i, [])):
            cache.append(i)
        else:
            skip.append(i)
    return wg, cache, skip, e


def package_for(e):
    from . import deploy
    row = db.q1("SELECT * FROM packages WHERE id=?", (e["package_id"],))
    if not row:
        raise CacheError("Paket aus dem winget-Cache fehlt.")
    return row, deploy.row_to_package(row)
