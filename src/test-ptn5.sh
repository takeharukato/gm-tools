set -euo pipefail

# 準備
ssh localhost 'rm -rf /tmp/gmtest && mkdir -p /tmp/gmtest/sub &&
               echo conf >/tmp/gmtest/a.conf &&
               echo log  >/tmp/gmtest/sub/b.log &&
               ln -s /tmp/gmtest/a.conf /tmp/gmtest/link.conf'

# 1-1 空 tail 相当：末尾スラッシュのみ ( 全配下 )
python3 -m gm_tools.gather_cli -H hostfile --dry-run '/tmp/gmtest/' /tmp/gather-test

# 1-2 複数 SRC マージ
python3 -m gm_tools.gather_cli -H hostfile --dry-run \
  '/tmp/gmtest/.*\.log$' '/tmp/gmtest/a\.conf$' /tmp/gather-test

# 1-3 missing root を混ぜる ( dropped を観察 )
python3 -m gm_tools.gather_cli -H hostfile --dry-run \
  '/no/such/dir/.*' '/tmp/gmtest/.*' /tmp/gather-test

# 1-4 SFTP で symlink 除外
rm -rf /tmp/gather-test
python3 -m gm_tools.gather_cli -H hostfile '/tmp/gmtest/(a\.conf|link\.conf)$' /tmp/gather-test
tree /tmp/gather-test

# 1-5 ~ 展開 ( ユーザー名は環境に合わせて )
ssh localhost 'mkdir -p $HOME/gmtest && echo home > $HOME/gmtest/x.txt'
python3 -m gm_tools.gather_cli -H hostfile --user "$USER" --dry-run \
  '~/gmtest/.*\.txt$' /tmp/gather-test

# 1-6 Windows root ( Linux 実行時は非ヒット想定 )
python3 -m gm_tools.gather_cli -H hostfile --dry-run \
  'C:/Windows/.*' /tmp/gather-test
