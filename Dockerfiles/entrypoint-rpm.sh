#!/bin/sh
set -eu


WORKDIR=/tmp/gm-tools-build
mkdir -p "${WORKDIR}"

# /src に gm-tools ソースがマウントされている前提
cp -a /src/. "${WORKDIR}/"
cd "${WORKDIR}"

echo "[entrypoint-rpm] autogen.sh / configure / update-po / dist / rpmbuild を実行します"

# 1. autotools と i18n 更新
./autogen.sh
./configure

# po カタログ更新
( cd po && make update-po )

# 2. 配布用ソースアーカイブ生成 (gm-tools-<version>.tar.gz)
make dist

TARBALL="$(ls -t gm-tools-*.tar.gz | head -n 1 || true)"

if [ -z "$TARBALL" ] || [ ! -f "$TARBALL" ]; then
    echo "ERROR: make dist で gm-tools-*.tar.gz が生成されていません" >&2
    exit 1
fi

echo "[entrypoint-rpm] TARBALL = $TARBALL"

# 3. rpmbuild 用ディレクトリ
TOPDIR=/tmp/rpmbuild
mkdir -p "$TOPDIR"/{BUILD,RPMS,SOURCES,SPECS,SRPMS}

# 4. rpmbuild -ta で RPM ビルド
echo "[entrypoint-rpm] rpmbuild -ta を実行します"
rpmbuild -ta "$TARBALL" \
  --define "_topdir $TOPDIR"

# 5. 生成された .rpm を /dist にコピー
echo "[entrypoint-rpm] /dist へ .rpm をコピーします"
mkdir -p /dist
find "$TOPDIR/RPMS" -type f -name 'gm-tools-*.rpm' -print -exec cp -v {} /dist/ \;

echo "[entrypoint-rpm] 完了しました"
