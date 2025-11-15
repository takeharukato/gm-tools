# -*- coding: utf-8 -*-
"""
gm_tools.scatter_parallel
=========================

C-1 helper: host-parallel execution, logging aggregation, exit code decision (scatter).
- Host-level parallelism only (per-host internals are sequential).
- Logging/summary/exit-code are unified with gather side.
"""

from __future__ import annotations

import concurrent.futures as _fut
import threading
from pathlib import Path as _Path
from typing import Dict, Optional, Sequence

from .core_constants import DEFAULT_PARALLEL_HOSTS, EXIT_OK, EXIT_ERR_GENERIC
from .core_logging import HostLogAggregator, init_logging, shutdown_logging
from .core_ssh import CancelledError, close_all
from .core_remote_fs import cleanup_all_remote_temps, RemoteRemover
from .core_archive import cleanup_all_local_temps
from .core_select import Plan

# Type aliases matching per-host scatter
from .core_push import HostResult as _HostResult
from .core_push import OnProgress as _OnProgress
from .core_push import SSHFactory as _SSHFactory
from .core_push import SFTPFactory as _SFTPFactory
from .core_push import PushOne as _PushOne
from .core_push import run_host_scatter as _run_host_scatter

from .core_signal_handling import GracefulStop

def _clamp_parallel(n: int) -> int:
    return 1 if n <= 0 else n


def execute(
    *,
    hosts: Sequence[str],
    plan: Optional[Plan] = None,
    plan_per_host: Optional[Dict[str, Plan]] = None,
    remote_root: str = "",
    src_root: _Path,
    parallel: int = DEFAULT_PARALLEL_HOSTS,
    verbose: bool = False,
    # cooperative cancellation (legacy; Step6 では GracefulStop.abort_event を使用)
    abort_event: Optional[threading.Event] = None,
    # injected factories (existing implementations from CLI)
    open_ssh: _SSHFactory,
    open_sftp: _SFTPFactory,
    push_one: _PushOne,
    push_one_map: Optional[Dict[str, _PushOne]] = None,
    join_host_dir: bool = True,
    remote_removers: Optional[Dict[str, RemoteRemover]] = None,
    do_cleanup_local: bool = False,
    do_cleanup_remote: bool = False,
    # GracefulStop orchestration : optional external coordinator
    graceful_stop: Optional[GracefulStop] = None,
    # reserved for future use; signal handlers are registered by CLI side
    register_signals: bool = False,
) -> int:
    """
    Run host-parallel scatter with logging aggregation and return process exit code.
    """
    # logging (single consumer)
    init_logging(verbose=verbose)
    aggr = HostLogAggregator(op="scatter")

    # ---- GracefulStop orchestration ----
    gs: GracefulStop
    if graceful_stop is not None:
        gs = graceful_stop
    else:
        gs = GracefulStop()

    # cooperative cancellation: always use GracefulStop.abort_event
    abort_event_effective: threading.Event = gs.abort_event

    # Cleanup callbacks (registered in reverse of desired execution order,
    # because GracefulStop.run_cleanups() runs them in LIFO order).
    def _cleanup_remote() -> None:
        if do_cleanup_remote and remote_removers:
            try:
                cleanup_all_remote_temps(remote_removers)
            except Exception:
                # best-effort cleanup; never raise from here
                pass

    def _cleanup_local() -> None:
        if do_cleanup_local:
            try:
                cleanup_all_local_temps()
            except Exception:
                pass

    def _cleanup_close_all() -> None:
        try:
            close_all()
        except Exception:
            pass

    def _cleanup_summary() -> None:
        # Summary and logging shutdown
        aggr.summary()
        shutdown_logging()

    # register in reverse of desired execution order
    # desired runtime order:
    #   remote cleanup -> local cleanup -> close_all -> summary+shutdown
    # run_cleanups() uses reversed(self._cleanups), so register:
    gs.register_cleanup(_cleanup_summary)
    gs.register_cleanup(_cleanup_close_all)
    gs.register_cleanup(_cleanup_local)
    gs.register_cleanup(_cleanup_remote)
    def _make_on_progress(host: str, total: int) -> _OnProgress:
        def _on(seq: int, trial: int, processed: int, total_in: int) -> None:
            aggr.progress(host, seq=seq, trial=trial, processed=processed, total=total)
        return _on

    max_workers: int = _clamp_parallel(int(parallel))
    futures: list[tuple[str, _fut.Future[_HostResult]]] = []
    errors_any: bool = False

    try:
        with _fut.ThreadPoolExecutor(max_workers=max_workers) as ex:
            for host in hosts:
                _plan: Plan = plan_per_host[host] if plan_per_host is not None else plan  # type: ignore[assignment]
                assert _plan is not None, "execute(): plan or plan_per_host must be provided"
                aggr.start_host(host, total=len(_plan))
                _remote_root: str = (remote_root.rstrip("/") + f"/{host}") if join_host_dir else remote_root
                _po: _PushOne = push_one_map.get(host, push_one) if push_one_map else push_one
                fut = ex.submit(
                    _run_host_scatter,
                    host,
                    _plan,
                    remote_root=_remote_root,
                    local_root=src_root,
                    abort_event=abort_event_effective,
                    on_progress=_make_on_progress(host, len(_plan)),
                    open_ssh=open_ssh,
                    open_sftp=open_sftp,
                    push_one=_po,
                )
                futures.append((host, fut))

            for host, fut in futures:
                try:
                    res: _HostResult = fut.result()
                    aggr.done_host(host, warnings=res.warnings, errors=res.errors)
                    if res.errors > 0:
                        errors_any = True
                except CancelledError:
                    aggr.done_host(host, warnings=0, errors=1)
                    errors_any = True
                except Exception:
                    aggr.done_host(host, warnings=0, errors=1)
                    errors_any = True
    finally:
        # Cleanup phase (idempotent best-effort, centralized via GracefulStop)
        gs.run_cleanups()

    return EXIT_ERR_GENERIC if errors_any else EXIT_OK
