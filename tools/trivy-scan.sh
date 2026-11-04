#!/bin/sh
# POCKET KNIFE – vulnerability scan of an image with Trivy (https://trivy.dev).
#
# Checks operating system packages (Debian) and Python packages in the image for known vulnerabilities of severity HIGH
# and CRITICAL for which a fix exists (--ignore-unfixed: without an available fix nothing can be done - that
# would block every release). Found → exit code 1 (the release is then NOT published).
#
# Usage:
#   sh tools/trivy-scan.sh pocketknife:local                       local image (Docker host)
#   sh tools/trivy-scan.sh ghcr.io/soleil0815/pocketknife:latest   from the registry (docker login ghcr.io first)
#   sh tools/trivy-scan.sh IMAGE1 IMAGE2 …                         several (e.g. amd64 and arm64)
#
# Trivy itself is downloaded in exactly one version and verified by SHA-256 - never "latest" and never a GitHub Action
# (in March 2026 trivy-action, setup-trivy and Trivy 0.69.4–0.69.6 were compromised: CVE-2026-33634).
# New Trivy version: enter TRIVY_VERSION and both checksums from trivy_<version>_checksums.txt of the release.
#
# Exceptions (deliberately accepted, e.g. not exploitable): .trivyignore in the program folder - one CVE number per line,
# with the reason as a comment above and an expiry date ("CVE-2026-12345 exp:2026-12-31").
# Optional: PK_TRIVY_SEVERITY (default HIGH,CRITICAL), PK_TRIVY_REPORT (file for the report, Markdown),
# PK_TRIVY_PLATFORM (2.37: e.g. linux/arm64 - for images from the registry the architecture, otherwise that of the machine).
set -eu
cd "$(dirname "$0")/.."

TRIVY_VERSION=0.76.1
SHA_LINUX_64="c7cbe3137fb8a48a17574bede3dd6beb16742d1e9772929940fc90082c39d9c0"
SHA_LINUX_ARM64="e174dff2a6dba5e3ae41f8d0537a6324405bd08132497340304c6b20e0f16a1f"
SEVERITY="${PK_TRIVY_SEVERITY:-HIGH,CRITICAL}"
REPORT="${PK_TRIVY_REPORT:-}"
PLAT="${PK_TRIVY_PLATFORM:-}"
case "$PLAT" in ""|linux/amd64|linux/arm64) ;; *) echo "PK_TRIVY_PLATFORM: only linux/amd64 or linux/arm64" >&2; exit 2 ;; esac
DB="mirror.gcr.io/aquasec/trivy-db:2,ghcr.io/aquasecurity/trivy-db:2"

[ $# -ge 1 ] || { sed -n '2,20p' "$0"; exit 2; }

case "$(uname -m)" in
  x86_64|amd64) ARCH=64bit; SHA="$SHA_LINUX_64" ;;
  aarch64|arm64) ARCH=ARM64; SHA="$SHA_LINUX_ARM64" ;;
  *) echo "Trivy: architecture $(uname -m) is not supported by this script." >&2; exit 2 ;;
esac

WORK=$(mktemp -d "${TMPDIR:-/tmp}/pk-trivy.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
if [ -n "${PK_TRIVY_BIN:-}" ]; then   # only for the self-test (dummy instead of download)
  T="$PK_TRIVY_BIN"
else
TGZ="trivy_${TRIVY_VERSION}_Linux-${ARCH}.tar.gz"
echo "Downloading Trivy $TRIVY_VERSION ($TGZ) …"
curl -fsSL --retry 3 -o "$WORK/$TGZ" "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/${TGZ}"
if ! printf '%s  %s\n' "$SHA" "$WORK/$TGZ" | sha256sum -c - >/dev/null 2>&1; then
  echo "ERROR: checksum of $TGZ does not match – Trivy is NOT executed." >&2
  exit 3
fi
tar -xzf "$WORK/$TGZ" -C "$WORK" trivy
T="$WORK/trivy"
fi
CACHE="${PK_TRIVY_CACHE:-$WORK/cache}"

IGN=""
[ -f .trivyignore ] && IGN="--ignorefile .trivyignore"

RC=0
for IMG in "$@"; do
  echo
  echo "== Scanning $IMG (severities: $SEVERITY, only with an available fix)"
  # shellcheck disable=SC2086
  if ! "$T" image --quiet --cache-dir "$CACHE" --db-repository "$DB" --scanners vuln --pkg-types os,library ${PLAT:+--platform "$PLAT"} \
         --severity "$SEVERITY" --ignore-unfixed $IGN --format json --output "$WORK/r.json" "$IMG"; then
    echo "ERROR: Trivy could not scan $IMG (image not found, not logged in, database not loadable)." >&2
    RC=3
    continue
  fi
  # Readable result: table for the log, Markdown for the report; count per severity
  # shellcheck disable=SC2086
  "$T" convert --format table "$WORK/r.json" || true
  N=$(grep -c '"VulnerabilityID"' "$WORK/r.json" || true)
  command -v python3 >/dev/null 2>&1 && python3 - "$WORK/r.json" "$IMG" "$REPORT" <<'PY' || true
import json, sys
data = json.load(open(sys.argv[1]))
img, report = sys.argv[2], sys.argv[3]
rows = []
for res in data.get("Results") or []:
    for v in res.get("Vulnerabilities") or []:
        rows.append((v.get("Severity", ""), v.get("VulnerabilityID", ""), v.get("PkgName", ""),
                     v.get("InstalledVersion", ""), v.get("FixedVersion", ""), (v.get("Title") or "")[:90]))
rows.sort(key=lambda r: ({"CRITICAL": 0, "HIGH": 1}.get(r[0], 2), r[2], r[1]))
if report:
    with open(report, "a", encoding="utf-8") as fh:
        fh.write(f"### Trivy: `{img}`\n\n")
        if not rows:
            fh.write("No vulnerabilities (HIGH/CRITICAL with an available fix).\n\n")
        else:
            fh.write("| Severity | CVE | Package | installed | fixed in | Title |\n|---|---|---|---|---|---|\n")
            for r in rows:
                fh.write("| " + " | ".join(str(x).replace("|", "/") for x in r) + " |\n")
            fh.write("\n")
import os
if os.environ.get("GITHUB_ACTIONS"):
    for r in rows[:30]:
        print(f"::error title=Trivy {r[0]} {r[1]}::{img}: {r[2]} {r[3]} → {r[4]} ({r[5]})")
PY
  if [ "${N:-0}" != "0" ]; then
    echo "→ $N vulnerability(ies) with an available fix in $IMG." >&2
    [ "$RC" = 0 ] && RC=1
  else
    echo "→ no vulnerabilities (HIGH/CRITICAL with an available fix)."
  fi
done
if [ "$RC" = 1 ]; then
  echo
  echo "Remedy: update versions – on GitHub \"Actions → Update versions → Run workflow\" (or on a" >&2
  echo "Docker host: sh tools/pin-versions.sh --update). Then rebuild and scan again." >&2
fi
exit "$RC"
