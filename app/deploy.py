"""Software distribution (winget or own packages from the repository)."""
import base64
import datetime
import json
import re
import shlex
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import db, inventory, pklog, remote, util

WINGET_OK = {0: False, -1978335189: False, -1978335135: False, -1978334967: True}  # code -> restart needed
REBOOT_CODES = {3010, 1641}

_pool = None
_lock = threading.Lock()

PKG_FIELDS = ["name", "version", "description", "type", "winget_id", "winget_action", "winget_version",
              "winget_scope", "extra_args", "folder", "install_type", "install_file", "arguments", "script",
              "success_codes", "detect_name", "timeout_min", "reboot", "rescan",
              "steps", "conditions", "run_as", "tenant_id", "folder_name", "rescan_profile_id", "notify_user"]
JSON_FIELDS = ("steps", "conditions")


def row_to_package(row):
    """DB row -> package dict (JSON fields decoded)."""
    p = {k: row.get(k) for k in PKG_FIELDS}
    p["id"] = row.get("id")
    for k in JSON_FIELDS:
        if isinstance(p.get(k), str):
            try:
                p[k] = json.loads(p[k])
            except ValueError:
                p[k] = None
    return p


def pool():
    global _pool
    with _lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=max(1, db.setting("deploy_concurrency")), thread_name_prefix="deploy")
        return _pool


def validate_package(p):
    if not (p.get("name") or "").strip():
        raise ValueError("Name fehlt.")
    if p.get("steps"):
        return  # step packages are checked when saving (steps.clean_package)
    t = p.get("type")
    if t == "winget":
        if not util.WINGET_ID_RE.match(p.get("winget_id") or ""):
            raise ValueError("Ungültige winget-ID.")
        if p.get("winget_action") not in ("install", "upgrade", "uninstall", "bootstrap"):
            raise ValueError("Ungültige winget-Aktion.")
        if p.get("winget_version") and not util.WINGET_ID_RE.match(p["winget_version"]):
            raise ValueError("Ungültige Version.")
    elif t == "custom":
        it = p.get("install_type")
        if it not in ("msi", "msp", "msu", "exe", "cmd", "ps1", "reg", "script"):
            raise ValueError("Ungültiger Installationstyp.")
        if it == "script":
            if not (p.get("script") or "").strip():
                raise ValueError("PowerShell-Skript fehlt.")
        else:
            if not (p.get("folder") or "").strip() or not (p.get("install_file") or "").strip():
                raise ValueError("Paketordner und Installationsdatei angeben.")
            if ".." in p["folder"].replace("/", "\\").split("\\") or ".." in p["install_file"]:
                raise ValueError("Ungültiger Pfad.")
    else:
        raise ValueError("Ungültiger Pakettyp.")
    if p.get("reboot") not in (None, "", "never", "if_required", "always"):
        raise ValueError("Ungültige Neustart-Option.")


def adhoc_winget(wid, action="upgrade", scope=None):
    p = {"name": f"winget {action} {wid}", "type": "winget", "winget_id": wid, "winget_action": action,
         "winget_scope": scope if scope is not None else db.setting("winget_scope"), "reboot": "never", "rescan": 1,
         "timeout_min": db.setting("deploy_timeout_minutes")}
    validate_package(p)
    return p


# --- Script generation -----------------------------------------------------
WINGET_MIN_BUILD = 17763  # winget runs from Windows 10 1809 / Windows Server 2019


def winget_ok(os_build):
    """Does winget run on this build? Unknown (not scanned yet) counts as yes - the client checks itself (9010)."""
    try:
        b = int(str(os_build or "").strip().split(".")[0] or 0)  # the scan stores "14393.6897" (build.UBR)
    except ValueError:
        return True
    return b == 0 or b >= WINGET_MIN_BUILD

# Finds winget.exe; if it is missing, the App Installer (winget) with its dependencies is installed afterwards as a provisioned package -
# only from the repository folder (2.37: POCKET KNIFE downloads it there once, see wingetboot.py).
WINGET_FIND = r"""
$PKProxy = __PROXY__
function PK-WingetProxy {
  # Set the proxy from POCKET KNIFE as the winget administrator setting "DefaultProxy" - and remove it again only if we set it ourselves
  # Marker in HKLM (administrators/SYSTEM only) - not in %ProgramData%, where users may pre-create folders (Audit 2.29)
  $mark = 'HKLM:\SOFTWARE\PocketKnife'
  $old = $null
  try { $old = ('' + (Get-ItemProperty -LiteralPath $mark -Name WingetProxy -ErrorAction Stop).WingetProxy).Trim() } catch {}
  if ($null -eq $old) {
    # up to 2.28 the marker was a file - adopt it once (read only)
    $old = ''
    $f = Join-Path $env:ProgramData 'PocketKnife\winget-proxy.txt'
    if (Test-Path -LiteralPath $f -PathType Leaf) { $old = ('' + (Get-Content -LiteralPath $f -Raw -ErrorAction SilentlyContinue)).Trim() }
  }
  if ($PKProxy -eq $old) { return }
  # SYSTEM or elevated administrator (high/system integrity level) - without .NET (Constrained Language Mode, 2.38)
  if (-not (whoami.exe /groups | Select-String -SimpleMatch -Pattern 'S-1-16-12288', 'S-1-16-16384')) { return }
  if ($PKProxy) { $o = & $wg settings set DefaultProxy $PKProxy 2>&1 } else { $o = & $wg settings reset DefaultProxy 2>&1 }
  if ($LASTEXITCODE -eq 0) {
    if ($PKProxy) {
      if (-not (Test-Path -LiteralPath $mark)) { New-Item -Path $mark -Force | Out-Null }
      Set-ItemProperty -LiteralPath $mark -Name WingetProxy -Value $PKProxy
      Write-Output ('winget-Proxy eingestellt: ' + $PKProxy)
    } else {
      if (-not (Test-Path -LiteralPath $mark)) { New-Item -Path $mark -Force | Out-Null }
      Set-ItemProperty -LiteralPath $mark -Name WingetProxy -Value ''
      Write-Output 'winget-Proxy von POCKET KNIFE wieder entfernt.'
    }
  } else { Write-Output ('winget-Proxy nicht übernommen: ' + (($o | Out-String).Trim())) }
}
function PK-FindWinget {
  $c = @(Resolve-Path "$env:ProgramFiles\WindowsApps\Microsoft.DesktopAppInstaller_*_*__8wekyb3d8bbwe\winget.exe" -ErrorAction SilentlyContinue)
  if ($c.Count -gt 0) {
    return ($c | Sort-Object { try { [version](($_.Path -split '_')[1]) } catch { [version]'0.0' } } -Descending | Select-Object -First 1).Path
  }
  # Fallback via PATH only with a valid Microsoft signature - otherwise a planted winget.exe would run as SYSTEM
  # from a PATH folder writable by users (Audit 2.29)
  $g = Get-Command winget.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($g) {
    $sig = Get-AuthenticodeSignature -LiteralPath $g.Source -ErrorAction SilentlyContinue
    if ($sig -and "$($sig.Status)" -eq 'Valid' -and "$($sig.SignerCertificate.Subject)" -match 'O=Microsoft Corporation') { return $g.Source }
  }
  return $null
}
function PK-InstallWinget {
  $arch = switch ($env:PROCESSOR_ARCHITECTURE) { 'ARM64' { 'arm64' } 'x86' { 'x86' } default { 'x64' } }
  $src = Join-Path $PKDir 'winget'
  New-Item -ItemType Directory -Path $src -Force | Out-Null
  # 2.37: never from the internet per PC - POCKET KNIFE puts the App Installer into the repository once (offline source)
  if (-not (Get-ChildItem $src -Filter *.msixbundle -ErrorAction SilentlyContinue)) {
    throw 'Der App-Installer liegt noch nicht im Repository (Einstellungen → winget → Offline-Quelle: „Jetzt laden“).'
  }
  Write-Output 'winget fehlt – installiere den App-Installer aus dem Repository …'
  foreach ($z in @(Get-ChildItem $src -Filter *.zip -ErrorAction SilentlyContinue)) { Expand-Archive -Path $z.FullName -DestinationPath (Join-Path $src 'deps') -Force }
  $bundle = Get-ChildItem $src -Filter *.msixbundle | Select-Object -First 1
  if (-not $bundle) { throw 'Keine App-Installer-Datei (*.msixbundle) gefunden.' }
  $deps = @(Get-ChildItem $src -Recurse -Include *.appx, *.msix | Where-Object { $_.FullName -match ('\\' + $arch + '\\') -or $_.Name -match ('_' + $arch + '(_|\.)') })
  $lic = Get-ChildItem $src -Filter '*License*.xml' | Select-Object -First 1
  $ap = @{ Online = $true; PackagePath = $bundle.FullName }
  if ($deps) { $ap.DependencyPackagePath = @($deps.FullName) }
  if ($lic) { $ap.LicensePath = $lic.FullName } else { $ap.SkipLicense = $true }
  Add-AppxProvisionedPackage @ap | Out-Null
  Start-Sleep -Seconds 3
}
$wg = PK-FindWinget
if (-not $wg) {
  $pkBuild = [int](Get-CimInstance Win32_OperatingSystem).BuildNumber
  if ($pkBuild -lt __MINBUILD__) {
    Write-Output ('winget wird auf diesem Betriebssystem nicht unterstützt (Build ' + $pkBuild + '; nötig ist mindestens Windows 10 1809 bzw. Windows Server 2019).')
    $global:PKExit = 9010; return
  }
  if (__AUTO__) {
    try { PK-InstallWinget } catch { Write-Output ('winget konnte nicht nachinstalliert werden: ' + $_.Exception.Message) }
    $wg = PK-FindWinget
    if ($wg) { Write-Output ('winget nachinstalliert: ' + $wg) }
  }
}
if (-not $wg) { Write-Output 'winget.exe wurde nicht gefunden (App-Installer fehlt auf dem Computer).'; $global:PKExit = 9009; return }
PK-WingetProxy
"""


