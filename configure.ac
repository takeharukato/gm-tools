AC_PREREQ([2.69])
AC_INIT([gm-tools], [0.1.0], [tkato1219@gmail.com])
AM_INIT_AUTOMAKE([foreign dist-xz no-dist-gzip subdir-objects])
AC_CONFIG_SRCDIR([src/gm-gather.py])
dnl AC_CONFIG_HEADERS([config.h])

AC_PROG_INSTALL
AM_PATH_PYTHON([3.8])  dnl 必要に応じて下限を調整

dnl ---- pkg-config (bash-completion 検出に利用) ----
PKG_PROG_PKG_CONFIG

dnl ---- ユーザー指定を受け付ける ----
AC_ARG_WITH([bash-completion-dir],
  [AS_HELP_STRING([--with-bash-completion-dir=DIR],
                  [install path for bash completions (override auto-detect)])],
  [BASH_COMPLETION_DIR="$withval"],
  [BASH_COMPLETION_DIR=""])

AC_ARG_WITH([zsh-completion-dir],
  [AS_HELP_STRING([--with-zsh-completion-dir=DIR],
                  [install path for zsh completions (override auto-detect)])],
  [ZSH_COMPLETION_DIR="$withval"],
  [ZSH_COMPLETION_DIR=""])

dnl ---- bash-completion の自動検出 ----
AS_IF([test -z "$BASH_COMPLETION_DIR"], [
  AS_IF([test -n "$PKG_CONFIG" && $PKG_CONFIG --exists bash-completion], [
    BASH_COMPLETION_DIR=`$PKG_CONFIG --variable=completionsdir bash-completion`
  ], [
    dnl フォールバック（ディストリ一般）
    for d in \
      /usr/share/bash-completion/completions \
      /etc/bash_completion.d \
      "${datarootdir}/bash-completion/completions"
    do
      test -d "$d" && BASH_COMPLETION_DIR="$d" && break
    done
    test -z "$BASH_COMPLETION_DIR" && BASH_COMPLETION_DIR='${datarootdir}/bash-completion/completions'
  ])
])

dnl ---- zsh completion の自動検出 ----
AS_IF([test -z "$ZSH_COMPLETION_DIR"], [
  for d in \
    /usr/share/zsh/site-functions \
    /usr/local/share/zsh/site-functions \
    /usr/share/zsh/vendor-completions \
    "${datarootdir}/zsh/site-functions"
  do
    test -d "$d" && ZSH_COMPLETION_DIR="$d" && break
  done
  test -z "$ZSH_COMPLETION_DIR" && ZSH_COMPLETION_DIR='${datarootdir}/zsh/site-functions'
])

AC_SUBST([BASH_COMPLETION_DIR])
AC_SUBST([ZSH_COMPLETION_DIR])

AC_CONFIG_FILES([
  Makefile
  src/Makefile
  compl/Makefile
  docs/Makefile
  scripts/Makefile
])
AC_OUTPUT
