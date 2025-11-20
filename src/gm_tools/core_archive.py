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

"""ローカル一時ファイル管理と tar アーカイブ操作を提供するモジュール。

ホスト単位のローカル一時ファイル登録・削除と、tar 形式での
パッキング/アンパッキング補助関数をまとめて公開する。

Examples:
    >>> from pathlib import Path
    >>> import tempfile
    >>> import tarfile
    >>> from gm_tools.core_archive import pack_directory_to_tar
    >>> with tempfile.TemporaryDirectory() as tmp:
    ...     src = Path(tmp) / "src"
    ...     src.mkdir()
    ...     _ = (src / "hello.txt").write_text("hello", encoding="utf-8")
    ...     tar_path = Path(tmp) / "bundle.tar"
    ...     pack_directory_to_tar(src, tar_path)
    ...     tarfile.open(tar_path).getnames()
    ['src', 'src/hello.txt']
"""

from __future__ import annotations

import threading
import re
import os
import random
import shlex
import shutil
import tarfile
import tempfile
import logging
from pathlib import Path
from pathlib import PurePosixPath
from typing import Callable, Dict, Iterable, List, Optional, Set, Literal, Tuple
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    # 型チェッカー向けのダミー定義（実行時には評価されない）
    from gettext import gettext as _

from .core_ssh import CancelledError, SSHClientLike, SFTPClientLike  # for consistent cancellation semantics
from .core_cmd_flavor import (
    run_remote_cmd_capture,
    detect_tar_flavor_remote,
)

# ローカル展開名を core_path_handling の規則と整合させるために再利用
from .core_path_handling import (
    WINDOWS_FORBIDDEN_RE,
    WINDOWS_RESERVED_DEVICES,
    WINDOWS_TRAILING_STRIP,
)

# ---- Typing helpers for tarfile modes ---------------------------------------
TarWriteMode = Literal['w', 'w:gz', 'w:bz2', 'w:xz']
TarReadMode = Literal['r', 'r:gz', 'r:bz2', 'r:xz']

_LOG = logging.getLogger(__name__)

# ---- Local temp registry (per host) -----------------------------------------

class _PerHost:
    """ホスト単位でローカル一時パス集合を保持する内部クラス。"""

    __slots__ = ("temps",)

    def __init__(self) -> None:
        """絶対パスの一時ファイル群を空集合で初期化する。"""
        self.temps: Set[Path] = set()


_lock = threading.Lock()
_registry: Dict[str, _PerHost] = {}  # host -> _PerHost


def _bucket(host: str) -> _PerHost:
    """ホスト名に対応する内部レジストリを取得する。

    Args:
        host (str): レジストリを参照したいホスト名。

    Returns:
        _PerHost: 指定ホストの一時パス集合を格納するバケット。

    Examples:
        >>> from gm_tools import core_archive
        >>> core_archive.cleanup_all_local_temps()
        >>> bucket = core_archive._bucket("example-host")
        >>> isinstance(bucket, core_archive._PerHost)
        True
    """
    with _lock:
        b = _registry.get(host)
        if b is None:
            b = _PerHost()
            _registry[host] = b
        return b


def register_local_temp(host: str, path: Path) -> None:
    """ローカル一時パスを登録し、後から確実に削除できるようにする。

    Args:
        host (str): 管理対象のホスト名。
        path (Path): 登録したいファイルまたはディレクトリのパス。

    Examples:
        >>> from pathlib import Path
        >>> import tempfile
        >>> from gm_tools import core_archive
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     p = Path(tmp) / "temp.txt"
        ...     _ = p.write_text("x", encoding="utf-8")
        ...     core_archive.register_local_temp("example", p)
        ...     core_archive.cleanup_local_temp("example")
        ...     p.exists()
        False
    """
    b = _bucket(host)
    with _lock:
        b.temps.add(Path(path).resolve())


def register_local_temps(host: str, paths: Iterable[Path]) -> None:
    """複数のローカル一時パスをまとめて登録する。

    Args:
        host (str): 管理対象のホスト名。
        paths (Iterable[Path]): 登録したい一時パスの反復可能オブジェクト。

    Examples:
        >>> from pathlib import Path
        >>> import tempfile
        >>> from gm_tools import core_archive
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     files = [Path(tmp) / name for name in ("a", "b")]
        ...     for f in files:
        ...         _ = f.write_text("x", encoding="utf-8")
        ...     core_archive.register_local_temps("example", files)
        ...     core_archive.cleanup_local_temp("example")
        ...     [f.exists() for f in files]
        [False, False]
    """
    b = _bucket(host)
    with _lock:
        for p in paths:
            b.temps.add(Path(p).resolve())


