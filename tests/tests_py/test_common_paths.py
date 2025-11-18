# tests/tests_py/test_common_paths.py
# 共有パスユーティリティ
from __future__ import annotations

import os
# no typing imports required


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
