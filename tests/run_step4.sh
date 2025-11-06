#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
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

# パラメタダンプ
: "${SSH_USER:=ansible}"
: "${HOSTS_BOTH:=localhost}"
: "${GM_GATHER_CMD:=python3 -m gm_tools.gather_cli}"
: "${GM_SCATTER_CMD:=python3 -m gm_tools.scatter_cli}"
echo "[env] SSH_USER=${SSH_USER} HOSTS_BOTH=${HOSTS_BOTH}"
echo "[env] GM_GATHER_CMD='${GM_GATHER_CMD}'"
echo "[env] GM_SCATTER_CMD='${GM_SCATTER_CMD}'"

PYTHONPATH="${SCRIPT_DIR}" exec python3 -m tests_py.runner_step4
