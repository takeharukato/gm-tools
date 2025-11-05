# -*- coding: utf-8 -*-
"""
gm_tools.core_select
====================

Plan construction utilities to *stabilize* transfer totals (total = number of
planned items) for gather/scatter before execution.

Policy
------
- "total" means the count of *planned* filesystem objects (files + dirs)
  after applying filters. It must be computed once and stay stable for the run.
- This module only builds/holds plans. It does not perform I/O transfers and
  does not log; callers are responsible for logging and progress.
- Sequences are 1-based (seq starts from 1) for human-facing logs.

This module performs no side effects on import.
"""

from __future__ import annotations

import fnmatch
import shlex
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, List, Optional, Sequence, Tuple

from .core_ssh import DEFAULT_TIMEOUT, SSHClientLike, SFTPClientLike
from .core_cmd_flavor import run_remote_cmd_capture

# ---- Data model --------------------------------------------------------------

@dataclass(frozen=True)
class PlanEntry:
    """
    A single planned item to transfer.
    - path    : absolute local path (or canonical placeholder when remote-listed)
    - relpath : relative path name used inside archives or remote roots
    - is_dir  : True if the entry represents a directory
    """
    path: Path
    relpath: str
    is_dir: bool


@dataclass
class Plan:
    """
    Immutable-ish container of planned items.
    - entries : ordered list of PlanEntry (order is stable and deterministic)
    """
    entries: List[PlanEntry]

    def __len__(self) -> int:
        return len(self.entries)

    def iter_seq(self) -> Iterator[Tuple[int, PlanEntry]]:
        """Iterate entries with 1-based sequence numbers."""
        for i, e in enumerate(self.entries, start=1):
            yield i, e


# ---- Helpers ----------------------------------------------------------------

def _norm_paths(paths: Iterable[Path]) -> List[Path]:
    """Normalize to absolute resolved paths (no actual I/O apart from resolve)."""
    out: List[Path] = []
    for p0 in paths:
        p = Path(p0)
        try:
            out.append(p.resolve())
        except Exception:
            # Best-effort resolve; fall back to absolute
            out.append(p.absolute())
    return out


def _make_exclude(globs: Optional[Sequence[str]]) -> Optional[Callable[[str], bool]]:
    """Return a predicate that matches any of the given glob patterns against POSIX-style strings."""
    if not globs:
        return None
    patterns = [g for g in globs if g]
    if not patterns:
        return None

    def _pred(s: str) -> bool:
        for g in patterns:
            if fnmatch.fnmatch(s, g):
                return True
        return False

    return _pred


def _walk_including_dirs(root: Path, *, follow_symlinks: bool) -> Iterator[Tuple[Path, bool]]:
    """
    Walk under root yielding (path, is_dir) for the root itself and its content.
    - Includes both directories and files.
    - Respects follow_symlinks for os.walk.
    """
    # Yield the root itself
    yield (root, True) if root.is_dir() else (root, False)

    if root.is_dir():
        for dirpath, dirnames, filenames in os.walk(str(root), followlinks=bool(follow_symlinks)):
            d = Path(dirpath)
            # yield directories
            for dn in dirnames:
                yield (d / dn, True)
            # yield files
            for fn in filenames:
                yield (d / fn, False)


def _relpath_for(base: Optional[Path], p: Path) -> str:
    if base is None:
        return p.name
    try:
        return str(p.resolve().relative_to(base.resolve()))
    except Exception:
        # Fallback to name when not under base
        return p.name


# ---- Public API --------------------------------------------------------------

def build_plan_from_paths(
    sources: Iterable[Path],
    *,
    base_dir: Optional[Path] = None,
    exclude: Optional[Sequence[str]] = None,
    follow_symlinks: bool = True,
) -> Plan:
    """
    Build a deterministic plan from local paths.
    - sources       : list of file/dir paths (may mix files and directories)
    - base_dir      : if given, relpath is computed relative to it
    - exclude       : glob patterns tested against relpath (POSIX-style)
    - follow_symlinks: whether to follow symlinks during directory walk
    Returns a Plan whose length is the *stable total*.
    """
    srcs = _norm_paths(sources)
    pred = _make_exclude(exclude)

    entries: List[PlanEntry] = []
    for src in sorted(srcs, key=lambda p: str(p)):
        for path, is_dir in _walk_including_dirs(src, follow_symlinks=follow_symlinks):
            rel = _relpath_for(base_dir or src, path)
            if pred is not None and pred(rel):
                continue
            entries.append(PlanEntry(path=path, relpath=rel, is_dir=bool(is_dir)))

    # Stable order: path string then is_dir flag to be deterministic
    entries.sort(key=lambda e: (e.relpath, 0 if e.is_dir else 1))
    return Plan(entries=entries)


