# -*- coding: utf-8 -*-
"""
gm_tools.core_signal_handling
============================

Graceful stop orchestration and signal handling for gm-tools.

Policy:
- CLI initializes this module and registers handlers once at startup.
- On SIGINT/SIGTERM: set abort flag, run registered cleanups (best-effort),
  and optionally trigger a summary callback. Do NOT call sys.exit() here;
  the CLI is responsible for exit codes.
- Callers should check `abort_event.is_set()` at "next trial just before start",
  and before/after long I/O, to return early.

This module performs no side effects on import.
"""

from __future__ import annotations

import signal
import threading
from typing import Callable, List, Optional


class GracefulStop:
    """
    A coordinator for cooperative cancellation and best-effort cleanup.

    - `abort_event` is set when a stop is requested.
    - Cleanup callbacks are executed at most once, in LIFO order.
    - All methods are thread-safe.
    """

    def __init__(self) -> None:
        self.abort_event: threading.Event = threading.Event()
        self._cleanups: List[Callable[[], None]] = []
        self._lock: threading.Lock = threading.Lock()
        self._cleaned: bool = False

    # ---- registration ----

    def register_cleanup(self, fn: Callable[[], None]) -> None:
        """
        Register a cleanup callback to be invoked upon stop (LIFO order).
        The callback must be idempotent and must not raise; exceptions will be swallowed.
        """
        with self._lock:
            # Newest cleanup should run first -> push to the end (LIFO on run).
            self._cleanups.append(fn)

    # ---- stop request & cleanup ----

    def request_stop(self) -> None:
        """Set the abort flag. Safe to call multiple times."""
        self.abort_event.set()
        self.run_cleanups()

    def run_cleanups(self) -> None:
        """
        Run all registered cleanup callbacks exactly once (best-effort, LIFO).
        Exceptions from callbacks are swallowed to avoid masking other cleanups.
        """
        with self._lock:
            if self._cleaned:
                return
            self._cleaned = True
            fns = list(reversed(self._cleanups))

        for fn in fns:
            try:
                fn()
            except Exception:
                # Best-effort: never re-raise here.
                pass


def register_signal_handlers(
    gs: GracefulStop,
    *,
    on_summary: Optional[Callable[[], None]] = None,
) -> None:
    """
    Register SIGINT/SIGTERM handlers that coordinate a graceful stop.

    Parameters
    ----------
    gs : GracefulStop
        The orchestrator instance whose abort flag and cleanups will be used.
    on_summary : Optional[Callable[[], None]]
        Callback invoked after cleanups to ensure a summary is printed.
        Should be idempotent and exception-safe.
    """

    def _handler(signum: int, frame: object) -> None:
        # ここではログを出さない（CLI 層がユーザ向けログを担当）
        gs.request_stop()
        gs.run_cleanups()
        if on_summary is not None:
            try:
                on_summary()
            except Exception:
                pass

    # Install handlers for SIGINT and SIGTERM
    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


__all__ = ["GracefulStop", "register_signal_handlers"]
