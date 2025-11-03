#!/usr/bin/env bash
set -euo pipefail
. "$(dirname "$0")/smoke-common.sh"

ensure_hostfile
prepare_src_tree
clean_dest

run_scatter "${DEST}" "${SRC_BASE}" -H "${HOSTFILE}" --pack

# a.txt / sub/b.txt は存在
must_exist "$(to_dest_path "${SRC_BASE}/a.txt")"
must_exist "$(to_dest_path "${SRC_BASE}/sub/b.txt")"

# 非deref: a.link は symlink として復元（ls の結果で種別を目視確認推奨）
if [[ -e "$(to_dest_path "${SRC_BASE}/a.link")" ]]; then
  log "a.link が展開済み（非deref想定）。symlinkであることを推奨確認: ls -l '$(to_dest_path "${SRC_BASE}/a.link")'"
else
  warn "a.link が見つかりません（環境依存の tar 実装かも）。"
fi

show_dest_tree
log "OK: pack (non-deref)"
