"""2.38.4: "gpupdate /force" on a client - computer AND user policies of the logged-on users.

Over WinRM, gpupdate runs in the service account (network logon). The computer policy is refreshed correctly that way,
but the user policy only for the service account itself - not for the users logged on at the computer. User settings
(e.g. browser bookmarks, drives, desktop) therefore only arrived at the next logon or background refresh, even though
gpupdate reported "success".

Hence: computer policy directly as before; for each logged-on user (owner of explorer.exe) a short scheduled task in
their session (Interactive, limited rights, no password) runs "gpupdate /target:user /force" - this is also what
Invoke-GPUpdate does. Output and exit code come back through a folder under %SystemRoot%\\Temp that is additionally
shared with that user only. Invisible from Windows 10 1903 on (conhost --headless), otherwise a console window
flashes briefly.
Constrained-Language-compatible: only cmdlets, CIM and programs from System32.
"""
import secrets

WAIT_SECONDS = 120  # wait at most this long for the user policies

SCRIPT = r"""$ErrorActionPreference = 'Continue'
$id = '__ID__'
$pkTask = '\POCKETKNIFE\'
$sys = Join-Path $env:SystemRoot 'System32'
# remove leftovers of earlier runs (aborted, timed out) - running tasks stay
Get-ScheduledTask -TaskPath $pkTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -like 'PK_GP_*' -and $_.State -ne 'Running' } |
  ForEach-Object { Unregister-ScheduledTask -TaskName $_.TaskName -TaskPath $pkTask -Confirm:$false -ErrorAction SilentlyContinue }
# 2.38.10 (Audit): delete with "rd /s /q" - it removes junctions/links themselves and never follows them. Remove-Item -Recurse
# of Windows PowerShell 5.1 follows junctions: a user who can write in the folder (or created a pk_gp_* folder himself
# under %SystemRoot%\Temp) could otherwise have arbitrary files deleted with administrator rights.
function PK-GpRemove($p) { & (Join-Path $sys 'cmd.exe') /d /c ('rd /s /q "' + $p + '"') 2>$null | Out-Null }
$old = (Get-Date).AddHours(-1)
Get-ChildItem -Path (Join-Path $env:SystemRoot 'Temp') -Filter 'pk_gp_*' -Directory -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -match '^pk_gp_[0-9a-zA-Z]+$' -and $_.LastWriteTime -lt $old -and "$($_.Attributes)" -notmatch 'ReparsePoint' } |
  ForEach-Object { PK-GpRemove $_.FullName }

'=== Computerrichtlinie ==='
(& (Join-Path $sys 'cmd.exe') /d /c 'echo N| gpupdate.exe /target:computer /force' | Out-String).Trim()

# Logged-on users: owner of explorer.exe or sihost.exe (also kiosk with its own shell), once per account
$users = @()
foreach ($p in @(Get-CimInstance Win32_Process -Filter "Name='explorer.exe' OR Name='sihost.exe'" -ErrorAction SilentlyContinue)) {
  $o = Invoke-CimMethod -InputObject $p -MethodName GetOwner -ErrorAction SilentlyContinue
  if ($o -and $o.User) {
    $n = $o.Domain + '\' + $o.User
    if ($users -notcontains $n) { $users += $n }
  }
}
''
if (-not $users) {
  '=== Benutzerrichtlinie ==='
  'Kein Benutzer angemeldet – Benutzerrichtlinien werden bei der nächsten Anmeldung angewendet.'
  return
}
$dir = Join-Path $env:SystemRoot ('Temp\pk_gp_' + $id)
New-Item -ItemType Directory -Path $dir -Force | Out-Null
$build = 0
try { $build = [int](Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -ErrorAction Stop).CurrentBuildNumber } catch {}
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
$started = @{}
$k = 0
foreach ($u in $users) {
  $k++
  $of = Join-Path $dir ('o' + $k + '.txt')
  $rf = Join-Path $dir ('r' + $k + '.txt')
  try {
    & icacls.exe $dir /grant ($u + ':(OI)(CI)M') /Q | Out-Null
    # gpupdate output and exit code go into the folder; "N" answers prompts about logoff/restart
    $c = '/d /v:on /s /c "echo N| gpupdate.exe /target:user /force >"' + $of + '" 2>&1 & (echo !errorlevel!)>"' + $rf + '""'
    if ($build -ge 18362) {
      $a = New-ScheduledTaskAction -Execute (Join-Path $sys 'conhost.exe') -Argument ('--headless "' + (Join-Path $sys 'cmd.exe') + '" ' + $c)
    } else {
      $a = New-ScheduledTaskAction -Execute (Join-Path $sys 'cmd.exe') -Argument $c
    }
    $pr = New-ScheduledTaskPrincipal -UserId $u -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName ('PK_GP_' + $id + '_' + $k) -TaskPath $pkTask -Action $a -Principal $pr -Settings $set -Force -ErrorAction Stop | Out-Null
    Start-ScheduledTask -TaskName ('PK_GP_' + $id + '_' + $k) -TaskPath $pkTask -ErrorAction Stop
    $started[$k] = $true
  } catch {
    $started[$k] = $false
    '=== Benutzerrichtlinie: ' + $u + ' ==='
    'FEHLER: Aktualisierung in der Sitzung des Benutzers nicht startbar – ' + $_.Exception.Message
  }
}
$deadline = (Get-Date).AddSeconds(__WAIT__)
do {
  Start-Sleep -Seconds 2
  $open = 0
  foreach ($i in 1..$k) {
    if ($started[$i] -and -not (Test-Path (Join-Path $dir ('r' + $i + '.txt')))) { $open++ }
  }
} while ($open -gt 0 -and (Get-Date) -lt $deadline)
Start-Sleep -Milliseconds 300
$i = 0
foreach ($u in $users) {
  $i++
  if (-not $started[$i]) { continue }
  '=== Benutzerrichtlinie: ' + $u + ' ==='
  $rf = Join-Path $dir ('r' + $i + '.txt')
  $of = Join-Path $dir ('o' + $i + '.txt')
  $out = ''
  if (Test-Path $of) { $out = (Get-Content -LiteralPath $of -Raw -Encoding Oem -ErrorAction SilentlyContinue) }
  if ($out) { $out.Trim() }
  if (Test-Path $rf) {
    $rc = ((Get-Content -LiteralPath $rf -Raw -ErrorAction SilentlyContinue) -replace '\s', '')
    if ($rc -ne '0') { 'FEHLER: gpupdate beim Benutzer endete mit Code ' + $rc }
    Unregister-ScheduledTask -TaskName ('PK_GP_' + $id + '_' + $i) -TaskPath $pkTask -Confirm:$false -ErrorAction SilentlyContinue
  } else {
    $ti = Get-ScheduledTaskInfo -TaskName ('PK_GP_' + $id + '_' + $i) -TaskPath $pkTask -ErrorAction SilentlyContinue
    if ($ti -and $ti.LastTaskResult -ne 267009 -and $ti.LastTaskResult -ne 267011 -and $ti.LastTaskResult -ne 0) {
      'FEHLER: Aufgabe in der Sitzung des Benutzers fehlgeschlagen (Code ' + $ti.LastTaskResult + ')'
    } else {
      'Noch nicht fertig nach __WAIT__ Sekunden – die Aktualisierung läuft beim Benutzer weiter.'
    }
  }
  ''
}
if (-not (Get-ScheduledTask -TaskPath $pkTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -like ('PK_GP_' + $id + '_*') })) {
  PK-GpRemove $dir
}
"""


def script(run_id=None):
    rid = run_id or secrets.token_hex(4)
    if not rid.isalnum():
        raise ValueError("Ungültige Lauf-ID")
    return SCRIPT.replace("__ID__", rid).replace("__WAIT__", str(WAIT_SECONDS))

