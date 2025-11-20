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

"""scatter のホスト単位プッシュ処理を提供するモジュール。

このモジュールは gm-scatter CLI から呼び出され、ローカルファイルをリモート環境へ
配置する。主な設計方針は以下のとおり。

* 具体的な SSH/SFTP 実装には依存せず、呼び出し側がファクトリー関数として注入する。
* ログおよびシリアライズは CLI/並列実行レイヤーが担当し、本モジュールは進捗のみ
    コールバックで通知する。
* ``abort_point()`` を要所で呼び出し、協調的なキャンセルを尊重する。
* 接続は呼び出し元が所有し、本モジュールでは直接クローズせずレジストリ経由で
    クリーンアップを委ねる。

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
    """ホスト単位で集計した scatter の処理結果を保持する不変データクラス。

    Attributes:
        warnings (int): 処理中に発生した警告件数。
        errors (int): 回復不能なエラー件数。
        processed (int): 正常に処理した項目数。
        trial (int): 進捗通知などで利用する処理済み試行回数。
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

# 個別項目を転送するためのコールバック。
# remote_root はホストごとのリモート基点ディレクトリ ( 例: DEST や DEST/<HOST> ) を表す。
PushOne = Callable[[SFTPClientLike, Path, str, bool], None]


# ---- Helpers ----------------------------------------------------------------

def _join_remote(root: str, relpath: str) -> str:
    """リモートのルートと相対パスを POSIX 区切りで連結する。

    Args:
        root (str): リモート側のベースディレクトリ。
        relpath (str): 連結対象の相対パス。

    Returns:
        str: POSIX 形式で連結されたリモートパス。

    Examples:
        >>> _join_remote('', 'dest/file.txt')
        'dest/file.txt'
        >>> _join_remote('/var', 'log/syslog')
        '/var/log/syslog'
    """
    if not root:
        return relpath
    if root.endswith("/"):
        return root + relpath
    return root + "/" + relpath


# ---- Main entry --------------------------------------------------------------

def run_host_scatter(
    host: str,
    plan: Plan,
    *,
    remote_root: str,
    local_root: Path,  # 対称性維持のためのダミー引数。実際のローカル絶対パスは PlanEntry 側が保持する。
    abort_event: threading.Event,
    on_progress: Optional[OnProgress],
    open_ssh: SSHFactory,
    open_sftp: SFTPFactory,
    push_one: PushOne,
) -> HostResult:
    """scatter 用のプランをホスト単位で順次実行する。

    Args:
        host (str): 接続対象のホスト名。接続レジストリのキーとしても使用される。
        plan (Plan): 呼び出し元が生成した安定なプラン。``len(plan)`` が進捗通知における総数となる。
        remote_root (str): リモート側で ``plan.relpath`` を展開する基点ディレクトリ ( 例: DEST や DEST/<HOST> )。
        local_root (Path): 対称性のために受け取るローカル基点。実際には ``PlanEntry.path`` が絶対パスを保持する。
        abort_event (threading.Event): シグナルハンドラからのキャンセル要求を受け取るイベントフラグ。
        on_progress (Optional[OnProgress]): 進捗通知を受け取るコールバック。``None`` の場合は通知しない。
        open_ssh (SSHFactory): SSH クライアントを生成するファクトリー関数。生成後 ``register_connection`` に登録される。
        open_sftp (SFTPFactory): SSH クライアントから SFTP クライアントを生成するファクトリー関数。
        push_one (PushOne): 単一エントリをリモートへ転送する処理。ディレクトリの場合の no-op を許容する。

    Returns:
        HostResult: 警告・エラー・処理済み件数を集計した結果レコード。

    Raises:
        CancelledError: ``abort_event`` が設定され、協調的キャンセルが要求された場合。

    Examples:
        >>> from types import SimpleNamespace
        >>> class DummyPlan:
        ...     def __init__(self):
        ...         self._items = [SimpleNamespace(relpath='remote/file.txt', path=Path('local/file.txt'), is_dir=False)]
        ...     def __len__(self):
        ...         return len(self._items)
        ...     def iter_seq(self):
        ...         for idx, item in enumerate(self._items, start=1):
        ...             yield idx, item
        >>> def fake_open_ssh(_host):
        ...     return SimpleNamespace(close=lambda: None)
        >>> def fake_open_sftp(_ssh):
        ...     return SimpleNamespace(close=lambda: None)
        >>> def fake_push_one(_sftp, _local_path, _remote_path, _is_dir):
        ...     pass
        >>> dummy_plan = DummyPlan()
        >>> result = run_host_scatter(  # doctest: +SKIP
        ...     host='localhost',
        ...     plan=dummy_plan,
        ...     remote_root='/srv/dest',
        ...     local_root=Path('./src'),
        ...     abort_event=threading.Event(),
        ...     on_progress=None,
        ...     open_ssh=fake_open_ssh,
        ...     open_sftp=fake_open_sftp,
        ...     push_one=fake_push_one,
        ... )
        >>> isinstance(result, HostResult)  # doctest: +SKIP
        True
    """
    total: int = len(plan)

    # ネットワーク I/O に入る前にキャンセル要求を確認
    abort_point(abort_event)

    # 接続を確立し、上位レイヤーの冪等なクリーンアップに備えてレジストリへ登録する
    ssh = open_ssh(host)
    register_connection(host, ssh)
    sftp = open_sftp(ssh)
    register_sftp(host, sftp)

    warnings: int = 0
    errors: int = 0
    trial: int = 0
    processed: int = 0

    for seq, entry in plan.iter_seq():
        # ループごとにキャンセルを確認
        abort_point(abort_event)
        trial += 1

        # リモートパスは remote_root と entry.relpath を結合して求める ( push_one 実装が利用 )
        remote_path: str = _join_remote(remote_root, entry.relpath)
        local_path: Path = entry.path

        try:
            abort_point(abort_event)  # リモート呼び出し直前にキャンセルを確認
            push_one(sftp, local_path, remote_path, entry.is_dir)
            abort_point(abort_event)  # リモート呼び出し直後にキャンセルを確認
            processed += 1
        except CancelledError:
            raise
        except Exception:
            errors += 1

        if on_progress is not None:
            on_progress(seq, trial, processed, total)

    return HostResult(warnings=warnings, errors=errors, processed=processed, trial=trial)
