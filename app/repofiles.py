"""Repository über die Oberfläche verwalten (2.27): Ordner anlegen, hochladen (in Stücken, mit Fortsetzen), umbenennen,
verschieben, löschen – immer mit dem Repository-/Dienstkonto, nie mit den Rechten des Bedieners.

Schutz:
- OU-Admins nur in den Repository-Ordnern ihrer Mandanten (mit „eigene Pakete erlaubt“); den Mandantenordner selbst
  können sie nicht umbenennen, verschieben oder löschen. Hauptadmins überall.
- Was ein Paket (auch ein ausgeschalteter Schritt), eine geplante/laufende Bereitstellung, ein Mandant (sein Ordner)
  oder die winget-Offline-Kopie benutzt, lässt sich nicht löschen oder überschreiben (Meldung mit den Paketen).
- Verschieben/Umbenennen passt die Pfade in allen betroffenen Paketen, Mandanten, geplanten Bereitstellungen und im
  bestätigten Stand der Bruchprüfung an – vorher automatische Sicherung, bei einem Fehler wird alles zurückgedreht.

Duplikatsperre: Kennzeichen je Paket (winget-ID, MSI-Produktcode, SHA-256 der Installationsdatei) in pkg_ident. Gleiche
Software in einem ANDEREN Bereich (zentral, Mandant X, Mandant Y) wird abgewiesen – auch zwischen zwei Mandanten. Im
selben Bereich ist es erlaubt. Hauptadmins dürfen übersteuern (protokolliert).

Reparatur per Fingerabdruck: wurde außerhalb von POCKET KNIFE verschoben, sucht repair_suggest die Dateien über Größe
(Index im Arbeitsspeicher) und SHA-256 und schlägt neue Pfade vor; repair_apply schreibt sie in alle Pakete.
"""
import hashlib
import itertools
import json
import re
import secrets
import threading
import time

from . import access, db, deploy, integrity, pklog, remote, steps, util

norm = integrity.norm
key = integrity.key

TENANT_BASE = "Mandanten"
CHUNK = 8 * 1024 * 1024         # Größe eines Upload-Stücks (Browser → Server)
UPLOAD_STALE_H = 24             # abgebrochene Uploads so lange fortsetzbar, danach aufgeräumt
REPAIR_CANDIDATES = 25          # je fehlender Datei höchstens so viele Kandidaten per Fingerabdruck prüfen
REPAIR_SECONDS = 90

_write_lock = threading.RLock()  # Struktur-Änderungen (Verschieben, Löschen, Abschluss eines Uploads) nacheinander
_up_locks = {}
_up_locks_lock = threading.Lock()
_hashers = {}                    # Upload-Token → (sha256-Objekt, bis Byte) – nach einem Neustart aus der Datei neu
_warned = set()

INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$", re.I)


class RepoError(ValueError):
    status = 400

    def __init__(self, msg, info=None):
        super().__init__(msg)
        self.info = info or {}


class Blocked(RepoError):
    """Ein Paket (o. Ä.) benutzt den Pfad noch."""
    status = 409


class Duplicate(RepoError):
    """Gleiche Software gibt es schon in einem anderen Bereich."""
    status = 409


# --- Pfade und Bereiche ------------------------------------------------------------------------------------------
def _parts(rel):
    p = [x for x in str(rel or "").replace("/", "\\").split("\\") if x not in ("", ".")]
    if ".." in p:
        raise RepoError("Ungültiger Pfad.")
    for x in p:
        # Zeichen, die Python beim Kleinschreiben anders zusammenlegt als Windows (z. B. das Kelvin-Zeichen „K“ → „k“):
        # sonst hielte die Bereichsprüfung einen Nachbarordner für den eigenen
        if x.lower().upper() != x.upper() or x.upper().lower() != x.lower():
            raise RepoError(f"„{x}“ enthält ein Sonderzeichen, das im Repository nicht erlaubt ist.")
    return p


def _join(*xs):
    return "\\".join(x for x in (norm(y) for y in xs) if x)


def _parent(rel):
    p = _parts(rel)
    return "\\".join(p[:-1])


def _base(rel):
    p = _parts(rel)
    return p[-1] if p else ""


def _inside(k, root_k):
    return k == root_k or (root_k == "" and k != "") or k.startswith(root_k + "\\")


def valid_name(name):
    n = str(name or "")
    if not n.strip():
        raise RepoError("Name fehlt.")
    if len(n) > 200:
        raise RepoError("Name zu lang (höchstens 200 Zeichen).")
    if INVALID.search(n):
        raise RepoError(f"„{n}“: Zeichen wie \\ / : * ? \" < > | sind in Datei- und Ordnernamen nicht erlaubt.")
    if n != n.strip() or n.endswith("."):
        raise RepoError(f"„{n}“: Name darf nicht mit Leerzeichen oder Punkt enden bzw. mit Leerzeichen beginnen.")
    if n in (".", "..") or RESERVED.match(n):
        raise RepoError(f"„{n}“ ist unter Windows ein reservierter Name.")
    if n.lower() == remote.UPLOAD_DIR or n.lower().startswith(".pk-upload"):
        raise RepoError(f"„{n}“ ist für POCKET KNIFE reserviert.")
    return n


def roots(user):
    """Ordner, in denen der Benutzer schreiben darf – None = überall (Hauptadmin)."""
    if not access.scoped(user):
        return None
    for t in access.build_tenants(user):
        # ohne Ordner – oder das Anlegen schlug zuletzt fehl (z. B. Freigabe kurz weg): erneut versuchen
        if not (t.get("repo_folder") or "").strip() or ("tenant", t["id"]) in _warned:
            try:
                ensure_tenant_folder(t["id"])
            except Exception:
                pass
    return [norm(t.get("repo_folder")) for t in access.build_tenants(user) if norm(t.get("repo_folder"))]


def need_scope(user, rel, own_root_ok=True):
    """Pfad prüfen und normalisieren. own_root_ok=False: der Mandantenordner selbst (bzw. das ganze Repository) ist
    nicht erlaubt – fürs Umbenennen, Verschieben und Löschen."""
    rel = "\\".join(_parts(rel))
    k = rel.lower()
    if k.split("\\")[0] == remote.UPLOAD_DIR:
        raise RepoError("Dieser Ordner gehört POCKET KNIFE.")
    rs = roots(user)
    if rs is None:
        if not own_root_ok and not rel:
            raise RepoError("Das ganze Repository lässt sich nicht umbenennen, verschieben oder löschen.")
        return rel
    if not rs:
        raise RepoError("Für Ihren Mandanten ist kein Repository-Ordner eingerichtet.", {"status": 403})
    for r in rs:
        rk = r.lower()
        if k == rk:
            if not own_root_ok:
                raise RepoError("Den Ordner Ihres Mandanten selbst können Sie nicht umbenennen, verschieben oder löschen.")
            return rel
        if k.startswith(rk + "\\"):
            return rel
    raise RepoError("Nur der Repository-Ordner Ihres Mandanten ist freigegeben.", {"status": 403})


def _kind_ci(rel):
    """Wie remote.repo_stat, aber ohne Rücksicht auf Groß-/Kleinschreibung (Windows-Freigaben unterscheiden nicht –
    ein lokales Repository unter Linux schon: dort darf „App“ neben „app“ nicht entstehen)."""
    k = remote.repo_stat(rel)
    if k is not None:
        return k
    par, name = _parent(rel), _base(rel)
    try:
        for it in remote.repo_list(par):
            if it["name"].lower() == name.lower():
                return "dir" if it["dir"] else "file"
    except remote.RemoteError:
        return None
    return None


def tenant_of_path(rel):
    """Mandant, in dessen Repository-Ordner der Pfad liegt (der längste passende) – 0 = zentral."""
    k = key(rel)
    best, tid = -1, 0
    for t in db.q("SELECT id, repo_folder FROM tenants WHERE COALESCE(repo_folder, '') != ''"):
        rk = key(t["repo_folder"])
        if rk and (k == rk or k.startswith(rk + "\\")) and len(rk) > best:
            best, tid = len(rk), t["id"]
    return tid


def scope_name(tid):
    if not tid:
        return "zentral"
    n = db.val("SELECT name FROM tenants WHERE id=?", (tid,))
    return f"Mandant {n}" if n else f"Mandant #{tid}"


def _log(user, msg, tid=None):
    if tid is None:
        tid = 0
    who = user.get("username") if isinstance(user, dict) else str(user or "")
    pklog.info("repo", msg + (f" [{scope_name(tid)}]" if tid else ""), user=who)


