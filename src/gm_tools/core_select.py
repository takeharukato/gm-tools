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
import re
import logging

from dataclasses import dataclass
from pathlib import Path

from typing import Callable, Iterable, Iterator, List, Optional, Sequence, Tuple, Set

from .core_remote_fs import sftp_exists, sftp_isdir, sftp_isfile, sftp_islink
from .core_ssh import DEFAULT_TIMEOUT, SSHClientLike, SFTPClientLike
from .core_cmd_flavor import run_remote_cmd_capture
from .core_path_handling import (
    split_src_to_root_and_tail_regex,
    is_abs_path,
    normalize_src_abs,
    looks_like_regex,
)

# ---- Logging setup -----------------------------------------------------------

_LOG = logging.getLogger(__name__)

# ---- Data model --------------------------------------------------------------

@dataclass
class PlanEntry:
    """
    A single planned item to transfer.
    - path        : absolute local path (or canonical placeholder when remote-listed)
    - relpath     : local-side relative path (under dest_root), also used as archive inner path
    - is_dir      : True if the entry represents a directory
    - remote_root : POSIX remote root to join with relpath when constructing remote path.
                    Examples: "/", "<home_abs>", "C:/". Empty string keeps legacy behavior.
    """
    path: Path
    relpath: str
    is_dir: bool
    remote_root: str = ""
    remote_abs: str = ""
    remote_rel: str = ""

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
# enumerate_candidates_for_host
# -----------------------------------------------------------------------------
# 役割:
#   - SRC の正規表現/リテラルを, root/tail に分解し,
#   - (A) pack_remote and use_sudo: sudo でサーバ側を python/os.walk 走査
#   - (B) それ以外: SFTP で root を走査して tail_re を適用
#   - ここでは「候補列挙」のみを担い, 存在/型の最終確認は呼び出し側に委譲
#
def remote_walk_files(sftp_client: SFTPClientLike, root: str, *, include_symlinks: bool = False) -> Iterator[str]:
    """
    SFTP で root 配下を再帰走査し, 通常ファイル,
    include_symlinks が真の場合は, シンボリックリンクの絶対パスを yield。
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
                elif include_symlinks and sftp_islink(sftp_client, ap):
                    yield ap
                elif sftp_isfile(sftp_client, ap):
                    yield ap
                else:
                    # symlink/デバイス等はここでは採用しない ( 呼び出し側で判断 )
                    pass
            except Exception:
                # ベストエフォート ( 権限等で失敗することがある )
                continue

def _enumerate_via_sftp_walk(
    sftp_client: SFTPClientLike,
    resolved_srcs: List[str],
    home_abs: str,
    verbose: bool,
    *,
    include_symlinks: bool,
) -> List[str]:
    candidates: Set[str] = set()

    for src in resolved_srcs:
        try:
            abs_norm = normalize_src_abs(src, home_abs_for_tilde=home_abs)
        except ValueError as e:
            _LOG.warning("reject SRC outside HOME (relative not confined): %s (%s)", src, e)
            continue
        # ここまでで相対はHOME基準に絶対化済
        is_abs = is_abs_path(abs_norm)
        if not is_abs:
            _LOG.warning("skip non-absolute SRC after normalization (unexpected): %s", src)
            continue

        # ------------------------------------------------------------------
        # Directory-SRC (リテラルディレクトリ指定) の先行判定
        # 条件:
        #   - looks_like_regex(src) == False  (正規表現扱いではない)
        #   - abs_norm がディレクトリ (remote 側で sftp_isdir)
        # 動作:
        #   abs_norm 配下の通常ファイルを列挙し candidates に追加。
        #   ディレクトリ自身は candidates に含めない。
        # ------------------------------------------------------------------
        if not looks_like_regex(src):
            try:
                if sftp_isdir(sftp_client, abs_norm) and not sftp_isfile(sftp_client, abs_norm):
                    _LOG.debug(
                        "Directory-SRC literal detected via abs path: src=%s abs_norm=%s",
                        src,
                        abs_norm,
                    )
                    for ap in remote_walk_files(sftp_client, abs_norm, include_symlinks=False):
                        if sftp_isfile(sftp_client, ap):
                            candidates.add(ap)
                    # この SRC については通常の正規表現ロジックには進まない
                    continue
            except Exception as e:
                _LOG.warning("directory-SRC detection failed for %s (%s)", src, e)


        try:
            root, tail_re = split_src_to_root_and_tail_regex(abs_norm)
        except ValueError as e:
            _LOG.warning("bad SRC pattern %s: %s", src, e)
            continue

        if not sftp_exists(sftp_client, root) or not sftp_isdir(sftp_client, root):
            _LOG.debug("skip missing/non-dir root: %s", root)
            continue

        pattern = tail_re if tail_re else r".*"
        try:
            rx = re.compile(pattern)
        except re.error as e:
            _LOG.warning("bad regex for %s: %s", src, e)
            continue

        for ap in remote_walk_files(sftp_client, root, include_symlinks=include_symlinks):
            # root からの相対
            rel = ap[len(root) :].lstrip("/\\")
            if rx.search(rel):
                candidates.add(ap)

    out = sorted(candidates)
    _LOG.debug("candidates (remote): %d", len(out))
    return out

def _enumerate_via_remote_walk_with_sudo(
    ssh: SSHClientLike,          # paramiko.SSHClient 想定 ( 型固定しない )
    resolved_srcs: List[str],
    home_abs: str,
    verbose: bool,
    *,
    include_symlinks: bool,
) -> List[str]:
    acc: Set[str] = set()

    py_script = r"""
