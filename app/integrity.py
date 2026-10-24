"""Brüche in Paketen erkennen (2.26).

POCKET KNIFE merkt sich für jede Repository-Datei, die ein Paket benutzt, den bestätigten Stand: Größe, Änderungsdatum
und SHA-256 (Tabelle repo_baseline, Schlüssel = Pfad im Repository, klein geschrieben – eine Datei, die mehrere Pakete
benutzen, hat einen gemeinsamen Stand). Für ganze Paketordner wird zusätzlich die Dateiliste bestätigt
(repo_baseline_dirs), damit hinzugekommene Dateien auffallen.

Prüfungen:
- Schnellprüfung alle 5 Minuten aus dem Repository-Index im Arbeitsspeicher (nur Name, Größe, Datum – kein Dateiinhalt);
  ohne gültigen Index höchstens alle 30 Minuten direkt auf der Freigabe.
- Hat sich nur das Änderungsdatum geändert, wird der Fingerabdruck neu berechnet (im Hintergrund, gedrosselt): gleich →
  nur „angefasst“, der Stand wird still nachgeführt; anders → Bruch.
- Vor jeder Bereitstellung (auch Zeitplan, Kiosk, Wiederholen) direkt auf der Freigabe. Bruch → der Lauf startet nicht
  (IntegrityError) – „unterbrechen + Warnung“ (Entscheidung des Betreibers, 24.10.2026).
- Vollprüfung: jede Datei einmal pro Woche, nachts im Zeitfenster, gedrosselt (Einstellungen → Repository & winget).

Stufen: broken (rot: Datei/Ordner fehlt, Installationsdatei bzw. Quelle einer Dateikopie geändert) – Lauf wird
unterbrochen; warn (gelb: andere Dateien im Paketordner geändert oder hinzugekommen, Prüfung läuft noch) – Lauf geht
weiter, Hinweis im Protokoll; unknown (grau: Repository nicht erreichbar) – kein Bruch; ok; "" (Paket ohne
Repository-Dateien, z. B. winget).
"""
import bisect
import datetime
import json
import threading
import time

from . import db, pklog, remote, util

TOL = 2                # Sekunden Abweichung beim Änderungsdatum (Zeitauflösung der Dateisysteme)
MAX_ISSUES = 60        # je Paket gespeicherte Abweichungen
LIVE_EVERY = 30 * 60   # ohne Index: so oft direkt auf der Freigabe prüfen (Sekunden)
PRECHECK_HASH_SECONDS = 120  # vor einem Lauf höchstens so lange Fingerabdrücke berechnen
FULL_DAYS = 7          # Vollprüfung: jede Datei spätestens nach so vielen Tagen

_q_lock = threading.Lock()
_hash_queue = {}       # Schlüssel → None (dict = Menge mit Reihenfolge): Fingerabdruck berechnen, sobald der Worker Zeit hat
_state = {"live_at": 0.0, "worker": False, "night_live": "", "moves": 0}


def moved():
    """Repository wird gerade umgebaut (repofiles: Verschieben/Umbenennen/Reparatur) – laufende Gesamtprüfungen
    verwerfen ihren Stand (sie haben die Pakete evtl. noch mit den alten Pfaden gelesen)."""
    with _q_lock:
        _state["moves"] += 1
_locks_lock = threading.Lock()
_pkg_locks = {}        # je Paket eine Sperre (Prüfen/Übernehmen) – nie eine globale, die alle Starts aufhält
_check_times = {}      # (benutzer, paket) → letzte „Jetzt prüfen“-Anfrage (Drossel)


class IntegrityError(ValueError):
    """Bruch – der Lauf wird unterbrochen. issues: Liste der Abweichungen (für die Anzeige je Benutzer)."""

    def __init__(self, msg, name="", issues=None):
        super().__init__(msg)
        self.name = name
        self.issues = issues or []


def norm(rel):
    return "\\".join(x for x in str(rel or "").replace("/", "\\").split("\\") if x not in ("", "."))


def key(rel):
    return norm(rel).lower()


