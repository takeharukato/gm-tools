# -*- coding:utf-8 -*-
from __future__ import annotations

import shlex
from typing import Iterable, Literal, List

try:
    import paramiko  # type: ignore
except Exception as e:
    raise RuntimeError("Paramiko is required: pip install paramiko") from e

from .core_cmd_flavor import run_remote_cmd_capture

SelinuxMode = Literal["auto", "policy", "ignore"]


def detect_selinux_capable(ssh: "paramiko.SSHClient") -> bool:
    """
    リモートホストが SELinux ラベリングの復元を実施可能かを判定する。
    条件:
      - /sys/fs/selinux の存在 または mount 出力に selinuxfs がある
      - restorecon コマンドが存在
    """
    rc_fs1, _o1, _e1 = run_remote_cmd_capture(
        ssh, (["bash", "-lc", "test -d /sys/fs/selinux"]), timeout=5.0
    )
    if rc_fs1 != 0:
        rc_fs2, _o2, _e2 = run_remote_cmd_capture(
            ssh, (["bash", "-lc", "mount | grep -q selinuxfs"]), timeout=5.0
        )
        if rc_fs2 != 0:
            return False

    rc_cmd, _o3, _e3 = run_remote_cmd_capture(
        ssh, (["bash", "-lc", "command -v restorecon >/dev/null 2>&1"]), timeout=5.0
    )
    if rc_cmd != 0:
        return False

    return True


def restorecon_recursive_if_needed(
    *,
    ssh: "paramiko.SSHClient",
    paths: Iterable[str],
    mode: SelinuxMode,
    selinux_capable: bool,
    use_sudo: bool,
) -> None:
    """
    SELinux ラベル復元を必要に応じて実行する。
    - mode == "ignore": 何もしない
    - mode == "auto": capable=False ならスキップ、True なら restorecon -RF
    - mode == "policy": capable=False なら例外（全体中断）、True なら restorecon -RF
    - 対象は NEW セットに限定して渡される前提（EXIST は呼び出し側で除外）
    """
    if mode == "ignore":
        return

    if not selinux_capable:
        if mode == "policy":
            raise RuntimeError("SELinux policy enforcement requested but host is not SELinux-capable")
        # auto かつ非対応は黙ってスキップ
        return

    # 実行
    q_paths: List[str] = [shlex.quote(p) for p in paths]
    if not q_paths:
        return

    prefix = "sudo " if use_sudo else ""
    # まとめて 1 回で実行（長い場合でも restorecon は複数引数を受ける）
    # -R（再帰）, -F（強制再ラベル）
    cmd = f"{prefix}restorecon -RF -- " + " ".join(q_paths)
    run_remote_cmd_capture(ssh, (["bash", "-lc", cmd]), timeout=120.0)
