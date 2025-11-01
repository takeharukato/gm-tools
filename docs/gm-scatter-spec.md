# gm-scatter.py 仕様

## 機能概要

ローカルのファイル / ディレクトリを, ホストファイルで列挙した複数のリモートホスト上の `dest` 配下へ配布する。配布方式は 以下の2 通り :

1) 逐次 SFTP ( デフォルト ) : SFTPプロトコルを用いてファイル, ディレクトリを逐次転送する
2) 一括アーカイブ配布 ( `--pack` )  : ローカルで作成した `tar.gz` アーカイブをリモートホストにアップロードし, リモートホスト上でアーカイブを展開する

---

## コマンドライン仕様

```
usage: gm-scatter.py [-h] [-a PATTERN_ABS] [-r PATTERN_REL] [-R ROOT] [-i]
                     [-H HOSTS] [-u USER] [-s SSH_USER] [-P PORT] [-K KEY]
                     [-W PASSWORD] [-T TIMEOUT] [-S] [--pack]
                     [--preserve-perms | --no-preserve-perms]
                     [--preserve-owner | --no-preserve-owner]
                     [--preserve-acls | --no-preserve-acls]
                     [--preserve-xattrs | --no-preserve-xattrs]
                     [-j PARALLEL]
                     [-n] [-v] [--follow-symlinks] [--include-empty-dirs]
                     [--selinux {auto,policy,archive,ignore}]
                     [src ...] [dest]

Distribute local files/dirs to multiple remote hosts under a destination directory.

- The last positional argument is always dest (remote destination).
- If only dest is given, at least one pattern (--pattern-abs/--pattern-rel) must select files.
  If no files are selected, it is treated as "no targets" and the program exits with code 1.

Positional arguments:
  src                  0 or more local files/dirs. Relative src is resolved from the current directory.
  dest                 Remote destination directory. If relative, it is resolved from the remote target account's HOME (i.e., --user). This is independent of the SSH login user (--ssh-user).

Selection (local):
  -a, --pattern-abs RE      Regex for ABSOLUTE local paths (after normalization). Repeatable. [default: none]
  -r, --pattern-rel RE      Regex for RELATIVE paths to each --root (evaluated on '/'-separated forms only).
                            Repeatable. [default: none]
  -R, --root PATH [...]    Local search root. [default: current directory]
  -i, --ignore-case         Compile regexes with IGNORECASE. [default: off]

Remote & SSH:
  -H, --hosts FILE          Hosts file (one host per line; empty lines, lines starting with '#',
                            and text after a TAB/space followed by '#' are comments). [default: hostfile]
  -u, --user USER           Target account semantics on remote (for '~' resolution). [default: local user]
  -s, --ssh-user USER       SSH login user. [default: same as --user]
  -P, --port PORT           SSH port. [default: 22]
  -K, --key PATH            SSH private key file. [default: none]
  -W, --password PASS       SSH password (not recommended). [default: none]
  -T, --timeout SEC         SSH/command timeout seconds. [default: 30]
  -S, --strict-host-key-checking
                            Enable strict host key checking (RejectPolicy). [default: AutoAdd]

Transfer mode:
      --pack                Use local tar.gz  =>  upload  =>  remote extract (fast for many files). [default: off]
      --preserve-perms      Preserve permissions on extract (tar -p).
      --no-preserve-perms   NOT preserve permissions on extract.
      --preserve-owner      Preserve owner/group on extract.
      --no-preserve-owner   NOT preserve owner/group on extract.
      --preserve-acls       Preserve ACLs on extract.
      --no-preserve-acls    NOT preserve ACLs on extract.
      --preserve-xattrs     Preserve extended attributes (xattr).
      --no-preserve-xattrs  NOT preserve extended attributes (xattr).
  -j, --parallel N          Parallel hosts. [default: 4]
  -n, --dry-run             Show plan only; do not upload or extract. [default: off]
  -v, --verbose             Verbose logs. [default: off]
      --follow-symlinks     Follow symlinks when scanning local root / packing explicit srcs. [default: off]
      --include-empty-dirs  SFTP mode: also create empty directories found under explicit src directories.

SELinux:
      --selinux {auto,policy,archive,ignore}
                            SELinux handling.
                            auto: if selinuxfs exists and 'restorecon' is available on remote,
                                  run 'restorecon -RF dest' after deploy; otherwise do nothing.
                            policy: require selinuxfs & restorecon; run 'restorecon -RF dest'.
                            archive: require --pack and --preserve-xattrs; restore security.selinux xattr if present;
                                     and if selinuxfs & restorecon exist, also run 'restorecon -RF dest'.
                            ignore: do nothing.
                            [default: auto]
```

