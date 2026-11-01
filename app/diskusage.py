"""Speicherplatz-Analyse: Ordnergrößen eines Laufwerks/Ordners auf dem Client ermitteln (wie WinDirStat/TreeSize).

Der Client durchläuft das Dateisystem mit einem kleinen C#-Helfer (Add-Type, schnell auch bei 100 000+ Ordnern),
folgt keinen Verknüpfungen (Junctions/Symlinks) und liefert einen beschnittenen Baum als JSON:
- bis zur gewählten Tiefe; tiefere Ebenen nur als Summe (Klick → genauer scannen)
- je Ordner die größten Unterordner und Dateien; Kleinkram unter 0,1 % des Gesamtvolumens zusammengefasst
Das Ergebnis wird je Computer und Pfad gespeichert (die letzten Läufe bleiben abrufbar).
"""
import json
import re
import secrets
import threading
import time

from . import config, db, pklog, remote, util

KEEP_PER_COMPUTER = 8
DEPTH_DEFAULT = 4
MAX_DRIVES = 12
PATH_RE = re.compile(r'^(%[A-Za-z_]+%|[A-Za-z]:)(\\[^<>"|?*\r\n]*)?$')

_jobs = {}
_lock = threading.Lock()

CS = r"""
using System;
using System.IO;
using System.Text;
using System.Collections.Generic;
public class PKDu {
  public class N { public string Name; public long Size; public long Files; public long Dirs; public bool Denied; public bool File;
    public List<N> Kids = new List<N>(); public bool More; public int Hidden; public long HiddenSize; public bool Other; }
  public static long Errors = 0;
  static int MaxDepth;
  public static N Scan(string path, int maxDepth) { MaxDepth = maxDepth; Errors = 0;
    var di = new DirectoryInfo(path); var n = Walk(di, 0); n.Name = di.FullName; return n; }
  static N Walk(DirectoryInfo d, int depth) {
    var n = new N(); n.Name = d.Name; bool keep = depth < MaxDepth;
    List<N> files = keep ? new List<N>() : null;
    IEnumerable<FileSystemInfo> items;
    try { items = d.EnumerateFileSystemInfos(); } catch (Exception) { n.Denied = true; Errors++; return n; }
    try {
      foreach (var it in items) {
        try {
          if ((it.Attributes & FileAttributes.ReparsePoint) != 0) continue;   // Junctions/Symlinks nicht doppelt zählen
          var fi = it as FileInfo;
          if (fi != null) { long l = fi.Length; n.Size += l; n.Files++;
            if (keep) files.Add(new N { Name = fi.Name, Size = l, File = true, Files = 1 }); continue; }
          var c = Walk((DirectoryInfo)it, depth + 1);
          n.Size += c.Size; n.Files += c.Files; n.Dirs += c.Dirs + 1; if (c.Denied) n.Denied = true;
          if (keep) n.Kids.Add(c); else if (c.Dirs > 0 || c.Files > 0) n.More = true;
        } catch (Exception) { Errors++; }
      }
    } catch (Exception) { n.Denied = true; Errors++; }
    if (keep) { n.Kids.AddRange(files); n.More = false; }
    else { n.Kids = null; n.More = n.Dirs > 0 || n.Files > 0; }
    return n;
  }
  // Beschneiden: je Ordner höchstens maxKids Einträge und nichts unter minSize – Rest zusammengefasst
  public static void Prune(N n, long minSize, int maxKids) {
    if (n.Kids == null) return;
    n.Kids.Sort((a, b) => b.Size.CompareTo(a.Size));
    var keep = new List<N>(); long restD = 0, restF = 0; int nD = 0, nF = 0;
    for (int i = 0; i < n.Kids.Count; i++) { var k = n.Kids[i];
      if (i < maxKids && (k.Size >= minSize || i < 3) && k.Size > 0) { keep.Add(k); if (!k.File) Prune(k, minSize, maxKids); }
      else if (k.File) { restF += k.Size; nF++; } else { restD += k.Size; nD++; } }
    if (nD > 0) keep.Add(new N { Name = "(" + nD + (nD == 1 ? " weiterer Ordner)" : " weitere Ordner)"), Size = restD, Other = true, Hidden = nD });
    if (nF > 0) keep.Add(new N { Name = "(" + nF + (nF == 1 ? " weitere Datei)" : " weitere Dateien)"), Size = restF, Other = true, File = true, Hidden = nF });
    n.Kids = keep;
  }
  static void Esc(StringBuilder sb, string s) { sb.Append('"');
    foreach (char ch in s) { if (ch == '"' || ch == '\\') { sb.Append('\\'); sb.Append(ch); }
      else if (ch < ' ') sb.Append("\\u" + ((int)ch).ToString("x4")); else sb.Append(ch); }
    sb.Append('"'); }
  public static void Json(N n, StringBuilder sb) {
    sb.Append("{\"n\":"); Esc(sb, n.Name ?? ""); sb.Append(",\"s\":").Append(n.Size);
    if (n.File) sb.Append(",\"f\":1"); else sb.Append(",\"fc\":").Append(n.Files).Append(",\"dc\":").Append(n.Dirs);
    if (n.Denied) sb.Append(",\"x\":1"); if (n.More) sb.Append(",\"m\":1"); if (n.Other) sb.Append(",\"o\":1");
    if (n.Kids != null && n.Kids.Count > 0) { sb.Append(",\"k\":["); for (int i = 0; i < n.Kids.Count; i++) { if (i > 0) sb.Append(','); Json(n.Kids[i], sb); } sb.Append(']'); }
    sb.Append('}'); }
}
"""


