# gm-tools 補完スクリプト概要

このディレクトリには `gm-gather` / `gm-scatter` 向けのシェル補完スクリプトを収録しています。bash と zsh の両方をサポートしており, コマンドライン引数の種類に応じた候補を提示します。

位置引数は 1 個以上のソースと必須のデスティネーションで構成されますが, 補完段階で判別できないため, bash/zsh 双方とも, ファイル名補完を有効化して, ファイルまたはディレクトリ名を補完します。

## bash 用スクリプト

- `gm-gather` / `gm-gather.bash`
  - bash-completion が提供する補完初期化ヘルパー(`_init_completion`関数)を用いて, 現在入力中の単語 (`$cur`) や直前の単語 (`$prev`) をセットアップして補完対象を判別します。
  - オプション引数を必要とする `--hosts/-H`, `--user/-u`, `--ssh-user/-s`, `--port/-P`, `--key/-K`, `--password/-W`, `--timeout/-T`, `--parallel/-j` を検知すると, それぞれに応じた候補を出します。
    - `--hosts/-H`: ファイル補完を有効化し, ホストリストファイル候補を提示します。
    - `--user/-u` と `--ssh-user/-s`: システム上のユーザー名候補を列挙します。
    - `--port/-P`: よく使うポート番号の定型値 (現行スクリプトでは `22`) を提示します。
    - `--key/-K`: ファイル補完を使い, 絶対パスの場合はそのまま候補を, 相対パスの場合は `$HOME/.ssh/` 以下を探索して候補に変換します。
    - `--password/-W`: 補完は行わず, 自由入力を許容します。
    - `--timeout/-T`: 代表的な秒数 (`30 45 60 90 120`) をリスト化します。
    - `--parallel/-j`: 並列実行数として想定される値 (`1 2 3 4 8 16 32 64 128 256`) を候補にします。
  - `--option=value` 形式で入力している場合は等号の右側だけを再補完し, 補完結果を `--option=候補` の形で再構成します。
  - `--help/-h`, `--strict-host-key-checking/-S`, `--pack`, `--follow-symlinks`, `--dry-run/-n`, `--verbose/-v`, `--sudo-collect`, `--no-sudo-collect`, 短縮オプション `-x` (sudo 併用の切り替え) など引数不要のオプションは, ダッシュを入力した段階で候補を一覧提示します。
  - `complete -F _gm_gather` により `gm-gather` / `gm-gather.py` 実行時にこの補完関数が呼び出されます。

- `gm-scatter` / `gm-scatter.bash`
  - `_init_completion` を用いて入力中の単語を解析する点は `gm-gather` と共通です。
  - 引数付きオプションは `--hosts/-H`, `--user/-u`, `--ssh-user/-s`, `--port/-P`, `--key/-K`, `--password/-W`, `--timeout/-T`, `--parallel/-j`, `--selinux` を判別します。
    - `--hosts/-H`, `--user/-u`, `--ssh-user/-s`, `--port/-P`, `--key/-K`, `--password/-W`, `--timeout/-T`, `--parallel/-j` は `gm-gather` と同じ補完ロジックを共有します。
    - `--selinux`: `auto policy ignore` のいずれかを候補として提示します。
  - `--help/-h`, `--strict-host-key-checking/-S`, `--pack`, `--follow-symlinks`, `--dry-run/-n`, `--verbose/-v`, `--sudo-extract`, `--no-sudo-extract`, 短縮の `-x` (sudo 利用切り替え) など引数不要オプションはダッシュ入力時に候補化します。
  - `complete -F _gm_scatter` が登録されることで `gm-scatter` / `gm-scatter.py` 実行時にこの補完が呼び出されます。

これらのファイルを bash-completion ディレクトリに配置すると, `gm-gather` / `gm-scatter` コマンド起動時に自動で補完関数が読み込まれます。リポジトリの `compl/Makefile.am` では, configure 時に指定した bash 補完ディレクトリにこれらのファイルをコピーするターゲットを用意しており, `make install` を実行すると必要な場所へ自動配置されます。

