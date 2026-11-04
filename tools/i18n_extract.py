"""Collect German texts from app.js/remote.js/HTML and the Python modules.
Usage: python3 tools/i18n_extract.py [output.json]  – also lists texts that are missing in app/i18n/en.json."""
import ast, json, re, sys, glob

import os
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app")

def js_strings(src):
    return _scan(src, 0, False)[0]


def _scan(src, i, until_brace):
    """Tokenizes from i; with until_brace up to the matching closing brace. -> (strings, end)"""
    out = []
    n = len(src)
    prev = ""
    depth = 0
    def regex_allowed():
        return prev == "" or prev in "(,=:[!&|?{};+-*%<>~^" or prev in ("return", "typeof", "case")
    while i < n:
        c = src[i]
        if c in " \t\r\n":
            i += 1; continue
        if src.startswith("//", i):
            j = src.find("\n", i); i = n if j < 0 else j; continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2); i = n if j < 0 else j + 2; continue
        if until_brace and c == "{":
            depth += 1; prev = c; i += 1; continue
        if until_brace and c == "}":
            if depth == 0:
                return out, i + 1
            depth -= 1; prev = c; i += 1; continue
        if c in "'\"":
            j, buf = i + 1, []
            while j < n and src[j] != c:
                if src[j] == "\\":
                    buf.append(src[j:j + 2]); j += 2; continue
                buf.append(src[j]); j += 1
            raw = "".join(buf)
            try:
                val = json.loads('"' + raw.replace('"', '\\"').replace("\\'", "'").replace('\\\\"', '\\"') + '"')
            except Exception:
                val = raw
            out.append(("exact", val)); i = j + 1; prev = "str"; continue
        if c == "`":
            j, k, cur = i + 1, 0, []
            while j < n and src[j] != "`":
                if src[j] == "\\":
                    cur.append(src[j:j + 2]); j += 2; continue
                if src.startswith("${", j):
                    sub, j = _scan(src, j + 2, True)
                    out.extend(sub)
                    cur.append("{%d}" % k); k += 1; continue
                cur.append(src[j]); j += 1
            raw = "".join(cur)
            try:
                val = json.loads('"' + raw.replace('"', '\\"').replace("\\`", "`").replace("\\$", "$") + '"')
            except Exception:
                val = raw
            out.append(("tpl" if k else "exact", val)); i = j + 1; prev = "str"; continue
        if c == "/" and regex_allowed():
            j, cls = i + 1, False
            while j < n:
                if src[j] == "\\": j += 2; continue
                if src[j] == "[": cls = True
                elif src[j] == "]": cls = False
                elif src[j] == "/" and not cls: break
                elif src[j] == "\n": break
                j += 1
            j += 1
            while j < n and src[j].isalpha(): j += 1
            i = j; prev = "re"; continue
        m = re.match(r"[A-Za-z_$][\w$]*", src[i:i + 40])
        if m:
            prev = m.group(0); i += len(prev); continue
        m = re.match(r"\d[\d.eE_xXa-fA-F]*", src[i:i + 40])
        if m:
            prev = "num"; i += len(m.group(0)); continue
        prev = c; i += 1
    return out, i


GERMAN_HINT = re.compile(r"[äöüÄÖÜß]|\b(der|die|das|und|oder|nicht|mit|für|ein|eine|wird|werden|ist|sind|nach|bei|von|zu|auf|im|aus|kein|keine|alle|neu|Computer|Paket|Sammlung|Scan|Fehler|Datei|Benutzer)\b", re.I)

# Single lower-case German words (otherwise they look like identifiers and would be dropped)
GERMAN_WORDS = set("""ab abgelaufen abgelehnt absteigend aktiv aktuell alle alt am angemeldet angenommen aufsteigend aus ausgenommen
ausgeschnitten ausstehend automatisch beenden beendet bereit bis deaktiviert ein eingebaut eingeschaltet erfolgreich
fehlgeschlagen fehlt gesetzt getrennt ignoriert immer inaktiv kopiert letzter manuell merken nach neueste nie selbstsigniert
sofort starten teilweise um veraltet verbunden verschoben versteckt von wartet offen geplant läuft heute gestern""".split())