CLM_MSG = ("Die Speicherplatz-Analyse braucht volle PowerShell – auf diesem Computer läuft PowerShell im eingeschränkten "
           "Sprachmodus (Softwareeinschränkung, AppLocker oder App Control). Alternative: Dateien (Explorer) bzw. ein Scanner "
           "„Dateien & Ordner“.")


def _script(path, depth):
    # 2.38: im Constrained Language Mode gibt es kein Add-Type – klar sagen statt mit einem Fehler abbrechen
    return f"""$ErrorActionPreference = 'Stop'
if ($ExecutionContext.SessionState.LanguageMode -ne 'FullLanguage') {{ 'PKERR:' + {util.psq(CLM_MSG)}; return }}
if (-not ('PKDu' -as [type])) {{ Add-Type -TypeDefinition @'
{CS}
'@ -Language CSharp }}
$p = [Environment]::ExpandEnvironmentVariables({util.psq(path)})
if ($p -match '^[A-Za-z]:$') {{ $p += '\\' }}
if (-not (Test-Path -LiteralPath $p -PathType Container)) {{ 'PKERR:Ordner nicht gefunden: ' + $p; return }}
$t0 = Get-Date
$root = [PKDu]::Scan($p, {int(depth)})
[PKDu]::Prune($root, [long][Math]::Max(1, $root.Size * 0.001), 40)
$sb = New-Object System.Text.StringBuilder
[PKDu]::Json($root, $sb)
$drv = $null
try {{ $d = New-Object System.IO.DriveInfo ([IO.Path]::GetPathRoot($p)); $drv = @{{ total = $d.TotalSize; free = $d.AvailableFreeSpace; label = $d.VolumeLabel; fs = $d.DriveFormat }} }} catch {{}}
'PKMETA:' + (@{{ path = $p; errors = [PKDu]::Errors; seconds = [int]((Get-Date) - $t0).TotalSeconds; drive = $drv }} | ConvertTo-Json -Compress)
'PKTREE:' + $sb.ToString()
"""


def clean_path(path):
    p = (path or "").strip().strip('"').replace("/", "\\")
    if re.match(r"^[A-Za-z]:$", p):
        p += "\\"
    if not PATH_RE.match(p) or ".." in p.split("\\"):
        raise ValueError("Bitte einen lokalen Pfad angeben, z. B. C:\\ oder D:\\Daten (auch %SystemDrive%\\Users).")
    return p


def _save(cid, path, meta, tree, depth, user):
    sid = db.ex("INSERT INTO disk_usage(computer_id, path, ts, total, seconds, created_by, json) VALUES(?,?,?,?,?,?,?)",
                (cid, meta.get("path") or path, util.now(), tree.get("s", 0), meta.get("seconds"), user,
                 json.dumps({"meta": meta, "tree": tree, "depth": depth}, ensure_ascii=False, separators=(",", ":"))))
    old = [r["id"] for r in db.q("SELECT id FROM disk_usage WHERE computer_id=? ORDER BY id DESC", (cid,))][KEEP_PER_COMPUTER:]
    for o in old:
        db.ex("DELETE FROM disk_usage WHERE id=?", (o,))
    return sid


def _run(jid, cid, path, depth, user):
    j = _jobs[jid]
    c = db.q1("SELECT * FROM computers WHERE id=?", (cid,))
    try:
        from . import presence
        host = presence.direct_host(c)
        out, err, _ = remote.ps(host, _script(path, depth), timeout=1800)
        if "PKERR:" in out:
            raise ValueError(util.first_line(out.split("PKERR:", 1)[1], "Fehler"))
        mi, ti = out.find("PKMETA:"), out.find("PKTREE:")
        if mi < 0 or ti < 0:
            raise ValueError("Keine Daten erhalten. " + (err or "").strip()[:500])
        meta = json.loads(util.first_line(out[mi + 7:], "{}"))
        tree = json.loads(out[ti + 7:].strip())
        sid = _save(cid, meta.get("path") or path, meta, tree, depth, user)
        j.update(status="done", result_id=sid)
        pklog.info("tool", f"Speicherplatz-Analyse {meta.get('path') or path}: {tree.get('s', 0) / 1073741824:.1f} GB in "
                           f"{meta.get('seconds')} s", user=user, computer_id=cid)
    except Exception as e:
        j.update(status="error", error=str(e)[:1000])
    finally:
        j["finished"] = time.time()