def build_plan_from_manifest(
    items: Sequence[Tuple[str, bool]],
    *,
    base_dir: Optional[Path] = None,
) -> Plan:
    """
    Build a plan from an explicit manifest (e.g., remote-side listing).
    - items : sequence of (relpath, is_dir)
    - base_dir: if given, absolute path will be base_dir / relpath; otherwise path is relpath as Path
    Note: This is used when the list of items is prepared elsewhere and we want to
    lock the total without re-scanning.
    """
    entries: List[PlanEntry] = []
    for rel, is_dir in items:
        p = Path(rel) if base_dir is None else (Path(base_dir) / rel)
        entries.append(PlanEntry(path=p, relpath=rel, is_dir=bool(is_dir)))
    # Keep manifest order, but normalize relpath for consistency
    return Plan(entries=entries)


def total_of(plan: Plan) -> int:
    """Return stable total (number of planned items)."""
    return len(plan)


def iter_with_seq(plan: Plan) -> Iterator[Tuple[int, PlanEntry]]:
    """Helper that yields (seq, PlanEntry) starting at 1."""
    return plan.iter_seq()

# -----------------------------------------------------------------------------
# Backward-compat (Step4 API): enumerate_candidates_for_host
# -----------------------------------------------------------------------------
# 既存 gather_cli.py が:
#   from .core_select import enumerate_candidates_for_host
# で参照するため、同名・同シグネチャを維持する。
#
# 役割:
#   - SRC の正規表現/リテラルを、root/tail に分解し、
#   - (A) pack_remote and use_sudo: sudo でサーバ側を python/os.walk 走査
#   - (B) それ以外: SFTP で root を走査して tail_re を適用
#   - ここでは「候補列挙」のみを担い、存在/型の最終確認は呼び出し側に委譲
#
import re as _re
from typing import Set as _Set
from .core_remote_fs import sftp_exists, sftp_isdir, sftp_isfile

_REGEX_META = _re.compile(r"[.^$*+?\[\]{}()|\\]")

def normalize_src_abs(src: str, *, home_abs_for_tilde: str) -> str:
    """'~/' を remote HOME に展開。その他はそのまま返す（フルパス前提）。"""
    if src.startswith("~/"):
        if home_abs_for_tilde.endswith("/"):
            return home_abs_for_tilde + src[2:]
        return home_abs_for_tilde + "/" + src[2:]
    return src

def split_src_to_root_and_tail_regex(abs_path: str) -> tuple[str, str]:
    """
    与えられた絶対パス（正規表現メタを含む可能性あり）を (root, tail_re) に分解する。
    - ルール:
      * メタ文字が無い場合: root=dirname(abs_path), tail_re='^basename$'（相対名への厳密一致）
      * メタ文字がある場合: 最初のメタ文字の直前の '/' までを root とし、以降（先頭'/'除去）を tail_re とする
    - いずれの場合も root はディレクトリを指すことを意図
    """
    if not abs_path or abs_path == "/":
        raise ValueError("invalid absolute path pattern")

    m = _REGEX_META.search(abs_path)
    if m is None:
        # pure literal
        root = os.path.dirname(abs_path) or "/"
        base = os.path.basename(abs_path)
        if not base:
            # '/etc/' のような末尾スラッシュはディレクトリ意図なので、全体一致ではなく '.*' にする
            return (abs_path.rstrip("/") or "/", r".*")
        # 相対名に対する厳密一致
        return (root, "^" + _re.escape(base) + "$")

    # regex case: メタの直前にある最後の '/' を探す
    slash_pos = abs_path.rfind("/", 0, m.start())
    if slash_pos < 0:
        # 先頭にメタ、あるいは '/' より前にメタが無い → root は '/' に倒す
        root = "/"
        tail = abs_path.lstrip("/")
    else:
        root = abs_path[:slash_pos] or "/"
        tail = abs_path[slash_pos + 1 :]
    if not tail:
        # root 直下全体
        return (root, r".*")
    return (root, tail)

def remote_walk_files(sftp_client: SFTPClientLike, root: str) -> Iterator[str]:
    """
    SFTP で root 配下を再帰走査し、通常ファイルの絶対パスを yield。
    ディレクトリ存在確認は呼び出し側で済ませている前提。
    """
    stack: List[str] = [root]
    while stack:
        d = stack.pop()
        try:
            # listdir で子候補を取得。型判定は sftp_isdir/sftp_isfile に任せる。
            names = sftp_client.listdir(d)
        except Exception:
            continue
        for name in names:
            ap = d + ("" if d.endswith("/") else "/") + name
            try:
                if sftp_isdir(sftp_client, ap):
                    stack.append(ap)
                elif sftp_isfile(sftp_client, ap):
                    yield ap
                else:
                    # symlink/デバイス等はここでは採用しない（呼び出し側で判断）
                    pass
            except Exception:
                # ベストエフォート（権限等で失敗することがある）
                continue

