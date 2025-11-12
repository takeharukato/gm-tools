# -*- coding: utf-8 -*-
"""
gm_tools.core_push
==================

Per-host execution unit for "scatter" (local -> remote push).

Design principles
-----------------
- Library-agnostic: concrete SSH/SFTP implementations are injected via callables.
- No logging here; CLI/parallel layer owns logging/serialization. Only progress via callback.
- Respect cooperative cancellation using `abort_point()` at key checkpoints.
- Do not close connections directly; they are owned by the caller.
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

# Push callback for one item.
# remote_root is the per-host remote base directory where relpaths are placed (e.g., DEST or DEST/<HOST>).
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
    remote_root: str,
    local_root: Path,  # unused; plan entries store absolute local paths
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
        Stable plan produced by caller; len(plan) is the `total` for logging.
    remote_root : str
        Remote base directory (e.g., DEST or DEST/<HOST>).
    local_root : Path
        Unused (kept for symmetry); actual local paths come from PlanEntry.path.
    abort_event : threading.Event
        Cooperative cancellation flag (set by signal handlers).
    on_progress : Optional[OnProgress]
        Callback invoked after each trial with (seq, trial, processed, total). May be None.
    open_ssh : SSHFactory
        Factory to create an SSH client instance. Must NOT raise for transient connection states.
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

    # Establish connections and register for finalization (idempotent cleanup by upper layer)
    ssh = open_ssh(host)
    register_connection(host, ssh)
    sftp = open_sftp(ssh)
    register_sftp(host, sftp)

    warnings: int = 0
    errors: int = 0
    trial: int = 0
    processed: int = 0

    for seq, entry in plan.iter_seq():
        abort_point(abort_event)
        trial += 1

        # Remote path = remote_root + entry.relpath (used by some push_one implementations)
        remote_path: str = _join_remote(remote_root, entry.relpath)
        local_path: Path = entry.path

        try:
            abort_point(abort_event)  # before remote call
            push_one(sftp, local_path, remote_path, entry.is_dir)
            abort_point(abort_event)  # after remote call
            processed += 1
        except CancelledError:
            raise
        except Exception:
            errors += 1

        if on_progress is not None:
            on_progress(seq, trial, processed, total)

    return HostResult(warnings=warnings, errors=errors, processed=processed, trial=trial)
