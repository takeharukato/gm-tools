#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import shlex
from typing import List, Set, Tuple, Iterable

try:
    import paramiko  # type: ignore
except Exception as e:
    raise RuntimeError("Paramiko is required: pip install paramiko") from e

from .core_path_handling import normalize_src_abs, split_src_to_root_and_tail_regex
from .core_remote_fs import sftp_exists, sftp_isdir, remote_walk_files


def _run(ssh: "paramiko.SSHClient", cmd: str) -> Tuple[int, str, str]:
    """
    小さめのヘルパ：remote でコマンド実行して (rc, stdout, stderr) を返す
    """
    stdin, stdout, stderr = ssh.exec_command(cmd)
    out = stdout.read().decode(errors="ignore")
    err = stderr.read().decode(errors="ignore")
    rc = stdout.channel.recv_exit_status()
    try:
        stdin.close()
        stdout.close()
        stderr.close()
    except Exception:
        pass
    return rc, out, err


def _enumerate_via_sftp_walk(
    sftp_client: "paramiko.SFTPClient",
    resolved_srcs: List[str],
    home_abs: str,
    verbose: bool,
) -> List[str]:
    """
    SFTP 経由の候補列挙（通常経路）。
    """
    candidates: Set[str] = set()
    for src in resolved_srcs:
        abs_norm = normalize_src_abs(src, home_abs_for_tilde=home_abs)
        is_abs = abs_norm.startswith("/") or re.match(r"^[A-Za-z]:/", abs_norm)
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
            rx = re.compile(pattern)
        except re.error as e:
            if verbose:
                print(f"[Warning] bad regex for {src}: {e}")
            continue

        for ap in remote_walk_files(sftp_client, root):
            rel = ap[len(root):].lstrip("/\\")
            if rx.search(rel):
                candidates.add(ap)

    out = sorted(candidates)
    if verbose:
        print(f"[debug] candidates (remote): {len(out)}")
    return out


def _enumerate_via_remote_walk_with_sudo(
    ssh: "paramiko.SSHClient",
    resolved_srcs: List[str],
    home_abs: str,
    verbose: bool,
) -> List[str]:
    """
    sudo でサーバ側を走査して候補列挙（--pack かつ ssh_user != user 用の経路）。
    - 各 SRC を (root, tail_re) に分解し、root 配下を python3/os.walk で走査
    - tail_re は “root からの相対パス” に対して Python の正規表現として適用
    """
    acc: Set[str] = set()

    # 変数で root / pat を受け取る形にして、Heredoc の中身は不変にする
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
"""

    for src in resolved_srcs:
        abs_norm = normalize_src_abs(src, home_abs_for_tilde=home_abs)
        is_abs = abs_norm.startswith("/") or re.match(r"^[A-Za-z]:/", abs_norm)
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

        # root の存在確認（sudo 可/不可の両パターンで試す）
        rc, _, _ = _run(ssh, f"sudo -n test -d {shlex.quote(root)} || test -d {shlex.quote(root)}")
        if rc != 0:
            if verbose:
                print(f"[debug] skip missing/non-dir root: {root}")
            continue

        pat = tail_re if tail_re else r".*"

        # まず sudo -n でトライ
        cmd_sudo = (
            "sudo -n env "
            f"GM_ROOT={shlex.quote(root)} GM_PAT={shlex.quote(pat)} "
            "python3 - <<'PY'\n"
            f"{py_script}\n"
            "PY"
        )
        rc, out, err = _run(ssh, cmd_sudo)

        if rc != 0:
            # sudo できない環境のフォールバック：非 sudo 実行
            cmd_nonsudo = (
                "env "
                f"GM_ROOT={shlex.quote(root)} GM_PAT={shlex.quote(pat)} "
                "python3 - <<'PY'\n"
                f"{py_script}\n"
                "PY"
            )
            rc2, out2, err2 = _run(ssh, cmd_nonsudo)
            if rc2 != 0:
                if verbose:
                    reason = err.strip() or err2.strip()
                    print(f"[debug] remote walk failed at root={root}: {reason}")
                continue
            out = out2

        for line in out.splitlines():
            p = line.strip()
            if p:
                acc.add(p)

    out = sorted(acc)
    if verbose:
        print(f"[debug] candidates (remote/sudo-walk): {len(out)}")
    return out


def enumerate_candidates_for_host(
    ssh: "paramiko.SSHClient",
    sftp_client: "paramiko.SFTPClient",
    resolved_srcs: List[str],
    home_abs: str,
    *,
    use_sudo: bool,
    pack_remote: bool,
    verbose: bool,
) -> List[str]:
    """
    候補列挙の統合 API。
    - `pack_remote and use_sudo` のときは sudo リモート走査で列挙
    - それ以外は従来の SFTP 走査
    """
    if pack_remote and use_sudo:
        return _enumerate_via_remote_walk_with_sudo(ssh, resolved_srcs, home_abs, verbose)
    return _enumerate_via_sftp_walk(sftp_client, resolved_srcs, home_abs, verbose)


def enumerate_candidates_local(paths: Iterable[str]) -> Iterable[str]:
    """
    Yield absolute local paths to process.
    - Deduplicate, preserve input order
    - Expand simple globs
    """
    seen: set[str] = set()
    import os, glob
    for p in paths:
        matches: list[str] = glob.glob(p)
        if not matches:
            matches = [p]
        for m in matches:
            ap: str = os.path.abspath(m)
            if ap not in seen:
                seen.add(ap)
                yield ap
