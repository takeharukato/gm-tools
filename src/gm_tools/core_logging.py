# -*- mode: python; coding: utf-8; line-endings: unix -*-
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2025 TAKEHARU KATO
#
# This file is distributed under the two-clause BSD license.
# For the full text of the license, see the LICENSE file in the project root directory.
# このファイルは2条項BSDライセンスの下で配布されています。
# ライセンス全文はプロジェクト直下の LICENSE を参照してください。
#
# OpenAI's ChatGPT partially generated this code.
# Author has modified some parts.
# OpenAIのChatGPTがこのコードの一部を生成しました。
# 著者が修正している部分があります。

"""キュー駆動の構造化ログを提供するモジュール。

キューリスナー経由で標準出力/標準エラーにレベルごとのログを送出し,
``core_constants.KEYS_PREFIX`` で定めたキー順序を維持する。import 時に副作用はなく,
CLI などで :func:`init_logging` を呼び出して初期化する。

Examples:
    >>> from gm_tools.core_logging import init_logging, log_inf, shutdown_logging
    >>> init_logging(verbose=False)  # doctest: +SKIP
    >>> log_inf('example', host='host1', op='gather')  # doctest: +SKIP
    >>> shutdown_logging()  # doctest: +SKIP
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
# ロギングキュー基盤の内部状態
# ---------------------------------------------------------------------------

_q: Optional[queue.Queue[logging.LogRecord]] = None
_listener: Optional[logging.handlers.QueueListener] = None
_logger: Optional[logging.Logger] = None


def _iso_timestamp(now: Optional[_dt.datetime] = None) -> str:
    """ミリ秒とタイムゾーンを含む ISO 8601 形式の日時文字列を生成する。

    Args:
        now (Optional[_dt.datetime]): 明示的に利用したい日時。 ``None`` なら現在時刻。

    Returns:
        str: 例 ``2025-11-05T12:34:56.789+09:00`` の形式。

    Examples:
        >>> from datetime import datetime, timezone
        >>> dt = datetime(2024, 1, 2, 3, 4, 5, 678000, tzinfo=timezone.utc)
        >>> _iso_timestamp(dt)
        '2024-01-02T03:04:05.678+00:00'
    """
    dt = now or _dt.datetime.now(_dt.timezone.utc).astimezone()
    # ミリ秒まで含む文字列表現を作成する
    base = dt.strftime("%Y-%m-%dT%H:%M:%S")
    ms = f"{int(dt.microsecond/1000):03d}"
    tz = dt.strftime("%z")
    tz_fmt = f"{tz[:-2]}:{tz[-2:]}" if tz else "+00:00"
    return f"{base}.{ms}{tz_fmt}"


class _StdoutFilter(logging.Filter):
    """WARNING 未満のログレコードのみ通過させるフィルタ。"""

    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        """WARNING 未満の場合に ``True`` を返す。

        Args:
            record (logging.LogRecord): 評価対象のログレコード。

        Returns:
            bool: 条件を満たした場合は ``True``。

        Examples:
            >>> import logging
            >>> flt = _StdoutFilter()
            >>> rec_info = logging.makeLogRecord({'levelno': logging.INFO, 'msg': 'info'})
            >>> flt.filter(rec_info)
            True
            >>> rec_warn = logging.makeLogRecord({'levelno': logging.WARNING, 'msg': 'warn'})
            >>> flt.filter(rec_warn)
            False
        """
        return int(record.levelno) < int(logging.WARNING)


class _StderrFilter(logging.Filter):
    """WARNING 以上のログレコードのみ通過させるフィルタ。"""

    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        """WARNING 以上の場合に ``True`` を返す。

        Args:
            record (logging.LogRecord): 評価対象のログレコード。

        Returns:
            bool: 条件を満たした場合は ``True``。

        Examples:
            >>> import logging
            >>> flt = _StderrFilter()
            >>> rec_warn = logging.makeLogRecord({'levelno': logging.WARNING, 'msg': 'warn'})
            >>> flt.filter(rec_warn)
            True
            >>> rec_info = logging.makeLogRecord({'levelno': logging.INFO, 'msg': 'info'})
            >>> flt.filter(rec_info)
            False
        """
        return int(record.levelno) >= int(logging.WARNING)


class _StructuredFormatter(logging.Formatter):
    """固定順序の ``key=value`` 形式でログレコードを整形するフォーマッタ。"""

    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        """構造化された ``key=value`` 文字列を生成する。

        Args:
            record (logging.LogRecord): 整形対象のログレコード。

        Returns:
            str: ``timestamp="..."`` 形式の文字列。

        Examples:
            >>> import logging
            >>> fmt = _StructuredFormatter()
            >>> rec = logging.makeLogRecord({'levelno': logging.INFO, 'msg': 'formatted'})
            >>> formatted = fmt.format(rec)
            >>> formatted.startswith('timestamp="')
            True
            >>> 'level="INFO"' in formatted
            True
        """
        # record.extra に相当する辞書があれば取り出す
        extra: Mapping[str, object] = getattr(record, "__extra", {})  # _emit() が dict を入れる前提

        # プレフィクスキーを定義順に収集する
        out_parts: List[str] = []
        # timestamp を先頭に追加
        out_parts.append(f'timestamp="{_iso_timestamp()}"')
        # level を続けて追加
        out_parts.append(f'level="{record.levelname}"')

        # 必須キーは既定値つきで KEYS_PREFIX の順に処理する ( timestamp/level は上で処理済み )
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

        # 任意キーは存在するものだけを付与する
        for key in KEYS_OPTIONAL:
            if key in extra:
                out_parts.append(f'{key}="{extra[key]}"')

        # メッセージ本文は msg として最後に追加する
        msg = record.getMessage()
        if msg:
            out_parts.append(f'msg="{msg}"')

        return " ".join(out_parts)


def init_logging(*, verbose: bool) -> None:
    """キュー駆動の構造化ロギングを初期化する。

    ``verbose`` が ``True`` の場合は DEBUG レベルまで出力し,  ``False`` では INFO 以上を扱う。
    既に初期化済みの場合は何もせず復帰する。

    Args:
        verbose (bool): 詳細ログを出力するかどうか。

    Returns:
        None: 返り値は使用しない。

    Examples:
        >>> init_logging(verbose=False)  # doctest: +SKIP
        >>> log_inf('example', host='host1', op='gather')  # doctest: +SKIP
        >>> shutdown_logging()  # doctest: +SKIP
    """
    global _q, _listener, _logger
    if _listener is not None:
        return  # 既に初期化済み

    _q = queue.Queue[logging.LogRecord]()
    queue_handler = logging.handlers.QueueHandler(_q)

    # ルートロガーを設定する
    root = logging.getLogger("gm-tools")
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.handlers = []  # 既存ハンドラーをリセットする
    root.propagate = False
    root.addHandler(queue_handler)

    # リスナーから利用するハンドラーを準備する
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
    """ログリスナーを停止し内部状態を破棄する。

    Returns:
        None: 返り値は使用しない。

    Examples:
        >>> init_logging(verbose=False)  # doctest: +SKIP
        >>> shutdown_logging()  # doctest: +SKIP
    """
    global _listener, _q, _logger
    if _listener is not None:
        _listener.stop()
        _listener = None
    _q = None
    _logger = None

def _emit(level: int, msg: str, **kv: object) -> None:
    """構造化ログを出力する。

    Args:
        level (int): ``logging`` モジュール互換のレベル値。
        msg (str): 出力するメッセージ文字列。
        **kv (object): ``KEYS_PREFIX``/``KEYS_OPTIONAL`` に含まれるキーに対応した値。

    Returns:
        None: 返り値は使用しない。

    Examples:
        >>> init_logging(verbose=False)  # doctest: +SKIP
        >>> _emit(logging.INFO, 'internal example', host='host1')  # doctest: +SKIP
        >>> shutdown_logging()  # doctest: +SKIP
    """
    if _logger is None:
        # ロガー未初期化時は非 verbose で初期化する
        init_logging(verbose=False)
    assert _logger is not None

    # 既知のキーのみ extra に保持し, それ以外はメッセージ側で扱う
    extra: Dict[str, object] = {}
    for k in ("host", "op", "phase", "trial", "processed", "total", *KEYS_OPTIONAL):
        if k in kv:
            extra[k] = kv[k]

    # logging の内部属性と衝突しないよう __extra に格納する
    _logger.log(level, msg, extra={"__extra": extra})


def log_dbg(msg: str, **kv: object) -> None:
    """DEBUG レベルの構造化ログを出力する。

    Args:
        msg (str): 出力するメッセージ文字列。
        **kv (object): 追加で出力したいキーと値。

    Returns:
        None: 返り値は使用しない。

    Examples:
        >>> init_logging(verbose=False)  # doctest: +SKIP
        >>> log_dbg('debug example', host='host1', op='gather')  # doctest: +SKIP
        >>> shutdown_logging()  # doctest: +SKIP
    """
    _emit(logging.DEBUG, msg, **kv)


def log_inf(msg: str, **kv: object) -> None:
    """INFO レベルの構造化ログを出力する。

    Args:
        msg (str): 出力するメッセージ文字列。
        **kv (object): 追加で出力したいキーと値。

    Returns:
        None: 返り値は使用しない。

    Examples:
        >>> init_logging(verbose=False)  # doctest: +SKIP
        >>> log_inf('info example', host='host1', op='gather')  # doctest: +SKIP
        >>> shutdown_logging()  # doctest: +SKIP
    """
    _emit(logging.INFO, msg, **kv)


def log_war(msg: str, **kv: object) -> None:
    """WARNING レベルの構造化ログを出力する。

    Args:
        msg (str): 出力するメッセージ文字列。
        **kv (object): 追加で出力したいキーと値。

    Returns:
        None: 返り値は使用しない。

    Examples:
        >>> init_logging(verbose=False)  # doctest: +SKIP
        >>> log_war('warn example', host='host1', op='gather')  # doctest: +SKIP
        >>> shutdown_logging()  # doctest: +SKIP
    """
    _emit(logging.WARNING, msg, **kv)


def log_err(msg: str, **kv: object) -> None:
    """ERROR レベルの構造化ログを出力する。

    Args:
        msg (str): 出力するメッセージ文字列。
        **kv (object): 追加で出力したいキーと値。

    Returns:
        None: 返り値は使用しない。

    Examples:
        >>> init_logging(verbose=False)  # doctest: +SKIP
        >>> log_err('error example', host='host1', op='gather')  # doctest: +SKIP
        >>> shutdown_logging()  # doctest: +SKIP
    """
    _emit(logging.ERROR, msg, **kv)


def log_cri(msg: str, **kv: object) -> None:
    """CRITICAL レベルの構造化ログを出力する。

    Args:
        msg (str): 出力するメッセージ文字列。
        **kv (object): 追加で出力したいキーと値。

    Returns:
        None: 返り値は使用しない。

    Examples:
        >>> init_logging(verbose=False)  # doctest: +SKIP
        >>> log_cri('critical example', host='host1', op='gather')  # doctest: +SKIP
        >>> shutdown_logging()  # doctest: +SKIP
    """
    _emit(logging.CRITICAL, msg, **kv)


# ---------------------------------------------------------------------------
# HostLogAggregator
# ---------------------------------------------------------------------------

@dataclass
class _HostState:
    """ホスト単位の進捗を保持する内部用ステート。"""

    host: str
    total: int
    trial: int = 0
    processed: int = 0
    warnings: int = 0
    errors: int = 0
    started_at: float = 0.0


class HostLogAggregator:
    """ホスト単位の進捗ログを集約し標準化されたイベントを出力するクラス。"""

    def __init__(self, op: str) -> None:
        """集計対象の処理種別 ``op`` を指定して初期化する。

        Args:
            op (str): 例 ``"gather"`` のような処理名。

        Returns:
            None: 返り値は使用しない。

        Examples:
            >>> agg = HostLogAggregator('gather')
            >>> isinstance(agg, HostLogAggregator)
            True
        """
        self._op: str = op
        self._hosts: Dict[str, _HostState] = {}
        self._lock: threading.Lock = threading.Lock()
        # 総数カウンターを初期化する
        self._tw: int = 0  # 警告件数
        self._te: int = 0  # エラー件数
        self._tp: int = 0  # 処理済み件数
        self._tt: int = 0  # 試行件数

    # ---- lifecycle ----

    def start_host(self, host: str, total: int) -> None:
        """ホスト処理の開始を記録する。

        Args:
            host (str): ログ上で識別するホスト名。
            total (int): 計画処理件数。 ``PlanEntry`` の件数など。

        Returns:
            None: 返り値は使用しない。

        Examples:
            >>> init_logging(verbose=False)  # doctest: +SKIP
            >>> agg = HostLogAggregator('gather')  # doctest: +SKIP
            >>> agg.start_host('host1', 2)  # doctest: +SKIP
            >>> shutdown_logging()  # doctest: +SKIP
        """
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
        """処理途中の進捗イベントを記録する。

        Args:
            host (str): 対象ホスト名。
            seq (int): 呼び出しごとに増分するシーケンス番号。 ``HostLogAggregator`` 外部で採番する。
            trial (int): 試行件数。リトライが発生した場合も加算する。
            processed (int): 現在までに処理できた件数。
            total (int): 計画処理件数。 ``start_host`` に渡した値と一致する想定。

        Returns:
            None: 返り値は使用しない。

        Examples:
            >>> init_logging(verbose=False)  # doctest: +SKIP
            >>> agg = HostLogAggregator('gather')  # doctest: +SKIP
            >>> agg.start_host('host1', 2)  # doctest: +SKIP
            >>> agg.progress('host1', seq=1, trial=1, processed=1, total=2)  # doctest: +SKIP
            >>> shutdown_logging()  # doctest: +SKIP
        """
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
        """ホスト処理終了時の統計を記録する。

        Args:
            host (str): 対象ホスト名。
            warnings (int): 終了時点での警告件数。
            errors (int): 終了時点でのエラー件数。

        Returns:
            None: 返り値は使用しない。

        Examples:
            >>> init_logging(verbose=False)  # doctest: +SKIP
            >>> agg = HostLogAggregator('gather')  # doctest: +SKIP
            >>> agg.start_host('host1', 2)  # doctest: +SKIP
            >>> agg.progress('host1', seq=1, trial=1, processed=2, total=2)  # doctest: +SKIP
            >>> agg.done_host('host1', warnings=0, errors=0)  # doctest: +SKIP
            >>> shutdown_logging()  # doctest: +SKIP
        """
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
        # 集計済みカウンターを更新する
        with self._lock:
            self._tw += int(warnings)
            self._te += int(errors)
            self._tp += int(processed)
            self._tt += int(trial)

        # 表示用途として 0.1 秒単位に丸める
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
        """集約結果を最終的なサマリーログとして出力する。

        Returns:
            None: 返り値は使用しない。

        Examples:
            >>> init_logging(verbose=False)  # doctest: +SKIP
            >>> agg = HostLogAggregator('gather')  # doctest: +SKIP
            >>> agg.start_host('host1', 1)  # doctest: +SKIP
            >>> agg.done_host('host1', warnings=0, errors=0)  # doctest: +SKIP
            >>> agg.summary()  # doctest: +SKIP
            >>> shutdown_logging()  # doctest: +SKIP
        """
        with self._lock:
            tw = self._tw
            te = self._te
            tp = self._tp
            tt = self._tt
        # 全体サマリーでは host="-" を使用する
        log_inf(
            "summary",
            host="-",
            op=self._op,
            phase="done",
            trial=tt,
            processed=tp,
            total=tt,  # サマリーでは試行回数を分母とみなして total を出力する
            warnings=tw,
            errors=te,
        )
