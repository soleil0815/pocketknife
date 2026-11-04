"""Windows notification ("toast") for the logged-on users after a successful installation or
update - like "Microsoft OneDrive wurde aktualisiert. Installierte Version: ...".

Flow (one short WinRM connection with the service account, in the background - the deployment does not wait for it):
  1. determine the logged-on users (owners of explorer.exe); nobody logged on → nothing to do
  2. display script and icon to C:\\Program Files\\PocketKnife (only administrators may write - no user
     can swap them); title/text go into the call only as Base64 data
  3. start a scheduled task per user "as the logged-on user" (Interactive, no password, no elevated rights)
     - only then does the message appear in their session; without a visible console window (conhost --headless)
  4. afterwards the script checks in the notification center whether Windows accepted the message; if not, it shows
     it with the sender "Windows PowerShell" (always registered). Its exit code = result per user → log
  5. remove the tasks again

Sender: own name (setting "toast_sender", default "Softwareverteilung"), registered in the user profile as an
AppUserModelId (HKCU\\Software\\Classes\\AppUserModelId\\POCKETKNIFE.Softwareverteilung). If no message appears on a
client, "Absender Windows PowerShell" (toast_compat) helps - that sender is always registered.
"""
import base64
import hashlib
import json
import os
import re
import threading

from . import config, db, pklog, remote, util

APP_ID = "POCKETKNIFE.Softwareverteilung"  # base; one own ID per sender name (app_id), see below
PS_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
ICON_CLIENT = r"%ProgramFiles%\PocketKnife\pk-ok.png"  # the client inserts the real path ($env:ProgramFiles) itself
ICON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "toast-ok.png")
WINGET_OK_SKIP = (-1978335189, -1978335135)  # already up to date / already installed → no message
TEXTS = {
    "de": {"installed": "{0} wurde installiert.", "updated": "{0} wurde aktualisiert.", "version": "Installierte Version: {0}",
           "nover": "Bereitgestellt von der IT",
           "many_installed": "{0} Programme wurden installiert.", "many_updated": "{0} Programme wurden aktualisiert."},
    "en": {"installed": "{0} has been installed.", "updated": "{0} has been updated.", "version": "Installed version: {0}",
           "nover": "Provided by IT",
           "many_installed": "{0} programs have been installed.", "many_updated": "{0} programs have been updated."},
}

