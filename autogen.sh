#!/usr/bin/env bash
set -euo pipefail

# autogen.sh が置かれているディレクトリ (ソースツリーのトップ)へ移動
srcdir=$(
  CDPATH= cd -- "$(dirname "$0")" && pwd
)
cd "$srcdir"

if [ ! -f configure.ac ]; then
  echo "autogen.sh: error: configure.ac not found in ${srcdir}" >&2
  exit 1
fi

echo "==> Running autoreconf to generate configure and related files..."
autoreconf --install --force

echo "==> Done. Now run ./configure (with any options you need), then make."
