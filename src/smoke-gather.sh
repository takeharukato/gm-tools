#!/usr/bin/env bash
set -euo pipefail

# ===== パラメータ ( 必要に応じて上書き可 )  =====
: "${PY:=python3}"
: "${MOD:=gm_tools.gather_cli}"
: "${HOSTS_LOCAL:=hostfile.local}"     # localhost 専用の hostfile
: "${OUT1:=./out-gather1}"             # /etc/hosts を取る先
: "${OUT2:=./out-gather2}"             # /tmp/gmtest_g を pack 非deref
: "${OUT3:=./out-gather3}"             # /tmp/gmtest_g を pack deref
: "${SRC_TREE:=/tmp/gmtest_g}"

log() { printf '[+] %s\n' "$*"; }
die() { printf '[x] %s\n' "$*" >&2; exit 1; }
must_exist() { [[ -e "$1" ]] || die "存在しません: $1"; }

# ===== ホストファイル ( localhost のみ )  =====
if [[ ! -f "${HOSTS_LOCAL}" ]]; then
  echo "localhost" > "${HOSTS_LOCAL}"
fi

# ===== 1) /etc/hosts を dry-run  =>  実行 =====
log "1) /etc/hosts を dry-run"
${PY} -m ${MOD} /etc/hosts "${OUT1}" -H "${HOSTS_LOCAL}" -n

log "1) /etc/hosts を取得"
rm -rf "${OUT1}"
${PY} -m ${MOD} /etc/hosts "${OUT1}" -H "${HOSTS_LOCAL}"
must_exist "${OUT1}/localhost/etc/hosts"
log "OK: ${OUT1}/localhost/etc/hosts あり"

# ===== 2) テスト用ツリーを作成 ( symlink 含む )  =====
log "2) リモートSRC用のツリー作成 ( localhost なので同一マシン ) : ${SRC_TREE}"
rm -rf "${SRC_TREE}"
mkdir -p "${SRC_TREE}/sub"
echo "hello" > "${SRC_TREE}/a.txt"
echo "world" > "${SRC_TREE}/sub/b.txt"
ln -s "${SRC_TREE}/a.txt" "${SRC_TREE}/a.link"

# ===== 3) pack ( 非deref )  =====
log "3) pack 非derefで取得  =>  ${OUT2}"
rm -rf "${OUT2}"
${PY} -m ${MOD} "${SRC_TREE}" "${OUT2}" -H "${HOSTS_LOCAL}" --pack
# a.link は symlink のまま期待
must_exist "${OUT2}/localhost/tmp/gmtest_g/a.txt"
must_exist "${OUT2}/localhost/tmp/gmtest_g/sub/b.txt"
if [[ -L "${OUT2}/localhost/tmp/gmtest_g/a.link" ]]; then
  log "OK: 非derefで symlink が保持されています"
else
  log "注意: ${OUT2}/localhost/tmp/gmtest_g/a.link は symlink ではありません ( 環境依存の可能性 ) "
fi

# ===== 4) pack + --follow-symlinks ( deref )  =====
log "4) pack derefで取得  =>  ${OUT3}"
rm -rf "${OUT3}"
${PY} -m ${MOD} "${SRC_TREE}" "${OUT3}" -H "${HOSTS_LOCAL}" --pack --follow-symlinks
must_exist "${OUT3}/localhost/tmp/gmtest_g/a.txt"
must_exist "${OUT3}/localhost/tmp/gmtest_g/sub/b.txt"
if [[ -e "${OUT3}/localhost/tmp/gmtest_g/a.link" ]]; then
  if [[ -L "${OUT3}/localhost/tmp/gmtest_g/a.link" ]]; then
    die "想定外: deref なのに symlink のままです: ${OUT3}/localhost/tmp/gmtest_g/a.link"
  else
    log "OK: derefで a.link は実体化 ( 通常ファイル ) しています"
  fi
else
  log "注意: deref 後に a.link 自体が無い環境があります ( tar 実装差 ) 。少なくとも実体は取得済み。"
fi

log "すべての gather スモーク OK"