---

### 各オプションのデフォルト値

- `--hosts` : `hostfile`
- `--user` : 実行ユーザ
- `--ssh-user` : `--user` と同じ
- `--parallel` : `4`
- `--port` : `22`
- `--timeout` : `30` ( 秒 )
- `--strict-host-key-checking` : `false` ( AutoAddとして扱う )
- `--pack` : `false`
- `--preserve-perms` : `false` ( `--pack` 指定時は `true` )
- `--preserve-owner` : `false` ( `--pack` 指定時は `true` )
- `--preserve-acls` : `false` ( `--pack` 指定時は `true` )
- `--preserve-xattrs` : `false` ( `--pack` 指定時は `true` )
- `--pattern-abs` : なし
- `--pattern-rel` : なし
- `--root` : カレントディレクトリ
- `--ignore-case` : `false`
- `--dry-run` : `false`
- `--verbose` : `false`
- `--follow-symlinks` : `false`
- `--include-empty-dirs` : `false`
- `--selinux` : `auto`

---

## コマンド終了コード

- `0` : 正常終了
- `1` : 配布対象なし ( src 不存在, パターン未指定, パターン不一致により転送対象件数が0件になった場合 )
- `2` : 部分成功 ( 継続エラーあり )
- `4` : 必須モジュール不在 ( 例 : paramiko )
- `5` : 無効引数 ( `dest` 欠落, 正規表現コンパイルエラー等 )


### 位置引数指定によるファイルパスと正規表現指定によるファイルパスの統合処理

- 常に最後の位置引数は, `dest` ( リモートの配置先ディレクトリ ) として扱われる。
- `src` は0 個以上。
- 位置引数が 1 個 ( `dest` のみ ) のとき, パターン選択 ( `--pattern-abs` / `--pattern-rel` / `--root` ) で 1 個以上選定できなければ, 配布対象なしとして終了コード 1 で終了する。
- 位置引数が **2 個以上 ( `src … dest` ) ** かつ, パターン指定があるときは,
  「パターンで選ばれたファイル / ディレクトリの集合」 と 「位置引数で明示された `src` ファイル / ディレクトリの集合」 の和集合を配布する ( ローカル上の絶対パスで解釈して同一パスに存在するファイル / ディレクトリは, 高々 1 回のみ転送する ) 。

### `dest` の解釈について

- `dest` が絶対パスの場合 : その絶対パスに展開。
- `dest` が相対パスの場合 : リモートの **ターゲットアカウント** ( `--user` ) の HOME からの相対パスとして展開。
  - `--ssh-user` と `--user` が異なる場合でも, 展開先の基点は `--user` の HOMEとなる。
  - `--ssh-user != --user` のときは `dest` 作成や展開に `sudo -n` を用いる場合がある。

### パス区切り文字の扱いについて

パス区切り文字は以下のように取り扱う:

