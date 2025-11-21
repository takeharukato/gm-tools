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
"""Paramiko 互換の SSH 接続・SFTP クライアント・チャネルのライフサイクルを扱います。

ここでいうライフサイクルとは, 接続確立 ( open ) から利用中の再利用管理, 確実なクローズ
( close ) に至るまでの一連の工程を指します。ホスト単位で ``paramiko.SSHClient`` 互換オブ
ジェクト ( SSH 接続 ) , ``paramiko.SFTPClient`` 互換オブジェクト ( SFTP クライアント ) , お
よび ``Channel`` 相当オブジェクト ( コマンド実行・転送チャネル ) の参照を登録し, 明示的な
close 呼び出しを忘れても安全に解放できるよう管理します。また長時間処理中の協調的な停
止用チェックポイントも提供します。特定ライブラリへの強い依存を避けるため構造的型付け
(PythonのProtocolを使用)を採用しており, モジュール import 時には副作用がありません。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Dict, Protocol, runtime_checkable, Optional, Tuple, List, Any

DEFAULT_SSH_PORT: int = 22
DEFAULT_TIMEOUT: float = 30.0

# ---- 構造的プロトコル ---------------------------------------------------

@runtime_checkable
class Closeable(Protocol):
    """close メソッドを備えるリソースの最小インターフェースです。"""

    def close(self) -> None: ...


@runtime_checkable
class ChannelLike(Closeable, Protocol):
    """待ちループで必要となるチャネル操作の部分集合です。"""

    def exit_status_ready(self) -> bool: ...

    def recv_ready(self) -> bool: ...

    def recv(self, nbytes: int) -> bytes: ...

    def recv_stderr_ready(self) -> bool: ...

    def recv_stderr(self, nbytes: int) -> bytes: ...

# Paramiko の SFTPFile / SFTPClient に相当するプロトコル
@runtime_checkable
class SFTPAttributesLike(Protocol):
    """paramiko.SFTPAttributes で参照する最小限の属性定義です。"""

    st_mode: int

@runtime_checkable
class SFTPFileLike(Protocol):
    """SFTP ファイルハンドルとして必要な操作の部分集合です。"""

    def write(self, data: bytes) -> int: ...

    def read(self, size: int = ...) -> bytes: ...

    def close(self) -> None: ...

    def __enter__(self) -> "SFTPFileLike": ...

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None: ...

@runtime_checkable
class SFTPClientLike(Protocol):
    """paramiko.SFTPClient と互換な最小限の操作集合です。"""

    def open(self, path: str, mode: str = ...) -> SFTPFileLike: ...

    def put(self, localpath: str, remotepath: str) -> None: ...

    def get(self, remotepath: str, localpath: str) -> None: ...

    def listdir(self, path: str) -> List[str]: ...

    def stat(self, path: str) -> SFTPAttributesLike: ...

    def lstat(self, path: str) -> SFTPAttributesLike: ...

    def close(self) -> None: ...

@runtime_checkable
class SSHClientLike(Protocol):
    """Paramiko の SSHClient と同様の操作を提供するための構造的型(Protocol)です。"""

    def exec_command(self, command: str, timeout: Optional[float] = ...) -> Tuple[Any, Any, Any]: ...

    def open_sftp(self) -> SFTPClientLike: ...

    def close(self) -> None: ...

# ---- リソースレジストリ ---------------------------------------------------

class _PerHost:
    """ホストごとの SSH 接続・SFTP クライアント・チャネル参照を保持する内部コンテナです。"""

    __slots__ = ("conns", "sftps", "chans")

    def __init__(self) -> None:
        # Paramiko の SSHClient/SFTPClient/Channel はハッシュ化できないため, リストで管理する。
        self.conns: list[Closeable] = []
        self.sftps: list[Closeable] = []
        self.chans: list[Closeable] = []


_lock: threading.Lock = threading.Lock()
_registry: Dict[str, _PerHost] = {}  # host -> SSH/SFTP/チャネルの格納バケット


def _get_bucket(host: str) -> _PerHost:
    """指定ホストの接続関連オブジェクトを蓄えるバケットを取得します。

    Args:
        host (str): ホスト名。リソース登録時のキーになります。

    Returns:
        _PerHost: SSH クライアント・SFTP クライアント・チャネルの参照を格納するバケット。

    Examples:
        >>> bucket = _get_bucket("example")
        >>> isinstance(bucket, _PerHost)
        True
    """
    with _lock:
        bucket = _registry.get(host)
        if bucket is None:
            bucket = _PerHost()
            _registry[host] = bucket
        return bucket


def register_connection(host: str, conn: SSHClientLike) -> None:
    """SSH 接続オブジェクトを登録し, 後で冪等に close できるようにします。

    Args:
        host (str): 接続を紐づけるホスト名。
        conn (SSHClientLike): ``paramiko.SSHClient`` 互換の接続オブジェクト。

    Examples:
        >>> class Dummy:
        ...     def close(self):
        ...         pass
        >>> register_connection("example", Dummy())
        >>> close_connections("example")
    """
    bucket = _get_bucket(host)
    with _lock:
        if conn not in bucket.conns:  # オブジェクト同一性で重複登録を避ける
            bucket.conns.append(conn)  # type: ignore[arg-type]


def register_sftp(host: str, sftp: SFTPClientLike) -> None:
    """SFTP クライアントオブジェクトを登録し, 後で冪等に close できるようにします。

    Args:
        host (str): 接続を紐づけるホスト名。
        sftp (SFTPClientLike): ``paramiko.SFTPClient`` 互換の SFTP クライアント。

    Examples:
        >>> class Dummy:
        ...     def close(self):
        ...         pass
        >>> register_sftp("example", Dummy())
        >>> close_connections("example")
    """
    bucket = _get_bucket(host)
    with _lock:
        if sftp not in bucket.sftps:
            bucket.sftps.append(sftp)  # type: ignore[arg-type]


def register_channel(host: str, chan: ChannelLike) -> None:
    """コマンド実行・転送チャネルを登録し, 後で冪等に close できるようにします。

    Args:
        host (str): 接続を紐づけるホスト名。
        chan (ChannelLike): ``paramiko.Channel`` 互換のチャネルオブジェクト。

    Examples:
        >>> class Dummy:
        ...     def close(self):
        ...         pass
        >>> register_channel("example", Dummy())
        >>> close_connections("example")
    """
    bucket = _get_bucket(host)
    with _lock:
        if chan not in bucket.chans:
            bucket.chans.append(chan)  # type: ignore[arg-type]


def _safe_close(obj: Closeable) -> None:
    """クローズ時の例外を握りつぶしつつ SSH/ SFTP/チャネルオブジェクトを解放します。

    Args:
        obj (Closeable): ``SSHClientLike``/``SFTPClientLike``/``ChannelLike`` など close を実装するオブジェクト。

    Examples:
        >>> class Dummy:
        ...     def __init__(self):
        ...         self.closed = False
        ...     def close(self):
        ...         self.closed = True
        >>> dummy = Dummy()
        >>> _safe_close(dummy)
        >>> dummy.closed
        True
    """
    try:
        obj.close()
    except Exception:
        # ベストエフォートのクリーンアップのため例外は握りつぶす。
        pass


def close_connections(host: str) -> None:
    """指定ホストのチャネル・SFTP クライアント・SSH 接続を順番にクローズします。

    Args:
        host (str): クローズ対象のホスト名 ( Paramiko 接続と紐づくキー ) 。

    Examples:
        >>> class Dummy:
        ...     def close(self):
        ...         pass
        >>> register_connection("example", Dummy())
        >>> register_channel("example", Dummy())
        >>> close_connections("example")
    """
    with _lock:
        bucket = _registry.get(host)
    if bucket is None:
        return

    # クローズ順序: チャネル -> SFTP クライアント -> SSH 接続
    #  ( チャネルと SFTP クライアントは基礎となる SSH 接続に依存するため )
    for obj in list(bucket.chans):
        _safe_close(obj)
    for obj in list(bucket.sftps):
        _safe_close(obj)
    for obj in list(bucket.conns):
        _safe_close(obj)

    # 空になったバケットを削除する。
    with _lock:
        _registry.pop(host, None)


def close_all() -> None:
    """登録済みすべてのホストの SSH 接続・SFTP クライアント・チャネルを冪等にクローズします。

    Examples:
        >>> class Dummy:
        ...     def close(self):
        ...         pass
        >>> register_connection("example", Dummy())
        >>> close_all()
    """
    with _lock:
        hosts = list(_registry.keys())
    for h in hosts:
        close_connections(h)


# ---- 中断チェックポイント -------------------------------------------------

class CancelledError(RuntimeError):
    """停止要求が行われた際に SSH/SFTP 操作を中断することを示す例外です。"""


def abort_point(abort_event: threading.Event) -> None:
    """協調的な停止のためのチェックポイント処理を行います。

    Args:
        abort_event (threading.Event): 停止要求の有無を示すフラグ。

    Raises:
        CancelledError: 停止フラグが立っている場合に送出します。

    Examples:
        >>> abort = threading.Event()
        >>> abort_point(abort)
        >>> abort.set()
        >>> try:
        ...     abort_point(abort)
        ... except CancelledError:
        ...     "stopped"
        'stopped'
    """
    if abort_event.is_set():
        raise CancelledError("operation aborted by user request")


@dataclass
class SSHConfig:
    """SSH 接続を確立するための設定値を保持します。

    Attributes:
        host (str): 接続先ホスト名。
        port (int): 接続ポート番号。
        ssh_user (Optional[str]): リモートユーザー名。
        key_filename (Optional[str]): 秘密鍵のパス。
        password (Optional[str]): パスワード認証で使用する文字列。
        timeout (float): 接続・コマンド実行のタイムアウト秒数。
        strict_host_key_checking (bool): ホスト鍵を厳格に検証するかどうか。
    """

    host: str
    port: int = DEFAULT_SSH_PORT
    ssh_user: Optional[str] = None
    key_filename: Optional[str] = None
    password: Optional[str] = None
    timeout: float = DEFAULT_TIMEOUT
    strict_host_key_checking: bool = False


def ssh_open(cfg: SSHConfig, *, debug_print: bool = False) -> SSHClientLike:
    """Paramiko 互換クライアントを用いて SSH 接続を確立します。

    Args:
        cfg (SSHConfig): 接続に使用する構成情報。
        debug_print (bool): デバッグ出力の有無 ( 互換性のため保持 ) 。

    Returns:
        SSHClientLike: 接続済みクライアントオブジェクト。

    Raises:
        RuntimeError: Paramiko が import できない場合に送出します。

    Examples:
        Paramiko をダミー実装に差し替えて疑似的に接続します。

        >>> from unittest.mock import Mock, patch
        >>> import sys
        >>> dummy_client = Mock()
        >>> dummy_client.set_missing_host_key_policy.return_value = None
        >>> dummy_client.connect.return_value = None
        >>> dummy_module = Mock()
        >>> dummy_module.SSHClient.return_value = dummy_client
        >>> dummy_module.AutoAddPolicy.return_value = "auto"
        >>> dummy_module.RejectPolicy.return_value = "reject"
        >>> with patch.dict(sys.modules, {"paramiko": dummy_module}):
        ...     cfg = SSHConfig(host="example.com")
        ...     ssh_open(cfg) is dummy_client
        True
    """
    try:
        import paramiko  # type: ignore
    except Exception as e:
        raise RuntimeError("Paramiko is required for ssh_open()") from e

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(
        paramiko.AutoAddPolicy() if not cfg.strict_host_key_checking else paramiko.RejectPolicy()
    )
    client.connect(
        cfg.host,
        port=int(cfg.port),
        username=cfg.ssh_user,
        key_filename=cfg.key_filename,
        password=cfg.password,
        timeout=float(cfg.timeout),
        look_for_keys=True,
        allow_agent=True,
    )
    # 互換性維持のため, 明示的に close しない呼び出し元に備えて登録しておく。
    register_connection(cfg.host, client)  # type: ignore[arg-type]
    return client  # type: ignore[return-value]


def finalize_sockets() -> None:
    """SSH/SFTP/チャネルをまとめて閉じます。

    Examples:
        >>> finalize_sockets()
    """
    try:
        close_all()
    except Exception:
        pass

__all__ = [
    "SSHClientLike",
    "SFTPClientLike",
    "ChannelLike",
    "register_connection",
    "register_sftp",
    "register_channel",
    "close_connections",
    "close_all",
    "abort_point",
    "CancelledError",
]
