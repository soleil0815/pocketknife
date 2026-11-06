#!/bin/sh
# POCKET KNIFE 2.38.5 – slim down the runtime image after all packages are installed.
#
# Removes what only the build needs. Fewer packages means fewer findings in the security scan (Trivy) and a
# smaller image; nothing the program uses at runtime is touched:
#   - apt with its libraries and keys: packages are only installed during the build. Removed with dpkg so the package
#     database stays correct - Trivy keeps checking all remaining Debian packages (deleting the database would only
#     hide vulnerabilities).
#   - C headers and the static Python library: only needed to compile.
#   - Tk, IDLE, turtle and 2to3 of Python: desktop/development tools, never imported by the program.
# dpkg itself stays (the package database belongs to it), as do perl-base, bash and coreutils (core of Debian).
set -eu
export DEBIAN_FRONTEND=noninteractive

# --- apt: first apt itself (marked "protected" in Debian), then what only apt needed ---
dpkg --purge --force-remove-protected apt
# Dependencies of apt: removed only if nothing else depends on them (dpkg refuses otherwise - then they stay)
for p in $(dpkg-query -W -f '${Package}\n' 'libapt-pkg*' sqv gpgv debian-archive-keyring libseccomp2 2>/dev/null); do
  if dpkg --purge "$p" >/dev/null 2>&1; then
    echo "slim: removed $p"
  else
    echo "slim: $p stays (still needed by another package)"
  fi
done
rm -rf /var/lib/apt /var/cache/apt /etc/apt /var/log/apt /var/log/dpkg.log /var/cache/debconf/*-old

# --- Python: build and desktop parts ---
PYLIB=$(python -c 'import sysconfig; print(sysconfig.get_path("stdlib"))')
rm -rf /usr/local/include \
       "$PYLIB"/config-*/libpython*.a \
       "$PYLIB"/idlelib "$PYLIB"/tkinter "$PYLIB"/turtledemo "$PYLIB"/turtle.py "$PYLIB"/lib2to3 \
       /usr/local/bin/idle* /usr/local/bin/2to3*
find "$PYLIB" -depth -type d -name __pycache__ -path '*tkinter*' -exec rm -rf {} + 2>/dev/null || true

# --- check: apt is gone, everything the program needs is still there ---
if command -v apt-get >/dev/null 2>&1 || command -v apt >/dev/null 2>&1; then
  echo "slim: ERROR - apt is still in the image" >&2
  exit 1
fi
command -v ping >/dev/null || { echo "slim: ERROR - ping missing (tool \"Ping & Ports\")" >&2; exit 1; }
python -c "import ssl, sqlite3, zoneinfo, gssapi, krb5, cryptography, fastapi, uvicorn, ldap3, pypsrp, smbprotocol, spnego, websockets, yaml" \
  || { echo "slim: ERROR - a module of the program can no longer be imported" >&2; exit 1; }
dpkg --audit
echo "slim: done"