- ローカル, リモートともに, ホスト上のシェルを通してファイル操作を行う際は, OS が規定する区切り文字 ( Windows は `\`, Unix 系は `/` など ) を使用して実施する。

- SFTP 経路で操作するリモートパス ( SFTP プロトコル上のパス)は, Paramiko の SFTPClientやWindows 上の OpenSSH/SFTP サーバの仕様に合わせて, '/'を使用する。

- 正規表現評価の際の相対名の内部表現は, パス区切り文字を`/` 区切りに変換して扱う ( 後述 ) 。

---

### ローカル選定 ( パターン ) と正規化

#### パターン指定

- `--pattern-abs` : ローカルの絶対パス ( 正規化後の文字列 ) に対して指定された正規表現にマッチするファイルを転送対象ファイルとして指定する。
- `--pattern-rel` : 各 `--root` からの相対パスに対して 指定された正規表現にマッチするファイルを転送対象ファイルとして指定する。この際, 評価時のみ パス区切り文字を`/` へ変換して判定。
- `--ignore-case` : 正規表現を `re.IGNORECASE` でコンパイル。
- `--root` : ローカルファイルを探索する際の起点となるルートディレクトリを指定する。デフォルトはカレントディレクトリ。複数指定することが可能。

なお, パターンによるファイル選択処理では, ファイルのみを選択対象とし, ディレクトリは選択対象としない。ディレクトリを選択する場合は, 位置引数`src`で明示して選択する。

Windows 環境では, 絶対パスはドライブレターを含む形式(例 : `C:\dir\file` )をそのまま絶対パスとして解釈する。正規表現評価時の相対パス名への変換では, パス区切り文字を `/` に変換するが, ドライブレターは保持する(例 : `C:/dir/file` )。

#### 正規化の定義

ローカルファイルの探索時に, パスの正規化を行う。
パスの正規化は以下のように実施する:

- 絶対化 : 入力が相対なら `cwd` ( カレントディレクトリ ) 起点で絶対パスを生成することでパスを正規化する。
- 冗長要素の畳み込み : 重複セパレータの縮約, `.` 除去, `..` の折りたたみ ( `--root`で指定されたディレクトリより上位のディレクトリ中のファイルは不正ファイルとみなし, 転送の対象としない ) 。
- シンボリックリンクのリンク先となるファイル (実体ファイル)への追従は, `--follow-symlinks`オプションに従って行い, ファイル探索時には, シンボリックリンクファイルも単なる文字列として取り扱う。

#### 送信用相対名の安全性保持

生成した相対名は,

  1) 先頭セパレータで始まらない,
  2) 正規化後に `..` による上位逸脱を含まない,

  を満たす場合にのみ, 転送対象ファイル, ディレクトリとして扱う。

  上記を満たさない場合は Errorメッセージを出力し, そのファイル, ディレクトリの転送をスキップする。

#### 送信用相対名の生成方式

- 相対パスによる `src`指定の場合 : `cwd` から絶対化  =>  正規化  =>  `rel = abs(src) 相対 cwd`  =>  リモートでは `dest/rel` に展開。
- 絶対パスによる `src`指定の場合 : 正規化  =>  先頭のルートセパレータを 1 個だけ落としたパス (`rel` と記載)を生成  =>  `dest/rel` に展開。
- パターン選定: 各 `--root` からの相対 ( 正規化済み ) を `rel` として用いる。
- 同一ローカル絶対パスに複数の相対候補が出た場合は, 最初に検出したファイルを転送対象とし, 警告メッセージを出す。

---

### 転送方式

#### 逐次 SFTP ( デフォルト )

- 必要に応じて `dest` を `mkdir -p` ( `--ssh-user != --user` の場合は `sudo -n` を付与 ) 。
- 送信用相対名チェックに合格したファイルを 1 件ずつ `dest/<rel>` に SFTP put。
- `--preserve-*` オプションは 無効となる ( 意味を持たない ) 。
- `--include-empty-dirs`は, SFTPモードでの空ディレクトリの扱いを指定する。 `--include-empty-dirs`オプション未指定時 ( false )時は, 空ディレクトリを転送しない。`--include-empty-dirs`オプションを指定した場合は, 空ディレクトリを転送する。


SFTP 転送では, 所有者 / グループ / ACL / xattr は保持されない。更新時刻(mtime)は SFTP 実装上, アップロード完了時刻になることがある ( ローカルファイルの更新時刻保持は保証しない ) 。

#### 一括アーカイブ配布 ( `--pack` )

- ローカルで `tar.gz` を作成。
- SFTP でアップロード  =>  リモートで `dest` に展開。
- デフォルトでは, メタデータ(所有権, 権限, ACL, xattr)を保持してファイルを展開する。

`--pack`指定時は,ローカル / リモート双方で一時的にアーカイブサイズ分の空き容量を要する。途中失敗時も一時ファイルは原則削除するが, 障害時に残存した場合は自動削除を再試行し, 不可の場合は警告メッセージを出力する。

#### `--follow-symlinks`がfalseの場合のシンボリックリンクの扱いについて

`--follow-symlinks`がfalseの場合, シンボリックリンクを以下のように扱う。

- 逐次 SFTP動作時: SFTP では通常 symlink の作成は行えないため, リンクはスキップし, 警告メッセージを出力して, 対象のファイルパス, ディレクトリパスの転送を中断し, 処理の継続を試みる。
- 一括アーカイブ配布 ( `--pack` )動作時 : シンボリックリンクファイル はリンクエントリとしてアーカイブ内に格納する。

---

### `--pack`時の所有権, 権限, ACL, xattrの扱いについて

`--pack`指定時の所有権, 権限, ACL, xattrに関するオプションのデフォルト値は以下のようになる:

- `--preserve-perms=true` ( `tar -p` 相当。)
- `--preserve-owner=true` ( 権限が許せば所有権復元 )
- `--preserve-acls=true` ( tar / FS が対応時 )
- `--preserve-xattrs=true` ( tar / FS が対応時。`security.selinux` (SELinuxのラベル)を含む)

`--pack` 指定がない場合は, 上記の `--preserve-*` は すべて false となり, 警告メッセージのみが表示される。SFTP ではこれらの設定値を復元することができないためである。

リモートホストがSELinux非対応である場合や権限不足による権限などの復元が行えない場合, ユーザが該当 `--preserve-*` を 明示的に trueにしている場合は,Errorメッセージを表示して処理を継続する。

`--preserve-*` がfalse, または, 未指定の場合は 警告メッセージを表示し, 展開処理の続行を試みる。

---

### SELinux の扱い ( ホスト単位で自動判定 )

#### SELinux対応可能ホストの判定

以下の両方を満たす場合, 「SELinux対応可能」と判断する:

- `test -d /sys/fs/selinux` または `mount | grep selinuxfs` が真
- `command -v restorecon` が成功

#### `--selinux` の挙動

- `auto` ( デフォルト値 )  : SELinux対応可能なホストの場合, `restorecon -RF dest` を実行する。非対応なら何もしない ( 情報メッセージを表示するのみ ) 。
- `policy` : SELinux対応可能なホストで*ない*場合, エラーメッセージを出して処理を中断する。SELinux対応可能なホストの場合, `restorecon -RF dest` を実行する。
- `archive` : `--pack` かつ `--preserve-xattrs=true` の場合, かつ, tar / FS が xattr 書込み非対応ならエラーメッセージを出して処理を中断する。 tar / FS が xattr 書込みに対応している場合は, アーカイブの xattr を復元し, さらにSELinux対応可能なホストの場合は,  `restorecon -RF dest` も実行する。
- `ignore` : SELinux 関連の処理を行わない。

restorecon -RF dest は dest 配下を再帰的に走査・再ラベリングするため, 配布規模によっては時間を要する。必要に応じて dest を細分化する運用を推奨する。

---

### xattr 復元処理実行条件, `restorecon` 実行条件について

#### xattr を復元する条件 ( `--pack` 時 )

次のすべてを満たす場合に, xattr を復元する :

1) `--pack` が指定されている。
2) `--preserve-xattrs=true` ( 既定で true ) 。
3) 転送元のファイルに xattr 属性が付いており, かつ, その属性をリモートホストで復元する場合, または, 作成したアーカイブ内に, xattr 属性が付いているファイルが含まれている。
4) リモートの tar / FS / マウントオプションが xattr 書込みに対応。
5) 権限が十分 ( 不足時は Warning または明示 true なら エラーメッセージを出して処理を継続 ) 。

逐次SFTPでは, xattr を転送できないため, xattr の保持,復元は行わない。**xattrの保持, 復元が必要な場合は, --packを指定すること**。

#### `restorecon` を実行する条件

以下の いずれかに該当し, かつ SELinux対応可能ホストの場合にのみ`restorecon` を実行する:

- `--selinux=auto` : SELinux対応可能ホストの場合, 常に実行 。
- `--selinux=policy` : SELinux対応可能ホストの場合, 常に実行する。SELinux対応不可能なホストの場合,エラーメッセージを出して処理を中断する。
- `--selinux=archive` : xattr 復元の有無にかかわらず, SELinux対応可能ホストの場合で, かつ, tar/FSが対応可能 ( 例: GNU tar 1.27以降相当) なら追加で実行する ( ポリシーと実体の整合を保証 ) 。

実質, restorecon は SELinux 対応ホストなら常に`restorecon`を実行する動作となる。

#### ツール利用可能性の事前検査処理について

ACL, xattrの復元処理に必要なコマンドの利用可否を事前に確認する処理を以下の通り実施する。

- `--preserve-acls` が有効な場合, リモートホストで`setfacl` が利用可能であることを確認し, 利用不可の場合は, 警告メッセージを出力して, ACLの復元処理をスキップします。
- `--preserve-xattrs` が有効な場合, リモートホストで`setfattr`が利用可能であることを確認し, 利用不可の場合は, 警告メッセージを出力して, xattrの復元処理をスキップします。
- `--preserve-owner`, `--preserve-acl`,  `--preserve-xattrs`, `--preserve-perms`のいずれか1つ以上がtrueの場合, 外部tarコマンドが利用可能であることを転送処理開始前に確認し, 利用不可の場合は, アーカイブの作成, および, ファイル転送を一切行うことなく, エラーメッセージを出力して, プログラム全体を終了する。

##### xattr 復元処理実行条件, `restorecon` 実行条件仕様策定の背景

- アーカイブ / SFTP いずれの配布方式でも, **リモート FS の SELinux ポリシーに基づく最終ラベル**が, リモートのセキュリティポリシーに準じた動作となることから望ましい。
- xattr を復元しても (`security.selinux` をローカルホストの設定値に合わせて復元しても ) , リモートホストの現行ポリシーと不整合な場合があるため, `restorecon` による再ラベリングで整合を担保するほうが望ましい。
- SFTP で上書きした場合も コンテキスト変化が起こり得るため, 対応可能ホストでは `restorecon` を実行する方がセキュリティポリシーに矛盾しないことからより望ましい。

---

### 並列実行と重複抑止

- `--parallel` ( 既定 4 ) でホスト単位の並列実行。
- 転送対象ファイルを指定されたファイル / ディレクトリパスの和集合を生成することで重複転送を抑止する。本処理は, ローカルでの絶対パスで一意化することで実現する。スキャン順は, 以下の通り:

1. srcで明示的に指定されたファイルパス (引数順)
2. --root オプションで指定された各基点ディレクトリに対して, pythonのos.walkによって検出された順(ディレクトリエントリの辞書順)に従って処理する。
3. --root オプションが複数指定された場合は, オプションの出現順序に従って処理する。

以上の手順で検出したファイルパスの内, 先行検出を採用し, 以降重複するパスを検出した場合は警告メッセージを出力して処理を継続する。

---

### パス・トラバーサルの拒否

- 各エントリの「送信用相対パス名」をOS 規定の区切りで正規化 ( `//` 縮約, `.` 除去, `..` 折畳み ) 。
- 上記の結果が
  1) 先頭セパレータで始まる, または,
  2) 正規化後に `..` により`dest`ディレクトリより上位のディレクトリへの逸脱が発生する
  のいずれかに該当する場合, `dest` より上位へは絶対にファイルを出力させないようにエラーメッセージを出して対象ファイル,ディレクトリの転送を中断し, 処理の継続を試みる。

