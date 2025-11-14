#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# cspell:ignore hostfile argparser

from __future__ import annotations

import re
import os
import sys
import shlex
import argparse
import getpass
import threading
import logging
from argparse import BooleanOptionalAction
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Union
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # 型チェッカー向けのダミー定義（実行時には評価されない）
    from gettext import gettext as _

from .core_ssh import (
    SSHConfig,
    SSHClientLike,
    SFTPClientLike,
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
from .core_select import (
    enumerate_candidates_for_host,
    Plan,
    PlanEntry,
)
from .core_cmd_flavor import run_remote_cmd_capture
from .core_remote_path import detect_remote_home
from .gather_parallel import execute as run_parallel
from .core_constants import (
    DEFAULT_PARALLEL_HOSTS,
    EXIT_OK,
    EXIT_ERR_ARGS,
    EXIT_ERR_NO_HOSTS,
    EXIT_ERR_TILDE_USER,
    RE_SAFE_HOST_PTN
)
from .core_logging import init_logging, shutdown_logging, HostLogAggregator
from .core_constants import DEFAULT_HOSTS_FILE
from .core_i18n import setup_gettext

_LOG = logging.getLogger(__name__)

def build_parser() -> argparse.ArgumentParser:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description=
            _("gm-gather: download remote files via SFTP (or remote tar) to a local DEST.\n"
            "Usage: gm-gather [SRC ...] DEST\n"
            "  - SRC:\n"
            "      * '/...': absolute on remote (UNIX)\n"
            "      * 'X:/...': absolute on remote (Windows)\n"
            "      * '~/...': expanded by -u user's HOME on remote\n"
            "      * RELATIVE: treated as '-u' user's HOME-relative,\n"
            "                  HOME escape via '..' is rejected.\n"
            "    The portion after the root is treated as a regex path.\n"
            "    e.g., '/etc/hosts' (literal), '/var/log/.*\\.log' (regex), '~/foo/.*', 'var/log/.*'.\n"
            "  - DEST: local directory where files are stored as DEST/<HOST>/..."),
        formatter_class=argparse.RawTextHelpFormatter,
    )

    # 位置引数 : SRC... DEST
    parser.add_argument(
        "src",
        nargs="+",
        help=_("One or more SRC path patterns. Absolute ('/','X:/','~/') or HOME-relative."),
    )
    parser.add_argument("dest", help=_("Local destination directory."))

    # SSH
    parser.add_argument(
        "-H",
        "--hosts",
        default=DEFAULT_HOSTS_FILE,
        help=_("Hosts file. Default: %(default)."),
    )
    parser.add_argument(
        "-u", "--user", default=getpass.getuser(), help=_("Target account on remote %(default).")
    )
    parser.add_argument(
        "-s", "--ssh-user", default=None, help=_("SSH login user. Default: same as --user.")
    )
    parser.add_argument(
        "-P", "--port", type=int, default=DEFAULT_SSH_PORT, help=_("SSH port. Default: %(default).")
    )
    parser.add_argument("-K", "--key", default=None, help=_("SSH private key file."))
    parser.add_argument("-W", "--password", default=None, help=_("SSH password (not recommended)."))
    parser.add_argument(
        "-T",
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=_("SSH/command timeout seconds. Default: %(default)."),
    )
    parser.add_argument(
        "-S", "--strict-host-key-checking", action="store_true", help=_("Enable strict host key checking.")
    )

    # 実行
    parser.add_argument(
        "-j",
        "--parallel",
        type=int,
        default=DEFAULT_PARALLEL_HOSTS,
        help=_("Parallel hosts (not parallel per-host). Default: %(default)."),
    )
    parser.add_argument("-n", "--dry-run", action="store_true", help=_("Show plan only; do not download."))
    parser.add_argument("-v", "--verbose", action="store_true", help=_("Verbose logs."))
    parser.add_argument("--pack", action="store_true", help=_("Pack on remote (tar.gz) and download once."))
    # --follow-symlinksは, ファイルへのシンボリックリンクをたどることを指示する
    #  ディレクトリへのシンボリックリンクについては未対応
    parser.add_argument(
        "--follow-symlinks",
        action="store_true",
        help=_("When used with --pack, dereference symlinks on remote."),
    )
    parser.add_argument(
        "-x",
        "--sudo-collect",
        action=BooleanOptionalAction,
        default=None,
        help=_("Use sudo for remote packing/collection (pack path only). Omitted = auto (enabled when ssh-user != --user)."),
    )

    return parser


