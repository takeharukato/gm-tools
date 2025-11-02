# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import random
import shlex
import tarfile
import tempfile
from pathlib import PurePosixPath
from typing import Iterable, List, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    import paramiko  # type: ignore


def _run(ssh: "paramiko.SSHClient", cmd: str, timeout: float) -> Tuple[int, bytes, bytes]:
    _stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read()
    err = stderr.read()
    rc = stdout.channel.recv_exit_status()
    return rc, out, err


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

    # 1) 一時リストにパス列挙
    content = "\n".join(abs_paths) + "\n"
    put_cmd = f"cat > {shlex.quote(lst)}"
    ch_in, ch_out, ch_err = ssh.exec_command(put_cmd, timeout=timeout)
    try:
        ch_in.write(content)
        ch_in.channel.shutdown_write()
        _ = ch_out.read()
        _ = ch_err.read()
        rc = ch_out.channel.recv_exit_status()
        if rc != 0:
            raise RuntimeError("prepare list failed")
    finally:
        try:
            ch_in.close()
            ch_out.close()
            ch_err.close()
        except Exception:
            pass

    sudo = "sudo -n " if use_sudo else ""
    deref = " -h" if follow_symlinks else ""

    # 2) tar作成（-P を付けない：アーカイブ内部は相対パスにする）
    cmd_tar = f"{sudo}sh -lc 'LC_ALL=C tar{deref} -cf {shlex.quote(tarf)} -T {shlex.quote(lst)}'"
    rc, _out, err = _run(ssh, cmd_tar, timeout)
    if rc != 0:
        _run(ssh, f"rm -f {shlex.quote(lst)}", timeout)
        raise RuntimeError(f"tar failed: {err.decode(errors='ignore')}")

    # 3) gzip圧縮
    cmd_gz = f"{sudo}sh -lc 'LC_ALL=C gzip -f {shlex.quote(tarf)}'"
    rc, _out, err = _run(ssh, cmd_gz, timeout)
    if rc != 0:
        _run(ssh, f"rm -f {shlex.quote(tarf)} {shlex.quote(lst)}", timeout)
        raise RuntimeError(f"gzip failed: {err.decode(errors='ignore')}")

    # 4) 一時リスト掃除（tgzは後でSFTP GETするため残す）
    _run(ssh, f"rm -f {shlex.quote(lst)}", timeout)

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