# Runs as the logged-on user (own session). Values arrive as Base64 JSON - nothing is inserted as code.
USER_PS = r"""param([string]$Data, [string]$Rid)
$ErrorActionPreference = 'Stop'
# Exit code for POCKET KNIFE: 0 = shown (own sender), 10 = shown with sender "Windows PowerShell"
# (own sender not accepted by Windows), 11 = shown but not verifiable, 12 = disabled by the user for this
# sender (respected - no falling back to another sender), 2 = Windows did not accept the message
# (notifications off, "Do not disturb", GPO), 1 = error
# Result NOT only as exit code: via conhost --headless it does not reliably reach the task (2.24.4: "shown"
# although the script did not run at all). Therefore additionally in HKCU\Software\PocketKnife\Toast (read by the service account)
function Done([int]$c) {
  try {
    if ($Rid -match '^[A-Za-z0-9_]{1,40}$') {
      # .NET instead of New-Item -Force: creates the key only if it is missing - never empties it (simultaneous messages)
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
# Icon always from "Program Files" of the client (only administrators may write there)
$d.icon = Join-Path $env:ProgramFiles 'PocketKnife\pk-ok.png'
$img = ''
if (Test-Path $d.icon) { $img = '<image placement="appLogoOverride" hint-crop="circle" src="' + (& $e ('file:///' + (($d.icon -replace '\\', '/') -replace ' ', '%20'))) + '"/>' }
$body = '<toast><visual><binding template="ToastGeneric"><text>' + (& $e $d.title) + '</text><text>' + (& $e $d.body) + '</text>' + $img + '</binding></visual></toast>'
$ns = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Notifications\Settings\'
function Allow($id) {
  # Enable notifications for this sender - but never if the user deliberately disabled them
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
  # Is the message now in the notification center? $null = not verifiable
  try { return [bool](@([Windows.UI.Notifications.ToastNotificationManager]::History.GetHistory($id)) | Where-Object { $_.Tag -eq $tag }) }
  catch { return $null }
}
$tag = 'pk' + [guid]::NewGuid().ToString('N').Substring(0, 10)
if (-not $d.compat) {
  # Windows remembers the display name of a sender; a changed name therefore gets its own ID. Clean up old IDs of
  # POCKET KNIFE - if the user had disabled one of them, the new one stays off as well.
  $cls = 'HKCU:\Software\Classes\AppUserModelId\'
  $off = $false
  foreach ($o in @(Get-ChildItem -Path $ns -ErrorAction SilentlyContinue | Where-Object { $_.PSChildName -like ($d.base + '*') -and $_.PSChildName -ne $d.appid })) {
    try { if ((Get-ItemProperty -Path $o.PSPath -ErrorAction Stop).Enabled -eq 0) { $off = $true } } catch {}
    # Remove the old sender's setting after adopting it - otherwise an old "off" takes effect on every name change
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
  if (-not (Test-Path $k)) { New-Item -Path $k -Force | Out-Null }   # never re-create an existing key (empties it)
  Set-ItemProperty -Path $k -Name DisplayName -Value $d.sender
  if (Test-Path $d.icon) { Set-ItemProperty -Path $k -Name IconUri -Value $d.icon }
  Set-ItemProperty -Path $k -Name ShowInSettings -Value 0 -Type DWord
  Allow $d.appid
  $r = $null; $threw = $false
  try { $r = Send $d.appid $tag } catch { $r = $false; $threw = $true }
  if ($r -eq $true) { Done 0 }
  if ($null -eq $r) { Done 11 }   # shown, but history not readable - do not send a second time
  # Did the user deliberately disable POCKET KNIFE in "Benachrichtigungen"? Then do NOT fall back to another
  # sender. "Im Benachrichtigungscenter anzeigen" off → the message came, it just is not in the history.
  $st = Get-ItemProperty -Path ($ns + $d.appid) -ErrorAction SilentlyContinue
  if ($st -and $st.PSObject.Properties['Enabled'] -and $st.Enabled -eq 0) { Done 12 }
  if (-not $threw -and $st -and $st.PSObject.Properties['ShowInActionCenter'] -and $st.ShowInActionCenter -eq 0) { Done 11 }
}
# Fallback: sender "Windows PowerShell" - always registered in Windows
Allow $d.psid
$r = Send $d.psid ($tag + 'p')
if ($r -eq $true) { if ($d.compat) { Done 0 } else { Done 10 } }
if ($null -eq $r) { Done 11 }
Done 2
"""

