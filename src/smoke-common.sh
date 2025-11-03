#!/usr/bin/env bash
set -euo pipefail

# === 可変項目（環境変数で上書き可） ===
: "${PY:=python3}"
: "${MOD:=gm_tools.scatter_cli}"
: "${HOSTFILE:=hostfile}"
: "${DEST:=/tmp/gm-scatter-dest}"
: "${SRC_BASE:=/tmp/gmtest}"

# === 共通ユーティリティ ===
log() { printf '%s\n' "[+] $*"; }
warn() { printf '%s\n' "[!] $*" >&2; }
die() { printf '%s\n' "[x] $*" >&2; exit 1; }

ensure_hostfile() {
  if [[ ! -f "${HOSTFILE}" ]]; then
    log "HOSTFILE '${HOSTFILE}' が無いので localhost を書き出します"
    echo "localhost" > "${HOSTFILE}"
  fi
}

prepare_src_tree() {
  log "準備: ソースツリー ${SRC_BASE} を再作成"
  rm -rf "${SRC_BASE}"
  mkdir -p "${SRC_BASE}/sub"
  echo "hello" > "${SRC_BASE}/a.txt"
  echo "world" > "${SRC_BASE}/sub/b.txt"
  ln -sf "${SRC_BASE}/a.txt" "${SRC_BASE}/a.link"   # symlink
}

clean_dest() {
  log "準備: 宛先 ${DEST} を掃除"
  rm -rf "${DEST}"
  mkdir -p "${DEST}"
}

run_scatter() {
  # 引数はそのまま scatter_cli へ
  log "実行: ${PY} -m ${MOD} $*"
  ${PY} -m ${MOD} "$@"
}

must_exist() {
  local p="$1"
  [[ -e "${p}" ]] || die "存在しません: ${p}"
}

must_not_exist() {
  local p="$1"
  [[ ! -e "${p}" ]] || die "存在してはなりません: ${p}"
}

show_dest_tree() {
  log "結果: 宛先ツリー"
  ( set +e; ls -lR "${DEST}" || true )
}

# 絶対パス => DEST への変換ヘルパ（期待確認用）
to_dest_path() {
  # /tmp/gmtest/sub/b.txt -> ${DEST}/tmp/gmtest/sub/b.txt
  local ap="$1"
  local rel="${ap#/}"  # 先頭スラッシュ除去
  printf '%s\n' "${DEST}/${rel}"
}