# --- Welche Repository-Pfade benutzt ein Paket? ---------------------------------------------------------
def refs(p, _depth=0):
    """[(pfad, art, haupt)] – art „dir“ (ganzer Ordner mit Unterordnern), „file“ oder „auto“ (Dateikopie: Ordner oder
    Datei). haupt = Installationsdatei bzw. Quelle einer Dateikopie (eine Änderung daran ist ein Bruch).
    Ausgeschaltete Schritte zählen nicht; verschachtelte Pakete werden mit geprüft."""
    from . import deploy, steps
    out = {}
    if _depth > steps.MAX_DEPTH + 1 or not p:
        return []
    try:
        p = steps.normalize(dict(p))
    except Exception:
        return []

    def add(rel, kind, main):
        k = key(rel)
        if not k:
            return
        if k in out:
            r, kd, m = out[k]
            out[k] = (r, kd if kd != "auto" else kind, m or main)
        else:
            out[k] = (norm(rel), kind, main)
    for _ph, st in steps.iter_steps(p):
        if not isinstance(st, dict) or not st.get("enabled", True):
            continue
        t = st.get("type")
        if t == "install" and st.get("source") == "file":
            folder = norm(st.get("folder"))
            f = (st.get("install_file") or "").strip()
            if st.get("copy_folder", True) and folder:
                add(folder, "dir", False)
            if f:
                add(f"{folder}\\{f}" if folder else f, "file", True)
        elif t == "filecopy" and st.get("source"):
            add(st["source"], "auto", True)
        elif t == "nested":
            sub = st.get("package")
            if not sub and st.get("package_id"):
                row = db.q1("SELECT * FROM packages WHERE id=?", (st["package_id"],))
                sub = deploy.row_to_package(row) if row else None
            for r, kd, m in refs(sub, _depth + 1):
                add(r, kd, m)
    return list(out.values())


# --- Aktueller Stand im Repository ------------------------------------------------------------------------
class _Snap:
    """Dateien im Repository: aus dem Index im Arbeitsspeicher (schnell) oder direkt auf der Freigabe (live)."""

    def __init__(self, live):
        self.live = live
        self.idx = None if live else remote.repo_index_files()
        if self.idx:
            self.files, self.dirs = self.idx
            self.keys = sorted(self.files)

    def ok(self):
        return self.live or self.idx is not None

    def kind(self, rel):
        if self.live:
            return remote.repo_stat(rel)
        k = key(rel)
        return "file" if k in self.files else ("dir" if k in self.dirs else None)

    def file(self, rel):
        if self.live:
            r = remote.repo_fstat(rel)
            return (norm(rel), r[0], r[1]) if r else None
        return self.files.get(key(rel))

    def tree(self, rel):
        """{schlüssel: (pfad, größe, mtime)} aller Dateien unter dem Ordner – None, wenn es ihn nicht gibt."""
        if self.live:
            lst = remote.repo_tree(rel)
            return None if lst is None else {key(x[0]): (norm(x[0]), x[1], x[2]) for x in lst}
        k = key(rel)
        if k not in self.dirs:
            return None
        pre = k + "\\"
        out = {}
        i = bisect.bisect_left(self.keys, pre)
        while i < len(self.keys) and self.keys[i].startswith(pre):
            out[self.keys[i]] = self.files[self.keys[i]]
            i += 1
        return out


def _rows(keys):
    out = {}
    keys = list(keys)
    for i in range(0, len(keys), 400):
        part = keys[i:i + 400]
        for r in db.q(f"SELECT * FROM repo_baseline WHERE path IN ({','.join('?' * len(part))})", part):
            out[r["path"]] = r
    return out


def _rows_under(dkey):
    # Bereich statt LIKE: alle Schlüssel, die mit „ordner\“ beginnen (']' folgt in ASCII direkt auf '\')
    return {r["path"]: r for r in db.q("SELECT * FROM repo_baseline WHERE path >= ? AND path < ?", (dkey + "\\", dkey + "]"))}


def _pkg_lock(pid):
    with _locks_lock:
        return _pkg_locks.setdefault(pid, threading.Lock())


def _adopt(k, cur, who, queue=False):
    """Stand einer Datei erstmals festhalten. Den Fingerabdruck dazu rechnet die nächtliche Vollprüfung (full_at leer
    → zuerst) – nur beim Speichern eines Pakets (queue=True) sofort, gedrosselt im Hintergrund."""
    db.ex("""INSERT OR IGNORE INTO repo_baseline(path, disp, size, mtime, sha256, confirmed_at, confirmed_by, cur_size, cur_mtime)
             VALUES(?,?,?,?,NULL,?,?,?,?)""", (k, cur[0], cur[1], cur[2], util.now(), who, cur[1], cur[2]))
    if queue:
        enqueue(k)


def enqueue(k):
    with _q_lock:
        _hash_queue.setdefault(k, None)
    _ensure_worker()


def _ensure_worker():
    """Genau EIN Thread berechnet Fingerabdrücke (eine Datei nach der anderen, gemeinsame Drossel)."""
    with _q_lock:
        if _state["worker"] or not _hash_queue:
            return
        _state["worker"] = True

    def run():
        try:
            process_queue(seconds=None)
        except Exception as e:  # pragma: no cover
            pklog.warn("deploy", f"Fingerabdrücke der Repository-Dateien: {e}")
        finally:
            with _q_lock:
                _state["worker"] = False
            with _q_lock:
                again = bool(_hash_queue)
            if again:
                _ensure_worker()
    threading.Thread(target=run, name="pk-integrity-hash", daemon=True).start()


