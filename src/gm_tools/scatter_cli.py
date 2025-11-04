# -*- coding:utf-8 -*-
from __future__ import annotations
from typing import List, Optional, Tuple
from argparse import Namespace, BooleanOptionalAction
import argparse, getpass, sys, os

# Paramiko 型注釈用
try:
    import paramiko  # type: ignore
except Exception as e:
    raise RuntimeError("Paramiko is required: pip install paramiko") from e

from .core_common import parse_hosts_file
from .core_scatter import ScatterOpts, local_pack_paths_to_tmp, upload_pack_and_extract, sftp_put_one
from .core_select import enumerate_candidates_local
from .core_ssh import SSHConfig, ssh_open, finalize_sockets,DEFAULT_SSH_PORT,DEFAULT_TIMEOUT
from .core_report import TransferReport, TransferItem
from .core_selinux import SelinuxMode
from .core_path_handling import (
    is_windows_abs,
    is_local_abs,       # type: ignore 使わないが将来の整合のためインポート
    tilde_username,
)

from .core_remote_path import detect_remote_home

# === Constants ===
DEFAULT_HOSTS_FILE: str = "hostfile"
DEFAULT_PARALLEL_HOSTS: int = 1

# Exit codes gather/scatter 共通化
EXIT_OK:           int = 0
EXIT_ERR_NO_HOSTS: int = 1
EXIT_ERR_GENERIC:   int = 2
EXIT_ERR_TILDE_USER:int = 3
EXIT_ERR_ARGS:      int = 5

