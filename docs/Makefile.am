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
## docs/Makefile.am for gm-tools

## ドキュメント本体は docdir 直下に配置
doc_DATA = \
	gm-gather-spec.md \
	gm-scatter-spec.md \
	common-spec.md

dist_man_MANS = \
	man/gm-gather.1 \
	man/gm-scatter.1

## 配布アーカイブに含めるファイル
EXTRA_DIST = \
	$(doc_DATA) \
	sphinx/conf.py \
	sphinx/_static/README \
	sphinx/_templates/README \
	sphinx/index.rst \
	sphinx/tst_framework/index.rst \
	sphinx/tst_framework/types_constants.rst \
	sphinx/tst_framework/low_level_interfaces.rst \
	sphinx/tst_framework/test_common_cleanup.rst \
	sphinx/tst_framework/test_common_config.rst \
	sphinx/tst_framework/test_common_hosts.rst \
	sphinx/tst_framework/test_common_json.rst \
	sphinx/tst_framework/test_common_local.rst \
	sphinx/tst_framework/test_common_logs.rst \
	sphinx/tst_framework/test_common_paths.rst \
	sphinx/tst_framework/test_common_runner.rst \
	sphinx/tst_framework/test_common_snapshot.rst \
	sphinx/tst_framework/test_common_ssh.rst \
	sphinx/design/index.rst \
	sphinx/design/modules/gm_tools.rst \
	sphinx/design/modules/modules.rst \
	$(dist_man_MANS)