# --- Prüfen -----------------------------------------------------------------------------------------------
def _issue(kind, path, main, level, old=None, new=None, note=""):
    return {"kind": kind, "path": path, "main": bool(main), "level": level, "old": old or {}, "new": new or {}, "note": note}


def _foreign_folder(path, tenant_id):
    """Liegt path im Repository-Ordner eines Mandanten, dem das Paket nicht gehört?"""
    k = key(path)
    own = int(tenant_id or 0)
    for t in db.q("SELECT id, repo_folder FROM tenants WHERE COALESCE(repo_folder, '') != ''"):
        f = key(t["repo_folder"])
        if f and (k == f or k.startswith(f + "\\")) and t["id"] != own:
            return True
    return False


def _sig(size, mtime, sha=None):
    return {"size": size, "mtime": util.fmt(datetime.datetime.fromtimestamp(mtime)) if mtime else None, "sha256": sha}


def _repo_errors():
    """Fehlerarten beim Zugriff auf das Repository (smbprotocol-Fehler sind keine OSError)."""
    try:
        from .clientfiles import _smb_errors
        return (remote.RemoteError,) + tuple(_smb_errors())
    except Exception:  # pragma: no cover
        return (remote.RemoteError, OSError)


def evaluate(p, live=False, adopt=True, hash_now=False, deadline=None, who="automatisch", snap=None, queue_new=False):
    """Prüft ein Paket gegen den bestätigten Stand. Ergebnis: {state, issues, at, live, keys, total}.
    adopt: Dateien ohne bestätigten Stand erstmals festhalten (neue Pakete, Pakete von vor 2.26).
    hash_now: geänderte Änderungsdaten sofort per Fingerabdruck klären (vor einem Lauf), sonst im Hintergrund.
    keys: alle Pfade, deren Stand dieses Paket braucht (auch fehlende) – fürs Aufräumen.
    Ein Fehler beim Zugriff betrifft nur den einen Pfad („nicht prüfbar“); ein sicher erkannter Bruch bleibt ein Bruch."""
    rs = refs(p)
    res = {"state": "", "issues": [], "at": util.now(), "live": live, "keys": [], "total": 0}
    if not rs:
        return res
    snap = snap or _Snap(live)
    if not snap.ok():
        res.update(state="unknown", issues=[_issue("unreachable", "", False, "unknown", note="Repository-Index noch nicht eingelesen")])
        return res
    errs = _repo_errors()
    issues, keep = [], set()
    expected = {}  # schlüssel → (pfad, haupt)
    current = {}
    for rel, kind, main in rs:
        k = key(rel)
        try:
            if kind == "auto":
                kind = snap.kind(rel)
                if kind is None:
                    keep.add(k)
                    keep.update(_rows_under(k))
                    issues.append(_issue("missing", rel, main, "broken", note="Quelle der Dateikopie fehlt"))
                    continue
            if kind == "dir":
                cur = snap.tree(rel)
                if cur is None:
                    keep.update(_rows_under(k))  # Stand behalten – kommt der Ordner geändert zurück, ist das ein Bruch
                    issues.append(_issue("missing", rel, main, "broken", note="Ordner fehlt"))
                    continue
                current.update(cur)
                base = _rows_under(k)
                confirmed = db.val("SELECT 1 FROM repo_baseline_dirs WHERE path=?", (k,))
                if not confirmed and adopt:
                    for ck, c in cur.items():
                        if ck not in base:
                            _adopt(ck, c, who, queue_new)
                    db.ex("INSERT OR IGNORE INTO repo_baseline_dirs(path, confirmed_at) VALUES(?,?)", (k, util.now()))
                    base, confirmed = _rows_under(k), 1
                for bk, b in base.items():
                    prev = expected.get(bk)
                    # Quelle einer Dateikopie: jede Datei im Ordner zählt wie die Installationsdatei
                    expected[bk] = (b["disp"] or bk, main or (prev[1] if prev else False))
                if confirmed:
                    for ck, c in cur.items():
                        if ck not in base:
                            # In einem Mandantenordner von jemand anderem abgelegt (2.27): Bruch – sonst könnte ein
                            # Mandant z. B. eine DLL neben die Installationsdatei eines zentralen Pakets legen
                            foreign = _foreign_folder(c[0], p.get("tenant_id"))
                            issues.append(_issue("added", c[0], foreign, "broken" if foreign else "warn", new=_sig(c[1], c[2]),
                                                 note="neu im Paketordner – liegt im Ordner eines anderen Mandanten" if foreign
                                                 else "neu im Paketordner"))
            else:
                c = snap.file(rel)
                if c:
                    current[k] = c
                    if adopt and not db.val("SELECT 1 FROM repo_baseline WHERE path=?", (k,)):
                        _adopt(k, c, who, queue_new)
                prev = expected.get(k)
                expected[k] = (rel, main or (prev[1] if prev else False))
        except errs as e:
            keep.add(k)
            keep.update(_rows_under(k))
            issues.append(_issue("unreachable", rel, main, "unknown", note=f"nicht prüfbar: {str(e)[:200]}"))
    base = _rows(expected)
    t_end = deadline or (time.time() + PRECHECK_HASH_SECONDS)
    for k, (disp, main) in expected.items():
        b, c = base.get(k), current.get(k)
        lvl = "broken" if main else "warn"
        if c is None:
            issues.append(_issue("missing", disp, main, "broken",
                                 old=_sig(b["size"], b["mtime"], b["sha256"]) if b else None, note="Datei fehlt"))
            continue
        if b is None:
            issues.append(_issue("unconfirmed", c[0], main, "warn", new=_sig(c[1], c[2]), note="noch kein bestätigter Stand"))
            continue
        same = c[1] == b["size"] and abs((c[2] or 0) - (b["mtime"] or 0)) <= TOL
        if same:
            # Vollprüfung hat bei gleicher Größe/gleichem Datum einen anderen Fingerabdruck gefunden?
            if (b["cur_sha256"] and b["sha256"] and b["cur_sha256"] != b["sha256"] and b["cur_size"] == c[1]
                    and abs((b["cur_mtime"] or 0) - (c[2] or 0)) <= TOL):
                issues.append(_issue("changed", c[0], main, lvl, old=_sig(b["size"], b["mtime"], b["sha256"]),
                                     new=_sig(c[1], c[2], b["cur_sha256"]), note="Inhalt ausgetauscht (gleiche Größe und gleiches Datum)"))
            continue
        if c[1] != b["size"]:
            issues.append(_issue("changed", c[0], main, lvl, old=_sig(b["size"], b["mtime"], b["sha256"]),
                                 new=_sig(c[1], c[2]), note="Größe geändert"))
            continue
        # Nur das Datum ist anders: der Fingerabdruck entscheidet
        sha = None
        if b["cur_sha256"] and b["cur_size"] == c[1] and abs((b["cur_mtime"] or 0) - (c[2] or 0)) <= TOL:
            sha = b["cur_sha256"]
        elif hash_now and b["sha256"] and time.time() < t_end:
            try:
                sha = remote.repo_sha256(c[0], deadline=t_end)
            except errs:
                sha = None
            if sha:
                db.ex("UPDATE repo_baseline SET cur_size=?, cur_mtime=?, cur_sha256=?, cur_at=? WHERE path=?",
                      (c[1], c[2], sha, util.now(), k))
        if not b["sha256"]:
            # Noch kein Fingerabdruck zum Vergleich (z. B. kurz nach der ersten Übernahme): gleiche Größe, nur das Datum
            # anders – kein sicherer Bruch; die Vollprüfung bzw. der Hintergrund holt den Vergleich nach
            issues.append(_issue("unverified", c[0], main, "warn", old=_sig(b["size"], b["mtime"]), new=_sig(c[1], c[2], sha),
                                 note="Änderungsdatum anders – kein früherer Fingerabdruck zum Vergleich (bitte prüfen und übernehmen)"))
            continue
        if sha and sha == b["sha256"]:
            # nur angefasst (z. B. kopiert mit neuem Datum) – Stand still nachführen
            db.ex("UPDATE repo_baseline SET size=?, mtime=? WHERE path=?", (c[1], c[2], k))
            continue
        if sha is None:
            enqueue(k)
            issues.append(_issue("pending", c[0], main, "warn", old=_sig(b["size"], b["mtime"]), new=_sig(c[1], c[2]),
                                 note="Änderungsdatum anders – Fingerabdruck wird geprüft"))
            continue
        issues.append(_issue("changed", c[0], main, lvl, old=_sig(b["size"], b["mtime"], b["sha256"]),
                             new=_sig(c[1], c[2], sha), note="Inhalt geändert"))
    order = {"broken": 0, "warn": 1, "unknown": 2}
    issues.sort(key=lambda x: (order.get(x["level"], 3), not x["main"], x["path"].lower()))
    levels = {x["level"] for x in issues}
    state = "broken" if "broken" in levels else ("warn" if "warn" in levels else ("unknown" if "unknown" in levels else "ok"))
    res.update(state=state, issues=issues[:MAX_ISSUES], total=len(issues), keys=sorted(set(expected) | keep))
    return res