def _winget_find():
    from . import proxy
    return WINGET_FIND.replace("__MINBUILD__", str(WINGET_MIN_BUILD)) \
        .replace("__AUTO__", "$true" if db.setting("winget_bootstrap") else "$false") \
        .replace("__PROXY__", util.psq(proxy.client_proxy()))


def adhoc_bootstrap():
    """Ad-hoc package: only check winget and install it afterwards if needed."""
    return {"name": "winget prüfen / nachinstallieren", "type": "winget", "winget_id": "Microsoft.AppInstaller",
            "winget_action": "bootstrap", "reboot": "never", "rescan": 0, "run_as": "system",
            "timeout_min": db.setting("deploy_timeout_minutes")}


def _winget_body(p):
    if p.get("winget_action") == "bootstrap":
        return _winget_find() + "\nWrite-Output ('winget: ' + $wg)\n& $wg --version\n$global:PKExit = 0\n"
    args = [p["winget_action"], "--id", p["winget_id"], "--exact", "--source", "winget",
            "--accept-source-agreements", "--disable-interactivity"]
    if p["winget_action"] in ("install", "upgrade"):
        args += ["--silent", "--accept-package-agreements"]
        if p.get("winget_version"):
            args += ["--version", p["winget_version"]]
        scope = p.get("winget_scope")
        if scope in ("machine", "user"):
            args += ["--scope", scope]
    else:
        args += ["--silent"]
    # Quote fixed arguments if needed; append the additional arguments unchanged (their own quotation marks stay)
    argline = " ".join(a if " " not in a else f'"{a}"' for a in args)
    extra = (p.get("extra_args") or "").strip()
    if extra:
        argline += " " + extra
    act = p["winget_action"] in ("install", "upgrade")
    body = f"""
Set-Location (Split-Path $wg)
$pkA = {util.psq(argline)}
PK-Run $wg $pkA
"""
    if act and "--scope" in args:
        # 2.30.1/2.31: packages without a scope given in the winget catalog (e.g. MobaXterm) or installations whose
        # scope winget does not know (e.g. PowerShell 7 via MSI): with --scope winget refuses the update
        # ("does not apply" -1978335189 or "No installed package found" -1978335212) - by hand without --scope it works.
        # So retry once without --scope; all further attempts (tar.exe) also run without it.
        i = args.index("--scope")
        plain = args[:i] + args[i + 2:]
        pline = " ".join(a if " " not in a else f'"{a}"' for a in plain) + (" " + extra if extra else "")
        body += f"""
if ($global:PKExit -eq -1978335189 -or $global:PKExit -eq -1978335212) {{
  Write-Output 'Hinweis: keine passende Aktualisierung mit festgelegtem Bereich (--scope) – neuer Versuch ohne, so wie „winget upgrade“ von Hand (das Paket gibt im winget-Katalog keinen Bereich an).'
  $pkA = {util.psq(pline)}
  PK-Run $wg $pkA
}}
"""
    if act:
        body += """
# Detect Local System without .NET methods (Constrained Language Mode): the user name is then <computer>$
$pkSys = ($env:USERNAME -eq ($env:COMPUTERNAME + '$')) -or ($env:USERPROFILE -like '*\\systemprofile')
if ($global:PKExit -eq -1978335140 -and $pkSys) {
  # As Local System, extraction via the Windows shell often fails - retry once with tar.exe
  # As Local System, winget reads ...\\WinGet\\Settings\\defaultState\\settings.json (according to "winget settings export" on a client)
  Write-Output 'Hinweis: Archiv konnte nicht entpackt werden – neuer Versuch mit tar.exe (winget-Einstellung archiveExtractionMethod für Lokales System).'
  foreach ($d in @("$env:LOCALAPPDATA\\Packages\\Microsoft.DesktopAppInstaller_8wekyb3d8bbwe\\LocalState", "$env:LOCALAPPDATA\\Microsoft\\WinGet\\Settings\\defaultState")) {
    try {
      $f = Join-Path $d 'settings.json'
      $j = $null
      # Remove comment lines (// ..., as in winget's template) - Windows PowerShell 5.1 cannot read them
      if (Test-Path $f) { try { $j = ((Get-Content $f -Raw -ErrorAction Stop) -replace '(?m)^\\s*//.*$', '') | ConvertFrom-Json -ErrorAction Stop } catch { Write-Output ('Eigene winget-Einstellungen unverändert: ' + $f); continue } }
      if ($null -eq $j) { $j = New-Object PSObject }
      if (-not $j.installBehavior) { $j | Add-Member -NotePropertyName installBehavior -NotePropertyValue (New-Object PSObject) -Force }
      $j.installBehavior | Add-Member -NotePropertyName archiveExtractionMethod -NotePropertyValue 'tar' -Force
      New-Item -ItemType Directory -Force -Path $d | Out-Null
      # UTF-8 without BOM; in Constrained Language Mode (no .NET calls) with BOM via Set-Content
      if ($ExecutionContext.SessionState.LanguageMode -eq 'FullLanguage') { [IO.File]::WriteAllText($f, ($j | ConvertTo-Json -Depth 10), (New-Object Text.UTF8Encoding $false)) }
      else { Set-Content -LiteralPath $f -Value ($j | ConvertTo-Json -Depth 10) -Encoding UTF8 }
    } catch { Write-Output ('winget-Einstellung nicht geschrieben: ' + $_.Exception.Message) }
  }
  PK-Run $wg $pkA
}
if ($global:PKExit -eq -1978335189) {
  # "Keine passende Aktualisierung" (no applicable update) does not always mean "up to date": a newer version may exist, it just does not fit
  $pkT = ($global:PKOut | Out-String)
  if ($pkT -match 'install technology is different|Installationstechnologie unterscheidet sich') { $global:PKExit = 9011 }
  elseif ($pkT -match 'does not apply to your system|nicht f\\S{1,2}r Ihr System') { $global:PKExit = 9012 }
}
"""
    return _winget_find() + body


