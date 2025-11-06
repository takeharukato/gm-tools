#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import argparse, os
from pathlib import Path
from typing import Final, Tuple

DEFAULT_LOCAL_TREE_ROOT: Final[str] = "tests/data/src_tree"
DEFAULT_FILES: Final[Tuple[str, ...]] = (
    "etc/sample.conf",
    "var/log/app/app.log",
    "var/log/app/app2.log",
    "opt/app/bin/run.sh",
)
DEFAULT_SYMLINKS: Final[Tuple[Tuple[str, str], ...]] = (
    ("opt/app/current", "../bin"),
)

def make_local_tree(root: str = DEFAULT_LOCAL_TREE_ROOT) -> None:
    base = Path(root)
    base.mkdir(parents=True, exist_ok=True)
    for rel in DEFAULT_FILES:
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# file: {rel}\n", encoding="utf-8")
    for link_path, target in DEFAULT_SYMLINKS:
        lp = base / link_path
        if lp.exists() or lp.is_symlink():
            try: lp.unlink()
            except Exception: pass
        lp.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, lp)

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--make-local-tree", action="store_true")
    a = ap.parse_args()
    if a.make_local_tree:
        make_local_tree()

if __name__ == "__main__":
    main()
