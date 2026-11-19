"""2.52.0: hints for own PowerShell code (package steps, tools, "Befehl ausführen", own scanners, .ps1 files in the
repository editor): what PowerShell's script block logging writes as a WARNING (event 4104) - monitoring software
(SIEM, Defender for Endpoint) can raise alerts on that. Only hints, never a block: the code runs as it is.

Three things trigger it:
1. a word of PowerShell's list of "suspicious" content anywhere in the text - even in a comment or a string, and also as a
   part of a longer word (case-insensitive: "CimClassMethods" contains "Methods")
2. a member named through a variable ($obj.$name) - PowerShell rates that as obfuscation
3. a cmdlet of a generated (CDXML) Windows module - loading the module writes its own code as a series of warnings
2.53.1: and one thing PowerShell logs as event 4100 ("System error.") although nothing failed:
4. Select-Object -First/-Index - it stops the commands before it with an internal exception, and Windows PowerShell
   5.1 logs every such stop

The word list is PowerShell's own (checked against its CheckSuspiciousContent in the self-test).
"""
import re
import threading

# PowerShell's list (the part a normal admin script can hit, plus the API names) - substring, case-insensitive
WORDS = (
    "Add-Type", "AddSecurityPackage", "AdjustTokenPrivileges", "AllocHGlobal", "BindingFlags", "Bypass", "CloseHandle",
    "CreateDecryptor", "CreateEncryptor", "CreateProcessWithToken", "CreateRemoteThread", "CreateThread", "CreateType",
    "CreateUserThread", "Cryptography", "CryptoServiceProvider", "CryptoStream", "DangerousGetHandle", "DeclaringMethod",
    "DeclaringType", "DefineConstructor", "DefineDynamicAssembly", "DefineDynamicModule", "DefineEnum", "DefineField",
    "DefineLiteral", "DefinePInvokeMethod", "DefineType", "DeflateStream", "DeviceIoControl", "DllImport",
    "DuplicateTokenEx", "Emit", "EncodedCommand", "EnumerateSecurityPackages", "ExpandString", "FreeHGlobal", "FreeLibrary",
    "GetAssemblies", "GetAsyncKeyState", "GetConstructor", "GetConstructors", "GetDefaultMembers",
    "GetDelegateForFunctionPointer", "GetEvent", "GetEvents", "GetField", "GetFields", "GetForegroundWindow", "GetInterface",
    "GetInterfaceMap", "GetInterfaces", "GetKeyboardState", "GetLogonSessionData", "GetMember", "GetMembers", "GetMethod",
    "GetMethods", "GetModuleHandle", "GetNestedType", "GetNestedTypes", "GetPowerShell", "GetProcAddress",
    "GetProcessHandle", "GetProperties", "GetProperty", "GetTokenInformation", "GetTypes", "ILGenerator",
    "ImpersonateLoggedOnUser", "InteropServices", "IntPtr", "InvokeMember", "LoadLibrary", "LogPipelineExecutionDetails",
    "MakeArrayType", "MakeByRefType", "MakeGenericType", "MakePointerType", "Marshal", "memcpy", "MemoryStream", "Methods",
    "MiniDumpWriteDump", "NonPublic", "OpenDesktop", "OpenProcess", "OpenProcessToken", "OpenThreadToken",
    "OpenWindowStation", "PasswordDeriveBytes", "Properties", "ProtectedEventLogging", "PtrToString", "PtrToStructure",
    "ReadProcessMemory", "ReflectedType", "RevertToSelf", "RijndaelManaged", "ScriptBlockLogging", "SetInformationProcess",
    "SetThreadToken", "StructureToPtr", "TransformFinalBlock", "TypeHandle", "TypeInitializer", "UnderlyingSystemType",
    "UnverifiableCodeAttribute", "VirtualAlloc", "VirtualFree", "VirtualProtect", "WriteByte", "WriteProcessMemory",
    "ZeroFreeGlobalAllocUnicode",
)

