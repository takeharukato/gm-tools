# -*- coding: utf-8 -*-
"""
gm_tools.core_logging
=====================

Structured, serialized logging with a single consumer (QueueListener).
- DEBUG/INFO -> stdout
- WARNING/ERROR/CRITICAL -> stderr
- Fixed key order: timestamp level host op phase trial processed total
- Optional keys: warnings errors duration seq
- ISO 8601 timestamps with milliseconds and timezone

Provides:
- init_logging(verbose: bool) -> None
- shutdown_logging() -> None
- log_dbg/inf/war/err/cri(msg: str, **kv) -> None
- HostLogAggregator(op: str)

This module has no side effects on import.
"""

from __future__ import annotations

import datetime as _dt
import logging
import logging.handlers
import queue
import sys
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional

from .core_constants import (
    KEYS_OPTIONAL,
    KEYS_PREFIX,
)

# ---------------------------------------------------------------------------
# Internal state for the logging queue infrastructure
# ---------------------------------------------------------------------------

_q: Optional[queue.Queue[logging.LogRecord]] = None
_listener: Optional[logging.handlers.QueueListener] = None
_logger: Optional[logging.Logger] = None


def _iso_timestamp(now: Optional[_dt.datetime] = None) -> str:
    """Return ISO 8601 timestamp with milliseconds and timezone, e.g. 2025-11-05T12:34:56.789+09:00"""
    dt = now or _dt.datetime.now(_dt.timezone.utc).astimezone()
    # Format with milliseconds
    base = dt.strftime("%Y-%m-%dT%H:%M:%S")
    ms = f"{int(dt.microsecond/1000):03d}"
    tz = dt.strftime("%z")
    tz_fmt = f"{tz[:-2]}:{tz[-2:]}" if tz else "+00:00"
    return f"{base}.{ms}{tz_fmt}"


class _StdoutFilter(logging.Filter):
    """Allow only records with level < WARNING."""

    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        return int(record.levelno) < int(logging.WARNING)


class _StderrFilter(logging.Filter):
    """Allow only records with level >= WARNING."""

    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        return int(record.levelno) >= int(logging.WARNING)


class _StructuredFormatter(logging.Formatter):
    """Format records as structured key=value pairs with fixed key order."""

    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        # Collect fields from record.extra dict if present
        extra: Mapping[str, object] = getattr(record, "__extra", {})  # _emit() が dict を入れる前提

        # Fixed prefix keys in order
        out_parts: List[str] = []
        # timestamp
        out_parts.append(f'timestamp="{_iso_timestamp()}"')
        # level
        out_parts.append(f'level="{record.levelname}"')

        # Required keys with defaults (ordered by KEYS_PREFIX; timestamp/level are handled above)
        prefix_defaults: Mapping[str, object] = {
            "host": "-",
            "op": "-",
            "phase": "-",
            "trial": 0,
            "processed": 0,
            "total": 0,
        }
        for key in KEYS_PREFIX:
            if key in ("timestamp", "level"):
                continue
            val = extra.get(key, prefix_defaults.get(key, "-"))
            out_parts.append(f'{key}="{val}"')

        # Optional keys if present
        for key in KEYS_OPTIONAL:
            if key in extra:
                out_parts.append(f'{key}="{extra[key]}"')

        # Message
        msg = record.getMessage()
        if msg:
            out_parts.append(f'msg="{msg}"')

        return " ".join(out_parts)


def init_logging(*, verbose: bool) -> None:
    """Initialize queue-based logging with stdout/stderr split and structured format."""
    global _q, _listener, _logger
    if _listener is not None:
        return  # already inited

    _q = queue.Queue[logging.LogRecord]()
    queue_handler = logging.handlers.QueueHandler(_q)

    # Root logger
    root = logging.getLogger("gm_tools")
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.handlers = []  # reset handlers
    root.propagate = False
    root.addHandler(queue_handler)

    # Handlers for the listener
    fmt = _StructuredFormatter()
    h_out = logging.StreamHandler(sys.stdout)
    h_out.addFilter(_StdoutFilter())
    h_out.setFormatter(fmt)

    h_err = logging.StreamHandler(sys.stderr)
    h_err.addFilter(_StderrFilter())
    h_err.setFormatter(fmt)

    _listener = logging.handlers.QueueListener(_q, h_out, h_err, respect_handler_level=False)
    _listener.start()

    _logger = root


