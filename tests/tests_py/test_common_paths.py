# tests/tests_py/test_common_paths.py
# 共有パスユーティリティ
from __future__ import annotations

import os
import pathlib
import fnmatch
from typing import Optional


def as_posix_rel(path_abs: str) -> str:
    """
    絶対パスをリモート展開用の相対表記へ正規化する:
        - OS 区切りを '/' に統一
        - 先頭の '/' はすべて除去
        - 末尾のスラッシュ有無は入力を尊重（存在すれば保持）
            例:
                '/tmp/a/b/'        -> 'tmp/a/b/'
                'C:\\work\\x\\y'   -> 'C/work/x/y'
    """
    s0: str = path_abs.replace("\\", "/")
    had_trailing: bool = s0.endswith("/")
    s: str = s0.lstrip("/")
    if had_trailing and not s.endswith("/"):
            s = s + "/"
    return s


def ensure_under(base: str, path_abs: str) -> bool:
    """
    path_abs が base 配下（または base と同一）であることを確認する。
    - base, path_abs は絶対パスで評価
    - シンボリックリンクは通さない（安全側）
    戻り値: True=許可, False=不許可
    """
    try:
        b = os.path.abspath(base)
        p = os.path.abspath(path_abs)
        if not (os.path.isabs(b) and os.path.isabs(p)):
            return False
        # ベース自身は許可
        if p == b:
            return True
        # 末尾セパレータを付与して prefix 判定
        b_slash = b + os.sep
        return p.startswith(b_slash)
    except Exception:
        return False


def walk_find_first(root: str, *, name: Optional[str] = None, pattern: Optional[str] = None) -> Optional[str]:
    """
    ローカルの出力ツリーを走査し、最初に一致したパスを返す。
    - name: 完全一致名（例: 'l.txt'）
    - pattern: グロブ（例: '**/src/l.txt'）
    戻り値は絶対パス。見つからなければ None。
    """
    root_path: pathlib.Path = pathlib.Path(root)
    if pattern:
        for p in root_path.rglob('*'):
            try:
                rel: str = str(p.relative_to(root_path))
            except Exception:
                rel = str(p)
            if fnmatch.fnmatch(rel, pattern):
                return str(p.resolve())
        return None
    if name:
        for p in root_path.rglob(name):
            return str(p.resolve())
    return None
