# tests/tests_py/test_common_local.py
from __future__ import annotations

import os
from typing import List, Optional, Any
from .test_common_cleanup import safe_rmtree_abs
from ._local_types import Config
from .gmwrap import gm_run_local_with_argv as _gm_run_local_argv_public
from ._local_types import LocalRun


def cleanup_local_temps(cfg: Config, rel_dirs: Optional[List[str]] = None) -> None:
    """
    共通のローカル一時ディレクトリクリーンアップ。
      - cfg.local_root を安全に削除
      - rel_dirs が与えられた場合、カレント配下の相対ディレクトリについても安全に削除
    """
    cwd: str = os.getcwd()
    safe_rmtree_abs(cfg.local_root, ensure_under=cwd)
    for d in (rel_dirs or []):
        abs_path: str = os.path.join(cwd, d)
        safe_rmtree_abs(abs_path, ensure_under=cwd)


def run_local_with_argv(argv: List[str]) -> LocalRun:
    """
    公開ローカル実行ヘルパ。
      - gmwrap 公開関数を使用してコマンドを実行し、rc/stdout/stderr を返す。
    """
    res: Any = _gm_run_local_argv_public(argv)
    return LocalRun(int(getattr(res, "rc", 0)), str(getattr(res, "stdout", "")), str(getattr(res, "stderr", "")))
