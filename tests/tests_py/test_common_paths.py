"""
パス操作ユーティリティ。絶対パスの正規化と探索ヘルパを提供します。

Notes:
- 安全性を優先し、一部の関数は例外発生時に保守的な結果（False / None）を返します。
"""
# tests/tests_py/test_common_paths.py
# 共有パスユーティリティ
from __future__ import annotations

import os
import pathlib
import fnmatch
from typing import Optional


def as_posix_rel(path_abs: str) -> str:
    """
    絶対パスをリモート展開用の相対表記へ正規化します。区切りを "/" に統一し、
    先頭の "/" をすべて除去、末尾のスラッシュ有無は入力を尊重します。

    Args:
    - path_abs (str): 正規化対象の絶対パス。

    Returns:
    - str: POSIX 風に正規化された相対表記。

    Examples:
    - '/tmp/a/b/' -> 'tmp/a/b/'
    - 'C:\\work\\x\\y' -> 'C/work/x/y'
    """
    s0: str = path_abs.replace("\\", "/")
    had_trailing: bool = s0.endswith("/")
    s: str = s0.lstrip("/")
    if had_trailing and not s.endswith("/"):
        s = s + "/"
    return s


def ensure_under(base: str, path_abs: str) -> bool:
    """
    path_abs が base 配下（または base と同一）であることを確認します。両者は絶対パスで評価し、
    シンボリックリンクは通さない安全側の判定を行います。

    Args:
    - base (str): ベースとなる絶対パス。
    - path_abs (str): 確認対象の絶対パス。

    Returns:
    - bool: True は許可、False は不許可（例外発生時も False）。

    Notes:
    - ベース自身（path_abs == base）は許可されます。
    - 末尾セパレータを付与して接頭辞判定を行います。
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
    ローカルの出力ツリーを走査し、最初に一致したパスを返します。`name` は完全一致名、
    `pattern` は `fnmatch` 互換のグロブを受け付けます。

    Args:
    - root (str): 走査の起点ディレクトリ（絶対/相対いずれも可）。
    - name (Optional[str]): 完全一致で探すファイル名（例: 'l.txt'）。
    - pattern (Optional[str]): グロブパターン（例: '**/src/l.txt'）。

    Returns:
    - Optional[str]: 最初に一致した項目の絶対パス。見つからなければ None。

    Notes:
    - `pattern` 指定時は `fnmatch.fnmatch` による一致判定を行います。
    - `name` 指定時は `pathlib.Path.rglob` による名前一致で探索します。
    - 返却値は `Path.resolve()` により絶対パスへ正規化されます。
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