# ---- pull_one factories ------------------------------------------------------


def _make_pull_one_sftp() -> Callable[[SFTPClientLike, str, Path, bool], None]:
    """SFTP 逐次GET。PlanEntry.relpathはローカル相対, remote_rootは per-entry。"""

    def _pull_one(sftp: SFTPClientLike, remote_path: str, local_path: Path, is_dir: bool) -> None:
        lp: Path = Path(local_path)

        _LOG.debug("[debug][sftp] get: remote=%s -> local=%s (is_dir=%s)", remote_path, str(lp), is_dir)

        if is_dir:
            return

        lp.parent.mkdir(parents=True, exist_ok=True)
        sftp.get(remote_path, str(lp))

    return _pull_one


def _make_pull_one_pack(
    ssh: SSHClientLike,
    sftp: SFTPClientLike,
    pack_list: List[str],
    *,
    timeout: float,
    use_sudo: bool,
    follow_symlinks: bool,
    host: str,
    dest_host_root: Path
) -> Callable[[SFTPClientLike, str, Path, bool], None]:
    """
    初回呼出しで pack+download+extract を実施。以降 no-op。
    Step4の権限/復元挙動 ( sudo指定時の権限復元含む ) を維持。
    """
    state: Dict[str, Union[int, bool]] = {"ran": False, "extracted": 0}

    def _pull_one(_sftp: SFTPClientLike, _remote: str, _local: Path, _is_dir: bool) -> None:

        _LOG.debug(
            "[debug][pack][sender] host=%s start: follow_symlinks=%s use_sudo=%s timeout=%s pack_items=%d",
            host, follow_symlinks, use_sudo, timeout, len(pack_list),
        )
        for line in pack_list:
            _LOG.debug("[debug][pack][sender][pack]   %s", line)

        if state["ran"]:
            return
        state["ran"] = True  # type: ignore[assignment]
        remote_gz: str = remote_pack_paths(
            ssh, pack_list, timeout=timeout, use_sudo=use_sudo, follow_symlinks=follow_symlinks
        )
        _LOG.debug("[debug][pack][sender] host=%s remote_tar_gz=%s", host, remote_gz)
        # 常に DEST/<HOST> 直下に展開（_local から親を推測しない）
        _LOG.debug(
            "[debug][pack][receiver] enter remote_tar_gz=%s extract_base=%s subdir=%s verbose=%s",
            remote_gz, str(dest_host_root), "", False,
        )

        # 常に DEST/<HOST> 直下に展開（_local から親を推測しない）
        # 第4引数subdirはdownload_and_extract_tar内で2重にパスを作らないよう
        # 空文字列を指定している。
        extracted, _ = download_and_extract_tar(_sftp, remote_gz, str(dest_host_root), "")
        state["extracted"] = extracted  # type: ignore[assignment]
        # sudo で作成された一時ファイルも確実に削除する
        cmd_rm = "sudo -n rm -f {f} || rm -f {f} || true".format(f=shlex.quote(remote_gz))
        _ = run_remote_cmd_capture(ssh, ["bash", "-lc", cmd_rm], timeout=timeout)
        _LOG.debug("[debug][pack][cleanup] host=%s removed %s; extracted_count=%s",
                    host, remote_gz, state["extracted"])
    return _pull_one

# ---- plan builder per host ---------------------------------------------------


