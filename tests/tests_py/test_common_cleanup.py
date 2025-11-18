from typing import Optional, Any, Union
import os
import shutil
from pathlib import Path
from .test_common_paths import ensure_under as _ensure_under


def safe_rmtree_abs(path: Union[str, Path], *, ensure_under: Optional[Union[str, Path]] = None) -> None:
    """
    絶対パスに対してのみ安全に rmtree を実行する（公開関数）。
    - ensure_under が指定された場合は、その配下の削除のみ許可。
    - path が symlink → 削除しない
    - path が存在しない → 無視
    - path が相対 → 何もしない（安全性のため）
    - 削除時の例外は握りつぶす
    """
    try:
        # 文字列化して安全確認（ensure_under は文字列パスで評価）
        p_str: str = os.path.abspath(str(path))
        if ensure_under is not None:
            base_str: str = os.path.abspath(str(ensure_under))
            if not _ensure_under(base_str, p_str):
                return

        p = Path(p_str)

        # 絶対パスでなければ何もしない（安全）
        if not p.is_absolute():
            return

        # symlink は削除しない（非常に危険なため除外）
        if p.is_symlink():
            return

        # 存在しなければ何もしない
        if not p.exists():
            return

        shutil.rmtree(p, ignore_errors=True)

    except Exception:
        # cleanup は best-effort とする
        pass


def cleanup_dir(path: Union[str, Path]) -> None:
    """
    任意パスを安全に削除するラッパー。
    """
    safe_rmtree_abs(path)


def create_clean_dir(path: str, *, ensure_under: Optional[str] = None) -> None:
    """
    ディレクトリ内容を安全にクリア（存在しなければ作成）。
    - ensure_under が与えられた場合は、その配下のみ動作。
    - 共有 cleanup_dir に委譲して削除後、空ディレクトリを再作成。
    """
    try:
        p: str = os.path.abspath(path)
        if ensure_under is not None and not _ensure_under(ensure_under, p):
            return
        if os.path.exists(p):
            cleanup_dir(p)
        os.makedirs(p, exist_ok=True)
    except Exception:
        # テストユーティリティのため、失敗時は握りつぶす
        pass


def cleanup_test_temp(cfg: Any) -> None:
    """
    cfg.test_temp_root で指定されたテスト用ディレクトリを削除する。
    test_common_config が test_temp_root を正規に決めるのが前提。
    """
    if hasattr(cfg, "test_temp_root"):
        cleanup_dir(Path(cfg.test_temp_root))