def create_local_temp(host: str, maker: Callable[[], Path]) -> Path:
    """`maker()` で作成した一時パスを登録して返す補助関数。

    Args:
        host (str): 登録対象のホスト名。
        maker (Callable[[], Path]): 一時ファイル・ディレクトリを作成しパスを返すコールバック。

    Returns:
        Path: `maker()` が作成し登録した絶対パス。

    Examples:
        >>> from pathlib import Path
        >>> import tempfile
        >>> from gm_tools import core_archive
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     def maker() -> Path:
        ...         p = Path(tmp) / "created.txt"
        ...         _ = p.write_text("data", encoding="utf-8")
        ...         return p
        ...     created = core_archive.create_local_temp("example", maker)
        ...     created.exists()
        True
        >>> core_archive.cleanup_local_temp("example")
    """
    p: Path = Path(maker()).resolve()
    register_local_temp(host, p)
    return p


def _rm_rf(path: Path) -> None:
    """ファイルまたはディレクトリを可能な範囲で削除する。

    Args:
        path (Path): 削除対象のファイルもしくはディレクトリのパス。

    Examples:
        >>> from pathlib import Path
        >>> import tempfile
        >>> from gm_tools.core_archive import _rm_rf
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     target = Path(tmp) / "sub"
        ...     target.mkdir()
        ...     _rm_rf(target)
        ...     target.exists()
        False
    """
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                path.unlink(missing_ok=True)  # py3.8+: use try/except for older
            except TypeError:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
    except Exception:
        # Never raise
        pass


def cleanup_local_temp(host: str) -> None:
    """指定ホストに登録された一時パスを削除する。

    Args:
        host (str): 登録を解放したいホスト名。

    Examples:
        >>> from pathlib import Path
        >>> import tempfile
        >>> from gm_tools import core_archive
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     target = Path(tmp) / "temp.txt"
        ...     _ = target.write_text("x", encoding="utf-8")
        ...     core_archive.register_local_temp("example", target)
        ...     core_archive.cleanup_local_temp("example")
        ...     target.exists()
        False
    """
    with _lock:
        b = _registry.get(host)
        paths: List[Path] = list(b.temps) if b is not None else []
    if not paths:
        return

    for p in paths:
        _rm_rf(p)

    with _lock:
        _registry.pop(host, None)


def cleanup_all_local_temps() -> None:
    """登録済みの全ホストに対する一時パスを削除する。

    `cleanup_local_temp()` と同様に冪等であり、既に削除済みでも安全に呼び出せる。

    Examples:
        >>> from pathlib import Path
        >>> import tempfile
        >>> from gm_tools import core_archive
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     files = [Path(tmp) / name for name in ("h1.txt", "h2.txt")]
        ...     for idx, f in enumerate(files):
        ...         _ = f.write_text("x", encoding="utf-8")
        ...         core_archive.register_local_temp(f"host{idx}", f)
        ...     core_archive.cleanup_all_local_temps()
        ...     any(f.exists() for f in files)
        False
    """
    with _lock:
        hosts = list(_registry.keys())
    for h in hosts:
        cleanup_local_temp(h)


# ---- Tar helpers -------------------------------------------------------------

def _tar_mode_for_path(tar_path: Path) -> TarWriteMode:
    """拡張子から tarfile の書き込みモードを推定する。

    以下の規則でモードを切り替え、該当しない場合は ``'w'`` を返す。
    - ``*.tar`` -> ``'w'``
    - ``*.tar.gz`` / ``*.tgz`` -> ``'w:gz'``
    - ``*.tar.xz`` / ``*.txz`` -> ``'w:xz'``
    - ``*.tar.bz2`` / ``*.tbz2`` -> ``'w:bz2'``

    Args:
        tar_path (Path): 作成する tar アーカイブのパス。

    Returns:
        TarWriteMode: tarfile.open で利用する書き込みモード。

    Examples:
        >>> from pathlib import Path
        >>> from gm_tools.core_archive import _tar_mode_for_path
        >>> _tar_mode_for_path(Path("bundle.tar.gz"))
        'w:gz'
    """
    name = tar_path.name.lower()
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        return "w:gz"
    if name.endswith(".tar.xz") or name.endswith(".txz"):
        return "w:xz"
    if name.endswith(".tar.bz2") or name.endswith(".tbz2"):
        return "w:bz2"
    return "w"


def _tar_read_mode_for_path(tar_path: Path) -> TarReadMode:
    """拡張子から tarfile の読み取りモードを推定する。

    Args:
        tar_path (Path): 読み込みたい tar アーカイブのパス。

    Returns:
        TarReadMode: tarfile.open で使用する読み取りモード。

    Examples:
        >>> from pathlib import Path
        >>> from gm_tools.core_archive import _tar_read_mode_for_path
        >>> _tar_read_mode_for_path(Path("bundle.tar.xz"))
        'r:xz'
    """
    name = tar_path.name.lower()
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        return "r:gz"
    if name.endswith(".tar.xz") or name.endswith(".txz"):
        return "r:xz"
    if name.endswith(".tar.bz2") or name.endswith(".tbz2"):
        return "r:bz2"
    return "r"