# Advice for the words an admin script typically contains
WORD_HINTS = {
    "bypass": "„-ExecutionPolicy Bypass“ ist unnötig – POCKET KNIFE startet PowerShell bereits passend; sonst „Unrestricted“ schreiben",
    "encodedcommand": "Befehl nicht kodiert übergeben – als Skriptdatei (-File) oder direkt als Schritt",
    "add-type": "kein eingebetteter C#-Code – ein Programm aus System32, ein Cmdlet oder eine CIM-Klasse nehmen",
    "properties": "z. B. $obj.PSObject.Properties: Eigenschaft direkt ansprechen ($obj.Name) oder Get-Member -MemberType Property; auch in Kommentaren umformulieren",
    "methods": "auch in Kommentaren und Namen (z. B. CimClassMethods) – umformulieren",
    "memorystream": "Datei- oder Text-Cmdlets nehmen (Set-Content/Get-Content -Encoding Byte)",
    "expandstring": "Registry-Wert REG_EXPAND_SZ: reg.exe add … /t REG_EXPAND_SZ oder der Registry-Schritt",
    "intptr": "kein Aufruf von Windows-APIs aus PowerShell – Programm aus System32 oder CIM",
    "marshal": "kein Aufruf von Windows-APIs aus PowerShell – Programm aus System32 oder CIM",
    "cryptography": "für Prüfsummen Get-FileHash nehmen",
}

# Cmdlets of generated (CDXML) Windows modules -> alternative
MODULES = (
    (r"(Get|Set|New|Register|Unregister|Start|Stop|Enable|Disable|Export)-(Clustered)?ScheduledTask\w*",
     "Aufgabenplanung", "schtasks.exe /Create /XML datei.xml, /Run, /End, /Delete – oder CIM (MSFT_ScheduledTask)"),
    (r"(Get|Set|New|Remove|Enable|Disable|Copy|Rename)-NetFirewall\w+", "Firewall",
     "netsh advfirewall firewall add|delete|set rule …"),
    (r"(Get|Set|New|Remove|Enable|Disable|Restart|Rename)-NetAdapter\w*", "Netzwerkkarten",
     "Get-CimInstance -Namespace root/StandardCimv2 -ClassName MSFT_NetAdapter bzw. netsh interface …"),
    (r"(Get|Set|New|Remove)-Net(IPAddress|IPConfiguration|IPInterface|Route|Neighbor|TCPConnection|UDPEndpoint|ConnectionProfile|TCPSetting)\b",
     "Netzwerk (TCP/IP)", "netsh interface ip …, route.exe, netstat -ano – oder die CIM-Klassen unter root/StandardCimv2"),
    (r"(Get|Set|Clear|Register|Unregister|Add|Remove)-DnsClient\w*", "DNS-Client",
     "netsh interface ip set dns …, ipconfig /flushdns"),
    # 2.56.1: every cmdlet of the Storage module (Get-PartitionSupportedSize, Get-StorageReliabilityCounter, Mount-DiskImage …)
    (r"[A-Za-z]+-(Disk|DiskImage|DiskStorageNodeView|PhysicalDisk|PhysicalDiskIdentification|PhysicalDiskStorageNodeView|"
     r"PhysicalExtent|PhysicalExtentAssociation|Partition|PartitionAccessPath|PartitionSupportedSize|Volume|VolumeCorruptionCount|"
     r"VolumeScrubPolicy|VirtualDisk|VirtualDiskSupportedSize|Storage[A-Z]\w*|MaskingSet|InitiatorId|InitiatorPort|TargetPort|"
     r"TargetPortal|ResiliencySetting|FileIntegrity|FileStorageTier|SupportedFileSystems|SupportedClusterSizes|"
     r"OffloadDataTransferSetting)",
     "Datenträger",
     "Get-CimInstance -Namespace root/Microsoft/Windows/Storage -ClassName MSFT_Disk / MSFT_Partition / MSFT_Volume / MSFT_PhysicalDisk (dieselben Daten ohne das Modul, Statusfelder als Zahl) bzw. Win32_DiskDrive/Win32_LogicalDisk; diskpart, defrag.exe, chkdsk"),
    (r"(Get|Enable|Disable)-PnpDevice\w*", "Geräte (PnP)",
     "Get-CimInstance -ClassName Win32_PnPEntity bzw. pnputil.exe /enum-devices, /disable-device, /enable-device"),
    (r"(Get|Set|New|Remove|Enable|Disable|Copy|Rename)-NetIPsec\w+", "IPsec", "netsh advfirewall consec …"),
    (r"(Get|Set|New|Remove|Grant|Revoke|Block|Unblock)-Smb\w+", "SMB-Freigaben", "net share …, icacls"),
    (r"(Get|Add|Set|Remove|Rename)-Printer\w*", "Drucker",
     "rundll32 printui.dll,PrintUIEntry … oder printui /?; Treiber mit pnputil"),
    (r"(Get|Enable|Disable|Suspend|Resume|Lock|Unlock|Add|Remove|Backup|Clear)-BitLocker\w*", "BitLocker",
     "manage-bde -status / -protectors … – oder CIM (Win32_EncryptableVolume)"),
    (r"(Get|Set|Add|Remove)-MpPreference|Get-MpComputerStatus|Start-MpScan|Update-MpSignature|(Get|Remove)-MpThreat\w*",
     "Microsoft Defender", "MpCmdRun.exe (z. B. -SignatureUpdate, -Scan) oder CIM (root/Microsoft/Windows/Defender); Ausnahmen besser per GPO"),
    (r"(Get|Set|Enable|Disable)-NetQos\w*|(Get|Set)-NetConnectionProfile", "Netzwerk", "netsh bzw. CIM (root/StandardCimv2)"),
)
_MOD_RX = [(re.compile(r"(?i)\b(?:" + rx + r")\b"), name, alt) for rx, name, alt in MODULES]
# 2.56.1: cmdlets that throw a terminating error when they cannot answer - even with -ErrorAction SilentlyContinue
# (PowerShell logs that as event 4100)
THROWS = (
    (r"Confirm-SecureBootUEFI|Get-SecureBootUEFI",
     "Secure Boot aus der Registry lesen: HKLM:\\SYSTEM\\CurrentControlSet\\Control\\SecureBoot\\State, Wert UEFISecureBootEnabled "
     "(fehlt auf Computern ohne UEFI – vorher Test-Path)"),
    (r"Get-ItemPropertyValue",
     "Get-ItemProperty -Path … -Name … -ErrorAction SilentlyContinue und dann die Eigenschaft lesen (vorher Test-Path)"),
)
_THROW_RX = [(re.compile(r"(?i)\b(?:" + rx + r")\b"), alt) for rx, alt in THROWS]
# member through a variable or an expression ($o.$n, $o.$($n), $o.("x")) - not inside quoted strings ("$a.$b" is text)
_DYN_RX = re.compile(r"[\w)\]}]\.(?:\$|\()")


