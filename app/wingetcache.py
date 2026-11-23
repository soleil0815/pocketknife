"""2.35: winget cache – the current version of selected winget packages is stored as a regular package in the repository.

For computers on which winget does not run (before Windows 10 1809 / Windows Server 2019): POCKET KNIFE fetches the manifest
for each winget ID in the list from the winget catalog (like winget itself: cdn.winget.microsoft.com, index v2), picks the
matching installer (architecture, scope, language), downloads it, verifies the SHA-256 from the manifest and stores it in the
repository (<folder>\\<winget ID>\\<version>\\). From that a package with silent switches, success codes and detection is
built. When a new version appears, it is downloaded, the package is switched over and the old version is deleted – the cache
only ever holds the current version of each program. Removing (from the list or by deleting the package) also cleans up the
folder and the package.

Updating via winget (software overview, computer view, schedule "winget-Updates") automatically uses the package from the cache
for computers without winget. The clients need neither winget nor internet for this.
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
# Silent switches per installer type, as winget uses them when the manifest names none. MSI: POCKET KNIFE calls
# msiexec itself with /qn /norestart – only properties here.
SILENT = {"inno": "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-", "nullsoft": "/S", "burn": "/quiet /norestart"}
SUPPORTED = ("msi", "wix", "inno", "nullsoft", "burn", "exe")
# Fields that apply at the top level of the manifest to all installers (the installer itself overrides them)
INSTALLER_KEYS = ("InstallerLocale", "Platform", "MinimumOSVersion", "InstallerType", "NestedInstallerType",
                  "NestedInstallerFiles", "Scope", "InstallerSwitches", "InstallerSuccessCodes", "ExpectedReturnCodes",
                  "ProductCode", "AppsAndFeaturesEntries", "Dependencies", "ElevationRequirement", "UpgradeBehavior",
                  "UnsupportedOSArchitectures")
# Return codes that count as success (ExpectedReturnCodes → ReturnResponse)
OK_RESPONSES = ("rebootRequiredToFinish", "rebootRequiredForInstall", "rebootInitiated", "alreadyInstalled")
MAX_YAML = 20 * 1024 * 1024
_lock = threading.Lock()       # only one run at a time (downloading into the repository)
_elocks = {}                   # 2.37: one lock per entry – checking/downloading and removing the same entry never happen at the same time
_elocks_guard = threading.Lock()  # (previously one lock for all: deleting waited up to 30 minutes for other downloads)


def _entry_lock(eid):
    with _elocks_guard:
        lk = _elocks.get(int(eid))
        if lk is None:
            lk = _elocks[int(eid)] = threading.RLock()
        return lk
_state = {"running": False, "current": None}
TMP_PREFIX = "pk-wc-"


class CacheError(ValueError):
    pass


def init():
    """The table is created by db.init (winget_cache); here: reset entries stuck in "wird geprüft" (checking) at startup and
    delete leftovers of aborted downloads."""
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
    """Cache folder in the repository – never empty (otherwise it would be the whole repository) and never containing ".."."""
    v = _norm_folder(db.setting("winget_cache_folder") or "winget-cache")
    if not v or ".." in v.split("\\") or ":" in v:
        raise CacheError("Der Ordner des winget-Caches ist ungültig (Einstellungen des winget-Caches).")
    return v


# --- MSZIP (Windows Compression API, buffer mode) -----------------------------------------------
def mszip_decompress(data, limit=MAX_YAML):
    """Unpack versionData.mszyml: 24-byte header (signature 0a51e5c01800, check byte, method 02 = MSZIP, unpacked size,
    size of the first chunk), then chunks: 4-byte length, "CK", deflate data – each chunk uses everything unpacked so far
    as its dictionary."""
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
    """SafeLoader without conversion to numbers/booleans: versions such as 1.10 or 2.50 stay text (as in winget)."""
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
        return yaml.load(text, Loader=_loader())  # noqa: S506 – SafeLoader subclass
    except yaml.YAMLError as e:
        raise CacheError(f"Manifest nicht lesbar: {str(e).splitlines()[0]}")


# --- Downloading ----------------------------------------------------------------------------
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


def _download(url, sha, max_bytes, verify=True):
    """Download the installer to a temporary file and check the SHA-256 (verify=False: only compute it – entry with
    "Prüfsumme des Katalogs nicht verlangen"; then HTTPS only, also after redirects). Returns (path, size, sha256)."""
    if not re.match(r"^https?://", url or "", re.I):
        raise CacheError("Download-Adresse fehlt oder ist ungültig.")
    if not verify and not re.match(r"^https://", url, re.I):
        raise CacheError("Ohne Prüfsumme des Katalogs nur über HTTPS – die Adresse im Manifest ist http.")
    fd, tmp = tempfile.mkstemp(dir=config.WINGET_DIR, prefix=TMP_PREFIX, suffix=".part")
    os.close(fd)
    h, size = hashlib.sha256(), 0
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "POCKETKNIFE/" + config.VERSION})
        with proxy.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
            if not verify and not str(r.geturl() or "").lower().startswith("https://"):
                raise CacheError("Ohne Prüfsumme des Katalogs nur über HTTPS – der Hersteller leitet auf http weiter.")
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
        if verify and (not sha or got.lower() != str(sha).lower()):
            raise CacheError(f"Prüfsumme stimmt nicht (Manifest {str(sha)[:16]}…, geladen {got[:16]}…) – der Hersteller "
                             "hat die Datei vermutlich ausgetauscht. Nicht übernommen; nächster Versuch beim nächsten Lauf. "
                             "Liefert der Hersteller unter derselben Adresse immer die neueste Datei (z. B. Google Chrome): "
                             "Rechtsklick → „Prüfsumme des Katalogs nicht verlangen“.")
        return tmp, size, got
    except CacheError:
        os.remove(tmp)
        raise
    except Exception as e:
        os.remove(tmp)
        raise CacheError(f"Download fehlgeschlagen: {proxy.safe_error(e)}")


def _head_sig(url):
    """Fingerprint of the file at the URL (ETag, otherwise date + size) – so that URLs without a version are not
    downloaded again every day. "" = unknown (then it is downloaded and compared)."""
    try:
        if not re.match(r"^https://", url or "", re.I):
            return ""
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "POCKETKNIFE/" + config.VERSION})
        with proxy.urlopen(req, timeout=30) as r:
            if not str(r.geturl() or "").lower().startswith("https://"):
                return ""
            h = r.headers
            et = (h.get("ETag") or "").strip()
            lm, ln = (h.get("Last-Modified") or "").strip(), (h.get("Content-Length") or "").strip()
            return (f"etag:{et}" if et else f"lm:{lm}|{ln}" if lm and ln else "")[:300]
    except Exception:
        return ""


def _zip_members(path, max_bytes):
    """Check the entries of a ZIP: only relative paths with valid Windows names, count and total size limited."""
    import zipfile
    from . import repofiles
    out = []
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise CacheError("Die geladene Datei ist kein gültiges ZIP.")
    with z:
        infos = z.infolist()
        if len(infos) > 5000:
            raise CacheError("Das ZIP enthält zu viele Dateien.")
        total, seen = 0, {}
        for i in infos:
            name = i.filename.replace("\\", "/")
            parts = [x for x in name.split("/") if x]
            if not parts or name.startswith("/") or ":" in name or ".." in parts:
                raise CacheError(f"Ungültiger Pfad im ZIP: {i.filename[:80]}")
            try:
                for x in parts:
                    repofiles.valid_name(x)
            except Exception:
                raise CacheError(f"Ungültiger Dateiname im ZIP: {i.filename[:80]}")
            total += i.file_size
            if total > max_bytes * 2:
                raise CacheError("Das ZIP ist entpackt unplausibel groß.")
            rel = "\\".join(parts)
            other = seen.get(rel.lower())
            if other is not None and other != rel:
                raise CacheError(f"Das ZIP enthält Namen, die sich nur in der Groß-/Kleinschreibung unterscheiden: {rel[:80]}")
            seen[rel.lower()] = rel
            out.append((i, rel, i.is_dir()))
    return out


def _put_zip(tmp, folder, max_bytes):
    """Extract the ZIP into the repository – first into "<folder>.pk-part", rename at the end (never visible half-finished)."""
    import zipfile
    members = _zip_members(tmp, max_bytes)
    part = f"{folder}.pk-part"
    remote.repo_delete(part)
    remote.repo_mkdir(part)
    try:
        _unzip_to(tmp, members, part)
    except BaseException:
        try:
            remote.repo_delete(part)  # do not leave a half-finished folder (possibly several GB) behind
        except Exception:
            pass
        raise
    remote.repo_delete(folder)
    remote.repo_rename(part, folder)
    remote.repo_cache_apply("add", folder, isdir=True)
    remote.repo_cache_refresh()
    return [rel for _i, rel, isdir in members if not isdir]


def _unzip_to(tmp, members, part):
    import zipfile
    with zipfile.ZipFile(tmp) as z:
        for info, rel, isdir in members:
            if isdir:
                remote.repo_mkdir(f"{part}\\{rel}")
                continue
            if "\\" in rel:
                remote.repo_mkdir(f"{part}\\{rel.rsplit(chr(92), 1)[0]}")
            with z.open(info) as src, remote.repo_open_write(f"{part}\\{rel}", "wb") as dst:
                while True:
                    b = src.read(1024 * 1024)
                    if not b:
                        break
                    dst.write(b)


def _put_file(tmp, folder, name):
    """The downloaded file unchanged into the cache folder (via ".pk-part" - never visible half-finished)."""
    remote.repo_mkdir(folder)
    target = f"{folder}\\{name}"
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
    remote.repo_cache_apply("add", target, size=os.path.getsize(tmp))
    return name


def _put_zip_into(tmp, inst_dir, nested, max_bytes):
    """Extract the ZIP into the folder "installer" and check that the installer named in the manifest is in it.
    Returns its path as spelled in the ZIP (the repository in the container is case-sensitive)."""
    files = _put_zip(tmp, inst_dir, max_bytes)
    real = {f.lower(): f for f in files}.get(nested.lower())
    if not real:
        remote.repo_delete(inst_dir)
        remote.repo_cache_apply("remove", inst_dir)
        raise CacheError(f"Die im Manifest genannte Datei „{nested}“ ist nicht im ZIP.")
    return real


def _repo_to_tmp(rel):
    """A file of the repository as a local temporary file (to extract an already stored ZIP again)."""
    fd, tmp = tempfile.mkstemp(dir=config.WINGET_DIR, prefix=TMP_PREFIX, suffix=".part")
    os.close(fd)
    try:
        with remote.repo_open_rel(rel) as src, open(tmp, "wb") as dst:
            while True:
                b = src.read(1024 * 1024)
                if not b:
                    break
                dst.write(b)
    except Exception as ex:
        os.remove(tmp)
        raise CacheError(f"Datei im Cache nicht lesbar: {ex}")
    return tmp


def _put_text(rel, text):
    """Small text file (the manifest YAML) - only written when the content changed."""
    data = text.encode("utf-8")
    try:
        if remote.repo_stat(rel) == "file":
            with remote.repo_open_rel(rel) as f:
                if f.read(len(data) + 1) == data:
                    return False
    except Exception:
        pass
    part = rel + ".pk-part"
    remote.repo_delete(part)
    with remote.repo_open_write(part, "wb") as dst:
        dst.write(data)
    remote.repo_delete(rel)
    remote.repo_rename(part, rel)
    remote.repo_cache_apply("add", rel, size=len(data))
    return True


def _pkg_broken(pid):
    """Integrity state of the cache package: break or deviation?"""
    if not pid:
        return False
    try:
        st = json.loads(db.val("SELECT integrity FROM packages WHERE id=?", (pid,)) or "{}").get("state")
    except (ValueError, TypeError):
        return False
    return st in ("broken", "warn")


def _confirm_own(pid):
    """The cache package's files as they are now = confirmed state ("winget-Cache"), then check again."""
    from . import deploy, integrity
    row = db.q1("SELECT * FROM packages WHERE id=?", (pid,))
    if not row:
        return
    try:
        integrity.accept(deploy.row_to_package(row), "winget-Cache", pid=pid)
    except Exception as ex:
        pklog.warn("winget", f"winget-Cache: Stand von „{row['name']}“ ließ sich nicht bestätigen: {ex}")
        return
    integrity.after_save(pid)


