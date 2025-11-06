\
#!/usr/bin/env bash
set -euo pipefail

# Load env & lib
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=env.sh
source "${SCRIPT_DIR}/env.sh"
# shellcheck source=lib/ssh.sh
source "${SCRIPT_DIR}/lib/ssh.sh"

VFLAG="$(maybe_verbose_flag)"

echo "== Step4 smoke (SFTP, --pack, sudo, follow-symlinks, path semantics) =="

# -----------------------------------------------------------------------------
# 0) Pre-flight: prepare remote roots & perms on both hosts
# -----------------------------------------------------------------------------
for H in $(cat "${HOSTS_BOTH}"); do
  echo "-- prepare on ${H}"
  ssh_run "${H}" "sudo mkdir -p '${REMOTE_DEST}' && sudo chown -R ${TARGET_USER}:${TARGET_USER} '${REMOTE_DEST}'"
done

# -----------------------------------------------------------------------------
# 1) Gather: simple SFTP /etc/hosts -> DEST/<HOST>/etc/hosts
# -----------------------------------------------------------------------------
echo "[G-01] gather SFTP /etc/hosts"
rm -rf "${LOCAL_DEST}/g01" && mkdir -p "${LOCAL_DEST}/g01"
${GM_GATHER_CMD} "/etc/hosts" "${LOCAL_DEST}/g01" -H "${HOSTS_BOTH}" -u "${TARGET_USER}" -s "${SSH_USER}" ${VFLAG:+-v}

for H in $(cat "${HOSTS_BOTH}"); do
  test -f "${LOCAL_DEST}/g01/${H}/etc/hosts" || { echo "missing ${H}/etc/hosts"; exit 1; }
done
echo "  ok"

# -----------------------------------------------------------------------------
# 2) Gather: --pack + --sudo-collect (root-owned file) + --follow-symlinks
# -----------------------------------------------------------------------------
echo "[G-02] gather --pack with sudo & follow-symlinks"

for H in $(cat "${HOSTS_BOTH}"); do
  ssh_run "${H}" "sudo rm -rf /tmp/gm_pack_case && sudo mkdir -p /tmp/gm_pack_case && echo secret | sudo tee /tmp/gm_pack_case/secret.txt >/dev/null && sudo chown root:root /tmp/gm_pack_case/secret.txt && sudo chmod 600 /tmp/gm_pack_case/secret.txt && ln -sf /tmp/gm_pack_case/secret.txt /tmp/gm_pack_case/secret.link"
done

rm -rf "${LOCAL_DEST}/g02" && mkdir -p "${LOCAL_DEST}/g02"
${GM_GATHER_CMD} "/tmp/gm_pack_case/.*" "${LOCAL_DEST}/g02" -H "${HOSTS_BOTH}" -u "${TARGET_USER}" -s "${SSH_USER}" --pack --follow-symlinks -x ${VFLAG:+-v}

for H in $(cat "${HOSTS_BOTH}"); do
  test -f "${LOCAL_DEST}/g02/${H}/tmp/gm_pack_case/secret.txt" || { echo "missing ${H}/secret.txt"; exit 1; }
done
echo "  ok"

# -----------------------------------------------------------------------------
# 3) Scatter: SFTP upload of a single file (absolute SRC)
#    Expect remote: DEST/<local_abs_without_leading_slash>
# -----------------------------------------------------------------------------
echo "[S-ABS-01] scatter absolute SRC"
TMP_SRC="${TMP_ROOT}/s_abs"
mkdir -p "${TMP_SRC}"
echo "hello" > "${TMP_SRC}/hello.txt"
ABS_FILE="$(readlink -f "${TMP_SRC}/hello.txt")"
REMOTE_EXPECT="${REMOTE_DEST}/${ABS_FILE#/}"
${GM_SCATTER_CMD} "${ABS_FILE}" "${REMOTE_DEST}" -H "${HOSTS_BOTH}" -u "${TARGET_USER}" -s "${SSH_USER}" ${VFLAG:+-v}

for H in $(cat "${HOSTS_BOTH}"); do
  ssh_run "${H}" "test -f '${REMOTE_EXPECT}'"
done
echo "  ok"

# -----------------------------------------------------------------------------
# 4) Scatter: **relative SRC** (S-REL-01)
#    Resolve against CWD, then same DEST/<local_abs_without_leading_slash> rule.
# -----------------------------------------------------------------------------
echo "[S-REL-01] scatter relative SRC -> CWD-based"
REL_ROOT="${TMP_ROOT}/rel_case/src"
mkdir -p "${REL_ROOT}/a"
echo "rel" > "${REL_ROOT}/a/b.txt"
pushd "${REL_ROOT}" >/dev/null
  ABS="$(readlink -f ./a/b.txt)"
  REMOTE_REL="${ABS#/}"
  REMOTE_EXPECT="${REMOTE_DEST}/${REMOTE_REL}"
  ${GM_SCATTER_CMD} ./a/b.txt "${REMOTE_DEST}" -H "${HOSTS_BOTH}" -u "${TARGET_USER}" -s "${SSH_USER}" ${VFLAG:+-v}
popd >/dev/null

for H in $(cat "${HOSTS_BOTH}"); do
  ssh_run "${H}" "test -f '${REMOTE_EXPECT}'"
done
echo "  ok"

echo "== Step4 smoke: ALL GREEN =="
