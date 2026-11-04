#!/usr/bin/env python3
"""POCKET KNIFE – prepare a security update (called by the weekly workflow, after tools/pin-versions.sh --update).

Counts the security update up - the program version stays: 2.29.6 → 2.29.6-s1 → 2.29.6-s2 ("same program,
only new packages"; a new program version starts again without -s) and enters the version in CHANGELOG.md and app/news.json
- the release workflow requires both. What has changed, the script reads from the pinned versions
(base image, Debian and Python packages) before and after the update.

Usage: python3 tools/security-release.py <folder with the previous state>   (e.g. a git worktree of HEAD)
Prints the new version.
"""
import datetime
import json
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def _read(base, rel):
    try:
        with open(os.path.join(base, rel), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def _pins(base):
    """{name: version} from requirements.lock, docker/apt-*.txt and the base image in the Dockerfile."""
    out = {}
    for rel, sep in (("requirements.lock", "=="), ("docker/apt-runtime.txt", "="), ("docker/apt-build.txt", "=")):
        for line in _read(base, rel).splitlines():
            line = line.strip()
            if not line or line.startswith("#") or sep not in line:
                continue
            n, _, v = line.partition(sep)
            out.setdefault(n.strip().lower(), v.strip())
    m = re.search(r"^ARG PYTHON_IMAGE=(\S+)", _read(base, "Dockerfile"), re.M)
    if m:
        out["base image"] = m.group(1).split("@")[0]
        out["_digest"] = m.group(1).partition("@")[2]
    return out


def bump(v):
    v = v.strip()
    m = re.match(r"^(\d+(?:\.\d+){1,3})(?:-s(\d+))?$", v)
    if not m:
        raise ValueError(f"VERSION \"{v}\" is not a valid version number")
    return f"{m.group(1)}-s{int(m.group(2) or 0) + 1}"


def changes(old_dir, new_dir=ROOT):
    a, b = _pins(old_dir), _pins(new_dir)
    out = []
    for k in sorted(set(a) | set(b), key=str.lower):
        if k.startswith("_"):
            continue
        if a.get(k) != b.get(k):
            out.append((k, a.get(k) or "–", b.get(k) or "–"))
    if a.get("_digest") != b.get("_digest") and not any(k == "base image" for k, _o, _n in out):
        out.insert(0, ("base image", "previous state", "new state (security updates from Debian/Python)"))
    return out


def main(old_dir):
    vf = os.path.join(ROOT, "VERSION")
    old = open(vf, encoding="utf-8").read().strip()
    new = bump(old)
    ch = changes(old_dir)
    today = datetime.date.today()
    lines = [f"## pocketknife-{new} ({today.strftime('%d.%m.%Y')})",
             "- **Weekly security update** (built automatically): base image, Debian and Python packages brought up to "
             "date, rebuilt without cache, self-test and vulnerability scan (Trivy) passed. "
             f"The program itself is unchanged (program state {new.split('-s')[0]})"]
    if ch:
        shown = ", ".join(f"`{k}` {o} → {n}" for k, o, n in ch[:25])
        lines.append(f"- Changed ({len(ch)}): {shown}" + (f" … (+{len(ch) - 25} more)" if len(ch) > 25 else ""))
    entry = "\n".join(lines) + "\n\n"
    cf = os.path.join(ROOT, "CHANGELOG.md")
    s = open(cf, encoding="utf-8").read()
    # New entries go above the newest one (below the title and the note on the language of older entries)
    m = re.search(r"(?m)^## pocketknife-", s)
    i = m.start() if m else len(s)
    open(cf, "w", encoding="utf-8").write(s[:i] + entry + s[i:])
    nf = os.path.join(ROOT, "app", "news.json")
    d = json.load(open(nf, encoding="utf-8"))
    d["versions"].insert(0, {"version": new, "date": today.isoformat(), "items": [{
        "kind": "security", "audience": "admin",
        "de": f"Wöchentliches Sicherheitsupdate: Basis-Image und Pakete aktualisiert ({len(ch)} Änderungen), geprüft mit "
              "Selbsttest und Trivy. Keine Änderung am Programm.",
        "en": f"Weekly security update: base image and packages updated ({len(ch)} changes), checked with self-test and "
              "Trivy. No change to the program."}]})
    with open(nf, "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    for rel, pat in (("README.md", r"(Aktuelle Version: \*\*pocketknife-)[^*]+(\*\*)"),
                     ("README.en.md", r"(Current version: \*\*pocketknife-)[^*]+(\*\*)")):
        p = os.path.join(ROOT, rel)
        if os.path.exists(p):
            t = open(p, encoding="utf-8").read()
            open(p, "w", encoding="utf-8").write(re.sub(pat, lambda m: m.group(1) + new + m.group(2), t, count=1))
    open(vf, "w", encoding="utf-8").write(new + "\n")
    print(new)


if __name__ == "__main__":
    if len(sys.argv) != 2 or not os.path.isdir(sys.argv[1]):
        print(__doc__)
        sys.exit(2)
    main(sys.argv[1])