def _check_abort(abort_event: Optional[threading.Event]) -> None:
    """中断イベントがセットされていれば ``CancelledError`` を送出する。

    Args:
        abort_event (Optional[threading.Event]): 中断判定に使用するイベント。 ``None`` なら無視する。

    Raises:
        CancelledError: ``abort_event`` がセットされている場合。

    Examples:
        >>> import threading
        >>> from gm_tools.core_archive import _check_abort
        >>> from gm_tools.core_ssh import CancelledError
        >>> event = threading.Event()
        >>> _check_abort(event)
        >>> event.set()
        >>> _check_abort(event)
        Traceback (most recent call last):
        ...
        gm_tools.core_ssh.CancelledError: operation aborted by user request
    """
    if abort_event is not None and abort_event.is_set():
        raise CancelledError("operation aborted by user request")


def pack_directory_to_tar(
    src_dir: Path,
    tar_path: Path,
    *,
    arcname: Optional[str] = None,
    abort_event: Optional[threading.Event] = None,
) -> None:
    """ディレクトリ全体を tar アーカイブに梱包する。

    出力フォーマットは ``tar_path`` の拡張子から推測されるため、拡張子に応じた
    圧縮方式（``.tar.gz`` など）を指定する必要がある。 ``src_dir`` は事前に存在する
    ディレクトリでなければならず、アーカイブ内でのルート名は ``arcname`` 引数で制御する。

    - ``src_dir`` は存在するディレクトリであること。
    - ``arcname`` を省略した場合は ``src_dir`` の末尾名が使用される。

    Args:
        src_dir (Path): アーカイブ対象のディレクトリ。
        tar_path (Path): 出力する tar アーカイブのパス。
        arcname (Optional[str]): アーカイブ内のルート名。 ``None`` なら ``src_dir`` の末尾名。
        abort_event (Optional[threading.Event]): 中断検出用イベント。

    Raises:
        CancelledError: 中断が要求された場合。
        FileNotFoundError: ``src_dir`` が存在しない場合。

    Examples:
        >>> from pathlib import Path
        >>> import tarfile
        >>> import tempfile
        >>> from gm_tools.core_archive import pack_directory_to_tar
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     src = Path(tmp) / "src"
        ...     src.mkdir()
        ...     _ = (src / "hello.txt").write_text("hello", encoding="utf-8")
        ...     tar_path = Path(tmp) / "bundle.tar"
        ...     pack_directory_to_tar(src, tar_path)
        ...     tarfile.open(tar_path).getnames()
        ['src', 'src/hello.txt']
    """
    _check_abort(abort_event)
    sd = Path(src_dir)
    tp = Path(tar_path)
    if arcname is None:
        arcname = sd.name

    mode: TarWriteMode = _tar_mode_for_path(tp)
    # Long I/O: creation/open
    _check_abort(abort_event)
    with tarfile.open(str(tp), mode) as tf:
        # Long I/O: adding
        _check_abort(abort_event)
        tf.add(sd, arcname=arcname, recursive=True)
        _check_abort(abort_event)


def pack_paths_to_tar(
    paths: Iterable[Path],
    tar_path: Path,
    *,
    base_dir: Optional[Path] = None,
    abort_event: Optional[threading.Event] = None,
) -> None:
    """複数パスを 1 つの tar アーカイブに集約する。

    Args:
        paths (Iterable[Path]): アーカイブ対象のファイルまたはディレクトリ群。
        tar_path (Path): 出力する tar アーカイブのパス。
        base_dir (Optional[Path]): 指定時はこのディレクトリからの相対パスで格納する。
        abort_event (Optional[threading.Event]): 中断検出用イベント。

    Raises:
        CancelledError: 中断が要求された場合。

    Examples:
        >>> from pathlib import Path
        >>> import tarfile
        >>> import tempfile
        >>> from gm_tools.core_archive import pack_paths_to_tar
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     base = Path(tmp) / "base"
        ...     base.mkdir()
        ...     files = []
        ...     for name in ("a.txt", "b.txt"):
        ...         p = base / name
        ...         _ = p.write_text("x", encoding="utf-8")
        ...         files.append(p)
        ...     tar_path = Path(tmp) / "bundle.tar.gz"
        ...     pack_paths_to_tar(files, tar_path, base_dir=base)
        ...     sorted(tarfile.open(tar_path).getnames())
        ['a.txt', 'b.txt']
    """
    _check_abort(abort_event)
    tp = Path(tar_path)
    mode: TarWriteMode = _tar_mode_for_path(tp)

    with tarfile.open(str(tp), mode) as tf:
        for p0 in paths:
            _check_abort(abort_event)
            p = Path(p0)
            if base_dir is not None:
                try:
                    arcname = str(p.resolve().relative_to(Path(base_dir).resolve()))
                except Exception:
                    # Fallback to basename if not under base_dir
                    arcname = p.name
            else:
                arcname = p.name
            tf.add(p, arcname=arcname, recursive=True)
            _check_abort(abort_event)


