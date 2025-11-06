#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC2154
function ssh_run() {
  local host="$1"; shift
  local cmd="$*"
  ssh ${SSH_OPTS} "${SSH_USER}@${host}" -- bash -lc "$cmd"
}

function scp_up() {
  local src="$1"
  local host="$2"
  local dst="$3"
  scp ${SSH_OPTS} -q "$src" "${SSH_USER}@${host}:$dst"
}
