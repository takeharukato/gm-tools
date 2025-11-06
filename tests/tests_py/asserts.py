from __future__ import annotations
from typing import Dict

def assert_rc(name: str, rc: int, expect_zero: bool=True) -> None:
    if expect_zero and rc != 0:
        raise AssertionError(f"{name}: expected rc=0 but got {rc}")
    if not expect_zero and rc == 0:
        raise AssertionError(f"{name}: expected non-zero rc but got 0")

def assert_eq(label: str, a: str, b: str) -> None:
    if a != b:
        raise AssertionError(f"{label} mismatch: {a!r} != {b!r}")

def assert_contains(label: str, text: str, needle: str) -> None:
    if needle not in text:
        raise AssertionError(f"{label} does not contain {needle!r}")

def compare_attr_maps(src: Dict[str,str], dst: Dict[str,str], keys = ("owner","mode")) -> None:
    for k in keys:
        if src.get(k,"") != dst.get(k,""):
            raise AssertionError(f"Attribute {k} mismatch: {src.get(k)} != {dst.get(k)}")
