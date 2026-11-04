#!/bin/sh
# Install Debian packages reproducibly.
# Usage: apt-install.sh <DEBIAN_SNAPSHOT> <package list>
#   DEBIAN_SNAPSHOT (YYYYMMDDThhmmssZ): point the package sources to this state of snapshot.debian.org – apt then always
#   delivers the same versions. If snapshot.debian.org is not reachable, the normal mirror is used with a warning;
#   pinned versions (package=version) must then still be available there.
#   Package list: one line per package, optionally with =version; # starts a comment.
set -eu
export DEBIAN_FRONTEND=noninteractive
TS="${1:-}"
LIST="$2"
PKGS=$(sed -e 's/#.*$//' -e '/^[[:space:]]*$/d' "$LIST")
SRC=/etc/apt/sources.list.d/debian.sources
CONF=/etc/apt/apt.conf.d/99pk-snapshot

if [ -n "$TS" ]; then
  case "$TS" in
    [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) ;;
    *) echo "DEBIAN_SNAPSHOT invalid: $TS (format YYYYMMDDThhmmssZ)" >&2; exit 1 ;;
  esac
  for f in "$SRC" /etc/apt/sources.list; do
    [ -f "$f" ] || continue
    cp "$f" "$f.pk-orig"
    sed -i -e "s#https\?://deb.debian.org/debian-security#http://snapshot.debian.org/archive/debian-security/$TS#g" \
           -e "s#https\?://deb.debian.org/debian\([ /]\|\$\)#http://snapshot.debian.org/archive/debian/$TS\1#g" "$f"
  done
  printf 'Acquire::Check-Valid-Until "false";\nAcquire::Retries "3";\n' > "$CONF"
  if apt-get update; then
    echo "apt: Debian-Snapshot $TS"
  else
    echo "WARNING: snapshot.debian.org not reachable – using the normal package sources (not reproducible)." >&2
    for f in "$SRC" /etc/apt/sources.list; do [ -f "$f.pk-orig" ] && mv "$f.pk-orig" "$f"; done
    rm -f "$CONF"
    apt-get update
  fi
else
  echo "Note: no DEBIAN_SNAPSHOT set – apt uses the current state (tools/pin-versions.sh)."
  apt-get update
fi
# Bring all packages of the base image to the same state (security updates) - not only our own: otherwise libraries
# of the base image would stay outdated until its next rebuild. Reproducible with the snapshot.
apt-get -y -o Dpkg::Options::=--force-confold upgrade
# shellcheck disable=SC2086
if ! apt-get install -y --no-install-recommends $PKGS; then
  echo "ERROR: pinned Debian packages not available ($PKGS)." >&2
  echo "       Pin new versions: ./tools/pin-versions.sh --update" >&2
  exit 1
fi
rm -rf /var/lib/apt/lists/* "$CONF"
for f in "$SRC" /etc/apt/sources.list; do [ -f "$f.pk-orig" ] && mv "$f.pk-orig" "$f"; done
exit 0
