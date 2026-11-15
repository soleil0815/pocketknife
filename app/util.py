"""Helper functions."""
import datetime
import re
import socket

# Always start Windows PowerShell with the full path and fixed switches (64-bit, no profile/logo, never interactive,
# execution policy bypassed) - otherwise Windows complains depending on policy/PATH, and every script would have to do it itself.
PS_EXE = r"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"           # display (preview)
PS_EXE_PS = r'"$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"'     # as a PowerShell expression
# 2.51.0: "Unrestricted" instead of "Bypass" - for the local files POCKET KNIFE writes (no internet zone mark) both run
# them the same way, but "Bypass" on a command line or in a script is a classic alarm signal for monitoring software
# (PowerShell's script block logging writes every script containing the word as a warning, event 4104)
PS_FLAGS = "-NoLogo -NonInteractive -NoProfile -ExecutionPolicy Unrestricted"
# 2.32.2: Start scripts first quite normally as a file (-File or & 'file' - same exit codes, $PSScriptRoot, and with
# AppLocker/WDAC allowed files continue to run in FullLanguage). Only if Windows refuses the file is its
# content loaded: an execution policy from group policy (MachinePolicy/UserPolicy) cannot be overridden with
# "-ExecutionPolicy Bypass", and with RemoteSigned Windows considers a local file, depending on the
# security zones of the account (SYSTEM, service account), "foreign" and demands a signature. Loaded content is not subject to
# the execution policy (like commands via WinRM); AMSI, antivirus and Constrained Language Mode still apply.
# Invoke-Expression instead of [ScriptBlock]::Create - that is allowed in Constrained Language Mode.
# "exit 0" at the end: same exit code as with -File (without exit 0, exit N → N, error → 1).
# 2.32.5: $PKLoadOk is set by the first line of the load file - if PowerShell cannot translate the content (syntax error),
# no line runs and the exit code is 1 (as with -File) instead of 0
PS_LOAD_PRE = "-Command \"$PKLoadOk = $false; Invoke-Expression ((Get-Content -LiteralPath '"
PS_LOAD_POST = "' -Raw -Encoding UTF8)); if (-not $PKLoadOk) { exit 1 }; exit 0\""
# the same parts for embedding in a PowerShell string in single quotes ('...' + $path + '...')
PS_LOAD_PRE_Q = PS_LOAD_PRE.replace("'", "''")
PS_LOAD_POST_Q = PS_LOAD_POST.replace("'", "''")
# Start of the frame pk_run.ps1 (scheduled task / own process): first & 'file'; only if exactly this file is refused because of
# execution policy/signature (UnauthorizedAccess), load its content. The exit code does not matter here
# (the result is in pk_exit.txt). Parts for embedding: '...' + $path + '...' + $path + '...' + $path + '...'
PS_START_PARTS = ("-Command \"try { & '", "' } catch { if ($_.FullyQualifiedErrorId -eq 'UnauthorizedAccess' -and "
                  "([string]$_).Contains('pk_run.ps1')) { Invoke-Expression ((Get-Content -LiteralPath '",
                  "' -Raw -Encoding UTF8)) } else { throw } }\"")


