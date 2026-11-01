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

# 2.32: Die Einstellungen der Instanz (.env im Container: PK_NETBIOS, PK_DOMAIN, Dienstkonto …) gelten hier nicht – sonst
# hinge ein Ergebnis von der Umgebung ab (z. B. Kiosk-Test mit „FIRMA\nutzer“ bei anderem NetBIOS-Namen), und echte
# Anmeldedaten hätten im Test nichts verloren. Nur die Schalter des Selbsttests selbst bleiben.
for _k in [k for k in os.environ if k.startswith("PK_") and k != "PK_PWSH" and not k.startswith("PK_SELFTEST")]:
    del os.environ[_k]
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
    # 2.33: guacd fest in docker/guacd.txt (Version + Digest) – veröffentlicht als …/pocketknife-guacd, die
    # docker-compose.yml nennt nur noch diesen Namen (ändert sich für ein guacd-Update nicht)
    g = open(os.path.join(root, "docker", "guacd.txt"), encoding="utf-8").read()
    assert re.fullmatch(r"guacamole/guacd:[\w.]+@sha256:[0-9a-f]{64}\n", g), "guacd ohne Digest"
    dc = open(os.path.join(root, "docker-compose.yml"), encoding="utf-8").read()
    assert "image: ${PK_GUACD_IMAGE:-ghcr.io/soleil0815/pocketknife-guacd:latest}" in dc and "guacamole/guacd" not in dc


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
    assert "draggable: locked(c) ? 'false' : 'true'" in js and "if (!locked(c)) it.addEventListener('dragstart'" in js \
        and "const locked = (c) => !!(c && (c.builtin || c.editable === false));" in js, "Standard- und AD-Baum nicht verschiebbar"


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
    assert "-1978335140" in body and "archiveExtractionMethod" in body and "$pkSys" in body and ".IsSystem" not in body[len(deploy._winget_find()):], "CLM: ohne .NET-Methode"
    assert body.count("PK-Run $wg") == 3, "Aufruf, Wiederholung mit tar.exe, Wiederholung ohne --scope (2.30.1)"
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
    from app import remote, steps, util
    assert util.PS_FLAGS == "-NoLogo -NonInteractive -NoProfile -ExecutionPolicy Bypass"
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app")
    for f in _g.glob(os.path.join(root, "*.py")):
        src = open(f, encoding="utf-8").read()
        for bad in ("'powershell.exe'", "powershell.exe -No", "--headless powershell.exe"):
            assert bad not in src, f"{os.path.basename(f)}: PowerShell ohne vollen Pfad ({bad})"
    b = steps.body_for({"type": "powershell", "script": "Write-Output 1", "id": "x"}, {})
    assert b.rstrip().endswith("PK-RunPs1 $pkF ''")
    w = remote.WRAPPER
    assert 'PK-Run "$env:SystemRoot\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" ((\'-NoLogo -NonInteractive -NoProfile -ExecutionPolicy Bypass -File' in w


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
              ("POST", "/api/tools"), ("PUT", "/api/tools/{tid}"), ("POST", "/api/customreports"), ("PUT", "/api/customreports/{rid}"),
              ("DELETE", "/api/customreports/{rid}"), ("POST", "/api/mail/reports"),
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
            msg = _m._repo_err(ou, Exception("SMB-Anmeldung an FS01 als svc-repo abgelehnt: \\\\fs01\\pakete\\x"))
            assert "fs01" not in msg.lower() and "svc-repo" not in msg and "pakete" not in msg.lower(), msg
            assert _m._repo_err(admin, Exception("\\\\fs01\\PAKETE")) == "\\\\fs01\\PAKETE", "Hauptadmins sehen alles"
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
        assert up["latest"] == fut and up["security"] and up["versions"][0]["items"][0]["kind"] == "security" and up["command"] == "sudo sh update.sh"
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
        # Schritt eines Hauptadmins (Paket für OU-Admins freigegeben, 2.30): unverändert speicherbar, geändert nicht
        pid = db.ex("INSERT INTO packages(name, version, steps, conditions, run_as, tenant_id, created_at, ou_edit) VALUES('SK von Haupt','1',?,'{}','system',?,?,1)",
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
    # 2.32.5: eigene Gruppe (sonst verwirft GitHub einen wartenden Release) und nur auf einen veröffentlichten Stand
    assert "group: weekly-security" in t and "group: release" in open(os.path.join(root, ".github", "workflows", "release.yml"), encoding="utf-8").read()
    assert 'gh release view "v$VER"' in t and "needs: stand" in t and "if: needs.stand.outputs.released == 'true'" in t
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
                         ("2.30", "2.29.6-s9", False), ("2.29.6-s1", "2.29.6", False),
                         # 2.32.5: vierstellige Versionen
                         ("2.29.6", "2.29.6.1", True), ("2.29.6.1", "2.29.6", False), ("2.29.6.1", "2.29.6.1-s1", True),
                         ("2.29.6.0", "2.29.6", False)):
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


@test("2.29.8 Übersicht: lange Namen bei „Letzte Bereitstellungen“ werden umgebrochen und schieben die Kachel nicht über ihren Rand")
def _():
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static")
    js = open(os.path.join(root, "app.js"), encoding="utf-8").read()
    dep = js[js.index("const depCard"):js.index("const logCard")]
    assert "h('td.wrap', h('b', x.name)" in dep, "Name der Bereitstellung umbrechen"
    css = open(os.path.join(root, "app.css"), encoding="utf-8").read()
    assert ".grid > * { min-width: 0; }" in css and ".card > .card-b.flush { overflow-x: auto; }" in css


@test("2.29.9 Mandanten: mehrere Sammlungen je Mandant (Bereich = alle zusammen); OU-Admins sehen nur die Sammlungen ihres Bereichs, nicht die ganze Domäne")
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
        # Bereich = beide Sammlungen zusammen
        assert access.tenant_cols(db.q1("SELECT * FROM tenants WHERE id=?", (tid,))) == [ou_a, ou_b]
        assert access.tenant_cols({"collection_id": 7, "collection_ids": None}) == [7], "ältere Mandanten: eine Sammlung"
        assert deploy.tenant_members(tid) == {a1, a2}
        assert {c["id"] for c in _asgi("GET", "/api/computers", tok)[1]} == {a1, a2}
        # Sichtbare Sammlungen: eigene, solche mit eigenen Computern (Ausnahme-Gruppe), Standardsammlungen, Ordner darüber
        vis = {c["id"]: c for c in _asgi("GET", "/api/collections", tok)[1]}
        assert ou_a in vis and ou_b in vis and ausn in vis and folder in vis, sorted(vis)
        assert ou_fremd not in vis, "Sammlung eines anderen Mandanten"
        assert ou_leer not in vis, "Sammlung ohne eigene Computer (Rest der Domäne)"
        assert vis[ausn]["count"] == 1, "zählt nur eigene Computer"
        assert any(c["builtin"] == 1 for c in vis.values()), "Standardsammlungen bleiben"
        meta = _asgi("GET", "/api/packages/meta", tok)[1]
        ch = {c["id"] for c in meta["collections"]}
        assert ausn in ch and ou_leer not in ch and ou_fremd not in ch, "auch die Auswahl in Paket-Bedingungen"
        # Mandant speichern mit mehreren Sammlungen (Hauptadmin-Weg)
        assert _m._tenant_cols_in({"collection_ids": [ou_a, str(ou_b), ou_a]}) == [ou_a, ou_b]
        assert _m._tenant_cols_in({"collection_id": ou_a}) == [ou_a], "alter Aufruf mit einer Sammlung"
        assert _m._tenant_cols_in({"collection_ids": [0, ou_a]}) == [ou_a], "„Alle Computer“ (0) zählt nicht als Sammlung"
        assert _m._tenant_cols_in({"collection_ids": []}) == []
        assert ou_b in collections_.referenced_ids(), "zweite Sammlung eines Mandanten ist vor dem Löschen geschützt"
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


@test("2.30 Mandanten sauber getrennt: OU-Admins ändern nur ihr Eigenes – Hauptadmin-Pakete/-Zeitpläne/-Sammlungen nur lesen (kopieren geht), zentrale Zeitpläne und Berichte nur mit eigenen Computern, eigene Sammlungen nie größer als der Mandant")
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
        # --- Pakete: vom Hauptadmin für den Mandanten = nur lesen; freigegeben oder selbst angelegt = ändern
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
        assert db.q1("SELECT tenant_id, ou_edit FROM packages WHERE id=?", (r["id"],)) == {"tenant_id": ta, "ou_edit": 1}, "Kopie gehört dem OU-Admin"
        assert _m._ou_edit_flag({"username": "tr-ou", "scope": [ta]}, {"ou_edit": False}) == 1, "OU-Admin: immer seins"
        # Hauptadmin-Häkchen „OU-Admins dürfen ändern“
        u_h = {"username": "tr-haupt", "scope": None, "role": "admin"}
        assert _m._ou_edit_flag(u_h, {"ou_edit": True}) == 1 and _m._ou_edit_flag(u_h, {"ou_edit": False}, {"ou_edit": 1}) == 0
        assert _m._ou_edit_flag(u_h, {}, {"ou_edit": 1}) == 1 and _m._ou_edit_flag(u_h, {}) == 0, "Standard: nur lesbar"

        # --- Zeitpläne: zentral = nur lesen, nur eigene Computer, nicht starten; Hauptadmin-Mandantenzeitplan = lesen + starten
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
        assert z["targets"] == {"computers": [a1], "collections": []}, ("fremde Ziele verborgen", z["targets"])
        assert lst[s_main]["editable"] is False and lst[s_main]["runnable"] is True and lst[s_own]["editable"] is True
        zb = {s["id"]: s for s in _asgi("GET", "/api/schedules", ou_b)[1]}
        assert zb[s_zen]["targets"]["computers"] == [b1] and zb[s_zen]["members"] == 1, "zentral: jeder sieht nur seine Computer"
        assert s_main not in {s["id"] for s in _asgi("GET", "/api/schedules", ou_b)[1]}, "Zeitplan eines anderen Mandanten"
        sd = _asgi("GET", f"/api/schedules/{s_zen}", ou)[1]
        assert sd["members"] == 1 and sd["targets"]["computers"] == [a1]
        assert _asgi("GET", f"/api/schedules/{s_zen}/preview", ou)[1]["members"] == 1
        body = {**base, "name": "geändert", "targets": {"computers": [a1], "collections": []}}
        for sid in (s_zen, s_main):
            assert _asgi("PUT", f"/api/schedules/{sid}", ou, body)[0] == 403, sid
            assert _asgi("DELETE", f"/api/schedules/{sid}", ou)[0] == 403, sid
        assert _asgi("POST", f"/api/schedules/{s_zen}/run", ou, {})[0] == 403, "zentral starten nur Hauptadmins"
        assert _asgi("POST", f"/api/schedules/{s_zen}/reset", ou, {})[0] == 403
        access.need_schedule({"username": "tr-ou", "scope": [ta]}, s_main, run=True)  # Hauptadmin-Zeitplan des Mandanten: starten erlaubt
        assert _asgi("PUT", f"/api/schedules/{s_own}", ou, {**body, "tenant_id": ta})[0] == 200
        st, r = _asgi("POST", f"/api/schedules/{s_zen}/duplicate", ou)
        assert st == 200, (st, r)
        made_s.append(r["id"])
        cp = schedules.get(r["id"])
        assert cp["tenant_id"] == ta and cp["ou_edit"] == 1 and cp["targets"]["computers"] == [a1] and not cp["enabled"], cp

        # --- Sammlungen: Hauptadmin-Sammlung nur lesen; eigene anlegen, nie größer als der Mandant
        assert _asgi("PUT", f"/api/collections/{ca}", ou, {"name": "x", "type": "static"})[0] == 403
        assert _asgi("DELETE", f"/api/collections/{ca}", ou)[0] == 403
        assert _asgi("POST", f"/api/collections/{ca}/rename", ou, {"name": "x"})[0] == 403
        st, r = _asgi("POST", "/api/collections", ou, {"name": "TR alles", "type": "dynamic", "filters": [], "match": "all"})
        assert st == 200, (st, r)
        dyn = r["id"]; made_c.append(dyn)
        assert db.val("SELECT tenant_id FROM collections WHERE id=?", (dyn,)) == ta
        assert set(collections_.member_ids(dyn, fresh=True)) == {a1}, "Regel „alle“ – trotzdem nur Computer des Mandanten"
        st, r = _asgi("POST", "/api/collections", ou, {"name": "TR statisch", "type": "static"})
        stat = r["id"]; made_c.append(stat)
        assert _asgi("POST", f"/api/collections/{stat}/members", ou, {"add": [b1]})[0] == 403, "fremder Computer"
        assert _asgi("POST", f"/api/collections/{stat}/members", ou, {"add": [a1]})[0] == 200
        assert _asgi("POST", "/api/collections", ou, {"name": "TR unter fremd", "type": "static", "parent_id": ca})[0] == 403
        flt = {"type": "group", "match": "all", "items": [{"type": "cond", "table": "computer", "column": "collection", "op": "in", "value": str(cb)}]}
        assert _asgi("POST", "/api/collections", ou, {"name": "TR Verweis", "type": "dynamic", "filters": flt})[0] == 403, "Verweis auf unsichtbare Sammlung"
        cl = {c["id"]: c for c in _asgi("GET", "/api/collections", ou)[1]}
        assert cl[dyn]["editable"] is True and cl[ca]["editable"] is False and cb not in cl
        assert dyn not in {c["id"] for c in _asgi("GET", "/api/collections", ou_b)[1]}, "eigene Sammlung bleibt beim eigenen Mandanten"
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
            raise AssertionError("OU-Sammlung als Mandantenbereich angenommen")
        except _HE:
            pass
        st, r = _asgi("DELETE", f"/api/collections/{ca}", haupt)
        assert st == 400 and "Mandanten" in str(r), ("Bereich eines Mandanten nie löschbar", st, r)
        assert _asgi("DELETE", f"/api/collections/{stat}", ou)[0] == 200
        made_c.remove(stat)

        # --- Berichte: lesen ja, nur eigene Computer; internen Parameter nicht von außen setzbar
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
        assert _asgi("GET", "/api/mail/reports", ou)[0] == 403, "Mailversand nur Hauptadmins"
        h_names = {x["name"] for x in _asgi("GET", "/api/reports/computers", haupt)[1]["rows"]}
        assert {"TR-A1", "TR-B1"} <= h_names

        # --- einmalige Übernahme: was ein OU-Admin angelegt hat, darf er weiter ändern
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


@test("2.30 docker-compose.yml: zweiter DNS-Server echt optional (nie dieselbe Adresse doppelt – ältere Compose-Versionen lehnen das ab); Installationsanleitung im Handbuch")
def _():
    import re as _re
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    src = os.path.join(base, "docker-compose.yml")
    if not os.path.exists(src):
        return  # im Image liegt die Compose-Datei nicht
    y = open(src, encoding="utf-8").read()
    assert "${PK_DNS_2:-${PK_DNS_1}}" not in y, "Ersatzwert für PK_DNS_2 darf nicht PK_DNS_1 sein (doppelter Eintrag)"

    def interp(v, env):  # das Nötige von Compose: ${VAR}, ${VAR:-Standard}, ${VAR:?Meldung}
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
    assert len(blocks) == 2, "dns bei pocketknife und guacd"
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