---

## ホストファイル形式

本ツールのホストファイル形式は以下の通り:

- 1 行につき, 1 ホストを記載する。
- 空行, `#`で開始する行, および, タブ または, 空白 に続く `#` 以降はコメントとして扱う。

---

## 運用上の注意

- タイムアウトの設定値: `--timeout` は SSH コマンド単位の上限です。大量配布や大容量アーカイブではタイムアウトに達しやすいため, `--pack` の利用や値の引き上げを検討してください。
- 一時領域の確保: `--pack` 時はローカル・各リモートの双方で アーカイブ分の一時ディスク容量が必要です。転送サイズと展開後サイズを考慮して, ストレージの一時領域を確保してください。
- 差分配布: 作業時間短縮の観点から, 頻繁に配布する場合は, 変更検出 ( mtime/ハッシュ ) に基づく変更ファイルのみを転送対象とする, ディレクトリ単位で分割配布するなどの方式を推奨します。

---

## 補足事項

### アーカイブ作成側のtarの扱いについて

GNU tar の場合は --acls/--xattrs を作成時にも付けて格納する。bsdtar / Libarchive 系では OS / バージョンにより挙動差があるため, ACL/xattr の格納は実装依存である。

### ディレクトリ型シンボリックリンクについて

ディレクトリ型シンボリックリンクは, 転送方式に応じて以下のように処理される。

