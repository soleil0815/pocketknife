# POCKET KNIFE – default scanners. Each section '# @module <key> | <label>' is a separate scanner
# that can be selected individually in scan profiles. '# @prelude' is always executed.
# @prelude
$ErrorActionPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'
function D($d) { if ($d) { try { ([datetime]$d).ToString('yyyy-MM-dd HH:mm:ss') } catch { "$d" } } else { $null } }
$r = [ordered]@{}
$e = [ordered]@{}
$cs = Get-CimInstance Win32_ComputerSystem
$os = Get-CimInstance Win32_OperatingSystem
$bios = Get-CimInstance Win32_BIOS
$cv = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
$r.errors = @()
function PKS([string]$name, [scriptblock]$sb) {
  try { $r[$name] = @(& $sb) } catch { $r[$name] = @(); $r.errors += ($name + ': ' + $_.Exception.Message) }
}
function PKX([string]$name, [scriptblock]$sb) {
  try { $e[$name] = & $sb } catch { $r.errors += ($name + ': ' + $_.Exception.Message) }
}
function PKO($o, [int]$max = 40) {
  $h = [ordered]@{}; $i = 0
  foreach ($p in $o.PSObject.Properties) {
    if ($p.Name -match '^(PS|Cim)' -or $i -ge $max) { continue }
    $v = $p.Value
    if ($v -is [datetime]) { $v = $v.ToString('yyyy-MM-dd HH:mm:ss') }
    elseif ($v -is [array]) { $v = ($v | ForEach-Object { "$_" }) -join ', ' }
    elseif ($null -ne $v) { $v = "$v" }
    $h[$p.Name] = $v; $i++
  }
  $h
}
function PKStr($a) { if ($a) { (($a | Where-Object { $_ -ne 0 } | ForEach-Object { [char]$_ }) -join '').Trim() } else { '' } }

# @module active_directory | Active Directory
# (runs server-side via LDAPS – no PowerShell on the client)

