# -*- coding: utf-8 -*-
"""
gm_tools.gather_parallel
========================

C-1 helper: host-parallel execution, logging aggregation, exit code decision.
This module is designed to minimize invasive changes in existing gather_cli.py.
- No option parsing here; caller passes already-parsed values and callables.
- No behavior changes for --pack/-x/sudo/SELinux/permissions; those live in the injected callables.
"""

from __future__ import annotations

import concurrent.futures as _fut
import threading
from typing import Dict, Optional, Sequence

from pathlib import Path as _Path

from .core_constants import DEFAULT_PARALLEL_HOSTS, EXIT_OK, EXIT_ERR_GENERIC
from .core_logging import HostLogAggregator, init_logging, shutdown_logging
from .core_select import Plan
from .core_ssh import CancelledError, close_all
from .core_remote_fs import cleanup_all_remote_temps, RemoteRemover
from .core_archive import cleanup_all_local_temps

# Type aliases matching per-host gather
from .core_pull import HostResult as _HostResult
from .core_pull import OnProgress as _OnProgress
from .core_pull import SSHFactory as _SSHFactory
from .core_pull import SFTPFactory as _SFTPFactory
from .core_pull import PullOne as _PullOne
from .core_pull import run_host_gather as _run_host_gather


def _clamp_parallel(n: int) -> int:
    return 1 if n <= 0 else n


def execute(
    *,
    hosts: Sequence[str],
    plan: Optional[Plan] = None,
    plan_per_host: Optional[Dict[str, Plan]] = None,
    remote_root: str = "",
    dest_root: _Path,
    parallel: int = DEFAULT_PARALLEL_HOSTS,
    verbose: bool = False,
    # cooperative cancellation (must be created and wired by CLI)
    abort_event: threading.Event,
    # injected factories (existing implementations from gather_cli)
    open_ssh: _SSHFactory,
    open_sftp: _SFTPFactory,
    pull_one: _PullOne,
    pull_one_map: Optional[Dict[str, _PullOne]] = None,
    # destination layout compatibility: if False, put items directly under dest_root
    join_host_dir: bool = True,
    # cleanup policy (delegated/controllable from CLI)
    remote_removers: Optional[Dict[str, RemoteRemover]] = None,
    do_cleanup_local: bool = False,
    do_cleanup_remote: bool = False,
) -> int:
    """
    Run host-parallel gather with logging aggregation and return process exit code.

    Parameters are intentionally explicit to avoid coupling with gather_cli internals.
    """
    # logging (single consumer)
    init_logging(verbose=verbose)
    aggr = HostLogAggregator(op="gather")

    # per-host progress closures
    def _make_on_progress(host: str, total: int) -> _OnProgress:
        def _on(seq: int, trial: int, processed: int, total_in: int) -> None:
            # trust total_in from per-host unit, but use total for stable denominator
            aggr.progress(host, seq=seq, trial=trial, processed=processed, total=total)
        return _on

    max_workers = _clamp_parallel(int(parallel))
    # List of (host, Future[HostResult])
    futures: list[tuple[str, _fut.Future[_HostResult]]] = []
    errors_any = False
    try:
        with _fut.ThreadPoolExecutor(max_workers=max_workers) as ex:
            for host in hosts:
                _plan = plan_per_host[host] if plan_per_host is not None else plan
                assert _plan is not None, "execute(): plan or plan_per_host must be provided"
                aggr.start_host(host, total=len(_plan))
                per_host_dest = (dest_root / host) if join_host_dir else dest_root
                # host毎の pull_one を選択 ( --pack のときは1回で完了する host-bound pull_one を注入 )
                _po = pull_one_map.get(host, pull_one) if pull_one_map else pull_one
                fut = ex.submit(
                    _run_host_gather,
                    host,
                    _plan,
                    remote_root=remote_root,
                    local_root=per_host_dest,
                    abort_event=abort_event,
                    on_progress=_make_on_progress(host, len(_plan)),
                    open_ssh=open_ssh,
                    open_sftp=open_sftp,
                    pull_one=_po,
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
        # Cleanup phase (idempotent best-effort)
        if do_cleanup_remote and remote_removers:
            try:
                cleanup_all_remote_temps(remote_removers)
            except Exception:
                pass
        if do_cleanup_local:
            try:
                cleanup_all_local_temps()
            except Exception:
                pass
        try:
            close_all()
        except Exception:
            pass
        # Summary and logging shutdown
        aggr.summary()
        shutdown_logging()

    return EXIT_ERR_GENERIC if errors_any else EXIT_OK
