# -*- coding:utf-8 -*-
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional

@dataclass(frozen=True)
class XattrSnapshot:
    """
    単一ファイルの xattr スナップショットを表す。
    - 現状は '参照専用' の想定（将来の復元機能のための土台）
    """
    path: str
    attrs: Dict[str, bytes]


def _platform_supports_xattr() -> bool:
    """
    Python の os モジュールが xattr を提供しているか（簡易）。
    """
    has_get: bool = hasattr(os, "getxattr")
    has_list: bool = hasattr(os, "listxattr")
    return bool(has_get and has_list)


def try_list_xattr(path: str) -> List[str]:
    """
    xattr 名の一覧を返す。非対応プラットフォームでは空配列。
    """
    if not _platform_supports_xattr():
        empty: List[str] = []
        return empty
    try:
        names: List[str] = list(os.listxattr(path))  # type: ignore[attr-defined]
        return names
    except Exception:
        empty2: List[str] = []
        return empty2


def try_get_xattr(path: str, name: str) -> Optional[bytes]:
    """
    xattr の値を取得（失敗時/非対応は None）。
    """
    if not _platform_supports_xattr():
        return None
    try:
        val: bytes = os.getxattr(path, name)  # type: ignore[attr-defined]
        return val
    except Exception:
        return None


def snapshot_xattr(path: str) -> XattrSnapshot:
    """
    参照用スナップショットを作る（現状は gather 側での確認・診断用途）。
    """
    attrs: Dict[str, bytes] = {}
    for name in try_list_xattr(path):
        val_opt: Optional[bytes] = try_get_xattr(path, name)
        if val_opt is not None:
            attrs[name] = val_opt
    snap: XattrSnapshot = XattrSnapshot(path=path, attrs=attrs)
    return snap
