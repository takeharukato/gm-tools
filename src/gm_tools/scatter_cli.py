# -*- coding:utf-8 -*-
from __future__ import annotations
from typing import List
from argparse import Namespace
import argparse, getpass, sys

from .core_common import parse_hosts_file
from .core_scatter import ScatterOpts, local_pack_paths_to_tmp, upload_pack_and_extract, sftp_put_one
from .core_select import enumerate_candidates_local
from .core_ssh import SSHConfig, ssh_open, finalize_sockets
from .core_report import TransferReport, TransferItem
from .core_selinux import SelinuxMode

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
    p.add_argument("-H", "--hosts", default="hostfile", help="Hosts file. Default: hostfile.")
    p.add_argument("-u", "--user", default=getpass.getuser(), help="Target account semantics on remote (展開アカウント).")
    p.add_argument("-s", "--ssh-user", default=None, help="SSH login user. Default: same as --user.")
    p.add_argument("-P", "--port", type=int, default=22, help="SSH port. Default: 22.")
    p.add_argument("-K", "--key", default=None, help="SSH private key file.")
    p.add_argument("-W", "--password", default=None, help="SSH password (not recommended).")
    p.add_argument("-T", "--timeout", type=float, default=30.0, help="SSH/command timeout seconds. Default: 30.")
    p.add_argument("-S", "--strict-host-key-checking", action="store_true", help="Enable strict host key checking.")
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose SSH connect log.")

    # Scatter options
    p.add_argument("--pack", action="store_true", help="pack to tar.gz then extract remotely")
    p.add_argument("--follow-symlinks", action="store_true", help="when packing, dereference symlinks")
    p.add_argument("--dry-run", action="store_true", help="plan only")
    p.add_argument("-x", "--sudo-extract", action="store_true", help="use sudo to mkdir/extract on remote when packing")
    # SELinuxはpack経路のみ有効（SFTP経路では無効）
    p.add_argument(
        "--selinux",
        choices=["auto", "policy", "ignore"],
        default="auto",
        help="SELinux label restore policy (pack path only). Default: auto.",
    )
    return p


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
        opts = ScatterOpts(
            dest_abs_root=str(args.dest),
            pack=bool(args.pack),
            follow_symlinks=bool(args.follow_symlinks),
            dry_run=bool(args.dry_run),
            sudo_extract=bool(args.sudo_extract) and bool(args.pack) and (ssh_user != str(args.user)),
            ssh_user=ssh_user,
            local_user=getpass.getuser(),
            # Step4 追加
            target_user=target_user,
            selinux_mode=selinux_mode,
        )
        # SRC 群を列挙（globbing → 絶対化 → 重複排除）
        cands: List[str] = list(enumerate_candidates_local(list(args.src)))
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
        sys.exit(1)

    for h in hosts:
        run_one_host(h, args)


if __name__ == "__main__":
    finalize_sockets()
    main()
