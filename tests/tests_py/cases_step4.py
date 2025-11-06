from __future__ import annotations
from typing import List, Tuple
from .types import Config

def gather_src_variants(user_home_rel: str) -> List[Tuple[str, str]]:
    # (label, src)
    return [
        ("rel", user_home_rel),
        ("abs_slash", "/tmp/gm_step4_src"),
        ("abs_tilde", "~/gm_step4_src"),
        ("abs_win", "C:\\gm\\step4\\src"),
        ("tilde_user_err", "~root/forbidden"),  # should error
    ]

def scatter_dest_variants() -> List[Tuple[str, str]]:
    return [
        ("abs_slash", "/tmp/gm_step4_dest"),
        ("abs_tilde", "~/gm_step4_dest"),
        ("abs_win", "D:\\gm\\step4\\dest"),
        ("rel_err", "relative/dest"),      # should error
        ("tilde_user_err", "~root/dest"),  # should error
    ]
