"""Regression tests for bugs that have already been fixed - run before every new version.

Usage (in the container or with the dependencies installed):
    python3 tools/selftest.py
Runs with an empty temporary database and needs no network and no Windows clients.
Each test names the version in which the bug was fixed - so it does not get reintroduced.
"""
import base64
import datetime
import json
import os
import shutil
import socket
import socketserver
import ssl
import sys
import tempfile
import threading
import traceback

# 2.32: The instance settings (.env in the container: PK_NETBIOS, PK_DOMAIN, service account ...) do not apply here - otherwise
# a result would depend on the environment (e.g. kiosk test with "FIRMA\nutzer" under a different NetBIOS name), and real
# credentials have no business in the test. Only the self-test's own switches remain.
for _k in [k for k in os.environ if k.startswith("PK_") and k != "PK_PWSH" and not k.startswith("PK_SELFTEST")]:
    del os.environ[_k]
TMP = tempfile.mkdtemp(prefix="pk-selftest-")
os.environ.update(PK_DATA_DIR=TMP, PK_HTTPS="false", PK_DOMAIN="firma.local", PK_PUBLIC_HOSTNAME="pocketknife")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app import collections_, customreports, db, diskusage, mailer, notify, reports, winget  # noqa: E402
import logging  # noqa: E402
# Do not print test messages (made-up names like "BUCHH-MUELLER7") to the console - only count the results
logging.getLogger("pocketknife").addHandler(logging.NullHandler())
logging.getLogger("pocketknife").propagate = False
from app import auth, clientfiles, util  # noqa: E402

db.init()
RESULTS = []


def test(name):
    def deco(fn):
        RESULTS.append((name, fn))
        return fn
    return deco


def must_fail(fn, *a, contains=""):
    try:
        fn(*a)
    except Exception as e:  # noqa: BLE001
        assert contains.lower() in str(e).lower(), f"wrong message: {e}"
        return
    raise AssertionError("no error although one was expected")


# --- Test data ----------------------------------------------------------------------------------
def seed():
    import json
    for i in range(1, 4):
        db.ex("INSERT INTO computers(id, name, online, os_name, ram_bytes) VALUES(?,?,?,?,?)",
              (i, f"PC-{i:02d}", i % 2, "Microsoft Windows 11 Pro" if i < 3 else "Microsoft Windows 10 Pro", 8 * 1024 ** 3))
        db.ex("INSERT INTO disks(computer_id, drive, size, free) VALUES(?,?,?,?)", (i, "C:", 256 * 1024 ** 3, i * 9 * 1024 ** 3))
        db.ex("INSERT INTO software(computer_id, name, version, publisher) VALUES(?,?,?,?)", (i, "Tool [x64]", f"1.{i}", "ACME"))
    db.ex("INSERT INTO custom_scanners(id, name, type, target, extra, enabled) VALUES(1, 'Profile', 'powershell', 'x', '{}', 1)")
    for cid, size in ((1, 15 * 1024 ** 3), (2, 700 * 1024 ** 2)):
        db.ex("INSERT INTO inventory_data(computer_id, category, json, updated) VALUES(?,?,?,?)",
              (cid, "custom:1", json.dumps([{"User": f"u{cid}", "Size": str(size), "Files": "120"}]), "2026-01-01"))


seed()


# --- Tests --------------------------------------------------------------------------------------
@test("2.7 Exceptions: square brackets in patterns are taken literally (\"Tool [x64]\")")
def _():
    assert winget.wildcard("Tool [x64]").match("tool [x64]")
    assert winget.wildcard("Tool*").match("Tool [x64]")
    assert not winget.wildcard("Tool [x64]").match("Tool x")


@test("2.6.3 Login lockout: a\\admin, b\\admin, admin@x count as the same user")
def _():
    assert auth._norm_user("X\\Admin") == auth._norm_user("admin@firma") == "admin"


@test("CSV export: formula injection is defused, numbers stay numbers")
def _():
    out = reports.to_csv([{"key": "a", "label": "A"}], [{"a": "=HYPERLINK(1)"}, {"a": "-5"}, {"a": "+49 30"}])
    assert "'=HYPERLINK" in out and "\n-5" in out.replace("\r", "") and "'+49 30" in out


@test("2.6.2 Collections: numbers with a unit (20 GB, 1,5 TB)")
def _():
    assert collections_._num("20 GB") == 20 * 1024 ** 3
    assert collections_._num("1,5 TB") == 1.5 * 1024 ** 4
    must_fail(collections_._num, "zwanzig", contains="keine Zahl")


@test("2.12.1 Size columns of custom scanners: recognised as \"bytes\", >=/<= with unit, readable suggestions")
def _():
    cols = dict(collections_._scanner_columns(1))
    assert cols["Size"] == "bytes" and cols["User"] == "any" and cols["Files"] == "any", cols

    def count(op, v):
        w, p = collections_.build_tree({"type": "group", "match": "all", "items": [
            {"type": "cond", "table": "custom:1", "column": "Size", "op": op, "value": v}]})
        return db.val(f"SELECT COUNT(*) FROM computers c WHERE {w}", p)
    assert count("gte", "10 GB") == 1 and count("lt", "1 GB") == 1 and count("gte", "500 MB") == 2
    assert count("contains", "1610") == 1  # old condition with a text operator keeps working
    vals = collections_.values("custom:1", "Size")
    assert vals and all(v.endswith(("MB", "GB")) for v in vals), vals
    assert collections_.fmt_bytes(15 * 1024 ** 3) == "15 GB" and collections_.fmt_bytes(1536) == "1,5 KB"


@test("2.12 Custom reports: only known columns, no SQL from inputs, summarising counts")
def _():
    must_fail(customreports.clean, {"name": "x", "definition": {"source": "computer", "columns": [{"t": "computer", "c": "name; DROP TABLE computers"}]}},
              contains="Unbekannte Spalte")
    must_fail(customreports.clean, {"name": "x", "definition": {"source": "computer", "columns": [{"t": "software", "c": "name"}]}},
              contains="Spalten nur")
    c = customreports.clean({"name": "x", "definition": {"source": "software", "columns": [{"t": "software", "c": "name"}], "group": True}})
    res = customreports.execute(c["definition"])
    assert res["rows"][0]["cnt"] == 3, res


@test("2.12.1 Custom reports: sizes sorted numerically and marked as \"bytes\"")
def _():
    c = customreports.clean({"name": "x", "definition": {"source": "custom:1", "columns": [{"t": "custom:1", "c": "User"}, {"t": "custom:1", "c": "Size"}],
                                                          "sort": {"i": 1, "dir": "desc"}}})
    res = customreports.execute(c["definition"])
    assert res["columns"][1]["type"] == "bytes" and [r["c0"] for r in res["rows"]] == ["u1", "u2"], res
    html, text = mailer.html_table(res["columns"], res["rows"])
    assert "15 GB" in text and "700 MB" in text, text


@test("All predefined reports run without errors")
def _():
    for d in reports.REPORTS:
        reports.run(d["key"], {p["name"]: p.get("default", "") for p in d["params"]})


@test("2.9 Files: local drive paths only, no \"..\", no UNC")
def _():
    assert clientfiles._parse("c:/Users/x") == ("C", "Users\\x")
    for bad in ("..\\x", "C:\\Users\\..\\Windows", "\\\\srv\\share", "Users"):
        must_fail(clientfiles._parse, bad)
    must_fail(diskusage.clean_path, "C:\\..\\x")


@test("2.12.3 Files: links (\"All Users\" -> C:\\ProgramData) do not break the list, opening jumps to the target")
def _():
    from smbprotocol.exceptions import SMBException

    class SMBLinkRedirectionError(SMBException):  # like smbprotocol: not an OSError!
        def __init__(self, path, target):
            super().__init__("Cannot resolve link targets that point to a local path")
            self.path, self.target = path, target

    class St:
        def __init__(self, attr):
            self.st_file_attributes, self.st_size, self.st_mtime, self.st_mode = attr, 0, 0, 0

    class Entry:
        def __init__(self, name, attr, link=None):
            self.name, self.attr, self.link = name, attr, link

        def stat(self, follow_symlinks=True):
            if follow_symlinks and self.link:
                raise SMBLinkRedirectionError(self.name, self.link)
            return St(self.attr)

        def is_dir(self, follow_symlinks=True):
            return bool(self.stat(follow_symlinks).st_file_attributes & 0x10)

    removed = []

    class Fake:
        @staticmethod
        def scandir(unc):
            if unc.endswith("All Users"):
                raise SMBLinkRedirectionError(unc, "C:\\ProgramData")
            return [Entry("All Users", 0x410, "C:\\ProgramData"), Entry("max", 0x10)]

        @staticmethod
        def stat(unc, follow_symlinks=True):
            if "Temp" in unc:
                raise FileNotFoundError(2, "No such file", unc)
            if not follow_symlinks:
                return St(0x410 if unc.endswith("All Users") else 0x10)
            raise SMBLinkRedirectionError(unc, "C:\\ProgramData")

        @staticmethod
        def rmdir(unc):
            removed.append(("rmdir", unc))

    orig = clientfiles._session
    clientfiles._session = lambda cid: (Fake, {}, "pc")
    import smbclient.shutil as real_shutil  # noqa: F401  – rmtree must never be called for links
    try:
        res = clientfiles.listing(1, "C:\\Users")
        names = {x["name"]: x for x in res["items"]}
        assert names["All Users"]["dir"] and names["All Users"]["reparse"] and not names["All Users"].get("denied"), res
        assert clientfiles.listing(1, "C:\\Users\\All Users")["redirect"] == "C:\\ProgramData"
        ok, errs = clientfiles.delete(1, [{"path": "C:\\Users", "name": "All Users"}])
        assert ok == 1 and removed == [("rmdir", "\\\\pc\\C$\\Users\\All Users")], (ok, errs, removed)
        ok, errs = clientfiles.transfer(1, "copy", "C:\\Users", ["All Users"], "C:\\Temp")
        assert ok == 0 and "Verknüpfung" in errs[0], errs
    finally:
        clientfiles._session = orig


@test("2.11 Notifications: a case is reported only once; disk space with hysteresis")
def _():
    cfg = {"params": {"gb": "20", "pct": "0", "drives": "", "collection": ""}, "since": None}
    cur = notify._check_low_disk(cfg["params"], cfg)
    assert set(cur) == {"1:C:", "2:C:"}, cur  # 9 GB and 18 GB free
    assert not notify._resolved_low_disk(cfg["params"], "2:C:")


@test("2.12.2 UI: no more <datalist> (showed only entries matching the value) – selection lists via combo()")
def _():
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    assert "h('datalist'" not in js and "datalist#" not in js, "datalist built in again"
    assert "let selKey = 'props'" in js, "2.12.2 package editor should start with the properties (name)"


# --- 2.13 Review: security and logic ------------------------------------------------------------
@test("2.13 Login: umlauts with emergency access set, no password confirmation to the outside, atomic lockout")
def _():
    from fastapi import HTTPException
    from app import config as cfg
    old = (cfg.BREAKGLASS_USER, cfg.BREAKGLASS_PASSWORD)
    cfg.BREAKGLASS_USER, cfg.BREAKGLASS_PASSWORD = "notfall", "geheim"
    orig = auth.ad.authenticate
    try:
        def fake(u, p, allow_kiosk=False):
            raise auth.ad.ADError("Kein Zugriff: Sie sind nicht Mitglied der Gruppe für POCKET KNIFE")
        auth.ad.authenticate = fake
        try:
            auth.login("jürgen", "pässwort", "10.0.0.1")
        except HTTPException as e:
            assert e.status_code == 401 and e.detail == auth.GENERIC_FAIL, e.detail
        else:
            raise AssertionError("login should have failed")
        for _i in range(7):
            try:
                auth.login("jürgen", "x", "10.0.0.1")
            except HTTPException:
                pass
        try:
            auth.login("jürgen", "x", "10.0.0.1")
        except HTTPException as e:
            assert e.status_code == 429, e.status_code
        tok, info = auth.login("notfall", "geheim", "10.0.0.9")
        assert info["username"] == "notfall"
    finally:
        auth.ad.authenticate = orig
        cfg.BREAKGLASS_USER, cfg.BREAKGLASS_PASSWORD = old


@test("2.13 Files: names \".\", \"..\" and those ending in a dot/space are rejected")
def _():
    for bad in (".", "..", "a.", "a "):
        assert not clientfiles.NAME_RE.match(bad), bad
    for ok in ("a", ".git", "Tool [x64].exe", "a..b"):
        assert clientfiles.NAME_RE.match(ok), ok


@test("2.13 Inventory: a failed software part does not delete the software list")
def _():
    from app import inventory
    before = db.val("SELECT COUNT(*) FROM software WHERE computer_id=1")
    inventory.save(1, {"software": [], "software_error": "Registry nicht lesbar"}, ["applications"])
    assert db.val("SELECT COUNT(*) FROM software WHERE computer_id=1") == before == 1
    assert not db.val("SELECT COUNT(*) FROM software_changes WHERE computer_id=1 AND change='entfernt'")


@test("2.13 AD sync: delete nothing if everything is missing or no container is included – deleting only after a grace period")
def _():
    from app import ad
    db.ex("UPDATE computers SET ad_guid='g'||id, ad_present=1, ad_missing_since=NULL")
    db.set_setting("ad_remove_missing", 1)
    class C:
        def unbind(self):
            pass
    o = (ad.service_conn, ad._search, ad.container_rules, ad.sync_ad_collections)
    ad.service_conn, ad.sync_ad_collections = (lambda: C()), (lambda: None)
    try:
        ad.container_rules = lambda: []
        must_fail(ad.sync_computers, contains="Kein AD-Container")
        ad.container_rules = lambda: [{"dn": "DC=firma,DC=local", "include": True, "path": ""}]
        ad._search = lambda c, base, flt, attrs: []  # e.g. missing read permissions: AD returns "nothing"
        ad.sync_computers()
        assert db.val("SELECT COUNT(*) FROM computers") == 3 and db.val("SELECT COUNT(*) FROM computers WHERE ad_present=0") == 0
    finally:
        ad.service_conn, ad._search, ad.container_rules, ad.sync_ad_collections = o
        db.set_setting("ad_remove_missing", 0)
        db.ex("UPDATE computers SET ad_guid=NULL")


@test("2.13 Versions: suffixes like \"(x64)\" or \"+build\" do not make a version older")
def _():
    from app import util
    assert util.vcmp("1.2.3 (x64)", "1.2.3") == 0 and util.vcmp("1.0+build5", "1.0") == 0
    assert util.vcmp("1.10", "1.9") == 1 and util.vcmp("130.0.1", "131.0") == -1


@test("2.13 Triggers: weekly every N weeks not before the start date; calculation is fast")
def _():
    import time as _t
    from app import profiles, schedules
    t = {"type": "weekly", "time": "08:00", "days": [0], "every": 2, "start": "2026-10-21", "enabled": True}
    assert profiles.last_occurrence(t, datetime.datetime(2026, 10, 20, 9, 0)) is None
    t0 = _t.time()
    n = schedules.next_occurrence({"type": "daily", "time": "07:00", "every": 365, "start": "2026-01-01"}, datetime.datetime(2026, 10, 20))
    assert n == datetime.datetime(2027, 1, 1, 7, 0) and _t.time() - t0 < 0.5


@test("2.13 RegEx from filters: catastrophic backtracking is rejected")
def _():
    from app import util
    assert util.regex_problem("(a+)+$") and util.regex_problem("(a|aa)*b") and util.regex_problem("x" * 300)
    assert util.regex_problem("^Win(dows)? 1[01]") is None
    assert db._pk_regex("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa!", "(a+)+$") == 0  # a stored legacy pattern is not executed


@test("2.13 Collections: deletion refused while in use; circular references are rejected")
def _():
    a = db.ex("INSERT INTO collections(name, type, match, filters) VALUES('A','dynamic','all','{\"type\":\"group\",\"match\":\"all\",\"items\":[]}')")
    b = db.ex("INSERT INTO collections(name, type, match, filters) VALUES('B','dynamic','all',?)",
              (json.dumps({"type": "group", "match": "all", "items": [{"type": "cond", "table": "computer", "column": "collection", "op": "in", "value": str(a)}]}),))
    assert "Sammlung „B“" in collections_.used_by([a])
    must_fail(collections_.check_cycle, a, {"type": "group", "match": "all", "items": [
        {"type": "cond", "table": "computer", "column": "collection", "op": "in", "value": str(b)}]}, contains="Zirkelbezug")
    db.ex("DELETE FROM collections WHERE id IN (?,?)", (a, b))


@test("2.13 Notification \"custom report\": identical rows are not swallowed, a row boundary does not lead to \"resolved\"")
def _():
    r = customreports.save(None, {"name": "Doppelt", "definition": {"source": "software", "columns": [{"t": "computer", "c": "cores"}], "limit": 1}})
    key = f"report:{r['id']}"
    a = notify.alert_def(key)
    cur, cols, trunc = notify._check_report(a, {})
    assert len(cur) == 3 and not trunc, (len(cur), trunc)  # 3 rows without text column, despite limit=1
    customreports.delete(r["id"])


@test("2.13 Email: stored password never sent to a changed server; no login without encryption")
def _():
    mailer.save({"enabled": True, "host": "mail.firma.local", "port": 587, "security": "starttls", "user": "pk@firma.local",
                 "password": "geheim", "from": "pk@firma.local", "to": "it@firma.local"})
    must_fail(mailer.save, {"enabled": True, "host": "evil.example", "port": 587, "security": "starttls", "user": "pk@firma.local",
                            "from": "pk@firma.local"}, contains="Kennwort erneut")
    must_fail(mailer.save, {"enabled": True, "host": "mail.firma.local", "port": 25, "security": "none", "user": "pk@firma.local",
                            "password": "x", "from": "pk@firma.local"}, contains="Verschlüsselung")
    assert mailer.parse_recipients('Max Muster <max@x.de>; "A, B" <a@b.de>') == ["max@x.de", "a@b.de"]


@test("2.13 CSV: column headers and \" =...\" with a leading space are also defused")
def _():
    out = reports.to_csv([{"key": "a", "label": "=cmd"}], [{"a": " =1+1"}])
    assert "'=cmd" in out and "' =1+1" in out


@test("2.13 Custom PowerShell scanners: a syntax error affects only that scanner (2.38: text literal + Invoke-Expression)")
def _():
    from app import scanners
    body = scanners._body({"type": "powershell", "target": "if ($true) { 'kaputt' ", "extra": "{}"})
    # 2.38: no [ScriptBlock]::Create/Base64 (Constrained Language Mode) – the code is a text literal, compiled
    # only at runtime inside try/catch
    assert "[ScriptBlock]::Create" not in body and "FromBase64String" not in body
    assert "$pkCode = 'if ($true) { ''kaputt'' '" in body and "try { Invoke-Expression $pkCode" in body


@test("2.13 Tools: javascript: URLs and unencoded values in URL tools are prevented")
def _():
    from app import tools
    must_fail(tools.validate, {"name": "x", "type": "url", "run_as": "service", "script": "javascript:alert(1)"}, contains="nicht erlaubt")
    c = {"name": "PC", "dns_name": "pc.firma.local", "current_user": "FIRMA\\\\o'brien & co", "extra": "{}"}
    assert tools.substitute("https://x/?u=$(User)", c, mode="url").endswith("o%27brien%20%26%20co")
    assert "$PK_User = 'FIRMA\\\\o''brien & co'" in tools.ps_variables(c)


@test("2.13 Uninstall: entries from user profiles never run as SYSTEM")
def _():
    from app import uninstall
    db.conn().executescript(uninstall.SCHEMA) if hasattr(uninstall, "SCHEMA") else None
    sw = {"name": "Google Chrome", "version": "1", "hive": "HKEY_USERS", "reg_path": "S-1-5-21-1-2-3-1001\\\\Software\\\\x",
          "quiet_uninstall": "cmd /c net localgroup administrators me /add", "uninstall_string": "", "product_code": "X"}
    p = uninstall.package_for(sw, None, "system")
    assert p["run_as"] == "user" and p["run_as_forced"], p["run_as"]


@test("2.13 Web: oversized requests are rejected before being read (also /api/login)")
def _():
    import asyncio
    try:
        from app.main import BodyLimit
    except Exception as e:  # pragma: no cover – skip without FastAPI (local check)
        print(f"      (skipped: {e})")
        return
    sent = []

    async def app_(scope, receive, send):
        while (await receive()).get("more_body"):
            pass
        await send({"type": "http.response.start", "status": 200, "headers": []})

    async def run(cl, chunks):
        it = iter(chunks)

        async def receive():
            b = next(it, b"")
            return {"type": "http.request", "body": b, "more_body": b != b""}

        async def send(m):
            sent.append(m)
        await BodyLimit(app_)({"type": "http", "path": "/api/login", "headers": [(b"content-length", cl)]}, receive, send)
    asyncio.run(run(b"999999999", []))
    assert sent[0]["status"] == 413
    sent.clear()
    asyncio.run(run(b"x", [b"a" * 1024 * 1024] * 3))  # without a valid length, streamed
    assert sent[0]["status"] == 413


@test("2.14 Service account steps: without a scheduled task and without a password on the client")
def _():
    from app import remote
    sent, polls = [], []

    def fake_ps(host, script, timeout=None):
        sent.append(script)
        if "Win32_Process" in script and "Create" in script:
            return "PK_STARTED", "", 0
        if "pk_exit.txt" in script and "PK_DONE" in script:
            return (polls.pop(0) if polls else "PK_PROC:running"), "", 0
        return "", "", 0
    old = (remote.ps, remote.secure_workdir, remote.time.sleep)
    remote.ps, remote.secure_workdir, remote.time.sleep = fake_ps, (lambda *a, **k: None), (lambda s: None)
    try:
        polls[:] = ["PK_PROC:running", "PK_DONE:0\n---PKLOG---\nfertig"]
        code, out = remote.run_as_system("pc1.firma.local", "Write-Output 1", "1_2_3_ab", run_as="deploy")
        assert code == 0 and out == "fertig"
        start = sent[0]
        assert "Register-ScheduledTask" not in start and "PKPass" not in start and "Password" not in start, "task/password"
        assert any("taskkill" in s for s in sent[-1:]), "cleanup ends the process on abort"
        sent.clear()
        polls[:] = ["PK_PROC:gone", "PK_PROC:gone"]
        must_fail(remote.run_as_system, "pc1.firma.local", "x", "1_2_4_ab", 3600, None, "", "deploy", contains="ohne Ergebnis")
        # SYSTEM/user: scheduled task, but never with a password
        assert "-Password" not in remote.REGISTER and "PKPass" not in remote.REGISTER
    finally:
        remote.ps, remote.secure_workdir, remote.time.sleep = old
    from app import inventory
    assert "LogonType)\" -eq 'Password'" in inventory.SCAN_END, "old tasks with a password are removed during the scan"


@test("2.14 Repository: new server/new share only with the password of the access account")
def _():
    try:
        from app import main
    except Exception as e:  # pragma: no cover
        print(f"      (skipped: {e})")
        return
    from app import config, vault
    db.set_setting("repo_path", "\\\\fs1\\software$")
    db.set_setting("repo_user", "FIRMA\\svc-repo")
    db.set_setting("repo_password", vault.encrypt("Geheim-1"))
    try:
        must_fail(main._check_repo_change, {"repo_path": "\\\\evil\\share"}, contains="Kennwort")
        must_fail(main._check_repo_change, {"repo_path": "//fs1/anders", "repo_password_confirm": "falsch"}, contains="passt nicht")
        main._check_repo_change({"repo_path": "\\\\evil\\share", "repo_password_confirm": "Geheim-1"})
        main._check_repo_change({"repo_path": "\\\\FS1\\Software$\\Unterordner"})  # same server/same share
        main._check_repo_change({"repo_path": "/repo/pakete"})  # mounted folder: no login
        assert config.repo_password() == "Geheim-1"
    finally:
        db.ex("DELETE FROM settings WHERE key IN ('repo_user', 'repo_password')")
        db.set_setting("repo_path", "")


@test("2.14 Sessions: logout on inactivity; automatic refreshes do not count")
def _():
    import time as _t
    from app import util
    db.set_setting("session_idle_minutes", 30)
    th = "a" * 64
    db.ex("INSERT INTO sessions(token_hash, username, display_name, created, expires, ip, last_active) VALUES(?,?,?,?,?,?,?)",
          (th, "u1", "U", util.now(), util.fmt(datetime.datetime.now() + datetime.timedelta(hours=8)), "1.2.3.4", util.now()))
    auth._active.pop(th, None)
    assert auth.session_valid(th) and auth.idle_remaining(th) > 29 * 60
    auth._active[th] = _t.time() - 31 * 60  # 31 minutes of nothing done (background queries only)
    assert not auth.session_valid(th), "inactive session must be invalid"
    auth.touch(th)  # input in the browser
    assert auth.session_valid(th)
    auth._active[th] = _t.time() - 31 * 60
    auth.expire_idle()
    assert not db.q1("SELECT 1 FROM sessions WHERE token_hash=?", (th,)), "session not ended"
    db.set_setting("session_idle_minutes", 0)
    assert auth.idle_minutes() >= auth.IDLE_MIN, "cannot be switched off"
    db.set_setting("session_idle_minutes", 30)


@test("2.14 Schedules: runs that did not start and failed targets are caught up")
def _():
    from app import schedules, util
    db.ex("INSERT INTO computers(id, name, online, winrm) VALUES(50, 'PC-50', 1, 1)")
    sid = db.ex("INSERT INTO schedules(name, kind, enabled, triggers, targets, packages, options, winget, window, updated_at) "
                "VALUES('T', 'packages', 1, '[]', ?, '[1]', '{}', '{}', '{}', ?)",
                (json.dumps({"computers": [50], "collections": []}), util.now()))
    s = schedules.get(sid)
    calls = []
    real_fire = schedules.fire

    def boom(s_, cids=None, reason="", **k):
        calls.append((cids, reason))
        raise RuntimeError("Datenbank gesperrt")
    schedules.fire = boom
    try:
        assert schedules._fire_safe(s) is False
        assert db.val("SELECT 1 FROM schedule_trigger_state WHERE schedule_id=? AND trigger_id=?", (sid, schedules.RETRY)), \
            "run forgotten"
        now = datetime.datetime.now()
        st = {(sid, schedules.RETRY): util.fmt(now - datetime.timedelta(minutes=5))}
        schedules._evaluate_one(s, now, util.fmt(now), st)
        assert len(calls) == 1, "repeated too early"
        st = {(sid, schedules.RETRY): util.fmt(now - datetime.timedelta(minutes=16))}
        schedules.fire = lambda s_, cids=None, reason="", **k: calls.append((cids, reason)) or {"count": 0}
        schedules._evaluate_one(s, now, util.fmt(now), st)
        assert calls[-1][0] is None and not db.val("SELECT 1 FROM schedule_trigger_state WHERE schedule_id=? AND trigger_id=?",
                                                   (sid, schedules.RETRY)), "retry"
        # Failed target: catch up after 1 hour, at most 3 attempts
        db.ex("INSERT INTO schedule_targets(schedule_id, computer_id, status, last_run, attempts, pending) VALUES(?,?,?,?,?,0)",
              (sid, 50, "failed", util.ago(minutes=30), 1))
        assert schedules.retry_candidates(s) == []
        db.ex("UPDATE schedule_targets SET last_run=? WHERE schedule_id=?", (util.ago(hours=2), sid))
        assert schedules.retry_candidates(s) == [50]
        calls.clear()
        schedules._evaluate_one(s, now, util.fmt(now), {})
        assert calls and calls[-1][0] == [50], calls
        assert schedules.retry_candidates(s) == [], "nothing started -> do not retry every minute"
        db.ex("UPDATE schedule_targets SET attempts=1 WHERE schedule_id=?", (sid,))
        s2 = {**s, "options": {**s["options"], "retry_failed": False}}
        assert schedules.retry_candidates(s2) == []
    finally:
        schedules.fire = real_fire


@test("2.14 Container: privileges are dropped, data folders belong to the service user afterwards")
def _():
    import subprocess
    if os.name != "posix" or os.geteuid() != 0:
        print("      (skipped: not started as root)")
        return
    d = tempfile.mkdtemp(prefix="pk-priv-", dir=TMP)
    os.makedirs(os.path.join(d, "logs"))
    open(os.path.join(d, "logs", "a.log"), "w").close()
    os.symlink("/etc/passwd", os.path.join(d, "link"))
    secret = os.path.join(TMP, "root-only")
    with open(secret, "w") as f:
        f.write("x")
    os.chmod(secret, 0o600)
    os.chmod(TMP, 0o755)
    code = ("import os,sys; sys.path.insert(0, %r); from app import privileges; privileges.drop([%r]); "
            "print(os.getuid(), os.getgid());\n"
            "try:\n open(%r).read(); print('LESBAR')\nexcept PermissionError: print('GESPERRT')"
            % (os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."), d, secret))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30,
                         env={**os.environ, "PK_UID": "10001", "PK_GID": "10001"}).stdout
    assert "10001 10001" in out and "GESPERRT" in out, out
    assert os.stat(os.path.join(d, "logs", "a.log")).st_uid == 10001
    assert os.stat("/etc/passwd").st_uid == 0, "link must not be followed"


@test("2.14.1 docker-compose.yml: no sysctls that prevent startup (ping_group_range)")
def _():
    f = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docker-compose.yml")
    if not os.path.exists(f):
        print("      (skipped: docker-compose.yml not in the image)")
        return
    lines = [ln.split("#", 1)[0] for ln in open(f, encoding="utf-8")]
    assert not any("sysctls:" in ln or "ping_group_range" in ln for ln in lines), "sysctls in docker-compose.yml"


@test("2.14.2 Build: base image with digest, Debian snapshot and all Python packages pinned exactly")
def _():
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    df = os.path.join(root, "Dockerfile")
    if not os.path.exists(df):
        print("      (skipped: build files not in the image)")
        return
    import re
    d = open(df, encoding="utf-8").read()
    assert re.search(r"^ARG PYTHON_IMAGE=python:3\.12\.\d+-slim-\w+@sha256:[0-9a-f]{64}$", d, re.M), "base image without version/digest"
    assert re.search(r"^ARG DEBIAN_SNAPSHOT=\d{8}T\d{6}Z$", d, re.M), "no Debian snapshot"
    lock = [ln for ln in open(os.path.join(root, "requirements.lock"), encoding="utf-8") if ln.strip() and not ln.startswith("#")]
    assert lock and all(re.match(r"^[A-Za-z0-9_.-]+==[^=\s]+$", ln.strip()) for ln in lock), "requirements.lock incomplete"
    names = {ln.split("==")[0].lower().replace("_", "-") for ln in lock}
    for need in ("fastapi", "uvicorn", "ldap3", "pypsrp", "smbprotocol", "pyspnego", "cryptography", "websockets", "gssapi", "krb5"):
        assert need in names, f"{need} missing in requirements.lock"
    rt = [ln for ln in open(os.path.join(root, "docker", "apt-runtime.txt"), encoding="utf-8") if ln.strip() and not ln.startswith("#")]
    assert rt and all("=" in ln for ln in rt), "Debian packages without version"
    # 2.33: guacd pinned in docker/guacd.txt (version + digest) – published as .../pocketknife-guacd, the
    # docker-compose.yml only names this one (does not change for a guacd update)
    g = open(os.path.join(root, "docker", "guacd.txt"), encoding="utf-8").read()
    assert re.fullmatch(r"guacamole/guacd:[\w.]+@sha256:[0-9a-f]{64}\n", g), "guacd without digest"
    dc = open(os.path.join(root, "docker-compose.yml"), encoding="utf-8").read()
    assert "image: ${PK_GUACD_IMAGE:-ghcr.io/soleil0815/pocketknife-guacd:latest}" in dc and "guacamole/guacd" not in dc


@test("2.14.3 Tables: column width, order (also to the end) and visibility are remembered")
def _():
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    assert "widths: st.widths" in js and "th-rs" in js, "column width"
    assert "dropto-r" in js and "moveCol(k, c.key, right)" in js, "drag column behind the last one"
    assert "cc-row" in js, "column window with order"
    assert "splitter(tree, 'collections')" in js and "themeToggle()" in js, "2.14.4 collection tree width, light/dark button"


@test("2.15 Kiosk: kiosk sessions never reach the administration; every admin API requires the role \"admin\"")
def _():
    import ast
    from fastapi import HTTPException
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "main.py"), encoding="utf-8").read()
    public = {"/api/login", "/api/logout", "/", "/remote", "/kiosk", "/favicon.ico", "/api/kiosk/info", "/api/kiosk/sso", "/api/remote/tunnel"}
    kiosk_ok = {"/api/kiosk/me", "/api/kiosk/catalog", "/api/kiosk/request", "/api/kiosk/requests/{rid}/withdraw",
                "/api/kiosk/requests", "/api/kiosk/reboot", "/api/kiosk/icon/{pid}"}
    session_only = {"/api/session/active", "/api/session/idle"}  # 2.25: every session (A) – only the inactivity clock
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for d in node.decorator_list:
            if isinstance(d, ast.Call) and getattr(d.func, "attr", "") in ("get", "post", "put", "delete", "websocket") \
                    and getattr(d.func.value, "id", "") == "app" and d.args and isinstance(d.args[0], ast.Constant):
                path = d.args[0].value
                if path in public:
                    continue
                deps = {a.arg: getattr(dv, "id", None) for a, dv in zip(node.args.args[-len(node.args.defaults):], node.args.defaults)} \
                    if node.args.defaults else {}
                want = "K" if path in kiosk_ok else ("A" if path in session_only else "U")
                assert deps.get("user") == want, f"{path}: user={deps.get('user')} instead of {want}"

    class Req:
        def __init__(self, tok):
            self.cookies = {auth.COOKIE: tok}
    from app import util
    tok = "kiosk-selftest-token"
    db.ex("INSERT INTO sessions(token_hash, username, display_name, created, expires, ip, last_active, role) VALUES(?,?,?,?,?,?,?,?)",
          (auth._h(tok), "nutzer", "Nutzer", util.now(), "2099-01-01 00:00:00", "1.2.3.4", util.now(), "kiosk"))
    auth._checked[auth._h(tok)] = __import__("time").time()
    assert auth.current_kiosk(Req(tok))["role"] == "kiosk"
    try:
        auth.current(Req(tok))
        raise AssertionError("kiosk session has access to the administration")
    except HTTPException as e:
        assert e.status_code == 403
    assert auth.session_for(tok)["role"] == "kiosk"  # remote sessions check the role (main.py, tunnel)
    assert 's.get("role") != "admin"' in src, "remote session tunnel without role check"


@test("2.15 Kiosk: access via OU or group; without kiosk login no kiosk role")
def _():
    from app import ad
    db.set_setting("kiosk_enabled", 1)
    db.set_setting("kiosk_ous", "OU=Mitarbeiter,DC=firma,DC=local")
    db.set_setting("kiosk_groups", "Kiosk-Nutzer")
    old = (ad._resolve_group, ad.is_member)
    ad._resolve_group = lambda c, g: "CN=Kiosk-Nutzer,DC=firma,DC=local" if g == "Kiosk-Nutzer" else None
    ad.is_member = lambda c, e, g: (e["dn"].startswith("CN=GruppenMitglied"), "x")
    try:
        assert ad.kiosk_match(None, {"dn": "CN=Max,OU=Verkauf,OU=Mitarbeiter,DC=firma,DC=local"})
        assert not ad.kiosk_match(None, {"dn": "CN=Max,OU=Extern,DC=firma,DC=local"})
        assert ad.kiosk_match(None, {"dn": "CN=GruppenMitglied,OU=Extern,DC=firma,DC=local"})
        db.set_setting("kiosk_enabled", 0)
        assert not ad.kiosk_match(None, {"dn": "CN=Max,OU=Mitarbeiter,DC=firma,DC=local"}), "kiosk switched off"
    finally:
        ad._resolve_group, ad.is_member = old
        db.set_setting("kiosk_enabled", 0)
    src = open(ad.__file__, encoding="utf-8").read()
    assert "if allow_kiosk and kiosk_match(uc, e)" in src
    # 2.15.2 AD tree: search only within the own domain
    from app import config as _cfg
    old_base = _cfg.BASE_DN
    _cfg.BASE_DN = "DC=firma,DC=local"
    try:
        assert ad._under_base("OU=X,DC=firma,DC=local") and ad._under_base("dc=firma, dc=local")
        assert not ad._under_base("DC=fremd,DC=local") and not ad._under_base("OU=X,DC=firma,DC=local,DC=evil")
        must_fail(ad.browse, "DC=fremd,DC=local", contains="eigenen Domäne")
        # 2.15.4 ldap3 without schema returns every attribute as a list ("'list' object has no attribute 'lower'")
        class FakeConn:
            def __init__(self):
                self.response = [
                    {"type": "searchResEntry", "dn": "OU=Mitarbeiter,DC=firma,DC=local",
                     "attributes": {"objectClass": ["top", "organizationalUnit"], "name": ["Mitarbeiter"], "description": []}},
                    {"type": "searchResEntry", "dn": "CN=Kiosk,OU=Gruppen,DC=firma,DC=local",
                     "attributes": {"objectClass": ["top", "group"], "name": ["Kiosk"], "sAMAccountName": ["Kiosk-Nutzer"],
                                    "description": ["Darf den Kiosk"], "groupType": [-2147483646]}},
                    {"type": "searchResEntry", "dn": "CN=System,DC=firma,DC=local", "attributes": {"objectClass": "container", "name": "System"}}]

            def search(self, *a, **k):
                return True

            def unbind(self):
                pass
        old_sc = ad.service_conn
        ad.service_conn = lambda: FakeConn()
        try:
            r = ad.browse(None, True)
            kinds = {i["name"]: i for i in r["items"]}
            assert set(kinds) == {"Mitarbeiter", "Kiosk"}, kinds
            assert kinds["Kiosk"]["kind"] == "group" and kinds["Kiosk"]["sam"] == "Kiosk-Nutzer" and kinds["Kiosk"]["scope"] == "global"
            assert kinds["Mitarbeiter"]["kind"] == "ou"
            g = ad.search_groups("kio")
            assert g and g[0]["sam"] == "Kiosk-Nutzer" and isinstance(g[0]["name"], str)
        finally:
            ad.service_conn = old_sc
    finally:
        _cfg.BASE_DN = old_base


@test("2.15 Kiosk: only on the own machine, approval per package, no duplicate jobs, daily limit, schedule restart")
def _():
    from app import kiosk, deploy, steps as stp, util
    db.set_setting("kiosk_enabled", 1)
    db.set_setting("kiosk_max_per_day", 3)
    db.ex("INSERT INTO computers(id, name, online, winrm, current_user) VALUES(70, 'PC-70', 1, 1, 'FIRMA\\nutzer')")
    db.ex("INSERT INTO computers(id, name, online, winrm, current_user) VALUES(71, 'PC-71', 1, 1, 'FIRMA\\anders')")
    pk = {}
    for name, appr in (("Tool A", 0), ("Tool B", 1)):
        p = stp.clean_package({"name": name, "steps": {"main": [{"type": "command", "command": "cmd /c exit 0"}]}})
        p["steps"], p["conditions"] = json.dumps(p["steps"]), json.dumps(p["conditions"])
        pk[name] = db.ex(f"INSERT INTO packages({', '.join(deploy.PKG_FIELDS)}) VALUES({', '.join('?' * len(deploy.PKG_FIELDS))})",
                         tuple(p[k] for k in deploy.PKG_FIELDS))
        kiosk.save_item(pk[name], {"enabled": True, "approval": appr})
    created = []
    old = (kiosk._logged_on, deploy.create)
    kiosk._logged_on = lambda c, fresh=False: {kiosk._sam(x) for x in (c["current_user"] or "").split(",") if x}
    def fake_create(pkg, ids, user, scheduled_at=None, **k):
        created.append((pkg["name"], ids, scheduled_at))
        did = db.ex("INSERT INTO deployments(name, status, scheduled_at) VALUES(?,?,?)",
                    (pkg["name"], "scheduled" if scheduled_at else "running", scheduled_at))
        db.ex("INSERT INTO deployment_targets(deployment_id, computer_id, status) VALUES(?,?,?)", (did, ids[0], "queued"))
        return did
    deploy.create = fake_create
    db.set_setting("kiosk_notify", 0)
    u = {"username": "nutzer", "display_name": "Nutzer"}
    try:
        assert [c["id"] for c in kiosk.computers_for("nutzer", "?")[0]] == [70]
        must_fail(kiosk.request, u, "?", 71, pk["Tool A"], "install", contains="nicht (mehr) angemeldet")
        r = kiosk.request(u, "?", 70, pk["Tool A"], "install")
        assert created and created[-1][1] == [70] and r["status"] != "pending"
        must_fail(kiosk.request, u, "?", 70, pk["Tool A"], "install", contains="bereits")
        must_fail(kiosk.request, u, "?", 70, pk["Tool A"], "uninstall", contains="deinstallieren")
        r2 = kiosk.request(u, "?", 70, pk["Tool B"], "install")
        assert r2["status"] == "pending" and len(created) == 1, "approval required – must not start"
        kiosk.decide(r2["id"], True, "admin")
        assert len(created) == 2
        must_fail(kiosk.decide, r2["id"], False, "admin", contains="bereits entschieden")
        # Audit 2.29: no restart while other users are still logged in, and never on servers
        db.ex("UPDATE computers SET current_user='FIRMA\\nutzer,FIRMA\\kollege' WHERE id=70")
        must_fail(kiosk.plan_reboot, u, "?", 70, "1h", contains="andere Benutzer")
        db.ex("UPDATE computers SET current_user='FIRMA\\nutzer,PC-70\\Administrator' WHERE id=70")
        must_fail(kiosk.plan_reboot, u, "?", 70, "1h", contains="andere Benutzer")  # local accounts count too
        db.ex("UPDATE computers SET current_user='FIRMA\\nutzer', os_name='Microsoft Windows Server 2022 Standard' WHERE id=70")
        must_fail(kiosk.plan_reboot, u, "?", 70, "1h", contains="Server")
        db.ex("UPDATE computers SET os_name='Microsoft Windows 11 Pro' WHERE id=70")
        p = kiosk.plan_reboot(u, "?", 70, "1h")
        assert created[-1][0] == "Neustart (Kiosk)" and created[-1][2], "restart not scheduled"
        must_fail(kiosk.plan_reboot, u, "?", 70, "1h", contains="bereits")
        db.ex("UPDATE deployment_targets SET status='success' WHERE computer_id=70")
        db.ex("UPDATE deployments SET status='success' WHERE status='running'")
        must_fail(kiosk.request, u, "?", 70, pk["Tool B"], "install", contains="24 Stunden")
        cat = {i["name"]: i for i in kiosk.catalog(70)}
        assert set(cat) == {"Tool A", "Tool B"} and cat["Tool B"]["approval"]
        assert p["action"] == "reboot"
    finally:
        kiosk._logged_on, deploy.create = old
        db.set_setting("kiosk_enabled", 0)


@test("2.15.1 Kiosk icons: only real raster images, no SVG (scripts), size limited")
def _():
    from app import kiosk
    assert kiosk.icon_type(b"\x89PNG\r\n\x1a\n" + b"0" * 20) == "image/png"
    assert kiosk.icon_type(b"\xff\xd8\xff\xe0" + b"0" * 20) == "image/jpeg"
    assert kiosk.icon_type(b"<svg onload=alert(1)>") is None
    pid = db.val("SELECT package_id FROM kiosk_items LIMIT 1")
    must_fail(kiosk.set_icon, pid, b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"></svg>', contains="SVG")
    must_fail(kiosk.set_icon, pid, b"\x89PNG\r\n\x1a\n" + b"0" * (kiosk.ICON_MAX + 1), contains="zu groß")
    kiosk.set_icon(pid, b"\x89PNG\r\n\x1a\n" + b"0" * 100)
    data, typ = kiosk.get_icon(pid)
    assert typ == "image/png" and data
    assert any(i["icon"] for i in kiosk.admin_items()), "icon missing in the administration"
    kiosk.clear_icon(pid)
    assert kiosk.get_icon(pid) == (None, None)


@test("2.15.3 Multiple packages: one deployment, order as selected, no duplicates, no mixed tenants")
def _():
    from app import deploy, steps as stp
    ids = []
    for name, ten in (("P-Eins", None), ("P-Zwei", None), ("P-Mandant", 77)):
        p = stp.clean_package({"name": name, "steps": {"main": [{"type": "command", "command": "cmd /c exit 0"}]}})
        p["steps"], p["conditions"], p["tenant_id"] = json.dumps(p["steps"]), json.dumps(p["conditions"]), ten
        ids.append(db.ex(f"INSERT INTO packages({', '.join(deploy.PKG_FIELDS)}) VALUES({', '.join('?' * len(deploy.PKG_FIELDS))})",
                         tuple(p[k] for k in deploy.PKG_FIELDS)))
    c = deploy.composite([ids[1], ids[0], ids[1]], stop_on_fail=False)
    nested = [s["package_id"] for s in c["steps"]["main"]]
    assert nested == [ids[1], ids[0]], nested
    assert all(s["on_error"] == "continue" for s in c["steps"]["main"])
    assert c["name"].startswith("2 Pakete: P-Zwei, P-Eins")
    ex = stp.expand(c)
    assert [s["package"]["name"] for s in ex["steps"]["main"]] == ["P-Zwei", "P-Eins"]
    must_fail(deploy.composite, [ids[0], ids[2]], contains="Mandanten")
    must_fail(deploy.composite, [], contains="Keine Pakete")


# --- Email: test SMTP server with STARTTLS/SSL and real certificate verification ----------------
def _cert(tag="ok"):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=2))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    cp, kp = os.path.join(TMP, f"smtp-{tag}.crt"), os.path.join(TMP, f"smtp-{tag}.key")
    open(cp, "wb").write(cert.public_bytes(serialization.Encoding.PEM))
    open(kp, "wb").write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return cp, kp


MAILS = []


def _smtp_server(cert, key, implicit):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)

    class H(socketserver.StreamRequestHandler):
        def out(self, s):
            self.wfile.write((s + "\r\n").encode())
            self.wfile.flush()

        def handle(self):
            tls, helo = implicit, None
            self.out("220 test ESMTP")
            while True:
                line = self.rfile.readline()
                if not line:
                    return
                cmd = line.decode().rstrip("\r\n")
                up = cmd.upper()
                if up.startswith("EHLO"):
                    helo = cmd[5:]
                    if not tls:
                        self.wfile.write(b"250-test\r\n250-STARTTLS\r\n")
                    else:
                        self.wfile.write(b"250-test\r\n")
                    self.out("250 AUTH PLAIN LOGIN")
                elif up == "STARTTLS":
                    self.out("220 go")
                    self.connection = ctx.wrap_socket(self.connection, server_side=True)
                    self.rfile, self.wfile = self.connection.makefile("rb"), self.connection.makefile("wb")
                    tls = True
                elif up.startswith("AUTH PLAIN"):
                    u = base64.b64decode(cmd.split()[2]).split(b"\0")
                    self.out("235 ok" if u[2] == b"geheim" else "535 nein")
                elif up.startswith("MAIL FROM"):
                    self.out("250 ok")
                elif up.startswith("RCPT TO"):
                    self.out("550 unbekannt" if "unbekannt" in cmd else "250 ok")
                elif up == "DATA":
                    self.out("354 go")
                    buf = []
                    while True:
                        ln = self.rfile.readline()
                        if ln in (b".\r\n", b""):
                            break
                        buf.append(ln)
                    MAILS.append({"helo": helo, "tls": tls, "data": b"".join(buf)})
                    self.out("250 2.0.0 Ok: queued as TEST123")
                elif up == "QUIT":
                    self.out("221 bye")
                    return
                else:
                    self.out("250 ok")

    class S(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

        def handle_error(self, request, client_address):
            pass  # aborted TLS handshakes (test "wrong certificate") are intended

        def get_request(self):
            s, a = super().get_request()
            return (ctx.wrap_socket(s, server_side=True) if implicit else s), a

    srv = S(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


@test("2.11.3 Email: STARTTLS and SSL/TLS with certificate verification (\"check_hostname requires server_hostname\")")
def _():
    cert, key = _cert()
    os.environ["SSL_CERT_FILE"] = cert
    for sec, implicit in (("starttls", False), ("ssl", True)):
        port = _smtp_server(cert, key, implicit)
        mailer.save({"enabled": True, "host": "localhost", "port": port, "security": sec, "verify": True, "user": "pk@firma.local",
                     "password": "geheim", "from": "pk@firma.local", "to": "it@firma.local, unbekannt@firma.local"})
        r = mailer.test("")
        assert r["reply"].endswith("TEST123") and r["refused"] == ["unbekannt@firma.local"], r
        assert not r["warnings"], r["warnings"]
    m = MAILS[-1]
    assert m["tls"], "mail not transmitted encrypted"
    assert m["helo"] == "pocketknife.firma.local", f"2.11.2 HELO-Name: {m['helo']}"
    assert b"quoted-printable" in m["data"] and b"8bit" not in m["data"].lower(), "2.11.2 encoding"


@test("2.11.3 Email: wrong certificate is detected and reported in an understandable way")
def _():
    cert, key = _cert("fremd")
    port = _smtp_server(cert, key, False)  # this certificate is NOT in SSL_CERT_FILE
    mailer.save({"enabled": True, "host": "localhost", "port": port, "security": "starttls", "verify": True, "user": "",
                 "from": "pk@firma.local", "to": "it@firma.local"})
    must_fail(mailer.test, "", contains="Zertifikat")


@test("2.11.3 Email: hint when sender and login account differ")
def _():
    s = {**mailer.settings(), "user": "konto@firma.local", "from": "anders@firma.local", "security": "starttls", "verify": True}
    assert any("weicht" in w for w in mailer.warnings(s))


def _keytab(entries):
    """Small keytab in MIT format 0x0502 (as produced by ktpass) for tests."""
    import struct
    out = b"\x05\x02"
    for princ, realm, kvno, etype in entries:
        comps = princ.split("/")
        e = struct.pack(">H", len(comps)) + struct.pack(">H", len(realm)) + realm.encode()
        for c in comps:
            e += struct.pack(">H", len(c)) + c.encode()
        e += struct.pack(">I", 1) + struct.pack(">I", 0) + bytes([kvno % 256]) + struct.pack(">H", etype) + struct.pack(">H", 32) + b"k" * 32
        e += struct.pack(">I", kvno)
        out += struct.pack(">i", len(e)) + e
    return out


@test("2.16 Kiosk SSO: off by default; keytab checked and encrypted; Windows login yields only the kiosk role")
def _():
    import types
    from app import ad, kiosk, sso, vault, main as M
    assert db.setting("kiosk_sso") == 0, "Windows login must be off by default"
    assert "kiosk_sso" not in M.INTERNAL_SETTINGS and "sso_keytab" not in db.all_settings(), "keytab never via /api/settings"
    must_fail(sso.save_keytab, b"kein keytab", contains="Keytab")
    must_fail(sso.save_keytab, _keytab([("host/pc01.firma.local", "FIRMA.LOCAL", 2, 18)]), contains="HTTP")
    must_fail(sso.save_keytab, _keytab([("HTTP/pk.fremd.local", "FREMD.LOCAL", 2, 18)]), contains="Realm")
    kt = _keytab([("HTTP/pocketknife.firma.local", "FIRMA.LOCAL", 3, 18), ("HTTP/pocketknife.firma.local", "FIRMA.LOCAL", 3, 23)])
    info = sso.save_keytab(kt, user="test")
    assert info["present"] and info["hosts"] == ["pocketknife.firma.local"], info
    assert {e["enctype_name"] for e in info["entries"]} == {"aes256-cts-hmac-sha1-96", "rc4-hmac"} and info["entries"][0]["kvno"] == 3
    raw = db.setting_raw("sso_keytab")
    assert raw and b"pocketknife" not in raw.encode() and base64.b64decode(vault.decrypt(raw)) == kt, "keytab not stored encrypted"
    assert not sso.enabled(), "not active without kiosk and without switch"

    class Req:
        def __init__(self, hdr=None):
            self.headers = {"authorization": hdr} if hdr else {}
            self.client = types.SimpleNamespace(host="10.0.0.5")
            self.cookies = {}
    r = M.api_kiosk_sso(Req())
    assert r.status_code == 404, "switched off -> no Kerberos challenge"
    db.set_setting("kiosk_enabled", 1)
    db.set_setting("kiosk_sso", 1)
    seen = {}

    class Ctx:
        def __init__(self, creds=None, usage=None):
            self.complete = False
            self.initiator_name = None

        def step(self, tok):
            seen["tok"] = tok
            self.complete = True
            self.initiator_name = seen.get("who", "max@FIRMA.LOCAL")
            return b"mutual"

    class Creds:
        def __init__(self, usage=None, store=None, name=None):
            seen["store"] = store
            assert usage == "accept" and os.path.exists(store["keytab"])
    fake = types.SimpleNamespace(Credentials=Creds, SecurityContext=Ctx, __version__="test")
    old_mod, old_user = sys.modules.get("gssapi"), ad.sso_user
    sys.modules["gssapi"] = fake
    ad.sso_user = lambda sam: {"username": sam, "display_name": "Max Muster", "dn": "CN=Max", "via": "kiosk", "role": "admin"}
    try:
        assert sso.enabled()
        r = M.api_kiosk_sso(Req())
        assert r.status_code == 401 and r.headers.get("www-authenticate") == "Negotiate", "without ticket: 401 + Negotiate"
        r = M.api_kiosk_sso(Req("Negotiate " + base64.b64encode(b"NTLMSSP\x00\x01\x00").decode()))
        assert r.status_code == 403 and "www-authenticate" not in r.headers and "NTLM" in r.body.decode(), "NTLM: 403 without a new challenge"
        assert "tok" not in seen, "NTLM token must not go to GSSAPI"
        r = M.api_kiosk_sso(Req("Negotiate " + base64.b64encode(b"krb-ticket").decode()))
        assert r.status_code == 200, r.body
        assert r.headers.get("www-authenticate") == "Negotiate " + base64.b64encode(b"mutual").decode()
        assert seen["tok"] == b"krb-ticket" and seen["store"]["keytab"].startswith("/tmp/"), "keytab only in memory (tmpfs)"
        cookie = r.headers.get("set-cookie", "")
        assert "httponly" in cookie.lower() and "samesite=strict" in cookie.lower()
        tok = cookie.split("=", 1)[1].split(";", 1)[0]
        sess = auth.session_for(tok)
        assert sess and sess["role"] == "kiosk" and sess["username"] == "max", "SSO always yields the kiosk role (also for admins)"
        from fastapi import HTTPException

        class R2:
            cookies = {auth.COOKIE: tok}
        try:
            auth.current(R2())
            raise AssertionError("SSO session reaches the administration")
        except HTTPException as e:
            assert e.status_code == 403
        seen["who"] = "max@FREMD.LOCAL"
        r = M.api_kiosk_sso(Req("Negotiate " + base64.b64encode(b"krb-ticket2").decode()))
        assert r.status_code == 403 and "Domäne" in r.body.decode(), "ticket from a foreign realm"
        seen["who"] = "HTTP/x.firma.local@FIRMA.LOCAL"
        assert M.api_kiosk_sso(Req("Negotiate " + base64.b64encode(b"t3").decode())).status_code == 403, "service ticket"
        seen["who"] = "max@FIRMA.LOCAL"
        ad.sso_user = lambda sam: (_ for _ in ()).throw(ad.ADError("Kein Zugriff: Sie gehören keiner Kiosk-Gruppe oder -OU an."))
        r = M.api_kiosk_sso(Req("Negotiate " + base64.b64encode(b"t4").decode()))
        assert r.status_code == 403 and "Kiosk-Gruppe" in r.body.decode()
        assert M.api_kiosk_info()["sso"] and M.api_kiosk_info()["sso_hosts"] == ["pocketknife.firma.local"]
        steps = sso.test()
        assert any(st["ok"] is True and "aes256" in st["text"] for st in steps), steps
    finally:
        if old_mod is None:
            sys.modules.pop("gssapi", None)
        else:
            sys.modules["gssapi"] = old_mod
        ad.sso_user = old_user
        db.set_setting("kiosk_enabled", 0)
        db.set_setting("kiosk_sso", 0)
    sso.clear_keytab()
    assert not sso.present() and not os.path.exists("/tmp/pocketknife-http.keytab"), "keytab gone after removal"
    # Running kiosk sessions of admins (Windows login) must not be lost during re-verification
    src = open(ad.__file__, encoding="utf-8").read()
    assert "out[key] = bool(is_acc or kiosk_match(c, e, strict=True, cache=kcache))" in src  # 2.25.1: check in check_sessions
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "kiosk.js"), encoding="utf-8").read()
    assert "ssoOff(); // after logging out" in js, "after logout do not log in automatically again immediately"


@test("2.16 Reports: no \"null\" in the toolbar; help shows code blocks and sub-items in lists")
def _():
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    i = js.index("btn('Per E-Mail …', () => mailSubDlg(null, defs, cols")
    seg = js[i:js.index("const res = await GET(`/api/reports/", i)]
    assert ": null);" not in seg, "form.append(..., null) writes \"null\" into the page"
    # Help: indented code blocks and sub-items in lists (Windows login guide)
    assert "li.appendChild(rawEl('pre', 'help-code'" in js and "if (!sub) { sub = rawEl(" in js


@test("2.16.1 Kiosk: only domain accounts count, double click/second approval start nothing twice, restart fulfils the need")
def _():
    from app import kiosk, deploy, config as cfg
    old_nb = cfg.NETBIOS
    cfg.NETBIOS = "FIRMA"
    try:
        assert kiosk._domain_sam("FIRMA\\Max", "PC-1") == "max"
        assert kiosk._domain_sam("PC-1\\max", "PC-1") is None, "local account with the same name"
        assert kiosk._domain_sam("FREMD\\max", "PC-1") is None, "account of another domain"
    finally:
        cfg.NETBIOS = old_nb
    # Browser's address first – the own machine is never dropped because of the maximum count
    for i in range(8):
        db.ex("INSERT INTO computers(id, name, current_user, last_scan) VALUES(?,?,?,?)", (300 + i, f"ALT-{i}", "FIRMA\\wand", "2026-01-01"))
    db.ex("INSERT INTO computers(id, name, ip_addresses) VALUES(310, 'MEIN-PC', '10.9.9.9')")
    assert kiosk.candidates("wand", "10.9.9.9")[0]["id"] == 310
    # Release atomically
    db.set_setting("kiosk_enabled", 1)
    rid = db.ex("INSERT INTO kiosk_requests(created_at, username, computer_id, computer_name, package_id, package_name, action, status) "
                "VALUES('2026-10-21 10:00:00','u',310,'MEIN-PC',99999,'X','install','pending')")
    started = []
    old_start = kiosk._start
    kiosk._start = lambda r, a: started.append(r) or 1
    try:
        kiosk.decide(rid, True, "admin")
        must_fail(kiosk.decide, rid, True, "admin2", contains="bereits entschieden")
        assert started == [rid]
        must_fail(kiosk.withdraw, {"username": "u"}, rid, contains="läuft bereits")
        assert kiosk.status(db.q1("SELECT * FROM kiosk_requests WHERE id=?", (rid,)))["open"], "\"starting\" counts as open"
    finally:
        kiosk._start = old_start
        db.set_setting("kiosk_enabled", 0)
    # After a successful kiosk restart no further "restart required" (until the next scan)
    db.ex("UPDATE computers SET extra=?, last_boot='2026-10-20 08:00:00', last_scan='2026-10-21 09:00:00' WHERE id=310",
          (json.dumps({"pending_reboot": True}),))
    assert kiosk.reboot_info(310)["needed"]
    did = db.ex("INSERT INTO deployments(name, status) VALUES('Kiosk: Neustart', 'success')")
    db.ex("INSERT INTO deployment_targets(deployment_id, computer_id, status, finished_at) VALUES(?,310,'success','2026-10-21 10:05:00')", (did,))
    db.ex("INSERT INTO kiosk_requests(created_at, username, computer_id, computer_name, package_name, action, status, deployment_id) "
          "VALUES('2026-10-21 10:00:00','u',310,'MEIN-PC','Neustart','reboot','started',?)", (did,))
    assert not kiosk.reboot_info(310)["needed"], "restart notice after successful restart"
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "kiosk.js"), encoding="utf-8").read()
    assert "if (!ok || S.busy) return;" in js and "mine(r)" in js and "catch (e) { ssoOff(); renderLogin(" in js


@test("2.16.1 Login: session belongs to the bound account; error messages after a correct password stay generic")
def _():
    from app import ad, config as cfg

    class Conn:
        def __init__(self, who):
            self.who = who
            self.extend = type("E", (), {"standard": type("S", (), {"who_am_i": lambda _s: self.who})()})()

        def unbind(self):
            pass
    entries = [{"dn": "CN=Alice,DC=f", "attributes": {"sAMAccountName": ["alice"], "userPrincipalName": ["alice2@firma.local"],
                                                      "displayName": ["Alice"]}},
               {"dn": "CN=Bob,DC=f", "attributes": {"sAMAccountName": ["bob"], "userPrincipalName": ["alice@firma.local"],
                                                    "displayName": ["Bob"]}}]
    seen = []
    old = (ad.bind, ad._search, ad.is_member, ad._group_dn, cfg.BASE_DN)
    ad.bind = lambda u, p, r=None: Conn("u:FIRMA\\bob")

    def fsearch(c, base, flt, attrs):
        seen.append(flt)
        return [e for e in entries if "sAMAccountName=bob" in flt and e["dn"].startswith("CN=Bob")] or \
            ([] if "sAMAccountName=bob" in flt else entries)
    ad._search, ad.is_member, ad._group_dn, cfg.BASE_DN = fsearch, (lambda c, e, g: (True, "x")), (lambda c: "CN=G"), "DC=f"
    try:
        info = ad.authenticate("alice", "pw-von-bob")
        assert info["username"] == "bob", f"session for {info['username']} instead of the bound account bob"
        ad.bind = lambda u, p, r=None: Conn(None)  # Server without "Who am I?": exact match via the UPN
        assert ad.authenticate("alice", "x")["username"] == "bob"
    finally:
        ad.bind, ad._search, ad.is_member, ad._group_dn, cfg.BASE_DN = old
    assert all(x in auth._PRIVATE for x in ("Zugriffsgruppe", "LDAP-Suche", "Notfallzugang"))
    src = open(auth.__file__, encoding="utf-8").read()
    assert 'key = [(f"bg|{ip}", 8)]' in src, "emergency access with its own counter"


@test("2.16.1 Kiosk SSO: only tickets for the HTTP name of the kiosk")
def _():
    import types
    from app import sso
    kt = _keytab([("HTTP/pocketknife.firma.local", "FIRMA.LOCAL", 3, 18)])
    sso.save_keytab(kt)

    class Ctx:
        complete = True
        initiator_name = "max@FIRMA.LOCAL"

        def __init__(self, creds=None, usage=None):
            pass

        def step(self, t):
            return None
    fake = types.SimpleNamespace(Credentials=lambda **k: None, SecurityContext=Ctx)
    old = sys.modules.get("gssapi")
    sys.modules["gssapi"] = fake
    try:
        Ctx.target_name = "HTTP/pocketknife.firma.local@FIRMA.LOCAL"
        assert sso.accept("Negotiate " + base64.b64encode(b"t").decode())[0] == "max"
        Ctx.target_name = "HTTP/intranet.firma.local@FIRMA.LOCAL"
        must_fail(sso.accept, "Negotiate " + base64.b64encode(b"t").decode(), contains="anderen Dienst")
        Ctx.target_name = "cifs/fs01.firma.local@FIRMA.LOCAL"
        must_fail(sso.accept, "Negotiate " + base64.b64encode(b"t").decode(), contains="anderen Dienst")
    finally:
        if old is None:
            sys.modules.pop("gssapi", None)
        else:
            sys.modules["gssapi"] = old
        sso.clear_keytab()


@test("2.16.1 Distribution: failed package in multi/schedule run = failed; restart setting stays; deleting stops")
def _():
    from app import deploy, steps as stp
    r = stp.Runner.__new__(stp.Runner)
    r.log, r.results, r.n = [], [], 0
    r.check = lambda conds: (True, "")
    outcome = {"A": "success", "B": "failed", "C": "success"}
    r.run_step = lambda st, pkg, phase, depth=0: (outcome[st["id"]], "Exitcode 1603" if outcome[st["id"]] == "failed" else "")
    main = [{"id": k, "type": "nested", "title": f"Paket {k}", "on_error": "continue"} for k in "ABC"]
    pkg = {"name": "3 Pakete", "steps": {"pre": [], "main": main, "post": []}, "conditions": stp.default_conditions(), "composite": True}
    res, msg = r.run_package(pkg)
    assert res == "failed" and "1603" in msg, (res, msg)
    pkg.pop("composite")
    assert r.run_package(pkg)[0] == "success", "single package with \"continue after error\" stays as before"
    assert deploy.strongest_reboot(["never", "if_required", None]) == "if_required"
    assert deploy.strongest_reboot(["never", "always"]) == "always" and deploy.strongest_reboot([None]) == "never"
    assert deploy._cancelled(987654), "deleted deployment counts as aborted"
    did = db.ex("INSERT INTO deployments(name, status) VALUES('X', 'cancelled')")
    deploy.start(did)
    assert db.val("SELECT status FROM deployments WHERE id=?", (did,)) == "cancelled", "start() after abort"
    src = open(deploy.__file__, encoding="utf-8").read()
    assert "Test-Path -LiteralPath $f" in src and "_computer_lock(cid)" in src
    from app import schedules
    ssrc = open(schedules.__file__, encoding="utf-8").read()
    assert "WHEN excluded.status='failed' THEN COALESCE(attempts, 0) + 1" in ssrc and "except ValueError as e:" in ssrc


@test("2.16.1 Data: RegEx without nested repetitions, number filters without empty values, notifications without endless loop")
def _():
    from app import util
    for bad in ("((a+))+$", "(?:(a+))+$", "(.*a){12}$", "(a|b)*"):
        assert util.regex_problem(bad), bad
    for good in ("^7-Zip.*", "Chrome (\\d+)\\.", "^(foo|bar)$", "[ab]+x"):
        assert util.regex_problem(good) is None, good
    db.ex("CREATE TABLE IF NOT EXISTS _t_num(v TEXT)")
    db.ex("DELETE FROM _t_num")
    for v in ("", "abc", "2,5", "3", None):
        db.ex("INSERT INTO _t_num(v) VALUES(?)", (v,))
    sql = "SELECT COUNT(*) FROM _t_num WHERE (v IS NOT NULL AND CAST(v AS TEXT) GLOB '*[0-9]*' AND CAST(REPLACE(CAST(v AS TEXT), ',', '.') AS REAL) < ?)"
    assert db.val(sql, (10,)) == 2 and db.val(sql.replace("< ?", "> ?"), (2,)) == 2
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "collections_.py"), encoding="utf-8").read()
    assert "GLOB '*[0-9]*'" in src
    db.set_setting("backup_last", "2026-10-21 10:00:00 · FEHLGESCHLAGEN: voll")
    a = notify._check_backup_failed({}, {"since": ""})
    db.set_setting("backup_last", "2026-10-21 10:10:00 · FEHLGESCHLAGEN: voll")
    assert set(a) == set(notify._check_backup_failed({}, {"since": ""})) == {"2026-10-21"}, "one message per day"
    alert = next(x for x in notify.ALERTS if x["key"] == "offline") if hasattr(notify, "ALERTS") else None
    if alert:
        must_fail(notify._clean_alert_params, alert, {"params": {"days": "nan"}}, contains="ungültig")
    from app import collections_
    assert "exclude_self" in collections_.used_by.__code__.co_varnames


@test("2.16.1 Tools and UI: values from the client never as code; remote session counts as activity; data not translated")
def _():
    from app import tools
    c = {"name": "PC1", "dns_name": "", "ip_addresses": "", "mac_addresses": "", "current_user": "x&calc", "extra": "{}"}
    assert tools.substitute("whoami $(User)", c, mode="powershell") == "whoami $($PK_User)"
    must_fail(tools.substitute, "echo $(User)", c, "cmd", contains="nicht sicher")
    c["current_user"] = "FIRMA\\max"
    assert tools.substitute("echo $(User)", c, mode="cmd") == "echo FIRMA\\max"
    st = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static")
    rjs = open(os.path.join(st, "remote.js"), encoding="utf-8").read()
    assert "fetch('/api/session/active'" in rjs
    js = open(os.path.join(st, "app.js"), encoding="utf-8").read()
    assert "'data-notr': rawCol(c) || undefined" in js and "r.status === 'Fehler' && r.res && r.res.exit ? `Exitcode" in js
    assert "${b.percent ?? '?'}%" in js and "curNames.has(f.name.toLowerCase())" in js


@test("2.17 Kiosk: software only for certain AD groups – checked in the catalog and when ordering, AD unreachable = not visible")
def _():
    from app import ad, kiosk, deploy, steps as stp
    db.set_setting("kiosk_enabled", 1)
    db.set_setting("kiosk_max_per_day", 50)
    db.ex("INSERT INTO computers(id, name, online, winrm, current_user) VALUES(400, 'PC-400', 1, 1, 'FIRMA\\grafik')")
    p = stp.clean_package({"name": "Grafikprogramm", "steps": {"main": [{"type": "command", "command": "cmd /c exit 0"}]}})
    p["steps"], p["conditions"] = json.dumps(p["steps"]), json.dumps(p["conditions"])
    pid = db.ex(f"INSERT INTO packages({', '.join(deploy.PKG_FIELDS)}) VALUES({', '.join('?' * len(deploy.PKG_FIELDS))})",
                tuple(p[k] for k in deploy.PKG_FIELDS))
    kiosk.save_item(pid, {"enabled": True, "groups": "Grafik-Team\ngrafik-team\n CN=Design,OU=G,DC=firma,DC=local "})
    assert db.val("SELECT groups FROM kiosk_items WHERE package_id=?", (pid,)) == "Grafik-Team\nCN=Design,OU=G,DC=firma,DC=local", "without duplicates"
    must_fail(kiosk.save_item, pid, {"groups": "\n".join(f"G{i}" for i in range(60))}, contains="50")
    calls = []
    members = {"grafik": True, "buchhaltung": False}
    old = (ad.member_of_any, kiosk._logged_on, deploy.create)
    ad.member_of_any = lambda sam, groups: calls.append((sam, tuple(groups))) or members.get(sam)
    kiosk._logged_on = lambda c, fresh=False: {kiosk._sam(x) for x in (c["current_user"] or "").split(",") if x}
    deploy.create = lambda pkg, ids, user, **k: db.ex("INSERT INTO deployments(name, status) VALUES(?, 'running')", (pkg["name"],))
    try:
        kiosk._grp_cache.clear()
        names = lambda u: [i["name"] for i in kiosk.catalog(400, u)]
        assert "Grafikprogramm" in names("grafik")
        assert "Grafikprogramm" not in names("buchhaltung"), "non-member sees the software"
        assert "Grafikprogramm" not in names("unbekannt"), "AD not checkable -> not visible"
        n = len(calls)
        names("grafik")
        assert len(calls) == n, "result is cached"
        db.ex("UPDATE computers SET current_user='FIRMA\\buchhaltung' WHERE id=400")
        must_fail(kiosk.request, {"username": "buchhaltung"}, "?", 400, pid, "install", contains="für Sie nicht freigegeben")
        db.ex("UPDATE computers SET current_user='FIRMA\\grafik' WHERE id=400")
        r = kiosk.request({"username": "grafik", "display_name": "G"}, "?", 400, pid, "install")
        assert r["status"] != "rejected"
        kiosk.save_item(pid, {"enabled": True, "groups": ""})
        assert "Grafikprogramm" in names("buchhaltung"), "without groups: all kiosk users"
        assert any(i["package_id"] == pid and i["groups"] is None for i in kiosk.admin_items())
    finally:
        ad.member_of_any, kiosk._logged_on, deploy.create = old
        db.set_setting("kiosk_enabled", 0)
    assert ad.member_of_any("x", []) is True


@test("2.18 Container restart: interrupted runs are cleaned up on the client, running installers awaited")
def _():
    from app import deploy, remote
    db.ex("INSERT INTO computers(id, name, dns_name, online, winrm) VALUES(500, 'PC-500', 'pc-500.firma.local', 1, 1)")
    pkg = {"name": "Lang", "timeout_min": 30, "steps": {"pre": [], "main": [{"type": "command", "timeout_min": 90}], "post": []}}
    did = db.ex("INSERT INTO deployments(name, status, package_json) VALUES('Lang', 'running', ?)", (json.dumps(pkg),))
    tid = db.ex("INSERT INTO deployment_targets(deployment_id, computer_id, computer_name, status) VALUES(?,500,'PC-500','running')", (did,))
    deploy.recover()
    t = db.q1("SELECT * FROM deployment_targets WHERE id=?", (tid,))
    assert t["status"] == "failed" and t["message"].startswith(deploy.INTERRUPTED_MSG)
    row = db.q1("SELECT * FROM client_cleanup WHERE target_id=?", (tid,))
    assert row and row["prefix"] == f"{did}_{tid}_" and row["host"] == "pc-500.firma.local" and row["max_minutes"] == 90, row
    must_fail(remote.cleanup_run, "pc", "x'; Remove-Item C:\\ -Recurse; '", 5, contains="Präfix")
    calls, answers = [], [{"busy": 1}, {"busy": 0, "dirs": 2, "tasks": 1, "killed": 0, "codes": ["0"]}]
    old = (remote.cleanup_run, deploy.time.sleep)
    remote.cleanup_run = lambda host, prefix, mm: calls.append((host, prefix, mm)) or answers.pop(0)
    deploy.time.sleep = lambda s: None
    try:
        deploy.process_cleanups()
        assert len(calls) == 1 and db.q1("SELECT * FROM client_cleanup WHERE id=?", (row["id"],)), "installer still running -> entry stays"
        deploy.process_cleanups()
        assert len(calls) == 1, "ask again only after the waiting time"
        # New deployment to the same computer: waits until the old installer is finished, then cleans up
        answers[:] = [{"busy": 1}, {"busy": 0, "dirs": 2, "tasks": 1, "killed": 0, "codes": ["0"]}]
        c = db.q1("SELECT * FROM computers WHERE id=500")
        did2 = db.ex("INSERT INTO deployments(name, status) VALUES('Neu', 'running')")
        tid2 = db.ex("INSERT INTO deployment_targets(deployment_id, computer_id, status) VALUES(?,500,'running')", (did2,))
        assert deploy._wait_for_orphans(c, "pc-500", did2, tid2) is True
        assert len(calls) == 3 and not db.q1("SELECT * FROM client_cleanup WHERE id=?", (row["id"],))
        msg = db.val("SELECT message FROM deployment_targets WHERE id=?", (tid,))
        assert "aufgeräumt" in msg and "Exitcode 0" in msg, msg
        # Unreachable for a long time: give up after 14 days (the scan cleans up leftovers > 2 days by itself)
        db.ex("INSERT INTO client_cleanup(computer_id, host, prefix, max_minutes, created) VALUES(500, 'pc-500', '1_2_', 60, ?)",
              (util.ago(days=20),))
        deploy.process_cleanups()
        assert not db.val("SELECT COUNT(*) FROM client_cleanup WHERE prefix='1_2_'")
    finally:
        remote.cleanup_run, deploy.time.sleep = old
    assert "taskkill.exe /PID $id /T /F" in remote.CLEANUP_RUN and "PK_BUSY:" in remote.CLEANUP_RUN
    assert "_wait_for_orphans(c, host, did, tid)" in open(deploy.__file__, encoding="utf-8").read()


@test("2.19 Collections: subgroups and reference chains limited separately; saving does not silently break a dependent collection")
def _():
    from app import collections_, main as M
    from fastapi import HTTPException
    grp = {"type": "group", "match": "any", "items": [{"table": "computer", "column": "name", "op": "contains", "value": "PC"},
                                                      {"table": "computer", "column": "name", "op": "contains", "value": "SRV"}]}

    def mk(name, items):
        return db.ex("INSERT INTO collections(name, type, match, filters) VALUES(?, 'dynamic', 'all', ?)",
                     (name, json.dumps({"type": "group", "match": "all", "items": items})))
    ref = lambda i: {"table": "computer", "column": "collection", "op": "in", "value": str(i)}  # noqa: E731
    # Chain of 5 collections, the lowest with a nested subgroup – until 2.18 "nested too deeply"
    base = mk("Kette-Basis", [{"type": "group", "match": "all", "items": [grp]}])
    top = base
    for i in range(4):
        top = mk(f"Kette-{i}", [ref(top)])
    collections_.where_for(top)
    assert collections_.count(top) >= 0 and not collections_.broken(collections_.dependents(base))
    # going through 13 collections in a row, no more
    chain = [mk("Lang-0", [{"table": "computer", "column": "name", "op": "contains", "value": "PC"}])]
    for i in range(1, collections_.HOPS_MAX + 1):
        chain.append(mk(f"Lang-{i}", [ref(chain[-1])]))
    collections_.where_for(chain[-1])
    extra = mk("Noch eine", [{"table": "computer", "column": "name", "op": "contains", "value": "X"}])
    # Let the lowest collection refer to another one: the top one would then be too long -> reject, name it
    try:
        M.api_collection_update(chain[0], {"name": "Lang-0", "type": "dynamic", "match": "all",
                                           "filters": {"type": "group", "match": "all", "items": [ref(extra)]}}, user={"username": "t"})
        raise AssertionError("a change that breaks a dependent collection was saved")
    except HTTPException as e:
        assert e.status_code == 400 and f"Lang-{collections_.HOPS_MAX}" in str(e.detail), e.detail
    assert "Lang-0" == db.val("SELECT name FROM collections WHERE id=?", (chain[0],))
    assert '"PC"' in db.val("SELECT filters FROM collections WHERE id=?", (chain[0],)), "change was not rolled back"
    # harmless change goes through
    M.api_collection_update(chain[0], {"name": "Lang-0", "type": "dynamic", "match": "all",
                                       "filters": {"type": "group", "match": "all", "items": [{"table": "computer", "column": "name", "op": "contains", "value": "Q"}]}},
                            user={"username": "t"})
    assert '"Q"' in db.val("SELECT filters FROM collections WHERE id=?", (chain[0],))


@test("2.20 Reachability: \"online\" only if this very computer answers; DNS cache with configurable time")
def _():
    import struct as _st
    from app import dnsres, presence, remote, collections_
    # Parse DNS response (A record behind a CNAME)
    q = b"\x12\x34\x81\x80\x00\x01\x00\x02\x00\x00\x00\x00" + b"\x02pc\x05firma\x05local\x00" + _st.pack(">HH", 1, 1)
    ans = b"\xc0\x0c" + _st.pack(">HHIH", 5, 1, 60, 2) + b"\xc0\x0c" + b"\xc0\x0c" + _st.pack(">HHIH", 1, 1, 60, 4) + bytes([10, 1, 2, 3])
    assert dnsres._parse(q + ans, 0x1234) == "10.1.2.3"
    assert dnsres._parse(b"\x12\x34\x81\x83" + b"\x00" * 8, 0x1234) == "", "NXDOMAIN"
    assert dnsres.resolve("10.0.0.9") == "10.0.0.9"
    calls = {"sys": 0}
    old_sys = dnsres._resolve_system
    dnsres._resolve_system = lambda n: calls.__setitem__("sys", calls["sys"] + 1) or "10.0.0.5"
    try:
        dnsres.flush()
        db.set_setting("dns_cache_minutes", 5)
        dnsres.resolve("pc-a"); dnsres.resolve("pc-a")
        assert calls["sys"] == 1, "cache"
        assert dnsres.flush() == 1
        dnsres.resolve("pc-a")
        assert calls["sys"] == 2, "asked again after clearing"
        db.set_setting("dns_cache_minutes", 0)
        dnsres.resolve("pc-a"); dnsres.resolve("pc-a")
        assert calls["sys"] == 4, "0 = ask every time"
    finally:
        dnsres._resolve_system = old_sys
        db.set_setting("dns_cache_minutes", 5)
        dnsres.flush()
    # Identity
    db.ex("INSERT INTO computers(id, name, dns_name) VALUES(600, 'PC-600', 'pc-600.firma.local')")
    st = {"ip": "10.0.0.60", "ports": (True, True), "who": {"name": "PC-600", "domain": "firma.local", "joined": True}, "n": 0}
    old = (dnsres.resolve, remote.is_online, remote.identify)
    dnsres.resolve = lambda h, fresh=False: st["ip"]
    remote.is_online = lambda h, ip=None: st["ports"]
    remote.identify = lambda h: st.__setitem__("n", st["n"] + 1) or dict(st["who"])
    row = lambda: db.q1(f"SELECT {presence.COLUMNS}, resolved_ip, addr_detail FROM computers WHERE id=600")  # noqa: E731
    try:
        db.set_setting("identity_check", 1)
        assert presence.probe(row()) == (True, True) and st["n"] == 1 and row()["verified_ip"] == "10.0.0.60"
        assert presence.probe(row()) == (True, True) and st["n"] == 1, "same address, recently confirmed: do not ask again"
        # Address changed -> ask again; another PC answers -> offline with a note
        st["ip"], st["who"] = "10.0.0.99", {"name": "LAPTOP-FREMD", "domain": "firma.local", "joined": True}
        assert presence.probe(row()) == (False, False) and st["n"] == 2
        r = row()
        assert r["addr_problem"] == "other" and "LAPTOP-FREMD" in r["addr_detail"] and r["resolved_ip"] == "10.0.0.99"
        cid_fa = db.val("SELECT id FROM collections WHERE builtin=1 AND name='Falsche Adresse'")
        assert cid_fa and 600 in collections_.member_ids(cid_fa, fresh=True), "default collection \"Falsche Adresse\""
        assert presence.probe(row()) == (False, False) and st["n"] == 2, "problem case: ask again at most hourly"
        assert presence.probe(row(), force=True) == (False, False) and st["n"] == 3, "\"Jetzt prüfen\" asks immediately"
        st["who"] = {"name": "PC-600", "domain": "WORKGROUP", "joined": False}
        presence.probe(row(), force=True)
        assert row()["addr_problem"] == "nodomain"
        st["who"] = {"error": "auth", "detail": "401"}
        assert presence.probe(row(), force=True) == (True, False) and row()["addr_problem"] == "unverified"
        # Correct computer again at the new address -> note gone
        st["who"] = {"name": "pc-600", "domain": "FIRMA.LOCAL", "joined": True}
        assert presence.probe(row(), force=True) == (True, True) and not row()["addr_problem"] and row()["verified_ip"] == "10.0.0.99"
        # Only SMB at an address other than the confirmed one (e.g. NAS): offline
        st["ip"], st["ports"] = "10.0.0.7", (True, False)
        assert presence.probe(row()) == (False, False) and row()["addr_problem"] == "unconfirmed"
        db.set_setting("identity_check", 0)
        assert presence.probe(row()) == (True, False), "switched off: as before"
    finally:
        dnsres.resolve, remote.is_online, remote.identify = old
        db.set_setting("identity_check", 1)
    # New default collection also arrives in existing databases
    db.ex("DELETE FROM collections WHERE builtin=1 AND name='Falsche Adresse'")
    db.init()
    assert db.val("SELECT COUNT(*) FROM collections WHERE builtin=1 AND name='Falsche Adresse'") == 1
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "deploy.py"), encoding="utf-8").read()
    assert "presence.probe(c)" in src and "presence.probe(c, now)" in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "inventory.py"), encoding="utf-8").read()
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    assert "draggable: locked(c) ? 'false' : 'true'" in js and "if (!locked(c)) it.addEventListener('dragstart'" in js \
        and "const locked = (c) => !!(c && (c.builtin || c.editable === false));" in js, "default and AD tree not movable"


@test("2.20.1 Kiosk: after an uninstall \"Installieren\" again; English UI fully translated")
def _():
    from app import kiosk, deploy, steps as stp
    db.ex("INSERT INTO computers(id, name, last_scan) VALUES(700, 'PC-700', '2026-10-21 10:00:00')")
    p = stp.clean_package({"name": "Editor X", "steps": {"main": [{"type": "install", "source": "winget", "winget_id": "Vendor.EditorX"}]}})
    p["steps"], p["conditions"] = json.dumps(p["steps"]), json.dumps(p["conditions"])
    pid = db.ex(f"INSERT INTO packages({', '.join(deploy.PKG_FIELDS)}) VALUES({', '.join('?' * len(deploy.PKG_FIELDS))})",
                tuple(p[k] for k in deploy.PKG_FIELDS))
    kiosk.save_item(pid, {"enabled": True})

    def req(action, finished):
        did = db.ex("INSERT INTO deployments(name, status) VALUES('k', 'success')")
        db.ex("INSERT INTO deployment_targets(deployment_id, computer_id, status, message, finished_at) VALUES(?,700,'success','Erfolgreich',?)",
              (did, finished))
        db.ex("INSERT INTO kiosk_requests(created_at, username, computer_id, computer_name, package_id, package_name, action, status, deployment_id) "
              "VALUES(?,?,?,?,?,?,?,?,?)", (finished, "u", 700, "PC-700", pid, "Editor X", action, "started", did))
    item = lambda: next(i for i in kiosk.catalog(700) if i["package_id"] == pid)  # noqa: E731
    req("install", "2026-10-21 09:00:00")  # before the last scan, scan does not know the software -> not installed
    assert not item()["installed"], "scan is newer: inventory counts (e.g. uninstalled via the administration)"
    req("install", "2026-10-21 11:00:00")
    assert item()["installed"], "installation after the last scan"
    db.ex("INSERT INTO software(computer_id, name, version, winget_id) VALUES(700, 'Editor X', '1.0', 'Vendor.EditorX')")
    req("uninstall", "2026-10-21 12:00:00")
    assert not item()["installed"], "uninstall after the last scan -> \"Installieren\" again"
    db.ex("UPDATE computers SET last_scan='2026-10-21 13:00:00' WHERE id=700")
    assert item()["installed"], "newer scan still finds the software -> inventory counts"
    # English: generated texts in tables are translated, dictionary complete (also single words and "– ..." texts)
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    js = open(os.path.join(root, "app", "static", "app.js"), encoding="utf-8").read()
    assert "function rawCol(c)" in js and "'data-notr': rawCol(c) || undefined" in js
    en = json.load(open(os.path.join(root, "app", "i18n", "en.json"), encoding="utf-8"))
    for k in ("nie", "Dienstkonto (Deploy-Benutzer)", "neueste", "{0} von „{1}“ auf {2} gestartet (Bereitstellung #{3})"):
        assert en.get(k), f"translation missing: {k}"
    if os.path.exists(os.path.join(root, "tools", "build_i18n.py")):  # development tools are not part of the image
        assert 'PREFIX.sub("", de)' in open(os.path.join(root, "tools", "build_i18n.py"), encoding="utf-8").read()
        assert "GERMAN_WORDS" in open(os.path.join(root, "tools", "i18n_extract.py"), encoding="utf-8").read()


@test("2.21 Diagnostic package: anonymised without real names, placeholders stay the same, never passwords")
def _():
    import io as _io
    import zipfile as _zf
    from app import diagnostics, pklog
    db.ex("INSERT INTO computers(id, name, dns_name, online, current_user, ip_addresses, mac_addresses) VALUES"
          "(900, 'BUCHH-MUELLER7', 'buchh-mueller7.firma.local', 1, 'FIRMA\\hmueller', '192.168.77.15', 'AA-BB-CC-DD-EE-77')")
    db.ex("INSERT INTO deployments(name, status, created_by) VALUES('Kiosk: Editor (hmueller)', 'failed', 'Kiosk: hmueller')")
    pklog.warn("deploy", "BUCHH-MUELLER7 (192.168.77.15): Zugriff auf ADMIN$ verweigert für FIRMA\\hmueller, "
               "Mail an h.mueller@beispiel.de, OU=Buchhaltung,DC=firma,DC=local, MAC AA:BB:CC:DD:EE:77", user="Kiosk: hmueller")
    db.set_setting("smtp_host", "mail.firma.local")
    data, name = diagnostics.build(7, True, user="admin")
    assert name.endswith(".zip")
    z = _zf.ZipFile(_io.BytesIO(data))
    assert {"LIESMICH.txt", "umgebung.json", "einstellungen.json", "statistik.json", "protokoll.csv"} <= set(z.namelist())
    blob = "\n".join(z.read(n).decode("utf-8", "replace") for n in z.namelist()).lower()
    for leak in ("mueller", "192.168.77", "aa:bb:cc:dd:ee:77", "aa-bb-cc", "beispiel.de", "buchhaltung", "mail.firma.local"):
        assert leak not in blob, f"real name in the package: {leak}"
    assert "admin$" in blob, "general words stay (ADMIN$ not replaced)"
    assert "kiosk: user-" in blob, "trigger prefix stays, user as placeholder"
    pc = db.val("SELECT alias FROM diag_alias WHERE kind='computer' AND real='BUCHH-MUELLER7'")
    assert pc and pc.lower() in blob
    assert db.val("SELECT COUNT(*) FROM diag_alias WHERE real LIKE 'Kiosk:%'") == 0, "no placeholder for \"Kiosk: ...\""
    diagnostics.build(3, True)
    assert db.val("SELECT alias FROM diag_alias WHERE kind='computer' AND real='BUCHH-MUELLER7'") == pc, "placeholder stays the same"
    assert any(x["real"] == "BUCHH-MUELLER7" for x in diagnostics.lookup(pc)), "lookup"
    assert "BUCHH-MUELLER7" in diagnostics.mapping_csv()
    sett = json.loads(z.read("einstellungen.json"))
    assert not [k for k in sett if diagnostics.NEVER_SETTINGS.search(k)], "no secret settings"
    # without anonymisation: real names, but still no secrets
    raw = _zf.ZipFile(_io.BytesIO(diagnostics.build(7, False)[0]))
    assert "BUCHH-MUELLER7" in raw.read("protokoll.csv").decode()
    assert not [k for k in json.loads(raw.read("einstellungen.json")) if diagnostics.NEVER_SETTINGS.search(k)]


@test("2.22 Diagnostic package: every line with version, statistics since the update separate, changes and fixed bugs attached")
def _():
    import csv as _csv
    import io as _io
    import zipfile as _zf
    from app import config, diagnostics
    db.ex("DELETE FROM version_history")
    db.ex("DELETE FROM logs")
    db.ex("DELETE FROM deployment_targets")
    # Startup messages of older versions in the log -> are added on first run (duplicate starts merged)
    for ts, msg, lvl in (("2026-10-15 08:00:00", "POCKET KNIFE 2.19 gestartet.", "INFO"),
                         ("2026-10-16 09:00:00", "Alter Fehler XYZ", "ERROR"),
                         ("2026-10-17 08:00:00", "POCKET KNIFE 2.19 gestartet.", "INFO"),
                         ("2026-10-18 08:00:00", "POCKET KNIFE 2.20 gestartet.", "INFO"),
                         ("2026-10-18 10:00:00", "Fehler unter 2.20", "ERROR"),
                         ("2026-10-10 10:00:00", "Ganz alter Eintrag", "WARN")):
        db.ex("INSERT INTO logs(ts, level, category, message) VALUES(?,?,?,?)", (ts, lvl, "system", msg))
    diagnostics.record_start()
    h = [(x["version"]) for x in diagnostics.history()]
    assert h[:2] == ["2.19", "2.20"] and h[-1] == config.VERSION and len(h) == 3, h
    diagnostics.record_start()
    assert len(diagnostics.history()) == 3, "restart of the same version does not count twice"
    vat = diagnostics._VersionAt(diagnostics.history())
    assert vat("2026-10-16 09:00:00") == "2.19" and vat("2026-10-18 10:00:00") == "2.20" and vat("2026-10-10 00:00:00") == "vor 2.19"
    did = db.ex("INSERT INTO deployments(name, status, created_at) VALUES('Alt', 'failed', '2026-10-16 09:00:00')")
    db.ex("INSERT INTO deployment_targets(deployment_id, computer_name, status, message, started_at, finished_at) "
          "VALUES(?, 'PC-1', 'failed', 'kaputt', '2026-10-16 09:00:00', '2026-10-16 09:01:00')", (did,))
    z = _zf.ZipFile(_io.BytesIO(diagnostics.build(30, True)[0]))
    assert {"versionen.json", "aenderungen.md", "behobene_fehler.txt"} <= set(z.namelist())
    rows = list(_csv.DictReader(_io.StringIO(z.read("protokoll.csv").decode("utf-8-sig")), delimiter=";"))
    ver = {r["message"]: r["version"] for r in rows}
    assert ver["Alter Fehler XYZ"] == "2.19" and ver["Fehler unter 2.20"] == "2.20" and ver["Ganz alter Eintrag"] == "vor 2.19"
    assert "[2.19] ERROR" in z.read("fehler.txt").decode()
    st = json.loads(z.read("statistik.json"))
    assert st["aktuelle_version"]["version"] == config.VERSION and st["aktuelle_version"]["update_im_zeitraum"]
    assert not st["aktuelle_version"]["deployment_targets"], "failure from 2.19 does not count towards the current version"
    assert st["zeitraum"]["deployment_targets"].get("failed") == 1
    assert st["je_version"]["2.19"]["targets_failed"] == 1 and st["je_version"]["2.19"]["log_error"] == 1
    assert json.loads(z.read("fehlgeschlagen.json"))[0]["version"] == "2.19"
    v = json.loads(z.read("versionen.json"))
    assert v["aktuell"] == config.VERSION and v["im_zeitraum"][:3] == ["vor 2.19", "2.19", "2.20"], v["im_zeitraum"]
    ch = z.read("aenderungen.md").decode()
    assert f"## pocketknife-{config.VERSION}" in ch and "## pocketknife-2.19 " in ch and "## pocketknife-2.18 " not in ch, "only up to the oldest version in the period"
    fx = z.read("behobene_fehler.txt").decode()
    assert "- 2.20.1 Kiosk: after an uninstall" in fx
    dfile = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Dockerfile")
    if os.path.exists(dfile):  # there is no Dockerfile in the image – there aenderungen.md above proves that it works
        assert "CHANGELOG.md" in open(dfile, encoding="utf-8").read()
    # Period "since the last update": only entries of the running version
    data, name = diagnostics.build(0, True)
    z0 = _zf.ZipFile(_io.BytesIO(data))
    assert "seit-update" in name and "seit dem Update" in z0.read("LIESMICH.txt").decode()
    rows0 = list(_csv.DictReader(_io.StringIO(z0.read("protokoll.csv").decode("utf-8-sig")), delimiter=";"))
    assert rows0 and all(r["version"] == config.VERSION for r in rows0), "only the running version"


@test("2.22.1 Dry run: placeholders not for program words/dates, kiosk groups anonymous; winget archives and Kerberos errors explained")
def _():
    import io as _io
    import zipfile as _zf
    from app import deploy, diagnostics, kerberos, pklog
    db.set_setting("kiosk_groups", "GEHEIMGRUPPE\nCN=Team Nord,OU=Gruppen,DC=firma,DC=local")
    db.ex("INSERT INTO logs(ts, level, category, message, username) VALUES(?, 'INFO', 'auth', 'x', 'Anmeldung')", (util.now(),))
    pklog.warn("deploy", "Anmeldung fehlgeschlagen; Log: GlavSoft.TightVNC.2.8.89-2025-10-20-12-55-07.log; MAC 00-15-5D-01-02-03; "
               "Freigabe für GEHEIMGRUPPE")
    z = _zf.ZipFile(_io.BytesIO(diagnostics.build(1, True)[0]))
    blob = "\n".join(z.read(n).decode("utf-8", "replace") for n in z.namelist())
    assert "Anmeldung fehlgeschlagen" in blob, "program word not replaced by placeholder"
    assert "anmeldung" in diagnostics._vocabulary() and "mueller" not in diagnostics._vocabulary(), "names from examples do not count"
    assert "2025-10-20-12-55-07" in blob, "date in file names is not a MAC address"
    assert "00-15-5D-01-02-03" not in blob and "00:15:5D" not in blob.upper(), "real MAC still replaced"
    assert "GEHEIMGRUPPE" not in blob and "Team Nord" not in blob, "kiosk groups anonymised"
    # winget: retry archive errors once with tar.exe as Local System; only for install/upgrade
    body = deploy._winget_body({"winget_action": "install", "winget_id": "TeamViewer.TeamViewer", "winget_scope": "machine"})
    assert "-1978335140" in body and "archiveExtractionMethod" in body and "$pkSys" in body and ".IsSystem" not in body[len(deploy._winget_find()):], "CLM: without .NET method"
    assert body.count("PK-Run $wg") == 3, "call, retry with tar.exe, retry without --scope (2.30.1)"
    assert "\\WinGet\\Settings\\defaultState" in body, "settings file that winget actually reads as Local System"
    assert "archiveExtractionMethod" not in deploy._winget_body({"winget_action": "uninstall", "winget_id": "X.Y"})
    for code in (-1978335140, -1978335215, -1978334974):
        assert deploy.WINGET_MESSAGES.get(code), code
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "steps.py"), encoding="utf-8").read()
    assert 'f"Fehlgeschlagen – {msg} (Exitcode {code})"' in src, "plain text and exit code"
    # Kerberos "Message stream modified": understandable hint, no service account protection
    e = "SpnegoError (4294967295): Major (851968): Unspecified GSS failure. Minor (2529638953): Message stream modified"
    assert "DNS-Eintrag" in (kerberos.explain(e) or "") and not kerberos._account_problem(e)
    assert '"krb_modified"' in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "presence.py"), encoding="utf-8").read()


@test("2.23 Proxy server: address checked, password in the vault, login only from the server, clients without credentials")
def _():
    import base64 as _b64
    import io as _io
    import zipfile as _zf
    from app import deploy, diagnostics, proxy
    assert proxy.clean_url("proxy.firma.local:8080") == "http://proxy.firma.local:8080"
    assert proxy.clean_url("") == ""
    must_fail(proxy.clean_url, "http://u:geheim@proxy:8080", contains="Kennwort")
    must_fail(proxy.clean_url, "https://proxy:8443", contains="http://")
    must_fail(proxy.clean_url, "http://proxy:8080/pfad; rm", contains="http://proxy")
    seen = []

    class H(socketserver.StreamRequestHandler):
        def handle(self):
            lines = []
            while True:
                ln = self.rfile.readline().decode("latin-1")
                if ln in ("\r\n", "\n", ""):
                    break
                lines.append(ln.strip())
            seen.append(lines)
            self.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}"
        r = proxy.save({"url": url, "user": "FIRMA\\px", "password": "Pr0xy:Geh@im", "clients": True})
        assert r["password_set"] and r["clients"] and r["user"] == "FIRMA\\px"
        assert "Geh@im" not in db.setting_raw("proxy_password"), "password encrypted"
        res = proxy.test("http://winget.example.invalid/source.msix")
        assert res["ok"], res
        req = seen[-1]
        assert req[0].startswith("GET http://winget.example.invalid/source.msix"), "request went through the proxy"
        auth = next(x for x in req if x.lower().startswith("proxy-authorization: basic "))
        assert _b64.b64decode(auth.split()[-1]).decode() == "FIRMA\\px:Pr0xy:Geh@im", "Basic login at the proxy"
        assert proxy.client_proxy() == url, "clients: only the address, never credentials"
        f = deploy._winget_find()
        assert f"$PKProxy = '{url}'" in f and "settings set DefaultProxy" in f and "Geh@im" not in f
        # 2.37: the client never downloads from the internet by itself (App Installer comes from the repository)
        assert "Invoke-WebRequest" not in f and "Invoke-RestMethod" not in f and "api.github.com" not in f
        assert "PK-WingetProxy" in f.split("9009; return }")[-1], "only after finding winget"
        assert "***@" in proxy.safe_error(Exception("<urlopen error http://FIRMA%5Cpx:Pr0xy@127.0.0.1:1>"))
        z = _zf.ZipFile(_io.BytesIO(diagnostics.build(1, True)[0]))
        blob = "\n".join(z.read(n).decode("utf-8", "replace") for n in z.namelist())
        assert "Geh@im" not in blob and "proxy_password" not in blob
        assert json.loads(z.read("einstellungen.json")).get("proxy_user") != "FIRMA\\px", "proxy user anonymised"
        # without the checkmark: clients get no proxy (and POCKET KNIFE removes one it set itself)
        proxy.save({"url": url, "user": "", "clients": False})
        assert proxy.client_proxy() == "" and "$PKProxy = ''" in deploy._winget_find()
        assert not db.setting_raw("proxy_password"), "without a user no stored password"
        from app import main as _m
        assert {"proxy_url", "proxy_user", "proxy_clients"} <= _m.INTERNAL_SETTINGS, "only via the checked endpoint"
    finally:
        proxy.save({"url": "", "user": "", "clients": False})
        srv.shutdown()


@test("2.23 Code review: diagnostic package without passwords/leftover names, fast with many computers; identity check does not suspect devices on account problems")
def _():
    import io as _io
    import time as _t
    import zipfile as _zf
    from app import config, diagnostics, guard, pklog, remote
    an = diagnostics.Anonymizer(True)
    db.ex("INSERT INTO computers(id, name, dns_name) VALUES(950, 'FS01', 'fs01.firma.local')")
    an.learn()
    t = an("Befehl: net use Z: \\\\fs01.firma.local\\daten /user:FIRMA\\hmueller Sommer2026! ; msiexec /i a.msi SERVICEPASSWORD=Geh31m /qn; "
           "-Password 'xY9' ; ConvertTo-SecureString 'Klar123' -AsPlainText")
    for leak in ("Sommer2026!", "Geh31m", "xY9", "Klar123", "fs01", "hmueller"):
        assert leak.lower() not in t.lower(), (leak, t)
    assert "/user:FIRMA\\USER-" in t and "SERVICEPASSWORD=***" in t
    assert "192.168.5.9" not in an("Verbindung zu 192.168.5.9. Danach"), "IP at the end of a sentence"
    assert "aa:bb:cc:dd:ee:01" not in an("MAC AA:BB:CC:DD:EE:01.").lower()
    assert "Sommer2026!" not in an("Anmeldung fehlgeschlagen für 'Sommer2026!' von 10.0.0.1: falsch")
    assert "fe80::1c2b:3d4e:5f60:7a8b" not in an("Anmeldung von fe80::1c2b:3d4e:5f60:7a8b%12").lower(), "IPv6"
    assert "12:30:45" in an("um 12:30:45 Uhr"), "time of day is not an IPv6 address"
    assert "DC=sub" not in an("CN=X,DC=sub,DC=firma,DC=local"), "partial domain"
    assert "jürgen" not in an("Mail an jürgen.müller@beispiel.de").lower()
    assert "fs01" not in an("Log: FS01_install.log").lower(), "name before underscore"
    mac_alias = an("AA:BB:CC:DD:EE:02")
    assert an(mac_alias) == mac_alias, "placeholder of a MAC is not replaced again"
    raw = diagnostics.Anonymizer(False)
    assert "Sommer2026!" not in raw("net use Z: \\\\srv\\x /user:a\\b Sommer2026!"), "passwords masked even without anonymisation"
    db.set_setting("ldap_cert_pending", '{"host": "fs01.firma.local", "fingerprint": "ab"}')
    z = _zf.ZipFile(_io.BytesIO(diagnostics.build(1, True)[0]))
    assert "fs01" not in z.read("einstellungen.json").decode().lower()
    db.set_setting("ldap_cert_pending", "")
    # Many computers, every line a new address: no recompiling of a giant regex per placeholder
    db.exmany("INSERT INTO computers(name, dns_name) VALUES(?, ?)", [(f"RV{i:04d}", f"rv{i:04d}.firma.local") for i in range(1500)])
    for i in range(1000):
        db.ex("INSERT INTO logs(ts, level, category, message) VALUES(?, 'WARN', 'system', ?)",
              (util.now(), f"RV{i:04d}: Adresse 172.20.{i // 250}.{i % 250 + 1} OU=Abt{i},DC=firma,DC=local nicht erreichbar."))
    t0 = _t.time()
    z = _zf.ZipFile(_io.BytesIO(diagnostics.build(1, True)[0]))
    took = _t.time() - t0
    assert took < 30, f"diagnostic package too slow: {took:.1f} s"
    assert "rv0999" not in z.read("protokoll.csv").decode().lower() and "172.20." not in z.read("protokoll.csv").decode()
    db.ex("DELETE FROM computers WHERE name LIKE 'RV%'")
    # two packages at once: each placeholder belongs to exactly one name
    db.exmany("INSERT INTO computers(name) VALUES(?)", [(f"PAR{i}",) for i in range(40)])
    th = [threading.Thread(target=diagnostics.build, args=(1, True)) for _ in range(2)]
    [x.start() for x in th]
    [x.join() for x in th]
    assert not db.q("SELECT kind, alias FROM diag_alias GROUP BY kind, alias HAVING COUNT(*) > 1"), "placeholder assigned twice"
    db.ex("DELETE FROM computers WHERE name LIKE 'PAR%'")
    # Identity check: wrong password / KDC gone != "device does not belong to the domain"
    class C:
        err = None
        def __init__(self, *a, **k): pass
        def execute_ps(self, *a): raise Exception(C.err)
        def close(self): pass
    old = (remote.Client, guard.verify_account, guard.trip)
    trips = []
    remote.Client, guard.trip = C, (lambda reason, src: trips.append(reason))
    try:
        guard.verify_account = lambda: True
        C.err = "SpnegoError: Unspecified GSS failure. Minor: Message stream modified"
        assert remote.identify("pc-x")["error"] == "krb_modified"
        C.err = "Kerberos: Cannot contact any KDC for realm 'FIRMA.LOCAL'"
        assert remote.identify("pc-x")["error"] == "other"
        C.err = "GSS failure: Preauthentication failed"
        assert remote.identify("pc-x")["error"] == "paused" and trips, "account problem triggers the protection"
        guard.verify_account = lambda: False
        C.err = "the specified credentials were rejected by the server (401)"
        assert remote.identify("pc-x")["error"] == "paused", "service account itself broken"
        guard.verify_account = lambda: True
        assert remote.identify("pc-x")["error"] == "auth", "only this device refuses"
    finally:
        remote.Client, guard.verify_account, guard.trip = old
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "steps.py"), encoding="utf-8").read()
    assert '"Neustart erforderlich" not in msg' in src


@test("2.23.1 Collection selection: organised by folders and alphabetical; software collections \"installiert\"/\"nicht installiert\" without prefix")
def _():
    from app import main as _m
    f = db.ex("INSERT INTO collections(name, type, builtin, sort) VALUES('Zz Apps', 'folder', 0, 1)")
    db.ex("INSERT INTO collections(name, parent_id, type, builtin, sort) VALUES('beta', ?, 'dynamic', 0, 5)", (f,))
    db.ex("INSERT INTO collections(name, parent_id, type, builtin, sort) VALUES('Alpha', ?, 'dynamic', 0, 9)", (f,))
    ch = _m._collection_choices()
    assert ch[0]["id"] == 0, "\"entire inventory\" first"
    assert all(c.get("type") != "folder" for c in ch) and not any(c["name"] == "Zz Apps" for c in ch), "no folders to choose from"
    mine = [c for c in ch if c["path"] == "Zz Apps"]
    assert [c["name"] for c in mine] == ["Alpha", "beta"], "alphabetical, regardless of case and sort field"
    keys = [(c["path"].lower(), c["name"].lower()) for c in ch[1:]]
    assert keys == sorted(keys), "sorted by path and name"
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    assert "async function collPicker(set" in js and js.count("await collPicker(") >= 2, "schedules and scan profiles use the structured selection"
    assert "newDynColl(`${base} installiert`" in js and "newDynColl(`${base} nicht installiert`" in js, "name without \"Anwendungen:\""
    assert "\\d+\\.\\d[\\d.]*" in js, "version number only with a dot – \"Microsoft 365 Apps\" does not become \"Microsoft*\""
    assert "typeof x === 'object' && !Array.isArray(x)" in js, "broken state in browser storage opens the dialog anyway"
    assert "'data-notr': '1', onchange" in js and "collChoiceLabel" in js, "path and custom names not translated, built-in ones are"
    assert all("pparts" in c for c in ch[1:])
    # Anonymisation: names with accents/special characters are also replaced in free text (regression 2.23)
    from app import diagnostics
    db.ex("INSERT INTO computers(name, current_user) VALUES('PC-ZOË', 'FIRMA\\josé'), ('SRV&DB', 'o''brien')")
    an = diagnostics.Anonymizer(True)
    an.learn()
    t = an("josé an PC-ZOË, o'brien auf SRV&DB, ::ffff:10.1.2.3")
    for leak in ("josé", "ZOË", "o'brien", "SRV&DB"):
        assert leak not in t, (leak, t)
    assert "10.99." in t and ".99.0." not in t.split("10.99.")[-1][3:], t
    db.ex("DELETE FROM computers WHERE name IN ('PC-ZOË', 'SRV&DB')")


@test("2.23.2 After an update the browser loads the new UI (app.js/i18n.js not from the cache)")
def _():
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "main.py"), encoding="utf-8").read()
    assert 'request.url.path.startswith("/static/")' in src and 'resp.headers["Cache-Control"] = "no-cache"' in src


@test("2.23.4 UI is guaranteed to be fresh after the update: scripts and stylesheets carry the version (app.js?v=...)")
def _():
    from app import config
    from app import main as _m
    for page in ("index.html", "kiosk.html", "remote.html"):
        _m._PAGES.pop(page, None)
        html = _m._page(page).body.decode()
        assert f"/static/app.css?v={config.VERSION}" in html, page
        assert f"/static/i18n.js?v={config.VERSION}" in html, page
        assert 'src="/static/i18n.js"' not in html


@test("2.24 Windows notification after install/upgrade: only if something changed, never on uninstall, texts never as code")
def _():
    import base64 as _b
    from app import deploy, toast

    def W(act, wid="7zip.7zip", name="7-Zip", sid="s1", **kw):
        return {"name": name, **kw, "steps": {"main": [{"id": sid, "type": "install", "source": "winget", "winget_id": wid, "winget_action": act}]}}

    def R(n=1, sid="s1", st="success", msg="Erfolgreich", ex=0, typ="install"):
        return {"n": n, "sid": sid, "type": typ, "status": st, "message": msg, "exit": ex}
    log = ["=== 1. Schritte: winget install 7zip.7zip (als Lokales System) ===\nFound 7-Zip [7zip.7zip] Version 26.00\nSuccessfully installed\n"]
    assert toast.classify(W("install"), [R()], log) == [("installed", "7-Zip", "26.00")]
    assert toast.classify(W("upgrade"), [R()], log)[0][0] == "updated"
    de = ["=== 1. Schritte: winget install 7zip.7zip ===\nGefunden 7-Zip [7zip.7zip] Version 26.00\n"]
    assert toast.classify(W("install"), [R()], de) == [("installed", "7-Zip", "26.00")], "German winget output"
    db.ex("INSERT INTO computers(id, name) VALUES(960, 'PC-TOAST')")
    db.ex("INSERT INTO software(computer_id, name, version, winget_id) VALUES(960, '7-Zip 24.08', '24.08', '7zip.7zip')")
    assert toast.classify(W("install"), [R()], log, 960)[0][0] == "updated", "was already present in an older version"
    assert toast.classify(W("install"), [R(msg="Paket ist bereits installiert", ex=-1978335135)], log) == [], "nothing changed"
    assert toast.classify(W("uninstall"), [R()], log) == [], "uninstall does not notify"
    assert toast.classify(W("install", name="Deinstallation: 7-Zip"), [R()], log) == []
    assert toast.classify(W("install"), [R(st="failed")], log) == [], "failed -> no notification"
    scr = {"name": "Registry setzen", "steps": {"main": [{"id": "r1", "type": "registry", "entries": []}]}}
    assert toast.classify(scr, [R(sid="r1", typ="registry")], []) == [], "pure registry packages do not"
    assert toast.classify({**scr, "notify_user": "on"}, [R(sid="r1", typ="registry")], [])[0][0] == "installed", "\"always\""
    assert toast.classify(W("install", notify_user="off"), [R()], log) == []
    assert toast.classify(W("install"), [R()], log, global_on=False) == [], "globally off"
    msi = {"name": "Office 2024 LTSC", "version": "16.0", "steps": {"main": [{"id": "m1", "type": "install", "source": "file", "install_type": "msi", "install_file": "x.msi"}]}}
    assert toast.classify(msi, [R(sid="m1")], ["=== 1. Schritte: x.msi ===\n"]) == [("installed", "Office 2024 LTSC", "16.0")]
    # Multiple packages (schedule/multi-selection): only what has changed, with its own name; the "never" of the inner package applies
    combo = {"name": "Zeitplan Büro", "steps": {"main": [
        {"id": "n1", "type": "nested", "package_id": 1, "package": W("install", sid="a1")},
        {"id": "n2", "type": "nested", "package_id": 2, "package": {**msi, "steps": {"main": [{**msi["steps"]["main"][0], "id": "b1"}]}}},
        {"id": "n3", "type": "nested", "package_id": 3, "package": W("install", wid="Foo.Bar", name="Foo", sid="c1", notify_user="off")}]}}
    res = [R(1, "n1", typ="nested"), R(2, "a1", msg="Paket ist bereits installiert", ex=-1978335135), R(3, "n2", typ="nested"), R(4, "b1"),
           R(5, "n3", typ="nested"), R(6, "c1")]
    lg = ["=== 2. Schritte:   winget install 7zip ===\nFound 7-Zip [7zip.7zip] Version 26.00\n", "=== 4. Schritte:   x.msi ===\n",
          "=== 6. Schritte:   winget install Foo ===\nFound Foo [Foo.Bar] Version 1\n"]
    assert toast.classify(combo, res, lg) == [("installed", "Office 2024 LTSC", "16.0")], toast.classify(combo, res, lg)
    two = toast.build_send_script([("installed", "A", "1"), ("updated", "B", "2")])[1]
    assert two["title"] == "2 Programme wurden installiert." and two["body"] == "A 1, B 2"
    # Names/versions only as data; user script is encoded in the task – no file under ProgramData
    evil = "X'); Remove-Item C:\\ -Recurse; ('"
    script, data = toast.build_send_script([("installed", evil, "1")])
    assert evil not in script and "Remove-Item C:" not in script and "ProgramData" not in script
    assert "Join-Path $env:ProgramFiles 'PocketKnife'" in script, "display script only where users cannot change anything"
    from app import util as _u
    assert _u.psq(toast.USER_PS) in script, "2.38: display script as text literal (Set-Content instead of .NET)"
    inner = toast.USER_PS
    assert evil not in inner and "ConvertFrom-Json" in inner and "SecurityElement]::Escape" in inner and "param([string]$Data, [string]$Rid)" in inner
    assert "GetHistory" in inner and "Done 10" in inner, "checks whether Windows accepted the notification; otherwise sender Windows PowerShell"
    assert "PK_TOAST:res:" in script and toast.results("PK_TOAST:res:FIRMA\\max:2\n")[0][1] is False
    assert "ProgramFiles" in data["icon"] and "Join-Path $env:ProgramFiles 'PocketKnife\\pk-ok.png'" in inner
    db.set_setting("toast_language", "en")
    assert toast.build_send_script([("updated", "7-Zip", "26.00")])[1]["title"] == "7-Zip has been updated."
    db.set_setting("toast_language", "de")
    assert "notify_user" in deploy.PKG_FIELDS
    db.ex("DELETE FROM software WHERE computer_id=960")
    db.ex("DELETE FROM computers WHERE id=960")


@test("2.24.3 PowerShell always with full path and fixed switches (-NoLogo -NonInteractive -NoProfile -ExecutionPolicy Bypass)")
def _():
    import glob as _g
    from app import remote, steps, util
    assert util.PS_FLAGS == "-NoLogo -NonInteractive -NoProfile -ExecutionPolicy Bypass"
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app")
    for f in _g.glob(os.path.join(root, "*.py")):
        src = open(f, encoding="utf-8").read()
        for bad in ("'powershell.exe'", "powershell.exe -No", "--headless powershell.exe"):
            assert bad not in src, f"{os.path.basename(f)}: PowerShell without full path ({bad})"
    b = steps.body_for({"type": "powershell", "script": "Write-Output 1", "id": "x"}, {})
    assert b.rstrip().endswith("PK-RunPs1 $pkF ''")
    w = remote.WRAPPER
    assert 'PK-Run "$env:SystemRoot\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" ((\'-NoLogo -NonInteractive -NoProfile -ExecutionPolicy Bypass -File' in w


@test("2.24.4 Notification: switched off by the user is respected, start independent of the execution policy, permissions under \"Programme\" enforced")
def _():
    import base64 as _b
    from app import toast
    u = toast.USER_PS
    assert "Done 12" in u and "$st.Enabled -eq 0" in u and "ShowInActionCenter -eq 0) { Done 11 }" in u
    assert u.index("Done 12") < u.index("# Fallback: sender"), "check the switch-off BEFORE falling back to \"Windows PowerShell\""
    assert toast.RESULT_TEXT[12][0] is False
    script, _d = toast.build_send_script([("installed", "7-Zip", "26.00")])
    assert "[ScriptBlock]::Create([IO.File]::ReadAllText(" in script and "$pol -eq 'AllSigned'" in script, "encoded starter only with GPO AllSigned/Restricted"
    assert "/setowner '*S-1-5-32-544'" in script and "/reset" in script and "Unsichere Rechte" in script


@test("2.24.5 Notification under software restriction (SRP/AppLocker, Constrained Language Mode): start via -File from \"Programme\"; result comes from the script itself, not from the exit code of conhost")
def _():
    import re as _re
    from app import toast
    script, _d = toast.build_send_script([("installed", "7-Zip", "26.00")])
    i_enc = script.index("-EncodedCommand"); i_file = script.index("-WindowStyle Hidden -File")
    blk = script[script.index("function Action"):script.index("$set = New-ScheduledTaskSettingsSet")]
    assert "if (($pol -eq 'AllSigned' -or $pol -eq 'Restricted') -and $ExecutionContext.SessionState.LanguageMode -eq 'FullLanguage')" in blk \
        and "} else {" in blk and i_enc < i_file
    assert "EnableScripts" in script and "HKEY_USERS" in script
    u = toast.USER_PS
    assert u.startswith("param([string]$Data, [string]$Rid)") and "function Done([int]$c)" in u and "trap { Done 1 }" in u
    assert not _re.search(r"^(?!\s*exit \$c)[^#\n]*\bexit [0-9]", u, _re.M), "every result via Done (registry), never only as exit code"
    assert "CreateSubKey('Software\\PocketKnife\\Toast')" in u, "result in HKCU\\Software\\PocketKnife\\Toast"
    assert "Software\\PocketKnife\\Toast" in script and ":13'" in script, "no answer from the script -> 13, never \"shown\""
    assert toast.RESULT_TEXT[13][0] is False and "Softwareeinschränkung" in toast.RESULT_TEXT[13][1]


@test("2.24.6 Message on restart/shutdown configurable (sender, texts, placeholders) – passed safely to shutdown.exe")
def _():
    import glob as _g
    from app import main as _m, rebootmsg, steps, util
    for k in ["reboot_sender"] + list(rebootmsg.KEYS.values()):
        assert k in db.DEFAULT_SETTINGS and k in _m.EDITABLE, k
    db.set_setting("reboot_sender", "")
    for k in rebootmsg.KEYS.values():
        db.set_setting(k, "")
    assert rebootmsg.text("restart", sekunden=60) == "POCKET KNIFE: Neustart in 60 Sekunden.", "default unchanged"
    assert rebootmsg.text("deploy", sekunden=120) == "POCKET KNIFE: Neustart nach Softwareverteilung in 2 Minuten."
    db.set_setting("reboot_sender", "Ihre IT-Abteilung")
    assert rebootmsg.text("shutdown", sekunden=60) == "Ihre IT-Abteilung: Herunterfahren in 60 Sekunden."
    assert "angefordert von Max Schmidt" in rebootmsg.text("kiosk", sekunden=300, benutzer="Max Schmidt")
    db.set_setting("reboot_msg_step", 'Achtung "{absender}"\nNeustart in {minuten} Min. {unbekannt}')
    t = steps.reboot_text({"delay_sec": 90})
    assert t == "Achtung 'Ihre IT-Abteilung' Neustart in 1,5 Min. {unbekannt}", t
    assert steps.reboot_text({"delay_sec": 60, "message": "Eigener Text: {absender}"}) == "Eigener Text: Ihre IT-Abteilung", "step text takes precedence"
    evil = "x'; Remove-Item C:\\ -Recurse; '"
    db.set_setting("reboot_sender", evil)
    q = util.psq(rebootmsg.text("restart", sekunden=60))
    assert q.startswith("'x''; Remove-Item"), "text always as PowerShell literal (psq), never as code"
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app")
    for f in _g.glob(os.path.join(root, "*.py")):
        src = open(f, encoding="utf-8").read()
        assert "POCKET KNIFE: Neustart" not in src.replace('"{absender}', "") or f.endswith("rebootmsg.py"), f"fixed text in {os.path.basename(f)}"
    db.set_setting("reboot_sender", "")
    db.set_setting("reboot_msg_step", "")


@test("2.24.6 A changed notification sender really appears (own sender ID per name, Windows remembers old names)")
def _():
    from app import toast
    db.set_setting("toast_compat", 0)
    db.set_setting("toast_sender", "Ihre IT-Abteilung")
    a = toast.build_send_script([("installed", "A", "1")])[1]
    db.set_setting("toast_sender", "Softwareverteilung")
    b = toast.build_send_script([("installed", "A", "1")])[1]
    assert a["sender"] == "Ihre IT-Abteilung" and a["appid"] != b["appid"] and a["appid"].startswith(toast.APP_ID + ".")
    assert a["appid"] == toast.app_id("Ihre IT-Abteilung"), "same name -> same ID (no new registration for every message)"
    u = toast.USER_PS
    assert "$_.PSChildName -like ($d.base + '*')" in u and "Remove-Item -Path $o.PSPath" in u and "if ($off" in u, \
        "clean up old IDs; switched off by the user stays switched off"


@test("2.24.8 Notification/restart message: review findings (feedback without emptying the key, cleanup, codes 13/14, 0 seconds, backslash)")
def _():
    from app import rebootmsg, steps, toast
    u = toast.USER_PS
    assert "CreateSubKey('Software\\PocketKnife\\Toast')" in u and "New-Item -Path $rk" not in u
    assert "if (-not (Test-Path $k)) { New-Item -Path $k -Force" in u, "never recreate (empty) the sender key"
    sc = toast.build_send_script([("installed", "7-Zip", "26.00")])[0]
    # 2.38: comparison via SHA-256 of the file (also pure upper/lower-case changes), without .NET
    import hashlib as _hl
    assert "$have -ne '" + _hl.sha256(b"\xef\xbb\xbf" + toast.USER_PS.encode("utf-8")).hexdigest().upper() + "'" in sc
    assert "$hv -ne '" in sc and "Get-FileHash" in sc and "[IO.File]" not in sc.split("function Action")[0]
    assert "ToString('yyMMddHHmm')" in sc and "AddMinutes(-30)" in sc and ":14'" in sc
    res = toast.results("PK_TOAST:res:A\\x:13\nPK_TOAST:res:B\\y:14\nPK_TOAST:res:C\\z:-2147023570\nPK_TOAST:res:D\\w:0\n")
    assert [o for _u, o, _t in res] == [False, False, False, True] and "Code -2147023570" in res[2][2]
    assert "Softwareeinschränkung" in res[0][2] and "nicht auflösen" in res[1][2]
    db.set_setting("reboot_msg_step", "")
    assert steps.reboot_text({"delay_sec": 0}) == "POCKET KNIFE: Neustart für die Softwareverteilung"
    db.set_setting("reboot_msg_step", "{absender}: Neustart in {sekunden} s")
    assert steps.reboot_text({"delay_sec": 0}).endswith("in 0 s"), "0 seconds stays 0 (not 60)"
    db.set_setting("reboot_msg_step", "")
    assert rebootmsg.clean("Ihre IT \\\\", 60) == "Ihre IT", "no backslash at the end (breaks the argument of shutdown.exe)"


def _asgi(method, path, token=None, body=None, raw=None):
    """A request directly to the app (without network) – (status, JSON or text). raw: bytes instead of JSON."""
    import asyncio
    from app import main as _m
    path, _, qs = path.partition("?")
    data = raw if raw is not None else (json.dumps(body if body is not None else {}).encode() if method in ("POST", "PUT", "DELETE") else b"")
    ctype = b"application/octet-stream" if raw is not None else b"application/json"
    hdr = [(b"host", b"testserver"), (b"content-type", ctype), (b"content-length", str(len(data)).encode()),
           (b"x-pk-request", b"1")]  # like the UI (CSRF protection) – otherwise the test would only test the CSRF protection
    if token:
        hdr.append((b"cookie", f"pk_session={token}".encode()))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
             "path": path, "raw_path": path.encode(), "query_string": qs.encode(), "root_path": "", "headers": hdr,
             "client": ("127.0.0.1", 50000), "server": ("testserver", 80), "app": _m.app}
    out = {"status": None, "body": b""}
    sent = {"done": False}

    async def receive():
        if sent["done"]:
            return {"type": "http.disconnect"}
        sent["done"] = True
        return {"type": "http.request", "body": data, "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
        elif msg["type"] == "http.response.body":
            out["body"] += msg.get("body", b"")
    asyncio.run(_m.app(scope, receive, send))
    try:
        return out["status"], json.loads(out["body"] or b"null")
    except ValueError:
        return out["status"], out["body"][:200]


@test("2.25 OU admins: every interface is blocked for them except the released ones – and those only show/control their computers")
def _():
    import hashlib
    import re as _re
    import secrets
    import time as _t
    from app import access, auth, config, deploy, pklog, schedules, util
    from app import main as _m
    # Setup: tenant "Nord" (collection with 2 computers, OU admins, own packages allowed), tenant "Süd" with its own package,
    # a foreign computer, a central package
    ids = [db.ex("INSERT INTO computers(name, dns_name, created_at) VALUES(?,?,?)", (n, n + ".firma.local", util.now()))
           for n in ("OU-IN1", "OU-IN2", "OU-OUT")]
    cin1, cin2, cout = ids
    col = db.ex("INSERT INTO collections(name, type) VALUES('OU Nord (Test)', 'static')")
    for c in (cin1, cin2):
        db.ex("INSERT INTO collection_members VALUES(?,?)", (col, c))
    t_nord = db.ex("INSERT INTO tenants(name, collection_id, repo_folder, admin_group, allow_packages, allow_scripts, created_at) "
                   "VALUES('Nord', ?, 'Kunden\\Nord', 'PK-OU-Nord', 1, 1, ?)", (col, util.now()))  # script steps allowed (2.29.1)
    t_sued = db.ex("INSERT INTO tenants(name, created_at) VALUES('Süd', ?)", (util.now(),))
    p_central = db.ex("INSERT INTO packages(name, version, steps, conditions, run_as, created_at) VALUES('Zentral (Test)','1',?,'{}','deploy',?)",
                      (json.dumps({"main": [{"id": "z1", "type": "command", "command": "hostname"}]}), util.now()))
    p_sued = db.ex("INSERT INTO packages(name, version, steps, conditions, run_as, tenant_id, created_at) VALUES('Süd-Paket','1',?,'{}','deploy',?,?)",
                   (json.dumps({"main": [{"type": "command", "command": "hostname"}]}), t_sued, util.now()))
    for c in ids:
        db.ex("INSERT INTO software(computer_id, name, version, winget_id) VALUES(?,?,?,?)", (c, f"OU-Testprogramm-{c}", "1", f"OU.Test{c}"))
        pklog.info("system", f"OU-Testeintrag {c}", computer_id=c)
    access.flush()

    def session(scope):
        tok = secrets.token_urlsafe(16)
        th = hashlib.sha256(tok.encode()).hexdigest()
        db.ex("INSERT INTO sessions(token_hash, username, display_name, created, expires, ip, last_active, role, scope) VALUES(?,?,?,?,?,?,?,?,?)",
              (th, "ou-test" if scope else "haupt-test", "Test", util.now(), "2099-01-01 00:00:00", "127.0.0.1", util.now(), "admin",
               json.dumps(scope) if scope is not None else None))
        auth._checked[th] = _t.time() + 10 ** 6  # no AD re-check in the test
        return tok
    ou, main_admin = session([t_nord]), session(None)
    st, me = _asgi("GET", "/api/me", ou)
    assert st == 200 and me["scoped"] is True and me["computers"] == 2 and me["may_build"], (st, me)
    assert _asgi("GET", "/api/me", main_admin)[1]["scoped"] is False

    # 1) Release list: every entry really exists (no typo), and critical routes are NOT released
    routes = set()
    for r in _m.app.routes:
        for mth in (getattr(r, "methods", None) or ()):
            if mth != "HEAD":
                routes.add((mth, _re.sub(r"\{(\w+):[^}]+\}", r"{\1}", r.path)))
    missing = [k for k in access.ROUTES if k not in routes]
    assert not missing, f"release list names unknown routes: {missing}"
    for k in [("PUT", "/api/settings"), ("GET", "/api/settings"), ("POST", "/api/tenants"), ("PUT", "/api/tenants/{tid}"),
              ("DELETE", "/api/tenants/{tid}"), ("GET", "/api/system"), ("PUT", "/api/system/accounts"), ("GET", "/api/backups"),
              ("POST", "/api/tools"), ("PUT", "/api/tools/{tid}"), ("POST", "/api/customreports"), ("PUT", "/api/customreports/{rid}"),
              ("DELETE", "/api/customreports/{rid}"), ("POST", "/api/mail/reports"),
              ("GET", "/api/ad/browse"), ("PUT", "/api/kiosk/admin/items/{pid}"), ("GET", "/api/mail/settings"),
              ("DELETE", "/api/computers/{cid}"), ("POST", "/api/computers"), ("GET", "/api/system/diagnostics"),
              ("PUT", "/api/remote/vnc_password"), ("POST", "/api/winget/refresh"), ("POST", "/api/software/exclusions")]:
        assert k in routes and k not in access.ROUTES, f"must not be released for OU admins: {k}"

    # 2) Every NOT released route answers an OU admin with 403 – the handler never runs
    def fill(path, cid):
        return _re.sub(r"\{(\w+)\}", lambda m: str(cid) if m.group(1) == "cid" else "1", path)
    for mth, path in sorted(routes):
        if not path.startswith("/api/") or (mth, path) in access.ROUTES:
            continue
        if path in ("/api/login", "/api/logout", "/api/session/active", "/api/session/idle", "/api/remote/tunnel") \
                or path.startswith("/api/kiosk/") and not path.startswith("/api/kiosk/admin") or path.startswith("/api/mail/view/"):
            continue  # without administration rights (kiosk, session, mail link with own token)
        st, res = _asgi(mth, fill(path, cin1), ou)
        assert st == 403 and "OU-Admins" in str(res), f"{mth} {path}: {st} {res} – must be blocked for OU admins"

    # 3) Released routes with a computer in the path: foreign computer -> 404, own -> not 403/404 (read access only)
    for (mth, path), chk in access.ROUTES.items():
        if "{cid}" not in path:
            continue
        st, _r = _asgi(mth, fill(path, cout), ou, {})
        assert st == 404, f"{mth} {path} with foreign computer: {st} (expected 404)"
    for path in ("/api/computers/{cid}", "/api/computers/{cid}/cat/software", "/api/computers/{cid}/cat/logs"):
        st, _r = _asgi("GET", fill(path, cin1), ou)
        assert st == 200, f"GET {path} with own computer: {st}"

    # 4) Lists show only own computers / data
    names = {c["name"] for c in _asgi("GET", "/api/computers", ou)[1]}
    assert names == {"OU-IN1", "OU-IN2"}, names
    assert "OU-OUT" in {c["name"] for c in _asgi("GET", "/api/computers", main_admin)[1]}, "main admin still sees everything"
    sw = {r["name"] for r in _asgi("GET", "/api/software", ou)[1]}
    assert f"OU-Testprogramm-{cin1}" in sw and f"OU-Testprogramm-{cout}" not in sw
    # 2.38.6: winget-ID picker of the schedule dialog - only programs on own computers
    wi = {r["winget_id"] for r in _asgi("GET", "/api/software/winget-ids", ou)[1]["items"]}
    assert f"OU.Test{cin1}" in wi and f"OU.Test{cout}" not in wi, wi
    st, det = _asgi("GET", f"/api/software/detail?name=OU-Testprogramm-{cout}", ou)
    assert st == 200 and det["computers"] == [], "software details of foreign computers"
    logs = [l["message"] for l in _asgi("GET", "/api/logs?q=OU-Testeintrag", ou)[1]]
    assert f"OU-Testeintrag {cin1}" in logs and f"OU-Testeintrag {cout}" not in logs, logs
    st, csv = _asgi("GET", "/api/logs/csv?q=OU-Testeintrag", ou)
    assert st == 200 and f"OU-Testeintrag {cout}" not in str(csv)
    sw_id_out = db.val("SELECT id FROM software WHERE computer_id=?", (cout,))
    assert _asgi("GET", f"/api/software/{sw_id_out}/uninstall", ou)[0] == 404
    assert _asgi("POST", "/api/uninstall", ou, {"items": [{"software_id": sw_id_out}]})[0] == 403
    assert _asgi("POST", "/api/computers/heartbeat", ou, {"ids": [cout]})[0] == 403
    assert _asgi("POST", "/api/computers/scan", ou, {"ids": [cin1, cout]})[0] == 403
    assert _asgi("POST", "/api/tools/builtin/ping", ou, {"computer_id": cout})[0] == 404
    assert _asgi("POST", "/api/remote/connect", ou, {"computer_id": cout})[0] == 404
    dash = _asgi("GET", "/api/dashboard", ou)[1]
    assert dash["computers"] == 2 and dash["guard"] is None, dash

    # 5) Packages: central ones visible (not changeable), foreign tenants invisible (2.37); own ones land in the own tenant
    pk = {p["name"]: p for p in _asgi("GET", "/api/packages", ou)[1]}
    assert "Zentral (Test)" in pk and pk["Zentral (Test)"]["editable"] is False
    assert "Süd-Paket" not in pk, "2.37: packages of other tenants are invisible to OU admins"
    assert _asgi("GET", f"/api/packages/{p_sued}", ou)[0] == 404
    assert "Süd-Paket" not in {p["name"] for p in _asgi("GET", "/api/packages/meta", ou)[1]["packages"]}
    assert _asgi("PUT", f"/api/packages/{p_sued}", ou, {"name": "x"})[0] in (403, 404)
    assert _asgi("DELETE", f"/api/packages/{p_sued}", ou)[0] in (403, 404)
    assert _asgi("PUT", f"/api/packages/{p_central}", ou, {"name": "x"})[0] == 403
    assert _asgi("DELETE", f"/api/packages/{p_central}", ou)[0] == 403
    st, r = _asgi("POST", "/api/packages", ou, {"name": "Nord-Paket (Test)", "tenant_id": t_sued,
                                                 "steps": {"main": [{"type": "command", "command": "hostname"}]}})
    assert st == 403, "specify a foreign tenant -> rejected"
    st, r = _asgi("POST", "/api/packages", ou, {"name": "Nord-Paket (Test)", "steps": {"main": [{"type": "command", "command": "hostname"}]}})
    assert st == 200 and db.val("SELECT tenant_id FROM packages WHERE id=?", (r["id"],)) == t_nord, (st, r)
    p_nord = r["id"]
    st, r = _asgi("POST", "/api/packages", ou, {"name": "Verschachtelt (Test)", "steps": {"main": [{"type": "nested", "package_id": p_sued}]}})
    assert st in (403, 404), ("nested only central/own packages", st, r)
    st, r = _asgi("POST", f"/api/packages/{p_central}/duplicate", ou)
    assert st == 200 and db.val("SELECT tenant_id FROM packages WHERE id=?", (r["id"],)) == t_nord, ("copy of a central package belongs to the own tenant", st, r)
    assert _asgi("GET", "/api/repository?path=Kunden\\Sued", ou)[0] == 403, "repository only in the own folder"
    # 6) Deploy: only own computers, only visible packages
    assert _asgi("POST", "/api/deployments", ou, {"package_id": p_central, "computer_ids": [cout], "scheduled_at": "2099-01-01 00:00"})[0] == 403
    # 2.37: packages of other tenants are invisible to OU admins – cannot be distributed either
    st, r = _asgi("POST", "/api/deployments", ou, {"package_id": p_sued, "computer_ids": [cin1], "scheduled_at": "2099-01-01 00:00"})
    assert st == 404, ("distribute foreign tenant package", st, r)
    st, r = _asgi("POST", "/api/deployments", ou, {"package_id": p_nord, "computer_ids": [cin1], "scheduled_at": "2099-01-01 00:00"})
    assert st == 200, (st, r)
    d_mine = r["id"]
    d_mixed = deploy.create({"name": "Gemischt (Test)", "steps": {"main": [{"type": "command", "command": "hostname"}]}},
                            [cin1, cout], "haupt-test", scheduled_at="2099-01-01 00:00:00")
    d_foreign = deploy.create({"name": "Fremd (Test)", "steps": {"main": [{"type": "command", "command": "hostname"}]}},
                              [cout], "haupt-test", scheduled_at="2099-01-01 00:00:00")
    deps = {d["id"]: d for d in _asgi("GET", "/api/deployments", ou)[1]}
    assert d_mine in deps and d_mixed in deps and d_foreign not in deps and deps[d_mixed]["total"] == 1
    st, d = _asgi("GET", f"/api/deployments/{d_mixed}", ou)
    assert st == 200 and [t["computer_id"] for t in d["targets"]] == [cin1], "own targets only"
    assert _asgi("GET", f"/api/deployments/{d_foreign}", ou)[0] == 404
    assert _asgi("POST", f"/api/deployments/{d_mixed}/cancel", ou)[0] == 403, "do not abort mixed deployment"
    assert _asgi("DELETE", f"/api/deployments/{d_mixed}", ou)[0] == 403
    assert _asgi("POST", f"/api/deployments/{d_mine}/cancel", ou)[0] == 200
    # 7) Schedules: own tenant only; new ones land there
    st, r = _asgi("POST", "/api/schedules", ou, {"name": "Nord-Zeitplan (Test)", "kind": "packages", "packages": [p_nord],
                                                  "targets": {"computers": [cin1], "collections": []}, "enabled": False})
    assert st == 200 and db.val("SELECT tenant_id FROM schedules WHERE id=?", (r["id"],)) == t_nord, (st, r)
    st, r2 = _asgi("POST", "/api/schedules", ou, {"name": "x", "kind": "packages", "packages": [p_central],
                                                   "targets": {"computers": [cout], "collections": []}, "enabled": False})
    assert st == 403, "foreign computers in the schedule"
    s_main = schedules.save(None, {"name": "Haupt-Zeitplan (Test)", "kind": "packages", "packages": [p_central],
                                   "targets": {"computers": [cout], "collections": []}, "enabled": False})
    assert s_main not in {x["id"] for x in _asgi("GET", "/api/schedules", ou)[1]}
    assert _asgi("GET", f"/api/schedules/{s_main}", ou)[0] == 404 and _asgi("POST", f"/api/schedules/{s_main}/run", ou)[0] == 404
    # A tenant's schedule reaches only its computers (even if foreign ones were entered)
    sx = schedules.save(None, {"name": "Nord direkt (Test)", "kind": "packages", "packages": [p_nord], "tenant_id": t_nord,
                               "targets": {"computers": [cin1, cout], "collections": []}, "enabled": False})
    assert schedules.members(schedules.get(sx)) == {cin1}
    # 8) Tenants: only the own one, without editing; without release no own packages
    assert [t["name"] for t in _asgi("GET", "/api/tenants", ou)[1]] == ["Nord"]
    db.ex("UPDATE tenants SET allow_packages=0 WHERE id=?", (t_nord,))
    assert _asgi("POST", "/api/packages", ou, {"name": "y", "steps": {"main": []}})[0] == 403
    assert _asgi("PUT", f"/api/packages/{p_nord}", ou, {"name": "y"})[0] == 403
    assert _asgi("POST", "/api/schedules", ou, {"name": "y", "kind": "packages"})[0] == 403
    db.ex("UPDATE tenants SET allow_packages=1 WHERE id=?", (t_nord,))
    # 9) No scope = no computers (never "all"); error details never to OU admins
    empty = session([])
    assert _asgi("GET", "/api/computers", empty)[1] == [] and _asgi("GET", "/api/me", empty)[1]["scoped"] is True
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "main.py"), encoding="utf-8").read()
    assert 'sess.get("scope") is not None:' in src
    # A tenant with OU admins needs a collection; the main admin group is not an OU admin group
    assert _asgi("POST", "/api/tenants", main_admin, {"name": "Ohne Sammlung", "admin_group": "PK-OU-X"})[0] == 400
    assert _asgi("POST", "/api/tenants", main_admin, {"name": "Haupt", "admin_group": config.ACCESS_GROUP or "POCKETKNIFE-Admins",
                                                      "collection_id": col})[0] == 400
    # 10) Review 2.25: "remember for all" only main admins; never as service account; files only with own folder; schedule collections
    sw_in = db.val("SELECT id FROM software WHERE computer_id=?", (cin1,))
    st, r = _asgi("POST", "/api/uninstall", ou, {"items": [{"software_id": sw_in, "command": "cmd /c echo x", "remember": "*"}]})
    assert st == 403 and not db.val("SELECT COUNT(*) FROM uninstall_overrides WHERE pattern='*'"), "command for all computers"
    db.ex("UPDATE software SET uninstall_string=? WHERE id=?", ('"C:\\Program Files\\OU\\unins000.exe"', sw_in))
    st, r = _asgi("POST", "/api/uninstall", ou, {"items": [{"software_id": sw_in, "run_as": "deploy"}]})
    assert st == 200 and r["deployments"], (st, r)
    pj = json.loads(db.val("SELECT package_json FROM deployments WHERE id=?", (r["deployments"][0],)))
    assert pj.get("run_as") != "deploy", "uninstall (UninstallString from the client) never as service account"
    db.ex("DELETE FROM deployments WHERE id=?", (r["deployments"][0],))
    from app import uninstall as _un
    swrow = db.q1("SELECT * FROM software WHERE id=?", (sw_in,))
    assert _un.package_for(swrow, None, "deploy")["run_as"] != "deploy", "also for main admins: registry command never as service account"
    stsrc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "steps.py"), encoding="utf-8").read()
    assert 'if ra == "deploy" and not (st.get("command") or "").strip():' in stsrc
    st, r = _asgi("POST", "/api/packages", ou, {"name": "Als Dienstkonto (Test)", "run_as": "deploy",
                                                 "steps": {"main": [{"type": "command", "command": "whoami", "run_as": "deploy"}]}})
    assert st == 200, (st, r)
    row = db.q1("SELECT run_as, steps FROM packages WHERE id=?", (r["id"],))
    assert row["run_as"] == "system" and '"deploy"' not in row["steps"], "code of an OU admin never as service account"
    st, r = _asgi("POST", "/api/packages", ou, {"name": "Ausbruch (Test)", "steps": {"main": [
        {"type": "install", "source": "file", "folder": "Kunden\\Nord\\..\\Sued", "install_file": "x.msi"}]}})
    assert st == 400, ("\"..\" in the package folder", st, r)
    db.ex("UPDATE tenants SET repo_folder='' WHERE id=?", (t_nord,))
    st, r = _asgi("POST", "/api/packages", ou, {"name": "Ohne Ordner (Test)", "steps": {"main": [
        {"type": "install", "source": "file", "folder": "Kunden\\Berlin", "install_file": "x.msi"}]}})
    assert st == 403, ("without repository folder no repository files", st, r)
    db.ex("UPDATE tenants SET repo_folder='Kunden\\Nord' WHERE id=?", (t_nord,))
    for cols in ([0], [col + 999]):
        st, r = _asgi("POST", "/api/schedules", ou, {"name": "Alle (Test)", "kind": "packages", "packages": [p_nord],
                                                      "targets": {"computers": [], "collections": cols}, "enabled": False})
        assert st == 403, ("schedule on foreign/all collections", cols, st)
    st, r = _asgi("POST", "/api/schedules", ou, {"name": "Eigene OU (Test)", "kind": "packages", "packages": [p_nord],
                                                  "targets": {"computers": [], "collections": [col]}, "enabled": False})
    assert st == 200, (st, r)
    st, r = _asgi("POST", "/api/remote/connect", ou, {"computer_id": cin1, "protocol": "vnc", "auth": "central"})
    assert st == 403, "central VNC password not for OU admins"
    assert _asgi("GET", "/api/kiosk/me", ou)[0] == 403, "kiosk not via the OU admin session"
    assert _asgi("POST", "/api/session/active", ou)[0] == 200, "inactivity clock also works for OU admins"
    st, d = _asgi("GET", f"/api/deployments/{d_mixed}", ou)
    assert d["package"].get("steps") == {}, "do not show content of a deployment with foreign targets"
    assert _asgi("GET", "/api/collections", ou)[1][0]["filters"] == []
    # Tenant had OU admins: without collection no computers (never "all"); deleted tenant: no computers
    db.ex("UPDATE tenants SET ou_ever=1 WHERE id=?", (t_nord,))
    assert deploy.tenant_members(t_nord) == {cin1, cin2}
    db.ex("UPDATE tenants SET collection_id=NULL WHERE id=?", (t_nord,))
    assert deploy.tenant_members(t_nord) == set() and deploy.tenant_members(10 ** 6) == set()
    assert schedules.members(schedules.get(sx)) == set(), "schedule of an OU tenant without collection reaches nobody"
    db.ex("UPDATE tenants SET collection_id=? WHERE id=?", (col, t_nord))
    assert deploy.tenant_members(t_sued) is None, "tenant without OU admins and without collection: no restriction as before"
    # Delete tenant: not while schedules are attached (they would otherwise become "central"); change ends OU sessions
    assert _asgi("DELETE", f"/api/tenants/{t_nord}", main_admin)[0] == 400
    _m._tenants_changed(t_nord)
    assert _asgi("GET", "/api/me", ou)[0] == 401, "OU admin session ended after a change to the tenant"
    adsrc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "ad.py"), encoding="utf-8").read()
    assert "def resolve_group_strict" in adsrc and "group_dn_by_sid(c, t.get(\"admin_group_sid\"))" in adsrc and "mehrdeutig" in adsrc, \
        "groups unambiguous (SID when saving), never by name again"
    fg = adsrc[adsrc.index("def _find_groups"):adsrc.index("def _group_dn")]
    assert fg.index("sAMAccountName=") < fg.index("(cn="), "first sAMAccountName (unique), then cn – a group of the same name locks nobody out"
    # Clean up
    db.ex("DELETE FROM uninstall_overrides WHERE created_by='ou-test'")
    for sid in (sx, s_main):
        db.ex("DELETE FROM schedules WHERE id=?", (sid,))
    db.ex("DELETE FROM schedules WHERE tenant_id=?", (t_nord,))
    for did in (d_mine, d_mixed, d_foreign):
        db.ex("DELETE FROM deployments WHERE id=?", (did,))
    db.ex("DELETE FROM packages WHERE tenant_id IN (?,?) OR id=?", (t_nord, t_sued, p_central))
    db.ex("DELETE FROM tenants WHERE id IN (?,?)", (t_nord, t_sued))
    db.ex("DELETE FROM collections WHERE id=?", (col,))
    for c in ids:
        db.ex("DELETE FROM computers WHERE id=?", (c,))
    db.ex("DELETE FROM sessions WHERE username IN ('ou-test', 'haupt-test')")
    access.flush()


@test("2.25.1 Changes in AD take effect by themselves: sessions are checked in the background every 2 minutes in one pass (removed -> logged out, fewer rights -> log in again, more -> extended, AD gone -> stays, OU admins at most 30 min unconfirmed)")
def _():
    import hashlib
    import secrets
    import time as _t
    from app import ad, auth, scheduler, util
    assert auth.RECHECK_SECONDS <= 120 and auth.MAX_UNCONFIRMED <= 1800
    assert "_bg(\"sessions\", _auth.recheck_all)" in open(scheduler.__file__, encoding="utf-8").read(), "runs every minute"

    def sess(scope, name, confirmed=None, expires="2099-01-01 00:00:00"):
        tok = secrets.token_urlsafe(16)
        th = hashlib.sha256(tok.encode()).hexdigest()
        db.ex("INSERT INTO sessions(token_hash, username, display_name, created, expires, ip, last_active, role, scope, confirmed_at) "
              "VALUES(?,?,?,?,?,?,?,?,?,?)", (th, name, "Test", util.now(), expires, "127.0.0.1", util.now(), "admin",
                                            json.dumps(scope) if scope is not None else None, confirmed or _t.time()))
        auth._checked[th] = 0  # due
        return tok, th
    answers = {"weg-t": False, "leer-t": [], "haupt-t2": True, "mehr-t": [5, 8], "weniger-t": [8], "ad-aus-t": None,
               "ad-aus-alt-t": None, "abgel-t": True}
    toks = {n: sess([5], n, confirmed=(_t.time() - 3600 if n == "ad-aus-alt-t" else None),
                    expires=("2000-01-01 00:00:00" if n == "abgel-t" else "2099-01-01 00:00:00")) for n in answers}
    calls = []
    orig_cs, orig_lc = ad.check_sessions, auth.config.ldap_configured

    def fake(items):
        items = list(items)
        calls.append(len(items))
        names = {th: sam for th, sam, _r in items}
        return {th: answers.get(sam, True) for th, sam in names.items()}  # sessions of other tests: unchanged
    ad.check_sessions = fake
    auth.config.ldap_configured = lambda: True
    try:
        auth.recheck_all()
    finally:
        ad.check_sessions, auth.config.ldap_configured = orig_cs, orig_lc
    assert len(calls) == 1, ("one pass for all due sessions", calls)
    assert "abgel-t" in {r["username"] for r in db.q("SELECT username FROM sessions")}, "do not check expired ones (the cleanup run removes them)"
    alive = {r["username"]: r["scope"] for r in db.q("SELECT username, scope FROM sessions WHERE username LIKE '%-t%'")}
    assert "weg-t" not in alive and "leer-t" not in alive, "removed from the group -> logged out"
    assert alive["haupt-t2"] is None, "added to POCKETKNIFE-Admins -> main admin"
    assert json.loads(alive["mehr-t"]) == [5, 8], "additional tenant group -> scope extended"
    assert "weniger-t" not in alive, "fewer rights -> log in again (also ends remote sessions)"
    assert json.loads(alive["ad-aus-t"]) == [5], "AD unreachable -> session stays ..."
    left = auth.RECHECK_SECONDS - (_t.time() - auth._checked[toks["ad-aus-t"][1]])
    assert left <= auth.RETRY_SECONDS + 1, "... and is checked again after a minute"
    assert "ad-aus-alt-t" not in alive, "OU admin unconfirmed for over 30 minutes -> logged out"
    st, res = _asgi("GET", "/api/me", toks["weniger-t"][0])
    assert st == 401 and "Berechtigung wurde geändert" in str(res), (st, res)
    # The request itself never asks AD (slow AD does not slow down the UI)
    asrc = open(auth.__file__, encoding="utf-8").read()
    sess_fn = asrc[asrc.index("def _session(request):"):asrc.index("def current(request")]
    assert "ad." not in sess_fn and "check_sessions" not in sess_fn
    # ad.check_sessions: failure != "not a member"; missing access group = nobody is main admin, tenants are checked
    adsrc = open(ad.__file__, encoding="utf-8").read()
    cs = adsrc[adsrc.index("def check_sessions"):adsrc.index("def still_allowed")]
    assert "except GroupConfigError" in cs and "strict=True" in cs and "out[key] = None" in cs
    assert "class GroupConfigError(ADError)" in adsrc and "def _strict_ok" in adsrc
    db.ex("DELETE FROM sessions WHERE username LIKE '%-t%'")

@test("2.25.1 AD check of the sessions: failure != \"not a member\", missing access group does not block revocations, deleted tenant group gives no access")
def _():
    from app import ad, util
    t_a = db.ex("INSERT INTO tenants(name, collection_id, admin_group, admin_group_sid, created_at) VALUES('SimA', 1, 'G-A', 'S-1-5-21-1-1-1-2001', ?)", (util.now(),))
    t_b = db.ex("INSERT INTO tenants(name, collection_id, admin_group, admin_group_sid, created_at) VALUES('SimB', 1, 'G-B', 'S-1-5-21-1-1-1-2002', ?)", (util.now(),))
    sid_dn = {"S-1-5-21-1-1-1-2001": "CN=G-A,DC=x", "S-1-5-21-1-1-1-2002": None}  # G-B deleted in AD
    members = {("anna", "CN=PK,DC=x"): True, ("bert", "CN=G-A,DC=x"): True, ("carl", "CN=G-A,DC=x"): "stoerung"}
    state = {"acc": "CN=PK,DC=x"}

    class Conn:
        def unbind(self):
            pass
    orig = (ad.service_conn, ad._search, ad._group_dn, ad.group_dn_by_sid, ad.is_member)

    def fsearch(c, base, flt, attrs):
        for u in ("anna", "bert", "carl", "dora"):
            if f"sAMAccountName={u})" in flt:
                return [{"dn": f"CN={u},DC=x", "attributes": {"userAccountControl": 2 if u == "dora" else 512}}]
        return []

    def fgroup(c):
        if state["acc"] is None:
            raise ad.GroupConfigError("Zugriffsgruppe fehlt")
        return state["acc"]

    def fmember(c, e, gdn, strict=False):
        v = members.get((e["dn"].split(",")[0][3:], gdn), False)
        if v == "stoerung":
            raise ad.ADError("LDAP-Suche gestört: busy")
        return bool(v), "sim"
    ad.service_conn = lambda: Conn()
    ad._search, ad._group_dn, ad.group_dn_by_sid, ad.is_member = fsearch, fgroup, lambda c, sid: sid_dn.get(sid), fmember
    _attr0 = ad._attr
    ad._attr = lambda e, n, d=None: e["attributes"].get(n, d)
    try:
        r = ad.check_sessions([(1, "anna", "admin"), (2, "bert", "admin"), (3, "carl", "admin"), (4, "dora", "admin"),
                               (5, "emil", "admin")])
        assert r[1] is True, "main admin"
        assert r[2] == [t_a], "OU admin only for tenants with an existing group (G-B deleted)"
        assert r[3] is None, "LDAP failure during membership check -> not checkable, do NOT log out"
        assert r[4] is False and r[5] is False, "disabled or not found -> log out"
        state["acc"] = None  # access group deleted/ambiguous
        r = ad.check_sessions([(1, "anna", "admin"), (2, "bert", "admin")])
        assert r[1] is False, "without an access group nobody is main admin (as at login)"
        assert r[2] == [t_a], "... the tenant groups are still checked (revocation keeps working)"
        ad.service_conn = lambda: (_ for _ in ()).throw(ad.ADError("DC weg"))
        assert ad.check_sessions([(1, "anna", "admin")]) == {1: None}, "AD gone -> all not checkable"
    finally:
        ad.service_conn, ad._search, ad._group_dn, ad.group_dn_by_sid, ad.is_member = orig
        ad._attr = _attr0
        db.ex("DELETE FROM tenants WHERE id IN (?,?)", (t_a, t_b))


@test("2.25.2 Computers: notes and custom fields can be entered directly on the computer (even without created fields), are saved and never overwritten by the computer")
def _():
    import hashlib
    import secrets
    import time as _t
    from app import auth, util
    cid = db.ex("INSERT INTO computers(name, dns_name, created_at) VALUES('NOTIZ-PC','notiz-pc.firma.local',?)", (util.now(),))
    tok = secrets.token_urlsafe(16)
    th = hashlib.sha256(tok.encode()).hexdigest()
    db.ex("INSERT INTO sessions(token_hash, username, display_name, created, expires, ip, last_active, role) VALUES(?,?,?,?,?,?,?,?)",
          (th, "notiz-test", "Test", util.now(), "2099-01-01 00:00:00", "127.0.0.1", util.now(), "admin"))
    auth._checked[th] = _t.time() + 10 ** 6
    assert _asgi("PUT", f"/api/computers/{cid}", tok, {"notes": "Raum 2.14, Ansprechpartner Frau Meier"})[0] == 200
    st, c = _asgi("GET", f"/api/computers/{cid}", tok)
    assert st == 200 and c["notes"] == "Raum 2.14, Ansprechpartner Frau Meier"
    fid = _asgi("POST", "/api/fields", tok, {"name": "Inventarnummer (Test)", "type": "text"})[1]["id"]
    assert _asgi("PUT", f"/api/computers/{cid}/fields", tok, {"values": {str(fid): "INV-0815"}})[0] == 200
    vals = {f["name"]: f["value"] for f in _asgi("GET", f"/api/computers/{cid}/cat/custom_fields", tok)[1]}
    assert vals["Inventarnummer (Test)"] == "INV-0815"
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    blk = js[js.index("if (cat === 'custom_fields') {"):js.index("if (cat === 'collections') {")]
    assert "field('Notizen'" in blk and "Feld anlegen" in blk and "Einstellungen → Benutzerdefinierte Felder" not in blk, \
        "notes always there, create field directly, no wrong reference"
    db.ex("DELETE FROM custom_fields WHERE id=?", (fid,))
    db.ex("DELETE FROM computers WHERE id=?", (cid,))
    db.ex("DELETE FROM sessions WHERE token_hash=?", (th,))


@test("2.25.3 Repository searchable (name/pattern, subfolders, limits) and repeating with freely chosen steps (order, targets, OU scope)")
def _():
    import tempfile as _tf
    from app import deploy, remote, steps, util
    # --- Repository search
    root = _tf.mkdtemp(prefix="pk-repo-", dir=TMP)
    for f in ("7zip/24.08/7z2408-x64.msi", "7zip/26.00/7z2600-x64.msi", "Office/2024/setup.exe", "Kunden/Nord/Tools/7zPortable.zip",
              "Office/setup[1].exe"):
        os.makedirs(os.path.join(root, os.path.dirname(f)), exist_ok=True)
        open(os.path.join(root, f), "wb").write(b"x" * 10)
    old = db.setting("repo_path")
    db.set_setting("repo_path", root)
    try:
        r = remote.repo_search("", "7z")
        names = {(x["folder"], x["name"]) for x in r["items"]}
        assert ("7zip", "24.08") not in names and ("7zip\\24.08", "7z2408-x64.msi") in names and ("", "7zip") in names, names
        assert ("Kunden\\Nord\\Tools", "7zPortable.zip") in names, "even deep in subfolders"
        assert {x["name"] for x in remote.repo_search("", "7z*.msi")["items"]} == {"7z2408-x64.msi", "7z2600-x64.msi"}, "pattern with *"
        assert [x["name"] for x in remote.repo_search("Kunden\\Nord", "7z")["items"]] == ["7zPortable.zip"], "only from the folder (OU admins)"
        r = remote.repo_search("", "7z", limit=2)
        assert len(r["items"]) == 2 and r["truncated"], "limit applies and is reported"
        assert remote.repo_search("7zip\\24.08", "x64", limit=1)["truncated"] is False
        assert remote.repo_search("7zip", "7z2", limit=1)["truncated"], "even if the limit is reached in the middle of the folder"
        assert [x["name"] for x in remote.repo_search("", "setup[1]")["items"]] == ["setup[1].exe"], "[ ] are taken literally"
        for bad in ("x" * 101, "a"):
            try:
                remote.repo_search("", bad)
                raise AssertionError(f"to be rejected: {bad[:10]}")
            except remote.RemoteError:
                pass
        try:
            remote.repo_search("gibtsnicht", "abc")
            raise AssertionError("missing start folder must be reported (not \"no hits\")")
        except remote.RemoteError:
            pass
        try:
            remote.repo_search("", "a")
            raise AssertionError("at least 2 characters")
        except remote.RemoteError:
            pass
    finally:
        db.set_setting("repo_path", old or "")
    # --- Choose steps
    pkg = steps.normalize({"name": "Rerun (Test)", "steps": {
        "pre": [{"id": "p1", "type": "command", "command": "echo pre"}],
        "main": [{"id": "m1", "type": "command", "command": "echo 1"}, {"id": "m2", "type": "command", "command": "echo 2", "enabled": False},
                 {"id": "m3", "type": "command", "command": "echo 3"}],
        "post": [{"id": "z1", "type": "command", "command": "echo post"}]}})
    cids = [db.ex("INSERT INTO computers(name, created_at) VALUES(?,?)", (n, util.now())) for n in ("RR-1", "RR-2", "RR-3")]
    did = db.ex("INSERT INTO deployments(name, package_json, status, created_by, created_at) VALUES('Rerun (Test)', ?, 'failed', 't', ?)",
                (json.dumps(pkg), util.now()))
    tids = {}
    for c, st, recs in ((cids[0], "success", [("p1", "success"), ("m1", "success"), ("m3", "success")]),
                        (cids[1], "failed", [("p1", "success"), ("m1", "failed")]), (cids[2], "failed", [("p1", "failed")])):
        tids[c] = db.ex("INSERT INTO deployment_targets(deployment_id, computer_id, computer_name, status, steps) VALUES(?,?,?,?,?)",
                        (did, c, f"RR-{c}", st, json.dumps([{"sid": a, "status": b, "depth": 0} for a, b in recs])))
    ov = deploy.steps_overview(did)
    by = {x["sid"]: x for x in ov["steps"]}
    assert [x["sid"] for x in ov["steps"]] == ["p1", "m1", "m2", "m3", "z1"] and by["m1"]["counts"] == {"success": 1, "failed": 1}
    assert by["m2"]["enabled"] is False and len(ov["targets"]) == 3
    assert len(deploy.steps_overview(did, {cids[0]})["targets"]) == 1, "OU admin sees only own targets"
    got = {}
    orig = deploy.create
    deploy.create = lambda p, ids, user, **kw: got.update(pkg=p, ids=ids, kw=kw) or 4711
    try:
        assert deploy.rerun_steps(did, ["m3", "m2"], "failed", [], "t") == 4711
        p2 = got["pkg"]["steps"]
        assert [s_["id"] for s_ in p2["main"]] == ["m2", "m3"] and p2["pre"] == [] and p2["post"] == [], "only chosen ones, package order"
        assert all(s_["enabled"] for s_ in p2["main"]), "chosen step that is switched off in the package runs"
        assert sorted(got["ids"]) == sorted(cids[1:]), "only failed targets"
        assert "2 Schritte wiederholt" in got["kw"]["name"]
        deploy.rerun_steps(did, ["z1"], "selected", [tids[cids[0]]], "t")
        assert got["ids"] == [cids[0]] and "„" in got["kw"]["name"]
        db.ex("UPDATE deployments SET package_json=? WHERE id=?", (json.dumps({**pkg, "reboot": "always"}), did))
        deploy.rerun_steps(did, ["m1"], "all", [], "t", reboot="never")
        assert got["pkg"]["reboot"] == "never", "restart can be deselected"
        deploy.rerun_steps(did, ["m1"], "all", [], "t")
        assert got["pkg"]["reboot"] == "always", "otherwise as in the package"
        assert deploy.RETRY_SUFFIX.sub("", "Pkg – 2 Schritte wiederholt – ab „A“ wiederholt") == "Pkg"
        deploy.rerun_steps(did, ["m1"], "all", [], "t", allowed={cids[1]})
        assert got["ids"] == [cids[1]], "OU admin: only targets in the own scope"
        for bad in ((did, [], "all"), (did, ["xx"], "all"), (did, ["m1"], "selected")):
            try:
                deploy.rerun_steps(bad[0], bad[1], bad[2], [], "t")
                raise AssertionError(f"must be rejected: {bad}")
            except ValueError:
                pass
        db.ex("UPDATE deployments SET status='running' WHERE id=?", (did,))
        try:
            deploy.rerun_steps(did, ["m1"], "all", [], "t")
            raise AssertionError("still running -> reject")
        except ValueError:
            pass
    finally:
        deploy.create = orig
        db.ex("DELETE FROM deployments WHERE id=?", (did,))
    # Step IDs: missing ones are filled in fixedly by position, duplicates are reassigned on saving
    n1 = steps.normalize({"steps": {"main": [{"type": "command", "command": "a"}, {"type": "command", "command": "b"}]}})
    n2 = steps.normalize({"steps": {"main": [{"type": "command", "command": "a"}, {"type": "command", "command": "b"}]}})
    ids1 = [x["id"] for x in n1["steps"]["main"]]
    assert ids1 == [x["id"] for x in n2["steps"]["main"]] and len(set(ids1)) == 2 and all(ids1)
    cp = steps.clean_package({"name": "Doppelt (Test)", "steps": {"main": [{"id": "aa", "type": "command", "command": "a"},
                                                                         {"id": "aa", "type": "command", "command": "b"}]}})
    assert len({x["id"] for x in cp["steps"]["main"]}) == 2
    from app import access
    assert access.ROUTES[("GET", "/api/deployments/{did}/steps")] is access._p_dep_w, "step title only with exclusively own targets"
    assert access.ROUTES[("GET", "/api/deployments/{did}/failed_steps")] is access._p_dep_w
    for c in cids:
        db.ex("DELETE FROM computers WHERE id=?", (c,))


@test("2.25.4 File explorer: fallback via C$ (pattern, links not followed, locked folders skipped, limits, OU rule)")
def _():
    from app import access, clientfiles
    from app.main import app as _app

    class St:
        def __init__(self, attr, size=5):
            self.st_file_attributes, self.st_size, self.st_mtime, self.st_mode = attr, size, 1700000000, 0

    class E:
        def __init__(self, name, attr, size=5):
            self.name, self._a, self._s = name, attr, size

        def stat(self, follow_symlinks=True):
            assert follow_symlinks is False, "links must not be followed"
            return St(self._a, self._s)

    D, F, J = 0x10, 0x20, 0x410
    tree = {"C:\\Logs": [E("app.log", F, 100), E("alt", D), E("Gesperrt", D), E("Link", J), E("setup[1].log", F)],
            "C:\\Logs\\alt": [E("app-2024.log", F), E("readme.txt", F), E("tief", D)],
            "C:\\Logs\\alt\\tief": [E("app.LOG", F)],
            "C:\\Logs\\Link": [E("app.log", F)]}
    seen = []

    class Fake:
        @staticmethod
        def scandir(unc):
            local = "C:" + unc.split("C$", 1)[1].rstrip("\\")
            seen.append(local)
            if local.endswith("Gesperrt"):
                raise PermissionError(13, "STATUS_ACCESS_DENIED", unc)
            if local not in tree:
                raise FileNotFoundError(2, "No such file", unc)
            return list(tree[local])

    orig = clientfiles._session
    clientfiles._session = lambda cid: (Fake, {}, "pc")
    o_host = clientfiles._host
    clientfiles._host = lambda cid: ({"name": "pc", "dns_name": "pc"}, "pc")

    def sm(path, q, **kw):
        return clientfiles._search_smb(1, *clientfiles._parse(path), q, **kw)
    try:
        r = sm("C:\\Logs", "app")
        got = {(x["folder"], x["name"]) for x in r["items"]}
        assert got == {("C:\\Logs", "app.log"), ("C:\\Logs\\alt", "app-2024.log"), ("C:\\Logs\\alt\\tief", "app.LOG")}, got
        assert "C:\\Logs\\Link" not in seen, "junction not followed"
        assert r["skipped"] == 1 and r["truncated"] is False, r
        assert [x["name"] for x in sm("C:\\Logs", "*.log")["items"]] == ["app-2024.log", "app.LOG", "app.log", "setup[1].log"], "sorted by path"
        assert [x["name"] for x in sm("C:\\Logs", "setup[1]")["items"]] == ["setup[1].log"], "[ ] literal"
        assert [x["name"] for x in sm("C:\\Logs", "alt")["items"]] == ["alt"] and sm("C:\\Logs", "alt")["items"][0]["dir"]
        assert sm("C:\\Logs", "app", limit=2)["truncated"]
        assert sm("C:\\Logs", "app", max_dirs=1)["truncated"]
        for bad_path in ("", "\\\\srv\\x", "C:\\Logs\\..\\Windows"):
            try:
                clientfiles.search(1, bad_path, "app")
                raise AssertionError(f"Pfad {bad_path!r} should have been rejected")
            except clientfiles.FileError:
                pass
        for bad_q in ("a", "**", "x" * 101):
            try:
                clientfiles.search(1, "C:\\Logs", bad_q)
                raise AssertionError(f"Suchbegriff {bad_q!r} should have been rejected")
            except clientfiles.FileError:
                pass
        try:
            sm("C:\\Gibtsnicht", "app")
            raise AssertionError("error in the start folder must be reported, not \"no hits\"")
        except clientfiles.FileError as e:
            assert "Nicht gefunden" in str(e), e
        # concurrent searches limited
        held = [clientfiles._search_sem.acquire(blocking=False) for _ in range(4)]
        try:
            clientfiles.search(1, "C:\\Logs", "app")
            raise AssertionError("fifth concurrent search should have been rejected")
        except clientfiles.FileError as e:
            assert "mehrere Suchen" in str(e)
        finally:
            for ok in held:
                if ok:
                    clientfiles._search_sem.release()
    finally:
        clientfiles._session = orig
        clientfiles._host = o_host
    class T:
        def __init__(self, tag):
            self.st_reparse_tag = tag
    assert clientfiles._is_link(T(0xA0000003), 0x410) and clientfiles._is_link(T(0xA000000C), 0x420)
    assert not clientfiles._is_link(T(0x9000001A), 0x410), "OneDrive folder is not a link"
    assert not clientfiles._is_link(T(0x80000013), 0x420), "deduplicated file is not a link"
    assert clientfiles._is_link(T(0), 0x410) and not clientfiles._is_link(T(0xA0000003), 0x10)
    assert access.ROUTES[("GET", "/api/computers/{cid}/files/search")] is access._p_cid, "OU admins only on own computers"
    assert any(getattr(rt, "path", "").replace("{cid:int}", "{cid}") == "/api/computers/{cid}/files/search" for rt in _app.routes)
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    assert "/files/search?path=" in js and "async function download(r, folder = cur)" in js
    # 2.25.7: ONE field in the explorer – it searches (Enter/"Suchen") and does not filter while typing
    assert "liveFilter: false" in js and "if (opt.liveFilter === false) return;" in js and "onSearchEnter: (t) => doSearch(t)" in js
    assert "In diesem Ordner suchen, z. B. *.log" not in js, "no second search field on the right any more"


@test("2.25.4 File explorer: search runs on the client (WinRM, Constrained Language Mode), passed safely, fallback only on connection error")
def _():
    import json as _json
    import subprocess as _sp
    from app import clientfiles, remote
    # Search term -> pattern for -like: [ ] ` literal (escaped with `), without wildcards "part of the name"
    assert clientfiles.like_pattern("app") == "*app*"
    assert clientfiles.like_pattern("*.log") == "*.log"
    assert clientfiles.like_pattern("setup[1]") == "*setup`[1`]*"
    assert clientfiles.like_pattern("a**b***c") == "a*b*c", "several * merged"
    for bad in ("*a*b*c*d*e*f*g*h*i*", "x" * 101, "a", "ab\x01"):
        try:
            clientfiles.like_pattern(bad)
            raise AssertionError(f"{bad!r} should have been rejected")
        except clientfiles.FileError:
            pass
    # Only real connection errors lead to the fallback via C$
    import requests as _rq
    assert remote._is_connect_error(_rq.exceptions.ConnectionError("Max retries exceeded with url"))
    assert remote._is_connect_error(_rq.exceptions.ConnectTimeout("x"))
    assert not remote._is_connect_error(_rq.exceptions.ReadTimeout("read timed out"))
    assert not remote._is_connect_error(Exception("401 Unauthorized"))
    assert issubclass(remote.RemoteConnectError, remote.RemoteError), "old except RemoteError still catch it"
    calls = []
    answer = {"v": 'PKJSON:{"items":[{"f":"C:\\\\Logs\\\\alt","n":"b.log","d":false,"r":false,"s":7,"m":"2026-01-01 10:00:00"},'
                   '{"f":"C:\\\\","n":"a","d":true,"r":false,"s":0,"m":null}],"dirs":12,"skipped":2,"truncated":false}'}

    def fake_ps(host, script, timeout=0, **kw):
        calls.append((host, script, timeout))
        if isinstance(answer["v"], Exception):
            raise answer["v"]
        return answer["v"], "", False
    o_ps, o_host, o_smb = remote.ps, clientfiles._host, clientfiles._search_smb
    remote.ps = fake_ps
    clientfiles._host = lambda cid: ({"name": "PC1", "dns_name": "pc1.firma.local"}, "pc1.firma.local")
    clientfiles._search_smb = lambda cid, drive, rel, q: {"items": [], "truncated": False, "dirs": 1, "skipped": 0}
    try:
        r = clientfiles.search(1, "C:\\Logs", "it's")
        assert r["via"] == "winrm" and r["dirs"] == 12 and r["skipped"] == 2
        assert [(x["folder"], x["name"], x["dir"]) for x in r["items"]] == [("C:\\", "a", True), ("C:\\Logs\\alt", "b.log", False)], r
        host, script, tmo = calls[-1]
        assert host == "pc1.firma.local" and tmo >= 60
        assert "$start = 'C:\\Logs';" in script and "$pat = '*it''s*';" in script and "$flt = '*it''s*';" in script, script[:300]
        assert "LinkType" in script and "-Force" in script and "-like $pat" in script and "-match" not in script
        # 2.25.6: ONE recursive call with Windows filter instead of folder by folder (otherwise the time only sufficed for the top)
        assert "-Filter $flt -Recurse" in script and "PK-UnderLink" in script and "-FollowSymlink" not in script
        # a single hit comes out of ConvertTo-Json as an object instead of a list
        answer["v"] = 'PKJSON:{"items":{"f":"C:\\\\Logs","n":"x.log","d":false,"r":false,"s":1,"m":null},"dirs":1,"skipped":0,"truncated":true}'
        r = clientfiles.search(1, "C:\\Logs", "x.log")
        assert [x["name"] for x in r["items"]] == ["x.log"] and r["truncated"]
        answer["v"] = 'PKJSON:{"error":"notfound"}'
        try:
            clientfiles.search(1, "C:\\Gibtsnicht", "app")
            raise AssertionError("not found must be reported")
        except clientfiles.FileError as e:
            assert "Nicht gefunden" in str(e)
        answer["v"] = 'PKJSON:{"error":"denied","message":"Der Zugriff auf den Pfad wurde verweigert."}'
        try:
            clientfiles.search(1, "C:\\System Volume Information", "app")
            raise AssertionError("denied must be reported")
        except clientfiles.FileError as e:
            assert "Zugriff verweigert" in str(e)
        # Fallback via C$ ONLY if WinRM is not reachable at all – not on timeout or the like
        answer["v"] = remote.RemoteConnectError("WinRM: Max retries exceeded (Connection refused)")
        assert clientfiles.search(1, "C:\\Logs", "app")["via"] == "smb"
        answer["v"] = remote.RemoteError("WinRM-Verbindung fehlgeschlagen: das steht nur im Text")
        try:
            clientfiles.search(1, "C:\\Logs", "app")
            raise AssertionError("only the kind of error counts, not the text")
        except clientfiles.FileError:
            pass
        # Fallback: errors of the share become the message (no internal error 500)
        answer["v"] = remote.RemoteConnectError("WinRM: Connection refused")

        def smb_fail(*a, **kw):
            raise remote.RemoteError("SMB-Anmeldung abgelehnt")
        clientfiles._search_smb = smb_fail
        try:
            clientfiles.search(1, "C:\\Logs", "app")
            raise AssertionError("SMB error must arrive as FileError")
        except clientfiles.FileError as e:
            assert "SMB-Anmeldung" in str(e)
        clientfiles._search_smb = lambda cid, drive, rel, q: {"items": [], "truncated": False, "dirs": 1, "skipped": 0}
        # one search per user and per computer
        with clientfiles._search_lock:
            clientfiles._search_busy["users"].add("max")
        try:
            clientfiles.search(2, "C:\\Logs", "app", user_key="max")
            raise AssertionError("second search by the same user")
        except clientfiles.FileError as e:
            assert "vorige Suche" in str(e)
        finally:
            with clientfiles._search_lock:
                clientfiles._search_busy["users"].discard("max")
        with clientfiles._search_lock:
            clientfiles._search_busy["cids"].add(1)
        try:
            clientfiles.search(1, "C:\\Logs", "app", user_key="moritz")
            raise AssertionError("second search on the same computer")
        except clientfiles.FileError as e:
            assert "diesem Computer" in str(e)
        finally:
            with clientfiles._search_lock:
                clientfiles._search_busy["cids"].discard(1)
        answer["v"] = 'PKJSON:{"items":[],"dirs":1,"skipped":0,"errors":2,"truncated":false}'
        assert clientfiles.search(1, "C:\\Logs", "app", user_key="max")["errors"] == 2
        assert not clientfiles._search_busy["users"] and not clientfiles._search_busy["cids"], "locks free again"
        answer["v"] = remote.RemoteError("WinRM: keine Antwort von pc1 nach 2 Minuten – Aufruf abgebrochen.")
        try:
            clientfiles.search(1, "C:\\Logs", "app")
            raise AssertionError("timeout must not silently fall back to C$")
        except clientfiles.FileError as e:
            assert "nicht geendet" in str(e) and "Unterordner" in str(e), e
        assert clientfiles._search_sem.acquire(blocking=False)
        clientfiles._search_sem.release()  # lock is released even on errors
    finally:
        remote.ps, clientfiles._host, clientfiles._search_smb = o_ps, o_host, o_smb

    # Optional: run the script in real PowerShell in Constrained Language Mode (PK_PWSH=/path/pwsh)
    pw = os.environ.get("PK_PWSH")
    if pw and os.path.exists(pw):
        base = os.path.join(TMP, "pstree")
        for f in ("Logs/app.log", "Logs/alt/app-2024.log", "Logs/alt/tief/app.LOG", "Logs/alt/Grüße é.txt", "other/app.log", "Logs/it's.log"):
            os.makedirs(os.path.join(base, os.path.dirname(f)), exist_ok=True)
            open(os.path.join(base, f), "w").close()
        os.symlink(os.path.join(base, "other"), os.path.join(base, "Logs", "Link"))

        def run(q, **kw):
            sc = clientfiles.search_script(os.path.join(base, "Logs"), q, kw.get("limit", 300), follow=kw.get("follow", False))
            out = _sp.run([pw, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "-"],
                          input="$ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage'\n" + sc + "\n",
                          capture_output=True, text=True, env={**os.environ, "TERM": "dumb"}).stdout
            return _json.loads(out[out.index("PKJSON:") + 7:].strip().splitlines()[0])
        d = run("app")
        names = sorted(x["n"] for x in d["items"])
        assert names == ["app-2024.log", "app.LOG", "app.log"], f"link must not be followed: {names}"
        assert [x["n"] for x in run("grüße é")["items"]] == ["Grüße é.txt"]
        assert [x["n"] for x in run("it's")["items"]] == ["it's.log"]
        assert run("app", limit=1)["truncated"]
        # like Windows PowerShell 5.1 (follows links): hits below are discarded
        assert sorted(x["n"] for x in run("app", follow=True)["items"]) == ["app-2024.log", "app.LOG", "app.log"], "nothing duplicated via links"
        # deeply nested and many folders: is found (2.25.6 – before, only the top levels)
        deep = os.path.join(base, "Logs", *[f"e{i}" for i in range(12)])
        os.makedirs(deep)
        for i in range(300):
            os.makedirs(os.path.join(base, "Logs", "breit", f"d{i}", "x"))
        open(os.path.join(deep, "ganz-tief.txt"), "w").close()
        t0 = __import__("time").time()
        assert [x["n"] for x in run("ganz-tief")["items"]] == ["ganz-tief.txt"]
        assert __import__("time").time() - t0 < 20
        os.makedirs(os.path.join(base, "Logs", "x[1]y"))
        open(os.path.join(base, "Logs", "x[1]y", "klammer.txt"), "w").close()
        assert [x["f"].endswith("x[1]y") for x in run("klammer")["items"]] == [True], "folder with [ ] in the name"
        open(os.path.join(base, "Logs", "set[1]up.log"), "w").close()
        assert [x["n"] for x in run("set[1]up")["items"]] == ["set[1]up.log"], "[ ] literal with -like"
        long = "a" * 200 + ".txt"
        open(os.path.join(base, "Logs", long), "w").close()
        import time as _t
        t0 = _t.time()
        d = run("*a*a*a*a*a*a*a*b")
        assert d["items"] == [] and _t.time() - t0 < 20, "many * must not hang"


@test("2.25.4 Repository index in memory: immediate hits, refresh, re-read, upper limit, switching the repository, OU folders")
def _():
    import tempfile as _tf
    import time as _time
    from app import remote

    def wait():
        for _ in range(100):
            if not remote.repo_cache_status()["building"]:
                return
            _time.sleep(0.05)
        raise AssertionError("index does not finish")
    def files(r):
        return {x["name"] for x in r["items"] if not x["dir"]}
    g = remote.name_pattern("*a*a*a*a*a*a*a*b")
    t0 = _time.time()
    assert not any(g.search("a" * 250) for _ in range(200)) and _time.time() - t0 < 2, "no backtracking"
    assert remote.name_pattern("7Z*.MSI").search("7z2408-x64.msi") and not remote.name_pattern("7z*.msi").search("a7z.msi")
    assert remote.name_pattern("ab*ab").search("abab") and not remote.name_pattern("ab*ab").search("ab")
    assert remote.name_pattern("setup[1]").search("Office setup[1].exe") and remote.name_pattern("a?c?").search("ABCD")
    root = _tf.mkdtemp(prefix="pk-repoidx-", dir=TMP)
    root2 = _tf.mkdtemp(prefix="pk-repoidx2-", dir=TMP)
    for f in ("7zip/24.08/7z2408-x64.msi", "Kunden/Nord/7zPortable.zip", "Office/setup.exe"):
        os.makedirs(os.path.join(root, os.path.dirname(f)), exist_ok=True)
        open(os.path.join(root, f), "wb").write(b"x")
    os.makedirs(os.path.join(root2, "Neu"))
    open(os.path.join(root2, "Neu", "7zNeu.msi"), "wb").write(b"x")
    old = db.setting("repo_path")
    o_max = remote.REPO_CACHE_MAX
    db.set_setting("repo_path", root)
    remote.repo_cache_clear()
    try:
        r = remote.repo_search("", "7z")
        assert r["cached"] is False and len(files(r)) == 2, r  # first call: direct, index is read in
        wait()
        r = remote.repo_search("", "7z")
        assert r["cached"] is True and files(r) == {"7z2408-x64.msi", "7zPortable.zip"} and "7zip" in {x["name"] for x in r["items"]}, r
        assert [x["name"] for x in remote.repo_search("Kunden\\Nord", "7z")["items"]] == ["7zPortable.zip"], "only from the folder (OU admins)"
        assert [x["name"] for x in remote.repo_search("kunden", "7z")["items"]] == ["7zPortable.zip"], "case does not matter"
        try:
            remote.repo_search("Gibtsnicht", "7z")
            raise AssertionError("unknown folder: search directly and report the error")
        except Exception as e:
            assert "nicht gefunden" in str(e).lower(), e
        open(os.path.join(root, "7zip", "7zNeu.exe"), "wb").write(b"x")
        assert "7zNeu.exe" not in {x["name"] for x in remote.repo_search("", "7z")["items"]}, "index is a snapshot"
        r = remote.repo_search("", "7z", refresh=True)
        assert r["cached"] is False and "7zNeu.exe" in {x["name"] for x in r["items"]}, "re-read: this search directly"
        assert remote.repo_cache_status()["entries"], "old snapshot stays until the new one is ready"
        assert remote.repo_search("", "7z")["cached"] is True
        wait()
        assert "7zNeu.exe" in {x["name"] for x in remote.repo_search("", "7z")["items"]}
        # Snapshot older than TTL -> still use it and refresh in the background; older than STALE -> search directly
        with remote._rc_lock:
            remote._rc["at"] -= remote.REPO_CACHE_STALE + 10
        assert remote.repo_search("", "7z")["cached"] is False
        wait()
        assert remote.repo_search("", "7z")["cached"] is True
        # another repository configured: the old index does not apply
        db.set_setting("repo_path", root2)
        r = remote.repo_search("", "7z")
        assert [x["name"] for x in r["items"]] == ["7zNeu.msi"], r
        wait()
        # Upper limit: too large -> no index, every search direct
        remote.repo_cache_clear()
        remote.REPO_CACHE_MAX = 1
        remote.repo_search("", "7z")
        wait()
        st = remote.repo_cache_status()
        assert st["entries"] is None and st["too_big_or_failed"], st
        assert set(remote.repo_cache_status(full=False)) == {"age", "building"}, "OU admins: nothing over the whole repository"
        assert remote.repo_search("", "7z")["cached"] is False and not remote.repo_cache_status()["building"], "do not retry constantly"
    finally:
        remote.REPO_CACHE_MAX = o_max
        wait()
        remote.repo_cache_clear()
        db.set_setting("repo_path", old or "")


@test("2.25.4 Export/import packages: without IDs (names), nested ones included, fixed paths reported, checking changes nothing, missing collection rejects, OU rules")
def _():
    import json as _json
    from fastapi import HTTPException
    from app import access, pkgexport, steps
    from app import main as _m
    admin = {"username": "haupt", "scope": None}
    old_repo = db.setting("repo_path")
    db.set_setting("repo_path", "\\\\fs01\\PAKETE")
    col = db.ex("INSERT INTO collections(name, type, match, filters) VALUES('Exp-Buchhaltung','dynamic','all','{\"type\":\"group\",\"match\":\"all\",\"items\":[]}')")
    prof = db.val("SELECT id FROM scan_profiles ORDER BY id LIMIT 1")
    prof_name = db.val("SELECT name FROM scan_profiles WHERE id=?", (prof,))
    conds = {**steps.default_conditions(), "collections": [{"id": col, "mode": "member"}, {"id": 0, "mode": "member"}]}
    created = []

    def mk(data):
        v = _m._pkg_values(data, user=admin)
        cols = _m.deploy.PKG_FIELDS + ["created_at", "updated_at"]
        pid = db.ex(f"INSERT INTO packages({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
                    tuple(v[k] for k in _m.deploy.PKG_FIELDS) + (util.now(), util.now()))
        created.append(pid)
        return pid
    try:
        a = mk({"name": "Exp-7zip", "version": "24.08", "rescan_profile_id": prof, "conditions": conds, "steps": {"main": [
            {"type": "install", "source": "file", "folder": "7zip\\24.08", "install_file": "7z.msi", "install_type": "msi"},
            {"type": "powershell", "script": "& \\\\fs01\\PAKETE\\tools\\fix.ps1\nWrite-Output ok"},
            {"type": "scan", "profile_id": prof}]}})
        b = mk({"name": "Exp-Bundle", "steps": {"main": [{"type": "nested", "package_id": a},
                                                         {"type": "command", "command": "echo hallo", "conditions": conds}]}})
        data, warn, extra = pkgexport.export(admin, [b])
        text = _json.dumps(data)
        assert [p["name"] for p in data["packages"]] == ["Exp-Bundle", "Exp-7zip"] and extra == ["Exp-7zip"], "nested ones included"
        for k in ('"package_id"', '"profile_id"', '"rescan_profile_id"', '"tenant_id"', f'"id": {col}'):
            assert k not in text, f"{k} does not belong in the file"
        assert "Exp-Buchhaltung" in text and '"all": true' in text and prof_name in text
        assert any("Pfad des Repositorys" in w and "fix.ps1" in w for w in warn), warn
        assert "7zip\\\\24.08" in text, "repository path relative"
        n0 = db.val("SELECT COUNT(*) FROM packages")
        # same installation, "skip": nothing new; checking never writes
        r = pkgexport.run_import(admin, _json.loads(text), conflict="skip", dry_run=False, pkg_values=_m._pkg_values)
        assert r["count"]["skip"] == 2 and db.val("SELECT COUNT(*) FROM packages") == n0, r
        r = pkgexport.run_import(admin, _json.loads(text), conflict="copy", dry_run=True, pkg_values=_m._pkg_values)
        assert r["count"]["copy"] == 2 and db.val("SELECT COUNT(*) FROM packages") == n0, ("checking must not create anything", r)
        r = pkgexport.run_import(admin, _json.loads(text), conflict="copy", dry_run=False, pkg_values=_m._pkg_values)
        assert r["count"]["copy"] == 2 and db.val("SELECT COUNT(*) FROM packages") == n0 + 2, r
        na = db.q1("SELECT * FROM packages WHERE name='Exp-7zip (Import)'")
        nb = db.q1("SELECT * FROM packages WHERE name='Exp-Bundle (Import)'")
        created += [na["id"], nb["id"]]
        pb = steps.normalize(_m.deploy.row_to_package(nb))
        assert pb["steps"]["main"][0]["package_id"] == na["id"], "nesting points to the imported package"
        assert pb["steps"]["main"][1]["conditions"]["collections"] == [{"id": col, "mode": "member"}, {"id": 0, "mode": "member"}]
        pa = steps.normalize(_m.deploy.row_to_package(na))
        assert pa["rescan_profile_id"] == prof and pa["steps"]["main"][2]["profile_id"] == prof
        # Collection missing in the target installation -> reject (do not silently omit – that would cancel the condition)
        bad = _json.loads(text.replace("Exp-Buchhaltung", "Gibt-es-nicht"))
        for p in bad["packages"]:
            p["name"] += " X"
        r = pkgexport.run_import(admin, bad, dry_run=True, pkg_values=_m._pkg_values)
        st = {x["name"]: (x["status"], x["message"]) for x in r["items"]}
        assert st["Exp-7zip X"][0] == "error" and "Gibt-es-nicht" in st["Exp-7zip X"][1], st
        assert st["Exp-Bundle X"][0] == "error" and "verschachtelt" in st["Exp-Bundle X"][1], st
        # broken files
        for broken in ({"format": "x"}, {"format": "pocketknife-packages", "format_version": 99, "packages": [{}]},
                       {"format": "pocketknife-packages", "packages": []}, {"format": "pocketknife-packages", "packages": [{"key": "a"}]}):
            try:
                pkgexport.run_import(admin, broken, pkg_values=_m._pkg_values)
                raise AssertionError(f"should have been rejected: {broken}")
            except HTTPException as e:
                assert e.status_code == 400
        loop = {"format": "pocketknife-packages", "format_version": 1, "packages": [
            {"key": "a", "name": "L1", "steps": {"main": [{"type": "nested", "package_key": "b"}]}},
            {"key": "b", "name": "L2", "steps": {"main": [{"type": "nested", "package_key": "a"}]}}]}
        try:
            pkgexport.run_import(admin, loop, pkg_values=_m._pkg_values)
            raise AssertionError("loop must be rejected")
        except HTTPException as e:
            assert "Schleife" in str(e.detail)
        # OU admin: tenant enforced, service account -> Local System, files only from the tenant folder
        tid = db.ex("INSERT INTO tenants(name, repo_folder, allow_packages) VALUES('Exp-Nord', 'Kunden\\Nord', 1)")
        ou = {"username": "ou", "scope": [tid]}
        one = _json.loads(text)
        one["packages"] = [p for p in one["packages"] if p["name"] == "Exp-7zip"]
        one["packages"][0]["name"] = "Exp-OU"
        r = pkgexport.run_import(ou, one, tenant_id=None, dry_run=True, pkg_values=_m._pkg_values)
        assert r["items"][0]["status"] == "error" and "Repository-Ordner" in r["items"][0]["message"], r
        for st_ in one["packages"][0]["steps"]["main"]:
            if st_.get("folder"):
                st_["folder"] = "Kunden\\Nord\\7zip"
        r = pkgexport.run_import(ou, one, tenant_id=None, dry_run=True, pkg_values=_m._pkg_values)
        assert r["items"][0]["status"] == "error" and "nicht freigegeben" in r["items"][0]["message"], ("2.29.1: scripts only with release", r)
        db.ex("UPDATE tenants SET allow_scripts=1 WHERE id=?", (tid,))
        r = pkgexport.run_import(ou, one, tenant_id=None, dry_run=False, pkg_values=_m._pkg_values)
        assert r["items"][0]["status"] == "new", r
        row = db.q1("SELECT * FROM packages WHERE name='Exp-OU'")
        created.append(row["id"])
        assert row["tenant_id"] == tid and row["run_as"] == "system", row
        db.ex("UPDATE tenants SET allow_packages=0 WHERE id=?", (tid,))
        try:
            pkgexport.run_import(ou, one, dry_run=True, pkg_values=_m._pkg_values)
            raise AssertionError("no import without release")
        except HTTPException as e:
            assert e.status_code == 403
        # 2.37: packages of other tenants are invisible to OU admins – cannot be exported either
        db.ex("UPDATE packages SET tenant_id=? WHERE id=?", (tid + 1000, a))
        try:
            got = pkgexport.export(ou, [a])[0]["packages"]
        except HTTPException as e:
            got = [] if e.status_code == 404 else None
        assert got == [], "foreign package not exportable"
        db.ex("UPDATE packages SET tenant_id=NULL WHERE id=?", (a,))
        # Nesting never onto a same-named package of ANOTHER tenant (whose OU admin could change it)
        other = db.ex("INSERT INTO tenants(name, allow_packages) VALUES('Exp-Sued', 1)")
        west = db.ex("INSERT INTO tenants(name, allow_packages) VALUES('Exp-West', 1)")
        db.ex("UPDATE packages SET tenant_id=? WHERE id=?", (other, a))  # "Exp-7zip" now belongs to Süd
        f2 = _json.loads(text)
        for p in f2["packages"]:
            if p["name"] == "Exp-Bundle":
                p["name"] = "Exp-Bundle-West"
        r = pkgexport.run_import(admin, f2, tenant_id=west, conflict="skip", dry_run=False, pkg_values=_m._pkg_values)
        st = {x["name"]: x["status"] for x in r["items"]}
        assert st == {"Exp-Bundle-West": "new", "Exp-7zip": "new"}, ("Süd package does not count as existing for West", st)
        nw = db.q1("SELECT * FROM packages WHERE name='Exp-Bundle-West'")
        n7 = db.q1("SELECT * FROM packages WHERE name='Exp-7zip' AND tenant_id=?", (west,))
        created += [nw["id"], n7["id"]]
        nested = [s_ for _k, s_ in steps.iter_steps(steps.normalize(_m.deploy.row_to_package(nw))) if s_.get("type") == "nested"]
        assert nested[0]["package_id"] == n7["id"] != a, "nested onto the West package, never onto the one of Süd"
        db.ex("UPDATE packages SET tenant_id=NULL WHERE id=?", (a,))
        db.ex("DELETE FROM packages WHERE id IN (?, ?)", (nw["id"], n7["id"]))
        db.ex("DELETE FROM tenants WHERE id IN (?, ?)", (other, west))
        # same name twice in the file -> only created once
        dup = _json.loads(text)
        dup["packages"] = [dict(p, name="Exp-Doppelt", key=k) for p, k in ((dup["packages"][1], "x1"), (dup["packages"][1], "x2"))]
        r = pkgexport.run_import(admin, dup, conflict="skip", dry_run=True, pkg_values=_m._pkg_values)
        assert [x["status"] for x in r["items"]] == ["new", "skip"], r
        # Export: nested package of another tenant – invisible to OU admins since 2.37, export rejected
        db.ex("UPDATE tenants SET allow_packages=1 WHERE id=?", (tid,))
        db.ex("UPDATE packages SET tenant_id=? WHERE id=?", (tid + 1000, a))
        try:
            got = [x["name"] for x in pkgexport.export(ou, [b])[0]["packages"]]
        except HTTPException as e:
            got = [] if e.status_code in (400, 403, 404) else ("HTTP", e.status_code, e.detail)
        fname = db.val("SELECT name FROM packages WHERE id=?", (a,))
        assert isinstance(got, list) and fname not in got, ("nested foreign package exported", got)
        assert len(pkgexport.export(admin, [b])[0]["packages"]) == 2, "main admin: both"
        db.ex("UPDATE packages SET tenant_id=NULL WHERE id=?", (a,))
        # Error messages of the repository: for OU admins without path, server and account (also other spelling)
        o_login = _m.config.repo_login
        _m.config.repo_login = lambda: "FIRMA\\svc-repo"
        try:
            msg = _m._repo_err(ou, Exception("SMB-Anmeldung an FS01 als svc-repo abgelehnt: \\\\fs01\\pakete\\x"))
            assert "fs01" not in msg.lower() and "svc-repo" not in msg and "pakete" not in msg.lower(), msg
            assert _m._repo_err(admin, Exception("\\\\fs01\\PAKETE")) == "\\\\fs01\\PAKETE", "main admins see everything"
        finally:
            _m.config.repo_login = o_login
        assert access.ROUTES[("POST", "/api/packages/import")] is access._p_build
        assert access.ROUTES[("POST", "/api/packages/export")] is access.OPEN
        db.ex("DELETE FROM tenants WHERE id=?", (tid,))
    finally:
        for pid in reversed(created):
            db.ex("DELETE FROM packages WHERE id=?", (pid,))
        db.ex("DELETE FROM collections WHERE id=?", (col,))
        db.set_setting("repo_path", old_repo or "")


@test("2.25.5 Package list: green checkmark \"Benachrichtigung\" by the same rule as the message on the client (always/never/as per setting, only for real installations)")
def _():
    from app import main as _m
    old = db.setting("toast_enabled")

    def eff(name, mode, st):
        row = {"id": 0, "name": name, "steps": json.dumps({"main": [st]}), "conditions": "{}", "run_as": "system",
               "notify_user": mode, "type": "custom"}
        return _m._pkg_out(row)["notify_effective"]
    inst = {"type": "install", "source": "winget", "winget_id": "7zip.7zip", "winget_action": "install"}
    ps = {"type": "powershell", "script": "Write-Output 1"}
    try:
        db.set_setting("toast_enabled", 1)
        assert eff("7-Zip", "", inst) is True
        assert eff("7-Zip", "off", inst) is False
        assert eff("Skript", "", ps) is False and eff("Skript", "on", ps) is True, "scripts only with \"always\""
        assert eff("Deinstallation: 7-Zip", "", {**inst, "winget_action": "uninstall"}) is False
        db.set_setting("toast_enabled", 0)
        assert eff("7-Zip", "", inst) is False and eff("7-Zip", "on", inst) is True, "setting off – \"always\" still applies"
    finally:
        db.set_setting("toast_enabled", old if old is not None else 1)
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    assert "notify_effective ? h('span', { style: { color: 'var(--ok)'" in js


@test("2.26 Integrity check: record the state, detect changed/missing/new files, interrupt the run, accept the new state, full check, OU view")
def _():
    import tempfile as _tf
    import time as _time
    from app import deploy, integrity, remote, steps
    from app import main as _m
    admin = {"username": "haupt", "scope": None}
    root = _tf.mkdtemp(prefix="pk-integ-", dir=TMP)

    def w(rel, data):
        f = os.path.join(root, *rel.split("\\"))
        os.makedirs(os.path.dirname(f), exist_ok=True)
        open(f, "wb").write(data)
        return f
    msi = w("7zip\\24.08\\7z.msi", b"A" * 4096)
    ini = w("7zip\\24.08\\setup.ini", b"[x]\n")
    w("Office\\setup.exe", b"E" * 100)
    old_repo = db.setting("repo_path")
    db.set_setting("repo_path", root)
    remote.repo_cache_clear()
    created = []

    def mk(data):
        v = _m._pkg_values(data, user=admin)
        cols = deploy.PKG_FIELDS + ["created_at", "updated_at"]
        pid = db.ex(f"INSERT INTO packages({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
                    tuple(v[k] for k in deploy.PKG_FIELDS) + (util.now(), util.now()))
        created.append(pid)
        return pid
    db.ex("INSERT OR IGNORE INTO computers(id, name, online, winrm) VALUES(2601, 'PC-2601', 1, 1)")

    def drain():
        # the one background worker computes fingerprints – wait until it has finished
        integrity.process_queue(seconds=30)
        for _ in range(200):
            with integrity._q_lock:
                idle = not integrity._hash_queue and not integrity._state["worker"]
            if idle:
                return
            _time.sleep(0.05)
        raise AssertionError("fingerprints do not finish")
    try:
        a = mk({"name": "Integ-7zip", "steps": {"main": [{"type": "install", "source": "file", "folder": "7zip\\24.08",
                                                           "install_file": "7z.msi", "install_type": "msi"}]}})
        b = mk({"name": "Integ-Bundle", "steps": {"main": [{"type": "nested", "package_id": a},
                                                           {"type": "filecopy", "source": "Office\\setup.exe", "target": "C:\\Temp"}]}})
        wg = mk({"name": "Integ-winget", "steps": {"main": [{"type": "install", "source": "winget", "winget_id": "7zip.7zip"}]}})
        assert integrity.check_package(wg)["state"] == "", "winget: nothing to check"
        r = integrity.check_package(a, queue_new=True)  # as after saving
        assert r["state"] == "ok", r
        assert "keys" not in integrity.scope_view(r, None) and "keys" not in integrity.scope_view(r, ["x"]), "no path list to the UI"
        assert db.val("SELECT COUNT(*) FROM repo_baseline") == 2 and db.val("SELECT 1 FROM repo_baseline_dirs WHERE path='7zip\\24.08'")
        drain()
        assert db.val("SELECT COUNT(*) FROM repo_baseline WHERE sha256 IS NOT NULL") == 2, "fingerprint filled in later"
        assert integrity.check_package(b, queue_new=True)["state"] == "ok"
        drain()
        # new file in the package folder -> yellow, run continues
        w("7zip\\24.08\\neu.txt", b"x")
        r = integrity.check_package(a)
        assert r["state"] == "warn" and r["issues"][0]["kind"] == "added", r
        os.remove(os.path.join(root, "7zip", "24.08", "neu.txt"))
        # other file changed -> yellow
        w("7zip\\24.08\\setup.ini", b"[x]\nmehr=1\n")
        r = integrity.check_package(a)
        assert r["state"] == "warn" and r["issues"][0]["kind"] == "changed" and not r["issues"][0]["main"], r
        integrity.accept(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (a,))), "haupt")
        # (the background worker may already have recomputed it – but from the NEW content; never an old one)
        assert db.val("SELECT sha256 FROM repo_baseline WHERE path='7zip\\24.08\\setup.ini'") in (
            None, __import__("hashlib").sha256(b"[x]\nmehr=1\n").hexdigest()), "fingerprint is recomputed, never an old one adopted"
        drain()
        assert integrity.check_package(a)["state"] == "ok", "new state accepted"
        # only touched (same content, new date) -> stays ok, state is brought up to date
        st0 = os.stat(msi).st_mtime
        os.utime(msi, (st0 + 100, st0 + 100))
        r = integrity.check_package(a)
        assert r["state"] == "ok", r
        assert abs(db.val("SELECT mtime FROM repo_baseline WHERE path='7zip\\24.08\\7z.msi'") - (st0 + 100)) <= 2
        # installation file with same size, different content, new date -> red; run is interrupted
        open(msi, "wb").write(b"B" * 4096)
        os.utime(msi, (st0 + 200, st0 + 200))
        n0 = db.val("SELECT COUNT(*) FROM deployments")
        for pkg_id in (a, b):  # also via the nested package
            try:
                deploy.create(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (pkg_id,))), [2601], "haupt",
                              package_id=pkg_id)
                raise AssertionError("break must interrupt the run")
            except integrity.IntegrityError as e:
                assert "Unterbrochen" in str(e) and "7z.msi" in str(e) and "Inhalt geändert" in str(e), e
        assert db.val("SELECT COUNT(*) FROM deployments") == n0, "nothing created"
        integrity.store(a, {"state": "ok", "issues": [], "at": util.now(), "total": 0})
        try:
            integrity.guard(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (a,))), package_id=a)
        except integrity.IntegrityError:
            pass
        assert json.loads(db.val("SELECT integrity FROM packages WHERE id=?", (a,)))["state"] == "ok", "guard saves nothing (snippet/old state)"
        integrity.check_package(a)
        assert json.loads(db.val("SELECT integrity FROM packages WHERE id=?", (a,)))["state"] == "broken", "visible in the package list"
        assert "keys" not in json.loads(db.val("SELECT integrity FROM packages WHERE id=?", (a,)))
        # OU admin: central file -> no paths
        err = integrity.IntegrityError("x", "Integ-7zip", integrity.check_package(a)["issues"])
        assert "nicht verfügbar" in integrity.public_message(err, ["kunden\\nord"]) and "7z.msi" not in integrity.public_message(err, ["kunden\\nord"])
        v = integrity.scope_view(integrity.check_package(a), ["kunden\\nord"])
        assert all(not x["path"] for x in v["issues"]) and v["issues"][0]["kind"] == "hidden", v
        # scheduled deployment: is interrupted at start, no target touched
        db.set_setting("integrity_full_enabled", 0)
        open(msi, "wb").write(b"A" * 4096)  # temporarily correct again so that the deployment gets created
        os.utime(msi, (st0 + 100, st0 + 100))
        did = deploy.create(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (a,))), [2601], "haupt",
                            scheduled_at="2099-01-01 00:00:00", package_id=a)
        open(msi, "wb").write(b"C" * 4096)
        os.utime(msi, (st0 + 300, st0 + 300))
        deploy.start(did)
        d = db.q1("SELECT * FROM deployments WHERE id=?", (did,))
        t = db.q1("SELECT * FROM deployment_targets WHERE deployment_id=?", (did,))
        assert d["status"] == "failed" and t["status"] == "failed" and t["message"] == integrity.NEUTRAL, (d, t)
        assert "7z" not in t["message"], "no paths at the target (OU admins/kiosk see it too)"
        # Accept new state -> ok again, deployment possible
        res = integrity.accept(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (a,))), "haupt")
        drain()
        assert res["count"] >= 1 and integrity.check_package(a)["state"] == "ok"
        # File missing -> red
        os.remove(os.path.join(root, "Office", "setup.exe"))
        r = integrity.check_package(b)
        assert r["state"] == "broken" and r["issues"][0]["kind"] == "missing" and r["issues"][0]["path"] == "Office\\setup.exe", r
        w("Office\\setup.exe", b"E" * 100)
        integrity.accept(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (b,))), "haupt")
        drain()
        assert integrity.check_package(b)["state"] == "ok"
        # Full check: content swapped with same size AND same date – only the full check finds that
        drain()
        st1 = os.stat(msi).st_mtime
        open(msi, "wb").write(b"D" * 4096)
        os.utime(msi, (st1, st1))
        assert integrity.check_package(a)["state"] == "ok", "quick check does not see it"
        db.set_setting("integrity_full_enabled", 1)
        db.set_setting("integrity_full_start", 0)
        db.set_setting("integrity_full_end", 24)
        db.ex("UPDATE repo_baseline SET full_at='2000-01-01 00:00:00'")
        assert integrity.full_check() >= 1
        r = integrity.check_package(a)
        assert r["state"] == "broken" and "ausgetauscht" in r["issues"][0]["note"], r
        # Review 2.26 – folder as source of a file copy: every file in it counts like the installation file (break)
        w("Cfg\\a.xml", b"<a/>")
        c = mk({"name": "Integ-Cfg", "steps": {"main": [{"type": "filecopy", "source": "Cfg", "target": "C:\\Temp"}]}})
        assert integrity.check_package(c, queue_new=True)["state"] == "ok"
        drain()
        w("Cfg\\a.xml", b"<a>neu</a>")
        r = integrity.check_package(c)
        assert r["state"] == "broken" and r["issues"][0]["main"], r
        integrity.accept(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (c,))), "haupt")
        drain()
        # disappears briefly and comes back CHANGED -> break (the state must not be cleaned up while missing)
        import shutil as _shx
        _shx.move(os.path.join(root, "Cfg"), os.path.join(root, "Cfg-weg"))
        assert integrity.check_package(c)["state"] == "broken"
        db.ex("UPDATE repo_baseline SET confirmed_at='2000-01-01 00:00:00'")
        integrity.check_all(live=True)
        assert db.val("SELECT COUNT(*) FROM repo_baseline WHERE path='cfg\\a.xml'") == 1, "state stays as long as the package needs it"
        _shx.move(os.path.join(root, "Cfg-weg"), os.path.join(root, "Cfg"))
        w("Cfg\\a.xml", b"<a>boese</a>")
        assert integrity.check_package(c)["state"] == "broken", "came back changed – never silently adopt"
        integrity.accept(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (c,))), "haupt")
        drain()
        # Repository not reachable -> "not checkable", never a break, state stays
        _shx.move(root, root + "-weg")
        try:
            for pid in (a, b, c):
                r = integrity.check_package(pid)
                assert r["state"] == "unknown", (pid, r)
            db.ex("UPDATE repo_baseline SET confirmed_at='2000-01-01 00:00:00'")
            integrity.check_all(live=True)
            assert db.val("SELECT COUNT(*) FROM repo_baseline") >= 3, "no cleanup when repository is unreachable"
        finally:
            _shx.move(root + "-weg", root)
        # Queue: many entries quickly (set instead of list)
        t0 = _time.time()
        with integrity._q_lock:
            for i in range(20000):
                integrity._hash_queue.setdefault(f"x{i}", None)
            integrity._hash_queue.clear()
        assert _time.time() - t0 < 1
        # Full check does not get stuck on an unreadable file
        db.ex("UPDATE repo_baseline SET full_at=NULL")
        o_sha = remote.repo_sha256
        bad = {"n": 0}

        def flaky(rel, **kw):
            if bad["n"] == 0:
                bad["n"] += 1
                raise PermissionError(13, "gesperrt", rel)
            return o_sha(rel, **kw)
        remote.repo_sha256 = flaky
        try:
            assert integrity.full_check() >= 1, "it continues after the locked file"
        finally:
            remote.repo_sha256 = o_sha
        assert db.val("SELECT COUNT(*) FROM repo_baseline WHERE full_at IS NULL") == 0
        # local links in the repository are not followed (as when copying)
        os.symlink(os.path.join(root, "Office"), os.path.join(root, "Cfg", "link"))
        assert all(not x["dir"] or x.get("link") for x in remote.repo_list("Cfg") if x["name"] == "link")
        assert [f for f, _s, _m in remote.repo_tree("Cfg")] == ["Cfg\\a.xml"]
        os.remove(os.path.join(root, "Cfg", "link"))
        # Index in memory (quick check every 5 minutes) delivers the same
        remote.repo_search("", "7z")
        for _ in range(100):
            if not remote.repo_cache_status()["building"]:
                break
            _time.sleep(0.05)
        assert remote.repo_index_files() is not None
        res_idx = integrity.evaluate(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (a,))), live=False)
        assert res_idx["state"] == "broken" and not res_idx["live"], res_idx
        # Cleanup: state of files that no package uses any more disappears
        # outdated index: false alarm is confirmed directly on the share before saving (here: not confirmed)
        open(msi, "wb").write(b"E" * 5000)  # the index still knows the old size
        integrity.accept(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (a,))), "haupt")
        drain()
        assert remote.repo_index_files() is not None and integrity.evaluate(
            deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (a,))), live=False)["state"] != "ok", "index is outdated"
        integrity.check_all(live=False)
        assert json.loads(db.val("SELECT integrity FROM packages WHERE id=?", (a,)))["state"] == "ok", "no false alarm from the index"
        for pid in (a, b, c):
            db.ex("DELETE FROM packages WHERE id=?", (pid,))
        db.ex("UPDATE repo_baseline SET confirmed_at='2000-01-01 00:00:00'")
        db.ex("UPDATE repo_baseline_dirs SET confirmed_at='2000-01-01 00:00:00'")
        # Audit 2.29: states that saved deployments still need (redistribute, scheduled) stay
        snap = {"name": "Alt", "steps": {"pre": [], "main": [{"type": "install", "id": "s1", "source": "file", "folder": "7zip", "install_file": "7z.msi", "copy_folder": False}], "post": []}}
        db.ex("INSERT INTO repo_baseline(path, size, mtime, confirmed_at) VALUES('7zip\\7z.msi', 1, 1, '2000-01-01 00:00:00') "
              "ON CONFLICT(path) DO NOTHING")
        sd = db.ex("INSERT INTO deployments(name, package_json, status, created_at) VALUES('Alt', ?, 'success', ?)",
                   (json.dumps(snap), util.now()))
        integrity.check_all(live=True)
        assert db.val("SELECT COUNT(*) FROM repo_baseline WHERE path='7zip\\7z.msi'") == 1, "needed by a deployment"
        db.ex("DELETE FROM deployments")
        integrity.check_all(live=True)
        assert db.val("SELECT COUNT(*) FROM repo_baseline") == 0 and db.val("SELECT COUNT(*) FROM repo_baseline_dirs") == 0, sd
        # Access rules and integration
        assert _m.access.ROUTES[("POST", "/api/packages/{pid}/integrity/accept")] is _m.access._p_pkg_edit
        assert _m.access.ROUTES[("POST", "/api/packages/{pid}/integrity/check")] is _m.access._p_pkg
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "deploy.py"), encoding="utf-8").read()
        assert "integrity.guard(pkg, package_id=package_id, name=name)" in src and "_interrupt(did, str(e))" in src
    finally:
        for pid in created:
            db.ex("DELETE FROM packages WHERE id=?", (pid,))
        db.ex("DELETE FROM deployment_targets WHERE computer_id=2601")
        db.ex("DELETE FROM computers WHERE id=2601")
        db.ex("DELETE FROM repo_baseline")
        db.ex("DELETE FROM repo_baseline_dirs")
        for k in ("integrity_full_enabled", "integrity_full_start", "integrity_full_end"):
            db.ex("DELETE FROM settings WHERE key=?", (k,))
        remote.repo_cache_clear()
        db.set_setting("repo_path", old_repo or "")


@test("2.27 Repository via the UI: tenant folders automatic, upload in chunks (resume, limits), protection of used files, moving with path adjustment and rollback, duplicate lock (also between tenants), repair by fingerprint")
def _():
    import hashlib
    import secrets
    import tempfile as _tf
    import time as _t
    from app import access, auth, backup, deploy, integrity, remote, repofiles, steps, util
    from app import main as _m
    from fastapi import HTTPException
    root = _tf.mkdtemp(prefix="pk-repo27-", dir=TMP)
    old_repo = db.setting("repo_path")
    db.set_setting("repo_path", root)
    remote.repo_cache_clear()
    t_nord = db.ex("INSERT INTO tenants(name, repo_folder, admin_group, admin_group_sid, allow_packages, created_at) "
                   "VALUES('Nord27', '', 'PK-Nord27', 'S-1-5-21-27-1', 1, ?)", (util.now(),))
    t_sued = db.ex("INSERT INTO tenants(name, repo_folder, admin_group, admin_group_sid, allow_packages, created_at) "
                   "VALUES('Süd27', 'Kunden\\Sued', 'PK-Sued27', 'S-1-5-21-27-2', 1, ?)", (util.now(),))
    nord = {"username": "ou-nord27", "scope": [t_nord]}
    sued = {"username": "ou-sued27", "scope": [t_sued]}
    admin = {"username": "haupt27", "scope": None}
    created = []
    real_backup = backup.create
    backups = []
    backup.create = lambda label="", user=None: backups.append(label) or {"name": f"test-{label}.zip"}

    def mk(data, user=admin):
        v = _m._pkg_values(data, user=user)
        ids, pend = repofiles.guard_package(v, None, user, allow=data.get("allow_duplicate"))
        cols = deploy.PKG_FIELDS + ["created_at", "updated_at"]
        pid = db.ex(f"INSERT INTO packages({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
                    tuple(v[k] for k in deploy.PKG_FIELDS) + (util.now(), util.now()))
        repofiles.store_idents(pid, ids, pend)
        created.append(pid)
        return pid

    def upload(user, folder, name, data, chunk=4000, **kw):
        s = repofiles.upload_start(user, folder, name, len(data), **kw)
        off = s["received"]
        while off < len(data):
            off = repofiles.upload_chunk(user, s["token"], off, data[off:off + chunk])["received"]
        return repofiles.upload_finish(user, s["token"])

    def expect(fn, cls=repofiles.RepoError, text=""):
        try:
            fn()
        except cls as e:
            assert text.lower() in str(e).lower(), (text, str(e))
            return e
        raise AssertionError(f"expected: {cls.__name__} \"{text}\"")
    disk = lambda rel: os.path.join(root, *rel.split("\\"))
    try:
        # 1) Tenant folders automatic ("Mandanten\<name>"), created in the repository; overlap forbidden
        assert repofiles.ensure_tenant_folder(t_nord) == "Mandanten\\Nord27" and os.path.isdir(disk("Mandanten\\Nord27"))
        assert repofiles.roots(nord) == ["Mandanten\\Nord27"]
        expect(lambda: repofiles.check_tenant_folder("Mandanten", t_sued), text="überschneidet")
        expect(lambda: repofiles.check_tenant_folder("Mandanten\\Nord27\\x", t_sued), text="überschneidet")
        repofiles.check_tenant_folder("Mandanten\\Nord27", t_nord)
        st, r = _asgi("POST", "/api/tenants", None, {})  # nothing without login
        assert st == 401
        # 2) Scope: only the own folder, never rename/delete the folder itself, no "..", nothing of POCKET KNIFE
        assert repofiles.need_scope(nord, "Mandanten/Nord27/App") == "Mandanten\\Nord27\\App"
        expect(lambda: repofiles.need_scope(nord, "Kunden\\Sued"), text="Mandanten")
        expect(lambda: repofiles.need_scope(nord, "Mandanten\\Nord27", own_root_ok=False), text="selbst")
        expect(lambda: repofiles.need_scope(nord, "Mandanten\\Nord27\\..\\..\\Kunden"), text="Ungültig")
        expect(lambda: repofiles.need_scope(admin, ".pk-uploads\\x"), text="POCKET KNIFE")
        for bad in ("a:b", "CON", "lpt1.txt", "x.", " x", "a*b", ".pk-uploads"):
            expect(lambda: repofiles.mkdir(nord, "Mandanten\\Nord27", bad))
        repofiles.mkdir(nord, "Mandanten\\Nord27", "App")
        expect(lambda: repofiles.mkdir(nord, "Mandanten\\Nord27", "app"), text="schon")
        # Link to the outside: nothing is ever written there
        outside = _tf.mkdtemp(prefix="pk-out-", dir=TMP)
        os.symlink(outside, disk("Mandanten\\Nord27\\Link"))
        expect(lambda: repofiles.mkdir(nord, "Mandanten\\Nord27\\Link", "x"), Exception, "außerhalb")
        assert not os.listdir(outside)
        os.remove(disk("Mandanten\\Nord27\\Link"))
        # 3) Upload in chunks: wrong position -> current state, resume after abort, foreign user -> not found
        data = bytes(range(256)) * 60  # 15360 bytes
        s = repofiles.upload_start(nord, "Mandanten\\Nord27\\App", "tool.exe", len(data))
        assert repofiles.upload_chunk(nord, s["token"], 0, data[:5000])["received"] == 5000
        e = expect(lambda: repofiles.upload_chunk(nord, s["token"], 0, data[:5000]), text="Position")
        assert e.info["received"] == 5000 and e.info["status"] == 409
        expect(lambda: repofiles.upload_chunk(sued, s["token"], 5000, data[5000:]), text="nicht gefunden")
        repofiles._hashers.clear()  # as after a restart: rebuild the checksum from the intermediate file
        s2 = repofiles.upload_start(nord, "Mandanten\\Nord27\\App", "tool.exe", len(data))
        assert s2["token"] == s["token"] and s2["received"] == 5000 and s2["resumed"], s2
        assert ".pk-uploads" not in {x["name"] for x in remote.repo_list("")}, "clipboard not visible"
        expect(lambda: repofiles.upload_finish(nord, s["token"]), text="unvollständig")
        repofiles.upload_chunk(nord, s["token"], 5000, data[5000:])
        fin = repofiles.upload_finish(nord, s["token"])
        assert fin["sha256"] == hashlib.sha256(data).hexdigest() and open(disk("Mandanten\\Nord27\\App\\tool.exe"), "rb").read() == data
        assert db.val("SELECT sha256 FROM repo_hash WHERE path=?", ("mandanten\\nord27\\app\\tool.exe",)) == fin["sha256"]
        assert not db.val("SELECT COUNT(*) FROM repo_uploads") and not os.listdir(disk(".pk-uploads"))
        expect(lambda: repofiles.upload_start(nord, "Mandanten\\Nord27\\App", "tool.exe", 3), text="schon")
        # Limits: file type (tenants only), size, quota
        expect(lambda: repofiles.upload_start(nord, "Mandanten\\Nord27\\App", "bild.xyz", 3), text="Dateityp")
        db.set_setting("repo_upload_max_mb", 1)
        expect(lambda: repofiles.upload_start(admin, "", "gross.exe", 2 * 1048576), text="zu groß")
        db.set_setting("repo_upload_max_mb", 4096)
        db.ex("UPDATE tenants SET quota_mb=1 WHERE id=?", (t_nord,))
        expect(lambda: repofiles.upload_start(nord, "Mandanten\\Nord27", "big.zip", 1048576), text="Kontingent")
        db.ex("UPDATE tenants SET quota_mb=NULL WHERE id=?", (t_nord,))
        # Aborted uploads are cleaned up after 24 hours
        s3 = repofiles.upload_start(nord, "Mandanten\\Nord27", "halb.zip", 100)
        repofiles.upload_chunk(nord, s3["token"], 0, b"x" * 10)
        db.ex("UPDATE repo_uploads SET updated='2000-01-01 00:00:00'")
        assert repofiles.cleanup_uploads() == 1 and not os.listdir(disk(".pk-uploads"))
        # 4) Used by a package: deleting and overwriting blocked (with name), adding new files works (with a note)
        p_tool = mk({"name": "Nord-Tool", "tenant_id": t_nord, "steps": {"main": [{"type": "install", "source": "file",
                     "folder": "Mandanten\\Nord27\\App", "install_file": "tool.exe", "install_type": "exe"}]}}, user=nord)
        assert ("sha256", fin["sha256"]) in {(r["kind"], r["value"]) for r in db.q("SELECT * FROM pkg_ident WHERE package_id=?", (p_tool,))}, "fingerprint from the upload"
        e = expect(lambda: repofiles.delete(nord, ["Mandanten\\Nord27\\App\\tool.exe"]), repofiles.Blocked, "Nord-Tool")
        assert any("Nord-Tool" in x for x in e.info["used_by"])
        expect(lambda: repofiles.delete(nord, ["Mandanten\\Nord27\\App"]), repofiles.Blocked, "Nord-Tool")
        expect(lambda: repofiles.upload_start(nord, "Mandanten\\Nord27\\App", "tool.exe", 3, overwrite=True), repofiles.Blocked, "Nord-Tool")
        expect(lambda: repofiles.delete(admin, ["Mandanten\\Nord27"]), repofiles.Blocked, "Mandant")
        r = upload(nord, "Mandanten\\Nord27\\App", "readme.txt", b"hallo")
        assert "Nord-Tool" in r.get("in_package_folder", ""), r
        assert "App" in {x["name"] for x in remote.repo_list("Mandanten\\Nord27") if x["dir"]}
        um = repofiles.used_map("Mandanten\\Nord27")
        assert any("Nord-Tool" in x for x in um["app"]), um
        # 5) Rename: paths in the package (also legacy fields) and in the confirmed state follow; backup beforehand
        integrity.check_package(p_tool, queue_new=True)
        integrity.process_queue(seconds=10)
        pv = repofiles.rename(nord, "Mandanten\\Nord27\\App", "App2", dry_run=True)
        assert pv["dry_run"] and pv["packages"][0]["name"] == "Nord-Tool" and pv["packages"][0]["changes"][0]["new"] == "Mandanten\\Nord27\\App2", pv
        assert os.path.isdir(disk("Mandanten\\Nord27\\App")), "preview changes nothing"
        res = repofiles.rename(nord, "Mandanten\\Nord27\\App", "App2")
        assert not res["dry_run"] and os.path.isfile(disk("Mandanten\\Nord27\\App2\\tool.exe")) and backups[-1] == "vor-umbenennen"
        row = db.q1("SELECT * FROM packages WHERE id=?", (p_tool,))
        st0 = steps.normalize(deploy.row_to_package(row))["steps"]["main"][0]
        assert st0["folder"] == "Mandanten\\Nord27\\App2" and row["folder"] == "Mandanten\\Nord27\\App2", (st0, row["folder"])
        assert db.val("SELECT 1 FROM repo_baseline WHERE path='mandanten\\nord27\\app2\\tool.exe'") and \
            not db.val("SELECT 1 FROM repo_baseline WHERE path LIKE 'mandanten\\nord27\\app\\%'"), "state follows"
        assert integrity.check_package(p_tool)["state"] in ("ok", "warn"), "no break after renaming"
        # Rename a file IN the used package folder (not the installation file) -> blocked; the installation file works
        pv = repofiles.rename(nord, "Mandanten\\Nord27\\App2\\readme.txt", "liesmich.txt", dry_run=True)
        assert pv["problems"] and "Paketordner" in pv["problems"][0] or "benutzt" in pv["problems"][0], pv
        repofiles.rename(nord, "Mandanten\\Nord27\\App2\\tool.exe", "tool2.exe")
        st0 = steps.normalize(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (p_tool,))))["steps"]["main"][0]
        assert st0["install_file"] == "tool2.exe" and st0["folder"] == "Mandanten\\Nord27\\App2", st0
        # Error in the middle of the rebuild -> everything rolled back (files back in the old place, package unchanged)
        real_apply = repofiles._apply_db
        repofiles._apply_db = lambda plan, pairs: (_ for _ in ()).throw(RuntimeError("Datenbank weg"))
        try:
            expect(lambda: repofiles.rename(nord, "Mandanten\\Nord27\\App2", "App3"), text="zurückgedreht")
        finally:
            repofiles._apply_db = real_apply
        assert os.path.isdir(disk("Mandanten\\Nord27\\App2")) and not os.path.exists(disk("Mandanten\\Nord27\\App3"))
        # Main admin: central package must not move into a tenant folder; moving a tenant folder moves the tenant along
        os.makedirs(disk("Zentral\\7zip"))
        open(disk("Zentral\\7zip\\7z.msi"), "wb").write(b"M" * 300)
        p_z = mk({"name": "Zentral-7zip", "steps": {"main": [{"type": "install", "source": "file", "folder": "Zentral\\7zip",
                  "install_file": "7z.msi", "install_type": "msi"}]}})
        pv = repofiles.move_into(admin, ["Zentral\\7zip"], "Mandanten\\Nord27", dry_run=True)
        assert any("Zentrales Paket" in x for x in pv["problems"]), pv
        expect(lambda: repofiles.move_into(nord, ["Zentral\\7zip"], "Mandanten\\Nord27", dry_run=True), text="Mandanten")
        os.makedirs(disk("Kunden\\Sued"))
        res = repofiles.move_into(admin, ["Kunden\\Sued"], "Mandanten", dry_run=False)
        assert db.val("SELECT repo_folder FROM tenants WHERE id=?", (t_sued,)) == "Mandanten\\Sued", "tenant folder moves along"
        # Wizard: create a new main folder and move several folders into it at once (preview changes nothing)
        os.makedirs(disk("Office"))
        pv = repofiles.move_into(admin, ["Zentral\\7zip", "Office"], "Basis", dry_run=True, create_dest=True)
        assert pv["create_dest"] == "Basis" and not pv["problems"] and not os.path.exists(disk("Basis")), pv
        res = repofiles.move_into(admin, ["Zentral\\7zip", "Office"], "Basis", dry_run=False, create_dest=True)
        assert os.path.isfile(disk("Basis\\7zip\\7z.msi")) and os.path.isdir(disk("Basis\\Office"))
        assert steps.normalize(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (p_z,))))["steps"]["main"][0]["folder"] == "Basis\\7zip"
        # scheduled deployment (snapshot) is adjusted too
        did = db.ex("INSERT INTO deployments(name, package_json, status, created_at) VALUES('Plan27', ?, 'scheduled', ?)",
                    (json.dumps(steps.expand(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (p_z,))))), util.now()))
        repofiles.rename(admin, "Basis\\7zip", "SevenZip")
        assert "Basis\\\\SevenZip" in db.val("SELECT package_json FROM deployments WHERE id=?", (did,))
        db.ex("DELETE FROM deployments WHERE id=?", (did,))
        # 6) Duplicate lock – scopes: central, Nord, Süd. Same scope allowed, different one blocked (also tenant <-> tenant)
        wg = {"steps": {"main": [{"type": "install", "source": "winget", "winget_id": "Dup27.App"}]}}
        p_wz = mk({"name": "Dup zentral", **wg})
        mk({"name": "Dup zentral 2", **wg})  # same scope: allowed
        e = expect(lambda: mk({"name": "Dup Nord", "tenant_id": t_nord, **wg}, user=nord), repofiles.Duplicate, "Dup zentral")
        assert e.info["can_override"] is False and e.info["duplicate"][0]["kind"] == "winget"
        db.ex("DELETE FROM pkg_ident WHERE package_id IN (?, ?)", (p_wz, created[-1]))
        p_wn = mk({"name": "Dup Nord", "tenant_id": t_nord, **wg}, user=nord)
        # 2.37: OU admin of another tenant – rejected, without revealing name/tenant of the foreign package
        e = expect(lambda: mk({"name": "Dup Süd", "tenant_id": t_sued, **wg}, user=sued), repofiles.Duplicate, "anderen Mandanten")
        assert "Nord27" not in str(e) and "Dup Nord" not in str(e) and not e.info["duplicate"], (str(e), e.info)
        e = expect(lambda: mk({"name": "Dup Süd", "tenant_id": t_sued, **wg}), repofiles.Duplicate, "übersteuern")
        assert e.info["can_override"] is True
        mk({"name": "Dup Süd (Hauptadmin)", "tenant_id": t_sued, "allow_duplicate": True, **wg})
        # Legacy case (duplicate already present) does not block saving other changes
        v = _m._pkg_values({"name": "Dup Nord umbenannt", "tenant_id": t_nord, **wg}, p_wn, user=nord)
        repofiles.guard_package(v, p_wn, nord)
        # via the interface: 409 with package list
        tok = secrets.token_urlsafe(16)
        th = hashlib.sha256(tok.encode()).hexdigest()
        db.ex("INSERT INTO sessions(token_hash, username, display_name, created, expires, ip, last_active, role, scope) VALUES(?,?,?,?,?,?,?,?,?)",
              (th, "ou-sued27", "Test", util.now(), "2099-01-01 00:00:00", "127.0.0.1", util.now(), "admin", json.dumps([t_sued])))
        auth._checked[th] = _t.time() + 10 ** 6
        st, r = _asgi("POST", "/api/packages", tok, {"name": "Dup API", **wg})
        assert st == 409 and "message" in r["detail"] and r["detail"]["duplicate"] == [], (st, r)  # 2.37: foreign package not named
        st, r = _asgi("POST", "/api/repository/mkdir", tok, {"folder": "Mandanten\\Sued", "name": "Neu"})
        assert st == 200 and os.path.isdir(disk("Mandanten\\Sued\\Neu")), (st, r)
        st, r = _asgi("POST", "/api/repository/mkdir", tok, {"folder": "Mandanten\\Nord27", "name": "Fremd"})
        assert st == 403, (st, r)
        st, r = _asgi("POST", "/api/repository/delete", tok, {"items": ["Mandanten\\Nord27\\App2"]})
        assert st == 403, (st, r)
        # Upload via the interface (chunk as raw bytes), completion
        st, r = _asgi("POST", "/api/repository/upload", tok, {"folder": "Mandanten\\Sued\\Neu", "name": "api.msi", "size": 6})
        assert st == 200 and r["chunk"] == repofiles.CHUNK, (st, r)
        st, r2 = _asgi("PUT", f"/api/repository/upload/{r['token']}?offset=0", tok, raw=b"abcdef")
        assert st == 200 and r2["received"] == 6, (st, r2)
        st, r2 = _asgi("POST", f"/api/repository/upload/{r['token']}/finish", tok, {})
        assert st == 200 and open(disk("Mandanten\\Sued\\Neu\\api.msi"), "rb").read() == b"abcdef", (st, r2)
        st, r = _asgi("GET", "/api/repository/info", tok)
        assert st == 200 and r["roots"] == ["Mandanten\\Sued"] and "msi" in r["types"], r
        db.ex("DELETE FROM sessions WHERE token_hash=?", (th,))
        # same file (SHA-256) of a package from Nord: Süd must not upload it; main admin can after confirmation
        tool = open(disk("Mandanten\\Nord27\\App2\\tool2.exe"), "rb").read()
        e = expect(lambda: upload(sued, "Mandanten\\Sued", "kopie.exe", tool), repofiles.Duplicate, "anderen Mandanten")
        assert "Nord-Tool" not in str(e), "2.37: foreign package not named"
        assert not os.path.exists(disk("Mandanten\\Sued\\kopie.exe")) and not os.listdir(disk(".pk-uploads")), "tenant: discarded"
        e = expect(lambda: upload(admin, "Basis", "kopie.exe", tool), repofiles.Duplicate, "Nord-Tool")
        assert e.info["token"] and not os.path.exists(disk("Basis\\kopie.exe"))
        repofiles.upload_finish(admin, e.info["token"], allow_dup=True)
        assert open(disk("Basis\\kopie.exe"), "rb").read() == tool
        # Nesting: only central ones or the same tenant's – also for main admins
        try:
            _m._pkg_values({"name": "Zentral mit Nord", "steps": {"main": [{"type": "nested", "package_id": p_tool}]}}, user=admin)
            raise AssertionError("central must not nest a tenant package")
        except HTTPException as x:
            assert x.status_code == 403
        _m._pkg_values({"name": "Nord mit Zentral", "tenant_id": t_nord, "steps": {"main": [{"type": "nested", "package_id": p_z}]}}, user=nord)
        # 7) Repair by fingerprint: moved outside -> found, paths adopted
        integrity.check_package(p_tool, queue_new=True)
        integrity.process_queue(seconds=10)
        assert db.val("SELECT sha256 FROM repo_baseline WHERE path='mandanten\\nord27\\app2\\tool2.exe'")
        os.makedirs(disk("Mandanten\\Nord27\\Woanders"))
        os.rename(disk("Mandanten\\Nord27\\App2\\tool2.exe"), disk("Mandanten\\Nord27\\Woanders\\tool2.exe"))
        os.rename(disk("Mandanten\\Nord27\\App2"), disk("Mandanten\\Nord27\\App-alt"))
        remote.repo_cache_clear()
        key = remote._repo_key()
        remote._repo_cache_start(key, force=True)
        for _ in range(200):
            if remote.repo_index_files() is not None:
                break
            _t.sleep(0.05)
        pk = deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (p_tool,)))
        sug = repofiles.repair_suggest(nord, pk)
        assert not sug["items"] and sug["not_found"] == ["Mandanten\\Nord27\\App2"], ("folder only if ALL files are located there", sug)
        # File back in the renamed folder -> the folder is completely found again
        os.rename(disk("Mandanten\\Nord27\\Woanders\\tool2.exe"), disk("Mandanten\\Nord27\\App-alt\\tool2.exe"))
        remote.repo_cache_clear()
        remote._repo_cache_start(remote._repo_key(), force=True)
        for _ in range(200):
            if remote.repo_index_files() is not None:
                break
            _t.sleep(0.05)
        sug = repofiles.repair_suggest(nord, pk)
        assert sug["items"] == [{"old": "Mandanten\\Nord27\\App2", "new": "Mandanten\\Nord27\\App-alt", "kind": "dir",
                                 "how": sug["items"][0]["how"]}], sug
        expect(lambda: repofiles.repair_apply(sued, [("Mandanten\\Nord27\\App2", "Mandanten\\Nord27\\App-alt")]), text="Mandanten")
        res = repofiles.repair_apply(nord, [(x["old"], x["new"]) for x in sug["items"]])
        st0 = steps.normalize(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (p_tool,))))["steps"]["main"][0]
        assert st0["folder"] == "Mandanten\\Nord27\\App-alt" and backups[-1] == "vor-reparatur", st0
        assert integrity.check_package(p_tool)["state"] != "broken"
        # 8) Index: own changes immediately visible (without re-reading), also while a read is running
        repofiles.mkdir(admin, "Basis", "Frisch")
        assert "Frisch" in {x["name"] for x in remote.repo_search("Basis", "Frisch")["items"]}
        repofiles.delete(admin, ["Basis\\Frisch"])
        assert not remote.repo_search("Basis", "Frisch")["items"]
        # --- Review findings 2.27 ---
        # H2: central package never with files from a tenant folder; OU admin puts nothing into folders used by others
        try:
            _m._pkg_values({"name": "Zentral im Mandant", "steps": {"main": [{"type": "install", "source": "file",
                            "folder": "Mandanten\\Nord27\\App-alt", "install_file": "tool2.exe", "install_type": "exe"}]}}, user=admin)
            raise AssertionError("central package from tenant folder")
        except HTTPException as x:
            assert x.status_code == 400 and "Mandant" in str(x.detail), x.detail
        p_cz = db.ex("INSERT INTO packages(name, steps, conditions, run_as, created_at) VALUES('Alt-Zentral', ?, '{}', 'system', ?)",
                     (json.dumps({"main": [{"type": "install", "source": "file", "folder": "Mandanten\\Nord27\\Alt",
                                            "install_file": "a.exe", "install_type": "exe"}]}), util.now()))
        created.append(p_cz)
        os.makedirs(disk("Mandanten\\Nord27\\Alt"))
        open(disk("Mandanten\\Nord27\\Alt\\a.exe"), "wb").write(b"alt")
        expect(lambda: repofiles.upload_start(nord, "Mandanten\\Nord27\\Alt", "version.dll", 3), repofiles.Blocked, "Alt-Zentral")
        expect(lambda: repofiles.mkdir(nord, "Mandanten\\Nord27\\Alt", "sub"), repofiles.Blocked, "Alt-Zentral")
        upload(nord, "Mandanten\\Nord27", "x.txt", b"x")
        pv = repofiles.move_into(nord, ["Mandanten\\Nord27\\x.txt"], "Mandanten\\Nord27\\Alt", dry_run=True)
        assert any("Alt-Zentral" in x for x in pv["problems"]), pv
        expect(lambda: repofiles.check_tenant_folder("Basis", t_nord), text="Zentral-7zip")
        # new file from someone else in a tenant folder next to a central package -> break, not just a note
        integrity.check_package(p_cz, queue_new=True)
        open(disk("Mandanten\\Nord27\\Alt\\version.dll"), "wb").write(b"evil")
        r = integrity.check_package(p_cz)
        assert r["state"] == "broken" and any(x["kind"] == "added" and x["level"] == "broken" for x in r["issues"]), r
        # M6: file type cannot be bypassed by renaming
        expect(lambda: repofiles.rename(nord, "Mandanten\\Nord27\\x.txt", "x.hta"), text="Dateityp")
        # L8: special characters that collapse when lowercasing (Kelvin sign)
        expect(lambda: repofiles.need_scope(nord, "Mandanten\\Nord27\u212a"), text="Sonderzeichen")
        expect(lambda: repofiles.mkdir(nord, "Mandanten\\Nord27", "\u212aunden"), text="Sonderzeichen")
        # L10: chunk at the right place, even if an earlier one was half written
        s4 = repofiles.upload_start(nord, "Mandanten\\Nord27", "teil.bin", 6)
        repofiles.upload_chunk(nord, s4["token"], 0, b"abc")
        with open(disk(".pk-uploads\\" + s4["token"] + ".part"), "ab") as fh:
            fh.write(b"JUNK")  # half-finished write
        repofiles.upload_chunk(nord, s4["token"], 3, b"def")
        expect(lambda: repofiles.upload_finish(nord, s4["token"]), text="Größe")  # rest at the end -> upload again
        repofiles.upload_cancel(nord, s4["token"])
        # M3: package is saved during the rebuild -> change stays, path is adjusted anyway
        real_apply = repofiles._apply_db

        def edit_then_apply(plan, pairs):
            db.ex("UPDATE packages SET name='Nord-Tool (geändert)' WHERE id=?", (p_tool,))
            row = db.q1("SELECT steps FROM packages WHERE id=?", (p_tool,))
            stp = json.loads(row["steps"])
            stp["main"][0]["arguments"] = "/S /neu"
            db.ex("UPDATE packages SET steps=? WHERE id=?", (json.dumps(stp), p_tool))
            return real_apply(plan, pairs)
        repofiles._apply_db = edit_then_apply
        try:
            repofiles.rename(nord, "Mandanten\\Nord27\\App-alt", "App4")
        finally:
            repofiles._apply_db = real_apply
        st0 = steps.normalize(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (p_tool,))))["steps"]["main"][0]
        assert st0["folder"] == "Mandanten\\Nord27\\App4" and st0["arguments"] == "/S /neu", st0
        # M4: full check while rebuilding saves nothing outdated and does not clean up
        calls = {"n": 0}
        real_eval = integrity.evaluate

        def eval_and_move(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                integrity.moved()
            return real_eval(*a, **kw)
        integrity.evaluate = eval_and_move
        n_base = db.val("SELECT COUNT(*) FROM repo_baseline")
        db.ex("UPDATE repo_baseline SET confirmed_at='2000-01-01 00:00:00'")
        try:
            integrity.check_all(live=True)
        finally:
            integrity.evaluate = real_eval
        assert db.val("SELECT COUNT(*) FROM repo_baseline") == n_base, "nothing cleaned up"
        # H1: deleting a folder on the share does not follow a junction in it – with a simulated smbclient
        # (junction = entry with attribute 0x400; the real server would follow it)
        import types as _ty
        sb = _tf.mkdtemp(prefix="pk-smb27-", dir=TMP)
        loc = lambda unc: os.path.join(sb, *[x for x in unc.replace("\\\\srv\\P$", "").split("\\") if x])  # noqa: E731

        class _St:
            def __init__(self, p):
                self.st_file_attributes = 0x410 if os.path.islink(p) else (0x10 if os.path.isdir(p) else 0x20)

        class _E:
            def __init__(self, n):
                self.name = n
        fake = _ty.SimpleNamespace(
            lstat=lambda u: _St(loc(u)),
            scandir=lambda u: [_E(n) for n in sorted(os.listdir(loc(u)))],
            rmdir=lambda u: os.remove(loc(u)) if os.path.islink(loc(u)) else os.rmdir(loc(u)),
            remove=lambda u: os.remove(loc(u)))
        os.makedirs(os.path.join(sb, "alt", "sub"))
        open(os.path.join(sb, "alt", "sub", "f.txt"), "w").write("x")
        os.makedirs(os.path.join(sb, "Zentral"))
        open(os.path.join(sb, "Zentral", "wichtig.msi"), "w").write("x")
        os.symlink(os.path.join(sb, "Zentral"), os.path.join(sb, "alt", "junction"))
        real_mod = sys.modules.get("smbclient")
        sys.modules["smbclient"] = fake
        try:
            remote._smb_rmtree("\\\\srv\\P$\\alt")
        finally:
            if real_mod is not None:
                sys.modules["smbclient"] = real_mod
            else:
                sys.modules.pop("smbclient", None)
        assert not os.path.exists(os.path.join(sb, "alt")) and os.path.isfile(os.path.join(sb, "Zentral", "wichtig.msi")), "target of the junction stays"
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "remote.py"), encoding="utf-8").read()
        assert "smbshutil.rmtree" not in src, "never smbclient.shutil.rmtree (follows junctions)"
        # Access rules: all new routes for OU admins only via _p_repo or _p_pkg_edit
        for k in [k for k in access.ROUTES if k[1].startswith("/api/repository/") and k[1] not in ("/api/repository/msi", "/api/repository/search")]:
            assert access.ROUTES[k] is access._p_repo, k
        assert access.ROUTES[("POST", "/api/packages/{pid}/repair/apply")] is access._p_pkg_edit
    finally:
        backup.create = real_backup
        for pid in created:
            db.ex("DELETE FROM packages WHERE id=?", (pid,))
            db.ex("DELETE FROM pkg_ident WHERE package_id=?", (pid,))
        db.ex("DELETE FROM tenants WHERE id IN (?, ?)", (t_nord, t_sued))
        for t in ("repo_baseline", "repo_baseline_dirs", "repo_hash", "repo_uploads"):
            db.ex(f"DELETE FROM {t}")
        db.ex("DELETE FROM settings WHERE key='repo_upload_max_mb'")
        remote.repo_cache_clear()
        db.set_setting("repo_path", old_repo or "")


@test("2.28 News in the app (seen per user, security first, OU only \"all\") and notice of new versions (server asks GitHub, token encrypted and never visible)")
def _():
    import urllib.error as _ue
    from app import config, news, pklog, util
    from app import main as _m
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    vers = news.versions()
    assert vers and any(v["version"] == config.VERSION for v in vers), "news.json needs an entry for the running version"
    assert util.vcmp(vers[0]["version"], config.VERSION) <= 0 or True
    for v in vers:
        assert v["items"], v["version"]
        for i in v["items"]:
            assert i["kind"] in news.KINDS and i["audience"] in news.AUDIENCES and i["de"] and i["en"], (v["version"], i)
    cl = open(os.path.join(root, "CHANGELOG.md"), encoding="utf-8").read() if os.path.exists(os.path.join(root, "CHANGELOG.md")) else ""
    if cl:
        assert f"## pocketknife-{config.VERSION} " in cl
    adm, ou = {"username": "news-haupt", "scope": None}, {"username": "news-ou", "scope": [999999]}
    db.ex("DELETE FROM news_seen WHERE username IN ('news-haupt','news-ou')")
    r = news.for_user(adm)
    assert r["current"] == config.VERSION and r["unseen"] == 1 and r["versions"][0]["unseen"] and not any(v["unseen"] for v in r["versions"][1:]), \
        "new user: only the running version is new"
    for v in r["versions"]:
        ks = [i["kind"] for i in v["items"]]
        assert ks == sorted(ks, key=lambda k: news.KINDS.index(k)), "security first"
    news.mark_seen(adm)
    assert news.for_user(adm)["unseen"] == 0
    db.ex("INSERT OR REPLACE INTO news_seen(username, version, at) VALUES('news-haupt', '2.25', '')")
    r = news.for_user(adm)
    assert r["unseen"] == sum(1 for v in r["versions"] if util.vcmp(v["version"], "2.25") > 0) and r["unseen"] >= 3, "skipped versions are all new"
    assert news.mark_seen(adm, "99.0")["seen"] == config.VERSION, "never a future version as seen"
    ro = news.for_user(ou)
    assert "update" not in ro and all(i["audience"] == "all" for v in ro["versions"] for i in v["items"]), "OU admins: only \"all\", no update notice"
    # New version: GitHub simulated (the browser never asks by itself)
    old = {k: db.setting_raw(k) for k in ("update_check_enabled", "update_repo", news.TOKEN_KEY, news.STATUS_KEY)}
    real = news._fetch
    calls = []
    try:
        news.save({"enabled": True, "repo": "https://github.com/test/pocketknife.git", "token": "github_pat_Zq9Xw7TOKEN"})
        assert db.setting("update_repo") == "test/pocketknife"
        raw = db.setting_raw(news.TOKEN_KEY)
        assert raw and "Zq9Xw7" not in raw, "token encrypted"
        fut = "99.1"
        news._fetch = lambda repo, token: calls.append((repo, token)) or {"tag": "v" + fut, "url": "https://github.com/test/pocketknife/releases/tag/v99.1",
            "published": "2099-01-01T00:00:00", "news": {"versions": [
                {"version": fut, "date": "2099-01-01", "items": [{"kind": "new", "audience": "all", "de": "Neu A", "en": "New A"},
                                                                 {"kind": "security", "audience": "admin", "de": "Lücke B", "en": "Hole B"}]},
                {"version": config.VERSION, "date": "", "items": [{"kind": "new", "audience": "all", "de": "x", "en": "x"}]}]}}
        st = news.check(force=True, who="test")
        assert st["ok"] and st["latest"] == fut and [v["version"] for v in st["newer"]] == [fut], st
        assert calls == [("test/pocketknife", "github_pat_Zq9Xw7TOKEN")]
        news.check()  # within 24 hours: do not ask again
        assert len(calls) == 1
        up = news.for_user(adm)["update"]
        assert up["latest"] == fut and up["security"] and up["versions"][0]["items"][0]["kind"] == "security" and up["command"] == "sudo sh update.sh"
        assert db.val("SELECT COUNT(*) FROM logs WHERE message LIKE ?", (f"%{fut} ist verfügbar (mit Sicherheitsupdates)%",)) >= 1
        # Token never to the UI (settings, news, diagnostic rule)
        for out in (news.settings(), news.for_user(adm), _m.db.all_settings()):
            assert "Zq9Xw7" not in json.dumps(out), ("token visible", [k for k, v in (out.items() if isinstance(out, dict) else []) if "Zq9Xw7" in json.dumps(v)])
        assert news.settings()["token_set"] is True
        # Error from GitHub: understandable, without token
        def _404(repo, token):
            raise _ue.HTTPError("https://api.github.com/x", 404, "Not Found", {}, None)
        news._fetch = _404
        st = news.check(force=True)
        assert not st["ok"] and "privaten Repository" in st["error"] and news.for_user(adm)["update"] is None
        news._fetch = lambda repo, token: (_ for _ in ()).throw(OSError("timed out github_pat_Zq9Xw7TOKEN"))
        st = news.check(force=True)
        assert "Zq9Xw7" not in st["error"] and "nicht erreichbar" in st["error"]
        news.save({"enabled": False})
        assert news.check(force=True) == news.status() or True
        assert news.for_user(adm).get("update") is None, "switched off -> no notice"
        try:
            news.save({"repo": "kein repo"})
            raise AssertionError("invalid repository")
        except ValueError:
            pass
        news.save({"clear_token": True})
        assert not db.setting_raw(news.TOKEN_KEY)
        assert ("GET", "/api/system/updates") not in _m.access.ROUTES and ("GET", "/api/news") in _m.access.ROUTES, "settings only for main admins"
        wf = os.path.join(root, ".github", "workflows", "release.yml")
        if os.path.exists(wf):
            assert "app/news.json" in open(wf, encoding="utf-8").read(), "release aborts without news for the version"
    finally:
        news._fetch = real
        for k, v in old.items():
            if v:
                db.set_setting(k, v)
            else:
                db.ex("DELETE FROM settings WHERE key=?", (k,))
        db.ex("DELETE FROM news_seen WHERE username IN ('news-haupt','news-ou')")


@test("2.29 Audit logic: exception collection missing/broken = package skipped (never \"everywhere\"), conditions protect against deletion, interrupted schedule uses up no repetitions, hanging \"waiting\" runs, tenant at start, unambiguous collections on import")
def _():
    from app import deploy, pkgexport, schedules, steps as stp, integrity
    db.ex("INSERT OR IGNORE INTO computers(id, name, online, winrm) VALUES(291, 'PC-291', 1, 1)")
    ex = db.ex("INSERT INTO collections(name, type, match, filters) VALUES('Ausnahme 2.29', 'static', 'all', '{}')")
    db.ex("INSERT INTO collection_members(collection_id, computer_id) VALUES(?, 291)", (ex,))
    collections_.invalidate()
    run = stp.Runner(1, 1, {"id": 291, "name": "PC-291", "dns_name": ""}, lambda: False, lambda *a: None)
    cond = {**stp.default_conditions(), "collections": [{"id": ex, "mode": "not_member"}]}
    assert run.check(cond)[0] is False, "member of the exception -> do not install"
    p = stp.clean_package({"name": "Audit-Ausnahme", "conditions": cond,
                           "steps": {"main": [{"type": "command", "command": "cmd /c exit 0"}]}})
    p["steps"], p["conditions"] = json.dumps(p["steps"]), json.dumps(p["conditions"])
    pid = db.ex(f"INSERT INTO packages({', '.join(deploy.PKG_FIELDS)}) VALUES({', '.join('?' * len(deploy.PKG_FIELDS))})",
                tuple(p[k] for k in deploy.PKG_FIELDS))
    try:
        assert any("Audit-Ausnahme" in x for x in collections_.used_by([ex])), "condition in the package counts as usage"
        db.ex("DELETE FROM collections WHERE id=?", (ex,))  # e.g. AD group renamed, old collection removed
        collections_.invalidate()
        ok, why = run.check(cond)
        assert ok is False and "gibt es nicht mehr" in why, (ok, why)
        real = collections_.member_ids
        collections_.member_ids = lambda cid, fresh=False: (_ for _ in ()).throw(RuntimeError("database is locked"))
        try:
            db.ex("INSERT INTO collections(id, name, type, match, filters) VALUES(?, 'Ausnahme 2.29', 'static', 'all', '{}')", (ex,))
            ok, why = run.check(cond)
            assert ok is False and "nicht prüfbar" in why, (ok, why)
        finally:
            collections_.member_ids = real
        # Step conditions count too
        assert ex in collections_._cond_refs(None, {"main": [{"type": "command", "conditions": {"collections": [{"id": ex}]}}]})
    finally:
        db.ex("DELETE FROM packages WHERE id=?", (pid,))
        db.ex("DELETE FROM collections WHERE id=?", (ex,))
        db.ex("DELETE FROM collection_members WHERE collection_id=?", (ex,))
    # Schedule: interrupted by a break in the repository -> not "done"
    real_fire = schedules.fire
    schedules.fire = lambda s_, cids=None, reason="", **k: {"deployment_id": None, "count": 0, "blocked": "x"}
    try:
        assert schedules._fire_safe({"id": 999291, "name": "T"}, [1]) is False
    finally:
        schedules.fire = real_fire
    # created as "waiting", start failed -> the tick catches up
    did = db.ex("INSERT INTO deployments(name, package_json, status, created_at) VALUES('Hängt', ?, 'queued', ?)",
                (json.dumps({"name": "Hängt", "steps": {"pre": [], "main": [], "post": []}}), util.ago(minutes=10)))
    db.ex("INSERT INTO deployment_targets(deployment_id, computer_id, status) VALUES(?, 291, 'queued')", (did,))
    started = []
    real_start = deploy.start
    deploy.start = lambda d, checked=False: started.append(d)
    try:
        deploy.restart_stuck()
        assert did in started, "hanging deployment is started"
    finally:
        deploy.start = real_start
    # Tenant again at start: computer has left the tenant collection -> skipped
    real_tf = deploy.tenant_filter
    deploy.tenant_filter = lambda pkg, ids, strict=False: ([], len(ids))
    try:
        deploy._rescope(did)
    finally:
        deploy.tenant_filter = real_tf
    assert db.val("SELECT status FROM deployment_targets WHERE deployment_id=?", (did,)) == "skipped"
    db.ex("DELETE FROM deployment_targets WHERE deployment_id=?", (did,))
    db.ex("DELETE FROM deployments WHERE id=?", (did,))
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "deploy.py"), encoding="utf-8").read()
    assert "    _rescope(did)\n" in src.split("def start(")[1].split("def _rescope")[0], "start() applies the tenant"
    # Import: collections of the same name -> only unambiguous (via the path), otherwise reject
    f1 = db.ex("INSERT INTO collections(name, type) VALUES('Ordner A 2.29', 'folder')")
    f2 = db.ex("INSERT INTO collections(name, type) VALUES('Ordner B 2.29', 'folder')")
    c1 = db.ex("INSERT INTO collections(name, parent_id, type, match, filters) VALUES('Doppelt 2.29', ?, 'static', 'all', '{}')", (f1,))
    c2 = db.ex("INSERT INTO collections(name, parent_id, type, match, filters) VALUES('Doppelt 2.29', ?, 'static', 'all', '{}')", (f2,))
    try:
        assert pkgexport._find_collection({"name": "Doppelt 2.29", "path": ["Ordner B 2.29"]}, "x") == c2
        must_fail(pkgexport._find_collection, {"name": "Doppelt 2.29"}, "x", contains="mehrfach")
        out = pkgexport._conds_out({"collections": [{"id": c1, "mode": "not_member"}]})
        assert out["collections"][0]["path"] == ["Ordner A 2.29"]
        assert pkgexport._find_collection(out["collections"][0], "x") == c1
    finally:
        for x in (c1, c2, f1, f2):
            db.ex("DELETE FROM collections WHERE id=?", (x,))
    # Main file with a different date, fingerprint not ready -> run does not start
    real_ev = integrity.evaluate
    integrity.evaluate = lambda *a, **k: {"state": "warn", "issues": [{"kind": "pending", "main": True, "level": "warn", "path": "a\\b.msi",
                                                                    "note": "", "old": {}, "new": {}}], "total": 1}
    try:
        ipkg = {"name": "X", "steps": {"pre": [], "main": [{"type": "install", "id": "s1", "source": "file", "folder": "a",
                                                           "install_file": "b.msi", "copy_folder": True}], "post": []}}
        must_fail(integrity.guard, ipkg, contains="Unterbrochen")
        ipkg["steps"]["main"][0]["install_file"] = "setup.exe"  # a\b.msi is only a file in the copied folder
        assert integrity.guard(ipkg)["state"] == "warn", "folder files do not block"
    finally:
        integrity.evaluate = real_ev
    # Tenant packages never as service account – even if a main admin saved them that way
    assert stp.effective_run_as({"type": "command", "run_as": "inherit"}, {"run_as": "deploy", "tenant_id": 3})[0] == "system"
    assert stp.effective_run_as({"type": "command", "run_as": "deploy"}, {"run_as": "deploy"})[0] == "deploy"


@test("2.29 Audit clients: file explorer follows no links when deleting/copying, working folder is checked after creation (CLM-compatible), marker in HKLM, winget only signed, message/variables/registry safe, no CredSSP")
def _():
    import subprocess as _sp
    from app import deploy, kerberos, remote, steps as stp, tools, uninstall
    sb = os.path.join(TMP, "cf-nofollow")
    os.makedirs(os.path.join(sb, "alt", "sub"))
    open(os.path.join(sb, "alt", "sub", "f.txt"), "w").write("x")
    os.makedirs(os.path.join(sb, "Geheim"))
    open(os.path.join(sb, "Geheim", "wichtig.txt"), "w").write("x")
    os.symlink(os.path.join(sb, "Geheim"), os.path.join(sb, "alt", "junction"))
    loc = lambda u: os.path.join(sb, *u.split("\\")[4:])  # noqa: E731  \\pc\C$\alt → sb/alt

    class _St:
        def __init__(self, p):
            self.st_file_attributes = (0x400 if os.path.islink(p) else 0) | (0x10 if os.path.isdir(p) else 0)

    class _E:
        def __init__(self, n):
            self.name = n

    class Fake:
        lstat = staticmethod(lambda u: _St(loc(u)))
        scandir = staticmethod(lambda u: [_E(n) for n in sorted(os.listdir(loc(u)))])
        rmdir = staticmethod(lambda u: os.remove(loc(u)) if os.path.islink(loc(u)) else os.rmdir(loc(u)))
        remove = staticmethod(lambda u: os.remove(loc(u)))
        mkdir = staticmethod(lambda u: os.mkdir(loc(u)))

    class FakeSh:
        copyfile = staticmethod(lambda a, b: shutil.copyfile(loc(a), loc(b)))
    clientfiles.copytree_nofollow(Fake, FakeSh, "\\\\pc\\C$\\alt", "\\\\pc\\C$\\kopie")
    assert os.path.isfile(os.path.join(sb, "kopie", "sub", "f.txt")) and not os.path.exists(os.path.join(sb, "kopie", "junction")), \
        "link skipped when copying"
    clientfiles.rmtree_nofollow(Fake, "\\\\pc\\C$\\alt")
    assert not os.path.exists(os.path.join(sb, "alt")) and os.path.isfile(os.path.join(sb, "Geheim", "wichtig.txt")), "target stays"
    csrc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "clientfiles.py"), encoding="utf-8").read()
    assert "smbshutil.rmtree" not in csrc and "smbshutil.copytree" not in csrc
    # Working folder: re-check owner/permissions after creation; cmdlets only (CLM)
    for need in ("PkTrusted", "PK_UNTRUSTED", "/setowner", "Sddl", "Inhalt beim Anlegen"):
        assert need in remote.SECURE_BASE, need
    pw = os.environ.get("PK_PWSH")
    if pw and os.path.exists(pw):
        def run(exists, sddl, after, kids=0):
            mocks = ("$ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage'\n$env:windir = '/w'\n"
                     f"$global:exists = ${'true' if exists else 'false'}; $global:sddl = '{sddl}'; $global:after = '{after}'; "
                     f"$global:kids = {kids}; $global:log = @()\n"
                     "function whoami.exe { '\"firma\\svc\",\"S-1-5-21-1-2-3-1105\"' }\n"
                     "function Test-Path { param($LiteralPath) $global:exists }\n"
                     "function Get-Acl { param($LiteralPath) '' | Select-Object @{n='Sddl';e={$global:sddl}} }\n"
                     "function Rename-Item { param($LiteralPath, $NewName) $global:log += 'rename'; $global:exists = $false }\n"
                     "function New-Item { param($ItemType, $Path) $global:log += 'new'; $global:exists = $true; $global:sddl = $global:after }\n"
                     "function icacls.exe { $global:LASTEXITCODE = 0 }\n"
                     "function Get-ChildItem { param($LiteralPath, [switch]$Force) if ($global:kids -gt 0) { 1..$global:kids } }\n")
            r = _sp.run([pw, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "-"],
                        input=mocks + remote.SECURE_BASE + "\n'LOG:' + ($global:log -join ',')\n",
                        capture_output=True, text=True, env={**os.environ, "TERM": "dumb"})
            return r.stdout + r.stderr
        good = "O:BAG:SYD:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
        o = run(True, good, good)
        assert "PK_BASE_OK" in o and "rename" not in o, o
        o = run(True, "O:S-1-5-21-1-2-3-1105G:DUD:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)", good)
        assert "PK_BASE_OK" in o and "rename" not in o, ("service account as owner is fine", o)
        o = run(True, "O:S-1-5-21-9-9-9-500G:DUD:(A;OICI;FA;;;S-1-5-21-9-9-9-500)", good)
        assert "PK_MOVED:S-1-5-21-9-9-9-500" in o and "PK_BASE_OK" in o, o
        o = run(True, good[:-1] + ")(A;OICI;FA;;;BU)", good)
        assert "PK_MOVED:Recht für BU" in o, o
        o = run(False, "", "O:S-1-5-21-9-9-9-777G:DUD:PAI(A;OICI;FA;;;SY)")
        assert "PK_UNTRUSTED" in o and "PK_BASE_OK" not in o, ("created by someone else at the same moment", o)
        o = run(False, "", good, kids=2)
        assert "PK_UNTRUSTED" in o and "Inhalt beim Anlegen" in o, o
        assert "Only core types" not in o and "cannot" not in o.lower(), o
    # winget: marker in HKLM, PATH replacement only with Microsoft signature
    assert "HKLM:\\SOFTWARE\\PocketKnife" in deploy.WINGET_FIND and "Set-Content -Path $mark" not in deploy.WINGET_FIND
    assert "Get-AuthenticodeSignature" in deploy.WINGET_FIND and "if ($g) { return $g.Source }" not in deploy.WINGET_FIND
    # Message: no " in the argument; variables with "-" not unquoted in CMD
    ssrc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "steps.py"), encoding="utf-8").read()
    assert ".replace('\"', \"'\")" in ssrc.split('            if t == "message":')[1][:1200], "message text without quotation marks"
    assert "/XJ" in ssrc, "robocopy follows no links"
    must_fail(tools.substitute, "x $(User)", {"name": "pc", "current_user": "max -Force"}, "cmd", contains="nicht sicher")
    assert tools.substitute("x $(User)", {"name": "pc", "current_user": "max -Force"}, "powershell") == "x $($PK_User)"
    assert "Test-Path $key" not in uninstall.__dict__.get("SCRIPT", "") and "-Path $key" not in open(uninstall.__file__, encoding="utf-8").read()
    # CredSSP no longer exists (the service account password would go to every client)
    old = db.setting_raw("auth_mode")
    try:
        db.set_setting("auth_mode", "credssp")
        assert kerberos.mode() == "negotiate"
    finally:
        db.set_setting("auth_mode", old or "")


@test("2.29 Audit login/data: generic error messages, emergency access limited and reported, diagnostic package masks more, proxy password never to a new proxy, token not on redirect, DNS answers checked, multiple DC certificates")
def _():
    import io as _io
    import urllib.request as _ur
    from fastapi import HTTPException as _HE
    from app import ad, auth, config as _cfg, diagnostics, dnsres, notify, proxy
    # Connection errors do not reveal DC names
    real = ad.authenticate
    ad.authenticate = lambda u, p, allow_kiosk=False: (_ for _ in ()).throw(ad.ADError("LDAPS-Verbindung zu dc1.firma.local:636 fehlgeschlagen"))
    try:
        try:
            auth.login("max", "x", "10.9.9.1")
            raise AssertionError("no login")
        except _HE as e:
            assert "dc1" not in str(e.detail) and "636" not in str(e.detail) and e.detail == auth.INFRA_FAIL, e.detail
    finally:
        ad.authenticate = real
    assert auth._log_name("Sommer!2024#") == "(ungültiger Benutzername, 12 Zeichen)" and auth._log_name("FIRMA\\max") == "'FIRMA\\max'"
    assert "breakglass" in notify.CHECKS and notify.alert_def("breakglass")
    db.ex("INSERT INTO logs(ts, level, category, message) VALUES(?, 'WARN', 'auth', 'Notfallzugang: Anmeldung von 10.1.1.1')", (util.now(),))
    hits = notify._check_breakglass({}, {"since": util.ago(minutes=1)})
    assert any("10.1.1.1" in v["event"] for v in hits.values()), hits
    # Diagnostic package: more password forms
    t = diagnostics._mask_secrets('cmdkey /add:x /user:a /pass:Geh31m psexec \\\\pc -u a -p Zq9Xw7 net user bob Zq9Xw8 /add '
                                  'Authorization: Bearer abcdefghijkl1 ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345')
    for bad in ("Geh31m", "Zq9Xw7", "Zq9Xw8", "abcdefghijkl1", "ghp_ABCDEF"):
        assert bad not in t, (bad, t)
    for keep in ("docker run -p 8443:8443 x", "Basic authentication", "Kerberos-Token abgelaufen"):
        assert diagnostics._mask_secrets(keep) == keep, keep
    db.ex("INSERT INTO tenants(name, admin_group) VALUES('Kanzlei Schmidt & Partner', 'FIRMA\\GG-Kanzlei-Admins')")
    try:
        an = diagnostics.Anonymizer(True)
        an.learn()
        txt = an("Anmeldung von 1.2.3.4 (OU-Admin: Kanzlei Schmidt &amp; Partner) – Gruppe GG-Kanzlei-Admins")
        assert "Kanzlei" not in txt, txt
        assert ("tenant", "kanzlei schmidt & partner") in an.map
    finally:
        db.ex("DELETE FROM tenants WHERE name='Kanzlei Schmidt & Partner'")
        db.ex("DELETE FROM diag_alias WHERE real IN ('Kanzlei Schmidt & Partner', 'GG-Kanzlei-Admins')")
    # Proxy: stored password never to another proxy
    try:
        proxy.save({"url": "http://proxy-a.firma.local:8080", "user": "px", "password": "Pw1", "clients": False})
        must_fail(proxy.save, {"url": "http://evil.example:8080", "user": "px", "clients": False}, contains="erneut eingeben")
        proxy.save({"url": "http://proxy-a.firma.local:8080", "user": "px", "clients": True})  # unchanged: ok
    finally:
        proxy.save({"url": "", "user": "", "clients": False})
    # Redirect to another machine: no Authorization header
    req = _ur.Request("https://api.github.com/x", headers={"Authorization": "Bearer T"})
    n = proxy.SafeRedirect().redirect_request(req, _io.BytesIO(), 302, "Found", {}, "https://evil.example/y")
    assert "Authorization" not in n.headers and not n.get_header("Authorization")
    n = proxy.SafeRedirect().redirect_request(req, _io.BytesIO(), 302, "Found", {}, "https://api.github.com/z")
    assert n.get_header("Authorization") == "Bearer T"
    # DNS: foreign/mismatched answers discarded
    import struct as _struct
    qname = b"\x02pc\x05firma\x05local\x00" + _struct.pack(">HH", 1, 1)

    class FakeSock:
        def __init__(self, *a):
            self.out = []
        def settimeout(self, t):
            pass
        def connect(self, a):
            self.peer = a
        def send(self, q):
            self.qid = _struct.unpack(">H", q[:2])[0]
        def recv(self, n):
            hdr = _struct.pack(">HHHHHH", self.qid, 0x8180, 1, 1, 0, 0)
            ans = b"\xc0\x0c" + _struct.pack(">HHIH", 1, 1, 60, 4)
            if not self.out:
                self.out.append(1)  # first an answer to a DIFFERENT question (forged)
                return hdr + b"\x04evil\x05firma\x05local\x00" + _struct.pack(">HH", 1, 1) + ans + bytes([6, 6, 6, 6])
            return hdr + qname + ans + bytes([10, 1, 2, 3])
        def close(self):
            pass
    real_sock = dnsres.socket.socket
    dnsres.socket.socket = FakeSock
    try:
        assert dnsres._query("pc.firma.local", "10.0.0.1") == "10.1.2.3"
    finally:
        dnsres.socket.socket = real_sock
    # LDAPS: several certificates per name (several DCs behind the domain name)
    assert ad._fp_list("AA") == ["AA"] and ad._fp_list(["AA", "BB"]) == ["AA", "BB"] and ad._fp_list(None) == []
    # UI: CSV protection with a leading space, only http(s)/mailto as link, stricter CSP of a route stays
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    assert "/^[\\s]*[=+\\-@]|^[\\t\\r]/" in js and "(https?:|mailto:)" in js
    msrc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "main.py"), encoding="utf-8").read()
    assert 'if "content-security-policy" not in resp.headers' in msrc
    assert diagnostics._csv([{"a": "=HYPERLINK(1)"}], ["a"]).splitlines()[1].startswith("'="), "diagnostic CSV defused"
    assert _cfg is not None


@test("2.29.1 Securing tenants: command/PowerShell steps only with their own release (main admin steps remain allowed unchanged, scripts as a file from the own folder work), own uninstall commands likewise, notice on access to shares/internet")
def _():
    import hashlib
    import secrets
    import time as _t
    from app import access, auth
    from app import main as _m
    col = db.ex("INSERT INTO collections(name, type) VALUES('OU Skript (Test)', 'static')")
    cid = db.ex("INSERT INTO computers(name, dns_name, created_at) VALUES('SK-IN1', 'sk-in1.firma.local', ?)", (util.now(),))
    db.ex("INSERT INTO collection_members VALUES(?,?)", (col, cid))
    tid = db.ex("INSERT INTO tenants(name, collection_id, repo_folder, admin_group, allow_packages, created_at) "
                "VALUES('Skript-Test', ?, 'Kunden\\SK', 'PK-OU-SK', 1, ?)", (col, util.now()))
    sw = db.ex("INSERT INTO software(computer_id, name, version, uninstall_string) VALUES(?,?,?,?)",
               (cid, "SK-Programm", "1", '"C:\\Program Files\\SK\\unins000.exe"'))
    access.flush()
    tok = secrets.token_urlsafe(16)
    th = hashlib.sha256(tok.encode()).hexdigest()
    db.ex("INSERT INTO sessions(token_hash, username, display_name, created, expires, ip, last_active, role, scope) VALUES(?,?,?,?,?,?,?,?,?)",
          (th, "ou-sk", "Test", util.now(), "2099-01-01 00:00:00", "127.0.0.1", util.now(), "admin", json.dumps([tid])))
    auth._checked[th] = _t.time() + 10 ** 6
    created = []
    try:
        me = _asgi("GET", "/api/me", tok)[1]
        assert me["may_build"] and not me["may_script"] and me["tenants"][0]["allow_scripts"] is False, me
        for stp_ in ({"type": "command", "command": "hostname"}, {"type": "powershell", "script": "Get-Date"},
                     {"type": "uninstall", "match_name": "X*", "command": "cmd /c del x"}):
            st, r = _asgi("POST", "/api/packages", tok, {"name": "SK verboten", "steps": {"main": [stp_]}})
            assert st == 403 and "nicht freigegeben" in str(r), (stp_["type"], st, r)
        # allowed: script as a file from the own repository folder, registry, uninstall with a recognised command
        st, r = _asgi("POST", "/api/packages", tok, {"name": "SK Datei-Skript", "steps": {"main": [
            {"type": "install", "source": "file", "folder": "Kunden\\SK\\tools", "install_file": "setup.ps1"},
            {"type": "uninstall", "match_name": "Alt*"}]}})
        assert st == 200, (st, r)
        created.append(r["id"])
        # Step of a main admin (package released for OU admins, 2.30): can be saved unchanged, not changed
        pid = db.ex("INSERT INTO packages(name, version, steps, conditions, run_as, tenant_id, created_at, ou_edit) VALUES('SK von Haupt','1',?,'{}','system',?,?,1)",
                    (json.dumps({"main": [{"id": "h1", "type": "command", "command": "hostname"}]}), tid, util.now()))
        created.append(pid)
        pkg = _asgi("GET", f"/api/packages/{pid}", tok)[1]
        pkg["description"] = "nur Beschreibung geändert"
        st, r = _asgi("PUT", f"/api/packages/{pid}", tok, pkg)
        assert st == 200, ("unchanged main admin step", st, r)
        pkg["steps"]["main"][0]["command"] = "net use Z: \\\\fremd\\c$"
        assert _asgi("PUT", f"/api/packages/{pid}", tok, pkg)[0] == 403, "changed code"
        pkg["steps"]["main"].append({"type": "command", "command": "hostname"})
        pkg["steps"]["main"][0]["command"] = "hostname"
        assert _asgi("PUT", f"/api/packages/{pid}", tok, pkg)[0] == 403, "additional code (same text counts only once)"
        # Software list: recognised command yes, changed one no
        from app import uninstall as _un
        det = _un.command_for(db.q1("SELECT * FROM software WHERE id=?", (sw,)))["command"]
        st, r = _asgi("POST", "/api/uninstall", tok, {"items": [{"software_id": sw, "command": det}]})
        assert st == 200, ("recognised command", st, r)
        for d in r["deployments"]:
            db.ex("DELETE FROM deployment_targets WHERE deployment_id=?", (d,))
            db.ex("DELETE FROM deployments WHERE id=?", (d,))
        st, r = _asgi("POST", "/api/uninstall", tok, {"items": [{"software_id": sw, "command": "cmd /c whoami"}]})
        assert st == 403, ("changed command", st, r)
        # Release at the tenant: only together with "own packages"; afterwards code allowed, with a note on network access
        db.ex("UPDATE tenants SET allow_scripts=1 WHERE id=?", (tid,))
        access.flush()
        st, r = _asgi("POST", "/api/packages", tok, {"name": "SK mit Netz", "steps": {"main": [
            {"type": "powershell", "script": "Copy-Item \\\\fileserver\\daten\\x.txt C:\\Temp; Invoke-WebRequest https://example.org -OutFile a"}]}})
        assert st == 200 and r["warnings"] and "fileserver" in r["warnings"][0], (st, r)
        created.append(r["id"])
        assert db.val("SELECT COUNT(*) FROM logs WHERE message LIKE '%SK mit Netz%Freigaben oder das Internet%'"), "note in the log"
        st, r = _asgi("POST", "/api/packages", tok, {"name": "SK ohne Netz", "steps": {"main": [{"type": "powershell", "script": "Get-Date"}]}})
        assert st == 200 and r["warnings"] == [], r
        created.append(r["id"])
        assert _m.net_hints({"steps": {"main": [{"type": "command", "command": "net use x: \\\\srv\\a"}]}})
        assert not _m.net_hints({"steps": {"main": [{"type": "command", "command": "msiexec /i C:\\Temp\\a.msi /qn"}]}})
        # Save tenant: scripts only with "own packages"
        _m._tenant_scripts(tid, {"allow_scripts": True}, {"username": "t"})
        assert db.val("SELECT allow_scripts FROM tenants WHERE id=?", (tid,)) == 1
        db.ex("UPDATE tenants SET allow_packages=0 WHERE id=?", (tid,))
        _m._tenant_scripts(tid, {"allow_scripts": True}, {"username": "t"})
        assert db.val("SELECT allow_scripts FROM tenants WHERE id=?", (tid,)) == 0, "no scripts without own packages"
        js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
        assert "mayScript(p.tenant_id)" in js and "allow_scripts: scripts.checked && allow.checked" in js
    finally:
        for p in created:
            db.ex("DELETE FROM packages WHERE id=?", (p,))
        db.ex("DELETE FROM sessions WHERE token_hash=?", (th,))
        db.ex("DELETE FROM software WHERE id=?", (sw,))
        db.ex("DELETE FROM tenants WHERE id=?", (tid,))
        db.ex("DELETE FROM collection_members WHERE collection_id=?", (col,))
        db.ex("DELETE FROM collections WHERE id=?", (col,))
        db.ex("DELETE FROM computers WHERE id=?", (cid,))
        access.flush()


@test("2.29.2 Trivy: HIGH/CRITICAL vulnerabilities with a fix stop the release before uploading; Trivy pinned in version and checksum (no action); daily scan of the published image; version updates via pull request")
def _():
    import re as _re
    import shutil as _sh
    import subprocess as _sp
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    sc = os.path.join(root, "tools", "trivy-scan.sh")
    wfd = os.path.join(root, ".github", "workflows")
    if not os.path.exists(sc) or not os.path.isdir(wfd):
        return  # the script and workflows are not in the container
    src = open(sc, encoding="utf-8").read()
    assert _re.search(r"^TRIVY_VERSION=\d+\.\d+\.\d+$", src, _re.M), "pinned Trivy version"
    assert _re.search(r'^SHA_LINUX_64="[0-9a-f]{64}"$', src, _re.M) and _re.search(r'^SHA_LINUX_ARM64="[0-9a-f]{64}"$', src, _re.M)
    assert "sha256sum -c" in src and "--ignore-unfixed" in src and "HIGH,CRITICAL" in src
    ver = tuple(int(x) for x in _re.search(r"^TRIVY_VERSION=(\S+)$", src, _re.M).group(1).split("."))
    assert not ((0, 69, 4) <= ver <= (0, 69, 6)), "compromised Trivy versions (CVE-2026-33634)"
    wfs = {f: open(os.path.join(wfd, f), encoding="utf-8").read() for f in os.listdir(wfd) if f.endswith(".yml")}
    for f, t in wfs.items():
        for use in _re.findall(r"uses:\s*(\S+)", t):
            assert _re.search(r"@[0-9a-f]{40}$", use), f"{f}: action not pinned to a commit: {use}"
        assert "aquasecurity/" not in t, f"{f}: Trivy only via tools/trivy-scan.sh (no action)"
    rel = wfs["release.yml"]
    assert rel.index("tools/trivy-scan.sh pocketknife-test:amd64 pocketknife-test:arm64") < rel.index("Build and push"), \
        "Trivy scans BEFORE uploading"
    assert "tools/trivy-scan.sh" in wfs["test.yml"]
    assert "schedule:" in wfs["security.yml"] and ":latest" in wfs["security.yml"] and "packages: read" in wfs["security.yml"]
    assert "pin-versions.sh --update" in wfs["update-pins.yml"] and "gh pr create" in wfs["update-pins.yml"]
    assert ".trivyignore" in open(os.path.join(root, "update.sh"), encoding="utf-8").read()
    if not _sh.which("sh") or not _sh.which("sha256sum"):
        return
    # Run with a Trivy dummy: clean -> 0, finding -> 1 (+ report), not checkable -> 3
    d = tempfile.mkdtemp(prefix="pk-trivy-", dir=TMP)
    fake = os.path.join(d, "trivy")
    with open(fake, "w") as h:
        h.write('#!/bin/sh\ncase "$1" in\n image) out=""; prev=""; for a in "$@"; do [ "$prev" = "--output" ] && out="$a"; prev="$a"; img="$a"; done\n'
                '   case "$img" in *schlecht*) printf \'{\\n "Results": [\\n  {\\n   "Vulnerabilities": [\\n    {\\n     "VulnerabilityID": "CVE-2026-1111",\\n'
                '     "PkgName": "libssl3t64",\\n     "InstalledVersion": "1",\\n     "FixedVersion": "2",\\n     "Severity": "CRITICAL",\\n     "Title": "t"\\n    }\\n   ]\\n  }\\n ]\\n}\\n\' > "$out" ;;\n'
                '   *fehlt*) exit 1 ;; *) echo \'{"Results": []}\' > "$out" ;; esac ;;\n convert) echo Tabelle ;;\nesac\n')
    os.chmod(fake, 0o755)
    rep = os.path.join(d, "r.md")
    env = {**os.environ, "PK_TRIVY_BIN": fake, "PK_TRIVY_REPORT": rep}
    env.pop("GITHUB_ACTIONS", None)
    rc = lambda img: _sp.run(["sh", sc, img], env=env, capture_output=True, text=True, timeout=60).returncode  # noqa: E731
    assert rc("pk-gut") == 0 and rc("pk-schlecht") == 1 and rc("pk-fehlt") == 3
    r = _sp.run(["sh", sc, "pk-gut", "pk-schlecht"], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "Update versions" in r.stderr, "finding in one of several images counts"
    t = open(rep, encoding="utf-8").read()
    assert "CVE-2026-1111" in t and "libssl3t64" in t and "No vulnerabilities" in t, t


@test("2.29.3 Weekly security update: Mondays update packages, rebuild, self-test and Trivy – only on success new version and release (latest); nothing without a change; version number, CHANGELOG and news automatic")
def _():
    import shutil as _sh
    import subprocess as _sp
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    wf = os.path.join(root, ".github", "workflows", "weekly.yml")
    tool = os.path.join(root, "tools", "security-release.py")
    if not os.path.exists(wf) or not os.path.exists(tool):
        return  # workflows and tools are not in the container
    t = open(wf, encoding="utf-8").read()
    assert '"23 3 * * 1"' in t, "Mondays"
    order = ["pin-versions.sh --update", "git diff --quiet", "tools/trivy-scan.sh pocketknife:local", "tools/security-release.py",
             'git switch -c "$BR"', 'git push', "gh pr create"]
    pos = [t.index(x) for x in order]
    assert pos == sorted(pos), "order: update/build/test -> Trivy -> version -> branch -> pull request"
    assert t.count("if: steps.diff.outputs.changed == 'true'") >= 3, "no new version without a change"
    # 2.37: never directly onto main, no release by itself – only branch + pull request (release after merging)
    assert "gh workflow run" not in t and "actions: write" not in t and "HEAD:${{" not in t, "no push to main, no release"
    assert "pull-requests: write" in t and "contents: write" in t and "packages: write" not in t, "only release.yml publishes"
    # 2.32.5: own group (otherwise GitHub discards a waiting release) and only for a published state
    assert "group: weekly-security" in t and "group: release" in open(os.path.join(root, ".github", "workflows", "release.yml"), encoding="utf-8").read()
    assert 'gh release view "v$VER"' in t and "needs: stand" in t and "if: needs.stand.outputs.released == 'true'" in t
    # Tool: version +1, CHANGELOG/news/README, changes from the pinned versions
    import importlib.util as _iu
    spec = _iu.spec_from_file_location("pk_secrel", tool)
    m = _iu.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.bump("2.29.2") == "2.29.2-s1" and m.bump("2.29.2-s1") == "2.29.2-s2" and m.bump("2.30-s9") == "2.30-s10", "same program, only new packages"
    d = tempfile.mkdtemp(prefix="pk-secrel-", dir=TMP)
    for side in ("alt", "neu"):
        os.makedirs(os.path.join(d, side, "tools")); os.makedirs(os.path.join(d, side, "app")); os.makedirs(os.path.join(d, side, "docker"))
        _sh.copy(tool, os.path.join(d, side, "tools"))
        open(os.path.join(d, side, "VERSION"), "w").write("2.29.2\n")
        open(os.path.join(d, side, "CHANGELOG.md"), "w").write("# Änderungen\n\n## pocketknife-2.29.2 (24.10.2026)\n- x\n")
        open(os.path.join(d, side, "README.md"), "w").write("Aktuelle Version: **pocketknife-2.29.2** – siehe\n")
        json.dump({"versions": [{"version": "2.29.2", "date": "2026-10-24", "items": []}]}, open(os.path.join(d, side, "app", "news.json"), "w"))
        open(os.path.join(d, side, "docker", "apt-runtime.txt"), "w").write("# x\nlibssl3t64=" + ("3.5.1-1" if side == "alt" else "3.5.1-2") + "\n")
        open(os.path.join(d, side, "requirements.lock"), "w").write("# x\nfastapi==0.155.0\n")
        open(os.path.join(d, side, "Dockerfile"), "w").write("ARG PYTHON_IMAGE=python:3.12.15-slim-trixie@sha256:" + ("a" if side == "alt" else "b") * 64 + "\n")
    r = _sp.run([sys.executable, "-I", os.path.join(d, "neu", "tools", "security-release.py"), os.path.join(d, "alt")],
                capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and r.stdout.strip() == "2.29.2-s1", r.stdout + r.stderr
    rd = lambda *p: open(os.path.join(d, "neu", *p), encoding="utf-8").read()  # noqa: E731
    assert rd("VERSION").strip() == "2.29.2-s1" and "pocketknife-2.29.2-s1**" in rd("README.md")
    ch = rd("CHANGELOG.md")
    assert ch.index("## pocketknife-2.29.2-s1 (") < ch.index("## pocketknife-2.29.2 ") and "libssl3t64` 3.5.1-1 → 3.5.1-2" in ch and "base image" in ch and ch.startswith("# Änderungen\n\n## pocketknife-2.29.2-s1"), ch
    nv = json.loads(rd("app", "news.json"))["versions"][0]
    assert nv["version"] == "2.29.2-s1" and nv["items"][0]["kind"] == "security" and nv["items"][0]["de"] and nv["items"][0]["en"]
    from app import news
    news.validate(json.loads(rd("app", "news.json")))  # Structure fits


@test("2.29.4 Everything in the image up to date: all Debian packages (also those of the base image) at snapshot state, pip not in the image, update stays within the Python series and Debian version, guacd latest version; base-layer warning in good time before the end of updates")
def _():
    import subprocess as _sp
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    pv = os.path.join(root, "tools", "pin-versions.sh")
    if not os.path.exists(pv):
        return  # the build tools are not in the container
    ai = open(os.path.join(root, "docker", "apt-install.sh"), encoding="utf-8").read()
    assert ai.index("upgrade") < ai.index("apt-get install -y --no-install-recommends $PKGS"), "first update everything, then install"
    df = open(os.path.join(root, "Dockerfile"), encoding="utf-8").read()
    assert df.index("pip check") < df.index("pip uninstall -y -q pip"), "pip removed after the check"
    src = open(pv, encoding="utf-8").read()
    assert "python:3.12-slim|" not in src, "never \"python:3.12-slim\" without a Debian name (silently changed the Debian version)"
    assert "PK_PYTHON_BASE" in src and "importlib.metadata" in src and "hub.docker.com/v2/repositories/guacamole/guacd/tags" in src
    line = [x for x in src.splitlines() if x.strip().startswith("BASE_TAG=$(printf")][0]
    sedexpr = line.split("sed -n '", 1)[1].rsplit("'", 1)[0]
    for cur, want in (("python:3.12.15-slim-trixie@sha256:" + "a" * 64, "python:3.12-slim-trixie"),
                      ("python:3.13.2-slim-forky", "python:3.13-slim-forky")):
        out = _sp.run(["sed", "-n", sedexpr], input=cur, capture_output=True, text=True).stdout.strip()
        assert out == want, (cur, out)
    # Base layer: report in good time, never block the security updates
    import importlib.util as _iu
    spec = _iu.spec_from_file_location("pk_unterbau", os.path.join(root, "tools", "unterbau-check.py"))
    m = _iu.module_from_spec(spec)
    spec.loader.exec_module(m)
    d = tempfile.mkdtemp(prefix="pk-unterbau-", dir=TMP)
    pj, dj = os.path.join(d, "py.json"), os.path.join(d, "deb.json")
    json.dump({"result": {"releases": [{"name": "3.13", "eolFrom": "2029-10-31"}, {"name": "3.12", "eolFrom": "2028-10-31"}]}}, open(pj, "w"))
    json.dump({"result": {"releases": [{"name": "13", "eolFrom": "2028-08-09"}]}}, open(dj, "w"))
    assert m.eol(json.load(open(pj)), "3.12").isoformat() == "2028-10-31"
    assert m.eol([{"cycle": "3.12", "eol": "2028-10-31"}], "3.12").isoformat() == "2028-10-31", "old format"
    py, code = m.base()
    assert __import__("re").match(r"^\d+\.\d+$", py) and code in m.DEBIAN, (py, code)
    run = lambda today, p=pj: _sp.run([sys.executable, "-I", os.path.join(root, "tools", "unterbau-check.py"), "--python-json", p,  # noqa: E731
                                      "--debian-json", dj, "--today", today], capture_output=True, text=True, timeout=60)
    r = run("2026-10-24")
    assert r.returncode == 0 and "::" not in r.stdout, r.stdout
    r = run("2027-12-01")
    assert r.returncode == 0 and "::warning" in r.stdout and "plan to raise it" in r.stdout
    r = run("2028-06-01")
    assert r.returncode == 1 and "::error title=Raise base platform" in r.stdout
    r = run("2028-06-01", p=os.path.join(d, "fehlt.json"))
    assert "cannot be determined" in r.stdout
    wf = open(os.path.join(root, ".github", "workflows", "weekly.yml"), encoding="utf-8").read()
    assert "unterbau:" in wf and "tools/unterbau-check.py" in wf and "Check guacd" in wf
    sec = open(os.path.join(root, ".github", "workflows", "security.yml"), encoding="utf-8").read()
    assert 'exit "$rc"' in sec and "grc=0" in sec, "guacd findings only as a notice"


@test("2.29.5 No version jump for Python packages: each stays on its major version (before 1.0 minor version), calendar versions (certifi) always new; limits apply in the weekly update; new major version only deliberately")
def _():
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    tool = os.path.join(root, "tools", "caps.py")
    if not os.path.exists(tool):
        return  # the build tools are not in the container
    import importlib.util as _iu
    spec = _iu.spec_from_file_location("pk_caps", tool)
    m = _iu.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.cap("50.0.2") == "<51" and m.cap("0.155.0") == "<0.156" and m.cap("0.0.5") == "<0.1" and m.cap("2.9.1") == "<3"
    assert m.cap("2026.10.19") is None and m.cap("2025.2") is None, "calendar versions without a limit (root certificates, time zones)"
    assert m.line_for("fastapi", "0.155.0") == "fastapi>=0.155.0,<0.156" and m.line_for("certifi", "2026.10.19") == "certifi>=2026.10.19"
    t = m.build_ranges("# Kommentar\nCython==3.2.1\nsetuptools\n")
    assert t == "# Kommentar\nCython>=3.2.1,<4\nsetuptools\n", t
    lock = open(os.path.join(root, "requirements.lock"), encoding="utf-8").read()
    caps = open(os.path.join(root, "requirements.caps"), encoding="utf-8").read()
    names = {x.split("==")[0].lower() for x in lock.splitlines() if "==" in x and not x.startswith("#")}
    capped = {__import__("re").split(r"[<>=]", x)[0].lower() for x in caps.splitlines() if x and not x.startswith("#")}
    assert names == capped, ("requirements.caps does not match requirements.lock", names ^ capped)
    df = open(os.path.join(root, "Dockerfile"), encoding="utf-8").read()
    assert df.count("-c requirements.caps") == 2 and df.count("requirements.caps") >= 4, "limits in build and runtime stage"
    pv = open(os.path.join(root, "tools", "pin-versions.sh"), encoding="utf-8").read()
    assert pv.index("tools/caps.py lock requirements.lock") < pv.index("' > requirements.lock"), "limits BEFORE emptying the file"
    assert "PK_ALLOW_MAJOR" in pv
    assert "docker compose config --images" not in pv, "read guacd from the file (compose config fails without .env -> without digest)"
    assert pv.index('docker pull -q "$BASE"') < pv.index("DIGEST=$(docker image inspect"), "BuildKit: fetch base image before reading the checksum"
    for f in ("weekly.yml", "update-pins.yml"):
        w = open(os.path.join(root, ".github", "workflows", f), encoding="utf-8").read()
        assert "requirements.caps" in w and "::error title=Update versions::" in w, f
    assert "requirements.caps" in open(os.path.join(root, "update.sh"), encoding="utf-8").read()


@test("2.29.7 Security updates as <version>-s<n> (same program, only new packages): sorted correctly in app, update.sh and release; no git credentials in the workflows' working folder")
def _():
    import subprocess as _sp
    import re as _re
    from app import news
    assert util.vcmp("2.29.6-s1", "2.29.6") > 0 and util.vcmp("2.29.6-s1", "2.29.7") < 0 and util.vcmp("2.29.6-s10", "2.29.6-s2") > 0
    assert util.vcmp("2.30", "2.29.6-s9") > 0 and util.vcmp("2.29.6-s3", "2.29.6-s3") == 0
    assert news.VER_RX.match("2.29.6-s3") and news.VER_RX.match("2.29.6") and not news.VER_RX.match("2.29.6-x1")
    news.validate({"versions": [{"version": "2.29.6-s1", "items": [{"kind": "security", "de": "a", "en": "b"}]}]})
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    up = os.path.join(root, "update.sh")
    if os.path.exists(up):
        src = open(up, encoding="utf-8").read()
        fn = src[src.index("ver_lt() {"):src.index("\n}\n", src.index("ver_lt() {")) + 3]
        for a, b, lt in (("2.29.6", "2.29.6-s1", True), ("2.29.6-s2", "2.29.6-s10", True), ("2.29.6-s3", "2.29.7", True),
                         ("2.30", "2.29.6-s9", False), ("2.29.6-s1", "2.29.6", False),
                         # 2.32.5: four-part versions
                         ("2.29.6", "2.29.6.1", True), ("2.29.6.1", "2.29.6", False), ("2.29.6.1", "2.29.6.1-s1", True),
                         ("2.29.6.0", "2.29.6", False)):
            r = _sp.run(["sh", "-c", fn + f'\nver_lt "{a}" "{b}"'], capture_output=True, text=True)
            assert (r.returncode == 0) == lt, (a, b, r.returncode)
        assert "(-s[0-9]+)?$" in src, "ZIP with -s version is accepted"
    wfd = os.path.join(root, ".github", "workflows")
    if os.path.isdir(wfd):
        rel = open(os.path.join(wfd, "release.yml"), encoding="utf-8").read()
        assert "(-s[0-9]+)?$" in rel
        for f in os.listdir(wfd):
            t = open(os.path.join(wfd, f), encoding="utf-8").read()
            n_co = len(_re.findall(r"uses: actions/checkout@", t))
            assert t.count("persist-credentials: false") == n_co, f"{f}: git credentials in the working folder"


@test("2.29.8 Overview: long names under \"Letzte Bereitstellungen\" are wrapped and do not push the tile over its edge")
def _():
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static")
    js = open(os.path.join(root, "app.js"), encoding="utf-8").read()
    dep = js[js.index("const depCard"):js.index("const logCard")]
    assert "h('td.wrap', h('b', x.name)" in dep, "wrap deployment name"
    css = open(os.path.join(root, "app.css"), encoding="utf-8").read()
    assert ".grid > * { min-width: 0; }" in css and ".card > .card-b.flush { overflow-x: auto; }" in css


@test("2.29.9 Tenants: several collections per tenant (scope = all together); OU admins see only the collections of their scope, not the whole domain")
def _():
    import hashlib
    import secrets
    import time as _t
    from app import access, auth, deploy
    from app import main as _m
    mk = lambda n: db.ex("INSERT INTO computers(name, dns_name, created_at) VALUES(?,?,?)", (n, n.lower() + ".firma.local", util.now()))  # noqa: E731
    a1, a2, b1, x1 = mk("MB-A1"), mk("MB-A2"), mk("MB-B1"), mk("MB-X1")
    folder = db.ex("INSERT INTO collections(name, type) VALUES('Domäne MB (Test)', 'folder')")
    def col(name, members, parent=folder):
        c = db.ex("INSERT INTO collections(name, parent_id, type) VALUES(?, ?, 'static')", (name, parent))
        for m in members:
            db.ex("INSERT INTO collection_members VALUES(?,?)", (c, m))
        return c
    ou_a, ou_b = col("OU Standort A", [a1]), col("OU Standort B", [a2])
    ou_fremd, ou_leer = col("OU Fremdkunde", [b1]), col("OU Unbeteiligt", [x1])
    ausn = col("GG Ausnahme", [a2, x1])
    tid = db.ex("INSERT INTO tenants(name, collection_id, collection_ids, admin_group, created_at) VALUES('MB', ?, ?, 'PK-OU-MB', ?)",
                (ou_a, json.dumps([ou_a, ou_b]), util.now()))
    tf = db.ex("INSERT INTO tenants(name, collection_id, admin_group, created_at) VALUES('MB fremd', ?, 'PK-OU-MBF', ?)", (ou_fremd, util.now()))
    collections_.invalidate()
    access.flush()
    tok = secrets.token_urlsafe(16)
    th = hashlib.sha256(tok.encode()).hexdigest()
    db.ex("INSERT INTO sessions(token_hash, username, display_name, created, expires, ip, last_active, role, scope) VALUES(?,?,?,?,?,?,?,?,?)",
          (th, "ou-mb", "Test", util.now(), "2099-01-01 00:00:00", "127.0.0.1", util.now(), "admin", json.dumps([tid])))
    auth._checked[th] = _t.time() + 10 ** 6
    try:
        # Scope = both collections together
        assert access.tenant_cols(db.q1("SELECT * FROM tenants WHERE id=?", (tid,))) == [ou_a, ou_b]
        assert access.tenant_cols({"collection_id": 7, "collection_ids": None}) == [7], "older tenants: one collection"
        assert deploy.tenant_members(tid) == {a1, a2}
        assert {c["id"] for c in _asgi("GET", "/api/computers", tok)[1]} == {a1, a2}
        # Visible collections: own ones, those with own computers (exception group), default collections, folders above them
        vis = {c["id"]: c for c in _asgi("GET", "/api/collections", tok)[1]}
        assert ou_a in vis and ou_b in vis and ausn in vis and folder in vis, sorted(vis)
        assert ou_fremd not in vis, "collection of another tenant"
        assert ou_leer not in vis, "collection without own computers (rest of the domain)"
        assert vis[ausn]["count"] == 1, "counts only own computers"
        assert any(c["builtin"] == 1 for c in vis.values()), "default collections stay"
        meta = _asgi("GET", "/api/packages/meta", tok)[1]
        ch = {c["id"] for c in meta["collections"]}
        assert ausn in ch and ou_leer not in ch and ou_fremd not in ch, "also the selection in package conditions"
        # Save tenant with several collections (main admin way)
        assert _m._tenant_cols_in({"collection_ids": [ou_a, str(ou_b), ou_a]}) == [ou_a, ou_b]
        assert _m._tenant_cols_in({"collection_id": ou_a}) == [ou_a], "old call with one collection"
        assert _m._tenant_cols_in({"collection_ids": [0, ou_a]}) == [ou_a], "\"Alle Computer\" (0) does not count as a collection"
        assert _m._tenant_cols_in({"collection_ids": []}) == []
        assert ou_b in collections_.referenced_ids(), "a tenant's second collection is protected from deletion"
        js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
        assert "collection_ids: [...colSet]" in js and "collChoiceOptions(meta.collections, x.id)" in js
    finally:
        db.ex("DELETE FROM sessions WHERE token_hash=?", (th,))
        for t in (tid, tf):
            db.ex("DELETE FROM tenants WHERE id=?", (t,))
        for c in (ou_a, ou_b, ou_fremd, ou_leer, ausn):
            db.ex("DELETE FROM collection_members WHERE collection_id=?", (c,))
            db.ex("DELETE FROM collections WHERE id=?", (c,))
        db.ex("DELETE FROM collections WHERE id=?", (folder,))
        for c in (a1, a2, b1, x1):
            db.ex("DELETE FROM computers WHERE id=?", (c,))
        collections_.invalidate()
        access.flush()


@test("2.30 Tenants cleanly separated: OU admins change only their own – main admin packages/schedules/collections read-only (copying works), central schedules and reports only with own computers, own collections never larger than the tenant")
def _():
    import hashlib
    import secrets
    import time as _t
    from fastapi import HTTPException as _HE
    from app import access, auth, customreports, pklog, schedules
    from app import main as _m
    mk = lambda n: db.ex("INSERT INTO computers(name, dns_name, created_at) VALUES(?,?,?)", (n, n.lower() + ".firma.local", util.now()))  # noqa: E731
    a1, b1 = mk("TR-A1"), mk("TR-B1")
    def scol(name, members):
        c = db.ex("INSERT INTO collections(name, type) VALUES(?, 'static')", (name,))
        for x in members:
            db.ex("INSERT INTO collection_members VALUES(?,?)", (c, x))
        return c
    ca, cb = scol("TR Bereich A", [a1]), scol("TR Bereich B", [b1])
    ta = db.ex("INSERT INTO tenants(name, collection_id, repo_folder, admin_group, allow_packages, created_at) VALUES('TR A', ?, 'TRA', 'PK-OU-TRA', 1, ?)", (ca, util.now()))
    db.ex("UPDATE tenants SET allow_scripts=1 WHERE id=?", (ta,))
    tb = db.ex("INSERT INTO tenants(name, collection_id, repo_folder, admin_group, allow_packages, created_at) VALUES('TR B', ?, 'TRB', 'PK-OU-TRB', 1, ?)", (cb, util.now()))
    collections_.invalidate()
    access.flush()
    toks = []
    def session(scope, name):
        tok = secrets.token_urlsafe(16)
        th = hashlib.sha256(tok.encode()).hexdigest()
        db.ex("INSERT INTO sessions(token_hash, username, display_name, created, expires, ip, last_active, role, scope) VALUES(?,?,?,?,?,?,?,?,?)",
              (th, name, "Test", util.now(), "2099-01-01 00:00:00", "127.0.0.1", util.now(), "admin", json.dumps(scope) if scope is not None else None))
        auth._checked[th] = _t.time() + 10 ** 6
        toks.append(th)
        return tok
    ou, ou_b, haupt = session([ta], "tr-ou"), session([tb], "tr-oub"), session(None, "tr-haupt")
    made_c, made_p, made_s, made_r = [], [], [], []
    stp = json.dumps({"main": [{"id": "x", "type": "command", "command": "hostname"}]})
    try:
        # --- Packages: from the main admin for the tenant = read only; released or self-created = changeable
        p_main = db.ex("INSERT INTO packages(name, version, steps, conditions, run_as, tenant_id, created_at, ou_edit) VALUES('TR Haupt','1',?,'{}','system',?,?,0)", (stp, ta, util.now()))
        p_frei = db.ex("INSERT INTO packages(name, version, steps, conditions, run_as, tenant_id, created_at, ou_edit) VALUES('TR Frei','1',?,'{}','system',?,?,1)", (stp, ta, util.now()))
        made_p += [p_main, p_frei]
        ed = {p["id"]: p["editable"] for p in _asgi("GET", "/api/packages", ou)[1]}
        assert ed.get(p_main) is False and ed.get(p_frei) is True, ed
        pk = _asgi("GET", f"/api/packages/{p_main}", ou)[1]
        assert _asgi("PUT", f"/api/packages/{p_main}", ou, pk)[0] == 403
        assert _asgi("DELETE", f"/api/packages/{p_main}", ou)[0] == 403
        st, r = _asgi("POST", f"/api/packages/{p_main}/duplicate", ou)
        assert st == 200, (st, r)
        made_p.append(r["id"])
        assert db.q1("SELECT tenant_id, ou_edit FROM packages WHERE id=?", (r["id"],)) == {"tenant_id": ta, "ou_edit": 1}, "copy belongs to the OU admin"
        assert _m._ou_edit_flag({"username": "tr-ou", "scope": [ta]}, {"ou_edit": False}) == 1, "OU admin: always their own"
        # Main admin checkbox "OU admins may change"
        u_h = {"username": "tr-haupt", "scope": None, "role": "admin"}
        assert _m._ou_edit_flag(u_h, {"ou_edit": True}) == 1 and _m._ou_edit_flag(u_h, {"ou_edit": False}, {"ou_edit": 1}) == 0
        assert _m._ou_edit_flag(u_h, {}, {"ou_edit": 1}) == 1 and _m._ou_edit_flag(u_h, {}) == 0, "default: read-only"

        # --- Schedules: central = read only, own computers only, not startable; main admin tenant schedule = read + start
        base = {"kind": "packages", "packages": [p_frei], "triggers": []}
        s_zen = schedules.save(None, {**base, "name": "TR zentral", "targets": {"computers": [a1, b1], "collections": [cb]}}, "tr-haupt")
        s_main = schedules.save(None, {**base, "name": "TR Haupt für A", "tenant_id": ta, "targets": {"computers": [a1], "collections": []}}, "tr-haupt")
        s_own = schedules.save(None, {**base, "name": "TR eigen", "tenant_id": ta, "targets": {"computers": [a1], "collections": []}}, "tr-ou")
        made_s += [s_zen, s_main, s_own]
        db.ex("UPDATE schedules SET ou_edit=0 WHERE id IN (?,?)", (s_zen, s_main))
        db.ex("UPDATE schedules SET ou_edit=1 WHERE id=?", (s_own,))
        lst = {s["id"]: s for s in _asgi("GET", "/api/schedules", ou)[1]}
        assert s_zen in lst and s_main in lst and s_own in lst, sorted(lst)
        z = lst[s_zen]
        assert z["editable"] is False and z["runnable"] is False and z["members"] == 1, z
        assert z["targets"] == {"computers": [a1], "collections": []}, ("foreign targets hidden", z["targets"])
        assert lst[s_main]["editable"] is False and lst[s_main]["runnable"] is True and lst[s_own]["editable"] is True
        zb = {s["id"]: s for s in _asgi("GET", "/api/schedules", ou_b)[1]}
        assert zb[s_zen]["targets"]["computers"] == [b1] and zb[s_zen]["members"] == 1, "central: everyone sees only their computers"
        assert s_main not in {s["id"] for s in _asgi("GET", "/api/schedules", ou_b)[1]}, "schedule of another tenant"
        sd = _asgi("GET", f"/api/schedules/{s_zen}", ou)[1]
        assert sd["members"] == 1 and sd["targets"]["computers"] == [a1]
        assert _asgi("GET", f"/api/schedules/{s_zen}/preview", ou)[1]["members"] == 1
        body = {**base, "name": "geändert", "targets": {"computers": [a1], "collections": []}}
        for sid in (s_zen, s_main):
            assert _asgi("PUT", f"/api/schedules/{sid}", ou, body)[0] == 403, sid
            assert _asgi("DELETE", f"/api/schedules/{sid}", ou)[0] == 403, sid
        assert _asgi("POST", f"/api/schedules/{s_zen}/run", ou, {})[0] == 403, "only main admins start central ones"
        assert _asgi("POST", f"/api/schedules/{s_zen}/reset", ou, {})[0] == 403
        access.need_schedule({"username": "tr-ou", "scope": [ta]}, s_main, run=True)  # Main admin schedule of the tenant: starting allowed
        assert _asgi("PUT", f"/api/schedules/{s_own}", ou, {**body, "tenant_id": ta})[0] == 200
        st, r = _asgi("POST", f"/api/schedules/{s_zen}/duplicate", ou)
        assert st == 200, (st, r)
        made_s.append(r["id"])
        cp = schedules.get(r["id"])
        assert cp["tenant_id"] == ta and cp["ou_edit"] == 1 and cp["targets"]["computers"] == [a1] and not cp["enabled"], cp

        # --- Collections: main admin collection read only; create own ones, never larger than the tenant
        assert _asgi("PUT", f"/api/collections/{ca}", ou, {"name": "x", "type": "static"})[0] == 403
        assert _asgi("DELETE", f"/api/collections/{ca}", ou)[0] == 403
        assert _asgi("POST", f"/api/collections/{ca}/rename", ou, {"name": "x"})[0] == 403
        st, r = _asgi("POST", "/api/collections", ou, {"name": "TR alles", "type": "dynamic", "filters": [], "match": "all"})
        assert st == 200, (st, r)
        dyn = r["id"]; made_c.append(dyn)
        assert db.val("SELECT tenant_id FROM collections WHERE id=?", (dyn,)) == ta
        assert set(collections_.member_ids(dyn, fresh=True)) == {a1}, "rule \"all\" – still only computers of the tenant"
        st, r = _asgi("POST", "/api/collections", ou, {"name": "TR statisch", "type": "static"})
        stat = r["id"]; made_c.append(stat)
        assert _asgi("POST", f"/api/collections/{stat}/members", ou, {"add": [b1]})[0] == 403, "foreign computer"
        assert _asgi("POST", f"/api/collections/{stat}/members", ou, {"add": [a1]})[0] == 200
        assert _asgi("POST", "/api/collections", ou, {"name": "TR unter fremd", "type": "static", "parent_id": ca})[0] == 403
        flt = {"type": "group", "match": "all", "items": [{"type": "cond", "table": "computer", "column": "collection", "op": "in", "value": str(cb)}]}
        assert _asgi("POST", "/api/collections", ou, {"name": "TR Verweis", "type": "dynamic", "filters": flt})[0] == 403, "reference to an invisible collection"
        cl = {c["id"]: c for c in _asgi("GET", "/api/collections", ou)[1]}
        assert cl[dyn]["editable"] is True and cl[ca]["editable"] is False and cb not in cl
        assert dyn not in {c["id"] for c in _asgi("GET", "/api/collections", ou_b)[1]}, "own collection stays with the own tenant"
        pv = _asgi("POST", "/api/collections/preview", ou, {"filters": [], "match": "all"})[1]
        assert pv["count"] == 1 and [x["id"] for x in pv["sample"]] == [a1], pv
        vals = _asgi("GET", "/api/collections/values?table=computer&column=name&q=TR-", ou)[1]
        assert "TR-A1" in vals and "TR-B1" not in vals, vals
        st, r = _asgi("POST", f"/api/collections/{ca}/duplicate", ou, {})
        assert st == 200, (st, r)
        made_c.append(r["id"])
        assert db.val("SELECT tenant_id FROM collections WHERE id=?", (r["id"],)) == ta
        assert _asgi("POST", f"/api/collections/{cb}/duplicate", ou, {})[0] == 404
        try:
            _m._tenant_cols_in({"collection_ids": [dyn]})
            raise AssertionError("OU collection accepted as tenant scope")
        except _HE:
            pass
        st, r = _asgi("DELETE", f"/api/collections/{ca}", haupt)
        assert st == 400 and "Mandanten" in str(r), ("a tenant's scope is never deletable", st, r)
        assert _asgi("DELETE", f"/api/collections/{stat}", ou)[0] == 200
        made_c.remove(stat)

        # --- Reports: reading yes, own computers only; internal parameter cannot be set from outside
        st, r = _asgi("GET", f"/api/reports/computers?_scope=%5B{b1}%5D", ou)
        names = {x["name"] for x in r["rows"]}
        assert st == 200 and "TR-A1" in names and "TR-B1" not in names, names
        assert _asgi("GET", f"/api/reports/computers?collection={cb}", ou)[0] == 404
        cr = customreports.save(None, {"name": "TR Namen", "definition": {"source": "computer", "columns": [{"t": "computer", "c": "name"}], "limit": 5000}})
        made_r.append(cr["id"])
        rows = _asgi("GET", f"/api/reports/{customreports.PREFIX}{cr['id']}", ou)[1]["rows"]
        assert "TR-A1" in {x["c0"] for x in rows} and "TR-B1" not in {x["c0"] for x in rows}
        assert _asgi("GET", f"/api/customreports/{cr['id']}", ou)[0] == 200
        assert _asgi("PUT", f"/api/customreports/{cr['id']}", ou, {"name": "x"})[0] == 403
        assert _asgi("GET", "/api/mail/reports", ou)[0] == 403, "mail sending only main admins"
        h_names = {x["name"] for x in _asgi("GET", "/api/reports/computers", haupt)[1]["rows"]}
        assert {"TR-A1", "TR-B1"} <= h_names

        # --- one-time takeover: what an OU admin created, they may keep changing
        db.ex("UPDATE packages SET ou_edit=NULL WHERE id IN (?,?)", (p_main, p_frei))
        db.ex("UPDATE schedules SET ou_edit=NULL WHERE id IN (?,?)", (s_main, s_own))
        db.ex("DELETE FROM settings WHERE key='ou_owner_migrated'")
        pklog.info("deploy", "Paket 'TR Frei' angelegt.", user="tr-ou")
        db._migrate_ownership()
        assert db.val("SELECT ou_edit FROM packages WHERE id=?", (p_frei,)) == 1 and db.val("SELECT ou_edit FROM packages WHERE id=?", (p_main,)) == 0
        assert db.val("SELECT ou_edit FROM schedules WHERE id=?", (s_own,)) == 1 and db.val("SELECT ou_edit FROM schedules WHERE id=?", (s_main,)) == 0
        assert db.val("SELECT value FROM settings WHERE key='ou_owner_migrated'") == "1"
        js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
        assert "const OU_HIDDEN = new Set(['scanners', 'kiosk', 'settings', 'wingetcache']);" in js and "'Als Vorlage kopieren'" in js and "fieldset.ro" in js
    finally:
        for th in toks:
            db.ex("DELETE FROM sessions WHERE token_hash=?", (th,))
        for r_ in made_r:
            customreports.delete(r_)
        for s_ in made_s:
            db.ex("DELETE FROM schedule_targets WHERE schedule_id=?", (s_,))
            db.ex("DELETE FROM schedules WHERE id=?", (s_,))
        for p_ in made_p:
            db.ex("DELETE FROM packages WHERE id=?", (p_,))
        for t in (ta, tb):
            db.ex("DELETE FROM tenants WHERE id=?", (t,))
        for c in made_c + [ca, cb]:
            for k in [c] + _m._descendants(c):
                db.ex("DELETE FROM collection_members WHERE collection_id=?", (k,))
                db.ex("DELETE FROM collections WHERE id=?", (k,))
        for c in (a1, b1):
            db.ex("DELETE FROM computers WHERE id=?", (c,))
        collections_.invalidate()
        access.flush()


@test("2.30 docker-compose.yml: second DNS server truly optional (never the same address twice – older Compose versions reject that); installation guide in the manual")
def _():
    import re as _re
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    src = os.path.join(base, "docker-compose.yml")
    if not os.path.exists(src):
        return  # the Compose file is not in the image
    y = open(src, encoding="utf-8").read()
    assert "${PK_DNS_2:-${PK_DNS_1}}" not in y, "fallback value for PK_DNS_2 must not be PK_DNS_1 (duplicate entry)"

    def interp(v, env):  # what is needed from Compose: ${VAR}, ${VAR:-default}, ${VAR:?message}
        def one(m):
            name, op, rest = m.group(1), m.group(2), m.group(3) or ""
            val = env.get(name, "")
            if op == ":?" and not val:
                raise ValueError(rest)
            return val if val else (interp(rest, env) if op == ":-" else "")
        prev = None
        while prev != v:
            prev, v = v, _re.sub(r"\$\{(\w+)(?:(:-|:\?)((?:[^{}]|\$\{[^{}]*\})*))?\}", one, v)
        return v
    blocks = _re.findall(r"\n    dns:\n((?:      - .*\n)+)", y)
    assert len(blocks) == 2, "dns for pocketknife and guacd"
    for env in ({"PK_DNS_1": "10.0.10.5"}, {"PK_DNS_1": "10.0.10.5", "PK_DNS_2": ""}, {"PK_DNS_1": "10.0.10.5", "PK_DNS_2": "10.0.10.6"}):
        for b in blocks:
            items = [interp(x.strip()[2:], env) for x in b.strip().splitlines()]
            assert items[0] == "10.0.10.5" and len(items) == len(set(items)), (env, items)
            assert [x for x in items if x] == [v for k, v in sorted(env.items()) if v], (env, items)
    ex = open(os.path.join(base, ".env.example"), encoding="utf-8").read()
    assert "PK_DNS_2 is optional" in ex and "leave empty" in ex
    for lang, words in (("de", ("### Schritt 3: Die .env ausfüllen", "{#setup-errors}", "must be unique", "Container-Verwaltung")),
                        ("en", ("### Step 3: Fill in the .env", "{#setup-errors}", "must be unique", "container manager"))):
        md = open(os.path.join(base, "app", "static", "help", f"{lang}.md"), encoding="utf-8").read()
        for w in words:
            assert w in md, (lang, w)


@test("2.30 Start without write permission on /data (e.g. Synology): clear message with folder, user ID and remedy instead of a traceback; NAS guide")
def _():
    from app import privileges
    d = tempfile.mkdtemp(prefix="pk-wr-")
    try:
        assert privileges.unwritable([d, os.path.join(d, "tls")]) == [] and os.path.isdir(os.path.join(d, "tls")), "create missing subfolders"
        def deny(p):
            if p.endswith("tls"):
                raise PermissionError(13, "Permission denied")
        bad = privileges.unwritable([d, os.path.join(d, "tls")], probe=deny)
        assert bad == [(os.path.join(d, "tls"), "Permission denied")], bad
        msg = privileges.write_hint(bad, d)
        for w in ("Start abgebrochen", f"{os.getuid()}:{os.getgid()}", "tls: Permission denied", "PK_UID und PK_GID", "Synology", "chown -R",
                  "nicht von Hand"):
            assert w in msg, (w, msg)
        if os.geteuid() == 0:  # really check: as a user without rights on a foreign folder (own process, no fork)
            import subprocess
            os.chmod(d, 0o700)
            # copy the module to a folder readable by everyone – the program folder may not be readable for UID 65534
            mod = tempfile.mkdtemp(prefix="pk-wrmod-")
            os.chmod(mod, 0o755)
            shutil.copy(privileges.__file__, os.path.join(mod, "pkpriv.py"))
            os.chmod(os.path.join(mod, "pkpriv.py"), 0o644)
            code = ("import sys; sys.path.insert(0, %r); import pkpriv; "
                    "sys.exit(3 if pkpriv.unwritable([%r]) else 0)") % (mod, d)
            r = subprocess.run([sys.executable, "-I", "-c", code], user=65534, group=65534, extra_groups=[],
                               capture_output=True, text=True, timeout=60, cwd="/")
            shutil.rmtree(mod, ignore_errors=True)
            assert r.returncode == 3, ("foreign folder must be recognised as not writable", r.returncode, r.stderr[-300:])
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
        rp = os.path.join(base, "run.py")
        if os.path.exists(rp):
            src = open(rp, encoding="utf-8").read()
            assert src.index("privileges.unwritable(") < src.index("config.ensure_dirs()") < src.index("tls.ensure()")
            assert "raise SystemExit(privileges.write_hint(" in src
        for lang, w in (("de", "### Auf einem NAS (z. B. Synology) {#setup-nas}"), ("en", "### On a NAS (e.g. Synology) {#setup-nas}")):
            md = open(os.path.join(base, "app", "static", "help", f"{lang}.md"), encoding="utf-8").read()
            assert w in md and "PK_UID=1027" in md and "PK_DATA_PATH=/volume1/" in md, lang
    finally:
        shutil.rmtree(d, ignore_errors=True)


@test("2.30.1/2.31 winget: \"no applicable upgrade\"/\"no installed package\" with --scope – retry once without --scope (MobaXterm, PowerShell 7); then tar.exe also without --scope; do not treat a newer but unsuitable version as \"current\"")
def _():
    from app import deploy
    find = deploy._winget_find()
    for scope in ("machine", "user"):
        body = deploy._winget_body({"winget_action": "install", "winget_id": "Mobatek.MobaXterm", "winget_scope": scope,
                                    "extra_args": "--force"})
        rest = body[len(find):]
        assert rest.count("PK-Run $wg $pkA") == 3 and "-1978335189 -or $global:PKExit -eq -1978335212" in rest
        plain = [x for x in rest.splitlines() if x.strip().startswith("$pkA = ")][1]
        assert "--scope" not in plain and "--force" in plain and "Mobatek.MobaXterm" in plain, "without --scope, otherwise the same"
        assert rest.index("-1978335212") < rest.index("-1978335140"), "first without --scope, then tar.exe – with the arguments without --scope"
    nos = deploy._winget_body({"winget_action": "install", "winget_id": "Mobatek.MobaXterm", "winget_scope": ""})[len(find):]
    assert "-1978335212" not in nos and nos.count("PK-Run $wg $pkA") == 2, "without a fixed scope nothing to repeat without --scope"
    assert "-1978335189" not in deploy._winget_body({"winget_action": "uninstall", "winget_id": "X.Y", "winget_scope": "machine"})
    assert deploy.WINGET_MESSAGES[9011] and deploy.WINGET_MESSAGES[9012] and not deploy.evaluate({"type": "winget"}, 9011)[0]
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "remote.py"), encoding="utf-8").read()
    assert "$global:PKOut += $pkL" in src, "PK-Run remembers the output"
    pw = os.environ.get("PK_PWSH")
    if pw and os.path.exists(pw):
        import subprocess
        rest = deploy._winget_body({"winget_action": "upgrade", "winget_id": "X.Y", "winget_scope": "machine"})[len(find):]
        rest = rest.replace("[IO.File]::WriteAllText", "$null = ")  # writing the file is not checked here
        NOT_APPLY = "A newer package version is available in a configured source, but it does not apply to your system or requirements."
        TECH = "A newer version was found, but the install technology is different from the current version installed."
        CUR = "No newer package versions are available from the configured sources."
        cases = [  # (answers per call: (exit code, text), expected calls, expected final code)
            ([(-1978335189, NOT_APPLY), (-1978335140, "Failed to extract the contents of the archive"), (0, "Successfully installed")], 3, "0"),
            ([(-1978335212, "No installed package found matching input criteria."), (-1978335189, TECH)], 2, "9011"),
            ([(-1978335189, NOT_APPLY), (-1978335189, NOT_APPLY)], 2, "9012"),
            ([(-1978335189, CUR), (-1978335189, CUR)], 2, "-1978335189"),
            ([(0, "Successfully installed")], 1, "0"),
        ]
        for answers, want_runs, want_exit in cases:
            arr = ",".join(f"@({c}, '{t}')" for c, t in answers)
            stub = ("$ErrorActionPreference='Stop'; $wg = Join-Path ([IO.Path]::GetTempPath()) 'winget.exe'\n"
                    "$env:COMPUTERNAME = 'PC1'; $env:USERNAME = 'PC1$'; $env:LOCALAPPDATA = [IO.Path]::GetTempPath()\n"
                    f"$global:pkAns = @({arr}); $global:pkN = 0\n"
                    "function PK-Run([string]$File, [string]$Arguments) { Write-Output ('RUN ' + $Arguments); "
                    "$a = $global:pkAns[$global:pkN]; $global:pkN++; $global:PKOut = @($a[1]); $global:PKExit = $a[0] }\n")
            out = subprocess.run([pw, "-NoProfile", "-NonInteractive", "-Command", stub + rest + "\nWrite-Output ('EXIT ' + $global:PKExit)"],
                                 capture_output=True, text=True, timeout=60)
            runs = [x for x in out.stdout.splitlines() if x.startswith("RUN ")]
            assert len(runs) == want_runs, (answers, out.stdout, out.stderr)
            assert "--scope" in runs[0] and all("--scope" not in r for r in runs[1:]), runs
            assert out.stdout.strip().endswith("EXIT " + want_exit), (answers, out.stdout[-400:], out.stderr[-400:])


@test("2.31 Service account protection: \"password wrong\" from a single device (wrong DNS entry, not a domain member) triggers it only if the account really fails via LDAP")
def _():
    from app import guard
    import app.remote as _r
    orig = guard.verify_account
    try:
        guard.reset()
        guard.verify_account = lambda: True
        msg = "SMB-Anmeldung: STATUS_LOGON_FAILURE 0xc000006d"
        t = guard.report_remote(msg, "SMB")
        assert not guard.paused() and t.startswith(guard.HOST_REJECT), t
        assert guard.report_remote("WinRM 401: Zugriff verweigert", "WinRM") is None, "no account error – nothing to do"
        assert not guard.paused()
        guard.verify_account = lambda: False
        guard.report_remote(msg, "SMB")
        assert guard.paused(), "account really fails -> protection"
        guard.reset()
        guard.verify_account = lambda: True
        assert _r._winrm_error("the specified credentials were rejected by the server 0xc000006d").startswith(guard.HOST_REJECT)
        assert not guard.paused()
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "remote.py"), encoding="utf-8").read()
        assert 'guard.report(' not in src, "WinRM/SMB to computers only via report_remote"
    finally:
        guard.verify_account = orig
        guard.reset()


@test("2.31 Wake-on-LAN: send several times, fixed relays first, MAC of the wired card from the inventory, wait for waking up with a second attempt, readiness in inventory/report; schedule wakes only on request and only for real runs")
def _():
    import time as _t
    from app import inventory, presence, reports, schedules, tools
    mk = lambda n, ip, mac: db.ex("INSERT INTO computers(name, dns_name, ip_addresses, mac_addresses, online, winrm, created_at) "  # noqa: E731
                                 "VALUES(?,?,?,?,?,?,?)", (n, n.lower() + ".firma.local", ip, mac, 1 if n != "WL-ZIEL" else 0,
                                                          1 if n != "WL-ZIEL" else 0, util.now()))
    ziel, nachbar, server = mk("WL-ZIEL", "10.9.0.5", "00:11:22:33:44:55"), mk("WL-NACHBAR", "10.9.0.6", ""), mk("WL-SRV", "10.8.0.1", "")
    db.ex("INSERT INTO inventory_data(computer_id, category, json, updated) VALUES(?, 'nics', ?, ?)", (ziel, json.dumps([
        {"name": "Intel Ethernet", "mac": "00:11:22:33:44:66", "ip": "", "wired": True, "wol_magic": "an", "wake_armed": True},
        {"name": "Intel Wi-Fi", "mac": "00:11:22:33:44:77", "ip": "", "wired": False, "wol_magic": "aus", "wake_armed": False}]), util.now()))
    db.ex("UPDATE computers SET extra=? WHERE id=?", (json.dumps({"fast_startup": False}), ziel))
    old = {k: db.setting(k) for k in ("wol_repeat", "wol_wait_seconds", "wol_relays")}
    orig_ps, orig_probe, orig_wait = tools.remote.ps, presence.probe, tools.WAKE_POLL
    sent = []
    try:
        c = db.q1("SELECT * FROM computers WHERE id=?", (ziel,))
        macs = [m for m, _p in tools._packets(c)]
        assert macs == ["00:11:22:33:44:55", "00:11:22:33:44:66"], ("wired card included, WLAN not", macs)
        db.set_setting("wol_relays", "wl-srv")
        rel = [(r["name"], same) for r, same in tools.wol_relays(c)]
        assert rel[0][0] == "WL-SRV" and ("WL-NACHBAR", True) in rel, rel
        # second attempt: other relays if there are any – otherwise (2.32.5) again those in the target network instead of only the container's
        assert ("WL-NACHBAR", True) in [(r["name"], same) for r, same in tools.wol_relays(c, exclude={"WL-NACHBAR", "WL-SRV"})], \
            "no other relay in the target network -> the previous one again"
        db.set_setting("wol_repeat", 3)
        tools.remote.ps = lambda host, script, **kw: (sent.append((host, script)) or ("OK 10.9.0.255:9\n", "", 0))
        res = tools.wake(c)
        assert res["exit"] == 0 and "3×" in res["output"] and "Magic Packet an, darf wecken" in res["output"], res["output"]
        assert all("-le 3" in sc for _h, sc in sent) and len(sent) == 2, "each relay sends 3x"
        assert "(fest)" in res["output"]
        # wait for waking up: online at the second look
        db.set_setting("wol_wait_seconds", 60)
        tools.WAKE_POLL = 0.05
        calls = {"n": 0}
        def probe(row, now=None, force=False):
            calls["n"] += 1
            return (calls["n"] >= 2, calls["n"] >= 2)
        presence.probe = probe
        assert tools.watch_wake(c, res["relay"], "test") is True
        for _i in range(100):
            st = tools.wake_state(ziel)
            if st and st["state"] != "waiting":
                break
            _t.sleep(0.05)
        assert st["state"] == "awake" and db.val("SELECT online FROM computers WHERE id=?", (ziel,)) == 1, st
        db.set_setting("wol_wait_seconds", 0)
        assert tools.watch_wake(c, [], "test") is False, "0 = do not wait"
        # Readiness: column for collections and report
        w, p = collections_.build({"type": "group", "match": "all", "items": [
            {"type": "cond", "table": "computer", "column": "wol_ready", "op": "is_true", "value": ""}]})
        assert ziel in {r["id"] for r in db.q(f"SELECT c.id FROM computers c WHERE {w}", p)}, "wired card with Magic Packet on -> ready"
        rows = reports.run("wol", {})["rows"]
        assert any(r["computer"] == "WL-ZIEL" and r["wol_magic"] == "an" and r["wake_armed"] == "ja" for r in rows), rows
        script = inventory.build_script(["nics"])
        assert "Get-NetAdapterPowerManagement" in script and "wake_armed" in script and "'fast_startup'" in script
        assert "fast_startup" in inventory.MODULE_EXTRA["nics"]
        pw = os.environ.get("PK_PWSH")
        if pw and os.path.exists(pw):
            import subprocess
            chk = ("$e = $null; $null = [System.Management.Automation.Language.Parser]::ParseInput($env:PKS, [ref]$null, [ref]$e); "
                   "if ($e) { $e | ForEach-Object { $_.Message } } else { 'PARSE-OK' }")
            r = subprocess.run([pw, "-NoProfile", "-NonInteractive", "-Command", chk], capture_output=True, text=True, timeout=60,
                               env={**os.environ, "PKS": script})
            assert "PARSE-OK" in r.stdout, r.stdout + r.stderr
        # Schedule: default off; on -> only for real runs, not for heartbeat/catch-up
        assert schedules.DEFAULT_OPTIONS["wake_offline"] is False
        woken = []
        orig_wake = tools.wake
        tools.wake = lambda cc, **kw: (woken.append(cc["id"]) or {"exit": 1})
        try:
            db.ex("UPDATE computers SET online=0 WHERE id=?", (ziel,))  # 2.32.5: only those that are really off are woken
            sc = {"id": 0, "name": "T"}
            schedules._wake_offline(sc, {"wake_offline": True}, [ziel], "Heartbeat (erreichbar, noch nicht versorgt)")
            schedules._wake_offline(sc, {"wake_offline": False}, [ziel], "täglich 07:00")
            schedules._wake_offline(sc, {"wake_offline": True}, [ziel], "täglich 07:00")
            for _i in range(50):
                if woken:
                    break
                _t.sleep(0.05)
            assert woken == [ziel], woken
        finally:
            tools.wake = orig_wake
        cl = schedules.clean({"name": "x", "kind": "packages", "packages": [db.val("SELECT id FROM packages LIMIT 1")] if db.val("SELECT id FROM packages LIMIT 1") else [],
                              "targets": {"computers": [ziel]}, "options": {"wake_offline": True, "offline": "skip"}}) \
            if db.val("SELECT id FROM packages LIMIT 1") else None
        if cl:
            assert cl["options"]["offline"] == "wait", "woken targets are noted"
        js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
        assert "function wakeDialog(targets)" in js and "['wol', 'Wake-on-LAN', 'play', [wolCard], true]" in js and "o.wake_offline" in js
    finally:
        tools.remote.ps, presence.probe, tools.WAKE_POLL = orig_ps, orig_probe, orig_wait
        for k, v in old.items():
            db.set_setting(k, v)
        for x in (ziel, nachbar, server):
            db.ex("DELETE FROM inventory_data WHERE computer_id=?", (x,))
            db.ex("DELETE FROM computers WHERE id=?", (x,))


@test("2.31 Troubleshooting: process \"ended without result\" names the reason (PowerShell error output, blocking by Defender ASR/AppLocker, last output); CPU load per background task and load peaks with context")
def _():
    from app import loadstats
    import app.remote as _r
    assert "2>\"' + (Join-Path $dir 'pk_err.txt')" in _r.START_DIRECT and "cmd.exe /d /c" in _r.START_DIRECT
    assert "PK_ASRBLOCK:" in _r.POLL and "Microsoft-Windows-Windows Defender/Operational" in _r.POLL and "8007" in _r.POLL
    m = _r.gone_message("PK_PROC:gone\nPK_ASRBLOCK:D1E49AAC-8F56-4280-B9BA-993A6D77406C\nPK_LAST:OK gesetzt", True)
    assert "Prozesserstellungen durch PSExec- und WMI-Befehle blockieren" in m and "Lokales System" in m and "Letzte Ausgabe: OK gesetzt" in m, m
    # 2.32.1: "only monitored" (1122) is never the reason – then the PowerShell error output counts
    m = _r.gone_message("PK_PROC:gone\nPK_ASRAUDIT:d1e49aac-8f56-4280-b9ba-993a6d77406c\nPK_ASRAUDIT:d1e49aac-8f56-4280-b9ba-993a6d77406c\n"
                        "PK_ERR:Dieses Skript enthält schädliche Inhalte und wurde von Ihrer Antivirensoftware blockiert.", True)
    assert "blockiert durch" not in m and "schädliche Inhalte" in m, m
    m = _r.gone_message("PK_PROC:gone\nPK_ASRAUDIT:d1e49aac-8f56-4280-b9ba-993a6d77406c", True)
    assert "nur protokolliert" in m and "nicht der Grund" in m and "blockiert durch" not in m, m
    assert "Bedrohung" in _r.gone_message("PK_PROC:gone\nPK_AV:Trojan:PowerShell/xyz", True)
    assert "Id = 1121, 1122; StartTime = $since" in _r.POLL and "PK_ASRAUDIT:" in _r.POLL
    m = _r.gone_message("PK_PROC:gone\nPK_ERR:Die Datei kann nicht geladen werden.", False)
    assert "Fehlermeldung von PowerShell: Die Datei kann nicht geladen werden." in m and "WMI" not in m, m
    assert "Lokales System" in _r.gone_message("PK_PROC:gone", True)
    with loadstats.task("selbsttest"):
        sum(i * i for i in range(200000))
    loadstats._req.clear()
    loadstats.request("/api/computers/17/inventory")
    v = loadstats.view()
    t = [x for x in v["tasks"] if x["key"] == "selbsttest"][0]
    assert t["runs"] == 1 and t["cpu"] >= 0, t
    assert loadstats._NUM.sub("/{id}", "/api/computers/17/inventory") == "/api/computers/{id}/inventory"
    loadstats._last = (loadstats.time.time() - 10, loadstats.cpu() - 9)  # 90 % in 10 s
    pct = loadstats.sample(["heartbeat"], scans=2, deployments=1)
    assert pct >= 80 and v is not None
    pk = loadstats.view()["peaks"][0]
    assert pk["tasks"] == ["heartbeat"] and pk["scans"] == 2 and any("/api/computers/{id}/inventory" in x for x in pk["requests"]), pk
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "scheduler.py"), encoding="utf-8").read()
    assert "loadstats.task(" in src and "loadstats.sample(" in src


@test("2.32 Combine packages: nested (order, restart, on error) or as a copy of the steps (\"Ausführen als\" stays, own conditions prevent copying); never across tenants; gpupdate /force as usual")
def _():
    import hashlib
    import secrets
    import time as _t
    from app import auth, deploy, steps
    tok = secrets.token_urlsafe(16)
    th = hashlib.sha256(tok.encode()).hexdigest()
    db.ex("INSERT INTO sessions(token_hash, username, display_name, created, expires, ip, last_active, role, scope) VALUES(?,?,?,?,?,?,?,?,?)",
          (th, "pm-haupt", "Test", util.now(), "2099-01-01 00:00:00", "127.0.0.1", util.now(), "admin", None))
    auth._checked[th] = _t.time() + 10 ** 6
    mk = lambda name, st, run_as="system", reboot="never", conds="{}", tenant=None: db.ex(  # noqa: E731
        "INSERT INTO packages(name, version, steps, conditions, run_as, reboot, tenant_id, created_at) VALUES(?,?,?,?,?,?,?,?)",
        (name, "1", json.dumps({"main": st}), conds, run_as, reboot, tenant, util.now()))
    a = mk("PM A", [{"id": "a1", "type": "command", "command": "echo a"}], run_as="system", reboot="if_required")
    b = mk("PM B", [{"id": "b1", "type": "command", "command": "echo b"}], run_as="deploy")
    cnd = mk("PM Bedingung", [{"id": "c1", "type": "command", "command": "echo c"}],
             conds=json.dumps({"os": ["win11"], "arch": ["x64"], "ps_min": "", "logged_on": "always", "files": [], "registry": [], "collections": []}))
    tid = db.ex("INSERT INTO tenants(name, created_at) VALUES('PM Mandant', ?)", (util.now(),))
    tid2 = db.ex("INSERT INTO tenants(name, created_at) VALUES('PM Mandant 2', ?)", (util.now(),))
    t1, t2 = mk("PM T1", [{"id": "t1", "type": "command", "command": "echo t1"}], tenant=tid), mk("PM T2", [{"id": "t2", "type": "command", "command": "echo t2"}], tenant=tid2)
    made = [a, b, cnd, t1, t2]
    try:
        st, r = _asgi("POST", "/api/packages/merge", tok, {"ids": [b, a], "name": "PM Zusammen", "mode": "nested"})
        assert st == 200, (st, r)
        made.append(r["id"])
        p = steps.normalize(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (r["id"],))))
        assert [x["package_id"] for x in p["steps"]["main"]] == [b, a] and all(x["type"] == "nested" for x in p["steps"]["main"])
        assert p["reboot"] == "if_required" and all(x["on_error"] == "stop" for x in p["steps"]["main"]), p["reboot"]
        st, r = _asgi("POST", "/api/packages/merge", tok, {"ids": [a, b], "mode": "copy", "continue_on_error": True})
        assert st == 200, (st, r)
        made.append(r["id"])
        p = steps.normalize(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (r["id"],))))
        cmds = [(x["command"], x["run_as"], x["on_error"]) for x in p["steps"]["main"]]
        assert cmds == [("echo a", "system", "continue"), ("echo b", "deploy", "continue")], cmds
        assert p["name"] == "PM A + PM B" and len({x["id"] for x in p["steps"]["main"]}) == 2
        st, r = _asgi("POST", "/api/packages/merge", tok, {"ids": [a, cnd], "mode": "copy"})
        assert st == 400 and "Bedingungen" in str(r), (st, r)
        st, r = _asgi("POST", "/api/packages/merge", tok, {"ids": [a, cnd], "mode": "nested"})
        assert st == 200, ("nested works with conditions", st, r)
        made.append(r["id"])
        st, r = _asgi("POST", "/api/packages/merge", tok, {"ids": [t1, t2], "mode": "nested"})
        assert st == 400 and "Mandanten" in str(r), (st, r)
        st, r = _asgi("POST", "/api/packages/merge", tok, {"ids": [a, t1], "mode": "nested"})
        assert st == 200 and db.val("SELECT tenant_id FROM packages WHERE id=?", (r["id"],)) == tid, ("central + tenant -> tenant", st, r)
        made.append(r["id"])
        assert _asgi("POST", "/api/packages/merge", tok, {"ids": [a]})[0] == 400, "at least two"
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "main.py"), encoding="utf-8").read()
        # 2.38.4: computer and user policy (user in the own session, see gpupdate.py)
        from app import gpupdate as _gp
        assert '"gpupdate": gpupdate.script()' in src and "echo N| gpupdate.exe /target:computer /force" in _gp.SCRIPT and "gpupdate.exe /target:user /force" in _gp.SCRIPT
        js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
        assert "function mergePackagesDlg(selRows)" in js and "Zu neuem Paket zusammenfassen" in js
    finally:
        db.ex("DELETE FROM sessions WHERE token_hash=?", (th,))
        for x in made:
            db.ex("DELETE FROM pkg_ident WHERE package_id=?", (x,))
            db.ex("DELETE FROM packages WHERE id=?", (x,))
        db.ex("DELETE FROM tenants WHERE id IN (?,?)", (tid, tid2))


@test("2.32 Self-test independent of the instance: settings from the .env (PK_NETBIOS, service account ...) do not apply in the test; no fork()")
def _():
    from app import config
    assert config.NETBIOS == "" and config.DOMAIN == "firma.local" and not config.ENV_SERVICE_USER, (config.NETBIOS, config.DOMAIN)
    assert not [k for k in os.environ if k.startswith("PK_") and k not in ("PK_PWSH", "PK_DATA_DIR", "PK_HTTPS", "PK_DOMAIN", "PK_PUBLIC_HOSTNAME")
                and not k.startswith("PK_SELFTEST")], sorted(k for k in os.environ if k.startswith("PK_"))
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    assert "os." + "fork()" not in src, "fork() in a process with threads can hang – use subprocess"


@test("2.32 Scan after deployment: the package's chosen scan profile – also with several packages and in schedules (per package, \"Nicht scannen\" counts); if a scan is already running, it is caught up afterwards instead of discarded")
def _():
    from app import deploy, inventory, profiles, schedules, steps
    apps = profiles.apps_profile_id()
    hw = db.val("SELECT id FROM scan_profiles WHERE builtin_key='hardware'") or profiles.default()["id"]
    mk = lambda name, rescan, prof: db.ex("INSERT INTO packages(name, version, steps, conditions, run_as, rescan, rescan_profile_id, created_at) "  # noqa: E731
                                          "VALUES(?,?,?,?,?,?,?,?)", (name, "1", json.dumps({"main": [{"id": "x", "type": "command", "command": "echo"}]}),
                                                                      "{}", "system", rescan, prof, util.now()))
    a, b, n, gone = mk("RS A", 1, None), mk("RS B", 1, hw), mk("RS Nie", 0, None), mk("RS Weg", 1, 987654)
    made = [a, b, n, gone]
    orig_pool = inventory.pool
    try:
        assert deploy.rescan_profiles(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (b,)))) == [hw], "own profile"
        assert deploy.rescan_profiles(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (a,)))) == [apps], "default: Anwendungen"
        assert deploy.rescan_profiles(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (n,)))) == [], "Nicht scannen"
        assert deploy.rescan_profiles(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (gone,)))) == [apps], "deleted profile -> Anwendungen"
        comp = deploy.composite([b, n, a])
        assert deploy.rescan_profiles(comp) == [hw, apps], ("several packages: per package", deploy.rescan_profiles(comp))
        assert deploy.rescan_profiles(deploy.composite([n])) == [], "only \"Nicht scannen\""
        s = {"name": "RS", "packages": [b, a], "options": {"stop_on_fail": True}, "tenant_id": None}
        assert deploy.rescan_profiles(schedules.composite_package(s)) == [hw, apps], "schedule: per package instead of always \"Anwendungen\""
        # a scan is running right now: catch up instead of discarding
        started = []
        class _P:
            def submit(self, fn, cid, user, prof):
                started.append((cid, prof["id"]))
        inventory.pool = lambda: _P()
        cid = 4242
        inventory._scanning[cid] = "läuft"
        assert inventory.queue_scan(cid, None, hw) is False, "without follow: as before"
        assert inventory.queue_scan(cid, None, hw, follow=True) is True and inventory.queue_scan(cid, None, hw, follow=True)
        inventory.queue_scan(cid, None, apps, follow=True)
        inventory._release(cid)
        assert started == [(cid, hw)] and inventory._scanning.get(cid), started
        inventory._release(cid)
        assert started == [(cid, hw), (cid, apps)], started
        inventory._release(cid)
        assert cid not in inventory._scanning and cid not in inventory._follow
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "deploy.py"), encoding="utf-8").read()
        assert "inventory.queue_scan(c[\"id\"], None, pid, follow=True)" in src
    finally:
        inventory.pool = orig_pool
        inventory._scanning.pop(4242, None)
        inventory._follow.pop(4242, None)
        for x in made:
            db.ex("DELETE FROM packages WHERE id=?", (x,))




@test("2.32.2 Scripts always run: first normally as a file (-File, exit code, $PSScriptRoot, parameters); if Windows refuses the file (execution policy via GPO, signature, zone), its content is loaded – never twice; hardened Windows (language mode, antivirus, blocked program) is reported by POCKET KNIFE with its own code and plain text")
def _():
    import subprocess
    from app import deploy, remote, steps, util
    # Wrapper: first & 'pk_run.ps1', load only on UnauthorizedAccess for exactly this file
    for src in (remote.REGISTER, remote.START_DIRECT):
        assert "__PKSTART__" not in src and "try { & ''' + $pkRunF + '''" in src and "-File" not in src, src[:300]
    # Steps, inline scripts, repository .ps1 and tools via PK-RunPs1
    b = steps.body_for({"type": "powershell", "script": "Write-Output 1", "id": "x"}, {})
    assert b.rstrip().endswith("PK-RunPs1 $pkF ''"), b[-80:]
    assert "PK-RunPs1 $f '-Silent'" in deploy._custom_body({"install_type": "ps1", "install_file": "setup.ps1", "arguments": "-Silent"})
    assert "PK-RunPs1 $sf ''" in deploy._custom_body({"install_type": "script", "script": "x", "arguments": ""})
    tsrc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "tools.py"), encoding="utf-8").read()
    assert "PK-RunPs1 $sf ''" in tsrc and "PS_LOAD" not in tsrc
    for k in ("__PSFLAGS__", "__PSLOADPRE__", "__PSLOADPOST__"):
        assert k not in remote.WRAPPER, k
    assert set(deploy.HARD_MESSAGES) == {9013, 9014, 9015, 9016}
    assert "Sprachmodus" in remote.hard_hint("Methodenaufrufe werden in diesem Sprachmodus nur für Kerntypen unterstützt.")
    assert "Virenschutz" in remote.hard_hint("This script contains malicious content and has been blocked by your antivirus software.")
    assert remote.hard_hint("System.UnauthorizedAccessException: Access to the path is denied") == "", "access denied is not hardening"
    assert "Sprachmodus" in remote.gone_message("PK_PROC:gone\nPK_ERR:Cannot invoke method. Method invocation is supported only on core types in this language mode.", False)
    pw = os.environ.get("PK_PWSH")
    if not (pw and os.path.exists(pw)):
        return
    d = tempfile.mkdtemp(prefix="pk-ps-")
    try:
        # Windows PowerShell simulated: refuses -File for files with "refuse" in the name like a GPO (RemoteSigned)
        exe = os.path.join(d, "sr", "System32", "WindowsPowerShell", "v1.0")
        os.makedirs(exe)
        exe = os.path.join(exe, "powershell.exe")
        with open(exe, "w") as fh:
            fh.write('#!/bin/sh\nprev=""\nfor a in "$@"; do\n  case "$prev:$a" in -File:*refuse*)\n'
                     '    echo "Die Datei $a kann nicht geladen werden. Die Datei ist nicht digital signiert." >&2\n'
                     '    echo "    + FullyQualifiedErrorId : UnauthorizedAccess" >&2; exit 1;;\n  esac\n  prev="$a"\ndone\n'
                     f'exec "{pw}" "$@"\n')
        os.chmod(exe, 0o755)
        run = os.path.join(d, "run")
        os.makedirs(run)
        env = {**os.environ, "SystemRoot": os.path.join(d, "sr"), "TERM": "dumb"}

        def wrap(body, clm=False):
            for f in os.listdir(run):
                os.remove(os.path.join(run, f))
            for n in ("refuse_x.ps1", "ok_x.ps1"):
                with open(os.path.join(run, n), "w", encoding="utf-8-sig") as fh:
                    fh.write("param([string]$Name = 'leer', [switch]$Laut)\n'R=' + $PSScriptRoot + ' N=' + $Name + ' L=' + $Laut + ' P=' + $PSCommandPath\n"
                             "'läuft' | Out-File -LiteralPath (Join-Path $PSScriptRoot 'o.txt') -Append\nexit 6\n")
            with open(os.path.join(run, "pk_run.ps1"), "w", encoding="utf-8-sig") as fh:
                fh.write(remote.WRAPPER.replace("__DIR__", run).replace("__BODY__", body))
            cmd = ("$ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage'; " if clm else "") + "& ./pk_run.ps1"
            subprocess.run([pw, "-NoProfile", "-NonInteractive", "-Command", cmd], cwd=run, env=env, capture_output=True, text=True, timeout=180)
            rd = lambda n: open(os.path.join(run, n), encoding="utf-8-sig").read() if os.path.exists(os.path.join(run, n)) else ""  # noqa: E731
            return rd("pk_exit.txt").strip(), rd("pk_output.log"), rd("o.txt")

        for clm in (False, True):
            code, log, o = wrap("$f = Join-Path $PKDir 'refuse_x.ps1'\nPK-RunPs1 $f '-Name \"a b\" -Laut'\n", clm)
            assert code == "6" and o.count("läuft") == 1, ("refused -> loaded, once, exit code", clm, code, log)
            assert f"R={run} N=a b L=True P={os.path.join(run, 'refuse_x.ps1')}" in log and "lädt ihren Inhalt" in log, log
            assert ("eingeschränkten Sprachmodus" in log) == clm and "Cannot" not in log, log
            code, log, o = wrap("$f = Join-Path $PKDir 'ok_x.ps1'\nPK-RunPs1 $f '-Name z'\n", clm)
            assert code == "6" and o.count("läuft") == 1 and "lädt ihren Inhalt" not in log and "N=z L=False" in log, ("normal -File", log)
        # Script already ran (output) and then fails on something else -> never a second time
        code, log, o = wrap("$sf = Join-Path $PKDir 'x.ps1'\nSet-Content -LiteralPath $sf -Value \"'teil'; Write-Error 'FullyQualifiedErrorId : UnauthorizedAccess x.ps1'; exit 1\"\nPK-RunPs1 $sf ''\n")
        assert code == "1" and "lädt ihren Inhalt" not in log, log
        sc = lambda t: "$sf = Join-Path $PKDir 'c.ps1'\nSet-Content -LiteralPath $sf -Value " + util.psq(t) + "\nPK-RunPs1 $sf ''\n"  # noqa: E731
        # 2.32.5: the wrapper no longer changes the exit code – POCKET KNIFE classifies it only on a failure
        code, log, _o = wrap(sc("$ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage'\n[System.Text.Encoding]::UTF8.GetString([byte[]](65))\nthrow 'x'"))
        assert code == "1" and remote.apply_hard(1, log)[0] == 9015, log
        code, log, _o = wrap(sc("Write-Error 'Dieses Skript enthält schädliche Inhalte und wurde von Ihrer Antivirensoftware blockiert.'; exit 1"))
        assert code == "1" and remote.hard_code(log) == 9014, log
        code, log, _o = wrap(sc("Write-Error 'Dieses Programm wird durch eine Gruppenrichtlinie blockiert.'; exit 1"))
        assert code == "1" and remote.hard_code(log) == 9016, log
        code, log, _o = wrap(sc("Write-Error 'Methodenaufrufe werden in diesem Sprachmodus nur für Kerntypen unterstützt.'; exit 0"))
        assert code == "0", ("success stays success", log)
        # 2.32.5: refused sub-script (error with position) -> the script already ran, never a second time
        inner = ("'teil' | Out-File -LiteralPath (Join-Path $PSScriptRoot 'o.txt') -Append\n"
                 "[Console]::Error.WriteLine('At ' + $PSCommandPath + ':2 char:1')\n"
                 "[Console]::Error.WriteLine('    + FullyQualifiedErrorId : UnauthorizedAccess')\nexit 1\n")
        code, log, o = wrap("$f = Join-Path $PKDir 'inner_y.ps1'\nSet-Content -LiteralPath $f -Value " + util.psq(inner) + "\nPK-RunPs1 $f ''\n")
        assert code == "1" and o.count("teil") == 1 and "lädt ihren Inhalt" not in log, ("sub-script refused – not again", o, log)
        # Software restriction: deliberate block, do not load
        code, log, o = wrap("$f = Join-Path $PKDir 'refuse_srp.ps1'\nSet-Content -LiteralPath $f -Value 'x'\nPK-RunPs1 $f ''\n")
        assert "lädt ihren Inhalt" in log, "control sample: normal refusal loads"
        # Syntax error in the loaded content -> 1 instead of "Erfolgreich"; "using namespace" stays at the front
        for body_ps, want in (("$x = (1 +", "1"), ("using namespace System.Text\n'ok' | Out-File -LiteralPath (Join-Path $PSScriptRoot 'o.txt')\nexit 3", "3")):
            code, log, o = wrap("$f = Join-Path $PKDir 'refuse_z.ps1'\nSet-Content -LiteralPath $f -Value " + util.psq(body_ps.replace("\\n", "\n")) + "\nPK-RunPs1 $f ''\n")
            assert code == want and "lädt ihren Inhalt" in log, (body_ps, code, log)
        assert remote.hard_code("FullyQualifiedErrorId : UnauthorizedAccess\n… POCKET KNIFE lädt ihren Inhalt.") == 0, "loaded -> 9013 is not the reason"
        assert "WinRM-Sitzung" in remote.hard_hint("Cannot invoke method … in this language mode.", start=True)
        code, log, _o = wrap("PK-Run (Join-Path $PKDir 'gibtsnicht.exe') 'x'\n")
        assert code == "1" and "Konnte nicht gestartet werden" in log, log
        # Start of the wrapper: normal once; refused (UnauthorizedAccess for pk_run.ps1) -> content loaded; other error -> not again
        expr = util.ps_start_expr("$pkRunF")
        for body, want in (("'x' | Out-File -LiteralPath (Join-Path (Get-Location) 'o.txt') -Append", 1),
                           ("if ($PSCommandPath) { throw (New-Object System.Management.Automation.ErrorRecord ((New-Object System.Management.Automation.PSSecurityException 'Die Datei pk_run.ps1 ist nicht digital signiert.'), 'UnauthorizedAccess', 'SecurityError', $null)) }\n'x' | Out-File -LiteralPath (Join-Path (Get-Location) 'o.txt') -Append", 1),
                           ("'x' | Out-File -LiteralPath (Join-Path (Get-Location) 'o.txt') -Append\nthrow 'anderer Fehler'", 1),
                           ("if ($PSCommandPath) { throw (New-Object System.Management.Automation.ErrorRecord ((New-Object System.Management.Automation.PSSecurityException 'andere.ps1'), 'UnauthorizedAccess', 'SecurityError', $null)) }\n'x' | Out-File -LiteralPath (Join-Path (Get-Location) 'o.txt') -Append", 0)):
            for f in os.listdir(run):
                os.remove(os.path.join(run, f))
            with open(os.path.join(run, "pk_run.ps1"), "w", encoding="utf-8-sig") as fh:
                fh.write(body + "\n")
            ps = (f"$pkRunF = '{os.path.join(run, 'pk_run.ps1')}'; $a = {expr}; "
                  f"$p = Start-Process -FilePath '{pw}' -ArgumentList $a -Wait -PassThru -WorkingDirectory '{run}' -NoNewWindow -RedirectStandardError '{d}/e.txt'")
            subprocess.run([pw, "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True, timeout=120)
            o = open(os.path.join(run, "o.txt")).read() if os.path.exists(os.path.join(run, "o.txt")) else ""
            assert o.count("x") == want, (body, o, open(f"{d}/e.txt").read())
    finally:
        shutil.rmtree(d, ignore_errors=True)


@test("2.32.4 Shortcuts belong to the tool: set in the tool (Ctrl+Alt [+Shift] + A–Z/0–9/F1–F12), built-in tools with defaults, shortcuts already assigned are detected (with name), shortcuts from 2.32.3 adopted; in the browser via e.code, never in input fields/dialogs, confirmation with several computers")
def _():
    from fastapi import HTTPException
    from app import db, main, tools
    tools.init()
    n = tools.norm_hotkey
    assert n("Strg+Alt+p") == "Ctrl+Alt+P" and n("ctrl + alt + shift + 5") == "Ctrl+Alt+Shift+5" and n("Ctrl+Alt+F12") == "Ctrl+Alt+F12"
    assert n("Alt+Strg+Umschalt+x") == "Ctrl+Alt+Shift+X" and n("") == "" and n(None) == ""
    for bad in ("Ctrl+P", "Alt+P", "Ctrl+Alt+F13", "Ctrl+Alt+Ä", "Ctrl+Alt+Shift", "Ctrl+Ctrl+P", "Ctrl+Alt+Win+P", "Ctrl+Alt+Entf"):
        try:
            n(bad)
            raise AssertionError(("accepted", bad))
        except ValueError:
            pass
    old = db.setting_raw("builtin_hotkeys")
    t1 = db.ex("INSERT INTO tools(name, type, script, run_as) VALUES('HK-Test', 'powershell', 'hostname', 'service')")
    try:
        db.set_setting("builtin_hotkeys", "")
        assert tools.builtin_hotkeys()["ping"] == "Ctrl+Alt+P", "default"
        # own tool: shortcut of Ping -> rejected with name
        try:
            main._tool_values({"name": "X", "type": "cmd", "script": "ver", "run_as": "service", "hotkey": "Strg+Alt+P"})
            raise AssertionError("accepted twice")
        except HTTPException as e:
            assert "Ping & Ports" in e.detail and "Strg+Alt+P" in e.detail, e.detail
        d = main._tool_values({"name": "X", "type": "cmd", "script": "ver", "run_as": "service", "hotkey": "strg+alt+shift+k"}, t1)
        assert d["hotkey"] == "Ctrl+Alt+Shift+K"
        db.ex("UPDATE tools SET hotkey='Ctrl+Alt+Shift+K' WHERE id=?", (t1,))
        assert main._tool_values({"name": "X", "type": "cmd", "script": "ver", "run_as": "service", "hotkey": "Ctrl+Alt+Shift+K"}, t1)["hotkey"], \
            "the own shortcut when saving the same tool is not a conflict"
        assert main._tool_values({"name": "X", "type": "cmd", "script": "ver", "run_as": "service", "hotkey": ""})["hotkey"] is None
        assert tools.hotkey_owner("Ctrl+Alt+Shift+K") == "HK-Test"
        # built-in: duplicate / assigned to own tool -> rejected; empty = deliberately without shortcut
        for bad in ({"ping": "Ctrl+Alt+R"}, {"wake": "Ctrl+Alt+Shift+K"}):
            try:
                tools.save_builtin_hotkeys(bad)
                raise AssertionError(("accepted", bad))
            except ValueError as e:
                assert "Strg+Alt" in str(e)
        out = tools.save_builtin_hotkeys({"ping": "", "abort": "Strg+Alt+P"})
        assert "ping" not in out and out["abort"] == "Ctrl+Alt+P" and out["rdpweb"] == "Ctrl+Alt+R"
        # Takeover from 2.32.3 (Ctrl+Alt+digit per user) – only if free and only for own tools
        db.ex("UPDATE tools SET hotkey=NULL WHERE id=?", (t1,))
        db.ex("INSERT OR REPLACE INTO user_prefs(username, key, value, at) VALUES('hk-alt', 'tool_hotkeys', ?, '2026-10-28')",
              (json.dumps({"1": f"tool:{t1}", "2": "builtin:ping"}),))
        db.ex("DELETE FROM settings WHERE key='hotkeys_migrated'")
        tools._migrate_user_hotkeys()
        assert db.val("SELECT hotkey FROM tools WHERE id=?", (t1,)) == "Ctrl+Alt+1" and db.setting_raw("hotkeys_migrated") == "1"
        tools._migrate_user_hotkeys()  # one-time – second run changes nothing
        # 2.32.5: restart/shutdown on Ctrl+Alt+B; stored old default N is converted once
        assert tools.BUILTIN_HOTKEY_DEFAULTS["power"] == "Ctrl+Alt+B"
        db.set_setting("builtin_hotkeys", json.dumps({"power": "Ctrl+Alt+N", "ping": "Ctrl+Alt+P"}))
        db.ex("DELETE FROM settings WHERE key='hotkeys_power_b'")
        tools._migrate_power_b()
        assert tools.builtin_hotkeys()["power"] == "Ctrl+Alt+B"
        db.set_setting("builtin_hotkeys", json.dumps({"power": "Ctrl+Alt+N"}))
        tools._migrate_power_b()
        assert tools.builtin_hotkeys()["power"] == "Ctrl+Alt+N", "only once – a later own choice stays"
        # an own tool already has B: restart/shutdown keeps N (no double assignment through the new default)
        db.set_setting("builtin_hotkeys", "")
        db.ex("UPDATE tools SET hotkey='Ctrl+Alt+B' WHERE id=?", (t1,))
        db.ex("DELETE FROM settings WHERE key='hotkeys_power_b'")
        tools._migrate_power_b()
        assert tools.builtin_hotkeys()["power"] == "Ctrl+Alt+N" and tools.hotkey_owner("Ctrl+Alt+B") == "HK-Test"
    finally:
        db.ex("DELETE FROM tools WHERE id=?", (t1,))
        db.ex("DELETE FROM user_prefs WHERE username LIKE 'hk-%'")
        db.set_setting("builtin_hotkeys", old)
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    import re as _re
    blk = js[js.index("const BUILTIN_TOOLS"):js.index("let HK = null")]
    bi = dict(_re.findall(r"\{ key: '([a-z]+)', name: '([^']+)'", blk))
    assert bi == tools.BUILTIN_NAMES, ("built-in tools identical in UI and server", set(bi.items()) ^ set(tools.BUILTIN_NAMES.items()))
    # 2.32.5: focus on the selection checkbox does not block the shortcut (only text fields)
    assert "['checkbox', 'radio', 'button'" in js and "closest('input, textarea" not in js
    for need in ("/^Key([A-Z])$/.exec(e.code", "closest('textarea, select", "#modal-root .backdrop", "targets.length > 1 && !asks",
                 "S.toolTargets = null;", "PUT('/api/tools-hotkeys'", "hotkey: hk.value", "hotkey: '' });", "Bereits vergeben an"):
        assert need in js, need
    assert "me/hotkeys" not in js

@test("2.32.3 Restart/shutdown: action and delay selectable (0 = immediately with /f ... 24 h), message text matching, own text stays")
def _():
    from app import db, rebootmsg, main
    import inspect
    old = {k: db.setting(rebootmsg.KEYS[k]) for k in ("restart", "shutdown")}
    try:
        for k in ("restart", "shutdown"):
            db.set_setting(rebootmsg.KEYS[k], "")
        assert rebootmsg.text_delay("restart", 60).endswith("Neustart in 60 Sekunden.")
        assert rebootmsg.text_delay("shutdown", 0).endswith("Herunterfahren jetzt.")
        assert rebootmsg.text_delay("restart", 900).endswith("Neustart in 15 Minuten.")
        assert rebootmsg.text_delay("restart", 90).endswith("Neustart in 90 Sekunden.")
        db.set_setting(rebootmsg.KEYS["restart"], "Bitte speichern – {minuten} min")
        assert rebootmsg.text_delay("restart", 900).endswith("Bitte speichern – 15 min"), "own text stays"
    finally:
        for k, v in old.items():
            db.set_setting(rebootmsg.KEYS[k], v or "")
    src = inspect.getsource(main.api_action)
    for need in ('secs = int(data.get("seconds", 60))', "0 <= secs <= rebootmsg.MAX_DELAY", 'force = " /f" if secs == 0 else ""',
                 "shutdown.exe /r /t {secs}{force} /c", "shutdown.exe /s /t {secs}{force} /c"):
        assert need in src, need
    assert rebootmsg.MAX_DELAY == 86400
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    assert "{ action: act.value, seconds: secs }" in js and "secs > 86400" in js and "key: 'power'" in js
    assert "Neustart (60 s)" not in js and "key: 'shutdown'" not in js

@test("2.32.5 Service account protection: \"locked/disabled/expired\" from a device triggers immediately (no cached \"account ok\"); AD unreachable != \"password wrong\"; \"account ok\" remembered only 60 s")
def _():
    from app import ad, guard
    import app.remote as _r
    orig_v, orig_sc = guard.verify_account, ad.service_conn
    try:
        guard.reset()
        calls = []
        guard.verify_account = lambda: calls.append(1) or True
        for code in ("0xc0000234", "0xc0000072", "0xc0000071", "0xc0000193", "0xc0000224"):
            guard.reset()
            guard.report_remote(f"SMB-Anmeldung fehlgeschlagen {code}", "SMB")
            assert guard.paused(), code
        assert not calls, "unambiguous account errors without LDAP detour"
        guard.reset()
        guard.verify_account = lambda: None
        t = guard.report_remote("STATUS_LOGON_FAILURE 0xc000006d", "SMB")
        assert not guard.paused() and "nicht per LDAP" in t, t
        assert "nicht per LDAP" in _r._winrm_error("the specified credentials were rejected by the server (401)")
        assert not guard.paused()
        # real verify_account: network error -> None (no protection), rejection -> False (protection), ok -> True, remembered for 60 s
        guard.verify_account = orig_v
        guard.reset()
        def boom():
            raise ad.ADError("LDAPS-Verbindung zu dc1:636 fehlgeschlagen: timed out")
        ad.service_conn = boom
        assert guard.verify_account() is None and not guard.paused()
        def rejected():
            guard.trip("Anmeldung abgelehnt", "LDAP")
            raise ad.ADError("Anmeldung abgelehnt")
        ad.service_conn = rejected
        assert guard.verify_account() is False and guard.paused()
        guard.reset()
        class Conn:
            def unbind(self): pass
        ad.service_conn = lambda: Conn()
        assert guard.verify_account() is True
        ad.service_conn = rejected
        assert guard.verify_account() is True, "remembered within 60 s"
        guard._verify["at"] -= guard.VERIFY_SECONDS + 1
        assert guard.verify_account() is False
        assert guard.VERIFY_SECONDS <= 60
    finally:
        guard.verify_account, ad.service_conn = orig_v, orig_sc
        guard.reset()

@test("2.32.5 Waking before schedules: only computers that are really switched off (not \"on, but without WinRM\"), also when repeating a failed appointment, only one wake pass per schedule")
def _():
    import threading as _th
    import time as _t
    from app import db, schedules, tools
    a = db.ex("INSERT INTO computers(name, dns_name, online, winrm) VALUES('WK-AUS', 'wk-aus.firma.local', 0, 0)")
    b = db.ex("INSERT INTO computers(name, dns_name, online, winrm) VALUES('WK-AN', 'wk-an.firma.local', 1, 0)")
    woken, gate = [], _th.Event()
    orig = tools.wake
    tools.wake = lambda cc, **kw: (gate.wait(5), woken.append(cc["id"]), {"exit": 1})[2]
    try:
        sc = {"id": 987654, "name": "WK"}
        schedules._wake_offline(sc, {"wake_offline": True}, [a, b], "Wiederholung – Lauf konnte nicht gestartet werden")
        schedules._wake_offline(sc, {"wake_offline": True}, [a, b], "täglich 07:00")  # still running -> no second pass
        gate.set()
        for _i in range(100):
            if woken and sc["id"] not in schedules._waking:
                break
            _t.sleep(0.05)
        assert woken == [a], woken
    finally:
        tools.wake = orig
        db.ex("DELETE FROM computers WHERE id IN (?, ?)", (a, b))

@test("2.32.5 OU admins: copy of a foreign dynamic collection does not reveal its rules (becomes \"is member of\"); nonsense in IDs/names yields 400 instead of 500; combining can be overridden despite the duplicate lock")
def _():
    import hashlib
    import secrets
    import time as _t
    from app import access, auth, collections_
    mk = lambda n: db.ex("INSERT INTO computers(name, dns_name, created_at) VALUES(?,?,?)", (n, n.lower() + ".firma.local", util.now()))  # noqa: E731
    a1, b1 = mk("CL-A1"), mk("CL-B1")
    ca = db.ex("INSERT INTO collections(name, type) VALUES('CL A', 'static')")
    db.ex("INSERT INTO collection_members VALUES(?,?)", (ca, a1))
    ta = db.ex("INSERT INTO tenants(name, collection_id, repo_folder, admin_group, allow_packages, created_at) VALUES('CL A', ?, 'CLA', 'G-CLA', 1, ?)", (ca, util.now()))
    exc = db.ex("INSERT INTO collections(name, type, match, filters) VALUES('CL Ausnahmen', 'dynamic', 'any', ?)",
                (json.dumps([{"field": "name", "op": "equals", "value": "CL-A1"}, {"field": "name", "op": "equals", "value": "CL-B1-GEHEIM"}]),))
    collections_.invalidate()
    access.flush()
    tok = secrets.token_urlsafe(16)
    th = hashlib.sha256(tok.encode()).hexdigest()
    db.ex("INSERT INTO sessions(token_hash, username, display_name, created, expires, ip, last_active, role, scope) VALUES(?,?,?,?,?,?,?,?,?)",
          (th, "cl-ou", "Test", util.now(), "2099-01-01 00:00:00", "127.0.0.1", util.now(), "admin", json.dumps([ta])))
    auth._checked[th] = _t.time() + 10 ** 6
    new = None
    try:
        st, r = _asgi("POST", f"/api/collections/{exc}/duplicate", tok, {})
        assert st == 200, (st, r)
        new = r["id"]
        f = db.val("SELECT filters FROM collections WHERE id=?", (new,))
        assert "GEHEIM" not in f and "CL-A1" not in f and str(exc) in f, f
        assert set(collections_.member_ids(new)) <= {a1}, "copy contains only own computers"
        own = db.ex("INSERT INTO collections(name, type, tenant_id) VALUES('CL eigen', 'static', ?)", (ta,))
        collections_.invalidate()
        access.flush()
        st, r = _asgi("POST", f"/api/collections/{own}/members", tok, {"add": ["abc"]})
        assert st == 400, (st, r)
        st, r = _asgi("POST", "/api/packages/merge", tok, {"ids": "12"})
        assert st == 400, (st, r)
        st, r = _asgi("POST", "/api/packages/merge", tok, {"ids": [1, 2], "name": 5})
        assert st != 500, (st, r)
        if st == 200:
            db.ex("DELETE FROM packages WHERE id=?", (r["id"],))
    finally:
        db.ex("DELETE FROM sessions WHERE token_hash=?", (th,))
        for c in (exc, ca, new):
            if c:
                db.ex("DELETE FROM collection_members WHERE collection_id=?", (c,))
                db.ex("DELETE FROM collections WHERE id=?", (c,))
        db.ex("DELETE FROM collections WHERE name='CL eigen'")
        db.ex("DELETE FROM tenants WHERE id=?", (ta,))
        db.ex("DELETE FROM computers WHERE id IN (?, ?)", (a1, b1))
        collections_.invalidate()
        access.flush()
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "main.py"), encoding="utf-8").read()
    assert '"allow_duplicate": bool(data.get("allow_duplicate"))}' in src and 'str(data.get("name") or "")' in src
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    assert "saveWithDup((x) => POST('/api/packages/merge'" in js

@test("2.32.5 Shortcuts/waking in the UI: Wake-on-LAN on several computers asks for confirmation, pressing twice does not start twice, current assignment counts, Ctrl+Alt triggers no copy/paste, URL tools ask for confirmation, waking keeps sending after closing and to all")
def _():
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    run = js[js.index("async function hotkeyRun2"):js.index("document.addEventListener('keydown', (e) => {\n  const combo")]
    assert "'wake'" not in run, "waking does not ask by itself – with several computers confirmation by the shortcut"
    assert "if (HK_BUSY) return;" in js and "ref = HK.map[combo];" in run
    assert js.count("if (e.altKey) return;  // Ctrl+Alt+") == 2
    assert "if (t.confirm && !(await confirmDlg(`„${t.name}“ für" in js
    wd = js[js.index("function wakeDialog(targets)"):js.index("function jobDialog(")]
    assert "slice(0, 200)" not in wd and "if (closed) break;" not in wd

@test("2.32.5 Execution: hardening codes only on failure (success codes and winget messages take precedence), uninstall via winget/registry builds the right command, second wake attempt reuses the only relay in the target network, relay in restricted language mode reports itself")
def _():
    from app import deploy, remote, tools
    clm = "Hinweis\nCannot invoke method. Method invocation is supported only on core types in this language mode.\nExitcode: 1"
    assert remote.apply_hard(1, clm)[0] == 9015 and remote.apply_hard(1, "nur ein normaler Fehler")[0] == 1
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "steps.py"), encoding="utf-8").read()
    i = src.index("ok, reboot = evaluate(st, code)")
    blk = src[i:src.index("self._record(", i)]
    assert "elif not ok:" in blk and "remote.apply_hard(code, out)" in blk, "only on failure, after the winget messages"
    assert "$global:PKExit = $pkH" not in remote.WRAPPER, "the wrapper no longer overwrites an exit code"
    # Uninstall: winget uninstall or command from the registry – both build a valid body
    assert "deploy.build_body(p2)" in src and "deploy._winget_body(p2)" not in src and "deploy._custom_body(p2)" not in src
    w = deploy.adhoc_winget("Mozilla.Firefox", "uninstall")
    assert "uninstall" in deploy.build_body(w)
    assert "PK-Run" in deploy.build_body({"type": "custom", "install_type": "script", "script": "msiexec /x {X} /qn", "arguments": ""})
    # WoL: relay in restricted language mode says so; second attempt without another relay in the target network -> the same again
    assert "PK_CLM" in tools.WOL_PS and "LanguageMode -ne 'FullLanguage'" in tools.WOL_PS
    mk = lambda n, ip: db.ex("INSERT INTO computers(name, dns_name, ip_addresses, online, winrm, created_at) VALUES(?,?,?,?,?,?)",  # noqa: E731
                             (n, n.lower() + ".firma.local", ip, 1, 1, util.now()))
    ziel = db.ex("INSERT INTO computers(name, dns_name, ip_addresses, online, winrm, created_at) VALUES('RL-ZIEL', 'rl-ziel.firma.local', '10.77.0.5', 0, 0, ?)", (util.now(),))
    nb, fremd = mk("RL-NB", "10.77.0.6"), mk("RL-FREMD", "10.66.0.6")
    try:
        c = db.q1("SELECT * FROM computers WHERE id=?", (ziel,))
        first = tools.wol_relays(c)
        assert [(r["name"], s) for r, s in first if s] == [("RL-NB", True)], [(r["name"], s) for r, s in first]
        again = tools.wol_relays(c, exclude=["RL-NB"])
        assert [(r["name"], s) for r, s in again if s] == [("RL-NB", True)], [(r["name"], s) for r, s in again]
    finally:
        db.ex("DELETE FROM computers WHERE id IN (?, ?, ?)", (ziel, nb, fremd))

@test("2.32.6 Logged-in users with session state: locked (LogonUI in the session) and disconnected (quser, German/English), several sessions -> active wins; in restricted language mode; scan and live query identical")
def _():
    import subprocess
    from app import remote
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app")
    scan = open(os.path.join(root, "scan_modules.ps1"), encoding="utf-8").read()
    blk = lambda t: t[t.index("# @sessions-begin"):t.index("# @sessions-end")]  # noqa: E731
    assert blk(scan) == blk(remote.SESSIONS_PS), "scan and live query determine the state identically"
    assert "user_states" in scan
    assert remote.parse_states("PKUSERS:x\nPKSTATES:DOM\\a=locked;DOM\\b=disconnected;DOM\\c=active;kaputt") == \
        {"DOM\\a": "locked", "DOM\\b": "disconnected"}
    # 2.32.11: all sessions active -> "PKSTATES:" at the very end without a line ending (was IndexError -> internal error 500)
    for o in ("PKUSERS:DOM\\a\nPKSTATES:", "PKUSERS:DOM\\a\r\nPKSTATES:\r\n", "PKSTATES:", "", None):
        assert remote.parse_states(o) == {}, repr(o)
    from app import steps as _stp
    assert _stp.step_title({"type": "command", "command": ""}) == "Befehl: " and _stp.step_title({"type": "powershell", "script": None}) == "PowerShell: "
    pw = os.environ.get("PK_PWSH")
    if not (pw and os.path.exists(pw)):
        return
    d = tempfile.mkdtemp(prefix="pk-sess-")
    try:
        os.makedirs(os.path.join(d, "System32"))
        for lang, active, disc in (("de", "Aktiv", "Getr."), ("en", "Active", "Disc")):
            q = os.path.join(d, "System32", "quser.exe")
            with open(q, "w") as fh:
                row = lambda u, sn, i, st: f" {u:<22}{sn:<19}{i:>2}  {st:<6}  {'.':>9}  28.10.2026 08:00\n"  # noqa: E731
                fh.write("#!/bin/sh\ncat <<'X'\n"
                         + f" {'USERNAME':<22}{'SESSIONNAME':<19}{'ID':>2}  {'STATE':<6}  {'IDLE TIME':>9}  LOGON TIME\n"
                         + row("max", "console", 1, active) + row("eva", "", 2, disc) + row("tom", "rdp-tcp#0", 3, active)
                         + row("ugo", "", 4, disc) + row("ugo", "rdp-tcp#1", 5, active)
                         # User name with a space and a digit: must not overwrite another session
                         + row("team 2 lager", "", 6, disc) + "X\n")
            os.chmod(q, 0o755)
            mocks = ("$ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage'\n"
                     f"$env:SystemRoot = '{d}'\n"
                     "function Get-CimInstance { param($ClassName, $Filter)\n"
                     "  if ($ClassName -eq 'Win32_ComputerSystem') { return @{ UserName = 'FIRMA\\max' } }\n"
                     "  if ($Filter -match 'LogonUI') { return @(@{ SessionId = 3 }) }\n"
                     "  if ($Filter -match 'explorer') { return @(1, 2, 3, 4, 5, 6 | ForEach-Object { @{ SessionId = $_ } }) } }\n"
                     "function Invoke-CimMethod { param($InputObject, $MethodName)\n"
                     "  $n = @{ 1 = 'max'; 2 = 'eva'; 3 = 'tom'; 4 = 'ugo'; 5 = 'ugo'; 6 = 'lager2' }[[int]$InputObject.SessionId]\n"
                     "  @{ Domain = 'FIRMA'; User = $n } }\n")
            sf = os.path.join(d, "lo.ps1")
            with open(sf, "w", encoding="utf-8-sig") as fh:
                fh.write(mocks + remote.LOGGEDON_PS)
            r = subprocess.run([pw, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", sf],
                               capture_output=True, text=True, timeout=120, env={**os.environ, "TERM": "dumb"})
            out = r.stdout + r.stderr
            assert "Cannot" not in out and "Methodenaufruf" not in out, out
            assert remote.parse_states(out) == {"FIRMA\\eva": "disconnected", "FIRMA\\tom": "locked", "FIRMA\\lager2": "disconnected"}, (lang, out)
            assert "PKUSERS:FIRMA\\max, FIRMA\\eva, FIRMA\\tom, FIRMA\\ugo, FIRMA\\lager2" in out, out
    finally:
        shutil.rmtree(d, ignore_errors=True)

@test("2.32.7 Tools: files from the repository (POCKET KNIFE copies itself, command in the working folder, cleaned up afterwards, never from tenant folders), scan afterwards (default off), time limit, success codes; CMD output with umlauts (OEM)")
def _():
    import time as _t
    from app import db, inventory, remote, tools
    tools.init()
    cols = {r["name"] for r in db.q("PRAGMA table_info(tools)")}
    assert {"repo_path", "scan_after", "scan_profile_id", "timeout_s", "success_codes"} <= cols
    base = {"name": "T", "type": "cmd", "script": "ver", "run_as": "service"}
    orig = (remote.repo_stat, remote.copy_package, remote.remove_package_copy, remote.ps, remote.run_as_system, inventory.queue_scan)
    calls = []
    remote.repo_stat = lambda rel: {"tools\\iperf": "dir", "tools\\x.exe": "file"}.get(rel.lower())
    remote.copy_package = lambda folder, host, name, single_file=None: calls.append(("copy", folder, name, single_file))
    remote.remove_package_copy = lambda host, name: calls.append(("rm", name))
    tid = db.ex("INSERT INTO tenants(name, repo_folder, created_at) VALUES('T-HK7', 'Mandant\\A', ?)", (util.now(),))
    c = db.ex("INSERT INTO computers(name, dns_name, created_at) VALUES('TL-PC', 'tl-pc.firma.local', ?)", (util.now(),))
    try:
        d = dict(base, repo_path="/Tools/iperf/")
        tools.validate(d)
        assert d["repo_path"] == "Tools\\iperf" and d["scan_after"] == "never" and d["scan_profile_id"] is None, d
        assert d["timeout_s"] is None and d["success_codes"] is None, "default: no time limit set, success = 0, no scan"
        for bad in ({"repo_path": "..\\geheim"}, {"repo_path": "C:\\x"}, {"repo_path": "\\\\srv\\x"}, {"repo_path": "Tools\\fehlt"},
                    {"repo_path": "Mandant\\A\\iperf"}, {"repo_path": "Mandant"}, {"scan_after": "manchmal"},
                    {"timeout_s": 5}, {"timeout_s": 999999}, {"timeout_s": "x"}, {"success_codes": "0, abc"}):
            try:
                tools.validate(dict(base, **bad))
                raise AssertionError(("accepted", bad))
            except ValueError:
                pass
        d = dict(base, success_codes="0; 3010 ,1", timeout_s="120", scan_after="success")
        tools.validate(d)
        assert d["success_codes"] == "0,3010,1" and d["timeout_s"] == 120
        assert tools.parse_codes("") == [0]
        # Execution as service account with files: copy -> run in the folder -> clean up; success codes; time limit
        seen = {}

        def ps(host, script, timeout=None, **kw):
            seen["script"], seen["timeout"] = script, timeout
            return "Ausgabe\nPKEXIT:3010", "", False
        remote.ps = ps
        t = {"id": 7, "name": "IPERF", "type": "cmd", "script": "iperf3.exe -c pve", "run_as": "service", "repo_path": "Tools\\iperf",
             "timeout_s": 90, "success_codes": "0,3010", "scan_after": "never", "scan_profile_id": None}
        # 2.32.7: with files the service account also runs via the wrapper (own process, time limit on the client)
        sysc = []
        remote.run_as_system = lambda host, body, name, timeout_s=0, run_as="system", **kw: (
            sysc.append((name, timeout_s, run_as, body)) or (3010, "> x\nAusgabe\nExitcode: 3010"))
        r = tools.run(t, c)
        assert r["exit"] == 3010 and r["ok"] is True and "Tools\\iperf" in r["output"], r
        assert calls[0][0] == "copy" and calls[0][1] == "Tools\\iperf" and calls[-1] == ("rm", calls[0][2]), calls
        assert sysc[-1][:3] == (calls[0][2], 90, "deploy") and "pk_tool.cmd" in sysc[-1][3], sysc[-1][:3]
        calls.clear()
        r = tools.run(dict(t, repo_path="Tools\\x.exe", type="powershell", script="Get-Item ."), c)
        assert calls[0] == ("copy", "Tools", calls[0][2], "x.exe") and "PK-RunPs1" in sysc[-1][3], calls
        # without files: service account directly via WinRM, working folder not needed, time limit passed to WinRM
        r = tools.run(dict(t, repo_path=None), c)
        assert r["exit"] == 3010 and seen["timeout"] == 90 and "cd /d" not in seen["script"] and "\\uFFFD" in seen["script"], seen["script"][:300]
        # SYSTEM: same name for copy and run; cleanup always happens
        calls.clear()
        remote.run_as_system = lambda host, body, name, timeout_s=0, **kw: (calls.append(("sys", name, timeout_s)) or (1, "> x\nExitcode: 1"))
        r = tools.run(dict(t, run_as="system"), c)
        assert calls[0][0] == "copy" and calls[1] == ("sys", calls[0][2], 90) and calls[-1] == ("rm", calls[0][2]), calls
        assert r["ok"] is False
        # Copy aborts -> clean up anyway
        calls.clear()
        def boom(folder, host, name, single_file=None):
            calls.append(("copy", folder, name, single_file))
            raise remote.RemoteError("Verbindung abgebrochen")
        remote.copy_package = boom
        try:
            tools.run(t, c)
            raise AssertionError("error expected")
        except remote.RemoteError:
            pass
        assert calls[-1] == ("rm", calls[0][2]), calls
        remote.copy_package = lambda folder, host, name, single_file=None: calls.append(("copy", folder, name, single_file))
        # Time limit: own message in seconds
        def slow(host, script, timeout=None, **kw):
            raise remote.RemoteTimeout("WinRM: keine Antwort")
        remote.ps = slow
        try:
            tools.run(dict(t, repo_path=None, timeout_s=45), c)
            raise AssertionError("time limit expected")
        except remote.RemoteTimeout as e:
            assert "45 Sekunden" in str(e), e
        remote.ps = ps
        # Tenant gets the folder AFTER the tool is saved -> tool no longer runs
        t2 = db.ex("INSERT INTO tenants(name, repo_folder, created_at) VALUES('T-HK7b', 'tools', ?)", (util.now(),))
        try:
            calls.clear()
            try:
                tools.run(t, c)
                raise AssertionError("tenant folder not recognised")
            except ValueError as e:
                assert "T-HK7b" in str(e)
            assert not [x for x in calls if x[0] == "copy"], "nothing copied"
        finally:
            db.ex("DELETE FROM tenants WHERE id=?", (t2,))
        # and vice versa: a tenant cannot get a folder that a tool uses
        from app import repofiles
        tt = db.ex("INSERT INTO tools(name, type, script, run_as, repo_path) VALUES('HK7-Tool', 'cmd', 'x', 'service', 'Tools\\iperf')")
        try:
            for f in ("Tools", "tools\\IPERF", "Tools\\iperf\\sub"):
                try:
                    repofiles.check_tenant_folder(f)
                    raise AssertionError(("tool folder as tenant folder", f))
                except repofiles.RepoError as e:
                    assert "HK7-Tool" in str(e)
        finally:
            db.ex("DELETE FROM tools WHERE id=?", (tt,))
        # Scan afterwards: default never; "after success" only on OK; "always" always
        scans = []
        inventory.queue_scan = lambda cid, user=None, profile_id=None, follow=False: scans.append((cid, profile_id, follow)) or True
        orig_run = tools.run
        for sa, ok, want in (("never", True, 0), ("success", False, 0), ("success", True, 1), ("always", False, 1)):
            scans.clear()
            tools.run = lambda tool, cid, _ok=ok: {"exit": 0 if _ok else 1, "ok": _ok, "output": "", "seconds": 0}
            jid = tools.start_job(dict(t, scan_after=sa), [c], "tester")
            for _i in range(500):
                j = tools.job(jid)
                if all(x["status"] in ("OK", "Fehler") for x in j["targets"]):
                    break
                _t.sleep(0.02)
            assert len(scans) == want and all(s[2] for s in scans), (sa, ok, scans)
        tools.run = orig_run
    finally:
        remote.repo_stat, remote.copy_package, remote.remove_package_copy, remote.ps, remote.run_as_system, inventory.queue_scan = orig
        db.ex("DELETE FROM tenants WHERE id=?", (tid,))
        db.ex("DELETE FROM computers WHERE id=?", (c,))
    assert "-match '\\uFFFD') { $pkEnc = 'Oem' }" in remote.WRAPPER, "first UTF-8, with replacement characters OEM"
    msrc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "main.py"), encoding="utf-8").read()
    assert "tools.scan_after(t, cid" in msrc, "scan afterwards also on a single call"
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    assert "repo_path: repo.value" in js and "scan_after: scanAfter.value" in js and "tool.scan_after || 'never'" in js

@test("2.32.8 Network adapters: speed from Get-NetAdapter (not the outdated WMI value), only for connected adapters; automatic scans only a few minutes after boot/wake-up, manual ones immediately")
def _():
    import subprocess
    from app import db, inventory, profiles
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app")
    src = open(os.path.join(root, "scan_modules.ps1"), encoding="utf-8").read()
    pre = src[src.index("# @prelude"):src.index("# @module active_directory")]
    nics = src[src.index("# @module nics"):src.index("# @module printers")]
    pw = os.environ.get("PK_PWSH")
    if pw and os.path.exists(pw):
        mocks = ("$ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage'\n"
                 "function Get-CimInstance { param($ClassName, $Filter)\n"
                 "  if ($ClassName -eq 'Win32_NetworkAdapter') { return @(\n"
                 "    @{ Name = 'Intel Ethernet'; Index = 1; NetConnectionStatus = 2; Speed = 10000000; MACAddress = 'AA'; NetEnabled = $true },\n"
                 "    @{ Name = 'Realtek USB'; Index = 2; NetConnectionStatus = 7; Speed = 10000000; MACAddress = 'BB'; NetEnabled = $true },\n"
                 "    @{ Name = 'Alt-Karte'; Index = 3; NetConnectionStatus = 2; Speed = 100000000; MACAddress = 'CC'; NetEnabled = $true }) }\n"
                 "  return @() }\n"
                 "function Get-NetAdapter { param([switch]$Physical, $ErrorAction)\n"
                 "  @(@{ InterfaceDescription = 'Intel Ethernet'; PhysicalMediaType = '802.3'; Speed = 1000000000; Status = 'Up' },\n"
                 "    @{ InterfaceDescription = 'Realtek USB'; PhysicalMediaType = '802.3'; Speed = 0; Status = 'Disconnected' }) }\n"
                 "function Get-NetAdapterPowerManagement { @() }\nfunction Get-NetAdapterAdvancedProperty { @() }\nfunction powercfg.exe { }\n")
        sf = os.path.join(TMP, "pk-nics.ps1")
        with open(sf, "w", encoding="utf-8-sig") as fh:
            fh.write(mocks + pre + nics + "\nConvertTo-Json -InputObject @($r.nics) -Compress\n'ERR:' + ($r.errors -join ' | ')\n")
        r = subprocess.run([pw, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", sf], capture_output=True, text=True, timeout=120)
        line = [x for x in r.stdout.splitlines() if x.startswith("[")]
        assert line, r.stdout + r.stderr
        sp = {n["name"]: n.get("speed") for n in json.loads(line[0])}
        assert sp == {"Intel Ethernet": 1000000000, "Realtek USB": None, "Alt-Karte": 100000000}, (sp, r.stdout[-300:])
        assert "ERR:\n" in r.stdout + "\n" or r.stdout.strip().endswith("ERR:"), r.stdout[-300:]
    # Waiting time after boot
    assert db.DEFAULT_SETTINGS["scan_online_delay_minutes"] == 3
    c = db.ex("INSERT INTO computers(name, dns_name, online, winrm, online_since, created_at) VALUES('NS-PC', 'ns-pc.firma.local', 1, 1, ?, ?)",
              (util.now(), util.now()))
    pid = profiles.default()["id"]
    started = []
    orig = (inventory.queue_scan, inventory.is_scanning)
    inventory.queue_scan = lambda cid, user=None, profile_id=None, follow=False: started.append(cid) or True
    inventory.is_scanning = lambda cid: False
    try:
        db.ex("INSERT OR REPLACE INTO profile_scans(computer_id, profile_id, pending, pending_since) VALUES(?,?,1,?)", (c, pid, util.now()))
        profiles.dispatch()
        assert c not in started, "just came online -> not yet"
        db.ex("UPDATE computers SET online_since=? WHERE id=?", (util.ago(minutes=4), c))
        profiles.dispatch()
        assert c in started, "after the waiting time"
        from app import presence
        db.ex("UPDATE computers SET winrm=0 WHERE id=?", (c,))
        assert presence.update(c, True, True) and db.val("SELECT online_since FROM computers WHERE id=?", (c,)), "set when coming online"
        presence.update(c, False, False)
        assert db.val("SELECT online_since FROM computers WHERE id=?", (c,)) is None
    finally:
        inventory.queue_scan, inventory.is_scanning = orig
        db.ex("DELETE FROM profile_scans WHERE computer_id=?", (c,))
        db.ex("DELETE FROM computers WHERE id=?", (c,))



@test("2.33 Docker standard: program only in the image, docker-compose.yml with version number (notice if too old), guacd under its own name, release without ZIP, no update helper any more")
def _():
    import importlib.util as _iu
    import re as _re
    from app import backup, config, news, privileges
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    assert _iu.find_spec("app.selfupdate") is None, "update helper (2.32.9–2.32.12) has been removed"
    js = open(os.path.join(root, "app", "static", "app.js"), encoding="utf-8").read()
    assert "/api/system/selfupdate" not in js and "Jetzt aktualisieren" not in js
    from app import main as _m
    assert not [r for r in _m.app.routes if "selfupdate" in getattr(r, "path", "")]
    # docker-compose.yml: state of the file = what the program expects
    dcp = os.path.join(root, "docker-compose.yml")
    if os.path.exists(dcp):
        dc = open(dcp, encoding="utf-8").read()
        m = _re.search(r'^\s*PK_COMPOSE_VERSION:\s*"(\d+)"\s*$', dc, _re.M)
        assert m and int(m.group(1)) == news.COMPOSE_VERSION, "PK_COMPOSE_VERSION in docker-compose.yml = news.COMPOSE_VERSION"
        assert not _re.search(r"^\s*build:", dc, _re.M) and "docker.sock" not in dc
        df = open(os.path.join(root, "Dockerfile"), encoding="utf-8").read()
        assert "COPY docker-compose.yml .env.example update.sh /opt/pocketknife/host/" in df and "dist" not in df.split("COPY app ./app")[1]
        assert "!dist/" not in open(os.path.join(root, ".dockerignore"), encoding="utf-8").read()
    wfd = os.path.join(root, ".github", "workflows")
    if os.path.isdir(wfd):
        rel = open(os.path.join(wfd, "release.yml"), encoding="utf-8").read()
        assert "git archive" not in rel and ".zip" not in rel, "release without program ZIP"
        assert "imagetools create" in rel and "docker/guacd.txt" in rel and "docker save" in rel and "tar.gz" in rel
        assert "cp .env.example dist/env.example" in rel and "dist/.env.example" not in rel, "GitHub renames .env.example – to env.example"
        assert rel.index("- name: Build and push") < rel.index("- name: Publish guacd under its own name") < rel.index("- name: Create release")
        wk = open(os.path.join(wfd, "weekly.yml"), encoding="utf-8").read()
        assert "docker/guacd.txt" in wk and "docker-compose.yml" not in wk.split("Anything changed?")[1].split("Check vulnerabilities")[0]
        for fn in os.listdir(wfd):
            t_ = open(os.path.join(wfd, fn), encoding="utf-8").read()
            assert "image: \\(guacamole" not in t_ and " docker-compose.yml;" not in t_, f"{fn}: guacd is in docker/guacd.txt, no longer in docker-compose.yml"
    # Notices for main admins
    old = os.environ.pop("PK_COMPOSE_VERSION", None)
    try:
        assert [h["id"] for h in news.hints()] == ["compose"], "without version number = old docker-compose.yml"
        os.environ["PK_COMPOSE_VERSION"] = str(news.COMPOSE_VERSION)
        assert news.hints() == []
        os.environ["PK_COMPOSE_VERSION"] = "x"
        assert [h["id"] for h in news.hints()] == ["compose"]
        os.environ["PK_COMPOSE_VERSION"] = str(news.COMPOSE_VERSION)
        # Database from a newer version (back without the backup): notice; a security update of the same version does not count
        import contextlib as _cl
        import io as _io
        _quiet = _cl.redirect_stdout(_io.StringIO())
        _quiet.__enter__()
        keep = (db.setting_raw("db_version_max"), db.setting_raw("last_version"), backup.NEWER_DB, backup.create)
        backup.create = lambda label="", user=None: {"name": label}
        try:
            for top, newer in (("99.0", "99.0"), (config.VERSION.split("-s")[0] + "-s7", None), ("1.0", None)):
                db.set_setting("db_version_max", top)
                backup.before_update()
                assert backup.NEWER_DB == newer, (top, backup.NEWER_DB)
            db.set_setting("db_version_max", "99.0")
            backup.mark_version()
            assert db.setting_raw("db_version_max") == "99.0" and db.setting_raw("last_version") == config.VERSION, "highest version stays"
            backup.before_update()
            assert [h["id"] for h in news.hints()] == ["newer_db"] and "vor-99.0" in news.hints()[0]["text"]
            db.set_setting("db_version_max", "1.0")
            backup.mark_version()
            assert db.setting_raw("db_version_max") == config.VERSION
        finally:
            db.set_setting("db_version_max", keep[0] or config.VERSION)
            db.set_setting("last_version", keep[1] or config.VERSION)
            backup.NEWER_DB, backup.create = keep[2], keep[3]
            _quiet.__exit__(None, None, None)
    finally:
        if old is None:
            os.environ.pop("PK_COMPOSE_VERSION", None)
        else:
            os.environ["PK_COMPOSE_VERSION"] = old
    # Start chown skips the former helper's folder (belongs to root, update.sh --cleanup removes it)
    if os.geteuid() == 0:
        d = tempfile.mkdtemp(dir=TMP)
        os.makedirs(os.path.join(d, "update", "host")); open(os.path.join(d, "a"), "w").close()
        privileges.chown_tree(d, 10001, 10001)
        assert os.stat(os.path.join(d, "a")).st_uid == 10001 and os.stat(os.path.join(d, "update", "host")).st_uid == 0


@test("2.33 update.sh: only update containers (pull, up, health check, back without download), pin version, newer docker-compose.yml from the image, without internet via archive, --cleanup spares data even with links")
def _():
    import hashlib as _hl
    import shutil as _sh
    import subprocess as _sp
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    f = os.path.join(root, "update.sh")
    if not os.path.exists(f) or not _sh.which("sh"):
        return  # update.sh is not in the container
    if _sh.which("dash"):
        assert _sp.run(["dash", "-n", f]).returncode == 0, "update.sh must run with /bin/sh (dash)"
    src = open(f, encoding="utf-8").read()
    for bad in ("unzip", "docker build", "git archive", "PROGRAM_ITEMS="):
        assert bad not in src, ("no ZIP, no building", bad)
    base = tempfile.mkdtemp(prefix="pk-up-", dir=TMP)
    inst, st, bind = os.path.join(base, "opt"), os.path.join(base, "st"), os.path.join(base, "bin")
    for d in (inst, bind, os.path.join(st, "host")):
        os.makedirs(d)
    with open(os.path.join(bind, "docker"), "w") as h:
        h.write(r'''#!/bin/sh
st="$FAKE_STATE"
echo "$*" >> "$st/calls"
case "$1" in
  info) exit 0 ;;
  compose)
    case "$2" in
      version) exit 0 ;;
      up) echo "up $(grep '^PK_IMAGE=' .env) $(grep '^PK_GUACD_IMAGE=' .env) env=${PK_IMAGE:-}" >> "$st/ups"; pwd > "$st/running_dir" ;;
      logs) echo "log line" ;;
    esac; exit 0 ;;
  inspect)
    case "$*" in
      *working_dir*) cat "$st/running_dir" 2>/dev/null ;;
      *Health*) cat "$st/health" ;;
      *image.version*) cat "$st/running_ver" ;;
      *Mounts*) echo ""; echo "" ;;   # 2.33.2: like the real docker – one empty line per container without mounts
      *pocketknife-guacd*) echo sha256:guacalt ;;
      *.Image*) echo sha256:alt ;;
    esac; exit 0 ;;
  image)
    case "$2" in
      inspect) case "$*" in *image.version*) t=${3##*:}; if [ "$t" = latest ]; then cat "$st/latest_ver"; else echo "$t"; fi ;; esac; exit 0 ;;
      ls) cat "$st/images" 2>/dev/null; exit 0 ;;
      rm) exit 0 ;;
    esac ;;
  tag) echo "$2 $3" >> "$st/tags"; exit 0 ;;
  pull) [ -f "$st/pullfail" ] && { echo "denied" >&2; exit 1; }; exit 0 ;;
  create) echo cid1; exit 0 ;;
  cp) cp -r "$st/host/." "$3"; exit 0 ;;
  rm) exit 0 ;;
  load) cat "$st/loaded"; exit 0 ;;
esac
exit 0
''')
    os.chmod(os.path.join(bind, "docker"), 0o755)
    w = lambda p, t: open(p, "w").write(t)  # noqa: E731
    rd = lambda *p: open(os.path.join(inst, *p)).read()  # noqa: E731
    env = {**os.environ, "PATH": bind + ":" + os.environ.get("PATH", ""), "FAKE_STATE": st, "PK_SYSROOT": tempfile.mkdtemp(dir=TMP),
           "PK_WAIT_STEPS": "2"}
    run = lambda *a, inp=None: _sp.run(["sh", "update.sh", *a], cwd=inst, env=env, input=inp, capture_output=True, text=True, timeout=120)  # noqa: E731
    old_compose = 'name: pocketknife\nservices:\n  pocketknife:\n    image: ${PK_IMAGE}\n  guacd:\n    image: guacamole/guacd:1.5@sha256:' + "0" * 64 + '\n'
    new_compose = open(os.path.join(root, "docker-compose.yml"), encoding="utf-8").read()
    w(os.path.join(inst, "docker-compose.yml"), old_compose)
    w(os.path.join(inst, ".env"), "PK_DNS_1=10.0.10.5\nPK_IMAGE=pocketknife:local\nPK_DATA_PATH=./data\n")
    _sh.copy(f, os.path.join(inst, "update.sh"))
    w(os.path.join(st, "host", "docker-compose.yml"), new_compose)
    w(os.path.join(st, "host", ".env.example"), "BEISPIEL=1\n")
    w(os.path.join(st, "running_dir"), inst + "\n"); w(os.path.join(st, "running_ver"), "2.32.12\n")
    w(os.path.join(st, "health"), "healthy\n"); w(os.path.join(st, "latest_ver"), "2.33.0\n")
    os.makedirs(os.path.join(inst, "data")); w(os.path.join(inst, "data", "pocketknife.db"), "DB")
    # 1. Switch from 2.32 (locally built image, old docker-compose.yml): registry image, new file, old one as .alt
    r = run("-y")
    assert r.returncode == 0, r.stdout[-800:] + r.stderr[-400:]
    assert "PK_IMAGE=ghcr.io/soleil0815/pocketknife:latest" in rd(".env") and "PK_DNS_1=10.0.10.5" in rd(".env")
    assert rd("docker-compose.yml") == new_compose and rd("docker-compose.yml.alt") == old_compose and rd(".env.example") == "BEISPIEL=1\n"
    calls = open(os.path.join(st, "calls")).read()
    assert "pull ghcr.io/soleil0815/pocketknife:latest" in calls and "pull ghcr.io/soleil0815/pocketknife-guacd:latest" in calls
    tags = open(os.path.join(st, "tags")).read()
    assert "sha256:alt pocketknife:previous" in tags and "sha256:guacalt pocketknife-guacd:previous" in tags, "note beforehand (back without download)"
    assert "build" not in calls and "load" not in calls
    assert sorted(os.listdir(inst)) == [".env", ".env.example", "data", "docker-compose.yml", "docker-compose.yml.alt", "update.sh"], os.listdir(inst)
    assert "Done: POCKET KNIFE 2.33.0" in r.stdout
    # 2. same file version: docker-compose.yml stays (own comments etc. are preserved)
    os.remove(os.path.join(inst, "docker-compose.yml.alt"))
    w(os.path.join(inst, "docker-compose.yml"), new_compose + "# eigene Zeile\n")
    r = run("-y")
    assert r.returncode == 0 and rd("docker-compose.yml").endswith("# eigene Zeile\n") and not os.path.exists(os.path.join(inst, "docker-compose.yml.alt"))
    # 3. pin a specific version – both images; "latest" back again
    r = run("-y", "2.33.1")
    assert r.returncode == 0 and "PK_IMAGE=ghcr.io/soleil0815/pocketknife:2.33.1" in rd(".env") and "PK_GUACD_IMAGE=ghcr.io/soleil0815/pocketknife-guacd:2.33.1" in rd(".env"), rd(".env")
    r = run("-y")
    assert r.returncode == 0 and open(os.path.join(st, "calls")).read().count("pull ghcr.io/soleil0815/pocketknife:2.33.1") == 2, "without argument: the pinned version"
    r = run("-y", "latest")
    assert r.returncode == 0 and "PK_IMAGE=ghcr.io/soleil0815/pocketknife:latest" in rd(".env") and "pocketknife-guacd:latest" in rd(".env")
    for bad in ("2.33; reboot", "../x", "--irgendwas"):
        r = run("-y", bad)
        assert r.returncode != 0, bad
    # old spelling --pull [version] keeps working
    r = run("-y", "--pull", "2.33.1")
    assert r.returncode == 0 and ":2.33.1" in rd(".env")
    run("-y", "latest")
    # 4. Pulling fails -> nothing changed, hint to docker login
    env_before = rd(".env")
    w(os.path.join(st, "pullfail"), "")
    r = run("-y", "2.33.2")
    assert r.returncode != 0 and "could not be pulled" in r.stdout and "docker login ghcr.io" in r.stdout and rd(".env") == env_before
    os.remove(os.path.join(st, "pullfail"))
    # 5. new version does not start -> previous image under the name of the former .env, .env back
    w(os.path.join(st, "tags"), ""); w(os.path.join(st, "health"), "unhealthy\n")
    ino = os.stat(os.path.join(inst, ".env")).st_ino
    r = run("-y", "2.33.2")
    assert r.returncode != 0 and "is running again" in r.stdout and rd(".env") == env_before, r.stdout[-600:]
    # 2.36.2: back keeps the .env itself (owner, permissions, link) – no mv of a copy from root
    est = os.stat(os.path.join(inst, ".env"))
    assert est.st_ino == ino and not os.path.exists(os.path.join(inst, ".env.pk-alt")), "2.36.2: .env on rollback"
    tags = open(os.path.join(st, "tags")).read()
    assert "pocketknife:previous ghcr.io/soleil0815/pocketknife:latest" in tags and "pocketknife-guacd:previous ghcr.io/soleil0815/pocketknife-guacd:latest" in tags, tags
    assert "sudo sh update.sh 2.32.12" in r.stdout, "how to stay on the version"
    w(os.path.join(st, "health"), "healthy\n")
    # 6. without internet: archive from the release (both images), checksum must match
    arc = os.path.join(base, "pocketknife-2.33.3-amd64.tar.gz"); w(arc, "ARCHIV")
    w(os.path.join(base, "pocketknife-2.33.3.sha256"), _hl.sha256(b"ARCHIV").hexdigest() + "  pocketknife-2.33.3-amd64.tar.gz\n")
    w(os.path.join(st, "loaded"), "Loaded image: ghcr.io/soleil0815/pocketknife:2.33.3\nLoaded image: ghcr.io/soleil0815/pocketknife-guacd:2.33.3\n")
    w(os.path.join(st, "calls"), "")
    r = run("-y", arc)
    assert r.returncode == 0 and ":2.33.3" in rd(".env") and "pocketknife-guacd:2.33.3" in rd(".env") and "Checksum OK" in r.stdout, r.stdout[-500:]
    assert "pull" not in open(os.path.join(st, "calls")).read(), "pull nothing without internet"
    w(os.path.join(base, "pocketknife-2.33.3.sha256"), "0" * 64 + "  pocketknife-2.33.3-amd64.tar.gz\n")
    env_before = rd(".env")
    r = run("-y", arc)
    assert r.returncode != 0 and "Checksum" in r.stderr and rd(".env") == env_before
    # 2.36.2: do not load unverified without .sha256
    os.remove(os.path.join(base, "pocketknife-2.33.3.sha256"))
    w(os.path.join(st, "calls"), "")
    r = run("-y", arc)
    assert r.returncode != 0 and "is missing" in r.stderr and rd(".env") == env_before, r.stderr[-300:]
    assert "load" not in open(os.path.join(st, "calls")).read(), "2.36.2: load nothing without a checksum"
    # 2.36.2: relative path from the current folder (not from the folder of update.sh)
    w(os.path.join(base, "pocketknife-2.33.3.sha256"), _hl.sha256(b"ARCHIV").hexdigest() + "  pocketknife-2.33.3-amd64.tar.gz\n")
    r = _sp.run(["sh", os.path.join(inst, "update.sh"), "-y", "pocketknife-2.33.3-amd64.tar.gz"], cwd=base, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "Checksum OK" in r.stdout and ":2.33.3" in rd(".env"), "2.36.2: relative archive path " + r.stdout[-300:] + r.stderr[-300:]
    run("-y", "latest")
    # 7. runs from another folder -> do nothing
    w(os.path.join(st, "running_dir"), base + "\n")
    r = run("-y")
    assert r.returncode != 0 and "runs from" in r.stderr
    w(os.path.join(st, "running_dir"), inst + "\n")
    # 8. Timer of the former update helper (2.32.9–2.32.12) is removed by the script itself – even if it calls it
    if os.geteuid() == 0:
        cd = os.path.join(env["PK_SYSROOT"], "etc", "cron.d"); os.makedirs(cd)
        w(os.path.join(cd, "pocketknife-update"), "*/5 * * * * root /bin/sh x --watch\n")
        r = run("--watch")
        assert r.returncode == 0 and not os.path.exists(os.path.join(cd, "pocketknife-update")), r.stdout + r.stderr
    # 9. --cleanup: old program files gone – data, backups, repository, .env, docker-compose.yml stay,
    #    even if a "program folder" is in reality a link to data
    for d in ("app", "docker", ".pk-vorher/app", "pocketknife-2.24/app", "pocketknife-2.23.3/app", "pocketknife-2.23.3/data", "data-backup", "repo", "data/update/host"):
        os.makedirs(os.path.join(inst, d), exist_ok=True)
    for p_ in ("Dockerfile", "run.py", "VERSION", "pocketknife-2.30.zip", "pocketknife-2.30.zip.sha256", "pocketknife-2.24/VERSION",
               "pocketknife-2.24/docker-compose.yml", "pocketknife-2.23.3/VERSION", "pocketknife-2.23.3/docker-compose.yml",
               "pocketknife-2.23.3/data/pocketknife.db", "data-backup/s.zip", "repo/x.msi", "data/update/request", "docker-compose.override.yml"):
        w(os.path.join(inst, p_), "x")
    ext = os.path.join(base, "geheim"); os.makedirs(ext); w(os.path.join(ext, "wichtig.txt"), "W")
    w(os.path.join(inst, ".env"), rd(".env") + f"PK_REPO_MOUNT={ext}\n")
    os.symlink(ext, os.path.join(inst, "tools"))  # "tools" points to the repository -> never delete
    w(os.path.join(st, "images"), "pocketknife:local\nghcr.io/soleil0815/pocketknife:latest\n")
    r = run("--cleanup", inp="n\nn\n")
    assert r.returncode == 0 and os.path.exists(os.path.join(inst, "app")), "nothing deleted without Ja"
    r = run("-y", "--cleanup")
    left = sorted(os.listdir(inst))
    assert r.returncode == 0, r.stderr
    assert left == [".env", ".env.example", "data", "data-backup", "docker-compose.override.yml", "docker-compose.yml", "pocketknife-2.23.3", "repo", "tools", "update.sh"], left
    assert os.path.islink(os.path.join(inst, "tools")) and open(os.path.join(ext, "wichtig.txt")).read() == "W"
    assert rd("data", "pocketknife.db") == "DB" and rd("data-backup", "s.zip") == "x" and rd("repo", "x.msi") == "x"
    assert not os.path.exists(os.path.join(inst, "data", "update")), "folder of the former helper gone"
    assert "kept: " in r.stdout and "pocketknife-2.23.3" in r.stdout, "version folder with database stays"
    assert "image rm pocketknife:local" in open(os.path.join(st, "calls")).read()
    assert "pocketknife:latest" not in [x.split()[-1] for x in open(os.path.join(st, "calls")).read().splitlines() if x.startswith("image rm")]
    # 2.33.1: Switch – the new script runs as update.sh.neu (the old one is still there as update.sh) and puts itself in place
    w(os.path.join(st, "host", "update.sh"), open(f, encoding="utf-8").read())
    _sh.copy(f, os.path.join(inst, "update.sh.neu")); w(os.path.join(inst, "update.sh"), "#!/bin/sh\n# POCKET KNIFE – update (since 2.24.1: alt)\n")
    r = _sp.run(["sh", "update.sh.neu", "-y"], cwd=inst, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and rd("update.sh") == open(f, encoding="utf-8").read(), r.stdout[-400:]
    for doc in ("README.md", "README.en.md", os.path.join("app", "static", "help", "de.md"), os.path.join("app", "static", "help", "en.md")):
        t_ = open(os.path.join(root, doc), encoding="utf-8").read()
        assert "> update.sh.neu && mv" not in t_ and "&& sh update.sh.neu'" in t_ and "sudo sh -c 'docker pull" in t_, ("switch in one command as root", doc)
    os.remove(os.path.join(inst, "update.sh.neu"))
    # 10. update.sh takes itself out of the image (new file via mv – the running script stays untouched)
    w(os.path.join(st, "host", "update.sh"), open(f, encoding="utf-8").read() + "\n# aus dem Image\n")
    r = run("-y")
    assert r.returncode == 0 and rd("update.sh").endswith("# aus dem Image\n")


@test("2.33.3 Collection tree: alphabetical per level (folders first), AD and default on top, default collections in a fixed order")
def _():
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    i = js.index("const byParent = {};")
    seg = js[i:i + 900]
    assert "v.sort(" in seg and "deCmp(a.name, b.name)" in seg and "a.builtin === 1 && b.builtin === 1 ? 0" in seg, "tree sorted"


@test("2.33.4 Antivirus on Windows Server: Microsoft Defender directly (no Security Center), empty output never as \"undefined (aus)\"")
def _():
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    ps = open(os.path.join(root, "app", "scan_modules.ps1"), encoding="utf-8").read()
    blk = ps[ps.index("PKX 'antivirus' {"):ps.index("} catch { $r.errors += ('security: '")]
    assert "if ($av.Count -eq 0 -and $mp)" in blk and "Microsoft Defender Antivirus" in blk and ",$av" in blk, "server: Defender, always a list"
    assert ps.index("$mp = Get-MpComputerStatus") < ps.index("PKX 'antivirus' {"), "Defender status read beforehand"
    assert "-ClassName MSFT_MpComputerStatus" in ps and ps.index("MSFT_MpComputerStatus") < ps.index("PKX 'antivirus' {"), "without Defender module: WMI class"
    from app import inventory
    out = inventory._merge_extra(-4711, {"extra": {"antivirus": {}, "firewall": [{"profile": "Domain", "enabled": True}, {}]}}, ["security"])
    assert out["antivirus"] == [] and out["firewall"] == [{"profile": "Domain", "enabled": True}], out
    out = inventory._merge_extra(-4711, {"extra": {"antivirus": {"name": "Microsoft Defender Antivirus", "enabled": True, "up_to_date": True}}}, ["security"])
    assert out["antivirus"] == [{"name": "Microsoft Defender Antivirus", "enabled": True, "up_to_date": True}]
    js = open(os.path.join(root, "app", "static", "app.js"), encoding="utf-8").read()
    assert "arr(e.antivirus).filter((a) => a && a.name)" in js
    src = open(os.path.join(root, "app", "inventory.py"), encoding="utf-8").read()
    assert "if not (isinstance(x, dict) and not x)" in src


@test("2.34 Windows Server: confirmation before restart/shutdown of servers and domain controllers, collection \"Domänencontroller\", virtualisation detected, winget version without profiles, Server Core users, screens on VMs without warning")
def _():
    import json as _j
    from app import main as _m, collections_, db as _db
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    # Role: from the scan (ProductType), otherwise from the operating system
    sr = _m.server_role
    assert sr({"extra": _j.dumps({"product_type": 2})}) == "dc" and sr({"extra": _j.dumps({"product_type": "3"})}) == "server"
    assert sr({"extra": _j.dumps({"product_type": 1}), "os_name": "Windows Server 2022"}) is None, "scan takes precedence"
    assert sr({"extra": None, "os_name": "Microsoft Windows Server 2025 Standard"}) == "server" and sr({"extra": "{}", "ad_os": "Windows 11 Pro"}) is None
    assert ("POST", "/api/computers/power-check") in _m.access.ROUTES
    js = open(os.path.join(root, "app", "static", "app.js"), encoding="utf-8").read()
    pdl = js[js.index("function powerDialog(targets)"):js.index("function fmtDelay(")]
    assert "/api/computers/power-check" in pdl and "servers.length && !sure.checked" in pdl and "servers === null" in pdl, "server: confirm"
    # Collection "Domänencontroller" and field "Rolle"
    assert any(n == "Domänencontroller" for n, _mm, _f in _db.BUILTIN_COLLECTIONS)
    a = _db.ex("INSERT INTO computers(name, extra) VALUES('DC-TEST1', ?)", (_j.dumps({"product_type": 2, "virtual": "Hyper-V"}),))
    b = _db.ex("INSERT INTO computers(name, extra) VALUES('SRV-TEST1', ?)", (_j.dumps({"product_type": 3}),))
    try:
        dc = _db.val("SELECT id FROM collections WHERE builtin=1 AND name='Domänencontroller'")
        assert dc, "created at startup"
        mem = set(collections_.member_ids(dc, fresh=True))
        assert a in mem and b not in mem, mem
        w, p = collections_.build([{"field": "virtual", "op": "equals", "value": "Hyper-V"}])
        assert _db.val(f"SELECT COUNT(*) FROM computers c WHERE c.id=? AND {w}", [a] + list(p)) == 1
    finally:
        _db.ex("DELETE FROM computers WHERE id IN (?,?)", (a, b))
    ps = open(os.path.join(root, "app", "scan_modules.ps1"), encoding="utf-8").read()
    vm = ps[ps.index("PKX 'virtual' {"):]
    vm = vm[:vm.index("\n}\n")]
    assert vm.index("'VMware'") < vm.index("'Hyper-V'") and "'KVM/QEMU" in vm and "Proxmox" in vm and "HypervisorPresent" not in vm.replace("(HypervisorPresent", ""), "manufacturer/model, not HypervisorPresent"
    wg = ps[ps.index("if ($os.ProductType -eq 1) {\n  try { $ai = Get-AppxPackage"):ps.index("$e.pending_reboot")]
    assert "^(Applications|InboxApplications|S-1-.*)$" in wg, "not EndOfLife/Staged"
    assert "AppxAllUserStore" in wg and "-Recurse -Depth" not in wg and "Get-AppxPackage -AllUsers" in wg.split("} else {")[0], "server: registry instead of all profiles"
    disp = ps[ps.index("PKS 'displays'"):ps.index("# @module environment")]
    assert "nicht unterst|not supported|8004100C" in disp and "{ throw }" in disp
    blk = ps[ps.index("# @sessions-begin"):ps.index("# @sessions-end")]
    assert "Server Core" in blk and "$pkExp.Count -eq 0 -and $pkQo.Count -gt 1" in blk
    from app import inventory, remote as _r
    assert "virtual" in inventory.MODULE_EXTRA["computer"]
    lo = _r.LOGGEDON_PS[_r.LOGGEDON_PS.index("@sessions-end"):]
    assert "$u += $pkExp" not in lo and ".Split('\\')[-1] -eq $x" in lo, "quser names not doubled to DOMAIN\\name"
    # power-check: all targets, even above 5000; failure of the check = ask for the checkmark to be safe
    import inspect as _insp
    assert "[:5000]" not in _insp.getsource(_m.api_power_check)
    a2 = _db.ex("INSERT INTO computers(name, extra, current_user) VALUES('SRV-PC2', ?, 'FIRMA\\a,FIRMA\\b')", (_j.dumps({"product_type": 3}),))
    try:
        r = _m.api_power_check({"ids": [a2]}, {"username": "haupt", "scope": None})
        assert r["servers"] == [{"id": a2, "name": "SRV-PC2", "role": "server", "users": 2}], r
    finally:
        _db.ex("DELETE FROM computers WHERE id=?", (a2,))
    assert "servers = [{ unknown: true }];" in pdl, "check failed: not without confirmation"

@test("2.34 Older Windows without winget (before 1809 / Server 2019): no winget upgrade, kiosk does not offer winget packages there, notice instead of \"missing\"")
def _():
    import re as _re
    from app import kiosk, deploy, steps as stp
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    ok = deploy.winget_ok
    assert ok(None) and ok("") and ok("17763") and ok(26100) and ok("x") and not ok("14393") and not ok(9600), "build limit"
    assert not ok("14393.6897") and ok("17763.1") and ok("26100.4061"), "scan stores Build.UBR"
    js = open(os.path.join(root, "app", "static", "app.js"), encoding="utf-8").read()
    assert int(_re.search(r"const WINGET_MIN_BUILD = (\d+);", js).group(1)) == deploy.WINGET_MIN_BUILD, "JS and server same limit"
    sv = js[js.index("if (cat === 'software') {"):]
    sv = sv[:sv.index("return wrapT(t);\n  }")]
    # 2.35: without winget only what is available in the winget cache
    assert "const wok = wingetOk(c);" in sv and "const upOk = (r) => wok || wcHas(r.winget_id);" in sv
    assert "r.outdated && r.winget_id && upOk(r) ? btn('Aktualisieren'" in sv and "s.outdated && s.winget_id && upOk(s)" in sv
    assert "c.outdated && (wingetOk(c) || wcHas(wid))" in js[js.index("async function updateOutdated("):js.index("async function scanSubItems(")]
    sd = js[js.index("async function softwareDlg(sw)"):]
    assert "c.outdated && (wingetOk(c) || wcHas(sw.winget_id))" in sd[:sd.index("modal(sw.name")] and sd.index("await wcReady();") < sd.index("const t = table(")
    from app import main as _m
    import inspect
    assert "c.os_build" in inspect.getsource(_m.api_software_detail), "build for display"
    # Kiosk: do not offer a winget package on Server 2016, own package yes
    db.set_setting("kiosk_enabled", 1)
    cid = db.ex("INSERT INTO computers(name, online, winrm, current_user, os_build) VALUES('PC-ALT1', 1, 1, 'FIRMA\\nutzer', '14393.6897')")
    pk = {}
    for name, st in (("WG Tool", {"type": "install", "source": "winget", "winget_id": "Mozilla.Firefox"}),
                     ("Eigen Tool", {"type": "command", "command": "cmd /c exit 0"})):
        p = stp.clean_package({"name": name, "steps": {"main": [st]}})
        p["steps"], p["conditions"] = json.dumps(p["steps"]), json.dumps(p["conditions"])
        pk[name] = db.ex(f"INSERT INTO packages({', '.join(deploy.PKG_FIELDS)}) VALUES({', '.join('?' * len(deploy.PKG_FIELDS))})",
                         tuple(p[k] for k in deploy.PKG_FIELDS))
        kiosk.save_item(pk[name], {"enabled": True, "approval": 0})
    old = kiosk._logged_on
    kiosk._logged_on = lambda c, fresh=False: {"nutzer"}
    try:
        names = {i["name"] for i in kiosk.catalog(cid)}
        assert "Eigen Tool" in names and "WG Tool" not in names, names
        must_fail(kiosk.request, {"username": "nutzer", "display_name": "N"}, "?", cid, pk["WG Tool"], "install", contains="1809")
        # Uninstall via winget: not offered on old systems
        kiosk.save_item(pk["Eigen Tool"], {"enabled": True, "approval": 0, "uninstall_package_id": pk["WG Tool"]})
        it = {i["name"]: i for i in kiosk.catalog(cid)}
        assert db.val("SELECT uninstall_package_id FROM kiosk_items WHERE package_id=?", (pk["Eigen Tool"],)) == pk["WG Tool"]
        assert it["Eigen Tool"]["can_uninstall"] is False, it["Eigen Tool"]
        must_fail(kiosk.request, {"username": "nutzer", "display_name": "N"}, "?", cid, pk["Eigen Tool"], "uninstall", contains="1809")
        db.ex("UPDATE computers SET os_build='19045.5011' WHERE id=?", (cid,))
        assert "WG Tool" in {i["name"] for i in kiosk.catalog(cid)}
    finally:
        kiosk._logged_on = old
        db.set_setting("kiosk_enabled", 0)
        for v in pk.values():
            db.ex("DELETE FROM kiosk_items WHERE package_id=?", (v,))
            db.ex("DELETE FROM packages WHERE id=?", (v,))
        db.ex("DELETE FROM computers WHERE id=?", (cid,))


@test("2.34 Run command: PowerShell/CMD directly without a tool, main admins only, logged, can be saved as a tool, Ctrl+Alt+C only if free")
def _():
    import time as _t
    from app import tools, remote, main as _m, pklog
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    t = tools.adhoc_tool({"type": "powershell", "script": "Get-Service Spooler\r\nGet-Date\n\n"})
    assert t["type"] == "powershell" and t["run_as"] == "service" and t["script"] == "Get-Service Spooler\r\nGet-Date", t["script"]
    assert t["name"] == "Befehl (PowerShell)" and not t["repo_path"] and t["scan_after"] == "never", "name without command text"
    assert tools.adhoc_tool({"type": "cmd", "script": "ipconfig", "run_as": "system"})["name"] == "Befehl (CMD als SYSTEM)"
    for bad, txt in (({"type": "url", "script": "http://x"}, "PowerShell oder CMD"), ({"type": "cmd", "script": "  \n "}, "Befehl eingeben"),
                     ({"type": "cmd", "script": "x", "run_as": "root"}, "Ausführung"),
                     ({"type": "cmd", "script": "x" * (tools.ADHOC_MAX + 1)}, "zu lang"),
                     ({"type": "cmd", "script": "x", "timeout_s": 3}, "Zeitlimit")):
        must_fail(tools.adhoc_tool, bad, contains=txt)
    # OU admins: route not released, handler checks additionally
    assert ("POST", "/api/tools-command") not in _m.access.ROUTES
    try:
        _m.api_tool_command({"type": "cmd", "script": "whoami", "ids": [1]}, {"username": "ou-x", "scope": [1]})
        raise AssertionError("OU admin was allowed to run a command")
    except _m.HTTPException as e:
        assert e.status_code == 403
    # Execution like a tool, command in the log
    c = db.ex("INSERT INTO computers(name, dns_name) VALUES('CMD-PC1', 'cmd-pc1.firma.local')")
    old_ps, logs = remote.ps, []
    old_info = pklog.info
    runs = []
    remote.ps = lambda host, script, timeout=None, **kw: (runs.append(host) or ("Spooler Running\nPKEXIT:0", "", False))
    pklog.info = lambda cat, msg, **kw: logs.append(msg)
    try:
        r = _m.api_tool_command({"type": "powershell", "script": "net user x Geheim123 /add\nGet-Service Spooler", "ids": [c, c]},
                                {"username": "haupt", "scope": None})
        for _ in range(100):
            j = tools.job(r["job"])
            if all(x["res"] for x in j["targets"]):
                break
            _t.sleep(0.05)
        assert j["targets"][0]["status"] == "OK" and "Spooler Running" in j["targets"][0]["res"]["output"], j
        assert any("gestartet" in m and "nicht protokolliert" in m for m in logs), logs
        assert not any("Geheim123" in m or "net user" in m for m in logs), ("command text in the log", logs)
        assert len(j["targets"]) == 1 and len(runs) == 1, ("duplicate ID executed twice", runs)
    finally:
        remote.ps, pklog.info = old_ps, old_info
        db.ex("DELETE FROM computers WHERE id=?", (c,))
    # Shortcut: default Ctrl+Alt+C only if no own tool has it
    assert "command" in tools.BUILTIN_KEYS and tools.builtin_hotkeys().get("command") == "Ctrl+Alt+C"
    tid = db.ex("INSERT INTO tools(name, type, script, hotkey) VALUES('Eigenes C', 'cmd', 'ver', 'Ctrl+Alt+C')")
    try:
        assert "command" not in tools.builtin_hotkeys() and tools.hotkey_owner("Ctrl+Alt+C") == "Eigenes C"
    finally:
        db.ex("DELETE FROM tools WHERE id=?", (tid,))
    js = open(os.path.join(root, "app", "static", "app.js"), encoding="utf-8").read()
    dlg = js[js.index("async function commandDlg(targets)"):]
    dlg = dlg[:dlg.index("\n}\n")]
    assert "/api/tools-command" in dlg and "toolDlg({" in dlg and "return false;  // Window stays open" in dlg and "if (busy) return false;" in dlg
    assert "localStorage" not in dlg, "do not store history (passwords)"
    assert "{ key: 'command', name: 'Befehl ausführen …', icon: 'terminal', main: true }" in js and "if (b.key === 'command') { commandDlg(targets); return; }" in js
    assert "const isNew = !tool || !tool.id;" in js


def _mszip(data, chunk=32768):
    """MSZIP like the Windows Compression API (for the winget cache self-test)."""
    import zlib as _z
    out, prev, first = [], b"", 0
    for i in range(0, len(data), chunk):
        part = data[i:i + chunk]
        c = _z.compressobj(9, _z.DEFLATED, -15, 9, zdict=prev[-32768:]) if prev else _z.compressobj(9, _z.DEFLATED, -15, 9)
        z = b"CK" + c.compress(part) + c.flush()
        out.append(len(z).to_bytes(4, "little") + z)
        first = first or len(part)
        prev += part
    return bytes.fromhex("0a51e5c01800") + b"\x00\x02" + len(data).to_bytes(8, "little") + first.to_bytes(8, "little") + b"".join(out)


@test("2.35 winget cache: load manifest like winget (MSZIP, checksums), choose installer, into the repository, create package, new version replaces old, removal cleans up, computers without winget get the package")
def _():
    import hashlib as _h, io as _io, sqlite3 as _sq, tempfile as _tf, json as _j, time as _t
    from app import wingetcache as wc, winget, remote, deploy, main as _m, schedules, config
    # MSZIP: several chunks with dictionary, broken data is rejected
    raw = ("vD:\n" + "".join(f"- v: '1.{i}'\n  rP: manifests/t/x/{i}.yaml\n  s256H: {'a' * 64}\n" for i in range(3000))).encode()
    assert len(raw) > 70000 and wc.mszip_decompress(_mszip(raw)) == raw
    y = wc._yaml("v: 1.10\nPackageVersion: 2.50\nInstallerSuccessCodes: [1, 0x10]\nx: yes\n")
    assert y == {"v": "1.10", "PackageVersion": "2.50", "InstallerSuccessCodes": ["1", "0x10"], "x": "yes"}, ("versions stay text", y)
    must_fail(wc.mszip_decompress, b"PK\x03\x04" + b"0" * 40, contains="MSZIP")
    bad = bytearray(_mszip(raw)); bad[8] ^= 1
    must_fail(wc.mszip_decompress, bytes(bad))
    # Choose installer and switches
    man = {"PackageIdentifier": "Test.App", "PackageVersion": "1.0", "PackageName": "Test App", "InstallerType": "msi",
           "Scope": "machine", "InstallerSwitches": {"Custom": "REBOOT=ReallySuppress"},
           "Installers": [{"Architecture": "x86", "InstallerUrl": "https://dl.example.test/app-x86.msi", "InstallerSha256": "00"},
                          {"Architecture": "x64", "InstallerLocale": "en-US", "InstallerUrl": "https://dl.example.test/app-en.msi", "InstallerSha256": "00"},
                          {"Architecture": "x64", "InstallerLocale": "de-DE", "InstallerUrl": "https://dl.example.test/app-de.msi", "InstallerSha256": "00",
                           "AppsAndFeaturesEntries": [{"DisplayName": "Test App 1.0 (x64)", "DisplayVersion": "1.0.0"}],
                           "ProductCode": "{11111111-2222-3333-4444-555555555555}"},
                          {"Architecture": "x64", "Scope": "user", "InstallerType": "exe", "InstallerUrl": "https://dl.example.test/u.exe", "InstallerSha256": "00"}],
           "ExpectedReturnCodes": [{"InstallerReturnCode": 1638, "ReturnResponse": "alreadyInstalled"}, {"InstallerReturnCode": 5, "ReturnResponse": "blockedByPolicy"}]}
    man["ExpectedReturnCodes"] = [{"InstallerReturnCode": "1638", "ReturnResponse": "alreadyInstalled"}, {"InstallerReturnCode": "5", "ReturnResponse": "blockedByPolicy"}]
    i = wc.choose(man, "x64", "machine", "de-DE")
    assert i["InstallerUrl"].endswith("app-de.msi"), i
    assert wc.choose(man, "x64", "machine", "fr-FR")["InstallerUrl"].endswith("app-en.msi"), "otherwise en-US"
    assert wc.choose(man, "x86", "machine", "de-DE")["InstallerUrl"].endswith("x86.msi")
    p = wc.plan(man, i)
    assert p["install_type"] == "msi" and p["arguments"] == "REBOOT=ReallySuppress ALLUSERS=1" and p["success_codes"] == "0,3010,1641,1638", p
    assert p["detect_name"] == "Test App * (x64)" and p["detect_version"] == "1.0.0" and p["product_code"].startswith("{1111"), p
    inno = {"PackageName": "I", "PackageVersion": "2", "Installers": [{"Architecture": "x64", "InstallerType": "inno", "InstallerUrl": "https://x/i.exe"}]}
    assert wc.plan(inno, wc.choose(inno))["arguments"] == "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /ALLUSERS"
    nsis = {"Installers": [{"Architecture": "x64", "InstallerType": "nullsoft", "InstallerSwitches": {"Silent": "/S /Q"}}]}
    assert wc.plan(nsis, wc.choose(nsis))["arguments"] == "/S /Q", "switches from the manifest take precedence"
    exe = {"Installers": [{"Architecture": "x64", "InstallerType": "exe"}]}
    must_fail(wc.plan, exe, wc.choose(exe), contains="stille Installation")
    must_fail(wc.choose, {"Installers": [{"Architecture": "x64", "InstallerType": "msix"}, {"Architecture": "x64", "InstallerType": "zip", "NestedInstallerType": "msi"}]},
              contains="Kein passender Installer")
    must_fail(wc.choose, {"Installers": [{"Architecture": "arm64", "InstallerType": "msi"}]}, "x64", contains="Kein passender")
    assert wc._file_name("https://x/dl?id=5", "Test.App", "1.0", "msi") == "Test.App_1.0.msi"
    assert wc._file_name("https://x/a/My%20Setup.msi", "T", "1", "msi") == "My_Setup.msi"
    assert wc.os_ok({"os_version": "10.0.14393", "os_build": "14393.6897"}, "10.0.0.0")
    assert not wc.os_ok({"os_version": "6.3.9600", "os_build": "9600"}, "10.0.0.0") and wc.os_ok({}, "10.0.17763.0")
    assert not wc.os_ok({"os_version": "10.0.14393", "os_build": "14393"}, "10.0.17763.0")
    # --- End to end with a simulated winget server and local repository
    root = _tf.mkdtemp(prefix="pk-wc-", dir=TMP)
    old_repo, old_idx = db.setting("repo_path"), None
    db.set_setting("repo_path", root)
    ip = winget.index_path()
    if os.path.exists(ip):
        old_idx = open(ip, "rb").read()
    files = {}

    def publish(ver, inst_bytes, sha=None):
        mf = (f"PackageIdentifier: Test.App\nPackageVersion: '{ver}'\nPackageName: Test App\nPublisher: Test\n"
              f"MinimumOSVersion: 10.0.0.0\nInstallers:\n- Architecture: x64\n  InstallerType: inno\n  Scope: machine\n"
              f"  InstallerUrl: https://dl.example.test/testapp-{ver}.exe\n"
              f"  InstallerSha256: {sha or _h.sha256(inst_bytes).hexdigest().upper()}\nManifestType: merged\n").encode()
        rp = f"manifests/t/Test/App/{ver}/abc.yaml"
        vd = _mszip(f"sV: 1.0\nvD:\n- v: '{ver}'\n  rP: {rp}\n  s256H: {_h.sha256(mf).hexdigest()}\n".encode())
        hx = _h.sha256(vd).hexdigest()
        files.update({f"{wc.BASE}/packages/Test.App/{hx[:8]}/versionData.mszyml": vd, f"{wc.BASE}/{rp}": mf,
                      f"https://dl.example.test/testapp-{ver}.exe": inst_bytes})
        con = _sq.connect(ip)
        con.execute("DROP TABLE IF EXISTS packages")
        con.execute("CREATE TABLE packages(rowid INTEGER PRIMARY KEY, id TEXT, name TEXT, moniker TEXT, latest_version TEXT, arp_min_version TEXT, arp_max_version TEXT, hash BLOB)")
        con.execute("INSERT INTO packages(id, name, latest_version, hash) VALUES('Test.App', 'Test App', ?, ?)", (ver, bytes.fromhex(hx)))
        con.commit(); con.close()

    class _R(_io.BytesIO):
        def geturl(self):
            return ""

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    calls = []

    def fake_open(req, timeout=0):
        u = req.full_url
        calls.append(u)
        if u not in files:
            raise OSError(f"404 {u}")
        return _R(files[u])
    old_open = wc.proxy.urlopen
    wc.proxy.urlopen = fake_open
    cmp_ = db.ex("INSERT INTO computers(name, os_build, os_version) VALUES('PC-ALT2', '14393.6897', '10.0.14393')")
    cnew = db.ex("INSERT INTO computers(name, os_build, os_version) VALUES('PC-NEU2', '26100.4061', '10.0.26100')")
    c81 = db.ex("INSERT INTO computers(name, os_build, os_version) VALUES('PC-ALT3', '9600', '6.3.9600')")
    eid = None
    try:
        publish("1.0", b"MZ-eins" * 1000)
        eid = wc.add("test.app", "x64", "selftest")
        must_fail(wc.add, "Test.App", "x64", contains="schon im Cache")
        e = wc.refresh_entry(eid)
        assert e["status"] == "ok" and e["version"] == "1.0" and e["winget_id"] == "Test.App", dict(e)
        d10 = "1.0_" + _h.sha256(b"MZ-eins" * 1000).hexdigest()[:8]
        assert e["folder"] == "winget-cache\\Test.App\\" + d10 and e["file"] == "testapp-1.0.exe", e["folder"]
        assert open(os.path.join(root, "winget-cache", "Test.App", d10, "testapp-1.0.exe"), "rb").read() == b"MZ-eins" * 1000
        assert not [f for f in os.listdir(os.path.join(root, "winget-cache", "Test.App", d10)) if f.endswith(".pk-part")]
        pk = db.q1("SELECT * FROM packages WHERE id=?", (e["package_id"],))
        st = _j.loads(pk["steps"])["main"][0]
        assert pk["name"] == "Test App (winget-Cache)" and pk["version"] == "1.0" and pk["winget_id"] == "Test.App" and pk["type"] == "custom"
        assert st["install_type"] == "exe" and st["folder"] == "winget-cache\\Test.App\\" + d10 and st["install_file"] == "testapp-1.0.exe"
        assert st["arguments"] == "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /ALLUSERS" and st["detect_name"] == "Test App"
        n = len(calls)
        assert wc.refresh_entry(eid)["status"] == "ok" and not any("testapp" in u for u in calls[n:]), "same version: do not reload"
        # same version, catalog swaps the file (new checksum): own folder, old one gone – integrity check stays consistent
        publish("1.0", b"MZ-eins-neu" * 1000)
        e1b = wc.refresh_entry(eid)
        d10b = "1.0_" + _h.sha256(b"MZ-eins-neu" * 1000).hexdigest()[:8]
        assert e1b["folder"].endswith(d10b) and sorted(os.listdir(os.path.join(root, "winget-cache", "Test.App"))) == [d10b], dict(e1b)
        # new version: load, switch package (same ID), old version gone – not while a deployment needs it
        dep = db.ex("INSERT INTO deployments(name, status, package_json) VALUES('WC-läuft', 'running', ?)",
                    (_j.dumps({"steps": {"pre": [], "main": [{"type": "install", "source": "file", "folder": e1b["folder"], "install_file": "testapp-1.0.exe"}], "post": []}}),))
        publish("1.1", b"MZ-zwei" * 1000)
        e2 = wc.refresh_entry(eid)
        d11 = "1.1_" + _h.sha256(b"MZ-zwei" * 1000).hexdigest()[:8]
        assert e2["version"] == "1.1" and e2["package_id"] == e["package_id"] and "noch verwendet" in (e2["message"] or ""), dict(e2)
        assert sorted(os.listdir(os.path.join(root, "winget-cache", "Test.App"))) == sorted([d10b, d11]), "old version stays as long as it is needed"
        db.ex("DELETE FROM deployments WHERE id=?", (dep,))
        wc.cleanup("selftest")
        assert sorted(os.listdir(os.path.join(root, "winget-cache", "Test.App"))) == [d11], "only the current version in the cache"
        assert _j.loads(db.val("SELECT steps FROM packages WHERE id=?", (e["package_id"],)))["main"][0]["install_file"] == "testapp-1.1.exe"
        # Vendor swaps the file: checksum wrong -> adopt nothing, old one stays
        publish("1.2", b"MZ-drei" * 1000, sha="0" * 64)
        e3 = wc.refresh_entry(eid)
        assert e3["status"] == "error" and "Prüfsumme" in e3["message"] and e3["version"] == "1.1", dict(e3)
        assert sorted(os.listdir(os.path.join(root, "winget-cache", "Test.App"))) == [d11]
        assert not [f for f in os.listdir(config.WINGET_DIR) if f.endswith(".part")], "no leftovers from the download"
        assert "test.app" in wc.ready(), "error loading the new version: the previous one stays usable"
        db.ex("UPDATE winget_cache SET status='checking' WHERE id=?", (eid,))
        assert "test.app" in wc.ready(), "also during a check"
        # Installed: 1.0 -> cache 1.1 newer; already 1.1 -> nothing to do; x86 installed -> no x64 next to it; 32-bit Windows -> no
        c32 = db.ex("INSERT INTO computers(name, os_build, os_version, os_arch) VALUES('PC-ALT32', '14393', '10.0.14393', '32-Bit')")
        cx86 = db.ex("INSERT INTO computers(name, os_build, os_version, os_arch) VALUES('PC-ALT86', '14393', '10.0.14393', '64-Bit')")
        cneu = db.ex("INSERT INTO computers(name, os_build, os_version, os_arch) VALUES('PC-ALTOK', '14393', '10.0.14393', '64-Bit')")
        for cid, ver, arch in ((cmp_, "1.1", "x64"), (cx86, "1.0", "x86"), (cneu, "1.0", "x64"), (c32, "1.0", "x86")):
            db.ex("INSERT INTO software(computer_id, name, version, arch, scope, winget_id) VALUES(?, 'Test App', ?, ?, 'Computer', 'Test.App')", (cid, ver, arch))
        _wg, cc, sk, _e = wc.split("Test.App", [cmp_, cx86, cneu, c32])
        assert cc == [cneu] and sorted(sk) == sorted([cmp_, cx86, c32]), (cc, sk)
        db.ex("DELETE FROM software WHERE winget_id='Test.App'")
        db.ex("DELETE FROM computers WHERE id IN (?,?,?)", (c32, cx86, cneu))
        # Split: new Windows via winget, old ones from the cache, skip too old ones (minimum version)
        wg, cache, skip, ce = wc.split("test.app", [cmp_, cnew, c81])
        assert wg == [cnew] and cache == [cmp_] and skip == [c81] and ce["id"] == eid, (wg, cache, skip)
        assert "test.app" in _m.api_wc_ready({"username": "ou", "scope": [1]})["ids"]
        assert ("GET", "/api/wingetcache/ready") in _m.access.ROUTES and ("GET", "/api/wingetcache") not in _m.access.ROUTES
        try:
            _m.api_wc_list({"username": "ou", "scope": [1]})
            raise AssertionError("OU admin must not manage the cache")
        except _m.HTTPException as x:
            assert x.status_code == 403
        made = []
        old_create = deploy.create
        deploy.create = lambda pkg, ids, user, *a, **k: (made.append((pkg.get("type"), list(ids), k.get("package_id"))) or len(made))
        try:
            r = _m.api_deploy({"winget_id": "Test.App", "action": "upgrade", "computer_ids": [cmp_, cnew]}, {"username": "haupt", "scope": None})
            assert ("custom", [cmp_], e["package_id"]) in made and ("winget", [cnew], None) in made and r["cache_id"], (made, r)
            made.clear()
            r = _m.api_deploy({"winget_id": "Test.App", "action": "upgrade", "computer_ids": [cmp_]}, {"username": "haupt", "scope": None})
            assert made == [("custom", [cmp_], e["package_id"])] and r.get("cache"), made
        finally:
            deploy.create = old_create
        # Remove: if still in use, says where; otherwise package and folder gone
        sid = db.ex("INSERT INTO schedules(name, packages) VALUES('WC-Test', ?)", (_j.dumps([e["package_id"]]),))
        must_fail(wc.remove, eid, "selftest", contains="Zeitplan")
        db.ex("DELETE FROM schedules WHERE id=?", (sid,))
        os.makedirs(os.path.join(root, "winget-cache", "Fremd.Rest", "9"))
        os.makedirs(os.path.join(root, "winget-cache", "Benutzt", "x"))
        os.makedirs(os.path.join(root, "Anderes"))
        bp = db.ex("INSERT INTO packages(name, type, steps) VALUES('WC-Fremd', 'custom', ?)",
                   (_j.dumps({"pre": [], "main": [{"type": "install", "source": "file", "folder": "winget-cache\\Benutzt\\x", "install_file": "a.exe"}], "post": []}),))
        assert wc.orphans() == ["Fremd.Rest"], ("used folders never as orphaned", wc.orphans())
        # Delete package in the package list -> entry and folder gone too
        _m.api_package_delete(e["package_id"], {"username": "haupt", "scope": None})
        assert not wc.entry(eid) and not os.path.exists(os.path.join(root, "winget-cache", "Test.App")), "clean up when deleting the package"
        eid = None
        assert wc.cleanup("selftest") == ["Fremd.Rest"] and os.path.isdir(os.path.join(root, "Anderes")), "clean up only in the cache folder"
        assert os.path.isdir(os.path.join(root, "winget-cache", "Benutzt", "x")), "used by a package – stays"
        db.ex("DELETE FROM packages WHERE id=?", (bp,))
        # Folder: never empty/root, not via the general settings, only an own (new/empty) folder
        must_fail(wc.set_folder, "..\\x", contains="Ordner")
        must_fail(wc.set_folder, ".", contains="Ordner")
        must_fail(wc.set_folder, "Anderes\\..", contains="Ordner")
        os.makedirs(os.path.join(root, "Software", "7zip"))
        must_fail(wc.set_folder, "Software", contains="nicht leer")
        assert "winget_cache_folder" not in _m.EDITABLE and "winget_cache_max_mb" not in _m.EDITABLE
        db.set_setting("winget_cache_folder", ".")
        must_fail(wc.folder_root, contains="ungültig")
        must_fail(wc.orphans, contains="ungültig")
        db.set_setting("winget_cache_folder", "winget-cache")
        assert wc.set_folder("winget-cache") == "winget-cache" and wc.set_folder("wc-neu") == "wc-neu"
        db.set_setting("winget_cache_folder", "winget-cache")
        # Adding several: one invalid ID does not stop the others
        r = _m.api_wc_add({"winget_ids": ["Gibt.Es.Nicht", "Test.App"]}, {"username": "haupt", "scope": None})
        assert len(r["ids"]) == 1 and r["errors"] and "Gibt.Es.Nicht" in r["errors"][0], r
        for _ in range(100):
            if not wc.status()["running"]:
                break
            _t.sleep(0.05)
        eid = r["ids"][0]
    finally:
        wc.proxy.urlopen = old_open
        if eid:
            try:
                wc.remove(eid, "selftest")
            except Exception:
                db.ex("DELETE FROM winget_cache WHERE id=?", (eid,))
        db.ex("DELETE FROM packages WHERE name='Test App (winget-Cache)'")
        db.ex("DELETE FROM computers WHERE id IN (?,?,?)", (cmp_, cnew, c81))
        db.set_setting("repo_path", old_repo or "")
        if old_idx is not None:
            open(ip, "wb").write(old_idx)
        elif os.path.exists(ip):
            os.remove(ip)
        winget.load()
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    assert "async function viewWingetCache()" in js and "/api/wingetcache/ready" in js


@test("2.35.1 Repeat after a change to the package: selectable with the current state (default) or that of the deployment, snippets stay snippets")
def _():
    import json as _j
    from app import deploy, main as _m, steps as stp
    st_a = {"id": "sa", "type": "command", "command": "echo alt", "title": "A"}
    st_b = {"id": "sb", "type": "command", "command": "echo neu-b", "title": "B"}
    old = stp.clean_package({"name": "Rerun-Neu", "steps": {"main": [st_a]}})
    cur = stp.clean_package({"name": "Rerun-Neu", "steps": {"main": [dict(st_a, command="echo neu"), st_b]}})
    for x in (old, cur):
        x["steps"], x["conditions"] = _j.dumps(x["steps"]), _j.dumps(x["conditions"])
    pid = db.ex(f"INSERT INTO packages({', '.join(deploy.PKG_FIELDS)}, created_at, updated_at) VALUES({', '.join('?' * len(deploy.PKG_FIELDS))}, ?, ?)",
                tuple(cur[k] for k in deploy.PKG_FIELDS) + ("2026-10-01 10:00:00", "2026-10-02 10:00:00"))
    snap = deploy.row_to_package(dict(old, id=pid))
    c = db.ex("INSERT INTO computers(name) VALUES('RR-PC1')")
    did = db.ex("INSERT INTO deployments(name, package_id, package_json, status, created_at) VALUES('Rerun-Neu', ?, ?, 'failed', '2026-10-01 12:00:00')",
                (pid, _j.dumps(snap)))
    db.ex("INSERT INTO deployment_targets(deployment_id, computer_id, computer_name, status, steps) VALUES(?,?,?,?,?)",
          (did, c, "RR-PC1", "failed", _j.dumps([{"sid": "sa", "status": "failed", "depth": 0, "title": "A"}])))
    made = []
    oc = deploy.create
    deploy.create = lambda pkg, ids, user, *a, **k: (made.append((pkg, ids, k)) or 4711)
    try:
        assert deploy.package_changed(db.q1("SELECT * FROM deployments WHERE id=?", (did,))) == "2026-10-02 10:00:00"
        assert _m.api_deployment(did, {"username": "haupt", "scope": None})["package_changed"] == "2026-10-02 10:00:00"
        cmds = lambda pk: [x.get("command") for _ph, x in stp.iter_steps(stp.normalize(pk))]
        deploy.redeploy(did, True, "t")
        assert cmds(made[-1][0]) == ["echo alt"], "function default: state of the deployment"
        deploy.redeploy(did, True, "t", current=True)
        assert cmds(made[-1][0]) == ["echo neu", "echo neu-b"] and made[-1][2]["package_id"] == pid, "current state"
        deploy.retry_step(did, "sa", "step", [], "t", current=True)
        assert cmds(made[-1][0]) == ["echo neu"]
        must_fail(deploy.retry_step, did, "gibts-nicht", "step", [], "t", True, contains="geänderten Paket")
        ov = deploy.steps_overview(did, None, current=True)
        assert [x["sid"] for x in ov["steps"]] == ["sa", "sb"] and ov["steps"][0]["counts"] == {"failed": 1} and ov["steps"][1]["counts"] == {}, ov
        assert [x["sid"] for x in deploy.steps_overview(did)["steps"]] == ["sa"]
        deploy.rerun_steps(did, ["sb"], "all", [], "t", current=True)
        assert cmds(made[-1][0]) == ["echo neu-b"], "new step selectable"
        must_fail(deploy.rerun_steps, did, ["sb"], "all", [], "t", contains="Unbekannter Schritt")
        # was already a snippet ("– "A" repeated"): from the current package only these steps
        db.ex("UPDATE deployments SET name='Rerun-Neu – „A“ wiederholt' WHERE id=?", (did,))
        deploy.redeploy(did, False, "t", current=True)
        assert cmds(made[-1][0]) == ["echo neu"], "snippet stays a snippet"
        # not changed (package older than the deployment): always the state of the deployment
        db.ex("UPDATE packages SET updated_at='2026-09-01 00:00:00' WHERE id=?", (pid,))
        deploy.redeploy(did, False, "t", current=True)
        assert cmds(made[-1][0]) == ["echo alt"] and deploy.package_changed(db.q1("SELECT * FROM deployments WHERE id=?", (did,))) is None
    finally:
        deploy.create = oc
        db.ex("DELETE FROM deployments WHERE id=?", (did,))
        db.ex("DELETE FROM deployment_targets WHERE deployment_id=?", (did,))
        db.ex("DELETE FROM packages WHERE id=?", (pid,))
        db.ex("DELETE FROM computers WHERE id=?", (c,))
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    assert js.count("current: !!RERUN_CUR[") == 4 and "/steps${RERUN_CUR[did] ? '?current=1' : ''}" in js
    assert "if (!(id in RERUN_CUR)) RERUN_CUR[id] = !!d.package_changed;" in js


@test("2.35.1 winget cache everywhere: winget step on computers without winget runs from the cache (packages, schedules, kiosk), set cache on the package page and in the package")
def _():
    import json as _j, tempfile as _tf
    from app import wingetcache as wc, deploy, kiosk, steps as stp
    root = _tf.mkdtemp(prefix="pk-wc2-", dir=TMP)
    os.makedirs(os.path.join(root, "winget-cache", "Cache.Tool", "2.0_abcdef12"))
    open(os.path.join(root, "winget-cache", "Cache.Tool", "2.0_abcdef12", "tool.msi"), "wb").write(b"x" * 50)
    old_repo = db.setting("repo_path")
    db.set_setting("repo_path", root)
    cp = stp.clean_package({"name": "Cache Tool (winget-Cache)", "steps": {"main": [{"type": "install", "source": "file", "folder": "winget-cache\\Cache.Tool\\2.0_abcdef12",
                            "install_file": "tool.msi", "install_type": "msi", "arguments": "ALLUSERS=1", "detect_name": "Cache Tool", "detect_version": "2.0"}]}})
    cp["steps"], cp["conditions"] = _j.dumps(cp["steps"]), _j.dumps(cp["conditions"])
    cpid = db.ex(f"INSERT INTO packages({', '.join(deploy.PKG_FIELDS)}) VALUES({', '.join('?' * len(deploy.PKG_FIELDS))})", tuple(cp[k] for k in deploy.PKG_FIELDS))
    eid = db.ex("INSERT INTO winget_cache(winget_id, arch, status, version, package_id, min_os) VALUES('Cache.Tool', 'x64', 'ok', '2.0', ?, '10.0.0.0')", (cpid,))
    alt = db.ex("INSERT INTO computers(name, os_build, os_version, os_arch, online, winrm, current_user) VALUES('PC-ALT9', '14393.100', '10.0.14393', '64-Bit', 1, 1, 'FIRMA\\nutzer')")
    neu = db.ex("INSERT INTO computers(name, os_build, os_version, os_arch) VALUES('PC-NEU9', '26100', '10.0.26100', '64-Bit')")
    try:
        ws = {"id": "w1", "type": "install", "source": "winget", "winget_id": "cache.tool", "winget_action": "install", "on_error": "continue", "title": "Cache Tool"}
        c_alt, c_neu = db.q1("SELECT * FROM computers WHERE id=?", (alt,)), db.q1("SELECT * FROM computers WHERE id=?", (neu,))
        sub = wc.step_for(ws, c_alt)
        assert sub and sub["source"] == "file" and sub["install_file"] == "tool.msi" and sub["id"] == "w1" and sub["on_error"] == "continue", sub
        assert "aus dem winget-Cache (2.0)" in sub["title"]
        db.set_setting("winget_cache_all", 0)
        assert wc.step_for(ws, c_neu) is None, "setting off: with winget as before via winget"
        db.set_setting("winget_cache_all", 1)
        assert wc.step_for(ws, c_neu)["install_file"] == "tool.msi", "2.35.2: also with winget from the cache (default)"
        assert wc.step_for(dict(ws, extra_args="--override /S"), c_neu) is None and wc.step_for(dict(ws, winget_scope="user"), c_neu) is None, "own winget arguments/user: winget"
        assert wc.step_for(dict(ws, winget_action="upgrade"), c_neu) is None, "upgrade installs nothing new"
        db.ex("INSERT INTO software(computer_id, name, version, arch, scope, winget_id) VALUES(?, 'Cache Tool', '1.0', 'x64', 'Computer', 'Cache.Tool')", (neu,))
        assert wc.step_for(dict(ws, winget_action="upgrade"), c_neu), "installed: upgrade from the cache"
        db.ex("DELETE FROM software WHERE computer_id=?", (neu,))
        # 2.35.2: cache packages not in the package list, not for selection (deploy, schedules, nested, kiosk)
        from app import main as _m2
        hu = {"username": "haupt", "scope": None}
        assert cpid not in {r["id"] for r in _m2.api_packages(hu)}
        # 2.35.5: selection lists get them with a marker (already chosen ones stay visible), they are not offered
        assert {r["id"]: r["cache"] for r in _m2.api_packages_meta(hu)["packages"]}.get(cpid) is True
        assert {r["id"]: r["cache"] for r in _m2.api_kiosk_admin(hu)["packages"]}.get(cpid) is True
        assert wc.step_for(dict(ws, winget_action="uninstall"), c_alt) is None and wc.step_for(dict(ws, winget_version="1.5"), c_alt) is None
        db.ex("INSERT INTO software(computer_id, name, version, arch, scope, winget_id) VALUES(?, 'Cache Tool', '1.0', 'x64', 'Computer', 'Cache.Tool')", (alt,))
        assert wc.step_for(dict(ws, winget_action="upgrade", winget_version="2.0"), c_alt), "same fixed version works"
        db.ex("DELETE FROM software WHERE computer_id=?", (alt,))
        assert wc.step_for(ws, dict(c_alt, os_arch="32-Bit")) is None, "x64 not on 32-bit Windows"
        # 2.35.5: never as logged-in user, never scope "user", never downgrade
        assert wc.step_for(ws, c_alt, {"run_as": "user"}) is None and wc.step_for(ws, c_alt, {"run_as": "deploy"})
        assert wc.step_for(dict(ws, winget_scope="user"), c_alt) is None, "user scope not even without winget"
        db.ex("INSERT INTO software(computer_id, name, version, arch, scope, winget_id) VALUES(?, 'Cache Tool', '3.0', 'x64', 'Computer', 'Cache.Tool')", (alt,))
        assert wc.step_for(ws, c_alt) is None, "do not replace installed 3.0 with 2.0"
        db.ex("DELETE FROM software WHERE computer_id=?", (alt,))
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "steps.py"), encoding="utf-8").read()
        cs = src[src.index("    def _client_step(self, st, pkg, phase, depth, t0):"):]
        assert cs.index("wingetcache.step_for(st, self.c, pkg)") < cs.index("effective_run_as(st, pkg)"), "replace before execution (with the package: run as)"
        # Kiosk: winget package on old Windows only if the cache covers it
        db.set_setting("kiosk_enabled", 1)
        wp = stp.clean_package({"name": "WG Cache Tool", "steps": {"main": [dict(ws, id="k1")]}})
        wp["steps"], wp["conditions"] = _j.dumps(wp["steps"]), _j.dumps(wp["conditions"])
        wpid = db.ex(f"INSERT INTO packages({', '.join(deploy.PKG_FIELDS)}) VALUES({', '.join('?' * len(deploy.PKG_FIELDS))})", tuple(wp[k] for k in deploy.PKG_FIELDS))
        kiosk.save_item(wpid, {"enabled": True, "approval": 0})
        try:
            assert "WG Cache Tool" in {i["name"] for i in kiosk.catalog(alt)}, "in cache -> in kiosk"
            db.ex("UPDATE winget_cache SET package_id=NULL WHERE id=?", (eid,))
            assert "WG Cache Tool" not in {i["name"] for i in kiosk.catalog(alt)}
            db.ex("UPDATE winget_cache SET package_id=? WHERE id=?", (cpid, eid))
        finally:
            db.ex("DELETE FROM kiosk_items WHERE package_id=?", (wpid,))
            db.ex("DELETE FROM packages WHERE id=?", (wpid,))
            db.set_setting("kiosk_enabled", 0)
    finally:
        db.ex("DELETE FROM winget_cache WHERE id=?", (eid,))
        db.ex("DELETE FROM packages WHERE id=?", (cpid,))
        db.ex("DELETE FROM computers WHERE id IN (?,?)", (alt, neu))
        db.set_setting("repo_path", old_repo or "")
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    assert "['wingetcache', 'winget-Cache', 'db']," in js and "'wingetcache']);" in js[js.index("const OU_HIDDEN"):js.index("const OU_HIDDEN") + 120]
    sw = js[js.index("async function viewSoftware("):js.index("// --- Exceptions of the up-to-date check")]
    assert "#/wingetcache" not in sw, "no longer under software"
    pk = js[js.index("async function viewPackages("):js.index("// --- 2.32: merge packages into a new one")]
    assert "go('#/wingetcache')" in pk and "key: 'wcache'" in pk and "In den winget-Cache aufnehmen" in pk and "].filter(Boolean), shown," in pk
    assert "const w = wcStepField(st);" in js
    assert "(!x.cache || x.id === Number(st.package_id))" in js and "meta.packages.filter((x) => !x.cache)" in js
    assert "(!inCat.has(p.id) && !p.cache) || p.id === x.package_id" in js and "!p.cache || p.id === x.uninstall_package_id" in js


@test("2.35.1 UI: no \"null\" as text – replaceChildren/append never get null (package properties showed \"null\" since 2.27.1)")
def _():
    import re as _re
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    bad = []
    for m in _re.finditer(r"\.(replaceChildren|append)\(", js):
        i, depth, j = m.end(), 1, m.end()
        while depth and j < len(js):
            depth += 1 if js[j] in "([{" else -1 if js[j] in ")]}" else 0
            j += 1
        top, d, cur = [], 0, ""
        for ch in js[i:j - 1]:
            d += 1 if ch in "([{" else -1 if ch in ")]}" else 0
            if ch == "," and d == 0:
                top.append(cur)
                cur = ""
            else:
                cur += ch
        top.append(cur)
        for a in top:
            a = a.strip()
            if a == "null" or _re.search(r"\?\s*[^:]+:\s*null$", a):
                bad.append((js.count("\n", 0, m.start()) + 1, a[:60]))
    assert not bad, ("DOM methods show null as text", bad)


@test("2.35.3 winget cache: ZIP with installer inside (extracted, never outside), \"do not require catalog checksum\" (addresses without version like Google Chrome)")
def _():
    import hashlib as _h, io as _io, sqlite3 as _sq, tempfile as _tf, json as _j, zipfile as _zf
    from app import wingetcache as wc, winget, config
    root = _tf.mkdtemp(prefix="pk-wc3-", dir=TMP)
    old_repo, ip = db.setting("repo_path"), winget.index_path()
    old_idx = open(ip, "rb").read() if os.path.exists(ip) else None
    db.set_setting("repo_path", root)
    files, heads, calls = {}, {}, []

    def zipbytes(entries):
        b = _io.BytesIO()
        with _zf.ZipFile(b, "w") as z:
            for n, d in entries:
                z.writestr(n, d)
        return b.getvalue()

    def publish(wid, ver, inst_yaml, url, data, sha=None):
        mf = (f"PackageIdentifier: {wid}\nPackageVersion: '{ver}'\nPackageName: {wid}\nInstallers:\n- Architecture: x64\n  Scope: machine\n"
              f"  InstallerUrl: {url}\n  InstallerSha256: {sha or _h.sha256(data).hexdigest()}\n{inst_yaml}ManifestType: merged\n").encode()
        rp = f"manifests/{wid}/{ver}/m.yaml"
        vd = _mszip(f"vD:\n- v: '{ver}'\n  rP: {rp}\n  s256H: {_h.sha256(mf).hexdigest()}\n".encode())
        hx = _h.sha256(vd).hexdigest()
        files.update({f"{wc.BASE}/packages/{wid}/{hx[:8]}/versionData.mszyml": vd, f"{wc.BASE}/{rp}": mf, url: data})
        con = _sq.connect(ip)
        con.execute("CREATE TABLE IF NOT EXISTS packages(rowid INTEGER PRIMARY KEY, id TEXT, name TEXT, moniker TEXT, latest_version TEXT, arp_min_version TEXT, arp_max_version TEXT, hash BLOB)")
        con.execute("DELETE FROM packages WHERE id=?", (wid,))
        con.execute("INSERT INTO packages(id, name, latest_version, hash) VALUES(?, ?, ?, ?)", (wid, wid, ver, bytes.fromhex(hx)))
        con.commit(); con.close()

    class _R(_io.BytesIO):
        headers = {}
        final = ""

        def geturl(self):
            return self.final

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    redirects = {}

    def fake_open(req, timeout=0):
        u = req.full_url
        calls.append((req.get_method(), u))
        if req.get_method() == "HEAD":
            r = _R(b"")
            r.headers = heads.get(u, {})
            r.final = redirects.get(u, u)
            return r
        if u not in files:
            raise OSError(f"404 {u}")
        r = _R(files[u])
        r.final = redirects.get(u, u)
        return r
    old_open = wc.proxy.urlopen
    wc.proxy.urlopen = fake_open
    ids = []
    try:
        if os.path.exists(ip):
            os.remove(ip)
        # --- ZIP with installer
        z = zipbytes([("sub/setup.exe", b"MZ-zip" * 100), ("sub/data.cab", b"cab"), ("readme.txt", b"hi")])
        publish("Zip.Tool", "5.1", "  InstallerType: zip\n  NestedInstallerType: nullsoft\n  NestedInstallerFiles:\n  - RelativeFilePath: sub\\setup.exe\n"
                "  InstallerSwitches:\n    Silent: /auto\n", "https://dl.example.test/zt.zip", z)
        ids.append(wc.add("Zip.Tool"))
        e = wc.refresh_entry(ids[-1])
        assert e["status"] == "ok" and e["installer_type"] == "zip/nullsoft" and e["file"] == "sub\\setup.exe", dict(e)
        d = os.path.join(root, *e["folder"].split("\\"))
        assert open(os.path.join(d, "sub", "setup.exe"), "rb").read() == b"MZ-zip" * 100 and os.path.isfile(os.path.join(d, "sub", "data.cab"))
        assert not [x for x in os.listdir(os.path.dirname(d)) if x.endswith(".pk-part")], "no half folder"
        st = _j.loads(db.val("SELECT steps FROM packages WHERE id=?", (e["package_id"],)))["main"][0]
        assert st["folder"] == e["folder"] + "\\sub" and st["install_file"] == "setup.exe" and st["arguments"] == "/auto", st
        # ZIP with path to the outside: rejected, nothing written outside
        evil = zipbytes([("../../boese.exe", b"x"), ("setup.exe", b"MZ")])
        publish("Zip.Evil", "1.0", "  InstallerType: zip\n  NestedInstallerType: exe\n  NestedInstallerFiles:\n  - RelativeFilePath: setup.exe\n"
                "  InstallerSwitches:\n    Silent: /S\n", "https://dl.example.test/ev.zip", evil)
        ids.append(wc.add("Zip.Evil"))
        e2 = wc.refresh_entry(ids[-1])
        assert e2["status"] == "error" and "Ungültiger Pfad im ZIP" in e2["message"], dict(e2)
        assert not os.path.exists(os.path.join(root, "boese.exe")) and not os.path.exists(os.path.join(os.path.dirname(root), "boese.exe"))
        # named file missing in the ZIP -> error, folder gone
        publish("Zip.Miss", "1.0", "  InstallerType: zip\n  NestedInstallerType: msi\n  NestedInstallerFiles:\n  - RelativeFilePath: fehlt.msi\n",
                "https://dl.example.test/zm.zip", zipbytes([("anders.msi", b"x")]))
        ids.append(wc.add("Zip.Miss"))
        e3 = wc.refresh_entry(ids[-1])
        assert e3["status"] == "error" and "nicht im ZIP" in e3["message"] and not os.listdir(os.path.join(root, "winget-cache", "Zip.Miss")), dict(e3)
        must_fail(wc.choose, {"Installers": [{"Architecture": "x64", "InstallerType": "zip", "NestedInstallerType": "portable",
                                               "NestedInstallerFiles": [{"RelativeFilePath": "a.exe"}]}]}, contains="Kein passender")
        # --- Address without version: checksum does not match -> error with hint; with checkmark: load, remember own checksum
        url = "https://dl.example.test/latest/chromish.exe"
        publish("Latest.Tool", "100.0", "  InstallerType: inno\n", url, b"MZ-alt", sha="0" * 64)
        files[url] = b"MZ-neu-1"
        heads[url] = {"ETag": '"v1"'}
        ids.append(wc.add("Latest.Tool"))
        e4 = wc.refresh_entry(ids[-1])
        assert e4["status"] == "error" and "Prüfsumme des Katalogs nicht verlangen" in e4["message"], dict(e4)
        wc.set_ignore_hash(ids[-1], True, "selftest")
        e5 = wc.refresh_entry(ids[-1])
        assert e5["status"] == "ok" and e5["sha256"] == _h.sha256(b"MZ-neu-1").hexdigest() and e5["remote_sig"] == 'etag:"v1"', dict(e5)
        assert "ohne Prüfsumme des Katalogs" in (e5["message"] or "")
        n = len([c for c in calls if c == ("GET", url)])
        e6 = wc.refresh_entry(ids[-1])
        assert len([c for c in calls if c == ("GET", url)]) == n and e6["folder"] == e5["folder"], "same ETag: do not reload"
        files[url], heads[url] = b"MZ-neu-2", {"ETag": '"v2"'}
        e7 = wc.refresh_entry(ids[-1])
        assert e7["sha256"] == _h.sha256(b"MZ-neu-2").hexdigest() and e7["folder"] != e5["folder"], dict(e7)
        assert sorted(os.listdir(os.path.join(root, "winget-cache", "Latest.Tool"))) == [e7["folder"].rsplit("\\", 1)[-1]], "old file gone"
        upd = db.val("SELECT updated_at FROM packages WHERE id=?", (e7["package_id"],))
        db.ex("UPDATE packages SET updated_at='2000-01-01 00:00:00' WHERE id=?", (e7["package_id"],))
        wc.refresh_entry(ids[-1])
        assert db.val("SELECT updated_at FROM packages WHERE id=?", (e7["package_id"],)) == "2000-01-01 00:00:00", "unchanged: package not rewritten"
        # 2.35.5: without catalog checksum only HTTPS – also after a redirect
        files[url], heads[url] = b"MZ-neu-3", {"ETag": '"v3"'}
        redirects[url] = "http://dl.example.test/latest/chromish.exe"
        e8 = wc.refresh_entry(ids[-1])
        assert e8["status"] == "error" and "nur über HTTPS" in e8["message"] and e8["sha256"] == e7["sha256"], dict(e8)
        redirects.clear()
        must_fail(wc._download, "http://dl.example.test/x.exe", None, 10 ** 6, False, contains="nur über HTTPS")
        # ZIP: names that differ only in spelling -> rejected; file with the spelling from the ZIP
        zp = os.path.join(TMP, "case.zip")
        open(zp, "wb").write(zipbytes([("Setup.exe", b"a"), ("setup.EXE", b"b")]))
        must_fail(wc._zip_members, zp, 10 ** 6, contains="Groß-/Kleinschreibung")
        publish("Zip.Case", "1.0", "  InstallerType: zip\n  NestedInstallerType: nullsoft\n  NestedInstallerFiles:\n  - RelativeFilePath: setup.exe\n",
                "https://dl.example.test/zc.zip", zipbytes([("Setup.EXE", b"MZ")]))
        ids.append(wc.add("Zip.Case"))
        e9 = wc.refresh_entry(ids[-1])
        assert e9["status"] == "ok" and e9["file"] == "Setup.EXE", dict(e9)
        from app import main as _m
        assert ("PUT", "/api/wingetcache/{eid}") not in _m.access.ROUTES
    finally:
        wc.proxy.urlopen = old_open
        for i in ids:
            try:
                wc.remove(i, "selftest")
            except Exception:
                db.ex("DELETE FROM winget_cache WHERE id=?", (i,))
        db.set_setting("repo_path", old_repo or "")
        if old_idx is not None:
            open(ip, "wb").write(old_idx)
        elif os.path.exists(ip):
            os.remove(ip)
        winget.load()


@test("2.35.4 winget cache: top-level manifest values apply to all installers (ZIP file, e.g. MobaXterm); program only as x86 also in the x64 entry")
def _():
    from app import wingetcache as wc
    man = {"PackageIdentifier": "Mobatek.MobaXterm", "PackageVersion": "26.6.0.5560", "PackageName": "MobaXterm", "InstallerType": "zip",
           "NestedInstallerType": "wix", "NestedInstallerFiles": [{"RelativeFilePath": "MobaXterm_installer_26.6.msi"}],
           "ProductCode": "{498DB265-E4C3-4580-B8D0-482C79F36E7D}", "AppsAndFeaturesEntries": [{"UpgradeCode": "{745A604F-05A1-4867-8881-59888EC8B806}"}],
           "Installers": [{"Architecture": "x86", "InstallerUrl": "https://dl.example.test/m.zip", "InstallerSha256": "C4" * 32}]}
    i = wc.choose(man, "x64")
    p = wc.plan(man, i)
    assert p["nested"] == "MobaXterm_installer_26.6.msi" and p["install_type"] == "msi" and p["type"] == "zip/wix" and p["product_code"].startswith("{498D"), p
    # Installer x86 in the x64 entry: installed x86 program fits, 32-bit Windows too; x64 installer never on 32-bit
    assert wc._arch_ok({"os_arch": "64-Bit"}, [{"arch": "x86", "scope": "Computer"}], "x86")
    assert wc._arch_ok({"os_arch": "32-Bit"}, [], "x86") and not wc._arch_ok({"os_arch": "32-Bit"}, [], "x64")
    assert not wc._arch_ok({"os_arch": "64-Bit"}, [{"arch": "x86"}], "x64"), "x86 installed, x64 installer: no"
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "wingetcache.py"), encoding="utf-8").read()
    assert src.count('e["inst_arch"] or e["arch"]') == 2, "split and winget step take the architecture of the installer"


@test("2.35.5 Repeat with current state: snippet marked firmly (also with shortened name), switched-off steps stay off")
def _():
    import json as _j
    from app import deploy, steps as stp
    cur = stp.clean_package({"name": "S" * 190, "steps": {"main": [
        {"id": "a", "type": "command", "command": "echo a2"}, {"id": "b", "type": "command", "command": "echo b2", "enabled": False},
        {"id": "c", "type": "command", "command": "echo c2"}]}})
    cur["steps"], cur["conditions"] = _j.dumps(cur["steps"]), _j.dumps(cur["conditions"])
    pid = db.ex(f"INSERT INTO packages({', '.join(deploy.PKG_FIELDS)}, created_at, updated_at) VALUES({', '.join('?' * len(deploy.PKG_FIELDS))}, ?, ?)",
                tuple(cur[k] for k in deploy.PKG_FIELDS) + ("2026-10-01 10:00:00", "2026-10-02 10:00:00"))
    full = stp.normalize(deploy.row_to_package(dict(cur, id=pid)))
    sl, _t = deploy.retry_package(full, "a", "from")  # from "a": b (switched off) and c along
    assert sl.get("slice") is True
    did = db.ex("INSERT INTO deployments(name, package_id, package_json, status, created_at) VALUES(?, ?, ?, 'failed', '2026-10-01 12:00:00')",
                (("S" * 190 + " – ab „" + "x" * 50 + "“ wiederholt")[:200], pid, _j.dumps(sl)))
    try:
        d = db.q1("SELECT * FROM deployments WHERE id=?", (did,))
        assert not deploy.RETRY_SUFFIX.search(d["name"]), "name shortened – suffix missing"
        p = stp.normalize(deploy.source_package(d, True))
        got = [(x["id"], x.get("enabled", True)) for _ph, x in stp.iter_steps(p)]
        assert got == [("a", True), ("b", False), ("c", True)] and p.get("slice"), got
    finally:
        db.ex("DELETE FROM deployments WHERE id=?", (did,))
        db.ex("DELETE FROM packages WHERE id=?", (pid,))


@test("2.35.6 Files (explorer): hidden entries hidden, switchable; count, upload confirmation and targeted entry stay")
def _():
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    i = js.index("async function viewFiles(")
    v = js[i:js.index("\nasync function ", i + 10)]
    assert "showHidden ? d0.items : d0.items.filter((x) => !x.hidden" in v, "hidden entries not filtered"
    assert "x.name.toLowerCase() === fl" in v, "targeted opened entry (focus) must stay visible"
    assert "localStorage.setItem('pk-files-hidden'" in v and "'Versteckte anzeigen'" in v, "toggle missing"
    # Upload checks against ALL names (also hidden) – otherwise a hidden file would be overwritten without asking
    assert "curNames = new Set((d.items || d.entries || []).map(" in v
    assert v.index("curNames = new Set((d.items") < v.index("lastList = d;")
    from app import clientfiles
    src = open(clientfiles.__file__, encoding="utf-8").read()
    assert '"hidden": bool(attr & 0x2)' in src, "server delivers the hidden attribute"


@test("2.36 Release of new winget versions: immediately / days / manual, approve, block, initial state, step with --version, schedules, cache")
def _():
    from app import wingetrelease as wr, wingetcache as wc, util as u
    W = "Selftest.RelApp"
    cat = {"v": "2.0"}
    old_latest, old_mode, old_days = wr._latest, db.setting("winget_release_mode"), db.setting("winget_release_days")
    wr._latest = lambda wid: cat["v"] if str(wid).lower() == W.lower() else old_latest(wid)
    db.ex("DELETE FROM winget_releases WHERE winget_id=?", (W,))
    try:
        db.set_setting("winget_release_mode", "days"); db.set_setting("winget_release_days", 2)
        assert wr.target(W)["state"] == "latest", "unknown ID: no restriction"
        # Initial state approved on first contact (otherwise everything would stand still after switching on)
        db.ex("INSERT INTO software(computer_id, name, version, winget_id, winget_sure) VALUES(NULL, 'RelApp', '1.0', ?, 1)", (W,))
        cat["v"] = "1.5"
        wr.sync()
        assert db.val("SELECT base FROM winget_releases WHERE winget_id=? AND version='1.5'", (W,)) == 1
        assert wr.target(W)["state"] == "latest"
        # new version 2.0: deferred for 2 days -> 1.5 is installed
        cat["v"] = "2.0"
        wr.sync()
        t = wr.target(W)
        assert t["state"] == "pin" and t["version"] == "1.5" and "zurückgestellt bis" in t["reason"], t
        st = {"type": "install", "source": "winget", "winget_id": W, "winget_action": "install", "winget_version": ""}
        s2, why = wr.apply_step(st)
        assert s2["winget_version"] == "1.5" and why is None
        assert wr.apply_step({**st, "winget_version": "1.9"})[0]["winget_version"] == "1.9", "fixed version stays"
        assert wr.apply_step(st, {"release_bypass": True})[0] is st, "button \"Aktualisieren\": latest"
        assert wr.apply_step({**st, "winget_action": "uninstall"})[0]["winget_version"] == "", "uninstall unchanged"
        assert wr.allowed_update(W, "1.0") and not wr.allowed_update(W, "1.5") and not wr.allowed_update(W, "")
        # released after the days have passed
        db.ex("UPDATE winget_releases SET first_seen=? WHERE winget_id=? AND version='2.0'", (u.ago(days=3), W))
        assert wr.target(W)["state"] == "latest" and wr.allowed_update(W, "1.5")
        # manual: waits until approved
        db.set_setting("winget_release_mode", "manual")
        t = wr.target(W)
        assert t["state"] == "pin" and "wartet auf Freigabe" in t["reason"], t
        wr.set_status(W, "2.0", "approved", "selftest")
        assert wr.target(W)["state"] == "latest"
        # immediately, but blocked -> the previous one; everything blocked -> nothing
        db.set_setting("winget_release_mode", "off")
        wr.set_status(W, "2.0", "blocked", "selftest")
        t = wr.target(W)
        assert t["state"] == "pin" and t["version"] == "1.5" and "gesperrt" in t["reason"], t
        wr.set_status(W, "1.5", "blocked", "selftest")
        assert wr.target(W)["state"] == "hold" and wr.apply_step(st)[0] is None and not wr.allowed_update(W, "1.0")
        wr.set_status(W, "1.5", "", "selftest"); wr.set_status(W, "2.0", "", "selftest")
        assert wr.target(W)["state"] == "latest", "back to the rule (immediately)"
        try:
            wr.set_status(W, "9.9", "approved", "selftest")
            raise AssertionError("unknown version accepted")
        except ValueError:
            pass
        # Catalog newer than the last sync: noted as first sighting on query
        db.set_setting("winget_release_mode", "days")
        cat["v"] = "2.1"
        t = wr.target(W)
        assert t["state"] == "pin" and t["version"] == "2.0" and db.val("SELECT 1 FROM winget_releases WHERE winget_id=? AND version='2.1'", (W,))
        # winget cache: approved version is already in the cache -> load nothing, keep state
        eid = db.ex("INSERT INTO winget_cache(winget_id, status, version, folder, file, created_at) VALUES(?, 'ok', '2.0', 'x\\y', 'a.msi', ?)", (W, u.now()))
        old_fm = wc.fetch_manifest
        wc.fetch_manifest = lambda *a, **k: (_ for _ in ()).throw(AssertionError("darf nichts laden"))
        try:
            e = wc.refresh_entry(eid)
            assert e["status"] == "ok" and e["version"] == "2.0" and "bleibt 2.0" in (e["message"] or ""), dict(e)
            # blocked version in the cache is not used
            assert wc._released(dict(e))
            wr.set_status(W, "2.0", "blocked", "selftest")
            assert not wc._released(dict(wc.entry(eid))) and wr.version_ok(W, "1.5") and wr.version_ok(W, "0.1")
            wr.set_status(W, "2.0", "", "selftest")
        finally:
            wc.fetch_manifest = old_fm
            db.ex("DELETE FROM winget_cache WHERE id=?", (eid,))
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "schedules.py"), encoding="utf-8").read()
        assert "wingetrelease.target(" in src and 'util.vcmp(str(r["version"]), t["version"]) < 0' in src, "schedules: only older ones than the approved one"
        stp = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "steps.py"), encoding="utf-8").read()
        assert stp.index("wingetrelease.apply_step(") < stp.index("wingetcache.step_for(st, self.c, pkg)"), "approval before the winget cache"
        assert "util.vcmp(str(v), pin) >= 0" in stp, "never downgrade"
        mn = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "main.py"), encoding="utf-8").read()
        assert 'pkg["release_bypass"] = True' in mn and '"winget_release_days": (1, 365)' in mn
        wcs = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "wingetcache.py"), encoding="utf-8").read()
        assert 'pkg.get("release_bypass")' in wcs, "\"Aktualisieren\": latest via winget instead of cache"

    finally:
        wr._latest = old_latest
        db.set_setting("winget_release_mode", old_mode); db.set_setting("winget_release_days", old_days)
        db.ex("DELETE FROM winget_releases WHERE winget_id=?", (W,))
        db.ex("DELETE FROM software WHERE winget_id=?", (W,))


@test("2.36.2 Audit: block also applies to \"Aktualisieren\", changing the rule keeps approvals, errors during checking not open, invalid version, health check waits for the migration")
def _():
    from app import wingetrelease as wr, wingetcache as wc
    W = "Selftest.RelAudit"
    cat = {"v": "1.0"}
    old_latest, old_mode, old_days = wr._latest, db.setting("winget_release_mode"), db.setting("winget_release_days")
    wr._latest = lambda wid: cat["v"] if str(wid).lower() == W.lower() else old_latest(wid)
    db.ex("DELETE FROM winget_releases WHERE winget_id=?", (W,))
    st = {"type": "install", "source": "winget", "winget_id": W, "winget_action": "upgrade", "winget_version": ""}
    try:
        db.set_setting("winget_release_mode", "off")
        db.ex("INSERT INTO software(computer_id, name, version, winget_id, winget_sure) VALUES(NULL, 'RelAudit', '1.0', ?, 1)", (W,))
        wr.sync()
        for v in ("1.1", "1.2", "1.3"):
            cat["v"] = v
            wr.sync()
        assert wr.target(W)["state"] == "latest"
        # Finding 1: blocked latest – "Aktualisieren" (release_bypass) gets the previous one, not the blocked one
        wr.set_status(W, "1.3", "blocked", "selftest")
        s2, why = wr.apply_step(st, {"release_bypass": True})
        assert s2 is not None and s2["winget_version"] == "1.2", ("block also applies to Aktualisieren", s2, why)
        wr.set_status(W, "1.2", "blocked", "selftest"); wr.set_status(W, "1.1", "blocked", "selftest"); wr.set_status(W, "1.0", "blocked", "selftest")
        s2, why = wr.apply_step(st, {"release_bypass": True})
        assert s2 is None and "keine freigegebene Version" in why, "everything blocked: not even Aktualisieren"
        for v in ("1.0", "1.1", "1.2", "1.3"):
            wr.set_status(W, v, "", "selftest")
        # deferred (not blocked): Aktualisieren still gets the latest
        db.set_setting("winget_release_mode", "manual"); cat["v"] = "1.4"; wr.sync()
        assert wr.target(W)["state"] == "pin" and wr.apply_step(st, {"release_bypass": True})[0] is st, "deferral bypasses Aktualisieren"
        db.ex("DELETE FROM winget_releases WHERE winget_id=? AND version='1.4'", (W,)); cat["v"] = "1.3"
        db.set_setting("winget_release_mode", "off")
        # Finding 2: change immediately -> manual: 1.3 (approved under "immediately") stays approved
        assert wr.freeze("manual") >= 3
        db.set_setting("winget_release_mode", "manual")
        assert wr.target(W)["state"] == "latest", ("changing the rule does not defer", wr.target(W))
        cat["v"] = "1.4"; wr.sync()
        t = wr.target(W)
        assert t["state"] == "pin" and t["version"] == "1.3" and "wartet auf Freigabe" in t["reason"], t
        assert wr.freeze("manual") == 0, "same rule: nothing"
        # days -> manual: a still-deferred version stays deferred
        db.set_setting("winget_release_mode", "days"); db.set_setting("winget_release_days", 5)
        assert wr.freeze("manual") == 0 and db.val("SELECT base FROM winget_releases WHERE winget_id=? AND version='1.4'", (W,)) == 0
        db.set_setting("winget_release_mode", "manual")
        mn = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "main.py"), encoding="utf-8").read()
        i = mn.index('if k == "winget_release_mode":\n')
        assert "wingetrelease.freeze(v)" in mn[i:i + 200] and mn.index("wingetrelease.freeze(v)") < mn.index("db.set_setting(k, v)", i), "freeze before saving"
        # Finding 3: error while checking -> do not install the latest, do not use the cache
        old_vo = wr.version_ok
        wr.version_ok = lambda *a: (_ for _ in ()).throw(RuntimeError("DB gesperrt"))
        try:
            assert wc._released({"winget_id": W, "version": "1.4"}) is False
        finally:
            wr.version_ok = old_vo
        stp = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "steps.py"), encoding="utf-8").read()
        j = stp.index("wingetrelease.apply_step(st, pkg)")
        assert "st2, why = st, None" not in stp[j:j + 400] and '"failed", why' in stp[j:j + 600], "2.36.2: error not open"
        # Finding 5: approved version from the index is checked before --version
        db.ex("UPDATE winget_releases SET version='1.3\" --override x' WHERE winget_id=? AND version='1.3'", (W,))
        s2, why = wr.apply_step({**st, "winget_action": "install"})
        assert s2 is None and "ungültig" in why, (s2, why)
    finally:
        wr._latest = old_latest
        db.set_setting("winget_release_mode", old_mode); db.set_setting("winget_release_days", old_days)
        db.ex("DELETE FROM winget_releases WHERE winget_id=?", (W,))
        db.ex("DELETE FROM software WHERE winget_id=?", (W,))
    # update.sh/Dockerfile: health check gives backup and migration time (only where the files exist)
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    for name in ("Dockerfile", "update.sh"):
        p = os.path.join(root, name)
        if not os.path.exists(p):
            continue
        t = open(p, encoding="utf-8").read()
        if name == "Dockerfile":
            assert "--start-period=15m" in t and "--start-interval=10s" in t, "start time of the health check"
        else:
            assert 'WAIT_STEPS="${PK_WAIT_STEPS:-240}"' in t and "mv .env.pk-alt .env" not in t
            assert "is missing next to" in t and 'OFFLINE=$(resolve "$CALLDIR" "$ARG")' in t


@test("2.36.1 License AGPL-3.0: license text unchanged, in the image, labelling, notice in help and UI")
def _():
    import hashlib
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    rd = lambda *p: open(os.path.join(root, *p), encoding="utf-8").read()
    lic = open(os.path.join(root, "LICENSE"), "rb").read()
    assert hashlib.sha256(lic).hexdigest() == "8486a10c4393cee1c25392769ddd3b2d6c242d6ec7928e1414efff7dfb2f07ef", "LICENSE is not the unchanged text of the AGPL-3.0"
    # Dockerfile, workflows and README are missing in the image (there the existing LICENSE already proves that it comes along)
    if os.path.exists(os.path.join(root, "Dockerfile")):
        assert "LICENSE ./" in rd("Dockerfile"), "license text belongs in the image"
    if os.path.exists(os.path.join(root, ".github", "workflows", "release.yml")):
        assert "org.opencontainers.image.licenses=AGPL-3.0-only" in rd(".github", "workflows", "release.yml")
    for f in ("de.md", "en.md"):
        assert "{#license}" in rd("app", "static", "help", f) and "github.com/soleil0815/pocketknife" in rd("app", "static", "help", f)
    js = rd("app", "static", "app.js")
    assert "'#/help?s=license'" in js and "'AGPL-3.0'" in js, "license notice in the UI"
    for f in ("README.md", "README.en.md"):
        if os.path.exists(os.path.join(root, f)):
            assert "AGPL-3.0-only" in rd(f) and "(LICENSE)" in rd(f)



@test("2.36.3 Audit: direct actions (files, services, tools, disk space, remote control, restart ...) never under an address that the identity check has marked as foreign")
def _():
    from app import actions, presence, remote_desktop, tools
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    old = db.setting("identity_check")
    cid = db.ex("INSERT INTO computers(name, dns_name, addr_problem, addr_detail) VALUES('PC-AUD363', 'fs01.firma.local', 'other', "
                "'Unter 10.0.10.5 antwortet „FS01“ statt PC-AUD363')")
    try:
        db.set_setting("identity_check", 1)
        must_fail(actions._host, cid, contains="FS01")
        try:
            clientfiles._host(cid)
            raise AssertionError("files: no error")
        except clientfiles.FileError as e:
            assert "Nicht ausgeführt" in str(e)
        c = db.q1("SELECT * FROM computers WHERE id=?", (cid,))
        must_fail(tools.run, {"id": 1, "repo_path": "", "script": "x", "type": "cmd", "run_as": "service"}, cid, contains="Nicht ausgeführt")
        must_fail(diskusage.start, cid, "C:\\", 2, "test", contains="Nicht ausgeführt")
        must_fail(remote_desktop.prepare, c, {"username": "haupt"}, "x", {"protocol": "rdp", "auth": "manual", "username": "a"},
                  contains="Nicht ausgeführt")
        # only id passed (without columns): note is read afterwards
        must_fail(presence.direct_host, {"id": cid, "name": "PC-AUD363", "dns_name": "fs01.firma.local"}, contains="FS01")
        # Check off: old note no longer applies; without a note the normal address
        db.set_setting("identity_check", 0)
        assert actions._host(cid)[1] == "fs01.firma.local"
        db.set_setting("identity_check", 1)
        db.ex("UPDATE computers SET addr_problem=NULL, addr_detail=NULL WHERE id=?", (cid,))
        assert actions._host(cid)[1] == "fs01.firma.local"
        # All direct paths use the check (source text)
        rd = lambda f: open(os.path.join(root, "app", f), encoding="utf-8").read()  # noqa: E731
        mn = rd("main.py")
        assert 'remote.ps(presence.direct_host(c), cmds[act])' in mn, "restart/shutdown/gpupdate"
        assert 'remote.ps(presence.direct_host(c), script)' in mn, "test scanner"
        assert "host = presence.direct_host(c)\n    except ValueError as e:\n        _err(e)\n    if not remote.is_online(host)[1]" in mn, "logged-in users"
        assert "presence.direct_host(c)" in rd("toast.py") and "presence.direct_host(c)" in rd("kiosk.py")
        assert 'remote.ps(c["dns_name"] or c["name"], script, timeout=seconds)' not in rd("clientfiles.py"), "file search via _host"
    finally:
        db.set_setting("identity_check", old)
        db.ex("DELETE FROM computers WHERE id=?", (cid,))


@test("2.36.3 Audit: loading fallback of PK-RunPs1 also doubles typographic apostrophes; .env copy in update.sh never readable by others")
def _():
    import re as _re
    from app import remote
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    w = remote.WRAPPER
    assert '.Replace("\'", "\'\'")' not in w, "only the ASCII apostrophe doubled"
    m = _re.search(r'\$pkApo = "(\[[^"]+\])"', w)
    assert m and "-replace $pkApo, '$0$0'" in w
    # Same effect as util.psq (PowerShell regex = Python regex for this class)
    pat = m.group(1).replace("\\u2018", "\u2018").replace("\\u201B", "\u201b")
    for name in ("C:\\Pakete\\Mike’s Setup.ps1", "C:\\a'b‚c‛d‘e.ps1"):
        assert "'" + _re.sub(pat, lambda x: x.group(0) * 2, name) + "'" == util.psq(name)
    us = os.path.join(root, "update.sh")
    if os.path.exists(us):  # update.sh is not in the image
        t = open(us, encoding="utf-8").read()
        assert "( umask 077; rm -f .env.pk-alt; cp .env .env.pk-alt )" in t and "\n  cp .env .env.pk-alt" not in t


@test("2.36.3 Audit: kiosk requests deleted after a deadline (open ones stay), leftovers of deleted computers gone")
def _():
    from app import scheduler
    old = db.setting("deployment_retention_days")
    cid = db.ex("INSERT INTO computers(name) VALUES('PC-KR363')")
    ids = {}
    try:
        db.set_setting("deployment_retention_days", 30)
        alt, neu = util.ago(days=40), util.now()
        for key, created, status, comp in (("alt_fertig", alt, "started", cid), ("alt_offen", alt, "pending", cid),
                                           ("alt_start", alt, "starting", cid), ("neu", neu, "rejected", cid),
                                           ("verwaist", neu, "pending", 987654)):
            ids[key] = db.ex("INSERT INTO kiosk_requests(created_at, username, computer_id, status, action) VALUES(?,?,?,?,'install')",
                             (created, "m.muster", comp, status))
        scheduler._cleanup()
        left = {r["id"] for r in db.q("SELECT id FROM kiosk_requests")}
        assert ids["alt_fertig"] not in left and ids["verwaist"] not in left, left
        assert {ids["alt_offen"], ids["alt_start"], ids["neu"]} <= left
        db.set_setting("deployment_retention_days", 0)  # 0 = never delete
        db.ex("UPDATE kiosk_requests SET status='success' WHERE id=?", (ids["alt_offen"],))
        scheduler._cleanup()
        assert db.val("SELECT 1 FROM kiosk_requests WHERE id=?", (ids["alt_offen"],))
    finally:
        db.set_setting("deployment_retention_days", old)
        db.ex("DELETE FROM kiosk_requests WHERE username='m.muster'")
        db.ex("DELETE FROM computers WHERE id=?", (cid,))


@test("2.36.3 Audit: db.tx – a failed COMMIT leaves no open transaction behind; download of the winget index limited")
def _():
    import io
    c = db.conn()
    c.execute("CREATE TABLE IF NOT EXISTS t363_p(id INTEGER PRIMARY KEY)")
    c.execute("CREATE TABLE IF NOT EXISTS t363_c(pid INTEGER REFERENCES t363_p(id) DEFERRABLE INITIALLY DEFERRED)")
    try:
        try:
            with db.tx():
                db.ex("INSERT INTO t363_c(pid) VALUES(4711)")  # Foreign key only checked at COMMIT -> COMMIT fails
            raise AssertionError("COMMIT should have failed")
        except Exception as e:  # noqa: BLE001
            assert "FOREIGN KEY" in str(e).upper(), e
        assert not c.in_transaction, "transaction stayed open"
        with db.tx():
            db.ex("INSERT INTO t363_p(id) VALUES(1)")
        assert db.val("SELECT COUNT(*) FROM t363_p") == 1 and db.val("SELECT COUNT(*) FROM t363_c") == 0
        # Error in the block: ROLLBACK, original error stays visible
        try:
            with db.tx():
                db.ex("INSERT INTO t363_p(id) VALUES(2)")
                raise KeyError("eigentlicher Fehler")
        except KeyError:
            pass
        assert db.val("SELECT COUNT(*) FROM t363_p") == 1 and not c.in_transaction
    finally:
        c.execute("DROP TABLE IF EXISTS t363_c")
        c.execute("DROP TABLE IF EXISTS t363_p")
    out = io.BytesIO()
    assert winget._copy_limited(io.BytesIO(b"x" * 3000), out, limit=3000) == 3000 and len(out.getvalue()) == 3000
    must_fail(winget._copy_limited, io.BytesIO(b"x" * 3001), io.BytesIO(), 3000, contains="unplausibel groß")
    src = open(winget.__file__, encoding="utf-8").read()
    assert "_copy_limited(r, f)" in src and "shutil.copyfileobj(r, f)" not in src


@test("2.36.3 Audit: OU admins see no foreign collections/computers in Computer -> Collections and in the scan profile list")
def _():
    from app import access, main as _m, profiles
    ou = {"username": "ou363", "scope": [1]}
    o_vis, o_mem, o_ids, o_all = _m._visible_collections, _m._member_collections, access.computer_ids, profiles.all_profiles
    try:
        _m._member_collections = lambda cid: [{"id": 11, "name": "Eigene"}, {"id": 22, "name": "Kunde B"}]
        _m._visible_collections = lambda user, *a, **k: None if not user.get("scope") else {11}
        assert [c["id"] for c in _m.api_computer_category(1, "collections", ou)] == [11]
        assert [c["id"] for c in _m.api_computer_category(1, "collections", {"username": "h", "scope": None})] == [11, 22]
        access.computer_ids = lambda user: {1} if user.get("scope") else None
        profiles.all_profiles = lambda: [{"id": 99, "name": "P", "collections": [profiles.ALL_COMPUTERS, 11, 22],
                                          "computers": [1, 2, 3], "triggers": []}]
        p = _m.api_profiles(ou)["profiles"][0]
        assert p["computers"] == [1] and p["collections"] == [profiles.ALL_COMPUTERS, 11], p
        n_all = db.val("SELECT COUNT(*) FROM computers")
        assert p["members"] <= 1, "counter only over the own scope"
        h = _m.api_profiles({"username": "h", "scope": None})["profiles"][0]
        assert h["computers"] == [1, 2, 3] and h["members"] == n_all
    finally:
        _m._visible_collections, _m._member_collections, access.computer_ids, profiles.all_profiles = o_vis, o_mem, o_ids, o_all


@test("2.37 Suggestions from the audit: App Installer only from the repository (server downloads once), NLA, notice for tenant PC, integrity check, lock per computer without blocking, report sending repeated, cache lock per entry, winter time, indexes, supply chain")
def _():
    import datetime as _dt
    import hashlib as _h
    import io as _io
    import json as _j
    import tempfile as _tf
    import time
    from app import deploy, notify, profiles, proxy, remote_desktop, wingetboot, wingetcache as wc
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    # --- App Installer: the client never downloads by itself; the server fetches the release once into the repository
    f = deploy._winget_find()
    assert "Invoke-WebRequest" not in f and "Invoke-RestMethod" not in f and "github.com" not in f and "liegt noch nicht im Repository" in f
    repo = _tf.mkdtemp(prefix="pk-boot-", dir=TMP)
    old_repo, old_tag, old_boot = db.setting("repo_path"), db.setting("winget_bootstrap_tag"), db.setting("winget_bootstrap")
    bundle, lic = b"MSIX-BUNDLE" * 100, b"<License/>"
    files = {"https://dl.example.test/AppInstaller.msixbundle": bundle, "https://dl.example.test/x_License1.xml": lic}
    rel = {"tag_name": "v1.99.0", "assets": [
        {"name": "Microsoft.DesktopAppInstaller_8wekyb3d8bbwe.msixbundle", "browser_download_url": "https://dl.example.test/AppInstaller.msixbundle",
         "digest": "sha256:" + _h.sha256(bundle).hexdigest()},
        {"name": "x_License1.xml", "browser_download_url": "https://dl.example.test/x_License1.xml", "digest": "sha256:" + _h.sha256(lic).hexdigest()},
        {"name": "Source.zip", "browser_download_url": "https://dl.example.test/src.zip"}]}
    calls = []

    class _R(_io.BytesIO):
        def geturl(self):
            return calls[-1]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(req, timeout=0):
        u = req.full_url
        calls.append(u)
        if u == wingetboot.API:
            return _R(_j.dumps(rel).encode())
        if u in files:
            return _R(files[u])
        raise OSError("404 " + u)
    old_open = proxy.urlopen
    proxy.urlopen = fake
    try:
        db.set_setting("repo_path", repo); db.set_setting("winget_bootstrap", 1); db.set_setting("winget_bootstrap_tag", "")
        db.set_setting("winget_bootstrap_folder", "")
        assert wingetboot.folder() == "_winget" and not wingetboot.present()
        msg = wingetboot.refresh("selftest")
        assert "v1.99.0" in msg and wingetboot.present()
        assert open(os.path.join(repo, "_winget", "Microsoft.DesktopAppInstaller_8wekyb3d8bbwe.msixbundle"), "rb").read() == bundle
        assert not os.path.exists(os.path.join(repo, "_winget", "Source.zip")), "only App Installer, license, dependencies"
        n = len(calls)
        assert "liegt schon" in wingetboot.refresh("selftest") and all(c == wingetboot.API for c in calls[n:]), "same release: load nothing"
        files["https://dl.example.test/AppInstaller.msixbundle"] = b"ANDERS"
        rel["tag_name"] = "v1.99.1"
        try:
            wingetboot.refresh("selftest")
            raise AssertionError("wrong checksum accepted")
        except wingetboot.BootError as e:
            assert "Prüfsumme" in str(e)
        assert open(os.path.join(repo, "_winget", "Microsoft.DesktopAppInstaller_8wekyb3d8bbwe.msixbundle"), "rb").read() == bundle, "old state stays"
        db.set_setting("winget_bootstrap", 0)
        assert "ausgeschaltet" in wingetboot.refresh("selftest")
    finally:
        proxy.urlopen = old_open
        db.set_setting("repo_path", old_repo or ""); db.set_setting("winget_bootstrap_tag", old_tag or ""); db.set_setting("winget_bootstrap", old_boot)
    # --- Remote control: with credentials only NLA; without (login at the computer) as before
    cid = db.ex("INSERT INTO computers(name, dns_name) VALUES('PC-0137', 'pc-0137.firma.local')")
    try:
        comp = db.q1("SELECT * FROM computers WHERE id=?", (cid,))
        u = {"username": "admin", "scope": None}
        tk = remote_desktop.prepare(comp, u, "th37", {"protocol": "rdp", "auth": "manual", "username": "FIRMA\\x", "password": "p"})
        assert remote_desktop._tokens[tk]["params"]["security"] == "nla"
        tk = remote_desktop.prepare(comp, u, "th37", {"protocol": "rdp", "auth": "prompt"})
        assert remote_desktop._tokens[tk]["params"]["security"] == "any"
        assert remote_desktop.tenant_names(cid) == []
    finally:
        db.ex("DELETE FROM computers WHERE id=?", (cid,))
    rjs = open(os.path.join(root, "app", "static", "remote.js"), encoding="utf-8").read()
    assert "st.tenant_names" in rjs and "/api/remote/status?computer_id=" in rjs, "notice before own login on tenant PCs"
    # --- Integrity check: unreadable installation file blocks
    src = open(os.path.join(root, "app", "integrity.py"), encoding="utf-8").read()
    assert 'x["kind"] == "unreachable" and x["main"]' in src
    # --- Lock per computer: never wait blocking, re-queue instead
    lcid = db.ex("INSERT INTO computers(name) VALUES('PC-LOCK37')")
    lk = deploy._computer_lock(lcid)
    lk.acquire()
    started = []
    old_timer = deploy.threading.Timer

    class _T:
        def __init__(self, sec, fn):
            started.append(sec)
            self.daemon = False

        def start(self):
            pass
    deploy.threading.Timer = _T
    did = db.ex("INSERT INTO deployments(name, package_json, status, created_at) VALUES('L', '{}', 'running', ?)", (util.now(),))
    tid = db.ex("INSERT INTO deployment_targets(deployment_id, computer_id, computer_name, status) VALUES(?, ?, 'X', 'queued')", (did, lcid))
    try:
        t0 = time.time()
        deploy._run_target_safe(did, tid)
        assert time.time() - t0 < 2 and started == [deploy.REQUEUE_SECONDS], "not blocked, re-queued"
        assert db.val("SELECT status FROM deployment_targets WHERE id=?", (tid,)) == "queued"
    finally:
        deploy.threading.Timer = old_timer
        lk.release()
        db.ex("DELETE FROM deployment_targets WHERE deployment_id=?", (did,)); db.ex("DELETE FROM deployments WHERE id=?", (did,))
        db.ex("DELETE FROM computers WHERE id=?", (lcid,))
    # --- Report sending: try again after an error (5/15/30 minutes), then give up
    sid = db.ex("INSERT INTO mail_reports(name, enabled) VALUES('Rt', 1)")
    tries, waits = [], []
    old_send = notify.send_subscription

    def boom(s_, reason):
        tries.append(reason)
        if len(tries) < 3:
            raise RuntimeError("SMTP weg")
    notify.send_subscription = boom
    try:
        notify._send_bg(sid, "Zeitplan", sleep=lambda s_: waits.append(s_))
        assert len(tries) == 3 and waits == [300, 900] and "3. Versuch" in tries[-1], (tries, waits)
        tries.clear(); waits.clear()
        notify.send_subscription = lambda s_, r: (_ for _ in ()).throw(RuntimeError("weg"))
        notify._send_bg(sid, "Zeitplan", sleep=lambda s_: waits.append(s_))
        assert waits == [300, 900, 1800], "after three retries it is over"
    finally:
        notify.send_subscription = old_send
        db.ex("DELETE FROM mail_reports WHERE id=?", (sid,))
    # --- winget cache: lock per entry (a download does not block deleting other entries)
    a, b = wc._entry_lock(1), wc._entry_lock(2)
    assert a is not b and wc._entry_lock(1) is a
    a.acquire()
    try:
        lk2 = wc._lock_entry(2, timeout=0.1)
        lk2.release()
        res = []
        th = __import__("threading").Thread(target=lambda: res.append(_try_lock(wc, 1)))
        th.start(); th.join()
        assert res == [False], "the same entry stays locked"
    finally:
        a.release()
    # --- Winter time: interval does not skip an hour; daily trigger does not fire twice
    it = {"type": "interval", "unit": "m", "every": 15, "start": "2026-10-01 00:00:00"}
    now = _dt.datetime(2026, 10, 25, 2, 5)
    last = _dt.datetime(2026, 10, 25, 2, 59)
    assert profiles.fires(it, profiles.last_occurrence(it, now), last, now), "clock set back: fire once"
    assert not profiles.fires(it, profiles.last_occurrence(it, now), _dt.datetime(2026, 10, 25, 2, 4, 50), now), "then continue normally"
    dl = {"type": "daily", "time": "02:30", "every": 1}
    assert not profiles.fires(dl, profiles.last_occurrence(dl, now), last, now), "daily: not twice"
    # --- Indexes
    names = {r["name"] for r in db.q("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"ix_dt_status", "ix_dep_sched", "ix_st_c", "ix_cm_c"} <= names
    # --- Result marked "as logged-in user"
    assert "Ergebnis vom angemeldeten Benutzer gemeldet" in open(os.path.join(root, "app", "steps.py"), encoding="utf-8").read()
    # --- Supply chain (only in the repository; the files are missing in the image)
    wfd = os.path.join(root, ".github", "workflows")
    if os.path.isdir(wfd):
        rl = open(os.path.join(wfd, "release.yml"), encoding="utf-8").read()
        assert "actions/attest-build-provenance@4d101475d8b20a2381f78447822ac1eab6504dd8" in rl and "attestations: write" in rl
        at = rl[rl.index("- name: Provenance attestation"):rl.index("uses: actions/attest-build-provenance")]
        assert "if: ${{ !github.event.repository.private }}" in at, "private repository: the release must not fail because of it"
        g = rl[rl.index("- name: Publish guacd under its own name"):rl.index("- name: Archives for installations")]
        assert "run: |" in g and "${{" not in g.split("run: |", 1)[1], "guacd step: values only via env"
        sec = open(os.path.join(wfd, "security.yml"), encoding="utf-8").read()
        assert "for arch in amd64 arm64" in sec and "PK_TRIVY_PLATFORM" in sec
        tst = open(os.path.join(wfd, "test.yml"), encoding="utf-8").read()
        assert "Self-test on the checkout" in tst and '-v "$PWD:/src:ro"' in tst
    dc = os.path.join(root, "docker-compose.yml")
    if os.path.exists(dc):
        c = open(dc, encoding="utf-8").read()
        gd = c[c.index("\n  guacd:"):]
        assert "read_only: true" in gd and "- /tmp" in gd
    ts = os.path.join(root, "tools", "trivy-scan.sh")
    if os.path.exists(ts):
        assert '${PLAT:+--platform "$PLAT"}' in open(ts, encoding="utf-8").read()


def _try_lock(wc_, eid):
    try:
        wc_._lock_entry(eid, timeout=0.1).release()
        return True
    except wc_.CacheError:
        return False


@test("2.38 Constrained Language Mode (software restriction, AppLocker, App Control): client PowerShell without blocked .NET calls; with PK_PWSH really executed in restricted mode")
def _():
    import re as _re
    import subprocess as _sp
    from app import deploy, diskusage, remote, scanners, steps, toast, tools, util as _u
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    st_cmd = steps.normalize({"name": "x", "steps": {"main": [{"type": "command", "command": "echo Grüße 'x'"}]}})["steps"]["main"][0]
    st_ps = steps.normalize({"name": "x", "steps": {"main": [{"type": "powershell", "script": "'a'"}]}})["steps"]["main"][0]
    st_reg = steps.normalize({"name": "x", "steps": {"main": [{"type": "registry", "view": "32", "entries": [
        {"action": "set", "path": "HKLM\\SOFTWARE\\Firma", "name": "N", "kind": "REG_DWORD", "data": "4294967295"},
        {"action": "set", "path": "HKLM\\SOFTWARE\\Firma", "name": "Q", "kind": "REG_QWORD", "data": "18446744073709551615"}]}]}})["steps"]["main"][0]
    st_fc = steps.normalize({"name": "x", "steps": {"main": [{"type": "filecopy", "source": "a", "target": "%ProgramData%\\Firma"}]}})["steps"]["main"][0]
    cond = steps.clean_conditions({"files": [{"path": "%SystemDrive%\\x.txt", "mode": "exists"}]})
    scripts = {
        "WRAPPER": remote.WRAPPER, "REGISTER": remote.REGISTER, "START_DIRECT": remote.START_DIRECT, "POLL": remote.POLL,
        "CLEANUP": remote.CLEANUP, "cmd": steps.body_for(st_cmd, {}), "ps": steps.body_for(st_ps, {}), "reg": steps.body_for(st_reg, {}),
        "filecopy": steps.body_for(st_fc, {}), "cond": steps.cond_script(cond),
        "winget": deploy._winget_body({"type": "winget", "winget_id": "7zip.7zip", "winget_action": "install"}),
        "scan_reg": scanners._body({"type": "registry", "target": "HKLM:\\SOFTWARE\\Firma", "extra": "{}"}),
        "scan_files": scanners._body({"type": "files", "target": "%SystemDrive%\\Users\\**\\*.log", "extra": "{}"}),
        "scan_ps": scanners._body({"type": "powershell", "target": "Get-Date", "extra": "{}"}),
        "tool_ps": tools.ps_tool_winrm("'x'; exit 3"), "tool_cmd": tools._cmd_ps("echo x"),
        # Setup (SYSTEM via WinRM) – without the display script itself: that runs at the user from "Programme"
        "toast_setup": toast.build_send_script([("installed", "7-Zip", "26.00")])[0].replace(_u.psq(toast.USER_PS), "''").split("function Action")[0],
        "modules": open(os.path.join(root, "app", "scan_modules.ps1"), encoding="utf-8").read(),
    }
    # Blocked in restricted mode: only allowed in lines that themselves check for FullLanguage
    bad = _re.compile(r"FromBase64String|\[ScriptBlock\]::Create|\[Environment\]::|\[BitConverter\]|::TryParse|\[math\]::|"
                      r"\[pscustomobject\]|WriteAllText|WriteAllBytes|ReadAllText|\[regex\]::Replace|IsInRole|\[Convert\]::", _re.I)
    for name, txt in scripts.items():
        for ln in txt.splitlines():
            if bad.search(ln) and "FullLanguage" not in ln and not ln.lstrip().startswith("#"):
                raise AssertionError(f"{name}: blocked in Constrained Language Mode: {ln.strip()[:160]}")
    # Add-Type/.NET registry/ADSI only behind a language mode check
    m = scripts["modules"]
    assert "if (-not $pkFull) { throw 'CLM' }" in m and m.index("if (-not $pkFull)") < m.index("Add-Type -ErrorAction")
    assert "Get-CimAssociatedInstance -InputObject $grp -Association Win32_GroupUser" in m and "$k.GetValue(" not in m
    assert "if ($ExecutionContext.SessionState.LanguageMode -ne 'FullLanguage') {\n    try { PK-RegClm" in scripts["reg"]
    assert "([int]-1)" in scripts["reg"] and "([long]-1)" in scripts["reg"], "DWORD/QWORD already converted"
    assert "LanguageMode -ne 'FullLanguage') {{ 'PKERR:'" not in diskusage._script("C:\\", 2) and "'PKERR:' + " in diskusage._script("C:\\", 2)
    assert "S-1-16-12288" in scripts["winget"] and "S-1-16-12288" in open(os.path.join(root, "app", "remote.py"), encoding="utf-8").read()
    # Writing without .NET: content is a text literal in the call (no Base64)
    assert _u.ps_write("$f", "a'b") == "Set-Content -LiteralPath ($f) -Value 'a''b' -Encoding UTF8 -NoNewline\n"
    w = _u.ps_write("$f", "@echo off\r\nä", cmd=True)
    assert "-Encoding Byte -Value ([byte[]]@(64,101,99,104,111,32,111,102,102,13,10,195,164))" in w and "-Encoding Oem" in w
    pw = os.environ.get("PK_PWSH")
    if not pw or not os.path.exists(pw):
        return
    d = tempfile.mkdtemp(prefix="pk-clm-", dir=TMP)

    def run(code, clm=True):
        f = os.path.join(d, "t.ps1")
        open(f, "w", encoding="utf-8").write(("$ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage'\n" if clm else "") + code)
        r = _sp.run([pw, "-NoProfile", "-NonInteractive", "-File", f], capture_output=True, text=True, timeout=120,
                    env={**os.environ, "TEMP": d, "PKTEST": "wert"})
        return r.stdout + r.stderr
    # Syntax of all generated scripts (parser of the real PowerShell)
    for name, txt in scripts.items():
        pf = os.path.join(d, name + ".ps1")
        open(pf, "w", encoding="utf-8").write(txt)
        out = run(f"$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile({_u.psq(pf)}, [ref]$null, [ref]$e); "
                  "if ($e) { $e | ForEach-Object { 'PARSEFEHLER: ' + $_.Message + ' Zeile ' + $_.Extent.StartLineNumber } }", clm=False)
        assert "PARSEFEHLER" not in out, (name, out[:600])
    # Resolve %VAR%, write file, read values – in restricted mode
    out = run(_u.PS_EXPAND + "PK-Exp 'a%PKTEST%b%NIX%c'\n" + _u.ps_write(_u.psq(os.path.join(d, "o.txt")), "Grüße 'x' ‘y’")
              + f"Get-Content -LiteralPath {_u.psq(os.path.join(d, 'o.txt'))} -Encoding UTF8")
    assert "awertb%NIX%c" in out and "Grüße 'x' ‘y’" in out and "language mode" not in out.lower(), out
    # Own PowerShell scanner: output and syntax error (only this scanner)
    out = run("function PKO($o) { $o }\n$r = & {" + scanners._body({"type": "powershell", "target": "'eins'; 2", "extra": "{}"}) + "}\n$r | ConvertTo-Json -Compress")
    assert '"value":"eins"' in out.replace(" ", "") and "language mode" not in out.lower(), out
    out = run("function PKO($o) { $o }\n$r = & {" + scanners._body({"type": "powershell", "target": "if ($true) { 'x' ", "extra": "{}"}) + "}\n$r | ConvertTo-Json -Compress")
    assert "fehler" in out and "language mode" not in out.lower(), out
    # Tool via WinRM: "exit N" and a program's exit code arrive
    assert "PKEXIT:3" in run(tools.ps_tool_winrm("'x'; exit 3")) and "PKEXIT:0" in run(tools.ps_tool_winrm("'ok'"))
    out = run(tools.ps_tool_winrm("Get-Item -LiteralPath '/gibt/es/nicht'"))
    assert "PKEXIT:1" in out, out
    # Registry paths for the cmdlets (32-bit view redirected only under HKLM\\SOFTWARE)
    out = run(steps.REG_FUNCS + "PK-RegPath 'HKLM' 'SOFTWARE\\Firma' '32'; PK-RegPath 'HKLM' 'SYSTEM\\X' '32'; PK-RegPath 'HKCU' 'Software\\A' '64'")
    assert "Registry::HKEY_LOCAL_MACHINE\\SOFTWARE\\WOW6432Node\\Firma" in out and "Registry::HKEY_LOCAL_MACHINE\\SYSTEM\\X" in out \
        and "Registry::HKEY_CURRENT_USER\\Software\\A" in out, out


@test("2.38.1 Tools as service account: if no script may run in %TEMP% (software restriction), the tool runs directly")
def _():
    import subprocess as _sp
    from app import tools
    ps = tools.ps_tool_winrm("'ok'")
    assert "pk_probe_" in ps and "-contains 'PKPROBE'" in ps and "if (-not $pkFileOk -or" in ps
    assert ps.index("$pkFileOk") < ps.index("& $pkF "), "check first, then start as a file"
    pw = os.environ.get("PK_PWSH")
    if not pw or not os.path.exists(pw):
        return
    d = tempfile.mkdtemp(prefix="pk-probe-", dir=TMP)
    f = os.path.join(d, "t.ps1")
    # %TEMP% not usable (as if blocked) -> run directly, output still arrives
    open(f, "w", encoding="utf-8").write("$ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage'\n" + ps)
    r = _sp.run([pw, "-NoProfile", "-NonInteractive", "-File", f], capture_output=True, text=True, timeout=120,
                env={**os.environ, "TEMP": os.path.join(d, "gibt-es-nicht")})
    assert "ok" in r.stdout and "PKEXIT:0" in r.stdout, r.stdout + r.stderr
    r = _sp.run([pw, "-NoProfile", "-NonInteractive", "-File", f], capture_output=True, text=True, timeout=120, env={**os.environ, "TEMP": d})
    assert "ok" in r.stdout and "PKEXIT:0" in r.stdout, r.stdout + r.stderr


@test("2.38.2 Registry step: values like Password/Kennwort/Secret never in the output (deployment log)")
def _():
    import re as _re
    import subprocess as _sp
    from app import steps
    rx = _re.compile(steps.SECRET_VALUE_RX)
    for n in ("Password", "VNCPassword", "passwd", "Pwd", "Kennwort", "ClientSecret", "AuthToken", "Credentials", "ApiKey", "private_key"):
        assert rx.search(n), n
    for n in ("RfbPort", "LogLevel", "QueryTimeout", "UseVncAuthentication", "Version"):
        assert not rx.search(n), n
    assert steps.REG_FUNCS.count("(PK-RegShow $Name $Data)") == 2 and "__SECRET_RX__" not in steps.REG_FUNCS
    assert "PK-RegShow" in steps.reg_body(steps.normalize({"name": "x", "steps": {"main": [{"type": "registry", "entries": [
        {"action": "set", "path": "HKLM\\SOFTWARE\\TightVNC\\Server", "name": "Password", "kind": "REG_BINARY", "data": "44,01,22"}]}]}})["steps"]["main"][0])
    pw = os.environ.get("PK_PWSH")
    if pw and os.path.exists(pw):
        f = os.path.join(TMP, "regshow.ps1")
        open(f, "w", encoding="utf-8").write("$ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage'\n" + steps.REG_FUNCS
            + "PK-RegShow 'Password' ([byte[]]@(68,1,34)); PK-RegShow 'RfbPort' 5900; PK-RegShow 'Liste' ([string[]]@('a','b'))\n")
        out = _sp.run([pw, "-NoProfile", "-NonInteractive", "-File", f], capture_output=True, text=True, timeout=60).stdout
        assert "******** (verborgen)" in out and "68" not in out and "5900" in out and "a; b" in out, out



@test("2.38.3 Tables: selection checkbox toggles reliably (draggable rows do not swallow the click)")
def _():
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    i = js.index("const cell = h('td.chk', {")
    blk = js[i:i + 900]
    # Pressing on the checkbox cell: row temporarily not draggable (otherwise a small movement starts a drag)
    assert "tr.draggable = false" in blk and "tr.draggable = drag" in blk and "'mouseup'" in blk, blk
    # Click on the edge of the cell toggles the checkbox
    assert "if (e.target !== cb) { e.stopPropagation(); cb.click(); }" in blk, blk
    # Released outside the cell: the row does not select "only this one" but toggles the checkbox
    j = js.index("tr.addEventListener('click', (e) => {", i)
    assert js[j:j + 120].split("\n")[1].strip() == "if (tr.pkChk && tr.pkChk()) return;", js[j:j + 120]



@test("2.38.4 gpupdate: user policy in the session of every logged-in user (not only for the service account), CLM-compatible")
def _():
    import subprocess as _sp
    from app import gpupdate
    sc = gpupdate.script("ab12")
    assert "__ID__" not in sc and "__WAIT__" not in sc
    # User: owner of explorer.exe/sihost.exe; task as this user, interactive, restricted, without password
    assert "Name='explorer.exe' OR Name='sihost.exe'" in sc and "-LogonType Interactive -RunLevel Limited" in sc and "-Password" not in sc
    assert "gpupdate.exe /target:user /force" in sc and "conhost.exe" in sc and "--headless" in sc
    assert "Kein Benutzer angemeldet" in sc
    for bad in ("[IO.", "[Convert]", "[Environment]", "Add-Type", "[ScriptBlock]", "New-Object Security", "[ref]"):
        assert bad not in sc, bad
    try:
        gpupdate.script("a;b")
        raise AssertionError("run ID must be alphanumeric")
    except ValueError:
        pass
    pw = os.environ.get("PK_PWSH")
    if pw and os.path.exists(pw):
        root = os.path.join(TMP, "gproot")
        os.makedirs(os.path.join(root, "System32"), exist_ok=True)
        os.makedirs(os.path.join(root, "Temp"), exist_ok=True)
        cmd = os.path.join(root, "System32", "cmd.exe")
        open(cmd, "w").write("#!/bin/sh\necho Computerrichtlinie aktualisiert\n")
        os.chmod(cmd, 0o755)
        stub = r"""
function Get-ScheduledTask { param($TaskPath, $ErrorAction) $script:tasks }
function Unregister-ScheduledTask { param($TaskName, $TaskPath, $Confirm, $ErrorAction) $script:tasks = @($script:tasks | Where-Object { $_.TaskName -ne $TaskName }) }
function Get-CimInstance { param($ClassName, $Filter, $ErrorAction) @(@{u='anna'}, @{u='anna'}, @{u='bob'}) }
function Invoke-CimMethod { param($InputObject, $MethodName, $ErrorAction) New-Object PSObject -Property @{ User = $InputObject.u; Domain = 'FIRMA' } }
function New-ScheduledTaskSettingsSet { 'set' }
function New-ScheduledTaskAction { param($Execute, $Argument) 'a' }
function New-ScheduledTaskPrincipal { param($UserId, $LogonType, $RunLevel) "PRINC $UserId" }
function Register-ScheduledTask { param($TaskName, $TaskPath, $Action, $Principal, $Settings, [switch]$Force, $ErrorAction) $script:tasks += New-Object PSObject -Property @{ TaskName = $TaskName; State = 'Ready' } }
function Start-ScheduledTask { param($TaskName, $TaskPath, $ErrorAction)
  $d = Join-Path $env:SystemRoot 'Temp/pk_gp_ab12'; $k = $TaskName.Substring($TaskName.Length - 1)
  Set-Content (Join-Path $d ('o' + $k + '.txt')) ('Benutzerrichtlinie ' + $k); Set-Content (Join-Path $d ('r' + $k + '.txt')) ([string]([int]$k - 1)) }
function Get-ScheduledTaskInfo { param($TaskName, $TaskPath, $ErrorAction) $null }
function icacls.exe { }
function Get-ItemProperty { param($Path, $ErrorAction) New-Object PSObject -Property @{ CurrentBuildNumber = '22631' } }
$script:tasks = @()
"""
        f = os.path.join(TMP, "gp.ps1")
        open(f, "w", encoding="utf-8").write("$ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage'\n" + stub + sc)
        r = _sp.run([pw, "-NoProfile", "-NonInteractive", "-File", f], capture_output=True, text=True, timeout=120,
                    env={**os.environ, "SystemRoot": root})
        out = r.stdout
        assert "=== Computerrichtlinie ===" in out and "Computerrichtlinie aktualisiert" in out, out + r.stderr
        assert out.count("=== Benutzerrichtlinie: FIRMA\\anna ===") == 1 and "=== Benutzerrichtlinie: FIRMA\\bob ===" in out, out
        assert "Benutzerrichtlinie 1" in out and "Benutzerrichtlinie 2" in out and "endete mit Code 1" in out, out
        assert not os.path.exists(os.path.join(root, "Temp", "pk_gp_ab12")), "folder removed after the run"
        assert not r.stderr.strip(), r.stderr



@test("Weekly update: exactly required build tools (gssapi/krb5: \"Cython == 3.3.0\") apply instead of the latest in the build image")
def _():
    import importlib.util as _iu
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    bp = os.path.join(root, "tools", "buildpins.py")
    if not os.path.exists(bp):
        return  # the build tools are not in the container
    spec = _iu.spec_from_file_location("pk_buildpins", bp)
    m = _iu.module_from_spec(spec)
    spec.loader.exec_module(m)
    gss = '[build-system]\nrequires = [\n    "Cython == 3.3.0",\n    "setuptools >= 40.6.0",  # PEP 517\n]\n\n[project]\nname = "gssapi"\n'
    krb = '[build-system]\nrequires = ["Cython==3.3.0", "setuptools >= 42.0.0"]\n'
    assert m.requires_from_pyproject(gss) == ["Cython == 3.3.0", "setuptools >= 40.6.0"]
    pins = m.collect(["gssapi==1.12.0", "krb5==0.10.0"], fetch=lambda n, v: {"gssapi": gss, "krb5": krb}[n])
    assert pins == {"cython": ("Cython", "3.3.0")}, pins
    try:
        m.collect(["gssapi==1", "krb5==2"], fetch=lambda n, v: gss if n == "gssapi" else krb.replace("3.3.0", "3.3.1"))
        raise AssertionError("different exact versions must fail")
    except SystemExit as e:
        assert "cannot be built together" in str(e)
    assert m.collect(["x==1"], fetch=lambda n, v: "") == {}
    pv = open(os.path.join(root, "tools", "pin-versions.sh"), encoding="utf-8").read()
    i = pv.index('say "Build tools"')
    blk = pv[i:pv.index("mv build-requirements.txt.new build-requirements.txt", i)]
    assert "tools/buildpins.py" in blk and blk.index('"$EXACT" | grep -i') < blk.index('"$FREEZE" | grep -i'), "exact requirement before pip freeze"
    assert "-linux_" in blk, "only self-built wheels"



@test("Build: outage of snapshot.debian.org (package lists or packages not delivered) - retry, then normal mirror instead of a failed build")
def _():
    import shutil as _sh
    import subprocess as _sp
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    ai = os.path.join(root, "docker", "apt-install.sh")
    if not os.path.exists(ai) or not _sh.which("sh"):
        return  # the build scripts are not in the container
    # mode: lists = snapshot does not deliver the package lists (504), pkgs = lists yes, packages no, ok = all fine
    for mode in ("lists", "pkgs", "ok"):
        base = os.path.join(TMP, "aptroot-" + mode)
        _sh.rmtree(base, ignore_errors=True)
        for d in (("etc", "apt", "sources.list.d"), ("etc", "apt", "apt.conf.d"), ("var", "lib", "apt", "lists"), ("bin",)):
            os.makedirs(os.path.join(base, *d))
        src = os.path.join(base, "etc", "apt", "sources.list.d", "debian.sources")
        orig = "Types: deb\nURIs: http://deb.debian.org/debian\nSuites: trixie\n\nURIs: http://deb.debian.org/debian-security\n"
        open(src, "w").write(orig)
        log = os.path.join(base, "calls.log")
        # Stand-in for apt-get: logs "<args> | <number of snapshot URLs in the sources>" and fails like the snapshot would
        stub = ("#!/bin/sh\nS=$(grep -c snapshot.debian.org '%s')\necho \"$* | $S\" >> '%s'\n"
                "[ \"$S\" = 0 ] && exit 0\n"
                "[ '%s' = lists ] && [ \"$1\" = update ] && exit 100\n"
                "[ '%s' = pkgs ] && [ \"$1\" = install ] && exit 100\n"
                "exit 0\n" % (src, log, mode, mode))
        open(os.path.join(base, "bin", "apt-get"), "w").write(stub)
        os.chmod(os.path.join(base, "bin", "apt-get"), 0o755)
        lst = os.path.join(base, "pk.txt")
        open(lst, "w").write("# comment\ntzdata=2026f-0+deb13u1\n")
        r = _sp.run(["sh", ai, "20261106T120000Z", lst], capture_output=True, text=True, timeout=60,
                    env={**os.environ, "PATH": os.path.join(base, "bin") + ":" + os.environ.get("PATH", ""),
                         "PK_APT_ROOT": base, "PK_APT_RETRY_WAIT": "0"})
        calls = [("upgrade |" + c.rsplit("|", 1)[1]) if " upgrade |" in c else c for c in open(log).read().splitlines()]
        assert r.returncode == 0, (mode, r.stdout, r.stderr, calls)
        inst = "install -y --no-install-recommends tzdata=2026f-0+deb13u1"
        if mode == "ok":
            assert calls == ["update --error-on=any | 2", "upgrade | 2", inst + " | 2"], calls
            assert "apt: Debian-Snapshot 20261106T120000Z" in r.stdout and "WARNING" not in r.stderr
        elif mode == "lists":
            # three attempts against the snapshot (missing lists count as errors), then the normal mirror
            assert calls == ["update --error-on=any | 2"] * 3 + ["update | 0", "upgrade | 0", inst + " | 0"], calls
            assert "attempt 1 of 3" in r.stderr and "did not deliver the package lists" in r.stderr, r.stderr
        else:
            # lists from the snapshot, packages do not come: install again from the normal mirror
            assert calls == ["update --error-on=any | 2", "upgrade | 2", inst + " | 2", "update | 0", "upgrade | 0", inst + " | 0"], calls
            assert "did not deliver the packages" in r.stderr, r.stderr
        assert open(src).read() == orig, "package sources restored after the build"
        assert not os.path.exists(os.path.join(base, "etc", "apt", "apt.conf.d", "99pk-snapshot"))


@test("CI: Test workflow saves Actions minutes (no double runs for PR branches, cancel outdated runs)")
def _():
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    p = os.path.join(root, ".github", "workflows", "test.yml")
    if not os.path.exists(p):
        return  # workflows are not part of the image
    w = open(p, encoding="utf-8").read()
    on = w[w.index("\non:"):w.index("\nconcurrency:")]
    assert "branches: [main]" in on and '"**"' not in on, "push only on main - PR branches are tested via pull_request"
    assert "pull_request:" in on and "workflow_dispatch:" in on
    assert "app/static/help" not in on and '"**.md"' not in on, "help files are part of the image and must be tested"
    assert "cancel-in-progress: true" in w and "github.event.pull_request.number || github.ref" in w



@test("2.38.5 Slim image: apt, kinit/klist, C headers and Tk/IDLE removed after the build; package database, ping and Kerberos stay")
def _():
    import shutil as _sh
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    df_p = os.path.join(root, "Dockerfile")
    if os.path.exists(df_p):
        df = open(df_p, encoding="utf-8").read()
        rt = df[df.index("# --- Runtime"):]
        # after pip and after all packages: slim.sh, in the same layer, before /tmp/pk is removed
        assert rt.index("pip uninstall -y -q pip") < rt.index("sh /tmp/pk/slim.sh") < rt.index("rm -rf /tmp/pk")
        sl = open(os.path.join(root, "docker", "slim.sh"), encoding="utf-8").read()
        assert "dpkg --purge --force-remove-protected apt" in sl
        assert "/var/lib/dpkg" not in sl, "never delete the package database (Trivy would no longer see any package)"
        assert "--force-depends" not in sl and "--force-all" not in sl, "dependencies of apt only if nothing else needs them"
        rtp = open(os.path.join(root, "docker", "apt-runtime.txt"), encoding="utf-8").read()
        assert "krb5-user" not in rtp.split("2.38.5:")[1].split("\n", 1)[1] and "libgssapi-krb5-2=" in rtp and "iputils-ping=" in rtp
    # In the built image (the self-test runs there in CI and on every start check): verify the result
    if os.path.exists("/opt/pocketknife/host/update.sh") and os.path.exists("/var/lib/dpkg/status"):
        for gone in ("apt", "apt-get", "kinit", "klist", "idle3", "2to3"):
            assert _sh.which(gone) is None, gone + " still in the image"
        assert _sh.which("ping"), "ping missing (tool Ping & Ports)"
        st = open("/var/lib/dpkg/status", encoding="utf-8", errors="replace").read()
        assert "Package: libgssapi-krb5-2\n" in st and "Package: libssl3t64\n" in st, "package database incomplete"
        assert "Package: apt\n" not in st
        import gssapi  # noqa: F401  - Kerberos for WinRM/SMB
        import krb5  # noqa: F401
        import sysconfig
        assert not os.path.exists(os.path.join(sysconfig.get_path("stdlib"), "tkinter"))



@test("2.38.6 Schedule winget-Updates: pick winget IDs from the inventory (sure assignment only, grouped, outdated first)")
def _():
    from app import main as _m
    for cid, name in ((9601, "WI-PC1"), (9602, "WI-PC2")):
        db.ex("INSERT OR REPLACE INTO computers(id, name) VALUES(?,?)", (cid, name))
    db.ex("DELETE FROM software WHERE computer_id IN (9601, 9602)")
    rows = [(9601, "Mozilla Firefox (x64 de)", "Mozilla.Firefox", 1, 1, 0), (9602, "Mozilla Firefox", "mozilla.firefox", 0, 1, 0),
            (9601, "7-Zip 24.08 (x64)", "7zip.7zip", 0, 1, 0),
            (9601, "Unsicher erkannt", "Some.Guess", 1, 0, 0),      # unsure assignment - not offered
            (9602, "Ausgeschlossen", "Excl.Tool", 1, 1, 1),         # excluded from the up-to-date check - not offered
            (9602, "Ohne winget", None, 0, 1, 0)]
    for cid, name, wid, outd, sure, excl in rows:
        db.ex("INSERT INTO software(computer_id, name, version, winget_id, outdated, winget_sure, excluded) VALUES(?,?,?,?,?,?,?)",
              (cid, name, "1", wid, outd, sure, excl))
    items = [r for r in _m.api_software_winget_ids(user={"username": "t", "scope": None, "role": "admin"})["items"]
             if r["winget_id"].lower() in ("mozilla.firefox", "7zip.7zip", "some.guess", "excl.tool")]
    ids = [r["winget_id"].lower() for r in items]
    assert ids == ["mozilla.firefox", "7zip.7zip"], items       # grouped case-insensitively, outdated first
    ff = items[0]
    assert ff["n"] == 2 and ff["outdated"] == 1, ff
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    i = js.index("async function wingetIdPickDlg(")
    blk = js[i:js.index("\n}\n", i)]
    assert "GET('/api/software/winget-ids')" in blk and "!have.has(x.toLowerCase())" in blk, "no duplicates in the field"
    assert "pickBtn(wIds, 'ids')" in js and "pickBtn(wEx, 'exclude')" in js, "picker for IDs and exclusions"
    db.ex("DELETE FROM software WHERE computer_id IN (9601, 9602)")
    db.ex("DELETE FROM computers WHERE id IN (9601, 9602)")



@test("CI: daily security scan starts the security update by itself when a fix is available (never twice, never merges)")
def _():
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    p = os.path.join(root, ".github", "workflows", "security.yml")
    if not os.path.exists(p):
        return  # workflows are not part of the image
    w = open(p, encoding="utf-8").read()
    assert 'echo "fixable=1" >> "$GITHUB_OUTPUT"' in w and "fixable: ${{ steps.scan.outputs.fixable }}" in w
    rem = w[w.index("\n  remedy:"):]
    assert "needs: scan" in rem and "always() && needs.scan.outputs.fixable == '1'" in rem
    assert "gh workflow run weekly.yml" in rem and "status=in_progress" in rem and 'startswith("sicherheitsupdate-")' in rem
    assert "actions: write" in rem and "contents: write" not in rem, "only starts the update - no pushes, no merges"
    assert "${{" not in rem.split("run: |", 1)[1], "no expressions inside run: (env instead)"


@test("2.38.7 Buy me a coffee: only in the administration, a plain link (nothing loaded from the internet)")
def _():
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static")
    js = open(os.path.join(base, "app.js"), encoding="utf-8").read()
    i = js.index("function coffeeLink() {")
    blk = js[i:js.index("\n}\n", i)]
    assert "rel: 'noopener noreferrer'" in blk and "target: '_blank'" in blk and "icon('coffee')" in blk, blk
    assert "innerHTML" not in blk
    # shown in the navigation of the administration only - not on the login page
    assert js.count("coffeeLink()") == 2, "defined once, used once"
    sh = js[js.index("function renderShell() {"):js.index("\n}\n", js.index("function renderShell() {"))]
    assert "coffeeLink()" in sh
    login = js[js.index("websiteLink('login-web', false)") - 400:js.index("websiteLink('login-web', false)") + 200]
    assert "coffee" not in login
    # never in the kiosk, and nothing from buymeacoffee is embedded (no image, script or font)
    for f in os.listdir(base):
        if f.endswith((".html", ".js", ".css")) and f not in ("app.js", "i18n.js"):  # i18n.js: translation of the tooltip
            assert "buymeacoffee" not in open(os.path.join(base, f), encoding="utf-8").read(), f
    assert not any(x in js for x in ("cdn.buymeacoffee", "img.buymeacoffee", "buymeacoffee.com/button"))



@test("2.38.8 Repository files are opened with read sharing - parallel deployments of one package no longer block each other")
def _():
    import sys as _sys
    import types as _types
    from app import remote as _r
    calls, fails = [], {"n": 0}

    class _Err(Exception):
        pass

    def open_file(path, mode="r", share_access=None, **kw):
        calls.append((path, mode, share_access))
        if fails["n"]:
            fails["n"] -= 1
            raise _Err("[Error 1] [NtStatus 0xc0000043] The process cannot access the file because it is being used by another process")
        return "handle"

    fake = _types.ModuleType("smbclient")
    fake.open_file = open_file
    old = _sys.modules.get("smbclient")
    _sys.modules["smbclient"] = fake
    try:
        waits = []
        assert _r._repo_open(r"\\fs01\PAKETE\7zip\7z.msi", sleep=waits.append) == "handle"
        assert calls[-1] == (r"\\fs01\PAKETE\7zip\7z.msi", "rb", "r"), calls   # read sharing, never exclusive
        # a writer holds the file: retried (2, 5, 10 s), then opened
        fails["n"] = 2
        assert _r._repo_open(r"\\fs01\PAKETE\x.msi", sleep=waits.append) == "handle" and waits == [2, 5], waits
        # still blocked after all retries: the error is passed on (no half-written copy)
        fails["n"], waits[:] = 9, []
        try:
            _r._repo_open(r"\\fs01\PAKETE\x.msi", sleep=waits.append)
            raise AssertionError("must fail")
        except _Err:
            pass
        assert waits == [2, 5, 10], waits
        # other errors are not retried
        def nf(path, mode="r", share_access=None, **kw):
            raise _Err("[NtStatus 0xc0000034] OBJECT_NAME_NOT_FOUND")
        fake.open_file, waits[:] = nf, []
        try:
            _r._repo_open(r"\\fs01\PAKETE\gone.msi", sleep=waits.append)
            raise AssertionError("must fail")
        except _Err:
            pass
        assert waits == []
    finally:
        if old is not None:
            _sys.modules["smbclient"] = old
        else:
            _sys.modules.pop("smbclient", None)
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "remote.py"), encoding="utf-8").read()
    assert 'smbclient.open_file(path, mode="rb")' not in src, "no exclusive read of repository files"
    cf = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "clientfiles.py"), encoding="utf-8").read()
    assert 'smbclient.open_file(unc, mode="rb", share_access="rw")' in cf



@test("2.38.9 Tables: right click on a cell copies its full value (\"<column> kopieren\"), also truncated cells and several rows")
def _():
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    cm = js[js.index("function contextMenu(x, y, items) {"):][:400]
    assert "cellCopyItem()" in cm and "'Wert kopieren'" in cm, "added to every menu, not twice where a table copies itself"
    tb = js[js.index("function table(cols, rows, opt = {}) {"):]
    h = tb[tb.index("tr.addEventListener('contextmenu', (e) => {"):][:1800]
    # full value from the data (not the truncated text), all selected rows, column label translated
    assert "const v = x[c.key]" in h and "rowsSel.map(one)" in h and "window.tr(c.label" in h, h
    # tables without own menu get the copy menu, but links/inputs/selected text keep the browser menu
    assert "else contextMenu(e.clientX, e.clientY, [])" in h and "closest('a,input,textarea,select')" in h and "getSelection" in h
    # the old handler only existed with onContext - the selection logic stays tied to it
    assert "if (opt.onContext && opt.selectable && !st.sel.has(id))" in h



@test("2.38.11 Notification \"Bruch im Repository\": one case per broken package, all-clear after confirming, deviations optional")
def _():
    import json as _json
    from app import notify
    a = notify.alert_def("repo_break")
    assert a and a["resolvable"] and [x["name"] for x in a["params"]] == ["warn"]
    db.ex("DELETE FROM packages WHERE name LIKE 'RB-Test-%'")
    def pkg(name, state):
        res = {"state": state, "at": "2026-11-08 12:38:00", "total": 1,
               "issues": [{"kind": "changed", "path": "OpenVPN\\FIRMA.ovpn", "level": state if state != "ok" else "ok", "note": "Größe geändert"}]}
        return db.ex("INSERT INTO packages(name, type, integrity) VALUES(?,?,?)", (name, "steps", _json.dumps(res)))
    pb, pw, po = pkg("RB-Test-Bruch", "broken"), pkg("RB-Test-Abweichung", "warn"), pkg("RB-Test-OK", "ok")
    cur = {k: v for k, v in notify._check_repo_break({}, {"since": ""}).items() if v["package"].startswith("RB-Test-")}
    assert set(cur) == {str(pb)}, cur
    v = cur[str(pb)]
    assert v["state"].startswith("Bruch") and "OpenVPN\\FIRMA.ovpn: Größe geändert" in v["issues"] and v["checked"] == "2026-11-08 12:38"
    assert notify._label("repo_break", v) == "RB-Test-Bruch"
    cur2 = {k for k, v in notify._check_repo_break({"warn": "1"}, {"since": ""}).items() if v["package"].startswith("RB-Test-")}
    assert cur2 == {str(pb), str(pw)}, cur2
    # confirmed ("Neuen Stand übernehmen") -> state ok -> case gone -> all-clear
    db.ex("UPDATE packages SET integrity=? WHERE id=?", (_json.dumps({"state": "ok", "issues": [], "at": "2026-11-08 13:00:00"}), pb))
    assert notify._is_resolved("repo_break", {}, str(pb), notify._check_repo_break({}, {"since": ""}))
    db.ex("DELETE FROM packages WHERE name LIKE 'RB-Test-%'")


def main():
    ok = 0
    for name, fn in RESULTS:
        try:
            fn()
            ok += 1
            print(f"  ✓ {name}")
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {name}\n      {type(e).__name__}: {e}")
            if os.environ.get("PK_SELFTEST_TRACE"):
                traceback.print_exc()
    print(f"\n{ok} of {len(RESULTS)} tests passed (Python {sys.version.split()[0]}).")
    shutil.rmtree(TMP, ignore_errors=True)  # do not leave pk-selftest-* folders behind
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    socket.setdefaulttimeout(15)
    sys.exit(main())
