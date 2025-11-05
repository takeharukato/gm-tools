set -euo pipefail

# 準備
ssh localhost 'rm -rf /tmp/gmtest && mkdir -p /tmp/gmtest/sub &&
               echo conf >/tmp/gmtest/a.conf &&
               echo log  >/tmp/gmtest/sub/b.log &&
               ln -s /tmp/gmtest/a.conf /tmp/gmtest/link.conf'

# B-1 ssh_user != user で --pack なし  =>  仕様的にエラー ( ホスト数ぶん )
python3 -m gm_tools.gather_cli -H hostfile -u root -s tkato \
  '/tmp/gmtest/.*' /tmp/gather-test || true

# B-2 ssh_user != user で --pack あり  =>  成功 ( sudo -n 必須 )
rm -rf /tmp/gather-test
python3 -m gm_tools.gather_cli -H hostfile -u root -s tkato --pack \
  '/tmp/gmtest/.*' /tmp/gather-test
tree /tmp/gather-test
