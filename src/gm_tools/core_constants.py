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
from typing import Tuple

# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------

#: Normal termination.
EXIT_OK: int = 0

#: Termination due to no hosts specified.
EXIT_ERR_NO_HOSTS: int = 1

#: Generic error (e.g., internal exception not mapped to specific code).
EXIT_ERR_GENERIC: int = 2

#: Termination due to invalid tilde user in remote path.
EXIT_ERR_TILDE_USER: int = 3

#: Termination due to invalid arguments.
EXIT_ERR_ARGS: int = 4

# ---------------------------------------------------------------------------
# Hosts file
# ---------------------------------------------------------------------------
DEFAULT_HOSTS_FILE: str = "hostfile"

# ---------------------------------------------------------------------------
# Parallelism
# ---------------------------------------------------------------------------

#: Default parallelism for host-level execution when -j/--parallel is omitted.
DEFAULT_PARALLEL_HOSTS: int = 4

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Regex pattern for sanitizing hostnames for filesystem use.
RE_SAFE_HOST_PTN: str = r"[^A-Za-z0-9._-]"

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
