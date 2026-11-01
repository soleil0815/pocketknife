"""Windows-Benachrichtigung („Toast“) für die angemeldeten Benutzer nach einer erfolgreichen Installation oder
Aktualisierung – wie „Microsoft OneDrive wurde aktualisiert. Installierte Version: …“.

Ablauf (eine kurze WinRM-Verbindung mit dem Dienstkonto, im Hintergrund – die Bereitstellung wartet nicht darauf):
  1. angemeldete Benutzer ermitteln (Besitzer von explorer.exe); niemand angemeldet → nichts zu tun
  2. Anzeigeskript und Symbol nach C:\\Program Files\\PocketKnife (nur Administratoren dürfen schreiben – kein Benutzer
     kann sie austauschen); Titel/Text gehen nur als Base64-Daten in den Aufruf
  3. je Benutzer eine geplante Aufgabe „als angemeldeter Benutzer“ (Interactive, ohne Kennwort, ohne erhöhte Rechte)
     starten – nur so erscheint die Meldung in seiner Sitzung; ohne sichtbares Konsolenfenster (conhost --headless)
  4. das Skript prüft danach im Benachrichtigungscenter, ob Windows die Meldung angenommen hat; wenn nicht, zeigt es
     sie mit dem Absender „Windows PowerShell“ (immer registriert). Sein Exitcode = Ergebnis je Benutzer → Protokoll
  5. Aufgaben wieder entfernen

Absender: eigener Name (Einstellung „toast_sender“, Standard „Softwareverteilung“), im Benutzerprofil als
AppUserModelId registriert (HKCU\\Software\\Classes\\AppUserModelId\\POCKETKNIFE.Softwareverteilung). Erscheint auf einem
Client keine Meldung, hilft „Absender Windows PowerShell“ (toast_compat) – dieser Absender ist immer registriert.
"""
import base64
import hashlib
import json
import os
import re
import threading

from . import config, db, pklog, remote

APP_ID = "POCKETKNIFE.Softwareverteilung"  # Basis; je Absendername eine eigene ID (app_id), s. u.
PS_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
ICON_CLIENT = r"%ProgramFiles%\PocketKnife\pk-ok.png"  # der Client setzt den echten Pfad ($env:ProgramFiles) selbst ein
ICON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "toast-ok.png")
WINGET_OK_SKIP = (-1978335189, -1978335135)  # bereits aktuell / bereits installiert → keine Meldung
TEXTS = {
    "de": {"installed": "{0} wurde installiert.", "updated": "{0} wurde aktualisiert.", "version": "Installierte Version: {0}",
           "nover": "Bereitgestellt von der IT",
           "many_installed": "{0} Programme wurden installiert.", "many_updated": "{0} Programme wurden aktualisiert."},
    "en": {"installed": "{0} has been installed.", "updated": "{0} has been updated.", "version": "Installed version: {0}",
           "nover": "Provided by IT",
           "many_installed": "{0} programs have been installed.", "many_updated": "{0} programs have been updated."},
}

