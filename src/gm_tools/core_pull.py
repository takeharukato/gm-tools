# -*- coding: utf-8 -*-
"""
gm_tools.core_pull
==================

Per-host execution unit for "gather" (remote -> local pull).

Design principles
-----------------
- Library-agnostic: concrete SSH/SFTP implementations are injected via callables.
- No logging here; CLI owns logging/serialization. We only report progress via callback.
- Respect cooperative cancellation using `abort_point()` at key checkpoints.
- Do not close connections directly; register via core_ssh and let CLI cleanup.

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

# Pull callback for one item. is_dir indicates directory; implementation may no-op for dirs.
PullOne = Callable[[SFTPClientLike, str, Path, bool], None]


# ---- Helpers ----------------------------------------------------------------

def _join_remote(root: str, relpath: str) -> str:
    """Join remote root and relpath using POSIX separators."""
    if not root:
        return relpath
    if root.endswith("/"):
        return root + relpath
    return root + "/" + relpath


# ---- Main entry --------------------------------------------------------------

def run_host_gather(
    host: str,
    plan: Plan,
    *,
    remote_root: str,
    local_root: Path,
    abort_event: threading.Event,
    on_progress: Optional[OnProgress],
    open_ssh: SSHFactory,
    open_sftp: SFTPFactory,
    pull_one: PullOne,
) -> HostResult:
    """
    Execute the gather plan for a single host.

    Parameters
    ----------
    host : str
        Target host name (for connection factories and registry keys).
    plan : Plan
        Stable plan produced by core_select; len(plan) is the `total` for logging.
    remote_root : str
        POSIX-style root on the remote host to join with plan.relpath. May be "" for absolute entries.
    local_root : Path
        Local destination root; entries will be written under this root following plan.relpath.
    abort_event : threading.Event
        Cooperative cancellation flag (set by signal handlers).
    on_progress : Optional[OnProgress]
        Callback invoked after each trial with (seq, trial, processed, total). May be None.
    open_ssh : SSHFactory
        Factory to create an SSH client instance. Must NOT raise for transient connection states.
    open_sftp : SFTPFactory
        Factory to create an SFTP client using the SSH client.
    pull_one : PullOne
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

    # Ensure local root exists
    local_root = Path(local_root)
    local_root.mkdir(parents=True, exist_ok=True)

    warnings: int = 0
    errors: int = 0
    trial: int = 0
    processed: int = 0

    for seq, entry in plan.iter_seq():
        # next-trial checkpoint
        abort_point(abort_event)
        trial += 1

        # Remote path resolution (mixed-root support):
        # 1) If entry has 'remote_abs', use it (exact original absolute path).
        # 2) Else, join per-entry 'remote_root' (if any) with 'remote_rel' (if any) or fallback to relpath.
        _remote_abs = getattr(entry, "remote_abs", "") if hasattr(entry, "remote_abs") else ""
        if _remote_abs:
            remote_path: str = _remote_abs  # type: ignore[assignment]
        else:
            _per_entry_root = getattr(entry, "remote_root", "") if hasattr(entry, "remote_root") else ""
            _base_root = _per_entry_root if _per_entry_root else remote_root
            _remote_rel = getattr(entry, "remote_rel", "") if hasattr(entry, "remote_rel") else ""
            _rel_for_remote = _remote_rel if _remote_rel else entry.relpath
            remote_path: str = _join_remote(_base_root, _rel_for_remote)

        local_path: Path = (local_root / entry.relpath)

        # Long I/O before: ensure directory locally if dir (or for file's parent)
        if entry.is_dir:
            local_path.mkdir(parents=True, exist_ok=True)
        else:
            local_path.parent.mkdir(parents=True, exist_ok=True)

        # Long I/O section: the transfer itself
        try:
            abort_point(abort_event)  # before remote call
            pull_one(sftp, remote_path, local_path, entry.is_dir)
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
