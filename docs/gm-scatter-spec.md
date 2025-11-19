# gm-scatter 仕様

本書は `gm-scatter` コマンドの挙動を現行実装に基づいて定義する。共通事項や用語は `docs/common-spec.md` を参照すること。

## 引数とオプション

### SYNOPSIS

```plaintext
gm-scatter [OPTIONS] SRC... DEST
```

- `SRC...` : 1 つ以上のローカルパスまたは正規表現。
- `DEST`   : リモートホスト上の基準ディレクトリ。必須。

### 位置引数

| 引数 | 説明 |
| ---- | ---- |
| `SRC...` | ローカル側の送信元。リテラル/正規表現/チルダ展開規則は「パスと正規表現仕様」を参照。最低 1 件必要。 |
| `DEST` | リモート側の配置先。相対指定はリモート `--user` の HOME に対して解決される。`~` や `~user` 形式は無効。 |

### オプション一覧 (man スタイル)

| オプション | 説明 |
| ----------- | ---- |
| `-H`, `--hosts` *FILE* | ホストファイルを指定。既定値 `hostfile` (`core_constants.DEFAULT_HOSTS_FILE`)。空行・先頭 `#`・トレーリングコメントは無視。 |
| `-u`, `--user` *USER* | リモートでファイルを配置すべきターゲットアカウント。既定は実行ユーザ。SELinux や sudo 判定もこの値を基準にする。 |
| `-s`, `--ssh-user` *USER* | SSH ログインユーザ。省略時は `--user` と同じ。`--sudo-extract` 自動判定や SFTP 経路の `sudo_mkdir` に影響。 |
| `-P`, `--port` *PORT* | SSH ポート番号。既定 `22` (`core_ssh.DEFAULT_SSH_PORT`)。 |
| `-K`, `--key` *PATH* | SSH 秘密鍵ファイル。未指定時は Paramiko の既定に従う。 |
| `-W`, `--password` *PASS* | SSH パスワード (履歴への残存に注意)。鍵認証推奨。 |
| `-T`, `--timeout` *SEC* | SSH セッションおよびリモートコマンドのタイムアウト秒。既定 `core_ssh.DEFAULT_TIMEOUT` (30 秒)。 |
| `-S`, `--strict-host-key-checking` | Paramiko に `RejectPolicy` を設定し未知のホスト鍵を拒否。省略時は `AutoAddPolicy`。 |
| `-j`, `--parallel` *N* | ホスト単位の並列数。1 以上で `scatter_parallel.execute` のワーカ数となる。既定 `core_constants.DEFAULT_PARALLEL_HOSTS` (4)。 |
| `-n`, `--dry-run` | 転送を行わず計画とログ集計のみ実施。Exit code は常に 0。 |
| `-v`, `--verbose` | 追加の DEBUG ログを有効化。dry-run 時も適用。 |
| `--pack` | ローカルで tar.gz を生成し 1 回のアップロードで展開する。省略時は SFTP 逐次転送。 |
| `--follow-symlinks` | `--pack` 使用時にローカル symlink を実体化して含める。SFTP モードでは無効。 |
| `-x`, `--sudo-extract` | `--pack` 経路でリモート `mkdir`/`tar`/上書きを常に `sudo -n` で実行。`--no-sudo-extract` で明示的に無効化できる。指定なしは自動判定。 |
| `--selinux` *MODE* | `auto`/`policy`/`ignore`。`--pack` 時の SELinux ラベル復元方針。既定 `auto`。SFTP 経路では効果なし。 |

### オプション詳細

