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

"""リモート一時ファイルをホスト単位で管理するユーティリティ。

gather/scatter 実行中に生成するリモート一時ファイルを登録し、最終的な片付けを
簡単にする。主な目的は以下のとおり。

* gather/scatter が生成するリモート一時パスを追跡する。
* 冪等に呼び出せるクリーンアップ API を提供する。
* 具体的なリモート削除処理は呼び出し元が渡す ``remover`` コールバックに委ねる。
* ``abort`` チェック => 作成 => 登録 => I/O => 最後にクリーンアップ、という呼び出し
    パターンを促進する。

インポート時に副作用は発生しない。
"""

from __future__ import annotations

import threading
import stat
from typing import Callable, Dict, Iterable, List, Set
from .core_ssh import SFTPClientLike

# ---- 型定義 ------------------------------------------------------------------

# remover はリモート絶対パス 1 件を受け取り、可能な範囲で削除を試みる。
RemoteRemover = Callable[[str], None]


# ---- レジストリ --------------------------------------------------------------

class _PerHost:
    """ホストごとのリモート一時パス集合を保持する内部コンテナ。

    Attributes:
        temps (Set[str]): クリーンアップ対象のリモート絶対パス集合。
    """

    __slots__ = ("temps",)

    def __init__(self) -> None:
        """リモート一時パス集合を初期化する。"""

        # 削除予定のリモート絶対パス集合。
        self.temps: Set[str] = set()


_lock: threading.Lock = threading.Lock()
_registry: Dict[str, _PerHost] = {}  # ホスト名 -> _PerHost


def _bucket(host: str) -> _PerHost:
    """内部レジストリからホストに対応するバケットを取得または作成する。

    Args:
        host (str): レジストリ上で識別するホスト名。

    Returns:
        _PerHost: 対象ホスト用の ``_PerHost`` インスタンス。

    Examples:
        >>> from gm_tools.core_remote_fs import _bucket  # doctest: +SKIP
        >>> bucket = _bucket('example-host')  # doctest: +SKIP
        >>> bucket.temps  # doctest: +SKIP
        set()
    """
    with _lock:
        b = _registry.get(host)
        if b is None:
            b = _PerHost()
            _registry[host] = b
        return b


def register_remote_temp(host: str, path: str) -> None:
    """後でクリーンアップするリモート一時パスを登録する。

    冪等に呼び出せるため、同じパスを複数回登録しても問題ない。

    Args:
        host (str): パスを紐付けるホスト名。
        path (str): 登録するリモートパス ( 絶対パスを推奨 )。

    Examples:
        >>> from gm_tools.core_remote_fs import register_remote_temp, cleanup_remote_temp
        >>> calls = []
        >>> register_remote_temp('host-a', '/tmp/remote/tmp1')
        >>> cleanup_remote_temp('host-a', calls.append)
        >>> calls
        ['/tmp/remote/tmp1']
    """
    b = _bucket(host)
    with _lock:
        b.temps.add(path)


def register_remote_temps(host: str, paths: Iterable[str]) -> None:
    """複数のリモート一時パスをまとめて登録する。

    いずれのパスも冪等に登録されるため、重複していても安全。

    Args:
        host (str): パスを紐付けるホスト名。
        paths (Iterable[str]): 登録したいリモートパスのイテラブル。

    Examples:
        >>> from gm_tools.core_remote_fs import register_remote_temps, cleanup_remote_temp
        >>> captured = []
        >>> register_remote_temps('host-b', ['/tmp/a', '/tmp/b'])
        >>> cleanup_remote_temp('host-b', captured.append)
        >>> sorted(captured)
        ['/tmp/a', '/tmp/b']
    """
    b = _bucket(host)
    with _lock:
        for p in paths:
            b.temps.add(p)


def create_remote_temp(host: str, maker: Callable[[], str]) -> str:
    """``maker()`` で生成したリモート一時パスを登録して返す。

    ``maker`` はリモート側で一時リソースを実際に作成し、その絶対パスを返す責務を
    持つ。呼び出し側は ``maker`` 実行前後で適切な ``abort`` チェックを行う必要がある。

    Args:
        host (str): 一時パスを紐付けるホスト名。
        maker (Callable[[], str]): リモートリソースを作成し、そのパスを返すコールバック。

    Returns:
        str: ``maker`` が返したリモートパス。

    Examples:
        >>> from gm_tools.core_remote_fs import create_remote_temp, cleanup_remote_temp
        >>> recorded = []
        >>> path = create_remote_temp('host-c', lambda: '/tmp/generated')
        >>> path
        '/tmp/generated'
        >>> cleanup_remote_temp('host-c', recorded.append)
        >>> recorded
        ['/tmp/generated']
    """
    path: str = maker()
    register_remote_temp(host, path)
    return path