def _tidy(folder, keep, own_pid):
    """Files of the layout before 2.55.0 in the version folder (extracted ZIP content, installer under its old name):
    delete once nothing uses the folder any more. Returns what is left."""
    left: list = []
    items = [it for it in _list_dir(folder) if str(it["name"]).lower() not in keep and not str(it["name"]).endswith(".pk-part")]
    if not items:
        return left
    if _users(folder, own_pid):
        return [it["name"] for it in items]
    for it in items:
        rel = f"{folder}\\{it['name']}"
        try:
            remote.repo_delete(rel)
            remote.repo_cache_apply("remove", rel)
        except Exception:
            left.append(it["name"])
    return left


def index_entry(wid):
    """(id, name, latest_version, hash-hex) from the winget index (format v2) – or CacheError."""
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


def fetch_manifest(wid, version=None):
    """Manifest of the latest (or the named) version (dict) – like winget: versionData.mszyml, then the manifest,
    both with checksum."""
    pid, name, latest, hx = index_entry(wid)
    vd_raw = _get(f"{BASE}/packages/{urllib.parse.quote(pid, safe='+.-_')}/{hx[:8]}/versionData.mszyml", 5 * 1024 * 1024)
    if hashlib.sha256(vd_raw).hexdigest() != hx:
        raise CacheError("Versionsliste: Prüfsumme stimmt nicht – Index evtl. veraltet (Index neu laden).")
    vd = _yaml(mszip_decompress(vd_raw).decode("utf-8", "replace")) or {}
    vers = [v for v in (vd.get("vD") or []) if isinstance(v, dict) and v.get("v") and v.get("rP") and v.get("s256H")]
    if not vers:
        raise CacheError("Versionsliste leer.")
    if version:
        # 2.36: approved (older) version instead of the latest
        pick = next((v for v in vers if str(v["v"]) == str(version)), None)
        if not pick:
            raise CacheError(f"Version {version} steht nicht mehr im winget-Katalog.")
    else:
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


