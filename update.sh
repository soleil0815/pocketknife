#!/bin/sh
# POCKET KNIFE – update (since 2.24.1: always in the same folder, e.g. /opt/pocketknife)
#
#     cd /opt/pocketknife
#     sh update.sh pocketknife-2.24.1.zip
#
#   → shows the current and the new version and asks; then replaces the program files IN THE SAME FOLDER
#     (app/, tools/, docker/, Dockerfile, docker-compose.yml …). Left untouched: .env, database (data/),
#     backups (data-backup/), repository (repo/) and everything that is not part of the program.
#     The previous program files stay in .pk-vorher/ until the next update (only ONE state, a few MB):
#     if the new version does not start cleanly, you are offered to go back to it automatically.
#     Afterwards: health check, self-test, cleanup (with confirmation).
#
#   Moving from version folders (pocketknife-2.23.3/, pocketknife-2.24/ … from 2.23.3/2.24): if POCKET KNIFE still
#   runs from such a subfolder, the script copies its .env into the program folder, starts from there and offers
#   to delete the version folders – never a folder that contains the database, backups or repository
#   (according to .env incl. defaults and to the folders actually mounted into the containers; paths fully
#   resolved – symlinks, "..").
#
# The old way still works: in the unpacked folder  ./update.sh <old folder>  (takes over its .env, builds, starts).
#
# Since 2.27.1 – ready-made image from the registry (GitHub Container Registry) instead of building locally:
#
#     sh update.sh --pull            newest version (tag "latest")
#     sh update.sh --pull 2.27.1     a specific version
#
#   → pulls the image (amd64/arm64, Docker picks the right one), takes the program ZIP contained in it (checked
#     against its SHA-256), replaces the program files as above and starts exactly the pulled image (no build).
#     The container then shows where it comes from (PK_IMAGE in the .env, e.g. ghcr.io/soleil0815/pocketknife:latest).
#   Recommended: update only this way or with the button in the web interface (see below) – not with automatic
#   updates by Watchtower, Dockhand, Tugtainer & co.: they only swap the image (docker-compose.yml and guacd stay
#   old, no automatic going back).
#     Registry: PK_IMAGE_REPO in the .env (default ghcr.io/soleil0815/pocketknife). For a private registry log in
#     once as the user that runs the update:  docker login ghcr.io -u <github user>  (token with read:packages).
#   The ZIP way stays unchanged (e.g. without internet access).
#
# Since 2.32.9 – update from the web interface (button "Update now", main admins only), without giving the container
# access to Docker:
#
#   Nothing to switch on: every update sets up a systemd timer (otherwise /etc/cron.d) that runs  update.sh --watch
#   every 5 minutes.
#     sudo sh update.sh --watch-install    set it up by hand (e.g. after a Synology DSM update)
#     sudo sh update.sh --watch-remove     switch it off for good (PK_UPDATE_HELPER=off in the .env)
#
#   --watch looks for a request in <data>/update/, carries out  update.sh --pull <version> -y  (backup, checksum,
#   health check, going back automatically) and writes state and log back for the web interface.
#   Synology: Task Scheduler → user-defined script as root, every 5 minutes:  sh /path/update.sh --watch
#
# Options: -y (answer all questions with "yes")
set -eu

SCRIPTDIR=$(cd "$(dirname "$0")" && pwd -P)
YES=0
ARG=""
PULL=""
PULLTAG=""
PULLREF=""
WATCH=""
LOCAL_IMAGE="pocketknife:local"      # image name in docker-compose.yml
PREV_IMAGE="pocketknife:previous"    # the image before the current (good) version – after a successful update
BACK_IMAGE="pocketknife:rollback"    # the image running before THIS update – going back needs no build
DEFAULT_IMAGE_REPO="ghcr.io/soleil0815/pocketknife"
TAB=$(printf '\t')
while [ $# -gt 0 ]; do
  case "$1" in
    -y|--yes) YES=1 ;;
    --pull) PULL=1
            case "${2:-}" in [0-9]*) PULLTAG="$2"; shift ;; esac ;;
    --pull=*) PULL=1; PULLTAG="${1#--pull=}" ;;
    --watch) WATCH=run ;;
    --watch-install) WATCH=install ;;
    --watch-remove) WATCH=remove ;;
    --keep) shift ;;   # formerly: keep old version folders – since 2.24.1 there is only one program folder
    -h|--help) sed -n '2,48p' "$0"; exit 0 ;;
    *) ARG="$1" ;;
  esac
  shift
done

say()  { printf '%s\n' "$*"; }
info() { printf '\n== %s\n' "$*"; }
die()  { printf '\nERROR: %s\n' "$*" >&2; exit 1; }
ask() {
  # ask "question" → 0 for yes (y/yes; j/ja is accepted as well)
  if [ "$YES" = 1 ]; then say "$1 [y/N] y (automatic)"; return 0; fi
  printf '%s [y/N] ' "$1"
  read -r a || a=""
  case "$a" in j|J|ja|Ja|JA|y|Y|yes|Yes) return 0 ;; *) return 1 ;; esac
}
abspath() { (cd "$1" 2>/dev/null && pwd -P); }
# Fully resolved path (even if it does not exist yet): symlinks, "." and ".."
canon() {
  [ -n "$1" ] || return 0
  realpath -m -- "$1" 2>/dev/null || readlink -f -- "$1" 2>/dev/null || printf '%s' "$1"
}