- 逐次 SFTP動作時: シンボリックリンクファイルの作成は行わず, 必要に応じて, シンボリックリンクの参照先(実体)を転送します。
  - `--follow-symlinks`無指定時 ( False, デフォルト動作 ): そのディレクトリSymlink自体の処理をスキップします(警告を出してシンボリックリンクをたどらない)。

  - `--follow-symlinks`指定時 ( True ) : リンクを辿って中身の実体 ( 通常ファイル ) を列挙・転送します。SFTPはシンボリックリンクそのものは作れないため, リンクとしては再現されず, 辿った先の実体が通常のファイル/ディレクトリとしてdest配下にコピーされます。壊れたリンク ( 参照先のないリンク ) があった場合は, 警告メッセージを出力して, 対象のシンボリックリンクの処理をスキップします。
- `--pack`オプション指定時: シンボリックリンクファイルをリモートホスト上に作成(再現)します。ディレクトリ型シンボリックリンクも作成(ディレクトリへのシンボリックリンクとして作成)します。壊れたリンク ( リモート側で参照先のないリンク ) の場合も, シンボリックリンクを作成します。


### Windows のドライブレターを含むパスの扱いについて

`--pattern-rel` は 相対名に対して適用されます。Windows 上でローカル探索を行う場合, 相対名にドライブレター ( `C:` 等 ) を含めません。ドライブレターを扱う必要がある場合は `--pattern-abs` を用いるか, `--root` にドライブのルート ( 例: `C:\\` ) を指定し, 相対名はドライブレターを除いた形 ( 例: `a/b.txt` ) で扱ってください。

