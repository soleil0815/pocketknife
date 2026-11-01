#!/bin/sh
# POCKET KNIFE – Schwachstellen-Prüfung eines Images mit Trivy (https://trivy.dev).
#
# Prüft Betriebssystem-Pakete (Debian) und Python-Pakete im Image auf bekannte Schwachstellen der Stufen HOCH und
# KRITISCH, für die es eine Korrektur gibt (--ignore-unfixed: ohne verfügbare Korrektur kann man nichts tun – das
# würde jedes Release blockieren). Gefunden → Exitcode 1 (das Release wird dann NICHT veröffentlicht).
#
# Aufruf:
#   sh tools/trivy-scan.sh pocketknife:local                       lokales Image (Docker-Host)
#   sh tools/trivy-scan.sh ghcr.io/soleil0815/pocketknife:latest   aus der Registry (vorher docker login ghcr.io)
#   sh tools/trivy-scan.sh IMAGE1 IMAGE2 …                         mehrere (z. B. amd64 und arm64)
#
# Trivy selbst wird in genau einer Version geladen und per SHA-256 geprüft – nie „latest“ und nie eine GitHub Action
# (im März 2026 wurden trivy-action, setup-trivy und Trivy 0.69.4–0.69.6 kompromittiert: CVE-2026-33634).
# Neue Trivy-Version: TRIVY_VERSION und beide Prüfsummen aus trivy_<Version>_checksums.txt des Releases eintragen.
#
# Ausnahmen (bewusst hingenommen, z. B. nicht ausnutzbar): .trivyignore im Programmordner – je Zeile eine CVE-Nummer,
# mit Begründung als Kommentar darüber und Ablaufdatum („CVE-2026-12345 exp:2026-12-31“).
# Optional: PK_TRIVY_SEVERITY (Standard HIGH,CRITICAL), PK_TRIVY_REPORT (Datei für den Bericht, Markdown),
# PK_TRIVY_PLATFORM (2.37: z. B. linux/arm64 – bei Images aus der Registry die Architektur, sonst die des Rechners).
set -eu
cd "$(dirname "$0")/.."

TRIVY_VERSION=0.76.1
SHA_LINUX_64="c7cbe3137fb8a48a17574bede3dd6beb16742d1e9772929940fc90082c39d9c0"
SHA_LINUX_ARM64="e174dff2a6dba5e3ae41f8d0537a6324405bd08132497340304c6b20e0f16a1f"
SEVERITY="${PK_TRIVY_SEVERITY:-HIGH,CRITICAL}"
REPORT="${PK_TRIVY_REPORT:-}"
PLAT="${PK_TRIVY_PLATFORM:-}"
case "$PLAT" in ""|linux/amd64|linux/arm64) ;; *) echo "PK_TRIVY_PLATFORM: nur linux/amd64 oder linux/arm64" >&2; exit 2 ;; esac
DB="mirror.gcr.io/aquasec/trivy-db:2,ghcr.io/aquasecurity/trivy-db:2"

[ $# -ge 1 ] || { sed -n '2,20p' "$0"; exit 2; }

case "$(uname -m)" in
  x86_64|amd64) ARCH=64bit; SHA="$SHA_LINUX_64" ;;
  aarch64|arm64) ARCH=ARM64; SHA="$SHA_LINUX_ARM64" ;;
  *) echo "Trivy: Architektur $(uname -m) wird von diesem Skript nicht unterstützt." >&2; exit 2 ;;
esac

WORK=$(mktemp -d "${TMPDIR:-/tmp}/pk-trivy.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
if [ -n "${PK_TRIVY_BIN:-}" ]; then   # nur für den Selbsttest (Attrappe statt Download)
  T="$PK_TRIVY_BIN"
else
TGZ="trivy_${TRIVY_VERSION}_Linux-${ARCH}.tar.gz"
echo "Trivy $TRIVY_VERSION laden ($TGZ) …"
curl -fsSL --retry 3 -o "$WORK/$TGZ" "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/${TGZ}"
if ! printf '%s  %s\n' "$SHA" "$WORK/$TGZ" | sha256sum -c - >/dev/null 2>&1; then
  echo "FEHLER: Prüfsumme von $TGZ stimmt nicht – Trivy wird NICHT ausgeführt." >&2
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
  echo "== Prüfe $IMG (Stufen: $SEVERITY, nur mit verfügbarer Korrektur)"
  # shellcheck disable=SC2086
  if ! "$T" image --quiet --cache-dir "$CACHE" --db-repository "$DB" --scanners vuln --pkg-types os,library ${PLAT:+--platform "$PLAT"} \
         --severity "$SEVERITY" --ignore-unfixed $IGN --format json --output "$WORK/r.json" "$IMG"; then
    echo "FEHLER: Trivy konnte $IMG nicht prüfen (Image nicht gefunden, keine Anmeldung, Datenbank nicht ladbar)." >&2
    RC=3
    continue
  fi
  # Ergebnis lesbar: Tabelle für das Protokoll, Markdown für den Bericht; Anzahl je Stufe
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
            fh.write("Keine Schwachstellen (HOCH/KRITISCH mit verfügbarer Korrektur).\n\n")
        else:
            fh.write("| Stufe | CVE | Paket | installiert | behoben in | Titel |\n|---|---|---|---|---|---|\n")
            for r in rows:
                fh.write("| " + " | ".join(str(x).replace("|", "/") for x in r) + " |\n")
            fh.write("\n")
import os
if os.environ.get("GITHUB_ACTIONS"):
    for r in rows[:30]:
        print(f"::error title=Trivy {r[0]} {r[1]}::{img}: {r[2]} {r[3]} → {r[4]} ({r[5]})")
PY
  if [ "${N:-0}" != "0" ]; then
    echo "→ $N Schwachstelle(n) mit verfügbarer Korrektur in $IMG." >&2
    [ "$RC" = 0 ] && RC=1
  else
    echo "→ keine Schwachstellen (HOCH/KRITISCH mit verfügbarer Korrektur)."
  fi
done
if [ "$RC" = 1 ]; then
  echo
  echo "Abhilfe: Versionen aktualisieren – auf GitHub „Actions → Versionen aktualisieren → Run workflow“ (oder auf einem" >&2
  echo "Docker-Host: sh tools/pin-versions.sh --update). Danach neu bauen und erneut prüfen." >&2
fi
exit "$RC"
