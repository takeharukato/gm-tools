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

"""gather のホスト単位プル処理を提供するモジュール。

このモジュールは gm-gather CLI から呼び出され、リモートからローカルへファイルを
取得する。主な設計方針は以下のとおり。

* 具体的な SSH/SFTP 実装には依存せず、呼び出し側がファクトリー関数として注入する。
* ログやシリアライズは CLI 側で扱い、本モジュールはコールバックで進捗のみ通知する。
* ``abort_point()`` を要所で呼び出し、協調的なキャンセルを尊重する。
* 接続の破棄は行わず、``core_ssh`` のレジストリへ登録して CLI 側でクリーンアップする。

インポート時に副作用は発生しない。
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
    """ホスト単位で集計した処理結果を保持する不変データクラス。

    Attributes:
        warnings (int): 収集処理中に発生した警告件数。
        errors (int): 回復不能なエラー件数。
        processed (int): 正常に処理した項目数。
        trial (int): 試行回数や進捗通知に用いる処理済みエントリ数。
    """
    warnings: int
    errors: int
    processed: int
    trial: int


# ---- Callbacks / factories ---------------------------------------------------

# 進捗通知コールバック: (seq, trial, processed, total)
OnProgress = Callable[[int, int, int, int], None]

# ホスト向けの SSH/SFTP オブジェクトを生成するために注入されるファクトリー。
SSHFactory = Callable[[str], SSHClientLike]
SFTPFactory = Callable[[SSHClientLike], SFTPClientLike]

# 個別項目の転送コールバック。``is_dir`` が True のときはディレクトリ扱いで、実装によっては no-op となる。
PullOne = Callable[[SFTPClientLike, str, Path, bool], None]


# ---- Helpers ----------------------------------------------------------------

def _join_remote(root: str, relpath: str) -> str:
    """リモートのルートと相対パスを POSIX 区切りで連結する。

    Args:
        root (str): リモート側のベースディレクトリ。
        relpath (str): 連結したい相対パス。

    Returns:
        str: POSIX 形式で連結されたリモートパス。

    Examples:
        >>> _join_remote('', 'etc/hosts')
        'etc/hosts'
        >>> _join_remote('/var', 'log/syslog')
        '/var/log/syslog'
    """
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
    """gather 用のプランをホスト単位で順次実行する。

    Args:
        host (str): 接続対象のホスト名。接続レジストリのキーにも利用される。
        plan (Plan): ``core_select`` が生成した安定なプラン。``len(plan)`` が進捗通知の総数になる。
        remote_root (str): リモート側で ``plan.relpath`` と結合するベースディレクトリ。絶対指定の場合は空文字列を許容する。
        local_root (Path): ローカルの配置先ルートディレクトリ。項目はこの配下に ``plan.relpath`` で格納される。
        abort_event (threading.Event): シグナルハンドラからのキャンセル要求を受け取るイベントフラグ。
        on_progress (Optional[OnProgress]): 進捗通知を受け取るコールバック。``None`` を指定すると通知しない。
        open_ssh (SSHFactory): SSH クライアントを生成するファクトリー関数。接続確立後に ``register_connection`` へ登録される。
        open_sftp (SFTPFactory): SSH クライアントから SFTP クライアントを生成するファクトリー関数。
        pull_one (PullOne): SFTP を用いて単一エントリの転送を行う処理。ディレクトリの場合は no-op を許容する。

    Returns:
        HostResult: 処理済み件数やエラー数を集計した結果レコード。

    Raises:
        CancelledError: ``abort_event`` が設定され、協調キャンセルが要求された場合。

    Examples:
        >>> from types import SimpleNamespace
        >>> class DummyPlan:
        ...     def __init__(self):
        ...         self._items = [SimpleNamespace(relpath='file.txt', is_dir=False)]
        ...     def __len__(self):
        ...         return len(self._items)
        ...     def iter_seq(self):
        ...         for idx, item in enumerate(self._items, start=1):
        ...             yield idx, item
        >>> def fake_open_ssh(_host):
        ...     return SimpleNamespace(close=lambda: None)
        >>> def fake_open_sftp(_ssh):
        ...     return SimpleNamespace(close=lambda: None)
        >>> def fake_pull_one(_sftp, _remote_path, _local_path, _is_dir):
        ...     pass
        >>> dummy_plan = DummyPlan()
        >>> result = run_host_gather(  # doctest: +SKIP
        ...     host='localhost',
        ...     plan=dummy_plan,
        ...     remote_root='/var/log',
        ...     local_root=Path('./tmp'),
        ...     abort_event=threading.Event(),
        ...     on_progress=None,
        ...     open_ssh=fake_open_ssh,
        ...     open_sftp=fake_open_sftp,
        ...     pull_one=fake_pull_one,
        ... )
        >>> isinstance(result, HostResult)  # doctest: +SKIP
        True
    """
    total: int = len(plan)

    # ネットワーク I/O を行う前にキャンセル要求を確認
    abort_point(abort_event)

    # ライブラリ非依存に接続を確立し、後段のクリーンアップに備えてレジストリへ登録する。
    ssh = open_ssh(host)
    register_connection(host, ssh)
    sftp = open_sftp(ssh)
    register_sftp(host, sftp)

    # ローカルのルートディレクトリが存在するように作成する
    local_root = Path(local_root)
    local_root.mkdir(parents=True, exist_ok=True)

    warnings: int = 0
    errors: int = 0
    trial: int = 0
    processed: int = 0

    for seq, entry in plan.iter_seq():
        # 試行ごとのチェックポイントでキャンセルを確認
        abort_point(abort_event)
        trial += 1

        # リモートパス解決 ( 複数種類のルート対応 ) :
        # 1) entry が 'remote_abs' を持つときは元の絶対パスをそのまま利用する。
        # 2) それ以外では entry の 'remote_root' と 'remote_rel' を優先して結合し、無い場合は relpath を使う。
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

        # 長い I/O に入る前に、ディレクトリであれば対象を、ファイルであれば親ディレクトリを作成しておく
        if entry.is_dir:
            local_path.mkdir(parents=True, exist_ok=True)
        else:
            local_path.parent.mkdir(parents=True, exist_ok=True)

        # 長時間 I/O 区間: 実際の転送処理
        try:
            abort_point(abort_event)  # リモート呼び出しの直前でキャンセルを確認
            pull_one(sftp, remote_path, local_path, entry.is_dir)
            abort_point(abort_event)  # リモート呼び出し直後にキャンセルを確認
            processed += 1
        except CancelledError:
            # キャンセルを呼び出し元へ伝播させ、上位で終了処理を任せる
            raise
        except Exception:
            # エラー件数に加算し、次の項目へ進む
            errors += 1

        # 進捗通知をコールバックへ送る
        if on_progress is not None:
            on_progress(seq, trial, processed, total)

    return HostResult(warnings=warnings, errors=errors, processed=processed, trial=trial)
