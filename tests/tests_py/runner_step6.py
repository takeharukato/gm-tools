#!/usr/bin/env python3
# tests/tests_py/runner_step6.py
# Step6: GracefulStop + gather/scatter_parallel.execute 結合テスト runner

from __future__ import annotations

import threading
import time
from pathlib import Path as _Path
from typing import Any, Callable, Dict, List, Optional, Tuple, cast

from ._local_types import Config, CaseResult
from .test_common_config import load_config_from_env, print_env
from .test_common_runner import run_cases
from .test_common_cleanup import cleanup_dir

from gm_tools.core_signal_handling import GracefulStop
from gm_tools.core_ssh import CancelledError, SSHClientLike, SFTPClientLike


# gather_parallel（pull 側）用
from gm_tools.core_pull import (
    HostResult as PullHostResult,
    OnProgress as PullOnProgress,
    SSHFactory as PullSSHFactory,
    SFTPFactory as PullSFTPFactory,
    PullOne,
)

# scatter_parallel（push 側）用
from gm_tools.core_push import (
    HostResult as PushHostResult,
    OnProgress as PushOnProgress,
    SSHFactory as PushSSHFactory,
    SFTPFactory as PushSFTPFactory,
    PushOne,
)

from gm_tools.core_select import Plan, PlanEntry
import gm_tools.gather_parallel as gather_parallel
import gm_tools.scatter_parallel as scatter_parallel


def _make_case_result(
    name: str,
    *,
    passed: bool,
    skipped: bool,
    reason: str,
    details: Dict[str, object],
) -> CaseResult:
    """
    旧 ResultDict 互換の情報を持つ CaseResult を生成するヘルパ。
    """
    return CaseResult(
        name=name,
        passed=passed,
        skipped=skipped,
        reason=reason,
        details=details,
    )


def case_gather_parallel_abort_via_graceful_stop(cfg: Config) -> CaseResult:
    """
    目的:
      gather_parallel.execute に GracefulStop を渡した場合の cooperative cancel 挙動を検証する。
      現段階では API 未対応のため TypeError になることが想定されるが、
      将来的には以下を確認する:
        - GracefulStop.request_stop() により abort_event が set されること
        - fake_run_host_gather が abort_event を観測して CancelledError を送出すること
        - GracefulStop に登録した cleanup が一度だけ実行されること
    """
    # GracefulStop と cleanup カウンタ
    gs: GracefulStop = GracefulStop()
    cleanup_calls_box: List[int] = [0]

    def cleanup() -> None:
        count: int = cleanup_calls_box[0]
        count = count + 1
        cleanup_calls_box[0] = count

    gs.register_cleanup(cleanup)

    # fake run_host_gather を差し込んで、abort_event の挙動を観測する。
    abort_seen_box: List[bool] = [False]
    host_calls_box: List[str] = []

    def fake_run_host_gather(
        host: str,
        plan: Plan,
        *,
        remote_root: str,
        local_root: _Path,
        abort_event: threading.Event,
        on_progress: Optional[PullOnProgress],
        open_ssh: PullSSHFactory,
        open_sftp: PullSFTPFactory,
        pull_one: PullOne,
    ) -> PullHostResult:
        host_calls_box.append(host)
        # abort_event が set されるまで少しだけ待つ（無限ループ防止つき）
        spin_count: int = 0
        abort_seen: bool = False
        while True:
            abort_now: bool = abort_event.is_set()
            if abort_now:
                abort_seen = True
                break
            if spin_count >= 100:
                break
            time.sleep(0.01)
            spin_count = spin_count + 1
        abort_seen_box[0] = abort_seen
        if abort_seen:
            raise CancelledError("fake_run_host_gather: observed abort_event and cancelled")
        # abort_event が set されなかった場合は「エラー 1 件」として返す。
        result: PullHostResult = PullHostResult(warnings=0, errors=1, processed=0, trial=0)
        return result

    def dummy_open_ssh(host: str) -> SSHClientLike:
        _host: str = host
        dummy: SSHClientLike = cast(SSHClientLike, object())
        return dummy

    def dummy_open_sftp(ssh: SSHClientLike) -> SFTPClientLike:
        _ssh: SSHClientLike = ssh
        dummy: SFTPClientLike = cast(SFTPClientLike, object())
        return dummy

    def dummy_pull_one(sftp: SFTPClientLike, remote: str, local: _Path, is_dir: bool) -> None:
        _sftp: SFTPClientLike = sftp
        _remote: str = remote
        _local: _Path = local
        _is_dir: bool = is_dir
        return None

    # hosts と Plan を最小構成で作る
    hosts: List[str] = ["hostA", "hostB"]
    entries: List[PlanEntry] = []
    entry: PlanEntry = PlanEntry(
        path=_Path("/dummy/src"),
        relpath="dummy",
        is_dir=False,
        remote_root="",
        remote_abs="",
        remote_rel="",
    )
    entries.append(entry)
    plan: Plan = Plan(entries=entries)

    # gather_parallel._run_host_gather を差し替え
    orig_run_host_gather: Any = getattr(gather_parallel, "_run_host_gather")

    # GracefulStop.request_stop() を別スレッドから呼び出す
    def stopper() -> None:
        # 少し待ってから停止要求を出す
        wait_sec: float = 0.05
        time.sleep(wait_sec)
        gs.request_stop()

    stopper_thread: threading.Thread = threading.Thread(target=stopper, daemon=True)

    rc: Optional[int] = None
    api_mismatch_reason: str = ""
    api_mismatch: bool = False
    exc_repr: Optional[str] = None

    try:
        setattr(gather_parallel, "_run_host_gather", fake_run_host_gather)
        stopper_thread.start()
        try:
            # graceful_stop 引数を受け取る execute() を想定して呼び出す。
            rc = gather_parallel.execute(
                hosts=hosts,
                plan=plan,
                plan_per_host=None,
                remote_root="/remote",
                dest_root=_Path("/local/dest"),
                parallel=2,
                verbose=False,
                graceful_stop=gs,
                open_ssh=dummy_open_ssh,
                open_sftp=dummy_open_sftp,
                pull_one=dummy_pull_one,
                pull_one_map=None,
                join_host_dir=True,
                remote_removers=None,
                do_cleanup_local=False,
                do_cleanup_remote=False,
            )
        except TypeError as e:
            # 現状は graceful_stop 未対応のためここに入る想定。
            api_mismatch = True
            api_mismatch_reason = (
                f"gather_parallel.execute graceful_stop API not yet available: {e!r}"
            )
        except Exception as e:
            exc_repr = repr(e)
    finally:
        setattr(gather_parallel, "_run_host_gather", orig_run_host_gather)
        stopper_thread.join(timeout=1.0)

    details: Dict[str, object] = {
        "rc": rc if rc is not None else -1,
        "abort_seen": abort_seen_box[0],
        "cleanup_calls": cleanup_calls_box[0],
        "host_calls": list(host_calls_box),
        "api_mismatch": api_mismatch,
        "api_mismatch_reason": api_mismatch_reason,
        "exc_repr": exc_repr,
    }

    case_name: str = "gather_parallel_abort_via_graceful_stop"

    if api_mismatch:
        return _make_case_result(
            name=case_name,
            passed=False,
            skipped=False,
            reason=api_mismatch_reason,
            details=details,
        )
    if exc_repr is not None:
        return _make_case_result(
            name=case_name,
            passed=False,
            skipped=False,
            reason="unexpected exception during gather_parallel.execute",
            details=details,
        )

    # 将来の仕様ではここで具体的な期待値を確認する。
    passed: bool = (
        abort_seen_box[0]
        and cleanup_calls_box[0] == 1
    )
    reason: str = "" if passed else "abort_event/cleanup 挙動が期待と異なる（将来の仕様確認用）"

    return _make_case_result(
        name=case_name,
        passed=passed,
        skipped=False,
        reason=reason,
        details=details,
    )