# --- Choosing the installer ---------------------------------------------------------------------------
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
    """Matching installer from the manifest – or CacheError with the reason."""
    insts = [_merged(man, i) for i in (man.get("Installers") or []) if isinstance(i, dict)]
    if not insts:
        raise CacheError("Das Manifest enthält keinen Installer.")
    locale = (locale or db.setting("winget_cache_locale") or "de-DE").lower()
    found_types = sorted({str(i.get("InstallerType") or "").lower() for i in insts})
    cand = []
    for i in insts:
        typ = str(i.get("InstallerType") or "").lower()
        a = str(i.get("Architecture") or "").lower()
        if typ == "zip":
            # 2.35.3: ZIP with an installer inside (NestedInstallerType/-Files) – "portable" (copy only) is not
            nt = str(i.get("NestedInstallerType") or "").lower()
            nf = [x for x in (i.get("NestedInstallerFiles") or []) if isinstance(x, dict) and x.get("RelativeFilePath")]
            if nt not in SUPPORTED or not nf:
                continue
        elif typ not in SUPPORTED or i.get("NestedInstallerType"):
            continue
        plat = [str(x) for x in (i.get("Platform") or [])]
        if plat and "Windows.Desktop" not in plat:
            continue
        order = ARCH_ORDER.get(arch, (arch, "neutral"))
        if a not in order:
            continue
        sc = str(i.get("Scope") or "").lower()
        if sc and sc != scope:
            continue
        if str(i.get("ElevationRequirement") or "") == "elevationProhibited":
            continue  # must not run with administrator rights – unsuitable as SYSTEM
        cand.append(i)
    if not cand:
        what = ", ".join(t for t in found_types if t) or "unbekannt"
        raise CacheError(f"Kein passender Installer ({ARCHS.get(arch, arch)}, für alle Benutzer, Typ MSI/EXE). Im Manifest: {what}.")
    # 2.55.0: the same installer as winget - its selection rebuilt (winget-cli ManifestComparator::GetPreferredInstaller):
    # go through the fitting installers in manifest order, a later one replaces the best so far only if it is better.
    # Before: no installer type order - Paint.NET's setup EXE (listed first) instead of the MSI winget takes
    best = cand[0]
    for i in cand[1:]:
        if _wg_better(i, best, [locale], ARCH_ORDER.get(arch, (arch, "neutral"))):
            best = i
    return best


# Architectures a computer can run, best first (winget: own architecture, x86 under x64 emulation, neutral)
# (ARM64: winget also takes x64/x86 there if the emulation allows it - the cache stays with ARM64 and neutral, it cannot
# check the emulation of every computer)
ARCH_ORDER = {"x64": ("x64", "x86", "neutral"), "x86": ("x86", "neutral"), "arm64": ("arm64", "neutral"), "arm": ("arm", "neutral")}


def _lang_score(pref, loc):
    """winget's distance of two languages (Windows BCP-47): 1 equal, close to 1 same language, 0 other or unknown."""
    pref, loc = str(pref or "").lower(), str(loc or "").lower()
    if not loc or not pref:
        return 0.0
    if pref == loc:
        return 1.0
    return 0.95 if pref.split("-")[0] == loc.split("-")[0] else 0.0