def unpack_tar_to_directory(
    tar_path: Path,
    dst_dir: Path,
    *,
    abort_event: Optional[threading.Event] = None,
) -> None:
    """tar アーカイブを指定ディレクトリへ展開する。

    展開先ディレクトリ ``dst_dir`` が存在しない場合は自動作成される。
    フォーマットは ``tar_path`` の拡張子から ``.tar`` / ``.tar.gz`` / ``.tar.xz`` / ``.tar.bz2`` 等を
    自動判定する。パストラバーサル対策や展開後の検証は呼び出し側で適切に行うこと。

    Args:
        tar_path (Path): 読み出す tar アーカイブのパス。
        dst_dir (Path): 展開先ディレクトリ。存在しない場合は作成される。
        abort_event (Optional[threading.Event]): 中断検出用イベント。

    Raises:
        CancelledError: 中断が要求された場合。

    Examples:
        >>> from pathlib import Path
        >>> import tempfile
        >>> from gm_tools.core_archive import pack_directory_to_tar, unpack_tar_to_directory
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     src = Path(tmp) / "src"
        ...     src.mkdir()
        ...     _ = (src / "file.txt").write_text("data", encoding="utf-8")
        ...     tar_path = Path(tmp) / "src.tar"
        ...     pack_directory_to_tar(src, tar_path)
        ...     dst = Path(tmp) / "dst"
        ...     unpack_tar_to_directory(tar_path, dst)
        ...     (dst / "src" / "file.txt").read_text(encoding="utf-8")
        'data'
    """
    _check_abort(abort_event)
    tp = Path(tar_path)
    dd = Path(dst_dir)
    dd.mkdir(parents=True, exist_ok=True)

    mode: TarReadMode = _tar_read_mode_for_path(tp)
    with tarfile.open(str(tp), mode) as tf:
        _check_abort(abort_event)
        tf.extractall(str(dd))  # noqa: S202 (trusted inputs expected by callers)
        _check_abort(abort_event)

#
# 外部IF
#

