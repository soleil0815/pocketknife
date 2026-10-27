"""Softwareverteilung (winget oder eigene Pakete aus dem Repository)."""
import base64
import datetime
import json
import re
import shlex
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import db, inventory, pklog, remote, util

WINGET_OK = {0: False, -1978335189: False, -1978335135: False, -1978334967: True}  # Code -> Neustart nötig
REBOOT_CODES = {3010, 1641}

_pool = None
_lock = threading.Lock()

PKG_FIELDS = ["name", "version", "description", "type", "winget_id", "winget_action", "winget_version",
              "winget_scope", "extra_args", "folder", "install_type", "install_file", "arguments", "script",
              "success_codes", "detect_name", "timeout_min", "reboot", "rescan",
              "steps", "conditions", "run_as", "tenant_id", "folder_name", "rescan_profile_id", "notify_user"]
JSON_FIELDS = ("steps", "conditions")


def row_to_package(row):
    """DB-Zeile -> Paket-Dict (JSON-Felder dekodiert)."""
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
        return  # Schritt-Pakete werden beim Speichern geprüft (steps.clean_package)
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


# --- Skripterzeugung ---------------------------------------------------------
WINGET_MIN_BUILD = 17763  # winget läuft ab Windows 10 1809 / Windows Server 2019

# Findet winget.exe; fehlt es, wird der App-Installer (winget) samt Abhängigkeiten als bereitgestelltes Paket
# nachinstalliert – aus dem Repository-Ordner (offline) oder vom offiziellen GitHub-Release von microsoft/winget-cli.
WINGET_FIND = r"""
$PKProxy = __PROXY__
$pkWeb = @{}
if ($PKProxy) { $pkWeb.Proxy = $PKProxy; $pkWeb.ProxyUseDefaultCredentials = $true }
function PK-WingetProxy {
  # Proxy aus POCKET KNIFE als winget-Administratoreinstellung „DefaultProxy“ setzen – und nur selbst gesetzte wieder entfernen
  # Merker in HKLM (nur Administratoren/SYSTEM) – nicht in %ProgramData%, dort dürfen Benutzer Ordner vorab anlegen (Audit 2.29)
  $mark = 'HKLM:\SOFTWARE\PocketKnife'
  $old = $null
  try { $old = ('' + (Get-ItemProperty -LiteralPath $mark -Name WingetProxy -ErrorAction Stop).WingetProxy).Trim() } catch {}
  if ($null -eq $old) {
    # bis 2.28 lag der Merker als Datei vor – einmalig übernehmen (nur lesen)
    $old = ''
    $f = Join-Path $env:ProgramData 'PocketKnife\winget-proxy.txt'
    if (Test-Path -LiteralPath $f -PathType Leaf) { $old = ('' + (Get-Content -LiteralPath $f -Raw -ErrorAction SilentlyContinue)).Trim() }
  }
  if ($PKProxy -eq $old) { return }
  $id = [Security.Principal.WindowsIdentity]::GetCurrent()
  if (-not $id.IsSystem -and -not ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { return }
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
  # Ersatzweg über PATH nur mit gültiger Microsoft-Signatur – sonst liefe als SYSTEM eine untergeschobene winget.exe
  # aus einem für Benutzer beschreibbaren PATH-Ordner (Audit 2.29)
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
  if (-not (Get-ChildItem $src -Filter *.msixbundle -ErrorAction SilentlyContinue)) {
    Write-Output 'winget fehlt – lade den App-Installer von github.com/microsoft/winget-cli …'
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $rel = Invoke-RestMethod -Uri 'https://api.github.com/repos/microsoft/winget-cli/releases/latest' -UseBasicParsing -Headers @{ 'User-Agent' = 'POCKET-KNIFE' } @pkWeb
    foreach ($a in $rel.assets) {
      if ($a.name -match '\.msixbundle$|License.*\.xml$|^DesktopAppInstaller_Dependencies\.zip$') {
        Invoke-WebRequest -Uri $a.browser_download_url -OutFile (Join-Path $src $a.name) -UseBasicParsing @pkWeb
      }
    }
    Write-Output ('Version ' + $rel.tag_name + ' heruntergeladen.')
  } else { Write-Output 'winget fehlt – installiere den App-Installer aus dem Repository …' }
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
    """Ad-hoc-Paket: nur winget prüfen und bei Bedarf nachinstallieren."""
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
    # Feste Argumente bei Bedarf quoten; die zusätzlichen Argumente unverändert anhängen (eigene Anführungszeichen bleiben)
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
        # 2.30.1/2.31: Pakete ohne angegebenen Bereich im winget-Katalog (z. B. MobaXterm) bzw. Installationen, deren
        # Bereich winget nicht kennt (z. B. PowerShell 7 per MSI): mit --scope lehnt winget die Aktualisierung ab
        # („does not apply“ -1978335189 bzw. „No installed package found“ -1978335212) – von Hand ohne --scope klappt es.
        # Dann einmal ohne --scope wiederholen; alle weiteren Versuche (tar.exe) laufen ebenfalls ohne.
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
# Lokales System erkennen, ohne .NET-Methoden (Constrained Language Mode): Benutzername ist dann <Computer>$
$pkSys = ($env:USERNAME -eq ($env:COMPUTERNAME + '$')) -or ($env:USERPROFILE -like '*\\systemprofile')
if ($global:PKExit -eq -1978335140 -and $pkSys) {
  # Als Lokales System scheitert das Entpacken über die Windows-Shell oft – einmal mit tar.exe wiederholen
  # winget liest als Lokales System ...\\WinGet\\Settings\\defaultState\\settings.json (laut „winget settings export“ auf einem Client)
  Write-Output 'Hinweis: Archiv konnte nicht entpackt werden – neuer Versuch mit tar.exe (winget-Einstellung archiveExtractionMethod für Lokales System).'
  foreach ($d in @("$env:LOCALAPPDATA\\Packages\\Microsoft.DesktopAppInstaller_8wekyb3d8bbwe\\LocalState", "$env:LOCALAPPDATA\\Microsoft\\WinGet\\Settings\\defaultState")) {
    try {
      $f = Join-Path $d 'settings.json'
      $j = $null
      # Kommentarzeilen (// …, wie in der Vorlage von winget) entfernen – Windows PowerShell 5.1 kann sie nicht lesen
      if (Test-Path $f) { try { $j = ((Get-Content $f -Raw -ErrorAction Stop) -replace '(?m)^\\s*//.*$', '') | ConvertFrom-Json -ErrorAction Stop } catch { Write-Output ('Eigene winget-Einstellungen unverändert: ' + $f); continue } }
      if ($null -eq $j) { $j = [pscustomobject]@{} }
      if (-not $j.installBehavior) { $j | Add-Member -NotePropertyName installBehavior -NotePropertyValue ([pscustomobject]@{}) -Force }
      $j.installBehavior | Add-Member -NotePropertyName archiveExtractionMethod -NotePropertyValue 'tar' -Force
      New-Item -ItemType Directory -Force -Path $d | Out-Null
      [IO.File]::WriteAllText($f, ($j | ConvertTo-Json -Depth 10), (New-Object Text.UTF8Encoding $false))
    } catch { Write-Output ('winget-Einstellung nicht geschrieben: ' + $_.Exception.Message) }
  }
  PK-Run $wg $pkA
}
if ($global:PKExit -eq -1978335189) {
  # „Keine passende Aktualisierung“ heißt nicht immer „aktuell“: eine neuere Version kann es geben, sie passt nur nicht
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
        b64 = base64.b64encode(p["script"].encode("utf-8")).decode()
        body += f"""
