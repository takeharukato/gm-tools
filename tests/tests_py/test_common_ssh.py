# gm-tools-tests-20251116/tests_py/test_common_ssh.py

from __future__ import annotations

from typing import Sequence

from ._local_types import CommandResult, Config
from . import sshexec


def ssh_run(cfg: Config, host: str, argv: Sequence[str]) -> CommandResult:
    """
    非 sudo の SSH 実行ラッパ。
    """
    return sshexec.run_remote(cfg, host, list(argv))


def ssh_run_sudo(cfg: Config, host: str, argv: Sequence[str]) -> CommandResult:
    """
    sudo 経由の SSH 実行ラッパ。
    """
    return sshexec.run_sudo(cfg, host, list(argv))


def ssh_pipe_to_tee(
    cfg: Config,
    host: str,
    path: str,
    content: str,
    *,
    sudo: bool = False,
) -> CommandResult:
    """
    tee を使ってリモートの path に content を流し込むラッパ。
    """
    return sshexec.pipe_to_tee(cfg, host, path, content, sudo=sudo)
