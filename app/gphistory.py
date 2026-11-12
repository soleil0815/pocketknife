"""2.47.0: Group Policy history of a computer (like the well-known "GPAnalyser" scripts, but in the browser).

Reads the log Microsoft-Windows-GroupPolicy/Operational live via WinRM on the client itself (no remote event log
access needed) and groups the events by activity ID into processing runs:
start 4000-4007, end 8000-8007 (ok) / 6000-6007 (warning) / 7000-7007 (error).
Details per run: domain controller (5308), account/domain (5310), time until all GPOs were determined (5312),
applied and filtered GPOs (5312/5313), extensions with their run time (5016/6016/7016) and their GPO list (4016).
For runs with warnings/errors the Group Policy errors of the System log are added (e.g. 1058, 1129, 1085).
The client script is Constrained-Language-safe: no ToXml() (method calls on non-core types are blocked there),
only properties of the events.
"""
import re
from datetime import datetime, timedelta
from typing import Any

from . import actions, remote, util

HOURS = (12, 24, 48, 168)
MAX_EVENTS = 8000
# Messages needed in full (GPO lists, DC details); all others are shortened
FULL_MSG_IDS = (4016, 5308, 5310, 5312, 5313, 5016, 6016, 7016)
REASONS = {4000: "Computerstart", 4001: "Anmeldung", 4002: "Benutzer (Netzwerkänderung)", 4003: "Computer (Netzwerkänderung)",
           4004: "Computer (manuell)", 4005: "Benutzer (manuell)", 4006: "Computer (periodisch)", 4007: "Benutzer (periodisch)"}
COMPUTER_STARTS = (4000, 4003, 4004, 4006)


def script(hours):
    return f"""$ErrorActionPreference = 'Stop'
$since = (Get-Date).AddHours(-{int(hours)})
function PK-Ev($filter, $max) {{
  try {{ @(Get-WinEvent -FilterHashtable $filter -MaxEvents $max -ErrorAction Stop) }}
  catch {{ if ("$($_.FullyQualifiedErrorId)" -like 'NoMatchingEventsFound*' -or "$($_.Exception.Message)" -like '*No events*') {{ @() }} else {{ throw }} }}
}}
function PK-Row($ev, $full) {{
  $m = "" + $ev.Message
  if (-not $full -and $m.Length -gt 600) {{ $m = $m.Substring(0, 600) }}
  if ($m.Length -gt 6000) {{ $m = $m.Substring(0, 6000) }}
  $p = @(); foreach ($x in @($ev.Properties)) {{ $v = "" + $x.Value; if ($v.Length -gt 300) {{ $v = $v.Substring(0, 300) }}; $p += $v }}
  [ordered]@{{ t = $ev.TimeCreated.ToString('yyyy-MM-ddTHH:mm:ss.fff'); id = [int]$ev.Id; lvl = [int]$ev.Level;
    act = "$($ev.ActivityId)"; src = "$($ev.ProviderName)"; msg = $m; p = $p }}
}}
$full = @({", ".join(str(i) for i in FULL_MSG_IDS)})
$ops = PK-Ev @{{ LogName = 'Microsoft-Windows-GroupPolicy/Operational'; StartTime = $since }} {MAX_EVENTS}
$sys = PK-Ev @{{ LogName = 'System'; ProviderName = 'Microsoft-Windows-GroupPolicy'; Level = 1, 2, 3; StartTime = $since }} 300
# 2.48.0: a failed Group Policy Preferences item (drive, printer, registry ...) is logged as 4098 in the Application log
$app = PK-Ev @{{ LogName = 'Application'; Id = 4098; StartTime = $since }} 300
'PKJSON:' + (ConvertTo-Json -Depth 4 -Compress -InputObject ([ordered]@{{
  ops = @($ops | ForEach-Object {{ PK-Row $_ ($full -contains [int]$_.Id) }});
  sys = @($sys | ForEach-Object {{ PK-Row $_ $true }});
  app = @($app | ForEach-Object {{ PK-Row $_ $true }}) }}))
"""


def _ts(s):
    try:
        return datetime.fromisoformat(str(s)[:23])
    except ValueError:
        return None