def _wg_better(first, second, prefs, arch_order):
    """winget's IsFirstBetter: comparators language, architecture, installer type; the first strong result decides,
    otherwise the first weak one (scope only filters here - "for all users" is a requirement, no preference)."""
    def loc(a, b):
        la, lb = a.get("InstallerLocale"), b.get("InstallerLocale")
        for pr in prefs:
            fa, fb = _lang_score(pr, la), _lang_score(pr, lb)
            if fa >= 0.9 or fb >= 0.9:
                return "S" if fa > fb else ""
        return "W" if not la and lb else ""

    def arc(a, b):
        def prio(x):
            x = str(x.get("Architecture") or "").lower()
            return len(arch_order) - arch_order.index(x) if x in arch_order else 0
        if prio(a) > prio(b):
            return "S" if str(a.get("Architecture") or "").lower() == arch_order[0] else "W"
        return ""

    def typ(a, b):
        ta = {effective_type(a), str(a.get("InstallerType") or "").lower()}
        tb = {effective_type(b), str(b.get("InstallerType") or "").lower()}
        for t in TYPE_ORDER:
            if t in ta and t in tb:
                return ""
            if (t in ta) != (t in tb):
                return "W" if t in ta else ""
        return ""

    weak = None
    for cmp in (loc, arc, typ):
        f, r = cmp(first, second), cmp(second, first)
        if f == "S":
            return True
        if r == "S":
            return False
        if weak is None and (f == "W" or r == "W"):
            weak = f == "W"
    return bool(weak)


# winget's default precedence of installer types (no user setting): Store, MSIX, MSI, WiX, Burn, Nullsoft, Inno, EXE, Portable
TYPE_ORDER = ("msstore", "msix", "msi", "wix", "burn", "nullsoft", "inno", "exe", "portable")


def effective_type(inst):
    """Installer type as winget sees it: for a ZIP the type of the installer inside."""
    typ = str(inst.get("InstallerType") or "").lower()
    return str(inst.get("NestedInstallerType") or "").lower() if typ == "zip" else typ


def type_rank(inst):
    t = effective_type(inst)
    return TYPE_ORDER.index(t) if t in TYPE_ORDER else len(TYPE_ORDER)


ARCH_NAMES = {"x86": "X86", "x64": "X64", "arm": "Arm", "arm64": "Arm64", "neutral": "Neutral"}


def download_name(man, inst, ext=None):
    """File name like "winget download": {name}_{version}_{scope}_{architecture}_{installer type}_{language}{.ext}
    (winget-cli DownloadFlow) - e.g. "paint.net_5.2_Machine_X64_wix_en-US.zip"."""
    parts = [str(man.get("PackageName") or man.get("PackageIdentifier") or "installer")]
    if str(man.get("PackageVersion") or ""):
        parts.append(str(man["PackageVersion"]))
    sc = str(inst.get("Scope") or "").lower()
    if sc in ("machine", "user"):
        parts.append(sc.capitalize())
    a = str(inst.get("Architecture") or "").lower()
    parts += [ARCH_NAMES.get(a, a or "Neutral"), effective_type(inst) or "unknown"]
    loc = str(inst.get("InstallerLocale") or man.get("PackageLocale") or "")
    if loc:
        parts.append(loc)
    if ext is None:
        base = str(inst.get("InstallerType") or "").lower()
        ext = ".zip" if base == "zip" else ".msi" if base in ("msi", "wix") else ".msix" if base in ("msix", "appx") else ".exe"
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", "_".join(parts)).strip(" .")
    return name[:180] + ext


def manifest_yaml(man, inst):
    """The manifest like "winget download" writes it next to the installer: everything of the package, only the chosen
    installer (with the values inherited from the top level)."""
    import yaml
    d = {k: v for k, v in man.items() if k != "Installers"}
    d["Installers"] = [inst]
    d = json.loads(json.dumps(d, default=str))  # dates and other special values as text
    head = ("# Manifest wie bei „winget download“ – nur der gewählte Installer. Erzeugt vom winget-Cache von POCKET KNIFE;\n"
            "# die Datei daneben ist unverändert die vom Hersteller (Prüfsumme siehe InstallerSha256).\n")
    return head + yaml.safe_dump(d, sort_keys=False, allow_unicode=True, default_flow_style=False, width=200)


def plan(man, inst, scope="machine"):
    """Installation step from manifest + installer: type, switches, success codes, detection, notes."""
    typ = str(inst.get("InstallerType") or "").lower()
    nested = ""
    if typ == "zip":
        nf = [x for x in (inst.get("NestedInstallerFiles") or []) if isinstance(x, dict) and x.get("RelativeFilePath")]
        nested = "\\".join(x for x in str(nf[0]["RelativeFilePath"]).replace("/", "\\").split("\\") if x not in ("", "."))
        if not nested or ".." in nested.split("\\") or ":" in nested:
            raise CacheError("Ungültige Angabe der Datei im ZIP (NestedInstallerFiles).")
        typ = str(inst.get("NestedInstallerType") or "").lower()
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
    # Replace the version in the display name (e.g. "7-Zip 24.09 (x64 edition)") with * – otherwise detection would never find older ones
    detect = re.sub(r"\s*\bv?\d+(\.\d+)+\S*", " *", dn).strip()
    detect = re.sub(r"(\s*\*)+", " *", detect).strip()
    detect = detect.replace("[", "?").replace("]", "?")  # [ ] would be wildcards under -like
    dver = str((arp[0].get("DisplayVersion") if arp else "") or man.get("PackageVersion") or "").strip()
    deps = (inst.get("Dependencies") or {}) if isinstance(inst.get("Dependencies"), dict) else {}
    pk = [str(d.get("PackageIdentifier")) for d in (deps.get("PackageDependencies") or []) if isinstance(d, dict)]
    if pk:
        hints.append("braucht außerdem: " + ", ".join(pk[:5]) + " (ggf. ebenfalls in den Cache aufnehmen)")
    pc = str(inst.get("ProductCode") or (arp[0].get("ProductCode") if arp else "") or "").strip()
    return {"install_type": it, "arguments": args, "success_codes": ",".join(str(c) for c in codes),
            "detect_name": detect[:200], "detect_version": dver[:50], "product_code": pc[:60] if pc.startswith("{") else "",
            "min_os": str(inst.get("MinimumOSVersion") or "").strip(), "type": f"zip/{typ}" if nested else typ,
            "hints": hints, "nested": nested}


