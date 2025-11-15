#
# src/ で:
#
#   PYTHONPATH=. python3 -m pytest -q ../tests/test_core_signal_handling.py
#

from __future__ import annotations

import signal
from typing import Callable, Dict, List

import pytest # type: ignore

from gm_tools.core_signal_handling import GracefulStop, register_signal_handlers


def test_graceful_stop_runs_cleanups_in_lifo_order() -> None:
    gs: GracefulStop = GracefulStop()
    called_labels: List[str] = []

    def make_cleanup(label: str) -> Callable[[], None]:
        def _cleanup() -> None:
            called_labels.append(label)

        return _cleanup

    cleanup1: Callable[[], None] = make_cleanup("first")
    cleanup2: Callable[[], None] = make_cleanup("second")

    gs.register_cleanup(cleanup1)
    gs.register_cleanup(cleanup2)

    gs.run_cleanups()

    expected_order: List[str] = ["second", "first"]
    before_second_run: List[str] = list(called_labels)

    assert called_labels == expected_order

    # 冪等性: 2 回目以降の run_cleanups() では何も変化しない
    gs.run_cleanups()
    assert called_labels == before_second_run


def test_graceful_stop_request_stop_sets_abort_and_runs_cleanups_once() -> None:
    gs: GracefulStop = GracefulStop()

    call_count_box: List[int] = [0]

    def cleanup() -> None:
        call_count_box[0] += 1

    gs.register_cleanup(cleanup)

    # 1 回目の request_stop で abort_event が set され、cleanup が実行されることを期待
    gs.request_stop()

    assert gs.abort_event.is_set()
    assert call_count_box[0] == 1

    # 2 回目以降に request_stop / run_cleanups を呼んでも cleanup が増えないことを確認
    gs.request_stop()
    gs.run_cleanups()
    gs.request_stop()
    gs.run_cleanups()

    assert call_count_box[0] == 1


def test_register_signal_handlers_integration(monkeypatch: pytest.MonkeyPatch) -> None: # type: ignore
    gs: GracefulStop = GracefulStop()

    cleanup_call_count_box: List[int] = [0]

    def cleanup() -> None:
        cleanup_call_count_box[0] += 1

    gs.register_cleanup(cleanup)

    summary_called_box: List[bool] = [False]

    def on_summary() -> None:
        summary_called_box[0] = True

    info_messages: List[str] = []
    warn_messages: List[str] = []

    def log_info(message: str) -> None:
        info_messages.append(message)

    def log_warn(message: str) -> None:
        warn_messages.append(message)

    registered_handlers: Dict[int, Callable[[int, object], None]] = {}

    def fake_signal(
        signum: int,
        handler: Callable[[int, object], None],
    ) -> Callable[[int, object], None]:
        registered_handlers[signum] = handler
        return handler

    # signal.signal を差し替えて、ハンドラ登録だけ検証する
    monkeypatch.setattr(signal, "signal", fake_signal) # type: ignore

    register_signal_handlers(
        gs,
        on_summary=on_summary,
        log_info=log_info,
        log_warn=log_warn,
    )

    # SIGINT 用に登録されたハンドラを取得
    handler_sigint: Callable[[int, object], None] = registered_handlers[signal.SIGINT]

    dummy_frame: object = object()
    handler_sigint(signal.SIGINT, dummy_frame)

    # abort_event が set されていること
    assert gs.abort_event.is_set()

    # cleanup は 1 回だけ実行されていること
    assert cleanup_call_count_box[0] == 1

    # summary が呼ばれていること
    assert summary_called_box[0] is True

    # warning ログと info ログが最低 1 件は記録されていること
    assert len(warn_messages) >= 1
    assert len(info_messages) >= 1

    first_warn: str = warn_messages[0]
    last_info: str = info_messages[-1]

    # メッセージ内容については大まかなパターンだけ確認
    assert "signal" in first_warn
    assert "graceful-stop" in last_info or "graceful stop" in last_info

    # ハンドラをもう一度呼んでも cleanup が増えないこと（冪等性）
    handler_sigint(signal.SIGINT, dummy_frame)
    assert cleanup_call_count_box[0] == 1
