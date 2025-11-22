#!/usr/bin/env bash
# -*- mode: bash; coding: utf-8; line-endings: unix -*-
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
# run-test-module-test.sh - 単体試験スクリプト
# 使い方: srcディレクトリで以下を実行
#   1) tests_env.sh.sample を tests_env.sh にコピーして編集
#   2) chmod +x run-test-module-test.sh
#   3) ./run-test-module-test.sh
set -Eeuo pipefail
PYTHONPATH=. python3 -m pytest -q ../tests/mt/test_core_signal_handling.py
PYTHONPATH=. python3 -m pytest -q ../tests/mt/test_path_handling.py
PYTHONPATH=. python3 -m pytest -q ../tests/mt/test_scatter_pack_cleanup.py
PYTHONPATH=. python3 -m pytest -q ../tests/mt/test_test_common_cleanup.py
PYTHONPATH=. python3 -m pytest -q ../tests/mt/test_test_common_config.py
PYTHONPATH=. python3 -m pytest -q ../tests/mt/test_test_common_ssh.py
PYTHONPATH=. python3 -m pytest -q ../tests/mt/test_core_path_handling.py
PYTHONPATH=. python3 -m pytest -q ../tests/mt/test_core_select.py
echo "=== All test-framework tests finished ==="