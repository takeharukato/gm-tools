# tests/tests_py/test_common_cleanup.py
# Step7: cleanup の統合責務モジュール

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Union, List, cast


# ============================================================
# 基本の安全削除機能
# ============================================================

def _safe_rmtree_abs(path: Union[str, Path]) -> None:
    """
    絶対パスに対してのみ安全に rmtree を実行する。
    - symlink なら削除しない
    - 相対パスなら何もしない
    - 存在しなければ何もしない
    - 例外は握りつぶす ( best effort )
    """
    try:
        p = Path(path)

        if not p.is_absolute():
            return

        if p.is_symlink():
            return

        if not p.exists():
            return

        shutil.rmtree(p, ignore_errors=True)

    except Exception:
        # cleanup は best-effort
        pass


def cleanup_dir(path: Union[str, Path]) -> None:
    """安全な削除ラッパー"""
    _safe_rmtree_abs(path)


# ============================================================
# Step7 統合 API
# ランナーは必ずこれらのみを使用する
# ============================================================

def init_local_work_root(cfg: Any) -> None:
    """
    Step4/5/6 runner が Config.local_work_root に基づき
    テスト用作業ディレクトリを作成する責務。
    """
    if not hasattr(cfg, "local_work_root"):
        return

    root = Path(cfg.local_work_root)

    try:
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
    except Exception:
        # best effort
        pass


def cleanup_local_work_root(cfg: Any) -> None:
    """
    Config.local_work_root を安全に削除。
    runner の終了時に必ず1回実行される想定。
    """
    if hasattr(cfg, "local_work_root"):
        cleanup_dir(Path(cfg.local_work_root))


def cleanup_extra_local_dirs(cfg: Any) -> None:
    """
    Step5/Step6 テストが追加で作るローカルディレクトリを削除する。
    - cfg.extra_local_dirs がリストで定義されていれば削除対象
    """
    dirs: List[Union[str, Path]] = []

    if hasattr(cfg, "extra_local_dirs"):
        raw = getattr(cfg, "extra_local_dirs")
        if isinstance(raw, list):
            dirs = cast(List[Union[str, Path]], raw)

    for d in dirs:
        cleanup_dir(Path(d))


# ============================================================
# 旧API: 互換性のため一応残す
# ============================================================

def cleanup_test_temp(cfg: Any) -> None:
    """
    test_common_config が test_temp_root を定義している場合に削除。
    旧 runner 互換のため残す。
    """
    if hasattr(cfg, "test_temp_root"):
        cleanup_dir(Path(cfg.test_temp_root))