def case_scatter_parallel_abort_via_graceful_stop(cfg: Config) -> CaseResult:
    """
    目的:
      scatter_parallel.execute に GracefulStop を渡した場合の cooperative cancel 挙動を検証する。
      現段階では API 未対応のため TypeError になることが想定されるが、
      将来的には以下を確認する:
        - GracefulStop.request_stop() により abort_event が set されること
        - fake_run_host_scatter が abort_event を観測して CancelledError を送出すること
        - GracefulStop に登録した cleanup が一度だけ実行されること
    """

    gs: GracefulStop = GracefulStop()
    cleanup_calls_box: List[int] = [0]

    def cleanup() -> None:
        count: int = cleanup_calls_box[0]
        count = count + 1
        cleanup_calls_box[0] = count

    gs.register_cleanup(cleanup)

    abort_seen_box: List[bool] = [False]
    host_calls_box: List[str] = []

    def dummy_open_ssh(host: str) -> SSHClientLike:
        _host: str = host
        dummy: SSHClientLike = cast(SSHClientLike, object())
        return dummy

    def dummy_open_sftp(ssh: SSHClientLike) -> SFTPClientLike:
        _ssh: SSHClientLike = ssh
        dummy: SFTPClientLike = cast(SFTPClientLike, object())
        return dummy

    def dummy_push_one(sftp: SFTPClientLike, local: _Path, remote: str, is_dir: bool) -> None:
        _sftp: SFTPClientLike = sftp
        _local: _Path = local
        _remote: str = remote
        _is_dir: bool = is_dir
        return None

    def fake_run_host_scatter(
        host: str,
        plan: Plan,
        *,
        remote_root: str,
        local_root: _Path,
        abort_event: threading.Event,
        on_progress: Optional[PushOnProgress],
        open_ssh: PushSSHFactory,
        open_sftp: PushSFTPFactory,
        push_one: PushOne,
    ) -> PushHostResult:
        host_calls_box.append(host)
        spin_count: int = 0
        abort_seen: bool = False
        while True:
            abort_now: bool = abort_event.is_set()
            if abort_now:
                abort_seen = True
                break
            if spin_count >= 100:
                break
            time.sleep(0.01)
            spin_count = spin_count + 1
        abort_seen_box[0] = abort_seen
        if abort_seen:
            raise CancelledError("fake_run_host_scatter: observed abort_event and cancelled")
        result: PushHostResult = PushHostResult(warnings=0, errors=1, processed=0, trial=0)
        return result

    hosts: List[str] = ["hostA", "hostB"]
    entries: List[PlanEntry] = []
    entry: PlanEntry = PlanEntry(
        path=_Path("/dummy/src"),
        relpath="dummy",
        is_dir=False,
        remote_root="",
        remote_abs="",
        remote_rel="",
    )
    entries.append(entry)
    plan: Plan = Plan(entries=entries)

    orig_run_host_scatter: Any = getattr(scatter_parallel, "_run_host_scatter")

    def stopper() -> None:
        wait_sec: float = 0.05
        time.sleep(wait_sec)
        gs.request_stop()

    stopper_thread: threading.Thread = threading.Thread(target=stopper, daemon=True)

    rc: Optional[int] = None
    api_mismatch_reason: str = ""
    api_mismatch: bool = False
    exc_repr: Optional[str] = None

    try:
        setattr(scatter_parallel, "_run_host_scatter", fake_run_host_scatter)
        stopper_thread.start()
        try:
            rc = scatter_parallel.execute(
                hosts=hosts,
                plan=plan,
                plan_per_host=None,
                remote_root="/remote",
                src_root=_Path("/local/src"),
                parallel=2,
                verbose=False,
                graceful_stop=gs,
                open_ssh=dummy_open_ssh,
                open_sftp=dummy_open_sftp,
                push_one=dummy_push_one,
                push_one_map=None,
                join_host_dir=True,
                remote_removers=None,
                do_cleanup_local=False,
                do_cleanup_remote=False,
            )
        except TypeError as e:
            api_mismatch = True
            api_mismatch_reason = (
                f"scatter_parallel.execute graceful_stop API not yet available: {e!r}"
            )
        except Exception as e:
            exc_repr = repr(e)
    finally:
        setattr(scatter_parallel, "_run_host_scatter", orig_run_host_scatter)
        stopper_thread.join(timeout=1.0)

    details: Dict[str, object] = {
        "rc": rc if rc is not None else -1,
        "abort_seen": abort_seen_box[0],
        "cleanup_calls": cleanup_calls_box[0],
        "host_calls": list(host_calls_box),
        "api_mismatch": api_mismatch,
        "api_mismatch_reason": api_mismatch_reason,
        "exc_repr": exc_repr,
    }

    case_name: str = "scatter_parallel_abort_via_graceful_stop"

    if api_mismatch:
        return _make_case_result(
            name=case_name,
            passed=False,
            skipped=False,
            reason=api_mismatch_reason,
            details=details,
        )
    if exc_repr is not None:
        return _make_case_result(
            name=case_name,
            passed=False,
            skipped=False,
            reason="unexpected exception during scatter_parallel.execute",
            details=details,
        )

    passed: bool = (
        abort_seen_box[0]
        and cleanup_calls_box[0] == 1
    )
    reason: str = "" if passed else "abort_event/cleanup 挙動が期待と異なる（将来の仕様確認用）"

    return _make_case_result(
        name=case_name,
        passed=passed,
        skipped=False,
        reason=reason,
        details=details,
    )


