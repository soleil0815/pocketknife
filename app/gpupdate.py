"""2.38.4: „gpupdate /force“ auf einem Client – Computer- UND Benutzerrichtlinien der angemeldeten Benutzer.

Über WinRM läuft gpupdate im Konto des Dienstkontos (Netzwerkanmeldung). Die Computerrichtlinie wird dabei richtig
aktualisiert, die Benutzerrichtlinie aber nur für das Dienstkonto selbst – nicht für die Benutzer, die am Computer
angemeldet sind. Benutzereinstellungen (z. B. Browser-Lesezeichen, Laufwerke, Desktop) kamen so erst bei der nächsten
Anmeldung oder Hintergrundaktualisierung an, obwohl gpupdate „erfolgreich“ meldete.

Deshalb: Computerrichtlinie wie bisher direkt; für jeden angemeldeten Benutzer (Besitzer von explorer.exe) eine kurze
geplante Aufgabe in seiner Sitzung (Interactive, eingeschränkte Rechte, ohne Kennwort) mit
„gpupdate /target:user /force“ – so macht es auch Invoke-GPUpdate. Ausgabe und Exitcode kommen über einen Ordner
unter %SystemRoot%\\Temp zurück, der nur dem Benutzer zusätzlich freigegeben wird. Ab Windows 10 1903 unsichtbar
(conhost --headless), sonst blitzt kurz ein Konsolenfenster auf.
Constrained-Language-tauglich: nur Cmdlets, CIM und Programme aus System32.
"""
import secrets

WAIT_SECONDS = 120  # höchstens so lange auf die Benutzerrichtlinien warten

SCRIPT = r"""$ErrorActionPreference = 'Continue'
$id = '__ID__'
$pkTask = '\POCKETKNIFE\'
$sys = Join-Path $env:SystemRoot 'System32'
# Reste früherer Läufe (abgebrochen, Zeitüberschreitung) entfernen – laufende Aufgaben bleiben
Get-ScheduledTask -TaskPath $pkTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -like 'PK_GP_*' -and $_.State -ne 'Running' } |
  ForEach-Object { Unregister-ScheduledTask -TaskName $_.TaskName -TaskPath $pkTask -Confirm:$false -ErrorAction SilentlyContinue }
$old = (Get-Date).AddHours(-1)
Get-ChildItem -Path (Join-Path $env:SystemRoot 'Temp') -Filter 'pk_gp_*' -Directory -ErrorAction SilentlyContinue |
  Where-Object { $_.LastWriteTime -lt $old } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

'=== Computerrichtlinie ==='
(& (Join-Path $sys 'cmd.exe') /d /c 'echo N| gpupdate.exe /target:computer /force' | Out-String).Trim()

# Angemeldete Benutzer: Besitzer von explorer.exe oder sihost.exe (auch Kiosk mit eigener Shell), je Konto einmal
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
    # Ausgabe und Exitcode von gpupdate in den Ordner; "N" beantwortet Rückfragen nach Abmelden/Neustart
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
  Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue
}
"""


def script(run_id=None):
    rid = run_id or secrets.token_hex(4)
    if not rid.isalnum():
        raise ValueError("Ungültige Lauf-ID")
    return SCRIPT.replace("__ID__", rid).replace("__WAIT__", str(WAIT_SECONDS))