# Read a value from a .env (without executing it – a .env is data, not a script).
# KEY=value, KEY="value", KEY='value', each with or without a trailing "# comment"; the last line wins.
envval() {
  [ -f "$1" ] || return 0
  awk -v k="$2" '
    { line = $0 }
    match(line, "^[ \t]*(export[ \t]+)?" k "[ \t]*=") {
      v = substr(line, RLENGTH + 1); sub(/^[ \t]+/, "", v)
      q = substr(v, 1, 1)
      if (q == "\"" || q == "\047") { v = substr(v, 2); i = index(v, q); if (i) v = substr(v, 1, i - 1) }
      else { sub(/[ \t]+#.*$/, "", v); sub(/[ \t\r]+$/, "", v) }
      last = v; found = 1
    }
    END { if (found) print last }' "$1"
}
# Set a value in a .env – without sed (paths with | & \ stay unchanged); temporary file readable by root only
env_set() {
  f="$1"; k="$2"; v="$3"
  ( umask 077
    { grep -v "^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}$k[[:space:]]*=" "$f" || true
      printf '%s=%s\n' "$k" "$v"; } > "$f.pk-neu" )
  cat "$f.pk-neu" > "$f" && rm -f "$f.pk-neu"
}
# Resolve a path relative to a folder (absolute paths stay as they are)
resolve() {
  case "$2" in
    /*) printf '%s' "$2" ;;
    "") printf '' ;;
    *) printf '%s/%s' "$1" "${2#./}" ;;
  esac
}
# Compare versions: 0 if $1 < $2
ver_lt() {  # „2.29.6-s3“ = Sicherheitsupdate von 2.29.6 (höher als 2.29.6, niedriger als 2.29.7)
  [ "$1" = "$2" ] && return 1
  # vier Stellen (2.29.6.1 ist erlaubt) und das „-sN“ als fünftes Feld
  a=$(printf '%s' "$1" | awk -F'-s' '{n=split($1,p,"."); while(n<4){n++; p[n]=0}; printf "%s.%s.%s.%s.%s", p[1],p[2],p[3],p[4],($2==""?0:$2+1)}')
  b=$(printf '%s' "$2" | awk -F'-s' '{n=split($1,p,"."); while(n<4){n++; p[n]=0}; printf "%s.%s.%s.%s.%s", p[1],p[2],p[3],p[4],($2==""?0:$2+1)}')
  [ "$a" = "$b" ] && return 1
  first=$(printf '%s\n%s\n' "$a" "$b" | sort -t. -k1,1n -k2,2n -k3,3n -k4,4n -k5,5n | head -n 1)
  [ "$first" = "$a" ]
}

# Going back without a saved image: build the previous version again (old compose files with or without „build:“)
back_build() {
  if grep -q '^[[:space:]]*build:' docker-compose.yml 2>/dev/null; then docker compose up -d --build
  else docker build -t "$LOCAL_IMAGE" . && docker compose up -d
  fi
}

need_docker() {
  command -v docker >/dev/null 2>&1 || die "docker was not found."
  docker compose version >/dev/null 2>&1 || die "\"docker compose\" is missing (Docker Compose v2)."
  docker info >/dev/null 2>&1 || die "No access to Docker – run as root or with sudo (sudo sh update.sh …)."
}

# Folder of the currently running version (from the container's Compose labels)
running_dir() {
  docker inspect pocketknife --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' 2>/dev/null || true
}
# Folders actually mounted into the containers (database, repository, backups) → one path per line
mounts() {
  for ct in pocketknife pocketknife-guacd; do
    docker inspect "$ct" --format '{{ range .Mounts }}{{ .Source }}{{ "\n" }}{{ end }}' 2>/dev/null || true
  done
}

wait_healthy() {
  # wait up to 5 minutes for "healthy"
  i=0
  while [ $i -lt 60 ]; do
    st=$(docker inspect pocketknife --format '{{ if .State.Health }}{{ .State.Health.Status }}{{ else }}{{ .State.Status }}{{ end }}' 2>/dev/null || echo "?")
    case "$st" in
      healthy) say "POCKET KNIFE is running and responding (healthy)."; return 0 ;;
      unhealthy) return 1 ;;
      exited|dead) return 1 ;;
    esac
    printf '.'
    sleep 5
    i=$((i + 1))
  done
  echo
  return 1
}

# Program files replaced by an update (everything else in the program folder stays untouched)
PROGRAM_ITEMS="app tools docker Dockerfile docker-compose.yml VERSION CHANGELOG.md README.md README.en.md run.py healthcheck.py \
requirements.txt requirements.lock requirements.caps build-requirements.txt .dockerignore .env.example .gitignore .gitattributes .github .trivyignore"

# Remove an entry from a list (after the step is done – a second run does not repeat it)
list_drop() {
  { grep -vxF -- "$2" "$1" || true; } > "$1.neu"; mv "$1.neu" "$1"
}
# Put the previous program files back (also after an abort in the middle of the swap). May be called any number of
# times and may itself have been interrupted: every step is removed from the list once done, only what is certainly NEW
# is deleted, and the old files are only moved, never copied. Ctrl+C is ignored meanwhile.
restore_prev() {
  [ -n "${TMP:-}" ] && [ -f "$TMP/moved_new" ] || return 0
  trap '' INT TERM
  case $- in *e*) _e=1 ;; *) _e="" ;; esac
  set +e
  # 1. Remove the newly placed program files
  while [ -s "$TMP/moved_new" ]; do
    n=$(head -n 1 "$TMP/moved_new")
    [ -n "$n" ] && rm -rf "${INST:?}/$n"
    list_drop "$TMP/moved_new" "$n"
  done
  # 2. Bring back the previous ones – only if they are still in the holding area (otherwise they are back already)
  while [ -s "$TMP/moved_old" ]; do
    n=$(head -n 1 "$TMP/moved_old")
    if [ -n "$n" ] && { [ -e "$KEEP/$n" ] || [ -L "$KEEP/$n" ]; }; then
      rm -rf "${INST:?}/$n"
      mv "$KEEP/$n" "$INST/$n" || { printf 'ERROR: %s could not be put back – it is still in %s\n' "$n" "$KEEP" >&2; break; }
    fi
    list_drop "$TMP/moved_old" "$n"
  done
  # 3. .env only if the update changed it (moving from a version folder) – via a new file, never cp over the
  #    existing one (full disk → empty .env)
  if [ -n "${ENVCHANGED:-}" ]; then
    if [ -n "${HADENV:-}" ]; then
      cp "$TMP/bisher/.env" "$INST/.env.pk-zurueck" && chmod 600 "$INST/.env.pk-zurueck" && mv "$INST/.env.pk-zurueck" "$INST/.env" && ENVCHANGED=""
    else
      rm -f "$INST/.env" && ENVCHANGED=""
    fi
  fi
  # 4. Restore the saved predecessor (only if it was rotated)
  #    (clear ROTATE afterwards: a second run must not touch the restored state again)
  #    Only once ALL previous files are back – otherwise some are still in .pk-vorher/ (= KEEP)
  if [ -n "${ROTATE:-}" ] && [ ! -s "$TMP/moved_old" ]; then
    if [ -d "$PREV.alt" ]; then rm -rf "${PREV:?}" && mv "$PREV.alt" "$PREV" && ROTATE=""
    else rm -rf "${PREV:?}" && ROTATE=""   # there was no saved state before
    fi
  fi
  if [ ! -s "$TMP/moved_old" ] && [ -z "${ENVCHANGED:-}" ] && [ -z "${ROTATE:-}" ]; then
    # Was the run before this one already unfinished, the marker stays: .pk-vorher/ holds the last GOOD version
    [ -n "${UNFINISHED:-}" ] || rm -f "${MARK:-/nonexistent}"
    STAGE=restored
  fi
  [ -n "$_e" ] && set -e
  trap 'echo; say "Aborted (Ctrl+C)."; exit 130' INT TERM
  return 0
}
on_exit() {
  rc=$?
  trap - EXIT INT TERM
  if [ "${STAGE:-}" = swap ]; then
    printf '\nUpdate aborted – putting the previous program files back ...\n' >&2
    set +e
    restore_prev
    if [ "${STAGE:-}" = restored ]; then
      printf 'Restored. The previous version keeps running unchanged.\n' >&2
    else
      printf 'WARNING: Not everything could be put back (see above). Please run "sh update.sh <ZIP>" again –\n' >&2
      printf '         the saved state in .pk-vorher/ is kept.\n' >&2
    fi
  elif [ "${STAGE:-}" = started ]; then
    printf '\nUpdate not completed. The next update keeps the saved state in .pk-vorher/.\n' >&2
  fi
  [ -n "${TMP:-}" ] && rm -rf "$TMP"
  [ -n "${PULLDIR:-}" ] && rm -rf "$PULLDIR"
  exit "$rc"
}

# --------------------------------------------------------------------------------------------------------------
# The old way: in the new version folder with the old folder as parameter (or without, if the .env is already there)
# --------------------------------------------------------------------------------------------------------------
classic() {
  OLD="$1"
  cd "$SCRIPTDIR"
  if [ ! -f .env ]; then
    if [ -n "$OLD" ] && [ -f "$OLD/.env" ]; then
      cp "$OLD/.env" .env
      chmod 600 .env 2>/dev/null || true
      say ".env taken over from $OLD."
    else
      die "No .env found. Usage: sh update.sh pocketknife-<version>.zip  or  ./update.sh <old version folder>"
    fi
  fi
  need_docker
  info "Building and starting POCKET KNIFE $(cat VERSION) ..."
  [ "$(envval .env PK_IMAGE)" = "$LOCAL_IMAGE" ] || { env_set .env PK_IMAGE "$LOCAL_IMAGE"; chmod 600 .env 2>/dev/null || true; }
  docker build -t "$LOCAL_IMAGE" .
  docker compose up -d
  docker compose ps
}

# --------------------------------------------------------------------------------------------------------------
# New: sh update.sh pocketknife-<version>.zip
# --------------------------------------------------------------------------------------------------------------
# Program folder = the folder containing this script (e.g. /opt/pocketknife). The update happens there.
from_zip() {
  ZIP="$1"
  [ -f "$ZIP" ] || die "File not found: $ZIP"
  ZIP="$(abspath "$(dirname "$ZIP")")/$(basename "$ZIP")"
  INST="$SCRIPTDIR"
  need_docker

  info "Checking $ZIP"
  # Checksum from the release (pocketknife-X.zip.sha256 next to the ZIP): if present, it must match
  if [ -f "$ZIP.sha256" ] && command -v sha256sum >/dev/null 2>&1; then
    WANT=$(awk '{print $1; exit}' "$ZIP.sha256")
    HAVE=$(sha256sum "$ZIP" | awk '{print $1}')
    [ "$WANT" = "$HAVE" ] || die "Checksum of $(basename "$ZIP") does not match $(basename "$ZIP").sha256 – damaged or altered download. Nothing was changed."
    say "  Checksum OK ($(basename "$ZIP").sha256)."
  fi
  TMP=$(mktemp -d "$INST/.pk-update.XXXXXX") || die "Cannot create a temporary folder in $INST."
  # STAGE: empty = nothing changed yet; swap = program files (partly) swapped, the previous version is still running
  # → on ANY abort (error, Ctrl+C, full disk) put everything back automatically; started = new version started
  STAGE=""
  trap 'on_exit' EXIT
  trap 'echo; say "Aborted (Ctrl+C)."; exit 130' INT TERM
  : > "$TMP/protect"
  mounts >> "$TMP/protect"   # folders mounted by the running version – never delete
  if command -v unzip >/dev/null 2>&1; then
    unzip -q "$ZIP" -d "$TMP/zip" || die "The ZIP cannot be unpacked (damaged or incompletely downloaded?)."
  elif command -v python3 >/dev/null 2>&1; then
    python3 -I -m zipfile -e "$ZIP" "$TMP/zip" || die "The ZIP cannot be unpacked."
  else
    die "\"unzip\" is missing for unpacking (apt install unzip)."
  fi
  SRC=""
  for d in "$TMP"/zip/*/; do
    d=${d%/}
    if [ -f "$d/VERSION" ] && [ -f "$d/docker-compose.yml" ] && [ -d "$d/app" ]; then SRC="$d"; break; fi
  done
  [ -n "$SRC" ] || die "The ZIP does not contain a POCKET KNIFE version (VERSION, docker-compose.yml and app/ are missing)."
  NEWVER=$(tr -d ' \r\n' < "$SRC/VERSION")
  printf '%s' "$NEWVER" | grep -Eq '^[0-9]+(\.[0-9]+){1,3}(-s[0-9]+)?$' || die "Invalid version number in the ZIP: \"$NEWVER\""

  # Where does the current configuration come from? The running version (also from an old version folder), otherwise
  # the program folder itself
  RUN=$(running_dir)
  if [ -n "$RUN" ] && [ -d "$RUN" ]; then RUN=$(abspath "$RUN"); else RUN=""; fi
  if [ -n "$RUN" ] && [ -f "$RUN/.env" ]; then ENVSRC="$RUN"
  elif [ -f "$INST/.env" ]; then ENVSRC="$INST"
  else die "No .env found (neither in $INST nor with the running version). First installation: see README."
  fi
  OLDVER=$(tr -d ' \r\n' < "$ENVSRC/VERSION" 2>/dev/null || echo "?")
  MIGRATE=""
  [ "$(canon "$ENVSRC")" != "$(canon "$INST")" ] && MIGRATE=1

  info "Overview"
  say "  Program folder:  $INST"
  say "  Current:         $OLDVER$( [ -n "$MIGRATE" ] && printf '  (runs from %s – will be moved into the program folder)' "$ENVSRC")"
  say "  New:             $NEWVER"
  DATA_RAW=$(envval "$ENVSRC/.env" PK_DATA_PATH)
  say "  Database:        $(canon "$(resolve "$ENVSRC" "${DATA_RAW:-./data}")")"
  if [ -n "$OLDVER" ] && [ "$OLDVER" != "?" ] && [ "$OLDVER" != "$NEWVER" ] && ver_lt "$NEWVER" "$OLDVER"; then
    say "  CAUTION: $NEWVER is OLDER than the current version $OLDVER (downgrade)."
  fi
  echo
  ask "Update to POCKET KNIFE $NEWVER now?" || { say "Cancelled – nothing changed."; exit 0; }

  # --- Swap program files; the previous ones go to .pk-vorher/ (exactly one state) -------------------------------
  # Only known program files (fixed list) – never anything that points into a protected folder
  : > "$TMP/items"
  for n in $PROGRAM_ITEMS; do
    [ -e "$SRC/$n" ] || [ -e "$INST/$n" ] || [ -L "$INST/$n" ] || continue
    if [ -e "$INST/$n" ] || [ -L "$INST/$n" ]; then
      c=$(canon "$INST/$n"); bad=""
      while IFS= read -r p; do
        [ -n "$p" ] || continue
        pc=$(canon "$p")
        if inside "$c" "$pc"; then bad="$pc"; fi   # a protected folder is inside → never move
      done < "$TMP/protect"
      if [ -n "$bad" ]; then say "  skipped: $n (points to protected folder $bad)"; continue; fi
    fi
    printf '%s\n' "$n" >> "$TMP/items"
  done
  for p in "$SRC"/* "$SRC"/.[!.]*; do
    [ -e "$p" ] || continue
    n=$(basename "$p")
    case " $PROGRAM_ITEMS update.sh " in *" $n "*) ;; *) say "  Note: \"$n\" in the ZIP is not part of the program – not taken over." ;; esac
  done
  PREV="$INST/.pk-vorher"
  MARK="$INST/.pk-update-unvollstaendig"
  ROTATE=1; UNFINISHED=""
  if [ -e "$MARK" ]; then
    ROTATE=""; say "  The last update was not completed – the saved state in .pk-vorher/ is kept."
    # Only an abort IN THE MIDDLE of rotating (marker .pk-rotiert-gerade) leaves the complete predecessor in .pk-vorher.alt.
    # Was the rotation finished (e.g. the new version did not start and going back was declined), .pk-vorher/ holds
    # the last good version – then .pk-vorher.alt is only the one before and is NOT put back over it.
    if [ -e "$INST/.pk-rotiert-gerade" ] && [ -d "$PREV.alt" ]; then rm -rf "${PREV:?}" && mv "$PREV.alt" "$PREV"
    else rm -rf "${PREV:?}.alt"
    fi
    rm -f "$INST/.pk-rotiert-gerade"
    UNFINISHED=1
  elif [ "$OLDVER" = "$NEWVER" ] && [ -d "$PREV" ]; then
    ROTATE=""; say "  Same version again – the saved state in .pk-vorher/ ($(tr -d ' \r\n' < "$PREV/VERSION" 2>/dev/null || echo ?)) is kept."
  fi
  if [ -n "$ROTATE" ]; then KEEP="$PREV"; else KEEP="$TMP/bisher"; fi
  HADENV=""; [ -f "$INST/.env" ] && HADENV=1
  mkdir -p "$TMP/bisher"
  [ -n "$HADENV" ] && cp "$INST/.env" "$TMP/bisher/.env"   # BEFORE the swap (full disk → nothing changed yet)
  printf '%s\n' "$NEWVER" > "$MARK"
  : > "$TMP/moved_old"; : > "$TMP/moved_new"
  STAGE=swap
  if [ -n "$ROTATE" ]; then
    : > "$INST/.pk-rotiert-gerade"   # until all previous files are in .pk-vorher/ (see above)
    rm -rf "$PREV.alt"; [ -d "$PREV" ] && mv "$PREV" "$PREV.alt"
    mkdir -p "$PREV"
    [ -n "$HADENV" ] && cp "$TMP/bisher/.env" "$PREV/.env"
  fi
  while IFS= read -r n; do
    # record first, then move – so putting back knows exactly what happened even after an abort in between
    if [ -e "$INST/$n" ] || [ -L "$INST/$n" ]; then printf '%s\n' "$n" >> "$TMP/moved_old"; mv "$INST/$n" "$KEEP/$n"; fi
  done < "$TMP/items"
  rm -f "$INST/.pk-rotiert-gerade"
  while IFS= read -r n; do
    if [ -e "$SRC/$n" ]; then printf '%s\n' "$n" >> "$TMP/moved_new"; mv "$SRC/$n" "$INST/$n"; fi
  done < "$TMP/items"

  # --- .env: take it over when moving from a version folder; write relative paths (also default ./data, ./repo) as absolute
  if [ -n "$MIGRATE" ]; then
    ENVCHANGED=1
    cp "$ENVSRC/.env" "$INST/.env"
    [ -f "$ENVSRC/docker-compose.override.yml" ] && cp "$ENVSRC/docker-compose.override.yml" "$INST/" && say "docker-compose.override.yml taken over."
    for k in PK_DATA_PATH PK_REPO_MOUNT PK_BACKUP_PATH; do
      v=$(envval "$INST/.env" "$k")
      if [ -z "$v" ]; then
        case "$k" in PK_DATA_PATH) v="./data" ;; PK_REPO_MOUNT) v="./repo" ;; *) continue ;; esac
        [ -e "$ENVSRC/${v#./}" ] || continue
      fi
      case "$v" in
        /*) ;;
        *) absv=$(canon "$(resolve "$ENVSRC" "$v")"); env_set "$INST/.env" "$k" "$absv"
           say "$k pointed into the version folder – now fixed: $absv"
           [ "$k" = PK_DATA_PATH ] && MOVEHINT="$absv" ;;
      esac
    done
  fi
  # Image name for docker-compose.yml (PK_IMAGE): from the registry the container shows where it comes from
  # (ghcr.io/…/pocketknife:latest, like other containers); the ZIP way builds locally as pocketknife:local
  # (without the entry docker-compose.yml would use the registry image).
  # The .env is put back with everything else if the update fails.
  WANT_IMAGE="$LOCAL_IMAGE"; [ -n "$PULLREF" ] && WANT_IMAGE="$PULLNAME"
  if [ "$(envval "$INST/.env" PK_IMAGE)" != "$WANT_IMAGE" ]; then
    env_set "$INST/.env" PK_IMAGE "$WANT_IMAGE"; ENVCHANGED=1
  fi
  chmod 600 "$INST/.env" 2>/dev/null || true

  cd "$INST"
  # Keep the image that is running now as „previous“: going back then needs neither a build nor the internet
  HAVEPREV=""; OLDREF=""
  PREVID=$(docker inspect pocketknife --format '{{.Image}}' 2>/dev/null || true)
  OLDREF=$(docker inspect pocketknife --format '{{.Config.Image}}' 2>/dev/null || true)
  # RUN_BACK: the image running now – going back in THIS run starts it again (matches the program files put back).
  # BACK_IMAGE („rollback“): the image of the last GOOD version (the one in .pk-vorher/). Normally both are the same.
  # Was the last update not completed (going back declined), what runs now is the not-good version: then „rollback“
  # keeps the last good image (never overwritten) and only RUN_BACK points to the running one.
  RUN_BACK="$BACK_IMAGE"
  if [ -n "$UNFINISHED" ] && docker image inspect "$BACK_IMAGE" >/dev/null 2>&1; then
    RUN_BACK="pocketknife:rollback-run"
    if [ -n "$PREVID" ] && docker tag "$PREVID" "$RUN_BACK" 2>/dev/null; then HAVEPREV=1; fi
  elif [ -n "$PREVID" ] && docker tag "$PREVID" "$BACK_IMAGE" 2>/dev/null; then HAVEPREV=1
  elif docker image inspect "$LOCAL_IMAGE" >/dev/null 2>&1; then
    docker tag "$LOCAL_IMAGE" "$BACK_IMAGE" && HAVEPREV=1 && OLDREF="$LOCAL_IMAGE"
  fi
  case "$OLDREF" in ''|*@*) OLDREF="$LOCAL_IMAGE" ;; esac
  if [ -n "$PULLREF" ]; then
    info "Using the pulled image $PULLNAME for POCKET KNIFE $NEWVER (no build) ..."
  else
    info "Building POCKET KNIFE $NEWVER (this can take a few minutes) ..."
    if ! docker build -t "$LOCAL_IMAGE" .; then
      [ -n "$HAVEPREV" ] && docker tag "$RUN_BACK" "$OLDREF" 2>/dev/null
      restore_prev
      [ "$STAGE" = restored ] || die "The build failed; the previous program files could not be put back completely (see above)."
      die "The build failed – the previous version keeps running unchanged (program files restored)."
    fi
  fi
  info "Starting POCKET KNIFE $NEWVER ..."
  STAGE=started
  UPOK=1
  docker compose up -d || UPOK=""
  [ -n "$UPOK" ] && printf 'Waiting for the start '
  if [ -z "$UPOK" ] || ! wait_healthy; then
    echo
    say "The new version does not report as healthy. Last log lines:"
    docker compose logs --tail 30 pocketknife || true
    if ask "Go back to the previous version $OLDVER?"; then
      STAGE=swap   # if putting back is interrupted, on_exit finishes it
      restore_prev
      [ "$STAGE" = restored ] || die "The previous program files could not be put back completely (see above)."
      BACKDIR="$INST"; [ -n "$MIGRATE" ] && BACKDIR="$ENVSRC"
      BACKUP_UP="back_build"
      # previous image still there → start exactly that one (no build, no internet)
      # (under the name the previous .env expects – e.g. …/pocketknife:latest already points to the NEW image)
      if [ -n "$HAVEPREV" ] && docker tag "$RUN_BACK" "$OLDREF" 2>/dev/null; then BACKUP_UP="docker compose up -d"; fi
      if ! ( cd "$BACKDIR" && if [ "$BACKUP_UP" = back_build ]; then back_build; else $BACKUP_UP; fi ); then
        die "The previous version $OLDVER does not start either (program files are restored). The cause is in:
  cd $BACKDIR && docker compose logs --tail 50   (e.g. port in use, Docker service disturbed)"
      fi
      [ -n "$UNFINISHED" ] || rm -f "$MARK"   # still unfinished: .pk-vorher/ keeps the last GOOD version
      say "Previous version $OLDVER is running again."
      say "If the new version already extended the database and the old one shows errors: Settings → Backup →"
      say "backup \"vor-$NEWVER\" (README: restore)."
    fi
    exit 1
  fi
  # Done: the new state applies, .pk-vorher/ contains exactly the previous one
  rm -f "$MARK"; rm -rf "$PREV.alt"
  # the image before this (now good) version stays as „previous“ (for going back by hand); a failed update never
  # overwrites it – so the last good image is not pruned
  if docker image inspect "$BACK_IMAGE" >/dev/null 2>&1; then
    docker tag "$BACK_IMAGE" "$PREV_IMAGE" 2>/dev/null && docker rmi "$BACK_IMAGE" >/dev/null 2>&1 || true
  fi
  [ "$RUN_BACK" = "$BACK_IMAGE" ] || docker rmi "$RUN_BACK" >/dev/null 2>&1 || true
  STAGE=done
  mounts >> "$TMP/protect"   # and those of the new version

  info "Self-test (checks that fixed bugs have not come back) ..."
  # -I: als root nie Code aus Benutzer-site-packages oder PYTHON*-Variablen; HOME, an dem niemand etwas ablegen kann
  if docker compose exec -T -e HOME=/nonexistent pocketknife python3 -I /opt/pocketknife/tools/selftest.py > "$TMP/selftest.txt" 2>&1; then
    tail -n 1 "$TMP/selftest.txt"
  else
    tail -n 15 "$TMP/selftest.txt" || true
    say "WARNING: The self-test reports errors (see above). POCKET KNIFE is running anyway; please pass on the output."
  fi

  cleanup
  watch_auto   # 2.32.9: "Update now" in the web interface works from now on
  # Finally replace the update script itself (new file via mv – the running script stays untouched)
  if [ -f "$SRC/update.sh" ]; then
    cp "$SRC/update.sh" "$INST/update.sh.neu" && chmod +x "$INST/update.sh.neu" && mv "$INST/update.sh.neu" "$INST/update.sh"
  fi
  info "Done: POCKET KNIFE $NEWVER is running from $INST."
  if [ -n "${MOVEHINT:-}" ]; then
    MH=$(dirname "$MOVEHINT")
    say "Tip: database and backups are still in the old version folder $MH; it is therefore never cleaned up."
    say "      To move them (database AND backups together):"
    say "        docker compose down"
    say "        mv \"$MOVEHINT\" \"$INST/data\""
    say "        mv \"$MOVEHINT-backup\" \"$INST/data-backup\"     (if present)"
    say "        mv \"$MH/repo\" \"$INST/repo\"                (if present; then PK_REPO_MOUNT=$INST/repo)"
    say "        in $INST/.env: PK_DATA_PATH=$INST/data  (and adjust or remove PK_BACKUP_PATH, if set)"
    say "        docker compose up -d"
  fi
}

# --------------------------------------------------------------------------------------------------------------
# Cleanup: old version folders (from 2.23.3/2.24), the ZIP and old images
# --------------------------------------------------------------------------------------------------------------
inside() {
  # 0 if path $2 is inside folder $1 (or equal) – both fully resolved
  case "$2/" in "$1"/*) return 0 ;; esac
  return 1
}
cleanup() {
  info "Cleanup"
  P1=$(envval "$INST/.env" PK_DATA_PATH); P1=$(resolve "$INST" "${P1:-./data}")
  P2=$(envval "$INST/.env" PK_BACKUP_PATH); P2=$(resolve "$INST" "${P2:-$P1-backup}")
  P3=$(envval "$INST/.env" PK_REPO_MOUNT); P3=$(resolve "$INST" "${P3:-./repo}")
  printf '%s\n%s\n%s\n' "$P1" "$P2" "$P3" >> "$TMP/protect"
  : > "$TMP/protect.c"
  while IFS= read -r p; do [ -n "$p" ] && { canon "$p"; echo; } >> "$TMP/protect.c"; done < "$TMP/protect"
  INSTC=$(canon "$INST")
  : > "$TMP/del"
  # Version folders in the program folder (created by update.sh 2.23.3/2.24 or unpacked by hand)
  for d in "$INST"/pocketknife-*/ "$INST"/pocketknife/; do
    [ -d "$d" ] || continue
    d=$(canon "${d%/}")
    [ "$d" = "$INSTC" ] && continue
    inside "$d" "$INSTC" && continue   # never a folder that contains the program folder
    [ -f "$d/VERSION" ] && [ -f "$d/docker-compose.yml" ] && [ -d "$d/app" ] || continue
    grep -qxF "$d" "$TMP/del" && continue
    skip=""
    while IFS= read -r p; do
      if [ -z "$skip" ] && [ -n "$p" ] && inside "$d" "$p"; then skip="contains $p"; fi
    done < "$TMP/protect.c"
    for sub in data data-backup repo; do
      if [ -z "$skip" ] && [ -d "$d/$sub" ] && [ -n "$(ls -A "$d/$sub" 2>/dev/null)" ]; then skip="$sub/ is not empty"; fi
    done
    if [ -z "$skip" ] && [ -n "$(find "$d" -maxdepth 4 \( -name '*.db' -o -name '*.db-wal' -o -name '*.sqlite' \) -print 2>/dev/null | head -n 1)" ]; then
      skip="contains a database file"
    fi
    if [ -n "$skip" ]; then say "  kept: $d ($skip)"; continue; fi
    printf '%s\n' "$d" >> "$TMP/del"
  done
  if [ -s "$TMP/del" ]; then
    say "  Old version folders (no longer needed – the program runs from $INST, the previous state is in .pk-vorher/):"
    while IFS= read -r d; do say "    $d ($(tr -d ' \r\n' < "$d/VERSION"))"; done < "$TMP/del"
    if ask "  Delete these folders?"; then
      while IFS= read -r d; do rm -rf -- "$d" && say "    deleted: $d"; done < "$TMP/del"
    fi
  else
    say "  No old version folders."
  fi
  if [ -n "$PULLREF" ]; then :   # ZIP came from the image (temporary folder, removed automatically)
  elif [ -f "$ZIP" ] && ask "  Delete the ZIP file $(basename "$ZIP")?"; then rm -f -- "$ZIP"; fi
  if ask "  Remove unused Docker images of old builds (docker image prune)?"; then
    docker image prune -f >/dev/null && say "    done."
  fi
}

# --------------------------------------------------------------------------------------------------------------
# Since 2.27.1: sh update.sh --pull [version] – ready-made image from the registry
# --------------------------------------------------------------------------------------------------------------
from_registry() {
  need_docker
  INST="$SCRIPTDIR"
  REPO=$(envval "$INST/.env" PK_IMAGE_REPO)
  [ -n "$REPO" ] || REPO="$DEFAULT_IMAGE_REPO"
  case "$REPO" in *[!a-z0-9./_:-]*|"") die "PK_IMAGE_REPO in the .env is invalid: \"$REPO\" (lower case, e.g. ghcr.io/owner/pocketknife)" ;; esac
  TAG="${PULLTAG:-latest}"
  case "$TAG" in *[!0-9A-Za-z._-]*) die "Invalid version: \"$TAG\"" ;; esac
  info "Pulling $REPO:$TAG"
  if ! docker pull "$REPO:$TAG"; then
    say ""
    say "The image could not be pulled. Typical causes:"
    say "  - not logged in to the (private) registry – once, as the user running this update:"
    say "      docker login ${REPO%%/*} -u <user>     (password: token with the right read:packages)"
    say "  - version \"$TAG\" does not exist, or no internet access (then: sh update.sh pocketknife-<version>.zip)"
    exit 1
  fi
  # Exactly this image from now on (by digest – the tag may move later)
  PULLREF=$(docker image inspect "$REPO:$TAG" --format '{{range .RepoDigests}}{{println .}}{{end}}' | grep -F "$REPO@" | head -n 1)
  [ -n "$PULLREF" ] || PULLREF="$REPO:$TAG"
  PULLNAME="$REPO:$TAG"   # name the container shows (like other containers: …/pocketknife:latest)
  say "  Image: $PULLREF"
  PD=$(mktemp -d "$INST/.pk-pull.XXXXXX") || die "Cannot create a temporary folder in $INST."
  # removed again at the end (also on errors); from_zip installs its own handler, so remove it from there as well
  PULLDIR="$PD"
  CID=$(docker create "$PULLREF" true) || { rm -rf "$PD"; die "The image cannot be opened."; }
  if ! docker cp "$CID:/opt/pocketknife/dist/." "$PD/" >/dev/null 2>&1; then
    docker rm "$CID" >/dev/null 2>&1 || true; rm -rf "$PD"
    die "The image contains no program ZIP (built without dist/?) – please update with the ZIP file."
  fi
  docker rm "$CID" >/dev/null 2>&1 || true
  Z=$(ls "$PD"/pocketknife-*.zip 2>/dev/null | head -n 1)
  [ -n "$Z" ] && [ -f "$Z.sha256" ] || { rm -rf "$PD"; die "Program ZIP or its checksum is missing in the image."; }
  if ! ( cd "$PD" && sha256sum -c "$(basename "$Z").sha256" >/dev/null 2>&1 ); then
    rm -rf "$PD"; die "Checksum of the program ZIP in the image does not match – update cancelled."
  fi
  IMGVER=$(docker image inspect "$PULLREF" --format '{{ index .Config.Labels "org.opencontainers.image.version" }}' 2>/dev/null || true)
  ZIPVER=$(basename "$Z" .zip); ZIPVER=${ZIPVER#pocketknife-}
  if [ -n "$IMGVER" ] && [ "$IMGVER" != "$ZIPVER" ]; then rm -rf "$PD"; die "Image ($IMGVER) and program ZIP ($ZIPVER) do not match."; fi
  say "  Program ZIP $(basename "$Z") checked (SHA-256)."
  RUNIMG=$(docker inspect pocketknife --format '{{.Image}}' 2>/dev/null || true)
  NEWIMG=$(docker image inspect "$PULLREF" --format '{{.Id}}' 2>/dev/null || true)
  if [ -n "$RUNIMG" ] && [ "$RUNIMG" = "$NEWIMG" ] && [ "$(tr -d ' \r\n' < "$INST/VERSION" 2>/dev/null)" = "$ZIPVER" ]; then
    rm -rf "$PD"
    RUNREF=$(docker inspect pocketknife --format '{{.Config.Image}}' 2>/dev/null || true)
    if [ "$RUNREF" != "$PULLNAME" ] && [ -f "$INST/.env" ]; then
      # Same image, but the container still shows the old name (e.g. pocketknife:local) → only switch the name
      info "POCKET KNIFE $ZIPVER is up to date – the container now shows $PULLNAME"
      cp "$INST/.env" "$INST/.env.pk-alt" && chmod 600 "$INST/.env.pk-alt"
      env_set "$INST/.env" PK_IMAGE "$PULLNAME"; chmod 600 "$INST/.env" 2>/dev/null || true
      if ! ( cd "$INST" && docker compose up -d ); then
        mv "$INST/.env.pk-alt" "$INST/.env"; ( cd "$INST" && docker compose up -d ) || true
        die "Starting with the new name failed – back to the previous name."
      fi
      rm -f "$INST/.env.pk-alt"
      printf 'Waiting for the start '
      wait_healthy || say "WARNING: POCKET KNIFE does not report as healthy – see: docker compose logs pocketknife"
      watch_auto
      exit 0
    fi
    say ""
    say "POCKET KNIFE $ZIPVER is already up to date (exactly this image is running)."
    watch_auto
    exit 0
  fi
  from_zip "$Z"
}

# --------------------------------------------------------------------------------------------------------------
# Since 2.32.9: update requested in the web interface (Settings → Maintenance / "Update now")
#   The container never gets access to Docker. It only puts a request into <data>/update/request; this helper runs
#   on the host every 5 minutes (sh update.sh --watch-install sets it up) and carries out  update.sh --pull <version> -y
#   – with the usual backup, checksum, health check, automatic going back and self-test – and writes the state and
#   the log back into <data>/update/. The request is not trusted: only a version number (or "latest"), never older than
#   the running version; the folder belongs to the container, so nothing in it is followed as a link and every file is
#   written as a new file (mktemp + mv).
# --------------------------------------------------------------------------------------------------------------
WATCH_UNIT="pocketknife-update"
watch_datadir() {
  D=$(envval "$SCRIPTDIR/.env" PK_DATA_PATH); D=$(resolve "$SCRIPTDIR" "${D:-./data}")
  if [ ! -f "$D/pocketknife.db" ] && command -v docker >/dev/null 2>&1; then
    m=$(docker inspect pocketknife --format '{{ range .Mounts }}{{ if eq .Destination "/data" }}{{ .Source }}{{ end }}{{ end }}' 2>/dev/null || true)
    [ -n "$m" ] && D="$m"
  fi
  printf '%s' "$D"
}
# write a file into the update folder: new file next to it, then rename (never through a link planted there)
watch_put() {
  t=$(mktemp "./.pk-host.XXXXXX") || return 1
  cat > "$t" && chmod 644 "$t" && mv -f "$t" "./$1" || { rm -f "$t"; return 1; }
}
watch_status() {  # state message
  { printf 'state=%s\n' "$1"
    printf 'from=%s\nto=%s\ntarget=%s\nby=%s\n' "${W_FROM:-}" "$(tr -d ' \r\n' < "$SCRIPTDIR/VERSION" 2>/dev/null || true)" "${W_REQ:-}" "${W_BY:-}"
    printf 'started=%s\n' "${W_START:-}"
    [ "$1" = running ] || printf 'finished=%s\n' "$(date +%s)"
    printf 'message=%s\n' "$(printf '%s' "${2:-}" | tr -d '\r\n' | cut -c1-280)"
  } | watch_put status
}
watch() {
  YES=1
  D=$(watch_datadir)
  [ -d "$D" ] || die "Data folder not found ($D) – PK_DATA_PATH in $SCRIPTDIR/.env?"
  WU="$D/update"
  if [ -L "$WU" ]; then die "$WU is a symbolic link – not used."; fi
  if [ ! -d "$WU" ]; then
    mkdir -p "$WU" && chown "$(stat -c '%u:%g' "$D")" "$WU" 2>/dev/null || true
  fi
  # work INSIDE the folder (relative names from here on): even if it were swapped for a link later, every file stays
  # in exactly this folder
  cd "$WU" 2>/dev/null || exit 0
  [ "$(pwd -P)" = "$(canon "$D")/update" ] || die "$WU does not resolve to $(canon "$D")/update – not used."
  # one helper at a time (an update takes a few minutes, the timer may start again meanwhile)
  LOCK="$SCRIPTDIR/.pk-watch.lock"
  if ! mkdir "$LOCK" 2>/dev/null; then
    op=$(cat "$LOCK/pid" 2>/dev/null || true)
    if [ -n "$op" ] && kill -0 "$op" 2>/dev/null; then exit 0; fi
    rm -rf "$LOCK"; mkdir "$LOCK" 2>/dev/null || exit 0
  fi
  echo $$ > "$LOCK/pid"
  trap 'rm -rf "$LOCK"' EXIT
  printf 'seen=%s\n' "$(date +%s)" | watch_put helper || true
  R="request"
  [ -f "$R" ] && [ ! -L "$R" ] || { [ -L "$R" ] && rm -f "$R"; exit 0; }
  REQTXT=$(head -c 1024 "$R" 2>/dev/null || true)
  rm -f "$R"   # taken – also if it is invalid (never twice)
  W_REQ=$(printf '%s\n' "$REQTXT" | sed -n 's/^version=//p' | head -n 1 | tr -d ' \r')
  W_BY=$(printf '%s\n' "$REQTXT" | sed -n 's/^by=//p' | head -n 1 | tr -cd 'A-Za-z0-9._@\\-' | cut -c1-64)
  W_FROM=$(tr -d ' \r\n' < "$SCRIPTDIR/VERSION" 2>/dev/null || echo "?")
  W_START=$(date +%s)
  if ! printf '%s' "$W_REQ" | grep -Eq '^(latest|[0-9]+(\.[0-9]+){1,3}(-s[0-9]+)?)$'; then
    W_REQ=""; watch_status refused "invalid"; exit 0   # invalid request (no version number)
  fi
  if [ "$W_REQ" != latest ] && [ "$W_FROM" != "?" ] && ! ver_lt "$W_FROM" "$W_REQ"; then
    watch_status refused "older"; exit 0   # not newer than the running version – going back only by hand on the host
  fi
  watch_status running ""
  L=$(mktemp "$SCRIPTDIR/.pk-watch-log.XXXXXX") || { watch_status failed "cannot create a log file in $SCRIPTDIR"; exit 1; }
  trap 'rm -rf "$LOCK"; rm -f "$L"; [ -n "${CP:-}" ] && kill "$CP" 2>/dev/null' EXIT
  # show progress in the web interface (as long as it is running)
  ( while :; do sleep 5; tail -c 32768 "$L" 2>/dev/null | watch_put update.log || true; done ) &
  CP=$!
  set +e
  if [ "$W_REQ" = latest ]; then sh "$SCRIPTDIR/update.sh" --pull -y < /dev/null > "$L" 2>&1
  else sh "$SCRIPTDIR/update.sh" --pull "$W_REQ" -y < /dev/null > "$L" 2>&1
  fi
  rc=$?
  set -e
  kill "$CP" 2>/dev/null || true; wait "$CP" 2>/dev/null || true; CP=""
  tail -c 32768 "$L" | watch_put update.log || true
  if [ "$rc" = 0 ] && grep -q "is already up to date" "$L"; then watch_status current ""
  elif [ "$rc" = 0 ]; then watch_status done ""
  elif grep -q "is running again" "$L"; then watch_status rolledback ""
  else watch_status failed "$(grep '^ERROR:' "$L" | tail -n 1 | sed 's/^ERROR: //')"
  fi
  exit 0
}

# The helper is set up AUTOMATICALLY by every update (update.sh runs as root on the host anyway) – nothing to switch
# on. Switched off for good with  sh update.sh --watch-remove  (PK_UPDATE_HELPER=off in the .env).
# PK_SYSROOT only for the self-test (instead of / – nothing is written to the real /etc there).
SYSROOT="${PK_SYSROOT:-}"
watch_installed() { [ -f "$SYSROOT/etc/systemd/system/$WATCH_UNIT.timer" ] || [ -f "$SYSROOT/etc/cron.d/$WATCH_UNIT" ]; }
# set up the timer; 0 = set up (or already there), 1 = not possible here (message printed)
watch_setup() {
  if [ -f "$SYSROOT/etc/synoinfo.conf" ] && ! { command -v systemctl >/dev/null 2>&1 && [ -d "$SYSROOT/run/systemd/system" ]; }; then
    say "Synology: please create the task in DSM once (Control Panel → Task Scheduler → Create → Scheduled Task →"
    say "  User-defined script; user root, daily, repeat every 5 minutes; script:  sh $SCRIPTDIR/update.sh --watch)"
    return 1
  fi
  if command -v systemctl >/dev/null 2>&1 && [ -d "$SYSROOT/run/systemd/system" ]; then
    cat > "$SYSROOT/etc/systemd/system/$WATCH_UNIT.service" <<UNIT
[Unit]
Description=POCKET KNIFE – carry out an update requested in the web interface
After=docker.service

[Service]
Type=oneshot
ExecStart=/bin/sh $SCRIPTDIR/update.sh --watch
UNIT
    cat > "$SYSROOT/etc/systemd/system/$WATCH_UNIT.timer" <<UNIT
[Unit]
Description=POCKET KNIFE – look for an update request every 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
AccuracySec=30s

[Install]
WantedBy=timers.target
UNIT
    if systemctl daemon-reload && systemctl enable --now "$WATCH_UNIT.timer" >/dev/null 2>&1; then
      say "  Update helper: systemd timer $WATCH_UNIT.timer (every 5 minutes) – \"Update now\" in the web interface works."
      [ -f "$SYSROOT/etc/synoinfo.conf" ] && say "  Synology: after a DSM update, check Settings → Maintenance; if the helper is gone, run  sh update.sh --watch-install"
      return 0
    fi
    rm -f "$SYSROOT/etc/systemd/system/$WATCH_UNIT.timer" "$SYSROOT/etc/systemd/system/$WATCH_UNIT.service"
  fi
  if [ -d "$SYSROOT/etc/cron.d" ]; then
    printf '# POCKET KNIFE – carry out an update requested in the web interface (update.sh --watch-remove removes this)\n*/5 * * * * root /bin/sh %s/update.sh --watch >/dev/null 2>&1\n' "$SCRIPTDIR" > "$SYSROOT/etc/cron.d/$WATCH_UNIT"
    chmod 644 "$SYSROOT/etc/cron.d/$WATCH_UNIT"
    say "  Update helper: /etc/cron.d/$WATCH_UNIT (every 5 minutes) – \"Update now\" in the web interface works."
    return 0
  fi
  say "  Neither systemd nor /etc/cron.d – for \"Update now\" run  sh $SCRIPTDIR/update.sh --watch  every 5 minutes as root."
  return 1
}
# called by every update: set up the helper unless it is there or was switched off
watch_auto() {
  [ "$(id -u)" = 0 ] || return 0
  [ "$(envval "$SCRIPTDIR/.env" PK_UPDATE_HELPER)" = off ] && return 0
  watch_installed && return 0
  info "Update from the web interface"
  watch_setup || say "  (to stop this note: PK_UPDATE_HELPER=off in the .env)"
  return 0
}
watch_install() {
  need_docker
  [ "$(id -u)" = 0 ] || die "Please run as root: sudo sh update.sh --watch-install"
  D=$(watch_datadir); [ -d "$D" ] || die "Data folder not found ($D) – install POCKET KNIFE first."
  if [ "$(envval "$SCRIPTDIR/.env" PK_UPDATE_HELPER)" = off ]; then env_set "$SCRIPTDIR/.env" PK_UPDATE_HELPER on; chmod 600 "$SCRIPTDIR/.env" 2>/dev/null || true; fi
  watch_setup || exit 1
  sh "$SCRIPTDIR/update.sh" --watch || true
  say "Private registry: log in once as root:  docker login ghcr.io -u <user>  (token with read:packages)."
  say "Recommended: no automatic updates of POCKET KNIFE by Watchtower, Dockhand, Tugtainer & co. – switch them off for it."
}
watch_remove() {
  [ "$(id -u)" = 0 ] || die "Please run as root: sudo sh update.sh --watch-remove"
  if [ -f "$SYSROOT/etc/systemd/system/$WATCH_UNIT.timer" ]; then
    systemctl disable --now "$WATCH_UNIT.timer" >/dev/null 2>&1 || true
    rm -f "$SYSROOT/etc/systemd/system/$WATCH_UNIT.timer" "$SYSROOT/etc/systemd/system/$WATCH_UNIT.service"
    systemctl daemon-reload 2>/dev/null || true
    say "Removed: systemd timer $WATCH_UNIT.timer"
  fi
  [ -f "$SYSROOT/etc/cron.d/$WATCH_UNIT" ] && rm -f "$SYSROOT/etc/cron.d/$WATCH_UNIT" && say "Removed: /etc/cron.d/$WATCH_UNIT"
  [ -f "$SCRIPTDIR/.env" ] && { env_set "$SCRIPTDIR/.env" PK_UPDATE_HELPER off; chmod 600 "$SCRIPTDIR/.env" 2>/dev/null || true; }
  D=$(watch_datadir); [ -d "$D/update" ] && rm -f "$D/update/helper"
  say "Update from the web interface is switched off – also for later updates (PK_UPDATE_HELPER=off in the .env;"
  say "on again: sh update.sh --watch-install). Synology: delete the task in the Task Scheduler."
}

case "$WATCH" in
  run) watch ;;
  install) watch_install; exit 0 ;;
  remove) watch_remove; exit 0 ;;
esac

if [ -n "$PULL" ]; then
  from_registry
  exit 0
fi
case "$ARG" in
  *.zip|*.ZIP) from_zip "$ARG" ;;
  *) classic "$ARG" ;;
esac
