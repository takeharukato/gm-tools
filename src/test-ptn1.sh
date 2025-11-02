# テスト用ファイルクリア
ssh localhost "rm -fr /tmp/gmtest/sub"
# テスト用ファイル作成
ssh localhost "mkdir -p /tmp/gmtest/sub && echo conf > /tmp/gmtest/a.conf && echo log > /tmp/gmtest/sub/b.log"
# a) -R + -r（相対パターン）
python3 -m gm_tools.gather_cli -H hostfile -K "$HOME/.ssh/private-id_ed25519"   -R /tmp/gmtest -r '^\w+\.conf$' --verbose --dry-run /tmp/gather-test
# b) -a（絶対パターン）
python3 -m gm_tools.gather_cli -H hostfile -K "$HOME/.ssh/private-id_ed25519"   -a '^/tmp/gmtest/.+\.log$' --verbose --dry-run /tmp/gather-test
# c) 実ダウンロードでまとめ取得
python3 -m gm_tools.gather_cli -H hostfile -K "$HOME/.ssh/private-id_ed25519"   -R /tmp/gmtest -r '^\w+\.conf$' -a '^/tmp/gmtest/.+\.log$'   --verbose /tmp/gather-test
ssh localhost "ln -sf /tmp/gmtest/a.conf /tmp/gmtest/link.conf"
python3 -m gm_tools.gather_cli -H hostfile -K "$HOME/.ssh/private-id_ed25519"   -R /tmp/gmtest -r '^(a\.conf|link\.conf|sub/b\.log)$'   --verbose --dry-run /tmp/gather-test
python3 -m gm_tools.gather_cli -H hostfile -K "$HOME/.ssh/private-id_ed25519"   /no/such/file /tmp/gather-test --verbose
