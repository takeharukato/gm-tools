\
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=env.sh
source "${SCRIPT_DIR}/env.sh"
# shellcheck source=lib/ssh.sh
source "${SCRIPT_DIR}/lib/ssh.sh"

VFLAG="$(maybe_verbose_flag)"

echo "== Step5 smoke (parallel scatter/gather basic) =="

# Prepare remote roots
for H in $(cat "${HOSTS_BOTH}"); do
  ssh_run "${H}" "sudo mkdir -p '${REMOTE_DEST}' && sudo chown -R ${TARGET_USER}:${TARGET_USER} '${REMOTE_DEST}'"
done

# Parallel scatter: two files to two hosts
SRC_DIR="${TMP_ROOT}/p_scatter"
mkdir -p "${SRC_DIR}"
echo "p1" > "${SRC_DIR}/f1.txt"
echo "p2" > "${SRC_DIR}/f2.txt"

F1="$(readlink -f "${SRC_DIR}/f1.txt")"
F2="$(readlink -f "${SRC_DIR}/f2.txt")"

${GM_SCATTER_CMD} "${F1}" "${F2}" "${REMOTE_DEST}" -H "${HOSTS_BOTH}" -u "${TARGET_USER}" -s "${SSH_USER}" -j "${PARALLEL}" ${VFLAG:+-v}

for H in $(cat "${HOSTS_BOTH}"); do
  ssh_run "${H}" "test -f '${REMOTE_DEST}/${F1#/}' && test -f '${REMOTE_DEST}/${F2#/}'"
done

echo "== Step5 smoke: ALL GREEN =="