# Läuft als angemeldeter Benutzer (eigene Sitzung). Werte kommen als Base64-JSON – nichts wird als Code eingesetzt.
USER_PS = r"""param([string]$Data, [string]$Rid)
$ErrorActionPreference = 'Stop'
# Exitcode für POCKET KNIFE: 0 = gezeigt (eigener Absender), 10 = gezeigt mit Absender „Windows PowerShell“
# (eigener Absender von Windows nicht angenommen), 11 = gezeigt, aber nicht nachprüfbar, 12 = vom Benutzer für diesen
# Absender abgeschaltet (wird respektiert – kein Ausweichen auf einen anderen Absender), 2 = Windows hat die Meldung
# nicht angenommen (Benachrichtigungen aus, „Nicht stören“, GPO), 1 = Fehler
# Ergebnis NICHT nur als Exitcode: über conhost --headless kommt er nicht zuverlässig bei der Aufgabe an (2.24.4: „gezeigt“,
# obwohl das Skript gar nicht lief). Deshalb zusätzlich in HKCU\Software\PocketKnife\Toast (liest das Dienstkonto aus)
function Done([int]$c) {
  try {
    if ($Rid -match '^[A-Za-z0-9_]{1,40}$') {
      # .NET statt New-Item -Force: legt den Schlüssel nur an, wenn er fehlt – nie leeren (gleichzeitige Meldungen)
      $rk = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('Software\PocketKnife\Toast')
      $rk.SetValue($Rid, [int]$c, [Microsoft.Win32.RegistryValueKind]::DWord); $rk.Close()
    }
  } catch {}
  exit $c
}
trap { Done 1 }
$d = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Data)) | ConvertFrom-Json
[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
[void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]
$e = { param($s) [Security.SecurityElement]::Escape([string]$s) }
# Symbol immer aus „Programme“ des Clients (nur Administratoren dürfen dort schreiben)
$d.icon = Join-Path $env:ProgramFiles 'PocketKnife\pk-ok.png'
$img = ''
if (Test-Path $d.icon) { $img = '<image placement="appLogoOverride" hint-crop="circle" src="' + (& $e ('file:///' + (($d.icon -replace '\\', '/') -replace ' ', '%20'))) + '"/>' }
$body = '<toast><visual><binding template="ToastGeneric"><text>' + (& $e $d.title) + '</text><text>' + (& $e $d.body) + '</text>' + $img + '</binding></visual></toast>'
$ns = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Notifications\Settings\'
function Allow($id) {
  # Benachrichtigungen für diesen Absender einschalten – aber nie, wenn der Benutzer sie bewusst abgeschaltet hat
  $k = $ns + $id
  if (-not (Test-Path $k)) {
    New-Item -Path $k -Force | Out-Null
    New-ItemProperty -Path $k -Name Enabled -Value 1 -PropertyType DWord -Force | Out-Null
    New-ItemProperty -Path $k -Name ShowInActionCenter -Value 1 -PropertyType DWord -Force | Out-Null
  }
}
function Send($id, $tag) {
  $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
  $xml.LoadXml($body)
  $t = [Windows.UI.Notifications.ToastNotification]::new($xml)
  $t.Tag = $tag; $t.Group = 'pocketknife'
  [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($id).Show($t)
  Start-Sleep -Milliseconds 1500
  # Liegt die Meldung jetzt im Benachrichtigungscenter? $null = nicht nachprüfbar
  try { return [bool](@([Windows.UI.Notifications.ToastNotificationManager]::History.GetHistory($id)) | Where-Object { $_.Tag -eq $tag }) }
  catch { return $null }
}
$tag = 'pk' + [guid]::NewGuid().ToString('N').Substring(0, 10)
if (-not $d.compat) {
  # Windows merkt sich den Anzeigenamen eines Absenders; ein geänderter Name bekommt deshalb eine eigene ID. Alte IDs von
  # POCKET KNIFE aufräumen – hatte der Benutzer eine davon abgeschaltet, bleibt auch die neue aus.
  $cls = 'HKCU:\Software\Classes\AppUserModelId\'
  $off = $false
  foreach ($o in @(Get-ChildItem -Path $ns -ErrorAction SilentlyContinue | Where-Object { $_.PSChildName -like ($d.base + '*') -and $_.PSChildName -ne $d.appid })) {
    try { if ((Get-ItemProperty -Path $o.PSPath -ErrorAction Stop).Enabled -eq 0) { $off = $true } } catch {}
    # Einstellung des alten Absenders nach dem Übernehmen entfernen – sonst wirkt ein altes „aus“ bei jedem Namenswechsel
    Remove-Item -Path $o.PSPath -Recurse -Force -ErrorAction SilentlyContinue
  }
  foreach ($o in @(Get-ChildItem -Path $cls -ErrorAction SilentlyContinue | Where-Object { $_.PSChildName -like ($d.base + '*') -and $_.PSChildName -ne $d.appid })) {
    Remove-Item -Path $o.PSPath -Recurse -Force -ErrorAction SilentlyContinue
  }
  if ($off -and -not (Test-Path ($ns + $d.appid))) {
    New-Item -Path ($ns + $d.appid) -Force | Out-Null
    New-ItemProperty -Path ($ns + $d.appid) -Name Enabled -Value 0 -PropertyType DWord -Force | Out-Null
  }
  $k = $cls + $d.appid
  if (-not (Test-Path $k)) { New-Item -Path $k -Force | Out-Null }   # vorhandenen Schlüssel nie neu anlegen (leeren)
  Set-ItemProperty -Path $k -Name DisplayName -Value $d.sender
  if (Test-Path $d.icon) { Set-ItemProperty -Path $k -Name IconUri -Value $d.icon }
  Set-ItemProperty -Path $k -Name ShowInSettings -Value 0 -Type DWord
  Allow $d.appid
  $r = $null; $threw = $false
  try { $r = Send $d.appid $tag } catch { $r = $false; $threw = $true }
  if ($r -eq $true) { Done 0 }
  if ($null -eq $r) { Done 11 }   # gezeigt, aber Verlauf nicht lesbar – kein zweites Mal senden
  # Hat der Benutzer POCKET KNIFE in „Benachrichtigungen“ bewusst abgeschaltet? Dann NICHT über einen anderen
  # Absender ausweichen. „Im Benachrichtigungscenter anzeigen“ aus → die Meldung kam, steht nur nicht im Verlauf.
  $st = Get-ItemProperty -Path ($ns + $d.appid) -ErrorAction SilentlyContinue
  if ($st -and $st.PSObject.Properties['Enabled'] -and $st.Enabled -eq 0) { Done 12 }
  if (-not $threw -and $st -and $st.PSObject.Properties['ShowInActionCenter'] -and $st.ShowInActionCenter -eq 0) { Done 11 }
}
# Rückfall: Absender „Windows PowerShell“ – in Windows immer registriert
Allow $d.psid
$r = Send $d.psid ($tag + 'p')
if ($r -eq $true) { if ($d.compat) { Done 0 } else { Done 10 } }
if ($null -eq $r) { Done 11 }
Done 2
"""

