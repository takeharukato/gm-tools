# gm-gather 仕様

本書は `gm-gather` コマンドの実装を権威仕様として記述する。共通仕様や用語は `docs/common-spec.md` を参照のこと。

## 引数とオプション

### SYNOPSIS

```plaintext
gm-gather [OPTIONS] SRC... DEST
```

- `SRC...` : 1 つ以上のリモートパスまたはパターン。
- `DEST`   : ローカル保存先ディレクトリ。必須。

### 位置引数

| 引数 | 説明 |
| ---- | ---- |
| `SRC...` | リモート側の取得元。リテラル/正規表現/チルダ展開の規則は「パスと正規表現仕様」を参照。最低 1 件必要。 |
| `DEST` | ローカル側の出力ディレクトリ。相対指定は起動時のカレントディレクトリで絶対化。`~` や `~user` 形式は無効。 |

### オプション一覧 (man スタイル)

| オプション | 説明 |
| ----------- | ---- |
| `-H`, `--hosts` *FILE* | ホストファイルを指定。既定値 `hostfile` (`core_constants.DEFAULT_HOSTS_FILE`)。空行・先頭 `#`・トレーリングコメントは無視。 |
| `-u`, `--user` *USER* | リモートでのターゲットアカウント。`SRC` の `~/` 展開とパーミッション評価で使用。既定は実行ユーザ。 |
| `-s`, `--ssh-user` *USER* | SSH ログインユーザ。省略時は `--user` と同じ。`--sudo-collect` の自動判定に影響。 |
| `-P`, `--port` *PORT* | SSH ポート番号。既定 `22` (`core_ssh.DEFAULT_SSH_PORT`)。 |
| `-K`, `--key` *PATH* | SSH 秘密鍵ファイルのパス。指定なしの場合は SSH ライブラリの既定に従う。 |
| `-W`, `--password` *PASS* | SSH パスワード。運用上は鍵認証推奨。 |
| `-T`, `--timeout` *SEC* | SSH セッションおよびリモート コマンドのタイムアウト秒。既定 `core_ssh.DEFAULT_TIMEOUT` (30 秒)。 |
| `-S`, `--strict-host-key-checking` | 有効にすると Paramiko の `RejectPolicy` を使用し未知のホスト鍵を拒否。省略時は `AutoAddPolicy`。 |
| `-j`, `--parallel` *N* | ホスト並列数。1 以上でホスト単位の並列実行。既定 `core_constants.DEFAULT_PARALLEL_HOSTS` (4)。 |
| `-n`, `--dry-run` | 取得計画のみ構築し、転送は行わない。ログ集計のみ実施。 |
| `-v`, `--verbose` | 追加の DEBUG ログを有効化。dry-run 時も適用。 |
| `--pack` | ホスト単位でリモートに tar.gz を生成し、1 回のダウンロードで展開する。省略時は SFTP 逐次転送。 |
| `--follow-symlinks` | `--pack` 使用時にリモートのファイルシンボリックリンクを実体化して収集するよう試行。SFTP モードでは候補列挙のみで転送対象にはならない。 |
| `-x`, `--sudo-collect` | `--pack` でのリモート tar/gzip 実行および候補列挙を常に `sudo -n` で実施。 |
| `--no-sudo-collect` | `--pack` 経路でも `sudo` を使用しない。 |

`--sudo-collect` / `--no-sudo-collect` をどちらも指定しない場合、実装は `--ssh-user != --user` のとき自動的に `sudo -n` を有効化し、それ以外では無効とする。

### オプション詳細

- SSH 接続は Paramiko を利用し、`ssh_open`/`finalize_sockets` でライフサイクルを管理する。`--key` と `--password` は Paramiko にそのまま引き渡す。
- `--timeout` は `detect_remote_home`、`remote_pack_paths`、`run_remote_cmd_capture` などすべてのリモート操作で共有する。
- `--pack` 指定時、各ホストにつき 1 度だけ `_make_pull_one_pack` が実行され、残りの PlanEntry は no-op となる。SFTP モードでは `_make_pull_one_sftp` が全ファイルを逐次 `sftp.get` する。
- `--follow-symlinks` は `enumerate_candidates_for_host(... include_symlinks=True)` に作用し、`--pack` 時のみ symlink を pack 対象に追加する。SFTP モードでは依然として symlink は転送しない。
- `--dry-run` は計画構築後に `HostLogAggregator` でホスト数・件数を要約し、`EXIT_OK` を返す。
- `--parallel` はホスト単位での ThreadPoolExecutor に渡され、1 ホスト 1 ワーカーで処理する。ファイル単位の並列は行わない。

## パスと正規表現仕様

