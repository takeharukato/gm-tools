## -*- makefile -*-
## compl/Makefile.am for gm-tools

## bash / zsh 補完スクリプトのインストール先
bashcompletiondir = @BASH_COMPLETION_DIR@
zshcompletiondir  = @ZSH_COMPLETION_DIR@

## インストールする補完スクリプト
bashcompletion_DATA = \
	gm-gather.bash \
	gm-scatter.bash

zshcompletion_DATA = \
	_gm-gather.zsh \
	_gm-scatter.zsh

## 配布アーカイブに含めるファイル
EXTRA_DIST = \
	$(bashcompletion_DATA) \
	$(zshcompletion_DATA)