import os, re, sys
root = os.environ.get("GM_ROOT", "")
pat  = os.environ.get("GM_PAT", ".*")
want_links = os.environ.get("GM_INC_LINKS", "0") == "1"
rx = re.compile(pat)
for dp, _dirs, files in os.walk(root, followlinks=False):
    rel = os.path.relpath(dp, root)
    rel = "" if rel == "." else rel
    for fn in files:
        rp = fn if not rel else rel + "/" + fn
        if rx.search(rp):
            sys.stdout.write(os.path.join(root, rp) + "\n")
            sys.stdout.flush()
        if want_links:
            ap = os.path.join(dp, fn)
            if os.path.islink(ap):
                rp = fn if not rel else rel + "/" + fn
                if rx.search(rp):
                    sys.stdout.write(os.path.join(root, rp) + "\n")
                    sys.stdout.flush()
""".strip()

    for src in resolved_srcs:
        try:
            abs_norm = normalize_src_abs(src, home_abs_for_tilde=home_abs)
        except ValueError as e:
            _LOG.warning("reject SRC outside HOME (relative not confined): %s (%s)", src, e)
            continue
        is_abs = is_abs_path(abs_norm)
        if not is_abs:
            _LOG.warning("skip non-absolute SRC after normalization (unexpected): %s", src)
            continue

        # ------------------------------------------------------------------
        # Directory-SRC (リテラルディレクトリ指定) の先行判定 (sudo 経路)
        # 条件:
        #   - looks_like_regex(src) == False
        #   - abs_norm がディレクトリ (sudo / 非 sudo いずれかの test -d が成功)
        # 動作:
        #   abs_norm 配下の通常ファイルを python3/os.walk で列挙し acc に追加。
        # ------------------------------------------------------------------
        if not looks_like_regex(src):
            check_dir = (
                f"sudo -n test -d {shlex.quote(abs_norm)} "
                f"|| test -d {shlex.quote(abs_norm)}"
            )
            rc_dir, _out_dir, _err_dir = run_remote_cmd_capture(
                ssh, ["bash", "-lc", check_dir], timeout=DEFAULT_TIMEOUT
            )
            if rc_dir == 0:
                _LOG.debug(
                    "Directory-SRC literal detected (sudo-walk) via abs path: src=%s abs_norm=%s",
                    src,
                    abs_norm,
                )

                py_script_dir = r"""
import os, sys
root = os.environ.get("GM_ROOT", "")
for dp, _dirs, files in os.walk(root, followlinks=False):
    for fn in files:
        ap = os.path.join(dp, fn)
        sys.stdout.write(ap + "\n")
        sys.stdout.flush()
