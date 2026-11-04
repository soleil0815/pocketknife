"""Custom scanners: registry, files & folders, WMI, PowerShell.

Every active scanner is run on the client during the inventory scan; the results
end up as category "custom:<id>" in the inventory_data table.
"""
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
$pkFull = $ExecutionContext.SessionState.LanguageMode -eq 'FullLanguage'
foreach ($k in $paths) {{
  # 2.38: in Constrained Language Mode no methods of the registry classes - read values via Get-ItemProperty (without type)
  $pv = if ($pkFull) {{ $null }} else {{ Get-ItemProperty -LiteralPath $k.PSPath -ErrorAction SilentlyContinue }}
  foreach ($n in $(if ($pkFull) {{ $k.GetValueNames() }} else {{ @($k.Property) }})) {{
    $v = if ($pkFull) {{ $k.GetValue($n) }} elseif ($n -eq '(default)') {{ $pv.'(default)' }} else {{ $pv.$n }}
    if ($v -is [array]) {{ $v = ($v | ForEach-Object {{ "$_" }}) -join ', ' }}
    [ordered]@{{ key = ($k.Name -replace '^HKEY_LOCAL_MACHINE', 'HKLM' -replace '^HKEY_CURRENT_USER', 'HKCU'); name = $(if ($n -and $n -ne '(default)') {{ $n }} else {{ '(Standard)' }}); value = "$v"
      type = $(if ($pkFull) {{ "$($k.GetValueKind($n))" }} else {{ '' }}) }}
  }}
}}"""
    if t == "files":
        recurse = "-Recurse" if extra.get("recurse") else ""
        # resolve environment variables (%SYSTEMDRIVE%, $env:ProgramData); "**" = any number of folder levels
        return f"""
{util.PS_EXPAND}$pkPath = PK-Exp {util.psq(target)}
# resolve $env:NAME - without [regex]::Replace with a script block (Constrained Language Mode, 2.38)
while ($pkPath -match '^(.*?)\\$env:(\\w+)(.*)$') {{
  $pkV = Get-Item -LiteralPath ('env:' + $Matches[2]) -ErrorAction SilentlyContinue
  if (-not $pkV) {{ break }}
  $pkPath = $Matches[1] + $pkV.Value + $Matches[3]
}}
if ($pkPath.Contains('**')) {{
  $pkBase = $pkPath.Substring(0, $pkPath.IndexOf('**'))
  $w = $pkBase.IndexOfAny([char[]]'*?')
  if ($w -ge 0) {{ $pkBase = $pkBase.Substring(0, $pkBase.LastIndexOfAny([char[]]'\\/', $w) + 1) }}
  $pkBase = $pkBase.TrimEnd('\\', '/')
  if ($pkBase -match '^[A-Za-z]:$' -or -not $pkBase) {{ $pkBase += '\\' }}
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
    # powershell: robustly convert the output into table rows - objects, hashtables, text, Write-Host,
    # Format-Table/-List (taken over as text) and error messages
    # Custom code is passed as Base64 and only compiled at run time: a syntax error (e.g. a
    # missing bracket) then affects only this scanner (column "fehler") instead of the whole inventory script
    # 2.38: as a text literal and Invoke-Expression (allowed in Constrained Language Mode; [ScriptBlock]::Create is not).
    # A syntax error still affects only this scanner (column "fehler").
    return f"""
$ErrorActionPreference = 'Continue'
$pkCode = {util.psq(target or '')}
$pkOut = @(try {{ Invoke-Expression $pkCode 2>&1 3>&1 6>&1 }} catch {{ $_ }})
if ($pkOut | Where-Object {{ $_ -ne $null -and (@($_.PSObject.TypeNames) -like 'Microsoft.PowerShell.Commands.Internal.Format.*') }}) {{
  $pkOut = @(($pkOut | Out-String -Width 400) -split "`r?`n" | Where-Object {{ $_.Trim() }})
}}
$pkOut | Where-Object {{ $_ -ne $null }} | Select-Object -First {MAX_ROWS} | ForEach-Object {{
  $o = $_
  if (@($o.PSObject.TypeNames) -contains 'System.Management.Automation.InformationRecord') {{ [ordered]@{{ value = "$($o.MessageData)" }} }}
  elseif (@($o.PSObject.TypeNames) -contains 'System.Management.Automation.ErrorRecord') {{ [ordered]@{{ fehler = "$($o.Exception.Message)" }} }}
  elseif (@($o.PSObject.TypeNames) -contains 'System.Management.Automation.WarningRecord') {{ [ordered]@{{ warnung = "$($o.Message)" }} }}
  elseif ($o -is [datetime]) {{ [ordered]@{{ value = $o.ToString('yyyy-MM-dd HH:mm:ss') }} }}
  elseif ($o -is [string] -or $o -is [int] -or $o -is [long] -or $o -is [double] -or $o -is [decimal] -or $o -is [bool] -or $o -is [char] -or $o -is [guid]) {{ [ordered]@{{ value = "$o" }} }}
  elseif ($o -is [hashtable] -or @($o.PSObject.TypeNames) -contains 'System.Collections.Specialized.OrderedDictionary') {{
    $h = [ordered]@{{}}; foreach ($k in @($o.Keys) | Select-Object -First 40) {{ $v = $o[$k]
      if ($v -is [datetime]) {{ $v = $v.ToString('yyyy-MM-dd HH:mm:ss') }} elseif ($v -is [array]) {{ $v = ($v | ForEach-Object {{ "$_" }}) -join ', ' }} elseif ($null -ne $v) {{ $v = "$v" }}
      $h["$k"] = $v }}
    $h
  }}
  else {{ PKO $o }}
}}"""


def script(ids=None):
    """Script part for custom scanners. ids=None: all active ones, otherwise only the given (active) ones."""
    rows = db.q("SELECT * FROM custom_scanners WHERE enabled=1 ORDER BY id")
    if ids is not None:
        want = {int(i) for i in ids}
        rows = [r for r in rows if r["id"] in want]
    return script_for(rows)


def script_for(rows):
    if not rows:
        return ""
    # Hashtable instead of [ordered]: its indexer always treats a key as a key (never as a position)
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
