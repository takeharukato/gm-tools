#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from pathlib import Path

def ensure_local_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)

def local_path_for_download(dest_dir: str, host: str, remote_abs_path: str) -> str:
    """
    Map remote ABS path to local: '/etc/hosts' -> '<dest>/<host>/etc/hosts'
    """
    rel: str = remote_abs_path.lstrip("/")
    return os.path.join(dest_dir, host, rel)
