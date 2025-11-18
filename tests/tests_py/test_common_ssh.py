# gm-tools-tests-20251116/tests_py/test_common_ssh.py

from __future__ import annotations

from typing import Sequence, Union, cast
import subprocess

from ._local_types import CommandResult, Config
from . import sshexec
from gm_tools.core_ssh import SSHClientLike, SFTPClientLike


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


def ssh_get_remote_home(cfg: Config, host: str, user: str) -> str:
    """
    リモートのユーザーの HOME を `getent passwd <user>` から取得するユーティリティ。
    - 成功時は絶対パスの HOME を文字列で返す
    - 失敗や不正な形式の場合は AssertionError を送出
    """
    r: CommandResult = ssh_run(cfg, host, ["getent", "passwd", user])
    if r.rc != 0:
        raise AssertionError(
            f"{host}: getent passwd {user} failed: rc={r.rc}, stderr={(r.stderr or '')!r}"
        )
    line: str = (r.stdout or "").splitlines()[0] if (r.stdout or "").splitlines() else ""
    parts = line.strip().split(":") if line else []
    if len(parts) < 6:
        raise AssertionError(f"{host}: invalid passwd entry for {user}: {line!r}")
    home: str = parts[5]
    if not home.startswith("/"):
        raise AssertionError(f"{host}: bad home path for {user}: {home!r}")
    return home


def ssh_run_raw(
    ssh_user: str,
    host: str,
    port: int,
    strict: Union[bool, str],
    *remote_argv: str,
) -> subprocess.CompletedProcess[str]:
    """素の SSH 実行の実装層に委譲"""
    return sshexec.ssh_run_raw(ssh_user, host, port, strict, *remote_argv)


def dummy_open_ssh(host: str) -> SSHClientLike:
    """テスト用のダミー SSH オープナー（Step6 用ユーティリティ）。"""
    _ = host
    return cast(SSHClientLike, object())


def dummy_open_sftp(ssh: SSHClientLike) -> SFTPClientLike:
    """テスト用のダミー SFTP オープナー（Step6 用ユーティリティ）。"""
    _ = ssh
    return cast(SFTPClientLike, object())
