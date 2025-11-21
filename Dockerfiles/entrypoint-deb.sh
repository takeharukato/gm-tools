#!/bin/sh
set -eu

WORKDIR=/tmp/gm-tools-build
mkdir -p "${WORKDIR}"

cp -a /src/. "${WORKDIR}/"
cd "${WORKDIR}"

# Autotools 生成物をコンテナ内のパスで作り直し,
# dh_auto_clean の distclean がホストパスに触れないようにする。
echo "[entrypoint-deb] ./autogen.sh / ./configure を事前実行します"
./autogen.sh
./configure PYTHON=python3 --prefix=/usr

# distclean を先に流しておき, 後段の dpkg-buildpackage が同じ処理を行っても
# コンテナ内で完結するようにする。
echo "[entrypoint-deb] make distclean を先行実行します"
make distclean || true

echo "[entrypoint-deb] dpkg-buildpackage -us -uc -b を実行します"

dpkg-buildpackage -us -uc -b

echo "[entrypoint-deb] /dist に .deb をコピーします"
mkdir -p /dist
find .. -maxdepth 1 -type f -name 'gm-tools_*.deb' -print -exec cp -v {} /dist/ \;

echo "[entrypoint-deb] 完了しました"
