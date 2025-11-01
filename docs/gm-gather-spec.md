# gm-gather.py 仕様

## 機能概要

複数のリモートホスト上から, 正規表現 ( 絶対パス / 相対パス ) で選択したファイルを収集し, ローカルの `dest` 配下に保存する。収集方式は次の 2 通り :

1. 直接 SFTP ( デフォルト ) : SFTPプロトコルを用いてファイル, ディレクトリを逐次転送する
2. リモート圧縮収集 ( `--pack` ) : リモートホスト上で作成した `tar.gz` アーカイブをダウンロード後, ローカルで展開する

---

## コマンドライン仕様

```text
Usage:
  gm-gather.py [-h] [--user USER] [--ssh-user SSH_USER]
               [--pattern-abs PATTERN_ABS] [--pattern-rel PATTERN_REL]
               [--parallel PARALLEL] [--ignore-case] [--hosts HOSTS]
               [--roots [ROOTS ...]] [--port PORT] [--key KEY]
               [--password PASSWORD] [--timeout TIMEOUT]
               [--strict-host-key-checking] [--pack] [--one-archive]
               [--dry-run] [--verbose]
               dest

Collect remote files by regex (supports absolute and relative path patterns).

- The single positional argument is dest (local destination directory).
- At least one of --pattern-abs / --pattern-rel must be specified.

Positional arguments:
  dest                      Local destination directory (created if absent).

Selection (remote):
  -a, --pattern-abs RE      Regex for ABSOLUTE remote paths. Repeatable. [default: none]
  -r, --pattern-rel RE      Regex for path RELATIVE to each --roots entry. Repeatable. [default: none]
  -R, --roots PATH [...]    Remote search root(s). "~" is resolved to --user's HOME. Use ABS paths. [default: "~"]
  -i, --ignore-case         Compile regex with IGNORECASE. [default: off]

Remote & SSH:
  -H, --hosts FILE          Hosts file (one host per line; empty line, '#' lines,
                            and text after SPACE/TAB+'#' are comments). [default: hostfile]
  -u, --user USER           Target account semantics on remote (for '~' resolve). [default: local user]
  -s, --ssh-user USER       SSH login user. [default: same as --user]
  -P, --port PORT           SSH port. [default: 22]
  -K, --key PATH            SSH private key file. [default: none]
  -W, --password PASS       SSH password (not recommended). [default: none]
  -T, --timeout SEC         SSH/command timeout seconds. [default: 30]
  -S, --strict-host-key-checking
                            Enable strict host key checking (RejectPolicy). [default: AutoAdd]

Collection mode:
      --pack                Pack on the REMOTE host using tar, then download & extract. [default: off]
      --one-archive         With --pack: combine all roots into ONE tar (name collisions may overwrite). [default: off]
  -j, --parallel N          Parallel hosts. [default: 4]
  -n, --dry-run             List matches only; do not download. [default: off]
  -v, --verbose             Verbose logs. [default: off]
```

---

### デフォルト値

- `--hosts`: `hostfile`
- `--user`: 実行ユーザ
- `--ssh-user`: `--user` と同じ
- `--parallel`: `4`
- `--port`: `22`
- `--timeout`: `30` ( 秒 )
- `--strict-host-key-checking`: `false` ( AutoAddとして扱う )
- `--pack`: `false`
- `--one-archive`: `false`
- `--pattern-abs`: なし
- `--pattern-rel`: なし
- `--roots`: `["~"]` (実行ユーザのホームディレクトリ)
- `--ignore-case`: `false`
- `--dry-run`: `false`
- `--verbose`: `false`

---

## 終了コード

- `0`: 正常終了
- `1`: ファイル未取得 ( エラーがあり 1 件も取得できず )
- `2`: 部分取得 ( エラーありだが一部は取得成功 )
- `3`: 一致ファイルなし
- `4`: モジュール未インストール ( 例: `paramiko` )
- `5`: 無効引数 ( パターン未指定, 正規表現エラー等 )

---

## 位置引数とパターン指定の解釈

- 本ツールの位置引数は `dest` のみ。
- 収集対象の指定は, 正規表現パターン で行う :
  - `--pattern-abs`: リモートホストの 絶対パス にマッチ
  - `--pattern-rel`: `--roots` で与えた各ルートからの 相対パス にマッチ
- どちらか一方, または両方を複数回指定可。いずれかに一致すれば収集対象とみなす。
- `--pattern-abs` と `--pattern-rel` の両方にマッチした場合は, 保存レイアウト重複を避けるため相対ヒットを優先して保存し, 絶対のみの保存は抑止する。

---

## `roots` と `~` の解決, および権限