def keep(s):
    t = s.strip()
    if t in ("ja", "nein") or t in GERMAN_WORDS:
        return True
    if len(t) < 2 or len(t) > 1500:
        return False
    if not re.search(r"[A-Za-zÄÖÜäöüß]{2}", t):
        return False
    if re.match(r"^(/api/|#/|https?://|[a-z]+[A-Z]\w*$|[a-z0-9_.#-]+$|[A-Z_0-9]+$|M\d|[\w-]+\.(js|css|svg|png|json|html|msi|exe)$)", t):
        return False  # paths, identifiers, classes, constants, SVG
    if re.search(r"(\bSELECT\b|\bDELETE FROM\b|\bCREATE INDEX\b|^\^\[|Join-Path|PK-Run|PK-Exp|LASTEXITCODE|PKEXIT|^\s*\$\w+ = |^try \{|ForEach-Object|\} else \{|^FullLanguage$|\bINSERT\b|\bUPDATE\b.*\bSET\b|\$_\.|Get-|Set-|New-Item|\$env:|-ErrorAction|^\s*[a-z_][\w.]*\(|=>|&&|\|\||^PK_[A-Z_]+[:=]?$|PKJSON)", t):
        return False
    if t[0].isupper() or GERMAN_HINT.search(t) or " " in t:
        if re.match(r"^[a-z][a-z0-9_-]*( [a-z][a-z0-9_-]*)*$", t) and not GERMAN_HINT.search(t) and t not in ("ja", "nein"):
            return False  # e.g. CSS class lists
        return True
    return False


items = {}
for f in ["static/app.js", "static/remote.js", "static/kiosk.js"]:
    for kind, s in js_strings(open(f"{ROOT}/{f}", encoding="utf-8").read()):
        if keep(s):
            items.setdefault((kind, s), f)
for f in ["static/index.html", "static/remote.html", "static/kiosk.html"]:
    html = open(f"{ROOT}/{f}", encoding="utf-8").read()
    for s in re.findall(r">([^<>{}]+)<", html) + re.findall(r'(?:placeholder|title|aria-label|alt)="([^"]+)"', html):
        if keep(s.strip()):
            items.setdefault(("exact", s.strip()), f)

# Labels of the default scanners
for m in re.finditer(r"^# @module \w+ \| (.+)$", open(f"{ROOT}/scan_modules.ps1", encoding="utf-8").read(), re.M):
    items.setdefault(("exact", m.group(1).strip()), "scan_modules.ps1")

# Python: constants and f-strings (without docstrings)
for f in glob.glob(f"{ROOT}/*.py"):
    tree = ast.parse(open(f, encoding="utf-8").read())
    doc_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)) and node.body and \
                isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
            doc_nodes.add(id(node.body[0].value))
    inside_fstring = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for v in node.values:
                inside_fstring.add(id(v))
            parts, k = [], 0
            for v in node.values:
                if isinstance(v, ast.Constant):
                    parts.append(str(v.value).replace("{", "{{").replace("}", "}}") if False else str(v.value))
                else:
                    parts.append("{%d}" % k); k += 1
            s = "".join(parts)
            if keep(s):
                items.setdefault(("tpl" if k else "exact", s), f.split("/")[-1])
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in doc_nodes \
                and id(node) not in inside_fstring:
            s = node.value
            if "\n" in s.strip() and len(s) > 200:
                continue  # scripts
            if keep(s):
                items.setdefault(("exact", s), f.split("/")[-1])

out = [{"s": s, "kind": k, "src": src} for (k, s), src in sorted(items.items(), key=lambda x: (x[1], x[0][1]))]
if len(sys.argv) > 1 and sys.argv[1].startswith("-"):
    print(__doc__)
    sys.exit(0)  # "--help" or similar is not a file name (otherwise a file "--help" would be created in the project)
json.dump(out, open(sys.argv[1] if len(sys.argv) > 1 else "strings.json", "w"), ensure_ascii=False, indent=0)
print(len(out), "texts;", sum(1 for x in out if x["kind"] == "tpl"), "templates;",
      {s: sum(1 for x in out if x["src"] == s) for s in sorted({x["src"] for x in out})})

known = json.load(open(os.path.join(ROOT, "i18n", "en.json"), encoding="utf-8"))
new = [x["s"] for x in out if x["s"] not in known]
print(len(new), "texts without translation" + (":" if new else "."))
for t in new[:200]:
    print("  ", repr(t)[:120])