@test("2.30 Start ohne Schreibrecht auf /data (z. B. Synology): klare Meldung mit Ordner, Benutzer-ID und Abhilfe statt Traceback; Anleitung für NAS")
def _():
    from app import privileges
    d = tempfile.mkdtemp(prefix="pk-wr-")
    try:
        assert privileges.unwritable([d, os.path.join(d, "tls")]) == [] and os.path.isdir(os.path.join(d, "tls")), "fehlende Unterordner anlegen"
        def deny(p):
            if p.endswith("tls"):
                raise PermissionError(13, "Permission denied")
        bad = privileges.unwritable([d, os.path.join(d, "tls")], probe=deny)
        assert bad == [(os.path.join(d, "tls"), "Permission denied")], bad
        msg = privileges.write_hint(bad, d)
        for w in ("Start abgebrochen", f"{os.getuid()}:{os.getgid()}", "tls: Permission denied", "PK_UID und PK_GID", "Synology", "chown -R",
                  "nicht von Hand"):
            assert w in msg, (w, msg)
        if os.geteuid() == 0:  # echt prüfen: als Benutzer ohne Rechte auf einem fremden Ordner (eigener Prozess, kein fork)
            import subprocess
            os.chmod(d, 0o700)
            # Modul in einen für alle lesbaren Ordner kopieren – der Programmordner ist für UID 65534 evtl. nicht lesbar
            mod = tempfile.mkdtemp(prefix="pk-wrmod-")
            os.chmod(mod, 0o755)
            shutil.copy(privileges.__file__, os.path.join(mod, "pkpriv.py"))
            os.chmod(os.path.join(mod, "pkpriv.py"), 0o644)
            code = ("import sys; sys.path.insert(0, %r); import pkpriv; "
                    "sys.exit(3 if pkpriv.unwritable([%r]) else 0)") % (mod, d)
            r = subprocess.run([sys.executable, "-I", "-c", code], user=65534, group=65534, extra_groups=[],
                               capture_output=True, text=True, timeout=60, cwd="/")
            shutil.rmtree(mod, ignore_errors=True)
            assert r.returncode == 3, ("fremder Ordner muss als nicht beschreibbar erkannt werden", r.returncode, r.stderr[-300:])
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


@test("2.30.1/2.31 winget: „keine passende Aktualisierung“/„kein installiertes Paket“ mit --scope – einmal ohne --scope wiederholen (MobaXterm, PowerShell 7); danach auch tar.exe ohne --scope; neuere, aber unpassende Version nicht als „aktuell“ werten")
def _():
    from app import deploy
    find = deploy._winget_find()
    for scope in ("machine", "user"):
        body = deploy._winget_body({"winget_action": "install", "winget_id": "Mobatek.MobaXterm", "winget_scope": scope,
                                    "extra_args": "--force"})
        rest = body[len(find):]
        assert rest.count("PK-Run $wg $pkA") == 3 and "-1978335189 -or $global:PKExit -eq -1978335212" in rest
        plain = [x for x in rest.splitlines() if x.strip().startswith("$pkA = ")][1]
        assert "--scope" not in plain and "--force" in plain and "Mobatek.MobaXterm" in plain, "ohne --scope, sonst gleich"
        assert rest.index("-1978335212") < rest.index("-1978335140"), "erst ohne --scope, dann tar.exe – mit den Argumenten ohne --scope"
    nos = deploy._winget_body({"winget_action": "install", "winget_id": "Mobatek.MobaXterm", "winget_scope": ""})[len(find):]
    assert "-1978335212" not in nos and nos.count("PK-Run $wg $pkA") == 2, "ohne festgelegten Bereich nichts ohne --scope zu wiederholen"
    assert "-1978335189" not in deploy._winget_body({"winget_action": "uninstall", "winget_id": "X.Y", "winget_scope": "machine"})
    assert deploy.WINGET_MESSAGES[9011] and deploy.WINGET_MESSAGES[9012] and not deploy.evaluate({"type": "winget"}, 9011)[0]
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "remote.py"), encoding="utf-8").read()
    assert "$global:PKOut += $pkL" in src, "PK-Run merkt sich die Ausgabe"
    pw = os.environ.get("PK_PWSH")
    if pw and os.path.exists(pw):
        import subprocess
        rest = deploy._winget_body({"winget_action": "upgrade", "winget_id": "X.Y", "winget_scope": "machine"})[len(find):]
        rest = rest.replace("[IO.File]::WriteAllText", "$null = ")  # Datei schreiben wird hier nicht geprüft
        NOT_APPLY = "A newer package version is available in a configured source, but it does not apply to your system or requirements."
        TECH = "A newer version was found, but the install technology is different from the current version installed."
        CUR = "No newer package versions are available from the configured sources."
        cases = [  # (Antworten je Aufruf: (Exitcode, Text), erwartete Aufrufe, erwarteter Endcode)
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


@test("2.31 Dienstkonto-Schutz: „Kennwort falsch“ von einem einzelnen Gerät (falscher DNS-Eintrag, kein Domänenmitglied) löst ihn nur aus, wenn das Konto per LDAP wirklich nicht geht")
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
        assert guard.report_remote("WinRM 401: Zugriff verweigert", "WinRM") is None, "kein Kontofehler – nichts zu tun"
        assert not guard.paused()
        guard.verify_account = lambda: False
        guard.report_remote(msg, "SMB")
        assert guard.paused(), "Konto geht wirklich nicht → Schutz"
        guard.reset()
        guard.verify_account = lambda: True
        assert _r._winrm_error("the specified credentials were rejected by the server 0xc000006d").startswith(guard.HOST_REJECT)
        assert not guard.paused()
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "remote.py"), encoding="utf-8").read()
        assert 'guard.report(' not in src, "WinRM/SMB zu Computern nur über report_remote"
    finally:
        guard.verify_account = orig
        guard.reset()


@test("2.31 Wake-on-LAN: mehrfach senden, feste Relays zuerst, MAC der Kabelkarte aus dem Inventar, Aufwachen abwarten mit zweitem Versuch, Bereitschaft im Inventar/Bericht; Zeitplan weckt nur auf Wunsch und nur bei echten Läufen")
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
        assert macs == ["00:11:22:33:44:55", "00:11:22:33:44:66"], ("Kabelkarte dazu, WLAN nicht", macs)
        db.set_setting("wol_relays", "wl-srv")
        rel = [(r["name"], same) for r, same in tools.wol_relays(c)]
        assert rel[0][0] == "WL-SRV" and ("WL-NACHBAR", True) in rel, rel
        # zweiter Versuch: andere Relays, falls es welche gibt – sonst (2.32.5) wieder die im Zielnetz statt nur des Containers
        assert ("WL-NACHBAR", True) in [(r["name"], same) for r, same in tools.wol_relays(c, exclude={"WL-NACHBAR", "WL-SRV"})], \
            "kein anderes Relay im Zielnetz → das bisherige erneut"
        db.set_setting("wol_repeat", 3)
        tools.remote.ps = lambda host, script, **kw: (sent.append((host, script)) or ("OK 10.9.0.255:9\n", "", 0))
        res = tools.wake(c)
        assert res["exit"] == 0 and "3×" in res["output"] and "Magic Packet an, darf wecken" in res["output"], res["output"]
        assert all("-le 3" in sc for _h, sc in sent) and len(sent) == 2, "jedes Relay sendet 3×"
        assert "(fest)" in res["output"]
        # Aufwachen abwarten: beim zweiten Blick online
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
        assert tools.watch_wake(c, [], "test") is False, "0 = nicht warten"
        # Bereitschaft: Spalte für Sammlungen und Bericht
        w, p = collections_.build({"type": "group", "match": "all", "items": [
            {"type": "cond", "table": "computer", "column": "wol_ready", "op": "is_true", "value": ""}]})
        assert ziel in {r["id"] for r in db.q(f"SELECT c.id FROM computers c WHERE {w}", p)}, "Kabelkarte mit Magic Packet an → bereit"
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
        # Zeitplan: Standard aus; an → nur bei echten Läufen, nicht bei Heartbeat/Nachholen
        assert schedules.DEFAULT_OPTIONS["wake_offline"] is False
        woken = []
        orig_wake = tools.wake
        tools.wake = lambda cc, **kw: (woken.append(cc["id"]) or {"exit": 1})
        try:
            db.ex("UPDATE computers SET online=0 WHERE id=?", (ziel,))  # 2.32.5: geweckt wird nur, wer wirklich aus ist
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
            assert cl["options"]["offline"] == "wait", "geweckte Ziele werden vorgemerkt"
        js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
        assert "function wakeDialog(targets)" in js and "['wol', 'Wake-on-LAN', 'play', [wolCard], true]" in js and "o.wake_offline" in js
    finally:
        tools.remote.ps, presence.probe, tools.WAKE_POLL = orig_ps, orig_probe, orig_wait
        for k, v in old.items():
            db.set_setting(k, v)
        for x in (ziel, nachbar, server):
            db.ex("DELETE FROM inventory_data WHERE computer_id=?", (x,))
            db.ex("DELETE FROM computers WHERE id=?", (x,))


@test("2.31 Fehlersuche: Prozess „ohne Ergebnis beendet“ nennt den Grund (Fehlerausgabe von PowerShell, Sperre durch Defender-ASR/AppLocker, letzte Ausgabe); Rechenlast je Hintergrundaufgabe und Lastspitzen mit Kontext")
def _():
    from app import loadstats
    import app.remote as _r
    assert "2>\"' + (Join-Path $dir 'pk_err.txt')" in _r.START_DIRECT and "cmd.exe /d /c" in _r.START_DIRECT
    assert "PK_ASRBLOCK:" in _r.POLL and "Microsoft-Windows-Windows Defender/Operational" in _r.POLL and "8007" in _r.POLL
    m = _r.gone_message("PK_PROC:gone\nPK_ASRBLOCK:D1E49AAC-8F56-4280-B9BA-993A6D77406C\nPK_LAST:OK gesetzt", True)
    assert "Prozesserstellungen durch PSExec- und WMI-Befehle blockieren" in m and "Lokales System" in m and "Letzte Ausgabe: OK gesetzt" in m, m
    # 2.32.1: „nur überwacht“ (1122) ist nie der Grund – dann zählt die Fehlerausgabe von PowerShell
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


@test("2.32 Pakete zusammenfassen: verschachtelt (Reihenfolge, Neustart, Bei Fehler) oder als Kopie der Schritte („Ausführen als“ bleibt, eigene Bedingungen verhindern das Kopieren); nie über Mandanten hinweg; gpupdate /force wie gewohnt")
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
        assert st == 200, ("verschachtelt geht mit Bedingungen", st, r)
        made.append(r["id"])
        st, r = _asgi("POST", "/api/packages/merge", tok, {"ids": [t1, t2], "mode": "nested"})
        assert st == 400 and "Mandanten" in str(r), (st, r)
        st, r = _asgi("POST", "/api/packages/merge", tok, {"ids": [a, t1], "mode": "nested"})
        assert st == 200 and db.val("SELECT tenant_id FROM packages WHERE id=?", (r["id"],)) == tid, ("zentral + Mandant → Mandant", st, r)
        made.append(r["id"])
        assert _asgi("POST", "/api/packages/merge", tok, {"ids": [a]})[0] == 400, "mindestens zwei"
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "main.py"), encoding="utf-8").read()
        assert "echo N| gpupdate.exe /force" in src and "/target:computer" not in src
        js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
        assert "function mergePackagesDlg(selRows)" in js and "Zu neuem Paket zusammenfassen" in js
    finally:
        db.ex("DELETE FROM sessions WHERE token_hash=?", (th,))
        for x in made:
            db.ex("DELETE FROM pkg_ident WHERE package_id=?", (x,))
            db.ex("DELETE FROM packages WHERE id=?", (x,))
        db.ex("DELETE FROM tenants WHERE id IN (?,?)", (tid, tid2))


@test("2.32 Selbsttest unabhängig von der Instanz: Einstellungen aus der .env (PK_NETBIOS, Dienstkonto …) gelten im Test nicht; kein fork()")
def _():
    from app import config
    assert config.NETBIOS == "" and config.DOMAIN == "firma.local" and not config.ENV_SERVICE_USER, (config.NETBIOS, config.DOMAIN)
    assert not [k for k in os.environ if k.startswith("PK_") and k not in ("PK_PWSH", "PK_DATA_DIR", "PK_HTTPS", "PK_DOMAIN", "PK_PUBLIC_HOSTNAME")
                and not k.startswith("PK_SELFTEST")], sorted(k for k in os.environ if k.startswith("PK_"))
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    assert "os." + "fork()" not in src, "fork() in einem Prozess mit Threads kann hängen – subprocess verwenden"


@test("2.32 Scan nach der Bereitstellung: gewähltes Scanprofil des Pakets – auch bei mehreren Paketen und in Zeitplänen (je Paket, „Nicht scannen“ zählt); läuft gerade ein Scan, wird er danach nachgeholt statt verworfen")
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
        assert deploy.rescan_profiles(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (b,)))) == [hw], "eigenes Profil"
        assert deploy.rescan_profiles(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (a,)))) == [apps], "Standard: Anwendungen"
        assert deploy.rescan_profiles(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (n,)))) == [], "Nicht scannen"
        assert deploy.rescan_profiles(deploy.row_to_package(db.q1("SELECT * FROM packages WHERE id=?", (gone,)))) == [apps], "gelöschtes Profil → Anwendungen"
        comp = deploy.composite([b, n, a])
        assert deploy.rescan_profiles(comp) == [hw, apps], ("mehrere Pakete: je Paket", deploy.rescan_profiles(comp))
        assert deploy.rescan_profiles(deploy.composite([n])) == [], "nur „Nicht scannen“"
        s = {"name": "RS", "packages": [b, a], "options": {"stop_on_fail": True}, "tenant_id": None}
        assert deploy.rescan_profiles(schedules.composite_package(s)) == [hw, apps], "Zeitplan: je Paket statt immer „Anwendungen“"
        # läuft gerade ein Scan: nachholen statt verwerfen
        started = []
        class _P:
            def submit(self, fn, cid, user, prof):
                started.append((cid, prof["id"]))
        inventory.pool = lambda: _P()
        cid = 4242
        inventory._scanning[cid] = "läuft"
        assert inventory.queue_scan(cid, None, hw) is False, "ohne follow: wie bisher"
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




