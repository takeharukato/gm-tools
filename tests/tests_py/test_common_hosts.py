#!/usr/bin/env python3
# tests/tests_py/test_common_hosts.py
# 共通: hostsファイルユーティリティ
from __future__ import annotations

import os
import tempfile
from typing import IO, List

_CREATED_HOSTS_FILES: List[str] = []


def write_temp_hosts(hosts: List[str]) -> str:
    """
    一時 hosts ファイルを作成し、パスを返す。
    - 1行1ホストで UTF-8 テキストとして書き込む
    - 呼び出し側がライフサイクル管理（削除）を行う前提
    """
    fd: int
    path: str
    fd, path = tempfile.mkstemp(prefix="hosts_", text=True)
    os.close(fd)
    f: IO[str]
    with open(path, "w", encoding="utf-8") as f:
        i: int = 0
        n: int = len(hosts)
        while i < n:
            h: str = hosts[i]
            _ = f.write(h + "\n")
            i += 1
    try:
        _CREATED_HOSTS_FILES.append(path)
    except Exception:
        pass
    return path

def get_created_hosts_files() -> List[str]:
    return list(_CREATED_HOSTS_FILES)