---

## 使用例

### 基本的な使用法

#### 明示的な src 群を指定して、各ホストの ~/deploy へ配布 ( SFTP, 逐次 put )

```:shell
gm-scatter.py -H hostfile -u appuser src/app.conf src/start.sh deploy
```

src は 0 個以上のファイル/ディレクトリ。最後の位置引数が `dest` ( 必須 ) 。

dest が相対の場合は、`--user` の HOME 配下に展開される。
この例では `~appuser/deploy`配下に展開される。

#### ディレクトリをそのまま配布 ( SFTP, 逐次 put )

```:shell
gm-scatter.py -H hostfile -u appuser config/ webroot/ deploy
```

config/ と webroot/ の中身を走査し、個々のファイルを SFTP で ~/deploy に作成。

デフォルトではシンボリックリンクはたどらない ( リンク自体の転送も除外 ) 。シンボリックリンクをたどる場合は, `--follow-symlinks`をつける。

#### 転送は行わず計画のみを表示する

```:shell
gm-scatter.py -H hostfile -u appuser -n config/ webroot/ deploy
```

転送・作成は実施せず, 台数や件数などの計画だけを表示。
実配布前の検証に有効。

#### パターン選択モード ( ローカル探索 )

##### 指定したディレクトリ(root)を起点とした正規表現にマッチしたファイルを選択 ( 絶対パスパターン --pattern-abs )

```:shell
gm-scatter.py -H hostfile -u appuser \
  -R /srv/app -a '.+\.service$' -a '/srv/app/config/.*\.yaml$' \
  deploy
```

`-R /srv/app`により, `/srv/app`を探索の基点に設定。

`/srv/app`配下で".service"および"config/*.yaml"に一致したファイルのみ選択して転送する。絶対パスに対してファイルのマッチング判定が行われる ( --pattern-abs ) 。

##### 指定したディレクトリ(root)を起点とした正規表現にマッチしたファイルを選択 ( 相対パスパターン --pattern-rel )

```:shell
gm-scatter.py -H hostfile -u appuser \
  -R /srv/app -r '^config/[^/]+\.yaml$' -r '^bin/[^/]+$' \
  deploy
```

`-R /srv/app`により, `/srv/app`を探索の基点に設定。
`/srv/app`からの相対パス ( config/..., bin/... 等 ) に対して正規表現を適用。

--pattern-abs と --pattern-rel は併用可。どちらか一方に一致すれば選定される。

#### 大文字小文字を無視してパターン選択を実施

```:shell
gm-scatter.py -H hostfile -u appuser -i -R /srv/app -r '^readme(\.md)?$' deploy
```

-i で IGNORECASE を有効化し, 正規表現マッチ時に大文字小文字の区別を行わずにマッチしたものを選択する。

#### アーカイブ一括配布モード ( --pack )

##### 転送対象ファイルをアーカイブ化しリモートホストで展開

```:shell
gm-scatter.py -H hostfile -u appuser --pack \
  src_dir/ extra/file1.txt deploy
```

`--pack` 指定時は `--no-preserve-perms/--no-preserve-owner/--no-preserve-acls/--no-preserve-xattrs` が未指定なら既定で有効になる ( True ) 。

ローカルホスト上に `tar` コマンドが必要 ( 見つからなければエラー終了 ) 。

##### `--pack`指定転送を行いつつ, パーミッション等は保持しない

