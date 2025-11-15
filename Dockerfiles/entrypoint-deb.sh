#!/bin/sh
set -eu

WORKDIR=/tmp/gm-tools-build
mkdir -p "${WORKDIR}"

cp -a /src/. "${WORKDIR}/"
cd "${WORKDIR}"

echo "[entrypoint-deb] dpkg-buildpackage -us -uc -b を実行します"

dpkg-buildpackage -us -uc -b

echo "[entrypoint-deb] /dist に .deb をコピーします"
mkdir -p /dist
find .. -maxdepth 1 -type f -name 'gm-tools_*.deb' -print -exec cp -v {} /dist/ \;

echo "[entrypoint-deb] 完了しました"