- SSH 接続は Paramiko 経由で確立し、`ssh_open`/`finalize_sockets` でライフサイクルを管理する。
- `--timeout` は `detect_remote_home`、`run_remote_cmd_capture`、`upload_pack_and_extract` などすべてのリモート操作に伝搬する。
- `--pack` 指定時はホストごとに 1 度だけ `local_pack_paths_to_tmp` で tar.gz を生成し、`upload_pack_and_extract` が `DEST` 直下へ展開する。PlanEntry 毎に個別アップロードは行わない。
- SFTP モードでは `sftp_put_one` が PlanEntry ごとに実行され、`PlanEntry.is_dir` が真でもファイルのみをアップロードする。ディレクトリはリモートで `mkdir -p` する。
- `--sudo-extract` / `--no-sudo-extract` を両方省略した場合、`--pack` かつ `--ssh-user != --user` のときだけ自動的に `sudo -n` を使用する。
- SFTP 経路でも `--ssh-user != --user` の場合は `remote_mkdir_p(... use_sudo=True)` で親ディレクトリを作成する。以後の SFTP 書き込みは SSH ユーザ権限のまま行い、書き込み不可なら即エラーになる。
- `--selinux` は `--pack` 時のみ評価され、`restorecon_recursive_if_needed` の挙動を制御する。`policy` 指定で対応不可ホストに対してはエラー扱い。
- `--dry-run` はローカルでの候補列挙とホスト別集計のみ行い、SSH/SFTP ハンドラ生成後は何も送信しない。

## パスと正規表現仕様

- `SRC` はローカルパスを前提とする。解釈規則は以下の通り。
  - `/...` : UNIX 絶対パス。`resolve_token_for_scatter` が絶対化し、`DEST` 下では先頭スラッシュを除去した形に正規化する。
  - `X:/...` または `X:\...` : Windows ドライブ絶対パス。`dest_rel_from_abs` により `X/...` 形式へ変換。Linux 上で Windows パスを指定すると `ValueError` でスキップされる。
  - `~/...` : 起動ユーザの HOME ディレクトリに展開 (`scatter_expand_tilde_for_exec_user`) した上で扱う。
  - 相対パス : 起動時のカレントディレクトリからの相対。`validate_relative_token_safe` が `..` による脱出を拒否し、失敗時は `ValueError` として PlanEntry 生成前に除外する。
  - `~` および `~user`／`~user/...` は CLI で明示的に拒否し、`"bare tilde is not allowed"` または `"tilde with username is not supported"` を表示して終了する。
- 正規表現判定は `looks_like_regex` に従い、`^$*+?{}[]\|()` のいずれかを含むと regex とみなす (`.` は除外)。
- 正規表現 `SRC` は `split_src_to_root_and_tail_regex` により `(root, tail_regex)` に分割し、`root` 配下のファイル名 (ディレクトリは除外) に対して `re.search(tail_regex)` を適用する。マッチしたファイルの絶対パスが `PlanEntry` に入る。`root` 自体がマッチする場合はディレクトリエントリも追加され、`is_dir=True` で扱う。
- リテラル `SRC` は存在するファイル/ディレクトリに限り列挙され、存在しない場合は静かにスキップされる。
- `--pack` 指定時の `_normalize_pack_srcs` はリテラルで実体がディレクトリのものに末尾スラッシュを付与し、正規表現はそのまま保持する。

## SRC と DEST の解釈とレイアウト

- `DEST` は `_resolve_remote_dest` により解決される。
  - `/...` : リモートの絶対パスとして使用。
  - `X:/...` または `X:\...` : Windows ドライブ絶対パスとして許容。
  - `~/...` : `detect_remote_home` で取得したターゲットユーザの HOME をプレフィックスとして展開。
  - 相対文字列 : リモート HOME に連結 (`$HOME/relative`)。
  - `~` および `~user` 形式は無効で `EXIT_ERR_ARGS` (4) を返す。
- レイアウトは `PlanEntry.relpath` により決定され、すべて POSIX 形式 (`/` 区切り) に正規化される。
  - 絶対 `SRC` : 先頭スラッシュを除去 (`/etc/hosts` → `etc/hosts`、`C:\logs` → `C/logs`)。
  - 相対 `SRC` : 指定した相対パスを正規化 (`foo/bar` → `foo/bar`)。
  - 正規表現 : マッチした実パスの `base_abs` から `normalize_rel_for_dest` した値を基点に、マッチしたファイルの相対を連結する。
