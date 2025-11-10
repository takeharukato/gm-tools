# -*- coding: utf-8 -*-
"""
gm_tools.core_archive
=====================

Local temporary artifact registry and archive helpers (pack/unpack).

Goals
-----
- Provide *local* temp registration/cleanup separate from remote temp control.
- Offer tar pack/unpack helpers with cooperative cancellation checkpoints.
- Do NOT handle logging here; callers must log via CLI-side facilities.
- Do NOT touch trial/processed counters; this layer is pure I/O utilities.

This module performs no side effects on import.
"""

from __future__ import annotations


import threading
import os
import random
import shlex
import shutil
import tarfile
import tempfile
import logging
from pathlib import Path
from pathlib import PurePosixPath
from typing import Callable, Dict, Iterable, List, Optional, Set, Literal, Tuple, TypeAlias

from .core_ssh import CancelledError, SSHClientLike, SFTPClientLike  # for consistent cancellation semantics
from .core_cmd_flavor import run_remote_cmd_capture

# ---- Typing helpers for tarfile modes ---------------------------------------

TarWriteMode: TypeAlias = Literal['w', 'w:gz', 'w:bz2', 'w:xz']
TarReadMode: TypeAlias = Literal['r', 'r:gz', 'r:bz2', 'r:xz']

_LOG = logging.getLogger(__name__)

# ---- Local temp registry (per host) -----------------------------------------

class _PerHost:
    __slots__ = ("temps",)

    def __init__(self) -> None:
        # Collection of absolute local paths (files or dirs) to be cleaned up.
        self.temps: Set[Path] = set()


_lock = threading.Lock()
_registry: Dict[str, _PerHost] = {}  # host -> _PerHost


def _bucket(host: str) -> _PerHost:
    with _lock:
        b = _registry.get(host)
        if b is None:
            b = _PerHost()
            _registry[host] = b
        return b


def register_local_temp(host: str, path: Path) -> None:
    """Register a *local* temporary file/dir path for later cleanup (idempotent)."""
    b = _bucket(host)
    with _lock:
        b.temps.add(Path(path).resolve())


def register_local_temps(host: str, paths: Iterable[Path]) -> None:
    """Register multiple local temporary paths (idempotent)."""
    b = _bucket(host)
    with _lock:
        for p in paths:
            b.temps.add(Path(p).resolve())


def create_local_temp(host: str, maker: Callable[[], Path]) -> Path:
    """
    Helper: create a local temp via `maker()` and register it.
    The `maker` callable should create the resource and return its absolute path.
    """
    p: Path = Path(maker()).resolve()
    register_local_temp(host, p)
    return p


def _rm_rf(path: Path) -> None:
    """Best-effort removal of file or directory (idempotent)."""
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
    """Delete all registered local temps for a host (idempotent)."""
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
    """Delete registered local temps for all hosts (idempotent)."""
    with _lock:
        hosts = list(_registry.keys())
    for h in hosts:
        cleanup_local_temp(h)


# ---- Tar helpers -------------------------------------------------------------

