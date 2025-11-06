#!/usr/bin/env bash
set -Eeuo pipefail
_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "${_here}/.." && pwd)"
if [[ -f "${root}/tests/env.sh" ]]; then
  source "${root}/tests/env.sh"
else
  source "${root}/tests/env.sample.sh"
fi

log() { printf '[%s] %s\n' "$(date +'%F %T')" "$*"; }
die() { log "ERROR: $*"; exit 2; }
req() { command -v "$1" >/dev/null 2>&1 || die "missing command: $1"; }
clean_dir(){ rm -rf "$1"; mkdir -p "$1"; }
ensure_remote_dir(){ ssh -p "${SSH_PORT}" -o BatchMode=yes "${SSH_USER}@$1" "mkdir -p '$2'"; }
wipe_remote_dir(){ ssh -p "${SSH_PORT}" -o BatchMode=yes "${SSH_USER}@$1" "rm -rf '$2'"; }
expect_exit(){ [[ "$2" -eq "$1" ]] || die "unexpected exit ($3): expected=$1 got=$2"; }
must_exist(){ [[ -e "$1" ]] || die "missing path: $1"; }