def summary(res, limit=4):
    """Kurztext der Abweichungen („7zip\\7z.msi: Inhalt geändert; …“)."""
    parts = [f"{x['path'] or 'Repository'}: {x['note'] or x['kind']}" for x in res.get("issues") or [] if x["level"] != "unknown"]
    more = (res.get("total") or len(parts)) - min(len(parts), limit)
    return "; ".join(parts[:limit]) + (f" (+{more} weitere)" if more > 0 else "")


def public(res):
    """Nur die Felder für die Oberfläche (nie die Liste aller Pfade eines Pakets)."""
    if not res:
        return res
    return {k: res.get(k) for k in ("state", "issues", "at", "total")}


def store(pid, res):
    """Ergebnis beim Paket ablegen; neue Brüche einmal im Protokoll melden."""
    row = db.q1("SELECT name, integrity FROM packages WHERE id=?", (pid,))
    if not row:
        return
    try:
        old = json.loads(row["integrity"] or "{}")
    except ValueError:
        old = {}
    db.ex("UPDATE packages SET integrity=? WHERE id=?", (json.dumps(public(res), ensure_ascii=False), pid))
    if res["state"] in ("broken", "warn") and (old.get("state") != res["state"] or summary(old).lower() != summary(res).lower()):
        fn = pklog.warn if res["state"] == "broken" else pklog.info
        fn("deploy", f"Paket „{row['name']}“: {'Bruch' if res['state'] == 'broken' else 'Abweichung'} im Repository – "
                     f"{summary(res)}.")
    elif res["state"] == "ok" and old.get("state") in ("broken", "warn"):
        pklog.info("deploy", f"Paket „{row['name']}“: Repository-Dateien wieder in Ordnung.")


