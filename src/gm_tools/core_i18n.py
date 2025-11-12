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
from pathlib import Path
from typing import Callable, Optional, Sequence, Tuple

from .core_constants import LOG_DOMAIN, LOCALE_DIR


def setup_gettext(
    *,
    domain: str = LOG_DOMAIN,
    locale_dir: Path = LOCALE_DIR,
    languages: Optional[Sequence[str]] = None,
    install_into_builtins: bool = True,
) -> Tuple[Callable[[str], str], Callable[[str, str, int], str]]:
    """
    Initialize gettext and return translation callables.

    Parameters
    ----------
    domain : str
        gettext domain name (defaults to core_constants.LOG_DOMAIN).
    locale_dir : Path
        Directory path that contains locale/<lang>/LC_MESSAGES/<domain>.mo
        (defaults to core_constants.LOCALE_DIR).
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

    Notes
    -----
    - If translation catalogs are missing, falls back to identity translations.
    - This function does not attempt to translate *exception payloads*.
      Callers should join `str(e)` directly to translated wrappers.
    """
    # Resolve directory to str for gettext API; allow Path inputs.
    localedir_str: str = str(locale_dir)

    try:
        trans = gettext.translation(
            domain=domain,
            localedir=localedir_str,
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
        # This affects modules loaded after this point.
        trans.install(names=("ngettext",))  # installs _ by default + ngettext alias
        # Ensure explicit builtins binding for type checkers / clarity.
        builtins._ = gettext_fn  # type: ignore[attr-defined]
        builtins.ngettext = ngettext_fn  # type: ignore[attr-defined]

    return gettext_fn, ngettext_fn


__all__ = ["setup_gettext"]
