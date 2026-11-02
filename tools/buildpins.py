#!/usr/bin/env python3
"""POCKET KNIFE – genaue Versionen, die selbst gebaute Pakete für ihre Bauwerkzeuge verlangen (für tools/pin-versions.sh).

gssapi und krb5 werden aus dem Quellpaket gebaut. Ihre pyproject.toml verlangt Cython mit GENAUER Version
(z. B. „Cython == 3.3.0“). build-requirements.txt gilt per PIP_CONSTRAINT auch in deren isolierter Bauumgebung – steht
dort eine andere Version (z. B. das neuere Cython 3.3.1, das pip oben im Bau-Image installiert hat), kann pip die
Bauumgebung nicht anlegen und der Bau scheitert (wöchentliches Update am 02.11.2026).

  python3 tools/buildpins.py gssapi==1.12.0 krb5==0.10.0   → je Zeile „name==version“ der genau verlangten Bauwerkzeuge

Liest die Quellpakete von PyPI. Verlangen zwei Pakete dasselbe Werkzeug in verschiedenen genauen Versionen, endet das
Skript mit Fehler (Exitcode 1) – das lässt sich nicht gemeinsam bauen.
"""
import io
import json
import re
import sys
import tarfile
import urllib.request

EXACT = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*===?\s*([0-9][^\s,;]*)\s*(?:;.*)?$")


def norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def requires_from_pyproject(text):
    """Einträge aus [build-system] requires (ohne TOML-Bibliothek: nur diese eine Liste)."""
    m = re.search(r"^\[build-system\]\s*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
    if not m:
        return []
    r = re.search(r"^\s*requires\s*=\s*\[(.*?)\]", m.group(1), re.M | re.S)
    if not r:
        return []
    return re.findall(r"""["']([^"']+)["']""", r.group(1))


def exact_pins(requires):
    out = {}
    for req in requires:
        m = EXACT.match(req)
        if m:
            out[norm(m.group(1))] = (m.group(1), m.group(2))
    return out


def sdist_pyproject(name, version, opener=urllib.request.urlopen):
    with opener(f"https://pypi.org/pypi/{name}/{version}/json", timeout=60) as r:
        info = json.load(r)
    urls = [u["url"] for u in info.get("urls", []) if u.get("packagetype") == "sdist"]
    if not urls:
        return ""
    with opener(urls[0], timeout=120) as r:
        data = r.read()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
        for m in tf.getmembers():
            parts = m.name.split("/")
            if len(parts) == 2 and parts[1] == "pyproject.toml" and m.isfile():
                f = tf.extractfile(m)
                return f.read().decode("utf-8") if f else ""
    return ""


def collect(specs, fetch=sdist_pyproject):
    pins, src = {}, {}
    for spec in specs:
        name, _, version = spec.partition("==")
        for key, (tool, ver) in exact_pins(requires_from_pyproject(fetch(name, version))).items():
            if key in pins and pins[key][1] != ver:
                raise SystemExit(f"{tool}: {src[key]} verlangt {pins[key][1]}, {spec} verlangt {ver} – nicht gemeinsam baubar")
            pins[key], src[key] = (tool, ver), spec
    return pins


if __name__ == "__main__":
    if len(sys.argv) < 2 or any("==" not in a for a in sys.argv[1:]):
        print(__doc__)
        sys.exit(2)
    for _key, (tool, ver) in sorted(collect(sys.argv[1:]).items()):
        print(f"{tool}=={ver}")