def _strip_strings(ln):
    """Quoted strings -> '' ('...' literal, "..." with backtick escapes); one linear pass (no regex backtracking on
    long unclosed strings). An unclosed string stays as it is."""
    out = []
    i, n = 0, len(ln)
    while i < n:
        c = ln[i]
        if c not in "'\"":
            out.append(c)
            i += 1
            continue
        j = i + 1
        while j < n and ln[j] != c:
            j += 2 if (c == '"' and ln[j] == "`") else 1
        if j >= n:  # not closed
            out.append(ln[i:])
            break
        out.append("''")
        i = j + 1
    return "".join(out)
# Select-Object -First/-Index (also abbreviated "select", "-f", "-fir", "-ind") - not -Last/-Skip, they read everything
_STOP_RX = re.compile(r"(?i)(?:^\s*|[|;({]\s*)(?:Select-Object|select)\b[^|;]{0,300}?\s-(?:f(?:i(?:r(?:st?)?)?)?|i(?:n(?:d(?:ex?)?)?)?)\b")
STOP_HINT = "das erste Element per Index nehmen: @(Befehl)[0] bzw. @(Befehl)[0..9]"
_lock = threading.Lock()
STATS = {"checks": 0, "with_findings": 0, "words": 0, "modules": 0, "dynamic": 0, "stops": 0, "throws": 0}


