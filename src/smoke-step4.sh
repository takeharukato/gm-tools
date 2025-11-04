#!/usr/bin/env bash
# Step4 smoke tests (minimal fixes)
set -euo pipefail

HOST_LOCAL="localhost"
HOST_REMOTE="vmlinux4"
SSH_USER="ansible"
SEM_USER_ROOT="root"
TIMEOUT=60
PARALLEL=1
USE_ENTRYPOINTS="${USE_ENTRYPOINTS:-0}"

if [[ "${USE_ENTRYPOINTS}" == "1" ]]; then
  GATHER_CMD=(gm-gather -v)
  SCATTER_CMD=(gm-scatter -v)
else
  GATHER_CMD=(python3 -m gm_tools.gather_cli -v)
  SCATTER_CMD=(python3 -m gm_tools.scatter_cli -v)
fi

SSH_OPTS=(-o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
WORKDIR="$(mktemp -d -t gmstep4-smoke-XXXXXX)"
trap 'rm -rf "${WORKDIR}" || :' EXIT

HOSTS_LOCAL="${WORKDIR}/hosts.local"
HOSTS_REMOTE="${WORKDIR}/hosts.remote"
echo "${HOST_LOCAL}"  >  "${HOSTS_LOCAL}"
echo "${HOST_REMOTE}" >  "${HOSTS_REMOTE}"

SRC_DIR="${WORKDIR}/src"
mkdir -p "${SRC_DIR}/subdir"
echo "hello world" > "${SRC_DIR}/a.txt"
: > "${SRC_DIR}/emptyfile"
ln -s "a.txt" "${SRC_DIR}/alink"

DL_BASE="${WORKDIR}/downloads"
mkdir -p "${DL_BASE}"

pass() { echo "PASS: $*"; }
fail() { echo "FAIL: $*" >&2; return 1; }

# 常に 1 引数で bash -lc に流し込む（クォート強化）
run_ssh() {
  local host="$1"; shift
  local cmd="$*"
  ssh "${SSH_OPTS[@]}" -l "${SSH_USER}" "${host}" -- bash -lc "$cmd"
}
run_ssh_sudo() {
  local host="$1"; shift
  local cmd="$*"
  ssh "${SSH_OPTS[@]}" -l "${SSH_USER}" "${host}" -- sudo -n bash -lc "$cmd"
}

# 事前クリーン
for h in "${HOST_LOCAL}" "${HOST_REMOTE}"; do
    run_ssh_sudo "${h}" "test -e /opt/gmstep4 && rm -rf -- /opt/gmstep4 || :"
    run_ssh      "${h}" "test -e /tmp/gmstep4_sftp && rm -rf -- /tmp/gmstep4_sftp || :"
done

echo "Workdir: ${WORKDIR}"
echo "Hosts  : ${HOST_LOCAL}, ${HOST_REMOTE}"
echo


# -------- Precheck: SELinux mode on remote (informational) --------
if run_ssh "${HOST_REMOTE}" "command -v getenforce >/dev/null 2>&1"; then
  SESTAT_REMOTE="$(run_ssh "${HOST_REMOTE}" "getenforce || true" || true)"
  case "${SESTAT_REMOTE}" in
    Permissive|Disabled)
      echo "[INFO] ${HOST_REMOTE} SELinux: ${SESTAT_REMOTE}"
      ;;
    Enforcing)
      echo "[WARN] ${HOST_REMOTE} SELinux: Enforcing (tests should still pass, but Step4 assumes Permissive/Disabled)" >&2
      ;;
    *)
      echo "[INFO] ${HOST_REMOTE} SELinux: ${SESTAT_REMOTE}"
      ;;
  esac
else
  echo "[INFO] ${HOST_REMOTE} has no getenforce (skipping SELinux precheck)"
fi
echo


# -------- T1: scatter (pack + sudo-extract auto) --------
echo "[T1] scatter (pack+sudo-extract auto) -> /opt/gmstep4"
DEST_OPT="/opt/gmstep4"
set +e
"${SCATTER_CMD[@]}" \
  -H "${HOSTS_LOCAL}" -u "${SEM_USER_ROOT}" -s "${SSH_USER}" \
  -T "${TIMEOUT}" -j "${PARALLEL}" --pack --follow-symlinks \
  "${SRC_DIR}" "${DEST_OPT}"