# Runs as the service account via WinRM
SEND_PS = r"""$ErrorActionPreference = 'Stop'
$users = @(Get-CimInstance Win32_Process -Filter "Name='explorer.exe'" | ForEach-Object {
  $o = Invoke-CimMethod -InputObject $_ -MethodName GetOwner -ErrorAction SilentlyContinue
  if ($o.User) { $o.Domain + '\' + $o.User } } | Sort-Object -Unique)
if (-not $users) { 'PK_TOAST:nouser'; return }
# Icon under "Program Files" (only administrators may write - no user can swap it beforehand)
$dir = Join-Path $env:ProgramFiles 'PocketKnife'
New-Item -ItemType Directory -Path $dir -Force | Out-Null
# Enforce rights: owner Administrators, rights inherited again as under "Program Files" (Administrators/SYSTEM
# full control, users read/execute only) - in case someone changed them
$ica = Join-Path $env:SystemRoot 'System32\icacls.exe'
& $ica $dir /setowner '*S-1-5-32-544' /T /C /Q | Out-Null
& $ica $dir /reset /T /C /Q | Out-Null
$acl = Get-Acl $dir
# Write permission for normal users (Everyone, Users, Authenticated/Interactive Users, Domain Users) → abort
$weak = @($acl.Access | Where-Object { $_.AccessControlType -eq 'Allow' -and
  ("$($_.FileSystemRights)" -match 'FullControl|Modify|Write|ChangePermissions|TakeOwnership') -and
  $(try { $sid = $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value } catch { $sid = '' }
    @('S-1-1-0', 'S-1-5-32-545', 'S-1-5-11', 'S-1-5-4') -contains $sid -or $sid -match '^S-1-5-21-.+-513$') })
if ($weak) { throw ('Unsichere Rechte auf ' + $dir + ': ' + (($weak | ForEach-Object { $_.IdentityReference }) -join ', ')) }
$ico = Join-Path $dir 'pk-ok.png'
# 2.38: without .NET calls (Constrained Language Mode): compare via the checksum, write with Set-Content
$hv = $null
if (Test-Path $ico) { try { $hv = (Get-FileHash -LiteralPath $ico -Algorithm SHA256).Hash } catch {} }
if ($hv -ne '__ICONHASH__') { try { Set-Content -LiteralPath $ico -Encoding Byte -Value ([byte[]]@(__ICONBYTES__)) } catch { if (-not (Test-Path $ico)) { throw } } }
# The display script is also under "Program Files" (users can read but not change it); the message
# itself is added only as data (Base64 JSON)
$ps = Join-Path $dir 'pk-toast.ps1'
$have = $null
if (Test-Path $ps) { try { $have = (Get-FileHash -LiteralPath $ps -Algorithm SHA256).Hash } catch {} }
if ($have -ne '__SCRIPTHASH__') {   # checksum: even pure upper/lower-case changes are adopted
  try { Set-Content -LiteralPath $ps -Value __SCRIPT__ -Encoding UTF8 -NoNewline }
  catch { if (-not (Test-Path $ps)) { throw } }   # just in use by a second message - use the existing one
}
# Timestamp in the ID: leftover feedback (abort, timeout) can safely be cleaned up later
$id = (Get-Date).ToString('yyMMddHHmm') + [guid]::NewGuid().ToString('N').Substring(0, 8)
$build = [int](Get-CimInstance Win32_OperatingSystem).BuildNumber
$pse = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
# Start method per user: normally -File (the script is under "Program Files" - there AppLocker/WDAC allows it even in
# Constrained Language Mode; an encoded command would run restricted there and fail, 2.24.4). Only if a
# group policy sets the execution policy to AllSigned/Restricted (then "-ExecutionPolicy Bypass" does not help
# with -File), a short encoded starter that reads the script from "Program Files".
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
  if (($pol -eq 'AllSigned' -or $pol -eq 'Restricted') -and $ExecutionContext.SessionState.LanguageMode -eq 'FullLanguage') {
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
  # Wait for the result on the user's side, 25 seconds at most. The value that the display script stores in
  # HKCU\Software\PocketKnife\Toast is authoritative - the task's exit code does not reliably arrive via conhost.
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
      'PK_TOAST:res:' + $users[$k - 1] + ':14'   # account not resolvable - feedback not readable
    } elseif ($ti -and $ti.LastTaskResult -ne 0 -and $ti.LastTaskResult -ne 1) {
      'PK_TOAST:res:' + $users[$k - 1] + ':' + $ti.LastTaskResult   # e.g. logon error of the task
    } elseif ($ti) {
      'PK_TOAST:res:' + $users[$k - 1] + ':13'   # task ended, but the display script never answered
    }
  }
  # Remove leftover feedback older than 30 minutes (earlier aborts) - running ones of other messages stay
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
  # Started tasks keep running even if the task is deleted
  Get-ScheduledTask -TaskPath '\POCKETKNIFE\' -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -like ('PK_Toast_' + $id + '_*') } |
    Unregister-ScheduledTask -Confirm:$false -ErrorAction SilentlyContinue
}
'PK_TOAST:sent:' + $ok + ':' + ($users -join ',')
"""


def _icon_bytes():
    try:
        with open(ICON, "rb") as fh:
            return fh.read()
    except OSError:
        return b""


def _user_ps_bytes():
    """Exactly how Set-Content -Encoding UTF8 writes the file (Windows PowerShell: with BOM) - for the checksum comparison."""
    return b"\xef\xbb\xbf" + USER_PS.encode("utf-8")


FOUND_RX = re.compile(r"(?:Found|Gefunden)\s+(.+?) \[([^\]]+)\] Version (\S+)")


