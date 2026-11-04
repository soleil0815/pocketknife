"""Type check (mypy) with a baseline: only NEW findings make the check fail.

The code is hardly annotated; mypy still checks the bodies of all functions (check_untyped_defs). The findings present
when this was introduced (mostly a consequence of missing annotations, not bugs) are in tools/mypy-baseline.txt -
without line numbers, so changes elsewhere do not make them "new". Whoever fixes a finding removes it from the
baseline with --update.

Usage:  python3 tools/typcheck.py            check (exit code 1 on new findings)
        python3 tools/typcheck.py --update   rewrite the baseline (after fixes)
mypy must be installed (CI: pinned version, see .github/workflows/test.yml).
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
        raise SystemExit(f"mypy did not run (exit code {r.returncode}).")
    return found


def key(f):
    return f"{f[0]} [{f[1]}] {f[2]}"


def main():
    found = run()
    if "--update" in sys.argv:
        with open(BASE, "w", encoding="utf-8") as fh:
            fh.write("# Baseline of the type check (tools/typcheck.py) – one finding per line, without line number\n")
            for k in sorted(key(f) for f in found):
                fh.write(k + "\n")
        print(f"Baseline written: {len(found)} findings.")
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
            print(f"NEW  {f[0]}:{f[3]}: {f[2]} [{f[1]}]")
            if gh:
                print(f"::error file={f[0]},line={f[3]},title=Type check::{f[2]} [{f[1]}]")
    print(f"\n{len(found)} findings, {sum(new.values())} of them new; {sum(gone.values())} fixed "
          f"(baseline {sum(base.values())}).")
    if gone:
        print("Fixed – please shrink the baseline: python3 tools/typcheck.py --update")
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main())
