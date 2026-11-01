"""Typprüfung (mypy) mit Ausgangsstand: nur NEUE Funde lassen die Prüfung scheitern.

Der Code ist kaum annotiert; mypy prüft trotzdem die Rümpfe aller Funktionen (check_untyped_defs). Die beim
Einführen vorhandenen Funde (meist Folge fehlender Annotationen, keine Fehler) stehen in tools/mypy-baseline.txt –
ohne Zeilennummern, damit Änderungen an anderer Stelle sie nicht „neu“ machen. Wer einen Fund behebt, nimmt ihn mit
--update aus dem Ausgangsstand.

Aufruf:  python3 tools/typcheck.py            prüfen (Exitcode 1 bei neuen Funden)
         python3 tools/typcheck.py --update   Ausgangsstand neu schreiben (nach Behebungen)
mypy muss installiert sein (CI: feste Version, siehe .github/workflows/test.yml).
"""
import collections
import os
import re
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
BASE = os.path.join(ROOT, "tools", "mypy-baseline.txt")
LINE = re.compile(r"^(?P<file>[^:]+):(?P<line>\d+): error: (?P<msg>.*?)\s+\[(?P<code>[a-z-]+)\]$")


def run():
    r = subprocess.run([sys.executable, "-m", "mypy", "--config-file", os.path.join(ROOT, "mypy.ini"), "app"],
                       cwd=ROOT, capture_output=True, text=True)
    found = []
    for ln in r.stdout.splitlines():
        m = LINE.match(ln.strip())
        if m:
            found.append((m["file"].replace("\\", "/"), m["code"], m["msg"], int(m["line"])))
    if r.returncode not in (0, 1) or (r.returncode == 1 and not found):
        sys.stderr.write(r.stdout + r.stderr)
        raise SystemExit(f"mypy ist nicht gelaufen (Exitcode {r.returncode}).")
    return found


def key(f):
    return f"{f[0]} [{f[1]}] {f[2]}"


def main():
    found = run()
    if "--update" in sys.argv:
        with open(BASE, "w", encoding="utf-8") as fh:
            fh.write("# Ausgangsstand der Typprüfung (tools/typcheck.py) – je Zeile ein Fund ohne Zeilennummer\n")
            for k in sorted(key(f) for f in found):
                fh.write(k + "\n")
        print(f"Ausgangsstand geschrieben: {len(found)} Funde.")
        return 0
    base = collections.Counter()
    if os.path.exists(BASE):
        with open(BASE, encoding="utf-8") as fh:
            base.update(ln.rstrip("\n") for ln in fh if ln.strip() and not ln.startswith("#"))
    now = collections.Counter(key(f) for f in found)
    new = now - base
    gone = base - now
    gh = os.environ.get("GITHUB_ACTIONS") == "true"
    shown = collections.Counter()
    for f in found:
        k = key(f)
        if shown[k] < new[k]:
            shown[k] += 1
            print(f"NEU  {f[0]}:{f[3]}: {f[2]} [{f[1]}]")
            if gh:
                print(f"::error file={f[0]},line={f[3]},title=Typprüfung::{f[2]} [{f[1]}]")
    print(f"\n{len(found)} Funde, davon {sum(new.values())} neu; {sum(gone.values())} behoben "
          f"(Ausgangsstand {sum(base.values())}).")
    if gone:
        print("Behoben – bitte den Ausgangsstand verkleinern: python3 tools/typcheck.py --update")
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main())
