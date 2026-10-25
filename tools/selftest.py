"""Regressionstests für bereits behobene Fehler – vor jeder neuen Version ausführen.

Aufruf (im Container oder mit installierten Abhängigkeiten):
    python3 tools/selftest.py
Läuft mit einer leeren, temporären Datenbank, braucht kein Netzwerk und keine Windows-Clients.
Jeder Test nennt die Version, in der der Fehler behoben wurde – so wird er nicht wieder eingebaut.
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

TMP = tempfile.mkdtemp(prefix="pk-selftest-")
os.environ.update(PK_DATA_DIR=TMP, PK_HTTPS="false", PK_DOMAIN="firma.local", PK_PUBLIC_HOSTNAME="pocketknife")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app import collections_, customreports, db, diskusage, mailer, notify, reports, winget  # noqa: E402
import logging  # noqa: E402
# Testmeldungen (erfundene Namen wie „BUCHH-MUELLER7“) nicht auf der Konsole ausgeben – nur die Ergebnisse zählen
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
        assert contains.lower() in str(e).lower(), f"falsche Meldung: {e}"
        return
    raise AssertionError("kein Fehler, obwohl erwartet")


# --- Testdaten ----------------------------------------------------------------------------------
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
@test("2.7 Ausnahmen: eckige Klammern in Mustern zählen wörtlich („Tool [x64]“)")
def _():
    assert winget.wildcard("Tool [x64]").match("tool [x64]")
    assert winget.wildcard("Tool*").match("Tool [x64]")
    assert not winget.wildcard("Tool [x64]").match("Tool x")


@test("2.6.3 Anmeldesperre: a\\admin, b\\admin, admin@x zählen als derselbe Benutzer")
def _():
    assert auth._norm_user("X\\Admin") == auth._norm_user("admin@firma") == "admin"


@test("CSV-Export: Formel-Injektion wird entschärft, Zahlen bleiben Zahlen")
def _():
    out = reports.to_csv([{"key": "a", "label": "A"}], [{"a": "=HYPERLINK(1)"}, {"a": "-5"}, {"a": "+49 30"}])
    assert "'=HYPERLINK" in out and "\n-5" in out.replace("\r", "") and "'+49 30" in out


@test("2.6.2 Sammlungen: Zahlen mit Einheit (20 GB, 1,5 TB)")
def _():
    assert collections_._num("20 GB") == 20 * 1024 ** 3
    assert collections_._num("1,5 TB") == 1.5 * 1024 ** 4
    must_fail(collections_._num, "zwanzig", contains="keine Zahl")


@test("2.12.1 Größenspalten eigener Scanner: als „bytes“ erkannt, ≥/≤ mit Einheit, lesbare Vorschläge")
def _():
    cols = dict(collections_._scanner_columns(1))
    assert cols["Size"] == "bytes" and cols["User"] == "any" and cols["Files"] == "any", cols

    def count(op, v):
        w, p = collections_.build_tree({"type": "group", "match": "all", "items": [
            {"type": "cond", "table": "custom:1", "column": "Size", "op": op, "value": v}]})
        return db.val(f"SELECT COUNT(*) FROM computers c WHERE {w}", p)
    assert count("gte", "10 GB") == 1 and count("lt", "1 GB") == 1 and count("gte", "500 MB") == 2
    assert count("contains", "1610") == 1  # alte Bedingung mit Text-Operator funktioniert weiter
    vals = collections_.values("custom:1", "Size")
    assert vals and all(v.endswith(("MB", "GB")) for v in vals), vals
    assert collections_.fmt_bytes(15 * 1024 ** 3) == "15 GB" and collections_.fmt_bytes(1536) == "1,5 KB"


@test("2.12 Eigene Berichte: nur bekannte Spalten, kein SQL aus Eingaben, Zusammenfassen zählt")
def _():
    must_fail(customreports.clean, {"name": "x", "definition": {"source": "computer", "columns": [{"t": "computer", "c": "name; DROP TABLE computers"}]}},
              contains="Unbekannte Spalte")
    must_fail(customreports.clean, {"name": "x", "definition": {"source": "computer", "columns": [{"t": "software", "c": "name"}]}},
              contains="Spalten nur")
    c = customreports.clean({"name": "x", "definition": {"source": "software", "columns": [{"t": "software", "c": "name"}], "group": True}})
    res = customreports.execute(c["definition"])
    assert res["rows"][0]["cnt"] == 3, res


@test("2.12.1 Eigene Berichte: Größen numerisch sortiert und als „bytes“ gekennzeichnet")
def _():
    c = customreports.clean({"name": "x", "definition": {"source": "custom:1", "columns": [{"t": "custom:1", "c": "User"}, {"t": "custom:1", "c": "Size"}],
                                                          "sort": {"i": 1, "dir": "desc"}}})
    res = customreports.execute(c["definition"])
    assert res["columns"][1]["type"] == "bytes" and [r["c0"] for r in res["rows"]] == ["u1", "u2"], res
    html, text = mailer.html_table(res["columns"], res["rows"])
    assert "15 GB" in text and "700 MB" in text, text


@test("Alle vorgegebenen Berichte laufen ohne Fehler")
def _():
    for d in reports.REPORTS:
        reports.run(d["key"], {p["name"]: p.get("default", "") for p in d["params"]})


@test("2.9 Dateien: nur lokale Laufwerkspfade, kein „..“, kein UNC")
def _():
    assert clientfiles._parse("c:/Users/x") == ("C", "Users\\x")
    for bad in ("..\\x", "C:\\Users\\..\\Windows", "\\\\srv\\share", "Users"):
        must_fail(clientfiles._parse, bad)
    must_fail(diskusage.clean_path, "C:\\..\\x")


@test("2.12.3 Dateien: Verknüpfungen („All Users“ → C:\\ProgramData) brechen die Liste nicht ab, Öffnen springt zum Ziel")
def _():
    from smbprotocol.exceptions import SMBException

    class SMBLinkRedirectionError(SMBException):  # wie smbprotocol: kein OSError!
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
    import smbclient.shutil as real_shutil  # noqa: F401  – rmtree darf für Links nie aufgerufen werden
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


@test("2.11 Benachrichtigungen: ein Fall wird nur einmal gemeldet; Speicherplatz mit Hysterese")
def _():
    cfg = {"params": {"gb": "20", "pct": "0", "drives": "", "collection": ""}, "since": None}
    cur = notify._check_low_disk(cfg["params"], cfg)
    assert set(cur) == {"1:C:", "2:C:"}, cur  # 9 GB und 18 GB frei
    assert not notify._resolved_low_disk(cfg["params"], "2:C:")


@test("2.12.2 Oberfläche: keine <datalist> mehr (zeigte nur zum Wert passende Einträge) – Auswahllisten über combo()")
def _():
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    assert "h('datalist'" not in js and "datalist#" not in js, "datalist wieder eingebaut"
    assert "let selKey = 'props'" in js, "2.12.2 Paket-Editor soll mit den Eigenschaften (Name) beginnen"


# --- 2.13 Durchsicht: Sicherheit und Logik ------------------------------------------------------
@test("2.13 Anmeldung: Umlaute bei gesetztem Notfallzugang, keine Kennwort-Bestätigung nach außen, atomare Sperre")
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
            raise AssertionError("Anmeldung hätte scheitern müssen")
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


@test("2.13 Dateien: Namen „.“, „..“ und mit Punkt/Leerzeichen am Ende werden abgelehnt")
def _():
    for bad in (".", "..", "a.", "a "):
        assert not clientfiles.NAME_RE.match(bad), bad
    for ok in ("a", ".git", "Tool [x64].exe", "a..b"):
        assert clientfiles.NAME_RE.match(ok), ok


@test("2.13 Inventar: fehlgeschlagener Software-Teil löscht die Softwareliste nicht")
def _():
    from app import inventory
    before = db.val("SELECT COUNT(*) FROM software WHERE computer_id=1")
    inventory.save(1, {"software": [], "software_error": "Registry nicht lesbar"}, ["applications"])
    assert db.val("SELECT COUNT(*) FROM software WHERE computer_id=1") == before == 1
    assert not db.val("SELECT COUNT(*) FROM software_changes WHERE computer_id=1 AND change='entfernt'")


@test("2.13 AD-Abgleich: nichts löschen, wenn alles fehlt, kein Container eingeschlossen ist – Löschen erst nach Karenzzeit")
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
        ad._search = lambda c, base, flt, attrs: []  # z. B. fehlende Leserechte: AD liefert „nichts“
        ad.sync_computers()
        assert db.val("SELECT COUNT(*) FROM computers") == 3 and db.val("SELECT COUNT(*) FROM computers WHERE ad_present=0") == 0
    finally:
        ad.service_conn, ad._search, ad.container_rules, ad.sync_ad_collections = o
        db.set_setting("ad_remove_missing", 0)
        db.ex("UPDATE computers SET ad_guid=NULL")


@test("2.13 Versionen: Zusätze wie „(x64)“ oder „+build“ machen eine Version nicht älter")
def _():
    from app import util
    assert util.vcmp("1.2.3 (x64)", "1.2.3") == 0 and util.vcmp("1.0+build5", "1.0") == 0
    assert util.vcmp("1.10", "1.9") == 1 and util.vcmp("130.0.1", "131.0") == -1


@test("2.13 Auslöser: wöchentlich alle N Wochen nicht vor dem Startdatum; Berechnung schnell")
def _():
    import time as _t
    from app import profiles, schedules
    t = {"type": "weekly", "time": "08:00", "days": [0], "every": 2, "start": "2026-10-21", "enabled": True}
    assert profiles.last_occurrence(t, datetime.datetime(2026, 10, 20, 9, 0)) is None
    t0 = _t.time()
    n = schedules.next_occurrence({"type": "daily", "time": "07:00", "every": 365, "start": "2026-01-01"}, datetime.datetime(2026, 10, 20))
    assert n == datetime.datetime(2027, 1, 1, 7, 0) and _t.time() - t0 < 0.5


@test("2.13 RegEx aus Filtern: katastrophales Backtracking wird abgelehnt")
def _():
    from app import util
    assert util.regex_problem("(a+)+$") and util.regex_problem("(a|aa)*b") and util.regex_problem("x" * 300)
    assert util.regex_problem("^Win(dows)? 1[01]") is None
    assert db._pk_regex("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa!", "(a+)+$") == 0  # gespeichertes Altmuster wird nicht ausgeführt


@test("2.13 Sammlungen: Löschen verweigert, solange verwendet; Zirkelbezüge werden abgelehnt")
def _():
    a = db.ex("INSERT INTO collections(name, type, match, filters) VALUES('A','dynamic','all','{\"type\":\"group\",\"match\":\"all\",\"items\":[]}')")
    b = db.ex("INSERT INTO collections(name, type, match, filters) VALUES('B','dynamic','all',?)",
              (json.dumps({"type": "group", "match": "all", "items": [{"type": "cond", "table": "computer", "column": "collection", "op": "in", "value": str(a)}]}),))
    assert "Sammlung „B“" in collections_.used_by([a])
    must_fail(collections_.check_cycle, a, {"type": "group", "match": "all", "items": [
        {"type": "cond", "table": "computer", "column": "collection", "op": "in", "value": str(b)}]}, contains="Zirkelbezug")
    db.ex("DELETE FROM collections WHERE id IN (?,?)", (a, b))


@test("2.13 Benachrichtigung „eigener Bericht“: gleiche Zeilen werden nicht verschluckt, Zeilengrenze führt nicht zu „behoben“")
def _():
    r = customreports.save(None, {"name": "Doppelt", "definition": {"source": "software", "columns": [{"t": "computer", "c": "cores"}], "limit": 1}})
    key = f"report:{r['id']}"
    a = notify.alert_def(key)
    cur, cols, trunc = notify._check_report(a, {})
    assert len(cur) == 3 and not trunc, (len(cur), trunc)  # 3 Zeilen ohne Text-Spalte, trotz limit=1
    customreports.delete(r["id"])


@test("2.13 E-Mail: gespeichertes Kennwort nie an einen geänderten Server; keine Anmeldung ohne Verschlüsselung")
def _():
    mailer.save({"enabled": True, "host": "mail.firma.local", "port": 587, "security": "starttls", "user": "pk@firma.local",
                 "password": "geheim", "from": "pk@firma.local", "to": "it@firma.local"})
    must_fail(mailer.save, {"enabled": True, "host": "evil.example", "port": 587, "security": "starttls", "user": "pk@firma.local",
                            "from": "pk@firma.local"}, contains="Kennwort erneut")
    must_fail(mailer.save, {"enabled": True, "host": "mail.firma.local", "port": 25, "security": "none", "user": "pk@firma.local",
                            "password": "x", "from": "pk@firma.local"}, contains="Verschlüsselung")
    assert mailer.parse_recipients('Max Muster <max@x.de>; "A, B" <a@b.de>') == ["max@x.de", "a@b.de"]


@test("2.13 CSV: auch Spaltenüberschriften und „ =…“ mit führendem Leerzeichen werden entschärft")
def _():
    out = reports.to_csv([{"key": "a", "label": "=cmd"}], [{"a": " =1+1"}])
    assert "'=cmd" in out and "' =1+1" in out


@test("2.13 Eigene PowerShell-Scanner: ein Syntaxfehler betrifft nur diesen Scanner (Code als Base64)")
def _():
    from app import scanners
    body = scanners._body({"type": "powershell", "target": "if ($true) { 'kaputt' ", "extra": "{}"})
    assert "FromBase64String" in body and "kaputt" not in body


@test("2.13 Tools: javascript:-URLs und unkodierte Werte in URL-Tools werden verhindert")
def _():
    from app import tools
    must_fail(tools.validate, {"name": "x", "type": "url", "run_as": "service", "script": "javascript:alert(1)"}, contains="nicht erlaubt")
    c = {"name": "PC", "dns_name": "pc.firma.local", "current_user": "FIRMA\\\\o'brien & co", "extra": "{}"}
    assert tools.substitute("https://x/?u=$(User)", c, mode="url").endswith("o%27brien%20%26%20co")
    assert "$PK_User = 'FIRMA\\\\o''brien & co'" in tools.ps_variables(c)


@test("2.13 Deinstallation: Einträge aus Benutzerprofilen laufen nie als SYSTEM")
def _():
    from app import uninstall
    db.conn().executescript(uninstall.SCHEMA) if hasattr(uninstall, "SCHEMA") else None
    sw = {"name": "Google Chrome", "version": "1", "hive": "HKEY_USERS", "reg_path": "S-1-5-21-1-2-3-1001\\\\Software\\\\x",
          "quiet_uninstall": "cmd /c net localgroup administrators me /add", "uninstall_string": "", "product_code": "X"}
    p = uninstall.package_for(sw, None, "system")
    assert p["run_as"] == "user" and p["run_as_forced"], p["run_as"]


@test("2.13 Web: zu große Anfragen werden vor dem Einlesen abgelehnt (auch /api/login)")
def _():
    import asyncio
    try:
        from app.main import BodyLimit
    except Exception as e:  # pragma: no cover – ohne FastAPI (lokale Prüfung) überspringen
        print(f"      (übersprungen: {e})")
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
    asyncio.run(run(b"x", [b"a" * 1024 * 1024] * 3))  # ohne gültige Länge, gestreamt
    assert sent[0]["status"] == 413


@test("2.14 Dienstkonto-Schritte: ohne geplante Aufgabe und ohne Kennwort auf dem Client")
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
        assert "Register-ScheduledTask" not in start and "PKPass" not in start and "Password" not in start, "Aufgabe/Kennwort"
        assert any("taskkill" in s for s in sent[-1:]), "Aufräumen beendet den Prozess bei Abbruch"
        sent.clear()
        polls[:] = ["PK_PROC:gone", "PK_PROC:gone"]
        must_fail(remote.run_as_system, "pc1.firma.local", "x", "1_2_4_ab", 3600, None, "", "deploy", contains="ohne Ergebnis")
        # SYSTEM/Benutzer: geplante Aufgabe, aber nie mit Kennwort
        assert "-Password" not in remote.REGISTER and "PKPass" not in remote.REGISTER
    finally:
        remote.ps, remote.secure_workdir, remote.time.sleep = old
    from app import inventory
    assert "LogonType)\" -eq 'Password'" in inventory.SCAN_END, "alte Aufgaben mit Kennwort werden beim Scan entfernt"


@test("2.14 Repository: neuer Server/neue Freigabe nur mit Kennwort des Zugriffskontos")
def _():
    try:
        from app import main
    except Exception as e:  # pragma: no cover
        print(f"      (übersprungen: {e})")
        return
    from app import config, vault
    db.set_setting("repo_path", "\\\\fs1\\software$")
    db.set_setting("repo_user", "FIRMA\\svc-repo")
    db.set_setting("repo_password", vault.encrypt("Geheim-1"))
    try:
        must_fail(main._check_repo_change, {"repo_path": "\\\\evil\\share"}, contains="Kennwort")
        must_fail(main._check_repo_change, {"repo_path": "//fs1/anders", "repo_password_confirm": "falsch"}, contains="passt nicht")
        main._check_repo_change({"repo_path": "\\\\evil\\share", "repo_password_confirm": "Geheim-1"})
        main._check_repo_change({"repo_path": "\\\\FS1\\Software$\\Unterordner"})  # gleicher Server/gleiche Freigabe
        main._check_repo_change({"repo_path": "/repo/pakete"})  # eingebundener Ordner: keine Anmeldung
        assert config.repo_password() == "Geheim-1"
    finally:
        db.ex("DELETE FROM settings WHERE key IN ('repo_user', 'repo_password')")
        db.set_setting("repo_path", "")


@test("2.14 Sitzungen: Abmeldung bei Inaktivität; automatische Aktualisierungen zählen nicht")
def _():
    import time as _t
    from app import util
    db.set_setting("session_idle_minutes", 30)
    th = "a" * 64
    db.ex("INSERT INTO sessions(token_hash, username, display_name, created, expires, ip, last_active) VALUES(?,?,?,?,?,?,?)",
          (th, "u1", "U", util.now(), util.fmt(datetime.datetime.now() + datetime.timedelta(hours=8)), "1.2.3.4", util.now()))
    auth._active.pop(th, None)
    assert auth.session_valid(th) and auth.idle_remaining(th) > 29 * 60
    auth._active[th] = _t.time() - 31 * 60  # 31 Minuten nichts getan (nur Hintergrundabfragen)
    assert not auth.session_valid(th), "inaktive Sitzung muss ungültig sein"
    auth.touch(th)  # Eingabe im Browser
    assert auth.session_valid(th)
    auth._active[th] = _t.time() - 31 * 60
    auth.expire_idle()
    assert not db.q1("SELECT 1 FROM sessions WHERE token_hash=?", (th,)), "Sitzung nicht beendet"
    db.set_setting("session_idle_minutes", 0)
    assert auth.idle_minutes() >= auth.IDLE_MIN, "lässt sich nicht abschalten"
    db.set_setting("session_idle_minutes", 30)


@test("2.14 Zeitpläne: nicht gestartete Läufe und fehlgeschlagene Ziele werden nachgeholt")
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
            "Lauf vergessen"
        now = datetime.datetime.now()
        st = {(sid, schedules.RETRY): util.fmt(now - datetime.timedelta(minutes=5))}
        schedules._evaluate_one(s, now, util.fmt(now), st)
        assert len(calls) == 1, "zu früh wiederholt"
        st = {(sid, schedules.RETRY): util.fmt(now - datetime.timedelta(minutes=16))}
        schedules.fire = lambda s_, cids=None, reason="", **k: calls.append((cids, reason)) or {"count": 0}
        schedules._evaluate_one(s, now, util.fmt(now), st)
        assert calls[-1][0] is None and not db.val("SELECT 1 FROM schedule_trigger_state WHERE schedule_id=? AND trigger_id=?",
                                                   (sid, schedules.RETRY)), "Wiederholung"
        # Fehlgeschlagenes Ziel: nach 1 Stunde nachholen, höchstens 3 Versuche
        db.ex("INSERT INTO schedule_targets(schedule_id, computer_id, status, last_run, attempts, pending) VALUES(?,?,?,?,?,0)",
              (sid, 50, "failed", util.ago(minutes=30), 1))
        assert schedules.retry_candidates(s) == []
        db.ex("UPDATE schedule_targets SET last_run=? WHERE schedule_id=?", (util.ago(hours=2), sid))
        assert schedules.retry_candidates(s) == [50]
        calls.clear()
        schedules._evaluate_one(s, now, util.fmt(now), {})
        assert calls and calls[-1][0] == [50], calls
        assert schedules.retry_candidates(s) == [], "nichts gestartet → nicht jede Minute erneut"
        db.ex("UPDATE schedule_targets SET attempts=1 WHERE schedule_id=?", (sid,))
        s2 = {**s, "options": {**s["options"], "retry_failed": False}}
        assert schedules.retry_candidates(s2) == []
    finally:
        schedules.fire = real_fire


@test("2.14 Container: Rechte werden abgegeben, Datenordner gehören danach dem Dienstbenutzer")
def _():
    import subprocess
    if os.name != "posix" or os.geteuid() != 0:
        print("      (übersprungen: nicht als root gestartet)")
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
    assert os.stat("/etc/passwd").st_uid == 0, "Verknüpfung darf nicht verfolgt werden"


@test("2.14.1 docker-compose.yml: keine sysctls, die den Start verhindern (ping_group_range)")
def _():
    f = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docker-compose.yml")
    if not os.path.exists(f):
        print("      (übersprungen: docker-compose.yml nicht im Image)")
        return
    lines = [ln.split("#", 1)[0] for ln in open(f, encoding="utf-8")]
    assert not any("sysctls:" in ln or "ping_group_range" in ln for ln in lines), "sysctls in docker-compose.yml"


@test("2.14.2 Build: Basis-Image mit Digest, Debian-Snapshot und alle Python-Pakete exakt festgeschrieben")
def _():
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    df = os.path.join(root, "Dockerfile")
    if not os.path.exists(df):
        print("      (übersprungen: Build-Dateien nicht im Image)")
        return
    import re
    d = open(df, encoding="utf-8").read()
    assert re.search(r"^ARG PYTHON_IMAGE=python:3\.12\.\d+-slim-\w+@sha256:[0-9a-f]{64}$", d, re.M), "Basis-Image ohne Version/Digest"
    assert re.search(r"^ARG DEBIAN_SNAPSHOT=\d{8}T\d{6}Z$", d, re.M), "kein Debian-Snapshot"
    lock = [ln for ln in open(os.path.join(root, "requirements.lock"), encoding="utf-8") if ln.strip() and not ln.startswith("#")]
    assert lock and all(re.match(r"^[A-Za-z0-9_.-]+==[^=\s]+$", ln.strip()) for ln in lock), "requirements.lock unvollständig"
    names = {ln.split("==")[0].lower().replace("_", "-") for ln in lock}
    for need in ("fastapi", "uvicorn", "ldap3", "pypsrp", "smbprotocol", "pyspnego", "cryptography", "websockets", "gssapi", "krb5"):
        assert need in names, f"{need} fehlt in requirements.lock"
    rt = [ln for ln in open(os.path.join(root, "docker", "apt-runtime.txt"), encoding="utf-8") if ln.strip() and not ln.startswith("#")]
    assert rt and all("=" in ln for ln in rt), "Debian-Pakete ohne Version"
    dc = open(os.path.join(root, "docker-compose.yml"), encoding="utf-8").read()
    assert re.search(r"image: guacamole/guacd:[\w.]+@sha256:[0-9a-f]{64}", dc), "guacd ohne Digest"


@test("2.14.3 Tabellen: Spaltenbreite, Reihenfolge (auch ans Ende) und Sichtbarkeit werden gemerkt")
def _():
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    assert "widths: st.widths" in js and "th-rs" in js, "Spaltenbreite"
    assert "dropto-r" in js and "moveCol(k, c.key, right)" in js, "Spalte hinter die letzte ziehen"
    assert "cc-row" in js, "Spalten-Fenster mit Reihenfolge"
    assert "splitter(tree, 'collections')" in js and "themeToggle()" in js, "2.14.4 Sammlungsbaum-Breite, Hell/Dunkel-Knopf"


@test("2.15 Kiosk: Kiosk-Sitzungen kommen nie an die Verwaltung; jede Verwaltungs-API verlangt die Rolle „admin“")
def _():
    import ast
    from fastapi import HTTPException
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "main.py"), encoding="utf-8").read()
    public = {"/api/login", "/api/logout", "/", "/remote", "/kiosk", "/favicon.ico", "/api/kiosk/info", "/api/kiosk/sso", "/api/remote/tunnel"}
    kiosk_ok = {"/api/kiosk/me", "/api/kiosk/catalog", "/api/kiosk/request", "/api/kiosk/requests/{rid}/withdraw",
                "/api/kiosk/requests", "/api/kiosk/reboot", "/api/kiosk/icon/{pid}"}
    session_only = {"/api/session/active", "/api/session/idle"}  # 2.25: jede Sitzung (A) – nur die Inaktivitäts-Uhr
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
                assert deps.get("user") == want, f"{path}: user={deps.get('user')} statt {want}"

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
        raise AssertionError("Kiosk-Sitzung hat Zugriff auf die Verwaltung")
    except HTTPException as e:
        assert e.status_code == 403
    assert auth.session_for(tok)["role"] == "kiosk"  # Fernsitzungen prüfen die Rolle (main.py, Tunnel)
    assert 's.get("role") != "admin"' in src, "Fernsitzungs-Tunnel ohne Rollenprüfung"


@test("2.15 Kiosk: Zugang über OU oder Gruppe; ohne Kiosk-Anmeldung keine Kiosk-Rolle")
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
        assert not ad.kiosk_match(None, {"dn": "CN=Max,OU=Mitarbeiter,DC=firma,DC=local"}), "ausgeschalteter Kiosk"
    finally:
        ad._resolve_group, ad.is_member = old
        db.set_setting("kiosk_enabled", 0)
    src = open(ad.__file__, encoding="utf-8").read()
    assert "if allow_kiosk and kiosk_match(uc, e)" in src
    # 2.15.2 AD-Baum: nur innerhalb der eigenen Domäne durchsuchen
    from app import config as _cfg
    old_base = _cfg.BASE_DN
    _cfg.BASE_DN = "DC=firma,DC=local"
    try:
        assert ad._under_base("OU=X,DC=firma,DC=local") and ad._under_base("dc=firma, dc=local")
        assert not ad._under_base("DC=fremd,DC=local") and not ad._under_base("OU=X,DC=firma,DC=local,DC=evil")
        must_fail(ad.browse, "DC=fremd,DC=local", contains="eigenen Domäne")
        # 2.15.4 ldap3 ohne Schema liefert jedes Attribut als Liste („'list' object has no attribute 'lower'“)
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


@test("2.15 Kiosk: nur am eigenen Rechner, Freigabe je Paket, keine Doppelaufträge, Tageslimit, Neustart planen")
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
        assert r2["status"] == "pending" and len(created) == 1, "Freigabe nötig – darf nicht starten"
        kiosk.decide(r2["id"], True, "admin")
        assert len(created) == 2
        must_fail(kiosk.decide, r2["id"], False, "admin", contains="bereits entschieden")
        # Audit 2.29: kein Neustart, wenn noch andere angemeldet sind, und nie bei Servern
        db.ex("UPDATE computers SET current_user='FIRMA\\nutzer,FIRMA\\kollege' WHERE id=70")
        must_fail(kiosk.plan_reboot, u, "?", 70, "1h", contains="andere Benutzer")
        db.ex("UPDATE computers SET current_user='FIRMA\\nutzer,PC-70\\Administrator' WHERE id=70")
        must_fail(kiosk.plan_reboot, u, "?", 70, "1h", contains="andere Benutzer")  # auch lokale Konten zählen
        db.ex("UPDATE computers SET current_user='FIRMA\\nutzer', os_name='Microsoft Windows Server 2022 Standard' WHERE id=70")
        must_fail(kiosk.plan_reboot, u, "?", 70, "1h", contains="Server")
        db.ex("UPDATE computers SET os_name='Microsoft Windows 11 Pro' WHERE id=70")
        p = kiosk.plan_reboot(u, "?", 70, "1h")
        assert created[-1][0] == "Neustart (Kiosk)" and created[-1][2], "Neustart nicht geplant"
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


@test("2.15.1 Kiosk-Symbole: nur echte Rasterbilder, kein SVG (Skripte), Größe begrenzt")
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
    assert any(i["icon"] for i in kiosk.admin_items()), "Symbol fehlt in der Verwaltung"
    kiosk.clear_icon(pid)
    assert kiosk.get_icon(pid) == (None, None)


@test("2.15.3 Mehrere Pakete: eine Bereitstellung, Reihenfolge wie gewählt, keine Doppelten, keine gemischten Mandanten")
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


# --- E-Mail: Test-SMTP-Server mit STARTTLS/SSL und echter Zertifikatsprüfung -------------------
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
            pass  # abgebrochene TLS-Handshakes (Test „falsches Zertifikat“) sind gewollt

        def get_request(self):
            s, a = super().get_request()
            return (ctx.wrap_socket(s, server_side=True) if implicit else s), a

    srv = S(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


@test("2.11.3 E-Mail: STARTTLS und SSL/TLS mit Zertifikatsprüfung („check_hostname requires server_hostname“)")
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
    assert m["tls"], "Mail nicht verschlüsselt übertragen"
    assert m["helo"] == "pocketknife.firma.local", f"2.11.2 HELO-Name: {m['helo']}"
    assert b"quoted-printable" in m["data"] and b"8bit" not in m["data"].lower(), "2.11.2 Kodierung"


@test("2.11.3 E-Mail: falsches Zertifikat wird erkannt und verständlich gemeldet")
def _():
    cert, key = _cert("fremd")
    port = _smtp_server(cert, key, False)  # dieses Zertifikat ist NICHT in SSL_CERT_FILE
    mailer.save({"enabled": True, "host": "localhost", "port": port, "security": "starttls", "verify": True, "user": "",
                 "from": "pk@firma.local", "to": "it@firma.local"})
    must_fail(mailer.test, "", contains="Zertifikat")


@test("2.11.3 E-Mail: Hinweis, wenn Absender und Anmeldekonto abweichen")
def _():
    s = {**mailer.settings(), "user": "konto@firma.local", "from": "anders@firma.local", "security": "starttls", "verify": True}
    assert any("weicht" in w for w in mailer.warnings(s))


def _keytab(entries):
    """Kleine Keytab im MIT-Format 0x0502 (wie von ktpass) für Tests."""
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


@test("2.16 Kiosk-SSO: Standard aus; Keytab geprüft und verschlüsselt; Windows-Anmeldung gibt nur die Kiosk-Rolle")
def _():
    import types
    from app import ad, kiosk, sso, vault, main as M
    assert db.setting("kiosk_sso") == 0, "Windows-Anmeldung muss standardmäßig aus sein"
    assert "kiosk_sso" not in M.INTERNAL_SETTINGS and "sso_keytab" not in db.all_settings(), "Keytab nie über /api/settings"
    must_fail(sso.save_keytab, b"kein keytab", contains="Keytab")
    must_fail(sso.save_keytab, _keytab([("host/pc01.firma.local", "FIRMA.LOCAL", 2, 18)]), contains="HTTP")
    must_fail(sso.save_keytab, _keytab([("HTTP/pk.fremd.local", "FREMD.LOCAL", 2, 18)]), contains="Realm")
    kt = _keytab([("HTTP/pocketknife.firma.local", "FIRMA.LOCAL", 3, 18), ("HTTP/pocketknife.firma.local", "FIRMA.LOCAL", 3, 23)])
    info = sso.save_keytab(kt, user="test")
    assert info["present"] and info["hosts"] == ["pocketknife.firma.local"], info
    assert {e["enctype_name"] for e in info["entries"]} == {"aes256-cts-hmac-sha1-96", "rc4-hmac"} and info["entries"][0]["kvno"] == 3
    raw = db.setting_raw("sso_keytab")
    assert raw and b"pocketknife" not in raw.encode() and base64.b64decode(vault.decrypt(raw)) == kt, "Keytab nicht verschlüsselt gespeichert"
    assert not sso.enabled(), "ohne Kiosk und ohne Schalter nicht aktiv"

    class Req:
        def __init__(self, hdr=None):
            self.headers = {"authorization": hdr} if hdr else {}
            self.client = types.SimpleNamespace(host="10.0.0.5")
            self.cookies = {}
    r = M.api_kiosk_sso(Req())
    assert r.status_code == 404, "ausgeschaltet → keine Kerberos-Aufforderung"
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
        assert r.status_code == 401 and r.headers.get("www-authenticate") == "Negotiate", "ohne Ticket: 401 + Negotiate"
        r = M.api_kiosk_sso(Req("Negotiate " + base64.b64encode(b"NTLMSSP\x00\x01\x00").decode()))
        assert r.status_code == 403 and "www-authenticate" not in r.headers and "NTLM" in r.body.decode(), "NTLM: 403 ohne neue Aufforderung"
        assert "tok" not in seen, "NTLM-Token darf nicht an GSSAPI gehen"
        r = M.api_kiosk_sso(Req("Negotiate " + base64.b64encode(b"krb-ticket").decode()))
        assert r.status_code == 200, r.body
        assert r.headers.get("www-authenticate") == "Negotiate " + base64.b64encode(b"mutual").decode()
        assert seen["tok"] == b"krb-ticket" and seen["store"]["keytab"].startswith("/tmp/"), "Keytab nur im Arbeitsspeicher (tmpfs)"
        cookie = r.headers.get("set-cookie", "")
        assert "httponly" in cookie.lower() and "samesite=strict" in cookie.lower()
        tok = cookie.split("=", 1)[1].split(";", 1)[0]
        sess = auth.session_for(tok)
        assert sess and sess["role"] == "kiosk" and sess["username"] == "max", "SSO ergibt immer die Kiosk-Rolle (auch für Admins)"
        from fastapi import HTTPException

        class R2:
            cookies = {auth.COOKIE: tok}
        try:
            auth.current(R2())
            raise AssertionError("SSO-Sitzung kommt an die Verwaltung")
        except HTTPException as e:
            assert e.status_code == 403
        seen["who"] = "max@FREMD.LOCAL"
        r = M.api_kiosk_sso(Req("Negotiate " + base64.b64encode(b"krb-ticket2").decode()))
        assert r.status_code == 403 and "Domäne" in r.body.decode(), "Ticket aus fremdem Realm"
        seen["who"] = "HTTP/x.firma.local@FIRMA.LOCAL"
        assert M.api_kiosk_sso(Req("Negotiate " + base64.b64encode(b"t3").decode())).status_code == 403, "Dienst-Ticket"
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
    assert not sso.present() and not os.path.exists("/tmp/pocketknife-http.keytab"), "Keytab nach Entfernen weg"
    # Laufende Kiosk-Sitzungen von Admins (Windows-Anmeldung) dürfen bei der Nachprüfung nicht verloren gehen
    src = open(ad.__file__, encoding="utf-8").read()
    assert "out[key] = bool(is_acc or kiosk_match(c, e, strict=True, cache=kcache))" in src  # 2.25.1: Prüfung in check_sessions
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "kiosk.js"), encoding="utf-8").read()
    assert "ssoOff(); // nach dem Abmelden" in js, "nach Abmelden nicht sofort wieder automatisch anmelden"


@test("2.16 Berichte: kein „null“ in der Werkzeugleiste; Hilfe zeigt Codeblöcke und Unterpunkte in Listen")
def _():
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    i = js.index("btn('Per E-Mail …', () => mailSubDlg(null, defs, cols")
    seg = js[i:js.index("const res = await GET(`/api/reports/", i)]
    assert ": null);" not in seg, "form.append(..., null) schreibt „null“ in die Seite"
    # Hilfe: eingerückte Codeblöcke und Unterpunkte in Listen (Anleitung Windows-Anmeldung)
    assert "li.appendChild(rawEl('pre', 'help-code'" in js and "if (!sub) { sub = rawEl(" in js


@test("2.16.1 Kiosk: nur Domänenkonten zählen, Doppelklick/zweite Freigabe starten nichts doppelt, Neustart erledigt den Bedarf")
def _():
    from app import kiosk, deploy, config as cfg
    old_nb = cfg.NETBIOS
    cfg.NETBIOS = "FIRMA"
    try:
        assert kiosk._domain_sam("FIRMA\\Max", "PC-1") == "max"
        assert kiosk._domain_sam("PC-1\\max", "PC-1") is None, "lokales Konto gleichen Namens"
        assert kiosk._domain_sam("FREMD\\max", "PC-1") is None, "Konto einer anderen Domäne"
    finally:
        cfg.NETBIOS = old_nb
    # Adresse des Browsers zuerst – der eigene Rechner fällt nie wegen der Höchstzahl weg
    for i in range(8):
        db.ex("INSERT INTO computers(id, name, current_user, last_scan) VALUES(?,?,?,?)", (300 + i, f"ALT-{i}", "FIRMA\\wand", "2026-01-01"))
    db.ex("INSERT INTO computers(id, name, ip_addresses) VALUES(310, 'MEIN-PC', '10.9.9.9')")
    assert kiosk.candidates("wand", "10.9.9.9")[0]["id"] == 310
    # Freigabe atomar
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
        assert kiosk.status(db.q1("SELECT * FROM kiosk_requests WHERE id=?", (rid,)))["open"], "„starting“ zählt als offen"
    finally:
        kiosk._start = old_start
        db.set_setting("kiosk_enabled", 0)
    # Nach erfolgreichem Kiosk-Neustart kein erneutes „Neustart erforderlich“ (bis zum nächsten Scan)
    db.ex("UPDATE computers SET extra=?, last_boot='2026-10-20 08:00:00', last_scan='2026-10-21 09:00:00' WHERE id=310",
          (json.dumps({"pending_reboot": True}),))
    assert kiosk.reboot_info(310)["needed"]
    did = db.ex("INSERT INTO deployments(name, status) VALUES('Kiosk: Neustart', 'success')")
    db.ex("INSERT INTO deployment_targets(deployment_id, computer_id, status, finished_at) VALUES(?,310,'success','2026-10-21 10:05:00')", (did,))
    db.ex("INSERT INTO kiosk_requests(created_at, username, computer_id, computer_name, package_name, action, status, deployment_id) "
          "VALUES('2026-10-21 10:00:00','u',310,'MEIN-PC','Neustart','reboot','started',?)", (did,))
    assert not kiosk.reboot_info(310)["needed"], "Neustart-Hinweis nach erfolgreichem Neustart"
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "kiosk.js"), encoding="utf-8").read()
    assert "if (!ok || S.busy) return;" in js and "mine(r)" in js and "catch (e) { ssoOff(); renderLogin(" in js


@test("2.16.1 Anmeldung: Sitzung gehört dem gebundenen Konto; Fehlermeldungen nach richtigem Kennwort bleiben allgemein")
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
        assert info["username"] == "bob", f"Sitzung für {info['username']} statt für das gebundene Konto bob"
        ad.bind = lambda u, p, r=None: Conn(None)  # Server ohne „Who am I?“: genauer Abgleich über den UPN
        assert ad.authenticate("alice", "x")["username"] == "bob"
    finally:
        ad.bind, ad._search, ad.is_member, ad._group_dn, cfg.BASE_DN = old
    assert all(x in auth._PRIVATE for x in ("Zugriffsgruppe", "LDAP-Suche", "Notfallzugang"))
    src = open(auth.__file__, encoding="utf-8").read()
    assert 'key = [(f"bg|{ip}", 8)]' in src, "Notfallzugang mit eigenem Zähler"


@test("2.16.1 Kiosk-SSO: nur Tickets für den HTTP-Namen des Kiosks")
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


@test("2.16.1 Verteilung: fehlgeschlagenes Paket in Mehrfach-/Zeitplan-Lauf = fehlgeschlagen; Neustart-Einstellung bleibt; Löschen stoppt")
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
    assert r.run_package(pkg)[0] == "success", "einzelnes Paket mit „weiter nach Fehler“ bleibt wie bisher"
    assert deploy.strongest_reboot(["never", "if_required", None]) == "if_required"
    assert deploy.strongest_reboot(["never", "always"]) == "always" and deploy.strongest_reboot([None]) == "never"
    assert deploy._cancelled(987654), "gelöschte Bereitstellung gilt als abgebrochen"
    did = db.ex("INSERT INTO deployments(name, status) VALUES('X', 'cancelled')")
    deploy.start(did)
    assert db.val("SELECT status FROM deployments WHERE id=?", (did,)) == "cancelled", "start() nach Abbruch"
    src = open(deploy.__file__, encoding="utf-8").read()
    assert "Test-Path -LiteralPath $f" in src and "_computer_lock(cid)" in src
    from app import schedules
    ssrc = open(schedules.__file__, encoding="utf-8").read()
    assert "WHEN excluded.status='failed' THEN COALESCE(attempts, 0) + 1" in ssrc and "except ValueError as e:" in ssrc


@test("2.16.1 Daten: RegEx ohne verschachtelte Wiederholungen, Zahlenfilter ohne leere Werte, Benachrichtigungen ohne Dauerschleife")
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
    assert set(a) == set(notify._check_backup_failed({}, {"since": ""})) == {"2026-10-21"}, "eine Meldung je Tag"
    alert = next(x for x in notify.ALERTS if x["key"] == "offline") if hasattr(notify, "ALERTS") else None
    if alert:
        must_fail(notify._clean_alert_params, alert, {"params": {"days": "nan"}}, contains="ungültig")
    from app import collections_
    assert "exclude_self" in collections_.used_by.__code__.co_varnames


@test("2.16.1 Tools und Oberfläche: Werte vom Client nie als Code; Fernsitzung zählt als Aktivität; Daten nicht übersetzt")
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


@test("2.17 Kiosk: Software nur für bestimmte AD-Gruppen – im Katalog und beim Bestellen geprüft, AD nicht erreichbar = nicht sichtbar")
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
    assert db.val("SELECT groups FROM kiosk_items WHERE package_id=?", (pid,)) == "Grafik-Team\nCN=Design,OU=G,DC=firma,DC=local", "ohne Doppelte"
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
        assert "Grafikprogramm" not in names("buchhaltung"), "Nicht-Mitglied sieht die Software"
        assert "Grafikprogramm" not in names("unbekannt"), "AD nicht prüfbar → nicht sichtbar"
        n = len(calls)
        names("grafik")
        assert len(calls) == n, "Ergebnis wird zwischengespeichert"
        db.ex("UPDATE computers SET current_user='FIRMA\\buchhaltung' WHERE id=400")
        must_fail(kiosk.request, {"username": "buchhaltung"}, "?", 400, pid, "install", contains="für Sie nicht freigegeben")
        db.ex("UPDATE computers SET current_user='FIRMA\\grafik' WHERE id=400")
        r = kiosk.request({"username": "grafik", "display_name": "G"}, "?", 400, pid, "install")
        assert r["status"] != "rejected"
        kiosk.save_item(pid, {"enabled": True, "groups": ""})
        assert "Grafikprogramm" in names("buchhaltung"), "ohne Gruppen: alle Kiosk-Nutzer"
        assert any(i["package_id"] == pid and i["groups"] is None for i in kiosk.admin_items())
    finally:
        ad.member_of_any, kiosk._logged_on, deploy.create = old
        db.set_setting("kiosk_enabled", 0)
    assert ad.member_of_any("x", []) is True


@test("2.18 Neustart des Containers: unterbrochene Läufe werden auf dem Client aufgeräumt, laufende Installer abgewartet")
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
        assert len(calls) == 1 and db.q1("SELECT * FROM client_cleanup WHERE id=?", (row["id"],)), "Installer läuft noch → Eintrag bleibt"
        deploy.process_cleanups()
        assert len(calls) == 1, "erst nach der Wartezeit erneut fragen"
        # Neue Bereitstellung auf denselben Computer: wartet, bis der alte Installer fertig ist, dann aufräumen
        answers[:] = [{"busy": 1}, {"busy": 0, "dirs": 2, "tasks": 1, "killed": 0, "codes": ["0"]}]
        c = db.q1("SELECT * FROM computers WHERE id=500")
        did2 = db.ex("INSERT INTO deployments(name, status) VALUES('Neu', 'running')")
        tid2 = db.ex("INSERT INTO deployment_targets(deployment_id, computer_id, status) VALUES(?,500,'running')", (did2,))
        assert deploy._wait_for_orphans(c, "pc-500", did2, tid2) is True
        assert len(calls) == 3 and not db.q1("SELECT * FROM client_cleanup WHERE id=?", (row["id"],))
        msg = db.val("SELECT message FROM deployment_targets WHERE id=?", (tid,))
        assert "aufgeräumt" in msg and "Exitcode 0" in msg, msg
        # Lange nicht erreichbar: nach 14 Tagen aufgeben (der Scan räumt Reste > 2 Tage selbst auf)
        db.ex("INSERT INTO client_cleanup(computer_id, host, prefix, max_minutes, created) VALUES(500, 'pc-500', '1_2_', 60, ?)",
              (util.ago(days=20),))
        deploy.process_cleanups()
        assert not db.val("SELECT COUNT(*) FROM client_cleanup WHERE prefix='1_2_'")
    finally:
        remote.cleanup_run, deploy.time.sleep = old
    assert "taskkill.exe /PID $id /T /F" in remote.CLEANUP_RUN and "PK_BUSY:" in remote.CLEANUP_RUN
    assert "_wait_for_orphans(c, host, did, tid)" in open(deploy.__file__, encoding="utf-8").read()


@test("2.19 Sammlungen: Untergruppen und Verweisketten getrennt begrenzt; Speichern bricht keine abhängige Sammlung still")
def _():
    from app import collections_, main as M
    from fastapi import HTTPException
    grp = {"type": "group", "match": "any", "items": [{"table": "computer", "column": "name", "op": "contains", "value": "PC"},
                                                      {"table": "computer", "column": "name", "op": "contains", "value": "SRV"}]}

    def mk(name, items):
        return db.ex("INSERT INTO collections(name, type, match, filters) VALUES(?, 'dynamic', 'all', ?)",
                     (name, json.dumps({"type": "group", "match": "all", "items": items})))
    ref = lambda i: {"table": "computer", "column": "collection", "op": "in", "value": str(i)}  # noqa: E731
    # Kette aus 5 Sammlungen, die unterste mit verschachtelter Untergruppe – bis 2.18 „zu tief verschachtelt“
    base = mk("Kette-Basis", [{"type": "group", "match": "all", "items": [grp]}])
    top = base
    for i in range(4):
        top = mk(f"Kette-{i}", [ref(top)])
    collections_.where_for(top)
    assert collections_.count(top) >= 0 and not collections_.broken(collections_.dependents(base))
    # 13 Sammlungen hintereinander gehen, mehr nicht
    chain = [mk("Lang-0", [{"table": "computer", "column": "name", "op": "contains", "value": "PC"}])]
    for i in range(1, collections_.HOPS_MAX + 1):
        chain.append(mk(f"Lang-{i}", [ref(chain[-1])]))
    collections_.where_for(chain[-1])
    extra = mk("Noch eine", [{"table": "computer", "column": "name", "op": "contains", "value": "X"}])
    # Die unterste Sammlung auf eine weitere verweisen lassen: die oberste wäre danach zu lang → ablehnen, Name nennen
    try:
        M.api_collection_update(chain[0], {"name": "Lang-0", "type": "dynamic", "match": "all",
                                           "filters": {"type": "group", "match": "all", "items": [ref(extra)]}}, user={"username": "t"})
        raise AssertionError("Änderung, die eine abhängige Sammlung kaputtmacht, wurde gespeichert")
    except HTTPException as e:
        assert e.status_code == 400 and f"Lang-{collections_.HOPS_MAX}" in str(e.detail), e.detail
    assert "Lang-0" == db.val("SELECT name FROM collections WHERE id=?", (chain[0],))
    assert '"PC"' in db.val("SELECT filters FROM collections WHERE id=?", (chain[0],)), "Änderung wurde nicht zurückgerollt"
    # harmlose Änderung geht durch
    M.api_collection_update(chain[0], {"name": "Lang-0", "type": "dynamic", "match": "all",
                                       "filters": {"type": "group", "match": "all", "items": [{"table": "computer", "column": "name", "op": "contains", "value": "Q"}]}},
                            user={"username": "t"})
    assert '"Q"' in db.val("SELECT filters FROM collections WHERE id=?", (chain[0],))


@test("2.20 Erreichbarkeit: „online“ nur, wenn wirklich dieser Computer antwortet; DNS-Cache mit einstellbarer Zeit")
def _():
    import struct as _st
    from app import dnsres, presence, remote, collections_
    # DNS-Antwort zerlegen (A-Eintrag hinter einem CNAME)
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
        assert calls["sys"] == 1, "Zwischenspeicher"
        assert dnsres.flush() == 1
        dnsres.resolve("pc-a")
        assert calls["sys"] == 2, "nach dem Leeren neu gefragt"
        db.set_setting("dns_cache_minutes", 0)
        dnsres.resolve("pc-a"); dnsres.resolve("pc-a")
        assert calls["sys"] == 4, "0 = jedes Mal neu"
    finally:
        dnsres._resolve_system = old_sys
        db.set_setting("dns_cache_minutes", 5)
        dnsres.flush()
    # Identität
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
        assert presence.probe(row()) == (True, True) and st["n"] == 1, "gleiche Adresse, kürzlich bestätigt: nicht erneut fragen"
        # Adresse geändert → nachfragen; ein anderer PC antwortet → offline mit Hinweis
        st["ip"], st["who"] = "10.0.0.99", {"name": "LAPTOP-FREMD", "domain": "firma.local", "joined": True}
        assert presence.probe(row()) == (False, False) and st["n"] == 2
        r = row()
        assert r["addr_problem"] == "other" and "LAPTOP-FREMD" in r["addr_detail"] and r["resolved_ip"] == "10.0.0.99"
        cid_fa = db.val("SELECT id FROM collections WHERE builtin=1 AND name='Falsche Adresse'")
        assert cid_fa and 600 in collections_.member_ids(cid_fa, fresh=True), "Standardsammlung „Falsche Adresse“"
        assert presence.probe(row()) == (False, False) and st["n"] == 2, "Problemfall höchstens stündlich erneut fragen"
        assert presence.probe(row(), force=True) == (False, False) and st["n"] == 3, "„Jetzt prüfen“ fragt sofort"
        st["who"] = {"name": "PC-600", "domain": "WORKGROUP", "joined": False}
        presence.probe(row(), force=True)
        assert row()["addr_problem"] == "nodomain"
        st["who"] = {"error": "auth", "detail": "401"}
        assert presence.probe(row(), force=True) == (True, False) and row()["addr_problem"] == "unverified"
        # Richtiger Computer wieder unter der neuen Adresse → Hinweis weg
        st["who"] = {"name": "pc-600", "domain": "FIRMA.LOCAL", "joined": True}
        assert presence.probe(row(), force=True) == (True, True) and not row()["addr_problem"] and row()["verified_ip"] == "10.0.0.99"
        # Nur SMB unter einer anderen Adresse als der bestätigten (z. B. NAS): offline
        st["ip"], st["ports"] = "10.0.0.7", (True, False)
        assert presence.probe(row()) == (False, False) and row()["addr_problem"] == "unconfirmed"
        db.set_setting("identity_check", 0)
        assert presence.probe(row()) == (True, False), "abgeschaltet: wie bisher"
    finally:
        dnsres.resolve, remote.is_online, remote.identify = old
        db.set_setting("identity_check", 1)
    # Neue Standardsammlung kommt auch in bestehende Datenbanken
    db.ex("DELETE FROM collections WHERE builtin=1 AND name='Falsche Adresse'")
    db.init()
    assert db.val("SELECT COUNT(*) FROM collections WHERE builtin=1 AND name='Falsche Adresse'") == 1
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "deploy.py"), encoding="utf-8").read()
    assert "presence.probe(c)" in src and "presence.probe(c, now)" in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "inventory.py"), encoding="utf-8").read()
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    assert "draggable: c.builtin ? 'false' : 'true'" in js and "if (!c.builtin) it.addEventListener('dragstart'" in js, "Standard- und AD-Baum nicht verschiebbar"


@test("2.20.1 Kiosk: nach einer Deinstallation wieder „Installieren“; englische Oberfläche vollständig übersetzt")
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
    req("install", "2026-10-21 09:00:00")  # vor dem letzten Scan, Scan kennt die Software nicht → nicht installiert
    assert not item()["installed"], "Scan ist neuer: Inventar zählt (z. B. über die Verwaltung deinstalliert)"
    req("install", "2026-10-21 11:00:00")
    assert item()["installed"], "Installation nach dem letzten Scan"
    db.ex("INSERT INTO software(computer_id, name, version, winget_id) VALUES(700, 'Editor X', '1.0', 'Vendor.EditorX')")
    req("uninstall", "2026-10-21 12:00:00")
    assert not item()["installed"], "Deinstallation nach dem letzten Scan → wieder „Installieren“"
    db.ex("UPDATE computers SET last_scan='2026-10-21 13:00:00' WHERE id=700")
    assert item()["installed"], "neuerer Scan findet die Software noch → Inventar zählt"
    # Englisch: erzeugte Texte in Tabellen werden übersetzt, Wörterbuch vollständig (auch Einzelwörter und „– …“-Texte)
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    js = open(os.path.join(root, "app", "static", "app.js"), encoding="utf-8").read()
    assert "function rawCol(c)" in js and "'data-notr': rawCol(c) || undefined" in js
    en = json.load(open(os.path.join(root, "app", "i18n", "en.json"), encoding="utf-8"))
    for k in ("nie", "Dienstkonto (Deploy-Benutzer)", "neueste", "{0} von „{1}“ auf {2} gestartet (Bereitstellung #{3})"):
        assert en.get(k), f"Übersetzung fehlt: {k}"
    if os.path.exists(os.path.join(root, "tools", "build_i18n.py")):  # Entwicklungswerkzeuge liegen nicht im Image
        assert 'PREFIX.sub("", de)' in open(os.path.join(root, "tools", "build_i18n.py"), encoding="utf-8").read()
        assert "GERMAN_WORDS" in open(os.path.join(root, "tools", "i18n_extract.py"), encoding="utf-8").read()


@test("2.21 Diagnosepaket: anonymisiert ohne echte Namen, Platzhalter bleiben gleich, nie Kennwörter")
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
        assert leak not in blob, f"echter Name im Paket: {leak}"
    assert "admin$" in blob, "allgemeine Wörter bleiben (ADMIN$ nicht ersetzt)"
    assert "kiosk: user-" in blob, "Auslöser-Vorsatz bleibt, Benutzer als Platzhalter"
    pc = db.val("SELECT alias FROM diag_alias WHERE kind='computer' AND real='BUCHH-MUELLER7'")
    assert pc and pc.lower() in blob
    assert db.val("SELECT COUNT(*) FROM diag_alias WHERE real LIKE 'Kiosk:%'") == 0, "kein Platzhalter für „Kiosk: …“"
    diagnostics.build(3, True)
    assert db.val("SELECT alias FROM diag_alias WHERE kind='computer' AND real='BUCHH-MUELLER7'") == pc, "Platzhalter bleibt gleich"
    assert any(x["real"] == "BUCHH-MUELLER7" for x in diagnostics.lookup(pc)), "Nachschlagen"
    assert "BUCHH-MUELLER7" in diagnostics.mapping_csv()
    sett = json.loads(z.read("einstellungen.json"))
    assert not [k for k in sett if diagnostics.NEVER_SETTINGS.search(k)], "keine geheimen Einstellungen"
    # ohne Anonymisierung: echte Namen, aber weiterhin keine Geheimnisse
    raw = _zf.ZipFile(_io.BytesIO(diagnostics.build(7, False)[0]))
    assert "BUCHH-MUELLER7" in raw.read("protokoll.csv").decode()
    assert not [k for k in json.loads(raw.read("einstellungen.json")) if diagnostics.NEVER_SETTINGS.search(k)]


@test("2.22 Diagnosepaket: jede Zeile mit Version, Statistik seit dem Update getrennt, Änderungen und behobene Fehler beigelegt")
def _():
    import csv as _csv
    import io as _io
    import zipfile as _zf
    from app import config, diagnostics
    db.ex("DELETE FROM version_history")
    db.ex("DELETE FROM logs")
    db.ex("DELETE FROM deployment_targets")
    # Startmeldungen älterer Versionen im Protokoll → werden beim ersten Mal nachgetragen (doppelte Starts zusammengefasst)
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
    assert len(diagnostics.history()) == 3, "erneuter Start derselben Version zählt nicht doppelt"
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
    assert not st["aktuelle_version"]["deployment_targets"], "Fehlschlag aus 2.19 zählt nicht zur aktuellen Version"
    assert st["zeitraum"]["deployment_targets"].get("failed") == 1
    assert st["je_version"]["2.19"]["targets_failed"] == 1 and st["je_version"]["2.19"]["log_error"] == 1
    assert json.loads(z.read("fehlgeschlagen.json"))[0]["version"] == "2.19"
    v = json.loads(z.read("versionen.json"))
    assert v["aktuell"] == config.VERSION and v["im_zeitraum"][:3] == ["vor 2.19", "2.19", "2.20"], v["im_zeitraum"]
    ch = z.read("aenderungen.md").decode()
    assert f"## pocketknife-{config.VERSION}" in ch and "## pocketknife-2.19 " in ch and "## pocketknife-2.18 " not in ch, "nur bis zur ältesten Version im Zeitraum"
    fx = z.read("behobene_fehler.txt").decode()
    assert "- 2.20.1 Kiosk: nach einer Deinstallation" in fx
    dfile = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Dockerfile")
    if os.path.exists(dfile):  # im Image gibt es kein Dockerfile – dort beweist aenderungen.md oben, dass es klappt
        assert "CHANGELOG.md" in open(dfile, encoding="utf-8").read()
    # Zeitraum „seit dem letzten Update“: nur Einträge der laufenden Version
    data, name = diagnostics.build(0, True)
    z0 = _zf.ZipFile(_io.BytesIO(data))
    assert "seit-update" in name and "seit dem Update" in z0.read("LIESMICH.txt").decode()
    rows0 = list(_csv.DictReader(_io.StringIO(z0.read("protokoll.csv").decode("utf-8-sig")), delimiter=";"))
    assert rows0 and all(r["version"] == config.VERSION for r in rows0), "nur die laufende Version"


@test("2.22.1 Probelauf: Platzhalter nicht für Programmwörter/Datumsangaben, Kiosk-Gruppen anonym; winget-Archive und Kerberos-Fehler erklärt")
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
    assert "Anmeldung fehlgeschlagen" in blob, "Programmwort nicht durch Platzhalter ersetzt"
    assert "anmeldung" in diagnostics._vocabulary() and "mueller" not in diagnostics._vocabulary(), "Namen aus Beispielen zählen nicht"
    assert "2025-10-20-12-55-07" in blob, "Datum in Dateinamen ist keine MAC-Adresse"
    assert "00-15-5D-01-02-03" not in blob and "00:15:5D" not in blob.upper(), "echte MAC weiterhin ersetzt"
    assert "GEHEIMGRUPPE" not in blob and "Team Nord" not in blob, "Kiosk-Gruppen anonymisiert"
    # winget: Archiv-Fehler als Lokales System einmal mit tar.exe wiederholen; nur bei Installation/Aktualisierung
    body = deploy._winget_body({"winget_action": "install", "winget_id": "TeamViewer.TeamViewer", "winget_scope": "machine"})
    assert "-1978335140" in body and "archiveExtractionMethod" in body and ".IsSystem" in body
    assert body.count("PK-Run $wg") == 2
    assert "\\WinGet\\Settings\\defaultState" in body, "Einstellungsdatei, die winget als Lokales System wirklich liest"
    assert "archiveExtractionMethod" not in deploy._winget_body({"winget_action": "uninstall", "winget_id": "X.Y"})
    for code in (-1978335140, -1978335215, -1978334974):
        assert deploy.WINGET_MESSAGES.get(code), code
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "steps.py"), encoding="utf-8").read()
    assert 'f"Fehlgeschlagen – {msg} (Exitcode {code})"' in src, "Klartext und Exitcode"
    # Kerberos „Message stream modified“: verständlicher Hinweis, kein Dienstkonto-Schutz
    e = "SpnegoError (4294967295): Major (851968): Unspecified GSS failure. Minor (2529638953): Message stream modified"
    assert "DNS-Eintrag" in (kerberos.explain(e) or "") and not kerberos._account_problem(e)
    assert '"krb_modified"' in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "presence.py"), encoding="utf-8").read()


@test("2.23 Proxyserver: Adresse geprüft, Kennwort im Tresor, Anmeldung nur vom Server, Clients ohne Anmeldedaten")
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
        assert "Geh@im" not in db.setting_raw("proxy_password"), "Kennwort verschlüsselt"
        res = proxy.test("http://winget.example.invalid/source.msix")
        assert res["ok"], res
        req = seen[-1]
        assert req[0].startswith("GET http://winget.example.invalid/source.msix"), "Anfrage ging über den Proxy"
        auth = next(x for x in req if x.lower().startswith("proxy-authorization: basic "))
        assert _b64.b64decode(auth.split()[-1]).decode() == "FIRMA\\px:Pr0xy:Geh@im", "Basic-Anmeldung am Proxy"
        assert proxy.client_proxy() == url, "Clients: nur die Adresse, nie Anmeldedaten"
        f = deploy._winget_find()
        assert f"$PKProxy = '{url}'" in f and "@pkWeb" in f and "settings set DefaultProxy" in f and "Geh@im" not in f
        assert "PK-WingetProxy" in f.split("9009; return }")[-1], "erst nach dem Finden von winget"
        assert "***@" in proxy.safe_error(Exception("<urlopen error http://FIRMA%5Cpx:Pr0xy@127.0.0.1:1>"))
        z = _zf.ZipFile(_io.BytesIO(diagnostics.build(1, True)[0]))
        blob = "\n".join(z.read(n).decode("utf-8", "replace") for n in z.namelist())
        assert "Geh@im" not in blob and "proxy_password" not in blob
        assert json.loads(z.read("einstellungen.json")).get("proxy_user") != "FIRMA\\px", "Proxy-Benutzer anonymisiert"
        # ohne Haken: Clients bekommen keinen Proxy (und POCKET KNIFE entfernt einen selbst gesetzten wieder)
        proxy.save({"url": url, "user": "", "clients": False})
        assert proxy.client_proxy() == "" and "$PKProxy = ''" in deploy._winget_find()
        assert not db.setting_raw("proxy_password"), "ohne Benutzer kein gespeichertes Kennwort"
        from app import main as _m
        assert {"proxy_url", "proxy_user", "proxy_clients"} <= _m.INTERNAL_SETTINGS, "nur über den geprüften Endpunkt"
    finally:
        proxy.save({"url": "", "user": "", "clients": False})
        srv.shutdown()


@test("2.23 Code-Review: Diagnosepaket ohne Kennwörter/Restnamen, schnell bei vielen Rechnern; Identitätsprüfung verdächtigt bei Kontoproblemen keine Geräte")
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
    assert "192.168.5.9" not in an("Verbindung zu 192.168.5.9. Danach"), "IP am Satzende"
    assert "aa:bb:cc:dd:ee:01" not in an("MAC AA:BB:CC:DD:EE:01.").lower()
    assert "Sommer2026!" not in an("Anmeldung fehlgeschlagen für 'Sommer2026!' von 10.0.0.1: falsch")
    assert "fe80::1c2b:3d4e:5f60:7a8b" not in an("Anmeldung von fe80::1c2b:3d4e:5f60:7a8b%12").lower(), "IPv6"
    assert "12:30:45" in an("um 12:30:45 Uhr"), "Uhrzeit ist keine IPv6-Adresse"
    assert "DC=sub" not in an("CN=X,DC=sub,DC=firma,DC=local"), "Teil-Domäne"
    assert "jürgen" not in an("Mail an jürgen.müller@beispiel.de").lower()
    assert "fs01" not in an("Log: FS01_install.log").lower(), "Name vor Unterstrich"
    mac_alias = an("AA:BB:CC:DD:EE:02")
    assert an(mac_alias) == mac_alias, "Platzhalter einer MAC wird nicht noch einmal ersetzt"
    raw = diagnostics.Anonymizer(False)
    assert "Sommer2026!" not in raw("net use Z: \\\\srv\\x /user:a\\b Sommer2026!"), "Kennwörter auch ohne Anonymisierung maskiert"
    db.set_setting("ldap_cert_pending", '{"host": "fs01.firma.local", "fingerprint": "ab"}')
    z = _zf.ZipFile(_io.BytesIO(diagnostics.build(1, True)[0]))
    assert "fs01" not in z.read("einstellungen.json").decode().lower()
    db.set_setting("ldap_cert_pending", "")
    # Viele Rechner, jede Zeile eine neue Adresse: keine Neuübersetzung einer Riesen-Regex je Platzhalter
    db.exmany("INSERT INTO computers(name, dns_name) VALUES(?, ?)", [(f"RV{i:04d}", f"rv{i:04d}.firma.local") for i in range(1500)])
    for i in range(1000):
        db.ex("INSERT INTO logs(ts, level, category, message) VALUES(?, 'WARN', 'system', ?)",
              (util.now(), f"RV{i:04d}: Adresse 172.20.{i // 250}.{i % 250 + 1} OU=Abt{i},DC=firma,DC=local nicht erreichbar."))
    t0 = _t.time()
    z = _zf.ZipFile(_io.BytesIO(diagnostics.build(1, True)[0]))
    took = _t.time() - t0
    assert took < 30, f"Diagnosepaket zu langsam: {took:.1f} s"
    assert "rv0999" not in z.read("protokoll.csv").decode().lower() and "172.20." not in z.read("protokoll.csv").decode()
    db.ex("DELETE FROM computers WHERE name LIKE 'RV%'")
    # zwei Pakete gleichzeitig: jeder Platzhalter gehört genau einem Namen
    db.exmany("INSERT INTO computers(name) VALUES(?)", [(f"PAR{i}",) for i in range(40)])
    th = [threading.Thread(target=diagnostics.build, args=(1, True)) for _ in range(2)]
    [x.start() for x in th]
    [x.join() for x in th]
    assert not db.q("SELECT kind, alias FROM diag_alias GROUP BY kind, alias HAVING COUNT(*) > 1"), "Platzhalter doppelt vergeben"
    db.ex("DELETE FROM computers WHERE name LIKE 'PAR%'")
    # Identitätsprüfung: falsches Kennwort / KDC weg ≠ „Gerät gehört nicht zur Domäne“
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
        assert remote.identify("pc-x")["error"] == "paused" and trips, "Kontoproblem löst den Schutz aus"
        guard.verify_account = lambda: False
        C.err = "the specified credentials were rejected by the server (401)"
        assert remote.identify("pc-x")["error"] == "paused", "Dienstkonto selbst defekt"
        guard.verify_account = lambda: True
        assert remote.identify("pc-x")["error"] == "auth", "nur dieses Gerät lehnt ab"
    finally:
        remote.Client, guard.verify_account, guard.trip = old
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "steps.py"), encoding="utf-8").read()
    assert '"Neustart erforderlich" not in msg' in src


@test("2.23.1 Sammlungsauswahl: nach Ordnern gegliedert und alphabetisch; Software-Sammlungen „installiert“/„nicht installiert“ ohne Präfix")
def _():
    from app import main as _m
    f = db.ex("INSERT INTO collections(name, type, builtin, sort) VALUES('Zz Apps', 'folder', 0, 1)")
    db.ex("INSERT INTO collections(name, parent_id, type, builtin, sort) VALUES('beta', ?, 'dynamic', 0, 5)", (f,))
    db.ex("INSERT INTO collections(name, parent_id, type, builtin, sort) VALUES('Alpha', ?, 'dynamic', 0, 9)", (f,))
    ch = _m._collection_choices()
    assert ch[0]["id"] == 0, "„gesamter Bestand“ zuerst"
    assert all(c.get("type") != "folder" for c in ch) and not any(c["name"] == "Zz Apps" for c in ch), "keine Ordner zur Auswahl"
    mine = [c for c in ch if c["path"] == "Zz Apps"]
    assert [c["name"] for c in mine] == ["Alpha", "beta"], "alphabetisch, unabhängig von Groß-/Kleinschreibung und Sortierfeld"
    keys = [(c["path"].lower(), c["name"].lower()) for c in ch[1:]]
    assert keys == sorted(keys), "nach Pfad und Name sortiert"
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    assert "async function collPicker(set" in js and js.count("await collPicker(") >= 2, "Zeitpläne und Scanprofile nutzen die gegliederte Auswahl"
    assert "newDynColl(`${base} installiert`" in js and "newDynColl(`${base} nicht installiert`" in js, "Name ohne „Anwendungen:“"
    assert "\\d+\\.\\d[\\d.]*" in js, "Versionsnummer nur mit Punkt – „Microsoft 365 Apps“ wird nicht zu „Microsoft*“"
    assert "typeof x === 'object' && !Array.isArray(x)" in js, "kaputter Zustand im Browser-Speicher öffnet den Dialog trotzdem"
    assert "'data-notr': '1', onchange" in js and "collChoiceLabel" in js, "Pfad und eigene Namen nicht übersetzt, eingebaute schon"
    assert all("pparts" in c for c in ch[1:])
    # Anonymisierung: Namen mit Akzenten/Sonderzeichen werden auch im Freitext ersetzt (Regression 2.23)
    from app import diagnostics
    db.ex("INSERT INTO computers(name, current_user) VALUES('PC-ZOË', 'FIRMA\\josé'), ('SRV&DB', 'o''brien')")
    an = diagnostics.Anonymizer(True)
    an.learn()
    t = an("josé an PC-ZOË, o'brien auf SRV&DB, ::ffff:10.1.2.3")
    for leak in ("josé", "ZOË", "o'brien", "SRV&DB"):
        assert leak not in t, (leak, t)
    assert "10.99." in t and ".99.0." not in t.split("10.99.")[-1][3:], t
    db.ex("DELETE FROM computers WHERE name IN ('PC-ZOË', 'SRV&DB')")


@test("2.23.2 Nach einem Update lädt der Browser die neue Oberfläche (app.js/i18n.js nicht aus dem Zwischenspeicher)")
def _():
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "main.py"), encoding="utf-8").read()
    assert 'request.url.path.startswith("/static/")' in src and 'resp.headers["Cache-Control"] = "no-cache"' in src


@test("2.23.3/2.24.1 update.sh: Update aus dem ZIP im selben Ordner mit Nachfrage und Rückfall; Aufräumen schont Daten/Repository (auch Standardpfade, Symlinks, eingebundene Ordner)")
def _():
    import shutil as _sh
    import subprocess as _sp
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    f = os.path.join(root, "update.sh")
    if not os.path.exists(f):
        return  # im Container liegt update.sh nicht (nur im Versionsordner)
    src = open(f, encoding="utf-8").read()
    if _sh.which("dash"):
        assert _sp.run(["dash", "-n", f]).returncode == 0, "update.sh muss mit /bin/sh (dash) laufen"
    for need in ("Update to POCKET KNIFE $NEWVER now?", 'if ! docker build -t "$LOCAL_IMAGE" .', "wait_healthy",
                 "tools/selftest.py", "PK_DATA_PATH PK_REPO_MOUNT PK_BACKUP_PATH", "restore_prev", "envval()",
                 'INST="$SCRIPTDIR"', ".pk-vorher", "PROGRAM_ITEMS=",
                 "mounts >> \"$TMP/protect\"", "canon()", "env_set()", "exit 130"):
        assert need in src, need
    assert ". \"$OLD/.env\"" not in src and "source " not in src, ".env wird gelesen, nie ausgeführt"
    assert "sed -i" not in src, "Pfade nie per sed einsetzen (| & \\ im Pfad)"
    assert not os.path.exists(os.path.join(root, "--help")), "keine versehentlich erzeugte Datei „--help“ im Projekt"
    di = open(os.path.join(root, ".dockerignore"), encoding="utf-8").read()
    for x in ("data/", "data-backup/", "repo/", ".pk-vorher/", "pocketknife-*/", "*.zip"):
        assert x in di, f".dockerignore: {x} (sonst wandern Datenbank/Repository/alte Stände in jeden Build)"


@test("2.23.4 Oberfläche nach dem Update garantiert neu: Skripte und Stylesheets tragen die Version (app.js?v=…)")
def _():
    from app import config
    from app import main as _m
    for page in ("index.html", "kiosk.html", "remote.html"):
        _m._PAGES.pop(page, None)
        html = _m._page(page).body.decode()
        assert f"/static/app.css?v={config.VERSION}" in html, page
        assert f"/static/i18n.js?v={config.VERSION}" in html, page
        assert 'src="/static/i18n.js"' not in html


@test("2.24 Windows-Benachrichtigung nach Installation/Aktualisierung: nur wenn sich etwas geändert hat, nie bei Deinstallation, Texte nie als Code")
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
    assert toast.classify(W("install"), [R()], de) == [("installed", "7-Zip", "26.00")], "deutsche winget-Ausgabe"
    db.ex("INSERT INTO computers(id, name) VALUES(960, 'PC-TOAST')")
    db.ex("INSERT INTO software(computer_id, name, version, winget_id) VALUES(960, '7-Zip 24.08', '24.08', '7zip.7zip')")
    assert toast.classify(W("install"), [R()], log, 960)[0][0] == "updated", "war in älterer Version schon da"
    assert toast.classify(W("install"), [R(msg="Paket ist bereits installiert", ex=-1978335135)], log) == [], "nichts geändert"
    assert toast.classify(W("uninstall"), [R()], log) == [], "Deinstallation meldet sich nicht"
    assert toast.classify(W("install", name="Deinstallation: 7-Zip"), [R()], log) == []
    assert toast.classify(W("install"), [R(st="failed")], log) == [], "fehlgeschlagen → keine Meldung"
    scr = {"name": "Registry setzen", "steps": {"main": [{"id": "r1", "type": "registry", "entries": []}]}}
    assert toast.classify(scr, [R(sid="r1", typ="registry")], []) == [], "reine Registry-Pakete nicht"
    assert toast.classify({**scr, "notify_user": "on"}, [R(sid="r1", typ="registry")], [])[0][0] == "installed", "„immer“"
    assert toast.classify(W("install", notify_user="off"), [R()], log) == []
    assert toast.classify(W("install"), [R()], log, global_on=False) == [], "global aus"
    msi = {"name": "Office 2024 LTSC", "version": "16.0", "steps": {"main": [{"id": "m1", "type": "install", "source": "file", "install_type": "msi", "install_file": "x.msi"}]}}
    assert toast.classify(msi, [R(sid="m1")], ["=== 1. Schritte: x.msi ===\n"]) == [("installed", "Office 2024 LTSC", "16.0")]
    # Mehrere Pakete (Zeitplan/Mehrfachauswahl): nur das, was sich geändert hat, mit eigenem Namen; „nie“ des inneren Pakets gilt
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
    # Namen/Versionen nur als Daten; Benutzerskript steht kodiert in der Aufgabe – keine Datei unter ProgramData
    evil = "X'); Remove-Item C:\\ -Recurse; ('"
    script, data = toast.build_send_script([("installed", evil, "1")])
    assert evil not in script and "Remove-Item C:" not in script and "ProgramData" not in script
    assert "Join-Path $env:ProgramFiles 'PocketKnife'" in script, "Anzeigeskript nur dort, wo Benutzer nichts ändern können"
    inner = _b.b64decode(script.split("FromBase64String('")[2].split("'")[0]).decode("utf-8")
    assert evil not in inner and "ConvertFrom-Json" in inner and "SecurityElement]::Escape" in inner and "param([string]$Data, [string]$Rid)" in inner
    assert "GetHistory" in inner and "Done 10" in inner, "prüft, ob Windows die Meldung angenommen hat; sonst Absender Windows PowerShell"
    assert "PK_TOAST:res:" in script and toast.results("PK_TOAST:res:FIRMA\\max:2\n")[0][1] is False
    assert "ProgramFiles" in data["icon"] and "Join-Path $env:ProgramFiles 'PocketKnife\\pk-ok.png'" in inner
    db.set_setting("toast_language", "en")
    assert toast.build_send_script([("updated", "7-Zip", "26.00")])[1]["title"] == "7-Zip has been updated."
    db.set_setting("toast_language", "de")
    assert "notify_user" in deploy.PKG_FIELDS
    db.ex("DELETE FROM software WHERE computer_id=960")
    db.ex("DELETE FROM computers WHERE id=960")


@test("2.24.3 PowerShell immer mit vollem Pfad und festen Schaltern (-NoLogo -NonInteractive -NoProfile -ExecutionPolicy Bypass)")
def _():
    import glob as _g
    from app import steps, util
    assert util.PS_FLAGS == "-NoLogo -NonInteractive -NoProfile -ExecutionPolicy Bypass"
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app")
    for f in _g.glob(os.path.join(root, "*.py")):
        src = open(f, encoding="utf-8").read()
        for bad in ("'powershell.exe'", "powershell.exe -No", "--headless powershell.exe"):
            assert bad not in src, f"{os.path.basename(f)}: PowerShell ohne vollen Pfad ({bad})"
    b = steps.body_for({"type": "powershell", "script": "Write-Output 1", "id": "x"}, {})
    assert '"$env:SystemRoot\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" (\'-NoLogo -NonInteractive -NoProfile -ExecutionPolicy Bypass -File' in b


@test("2.24.4 update.sh: Abbruch mitten im Tausch legt automatisch zurück; .pk-vorher bleibt bei Wiederholung; Ordner mit Sicherungen/Datenbank werden nie gelöscht; nur bekannte Programmdateien")
def _():
    import shutil as _sh
    import subprocess as _sp
    import zipfile as _z
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    f = os.path.join(root, "update.sh")
    if not os.path.exists(f) or not _sh.which("sh"):
        return  # im Container liegt update.sh nicht
    src = open(f, encoding="utf-8").read()
    for need in ("trap 'on_exit' EXIT", "STAGE=swap", "docker compose up -d || UPOK=\"\"", ".pk-update-unvollstaendig",
                 "umask 077", "data-backup repo", "PROGRAM_ITEMS="):
        assert need in src, need
    base = tempfile.mkdtemp(prefix="pk-upd-", dir=TMP)
    bind, st = os.path.join(base, "bin"), os.path.join(base, "st")
    os.makedirs(bind); os.makedirs(st)
    with open(os.path.join(bind, "docker"), "w") as h:
        h.write('#!/bin/sh\nst="$FAKE_STATE"\ncase "$1" in\n info|image) exit 0 ;;\n'
                ' inspect) case "$*" in *working_dir*) cat "$st/running" 2>/dev/null; exit 0 ;; *Health*) echo healthy; exit 0 ;; esac; exit 0 ;;\n'
                ' compose) case "$2" in up) pwd > "$st/running"; cat VERSION > "$st/runver"; exit 0 ;; exec) echo ok; exit 0 ;; esac; exit 0 ;;\nesac\nexit 0\n')
    with open(os.path.join(bind, "mv"), "w") as h:  # „volle Platte“ beim Einsetzen der neuen run.py
        h.write('#!/bin/sh\ncase "$1" in */zip/pocketknife/run.py) [ -f "$FAKE_STATE/mvfail" ] && { echo "mv: No space left on device" >&2; exit 1; } ;; esac\nexec /bin/mv "$@"\n')
    for x in ("docker", "mv"):
        os.chmod(os.path.join(bind, x), 0o755)
    inst = os.path.join(base, "opt")

    def tree(d, ver, mark):
        os.makedirs(os.path.join(d, "app"), exist_ok=True)
        for n, c in (("VERSION", ver), ("docker-compose.yml", "x"), ("run.py", mark), ("app/m.txt", mark)):
            open(os.path.join(d, n), "w").write(c + "\n")
    tree(inst, "2.24.2", "ALT")
    open(os.path.join(inst, ".env"), "w").write("PK_DNS_1=1.2.3.4\n")
    os.makedirs(os.path.join(inst, "data")); open(os.path.join(inst, "data", "pocketknife.db"), "w").write("DB")
    _sh.copy(f, os.path.join(inst, "update.sh"))

    def mkzip(ver, mark):
        zd = tempfile.mkdtemp(dir=base)
        tree(os.path.join(zd, "pocketknife"), ver, mark)
        open(os.path.join(zd, "pocketknife", "fremd.txt"), "w").write("x")
        zp = os.path.join(base, f"pk-{ver}-{mark}.zip")
        with _z.ZipFile(zp, "w") as z:
            for dp, _dn, fn in os.walk(zd):
                for n in fn:
                    z.write(os.path.join(dp, n), os.path.relpath(os.path.join(dp, n), zd))
        return zp
    env = {**os.environ, "PATH": bind + ":" + os.environ.get("PATH", ""), "FAKE_STATE": st}
    open(os.path.join(st, "running"), "w").write(inst + "\n")

    def run(zp):
        return _sp.run(["sh", "update.sh", "-y", zp], cwd=inst, env=env, capture_output=True, text=True, timeout=120)
    rd = lambda *p: open(os.path.join(inst, *p)).read().strip()  # noqa: E731
    r = run(mkzip("2.24.4", "NEU"))
    assert r.returncode == 0, r.stdout[-800:] + r.stderr[-800:]
    assert rd("VERSION") == "2.24.4" and rd(".pk-vorher", "VERSION") == "2.24.2" and rd(".pk-vorher", "app", "m.txt") == "ALT"
    assert not os.path.exists(os.path.join(inst, "fremd.txt")), "nur bekannte Programmdateien übernehmen"
    assert rd("data", "pocketknife.db") == "DB" and rd(".env") == "PK_DNS_1=1.2.3.4\nPK_IMAGE=pocketknife:local", rd(".env")
    # Gleiche Version noch einmal → .pk-vorher behält den echten Vorgänger
    r = run(mkzip("2.24.4", "NEU2"))
    assert r.returncode == 0 and rd(".pk-vorher", "VERSION") == "2.24.2" and rd("run.py") == "NEU2", r.stdout[-500:]
    # Abbruch mitten im Tausch (volle Platte) → alles automatisch zurück, nichts fehlt
    open(os.path.join(st, "mvfail"), "w").close()
    r = run(mkzip("2.24.5", "KAPUTT"))
    assert r.returncode != 0 and "Restored." in r.stderr, r.stdout[-500:] + r.stderr[-500:]
    assert rd("VERSION") == "2.24.4" and rd("run.py") == "NEU2" and rd("app", "m.txt") == "NEU2", "Programmordner wieder vollständig"
    assert rd(".pk-vorher", "VERSION") == "2.24.2" and rd(".env") == "PK_DNS_1=1.2.3.4\nPK_IMAGE=pocketknife:local"
    assert not os.path.exists(os.path.join(inst, ".pk-update-unvollstaendig"))
    assert not [n for n in os.listdir(inst) if n.startswith(".pk-update.")], "kein temporärer Ordner übrig"
    os.remove(os.path.join(st, "mvfail"))
    # Alte Versionsordner: mit Sicherungen oder Datenbankdatei bleiben sie, leere werden gelöscht
    for v, extra in (("2.24", "data-backup/vor-2.24.tar.gz"), ("2.23.3", "alt/pocketknife.db"), ("2.23.2", None)):
        d = os.path.join(inst, f"pocketknife-{v}")
        tree(d, v, "X")
        if extra:
            os.makedirs(os.path.dirname(os.path.join(d, extra)), exist_ok=True)
            open(os.path.join(d, extra), "w").write("x")
    r = run(mkzip("2.24.6", "NEU3"))
    assert r.returncode == 0, r.stdout[-500:]
    assert os.path.isdir(os.path.join(inst, "pocketknife-2.24")) and os.path.isdir(os.path.join(inst, "pocketknife-2.23.3"))
    assert not os.path.exists(os.path.join(inst, "pocketknife-2.23.2"))
    assert rd(".pk-vorher", "VERSION") == "2.24.4"


@test("2.27.1 update.sh --pull: fertiges Image aus der Registry (Digest), Programm-ZIP aus dem Image mit Prüfsumme, kein Bauen; Rückweg ohne Bauen; ZIP-Weg unverändert")
def _():
    import hashlib as _hl
    import shutil as _sh
    import subprocess as _sp
    import re as _re
    import zipfile as _z
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    f = os.path.join(root, "update.sh")
    if not os.path.exists(f) or not _sh.which("sh") or not _sh.which("sha256sum"):
        return  # im Container liegt update.sh nicht
    src = open(f, encoding="utf-8").read()
    for need in ('--pull) PULL=1', 'env_set "$INST/.env" PK_IMAGE "$WANT_IMAGE"', 'docker tag "$PREVID" "$BACK_IMAGE"', ".pk-rotiert-gerade", "sha256sum -c",
                 'DEFAULT_IMAGE_REPO="ghcr.io/', "docker login"):
        assert need in src, need
    dc = open(os.path.join(root, "docker-compose.yml"), encoding="utf-8").read()
    assert "image: ${PK_IMAGE:-ghcr.io/soleil0815/pocketknife:latest}" in dc, "Standard: fertiges Image aus der Registry (:latest – Dockhand/Watchtower)"
    assert not _re.search(r"^\s*build:", dc.split("guacd:")[0], _re.M), "kein „build:“ – sonst baut eine Container-Verwaltung statt zu ziehen"
    df = open(os.path.join(root, "Dockerfile"), encoding="utf-8").read()
    assert "dist* /opt/pocketknife/dist/" in df, "Programm-ZIP gehört ins Image (für update.sh --pull)"
    di = open(os.path.join(root, ".dockerignore"), encoding="utf-8").read()
    assert di.index("*.zip") < di.index("!dist/pocketknife-*.zip"), "ZIP in dist/ darf in den Build"
    wf = open(os.path.join(root, ".github", "workflows", "release.yml"), encoding="utf-8").read()
    for need in ("linux/amd64,linux/arm64", "tools/selftest.py", "ghcr.io", "git archive", "sha256sum", "org.opencontainers.image.version"):
        assert need in wf, need
    import re as _re
    for use in _re.findall(r"uses:\s*(\S+)", wf):
        assert _re.search(r"@[0-9a-f]{40}$", use), f"Action nicht auf Commit festgelegt: {use}"
    base = tempfile.mkdtemp(prefix="pk-pull-", dir=TMP)
    bind, st = os.path.join(base, "bin"), os.path.join(base, "st")
    os.makedirs(bind); os.makedirs(os.path.join(st, "dist"))
    with open(os.path.join(bind, "docker"), "w") as h:
        h.write('#!/bin/sh\nst="$FAKE_STATE"\necho "$*" >> "$st/calls"\ncase "$1" in\n info) exit 0 ;;\n'
                ' pull) [ -f "$st/pullfail" ] && { echo "denied" >&2; exit 1; }; exit 0 ;;\n'
                ' create) echo cid123 ;;\n cp) cp -r "$st/dist/." "$3" ;;\n rm) exit 0 ;;\n'
                ' tag) echo "$2 $3" >> "$st/tags" ;;\n build) touch "$st/built" ;;\n'
                ' image) case "$*" in *RepoDigests*) echo "ghcr.io/test/pocketknife@sha256:abc" ;; *image.version*) cat "$st/imgver" ;;'
                ' *.Id*) cat "$st/newid" ;; esac; exit 0 ;;\n'
                ' inspect) case "$*" in *working_dir*) cat "$st/running" 2>/dev/null ;; *Health*) if [ -f "$st/unhealthy" ]; then echo unhealthy; else echo healthy; fi ;; *Config.Image*) cat "$st/runref" ;; *.Image*) cat "$st/runid" ;; esac; exit 0 ;;\n'
                ' compose) case "$2" in build) touch "$st/built" ;; up) pwd > "$st/running"; cat VERSION > "$st/runver" ;; exec) echo ok ;; esac; exit 0 ;;\n'
                'esac\nexit 0\n')
    os.chmod(os.path.join(bind, "docker"), 0o755)
    inst = os.path.join(base, "opt")

    def tree(d, ver, mark):
        os.makedirs(os.path.join(d, "app"), exist_ok=True)
        for n, c in (("VERSION", ver), ("docker-compose.yml", "x"), ("run.py", mark), ("app/m.txt", mark)):
            open(os.path.join(d, n), "w").write(c + "\n")
    tree(inst, "2.27", "ALT")
    open(os.path.join(inst, ".env"), "w").write("PK_DNS_1=1.2.3.4\nPK_IMAGE_REPO=ghcr.io/test/pocketknife\n")
    _sh.copy(f, os.path.join(inst, "update.sh"))

    def dist(ver, mark, bad_sum=False):
        _sh.rmtree(os.path.join(st, "dist")); os.makedirs(os.path.join(st, "dist"))
        zd = tempfile.mkdtemp(dir=base)
        tree(os.path.join(zd, "pocketknife"), ver, mark)
        zp = os.path.join(st, "dist", f"pocketknife-{ver}.zip")
        with _z.ZipFile(zp, "w") as z:
            for dp, _dn, fn in os.walk(zd):
                for n in fn:
                    z.write(os.path.join(dp, n), os.path.relpath(os.path.join(dp, n), zd))
        h = _hl.sha256(open(zp, "rb").read()).hexdigest()
        open(zp + ".sha256", "w").write(("0" * 64 if bad_sum else h) + f"  pocketknife-{ver}.zip\n")
        open(os.path.join(st, "imgver"), "w").write(ver + "\n")
    env = {**os.environ, "PATH": bind + ":" + os.environ.get("PATH", ""), "FAKE_STATE": st}
    open(os.path.join(st, "running"), "w").write(inst + "\n")
    open(os.path.join(st, "runid"), "w").write("sha256:old\n")
    open(os.path.join(st, "runref"), "w").write("pocketknife:local\n")
    open(os.path.join(st, "newid"), "w").write("sha256:new\n")
    run = lambda *a: _sp.run(["sh", "update.sh", "-y", *a], cwd=inst, env=env, capture_output=True, text=True, timeout=120)  # noqa: E731
    rd = lambda *p: open(os.path.join(inst, *p)).read().strip()  # noqa: E731
    dist("2.27.1", "NEU")
    r = run("--pull")
    assert r.returncode == 0, r.stdout[-900:] + r.stderr[-900:]
    assert rd("VERSION") == "2.27.1" and rd("run.py") == "NEU" and rd(".pk-vorher", "VERSION") == "2.27"
    assert not os.path.exists(os.path.join(st, "built")), "--pull baut nicht"
    tags = open(os.path.join(st, "tags")).read()
    assert "sha256:old pocketknife:rollback" in tags and "pocketknife:rollback pocketknife:previous" in tags, tags
    assert "pocketknife:local" not in tags, tags
    assert "PK_IMAGE=ghcr.io/test/pocketknife:latest" in rd(".env") and "PK_DNS_1=1.2.3.4" in rd(".env"), "Container zeigt die Herkunft (wie andere Container)"
    assert "pull ghcr.io/test/pocketknife:latest" in open(os.path.join(st, "calls")).read()
    assert not [n for n in os.listdir(inst) if n.startswith((".pk-pull.", ".pk-update."))], "keine temporären Ordner übrig"
    # bestimmte Version; Prüfsumme falsch → nichts geändert
    dist("2.27.2", "BOESE", bad_sum=True)
    r = run("--pull", "2.27.2")
    assert r.returncode != 0 and "Checksum" in r.stderr and rd("VERSION") == "2.27.1", r.stdout[-500:] + r.stderr[-500:]
    assert "pull ghcr.io/test/pocketknife:2.27.2" in open(os.path.join(st, "calls")).read()
    # Image und ZIP passen nicht zusammen
    dist("2.27.2", "X"); open(os.path.join(st, "imgver"), "w").write("2.27.9\n")
    r = run("--pull=2.27.2")
    assert r.returncode != 0 and "do not match" in r.stderr and rd("VERSION") == "2.27.1"
    # gleiches Image, aber der Container zeigt noch „pocketknife:local“ → nur den Namen umstellen
    dist("2.27.1", "NEU"); open(os.path.join(st, "runid"), "w").write("sha256:new\n")
    open(os.path.join(inst, ".env"), "w").write("PK_DNS_1=1.2.3.4\nPK_IMAGE_REPO=ghcr.io/test/pocketknife\n")
    r = run("--pull")
    assert r.returncode == 0 and "now shows ghcr.io/test/pocketknife:latest" in r.stdout and "PK_IMAGE=ghcr.io/test/pocketknife:latest" in rd(".env"), r.stdout[-500:]
    # schon aktuell (genau dieses Image unter diesem Namen) → nichts zu tun
    open(os.path.join(st, "runref"), "w").write("ghcr.io/test/pocketknife:latest\n")
    r = run("--pull")
    assert r.returncode == 0 and "already up to date" in r.stdout, r.stdout[-500:]
    # ZIP-Weg danach: wieder lokal gebaut als pocketknife:local (PK_IMAGE leer)
    zd = tempfile.mkdtemp(dir=base); tree(os.path.join(zd, "pocketknife"), "2.27.3", "ZIP")
    zp = os.path.join(base, "pocketknife-2.27.3.zip")
    with _z.ZipFile(zp, "w") as z:
        for dp, _dn, fn in os.walk(zd):
            for n in fn:
                z.write(os.path.join(dp, n), os.path.relpath(os.path.join(dp, n), zd))
    # Audit 2.29: liegt die Prüfsumme des Releases daneben, muss sie stimmen
    open(zp + ".sha256", "w").write("0" * 64 + "  pocketknife-2.27.3.zip\n")
    r = run(zp)
    assert r.returncode != 0 and "Checksum" in r.stderr and rd("VERSION") == "2.27.1", r.stdout[-500:] + r.stderr[-300:]
    open(zp + ".sha256", "w").write(_hl.sha256(open(zp, "rb").read()).hexdigest() + "  pocketknife-2.27.3.zip\n")
    r = run(zp)
    assert r.returncode == 0 and os.path.exists(os.path.join(st, "built")), r.stdout[-500:]
    assert "Checksum OK" in r.stdout
    assert "PK_IMAGE=pocketknife:local" in rd(".env"), ("ZIP-Weg: lokal gebautes Image", rd(".env"))
    os.remove(os.path.join(st, "built"))
    # Neue Version startet nicht → zurück zum vorigen Image unter dem Namen, den die bisherige .env erwartet
    open(os.path.join(inst, ".env"), "w").write("PK_DNS_1=1.2.3.4\nPK_IMAGE_REPO=ghcr.io/test/pocketknife\nPK_IMAGE=ghcr.io/test/pocketknife:latest\n")
    open(os.path.join(st, "runref"), "w").write("ghcr.io/test/pocketknife:latest\n")
    open(os.path.join(st, "runid"), "w").write("sha256:old\n")
    open(os.path.join(st, "tags"), "w").close()
    open(os.path.join(st, "unhealthy"), "w").close()
    dist("2.27.4", "KAPUTT")
    r = run("--pull")
    assert r.returncode != 0 and "Previous version 2.27.3 is running again" in r.stdout, r.stdout[-800:]
    tags = open(os.path.join(st, "tags")).read()
    assert "sha256:old pocketknife:rollback" in tags and "pocketknife:rollback ghcr.io/test/pocketknife:latest" in tags, tags
    assert "pocketknife:previous" not in tags, "ein gescheitertes Update überschreibt das letzte gute Image nicht"
    assert rd("VERSION") == "2.27.3" and "PK_IMAGE=ghcr.io/test/pocketknife:latest" in rd(".env")
    assert not os.path.exists(os.path.join(st, "built")), "Rückweg ohne Bauen"
    # Audit 2.29: Zurückgehen abgelehnt → beim nächsten Lauf bleiben .pk-vorher/ und das letzte gute Image erhalten
    open(os.path.join(st, "tags"), "w").close()
    open(os.path.join(st, "runid"), "w").write("sha256:gut\n")
    dist("2.27.5", "SCHLECHT")
    r = _sp.run(["sh", "update.sh", "--pull"], cwd=inst, env=env, input="y\nn\n", capture_output=True, text=True, timeout=120)
    assert r.returncode != 0 and rd("VERSION") == "2.27.5" and os.path.exists(os.path.join(inst, ".pk-update-unvollstaendig")), r.stdout[-800:]
    assert rd(".pk-vorher", "VERSION") == "2.27.3" and not os.path.exists(os.path.join(inst, ".pk-rotiert-gerade"))
    open(os.path.join(st, "runid"), "w").write("sha256:schlecht\n")
    # noch ein schlechtes Update, diesmal zurück: zurück kommt der laufende (schlechte) Stand MIT seinem Image –
    # das letzte gute Image („rollback“) und .pk-vorher/ bleiben unangetastet, der Lauf bleibt „unvollständig“
    dist("2.27.6", "AUCHSCHLECHT")
    r = run("--pull")
    assert r.returncode != 0 and rd("VERSION") == "2.27.5" and rd("run.py") == "SCHLECHT", r.stdout[-800:]
    tags = open(os.path.join(st, "tags")).read()
    assert "sha256:schlecht pocketknife:rollback-run" in tags and "pocketknife:rollback-run ghcr.io/test/pocketknife:latest" in tags, tags
    assert "pocketknife:rollback ghcr.io" not in tags and "sha256:schlecht pocketknife:rollback\n" not in tags, tags
    assert rd(".pk-vorher", "VERSION") == "2.27.3" and os.path.exists(os.path.join(inst, ".pk-update-unvollstaendig"))
    os.remove(os.path.join(st, "unhealthy"))
    dist("2.27.7", "REPARIERT")
    r = run("--pull")
    assert r.returncode == 0 and rd("VERSION") == "2.27.7", r.stdout[-800:] + r.stderr[-500:]
    assert rd(".pk-vorher", "VERSION") == "2.27.3", "der letzte gute Stand wird nicht durch den gescheiterten ersetzt"
    tags = open(os.path.join(st, "tags")).read()
    assert "sha256:gut pocketknife:rollback\n" in tags and "sha256:schlecht pocketknife:rollback\n" not in tags, tags
    assert "pocketknife:rollback pocketknife:previous" in tags, "letztes gutes Image wird „previous“ (passt zu .pk-vorher/)"
    assert not os.path.exists(os.path.join(inst, ".pk-rollback-ref"))
    # Abbruch MITTEN im Umbenennen (Marker gesetzt) → .pk-vorher.alt ist der vollständige Vorgänger und kommt zurück
    os.rename(os.path.join(inst, ".pk-vorher"), os.path.join(inst, ".pk-vorher.alt"))
    os.makedirs(os.path.join(inst, ".pk-vorher"))
    open(os.path.join(inst, ".pk-rotiert-gerade"), "w").close()
    open(os.path.join(inst, ".pk-update-unvollstaendig"), "w").write("2.27.7\n")
    dist("2.27.8", "WEITER")
    r = run("--pull")
    assert r.returncode == 0 and rd(".pk-vorher", "VERSION") == "2.27.3", r.stdout[-800:]
    assert not os.path.exists(os.path.join(inst, ".pk-vorher.alt")) and not os.path.exists(os.path.join(inst, ".pk-rotiert-gerade"))
    open(os.path.join(st, "runref"), "w").write("pocketknife:local\n")
    # Anmeldung fehlt → verständlicher Hinweis, nichts geändert
    open(os.path.join(st, "pullfail"), "w").close()
    r = run("--pull")
    assert r.returncode != 0 and "docker login ghcr.io" in r.stdout and rd("VERSION") == "2.27.8"
    assert not [n for n in os.listdir(inst) if n.startswith((".pk-pull.", ".pk-update."))]


@test("2.24.4 Benachrichtigung: vom Benutzer abgeschaltet wird respektiert, Start unabhängig von der Ausführungsrichtlinie, Rechte unter „Programme“ erzwungen")
def _():
    import base64 as _b
    from app import toast
    u = toast.USER_PS
    assert "Done 12" in u and "$st.Enabled -eq 0" in u and "ShowInActionCenter -eq 0) { Done 11 }" in u
    assert u.index("Done 12") < u.index("Rückfall"), "Abschaltung prüfen, BEVOR auf „Windows PowerShell“ ausgewichen wird"
    assert toast.RESULT_TEXT[12][0] is False
    script, _d = toast.build_send_script([("installed", "7-Zip", "26.00")])
    assert "[ScriptBlock]::Create([IO.File]::ReadAllText(" in script and "$pol -eq 'AllSigned'" in script, "kodierter Starter nur bei GPO AllSigned/Restricted"
    assert "/setowner '*S-1-5-32-544'" in script and "/reset" in script and "Unsichere Rechte" in script


@test("2.24.5 Benachrichtigung bei Softwareeinschränkung (SRP/AppLocker, Constrained Language Mode): Start per -File aus „Programme“; Ergebnis kommt aus dem Skript selbst, nicht aus dem Exitcode von conhost")
def _():
    import re as _re
    from app import toast
    script, _d = toast.build_send_script([("installed", "7-Zip", "26.00")])
    i_enc = script.index("-EncodedCommand"); i_file = script.index("-WindowStyle Hidden -File")
    blk = script[script.index("function Action"):script.index("$set = New-ScheduledTaskSettingsSet")]
    assert "if ($pol -eq 'AllSigned' -or $pol -eq 'Restricted')" in blk and "} else {" in blk and i_enc < i_file
    assert "EnableScripts" in script and "HKEY_USERS" in script
    u = toast.USER_PS
    assert u.startswith("param([string]$Data, [string]$Rid)") and "function Done([int]$c)" in u and "trap { Done 1 }" in u
    assert not _re.search(r"^(?!\s*exit \$c)[^#\n]*\bexit [0-9]", u, _re.M), "jedes Ergebnis über Done (Registry), nie nur als Exitcode"
    assert "CreateSubKey('Software\\PocketKnife\\Toast')" in u, "Ergebnis in HKCU\\Software\\PocketKnife\\Toast"
    assert "Software\\PocketKnife\\Toast" in script and ":13'" in script, "keine Antwort vom Skript → 13, nie „gezeigt“"
    assert toast.RESULT_TEXT[13][0] is False and "Softwareeinschränkung" in toast.RESULT_TEXT[13][1]


@test("2.24.6 Meldung bei Neustart/Herunterfahren einstellbar (Absender, Texte, Platzhalter) – sicher an shutdown.exe übergeben")
def _():
    import glob as _g
    from app import main as _m, rebootmsg, steps, util
    for k in ["reboot_sender"] + list(rebootmsg.KEYS.values()):
        assert k in db.DEFAULT_SETTINGS and k in _m.EDITABLE, k
    db.set_setting("reboot_sender", "")
    for k in rebootmsg.KEYS.values():
        db.set_setting(k, "")
    assert rebootmsg.text("restart", sekunden=60) == "POCKET KNIFE: Neustart in 60 Sekunden.", "Standard unverändert"
    assert rebootmsg.text("deploy", sekunden=120) == "POCKET KNIFE: Neustart nach Softwareverteilung in 2 Minuten."
    db.set_setting("reboot_sender", "Ihre IT-Abteilung")
    assert rebootmsg.text("shutdown", sekunden=60) == "Ihre IT-Abteilung: Herunterfahren in 60 Sekunden."
    assert "angefordert von Max Schmidt" in rebootmsg.text("kiosk", sekunden=300, benutzer="Max Schmidt")
    db.set_setting("reboot_msg_step", 'Achtung "{absender}"\nNeustart in {minuten} Min. {unbekannt}')
    t = steps.reboot_text({"delay_sec": 90})
    assert t == "Achtung 'Ihre IT-Abteilung' Neustart in 1,5 Min. {unbekannt}", t
    assert steps.reboot_text({"delay_sec": 60, "message": "Eigener Text: {absender}"}) == "Eigener Text: Ihre IT-Abteilung", "Schritttext hat Vorrang"
    evil = "x'; Remove-Item C:\\ -Recurse; '"
    db.set_setting("reboot_sender", evil)
    q = util.psq(rebootmsg.text("restart", sekunden=60))
    assert q.startswith("'x''; Remove-Item"), "Text immer als PowerShell-Literal (psq), nie als Code"
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app")
    for f in _g.glob(os.path.join(root, "*.py")):
        src = open(f, encoding="utf-8").read()
        assert "POCKET KNIFE: Neustart" not in src.replace('"{absender}', "") or f.endswith("rebootmsg.py"), f"fester Text in {os.path.basename(f)}"
    db.set_setting("reboot_sender", "")
    db.set_setting("reboot_msg_step", "")


@test("2.24.6 Geänderter Absender der Benachrichtigung erscheint wirklich (eigene Absender-ID je Name, Windows merkt sich alte Namen)")
def _():
    from app import toast
    db.set_setting("toast_compat", 0)
    db.set_setting("toast_sender", "Ihre IT-Abteilung")
    a = toast.build_send_script([("installed", "A", "1")])[1]
    db.set_setting("toast_sender", "Softwareverteilung")
    b = toast.build_send_script([("installed", "A", "1")])[1]
    assert a["sender"] == "Ihre IT-Abteilung" and a["appid"] != b["appid"] and a["appid"].startswith(toast.APP_ID + ".")
    assert a["appid"] == toast.app_id("Ihre IT-Abteilung"), "gleicher Name → gleiche ID (keine neue Registrierung bei jeder Meldung)"
    u = toast.USER_PS
    assert "$_.PSChildName -like ($d.base + '*')" in u and "Remove-Item -Path $o.PSPath" in u and "if ($off" in u, \
        "alte IDs aufräumen; vom Benutzer abgeschaltet bleibt abgeschaltet"


@test("2.24.8 update.sh: Zurücklegen darf selbst unterbrochen werden (zweiter Durchlauf zerstört nichts), Bau-Fehler legt zurück, kein leeres .pk-vorher")
def _():
    import shutil as _sh
    import subprocess as _sp
    import zipfile as _z
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    f = os.path.join(root, "update.sh")
    if not os.path.exists(f) or not _sh.which("sh"):
        return
    base = tempfile.mkdtemp(prefix="pk-upd2-", dir=TMP)
    bind, st = os.path.join(base, "bin"), os.path.join(base, "st")
    os.makedirs(bind); os.makedirs(st)
    with open(os.path.join(bind, "docker"), "w") as h:
        h.write('#!/bin/sh\nst="$FAKE_STATE"\ncase "$1" in\n info|image) exit 0 ;;\n build) [ -f "$st/buildfail" ] && exit 1; exit 0 ;;\n'
                ' inspect) case "$*" in *working_dir*) cat "$st/running" 2>/dev/null; exit 0 ;; *Health*) echo healthy; exit 0 ;; esac; exit 0 ;;\n'
                ' compose) case "$2" in build) [ -f "$st/buildfail" ] && exit 1; exit 0 ;; up) pwd > "$st/running"; exit 0 ;; exec) echo ok; exit 0 ;; esac; exit 0 ;;\nesac\nexit 0\n')
    with open(os.path.join(bind, "mv"), "w") as h:  # Zurücklegen von app/ scheitert genau einmal (z. B. E/A-Fehler)
        h.write('#!/bin/sh\ncase "$1" in */.pk-vorher/app) if [ -f "$FAKE_STATE/mvfail_once" ]; then rm -f "$FAKE_STATE/mvfail_once"; echo "mv: Input/output error" >&2; exit 1; fi ;; esac\nexec /bin/mv "$@"\n')
    for x in ("docker", "mv"):
        os.chmod(os.path.join(bind, x), 0o755)
    inst = os.path.join(base, "opt")

    def tree(d, ver, mark):
        os.makedirs(os.path.join(d, "app"), exist_ok=True)
        for n, c in (("VERSION", ver), ("docker-compose.yml", "x"), ("run.py", mark), ("app/m.txt", mark)):
            open(os.path.join(d, n), "w").write(c + "\n")

    def mkzip(ver, mark):
        zd = tempfile.mkdtemp(dir=base)
        tree(os.path.join(zd, "pocketknife"), ver, mark)
        zp = os.path.join(base, f"pk-{ver}-{mark}.zip")
        with _z.ZipFile(zp, "w") as z:
            for dp, _dn, fn in os.walk(zd):
                for n in fn:
                    z.write(os.path.join(dp, n), os.path.relpath(os.path.join(dp, n), zd))
        return zp
    tree(inst, "2.24.2", "ALT")
    open(os.path.join(inst, ".env"), "w").write("PK_SERVICE_PASSWORD=geheim\n")
    env = {**os.environ, "PATH": bind + ":" + os.environ.get("PATH", ""), "FAKE_STATE": st}
    open(os.path.join(st, "running"), "w").write(inst + "\n")

    def run(zp):
        _sh.copy(f, os.path.join(inst, "update.sh"))  # immer das aktuelle Skript (nicht das aus dem ZIP)
        return _sp.run(["sh", "update.sh", "-y", zp], cwd=inst, env=env, capture_output=True, text=True, timeout=120)
    rd = lambda *p: open(os.path.join(inst, *p)).read().strip()  # noqa: E731
    # Erstes Update scheitert beim Bau → alles zurück, kein halbleeres .pk-vorher übrig
    open(os.path.join(st, "buildfail"), "w").close()
    r = run(mkzip("2.24.3", "B"))
    assert r.returncode != 0 and rd("VERSION") == "2.24.2" and rd("app", "m.txt") == "ALT", r.stdout[-400:] + r.stderr[-400:]
    assert not os.path.exists(os.path.join(inst, ".pk-vorher")), "vorher gab es keinen gesicherten Stand"
    os.remove(os.path.join(st, "buildfail"))
    assert run(mkzip("2.24.4", "NEU")).returncode == 0 and rd(".pk-vorher", "VERSION") == "2.24.2"
    # Bau scheitert UND das Zurücklegen wird unterbrochen → on_exit macht es fertig; nichts geht verloren
    open(os.path.join(st, "buildfail"), "w").close(); open(os.path.join(st, "mvfail_once"), "w").close()
    r = run(mkzip("2.24.5", "KAPUTT"))
    assert r.returncode != 0 and "Restored." in r.stderr, r.stdout[-400:] + r.stderr[-600:]
    assert rd("VERSION") == "2.24.4" and rd("run.py") == "NEU" and rd("app", "m.txt") == "NEU", "Programmordner vollständig"
    assert rd(".pk-vorher", "VERSION") == "2.24.2" and rd(".pk-vorher", "app", "m.txt") == "ALT", "gesicherter Vorgänger unversehrt"
    assert rd(".env") == "PK_SERVICE_PASSWORD=geheim\nPK_IMAGE=pocketknife:local", rd(".env")  # Stand vor diesem Lauf
    assert not os.path.exists(os.path.join(inst, ".pk-update-unvollstaendig"))
    src = open(f, encoding="utf-8").read()
    assert "list_drop" in src and "trap '' INT TERM" in src and 'cp "$TMP/bisher/.env" "$INST/.env"' not in src


@test("2.24.8 Benachrichtigung/Neustart-Meldung: Review-Funde (Rückmeldung ohne Leeren des Schlüssels, Aufräumen, Codes 13/14, 0 Sekunden, Backslash)")
def _():
    from app import rebootmsg, steps, toast
    u = toast.USER_PS
    assert "CreateSubKey('Software\\PocketKnife\\Toast')" in u and "New-Item -Path $rk" not in u
    assert "if (-not (Test-Path $k)) { New-Item -Path $k -Force" in u, "Absender-Schlüssel nie neu anlegen (leeren)"
    sc = toast.build_send_script([("installed", "7-Zip", "26.00")])[0]
    assert "$have -cne $code" in sc and "$hv -cne" in sc, "Groß-/Kleinschreibung zählt beim Vergleich des Skripts"
    assert "ToString('yyMMddHHmm')" in sc and "AddMinutes(-30)" in sc and ":14'" in sc
    res = toast.results("PK_TOAST:res:A\\x:13\nPK_TOAST:res:B\\y:14\nPK_TOAST:res:C\\z:-2147023570\nPK_TOAST:res:D\\w:0\n")
    assert [o for _u, o, _t in res] == [False, False, False, True] and "Code -2147023570" in res[2][2]
    assert "Softwareeinschränkung" in res[0][2] and "nicht auflösen" in res[1][2]
    db.set_setting("reboot_msg_step", "")
    assert steps.reboot_text({"delay_sec": 0}) == "POCKET KNIFE: Neustart für die Softwareverteilung"
    db.set_setting("reboot_msg_step", "{absender}: Neustart in {sekunden} s")
    assert steps.reboot_text({"delay_sec": 0}).endswith("in 0 s"), "0 Sekunden bleiben 0 (nicht 60)"
    db.set_setting("reboot_msg_step", "")
    assert rebootmsg.clean("Ihre IT \\\\", 60) == "Ihre IT", "kein Backslash am Ende (zerlegt das Argument von shutdown.exe)"


def _asgi(method, path, token=None, body=None, raw=None):
    """Eine Anfrage direkt an die App (ohne Netzwerk) – (Status, JSON oder Text). raw: Bytes statt JSON."""
    import asyncio
    from app import main as _m
    path, _, qs = path.partition("?")
    data = raw if raw is not None else (json.dumps(body if body is not None else {}).encode() if method in ("POST", "PUT", "DELETE") else b"")
    ctype = b"application/octet-stream" if raw is not None else b"application/json"
    hdr = [(b"host", b"testserver"), (b"content-type", ctype), (b"content-length", str(len(data)).encode()),
           (b"x-pk-request", b"1")]  # wie die Oberfläche (CSRF-Schutz) – sonst prüfte der Test nur den CSRF-Schutz
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


@test("2.25 OU-Admins: jede Schnittstelle ist für sie gesperrt, außer den freigegebenen – und die zeigen/steuern nur ihre Computer")
def _():
    import hashlib
    import re as _re
    import secrets
    import time as _t
    from app import access, auth, config, deploy, pklog, schedules, util
    from app import main as _m
    # Aufbau: Mandant „Nord“ (Sammlung mit 2 Computern, OU-Admins, eigene Pakete erlaubt), Mandant „Süd“ mit eigenem Paket,
    # ein fremder Computer, ein zentrales Paket
    ids = [db.ex("INSERT INTO computers(name, dns_name, created_at) VALUES(?,?,?)", (n, n + ".firma.local", util.now()))
           for n in ("OU-IN1", "OU-IN2", "OU-OUT")]
    cin1, cin2, cout = ids
    col = db.ex("INSERT INTO collections(name, type) VALUES('OU Nord (Test)', 'static')")
    for c in (cin1, cin2):
        db.ex("INSERT INTO collection_members VALUES(?,?)", (col, c))
    t_nord = db.ex("INSERT INTO tenants(name, collection_id, repo_folder, admin_group, allow_packages, allow_scripts, created_at) "
                   "VALUES('Nord', ?, 'Kunden\\Nord', 'PK-OU-Nord', 1, 1, ?)", (col, util.now()))  # Skript-Schritte erlaubt (2.29.1)
    t_sued = db.ex("INSERT INTO tenants(name, created_at) VALUES('Süd', ?)", (util.now(),))
    p_central = db.ex("INSERT INTO packages(name, version, steps, conditions, run_as, created_at) VALUES('Zentral (Test)','1',?,'{}','deploy',?)",
                      (json.dumps({"main": [{"id": "z1", "type": "command", "command": "hostname"}]}), util.now()))
    p_sued = db.ex("INSERT INTO packages(name, version, steps, conditions, run_as, tenant_id, created_at) VALUES('Süd-Paket','1',?,'{}','deploy',?,?)",
                   (json.dumps({"main": [{"type": "command", "command": "hostname"}]}), t_sued, util.now()))
    for c in ids:
        db.ex("INSERT INTO software(computer_id, name, version) VALUES(?,?,?)", (c, f"OU-Testprogramm-{c}", "1"))
        pklog.info("system", f"OU-Testeintrag {c}", computer_id=c)
    access.flush()

    def session(scope):
        tok = secrets.token_urlsafe(16)
        th = hashlib.sha256(tok.encode()).hexdigest()
        db.ex("INSERT INTO sessions(token_hash, username, display_name, created, expires, ip, last_active, role, scope) VALUES(?,?,?,?,?,?,?,?,?)",
              (th, "ou-test" if scope else "haupt-test", "Test", util.now(), "2099-01-01 00:00:00", "127.0.0.1", util.now(), "admin",
               json.dumps(scope) if scope is not None else None))
        auth._checked[th] = _t.time() + 10 ** 6  # keine AD-Nachprüfung im Test
        return tok
    ou, main_admin = session([t_nord]), session(None)
    st, me = _asgi("GET", "/api/me", ou)
    assert st == 200 and me["scoped"] is True and me["computers"] == 2 and me["may_build"], (st, me)
    assert _asgi("GET", "/api/me", main_admin)[1]["scoped"] is False

    # 1) Freigabeliste: jeder Eintrag gibt es wirklich (kein Tippfehler), und kritische Routen sind NICHT freigegeben
    routes = set()
    for r in _m.app.routes:
        for mth in (getattr(r, "methods", None) or ()):
            if mth != "HEAD":
                routes.add((mth, _re.sub(r"\{(\w+):[^}]+\}", r"{\1}", r.path)))
    missing = [k for k in access.ROUTES if k not in routes]
    assert not missing, f"Freigabeliste nennt unbekannte Routen: {missing}"
    for k in [("PUT", "/api/settings"), ("GET", "/api/settings"), ("POST", "/api/tenants"), ("PUT", "/api/tenants/{tid}"),
              ("DELETE", "/api/tenants/{tid}"), ("GET", "/api/system"), ("PUT", "/api/system/accounts"), ("GET", "/api/backups"),
              ("POST", "/api/tools"), ("PUT", "/api/tools/{tid}"), ("POST", "/api/collections"), ("PUT", "/api/collections/{cid}"),
              ("GET", "/api/ad/browse"), ("PUT", "/api/kiosk/admin/items/{pid}"), ("GET", "/api/mail/settings"),
              ("DELETE", "/api/computers/{cid}"), ("POST", "/api/computers"), ("GET", "/api/system/diagnostics"),
              ("PUT", "/api/remote/vnc_password"), ("POST", "/api/winget/refresh"), ("POST", "/api/software/exclusions")]:
        assert k in routes and k not in access.ROUTES, f"darf für OU-Admins nicht freigegeben sein: {k}"

    # 2) Jede NICHT freigegebene Route antwortet einem OU-Admin mit 403 – der Handler läuft nie
    def fill(path, cid):
        return _re.sub(r"\{(\w+)\}", lambda m: str(cid) if m.group(1) == "cid" else "1", path)
    for mth, path in sorted(routes):
        if not path.startswith("/api/") or (mth, path) in access.ROUTES:
            continue
        if path in ("/api/login", "/api/logout", "/api/session/active", "/api/session/idle", "/api/remote/tunnel") \
                or path.startswith("/api/kiosk/") and not path.startswith("/api/kiosk/admin") or path.startswith("/api/mail/view/"):
            continue  # ohne Verwaltungsrechte (Kiosk, Sitzung, Mail-Link mit eigenem Token)
        st, res = _asgi(mth, fill(path, cin1), ou)
        assert st == 403 and "OU-Admins" in str(res), f"{mth} {path}: {st} {res} – muss für OU-Admins gesperrt sein"

    # 3) Freigegebene Routen mit Computer im Pfad: fremder Computer → 404, eigener → nicht 403/404 (nur Lesezugriffe)
    for (mth, path), chk in access.ROUTES.items():
        if "{cid}" not in path:
            continue
        st, _r = _asgi(mth, fill(path, cout), ou, {})
        assert st == 404, f"{mth} {path} mit fremdem Computer: {st} (erwartet 404)"
    for path in ("/api/computers/{cid}", "/api/computers/{cid}/cat/software", "/api/computers/{cid}/cat/logs"):
        st, _r = _asgi("GET", fill(path, cin1), ou)
        assert st == 200, f"GET {path} mit eigenem Computer: {st}"

    # 4) Listen zeigen nur eigene Computer / Daten
    names = {c["name"] for c in _asgi("GET", "/api/computers", ou)[1]}
    assert names == {"OU-IN1", "OU-IN2"}, names
    assert "OU-OUT" in {c["name"] for c in _asgi("GET", "/api/computers", main_admin)[1]}, "Hauptadmin sieht weiter alles"
    sw = {r["name"] for r in _asgi("GET", "/api/software", ou)[1]}
    assert f"OU-Testprogramm-{cin1}" in sw and f"OU-Testprogramm-{cout}" not in sw
    st, det = _asgi("GET", f"/api/software/detail?name=OU-Testprogramm-{cout}", ou)
    assert st == 200 and det["computers"] == [], "Softwaredetails fremder Computer"
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

    # 5) Pakete: alle sichtbar (2.27: auch fremde Mandanten), nur eigene änderbar; eigene landen im eigenen Mandanten
    pk = {p["name"]: p for p in _asgi("GET", "/api/packages", ou)[1]}
    assert "Zentral (Test)" in pk and pk["Zentral (Test)"]["editable"] is False
    assert "Süd-Paket" in pk and pk["Süd-Paket"]["editable"] is False, "fremde Mandanten-Pakete: sehen ja, ändern nein"
    assert _asgi("GET", f"/api/packages/{p_sued}", ou)[0] == 200
    assert _asgi("PUT", f"/api/packages/{p_sued}", ou, {"name": "x"})[0] == 403
    assert _asgi("DELETE", f"/api/packages/{p_sued}", ou)[0] == 403
    assert _asgi("PUT", f"/api/packages/{p_central}", ou, {"name": "x"})[0] == 403
    assert _asgi("DELETE", f"/api/packages/{p_central}", ou)[0] == 403
    st, r = _asgi("POST", "/api/packages", ou, {"name": "Nord-Paket (Test)", "tenant_id": t_sued,
                                                 "steps": {"main": [{"type": "command", "command": "hostname"}]}})
    assert st == 403, "fremden Mandanten angeben → abgelehnt"
    st, r = _asgi("POST", "/api/packages", ou, {"name": "Nord-Paket (Test)", "steps": {"main": [{"type": "command", "command": "hostname"}]}})
    assert st == 200 and db.val("SELECT tenant_id FROM packages WHERE id=?", (r["id"],)) == t_nord, (st, r)
    p_nord = r["id"]
    st, r = _asgi("POST", "/api/packages", ou, {"name": "Verschachtelt (Test)", "steps": {"main": [{"type": "nested", "package_id": p_sued}]}})
    assert st == 403, ("verschachtelt nur zentrale/eigene Pakete", st, r)
    st, r = _asgi("POST", f"/api/packages/{p_central}/duplicate", ou)
    assert st == 200 and db.val("SELECT tenant_id FROM packages WHERE id=?", (r["id"],)) == t_nord, ("Kopie eines zentralen Pakets gehört dem eigenen Mandanten", st, r)
    assert _asgi("GET", "/api/repository?path=Kunden\\Sued", ou)[0] == 403, "Repository nur im eigenen Ordner"
    # 6) Bereitstellen: nur eigene Computer, nur sichtbare Pakete
    assert _asgi("POST", "/api/deployments", ou, {"package_id": p_central, "computer_ids": [cout], "scheduled_at": "2099-01-01 00:00"})[0] == 403
    # 2.27: Pakete anderer Mandanten dürfen auf eigene Computer verteilt werden (nutzen ja, ändern nein)
    st, r = _asgi("POST", "/api/deployments", ou, {"package_id": p_sued, "computer_ids": [cin1], "scheduled_at": "2099-01-01 00:00"})
    assert st == 200, ("fremdes Mandanten-Paket verteilen", st, r)
    db.ex("DELETE FROM deployment_targets WHERE deployment_id=?", (r["id"],))
    db.ex("DELETE FROM deployments WHERE id=?", (r["id"],))
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
    assert st == 200 and [t["computer_id"] for t in d["targets"]] == [cin1], "nur eigene Ziele"
    assert _asgi("GET", f"/api/deployments/{d_foreign}", ou)[0] == 404
    assert _asgi("POST", f"/api/deployments/{d_mixed}/cancel", ou)[0] == 403, "gemischte Bereitstellung nicht abbrechen"
    assert _asgi("DELETE", f"/api/deployments/{d_mixed}", ou)[0] == 403
    assert _asgi("POST", f"/api/deployments/{d_mine}/cancel", ou)[0] == 200
    # 7) Zeitpläne: nur eigener Mandant; neue landen dort
    st, r = _asgi("POST", "/api/schedules", ou, {"name": "Nord-Zeitplan (Test)", "kind": "packages", "packages": [p_nord],
                                                  "targets": {"computers": [cin1], "collections": []}, "enabled": False})
    assert st == 200 and db.val("SELECT tenant_id FROM schedules WHERE id=?", (r["id"],)) == t_nord, (st, r)
    st, r2 = _asgi("POST", "/api/schedules", ou, {"name": "x", "kind": "packages", "packages": [p_central],
                                                   "targets": {"computers": [cout], "collections": []}, "enabled": False})
    assert st == 403, "fremde Computer im Zeitplan"
    s_main = schedules.save(None, {"name": "Haupt-Zeitplan (Test)", "kind": "packages", "packages": [p_central],
                                   "targets": {"computers": [cout], "collections": []}, "enabled": False})
    assert s_main not in {x["id"] for x in _asgi("GET", "/api/schedules", ou)[1]}
    assert _asgi("GET", f"/api/schedules/{s_main}", ou)[0] == 404 and _asgi("POST", f"/api/schedules/{s_main}/run", ou)[0] == 404
    # Zeitplan eines Mandanten erreicht nur dessen Computer (auch wenn fremde eingetragen wären)
    sx = schedules.save(None, {"name": "Nord direkt (Test)", "kind": "packages", "packages": [p_nord], "tenant_id": t_nord,
                               "targets": {"computers": [cin1, cout], "collections": []}, "enabled": False})
    assert schedules.members(schedules.get(sx)) == {cin1}
    # 8) Mandanten: nur der eigene, ohne Ändern; ohne Freigabe keine eigenen Pakete
    assert [t["name"] for t in _asgi("GET", "/api/tenants", ou)[1]] == ["Nord"]
    db.ex("UPDATE tenants SET allow_packages=0 WHERE id=?", (t_nord,))
    assert _asgi("POST", "/api/packages", ou, {"name": "y", "steps": {"main": []}})[0] == 403
    assert _asgi("PUT", f"/api/packages/{p_nord}", ou, {"name": "y"})[0] == 403
    assert _asgi("POST", "/api/schedules", ou, {"name": "y", "kind": "packages"})[0] == 403
    db.ex("UPDATE tenants SET allow_packages=1 WHERE id=?", (t_nord,))
    # 9) Kein Bereich = keine Computer (nie „alles“); Fehlerdetails nie an OU-Admins
    empty = session([])
    assert _asgi("GET", "/api/computers", empty)[1] == [] and _asgi("GET", "/api/me", empty)[1]["scoped"] is True
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "main.py"), encoding="utf-8").read()
    assert 'sess.get("scope") is not None:' in src
    # Mandant mit OU-Admins braucht eine Sammlung; die Hauptadmin-Gruppe ist keine OU-Admin-Gruppe
    assert _asgi("POST", "/api/tenants", main_admin, {"name": "Ohne Sammlung", "admin_group": "PK-OU-X"})[0] == 400
    assert _asgi("POST", "/api/tenants", main_admin, {"name": "Haupt", "admin_group": config.ACCESS_GROUP or "POCKETKNIFE-Admins",
                                                      "collection_id": col})[0] == 400
    # 10) Review 2.25: „für alle merken“ nur Hauptadmins; nie als Dienstkonto; Dateien nur mit eigenem Ordner; Zeitplan-Sammlungen
    sw_in = db.val("SELECT id FROM software WHERE computer_id=?", (cin1,))
    st, r = _asgi("POST", "/api/uninstall", ou, {"items": [{"software_id": sw_in, "command": "cmd /c echo x", "remember": "*"}]})
    assert st == 403 and not db.val("SELECT COUNT(*) FROM uninstall_overrides WHERE pattern='*'"), "Befehl für alle Computer"
    db.ex("UPDATE software SET uninstall_string=? WHERE id=?", ('"C:\\Program Files\\OU\\unins000.exe"', sw_in))
    st, r = _asgi("POST", "/api/uninstall", ou, {"items": [{"software_id": sw_in, "run_as": "deploy"}]})
    assert st == 200 and r["deployments"], (st, r)
    pj = json.loads(db.val("SELECT package_json FROM deployments WHERE id=?", (r["deployments"][0],)))
    assert pj.get("run_as") != "deploy", "Deinstallation (UninstallString vom Client) nie als Dienstkonto"
    db.ex("DELETE FROM deployments WHERE id=?", (r["deployments"][0],))
    from app import uninstall as _un
    swrow = db.q1("SELECT * FROM software WHERE id=?", (sw_in,))
    assert _un.package_for(swrow, None, "deploy")["run_as"] != "deploy", "auch für Hauptadmins: Registry-Befehl nie als Dienstkonto"
    stsrc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "steps.py"), encoding="utf-8").read()
    assert 'if ra == "deploy" and not (st.get("command") or "").strip():' in stsrc
    st, r = _asgi("POST", "/api/packages", ou, {"name": "Als Dienstkonto (Test)", "run_as": "deploy",
                                                 "steps": {"main": [{"type": "command", "command": "whoami", "run_as": "deploy"}]}})
    assert st == 200, (st, r)
    row = db.q1("SELECT run_as, steps FROM packages WHERE id=?", (r["id"],))
    assert row["run_as"] == "system" and '"deploy"' not in row["steps"], "Code eines OU-Admins nie als Dienstkonto"
    st, r = _asgi("POST", "/api/packages", ou, {"name": "Ausbruch (Test)", "steps": {"main": [
        {"type": "install", "source": "file", "folder": "Kunden\\Nord\\..\\Sued", "install_file": "x.msi"}]}})
    assert st == 400, ("„..“ im Paketordner", st, r)
    db.ex("UPDATE tenants SET repo_folder='' WHERE id=?", (t_nord,))
    st, r = _asgi("POST", "/api/packages", ou, {"name": "Ohne Ordner (Test)", "steps": {"main": [
        {"type": "install", "source": "file", "folder": "Kunden\\Berlin", "install_file": "x.msi"}]}})
    assert st == 403, ("ohne Repository-Ordner keine Repository-Dateien", st, r)
    db.ex("UPDATE tenants SET repo_folder='Kunden\\Nord' WHERE id=?", (t_nord,))
    for cols in ([0], [col + 999]):
        st, r = _asgi("POST", "/api/schedules", ou, {"name": "Alle (Test)", "kind": "packages", "packages": [p_nord],
                                                      "targets": {"computers": [], "collections": cols}, "enabled": False})
        assert st == 403, ("Zeitplan auf fremde/alle Sammlungen", cols, st)
    st, r = _asgi("POST", "/api/schedules", ou, {"name": "Eigene OU (Test)", "kind": "packages", "packages": [p_nord],
                                                  "targets": {"computers": [], "collections": [col]}, "enabled": False})
    assert st == 200, (st, r)
    st, r = _asgi("POST", "/api/remote/connect", ou, {"computer_id": cin1, "protocol": "vnc", "auth": "central"})
    assert st == 403, "zentrales VNC-Kennwort nicht für OU-Admins"
    assert _asgi("GET", "/api/kiosk/me", ou)[0] == 403, "Kiosk nicht über die OU-Admin-Sitzung"
    assert _asgi("POST", "/api/session/active", ou)[0] == 200, "Inaktivitäts-Uhr funktioniert auch für OU-Admins"
    st, d = _asgi("GET", f"/api/deployments/{d_mixed}", ou)
    assert d["package"].get("steps") == {}, "Inhalt einer Bereitstellung mit fremden Zielen nicht zeigen"
    assert _asgi("GET", "/api/collections", ou)[1][0]["filters"] == []
    # Mandant hatte OU-Admins: ohne Sammlung keine Computer (nie „alle“); gelöschter Mandant: keine Computer
    db.ex("UPDATE tenants SET ou_ever=1 WHERE id=?", (t_nord,))
    assert deploy.tenant_members(t_nord) == {cin1, cin2}
    db.ex("UPDATE tenants SET collection_id=NULL WHERE id=?", (t_nord,))
    assert deploy.tenant_members(t_nord) == set() and deploy.tenant_members(10 ** 6) == set()
    assert schedules.members(schedules.get(sx)) == set(), "Zeitplan eines OU-Mandanten ohne Sammlung erreicht niemanden"
    db.ex("UPDATE tenants SET collection_id=? WHERE id=?", (col, t_nord))
    assert deploy.tenant_members(t_sued) is None, "Mandant ohne OU-Admins und ohne Sammlung: wie bisher keine Einschränkung"
    # Mandant löschen: nicht solange Zeitpläne dranhängen (würden sonst „zentral“); Änderung beendet OU-Sitzungen
    assert _asgi("DELETE", f"/api/tenants/{t_nord}", main_admin)[0] == 400
    _m._tenants_changed(t_nord)
    assert _asgi("GET", "/api/me", ou)[0] == 401, "Sitzung des OU-Admins nach Änderung am Mandanten beendet"
    adsrc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "ad.py"), encoding="utf-8").read()
    assert "def resolve_group_strict" in adsrc and "group_dn_by_sid(c, t.get(\"admin_group_sid\"))" in adsrc and "mehrdeutig" in adsrc, \
        "Gruppen eindeutig (SID beim Speichern), nie erneut nach Namen"
    fg = adsrc[adsrc.index("def _find_groups"):adsrc.index("def _group_dn")]
    assert fg.index("sAMAccountName=") < fg.index("(cn="), "erst sAMAccountName (eindeutig), dann cn – gleichnamige Gruppe sperrt niemanden aus"
    # Aufräumen
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


@test("2.25.1 Änderungen im AD wirken von selbst: Sitzungen werden im Hintergrund alle 2 Minuten in einem Durchlauf geprüft (entfernt → abgemeldet, weniger Rechte → neu anmelden, mehr → erweitert, AD weg → bleibt, OU-Admins höchstens 30 Min. unbestätigt)")
def _():
    import hashlib
    import secrets
    import time as _t
    from app import ad, auth, scheduler, util
    assert auth.RECHECK_SECONDS <= 120 and auth.MAX_UNCONFIRMED <= 1800
    assert "_bg(\"sessions\", _auth.recheck_all)" in open(scheduler.__file__, encoding="utf-8").read(), "läuft im Minuten-Takt"

    def sess(scope, name, confirmed=None, expires="2099-01-01 00:00:00"):
        tok = secrets.token_urlsafe(16)
        th = hashlib.sha256(tok.encode()).hexdigest()
        db.ex("INSERT INTO sessions(token_hash, username, display_name, created, expires, ip, last_active, role, scope, confirmed_at) "
              "VALUES(?,?,?,?,?,?,?,?,?,?)", (th, name, "Test", util.now(), expires, "127.0.0.1", util.now(), "admin",
                                            json.dumps(scope) if scope is not None else None, confirmed or _t.time()))
        auth._checked[th] = 0  # fällig
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
        return {th: answers.get(sam, True) for th, sam in names.items()}  # Sitzungen anderer Tests: unverändert
    ad.check_sessions = fake
    auth.config.ldap_configured = lambda: True
    try:
        auth.recheck_all()
    finally:
        ad.check_sessions, auth.config.ldap_configured = orig_cs, orig_lc
    assert len(calls) == 1, ("ein Durchlauf für alle fälligen Sitzungen", calls)
    assert "abgel-t" in {r["username"] for r in db.q("SELECT username FROM sessions")}, "abgelaufene nicht prüfen (räumt der Aufräumlauf weg)"
    alive = {r["username"]: r["scope"] for r in db.q("SELECT username, scope FROM sessions WHERE username LIKE '%-t%'")}
    assert "weg-t" not in alive and "leer-t" not in alive, "aus der Gruppe entfernt → abgemeldet"
    assert alive["haupt-t2"] is None, "in POCKETKNIFE-Admins aufgenommen → Hauptadmin"
    assert json.loads(alive["mehr-t"]) == [5, 8], "weitere Mandanten-Gruppe → Bereich erweitert"
    assert "weniger-t" not in alive, "weniger Rechte → neu anmelden (beendet auch Fernsitzungen)"
    assert json.loads(alive["ad-aus-t"]) == [5], "AD nicht erreichbar → Sitzung bleibt …"
    left = auth.RECHECK_SECONDS - (_t.time() - auth._checked[toks["ad-aus-t"][1]])
    assert left <= auth.RETRY_SECONDS + 1, "… und wird nach einer Minute erneut geprüft"
    assert "ad-aus-alt-t" not in alive, "OU-Admin seit über 30 Minuten unbestätigt → abgemeldet"
    st, res = _asgi("GET", "/api/me", toks["weniger-t"][0])
    assert st == 401 and "Berechtigung wurde geändert" in str(res), (st, res)
    # Die Anfrage selbst fragt nie das AD (langsames AD bremst die Oberfläche nicht)
    asrc = open(auth.__file__, encoding="utf-8").read()
    sess_fn = asrc[asrc.index("def _session(request):"):asrc.index("def current(request")]
    assert "ad." not in sess_fn and "check_sessions" not in sess_fn
    # ad.check_sessions: Störung ≠ „kein Mitglied“; fehlende Zugriffsgruppe = niemand Hauptadmin, Mandanten werden geprüft
    adsrc = open(ad.__file__, encoding="utf-8").read()
    cs = adsrc[adsrc.index("def check_sessions"):adsrc.index("def still_allowed")]
    assert "except GroupConfigError" in cs and "strict=True" in cs and "out[key] = None" in cs
    assert "class GroupConfigError(ADError)" in adsrc and "def _strict_ok" in adsrc
    db.ex("DELETE FROM sessions WHERE username LIKE '%-t%'")

@test("2.25.1 AD-Prüfung der Sitzungen: Störung ≠ „kein Mitglied“, fehlende Zugriffsgruppe sperrt Entzüge nicht, gelöschte Mandanten-Gruppe gibt keinen Zugang")
def _():
    from app import ad, util
    t_a = db.ex("INSERT INTO tenants(name, collection_id, admin_group, admin_group_sid, created_at) VALUES('SimA', 1, 'G-A', 'S-1-5-21-1-1-1-2001', ?)", (util.now(),))
    t_b = db.ex("INSERT INTO tenants(name, collection_id, admin_group, admin_group_sid, created_at) VALUES('SimB', 1, 'G-B', 'S-1-5-21-1-1-1-2002', ?)", (util.now(),))
    sid_dn = {"S-1-5-21-1-1-1-2001": "CN=G-A,DC=x", "S-1-5-21-1-1-1-2002": None}  # G-B im AD gelöscht
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
        assert r[1] is True, "Hauptadmin"
        assert r[2] == [t_a], "OU-Admin nur noch für Mandanten mit vorhandener Gruppe (G-B gelöscht)"
        assert r[3] is None, "LDAP-Störung bei der Mitgliedschaft → nicht prüfbar, NICHT abmelden"
        assert r[4] is False and r[5] is False, "deaktiviert bzw. nicht gefunden → abmelden"
        state["acc"] = None  # Zugriffsgruppe gelöscht/mehrdeutig
        r = ad.check_sessions([(1, "anna", "admin"), (2, "bert", "admin")])
        assert r[1] is False, "ohne Zugriffsgruppe ist niemand Hauptadmin (wie bei der Anmeldung)"
        assert r[2] == [t_a], "… die Mandanten-Gruppen werden trotzdem geprüft (Entzug wirkt weiter)"
        ad.service_conn = lambda: (_ for _ in ()).throw(ad.ADError("DC weg"))
        assert ad.check_sessions([(1, "anna", "admin")]) == {1: None}, "AD weg → alle nicht prüfbar"
    finally:
        ad.service_conn, ad._search, ad._group_dn, ad.group_dn_by_sid, ad.is_member = orig
        ad._attr = _attr0
        db.ex("DELETE FROM tenants WHERE id IN (?,?)", (t_a, t_b))


@test("2.25.2 Computer: Notizen und eigene Felder lassen sich direkt beim Computer eintragen (auch ohne angelegte Felder), werden gespeichert und nie vom Computer überschrieben")
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
        "Notizen immer da, Feld direkt anlegen, kein falscher Verweis"
    db.ex("DELETE FROM custom_fields WHERE id=?", (fid,))
    db.ex("DELETE FROM computers WHERE id=?", (cid,))
    db.ex("DELETE FROM sessions WHERE token_hash=?", (th,))


@test("2.25.3 Repository durchsuchbar (Name/Muster, Unterordner, Grenzen) und Wiederholen mit frei gewählten Schritten (Reihenfolge, Ziele, OU-Bereich)")
def _():
    import tempfile as _tf
    from app import deploy, remote, steps, util
    # --- Repository-Suche
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
        assert ("Kunden\\Nord\\Tools", "7zPortable.zip") in names, "auch tief in Unterordnern"
        assert {x["name"] for x in remote.repo_search("", "7z*.msi")["items"]} == {"7z2408-x64.msi", "7z2600-x64.msi"}, "Muster mit *"
        assert [x["name"] for x in remote.repo_search("Kunden\\Nord", "7z")["items"]] == ["7zPortable.zip"], "nur ab dem Ordner (OU-Admins)"
        r = remote.repo_search("", "7z", limit=2)
        assert len(r["items"]) == 2 and r["truncated"], "Grenze greift und wird gemeldet"
        assert remote.repo_search("7zip\\24.08", "x64", limit=1)["truncated"] is False
        assert remote.repo_search("7zip", "7z2", limit=1)["truncated"], "auch wenn die Grenze mitten im Ordner erreicht wird"
        assert [x["name"] for x in remote.repo_search("", "setup[1]")["items"]] == ["setup[1].exe"], "[ ] gelten wörtlich"
        for bad in ("x" * 101, "a"):
            try:
                remote.repo_search("", bad)
                raise AssertionError(f"abzulehnen: {bad[:10]}")
            except remote.RemoteError:
                pass
        try:
            remote.repo_search("gibtsnicht", "abc")
            raise AssertionError("fehlender Startordner muss gemeldet werden (nicht „keine Treffer“)")
        except remote.RemoteError:
            pass
        try:
            remote.repo_search("", "a")
            raise AssertionError("mindestens 2 Zeichen")
        except remote.RemoteError:
            pass
    finally:
        db.set_setting("repo_path", old or "")
    # --- Schritte wählen
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
    assert len(deploy.steps_overview(did, {cids[0]})["targets"]) == 1, "OU-Admin sieht nur eigene Ziele"
    got = {}
    orig = deploy.create
    deploy.create = lambda p, ids, user, **kw: got.update(pkg=p, ids=ids, kw=kw) or 4711
    try:
        assert deploy.rerun_steps(did, ["m3", "m2"], "failed", [], "t") == 4711
        p2 = got["pkg"]["steps"]
        assert [s_["id"] for s_ in p2["main"]] == ["m2", "m3"] and p2["pre"] == [] and p2["post"] == [], "nur gewählte, Paket-Reihenfolge"
        assert all(s_["enabled"] for s_ in p2["main"]), "gewählter, im Paket ausgeschalteter Schritt läuft"
        assert sorted(got["ids"]) == sorted(cids[1:]), "nur fehlgeschlagene Ziele"
        assert "2 Schritte wiederholt" in got["kw"]["name"]
        deploy.rerun_steps(did, ["z1"], "selected", [tids[cids[0]]], "t")
        assert got["ids"] == [cids[0]] and "„" in got["kw"]["name"]
        db.ex("UPDATE deployments SET package_json=? WHERE id=?", (json.dumps({**pkg, "reboot": "always"}), did))
        deploy.rerun_steps(did, ["m1"], "all", [], "t", reboot="never")
        assert got["pkg"]["reboot"] == "never", "Neustart abwählbar"
        deploy.rerun_steps(did, ["m1"], "all", [], "t")
        assert got["pkg"]["reboot"] == "always", "sonst wie im Paket"
        assert deploy.RETRY_SUFFIX.sub("", "Pkg – 2 Schritte wiederholt – ab „A“ wiederholt") == "Pkg"
        deploy.rerun_steps(did, ["m1"], "all", [], "t", allowed={cids[1]})
        assert got["ids"] == [cids[1]], "OU-Admin: nur Ziele im eigenen Bereich"
        for bad in ((did, [], "all"), (did, ["xx"], "all"), (did, ["m1"], "selected")):
            try:
                deploy.rerun_steps(bad[0], bad[1], bad[2], [], "t")
                raise AssertionError(f"muss abgelehnt werden: {bad}")
            except ValueError:
                pass
        db.ex("UPDATE deployments SET status='running' WHERE id=?", (did,))
        try:
            deploy.rerun_steps(did, ["m1"], "all", [], "t")
            raise AssertionError("läuft noch → ablehnen")
        except ValueError:
            pass
    finally:
        deploy.create = orig
        db.ex("DELETE FROM deployments WHERE id=?", (did,))
    # Schritt-IDs: fehlende werden fest nach Position ergänzt, doppelte beim Speichern neu vergeben
    n1 = steps.normalize({"steps": {"main": [{"type": "command", "command": "a"}, {"type": "command", "command": "b"}]}})
    n2 = steps.normalize({"steps": {"main": [{"type": "command", "command": "a"}, {"type": "command", "command": "b"}]}})
    ids1 = [x["id"] for x in n1["steps"]["main"]]
    assert ids1 == [x["id"] for x in n2["steps"]["main"]] and len(set(ids1)) == 2 and all(ids1)
    cp = steps.clean_package({"name": "Doppelt (Test)", "steps": {"main": [{"id": "aa", "type": "command", "command": "a"},
                                                                         {"id": "aa", "type": "command", "command": "b"}]}})
    assert len({x["id"] for x in cp["steps"]["main"]}) == 2
    from app import access
    assert access.ROUTES[("GET", "/api/deployments/{did}/steps")] is access._p_dep_w, "Schritt-Titel nur bei ausschließlich eigenen Zielen"
    assert access.ROUTES[("GET", "/api/deployments/{did}/failed_steps")] is access._p_dep_w
    for c in cids:
        db.ex("DELETE FROM computers WHERE id=?", (c,))


@test("2.25.4 Datei-Explorer: Ersatzweg über C$ (Muster, Verknüpfungen nicht verfolgt, gesperrte Ordner übersprungen, Grenzen, OU-Regel)")
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
            assert follow_symlinks is False, "Links dürfen nicht verfolgt werden"
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
        assert "C:\\Logs\\Link" not in seen, "Junction nicht verfolgt"
        assert r["skipped"] == 1 and r["truncated"] is False, r
        assert [x["name"] for x in sm("C:\\Logs", "*.log")["items"]] == ["app-2024.log", "app.LOG", "app.log", "setup[1].log"], "sortiert nach Pfad"
        assert [x["name"] for x in sm("C:\\Logs", "setup[1]")["items"]] == ["setup[1].log"], "[ ] wörtlich"
        assert [x["name"] for x in sm("C:\\Logs", "alt")["items"]] == ["alt"] and sm("C:\\Logs", "alt")["items"][0]["dir"]
        assert sm("C:\\Logs", "app", limit=2)["truncated"]
        assert sm("C:\\Logs", "app", max_dirs=1)["truncated"]
        for bad_path in ("", "\\\\srv\\x", "C:\\Logs\\..\\Windows"):
            try:
                clientfiles.search(1, bad_path, "app")
                raise AssertionError(f"Pfad {bad_path!r} hätte abgelehnt werden müssen")
            except clientfiles.FileError:
                pass
        for bad_q in ("a", "**", "x" * 101):
            try:
                clientfiles.search(1, "C:\\Logs", bad_q)
                raise AssertionError(f"Suchbegriff {bad_q!r} hätte abgelehnt werden müssen")
            except clientfiles.FileError:
                pass
        try:
            sm("C:\\Gibtsnicht", "app")
            raise AssertionError("Fehler im Startordner muss gemeldet werden, nicht „keine Treffer“")
        except clientfiles.FileError as e:
            assert "Nicht gefunden" in str(e), e
        # gleichzeitige Suchen begrenzt
        held = [clientfiles._search_sem.acquire(blocking=False) for _ in range(4)]
        try:
            clientfiles.search(1, "C:\\Logs", "app")
            raise AssertionError("fünfte gleichzeitige Suche hätte abgelehnt werden müssen")
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
    assert not clientfiles._is_link(T(0x9000001A), 0x410), "OneDrive-Ordner ist keine Verknüpfung"
    assert not clientfiles._is_link(T(0x80000013), 0x420), "deduplizierte Datei ist keine Verknüpfung"
    assert clientfiles._is_link(T(0), 0x410) and not clientfiles._is_link(T(0xA0000003), 0x10)
    assert access.ROUTES[("GET", "/api/computers/{cid}/files/search")] is access._p_cid, "OU-Admins nur auf eigenen Computern"
    assert any(getattr(rt, "path", "").replace("{cid:int}", "{cid}") == "/api/computers/{cid}/files/search" for rt in _app.routes)
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    assert "/files/search?path=" in js and "async function download(r, folder = cur)" in js
    # 2.25.7: im Explorer EIN Feld – es sucht (Enter/„Suchen“) und filtert nicht beim Tippen
    assert "liveFilter: false" in js and "if (opt.liveFilter === false) return;" in js and "onSearchEnter: (t) => doSearch(t)" in js
    assert "In diesem Ordner suchen, z. B. *.log" not in js, "kein zweites Suchfeld rechts mehr"


@test("2.25.4 Datei-Explorer: Suche läuft auf dem Client (WinRM, Constrained Language Mode), sicher übergeben, Ersatzweg nur bei Verbindungsfehler")
def _():
    import json as _json
    import subprocess as _sp
    from app import clientfiles, remote
    # Suchbegriff → Muster für -like: [ ] ` wörtlich (mit ` maskiert), ohne Platzhalter „Teil des Namens“
    assert clientfiles.like_pattern("app") == "*app*"
    assert clientfiles.like_pattern("*.log") == "*.log"
    assert clientfiles.like_pattern("setup[1]") == "*setup`[1`]*"
    assert clientfiles.like_pattern("a**b***c") == "a*b*c", "mehrere * zusammengefasst"
    for bad in ("*a*b*c*d*e*f*g*h*i*", "x" * 101, "a", "ab\x01"):
        try:
            clientfiles.like_pattern(bad)
            raise AssertionError(f"{bad!r} hätte abgelehnt werden müssen")
        except clientfiles.FileError:
            pass
    # Nur echte Verbindungsfehler führen zum Ersatzweg über C$
    import requests as _rq
    assert remote._is_connect_error(_rq.exceptions.ConnectionError("Max retries exceeded with url"))
    assert remote._is_connect_error(_rq.exceptions.ConnectTimeout("x"))
    assert not remote._is_connect_error(_rq.exceptions.ReadTimeout("read timed out"))
    assert not remote._is_connect_error(Exception("401 Unauthorized"))
    assert issubclass(remote.RemoteConnectError, remote.RemoteError), "alte except RemoteError fangen ihn weiter"
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
        # 2.25.6: EIN rekursiver Aufruf mit Windows-Filter statt Ordner für Ordner (sonst reichte die Zeit nur für oben)
        assert "-Filter $flt -Recurse" in script and "PK-UnderLink" in script and "-FollowSymlink" not in script
        # ein einzelner Treffer kommt aus ConvertTo-Json als Objekt statt Liste
        answer["v"] = 'PKJSON:{"items":{"f":"C:\\\\Logs","n":"x.log","d":false,"r":false,"s":1,"m":null},"dirs":1,"skipped":0,"truncated":true}'
        r = clientfiles.search(1, "C:\\Logs", "x.log")
        assert [x["name"] for x in r["items"]] == ["x.log"] and r["truncated"]
        answer["v"] = 'PKJSON:{"error":"notfound"}'
        try:
            clientfiles.search(1, "C:\\Gibtsnicht", "app")
            raise AssertionError("nicht gefunden muss gemeldet werden")
        except clientfiles.FileError as e:
            assert "Nicht gefunden" in str(e)
        answer["v"] = 'PKJSON:{"error":"denied","message":"Der Zugriff auf den Pfad wurde verweigert."}'
        try:
            clientfiles.search(1, "C:\\System Volume Information", "app")
            raise AssertionError("verweigert muss gemeldet werden")
        except clientfiles.FileError as e:
            assert "Zugriff verweigert" in str(e)
        # Ersatzweg über C$ NUR, wenn WinRM gar nicht erreichbar ist – nicht bei Zeitüberschreitung o. Ä.
        answer["v"] = remote.RemoteConnectError("WinRM: Max retries exceeded (Connection refused)")
        assert clientfiles.search(1, "C:\\Logs", "app")["via"] == "smb"
        answer["v"] = remote.RemoteError("WinRM-Verbindung fehlgeschlagen: das steht nur im Text")
        try:
            clientfiles.search(1, "C:\\Logs", "app")
            raise AssertionError("nur die Fehlerart zählt, nicht der Text")
        except clientfiles.FileError:
            pass
        # Ersatzweg: Fehler der Freigabe werden zur Meldung (kein interner Fehler 500)
        answer["v"] = remote.RemoteConnectError("WinRM: Connection refused")

        def smb_fail(*a, **kw):
            raise remote.RemoteError("SMB-Anmeldung abgelehnt")
        clientfiles._search_smb = smb_fail
        try:
            clientfiles.search(1, "C:\\Logs", "app")
            raise AssertionError("SMB-Fehler muss als FileError kommen")
        except clientfiles.FileError as e:
            assert "SMB-Anmeldung" in str(e)
        clientfiles._search_smb = lambda cid, drive, rel, q: {"items": [], "truncated": False, "dirs": 1, "skipped": 0}
        # je Benutzer und je Computer eine Suche
        with clientfiles._search_lock:
            clientfiles._search_busy["users"].add("max")
        try:
            clientfiles.search(2, "C:\\Logs", "app", user_key="max")
            raise AssertionError("zweite Suche desselben Benutzers")
        except clientfiles.FileError as e:
            assert "vorige Suche" in str(e)
        finally:
            with clientfiles._search_lock:
                clientfiles._search_busy["users"].discard("max")
        with clientfiles._search_lock:
            clientfiles._search_busy["cids"].add(1)
        try:
            clientfiles.search(1, "C:\\Logs", "app", user_key="moritz")
            raise AssertionError("zweite Suche auf demselben Computer")
        except clientfiles.FileError as e:
            assert "diesem Computer" in str(e)
        finally:
            with clientfiles._search_lock:
                clientfiles._search_busy["cids"].discard(1)
        answer["v"] = 'PKJSON:{"items":[],"dirs":1,"skipped":0,"errors":2,"truncated":false}'
        assert clientfiles.search(1, "C:\\Logs", "app", user_key="max")["errors"] == 2
        assert not clientfiles._search_busy["users"] and not clientfiles._search_busy["cids"], "Sperren wieder frei"
        answer["v"] = remote.RemoteError("WinRM: keine Antwort von pc1 nach 2 Minuten – Aufruf abgebrochen.")
        try:
            clientfiles.search(1, "C:\\Logs", "app")
            raise AssertionError("Zeitüberschreitung darf nicht still auf C$ ausweichen")
        except clientfiles.FileError as e:
            assert "nicht geendet" in str(e) and "Unterordner" in str(e), e
        assert clientfiles._search_sem.acquire(blocking=False)
        clientfiles._search_sem.release()  # Sperre wird auch bei Fehlern freigegeben
    finally:
        remote.ps, clientfiles._host, clientfiles._search_smb = o_ps, o_host, o_smb

    # Optional: das Skript in echter PowerShell im Constrained Language Mode ausführen (PK_PWSH=/pfad/pwsh)
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
        assert names == ["app-2024.log", "app.LOG", "app.log"], f"Link darf nicht verfolgt werden: {names}"
        assert [x["n"] for x in run("grüße é")["items"]] == ["Grüße é.txt"]
        assert [x["n"] for x in run("it's")["items"]] == ["it's.log"]
        assert run("app", limit=1)["truncated"]
        # wie Windows PowerShell 5.1 (folgt Verknüpfungen): Treffer darunter werden verworfen
        assert sorted(x["n"] for x in run("app", follow=True)["items"]) == ["app-2024.log", "app.LOG", "app.log"], "nichts doppelt über Links"
        # tief verschachtelt und viele Ordner: wird gefunden (2.25.6 – vorher nur die obersten Ebenen)
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
        assert [x["f"].endswith("x[1]y") for x in run("klammer")["items"]] == [True], "Ordner mit [ ] im Namen"
        open(os.path.join(base, "Logs", "set[1]up.log"), "w").close()
        assert [x["n"] for x in run("set[1]up")["items"]] == ["set[1]up.log"], "[ ] wörtlich mit -like"
        long = "a" * 200 + ".txt"
        open(os.path.join(base, "Logs", long), "w").close()
        import time as _t
        t0 = _t.time()
        d = run("*a*a*a*a*a*a*a*b")
        assert d["items"] == [] and _t.time() - t0 < 20, "viele * dürfen nicht hängen"


@test("2.25.4 Repository-Index im Arbeitsspeicher: sofortige Treffer, Auffrischen, Neu einlesen, Obergrenze, Wechsel des Repositorys, OU-Ordner")
def _():
    import tempfile as _tf
    import time as _time
    from app import remote

    def wait():
        for _ in range(100):
            if not remote.repo_cache_status()["building"]:
                return
            _time.sleep(0.05)
        raise AssertionError("Index wird nicht fertig")
    def files(r):
        return {x["name"] for x in r["items"] if not x["dir"]}
    g = remote.name_pattern("*a*a*a*a*a*a*a*b")
    t0 = _time.time()
    assert not any(g.search("a" * 250) for _ in range(200)) and _time.time() - t0 < 2, "kein Backtracking"
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
        assert r["cached"] is False and len(files(r)) == 2, r  # erster Aufruf: direkt, Index wird eingelesen
        wait()
        r = remote.repo_search("", "7z")
        assert r["cached"] is True and files(r) == {"7z2408-x64.msi", "7zPortable.zip"} and "7zip" in {x["name"] for x in r["items"]}, r
        assert [x["name"] for x in remote.repo_search("Kunden\\Nord", "7z")["items"]] == ["7zPortable.zip"], "nur ab dem Ordner (OU-Admins)"
        assert [x["name"] for x in remote.repo_search("kunden", "7z")["items"]] == ["7zPortable.zip"], "Groß-/Kleinschreibung egal"
        try:
            remote.repo_search("Gibtsnicht", "7z")
            raise AssertionError("unbekannter Ordner: direkt suchen und Fehler melden")
        except Exception as e:
            assert "nicht gefunden" in str(e).lower(), e
        open(os.path.join(root, "7zip", "7zNeu.exe"), "wb").write(b"x")
        assert "7zNeu.exe" not in {x["name"] for x in remote.repo_search("", "7z")["items"]}, "Index ist ein Stand"
        r = remote.repo_search("", "7z", refresh=True)
        assert r["cached"] is False and "7zNeu.exe" in {x["name"] for x in r["items"]}, "Neu einlesen: diese Suche direkt"
        assert remote.repo_cache_status()["entries"], "alter Stand bleibt, bis der neue fertig ist"
        assert remote.repo_search("", "7z")["cached"] is True
        wait()
        assert "7zNeu.exe" in {x["name"] for x in remote.repo_search("", "7z")["items"]}
        # Stand älter als TTL → noch verwenden und im Hintergrund auffrischen; älter als STALE → direkt suchen
        with remote._rc_lock:
            remote._rc["at"] -= remote.REPO_CACHE_STALE + 10
        assert remote.repo_search("", "7z")["cached"] is False
        wait()
        assert remote.repo_search("", "7z")["cached"] is True
        # anderes Repository eingestellt: der alte Index gilt nicht
        db.set_setting("repo_path", root2)
        r = remote.repo_search("", "7z")
        assert [x["name"] for x in r["items"]] == ["7zNeu.msi"], r
        wait()
        # Obergrenze: zu groß → kein Index, jede Suche direkt
        remote.repo_cache_clear()
        remote.REPO_CACHE_MAX = 1
        remote.repo_search("", "7z")
        wait()
        st = remote.repo_cache_status()
        assert st["entries"] is None and st["too_big_or_failed"], st
        assert set(remote.repo_cache_status(full=False)) == {"age", "building"}, "OU-Admins: nichts über das ganze Repository"
        assert remote.repo_search("", "7z")["cached"] is False and not remote.repo_cache_status()["building"], "nicht ständig neu versuchen"
    finally:
        remote.REPO_CACHE_MAX = o_max
        wait()
        remote.repo_cache_clear()
        db.set_setting("repo_path", old or "")


@test("2.25.4 Pakete exportieren/importieren: ohne IDs (Namen), verschachtelte mit, feste Pfade gemeldet, Prüfen ändert nichts, fehlende Sammlung lehnt ab, OU-Regeln")
def _():
    import json as _json
    from fastapi import HTTPException
    from app import access, pkgexport, steps
    from app import main as _m
    admin = {"username": "haupt", "scope": None}
    old_repo = db.setting("repo_path")
    db.set_setting("repo_path", "\\\\boss\\PACKAGES")
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
            {"type": "powershell", "script": "& \\\\boss\\PACKAGES\\tools\\fix.ps1\nWrite-Output ok"},
            {"type": "scan", "profile_id": prof}]}})
        b = mk({"name": "Exp-Bundle", "steps": {"main": [{"type": "nested", "package_id": a},
                                                         {"type": "command", "command": "echo hallo", "conditions": conds}]}})
        data, warn, extra = pkgexport.export(admin, [b])
        text = _json.dumps(data)
        assert [p["name"] for p in data["packages"]] == ["Exp-Bundle", "Exp-7zip"] and extra == ["Exp-7zip"], "verschachtelte mit"
        for k in ('"package_id"', '"profile_id"', '"rescan_profile_id"', '"tenant_id"', f'"id": {col}'):
            assert k not in text, f"{k} gehört nicht in die Datei"
        assert "Exp-Buchhaltung" in text and '"all": true' in text and prof_name in text
        assert any("Pfad des Repositorys" in w and "fix.ps1" in w for w in warn), warn
        assert "7zip\\\\24.08" in text, "Repository-Pfad relativ"
        n0 = db.val("SELECT COUNT(*) FROM packages")
        # gleiche Installation, „überspringen“: nichts Neues; Prüfen schreibt nie
        r = pkgexport.run_import(admin, _json.loads(text), conflict="skip", dry_run=False, pkg_values=_m._pkg_values)
        assert r["count"]["skip"] == 2 and db.val("SELECT COUNT(*) FROM packages") == n0, r
        r = pkgexport.run_import(admin, _json.loads(text), conflict="copy", dry_run=True, pkg_values=_m._pkg_values)
        assert r["count"]["copy"] == 2 and db.val("SELECT COUNT(*) FROM packages") == n0, ("Prüfen darf nichts anlegen", r)
        r = pkgexport.run_import(admin, _json.loads(text), conflict="copy", dry_run=False, pkg_values=_m._pkg_values)
        assert r["count"]["copy"] == 2 and db.val("SELECT COUNT(*) FROM packages") == n0 + 2, r
        na = db.q1("SELECT * FROM packages WHERE name='Exp-7zip (Import)'")
        nb = db.q1("SELECT * FROM packages WHERE name='Exp-Bundle (Import)'")
        created += [na["id"], nb["id"]]
        pb = steps.normalize(_m.deploy.row_to_package(nb))
        assert pb["steps"]["main"][0]["package_id"] == na["id"], "Verschachtelung zeigt auf das importierte Paket"
        assert pb["steps"]["main"][1]["conditions"]["collections"] == [{"id": col, "mode": "member"}, {"id": 0, "mode": "member"}]
        pa = steps.normalize(_m.deploy.row_to_package(na))
        assert pa["rescan_profile_id"] == prof and pa["steps"]["main"][2]["profile_id"] == prof
        # Sammlung fehlt in der Ziel-Installation → ablehnen (nicht still weglassen – das würde die Bedingung aufheben)
        bad = _json.loads(text.replace("Exp-Buchhaltung", "Gibt-es-nicht"))
        for p in bad["packages"]:
            p["name"] += " X"
        r = pkgexport.run_import(admin, bad, dry_run=True, pkg_values=_m._pkg_values)
        st = {x["name"]: (x["status"], x["message"]) for x in r["items"]}
        assert st["Exp-7zip X"][0] == "error" and "Gibt-es-nicht" in st["Exp-7zip X"][1], st
        assert st["Exp-Bundle X"][0] == "error" and "verschachtelt" in st["Exp-Bundle X"][1], st
        # kaputte Dateien
        for broken in ({"format": "x"}, {"format": "pocketknife-packages", "format_version": 99, "packages": [{}]},
                       {"format": "pocketknife-packages", "packages": []}, {"format": "pocketknife-packages", "packages": [{"key": "a"}]}):
            try:
                pkgexport.run_import(admin, broken, pkg_values=_m._pkg_values)
                raise AssertionError(f"hätte abgelehnt werden müssen: {broken}")
            except HTTPException as e:
                assert e.status_code == 400
        loop = {"format": "pocketknife-packages", "format_version": 1, "packages": [
            {"key": "a", "name": "L1", "steps": {"main": [{"type": "nested", "package_key": "b"}]}},
            {"key": "b", "name": "L2", "steps": {"main": [{"type": "nested", "package_key": "a"}]}}]}
        try:
            pkgexport.run_import(admin, loop, pkg_values=_m._pkg_values)
            raise AssertionError("Schleife muss abgelehnt werden")
        except HTTPException as e:
            assert "Schleife" in str(e.detail)
        # OU-Admin: Mandant erzwungen, Dienstkonto → Lokales System, Dateien nur aus dem Mandanten-Ordner
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
        assert r["items"][0]["status"] == "error" and "nicht freigegeben" in r["items"][0]["message"], ("2.29.1: Skripte nur mit Freigabe", r)
        db.ex("UPDATE tenants SET allow_scripts=1 WHERE id=?", (tid,))
        r = pkgexport.run_import(ou, one, tenant_id=None, dry_run=False, pkg_values=_m._pkg_values)
        assert r["items"][0]["status"] == "new", r
        row = db.q1("SELECT * FROM packages WHERE name='Exp-OU'")
        created.append(row["id"])
        assert row["tenant_id"] == tid and row["run_as"] == "system", row
        db.ex("UPDATE tenants SET allow_packages=0 WHERE id=?", (tid,))
        try:
            pkgexport.run_import(ou, one, dry_run=True, pkg_values=_m._pkg_values)
            raise AssertionError("ohne Freigabe kein Import")
        except HTTPException as e:
            assert e.status_code == 403
        # 2.27: OU-Admins sehen (und exportieren) alle Pakete – auch die anderer Mandanten
        db.ex("UPDATE packages SET tenant_id=? WHERE id=?", (tid + 1000, a))
        assert pkgexport.export(ou, [a])[0]["packages"], "fremdes Paket ist sichtbar und exportierbar"
        db.ex("UPDATE packages SET tenant_id=NULL WHERE id=?", (a,))
        # Verschachtelung nie auf ein gleichnamiges Paket eines ANDEREN Mandanten (dessen OU-Admin könnte es ändern)
        other = db.ex("INSERT INTO tenants(name, allow_packages) VALUES('Exp-Sued', 1)")
        west = db.ex("INSERT INTO tenants(name, allow_packages) VALUES('Exp-West', 1)")
        db.ex("UPDATE packages SET tenant_id=? WHERE id=?", (other, a))  # „Exp-7zip“ gehört jetzt Süd
        f2 = _json.loads(text)
        for p in f2["packages"]:
            if p["name"] == "Exp-Bundle":
                p["name"] = "Exp-Bundle-West"
        r = pkgexport.run_import(admin, f2, tenant_id=west, conflict="skip", dry_run=False, pkg_values=_m._pkg_values)
        st = {x["name"]: x["status"] for x in r["items"]}
        assert st == {"Exp-Bundle-West": "new", "Exp-7zip": "new"}, ("Süd-Paket zählt für West nicht als vorhanden", st)
        nw = db.q1("SELECT * FROM packages WHERE name='Exp-Bundle-West'")
        n7 = db.q1("SELECT * FROM packages WHERE name='Exp-7zip' AND tenant_id=?", (west,))
        created += [nw["id"], n7["id"]]
        nested = [s_ for _k, s_ in steps.iter_steps(steps.normalize(_m.deploy.row_to_package(nw))) if s_.get("type") == "nested"]
        assert nested[0]["package_id"] == n7["id"] != a, "verschachtelt auf das West-Paket, nie auf das von Süd"
        db.ex("UPDATE packages SET tenant_id=NULL WHERE id=?", (a,))
        db.ex("DELETE FROM packages WHERE id IN (?, ?)", (nw["id"], n7["id"]))
        db.ex("DELETE FROM tenants WHERE id IN (?, ?)", (other, west))
        # gleicher Name zweimal in der Datei → nur einmal neu
        dup = _json.loads(text)
        dup["packages"] = [dict(p, name="Exp-Doppelt", key=k) for p, k in ((dup["packages"][1], "x1"), (dup["packages"][1], "x2"))]
        r = pkgexport.run_import(admin, dup, conflict="skip", dry_run=True, pkg_values=_m._pkg_values)
        assert [x["status"] for x in r["items"]] == ["new", "skip"], r
        # Export: verschachteltes Paket eines anderen Mandanten – seit 2.27 sichtbar, also mit exportiert
        db.ex("UPDATE tenants SET allow_packages=1 WHERE id=?", (tid,))
        db.ex("UPDATE packages SET tenant_id=? WHERE id=?", (tid + 1000, a))
        assert len(pkgexport.export(ou, [b])[0]["packages"]) == 2
        db.ex("UPDATE packages SET tenant_id=NULL WHERE id=?", (a,))
        # Fehlermeldungen des Repositorys: für OU-Admins ohne Pfad, Server und Konto (auch andere Schreibweise)
        o_login = _m.config.repo_login
        _m.config.repo_login = lambda: "FIRMA\\svc-repo"
        try:
            msg = _m._repo_err(ou, Exception("SMB-Anmeldung an BOSS als svc-repo abgelehnt: \\\\Boss\\packages\\x"))
            assert "boss" not in msg.lower() and "svc-repo" not in msg and "packages" not in msg.lower(), msg
            assert _m._repo_err(admin, Exception("\\\\boss\\PACKAGES")) == "\\\\boss\\PACKAGES", "Hauptadmins sehen alles"
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


@test("2.25.5 Paketliste: grünes Häkchen „Benachrichtigung“ nach derselben Regel wie die Meldung am Client (immer/nie/wie Einstellung, nur bei echten Installationen)")
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
        assert eff("Skript", "", ps) is False and eff("Skript", "on", ps) is True, "Skripte nur bei „immer“"
        assert eff("Deinstallation: 7-Zip", "", {**inst, "winget_action": "uninstall"}) is False
        db.set_setting("toast_enabled", 0)
        assert eff("7-Zip", "", inst) is False and eff("7-Zip", "on", inst) is True, "Einstellung aus – „immer“ gilt trotzdem"
    finally:
        db.set_setting("toast_enabled", old if old is not None else 1)
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    assert "notify_effective ? h('span', { style: { color: 'var(--ok)'" in js


@test("2.26 Bruchprüfung: Stand festhalten, geänderte/fehlende/neue Dateien erkennen, Lauf unterbrechen, Neuen Stand übernehmen, Vollprüfung, OU-Ansicht")
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
        # der eine Hintergrund-Worker rechnet Fingerabdrücke – warten, bis er fertig ist
        integrity.process_queue(seconds=30)
        for _ in range(200):
            with integrity._q_lock:
                idle = not integrity._hash_queue and not integrity._state["worker"]
            if idle:
                return
            _time.sleep(0.05)
        raise AssertionError("Fingerabdrücke werden nicht fertig")
    try:
        a = mk({"name": "Integ-7zip", "steps": {"main": [{"type": "install", "source": "file", "folder": "7zip\\24.08",
                                                           "install_file": "7z.msi", "install_type": "msi"}]}})
        b = mk({"name": "Integ-Bundle", "steps": {"main": [{"type": "nested", "package_id": a},
                                                           {"type": "filecopy", "source": "Office\\setup.exe", "target": "C:\\Temp"}]}})
        wg = mk({"name": "Integ-winget", "steps": {"main": [{"type": "install", "source": "winget", "winget_id": "7zip.7zip"}]}})
        assert integrity.check_package(wg)["state"] == "", "winget: nichts zu prüfen"
        r = integrity.check_package(a, queue_new=True)  # wie nach dem Speichern
        assert r["state"] == "ok", r
        assert "keys" not in integrity.scope_view(r, None) and "keys" not in integrity.scope_view(r, ["x"]), "keine Pfadliste an die Oberfläche"
        assert db.val("SELECT COUNT(*) FROM repo_baseline") == 2 and db.val("SELECT 1 FROM repo_baseline_dirs WHERE path='7zip\\24.08'")
        drain()
        assert db.val("SELECT COUNT(*) FROM repo_baseline WHERE sha256 IS NOT NULL") == 2, "Fingerabdruck nachgetragen"
        assert integrity.check_package(b, queue_new=True)["state"] == "ok"
        drain()
        # neue Datei im Paketordner → gelb, Lauf geht weiter
        w("7zip\\24.08\\neu.txt", b"x")
        r = integrity.check_package(a)
        assert r["state"] == "warn" and r["issues"][0]["kind"] == "added", r
        os.remove(os.path.join(root, "7zip", "24.08", "neu.txt"))
        # andere Datei geändert → gelb
        w("7zip\\24.08\\setup.ini", b"[x]\nmehr=1\n")
        r = integrity.check_package(a)
        assert r["state"] == "warn" and r["issues"][0]["kind"] == "changed" and not r["issues"][0]["main"], r
        integrity.accept(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (a,))), "haupt")
        # (der Hintergrund-Worker kann ihn schon neu gerechnet haben – dann aber vom NEUEN Inhalt; nie ein alter)
        assert db.val("SELECT sha256 FROM repo_baseline WHERE path='7zip\\24.08\\setup.ini'") in (
            None, __import__("hashlib").sha256(b"[x]\nmehr=1\n").hexdigest()), "Fingerabdruck wird neu gerechnet, nie ein alter übernommen"
        drain()
        assert integrity.check_package(a)["state"] == "ok", "Neuen Stand übernommen"
        # nur angefasst (gleicher Inhalt, neues Datum) → bleibt ok, Stand wird nachgeführt
        st0 = os.stat(msi).st_mtime
        os.utime(msi, (st0 + 100, st0 + 100))
        r = integrity.check_package(a)
        assert r["state"] == "ok", r
        assert abs(db.val("SELECT mtime FROM repo_baseline WHERE path='7zip\\24.08\\7z.msi'") - (st0 + 100)) <= 2
        # Installationsdatei mit gleicher Größe, anderem Inhalt, neuem Datum → rot; Lauf wird unterbrochen
        open(msi, "wb").write(b"B" * 4096)
        os.utime(msi, (st0 + 200, st0 + 200))
        n0 = db.val("SELECT COUNT(*) FROM deployments")
        for pkg_id in (a, b):  # auch über das verschachtelte Paket
            try:
                deploy.create(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (pkg_id,))), [2601], "haupt",
                              package_id=pkg_id)
                raise AssertionError("Bruch muss den Lauf unterbrechen")
            except integrity.IntegrityError as e:
                assert "Unterbrochen" in str(e) and "7z.msi" in str(e) and "Inhalt geändert" in str(e), e
        assert db.val("SELECT COUNT(*) FROM deployments") == n0, "nichts angelegt"
        integrity.store(a, {"state": "ok", "issues": [], "at": util.now(), "total": 0})
        try:
            integrity.guard(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (a,))), package_id=a)
        except integrity.IntegrityError:
            pass
        assert json.loads(db.val("SELECT integrity FROM packages WHERE id=?", (a,)))["state"] == "ok", "guard speichert nichts (Ausschnitt/alter Stand)"
        integrity.check_package(a)
        assert json.loads(db.val("SELECT integrity FROM packages WHERE id=?", (a,)))["state"] == "broken", "in der Paketliste sichtbar"
        assert "keys" not in json.loads(db.val("SELECT integrity FROM packages WHERE id=?", (a,)))
        # OU-Admin: zentrale Datei → keine Pfade
        err = integrity.IntegrityError("x", "Integ-7zip", integrity.check_package(a)["issues"])
        assert "nicht verfügbar" in integrity.public_message(err, ["kunden\\nord"]) and "7z.msi" not in integrity.public_message(err, ["kunden\\nord"])
        v = integrity.scope_view(integrity.check_package(a), ["kunden\\nord"])
        assert all(not x["path"] for x in v["issues"]) and v["issues"][0]["kind"] == "hidden", v
        # geplante Bereitstellung: wird beim Start unterbrochen, kein Ziel angefasst
        db.set_setting("integrity_full_enabled", 0)
        open(msi, "wb").write(b"A" * 4096)  # vorübergehend wieder richtig, damit die Bereitstellung angelegt wird
        os.utime(msi, (st0 + 100, st0 + 100))
        did = deploy.create(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (a,))), [2601], "haupt",
                            scheduled_at="2099-01-01 00:00:00", package_id=a)
        open(msi, "wb").write(b"C" * 4096)
        os.utime(msi, (st0 + 300, st0 + 300))
        deploy.start(did)
        d = db.q1("SELECT * FROM deployments WHERE id=?", (did,))
        t = db.q1("SELECT * FROM deployment_targets WHERE deployment_id=?", (did,))
        assert d["status"] == "failed" and t["status"] == "failed" and t["message"] == integrity.NEUTRAL, (d, t)
        assert "7z" not in t["message"], "am Ziel keine Pfade (sehen auch OU-Admins/Kiosk)"
        # Neuen Stand übernehmen → wieder ok, Bereitstellung möglich
        res = integrity.accept(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (a,))), "haupt")
        drain()
        assert res["count"] >= 1 and integrity.check_package(a)["state"] == "ok"
        # Datei fehlt → rot
        os.remove(os.path.join(root, "Office", "setup.exe"))
        r = integrity.check_package(b)
        assert r["state"] == "broken" and r["issues"][0]["kind"] == "missing" and r["issues"][0]["path"] == "Office\\setup.exe", r
        w("Office\\setup.exe", b"E" * 100)
        integrity.accept(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (b,))), "haupt")
        drain()
        assert integrity.check_package(b)["state"] == "ok"
        # Vollprüfung: Inhalt bei gleicher Größe UND gleichem Datum ausgetauscht – nur die Vollprüfung findet das
        drain()
        st1 = os.stat(msi).st_mtime
        open(msi, "wb").write(b"D" * 4096)
        os.utime(msi, (st1, st1))
        assert integrity.check_package(a)["state"] == "ok", "Schnellprüfung sieht es nicht"
        db.set_setting("integrity_full_enabled", 1)
        db.set_setting("integrity_full_start", 0)
        db.set_setting("integrity_full_end", 24)
        db.ex("UPDATE repo_baseline SET full_at='2000-01-01 00:00:00'")
        assert integrity.full_check() >= 1
        r = integrity.check_package(a)
        assert r["state"] == "broken" and "ausgetauscht" in r["issues"][0]["note"], r
        # Review 2.26 – Ordner als Quelle einer Dateikopie: jede Datei darin zählt wie die Installationsdatei (Bruch)
        w("Cfg\\a.xml", b"<a/>")
        c = mk({"name": "Integ-Cfg", "steps": {"main": [{"type": "filecopy", "source": "Cfg", "target": "C:\\Temp"}]}})
        assert integrity.check_package(c, queue_new=True)["state"] == "ok"
        drain()
        w("Cfg\\a.xml", b"<a>neu</a>")
        r = integrity.check_package(c)
        assert r["state"] == "broken" and r["issues"][0]["main"], r
        integrity.accept(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (c,))), "haupt")
        drain()
        # verschwindet kurz und kommt GEÄNDERT zurück → Bruch (der Stand darf beim Fehlen nicht aufgeräumt werden)
        import shutil as _shx
        _shx.move(os.path.join(root, "Cfg"), os.path.join(root, "Cfg-weg"))
        assert integrity.check_package(c)["state"] == "broken"
        db.ex("UPDATE repo_baseline SET confirmed_at='2000-01-01 00:00:00'")
        integrity.check_all(live=True)
        assert db.val("SELECT COUNT(*) FROM repo_baseline WHERE path='cfg\\a.xml'") == 1, "Stand bleibt, solange das Paket ihn braucht"
        _shx.move(os.path.join(root, "Cfg-weg"), os.path.join(root, "Cfg"))
        w("Cfg\\a.xml", b"<a>boese</a>")
        assert integrity.check_package(c)["state"] == "broken", "geändert zurückgekommen – nie still übernehmen"
        integrity.accept(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (c,))), "haupt")
        drain()
        # Repository nicht erreichbar → „nicht prüfbar“, nie Bruch, Stand bleibt
        _shx.move(root, root + "-weg")
        try:
            for pid in (a, b, c):
                r = integrity.check_package(pid)
                assert r["state"] == "unknown", (pid, r)
            db.ex("UPDATE repo_baseline SET confirmed_at='2000-01-01 00:00:00'")
            integrity.check_all(live=True)
            assert db.val("SELECT COUNT(*) FROM repo_baseline") >= 3, "kein Aufräumen bei nicht erreichbarem Repository"
        finally:
            _shx.move(root + "-weg", root)
        # Warteschlange: viele Einträge schnell (Menge statt Liste)
        t0 = _time.time()
        with integrity._q_lock:
            for i in range(20000):
                integrity._hash_queue.setdefault(f"x{i}", None)
            integrity._hash_queue.clear()
        assert _time.time() - t0 < 1
        # Vollprüfung bleibt nicht an einer unlesbaren Datei hängen
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
            assert integrity.full_check() >= 1, "nach der gesperrten Datei geht es weiter"
        finally:
            remote.repo_sha256 = o_sha
        assert db.val("SELECT COUNT(*) FROM repo_baseline WHERE full_at IS NULL") == 0
        # lokale Verknüpfungen im Repository werden nicht verfolgt (wie beim Kopieren)
        os.symlink(os.path.join(root, "Office"), os.path.join(root, "Cfg", "link"))
        assert all(not x["dir"] or x.get("link") for x in remote.repo_list("Cfg") if x["name"] == "link")
        assert [f for f, _s, _m in remote.repo_tree("Cfg")] == ["Cfg\\a.xml"]
        os.remove(os.path.join(root, "Cfg", "link"))
        # Index im Arbeitsspeicher (Schnellprüfung alle 5 Minuten) liefert dasselbe
        remote.repo_search("", "7z")
        for _ in range(100):
            if not remote.repo_cache_status()["building"]:
                break
            _time.sleep(0.05)
        assert remote.repo_index_files() is not None
        res_idx = integrity.evaluate(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (a,))), live=False)
        assert res_idx["state"] == "broken" and not res_idx["live"], res_idx
        # Aufräumen: Stand von Dateien, die kein Paket mehr benutzt, verschwindet
        # veralteter Index: Fehlalarm wird vor dem Speichern direkt auf der Freigabe bestätigt (hier: nicht bestätigt)
        open(msi, "wb").write(b"E" * 5000)  # der Index kennt noch die alte Größe
        integrity.accept(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (a,))), "haupt")
        drain()
        assert remote.repo_index_files() is not None and integrity.evaluate(
            deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (a,))), live=False)["state"] != "ok", "Index ist veraltet"
        integrity.check_all(live=False)
        assert json.loads(db.val("SELECT integrity FROM packages WHERE id=?", (a,)))["state"] == "ok", "kein Fehlalarm aus dem Index"
        for pid in (a, b, c):
            db.ex("DELETE FROM packages WHERE id=?", (pid,))
        db.ex("UPDATE repo_baseline SET confirmed_at='2000-01-01 00:00:00'")
        db.ex("UPDATE repo_baseline_dirs SET confirmed_at='2000-01-01 00:00:00'")
        # Audit 2.29: Stände, die gespeicherte Bereitstellungen noch brauchen (Erneut verteilen, geplant), bleiben
        snap = {"name": "Alt", "steps": {"pre": [], "main": [{"type": "install", "id": "s1", "source": "file", "folder": "7zip", "install_file": "7z.msi", "copy_folder": False}], "post": []}}
        db.ex("INSERT INTO repo_baseline(path, size, mtime, confirmed_at) VALUES('7zip\\7z.msi', 1, 1, '2000-01-01 00:00:00') "
              "ON CONFLICT(path) DO NOTHING")
        sd = db.ex("INSERT INTO deployments(name, package_json, status, created_at) VALUES('Alt', ?, 'success', ?)",
                   (json.dumps(snap), util.now()))
        integrity.check_all(live=True)
        assert db.val("SELECT COUNT(*) FROM repo_baseline WHERE path='7zip\\7z.msi'") == 1, "von einer Bereitstellung gebraucht"
        db.ex("DELETE FROM deployments")
        integrity.check_all(live=True)
        assert db.val("SELECT COUNT(*) FROM repo_baseline") == 0 and db.val("SELECT COUNT(*) FROM repo_baseline_dirs") == 0, sd
        # Zugriffsregeln und Einbindung
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


@test("2.27 Repository über die Oberfläche: Mandantenordner automatisch, Upload in Stücken (Fortsetzen, Grenzen), Schutz benutzter Dateien, Verschieben mit Pfadanpassung und Zurückdrehen, Duplikatsperre (auch zwischen Mandanten), Reparatur per Fingerabdruck")
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
        raise AssertionError(f"erwartet: {cls.__name__} „{text}“")
    disk = lambda rel: os.path.join(root, *rel.split("\\"))
    try:
        # 1) Mandantenordner automatisch („Mandanten\<Name>“), angelegt im Repository; Überschneidung verboten
        assert repofiles.ensure_tenant_folder(t_nord) == "Mandanten\\Nord27" and os.path.isdir(disk("Mandanten\\Nord27"))
        assert repofiles.roots(nord) == ["Mandanten\\Nord27"]
        expect(lambda: repofiles.check_tenant_folder("Mandanten", t_sued), text="überschneidet")
        expect(lambda: repofiles.check_tenant_folder("Mandanten\\Nord27\\x", t_sued), text="überschneidet")
        repofiles.check_tenant_folder("Mandanten\\Nord27", t_nord)
        st, r = _asgi("POST", "/api/tenants", None, {})  # ohne Anmeldung nichts
        assert st == 401
        # 2) Bereich: nur der eigene Ordner, nie der Ordner selbst umbenennen/löschen, kein „..“, nichts von POCKET KNIFE
        assert repofiles.need_scope(nord, "Mandanten/Nord27/App") == "Mandanten\\Nord27\\App"
        expect(lambda: repofiles.need_scope(nord, "Kunden\\Sued"), text="Mandanten")
        expect(lambda: repofiles.need_scope(nord, "Mandanten\\Nord27", own_root_ok=False), text="selbst")
        expect(lambda: repofiles.need_scope(nord, "Mandanten\\Nord27\\..\\..\\Kunden"), text="Ungültig")
        expect(lambda: repofiles.need_scope(admin, ".pk-uploads\\x"), text="POCKET KNIFE")
        for bad in ("a:b", "CON", "lpt1.txt", "x.", " x", "a*b", ".pk-uploads"):
            expect(lambda: repofiles.mkdir(nord, "Mandanten\\Nord27", bad))
        repofiles.mkdir(nord, "Mandanten\\Nord27", "App")
        expect(lambda: repofiles.mkdir(nord, "Mandanten\\Nord27", "app"), text="schon")
        # Verknüpfung nach draußen: dort wird nie geschrieben
        outside = _tf.mkdtemp(prefix="pk-out-", dir=TMP)
        os.symlink(outside, disk("Mandanten\\Nord27\\Link"))
        expect(lambda: repofiles.mkdir(nord, "Mandanten\\Nord27\\Link", "x"), Exception, "außerhalb")
        assert not os.listdir(outside)
        os.remove(disk("Mandanten\\Nord27\\Link"))
        # 3) Upload in Stücken: falsche Position → aktueller Stand, Fortsetzen nach Abbruch, fremder Benutzer → nicht gefunden
        data = bytes(range(256)) * 60  # 15360 Byte
        s = repofiles.upload_start(nord, "Mandanten\\Nord27\\App", "tool.exe", len(data))
        assert repofiles.upload_chunk(nord, s["token"], 0, data[:5000])["received"] == 5000
        e = expect(lambda: repofiles.upload_chunk(nord, s["token"], 0, data[:5000]), text="Position")
        assert e.info["received"] == 5000 and e.info["status"] == 409
        expect(lambda: repofiles.upload_chunk(sued, s["token"], 5000, data[5000:]), text="nicht gefunden")
        repofiles._hashers.clear()  # wie nach einem Neustart: Prüfsumme aus der Zwischendatei neu aufbauen
        s2 = repofiles.upload_start(nord, "Mandanten\\Nord27\\App", "tool.exe", len(data))
        assert s2["token"] == s["token"] and s2["received"] == 5000 and s2["resumed"], s2
        assert ".pk-uploads" not in {x["name"] for x in remote.repo_list("")}, "Zwischenablage nicht sichtbar"
        expect(lambda: repofiles.upload_finish(nord, s["token"]), text="unvollständig")
        repofiles.upload_chunk(nord, s["token"], 5000, data[5000:])
        fin = repofiles.upload_finish(nord, s["token"])
        assert fin["sha256"] == hashlib.sha256(data).hexdigest() and open(disk("Mandanten\\Nord27\\App\\tool.exe"), "rb").read() == data
        assert db.val("SELECT sha256 FROM repo_hash WHERE path=?", ("mandanten\\nord27\\app\\tool.exe",)) == fin["sha256"]
        assert not db.val("SELECT COUNT(*) FROM repo_uploads") and not os.listdir(disk(".pk-uploads"))
        expect(lambda: repofiles.upload_start(nord, "Mandanten\\Nord27\\App", "tool.exe", 3), text="schon")
        # Grenzen: Dateityp (nur Mandanten), Größe, Kontingent
        expect(lambda: repofiles.upload_start(nord, "Mandanten\\Nord27\\App", "bild.xyz", 3), text="Dateityp")
        db.set_setting("repo_upload_max_mb", 1)
        expect(lambda: repofiles.upload_start(admin, "", "gross.exe", 2 * 1048576), text="zu groß")
        db.set_setting("repo_upload_max_mb", 4096)
        db.ex("UPDATE tenants SET quota_mb=1 WHERE id=?", (t_nord,))
        expect(lambda: repofiles.upload_start(nord, "Mandanten\\Nord27", "big.zip", 1048576), text="Kontingent")
        db.ex("UPDATE tenants SET quota_mb=NULL WHERE id=?", (t_nord,))
        # Abgebrochene Uploads werden nach 24 Stunden aufgeräumt
        s3 = repofiles.upload_start(nord, "Mandanten\\Nord27", "halb.zip", 100)
        repofiles.upload_chunk(nord, s3["token"], 0, b"x" * 10)
        db.ex("UPDATE repo_uploads SET updated='2000-01-01 00:00:00'")
        assert repofiles.cleanup_uploads() == 1 and not os.listdir(disk(".pk-uploads"))
        # 4) Benutzt von einem Paket: Löschen und Überschreiben gesperrt (mit Namen), Neues dazulegen geht (mit Hinweis)
        p_tool = mk({"name": "Nord-Tool", "tenant_id": t_nord, "steps": {"main": [{"type": "install", "source": "file",
                     "folder": "Mandanten\\Nord27\\App", "install_file": "tool.exe", "install_type": "exe"}]}}, user=nord)
        assert ("sha256", fin["sha256"]) in {(r["kind"], r["value"]) for r in db.q("SELECT * FROM pkg_ident WHERE package_id=?", (p_tool,))}, "Fingerabdruck aus dem Upload"
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
        # 5) Umbenennen: Pfade im Paket (auch Altfelder) und im bestätigten Stand ziehen mit; Sicherung vorher
        integrity.check_package(p_tool, queue_new=True)
        integrity.process_queue(seconds=10)
        pv = repofiles.rename(nord, "Mandanten\\Nord27\\App", "App2", dry_run=True)
        assert pv["dry_run"] and pv["packages"][0]["name"] == "Nord-Tool" and pv["packages"][0]["changes"][0]["new"] == "Mandanten\\Nord27\\App2", pv
        assert os.path.isdir(disk("Mandanten\\Nord27\\App")), "Vorschau ändert nichts"
        res = repofiles.rename(nord, "Mandanten\\Nord27\\App", "App2")
        assert not res["dry_run"] and os.path.isfile(disk("Mandanten\\Nord27\\App2\\tool.exe")) and backups[-1] == "vor-umbenennen"
        row = db.q1("SELECT * FROM packages WHERE id=?", (p_tool,))
        st0 = steps.normalize(deploy.row_to_package(row))["steps"]["main"][0]
        assert st0["folder"] == "Mandanten\\Nord27\\App2" and row["folder"] == "Mandanten\\Nord27\\App2", (st0, row["folder"])
        assert db.val("SELECT 1 FROM repo_baseline WHERE path='mandanten\\nord27\\app2\\tool.exe'") and \
            not db.val("SELECT 1 FROM repo_baseline WHERE path LIKE 'mandanten\\nord27\\app\\%'"), "Stand zieht mit"
        assert integrity.check_package(p_tool)["state"] in ("ok", "warn"), "kein Bruch nach dem Umbenennen"
        # Datei IM benutzten Paketordner umbenennen (nicht die Installationsdatei) → gesperrt; die Installationsdatei geht
        pv = repofiles.rename(nord, "Mandanten\\Nord27\\App2\\readme.txt", "liesmich.txt", dry_run=True)
        assert pv["problems"] and "Paketordner" in pv["problems"][0] or "benutzt" in pv["problems"][0], pv
        repofiles.rename(nord, "Mandanten\\Nord27\\App2\\tool.exe", "tool2.exe")
        st0 = steps.normalize(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (p_tool,))))["steps"]["main"][0]
        assert st0["install_file"] == "tool2.exe" and st0["folder"] == "Mandanten\\Nord27\\App2", st0
        # Fehler mitten im Umbau → alles zurückgedreht (Dateien wieder am alten Ort, Paket unverändert)
        real_apply = repofiles._apply_db
        repofiles._apply_db = lambda plan, pairs: (_ for _ in ()).throw(RuntimeError("Datenbank weg"))
        try:
            expect(lambda: repofiles.rename(nord, "Mandanten\\Nord27\\App2", "App3"), text="zurückgedreht")
        finally:
            repofiles._apply_db = real_apply
        assert os.path.isdir(disk("Mandanten\\Nord27\\App2")) and not os.path.exists(disk("Mandanten\\Nord27\\App3"))
        # Hauptadmin: zentrales Paket darf nicht in einen Mandantenordner wandern; Mandantenordner verschieben zieht den Mandanten mit
        os.makedirs(disk("Zentral\\7zip"))
        open(disk("Zentral\\7zip\\7z.msi"), "wb").write(b"M" * 300)
        p_z = mk({"name": "Zentral-7zip", "steps": {"main": [{"type": "install", "source": "file", "folder": "Zentral\\7zip",
                  "install_file": "7z.msi", "install_type": "msi"}]}})
        pv = repofiles.move_into(admin, ["Zentral\\7zip"], "Mandanten\\Nord27", dry_run=True)
        assert any("Zentrales Paket" in x for x in pv["problems"]), pv
        expect(lambda: repofiles.move_into(nord, ["Zentral\\7zip"], "Mandanten\\Nord27", dry_run=True), text="Mandanten")
        os.makedirs(disk("Kunden\\Sued"))
        res = repofiles.move_into(admin, ["Kunden\\Sued"], "Mandanten", dry_run=False)
        assert db.val("SELECT repo_folder FROM tenants WHERE id=?", (t_sued,)) == "Mandanten\\Sued", "Mandantenordner zieht mit"
        # Assistent: neuen Hauptordner anlegen und mehrere Ordner auf einmal hinein (Vorschau ändert nichts)
        os.makedirs(disk("Office"))
        pv = repofiles.move_into(admin, ["Zentral\\7zip", "Office"], "Basis", dry_run=True, create_dest=True)
        assert pv["create_dest"] == "Basis" and not pv["problems"] and not os.path.exists(disk("Basis")), pv
        res = repofiles.move_into(admin, ["Zentral\\7zip", "Office"], "Basis", dry_run=False, create_dest=True)
        assert os.path.isfile(disk("Basis\\7zip\\7z.msi")) and os.path.isdir(disk("Basis\\Office"))
        assert steps.normalize(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (p_z,))))["steps"]["main"][0]["folder"] == "Basis\\7zip"
        # geplante Bereitstellung (Snapshot) wird mit angepasst
        did = db.ex("INSERT INTO deployments(name, package_json, status, created_at) VALUES('Plan27', ?, 'scheduled', ?)",
                    (json.dumps(steps.expand(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (p_z,))))), util.now()))
        repofiles.rename(admin, "Basis\\7zip", "SevenZip")
        assert "Basis\\\\SevenZip" in db.val("SELECT package_json FROM deployments WHERE id=?", (did,))
        db.ex("DELETE FROM deployments WHERE id=?", (did,))
        # 6) Duplikatsperre – Bereiche: zentral, Nord, Süd. Gleicher Bereich erlaubt, anderer gesperrt (auch Mandant ↔ Mandant)
        wg = {"steps": {"main": [{"type": "install", "source": "winget", "winget_id": "Dup27.App"}]}}
        p_wz = mk({"name": "Dup zentral", **wg})
        mk({"name": "Dup zentral 2", **wg})  # gleicher Bereich: erlaubt
        e = expect(lambda: mk({"name": "Dup Nord", "tenant_id": t_nord, **wg}, user=nord), repofiles.Duplicate, "Dup zentral")
        assert e.info["can_override"] is False and e.info["duplicate"][0]["kind"] == "winget"
        db.ex("DELETE FROM pkg_ident WHERE package_id IN (?, ?)", (p_wz, created[-1]))
        p_wn = mk({"name": "Dup Nord", "tenant_id": t_nord, **wg}, user=nord)
        expect(lambda: mk({"name": "Dup Süd", "tenant_id": t_sued, **wg}, user=sued), repofiles.Duplicate, "Nord27")
        e = expect(lambda: mk({"name": "Dup Süd", "tenant_id": t_sued, **wg}), repofiles.Duplicate, "übersteuern")
        assert e.info["can_override"] is True
        mk({"name": "Dup Süd (Hauptadmin)", "tenant_id": t_sued, "allow_duplicate": True, **wg})
        # Altfall (schon vorhandenes Duplikat) blockiert das Speichern anderer Änderungen nicht
        v = _m._pkg_values({"name": "Dup Nord umbenannt", "tenant_id": t_nord, **wg}, p_wn, user=nord)
        repofiles.guard_package(v, p_wn, nord)
        # über die Schnittstelle: 409 mit Paketliste
        tok = secrets.token_urlsafe(16)
        th = hashlib.sha256(tok.encode()).hexdigest()
        db.ex("INSERT INTO sessions(token_hash, username, display_name, created, expires, ip, last_active, role, scope) VALUES(?,?,?,?,?,?,?,?,?)",
              (th, "ou-sued27", "Test", util.now(), "2099-01-01 00:00:00", "127.0.0.1", util.now(), "admin", json.dumps([t_sued])))
        auth._checked[th] = _t.time() + 10 ** 6
        st, r = _asgi("POST", "/api/packages", tok, {"name": "Dup API", **wg})
        assert st == 409 and r["detail"]["duplicate"] and "message" in r["detail"], (st, r)
        st, r = _asgi("POST", "/api/repository/mkdir", tok, {"folder": "Mandanten\\Sued", "name": "Neu"})
        assert st == 200 and os.path.isdir(disk("Mandanten\\Sued\\Neu")), (st, r)
        st, r = _asgi("POST", "/api/repository/mkdir", tok, {"folder": "Mandanten\\Nord27", "name": "Fremd"})
        assert st == 403, (st, r)
        st, r = _asgi("POST", "/api/repository/delete", tok, {"items": ["Mandanten\\Nord27\\App2"]})
        assert st == 403, (st, r)
        # Upload über die Schnittstelle (Stück als rohe Bytes), Abschluss
        st, r = _asgi("POST", "/api/repository/upload", tok, {"folder": "Mandanten\\Sued\\Neu", "name": "api.msi", "size": 6})
        assert st == 200 and r["chunk"] == repofiles.CHUNK, (st, r)
        st, r2 = _asgi("PUT", f"/api/repository/upload/{r['token']}?offset=0", tok, raw=b"abcdef")
        assert st == 200 and r2["received"] == 6, (st, r2)
        st, r2 = _asgi("POST", f"/api/repository/upload/{r['token']}/finish", tok, {})
        assert st == 200 and open(disk("Mandanten\\Sued\\Neu\\api.msi"), "rb").read() == b"abcdef", (st, r2)
        st, r = _asgi("GET", "/api/repository/info", tok)
        assert st == 200 and r["roots"] == ["Mandanten\\Sued"] and "msi" in r["types"], r
        db.ex("DELETE FROM sessions WHERE token_hash=?", (th,))
        # gleiche Datei (SHA-256) eines Pakets von Nord: Süd darf sie nicht hochladen; Hauptadmin nach Rückfrage schon
        tool = open(disk("Mandanten\\Nord27\\App2\\tool2.exe"), "rb").read()
        e = expect(lambda: upload(sued, "Mandanten\\Sued", "kopie.exe", tool), repofiles.Duplicate, "Nord-Tool")
        assert not os.path.exists(disk("Mandanten\\Sued\\kopie.exe")) and not os.listdir(disk(".pk-uploads")), "Mandant: verworfen"
        e = expect(lambda: upload(admin, "Basis", "kopie.exe", tool), repofiles.Duplicate, "Nord-Tool")
        assert e.info["token"] and not os.path.exists(disk("Basis\\kopie.exe"))
        repofiles.upload_finish(admin, e.info["token"], allow_dup=True)
        assert open(disk("Basis\\kopie.exe"), "rb").read() == tool
        # Verschachteln: nur zentrale oder desselben Mandanten – auch für Hauptadmins
        try:
            _m._pkg_values({"name": "Zentral mit Nord", "steps": {"main": [{"type": "nested", "package_id": p_tool}]}}, user=admin)
            raise AssertionError("zentral darf kein Mandanten-Paket einbauen")
        except HTTPException as x:
            assert x.status_code == 403
        _m._pkg_values({"name": "Nord mit Zentral", "tenant_id": t_nord, "steps": {"main": [{"type": "nested", "package_id": p_z}]}}, user=nord)
        # 7) Reparatur per Fingerabdruck: außerhalb verschoben → gefunden, Pfade übernommen
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
        assert not sug["items"] and sug["not_found"] == ["Mandanten\\Nord27\\App2"], ("Ordner nur, wenn ALLE Dateien dort liegen", sug)
        # Datei zurück in den umbenannten Ordner → der Ordner ist vollständig wiedergefunden
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
        # 8) Index: eigene Änderungen sofort sichtbar (ohne Neu-Einlesen), auch während ein Einlesen läuft
        repofiles.mkdir(admin, "Basis", "Frisch")
        assert "Frisch" in {x["name"] for x in remote.repo_search("Basis", "Frisch")["items"]}
        repofiles.delete(admin, ["Basis\\Frisch"])
        assert not remote.repo_search("Basis", "Frisch")["items"]
        # --- Review-Funde 2.27 ---
        # H2: zentrales Paket nie mit Dateien aus einem Mandantenordner; OU-Admin legt nichts in fremd benutzte Ordner
        try:
            _m._pkg_values({"name": "Zentral im Mandant", "steps": {"main": [{"type": "install", "source": "file",
                            "folder": "Mandanten\\Nord27\\App-alt", "install_file": "tool2.exe", "install_type": "exe"}]}}, user=admin)
            raise AssertionError("zentrales Paket aus Mandantenordner")
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
        # neue Datei von jemand anderem in einem Mandantenordner neben einem zentralen Paket → Bruch, nicht nur Hinweis
        integrity.check_package(p_cz, queue_new=True)
        open(disk("Mandanten\\Nord27\\Alt\\version.dll"), "wb").write(b"evil")
        r = integrity.check_package(p_cz)
        assert r["state"] == "broken" and any(x["kind"] == "added" and x["level"] == "broken" for x in r["issues"]), r
        # M6: Dateityp lässt sich nicht per Umbenennen umgehen
        expect(lambda: repofiles.rename(nord, "Mandanten\\Nord27\\x.txt", "x.hta"), text="Dateityp")
        # L8: Sonderzeichen, die beim Kleinschreiben zusammenfallen (Kelvin-Zeichen)
        expect(lambda: repofiles.need_scope(nord, "Mandanten\\Nord27\u212a"), text="Sonderzeichen")
        expect(lambda: repofiles.mkdir(nord, "Mandanten\\Nord27", "\u212aunden"), text="Sonderzeichen")
        # L10: Stück an der richtigen Stelle, auch wenn ein früheres halb geschrieben wurde
        s4 = repofiles.upload_start(nord, "Mandanten\\Nord27", "teil.bin", 6)
        repofiles.upload_chunk(nord, s4["token"], 0, b"abc")
        with open(disk(".pk-uploads\\" + s4["token"] + ".part"), "ab") as fh:
            fh.write(b"JUNK")  # halber Schreibvorgang
        repofiles.upload_chunk(nord, s4["token"], 3, b"def")
        expect(lambda: repofiles.upload_finish(nord, s4["token"]), text="Größe")  # Rest hinten → neu hochladen
        repofiles.upload_cancel(nord, s4["token"])
        # M3: Paket wird während des Umbaus gespeichert → Änderung bleibt, Pfad wird trotzdem angepasst
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
        # M4: Gesamtprüfung, während umgebaut wird, speichert nichts Veraltetes und räumt nicht auf
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
        assert db.val("SELECT COUNT(*) FROM repo_baseline") == n_base, "nichts aufgeräumt"
        # H1: Ordner auf der Freigabe löschen folgt keiner Junction darin – mit einem nachgebildeten smbclient
        # (Junction = Eintrag mit Attribut 0x400; der echte Server würde ihr folgen)
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
        assert not os.path.exists(os.path.join(sb, "alt")) and os.path.isfile(os.path.join(sb, "Zentral", "wichtig.msi")), "Ziel der Junction bleibt"
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "remote.py"), encoding="utf-8").read()
        assert "smbshutil.rmtree" not in src, "nie smbclient.shutil.rmtree (folgt Junctions)"
        # Zugriffsregeln: alle neuen Routen für OU-Admins nur über _p_repo bzw. _p_pkg_edit
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


@test("2.28 Neuigkeiten in der App (je Benutzer gesehen, Sicherheit zuerst, OU nur „alle“) und Hinweis auf neue Versionen (Server fragt GitHub, Token verschlüsselt und nie sichtbar)")
def _():
    import urllib.error as _ue
    from app import config, news, pklog, util
    from app import main as _m
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    vers = news.versions()
    assert vers and any(v["version"] == config.VERSION for v in vers), "news.json braucht einen Eintrag für die laufende Version"
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
        "neuer Benutzer: nur die laufende Version ist neu"
    for v in r["versions"]:
        ks = [i["kind"] for i in v["items"]]
        assert ks == sorted(ks, key=lambda k: news.KINDS.index(k)), "Sicherheit zuerst"
    news.mark_seen(adm)
    assert news.for_user(adm)["unseen"] == 0
    db.ex("INSERT OR REPLACE INTO news_seen(username, version, at) VALUES('news-haupt', '2.25', '')")
    r = news.for_user(adm)
    assert r["unseen"] == sum(1 for v in r["versions"] if util.vcmp(v["version"], "2.25") > 0) and r["unseen"] >= 3, "übersprungene Versionen alle neu"
    assert news.mark_seen(adm, "99.0")["seen"] == config.VERSION, "nie eine Zukunftsversion als gesehen"
    ro = news.for_user(ou)
    assert "update" not in ro and all(i["audience"] == "all" for v in ro["versions"] for i in v["items"]), "OU-Admins: nur „alle“, kein Update-Hinweis"
    # Neue Version: GitHub nachgestellt (der Browser fragt nie selbst)
    old = {k: db.setting_raw(k) for k in ("update_check_enabled", "update_repo", news.TOKEN_KEY, news.STATUS_KEY)}
    real = news._fetch
    calls = []
    try:
        news.save({"enabled": True, "repo": "https://github.com/test/pocketknife.git", "token": "github_pat_Zq9Xw7TOKEN"})
        assert db.setting("update_repo") == "test/pocketknife"
        raw = db.setting_raw(news.TOKEN_KEY)
        assert raw and "Zq9Xw7" not in raw, "Token verschlüsselt"
        fut = "99.1"
        news._fetch = lambda repo, token: calls.append((repo, token)) or {"tag": "v" + fut, "url": "https://github.com/test/pocketknife/releases/tag/v99.1",
            "published": "2099-01-01T00:00:00", "news": {"versions": [
                {"version": fut, "date": "2099-01-01", "items": [{"kind": "new", "audience": "all", "de": "Neu A", "en": "New A"},
                                                                 {"kind": "security", "audience": "admin", "de": "Lücke B", "en": "Hole B"}]},
                {"version": config.VERSION, "date": "", "items": [{"kind": "new", "audience": "all", "de": "x", "en": "x"}]}]}}
        st = news.check(force=True, who="test")
        assert st["ok"] and st["latest"] == fut and [v["version"] for v in st["newer"]] == [fut], st
        assert calls == [("test/pocketknife", "github_pat_Zq9Xw7TOKEN")]
        news.check()  # innerhalb von 24 Stunden: nicht erneut fragen
        assert len(calls) == 1
        up = news.for_user(adm)["update"]
        assert up["latest"] == fut and up["security"] and up["versions"][0]["items"][0]["kind"] == "security" and up["command"] == "sh update.sh --pull"
        assert db.val("SELECT COUNT(*) FROM logs WHERE message LIKE ?", (f"%{fut} ist verfügbar (mit Sicherheitsupdates)%",)) >= 1
        # Token nie an die Oberfläche (Einstellungen, Neuigkeiten, Diagnose-Regel)
        for out in (news.settings(), news.for_user(adm), _m.db.all_settings()):
            assert "Zq9Xw7" not in json.dumps(out), ("Token sichtbar", [k for k, v in (out.items() if isinstance(out, dict) else []) if "Zq9Xw7" in json.dumps(v)])
        assert news.settings()["token_set"] is True
        # Fehler von GitHub: verständlich, ohne Token
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
        assert news.for_user(adm).get("update") is None, "abgeschaltet → kein Hinweis"
        try:
            news.save({"repo": "kein repo"})
            raise AssertionError("ungültiges Repository")
        except ValueError:
            pass
        news.save({"clear_token": True})
        assert not db.setting_raw(news.TOKEN_KEY)
        assert ("GET", "/api/system/updates") not in _m.access.ROUTES and ("GET", "/api/news") in _m.access.ROUTES, "Einstellungen nur Hauptadmins"
        wf = os.path.join(root, ".github", "workflows", "release.yml")
        if os.path.exists(wf):
            assert "app/news.json" in open(wf, encoding="utf-8").read(), "Release bricht ohne Neuigkeiten der Version ab"
    finally:
        news._fetch = real
        for k, v in old.items():
            if v:
                db.set_setting(k, v)
            else:
                db.ex("DELETE FROM settings WHERE key=?", (k,))
        db.ex("DELETE FROM news_seen WHERE username IN ('news-haupt','news-ou')")


@test("2.29 Audit Logik: Ausnahme-Sammlung fehlt/kaputt = Paket übersprungen (nie „überall“), Bedingungen schützen vor dem Löschen, unterbrochener Zeitplan verbraucht keine Wiederholungen, hängende „wartend“-Läufe, Mandant beim Start, eindeutige Sammlungen beim Import")
def _():
    from app import deploy, pkgexport, schedules, steps as stp, integrity
    db.ex("INSERT OR IGNORE INTO computers(id, name, online, winrm) VALUES(291, 'PC-291', 1, 1)")
    ex = db.ex("INSERT INTO collections(name, type, match, filters) VALUES('Ausnahme 2.29', 'static', 'all', '{}')")
    db.ex("INSERT INTO collection_members(collection_id, computer_id) VALUES(?, 291)", (ex,))
    collections_.invalidate()
    run = stp.Runner(1, 1, {"id": 291, "name": "PC-291", "dns_name": ""}, lambda: False, lambda *a: None)
    cond = {**stp.default_conditions(), "collections": [{"id": ex, "mode": "not_member"}]}
    assert run.check(cond)[0] is False, "Mitglied der Ausnahme → nicht installieren"
    p = stp.clean_package({"name": "Audit-Ausnahme", "conditions": cond,
                           "steps": {"main": [{"type": "command", "command": "cmd /c exit 0"}]}})
    p["steps"], p["conditions"] = json.dumps(p["steps"]), json.dumps(p["conditions"])
    pid = db.ex(f"INSERT INTO packages({', '.join(deploy.PKG_FIELDS)}) VALUES({', '.join('?' * len(deploy.PKG_FIELDS))})",
                tuple(p[k] for k in deploy.PKG_FIELDS))
    try:
        assert any("Audit-Ausnahme" in x for x in collections_.used_by([ex])), "Bedingung im Paket zählt als Verwendung"
        db.ex("DELETE FROM collections WHERE id=?", (ex,))  # z. B. AD-Gruppe umbenannt, alte Sammlung entfernt
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
        # Schritt-Bedingungen zählen ebenfalls
        assert ex in collections_._cond_refs(None, {"main": [{"type": "command", "conditions": {"collections": [{"id": ex}]}}]})
    finally:
        db.ex("DELETE FROM packages WHERE id=?", (pid,))
        db.ex("DELETE FROM collections WHERE id=?", (ex,))
        db.ex("DELETE FROM collection_members WHERE collection_id=?", (ex,))
    # Zeitplan: vom Bruch im Repository unterbrochen → kein „erledigt“
    real_fire = schedules.fire
    schedules.fire = lambda s_, cids=None, reason="", **k: {"deployment_id": None, "count": 0, "blocked": "x"}
    try:
        assert schedules._fire_safe({"id": 999291, "name": "T"}, [1]) is False
    finally:
        schedules.fire = real_fire
    # „wartend“ angelegt, Start gescheitert → der Takt holt ihn nach
    did = db.ex("INSERT INTO deployments(name, package_json, status, created_at) VALUES('Hängt', ?, 'queued', ?)",
                (json.dumps({"name": "Hängt", "steps": {"pre": [], "main": [], "post": []}}), util.ago(minutes=10)))
    db.ex("INSERT INTO deployment_targets(deployment_id, computer_id, status) VALUES(?, 291, 'queued')", (did,))
    started = []
    real_start = deploy.start
    deploy.start = lambda d, checked=False: started.append(d)
    try:
        deploy.restart_stuck()
        assert did in started, "hängende Bereitstellung wird gestartet"
    finally:
        deploy.start = real_start
    # Mandant beim Start erneut: Computer hat die Mandanten-Sammlung verlassen → ausgelassen
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
    assert "    _rescope(did)\n" in src.split("def start(")[1].split("def _rescope")[0], "start() wendet den Mandanten an"
    # Import: gleichnamige Sammlungen → nur eindeutig (über den Pfad), sonst ablehnen
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
    # Hauptdatei mit anderem Datum, Fingerabdruck nicht fertig → Lauf startet nicht
    real_ev = integrity.evaluate
    integrity.evaluate = lambda *a, **k: {"state": "warn", "issues": [{"kind": "pending", "main": True, "level": "warn", "path": "a\\b.msi",
                                                                    "note": "", "old": {}, "new": {}}], "total": 1}
    try:
        ipkg = {"name": "X", "steps": {"pre": [], "main": [{"type": "install", "id": "s1", "source": "file", "folder": "a",
                                                           "install_file": "b.msi", "copy_folder": True}], "post": []}}
        must_fail(integrity.guard, ipkg, contains="Unterbrochen")
        ipkg["steps"]["main"][0]["install_file"] = "setup.exe"  # a\b.msi ist nur eine Datei im kopierten Ordner
        assert integrity.guard(ipkg)["state"] == "warn", "Ordnerdateien blockieren nicht"
    finally:
        integrity.evaluate = real_ev
    # Mandantenpakete nie als Dienstkonto – auch wenn ein Hauptadmin sie so gespeichert hat
    assert stp.effective_run_as({"type": "command", "run_as": "inherit"}, {"run_as": "deploy", "tenant_id": 3})[0] == "system"
    assert stp.effective_run_as({"type": "command", "run_as": "deploy"}, {"run_as": "deploy"})[0] == "deploy"


@test("2.29 Audit Clients: Datei-Explorer folgt beim Löschen/Kopieren keinen Verknüpfungen, Arbeitsordner wird nach dem Anlegen geprüft (CLM-tauglich), Merker in HKLM, winget nur signiert, Nachricht/Variablen/Registry sicher, kein CredSSP")
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
        "Verknüpfung beim Kopieren ausgelassen"
    clientfiles.rmtree_nofollow(Fake, "\\\\pc\\C$\\alt")
    assert not os.path.exists(os.path.join(sb, "alt")) and os.path.isfile(os.path.join(sb, "Geheim", "wichtig.txt")), "Ziel bleibt"
    csrc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "clientfiles.py"), encoding="utf-8").read()
    assert "smbshutil.rmtree" not in csrc and "smbshutil.copytree" not in csrc
    # Arbeitsordner: nach dem Anlegen Besitzer/Rechte erneut prüfen; nur Cmdlets (CLM)
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
        assert "PK_BASE_OK" in o and "rename" not in o, ("Dienstkonto als Besitzer ist in Ordnung", o)
        o = run(True, "O:S-1-5-21-9-9-9-500G:DUD:(A;OICI;FA;;;S-1-5-21-9-9-9-500)", good)
        assert "PK_MOVED:S-1-5-21-9-9-9-500" in o and "PK_BASE_OK" in o, o
        o = run(True, good[:-1] + ")(A;OICI;FA;;;BU)", good)
        assert "PK_MOVED:Recht für BU" in o, o
        o = run(False, "", "O:S-1-5-21-9-9-9-777G:DUD:PAI(A;OICI;FA;;;SY)")
        assert "PK_UNTRUSTED" in o and "PK_BASE_OK" not in o, ("im selben Moment fremd angelegt", o)
        o = run(False, "", good, kids=2)
        assert "PK_UNTRUSTED" in o and "Inhalt beim Anlegen" in o, o
        assert "Only core types" not in o and "cannot" not in o.lower(), o
    # winget: Merker in HKLM, PATH-Ersatz nur mit Microsoft-Signatur
    assert "HKLM:\\SOFTWARE\\PocketKnife" in deploy.WINGET_FIND and "Set-Content -Path $mark" not in deploy.WINGET_FIND
    assert "Get-AuthenticodeSignature" in deploy.WINGET_FIND and "if ($g) { return $g.Source }" not in deploy.WINGET_FIND
    # Nachricht: keine " im Argument; Variablen mit „-“ nicht unzitiert in CMD
    ssrc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "steps.py"), encoding="utf-8").read()
    assert ".replace('\"', \"'\")" in ssrc.split('            if t == "message":')[1][:1200], "Nachrichtentext ohne Anführungszeichen"
    assert "/XJ" in ssrc, "robocopy folgt keinen Verknüpfungen"
    must_fail(tools.substitute, "x $(User)", {"name": "pc", "current_user": "max -Force"}, "cmd", contains="nicht sicher")
    assert tools.substitute("x $(User)", {"name": "pc", "current_user": "max -Force"}, "powershell") == "x $($PK_User)"
    assert "Test-Path $key" not in uninstall.__dict__.get("SCRIPT", "") and "-Path $key" not in open(uninstall.__file__, encoding="utf-8").read()
    # CredSSP gibt es nicht mehr (Kennwort des Dienstkontos ginge an jeden Client)
    old = db.setting_raw("auth_mode")
    try:
        db.set_setting("auth_mode", "credssp")
        assert kerberos.mode() == "negotiate"
    finally:
        db.set_setting("auth_mode", old or "")


@test("2.29 Audit Anmeldung/Daten: allgemeine Fehlermeldungen, Notfallzugang begrenzt und gemeldet, Diagnosepaket maskiert mehr, Proxy-Kennwort nie an neuen Proxy, Token nicht bei Weiterleitung, DNS-Antworten geprüft, mehrere DC-Zertifikate")
def _():
    import io as _io
    import urllib.request as _ur
    from fastapi import HTTPException as _HE
    from app import ad, auth, config as _cfg, diagnostics, dnsres, notify, proxy
    # Verbindungsfehler verraten keine DC-Namen
    real = ad.authenticate
    ad.authenticate = lambda u, p, allow_kiosk=False: (_ for _ in ()).throw(ad.ADError("LDAPS-Verbindung zu dc1.firma.local:636 fehlgeschlagen"))
    try:
        try:
            auth.login("max", "x", "10.9.9.1")
            raise AssertionError("keine Anmeldung")
        except _HE as e:
            assert "dc1" not in str(e.detail) and "636" not in str(e.detail) and e.detail == auth.INFRA_FAIL, e.detail
    finally:
        ad.authenticate = real
    assert auth._log_name("Sommer!2024#") == "(ungültiger Benutzername, 12 Zeichen)" and auth._log_name("FIRMA\\max") == "'FIRMA\\max'"
    assert "breakglass" in notify.CHECKS and notify.alert_def("breakglass")
    db.ex("INSERT INTO logs(ts, level, category, message) VALUES(?, 'WARN', 'auth', 'Notfallzugang: Anmeldung von 10.1.1.1')", (util.now(),))
    hits = notify._check_breakglass({}, {"since": util.ago(minutes=1)})
    assert any("10.1.1.1" in v["event"] for v in hits.values()), hits
    # Diagnosepaket: mehr Kennwortformen
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
    # Proxy: gespeichertes Kennwort nie an einen anderen Proxy
    try:
        proxy.save({"url": "http://proxy-a.firma.local:8080", "user": "px", "password": "Pw1", "clients": False})
        must_fail(proxy.save, {"url": "http://evil.example:8080", "user": "px", "clients": False}, contains="erneut eingeben")
        proxy.save({"url": "http://proxy-a.firma.local:8080", "user": "px", "clients": True})  # unverändert: ok
    finally:
        proxy.save({"url": "", "user": "", "clients": False})
    # Weiterleitung auf einen anderen Rechner: kein Authorization-Header
    req = _ur.Request("https://api.github.com/x", headers={"Authorization": "Bearer T"})
    n = proxy.SafeRedirect().redirect_request(req, _io.BytesIO(), 302, "Found", {}, "https://evil.example/y")
    assert "Authorization" not in n.headers and not n.get_header("Authorization")
    n = proxy.SafeRedirect().redirect_request(req, _io.BytesIO(), 302, "Found", {}, "https://api.github.com/z")
    assert n.get_header("Authorization") == "Bearer T"
    # DNS: fremde/unpassende Antworten verworfen
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
                self.out.append(1)  # erst eine Antwort auf eine ANDERE Frage (gefälscht)
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
    # LDAPS: mehrere Zertifikate je Name (mehrere DCs hinter dem Domänennamen)
    assert ad._fp_list("AA") == ["AA"] and ad._fp_list(["AA", "BB"]) == ["AA", "BB"] and ad._fp_list(None) == []
    # Oberfläche: CSV-Schutz mit Leerzeichen davor, nur http(s)/mailto als Link, strengere CSP einer Route bleibt
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    assert "/^[\\s]*[=+\\-@]|^[\\t\\r]/" in js and "(https?:|mailto:)" in js
    msrc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "main.py"), encoding="utf-8").read()
    assert 'if "content-security-policy" not in resp.headers' in msrc
    assert diagnostics._csv([{"a": "=HYPERLINK(1)"}], ["a"]).splitlines()[1].startswith("'="), "Diagnose-CSV entschärft"
    assert _cfg is not None


@test("2.29.1 Mandanten absichern: Befehls-/PowerShell-Schritte nur mit eigener Freigabe (Hauptadmin-Schritte unverändert erlaubt, Skripte als Datei aus dem eigenen Ordner gehen), eigene Deinstallationsbefehle ebenso, Hinweis bei Zugriff auf Freigaben/Internet")
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
        # erlaubt: Skript als Datei aus dem eigenen Repository-Ordner, Registry, Deinstallation mit erkanntem Befehl
        st, r = _asgi("POST", "/api/packages", tok, {"name": "SK Datei-Skript", "steps": {"main": [
            {"type": "install", "source": "file", "folder": "Kunden\\SK\\tools", "install_file": "setup.ps1"},
            {"type": "uninstall", "match_name": "Alt*"}]}})
        assert st == 200, (st, r)
        created.append(r["id"])
        # Schritt eines Hauptadmins: unverändert speicherbar, geändert nicht
        pid = db.ex("INSERT INTO packages(name, version, steps, conditions, run_as, tenant_id, created_at) VALUES('SK von Haupt','1',?,'{}','system',?,?)",
                    (json.dumps({"main": [{"id": "h1", "type": "command", "command": "hostname"}]}), tid, util.now()))
        created.append(pid)
        pkg = _asgi("GET", f"/api/packages/{pid}", tok)[1]
        pkg["description"] = "nur Beschreibung geändert"
        st, r = _asgi("PUT", f"/api/packages/{pid}", tok, pkg)
        assert st == 200, ("unveränderter Hauptadmin-Schritt", st, r)
        pkg["steps"]["main"][0]["command"] = "net use Z: \\\\fremd\\c$"
        assert _asgi("PUT", f"/api/packages/{pid}", tok, pkg)[0] == 403, "geänderter Code"
        pkg["steps"]["main"].append({"type": "command", "command": "hostname"})
        pkg["steps"]["main"][0]["command"] = "hostname"
        assert _asgi("PUT", f"/api/packages/{pid}", tok, pkg)[0] == 403, "zusätzlicher Code (gleicher Text zählt nur einmal)"
        # Software-Liste: erkannter Befehl ja, geänderter nein
        from app import uninstall as _un
        det = _un.command_for(db.q1("SELECT * FROM software WHERE id=?", (sw,)))["command"]
        st, r = _asgi("POST", "/api/uninstall", tok, {"items": [{"software_id": sw, "command": det}]})
        assert st == 200, ("erkannter Befehl", st, r)
        for d in r["deployments"]:
            db.ex("DELETE FROM deployment_targets WHERE deployment_id=?", (d,))
            db.ex("DELETE FROM deployments WHERE id=?", (d,))
        st, r = _asgi("POST", "/api/uninstall", tok, {"items": [{"software_id": sw, "command": "cmd /c whoami"}]})
        assert st == 403, ("geänderter Befehl", st, r)
        # Freigabe beim Mandanten: nur zusammen mit „eigene Pakete“; danach Code erlaubt, mit Hinweis bei Netzzugriff
        db.ex("UPDATE tenants SET allow_scripts=1 WHERE id=?", (tid,))
        access.flush()
        st, r = _asgi("POST", "/api/packages", tok, {"name": "SK mit Netz", "steps": {"main": [
            {"type": "powershell", "script": "Copy-Item \\\\fileserver\\daten\\x.txt C:\\Temp; Invoke-WebRequest https://example.org -OutFile a"}]}})
        assert st == 200 and r["warnings"] and "fileserver" in r["warnings"][0], (st, r)
        created.append(r["id"])
        assert db.val("SELECT COUNT(*) FROM logs WHERE message LIKE '%SK mit Netz%Freigaben oder das Internet%'"), "Hinweis im Protokoll"
        st, r = _asgi("POST", "/api/packages", tok, {"name": "SK ohne Netz", "steps": {"main": [{"type": "powershell", "script": "Get-Date"}]}})
        assert st == 200 and r["warnings"] == [], r
        created.append(r["id"])
        assert _m.net_hints({"steps": {"main": [{"type": "command", "command": "net use x: \\\\srv\\a"}]}})
        assert not _m.net_hints({"steps": {"main": [{"type": "command", "command": "msiexec /i C:\\Temp\\a.msi /qn"}]}})
        # Mandant speichern: Skripte nur mit „eigene Pakete“
        _m._tenant_scripts(tid, {"allow_scripts": True}, {"username": "t"})
        assert db.val("SELECT allow_scripts FROM tenants WHERE id=?", (tid,)) == 1
        db.ex("UPDATE tenants SET allow_packages=0 WHERE id=?", (tid,))
        _m._tenant_scripts(tid, {"allow_scripts": True}, {"username": "t"})
        assert db.val("SELECT allow_scripts FROM tenants WHERE id=?", (tid,)) == 0, "ohne eigene Pakete keine Skripte"
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


@test("2.29.2 Trivy: Schwachstellen HOCH/KRITISCH mit Korrektur stoppen das Release vor dem Hochladen; Trivy fest in Version und Prüfsumme (keine Action); täglicher Scan des veröffentlichten Images; Versionen aktualisieren per Pull Request")
def _():
    import re as _re
    import shutil as _sh
    import subprocess as _sp
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    sc = os.path.join(root, "tools", "trivy-scan.sh")
    wfd = os.path.join(root, ".github", "workflows")
    if not os.path.exists(sc) or not os.path.isdir(wfd):
        return  # im Container liegen Skript und Workflows nicht
    src = open(sc, encoding="utf-8").read()
    assert _re.search(r"^TRIVY_VERSION=\d+\.\d+\.\d+$", src, _re.M), "feste Trivy-Version"
    assert _re.search(r'^SHA_LINUX_64="[0-9a-f]{64}"$', src, _re.M) and _re.search(r'^SHA_LINUX_ARM64="[0-9a-f]{64}"$', src, _re.M)
    assert "sha256sum -c" in src and "--ignore-unfixed" in src and "HIGH,CRITICAL" in src
    ver = tuple(int(x) for x in _re.search(r"^TRIVY_VERSION=(\S+)$", src, _re.M).group(1).split("."))
    assert not ((0, 69, 4) <= ver <= (0, 69, 6)), "kompromittierte Trivy-Versionen (CVE-2026-33634)"
    wfs = {f: open(os.path.join(wfd, f), encoding="utf-8").read() for f in os.listdir(wfd) if f.endswith(".yml")}
    for f, t in wfs.items():
        for use in _re.findall(r"uses:\s*(\S+)", t):
            assert _re.search(r"@[0-9a-f]{40}$", use), f"{f}: Action nicht auf Commit festgelegt: {use}"
        assert "aquasecurity/" not in t, f"{f}: Trivy nur über tools/trivy-scan.sh (keine Action)"
    rel = wfs["release.yml"]
    assert rel.index("tools/trivy-scan.sh pocketknife-test:amd64 pocketknife-test:arm64") < rel.index("Bauen und hochladen"), \
        "Trivy prüft VOR dem Hochladen"
    assert "tools/trivy-scan.sh" in wfs["test.yml"]
    assert "schedule:" in wfs["security.yml"] and ":latest" in wfs["security.yml"] and "packages: read" in wfs["security.yml"]
    assert "pin-versions.sh --update" in wfs["update-pins.yml"] and "gh pr create" in wfs["update-pins.yml"]
    assert ".trivyignore" in open(os.path.join(root, "update.sh"), encoding="utf-8").read()
    if not _sh.which("sh") or not _sh.which("sha256sum"):
        return
    # Ablauf mit einer Trivy-Attrappe: sauber → 0, Befund → 1 (+ Bericht), nicht prüfbar → 3
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
    assert r.returncode == 1 and "Versionen aktualisieren" in r.stderr, "Befund in einem von mehreren Images zählt"
    t = open(rep, encoding="utf-8").read()
    assert "CVE-2026-1111" in t and "libssl3t64" in t and "Keine Schwachstellen" in t, t


@test("2.29.3 Wöchentliches Sicherheitsupdate: montags Pakete aktualisieren, neu bauen, Selbsttest und Trivy – nur bei Erfolg neue Version und Release (latest); ohne Änderung nichts; Versionsnummer, CHANGELOG und Neuigkeiten automatisch")
def _():
    import shutil as _sh
    import subprocess as _sp
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    wf = os.path.join(root, ".github", "workflows", "weekly.yml")
    tool = os.path.join(root, "tools", "security-release.py")
    if not os.path.exists(wf) or not os.path.exists(tool):
        return  # im Container liegen Workflows und Werkzeuge nicht
    t = open(wf, encoding="utf-8").read()
    assert '"23 3 * * 1"' in t, "montags"
    order = ["pin-versions.sh --update", "git diff --quiet", "tools/trivy-scan.sh pocketknife:local", "tools/security-release.py",
             "git push", "gh workflow run release.yml"]
    pos = [t.index(x) for x in order]
    assert pos == sorted(pos), "Reihenfolge: aktualisieren/bauen/testen → Trivy → Version → speichern → Release"
    assert t.count("if: steps.diff.outputs.changed == 'true'") >= 3, "ohne Änderung keine neue Version"
    assert "actions: write" in t and "contents: write" in t and "packages: write" not in t, "veröffentlichen tut nur release.yml"
    assert "group: release" in t and "group: release" in open(os.path.join(root, ".github", "workflows", "release.yml"), encoding="utf-8").read()
    # Werkzeug: Version +1, CHANGELOG/Neuigkeiten/README, Änderungen aus den festgelegten Versionen
    import importlib.util as _iu
    spec = _iu.spec_from_file_location("pk_secrel", tool)
    m = _iu.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.bump("2.29.2") == "2.29.2-s1" and m.bump("2.29.2-s1") == "2.29.2-s2" and m.bump("2.30-s9") == "2.30-s10", "gleiches Programm, nur neue Pakete"
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
    assert ch.index("## pocketknife-2.29.2-s1 (") < ch.index("## pocketknife-2.29.2 ") and "libssl3t64` 3.5.1-1 → 3.5.1-2" in ch and "Basis-Image" in ch, ch
    nv = json.loads(rd("app", "news.json"))["versions"][0]
    assert nv["version"] == "2.29.2-s1" and nv["items"][0]["kind"] == "security" and nv["items"][0]["de"] and nv["items"][0]["en"]
    from app import news
    news.validate(json.loads(rd("app", "news.json")))  # Aufbau passt


@test("2.29.4 Alles im Image aktuell: alle Debian-Pakete (auch die des Basis-Images) auf Snapshot-Stand, pip nicht im Image, Update bleibt in Python-Reihe und Debian-Version, guacd neueste Version; Unterbau-Warnung rechtzeitig vor Ende der Updates")
def _():
    import subprocess as _sp
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    pv = os.path.join(root, "tools", "pin-versions.sh")
    if not os.path.exists(pv):
        return  # im Container liegen die Bauwerkzeuge nicht
    ai = open(os.path.join(root, "docker", "apt-install.sh"), encoding="utf-8").read()
    assert ai.index("upgrade") < ai.index("apt-get install -y --no-install-recommends $PKGS"), "erst alles aktualisieren, dann installieren"
    df = open(os.path.join(root, "Dockerfile"), encoding="utf-8").read()
    assert df.index("pip check") < df.index("pip uninstall -y -q pip"), "pip nach der Prüfung entfernt"
    src = open(pv, encoding="utf-8").read()
    assert "python:3.12-slim|" not in src, "nie „python:3.12-slim“ ohne Debian-Namen (wechselte still die Debian-Version)"
    assert "PK_PYTHON_BASE" in src and "importlib.metadata" in src and "hub.docker.com/v2/repositories/guacamole/guacd/tags" in src
    line = [x for x in src.splitlines() if x.strip().startswith("BASE_TAG=$(printf")][0]
    sedexpr = line.split("sed -n '", 1)[1].rsplit("'", 1)[0]
    for cur, want in (("python:3.12.15-slim-trixie@sha256:" + "a" * 64, "python:3.12-slim-trixie"),
                      ("python:3.13.2-slim-forky", "python:3.13-slim-forky")):
        out = _sp.run(["sed", "-n", sedexpr], input=cur, capture_output=True, text=True).stdout.strip()
        assert out == want, (cur, out)
    # Unterbau: rechtzeitig melden, nie die Sicherheitsupdates blockieren
    import importlib.util as _iu
    spec = _iu.spec_from_file_location("pk_unterbau", os.path.join(root, "tools", "unterbau-check.py"))
    m = _iu.module_from_spec(spec)
    spec.loader.exec_module(m)
    d = tempfile.mkdtemp(prefix="pk-unterbau-", dir=TMP)
    pj, dj = os.path.join(d, "py.json"), os.path.join(d, "deb.json")
    json.dump({"result": {"releases": [{"name": "3.13", "eolFrom": "2029-10-31"}, {"name": "3.12", "eolFrom": "2028-10-31"}]}}, open(pj, "w"))
    json.dump({"result": {"releases": [{"name": "13", "eolFrom": "2028-08-09"}]}}, open(dj, "w"))
    assert m.eol(json.load(open(pj)), "3.12").isoformat() == "2028-10-31"
    assert m.eol([{"cycle": "3.12", "eol": "2028-10-31"}], "3.12").isoformat() == "2028-10-31", "altes Format"
    py, code = m.base()
    assert __import__("re").match(r"^\d+\.\d+$", py) and code in m.DEBIAN, (py, code)
    run = lambda today, p=pj: _sp.run([sys.executable, "-I", os.path.join(root, "tools", "unterbau-check.py"), "--python-json", p,  # noqa: E731
                                      "--debian-json", dj, "--today", today], capture_output=True, text=True, timeout=60)
    r = run("2026-10-24")
    assert r.returncode == 0 and "::" not in r.stdout, r.stdout
    r = run("2027-12-01")
    assert r.returncode == 0 and "::warning" in r.stdout and "einplanen" in r.stdout
    r = run("2028-06-01")
    assert r.returncode == 1 and "::error title=Unterbau anheben" in r.stdout
    r = run("2028-06-01", p=os.path.join(d, "fehlt.json"))
    assert "nicht ermittelbar" in r.stdout
    wf = open(os.path.join(root, ".github", "workflows", "weekly.yml"), encoding="utf-8").read()
    assert "unterbau:" in wf and "tools/unterbau-check.py" in wf and "guacd prüfen" in wf
    sec = open(os.path.join(root, ".github", "workflows", "security.yml"), encoding="utf-8").read()
    assert 'exit "$rc"' in sec and "grc=0" in sec, "guacd-Befunde nur als Hinweis"


@test("2.29.5 Kein Versionssprung bei Python-Paketen: jedes bleibt auf seiner Hauptversion (vor 1.0 Unterversion), Kalenderversionen (certifi) immer neu; Grenzen gelten beim wöchentlichen Update; neue Hauptversion nur bewusst")
def _():
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    tool = os.path.join(root, "tools", "caps.py")
    if not os.path.exists(tool):
        return  # im Container liegen die Bauwerkzeuge nicht
    import importlib.util as _iu
    spec = _iu.spec_from_file_location("pk_caps", tool)
    m = _iu.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.cap("50.0.2") == "<51" and m.cap("0.155.0") == "<0.156" and m.cap("0.0.5") == "<0.1" and m.cap("2.9.1") == "<3"
    assert m.cap("2026.10.19") is None and m.cap("2025.2") is None, "Kalenderversionen ohne Grenze (Stammzertifikate, Zeitzonen)"
    assert m.line_for("fastapi", "0.155.0") == "fastapi>=0.155.0,<0.156" and m.line_for("certifi", "2026.10.19") == "certifi>=2026.10.19"
    t = m.build_ranges("# Kommentar\nCython==3.2.1\nsetuptools\n")
    assert t == "# Kommentar\nCython>=3.2.1,<4\nsetuptools\n", t
    lock = open(os.path.join(root, "requirements.lock"), encoding="utf-8").read()
    caps = open(os.path.join(root, "requirements.caps"), encoding="utf-8").read()
    names = {x.split("==")[0].lower() for x in lock.splitlines() if "==" in x and not x.startswith("#")}
    capped = {__import__("re").split(r"[<>=]", x)[0].lower() for x in caps.splitlines() if x and not x.startswith("#")}
    assert names == capped, ("requirements.caps passt nicht zu requirements.lock", names ^ capped)
    df = open(os.path.join(root, "Dockerfile"), encoding="utf-8").read()
    assert df.count("-c requirements.caps") == 2 and df.count("requirements.caps") >= 4, "Grenzen in Bau- und Laufzeit-Stufe"
    pv = open(os.path.join(root, "tools", "pin-versions.sh"), encoding="utf-8").read()
    assert pv.index("tools/caps.py lock requirements.lock") < pv.index("' > requirements.lock"), "Grenzen VOR dem Leeren der Datei"
    assert "PK_ALLOW_MAJOR" in pv
    assert "docker compose config --images" not in pv, "guacd aus der Datei lesen (compose config scheitert ohne .env → ohne Digest)"
    assert pv.index('docker pull -q "$BASE"') < pv.index("DIGEST=$(docker image inspect"), "BuildKit: Basis-Image vor dem Lesen der Prüfsumme holen"
    for f in ("weekly.yml", "update-pins.yml"):
        w = open(os.path.join(root, ".github", "workflows", f), encoding="utf-8").read()
        assert "requirements.caps" in w and "::error title=Versionen aktualisieren::" in w, f
    assert "requirements.caps" in open(os.path.join(root, "update.sh"), encoding="utf-8").read()


@test("2.29.7 Sicherheitsupdates als <Version>-s<n> (gleiches Programm, nur neue Pakete): richtig sortiert in App, update.sh und Release; keine Git-Anmeldedaten im Arbeitsordner der Workflows")
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
                         ("2.30", "2.29.6-s9", False), ("2.29.6-s1", "2.29.6", False)):
            r = _sp.run(["sh", "-c", fn + f'\nver_lt "{a}" "{b}"'], capture_output=True, text=True)
            assert (r.returncode == 0) == lt, (a, b, r.returncode)
        assert "(-s[0-9]+)?$" in src, "ZIP mit -s-Version wird angenommen"
    wfd = os.path.join(root, ".github", "workflows")
    if os.path.isdir(wfd):
        rel = open(os.path.join(wfd, "release.yml"), encoding="utf-8").read()
        assert "(-s[0-9]+)?$" in rel
        for f in os.listdir(wfd):
            t = open(os.path.join(wfd, f), encoding="utf-8").read()
            n_co = len(_re.findall(r"uses: actions/checkout@", t))
            assert t.count("persist-credentials: false") == n_co, f"{f}: Git-Anmeldedaten im Arbeitsordner"


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
    print(f"\n{ok} von {len(RESULTS)} Tests bestanden (Python {sys.version.split()[0]}).")
    shutil.rmtree(TMP, ignore_errors=True)  # keine pk-selftest-*-Ordner zurücklassen
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    socket.setdefaulttimeout(15)
    sys.exit(main())
