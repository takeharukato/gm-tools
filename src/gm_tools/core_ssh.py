# -*- coding: utf-8 -*-
"""
gm_tools.core_ssh
=================

SSH/SFTP connection and channel lifecycle helpers for gm-tools.

Goals:
- Centralize registration and idempotent cleanup of SSH/SFTP resources per host.
- Provide simple abort checkpoints to be called "just before next trial" and
  "before/after long I/O".
- Avoid hard dependency on a specific SSH library; rely on structural typing.

This module performs no side effects on import.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Dict, Protocol, runtime_checkable, Optional, Tuple, List, Any

DEFAULT_SSH_PORT: int = 22
DEFAULT_TIMEOUT: float = 30.0

# ---- Structural protocols ---------------------------------------------------

@runtime_checkable
class Closeable(Protocol):
    def close(self) -> None: ...  # noqa: D401


@runtime_checkable
class ChannelLike(Closeable, Protocol):
    # Subset used by wait loops in callers (paramiko-like)
    def exit_status_ready(self) -> bool: ...  # noqa: D401
    def recv_ready(self) -> bool: ...  # noqa: D401
    def recv(self, nbytes: int) -> bytes: ...  # noqa: D401
    def recv_stderr_ready(self) -> bool: ...  # noqa: D401
    def recv_stderr(self, nbytes: int) -> bytes: ...  # noqa: D401

# Paramiko SFTPFile and SFTPClient like protocols
@runtime_checkable
class SFTPAttributesLike(Protocol):
    """Minimal subset of paramiko.SFTPAttributes used by our code."""
    st_mode: int  # must exist; used for S_ISDIR/S_ISREG checks

@runtime_checkable
class SFTPFileLike(Protocol):
    def write(self, data: bytes) -> int: ...
    def read(self, size: int = ...) -> bytes: ...
    def close(self) -> None: ...
    def __enter__(self) -> "SFTPFileLike": ...
    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None: ...

@runtime_checkable
class SFTPClientLike(Protocol):
    def open(self, path: str, mode: str = ...) -> SFTPFileLike: ...
    def put(self, localpath: str, remotepath: str) -> None: ...
    def get(self, remotepath: str, localpath: str) -> None: ...
    def listdir(self, path: str) -> List[str]: ...
    def stat(self, path: str) -> SFTPAttributesLike: ...
    def lstat(self, path: str) -> SFTPAttributesLike: ...
    def close(self) -> None: ...

@runtime_checkable
class SSHClientLike(Protocol):
    def exec_command(self, command: str, timeout: Optional[float] = ...) -> Tuple[Any, Any, Any]: ...
    def open_sftp(self) -> SFTPClientLike: ...
    def close(self) -> None: ...

# ---- Registry ---------------------------------------------------------------

class _PerHost:
    __slots__ = ("conns", "sftps", "chans")

    def __init__(self) -> None:
        # Use lists to avoid hashability requirements (e.g., many Paramiko objects are unhashable).
        self.conns: list[Closeable] = []
        self.sftps: list[Closeable] = []
        self.chans: list[Closeable] = []


_lock: threading.Lock = threading.Lock()
_registry: Dict[str, _PerHost] = {}  # host -> resources


def _get_bucket(host: str) -> _PerHost:
    with _lock:
        bucket = _registry.get(host)
        if bucket is None:
            bucket = _PerHost()
            _registry[host] = bucket
        return bucket


def register_connection(host: str, conn: SSHClientLike) -> None:
    """Register an SSH client connection for later idempotent close."""
    bucket = _get_bucket(host)
    with _lock:
        if conn not in bucket.conns:  # identity-based dedup
            bucket.conns.append(conn)  # type: ignore[arg-type]


def register_sftp(host: str, sftp: SFTPClientLike) -> None:
    """Register an SFTP client for later idempotent close."""
    bucket = _get_bucket(host)
    with _lock:
        if sftp not in bucket.sftps:
            bucket.sftps.append(sftp)  # type: ignore[arg-type]


def register_channel(host: str, chan: ChannelLike) -> None:
    """Register a channel object for later idempotent close."""
    bucket = _get_bucket(host)
    with _lock:
        if chan not in bucket.chans:
            bucket.chans.append(chan)  # type: ignore[arg-type]


def _safe_close(obj: Closeable) -> None:
    try:
        obj.close()
    except Exception:
        # Best-effort cleanup: never raise
        pass


def close_connections(host: str) -> None:
    """
    Close all registered channels, sftps and connections for a host (idempotent).
    Safe to call multiple times, even concurrently.
    """
    with _lock:
        bucket = _registry.get(host)
    if bucket is None:
        return

    # Close in the order: channels -> sftps -> connections
    # (channels depend on connections; sftps depend on the underlying connection)
    for obj in list(bucket.chans):
        _safe_close(obj)
    for obj in list(bucket.sftps):
        _safe_close(obj)
    for obj in list(bucket.conns):
        _safe_close(obj)

    # Remove emptied bucket
    with _lock:
        _registry.pop(host, None)


def close_all() -> None:
    """Close resources for all hosts (idempotent)."""
    with _lock:
        hosts = list(_registry.keys())
    for h in hosts:
        close_connections(h)


# ---- Abort checkpoints ------------------------------------------------------

class CancelledError(RuntimeError):
    """Raised when an abort has been requested and an operation should stop."""


def abort_point(abort_event: threading.Event) -> None:
    """
    Cooperative cancellation checkpoint.

    Call at: "next trial just before start", and before/after long I/O.
    If the abort flag is set, raises CancelledError for callers to handle.
    """
    if abort_event.is_set():
        raise CancelledError("operation aborted by user request")

@dataclass
class SSHConfig:
    host: str
    port: int = DEFAULT_SSH_PORT
    ssh_user: Optional[str] = None
    key_filename: Optional[str] = None
    password: Optional[str] = None
    timeout: float = DEFAULT_TIMEOUT
    strict_host_key_checking: bool = False

def ssh_open(cfg: SSHConfig, *, debug_print: bool = False) -> SSHClientLike:
    """
    Paramiko で接続を張る薄いヘルパ。
    - 返り値は SSHClientLike として扱われる ( register_connection で登録 )
    """
    try:
        import paramiko  # type: ignore
    except Exception as e:
        raise RuntimeError("Paramiko is required for ssh_open()") from e

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(
        paramiko.AutoAddPolicy() if not cfg.strict_host_key_checking else paramiko.RejectPolicy()
    )
    client.connect(
        cfg.host,
        port=int(cfg.port),
        username=cfg.ssh_user,
        key_filename=cfg.key_filename,
        password=cfg.password,
        timeout=float(cfg.timeout),
        look_for_keys=True,
        allow_agent=True,
    )
    # 互換: 呼び出し側が明示 close しない前提だったため, 登録して idempotent close させる
    register_connection(cfg.host, client)  # type: ignore[arg-type]
    return client  # type: ignore[return-value]

def finalize_sockets() -> None:
    """
    Step4 互換: プロセス終了時のソケット整理。新実装では close_all() に委譲。
    """
    try:
        close_all()
    except Exception:
        pass

__all__ = [
    "SSHClientLike",
    "SFTPClientLike",
    "ChannelLike",
    "register_connection",
    "register_sftp",
    "register_channel",
    "close_connections",
    "close_all",
    "abort_point",
    "CancelledError",
]