# Läuft als Dienstkonto über WinRM
SEND_PS = r"""$ErrorActionPreference = 'Stop'
$users = @(Get-CimInstance Win32_Process -Filter "Name='explorer.exe'" | ForEach-Object {
  $o = Invoke-CimMethod -InputObject $_ -MethodName GetOwner -ErrorAction SilentlyContinue
  if ($o.User) { $o.Domain + '\' + $o.User } } | Sort-Object -Unique)
if (-not $users) { 'PK_TOAST:nouser'; return }
# Symbol unter „Programme“ (nur Administratoren dürfen schreiben – kein Benutzer kann es vorher austauschen)
$dir = Join-Path $env:ProgramFiles 'PocketKnife'
New-Item -ItemType Directory -Path $dir -Force | Out-Null
# Rechte erzwingen: Besitzer Administratoren, Rechte wieder wie unter „Programme“ geerbt (Administratoren/SYSTEM
# Vollzugriff, Benutzer nur Lesen/Ausführen) – falls jemand sie geändert hat
$ica = Join-Path $env:SystemRoot 'System32\icacls.exe'
& $ica $dir /setowner '*S-1-5-32-544' /T /C /Q | Out-Null
& $ica $dir /reset /T /C /Q | Out-Null
$acl = Get-Acl $dir
# Schreibrecht für normale Benutzer (Jeder, Benutzer, Authentifizierte/Interaktive Benutzer, Domänen-Benutzer) → abbrechen
$weak = @($acl.Access | Where-Object { $_.AccessControlType -eq 'Allow' -and
  ($_.FileSystemRights.ToString() -match 'FullControl|Modify|Write|ChangePermissions|TakeOwnership') -and
  $(try { $sid = $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value } catch { $sid = '' }
    @('S-1-1-0', 'S-1-5-32-545', 'S-1-5-11', 'S-1-5-4') -contains $sid -or $sid -match '^S-1-5-21-.+-513$') })
if ($weak) { throw ('Unsichere Rechte auf ' + $dir + ': ' + (($weak | ForEach-Object { $_.IdentityReference }) -join ', ')) }
$ico = Join-Path $dir 'pk-ok.png'
$icb = [Convert]::FromBase64String('__ICON__')
$hv = $null
if (Test-Path $ico) { try { $hv = [Convert]::ToBase64String([IO.File]::ReadAllBytes($ico)) } catch {} }
if ($hv -cne '__ICON__') { try { [IO.File]::WriteAllBytes($ico, $icb) } catch { if (-not (Test-Path $ico)) { throw } } }
# Das Anzeigeskript liegt ebenfalls unter „Programme“ (Benutzer können es lesen, aber nicht ändern); die Meldung
# selbst kommt nur als Daten (Base64-JSON) dazu
$ps = Join-Path $dir 'pk-toast.ps1'
$code = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__SCRIPT__'))
$have = $null
if (Test-Path $ps) { try { $have = [IO.File]::ReadAllText($ps) } catch {} }
if ($have -cne $code) {   # -cne: auch reine Groß-/Kleinschreibungs-Änderungen übernehmen
  try { [IO.File]::WriteAllText($ps, $code, (New-Object Text.UTF8Encoding $true)) }
  catch { if (-not (Test-Path $ps)) { throw } }   # gerade von einer zweiten Meldung in Gebrauch – vorhandenes nutzen
}
# Zeitstempel in der ID: liegengebliebene Rückmeldungen (Abbruch, Zeitüberschreitung) lassen sich später sicher aufräumen
$id = (Get-Date).ToString('yyMMddHHmm') + [guid]::NewGuid().ToString('N').Substring(0, 8)
$build = [int](Get-CimInstance Win32_OperatingSystem).BuildNumber
$pse = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
# Startweg je Benutzer: normal -File (das Skript liegt unter „Programme“ – dort erlaubt AppLocker/WDAC es auch im
# Constrained Language Mode; ein kodierter Befehl liefe dort eingeschränkt und scheitert, 2.24.4). Nur wenn eine
# Gruppenrichtlinie die Ausführungsrichtlinie auf AllSigned/Restricted festlegt (dann hilft „-ExecutionPolicy Bypass“
# bei -File nicht), ein kurzer kodierter Starter, der das Skript aus „Programme“ liest.
function GpoPolicy($sid) {
  $m = Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\PowerShell' -ErrorAction SilentlyContinue
  $u = $null
  if ($sid) { $u = Get-ItemProperty ('Registry::HKEY_USERS\' + $sid + '\Software\Policies\Microsoft\Windows\PowerShell') -ErrorAction SilentlyContinue }
  foreach ($p in @($m, $u)) {
    if ($p -and $p.PSObject.Properties['EnableScripts']) {
      if ($p.EnableScripts -eq 0) { return 'Restricted' }
      if ($p.PSObject.Properties['ExecutionPolicy']) { return [string]$p.ExecutionPolicy }
    }
  }
  return ''
}
function Action($sid, $rid) {
  $pol = GpoPolicy $sid
  if ($pol -eq 'AllSigned' -or $pol -eq 'Restricted') {
    $start = "& ([ScriptBlock]::Create([IO.File]::ReadAllText('" + ($ps -replace "'", "''") + "'))) '__DATA__' '" + $rid + "'; exit 1"
    $a0 = '-NoLogo -NonInteractive -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -EncodedCommand ' +
      [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($start))
  } else {
    $a0 = '-NoLogo -NonInteractive -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $ps + '" __DATA__ ' + $rid
  }
  if ($build -ge 18362) { return New-ScheduledTaskAction -Execute (Join-Path $env:SystemRoot 'System32\conhost.exe') -Argument ('--headless "' + $pse + '" ' + $a0) }
  return New-ScheduledTaskAction -Execute $pse -Argument $a0
}
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
$n = 0; $ok = 0; $sids = @{}
try {
  foreach ($u in $users) {
    $n++
    try { $sids[$n] = (New-Object Security.Principal.NTAccount($u)).Translate([Security.Principal.SecurityIdentifier]).Value } catch { $sids[$n] = '' }
    try {
      $act = Action $sids[$n] ('PK' + $id + '_' + $n)
      $pr = New-ScheduledTaskPrincipal -UserId $u -LogonType Interactive -RunLevel Limited
      Register-ScheduledTask -TaskName ('PK_Toast_' + $id + '_' + $n) -TaskPath '\POCKETKNIFE\' -Action $act -Principal $pr -Settings $set -Force | Out-Null
      Start-ScheduledTask -TaskName ('PK_Toast_' + $id + '_' + $n) -TaskPath '\POCKETKNIFE\'
      $ok++
    } catch { 'PK_TOAST:fail:' + $u + ':' + $_.Exception.Message }
  }
  # Auf das Ergebnis beim Benutzer warten, höchstens 25 Sekunden. Maßgeblich ist der Wert, den das Anzeigeskript in
  # HKCU\Software\PocketKnife\Toast hinterlegt – der Exitcode der Aufgabe kommt über conhost nicht zuverlässig an.
  function Res($k) {
    if (-not $sids[$k]) { return $null }
    $rk = 'Registry::HKEY_USERS\' + $sids[$k] + '\Software\PocketKnife\Toast'
    $nm = 'PK' + $id + '_' + $k
    $v = Get-ItemProperty -Path $rk -Name $nm -ErrorAction SilentlyContinue
    if ($v -and $v.PSObject.Properties[$nm]) { return [int]$v.$nm }
    return $null
  }
  $deadline = (Get-Date).AddSeconds(25)
  do {
    Start-Sleep -Seconds 2
    $open = 0
    foreach ($k in 1..$n) {
      if ($null -ne (Res $k)) { continue }
      $ti = Get-ScheduledTaskInfo -TaskName ('PK_Toast_' + $id + '_' + $k) -TaskPath '\POCKETKNIFE\' -ErrorAction SilentlyContinue
      if ($ti -and ($ti.LastTaskResult -eq 267009 -or $ti.LastTaskResult -eq 267011)) { $open++ }
    }
  } while ($open -gt 0 -and (Get-Date) -lt $deadline)
  foreach ($k in 1..$n) {
    $r = Res $k
    $ti = Get-ScheduledTaskInfo -TaskName ('PK_Toast_' + $id + '_' + $k) -TaskPath '\POCKETKNIFE\' -ErrorAction SilentlyContinue
    if ($null -ne $r) {
      'PK_TOAST:res:' + $users[$k - 1] + ':' + $r
      Remove-ItemProperty -Path ('Registry::HKEY_USERS\' + $sids[$k] + '\Software\PocketKnife\Toast') -Name ('PK' + $id + '_' + $k) -ErrorAction SilentlyContinue
    } elseif ($ti -and ($ti.LastTaskResult -eq 267009 -or $ti.LastTaskResult -eq 267011)) {
      'PK_TOAST:res:' + $users[$k - 1] + ':' + $ti.LastTaskResult
    } elseif (-not $sids[$k]) {
      'PK_TOAST:res:' + $users[$k - 1] + ':14'   # Konto nicht auflösbar – Rückmeldung nicht lesbar
    } elseif ($ti -and $ti.LastTaskResult -ne 0 -and $ti.LastTaskResult -ne 1) {
      'PK_TOAST:res:' + $users[$k - 1] + ':' + $ti.LastTaskResult   # z. B. Anmeldefehler der Aufgabe
    } elseif ($ti) {
      'PK_TOAST:res:' + $users[$k - 1] + ':13'   # Aufgabe beendet, aber das Anzeigeskript hat nie geantwortet
    }
  }
  # Liegengebliebene Rückmeldungen älter als 30 Minuten entfernen (frühere Abbrüche) – laufende anderer Meldungen bleiben
  $old = (Get-Date).AddMinutes(-30).ToString('yyMMddHHmm')
  foreach ($k in 1..$n) {
    if (-not $sids[$k]) { continue }
    $rp = 'Registry::HKEY_USERS\' + $sids[$k] + '\Software\PocketKnife\Toast'
    $it = Get-Item -Path $rp -ErrorAction SilentlyContinue
    if ($it) {
      foreach ($vn in $it.GetValueNames()) {
        if ($vn -match '^PK(\d{10})' -and $Matches[1] -lt $old) { Remove-ItemProperty -Path $rp -Name $vn -ErrorAction SilentlyContinue }
      }
    }
  }
} finally {
  # Gestartete Aufgaben laufen weiter, auch wenn die Aufgabe gelöscht ist
  Get-ScheduledTask -TaskPath '\POCKETKNIFE\' -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -like ('PK_Toast_' + $id + '_*') } |
    Unregister-ScheduledTask -Confirm:$false -ErrorAction SilentlyContinue
}
'PK_TOAST:sent:' + $ok + ':' + ($users -join ',')
"""


