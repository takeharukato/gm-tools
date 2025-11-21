# gm-tools

## 概要

gm-tools は複数ホスト間でのファイル収集・配布を支援する `gm-gather` と `gm-scatter` を提供するツールセットです。SSH 経由での転送や圧縮アーカイブを転送することによる複数ファイルの一括転送や配布ファイルのメタ情報を自動復元する機能などを備えています。

使用方法の詳細は, [gm-gather 仕様](docs/gm-gather-spec.md), および, [gm-scatter 仕様](docs/gm-scatter-spec.md)を参照ください。

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

## 著作権表記

Copyright 2025 Takeharu KATO.

本プロジェクトは BSD 2-Clause ライセンスの下で配布されています。
詳細は `LICENSE` ファイルを参照してください。