def _split_remote_root_for_abs(
    p: str, *, home_abs: str
) -> Tuple[str, str]:
    """
    絶対パス文字列 p を (remote_root, inner_rel) に分解する。
    対応:
      - '/var/log/...'   -> ('/', 'var/log/...')
      - '~/foo/bar'      -> ('<home_abs>', 'foo/bar')
      - 'C:/Windows/...' -> ('C:/', 'Windows/...')
      - 'C:\\Windows\\..'-> ('C:/', 'Windows/...')
    """
    if len(p) >= 3 and p[1] == ":" and (p[2] == "/" or p[2] == "\\"):
        drive: str = p[:2]  # e.g., 'C:'
        rest: str = p[3:].replace("\\", "/")
        return f"{drive}/", rest.lstrip("/")
    if p.startswith("~/"):
        return home_abs.rstrip("/"), p[2:].lstrip("/")
    if p.startswith("/"):
        return "/", p.lstrip("/")
    # ここに来るのは非絶対の不正ケースだが, 呼び出し側で弾いている前提
    return "", p


def _build_plan_for_host(
    *,
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
    pack_remote: bool,
    follow_symlinks: bool,
    sudo_collect_flag: Optional[bool],
    verbose: bool,
) -> Tuple[Plan, Dict[str, object]]:
    """
    ホストごとに Plan を構築。
    戻り値: (plan, meta) / metaには home_abs, use_sudo, pack_list, ssh/sftp 等を格納。
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

    home_abs: str = detect_remote_home(ssh, args_user, timeout=timeout)
    sudo_collect: Optional[bool] = sudo_collect_flag
    use_sudo: bool = (ssh_user != args_user) if (sudo_collect is None) else bool(sudo_collect)

    # 候補列挙 ( SRCの正規表現混在を解決 )
    candidates: List[str] = enumerate_candidates_for_host(
        ssh=ssh,
        sftp_client=sftp,
        resolved_srcs=srcs,
        home_abs=home_abs,
        use_sudo=use_sudo,
        pack_remote=pack_remote,
        follow_symlinks=follow_symlinks,
        verbose=verbose,
    )
    _LOG.debug("[debug][plan] host=%s enumerate_candidates: %d item(s)", host, len(candidates))
    # ファイルのみに絞る ( symlink は pack+follow 指定時のみ pack_list に含める )
    files_only: List[str] = []
    symlinks: List[str] = []
    for rp in candidates:
        try:
            if not sftp_exists(sftp, rp):
                _LOG.debug("[debug][plan] host=%s skip_file=%s", host, rp)
                continue
            if sftp_isdir(sftp, rp):
                _LOG.debug("[debug][plan] host=%s skip_dir=%s", host, rp)
                continue
            if sftp_islink(sftp, rp):
                _LOG.debug("[debug][plan] host=%s add_symlink=%s", host, rp)
                symlinks.append(rp)
                continue
            if sftp_isfile(sftp, rp):
                _LOG.debug("[debug][plan] host=%s add_file=%s", host, rp)
                files_only.append(rp)
        except Exception:
            # 事前検査エラーは対象外扱い ( 進捗は run_host_gather 側でERROR加算 )
            pass

    # Plan 構築：relpath はローカル相対 ( DEST/<HOST>/relpath )
    entries: List[PlanEntry] = []
    safe_host = re.sub(RE_SAFE_HOST_PTN, "_", host).lstrip(".") or "_"
    dest_host_root: str = os.path.join(dest_local, safe_host)
    os.makedirs(dest_host_root, exist_ok=True)

    targets: List[str] = list(files_only)
    pack_list: List[str] = list(files_only) + (symlinks if pack_remote else [])
    if verbose:
        _LOG.debug(
            "[debug][plan] host=%s pack_remote=%s follow_symlinks=%s files=%d symlinks=%d pack_list=%d",
            host, pack_remote, follow_symlinks, len(files_only), len(symlinks), len(pack_list),
        )
        _LOG.debug("[debug][plan] sample files: %s", ", ".join(map(str, files_only[:3])))
        _LOG.debug("[debug][plan] sample symlinks: %s", ", ".join(map(str, symlinks[:3])))
        _LOG.debug("[debug][plan] sample pack_list: %s", ", ".join(map(str, pack_list[:5])))

    for rp in (targets if not pack_remote else pack_list):
        remote_root: str
        inner: str
        remote_root, inner = _split_remote_root_for_abs(rp, home_abs=home_abs)
        abs_local: str = local_path_for_download(dest_local, safe_host, rp)
        rel_local: str = os.path.relpath(abs_local, start=dest_host_root)

        pe = PlanEntry(
            path=Path(abs_local),
            relpath=rel_local,
            is_dir=False,
            remote_root=remote_root,
        )
        # 並行処理仕様を保ちつつ, 混在ルートでも確実に元の絶対パスへ到達できるように付帯情報を持たせる
        # - core_pull は remote_abs を最優先, 次点で remote_root + remote_rel を使用
        pe.remote_abs = rp
        pe.remote_rel = inner
        entries.append(pe)
        _LOG.debug(
            "[debug][plan] host=%s plan_entry: remote_abs=%s remote_root=%s remote_rel=%s -> local_abs=%s rel=%s",
            host, rp, remote_root, inner, abs_local, rel_local
        )
    meta: Dict[str, object] = {
        "ssh": ssh,
        "sftp": sftp,
        "home_abs": home_abs,
        "use_sudo": use_sudo,
        "pack_list": pack_list,
        "dest_host_root": dest_host_root,
    }
    return Plan(entries=entries), meta


# ---- main --------------------------------------------------------------------


def main() -> None:

    # 1. 国際化初期化
    setup_gettext()

    # 2. 引数解析
    parser: argparse.ArgumentParser = build_parser()
    args: argparse.Namespace = parser.parse_args()

    # --- デバッグ常時表示のため、最初にロギング初期化（既存handlerが無ければ） ---
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        # 既存の core_logging のフォーマットを使いたい場合は init_logging を使う
        # （run_parallel 側でも初期化されるが、重複追加しない実装なら問題なし）
        init_logging(verbose=True)
    else:
        # 既に何かしらの初期化が済んでいる環境でも INFO を出す
        root_logger.setLevel(logging.INFO)

    # 位置引数の検証
    if len(args.src) < 1 or not args.dest:
        print(_("At least one SRC and a DEST are required."), file=sys.stderr)
        sys.exit(EXIT_ERR_ARGS)

    # DEST: '~user' は非対応なので明示エラー
    _dest_raw: str = str(args.dest)
    _dest_tilde_user: Optional[str] = tilde_username(_dest_raw)
    if _dest_tilde_user is not None:
        print(
            _("Error: tilde with username is not supported in DEST: ~%(user)") % {"user": _dest_tilde_user},
            file=sys.stderr,
        )
        sys.exit(EXIT_ERR_TILDE_USER)

    # DEST: '~' をローカル実行ユーザの HOME で展開。相対ならカレント起点で絶対化。
    dest_local: str = os.path.expanduser(_dest_raw)
    if not is_local_abs(dest_local):
        dest_local = os.path.abspath(dest_local)

    srcs: List[str] = list(args.src)

    # SRC に '~user' が含まれていればエラー ( 共通仕様 )
    for s in srcs:
        u: Optional[str] = tilde_username(s)
        if u is not None:
            print(_("Error: tilde with username is not supported in SRC: ~%(user)") % {"user": u}, file=sys.stderr)
            sys.exit(EXIT_ERR_TILDE_USER)

    hosts: List[str] = parse_hosts_file(str(args.hosts))
    if len(hosts) == 0:
        print(_("No hosts found in hosts file."), file=sys.stderr)
        sys.exit(EXIT_ERR_NO_HOSTS)

    ssh_user: str = str(args.ssh_user) if args.ssh_user is not None else str(args.user)
    args_user: str = str(args.user)

    # per-host Plan 構築 ( regex 展開・~ 展開・drive対応 )
    plan_per_host: Dict[str, Plan] = {}
    meta_per_host: Dict[str, Dict[str, object]] = {}

    for h in hosts:
        plan: Plan
        meta: Dict[str, object]
        plan, meta = _build_plan_for_host(
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
            pack_remote=bool(args.pack),
            follow_symlinks=bool(args.follow_symlinks),
            sudo_collect_flag=(args.sudo_collect if hasattr(args, "sudo_collect") else None),
            verbose=bool(args.verbose),
        )
        plan_per_host[h] = plan
        meta_per_host[h] = meta

    # ---- dry-run: 計画だけ報告して終了 ( 集計は CLI 側で実施 )  ----
    if bool(args.dry_run):
        init_logging(verbose=bool(args.verbose))
        aggr: HostLogAggregator = HostLogAggregator(op="gather")
        for h in hosts:
            ph: Optional[Plan] = plan_per_host.get(h)
            total: int = len(ph) if ph is not None else 0
            aggr.start_host(h, total=total)
            aggr.done_host(h, warnings=0, errors=0)
        aggr.summary()
        shutdown_logging()
        sys.exit(EXIT_OK)

    # 接続ファクトリ ( 事前確立済みを返す )
    def _open_ssh(host: str) -> SSHClientLike:
        return meta_per_host[host]["ssh"]  # type: ignore[return-value]

    def _open_sftp(ssh: SSHClientLike) -> SFTPClientLike:
        for _h, m in meta_per_host.items():
            if m["ssh"] is ssh:
                return m["sftp"]  # type: ignore[return-value]
        # fallback ( 通常到達しない )
        return list(meta_per_host.values())[0]["sftp"]  # type: ignore[index, return-value]

    # SFTP 逐次 pull_one
    pull_one: Callable[[SFTPClientLike, str, Path, bool], None] = _make_pull_one_sftp()

    # --pack の場合, ホストごとに「一回だけ pack+extract」を実行する pull_one を差し替える
    pull_one_map: Optional[Dict[str, Callable[[SFTPClientLike, str, Path, bool], None]]] = None
    if bool(args.pack):
        pull_one_map = {}
        for h, m in meta_per_host.items():
            pull_one_map[h] = _make_pull_one_pack(
                m["ssh"],  # type: ignore[arg-type]
                m["sftp"],  # type: ignore[arg-type]
                m["pack_list"],  # type: ignore[arg-type]
                timeout=float(args.timeout),
                use_sudo=bool(m["use_sudo"]),
                follow_symlinks=bool(args.follow_symlinks),
                host=h,
                dest_host_root=Path(m["dest_host_root"]),  # type: ignore[arg-type]
            )

    # 並行実行 : ロギング初期化・集計は run_parallel 側が担当
    exit_code: int = run_parallel(
        hosts=hosts,
        plan_per_host=plan_per_host,       # execute() の実シグネチャに合わせる
        remote_root="",                    # per-entry remote_root を core_pull 側で優先
        dest_root=Path(dest_local),
        parallel=max(1, int(args.parallel)),
        verbose=bool(args.verbose),
        abort_event=threading.Event(),
        open_ssh=_open_ssh,
        open_sftp=_open_sftp,
        pull_one=pull_one,
        pull_one_map=pull_one_map,   # ← 次セクションの C) に合わせ, execute 側で受ける
        join_host_dir=True,
        remote_removers=None,
        do_cleanup_local=False,
        do_cleanup_remote=False,
     )
    sys.exit(exit_code)


if __name__ == "__main__":
    # 開始前の finalize ではなく、終了時に必ず後片付けを行う
    try:
        main()
    finally:
        try:
            finalize_sockets()
        except Exception:
            pass