def _detect(p):
    name = (p.get("detect_name") or "").strip()
    if not name:
        return ""
    return f"""
$pkDet = Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*','HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*' -ErrorAction SilentlyContinue |
  Where-Object {{ $_.DisplayName -like {util.psq(name)} }} | Select-Object -First 1
if ($pkDet) {{
  $need = {util.psq(p.get('version') or '')}
  $have = "" + $pkDet.DisplayVersion
  $skip = $false
  if (-not $need) {{ $skip = $true }} else {{ try {{ $skip = ([version]$have -ge [version]$need) }} catch {{ $skip = ($have -eq $need) }} }}
  if ($skip) {{ Write-Output ("Bereits installiert: " + $pkDet.DisplayName + " " + $have + " - übersprungen."); $global:PKExit = 0; return }}
}}
"""


def _custom_body(p):
    it = p["install_type"]
    args = p.get("arguments") or ""
    body = _detect(p)
    if it == "script":
        body += "\n$sf = Join-Path $PKDir 'pk_script.ps1'\n" + util.ps_write("$sf", p["script"]) + f"PK-RunPs1 $sf {util.psq(args)}\n"
        return body
    body += f"$f = Join-Path $PKDir {util.psq(p['install_file'])}\n"
    body += "if (-not (Test-Path -LiteralPath $f)) { Write-Output ('Datei nicht gefunden: ' + $f); $global:PKExit = 2; return }\n"
    if it in ("msi", "msp", "msu"):
        args, has_log = clean_msi_args(args)
        a = util.psq(args)
        log = "" if has_log or it == "msu" else " + ' /l*v \"' + $PKMsiLog + '\"'"
        body += "$PKMsiLog = Join-Path $PKDir 'pk_msi.log'\n"
        if it == "msi":
            body += f"PK-Run 'msiexec.exe' (('/i \"' + $f + '\" /qn /norestart ' + {a}).Trim(){log})\n"
        elif it == "msp":
            body += f"PK-Run 'msiexec.exe' (('/p \"' + $f + '\" /qn /norestart ' + {a}).Trim(){log})\n"
        else:
            body += f"PK-Run 'wusa.exe' (('\"' + $f + '\" /quiet /norestart ' + {a}).Trim())\n"
        if it != "msu":
            body += MSI_LOG_TAIL
        return body
    a = util.psq(args)
    if it == "exe":
        body += f"PK-Run $f {a}\n"
    elif it == "cmd":
        body += f"PK-Run 'cmd.exe' ('/c \"\"' + $f + '\" ' + {a} + '\"')\n"
    elif it == "ps1":
        body += f"PK-RunPs1 $f {a}\n"
    elif it == "reg":
        # 64-bit reg.exe (also from a 32-bit PowerShell); unlike regedit /s, reg import returns an exit code
        body += ("$pkRegExe = Join-Path $env:windir 'Sysnative\\reg.exe'\n"
                 "if (-not (Test-Path $pkRegExe)) { $pkRegExe = Join-Path $env:windir 'System32\\reg.exe' }\n"
                 f"PK-Run $pkRegExe (('import \"' + $f + '\" ' + {a}).Trim())\n")
    return body


# Switches that POCKET KNIFE sets itself for MSI/MSP/MSU - duplicate entries in the "Parameter" field are removed
_MSI_DUP = re.compile(r"^/(q[nbrf]?[+-]?|quiet|passive|norestart|promptrestart|forcerestart)$", re.I)
MSI_LOG_TAIL = r"""
if ($global:PKExit -ne 0 -and $global:PKExit -ne 3010 -and $global:PKExit -ne 1641 -and (Test-Path $PKMsiLog)) {
  Write-Output '--- Auszug aus dem MSI-Protokoll ---'
  $ml = Get-Content $PKMsiLog -ErrorAction SilentlyContinue
  $hit = @($ml | Select-String -Pattern 'Return value 3|Error \d{4}|Fehler \d{4}' -Context 6,2 | Select-Object -First 3)
  if ($hit) { $hit | ForEach-Object { $_.Context.PreContext; $_.Line; $_.Context.PostContext } } else { $ml | Select-Object -Last 30 }
}
"""


def clean_msi_args(args):
    """Removes /qn, /quiet, /norestart ... from own parameters. Returns (args, has_own_log)."""
    try:
        toks = shlex.split(args or "", posix=False)
    except ValueError:
        toks = (args or "").split()
    out, has_log = [], False
    for t in toks:
        if _MSI_DUP.match(t):
            continue
        if re.match(r"^/l[*a-z!+]*$", t, re.I):
            has_log = True
        out.append(t)
    return " ".join(out), has_log


def command_preview(p):
    """Command line as it is executed on the client (for the display)."""
    if p.get("type") == "winget":
        b = _winget_body(p)
        m = re.search(r"PK-Run \$wg '(.*)'", b)
        return "winget.exe " + (m.group(1).replace("''", "'") if m else "")
    it, f = p.get("install_type"), (p.get("install_file") or "<datei>")
    path = f"C:\\Windows\\Temp\\PocketKnife\\<id>\\{f}"
    args = p.get("arguments") or ""
    if it in ("msi", "msp"):
        a, has_log = clean_msi_args(args)
        log = "" if has_log else ' /l*v "…\\pk_msi.log"'
        return f'msiexec.exe {"/i" if it == "msi" else "/p"} "{path}" /qn /norestart {a}'.rstrip() + log
    if it == "msu":
        a, _ = clean_msi_args(args)
        return f'wusa.exe "{path}" /quiet /norestart {a}'.rstrip()
    if it == "exe":
        return f'"{path}" {args}'.rstrip()
    if it == "cmd":
        return f'cmd.exe /c ""{path}" {args}"'
    if it == "ps1":
        return f'{util.PS_EXE} {util.PS_FLAGS} -File "{path}" {args}'.rstrip()
    if it == "reg":
        return f'reg.exe import "{path}" {args}'.rstrip()
    if it == "script":
        return "PowerShell-Skript (inline)"
    return ""


def build_body(p):
    return _winget_body(p) if p["type"] == "winget" else _custom_body(p)


def evaluate(p, code):
    """(success, restart_needed)"""
    if p["type"] == "winget":
        if code in WINGET_OK:
            return True, WINGET_OK[code]
        if code in REBOOT_CODES:
            return True, True
        return False, False
    codes = set()
    for x in (p.get("success_codes") or "0,3010,1641").split(","):
        x = x.strip()
        if x.lstrip("-").isdigit():
            codes.add(int(x))
    return code in codes, code in REBOOT_CODES


WINGET_MESSAGES = {
    -1978335189: "Keine passende Aktualisierung (bereits aktuell)",
    -1978335135: "Paket ist bereits installiert",
    -1978334967: "Neustart erforderlich, um die Installation abzuschließen",
    -1978335212: "Kein Paket mit dieser ID gefunden",
    -1978335216: "Kein passender Installer gefunden (Architektur/Scope)",
    -1978335226: "Installer fehlgeschlagen",
    -1978335230: "Ungültige Argumente für winget",
    -1978334975: "Anwendung läuft gerade und blockiert die Installation",
    -1978335140: "Archiv des Installers konnte nicht entpackt werden",
    -1978335215: "Prüfsumme des Installers passt nicht – der Hersteller hat die Datei geändert, der winget-Katalog ist noch "
                 "nicht aktualisiert (meist nach ein bis zwei Tagen behoben)",
    -1978334974: "Eine andere Installation läuft gerade (Windows Installer belegt, 1618) – später erneut versuchen",
    9009: "winget nicht gefunden (App-Installer fehlt, Nachinstallation nicht möglich)",
    9010: "Übersprungen – winget wird auf diesem Betriebssystem nicht unterstützt",
    # 2.31: assigned by POCKET KNIFE when winget reports "keine passende Aktualisierung" (no applicable update) although a newer one exists
    9011: "Neuere Version vorhanden, aber mit anderer Installationstechnik (z. B. MSI ↔ EXE/MSIX) – winget aktualisiert "
          "das nicht. Abhilfe: im Paket erst einen Schritt „Deinstallieren“, dann „Installieren“ – oder bei „Zusätzliche "
          "Argumente“ --force (installiert die neue Version daneben, die alte bleibt)",
    9012: "Neuere Version vorhanden, passt aber nicht zu diesem System (Architektur, Windows-Version oder andere "
          "Voraussetzungen im winget-Katalog)",
}