def _icon_b64():
    try:
        with open(ICON, "rb") as fh:
            return base64.b64encode(fh.read()).decode("ascii")
    except OSError:
        return ""


FOUND_RX = re.compile(r"(?:Found|Gefunden)\s+(.+?) \[([^\]]+)\] Version (\S+)")


def _tree(p):
    """Schritt-ID → (Schritt, Paket, dem er gehört) – auch in verschachtelten Paketen."""
    from . import steps as stp
    out = {}

    def walk(pkg, depth=0):
        try:
            for _k, st in stp.iter_steps(stp.normalize(json.loads(json.dumps(pkg)))):
                if st.get("id"):
                    out.setdefault(st["id"], (st, pkg))
                if st.get("type") == "nested" and isinstance(st.get("package"), dict) and depth < 10:
                    walk(st["package"], depth + 1)  # verschachtelte Pakete („Paket: TightVNC …“, Zeitpläne mit mehreren Paketen)
        except Exception:
            pass
    walk(p)
    return out


def _real_install(st, mode):
    """Installiert/aktualisiert dieser Schritt ein Programm? (Deinstallation und reine Skripte nicht – außer „immer“)"""
    if mode == "on":
        return True
    if st.get("type") != "install":
        return False
    if st.get("source") == "winget":
        return st.get("winget_action") in ("install", "upgrade")
    return st.get("install_type") != "script"


