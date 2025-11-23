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

"""CLI向け位置引数検証の共通ヘルパーを提供するモジュール。

Examples:
    >>> from gm_tools.core_cli_support import validate_cli_positional_args
    >>> result = validate_cli_positional_args(['a'], 'b')
    >>> result.error_code
    0
    >>> result.has_error()
    False
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gettext import gettext as _

from .core_constants import EXIT_ERR_ARGS
from .core_path_handling import is_bare_tilde, tilde_username

# CLI 初期化より前に doctest や単体テストから呼ばれても英語メッセージを返せるよう,
# _ が未定義な場合は英語を返すフォールバックを用意
try:
    _  # type: ignore[name-defined]
except NameError:  # pragma: no cover - fallback for doctest / early imports
    def _(message: str) -> str:  # type: ignore[override]
        return message

ERROR_CODE_OK: int = 0
ERROR_CODE_NO_SRC: int = 1
ERROR_CODE_NO_DEST: int = 2
ERROR_CODE_SRC_BARE_TILDE: int = 3
ERROR_CODE_SRC_TILDE_USERNAME: int = 4
ERROR_CODE_DEST_BARE_TILDE: int = 5
ERROR_CODE_DEST_TILDE_USERNAME: int = 6


@dataclass
class CliPositionalValidationResult:
    """CLI位置引数検証の結果を格納するデータクラス。

    Attributes:
        normalized_srcs (List[str]): 検証済みSRCトークンのリスト。
        normalized_dest (str): 検証済みDESTトークン。
        error_code (int): エラー要因を表す数値コード。
        exit_code (int): 推奨される終了コード。
        error_message (Optional[str]): メッセージ。平常時は ``None``。

    Examples:
        >>> CliPositionalValidationResult(['a'], 'b', ERROR_CODE_OK, EXIT_ERR_ARGS).has_error()
        False
    """

    normalized_srcs: List[str]
    normalized_dest: str
    error_code: int
    exit_code: int
    error_message: Optional[str] = None

    def has_error(self) -> bool:
        """結果がエラーかどうかを返す。

        Returns:
            bool: ``True`` の場合はエラー, ``False`` の場合は正常。

        Examples:
            >>> CliPositionalValidationResult(['a'], 'dest', ERROR_CODE_OK, EXIT_ERR_ARGS).has_error()
            False
            >>> CliPositionalValidationResult(['a'], 'dest', ERROR_CODE_NO_DEST, EXIT_ERR_ARGS).has_error()
            True
        """

        return self.error_code != ERROR_CODE_OK


def validate_cli_positional_args(
    src_tokens: Sequence[str],
    dest_token: Optional[str],
    *,
    allow_src_bare_tilde: bool = False,
    allow_src_tilde_username: bool = False,
    allow_dest_bare_tilde: bool = False,
    allow_dest_tilde_username: bool = False,
    exit_code_default: int = EXIT_ERR_ARGS,
    exit_code_src_tilde_username: Optional[int] = None,
    exit_code_dest_tilde_username: Optional[int] = None,
) -> CliPositionalValidationResult:
    """SRCとDESTの位置引数を共通仕様に従って検証する。

    Args:
        src_tokens (Sequence[str]): CLIから受け取ったSRCトークン列。
        dest_token (Optional[str]): CLIから受け取ったDESTトークン。
        allow_src_bare_tilde (bool): SRCで素の ``~`` を許容する場合は ``True``。
        allow_src_tilde_username (bool): SRCで ``~user`` を許容する場合は ``True``。
        allow_dest_bare_tilde (bool): DESTで素の ``~`` を許容する場合は ``True``。
        allow_dest_tilde_username (bool): DESTで ``~user`` を許容する場合は ``True``。
        exit_code_default (int): エラー時に利用する既定の終了コード。
        exit_code_src_tilde_username (Optional[int]): SRC ``~user`` エラー用終了コード。
        exit_code_dest_tilde_username (Optional[int]): DEST ``~user`` エラー用終了コード。

    Returns:
        CliPositionalValidationResult: 正規化済みトークンとエラー情報を格納した結果。

    Examples:
        >>> validate_cli_positional_args(['src'], 'dest').has_error()
        False
        >>> result = validate_cli_positional_args([], 'dest')
        >>> (result.error_code, result.has_error())
        (ERROR_CODE_NO_SRC, True)
        >>> result = validate_cli_positional_args(['~'], 'dest')
        >>> (result.error_code, result.exit_code)
        (ERROR_CODE_SRC_BARE_TILDE, EXIT_ERR_ARGS)
    """

    src_list: List[str] = [str(token) for token in src_tokens]
    dest_str: str = "" if dest_token is None else str(dest_token)

    if not src_list:
        message: str = _("At least one SRC and a DEST are required.")
        return CliPositionalValidationResult(
            normalized_srcs=src_list,
            normalized_dest=dest_str,
            error_code=ERROR_CODE_NO_SRC,
            exit_code=exit_code_default,
            error_message=message,
        )

    if not dest_str:
        message = _("At least one SRC and a DEST are required.")
        return CliPositionalValidationResult(
            normalized_srcs=src_list,
            normalized_dest=dest_str,
            error_code=ERROR_CODE_NO_DEST,
            exit_code=exit_code_default,
            error_message=message,
        )

    if (not allow_src_bare_tilde) and any(is_bare_tilde(token) for token in src_list):
        message = _("bare tilde is not allowed")
        return CliPositionalValidationResult(
            normalized_srcs=src_list,
            normalized_dest=dest_str,
            error_code=ERROR_CODE_SRC_BARE_TILDE,
            exit_code=exit_code_default,
            error_message=message,
        )

    if not allow_src_tilde_username:
        for token in src_list:
            if tilde_username(token) is not None:
                message = _("tilde with username is not supported")
                exit_code: int = exit_code_src_tilde_username or exit_code_default
                return CliPositionalValidationResult(
                    normalized_srcs=src_list,
                    normalized_dest=dest_str,
                    error_code=ERROR_CODE_SRC_TILDE_USERNAME,
                    exit_code=exit_code,
                    error_message=message,
                )

    if (not allow_dest_bare_tilde) and is_bare_tilde(dest_str):
        message = _("bare tilde is not allowed")
        return CliPositionalValidationResult(
            normalized_srcs=src_list,
            normalized_dest=dest_str,
            error_code=ERROR_CODE_DEST_BARE_TILDE,
            exit_code=exit_code_default,
            error_message=message,
        )

    if (not allow_dest_tilde_username) and tilde_username(dest_str) is not None:
        message = _("tilde with username is not supported")
        exit_code = exit_code_dest_tilde_username or exit_code_default
        return CliPositionalValidationResult(
            normalized_srcs=src_list,
            normalized_dest=dest_str,
            error_code=ERROR_CODE_DEST_TILDE_USERNAME,
            exit_code=exit_code,
            error_message=message,
        )

    return CliPositionalValidationResult(
        normalized_srcs=src_list,
        normalized_dest=dest_str,
        error_code=ERROR_CODE_OK,
        exit_code=exit_code_default,
        error_message=None,
    )