# 2.32.2: assigned by the wrapper on the client when Windows prevented the execution (hardened system) - for all
# step kinds; the original exit code and Windows' message are in the log
HARD_MESSAGES = {
    9013: "Windows lässt ein PowerShell-Skript nicht zu (Ausführungsrichtlinie/Signatur) – z. B. ein Skript, das dieses "
          "Skript selbst aufruft. Ausführungsrichtlinie der GPO für diesen Computer prüfen oder das Skript signieren",
    9014: "Vom Virenschutz blockiert (AMSI: „schädlicher Inhalt“). Skript und Meldung im Virenschutz prüfen",
    9015: "Windows führt PowerShell hier im eingeschränkten Sprachmodus aus (AppLocker/WDAC); dort sind die benötigten "
          "Funktionen gesperrt. Den Arbeitsordner C:\\Windows\\Temp\\PocketKnife in der Richtlinie zulassen oder den "
          "Computer von dieser Härtung ausnehmen",
    9016: "Windows hat ein Programm gesperrt (AppLocker oder Softwareeinschränkung). Programm bzw. Ordner in der "
          "Richtlinie zulassen – welches, steht im Protokoll",
}


# --- Flow ----------------------------------------------------------------
def tenant_members(tid, strict=False):
    """Computers a tenant may reach: set - or None = no restriction.
    Safety rules: tenant deleted -> no computers; tenant that ever had OU admins, without a (valid) collection ->
    no computers (its packages/schedules must never run "everywhere")."""
    from . import collections_, profiles
    from . import access
    t = db.q1("SELECT collection_id, collection_ids, COALESCE(ou_ever, 0) ou_ever, admin_group FROM tenants WHERE id=?", (int(tid),))
    if not t:
        return set()
    cols = access.tenant_cols(t)
    if not cols or profiles.ALL_COMPUTERS in cols:
        return set() if (t["ou_ever"] or t["admin_group"]) else None
    out = set()
    for col in cols:  # 2.29.9: several collections - together
        try:
            out.update(collections_.member_ids(int(col)))
        except Exception:
            if strict:
                raise
    return out


def tenant_filter(pkg, ids, strict=False):
    """Distribute a tenant's packages (also nested ones) only to computers of the tenant collection."""
    from . import collections_, steps
    tenants = set()

    def walk(p):
        if p.get("tenant_id"):
            tenants.add(int(p["tenant_id"]))
        for _k, s in steps.iter_steps(steps.normalize(p)):
            if s.get("type") == "nested" and s.get("package"):
                walk(s["package"])
    walk(pkg)
    allowed = None
    for t in tenants:
        mem = tenant_members(t, strict)
        if mem is not None:
            allowed = mem if allowed is None else allowed & mem
    if allowed is None:
        return ids, 0
    keep = [i for i in ids if i in allowed]
    return keep, len(ids) - len(keep)


def composite(package_ids, stop_on_fail=True):
    """Several saved packages as ONE deployment: per computer one after another in this order
    (nested steps). This way two installations never run at the same time on one computer
    (msiexec otherwise reports 1618 "another installation is running")."""
    from . import steps
    ids, seen = [], set()
    for pid in package_ids:
        pid = int(pid)
        if pid not in seen:
            seen.add(pid)
            ids.append(pid)
    rows = {r["id"]: r for r in db.q(f"SELECT id, name, tenant_id, rescan, rescan_profile_id, reboot FROM packages "
                                     f"WHERE id IN ({','.join('?' * len(ids))})", tuple(ids))} if ids else {}
    missing = [i for i in ids if i not in rows]
    if missing or not ids:
        raise ValueError("Paket nicht gefunden." if ids else "Keine Pakete ausgewählt.")
    tenants = {rows[i]["tenant_id"] for i in ids}
    if len(tenants) > 1:
        raise ValueError("Pakete verschiedener Mandanten lassen sich nicht zusammen bereitstellen.")
    names = [rows[i]["name"] for i in ids]
    label = f"{len(ids)} Pakete: " + ", ".join(names)
    main = [{"id": f"p{n}", "type": "nested", "package_id": pid, "title": "", "enabled": True, "run_as": "inherit",
             "on_error": "stop" if stop_on_fail else "continue", "timeout_min": 0, "conditions": steps.default_conditions()}
            for n, pid in enumerate(ids)]
    return {"name": label[:200], "version": "", "run_as": "deploy", "rescan": 1 if any(rows[i]["rescan"] for i in ids) else 0,
            "reboot": strongest_reboot(rows[i]["reboot"] for i in ids), "composite": True,
            "timeout_min": db.setting("deploy_timeout_minutes"), "conditions": steps.default_conditions(),
            "steps": {"pre": [], "main": main, "post": []}, "tenant_id": tenants.pop()}


def rescan_profiles(p):
    """2.32: Which scan profiles are used to scan after the deployment - [] = do not scan.
    One package: its profile ("Danach scannen" (scan afterwards), default "Anwendungen"). Several packages in one deployment or
    schedule (composite): the profile of each individual package, "Nicht scannen" counts per package; each profile once."""
    from . import profiles, steps

    def one(rescan, pid):
        if not rescan:
            return None
        return pid if pid and profiles.get(pid) else profiles.apps_profile_id()
    if not p.get("composite"):
        x = one(p.get("rescan", 1), p.get("rescan_profile_id"))
        return [x] if x else []
    out = []
    for _k, st in steps.iter_steps(steps.normalize(p)):
        if st.get("type") != "nested" or not st.get("package_id") or st.get("enabled") is False:
            continue
        r = db.q1("SELECT rescan, rescan_profile_id FROM packages WHERE id=?", (st["package_id"],))
        x = one(r["rescan"] if r and r["rescan"] is not None else 1, r["rescan_profile_id"] if r else None)
        if x and x not in out:
            out.append(x)
    return out


def strongest_reboot(values):
    """Restart setting of several packages combined: "immer" (always) before "wenn nötig" (if needed) before "nie" (never) -
    a package that restarts on its own also does so within a schedule or a multi-selection."""
    vals = {v or "never" for v in values}
    return "always" if "always" in vals else ("if_required" if "if_required" in vals else "never")


