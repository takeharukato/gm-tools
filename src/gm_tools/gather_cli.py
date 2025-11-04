#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# cspell:ignore hostfile argparser

from __future__ import annotations

import os
import sys
import shlex
import argparse
import getpass
import traceback
from argparse import BooleanOptionalAction
from typing import List, Optional

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
    finalize_sockets,
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
)
from .core_path_handling import (
    local_path_for_download,
    is_local_abs,
    tilde_username,
)

from .core_common import parse_hosts_file
from .core_select import enumerate_candidates_for_host
from .core_cmd_flavor import run_remote_cmd_capture
from .core_remote_path import detect_remote_home

# === Defaults / Exit codes (constantized) ===
DEFAULT_HOSTS_FILE: str = "hostfile"
DEFAULT_PARALLEL_HOSTS: int = 1
EXIT_OK:            int = 0
EXIT_ERR_NO_HOSTS:  int = 1
EXIT_ERR_GENERIC:   int = 2
EXIT_ERR_TILDE_USER:int = 3
EXIT_ERR_ARGS:      int = 5

def build_parser() -> argparse.ArgumentParser:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description=(
            "gm-gather: download remote files via SFTP (or remote tar) to a local DEST.\n"
            "Usage: gm-gather [SRC ...] DEST\n"
            "  - SRC: absolute path starting with '/', 'X:/' (Windows), or '~/'.\n"
            "         The portion after the root is treated as a regex path.\n"
            "         e.g., '/etc/hosts' (literal), '/var/log/.*\\.log' (regex), '~/foo/bar\\.txt'.\n"
            "  - DEST: local directory where files are stored as DEST/<HOST>/..."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )

    # 位置引数 : SRC... DEST ( 最後が保存先ローカルディレクトリ )
    parser.add_argument("src", nargs="+", help="One or more SRC absolute path patterns (root '/', 'X:/', or '~/').")
    parser.add_argument("dest", help="Local destination directory.")

    # SSH
    parser.add_argument("-H", "--hosts", default=DEFAULT_HOSTS_FILE, help=f"Hosts file. Default: {DEFAULT_HOSTS_FILE}.")
    parser.add_argument("-u", "--user", default=getpass.getuser(), help="Target account semantics on remote (収集アカウント).")
    parser.add_argument("-s", "--ssh-user", default=None, help="SSH login user. Default: same as --user.")
    parser.add_argument("-P", "--port", type=int, default=DEFAULT_SSH_PORT, help=f"SSH port. Default: {DEFAULT_SSH_PORT}.")
    parser.add_argument("-K", "--key", default=None, help="SSH private key file.")
    parser.add_argument("-W", "--password", default=None, help="SSH password (not recommended).")
    parser.add_argument("-T", "--timeout", type=float, default=DEFAULT_TIMEOUT, help=f"SSH/command timeout seconds. Default: {DEFAULT_TIMEOUT}.")
    parser.add_argument("-S", "--strict-host-key-checking", action="store_true", help="Enable strict host key checking.")

    # 実行
    parser.add_argument("-j", "--parallel", type=int, default=DEFAULT_PARALLEL_HOSTS, help=f"Parallel hosts (not parallel per-host). Default: {DEFAULT_PARALLEL_HOSTS}.")
    parser.add_argument("-n", "--dry-run", action="store_true", help="Show plan only; do not download.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logs.")
    parser.add_argument("--pack", action="store_true", help="Pack on remote (tar.gz) and download once.")
    # pack 時のリンク追随 ( 指定時にのみ dereference ) 。デフォルトは追随しない＝リンクは含めない。
    parser.add_argument("--follow-symlinks", action="store_true", help="When used with --pack, dereference symlinks on remote.")
    # sudo-collect: 三値 ( True/False/None=auto ) 。pack 経路でのみ有効。
    parser.add_argument(
        "-x", "--sudo-collect",
        action=BooleanOptionalAction,
        default=None,
        help="Use sudo for remote packing/collection (pack path only). Omitted = auto (enabled when ssh-user != --user).")

    return parser


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
    follow_symlinks: bool,
    sudo_collect_flag: Optional[bool],
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

        # リモート HOME 検出
        home_abs: str = detect_remote_home(ssh, args_user, timeout=timeout)
        # sudo 利用可否の三値判定 ( 未指定 None は自動 : ssh_user != user )
        sudo_collect: Optional[bool] = sudo_collect_flag
        use_sudo: bool = (ssh_user != args_user) if (sudo_collect is None) else bool(sudo_collect)

        # 権限モデル : sudo 有効なら pack 経路が必須 ( SFTP 経路では権限昇格不可 )
        if use_sudo and not pack_remote:
            errors.append(
                "sudo-collect requires --pack (SFTP path cannot elevate privileges). "
                "Please re-run with --pack or disable --sudo-collect."
            )
            print(f"[{host}] downloaded: {downloaded}")
            for w in warnings:
                print(f"[{host}] Warning: {w}", file=sys.stderr)
            for er in errors:
                print(f"[{host}] Error: {er}", file=sys.stderr)
            return HostResult(host=host, downloaded=downloaded, warnings=warnings, errors=errors)

        # 候補列挙 ( core_select に委譲 )
        # ここでの選択肢は、「存在する可能性のある候補」で、この後に
        # 型別 ( 通常ファイル/リンク/ディレクトリ ) で仕分ける
        candidates: List[str] = enumerate_candidates_for_host(
            ssh=ssh,
            sftp_client=sftp_client,
            resolved_srcs=srcs,
            home_abs=home_abs,
            use_sudo=use_sudo,
            pack_remote=pack_remote,
            verbose=verbose,
        )

        if dry_run:
            header: str = f"[{host}] DRY-RUN download: files={len(candidates)}"
            print(header)
            if verbose:
                for pth in candidates:
                    lp_dbg: str = local_path_for_download(dest_local, host, pth)
                    print(f"[planned] {host}:{pth} -> {lp_dbg}")
            return HostResult(host=host, downloaded=0, warnings=warnings, errors=errors)

        # まず候補を型別に仕分け ( 存在チェックもここで実施 )
        files_only: List[str] = []
        symlinks:   List[str] = []
        for remote_path in candidates:
            try:
                if not sftp_exists(sftp_client, remote_path):
                    warnings.append(f"not found (skip): {remote_path}")
                    continue
                if sftp_isdir(sftp_client, remote_path):
                    # ディレクトリはここでは扱わない ( `--pack`でも -T に列挙しない )
                    continue
                if sftp_islink(sftp_client, remote_path):
                    symlinks.append(remote_path)
                    continue
                if sftp_isfile(sftp_client, remote_path):
                    files_only.append(remote_path)
            except Exception as e:
                errors.append(f"precheck failed: {remote_path}: {e}")

        # 実ダウンロード
        if pack_remote:
            # --pack 経路 :
            #   - デフォルト: symlink は含めない ( 安全第一 )
            #   - --follow-symlinks 指定時のみ symlink もリストに入れ、tar -h で実体参照
            pack_list: List[str] = list(files_only)
            if follow_symlinks and symlinks:
                pack_list.extend(symlinks)

            try:
                if pack_list:
                    remote_gz: str = remote_pack_paths(
                        ssh,
                        pack_list,
                        timeout=timeout,
                        use_sudo=use_sudo,
                        follow_symlinks=follow_symlinks,
                    )
                    extracted, _ = download_and_extract_tar(
                        sftp_client, remote_gz, dest_local, host
                    )
                    downloaded += extracted
                    if verbose:
                        print(f"[pack] {host}:{remote_gz} -> extracted {extracted} file(s)")

                    # 一時アーカイブ削除は PATH 注入・エラー抑止で統一
                    _ = run_remote_cmd_capture(
                        ssh, ["bash", "-lc", f"rm -f {shlex.quote(remote_gz)} || true"], timeout=timeout
                    )

            except Exception as e:
                errors.append(f"pack/download failed: {e}")
        else:
            # 逐次 SFTP : リンクは無視、通常ファイルのみ
            for remote_path in files_only:
                try:
                    # local_path は download_one 内で正規化 ( DEST/<HOST>/... )
                    download_one(sftp_client, remote_path, dest_local, host)
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
        sys.exit(EXIT_ERR_ARGS)


    # DEST: '~user' は非対応なので明示エラー
    _dest_raw: str = str(args.dest)
    _dest_tilde_user = tilde_username(_dest_raw)
    if _dest_tilde_user is not None:
        print(f"Error: tilde with username is not supported in DEST: ~{_dest_tilde_user}", file=sys.stderr)
        sys.exit(EXIT_ERR_TILDE_USER)

    # DEST: '~' をローカル実行ユーザの HOME で展開。相対ならカレント起点で絶対化。
    dest_local: str = os.path.expanduser(_dest_raw)
    if not is_local_abs(dest_local):
        dest_local = os.path.abspath(dest_local)
    srcs: List[str] = list(args.src)

    # SRC に '~user' が含まれていればエラー ( 共通仕様 )
    for s in srcs:
        u = tilde_username(s)
        if u is not None:
            print(f"Error: tilde with username is not supported in SRC: ~{u}", file=sys.stderr)
            sys.exit(EXIT_ERR_TILDE_USER)

    hosts: List[str] = parse_hosts_file(str(args.hosts))
    if len(hosts) == 0:
        print("No hosts found in hosts file.", file=sys.stderr)
        sys.exit(EXIT_ERR_NO_HOSTS)

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
            follow_symlinks=bool(args.follow_symlinks),
            sudo_collect_flag=(args.sudo_collect if hasattr(args, "sudo_collect") else None),
            verbose=bool(args.verbose),
        )
        results.append(res)

    total_downloaded: int = sum(r.downloaded for r in results)
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

    # Info line about link handling with packed path collection
    if bool(args.pack) and bool(args.follow_symlinks):
        # ダウンロード件数にはハードリンクの実体も含まれる旨の注意を明示
        print("(Note) With --pack and --follow-symlinks, hardlink targets are dereferenced and "
              "included in the 'downloaded' count.")

    exit_code: int = EXIT_ERR_GENERIC if len(err_hosts) > EXIT_OK else EXIT_OK
    sys.exit(exit_code)

if __name__ == "__main__":
    finalize_sockets()
    main()
