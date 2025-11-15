#!/usr/bin/env bash
# run_step6.sh - Step6 GracefulStop/Signal/Parallel 統合テスト実行スクリプト
# 使い方:
#   1) tests_env.sh.sample を tests_env.sh にコピーして編集
#   2) chmod +x run_step6.sh
#   3) ./run_step6.sh
set -Eeuo pipefail

# スクリプトの所在ディレクトリ（テストアーカイブ直下想定）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CWD="$(pwd)"

# 1) 明示指定があれば最優先（例: ENV_FILE=./my.env ./run_step6.sh）
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

# Python 実行（runner_step6.py は現状プレースホルダ結果のみを出力）
PYTHON_BIN="${PYTHON_BIN:-python3}"
echo "PYTHON_BIN  : ${PYTHON_BIN}"

# tests_py をパッケージとして解決するため、tests ディレクトリ（SCRIPT_DIR）を
# PYTHONPATH に含めれば十分です。
# （SCRIPT_DIR/tests_py を残しても支障はありませんが、必須ではありません）
export PYTHONPATH="${CWD}:${SCRIPT_DIR}:${PYTHONPATH:-}"

set +e
# runner_step6 を「tests_py パッケージ配下のモジュール」として起動する
"${PYTHON_BIN}" -u -m tests_py.runner_step6
RC=$?
set -e

echo "=== Step6 tests finished: RC=${RC} ==="
exit "${RC}"
