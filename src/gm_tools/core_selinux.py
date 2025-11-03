# -*- coding:utf-8 -*-
from __future__ import annotations

import shlex
from typing import Tuple

try:
    import paramiko  # type: ignore
except Exception as e:
    raise RuntimeError("Paramiko is required: pip install paramiko") from e

from .core_cmd_flavor import run_remote_cmd_capture


class SelinuxMode:
    AUTO: str = "auto"
    POLICY: str = "policy"
    IGNORE: str = "ignore"


def _run0(ssh: "paramiko.SSHClient", cmd: str, use_sudo: bool, timeout: float = 30.0) -> Tuple[int, str, str]:
    return run_remote_cmd_capture(ssh, (["sudo"] if use_sudo else []) + ["bash", "-lc", cmd], timeout=timeout)


def selinux_supported(ssh: "paramiko.SSHClient", use_sudo: bool) -> bool:
    """リモートが SELinux ラベル適用に対応できるか（selinuxfs 存在、restorecon 実行可）。"""
    rc1, _, _ = _run0(ssh, "[ -d /sys/fs/selinux ] || mount | grep -q selinuxfs", use_sudo, 5.0)
    rc2, _, _ = _run0(ssh, "command -v restorecon >/dev/null 2>&1", use_sudo, 5.0)
    return rc1 == 0 and rc2 == 0


def apply_restorecon_for_members(
    ssh: "paramiko.SSHClient",
    dest_abs_root: str,
    members_file: str,
    use_sudo: bool,
    timeout: float = 180.0,
) -> Tuple[int, str, str]:
    """
    members_file に列挙された相対パス（NEW のみを想定）へ restorecon -RF を適用。
    """
    qdest: str = shlex.quote(dest_abs_root)
    qmem: str = shlex.quote(members_file)
    # 1 行ずつ対象に適用（-RF）：ディレクトリにもファイルにも対応
    cmd: str = f'while IFS= read -r p; do [ -n "$p" ] || continue; restorecon -RF "{qdest}/$p" || exit 1; done < {qmem}'
    return _run0(ssh, cmd, use_sudo, timeout)
