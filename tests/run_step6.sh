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
# run_step6.sh - Step6 GracefulStop/Signal/Parallel 統合テスト実行スクリプト
# 使い方:
#   1) tests_env.sh.sample を tests_env.sh にコピーして編集
#   2) chmod +x run_step6.sh
#   3) ./run_step6.sh
set -Eeuo pipefail

# スクリプトの所在ディレクトリ ( tests ディレクトリ想定 )
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# プロジェクトルート ( tests の 1 つ上 ) を決定
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CWD="$(pwd)"

# 1) 明示指定があれば最優先 ( 例: ENV_FILE=./my.env ./run_step6.sh )
ENV_FILE="${ENV_FILE:-}"

# 2) 読み込み順: $ENV_FILE > $CWD/tests_env.sh > $SCRIPT_DIR/tests_env.sh.sample
if [[ -n "${ENV_FILE}" && -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
elif [[ -f "${CWD}/tests_env.sh" ]]; then
  # shellcheck disable=SC1091
  source "${CWD}/tests_env.sh"
else
  # shellcheck disable=SC1091
  source "${SCRIPT_DIR}/tests_env.sh.sample"
fi

# Python 実行 ( runner_step6.py は現状プレースホルダ結果のみを出力 )
PYTHON_BIN="${PYTHON_BIN:-python3}"
echo "PYTHON_BIN  : ${PYTHON_BIN}"

# ワークツリー版 gm_tools と tests_py を解決できるよう PYTHONPATH を設定
#  - PROJECT_ROOT/src : gm_tools パッケージ
#  - SCRIPT_DIR/tests_py: runner_step6 などテストモジュール
export PYTHONPATH="${PROJECT_ROOT}/src:${SCRIPT_DIR}:${SCRIPT_DIR}/tests_py:${PYTHONPATH:-}"

set +e
# runner_step6 を「tests_py パッケージ配下のモジュール」として起動する
"${PYTHON_BIN}" -u -m tests_py.runner_step6
RC=$?
set -e

echo "=== Step6 tests finished: RC=${RC} ==="
exit "${RC}"