# --- Mandantenordner -------------------------------------------------------------------------------------------
def _safe_folder_name(name, tid):
    n = INVALID.sub("_", str(name or "")).strip().rstrip(". ")
    if not n or RESERVED.match(n) or n.startswith("."):
        n = f"Mandant-{tid}"
    return n[:80]


def ensure_tenant_folder(tid, create=True):
    """Darf der Mandant eigene Pakete bauen und hat keinen Repository-Ordner, bekommt er „Mandanten\\<Name>“ (ein Ordner
    je Mandant, alle seine Admins teilen ihn). Der Ordner wird im Repository angelegt, falls er fehlt."""
    t = db.q1("SELECT * FROM tenants WHERE id=?", (tid,))
    if not t or not t.get("allow_packages"):
        return None
    folder = norm(t.get("repo_folder"))
    if not folder:
        others = {key(r["repo_folder"]) for r in db.q("SELECT repo_folder FROM tenants WHERE id != ? AND "
                                                       "COALESCE(repo_folder, '') != ''", (tid,))}
        base = _safe_folder_name(t["name"], tid)
        cand, i = f"{TENANT_BASE}\\{base}", 2
        def taken(c):
            if any(c.lower() == o or c.lower().startswith(o + "\\") or o.startswith(c.lower() + "\\") for o in others):
                return True
            try:
                check_tenant_folder(c, tid)
            except RepoError:
                return True
            return False
        while taken(cand):
            cand, i = f"{TENANT_BASE}\\{base}-{i}", i + 1
        if db.ex_count("UPDATE tenants SET repo_folder=? WHERE id=? AND COALESCE(repo_folder, '')=''", (cand, tid)) != 1:
            return norm(db.val("SELECT repo_folder FROM tenants WHERE id=?", (tid,)))
        folder = cand
        pklog.info("repo", f"Mandant „{t['name']}“: Repository-Ordner „{folder}“ zugewiesen.")
        access.flush()
    if create:
        try:
            if remote.repo_stat(folder) != "dir":
                remote.repo_mkdir(folder)
                remote.repo_cache_apply("add", folder, isdir=True)
                pklog.info("repo", f"Repository-Ordner „{folder}“ für Mandant „{t['name']}“ angelegt.")
            _warned.discard(("tenant", tid))
        except Exception as e:
            if ("tenant", tid) not in _warned:
                _warned.add(("tenant", tid))
                pklog.warn("repo", f"Repository-Ordner „{folder}“ für Mandant „{t['name']}“ konnte nicht angelegt werden: {e}")
    return folder


def ensure_all_tenant_folders():
    for t in db.q("SELECT id FROM tenants WHERE allow_packages=1"):
        try:
            ensure_tenant_folder(t["id"])
        except Exception:  # pragma: no cover
            pass


def check_tenant_folder(folder, tid=None):
    """Zwei Mandanten dürfen sich keinen Ordner teilen (auch nicht ineinander verschachtelt) – sonst sähe und änderte
    der eine die Dateien des anderen."""
    k = key(folder)
    if not k:
        return
    for r in db.q("SELECT id, name, repo_folder FROM tenants WHERE COALESCE(repo_folder, '') != '' AND id != ?", (tid or 0,)):
        o = key(r["repo_folder"])
        if k == o or k.startswith(o + "\\") or o.startswith(k + "\\"):
            raise RepoError(f"Der Repository-Ordner überschneidet sich mit dem von Mandant „{r['name']}“ ({norm(r['repo_folder'])}).")
    # Ordner, die schon Pakete anderer Bereiche (zentral, andere Mandanten) benutzen, darf ein Mandant nicht bekommen –
    # seine Admins könnten sonst deren Dateien ändern oder etwas danebenlegen
    others = [h for h in usage(folder) if h[4][0] == "pkg" and h[4][3] != int(tid or 0) and
              (key(h[0]) == k or key(h[0]).startswith(k + "\\"))]
    if others:
        raise RepoError(f"Im Ordner „{norm(folder)}“ liegen Dateien von {usage_summary(others, 4)} – als Mandantenordner nicht möglich.")


# --- Wer benutzt welchen Pfad? ------------------------------------------------------------------------------------
_sid = itertools.count(1)


def _step_paths(stp, owner, out, _depth=0):
    """Alle Repository-Pfade eines Schritt-Baums (auch ausgeschaltete Schritte und Snapshots verschachtelter Pakete):
    out += [(pfad, art, haupt, schritt-id, besitzer)]."""
    if _depth > steps.MAX_DEPTH + 2 or not isinstance(stp, dict):
        return
    for lst in stp.values():
        if not isinstance(lst, list):
            continue
        for st in lst:
            if not isinstance(st, dict):
                continue
            t = st.get("type")
            sid = next(_sid)  # eindeutig je Schritt (id() eines kurzlebigen Objekts kann sich wiederholen)
            if t == "install" and st.get("source") == "file":
                folder = norm(st.get("folder"))
                f = (st.get("install_file") or "").strip()
                if st.get("copy_folder", True) and folder:
                    out.append((folder, "dir", False, sid, owner))
                if f:
                    out.append((_join(folder, f), "file", True, sid, owner))
            elif t == "filecopy" and st.get("source"):
                out.append((norm(st["source"]), "auto", True, sid, owner))
            elif t == "nested" and isinstance(st.get("package"), dict):
                _step_paths(st["package"].get("steps"), owner, out, _depth + 1)


def _pkg_steps(row):
    return steps.normalize(deploy.row_to_package(row))["steps"]


def all_refs():
    """[(pfad, art, haupt, schritt, besitzer)] – besitzer: („pkg“, id, name, tenant) / („dep“, id, name) /
    („tenant“, id, name) / („setting“, schlüssel, titel)."""
    out = []
    for r in db.q("SELECT * FROM packages"):
        try:
            _step_paths(_pkg_steps(r), ("pkg", r["id"], r["name"], int(r.get("tenant_id") or 0)), out)
        except Exception:
            continue
    for d in db.q("SELECT id, name, status, package_json FROM deployments WHERE status IN ('queued','scheduled','running')"):
        try:
            pj = json.loads(d["package_json"] or "{}")
        except ValueError:
            continue
        _step_paths(pj.get("steps"), ("dep", d["id"], d["name"], d["status"]), out)
    for t in db.q("SELECT id, name, repo_folder FROM tenants WHERE COALESCE(repo_folder, '') != ''"):
        out.append((norm(t["repo_folder"]), "tenant", False, None, ("tenant", t["id"], t["name"])))
    wb = norm(db.setting("winget_bootstrap_folder"))
    if wb:
        out.append((wb, "dir", True, None, ("setting", "winget_bootstrap_folder", "winget-Offline-Kopie (Einstellungen)")))
    return out


def _owner_text(o):
    if o[0] == "pkg":
        return f"Paket „{o[2]}“" + (f" ({scope_name(o[3])})" if o[3] else "")
    if o[0] == "dep":
        return f"{'laufende' if o[3] == 'running' else 'geplante'} Bereitstellung „{o[2]}“"
    if o[0] == "tenant":
        return f"Repository-Ordner von Mandant „{o[2]}“"
    return o[2]


def usage(rel, refs=None):
    """Wer benutzt den Pfad (die Datei, den Ordner mit allem darin, oder liegt er in einem benutzten Paketordner)?"""
    k = key(rel)
    hits = []
    for r, kind, main, sid, owner in (refs if refs is not None else all_refs()):
        rk = key(r)
        if rk == k or (k == "" and rk) or rk.startswith(k + "\\") or (kind in ("dir", "auto") and k.startswith(rk + "\\")):
            hits.append((r, kind, main, sid, owner))
    return hits


def usage_summary(hits, limit=8):
    seen, out = set(), []
    for _r, _k, _m, _s, o in hits:
        t = _owner_text(o)
        if t not in seen:
            seen.add(t)
            out.append(t)
    more = len(out) - limit
    return ", ".join(out[:limit]) + (f" und {more} weitere" if more > 0 else "")


def used_map(folder):
    """Für die Repository-Ansicht: {name klein: [„Paket X“, …]} der Einträge eines Ordners, die benutzt werden."""
    fk = key(folder)
    res = {}
    for r, kind, _m, _s, o in all_refs():
        if kind == "tenant":
            continue
        rk = key(r)
        if fk and not rk.startswith(fk + "\\"):
            continue
        rest = rk[len(fk) + 1:] if fk else rk
        if not rest:
            continue
        name = rest.split("\\")[0]
        res.setdefault(name, set()).add(_owner_text(o))
    return {k: sorted(v)[:10] for k, v in res.items()}