- `SRC` は以下のいずれかで始まることを想定する。
  - `/...` : UNIX 絶対パス。
  - `X:/...` または `X:\...` : Windows ドライブ絶対パス。内部では `X:/...` に正規化する。
  - `~/...` : `--user` のリモート HOME (`detect_remote_home`) に展開する。
  - 相対指定 : 正規表現メタ文字より前のパス部分を HOME 直下に結合する。`..` で HOME より上に出ようとすると `ValueError` として拒否し、呼び出し元でメッセージ付きスキップする。
- `~` 単独、`~user`、`~user/...` はいずれも無効。検出時に「bare tilde is not allowed」「tilde with username is not supported」を出力して終了する。
- 正規表現検出は `looks_like_regex` に従い、`^$*+?{}[]\|()` のいずれかを含むと正規表現として扱う。`.` は検出対象外。
- 正規表現は root から後ろの「ルート相対パス文字列」に対して `re.search` で評価される。Windows でも `/` 区切りでマッチングする。
- `split_src_to_root_and_tail_regex` により `root` (列挙開始ディレクトリ) と `tail_re` (正規表現) に分割される。root が存在しない場合はスキップする。

## SRC と DEST の解釈およびレイアウト

- `parse_hosts_file` で取得した各ホストに対し `_build_plan_for_host` が以下を行う。
  1. SSH/SFTP を開き、`detect_remote_home` でリモート HOME を取得。
  2. `enumerate_candidates_for_host` で `SRC` パターンに一致する絶対パスを列挙。
  3. `sftp_isfile` で通常ファイルか確認し、PlanEntry を生成。ディレクトリやデバイスは除外。
- `DEST` は `os.path.expanduser` でローカル実行ユーザの HOME を展開した後、絶対パスへ変換。`~` や `~user` は事前に拒否する。
- 保存レイアウトは `local_path_for_download(dest_local, safe_host, remote_abs)` に従い、

  ```text
  DEST/<SANITIZED_HOST>/<REMOTE_PATH_NORMALIZED>
  ```

  となる。`<SANITIZED_HOST>` は `RE_SAFE_HOST_PTN` で英数字・`.`・`_`・`-` 以外を `_` に置換し、先頭 `.` を除去。`<REMOTE_PATH_NORMALIZED>` は `_sanitize_remote_abs_for_local` により `/` の正規化、`C:/` → `C_/`、UNC の `_` 保持などを行う。

## 転送方式

### SFTP 逐次 (既定)

- PlanEntry 毎に `sftp.get(remote_abs, local_path)` を呼び出す。ディレクトリはスキップ。
- 転送ごとにローカル親ディレクトリを `mkdir(parents=True)` で作成。
- メタデータは Paramiko 既定のままで、モード/mtime は保証されない。

### `--pack`

- ホストごとに `remote_pack_paths` が `/tmp/collect_abs_<pid>_<rand>.tar.gz` を生成。
- `tar` フレーバを検出し、`--follow-symlinks` 指定時は `-h` (GNU) / `-L` (bsdtar) を付与。それ以外は警告を出しリンクとして保存。
- 圧縮した tar.gz を `download_and_extract_tar` でローカルへ取得後、`dest_local/<SANITIZED_HOST>` 配下へ展開。
- 展開時はパストラバーサル防止・親ディレクトリの symlink 拒否を行い、通常ファイルと GNU tar のハードリンクをデマテリアライズする。
- 展開したファイルに対して可能な範囲で `chmod(mode & 0o777)` と `utime(mtime)` を適用する。
- リモートの一時ファイルは `sudo -n rm -f` → `rm -f` → `true` の順に削除される。

## 転送対象の相違

| 項目 | SFTP | `--pack` |
| ---- | ---- | -------- |
| 対象とする通常ファイル | `sftp_isfile` 真のエントリのみ。 | tar に含まれる通常ファイル、および GNU hardlink を通常ファイルとして復元。 |
| ディレクトリ | PlanEntry には含めるが、転送対象ではない。親ディレクトリは必要に応じて作成。 | tar 展開時に生成。親チェーンに symlink があればスキップ。 |
| シンボリックリンク | 常に転送しない。`--follow-symlinks` でも通常ファイル化されない。 | `--follow-symlinks` 指定時のみ候補に含まれ、remote tar の dereference が成功した場合はリンク先の実体を取得。未知フレーバや失敗時はリンク自体が除外される。 |
| デバイス/FIFO/ソケット | 列挙時点で除外。 | tar 展開時の `_safe_members` で除外。 |

## シンボリックリンクと `--follow-symlinks`

- フラグ未指定時
  - 候補列挙 (`enumerate_candidates_for_host`) で symlink は取得対象外。
  - `--pack` でもリモート tar は symlink を含めず、ローカルには生成されない。