def classify(p, runner_results, log_list, computer_id=None, global_on=True):
    """[(art, name, version), …] der Programme, die dieser Lauf wirklich installiert/aktualisiert hat – leer, wenn
    keine Meldung angebracht ist. art: „installed“/„updated“. Maßgeblich ist der Schritt selbst und das Paket, zu
    dem er gehört (auch verschachtelt: „nie“/„immer“ des inneren Pakets gelten)."""
    if isinstance(log_list, str):
        log_list = [log_list]
    tree = _tree(p)
    items, seen = [], set()
    for r in runner_results or []:
        if r.get("status") != "success" or r.get("type") == "nested":
            continue
        st, owner = tree.get(r.get("sid"), (None, p))
        if st is None:
            continue
        mode = owner.get("notify_user") or p.get("notify_user") or ""
        if mode == "off" or (not global_on and mode != "on"):
            continue
        name = (owner.get("name") or p.get("name") or "").strip()
        if not name or re.match(r"^(Deinstallation|Uninstall)\b", name, re.I) or not _real_install(st, mode):
            continue
        if "Bereits installiert" in (r.get("message") or "") or r.get("exit") in WINGET_OK_SKIP:
            continue  # nichts geändert
        # Ausgabe genau dieses Schritts (Protokollblock „=== n. …“)
        out = next((x for x in log_list or [] if x.startswith(f"=== {r.get('n')}. ")), "")
        kind = "updated" if (st.get("source") == "winget" and st.get("winget_action") == "upgrade") \
            or name.startswith("Aktualisieren:") else "installed"
        disp, ver = name, (owner.get("version") or "").strip()
        f = FOUND_RX.search(out)
        if f:
            disp, wid, ver = f.group(1).strip(), f.group(2), f.group(3)
            if kind == "installed" and computer_id and db.val(
                    "SELECT 1 FROM software WHERE computer_id=? AND lower(winget_id)=lower(?)", (computer_id, wid)):
                kind = "updated"  # war schon in einer älteren Version da
        disp = re.sub(r"^(Kiosk|Zeitplan|Regel|Aktualisieren|auto)\s*:?\s+", "", disp, flags=re.I).strip() or name
        if disp.lower() in seen:
            continue
        seen.add(disp.lower())
        items.append((kind, disp[:120], ver[:60]))
    return items