def _foreign_use(user, rel, refs=None):
    """OU-Admin: benutzen Pakete ANDERER Bereiche (zentral, andere Mandanten) oder Einstellungen diesen Pfad bzw.
    den Ordner, in dem er liegt? Dann darf er dort nichts ablegen, anlegen oder hinschieben. (Bereitstellungen sind
    Abzüge von Paketen – maßgeblich ist, wem das Paket gehört.)"""
    if not access.scoped(user):
        return []
    mine = set(access.tenant_ids(user) or [])
    return [h for h in usage(rel, refs) if h[4][0] not in ("tenant", "dep") and not (h[4][0] == "pkg" and h[4][3] in mine)]


def _block_foreign(user, rel, refs=None):
    hits = _foreign_use(user, rel, refs)
    if hits:
        raise Blocked(f"„{rel}“ liegt in einem Ordner, den {usage_summary(hits, 4)} benutzt – dort können nur Hauptadmins "
                      "bzw. dessen Besitzer etwas ablegen.", {"used_by": sorted({_owner_text(h[4]) for h in hits})})


def _block_if_used(rel, what):
    hits = usage(rel)
    if hits:
        raise Blocked(f"„{rel}“ wird noch benutzt – {what} nicht möglich: {usage_summary(hits)}.",
                      {"used_by": sorted({_owner_text(h[4]) for h in hits})})


# --- Pfade umschreiben ---------------------------------------------------------------------------------------------
def map_path(rel, pairs):
    """Neuer Pfad nach dem Verschieben (pairs = [(alt, neu)]) – None, wenn nicht betroffen."""
    p = _parts(rel)
    k = "\\".join(p).lower()
    for old, new in pairs:
        ok = key(old)
        if k == ok or k.startswith(ok + "\\"):
            n_old = len(_parts(old))
            return "\\".join(_parts(new) + p[n_old:])
    return None


def rewrite_steps(stp, pairs, _depth=0):
    """Pfade in einem Schritt-Baum anpassen (an Ort und Stelle). Gibt (geändert, probleme) zurück."""
    changed, problems = False, []
    if _depth > steps.MAX_DEPTH + 2 or not isinstance(stp, dict):
        return changed, problems
    for lst in stp.values():
        if not isinstance(lst, list):
            continue
        for st in lst:
            if not isinstance(st, dict):
                continue
            t = st.get("type")
            if t == "install" and st.get("source") == "file":
                folder = norm(st.get("folder"))
                f = (st.get("install_file") or "").strip()
                nf = map_path(folder, pairs) if folder else None
                if nf is not None:
                    st["folder"] = nf
                    changed = True
                elif f:
                    nfull = map_path(_join(folder, f), pairs)
                    if nfull is not None:
                        nd, nn = _parent(nfull), _base(nfull)
                        if key(nd) == key(folder):
                            st["install_file"] = nn
                            changed = True
                        elif not st.get("copy_folder", True):
                            st["folder"], st["install_file"] = nd, nn
                            changed = True
                        else:
                            problems.append(f"Installationsdatei „{f}“ würde aus ihrem Paketordner „{folder}“ verschoben")
            elif t == "filecopy" and st.get("source"):
                n = map_path(st["source"], pairs)
                if n is not None:
                    st["source"] = n
                    changed = True
            elif t == "nested" and isinstance(st.get("package"), dict):
                c, pr = rewrite_steps(st["package"].get("steps"), pairs, _depth + 1)
                changed, problems = changed or c, problems + pr
    return changed, problems


def _legacy_from_steps(stp):
    first = next((s for k, _ in steps.PHASES for s in (stp.get(k) or []) if s.get("type") == "install"), None)
    if first and first.get("source") == "file":
        return first.get("folder") or "", first.get("install_file") or ""
    return None


def _map_keys(table, pairs, extra_cols=True):
    """Schlüssel (Pfad klein) einer Tabelle nach dem Verschieben anpassen: repo_baseline, repo_baseline_dirs, repo_hash."""
    for old, new in pairs:
        ok = key(old)
        rows = db.q(f"SELECT * FROM {table} WHERE path=? OR (path >= ? AND path < ?)", (ok, ok + "\\", ok + "]"))
        for r in rows:
            np_ = map_path(r.get("disp") or r["path"], [(old, new)]) if extra_cols else map_path(r["path"], [(old, new)])
            if np_ is None:
                continue
            db.ex(f"DELETE FROM {table} WHERE path=?", (r["path"],))
            r = dict(r)
            r["path"] = np_.lower()
            if extra_cols:
                r["disp"] = np_
            cols = list(r)
            # ein schon vorhandener Stand am neuen Ort (Reparatur) bleibt – andere Pakete verlassen sich darauf
            db.ex(f"INSERT OR IGNORE INTO {table}({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
                  tuple(r[c] for c in cols))


def plan_rewrite(pairs, refs=None):
    """Was ändert sich in der Datenbank, wenn pairs verschoben werden? {packages, tenants, deployments, settings, problems}"""
    refs = refs if refs is not None else all_refs()
    tenants = {t["id"]: t for t in db.q("SELECT id, name, repo_folder FROM tenants")}
    out = {"packages": [], "tenants": [], "deployments": [], "settings": [], "problems": []}
    pk_ids = sorted({o[1] for _r, _k, _m, _s, o in refs if o[0] == "pkg"})
    new_tenant_folder = {}
    for tid, t in tenants.items():
        nf = map_path(t["repo_folder"], pairs) if norm(t.get("repo_folder")) else None
        if nf is not None:
            out["tenants"].append({"id": tid, "name": t["name"], "old": norm(t["repo_folder"]), "new": nf})
            new_tenant_folder[tid] = nf
    for pid in pk_ids:
        row = db.q1("SELECT * FROM packages WHERE id=?", (pid,))
        if not row:
            continue
        stp = _pkg_steps(row)
        before = []
        _step_paths(stp, None, before)
        changed, problems = rewrite_steps(stp, pairs)
        tid = int(row.get("tenant_id") or 0)
        for pr in problems:
            out["problems"].append(f"Paket „{row['name']}“: {pr}")
        if not changed:
            continue
        after = []
        _step_paths(stp, None, after)
        changes = list({(a[0], b[0]): {"old": a[0], "new": b[0]} for a, b in zip(before, after) if a[0] != b[0]}.values())
        # Pakete eines Mandanten müssen in dessen Ordner bleiben, zentrale dürfen nicht in einen Mandantenordner
        folder = new_tenant_folder.get(tid, norm(tenants.get(tid, {}).get("repo_folder"))) if tid else None
        for b in after:
            bk = key(b[0])
            if tid and folder and not (bk == key(folder) or bk.startswith(key(folder) + "\\")):
                out["problems"].append(f"Paket „{row['name']}“ ({scope_name(tid)}) läge dann außerhalb des Mandantenordners: {b[0]}")
                break
            if not tid:
                owner = _tenant_of_new(bk, tenants, new_tenant_folder)
                if owner:
                    out["problems"].append(f"Zentrales Paket „{row['name']}“ läge dann im Ordner von {scope_name(owner)} "
                                           f"(dort könnten dessen Admins die Dateien ändern): {b[0]}")
                    break
        out["packages"].append({"id": pid, "name": row["name"], "tenant": scope_name(tid), "steps": stp,
                                "changes": changes})
    # alle Bereitstellungen – auch abgeschlossene: „Erneut verteilen“/„Wiederholen“ nimmt deren Snapshot
    for d in db.q("SELECT id, name, status, package_json FROM deployments"):
        try:
            pj = json.loads(d["package_json"] or "{}")
        except ValueError:
            continue
        changed, _pr = rewrite_steps(pj.get("steps"), pairs)
        if changed:
            if d["status"] == "running":
                out["problems"].append(f"Bereitstellung „{d['name']}“ läuft gerade und benutzt diese Dateien – bitte warten.")
            out["deployments"].append({"id": d["id"], "name": d["name"], "json": pj, "status": d["status"]})
    wb = norm(db.setting("winget_bootstrap_folder"))
    if wb:
        n = map_path(wb, pairs)
        if n is not None:
            out["settings"].append({"key": "winget_bootstrap_folder", "old": wb, "new": n})
    out["deployments_open"] = sum(1 for d in out["deployments"] if d.get("status") in ("queued", "scheduled", "running"))
    return out


def _tenant_of_new(k, tenants, new_folder):
    for tid, t in tenants.items():
        f = key(new_folder.get(tid, t.get("repo_folder") or ""))
        if f and (k == f or k.startswith(f + "\\")):
            return tid
    return 0


