#!/bin/sh
# POCKET KNIFE – update the containers by hand (since 2.33: the program lives only in the image)
#
#   On the Docker host, in the folder with docker-compose.yml and .env (e.g. /opt/pocketknife):
#
#     sudo sh update.sh                  newest version (or the version pinned in the .env)
#     sudo sh update.sh 2.33.1           a specific version – pinned in the .env (PK_IMAGE, PK_GUACD_IMAGE)
#     sudo sh update.sh latest           back to "always the newest" (removes the pin)
#     sudo sh update.sh pocketknife-2.33.1-amd64.tar.gz    without internet: archive from the release (docker load)
#     sudo sh update.sh --cleanup        remove what older versions left in this folder (program files, ZIPs …)
#
#   → remembers the running images (pocketknife:previous), pulls the new ones (POCKET KNIFE and guacd), takes a newer
#     docker-compose.yml from the image (the old one stays as docker-compose.yml.alt), starts and waits for the health
#     check. If the new version does not start, it offers to start the previous images again (no download).
#     POCKET KNIFE backs up its database itself before a new version starts (Settings → Backup, "vor-<version>").
#   Nothing of the program is unpacked or placed on the host – only docker-compose.yml, .env and the data folders.
#   The same works with a container manager (Dockhand, Watchtower …) or "docker compose pull && docker compose up -d".
#   Private registry: log in once as the user running this (usually root):  docker login ghcr.io -u <user>
#
# Options: -y (answer all questions with "yes")
set -eu

SCRIPTDIR=$(cd "$(dirname "$0")" && pwd -P)
YES=0
ARG=""
CLEANUP=""
DEFAULT_REPO="ghcr.io/soleil0815/pocketknife"
PREV_IMAGE="pocketknife:previous"
PREV_GUACD="pocketknife-guacd:previous"
COMPOSE_IN_IMAGE="/opt/pocketknife/host"
WATCH_UNIT="pocketknife-update"
SYSROOT="${PK_SYSROOT:-}"   # only for the self-test (instead of / – nothing is written to the real /etc there)
WAIT_STEPS="${PK_WAIT_STEPS:-240}"   # health check: 240 × 5 s = 20 min (2.36.2: backup + migration may take long; image start period 15 min)
CALLDIR=$(pwd -P 2>/dev/null || pwd)   # 2.36.2: a relative archive path means the folder update.sh was called from
while [ $# -gt 0 ]; do
  case "$1" in
    -y|--yes) YES=1 ;;
    --cleanup) CLEANUP=1 ;;
    # 2.32.9–2.32.12 had an update helper (timer running "update.sh --watch") – it is switched off on its first run
    --watch|--watch-install|--watch-remove|--watch-auto) ARG="--watch-gone" ;;
    --pull) ;;                                      # old spelling: pulling is what this script always does
    --pull=*) ARG="${1#--pull=}" ;;
    --keep) ;;
    -h|--help) sed -n '2,21p' "$0"; exit 0 ;;
    -*) printf 'ERROR: unknown option %s (see sh update.sh --help)\n' "$1" >&2; exit 2 ;;
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
ver_lt() {  # "2.29.6-s3" = security update of 2.29.6 (higher than 2.29.6, lower than 2.29.7)
  [ "$1" = "$2" ] && return 1
  # four positions (2.29.6.1 is allowed) and the "-sN" as fifth field
  a=$(printf '%s' "$1" | awk -F'-s' '{n=split($1,p,"."); while(n<4){n++; p[n]=0}; printf "%s.%s.%s.%s.%s", p[1],p[2],p[3],p[4],($2==""?0:$2+1)}')
  b=$(printf '%s' "$2" | awk -F'-s' '{n=split($1,p,"."); while(n<4){n++; p[n]=0}; printf "%s.%s.%s.%s.%s", p[1],p[2],p[3],p[4],($2==""?0:$2+1)}')
  [ "$a" = "$b" ] && return 1
  first=$(printf '%s\n%s\n' "$a" "$b" | sort -t. -k1,1n -k2,2n -k3,3n -k4,4n -k5,5n | head -n 1)
  [ "$first" = "$a" ]
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
  # wait up to 20 minutes for "healthy" (during the start period of the image, "starting" is not an error)
  i=0
  while [ $i -lt "$WAIT_STEPS" ]; do
    st=$(docker inspect pocketknife --format '{{ if .State.Health }}{{ .State.Health.Status }}{{ else }}{{ .State.Status }}{{ end }}' 2>/dev/null || echo "?")
    case "$st" in
      healthy) say "POCKET KNIFE is running and responding (healthy)."; return 0 ;;
      unhealthy) return 1 ;;
      exited|dead) return 1 ;;
    esac
    printf '.'
    [ "$WAIT_STEPS" -lt 60 ] || sleep 5
    i=$((i + 1))
  done
  echo
  return 1
}


