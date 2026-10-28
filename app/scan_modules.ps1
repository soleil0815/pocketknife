# POCKET KNIFE – Standard-Scanner. Jeder Abschnitt '# @module <schlüssel> | <bezeichnung>' ist ein eigener Scanner,
# der in Scanprofilen einzeln ausgewählt werden kann. '# @prelude' wird immer ausgeführt.
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
# (läuft serverseitig über LDAPS – kein PowerShell auf dem Client)

# @module computer | Computer-Details (Allgemein, Betriebssystem, System)
try {
$cpu = @(Get-CimInstance Win32_Processor)
# @sessions-begin (2.32.6: gleich in remote.SESSIONS_PS – der Selbsttest prüft das)
# Sitzungszustand je angemeldetem Benutzer: getrennt (Remotedesktop/Benutzerwechsel, quser) oder gesperrt (LogonUI.exe
# läuft in seiner Sitzung). Nur Cmdlets, Hashtables und quser.exe – läuft auch im eingeschränkten Sprachmodus.
$pkLui = @(Get-CimInstance Win32_Process -Filter "Name='LogonUI.exe'" | ForEach-Object { [int]$_.SessionId })
$pkQ = @{}
$pkQo = @()
try { $pkQo = @(& (Join-Path $env:SystemRoot 'System32\quser.exe') 2>$null) } catch {}
# ID steht rechtsbündig unter „ID“ (Kopfzeile in jeder Sprache) – ab dort lesen, Benutzer-/Sitzungsnamen davor egal
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
    # mehrere Sitzungen eines Benutzers: aktiv vor gesperrt vor getrennt
    $rank = @{ active = 3; locked = 2; disconnected = 1 }
    if (-not $pkSt.ContainsKey($n) -or $rank[$s] -gt $rank[$pkSt[$n]]) { $pkSt[$n] = $s }
    $n
  } })
