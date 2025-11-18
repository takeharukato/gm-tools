# tests/tests_py/test_common_local.py
from __future__ import annotations

import os
from typing import List, Optional
from .test_common_cleanup import cleanup_dir
from ._local_types import Config
from .test_common_paths import ensure_under as _ensure_under


def _safe_rmtree_abs(path_abs: str, *, ensure_under: Optional[str] = None) -> None:
    try:
        p: str = os.path.abspath(path_abs)
        if ensure_under is not None and not _ensure_under(ensure_under, p):
            return
        if not os.path.isabs(p):
            return
        if os.path.islink(p):
            return
        if os.path.exists(p):
            cleanup_dir(p)
    except Exception:
        pass


def cleanup_local_temps(cfg: Config, rel_dirs: Optional[List[str]] = None) -> None:
    """
    共通のローカル一時ディレクトリクリーンアップ。
      - cfg.local_root を安全に削除
      - rel_dirs が与えられた場合、カレント配下の相対ディレクトリについても安全に削除
    """
    cwd: str = os.getcwd()
    _safe_rmtree_abs(cfg.local_root, ensure_under=cwd)
    for d in (rel_dirs or []):
        abs_path: str = os.path.join(cwd, d)
        _safe_rmtree_abs(abs_path, ensure_under=cwd)
