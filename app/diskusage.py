"""Disk space analysis: determine folder sizes of a drive/folder on the client (like WinDirStat/TreeSize).

The client lists the folder with robocopy (/L, only listing; /XJ, never through junctions/symlinks) and sums it up per
folder (2.53.0 - before a compiled C# helper, which PowerShell logs as a warning); the server builds the pruned tree:
- down to the chosen depth; deeper levels only as a sum (click → scan more precisely)
- per folder the largest subfolders and files; small items below 0.1 % of the total volume combined
The result is stored per computer and path (the last runs remain retrievable).
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

# 2.53.0: the client lists the folder with robocopy (/L: only list, /XJ: never through junctions/links, UTF-16 log) and
# sums up per folder in PowerShell - no compiled C# helper any more (PowerShell logs every script with one as a warning,
# event 4104), and it also works in Constrained Language Mode. The tree is built and pruned on the server (_tree).
# Sent back per folder down to the chosen depth: direct size and file count, folders below the depth as a sum, the
# largest files of the folders above it (what can survive the pruning), the rest of the files as count and size.
CLIENT = r"""function PK-Du([string]$Root, [int]$Depth) {
  $t0 = Get-Date
  if ($Root -notmatch '\\$') { $Root += '\' }
  $rl = $Root.Length
  $log = Join-Path $env:TEMP ('pk_du_' + (Get-Date).ToString('yyMMddHHmmssfff') + '.log')
  $src = $Root; if ($src -notmatch '^[A-Za-z]:\\$') { $src = $src.TrimEnd('\') }
  $null = & robocopy.exe $src (Join-Path $env:TEMP 'pk_du_nirgends') /L /E /XJ /BYTES /NC /FP /NP /NJH /NJS /R:0 /W:0 ('/UNILOG:' + $log)
  $ents = @{}; $err = 0; $cur = $null; $curD = 0
  function PK-Ent($k) { $x = $ents[$k]; if (-not $x) { $x = @{ s = [long]0; c = [long]0; d = [long]0; x = 0; f = @{}; fn = 0 }; $ents[$k] = $x }; $x }
  # UTF-16 log (file names with any characters); read in one go - robocopy writes it with a byte order mark
  $lines = Get-Content -LiteralPath $log -Encoding Unicode -ReadCount 0 -ErrorAction SilentlyContinue   # one array with all lines
  if ($null -eq $lines) { $lines = @() }
  switch -Regex ($lines) {
    '^\s*(\d+)\t(.+)$' {
      $pth = $Matches[2]
      if ($pth.EndsWith('\')) {
        $rel = ''; if ($pth.Length -gt $rl) { $rel = $pth.Substring($rl).TrimEnd('\') }
        $parts = @(); if ($rel) { $parts = $rel.Split('\') }
        $curD = $parts.Count; $cur = $pth
        if ($curD -le $Depth) { $key = $rel; $null = PK-Ent $key }
        else { $key = ($parts[0..($Depth - 1)] -join '\'); $e2 = PK-Ent $key; $e2.d++ }
        $curK = $key
      } elseif ($cur) {
        $sz = [long]$Matches[1]; $e2 = $ents[$curK]; $e2.s += $sz; $e2.c++
        if ($curD -lt $Depth) { $e2.f[$e2.fn] = @($pth.Substring($cur.Length), $sz); $e2.fn++ }
      }
      continue
    }
    '\(0x[0-9A-Fa-f]{8}\).*?([A-Za-z]:\\.*)$' {
      $err++
      $pth = $Matches[1]
      if ($pth.Length -ge $rl -and $pth.StartsWith($Root, 'OrdinalIgnoreCase')) {
        $rel = $pth.Substring($rl).TrimEnd('\'); $parts = @(); if ($rel) { $parts = $rel.Split('\') }
        $key = $(if ($parts.Count -le $Depth) { $rel } else { $parts[0..($Depth - 1)] -join '\' }); $e2 = PK-Ent $key; $e2.x = 1
      }
    }
  }
  Remove-Item -LiteralPath $log -Force -ErrorAction SilentlyContinue
  $total = [long]0; foreach ($v in $ents.Values) { $total += $v.s }
  $min = [long]($total / 1000); if ($min -lt 1) { $min = 1 }
  $out = @{}
  foreach ($k in @($ents.Keys)) {
    $v = $ents[$k]; $o = @{ s = $v.s; c = $v.c }
    if ($v.d) { $o.d = $v.d }
    if ($v.x) { $o.x = 1 }
    if ($v.fn) {
      # only files that can stay after pruning: the 3 largest and those of at least 0.1 % of the total, at most 40
      $all = @($v.f.Values | Sort-Object { $_[1] } -Descending)
      $keep = @(); $rn = 0; $rs = [long]0; $i = 0
      foreach ($fi in $all) { if ($i -lt 40 -and ($i -lt 3 -or $fi[1] -ge $min)) { $keep += ,@($fi[0], $fi[1]) } else { $rn++; $rs += $fi[1] }; $i++ }
      $o.f = $keep; if ($rn) { $o.r = @($rn, $rs) }
    }
    $out[$k] = $o
  }
  @{ path = $Root; errors = $err; seconds = [int]((Get-Date) - $t0).TotalSeconds; dirs = $out }
}
function PK-DuDrive([string]$Root) {
  $l = $Root.Substring(0, 2)
  $d = Get-CimInstance Win32_LogicalDisk -Filter ("DeviceID='" + $l + "'") -ErrorAction SilentlyContinue
  if ($d) { @{ total = $d.Size; free = $d.FreeSpace; label = $d.VolumeName; fs = $d.FileSystem } }
}
"""


def _script(path, depth):
    return f"""$ErrorActionPreference = 'Stop'
{util.PS_EXPAND}{CLIENT}
$p = PK-Exp {util.psq(path)}
if ($p -match '^[A-Za-z]:$') {{ $p += '\\' }}
if (-not (Test-Path -LiteralPath $p -PathType Container)) {{ 'PKERR:Ordner nicht gefunden: ' + $p; return }}
$r = PK-Du $p {int(depth)}
$r.drive = PK-DuDrive $p
'PKDU:' + (ConvertTo-Json -InputObject $r -Depth 6 -Compress)
"""


def _tree(res, depth):
    """Tree like before (n name, s size, f file, fc/dc counts, x not readable, m more below, o "others", k children) from
    the folder sums of the client - built and pruned here: per folder at most 40 entries, nothing below 0.1 %."""
    root_path = str(res.get("path") or "")
    ents = res.get("dirs") or {}
    nodes: dict = {}

    def node(key):
        n = nodes.get(key)
        if n is None:
            d = 0 if key == "" else key.count("\\") + 1
            n = nodes[key] = {"n": root_path if key == "" else key.rsplit("\\", 1)[-1], "s": 0, "fc": 0, "dc": 0, "x": False,
                              "depth": d, "k": [], "files": [], "rest": (0, 0)}
            if key:
                node(key.rsplit("\\", 1)[0] if "\\" in key else "")
        return n
    node("")
    for key, e in ents.items():
        n = node(str(key))
        n["s"] += int(e.get("s") or 0)
        n["fc"] += int(e.get("c") or 0)
        n["dc"] += int(e.get("d") or 0)
        n["x"] = n["x"] or bool(e.get("x"))
        n["files"] = [(str(f[0]), int(f[1])) for f in (e.get("f") or []) if isinstance(f, list) and len(f) == 2]
        r = e.get("r") or [0, 0]
        n["rest"] = (int(r[0]), int(r[1]))
    for key in sorted(nodes, key=lambda k: -nodes[k]["depth"]):
        if key == "":
            continue
        n, p = nodes[key], nodes[key.rsplit("\\", 1)[0] if "\\" in key else ""]
        p["s"] += n["s"]
        p["fc"] += n["fc"]
        p["dc"] += n["dc"] + 1
        p["x"] = p["x"] or n["x"]
        p["k"].append(n)
    total = nodes[""]["s"]
    min_size = max(1, int(total * 0.001))

    def prune(n):
        if n["depth"] >= depth:
            return {"n": n["n"], "s": n["s"], "fc": n["fc"], "dc": n["dc"], **({"x": 1} if n["x"] else {}),
                    **({"m": 1} if n["dc"] > 0 or n["fc"] > 0 else {})}
        kids = [(k["s"], False, k) for k in n["k"]] + [(sz, True, nm) for nm, sz in n["files"]]
        kids.sort(key=lambda t: -t[0])
        keep, rest_d, rest_f, n_d = [], 0, n["rest"][1], 0
        n_f = n["rest"][0]
        for i, (sz, is_file, k) in enumerate(kids):
            if i < 40 and (sz >= min_size or i < 3) and sz > 0:
                keep.append({"n": k, "s": sz, "f": 1} if is_file else prune(k))
            elif is_file:
                rest_f += sz
                n_f += 1
            else:
                rest_d += sz
                n_d += 1
        if n_d:
            keep.append({"n": f"({n_d} weiterer Ordner)" if n_d == 1 else f"({n_d} weitere Ordner)", "s": rest_d, "fc": 0, "dc": 0, "o": 1})
        if n_f:
            keep.append({"n": f"({n_f} weitere Datei)" if n_f == 1 else f"({n_f} weitere Dateien)", "s": rest_f, "f": 1, "o": 1})
        out = {"n": n["n"], "s": n["s"], "fc": n["fc"], "dc": n["dc"], **({"x": 1} if n["x"] else {})}
        if keep:
            out["k"] = keep
        return out
    return prune(nodes[""])


def _meta(res):
    return {"path": res.get("path"), "errors": int(res.get("errors") or 0), "seconds": res.get("seconds"), "drive": res.get("drive")}


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
        i = out.find("PKDU:")
        if i < 0:
            raise ValueError("Keine Daten erhalten. " + (err or "").strip()[:500])
        res = json.loads(util.first_line(out[i + 5:], "{}"))
        meta, tree = _meta(res), _tree(res, depth)
        sid = _save(cid, meta.get("path") or path, meta, tree, depth, user)
        j.update(status="done", result_id=sid)
        pklog.info("tool", f"Speicherplatz-Analyse {meta.get('path') or path}: {tree.get('s', 0) / 1073741824:.1f} GB in "
                           f"{meta.get('seconds')} s", user=user, computer_id=cid)
    except Exception as e:
        j.update(status="error", error=str(e)[:1000])
    finally:
        j["finished"] = time.time()


# --- As a scanner ("Speicherplatz"): all fixed local drives in one pass -------
def _fixed_script(depth):
    return f"""$ErrorActionPreference = 'Stop'
{CLIENT}
foreach ($d in @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | Select-Object -First {MAX_DRIVES})) {{
  try {{
    $r = PK-Du ($d.DeviceID + '\\') {int(depth)}
    $r.drive = @{{ total = $d.Size; free = $d.FreeSpace; label = $d.VolumeName; fs = $d.FileSystem }}
    'PKDU:' + (ConvertTo-Json -InputObject $r -Depth 6 -Compress)
  }} catch {{ 'PKDRIVEERR:' + $d.DeviceID + ': ' + $_.Exception.Message }}
}}
'PKEND'
"""


def scan_fixed(cid, depth=None, user="Zeitplan", host=None):
    """Analyze all fixed local drives of the client (scanner "Speicherplatz"). Returns warnings."""
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
    for line in out.splitlines():
        if not line.startswith("PKDU:"):
            continue
        try:
            res = json.loads(line[5:].strip())
            meta = _meta(res)
            _save(cid, meta.get("path"), meta, _tree(res, depth), depth, user)
            saved += 1
            if meta.get("errors"):
                warns.append(f"{meta.get('path')}: {meta['errors']} Eintrag/Einträge nicht lesbar")
        except (ValueError, KeyError, TypeError):
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
    presence.direct_host(c)  # 2.36.3: never analyze under a foreign address
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