def app_id(sender):
    """Absender-ID je Anzeigename – Windows übernimmt einen geänderten Namen für eine bekannte ID nicht (2.24.6)."""
    return APP_ID + "." + hashlib.sha256(sender.encode("utf-8")).hexdigest()[:10]


def build_send_script(items):
    """items: [(art, name, version), …] → (PowerShell für WinRM, Daten der Meldung)."""
    lang = "en" if str(db.setting("toast_language") or "de") == "en" else "de"
    t = TEXTS[lang]
    if len(items) == 1:
        kind, disp, ver = items[0]
        title, body = t[kind].format(disp), (t["version"].format(ver) if ver else t["nover"])
    else:
        kind = "updated" if all(k == "updated" for k, _n, _v in items) else "installed"
        title = t["many_" + kind].format(len(items))
        body = ", ".join(n + (f" {v}" if v else "") for _k, n, v in items)[:200]
    sender = (str(db.setting("toast_sender") or "").strip() or "Softwareverteilung")[:60]
    data = {"appid": PS_APP_ID if db.setting("toast_compat") else app_id(sender), "psid": PS_APP_ID, "base": APP_ID,
            "compat": bool(db.setting("toast_compat")),
            "sender": sender,
            "icon": ICON_CLIENT, "title": title, "body": body}
    # Skript unter „Programme“ (nur Administratoren dürfen schreiben), Meldung nur als Base64-Daten im Aufruf
    b64 = base64.b64encode(json.dumps(data, ensure_ascii=False).encode("utf-8")).decode("ascii")
    return (SEND_PS.replace("__ICON__", _icon_b64())
            .replace("__SCRIPT__", base64.b64encode(USER_PS.encode("utf-8")).decode("ascii"))
            .replace("__DATA__", b64)), data


