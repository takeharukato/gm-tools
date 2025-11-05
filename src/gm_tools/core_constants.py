# -*- coding: utf-8 -*-
"""
gm_tools.core_constants
=======================

Centralized, typed constants for gm-tools.

Policy:
- No magic numbers/strings in code outside this module.
- Keep names stable; add new constants here instead of scattering literals.
- NOTE: As requested, we *do not* use typing.Final for these constants.
"""

from __future__ import annotations

from pathlib import Path
from typing import Tuple

# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------

#: Normal termination.
EXIT_OK: int = 0

#: Termination with one or more errors detected during host processing.
EXIT_ERR: int = 2

# ---------------------------------------------------------------------------
# Parallelism
# ---------------------------------------------------------------------------

#: Default parallelism for host-level execution when -j/--parallel is omitted.
DEFAULT_PARALLEL_HOSTS: int = 4

# ---------------------------------------------------------------------------
# Internationalization (i18n)
# ---------------------------------------------------------------------------

#: gettext domain name for gm-tools.
LOG_DOMAIN: str = "gm_tools"

#: Directory where compiled message catalogs (*.mo) are stored.
#: By default, it expects "<package_root>/locale".
LOCALE_DIR: Path = Path(__file__).resolve().parent / "locale"

# ---------------------------------------------------------------------------
# Logging keys and schema
# ---------------------------------------------------------------------------

#: Fixed-leading keys in log records (key order requirement).
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

#: Optional keys that may appear depending on context.
KEYS_OPTIONAL: Tuple[str, ...] = (
    "warnings",
    "errors",
    "duration",
    "seq",
)

__all__ = [
    "EXIT_OK",
    "EXIT_ERR",
    "DEFAULT_PARALLEL_HOSTS",
    "LOG_DOMAIN",
    "LOCALE_DIR",
    "KEYS_PREFIX",
    "KEYS_OPTIONAL",
]
