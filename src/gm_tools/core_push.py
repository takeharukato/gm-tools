# -*- coding: utf-8 -*-
"""
gm_tools.core_push
==================

Per-host execution unit for "scatter" (local -> remote push).

Design principles
-----------------
- Library-agnostic: concrete SSH/SFTP implementations are injected via callables.
- No logging here; CLI owns logging/serialization. We only report progress via callback.
- Respect cooperative cancellation using `abort_point()` at key checkpoints.
- Do not close connections directly; register via core_ssh and let CLI cleanup.
- Direct remote path creation (e.g., mkdir -p) は本モジュールでは行わず、push_one 側で実施可とする。
  （実装を簡潔に保つため。必要なら ensure_remote_dir などのコールバックを将来追加する）

This module performs no side effects on import.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .core_select import Plan
from .core_ssh import (
    SSHClientLike,
    SFTPClientLike,
    abort_point,
    register_connection,
    register_sftp,
    CancelledError,
)


# ---- Result model ------------------------------------------------------------

@dataclass(frozen=True)
class HostResult:
    """Per-host aggregated outcome."""
    warnings: int
    errors: int
    processed: int
    trial: int


# ---- Callbacks / factories ---------------------------------------------------

# Progress callback: (seq, trial, processed, total)
OnProgress = Callable[[int, int, int, int], None]

# Injected factories to construct SSH/SFTP objects for a host.
SSHFactory = Callable[[str], SSHClientLike]
SFTPFactory = Callable[[SSHClientLike], SFTPClientLike]

# Push callback for one item. is_dir indicates directory; implementation may no-op for dirs.
PushOne = Callable[[SFTPClientLike, Path, str, bool], None]


# ---- Helpers ----------------------------------------------------------------

def _join_remote(root: str, relpath: str) -> str:
    """Join remote root and relpath using POSIX separators."""
    if not root:
        return relpath
    if root.endswith("/"):
        return root + relpath
    return root + "/" + relpath


# ---- Main entry --------------------------------------------------------------

def run_host_scatter(
    host: str,
    plan: Plan,
    *,
    local_root: Path,
    remote_root: str,
    abort_event: threading.Event,
    on_progress: Optional[OnProgress],
    open_ssh: SSHFactory,
    open_sftp: SFTPFactory,
    push_one: PushOne,
) -> HostResult:
    """
    Execute the scatter plan for a single host.

    Parameters
    ----------
    host : str
        Target host name (for connection factories and registry keys).
    plan : Plan
        Stable plan produced by core_select; len(plan) is the `total` for logging.
    local_root : Path
        Local source root; entries will be read under this root following plan.relpath.
    remote_root : str
        POSIX-style root on the remote host to join with plan.relpath. May be "" for absolute entries.
    abort_event : threading.Event
        Cooperative cancellation flag (set by signal handlers).
    on_progress : Optional[OnProgress]
        Callback invoked after each trial with (seq, trial, processed, total). May be None.
    open_ssh : SSHFactory
        Factory to create an SSH client instance.
    open_sftp : SFTPFactory
        Factory to create an SFTP client using the SSH client.
    push_one : PushOne
        Function that performs the actual transfer for one item.

    Returns
    -------
    HostResult
        Aggregated result: warnings, errors, processed, trial.
    """
    total: int = len(plan)

    # Abort checkpoint before any network I/O
    abort_point(abort_event)

    # Establish connections (library-agnostic) and register for idempotent cleanup.
    ssh = open_ssh(host)
    register_connection(host, ssh)
    sftp = open_sftp(ssh)
    register_sftp(host, sftp)

    # Normalize local root
    local_root = Path(local_root)

    warnings: int = 0
    errors: int = 0
    trial: int = 0
    processed: int = 0

    for seq, entry in plan.iter_seq():
        # next-trial checkpoint
        abort_point(abort_event)
        trial += 1

        local_path: Path = (local_root / entry.relpath)
        remote_path: str = _join_remote(remote_root, entry.relpath)

        # Long I/O section: the transfer itself
        try:
            abort_point(abort_event)  # before remote call
            push_one(sftp, local_path, remote_path, entry.is_dir)
            abort_point(abort_event)  # after remote call
            processed += 1
        except CancelledError:
            # Propagate cancellation (upper layer will finalize/cleanup)
            raise
        except Exception:
            # Count as error and continue to next
            errors += 1

        # Progress notification
        if on_progress is not None:
            on_progress(seq, trial, processed, total)

    return HostResult(warnings=warnings, errors=errors, processed=processed, trial=trial)