def _safe_dir(s):
    s = re.sub(r"[^A-Za-z0-9._+-]+", "_", str(s or "")).strip("._ ")
    return s[:100] or "_"


# --- Entries ------------------------------------------------------------------------------------
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
    """Where else is the cache package used? (schedules, rules, nested, kiosk) – list of texts."""
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
    """Who uses the folder besides its own cache package? (packages, scheduled/running deployments, tenants)"""
    from . import repofiles
    hits = [h for h in repofiles.usage(rel) if not (h[4][0] == "pkg" and own_pid and h[4][1] == own_pid)]
    return repofiles.usage_summary(hits) if hits else ""


def _lock_entry(eid, timeout=3):
    lk = _entry_lock(eid)
    if not lk.acquire(timeout=timeout):
        raise CacheError("Der Eintrag wird gerade geprüft bzw. geladen – bitte gleich noch einmal.")
    return lk


def remove(eid, user=None, package_gone=False):
    """Remove the entry and clean up everything related: folder in the repository and the generated package. package_gone: the package
    has just been deleted (package list) – only the folder and entry remain."""
    lk = _lock_entry(eid, 1800 if package_gone else 3)
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
        busy = _users(folder, own) or ("eine laufende Bereitstellung" if _runtime_busy(e["winget_id"]) else "")
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
        lk.release()
        if not entry(eid):
            with _elocks_guard:
                _elocks.pop(int(eid), None)


def check_package_delete(pid):
    """Before deleting a package from the package list: is it a cache package whose folder is still needed by a scheduled or
    running deployment? Then do not delete it (otherwise its files would be missing)."""
    for e in db.q("SELECT * FROM winget_cache WHERE package_id=?", (pid,)):
        try:
            busy = _users(f"{folder_root()}\\{_safe_dir(e['winget_id'])}", pid)
        except CacheError:
            continue
        if busy:
            raise CacheError(f"Paket aus dem winget-Cache: Ordner wird noch benutzt ({busy}) – bitte danach löschen.")


def package_deleted(pid, user=None):
    """Deleted from the package list: also clean up the cache entry and folder."""
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


def _runtime_busy(wid):
    """Is a deployment currently running that contains this winget ID? Its winget step may use the folder in the cache at runtime
    (step_for) – in that case delete old folders only on the next run."""
    w = str(wid or "").lower()
    return bool(w) and any(w in (r["package_json"] or "").lower()
                           for r in db.q("SELECT package_json FROM deployments WHERE status='running'"))


def _cleanup_versions(e, keep, own_pid):
    """Delete older version folders of this program – not while anything else still needs them (scheduled or running
    deployment, another package)."""
    base = f"{folder_root()}\\{_safe_dir(e['winget_id'])}"
    left = []
    if _runtime_busy(e["winget_id"]):
        return [it["name"] for it in _list_dir(base) if it.get("dir") and it["name"].lower() != keep.lower()]
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


def _same_steps(a, b):
    """Same steps, regardless of the random step IDs?"""
    def strip(x):
        try:
            d = json.loads(x) if isinstance(x, str) else (x or {})
        except ValueError:
            return x
        return {ph: [{k: v for k, v in st.items() if k != "id"} for st in (d.get(ph) or [])] for ph in ("pre", "main", "post")}
    return strip(a) == strip(b)


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
            "steps": {"pre": [], "main": [step], "post": []}, "reboot": "never", "rescan": 1, "run_as": "default"}
    v = steps.clean_package(data)
    v["type"] = "custom"
    v["winget_id"] = e["winget_id"]  # for schedules: "already running / last failed" per winget ID
    for k in ("steps", "conditions"):
        if not isinstance(v.get(k), str):
            v[k] = json.dumps(v.get(k))
    pid = e["package_id"]
    if pid and not db.val("SELECT 1 FROM packages WHERE id=?", (pid,)):
        return None  # package has just been deleted (package list) – do not recreate it, the entry is removed too
    if pid:
        old = db.q1("SELECT * FROM packages WHERE id=?", (pid,))
        if all(str(old.get(k) if old.get(k) is not None else "") == str(v.get(k) if v.get(k) is not None else "")
               for k in deploy.PKG_FIELDS if k != "steps") and _same_steps(old.get("steps"), v.get("steps")):
            return pid  # nothing changed – no "package changed" in deployments every day
        db.ex(f"UPDATE packages SET {', '.join(k + '=?' for k in deploy.PKG_FIELDS)}, updated_at=? WHERE id=?",
              tuple(v.get(k) for k in deploy.PKG_FIELDS) + (util.now(), pid))
    else:
        cols = deploy.PKG_FIELDS + ["created_at", "updated_at"]
        pid = db.ex(f"INSERT INTO packages({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
                    tuple(v.get(k) for k in deploy.PKG_FIELDS) + (util.now(), util.now()))
    integrity.after_save(pid)
    return pid