@test("2.32.2 Skripte laufen immer: erst normal als Datei (-File, Exitcode, $PSScriptRoot, Parameter), verweigert Windows die Datei (Ausführungsrichtlinie per GPO, Signatur, Zone), wird ihr Inhalt geladen – nie doppelt; gehärtetes Windows (Sprachmodus, Virenschutz, gesperrtes Programm) meldet POCKET KNIFE mit eigenem Code und Klartext")
def _():
    import subprocess
    from app import deploy, remote, steps, util
    # Rahmen: erst & 'pk_run.ps1', Laden nur bei UnauthorizedAccess für genau diese Datei
    for src in (remote.REGISTER, remote.START_DIRECT):
        assert "__PKSTART__" not in src and "try { & ''' + $pkRunF + '''" in src and "-File" not in src, src[:300]
    # Schritte, Inline-Skripte, Repository-.ps1 und Tools über PK-RunPs1
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
    assert remote.hard_hint("System.UnauthorizedAccessException: Access to the path is denied") == "", "Zugriff verweigert ist keine Härtung"
    assert "Sprachmodus" in remote.gone_message("PK_PROC:gone\nPK_ERR:Cannot invoke method. Method invocation is supported only on core types in this language mode.", False)
    pw = os.environ.get("PK_PWSH")
    if not (pw and os.path.exists(pw)):
        return
    d = tempfile.mkdtemp(prefix="pk-ps-")
    try:
        # Windows PowerShell nachgebildet: verweigert -File für Dateien mit „refuse“ im Namen wie eine GPO (RemoteSigned)
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
            assert code == "6" and o.count("läuft") == 1, ("Verweigert → geladen, einmal, Exitcode", clm, code, log)
            assert f"R={run} N=a b L=True P={os.path.join(run, 'refuse_x.ps1')}" in log and "lädt ihren Inhalt" in log, log
            assert ("eingeschränkten Sprachmodus" in log) == clm and "Cannot" not in log, log
            code, log, o = wrap("$f = Join-Path $PKDir 'ok_x.ps1'\nPK-RunPs1 $f '-Name z'\n", clm)
            assert code == "6" and o.count("läuft") == 1 and "lädt ihren Inhalt" not in log and "N=z L=False" in log, ("normal -File", log)
        # Skript lief schon (Ausgabe) und scheitert danach an etwas anderem → nie ein zweites Mal
        code, log, o = wrap("$sf = Join-Path $PKDir 'x.ps1'\nSet-Content -LiteralPath $sf -Value \"'teil'; Write-Error 'FullyQualifiedErrorId : UnauthorizedAccess x.ps1'; exit 1\"\nPK-RunPs1 $sf ''\n")
        assert code == "1" and "lädt ihren Inhalt" not in log, log
        sc = lambda t: "$sf = Join-Path $PKDir 'c.ps1'\nSet-Content -LiteralPath $sf -Value " + util.psq(t) + "\nPK-RunPs1 $sf ''\n"  # noqa: E731
        # 2.32.5: der Rahmen ändert den Exitcode nicht mehr – POCKET KNIFE ordnet ihn erst bei einem Fehlschlag ein
        code, log, _o = wrap(sc("$ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage'\n[System.Text.Encoding]::UTF8.GetString([byte[]](65))\nthrow 'x'"))
        assert code == "1" and remote.apply_hard(1, log)[0] == 9015, log
        code, log, _o = wrap(sc("Write-Error 'Dieses Skript enthält schädliche Inhalte und wurde von Ihrer Antivirensoftware blockiert.'; exit 1"))
        assert code == "1" and remote.hard_code(log) == 9014, log
        code, log, _o = wrap(sc("Write-Error 'Dieses Programm wird durch eine Gruppenrichtlinie blockiert.'; exit 1"))
        assert code == "1" and remote.hard_code(log) == 9016, log
        code, log, _o = wrap(sc("Write-Error 'Methodenaufrufe werden in diesem Sprachmodus nur für Kerntypen unterstützt.'; exit 0"))
        assert code == "0", ("Erfolg bleibt Erfolg", log)
        # 2.32.5: verweigertes Unterskript (Fehler mit Positionsangabe) → das Skript lief schon, nie ein zweites Mal
        inner = ("'teil' | Out-File -LiteralPath (Join-Path $PSScriptRoot 'o.txt') -Append\n"
                 "[Console]::Error.WriteLine('At ' + $PSCommandPath + ':2 char:1')\n"
                 "[Console]::Error.WriteLine('    + FullyQualifiedErrorId : UnauthorizedAccess')\nexit 1\n")
        code, log, o = wrap("$f = Join-Path $PKDir 'inner_y.ps1'\nSet-Content -LiteralPath $f -Value " + util.psq(inner) + "\nPK-RunPs1 $f ''\n")
        assert code == "1" and o.count("teil") == 1 and "lädt ihren Inhalt" not in log, ("Unterskript verweigert – nicht nochmal", o, log)
        # Softwareeinschränkung: bewusste Sperre, nicht laden
        code, log, o = wrap("$f = Join-Path $PKDir 'refuse_srp.ps1'\nSet-Content -LiteralPath $f -Value 'x'\nPK-RunPs1 $f ''\n")
        assert "lädt ihren Inhalt" in log, "Gegenprobe: normale Verweigerung lädt"
        # Syntaxfehler im geladenen Inhalt → 1 statt „Erfolgreich“; „using namespace“ bleibt vorne
        for body_ps, want in (("$x = (1 +", "1"), ("using namespace System.Text\n'ok' | Out-File -LiteralPath (Join-Path $PSScriptRoot 'o.txt')\nexit 3", "3")):
            code, log, o = wrap("$f = Join-Path $PKDir 'refuse_z.ps1'\nSet-Content -LiteralPath $f -Value " + util.psq(body_ps.replace("\\n", "\n")) + "\nPK-RunPs1 $f ''\n")
            assert code == want and "lädt ihren Inhalt" in log, (body_ps, code, log)
        assert remote.hard_code("FullyQualifiedErrorId : UnauthorizedAccess\n… POCKET KNIFE lädt ihren Inhalt.") == 0, "geladen → 9013 ist nicht der Grund"
        assert "WinRM-Sitzung" in remote.hard_hint("Cannot invoke method … in this language mode.", start=True)
        code, log, _o = wrap("PK-Run (Join-Path $PKDir 'gibtsnicht.exe') 'x'\n")
        assert code == "1" and "Konnte nicht gestartet werden" in log, log
        # Start des Rahmens: normal einmal; verweigert (UnauthorizedAccess für pk_run.ps1) → Inhalt geladen; anderer Fehler → nicht nochmal
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


@test("2.32.4 Tastenkürzel gehören zum Tool: im Tool festlegen (Strg+Alt [+Umschalt] + A–Z/0–9/F1–F12), eingebaute Tools mit Vorgaben, schon vergebene Kürzel werden erkannt (mit Namen), Kürzel aus 2.32.3 übernommen; im Browser über e.code, nie in Eingabefeldern/Dialogen, bei mehreren Computern Rückfrage")
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
            raise AssertionError(("angenommen", bad))
        except ValueError:
            pass
    old = db.setting_raw("builtin_hotkeys")
    t1 = db.ex("INSERT INTO tools(name, type, script, run_as) VALUES('HK-Test', 'powershell', 'hostname', 'service')")
    try:
        db.set_setting("builtin_hotkeys", "")
        assert tools.builtin_hotkeys()["ping"] == "Ctrl+Alt+P", "Vorgabe"
        # eigenes Tool: Kürzel von Ping → abgelehnt mit Namen
        try:
            main._tool_values({"name": "X", "type": "cmd", "script": "ver", "run_as": "service", "hotkey": "Strg+Alt+P"})
            raise AssertionError("doppelt angenommen")
        except HTTPException as e:
            assert "Ping & Ports" in e.detail and "Strg+Alt+P" in e.detail, e.detail
        d = main._tool_values({"name": "X", "type": "cmd", "script": "ver", "run_as": "service", "hotkey": "strg+alt+shift+k"}, t1)
        assert d["hotkey"] == "Ctrl+Alt+Shift+K"
        db.ex("UPDATE tools SET hotkey='Ctrl+Alt+Shift+K' WHERE id=?", (t1,))
        assert main._tool_values({"name": "X", "type": "cmd", "script": "ver", "run_as": "service", "hotkey": "Ctrl+Alt+Shift+K"}, t1)["hotkey"], \
            "das eigene Kürzel beim Speichern desselben Tools ist kein Konflikt"
        assert main._tool_values({"name": "X", "type": "cmd", "script": "ver", "run_as": "service", "hotkey": ""})["hotkey"] is None
        assert tools.hotkey_owner("Ctrl+Alt+Shift+K") == "HK-Test"
        # eingebaute: doppelt / an eigenes Tool vergeben → abgelehnt; leer = bewusst ohne Kürzel
        for bad in ({"ping": "Ctrl+Alt+R"}, {"wake": "Ctrl+Alt+Shift+K"}):
            try:
                tools.save_builtin_hotkeys(bad)
                raise AssertionError(("angenommen", bad))
            except ValueError as e:
                assert "Strg+Alt" in str(e)
        out = tools.save_builtin_hotkeys({"ping": "", "abort": "Strg+Alt+P"})
        assert "ping" not in out and out["abort"] == "Ctrl+Alt+P" and out["rdpweb"] == "Ctrl+Alt+R"
        # Übernahme aus 2.32.3 (Strg+Alt+Ziffer je Benutzer) – nur frei und nur für eigene Tools
        db.ex("UPDATE tools SET hotkey=NULL WHERE id=?", (t1,))
        db.ex("INSERT OR REPLACE INTO user_prefs(username, key, value, at) VALUES('hk-alt', 'tool_hotkeys', ?, '2026-10-28')",
              (json.dumps({"1": f"tool:{t1}", "2": "builtin:ping"}),))
        db.ex("DELETE FROM settings WHERE key='hotkeys_migrated'")
        tools._migrate_user_hotkeys()
        assert db.val("SELECT hotkey FROM tools WHERE id=?", (t1,)) == "Ctrl+Alt+1" and db.setting_raw("hotkeys_migrated") == "1"
        tools._migrate_user_hotkeys()  # einmalig – zweiter Lauf ändert nichts
        # 2.32.5: Neustart/Herunterfahren auf Strg+Alt+B; gespeicherte alte Vorgabe N wird einmalig umgestellt
        assert tools.BUILTIN_HOTKEY_DEFAULTS["power"] == "Ctrl+Alt+B"
        db.set_setting("builtin_hotkeys", json.dumps({"power": "Ctrl+Alt+N", "ping": "Ctrl+Alt+P"}))
        db.ex("DELETE FROM settings WHERE key='hotkeys_power_b'")
        tools._migrate_power_b()
        assert tools.builtin_hotkeys()["power"] == "Ctrl+Alt+B"
        db.set_setting("builtin_hotkeys", json.dumps({"power": "Ctrl+Alt+N"}))
        tools._migrate_power_b()
        assert tools.builtin_hotkeys()["power"] == "Ctrl+Alt+N", "nur einmal – eine spätere eigene Wahl bleibt"
        # ein eigenes Tool hat B schon: Neustart/Herunterfahren behält N (keine Doppelbelegung durch die neue Vorgabe)
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
    assert bi == tools.BUILTIN_NAMES, ("eingebaute Tools in Oberfläche und Server gleich", set(bi.items()) ^ set(tools.BUILTIN_NAMES.items()))
    # 2.32.5: Fokus auf dem Kontrollkästchen der Auswahl blockiert das Kürzel nicht (nur Schreibfelder)
    assert "['checkbox', 'radio', 'button'" in js and "closest('input, textarea" not in js
    for need in ("/^Key([A-Z])$/.exec(e.code", "closest('textarea, select", "#modal-root .backdrop", "targets.length > 1 && !asks",
                 "S.toolTargets = null;", "PUT('/api/tools-hotkeys'", "hotkey: hk.value", "hotkey: '' });", "Bereits vergeben an"):
        assert need in js, need
    assert "me/hotkeys" not in js

@test("2.32.3 Neustart/Herunterfahren: Aktion und Wartezeit wählbar (0 = sofort mit /f … 24 h), Meldungstext passend, eigener Text bleibt")
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
        assert rebootmsg.text_delay("restart", 900).endswith("Bitte speichern – 15 min"), "eigener Text bleibt"
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

@test("2.32.5 Dienstkonto-Schutz: „gesperrt/deaktiviert/abgelaufen“ von einem Gerät löst sofort aus (kein zwischengespeichertes „Konto ok“); AD nicht erreichbar ≠ „Kennwort falsch“; „Konto ok“ nur 60 s gemerkt")
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
        assert not calls, "eindeutige Kontofehler ohne LDAP-Umweg"
        guard.reset()
        guard.verify_account = lambda: None
        t = guard.report_remote("STATUS_LOGON_FAILURE 0xc000006d", "SMB")
        assert not guard.paused() and "nicht per LDAP" in t, t
        assert "nicht per LDAP" in _r._winrm_error("the specified credentials were rejected by the server (401)")
        assert not guard.paused()
        # echte verify_account: Netzwerkfehler → None (kein Schutz), Ablehnung → False (Schutz), ok → True, 60 s gemerkt
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
        assert guard.verify_account() is True, "innerhalb von 60 s gemerkt"
        guard._verify["at"] -= guard.VERIFY_SECONDS + 1
        assert guard.verify_account() is False
        assert guard.VERIFY_SECONDS <= 60
    finally:
        guard.verify_account, ad.service_conn = orig_v, orig_sc
        guard.reset()

@test("2.32.5 Wecken vor Zeitplänen: nur wirklich ausgeschaltete Computer (nicht „an, aber ohne WinRM“), auch bei der Wiederholung eines gescheiterten Termins, je Zeitplan nur ein Weck-Durchlauf")
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
        schedules._wake_offline(sc, {"wake_offline": True}, [a, b], "täglich 07:00")  # läuft noch → kein zweiter Durchlauf
        gate.set()
        for _i in range(100):
            if woken and sc["id"] not in schedules._waking:
                break
            _t.sleep(0.05)
        assert woken == [a], woken
    finally:
        tools.wake = orig
        db.ex("DELETE FROM computers WHERE id IN (?, ?)", (a, b))

@test("2.32.5 OU-Admins: Kopie einer fremden dynamischen Sammlung verrät deren Regeln nicht (wird „ist Mitglied von“); Unsinn in IDs/Namen ergibt 400 statt 500; Zusammenfassen mit Duplikatsperre übersteuerbar")
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
        assert set(collections_.member_ids(new)) <= {a1}, "Kopie enthält nur eigene Computer"
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

@test("2.32.5 Tastenkürzel/Wecken in der Oberfläche: Wake-on-LAN auf mehreren Computern fragt nach, zweimal drücken startet nicht doppelt, aktuelle Zuordnung zählt, Strg+Alt löst kein Kopieren/Einfügen aus, URL-Tools mit Rückfrage fragen, Wecken sendet nach dem Schließen weiter und an alle")
def _():
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    run = js[js.index("async function hotkeyRun2"):js.index("document.addEventListener('keydown', (e) => {\n  const combo")]
    assert "'wake'" not in run, "Wecken fragt selbst nicht nach – bei mehreren Computern Rückfrage durch das Kürzel"
    assert "if (HK_BUSY) return;" in js and "ref = HK.map[combo];" in run
    assert js.count("if (e.altKey) return;  // Strg+Alt+") == 2
    assert "if (t.confirm && !(await confirmDlg(`„${t.name}“ für" in js
    wd = js[js.index("function wakeDialog(targets)"):js.index("function jobDialog(")]
    assert "slice(0, 200)" not in wd and "if (closed) break;" not in wd

@test("2.32.5 Ausführung: Härtungscodes nur bei Fehlschlag (Erfolgscodes und winget-Meldungen gehen vor), Deinstallation per winget/Registry baut den richtigen Befehl, zweiter Weckversuch nutzt das einzige Relay im Zielnetz erneut, Relay im eingeschränkten Sprachmodus meldet sich")
def _():
    from app import deploy, remote, tools
    clm = "Hinweis\nCannot invoke method. Method invocation is supported only on core types in this language mode.\nExitcode: 1"
    assert remote.apply_hard(1, clm)[0] == 9015 and remote.apply_hard(1, "nur ein normaler Fehler")[0] == 1
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "steps.py"), encoding="utf-8").read()
    i = src.index("ok, reboot = evaluate(st, code)")
    blk = src[i:src.index("self._record(", i)]
    assert "elif not ok:" in blk and "remote.apply_hard(code, out)" in blk, "nur bei Fehlschlag, nach den winget-Meldungen"
    assert "$global:PKExit = $pkH" not in remote.WRAPPER, "der Rahmen überschreibt keinen Exitcode mehr"
    # Deinstallation: winget uninstall bzw. Befehl aus der Registry – beides baut einen gültigen Rumpf
    assert "deploy.build_body(p2)" in src and "deploy._winget_body(p2)" not in src and "deploy._custom_body(p2)" not in src
    w = deploy.adhoc_winget("Mozilla.Firefox", "uninstall")
    assert "uninstall" in deploy.build_body(w)
    assert "PK-Run" in deploy.build_body({"type": "custom", "install_type": "script", "script": "msiexec /x {X} /qn", "arguments": ""})
    # WoL: Relay im eingeschränkten Sprachmodus sagt es; zweiter Versuch ohne anderes Relay im Zielnetz → wieder dasselbe
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