def main() -> None:
    """
    Config をロードし、Step6 用テストケース群を共通ランナーで実行する。
    """
    cfg: Config = load_config_from_env()
    _ = print_env(cfg)

    cases: List[Tuple[str, Callable[[Config], CaseResult]]] = [
        ("gather_parallel_abort_via_graceful_stop", case_gather_parallel_abort_via_graceful_stop),
        ("scatter_parallel_abort_via_graceful_stop", case_scatter_parallel_abort_via_graceful_stop),
    ]

    try:
        # 共通フレームワークに実行と JSON summary 出力を委譲
        _ = run_cases(step_number=6, cfg=cfg, cases=cases)
    finally:
        # ---------------------------------------------------------
        # Cleanup: Step6 テスト用ローカル作業ディレクトリの削除（共有実装）
        #  - Config.local_work_root は tests_env.sh.sample で
        #    "${PWD}/_tmp_test_local" に設定される想定。
        #  - Step4/Step5 runner と同様に、runner 側が temp の寿命を持つ。
        # ---------------------------------------------------------
        local_root: _Path = _Path(cfg.local_work_root)
        try:
            if local_root.exists():
                cleanup_dir(str(local_root))
        except Exception:
            # Best-effort cleanup: テスト結果を壊さないため、例外は握りつぶす。
            pass


if __name__ == "__main__":
    main()