def check_package(pid, live=True, who="automatisch", queue_new=False):
    from . import deploy
    row = db.q1("SELECT * FROM packages WHERE id=?", (pid,))
    if not row:
        return None
    with _pkg_lock(pid):
        res = evaluate(deploy.row_to_package(row), live=live, adopt=True, hash_now=live, who=who, queue_new=queue_new)
        store(pid, res)
    return res


def throttle_check(user, pid, seconds=10):
    """„Jetzt prüfen“ höchstens alle 10 Sekunden je Benutzer und Paket (liest auf der Freigabe)."""
    k = (str(user), int(pid))
    now = time.time()
    if now - _check_times.get(k, 0) < seconds:
        return False
    _check_times[k] = now
    if len(_check_times) > 5000:
        _check_times.clear()
    return True


def check_all(live=None):
    """Alle Pakete prüfen (Hintergrund). Mit Index: Brüche und Abweichungen werden vor dem Speichern direkt auf der
    Freigabe bestätigt (ein veralteter Index meldet sonst Fehlalarme). Aufgeräumt wird nur bei direkter Prüfung."""
    from . import deploy
    if not (db.setting("repo_path") or "").strip():
        return
    if live is None:
        live = remote.repo_index_files() is None
        if live:
            if time.time() - _state["live_at"] < LIVE_EVERY:
                return
            _state["live_at"] = time.time()
    try:
        snap = _Snap(live) if not live else None
    except Exception:
        snap = None
    if not live and (snap is None or not snap.ok()):
        return
    used, t0 = set(), util.now()
    m0 = _state["moves"]
    for row in db.q("SELECT * FROM packages"):
        pkg = deploy.row_to_package(row)
        with _pkg_lock(row["id"]):
            res = evaluate(pkg, live=live, adopt=live, snap=snap)
            if not live and res["state"] in ("broken", "warn"):
                res = evaluate(pkg, live=True, adopt=True)
            if _state["moves"] != m0:
                return  # inzwischen umgebaut: nichts Veraltetes speichern, nichts aufräumen (nächster Lauf prüft neu)
            used.update(res.get("keys") or [])
            store(row["id"], res)
    if live and _state["moves"] == m0:
        _prune(used, t0)


