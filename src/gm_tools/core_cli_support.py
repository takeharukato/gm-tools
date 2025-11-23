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

"""CLI向け引数検証の共通ヘルパーを提供するモジュール。

Examples:
    >>> from gm_tools.core_cli_support import validate_cli_positional_args
    >>> result = validate_cli_positional_args(['a'], 'b')
    >>> result.error_code
    0
    >>> result.has_error()
    False
"""

from __future__ import annotations

import sys
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gettext import gettext as _

from .core_constants import DEFAULT_PARALLEL_HOSTS, EXIT_ERR_ARGS
from .core_path_handling import is_bare_tilde, tilde_username
from .core_ssh import SSHConfig, close_connections, ssh_open

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
    """CLI位置引数検証の結果を保持するデータクラス。

    Attributes:
        normalized_srcs (List[str]): 検証済み SRC トークンのリスト。
        normalized_dest (str): 検証済み DEST トークン。
        error_code (int): エラー要因を表す数値コード。
        exit_code (int): 推奨される終了コード。
        error_message (Optional[str]): エラーメッセージ。平常時は ``None``。

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
        """検証結果にエラーが存在するかを判定する。

        Returns:
            bool: エラーがある場合は ``True``, 正常なら ``False``。

        Examples:
            >>> CliPositionalValidationResult(['a'], 'dest', ERROR_CODE_OK, EXIT_ERR_ARGS).has_error()
            False
            >>> CliPositionalValidationResult(['a'], 'dest', ERROR_CODE_NO_DEST, EXIT_ERR_ARGS).has_error()
            True
        """

        return self.error_code != ERROR_CODE_OK


@dataclass
class HostConnectivityValidationResult:
    """ホストごとの SSH/SFTP 接続検証結果を保持するデータクラス。

    Attributes:
        reachable_hosts (List[str]): 接続と SFTP オープンに成功したホスト一覧。
        unreachable_hosts (List[str]): 接続または SFTP オープンに失敗したホスト一覧。
        errors (Dict[str, str]): 失敗ホストに紐づくエラーメッセージ。

    Examples:
        >>> HostConnectivityValidationResult(['ok'], ['ng'], {'ng': 'error'}).has_failures()
        True
    """

    reachable_hosts: List[str]
    unreachable_hosts: List[str]
    errors: Dict[str, str]

    def has_failures(self) -> bool:
        """一つでも接続失敗を含むかを判定する。

        Returns:
            bool: 失敗を含む場合は ``True``, 全ホスト成功時は ``False``。

        Examples:
            >>> HostConnectivityValidationResult(['ok'], [], {}).has_failures()
            False
            >>> HostConnectivityValidationResult([], ['ng'], {'ng': 'err'}).has_failures()
            True
        """

        return bool(self.unreachable_hosts)


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


def validate_hosts_connectivity(
    hosts: Sequence[str],
    *,
    ssh_user: Optional[str],
    port: int,
    key_filename: Optional[str],
    password: Optional[str],
    timeout: float,
    strict_host_key_checking: bool,
    debug_print: bool = False,
    max_workers: Optional[int] = None,
) -> HostConnectivityValidationResult:
    """SSH および SFTP 接続可否を確認する。

    Args:
        hosts (Sequence[str]): 検証対象となるホスト名またはアドレスの列。
        ssh_user (Optional[str]): SSH 接続に使用するユーザー名。
        port (int): SSH ポート番号。
        key_filename (Optional[str]): 秘密鍵ファイルのパス。
        password (Optional[str]): パスワード認証に使用する文字列。
        timeout (float): 接続・SFTP オープンのタイムアウト秒数。
        strict_host_key_checking (bool): Known Hosts の厳格検証を行う場合は ``True``。
        debug_print (bool): ``ssh_open`` のデバッグ出力可否。
        max_workers (Optional[int]): 並行実行するスレッド数の上限。 ``None`` の場合は
            ``min(len(hosts), DEFAULT_PARALLEL_HOSTS)`` を用いる。

    Returns:
        HostConnectivityValidationResult: 接続可否の判定結果。

    Examples:
        >>> validate_hosts_connectivity(['example'], ssh_user=None, port=22,  # doctest: +SKIP
        ...     key_filename=None, password=None, timeout=5.0,
        ...     strict_host_key_checking=False)  # doctest: +SKIP
        HostConnectivityValidationResult(...)
    """

    host_items: List[str] = [str(h) for h in hosts]
    if not host_items:
        return HostConnectivityValidationResult(
            reachable_hosts=[],
            unreachable_hosts=[],
            errors={},
        )

    worker_count: int
    if max_workers is None:
        worker_count = min(len(host_items), DEFAULT_PARALLEL_HOSTS)
    else:
        worker_count = int(max_workers)
    if worker_count <= 0:
        worker_count = 1

    def _probe_host(host_value: str) -> Tuple[str, bool, Optional[str]]:
        """単一ホストに対する SSH/SFTP 接続可否を試行する。

        Args:
            host_value (str): 接続検証対象のホスト文字列。

        Returns:
            Tuple[str, bool, Optional[str]]: ``(ホスト名, 成功フラグ, エラーメッセージ)``。

        Examples:
            >>> _probe_host('example')  # doctest: +SKIP
            ('example', True, None)
        """
        host_str: str = str(host_value).strip()
        if not host_str:
            return "", False, _("host value is empty")

        cfg: SSHConfig = SSHConfig(
            host=host_str,
            port=int(port),
            ssh_user=ssh_user,
            key_filename=key_filename,
            password=password,
            timeout=float(timeout),
            strict_host_key_checking=bool(strict_host_key_checking),
        )

        ssh = None
        sftp = None
        try:
            try:
                ssh = ssh_open(cfg, debug_print=debug_print)
            except Exception as exc:
                message = _("Failed to establish SSH connection: {error}").format(error=str(exc))
                return host_str, False, message

            try:
                sftp = ssh.open_sftp()
            except Exception as exc:
                message = _("Failed to open SFTP session: {error}").format(error=str(exc))
                return host_str, False, message

            return host_str, True, None
        finally:
            if sftp is not None:
                try:
                    sftp.close()
                except Exception:
                    pass
            if ssh is not None:
                try:
                    ssh.close()
                except Exception:
                    pass
            if host_str:
                close_connections(host_str)

    reachable: List[str] = []
    unreachable: List[str] = []
    errors: Dict[str, str] = {}

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        for host_str, success, message in executor.map(_probe_host, host_items):
            if success:
                reachable.append(host_str)
            else:
                unreachable.append(host_str)
                if message:
                    errors[host_str] = message

    return HostConnectivityValidationResult(
        reachable_hosts=reachable,
        unreachable_hosts=unreachable,
        errors=errors,
    )


def filter_hosts_by_connectivity(
    hosts: Sequence[str],
    *,
    ssh_user: Optional[str],
    port: int,
    key_filename: Optional[str],
    password: Optional[str],
    timeout: float,
    strict_host_key_checking: bool,
    debug_print: bool = False,
    max_workers: Optional[int] = None,
    logger: Optional[logging.Logger] = None,
) -> HostConnectivityValidationResult:
    """接続検証を行い, 利用可能なホストのみ抽出して警告を記録する。

    Args:
        hosts (Sequence[str]): 接続検証対象のホスト名またはアドレスの列。
        ssh_user (Optional[str]): SSH 接続に使用するユーザー名。
        port (int): SSH ポート番号。
        key_filename (Optional[str]): 秘密鍵ファイルのパス。
        password (Optional[str]): パスワード認証に使用する文字列。
        timeout (float): 接続および SFTP セッション確立のタイムアウト秒数。
        strict_host_key_checking (bool): Known Hosts の厳格検証を行う場合は ``True``。
        debug_print (bool): ``ssh_open`` のデバッグ出力可否。
        max_workers (Optional[int]): 並列実行するスレッド数の上限。
        logger (Optional[logging.Logger]): 警告メッセージを出力するロガー。

    Returns:
        HostConnectivityValidationResult: 接続検証の結果。 ``reachable_hosts`` に
        実行対象ホストが格納される。
    """

    probe_result = validate_hosts_connectivity(
        hosts,
        ssh_user=ssh_user,
        port=port,
        key_filename=key_filename,
        password=password,
        timeout=timeout,
        strict_host_key_checking=strict_host_key_checking,
        debug_print=debug_print,
        max_workers=max_workers,
    )

    log_obj: logging.Logger = logger or logging.getLogger(__name__)
    if probe_result.unreachable_hosts:
        for host_name in probe_result.unreachable_hosts:
            reason: str = probe_result.errors.get(host_name, _("Unknown error"))
            message = _(
                "Host '%(host)s' excluded from processing: %(reason)s"
            ) % {
                "host": host_name or "<empty>",
                "reason": reason,
            }
            log_obj.warning(message)

    if not probe_result.reachable_hosts:
        print(_("No hosts passed connectivity validation."), file=sys.stderr)

    return probe_result
