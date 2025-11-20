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

"""リモートユーザーのホームディレクトリを検出するユーティリティ。

``getent passwd`` を優先しつつ、適切なフォールバック ( ``/root`` または
``/home/<user>`` ) を提供する。入力コマンドは ``run_remote_cmd_capture`` のポリシーに
従い、``bash -lc`` で PATH を初期化して実行される。
"""

from __future__ import annotations

import shlex

from .core_cmd_flavor import run_remote_cmd_capture
from .core_path_handling import (
    HOME_DETECT_CMD_FMT,
    HOME_FALLBACK_ROOT,
    HOME_FALLBACK_PREFIX,
)
from .core_ssh import SSHClientLike


def detect_remote_home(ssh: SSHClientLike, user: str, timeout: float) -> str:
    """リモートホスト上のユーザー HOME を検出する。

    まず ``getent passwd`` を利用して HOME を取得し、失敗または絶対パスでない結果の
    場合にフォールバック先 ( ``root`` ユーザーは ``/root``、それ以外は
    ``/home/<user>`` ) を返す。

    Args:
        ssh (SSHClientLike): リモートコマンドを実行するための SSH クライアント互換オブジェクト。
        user (str): HOME を検出したいリモートユーザー名。
        timeout (float): コマンド実行に許容する秒数。

    Returns:
        str: 検出またはフォールバックにより得られた HOME の絶対パス。

    Examples:
        >>> from types import SimpleNamespace
        >>> import gm_tools.core_remote_path as mod  # doctest: +SKIP
        >>> def fake_run_remote_cmd_capture(_ssh, argv, timeout):  # doctest: +SKIP
        ...     assert argv[2].startswith('getent passwd')
        ...     return (0, '/home/demo\n', '')
        >>> original = mod.run_remote_cmd_capture  # doctest: +SKIP
        >>> mod.run_remote_cmd_capture = fake_run_remote_cmd_capture  # doctest: +SKIP
        >>> try:  # doctest: +SKIP
        ...     detect_remote_home(SimpleNamespace(), 'demo', 1.0)
        ... finally:  # doctest: +SKIP
        ...     mod.run_remote_cmd_capture = original
        '/home/demo'
        >>> def fail_run_remote_cmd_capture(_ssh, argv, timeout):  # doctest: +SKIP
        ...     return (1, '', '')
        >>> mod.run_remote_cmd_capture = fail_run_remote_cmd_capture  # doctest: +SKIP
        >>> try:  # doctest: +SKIP
        ...     detect_remote_home(SimpleNamespace(), 'guest', 1.0)
        ... finally:  # doctest: +SKIP
        ...     mod.run_remote_cmd_capture = original
        '/home/guest'
    """
    fallback: str = HOME_FALLBACK_ROOT if user == "root" else f"{HOME_FALLBACK_PREFIX}/{user}"
    rc, out, _ = run_remote_cmd_capture(
        ssh, ["bash", "-lc", HOME_DETECT_CMD_FMT.format(user=shlex.quote(user))], timeout=timeout
    )
    cand: str = out.strip()
    return cand if (rc == 0 and cand.startswith("/")) else fallback