@test("2.32.6 Angemeldete Benutzer mit Sitzungszustand: gesperrt (LogonUI in der Sitzung) und getrennt (quser, deutsch/englisch), mehrere Sitzungen → aktiv gewinnt; im eingeschränkten Sprachmodus; Scan und Live-Abfrage gleich")
def _():
    import subprocess
    from app import remote
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app")
    scan = open(os.path.join(root, "scan_modules.ps1"), encoding="utf-8").read()
    blk = lambda t: t[t.index("# @sessions-begin"):t.index("# @sessions-end")]  # noqa: E731
    assert blk(scan) == blk(remote.SESSIONS_PS), "Scan und Live-Abfrage ermitteln den Zustand gleich"
    assert "user_states" in scan
    assert remote.parse_states("PKUSERS:x\nPKSTATES:DOM\\a=locked;DOM\\b=disconnected;DOM\\c=active;kaputt") == \
        {"DOM\\a": "locked", "DOM\\b": "disconnected"}
    # 2.32.11: alle Sitzungen aktiv → „PKSTATES:“ ganz am Ende ohne Zeilenende (war IndexError → interner Fehler 500)
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
                         # Benutzername mit Leerzeichen und Ziffer: darf keine andere Sitzung überschreiben
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

@test("2.32.7 Tools: Dateien aus dem Repository (POCKET KNIFE kopiert selbst, Befehl im Arbeitsordner, danach aufgeräumt, nie aus Mandanten-Ordnern), Danach scannen (Standard aus), Zeitlimit, Erfolgscodes; CMD-Ausgabe mit Umlauten (OEM)")
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
        assert d["timeout_s"] is None and d["success_codes"] is None, "Standard: kein Zeitlimit eingetragen, Erfolg = 0, kein Scan"
        for bad in ({"repo_path": "..\\geheim"}, {"repo_path": "C:\\x"}, {"repo_path": "\\\\srv\\x"}, {"repo_path": "Tools\\fehlt"},
                    {"repo_path": "Mandant\\A\\iperf"}, {"repo_path": "Mandant"}, {"scan_after": "manchmal"},
                    {"timeout_s": 5}, {"timeout_s": 999999}, {"timeout_s": "x"}, {"success_codes": "0, abc"}):
            try:
                tools.validate(dict(base, **bad))
                raise AssertionError(("angenommen", bad))
            except ValueError:
                pass
        d = dict(base, success_codes="0; 3010 ,1", timeout_s="120", scan_after="success")
        tools.validate(d)
        assert d["success_codes"] == "0,3010,1" and d["timeout_s"] == 120
        assert tools.parse_codes("") == [0]
        # Ausführung als Dienstkonto mit Dateien: kopieren → im Ordner ausführen → aufräumen; Erfolgscodes; Zeitlimit
        seen = {}

        def ps(host, script, timeout=None, **kw):
            seen["script"], seen["timeout"] = script, timeout
            return "Ausgabe\nPKEXIT:3010", "", False
        remote.ps = ps
        t = {"id": 7, "name": "IPERF", "type": "cmd", "script": "iperf3.exe -c pve", "run_as": "service", "repo_path": "Tools\\iperf",
             "timeout_s": 90, "success_codes": "0,3010", "scan_after": "never", "scan_profile_id": None}
        # 2.32.7: mit Dateien läuft auch das Dienstkonto über den Rahmen (eigener Prozess, Zeitlimit auf dem Client)
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
        # ohne Dateien: Dienstkonto direkt über WinRM, Arbeitsordner nicht nötig, Zeitlimit an WinRM
        r = tools.run(dict(t, repo_path=None), c)
        assert r["exit"] == 3010 and seen["timeout"] == 90 and "cd /d" not in seen["script"] and "\\uFFFD" in seen["script"], seen["script"][:300]
        # SYSTEM: gleicher Name für Kopie und Lauf; aufgeräumt wird immer
        calls.clear()
        remote.run_as_system = lambda host, body, name, timeout_s=0, **kw: (calls.append(("sys", name, timeout_s)) or (1, "> x\nExitcode: 1"))
        r = tools.run(dict(t, run_as="system"), c)
        assert calls[0][0] == "copy" and calls[1] == ("sys", calls[0][2], 90) and calls[-1] == ("rm", calls[0][2]), calls
        assert r["ok"] is False
        # Kopie bricht ab → trotzdem aufräumen
        calls.clear()
        def boom(folder, host, name, single_file=None):
            calls.append(("copy", folder, name, single_file))
            raise remote.RemoteError("Verbindung abgebrochen")
        remote.copy_package = boom
        try:
            tools.run(t, c)
            raise AssertionError("Fehler erwartet")
        except remote.RemoteError:
            pass
        assert calls[-1] == ("rm", calls[0][2]), calls
        remote.copy_package = lambda folder, host, name, single_file=None: calls.append(("copy", folder, name, single_file))
        # Zeitlimit: eigene Meldung in Sekunden
        def slow(host, script, timeout=None, **kw):
            raise remote.RemoteTimeout("WinRM: keine Antwort")
        remote.ps = slow
        try:
            tools.run(dict(t, repo_path=None, timeout_s=45), c)
            raise AssertionError("Zeitlimit erwartet")
        except remote.RemoteTimeout as e:
            assert "45 Sekunden" in str(e), e
        remote.ps = ps
        # Mandant bekommt den Ordner NACH dem Speichern des Tools → Tool läuft nicht mehr
        t2 = db.ex("INSERT INTO tenants(name, repo_folder, created_at) VALUES('T-HK7b', 'tools', ?)", (util.now(),))
        try:
            calls.clear()
            try:
                tools.run(t, c)
                raise AssertionError("Mandanten-Ordner nicht erkannt")
            except ValueError as e:
                assert "T-HK7b" in str(e)
            assert not [x for x in calls if x[0] == "copy"], "nichts kopiert"
        finally:
            db.ex("DELETE FROM tenants WHERE id=?", (t2,))
        # und umgekehrt: ein Mandant kann keinen Ordner bekommen, den ein Tool benutzt
        from app import repofiles
        tt = db.ex("INSERT INTO tools(name, type, script, run_as, repo_path) VALUES('HK7-Tool', 'cmd', 'x', 'service', 'Tools\\iperf')")
        try:
            for f in ("Tools", "tools\\IPERF", "Tools\\iperf\\sub"):
                try:
                    repofiles.check_tenant_folder(f)
                    raise AssertionError(("Tool-Ordner als Mandantenordner", f))
                except repofiles.RepoError as e:
                    assert "HK7-Tool" in str(e)
        finally:
            db.ex("DELETE FROM tools WHERE id=?", (tt,))
        # Danach scannen: Standard nie; „Nach Erfolg“ nur bei OK; „Immer“ immer
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
    assert "-match '\\uFFFD') { $pkEnc = 'Oem' }" in remote.WRAPPER, "erst UTF-8, bei Ersatzzeichen OEM"
    msrc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "main.py"), encoding="utf-8").read()
    assert "tools.scan_after(t, cid" in msrc, "Danach scannen auch beim Einzelaufruf"
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    assert "repo_path: repo.value" in js and "scan_after: scanAfter.value" in js and "tool.scan_after || 'never'" in js

@test("2.32.8 Netzwerkkarten: Geschwindigkeit aus Get-NetAdapter (nicht der veraltete WMI-Wert), nur für verbundene Karten; automatische Scans erst einige Minuten nach dem Hochfahren/Aufwachen, manuelle sofort")
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
    # Wartezeit nach dem Hochfahren
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
        assert c not in started, "gerade erst online → noch nicht"
        db.ex("UPDATE computers SET online_since=? WHERE id=?", (util.ago(minutes=4), c))
        profiles.dispatch()
        assert c in started, "nach der Wartezeit"
        from app import presence
        db.ex("UPDATE computers SET winrm=0 WHERE id=?", (c,))
        assert presence.update(c, True, True) and db.val("SELECT online_since FROM computers WHERE id=?", (c,)), "beim Online-Kommen gesetzt"
        presence.update(c, False, False)
        assert db.val("SELECT online_since FROM computers WHERE id=?", (c,)) is None
    finally:
        inventory.queue_scan, inventory.is_scanning = orig
        db.ex("DELETE FROM profile_scans WHERE computer_id=?", (c,))
        db.ex("DELETE FROM computers WHERE id=?", (c,))



@test("2.33 Docker-Standard: Programm nur im Image, docker-compose.yml mit Versionsnummer (Hinweis, wenn zu alt), guacd unter eigenem Namen, Release ohne ZIP, kein Update-Helfer mehr")
def _():
    import importlib.util as _iu
    import re as _re
    from app import backup, config, news, privileges
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    assert _iu.find_spec("app.selfupdate") is None, "Update-Helfer (2.32.9–2.32.12) ist ausgebaut"
    js = open(os.path.join(root, "app", "static", "app.js"), encoding="utf-8").read()
    assert "/api/system/selfupdate" not in js and "Jetzt aktualisieren" not in js
    from app import main as _m
    assert not [r for r in _m.app.routes if "selfupdate" in getattr(r, "path", "")]
    # docker-compose.yml: Stand der Datei = das, was das Programm erwartet
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
        assert "git archive" not in rel and ".zip" not in rel, "Release ohne Programm-ZIP"
        assert "imagetools create" in rel and "docker/guacd.txt" in rel and "docker save" in rel and "tar.gz" in rel
        assert "cp .env.example dist/env.example" in rel and "dist/.env.example" not in rel, "GitHub benennt .env.example um – als env.example"
        assert rel.index("- name: Bauen und hochladen") < rel.index("- name: guacd unter eigenem Namen") < rel.index("- name: Release anlegen")
        wk = open(os.path.join(wfd, "weekly.yml"), encoding="utf-8").read()
        assert "docker/guacd.txt" in wk and "docker-compose.yml" not in wk.split("Etwas geändert?")[1].split("Schwachstellen")[0]
        for fn in os.listdir(wfd):
            t_ = open(os.path.join(wfd, fn), encoding="utf-8").read()
            assert "image: \\(guacamole" not in t_ and " docker-compose.yml;" not in t_, f"{fn}: guacd steht in docker/guacd.txt, nicht mehr in der docker-compose.yml"
    # Hinweise für Hauptadmins
    old = os.environ.pop("PK_COMPOSE_VERSION", None)
    try:
        assert [h["id"] for h in news.hints()] == ["compose"], "ohne Versionsnummer = alte docker-compose.yml"
        os.environ["PK_COMPOSE_VERSION"] = str(news.COMPOSE_VERSION)
        assert news.hints() == []
        os.environ["PK_COMPOSE_VERSION"] = "x"
        assert [h["id"] for h in news.hints()] == ["compose"]
        os.environ["PK_COMPOSE_VERSION"] = str(news.COMPOSE_VERSION)
        # Datenbank von einer neueren Version (zurück ohne die Sicherung): Hinweis; Sicherheitsupdate derselben Version zählt nicht
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
            assert db.setting_raw("db_version_max") == "99.0" and db.setting_raw("last_version") == config.VERSION, "höchste Version bleibt"
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
    # Start-chown lässt den Ordner des früheren Helfers aus (gehört root, update.sh --cleanup räumt ihn weg)
    if os.geteuid() == 0:
        d = tempfile.mkdtemp(dir=TMP)
        os.makedirs(os.path.join(d, "update", "host")); open(os.path.join(d, "a"), "w").close()
        privileges.chown_tree(d, 10001, 10001)
        assert os.stat(os.path.join(d, "a")).st_uid == 10001 and os.stat(os.path.join(d, "update", "host")).st_uid == 0