def refresh_entry(eid, force=False, user=None):
    """Check one winget ID and, if needed, download the new version into the cache. Returns the entry (None if it has
    been removed in the meantime)."""
    with _entry_lock(eid):
        e = entry(eid)
        if not e:
            return None
        now = util.now()
        db.ex("UPDATE winget_cache SET status='checking', message=NULL WHERE id=?", (eid,))
        try:
            root = folder_root()
            # 2.36: latest version not yet approved → download the approved one or keep the current state
            from . import wingetrelease
            rel = wingetrelease.target(e["winget_id"])
            if rel["state"] != "latest":
                # Audit 2026-11: no status condition - e was read before "checking" was set, and after one error
                # (e.g. "interrupted by restart") a held/pinned entry stayed in "error" forever; like ready(): the version counts
                have = e["version"] and e["folder"] and e["file"]
                if rel["state"] == "hold" or (have and str(e["version"]) == rel["version"]) or (have and e["ignore_hash"]):
                    if not have:
                        raise CacheError(f"{rel['reason']} – keine freigegebene Version zum Laden.")
                    keep = (f"{rel['reason']} – im Cache bleibt {e['version']}" if _released(e) else
                            f"{rel['reason']} – {e['version']} im Cache ist nicht freigegeben und wird nicht benutzt")
                    db.ex("UPDATE winget_cache SET status='ok', message=?, checked_at=? WHERE id=?", (keep, now, eid))
                    return entry(eid)
                if e["ignore_hash"]:
                    raise CacheError(f"{rel['reason']} – ohne Prüfsumme des Katalogs lädt der Hersteller immer die neueste, "
                                     "eine ältere Version kann nicht geladen werden.")
            man = fetch_manifest(e["winget_id"], rel.get("version") if rel["state"] == "pin" else None)
            inst = choose(man, e["arch"] or "x64", e["scope"] or "machine")
            p = plan(man, inst, e["scope"] or "machine")
            ver = str(man.get("PackageVersion") or "")
            url = inst.get("InstallerUrl")
            msha = str(inst.get("InstallerSha256") or "").lower()
            ign = bool(e["ignore_hash"])
            if not ign and not re.match(r"^[0-9a-f]{64}$", msha):
                raise CacheError("Im Manifest fehlt die Prüfsumme des Installers.")
            mx = max(16, int(db.setting("winget_cache_max_mb") or 2048)) * 1024 * 1024
            base = f"{root}\\{_safe_dir(e['winget_id'])}"
            msg = []
            size, sig = e["size"], e["remote_sig"]

            # 2.55.0: stored like "winget download": the file from the vendor unchanged (name as winget download gives
            # it, checksum = InstallerSha256 of the manifest) and the manifest YAML next to it. A ZIP is additionally
            # extracted into the subfolder "installer" - that is what goes to the computers (unpacking on the computer
            # with PowerShell's own Expand-Archive would load code PowerShell logs as a warning, tar.exe is missing on
            # older Windows). Before: only the extracted installer, under a name of its own
            orig = download_name(man, inst)
            yml = orig.rsplit(".", 1)[0] + ".yaml"

            def get_new():
                """Download (with or without the catalog checksum) -> (tmp, size, sha)."""
                if ign:
                    return _download(url, None, mx, verify=False)
                return _download(url, msha, mx)

            tmp = None
            try:
                if ign:
                    # Without the catalog checksum (URL without a version, e.g. Google Chrome): only download if the
                    # file at the URL has changed; remember the checksum of the downloaded file
                    sig = _head_sig(url)
                    keep = (not force and sig and sig == e["remote_sig"] and e["folder"] and e["sha256"]
                            and remote.repo_stat(f"{e['folder']}\\{orig}") == "file")
                    if keep:
                        sha, ver = e["sha256"], e["version"] or ver
                        p["detect_version"] = e["detect_version"] or p["detect_version"]
                    else:
                        tmp, size, sha = get_new()
                        if p["install_type"] == "msi" and not p["nested"]:
                            # Product code from the downloaded file (the one in the manifest belongs to an older one). The version
                            # stays the catalog one: the MSI ProductVersion differs from the display version for some vendors
                            try:
                                from . import msi
                                with open(tmp, "rb") as fh:
                                    info = msi.read(fh)
                                pc = str(info.get("product_code") or "").strip()
                                if pc.startswith("{"):
                                    p["product_code"] = pc[:60]
                                if info.get("version"):
                                    msg.append(f"MSI-Version der Datei: {str(info['version'])[:40]}")
                            except Exception:
                                pass
                    msg.append("ohne Prüfsumme des Katalogs (Datei vom Hersteller per HTTPS)")
                else:
                    sha = msha
                # Folder per version AND checksum: if the catalog/vendor swaps the file for the same version, a new folder
                # is created - the confirmed state of the old file (integrity check) stays consistent
                folder = f"{base}\\{_safe_dir(ver)}_{sha[:8]}"
                target = f"{folder}\\{orig}"
                exists = remote.repo_stat(target) == "file"
                own_pid = e["package_id"]
                changed = False  # 2.55.1: did this run change files in the version folder? -> confirm the new state
                heal = False
                if exists and not force and tmp is None and _pkg_broken(own_pid):
                    # 2.55.1: "Bruch" at the cache package (e.g. after the switch to the layout of 2.55.0, the old file
                    # name was still in the confirmed state): files from the verified original again, then confirm them
                    try:
                        heal = (remote.repo_sha256(target) or "").lower() == sha
                    except Exception:
                        heal = False
                    if not heal:
                        force = True  # the original itself differs: load it again from the vendor (below)
                if exists and force and tmp is None:
                    # "Neu laden": compare the stored file with the catalog, replace it if it is damaged
                    tmp, size, _got = get_new()
                    same = False
                    try:
                        same = (remote.repo_sha256(target) or "").lower() == sha
                    except Exception:
                        same = False
                    if not same and _users(folder, own_pid):
                        raise CacheError("Die Datei im Cache ist beschädigt, wird aber gerade von einer Bereitstellung "
                                         "benutzt – nächster Versuch beim nächsten Lauf.")
                    if not same:
                        _put_file(tmp, folder, orig)
                        changed = True
                    if p["nested"] and (not same or _pkg_broken(own_pid)):
                        remote.repo_delete(f"{folder}\\installer")  # extract again below
                elif not exists:
                    old = f"{e['folder']}\\{e['file']}" if e["folder"] and e["file"] else ""
                    if (tmp is None and not p["nested"] and e["folder"] == folder and old and "\\" not in str(e["file"])
                            and str(e["sha256"] or "").lower() == sha and remote.repo_stat(old) == "file"
                            and not _users(folder, own_pid) and not _runtime_busy(e["winget_id"])):
                        # same file from before 2.55.0 under its old name: only renamed, no new download
                        remote.repo_rename(old, target)
                        remote.repo_cache_apply("remove", old)
                        remote.repo_cache_apply("add", target, size=e["size"] or 0)
                    else:
                        if tmp is None:
                            tmp, size, _got = get_new()
                        _put_file(tmp, folder, orig)
                    changed = True
                if heal and p["nested"]:
                    remote.repo_delete(f"{folder}\\installer")  # extract again below from the verified original
                file = orig
                if p["nested"]:
                    inst_dir = f"{folder}\\installer"
                    if remote.repo_stat(f"{inst_dir}\\{p['nested']}") != "file":
                        if tmp is None:
                            tmp = _repo_to_tmp(target)
                        try:
                            file = "installer\\" + _put_zip_into(tmp, inst_dir, p["nested"], mx)
                            changed = True
                        except CacheError:
                            if not exists and not _users(folder, own_pid):
                                # nothing usable in it (path outside the folder, named file missing): no half version left
                                remote.repo_delete(folder)
                                remote.repo_cache_apply("remove", folder)
                            raise
                    else:
                        file = "installer\\" + p["nested"]
                changed = _put_text(f"{folder}\\{yml}", manifest_yaml(man, inst)) or changed or heal
            finally:
                if tmp:
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
            vdir = folder.rsplit("\\", 1)[-1]
            ver = str(ver)
            if not entry(eid):
                return None
            sfolder, sfile = (f"{folder}\\{file.rsplit(chr(92), 1)[0]}", file.rsplit(chr(92), 1)[1]) if chr(92) in file else (folder, file)
            pid = _save_package({**e, "name": man.get("PackageName") or e["name"]}, man, p, sfolder, sfile)
            if pid is None:
                # package was deleted from the package list during the download → remove entry and folder too
                remove(eid, user, package_gone=True)
                return None
            left = _cleanup_versions(e, vdir, pid)
            if not _runtime_busy(e["winget_id"]):
                before = len(_list_dir(folder))
                _tidy(folder, {orig.lower(), yml.lower(), "installer"}, pid)
                changed = changed or len(_list_dir(folder)) != before
            if changed:
                # 2.55.1: POCKET KNIFE itself put these files there (original checked against the catalog checksum, ZIP
                # content extracted from it) - they are the confirmed state of the cache package, no "Bruch". Before:
                # renaming kept the old name in the confirmed state of the folder -> "Datei fehlt" at every cache package
                _confirm_own(pid)
                if heal:
                    msg.append("Bruch behoben: Dateien aus dem geprüften Original wiederhergestellt und bestätigt")
            if left:
                msg.append("alte Version wird noch verwendet, wird beim nächsten Lauf gelöscht: " + ", ".join(left))
            msg += p["hints"]
            msg.insert(0, f"Installer wie bei winget: {orig} (vom Hersteller: {urllib.parse.urlsplit(url).netloc})")
            db.ex("""UPDATE winget_cache SET status='ok', message=?, name=?, version=?, detect_version=?, folder=?, file=?,
                     size=?, sha256=?, installer_type=?, min_os=?, package_id=?, checked_at=?, remote_sig=?, inst_arch=?,
                     updated_at=CASE WHEN COALESCE(version, '') = ? THEN updated_at ELSE ? END WHERE id=?""",
                  ("; ".join(msg) or None, man.get("PackageName") or e["name"], ver, p["detect_version"] or ver, folder, file,
                   size, sha, p["type"], p["min_os"] or None, pid, now, sig if ign else None,
                   str(inst.get("Architecture") or e["arch"] or "x64").lower(), ver, now, eid))
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
    """Check all (or the selected) entries one after another – only one run at a time. Entries added during
    the run (status "neu") are picked up at the end."""
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
    """Folders in the cache folder that no entry belongs to (any more) and that nothing else uses."""
    known = {_safe_dir(e["winget_id"]).lower() for e in entries()}
    root = folder_root()
    return [it["name"] for it in _list_dir(root) if it.get("dir") and not it.get("link") and it["name"].lower() not in known
            and not it["name"].startswith(".") and not _users(f"{root}\\{it['name']}")]


