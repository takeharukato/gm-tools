dnl -*- Autoconf -*-
dnl configure.ac for gm-tools

AC_PREREQ([2.69])
AC_INIT([gm-tools], [0.1.0], [bugs@example.invalid])

dnl このファイルが存在することでソースツリーであることを確認
AC_CONFIG_SRCDIR([src/gm_tools/gather_cli.py])

dnl 補助スクリプト(config.guess等)のディレクトリ
AC_CONFIG_AUX_DIR([build-aux])

dnl m4 マクロディレクトリ（gettext等が追加される想定）
AC_CONFIG_MACRO_DIR([m4])

dnl Automake 初期化
dnl - foreign: GNU strict にはしない
AM_INIT_AUTOMAKE([foreign])

dnl C コンパイラは使わないが、gettext マクロが期待することもあるので念のため
AC_PROG_CC

dnl Python 検出 (最低 3.10 程度を想定)
AM_PATH_PYTHON([3.10])

dnl ----------------------------------------
dnl Gettext / po/ の統合
dnl ----------------------------------------

dnl gettext を外部ライブラリとして利用
AM_GNU_GETTEXT([external])

dnl 最小 gettext バージョン
AM_GNU_GETTEXT_VERSION([0.21])

dnl gettext パッケージ名 (po/Makevars の DOMAIN とも一致させる)
AC_SUBST([GETTEXT_PACKAGE], [gm-tools])

dnl ----------------------------------------
dnl 補完スクリプトのインストール先
dnl ----------------------------------------

dnl bash completion のインストール先 (--with-bash-completion-dir=DIR で上書き可能)
AC_ARG_WITH([bash-completion-dir],
  [AS_HELP_STRING([--with-bash-completion-dir=DIR],
                  [directory for bash completion scripts])],
  [bash_completion_dir="$withval"],
  [bash_completion_dir=""])

if test "x$bash_completion_dir" = "x"; then
  dnl デフォルト値: /usr/share/bash-completion/completions 相当
  bash_completion_dir='${datarootdir}/bash-completion/completions'
fi
AC_SUBST([BASH_COMPLETION_DIR], [$bash_completion_dir])

dnl zsh completion のインストール先 (--with-zsh-completion-dir=DIR で上書き可能)
AC_ARG_WITH([zsh-completion-dir],
  [AS_HELP_STRING([--with-zsh-completion-dir=DIR],
                  [directory for zsh completion scripts])],
  [zsh_completion_dir="$withval"],
  [zsh_completion_dir=""])

if test "x$zsh_completion_dir" = "x"; then
  dnl デフォルト値: /usr/share/zsh/site-functions 相当
  zsh_completion_dir='${datarootdir}/zsh/site-functions'
fi
AC_SUBST([ZSH_COMPLETION_DIR], [$zsh_completion_dir])

dnl ----------------------------------------
dnl 開発支援ツールの検出 (cloc, sphinx-build)
dnl ----------------------------------------

dnl cloc: 無ければ ":" (no-op) にして make cloc を no-op にする
AC_PATH_PROG([CLOC], [cloc], [:])

dnl Sphinx: 無ければ ":" にして make docs を no-op にする
AC_PATH_PROG([SPHINXBUILD], [sphinx-build], [:])

dnl ----------------------------------------
dnl 生成するファイルの一覧
dnl ----------------------------------------

AC_CONFIG_FILES([
  Makefile
  src/Makefile
  compl/Makefile
  config/Makefile
  Dockerfiles/Makefile
  docs/Makefile
  tests/Makefile
  debug-patches/Makefile
  scripts/Makefile
  src/gm_tools/_config.py
])

AC_OUTPUT
