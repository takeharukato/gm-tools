
from __future__ import annotations

from typing import Dict, Iterable, Any, List

def assert_rc(name: str, rc: int, expect_zero: bool = True) -> None:
    """Assert return code according to expectation.
    - If expect_zero is True (default), rc must be 0.
    - If expect_zero is False, rc must be non-zero.
    """
    if expect_zero and rc != 0:
        raise AssertionError(f"{name}: expected rc=0 but got {rc}")
    if not expect_zero and rc == 0:
        raise AssertionError(f"{name}: expected non-zero rc but got 0")

def _norm_mode(m: Any) -> str:
    """Normalize a mode value to 3-digit octal string (e.g., '640')."""
    if m is None:
        return ""
    try:
        s = str(m).strip()
        # If it's like '0640' or '640' or '0o640'
        if s.startswith("0o"):
            val = int(s, 8)
        elif s.startswith("0") and s != "0":
            val = int(s, 8)
        else:
            # if looks like decimal but is actually already octal text like '640'
            # try int as base 8 if it consists of 0-7 digits
            if all(ch in "01234567" for ch in s):
                val = int(s, 8)
            else:
                # could be 'drwxr-xr--' -> convert rough to octal bits  (owner/group/other)
                perms = s[-9:]
                bits = 0
                mapping = {'r':4,'w':2,'x':1,'-':0}
                for i, ch in enumerate(perms):
                    if ch in mapping:
                        # which triad?
                        tri = i // 3
                        shift = (2 - (i % 3))
                        bits += mapping[ch] << ( (2 - tri) * 3 + (2 - shift) )  # not exact, but try
                val = bits
        return f"{val:03o}"[-3:]
    except Exception:
        return str(m)

def _norm_owner(o: Any) -> str:
    if o is None:
        return ""
    s = str(o).strip()
    # Common forms: 'user:group', 'user group', 'uid:gid'
    s = s.replace(" ", ":")
    # collapse multiple ':'
    while "::" in s:
        s = s.replace("::", ":")
    return s

def compare_attr_maps(src: Dict[str, Any],
                      dst: Dict[str, Any],
                      keys: Iterable[str] = ("mode", "owner")) -> None:
    """Compare attribute maps of two paths.
    By default compares 'mode' and 'owner'. Extend `keys` to include 'acl', 'xattr', 'selinux' when desired.

    Raises AssertionError with a detailed diff on mismatch.
    """
    diffs: List[str] = []
    for k in keys:
        s = src.get(k, None)
        d = dst.get(k, None)
        if k == "mode":
            s_n = _norm_mode(s)
            d_n = _norm_mode(d)
            if s_n != d_n:
                diffs.append(f"mode: expected={s_n!r} got={d_n!r} (raw src={s!r}, dst={d!r})")
        elif k == "owner":
            s_n = _norm_owner(s)
            d_n = _norm_owner(d)
            if s_n != d_n:
                diffs.append(f"owner: expected={s_n!r} got={d_n!r} (raw src={s!r}, dst={d!r})")
        else:
            if s != d:
                diffs.append(f"{k}: expected={s!r} got={d!r}")
    if diffs:
        raise AssertionError("attribute mismatch: " + "; ".join(diffs))
