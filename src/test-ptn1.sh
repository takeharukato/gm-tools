# テスト用ファイルクリア
ssh localhost "rm -fr /tmp/gmtest"
# テスト用ファイル作成
ssh localhost "mkdir -p /tmp/gmtest/sub && echo conf > /tmp/gmtest/a.conf && echo log > /tmp/gmtest/sub/b.log"
# リンク作成
ssh localhost "ln -sf /tmp/gmtest/a.conf /tmp/gmtest/link.conf"
# リモートファイル表示
ssh localhost "tree /tmp/gmtest"

# 絶対パターン1
python3 -m gm_tools.gather_cli -H hostfile -K "$HOME/.ssh/private-id_ed25519"  --verbose --dry-run '/tmp/gmtest/sub/.+\.log$' /tmp/gather-test

# 絶対パターン2 シンボリックリンク無視
python3 -m gm_tools.gather_cli -H hostfile -K "$HOME/.ssh/private-id_ed25519"    --verbose --dry-run '/tmp/gmtest/(a\.conf|link\.conf)$' /tmp/gather-test

# 不在ファイルパターン
python3 -m gm_tools.gather_cli -H hostfile -K "$HOME/.ssh/private-id_ed25519"   /no/such/file /tmp/gather-test --verbose

tree /tmp/gather-test