RC1_L=$?

"${SCATTER_CMD[@]}" \
  -H "${HOSTS_REMOTE}" -u "${SEM_USER_ROOT}" -s "${SSH_USER}" \
  -T "${TIMEOUT}" -j "${PARALLEL}" --pack --follow-symlinks \
  "${SRC_DIR}" "${DEST_OPT}"
RC1_R=$?
set -e

if [[ $RC1_L -eq 0 && $RC1_R -eq 0 ]]; then
  ABASE="$(cd "${SRC_DIR}" && pwd)"
  # 想定レイアウト: DEST/<abs_without_leading_slash>/a.txt
  CAND="${DEST_OPT}/${ABASE#/}/a.txt"
  # 無ければフォールバックで find 検索（レイアウト差異の検出と説明用）
  run_ssh_sudo "${HOST_LOCAL}"  "test -f '${CAND}' || find '${DEST_OPT}' -type f -name a.txt -print -quit | grep -q ."
  run_ssh_sudo "${HOST_REMOTE}" "test -f '${CAND}' || find '${DEST_OPT}' -type f -name a.txt -print -quit | grep -q ."
  pass "T1 scatter packed & sudo-extract on both hosts (found a.txt under ${DEST_OPT})"
else
  fail "T1 scatter: non-zero exit (localhost=${RC1_L}, vmlinux4=${RC1_R})"
fi
echo

# -------- T2: gather (pack + sudo-collect auto) /etc/hosts --------
echo "[T2] gather (pack+sudo-collect auto) /etc/hosts"
DEST_DL="${DL_BASE}/pack"
mkdir -p "${DEST_DL}"
set +e
"${GATHER_CMD[@]}" \
  -H "${HOSTS_LOCAL}" -u "${SEM_USER_ROOT}" -s "${SSH_USER}" \
  -T "${TIMEOUT}" -j "${PARALLEL}" --pack \
  "/etc/hosts" "${DEST_DL}"
RC2_L=$?
"${GATHER_CMD[@]}" \
  -H "${HOSTS_REMOTE}" -u "${SEM_USER_ROOT}" -s "${SSH_USER}" \
  -T "${TIMEOUT}" -j "${PARALLEL}" --pack \
  "/etc/hosts" "${DEST_DL}"
RC2_R=$?
set -e
if [[ $RC2_L -eq 0 && $RC2_R -eq 0 ]]; then
  test -f "${DEST_DL}/${HOST_LOCAL}/etc/hosts"
  test -f "${DEST_DL}/${HOST_REMOTE}/etc/hosts"
  pass "T2 gather packed & sudo-collect from both hosts"
else
  fail "T2 gather: non-zero exit (localhost=${RC2_L}, vmlinux4=${RC2_R})"
fi
echo

# -------- T2b: sudo auto=OFF when ssh-user==user==ansible (no sudo-walk marker) --------
echo "[T2b] gather (pack, ssh-user==user==ansible) should NOT use sudo (no 'remote/sudo-walk' marker)"
DEST_DL2B="${DL_BASE}/pack_no_sudo"
mkdir -p "${DEST_DL2B}"
set +e
OUT_T2B="$(
  "${GATHER_CMD[@]}" \
    -H "${HOSTS_LOCAL}" -u ansible -s ansible \
    -T "${TIMEOUT}" -j "${PARALLEL}" --pack \
    "/etc/hosts" "${DEST_DL2B}" 2>&1
)"
RC2B=$?
set -e
if [[ $RC2B -eq 0 ]]; then
  # sudo 走査のデバッグ行が含まれていないことを確認
  if echo "${OUT_T2B}" | grep -q "\[debug\] candidates (remote/sudo-walk)"; then
    echo "${OUT_T2B}" >&2
    fail "T2b: unexpected sudo-walk path used"
  else
    pass "T2b gather used non-sudo path as expected"
  fi
else
  echo "${OUT_T2B}" >&2
  fail "T2b gather failed (rc=${RC2B})"
fi
echo