## zsh 用スクリプト

- `_gm-gather` / `_gm-gather.zsh`
  - コマンドと補完関数を関連付ける zsh 組み込み関数(`compdef`)を用いて, `gm-gather` と補完定義を紐付けます。
  - zsh の高機能ヘルパー(`_arguments`)を用いて, オプション仕様を宣言形式で記述し, その内容に沿って補完候補と説明文を生成します。
  - `--help/-h`: 説明文を表示したうえでフラグ候補として提示します。
  - `--strict-host-key-checking/-S`: フラグを候補提示します。
  - `--pack`, `--follow-symlinks`, `--dry-run/-n`, `--verbose/-v`, `--sudo-collect`, `--no-sudo-collect`, `-x`: 引数不要のフラグとして一覧に含まれます。
  - `--hosts/-H`: `_files` (ファイル名を候補化するウィジェット) を呼び出し, ホストリストファイルを補完します。
  - `--user/-u`, `--ssh-user/-s`: `_users` (システムユーザー名を列挙) を呼び出して候補を提示します。
  - `--port/-P`: `22` のみを候補とするポート番号補完を提供します。
  - `--key/-K`: `_files` を利用し, `-W "$HOME/.ssh"` 指定によって `~/.ssh` ディレクトリを優先的に補完候補へ含めます。
  - `--password/-W`: `_guard "^.*$" password` を使い任意文字列を受け入れる形にし, 候補は表示せず自由入力を許容します。
  - `--timeout/-T`: `(30 45 60 90 120)` を候補として提示します。
  - `--parallel/-j`: `(1 2 3 4 8 16 32 64 128 256)` を候補として提示します。
  - 位置引数 (`*:argument:_files`): `_files` を用いてファイルまたはディレクトリ名を補完します。

- `_gm-scatter` / `_gm-scatter.zsh`
  - コマンドと補完関数を関連付ける zsh 組み込み関数(`compdef`)を用いて, `gm-scatter` と補完定義を紐付けます。
  - zsh の高機能ヘルパー(`_arguments`)を用いて, オプション仕様を宣言形式で記述し, その内容に沿って補完候補と説明文を生成します。
  - `--selinux` では `(auto policy ignore)` の 3 候補を表示します。
  - `--help/-h`, `--strict-host-key-checking/-S`, `--pack`, `--follow-symlinks`, `--dry-run/-n`, `--verbose/-v`, `--sudo-extract`, `--no-sudo-extract`, `-x` などのフラグは `_arguments` により候補表示されます。
  - `--hosts/-H`, `--user/-u`, `--ssh-user/-s`, `--port/-P`, `--key/-K`, `--password/-W`, `--timeout/-T`, `--parallel/-j` は `gm-gather` と同じ補完方式を共有します。

zsh では補完定義を検索するパスへファイルを配置し, `autoload -U compinit && compinit` を実行して補完システムを初期化すると (`compinit` は zsh の補完定義を読み込む初期化コマンド), 補完定義の設定が反映されます。リポジトリの `compl/Makefile.am` では, configure 時に指定した zsh 補完ディレクトリにこれらのファイルをコピーするターゲットを用意しており, `make install` を実行すると必要な場所へ自動配置されます。

## 利用手順の目安

1. `./configure` 実行時に `--with-bash-completion-dir` や `--with-zsh-completion-dir` を指定して, 補完スクリプトのインストール先を決めます。
2. `make install` を実行すると, 上記ディレクトリに各補完ファイルが配置されます。
3. システムの補完が有効化済みであれば, 新しいシェルを開くと即座に補完が利用できます。手動で試す場合は `source` で読み込んでも構いません。

補完内容を変更する場合は, それぞれのスクリプト内のオプション配列や `_arguments` の定義を修正してください。