def build_parser() -> argparse.ArgumentParser:
    p: argparse.ArgumentParser = argparse.ArgumentParser(
        prog="gm-scatter",
        description=("gm-scatter: upload local files to remote DEST.\n"
                     "Usage: gm-scatter [SRC ...] DEST\n"
                     "Remote layout: DEST/<local_abs_without_leading_slash>"),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    # gather/scatterともに: SRC ... DEST の順に指定
    p.add_argument("src", nargs="+", help="local SRC paths (abs or rel)")
    p.add_argument("dest", help="remote DEST absolute root (e.g., /dest)")
    # SSH (align with gather)
    p.add_argument("-H", "--hosts", default=DEFAULT_HOSTS_FILE, help=f"Hosts file. Default: {DEFAULT_HOSTS_FILE}.")
    p.add_argument("-u", "--user", default=getpass.getuser(), help="Target account semantics on remote (展開アカウント).")
    p.add_argument("-s", "--ssh-user", default=None, help="SSH login user. Default: same as --user.")
    p.add_argument("-P", "--port", type=int, default=DEFAULT_SSH_PORT, help=f"SSH port. Default: {DEFAULT_SSH_PORT}.")
    p.add_argument("-K", "--key", default=None, help="SSH private key file.")
    p.add_argument("-W", "--password", default=None, help="SSH password (not recommended).")
    p.add_argument("-T", "--timeout", type=float, default=DEFAULT_TIMEOUT, help=f"SSH/command timeout seconds. Default: {DEFAULT_TIMEOUT}.")
    p.add_argument("-S", "--strict-host-key-checking", action="store_true", help="Enable strict host key checking.")


    # 実行
    p.add_argument("-j", "--parallel", type=int, default=DEFAULT_PARALLEL_HOSTS, help=f"Parallel hosts (not parallel per-host). Default: {DEFAULT_PARALLEL_HOSTS}.")
    p.add_argument("-n", "--dry-run", action="store_true", help="Show plan only; do not upload.")
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose logs.")
    p.add_argument("--pack", action="store_true", help="pack to tar.gz then extract remotely")
    # pack 時のリンク追随 ( 指定時にのみ dereference ) 。デフォルトは追随しない＝リンクは含めない。
    p.add_argument("--follow-symlinks", action="store_true", help="when packing, dereference symlinks")

    # tri-state: True (--sudo-extract), False (--no-sudo-extract), None (auto)
    p.add_argument(
        "-x", "--sudo-extract",
        action=BooleanOptionalAction,
        default=None,
        help="Force sudo for remote mkdir/extract when packing (use --no-sudo-extract to force off). Omitted = auto.",
    )
    # SELinuxはpack経路のみ有効（SFTP経路では無効）
    p.add_argument(
        "--selinux",
        choices=["auto", "policy", "ignore"],
        default="auto",
        help="SELinux label restore policy (pack path only). Default: auto.",
    )
    return p

def _resolve_remote_dest(dest_raw: str, remote_home: str) -> Tuple[str, Optional[str]]:
    """
    仕様:
      - '/' で始まれば絶対
      - 'X:\\'/'X:/'（Windows）で始まれば絶対
      - '~' / '~/' は remote_home に展開
      - '~user' は非対応（エラーメッセージを返す）
      - 上記以外は remote_home からの相対
    戻り値: (dest_abs, error_or_None)
    """
    d: str = dest_raw.strip()
    if not d:
        return remote_home, None
    if d.startswith("/"):
        return d, None
    if is_windows_abs(d):
        return d, None
    u: Optional[str] = tilde_username(d)
    if u is not None:
        return "", f"tilde with username is not supported: ~{u}"
    if d == "~" or d.startswith("~/"):
        tail: str = d[1:].lstrip("/\\")
        return (remote_home if not tail else f"{remote_home}/{tail}"), None
    # 相対
    return f"{remote_home}/{d}", None


def run_one_host(host: str, args: Namespace) -> None:
    ssh_user: str = str(args.ssh_user) if args.ssh_user is not None else str(args.user)
    target_user: str = str(args.user)
    selinux_mode: SelinuxMode = str(args.selinux) if hasattr(args, "selinux") else "auto"  # type: ignore[assignment]
    report: TransferReport = TransferReport()

    # SSH open
    cfg: SSHConfig = SSHConfig(
        host=host,
        port=int(args.port),
        ssh_user=ssh_user,
        key_filename=str(args.key) if args.key else None,
        password=str(args.password) if args.password else None,
        timeout=float(args.timeout),
        strict_host_key_checking=bool(args.strict_host_key_checking),
    )
    ssh = ssh_open(cfg, debug_print=bool(args.verbose))
    sftp = ssh.open_sftp()

    try:

        # リモート HOME 取得
        remote_home: str = detect_remote_home(ssh, target_user, float(args.timeout))

        # DEST 絶対解決（~user はエラー）
        raw_dest: str = str(args.dest)
        dest_abs_root, dest_err = _resolve_remote_dest(raw_dest, remote_home)
        if dest_err is not None:
            print(f"[{host}] Error: {dest_err}", file=sys.stderr)
            return

        # --sudo-extract tri-state（未指定 None は auto 判定）
        se_flag: Optional[bool] = args.sudo_extract if hasattr(args, "sudo_extract") else None
        sudo_extract_effective: bool = (bool(args.pack) and (ssh_user != str(args.user))) if se_flag is None else bool(se_flag)

        # SRC の '~user' を明示エラー。'~'/'~/' はローカル HOME に展開。
        src_raw: List[str] = list(args.src)
        for s in src_raw:
            u = tilde_username(s)
            if u is not None:
                print(f"[{host}] Error: tilde with username is not supported in SRC: ~{u}", file=sys.stderr)
                return

        # '~' 展開（ローカル実行ユーザの HOME）
        src_expanded: List[str] = [os.path.expanduser(s) for s in src_raw]
        # SRC が相対ならカレント起点で絶対化
        src_abs: List[str] = [
            s if (s.startswith("/") or is_windows_abs(s)) else os.path.abspath(s)
            for s in src_expanded
        ]

        opts = ScatterOpts(
            dest_abs_root=dest_abs_root,
            pack=bool(args.pack),
            follow_symlinks=bool(args.follow_symlinks),
            dry_run=bool(args.dry_run),
            sudo_extract=sudo_extract_effective,
            ssh_user=ssh_user,
            local_user=getpass.getuser(),
            # Step4 追加
            target_user=target_user,
            selinux_mode=selinux_mode,
        )
        # SRC 群を列挙（globbing => 絶対化済み => 重複排除）
        cands: List[str] = list(enumerate_candidates_local(src_abs))
        if opts.pack:
            # When packing, symlink deref handling is decided by --follow-symlinks.
            tar_path, _deref = local_pack_paths_to_tmp(cands, follow_symlinks=opts.follow_symlinks)
            # (Optional) deref note printing could be added if desired.
            upload_pack_and_extract(
                ssh,
                sftp,
                tar_path,
                opts.dest_abs_root,
                opts.sudo_extract,
                host,
                report,
                opts.dry_run,
                target_user=opts.target_user,
                selinux_mode=opts.selinux_mode,
            )
        else:
            # Sequential SFTP: mkdir は SSH 経由の 'mkdir -p'（core_scatter 側実装）を使用
            for pth in cands:
                sftp_put_one(ssh, sftp, pth, opts.dest_abs_root, host, report, opts.dry_run, sudo_mkdir=(ssh_user != str(args.user)))
    finally:
        try:
            sftp.close()
        except Exception:
            pass
        try:
            ssh.close()
        except Exception:
            pass

    # Summary
    planned: List[TransferItem] = report.planned()
    dropped: List[TransferItem] = report.dropped()
    failed: List[TransferItem] = report.failed()
    if planned:
        print("[planned]")
        for it in planned:
            print(f"  {it.host}: {it.local_path or '-'} -> {it.remote_path}")
    if dropped:
        print("[dropped]")
        for it in dropped:
            print(f"  {it.host}: {it.local_path or '-'} -> {it.remote_path}  reason={it.reason}")
    if failed:
        print("[failed]")
        for it in failed:
            print(f"  {it.host}: {it.local_path or '-'} -> {it.remote_path}  reason={it.reason}")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    hosts: List[str] = parse_hosts_file(str(args.hosts))
    if len(hosts) == 0:
        print("No hosts found in hosts file.", file=sys.stderr)
        sys.exit(EXIT_ERR_NO_HOSTS)

    for h in hosts:
        run_one_host(h, args)


if __name__ == "__main__":
    finalize_sockets()
    main()
