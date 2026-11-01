"""2.37: App-Installer (winget) für Computer ohne winget – POCKET KNIFE lädt ihn EINMAL in das Repository.

Bisher lud jeder Client ohne Offline-Kopie die Dateien selbst von github.com („latest“, ohne Prüfsumme). Jetzt holt der
Server das aktuelle Release von microsoft/winget-cli (Prüfsumme aus der GitHub-API, nur HTTPS) in den Ordner
„Offline-Quelle“ (Standard „_winget“). Clients installieren nur noch von dort – ohne Internetzugriff je PC.
Add-AppxProvisionedPackage prüft zusätzlich die Microsoft-Signatur der Pakete.
"""
import json
import os
import re
import threading
import urllib.request

from . import config, db, pklog, proxy, remote, util

API = "https://api.github.com/repos/microsoft/winget-cli/releases/latest"
DEFAULT_FOLDER = "_winget"
MAX_BYTES = 512 * 1024 * 1024
WANT = re.compile(r"(\.msixbundle$|License.*\.xml$|^DesktopAppInstaller_Dependencies\.zip$)", re.I)
_lock = threading.Lock()


class BootError(Exception):
    pass


def folder():
    """Ordner im Repository (Einstellung „Offline-Quelle“, sonst „_winget“)."""
    f = str(db.setting("winget_bootstrap_folder") or "").strip().strip("\\/").replace("/", "\\")
    return f or DEFAULT_FOLDER


def _release():
    req = urllib.request.Request(API, headers={"User-Agent": "POCKETKNIFE/" + config.VERSION,
                                               "Accept": "application/vnd.github+json"})
    with proxy.urlopen(req, timeout=60) as r:
        data = json.loads(r.read(2 * 1024 * 1024).decode("utf-8"))
    tag = str(data.get("tag_name") or "").strip()
    assets = []
    for a in data.get("assets") or []:
        name, url = str(a.get("name") or ""), str(a.get("browser_download_url") or "")
        if not WANT.search(name):
            continue
        if not re.match(r"^[A-Za-z0-9._ -]{1,120}$", name) or not url.lower().startswith("https://"):
            raise BootError(f"Unerwarteter Dateiname oder Adresse im Release: {name[:80]}")
        dg = str(a.get("digest") or "")
        sha = dg.split(":", 1)[1].lower() if dg.lower().startswith("sha256:") else None
        assets.append({"name": name, "url": url, "sha": sha, "size": int(a.get("size") or 0)})
    if not tag or not any(x["name"].lower().endswith(".msixbundle") for x in assets):
        raise BootError("Im aktuellen Release von microsoft/winget-cli fehlt der App-Installer (*.msixbundle).")
    return tag, assets


def present():
    """Liegt ein App-Installer im Ordner?"""
    try:
        return any(n.lower().endswith(".msixbundle") for n in _list())
    except Exception:
        return False


def _list():
    """Dateinamen im Ordner (ohne Unterordner); fehlt der Ordner: leer."""
    try:
        return [str(e["name"]) for e in remote.repo_list(folder()) or [] if not e.get("dir")]
    except Exception:
        return []


def refresh(user=None, force=False):
    """Aktuelles Release laden, wenn es neuer ist bzw. Dateien fehlen. Gibt einen Text für Protokoll/Oberfläche zurück."""
    from . import wingetcache
    if not db.setting("winget_bootstrap"):
        return "Automatisches Nachinstallieren von winget ist ausgeschaltet."
    if not _lock.acquire(blocking=False):
        return "Läuft bereits."
    try:
        tag, assets = _release()
        have = {n.lower() for n in _list()}
        if not force and db.setting("winget_bootstrap_tag") == tag and all(a["name"].lower() in have for a in assets):
            return f"App-Installer {tag} liegt schon im Repository."
        remote.repo_mkdir(folder())
        for a in assets:
            tmp, size, got = wingetcache._download(a["url"], a["sha"], MAX_BYTES, verify=bool(a["sha"]))
            try:
                target = f"{folder()}\\{a['name']}"
                part = target + ".pk-part"
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
        # Dateien älterer Releases entfernen – nur die, die POCKET KNIFE selbst ablegt (nie Fremdes im Ordner)
        keep = {a["name"].lower() for a in assets}
        for n in _list():
            if WANT.search(n) and n.lower() not in keep:
                remote.repo_delete(f"{folder()}\\{n}")
                remote.repo_cache_apply("remove", f"{folder()}\\{n}")
        db.set_setting("winget_bootstrap_tag", tag)
        msg = f"App-Installer (winget) {tag} in das Repository geladen ({folder()})."
        pklog.info("winget", msg, user=user)
        return msg
    except BootError as e:
        pklog.warn("winget", f"App-Installer für Computer ohne winget: {e}", user=user)
        raise
    except Exception as e:  # noqa: BLE001
        msg = f"App-Installer für Computer ohne winget konnte nicht geladen werden: {proxy.safe_error(e)}"
        pklog.warn("winget", msg, user=user)
        raise BootError(msg)
    finally:
        _lock.release()


def status():
    return {"folder": folder(), "tag": db.setting("winget_bootstrap_tag") or "", "present": present(),
            "checked": util.now()}
