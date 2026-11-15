"""2.49.0: Text editor for script and text files in the repository.

Edit a .ps1/.cmd/.ini/.xml ... directly in the browser instead of replacing it on the share - and without a "break":
the edit is a deliberate change by an administrator, so POCKET KNIFE confirms the new content for the integrity
check right away (exact size, date and SHA-256 of what it wrote) and refreshes the identifiers of the packages
that use the file.

Safety:
- Same scope rules as the other repository functions (OU admins only in their tenant folders, never in a folder
  that a package of another scope uses, only file types they may upload).
- Only text: known extensions, at most MAX_BYTES, no NUL bytes (binary). Encoding (UTF-8 with/without BOM,
  UTF-16 LE/BE, ANSI = Windows-1252) and line endings (CRLF/LF) are kept unless chosen otherwise.
- Conflict check: saving needs the SHA-256 of the content that was loaded - if someone changed the file meanwhile,
  nothing is overwritten.
- Written to a temporary file first, then swapped in (the old file is kept until the new one is in place, rolled back
  on an error).
- The previous version is kept (HISTORY_KEEP per file) and can be loaded back into the editor.
"""
import hashlib
import secrets
import zlib

from . import access, db, integrity, remote, repofiles, util

MAX_BYTES = 2 * 1024 * 1024
HISTORY_KEEP = 10
HISTORY_MAX_ROWS = 3000
TEXT_EXT = {"txt", "log", "md", "csv", "ps1", "psm1", "psd1", "ps1xml", "cmd", "bat", "vbs", "js", "wsf", "reg", "ini", "inf",
            "cfg", "conf", "config", "xml", "json", "yml", "yaml", "iss", "nsi", "properties", "manifest", "lst", "ahk", "sh", "py"}
SCRIPT_EXT = {"ps1", "psm1", "psd1", "cmd", "bat", "vbs", "js", "wsf", "reg", "ahk", "sh", "py"}
ENCODINGS = {"utf-8": "UTF-8", "utf-8-sig": "UTF-8 mit BOM", "utf-16-le": "UTF-16 LE", "utf-16-be": "UTF-16 BE", "cp1252": "ANSI (Windows-1252)"}
_BOM = {"utf-8-sig": b"\xef\xbb\xbf", "utf-16-le": b"\xff\xfe", "utf-16-be": b"\xfe\xff"}

def _ext(rel):
    b = repofiles._base(rel)
    return b.rsplit(".", 1)[-1].lower() if "." in b else ""


def editable(rel):
    return _ext(rel) in TEXT_EXT


def decode(raw):
    """bytes -> (text with "\\n", encoding, newline). Raises RepoError for binary content."""
    enc = None
    if raw.startswith(_BOM["utf-8-sig"]):
        enc, body = "utf-8-sig", raw[3:]
    elif raw.startswith(_BOM["utf-16-le"]):
        enc, body = "utf-16-le", raw[2:]
    elif raw.startswith(_BOM["utf-16-be"]):
        enc, body = "utf-16-be", raw[2:]
    else:
        body = raw
        if b"\x00" in body:
            raise repofiles.RepoError("Die Datei enthält Binärdaten – der Editor ist nur für Textdateien.")
        try:
            body.decode("utf-8")
            enc = "utf-8"
        except UnicodeDecodeError:
            enc = "cp1252"
    try:
        text = body.decode("utf-8" if enc == "utf-8-sig" else enc)
    except UnicodeDecodeError:
        raise repofiles.RepoError("Die Datei lässt sich nicht als Text lesen (unbekannte Zeichenkodierung).")
    if "\x00" in text:
        raise repofiles.RepoError("Die Datei enthält Binärdaten – der Editor ist nur für Textdateien.")
    crlf, lf = text.count("\r\n"), text.count("\n")
    newline = "\r\n" if (crlf and crlf * 2 >= lf) or not lf else "\n"  # no line break yet: Windows style
    return text.replace("\r\n", "\n"), enc, newline


def encode(text, enc, newline):
    if enc not in ENCODINGS:
        raise repofiles.RepoError("Unbekannte Zeichenkodierung.")
    t = str(text).replace("\r\n", "\n").replace("\r", "\n")
    if newline == "\r\n":
        t = t.replace("\n", "\r\n")
    try:
        body = t.encode("utf-8" if enc == "utf-8-sig" else enc)
    except UnicodeEncodeError as e:
        bad = t[e.start:e.end]
        raise repofiles.RepoError(f"„{bad}“ lässt sich in {ENCODINGS[enc]} nicht speichern – bitte UTF-8 wählen.")
    return _BOM.get(enc, b"") + body