def remote_pack_paths(
    ssh: SSHClientLike,       # paramiko.SSHClient 想定 ( 型固定しない )
    abs_paths: List[str],
    *,
    timeout: float = 30.0,
    use_sudo: bool = False,
    follow_symlinks: bool = False,
) -> str:
    """リモートホストで絶対パス群を ``.tar.gz`` にまとめる。

    1. ``printf`` でパス一覧ファイルを作成。
    2. ``tar`` で ``.tar`` を生成。
    3. ``gzip`` で圧縮。
    4. 作業ファイルを削除し、生成したアーカイブパスを返す。

    Args:
        ssh (SSHClientLike): ``paramiko.SSHClient`` 互換オブジェクト。
        abs_paths (List[str]): 収集対象のリモート絶対パス。
        timeout (float): コマンド実行のタイムアウト秒。
        use_sudo (bool): ``True`` なら ``sudo -n`` 経由で実行する。
        follow_symlinks (bool): ``True`` なら判別した tar フレーバに応じて ``--dereference`` 相当を付与する。

    Returns:
        str: 作成した ``.tar.gz`` のリモート絶対パス。

    Raises:
        ValueError: ``abs_paths`` が空の場合。
        RuntimeError: ``tar`` または ``gzip`` の実行が失敗した場合。

    Examples:
        >>> from gm_tools.core_archive import remote_pack_paths
        >>> class DummySSH:
        ...     pass
        >>> remote_pack_paths(DummySSH(), [])
        Traceback (most recent call last):
        ...
        ValueError: remote_pack_paths: no paths
    """
    if not abs_paths:
        raise ValueError("remote_pack_paths: no paths")

    ident = f"/tmp/collect_abs_{os.getpid()}_{random.randint(10**6,10**7-1)}"
    lst = f"{ident}.lst"
    tarf = f"{ident}.tar"
    tgz = f"{tarf}.gz"

    q_lst = shlex.quote(lst)
    q_paths = " ".join(shlex.quote(p) for p in abs_paths)
    # 1) list 作成 ( sudo なし )
    rc_l, _out_l, err_l = run_remote_cmd_capture(ssh, ["bash", "-lc", f"printf '%s\\n' {q_paths} > {q_lst}"], timeout=timeout)
    if rc_l != 0:
        raise RuntimeError(f"prepare list failed: {(err_l or '').strip()}")

    try:
        # 2) tar 作成（follow_symlinks のときのみフレーバ別に deref フラグを付与）
        deref_flag = ""
        if follow_symlinks:
            try:
                tar_flavor = detect_tar_flavor_remote(ssh, timeout=timeout).tar
            except Exception:
                tar_flavor = "unknown"
            if tar_flavor == "gnu":
                deref_flag = "-h"          # GNU tar: --dereference と等価
            elif tar_flavor == "bsdtar":
                deref_flag = "-L"          # libarchive/bsdtar 系
            else:
                # フレーバ不明: 警告しフラグ無しで続行（symlinkはリンクのまま保存される）
                _LOG.warning(_(
                                "remote_pack_paths: follow_symlinks requested but remote tar flavor is unknown;"
                               "proceeding without dereference flag (symlinks will be archived as links)."
                               )
                )

        q_tarf = shlex.quote(tarf)
        tar_cmd = f"LC_ALL=C tar {deref_flag} -cf {q_tarf} -T {q_lst}".strip()
        if follow_symlinks and deref_flag:
            _LOG.debug("remote_pack_paths: using dereference flag '%s' for tar", deref_flag)
        tar_argv = (["sudo", "-n"] if use_sudo else []) + ["bash", "-lc", tar_cmd]
        rc_t, _o_t, err_t = run_remote_cmd_capture(ssh, tar_argv, timeout=timeout)
        if rc_t != 0:
            raise RuntimeError(f"tar failed: {(err_t or '(no stderr)').strip()}")

        # 3) gzip 圧縮
        gz_cmd = f"LC_ALL=C gzip -f {q_tarf}"
        gz_argv = (["sudo", "-n"] if use_sudo else []) + ["bash", "-lc", gz_cmd]
        rc_g, _o_g, err_g = run_remote_cmd_capture(ssh, gz_argv, timeout=timeout)
        if rc_g != 0:
            raise RuntimeError(f"gzip failed: {(err_g or '(no stderr)').strip()}")
    except Exception:
        # 失敗時の掃除 ( ベストエフォート )
        _tmp_cleanup = run_remote_cmd_capture(
            ssh, ["bash", "-lc", f"rm -f {shlex.quote(tarf)} {shlex.quote(tgz)} {q_lst} || true"], timeout=timeout)
        raise
    finally:
        # 4) リスト削除
        _tmp_cleanup = run_remote_cmd_capture(ssh, ["bash", "-lc", f"rm -f {q_lst} || true"], timeout=timeout)

    return tgz

def _safe_members(base_dir: str, members: Iterable[tarfile.TarInfo]) -> List[tarfile.TarInfo]:
    """tar 展開時のパストラバーサル対策として安全なメンバーのみ抽出する。

    - 先頭 ``/`` や ``\\`` を含む絶対パス・ルート始まりは除外する。
    - ``..`` を含むバックトラック要素を除去する。
    - 展開先が ``base_dir`` 配下に収まることを ``os.path.abspath`` で検証する。

    Args:
        base_dir (str): 展開先ベースディレクトリの絶対パス。
        members (Iterable[tarfile.TarInfo]): tarfile が列挙したメンバー。

    Returns:
        List[tarfile.TarInfo]: ``base_dir`` 配下に安全に展開できるメンバー。

    Examples:
        >>> import tarfile
        >>> from gm_tools.core_archive import _safe_members
        >>> base = "/tmp"
        >>> ok = tarfile.TarInfo("safe/file.txt")
        >>> bad = tarfile.TarInfo("../evil.txt")
        >>> [m.name for m in _safe_members(base, [ok, bad])]
        ['safe/file.txt']
    """
    base_abs = os.path.abspath(base_dir)
    safe: List[tarfile.TarInfo] = []
    for m in members:
        name = m.name
        p = PurePosixPath(name)
        if p.is_absolute() or name.startswith(("/", "\\")):
            continue
        if ".." in p.parts:
            continue
        dest_abs = os.path.abspath(os.path.join(base_abs, str(p)))
        if not (dest_abs == base_abs or dest_abs.startswith(base_abs + os.sep)):
            continue
        safe.append(m)
    return safe