def _apply_db(plan, pairs):
    """Änderungen aus plan_rewrite in EINER Transaktion schreiben."""
    with db.tx():
        for pk in plan["packages"]:
            # Zeile JETZT neu lesen und erneut umschreiben – wer das Paket inzwischen gespeichert hat, verliert nichts
            row = db.q1("SELECT * FROM packages WHERE id=?", (pk["id"],))
            if not row:
                continue
            stp = _pkg_steps(row)
            rewrite_steps(stp, pairs)
            leg = _legacy_from_steps(stp)
            if leg is not None:
                db.ex("UPDATE packages SET steps=?, folder=?, install_file=?, updated_at=? WHERE id=?",
                      (json.dumps(stp, ensure_ascii=False), leg[0], leg[1], util.now(), pk["id"]))
            else:
                db.ex("UPDATE packages SET steps=?, updated_at=? WHERE id=?",
                      (json.dumps(stp, ensure_ascii=False), util.now(), pk["id"]))
        for t in plan["tenants"]:
            db.ex("UPDATE tenants SET repo_folder=? WHERE id=?", (t["new"], t["id"]))  # nur Hauptadmins (OU: eigener Ordner gesperrt)
        for d in plan["deployments"]:
            cur = db.val("SELECT package_json FROM deployments WHERE id=?", (d["id"],))
            try:
                pj = json.loads(cur or "{}")
            except ValueError:
                continue
            rewrite_steps(pj.get("steps"), pairs)
            db.ex("UPDATE deployments SET package_json=? WHERE id=?", (json.dumps(pj, ensure_ascii=False), d["id"]))
        for s in plan["settings"]:
            db.set_setting(s["key"], s["new"])
        _map_keys("repo_baseline", pairs)
        _map_keys("repo_hash", pairs)
        _map_keys("repo_baseline_dirs", pairs, extra_cols=False)
    if plan["tenants"]:
        access.flush()


def _public_plan(plan):
    return {"packages": [{"id": p["id"], "name": p["name"], "tenant": p["tenant"], "changes": p["changes"]} for p in plan["packages"]],
            "tenants": [{k: t[k] for k in ("id", "name", "old", "new")} for t in plan["tenants"]],
            "deployments": [{"id": d["id"], "name": d["name"]} for d in plan["deployments"]],
            "settings": plan["settings"], "problems": plan["problems"]}


def _recheck(pids):
    def run():
        for pid in pids:
            try:
                integrity.check_package(pid, live=True)
            except Exception:  # pragma: no cover
                pass
    if pids:
        threading.Thread(target=run, name="pk-repo-recheck", daemon=True).start()


# --- Ordner, Umbenennen, Verschieben, Löschen -----------------------------------------------------------------------
def mkdir(user, folder, name):
    folder = need_scope(user, folder)
    name = valid_name(name)
    rel = _join(folder, name)
    need_scope(user, rel)
    _block_foreign(user, rel)
    with _write_lock:
        kind = _kind_ci(rel)
        if kind is not None:
            raise RepoError(f"„{name}“ gibt es hier schon.")
        if folder and remote.repo_stat(folder) != "dir":
            raise RepoError(f"Ordner „{folder}“ gibt es nicht (mehr).")
        remote.repo_mkdir(rel)
    remote.repo_cache_apply("add", rel, isdir=True)
    _log(user, f"Repository: Ordner „{rel}“ angelegt.", tenant_of_path(rel))
    return {"path": rel}


def mkdirs(user, folder, rel_dirs):
    """Ordnerbaum für einen Ordner-Upload anlegen (vorhandene sind kein Fehler)."""
    folder = need_scope(user, folder)
    made = 0
    for d in sorted({"\\".join(_parts(x)) for x in rel_dirs or [] if _parts(x)}, key=lambda x: x.count("\\")):
        for n in _parts(d):
            valid_name(n)
        rel = _join(folder, d)
        need_scope(user, rel)
        if remote.repo_stat(rel) == "dir":
            continue
        _block_foreign(user, rel)
        with _write_lock:
            remote.repo_mkdir(rel)
        remote.repo_cache_apply("add", rel, isdir=True)
        made += 1
    if made:
        _log(user, f"Repository: {made} Ordner unter „{folder or '(Hauptordner)'}“ angelegt.", tenant_of_path(folder))
    return {"created": made}


def _check_pairs(user, pairs):
    """Quellen und Ziele prüfen: Bereich, vorhanden/frei, nichts in sich selbst, keine doppelten Quellen."""
    out = []
    for src, dst in pairs:
        src = need_scope(user, src, own_root_ok=False)
        dst = need_scope(user, dst, own_root_ok=False)
        valid_name(_base(dst))
        if access.scoped(user) and _base(src).lower() != _base(dst).lower() and remote.repo_stat(src) == "file" \
                and not _ext_ok(user, _base(dst)):
            raise RepoError(f"Dateityp von „{_base(dst)}“ ist für Mandanten nicht freigegeben.")
        sk, dk = src.lower(), dst.lower()
        if src == dst:
            continue
        if dk.startswith(sk + "\\"):
            raise RepoError(f"„{src}“ lässt sich nicht in sich selbst verschieben.")
        out.append((src, dst))
    for i, (a, _x) in enumerate(out):
        for j, (b, _y) in enumerate(out):
            if i != j and (a.lower() == b.lower() or a.lower().startswith(b.lower() + "\\")):
                raise RepoError(f"„{a}“ ist doppelt bzw. in „{b}“ enthalten – bitte nur den übergeordneten Ordner wählen.")
    dsts = [d.lower() for _s, d in out]
    if len(set(dsts)) != len(dsts):
        raise RepoError("Zwei Einträge hätten danach denselben Namen.")
    return out


def _moving_out_of_used(pairs, refs):
    """Datei/Ordner liegt IN einem benutzten Paketordner (und ist nicht selbst die Installationsdatei dieses Schritts):
    Umbenennen oder Verschieben würde das Paket stillschweigend ändern."""
    probs = []
    for src, _dst in pairs:
        sk = key(src)
        same_step = {sid for r, kind, _m, sid, _o in refs if kind == "file" and key(r) == sk}
        for r, kind, _m, sid, o in refs:
            rk = key(r)
            if kind in ("dir", "auto") and sk.startswith(rk + "\\") and sid not in same_step:
                probs.append(f"„{src}“ liegt im Ordner „{r}“, den {_owner_text(o)} benutzt – das Paket würde sich ändern. "
                             "Bitte den ganzen Ordner verschieben oder das Paket anpassen.")
                break
    return probs