def _tree(p):
    """Step ID → (step, package it belongs to) - also in nested packages."""
    from . import steps as stp
    out = {}

    def walk(pkg, depth=0):
        try:
            for _k, st in stp.iter_steps(stp.normalize(json.loads(json.dumps(pkg)))):
                if st.get("id"):
                    out.setdefault(st["id"], (st, pkg))
                if st.get("type") == "nested" and isinstance(st.get("package"), dict) and depth < 10:
                    walk(st["package"], depth + 1)  # nested packages ("Paket: TightVNC ...", schedules with several packages)
        except Exception:
            pass
    walk(p)
    return out


def _real_install(st, mode):
    """Does this step install/update a program? (Uninstalls and pure scripts do not - except "immer")"""
    if mode == "on":
        return True
    if st.get("type") != "install":
        return False
    if st.get("source") == "winget":
        return st.get("winget_action") in ("install", "upgrade")
    return st.get("install_type") != "script"


def classify(p, runner_results, log_list, computer_id=None, global_on=True):
    """[(kind, name, version), ...] of the programs that this run really installed/updated - empty if
    no message is appropriate. kind: "installed"/"updated". The step itself and the package it belongs
    to are authoritative (also nested: "nie"/"immer" of the inner package apply)."""
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
            continue  # nothing changed
        # Output of exactly this step (log block "=== n. ...")
        out = next((x for x in log_list or [] if x.startswith(f"=== {r.get('n')}. ")), "")
        kind = "updated" if (st.get("source") == "winget" and st.get("winget_action") == "upgrade") \
            or name.startswith("Aktualisieren:") else "installed"
        disp, ver = name, (owner.get("version") or "").strip()
        f = FOUND_RX.search(out)
        if f:
            disp, wid, ver = f.group(1).strip(), f.group(2), f.group(3)
            if kind == "installed" and computer_id and db.val(
                    "SELECT 1 FROM software WHERE computer_id=? AND lower(winget_id)=lower(?)", (computer_id, wid)):
                kind = "updated"  # was already there in an older version
        disp = re.sub(r"^(Kiosk|Zeitplan|Regel|Aktualisieren|auto)\s*:?\s+", "", disp, flags=re.I).strip() or name
        if disp.lower() in seen:
            continue
        seen.add(disp.lower())
        items.append((kind, disp[:120], ver[:60]))
    return items


def app_id(sender):
    """Sender ID per display name - Windows does not adopt a changed name for a known ID (2.24.6)."""
    return APP_ID + "." + hashlib.sha256(sender.encode("utf-8")).hexdigest()[:10]


def build_send_script(items):
    """items: [(kind, name, version), ...] → (PowerShell for WinRM, data of the message)."""
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
    # Script under "Program Files" (only administrators may write), message only as Base64 data in the call
    b64 = base64.b64encode(json.dumps(data, ensure_ascii=False).encode("utf-8")).decode("ascii")
    ico = _icon_bytes()
    out = (SEND_PS.replace("__ICONHASH__", hashlib.sha256(ico).hexdigest().upper())
           .replace("__ICONBYTES__", ",".join(str(b) for b in ico))
           .replace("__SCRIPTHASH__", hashlib.sha256(_user_ps_bytes()).hexdigest().upper())
           .replace("__DATA__", b64))
    # last: the display script as a text literal (its content is no longer replaced)
    return out.replace("__SCRIPT__", util.psq(USER_PS), 1), data


def maybe_notify(c, host, p, runner):
    """Call after a successful target. Returns immediately; the message goes out in the background."""
    try:
        items = classify(p, runner.results, runner.log, c.get("id"), global_on=bool(db.setting("toast_enabled")))
        if not items:
            return None
        script, data = build_send_script(items)
    except Exception as e:  # noqa: BLE001 – a message must never disturb the deployment
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
    except Exception as e:  # noqa: BLE001 – e.g. too many threads: then simply no message
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
    """[(user, ok, text)] from the feedback per logged-on user."""
    res = []
    for u, code in re.findall(r"PK_TOAST:res:(.+):(-?\d+)\s*$", out or "", re.M):
        code = int(code)
        ok, text = RESULT_TEXT.get(code, (False, f"Aufgabe endete mit Code {code} (0x{code & 0xFFFFFFFF:08X})"))
        res.append((u.strip(), ok, text))
    return res


def test(c, user):
    """The "Test-Benachrichtigung senden" action from the settings."""
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