def download_and_extract_tar(
    sftp: SFTPClientLike,   # paramiko.SFTPClient 想定 ( 型固定しない )
    remote_tar_gz: str,
    extract_base: str,
    subdir: str,
    *,
    verbose: bool = False,
) -> Tuple[int, List[str]]:
    """リモート ``.tar.gz`` を取得し安全に展開する。

        - リモートから ``.tar.gz`` を受信し、 ``extract_base/subdir`` に安全展開する。
        - パストラバーサルや symlink の混入を避けるためのハードニングを適用する。
        - GNU tar の最適化で生成される hardlink も通常ファイルとして復元する。
        - 戻り値は ``(extracted_count, extracted_paths)`` のタプル。
            ``extracted_count`` は通常ファイル（hardlink デマテリアライズ含む）の数、
            ``extracted_paths`` は正規化済み相対パス一覧。

    Args:
        sftp (SFTPClientLike): ``paramiko.SFTPClient`` 互換オブジェクト。
        remote_tar_gz (str): リモート上の ``.tar.gz`` ファイルパス。
        extract_base (str): 展開のベースディレクトリ。
        subdir (str): 展開先サブディレクトリ名。
        verbose (bool): デバッグログを冗長に出す場合 ``True``。

    Returns:
        Tuple[int, List[str]]: 抽出した通常ファイル数と正規化済み相対パスの一覧。

    Raises:
        OSError: ローカルへの保存や展開で失敗した場合。

    Examples:
        >>> import shutil
        >>> import tarfile
        >>> import tempfile
        >>> from pathlib import Path
        >>> from gm_tools.core_archive import download_and_extract_tar, pack_directory_to_tar
        >>> class DummySFTP:
        ...     def __init__(self, source: Path) -> None:
        ...         self.source = source
        ...     def get(self, remote: str, local: str) -> None:
        ...         shutil.copy(self.source, local)
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     src_dir = Path(tmp) / "src"
        ...     src_dir.mkdir()
        ...     _ = (src_dir / "x.txt").write_text("1", encoding="utf-8")
        ...     tar_path = Path(tmp) / "bundle.tar.gz"
        ...     pack_directory_to_tar(src_dir, tar_path)
        ...     dest_base = Path(tmp) / "dest"
        ...     sftp = DummySFTP(tar_path)
        ...     count, rels = download_and_extract_tar(
        ...         sftp,
        ...         str(tar_path),
        ...         str(dest_base),
        ...         "extract",
        ...     )
        ...     count
        1
        >>> sorted(rels)
        ['src/x.txt']
        >>> (dest_base / "extract" / "src" / "x.txt").read_text(encoding="utf-8")
        '1'
    """

    tmp_path = None

    os.makedirs(extract_base, exist_ok=True)
    dest_root = os.path.join(extract_base, subdir)
    os.makedirs(dest_root, exist_ok=True)

    # [debug] 受信側：関数進入の観測
    if verbose:
        _LOG.debug("[debug][pack][receiver] enter remote_tar_gz=%s extract_base=%s subdir=%s verbose=%s",
                remote_tar_gz, extract_base, subdir, verbose)

    with tempfile.NamedTemporaryFile(prefix="gm_dl_", suffix=".tar.gz", delete=False) as tmpf:
        tmp_path = tmpf.name
    try:
        sftp.get(remote_tar_gz, tmp_path)
    except Exception:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        raise

    # ---- Helpers for hardened extraction -----------------------------------
    def _normalize_archive_relpath_for_local(name: str) -> str:
        """アーカイブ内相対パスをローカル安全名に正規化する。

        以下のようなハードニングを順番に適用する。
        - ``\\`` を ``/`` に置換し、先頭 ``/`` を除去する。
        - ``X:/`` 形式を ``X_/`` に変換して Windows ドライブ記法を無効化する。
        - 連続する ``//`` や ``.`` の整理、および ``..`` を含む要素の除外を行う。
        - Windows 禁止文字の置換、末尾スペース/ドットの削除、予約デバイス名からの退避を実施する。
        - 空要素や UNC 由来の空セグメントは ``_`` として保持する。

        Args:
            name (str): tar アーカイブに記録されたエントリ名。

        Returns:
            str: OS 依存の危険要素を除去した相対パス。

        Examples:
            >>> _normalize_archive_relpath_for_local("C:/tmp/..\\evil?.txt")  # doctest: +SKIP
            'C_/tmp/evil_.txt'
        """
        p = name.replace("\\", "/")
        p = p.lstrip("/")
        # 'X:/' → 'X_/'（最初の 1 か所だけ）
        if re.match(r'^[A-Za-z]:/', p):
            p = p.replace(":/", "_/", 1)
        # 末尾の '/' は抽出名としては無視
        had_trailing = p.endswith("/")
        parts_raw = p.split("/")
        if had_trailing:
            while parts_raw and parts_raw[-1] == "":
                parts_raw.pop()
        out: List[str] = []
        for comp in parts_raw:
            if comp == "":
                out.append("_")
                continue
            if comp == ".":
                continue
            if comp == "..":
                # _safe_members 側で排除済みのはずだが、念のため拒否
                continue
            comp = WINDOWS_FORBIDDEN_RE.sub("_", comp)
            comp = comp.rstrip(WINDOWS_TRAILING_STRIP)
            base = comp.split(".", 1)[0].upper()
            if base in WINDOWS_RESERVED_DEVICES:
                comp = "_" + (comp or "_")
            out.append(comp or "_")
        if not out:
            out.append("_")
        return "/".join(out)

    def _parent_chain_has_symlink(base_dir: str, relpath: str) -> bool:
        """親ディレクトリにシンボリックリンクが含まれるかを確認する。

        展開対象の親ディレクトリを ``os.lstat`` で順次検査し、途中にシンボリックリンクが
        混在していないかを確認して安全な展開を保証する。

        Args:
            base_dir (str): 展開ベースディレクトリの絶対パス。
            relpath (str): チェック対象の相対パス。

        Returns:
            bool: シンボリックリンクを検出した場合 ``True``。

        Examples:
            >>> import tempfile
            >>> from pathlib import Path
            >>> with tempfile.TemporaryDirectory() as tmp:  # doctest: +SKIP
            ...     base = Path(tmp)
            ...     (base / "dir").mkdir()
            ...     _parent_chain_has_symlink(str(base), "dir")
            False
        """
        base_abs = os.path.abspath(base_dir)
        cur = base_abs
        for part in PurePosixPath(relpath).parts:
            cur = os.path.abspath(os.path.join(cur, part))
            parent = os.path.dirname(cur)
            try:
                _st = os.lstat(parent)
            except FileNotFoundError:
                # これから mkdir する場合はここでは判断しない
                continue
            if os.path.islink(parent):
                return True
        return False

    extracted = 0
    extracted_paths: List[str] = []
    try:
        with tarfile.open(str(tmp_path), mode="r:gz") as tf:
            members_all = tf.getmembers()
            members = list(_safe_members(dest_root, members_all))

            # 1) 先にディレクトリを作成（正規化名で）
            dirs = [m for m in members if m.isdir()]
            for d in dirs:
                norm_dir = _normalize_archive_relpath_for_local(d.name)
                if _parent_chain_has_symlink(dest_root, norm_dir):
                    _LOG.warning(_("skip extracting directory due to symlinked parent: %s") % norm_dir)
                    continue
                target_dir = os.path.join(dest_root, norm_dir)
                os.makedirs(target_dir, exist_ok=True)

            # 索引用: アーカイブ内「正規化名 -> TarInfo」
            members_map: Dict[str, tarfile.TarInfo] = {}
            for _m in members:
                key = _normalize_archive_relpath_for_local(_m.name)
                if key not in members_map:
                    members_map[key] = _m

            # 2) 通常ファイルを抽出（symlink は抽出しない / hardlink は後段で特例処理）
            extracted_map: Dict[str, str] = {}  # 正規化相対 -> ローカル絶対
            files = [m for m in members if m.isfile()]

            for m in files:
                norm_rel = _normalize_archive_relpath_for_local(m.name)
                if _parent_chain_has_symlink(dest_root, norm_rel):
                    _LOG.warning(_("skip extracting file due to symlinked parent: %s") % norm_rel)
                    continue
                abs_out = os.path.join(dest_root, norm_rel)
                os.makedirs(os.path.dirname(abs_out), exist_ok=True)
                # 書き出し
                fobj = tf.extractfile(m)
                if fobj is None:
                    # 読み取り不能（デバイス等）はスキップ
                    continue
                with open(abs_out, "wb") as w:
                    shutil.copyfileobj(fobj, w)
                # パーミッション・時刻（可能な範囲で）を反映
                try:
                    os.chmod(abs_out, m.mode & 0o777)
                except Exception:
                    pass
                try:
                    os.utime(abs_out, (m.mtime, m.mtime))
                except Exception:
                    pass
                extracted += 1
                extracted_paths.append(norm_rel)
                extracted_map[norm_rel] = abs_out

            # 3) GNU tar 最適化由来のハードリンク（islnk）を「通常ファイル」としてデマテリアライズ
            hardlinks = [m for m in members if m.islnk()]
            for m in hardlinks:
                norm_rel = _normalize_archive_relpath_for_local(m.name)
                # 親チェーンに symlink があれば安全のため拒否
                if _parent_chain_has_symlink(dest_root, norm_rel):
                    _LOG.warning(_("skip extracting hardlink due to symlinked parent: %s -> %s") % (norm_rel, m.linkname))
                    continue
                abs_out = os.path.join(dest_root, norm_rel)
                os.makedirs(os.path.dirname(abs_out), exist_ok=True)

                # 3-1) 既に抽出済みのリンク先があれば、それをソースに複製
                norm_link = _normalize_archive_relpath_for_local(m.linkname or "")
                src_abs = extracted_map.get(norm_link)
                if src_abs and os.path.isfile(src_abs):
                    try:
                        with open(src_abs, "rb") as rf, open(abs_out, "wb") as wf:
                            shutil.copyfileobj(rf, wf)
                        try:
                            os.chmod(abs_out, m.mode & 0o777)
                        except Exception:
                            pass
                        try:
                            os.utime(abs_out, (m.mtime, m.mtime))
                        except Exception:
                            pass
                        extracted += 1
                        extracted_paths.append(norm_rel)
                        extracted_map[norm_rel] = abs_out
                        continue
                    except Exception as e:
                        _LOG.warning(_("failed to materialize hardlink from extracted source: %s -> %s (%s)") % (
                            norm_rel, norm_link, e))

                # 3-2) 未抽出なら、アーカイブ内の同名メンバー（通常ファイル）から抽出・複製
                src_member = members_map.get(norm_link)
                if src_member is not None and src_member.isfile():
                    try:
                        fobj2 = tf.extractfile(src_member)
                        if fobj2 is None:
                            raise RuntimeError("extractfile() returned None")
                        with open(abs_out, "wb") as wf2:
                            shutil.copyfileobj(fobj2, wf2)
                        try:
                            os.chmod(abs_out, src_member.mode & 0o777)
                        except Exception:
                            pass
                        try:
                            os.utime(abs_out, (src_member.mtime, src_member.mtime))
                        except Exception:
                            pass
                        extracted += 1
                        extracted_paths.append(norm_rel)
                        extracted_map[norm_rel] = abs_out
                        continue
                    except Exception as e:
                        _LOG.warning(_("failed to materialize hardlink from archive member: %s -> %s (%s)") % (
                            norm_rel, norm_link, e))

                # 3-3) いずれも解決できなければ警告してスキップ
                _LOG.warning(_("skip hardlink (unresolvable link target): %s -> %s") % (norm_rel, m.linkname))
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass

    # verbose 時のみログ出力（QueueHandler/QueueListener配下で直列化され行崩れ防止）
    if verbose:
        _LOG.info(
            _("[pack] downloaded %s -> extracted %d file(s) to %s") % (
                remote_tar_gz, extracted, dest_root )
        )

    return extracted, extracted_paths