$sf = Join-Path $PKDir 'pk_script.ps1'
[System.IO.File]::WriteAllText($sf, [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{b64}')), (New-Object System.Text.UTF8Encoding $true))
PK-Run {util.PS_EXE_PS} ('{util.PS_FLAGS} -File "' + $sf + '" ' + {util.psq(args)})
"""
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
        body += f"PK-Run {util.PS_EXE_PS} ('{util.PS_FLAGS} -File \"' + $f + '\" ' + {a})\n"
    elif it == "reg":
        # 64-Bit-reg.exe (auch aus einer 32-Bit-PowerShell); anders als regedit /s liefert reg import einen Exitcode
        body += ("$pkRegExe = Join-Path $env:windir 'Sysnative\\reg.exe'\n"
                 "if (-not (Test-Path $pkRegExe)) { $pkRegExe = Join-Path $env:windir 'System32\\reg.exe' }\n"
                 f"PK-Run $pkRegExe (('import \"' + $f + '\" ' + {a}).Trim())\n")
    return body


# Schalter, die POCKET KNIFE bei MSI/MSP/MSU selbst setzt – doppelte Angaben im Feld „Parameter“ werden entfernt
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
    """Entfernt /qn, /quiet, /norestart … aus eigenen Parametern. Gibt (args, hat_eigenes_log) zurück."""
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
    """Befehlszeile, wie sie auf dem Client ausgeführt wird (für die Anzeige)."""
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
    """(erfolgreich, neustart_nötig)"""
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
    # 2.31: von POCKET KNIFE vergeben, wenn winget „keine passende Aktualisierung“ meldet, obwohl es eine neuere gibt
    9011: "Neuere Version vorhanden, aber mit anderer Installationstechnik (z. B. MSI ↔ EXE/MSIX) – winget aktualisiert "
          "das nicht. Abhilfe: im Paket erst einen Schritt „Deinstallieren“, dann „Installieren“ – oder bei „Zusätzliche "
          "Argumente“ --force (installiert die neue Version daneben, die alte bleibt)",
    9012: "Neuere Version vorhanden, passt aber nicht zu diesem System (Architektur, Windows-Version oder andere "
          "Voraussetzungen im winget-Katalog)",
}


# --- Ablauf ----------------------------------------------------------------
def tenant_members(tid, strict=False):
    """Computer, die ein Mandant erreichen darf: set – oder None = keine Einschränkung.
    Sicherheitsregeln: Mandant gelöscht → keine Computer; Mandant, der je OU-Admins hatte, ohne (gültige) Sammlung →
    keine Computer (deren Pakete/Zeitpläne dürfen nie „überall“ laufen)."""
    from . import collections_, profiles
    from . import access
    t = db.q1("SELECT collection_id, collection_ids, COALESCE(ou_ever, 0) ou_ever, admin_group FROM tenants WHERE id=?", (int(tid),))
    if not t:
        return set()
    cols = access.tenant_cols(t)
    if not cols or profiles.ALL_COMPUTERS in cols:
        return set() if (t["ou_ever"] or t["admin_group"]) else None
    out = set()
    for col in cols:  # 2.29.9: mehrere Sammlungen – zusammen
        try:
            out.update(collections_.member_ids(int(col)))
        except Exception:
            if strict:
                raise
    return out


def tenant_filter(pkg, ids, strict=False):
    """Pakete eines Mandanten (auch verschachtelte) nur auf Computer der Mandanten-Sammlung verteilen."""
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
    """Mehrere gespeicherte Pakete als EINE Bereitstellung: je Computer nacheinander in dieser Reihenfolge
    (verschachtelte Schritte). So laufen nie zwei Installationen gleichzeitig auf einem Computer
    (msiexec meldet sonst 1618 „eine andere Installation läuft“)."""
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
    """2.32: Mit welchen Scanprofilen nach der Bereitstellung gescannt wird – [] = nicht scannen.
    Ein Paket: sein Profil („Danach scannen“, Standard „Anwendungen“). Mehrere Pakete in einer Bereitstellung oder einem
    Zeitplan (composite): das Profil jedes einzelnen Pakets, „Nicht scannen“ zählt je Paket; jedes Profil einmal."""
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
    """Neustart-Einstellung mehrerer Pakete zusammengefasst: „immer“ vor „wenn nötig“ vor „nie“ –
    ein Paket, das allein neu startet, tut das auch innerhalb eines Zeitplans oder einer Mehrfachauswahl."""
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
    # 2.26: Repository-Dateien gegen den bestätigten Stand prüfen – Bruch → nichts anlegen, nichts starten
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
    """checked: gerade eben von create() geprüft. Sonst (geplante Bereitstellung, Zeitplan) vor dem Start die
    Repository-Dateien prüfen – bei einem Bruch wird der Lauf unterbrochen, bevor ein Computer etwas tut."""
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
    # Nur wenn noch nicht abgebrochen – sonst stünde eine abgebrochene Bereitstellung für immer auf „läuft“
    if db.ex_count("UPDATE deployments SET status='running', started_at=COALESCE(started_at, ?) WHERE id=? "
                   "AND status IN ('queued','scheduled','running')", (util.now(), did)) != 1:
        return
    for t in db.q("SELECT id FROM deployment_targets WHERE deployment_id=? AND status='queued'", (did,)):
        pool().submit(_run_target_safe, did, t["id"])


def _rescope(did):
    """Audit 2.29: Mandantenbereich beim START erneut anwenden (nicht nur beim Anlegen) – ein Computer, der seit dem
    Planen die Mandanten-Sammlung verlassen hat, bekommt das Mandantenpaket nicht mehr."""
    d = db.q1("SELECT package_json FROM deployments WHERE id=?", (did,))
    if not d:
        return
    try:
        pkg = json.loads(d["package_json"] or "{}")
    except ValueError:
        return
    rows = db.q("SELECT id, computer_id FROM deployment_targets WHERE deployment_id=? AND status='queued'", (did,))
    try:
        # strict: eine kurze Störung (Datenbank gesperrt) darf nicht alle Ziele als „nicht mehr im Mandanten“ auslassen
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
    """Audit 2.29: „wartend“ angelegte Bereitstellungen, deren Start scheiterte (z. B. Datenbank kurz gesperrt), blieben
    bis zum nächsten Neustart liegen – und sperrten ihre Computer für alle Zeitpläne. Jetzt holt der Takt sie nach."""
    old = util.ago(minutes=minutes)
    for d in db.q("""SELECT d.id FROM deployments d WHERE d.status='queued' AND d.created_at <= ? AND EXISTS(
                     SELECT 1 FROM deployment_targets t WHERE t.deployment_id = d.id AND t.status='queued')""", (old,)):
        start(d["id"])


def _interrupt(did, msg):
    """Bereitstellung wegen eines Bruchs im Repository unterbrechen: kein Ziel wird angefasst."""
    now = util.now()
    if db.ex_count("UPDATE deployments SET status='failed', started_at=COALESCE(started_at, ?), finished_at=? "
                   "WHERE id=? AND status IN ('queued','scheduled')", (now, now, did)) != 1:
        return
    tids = [r["id"] for r in db.q("SELECT id FROM deployment_targets WHERE deployment_id=? AND status='queued'", (did,))]
    from . import integrity
    # Am Ziel steht ein neutraler Text (sehen auch OU-Admins und Kiosk-Nutzer) – die Dateien nur im Protokoll ohne Computer
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


def redeploy(did, failed_only, user):
    d = db.q1("SELECT * FROM deployments WHERE id=?", (did,))
    if not d:
        raise ValueError("Bereitstellung nicht gefunden")
    sql = "SELECT computer_id FROM deployment_targets WHERE deployment_id=? AND computer_id IS NOT NULL"
    if failed_only:
        sql += " AND status NOT IN ('success')"
    ids = [r["computer_id"] for r in db.q(sql, (did,))]
    return create(json.loads(d["package_json"]), ids, user, name=d["name"], package_id=d["package_id"],
                  target_label=d.get("target_label"))


# --- Einzelne Schritte wiederholen -------------------------------------------------
def _top_records(recs):
    """Ergebnisse der obersten Ebene (ohne die Schritte verschachtelter Pakete)."""
    out = []
    for r in recs:
        depth = r.get("depth")
        if depth is None:  # Ergebnisse älterer Versionen: Einrückung des Titels
            t = r.get("title") or ""
            depth = (len(t) - len(t.lstrip(" "))) // 2
        if depth == 0:
            out.append(r)
    return out


def failed_steps(did):
    """Fehlgeschlagene Schritte einer Bereitstellung mit den betroffenen Zielen (für „Schritt wiederholen“)."""
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
    """Paket-Snapshot auf einen Schritt (mode=step) bzw. ab diesem Schritt (mode=from) kürzen."""
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
    p = dict(p, steps=new)
    return p, steps.step_title(st)


def retry_step(did, sid, mode, target_ids, user):
    d = db.q1("SELECT * FROM deployments WHERE id=?", (did,))
    if not d:
        raise ValueError("Bereitstellung nicht gefunden")
    if d["status"] in ("running", "queued", "scheduled"):
        raise ValueError("Die Bereitstellung läuft noch – bitte warten oder abbrechen.")
    mode = "from" if mode == "from" else "step"
    pkg, title = retry_package(json.loads(d["package_json"] or "{}"), sid, mode)
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


def steps_overview(did, allowed=None):
    """Oberste Schritte einer Bereitstellung mit dem Ergebnis je Ziel (für „Schritte wählen …“).
    allowed: nur diese Computer zählen (OU-Admins)."""
    from . import steps
    d = db.q1("SELECT * FROM deployments WHERE id=?", (did,))
    if not d:
        raise ValueError("Bereitstellung nicht gefunden")
    p = steps.normalize(json.loads(d["package_json"] or "{}"))
    top = list(steps.iter_steps(p))
    out = [{"sid": st.get("id"), "phase": ph, "title": steps.step_title(st), "type": st.get("type"),
            "enabled": st.get("enabled", True) is not False, "counts": {}} for ph, st in top]
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


# Endung früherer Wiederholungen im Namen („– „X“ wiederholt“, „– ab „X“ wiederholt“, „– 3 Schritte wiederholt“)
RETRY_SUFFIX = re.compile(r"( – ((ab )?„.*“|\d+ Schritte?) wiederholt)+$")


def rerun_steps(did, step_ids, target, target_ids, user, allowed=None, reboot=None):
    """Bereitstellung mit frei gewählten Schritten wiederholen – neue Bereitstellung mit einem Ausschnitt des Pakets
    (Reihenfolge und Phasen bleiben; gewählte, im Original ausgeschaltete Schritte werden eingeschaltet).
    target: "all" | "failed" | "selected" (dann target_ids = IDs der Ziele dieser Bereitstellung)."""
    from . import steps
    d = db.q1("SELECT * FROM deployments WHERE id=?", (did,))
    if not d:
        raise ValueError("Bereitstellung nicht gefunden")
    if d["status"] in ("running", "queued", "scheduled"):
        raise ValueError("Die Bereitstellung läuft noch – bitte warten oder abbrechen.")
    want = [str(x) for x in step_ids or [] if x]
    if not want:
        raise ValueError("Bitte mindestens einen Schritt wählen.")
    p = steps.normalize(json.loads(d["package_json"] or "{}"))
    known = {st.get("id") for _ph, st in steps.iter_steps(p)}
    unknown = [x for x in want if x not in known]
    if unknown:
        raise ValueError("Unbekannter Schritt – die Ansicht bitte neu laden.")
    chosen = set(want)
    new_steps = {}
    for ph, _label in steps.PHASES:
        new_steps[ph] = [dict(st, enabled=True) for st in p["steps"].get(ph) or [] if st.get("id") in chosen]
    pkg = dict(p, steps=new_steps)
    if reboot == "never":
        pkg["reboot"] = "never"  # z. B. nur einen Registry-Schritt wiederholen: dafür nicht alle neu starten
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
    # Gelöscht gilt als abgebrochen – sonst liefe ein Ziel nach „Abbrechen“ + „Löschen“ einfach weiter
    return db.val("SELECT status FROM deployments WHERE id=?", (did,)) in (None, "cancelled")


def _set(tid, **kw):
    keys = ", ".join(f"{k}=?" for k in kw)
    db.ex(f"UPDATE deployment_targets SET {keys} WHERE id=?", tuple(kw.values()) + (tid,))


_comp_locks = {}
_comp_locks_lock = threading.Lock()


def _computer_lock(cid):
    with _comp_locks_lock:
        return _comp_locks.setdefault(cid, threading.Lock())


def _run_target_safe(did, tid):
    # Je Computer immer nur eine Bereitstellung gleichzeitig (z. B. mehrere Deinstallationen oder winget-Updates als
    # eigene Bereitstellungen): zwei msiexec gleichzeitig enden sonst mit 1618. Wer wartet, bleibt „in Warteschlange“
    # und lässt sich normal abbrechen.
    cid = db.val("SELECT computer_id FROM deployment_targets WHERE id=?", (tid,))
    lk = _computer_lock(cid) if cid else None
    if lk:
        lk.acquire()
    try:
        _run_target(did, tid)
    except Exception as e:  # pragma: no cover
        try:
            _set(tid, status="failed", message=f"Interner Fehler: {e}", finished_at=util.now())
        finally:
            _after_target(did, tid)  # Zeitplan-Ziel sonst dauerhaft „running“ → nie wieder versorgt
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
    # Atomar von „queued“ auf „running“: ein gleichzeitiger Abbruch (oder doppelte Einplanung) gewinnt
    if not db.ex_count("UPDATE deployment_targets SET status='running', started_at=?, message='Verbinde …' "
                       "WHERE id=? AND status='queued'", (util.now(), tid)):
        return  # inzwischen abgebrochen (cancel() meldet selbst an den Zeitplan) oder schon in Arbeit
    from . import presence
    online, winrm = presence.probe(c)  # nie unter einer veralteten Adresse auf einem fremden Gerät installieren
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
        # Ab hier darf nichts mehr das gespeicherte Ergebnis überschreiben
        if res == "success":
            try:
                from . import toast
                toast.maybe_notify(c, host, p, runner)  # im Hintergrund; Fehler landen nur im Protokoll
            except Exception as e:  # noqa: BLE001 – darf das gespeicherte Ergebnis nie überschreiben
                pklog.warn("deploy", f"#{did}: Benachrichtigung nicht gesendet: {e}", computer_id=c["id"])
        try:
            lvl = pklog.info if ok else pklog.warn
            lvl("deploy", f"#{did} {p['name']} auf {c['name']}: {msg}", computer_id=c["id"])
            if res in ("success", "failed") and db.setting("rescan_after_deploy"):
                # Nach jeder Installation/Deinstallation: Standard ist das Profil „Anwendungen“, im Paket änderbar.
                # 2.32: bei mehreren Paketen (Auswahl, Zeitplan) das jeweils im Paket gewählte Profil; läuft gerade ein
                # Scan, wird der danach nachgeholt statt verworfen
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
    """Ergebnis an einen Zeitplan zurückmelden (Zielverlauf)."""
    sid = db.val("SELECT schedule_id FROM deployments WHERE id=?", (did,))
    if sid:
        from . import schedules
        try:
            schedules.target_finished(sid, did, tid)
        except Exception as e:  # pragma: no cover
            pklog.error("deploy", f"Zielverlauf des Zeitplans konnte nicht aktualisiert werden: {e}")


def _cleanup_one(row):
    """Einen Eintrag aus client_cleanup abarbeiten. Gibt 'clean', 'busy' oder 'error' zurück."""
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
    """Hintergrund (alle 5 Minuten): offene Aufräum-Aufträge für erreichbare Computer abarbeiten."""
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
            continue  # gerade läuft dort eine Bereitstellung – die räumt vorher selbst auf
        try:
            _cleanup_one(r)
        finally:
            lk.release()


def _wait_for_orphans(c, host, did, tid):
    """Vor einer neuen Bereitstellung: Reste unterbrochener Läufe auf diesem Computer entfernen und – falls dort
    noch ein Installer aus einem unterbrochenen Lauf arbeitet – warten (sonst zwei Installationen gleichzeitig, 1618).
    False, wenn währenddessen abgebrochen wurde."""
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


# --- Aufräumen nach einem Neustart des Containers ------------------------------------------------
INTERRUPTED_MSG = "Unterbrochen (POCKET KNIFE wurde neu gestartet)"
CLEANUP_GIVE_UP_DAYS = 14


def max_minutes(p):
    """Längste Zeitgrenze eines Schritts dieses Pakets (Minuten) – so lange darf ein verwaister Installer weiterlaufen."""
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
    """Nach einem Neustart des Containers: hängende Ziele als unterbrochen beenden und für jedes merken, dass auf dem
    Client aufgeräumt werden muss (Arbeitsordner, geplante Aufgaben, ggf. ein noch laufender Installer)."""
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
            start(d["id"], checked=True)  # beim Anlegen geprüft – der Neustart von POCKET KNIFE soll nicht auf Fingerabdrücke warten
        else:
            _finish_check(d["id"])
