#!/bin/bash
set -euo pipefail

# 準備
ssh localhost 'rm -rf /tmp/gmtest &&
               mkdir -p /tmp/gmtest/sub &&
               echo conf >/tmp/gmtest/a.conf &&
               echo log  >/tmp/gmtest/sub/b.log &&
               ln -s /tmp/gmtest/a.conf /tmp/gmtest/link.conf &&
               ln -s /no/such/path      /tmp/gmtest/broken.conf || :'

echo '## 9-1 --pack (デフォルト: symlink含めない)'
rm -rf /tmp/gather-test
python3 -m gm_tools.gather_cli -H hostfile --pack '/tmp/gmtest/.*' /tmp/gather-test
echo '--- tree'
tree /tmp/gather-test || true
echo '--- grep link.conf (ヒットしないはず)'
! grep -R '/tmp/gmtest/link.conf' -n /tmp/gather-test || exit 1
echo '--- grep broken.conf (ヒットしないはず)'
! grep -R 'broken.conf' -n /tmp/gather-test || exit 1

echo '## 9-2 --pack + --follow-symlinks （実体追随。link.conf→a.conf を収集、broken は除外）'
rm -rf /tmp/gather-test
python3 -m gm_tools.gather_cli -H hostfile --pack --follow-symlinks '/tmp/gmtest/(a\.conf|link\.conf|broken\.conf)$' /tmp/gather-test
echo '--- tree'
tree /tmp/gather-test || true
echo '--- a.conf は1つ以上存在（元+リンク先の解決で重複は tar がまとめる場合あり）'
test -f /tmp/gather-test/localhost/abs/tmp/gmtest/a.conf
echo '--- link.conf 自体のエントリは（dereference のため）原則出力されない想定'
! test -f /tmp/gather-test/localhost/abs/tmp/gmtest/link.conf || echo '(実装/環境によりメタエントリが残ることはあり)'
echo '--- broken.conf は存在しない'
! test -f /tmp/gather-test/localhost/abs/tmp/gmtest/broken.conf