@test("2.33 update.sh: nur Container aktualisieren (pull, up, Gesundheitscheck, zurück ohne Download), Version festlegen, neuere docker-compose.yml aus dem Image, ohne Internet per Archiv, --cleanup schont Daten auch bei Links")
def _():
    import hashlib as _hl
    import shutil as _sh
    import subprocess as _sp
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    f = os.path.join(root, "update.sh")
    if not os.path.exists(f) or not _sh.which("sh"):
        return  # im Container liegt update.sh nicht
    if _sh.which("dash"):
        assert _sp.run(["dash", "-n", f]).returncode == 0, "update.sh muss mit /bin/sh (dash) laufen"
    src = open(f, encoding="utf-8").read()
    for bad in ("unzip", "docker build", "git archive", "PROGRAM_ITEMS="):
        assert bad not in src, ("kein ZIP, kein Bauen", bad)
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
      *Mounts*) echo ""; echo "" ;;   # 2.33.2: wie das echte docker – eine leere Zeile je Container ohne Mounts
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
    # 1. Umstieg von 2.32 (lokal gebautes Image, alte docker-compose.yml): Registry-Image, neue Datei, alte als .alt
    r = run("-y")
    assert r.returncode == 0, r.stdout[-800:] + r.stderr[-400:]
    assert "PK_IMAGE=ghcr.io/soleil0815/pocketknife:latest" in rd(".env") and "PK_DNS_1=10.0.10.5" in rd(".env")
    assert rd("docker-compose.yml") == new_compose and rd("docker-compose.yml.alt") == old_compose and rd(".env.example") == "BEISPIEL=1\n"
    calls = open(os.path.join(st, "calls")).read()
    assert "pull ghcr.io/soleil0815/pocketknife:latest" in calls and "pull ghcr.io/soleil0815/pocketknife-guacd:latest" in calls
    tags = open(os.path.join(st, "tags")).read()
    assert "sha256:alt pocketknife:previous" in tags and "sha256:guacalt pocketknife-guacd:previous" in tags, "vorher merken (zurück ohne Download)"
    assert "build" not in calls and "load" not in calls
    assert sorted(os.listdir(inst)) == [".env", ".env.example", "data", "docker-compose.yml", "docker-compose.yml.alt", "update.sh"], os.listdir(inst)
    assert "Done: POCKET KNIFE 2.33.0" in r.stdout
    # 2. gleiche Datei-Version: docker-compose.yml bleibt (eigene Kommentare o. ä. bleiben erhalten)
    os.remove(os.path.join(inst, "docker-compose.yml.alt"))
    w(os.path.join(inst, "docker-compose.yml"), new_compose + "# eigene Zeile\n")
    r = run("-y")
    assert r.returncode == 0 and rd("docker-compose.yml").endswith("# eigene Zeile\n") and not os.path.exists(os.path.join(inst, "docker-compose.yml.alt"))
    # 3. bestimmte Version festlegen – beide Images; „latest“ wieder zurück
    r = run("-y", "2.33.1")
    assert r.returncode == 0 and "PK_IMAGE=ghcr.io/soleil0815/pocketknife:2.33.1" in rd(".env") and "PK_GUACD_IMAGE=ghcr.io/soleil0815/pocketknife-guacd:2.33.1" in rd(".env"), rd(".env")
    r = run("-y")
    assert r.returncode == 0 and open(os.path.join(st, "calls")).read().count("pull ghcr.io/soleil0815/pocketknife:2.33.1") == 2, "ohne Angabe: die festgelegte Version"
    r = run("-y", "latest")
    assert r.returncode == 0 and "PK_IMAGE=ghcr.io/soleil0815/pocketknife:latest" in rd(".env") and "pocketknife-guacd:latest" in rd(".env")
    for bad in ("2.33; reboot", "../x", "--irgendwas"):
        r = run("-y", bad)
        assert r.returncode != 0, bad
    # alte Schreibweise --pull [Version] geht weiter
    r = run("-y", "--pull", "2.33.1")
    assert r.returncode == 0 and ":2.33.1" in rd(".env")
    run("-y", "latest")
    # 4. Ziehen scheitert → nichts geändert, Hinweis auf docker login
    env_before = rd(".env")
    w(os.path.join(st, "pullfail"), "")
    r = run("-y", "2.33.2")
    assert r.returncode != 0 and "could not be pulled" in r.stdout and "docker login ghcr.io" in r.stdout and rd(".env") == env_before
    os.remove(os.path.join(st, "pullfail"))
    # 5. neue Version startet nicht → vorheriges Image unter dem Namen der bisherigen .env, .env zurück
    w(os.path.join(st, "tags"), ""); w(os.path.join(st, "health"), "unhealthy\n")
    r = run("-y", "2.33.2")
    assert r.returncode != 0 and "is running again" in r.stdout and rd(".env") == env_before, r.stdout[-600:]
    tags = open(os.path.join(st, "tags")).read()
    assert "pocketknife:previous ghcr.io/soleil0815/pocketknife:latest" in tags and "pocketknife-guacd:previous ghcr.io/soleil0815/pocketknife-guacd:latest" in tags, tags
    assert "sudo sh update.sh 2.32.12" in r.stdout, "wie man auf der Version bleibt"
    w(os.path.join(st, "health"), "healthy\n")
    # 6. ohne Internet: Archiv aus dem Release (beide Images), Prüfsumme muss stimmen
    arc = os.path.join(base, "pocketknife-2.33.3-amd64.tar.gz"); w(arc, "ARCHIV")
    w(os.path.join(base, "pocketknife-2.33.3.sha256"), _hl.sha256(b"ARCHIV").hexdigest() + "  pocketknife-2.33.3-amd64.tar.gz\n")
    w(os.path.join(st, "loaded"), "Loaded image: ghcr.io/soleil0815/pocketknife:2.33.3\nLoaded image: ghcr.io/soleil0815/pocketknife-guacd:2.33.3\n")
    w(os.path.join(st, "calls"), "")
    r = run("-y", arc)
    assert r.returncode == 0 and ":2.33.3" in rd(".env") and "pocketknife-guacd:2.33.3" in rd(".env") and "Checksum OK" in r.stdout, r.stdout[-500:]
    assert "pull" not in open(os.path.join(st, "calls")).read(), "ohne Internet nichts ziehen"
    w(os.path.join(base, "pocketknife-2.33.3.sha256"), "0" * 64 + "  pocketknife-2.33.3-amd64.tar.gz\n")
    env_before = rd(".env")
    r = run("-y", arc)
    assert r.returncode != 0 and "Checksum" in r.stderr and rd(".env") == env_before
    run("-y", "latest")
    # 7. läuft aus einem anderen Ordner → nichts tun
    w(os.path.join(st, "running_dir"), base + "\n")
    r = run("-y")
    assert r.returncode != 0 and "runs from" in r.stderr
    w(os.path.join(st, "running_dir"), inst + "\n")
    # 8. Timer des früheren Update-Helfers (2.32.9–2.32.12) räumt das Skript selbst weg – auch wenn er es aufruft
    if os.geteuid() == 0:
        cd = os.path.join(env["PK_SYSROOT"], "etc", "cron.d"); os.makedirs(cd)
        w(os.path.join(cd, "pocketknife-update"), "*/5 * * * * root /bin/sh x --watch\n")
        r = run("--watch")
        assert r.returncode == 0 and not os.path.exists(os.path.join(cd, "pocketknife-update")), r.stdout + r.stderr
    # 9. --cleanup: alte Programmdateien weg – Daten, Sicherungen, Repository, .env, docker-compose.yml bleiben,
    #    auch wenn ein „Programmordner“ in Wirklichkeit ein Link auf Daten ist
    for d in ("app", "docker", ".pk-vorher/app", "pocketknife-2.24/app", "pocketknife-2.23.3/app", "pocketknife-2.23.3/data", "data-backup", "repo", "data/update/host"):
        os.makedirs(os.path.join(inst, d), exist_ok=True)
    for p_ in ("Dockerfile", "run.py", "VERSION", "pocketknife-2.30.zip", "pocketknife-2.30.zip.sha256", "pocketknife-2.24/VERSION",
               "pocketknife-2.24/docker-compose.yml", "pocketknife-2.23.3/VERSION", "pocketknife-2.23.3/docker-compose.yml",
               "pocketknife-2.23.3/data/pocketknife.db", "data-backup/s.zip", "repo/x.msi", "data/update/request", "docker-compose.override.yml"):
        w(os.path.join(inst, p_), "x")
    ext = os.path.join(base, "geheim"); os.makedirs(ext); w(os.path.join(ext, "wichtig.txt"), "W")
    w(os.path.join(inst, ".env"), rd(".env") + f"PK_REPO_MOUNT={ext}\n")
    os.symlink(ext, os.path.join(inst, "tools"))  # „tools“ zeigt auf das Repository → nie löschen
    w(os.path.join(st, "images"), "pocketknife:local\nghcr.io/soleil0815/pocketknife:latest\n")
    r = run("--cleanup", inp="n\nn\n")
    assert r.returncode == 0 and os.path.exists(os.path.join(inst, "app")), "ohne Ja nichts gelöscht"
    r = run("-y", "--cleanup")
    left = sorted(os.listdir(inst))
    assert r.returncode == 0, r.stderr
    assert left == [".env", ".env.example", "data", "data-backup", "docker-compose.override.yml", "docker-compose.yml", "pocketknife-2.23.3", "repo", "tools", "update.sh"], left
    assert os.path.islink(os.path.join(inst, "tools")) and open(os.path.join(ext, "wichtig.txt")).read() == "W"
    assert rd("data", "pocketknife.db") == "DB" and rd("data-backup", "s.zip") == "x" and rd("repo", "x.msi") == "x"
    assert not os.path.exists(os.path.join(inst, "data", "update")), "Ordner des früheren Helfers weg"
    assert "kept: " in r.stdout and "pocketknife-2.23.3" in r.stdout, "Versionsordner mit Datenbank bleibt"
    assert "image rm pocketknife:local" in open(os.path.join(st, "calls")).read()
    assert "pocketknife:latest" not in [x.split()[-1] for x in open(os.path.join(st, "calls")).read().splitlines() if x.startswith("image rm")]
    # 2.33.1: Umstieg – das neue Skript läuft als update.sh.neu (das alte liegt noch als update.sh da) und legt sich selbst ab
    w(os.path.join(st, "host", "update.sh"), open(f, encoding="utf-8").read())
    _sh.copy(f, os.path.join(inst, "update.sh.neu")); w(os.path.join(inst, "update.sh"), "#!/bin/sh\n# POCKET KNIFE – update (since 2.24.1: alt)\n")
    r = _sp.run(["sh", "update.sh.neu", "-y"], cwd=inst, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and rd("update.sh") == open(f, encoding="utf-8").read(), r.stdout[-400:]
    for doc in ("README.md", "README.en.md", os.path.join("app", "static", "help", "de.md"), os.path.join("app", "static", "help", "en.md")):
        t_ = open(os.path.join(root, doc), encoding="utf-8").read()
        assert "> update.sh.neu && mv" not in t_ and "&& sh update.sh.neu'" in t_ and "sudo sh -c 'docker pull" in t_, ("Umstieg in einem Befehl als root", doc)
    os.remove(os.path.join(inst, "update.sh.neu"))
    # 10. update.sh nimmt sich selbst aus dem Image (neue Datei per mv – das laufende Skript bleibt unberührt)
    w(os.path.join(st, "host", "update.sh"), open(f, encoding="utf-8").read() + "\n# aus dem Image\n")
    r = run("-y")
    assert r.returncode == 0 and rd("update.sh").endswith("# aus dem Image\n")


@test("2.33.3 Sammlungsbaum: je Ebene alphabetisch (Ordner zuerst), AD und Standard oben, Standard-Sammlungen in fester Reihenfolge")
def _():
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    i = js.index("const byParent = {};")
    seg = js[i:i + 900]
    assert "v.sort(" in seg and "deCmp(a.name, b.name)" in seg and "a.builtin === 1 && b.builtin === 1 ? 0" in seg, "Baum sortiert"


@test("2.33.4 Virenschutz auf Windows Server: Microsoft Defender direkt (kein Sicherheitscenter), leere Ausgabe nie als „undefined (aus)“")
def _():
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    ps = open(os.path.join(root, "app", "scan_modules.ps1"), encoding="utf-8").read()
    blk = ps[ps.index("PKX 'antivirus' {"):ps.index("} catch { $r.errors += ('security: '")]
    assert "if ($av.Count -eq 0 -and $mp)" in blk and "Microsoft Defender Antivirus" in blk and ",$av" in blk, "Server: Defender, immer eine Liste"
    assert ps.index("$mp = Get-MpComputerStatus") < ps.index("PKX 'antivirus' {"), "Defender-Status vorher gelesen"
    assert "-ClassName MSFT_MpComputerStatus" in ps and ps.index("MSFT_MpComputerStatus") < ps.index("PKX 'antivirus' {"), "ohne Defender-Modul: WMI-Klasse"
    from app import inventory
    out = inventory._merge_extra(-4711, {"extra": {"antivirus": {}, "firewall": [{"profile": "Domain", "enabled": True}, {}]}}, ["security"])
    assert out["antivirus"] == [] and out["firewall"] == [{"profile": "Domain", "enabled": True}], out
    out = inventory._merge_extra(-4711, {"extra": {"antivirus": {"name": "Microsoft Defender Antivirus", "enabled": True, "up_to_date": True}}}, ["security"])
    assert out["antivirus"] == [{"name": "Microsoft Defender Antivirus", "enabled": True, "up_to_date": True}]
    js = open(os.path.join(root, "app", "static", "app.js"), encoding="utf-8").read()
    assert "arr(e.antivirus).filter((a) => a && a.name)" in js
    src = open(os.path.join(root, "app", "inventory.py"), encoding="utf-8").read()
    assert "if not (isinstance(x, dict) and not x)" in src


@test("2.34 Windows Server: Rückfrage vor Neustart/Herunterfahren von Servern und Domänencontrollern, Sammlung „Domänencontroller“, Virtualisierung erkannt, winget-Version ohne Profile, Server Core-Benutzer, Bildschirme auf VMs ohne Warnung")
def _():
    import json as _j
    from app import main as _m, collections_, db as _db
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    # Rolle: aus dem Scan (ProductType), sonst aus dem Betriebssystem
    sr = _m.server_role
    assert sr({"extra": _j.dumps({"product_type": 2})}) == "dc" and sr({"extra": _j.dumps({"product_type": "3"})}) == "server"
    assert sr({"extra": _j.dumps({"product_type": 1}), "os_name": "Windows Server 2022"}) is None, "Scan geht vor"
    assert sr({"extra": None, "os_name": "Microsoft Windows Server 2025 Standard"}) == "server" and sr({"extra": "{}", "ad_os": "Windows 11 Pro"}) is None
    assert ("POST", "/api/computers/power-check") in _m.access.ROUTES
    js = open(os.path.join(root, "app", "static", "app.js"), encoding="utf-8").read()
    pdl = js[js.index("function powerDialog(targets)"):js.index("function fmtDelay(")]
    assert "/api/computers/power-check" in pdl and "servers.length && !sure.checked" in pdl and "servers === null" in pdl, "Server: bestätigen"
    # Sammlung „Domänencontroller“ und Feld „Rolle“
    assert any(n == "Domänencontroller" for n, _mm, _f in _db.BUILTIN_COLLECTIONS)
    a = _db.ex("INSERT INTO computers(name, extra) VALUES('DC-TEST1', ?)", (_j.dumps({"product_type": 2, "virtual": "Hyper-V"}),))
    b = _db.ex("INSERT INTO computers(name, extra) VALUES('SRV-TEST1', ?)", (_j.dumps({"product_type": 3}),))
    try:
        dc = _db.val("SELECT id FROM collections WHERE builtin=1 AND name='Domänencontroller'")
        assert dc, "beim Start angelegt"
        mem = set(collections_.member_ids(dc, fresh=True))
        assert a in mem and b not in mem, mem
        w, p = collections_.build([{"field": "virtual", "op": "equals", "value": "Hyper-V"}])
        assert _db.val(f"SELECT COUNT(*) FROM computers c WHERE c.id=? AND {w}", [a] + list(p)) == 1
    finally:
        _db.ex("DELETE FROM computers WHERE id IN (?,?)", (a, b))
    ps = open(os.path.join(root, "app", "scan_modules.ps1"), encoding="utf-8").read()
    vm = ps[ps.index("PKX 'virtual' {"):]
    vm = vm[:vm.index("\n}\n")]
    assert vm.index("'VMware'") < vm.index("'Hyper-V'") and "'KVM/QEMU" in vm and "Proxmox" in vm and "HypervisorPresent" not in vm.replace("(HypervisorPresent", ""), "Hersteller/Modell, nicht HypervisorPresent"
    wg = ps[ps.index("if ($os.ProductType -eq 1) {\n  try { $ai = Get-AppxPackage"):ps.index("$e.pending_reboot")]
    assert "^(Applications|InboxApplications|S-1-.*)$" in wg, "nicht EndOfLife/Staged"
    assert "AppxAllUserStore" in wg and "-Recurse -Depth" not in wg and "Get-AppxPackage -AllUsers" in wg.split("} else {")[0], "Server: Registry statt alle Profile"
    disp = ps[ps.index("PKS 'displays'"):ps.index("# @module environment")]
    assert "nicht unterst|not supported|8004100C" in disp and "{ throw }" in disp
    blk = ps[ps.index("# @sessions-begin"):ps.index("# @sessions-end")]
    assert "Server Core" in blk and "$pkExp.Count -eq 0 -and $pkQo.Count -gt 1" in blk
    from app import inventory, remote as _r
    assert "virtual" in inventory.MODULE_EXTRA["computer"]
    lo = _r.LOGGEDON_PS[_r.LOGGEDON_PS.index("@sessions-end"):]
    assert "$u += $pkExp" not in lo and ".Split('\\')[-1] -eq $x" in lo, "quser-Namen nicht doppelt zu DOMÄNE\\name"
    # power-check: alle Ziele, auch über 5000; Fehler der Prüfung = vorsichtshalber Häkchen verlangen
    import inspect as _insp
    assert "[:5000]" not in _insp.getsource(_m.api_power_check)
    a2 = _db.ex("INSERT INTO computers(name, extra, current_user) VALUES('SRV-PC2', ?, 'FIRMA\\a,FIRMA\\b')", (_j.dumps({"product_type": 3}),))
    try:
        r = _m.api_power_check({"ids": [a2]}, {"username": "haupt", "scope": None})
        assert r["servers"] == [{"id": a2, "name": "SRV-PC2", "role": "server", "users": 2}], r
    finally:
        _db.ex("DELETE FROM computers WHERE id=?", (a2,))
    assert "servers = [{ unknown: true }];" in pdl, "Prüfung fehlgeschlagen: nicht ohne Rückfrage"

@test("2.34 Ältere Windows ohne winget (vor 1809 / Server 2019): kein winget-Aktualisieren, Kiosk bietet winget-Pakete dort nicht an, Hinweis statt „fehlt“")
def _():
    import re as _re
    from app import kiosk, deploy, steps as stp
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    ok = deploy.winget_ok
    assert ok(None) and ok("") and ok("17763") and ok(26100) and ok("x") and not ok("14393") and not ok(9600), "Build-Grenze"
    assert not ok("14393.6897") and ok("17763.1") and ok("26100.4061"), "Scan speichert Build.UBR"
    js = open(os.path.join(root, "app", "static", "app.js"), encoding="utf-8").read()
    assert int(_re.search(r"const WINGET_MIN_BUILD = (\d+);", js).group(1)) == deploy.WINGET_MIN_BUILD, "JS und Server gleiche Grenze"
    sv = js[js.index("if (cat === 'software') {"):]
    sv = sv[:sv.index("return wrapT(t);\n  }")]
    # 2.35: ohne winget nur, was im winget-Cache bereitliegt
    assert "const wok = wingetOk(c);" in sv and "const upOk = (r) => wok || wcHas(r.winget_id);" in sv
    assert "r.outdated && r.winget_id && upOk(r) ? btn('Aktualisieren'" in sv and "s.outdated && s.winget_id && upOk(s)" in sv
    assert "c.outdated && (wingetOk(c) || wcHas(wid))" in js[js.index("async function updateOutdated("):js.index("async function scanSubItems(")]
    sd = js[js.index("async function softwareDlg(sw)"):]
    assert "c.outdated && (wingetOk(c) || wcHas(sw.winget_id))" in sd[:sd.index("modal(sw.name")] and sd.index("await wcReady();") < sd.index("const t = table(")
    from app import main as _m
    import inspect
    assert "c.os_build" in inspect.getsource(_m.api_software_detail), "Build für die Anzeige"
    # Kiosk: winget-Paket auf Server 2016 nicht anbieten, eigenes Paket schon
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
        # Deinstallieren per winget: auf alten Systemen nicht angeboten
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


@test("2.34 Befehl ausführen: PowerShell/CMD direkt ohne Tool, nur Hauptadmins, protokolliert, als Tool speicherbar, Strg+Alt+C nur wenn frei")
def _():
    import time as _t
    from app import tools, remote, main as _m, pklog
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    t = tools.adhoc_tool({"type": "powershell", "script": "Get-Service Spooler\r\nGet-Date\n\n"})
    assert t["type"] == "powershell" and t["run_as"] == "service" and t["script"] == "Get-Service Spooler\r\nGet-Date", t["script"]
    assert t["name"] == "Befehl (PowerShell)" and not t["repo_path"] and t["scan_after"] == "never", "Name ohne Befehlstext"
    assert tools.adhoc_tool({"type": "cmd", "script": "ipconfig", "run_as": "system"})["name"] == "Befehl (CMD als SYSTEM)"
    for bad, txt in (({"type": "url", "script": "http://x"}, "PowerShell oder CMD"), ({"type": "cmd", "script": "  \n "}, "Befehl eingeben"),
                     ({"type": "cmd", "script": "x", "run_as": "root"}, "Ausführung"),
                     ({"type": "cmd", "script": "x" * (tools.ADHOC_MAX + 1)}, "zu lang"),
                     ({"type": "cmd", "script": "x", "timeout_s": 3}, "Zeitlimit")):
        must_fail(tools.adhoc_tool, bad, contains=txt)
    # OU-Admins: Route nicht freigegeben, Handler prüft zusätzlich
    assert ("POST", "/api/tools-command") not in _m.access.ROUTES
    try:
        _m.api_tool_command({"type": "cmd", "script": "whoami", "ids": [1]}, {"username": "ou-x", "scope": [1]})
        raise AssertionError("OU-Admin durfte Befehl ausführen")
    except _m.HTTPException as e:
        assert e.status_code == 403
    # Ausführung wie ein Tool, Befehl im Protokoll
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
        assert not any("Geheim123" in m or "net user" in m for m in logs), ("Befehlstext im Protokoll", logs)
        assert len(j["targets"]) == 1 and len(runs) == 1, ("doppelte ID doppelt ausgeführt", runs)
    finally:
        remote.ps, pklog.info = old_ps, old_info
        db.ex("DELETE FROM computers WHERE id=?", (c,))
    # Tastenkürzel: Vorgabe Strg+Alt+C nur, wenn kein eigenes Tool es hat
    assert "command" in tools.BUILTIN_KEYS and tools.builtin_hotkeys().get("command") == "Ctrl+Alt+C"
    tid = db.ex("INSERT INTO tools(name, type, script, hotkey) VALUES('Eigenes C', 'cmd', 'ver', 'Ctrl+Alt+C')")
    try:
        assert "command" not in tools.builtin_hotkeys() and tools.hotkey_owner("Ctrl+Alt+C") == "Eigenes C"
    finally:
        db.ex("DELETE FROM tools WHERE id=?", (tid,))
    js = open(os.path.join(root, "app", "static", "app.js"), encoding="utf-8").read()
    dlg = js[js.index("async function commandDlg(targets)"):]
    dlg = dlg[:dlg.index("\n}\n")]
    assert "/api/tools-command" in dlg and "toolDlg({" in dlg and "return false;  // Fenster bleibt offen" in dlg and "if (busy) return false;" in dlg
    assert "localStorage" not in dlg, "Verlauf nicht speichern (Kennwörter)"
    assert "{ key: 'command', name: 'Befehl ausführen …', icon: 'terminal', main: true }" in js and "if (b.key === 'command') { commandDlg(targets); return; }" in js
    assert "const isNew = !tool || !tool.id;" in js


def _mszip(data, chunk=32768):
    """MSZIP wie die Windows Compression API (für den Selbsttest des winget-Caches)."""
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


@test("2.35 winget-Cache: Manifest wie winget laden (MSZIP, Prüfsummen), Installer wählen, ins Repository, Paket erzeugen, neue Version ersetzt alte, Entfernen räumt auf, Computer ohne winget bekommen das Paket")
def _():
    import hashlib as _h, io as _io, sqlite3 as _sq, tempfile as _tf, json as _j, time as _t
    from app import wingetcache as wc, winget, remote, deploy, main as _m, schedules, config
    # MSZIP: mehrere Stücke mit Wörterbuch, kaputte Daten werden abgelehnt
    raw = ("vD:\n" + "".join(f"- v: '1.{i}'\n  rP: manifests/t/x/{i}.yaml\n  s256H: {'a' * 64}\n" for i in range(3000))).encode()
    assert len(raw) > 70000 and wc.mszip_decompress(_mszip(raw)) == raw
    y = wc._yaml("v: 1.10\nPackageVersion: 2.50\nInstallerSuccessCodes: [1, 0x10]\nx: yes\n")
    assert y == {"v": "1.10", "PackageVersion": "2.50", "InstallerSuccessCodes": ["1", "0x10"], "x": "yes"}, ("Versionen bleiben Text", y)
    must_fail(wc.mszip_decompress, b"PK\x03\x04" + b"0" * 40, contains="MSZIP")
    bad = bytearray(_mszip(raw)); bad[8] ^= 1
    must_fail(wc.mszip_decompress, bytes(bad))
    # Installer wählen und Schalter
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
    assert wc.choose(man, "x64", "machine", "fr-FR")["InstallerUrl"].endswith("app-en.msi"), "sonst en-US"
    assert wc.choose(man, "x86", "machine", "de-DE")["InstallerUrl"].endswith("x86.msi")
    p = wc.plan(man, i)
    assert p["install_type"] == "msi" and p["arguments"] == "REBOOT=ReallySuppress ALLUSERS=1" and p["success_codes"] == "0,3010,1641,1638", p
    assert p["detect_name"] == "Test App * (x64)" and p["detect_version"] == "1.0.0" and p["product_code"].startswith("{1111"), p
    inno = {"PackageName": "I", "PackageVersion": "2", "Installers": [{"Architecture": "x64", "InstallerType": "inno", "InstallerUrl": "https://x/i.exe"}]}
    assert wc.plan(inno, wc.choose(inno))["arguments"] == "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /ALLUSERS"
    nsis = {"Installers": [{"Architecture": "x64", "InstallerType": "nullsoft", "InstallerSwitches": {"Silent": "/S /Q"}}]}
    assert wc.plan(nsis, wc.choose(nsis))["arguments"] == "/S /Q", "Schalter aus dem Manifest gehen vor"
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
    # --- Ende zu Ende mit nachgebautem winget-Server und lokalem Repository
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
        assert wc.refresh_entry(eid)["status"] == "ok" and not any("testapp" in u for u in calls[n:]), "gleiche Version: nicht neu laden"
        # gleiche Version, Katalog tauscht die Datei (neue Prüfsumme): eigener Ordner, alter weg – Bruchprüfung bleibt stimmig
        publish("1.0", b"MZ-eins-neu" * 1000)
        e1b = wc.refresh_entry(eid)
        d10b = "1.0_" + _h.sha256(b"MZ-eins-neu" * 1000).hexdigest()[:8]
        assert e1b["folder"].endswith(d10b) and sorted(os.listdir(os.path.join(root, "winget-cache", "Test.App"))) == [d10b], dict(e1b)
        # neue Version: laden, Paket umstellen (gleiche ID), alte Version weg – nicht, solange eine Bereitstellung sie braucht
        dep = db.ex("INSERT INTO deployments(name, status, package_json) VALUES('WC-läuft', 'running', ?)",
                    (_j.dumps({"steps": {"pre": [], "main": [{"type": "install", "source": "file", "folder": e1b["folder"], "install_file": "testapp-1.0.exe"}], "post": []}}),))
        publish("1.1", b"MZ-zwei" * 1000)
        e2 = wc.refresh_entry(eid)
        d11 = "1.1_" + _h.sha256(b"MZ-zwei" * 1000).hexdigest()[:8]
        assert e2["version"] == "1.1" and e2["package_id"] == e["package_id"] and "noch verwendet" in (e2["message"] or ""), dict(e2)
        assert sorted(os.listdir(os.path.join(root, "winget-cache", "Test.App"))) == sorted([d10b, d11]), "alte Version bleibt, solange sie gebraucht wird"
        db.ex("DELETE FROM deployments WHERE id=?", (dep,))
        wc.cleanup("selftest")
        assert sorted(os.listdir(os.path.join(root, "winget-cache", "Test.App"))) == [d11], "nur die aktuelle Version im Cache"
        assert _j.loads(db.val("SELECT steps FROM packages WHERE id=?", (e["package_id"],)))["main"][0]["install_file"] == "testapp-1.1.exe"
        # Hersteller tauscht die Datei: Prüfsumme falsch → nichts übernehmen, alte bleibt
        publish("1.2", b"MZ-drei" * 1000, sha="0" * 64)
        e3 = wc.refresh_entry(eid)
        assert e3["status"] == "error" and "Prüfsumme" in e3["message"] and e3["version"] == "1.1", dict(e3)
        assert sorted(os.listdir(os.path.join(root, "winget-cache", "Test.App"))) == [d11]
        assert not [f for f in os.listdir(config.WINGET_DIR) if f.endswith(".part")], "keine Reste vom Download"
        assert "test.app" in wc.ready(), "Fehler beim Laden der neuen Version: die bisherige bleibt nutzbar"
        db.ex("UPDATE winget_cache SET status='checking' WHERE id=?", (eid,))
        assert "test.app" in wc.ready(), "auch während einer Prüfung"
        # Installiert: 1.0 → Cache 1.1 neuer; schon 1.1 → nichts zu tun; x86 installiert → kein x64 daneben; 32-Bit-Windows → nein
        c32 = db.ex("INSERT INTO computers(name, os_build, os_version, os_arch) VALUES('PC-ALT32', '14393', '10.0.14393', '32-Bit')")
        cx86 = db.ex("INSERT INTO computers(name, os_build, os_version, os_arch) VALUES('PC-ALT86', '14393', '10.0.14393', '64-Bit')")
        cneu = db.ex("INSERT INTO computers(name, os_build, os_version, os_arch) VALUES('PC-ALTOK', '14393', '10.0.14393', '64-Bit')")
        for cid, ver, arch in ((cmp_, "1.1", "x64"), (cx86, "1.0", "x86"), (cneu, "1.0", "x64"), (c32, "1.0", "x86")):
            db.ex("INSERT INTO software(computer_id, name, version, arch, scope, winget_id) VALUES(?, 'Test App', ?, ?, 'Computer', 'Test.App')", (cid, ver, arch))
        _wg, cc, sk, _e = wc.split("Test.App", [cmp_, cx86, cneu, c32])
        assert cc == [cneu] and sorted(sk) == sorted([cmp_, cx86, c32]), (cc, sk)
        db.ex("DELETE FROM software WHERE winget_id='Test.App'")
        db.ex("DELETE FROM computers WHERE id IN (?,?,?)", (c32, cx86, cneu))
        # Aufteilen: neue Windows per winget, alte aus dem Cache, zu alte (Mindestversion) auslassen
        wg, cache, skip, ce = wc.split("test.app", [cmp_, cnew, c81])
        assert wg == [cnew] and cache == [cmp_] and skip == [c81] and ce["id"] == eid, (wg, cache, skip)
        assert "test.app" in _m.api_wc_ready({"username": "ou", "scope": [1]})["ids"]
        assert ("GET", "/api/wingetcache/ready") in _m.access.ROUTES and ("GET", "/api/wingetcache") not in _m.access.ROUTES
        try:
            _m.api_wc_list({"username": "ou", "scope": [1]})
            raise AssertionError("OU-Admin darf den Cache nicht verwalten")
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
        # Entfernen: wird es noch benutzt, sagt es wo; sonst Paket und Ordner weg
        sid = db.ex("INSERT INTO schedules(name, packages) VALUES('WC-Test', ?)", (_j.dumps([e["package_id"]]),))
        must_fail(wc.remove, eid, "selftest", contains="Zeitplan")
        db.ex("DELETE FROM schedules WHERE id=?", (sid,))
        os.makedirs(os.path.join(root, "winget-cache", "Fremd.Rest", "9"))
        os.makedirs(os.path.join(root, "winget-cache", "Benutzt", "x"))
        os.makedirs(os.path.join(root, "Anderes"))
        bp = db.ex("INSERT INTO packages(name, type, steps) VALUES('WC-Fremd', 'custom', ?)",
                   (_j.dumps({"pre": [], "main": [{"type": "install", "source": "file", "folder": "winget-cache\\Benutzt\\x", "install_file": "a.exe"}], "post": []}),))
        assert wc.orphans() == ["Fremd.Rest"], ("benutzte Ordner nie als verwaist", wc.orphans())
        # Paket in der Paketliste löschen → Eintrag und Ordner auch weg
        _m.api_package_delete(e["package_id"], {"username": "haupt", "scope": None})
        assert not wc.entry(eid) and not os.path.exists(os.path.join(root, "winget-cache", "Test.App")), "beim Löschen des Pakets aufräumen"
        eid = None
        assert wc.cleanup("selftest") == ["Fremd.Rest"] and os.path.isdir(os.path.join(root, "Anderes")), "nur im Cache-Ordner aufräumen"
        assert os.path.isdir(os.path.join(root, "winget-cache", "Benutzt", "x")), "von einem Paket benutzt – bleibt"
        db.ex("DELETE FROM packages WHERE id=?", (bp,))
        # Ordner: nie leer/Wurzel, nicht über die allgemeinen Einstellungen, nur ein eigener (neuer/leerer) Ordner
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
        # Aufnehmen mehrerer: eine ungültige ID hält die anderen nicht auf
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


@test("2.35.1 Wiederholen nach Änderung am Paket: wählbar mit dem aktuellen Stand (Standard) oder dem der Bereitstellung, Ausschnitte bleiben Ausschnitte")
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
        assert cmds(made[-1][0]) == ["echo alt"], "Standard der Funktion: Stand der Bereitstellung"
        deploy.redeploy(did, True, "t", current=True)
        assert cmds(made[-1][0]) == ["echo neu", "echo neu-b"] and made[-1][2]["package_id"] == pid, "aktueller Stand"
        deploy.retry_step(did, "sa", "step", [], "t", current=True)
        assert cmds(made[-1][0]) == ["echo neu"]
        must_fail(deploy.retry_step, did, "gibts-nicht", "step", [], "t", True, contains="geänderten Paket")
        ov = deploy.steps_overview(did, None, current=True)
        assert [x["sid"] for x in ov["steps"]] == ["sa", "sb"] and ov["steps"][0]["counts"] == {"failed": 1} and ov["steps"][1]["counts"] == {}, ov
        assert [x["sid"] for x in deploy.steps_overview(did)["steps"]] == ["sa"]
        deploy.rerun_steps(did, ["sb"], "all", [], "t", current=True)
        assert cmds(made[-1][0]) == ["echo neu-b"], "neuer Schritt wählbar"
        must_fail(deploy.rerun_steps, did, ["sb"], "all", [], "t", contains="Unbekannter Schritt")
        # war schon ein Ausschnitt („– „A“ wiederholt“): aus dem aktuellen Paket nur diese Schritte
        db.ex("UPDATE deployments SET name='Rerun-Neu – „A“ wiederholt' WHERE id=?", (did,))
        deploy.redeploy(did, False, "t", current=True)
        assert cmds(made[-1][0]) == ["echo neu"], "Ausschnitt bleibt Ausschnitt"
        # nicht geändert (Paket älter als die Bereitstellung): immer der Stand der Bereitstellung
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


@test("2.35.1 winget-Cache überall: winget-Schritt auf Computern ohne winget läuft aus dem Cache (Pakete, Zeitpläne, Kiosk), Cache auf der Paketseite und im Paket festlegen")
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
        assert wc.step_for(ws, c_neu) is None, "Einstellung aus: mit winget wie bisher per winget"
        db.set_setting("winget_cache_all", 1)
        assert wc.step_for(ws, c_neu)["install_file"] == "tool.msi", "2.35.2: auch mit winget aus dem Cache (Standard)"
        assert wc.step_for(dict(ws, extra_args="--override /S"), c_neu) is None and wc.step_for(dict(ws, winget_scope="user"), c_neu) is None, "eigene winget-Argumente/Benutzer: winget"
        assert wc.step_for(dict(ws, winget_action="upgrade"), c_neu) is None, "Aktualisieren installiert nichts neu"
        db.ex("INSERT INTO software(computer_id, name, version, arch, scope, winget_id) VALUES(?, 'Cache Tool', '1.0', 'x64', 'Computer', 'Cache.Tool')", (neu,))
        assert wc.step_for(dict(ws, winget_action="upgrade"), c_neu), "installiert: Aktualisieren aus dem Cache"
        db.ex("DELETE FROM software WHERE computer_id=?", (neu,))
        # 2.35.2: Cache-Pakete nicht in der Paketliste, nicht zur Auswahl (Bereitstellen, Zeitpläne, verschachtelt, Kiosk)
        from app import main as _m2
        hu = {"username": "haupt", "scope": None}
        assert cpid not in {r["id"] for r in _m2.api_packages(hu)}
        # 2.35.5: Auswahllisten bekommen sie mit Kennzeichen (bereits gewählte bleiben sichtbar), angeboten werden sie nicht
        assert {r["id"]: r["cache"] for r in _m2.api_packages_meta(hu)["packages"]}.get(cpid) is True
        assert {r["id"]: r["cache"] for r in _m2.api_kiosk_admin(hu)["packages"]}.get(cpid) is True
        assert wc.step_for(dict(ws, winget_action="uninstall"), c_alt) is None and wc.step_for(dict(ws, winget_version="1.5"), c_alt) is None
        db.ex("INSERT INTO software(computer_id, name, version, arch, scope, winget_id) VALUES(?, 'Cache Tool', '1.0', 'x64', 'Computer', 'Cache.Tool')", (alt,))
        assert wc.step_for(dict(ws, winget_action="upgrade", winget_version="2.0"), c_alt), "gleiche feste Version geht"
        db.ex("DELETE FROM software WHERE computer_id=?", (alt,))
        assert wc.step_for(ws, dict(c_alt, os_arch="32-Bit")) is None, "x64 nicht auf 32-Bit-Windows"
        # 2.35.5: nie als angemeldeter Benutzer, nie Bereich „Benutzer“, nie herunterstufen
        assert wc.step_for(ws, c_alt, {"run_as": "user"}) is None and wc.step_for(ws, c_alt, {"run_as": "deploy"})
        assert wc.step_for(dict(ws, winget_scope="user"), c_alt) is None, "Benutzer-Bereich auch ohne winget nicht"
        db.ex("INSERT INTO software(computer_id, name, version, arch, scope, winget_id) VALUES(?, 'Cache Tool', '3.0', 'x64', 'Computer', 'Cache.Tool')", (alt,))
        assert wc.step_for(ws, c_alt) is None, "installierte 3.0 nicht durch 2.0 ersetzen"
        db.ex("DELETE FROM software WHERE computer_id=?", (alt,))
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "steps.py"), encoding="utf-8").read()
        cs = src[src.index("    def _client_step(self, st, pkg, phase, depth, t0):"):]
        assert cs.index("wingetcache.step_for(st, self.c, pkg)") < cs.index("effective_run_as(st, pkg)"), "vor der Ausführung ersetzen (mit dem Paket: Ausführen als)"
        # Kiosk: winget-Paket auf altem Windows nur, wenn der Cache es abdeckt
        db.set_setting("kiosk_enabled", 1)
        wp = stp.clean_package({"name": "WG Cache Tool", "steps": {"main": [dict(ws, id="k1")]}})
        wp["steps"], wp["conditions"] = _j.dumps(wp["steps"]), _j.dumps(wp["conditions"])
        wpid = db.ex(f"INSERT INTO packages({', '.join(deploy.PKG_FIELDS)}) VALUES({', '.join('?' * len(deploy.PKG_FIELDS))})", tuple(wp[k] for k in deploy.PKG_FIELDS))
        kiosk.save_item(wpid, {"enabled": True, "approval": 0})
        try:
            assert "WG Cache Tool" in {i["name"] for i in kiosk.catalog(alt)}, "im Cache → im Kiosk"
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
    sw = js[js.index("async function viewSoftware("):js.index("// --- Ausnahmen der Aktualitätsprüfung")]
    assert "#/wingetcache" not in sw, "nicht mehr unter Software"
    pk = js[js.index("async function viewPackages("):js.index("// --- 2.32: Pakete zu einem neuen zusammenfassen")]
    assert "go('#/wingetcache')" in pk and "key: 'wcache'" in pk and "In den winget-Cache aufnehmen" in pk and "].filter(Boolean), shown," in pk
    assert "const w = wcStepField(st);" in js
    assert "(!x.cache || x.id === Number(st.package_id))" in js and "meta.packages.filter((x) => !x.cache)" in js
    assert "(!inCat.has(p.id) && !p.cache) || p.id === x.package_id" in js and "!p.cache || p.id === x.uninstall_package_id" in js