def _prune(used, before):
    """Stände von Dateien, die kein Paket mehr braucht, entfernen (Tabelle bleibt klein). Nur ältere als der Beginn des
    Durchlaufs – ein gerade gespeichertes Paket hat seinen Stand vielleicht erst währenddessen festgehalten."""
    # Audit 2.29: auch Dateien behalten, die nur noch gespeicherte Bereitstellungen brauchen (geplant, „Erneut verteilen“,
    # „Schritt wiederholen“) – sonst würde eine danach ausgetauschte Datei beim nächsten Lauf als neuer Stand übernommen
    snap_files, snap_dirs = _snapshot_refs()
    for r in db.q("SELECT path FROM repo_baseline WHERE confirmed_at < ?", (before,)):
        p = r["path"]
        if p not in used and p not in snap_files and not any(p.startswith(d + "\\") for d in snap_dirs):
            db.ex("DELETE FROM repo_baseline WHERE path=?", (p,))
    dirs = set(snap_dirs)
    from . import deploy
    for row in db.q("SELECT * FROM packages"):
        for rel, kind, _m in refs(deploy.row_to_package(row)):
            if kind in ("dir", "auto"):
                dirs.add(key(rel))
    for r in db.q("SELECT path FROM repo_baseline_dirs WHERE confirmed_at < ?", (before,)):
        if r["path"] not in dirs:
            db.ex("DELETE FROM repo_baseline_dirs WHERE path=?", (r["path"],))


def _snapshot_refs():
    """Repository-Dateien und -Ordner aus den Paket-Ständen gespeicherter Bereitstellungen."""
    files, dirs = set(), set()
    for d in db.q("SELECT DISTINCT package_json FROM deployments WHERE package_json IS NOT NULL"):
        try:
            pkg = json.loads(d["package_json"] or "{}")
            for rel, kind, _m in refs(pkg):
                k = key(rel)
                if k:
                    if kind in ("dir", "auto"):
                        dirs.add(k)
                    if kind != "dir":
                        files.add(k)
        except Exception:  # noqa: BLE001 – ein kaputter alter Stand darf das Aufräumen nicht verhindern
            continue
    return files, dirs


def process_queue(seconds=240):
    """Fingerabdrücke aus der Warteschlange berechnen (gedrosselt, eine Datei nach der anderen). Eine begonnene Datei
    wird zu Ende gerechnet – auch große Dateien bekommen so ihren Fingerabdruck. seconds=None: bis die Schlange leer ist."""
    mbps = max(5, int(db.setting("integrity_full_mbps") or 50))
    t_end = time.time() + seconds if seconds else None
    errs = _repo_errors()
    done = 0
    while not t_end or time.time() < t_end:
        with _q_lock:
            if not _hash_queue:
                break
            k = next(iter(_hash_queue))
            del _hash_queue[k]
        b = db.q1("SELECT * FROM repo_baseline WHERE path=?", (k,))
        if not b:
            continue
        try:
            st = remote.repo_fstat(b["disp"])
            if st is None:
                continue
            sha = remote.repo_sha256(b["disp"], mbps=mbps)
        except errs:
            continue
        if sha:
            _record_hash(b, st, sha)
            done += 1
    return done


def _record_hash(b, st, sha):
    now = util.now()
    db.ex("UPDATE repo_baseline SET cur_size=?, cur_mtime=?, cur_sha256=?, cur_at=?, full_at=? WHERE path=?",
          (st[0], st[1], sha, now, now, b["path"]))
    if not b["sha256"] and st[0] == b["size"] and abs((st[1] or 0) - (b["mtime"] or 0)) <= TOL:
        db.ex("UPDATE repo_baseline SET sha256=? WHERE path=? AND sha256 IS NULL", (sha, b["path"]))  # erster Fingerabdruck
    elif b["sha256"] and sha == b["sha256"] and st[0] == b["size"]:
        db.ex("UPDATE repo_baseline SET mtime=? WHERE path=?", (st[1], b["path"]))  # nur angefasst


def _hours():
    def h(k, d):
        try:
            v = int(db.setting(k))
        except (TypeError, ValueError):
            v = d
        return max(0, min(24, v))
    return h("integrity_full_start", 1), h("integrity_full_end", 6)


def in_window(now=None):
    """Zeitfenster der Vollprüfung (Stunden, Ende ausschließlich; über Mitternacht möglich, z. B. 22–5)."""
    now = now or datetime.datetime.now()
    s, e = _hours()
    h = now.hour
    if s == e:
        return False
    return (s <= h < e) if s < e else (h >= s or h < e)


def _window_end(now=None):
    now = now or datetime.datetime.now()
    _s, e = _hours()
    end = now.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1)
    while end.hour != e % 24:
        end += datetime.timedelta(hours=1)
    return end.timestamp()


