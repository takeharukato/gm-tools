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
gm ツールのローカル実行ラッパを提供します。サブプロセス実行と一時 hosts ファイルの生成を伴います。
"""
from __future__ import annotations
import subprocess, shlex, tempfile
from typing import List
from ._local_types import CommandResult, Config

def _run_local_argv(argv: List[str]) -> CommandResult:
        """
        任意の argv をローカルで実行します ( 内部ヘルパ ) 。

        Args:
        - argv (List[str]): 実行するコマンドと引数。

        Returns:
        - CommandResult: 実行結果 ( rc/stdout/stderr ) 。
        """
        print("[DEBUG] _run_local_argv argv:",
            " ".join(shlex.quote(x) for x in argv), flush=True)
        p = subprocess.run(argv, capture_output=True, text=True)
        return CommandResult(p.returncode, p.stdout, p.stderr)

def gm_run_local_with_argv(argv: List[str]) -> CommandResult:
    """
    公開 API。gm ツールのローカル実行を行い, rc/stdout/stderr を返します。
    既存の `_run_local_argv` を安定インターフェースとしてエクスポートします。

    Args:
    - argv (List[str]): 実行するコマンドライン。

    Returns:
    - CommandResult: 実行結果 ( rc/stdout/stderr ) 。
    """
    return _run_local_argv(argv)

def run_gather(cfg: Config, host: str, user: str, src: str, dest: str, extra: List[str]) -> CommandResult:
    """
    gather 相当の CLI をローカル実行します。単一ホストでも一時 hosts ファイルを作成して `-H` で渡します。

    Args:
    - cfg (Config): 実行時構成。
    - host (str): 対象ホスト。
    - user (str): 対象ユーザ。
    - src (str): 取得元パス。
    - dest (str): 保存先パス。
    - extra (List[str]): 追加の CLI オプション。

    Returns:
    - CommandResult: 実行結果 ( rc/stdout/stderr ) 。

    Notes:
    - 一時ファイル ( hosts ) を作成します。
    - サブプロセスを起動します。
    """
    argv: List[str] = list(cfg.gm_gather_cmd) + ["-u", user, "-n"]
    argv: List[str] = list(cfg.gm_scatter_cmd) + ["-u", user, "-n"]
    # 単一ホストでも hosts ファイルを作って -H に渡す
    hf = tempfile.NamedTemporaryFile(mode="w", delete=False)
    try:
        hf.write(host + "\n")
        hf.flush()
        hosts_file = hf.name
    finally:
        hf.close()
    argv += ["-H", hosts_file]
    if cfg.verbose: argv.append("-v")
    argv += extra + ["--", src, dest]
    return _run_local_argv(argv)

def run_scatter(cfg: Config, host: str, user: str, src: str, dest: str, extra: List[str]) -> CommandResult:
    """
    scatter 相当の CLI をローカル実行します。単一ホストでも一時 hosts ファイルを作成して `-H` で渡します。

    Args:
    - cfg (Config): 実行時構成。
    - host (str): 対象ホスト。
    - user (str): 対象ユーザ。
    - src (str): 入力パス。
    - dest (str): 展開先パス。
    - extra (List[str]): 追加の CLI オプション。

    Returns:
    - CommandResult: 実行結果 ( rc/stdout/stderr ) 。

    Notes:
    - 一時ファイル ( hosts ) を作成します。
    - サブプロセスを起動します。
    """
    argv: List[str] = list(cfg.gm_scatter_cmd) + ["-u", user, "-n"]
    # 単一ホストでも hosts ファイルを作って -H に渡す
    hf = tempfile.NamedTemporaryFile(mode="w", delete=False)
    try:
        hf.write(host + "\n")
        hf.flush()
        hosts_file = hf.name
    finally:
        hf.close()
    argv += ["-H", hosts_file]
    if cfg.verbose: argv.append("-v")
    argv += extra + ["--", src, dest]
    return _run_local_argv(argv)