def move(user, pairs, dry_run=True, label="vor-repo-umbau", assume_dirs=()):
    """Verschieben/Umbenennen (pairs = [(quelle, ziel)] als volle relative Pfade). dry_run: nur Vorschau.
    Ausführen: Sicherung (wenn sich in der Datenbank etwas ändert), Dateien verschieben, Datenbank in einer Transaktion
    anpassen – bei einem Fehler wird alles zurückgedreht."""
    from . import backup
    with _write_lock:
        pairs = _check_pairs(user, pairs)
        if not pairs:
            raise RepoError("Nichts zu verschieben.")
        problems = []
        for src, dst in pairs:
            if remote.repo_stat(src) is None:
                problems.append(f"„{src}“ gibt es nicht (mehr).")
            if _kind_ci(dst) is not None and dst.lower() != src.lower():
                problems.append(f"„{dst}“ gibt es schon.")
            par = _parent(dst)
            if par and par.lower() not in assume_dirs and remote.repo_stat(par) != "dir":
                problems.append(f"Zielordner „{par}“ gibt es nicht.")
        refs = all_refs()
        # Ordner eines Mandanten verschieben (nur Hauptadmin): erlaubt, sein Repository-Ordner zieht mit
        problems += _moving_out_of_used(pairs, refs)
        for _src, dst in pairs:
            hits = _foreign_use(user, dst, refs)
            if hits:
                problems.append(f"Ziel „{_parent(dst) or dst}“ benutzt {usage_summary(hits, 3)} – dort können Sie nichts hinschieben.")
        plan = plan_rewrite(pairs, refs)
        # Neuer Mandantenordner darf sich nicht mit einem anderen überschneiden
        newt = {t["id"]: t["new"] for t in plan["tenants"]}
        allt = {r["id"]: newt.get(r["id"], norm(r["repo_folder"])) for r in db.q(
            "SELECT id, repo_folder FROM tenants WHERE COALESCE(repo_folder, '') != ''")}
        for a, fa in allt.items():
            for b, fb in allt.items():
                if a < b and (key(fa) == key(fb) or key(fa).startswith(key(fb) + "\\") or key(fb).startswith(key(fa) + "\\")):
                    problems.append(f"Danach überschnitten sich die Ordner von {scope_name(a)} und {scope_name(b)}.")
        # Etwas in einen fremden Mandantenordner schieben (Hauptadmin) – fremde Admins könnten es dann ändern
        for src, dst in pairs:
            ts, td = tenant_of_path(src), _tenant_of_new(key(dst), {r["id"]: r for r in db.q(
                "SELECT id, repo_folder FROM tenants")}, newt)
            if td and ts != td and any(h[4][0] == "pkg" and h[4][3] != td for h in usage(src, refs)):
                problems.append(f"„{src}“ wird von Paketen außerhalb von {scope_name(td)} benutzt und käme in dessen Ordner.")
        pub = _public_plan(plan)
        pub["problems"] = list(dict.fromkeys(problems + plan["problems"]))
        pub["pairs"] = [{"old": s, "new": d} for s, d in pairs]
        if dry_run or pub["problems"]:
            pub["dry_run"] = True
            return pub
        touches_db = bool(plan["packages"] or plan["tenants"] or plan["deployments"] or plan["settings"])
        if touches_db:
            try:
                b = backup.create(label=label, user=user.get("username"))
                pub["backup"] = b.get("name")
            except Exception as e:
                raise RepoError(f"Sicherung vor dem Umbau fehlgeschlagen – nichts geändert: {e}")
        done = []
        integrity.moved()
        try:
            for src, dst in pairs:
                remote.repo_rename(src, dst)
                done.append((src, dst))
            _apply_db(plan, pairs)
        except Exception as e:
            # Was ist wirklich passiert? Ein Fehler nach dem Umbenennen auf dem Server (z. B. Zeitüberschreitung) –
            # dann liegt die Datei schon am Ziel, obwohl der Aufruf fehlschlug
            for src, dst in pairs:
                if (src, dst) not in done:
                    try:
                        if remote.repo_stat(dst) is not None and remote.repo_stat(src) is None:
                            done.append((src, dst))
                    except Exception:
                        pass
            undo_err = []
            for src, dst in reversed(done):
                try:
                    remote.repo_rename(dst, src)
                except Exception as e2:  # pragma: no cover
                    undo_err.append(f"{dst} → {src}: {e2}")
            if undo_err:
                pklog.error("repo", "Zurückdrehen unvollständig: " + "; ".join(undo_err), user=user.get("username"))
                raise RepoError(f"Verschieben fehlgeschlagen ({e}). ACHTUNG: nicht alles ließ sich zurückdrehen: "
                                + "; ".join(undo_err) + (f" – Sicherung: {pub.get('backup')}" if pub.get("backup") else ""))
            raise RepoError(f"Verschieben fehlgeschlagen, alles zurückgedreht: {e}")
    integrity.moved()
    for src, dst in pairs:
        remote.repo_cache_apply("move", src, new=dst)
        _log(user, f"Repository: „{src}“ → „{dst}“ verschoben.", tenant_of_path(dst))
    if touches_db:
        _log(user, f"Repository umgebaut: {len(plan['packages'])} Paket(e), {len(plan['tenants'])} Mandant(en), "
                   f"{len(plan['deployments'])} geplante Bereitstellung(en) angepasst.")
    _recheck([p["id"] for p in plan["packages"]])
    pub["dry_run"] = False
    return pub


def rename(user, path, new_name, dry_run=False):
    path = need_scope(user, path, own_root_ok=False)
    new_name = valid_name(new_name)
    return move(user, [(path, _join(_parent(path), new_name))], dry_run=dry_run, label="vor-umbenennen")


def move_into(user, items, dest, dry_run=True, create_dest=False):
    dest = need_scope(user, dest)
    pairs = [(s, _join(dest, _base(s))) for s in items or []]
    if not (create_dest and dest and remote.repo_stat(dest) is None):
        return move(user, pairs, dry_run=dry_run)
    for n in _parts(dest):
        valid_name(n)
    # Zielordner fehlt: Vorschau so, als gäbe es ihn; beim Ausführen erst anlegen (und bei Problemen wieder entfernen)
    pub = move(user, pairs, dry_run=True, assume_dirs={dest.lower()})
    pub["create_dest"] = dest
    if dry_run or pub["problems"]:
        return pub
    with _write_lock:
        remote.repo_mkdir(dest)
        remote.repo_cache_apply("add", dest, isdir=True)
        _log(user, f"Repository: Ordner „{dest}“ angelegt.", tenant_of_path(dest))
        def drop_empty():
            try:
                if not remote.repo_list(dest):
                    remote.repo_delete(dest)
                    remote.repo_cache_apply("remove", dest)
            except Exception:
                pass
        try:
            res = move(user, pairs, dry_run=False)
        except Exception:
            drop_empty()
            raise
        if res.get("problems"):
            drop_empty()
    res["create_dest"] = dest
    return res


def delete(user, items):
    out = []
    with _write_lock:
        refs = all_refs()
        for it in items:
            rel = need_scope(user, it, own_root_ok=False)
            hits = usage(rel, refs)
            if hits:
                raise Blocked(f"„{rel}“ wird noch benutzt – Löschen nicht möglich: {usage_summary(hits)}.",
                              {"used_by": sorted({_owner_text(h[4]) for h in hits})})
        for it in items:
            rel = need_scope(user, it, own_root_ok=False)
            if remote.repo_delete(rel):
                db.ex("DELETE FROM repo_hash WHERE path=? OR (path >= ? AND path < ?)", (rel.lower(), rel.lower() + "\\", rel.lower() + "]"))
                remote.repo_cache_apply("remove", rel)
                _log(user, f"Repository: „{rel}“ gelöscht.", tenant_of_path(rel))
                out.append(rel)
    return {"deleted": out}


# --- Upload ----------------------------------------------------------------------------------------------------------
def _ext_ok(user, name):
    if not access.scoped(user):
        return True
    allowed = {x.strip().lower().lstrip(".") for x in str(db.setting("repo_upload_types") or "").split(",") if x.strip()}
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return ext in allowed


def quota_bytes(tid):
    if not tid:
        return 0
    q = db.val("SELECT quota_mb FROM tenants WHERE id=?", (tid,))
    if q is None:
        q = db.setting("repo_tenant_quota_mb") or 0
    return int(q or 0) * 1024 * 1024


def used_bytes(folder):
    idx = remote.repo_index_files()
    fk = key(folder)
    if idx is not None:
        files, dirs = idx
        if fk in dirs:
            return sum(v[1] for k, v in files.items() if k.startswith(fk + "\\"))
    lst = remote.repo_tree(folder, max_files=200000)
    return sum(x[1] for x in lst or [])


def _tmp_rel(token):
    return f"{remote.UPLOAD_DIR}\\{token}.part"


def _up_lock(token):
    with _up_locks_lock:
        return _up_locks.setdefault(token, threading.Lock())


def cleanup_uploads():
    """Abgebrochene Uploads nach UPLOAD_STALE_H Stunden aufräumen (Zwischendatei und Eintrag)."""
    rows = db.q("SELECT * FROM repo_uploads")
    n = 0
    for r in rows:
        try:
            age = time.time() - util.parse(r["updated"]).timestamp()
        except Exception:
            age = UPLOAD_STALE_H * 3600 + 1
        if age > UPLOAD_STALE_H * 3600:
            try:
                remote.repo_delete(_tmp_rel(r["token"]))
            except Exception:
                pass
            db.ex("DELETE FROM repo_uploads WHERE token=?", (r["token"],))
            _hashers.pop(r["token"], None)
            n += 1
    return n