def _enumerate_via_sftp_walk(
    sftp_client: SFTPClientLike,
    resolved_srcs: List[str],
    home_abs: str,
    verbose: bool,
) -> List[str]:
    candidates: _Set[str] = set()
    for src in resolved_srcs:
        abs_norm = normalize_src_abs(src, home_abs_for_tilde=home_abs)
        # 絶対パスでなければスキップ（Step4 同等）
        is_abs = abs_norm.startswith("/") or _re.match(r"^[A-Za-z]:/", abs_norm)
        if not is_abs:
            if verbose:
                print(f"[Warning] skip non-absolute SRC: {src}")
            continue
        try:
            root, tail_re = split_src_to_root_and_tail_regex(abs_norm)
        except ValueError as e:
            if verbose:
                print(f"[Warning] {src}: {e}")
            continue

        if not sftp_exists(sftp_client, root) or not sftp_isdir(sftp_client, root):
            if verbose:
                print(f"[debug] skip missing/non-dir root: {root}")
            continue

        pattern = tail_re if tail_re else r".*"
        try:
            rx = _re.compile(pattern)
        except _re.error as e:
            if verbose:
                print(f"[Warning] bad regex for {src}: {e}")
            continue

        for ap in remote_walk_files(sftp_client, root):
            # root からの相対
            rel = ap[len(root) :].lstrip("/\\")
            if rx.search(rel):
                candidates.add(ap)

    out = sorted(candidates)
    if verbose:
        print(f"[debug] candidates (remote): {len(out)}")
    return out

def _enumerate_via_remote_walk_with_sudo(
    ssh: SSHClientLike,          # paramiko.SSHClient 想定（型固定しない）
    resolved_srcs: List[str],
    home_abs: str,
    verbose: bool,
) -> List[str]:
    acc: _Set[str] = set()

    py_script = r"""
import os, re
root = os.environ.get("GM_ROOT", "")
pat  = os.environ.get("GM_PAT", ".*")
rx = re.compile(pat)
for dp, _dirs, files in os.walk(root, followlinks=False):
    rel = os.path.relpath(dp, root)
    rel = "" if rel == "." else rel
    for fn in files:
        rp = fn if not rel else rel + "/" + fn
        if rx.search(rp):
            print(os.path.join(root, rp))
""".strip()

    for src in resolved_srcs:
        abs_norm = normalize_src_abs(src, home_abs_for_tilde=home_abs)
        is_abs = abs_norm.startswith("/") or _re.match(r"^[A-Za-z]:/", abs_norm)
        if not is_abs:
            if verbose:
                print(f"[Warning] skip non-absolute SRC: {src}")
            continue

        try:
            root, tail_re = split_src_to_root_and_tail_regex(abs_norm)
        except ValueError as e:
            if verbose:
                print(f"[Warning] {src}: {e}")
            continue

        # root ディレクトリの存在確認（sudo/非 sudo のどちらかで通ればOK）
        check = f"sudo -n test -d {shlex.quote(root)} || test -d {shlex.quote(root)}"
        rc, _out, _err = run_remote_cmd_capture(ssh, ["bash", "-lc", check], timeout=DEFAULT_TIMEOUT)
        if rc != 0:
            if verbose:
                print(f"[debug] skip missing/non-dir root: {root}")
            continue

        pat = tail_re if tail_re else r".*"

        # sudo -n で試行
        cmd_sudo = (
            "sudo -n env "
            f"GM_ROOT={shlex.quote(root)} GM_PAT={shlex.quote(pat)} "
            "python3 - <<'PY'\n" + py_script + "\nPY"
        )
        rc, out, err = run_remote_cmd_capture(ssh, ["bash", "-lc", cmd_sudo], timeout=DEFAULT_TIMEOUT)

        if rc != 0:
            # 非 sudo フォールバック
            cmd_nonsudo = (
                "env "
                f"GM_ROOT={shlex.quote(root)} GM_PAT={shlex.quote(pat)} "
                "python3 - <<'PY'\n" + py_script + "\nPY"
            )
            rc2, out2, err2 = run_remote_cmd_capture(ssh, ["bash", "-lc", cmd_nonsudo], timeout=DEFAULT_TIMEOUT)
            if rc2 != 0:
                if verbose:
                    reason = (err or err2 or "").strip()
                    print(f"[debug] remote walk failed at root={root}: {reason}")
                continue
            out = out2

        for line in (out or "").splitlines():
            p = line.strip()
            if p:
                acc.add(p)

    out = sorted(acc)
    if verbose:
        print(f"[debug] candidates (remote/sudo-walk): {len(out)}")
    return out

def enumerate_candidates_for_host(
    ssh: SSHClientLike,          # paramiko.SSHClient 想定（型固定しない）
    sftp_client: SFTPClientLike, # paramiko.SFTPClient（型固定しない）
    resolved_srcs: List[str],
    home_abs: str,
    *,
    use_sudo: bool,
    pack_remote: bool,
    verbose: bool,
) -> List[str]:
    """
    候補列挙の統合 API（Step4 互換）。
    - pack_remote and use_sudo のとき sudo リモート走査
    - それ以外は SFTP 走査
    """
    if pack_remote and use_sudo:
        return _enumerate_via_remote_walk_with_sudo(ssh, resolved_srcs, home_abs, verbose)
    return _enumerate_via_sftp_walk(sftp_client, resolved_srcs, home_abs, verbose)