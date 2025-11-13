#!/bin/sh
set -eu

# /src に gm-tools ソースがマウントされている前提
cd /src

# autogen → configure → make dist (ソースアーカイブ生成)
./autogen.sh
./configure
make dist

# 生成された tarball を使って rpmbuild を実行する
# 例: rpmbuild -ta gm-tools-0.1.0.tar.xz
#
# 出来上がった .rpm を /dist にコピーする