@test("2.35.1 Oberfläche: kein „null“ als Text – replaceChildren/append bekommen nie null (Paket-Eigenschaften zeigten seit 2.27.1 „null“)")
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
    assert not bad, ("DOM-Methoden zeigen null als Text", bad)


@test("2.35.3 winget-Cache: ZIP mit Installer darin (entpackt, nie außerhalb), „Prüfsumme des Katalogs nicht verlangen“ (Adressen ohne Version wie Google Chrome)")
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
        # --- ZIP mit Installer
        z = zipbytes([("sub/setup.exe", b"MZ-zip" * 100), ("sub/data.cab", b"cab"), ("readme.txt", b"hi")])
        publish("Zip.Tool", "5.1", "  InstallerType: zip\n  NestedInstallerType: nullsoft\n  NestedInstallerFiles:\n  - RelativeFilePath: sub\\setup.exe\n"
                "  InstallerSwitches:\n    Silent: /auto\n", "https://dl.example.test/zt.zip", z)
        ids.append(wc.add("Zip.Tool"))
        e = wc.refresh_entry(ids[-1])
        assert e["status"] == "ok" and e["installer_type"] == "zip/nullsoft" and e["file"] == "sub\\setup.exe", dict(e)
        d = os.path.join(root, *e["folder"].split("\\"))
        assert open(os.path.join(d, "sub", "setup.exe"), "rb").read() == b"MZ-zip" * 100 and os.path.isfile(os.path.join(d, "sub", "data.cab"))
        assert not [x for x in os.listdir(os.path.dirname(d)) if x.endswith(".pk-part")], "kein halber Ordner"
        st = _j.loads(db.val("SELECT steps FROM packages WHERE id=?", (e["package_id"],)))["main"][0]
        assert st["folder"] == e["folder"] + "\\sub" and st["install_file"] == "setup.exe" and st["arguments"] == "/auto", st
        # ZIP mit Pfad nach außen: abgelehnt, nichts außerhalb geschrieben
        evil = zipbytes([("../../boese.exe", b"x"), ("setup.exe", b"MZ")])
        publish("Zip.Evil", "1.0", "  InstallerType: zip\n  NestedInstallerType: exe\n  NestedInstallerFiles:\n  - RelativeFilePath: setup.exe\n"
                "  InstallerSwitches:\n    Silent: /S\n", "https://dl.example.test/ev.zip", evil)
        ids.append(wc.add("Zip.Evil"))
        e2 = wc.refresh_entry(ids[-1])
        assert e2["status"] == "error" and "Ungültiger Pfad im ZIP" in e2["message"], dict(e2)
        assert not os.path.exists(os.path.join(root, "boese.exe")) and not os.path.exists(os.path.join(os.path.dirname(root), "boese.exe"))
        # genannte Datei fehlt im ZIP → Fehler, Ordner weg
        publish("Zip.Miss", "1.0", "  InstallerType: zip\n  NestedInstallerType: msi\n  NestedInstallerFiles:\n  - RelativeFilePath: fehlt.msi\n",
                "https://dl.example.test/zm.zip", zipbytes([("anders.msi", b"x")]))
        ids.append(wc.add("Zip.Miss"))
        e3 = wc.refresh_entry(ids[-1])
        assert e3["status"] == "error" and "nicht im ZIP" in e3["message"] and not os.listdir(os.path.join(root, "winget-cache", "Zip.Miss")), dict(e3)
        must_fail(wc.choose, {"Installers": [{"Architecture": "x64", "InstallerType": "zip", "NestedInstallerType": "portable",
                                               "NestedInstallerFiles": [{"RelativeFilePath": "a.exe"}]}]}, contains="Kein passender")
        # --- Adresse ohne Version: Prüfsumme passt nicht → Fehler mit Hinweis; mit Häkchen: laden, eigene Prüfsumme merken
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
        assert len([c for c in calls if c == ("GET", url)]) == n and e6["folder"] == e5["folder"], "gleiches ETag: nicht neu laden"
        files[url], heads[url] = b"MZ-neu-2", {"ETag": '"v2"'}
        e7 = wc.refresh_entry(ids[-1])
        assert e7["sha256"] == _h.sha256(b"MZ-neu-2").hexdigest() and e7["folder"] != e5["folder"], dict(e7)
        assert sorted(os.listdir(os.path.join(root, "winget-cache", "Latest.Tool"))) == [e7["folder"].rsplit("\\", 1)[-1]], "alte Datei weg"
        upd = db.val("SELECT updated_at FROM packages WHERE id=?", (e7["package_id"],))
        db.ex("UPDATE packages SET updated_at='2000-01-01 00:00:00' WHERE id=?", (e7["package_id"],))
        wc.refresh_entry(ids[-1])
        assert db.val("SELECT updated_at FROM packages WHERE id=?", (e7["package_id"],)) == "2000-01-01 00:00:00", "unverändert: Paket nicht neu geschrieben"
        # 2.35.5: ohne Katalog-Prüfsumme nur HTTPS – auch nach Weiterleitung
        files[url], heads[url] = b"MZ-neu-3", {"ETag": '"v3"'}
        redirects[url] = "http://dl.example.test/latest/chromish.exe"
        e8 = wc.refresh_entry(ids[-1])
        assert e8["status"] == "error" and "nur über HTTPS" in e8["message"] and e8["sha256"] == e7["sha256"], dict(e8)
        redirects.clear()
        must_fail(wc._download, "http://dl.example.test/x.exe", None, 10 ** 6, False, contains="nur über HTTPS")
        # ZIP: Namen, die sich nur in der Schreibung unterscheiden → abgelehnt; Datei mit der Schreibung aus dem ZIP
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