""".strip()

                # sudo または非 sudo のいずれかで歩ければ採用
                cmd_sudo = (
                    "sudo -n env GM_ROOT=" + shlex.quote(abs_norm) +
                    " python3 - <<'PY'\n" + py_script_dir + "\nPY"
                )
                rc_walk, out, _err_walk = run_remote_cmd_capture(
                    ssh, ["bash", "-lc", cmd_sudo], timeout=DEFAULT_TIMEOUT
                )
                if rc_walk != 0:
                    cmd_nonsudo = (
                        "env GM_ROOT=" + shlex.quote(abs_norm) +
                        " python3 - <<'PY'\n" + py_script_dir + "\nPY"
                    )
                    rc2, out2, _err2 = run_remote_cmd_capture(
                        ssh, ["bash", "-lc", cmd_nonsudo], timeout=DEFAULT_TIMEOUT
                    )
                    if rc2 != 0:
                        _LOG.warning("directory-SRC sudo-walk failed at root=%s", abs_norm)
                        continue
                    out = out2

                for line in (out or "").splitlines():
                    p = line.strip()
                    if p:
                        acc.add(p)
                # この SRC については通常の正規表現ロジックには進まない
                continue
        # ------------------------------------------------------------------
        # ここからは「正規表現 SRC」または「非ディレクトリのリテラル SRC」
        # として、従来通り root/tail_re を使った列挙を行う。
        # ------------------------------------------------------------------
        try:
            root, tail_re = split_src_to_root_and_tail_regex(abs_norm)
        except ValueError as e:
            _LOG.warning("bad SRC pattern %s: %s", src, e)
            continue

        # root ディレクトリの存在確認 ( sudo/非 sudo のどちらかで通ればOK )
        check_root = f"sudo -n test -d {shlex.quote(root)} || test -d {shlex.quote(root)}"
        rc, _out, _err = run_remote_cmd_capture(ssh, ["bash", "-lc", check_root], timeout=DEFAULT_TIMEOUT)
        if rc != 0:
            _LOG.debug("skip missing/non-dir root: %s", root)
            continue

        pat = tail_re if tail_re else r".*"

        # sudo -n で試行
        cmd_sudo = (
            "sudo -n env "
            f"GM_ROOT={shlex.quote(root)} GM_PAT={shlex.quote(pat)} GM_INC_LINKS={'1' if include_symlinks else '0'} "
            "python3 - <<'PY'\n" + py_script + "\nPY"
        )
        rc, out, err = run_remote_cmd_capture(ssh, ["bash", "-lc", cmd_sudo], timeout=DEFAULT_TIMEOUT)

        if rc != 0:
            # 非 sudo フォールバック
            cmd_nonsudo = (
                "env "
                f"GM_ROOT={shlex.quote(root)} GM_PAT={shlex.quote(pat)} GM_INC_LINKS={'1' if include_symlinks else '0'} "
                "python3 - <<'PY'\n" + py_script + "\nPY"
            )
            rc2, out2, err2 = run_remote_cmd_capture(ssh, ["bash", "-lc", cmd_nonsudo], timeout=DEFAULT_TIMEOUT)
            if rc2 != 0:
                reason = (err or err2 or "").strip()
                _LOG.debug("remote walk failed at root=%s: %s", root, reason)
                continue
            out = out2

        for line in (out or "").splitlines():
            p = line.strip()
            if p:
                acc.add(p)

    out = sorted(acc)
    _LOG.debug("candidates (remote/sudo-walk): %d", len(out))
    return out

def enumerate_candidates_for_host(
    ssh: SSHClientLike,          # paramiko.SSHClient 想定 ( 型固定しない )
    sftp_client: SFTPClientLike, # paramiko.SFTPClient ( 型固定しない )
    resolved_srcs: List[str],
    home_abs: str,
    *,
    use_sudo: bool,
    pack_remote: bool,
    follow_symlinks: bool,
    verbose: bool,
) -> List[str]:
    """
    候補列挙の統合 API
    - pack_remote and use_sudo のとき sudo リモート走査
    - それ以外は SFTP 走査
    follow_symlinksの扱い:
     - ディレクトリシンボリックリンクは辿らない (扱わない)
     - follow_symlinksが偽, かつ, pack_remote偽ならリンクを候補に含めない
     - follow_symlinksが偽, かつ, pack_remote真ならリンク自体を候補に含める
     - follow_symlinksが真, かつ, pack_remote真ならリンク先の実体を候補に含める
    """
    include_symlinks = bool(pack_remote)
    if pack_remote and use_sudo:
        return _enumerate_via_remote_walk_with_sudo(
            ssh, resolved_srcs, home_abs, verbose, include_symlinks=include_symlinks
        )
    return _enumerate_via_sftp_walk(
        sftp_client, resolved_srcs, home_abs, verbose, include_symlinks=include_symlinks
    )

def enumerate_candidates_local(paths: Iterable[str]) -> Iterator[str]:
    """
    Yield absolute local paths to process using **regex semantics** (glob 非対応).
    仕様:
      - SRC に正規表現メタが含まれる場合:
          split_src_to_root_and_tail_regex で (root, tail_re) に分解し、
          root 配下を走査して「root 相対パス」に re.search(tail_re) を適用。
          **ファイルのみ**列挙（ディレクトリは明示指定時に後段で展開）。
      - 正規表現メタが含まれない場合:
          単一の厳密パスとして扱い、存在すれば（ファイル/ディレクトリいずれも）そのまま列挙。
      - 相対パスは CWD 基準で絶対化。
    """
    seen: Set[str] = set()
    cwd: str = os.getcwd()

    # "~" を含むかどうかの判定ヘルパ（展開のタイミングを誤らないため分離）
    def _looks_tilde(s: str) -> bool:
        s_in: str = s
        return s_in.startswith("~" + os.sep) or s_in == "~" or s_in.startswith("~/")

    for p in paths:
        token: str = p
        # 1) 正規表現メタの検出は**文字列を壊さず**そのまま判定する
        #    - ここでは expanduser もしない（"~" を誤解しない）
        has_meta: bool = looks_like_regex(token)

        # 2) 実際にファイル探索に使うための絶対パス化
        #    - "~" はこのタイミングでのみ展開する
        token_expanded: str = os.path.expanduser(token) if _looks_tilde(token) else token
        is_abs: bool = is_abs_path(token_expanded)

        abs_raw: str = token_expanded if is_abs else os.path.abspath(os.path.join(cwd, token_expanded))
        # tail の正規表現を壊さないため、ここでは '\\'→'/' の全体置換は行わない
        abs_norm: str = abs_raw

        if not has_meta:
            ap_exact: str = abs_norm
            if os.path.exists(ap_exact):
                ap_real: str = os.path.abspath(ap_exact)
                if ap_real not in seen:
                    seen.add(ap_real)
                    yield ap_real
            # リテラル指定で存在しなければ何も列挙しない（静かに無視）
            continue

        # --- 正規表現モード ---
        try:
            root: str
            tail_re: str
            # split_src_to_root_and_tail_regex 側で head（パス）だけを正規化し、
            # tail（正規表現）は無改変で保持する
            root, tail_re = split_src_to_root_and_tail_regex(abs_norm)
        except ValueError as _ex:
            _ex_msg: str = str(_ex)
            # 不正トークンは無視（gather と整合）
            continue

        if not os.path.isdir(root):
            # 走査起点がディレクトリでなければ列挙不可
            continue

        pattern_text: str = tail_re if tail_re else r".*"
        try:
            rx: re.Pattern[str] = re.compile(pattern_text)
        except re.error as _re:
            _re_msg: str = str(_re)
            # 不正な正規表現は無視
            continue

        walk_root: str = root
        dirpath: str
        _dirnames: List[str]
        filenames: List[str]
        for dirpath, _dirnames, filenames in os.walk(walk_root, followlinks=False):
            name: str
            for name in filenames:
                ap: str = os.path.join(dirpath, name)
                rel: str = ap[len(walk_root):].lstrip("/\\")
                m: Optional[re.Match[str]] = rx.search(rel)
                if m is not None:
                    ap_abs: str = os.path.abspath(ap)
                    if ap_abs not in seen:
                        seen.add(ap_abs)
                        yield ap_abs

        # ルート自身に対するマッチ（空相対にマッチ）を考慮し、ディレクトリ根を含める
        try:
            root_rel_self: str = ""
            m_root: Optional[re.Match[str]] = rx.search(root_rel_self)
            if m_root is not None:
                root_abs: str = os.path.abspath(root)
                if root_abs not in seen:
                    seen.add(root_abs)
                    yield root_abs
        except Exception as _e:
            _e_msg: str = str(_e)
            # 失敗時は黙って無視（列挙結果に影響なし）
            pass
