#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [[ -f "${SCRIPT_DIR}/tests_env.sh" ]]; then
  source "${SCRIPT_DIR}/tests_env.sh"
else
  echo "WARN: tests_env.sh not found; using defaults from sample"
  source "${SCRIPT_DIR}/tests_env.sh.sample"
fi
PYTHONPATH="${SCRIPT_DIR}" exec python3 -m tests_py.runner_step5
