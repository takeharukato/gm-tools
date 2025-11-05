# -*- coding:utf-8 -*-
from __future__ import annotations

import shlex

from .core_ssh import SSHClientLike
from .core_cmd_flavor import run_remote_cmd_capture
from .core_path_handling import (
    HOME_DETECT_CMD_FMT,
    HOME_FALLBACK_ROOT,
    HOME_FALLBACK_PREFIX,
)

def detect_remote_home(ssh: SSHClientLike, user: str, timeout: float) -> str:
    """
    getent 優先でリモート user の HOME を取得。失敗時はフォールバック。
    - PATH 注入は run_remote_cmd_capture 側ポリシーに従う（bash -lc）。
    - 失敗/未設定時は root→/root、その他→/home/<user> にフォールバック。
    """
    fallback: str = HOME_FALLBACK_ROOT if user == "root" else f"{HOME_FALLBACK_PREFIX}/{user}"
    rc, out, _ = run_remote_cmd_capture(
        ssh, ["bash", "-lc", HOME_DETECT_CMD_FMT.format(user=shlex.quote(user))], timeout=timeout
    )
    cand: str = out.strip()
    return cand if (rc == 0 and cand.startswith("/")) else fallback