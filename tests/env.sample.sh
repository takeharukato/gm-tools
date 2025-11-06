#!/usr/bin/env bash
export GM_GATHER_CMD="${GM_GATHER_CMD:-gm-gather}"
export GM_SCATTER_CMD="${GM_SCATTER_CMD:-gm-scatter}"

export HOSTS_LOCAL="${HOSTS_LOCAL:-tests/hosts_local.txt}"
export HOSTS_VMLINUX="${HOSTS_VMLINUX:-tests/hosts_vmlinux.txt}"
export HOSTS_BOTH="${HOSTS_BOTH:-tests/hosts_both.txt}"

export SSH_USER="${SSH_USER:-ansible}"
export TARGET_USER="${TARGET_USER:-ansible}"

export SSH_PORT="${SSH_PORT:-22}"
export STRICT_HK="${STRICT_HK:-}"

export TEST_OUTPUT_DIR="${TEST_OUTPUT_DIR:-tests/output}"
export SCATTER_SRC_DIR="${SCATTER_SRC_DIR:-tests/data/src_tree}"
export SCATTER_PACK_DEST="/tmp/gm_scatter_dest"
export GATHER_DEST_LOCAL="${GATHER_DEST_LOCAL:-tests/output/gather_out}"

export PARALLEL_J1="${PARALLEL_J1:-1}"
export PARALLEL_J4="${PARALLEL_J4:-4}"
export PARALLEL_J0="${PARALLEL_J0:-0}"