def list_tar_members_local(tar_path: str) -> Tuple[List[str], List[str]]:
    """ローカル tar アーカイブの通常ファイルと空ディレクトリを列挙する。

    戻り値は ``(regular_files, empty_dirs)`` のタプルで、それぞれアーカイブ内の相対パス。
    - ``regular_files`` は symlink・hardlink・特殊ファイルを除外した通常ファイルのみ。
    - ``empty_dirs`` は配下にメンバーを持たないディレクトリのみ。
    - 先頭 ``/`` は除去し、必要に応じて末尾 ``/`` を調整して返す。

    Args:
        tar_path (str): 読み出す tar アーカイブのパス。

    Returns:
        Tuple[List[str], List[str]]: 通常ファイル名リストと空ディレクトリ名リスト。

    Examples:
        >>> import tempfile
        >>> from pathlib import Path
        >>> from gm_tools.core_archive import list_tar_members_local, pack_directory_to_tar
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     src = Path(tmp) / "src"
        ...     src.mkdir()
        ...     _ = (src / "a.txt").write_text("A", encoding="utf-8")
        ...     (src / "empty").mkdir()
        ...     tar_path = Path(tmp) / "src.tar"
        ...     pack_directory_to_tar(src, tar_path)
        ...     list_tar_members_local(str(tar_path))
        (['src/a.txt'], ['src/empty'])
    """
    regular_files: List[str] = []
    dirs: List[str] = []

    # 拡張子から読取モードを判定
    tp = Path(tar_path)
    read_mode: TarReadMode = _tar_read_mode_for_path(tp)
    with tarfile.open(str(tp), read_mode) as tf:
        members = tf.getmembers()
        for m in members:
            nm: str = m.name.lstrip("/")
            if not nm:
                continue
            # 通常ファイルのみ採用 ( symlink/hardlink は除外 )
            if m.isfile():
                regular_files.append(nm)
            elif m.isdir():
                # 判定用に末尾'/'で保持
                dirs.append(nm if nm.endswith("/") else nm + "/")

    # 空ディレクトリ判定 : 配下に他メンバーが存在しないもの
    all_names_set = set(m.name.lstrip("/") for m in members if m.name)
    empty_dirs: List[str] = []
    for d in dirs:
        has_child = any(n != d.rstrip("/") and n.startswith(d) for n in all_names_set)
        if not has_child:
            empty_dirs.append(d.rstrip("/")) # 空ディレクトリの場合は, 末尾'/'除去して返す

    return sorted(regular_files), sorted(empty_dirs)



__all__ = [
    # registry (local)
    "register_local_temp",
    "register_local_temps",
    "create_local_temp",
    "cleanup_local_temp",
    "cleanup_all_local_temps",
    # tar helpers
    "pack_directory_to_tar",
    "pack_paths_to_tar",
    "unpack_tar_to_directory",
    "remote_pack_paths",
    "download_and_extract_tar",
    "list_tar_members_local",
]