# --- Als Scanner („Speicherplatz“): alle festen lokalen Laufwerke in einem Durchlauf -------
def _fixed_script(depth):
    return f"""$ErrorActionPreference = 'Stop'
if ($ExecutionContext.SessionState.LanguageMode -ne 'FullLanguage') {{ 'PKDRIVEERR:' + {util.psq(CLM_MSG)}; return }}
if (-not ('PKDu' -as [type])) {{ Add-Type -TypeDefinition @'
{CS}
'@ -Language CSharp }}
$drives = [System.IO.DriveInfo]::GetDrives() | Where-Object {{ $_.DriveType -eq 'Fixed' -and $_.IsReady }} | Select-Object -First {MAX_DRIVES}
foreach ($d in $drives) {{
  try {{
    $t0 = Get-Date
    $root = [PKDu]::Scan($d.RootDirectory.FullName, {int(depth)})
    [PKDu]::Prune($root, [long][Math]::Max(1, $root.Size * 0.001), 40)
    $sb = New-Object System.Text.StringBuilder
    [PKDu]::Json($root, $sb)
    'PKDRIVE:' + (@{{ path = $d.RootDirectory.FullName; errors = [PKDu]::Errors; seconds = [int]((Get-Date) - $t0).TotalSeconds;
      drive = @{{ total = $d.TotalSize; free = $d.AvailableFreeSpace; label = $d.VolumeLabel; fs = $d.DriveFormat }} }} | ConvertTo-Json -Compress)
    'PKTREE:' + $sb.ToString()
  }} catch {{ 'PKDRIVEERR:' + $d.Name + ': ' + $_.Exception.Message }}
}}
'PKEND'
"""


def scan_fixed(cid, depth=None, user="Zeitplan", host=None):
    """Alle festen lokalen Laufwerke des Clients analysieren (Scanner „Speicherplatz“). Gibt Warnhinweise zurück."""
    depth = max(1, min(int(depth if depth is not None else (db.setting("du_scan_depth") or DEPTH_DEFAULT)), 8))
    c = db.q1("SELECT * FROM computers WHERE id=?", (cid,))
    if not c:
        raise ValueError("Computer nicht gefunden")
    host = host or c["dns_name"] or c["name"]
    out, err, _ = remote.ps(host, _fixed_script(depth), timeout=1800)
    warns, saved = [], 0
    for line in out.splitlines():
        if line.startswith("PKDRIVEERR:"):
            warns.append("Speicherplatz " + line[len("PKDRIVEERR:"):].strip())
    # Blöcke PKDRIVE:…\nPKTREE:… paarweise lesen
    parts = out.split("PKDRIVE:")
    for block in parts[1:]:
        try:
            meta_line, _, rest = block.partition("\n")
            ti = rest.find("PKTREE:")
            if ti < 0:
                continue
            meta = json.loads(meta_line.strip())
            tree = json.loads(util.first_line(rest[ti + 7:], "{}"))
            _save(cid, meta.get("path"), meta, tree, depth, user)
            saved += 1
            if meta.get("errors"):
                warns.append(f"{meta.get('path')}: {meta['errors']} Eintrag/Einträge nicht lesbar")
        except (ValueError, KeyError):
            continue
    if not saved and "PKEND" not in out:
        raise remote.RemoteError(util.first_line(err, "Speicherplatz-Scan ohne Ergebnis")[:400])
    pklog.info("scan", f"Speicherplatz-Scanner: {saved} Laufwerk(e) auf {c['name']} analysiert (Tiefe {depth}).",
               user=user, computer_id=cid)
    return warns


def start(cid, path, depth, user):
    path = clean_path(path)
    depth = max(1, min(int(depth or 4), 8))
    c = db.q1("SELECT id, name, dns_name, addr_problem, addr_detail FROM computers WHERE id=?", (cid,))
    if not c:
        raise ValueError("Computer nicht gefunden")
    from . import presence
    presence.direct_host(c)  # 2.36.3: nie unter einer fremden Adresse analysieren
    jid = secrets.token_hex(6)
    with _lock:
        for k in [k for k, v in _jobs.items() if v.get("finished") and time.time() - v["finished"] > 3600]:
            _jobs.pop(k, None)
        _jobs[jid] = {"id": jid, "computer_id": cid, "path": path, "status": "running", "started": time.time()}
    threading.Thread(target=_run, args=(jid, cid, path, depth, user), daemon=True, name="diskusage").start()
    return jid


def job(jid):
    j = _jobs.get(jid)
    if not j:
        return None
    return {**j, "elapsed": int((j.get("finished") or time.time()) - j["started"])}


def listing(cid):
    return db.q("SELECT id, path, ts, total, seconds, created_by FROM disk_usage WHERE computer_id=? ORDER BY id DESC", (cid,))


def get(sid):
    r = db.q1("SELECT * FROM disk_usage WHERE id=?", (sid,))
    if not r:
        return None
    doc = json.loads(r.pop("json") or "{}")
    return {**r, **doc}
