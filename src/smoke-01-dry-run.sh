#!/usr/bin/env bash
set -euo pipefail
. "$(dirname "$0")/smoke-common.sh"

ensure_hostfile
prepare_src_tree
clean_dest

run_scatter "${SRC_BASE}" "${DEST}" -H "${HOSTFILE}" --dry-run

# dry-run は書き込みなしが前提
must_not_exist "$(to_dest_path "${SRC_BASE}/a.txt")"
must_not_exist "$(to_dest_path "${SRC_BASE}/sub/b.txt")"
show_dest_tree
log "OK: dry-run"
