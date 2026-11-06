#!/bin/sh
# Install Debian packages reproducibly.
# Usage: apt-install.sh <DEBIAN_SNAPSHOT> <package list>
#   DEBIAN_SNAPSHOT (YYYYMMDDThhmmssZ): point the package sources to this state of snapshot.debian.org – apt then always
#   delivers the same versions. If snapshot.debian.org is not reachable or does not deliver the package lists (it has
#   outages with "503/504 Gateway Timeout"), it is tried again a few times, then the normal mirror is used with a
#   warning; pinned versions (package=version) must then still be available there.
#   Package list: one line per package, optionally with =version; # starts a comment.
set -eu
export DEBIAN_FRONTEND=noninteractive
TS="${1:-}"
LIST="$2"
PKGS=$(sed -e 's/#.*$//' -e '/^[[:space:]]*$/d' "$LIST")
# PK_APT_ROOT / PK_APT_RETRY_WAIT: only for the self-test (sources in a test directory, no waiting)
ROOT="${PK_APT_ROOT:-}"
SRC=$ROOT/etc/apt/sources.list.d/debian.sources
LEGACY=$ROOT/etc/apt/sources.list
CONF=$ROOT/etc/apt/apt.conf.d/99pk-snapshot
WAIT="${PK_APT_RETRY_WAIT:-30}"

SNAPSHOT_ACTIVE=""
# Switch back to the normal package sources (snapshot not usable); pinned versions must be available there
use_mirror() {
  echo "WARNING: snapshot.debian.org $1 – using the normal package sources (not reproducible)." >&2
  for f in "$SRC" "$LEGACY"; do [ -f "$f.pk-orig" ] && mv "$f.pk-orig" "$f"; done
  rm -f "$CONF"
  rm -rf "$ROOT"/var/lib/apt/lists/*
  apt-get update
}

if [ -n "$TS" ]; then
  case "$TS" in
    [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) ;;
    *) echo "DEBIAN_SNAPSHOT invalid: $TS (format YYYYMMDDThhmmssZ)" >&2; exit 1 ;;
  esac
  for f in "$SRC" "$LEGACY"; do
    [ -f "$f" ] || continue
    cp "$f" "$f.pk-orig"
    sed -i -e "s#https\?://deb.debian.org/debian-security#http://snapshot.debian.org/archive/debian-security/$TS#g" \
           -e "s#https\?://deb.debian.org/debian\([ /]\|\$\)#http://snapshot.debian.org/archive/debian/$TS\1#g" "$f"
  done
  # Timeout: a hanging snapshot server (outages with 503/504) should fail quickly, not block the build for minutes
  printf 'Acquire::Check-Valid-Until "false";\nAcquire::Retries "3";\nAcquire::http::Timeout "30";\n' > "$CONF"
  # --error-on=any: a package list that could not be fetched (e.g. "504 Gateway Timeout") is an error - without it
  # apt only warns, keeps going without the lists and the install fails later with "packages not available"
  ok=""
  for n in 1 2 3; do
    if apt-get update --error-on=any; then ok=1; break; fi
    [ "$n" = 3 ] || { echo "apt: snapshot.debian.org did not deliver the package lists – attempt $n of 3, retrying in $((WAIT * n)) s" >&2; sleep $((WAIT * n)); }
  done
  if [ -n "$ok" ]; then
    echo "apt: Debian-Snapshot $TS"
    SNAPSHOT_ACTIVE=1
  else
    use_mirror "did not deliver the package lists"
  fi
else
  echo "Note: no DEBIAN_SNAPSHOT set – apt uses the current state (tools/pin-versions.sh)."
  apt-get update
fi

# Bring all packages of the base image to the same state (security updates) - not only our own: otherwise libraries
# of the base image would stay outdated until its next rebuild. Reproducible with the snapshot.
install_all() {
  apt-get -y -o Dpkg::Options::=--force-confold upgrade || return 1
  # shellcheck disable=SC2086
  apt-get install -y --no-install-recommends $PKGS
}
if ! install_all; then
  # The snapshot delivered the lists but not the packages (downloads time out): once more from the normal mirror
  if [ -n "$SNAPSHOT_ACTIVE" ]; then
    use_mirror "did not deliver the packages"
    SNAPSHOT_ACTIVE=""
    install_all || FAILED=1
  else
    FAILED=1
  fi
fi
if [ -n "${FAILED:-}" ]; then
  echo "ERROR: pinned Debian packages not available ($PKGS)." >&2
  echo "       Pin new versions: ./tools/pin-versions.sh --update" >&2
  exit 1
fi
rm -rf "$ROOT"/var/lib/apt/lists/* "$CONF"
for f in "$SRC" "$LEGACY"; do [ -f "$f.pk-orig" ] && mv "$f.pk-orig" "$f"; done
exit 0
