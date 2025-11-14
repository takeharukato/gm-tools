# -*- coding: utf-8 -*-
"""
gm_tools.core_i18n
==================

Centralized initialization for gettext-based internationalization.

Policy:
- Call `setup_gettext()` once at program start (CLI entry).
- All user-facing messages should be wrapped with `_()` (gettext).
- *Exception payloads* (e.g., `str(e)`) are NOT translated; wrap only the
  surrounding messages.
- This module performs no side effects on import.
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
    """
    Substitute GNU-style ${var} placeholders using the given mapping.

    Unknown variables are left as-is (i.e., ${unknown} stays unchanged).
    """
    def _repl(match: re.Match[str]) -> str:
        name = match.group(1)
        return mapping.get(name, match.group(0))

    return _VAR_PATTERN.sub(_repl, pattern)


def _compute_default_locale_dir() -> Path:
    """
    Compute the default locale directory from gm_tools._config.

    This interprets _config.LOCALEDIR / _config.DATAROOTDIR /
    _config.PREFIX / _config.EXEC_PREFIX as *templates* that may contain
    GNU-style placeholders like ${prefix}, ${exec_prefix}, ${datarootdir},
    and converts them into an absolute filesystem path.

    Fallback order:
    - prefix:       _config.PREFIX      or sys.prefix
    - exec_prefix:  _config.EXEC_PREFIX or sys.exec_prefix
    - datarootdir:  _config.DATAROOTDIR or "${prefix}/share"
    - localedir:    _config.LOCALEDIR   or "${datarootdir}/locale"
    - if any ${...} remains after substitution, fall back to
      prefix + "share/locale".
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
    """
    Initialize gettext and return translation callables.

    Parameters
    ----------
    domain : Union[str, None]
        gettext domain name (defaults to configured DOMAIN).
    locale_dir : Union[Path, str, None]
        Directory path that contains locale/<lang>/LC_MESSAGES/<domain>.mo.
        When None (default), this is derived from gm_tools._config
        (LOCALEDIR / DATAROOTDIR / PREFIX / EXEC_PREFIX).
    languages : Optional[Sequence[str]]
        Preferred languages (e.g., ["ja_JP", "ja", "en"]). If None, gettext
        will use environment settings.
    install_into_builtins : bool
        When True (default), install `_` and `ngettext` into builtins so that
        modules can simply call `_('message')` without explicit imports.

    Returns
    -------
    (gettext_fn, ngettext_fn) : tuple[Callable, Callable]
        - gettext_fn(msgid) -> str
        - ngettext_fn(singular, plural, n) -> str
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
