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

## 配布に含めたいファイル群
## 必要に応じて追加してください。
EXTRA_DIST = \
    run-test-module-test.sh \
    test_core_signal_handling.py \
    test_path_handling.py \
    test_scatter_pack_cleanup.py \
    test_test_common_cleanup.py \
    test_test_common_config.py \
    test_test_common_ssh.py

## automake の標準 TESTS 機構は使わず, check-local で run_step*.sh を回す
TESTS =

.PHONY: coverage

check-local:
	@echo "No tests found.";


## ----------------------------------------
## make coverage: Python カバレッジ計測
## - coverage モジュールが無ければスキップして正常終了
## - tests_env.sh があれば読み込んでから runner_step*.py を実行
## ----------------------------------------
coverage:
	@echo "Checking for Python coverage module..."
	@if $(PYTHON) -m coverage --version >/dev/null 2>&1; then \
	  echo "coverage module found; running coverage for gm_tools modules"; \
	  if test -f ./tests_env.sh; then \
	    echo "Sourcing ../tests_env.sh"; \
	    . ./tests_env.sh; \
	  fi; \
	  rm -f .coverage; \
	  $(PYTHON) -m coverage report; \
	else \
	  echo "coverage module not available; skipping coverage"; \
	fi

#------------------------------------------------------
# Clean up test logs (automake test harness + our logs)
#------------------------------------------------------
CLEANFILES = \
	*.log