def _hint(rel, enc, text):
    """PowerShell 5.1 reads UTF-8 without BOM as ANSI - umlauts in a .ps1 would break on the client."""
    if _ext(rel) in ("ps1", "psm1", "psd1") and enc == "utf-8" and any(ord(ch) > 127 for ch in text):
        return ("Windows PowerShell 5.1 liest UTF-8 ohne BOM als ANSI – Umlaute und Sonderzeichen werden auf dem Client falsch. "
                "Empfehlung: als „UTF-8 mit BOM“ speichern.")
    return ""


def _check(user, path, write=False):
    rel = repofiles.need_scope(user, path, own_root_ok=False)
    if not editable(rel):
        raise repofiles.RepoError("Diese Datei ist keine Text- oder Skriptdatei, die der Editor öffnen kann.")
    if access.scoped(user):
        if not repofiles._ext_ok(user, repofiles._base(rel)):
            raise repofiles.RepoError("Dateien dieses Typs dürfen Sie im Repository nicht ändern.")
        if write:
            repofiles._block_foreign(user, rel)
    return rel


def _read_raw(rel):
    kind = remote.repo_stat(rel)
    if kind != "file":
        raise repofiles.RepoError(f"„{rel}“ gibt es nicht (mehr).")
    st = remote.repo_fstat(rel)
    if st and st[0] > MAX_BYTES:
        raise repofiles.RepoError(f"Die Datei ist zu groß für den Editor (höchstens {MAX_BYTES // 1048576} MB).")
    with remote.repo_open_rel(rel) as fh:
        raw = fh.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise repofiles.RepoError(f"Die Datei ist zu groß für den Editor (höchstens {MAX_BYTES // 1048576} MB).")
    return raw, st


def _users(rel):
    hits = repofiles.usage(rel)
    return hits, sorted({repofiles._owner_text(h[4]) for h in hits})


def read(user, path):
    rel = _check(user, path)
    raw, st = _read_raw(rel)
    text, enc, nl = decode(raw)
    sha = hashlib.sha256(raw).hexdigest()
    _hits, used_by = _users(rel)
    b = db.q1("SELECT sha256 FROM repo_baseline WHERE path=?", (integrity.key(rel),))
    return {"path": rel, "text": text, "encoding": enc, "newline": "crlf" if nl == "\r\n" else "lf", "sha256": sha,
            "size": len(raw), "mtime": st[1] if st else None, "used_by": used_by,
            # the file already differs from the confirmed state (changed outside POCKET KNIFE)
            "differs": bool(b and b["sha256"] and b["sha256"] != sha),
            "hint": _hint(rel, enc, text), "script": _ext(rel) in SCRIPT_EXT, "encodings": ENCODINGS,
            "history": db.val("SELECT COUNT(*) FROM repo_edits WHERE path=?", (integrity.key(rel),), 0)}


def save(user, path, text, expected_sha, encoding=None, newline=None):
    rel = _check(user, path, write=True)
    if text is None or len(str(text)) > MAX_BYTES:
        raise repofiles.RepoError("Text fehlt oder ist zu lang.")
    who = user["username"]
    tok = secrets.token_hex(8)
    tmp = f"{remote.UPLOAD_DIR}\\edit-{tok}.part"
    bak = f"{remote.UPLOAD_DIR}\\edit-{tok}.old"
    with repofiles._write_lock:
        raw, _st = _read_raw(rel)
        cur_sha = hashlib.sha256(raw).hexdigest()
        if not expected_sha or cur_sha != str(expected_sha).lower():
            raise repofiles.RepoError("Die Datei wurde inzwischen geändert – bitte neu laden (Ihre Änderungen vorher kopieren).",
                                      {"conflict": True, "sha256": cur_sha, "status": 409})
        _old_text, old_enc, old_nl = decode(raw)
        enc = encoding or old_enc
        nl = {"crlf": "\r\n", "lf": "\n"}.get(newline or "", old_nl)
        data = encode(text, enc, nl)
        if len(data) > MAX_BYTES:
            raise repofiles.RepoError(f"Der Text ist zu lang (höchstens {MAX_BYTES // 1048576} MB).")
        new_sha = hashlib.sha256(data).hexdigest()
        if new_sha == cur_sha:
            return {"path": rel, "sha256": cur_sha, "unchanged": True}
        remote.repo_mkdir(remote.UPLOAD_DIR)
        with remote.repo_open_write(tmp, "wb") as fh:
            fh.write(data)
        remote.repo_rename(rel, bak)
        try:
            remote.repo_rename(tmp, rel)
        except Exception:
            remote.repo_rename(bak, rel)
            try:
                remote.repo_delete(tmp)
            except Exception:
                pass
            raise
        try:
            remote.repo_delete(bak)
        except Exception:
            pass
        _remember(rel, who, raw, cur_sha, new_sha, old_enc)
    st = remote.repo_fstat(rel) or (len(data), int(__import__("time").time()))
    db.ex("INSERT OR REPLACE INTO repo_hash(path, disp, size, mtime, sha256, msi_product, msi_version, at) VALUES(?,?,?,?,?,NULL,NULL,?)",
          (rel.lower(), rel, st[0], st[1], new_sha, util.now()))
    remote.repo_cache_apply("add", rel, size=st[0], mtime=st[1])
    hits, used_by = _users(rel)
    confirmed = integrity.confirm_written(rel, st[0], st[1], new_sha, who)
    pids = sorted({h[4][1] for h in hits if h[4][0] == "pkg"})
    _after(pids)
    tid = repofiles.tenant_of_path(rel)
    enc_t, nl_t = ENCODINGS[enc], "CRLF" if nl == "\r\n" else "LF"
    if hits:
        repofiles._log(user, f"Repository: „{rel}“ im Editor geändert ({enc_t}, {nl_t}, SHA-256 {new_sha[:12]}…) – benutzt von "
                             f"{repofiles.usage_summary(hits, 6)}; neuer Stand für die Bruchprüfung übernommen.", tid)
    else:
        repofiles._log(user, f"Repository: „{rel}“ im Editor geändert ({enc_t}, {nl_t}, SHA-256 {new_sha[:12]}…).", tid)
    return {"path": rel, "sha256": new_sha, "size": st[0], "encoding": enc, "newline": "crlf" if nl == "\r\n" else "lf",
            "used_by": used_by, "confirmed": confirmed, "hint": _hint(rel, enc, str(text))}


