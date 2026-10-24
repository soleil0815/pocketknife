#!/bin/sh
# POCKET KNIFE – pin all versions for reproducible builds.
#
# Reads the exact versions from the built image and writes them into the build files:
#   Dockerfile         ARG PYTHON_IMAGE=python:<version>-slim-<debian>@sha256:…   and   ARG DEBIAN_SNAPSHOT=<timestamp>
#   requirements.lock  all Python packages with == (incl. dependencies)
#   build-requirements.txt, docker/apt-build.txt, docker/apt-runtime.txt   with exact versions
#   docker-compose.yml guacd image with digest
# Afterwards it rebuilds completely (without cache) as a check and runs the self-test in the new image.
#
# Usage on the Docker host in the program folder:
#   ./tools/pin-versions.sh            pin the versions of the currently built image (default)
#   ./tools/pin-versions.sh --update   first fetch everything anew (latest versions) and pin those
#   ./tools/pin-versions.sh --no-verify  without check build
set -eu
cd "$(dirname "$0")/.."

UPDATE=0; VERIFY=1
for a in "$@"; do
  case "$a" in
    --update) UPDATE=1 ;;
    --no-verify) VERIFY=0 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "Unknown option: $a" >&2; exit 2 ;;
  esac
done

IMG=pocketknife:local
BUILDIMG=pocketknife-build:local
say() { printf '\n== %s\n' "$*"; }

if [ "$UPDATE" = 1 ]; then
  say "Fetching latest versions (Dockerfile without pinning)"
  # Unterbau bleibt: dieselbe Python-Reihe (z. B. 3.12) auf derselben Debian-Version (z. B. trixie) – nur deren neueste
  # Updates. „python:3.12-slim“ ohne Debian-Namen wechselte still auf die nächste Debian-Version. Den Unterbau anheben
  # (z. B. Python 3.13 oder Debian 14) nur bewusst: PK_PYTHON_BASE=python:3.13-slim-trixie ./tools/pin-versions.sh --update
  CUR=$(sed -n 's/^ARG PYTHON_IMAGE=//p' Dockerfile)
  BASE_TAG=$(printf '%s' "$CUR" | sed -n 's|^python:\([0-9]*\.[0-9]*\)[0-9.]*-slim-\([a-z]*\).*|python:\1-slim-\2|p')
  BASE_TAG="${PK_PYTHON_BASE:-${BASE_TAG:-python:3.12-slim-trixie}}"
  echo "Basis: $BASE_TAG"
  sed -i -e "s|^ARG PYTHON_IMAGE=.*|ARG PYTHON_IMAGE=${BASE_TAG}|" -e 's|^ARG DEBIAN_SNAPSHOT=.*|ARG DEBIAN_SNAPSHOT=|' Dockerfile
  for f in docker/apt-build.txt docker/apt-runtime.txt; do
    sed -i -e '/^[[:space:]]*#/!s/[=<>!~].*$//' "$f"
  done
  # Python-Pakete: kein Versionssprung – jedes bleibt auf seiner Hauptversion (vor 1.0: Unterversion), Korrekturen darin
  # kommen mit (tools/caps.py). Neue Hauptversionen nur bewusst: PK_ALLOW_MAJOR=1
  if [ "${PK_ALLOW_MAJOR:-0}" = 1 ]; then
    echo "PK_ALLOW_MAJOR=1: neue Hauptversionen der Python-Pakete erlaubt"
    printf '# keine Obergrenzen (PK_ALLOW_MAJOR=1)\n' > requirements.caps
    sed -i -e '/^[[:space:]]*#/!s/[=<>!~].*$//' build-requirements.txt
  else
    python3 tools/caps.py lock requirements.lock > requirements.caps.new && mv requirements.caps.new requirements.caps
    python3 tools/caps.py build build-requirements.txt > build-requirements.txt.new && mv build-requirements.txt.new build-requirements.txt
    echo "Obergrenzen: $(grep -c '<' requirements.caps) Python-Pakete bleiben auf ihrer Hauptversion"
  fi
  printf '# Exact versions of all Python packages (incl. dependencies) – created with tools/pin-versions.sh.\n' > requirements.lock
  sed -i -e 's|^\(    image: guacamole/guacd:[^@]*\)@sha256:[0-9a-f]*|\1|' docker-compose.yml
  # guacd: neueste stabile Version (x.y.z) von Docker Hub – bei einem Fehler bleibt die bisherige
  GNEW=$(python3 -c '
import json, re, urllib.request
u = "https://hub.docker.com/v2/repositories/guacamole/guacd/tags?page_size=100"
best = None
for _ in range(5):
    d = json.load(urllib.request.urlopen(u, timeout=30))
    for t in d.get("results") or []:
        n = t.get("name", "")
        if re.fullmatch(r"\d+\.\d+\.\d+", n):
            v = tuple(int(x) for x in n.split("."))
            best = max(best or v, v)
    u = d.get("next")
    if not u:
        break
print(".".join(map(str, best)) if best else "")
' 2>/dev/null || true)
  if [ -n "$GNEW" ]; then
    sed -i -e "s|^\(    image: guacamole/guacd:\)[^@ ]*|\1${GNEW}|" docker-compose.yml
    echo "guacd: $GNEW"
  else
    echo "guacd: neueste Version nicht ermittelbar – bleibt"
  fi
  docker build --pull --no-cache -t "$IMG" .
else
  docker build -t "$IMG" .
fi
docker build -q --target build -t "$BUILDIMG" . >/dev/null

say "Base image"
BASE=$(sed -n 's/^ARG PYTHON_IMAGE=//p' Dockerfile)
case "$BASE" in
  *@sha256:*) PIN_BASE="$BASE"; echo "already pinned: $BASE" ;;
  *)
    DIGEST=$(docker image inspect "$BASE" --format '{{range .RepoDigests}}{{println .}}{{end}}' | sed -n 's/.*@//p' | head -n1)
    [ -n "$DIGEST" ] || { echo "Digest of $BASE not found (docker pull $BASE)" >&2; exit 1; }
    PYV=$(docker run --rm --entrypoint python "$IMG" -c 'import platform; print(platform.python_version())')
    DEB=$(docker run --rm --entrypoint sh "$IMG" -c '. /etc/os-release; echo "$VERSION_CODENAME"')
    PIN_BASE="python:${PYV}-slim-${DEB}@${DIGEST}"
    echo "$BASE → $PIN_BASE"
    ;;