```:shell
gm-scatter.py -H hostfile -u appuser --pack \
  --no-preserve-perms --no-preserve-owner --no-preserve-acls --no-preserve-xattrs \
  src_dir/ deploy
```

#### SELinux コンテキストの復元

##### 自動でrestorecon 実行

```:shell
gm-scatter.py -H hostfile -u appuser --pack --selinux auto src_dir/ deploy
```

##### 常にポリシー適用 ( restorecon 必須。無ければエラー )

```:shell
gm-scatter.py -H hostfile -u appuser --pack --selinux policy src_dir/ deploy
```

##### アーカイブに xattrs を保持し、展開後に xattrs 復元 ( GNU tar + setfattr 必須 )

```:shell
gm-scatter.py -H hostfile -u appuser --pack --preserve-xattrs --selinux archive src_dir/ deploy
```

`--selinux archive` を指定するためには `--pack` と `--preserve-xattrs` の両方のオプションをつける必要がある。また, リモートホストに `setfattr`コマンド が必要。

リモートホスト側のtarコマンドが GNU tar でない場合、--preserve-* の一部は無効化される可能性があり, 警告またはエラー ( `--preserve-*`オプションを明示指定した場合 ) として集計出力。


#### 並列度・タイムアウト・SSH 認証の指定

```:shell
gm-scatter.py -H hostfile -u appuser \
  -j 16 -T 45 -P 2222 -K ~/.ssh/id_ed25519 -S \
  src/ deploy
```

- `-j` でホスト並列数、-T で SSH/コマンドタイムアウト秒。

- `-P` でポート、`-K` で秘密鍵ファイル、`-S` で厳格なホスト鍵検証を有効化。

- `--ssh-user` を併用すると「SSH ログインユーザー」と「リモート上のターゲットアカウント ( `--user` ) 」を分離できる ( `sudo -n` を使用した復元作業などに使用 ) 。

```:shell
gm-scatter.py -H hostfile --ssh-user deployer -u appuser src/ deploy
```
「SSH ログインユーザー」と「リモート上のターゲットアカウント ( `--user` ) 」を分離し, deployerでsshログインし, `appuser`ユーザでファイルの展開・復元を行う。


#### SFTP モードで空ディレクトリも作成する

```:shell
gm-scatter.py -H hostfile -u appuser --include-empty-dirs config-dir/ deploy
```

`--pack` なしのときのみ有効。config-dir/ 直下で空のディレクトリもリモートに作成。

`--pack` の場合はアーカイブ展開により, 空ディレクトリも含まれる。

#### シンボリックリンクの追随

```:shell
gm-scatter.py -H hostfile -u appuser --follow-symlinks project-root/ deploy
```

SFTP モードで、リンク先をたどってファイルを転送したい場合に使用。
デフォルトでは、リンクは安全側でスキップ ( 壊れたリンクは常にスキップ ) 。

## hostfile の例

コメントや空行を含むホストファイルの例を以下に示す:

```:text
# 1 行につき1 ホストを記載。空行可。
# 行中の「タブ, または, 空白に続く #」以降はコメントとみなされる。

srv-a.example.com
srv-b.example.com   # blue rack
10.0.0.15
```

`-H` 未指定時のデフォルトファイル名は, `hostfile`になる。

---

## ログ分類

- **Info** : 実行計画 ( ホスト数 / モード / `dest` / 選定件数 / 並列度 ) , 各ホストの進捗 ( DRY-RUN / packed / uploaded 件数, SELinux 実施など ) 。
- **Warning ( 継続 ) ** : `--preserve-*` の非対応降格, `--selinux=auto` での非対応降格, xattr 書込み不可による部分無視等。**最後に**件数・ホスト数・詳細一覧を集計表示。
- **Error ( 継続 ) ** : 個別ファイル失敗, 相対名の不正 ( 先頭セパレータ / `..` 逸脱 ) , 権限不足など。**最後に**件数・ホスト数・詳細一覧を集計。
- **Error ( 致命 ) ** : 無効引数, `dest` 欠落, hostfile 空, 正規表現構文エラー, `paramiko` 不在, `--selinux=policy` / `archive` の必須条件欠落など  =>  **全体停止**。

## 依存パッケージ

本ツールの動作には以下のパッケージが必要となる:

- Paramiko ( OS パッケージ `python3-paramiko` または `pip install paramiko` )
