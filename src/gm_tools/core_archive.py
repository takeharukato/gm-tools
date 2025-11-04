# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import random
import shlex
import tarfile
import tempfile
from pathlib import PurePosixPath
from typing import TYPE_CHECKING
from typing import Iterable, List, Tuple

if TYPE_CHECKING:
    import paramiko  # type: ignore

from .core_cmd_flavor import run_remote_cmd_capture

def remote_pack_paths(
    ssh: "paramiko.SSHClient",
    abs_paths: List[str],
    *,
    timeout: float = 30.0,
    use_sudo: bool = False,
    follow_symlinks: bool = False,
) -> str:
    """
    abs_paths（すべて絶対パス）をリモートで .tar.gz にまとめ、そのリモートパスを返す。
    - use_sudo=True なら 'sudo -n' で tar を実行
    - follow_symlinks=True なら 'tar -h' でリンク先を実体参照（壊れリンクは失敗になる点に注意）
    """
    if not abs_paths:
        raise ValueError("remote_pack_paths: no paths")

    ident = f"/tmp/collect_abs_{os.getpid()}_{random.randint(10**6,10**7-1)}"
    lst = f"{ident}.lst"
    tarf = f"{ident}.tar"
    tgz = f"{tarf}.gz"

    # 1) 一時リストにパス列挙（printf 経由で安全に書き込み）
    #    各要素は printf の % 展開を避けるためにクォートして "%s\n" で渡す
    q_lst = shlex.quote(lst)
    q_paths: List[str] = [shlex.quote(p) for p in abs_paths]
    printf_args: str = " ".join(q_paths)
    list_cmd: List[str] = ["bash", "-lc", f"printf '%s\\n' {printf_args} > {q_lst}"]
    rc_l, _out_l, err_l = run_remote_cmd_capture(ssh, list_cmd, timeout=timeout)
    if rc_l != 0:
        raise RuntimeError(f"prepare list failed: {err_l.strip()}")

    # 2) tar 作成（-P は付けない：アーカイブ内部は相対パス）
    deref_flag: str = " -h" if follow_symlinks else ""
    q_tarf: str = shlex.quote(tarf)
    tar_cmd_str: str = f"LC_ALL=C tar{deref_flag} -cf {q_tarf} -T {q_lst}"
    tar_argv: List[str] = (["sudo", "-n"] if use_sudo else []) + ["bash", "-lc", tar_cmd_str]
    rc_t, _out_t, err_t = run_remote_cmd_capture(ssh, tar_argv, timeout=timeout)
    if rc_t != 0:
        # list ファイルの掃除だけは試みる
        _ = run_remote_cmd_capture(ssh, ["bash", "-lc", f"rm -f {q_lst} || true"], timeout=timeout)
        raise RuntimeError(f"tar failed: {err_t.strip() or '(no stderr)'}")

    # 3) gzip 圧縮
    gz_cmd_str: str = f"LC_ALL=C gzip -f {q_tarf}"
    gz_argv: List[str] = (["sudo", "-n"] if use_sudo else []) + ["bash", "-lc", gz_cmd_str]
    rc_g, _out_g, err_g = run_remote_cmd_capture(ssh, gz_argv, timeout=timeout)
    if rc_g != 0:
        # tar と list の掃除だけは試みる
        _ = run_remote_cmd_capture(ssh, ["bash", "-lc", f"rm -f {q_tarf} {q_lst} || true"], timeout=timeout)
        raise RuntimeError(f"gzip failed: {err_g.strip() or '(no stderr)'}")

    # 4) 一時リスト掃除（tgzはこの後 SFTP GET するため残す）
    _ = run_remote_cmd_capture(ssh, ["bash", "-lc", f"rm -f {q_lst} || true"], timeout=timeout)

    return tgz


def _safe_members(base_dir: str, members: Iterable[tarfile.TarInfo]) -> list[tarfile.TarInfo]:
    """
    Tar 展開におけるパストラバーサル対策フィルタ。
    - 絶対パス/ドライブ指定/ルート始まりを排除
    - '..' を含む相対バックトラックを排除
    - 最終展開先が base_dir 配下に収まらないものを排除
    """
    base_abs = os.path.abspath(base_dir)
    safe: list[tarfile.TarInfo] = []

    for m in members:
        name = m.name  # tar 内のパスは POSIX 形式が前提
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
    sftp: "paramiko.SFTPClient",
    remote_tar_gz: str,
    extract_base: str,
    subdir: str,
    *,
    verbose: bool = False,
) -> Tuple[int, List[str]]:
    """
    リモートの .tar.gz を一旦ローカルのテンポラリにダウンロードし、
    extract_base/subdir/ 以下に安全に展開する。
    返り値:
      - extracted_count: 展開されたファイル数。通常ファイルに加え、
        tar のハードリンクエントリ（m.islnk()）もファイルとしてカウントする。
      - extracted_paths: 展開されたパス（dest_root からの相対パス）のリスト。
    """
    os.makedirs(extract_base, exist_ok=True)
    dest_root = os.path.join(extract_base, subdir)
    os.makedirs(dest_root, exist_ok=True)

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

    extracted = 0
    extracted_paths: List[str] = []
    try:
        with tarfile.open(tmp_path, mode="r:gz") as tf:
            members = list(_safe_members(dest_root, tf.getmembers()))
            for m in members:
                tf.extract(m, dest_root)
                # GNU tar は同一 inode を LNKTYPE（ハードリンク）として記録し得る。
                # その場合(m.islnk())も実体として 1 ファイルが出力されるためカウントに含める。
                if m.isfile() or m.islnk():
                    extracted += 1
                    # 記録名（Tar 内パス）を展開先相対で保持
                    extracted_paths.append(str(PurePosixPath(m.name)))
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass

    if verbose:
        print(f"[pack] downloaded {remote_tar_gz} -> extracted {extracted} file(s) to {dest_root}")
    return extracted, extracted_paths

def list_tar_members_local(tar_path: str) -> Tuple[List[str], List[str]]:
    """
    ローカル tar.gz のメンバーをハードニングして列挙する。
    戻り値は (regular_files, empty_dirs) のタプル。
      - regular_files: 通常ファイルのみ（symlink/ハードリンク/デバイス等は含めない）
      - empty_dirs   : 空ディレクトリ（配下にメンバーを持たないディレクトリ）
    いずれもアーカイブ内の相対パス（先頭の '/' は除去）で返す。
    """
    regular_files: List[str] = []
    dirs: List[str] = []

    with tarfile.open(tar_path, mode="r:gz") as tf:
        members = tf.getmembers()
        for m in members:
            nm: str = m.name.lstrip("/")
            if not nm:
                continue
            # 通常ファイルのみ採用（symlink/hardlink は除外）
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