- フラグ指定時
  - `--pack` のみ有効。列挙時に symlink を pack リストへ追加。GNU tar/bsdtar では実体を取り込み、ローカルには通常ファイルとして復元される。
  - SFTP モードでは候補列挙時に symlink を検出しても PlanEntry を作成せず、実質的な影響はない。
  - 親ディレクトリが symlink の場合、抽出時に警告を出してスキップする。

## sudo の利用条件

- `--sudo-collect` 指定時: `remote_pack_paths` の tar/gzip 実行、および `enumerate_candidates_for_host` の sudo 経路で `sudo -n` を強制する。
- `--no-sudo-collect` 指定時: 常に sudo を使用しない。
- 指定なし: `--ssh-user != --user` のときだけ sudo を有効化。sudo 実行ユーザは `--ssh-user` で、`sudo -n` により権限昇格する。昇格先は通常 root (sudoers 設定による)。
- ローカル側で sudo を用いる処理は存在しない。

## SELinux について

`gm-gather` は SELinux ラベルの検出・復元機能を持たず、`--selinux` オプションも実装していない。SELinux 対応ホスト判定 (`restorecon` や selinuxfs の確認) は行われないため、収集結果に SELinux コンテキストは付与されない。

## メタデータ復元条件

- 所有者/グループ: SFTP・`--pack` ともに復元しない。ローカルファイルは実行ユーザで作成される。
- アクセス権: SFTP は Paramiko 既定 (作成時の umask 依存)。`--pack` では tar 内のモードを `chmod` で適用 (失敗時は警告なしで継続)。
- mtime: SFTP は保証なし。`--pack` は `utime` で設定を試みる。
- xattr/ACL: いずれの方式でも復元しない。

## 並列処理 (`--parallel`)

- `gather_parallel.execute` がホスト単位で ThreadPoolExecutor を使用する。ファイル転送はホスト内で逐次。
- 並列度は `max(1, int(parallel))` に丸められる。非常に大きい値を指定するとスレッド数が増えるため、リモートホスト負荷とネットワーク帯域に注意。

## エラーメッセージと終了コード

`print()` で出力される代表的なメッセージと終了コードは次の通り (メッセージは gettext で翻訳される)。

| メッセージ (msgid) | 発生条件 | 終了コード |
| ------------------- | -------- | ---------- |
| `"At least one SRC and a DEST are required."` | 位置引数が不足。 | `EXIT_ERR_ARGS` (4) |
| `"bare tilde is not allowed"` | `SRC`/`DEST` に `~` 単独を指定。 | `EXIT_ERR_ARGS` (4) |
| `"tilde with username is not supported"` | `SRC`/`DEST` に `~user` 形式を指定。 | `EXIT_ERR_TILDE_USER` (3) |
| `"No hosts found in hosts file."` | ホストファイルから有効ホストが得られない。 | `EXIT_ERR_NO_HOSTS` (1) |

- 実行中の転送でエラーが起きると `HostLogAggregator` に `errors>0` が記録され、終了コードは `EXIT_ERR_GENERIC` (2) となる。
- `--dry-run` で計画生成のみ実行した場合は常に `EXIT_OK` (0)。

## シグナル受信時の動作

- `GracefulStop` を登録し、`SIGINT`/`SIGTERM` で `abort_event` をセットする。以後新規ホストを開始せず、進行中のホストにはキャンセルを通知。
- 中断後は `"Interrupt requested; cancelling remaining transfers…"` (WARN) をログに出力する。成功/失敗に応じて exit code は 0 または 2。
- クリーンアップとして `close_all()`、ローカル temp、集計ログ (`summary`) を順に実行する。

## 制限と注意事項

- `SRC` の正規表現は Python `re` 準拠であり、ロケール依存挙動を持つ metacharacter を含む場合がある。必要に応じて `(?u)` などを指定すること。
- `--pack` にはリモートに `tar` と `gzip` が必要。利用不可の場合、エラー終了となる。
- `--follow-symlinks` は `--pack` と組み合わせた場合のみ実質的な効果があり、リモート tar が dereference をサポートしない場合は symlink を取得できない。
- ローカル保存先に既存ファイルがある場合は上書きされる。差分取得機能は無い。
- `--password` は標準入力からではなくコマンドラインで指定するため、履歴に残る点に注意。
- Windows ホストの UNC パスは `//srv/share` 形式で指定可能だが、保存時は `_` を含むパスに正規化される。
- 長時間の収集で `SIGINT` を送信すると部分的な取得で終了する可能性がある。ログと出力内容を確認して再実行すること。
- 取得したファイルの整合性チェック (ハッシュ検証等) は実装していない。必要に応じて別途確認すること。
