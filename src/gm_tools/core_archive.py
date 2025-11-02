#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import random
import shlex
import tarfile
from typing import List
from .core_ssh import run_cmd  # SSH 上でコマンド実行

try:
    import paramiko  # type: ignore
except Exception as e:
    raise RuntimeError("Paramiko is required: pip install paramiko") from e

def safe_extract(tf: tarfile.TarFile, base_dir: str) -> int:
    """
    Extract directories and regular files only, stripping leading '/' and
    preventing path traversal. Return number of regular files extracted.
    """
    cnt: int = 0
    for m in tf.getmembers():
        name_norm: str = os.path.normpath(m.name.lstrip("/"))
        if os.path.isabs(name_norm) or name_norm.startswith(".." + os.sep):
            continue
        if m.issym() or m.islnk():
            continue
        if not (m.isdir() or m.isfile()):
            continue
        m.name = name_norm
        tf.extract(m, path=base_dir)
        if m.isfile():
            cnt += 1
    return cnt


def download_and_extract_tar(
    sftp: "paramiko.SFTPClient",  # type: ignore
    remote_tar_gz: str,
    extract_base: str,
    subdir: str,
    *,
    verbose: bool = False,
) -> int:
    """
    Download remote .tar.gz via SFTP to a temp file, extract safely under
    '<extract_base>/<subdir>', and remove the temp file. Returns file count.
    """
    import tempfile
    os.makedirs(os.path.join(extract_base, subdir), exist_ok=True)
    safe_prefix: str = subdir.replace(os.sep, "_")
    with tempfile.NamedTemporaryFile(prefix=f"{safe_prefix}_", suffix=".tar.gz", delete=False) as tmpf:
        local_tar: str = tmpf.name
    try:
        if verbose:
            print(f"[pack] downloading {remote_tar_gz} -> {local_tar}")
        sftp.get(remote_tar_gz, local_tar)
        with tarfile.open(local_tar, mode="r:gz") as tf:
            base: str = os.path.join(extract_base, subdir)
            return safe_extract(tf, base)
    finally:
        try:
            os.remove(local_tar)
        except Exception:
            pass

def remote_pack_paths(
    ssh: "paramiko.SSHClient",
    abs_paths: List[str],
    *,
    timeout: float = 30.0,
    sudo: bool = False,
) -> str:
    """
    指定のリモート絶対パス群を、リモート側で 1 本の .tar.gz にまとめて作成して
    その .tar.gz の絶対パスを返す。

    - tar は -P（絶対パス保持）と -T（リストファイル）を使用
    - 改行を含むパスは安全のため除外
    - sudo が必要な場合は sudo -n を付与する
    """
    if not abs_paths:
        raise ValueError("abs_paths must not be empty")

    # 安全化：絶対パスのみ、改行を含むものは除外
    safe = [p for p in abs_paths if p.startswith("/") and "\n" not in p]
    if not safe:
        raise ValueError("no valid absolute paths to pack")

    ident = f"/tmp/collect_{os.getpid()}_{random.randint(10**6, 10**7 - 1)}"
    list_path = f"{ident}.lst"
    tar_path = f"{ident}.tar"
    gz_path = f"{tar_path}.gz"
    sudo_pfx = "sudo -n " if sudo else ""

    # 1) リストファイル投入（ヒアドキュメント）
    delim = f"__GG_{os.getpid()}_{random.randint(10**6,10**7-1)}__"
    list_payload = "\n".join(safe) + "\n"
    rc, _, err = run_cmd(
        ssh,
        f"{sudo_pfx}sh -c 'cat > {shlex.quote(list_path)} <<\"{delim}\"\n{list_payload}{delim}'",
        timeout,
    )
    if rc != 0:
        raise RuntimeError(f"prepare list failed: {err.decode(errors='ignore')}")

    # 2) tar -> gzip（完了後にリストファイルを削除）
    rc, _, err = run_cmd(
        ssh,
        f"{sudo_pfx}tar -P -cf {shlex.quote(tar_path)} -T {shlex.quote(list_path)} && "
        f"{sudo_pfx}gzip -f {shlex.quote(tar_path)} && "
        f"{sudo_pfx}rm -f {shlex.quote(list_path)}",
        timeout,
    )
    if rc != 0:
        raise RuntimeError(f"tar/gzip failed: {err.decode(errors='ignore')}")

    return gz_path
