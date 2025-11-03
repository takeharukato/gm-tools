#!/usr/bin/env bash
set -euo pipefail
. "$(dirname "$0")/smoke-common.sh"

ensure_hostfile
prepare_src_tree
clean_dest

run_scatter "${SRC_BASE}" "${DEST}" -H "${HOSTFILE}"

# a.txt と sub/b.txt は存在
must_exist "$(to_dest_path "${SRC_BASE}/a.txt")"
must_exist "$(to_dest_path "${SRC_BASE}/sub/b.txt")"
# symlink は送らない
must_not_exist "$(to_dest_path "${SRC_BASE}/a.link")"

show_dest_tree
log "OK: sftp (symlink dropped)"
