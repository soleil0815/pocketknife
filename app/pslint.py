"""2.52.0: hints for own PowerShell code (package steps, tools, "Befehl ausführen", own scanners, .ps1 files in the
repository editor): what PowerShell's script block logging writes as a WARNING (event 4104) - monitoring software
(SIEM, Defender for Endpoint) can raise alerts on that. Only hints, never a block: the code runs as it is.

Three things trigger it:
1. a word of PowerShell's list of "suspicious" content anywhere in the text - even in a comment or a string, and also as a
   part of a longer word (case-insensitive: "CimClassMethods" contains "Methods")
2. a member named through a variable ($obj.$name) - PowerShell rates that as obfuscation
3. a cmdlet of a generated (CDXML) Windows module - loading the module writes its own code as a series of warnings

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
    (r"(Get|Set|New|Remove|Initialize|Clear|Format|Resize|Repair|Optimize|Mount|Dismount)-(Disk|PhysicalDisk|Volume|Partition|StoragePool|VirtualDisk)\b",
     "Datenträger", "Get-CimInstance Win32_DiskDrive/Win32_Volume, diskpart, defrag.exe, chkdsk"),
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
# member through a variable or an expression ($o.$n, $o.$($n), $o.("x")) - not inside quoted strings ("$a.$b" is text)
_DYN_RX = re.compile(r"[\w)\]}]\.(?:\$|\()")
_STR_RX = re.compile(r"'[^']*'|\"(?:[^\"`]|`.)*\"")
_lock = threading.Lock()
STATS = {"checks": 0, "with_findings": 0, "words": 0, "modules": 0, "dynamic": 0}


def check(text):
    """[{line, kind (word|module|dynamic), what, hint}] - at most one entry per kind and thing, in text order."""
    out, seen = [], set()
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
            m = _DYN_RX.search(_STR_RX.sub("''", ln.split(" #", 1)[0]))
            if m and ("d", no) not in seen:
                seen.add(("d", no))
                out.append({"line": no, "kind": "dynamic", "what": ln.strip()[:80],
                            "hint": "Eigenschaft ausschreiben ($obj.Name) oder Select-Object -ExpandProperty $name"})
    with _lock:
        STATS["checks"] += 1
        if out:
            STATS["with_findings"] += 1
        for f in out:
            STATS[{"word": "words", "module": "modules", "dynamic": "dynamic"}[f["kind"]]] += 1
    return out[:50]


def stats():
    with _lock:
        return dict(STATS)
