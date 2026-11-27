#!/bin/sh
# POCKET KNIFE – is there a new guacd from the Guacamole project? (2.62.2, called daily by .github/workflows/guacd.yml)
#
# The Guacamole project now and then rebuilds guacd under the SAME version number (e.g. 1.6.0 with updated system
# packages) or brings out a patch version (1.6.x). docker/guacd.txt holds the image POCKET KNIFE publishes, pinned to
# its digest. This script compares it with the newest build of the same series (major.minor) on Docker Hub.
# A new series (e.g. 1.7) is never taken over here - it may need a matching guacamole-common-js; it comes on patch day
# (tools/pin-versions.sh --update) with the full test.
#
# Output (key=value, for $GITHUB_OUTPUT):
#   status=same|update   current=<image@digest>   image=<new image@digest>   version=<x.y.z>   newer=<newer series or empty>
# Exit code 2: could not check (Docker Hub, registry) - nothing is changed then.
# Only for the self-test: PK_DOCKER (command instead of docker), PK_GUACD_TAGS (comma list instead of Docker Hub).
set -eu
cd "$(dirname "$0")/.."
DOCKER="${PK_DOCKER:-docker}"

CUR=$(sed -n 's/^\(guacamole\/guacd:[0-9][0-9.]*@sha256:[0-9a-f]\{64\}\)$/\1/p' docker/guacd.txt | head -n 1)
[ -n "$CUR" ] || { echo "docker/guacd.txt: no guacamole/guacd:<version>@sha256:<digest>" >&2; exit 2; }
VER=$(printf '%s' "$CUR" | sed 's/^guacamole\/guacd:\([0-9.]*\)@.*/\1/')
OLD=${CUR#*@}

if [ -n "${PK_GUACD_TAGS:-}" ]; then
  TAGS=$(printf '%s' "$PK_GUACD_TAGS" | tr ',' '\n')
else
  TAGS=$(python3 -c '
import json, urllib.request
u = "https://hub.docker.com/v2/repositories/guacamole/guacd/tags?page_size=100"
for _ in range(5):
    d = json.load(urllib.request.urlopen(u, timeout=30))
    for t in d.get("results") or []:
        print(t.get("name", ""))
    u = d.get("next")
    if not u:
        break
') || { echo "Docker Hub: tags of guacamole/guacd could not be read" >&2; exit 2; }
fi
PICK=$(printf '%s\n' "$TAGS" | python3 -c '
import re, sys
cur = tuple(int(x) for x in sys.argv[1].split("."))
vs = sorted({tuple(int(x) for x in t.split(".")) for t in sys.stdin.read().split() if re.fullmatch(r"\d+\.\d+\.\d+", t)})
same = [v for v in vs if v[:2] == cur[:2] and v >= cur]
newer = [v for v in vs if v[:2] > cur[:2]]
print(".".join(map(str, same[-1] if same else cur)), ".".join(map(str, newer[-1])) if newer else "")
' "$VER")
CAND=${PICK% *}
NEWER=$(printf '%s' "$PICK" | cut -s -d' ' -f2)

INFO=$($DOCKER buildx imagetools inspect "guacamole/guacd:$CAND") || { echo "registry: guacamole/guacd:$CAND could not be inspected" >&2; exit 2; }
DIG=$(printf '%s\n' "$INFO" | sed -n 's/^Digest:[[:space:]]*\(sha256:[0-9a-f]\{64\}\)$/\1/p' | head -n 1)
[ -n "$DIG" ] || { echo "registry: no digest for guacamole/guacd:$CAND" >&2; exit 2; }
for p in linux/amd64 linux/arm64; do
  printf '%s\n' "$INFO" | grep -q "Platform:[[:space:]]*$p\$" || { echo "guacamole/guacd:$CAND has no $p image - not taken over" >&2; exit 2; }
done

echo "current=$CUR"
echo "version=$CAND"
echo "newer=$NEWER"
if [ "$DIG" = "$OLD" ] && [ "$CAND" = "$VER" ]; then
  echo "status=same"
  echo "image=$CUR"
else
  echo "status=update"
  echo "image=guacamole/guacd:$CAND@$DIG"
fi
