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
# Time-limited exception (2.61.1, PK_TRIVY_GRACE_DAYS=N, set in the PR test, the release and on patch day): a finding with
# a fix does not block while the image's package state is younger than N days - counted from DEBIAN_SNAPSHOT in the
# Dockerfile, the point in time of the last version update. Such findings are shown as warnings with their deadline;
# after the deadline they block again (exit code 1). Without the variable (default 0) every finding blocks - like the
# daily scan of the published image, which keeps reporting everything.
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
GRACE="${PK_TRIVY_GRACE_DAYS:-0}"
case "$GRACE" in ""|*[!0-9]*) echo "PK_TRIVY_GRACE_DAYS: whole number of days" >&2; exit 2 ;; esac
[ "$GRACE" -le 60 ] || { echo "PK_TRIVY_GRACE_DAYS: at most 60" >&2; exit 2; }
# package state = last version update (tools/pin-versions.sh sets DEBIAN_SNAPSHOT together with all other pins)
SNAP="${PK_TRIVY_SNAPSHOT:-}"   # only for the self-test: package state YYYYMMDD instead of the Dockerfile
[ -n "$SNAP" ] || SNAP=$(sed -n 's/^ARG DEBIAN_SNAPSHOT=\([0-9]\{8\}\)T.*/\1/p' Dockerfile 2>/dev/null | head -n 1)

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
  # verdict: 0 = nothing, 1 = blocks, 4 = only findings inside the time-limited exception
  V=0
  if [ "${N:-0}" != "0" ]; then
    V=1
    if command -v python3 >/dev/null 2>&1; then
      V=$(python3 - "$WORK/r.json" "$IMG" "$REPORT" "$GRACE" "$SNAP" <<'PY'
import datetime as dt, json, os, sys
data = json.load(open(sys.argv[1]))
img, report, grace, snap = sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5]
rows = []
for res in data.get("Results") or []:
    for v in res.get("Vulnerabilities") or []:
        rows.append((v.get("Severity", ""), v.get("VulnerabilityID", ""), v.get("PkgName", ""),
                     v.get("InstalledVersion", ""), v.get("FixedVersion", ""), (v.get("Title") or "")[:90]))
rows.sort(key=lambda r: ({"CRITICAL": 0, "HIGH": 1}.get(r[0], 2), r[2], r[1]))
deadline = None
try:
    if grace and snap:
        deadline = dt.datetime.strptime(snap, "%Y%m%d").date() + dt.timedelta(days=grace)
except ValueError:
    deadline = None
today = dt.datetime.now(dt.timezone.utc).date()
within = bool(rows) and deadline is not None and today <= deadline
if report:
    with open(report, "a", encoding="utf-8") as fh:
        fh.write(f"### Trivy: `{img}`\n\n")
        if within:
            fh.write(f"**Time-limited exception until {deadline.isoformat()}** (package state of {snap[:4]}-{snap[4:6]}-{snap[6:]}, "
                     f"{grace} days): these findings do not block yet. The next patch day or \"Actions → Update versions\" "
                     "fetches the fixes; after the deadline they block every release.\n\n")
        elif rows and deadline is not None:
            fh.write(f"**The time-limited exception has expired ({deadline.isoformat()})** – the package state is older than "
                     f"{grace} days. Update the versions: \"Actions → Update versions\" or the patch day run.\n\n")
        fh.write("| Severity | CVE | Package | installed | fixed in | Title |\n|---|---|---|---|---|---|\n")
        for r in rows:
            fh.write("| " + " | ".join(str(x).replace("|", "/") for x in r) + " |\n")
        fh.write("\n")
if os.environ.get("GITHUB_ACTIONS"):
    kind = "warning" if within else "error"
    note = f" – exception until {deadline.isoformat()}" if within else ""
    for r in rows[:30]:
        sys.stderr.write(f"::{kind} title=Trivy {r[0]} {r[1]}::{img}: {r[2]} {r[3]} → {r[4]} ({r[5]}){note}\n")
print(4 if within else 1)
PY
) || V=1
    fi
  elif [ -n "$REPORT" ]; then
    printf '### Trivy: `%s`\n\nNo vulnerabilities (HIGH/CRITICAL with an available fix).\n\n' "$IMG" >> "$REPORT"
  fi
  case "$V" in
    0) echo "→ no vulnerabilities (HIGH/CRITICAL with an available fix)." ;;
    4) echo "→ $N vulnerability(ies) with an available fix in $IMG – time-limited exception (package state younger than $GRACE days), does not block." >&2 ;;
    *) echo "→ $N vulnerability(ies) with an available fix in $IMG." >&2
       [ "$RC" = 0 ] && RC=1 ;;
  esac
done
if [ "$RC" = 1 ]; then
  echo
  echo "Remedy: update versions – on GitHub \"Actions → Update versions → Run workflow\" (or on a" >&2
  echo "Docker host: sh tools/pin-versions.sh --update). Then rebuild and scan again." >&2
fi
exit "$RC"