def cleanup(user=None):
    """Delete orphaned folders in the cache folder and catch up on old versions – never outside the cache folder and never
    anything a package or a deployment uses."""
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


def set_ignore_hash(eid, on, user=None):
    """Per entry: do not require the catalog checksum (for URLs without a version that always deliver the latest file)."""
    lk = _lock_entry(eid, 10)
    try:
        e = entry(eid)
        if not e:
            raise CacheError("Eintrag nicht gefunden.")
        # status "neu": a pass that is currently running picks up the entry at the end
        db.ex("UPDATE winget_cache SET ignore_hash=?, remote_sig=NULL, status='new' WHERE id=?", (1 if on else 0, eid))
    finally:
        lk.release()
    pklog.info("winget", f"winget-Cache: „{e['winget_id']}“ – Prüfsumme des Katalogs {'nicht mehr verlangt' if on else 'wieder verlangt'}.",
               user=user)


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


# --- For computers without winget ---------------------------------------------------------------------
def ready():
    """{winget-id lowercase: entry} – package ready from the cache (also during a check or after an error while
    downloading a newer version: then the previous one applies)."""
    return {e["winget_id"].lower(): e for e in db.q(
        """SELECT w.* FROM winget_cache w JOIN packages p ON p.id = w.package_id WHERE COALESCE(w.version, '') != ''""")}


def os_ok(c, min_os):
    """Does the computer meet the installer minimum version (MinimumOSVersion, e.g. 10.0.17763.0)? Unknown → yes."""
    need = _ver_tuple(min_os)
    mm = _ver_tuple(c.get("os_version"))[:2]
    b = _ver_tuple(str(c.get("os_build") or "").split(".")[0])
    if not need or len(mm) < 2 or not b:
        return True
    return (mm[0], mm[1], b[0]) >= tuple((list(need) + [0, 0, 0])[:3])


def _arch_ok(c, sw_rows, arch):
    """32-bit Windows only x86; do not replace an installed x86 program with x64 (and vice versa); do not replace an installation
    for one user only with one for the computer. arch: architecture of the downloaded installer (2.35.4 – a
    program that only exists as x86 is x86 in the x64 entry too)."""
    arch = (arch or "x64").lower()
    osa = str(c.get("os_arch") or "").lower()
    if "32" in osa and "64" not in osa and arch not in ("x86", "neutral"):
        return False
    archs = {str(r.get("arch") or "").lower() for r in sw_rows if r.get("arch")}
    if archs and arch != "neutral" and arch not in archs and not (arch == "x64" and archs <= {"x64", "amd64"}):
        return False
    scopes = {str(r.get("scope") or "").lower() for r in sw_rows}
    if scopes and scopes <= {"benutzer", "user"}:
        return False
    return True


