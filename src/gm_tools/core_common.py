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
import sys
from typing import List
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # 型チェッカー向けのダミー定義 ( 実行時には評価されない )
    from gettext import gettext as _

def _parse_hosts_file(path: str) -> List[str]:
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
        >>> content = '# heading\\nweb01.example.com\\n\\nweb02.example.com  # memo\\n'
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     hostfile = Path(tmp) / "hostfile-test"
        ...     _ = hostfile.write_text(content, encoding="utf-8")
        ...     _parse_hosts_file(str(hostfile))
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

def get_host_list_from_hostfile(hostfile_path: str) -> List[str]:
    """ホストファイルを解析し, ホスト名一覧を取得する。
       CLI処理フローを共通化するためのラッパー関数。

    Args:
        hostfile_path (str): ホストファイルのパス。

    Returns:
        List[str]: ホストリストに含まれるホスト名一覧。

    Raises:
        FileNotFoundError: ホストリストファイルが存在しない場合。
        OSError: ホストリストファイルが読み取り不可能な場合。
        ValueError: ホストリストが空の場合。

    Examples:
        >>> from pathlib import Path
        >>> import tempfile
        >>> content = '# heading\\nweb01.example.com\\n\\nweb02.example.com  # memo\\n'
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     hostfile = Path(tmp) / "hostfile-test"
        ...     _ = hostfile.write_text(content, encoding="utf-8")
        ...     get_host_list_from_hostfile(str(hostfile))
        ['web01.example.com', 'web02.example.com']
    """

    # ホストファイル解析
    try:
        hosts: List[str] = _parse_hosts_file(hostfile_path)

    except FileNotFoundError as exc:
        print(_("No hostfile found: hostfile='{fname}' {err}").format(fname=str(hostfile_path), err=str(exc)), file=sys.stderr)
        raise FileNotFoundError from exc

    except OSError as exc:
        print(_("Can not read hostfile: hostfile='{fname}' {err}").format(fname=str(hostfile_path), err=str(exc)), file=sys.stderr)
        raise OSError from exc
    finally:
        pass

    if len(hosts) == 0:
        print(_("No hosts found in hostfile: hostfile='{fname}'").format(fname=str(hostfile_path)), file=sys.stderr)
        raise ValueError("No hosts found in hostfile")

    return hosts