inside() {
  # 0 if path $2 is inside folder $1 (or equal) – both fully resolved
  case "$2/" in "$1"/*) return 0 ;; esac
  return 1
}
# Number in "PK_COMPOSE_VERSION: "2"" of a docker-compose.yml (0 = older file without it)
compose_ver() {
  v=$(sed -n 's/^[[:space:]]*PK_COMPOSE_VERSION:[[:space:]]*"\{0,1\}\([0-9]\{1,4\}\)"\{0,1\}[[:space:]]*$/\1/p' "$1" 2>/dev/null | head -n 1)
  printf '%s' "${v:-0}"
}
# Registry of the POCKET KNIFE image: from PK_IMAGE (without tag/digest), the old PK_IMAGE_REPO, or the default
image_repo() {
  r=$(envval "$INST/.env" PK_IMAGE)
  r=${r%%@*}
  case "$r" in
    ""|pocketknife:*|pocketknife) r=$(envval "$INST/.env" PK_IMAGE_REPO) ;;   # locally built (ZIP update up to 2.32)
    *:*) case "${r##*/}" in *:*) r=${r%:*} ;; esac ;;
  esac
  [ -n "$r" ] || r="$DEFAULT_REPO"
  case "$r" in *[!a-z0-9./_:-]*) die "Image name in the .env is invalid: \"$r\" (lower case, e.g. ghcr.io/owner/pocketknife)" ;; esac
  printf '%s' "$r"
}
image_version() {  # version label of an image (empty if unknown)
  docker image inspect "$1" --format '{{ index .Config.Labels "org.opencontainers.image.version" }}' 2>/dev/null | tr -d ' \r\n' || true
}

# --------------------------------------------------------------------------------------------------------------
# The update helper of 2.32.9–2.32.12 (timer running "update.sh --watch") is no longer needed: remove it
# --------------------------------------------------------------------------------------------------------------
helper_gone() {
  [ "$(id -u)" = 0 ] || return 0
  if [ -f "$SYSROOT/etc/systemd/system/$WATCH_UNIT.timer" ]; then
    systemctl disable --now "$WATCH_UNIT.timer" >/dev/null 2>&1 || true
    rm -f "$SYSROOT/etc/systemd/system/$WATCH_UNIT.timer" "$SYSROOT/etc/systemd/system/$WATCH_UNIT.service"
    systemctl daemon-reload >/dev/null 2>&1 || true
    say "  Removed the update helper of 2.32.9–2.32.12 (systemd timer $WATCH_UNIT) – no longer needed."
  fi
  if [ -f "$SYSROOT/etc/cron.d/$WATCH_UNIT" ]; then
    rm -f "$SYSROOT/etc/cron.d/$WATCH_UNIT"
    say "  Removed the update helper of 2.32.9–2.32.12 (/etc/cron.d/$WATCH_UNIT) – no longer needed."
  fi
  rm -rf "$SCRIPTDIR/.pk-watch.lock" 2>/dev/null || true
  return 0
}
if [ "$ARG" = "--watch-gone" ]; then
  helper_gone
  exit 0
fi

