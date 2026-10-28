"""Hilfsfunktionen."""
import datetime
import re
import socket

# Windows PowerShell immer mit vollem Pfad und festen Schaltern starten (64 Bit, ohne Profil/Logo, nie interaktiv,
# Ausführungsrichtlinie umgangen) – sonst meckert Windows je nach Richtlinie/PATH, und jedes Skript müsste es selbst tun.
PS_EXE = r"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"           # Anzeige (Vorschau)
PS_EXE_PS = r'"$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"'     # als PowerShell-Ausdruck
PS_FLAGS = "-NoLogo -NonInteractive -NoProfile -ExecutionPolicy Bypass"
# 2.32.2: Skripte erst ganz normal als Datei starten (-File bzw. & 'datei' – gleiche Exitcodes, $PSScriptRoot, und bei
# AppLocker/WDAC laufen zugelassene Dateien weiter in FullLanguage). Nur wenn Windows die Datei verweigert, wird ihr
# Inhalt geladen: Eine Ausführungsrichtlinie aus der Gruppenrichtlinie (MachinePolicy/UserPolicy) lässt sich mit
# „-ExecutionPolicy Bypass“ nicht übersteuern, und mit RemoteSigned hält Windows eine lokale Datei je nach
# Sicherheitszonen des Kontos (SYSTEM, Dienstkonto) für „fremd“ und verlangt eine Signatur. Geladener Inhalt unterliegt
# der Ausführungsrichtlinie nicht (wie Befehle über WinRM); AMSI, Virenschutz und Constrained Language Mode gelten
# weiter. Invoke-Expression statt [ScriptBlock]::Create – das ist im Constrained Language Mode erlaubt.
# „exit 0“ am Ende: gleicher Exitcode wie bei -File (ohne exit 0, exit N → N, Fehler → 1).
# 2.32.5: $PKLoadOk setzt die erste Zeile der Ladedatei – kann PowerShell den Inhalt nicht übersetzen (Syntaxfehler),
# läuft keine Zeile und der Exitcode ist 1 (wie bei -File) statt 0
PS_LOAD_PRE = "-Command \"$PKLoadOk = $false; Invoke-Expression ((Get-Content -LiteralPath '"
PS_LOAD_POST = "' -Raw -Encoding UTF8)); if (-not $PKLoadOk) { exit 1 }; exit 0\""
# dieselben Teile zum Einbetten in einen PowerShell-String in einfachen Anführungszeichen ('…' + $pfad + '…')
PS_LOAD_PRE_Q = PS_LOAD_PRE.replace("'", "''")
PS_LOAD_POST_Q = PS_LOAD_POST.replace("'", "''")
# Start des Rahmens pk_run.ps1 (geplante Aufgabe / eigener Prozess): erst & 'datei'; nur wenn genau diese Datei wegen
# Ausführungsrichtlinie/Signatur verweigert wird (UnauthorizedAccess), ihren Inhalt laden. Der Exitcode ist hier egal
# (das Ergebnis steht in pk_exit.txt). Teile zum Einbetten: '…' + $pfad + '…' + $pfad + '…' + $pfad + '…'
PS_START_PARTS = ("-Command \"try { & '", "' } catch { if ($_.FullyQualifiedErrorId -eq 'UnauthorizedAccess' -and "
                  "([string]$_).Contains('pk_run.ps1')) { Invoke-Expression ((Get-Content -LiteralPath '",
                  "' -Raw -Encoding UTF8)) } else { throw } }\"")


def ps_start_expr(var):
    """PowerShell-Ausdruck mit den Argumenten für powershell.exe, die den Rahmen an Pfad var (PS-Ausdruck) starten."""
    q = [x.replace("'", "''") for x in PS_START_PARTS]
    return f"('{PS_FLAGS} {q[0]}' + {var} + '{q[1]}' + {var} + '{q[2]}')"


def ps_load_arg(path):
    """Argumente für powershell.exe, die das Skript an path laden (path ohne einfaches Anführungszeichen)."""
    return f"{PS_FLAGS} {PS_LOAD_PRE}{path}{PS_LOAD_POST}"


def ps_file_arg(path, args=""):
    """Argumente für powershell.exe -File (Vorschau)."""
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


# --- Versionen -----------------------------------------------------------
def _vparts(v):
    v = (v or "").strip().lower()
    v = re.sub(r"^v(?=\d)", "", v)
    # Zusätze ohne Versionsbedeutung: „1.2.3 (x64)“, „1.0+build5“, „2.4 [de]“ – sonst gälte 1.2.3 (x64) < 1.2.3
    v = re.sub(r"\s*[\(\[].*?[\)\]]", "", v)
    v = v.split("+", 1)[0].strip()
    v = re.sub(r"\s+(x64|x86|amd64|arm64|64-bit|32-bit|64bit|32bit)$", "", v)
    # „2.29.6-s3“: drittes Sicherheitsupdate von 2.29.6 – höher als 2.29.6, niedriger als 2.29.7
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
    """-1 wenn a<b, 0 gleich, 1 wenn a>b."""
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
    """Sicherer PowerShell-String in einfachen Anführungszeichen."""
    # PowerShell wertet auch typografische Apostrophe (U+2018–U+201B) als einfache Anführungszeichen
    t = str(s if s is not None else "")
    for ch in ("'", "\u2018", "\u2019", "\u201a", "\u201b"):
        t = t.replace(ch, ch + ch)
    return "'" + t + "'"


# --- Netzwerk ------------------------------------------------------------
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


# Kodierung für .cmd-Dateien: cmd.exe liest Batchdateien in der Codepage der Konsole. Der Wrapper stellt die
# Konsole auf UTF-8 um, über WinRM ohne Konsole gilt die OEM-Codepage – daher zur Laufzeit ermitteln (UTF-8 ohne BOM).
PS_CMD_ENCODING = ("$(try { $pkE = [Console]::OutputEncoding; if ($pkE.CodePage -eq 65001) { New-Object System.Text.UTF8Encoding $false } "
                   "else { $pkE } } catch { [System.Text.Encoding]::GetEncoding([System.Globalization.CultureInfo]::CurrentCulture.TextInfo.OEMCodePage) })")


def first_line(text, default=""):
    """Erste nicht leere Zeile (ohne Fehler bei leerem Text)."""
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return default


# --- Reguläre Ausdrücke aus Benutzereingaben -------------------------------------------------------
_NESTED_Q = re.compile(r"\((?:[^()\\]|\\.)*[*+](?:[^()\\]|\\.)*\)\s*(?:[*+]|\{\d*,)")
_ALT_Q = re.compile(r"\((?:[^()\\]|\\.)*\|(?:[^()\\]|\\.)*\)\s*(?:[*+]|\{\d*,)")
REGEX_MAX = 200


def _nested_repeat(pattern):
    """True, wenn eine Wiederholung (+, *, {n}, {n,m}) etwas enthält, das selbst wiederholt oder verzweigt wird –
    am Syntaxbaum geprüft, also auch bei inneren Klammern wie ((a+))+ oder festen Zahlen wie (.*a){12}."""
    try:
        try:
            from re import _parser as sp, _constants as sc  # Python 3.11+
        except ImportError:  # pragma: no cover
            import sre_parse as sp
            import sre_constants as sc
        tree = sp.parse(pattern)
    except Exception:
        return False  # ungültig – meldet danach re.compile
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
    """Grund, warum ein Muster abgelehnt wird (zu lang, katastrophales Backtracking), sonst None.
    Python-RegEx haben keine Zeitgrenze – ein Muster wie (a+)+$ kann den ganzen Server lahmlegen."""
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