def _lines_after_header(msg):
    """GPO names from a 4016/5312/5313 message: the lines after the first line ending in ':' (list header).
    Indented lines are the reason for the GPO above (5313: "Nicht angewendet (leer)") and are appended to it."""
    raw = str(msg or "").replace("\r", "").split("\n")
    for i, ln in enumerate(raw):
        if re.search(r":\s*(\(.*\))?\s*$", ln.strip()):
            rest = raw[i + 1:]
            break
    else:
        rest = raw[1:]
    out: list[str] = []
    for ln in rest:
        t = ln.strip()
        if not t or t.endswith(":"):
            continue
        if ln[:1] in (" ", "\t") and out:
            out[-1] = f"{out[-1]} – {t}"
        else:
            out.append(t)
    return out[:200]


def _principal(ev):
    for v in ev.get("p") or []:
        if "\\" in v and not v.startswith("\\\\") and len(v) < 200:
            return v
    m = re.search(r"\b([\w.\- ]+\\[^\s.]+)", ev.get("msg") or "")
    return m.group(1) if m else ""


def _status(end_id):
    if end_id is None:
        return "open"  # no end event (still running, or ended without a log entry, e.g. switched off)
    return {8: "ok", 6: "warn", 7: "err"}.get(end_id // 1000, "unknown")


def build(data):
    """Groups the raw events into runs (newest first). Pure function (tested without a client)."""
    ops = [e for e in actions._L((data or {}).get("ops")) if isinstance(e, dict)]
    sysev = [e for e in actions._L((data or {}).get("sys")) if isinstance(e, dict)]
    appev = [e for e in actions._L((data or {}).get("app")) if isinstance(e, dict)]
    by_act: dict[str, list[dict]] = {}
    for e in ops:
        by_act.setdefault(e.get("act") or "", []).append(e)
    runs = []
    for act, evs in by_act.items():
        if not act:
            continue
        evs.sort(key=lambda e: (e.get("t") or "", e.get("id") or 0))
        start = next((e for e in evs if 4000 <= (e.get("id") or 0) <= 4007), None)
        if not start:
            continue  # started before the time window
        ends = [e for e in evs if (e.get("id") or 0) in range(6000, 6008) or (e.get("id") or 0) in range(7000, 7008)
                or (e.get("id") or 0) in range(8000, 8008)]
        end = ends[-1] if ends else None
        t0, t1 = _ts(start.get("t")), _ts(end.get("t")) if end else None
        sid = start["id"]
        run = {"act": act, "start": start.get("t"), "end": end.get("t") if end else None,
               "seconds": round((t1 - t0).total_seconds(), 2) if t0 and t1 else None,
               "status": _status(end.get("id") if end else None), "end_id": end.get("id") if end else None,
               "end_message": (end.get("msg") or "") if end and end.get("id", 0) < 8000 else "",
               "reason": REASONS.get(sid, str(sid)), "target": "computer" if sid in COMPUTER_STARTS else "user",
               "principal": _principal(start), "dc": "", "dc_ip": "", "account": "", "domain": "",
               "discover_seconds": None, "gpos": [], "filtered": [], "extensions": [], "events": [], "system": []}
        gp_lists: dict[str, list[str]] = {}
        for e in evs:
            i = e.get("id") or 0
            p = e.get("p") or []
            if i == 5308:
                run["dc"] = next((v for v in p if v.startswith("\\\\")), run["dc"])
                run["dc_ip"] = next((v for v in p if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", v)
                                     or (":" in v and re.fullmatch(r"[0-9a-fA-F:]+(%\d+)?", v))), run["dc_ip"])
            elif i == 5310:
                run["account"] = next((v for v in p if v.upper().startswith("CN=")), run["account"])
                run["domain"] = next((v for v in p if re.fullmatch(r"[\w-]+(\.[\w-]+)+", v) and not v.upper().startswith("CN=")), run["domain"])
            elif i == 5312:
                if t0 and _ts(e.get("t")):
                    run["discover_seconds"] = round((_ts(e.get("t")) - t0).total_seconds(), 2)
                run["gpos"] = _lines_after_header(e.get("msg"))
            elif i == 5313:
                run["filtered"] = _lines_after_header(e.get("msg"))
            elif i == 4016:
                name = p[1] if len(p) > 1 else ""
                gp_lists[name] = _lines_after_header(e.get("msg"))
            elif i in (5016, 6016, 7016):
                try:
                    ms = float(p[0]) if p else 0.0
                except ValueError:
                    ms = 0.0
                name = p[2] if len(p) > 2 else (p[1] if len(p) > 1 else "")
                run["extensions"].append({"name": name, "seconds": round(ms / 1000, 2),
                                          "status": {5016: "ok", 6016: "warn", 7016: "err"}[i],
                                          "message": (e.get("msg") or "") if i != 5016 else ""})
            run["events"].append({"t": e.get("t"), "id": i, "lvl": e.get("lvl"), "msg": e.get("msg") or ""})
        for x in run["extensions"]:
            x["gpos"] = gp_lists.get(x["name"], [])
        run["ext_seconds"] = round(sum(x["seconds"] for x in run["extensions"]), 2) if run["extensions"] else None
        if run["extensions"]:
            slow = max(run["extensions"], key=lambda x: x["seconds"])
            slow["slowest"] = True
        # Errors of the System log: same activity ID, otherwise within the run (+30 s)
        lim = (t1 or t0) + timedelta(seconds=30) if t0 else None
        for s in sysev:
            ts = _ts(s.get("t"))
            if (s.get("act") and s.get("act") == act) or (not s.get("act") and t0 and ts and t0 <= ts <= lim):
                run["system"].append({"t": s.get("t"), "id": s.get("id"), "lvl": s.get("lvl"), "msg": s.get("msg") or "",
                                      "log": "System", "src": s.get("src") or ""})
        run["gpp_errors"] = 0
        run["_t0"], run["_lim"] = t0, lim
        runs.append(run)
    # 2.48.0: GPP item errors (Application log 4098, no activity ID) belong to the run during which they were written
    # (start .. end + 30 s); if several runs fit, the one that started last
    for a in appev:
        ts = _ts(a.get("t"))
        fit = [r for r in runs if ts and r["_t0"] and r["_t0"] <= ts <= r["_lim"]]
        if fit:
            r = max(fit, key=lambda x: x["_t0"])
            r["system"].append({"t": a.get("t"), "id": a.get("id"), "lvl": a.get("lvl"), "msg": a.get("msg") or "",
                                "log": "Anwendung", "src": a.get("src") or ""})
            r["gpp_errors"] += 1
    for r in runs:
        del r["_t0"], r["_lim"]
        r["system"].sort(key=lambda x: x.get("t") or "")
    runs.sort(key=lambda r: r["start"] or "", reverse=True)
    return runs


def history(cid, hours=24):
    hours = int(hours or 24)
    if hours not in HOURS:
        raise ValueError("Zeitraum nicht erlaubt")
    _c, host = actions._host(cid)
    out, err, _ = remote.ps(host, script(hours), timeout=180)
    if "PKJSON:" not in out:
        raise remote.RemoteError(util.first_line(err, "Gruppenrichtlinien-Protokoll konnte nicht gelesen werden")[:400])
    runs = build(actions._json(out))
    return {"hours": hours, "runs": runs}


# --- 2.48.0: Resultant Set of Policy (gpresult /x) ----------------------------------------------------------------
# What is effective on this computer (or for a user on it): every setting with its value and the GPO it comes from,
# including Group Policy Preferences (drives, printers, registry, files, tasks ...). Runs only on request.
RSOP_USER_RE = re.compile(r"^[\w.\- ]{1,64}\\[\w.\-$ ]{1,104}$")
RSOP_MAX_BYTES = 30 * 1024 * 1024


def rsop_script(scope="computer", user=None):
    if scope not in ("computer", "user"):
        raise ValueError("Bereich muss computer oder user sein")
    args = "'/scope', 'computer'"
    if scope == "user":
        if not user or not RSOP_USER_RE.match(str(user)):
            raise ValueError("Benutzer bitte als DOMÄNE\\Name angeben")
        args = f"'/user', {util.psq(user)}, '/scope', 'user'"
    # Explain/Supported (help texts of administrative templates) are dropped on the client - they make up most of the size
    return f"""$ErrorActionPreference = 'Stop'
$f = Join-Path $env:TEMP ('pk-gpresult-' + $PID + '-' + (Get-Random) + '.xml')
try {{
  $o = & (Join-Path $env:SystemRoot 'System32\\gpresult.exe') {args} '/x' $f '/f' 2>&1 | Out-String
  if (-not (Test-Path -LiteralPath $f)) {{ throw ('gpresult: ' + $o.Trim()) }}
  $x = Get-Content -LiteralPath $f -Raw
  $x = $x -replace '(?s)<(\\w+:)?Explain>.*?</(\\w+:)?Explain>', '' -replace '(?s)<(\\w+:)?Supported>.*?</(\\w+:)?Supported>', ''
  'PKXML:' + $x
}} finally {{ Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue }}
"""


def _ln(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _child(el, name):
    for c in el:
        if _ln(c.tag) == name:
            return c
    return None


def _txt(el, name, default=""):
    c = _child(el, name) if el is not None else None
    return (c.text or "").strip() if c is not None and c.text else default


def _gpo_id(el):
    """{GUID} of a <GPO> element (Identifier in the types namespace), upper case."""
    if el is None:
        return ""
    for c in el.iter():
        if _ln(c.tag) == "Identifier" and c.text:
            return c.text.strip().upper()
    return ""


# German labels for the item kinds that occur most often (unknown ones keep the XML name)
KINDS = {"Policy": "Administrative Vorlage", "RegistrySetting": "Registry-Wert", "SecurityOptions": "Sicherheitsoption",
         "Account": "Kontorichtlinie", "UserRightsAssignment": "Benutzerrecht", "Audit": "Überwachung",
         "AuditSetting": "Erweiterte Überwachung", "SystemServices": "Dienst (Sicherheit)", "RestrictedGroups": "Eingeschränkte Gruppe",
         "EventLog": "Ereignisprotokoll", "File": "Datei", "Registry": "Registry", "Script": "Skript",
         "Drive": "Laufwerk", "SharedPrinter": "Drucker", "PortPrinter": "Drucker (TCP/IP)", "LocalPrinter": "Drucker (lokal)",
         "Shortcut": "Verknüpfung", "Folder": "Ordner", "EnvironmentVariable": "Umgebungsvariable", "Ini": "INI-Datei",
         "Task": "Geplante Aufgabe", "TaskV2": "Geplante Aufgabe", "ImmediateTask": "Sofortige Aufgabe", "ImmediateTaskV2": "Sofortige Aufgabe",
         "NTService": "Dienst", "Group": "Lokale Gruppe", "User": "Lokaler Benutzer", "MsiApplication": "Software",
         "GlobalPowerOptions": "Energieoptionen", "PowerScheme": "Energieschema", "DataSource": "Datenquelle",
         "Device": "Gerät", "FolderOptions": "Ordneroptionen", "RegionalOptions": "Regionaleinstellungen",
         "InternetSettings": "Interneteinstellungen", "StartMenu": "Startmenü", "Application": "Anwendung",
         "Rule": "Firewall-Regel", "DomainProfile": "Firewall (Domäne)", "PrivateProfile": "Firewall (privat)",
         "PublicProfile": "Firewall (öffentlich)", "AutoEnrollmentSettings": "Zertifikate (automatisch)", "FolderRedirection": "Ordnerumleitung"}
_SKIP = {"GPO", "Precedence", "Explain", "Supported", "Filters", "Category", "Name", "State"}
_SKIP_ATTR = {"clsid", "uid", "image", "changed", "disabled", "userContext", "removePolicy", "bypassErrors", "desc"}


GPP_ACTIONS = {"C": "Erstellen", "R": "Ersetzen", "U": "Aktualisieren", "D": "Löschen"}


# Readable names for the GPP properties that occur most often (others keep their XML name)
GPP_LABELS = {"action": "Aktion", "path": "Pfad", "label": "Bezeichnung", "letter": "Laufwerksbuchstabe", "useLetter": "Buchstaben verwenden",
              "persistent": "Wiederherstellen", "thisDrive": "Dieses Laufwerk", "allDrives": "Alle Laufwerke", "userName": "Verbinden als",
              "targetPath": "Ziel", "targetType": "Zieltyp", "arguments": "Argumente", "startIn": "Ausführen in", "shortcutPath": "Verknüpfung",
              "iconPath": "Symbol", "hive": "Struktur", "key": "Schlüssel", "type": "Typ", "value": "Wert", "fromPath": "Quelle",
              "default": "Standard", "location": "Ort", "comment": "Kommentar", "serviceName": "Dienst", "startupType": "Starttyp",
              "serviceAction": "Dienstaktion", "groupName": "Gruppe", "newName": "Neuer Name", "section": "Abschnitt", "property": "Eigenschaft",
              "user": "Benutzer", "partial": "Teilweise", "readOnly": "Schreibgeschützt", "hidden": "Versteckt", "port": "Anschluss",
              "ipAddress": "IP-Adresse", "localName": "Lokaler Name"}


_PRIO = ["Aktion", "Laufwerksbuchstabe", "Pfad", "Ziel", "Struktur", "Schlüssel", "Wert", "Gruppe", "Dienst", "Starttyp"]


def _attr(out, k, v):
    if k == "action":
        v = GPP_ACTIONS.get(v, v)
    out.append([GPP_LABELS.get(k, k), v])


def _flat(el, out, prefix=""):
    """Readable key/value pairs of a setting (generic for every extension)."""
    for k, v in el.attrib.items():
        k = _ln(k)
        if k not in _SKIP_ATTR and v not in ("", None) and not (not prefix and k in ("name", "status")):
            _attr(out, prefix + k, v)
    for c in el:
        n = _ln(c.tag)
        if n in _SKIP and not prefix:
            continue
        if len(out) >= 60:
            return
        kids = list(c)
        if n in ("Properties", "Display"):  # GPP item values / security option value: without a prefix
            for k, v in c.attrib.items():
                if _ln(k) not in _SKIP_ATTR and v not in ("", None):
                    _attr(out, _ln(k), v)
            for d in kids:
                if _ln(d.tag) != "Name" and not list(d) and d.text and d.text.strip():
                    out.append([_ln(d.tag), d.text.strip()[:500]])
                elif list(d):
                    _flat(d, out, f"{_ln(d.tag)} › ")
            continue
        nm, val, st = _child(c, "Name"), _child(c, "Value"), _child(c, "State")
        if nm is not None and (val is not None or st is not None) and nm.text:
            v = " ".join(t.strip() for t in val.itertext() if t.strip()) if val is not None else ""
            sv = (st.text or "").strip() if st is not None else ""
            out.append([nm.text.strip(), v or {"Enabled": "Aktiviert", "Disabled": "Deaktiviert"}.get(sv, sv)])
        elif not kids and not c.attrib:
            if c.text and c.text.strip():
                out.append([prefix + n, c.text.strip()[:500]])
        else:
            _flat(c, out, f"{prefix}{n} › ")


def _filters(el):
    """GPP item-level targeting as short text lines."""
    f = _child(el, "Filters")
    if f is None:
        return []
    out = []
    for c in f.iter():
        if c is f or not _ln(c.tag).startswith("Filter"):
            continue
        a = {_ln(k): v for k, v in c.attrib.items()}
        bits = [f"{k}={v}" for k, v in a.items() if k not in ("bool", "not", "hidden", "userContext") and v]
        out.append(("NICHT " if a.get("not") == "1" else "") + _ln(c.tag)[6:] + (": " + ", ".join(bits) if bits else ""))
        if len(out) >= 20:
            break
    return out


def _title(item):
    t = _txt(item, "Name")
    disp = _child(item, "Display")
    if disp is not None and _txt(disp, "Name"):
        t = _txt(disp, "Name")
    if not t:
        t = item.attrib.get("name", "")
    if not t:
        for k in ("KeyName", "KeyPath", "Path", "Command", "GroupName"):
            if _txt(item, k):
                t = _txt(item, k)
                break
    props = _child(item, "Properties")
    if props is not None:
        a = props.attrib
        t = t or a.get("name") or a.get("path") or a.get("key") or a.get("groupName") or ""
    return t or _ln(item.tag)


def parse_rsop(xml_text):
    """gpresult /x output -> GPOs (with reason if not applied), extension status, settings. Pure function."""
    import xml.etree.ElementTree as ET
    text = str(xml_text or "")
    if len(text) > RSOP_MAX_BYTES:
        raise ValueError("Ergebnis von gpresult zu groß")
    if re.search(r"<!(DOCTYPE|ENTITY)", text[:20000], re.I):
        raise ValueError("Unerwartetes Format von gpresult")
    text = re.sub(r"^﻿?\s*<\?xml[^>]*\?>", "", text.strip())
    root = ET.fromstring(text)
    res = None
    for c in root:
        if _ln(c.tag) in ("ComputerResults", "UserResults"):
            res = c
            break
    if res is None:
        raise ValueError("gpresult hat keine Ergebnisse geliefert")
    out: dict[str, Any] = {"read_time": _txt(root, "ReadTime"), "name": _txt(res, "Name"), "domain": _txt(res, "Domain"),
           "site": _txt(res, "Site"), "som": _txt(res, "SOM"), "slow_link": _txt(res, "SlowLink") == "true",
           "groups": [], "gpos": [], "extensions": [], "settings": []}
    names: dict[str, dict[str, Any]] = {}
    for c in res:
        n = _ln(c.tag)
        if n == "SecurityGroup":
            grp = next((x.text for x in c if _ln(x.tag) == "Name" and x.text), "")
            if grp:
                out["groups"].append(grp.strip())
        elif n == "GPO":
            gid = _gpo_id(_child(c, "Path"))
            link = _child(c, "Link")
            enabled, valid = _txt(c, "Enabled", "true") == "true", _txt(c, "IsValid", "true") == "true"
            allowed, denied = _txt(c, "FilterAllowed", "true") == "true", _txt(c, "AccessDenied", "false") == "true"
            reason = ""
            if not enabled:
                reason = "Deaktiviert"
            elif denied:
                reason = "Zugriff verweigert (Sicherheitsfilterung)"
            elif not allowed:
                reason = "WMI-Filter trifft nicht zu" + (f" ({_txt(c, 'FilterName')})" if _txt(c, "FilterName") else "")
            elif not valid:
                reason = "Ungültig"
            elif link is not None and _txt(link, "Enabled", "true") != "true":
                reason = "Verknüpfung deaktiviert"
            g = {"id": gid, "name": _txt(c, "Name"), "link": _txt(link, "SOMPath") if link is not None else "",
                 "order": _txt(link, "AppliedOrder") if link is not None else "", "enforced": link is not None and _txt(link, "NoOverride") == "true",
                 "applied": not reason, "reason": reason, "version": _txt(c, "VersionSysvol"), "settings": 0}
            # the same GPO can be linked several times - one entry
            if gid and gid in names:
                continue
            names[gid] = g
            out["gpos"].append(g)
        elif n == "ExtensionStatus":
            out["extensions"].append({"name": _txt(c, "Name"), "id": _txt(c, "Identifier"), "status": _txt(c, "LoggingStatus"),
                                      "error": _txt(c, "Error", "0"), "begin": _txt(c, "BeginTime"), "end": _txt(c, "EndTime")})
        elif n == "ExtensionData":
            ext = _txt(c, "Name")
            ex = _child(c, "Extension")
            if ex is None:
                continue
            for item in ex.iter():
                gel = _child(item, "GPO") if item is not ex else None
                if gel is None or not _gpo_id(gel):
                    continue
                gid = _gpo_id(gel)
                details: list = []
                _flat(item, details)
                details.sort(key=lambda kv: _PRIO.index(kv[0]) if kv[0] in _PRIO else len(_PRIO))  # the essentials first
                kind = _ln(item.tag)
                st = {"gpo_id": gid, "gpo": (names.get(gid) or {}).get("name", gid), "ext": ext,
                      "kind": KINDS.get(kind, kind), "title": _title(item)[:300],
                      "category": _txt(item, "Category"), "state": _txt(item, "State"),
                      "precedence": int(_txt(item, "Precedence", "1") or 1) if _txt(item, "Precedence", "1").isdigit() else 1,
                      "details": details[:60], "filters": _filters(item)}
                out["settings"].append(st)
                if len(out["settings"]) >= 5000:
                    break
    # overridden settings (precedence > 1): which GPO won
    win = {(x["ext"], x["kind"], x["title"], x["category"]): x["gpo"] for x in out["settings"] if x["precedence"] == 1}
    for st in out["settings"]:
        if st["precedence"] > 1:
            st["winner"] = win.get((st["ext"], st["kind"], st["title"], st["category"]), "")
        if st["gpo_id"] in names and st["precedence"] == 1:
            names[st["gpo_id"]]["settings"] += 1
        if st["gpo"] == st["gpo_id"] and st["gpo_id"] in names:
            st["gpo"] = names[st["gpo_id"]]["name"]
    return out


def rsop(cid, scope="computer", user=None):
    _c, host = actions._host(cid)
    out, err, _ = remote.ps(host, rsop_script(scope, user), timeout=240)
    i = out.find("PKXML:")
    if i < 0:
        msg = util.first_line(err, "gpresult hat kein Ergebnis geliefert")
        if scope == "user" and re.search(r"RSoP|RSOP", msg):
            msg = f"Für {user} gibt es auf diesem Computer keine Richtliniendaten (noch nie angemeldet?). – {msg}"
        raise remote.RemoteError(msg[:500])
    r = parse_rsop(out[i + 6:])
    r["scope"], r["user"] = scope, user or ""
    return r