# 2.51.0: scheduled tasks straight through their CIM classes - the cmdlets (Get-/Register-/Start-ScheduledTask ...)
# load the generated "ScheduledTasks" module in every WinRM session, and PowerShell logs its code as some 18 warnings
# (event 4104) each time. Constrained-Language-safe: only CIM cmdlets and strings. Task path like '\POCKETKNIFE\'.
PS_TASKS = r"""$PKTns = 'Root/Microsoft/Windows/TaskScheduler'
$PKTState = @{ 0 = 'Unknown'; 1 = 'Disabled'; 2 = 'Queued'; 3 = 'Ready'; 4 = 'Running' }
function PK-TaskList([string]$TaskPath, [string]$Like = '*') {
  @(Get-CimInstance -Namespace $PKTns -ClassName MSFT_ScheduledTask -Filter ("TaskPath = '" + ($TaskPath -replace '\\', '\\' -replace "'", "\'") + "'") -ErrorAction SilentlyContinue |
    Where-Object { $_.TaskName -like $Like })
}
function PK-TaskState($Task) { $s = $PKTState[[int]$Task.State]; if ($s) { $s } else { "" + $Task.State } }
function PK-TaskInfo([string]$TaskName, [string]$TaskPath) {
  $r = Invoke-CimMethod -Namespace $PKTns -ClassName PS_ScheduledTask -MethodName GetInfoByName -Arguments @{ TaskName = $TaskName; TaskPath = $TaskPath } -ErrorAction SilentlyContinue
  if ($r) { $r.cmdletOutput }
}
function PK-TaskStart([string]$TaskName, [string]$TaskPath) {
  $null = Invoke-CimMethod -Namespace $PKTns -ClassName PS_ScheduledTask -MethodName StartByPath -Arguments @{ TaskName = $TaskName; TaskPath = $TaskPath } -ErrorAction Stop
}
function PK-TaskStop([string]$TaskName, [string]$TaskPath) {
  $null = Invoke-CimMethod -Namespace $PKTns -ClassName PS_ScheduledTask -MethodName StopByPath -Arguments @{ TaskName = $TaskName; TaskPath = $TaskPath } -ErrorAction SilentlyContinue
}
function PK-TaskRemove([string]$TaskName, [string]$TaskPath) {
  PK-TaskList $TaskPath | Where-Object { $_.TaskName -eq $TaskName } | Remove-CimInstance -ErrorAction SilentlyContinue
}
function PK-Xml([string]$Text) { $Text -replace '&', '&amp;' -replace '<', '&lt;' -replace '>', '&gt;' -replace '"', '&quot;' }
# Principal: 'system', 'interactive' (logged-on user, highest rights), 'limited' (logged-on user, normal rights) or
# 'password' (account with a password - given to PK-TaskRegister, Windows stores it while the task exists)
function PK-TaskXml([string]$Execute, [string]$Argument, [string]$WorkingDirectory, [string]$Principal, [string]$UserId, [int]$Minutes) {
  $pr = switch ($Principal) {
    'system' { '<UserId>S-1-5-18</UserId><RunLevel>HighestAvailable</RunLevel>' }
    'interactive' { '<UserId>' + (PK-Xml $UserId) + '</UserId><LogonType>InteractiveToken</LogonType><RunLevel>HighestAvailable</RunLevel>' }
    'limited' { '<UserId>' + (PK-Xml $UserId) + '</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel>' }
    'password' { '<UserId>' + (PK-Xml $UserId) + '</UserId><LogonType>Password</LogonType><RunLevel>HighestAvailable</RunLevel>' }
    default { throw ('PK-TaskXml: unknown principal ' + $Principal) } }
  '<?xml version="1.0" encoding="UTF-16"?><Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">' +
  '<Principals><Principal id="Author">' + $pr + '</Principal></Principals><Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>' +
  '<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>' +
  '<ExecutionTimeLimit>PT' + $Minutes + 'M</ExecutionTimeLimit><Enabled>true</Enabled></Settings><Actions Context="Author"><Exec>' +
  '<Command>' + (PK-Xml $Execute) + '</Command>' + $(if ($Argument) { '<Arguments>' + (PK-Xml $Argument) + '</Arguments>' }) +
  $(if ($WorkingDirectory) { '<WorkingDirectory>' + (PK-Xml $WorkingDirectory) + '</WorkingDirectory>' }) + '</Exec></Actions></Task>'
}
function PK-TaskRegister([string]$TaskName, [string]$TaskPath, [string]$Xml, [string]$User, [string]$Password) {
  $a = @{ Xml = $Xml; TaskName = $TaskName; TaskPath = $TaskPath; Force = $true }
  if ($User) { $a.User = $User; $a.Password = $Password }
  $null = Invoke-CimMethod -Namespace $PKTns -ClassName PS_ScheduledTask -MethodName RegisterByXml -Arguments $a -ErrorAction Stop
}
"""


