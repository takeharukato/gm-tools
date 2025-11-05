ssh localhost  'rm -rf /tmp/gmtest && mkdir -p /tmp/gmtest && echo A >/tmp/gmtest/a.conf'
ssh devserver  'rm -rf /tmp/gmtest && mkdir -p /tmp/gmtest && rm -f /tmp/gmtest/a.conf || :'

python3 -m gm_tools.gather_cli -H hostfile '/tmp/gmtest/a\.conf$' /tmp/gather-test || true
# 期待：localhost は成功, devserver は not found  =>  errors に記録
#  ( Failure matrix を出す実装を入れたら, ここで '/tmp/gmtest/a.conf -> [devserver]' が表示される )