def full_check():
    """Vollprüfung: Fingerabdruck jeder Datei, deren letzte Prüfung über eine Woche zurückliegt (neue zuerst) – nachts im
    Zeitfenster, gedrosselt, eine Datei nach der anderen. Endet das Zeitfenster, geht es in der nächsten Nacht weiter.
    Einmal je Nacht zusätzlich eine direkte Prüfung aller Pakete (räumt auch nicht mehr benutzte Stände auf)."""
    if not db.setting("integrity_full_enabled") or not in_window():
        return 0
    night = datetime.date.today().isoformat()
    if _state["night_live"] != night:
        _state["night_live"] = night
        check_all(live=True)
    mbps = max(5, int(db.setting("integrity_full_mbps") or 50))
    limit = util.fmt(datetime.datetime.now() - datetime.timedelta(days=FULL_DAYS))
    t_end = _window_end()
    n = 0
    for b in db.q("SELECT * FROM repo_baseline WHERE full_at IS NULL OR full_at < ? ORDER BY full_at IS NOT NULL, full_at", (limit,)):
        if time.time() >= t_end or not in_window():
            break
        try:
            st = remote.repo_fstat(b["disp"])   # Fehler des ganzen Repositorys → RemoteError → nächste Nacht weiter
        except remote.RemoteError:
            break
        if st is None:
            db.ex("UPDATE repo_baseline SET full_at=? WHERE path=?", (util.now(), b["path"]))
            continue
        try:
            sha = remote.repo_sha256(b["disp"], mbps=mbps, deadline=t_end, cancel=lambda: not in_window())
        except remote.RemoteError:
            break
        except Exception:
            # einzelne Datei nicht lesbar (gesperrt, Rechte) – nicht jede Nacht daran hängen bleiben
            db.ex("UPDATE repo_baseline SET full_at=? WHERE path=?", (util.now(), b["path"]))
            continue
        if sha is None:
            break
        _record_hash(b, st, sha)
        n += 1
    if n:
        pklog.info("deploy", f"Vollprüfung der Repository-Dateien: {n} Fingerabdruck/Fingerabdrücke geprüft.")
        check_all(live=remote.repo_index_files() is None)
    return n


def background():
    """Alle 5 Minuten (Scheduler): Index auffrischen (für die Schnellprüfung), dann alle Pakete prüfen."""
    remote.repo_cache_refresh()
    check_all()


# --- Vor einem Lauf -----------------------------------------------------------------------------------------
NEUTRAL = "Unterbrochen – Dateien des Pakets im Repository haben sich geändert (Details im Paket, Spalte „Zustand“)."


def guard(pkg, package_id=None, name=None):
    """Vor einer Bereitstellung: direkt auf der Freigabe prüfen. Bruch → IntegrityError (der Lauf startet nicht).
    Gibt das Ergebnis zurück (Abweichungen der Stufe „warn“ lassen den Lauf zu). Das Ergebnis wird NICHT beim Paket
    gespeichert – pkg kann ein Ausschnitt (Wiederholen einzelner Schritte) oder ein alter Stand sein."""
    res = evaluate(pkg, live=True, adopt=True, hash_now=True)
    nm = name or pkg.get("name") or "?"
    # Audit 2.29: Hauptdatei mit anderem Änderungsdatum, deren Fingerabdruck vor dem Start nicht fertig wurde → nicht
    # einfach laufen lassen (gleich große, ausgetauschte Datei); der Fingerabdruck wird im Hintergrund fertig gerechnet
    # Nur die Installationsdatei selbst (nicht jede Datei eines kopierten Ordners – ein neu kopierter Ordner hätte sonst
    # jeden Lauf blockiert)
    inst = {key(r) for r, kd, m in refs(pkg) if kd == "file" and m}
    for x in res.get("issues") or []:
        if x["kind"] == "pending" and x["main"] and key(x["path"]) in inst:
            x["level"] = "broken"
            x["note"] = ("Änderungsdatum der Installationsdatei anders – Fingerabdruck noch nicht fertig (wird im Hintergrund "
                         "berechnet); danach erneut starten")
            res["state"] = "broken"
    if res["state"] == "broken":
        bad = {"issues": [x for x in res["issues"] if x["level"] == "broken"], "total": None}
        raise IntegrityError(f"Unterbrochen – Paket „{nm}“: Dateien im Repository haben sich geändert ({summary(bad)}). "
                             "Bitte prüfen und im Paket „Neuen Stand übernehmen“ oder das Paket anpassen.", nm, res["issues"])
    return res


def public_message(err, prefixes):
    """Meldung für einen OU-Admin: Details nur zu Dateien in seinen Mandanten-Ordnern, sonst allgemein."""
    own = [x for x in err.issues if x["level"] == "broken" and _inside(x["path"], prefixes)]
    other = [x for x in err.issues if x["level"] == "broken" and not _inside(x["path"], prefixes)]
    if other or not own:
        return (f"Unterbrochen – Paket „{err.name}“ ist derzeit nicht verfügbar (Dateien im Repository geändert). "
                "Bitte einen Hauptadmin informieren.")
    return str(err)


