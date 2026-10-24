#!/usr/bin/env python3
"""POCKET KNIFE – Unterbau prüfen: Wie lange bekommen die Python-Reihe und die Debian-Version des Images noch
Sicherheitsupdates? (Daten: endoflife.date)

Das wöchentliche Sicherheitsupdate hält alles innerhalb der Python-Reihe (z. B. 3.12) und der Debian-Version
(z. B. 13 „trixie“) aktuell. Den Unterbau selbst hebt niemand still an – dieses Skript meldet, wann es Zeit dafür ist:
  - weniger als WARN_DAYS (Standard 365) Tage: Hinweis
  - weniger als FAIL_DAYS (Standard 180) Tage oder schon vorbei: Exitcode 1 (Lauf rot → Mail von GitHub)
Nicht erreichbar oder unbekanntes Format: nur ein Hinweis, Exitcode 0 (blockiert nie die Sicherheitsupdates).

Aufruf: python3 tools/unterbau-check.py [--python-json DATEI --debian-json DATEI] [--today JJJJ-MM-TT]
"""
import datetime
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DEBIAN = {"bullseye": "11", "bookworm": "12", "trixie": "13", "forky": "14", "duke": "15"}
WARN_DAYS = int(os.environ.get("PK_UNTERBAU_WARN_DAYS", "365"))
FAIL_DAYS = int(os.environ.get("PK_UNTERBAU_FAIL_DAYS", "180"))
API = "https://endoflife.date/api/v1/products/{}/"


def base():
    """(python_reihe, debian_codename) aus dem Dockerfile, z. B. ("3.12", "trixie")."""
    df = open(os.path.join(ROOT, "Dockerfile"), encoding="utf-8").read()
    m = re.search(r"^ARG PYTHON_IMAGE=python:(\d+\.\d+)[\d.]*-slim-([a-z]+)", df, re.M)
    if not m:
        raise ValueError("ARG PYTHON_IMAGE im Dockerfile nicht erkannt")
    return m.group(1), m.group(2)


def _load(path, product):
    if path:
        return json.load(open(path, encoding="utf-8"))
    req = urllib.request.Request(API.format(product), headers={"User-Agent": "POCKETKNIFE-unterbau-check"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def eol(data, cycle):
    """Ende der Sicherheitsupdates (date) für eine Reihe – v1-Format ({"result": {"releases": […]}}) oder alt ([…])."""
    rels = data.get("result", {}).get("releases") if isinstance(data, dict) else data
    for r in rels or []:
        if str(r.get("name", r.get("cycle", ""))) == cycle:
            v = r.get("eolFrom") or r.get("eol")
            if isinstance(v, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", v):
                return datetime.date.fromisoformat(v)
            return None
    return None


def main(argv):
    args = dict(zip(argv[::2], argv[1::2]))
    today = datetime.date.fromisoformat(args["--today"]) if "--today" in args else datetime.date.today()
    py, codename = base()
    deb = DEBIAN.get(codename)
    checks = [("Python", py, "python", args.get("--python-json"))]
    if deb:
        checks.append((f"Debian {deb} ({codename})", deb, "debian", args.get("--debian-json")))
    rc, lines = 0, []
    for label, cycle, product, path in checks:
        try:
            end = eol(_load(path, product), cycle)
        except Exception as e:  # noqa: BLE001 – nie die Sicherheitsupdates blockieren
            lines.append(f"::warning title=Unterbau::{label} {cycle}: Ende der Updates nicht ermittelbar ({e})")
            continue
        if end is None:
            lines.append(f"::warning title=Unterbau::{label} {cycle}: Ende der Updates unbekannt")
            continue
        days = (end - today).days
        text = f"{label if label != 'Python' else 'Python ' + cycle}: Sicherheitsupdates bis {end.isoformat()} (noch {days} Tage)"
        if days < FAIL_DAYS:
            rc = 1
            lines.append(f"::error title=Unterbau anheben::{text} – jetzt den Unterbau anheben "
                         f"(PK_PYTHON_BASE=python:<neue Reihe>-slim-<Debian> tools/pin-versions.sh --update, voller Test).")
        elif days < WARN_DAYS:
            lines.append(f"::warning title=Unterbau::{text} – Anheben einplanen.")
        else:
            lines.append(f"OK – {text}")
    print("\n".join(lines))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
