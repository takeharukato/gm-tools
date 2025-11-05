# -*- coding:utf-8 -*-
from __future__ import annotations

import argparse
import getpass
import os
import sys
import threading
from argparse import BooleanOptionalAction, Namespace
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Final

from .core_common import parse_hosts_file
from .core_report import NullTransferReport
from .core_constants import (
    DEFAULT_HOSTS_FILE,
    DEFAULT_PARALLEL_HOSTS,
    EXIT_ERR_ARGS,
    EXIT_ERR_NO_HOSTS,
    EXIT_OK,
)
from .core_logging import HostLogAggregator, init_logging, shutdown_logging
from .core_path_handling import (
    is_local_abs,  # type: ignore[unused-ignore] 使わないが将来の整合のため保持
    is_windows_abs,
    tilde_username,
)
from .core_push import PushOne
from .core_remote_path import detect_remote_home
from .core_scatter import (
    local_pack_paths_to_tmp,
    sftp_put_one,
    upload_pack_and_extract,
)
from .core_select import Plan, PlanEntry, enumerate_candidates_local
from .core_selinux import SelinuxMode
from .core_ssh import (
    DEFAULT_SSH_PORT,
    DEFAULT_TIMEOUT,
    SFTPClientLike,
    SSHClientLike,
    SSHConfig,
    finalize_sockets,
    ssh_open,
)
from .scatter_parallel import execute as run_parallel

# === Module-level constants ===
_DESC: str = (
    "gm-scatter: upload local files to remote DEST.\n"
    "Usage: gm-scatter [SRC ...] DEST\n"
    "Remote layout: DEST/<local_abs_without_leading_slash>"
)

# Null Object: 読み捨て用のレポートシンク
# None は使わず常に TransferReport を渡す
_NULL_REPORT: Final[NullTransferReport] = NullTransferReport()

def build_parser() -> argparse.ArgumentParser:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        prog="gm-scatter",
        description=_DESC,
        formatter_class=argparse.RawTextHelpFormatter,
    )
    # 位置引数: SRC ... DEST
    parser.add_argument("src", nargs="+", help="Local SRC paths (abs or rel)")
    parser.add_argument("dest", help="Remote DEST absolute root (e.g., /dest)")

    # SSH
    parser.add_argument(
        "-H", "--hosts", default=DEFAULT_HOSTS_FILE, help=f"Hosts file. Default: {DEFAULT_HOSTS_FILE}."
    )
    parser.add_argument(
        "-u", "--user", default=getpass.getuser(), help="Target account semantics on remote."
    )
    parser.add_argument(
        "-s", "--ssh-user", default=None, help="SSH login user. Default: same as --user."
    )
    parser.add_argument(
        "-P", "--port", type=int, default=DEFAULT_SSH_PORT, help=f"SSH port. Default: {DEFAULT_SSH_PORT}."
    )
    parser.add_argument("-K", "--key", default=None, help="SSH private key file.")
    parser.add_argument("-W", "--password", default=None, help="SSH password (not recommended).")
    parser.add_argument(
        "-T",
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"SSH/command timeout seconds. Default: {DEFAULT_TIMEOUT}.",
    )
    parser.add_argument(
        "-S", "--strict-host-key-checking", action="store_true", help="Enable strict host key checking."
    )

    # 実行
    parser.add_argument(
        "-j",
        "--parallel",
        type=int,
        default=DEFAULT_PARALLEL_HOSTS,
        help=f"Parallel hosts (not parallel per-host). Default: {DEFAULT_PARALLEL_HOSTS}.",
    )
    parser.add_argument("-n", "--dry-run", action="store_true", help="Show plan only; do not upload.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logs.")
    parser.add_argument("--pack", action="store_true", help="Pack locally (tar.gz) then extract remotely.")
    parser.add_argument("--follow-symlinks", action="store_true", help="When packing, dereference symlinks.")

    # tri-state: True (--sudo-extract), False (--no-sudo-extract), None (auto)
    parser.add_argument(
        "-x",
        "--sudo-extract",
        action=BooleanOptionalAction,
        default=None,
        help="Force sudo for remote mkdir/extract when packing (use --no-sudo-extract to force off). Omitted = auto.",
    )
    # SELinux は pack 経路のみで使用
    parser.add_argument(
        "--selinux",
        choices=["auto", "policy", "ignore"],
        default="auto",
        help="SELinux label restore policy (pack path only). Default: auto.",
    )
    return parser