def _inside(path, prefixes):
    k = key(path)
    return bool(k) and any(k == p or k.startswith(p + "\\") for p in prefixes or [])


def scope_view(res, prefixes):
    """Ergebnis für die Oberfläche; für einen OU-Admin (prefixes = seine Mandanten-Ordner) ohne Pfade und Details
    außerhalb dieser Ordner."""
    res = public(res)
    if prefixes is None or not res:
        return res
    out = dict(res)
    iss = []
    hidden = 0
    for x in res.get("issues") or []:
        if _inside(x.get("path") or "", prefixes):
            iss.append(x)
        else:
            hidden += 1
    if hidden:
        iss.append(_issue("hidden", "", False, "broken" if res.get("state") == "broken" else "warn",
                          note=f"{hidden} Abweichung(en) in zentralen Dateien – bitte einen Hauptadmin informieren"))
    out["issues"] = iss
    return out


# --- Neuen Stand übernehmen ---------------------------------------------------------------------------------
def accept(p, user, prefixes=None, pid=None):
    """Den aktuellen Inhalt der Repository-Dateien eines Pakets als bestätigten Stand übernehmen (bewusste Änderung).
    prefixes: OU-Admin – nur Dateien in seinen Mandanten-Ordnern. Der Fingerabdruck wird danach neu berechnet (nie ein
    älterer übernommen). Gibt {count, msi} zurück."""
    rs = refs(p)
    snap = _Snap(True)
    now = util.now()
    n = 0
    msi_info = None
    lk = _pkg_lock(pid) if pid else threading.Lock()
    with lk:
        for rel, kind, main in rs:
            if prefixes is not None and not _inside(rel, prefixes):
                continue
            k = key(rel)
            if kind == "auto":
                kind = snap.kind(rel)
                if kind is None:
                    continue
            if kind == "dir":
                cur = snap.tree(rel)
                if cur is None:
                    continue
                for bk in _rows_under(k):
                    if bk not in cur:
                        db.ex("DELETE FROM repo_baseline WHERE path=?", (bk,))
                        n += 1
                for ck, c in cur.items():
                    n += _confirm(ck, c, user, now)
                db.ex("INSERT OR REPLACE INTO repo_baseline_dirs(path, confirmed_at) VALUES(?,?)", (k, now))
            else:
                c = snap.file(rel)
                if c:
                    n += _confirm(k, c, user, now)
                    if main and c[0].lower().endswith(".msi"):
                        try:
                            from . import msi
                            with remote.repo_open_rel(c[0]) as fh:
                                info = msi.read(fh)
                            msi_info = {"file": c[0], "version": info.get("version"), "product_code": info.get("product_code"),
                                        "name": info.get("name")}
                        except Exception:
                            pass
    return {"count": n, "msi": msi_info}


def _confirm(k, c, user, now):
    b = db.q1("SELECT * FROM repo_baseline WHERE path=?", (k,))
    if b and b["size"] == c[1] and abs((b["mtime"] or 0) - (c[2] or 0)) <= TOL and not (
            b["cur_sha256"] and b["sha256"] and b["cur_sha256"] != b["sha256"]):
        return 0
    # Fingerabdruck immer neu rechnen lassen – ein gespeicherter könnte zu einem inzwischen wieder getauschten Inhalt gehören
    db.ex("""INSERT INTO repo_baseline(path, disp, size, mtime, sha256, confirmed_at, confirmed_by, cur_size, cur_mtime, cur_sha256, full_at)
             VALUES(?,?,?,?,NULL,?,?,?,?,NULL,NULL) ON CONFLICT(path) DO UPDATE SET disp=excluded.disp, size=excluded.size,
             mtime=excluded.mtime, sha256=NULL, confirmed_at=excluded.confirmed_at, confirmed_by=excluded.confirmed_by,
             cur_size=excluded.cur_size, cur_mtime=excluded.cur_mtime, cur_sha256=NULL, full_at=NULL""",
          (k, c[0], c[1], c[2], now, user, c[1], c[2]))
    enqueue(k)
    return 1


def after_save(pid):
    """Nach dem Speichern eines Pakets: Stand neuer Dateien festhalten und prüfen (im Hintergrund); die Fingerabdrücke
    der neu festgehaltenen Dateien rechnet der eine Hintergrund-Worker gedrosselt."""
    def run():
        try:
            check_package(pid, live=True, queue_new=True)
        except Exception as e:  # pragma: no cover
            pklog.warn("deploy", f"Prüfung des Pakets #{pid} fehlgeschlagen: {e}")
    threading.Thread(target=run, name=f"pk-integrity-{pid}", daemon=True).start()