def ps_start_expr(var):
    """PowerShell expression with the arguments for powershell.exe that start the frame at path var (PS expression)."""
    q = [x.replace("'", "''") for x in PS_START_PARTS]
    return f"('{PS_FLAGS} {q[0]}' + {var} + '{q[1]}' + {var} + '{q[2]}')"


def ps_load_arg(path):
    """Arguments for powershell.exe that load the script at path (path without a single quote)."""
    return f"{PS_FLAGS} {PS_LOAD_PRE}{path}{PS_LOAD_POST}"


def ps_file_arg(path, args=""):
    """Arguments for powershell.exe -File (preview)."""
    return f'{PS_FLAGS} -File "{path}" {args}'.rstrip()


WINGET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-_+]{0,127}$")


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else None


def parse(ts):
    if not ts:
        return None
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(ts[:19], f)
        except ValueError:
            continue
    return None


def ago(**kw):
    return fmt(datetime.datetime.now() - datetime.timedelta(**kw))


# --- Versions -----------------------------------------------------------
def _vparts(v):
    v = (v or "").strip().lower()
    v = re.sub(r"^v(?=\d)", "", v)
    # Additions without version meaning: "1.2.3 (x64)", "1.0+build5", "2.4 [de]" - otherwise 1.2.3 (x64) < 1.2.3 would hold
    v = re.sub(r"\s*[\(\[].*?[\)\]]", "", v)
    v = v.split("+", 1)[0].strip()
    v = re.sub(r"\s+(x64|x86|amd64|arm64|64-bit|32-bit|64bit|32bit)$", "", v)
    # "2.29.6-s3": third security update of 2.29.6 - higher than 2.29.6, lower than 2.29.7
    sec = re.match(r"^(.*\d)-s(\d+)$", v)
    if sec:
        v = sec.group(1)
    out = []
    for t in re.findall(r"\d+|[a-z]+", v):
        out.append((0, int(t)) if t.isdigit() else (-1, t))
    if sec:
        out += [(0, 0)] * max(0, 4 - len(out)) + [(1, int(sec.group(2)))]
    return out


def vcmp(a, b):
    """-1 if a<b, 0 equal, 1 if a>b."""
    pa, pb = _vparts(a), _vparts(b)
    n = max(len(pa), len(pb))
    pa += [(0, 0)] * (n - len(pa))
    pb += [(0, 0)] * (n - len(pb))
    for x, y in zip(pa, pb):
        if x != y:
            return -1 if x < y else 1
    return 0


def is_outdated(installed, latest):
    if not installed or not latest:
        return False
    if latest.strip().lower() in ("unknown", "latest", ""):
        return False
    if not re.search(r"\d", installed):
        return False
    return vcmp(installed, latest) < 0


# --- PowerShell ----------------------------------------------------------
def psq(s):
    """Safe PowerShell string in single quotes."""
    # PowerShell also treats typographic apostrophes (U+2018–U+201B) as single quotes
    t = str(s if s is not None else "")
    for ch in ("'", "\u2018", "\u2019", "\u201a", "\u201b"):
        t = t.replace(ch, ch + ch)
    return "'" + t + "'"


