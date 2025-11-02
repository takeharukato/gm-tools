#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# cspell:ignore hostfile argparser srcs cands disp

"""
gather_cli.py
  - 既存仕様のまま（機能追加なし）
  - lstat().st_mode が None のSFTPで落ちる問題は core_pull 側で対処済み
  - Pylance/cSpell 警告解消（型ナロー + 用語抑止）
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
import traceback
from typing import List, Optional, Pattern, Set

# Paramiko 型注釈用
try:
    import paramiko  # type: ignore
except Exception as e:
    raise RuntimeError("Paramiko is required: pip install paramiko") from e

from .core_pull import (
    SSHConfig,
    HostResult,
    DEFAULT_SSH_PORT,
    DEFAULT_TIMEOUT,
    ssh_open,
    sftp_exists,
    sftp_isdir,
    sftp_isfile,
    remote_walk_files,
    local_path_for_download,
    download_one,
)

from .core_ssh import (
    DEFAULT_SSH_PORT,
    DEFAULT_TIMEOUT,
    finalize_sockets,
)
from .core_common import parse_hosts_file
from .core_archive import (
    download_and_extract_tar,
    remote_pack_paths,
)  # ローカルで安全展開

# =============================== Regex utils ================================

def compile_many(patterns: List[str], flags: int) -> List[Pattern[str]]:
    compiled: List[Pattern[str]] = [re.compile(p, flags) for p in patterns]
    return compiled


def match_any_abs(abs_path: str, abs_regexes: List[Pattern[str]]) -> bool:
    matched: bool = any(rx.search(abs_path) for rx in abs_regexes)
    return matched


def match_any_rel_under(abs_path: str, roots: List[str], rel_regexes: List[Pattern[str]]) -> bool:
    """
    リモート絶対パス abs_path が roots の下にある場合、
    root 基準の相対パスに対して rel パターンを適用して判定。
    """
    has_rel: bool = len(rel_regexes) > 0
    if not has_rel:
        return False
    root: str
    for root in roots:
        r: str = root.rstrip("/")
        if len(r) == 0:
            continue
        if abs_path == r or abs_path.startswith(r + "/"):
            rel: str = abs_path[len(r):].lstrip("/")
            if any(rx.search(rel) for rx in rel_regexes):
                return True
    return False


# ================================ CLI parser ================================

def build_parser() -> argparse.ArgumentParser:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Gather (download) remote files/dirs over SFTP into a local destination.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    # 位置引数：src... dest（最後が保存先ローカルディレクトリ）
    parser.add_argument("src", nargs="*", help="0 or more remote paths (absolute).")
    parser.add_argument("dest", nargs="?", help="Local destination directory (required).")

    # 選択（リモート）
    parser.add_argument("-a", "--pattern-abs", action="append", default=[], help="ABSOLUTE remote path regex (repeatable).")
    parser.add_argument("-r", "--pattern-rel", action="append", default=[], help="RELATIVE path regex to each --root (repeatable).")
    parser.add_argument("-R", "--root", action="append", default=[], help="Remote search root(s).")
    parser.add_argument("-i", "--ignore-case", action="store_true", help="Compile regexes with IGNORECASE.")

    # SSH
    parser.add_argument("-H", "--hosts", default="hostfile", help="Hosts file. Default: hostfile.")
    parser.add_argument("-u", "--user", default=getpass.getuser(), help="Target account semantics on remote (unused by gather).")
    parser.add_argument("-s", "--ssh-user", default=None, help="SSH login user. Default: same as --user.")
    parser.add_argument("-P", "--port", type=int, default=DEFAULT_SSH_PORT, help=f"SSH port. Default: {DEFAULT_SSH_PORT}.")
    parser.add_argument("-K", "--key", default=None, help="SSH private key file.")
    parser.add_argument("-W", "--password", default=None, help="SSH password (not recommended).")
    parser.add_argument("-T", "--timeout", type=float, default=DEFAULT_TIMEOUT, help=f"SSH/command timeout seconds. Default: {DEFAULT_TIMEOUT}.")
    parser.add_argument("-S", "--strict-host-key-checking", action="store_true", help="Enable strict host key checking.")

    # 実行
    parser.add_argument("-j", "--parallel", type=int, default=1, help="Parallel hosts (not parallel per-host). Default: 1.")
    parser.add_argument("-n", "--dry-run", action="store_true", help="Show plan only; do not download.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logs.")
    parser.add_argument("--pack", action="store_true", help="Pack on remote (tar.gz) and download once.")
    return parser

# =============================== Worker logic ===============================

def _enumerate_candidates_for_host(
    sftp_client: paramiko.SFTPClient,
    explicit_sources: List[str],
    roots: List[str],
    pat_abs: List[Pattern[str]],
    pat_rel: List[Pattern[str]],
    verbose: bool,
) -> List[str]:
    """
    取得候補（リモート絶対パス）を列挙。
    """
    candidates: Set[str] = set()

    # 明示 src
    src: str
    for src in explicit_sources:
        if len(src) == 0 or not src.startswith("/"):
            if verbose:
                warn_msg: str = f"[Warning] skip non-absolute src: {src}"
                print(warn_msg, file=sys.stderr)
            continue
        candidates.add(src)

    # ABS パターン探索の起点
    scan_roots: List[str] = []
    if len(roots) > 0:
        scan_roots.extend(roots)
    else:
        parents: Set[str] = {os.path.dirname(pp) for pp in explicit_sources if pp.startswith("/") and len(pp) > 1}
        scan_roots.extend(sorted(parents))

    # roots 未指定かつ explicit も空の場合は ABS パターン探索をしない
    if len(pat_abs) > 0 and len(scan_roots) > 0:
        rt: str
        for rt in scan_roots:
            if not rt.startswith("/"):
                continue
            if not sftp_exists(sftp_client, rt) or not sftp_isdir(sftp_client, rt):
                continue
            ap: str
            for ap in remote_walk_files(sftp_client, rt):
                if any(rx.search(ap) for rx in pat_abs):
                    candidates.add(ap)

    # REL パターン：明示 roots 配下のみ
    rt2: str
    for rt2 in roots:
        if len(rt2) == 0 or not rt2.startswith("/"):
            continue
        if not sftp_exists(sftp_client, rt2) or not sftp_isdir(sftp_client, rt2):
            continue
        ap2: str
        for ap2 in remote_walk_files(sftp_client, rt2):
            if any(rx.search(ap2) for rx in pat_abs):
                candidates.add(ap2)
            rel: str = ap2[len(rt2):].lstrip("/")
            if any(rx.search(rel) for rx in pat_rel):
                candidates.add(ap2)

    out: List[str] = sorted(candidates)
    if verbose:
        msg: str = f"[debug] candidates (remote): {len(out)}"
        print(msg)
    return out


def _worker(
    host: str,
    dest_local: str,
    explicit_sources: List[str],
    roots: List[str],
    pat_abs: List[Pattern[str]],
    pat_rel: List[Pattern[str]],
    ssh_user: str,
    port: int,
    key: Optional[str],
    password: Optional[str],
    timeout: float,
    strict: bool,
    dry_run: bool,
    pack_remote: bool,
    verbose: bool,
) -> HostResult:
    downloaded: int = 0
    warnings: List[str] = []
    errors: List[str] = []

    ssh: Optional[paramiko.SSHClient] = None
    sftp_client: Optional[paramiko.SFTPClient] = None

    try:
        cfg: SSHConfig = SSHConfig(
            host=host,
            port=port,
            ssh_user=ssh_user,
            key_filename=key,
            password=password,
            timeout=timeout,
            strict_host_key_checking=strict,
        )
        ssh = ssh_open(cfg, debug_print=verbose)
        sftp_client = ssh.open_sftp()

        candidates: List[str] = _enumerate_candidates_for_host(
            sftp_client=sftp_client,
            explicit_sources=explicit_sources,
            roots=roots,
            pat_abs=pat_abs,
            pat_rel=pat_rel,
            verbose=verbose,
        )

        if dry_run:
            header: str = f"[{host}] DRY-RUN download: files={len(candidates)}"
            print(header)
            pth: str
            for pth in candidates:
                if verbose:
                    lp_dbg: str = local_path_for_download(dest_local, host, pth)
                    print(f"[plan] {host}:{pth} -> {lp_dbg}")
            return HostResult(host=host, downloaded=0, warnings=warnings, errors=errors)

        # 実ダウンロード
        remote_path: str
        # sftp_client はここまでに必ずセット済み
        sftp_checked: paramiko.SFTPClient = sftp_client
         # まず存在＆通常ファイルのみ抽出
        files_only: List[str] = []
        for remote_path in candidates:
            try:
                if not sftp_exists(sftp_checked, remote_path):
                    warnings.append(f"not found (skip): {remote_path}")
                    continue
                if sftp_isdir(sftp_checked, remote_path):
                     # ディレクトリは walk で拾う方針のため直接は取らない
                     continue
                if sftp_isfile(sftp_checked, remote_path):
                     files_only.append(remote_path)
            except Exception as e:
                 errors.append(f"precheck failed: {remote_path}: {e}")
        if pack_remote and files_only:
            try:
                # 1) リモートで tar.gz 化
                remote_gz: str = remote_pack_paths(ssh, files_only, timeout=timeout)
                # 2) 1 本ダウンロード → ローカル安全展開（<dest>/<host>/abs/）
                extracted:int = download_and_extract_tar(
                    sftp_checked, remote_gz, dest_local, os.path.join(host, "abs")
                )
                downloaded += extracted
                if verbose:
                    print(f"[pack] {host}:{remote_gz} -> extracted {extracted} file(s)")
                # 3) リモートの一時ファイル掃除
                try:
                    ssh.exec_command(f"rm -f {remote_gz}", timeout=timeout)
                except Exception:
                    pass
            except Exception as e:
                errors.append(f"pack/download failed: {e}")
        else:
            # 従来の逐次 SFTP 取得（1 ファイルずつ保存）
            for remote_path in files_only:
                local_path: str = local_path_for_download(dest_local, host, remote_path)
                try:
                    download_one(sftp_checked, remote_path, local_path, host)
                    downloaded += 1
                    if verbose:
                        print(f"[get] {host}:{remote_path} -> {local_path}")
                except Exception as e:
                    errors.append(f"download failed: {remote_path}: {e}")
        print(f"[{host}] downloaded: {downloaded}")
        w: str
        for w in warnings:
            print(f"[{host}] Warning: {w}", file=sys.stderr)
        er: str
        for er in errors:
            print(f"[{host}] Error: {er}", file=sys.stderr)

    except Exception as e:
        if verbose:
            traceback.print_exc()
        errors.append(f"{type(e).__name__}: {e}")
    finally:
        try:
            if sftp_client is not None:
                sftp_client.close()
        except Exception:
            pass
        try:
            if ssh is not None:
                ssh.close()
        except Exception:
            pass

    result: HostResult = HostResult(host=host, downloaded=downloaded, warnings=warnings, errors=errors)
    return result


# ================================== Main ====================================

def main() -> None:
    parser: argparse.ArgumentParser = build_parser()
    args: argparse.Namespace = parser.parse_args()

    # dest 解決：最後の位置引数
    if args.dest is None:
        if len(args.src) == 0:
            print("dest is required as the last positional argument.", file=sys.stderr)
            sys.exit(5)
        args.dest = args.src[-1]
        args.src = args.src[:-1]

    dest_local: str = str(args.dest)
    explicit_sources: List[str] = list(args.src)

    if len(dest_local) == 0:
        print("dest is required.", file=sys.stderr)
        sys.exit(5)

    hosts: List[str] = parse_hosts_file(str(args.hosts))
    if len(hosts) == 0:
        print("No hosts found in hosts file.", file=sys.stderr)
        sys.exit(1)

    flags: int = re.IGNORECASE if bool(args.ignore_case) else 0
    try:
        pat_abs: List[Pattern[str]] = compile_many(list(args.pattern_abs), flags)
        pat_rel: List[Pattern[str]] = compile_many(list(args.pattern_rel), flags)
    except re.error as e:
        print(f"Invalid regex: {e}", file=sys.stderr)
        sys.exit(5)

    ssh_user: str = str(args.ssh_user) if args.ssh_user is not None else str(args.user)

    if len(args.pattern_abs) > 0 or len(args.pattern_rel) > 0 or len(args.root) > 0:
        roots_display: str = ", ".join([str(r) for r in args.root]) if len(args.root) > 0 else "(none)"
        print(f"Hosts: {len(hosts)}  Dest: {dest_local}")
        print(f"Select: patterns ({len(args.pattern_abs)} abs, {len(args.pattern_rel)} rel) roots={roots_display}")
    else:
        print(f"Hosts: {len(hosts)}  Dest: {dest_local}")
        print(f"Sources: {len(explicit_sources)} (explicit)")
    print(f"SSH  : ssh-user={ssh_user} user={args.user} port={args.port} strict={bool(args.strict_host_key_checking)}")

    results: List[HostResult] = []
    h: str
    for h in hosts:
        res: HostResult = _worker(
            host=h,
            dest_local=dest_local,
            explicit_sources=explicit_sources,
            roots=list(args.root) if args.root is not None else [],
            pat_abs=pat_abs,
            pat_rel=pat_rel,
            ssh_user=ssh_user,
            port=int(args.port),
            key=str(args.key) if args.key is not None else None,
            password=str(args.password) if args.password is not None else None,
            timeout=float(args.timeout),
            strict=bool(args.strict_host_key_checking),
            dry_run=bool(args.dry_run),
            pack_remote=bool(args.pack),
            verbose=bool(args.verbose),
        )
        results.append(res)

    total_downloaded: int = sum(r.downloaded for r in results if len(r.errors) == 0)
    warn_hosts: List[HostResult] = [r for r in results if len(r.warnings) > 0]
    err_hosts: List[HostResult] = [r for r in results if len(r.errors) > 0]

    print("\n=== Summary ===")
    print(f"Hosts processed: {len(results)}")
    print(f"Total downloaded: {total_downloaded}")
    if len(warn_hosts) > 0:
        warn_count: int = sum(len(r.warnings) for r in warn_hosts)
        print(f"Warnings: {warn_count} on {len(warn_hosts)} host(s)")
    if len(err_hosts) > 0:
        err_count: int = sum(len(r.errors) for r in err_hosts)
        print(f"Errors (continuing): {err_count} on {len(err_hosts)} host(s)")

    exit_code: int = 2 if len(err_hosts) > 0 else 0
    sys.exit(exit_code)


if __name__ == "__main__":
    finalize_sockets()
    main()