- `--roots`オプション中に指定した `"~"` は, 収集アカウント ( `--user` ) の HOMEディレクトリとして解釈される。
- SSH ログインユーザ ( `--ssh-user` ) と収集アカウント ( `--user` ) が異なる場合は, 収集対象のリストアップやアーカイブ作成時に `sudo -n` を用いる。
- `--ssh-user != --user`, または `--pack` 指定時は, 事前に リモートホスト上で, `tar`, `gzip`, および, `sudo -n` が実行可能であることを確認する。

---

## ローカルホスト上での保存レイアウトについて

- 相対ヒット: `dest/<host>/rel/<root からの相対パス>`
- 絶対のみヒット: `dest/<host>/abs/<先頭 '/' を除いた絶対パス>`

同一ホスト内で同じ相対名が複数 ROOT に存在する場合, 展開順により後勝ちで上書きされ得る ( `--one-archive` オプション指定の有無に依存 ) 。

---

## パス区切りと正規化

- `--pattern-rel` の評価では, 評価時のみパス区切り文字を `/` に正規化してマッチ判定する。
- 絶対パスは OS 依存表現を許容するが, 正規表現はリモート側のパス表現 ( 通常 `/` 区切り ) に合わせることを推奨。
- `--ignore-case` 指定時は, 正規表現を `re.IGNORECASE` でコンパイルし, 収集対象ファイルの選定を大文字小文字の区別なく実施する。

---

## 収集方式

### 直接 SFTP ( デフォルト )

- マッチしたファイルを 1 件ずつSFTP で `dest/<host>/rel/...` または `dest/<host>/abs/...` に保存する。
- シンボリックリンクは, 取得しない ( リンク先実体の直接ダウンロードも行わない ) 。

ネットワーク遅延が大きい場合や転送対象ファイルが多い場合, 処理時間が増加する可能性があることに留意してください。

### リモート圧縮収集 ( `--pack` )

- リモートホストで収集対象のファイルを`--roots`で指定した基点となるディレクトリ (ROOT)単位 ( `--one-archive` 指定時は, 単一 ) の `tar` アーカイブに格納し, `gzip` による圧縮を施した後, 1 回の転送でローカルホストにダウンロードし, アーカイブの内容を展開する。
- パストラバーサル防止, リンクファイルを展開しないことから, 安全にファイル, ディレクトリの取得と展開が可能。
- 相対ヒット: ROOT ごとに `cd <root>; tar -cf|-rf` で相対名を収集。
- 絶対のみヒット: `tar -P` ( absolute paths ) を用いて絶対名を保持。
- `--one-archive` を指定すると, 全 ROOT を 1 つの tar アーカイブに集約する。相対名が衝突した場合, 後から収集されたファイルの内容で,  上書きされる。
- 展開はローカルホスト側で実施する。展開時に, 先頭 `/` の除去, `..` 折り畳み, シンボリックリンク・ハードリンク・特殊ファイルをスキップする処理を実施することで, より安全にファイルの取得を行える 。

---

## 並行実行

`--parallel` ( 既定 4 )で指定された数のリモートホストに対して並行してファイル, ディレクトリの転送を実施する。

## メッセージ出力種別について

本ツールの出力メッセージは以下のように分類される。

- **Info**: スキャン件数, ヒット数, ダウンロード件数, モード ( DRY-RUN/pack/SFTP ) などを逐次表示。
- **Warning**: 一部ファイルの取得失敗や衝突上書きの可能性などを継続警告。
- **Error**: ホスト単位の失敗は集計してサマリに表示 ( 致命条件は即時終了 ) 。

---

## ホストファイル形式

本ツールのホストファイル形式は以下の通り:

- 1 行につき, 1 ホストを記載する。
- 空行, `#`で開始する行, および, タブ または, 空白 に続く `#` 以降はコメントとして扱う。

---

## 運用上の注意

- 正規表現パターンが必須オプションであること, どちらも未指定の場合は引数エラー終了する ( 終了コード 5 ) 。
- `--pack` は転送効率に優れるが, ローカルホスト / リモートホスト側のストレージの一時ストレージ容量がアーカイブサイズ分必要となるため,ストレージの空き容量を確認の上, 実行すること。
- `--ssh-user != --user` の場合, `sudoers` に `NOPASSWD`を設定することを推奨する ( `sudo -n` を実行するための前提条件 ) 。
- 絶対パス指定 / 相対パス指定の 双方で重複ヒットした場合は, 相対パス指定を優先する。レイアウトの意図に応じて正規表現に指定するパターンや指定順序を検討すること。
- `--one-archive` 利用時は 複数の相対パス名間で名前の衝突が起こった場合は, 後から追加されたファイル, ディレクトリの内容で上書きされる。

---

## 依存パッケージ

本ツールの動作には以下のパッケージが必要となる:

- Paramiko ( OS パッケージ `python3-paramiko` または `pip install paramiko` )

## 使用例

### 基本的な使用法
