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
ローカル一時ディレクトリのクリーンアップと, ローカル実行の薄いラッパを提供します。
"""
# tests/tests_py/test_common_local.py
from __future__ import annotations

import os
from typing import List, Optional, Any
from .test_common_cleanup import safe_rmtree_abs
from ._local_types import Config
from .gmwrap import gm_run_local_with_argv as _gm_run_local_argv_public
from ._local_types import LocalRun


def cleanup_local_temps(cfg: Config, rel_dirs: Optional[List[str]] = None) -> None:
    """
    共通のローカル一時ディレクトリクリーンアップを行います。

    Args:
        cfg (Config): 実行時構成 ( `local_root` を参照 ) 。
        rel_dirs (Optional[List[str]]): カレント配下の相対ディレクトリ群。

    Notes:
        - `cfg.local_root` を安全に削除します。
        - `rel_dirs` が与えられた場合, それらも安全に削除します。
    """
    cwd: str = os.getcwd()
    safe_rmtree_abs(cfg.local_root, ensure_under=cwd)
    for d in (rel_dirs or []):
        abs_path: str = os.path.join(cwd, d)
        safe_rmtree_abs(abs_path, ensure_under=cwd)


def run_local_with_argv(argv: List[str]) -> LocalRun:
    """
    公開ローカル実行ヘルパ。`gmwrap` の公開関数で実行し, 結果を `LocalRun` として返します。

    Args:
        argv (List[str]): 実行するコマンドライン。

    Returns:
        LocalRun: 実行結果 ( rc/stdout/stderr ) 。
    """
    res: Any = _gm_run_local_argv_public(argv)
    return LocalRun(int(getattr(res, "rc", 0)), str(getattr(res, "stdout", "")), str(getattr(res, "stderr", "")))
