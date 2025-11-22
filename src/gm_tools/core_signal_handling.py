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
"""gm-tools における協調的な停止処理とシグナルハンドリングを提供する。

CLI レイヤーが起動時に初期化し, SIGINT/SIGTERM を受けた際に停止フラグとクリーンアップ処理を
統合的に扱うユーティリティを定義する。モジュールインポート時に副作用は発生しない。
"""

from __future__ import annotations

import signal
import threading
from typing import Callable, List, Optional


class GracefulStop:
    """協調的な停止と後処理をまとめて管理するコーディネータである。

    ``abort_event`` が立った時点で停止要求を検知し, 登録済みクリーンアップを LIFO で一度だけ
    実行する。スレッドセーフな API を提供する。
    """

    def __init__(self) -> None:
        """初期状態の停止フラグとクリーンアップ管理構造を生成する。"""
        self.abort_event: threading.Event = threading.Event()
        self._cleanups: List[Callable[[], None]] = []
        self._lock: threading.Lock = threading.Lock()
        self._cleaned: bool = False

    # ---- registration ----

    def register_cleanup(self, fn: Callable[[], None]) -> None:
        """停止時に実行するクリーンアップ関数を登録する。

        Args:
            fn (Callable[[], None]): 停止時に呼び出すコールバック。副作用は冪等である必要がある。

        Examples:
            >>> gs = GracefulStop()
            >>> called = []
            >>> def cleanup():
            ...     called.append("done")
            >>> gs.register_cleanup(cleanup)
            >>> gs.request_stop()
            >>> "done" in called
            True
        """
        with self._lock:
            # Newest cleanup should run first -> push to the end (LIFO on run).
            self._cleanups.append(fn)

    # ---- stop request & cleanup ----

    def request_stop(self) -> None:
        """停止要求フラグを立て, 必要なクリーンアップを走らせる。

        Examples:
            >>> gs = GracefulStop()
            >>> gs.abort_event.is_set()
            False
            >>> gs.request_stop()
            >>> gs.abort_event.is_set()
            True
        """
        self.abort_event.set()
        self.run_cleanups()

    def run_cleanups(self) -> None:
        """登録済みクリーンアップを一度だけ LIFO で実行する。

        Examples:
            >>> gs = GracefulStop()
            >>> order = []
            >>> gs.register_cleanup(lambda: order.append("first"))
            >>> gs.register_cleanup(lambda: order.append("second"))
            >>> gs.run_cleanups()
            >>> order
            ['second', 'first']
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
                # 可能な限り, 複数回例外を送出しないように無視する
                pass


def register_signal_handlers(
    gs: GracefulStop,
    *,
    on_summary: Optional[Callable[[], None]] = None,
) -> None:
    """SIGINT/SIGTERM に応じて協調的停止を実現するハンドラを登録する。

    Args:
        gs (GracefulStop): 停止フラグとクリーンアップを管理するコーディネータ。
        on_summary (Optional[Callable[[], None]]): クリーンアップ後に要約出力を行う任意コールバック。

    Examples:
        >>> gs = GracefulStop()
        >>> events = []
        >>> def summary():
        ...     events.append("summary")
        >>> register_signal_handlers(gs, on_summary=summary)
        >>> handler = signal.getsignal(signal.SIGINT)
        >>> handler(signal.SIGINT, None)
        >>> events
        ['summary']
    """

    def _handler(signum: int, frame: object) -> None:
        """受信したシグナルを契機に停止処理と要約出力を実行する。

        Args:
            signum (int): 受信したシグナル番号。
            frame (object): シグナルを受け取ったスタックフレーム。未使用。
        """
        # ここではログを出さない ( CLI 層がユーザ向けログを担当 )
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