def check(text, count=True):
    """[{line, kind (word|module|dynamic|stop), what, hint}] - at most one entry per kind and thing, in text order."""
    out: list = []
    seen: set = set()
    lines = str(text or "").splitlines()
    for no, ln in enumerate(lines, 1):
        low = ln.lower()
        for w in WORDS:
            if w.lower() in low and ("w", w) not in seen:
                seen.add(("w", w))
                out.append({"line": no, "kind": "word", "what": w, "hint": WORD_HINTS.get(w.lower(), "")})
        for rx, name, alt in _MOD_RX:
            m = rx.search(ln)
            if m and ("m", m.group(0).lower()) not in seen and not ln.lstrip().startswith("#"):
                seen.add(("m", m.group(0).lower()))
                out.append({"line": no, "kind": "module", "what": m.group(0), "module": name, "hint": alt})
        if not ln.lstrip().startswith("#"):
            code = _strip_strings(ln.split(" #", 1)[0])
            m = _DYN_RX.search(code)
            if m and ("d", no) not in seen:
                seen.add(("d", no))
                out.append({"line": no, "kind": "dynamic", "what": ln.strip()[:80],
                            "hint": "Eigenschaft ausschreiben ($obj.Name) oder Select-Object -ExpandProperty $name"})
            m = _STOP_RX.search(code)
            if m and ("s", no) not in seen:
                seen.add(("s", no))
                out.append({"line": no, "kind": "stop", "what": m.group(0).lstrip("|;({ \t"), "hint": STOP_HINT})
            for rx, alt in _THROW_RX:
                m = rx.search(code)
                if m and ("t", m.group(0).lower()) not in seen:
                    seen.add(("t", m.group(0).lower()))
                    out.append({"line": no, "kind": "throw", "what": m.group(0), "hint": alt})
    if not count:   # overview of stored code: not an editor check
        return out[:50]
    with _lock:
        STATS["checks"] += 1
        if out:
            STATS["with_findings"] += 1
        for f in out:
            STATS[{"word": "words", "module": "modules", "dynamic": "dynamic", "stop": "stops", "throw": "throws"}[str(f["kind"])]] += 1
    return out[:50]


def stats():
    with _lock:
        return dict(STATS)


def _scripts_of(obj, depth=0):
    """All PowerShell texts in a package structure (steps, nested packages): yields (label, text)."""
    if depth > 8:
        return
    if isinstance(obj, dict):
        if obj.get("type") == "powershell" and isinstance(obj.get("script"), str) and obj["script"].strip():
            yield (obj.get("title") or "PowerShell"), obj["script"]
        for v in obj.values():
            if isinstance(v, (dict, list)):
                yield from _scripts_of(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            yield from _scripts_of(v, depth + 1)


def overview(package_ok=None):
    """2.56.1: own PowerShell code that Windows would log as a warning or error - eigene Scanner, Tools, PowerShell steps
    of packages. Shown in Einstellungen → Wartung and next to the scanners, so a hint does not depend on opening the
    editor. [{kind: scanner|tool|package, id, name, findings: [...]}]; package_ok(pid) filters packages (tenants)."""
    import json as _json
    from . import db
    out = []

    def add(kind, rid, name, texts):
        found: list = []
        for label, text in texts:
            for f in check(text, count=False):
                if len(found) < 20:
                    found.append({**f, "where": label})
        if found:
            out.append({"kind": kind, "id": rid, "name": name, "findings": found})
    for r in db.q("SELECT id, name, target FROM custom_scanners WHERE type='powershell' ORDER BY name"):
        add("scanner", r["id"], r["name"], [("Abfrage", r["target"] or "")])
    try:
        rows = db.q("SELECT id, name, script FROM tools WHERE type='powershell' ORDER BY name")
    except Exception:  # noqa: BLE001 - table of the tools module
        rows = []
    for r in rows:
        add("tool", r["id"], r["name"], [("Skript", r["script"] or "")])
    for r in db.q("SELECT id, name, steps, conditions, tenant_id FROM packages ORDER BY name"):
        if package_ok and not package_ok(r):
            continue
        try:
            data = [_json.loads(r["steps"] or "null"), _json.loads(r["conditions"] or "null")]
        except ValueError:
            continue
        add("package", r["id"], r["name"], list(_scripts_of(data)))
    return out


def overview_counts():
    """Diagnostic package: how much own code has hints - counts only, never names or code."""
    ov = overview()
    kinds: dict = {}
    for o in ov:
        for f in o["findings"]:
            kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
    return {"scanners": sum(1 for o in ov if o["kind"] == "scanner"), "tools": sum(1 for o in ov if o["kind"] == "tool"),
            "packages": sum(1 for o in ov if o["kind"] == "package"), "findings_per_kind": kinds}
