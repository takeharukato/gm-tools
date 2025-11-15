#!/bin/sh
set -eu

cd /src

echo "[entrypoint-deb] autogen.sh / configure / update-po / dpkg-buildpackage を実行します"

# 1. autotools & i18n
./autogen.sh
./configure PYTHON=python3

( cd po && make update-po )

# 2. debian/ ディレクトリがある前提で dpkg-buildpackage
if [ ! -d debian ]; then
    echo "ERROR: debian/ ディレクトリがありません。DEB パッケージング設定が必要です。" >&2
    exit 1
fi

echo "[entrypoint-deb] dpkg-buildpackage -us -uc -b を実行します"
dpkg-buildpackage -us -uc -b

# 3. 出来上がった .deb を /dist にコピー
echo "[entrypoint-deb] /dist に .deb をコピーします"
mkdir -p /dist
# gm-tools_* を想定
find .. -maxdepth 1 -type f -name 'gm-tools_*.deb' -print -exec cp -v {} /dist/ \;

echo "[entrypoint-deb] 完了しました"