def shutdown_logging() -> None:
    global _listener, _q, _logger
    if _listener is not None:
        _listener.stop()
        _listener = None
    _q = None
    _logger = None

def _emit(level: int, msg: str, **kv: object) -> None:
    """Emit a structured log with extra key/values (merged under __extra)."""
    if _logger is None:
        # Fallback init (non-verbose)
        init_logging(verbose=False)
    assert _logger is not None

    # Sanitize: keep only known keys for prefix/optional; others can still be included as msg context.
    extra: Dict[str, object] = {}
    for k in ("host", "op", "phase", "trial", "processed", "total", *KEYS_OPTIONAL):
        if k in kv:
            extra[k] = kv[k]

    # Attach extra into a known attribute to avoid collision with logging internals.
    _logger.log(level, msg, extra={"__extra": extra})


def log_dbg(msg: str, **kv: object) -> None:
    _emit(logging.DEBUG, msg, **kv)


def log_inf(msg: str, **kv: object) -> None:
    _emit(logging.INFO, msg, **kv)


def log_war(msg: str, **kv: object) -> None:
    _emit(logging.WARNING, msg, **kv)


def log_err(msg: str, **kv: object) -> None:
    _emit(logging.ERROR, msg, **kv)


def log_cri(msg: str, **kv: object) -> None:
    _emit(logging.CRITICAL, msg, **kv)


# ---------------------------------------------------------------------------
# HostLogAggregator
# ---------------------------------------------------------------------------

@dataclass
class _HostState:
    host: str
    total: int
    trial: int = 0
    processed: int = 0
    warnings: int = 0
    errors: int = 0
    started_at: float = 0.0


class HostLogAggregator:
    """
    Per-host progress and summary logger.
    Ensures consistent fields and phases: start -> processing -> done.
    """

    def __init__(self, op: str) -> None:
        self._op: str = op
        self._hosts: Dict[str, _HostState] = {}
        self._lock: threading.Lock = threading.Lock()
        # Totals
        self._tw: int = 0  # warnings
        self._te: int = 0  # errors
        self._tp: int = 0  # processed
        self._tt: int = 0  # trial

    # ---- lifecycle ----

    def start_host(self, host: str, total: int) -> None:
        now = time.perf_counter()
        st = _HostState(host=host, total=total, started_at=now)
        with self._lock:
            self._hosts[host] = st
        log_inf(
            "host start",
            host=host,
            op=self._op,
            phase="start",
            trial=0,
            processed=0,
            total=total,
        )

    def progress(self, host: str, *, seq: int, trial: int, processed: int, total: int) -> None:
        with self._lock:
            st = self._hosts.get(host)
            if st is not None:
                st.trial = trial
                st.processed = processed
        log_dbg(
            "processing",
            host=host,
            op=self._op,
            phase="processing",
            trial=trial,
            processed=processed,
            total=total,
            seq=seq,
        )

    def done_host(self, host: str, *, warnings: int, errors: int) -> None:
        end = time.perf_counter()
        with self._lock:
            st = self._hosts.get(host)
        duration = 0.0
        trial = 0
        processed = 0
        total = 0
        if st is not None:
            duration = end - st.started_at
            trial = st.trial
            processed = st.processed
            total = st.total
        # Update totals
        with self._lock:
            self._tw += int(warnings)
            self._te += int(errors)
            self._tp += int(processed)
            self._tt += int(trial)

        # Round to 0.1s for display
        dur_disp = f"{duration:.1f}"
        log_inf(
            "host done",
            host=host,
            op=self._op,
            phase="done",
            trial=trial,
            processed=processed,
            total=total,
            warnings=int(warnings),
            errors=int(errors),
            duration=dur_disp,
        )

    def summary(self) -> None:
        with self._lock:
            tw = self._tw
            te = self._te
            tp = self._tp
            tt = self._tt
        # host="-" for global summary
        log_inf(
            "summary",
            host="-",
            op=self._op,
            phase="done",
            trial=tt,
            processed=tp,
            total=tt,  # total at summary uses "trial" as denominator of attempts
            warnings=tw,
            errors=te,
        )