- `_normalize_remote_rel_file` がリモート相対パスから `..`、`./`、先頭 `/` を除去し、`DEST` を超える配置やパストラバーサルを拒否する。正規化結果が空文字の場合は basename にフォールバックする。
- すべてのホストで共通の `DEST` を使用し、`gm-scatter` は `DEST/<relpath>` へ直接配置する。`gm-gather` と異なりホスト名サブディレクトリは作成しない (`join_host_dir=False`)。

## 転送方式

### SFTP 逐次 (既定)

- `PlanEntry` ごとに `sftp_put_one` を呼び出し、ファイル単位でアップロードする。ディレクトリはローカル `os.walk` で展開し、リモートでは `remote_mkdir_p` で作成する。
- `ssh_user != --user` の場合、親ディレクトリ作成は `sudo -n mkdir -p` で実行するが、ファイルの書き込みは SSH ユーザ権限のままとなる。書き込み不可であれば `E_SFTP_DIR_NOT_WRITABLE` を記録して失敗とする。
- 新規ファイル・ディレクトリの所有者は SSH ユーザ、パーミッションはリモート側の umask 依存。SELinux ラベル、ACL、xattr は復元されない。
- 既存ファイルを上書きする場合、`sudo_mkdir=True` なら上書き前に `stat/getfacl/getfattr` でメタデータを取得し、アップロード後に `chown`/`chmod`/`setfacl`/`setfattr` を試みて復元する。`sudo_mkdir=False` の場合はメタデータを取得しない。
- シンボリックリンクは常に `status="dropped"` として除外される。

### `--pack`

- 各ホストで最初の PlanEntry を処理するときだけ `local_pack_paths_to_tmp` が呼ばれ、マッチしたファイル/ディレクトリをまとめて tar.gz にする。重複パスや親子関係は tar 作成時に正規化される。
- 生成したアーカイブを `sftp.put` でリモート一時領域へ送信し、`upload_pack_and_extract` が `DEST` 直下に展開する。
- tar フレーバに応じて適切な `tar` コマンドを構築し、既存ファイルと新規ファイルを分離して処理する。
  - 新規 (NEW) : `tar -T members.new.txt` で直接 `DEST` 以下に展開。
  - 既存 (EXIST) : 一時ディレクトリに展開し、`cat`→`mv` で上書き。上書き前に所有者・グループ・モード・ACL・xattr をダンプし、`sudo_extract` 有効時に復元する。
- 空ディレクトリは別途 `remote_mkdir_p` で作成し、`sudo_extract` 有効時は `chown` でターゲットユーザの primary group に合わせる。
- `follow_symlinks` 有効時のみ symlink を実体化してアーカイブに含める。無効時は symlink エントリを落とす。
- リモート一時ファイル (`mktemp` で生成) は正常終了・エラーを問わずクリーンアップする。`sudo_extract` 有効時は tar/restorecon/chown も sudo 経由で実行する。
- SELinux ラベル再設定 (`restorecon`) は pack 経路のみで実行される。

## 転送対象の相違

| 項目 | SFTP | `--pack` |
| ---- | ---- | -------- |
| 通常ファイル | 逐次 `sftp.put`。既存ファイルは sudo 経路でのみメタ復元。 | tar 内のファイルを新規/既存に分けて展開。既存は一時領域経由で上書きしメタ復元。 |
| ディレクトリ | `os.walk` で作成し、親を `remote_mkdir_p`。属性復元は行わない。 | tar 展開時に生成。`sudo_extract` 有効なら新規ディレクトリへ `chown`。 |
| シンボリックリンク | 常に除外 (`status="dropped"`)。 | `--follow-symlinks` 有効時にリンク先実体を収集。無効時はアーカイブにも入らない。 |
| ハードリンク | Paramiko 経路ではハードリンク情報を保持しない。 | GNU tar のハードリンクは tar 展開時に通常ファイルとして復元する。 |
| デバイス/FIFO/ソケット | 列挙時点で除外。 | `_safe_members` チェックで除外し、展開しない。 |
| ACL/xattr | `sudo_mkdir=True` かつツール存在時に既存ファイルのみ復元。 | `sudo_extract=True` かつツール存在時に既存ファイルで復元。新規はデフォルト。 |

