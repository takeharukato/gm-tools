# -*- mode: makefile; coding: utf-8; line-endings: unix -*-
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2025 TAKEHARU KATO
#
# This file is distributed under the two-clause BSD license.
# For the full text of the license, see the LICENSE file in the project root directory.
# このファイルは2条項BSDライセンスの下で配布されています。
# ライセンス全文はプロジェクト直下の LICENSE を参照してください。
#
# OpenAI's ChatGPT partially generated this code.
# Author has modified some parts.
# OpenAIのChatGPTがこのコードの一部を生成しました。
# 著者が修正している部分があります。
#
## compl/Makefile.am for gm-tools

## bash / zsh 補完スクリプトのインストール先
bashcompletiondir = @BASH_COMPLETION_DIR@
zshcompletiondir  = @ZSH_COMPLETION_DIR@

## ドキュメント (補完スクリプトの説明) のインストール先
doc_DATA = \
	Readme-command-completion.md \
	ReadmeJP-command-completion.md

## インストールする補完スクリプト
bashcompletion_DATA = \
	gm-gather.bash \
	gm-scatter.bash

zshcompletion_DATA = \
	_gm-gather.zsh \
	_gm-scatter.zsh

## 配布アーカイブに含めるファイル
EXTRA_DIST = \
	$(doc_DATA) \
	$(bashcompletion_DATA) \
	$(zshcompletion_DATA)
