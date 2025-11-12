# -*- coding:utf-8 -*-
from __future__ import annotations
import shlex
from typing import Iterable, Literal, List, Set

from .core_ssh import SSHClientLike
from .core_cmd_flavor import run_remote_cmd_capture

SelinuxMode = Literal["auto", "policy", "ignore"]

# --- constants (seconds / sizes) ---
_SELINUX_DETECT_TIMEOUT: float = 5.0
_RESTORECON_TIMEOUT: float = 180.0
# keep conservative to avoid "Argument list too long"
_RESTORECON_BATCH_SIZE: int = 64

# --- command strings / flags (no Final, to match project style) ---
_SELINUX_FS_TEST_CMD: str = "test -d /sys/fs/selinux"
_SELINUX_MOUNT_CHECK_CMD: str = "mount | grep -q selinuxfs"
_RESTORECON_CHECK_CMD: str = "command -v restorecon >/dev/null 2>&1"
_RESTORECON_FLAGS: str = "-RF"

def detect_selinux_capable(ssh: SSHClientLike) -> bool:
    """
    リモートホストが SELinux ラベリングの復元を実施可能かを判定する。
    条件:
      - /sys/fs/selinux の存在 または mount 出力に selinuxfs がある
      - restorecon コマンドが存在
    """

    rc_fs1: int
    _o1: str
    _e1: str
    rc_fs1, _o1, _e1 = run_remote_cmd_capture(
        ssh, ["bash", "-lc", _SELINUX_FS_TEST_CMD], timeout=_SELINUX_DETECT_TIMEOUT
    )
    if rc_fs1 != 0:
        rc_fs2: int
        _o2: str
        _e2: str
        rc_fs2, _o2, _e2 = run_remote_cmd_capture(
            ssh, ["bash", "-lc", _SELINUX_MOUNT_CHECK_CMD], timeout=_SELINUX_DETECT_TIMEOUT
        )
        if rc_fs2 != 0:
            return False

    rc_cmd: int
    _o3: str
    _e3: str
    rc_cmd, _o3, _e3 = run_remote_cmd_capture(
        ssh, ["bash", "-lc", _RESTORECON_CHECK_CMD], timeout=_SELINUX_DETECT_TIMEOUT
    )
    if rc_cmd != 0:
        return False

    return True


def restorecon_recursive_if_needed(
    *,
    ssh: SSHClientLike,
    paths: Iterable[str],
    mode: SelinuxMode,
    selinux_capable: bool,
    use_sudo: bool,
) -> None:
    """
    SELinux ラベル復元を必要に応じて実行する。
    - mode == "ignore": 何もしない
    - mode == "auto": capable=False ならスキップ, True なら restorecon -RF
    - mode == "policy": capable=False なら例外 ( 全体中断 ) , True なら restorecon -RF
    - 対象は NEW セットに限定して渡される前提 ( EXIST は呼び出し側で除外 )
    """
    if mode == "ignore":
        return

    if not selinux_capable:
        if mode == "policy":
            raise RuntimeError("SELinux policy enforcement requested but host is not SELinux-capable")
        # auto かつ非対応は黙ってスキップ
        return

    # 実行
    # 正規化：空文字除外・重複排除・安定順序
    norm: List[str] = []
    seen: Set[str] = set()
    p: str
    for p in paths:
        s: str = str(p).strip()
        if not s:
            continue
        if s in seen:
            continue
        seen.add(s)
        norm.append(s)

    if not norm:
        return

    # まとめて 1 回で実行 ( 長い場合でも restorecon は複数引数を受ける )
    # -R ( 再帰 ) , -F ( 強制再ラベル )
    sudo_argv_prefix: List[str] = ["sudo", "-n"] if use_sudo else []
    # バッチ実行 ( 引数超過・ARG_MAX 回避 )
    i: int
    for i in range(0, len(norm), _RESTORECON_BATCH_SIZE):
        chunk: List[str] = norm[i : i + _RESTORECON_BATCH_SIZE]
        q_paths: List[str] = [shlex.quote(p) for p in chunk]
        cmd: str = f"restorecon {_RESTORECON_FLAGS} -- " + " ".join(q_paths)
        rc: int; _out: str; err: str
        rc, _out, err = run_remote_cmd_capture(
            ssh, sudo_argv_prefix + ["bash", "-lc", cmd], timeout=_RESTORECON_TIMEOUT
        )
        if rc != 0:
            # policy/auto いずれでも restorecon 失敗は中断させる ( 上位で failed 記録 )
            emsg: str = (err.strip() or f"restorecon failed (rc={rc}) on batch starting with: {chunk[0]}")
            raise RuntimeError(emsg)

    return