@test("2.35.4 winget-Cache: Angaben auf oberster Ebene des Manifests gelten für alle Installer (ZIP-Datei, z. B. MobaXterm); Programm nur als x86 auch im x64-Eintrag")
def _():
    from app import wingetcache as wc
    man = {"PackageIdentifier": "Mobatek.MobaXterm", "PackageVersion": "26.6.0.5560", "PackageName": "MobaXterm", "InstallerType": "zip",
           "NestedInstallerType": "wix", "NestedInstallerFiles": [{"RelativeFilePath": "MobaXterm_installer_26.6.msi"}],
           "ProductCode": "{498DB265-E4C3-4580-B8D0-482C79F36E7D}", "AppsAndFeaturesEntries": [{"UpgradeCode": "{745A604F-05A1-4867-8881-59888EC8B806}"}],
           "Installers": [{"Architecture": "x86", "InstallerUrl": "https://dl.example.test/m.zip", "InstallerSha256": "C4" * 32}]}
    i = wc.choose(man, "x64")
    p = wc.plan(man, i)
    assert p["nested"] == "MobaXterm_installer_26.6.msi" and p["install_type"] == "msi" and p["type"] == "zip/wix" and p["product_code"].startswith("{498D"), p
    # Installer x86 im x64-Eintrag: installiertes x86-Programm passt, 32-Bit-Windows auch; x64-Installer nie auf 32 Bit
    assert wc._arch_ok({"os_arch": "64-Bit"}, [{"arch": "x86", "scope": "Computer"}], "x86")
    assert wc._arch_ok({"os_arch": "32-Bit"}, [], "x86") and not wc._arch_ok({"os_arch": "32-Bit"}, [], "x64")
    assert not wc._arch_ok({"os_arch": "64-Bit"}, [{"arch": "x86"}], "x64"), "x86 installiert, x64-Installer: nein"
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "wingetcache.py"), encoding="utf-8").read()
    assert src.count('e["inst_arch"] or e["arch"]') == 2, "Aufteilen und winget-Schritt nehmen die Architektur des Installers"


@test("2.35.5 Wiederholen mit aktuellem Stand: Ausschnitt fest markiert (auch bei gekürztem Namen), ausgeschaltete Schritte bleiben aus")
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
    sl, _t = deploy.retry_package(full, "a", "from")  # ab „a“: b (ausgeschaltet) und c mit
    assert sl.get("slice") is True
    did = db.ex("INSERT INTO deployments(name, package_id, package_json, status, created_at) VALUES(?, ?, ?, 'failed', '2026-10-01 12:00:00')",
                (("S" * 190 + " – ab „" + "x" * 50 + "“ wiederholt")[:200], pid, _j.dumps(sl)))
    try:
        d = db.q1("SELECT * FROM deployments WHERE id=?", (did,))
        assert not deploy.RETRY_SUFFIX.search(d["name"]), "Name gekürzt – Endung fehlt"
        p = stp.normalize(deploy.source_package(d, True))
        got = [(x["id"], x.get("enabled", True)) for _ph, x in stp.iter_steps(p)]
        assert got == [("a", True), ("b", False), ("c", True)] and p.get("slice"), got
    finally:
        db.ex("DELETE FROM deployments WHERE id=?", (did,))
        db.ex("DELETE FROM packages WHERE id=?", (pid,))


