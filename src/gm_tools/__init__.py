# -*- coding: utf-8 -*-
"""
gm_tools package
"""
# __init__.py  (差し替え推奨版)
from __future__ import annotations

import importlib
from typing import Any, Dict, TYPE_CHECKING

__all__ = [
    "gather_cli",
    "scatter_cli",
    "core_archive",
    "core_pull",
    "core_push",
]

__version__ = "0.1.0"

# 型チェッカー向けに参照可能化（実行時には import されない）
if TYPE_CHECKING:
    from . import gather_cli as gather_cli
    from . import scatter_cli as scatter_cli
    from . import core_archive as core_archive
    from . import core_pull as core_pull
    from . import core_push as core_push

# 遅延 import マップ
_SUBMODULES: Dict[str, str] = {name: f".{name}" for name in __all__}

def __getattr__(name: str) -> Any:
    mod = _SUBMODULES.get(name)
    if mod is not None:
        return importlib.import_module(mod, __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + __all__)