def _remember(rel, who, raw, sha_before, sha_after, enc):
    k = integrity.key(rel)
    db.ex("INSERT INTO repo_edits(path, disp, at, username, size, sha_before, sha_after, encoding, content) VALUES(?,?,?,?,?,?,?,?,?)",
          (k, rel, util.now(), who, len(raw), sha_before, sha_after, enc, zlib.compress(raw, 6)))
    db.ex("DELETE FROM repo_edits WHERE path=? AND id NOT IN (SELECT id FROM repo_edits WHERE path=? ORDER BY id DESC LIMIT ?)",
          (k, k, HISTORY_KEEP))
    n = db.val("SELECT COUNT(*) FROM repo_edits", default=0) or 0
    if n > HISTORY_MAX_ROWS:
        db.ex("DELETE FROM repo_edits WHERE id IN (SELECT id FROM repo_edits ORDER BY id LIMIT ?)", (n - HISTORY_MAX_ROWS,))


def _after(pids):
    """Packages using the file: refresh identifiers (duplicate lock) and their integrity state."""
    import threading
    from . import deploy

    def run():
        for pid in pids:
            try:
                row = db.q1("SELECT * FROM packages WHERE id=?", (pid,))
                if not row:
                    continue
                ids, pending = repofiles.idents(deploy.row_to_package(row))
                repofiles.store_idents(pid, ids, pending)
                integrity.check_package(pid, live=True)
            except Exception as e:  # noqa: BLE001
                from . import pklog
                pklog.warn("repo", f"Paket #{pid} nach Änderung im Editor nicht neu geprüft: {e}")
    if pids:
        threading.Thread(target=run, name="pk-repoedit-after", daemon=True).start()


def history(user, path):
    rel = _check(user, path)
    return [{"id": r["id"], "at": r["at"], "user": r["username"], "size": r["size"], "encoding": r["encoding"]}
            for r in db.q("SELECT id, at, username, size, encoding FROM repo_edits WHERE path=? ORDER BY id DESC",
                          (integrity.key(rel),))]


def version(user, path, hid):
    """Text of an earlier version (to load it back into the editor - saving is the normal save)."""
    rel = _check(user, path)
    r = db.q1("SELECT * FROM repo_edits WHERE id=? AND path=?", (int(hid), integrity.key(rel)))
    if not r:
        raise repofiles.RepoError("Diese frühere Fassung gibt es nicht (mehr).")
    text, enc, nl = decode(zlib.decompress(r["content"]))
    return {"id": r["id"], "at": r["at"], "user": r["username"], "text": text, "encoding": enc,
            "newline": "crlf" if nl == "\r\n" else "lf"}


def stats(since):
    """For the diagnostic package: edits in the period (counts only)."""
    return {"edits": db.val("SELECT COUNT(*) FROM repo_edits WHERE at >= ?", (since,), 0),
            "files": db.val("SELECT COUNT(DISTINCT path) FROM repo_edits WHERE at >= ?", (since,), 0),
            "by_encoding": {r["encoding"]: r["n"] for r in db.q("SELECT encoding, COUNT(*) n FROM repo_edits WHERE at >= ? GROUP BY 1", (since,))},
            "history_rows": db.val("SELECT COUNT(*) FROM repo_edits", default=0)}
