# gm_tools スモークテスト (ステップ4 & ステップ5)

このスイートは、`gm-gather` および `gm-scatter` に対する **回帰テスト (ステップ4)** および **並列転送 (ステップ5)** スモークテストを提供します。
2台のテストホストと2人のユーザーを想定しています:

- **ユーザー**
- `ansible` (SSH公開鍵ログイン、パスワード不要の`sudo`)
- `root`
- **ホスト**
- `localhost` (SELinux無効)
- `vmlinux` (SELinux Permissive)

> 注記: 設計上、これらのスクリプトは**`.ssh/config`を参照しません**。SSHオプションは明示的に渡されます。

## カバレッジマップ

### ステップ4 (権限/SELinuxパス)

- 収集: SFTP & `--pack`
- 散布: SFTP
- 収集用 `-x/--sudo-collect` (特権ファイル)
- 収集用 `--follow-symlinks` と `--pack` の併用
- **パスの解釈**
- DEST: 絶対パス (`/...`)、`~/...`、相対パス (収集時のみ)
- SRC: 絶対パス (`/...`、`~/...`)
- 散布元 SRC: 相対パス (S-REL-01) → CWD を基準に解決 → `DEST/<先頭スラッシュなしのローカル絶対パス>` にアップロード

### ステップ5 (並列パス)

- 並列ホスト `-j N` 基本成功
- 早期失敗/中止はスモークテストレベルでは対象外（詳細テストで対応）

## レイアウト

- `tests/env.sh` — 必須確認環境変数と定数
- `tests/lib/ssh.sh` — `.ssh/config` を使用しない簡易SSHヘルパー
- `tests/hosts/hosts_both` — `-H` オプション用サンプルホストファイル
- `tests/run_smoke_step4.sh` — Step4 + S-REL-01
- `tests/run_smoke_step5.sh` — Step5

## クイックスタート

```bash
# 0) tests/env.sh を自身のパスとキーに合わせて調整
$ sed -n 『1,160p』 tests/env.sh

# 1) 環境変数をエクスポートし Step4 スイートを実行 (S-REL-01 含む)
$ source tests/env.sh
$ bash tests/run_smoke_step4.sh

# 2) Step5 (並列実行)
$ bash tests/run_smoke_step5.sh
```


## 注意事項
- スクリプトは `tests/_tmp_*` 配下に一時ディレクトリを作成し、選択的にクリーンアップします。
- `set -euo pipefail` を設定し、最初のエラーで失敗します。各セクションのログを確認してください。
- スキャッターの期待パス: `DEST/<先頭スラッシュなしのローカル絶対パス>`
- ギャザーの期待パス: `DEST/<HOST>/...`
- リモートデプロイ先はデフォルトで /tmp/gm_scatter_dest (env.sh で変更可能)
- 生成/検証済み出力は tests/output/ に集約されます

## 補足事項

GM_GATHER_CMD / GM_SCATTER_CMD は既定で gm-gather / gm-scatter。
未導入なら env.sh で

```:shell
export GM_GATHER_CMD="python -m gm_tools.gather_cli"
export GM_SCATTER_CMD="python -m gm_tools.scatter_cli"
```

のように上書きしてください。