def cleanup_remote_temp(host: str, remover: RemoteRemover) -> None:
    """登録済みのリモート一時パスを ``remover`` で削除する。

    削除処理は冪等であり、成功したパスは登録から外れるため再度呼び出しても安全。
    またベストエフォートで動作し、``remover`` 内で例外が発生した場合は握りつぶして
    後続処理を継続する。

    Args:
        host (str): クリーンアップ対象のホスト名。
        remover (RemoteRemover): 個々のパスを削除するコールバック。

    Examples:
        >>> from gm_tools.core_remote_fs import register_remote_temp, cleanup_remote_temp
        >>> attempts = []
        >>> register_remote_temp('host-d', '/tmp/cleanup')
        >>> cleanup_remote_temp('host-d', attempts.append)
        >>> attempts
        ['/tmp/cleanup']
    """
    with _lock:
        b = _registry.get(host)
        paths: List[str] = list(b.temps) if b is not None else []

    if not paths:
        return

    failures: List[str] = []
    for p in paths:
        try:
            remover(p)
        except Exception:
            # 削除に失敗したパスは後で再試行するため保持する
            failures.append(p)

    # 失敗パスでレジストリを更新する
    if b is not None:
        with _lock:
            if failures:
                b.temps = set(failures)
            else:
                # すべて削除できた場合はバケットを取り除く
                _registry.pop(host, None)


def cleanup_all_remote_temps(host_to_remover: Dict[str, RemoteRemover]) -> None:
    """複数ホスト分のリモート一時パスをまとめて削除する。

    ``remover`` が提供されていないホストはスキップされ、後で個別にクリーンアップできる。

    Args:
        host_to_remover (Dict[str, RemoteRemover]): ホスト名から ``remover`` へのマッピング。

    Examples:
        >>> from gm_tools.core_remote_fs import register_remote_temp, cleanup_all_remote_temps
        >>> done = []
        >>> register_remote_temp('host-e', '/tmp/a')
        >>> register_remote_temp('host-f', '/tmp/b')
        >>> cleanup_all_remote_temps({'host-e': done.append})
        >>> done
        ['/tmp/a']
    """
    with _lock:
        hosts = list(_registry.keys())
    for host in hosts:
        remover = host_to_remover.get(host)
        if remover is not None:
            cleanup_remote_temp(host, remover)


def sftp_exists(sftp_client: SFTPClientLike, path: str) -> bool:
    """SFTP クライアントで ``path`` が存在するかを確認する。

    Args:
        sftp_client (SFTPClientLike): ``stat`` メソッドを備えた SFTP クライアント互換オブジェクト。
        path (str): 存在確認したいリモートパス。

    Returns:
        bool: 存在が確認できれば ``True``、例外が発生した場合は ``False``。

    Examples:
        >>> class FakeSFTP:
        ...     def stat(self, _path):
        ...         return type('Stat', (), {'st_mode': 0})
        >>> sftp_exists(FakeSFTP(), '/tmp/any')
        True
    """
    try:
        sftp_client.stat(path)
        return True
    except Exception:
        return False

def sftp_isdir(sftp_client: SFTPClientLike, path: str) -> bool:
    """SFTP クライアントで ``path`` がディレクトリかどうかを判定する。

    Args:
        sftp_client (SFTPClientLike): ``stat`` メソッドを備えた SFTP クライアント互換オブジェクト。
        path (str): 判定対象のリモートパス。

    Returns:
        bool: ディレクトリであれば ``True``、それ以外または例外発生時は ``False``。

    Examples:
        >>> import stat
        >>> class DirSFTP:
        ...     def stat(self, _path):
        ...         return type('Stat', (), {'st_mode': stat.S_IFDIR})
        >>> sftp_isdir(DirSFTP(), '/tmp/dir')
        True
    """
    try:
        st = sftp_client.stat(path)
        return stat.S_ISDIR(st.st_mode)
    except Exception:
        return False

def sftp_isfile(sftp_client: SFTPClientLike, path: str) -> bool:
    """SFTP クライアントで ``path`` が通常ファイルかどうかを判定する。

    Args:
        sftp_client (SFTPClientLike): ``stat`` メソッドを備えた SFTP クライアント互換オブジェクト。
        path (str): 判定対象のリモートパス。

    Returns:
        bool: 通常ファイルであれば ``True``、それ以外または例外発生時は ``False``。

    Examples:
        >>> import stat
        >>> class FileSFTP:
        ...     def stat(self, _path):
        ...         return type('Stat', (), {'st_mode': stat.S_IFREG})
        >>> sftp_isfile(FileSFTP(), '/tmp/file')
        True
    """
    try:
        st = sftp_client.stat(path)
        return stat.S_ISREG(st.st_mode)
    except Exception:
        return False

def sftp_islink(sftp_client: SFTPClientLike, path: str) -> bool:
    """SFTP クライアントで ``path`` がシンボリックリンクかどうかを判定する。

    Args:
        sftp_client (SFTPClientLike): ``lstat`` メソッドを備えた SFTP クライアント互換オブジェクト。
        path (str): 判定対象のリモートパス。

    Returns:
        bool: シンボリックリンクであれば ``True``、それ以外または例外発生時は ``False``。

    Examples:
        >>> import stat
        >>> class LinkSFTP:
        ...     def lstat(self, _path):
        ...         return type('Stat', (), {'st_mode': stat.S_IFLNK})
        >>> sftp_islink(LinkSFTP(), '/tmp/link')
        True
    """
    try:
        st = sftp_client.lstat(path)
        return stat.S_ISLNK(st.st_mode)
    except Exception:
        return False


__all__ = [
    "RemoteRemover",
    "register_remote_temp",
    "register_remote_temps",
    "create_remote_temp",
    "cleanup_remote_temp",
    "cleanup_all_remote_temps",
    "sftp_exists",
    "sftp_isdir",
    "sftp_isfile",
    "sftp_islink",
]
