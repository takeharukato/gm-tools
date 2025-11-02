#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import getpass
import sys
from typing import List

from .core_common import parse_hosts_file
from .core_ssh import add_ssh_common_args, finalize_sockets
from .core_push import push_file_to_host

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Scatter (push) local file(s) to remote via SFTP.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    p.add_argument("local", help="Local file to upload.")
    p.add_argument("remote", help="Remote absolute destination path.")
    p.add_argument("-H", "--hosts", default="hostfile", help="Hosts file. Default: hostfile.")
    p.add_argument("-u", "--user", default=getpass.getuser(), help="Target account semantics (unused for push).")
    add_ssh_common_args(p)
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose logs.")
    return p

def main() -> None:
    ap = build_parser()
    args = ap.parse_args()

    hosts: List[str] = parse_hosts_file(args.hosts)
    if not hosts:
        print("No hosts found in hosts file.", file=sys.stderr)
        sys.exit(1)

    ssh_user: str = args.ssh_user or args.user
    ok: int = 0
    for h in hosts:
        host, success, err = push_file_to_host(
            host=h,
            ssh_user=ssh_user,
            port=int(args.port),
            key=str(args.key) if args.key else None,
            password=str(args.password) if args.password else None,
            timeout=float(args.timeout),
            strict=bool(args.strict_host_key_checking),
            local_path=str(args.local),
            remote_path=str(args.remote),
            verbose=bool(args.verbose),
        )
        if success:
            ok += 1
        else:
            print(f"[{host}] ERROR: {err}", file=sys.stderr)

    print(f"\nSummary: success {ok}/{len(hosts)}")
    sys.exit(0 if ok == len(hosts) else 2)

if __name__ == "__main__":
    finalize_sockets()
    main()
