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
"""ホスト単位の並列収集とログ集約を調停する補助モジュールである。

gather_cli.py の実装へ破壊的な変更を加えない方針で, 事前に解析済みの引数や
コールバックを受け取り, スレッドプール上でホスト並列の処理を進行させる。
オプション処理や SELinux・sudo の詳細は呼び出し元が注入するコールバック側で
担保する設計である。
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

# ホスト単位 gather で利用する型エイリアス群
from .core_pull import HostResult as _HostResult
from .core_pull import OnProgress as _OnProgress
from .core_pull import SSHFactory as _SSHFactory
from .core_pull import SFTPFactory as _SFTPFactory
from .core_pull import PullOne as _PullOne
from .core_pull import run_host_gather as _run_host_gather

from .core_signal_handling import GracefulStop


def _clamp_parallel(n: int) -> int:
    """並列ホスト数を 1 以上に調整する。

    Args:
        n (int): 要求された並列ホスト数。

    Returns:
        int: 1 未満の値を指定した場合は 1, それ以外は元の値。

    Examples:
        >>> _clamp_parallel(0)
        1
        >>> _clamp_parallel(4)
        4
    """

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
    # 協調停止 (外部イベントを渡せるが GracefulStop.abort_event を使用するのが既定)
    abort_event: Optional[threading.Event] = None,
    # gather_cli から注入されるファクトリ群
    open_ssh: _SSHFactory,
    open_sftp: _SFTPFactory,
    pull_one: _PullOne,
    pull_one_map: Optional[Dict[str, _PullOne]] = None,
    # 宛先ディレクトリのレイアウト互換性 (False の場合は dest_root 直下へ配置)
    join_host_dir: bool = True,
    # クリーンアップ方針 (CLI から制御)
    remote_removers: Optional[Dict[str, RemoteRemover]] = None,
    do_cleanup_local: bool = False,
    do_cleanup_remote: bool = False,
    # GracefulStop の調停 (外部コーディネータを渡す場合に使用)
    graceful_stop: Optional[GracefulStop] = None,
    # 予約パラメータ (シグナルハンドラ登録は CLI 側で実施)
    register_signals: bool = False,
) -> int:
    """ホストごとの gather を並列実行し, ログ集約とクリーンアップを統括する。

    Args:
        hosts (Sequence[str]): 対象ホスト名のシーケンス。
        plan (Optional[Plan]): 全ホスト共通で利用する転送計画(``Plan``)。
        plan_per_host (Optional[Dict[str, Plan]]): ホスト別の転送計画。指定があれば ``Plan`` より優先される。
        remote_root (str): リモート側のルートパス。空文字列の場合は各エントリが絶対パスを保持する。
        dest_root (_Path): ローカル側の配置ルート。
        parallel (int): 並列実行するホスト数。0 以下は 1 として扱う。
        verbose (bool): 詳細ログを出力する際は ``True``。
        abort_event (Optional[threading.Event]): 協調停止に利用するイベント。未指定時は ``GracefulStop.abort_event`` を用いる。
        open_ssh (_SSHFactory): SSH 接続を生成するファクトリ関数。
        open_sftp (_SFTPFactory): SFTP 接続を生成するファクトリ関数。
        pull_one (_PullOne): ホスト単位の転送処理を行うコールバック。
        pull_one_map (Optional[Dict[str, _PullOne]]): ホストごとに ``pull_one`` を差し替えるマップ。
        join_host_dir (bool): ``True`` の場合はホスト名ディレクトリを ``dest_root`` 配下に作成する。
        remote_removers (Optional[Dict[str, RemoteRemover]]): リモート一時領域をクリーンアップするコールバック群。
        do_cleanup_local (bool): ローカル一時領域をクリーンアップする場合は ``True``。
        do_cleanup_remote (bool): リモート一時領域をクリーンアップする場合は ``True``。
        graceful_stop (Optional[GracefulStop]): 既存の GracefulStop 管理器。未指定時は内部で生成する。
        register_signals (bool): 予約パラメータ。シグナル登録は CLI 側で実施する。

    Returns:
        int: エラーが発生した場合は ``EXIT_ERR_GENERIC``, 成功した場合は ``EXIT_OK``。

    Examples:
        >>> from pathlib import Path  # doctest: +SKIP
        >>> from unittest.mock import MagicMock, patch  # doctest: +SKIP
        >>> dummy_plan = MagicMock()  # doctest: +SKIP
        >>> dummy_plan.__len__.return_value = 1  # doctest: +SKIP
        >>> fake_result = MagicMock(warnings=0, errors=0)  # doctest: +SKIP
        >>> with patch('gm_tools.gather_parallel._run_host_gather', return_value=fake_result):  # doctest: +SKIP
        ...     execute(  # doctest: +SKIP
        ...         hosts=['host1'],  # doctest: +SKIP
        ...         plan=dummy_plan,  # doctest: +SKIP
        ...         remote_root='/srv/src',  # doctest: +SKIP
        ...         dest_root=Path('/tmp/dest'),  # doctest: +SKIP
        ...         parallel=1,  # doctest: +SKIP
        ...         verbose=False,  # doctest: +SKIP
        ...         open_ssh=MagicMock(),  # doctest: +SKIP
        ...         open_sftp=MagicMock(),  # doctest: +SKIP
        ...         pull_one=MagicMock(),  # doctest: +SKIP
        ...     ) == EXIT_OK  # doctest: +SKIP
    """
    # ログ集約器を単一インスタンスで初期化
    init_logging(verbose=verbose)
    aggr = HostLogAggregator(op="gather")

    # ---- 協調停止 (GracefulStop) の調停 ----
    gs: GracefulStop
    if graceful_stop is not None:
        gs = graceful_stop
    else:
        gs = GracefulStop()

    # 協調的な停止(中断)処理を GracefulStop.abort_event に集約
    abort_event_effective: threading.Event = gs.abort_event

    # クリーンアップ関数を期待する実行順の逆順で登録する
    # GracefulStop.run_cleanups() が LIFO で呼び出すためである。
    def _cleanup_remote() -> None:
        if do_cleanup_remote and remote_removers:
            try:
                cleanup_all_remote_temps(remote_removers)
            except Exception:
                # 可能な限りクリーンアップ処理を実施する方針とし, ここでは例外を外へ出さない
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
        # サマリ出力とログシステムの終了処理
        aggr.summary()
        shutdown_logging()

    # 実行順の逆順で登録する
    # 望ましい実行順:
    #   リモートクリーンアップ -> ローカルクリーンアップ -> close_all -> summary+shutdown
    # run_cleanups() は reversed(self._cleanups) を利用するためこの順で登録する
    gs.register_cleanup(_cleanup_summary)
    gs.register_cleanup(_cleanup_close_all)
    gs.register_cleanup(_cleanup_local)
    gs.register_cleanup(_cleanup_remote)


    # ホスト別の進捗通知クロージャ
    def _make_on_progress(host: str, total: int) -> _OnProgress:
        """進捗通知コールバックを生成する。

        Args:
            host (str): 対象ホスト名。
            total (int): 当該ホストに割り当てられたアイテム数。

        Returns:
            _OnProgress: ``HostLogAggregator`` に橋渡しする進捗通知関数。

        Examples:
            >>> cb = execute.__globals__['_make_on_progress']('example', 10)  # doctest: +SKIP
            >>> callable(cb)  # doctest: +SKIP
        """

        def _on(seq: int, trial: int, processed: int, total_in: int) -> None:
            # 各ホストから通知された total_in を尊重しつつ可視化は total で安定化
            aggr.progress(host, seq=seq, trial=trial, processed=processed, total=total)
        return _on

    max_workers = _clamp_parallel(int(parallel))
    # (host, Future[HostResult]) ペアのリスト
    futures: list[tuple[str, _fut.Future[_HostResult]]] = []
    errors_any = False
    try:
        with _fut.ThreadPoolExecutor(max_workers=max_workers) as ex:
            for host in hosts:
                _plan = plan_per_host[host] if plan_per_host is not None else plan
                assert _plan is not None, "execute(): plan or plan_per_host must be provided"
                aggr.start_host(host, total=len(_plan))
                per_host_dest = (dest_root / host) if join_host_dir else dest_root
                # ホスト単位で pull_one を選択 (--pack のときはホストごとに1度だけ実行されるホスト専用の pull_one を注入)
                _po = pull_one_map.get(host, pull_one) if pull_one_map else pull_one
                fut = ex.submit(
                    _run_host_gather,
                    host,
                    _plan,
                    remote_root=remote_root,
                    local_root=per_host_dest,
                    abort_event=abort_event_effective,
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
        # クリーンアップフェーズ (GracefulStop に集約し, 可能な限りクリーンアップを実施, 例外は出さない方針)
        gs.run_cleanups()

    return EXIT_ERR_GENERIC if errors_any else EXIT_OK
