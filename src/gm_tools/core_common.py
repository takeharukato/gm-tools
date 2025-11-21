#!/usr/bin/env python3
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
# cspell:ignore hostfile
"""ホストファイル共通処理を提供するモジュール。

Hosts file 形式のテキストから空行やコメントを除外したリストを返す
`parse_hosts_file()` を公開する。

Examples:
    >>> from pathlib import Path
    >>> import tempfile
    >>> data = '# comment\\nweb01.example.com  # primary\\n\\nweb02.example.com\\n'
    >>> with tempfile.TemporaryDirectory() as tmp:
    ...     hostfile = Path(tmp) / "hosts"
    ...     _ = hostfile.write_text(data, encoding="utf-8")
    ...     parse_hosts_file(str(hostfile))
    ['web01.example.com', 'web02.example.com']
"""

from __future__ import annotations

import re
from typing import List


def parse_hosts_file(path: str) -> List[str]:
    """hosts ファイルを読み込みコメントを除外したホスト一覧を返す。

    Args:
        path (str): 読み取る hosts ファイルへのパス。

    Returns:
        list[str]: コメントと空行を除いたホスト名または IP のリスト。

    Raises:
        FileNotFoundError: 指定したファイルが存在しない場合。
        OSError: 読み込み時に OS レベルのエラーが発生した場合。

    Examples:
        >>> from pathlib import Path
        >>> import tempfile
        >>> content = '# heading\\nweb01.example.com\\nweb02.example.com  # memo\\n'
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     hostfile = Path(tmp) / "hosts"
        ...     _ = hostfile.write_text(content, encoding="utf-8")
        ...     parse_hosts_file(str(hostfile))
        ['web01.example.com', 'web02.example.com']
    """
    hosts: List[str] = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            s: str = raw.strip()
            if not s or s.startswith("#"):
                continue
            s = re.split(r"\s+#", s, 1)[0].strip()
            if s:
                hosts.append(s)
    return hosts
