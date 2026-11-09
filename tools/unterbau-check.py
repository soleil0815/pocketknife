#!/usr/bin/env python3
"""POCKET KNIFE – check the base platform: how much longer will the image's Python series and Debian version
receive security updates? (Data: endoflife.date)

The monthly security update (patch day) keeps everything current within the Python series (e.g. 3.12) and the Debian version
(e.g. 13 "trixie"). Nobody raises the base platform silently - this script reports when it is time to do so:
  - less than WARN_DAYS (default 365) days: notice
  - less than FAIL_DAYS (default 180) days or already over: exit code 1 (run turns red → mail from GitHub)
Not reachable or unknown format: only a notice, exit code 0 (never blocks the security updates).

Usage: python3 tools/unterbau-check.py [--python-json FILE --debian-json FILE] [--today YYYY-MM-DD]
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
    """(python_series, debian_codename) from the Dockerfile, e.g. ("3.12", "trixie")."""
    df = open(os.path.join(ROOT, "Dockerfile"), encoding="utf-8").read()
    m = re.search(r"^ARG PYTHON_IMAGE=python:(\d+\.\d+)[\d.]*-slim-([a-z]+)", df, re.M)
    if not m:
        raise ValueError("ARG PYTHON_IMAGE not recognized in the Dockerfile")
    return m.group(1), m.group(2)


def _load(path, product):
    if path:
        return json.load(open(path, encoding="utf-8"))
    req = urllib.request.Request(API.format(product), headers={"User-Agent": "POCKETKNIFE-unterbau-check"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def eol(data, cycle):
    """End of security updates (date) for a series - v1 format ({"result": {"releases": [...]}}) or old ([...])."""
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
        except Exception as e:  # noqa: BLE001 – never block the security updates
            lines.append(f"::warning title=Base platform::{label} {cycle}: end of updates cannot be determined ({e})")
            continue
        if end is None:
            lines.append(f"::warning title=Base platform::{label} {cycle}: end of updates unknown")
            continue
        days = (end - today).days
        text = f"{label if label != 'Python' else 'Python ' + cycle}: security updates until {end.isoformat()} ({days} days left)"
        if days < FAIL_DAYS:
            rc = 1
            lines.append(f"::error title=Raise base platform::{text} – raise the base platform now "
                         f"(PK_PYTHON_BASE=python:<new series>-slim-<Debian> tools/pin-versions.sh --update, full test).")
        elif days < WARN_DAYS:
            lines.append(f"::warning title=Base platform::{text} – plan to raise it.")
        else:
            lines.append(f"OK – {text}")
    print("\n".join(lines))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