def create(pkg, computer_ids, user, scheduled_at=None, source="manual", rule_id=None, name=None, package_id=None,
           schedule_id=None, start_now=True, target_label=None):
    from . import integrity, steps
    validate_package(pkg)
    pkg = steps.expand(pkg)
    ids = sorted({int(i) for i in computer_ids})
    if not ids:
        raise ValueError("Keine Zielcomputer ausgewählt.")
    # 2.26: check repository files against the confirmed state - break -> create nothing, start nothing
    chk = integrity.guard(pkg, package_id=package_id, name=name)
    if chk.get("state") == "warn":
        pklog.info("deploy", f"„{name or pkg['name']}“: Abweichung im Repository (Lauf geht weiter) – {integrity.summary(chk)}.", user=user)
    ids, ignored = tenant_filter(pkg, ids)
    if not ids:
        raise ValueError("Keiner der Zielcomputer gehört zum Mandanten dieses Pakets.")
    if ignored:
        pklog.info("deploy", f"„{name or pkg['name']}“: {ignored} Computer gehören nicht zum Mandanten und wurden ausgelassen.",
                   user=user)
    now = util.now()
    sched = scheduled_at if scheduled_at and scheduled_at > now else None
    with db.tx():
        did = db.ex("""INSERT INTO deployments(name, package_id, package_json, source, rule_id, status, created_by,
                       created_at, scheduled_at, schedule_id, tenant_id, target_label) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (name or pkg["name"], package_id, json.dumps(pkg), source, rule_id,
                     "scheduled" if sched else "queued", user, now, sched, schedule_id, pkg.get("tenant_id"),
                     (target_label or "")[:300] or None))
        for cid in ids:
            cname = db.val("SELECT name FROM computers WHERE id=?", (cid,), "?")
            db.ex("INSERT INTO deployment_targets(deployment_id, computer_id, computer_name, status) VALUES(?,?,?,'queued')",
                  (did, cid, cname))
    pklog.info("deploy", f"Bereitstellung #{did} '{name or pkg['name']}' für {len(ids)} Computer "
                         f"{'geplant für ' + sched if sched else 'gestartet'}.", user=user)
    if not sched and start_now:
        start(did, checked=True)
    return did


def start(did, checked=False):
    """checked: just checked by create(). Otherwise (scheduled deployment, schedule) check the
    repository files before starting - on a break the run is interrupted before any computer does anything."""
    if not checked:
        d = db.q1("SELECT * FROM deployments WHERE id=? AND status IN ('queued','scheduled')", (did,))
        if d:
            from . import integrity
            try:
                integrity.guard(json.loads(d["package_json"] or "{}"), package_id=d.get("package_id"), name=d["name"])
            except integrity.IntegrityError as e:
                _interrupt(did, str(e))
                return
    _rescope(did)
    # Only if not aborted yet - otherwise an aborted deployment would stay on "läuft" (running) forever
    if db.ex_count("UPDATE deployments SET status='running', started_at=COALESCE(started_at, ?) WHERE id=? "
                   "AND status IN ('queued','scheduled','running')", (util.now(), did)) != 1:
        return
    for t in db.q("SELECT id FROM deployment_targets WHERE deployment_id=? AND status='queued'", (did,)):
        pool().submit(_run_target_safe, did, t["id"])


def _rescope(did):
    """Audit 2.29: Apply the tenant scope again at START (not only when creating) - a computer that has left the
    tenant collection since planning no longer gets the tenant package."""
    d = db.q1("SELECT package_json FROM deployments WHERE id=?", (did,))
    if not d:
        return
    try:
        pkg = json.loads(d["package_json"] or "{}")
    except ValueError:
        return
    rows = db.q("SELECT id, computer_id FROM deployment_targets WHERE deployment_id=? AND status='queued'", (did,))
    try:
        # strict: a brief disturbance (database locked) must not leave out all targets as "no longer in the tenant"
        keep, dropped = tenant_filter(pkg, [r["computer_id"] for r in rows if r["computer_id"]], strict=True)
    except Exception as e:  # noqa: BLE001
        pklog.warn("deploy", f"Bereitstellung #{did}: Mandantenbereich beim Start nicht prüfbar ({e}) – Ziele wie beim Anlegen.")
        return
    if not dropped:
        return
    keep = set(keep)
    now = util.now()
    for r in rows:
        if r["computer_id"] not in keep:
            db.ex("UPDATE deployment_targets SET status='skipped', message=?, finished_at=? WHERE id=? AND status='queued'",
                  ("Gehört nicht (mehr) zum Mandanten des Pakets – ausgelassen", now, r["id"]))
    pklog.info("deploy", f"Bereitstellung #{did}: {dropped} Computer gehören nicht mehr zum Mandanten und wurden ausgelassen.")


def restart_stuck(minutes=3):
    """Audit 2.29: Deployments created as "wartend" (waiting) whose start failed (e.g. database briefly locked) stayed
    until the next restart - and blocked their computers for all schedules. Now the tick picks them up."""
    old = util.ago(minutes=minutes)
    for d in db.q("""SELECT d.id FROM deployments d WHERE d.status='queued' AND d.created_at <= ? AND EXISTS(
                     SELECT 1 FROM deployment_targets t WHERE t.deployment_id = d.id AND t.status='queued')""", (old,)):
        start(d["id"])


def _interrupt(did, msg):
    """Interrupt a deployment because of a break in the repository: no target is touched."""
    now = util.now()
    if db.ex_count("UPDATE deployments SET status='failed', started_at=COALESCE(started_at, ?), finished_at=? "
                   "WHERE id=? AND status IN ('queued','scheduled')", (now, now, did)) != 1:
        return
    tids = [r["id"] for r in db.q("SELECT id FROM deployment_targets WHERE deployment_id=? AND status='queued'", (did,))]
    from . import integrity
    # The target shows a neutral text (OU admins and kiosk users see it too) - the files only in the log without a computer
    db.ex("UPDATE deployment_targets SET status='failed', message=?, finished_at=? WHERE deployment_id=? AND status='queued'",
          (integrity.NEUTRAL, now, did))
    for tid in tids:
        _after_target(did, tid)
    pklog.warn("deploy", f"Bereitstellung #{did}: {msg}")


def cancel(did, user=None):
    db.ex("UPDATE deployments SET status='cancelled', finished_at=? WHERE id=? AND status IN ('queued','scheduled','running')",
          (util.now(), did))
    queued = [r["id"] for r in db.q("SELECT id FROM deployment_targets WHERE deployment_id=? AND status='queued'", (did,))]
    db.ex("UPDATE deployment_targets SET status='cancelled', message='Abgebrochen', finished_at=? "
          "WHERE deployment_id=? AND status='queued'", (util.now(), did))
    for tid in queued:
        _after_target(did, tid)
    pklog.info("deploy", f"Bereitstellung #{did} abgebrochen.", user=user)


# --- 2.35.1: Retry with the current state of the package --------------------------------
def package_changed(d):
    """Modified date of the package if it was changed since this deployment - otherwise None."""
    if not d or not d.get("package_id"):
        return None
    r = db.q1("SELECT updated_at FROM packages WHERE id=?", (d["package_id"],))
    if not r or not r["updated_at"] or r["updated_at"] <= (d.get("created_at") or ""):
        return None
    return r["updated_at"]


def source_package(d, current=False):
    """Package for a retry: the state of this deployment (default) - or, with current, the current state
    of the package if it was changed since. If the deployment itself was already a slice (individual steps
    retried), it stays a slice: from the current package only these steps."""
    from . import steps
    snap = json.loads(d["package_json"] or "{}")
    if not current or not package_changed(d):
        return snap
    row = db.q1("SELECT * FROM packages WHERE id=?", (d["package_id"],))
    cur = steps.normalize(row_to_package(row))
    # Slice: marked explicitly (2.35.5, "slice") - older deployments recognized by name
    if snap.get("slice") or RETRY_SUFFIX.search(d.get("name") or ""):
        # what was enabled in the slice stays enabled (disabled steps do not suddenly run too)
        keep = {st.get("id"): st.get("enabled", True) is not False for _ph, st in steps.iter_steps(steps.normalize(snap))}
        cur = dict(cur, steps={ph: [dict(st, enabled=keep[st.get("id")]) for st in cur["steps"].get(ph) or [] if st.get("id") in keep]
                               for ph, _l in steps.PHASES}, reboot=snap.get("reboot", cur.get("reboot")), slice=True)
        if not any(cur["steps"].values()):
            raise ValueError("Die wiederholten Schritte gibt es im geänderten Paket nicht mehr – bitte das Paket neu "
                             "bereitstellen.")
    return cur


def redeploy(did, failed_only, user, current=False):
    d = db.q1("SELECT * FROM deployments WHERE id=?", (did,))
    if not d:
        raise ValueError("Bereitstellung nicht gefunden")
    sql = "SELECT computer_id FROM deployment_targets WHERE deployment_id=? AND computer_id IS NOT NULL"
    if failed_only:
        sql += " AND status NOT IN ('success')"
    ids = [r["computer_id"] for r in db.q(sql, (did,))]
    return create(source_package(d, current), ids, user, name=d["name"], package_id=d["package_id"],
                  target_label=d.get("target_label"))


# --- Retry individual steps ------------------------------------------------------
def _top_records(recs):
    """Results of the top level (without the steps of nested packages)."""
    out = []
    for r in recs:
        depth = r.get("depth")
        if depth is None:  # results of older versions: indentation of the title
            t = r.get("title") or ""
            depth = (len(t) - len(t.lstrip(" "))) // 2
        if depth == 0:
            out.append(r)
    return out


def failed_steps(did):
    """Failed steps of a deployment with the affected targets (for "Schritt wiederholen" (retry step))."""
    from . import steps
    d = db.q1("SELECT * FROM deployments WHERE id=?", (did,))
    if not d:
        raise ValueError("Bereitstellung nicht gefunden")
    p = steps.normalize(json.loads(d["package_json"] or "{}"))
    top = list(steps.iter_steps(p))
    by_id = {st.get("id"): (ph, st) for ph, st in top}
    out = {}
    for t in db.q("SELECT id, computer_id, computer_name, steps FROM deployment_targets WHERE deployment_id=?", (did,)):
        try:
            recs = _top_records(json.loads(t["steps"] or "[]"))
        except ValueError:
            continue
        for i, r in enumerate(recs):
            if r.get("status") != "failed":
                continue
            sid = r.get("sid") or (top[i][1].get("id") if i < len(top) else None)
            if sid not in by_id or not t["computer_id"]:
                continue
            ph, st = by_id[sid]
            e = out.setdefault(sid, {"sid": sid, "phase": ph, "title": steps.step_title(st), "type": st["type"],
                                     "index": [x[1].get("id") for x in top].index(sid), "targets": []})
            e["targets"].append({"tid": t["id"], "computer_id": t["computer_id"], "computer": t["computer_name"],
                                 "message": r.get("message") or ""})
    return sorted(out.values(), key=lambda x: x["index"])


def retry_package(p, sid, mode):
    """Shorten the package snapshot to one step (mode=step) or from this step on (mode=from)."""
    from . import steps
    p = steps.normalize(p)
    order = [k for k, _ in steps.PHASES]
    pos = next(((ph, i) for ph in order for i, st in enumerate(p["steps"][ph]) if st.get("id") == sid), None)
    if not pos:
        raise ValueError("Schritt nicht gefunden")
    ph, i = pos
    st = p["steps"][ph][i]
    new = {k: [] for k in order}
    if mode == "step":
        new[ph] = [dict(st, enabled=True)]
    else:
        for k in order[order.index(ph):]:
            new[k] = list(p["steps"][k][i:] if k == ph else p["steps"][k])
        new[ph][0] = dict(new[ph][0], enabled=True)
    p = dict(p, steps=new, slice=True)  # 2.35.5: slice marked explicitly (not only recognizable by name)
    return p, steps.step_title(st)


def retry_step(did, sid, mode, target_ids, user, current=False):
    d = db.q1("SELECT * FROM deployments WHERE id=?", (did,))
    if not d:
        raise ValueError("Bereitstellung nicht gefunden")
    if d["status"] in ("running", "queued", "scheduled"):
        raise ValueError("Die Bereitstellung läuft noch – bitte warten oder abbrechen.")
    mode = "from" if mode == "from" else "step"
    try:
        pkg, title = retry_package(source_package(d, current), sid, mode)
    except ValueError as e:
        if current and str(e) == "Schritt nicht gefunden":
            raise ValueError("Diesen Schritt gibt es im geänderten Paket nicht mehr – „Schritte wählen …“ zeigt die "
                             "aktuellen Schritte.")
        raise
    failed = {x["tid"]: x["computer_id"] for f in failed_steps(did) if f["sid"] == sid for x in f["targets"]}
    if target_ids:
        rows = db.q(f"SELECT id, computer_id FROM deployment_targets WHERE deployment_id=? AND id IN "
                    f"({','.join('?' * len(target_ids))})", (did, *[int(x) for x in target_ids]))
        ids = [r["computer_id"] for r in rows if r["computer_id"]]
    else:
        ids = list(failed.values())
    if not ids:
        raise ValueError("Keine Computer, auf denen dieser Schritt fehlgeschlagen ist.")
    label = f"„{title}“ wiederholt" if mode == "step" else f"ab „{title}“ wiederholt"
    base = RETRY_SUFFIX.sub("", d["name"])
    new = create(pkg, ids, user, name=f"{base} – {label}"[:200], package_id=d["package_id"])
    pklog.info("deploy", f"Bereitstellung #{did}: Schritt {label} auf {len(ids)} Computer(n) → #{new}.", user=user)
    return new


def steps_overview(did, allowed=None, current=False):
    """Top-level steps of a deployment with the result per target (for "Schritte wählen ..." (choose steps)).
    allowed: only these computers count (OU admins). current: steps of the current package (2.35.1) - the results
    are assigned via the step ID (new steps do not have any yet)."""
    from . import steps
    d = db.q1("SELECT * FROM deployments WHERE id=?", (did,))
    if not d:
        raise ValueError("Bereitstellung nicht gefunden")
    top = list(steps.iter_steps(steps.normalize(json.loads(d["package_json"] or "{}"))))  # for old results without sid
    p = steps.normalize(source_package(d, current))
    out = [{"sid": st.get("id"), "phase": ph, "title": steps.step_title(st), "type": st.get("type"),
            "enabled": st.get("enabled", True) is not False, "counts": {}} for ph, st in steps.iter_steps(p)]
    by_id = {x["sid"]: x for x in out}
    targets = []
    for t in db.q("SELECT id, computer_id, computer_name, status, steps FROM deployment_targets WHERE deployment_id=? "
                  "ORDER BY computer_name", (did,)):
        if allowed is not None and t["computer_id"] not in allowed:
            continue
        targets.append({"tid": t["id"], "computer_id": t["computer_id"], "computer": t["computer_name"], "status": t["status"]})
        try:
            recs = _top_records(json.loads(t["steps"] or "[]"))
        except ValueError:
            recs = []
        seen = set()
        for i, r in enumerate(recs):
            sid = r.get("sid") or (top[i][1].get("id") if i < len(top) else None)
            if sid in by_id and sid not in seen:
                seen.add(sid)
                k = r.get("status") or "notrun"
                by_id[sid]["counts"][k] = by_id[sid]["counts"].get(k, 0) + 1
    return {"steps": out, "targets": targets, "status": d["status"]}


# Suffix of earlier retries in the name ("– „X“ wiederholt", "– ab „X“ wiederholt", "– 3 Schritte wiederholt")
RETRY_SUFFIX = re.compile(r"( – ((ab )?„.*“|\d+ Schritte?) wiederholt)+$")


def rerun_steps(did, step_ids, target, target_ids, user, allowed=None, reboot=None, current=False):
    """Retry a deployment with freely chosen steps - a new deployment with a slice of the package
    (order and phases stay; chosen steps that were disabled in the original are enabled).
    target: "all" | "failed" | "selected" (then target_ids = IDs of this deployment's targets)."""
    from . import steps
    d = db.q1("SELECT * FROM deployments WHERE id=?", (did,))
    if not d:
        raise ValueError("Bereitstellung nicht gefunden")
    if d["status"] in ("running", "queued", "scheduled"):
        raise ValueError("Die Bereitstellung läuft noch – bitte warten oder abbrechen.")
    want = [str(x) for x in step_ids or [] if x]
    if not want:
        raise ValueError("Bitte mindestens einen Schritt wählen.")
    p = steps.normalize(source_package(d, current))
    known = {st.get("id") for _ph, st in steps.iter_steps(p)}
    unknown = [x for x in want if x not in known]
    if unknown:
        raise ValueError("Unbekannter Schritt – die Ansicht bitte neu laden.")
    chosen = set(want)
    new_steps = {}
    for ph, _label in steps.PHASES:
        new_steps[ph] = [dict(st, enabled=True) for st in p["steps"].get(ph) or [] if st.get("id") in chosen]
    pkg = dict(p, steps=new_steps, slice=True)
    if reboot == "never":
        pkg["reboot"] = "never"  # e.g. retrying only a registry step: do not restart everyone for that
    rows = db.q("SELECT id, computer_id, status FROM deployment_targets WHERE deployment_id=? AND computer_id IS NOT NULL", (did,))
    if target == "selected":
        tids = {int(x) for x in target_ids or []}
        rows = [r for r in rows if r["id"] in tids]
    elif target == "failed":
        rows = [r for r in rows if r["status"] != "success"]
    elif target != "all":
        raise ValueError("Unbekannte Auswahl der Ziele.")
    ids = [r["computer_id"] for r in rows if allowed is None or r["computer_id"] in allowed]
    if not ids:
        raise ValueError("Keine Ziele ausgewählt." if target == "selected" else "Keine passenden Ziele.")
    n = sum(len(v) for v in new_steps.values())
    base = RETRY_SUFFIX.sub("", d["name"])
    label = f"„{steps.step_title(next(st for v in new_steps.values() for st in v))}“ wiederholt" if n == 1 else f"{n} Schritte wiederholt"
    new = create(pkg, ids, user, name=f"{base} – {label}"[:200], package_id=d["package_id"],
                 target_label=d.get("target_label") if target == "all" else None)
    pklog.info("deploy", f"Bereitstellung #{did}: {label} auf {len(ids)} Computer(n) → #{new}.", user=user)
    return new


def _cancelled(did):
    # Deleted counts as aborted - otherwise a target would simply keep running after "Abbrechen" + "Löschen"
    return db.val("SELECT status FROM deployments WHERE id=?", (did,)) in (None, "cancelled")


def _set(tid, **kw):
    keys = ", ".join(f"{k}=?" for k in kw)
    db.ex(f"UPDATE deployment_targets SET {keys} WHERE id=?", tuple(kw.values()) + (tid,))


_comp_locks = {}
_comp_locks_lock = threading.Lock()


def _computer_lock(cid):
    with _comp_locks_lock:
        return _comp_locks.setdefault(cid, threading.Lock())


REQUEUE_SECONDS = 5


def _run_target_safe(did, tid):
    # Only one deployment at a time per computer (e.g. several uninstallations or winget updates as
    # separate deployments): two msiexec at once otherwise end with 1618. Whoever waits stays "in Warteschlange" (queued)
    # and can be aborted normally.
    cid = db.val("SELECT computer_id FROM deployment_targets WHERE id=?", (tid,))
    lk = _computer_lock(cid) if cid else None
    if lk and not lk.acquire(blocking=False):
        # 2.37: do not wait blocking - otherwise many deployments for the same computer occupy all slots of the
        # pool and other computers never get their turn. The target stays "in Warteschlange" (queued) and is re-queued later.
        if db.val("SELECT status FROM deployment_targets WHERE id=?", (tid,)) == "queued":
            t = threading.Timer(REQUEUE_SECONDS, lambda: pool().submit(_run_target_safe, did, tid))
            t.daemon = True
            t.start()
        else:
            _finish_check(did)
        return
    try:
        _run_target(did, tid)
    except Exception as e:  # pragma: no cover
        try:
            _set(tid, status="failed", message=f"Interner Fehler: {e}", finished_at=util.now())
        finally:
            _after_target(did, tid)  # schedule target would otherwise stay "running" permanently -> never supplied again
    finally:
        if lk:
            lk.release()
        _finish_check(did)


def _run_target(did, tid):
    t = db.q1("SELECT * FROM deployment_targets WHERE id=?", (tid,))
    if not t or t["status"] != "queued":
        return
    if _cancelled(did):
        _set(tid, status="cancelled", message="Abgebrochen", finished_at=util.now())
        _after_target(did, tid)
        return
    d = db.q1("SELECT * FROM deployments WHERE id=?", (did,))
    p = json.loads(d["package_json"])
    c = db.q1("SELECT * FROM computers WHERE id=?", (t["computer_id"],)) if t["computer_id"] else None
    if not c:
        _set(tid, status="failed", message="Computer existiert nicht mehr", finished_at=util.now())
        _after_target(did, tid)
        return
    host = c["dns_name"] or c["name"]
    # Atomically from "queued" to "running": a simultaneous abort (or double scheduling) wins
    if not db.ex_count("UPDATE deployment_targets SET status='running', started_at=?, message='Verbinde …' "
                       "WHERE id=? AND status='queued'", (util.now(), tid)):
        return  # aborted in the meantime (cancel() reports to the schedule itself) or already in progress
    from . import presence
    online, winrm = presence.probe(c)  # never install on a foreign device under a stale address
    if not winrm:
        prob = db.q1("SELECT addr_problem, addr_detail FROM computers WHERE id=?", (c["id"],)) or {}
        _set(tid, status="failed", message=(f"Nicht ausgeführt – {prob['addr_detail']}" if prob.get("addr_problem")
                                            else "Computer nicht erreichbar (offline oder WinRM gesperrt)"),
             finished_at=util.now())
        _after_target(did, tid)
        return
    if not _wait_for_orphans(c, host, did, tid):
        _set(tid, status="cancelled", message="Abgebrochen", finished_at=util.now())
        _after_target(did, tid)
        return
    from . import steps
    runner = steps.Runner(did, tid, c, cancel=lambda: _cancelled(did), set_status=lambda m: _set(tid, message=m),
                          user=d.get("created_by"))
    try:
        res, why = runner.run_package(p)
        code = runner.last_code
        if res == "skipped":
            msg = f"Übersprungen – Bedingung nicht erfüllt: {why}"
        elif res == "success":
            done = [r for r in runner.results if r["status"] == "success"]
            msg = done[-1]["message"] if len(runner.results) == 1 and done else \
                f"Erfolgreich ({len(done)} von {len(runner.results)} Schritten ausgeführt)"
        else:
            msg = f"Fehlgeschlagen – {why}"
        ok = res != "failed"
        reboot = p.get("reboot") or "never"
        if res == "success" and (reboot == "always" or (reboot == "if_required" and runner.reboot_needed)):
            try:
                from . import rebootmsg
                o, _e, _ = remote.ps(host, "& shutdown.exe /r /t 120 /c " + util.psq(rebootmsg.text("deploy", sekunden=120))
                                     + " 2>&1; 'PKEXIT:' + $LASTEXITCODE")
                rc = re.search(r"PKEXIT:(-?\d+)", o or "")
                rc = int(rc.group(1)) if rc else 0
                msg += (" – Neustart in 2 Minuten" if rc == 0 else
                        " – Neustart war bereits geplant" if rc == 1190 else f" – Neustart fehlgeschlagen (Exitcode {rc})")
            except Exception as e:
                msg += f" – Neustart fehlgeschlagen: {e}"
        elif res == "success" and runner.reboot_needed and "Neustart" not in msg:
            msg += " – Neustart erforderlich"
        _set(tid, status={"success": "success", "failed": "failed", "skipped": "skipped"}[res], exit_code=code,
             message=msg[:1000], output="\n".join(runner.log)[-200000:], steps=json.dumps(runner.results),
             finished_at=util.now())
        # From here on nothing may overwrite the saved result any more
        if res == "success":
            try:
                from . import toast
                toast.maybe_notify(c, host, p, runner)  # in the background; errors only end up in the log
            except Exception as e:  # noqa: BLE001 – must never overwrite the saved result
                pklog.warn("deploy", f"#{did}: Benachrichtigung nicht gesendet: {e}", computer_id=c["id"])
        try:
            lvl = pklog.info if ok else pklog.warn
            lvl("deploy", f"#{did} {p['name']} auf {c['name']}: {msg}", computer_id=c["id"])
            if res in ("success", "failed") and db.setting("rescan_after_deploy"):
                # After every installation/uninstallation: the default is the profile "Anwendungen" (applications), changeable in the package.
                # 2.32: with several packages (selection, schedule) the profile chosen in each package; if a scan is
                # running, it is made up afterwards instead of discarded
                for pid in rescan_profiles(p):
                    inventory.queue_scan(c["id"], None, pid, follow=True)
        except Exception as e:
            pklog.warn("deploy", f"#{did}: Nacharbeiten (Protokoll/Scan) fehlgeschlagen: {e}", computer_id=c["id"])
    except remote.Cancelled:
        _set(tid, status="cancelled", message="Abgebrochen", output="\n".join(runner.log), steps=json.dumps(runner.results),
             finished_at=util.now())
    except Exception as e:
        _set(tid, status="failed", message=str(e)[:1000], output="\n".join(runner.log), steps=json.dumps(runner.results),
             finished_at=util.now())
        pklog.warn("deploy", f"#{did} {p['name']} auf {c['name']}: {e}", computer_id=c["id"])
    finally:
        _after_target(did, tid)


def _after_target(did, tid):
    """Report the result back to a schedule (target history)."""
    sid = db.val("SELECT schedule_id FROM deployments WHERE id=?", (did,))
    if sid:
        from . import schedules
        try:
            schedules.target_finished(sid, did, tid)
        except Exception as e:  # pragma: no cover
            pklog.error("deploy", f"Zielverlauf des Zeitplans konnte nicht aktualisiert werden: {e}")


def _cleanup_one(row):
    """Process an entry from client_cleanup. Returns 'clean', 'busy' or 'error'."""
    try:
        res = remote.cleanup_run(row["host"], row["prefix"], row["max_minutes"])
    except Exception as e:
        db.ex("UPDATE client_cleanup SET last_try=?, attempts=COALESCE(attempts, 0) + 1, last_result=? WHERE id=?",
              (util.now(), str(e)[:300], row["id"]))
        return "error"
    if res["busy"]:
        db.ex("UPDATE client_cleanup SET last_try=?, last_result=? WHERE id=?",
              (util.now(), "Installer aus dem unterbrochenen Lauf läuft noch", row["id"]))
        return "busy"
    db.ex("DELETE FROM client_cleanup WHERE id=?", (row["id"],))
    note = "auf dem Computer aufgeräumt"
    if res["killed"]:
        note += " (noch laufender Installer nach Ablauf der Zeitgrenze beendet)"
    elif res["codes"]:
        note += f" (der unterbrochene Schritt lief noch zu Ende, Exitcode {res['codes'][-1]})"
    db.ex("UPDATE deployment_targets SET message=? WHERE id=? AND message LIKE ?",
          (f"{INTERRUPTED_MSG} – {note}", row["target_id"], INTERRUPTED_MSG + "%"))
    if res["dirs"] or res["tasks"] or res["killed"]:
        pklog.info("deploy", f"{row['computer_name'] or row['host']}: Reste der unterbrochenen Bereitstellung #{row['deployment_id']} "
                             f"entfernt ({res['dirs']} Ordner, {res['tasks']} Aufgaben"
                             + (f", {res['killed']} Prozess(e) beendet" if res["killed"] else "") + ").",
                   computer_id=row["computer_id"])
    return "clean"


def _cleanup_due(row, now=None):
    if not row["last_try"]:
        return True
    last = util.parse(row["last_try"])
    if not last:
        return True
    wait = 5 if row["last_result"] and "läuft noch" in row["last_result"] else min(5 * 2 ** min(row["attempts"] or 0, 6), 240)
    return ((now or datetime.datetime.now()) - last).total_seconds() >= wait * 60


def process_cleanups():
    """Background (every 5 minutes): process open cleanup jobs for reachable computers."""
    rows = db.q("""SELECT x.*, c.id AS exists_id, c.online, c.winrm FROM client_cleanup x LEFT JOIN computers c ON c.id = x.computer_id
                   ORDER BY x.id""")
    limit = util.ago(days=CLEANUP_GIVE_UP_DAYS)
    for r in rows:
        if (r["created"] and r["created"] < limit) or r["exists_id"] is None:
            db.ex("DELETE FROM client_cleanup WHERE id=?", (r["id"],))
            pklog.warn("deploy", f"{r['computer_name'] or r['host']}: Reste der unterbrochenen Bereitstellung #{r['deployment_id']} "
                                 "konnten nicht entfernt werden (Computer lange nicht erreichbar oder gelöscht). "
                                 "Der Inventar-Scan räumt Reste älter als 2 Tage selbst auf.", computer_id=r["computer_id"])
            continue
        if not (r["online"] and r["winrm"]) or not _cleanup_due(r):
            continue
        lk = _computer_lock(r["computer_id"])
        if not lk.acquire(blocking=False):
            continue  # a deployment is running there right now - it cleans up itself beforehand
        try:
            _cleanup_one(r)
        finally:
            lk.release()


def _wait_for_orphans(c, host, did, tid):
    """Before a new deployment: remove leftovers of interrupted runs on this computer and - if an installer
    from an interrupted run is still working there - wait (otherwise two installations at the same time, 1618).
    False if it was aborted in the meantime."""
    rows = db.q("SELECT * FROM client_cleanup WHERE computer_id=? ORDER BY id", (c["id"],))
    if not rows:
        return True
    deadline = time.time() + max(r["max_minutes"] or 60 for r in rows) * 60 + 600
    while True:
        busy = False
        for r in db.q("SELECT * FROM client_cleanup WHERE computer_id=? ORDER BY id", (c["id"],)):
            if _cleanup_one(r) == "busy":
                busy = True
        if not busy or time.time() > deadline:
            return True
        _set(tid, message="Wartet: auf dem Computer läuft noch eine Installation aus einem unterbrochenen Lauf "
                          "(POCKET KNIFE wurde neu gestartet) …")
        for _ in range(6):
            if _cancelled(did):
                return False
            time.sleep(5)


def _finish_check(did):
    open_ = db.val("SELECT COUNT(*) FROM deployment_targets WHERE deployment_id=? AND status IN ('queued','running')",
                   (did,), 0)
    if open_:
        return
    stats = {r["status"]: r["n"] for r in db.q(
        "SELECT status, COUNT(*) n FROM deployment_targets WHERE deployment_id=? GROUP BY status", (did,))}
    cur = db.val("SELECT status FROM deployments WHERE id=?", (did,))
    if cur == "cancelled":
        status = "cancelled"
    elif (stats.get("success", 0) or stats.get("skipped", 0)) and not stats.get("failed", 0):
        status = "success"
    elif stats.get("success", 0):
        status = "partial"
    else:
        status = "failed"
    db.ex("UPDATE deployments SET status=?, finished_at=? WHERE id=?", (status, util.now(), did))


# --- Cleanup after a container restart ---------------------------------------------------------
INTERRUPTED_MSG = "Unterbrochen (POCKET KNIFE wurde neu gestartet)"
CLEANUP_GIVE_UP_DAYS = 14


def max_minutes(p):
    """Longest time limit of a step of this package (minutes) - that long an orphaned installer may keep running."""
    base = (p or {}).get("timeout_min") or db.setting("deploy_timeout_minutes") or 60
    best = base
    stack = [p or {}]
    while stack:
        q = stack.pop()
        for phase in ("pre", "main", "post"):
            for st in ((q.get("steps") or {}).get(phase) or []) if isinstance(q.get("steps"), dict) else []:
                best = max(best, st.get("timeout_min") or q.get("timeout_min") or base)
                if isinstance(st.get("package"), dict):
                    stack.append(st["package"])
    return int(best)


def recover():
    """After a container restart: end hanging targets as interrupted and note for each that the client
    has to be cleaned up (working folder, scheduled tasks, possibly a still running installer)."""
    hung = db.q("""SELECT t.id, t.deployment_id, t.computer_id, t.computer_name, c.name, c.dns_name, d.package_json
                   FROM deployment_targets t LEFT JOIN computers c ON c.id = t.computer_id
                   LEFT JOIN deployments d ON d.id = t.deployment_id WHERE t.status='running'""")
    for r in hung:
        db.ex("UPDATE deployment_targets SET status='failed', message=?, finished_at=? WHERE id=? AND status='running'",
              (INTERRUPTED_MSG + " – Reste auf dem Computer werden entfernt, sobald er erreichbar ist", util.now(), r["id"]))
        if r["computer_id"] and (r["dns_name"] or r["name"]):
            try:
                mm = max_minutes(json.loads(r["package_json"] or "{}"))
            except ValueError:
                mm = db.setting("deploy_timeout_minutes") or 60
            db.ex("""INSERT INTO client_cleanup(computer_id, computer_name, host, prefix, max_minutes, deployment_id, target_id, created)
                     VALUES(?,?,?,?,?,?,?,?)""", (r["computer_id"], r["name"] or r["computer_name"], r["dns_name"] or r["name"],
                                                  f"{r['deployment_id']}_{r['id']}_", mm, r["deployment_id"], r["id"], util.now()))
        _after_target(r["deployment_id"], r["id"])
    if hung:
        pklog.warn("deploy", f"{len(hung)} laufende Bereitstellungsziele wurden durch den Neustart unterbrochen – "
                             "auf den Computern wird aufgeräumt, sobald sie erreichbar sind.")
    for d in db.q("SELECT id FROM deployments WHERE status IN ('running','queued')"):
        if db.val("SELECT COUNT(*) FROM deployment_targets WHERE deployment_id=? AND status='queued'", (d["id"],), 0):
            start(d["id"], checked=True)  # checked at creation - the restart of POCKET KNIFE should not wait for fingerprints
        else:
            _finish_check(d["id"])