## --follow-symlinks の挙動

- フラグ未指定時
  - SFTP: ローカル symlink は PlanEntry 作成後に `status="dropped"` で無視される。
  - `--pack`: `tarfile` 生成時に symlink エントリを除外し、リモートには配置されない。
- フラグ指定時
  - SFTP: 挙動に変化なし (symlink は転送しない)。
  - `--pack`: `tarfile.open(... dereference=True)` により symlink をリンク先の実体として格納し、リモートでは通常ファイル/ディレクトリとして展開される。親ディレクトリが symlink の場合は展開処理で警告を出してスキップする。

## sudo の利用条件

- パラメータ `sudo_extract` は以下で決定される。
  - `--sudo-extract` 明示指定 → 常に `True`。
  - `--no-sudo-extract` 明示指定 → 常に `False`。
  - いずれも指定なし → `--pack` かつ `--ssh-user != --user` の場合だけ `True`。
- `sudo_extract=True` の場合、以下の処理で `sudo -n` を使用する。
  - リモート `mkdir -p`、tar 抽出、既存ファイルの上書き (`cat`→`mv`)、`chown/chmod`、ACL/xattr 復元、`restorecon`、一時ファイル削除。
  - primary group の取得 (`id -gn target_user`) にも sudo を使用する。
- SFTP 経路では `ssh_user != --user` のとき親ディレクトリ作成に sudo を使用するが、ファイル上書きは SSH ユーザ権限で行う。`sudo` を使ってファイル内容を書き込む経路は存在しない。
- ローカルホストで `sudo` を呼び出す処理は無い。

## SELinux 対応

### SELinux 利用可能ホストの判定

`detect_selinux_capable` は以下の条件を満たす場合に `True` を返す。
- `/sys/fs/selinux` ディレクトリが存在する、または `mount` の出力に `selinuxfs` が含まれる。
- `restorecon` コマンドが存在する (`command -v restorecon`).

### `--selinux` オプションの挙動

- `ignore` : SELinux 処理を常にスキップ。
- `auto` (既定) : `detect_selinux_capable` が `True` のときだけ `restorecon -RF` を実行。非対応ホストでは黙ってスキップ。
- `policy` : ホストが SELinux 非対応の場合は `RuntimeError` を送出し、転送失敗として扱う。対応時は `restorecon -RF` を強制。
- 処理対象は pack 経路で生成・更新されたファイルと空ディレクトリであり、既存ファイルの上書き後も対象に含める。SFTP 経路では `restorecon` を呼ばない。

## メタデータ復元条件

- 所有者/グループ/パーミッション
  - SFTP: 既存ファイルを上書きするとき `sudo_mkdir=True` かつ `getfacl`/`getfattr` が利用可能であれば、上書き前に取得したメタデータを `chown_chmod` で復元する。新規ファイル/ディレクトリは SSH ユーザ所有で作成される。
  - `--pack`: `sudo_extract=True` の場合、既存ファイルで捕捉した所有者・グループ・モードを復元し、新規ファイルもターゲットユーザ (および primary group) に `chown` する。`sudo_extract=False` では復元を行わない。
- タイムスタンプ
  - SFTP: Paramiko の `sftp.put` に任せる (mtime/atime の維持は保証しない)。
  - `--pack`: 一時ディレクトリから上書きする際に `touch -r` を実行し、元ファイルと同じ mtime に合わせるよう試みる。失敗しても転送は継続する。
