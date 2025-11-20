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

"""ローカルファイルに対する属性解決と適用ユーティリティを提供するモジュール。

パーミッションや所有者を表すデータクラスを定義し、既存ファイルの属性取得や
ユーザー/グループ解決、ベストエフォート方式での属性適用処理を提供する。

Examples:
    >>> from gm_tools.core_file_attr import FileAttrs
    >>> FileAttrs(mode=0o644, uid=None, gid=None)
    FileAttrs(mode=420, uid=None, gid=None)
"""

from __future__ import annotations

import os
import pwd
import grp
import stat
from dataclasses import dataclass
from typing import Optional, Union


@dataclass(frozen=True)
class FileAttrs:
    """ファイルへ適用したい属性を束ねるデータクラス。

    ``None`` を設定した項目は変更対象から除外される。

    Attributes:
        mode (Optional[int]): POSIX パーミッションビット（例: ``0o644``）。
        uid (Optional[int]): 所有者 UID。 ``None`` なら変更しない。
        gid (Optional[int]): グループ GID。 ``None`` なら変更しない。
    """

    mode: Optional[int] = None   # 例: 0o644
    uid: Optional[int] = None
    gid: Optional[int] = None


@dataclass(frozen=True)
class ApplyResult:
    """属性適用処理 :func:`apply_attrs_best_effort` の結果を表すデータクラス。

    Attributes:
        path (str): 対象パス。
        changed_mode (bool): ``mode`` を変更できた場合 ``True``。
        changed_owner (bool): ``uid`` を変更できた場合 ``True``。
        changed_group (bool): ``gid`` を変更できた場合 ``True``。
        error (Optional[str]): 発生した最後のエラーメッセージ。成功時は ``None``。
    """

    path: str
    changed_mode: bool
    changed_owner: bool
    changed_group: bool
    error: Optional[str] = None


def get_current_attrs(path: str) -> FileAttrs:
    """現在のファイル属性を取得する。

    シンボリックリンクを辿らず、指定パスのパーミッション・UID・GID を取得して
    :class:`FileAttrs` にまとめて返す。

    Args:
        path (str): 属性を取得したいファイルまたはディレクトリ。

    Returns:
        FileAttrs: 現在のパーミッション、UID、GID を格納したデータクラス。

    Raises:
        FileNotFoundError: 対象パスが存在しない場合。
        OSError: ``os.stat`` が失敗した場合。

    Examples:
        >>> import tempfile
        >>> with tempfile.NamedTemporaryFile() as tmp:  # doctest: +SKIP
        ...     attrs = get_current_attrs(tmp.name)
        ...     isinstance(attrs.mode, int)
        True
    """
    st: os.stat_result = os.stat(path, follow_symlinks=False)
    mode_now: int = stat.S_IMODE(st.st_mode)
    uid_now: int = st.st_uid
    gid_now: int = st.st_gid
    return FileAttrs(mode=mode_now, uid=uid_now, gid=gid_now)


def resolve_user(user: Optional[Union[int, str, None]]) -> Optional[int]:
    """ユーザー指定を UID に解決する。

    - 数値 UID を渡した場合はそのまま返す。
    - ユーザー名を渡した場合は ``pwd.getpwnam`` で UID を取得する。
    - 解決できない場合は ``None`` を返す。

    Args:
        user (Optional[Union[int, str, None]]): UID またはユーザー名、未指定は ``None``。

    Returns:
        Optional[int]: 解決した UID。解決不可の場合は ``None``。

    Examples:
        >>> resolve_user(0)
        0
        >>> resolve_user('nonexistent-user') is None
        True
    """
    if user is None:
        return None
    if isinstance(user, int):
        return user
    # 名前指定
    try:
        pw: pwd.struct_passwd = pwd.getpwnam(user)
        return int(pw.pw_uid)
    except Exception:
        return None


def resolve_group(group: Optional[Union[int, str, None]]) -> Optional[int]:
    """グループ指定を GID に解決する。

    数値 GID はそのまま返し、グループ名は ``grp.getgrnam`` で解決する。
    解決できない場合は ``None``。

    Args:
        group (Optional[Union[int, str, None]]): GID またはグループ名、未指定は ``None``。

    Returns:
        Optional[int]: 解決した GID。解決不可の場合は ``None``。

    Examples:
        >>> resolve_group(0)
        0
        >>> resolve_group('nonexistent-group') is None
        True
    """
    if group is None:
        return None
    if isinstance(group, int):
        return group
    try:
        gr: grp.struct_group = grp.getgrnam(group)
        return int(gr.gr_gid)
    except Exception:
        return None


def apply_attrs_best_effort(path: str, attrs: FileAttrs) -> ApplyResult:
    """可能な範囲でモード・所有者・グループを適用する。

    - どれかの操作で失敗しても残りは継続し、ベストエフォートで適用する。
    - シンボリックリンクは辿らずに ``chmod`` と ``chown`` を行う。
    - 最後に発生したエラーを ``error`` フィールドへ格納する。

    Args:
        path (str): 属性を変更したいファイルまたはディレクトリのパス。
        attrs (FileAttrs): 適用したい属性の指定。

    Returns:
        ApplyResult: 変更結果とエラー情報を含むサマリ。

    Examples:
        >>> import tempfile
        >>> with tempfile.NamedTemporaryFile() as tmp:  # doctest: +SKIP
        ...     result = apply_attrs_best_effort(tmp.name, FileAttrs(mode=0o600))
        ...     result.changed_mode
        True
    """
    changed_mode: bool = False
    changed_owner: bool = False
    changed_group: bool = False
    last_err: Optional[str] = None

    # mode
    if attrs.mode is not None:
        try:
            os.chmod(path, int(attrs.mode), follow_symlinks=False)
            changed_mode = True
        except Exception as ex1:
            last_err = f"chmod failed: {ex1!s}"

    # owner/group
    uid_target: int = -1
    gid_target: int = -1
    need_chown: bool = False
    if attrs.uid is not None:
        uid_target = int(attrs.uid)
        need_chown = True
    if attrs.gid is not None:
        gid_target = int(attrs.gid)
        need_chown = True

    if need_chown:
        try:
            os.chown(path, uid_target if uid_target >= 0 else -1, gid_target if gid_target >= 0 else -1, follow_symlinks=False)
            if attrs.uid is not None:
                changed_owner = True
            if attrs.gid is not None:
                changed_group = True
        except Exception as ex2:
            last_err = f"chown failed: {ex2!s}"

    return ApplyResult(
        path=path,
        changed_mode=changed_mode,
        changed_owner=changed_owner,
        changed_group=changed_group,
        error=last_err,
    )
