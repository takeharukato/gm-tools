#!/usr/bin/env bash
# -*- bash -*-

# ========== CONSTANTS (edit here) ============================================
export GM_GATHER_CMD=${GM_GATHER_CMD:-gm-gather}
export GM_SCATTER_CMD=${GM_SCATTER_CMD:-gm-scatter}

# Paths
export SUITE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export TESTS_DIR="${SUITE_ROOT}/tests"
export TMP_ROOT="${TESTS_DIR}/_tmp_run"
export LOCAL_DEST="${LOCAL_DEST:-${TMP_ROOT}/local_dest}"

# Remote destination root (absolute on remote)
export REMOTE_DEST="${REMOTE_DEST:-/tmp/gmtools_remote_dest}"

# Hosts files (absolute paths; we resolve them early to survive cd)
export HOSTS_BOTH="$(readlink -f "${TESTS_DIR}/hosts/hosts_both")"

# Users
export SSH_USER="${SSH_USER:-ansible}"
export TARGET_USER="${TARGET_USER:-ansible}"   # gather --user / scatter --user

# SSH options (no .ssh/config dependency)
export SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_rsa}"
export SSH_PORT="${SSH_PORT:-22}"
export SSH_STRICT="${SSH_STRICT:-yes}"  # yes|no
export SSH_OPTS="-o StrictHostKeyChecking=${SSH_STRICT} -p ${SSH_PORT} -i ${SSH_KEY}"

# Verbose logs (set to 1 to pass -v)
export VERBOSE="${VERBOSE:-1}"

# Parallel degree for Step5
export PARALLEL="${PARALLEL:-2}"

# =============================================================================
mkdir -p "${TMP_ROOT}" "${LOCAL_DEST}"

# helper to pass -v
function maybe_verbose_flag() {
  if [[ "${VERBOSE}" == "1" ]]; then echo "-v"; fi
}