def _tar_mode_for_path(tar_path: Path) -> TarWriteMode:
    """
    Choose tarfile mode from extension.
    - .tar -> 'w'
    - .tar.gz / .tgz -> 'w:gz'
    - .tar.xz / .txz -> 'w:xz'
    - .tar.bz2 / .tbz2 -> 'w:bz2'
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
    """Read mode counterpart of `_tar_mode_for_path`."""
    name = tar_path.name.lower()
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        return "r:gz"
    if name.endswith(".tar.xz") or name.endswith(".txz"):
        return "r:xz"
    if name.endswith(".tar.bz2") or name.endswith(".tbz2"):
        return "r:bz2"
    return "r"


def _check_abort(abort_event: Optional[threading.Event]) -> None:
    if abort_event is not None and abort_event.is_set():
        raise CancelledError("operation aborted by user request")


def pack_directory_to_tar(
    src_dir: Path,
    tar_path: Path,
    *,
    arcname: Optional[str] = None,
    abort_event: Optional[threading.Event] = None,
) -> None:
    """
    Pack a directory into a tar archive (format guessed by suffix).
    - `src_dir` must exist and be a directory.
    - `arcname` is the archive root name (default: basename of `src_dir`).
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
    """
    Pack multiple paths into a tar archive.
    - If `base_dir` is provided, archive names will be relative to it.
    - Otherwise each entry uses its own basename as `arcname`.
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
    """
    Extract a tar archive to `dst_dir` (created if missing).
    - Format guessed by suffix (.tar, .tar.gz, .tar.xz, ...).
    - Caller is responsible for validation and path traversal checks if needed.
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
    """
    絶対パス群をリモートで .tar.gz にまとめ, そのリモートパスを返す。
      1) printfでリスト作成  =>  2) tar -cf  =>  3) gzip -f  =>  4) リスト削除
    - アーカイブ内部パスは相対 ( -Pは使わない )
    - follow_symlinks=True なら 'tar -h'
    - use_sudo=True なら 'sudo -n' で tar/gzip を実行
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
        # 2) tar 作成 ( -h は必要時のみ )
        deref = " -h" if follow_symlinks else ""
        q_tarf = shlex.quote(tarf)
        tar_cmd = f"LC_ALL=C tar{deref} -cf {q_tarf} -T {q_lst}"
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
        _ = run_remote_cmd_capture(ssh, ["bash", "-lc", f"rm -f {shlex.quote(tarf)} {q_lst} || true"], timeout=timeout)
        raise
    finally:
        # 4) リスト削除
        _ = run_remote_cmd_capture(ssh, ["bash", "-lc", f"rm -f {q_lst} || true"], timeout=timeout)

    return tgz


def _safe_members(base_dir: str, members: Iterable[tarfile.TarInfo]) -> List[tarfile.TarInfo]:
    """
    Tar 展開のパストラバーサル対策。
      - 絶対/ルート始まりを除外
      - '..' バックトラックを除外
      - 展開先が base_dir 配下に収まることを保証
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
    """
    リモート .tar.gz をローカルに保存し, extract_base/subdir に安全展開。
    戻り値: (extracted_count, extracted_paths)
      - extracted_count は「通常ファイル + ハードリンク」をカウント
      - extracted_paths は tar 内パス ( 相対 ) 一覧
    """

    tmp_path = None

    os.makedirs(extract_base, exist_ok=True)
    dest_root = os.path.join(extract_base, subdir)
    os.makedirs(dest_root, exist_ok=True)

    # [debug] 受信側：関数進入の観測
    if verbose:
        _LOG.info("[debug][pack][receiver] enter remote_tar_gz=%s extract_base=%s subdir=%s verbose=%s",
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

    #
    # tarの中身を確認する
    #
    if verbose:
        try:
            with tarfile.open(tmp_path, "r:gz") as tfobj:
                members = tfobj.getmembers()
                _LOG.info("archive peek (%d entries):", len(members))
                for m in members:
                    kind = "file" if m.isfile() else "symlink" if m.issym() \
                        else "hardlink" if m.islnk() else "other"
                    _LOG.info("  [%s] %s -> %s", kind, m.name, (m.linkname or ""))
        except Exception as e:
            _LOG.warning("archive peek via tarfile failed: %s", e)

    extracted = 0
    extracted_paths: List[str] = []
    try:
        with tarfile.open(str(tmp_path), mode="r:gz") as tf:
            members = list(_safe_members(dest_root, tf.getmembers()))
            for m in members:
                tf.extract(m, dest_root)  # 安全化済みメンバーのみ
                if m.isfile() or m.islnk():       # ハードリンクも1件として数える
                    extracted += 1
                    extracted_paths.append(str(PurePosixPath(m.name)))
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass

    # verbose 時のみログ出力（QueueHandler/QueueListener配下で直列化され行崩れ防止）
    if verbose:
        _LOG.info(
            "[pack] downloaded %s -> extracted %d file(s) to %s",
            remote_tar_gz, extracted, dest_root,
        )

    return extracted, extracted_paths


def list_tar_members_local(tar_path: str) -> Tuple[List[str], List[str]]:
    """
    ローカル tar.gz のメンバーをハードニングして列挙する。
    戻り値は (regular_files, empty_dirs) のタプル。
      - regular_files: 通常ファイルのみ ( symlink/ハードリンク/デバイス等は含めない )
      - empty_dirs   : 空ディレクトリ ( 配下にメンバーを持たないディレクトリ )
    いずれもアーカイブ内の相対パス ( 先頭の '/' は除去 ) で返す。
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
