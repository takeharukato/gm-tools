#!/usr/bin/env bash
# gen-pot.sh - create/update POT from src/*.py
# Usage: ./setup-i18n.sh [textdomain]
# Examples:
#   デフォルト ( DOMAIN=gm-tools, SRCDIR=src, OUTDIR=locale )
#   ./gen-pot.sh
#
#   ドメイン名を指定
#   ./gen-pot.sh gm-gather
#
#   環境変数で場所を変更
#   SRCDIR=src PYTHONPATH=src OUTDIR=po ./gen-pot.sh gm-scatter
# Notes:
#   - Requires GNU gettext (xgettext).
#   - Extracts from Python source files only.
set -euo pipefail

# ==== settings ====
DOMAIN="${1:-gm-tools}"                 # textdomain ( 出力ファイル名のベース ) 。引数で上書き可
SRCDIR="${SRCDIR:-src}"                 # 走査対象のディレクトリ
OUTDIR="${OUTDIR:-locale}"              # 出力先ディレクトリ
POT="${OUTDIR}/${DOMAIN}.pot"

# 抽出対象のキーワード ( Pythonのgettext慣習 )
#  - _()
#  - ngettext(singular, plural, n)
#  - pgettext(context, msg)
#  - npgettext(context, singular, plural, n)
KEYS=(
  --keyword=_
  --keyword=ngettext:1,2
  --keyword=pgettext:1c,2
  --keyword=npgettext:1c,2,3
)

# ==== checks ====
command -v xgettext >/dev/null 2>&1 || {
  echo "Error: xgettext not found. Install GNU gettext." >&2
  exit 1
}
test -d "$SRCDIR" || {
  echo "Error: SRCDIR '$SRCDIR' does not exist." >&2
  exit 1
}
mkdir -p "$OUTDIR"

# ==== collect python sources safely (null-delimited) ====
# 除外例：venv, .git, __pycache__ は除外
tmp_list="$(mktemp)"
trap 'rm -f "$tmp_list"' EXIT

find "$SRCDIR" -type f -name '*.py' \
  -not -path '*/.git/*' \
  -not -path '*/__pycache__/*' \
  -not -path '*/venv/*' \
  -print0 |
  xargs -0r printf '%s\n' > "$tmp_list"

if ! test -s "$tmp_list"; then
  echo "No Python files found under '$SRCDIR'." >&2
  # 空でもPOTだけは生成しておく ( ツールチェーンがファイルを期待することがあるため )
  : > "$POT"
  exit 0
fi

# 版情報 ( 任意 )
PKG_NAME="${PKG_NAME:-$DOMAIN}"
PKG_VER="${PKG_VER:-$(git describe --tags --always 2>/dev/null || echo 0.0.0)}"

# ==== run xgettext ====
# --from-code=UTF-8 : UTF-8想定
# --language=Python : Python用パーサ
# --add-location=file : 参照元位置をファイル名だけに ( 行番号はdiffノイズになりやすい )
# -f listfile : 入力ファイル一覧を与える
xgettext \
  --language=Python \
  --from-code=UTF-8 \
  --add-location=file \
  --package-name="$PKG_NAME" \
  --package-version="$PKG_VER" \
  "${KEYS[@]}" \
  -f "$tmp_list" \
  -o "$POT"

echo "Wrote: $POT"
