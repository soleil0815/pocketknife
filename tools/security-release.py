#!/usr/bin/env python3
"""POCKET KNIFE – Sicherheitsupdate vorbereiten (vom wöchentlichen Workflow aufgerufen, nach tools/pin-versions.sh --update).

Erhöht VERSION um eine Stelle (2.29.2 → 2.29.3, 2.30 → 2.30.1) und trägt die Version in CHANGELOG.md und app/news.json
ein – beides verlangt der Release-Workflow. Was sich geändert hat, liest das Skript aus den festgelegten Versionen
(Basis-Image, Debian- und Python-Pakete) vor und nach dem Update.

Aufruf: python3 tools/security-release.py <Ordner mit dem bisherigen Stand>   (z. B. ein git worktree von HEAD)
Gibt die neue Version aus.
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
    """{name: version} aus requirements.lock, docker/apt-*.txt und dem Basis-Image im Dockerfile."""
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
        out["Basis-Image"] = m.group(1).split("@")[0]
        out["_digest"] = m.group(1).partition("@")[2]
    return out


def bump(v):
    parts = [int(x) for x in v.strip().split(".")]
    if len(parts) == 2:
        parts.append(1)
    else:
        parts[-1] += 1
    return ".".join(str(x) for x in parts)


def changes(old_dir, new_dir=ROOT):
    a, b = _pins(old_dir), _pins(new_dir)
    out = []
    for k in sorted(set(a) | set(b), key=str.lower):
        if k.startswith("_"):
            continue
        if a.get(k) != b.get(k):
            out.append((k, a.get(k) or "–", b.get(k) or "–"))
    if a.get("_digest") != b.get("_digest") and not any(k == "Basis-Image" for k, _o, _n in out):
        out.insert(0, ("Basis-Image", "bisheriger Stand", "neuer Stand (Sicherheitsupdates von Debian/Python)"))
    return out


def main(old_dir):
    vf = os.path.join(ROOT, "VERSION")
    old = open(vf, encoding="utf-8").read().strip()
    new = bump(old)
    ch = changes(old_dir)
    today = datetime.date.today()
    lines = [f"## pocketknife-{new} ({today.strftime('%d.%m.%Y')})",
             "- **Wöchentliches Sicherheitsupdate** (automatisch gebaut): Basis-Image, Debian- und Python-Pakete auf den "
             "neuesten Stand gebracht, ohne Cache neu gebaut, Selbsttest und Schwachstellen-Prüfung (Trivy) bestanden. "
             "Am Programm selbst hat sich nichts geändert"]
    if ch:
        shown = ", ".join(f"`{k}` {o} → {n}" for k, o, n in ch[:25])
        lines.append(f"- Geändert ({len(ch)}): {shown}" + (f" … (+{len(ch) - 25} weitere)" if len(ch) > 25 else ""))
    entry = "\n".join(lines) + "\n\n"
    cf = os.path.join(ROOT, "CHANGELOG.md")
    s = open(cf, encoding="utf-8").read()
    head = "# Änderungen\n\n"
    i = s.index(head) + len(head) if head in s else 0
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
