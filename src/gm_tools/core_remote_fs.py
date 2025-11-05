# -*- coding: utf-8 -*-
"""
gm_tools.core_remote_fs
=======================

Best-effort management of *remote* temporary files per host.

Goals
-----
- Register remote temporary paths created during gather/scatter.
- Provide idempotent cleanup APIs (safe to call multiple times).
- Keep library-agnostic: callers supply *remover* callables that perform the
  actual remote deletion (e.g., via SFTP/SSH).
- Encourage the call pattern: "check abort -> create -> register -> I/O -> finally cleanup".

This module performs no side effects on import.
"""

from __future__ import annotations

import threading
from typing import Callable, Dict, Iterable, List, Set
from .core_ssh import SFTPClientLike

# ---- Types ------------------------------------------------------------------

# Remover receives a single remote absolute path and deletes it (best-effort).
RemoteRemover = Callable[[str], None]


# ---- Registry ----------------------------------------------------------------

class _PerHost:
    __slots__ = ("temps",)

    def __init__(self) -> None:
        # Set of remote absolute paths scheduled for deletion.
        self.temps: Set[str] = set()


_lock: threading.Lock = threading.Lock()
_registry: Dict[str, _PerHost] = {}  # host -> _PerHost


def _bucket(host: str) -> _PerHost:
    with _lock:
        b = _registry.get(host)
        if b is None:
            b = _PerHost()
            _registry[host] = b
        return b


def register_remote_temp(host: str, path: str) -> None:
    """
    Register a remote temporary file/directory path for later cleanup.
    - `path` must be a string (remote absolute path is recommended).
    - Idempotent: registering the same path multiple times is fine.
    """
    b = _bucket(host)
    with _lock:
        b.temps.add(path)


def register_remote_temps(host: str, paths: Iterable[str]) -> None:
    """Register multiple remote temporary paths at once (idempotent)."""
    b = _bucket(host)
    with _lock:
        for p in paths:
            b.temps.add(p)


def create_remote_temp(host: str, maker: Callable[[], str]) -> str:
    """
    Helper: create a single remote temp via `maker()` and register it.
    - The `maker` callable should create the remote resource and return its path.
    - The caller remains responsible for abort checkpoints around this call.
    """
    path: str = maker()
    register_remote_temp(host, path)
    return path


def cleanup_remote_temp(host: str, remover: RemoteRemover) -> None:
    """
    Delete all registered remote temps for `host` using the given `remover`.
    - Idempotent: paths are removed from the registry as we attempt deletion.
    - Best-effort: exceptions from the remover are swallowed to allow progress.
    """
    with _lock:
        b = _registry.get(host)
        paths: List[str] = list(b.temps) if b is not None else []

    if not paths:
        return

    failures: List[str] = []
    for p in paths:
        try:
            remover(p)
        except Exception:
            # Keep the failed path to try later
            failures.append(p)

    # Update registry with remaining failures
    if b is not None:
        with _lock:
            if failures:
                b.temps = set(failures)
            else:
                # All cleared -> remove bucket
                _registry.pop(host, None)


def cleanup_all_remote_temps(host_to_remover: Dict[str, RemoteRemover]) -> None:
    """
    Delete registered remote temps for multiple hosts.
    - host_to_remover: mapping from host -> remover callable
    - Hosts without a remover are skipped (left for later cleanup).
    """
    with _lock:
        hosts = list(_registry.keys())
    for host in hosts:
        remover = host_to_remover.get(host)
        if remover is not None:
            cleanup_remote_temp(host, remover)


def sftp_exists(sftp_client: SFTPClientLike, path: str) -> bool:
    try:
        sftp_client.stat(path)
        return True
    except Exception:
        return False

def sftp_isdir(sftp_client: SFTPClientLike, path: str) -> bool:
    import stat
    try:
        st = sftp_client.stat(path)
        return stat.S_ISDIR(st.st_mode)
    except Exception:
        return False

def sftp_isfile(sftp_client: SFTPClientLike, path: str) -> bool:
    import stat
    try:
        st = sftp_client.stat(path)
        return stat.S_ISREG(st.st_mode)
    except Exception:
        return False

def sftp_islink(sftp_client: SFTPClientLike, path: str) -> bool:
    import stat
    try:
        st = sftp_client.lstat(path)
        return stat.S_ISLNK(st.st_mode)
    except Exception:
        return False


__all__ = [
    "RemoteRemover",
    "register_remote_temp",
    "register_remote_temps",
    "create_remote_temp",
    "cleanup_remote_temp",
    "cleanup_all_remote_temps",
    "sftp_exists",
    "sftp_isdir",
    "sftp_isfile",
    "sftp_islink",
]