def _newer(e, sw_rows):
    """Is the version in the cache newer than the installed one? (otherwise nothing to do – no idle runs)"""
    inst = [str(r.get("version") or "") for r in sw_rows if r.get("version")]
    if not inst:
        return True
    have = inst[0]
    for v in inst[1:]:
        if util.vcmp(v, have) > 0:
            have = v
    cv = e.get("detect_version") or e.get("version") or ""  # display version (as in the inventory), otherwise package version
    return util.vcmp(cv, have) > 0


def _released(e):
    """2.36: version in the cache blocked or not yet approved → do not use the cache."""
    try:
        from . import wingetrelease
        return wingetrelease.version_ok(e["winget_id"], e["version"])
    except Exception:
        return False  # 2.36.2: cannot be checked → do not use the cache (winget decides, or the step fails)


def split(wid, ids):
    """Split computers for an update of this winget ID: (via winget, from the cache, skipped, cache entry)."""
    from . import deploy
    ids = list(dict.fromkeys(ids))
    q = ",".join("?" * len(ids)) or "NULL"
    rows = {r["id"]: r for r in db.q(f"SELECT id, os_build, os_version, os_arch FROM computers WHERE id IN ({q})", tuple(ids))}
    e = ready().get(str(wid or "").lower())
    if e and not _released(e):
        e = None
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
        elif e and os_ok(c, e["min_os"]) and _arch_ok(c, sw.get(i, []), e["inst_arch"] or e["arch"] or "x64") and _newer(e, sw.get(i, [])):
            cache.append(i)
        else:
            skip.append(i)
    return wg, cache, skip, e


_guard_cache = {}   # (package_id, updated_at) → (time, ok) – integrity check not repeated for every target
GUARD_TTL = 120


def _guard_ok(row, pkg):
    from . import integrity
    import time as _t
    k = (row["id"], row.get("updated_at"))
    hit = _guard_cache.get(k)
    if hit and _t.time() - hit[0] < GUARD_TTL:
        return hit[1]
    try:
        integrity.guard(pkg, package_id=row["id"], name=row["name"])
        ok = True
    except Exception as ex:
        ok = False
        pklog.warn("winget", f"winget-Cache: „{row['name']}“ nicht benutzt – Datei im Repository geändert oder nicht "
                             f"lesbar ({str(ex)[:200]}). Bitte im winget-Cache „Neu laden“.")
    if len(_guard_cache) > 500:
        _guard_cache.clear()
    _guard_cache[k] = (_t.time(), ok)
    return ok


def step_for(st, c, pkg=None):
    """winget step (install/update) → the installation step from the winget cache, if it is available there
    and fits – otherwise None (then as before: winget, or without winget "Übersprungen").
    2.35.1: computers without winget. 2.35.2: also computers with winget (setting "winget_cache_all", default on) –
    faster from the LAN, downloaded once instead of per computer from the internet. Applies everywhere: packages, schedules,
    nested packages, kiosk, updating."""
    from . import deploy, steps
    act = st.get("winget_action") or "install"
    if st.get("type") != "install" or st.get("source") != "winget" or act == "uninstall":
        return None
    if st.get("winget_scope") == "user":
        return None  # installation for one user only: the cache installs for the computer
    if pkg is not None and steps.effective_run_as(st, pkg)[0] == "user":
        return None  # runs as the logged-on user – an installer for all users would need administrator rights
    has_wg = deploy.winget_ok((c or {}).get("os_build"))
    if has_wg and not db.setting("winget_cache_all"):
        return None
    if has_wg and str(st.get("extra_args") or "").strip():
        return None  # custom winget arguments: only winget can do that
    if has_wg and pkg is not None and pkg.get("release_bypass") and not st.get("winget_version"):
        from . import wingetrelease
        if wingetrelease.target(st.get("winget_id"))["state"] != "latest":
            return None  # 2.36: "Aktualisieren" by hand – the latest via winget, not the approved one from the cache
    e = ready().get(str(st.get("winget_id") or "").lower())
    if not e or not _released(e):
        return None
    if st.get("winget_version") and str(st["winget_version"]) != str(e["version"]):
        return None  # a specific other version is requested – the cache only has the current one
    sw = db.q("SELECT version, arch, scope FROM software WHERE computer_id=? AND lower(winget_id)=?",
              ((c or {}).get("id"), str(e["winget_id"]).lower()))
    if act == "upgrade" and not sw:
        return None  # "Aktualisieren" does not install anything new (like winget upgrade) – not installed: winget decides
    if not os_ok(c or {}, e["min_os"]) or not _arch_ok(c or {}, sw, e["inst_arch"] or e["arch"] or "x64"):
        return None
    if sw and not _newer(e, sw):
        return None  # installed version is not older – never downgrade (winget decides, or "Übersprungen")
    try:
        row, cpkg = package_for(e)
    except CacheError:
        return None
    if not _guard_ok(row, cpkg):  # file in the cache unchanged?
        return None
    base = next((x for _ph, x in steps.iter_steps(steps.normalize(cpkg)) if x.get("type") == "install"), None)
    if not base:
        return None
    return {**base, "id": st.get("id"), "enabled": True, "on_error": st.get("on_error", "stop"),
            "conditions": st.get("conditions") or base.get("conditions"), "run_as": st.get("run_as") or "inherit",
            "timeout_min": st.get("timeout_min") or 0,
            "title": f"{steps.step_title(st)} – aus dem winget-Cache ({e['version']})"}


def package_for(e):
    from . import deploy
    row = db.q1("SELECT * FROM packages WHERE id=?", (e["package_id"],))
    if not row:
        raise CacheError("Paket aus dem winget-Cache fehlt.")
    return row, deploy.row_to_package(row)
