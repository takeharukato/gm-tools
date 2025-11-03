#!/usr/bin/env bash
set -euo pipefail
. "$(dirname "$0")/smoke-common.sh"

# 例: SSHログインは一般ユーザー（あなたの通常アカウント）
: "${SSH_USER:=${USER:-$(id -un)}}"

# 展開アカウント（所有者概念としての --user）。ssh_user と異なる値にする
: "${TARGET_USER:=targetuser}"

# sudo が必要なディレクトリを宛先に（権限で書けない場所）
DEST=/var/lib/gm-scatter-dest

ensure_hostfile
prepare_src_tree
clean_dest || true  # /var/lib 配下は一般ユーザーでは消せないことがあるので失敗は無視

# 成功ケース：--pack + -x（sudo 展開）
run_scatter "${DEST}" "${SRC_BASE}" \
  -H "${HOSTFILE}" \
  --pack -x \
  -u "${TARGET_USER}" -s "${SSH_USER}"

# 配置確認（sudo 経路で展開済みのはず）
must_exist "$(to_dest_path "${SRC_BASE}/a.txt")"
must_exist "$(to_dest_path "${SRC_BASE}/sub/b.txt")"
show_dest_tree
log "OK: pack + sudo extract with non-root SSH login (ssh_user='${SSH_USER}', user='${TARGET_USER}', dest='${DEST}')"