- ACL/xattr
  - いずれの経路でも、リモートに `getfacl`/`setfacl` および `getfattr`/`setfattr` が存在しない場合は取得・復元を行わない。
  - SFTP: 既存ファイルを上書きする際に `sudo_mkdir=True` かつ各ツールが利用可能なら復元する。新規作成では行わない。
  - `--pack`: `sudo_extract=True` かつツールが利用可能な場合に既存ファイルで復元する。新規はデフォルト値のままとなる。
- SELinux コンテキスト
  - `--pack` 経路で `--selinux` により auto/policy が有効になっているときのみ `restorecon` で再設定する。
  - SFTP 経路では SELinux ラベルは変更しない。

## 並列処理 (`--parallel`)

- `scatter_parallel.execute` がホスト単位で `ThreadPoolExecutor` を使用し、最大 `max(1, parallel)` 個のワーカーを動作させる。`Plan` の処理はホスト内で逐次実行され、ファイル単位の並列化は行わない。
- 進捗は `HostLogAggregator` を通じて `seq/trial/processed/total` が記録される。`GracefulStop.abort_event` による協調キャンセルを共有する。

## ログ・エラーメッセージ・終了コード

代表的な標準エラー出力と終了コードは下表の通り (メッセージは gettext 化されている)。

| メッセージ (msgid) | 発生条件 | 終了コード |
| ------------------- | -------- | ---------- |
| `"At least one SRC and a DEST are required."` | `SRC` または `DEST` の不足。 | `EXIT_ERR_ARGS` (4) |
| `"bare tilde is not allowed"` | `SRC` または `DEST` に `~` 単独を指定。 | `EXIT_ERR_ARGS` (4) |
| `"tilde with username is not supported"` | `SRC`/`DEST` に `~user` 形式。 | `EXIT_ERR_ARGS` (4) |
| `"No hosts found in hosts file."` | ホストファイルに有効なホストが存在しない。 | `EXIT_ERR_NO_HOSTS` (1) |

- 転送実行中にエラーが発生すると `HostLogAggregator` が `errors>0` を記録し、`run_parallel` は `EXIT_ERR_GENERIC` (2) を返す。
- `--dry-run` 成功時は常に `EXIT_OK` (0)。

## シグナル受信時の動作

- `GracefulStop` を登録し、`SIGINT`/`SIGTERM` 受信で `abort_event` をセットする。以後新規ホストの着手を停止し、進行中の `run_host_scatter` ループに `CancelledError` を伝播させる。
- 中断が発生すると `"Interrupt requested; cancelling remaining transfers (some remote hosts may see partial files or directories)."` を WARN ログに記録する。
- クリーンアップは `GracefulStop.run_cleanups()` を介し、リモート一時領域 → ローカル一時領域 → SSH/SFTP クローズ → サマリ出力の順に実行される。部分的なアップロードが残る可能性があるため、再実行時はホスト側の整合性確認が推奨される。

## 制限と注意事項

- 正規表現は Python `re` を使用し、ロケール依存メタ (`\w` など) の挙動はプラットフォームに依存する。
- `--pack` にはローカル Python の `tarfile` モジュール、リモート `tar`/`gzip`/`mktemp`/`bash` が必要。欠如すると `E_TAR_DETECT` や `E_PREFLIGHT` で失敗する。
- `--follow-symlinks` は pack 経路でのみ効果があり、symlink 先の実体が存在しない場合は tar 生成時にスキップされる。
- `--password` はコマンドラインに平文で残る。可能な限り鍵認証を使用すること。
- SFTP 経路ではリモート側の umask に依存して権限が変化する。必要に応じて `--pack` や事前調整で補う。
- SELinux ラベル復元は pack 経路でのみ実行され、SFTP ではラベル齟齬が生じる可能性がある。
- ACL/xattr 復元は `getfacl/setfacl`・`getfattr/setfattr` がインストールされている場合に限り機能する。存在しない場合でも転送自体は継続される。
- `GM_SCATTER_DEBUG=1` を設定するとデバッグログが増えるが、出力量が多いため本番運用では無効化を推奨する。