def _resolve_remote_dest(dest_raw: str, remote_home: str) -> Tuple[str, Optional[str]]:
    """
    仕様:
      - '/' で始まれば絶対
      - 'X:\\'/'X:/' ( Windows ) で始まれば絶対
      - '~' / '~/' は remote_home に展開
      - '~user' は非対応 ( エラーメッセージを返す )
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


def _build_plan_for_host(
    *,
    host: str,
    dest_remote_raw: str,
    srcs_raw: List[str],
    ssh_user: str,
    target_user: str,
    port: int,
    key: Optional[str],
    password: Optional[str],
    timeout: float,
    strict: bool,
    pack: bool,
    follow_symlinks: bool,
    sudo_extract_flag: Optional[bool],
    selinux_mode: SelinuxMode,
    verbose: bool,
) -> Tuple[Plan, Dict[str, object]]:
    """
    Plan + Meta per host ( Paramiko 直接依存なし, DI 用に接続を構築して meta に格納 ) 。
    - SRC 正規表現混在の解決 ( ローカル )
    - DEST の絶対解決 ( ~ 展開 )
    - 配置規則: DEST/<local_abs_without_leading_slash>
    """
    cfg: SSHConfig = SSHConfig(
        host=host,
        port=port,
        ssh_user=ssh_user,
        key_filename=key,
        password=password,
        timeout=timeout,
        strict_host_key_checking=strict,
    )
    ssh: SSHClientLike = ssh_open(cfg, debug_print=verbose)
    sftp: SFTPClientLike = ssh.open_sftp()

    remote_home: str = detect_remote_home(ssh, target_user, float(timeout))
    dest_abs_root, dest_err = _resolve_remote_dest(dest_remote_raw, remote_home)
    if dest_err is not None:
        raise SystemExit(f"[{host}] Error: {dest_err}")

    # sudo-extract ( 三値 None=auto ) : auto は (--pack かつ ssh_user != target_user) で True
    auto_sudo: bool = bool(pack) and (ssh_user != target_user)
    sudo_extract: bool = auto_sudo if (sudo_extract_flag is None) else bool(sudo_extract_flag)

    # SRC '~user' 不許可・'~' 展開・絶対化
    for s in srcs_raw:
        u: Optional[str] = tilde_username(s)
        if u is not None:
            raise SystemExit(f"[{host}] Error: tilde with username is not supported in SRC: ~{u}")
    src_expanded: List[str] = [os.path.expanduser(s) for s in srcs_raw]
    src_abs: List[str] = [s if (s.startswith("/") or is_windows_abs(s)) else os.path.abspath(s) for s in src_expanded]

    # 候補列挙 ( 重複排除・順序安定 )
    cands: List[str] = list(enumerate_candidates_local(src_abs))

    # Plan を構築：relpath は「ローカル絶対の先頭セパレータ除去」
    entries: List[PlanEntry] = []
    for p in cands:
        st_is_dir: bool = os.path.isdir(p)
        # 非 pack 経路ではディレクトリはスキップ
        if (not pack) and st_is_dir:
            continue
        rel_local: str = p.replace("\\", "/").lstrip("/")
        entries.append(PlanEntry(path=Path(p), relpath=rel_local, is_dir=st_is_dir))
    plan: Plan = Plan(entries=entries)

    meta: Dict[str, object] = {
        "ssh": ssh,
        "sftp": sftp,
        "dest_abs_root": dest_abs_root,
        "sudo_extract": sudo_extract,
        "target_user": target_user,
        "ssh_user": ssh_user,
        "pack": bool(pack),
        "follow_symlinks": bool(follow_symlinks),
        "selinux_mode": selinux_mode,
        "timeout": float(timeout),
        "verbose": bool(verbose),
    }
    return plan, meta


def _make_push_one_sftp(
    *,
    ssh: SSHClientLike,
    sftp: SFTPClientLike,
    dest_abs_root: str,
    ssh_user: str,
    target_user: str,
    host: str,
) -> PushOne:
    """
    非 pack 経路：PlanEntry 毎の逐次 PUT。
    リモート配置は core_scatter.sftp_put_one が DEST/<local_abs_without_leading_slash> を作成。
    """
    def _push_one(_sftp: SFTPClientLike, local_path: Path, _remote_root: str, is_dir: bool) -> None:
        if is_dir:
            return
        _local_path_str: str = str(local_path)
        sftp_put_one(
            ssh,
            sftp,
            _local_path_str,
            dest_abs_root,
            host,
            _NULL_REPORT,    # Null Object (読み捨て)
            False,           # dry_run
            sudo_mkdir=(ssh_user != target_user),
        )

    return _push_one


def _make_push_one_pack(
    *,
    ssh: SSHClientLike,
    sftp: SFTPClientLike,
    pack_srcs: List[Path],
    dest_abs_root: str,
    sudo_extract: bool,
    follow_symlinks: bool,
    target_user: str,
    selinux_mode: SelinuxMode,
    timeout: float,
    host: str,
) -> PushOne:
    """
    pack 経路：最初の 1 回だけ local pack  =>  upload  =>  remote extract。
    """
    state: Dict[str, bool] = {"ran": False}

    def _push_one(_sftp: SFTPClientLike, _local_path: Path, _remote_root: str, _is_dir: bool) -> None:
        if state["ran"]:
            return
        state["ran"] = True
        src_list: List[str] = [str(p) for p in pack_srcs]
        # アンパック代入に直接の型注釈は不可なため, いったんタプルに受けてから展開する。
        # local_pack_paths_to_tmp の戻り値は Tuple[str, List[str]]
        tar_tuple: Tuple[str, List[str]] = local_pack_paths_to_tmp(
            src_list,
            follow_symlinks=follow_symlinks,
        )
        tar_path: str
        _src_manifest: List[str]
        tar_path, _src_manifest = tar_tuple
        upload_pack_and_extract(
            ssh,
            sftp,
            tar_path,
            dest_abs_root,
            sudo_extract,
            host,
            _NULL_REPORT,   # Null Object (読み捨て)
            False,          # dry_run
            target_user=target_user,
            selinux_mode=selinux_mode,
        )

    return _push_one


def main() -> None:
    parser: argparse.ArgumentParser = build_parser()
    args: Namespace = parser.parse_args()

    # 位置引数検証 ( gather と同様の方針 )
    if len(args.src) < 1 or not args.dest:
        print("At least one SRC and a DEST are required.", file=sys.stderr)
        sys.exit(EXIT_ERR_ARGS)

    hosts: List[str] = parse_hosts_file(str(args.hosts))
    if len(hosts) == 0:
        print("No hosts found in hosts file.", file=sys.stderr)
        sys.exit(EXIT_ERR_NO_HOSTS)

    # per-host の Plan/Meta 構築 ( 接続確立もここで実施し DI で渡す )
    ssh_user: str = str(args.ssh_user) if args.ssh_user is not None else str(args.user)
    target_user: str = str(args.user)
    selinux_mode: SelinuxMode = str(args.selinux) if hasattr(args, "selinux") else "auto"  # type: ignore[assignment]

    plan_per_host: Dict[str, Plan] = {}
    meta_per_host: Dict[str, Dict[str, object]] = {}

    for host in hosts:
        plan: Plan
        meta: Dict[str, object]
        plan, meta = _build_plan_for_host(
            host=host,
            dest_remote_raw=str(args.dest),
            srcs_raw=list(args.src),
            ssh_user=ssh_user,
            target_user=target_user,
            port=int(args.port),
            key=str(args.key) if args.key else None,
            password=str(args.password) if args.password else None,
            timeout=float(args.timeout),
            strict=bool(args.strict_host_key_checking),
            pack=bool(args.pack),
            follow_symlinks=bool(args.follow_symlinks),
            sudo_extract_flag=(args.sudo_extract if hasattr(args, "sudo_extract") else None),
            selinux_mode=selinux_mode,
            verbose=bool(args.verbose),
        )
        plan_per_host[host] = plan
        meta_per_host[host] = meta

    # dry-run: 計画のみ出力 ( logger 集約 )
    if bool(args.dry_run):
        init_logging(verbose=bool(args.verbose))
        aggr: HostLogAggregator = HostLogAggregator(op="scatter")
        for host in hosts:
            total: int = len(plan_per_host.get(host) or [])
            aggr.start_host(host, total=total)
            aggr.done_host(host, warnings=0, errors=0)
        aggr.summary()
        shutdown_logging()
        sys.exit(EXIT_OK)

    # 事前確立済み接続の DI 工場
    def _open_ssh(host: str) -> SSHClientLike:
        return meta_per_host[host]["ssh"]  # type: ignore[return-value]

    def _open_sftp(ssh: SSHClientLike) -> SFTPClientLike:
        for _h, m in meta_per_host.items():
            if m["ssh"] is ssh:
                return m["sftp"]  # type: ignore[return-value]
        # fallback ( 通常到達しない )
        return list(meta_per_host.values())[0]["sftp"]  # type: ignore[index, return-value]

    # push_one をホスト毎に割当
    push_one_map: Dict[str, PushOne] = {}
    for host, meta in meta_per_host.items():

        # timeout は meta に object として格納されているため, 安全に float へ収束させる
        timeout_obj: object = meta.get("timeout", DEFAULT_TIMEOUT)  # type: ignore[assignment]
        timeout_f: float
        if isinstance(timeout_obj, (int, float)):
            timeout_f = float(timeout_obj)
        elif isinstance(timeout_obj, str):
            try:
                timeout_f = float(timeout_obj)
            except ValueError:
                timeout_f = DEFAULT_TIMEOUT
        else:
            timeout_f = DEFAULT_TIMEOUT


        if bool(meta["pack"]):  # type: ignore[index]
            push_one_map[host] = _make_push_one_pack(
                ssh=meta["ssh"],  # type: ignore[arg-type]
                sftp=meta["sftp"],  # type: ignore[arg-type]
                pack_srcs=[e.path for e in plan_per_host[host].entries],
                dest_abs_root=str(meta["dest_abs_root"]),
                sudo_extract=bool(meta["sudo_extract"]),
                follow_symlinks=bool(meta["follow_symlinks"]),
                target_user=str(meta["target_user"]),
                selinux_mode=meta["selinux_mode"],  # type: ignore[arg-type]
                timeout=timeout_f,
                host=host,
            )
        else:
            push_one_map[host] = _make_push_one_sftp(
                ssh=meta["ssh"],  # type: ignore[arg-type]
                sftp=meta["sftp"],  # type: ignore[arg-type]
                dest_abs_root=str(meta["dest_abs_root"]),
                ssh_user=str(meta["ssh_user"]),
                target_user=str(meta["target_user"]),
                host=host,
            )

    # 並行実行: DEST/<local_abs_without_leading_slash> へ配置 ( Step4 のレイアウト規則を厳守 )
    exit_code: int = run_parallel(
        hosts=hosts,
        plan_per_host=plan_per_host,
        remote_root=str(args.dest), # 実質未使用: push_one_map がすべての転送先のルートを保持する
        src_root=Path("."),  # 未使用 ( PlanEntry.path を直接参照 )
        parallel=max(1, int(args.parallel)),
        verbose=bool(args.verbose),
        abort_event=threading.Event(),
        open_ssh=_open_ssh,       # type: SSHFactory
        open_sftp=_open_sftp,     # type: SFTPFactory
        push_one=lambda s, lp, rr, d: None,  # 既定は未使用 ( push_one_map を利用 )
        push_one_map=push_one_map,
        join_host_dir=False,      # HOST サブディレクトリは作らない
        remote_removers=None,
        do_cleanup_local=False,
        do_cleanup_remote=False,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    finalize_sockets()
    main()
