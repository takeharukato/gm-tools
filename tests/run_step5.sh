#!/usr/bin/env bash
# run_step5.sh - Step5 並行転送テスト実行スクリプト
# 使い方:
#   1) tests_env.sh.sample を tests_env.sh にコピーして編集
#   2) chmod +x run_step5.sh
#   3) ./run_step5.sh
set -Eeuo pipefail

# スクリプトの所在ディレクトリ（テストアーカイブ直下想定）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# tests_env.sh を読み込み（未存在ならエラー）
CWD="$(pwd)"

# 1) 明示指定があれば最優先（例: ENV_FILE=./my.env ./run_step4.sh）
ENV_FILE="${ENV_FILE:-}"

# 2) 読み込み順: $ENV_FILE > $CWD/tests_env.sh > tests_env.sh.sample
if [[ -n "${ENV_FILE}" && -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
elif [[ -f "${CWD}/tests_env.sh" ]]; then
  # shellcheck disable=SC1091
  source "${CWD}/tests_env.sh"
else
  echo "WARN: tests_env.sh not found under ${CWD}; using defaults from sample"
  # shellcheck disable=SC1091
  source "${SCRIPT_DIR}/tests_env.sh.sample"
fi

# 任意: Python 仮想環境があれば有効化（tests_env.sh 側で VENV_PATH を指定可能）
if [[ -n "${VENV_PATH:-}" && -d "${VENV_PATH}" ]]; then
  # shellcheck disable=SC1091
  source "${VENV_PATH}/bin/activate"
fi

# 必須環境変数のデフォルト（tests_env.sh で上書き可）
export GM_SCATTER="${GM_SCATTER_CMD:-gm-scatter}"              # 例: "python3 -m gm_tools.scatter_cli"
export GM_GATHER="${GM_GATHER_CMD:-gm-gather}"                 # 例: "python3 -m gm_tools.gather_cli"
export HOSTS="${HOSTS:-localhost,vmlinux4.local}"
export DEST_BASE="${DEST_BASE:-/tmp/gmtools_step5_out}"
export RESULTS_DIR="${RESULTS_DIR:-results/step5}"
export GM_SCATTER_EXTRA_ARGS="${GM_SCATTER_EXTRA_ARGS:-}"     # 任意: "--ssh-config ~/.ssh/config" 等
export GM_GATHER_EXTRA_ARGS="${GM_GATHER_EXTRA_ARGS:-}"       # 任意: "--ssh-config ~/.ssh/config" 等


# 事前の表示（ログ用）
echo "=== Step5 tests ==="
echo "DATE        : $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "GM_SCATTER  : ${GM_SCATTER}"
echo "HOSTS       : ${HOSTS}"
echo "DEST_BASE    : ${DEST_BASE}"
echo "RESULTS_DIR  : ${RESULTS_DIR}"
echo "EXTRA_ARGS   : ${GM_SCATTER_EXTRA_ARGS}"

# 出力ディレクトリの作成
mkdir -p "${RESULTS_DIR}"

# 参考: gm-scatter コマンドの存在チェック（コマンド文字列対応）
# 実際の疎通はテスト本体で行うため、ここでは help 実行を軽く試すのみ（失敗しても続行）
if [[ "${GM_SCATTER}" != *" "* ]]; then
  if ! command -v "${GM_SCATTER}" >/dev/null 2>&1; then
    echo "[WARN] GM_SCATTER=${GM_SCATTER} は PATH 上に見つかりません（tests_env.sh の設定を確認してください）。" >&2
  fi
fi

# Python 実行（runner_step5.py は必要な入出力を results/step5 配下に保存）
PYTHON_BIN="${PYTHON_BIN:-python3}"
echo "PYTHON_BIN  : ${PYTHON_BIN}"
export PYTHONPATH="${CWD}:${SCRIPT_DIR}/tests_py:${PYTHONPATH:-}"
# 環境変数を runner に継承して実行
set +e
"${PYTHON_BIN}" -u "${SCRIPT_DIR}/tests_py/runner_step5.py"
RC=$?
set -e

echo "=== Step5 tests finished: RC=${RC} ==="
if [[ ${RC} -eq 0 ]]; then
  echo "ALL TESTS PASSED"
else
  echo "SOME TESTS FAILED (see ${RESULTS_DIR}/summary.json and per-test details.json)"
fi

exit "${RC}"