# --- Network ------------------------------------------------------------
def tcp_open(host, port, timeout=1.5):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def like_pattern(value, mode="contains"):
    v = (value or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    v = v.replace("*", "%")
    if mode == "contains":
        return f"%{v}%"
    if mode == "starts":
        return f"{v}%"
    if mode == "ends":
        return f"%{v}"
    return v


# 2.38: resolve %VAR% without [Environment]::ExpandEnvironmentVariables (not allowed in Constrained Language Mode).
# Unknown variables are left as they are (as on Windows).
PS_EXPAND = r"""function PK-Exp([string]$s) {
  $o = ''
  while ($s -match '^([^%]*)%([^%]+)%(.*)$') {
    $pre = $Matches[1]; $n = $Matches[2]; $rest = $Matches[3]
    $v = Get-Item -LiteralPath ('env:' + $n) -ErrorAction SilentlyContinue
    if ($v) { $o += $pre + $v.Value; $s = $rest } else { $o += $pre + '%' + $n; $s = '%' + $rest }
  }
  $o + $s
}
"""


def ps_write(path_expr, text, cmd=False):
    """2.38: PowerShell code that writes text to a file - without .NET calls, so also in Constrained Language
    Mode (software restriction, AppLocker, App Control). path_expr: PowerShell expression for the path.
    Scripts (.ps1): UTF-8 with BOM (Windows PowerShell then reads umlauts correctly).
    cmd=True (.cmd): cmd.exe reads batch files in the console's code page - UTF-8 without BOM if the console is set to 65001
    (bytes ready-made from POCKET KNIFE, otherwise a BOM would precede "@echo off"), else the OEM code page."""
    t = str(text or "")
    if not cmd:
        return f"Set-Content -LiteralPath ({path_expr}) -Value {psq(t)} -Encoding UTF8 -NoNewline\n"
    raw = t.encode("utf-8")
    by = ",".join(str(b) for b in raw) if raw else ""
    utf8 = f"Set-Content -LiteralPath ({path_expr}) -Encoding Byte -Value ([byte[]]@({by}))" if raw else \
        f"Set-Content -LiteralPath ({path_expr}) -Value '' -NoNewline"
    return (f"if ((& chcp.com) -match '65001') {{ {utf8} }} "
            f"else {{ Set-Content -LiteralPath ({path_expr}) -Value {psq(t)} -Encoding Oem -NoNewline }}\n")


def first_line(text, default=""):
    """First non-empty line (no error for empty text)."""
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return default


# --- Regular expressions from user input -------------------------------------------------------
_NESTED_Q = re.compile(r"\((?:[^()\\]|\\.)*[*+](?:[^()\\]|\\.)*\)\s*(?:[*+]|\{\d*,)")
_ALT_Q = re.compile(r"\((?:[^()\\]|\\.)*\|(?:[^()\\]|\\.)*\)\s*(?:[*+]|\{\d*,)")
REGEX_MAX = 200


def _nested_repeat(pattern):
    """True if a repetition (+, *, {n}, {n,m}) contains something that is itself repeated or branched -
    checked on the syntax tree, so also with inner parentheses like ((a+))+ or fixed numbers like (.*a){12}."""
    try:
        try:
            from re import _parser as sp, _constants as sc  # Python 3.11+
        except ImportError:  # pragma: no cover
            import sre_parse as sp
            import sre_constants as sc
        tree = sp.parse(pattern)
    except Exception:
        return False  # invalid - re.compile reports it afterwards
    reps = {sc.MAX_REPEAT, sc.MIN_REPEAT} | ({sc.POSSESSIVE_REPEAT} if hasattr(sc, "POSSESSIVE_REPEAT") else set())

    def items(sub):
        for op, av in sub:
            yield op, av
            if op in reps:
                yield from items(av[2])
            elif op == sc.SUBPATTERN:
                yield from items(av[-1])
            elif op == sc.BRANCH:
                for b in av[1]:
                    yield from items(b)
            elif op in (sc.ASSERT, sc.ASSERT_NOT):
                yield from items(av[1])

    for op, av in items(tree):
        if op in reps and av[1] > 1:
            for op2, av2 in items(av[2]):
                if (op2 in reps and av2[1] > 1) or op2 == sc.BRANCH:
                    return True
    return False


def regex_problem(pattern):
    """Reason why a pattern is rejected (too long, catastrophic backtracking), otherwise None.
    Python regexes have no time limit - a pattern like (a+)+$ can paralyze the whole server."""
    p = pattern or ""
    if len(p) > REGEX_MAX:
        return f"höchstens {REGEX_MAX} Zeichen"
    if _NESTED_Q.search(p) or _ALT_Q.search(p) or _nested_repeat(p):
        return "verschachtelte Wiederholungen wie (a+)+ oder (a|b)* sind nicht erlaubt (Gefahr sehr langer Laufzeit)"
    if re.search(r"\\[1-9]", p):
        return "Rückverweise (\\1 …) sind nicht erlaubt"
    try:
        re.compile(p)
    except re.error as e:
        return str(e)
    return None
