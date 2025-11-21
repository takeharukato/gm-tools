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

"""gm-tools で使用する定数群を集中管理するモジュール。

アプリケーション全域で利用される終了コード, ログ整形キー, ホスト名の正規化規則
などを 1 箇所に定義し, 他モジュールでのマジックナンバー利用を避ける。既存ポリシーに
従い, ``typing.Final`` は使用せず, 定数値を変更する場合は互換性影響を考慮する。

Examples:
    >>> from gm_tools.core_constants import EXIT_OK, KEYS_PREFIX
    >>> EXIT_OK
    0
    >>> 'timestamp' in KEYS_PREFIX
    True
"""

from __future__ import annotations
from typing import Tuple

# プロジェクトポリシーにより typing.Final は使用しない。

# ---------------------------------------------------------------------------
# 終了コード
# ---------------------------------------------------------------------------

#: 正常終了を表す終了コード。
EXIT_OK: int = 0

#: ホスト未指定により処理を打ち切る際の終了コード。
EXIT_ERR_NO_HOSTS: int = 1

#: 汎用エラー終了コード ( 個別コードへ分類できない内部例外など ) 。
EXIT_ERR_GENERIC: int = 2

#: リモートパスのチルダ展開でユーザが不正だった場合の終了コード。
EXIT_ERR_TILDE_USER: int = 3

#: 引数不正で処理を打ち切る際の終了コード。
EXIT_ERR_ARGS: int = 4

# ---------------------------------------------------------------------------
# ホストファイル
# ---------------------------------------------------------------------------
DEFAULT_HOSTS_FILE: str = "hostfile"

# ---------------------------------------------------------------------------
# 並列実行
# ---------------------------------------------------------------------------

#: ``-j/--parallel`` を省略した場合に使用するホスト単位の並列数。
DEFAULT_PARALLEL_HOSTS: int = 4

# ---------------------------------------------------------------------------
# 正規表現パターン
# ---------------------------------------------------------------------------

# ファイルシステム格納用にホスト名を正規化するための正規表現。
RE_SAFE_HOST_PTN: str = r"[^A-Za-z0-9._-]"

# ---------------------------------------------------------------------------
# ログキーとスキーマ
# ---------------------------------------------------------------------------

#: ログレコード先頭に必ず並ぶキー群 ( 順序保持が必須 ) 。
KEYS_PREFIX: Tuple[str, ...] = (
    "timestamp",
    "level",
    "host",
    "op",
    "phase",
    "trial",
    "processed",
    "total",
)

#: コンテキストに応じて付加される任意キー群。
KEYS_OPTIONAL: Tuple[str, ...] = (
    "warnings",
    "errors",
    "duration",
    "seq",
)

__all__ = [
    "EXIT_OK",
    "EXIT_ERR_GENERIC",
    "EXIT_ERR_NO_HOSTS",
    "EXIT_ERR_TILDE_USER",
    "EXIT_ERR_ARGS",
    "DEFAULT_PARALLEL_HOSTS",
    "DEFAULT_HOSTS_FILE",
    "RE_SAFE_HOST_PTN",
    "KEYS_PREFIX",
    "KEYS_OPTIONAL",
]
