#!/bin/sh
set -eu

cd /src

./autogen.sh
./configure
make dist

# Debian パッケージング:
#  - debian/ ディレクトリを用意しておく場合:
#      dpkg-buildpackage -us -uc -b
#  - あるいは単純に python + setuptools から debhelper を叩く場合 etc.
#
# 出来上がった .deb を /dist にコピーする
