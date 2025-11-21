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

"""gettext を利用した国際化初期化をまとめたモジュール。

CLI エントリポイントで :func:`setup_gettext` を 1 度呼び出し, ユーザー向けメッセージは
``_()`` でラップする。例外オブジェクトの ``str(e)`` 自体は翻訳せず, 周辺メッセージのみ
翻訳する。モジュール import 時に副作用は発生しない。

Examples:
        >>> from gm_tools.core_i18n import setup_gettext
        >>> gettext_fn, _ = setup_gettext(install_into_builtins=False)  # doctest: +SKIP
        >>> gettext_fn('Hello')  # doctest: +SKIP
        'Hello'
"""

from __future__ import annotations

import builtins
import gettext
import os
import re
import sys
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence, Tuple, Union

from . import _config

_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")

def _substitute_gnu_vars(pattern: str, mapping: Mapping[str, str]) -> str:
    """GNU 形式 ``${var}`` プレースホルダを置換する。

    対応するキーがない場合は元の構文を残す。

    Args:
        pattern (str): 置換対象文字列。
        mapping (Mapping[str, str]): 変数名から展開後文字列へのマッピング。

    Returns:
        str: プレースホルダを置換した文字列。

    Examples:
        >>> _substitute_gnu_vars('${prefix}/share', {'prefix': '/usr'})
        '/usr/share'
        >>> _substitute_gnu_vars('${prefix}/${unknown}', {'prefix': '/opt'})
        '/opt/${unknown}'
    """
    def _repl(match: re.Match[str]) -> str:
        name = match.group(1)
        return mapping.get(name, match.group(0))

    return _VAR_PATTERN.sub(_repl, pattern)


def _compute_default_locale_dir() -> Path:
    """翻訳ファイルのデフォルトディレクトリを算出する。

    ``gm_tools._config`` に定義された ``PREFIX`` や ``LOCALEDIR`` は GNU 形式の
    テンプレートを含む可能性があるため, ``${prefix}`` 等の変数を展開しながら
    実際のパスへ変換する。展開結果に未解決の ``${...}`` が残った場合はフォールバックで
    ``prefix/share/locale`` を採用する。

    フォールバックの優先順位は以下の通り。
    1. ``prefix`` は ``_config.PREFIX``, 未設定なら ``sys.prefix``。
    2. ``exec_prefix`` は ``_config.EXEC_PREFIX``, 未設定なら ``${prefix}``, さらに失敗時は ``sys.exec_prefix``。
    3. ``datarootdir`` は ``_config.DATAROOTDIR``, 未設定なら ``${prefix}/share``。
    4. ``localedir`` は ``_config.LOCALEDIR``, 未設定なら ``${datarootdir}/locale``。
    5. 展開後に ``${...}`` が残っていれば ``prefix/share/locale`` へフォールバックする。

    Returns:
        Path: 展開後のロケールディレクトリ。

    Examples:
        >>> isinstance(_compute_default_locale_dir(), Path)
        True
    """
    # 1) prefix / exec_prefix を _config 優先で取得
    prefix = getattr(_config, "PREFIX", sys.prefix)
    exec_pattern = getattr(_config, "EXEC_PREFIX", "") or "${prefix}"

    # exec_prefix の中の ${prefix} などをまず展開
    exec_prefix = _substitute_gnu_vars(exec_pattern, {"prefix": prefix}) or sys.exec_prefix

    raw_locale = getattr(_config, "LOCALEDIR", "") or ""
    raw_dataroot = getattr(_config, "DATAROOTDIR", "") or ""

    # 2) datarootdir: from config or default "${prefix}/share"
    dataroot_pattern = raw_dataroot or "${prefix}/share"
    dataroot_mapping: dict[str, str] = {
        "prefix": prefix,
        "exec_prefix": exec_prefix,
    }
    datarootdir_str = _substitute_gnu_vars(dataroot_pattern, dataroot_mapping)

    # まだ ${...} が残っていれば prefix/share にフォールバック
    if "${" in datarootdir_str:
        datarootdir_str = os.path.join(prefix, "share")

    # 3) localedir: from config or default "${datarootdir}/locale"
    locale_pattern = raw_locale or "${datarootdir}/locale"
    locale_mapping: dict[str, str] = {
        "prefix": prefix,
        "exec_prefix": exec_prefix,
        "datarootdir": datarootdir_str,
    }
    locale_dir_str = _substitute_gnu_vars(locale_pattern, locale_mapping)

    # 最後の保険: まだ ${...} が残っていれば datarootdir/locale にフォールバック
    if "${" in locale_dir_str:
        locale_dir_str = os.path.join(datarootdir_str, "locale")

    return Path(locale_dir_str)


def setup_gettext(
    *,
    domain: Union[str, None] = None,
    locale_dir: Union[Path, str, None] = None,
    languages: Optional[Sequence[str]] = None,
    install_into_builtins: bool = True,
) -> Tuple[Callable[[str], str], Callable[[str, str, int], str]]:
    """gettext を初期化し翻訳関数を返す。

    Args:
        domain (Union[str, None]): 使用する gettext ドメイン。``None`` なら設定値を利用。
        locale_dir (Union[Path, str, None]): 翻訳ファイルを格納したディレクトリ。
            ``None`` の場合は :func:`_compute_default_locale_dir` で決定する。
        languages (Optional[Sequence[str]]): 優先したい言語コードリスト。
            ``None`` の場合は環境変数に依存する。
        install_into_builtins (bool): ``True`` の場合は ``_`` と ``ngettext`` を ``builtins`` に登録する。

    Returns:
        Tuple[Callable[[str], str], Callable[[str, str, int], str]]: ``gettext`` と ``ngettext`` のコール可能。

    Examples:
        >>> gettext_fn, ngettext_fn = setup_gettext(  # doctest: +SKIP
        ...     domain='gm-tools',
        ...     locale_dir='/usr/share/locale',
        ...     languages=['ja_JP', 'ja'],
        ...     install_into_builtins=False,
        ... )
        >>> gettext_fn('hello')  # doctest: +SKIP
        'hello'
    """
    # Domain: _config.DOMAIN をデフォルトに
    if domain is None:
        effective_domain = _config.DOMAIN
    else:
        effective_domain = domain

    # Locale dir: 明示指定があればそれを優先
    if locale_dir is None:
        effective_locale_dir = _compute_default_locale_dir()
    else:
        effective_locale_dir = Path(locale_dir)

    try:
        trans = gettext.translation(
            domain=effective_domain,
            localedir=str(effective_locale_dir),
            languages=list(languages) if languages is not None else None,
            fallback=True,  # Use NullTranslations when catalogs are missing.
        )
    except Exception:
        # Extremely defensive: even if something goes wrong, do not crash i18n init.
        trans = gettext.NullTranslations()

    # Acquire callables
    gettext_fn: Callable[[str], str] = trans.gettext
    ngettext_fn: Callable[[str, str, int], str] = trans.ngettext  # type: ignore[assignment]

    if install_into_builtins:
        # Install '_' and 'ngettext' globally for convenience.
        trans.install(names=("ngettext",))  # installs _ by default + ngettext alias
        builtins._ = gettext_fn       # type: ignore[attr-defined]
        builtins.ngettext = ngettext_fn  # type: ignore[attr-defined]

    return gettext_fn, ngettext_fn


__all__ = ["setup_gettext"]
