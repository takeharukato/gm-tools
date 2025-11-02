#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# cspell:ignore hostfile argparser

from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
import traceback
from typing import List, Optional, Set

# Paramiko 型注釈用
try:
    import paramiko  # type: ignore
except Exception as e:
    raise RuntimeError("Paramiko is required: pip install paramiko") from e

from .core_pull import (
    HostResult,
    download_one,
)
from .core_ssh import (
    SSHConfig,
    DEFAULT_SSH_PORT,
    DEFAULT_TIMEOUT,
    ssh_open,
    finalize_sockets
)
from .core_archive import (
    remote_pack_paths,
    download_and_extract_tar,
)
from .core_remote_fs import (
    sftp_exists,
    sftp_isdir,
    sftp_isfile,
    sftp_islink,
    remote_walk_files
)
from .core_path_handling import (
    normalize_src_abs,
    split_src_to_root_and_tail_regex,
    local_path_for_download,
)
from .core_common import parse_hosts_file


def build_parser() -> argparse.ArgumentParser:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description=(
            "gm-gather: download remote files via SFTP (or remote tar) to a local DEST.\n"
            "Usage: gm-gather [SRC ...] DEST\n"
            "  - SRC: absolute path starting with '/', 'X:/' (Windows), or '~/'.\n"
            "         The portion after the root is treated as a regex path.\n"
            "         e.g., '/etc/hosts' (literal), '/var/log/.*\\.log' (regex), '~/foo/bar\\.txt'.\n"
            "  - DEST: local directory where files are stored as DEST/<HOST>/abs/..."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    # 位置引数：SRC... DEST（最後が保存先ローカルディレクトリ）
    parser.add_argument("src", nargs="+", help="One or more SRC absolute path patterns (root '/', 'X:/', or '~/').")
    parser.add_argument("dest", help="Local destination directory.")

    # SSH
    parser.add_argument("-H", "--hosts", default="hostfile", help="Hosts file. Default: hostfile.")
    parser.add_argument("-u", "--user", default=getpass.getuser(), help="Target account semantics on remote (収集アカウント).")
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
    # 将来: --follow-symlinks を公開予定（APIはすでに対応）
    return parser


def _enumerate_candidates_for_host_by_src(
    sftp_client: paramiko.SFTPClient,
    resolved_srcs: List[str],
    verbose: bool,
    home_abs: str,
) -> List[str]:
    """
    新仕様：SRCそれぞれを (root, tail_re) に分け、root配下をwalkして tail_re に合致する
    通常ファイル（リンク除外は後段）候補を列挙。
    ルートは '/' または 'X:/'（'~/' は事前に home_abs で展開）。
    """
    candidates: Set[str] = set()
    for src in resolved_srcs:
        abs_norm = normalize_src_abs(src, home_abs_for_tilde=home_abs)
        is_abs = abs_norm.startswith("/") or re.match(r"^[A-Za-z]:/", abs_norm)
        if not is_abs:
            if verbose:
                print(f"[Warning] skip non-absolute SRC: {src}", file=sys.stderr)
            continue
        try:
            root, tail_re = split_src_to_root_and_tail_regex(abs_norm)
        except ValueError as e:
            print(f"[Warning] {src}: {e}", file=sys.stderr)
            continue
        if not sftp_exists(sftp_client, root) or not sftp_isdir(sftp_client, root):
            if verbose:
                print(f"[debug] skip missing/non-dir root: {root}", file=sys.stderr)
            continue

        # 空の tail は「配下すべて」を意味する
        pattern = tail_re if tail_re else r".*"
        try:
            rx = re.compile(pattern)
        except re.error as e:
            if verbose:
                print(f"[Warning] bad regex for {src}: {e}", file=sys.stderr)
            continue
        for ap in remote_walk_files(sftp_client, root):
            rel = ap[len(root):].lstrip("/")
            if rx.search(rel):
                candidates.add(ap)
    out = sorted(candidates)
    if verbose:
        print(f"[debug] candidates (remote): {len(out)}")
    return out


def _worker(
    host: str,
    dest_local: str,
    srcs: List[str],
    ssh_user: str,
    args_user: str,
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

        # '~' 展開用ホームディレクトリの決定（getent優先）
        home_abs = "/root" if args_user == "root" else f"/home/{args_user}"
        try:
            _stdin, stdout, _stderr = ssh.exec_command(f"getent passwd {args_user} | cut -d: -f6", timeout=timeout)
            v = stdout.read().decode().strip()
            if v.startswith("/"):
                home_abs = v
        except Exception:
            pass

        candidates: List[str] = _enumerate_candidates_for_host_by_src(
            sftp_client=sftp_client,
            resolved_srcs=srcs,
            verbose=verbose,
            home_abs=home_abs,
        )

        if dry_run:
            header: str = f"[{host}] DRY-RUN download: files={len(candidates)}"
            print(header)
            if verbose:
                for pth in candidates:
                    lp_dbg: str = local_path_for_download(dest_local, host, pth)
                    print(f"[plan] {host}:{pth} -> {lp_dbg}")
            return HostResult(host=host, downloaded=0, warnings=warnings, errors=errors)

        # 実ダウンロード（ファイルのみ、シンボリックリンクは除外）
        sftp_checked: paramiko.SFTPClient = sftp_client
        files_only: List[str] = []
        for remote_path in candidates:
            try:
                if not sftp_exists(sftp_checked, remote_path):
                    warnings.append(f"not found (skip): {remote_path}")
                    continue
                if sftp_islink(sftp_checked, remote_path):
                    # 逐次SFTPではリンクは無視（--pack時の follow_symlinks は将来実装）
                    continue
                if sftp_isdir(sftp_checked, remote_path):
                    continue
                if sftp_isfile(sftp_checked, remote_path):
                    files_only.append(remote_path)
            except Exception as e:
                errors.append(f"precheck failed: {remote_path}: {e}")

        # 権限モデル : ssh_user != user で --pack なしは不可
        use_sudo = (ssh_user != args_user)
        if use_sudo and not pack_remote:
            errors.append("ssh_user != user requires --pack. Please re-run with --pack.")
            # この条件では転送を実行せず、結果だけ返す
            print(f"[{host}] downloaded: {downloaded}")
            for w in warnings:
                print(f"[{host}] Warning: {w}", file=sys.stderr)
            for er in errors:
                print(f"[{host}] Error: {er}", file=sys.stderr)
            return HostResult(host=host, downloaded=downloaded, warnings=warnings, errors=errors)
        elif pack_remote and files_only:
            try:
                remote_gz: str = remote_pack_paths(
                    ssh, files_only, timeout=timeout, use_sudo=use_sudo, follow_symlinks=False
                )
                extracted: int = download_and_extract_tar(
                    sftp_checked, remote_gz, dest_local, os.path.join(host, "abs")
                )
                downloaded += extracted
                if verbose:
                    print(f"[pack] {host}:{remote_gz} -> extracted {extracted} file(s)")
                try:
                    ssh.exec_command(f"rm -f {remote_gz}", timeout=timeout)
                except Exception:
                    pass
            except Exception as e:
                errors.append(f"pack/download failed: {e}")
        else:
            for remote_path in files_only:

                try:
                    # ここは「ベースDIR」を渡すのが正解
                    download_one(sftp_checked, remote_path, dest_local, host)
                    downloaded += 1
                    if verbose:
                        local_path = local_path_for_download(dest_local, host, remote_path)
                        print(f"[get] {host}:{remote_path} -> {local_path}")
                except Exception as e:
                    errors.append(f"download failed: {remote_path}: {e}")

        print(f"[{host}] downloaded: {downloaded}")
        for w in warnings:
            print(f"[{host}] Warning: {w}", file=sys.stderr)
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

    return HostResult(host=host, downloaded=downloaded, warnings=warnings, errors=errors)


def main() -> None:
    parser: argparse.ArgumentParser = build_parser()
    args: argparse.Namespace = parser.parse_args()

    # 位置引数の検証
    if len(args.src) < 1 or not args.dest:
        print("At least one SRC and a DEST are required.", file=sys.stderr)
        sys.exit(5)

    dest_local: str = str(args.dest)
    srcs: List[str] = list(args.src)

    hosts: List[str] = parse_hosts_file(str(args.hosts))
    if len(hosts) == 0:
        print("No hosts found in hosts file.", file=sys.stderr)
        sys.exit(1)

    ssh_user: str = str(args.ssh_user) if args.ssh_user is not None else str(args.user)
    args_user: str = str(args.user)

    print(f"Hosts: {len(hosts)}  Dest: {dest_local}")
    print(f"SRCs : {len(srcs)}")
    print(f"SSH  : ssh-user={ssh_user} user={args_user} port={args.port} strict={bool(args.strict_host_key_checking)}")

    results: List[HostResult] = []
    for h in hosts:
        res: HostResult = _worker(
            host=h,
            dest_local=dest_local,
            srcs=srcs,
            ssh_user=ssh_user,
            args_user=args_user,
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

    total_downloaded: int = sum( r.downloaded for r in results )
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
