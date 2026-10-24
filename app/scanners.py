"""Eigene Scanner: Registry, Dateien & Ordner, WMI, PowerShell.

Jeder aktive Scanner wird beim Inventar-Scan auf dem Client ausgeführt; die Ergebnisse
landen als Kategorie „custom:<id>“ in der Tabelle inventory_data.
"""
import base64
import json

from . import db, util

TYPES = {
    "registry": "Registry",
    "files": "Dateien & Ordner",
    "wmi": "WMI",
    "powershell": "PowerShell",
}
MAX_ROWS = 500


def validate(d):
    if not (d.get("name") or "").strip():
        raise ValueError("Name fehlt.")
    if d.get("type") not in TYPES:
        raise ValueError("Ungültiger Scanner-Typ.")
    if not (d.get("target") or "").strip():
        raise ValueError({"registry": "Registry-Pfad angeben (z. B. HKLM:\\SOFTWARE\\Firma).",
                          "files": "Pfad angeben (z. B. C:\\Programme\\Firma\\*.exe).",
                          "wmi": "WMI-Abfrage angeben (z. B. SELECT * FROM Win32_Battery).",
                          "powershell": "PowerShell-Code angeben."}[d["type"]])


def _body(sc):
    t, target = sc["type"], sc["target"]
    extra = json.loads(sc.get("extra") or "{}")
    if t == "registry":
        recurse = "-Recurse -Depth 3" if extra.get("recurse") else ""
        return f"""
$paths = @(Get-Item -Path {util.psq(target)} -ErrorAction SilentlyContinue)
if ({'$true' if extra.get('recurse') else '$false'}) {{ $paths += @(Get-ChildItem -Path {util.psq(target)} {recurse} -ErrorAction SilentlyContinue) }}
foreach ($k in $paths) {{
  foreach ($n in $k.GetValueNames()) {{
    $v = $k.GetValue($n)
    if ($v -is [array]) {{ $v = ($v | ForEach-Object {{ "$_" }}) -join ', ' }}
    [ordered]@{{ key = ($k.Name -replace '^HKEY_LOCAL_MACHINE', 'HKLM' -replace '^HKEY_CURRENT_USER', 'HKCU'); name = $(if ($n) {{ $n }} else {{ '(Standard)' }}); value = "$v"; type = "$($k.GetValueKind($n))" }}
  }}
}}"""
    if t == "files":
        recurse = "-Recurse" if extra.get("recurse") else ""
        # Umgebungsvariablen (%SYSTEMDRIVE%, $env:ProgramData) auflösen; „**“ = beliebig viele Ordnerebenen
        return f"""
$pkPath = [Environment]::ExpandEnvironmentVariables({util.psq(target)})
$pkPath = [regex]::Replace($pkPath, '\\$env:(\\w+)', {{ param($m) $v = [Environment]::GetEnvironmentVariable($m.Groups[1].Value); if ($null -ne $v) {{ $v }} else {{ $m.Value }} }}, 'IgnoreCase')
if ($pkPath.Contains('**')) {{
  $pkBase = $pkPath.Substring(0, $pkPath.IndexOf('**'))
  $w = $pkBase.IndexOfAny([char[]]'*?')
  if ($w -ge 0) {{ $pkBase = $pkBase.Substring(0, $pkBase.LastIndexOfAny([char[]]'\\/', $w) + 1) }}
  $pkBase = $pkBase.TrimEnd('\\', '/')
  if ($pkBase -match '^[A-Za-z]:$' -or -not $pkBase) {{ $pkBase += [IO.Path]::DirectorySeparatorChar }}
  $pkLeaf = Split-Path $pkPath -Leaf
  $pkLike = $pkPath.Replace('**', '*')
  $pkItems = Get-ChildItem -LiteralPath $pkBase -Recurse -Force -Filter $pkLeaf -ErrorAction SilentlyContinue | Where-Object {{ $_.FullName -like $pkLike }}
}} else {{
  $pkItems = Get-ChildItem -Path $pkPath {recurse} -Force -ErrorAction SilentlyContinue
}}
$pkItems | Select-Object -First {MAX_ROWS} | ForEach-Object {{
  [ordered]@{{ path = $_.FullName; type = $(if ($_.PSIsContainer) {{ 'Ordner' }} else {{ 'Datei' }}); size = $(if ($_.PSIsContainer) {{ $null }} else {{ [int64]$_.Length }})
    modified = (D $_.LastWriteTime); created = (D $_.CreationTime)
    version = $(if (-not $_.PSIsContainer -and $_.VersionInfo) {{ $_.VersionInfo.FileVersion }} else {{ $null }})
    product = $(if (-not $_.PSIsContainer -and $_.VersionInfo) {{ $_.VersionInfo.ProductName }} else {{ $null }}) }}
}}"""
    if t == "wmi":
        ns = extra.get("namespace") or "root\\cimv2"
        return f"""
Get-CimInstance -Namespace {util.psq(ns)} -Query {util.psq(target)} -ErrorAction Stop | Select-Object -First {MAX_ROWS} | ForEach-Object {{ PKO $_ }}"""
    # powershell: Ausgabe robust in Tabellenzeilen umwandeln – Objekte, Hashtables, Texte, Write-Host,
    # Format-Table/-List (wird als Text übernommen) und Fehlermeldungen
    # Eigener Code wird als Base64 übergeben und erst zur Laufzeit übersetzt: ein Syntaxfehler (z. B. eine
    # fehlende Klammer) trifft dann nur diesen Scanner (Spalte „fehler“) statt das ganze Inventarskript
    b64 = base64.b64encode((target or "").encode("utf-8")).decode()
    return f"""
$ErrorActionPreference = 'Continue'
$pkSb = [ScriptBlock]::Create([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{b64}')))
$pkOut = @(& $pkSb 2>&1 3>&1 6>&1)
if ($pkOut | Where-Object {{ $_ -ne $null -and $_.GetType().FullName -like 'Microsoft.PowerShell.Commands.Internal.Format.*' }}) {{
  $pkOut = @(($pkOut | Out-String -Width 400) -split "`r?`n" | Where-Object {{ $_.Trim() }})
}}
$pkOut | Where-Object {{ $_ -ne $null }} | Select-Object -First {MAX_ROWS} | ForEach-Object {{
  $o = $_
  if ($o -is [System.Management.Automation.InformationRecord]) {{ [ordered]@{{ value = "$($o.MessageData)" }} }}
  elseif ($o -is [System.Management.Automation.ErrorRecord]) {{ [ordered]@{{ fehler = "$($o.Exception.Message)" }} }}
  elseif ($o -is [System.Management.Automation.WarningRecord]) {{ [ordered]@{{ warnung = "$($o.Message)" }} }}
  elseif ($o -is [datetime]) {{ [ordered]@{{ value = $o.ToString('yyyy-MM-dd HH:mm:ss') }} }}
  elseif ($o -is [string] -or $o -is [ValueType]) {{ [ordered]@{{ value = "$o" }} }}
  elseif ($o -is [System.Collections.IDictionary]) {{
    $h = [ordered]@{{}}; foreach ($k in @($o.Keys) | Select-Object -First 40) {{ $v = $o[$k]
      if ($v -is [datetime]) {{ $v = $v.ToString('yyyy-MM-dd HH:mm:ss') }} elseif ($v -is [array]) {{ $v = ($v | ForEach-Object {{ "$_" }}) -join ', ' }} elseif ($null -ne $v) {{ $v = "$v" }}
      $h["$k"] = $v }}
    $h
  }}
  else {{ PKO $o }}
}}"""


def script(ids=None):
    """Skriptteil für eigene Scanner. ids=None: alle aktiven, sonst nur die angegebenen (aktiven)."""
    rows = db.q("SELECT * FROM custom_scanners WHERE enabled=1 ORDER BY id")
    if ids is not None:
        want = {int(i) for i in ids}
        rows = [r for r in rows if r["id"] in want]
    return script_for(rows)


def script_for(rows):
    if not rows:
        return ""
    # Hashtable statt [ordered]: deren Indexer nimmt Schlüssel immer als Schlüssel (nie als Position)
    parts = ["$r.custom = @{}"]
    for sc in rows:
        key = str(sc["id"])
        parts.append(f"""try {{ $r.custom['{key}'] = @(& {{ {_body(sc)} }}) }} catch {{ $r.custom['{key}'] = @([ordered]@{{ fehler = $_.Exception.Message }}) }}
$ErrorActionPreference = 'SilentlyContinue'""")
    return "\n".join(parts) + "\n"


def list_all():
    rows = db.q("SELECT * FROM custom_scanners ORDER BY type, name")
    for r in rows:
        r["extra"] = json.loads(r.get("extra") or "{}")
        r["type_label"] = TYPES.get(r["type"], r["type"])
    return rows