# --------------------------------------------------------------------------------------------------------------
# Update
# --------------------------------------------------------------------------------------------------------------
update() {
  INST="$SCRIPTDIR"
  cd "$INST"
  [ -f docker-compose.yml ] || die "No docker-compose.yml in $INST – put update.sh next to docker-compose.yml and .env (installation: see README)."
  [ -f .env ] || die "No .env in $INST – copy .env.example to .env and fill it in (see README)."
  need_docker
  RUN=$(running_dir)
  if [ -n "$RUN" ] && [ -d "$RUN" ] && [ "$(canon "$RUN")" != "$(canon "$INST")" ]; then
    die "POCKET KNIFE runs from $RUN, not from $INST. Run update.sh there (or stop it there first: docker compose down)."
  fi
  REPO=$(image_repo)
  GREPO="${REPO}-guacd"
  CUR=$(envval .env PK_IMAGE)
  case "$CUR" in pocketknife:*|pocketknife) CUR="" ;; esac   # locally built up to 2.32 → from now on the registry
  OFFLINE=""
  case "$ARG" in
    "") WANT=${CUR:-$REPO:latest}; WANTG=$(envval .env PK_GUACD_IMAGE)
        if [ -z "$WANTG" ]; then case "$WANT" in *:latest|*@*) WANTG="$GREPO:latest" ;; *) WANTG="$GREPO:${WANT##*:}" ;; esac; fi ;;
    latest) WANT="$REPO:latest"; WANTG="$GREPO:latest" ;;
    *.tar.gz|*.tar|*.tgz) OFFLINE=$(resolve "$CALLDIR" "$ARG")
        [ -f "$OFFLINE" ] || [ ! -f "$INST/$ARG" ] || OFFLINE="$INST/$ARG" ;;   # also next to update.sh
    *) printf '%s' "$ARG" | grep -Eq '^[0-9]+(\.[0-9]+){1,3}(-s[0-9]+)?$' || die "Unknown version or file: \"$ARG\" (e.g. 2.33.1, latest or pocketknife-2.33.1-amd64.tar.gz)"
       WANT="$REPO:$ARG"; WANTG="$GREPO:$ARG" ;;
  esac

  OLDVER=$(docker inspect pocketknife --format '{{ index .Config.Labels "org.opencontainers.image.version" }}' 2>/dev/null | tr -d ' \r\n' || true)
  info "POCKET KNIFE in $INST"
  say "  Running:  ${OLDVER:-?}"

  # Offline: load the archive from the release (both images), then use exactly these
  if [ -n "$OFFLINE" ]; then
    [ -f "$OFFLINE" ] || die "File not found: $OFFLINE"
    SUMS="$(dirname "$OFFLINE")/$(basename "$OFFLINE" | sed -e 's/-amd64\.t.*$//' -e 's/-arm64\.t.*$//').sha256"
    # 2.36.2: the checksum is required (before, a missing .sha256 silently skipped the check)
    [ -f "$SUMS" ] || die "$(basename "$SUMS") is missing next to $(basename "$OFFLINE") – copy it from the same release into the same folder. Nothing was changed."
    command -v sha256sum >/dev/null 2>&1 || die "sha256sum was not found – the checksum cannot be checked. Nothing was changed."
    ( cd "$(dirname "$OFFLINE")" && grep -F " $(basename "$OFFLINE")" "$(basename "$SUMS")" | sha256sum -c - >/dev/null 2>&1 ) \
      || die "Checksum of $(basename "$OFFLINE") does not match $(basename "$SUMS") – damaged or altered download. Nothing was changed."
    say "  Checksum OK ($(basename "$SUMS"))."
    info "Loading $(basename "$OFFLINE")"
    LOADED=$(docker load -i "$OFFLINE" | sed -n 's/^Loaded image: //p')
    WANT=$(printf '%s\n' "$LOADED" | grep -v -- '-guacd:' | head -n 1)
    WANTG=$(printf '%s\n' "$LOADED" | grep -- '-guacd:' | head -n 1)
    [ -n "$WANT" ] && [ -n "$WANTG" ] || die "The archive does not contain the POCKET KNIFE and guacd images."
  fi
  say "  New:      $WANT"
  say "            $WANTG"
  echo
  ask "Update now?" || { say "Cancelled – nothing changed."; exit 0; }

  # Remember what runs now – going back needs no download
  HAVEPREV=""
  PID=$(docker inspect pocketknife --format '{{.Image}}' 2>/dev/null || true)
  GID=$(docker inspect pocketknife-guacd --format '{{.Image}}' 2>/dev/null || true)
  if [ -n "$PID" ] && docker tag "$PID" "$PREV_IMAGE" 2>/dev/null; then HAVEPREV=1; fi
  [ -n "$GID" ] && docker tag "$GID" "$PREV_GUACD" 2>/dev/null || true

  if [ -z "$OFFLINE" ]; then
    info "Pulling the images"
    for img in "$WANT" "$WANTG"; do
      if ! docker pull "$img"; then
        say ""
        say "$img could not be pulled. Typical causes:"
        say "  - not logged in to the (private) registry – once, as the user running this update:"
        say "      docker login ${REPO%%/*} -u <user>     (password: token with the right read:packages)"
        say "  - version does not exist, or no internet access (then: the archive pocketknife-<version>-<platform>.tar.gz"
        say "    from the release:  sudo sh update.sh pocketknife-<version>-amd64.tar.gz)"
        exit 1
      fi
    done
  fi
  NEWVER=$(image_version "$WANT")
  if [ -n "$OLDVER" ] && [ -n "$NEWVER" ] && [ "$OLDVER" != "$NEWVER" ] && ver_lt "$NEWVER" "$OLDVER"; then
    say "  CAUTION: $NEWVER is OLDER than the running version $OLDVER. If it shows errors afterwards: restore the"
    say "           backup \"vor-$OLDVER\" (README: backup) – the newer version may already have extended the database."
    ask "Go back to $NEWVER anyway?" || { say "Cancelled – nothing changed."; exit 0; }
  fi

  # The .env names exactly these images (container managers and "docker compose up" use the same ones)
  # 2.36.3: the copy holds passwords – never readable by others, not even briefly (remove first, create with umask 077)
  ( umask 077; rm -f .env.pk-alt; cp .env .env.pk-alt ) && chmod 600 .env.pk-alt
  set_img() {  # key value default – the default (…:latest) is not written into the .env
    if [ "$2" = "$3" ]; then
      if [ -n "$(envval .env "$1")" ]; then env_set .env "$1" "$2"; fi
    elif [ "$(envval .env "$1")" != "$2" ]; then env_set .env "$1" "$2"
    fi
  }
  set_img PK_IMAGE "$WANT" "$DEFAULT_REPO:latest"
  set_img PK_GUACD_IMAGE "$WANTG" "$DEFAULT_REPO-guacd:latest"
  chmod 600 .env 2>/dev/null || true

  # A newer docker-compose.yml (and update.sh) from the image – the only files of the program that belong on the host
  NEWCOMPOSE=""
  T=$(mktemp -d "$INST/.pk-files.XXXXXX") || die "Cannot create a temporary folder in $INST."
  trap 'rm -rf "$T"' EXIT
  CID=$(docker create "$WANT" true 2>/dev/null || true)
  if [ -n "$CID" ] && docker cp "$CID:$COMPOSE_IN_IMAGE/." "$T/" >/dev/null 2>&1; then
    if [ -f "$T/docker-compose.yml" ] && [ "$(compose_ver "$T/docker-compose.yml")" -gt "$(compose_ver docker-compose.yml)" ]; then
      cp docker-compose.yml docker-compose.yml.alt
      cp "$T/docker-compose.yml" docker-compose.yml.pk-neu && mv docker-compose.yml.pk-neu docker-compose.yml
      NEWCOMPOSE=1
      say "  New docker-compose.yml (state $(compose_ver docker-compose.yml)) – the previous one is docker-compose.yml.alt."
      [ -f docker-compose.override.yml ] && say "  Your docker-compose.override.yml stays as it is – please check that it still fits."
    fi
    [ -f "$T/.env.example" ] && cp "$T/.env.example" .env.example 2>/dev/null || true
  fi
  [ -n "$CID" ] && docker rm "$CID" >/dev/null 2>&1 || true

  info "Starting"
  UPOK=1
  docker compose up -d --remove-orphans || UPOK=""
  [ -n "$UPOK" ] && printf 'Waiting for the start '
  if [ -z "$UPOK" ] || ! wait_healthy; then
    echo
    say "The new version does not report as healthy. Last log lines:"
    docker compose logs --tail 30 pocketknife || true
    if [ -n "$HAVEPREV" ] && ask "Start the previous version ${OLDVER:-} again?"; then
      cat .env.pk-alt > .env && rm -f .env.pk-alt   # 2.36.2: same file (owner, rights, link) as before
      [ -n "$NEWCOMPOSE" ] && cp docker-compose.yml.alt docker-compose.yml
      # exactly the images that ran before, under the names the .env expects
      P=$(envval .env PK_IMAGE); P=${P:-$DEFAULT_REPO:latest}
      G=$(envval .env PK_GUACD_IMAGE); G=${G:-$DEFAULT_REPO-guacd:latest}
      case "$P" in *@*) ;; *) docker tag "$PREV_IMAGE" "$P" ;; esac
      case "$G" in *@*) ;; *) docker image inspect "$PREV_GUACD" >/dev/null 2>&1 && docker tag "$PREV_GUACD" "$G" || true ;; esac
      if ! docker compose up -d; then die "The previous version does not start either – see: docker compose logs --tail 50"; fi
      say "Previous version ${OLDVER:-} is running again."
      say "Careful: a container manager or the next update pulls the new version again. To stay on ${OLDVER:-it}:"
      say "  sudo sh update.sh ${OLDVER:-<version>}"
      say "If the new version already extended the database and the old one shows errors: restore the backup"
      say "\"vor-${NEWVER:-<new version>}\" (README: backup)."
    fi
    exit 1
  fi
  rm -f .env.pk-alt
  helper_gone
  info "Done: POCKET KNIFE ${NEWVER:-} is running."
  if [ -n "$(find "$INST" -maxdepth 1 \( -name app -o -name Dockerfile -o -name '.pk-vorher' -o -name 'pocketknife-*.zip' \) -print 2>/dev/null | head -n 1)" ]; then
    say "Tip: this folder still contains files of older versions that are no longer needed:  sudo sh update.sh --cleanup"
  fi
  # update.sh itself last (new file via mv – the running script stays untouched)
  if [ -f "$T/update.sh" ] && ! cmp -s "$T/update.sh" "$INST/update.sh"; then
    cp "$T/update.sh" "$INST/update.sh.pk-neu" && chmod +x "$INST/update.sh.pk-neu" && mv "$INST/update.sh.pk-neu" "$INST/update.sh"
  fi
}

