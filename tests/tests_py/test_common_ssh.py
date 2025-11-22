# -*- mode: python; coding: utf-8; line-endings: unix -*-
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2025 TAKEHARU KATO
#
# This file is distributed under the two-clause BSD license.
# For the full text of the license, see the LICENSE file in the project root directory.
# このファイルは2条項BSDライセンスの下で配布されています。
# ライセンス全文はプロジェクト直下の LICENSE を参照してください。
#
# OpenAI's ChatGPT partially generated this code.
# Author has modified some parts.
# OpenAIのChatGPTがこのコードの一部を生成しました。
# 著者が修正している部分があります。
"""
SSH 実行ラッパとダミーオープナー。
"""

from __future__ import annotations

from typing import Sequence, Union, cast
import subprocess

from ._local_types import CommandResult, Config
from . import sshexec
from gm_tools.core_ssh import SSHClientLike, SFTPClientLike


def ssh_run(cfg: Config, host: str, argv: Sequence[str]) -> CommandResult:
    """
    非 sudo の SSH 実行ラッパです。

    Args:
        cfg (Config): 実行時構成。
        host (str): 対象ホスト。
        argv (Sequence[str]): 実行するコマンド引数列。

    Returns:
        CommandResult: 実行結果 ( rc/stdout/stderr ) 。
    """
    return sshexec.run_remote(cfg, host, list(argv))


def ssh_run_sudo(cfg: Config, host: str, argv: Sequence[str]) -> CommandResult:
    """
    sudo 経由の SSH 実行ラッパです。

    Args:
        cfg (Config): 実行時構成。
        host (str): 対象ホスト。
        argv (Sequence[str]): 実行するコマンド引数列。
    Returns:
        CommandResult: 実行結果 ( rc/stdout/stderr ) 。
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
    `tee` を使ってリモートの `path` に `content` を流し込みます。

    Args:
        cfg (Config): 実行時構成。
        host (str): 対象ホスト。
        path (str): 書き込み先のパス。
        content (str): 書き込む内容。
        sudo (bool): sudo 経由で実行する場合は True。

    Returns:
        CommandResult: 実行結果 ( rc/stdout/stderr ) 。
    """
    return sshexec.pipe_to_tee(cfg, host, path, content, sudo=sudo)


def ssh_get_remote_home(cfg: Config, host: str, user: str) -> str:
    """
    `getent passwd <user>` からリモートユーザの HOME を取得します。

    Args:
        cfg (Config): 実行時構成。
        host (str): 対象ホスト。
        user (str): 対象ユーザ。

    Returns:
        str: 絶対パスの HOME。

    Raises:
        AssertionError: コマンド失敗/エントリ不正/絶対パスでない場合。
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
    """
    素の SSH 実行の実装層に委譲します。

    Args:
        ssh_user (str): SSH ユーザ。
        host (str): 対象ホスト。
        port (int): ポート番号。
        strict (Union[bool, str]): StrictHostKeyChecking 設定。
        remote_argv (str): リモートに渡す引数列。

    Returns:
        subprocess.CompletedProcess[str]: 実行結果。
    """
    return sshexec.ssh_run_raw(ssh_user, host, port, strict, *remote_argv)


def dummy_open_ssh(host: str) -> SSHClientLike:
    """
    テスト用のダミー SSH オープナー ( Step6 用 ) 。

    Args:
        host (str): 対象ホスト。

    Returns:
        SSHClientLike: ダミーオブジェクト。
    """
    _ = host
    return cast(SSHClientLike, object())


def dummy_open_sftp(ssh: SSHClientLike) -> SFTPClientLike:
    """
    テスト用のダミー SFTP オープナー ( Step6 用 ) 。

    Args:
        ssh (SSHClientLike): ダミー SSH クライアント。

    Returns:
        SFTPClientLike: ダミーオブジェクト。
    """
    _ = ssh
    return cast(SFTPClientLike, object())