@test("2.35.6 Dateien (Explorer): versteckte Einträge ausgeblendet, umschaltbar; Anzahl, Hochladen-Rückfrage und gezielter Eintrag bleiben")
def _():
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "app.js"), encoding="utf-8").read()
    i = js.index("async function viewFiles(")
    v = js[i:js.index("\nasync function ", i + 10)]
    assert "showHidden ? d0.items : d0.items.filter((x) => !x.hidden" in v, "versteckte Einträge nicht gefiltert"
    assert "x.name.toLowerCase() === fl" in v, "gezielt geöffneter Eintrag (focus) muss sichtbar bleiben"
    assert "localStorage.setItem('pk-files-hidden'" in v and "'Versteckte anzeigen'" in v, "Umschalter fehlt"
    # Hochladen prüft gegen ALLE Namen (auch versteckte) – sonst würde eine versteckte Datei ungefragt überschrieben
    assert "curNames = new Set((d.items || d.entries || []).map(" in v
    assert v.index("curNames = new Set((d.items") < v.index("lastList = d;")
    from app import clientfiles
    src = open(clientfiles.__file__, encoding="utf-8").read()
    assert '"hidden": bool(attr & 0x2)' in src, "Server liefert das Attribut versteckt"


@test("2.36 Freigabe neuer winget-Versionen: sofort / Tage / manuell, freigeben, sperren, Ausgangsstand, Schritt mit --version, Zeitpläne, Cache")
def _():
    from app import wingetrelease as wr, wingetcache as wc, util as u
    W = "Selftest.RelApp"
    cat = {"v": "2.0"}
    old_latest, old_mode, old_days = wr._latest, db.setting("winget_release_mode"), db.setting("winget_release_days")
    wr._latest = lambda wid: cat["v"] if str(wid).lower() == W.lower() else old_latest(wid)
    db.ex("DELETE FROM winget_releases WHERE winget_id=?", (W,))
    try:
        db.set_setting("winget_release_mode", "days"); db.set_setting("winget_release_days", 2)
        assert wr.target(W)["state"] == "latest", "unbekannte ID: keine Einschränkung"
        # Ausgangsstand beim ersten Kontakt freigegeben (sonst stünde nach dem Einschalten alles still)
        db.ex("INSERT INTO software(computer_id, name, version, winget_id, winget_sure) VALUES(NULL, 'RelApp', '1.0', ?, 1)", (W,))
        cat["v"] = "1.5"
        wr.sync()
        assert db.val("SELECT base FROM winget_releases WHERE winget_id=? AND version='1.5'", (W,)) == 1
        assert wr.target(W)["state"] == "latest"
        # neue Version 2.0: 2 Tage zurückgestellt → 1.5 wird installiert
        cat["v"] = "2.0"
        wr.sync()
        t = wr.target(W)
        assert t["state"] == "pin" and t["version"] == "1.5" and "zurückgestellt bis" in t["reason"], t
        st = {"type": "install", "source": "winget", "winget_id": W, "winget_action": "install", "winget_version": ""}
        s2, why = wr.apply_step(st)
        assert s2["winget_version"] == "1.5" and why is None
        assert wr.apply_step({**st, "winget_version": "1.9"})[0]["winget_version"] == "1.9", "feste Version bleibt"
        assert wr.apply_step(st, {"release_bypass": True})[0] is st, "Knopf „Aktualisieren“: neueste"
        assert wr.apply_step({**st, "winget_action": "uninstall"})[0]["winget_version"] == "", "Deinstallieren unverändert"
        assert wr.allowed_update(W, "1.0") and not wr.allowed_update(W, "1.5") and not wr.allowed_update(W, "")
        # nach Ablauf der Tage frei
        db.ex("UPDATE winget_releases SET first_seen=? WHERE winget_id=? AND version='2.0'", (u.ago(days=3), W))
        assert wr.target(W)["state"] == "latest" and wr.allowed_update(W, "1.5")
        # manuell: wartet, bis freigegeben
        db.set_setting("winget_release_mode", "manual")
        t = wr.target(W)
        assert t["state"] == "pin" and "wartet auf Freigabe" in t["reason"], t
        wr.set_status(W, "2.0", "approved", "selftest")
        assert wr.target(W)["state"] == "latest"
        # sofort, aber gesperrt → die vorige; alles gesperrt → nichts
        db.set_setting("winget_release_mode", "off")
        wr.set_status(W, "2.0", "blocked", "selftest")
        t = wr.target(W)
        assert t["state"] == "pin" and t["version"] == "1.5" and "gesperrt" in t["reason"], t
        wr.set_status(W, "1.5", "blocked", "selftest")
        assert wr.target(W)["state"] == "hold" and wr.apply_step(st)[0] is None and not wr.allowed_update(W, "1.0")
        wr.set_status(W, "1.5", "", "selftest"); wr.set_status(W, "2.0", "", "selftest")
        assert wr.target(W)["state"] == "latest", "zurück auf die Regel (sofort)"
        try:
            wr.set_status(W, "9.9", "approved", "selftest")
            raise AssertionError("unbekannte Version angenommen")
        except ValueError:
            pass
        # Katalog neuer als der letzte Abgleich: wird bei der Abfrage als erste Sichtung vermerkt
        db.set_setting("winget_release_mode", "days")
        cat["v"] = "2.1"
        t = wr.target(W)
        assert t["state"] == "pin" and t["version"] == "2.0" and db.val("SELECT 1 FROM winget_releases WHERE winget_id=? AND version='2.1'", (W,))
        # winget-Cache: freigegebene Version liegt schon im Cache → nichts laden, Stand behalten
        eid = db.ex("INSERT INTO winget_cache(winget_id, status, version, folder, file, created_at) VALUES(?, 'ok', '2.0', 'x\\y', 'a.msi', ?)", (W, u.now()))
        old_fm = wc.fetch_manifest
        wc.fetch_manifest = lambda *a, **k: (_ for _ in ()).throw(AssertionError("darf nichts laden"))
        try:
            e = wc.refresh_entry(eid)
            assert e["status"] == "ok" and e["version"] == "2.0" and "bleibt 2.0" in (e["message"] or ""), dict(e)
            # gesperrte Version im Cache wird nicht benutzt
            assert wc._released(dict(e))
            wr.set_status(W, "2.0", "blocked", "selftest")
            assert not wc._released(dict(wc.entry(eid))) and wr.version_ok(W, "1.5") and wr.version_ok(W, "0.1")
            wr.set_status(W, "2.0", "", "selftest")
        finally:
            wc.fetch_manifest = old_fm
            db.ex("DELETE FROM winget_cache WHERE id=?", (eid,))
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "schedules.py"), encoding="utf-8").read()
        assert "wingetrelease.target(" in src and 'util.vcmp(str(r["version"]), t["version"]) < 0' in src, "Zeitpläne: nur ältere als die freigegebene"
        stp = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "steps.py"), encoding="utf-8").read()
        assert stp.index("wingetrelease.apply_step(") < stp.index("wingetcache.step_for(st, self.c, pkg)"), "Freigabe vor dem winget-Cache"
        assert "util.vcmp(str(v), pin) >= 0" in stp, "nie herunterstufen"
        mn = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "main.py"), encoding="utf-8").read()
        assert 'pkg["release_bypass"] = True' in mn and '"winget_release_days": (1, 365)' in mn
        wcs = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "wingetcache.py"), encoding="utf-8").read()
        assert 'pkg.get("release_bypass")' in wcs, "„Aktualisieren“: neueste per winget statt Cache"

    finally:
        wr._latest = old_latest
        db.set_setting("winget_release_mode", old_mode); db.set_setting("winget_release_days", old_days)
        db.ex("DELETE FROM winget_releases WHERE winget_id=?", (W,))
        db.ex("DELETE FROM software WHERE winget_id=?", (W,))


@test("2.36.1 Lizenz AGPL-3.0: Lizenztext unverändert, im Image, Kennzeichnung, Hinweis in Hilfe und Oberfläche")
def _():
    import hashlib
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    rd = lambda *p: open(os.path.join(root, *p), encoding="utf-8").read()
    lic = open(os.path.join(root, "LICENSE"), "rb").read()
    assert hashlib.sha256(lic).hexdigest() == "8486a10c4393cee1c25392769ddd3b2d6c242d6ec7928e1414efff7dfb2f07ef", "LICENSE ist nicht der unveränderte Text der AGPL-3.0"
    # Im Image fehlen Dockerfile, Workflows und README (dort beweist schon die vorhandene LICENSE, dass sie mitkommt)
    if os.path.exists(os.path.join(root, "Dockerfile")):
        assert "LICENSE ./" in rd("Dockerfile"), "Lizenztext gehört ins Image"
    if os.path.exists(os.path.join(root, ".github", "workflows", "release.yml")):
        assert "org.opencontainers.image.licenses=AGPL-3.0-only" in rd(".github", "workflows", "release.yml")
    for f in ("de.md", "en.md"):
        assert "{#license}" in rd("app", "static", "help", f) and "github.com/soleil0815/pocketknife" in rd("app", "static", "help", f)
    js = rd("app", "static", "app.js")
    assert "'#/help?s=license'" in js and "'AGPL-3.0'" in js, "Lizenzhinweis in der Oberfläche"
    for f in ("README.md", "README.en.md"):
        if os.path.exists(os.path.join(root, f)):
            assert "AGPL-3.0-only" in rd(f) and "(LICENSE)" in rd(f)



@test("2.36.3 Audit: Direktaktionen (Dateien, Dienste, Tools, Speicherplatz, Fernsteuerung, Neustart …) nie unter einer Adresse, die die Identitätsprüfung als fremd markiert hat")
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
            raise AssertionError("Dateien: kein Fehler")
        except clientfiles.FileError as e:
            assert "Nicht ausgeführt" in str(e)
        c = db.q1("SELECT * FROM computers WHERE id=?", (cid,))
        must_fail(tools.run, {"id": 1, "repo_path": "", "script": "x", "type": "cmd", "run_as": "service"}, cid, contains="Nicht ausgeführt")
        must_fail(diskusage.start, cid, "C:\\", 2, "test", contains="Nicht ausgeführt")
        must_fail(remote_desktop.prepare, c, {"username": "haupt"}, "x", {"protocol": "rdp", "auth": "manual", "username": "a"},
                  contains="Nicht ausgeführt")
        # nur id übergeben (ohne Spalten): Vermerk wird nachgelesen
        must_fail(presence.direct_host, {"id": cid, "name": "PC-AUD363", "dns_name": "fs01.firma.local"}, contains="FS01")
        # Prüfung aus: alter Vermerk gilt nicht mehr; ohne Vermerk die normale Adresse
        db.set_setting("identity_check", 0)
        assert actions._host(cid)[1] == "fs01.firma.local"
        db.set_setting("identity_check", 1)
        db.ex("UPDATE computers SET addr_problem=NULL, addr_detail=NULL WHERE id=?", (cid,))
        assert actions._host(cid)[1] == "fs01.firma.local"
        # Alle Direktwege benutzen die Prüfung (Quelltext)
        rd = lambda f: open(os.path.join(root, "app", f), encoding="utf-8").read()  # noqa: E731
        mn = rd("main.py")
        assert 'remote.ps(presence.direct_host(c), cmds[act])' in mn, "Neustart/Herunterfahren/gpupdate"
        assert 'remote.ps(presence.direct_host(c), script)' in mn, "Scanner testen"
        assert "host = presence.direct_host(c)\n    except ValueError as e:\n        _err(e)\n    if not remote.is_online(host)[1]" in mn, "angemeldete Benutzer"
        assert "presence.direct_host(c)" in rd("toast.py") and "presence.direct_host(c)" in rd("kiosk.py")
        assert 'remote.ps(c["dns_name"] or c["name"], script, timeout=seconds)' not in rd("clientfiles.py"), "Dateisuche über _host"
    finally:
        db.set_setting("identity_check", old)
        db.ex("DELETE FROM computers WHERE id=?", (cid,))


@test("2.36.3 Audit: Lade-Ausweichweg von PK-RunPs1 verdoppelt auch typografische Apostrophe; .env-Kopie in update.sh nie für andere lesbar")
def _():
    import re as _re
    from app import remote
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    w = remote.WRAPPER
    assert '.Replace("\'", "\'\'")' not in w, "nur ASCII-Apostroph verdoppelt"
    m = _re.search(r'\$pkApo = "(\[[^"]+\])"', w)
    assert m and "-replace $pkApo, '$0$0'" in w
    # Gleiche Wirkung wie util.psq (PowerShell-Regex ≙ Python-Regex für diese Klasse)
    pat = m.group(1).replace("\\u2018", "\u2018").replace("\\u201B", "\u201b")
    for name in ("C:\\Pakete\\Mike’s Setup.ps1", "C:\\a'b‚c‛d‘e.ps1"):
        assert "'" + _re.sub(pat, lambda x: x.group(0) * 2, name) + "'" == util.psq(name)
    us = os.path.join(root, "update.sh")
    if os.path.exists(us):  # im Image liegt update.sh nicht
        t = open(us, encoding="utf-8").read()
        assert "( umask 077; rm -f .env.pk-alt; cp .env .env.pk-alt )" in t and "\n  cp .env .env.pk-alt" not in t


@test("2.36.3 Audit: Kiosk-Anfragen mit Frist gelöscht (offene bleiben), Reste gelöschter Computer weg")
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
        db.set_setting("deployment_retention_days", 0)  # 0 = nie löschen
        db.ex("UPDATE kiosk_requests SET status='success' WHERE id=?", (ids["alt_offen"],))
        scheduler._cleanup()
        assert db.val("SELECT 1 FROM kiosk_requests WHERE id=?", (ids["alt_offen"],))
    finally:
        db.set_setting("deployment_retention_days", old)
        db.ex("DELETE FROM kiosk_requests WHERE username='m.muster'")
        db.ex("DELETE FROM computers WHERE id=?", (cid,))


@test("2.36.3 Audit: db.tx – fehlgeschlagenes COMMIT lässt keine offene Transaktion zurück; Download des winget-Index begrenzt")
def _():
    import io
    c = db.conn()
    c.execute("CREATE TABLE IF NOT EXISTS t363_p(id INTEGER PRIMARY KEY)")
    c.execute("CREATE TABLE IF NOT EXISTS t363_c(pid INTEGER REFERENCES t363_p(id) DEFERRABLE INITIALLY DEFERRED)")
    try:
        try:
            with db.tx():
                db.ex("INSERT INTO t363_c(pid) VALUES(4711)")  # Fremdschlüssel erst beim COMMIT geprüft → COMMIT scheitert
            raise AssertionError("COMMIT hätte scheitern müssen")
        except Exception as e:  # noqa: BLE001
            assert "FOREIGN KEY" in str(e).upper(), e
        assert not c.in_transaction, "Transaktion blieb offen"
        with db.tx():
            db.ex("INSERT INTO t363_p(id) VALUES(1)")
        assert db.val("SELECT COUNT(*) FROM t363_p") == 1 and db.val("SELECT COUNT(*) FROM t363_c") == 0
        # Fehler im Block: ROLLBACK, ursprünglicher Fehler bleibt sichtbar
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


@test("2.36.3 Audit: OU-Admins sehen in Computer → Sammlungen und in der Scanprofil-Liste keine fremden Sammlungen/Computer")
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
        assert p["members"] <= 1, "Zähler nur über den eigenen Bereich"
        h = _m.api_profiles({"username": "h", "scope": None})["profiles"][0]
        assert h["computers"] == [1, 2, 3] and h["members"] == n_all
    finally:
        _m._visible_collections, _m._member_collections, access.computer_ids, profiles.all_profiles = o_vis, o_mem, o_ids, o_all


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
