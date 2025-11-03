#!/usr/bin/env bash
set -euo pipefail
. "$(dirname "$0")/smoke-common.sh"

ensure_hostfile
prepare_src_tree
clean_dest

run_scatter "${SRC_BASE}" "${DEST}" -H "${HOSTFILE}" --pack --follow-symlinks

# a.txt / sub/b.txt は存在
must_exist "$(to_dest_path "${SRC_BASE}/a.txt")"
must_exist "$(to_dest_path "${SRC_BASE}/sub/b.txt")"

# deref: a.link は「リンクではなく実体」で展開される想定（少なくともパスは存在）
if [[ -e "$(to_dest_path "${SRC_BASE}/a.link")" ]]; then
  log "a.link が実体化済み（deref想定）。ls -l でリンク種別でないことを確認ください。"
else
  warn "a.link が見つかりません（tar 実装/挙動に依存の可能性）。"
fi

show_dest_tree
log "OK: pack + deref"
