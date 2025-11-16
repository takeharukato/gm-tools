# gm-tools-tests-20251116/tests_py/test_common_cleanup.py

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Iterable, Optional

from ._local_types import Config


def _safe_rmtree_abs(path_abs: Path, *, ensure_under: Optional[Path] = None) -> None:
    """
    安全な rmtree:
      - ensure_under が指定されていればその配下のみ削除。
      - パスが存在しなければ何もしない。
      - シンボリックリンクは削除しない。
      - 例外は握りつぶす（テスト結果を壊さないため）。
    """
    try:
        # p_abs: 実際に削除対象として扱うパス（シンボリックリンク判定はこちらで行う）
        p_abs: Path = path_abs if path_abs.is_absolute() else path_abs.absolute()
    except (OSError, RuntimeError):
        return

    # ensure_under が指定されている場合は「解決後のパス」で配下かどうかを判定する
    if ensure_under is not None:
        try:
            base: Path = ensure_under.resolve()
            p_resolved: Path = p_abs.resolve()
            p_resolved.relative_to(base)
        except Exception:
            # base 配下でなければ削除対象外
            return

    if not p_abs.exists():
        return

    # ★ ここは resolve していない p_abs で判定するので、シンボリックリンクを正しく検出できる
    if p_abs.is_symlink():
        # 誤爆防止: シンボリックリンクは削除しない
        return

    try:
        if p_abs.is_dir():
            shutil.rmtree(p_abs, ignore_errors=True)
        else:
            # 通常ファイルなど
            p_abs.unlink(missing_ok=True)  # type: ignore[attr-defined]
    except Exception:
        # best-effort
        pass

def _clear_dir(path: Path, *, ensure_under: Optional[Path] = None) -> None:
    """
    path で指定されたディレクトリを一度まるごと削除してから作り直す。
    - ensure_under が指定されていればその配下のみ削除対象。
    """
    _safe_rmtree_abs(path, ensure_under=ensure_under)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception:
        # best-effort
        pass


def init_local_work_root(cfg: Config, *, ensure_under_cwd: bool = True) -> None:
    """
    cfg.local_work_root を初期化するユーティリティ。
    - ensure_under_cwd=True のとき、カレントディレクトリ配下のみ削除・作成。
    """
    base: Optional[Path] = Path(os.getcwd()) if ensure_under_cwd else None
    _clear_dir(Path(cfg.local_work_root), ensure_under=base)


def cleanup_local_work_root(cfg: Config, *, ensure_under_cwd: bool = True) -> None:
    """
    cfg.local_work_root を best-effort で削除するユーティリティ。
    """
    base: Optional[Path] = Path(os.getcwd()) if ensure_under_cwd else None
    _safe_rmtree_abs(Path(cfg.local_work_root), ensure_under=base)


def cleanup_extra_local_dirs(
    rel_paths: Iterable[str],
    *,
    ensure_under_cwd: bool = True,
) -> None:
    """
    カレントディレクトリ配下の補助ディレクトリ（nf_rel など）を削除するユーティリティ。
    """
    cwd: Path = Path(os.getcwd())
    base: Optional[Path] = cwd if ensure_under_cwd else None
    for rel in rel_paths:
        _safe_rmtree_abs(cwd / rel, ensure_under=base)
