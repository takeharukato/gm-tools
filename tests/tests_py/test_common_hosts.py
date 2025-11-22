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
hosts ファイルの一時生成と追跡を行うユーティリティ。
"""
# tests/tests_py/test_common_hosts.py
# 共通: hostsファイルユーティリティ
from __future__ import annotations

import os
import tempfile
from typing import IO, List

_CREATED_HOSTS_FILES: List[str] = []


def write_temp_hosts(hosts: List[str]) -> str:
    """
    一時 hosts ファイルを作成し, そのパスを返します。

    Args:
        hosts (List[str]): 1 行 1 ホストで書き込むホスト名の配列。

    Returns:
        str: 作成した一時ファイルのパス。

    Notes:
        - 内容は UTF-8 テキストで 1 行 1 ホストとして書き込みます。
        - 呼び出し側がライフサイクル管理 ( 削除 ) を行う前提です。
    """
    fd: int
    path: str
    fd, path = tempfile.mkstemp(prefix="hosts_", text=True)
    os.close(fd)
    f: IO[str]
    with open(path, "w", encoding="utf-8") as f:
        i: int = 0
        n: int = len(hosts)
        while i < n:
            h: str = hosts[i]
            _ = f.write(h + "\n")
            i += 1
    try:
        _CREATED_HOSTS_FILES.append(path)
    except Exception:
        pass
    return path

def get_created_hosts_files() -> List[str]:
    """
    生成済みの hosts ファイルパス一覧を返します。

    Returns:
        List[str]: 追跡しているファイルパスのコピー。
    """
    return list(_CREATED_HOSTS_FILES)