# -------- T2c: gather (pack) with '~/': tilde expansion smoke --------
echo "[T2c] gather (pack) with '~/' tilde SRC (localhost)"
DEST_DL2C="${DL_BASE}/pack_tilde"
mkdir -p "${DEST_DL2C}"
set +e
"${GATHER_CMD[@]}" \
  -H "${HOSTS_LOCAL}" -u "${SEM_USER_ROOT}" -s "${SSH_USER}" \
  -T "${TIMEOUT}" -j "${PARALLEL}" --pack \
  "~/" "${DEST_DL2C}"
RC2C=$?
set -e
if [[ $RC2C -eq 0 ]]; then
  # 展開先にホストディレクトリができていれば十分（深追いしない）
  test -d "${DEST_DL2C}/${HOST_LOCAL}"
  pass "T2c gather with '~/' completed"
else
  fail "T2c gather with '~/' failed (rc=${RC2C})"
fi
echo

# -------- T3: gather (non-pack + --sudo-collect) → FAIL --------
echo "[T3] gather (non-pack) + --sudo-collect should FAIL"
DEST_DL2="${DL_BASE}/nonpack_sudo_collect"
mkdir -p "${DEST_DL2}"
set +e
"${GATHER_CMD[@]}" \
  -H "${HOSTS_LOCAL}" -u "${SEM_USER_ROOT}" -s "${SSH_USER}" \
  -T "${TIMEOUT}" -j "${PARALLEL}" --sudo-collect \
  "/etc/hosts" "${DEST_DL2}"
RC3=$?
set -e
if [[ $RC3 -ne 0 ]]; then
  pass "T3 gather non-pack + --sudo-collect correctly failed (rc=${RC3})"
else
  fail "T3 gather should have failed but returned success"
fi
echo

# -------- T4: scatter (SFTP non-pack no sudo) -> /tmp/gmstep4_sftp --------
echo "[T4] scatter (SFTP, non-pack, no sudo) -> /tmp/gmstep4_sftp"
DEST_TMP="/tmp/gmstep4_sftp"
set +e
"${SCATTER_CMD[@]}" \
  -H "${HOSTS_LOCAL}" -u "${SSH_USER}" -s "${SSH_USER}" \
  -T "${TIMEOUT}" -j "${PARALLEL}" \
  "${SRC_DIR}" "${DEST_TMP}"
RC4_L=$?
"${SCATTER_CMD[@]}" \
  -H "${HOSTS_REMOTE}" -u "${SSH_USER}" -s "${SSH_USER}" \
  -T "${TIMEOUT}" -j "${PARALLEL}" \
  "${SRC_DIR}" "${DEST_TMP}"
RC4_R=$?
set -e
if [[ $RC4_L -eq 0 && $RC4_R -eq 0 ]]; then
  ABASE="$(cd "${SRC_DIR}" && pwd)"
  CAND="${DEST_TMP}/${ABASE#/}/a.txt"
  run_ssh "${HOST_LOCAL}"  "test -f '${CAND}' || find '${DEST_TMP}' -type f -name a.txt -print -quit | grep -q ."
  run_ssh "${HOST_REMOTE}" "test -f '${CAND}' || find '${DEST_TMP}' -type f -name a.txt -print -quit | grep -q ."
  # symlink は SFTP 経路では無視される
  if run_ssh "${HOST_LOCAL}" "test -e '${DEST_TMP}/${ABASE#/}/alink'"; then
    fail "T4: unexpected symlink materialized on localhost"
  fi
  if run_ssh "${HOST_REMOTE}" "test -e '${DEST_TMP}/${ABASE#/}/alink'"; then
    fail "T4: unexpected symlink materialized on vmlinux4"
  fi
  pass "T4 scatter SFTP on both hosts"
else
  fail "T4 scatter SFTP: non-zero exit (localhost=${RC4_L}, vmlinux4=${RC4_R})"
fi
echo
echo "=== All smoke tests attempted ==="

# checking no leftover
echo "[Post] ensure no leftover /tmp/collect_abs_* on both hosts"
run_ssh "${HOST_LOCAL}"  "ls /tmp/collect_abs_* 2>/dev/null | wc -l | grep -q '^0$' || { ls -l /tmp/collect_abs_* 2>/dev/null; exit 1; }"
run_ssh "${HOST_REMOTE}" "ls /tmp/collect_abs_* 2>/dev/null | wc -l | grep -q '^0$' || { ls -l /tmp/collect_abs_* 2>/dev/null; exit 1; }"
echo "[Post] OK: no leftover temporary archives"