# @module computer | Computer-Details (Allgemein, Betriebssystem, System)
try {
$cpu = @(Get-CimInstance Win32_Processor)
# @sessions-begin (2.32.6: identical to remote.SESSIONS_PS – the self-test checks this)
# Session state per logged-in user: disconnected (Remote Desktop/user switch, quser) or locked (LogonUI.exe
# runs in its session). Only cmdlets, hashtables and quser.exe – also works in Constrained Language Mode.
$pkLui = @(Get-CimInstance Win32_Process -Filter "Name='LogonUI.exe'" | ForEach-Object { [int]$_.SessionId })
$pkQ = @{}
$pkQo = @()
try { $pkQo = @(& (Join-Path $env:SystemRoot 'System32\quser.exe') 2>$null) } catch {}
# The ID is right-aligned under "ID" (header line in every language) – read from there on, user/session names before it do not matter
$pkIp = -1
if ($pkQo.Count) { $pkIp = ("" + $pkQo[0]).IndexOf(' ID ') }
foreach ($l in @($pkQo | Select-Object -Skip 1)) {
  $t = "" + $l
  if ($pkIp -gt 4 -and $t.Length -gt $pkIp) { $t = $t.Substring($pkIp - 4) } elseif ($t -match '^\s*>?\S+\s+(?:\S+\s+)?(\d+\s+\S+.*)$') { $t = $matches[1] }
  if ($t -match '^\s*(\d+)\s+(\S+)') { $pkQ[[int]$matches[1]] = $matches[2] }
}
$pkSt = @{}
$pkExp = @(Get-CimInstance Win32_Process -Filter "Name='explorer.exe'" | ForEach-Object {
  $o = Invoke-CimMethod -InputObject $_ -MethodName GetOwner
  if ($o -and $o.User) {
    $n = $o.Domain + '\' + $o.User
    $sid = [int]$_.SessionId
    $s = 'active'
    if ("" + $pkQ[$sid] -match '^(Disc|Getr|D.co|Desc|Disco|Getrennt)') { $s = 'disconnected' } elseif ($pkLui -contains $sid) { $s = 'locked' }
    # several sessions of one user: active before locked before disconnected
    $rank = @{ active = 3; locked = 2; disconnected = 1 }
    if (-not $pkSt.ContainsKey($n) -or $rank[$s] -gt $rank[$pkSt[$n]]) { $pkSt[$n] = $s }
    $n
  } })
# 2.34: Server Core (no Explorer) – logged-in users from quser (name without domain)
if ($pkExp.Count -eq 0 -and $pkQo.Count -gt 1) {
  $pkExp = @(foreach ($l in @($pkQo | Select-Object -Skip 1)) {
    $t = "" + $l
    if ($pkIp -gt 4 -and $t.Length -gt $pkIp) { $nm = $t.Substring(0, $pkIp - 4); $rest = $t.Substring($pkIp - 4) } else { $nm = $t; $rest = '' }
    if ($nm -match '^\s*>?(\S+)') {
      $n = $matches[1]; $s = 'active'
      if ($rest -match '^\s*(\d+)\s+(\S+)') { if ($matches[2] -match '^(Disc|Getr|D.co|Desc|Disco|Getrennt)') { $s = 'disconnected' } elseif ($pkLui -contains [int]$matches[1]) { $s = 'locked' } }
      if (-not $pkSt.ContainsKey($n)) { $pkSt[$n] = $s }
      $n
    } })
}
# @sessions-end
$pkUser = $cs.UserName
if (-not $pkUser) {
  # With Remote Desktop sessions Win32_ComputerSystem.UserName stays empty – determine logged-in users via explorer.exe
  $pkUser = (@($pkExp) | Select-Object -Unique) -join ', '
}
$e.user_states = [ordered]@{}
foreach ($k in $pkSt.Keys) { if ($pkSt[$k] -ne 'active') { $e.user_states[$k] = $pkSt[$k] } }
$dv = $cv.DisplayVersion; if (-not $dv) { $dv = $cv.ReleaseId }
$osName = $os.Caption
if ([int]$os.BuildNumber -ge 22000 -and $osName -match 'Windows 10') { $osName = $osName -replace 'Windows 10', 'Windows 11' }
$r.system = [ordered]@{
  manufacturer = $cs.Manufacturer; model = $cs.Model; ram = [int64]$cs.TotalPhysicalMemory; user = $pkUser
  serial = $bios.SerialNumber; bios = $bios.SMBIOSBIOSVersion; bios_date = (D $bios.ReleaseDate)
  cpu = ("" + ($cpu | Select-Object -First 1).Name).Trim()
  cores = [int](($cpu | Measure-Object -Property NumberOfCores -Sum).Sum)
  threads = [int](($cpu | Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum)
  os_name = $osName; os_version = $os.Version; os_build = ("" + $os.BuildNumber + $(if ($cv.UBR -ne $null) { '.' + $cv.UBR } else { '' }))
  display_version = $dv; os_arch = $os.OSArchitecture; install_date = (D $os.InstallDate); last_boot = (D $os.LastBootUpTime)
}
if ($os.ProductType -eq 1) {
  try { $ai = Get-AppxPackage -AllUsers -Name Microsoft.DesktopAppInstaller -ErrorAction Stop | Sort-Object Version -Descending | Select-Object -First 1
    if ($ai) { $e.winget = "$($ai.Version)" } } catch {}
} else {
  # 2.34: Server – Get-AppxPackage -AllUsers walks through all user profiles (on terminal servers with many profiles
  # for minutes). The installed packages are also in the registry (AppxAllUserStore) – fast and without profiles.
  try {
    # two levels (Applications or per user SID) – without -Depth, PowerShell 4 (Server 2012 R2) does not know it
    $au = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Appx\AppxAllUserStore'
    # only installed packages (Applications, InboxApplications, per user SID) – not EndOfLife, Staged, Deprovisioned
    $wk = @(Get-ChildItem $au -ErrorAction SilentlyContinue | Where-Object { $_.PSChildName -match '^(Applications|InboxApplications|S-1-.*)$' } |
      ForEach-Object { Get-ChildItem $_.PSPath -ErrorAction SilentlyContinue })
    $wv = @($wk | ForEach-Object { if ($_.PSChildName -match '^Microsoft\.DesktopAppInstaller_([\d.]+)_') { $matches[1] } } |
      Sort-Object { try { [version]$_ } catch { [version]'0.0' } } -Descending)
    if ($wv.Count) { $e.winget = $wv[0] }
  } catch {}
}
$e.pending_reboot = [bool]((Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') -or
  (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired'))
$e.timezone = (Get-CimInstance Win32_TimeZone).Caption
$e.ps_version = $PSVersionTable.PSVersion.ToString()
$enc = Get-CimInstance Win32_SystemEnclosure
$csp = Get-CimInstance Win32_ComputerSystemProduct
PKX 'chassis' { @($enc.ChassisTypes)[0] }
PKX 'bios_manufacturer' { $bios.Manufacturer }
PKX 'asset_tag' { $enc.SMBIOSAssetTag }
PKX 'family' { $cs.SystemFamily }
PKX 'product_version' { $csp.Version }
PKX 'sku' { $cs.SystemSKUNumber }
PKX 'uuid' { $csp.UUID }
PKX 'domain' { $cs.Domain }
PKX 'os_serial' { $os.SerialNumber }
PKX 'system_drive' { $os.SystemDrive }
PKX 'product_type' { $os.ProductType }
# 2.40: update status - edition (LTSC = EnterpriseS), Office Click-to-Run with version and update channel
PKX 'os_edition' { $cv.EditionID }
PKX 'os_install_type' { $cv.InstallationType }
# 2.44.4: language mode of PowerShell on the client (ConstrainedLanguage = software restriction/AppLocker active)
PKX 'ps_language' { "" + $ExecutionContext.SessionState.LanguageMode }
# 2.43: Windows 10 Extended Security Updates - highest licensed ESU year (activation IDs from Microsoft, same for
# every edition); only on Windows 10 22H2/21H2 outside LTSC, filtered by ID (the unfiltered class is very slow)
PKX 'os_esu' {
  if (("" + $os.Caption) -match 'Windows 10' -and ("" + $cv.EditionID) -notmatch 'EnterpriseS' -and [int]$cv.CurrentBuildNumber -ge 19044) {
    $esuIds = @{ 'f520e45e-7413-4a34-a497-d2765967d094' = 1; '1043add5-23b1-4afb-9a0f-64343c8f3f8d' = 2; '83d49986-add3-41d7-ba33-87c7bfb5c0fb' = 3 }
    $flt = (@($esuIds.Keys) | ForEach-Object { "ID='" + $_ + "'" }) -join ' OR '
    $yr = 0
    foreach ($l in @(Get-CimInstance -ClassName SoftwareLicensingProduct -Filter $flt -Property ID, LicenseStatus -ErrorAction Stop)) {
      $y = $esuIds[("" + $l.ID).ToLower()]
      if ($y -and [int]$l.LicenseStatus -eq 1 -and $y -gt $yr) { $yr = $y }
    }
    $yr
  }
}
PKX 'office_c2r' {
  $o = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Office\ClickToRun\Configuration' -ErrorAction SilentlyContinue
  if ($o -and $o.VersionToReport) {
    $ch = "" + $o.UpdateChannel
    if (-not $ch) { $ch = "" + $o.CDNBaseUrl }
    # 2.44.3: UpdateChannel can be a network share (UpdatePath) or empty - the channel is then in CDNBaseUrl,
    # AudienceId (GUID) or AudienceData (e.g. "Production::LTSC2024")
    [ordered]@{ version = "" + $o.VersionToReport; channel = $ch; products = "" + $o.ProductReleaseIds; platform = "" + $o.Platform
                cdn = "" + $o.CDNBaseUrl; audience_id = "" + $o.AudienceId; audience = "" + $o.AudienceData }
  }
}
# 2.34: virtual machine? Manufacturer/model/BIOS reveal the hypervisor (HypervisorPresent alone does not – it is also true
# on real hardware with Hyper-V or virtualization-based security)
PKX 'virtual' {
  $vm = ("" + $cs.Manufacturer + ' | ' + $cs.Model + ' | ' + $bios.Manufacturer + ' | ' + $bios.SMBIOSBIOSVersion + ' | ' + $csp.Version + ' | ' + $csp.Vendor)
  if ($vm -match 'VMware') { 'VMware' }
  elseif ($vm -match 'VirtualBox|innotek') { 'VirtualBox' }
  elseif ($vm -match 'Proxmox') { 'Proxmox (KVM)' }
  elseif ($vm -match 'Nutanix|AHV') { 'Nutanix AHV' }
  elseif ($vm -match 'QEMU|KVM|Bochs|SeaBIOS|OVMF') { 'KVM/QEMU (z. B. Proxmox)' }
  elseif ($vm -match 'Xen') { 'Xen' }
  elseif ($vm -match 'Parallels') { 'Parallels' }
  elseif ($vm -match 'Amazon EC2') { 'Amazon EC2' }
  elseif ($vm -match 'Google Compute') { 'Google Cloud' }
  elseif ($vm -match 'Microsoft Corporation.*Virtual Machine|Hyper-V|Virtual Machine') { 'Hyper-V' }
  else { '' }
}
PKX 'locale' { $os.Locale }
PKX 'bios_mode' { $env:firmware_type }
PKX 'secure_boot' { try { [bool](Confirm-SecureBootUEFI -ErrorAction Stop) } catch { $null } }
PKX 'smb1_server' { try { [bool](Get-SmbServerConfiguration -ErrorAction Stop).EnableSMB1Protocol } catch { $null } }
PKX 'smb1_client' { $s = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\mrxsmb10' -ErrorAction SilentlyContinue; [bool]($s -and $s.Start -ne 4) }
PKX 'ie_version' { $ie = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Internet Explorer' -ErrorAction SilentlyContinue; if ($ie.svcVersion) { $ie.svcVersion } else { $ie.Version } }
PKX 'domain_controller' { (Get-CimInstance Win32_NTDomain | Where-Object { $_.DomainControllerName } | Select-Object -First 1).DomainControllerName }
PKX 'dotnet' {
  $v = @()
  $n35 = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\NET Framework Setup\NDP\v3.5' -ErrorAction SilentlyContinue
  if ($n35.Install -eq 1) { $v += '3.5' }
  $rel = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full' -ErrorAction SilentlyContinue).Release
  if ($rel) {
    $map = [ordered]@{ 533320 = '4.8.1'; 528040 = '4.8'; 461808 = '4.7.2'; 461308 = '4.7.1'; 460798 = '4.7'; 394802 = '4.6.2'; 394254 = '4.6.1'; 393295 = '4.6'; 379893 = '4.5.2'; 378675 = '4.5.1'; 378389 = '4.5' }
    foreach ($k in $map.Keys) { if ($rel -ge $k) { $v += $map[$k]; break } }
  }
  $core = Join-Path $env:ProgramFiles 'dotnet\shared\Microsoft.NETCore.App'
  if (Test-Path $core) { Get-ChildItem $core -Directory | ForEach-Object { $v += ('.NET ' + $_.Name) } }
  $v -join ', '
}
PKX 'last_wu_install' { try { $au = New-Object -ComObject Microsoft.Update.AutoUpdate; D $au.Results.LastInstallationSuccessDate } catch { $null } }
} catch { $r.errors += ('computer: ' + $_.Exception.Message) }

# @module applications | Anwendungen
try {
$paths = @(
  @{ p = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'; a = $(if ($env:PROCESSOR_ARCHITECTURE -ne 'x86' -or $env:PROCESSOR_ARCHITEW6432) { 'x64' } else { 'x86' }); s = 'Computer' },
  @{ p = 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'; a = 'x86'; s = 'Computer' }
)
foreach ($u in (Get-ChildItem Registry::HKEY_USERS | Where-Object { $_.PSChildName -match '^S-1-5-21-[\d-]+$' })) {
  $paths += @{ p = "Registry::HKEY_USERS\$($u.PSChildName)\Software\Microsoft\Windows\CurrentVersion\Uninstall\*"; a = ''; s = 'Benutzer' }
}
$pkFull = $ExecutionContext.SessionState.LanguageMode -eq 'FullLanguage'
try {
  # 2.38: only with full language mode (software restriction/AppLocker/App Control: Constrained Language Mode – no Add-Type)
  if (-not $pkFull) { throw 'CLM' }
  Add-Type -ErrorAction Stop -TypeDefinition @"
using System; using System.Runtime.InteropServices;
public static class PKReg {
  [DllImport("advapi32.dll", CharSet = CharSet.Unicode)]
  static extern int RegQueryInfoKey(Microsoft.Win32.SafeHandles.SafeRegistryHandle h, IntPtr c, IntPtr cl, IntPtr r, IntPtr sk, IntPtr msk, IntPtr mcl, IntPtr v, IntPtr mvn, IntPtr mvl, IntPtr sd, out long ft);
  public static string LastWrite(Microsoft.Win32.RegistryKey k) {
    long ft;
    if (RegQueryInfoKey(k.Handle, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, out ft) == 0)
      return DateTime.FromFileTime(ft).ToString("yyyy-MM-dd HH:mm:ss");
    return null;
  }
}
"@
  $pkLw = $true
} catch { $pkLw = $false }
# 2.38: values via Get-ItemProperty (properties) instead of the GetValue method – methods of the registry classes are
# blocked in Constrained Language Mode
function PKRegVal($pv, $n) { $v = $pv.$n; if ($null -eq $v) { '' } else { ("" + $v).Trim() } }
$sw = @()
try {
$sw = foreach ($x in $paths) {
  Get-ChildItem -Path ($x.p -replace '\\\*$', '') -ErrorAction SilentlyContinue | ForEach-Object {
    $k = $_
    $pv = Get-ItemProperty -LiteralPath $k.PSPath -ErrorAction SilentlyContinue
    if (-not $pv) { return }
    $dn = PKRegVal $pv 'DisplayName'
    if (-not $dn -or $pv.SystemComponent -eq 1 -or $pv.ParentKeyName -or (PKRegVal $pv 'ReleaseType') -match 'Update|Hotfix') { return }
    $lw = $null
    if ($pkLw) { try { $lw = [PKReg]::LastWrite($k) } catch {} }
    $parts = $k.Name -split '\\', 2
    [ordered]@{ name = $dn; version = (PKRegVal $pv 'DisplayVersion'); publisher = (PKRegVal $pv 'Publisher')
                install_date = (PKRegVal $pv 'InstallDate'); key = $k.PSChildName; arch = $x.a; scope = $x.s
                uninstall = (PKRegVal $pv 'UninstallString'); quiet = (PKRegVal $pv 'QuietUninstallString'); modify = (PKRegVal $pv 'ModifyPath')
                location = (PKRegVal $pv 'InstallLocation'); size_kb = $pv.EstimatedSize; modified = $lw
                hive = $parts[0]; reg_path = $(if ($parts.Count -gt 1) { $parts[1] } else { '' })
                no_remove = [bool]($pv.NoRemove -eq 1) } } }
} catch { $r.software_error = $_.Exception.Message }
# 2.40.1: MSIX apps (e.g. PowerShell 7 installed by winget) are not in the Uninstall registry. The installed packages
# are listed under AppxAllUserStore – fast (no walk through the user profiles) and CLM-safe (no Appx module needed).
# Skipped: resource packages, frameworks (VCLibs, UI.Xaml ...) and inbox apps (InboxApplications).
$mx = @()
try {
  $au = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Appx\AppxAllUserStore'
  $mxSeen = @{}
  # 2.41.3: packages Windows has marked for removal (EndOfLife\<SID>\<full name>) - leftovers of removed apps
  # (e.g. "MSTeams" 1.0.0.0 of an old profile: still under S-1-..., but no longer installed)
  $mxEol = @{}
  # never name a variable $e here - that is the extras hashtable of the whole scan (2.44.2: it was overwritten)
  foreach ($eolSid in @(Get-ChildItem ($au + '\EndOfLife') -ErrorAction SilentlyContinue)) {
    foreach ($ek in @(Get-ChildItem $eolSid.PSPath -ErrorAction SilentlyContinue)) { $mxEol[$ek.PSChildName] = 1 }
  }
  $mx = foreach ($grp in @(Get-ChildItem $au -ErrorAction SilentlyContinue | Where-Object { $_.PSChildName -match '^(Applications|S-1-.*)$' })) {
    foreach ($k in @(Get-ChildItem $grp.PSPath -ErrorAction SilentlyContinue)) {
      $fn = $k.PSChildName
      if ($fn -notmatch '^([A-Za-z0-9.\-]+)_(\d+(?:\.\d+){1,3})_([A-Za-z0-9]*)_~?_([a-z0-9]+)$') { continue }
      $nm = $matches[1]; $ver = $matches[2]; $ar = $matches[3]; $pubId = $matches[4]
      if ($nm -match '^Microsoft\.(VCLibs|NET\.|UI\.Xaml|WindowsAppRuntime|WinAppRuntime|Services\.Store\.Engagement|Advertising\.|DirectXRuntime)') { continue }
      if ($mxEol.ContainsKey($fn)) { continue }
      if ($mxSeen.ContainsKey($nm + '_' + $ver)) { continue }
      $mxSeen[$nm + '_' + $ver] = 1
      $pv = Get-ItemProperty -LiteralPath $k.PSPath -ErrorAction SilentlyContinue
      $mp = ''
      if ($pv -and $pv.Path) { $mp = ("" + $pv.Path).Trim() }
      if (-not $mp) { $mp = $env:ProgramFiles + '\WindowsApps\' + $fn + '\AppxManifest.xml' }
      # 2.41.2: manifest outside WindowsApps/SystemApps = sparse package or registered from a folder - not a separate program
      if ($mp -notmatch '[\\/](WindowsApps|SystemApps)[\\/]') { continue }
      # 2.41.3: package folder gone = not installed (registry leftover). Only "not found" counts - with
      # "access denied" the package is kept (name from the registry)
      if (-not (Test-Path -LiteralPath $mp)) {
        $gone = $false
        try { $null = Get-Item -LiteralPath $mp -Force -ErrorAction Stop } catch { $gone = ("" + $_.FullyQualifiedErrorId) -match 'PathNotFound' }
        if ($gone) { continue }
      }
      $dn = $nm; $pub = ''; $fw = $false
      try {
        if (Test-Path -LiteralPath $mp) {
          $xm = [xml]((Get-Content -LiteralPath $mp -ErrorAction Stop) -join "`n")
          $pp = $xm.Package.Properties
          if ($pp) {
            # 2.41.2: sparse packages (AllowExternalContent) are only an identity of a normal program for the Windows 11
            # context menu (Notepad++ 1.0.0.0, Adobe Acrobat Reader 26.0.0.1 ...) - the program itself is in the uninstall registry
            $fw = ("" + $pp.Framework) -eq 'true' -or ("" + $pp.ResourcePackage) -eq 'true' -or ("" + $pp.AllowExternalContent) -eq 'true'
            $d = ("" + $pp.DisplayName).Trim(); if ($d -and $d -notmatch '^ms-resource:') { $dn = $d }
            $d = ("" + $pp.PublisherDisplayName).Trim(); if ($d -and $d -notmatch '^ms-resource:') { $pub = $d }
          }
        }
      } catch {}
      if ($fw) { continue }
      [ordered]@{ name = $dn; version = $ver; publisher = $pub; install_date = ''; key = ($nm + '_' + $pubId)
                  arch = $(if ($ar) { $ar } else { 'neutral' }); scope = $(if ($grp.PSChildName -eq 'Applications') { 'Computer' } else { 'Benutzer' })
                  uninstall = ''; quiet = ''; modify = ''; location = $(if ($mp -match '\\AppxManifest\.xml$') { $mp -replace '\\AppxManifest\.xml$', '' } else { '' })
                  size_kb = $null; modified = $null; hive = 'MSIX'; reg_path = ($grp.PSChildName + '\' + $fn); no_remove = $false }
    }
  }
} catch { $r.errors += ('msix: ' + $_.Exception.Message) }
$r.software = @($sw) + @($mx)
} catch { $r.errors += ('applications: ' + $_.Exception.Message) }

# @module cpu | CPU
try {
PKS 'cpus' { Get-CimInstance Win32_Processor | ForEach-Object { [ordered]@{
  name = ("" + $_.Name).Trim(); manufacturer = $_.Manufacturer; cores = $_.NumberOfCores; logical = $_.NumberOfLogicalProcessors
  max_mhz = $_.MaxClockSpeed; current_mhz = $_.CurrentClockSpeed; l2_kb = $_.L2CacheSize; l3_kb = $_.L3CacheSize
  socket = $_.SocketDesignation; architecture = $_.Architecture; virtualization = $_.VirtualizationFirmwareEnabled
  processor_id = $_.ProcessorId; description = $_.Description } } }
} catch { $r.errors += ('cpu: ' + $_.Exception.Message) }

# @module memory | Speichermodule
try {
PKS 'memory' { Get-CimInstance Win32_PhysicalMemory | ForEach-Object { [ordered]@{
  slot = $_.DeviceLocator; bank = $_.BankLabel; capacity = [int64]$_.Capacity
  speed = $(if ($_.ConfiguredClockSpeed) { $_.ConfiguredClockSpeed } else { $_.Speed })
  manufacturer = ("" + $_.Manufacturer).Trim(); part = ("" + $_.PartNumber).Trim(); serial = ("" + $_.SerialNumber).Trim()
  form_factor = $_.FormFactor; type = $_.SMBIOSMemoryType } } }
PKX 'memory_slots' { (Get-CimInstance Win32_PhysicalMemoryArray | Select-Object -First 1).MemoryDevices }
PKX 'memory_max' { $ma = Get-CimInstance Win32_PhysicalMemoryArray | Select-Object -First 1; if ($ma.MaxCapacityEx) { [int64]$ma.MaxCapacityEx * 1024 } else { [int64]$ma.MaxCapacity * 1024 } }
} catch { $r.errors += ('memory: ' + $_.Exception.Message) }

# @module disk_drives | Laufwerke
try {
$r.disks = @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | ForEach-Object {
  [ordered]@{ drive = $_.DeviceID; label = $_.VolumeName; fs = $_.FileSystem; size = [int64]$_.Size; free = [int64]$_.FreeSpace } })
PKS 'physical_disks' {
  $pd = @{}
  try { Get-PhysicalDisk -ErrorAction Stop | ForEach-Object { $pd["$($_.DeviceId)"] = $_ } } catch {}
  Get-CimInstance Win32_DiskDrive | ForEach-Object {
    $p = $pd["$($_.Index)"]
    [ordered]@{ model = ("" + $_.Model).Trim(); serial = ("" + $_.SerialNumber).Trim(); size = [int64]$_.Size
      interface = $_.InterfaceType; media = $(if ($p) { "$($p.MediaType)" } else { $_.MediaType }); bus = $(if ($p) { "$($p.BusType)" } else { '' })
      health = $(if ($p) { "$($p.HealthStatus)" } else { $_.Status }); firmware = $_.FirmwareRevision; partitions = $_.Partitions } } }
PKS 'volumes' { Get-CimInstance Win32_Volume -Filter 'DriveType=3' | ForEach-Object { [ordered]@{
  drive = $_.DriveLetter; label = $_.Label; fs = $_.FileSystem; size = [int64]$_.Capacity; free = [int64]$_.FreeSpace
  boot = $_.BootVolume; system = $_.SystemVolume; path = $_.Name } } }
} catch { $r.errors += ('disk_drives: ' + $_.Exception.Message) }

# @module displays | Bildschirme
try {
# 2.34: servers and virtual machines without a display report "not supported" – that is not an error, there are just no displays
PKS 'displays' { $mon = @(); try { $mon = @(Get-CimInstance -Namespace root\wmi -ClassName WmiMonitorID -ErrorAction Stop) } catch {
    if ("$_" -notmatch 'nicht unterst|not supported|8004100C|Ungültige Klasse|Invalid class|Keine Instanzen|No instances') { throw } }
  $mon | ForEach-Object { [ordered]@{
  manufacturer = (PKStr $_.ManufacturerName); model = (PKStr $_.UserFriendlyName); serial = (PKStr $_.SerialNumberID)
  product_code = (PKStr $_.ProductCodeID); year = $_.YearOfManufacture; week = $_.WeekOfManufacture; active = $_.Active } } }
} catch { $r.errors += ('displays: ' + $_.Exception.Message) }

# @module environment | Umgebungsvariablen
try {
PKS 'environment' { Get-CimInstance Win32_Environment | Where-Object { $_.UserName -eq '<SYSTEM>' } | Sort-Object Name | ForEach-Object {
  [ordered]@{ name = $_.Name; value = $_.VariableValue } } }
} catch { $r.errors += ('environment: ' + $_.Exception.Message) }

# @module hardware | Hardware
try {
# Shadow copies, legacy drivers and software devices are not hardware (often hundreds of entries on VMs)
PKS 'devices' { Get-CimInstance Win32_PnPEntity | Where-Object { $_.Name -and $_.PNPClass -notin @('VolumeSnapshot', 'LegacyDriver', 'SoftwareComponent') -and $_.DeviceID -notlike 'SWD\PRINTENUM\*' } | ForEach-Object { [ordered]@{
  name = $_.Name; class = $_.PNPClass; manufacturer = $_.Manufacturer; status = $_.Status; problem = $_.ConfigManagerErrorCode
  device_id = $_.DeviceID } } }
} catch { $r.errors += ('hardware: ' + $_.Exception.Message) }

# @module hotfixes | Hotfixes
try {
$r.hotfixes = @(Get-CimInstance Win32_QuickFixEngineering | ForEach-Object {
  [ordered]@{ id = $_.HotFixID; description = $_.Description; installed_on = (D $_.InstalledOn); by = $_.InstalledBy } })
} catch { $r.errors += ('hotfixes: ' + $_.Exception.Message) }

# @module local_groups | Benutzer & Gruppen: lokale Gruppen (inkl. Administratoren)
try {
# 2.38: members via ADSI (fast, also orphaned SIDs) – in Constrained Language Mode via CIM (Win32_GroupUser)
function PK-GroupMembers($grp) {
  if ($ExecutionContext.SessionState.LanguageMode -eq 'FullLanguage') {
    $g = [ADSI]("WinNT://$env:COMPUTERNAME/" + $grp.Name + ",group")
    @($g.psbase.Invoke('Members') | ForEach-Object {
      $p = $_.GetType().InvokeMember('ADsPath', 'GetProperty', $null, $_, $null)
      $c = $_.GetType().InvokeMember('Class', 'GetProperty', $null, $_, $null)
      [ordered]@{ name = (($p -replace '^WinNT://', '') -replace '/', '\'); type = $c } })
  } else {
    @(Get-CimAssociatedInstance -InputObject $grp -Association Win32_GroupUser -ErrorAction Stop | ForEach-Object {
      [ordered]@{ name = ($_.Domain + '\' + $_.Name); type = $(if ($_.CimClass.CimClassName -eq 'Win32_Group') { 'Group' } else { 'User' }) } })
  }
}
$admins = @()
try {
  $grp = Get-CimInstance Win32_Group -Filter "LocalAccount=True AND SID='S-1-5-32-544'"
  $admins = @(PK-GroupMembers $grp)
} catch {}
$r.admins = $admins
PKS 'local_groups' { Get-CimInstance Win32_Group -Filter 'LocalAccount=True' | ForEach-Object {
  $grp = $_; $m = @()
  try { $m = @(PK-GroupMembers $grp | ForEach-Object { $_.name }) } catch {}
  [ordered]@{ name = $grp.Name; description = $grp.Description; sid = $grp.SID; members = ($m -join '; '); count = $m.Count } } }
} catch { $r.errors += ('local_groups: ' + $_.Exception.Message) }

# @module local_users | Benutzer & Gruppen: lokale Benutzer
try {
PKS 'local_users' {
  try {
    Get-LocalUser -ErrorAction Stop | ForEach-Object { [ordered]@{
      name = $_.Name; full_name = $_.FullName; enabled = $_.Enabled; description = $_.Description; last_logon = (D $_.LastLogon)
      password_last_set = (D $_.PasswordLastSet); password_expires = (D $_.PasswordExpires); password_required = $_.PasswordRequired
      sid = "$($_.SID)" } }
  } catch {
    Get-CimInstance Win32_UserAccount -Filter 'LocalAccount=True' | ForEach-Object { [ordered]@{
      name = $_.Name; full_name = $_.FullName; enabled = -not $_.Disabled; description = $_.Description; locked = $_.Lockout
      password_required = $_.PasswordRequired; sid = $_.SID } }
  } }
} catch { $r.errors += ('local_users: ' + $_.Exception.Message) }

# @module nics | Netzwerkkarten
try {
$r.network = @(Get-CimInstance Win32_NetworkAdapterConfiguration -Filter 'IPEnabled=True' | ForEach-Object {
  [ordered]@{ description = $_.Description; mac = $_.MACAddress; ip = (@($_.IPAddress) -join ', ')
              gateway = (@($_.DefaultIPGateway) -join ', '); dns = (@($_.DNSServerSearchOrder) -join ', '); dhcp = [bool]$_.DHCPEnabled } })
PKS 'nics' {
  $cfg = @{}; Get-CimInstance Win32_NetworkAdapterConfiguration | ForEach-Object { $cfg[[int]$_.Index] = $_ }
  # 2.31: Wake-on-LAN readiness per adapter – magic packet allowed, may wake the computer, driver settings,
  # wired (only cmdlets and powercfg, Constrained-Language-compatible)
  $wolPm = @{}; try { Get-NetAdapterPowerManagement -ErrorAction Stop | ForEach-Object { $wolPm[[string]$_.InterfaceDescription] = [string]$_.WakeOnMagicPacket } } catch {}
  # 2.32.8: Speed from Get-NetAdapter (asks the driver directly) – Win32_NetworkAdapter.Speed is outdated with some
  # drivers or reports 10 Mbit/s for disconnected adapters. Only for connected adapters.
  $wolMedia = @{}; $nSpeed = @{}; $nUp = @{}
  try { Get-NetAdapter -Physical -ErrorAction Stop | ForEach-Object { $k = [string]$_.InterfaceDescription
    $wolMedia[$k] = [string]$_.PhysicalMediaType; $nSpeed[$k] = $_.Speed; $nUp[$k] = ([string]$_.Status -eq 'Up') } } catch {}
  $wolAdv = @{}; try { Get-NetAdapterAdvancedProperty -ErrorAction Stop | Where-Object { $_.RegistryKeyword -match 'Wake|WOL|PME|S5' } | ForEach-Object {
    $k = [string]$_.InterfaceDescription; $wolAdv[$k] = @($wolAdv[$k]) + @(([string]$_.DisplayName) + ': ' + ([string]$_.DisplayValue)) } } catch {}
  $wolArmed = @(); try { $wolArmed = @(& powercfg.exe /devicequery wake_armed 2>$null | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ }) } catch {}
  $wolTxt = @{ Enabled = 'an'; Disabled = 'aus'; Unsupported = 'nicht unterstützt'; Inactive = 'inaktiv' }
  $states = @{ 0 = 'Getrennt'; 1 = 'Verbinde'; 2 = 'Verbunden'; 3 = 'Trenne'; 4 = 'Hardware fehlt'; 5 = 'Hardware deaktiviert'; 6 = 'Hardwarefehler'; 7 = 'Kein Kabel'; 8 = 'Authentifiziere'; 9 = 'Authentifiziert'; 10 = 'Fehlerhaft'; 11 = 'Ungültige Adresse'; 12 = 'Anmeldeinformationen erforderlich' }
  Get-CimInstance Win32_NetworkAdapter -Filter 'PhysicalAdapter=True' | ForEach-Object {
    $c = $cfg[[int]$_.Index]
    [ordered]@{ name = $_.Name; connection = $_.NetConnectionID; status = $states[[int]$_.NetConnectionStatus]; mac = $_.MACAddress
      speed = $(
        $sp = $null; $nk = [string]$_.Name
        if ($nSpeed.ContainsKey($nk)) { if ($nUp[$nk] -and $nSpeed[$nk]) { $sp = [int64]$nSpeed[$nk] } }
        elseif ([int]$_.NetConnectionStatus -eq 2 -and $_.Speed -and $_.Speed -lt 1e12) { $sp = [int64]$_.Speed }
        if ($sp -gt 0 -and $sp -lt 1e12) { $sp } else { $null }); manufacturer = $_.Manufacturer
      ip = (@($c.IPAddress) -join ', '); subnet = (@($c.IPSubnet) -join ', '); gateway = (@($c.DefaultIPGateway) -join ', ')
      dns = (@($c.DNSServerSearchOrder) -join ', '); dhcp = [bool]$c.DHCPEnabled; dhcp_server = $c.DHCPServer
      dns_suffix = $c.DNSDomain; enabled = $_.NetEnabled
      wired = $(if ($wolMedia.ContainsKey([string]$_.Name)) { $wolMedia[[string]$_.Name] -match '802\.3' } else { $_.Name -notmatch 'Wi-?Fi|Wireless|WLAN|802\.11|Bluetooth' })
      wol_magic = $(if ($wolPm.ContainsKey([string]$_.Name)) { $v = $wolPm[[string]$_.Name]; if ($wolTxt.ContainsKey($v)) { $wolTxt[$v] } else { $v } } else { $null })
      wake_armed = $(if ($wolArmed.Count) { $wolArmed -contains ([string]$_.Name) } else { $null })
      wol_driver = $(if ($wolAdv.ContainsKey([string]$_.Name)) { (@($wolAdv[[string]$_.Name] | Where-Object { $_ }) -join '; ') } else { $null }) } } }
PKX 'fast_startup' {
  # Windows Fast Startup (hybrid shutdown): many network adapters then do not wake from "Herunterfahren" (shutdown)
  $pol = Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\System' -ErrorAction SilentlyContinue
  $pw = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power' -ErrorAction SilentlyContinue
  $hb = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Power' -ErrorAction SilentlyContinue
  if ($hb -and $null -ne $hb.HibernateEnabled -and $hb.HibernateEnabled -eq 0) { $false }
  elseif ($pol -and $null -ne $pol.HiberbootEnabled) { $pol.HiberbootEnabled -eq 1 }
  elseif ($pw -and $null -ne $pw.HiberbootEnabled) { $pw.HiberbootEnabled -eq 1 }
  else { $null }
}
} catch { $r.errors += ('nics: ' + $_.Exception.Message) }

# @module printers | Drucker (lokal)
try {
PKS 'printers' { Get-CimInstance Win32_Printer | ForEach-Object { [ordered]@{
  name = $_.Name; driver = $_.DriverName; port = $_.PortName; shared = $_.Shared; share_name = $_.ShareName
  default = $_.Default; network = $_.Network; location = $_.Location; status = $_.PrinterStatus; comment = $_.Comment } } }
} catch { $r.errors += ('printers: ' + $_.Exception.Message) }

# @module processes | Prozesse
try {
PKS 'processes' {
  try { $pl = Get-Process -IncludeUserName -ErrorAction Stop } catch { $pl = Get-Process }
  $pl | Sort-Object ProcessName | ForEach-Object { [ordered]@{
    name = $_.ProcessName; pid = $_.Id; user = $_.UserName; memory = [int64]$_.WorkingSet64
    cpu_s = $(if ($_.CPU) { [int]($_.CPU * 10) / 10 } else { $null }); start = $(try { D $_.StartTime } catch { $null })
    path = $(try { $_.Path } catch { $null }); company = $(try { $_.Company } catch { $null }) } } }
} catch { $r.errors += ('processes: ' + $_.Exception.Message) }

# @module product_keys | Produktschlüssel
try {
PKS 'product_keys' {
  $l = @{ 0 = 'Nicht lizenziert'; 1 = 'Lizenziert'; 2 = 'OOB-Toleranz'; 3 = 'OOT-Toleranz'; 4 = 'Nicht-Original-Toleranz'; 5 = 'Benachrichtigung'; 6 = 'Erweiterte Toleranz' }
  $oa3 = (Get-CimInstance SoftwareLicensingService).OA3xOriginalProductKey
  if ($oa3) { [ordered]@{ product = 'Windows (OEM-Schlüssel im BIOS/UEFI)'; key = $oa3; channel = 'OEM'; status = '' } }
  Get-CimInstance SoftwareLicensingProduct -Filter 'PartialProductKey IS NOT NULL' | ForEach-Object { [ordered]@{
    product = $_.Name; key = ('XXXXX-XXXXX-XXXXX-XXXXX-' + $_.PartialProductKey); channel = $_.ProductKeyChannel
    status = $l[[int]$_.LicenseStatus]; description = $_.Description } } }
} catch { $r.errors += ('product_keys: ' + $_.Exception.Message) }

# @module security | Sicherheit (Defender, BitLocker, TPM, Firewall, Antivirus)
try {
# Defender status: the cmdlet (module "Defender"), otherwise directly the WMI class behind it – 2.33.4: on some Windows
# servers the module is missing (Get-MpComputerStatus unknown), the class MSFT_MpComputerStatus exists anyway
$mp = $null
try { $mp = Get-MpComputerStatus -ErrorAction Stop } catch {
  try { $mp = Get-CimInstance -Namespace 'root\Microsoft\Windows\Defender' -ClassName MSFT_MpComputerStatus -ErrorAction Stop } catch { $mp = $null } }
if ($mp) {
  $e.defender = [ordered]@{ enabled = $mp.AntivirusEnabled; realtime = $mp.RealTimeProtectionEnabled; signature = $mp.AntivirusSignatureVersion
                            signature_age = $mp.AntivirusSignatureAge; last_quick_scan = (D $mp.QuickScanEndTime) } }
try { $e.bitlocker = @(Get-BitLockerVolume -ErrorAction Stop | ForEach-Object {
  [ordered]@{ mount = $_.MountPoint; status = "$($_.VolumeStatus)"; protection = "$($_.ProtectionStatus)"; percent = $_.EncryptionPercentage } }) } catch {}
try { $t = Get-CimInstance -Namespace 'root\cimv2\security\microsofttpm' -ClassName Win32_Tpm -ErrorAction Stop
  if ($t) { $e.tpm = [ordered]@{ present = $true; enabled = $t.IsEnabled_InitialValue; version = $t.SpecVersion } } } catch {}
PKX 'firewall' { @(Get-NetFirewallProfile -ErrorAction Stop | ForEach-Object { [ordered]@{ profile = "$($_.Name)"; enabled = [bool]($_.Enabled -eq 'True' -or $_.Enabled -eq 1) } }) }
PKX 'antivirus' {
  # Workstations: the Windows Security Center knows every registered antivirus product
  $av = @()
  if ($os.ProductType -eq 1) {
    $av = @(Get-CimInstance -Namespace 'root\SecurityCenter2' -ClassName AntiVirusProduct -ErrorAction Stop | ForEach-Object {
      $st = [int]$_.productState
      [ordered]@{ name = $_.displayName; enabled = (($st -band 0x1000) -ne 0); up_to_date = (($st -band 0x10) -eq 0) } })
  }
  # 2.33.4: Windows Server has no Security Center (root\SecurityCenter2 is missing) – use Microsoft Defender directly there.
  # Previously an empty output arrived, which appeared as "undefined (aus)".
  if ($av.Count -eq 0 -and $mp) {
    $mode = "$($mp.AMRunningMode)"
    $nm = 'Microsoft Defender Antivirus'
    if ($mode -and $mode -ne 'Normal') { $nm = $nm + ' (' + $mode + ')' }
    $av = @([ordered]@{ name = $nm; enabled = [bool]($mp.AntivirusEnabled -and $mp.RealTimeProtectionEnabled)
                        up_to_date = [bool]($mp.AntivirusSignatureAge -le 7) })
  }
  ,$av   # always a list – also when empty (an empty output becomes "{}" in PowerShell 5.1 otherwise)
}
} catch { $r.errors += ('security: ' + $_.Exception.Message) }

# @module services | Dienste
try {
$r.services = @(Get-CimInstance Win32_Service | ForEach-Object {
  [ordered]@{ name = $_.Name; display = $_.DisplayName; state = $_.State; start = $_.StartMode; account = $_.StartName
              description = $_.Description; path = $_.PathName } })
} catch { $r.errors += ('services: ' + $_.Exception.Message) }

# @module shares | Freigaben
try {
PKS 'shares' {
  $t = @{ 0 = 'Datenträger'; 1 = 'Drucker'; 2 = 'Gerät'; 3 = 'IPC' }
  Get-CimInstance Win32_Share | ForEach-Object { [ordered]@{
    name = $_.Name; path = $_.Path; description = $_.Description; type = $t[[int]($_.Type -band 3)]; admin = (($_.Type -band 0x80000000) -ne 0) } } }
} catch { $r.errors += ('shares: ' + $_.Exception.Message) }

# @module features | Windows-Features
try {
PKS 'features' {
  $st = @{ 1 = 'Aktiviert'; 2 = 'Deaktiviert'; 3 = 'Nicht vorhanden'; 4 = 'Unbekannt' }
  if ($os.ProductType -ne 1) {
    Get-CimInstance Win32_ServerFeature -ErrorAction SilentlyContinue | ForEach-Object { [ordered]@{ name = $_.Name; caption = $_.Name; state = 'Installiert'; kind = 'Serverrolle' } }
  }
  Get-CimInstance Win32_OptionalFeature | Sort-Object Name | ForEach-Object { [ordered]@{
    name = $_.Name; caption = $_.Caption; state = $st[[int]$_.InstallState]; kind = 'Optionales Feature' } } }
} catch { $r.errors += ('features: ' + $_.Exception.Message) }

# @module tasks | Geplante Aufgaben
try {
PKS 'tasks' { Get-ScheduledTask -ErrorAction Stop | ForEach-Object {
  $t = $_; $i = $null
  if ($t.TaskPath -notlike '\Microsoft\*') { try { $i = $t | Get-ScheduledTaskInfo -ErrorAction Stop } catch {} }
  $act = (@($t.Actions) | ForEach-Object { (("" + $_.Execute) + ' ' + ("" + $_.Arguments)).Trim() }) -join ' | '
  [ordered]@{ path = $t.TaskPath; name = $t.TaskName; state = "$($t.State)"; author = $t.Author; run_as = $t.Principal.UserId
    action = $act
    last_run = $(if ($i) { D $i.LastRunTime } else { $null }); next_run = $(if ($i) { D $i.NextRunTime } else { $null })
    last_result = $(if ($i) { $i.LastTaskResult } else { $null }); microsoft = ($t.TaskPath -like '\Microsoft\*') } } }
} catch { $r.errors += ('tasks: ' + $_.Exception.Message) }

# @module disk_usage | Speicherplatz (Ordnergrößen als Diagramm)
# Special scanner: does NOT run in this combined script but as a separate pass (app/diskusage.py),
# because the data volume is large and the client is loaded more heavily. Deliberately no scan logic here.