def upload_start(user, folder, name, size, overwrite=False, allow_dup=False):
    folder = need_scope(user, folder)
    name = valid_name(name)
    rel = _join(folder, name)
    need_scope(user, rel)
    try:
        size = int(size)
    except (TypeError, ValueError):
        raise RepoError("Dateigröße fehlt.")
    if size < 0:
        raise RepoError("Ungültige Dateigröße.")
    mx = int(db.setting("repo_upload_max_mb") or 0) * 1024 * 1024
    if mx and size > mx:
        raise RepoError(f"„{name}“ ist zu groß ({size // 1048576} MB, höchstens {mx // 1048576} MB – Einstellungen › Repository).")
    if not _ext_ok(user, name):
        raise RepoError(f"Dateityp von „{name}“ ist für Mandanten nicht freigegeben (erlaubt: {db.setting('repo_upload_types')}).")
    if folder and remote.repo_stat(folder) != "dir":
        raise RepoError(f"Ordner „{folder}“ gibt es nicht (mehr).")
    username = user["username"]
    # Fortsetzen: derselbe Benutzer, dieselbe Datei, dieselbe Größe
    old = db.q1("SELECT * FROM repo_uploads WHERE username=? AND lower(folder)=? AND lower(name)=? AND size=?",
                (username, folder.lower(), name.lower(), size))
    kind = _kind_ci(rel)
    if kind == "dir":
        raise RepoError(f"„{name}“ ist hier ein Ordner.")
    if kind == "file":
        if not overwrite:
            raise RepoError(f"„{name}“ gibt es hier schon.", {"exists": True})
        _block_if_used(rel, "Überschreiben")
    _block_foreign(user, rel)
    tid = tenant_of_path(rel)
    q = quota_bytes(tid) if access.scoped(user) else 0
    if q:
        tf = norm(db.val("SELECT repo_folder FROM tenants WHERE id=?", (tid,)))
        pend = sum(int(r["size"] or 0) for r in db.q("SELECT folder, size, token FROM repo_uploads")
                   if tenant_of_path(r["folder"]) == tid and (not old or r["token"] != old["token"]))
        replaced = 0
        if kind == "file":
            st = remote.repo_fstat(rel)
            replaced = st[0] if st else 0
        used = used_bytes(tf)
        if used + pend + size - replaced > q:
            raise RepoError(f"Speicherkontingent von {scope_name(tid)} reicht nicht: belegt {used // 1048576} MB"
                            + (f" (+ {pend // 1048576} MB in laufenden Uploads)" if pend else "")
                            + f", Kontingent {q // 1048576} MB, Datei {size // 1048576} MB.")
    if old:
        st = remote.repo_fstat(_tmp_rel(old["token"]))
        got = st[0] if st else 0
        if got != int(old["received"] or 0) or got > size:
            got = 0
            try:
                remote.repo_delete(_tmp_rel(old["token"]))
            except Exception:
                pass
            _hashers.pop(old["token"], None)
        db.ex("UPDATE repo_uploads SET received=?, overwrite=?, allow_dup=?, updated=? WHERE token=?",
              (got, 1 if overwrite else 0, 1 if allow_dup and not access.scoped(user) else 0, util.now(), old["token"]))
        return {"token": old["token"], "received": got, "chunk": CHUNK, "resumed": got > 0}
    token = secrets.token_hex(16)
    if remote.repo_stat(remote.UPLOAD_DIR) != "dir":
        remote.repo_mkdir(remote.UPLOAD_DIR)
    with remote.repo_open_write(_tmp_rel(token), "wb"):
        pass
    db.ex("INSERT INTO repo_uploads(token, username, folder, name, size, received, overwrite, allow_dup, created, updated) "
          "VALUES(?,?,?,?,?,0,?,?,?,?)", (token, username, folder, name, size, 1 if overwrite else 0,
                                           1 if allow_dup and not access.scoped(user) else 0, util.now(), util.now()))
    return {"token": token, "received": 0, "chunk": CHUNK, "resumed": False}


def _session(user, token):
    r = db.q1("SELECT * FROM repo_uploads WHERE token=?", (str(token or ""),))
    if not r or r["username"] != user["username"]:
        raise RepoError("Upload nicht gefunden (abgelaufen?) – bitte neu starten.", {"status": 404})
    need_scope(user, r["folder"])
    return r


def _hasher(token, received):
    h = _hashers.get(token)
    if h and h[1] == received:
        return h[0]
    hh = hashlib.sha256()
    done = 0
    if received:
        with remote.repo_open_rel(_tmp_rel(token)) as fh:
            while done < received:
                buf = fh.read(min(1024 * 1024, received - done))
                if not buf:
                    break
                hh.update(buf)
                done += len(buf)
        if done != received:
            raise RepoError("Zwischendatei des Uploads ist unvollständig – bitte neu starten.", {"restart": True})
    _hashers[token] = (hh, received)
    return hh


def upload_chunk(user, token, offset, data):
    with _up_lock(token):
        r = _session(user, token)
        rec = int(r["received"] or 0)
        if int(offset) != rec:
            raise RepoError(f"Falsche Position (Server hat {rec} Byte).", {"received": rec, "status": 409})
        if not data:
            return {"received": rec}
        if rec + len(data) > int(r["size"]):
            raise RepoError("Mehr Daten als angekündigt.")
        h = _hasher(token, rec)
        remote.repo_write_at(_tmp_rel(token), rec, data)
        h.update(data)
        rec += len(data)
        _hashers[token] = (h, rec)
        db.ex("UPDATE repo_uploads SET received=?, updated=? WHERE token=?", (rec, util.now(), token))
        return {"received": rec}


def upload_cancel(user, token):
    with _up_lock(token):
        r = _session(user, token)
        try:
            remote.repo_delete(_tmp_rel(r["token"]))
        except Exception:
            pass
        db.ex("DELETE FROM repo_uploads WHERE token=?", (r["token"],))
        _hashers.pop(r["token"], None)
    return {"ok": True}


def upload_finish(user, token, allow_dup=False):
    with _up_lock(token):
        r = _session(user, token)
        size, rec = int(r["size"]), int(r["received"] or 0)
        if rec != size:
            raise RepoError(f"Upload unvollständig ({rec} von {size} Byte).", {"received": rec})
        st = remote.repo_fstat(_tmp_rel(r["token"]))
        if not st or st[0] != size:
            raise RepoError("Zwischendatei passt nicht zur Größe – bitte neu hochladen.", {"restart": True})
        sha = _hasher(r["token"], rec).hexdigest()
        rel = _join(r["folder"], r["name"])
        tid = tenant_of_path(rel)
        info = {"sha256": sha, "path": rel}
        msi_info = None
        if r["name"].lower().endswith((".msi", ".msp")):
            try:
                from . import msi
                with remote.repo_open_rel(_tmp_rel(r["token"])) as fh:
                    m = msi.read(fh)
                msi_info = {"product_code": (m.get("product_code") or "").upper(), "version": m.get("version") or "",
                            "name": m.get("name") or ""}
                info["msi"] = msi_info
            except Exception:
                pass
        ids = {("sha256", sha)}
        if msi_info and msi_info["product_code"] and r["name"].lower().endswith(".msi"):
            ids.add(("msi", msi_info["product_code"]))
        conflicts = find_conflicts(ids, tid)
        if conflicts:
            if (allow_dup or r["allow_dup"]) and not access.scoped(user):
                pklog.warn("repo", f"Duplikatsperre übersteuert beim Upload „{rel}“: {_conflict_text(conflicts)}",
                           user=user["username"])
            elif access.scoped(user):
                _discard(r["token"])
                raise Duplicate(_dup_message(conflicts, user, upload=True), _dup_info(conflicts, user))
            else:
                # Hauptadmin: Datei bleibt in der Zwischenablage – „Trotzdem hochladen“ schließt ohne neues Hochladen ab
                info = _dup_info(conflicts, user)
                info["token"] = r["token"]
                raise Duplicate(_dup_message(conflicts, user, upload=True), info)
        same = [x["disp"] for x in db.q("SELECT disp FROM repo_hash WHERE sha256=? AND path != ? LIMIT 5", (sha, rel.lower()))]
        if same:
            info["same_file_at"] = [p for p in same if _visible_path(user, p)]
        with _write_lock:
            kind = remote.repo_stat(rel)
            if kind == "dir":
                raise RepoError(f"„{r['name']}“ ist inzwischen ein Ordner.")
            if kind == "file":
                if not r["overwrite"]:
                    raise RepoError(f"„{r['name']}“ gibt es inzwischen schon.", {"exists": True})
                _block_if_used(rel, "Überschreiben")
                remote.repo_delete(rel)
            remote.repo_rename(_tmp_rel(r["token"]), rel)
        db.ex("DELETE FROM repo_uploads WHERE token=?", (r["token"],))
        _hashers.pop(r["token"], None)
    st = remote.repo_fstat(rel) or (size, int(time.time()))
    db.ex("INSERT OR REPLACE INTO repo_hash(path, disp, size, mtime, sha256, msi_product, msi_version, at) VALUES(?,?,?,?,?,?,?,?)",
          (rel.lower(), rel, st[0], st[1], sha, (msi_info or {}).get("product_code"), (msi_info or {}).get("version"), util.now()))
    remote.repo_cache_apply("add", rel, size=st[0], mtime=st[1])
    hits = usage(rel)
    if hits:
        info["in_package_folder"] = usage_summary(hits)
    _log(user, f"Repository: „{rel}“ hochgeladen ({_mb(size)}" + (", überschrieben" if r["overwrite"] and kind == "file" else "")
         + f", SHA-256 {sha[:12]}…).", tid)
    return info


def _discard(token):
    try:
        remote.repo_delete(_tmp_rel(token))
    except Exception:
        pass
    db.ex("DELETE FROM repo_uploads WHERE token=?", (token,))
    _hashers.pop(token, None)


