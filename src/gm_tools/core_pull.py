#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass, field
import os

# Paramiko は実行時に必須（型注釈は forward ref でOK）
try:
    import paramiko  # type: ignore
except Exception as e:
    raise RuntimeError("Paramiko is required: pip install paramiko") from e

from .core_path_handling import (
    ensure_local_dir,
    local_path_for_download,
)

__all__ = [
    "HostResult",
    "download_one",
]

# ============================================================================

@dataclass
class HostResult:
    """ホスト単位の処理結果"""
    host: str
    downloaded: int = 0
    warnings: list[str] = field(default_factory=list)  # type: ignore
    errors: list[str] = field(default_factory=list)  # type: ignore

# ============================================================================

def download_one(
    sftp: "paramiko.SFTPClient",
    remote_abs_path: str,
    dest_base_dir: str,
    host: str,
) -> None:
    """
    単一リモートファイルをローカルにダウンロードする。

    保存先は DEST/<HOST>/abs/<remote_abs_path> の形に正規化する。
    例: remote_abs_path='/etc/hosts'、host='node1'、dest_base_dir='/tmp/out'
        -> '/tmp/out/node1/abs/etc/hosts'
    """
    local_abs: str = local_path_for_download(dest_base_dir, host, remote_abs_path)
    ensure_local_dir(os.path.dirname(local_abs))
    sftp.get(remote_abs_path, local_abs)