# @sessions-end
$pkUser = $cs.UserName
if (-not $pkUser) {
  # Bei Remotedesktop-Sitzungen bleibt Win32_ComputerSystem.UserName leer – angemeldete Benutzer über explorer.exe ermitteln
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
try { $ai = Get-AppxPackage -AllUsers -Name Microsoft.DesktopAppInstaller -ErrorAction Stop | Sort-Object Version -Descending | Select-Object -First 1
  if ($ai) { $e.winget = "$($ai.Version)" } } catch {}
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
  @{ p = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'; a = $(if ([Environment]::Is64BitOperatingSystem) { 'x64' } else { 'x86' }); s = 'Computer' },
  @{ p = 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'; a = 'x86'; s = 'Computer' }
)
foreach ($u in (Get-ChildItem Registry::HKEY_USERS | Where-Object { $_.PSChildName -match '^S-1-5-21-[\d-]+$' })) {
  $paths += @{ p = "Registry::HKEY_USERS\$($u.PSChildName)\Software\Microsoft\Windows\CurrentVersion\Uninstall\*"; a = ''; s = 'Benutzer' }
}
try {
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
function PKRegVal($k, $n) { $v = $k.GetValue($n); if ($null -eq $v) { '' } else { ("" + $v).Trim() } }
$sw = @()
try {
$sw = foreach ($x in $paths) {
  Get-ChildItem -Path ($x.p -replace '\\\*$', '') -ErrorAction SilentlyContinue | ForEach-Object {
    $k = $_
    $dn = PKRegVal $k 'DisplayName'
    if (-not $dn -or $k.GetValue('SystemComponent') -eq 1 -or $k.GetValue('ParentKeyName') -or (PKRegVal $k 'ReleaseType') -match 'Update|Hotfix') { return }
    $lw = $null
    if ($pkLw) { try { $lw = [PKReg]::LastWrite($k) } catch {} }
    $parts = $k.Name -split '\\', 2
    [ordered]@{ name = $dn; version = (PKRegVal $k 'DisplayVersion'); publisher = (PKRegVal $k 'Publisher')
                install_date = (PKRegVal $k 'InstallDate'); key = $k.PSChildName; arch = $x.a; scope = $x.s
                uninstall = (PKRegVal $k 'UninstallString'); quiet = (PKRegVal $k 'QuietUninstallString'); modify = (PKRegVal $k 'ModifyPath')
                location = (PKRegVal $k 'InstallLocation'); size_kb = $k.GetValue('EstimatedSize'); modified = $lw
                hive = $parts[0]; reg_path = $(if ($parts.Count -gt 1) { $parts[1] } else { '' })
                no_remove = [bool]($k.GetValue('NoRemove') -eq 1) } } }
} catch { $r.software_error = $_.Exception.Message }
$r.software = @($sw)
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
PKS 'displays' { Get-CimInstance -Namespace root\wmi -ClassName WmiMonitorID -ErrorAction Stop | ForEach-Object { [ordered]@{
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
# Schattenkopien, Legacy-Treiber und Software-Geräte sind keine Hardware (bei VMs oft hunderte Einträge)
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
$admins = @()
try {
  $grp = Get-CimInstance Win32_Group -Filter "LocalAccount=True AND SID='S-1-5-32-544'"
  $g = [ADSI]("WinNT://$env:COMPUTERNAME/" + $grp.Name + ",group")
  $admins = @($g.psbase.Invoke('Members') | ForEach-Object {
    $p = $_.GetType().InvokeMember('ADsPath', 'GetProperty', $null, $_, $null)
    $c = $_.GetType().InvokeMember('Class', 'GetProperty', $null, $_, $null)
    [ordered]@{ name = (($p -replace '^WinNT://', '') -replace '/', '\'); type = $c } })
} catch {}
$r.admins = $admins
PKS 'local_groups' { Get-CimInstance Win32_Group -Filter 'LocalAccount=True' | ForEach-Object {
  $grp = $_; $m = @()
  try {
    $g = [ADSI]("WinNT://$env:COMPUTERNAME/" + $grp.Name + ',group')
    $m = @($g.psbase.Invoke('Members') | ForEach-Object { (($_.GetType().InvokeMember('ADsPath', 'GetProperty', $null, $_, $null) -replace '^WinNT://', '') -replace '/', '\') })
  } catch {}
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
  # 2.31: Wake-on-LAN-Bereitschaft je Karte – Magic Packet erlaubt, darf den Computer wecken, Treiber-Einstellungen,
  # kabelgebunden (nur cmdlets und powercfg, Constrained-Language-tauglich)
  $wolPm = @{}; try { Get-NetAdapterPowerManagement -ErrorAction Stop | ForEach-Object { $wolPm[[string]$_.InterfaceDescription] = [string]$_.WakeOnMagicPacket } } catch {}
  $wolMedia = @{}; try { Get-NetAdapter -Physical -ErrorAction Stop | ForEach-Object { $wolMedia[[string]$_.InterfaceDescription] = [string]$_.PhysicalMediaType } } catch {}
  $wolAdv = @{}; try { Get-NetAdapterAdvancedProperty -ErrorAction Stop | Where-Object { $_.RegistryKeyword -match 'Wake|WOL|PME|S5' } | ForEach-Object {
    $k = [string]$_.InterfaceDescription; $wolAdv[$k] = @($wolAdv[$k]) + @(([string]$_.DisplayName) + ': ' + ([string]$_.DisplayValue)) } } catch {}
  $wolArmed = @(); try { $wolArmed = @(& powercfg.exe /devicequery wake_armed 2>$null | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ }) } catch {}
  $wolTxt = @{ Enabled = 'an'; Disabled = 'aus'; Unsupported = 'nicht unterstützt'; Inactive = 'inaktiv' }
  $states = @{ 0 = 'Getrennt'; 1 = 'Verbinde'; 2 = 'Verbunden'; 3 = 'Trenne'; 4 = 'Hardware fehlt'; 5 = 'Hardware deaktiviert'; 6 = 'Hardwarefehler'; 7 = 'Kein Kabel'; 8 = 'Authentifiziere'; 9 = 'Authentifiziert'; 10 = 'Fehlerhaft'; 11 = 'Ungültige Adresse'; 12 = 'Anmeldeinformationen erforderlich' }
  Get-CimInstance Win32_NetworkAdapter -Filter 'PhysicalAdapter=True' | ForEach-Object {
    $c = $cfg[[int]$_.Index]
    [ordered]@{ name = $_.Name; connection = $_.NetConnectionID; status = $states[[int]$_.NetConnectionStatus]; mac = $_.MACAddress
      speed = $(if ($_.Speed -and $_.Speed -lt 1e12) { [int64]$_.Speed } else { $null }); manufacturer = $_.Manufacturer
      ip = (@($c.IPAddress) -join ', '); subnet = (@($c.IPSubnet) -join ', '); gateway = (@($c.DefaultIPGateway) -join ', ')
      dns = (@($c.DNSServerSearchOrder) -join ', '); dhcp = [bool]$c.DHCPEnabled; dhcp_server = $c.DHCPServer
      dns_suffix = $c.DNSDomain; enabled = $_.NetEnabled
      wired = $(if ($wolMedia.ContainsKey([string]$_.Name)) { $wolMedia[[string]$_.Name] -match '802\.3' } else { $_.Name -notmatch 'Wi-?Fi|Wireless|WLAN|802\.11|Bluetooth' })
      wol_magic = $(if ($wolPm.ContainsKey([string]$_.Name)) { $v = $wolPm[[string]$_.Name]; if ($wolTxt.ContainsKey($v)) { $wolTxt[$v] } else { $v } } else { $null })
      wake_armed = $(if ($wolArmed.Count) { $wolArmed -contains ([string]$_.Name) } else { $null })
      wol_driver = $(if ($wolAdv.ContainsKey([string]$_.Name)) { (@($wolAdv[[string]$_.Name] | Where-Object { $_ }) -join '; ') } else { $null }) } } }
PKX 'fast_startup' {
  # Windows-Schnellstart (Hybrid-Herunterfahren): viele Netzwerkkarten wecken dann aus „Herunterfahren“ nicht auf
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
    cpu_s = $(if ($_.CPU) { [math]::Round($_.CPU, 1) } else { $null }); start = $(try { D $_.StartTime } catch { $null })
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
try { $mp = Get-MpComputerStatus -ErrorAction Stop
  $e.defender = [ordered]@{ enabled = $mp.AntivirusEnabled; realtime = $mp.RealTimeProtectionEnabled; signature = $mp.AntivirusSignatureVersion
                            signature_age = $mp.AntivirusSignatureAge; last_quick_scan = (D $mp.QuickScanEndTime) } } catch {}
try { $e.bitlocker = @(Get-BitLockerVolume -ErrorAction Stop | ForEach-Object {
  [ordered]@{ mount = $_.MountPoint; status = "$($_.VolumeStatus)"; protection = "$($_.ProtectionStatus)"; percent = $_.EncryptionPercentage } }) } catch {}
try { $t = Get-CimInstance -Namespace 'root\cimv2\security\microsofttpm' -ClassName Win32_Tpm -ErrorAction Stop
  if ($t) { $e.tpm = [ordered]@{ present = $true; enabled = $t.IsEnabled_InitialValue; version = $t.SpecVersion } } } catch {}
PKX 'firewall' { @(Get-NetFirewallProfile -ErrorAction Stop | ForEach-Object { [ordered]@{ profile = "$($_.Name)"; enabled = [bool]($_.Enabled -eq 'True' -or $_.Enabled -eq 1) } }) }
PKX 'antivirus' {
  if ($os.ProductType -eq 1) {
    @(Get-CimInstance -Namespace 'root\SecurityCenter2' -ClassName AntiVirusProduct -ErrorAction Stop | ForEach-Object {
      $st = [int]$_.productState
      [ordered]@{ name = $_.displayName; enabled = (($st -band 0x1000) -ne 0); up_to_date = (($st -band 0x10) -eq 0) } })
  }
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
# Sonder-Scanner: läuft NICHT in diesem Sammel-Skript, sondern als eigener Durchlauf (app/diskusage.py),
# da die Datenmenge groß ist und der Client stärker belastet wird. Hier bewusst ohne Scan-Logik.