def _mb(n):
    return f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{max(1, n // 1024)} KB"


def _visible_path(user, p):
    rs = None if not access.scoped(user) else [key(t.get("repo_folder")) for t in access.build_tenants(user) if norm(t.get("repo_folder"))]
    if rs is None:
        return True
    k = key(p)
    return any(k == r or k.startswith(r + "\\") for r in rs)


# --- Duplikatsperre ----------------------------------------------------------------------------------------------
def _pc(v):
    v = str(v or "").strip().upper()
    if v and not v.startswith("{"):
        v = "{" + v.strip("{}") + "}"
    return v if re.match(r"^\{[0-9A-F-]{36}\}$", v) else ""


def _file_sha(rel, size_mtime):
    """Bekannter Fingerabdruck einer Repository-Datei (Upload oder Bruchprüfung) – ohne neu zu rechnen."""
    k = key(rel)
    size, mtime = size_mtime
    for tbl, scol, mcol in (("repo_hash", "size", "mtime"), ("repo_baseline", "size", "mtime")):
        r = db.q1(f"SELECT * FROM {tbl} WHERE path=?", (k,))
        if r and r.get("sha256") and r[scol] == size and abs((r[mcol] or 0) - (mtime or 0)) <= integrity.TOL:
            return r["sha256"]
        if tbl == "repo_baseline" and r and r.get("cur_sha256") and r.get("cur_size") == size and \
                abs((r.get("cur_mtime") or 0) - (mtime or 0)) <= integrity.TOL:
            return r["cur_sha256"]
    return None


def _msi_code(rel, size_mtime):
    k = key(rel)
    r = db.q1("SELECT * FROM repo_hash WHERE path=?", (k,))
    if r and r.get("msi_product") is not None and r["size"] == size_mtime[0] and abs((r["mtime"] or 0) - size_mtime[1]) <= integrity.TOL:
        return r["msi_product"]
    try:
        from . import msi
        with remote.repo_open_rel(rel) as fh:
            m = msi.read(fh)
        code = _pc(m.get("product_code"))
    except Exception:
        return None
    if r:
        db.ex("UPDATE repo_hash SET msi_product=?, msi_version=? WHERE path=?", (code, m.get("version") or "", k))
    else:
        db.ex("INSERT OR REPLACE INTO repo_hash(path, disp, size, mtime, sha256, msi_product, msi_version, at) VALUES(?,?,?,?,NULL,?,?,?)",
              (k, norm(rel), size_mtime[0], size_mtime[1], code, m.get("version") or "", util.now()))
    return code


def idents(p, io=True):
    """Kennzeichen eines Pakets: {(art, wert)} und ob noch etwas fehlt (Fingerabdruck noch nicht berechnet).
    Zählen nur eingeschaltete Installationsschritte (verschachtelte Pakete haben eigene Kennzeichen)."""
    out, pending = set(), False
    try:
        p = dict(p)
        for k in ("steps", "conditions"):
            if isinstance(p.get(k), str):
                p[k] = json.loads(p[k])
        stp = steps.normalize(p)["steps"]
    except Exception:
        return out, False
    for k, _ in steps.PHASES:
        for st in stp.get(k) or []:
            if st.get("type") != "install" or not st.get("enabled", True):
                continue
            if st.get("source") == "winget":
                if (st.get("winget_action") or "install") in ("install", "upgrade") and st.get("winget_id"):
                    out.add(("winget", st["winget_id"].strip().lower()))
                continue
            f = (st.get("install_file") or "").strip()
            if not f:
                continue
            rel = _join(st.get("folder"), f)
            pc = _pc(st.get("product_code"))
            if pc:
                out.add(("msi", pc))
            if not io:
                continue
            try:
                sm = remote.repo_fstat(rel)
            except Exception:
                pending = True
                continue
            if not sm:
                continue
            sha = _file_sha(rel, sm)
            if sha:
                out.add(("sha256", sha))
            else:
                pending = True
                if db.val("SELECT 1 FROM repo_baseline WHERE path=?", (key(rel),)):
                    integrity.enqueue(key(rel))  # der Hintergrund rechnet ihn, backfill trägt ihn dann nach
            if not pc and f.lower().endswith(".msi"):
                code = _msi_code(rel, sm)
                if code:
                    out.add(("msi", code))
    return out, pending


def find_conflicts(ids, scope, exclude_pid=None):
    """Pakete mit gleichen Kennzeichen in einem ANDEREN Bereich (zentral = 0, sonst Mandanten-ID)."""
    out = []
    for kind, value in ids:
        for r in db.q("SELECT p.id, p.name, p.tenant_id FROM pkg_ident i JOIN packages p ON p.id=i.package_id "
                      "WHERE i.kind=? AND i.value=?", (kind, value)):
            if exclude_pid and r["id"] == exclude_pid:
                continue
            if int(r["tenant_id"] or 0) != int(scope or 0):
                out.append({"id": r["id"], "name": r["name"], "tenant_id": int(r["tenant_id"] or 0), "kind": kind, "value": value})
    seen, res = set(), []
    for c in out:
        if (c["id"], c["kind"]) not in seen:
            seen.add((c["id"], c["kind"]))
            res.append(c)
    return res


KIND_TEXT = {"winget": "gleiche winget-ID", "msi": "gleicher MSI-Produktcode", "sha256": "gleiche Installationsdatei (SHA-256)"}


def _conflict_text(conf):
    return "; ".join(f"„{c['name']}“ ({scope_name(c['tenant_id'])}, {KIND_TEXT.get(c['kind'], c['kind'])})" for c in conf[:5])


def _dup_message(conf, user, upload=False):
    c = conf[0]
    msg = (f"Diese Software gibt es schon: „{c['name']}“ ({scope_name(c['tenant_id'])}, {KIND_TEXT.get(c['kind'], c['kind'])})"
           + (f" und {len(conf) - 1} weitere" if len(conf) > 1 else "") + ". ")
    msg += ("Bitte das vorhandene Paket verwenden – verteilen dürfen Sie alle Pakete." if access.scoped(user) else
            "Bitte das vorhandene Paket verwenden – als Hauptadmin können Sie die Sperre übersteuern („Trotzdem …“).")
    if upload:
        msg = "Hochladen abgelehnt. " + msg
    return msg


def _dup_info(conf, user):
    return {"duplicate": [{"id": c["id"], "name": c["name"], "scope": scope_name(c["tenant_id"]), "kind": c["kind"]} for c in conf[:10]],
            "can_override": not access.scoped(user)}


def guard_package(p, pid, user, allow=False, io=True):
    """Vor dem Speichern: neue bzw. geänderte Kennzeichen gegen andere Bereiche prüfen (vorhandene Altfälle blockieren
    das Speichern anderer Änderungen nicht). Gibt (kennzeichen, fehlt_noch) zum Speichern zurück."""
    ids, pending = idents(p, io=io)
    if not io:
        pending = True  # Fingerabdruck/MSI ohne Zugriff auf die Freigabe nicht geprüft – backfill trägt nach
    old = {(r["kind"], r["value"]) for r in db.q("SELECT kind, value FROM pkg_ident WHERE package_id=?", (pid,))} if pid else set()
    if pid:
        # Kennzeichen des bisher gespeicherten Pakets zählen als „alt“ – auch solche, die erst inzwischen bekannt sind
        # (Fingerabdruck nachträglich berechnet): ein Altfall blockiert keine andere Änderung
        row = db.q1("SELECT * FROM packages WHERE id=?", (pid,))
        if row:
            old |= idents(deploy.row_to_package(row), io=io)[0]
    old_scope = db.val("SELECT COALESCE(tenant_id, 0) FROM packages WHERE id=?", (pid,), 0) if pid else None
    scope = int(p.get("tenant_id") or 0)
    check = ids if (not pid or int(old_scope or 0) != scope) else ids - old
    conf = find_conflicts(check, scope, exclude_pid=pid)
    if conf:
        if allow and not access.scoped(user):
            pklog.warn("deploy", f"Duplikatsperre übersteuert für Paket „{p.get('name')}“: {_conflict_text(conf)}",
                       user=user.get("username"))
        else:
            raise Duplicate(_dup_message(conf, user), _dup_info(conf, user))
    return ids, pending


def store_idents(pid, ids, pending):
    """Kennzeichen speichern (ohne eigene Transaktion – läuft auch innerhalb des Paket-Imports)."""
    db.ex("DELETE FROM pkg_ident WHERE package_id=?", (pid,))
    db.exmany("INSERT OR IGNORE INTO pkg_ident(package_id, kind, value) VALUES(?,?,?)", [(pid, k, v) for k, v in ids])
    db.ex("UPDATE packages SET ident_at=? WHERE id=?", (None if pending else util.now(), pid))


def forget_idents(pid):
    db.ex("DELETE FROM pkg_ident WHERE package_id=?", (pid,))


def backfill(limit=100, seconds=60):
    """Kennzeichen nachtragen (Pakete von vor 2.27, Fingerabdruck inzwischen berechnet). Neue Duplikate über Bereiche
    hinweg lassen sich nachträglich nicht mehr sperren – sie kommen ins Protokoll."""
    t0 = time.time()
    n = 0
    for r in db.q("SELECT * FROM packages WHERE ident_at IS NULL OR ident_at < COALESCE(updated_at, '') ORDER BY id LIMIT ?", (limit,)):
        if time.time() - t0 > seconds:
            break
        p = deploy.row_to_package(r)
        ids, pending = idents(p)
        old = {(x["kind"], x["value"]) for x in db.q("SELECT kind, value FROM pkg_ident WHERE package_id=?", (r["id"],))}
        new = ids - old
        conf = find_conflicts(new, int(r.get("tenant_id") or 0), exclude_pid=r["id"]) if new else []
        if conf:
            pklog.warn("deploy", f"Mögliches Duplikat: Paket „{r['name']}“ ({scope_name(int(r.get('tenant_id') or 0))}) – "
                                 f"{_conflict_text(conf)}.")
        store_idents(r["id"], ids, pending)
        n += 1
    return n


# --- Reparatur per Fingerabdruck -------------------------------------------------------------------------------------
def _missing_refs(p):
    """Fehlende Dateien/Ordner eines Pakets (direkt auf der Freigabe nachgesehen): [(pfad, art)]."""
    out = []
    try:
        stp = steps.normalize(dict(p))["steps"]
    except Exception:
        return out
    refs = []
    _step_paths(stp, None, refs)
    seen = set()
    for rel, kind, _main, _sid, _o in refs:
        k = key(rel)
        if k in seen:
            continue
        seen.add(k)
        st = remote.repo_stat(rel)
        if st is not None:
            continue
        if kind == "auto":
            kind = "dir" if integrity._rows_under(k) or db.val("SELECT 1 FROM repo_baseline_dirs WHERE path=?", (k,)) else "file"
        out.append((rel, kind))
    # Ordner fehlt → seine Installationsdatei nicht extra suchen
    dirs = [key(r) for r, kd in out if kd == "dir"]
    return [(r, kd) for r, kd in out if kd == "dir" or not any(key(r).startswith(d + "\\") for d in dirs)]


def _sha_of(path, size, mtime, deadline):
    k = key(path)
    r = db.q1("SELECT sha256, size, mtime FROM repo_hash WHERE path=?", (k,))
    if r and r["sha256"] and r["size"] == size and abs((r["mtime"] or 0) - mtime) <= integrity.TOL:
        return r["sha256"]
    sha = remote.repo_sha256(path, mbps=db.setting("integrity_full_mbps") or None, deadline=deadline)
    if sha:
        db.ex("INSERT OR REPLACE INTO repo_hash(path, disp, size, mtime, sha256, msi_product, msi_version, at) VALUES(?,?,?,?,?,"
              "(SELECT msi_product FROM repo_hash WHERE path=?),(SELECT msi_version FROM repo_hash WHERE path=?),?)",
              (k, path, size, mtime, sha, k, k, util.now()))
    return sha


def repair_suggest(user, p):
    """Vorschläge [(alt, neu, art, wie)] für fehlende Dateien/Ordner eines Pakets. Braucht den Index im Arbeitsspeicher
    (Kandidaten über die Größe); Fingerabdrücke werden höchstens REPAIR_SECONDS lang berechnet."""
    idx = remote.repo_index_files()
    if idx is None:
        remote.repo_cache_refresh()
        raise RepoError("Der Repository-Index wird gerade eingelesen – bitte in einer Minute erneut versuchen.", {"retry": True})
    files, dirs = idx
    by_size = {}
    for k, (disp, size, mtime) in files.items():
        if not k.startswith(remote.UPLOAD_DIR + "\\"):
            by_size.setdefault(size, []).append((disp, mtime))
    deadline = time.time() + REPAIR_SECONDS
    out, missing = [], _missing_refs(p)
    for rel, kind in missing:
        if not _visible_path(user, rel):
            continue
        k = key(rel)
        if kind == "file":
            b = db.q1("SELECT * FROM repo_baseline WHERE path=?", (k,))
            if not b:
                continue
            cands = sorted(by_size.get(b["size"], []), key=lambda x: (_base(x[0]).lower() != _base(rel).lower(), x[0].lower()))
            cands = [c for c in cands if _visible_path(user, c[0])][:REPAIR_CANDIDATES]
            hit = None
            for disp, mtime in cands:
                if b["sha256"]:
                    if time.time() > deadline:
                        break
                    if _sha_of(disp, b["size"], mtime, deadline) == b["sha256"]:
                        hit = (disp, "Fingerabdruck gleich")
                        break
                elif _base(disp).lower() == _base(rel).lower():
                    hit = (disp, "gleicher Name und gleiche Größe (kein Fingerabdruck gespeichert)")
                    break
            if hit:
                out.append({"old": rel, "new": hit[0], "kind": "file", "how": hit[1]})
        else:
            base = integrity._rows_under(k)
            if not base:
                continue
            rels = sorted(((bk[len(k) + 1:], b) for bk, b in base.items()), key=lambda x: -(x[1]["size"] or 0))
            anchor = next(((r_, b) for r_, b in rels if b["sha256"]), None)
            hit = None
            if anchor:
                suffix, b = anchor
                for disp, mtime in by_size.get(b["size"], [])[:REPAIR_CANDIDATES * 4]:
                    if not disp.lower().endswith("\\" + suffix) or not _visible_path(user, disp):
                        continue
                    cand_dir = disp[:len(disp) - len(suffix) - 1]
                    others = [r_ for r_, bb in rels[:20] if r_ != suffix]
                    if any(f"{cand_dir}\\{o}".lower() not in files for o in others):
                        continue
                    if time.time() > deadline:
                        break
                    if _sha_of(disp, b["size"], mtime, deadline) == b["sha256"]:
                        hit = (cand_dir, f"Fingerabdruck von „{suffix}“ gleich, übrige Dateien vorhanden")
                        break
            if hit and key(hit[0]) in dirs:
                out.append({"old": rel, "new": hit[0], "kind": "dir", "how": hit[1]})
    found = {key(x["old"]) for x in out}
    return {"items": out, "not_found": [r for r, _k in missing if key(r) not in found and _visible_path(user, r)],
            "missing": len(missing)}


def repair_apply(user, pairs):
    """Gefundene neue Pfade in ALLE Pakete übernehmen (die Dateien selbst wurden schon außerhalb verschoben)."""
    from . import backup
    with _write_lock:
        clean = []
        for old, new in pairs:
            old, new = "\\".join(_parts(old)), "\\".join(_parts(new))
            if not _visible_path(user, old) or not _visible_path(user, new):
                raise RepoError("Nur Pfade im Ordner Ihres Mandanten.", {"status": 403})
            if remote.repo_stat(old) is not None:
                raise RepoError(f"„{old}“ gibt es noch – nichts zu reparieren.")
            if remote.repo_stat(new) is None:
                raise RepoError(f"„{new}“ gibt es nicht.")
            clean.append((old, new))
        if not clean:
            raise RepoError("Keine Pfade gewählt.")
        plan = plan_rewrite(clean)
        if access.scoped(user):
            mine = [t["id"] for t in access.build_tenants(user)]
            plan["packages"] = [p for p in plan["packages"] if db.val("SELECT tenant_id FROM packages WHERE id=?", (p["id"],)) in mine]
            plan["tenants"], plan["settings"], plan["deployments"] = [], [], []
        if plan["problems"]:
            raise RepoError("Reparatur nicht möglich: " + "; ".join(plan["problems"][:5]))
        b = None
        if plan["packages"] or plan["tenants"] or plan["deployments"] or plan["settings"]:
            try:
                b = backup.create(label="vor-reparatur", user=user.get("username"))
            except Exception as e:
                raise RepoError(f"Sicherung vor der Reparatur fehlgeschlagen – nichts geändert: {e}")
        integrity.moved()
        _apply_db(plan, clean)
        integrity.moved()
    for old, new in clean:
        _log(user, f"Repository-Reparatur: „{old}“ → „{new}“ in {len(plan['packages'])} Paket(en) übernommen.", tenant_of_path(new))
    _recheck([p["id"] for p in plan["packages"]])
    out = _public_plan(plan)
    out["backup"] = (b or {}).get("name")
    return out