def maybe_notify(c, host, p, runner):
    """Nach einem erfolgreichen Ziel aufrufen. Kehrt sofort zurück; die Meldung geht im Hintergrund raus."""
    try:
        items = classify(p, runner.results, runner.log, c.get("id"), global_on=bool(db.setting("toast_enabled")))
        if not items:
            return None
        script, data = build_send_script(items)
    except Exception as e:  # noqa: BLE001 – eine Meldung darf nie die Bereitstellung stören
        pklog.warn("deploy", f"Benachrichtigung für {c.get('name')} nicht vorbereitet: {e}", computer_id=c.get("id"))
        return None

    def send():
        try:
            out, _err, _ = remote.ps(host, script)
            for u, ok, text in results(out):
                (pklog.info if ok else pklog.warn)("deploy", f"{c['name']}: Benachrichtigung „{data['title']}“ für {u}: {text}",
                                                   computer_id=c["id"])
            for f in re.findall(r"PK_TOAST:fail:(.*)", out or ""):
                pklog.warn("deploy", f"{c['name']}: Benachrichtigung nicht gezeigt ({f.strip()[:200]}).", computer_id=c["id"])
        except Exception as e:  # noqa: BLE001
            pklog.warn("deploy", f"{c['name']}: Benachrichtigung nicht gezeigt: {e}", computer_id=c["id"])
    try:
        threading.Thread(target=send, daemon=True, name=f"toast-{c.get('id')}").start()
    except Exception as e:  # noqa: BLE001 – z. B. zu viele Threads: dann eben keine Meldung
        pklog.warn("deploy", f"{c.get('name')}: Benachrichtigung nicht gestartet: {e}", computer_id=c.get("id"))
        return None
    return data


