# gm-tools

## 概要

gm-tools は複数ホスト間でのファイル収集・配布を支援する `gm-gather` と `gm-scatter` を提供するツールセットです。SSH 経由での転送や圧縮アーカイブを転送することによる複数ファイルの一括転送や配布ファイルのメタ情報を自動復元する機能などを備えています。

使用方法の詳細は, [gm-gather 仕様](docs/gm-gather-specJP.md), および, [gm-scatter 仕様](docs/gm-scatter-specJP.md)を参照ください。

## 前提パッケージ

- Python 3.9 以降
- [python3-paramiko](https://www.paramiko.org/)
- gettext-0.21 以降
- autoconf-2.69 以降
- automake-1.16 以降
- acl-2.2 以降 (Access Control Listの復元機能を使用する場合)
- attr-2.4 以降  (拡張属性の復元機能を使用する場合)
- sudo-1.8 以降
- policycoreutils (SE Linuxのコンテキスト情報の復元機能を使用する場合, `restorecon`コマンドで, `-RF` オプションが使用可能な版数が必要です)

その他, bash, mktempコマンド (`-d`オプションによるテンプレート指定付きディレクトリ作成機能が必要)が必要です。標準的なLinuxディストリビューション, BSD系OSであれば利用可能と考えます。

## インストール

以下の手順でインストールすることが可能です。

```:shell
./autogen.sh
./configure
make
make install
```

## make ターゲット

- `make` : コアモジュールおよびスクリプトのビルド
- `make install` : 実行ファイル・モジュール・man ページをシステムへ導入
- `make check` : 主要テストスイートの実行
- `make dist` : 配布用 tarball の生成
- `make clean` : 生成物の削除
- `make cloc` : ソース規模の出力 (`cloc`コマンドが必要です)
- `make rpm`: rpmパッケージの生成 (`docker`コマンドが必要です)
- `make deb`: debianパッケージの生成 (`docker`コマンドが必要です)

`docs/sphinx`ディレクトリで, `make docs`コマンドを実行することで, テストフレームワークと本プログラムのインターフェース仕様が生成されます(Sphinxパッケージが必要です)。

`po`ディレクトリでは, 以下のmake ターゲットが定義されています。

- `make update-po` POT ファイル (`gm-tools.pot`) の再生成と各言語 PO ファイル (`*.po`) へのマージ, さらに `.gmo` ( バイナリ辞書 ) を更新します。

- `make gm-tools.pot-update` POT ファイルだけを再生成します ( make update-po 内部でも呼び出されます ) 。
- `make update-gmo` 既存の PO から .gmo を作り直します。PO を手で編集した後に mo ファイルだけ再生成したい場合に使えます。
- `make` または, `make all` stamp-po と .gmo を整備します ( 通常ビルド時に呼ばれます ) 。
- `make install` 翻訳済み `.mo`ファイル を導入します。
- `make uninstall` 翻訳済み `.mo`ファイル を削除します。
- `make clean`, `make distclean`, `make maintainer-clean` 生成物 (`*.gmo`, `stamp-po` など) をクリンナップレベルに応じて削除します。

## 使用例

### ホストファイルの作成

本例では, `localhost`, `vmlinux.local`の双方を操作対象リモートホストに設定します。本例では, 各リモートホストに, カレントユーザと同じ名前で, パスワード無しにsshログイン可能であることを前提とします。カレントユーザのユーザ名は, `user`であること, かつ, 同じユーザがリモートホストにも存在することを前提としています。

ホストファイルを`hostfile`という名前で以下のように作成します。

```:plaintext
localhost
vmlinux.local
```

### 正規表現によるファイルの取得の例

リモートホストのホームディレクトリ配下にある`.zsh`で始まるファイルを取得する場合は, 以下のように`gm-gather`を使用します。

```:shell
gm-gather   "~/\.zsh.*" dest
```

実行例は以下のようになります。

```:shell
$ gm-gather   "~/\.zsh.*" dest
timestamp="2025-11-23T01:14:33.209+09:00" level="INFO" host="localhost" op="gather" phase="start" trial="0" processed="0" total="4" msg="host start"
timestamp="2025-11-23T01:14:33.210+09:00" level="INFO" host="vmlinux.local" op="gather" phase="start" trial="0" processed="0" total="4" msg="host start"
timestamp="2025-11-23T01:14:33.214+09:00" level="DEBUG" host="localhost" op="gather" phase="processing" trial="1" processed="1" total="4" seq="1" msg="processing"
timestamp="2025-11-23T01:14:33.214+09:00" level="DEBUG" host="vmlinux.local" op="gather" phase="processing" trial="1" processed="1" total="4" seq="1" msg="processing"
timestamp="2025-11-23T01:14:33.218+09:00" level="DEBUG" host="localhost" op="gather" phase="processing" trial="2" processed="2" total="4" seq="2" msg="processing"
timestamp="2025-11-23T01:14:33.218+09:00" level="DEBUG" host="vmlinux.local" op="gather" phase="processing" trial="2" processed="2" total="4" seq="2" msg="processing"
timestamp="2025-11-23T01:14:33.221+09:00" level="DEBUG" host="localhost" op="gather" phase="processing" trial="3" processed="3" total="4" seq="3" msg="processing"
timestamp="2025-11-23T01:14:33.222+09:00" level="DEBUG" host="vmlinux.local" op="gather" phase="processing" trial="3" processed="3" total="4" seq="3" msg="processing"
timestamp="2025-11-23T01:14:33.225+09:00" level="DEBUG" host="localhost" op="gather" phase="processing" trial="4" processed="4" total="4" seq="4" msg="processing"
timestamp="2025-11-23T01:14:33.225+09:00" level="INFO" host="localhost" op="gather" phase="done" trial="4" processed="4" total="4" warnings="0" errors="0" duration="0.0" msg="host done"
timestamp="2025-11-23T01:14:33.225+09:00" level="DEBUG" host="vmlinux.local" op="gather" phase="processing" trial="4" processed="4" total="4" seq="4" msg="processing"
timestamp="2025-11-23T01:14:33.225+09:00" level="INFO" host="vmlinux.local" op="gather" phase="done" trial="4" processed="4" total="4" warnings="0" errors="0" duration="0.0" msg="host done"
timestamp="2025-11-23T01:14:33.226+09:00" level="INFO" host="-" op="gather" phase="done" trial="8" processed="8" total="8" warnings="0" errors="0" msg="summary"
$ tree -a dest
dest
|-- localhost
|   `-- home
|       `-- user
|           |-- .zshrc
|           |-- .zshrc.lmod
|           |-- .zshrc.mine
|           `-- .zshrc.proxy
`-- vmlinux.local
    `-- home
        `-- user
            |-- .zshrc
            |-- .zshrc.lmod
            |-- .zshrc.mine
            `-- .zshrc.proxy

6 directories, 8 files
```

### 正規表現によるファイルの配布の例

ローカルホストのカレントディレクトリ配下にある`host`で始まるファイルをリモートホストのホームディレクトリ直下の`dest-scatter`というディレクトリに展開する場合は, 以下のように`gm-scatter`を使用します。

```:shell
gm-scatter './host.*' ~/dest-scatter
```

カレントディレクトリに, `host`で始まるファイルとして, `hostfile`だけが存在する場合, 実行例は以下のようになります。

```:shell
$ gm-scatter './host.*' ~/dest-scatter
$ tree -a dest-scatter
dest-scatter
`-- home
    `-- user
        `-- hostfile
```

## 著作権表記

Copyright 2025 Takeharu KATO.

本プロジェクトは BSD 2-Clause ライセンスの下で配布されています。
詳細は `LICENSE` ファイルを参照してください。
