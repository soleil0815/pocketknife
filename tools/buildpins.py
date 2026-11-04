#!/usr/bin/env python3
"""POCKET KNIFE – exact versions that self-built packages require for their build tools (for tools/pin-versions.sh).

gssapi and krb5 are built from the source package. Their pyproject.toml requires Cython with an EXACT version
(e.g. "Cython == 3.3.0"). build-requirements.txt also applies in their isolated build environment via PIP_CONSTRAINT - if
it names a different version there (e.g. the newer Cython 3.3.1 that pip installed in the build image above), pip cannot
create the build environment and the build fails (weekly update on 2026-11-02).

  python3 tools/buildpins.py gssapi==1.12.0 krb5==0.10.0   → one line "name==version" per exactly required build tool

Reads the source packages from PyPI. If two packages require the same tool in different exact versions, the script
ends with an error (exit code 1) - they cannot be built together.
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
    """Entries of [build-system] requires (without a TOML library: only this one list)."""
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
                raise SystemExit(f"{tool}: {src[key]} requires {pins[key][1]}, {spec} requires {ver} – cannot be built together")
            pins[key], src[key] = (tool, ver), spec
    return pins


if __name__ == "__main__":
    if len(sys.argv) < 2 or any("==" not in a for a in sys.argv[1:]):
        print(__doc__)
        sys.exit(2)
    for _key, (tool, ver) in sorted(collect(sys.argv[1:]).items()):
        print(f"{tool}=={ver}")