RESULT_TEXT = {
    0: (True, "gezeigt"),
    10: (True, "gezeigt – mit Absender „Windows PowerShell“, weil Windows den eigenen Absender auf diesem Computer nicht "
               "angenommen hat (mit „Kompatibilität“ wird das gleich so gemacht)"),
    11: (True, "an Windows übergeben (ob sie erschienen ist, lässt sich auf diesem Computer nicht nachprüfen)"),
    2: (False, "von Windows nicht angezeigt – Benachrichtigungen für den Benutzer aus, „Nicht stören“ an oder per "
               "Richtlinie abgeschaltet"),
    14: (False, "Rückmeldung nicht lesbar – das Benutzerkonto ließ sich nicht auflösen (z. B. Entra-ID-/lokales Konto)"),
    13: (False, "Anzeigeskript ist beim Benutzer nicht gestartet – PowerShell ist dort per Richtlinie eingeschränkt "
                "(Softwareeinschränkung/SRP, AppLocker/WDAC oder Ausführungsrichtlinie). Skripte aus „C:\\Program Files\\PocketKnife“ zulassen."),
    12: (False, "nicht angezeigt – der Benutzer hat Benachrichtigungen dieses Absenders in den Windows-Einstellungen "
                "abgeschaltet (wird respektiert)"),
    1: (False, "Fehler beim Anzeigen in der Benutzersitzung"),
    267009: (False, "keine Rückmeldung (läuft noch – Anmeldung gesperrt oder Rechner sehr langsam?)"),
    267011: (False, "Aufgabe wurde nicht gestartet (Benutzer nicht mehr angemeldet?)"),
}


def results(out):
    """[(benutzer, ok, text)] aus der Rückmeldung je angemeldetem Benutzer."""
    res = []
    for u, code in re.findall(r"PK_TOAST:res:(.+):(-?\d+)\s*$", out or "", re.M):
        code = int(code)
        ok, text = RESULT_TEXT.get(code, (False, f"Aufgabe endete mit Code {code} (0x{code & 0xFFFFFFFF:08X})"))
        res.append((u.strip(), ok, text))
    return res


def test(c, user):
    """„Test-Benachrichtigung senden“ aus den Einstellungen."""
    script, data = build_send_script([("installed", "POCKET KNIFE", config.VERSION)])
    from . import presence
    out, _err, _ = remote.ps(presence.direct_host(c), script)
    if "PK_TOAST:nouser" in (out or ""):
        return {"ok": False, "message": "Auf diesem Computer ist niemand angemeldet."}
    res = results(out)
    fails = [x.strip() for x in re.findall(r"PK_TOAST:fail:(.*)", out or "")]
    if res or fails:
        msg = "; ".join([f"{u}: {t}" for u, _ok, t in res] + [f"nicht gestartet – {f}" for f in fails])
        ok = any(o for _u, o, _t in res)
        (pklog.info if ok else pklog.warn)("system", f"Test-Benachrichtigung an {c['name']}: {msg}", user=user)
        return {"ok": ok, "message": msg, "title": data["title"]}
    return {"ok": False, "message": "Keine Rückmeldung: " + (out or "")[-300:]}