esac

say "Python packages"
{
  printf '# Exact versions of all Python packages (incl. dependencies) – created with tools/pin-versions.sh on %s\n' "$(date -u +%Y-%m-%d)"
  printf '# Base: %s\n' "$PIN_BASE"
  # pip ist im fertigen Image nicht mehr enthalten – Pakete über importlib.metadata lesen
  docker run --rm --entrypoint python "$IMG" -c 'import importlib.metadata as m; print("\n".join(sorted({d.metadata["Name"] + "==" + d.version for d in m.distributions() if d.metadata["Name"].lower() not in ("pip", "setuptools", "wheel")})))' | LC_ALL=C sort -f
} > requirements.lock.new
grep -c '==' requirements.lock.new | sed 's/^/Packages: /'
mv requirements.lock.new requirements.lock

say "Build tools"
TOOLS=$(sed -e '/^[[:space:]]*#/d' -e 's/[=<>!~].*$//' -e '/^[[:space:]]*$/d' build-requirements.txt)
FREEZE=$(docker run --rm --entrypoint pip "$BUILDIMG" freeze --all --disable-pip-version-check)
{
  sed -n '/^[[:space:]]*#/p' build-requirements.txt
  for n in $TOOLS; do
    line=$(printf '%s\n' "$FREEZE" | grep -i "^${n}==" || true)
    [ -n "$line" ] || { echo "$n not found in the build image" >&2; exit 1; }
    echo "$line"
  done
} > build-requirements.txt.new
mv build-requirements.txt.new build-requirements.txt
sed -n '/^[^#]/p' build-requirements.txt

pin_apt() {  # $1 = Image, $2 = Liste
  names=$(sed -e '/^[[:space:]]*#/d' -e 's/=.*$//' -e '/^[[:space:]]*$/d' "$2")
  vers=$(docker run --rm --entrypoint dpkg-query "$1" -W -f '${Package}=${Version}\n' $names)
  { sed -n '/^[[:space:]]*#/p' "$2"; printf '%s\n' "$vers"; } > "$2.new"
  mv "$2.new" "$2"
  printf '%s\n' "$vers"
}
say "Debian packages"
pin_apt "$BUILDIMG" docker/apt-build.txt
pin_apt "$IMG" docker/apt-runtime.txt

say "Debian-Snapshot"
SNAP=$(sed -n 's/^ARG DEBIAN_SNAPSHOT=//p' Dockerfile)
if [ -z "$SNAP" ]; then
  # Point in time at which apt installed in the runtime image – exactly this state is pinned
  T=$(docker history --no-trunc --human=false --format '{{.CreatedAt}}|{{.CreatedBy}}' "$IMG" | grep -E 'apt-install.sh|apt-get install' | head -n1 | cut -d'|' -f1)
  SNAP=$(date -u -d "${T:-now}" +%Y%m%dT%H%M%SZ)
  if docker run --rm --entrypoint python "$IMG" -c "import urllib.request; urllib.request.urlopen('http://snapshot.debian.org/archive/debian/$SNAP/dists/', timeout=20)" 2>/dev/null; then
    echo "snapshot.debian.org reachable – state $SNAP"
  else
    echo "WARNING: snapshot.debian.org not reachable – Debian packages pinned by version only."
    echo "         As soon as Debian publishes an update, a rebuild fails; then run ./tools/pin-versions.sh --update."
    SNAP=""
  fi
fi

say "guacd image"
GUAC=$(docker compose config --images 2>/dev/null | grep guacd | head -n1 || true)
case "$GUAC" in
  ""|*@sha256:*) PIN_GUAC="$GUAC" ;;
  *) docker pull -q "$GUAC" >/dev/null
     GD=$(docker image inspect "$GUAC" --format '{{range .RepoDigests}}{{println .}}{{end}}' | sed -n 's/.*@//p' | head -n1)
     PIN_GUAC="${GUAC}@${GD}" ;;
esac
echo "${PIN_GUAC:-–}"

sed -i -e "s|^ARG PYTHON_IMAGE=.*|ARG PYTHON_IMAGE=${PIN_BASE}|" -e "s|^ARG DEBIAN_SNAPSHOT=.*|ARG DEBIAN_SNAPSHOT=${SNAP}|" Dockerfile
if [ -n "$PIN_GUAC" ]; then
  sed -i -e "s|^\(    image: \)guacamole/guacd:.*|\1${PIN_GUAC}|" docker-compose.yml
fi

if [ "$VERIFY" = 1 ]; then
  say "Check: complete rebuild with the pinned versions"
  echo "(If this fails, e.g. because a Debian version is missing in the snapshot: ./tools/pin-versions.sh --update)"
  docker build --no-cache -t "$IMG" .
  docker run --rm --entrypoint python "$IMG" tools/selftest.py
fi
say "Done. Pinned in Dockerfile, requirements.lock, build-requirements.txt, docker/apt-*.txt, docker-compose.yml."
echo "Apply with: PK_IMAGE=pocketknife:local in the .env, then docker compose up -d"
