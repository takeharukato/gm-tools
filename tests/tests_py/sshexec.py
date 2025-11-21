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
SSH 経由の実行・sudo・tee 等の低レベルユーティリティ。
"""
from __future__ import annotations
import subprocess
from typing import List, Union
from ._local_types import CommandResult, Config

def _base_ssh_args(cfg: Config, host: str) -> List[str]:
    """
    ssh 実行の共通引数配列を構築します ( 内部ヘルパ ) 。

    Args:
    - cfg (Config): 実行時構成。
    - host (str): 対象ホスト。

    Returns:
    - List[str]: ssh の引数配列。
    """
    return [
        "ssh",
        "-o", f"StrictHostKeyChecking={cfg.ssh_strict}",
        "-p", str(cfg.ssh_port),
        f"{cfg.ssh_user}@{host}",
    ]

def run_remote(cfg: Config, host: str, argv: List[str]) -> CommandResult:
    """
    非 sudo でリモートコマンドを実行します。

    Args:
    - cfg (Config): 実行時構成。
    - host (str): 対象ホスト。
    - argv (List[str]): リモートで実行するコマンド。

    Returns:
    - CommandResult: 実行結果 ( rc/stdout/stderr ) 。
    """
    cmd = _base_ssh_args(cfg, host) + ["--"] + argv
    p = subprocess.run(cmd, capture_output=True, text=True)
    return CommandResult(p.returncode, p.stdout, p.stderr)

def run_sudo(cfg: Config, host: str, argv: List[str]) -> CommandResult:
    """
    sudo 経由でリモートコマンドを実行します ( `sudo -n` ) 。

    Args:
    - cfg (Config): 実行時構成。
    - host (str): 対象ホスト。
    - argv (List[str]): リモートで実行するコマンド。

    Returns:
    - CommandResult: 実行結果 ( rc/stdout/stderr ) 。
    """
    cmd = _base_ssh_args(cfg, host) + ["--"] + ["sudo", "-n"] + argv
    p = subprocess.run(cmd, capture_output=True, text=True)
    return CommandResult(p.returncode, p.stdout, p.stderr)

def pipe_to_tee(cfg: Config, host: str, path: str, content: str, sudo: bool) -> CommandResult:
    """
    `tee` を用いてリモートの `path` へ `content` を書き込みます。

    Args:
    - cfg (Config): 実行時構成。
    - host (str): 対象ホスト。
    - path (str): 書き込み先の絶対パス。
    - content (str): 書き込むテキスト。
    - sudo (bool): sudo 経由で実行する場合は True。

    Returns:
    - CommandResult: 実行結果 ( rc/stdout/stderr ) 。
    """
    base = _base_ssh_args(cfg, host)
    tee_argv = ["tee", path]
    cmd = base + ["--"] + (["sudo", "-n"] if sudo else []) + tee_argv
    p = subprocess.run(cmd, input=content, capture_output=True, text=True)
    return CommandResult(p.returncode, p.stdout, p.stderr)

def ssh_run_raw(
    ssh_user: str,
    host: str,
    port: int,
    strict: Union[bool, str],
    *remote_argv: str,
) -> subprocess.CompletedProcess[str]:
    """
    素の ssh を直接叩く低レベルヘルパ ( Config 非依存 ) 。

    Args:
    - ssh_user (str): SSH ユーザ。
    - host (str): 対象ホスト。
    - port (int): ポート番号。
    - strict (Union[bool, str]): StrictHostKeyChecking の設定 ( bool は yes/no に正規化 ) 。
    - remote_argv (str): リモートで実行する引数列。

    Returns:
    - subprocess.CompletedProcess[str]: 実行結果オブジェクト。

    Notes:
    - 用途: スナップショット採取など, cfg に依存したくない場面。
    """
    strict_str: str = strict if isinstance(strict, str) else ("yes" if strict else "no")
    argv: List[str] = [
        "ssh",
        "-p",
        str(port),
        "-o",
        f"StrictHostKeyChecking={strict_str}",
        "--",
        f"{ssh_user}@{host}",
    ] + list(remote_argv)
    return subprocess.run(argv, capture_output=True, text=True)