# --------------------------------------------------------------------------------------------------------------
# sudo sh update.sh --cleanup: what older versions (up to 2.32) left in this folder
# --------------------------------------------------------------------------------------------------------------
# Known files of the program from the time it was unpacked on the host – only these are ever deleted here
OLD_ITEMS="app tools docker Dockerfile VERSION CHANGELOG.md README.md README.en.md run.py healthcheck.py \
requirements.txt requirements.lock requirements.caps build-requirements.txt .dockerignore .gitignore .gitattributes \
.github .trivyignore .pk-vorher .pk-vorher.alt .pk-update-unvollstaendig .pk-rotiert-gerade .pk-rollback-ref .pk-watch.lock dist"
cleanup() {
  INST="$SCRIPTDIR"
  cd "$INST"
  [ -f docker-compose.yml ] && [ -f .env ] || die "No docker-compose.yml/.env in $INST – nothing is cleaned up here."
  T=$(mktemp -d) || die "Cannot create a temporary folder."
  trap 'rm -rf "$T"' EXIT
  # Protected: data, backups, repository (from the .env incl. defaults) and everything mounted into the containers
  D=$(envval .env PK_DATA_PATH); D=$(resolve "$INST" "${D:-./data}")
  B=$(envval .env PK_BACKUP_PATH); B=$(resolve "$INST" "${B:-$D-backup}")
  R=$(envval .env PK_REPO_MOUNT); R=$(resolve "$INST" "${R:-./repo}")
  # (2.33.2: every loop body ends with a command that succeeds – under "set -e" a loop whose last command failed,
  # e.g. "[ -n "" ] && …" for the empty line docker prints, ended the script without a word)
  { printf '%s\n%s\n%s\n' "$D" "$B" "$R"; if command -v docker >/dev/null 2>&1; then mounts; fi; } | while IFS= read -r p; do
    if [ -n "$p" ]; then canon "$p"; echo; fi
  done > "$T/protect"
  for n in .env docker-compose.yml docker-compose.override.yml docker-compose.yml.alt update.sh .env.example; do canon "$INST/$n"; echo; done >> "$T/protect"
  : > "$T/del"
  add() {  # path – only if it contains nothing protected and is not protected itself
    c=$(canon "$1")
    while IFS= read -r p; do
      [ -n "$p" ] || continue
      if inside "$c" "$p" || [ "$c" = "$p" ]; then say "  kept: $1 (contains $p)"; return 0; fi
    done < "$T/protect"
    printf '%s\n' "$1" >> "$T/del"
  }
  for n in $OLD_ITEMS; do
    { [ -e "$INST/$n" ] || [ -L "$INST/$n" ]; } && add "$INST/$n"
  done
  for z in "$INST"/pocketknife-*.zip "$INST"/pocketknife-*.zip.sha256 "$INST"/.pk-update.* "$INST"/.pk-pull.* "$INST"/.pk-files.* \
           "$INST"/.pk-watch-log.* "$INST"/.pk-watch-result.* "$INST"/update.sh.neu "$INST"/.env.pk-alt "$INST"/.env.pk-zurueck; do
    { [ -e "$z" ] || [ -L "$z" ]; } && add "$z"
  done
  # Version folders of 2.23.3/2.24 (pocketknife-2.24/ …) – never one with data, backups or a database in it
  for d in "$INST"/pocketknife-*/ "$INST"/pocketknife/; do
    [ -d "$d" ] || continue
    d=${d%/}
    [ -f "$d/VERSION" ] && [ -f "$d/docker-compose.yml" ] && [ -d "$d/app" ] || continue
    skip=""
    for sub in data data-backup repo; do
      if [ -d "$d/$sub" ] && [ -n "$(ls -A "$d/$sub" 2>/dev/null)" ]; then skip="$sub/ is not empty"; fi
    done
    if [ -z "$skip" ] && [ -n "$(find "$d" -maxdepth 4 \( -name '*.db' -o -name '*.db-wal' -o -name '*.sqlite' \) -print 2>/dev/null | head -n 1)" ]; then
      skip="contains a database file"
    fi
    if [ -n "$skip" ]; then say "  kept: $d ($skip)"; else add "$d"; fi
  done
  # the folder of the update helper of 2.32.9–2.32.12 in the data folder (request, state, log – nothing else)
  if [ -d "$D/update" ] && [ ! -L "$D/update" ]; then printf '%s\n' "$D/update" >> "$T/del"; fi

  info "Cleanup in $INST"
  if [ -s "$T/del" ]; then
    say "  No longer needed (the program lives in the image; data, backups, .env and docker-compose.yml stay):"
    while IFS= read -r p; do say "    $p"; done < "$T/del"
    if ask "  Delete these?"; then
      while IFS= read -r p; do
        if rm -rf -- "$p"; then say "    deleted: $p"; else say "    could not delete: $p"; fi
      done < "$T/del"
    else
      say "  Nothing deleted."
    fi
  else
    say "  Nothing to clean up."
  fi
  helper_gone
  if command -v docker >/dev/null 2>&1; then
    OLDIMG=$(docker image ls --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | grep -E '^pocketknife:(local|rollback|rollback-run)$' || true)
    if [ -n "$OLDIMG" ]; then
      say "  Old images of locally built versions: $(printf '%s' "$OLDIMG" | tr '\n' ' ')"
      if ask "  Remove them?"; then
        printf '%s\n' "$OLDIMG" | while IFS= read -r i; do
          if docker image rm "$i" >/dev/null 2>&1; then say "    removed: $i"; else say "    still in use: $i"; fi
        done
      fi
    fi
  fi
  say "  Left in $INST: $(ls -A "$INST" | tr '\n' ' ')"
}

if [ -n "$CLEANUP" ]; then
  cleanup
else
  update
fi
exit 0
