# root しか読めないファイル
ssh localhost 'sudo sh -lc "echo secret >/root/secret.txt; chmod 600 /root/secret.txt"'

# C-1 通常ユーザ収集（-u tkato -s tkato）+ --pack → 読めずにエラーへ
python3 -m gm_tools.gather_cli -H hostfile --pack \
  '/root/secret\.txt$' /tmp/gather-test || true

# C-2 ssh_user=tkato, user=root + --pack → sudo で取得できること
rm -rf /tmp/gather-test
python3 -m gm_tools.gather_cli -H hostfile -s tkato -u root --pack \
  '/root/secret\.txt$' /tmp/gather-test
