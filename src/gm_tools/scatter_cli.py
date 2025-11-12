# -*- coding:utf-8 -*-
from __future__ import annotations

import argparse
import getpass
import os
import sys
import threading
from argparse import BooleanOptionalAction, Namespace
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Final, Sequence, Set

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
    # 相対/絶対 SRC の正規化とレイアウト算出に利用
    ScatterSrcToken,
    ScatterResolvedToken,
    resolve_token_for_scatter,
    normalize_rel_for_dest,
    looks_like_regex,
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
    "Remote layout: DEST/<rel>/...  where rel =\n"
    "  - for absolute SRC: <local_abs_without_leading_slash>\n"
    "  - for relative SRC: the original relative path"
)
ERR_TILDE_USERNAME: Final[str] = "tilde with username is not supported"
ERR_BARE_TILDE: Final[str] = "bare tilde is not allowed"

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
    parser.add_argument("dest", help="Remote DEST ( supports /abs, ~/, and relative-from-remote-home )")

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
      - '~/' は remote_home に展開
      - 素の '~' は非対応 ( エラーメッセージを返す )
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
        # "~user/..." は非対応
        return "", ERR_TILDE_USERNAME
    if d == "~":
        # 素の "~" は非対応
        return "", ERR_BARE_TILDE
    if d.startswith("~/"):
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
    - 配置規則: DEST/<rel>/... ( 絶対 SRC は先頭スラッシュを除去、相対 SRC は指定相対を保持 )
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
        # 仕様に合わせて厳密な文言・終了コードで終了
        print(dest_err, file=sys.stderr)
        raise SystemExit(EXIT_ERR_ARGS)

    # sudo-extract ( 三値 None=auto ) : auto は (--pack かつ ssh_user != target_user) で True
    auto_sudo: bool = bool(pack) and (ssh_user != target_user)
    sudo_extract: bool = auto_sudo if (sudo_extract_flag is None) else bool(sudo_extract_flag)

    # SRC '~user' 不許可
    for s in srcs_raw:
        u: Optional[str] = tilde_username(s)
        if u is not None:
            print(ERR_TILDE_USERNAME, file=sys.stderr)
            raise SystemExit(EXIT_ERR_ARGS)

    # SRC を scatter 仕様に基づいて解決 ( ~/ は実行ユーザ HOME 展開、相対は cwd 起点 )
    resolved_tokens: List[ScatterResolvedToken] = []
    for s in srcs_raw:
        tok: ScatterSrcToken = ScatterSrcToken(raw=str(s))
        res: ScatterResolvedToken = resolve_token_for_scatter(tok, cwd=os.getcwd())
        resolved_tokens.append(res)

    # 候補列挙とリモート配置 rel の算出
    #  - 絶対 SRC: rel_root は <local_abs_without_leading_slash>
    #  - 相対 SRC: rel_root は <指定された相対パス> ( 正規化済 )
    entries: List[PlanEntry] = []
    remote_rel_map: Dict[str, str] = {}
    for res in resolved_tokens:
        # pack ルートが候補列挙に出ない実装に備えて、先にマップだけは保証しておく
        remote_rel_map[os.path.abspath(res.abs_root)] = res.rel_root
        cand_list: List[str] = list(enumerate_candidates_local([res.abs_root]))
        for p in cand_list:
            st_is_dir: bool = os.path.isdir(p)
            if (not pack) and st_is_dir:
                continue
            # inner_rel: abs_root からの相対。ルート自身は追加なし ( =空 ) 。
            try:
                inner_rel_raw: str = os.path.relpath(p, start=res.abs_root)
            except ValueError:
                inner_rel_raw = ""
            if inner_rel_raw in (".", "\\", ""):
                inner_rel_raw = ""

            inner_rel: str = normalize_rel_for_dest(inner_rel_raw)
            remote_rel: str = res.rel_root if not inner_rel else normalize_rel_for_dest(f"{res.rel_root}/{inner_rel}")
            # ルックアップの安定化のため絶対パスキーで登録
            key_abs: str = os.path.abspath(p)
            remote_rel_map[key_abs] = remote_rel
            entries.append(PlanEntry(path=Path(p), relpath=remote_rel, is_dir=st_is_dir))

    plan: Plan = Plan(entries=entries)
    pack_roots_abs: List[str] = [os.path.abspath(res.abs_root) for res in resolved_tokens]

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
        # ローカル絶対パス -> リモート相対の対応
        "remote_rel_map": remote_rel_map,
        # pack ルート（解決済みトークンの abs_root のみ）
        "pack_roots_abs": pack_roots_abs,
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
    remote_rel_map: Dict[str, str],
) -> PushOne:
    """
    非 pack 経路 : PlanEntry 毎の逐次 PUT。
    リモート配置は core_scatter.sftp_put_one が DEST/<local_abs_without_leading_slash> を作成。
    """
    def _push_one(_sftp: SFTPClientLike, local_path: Path, _remote_root: str, is_dir: bool) -> None:
        if is_dir:
            return
        _local_path_str: str = str(local_path)
        # ルックアップは絶対パスで
        _key_abs: str = os.path.abspath(_local_path_str)
        _remote_rel: Optional[str] = remote_rel_map.get(_key_abs)
        sftp_put_one(
            ssh,
            sftp,
            _local_path_str,
            dest_abs_root,
            host,
            _NULL_REPORT,    # Null Object (読み捨て)
            False,           # dry_run
            sudo_mkdir=(ssh_user != target_user),
            remote_rel=_remote_rel,
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
    _timeout: float, # 現状 _timeout は未使用。将来のリトライ/監視に利用する想定で受け取っている。
    host: str,
    remote_rel_map: Dict[str, str],
) -> PushOne:
    """
    pack 経路 : 最初の 1 回だけ local pack  =>  upload  =>  remote extract。
    """
    state: Dict[str, bool] = {"ran": False}

    def _push_one(_sftp: SFTPClientLike, _local_path: Path, _remote_root: str, _is_dir: bool) -> None:
        if state["ran"]:
            return
        state["ran"] = True
        src_list: List[str] = [str(p) for p in pack_srcs]
        # アーカイブ名はリモート相対配置に一致させる ( DEST 直下に再現 )
        # フォールバックは許容しない : remote_rel_map に存在しないキーは例外
        src_list_abs: List[str] = [os.path.abspath(p) for p in src_list]
        abs_to_rel: Dict[str, str] = remote_rel_map
        missing: List[str] = [a for a in src_list_abs if a not in abs_to_rel]
        if missing:
            miss_join: str = ", ".join(missing)
            raise ValueError(f"E_ARCNAME_KEY: missing remote_rel for pack roots: {miss_join}")
        # ここで '' が含まれていてよい ( src='/' の正規表現／リテラル入力に対応 )
        arc_list: List[str] = [abs_to_rel[a] for a in src_list_abs]

        # アンパック代入に直接の型注釈は不可なため, いったんタプルに受けてから展開する。
        # local_pack_paths_to_tmp の戻り値は Tuple[str, List[str]]
        tar_tuple: Tuple[str, List[str]] = local_pack_paths_to_tmp(
            src_list,
            follow_symlinks=follow_symlinks,
            arcnames=arc_list,
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

    # --pack の場合の SRC 正規化 :
    #   - 正規表現入力は壊さない ( 末尾スラッシュ付与しない )
    #   - リテラル入力については「実体がディレクトリ」のときのみ末尾スラッシュを付与
    #      ( ファイルに付けてしまうと「x.txt/」となり不正 )
    def _normalize_pack_srcs(srcs: Sequence[str]) -> List[str]:
        out: List[str] = []
        s_in: str = ""
        v: str = ""
        is_rx: bool = False
        ends_with_sep: bool = False
        exists_b: bool = False
        looks_dir: bool = False
        abs_probe: str = ""
        for s_in in srcs:
            v = str(s_in)
            # 1) 正規表現は末尾スラッシュを付与しない
            is_rx = looks_like_regex(v)
            if is_rx:
                out.append(v)
                continue
            # 2) 既に区切りで終わっていればそのまま
            ends_with_sep = bool(v.endswith("/") or v.endswith(os.sep))
            if ends_with_sep:
                out.append(v)
                continue
            # 3) リテラルとして実体が「存在」かつ「ディレクトリ」のときのみ付与
            #    - 相対は cwd 起点で評価
            #    - 例外 ( 権限・競合 ) 時は安全側 ( 付与しない )
            try:
                abs_probe = os.path.abspath(v)
                exists_b = os.path.exists(abs_probe)
                looks_dir = os.path.isdir(abs_probe) if exists_b else False
            except OSError:
                exists_b = False
                looks_dir = False
            if looks_dir:
                v = v + "/"
            out.append(v)
        return out

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

    srcs_input: List[str] = list(args.src)
    if bool(args.pack):
        srcs_input = _normalize_pack_srcs(srcs_input)

    plan_per_host: Dict[str, Plan] = {}
    meta_per_host: Dict[str, Dict[str, object]] = {}

    for host in hosts:
        plan: Plan
        meta: Dict[str, object]
        plan, meta = _build_plan_for_host(
            host=host,
            dest_remote_raw=str(args.dest),
            srcs_raw=srcs_input,
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
            plan_opt: Optional[Plan] = plan_per_host.get(host)
            total: int = len(plan_opt.entries) if plan_opt is not None else 0
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
    def _dedup_roots_for_pack_abs(abs_paths: List[str]) -> List[str]:
        """
        pack 対象の『ルート候補（abs のみ）』から**実体ベース**の最小集合を作る。
        - ルート候補は os.path.realpath()（Windows では大小無視のため casefold() も併用）で正規化した
        canon_abs を基準に親子判定を行う。
        - 親が採択された場合は子は除外する（最小集合化）。
        - 途中でより上位の親が現れた場合は、既存の子を外して親に置換する（順序に依らず最小集合）。
        """

        def _canon(p_in: str) -> str:
            p0: str = os.path.abspath(p_in)
            rp: str = os.path.realpath(p0)
            canon: str = rp.casefold() if os.name == "nt" else rp
            return canon

        # 入力の正規化（重複除去）: 同一 orig_abs の重複をまず除く
        uniq_abs: Set[str] = {os.path.abspath(p) for p in abs_paths}
        # (orig_abs, canon_abs) のペアに
        pairs: List[Tuple[str, str]] = [(p, _canon(p)) for p in uniq_abs]

        # 同一実体（canon_abs が同じ）は 1 つに代表させる（代表は安定に選ぶ）
        # 代表選択: (canon_abs に対する orig_abs の最短長, lex 小) を優先
        best_for_canon: Dict[str, str] = {}
        for orig_abs, canon_abs in pairs:
            prev: Optional[str] = best_for_canon.get(canon_abs)  # type: ignore[assignment]
            if prev is None:
                best_for_canon[canon_abs] = orig_abs
            else:
                prev_len: int = len(prev)
                cur_len: int = len(orig_abs)
                if (cur_len < prev_len) or (cur_len == prev_len and orig_abs < prev):
                    best_for_canon[canon_abs] = orig_abs

        # 採用候補を (canon 深さ, canon 長さ, canon 文字列, 代表 orig_abs) で安定ソート
        sorted_candidates: List[Tuple[int, int, str, str]] = []
        for canon_abs, rep_orig in best_for_canon.items():
            depth: int = canon_abs.count(os.sep)
            length: int = len(canon_abs)
            key_tuple: Tuple[int, int, str, str] = (depth, length, canon_abs, rep_orig)
            sorted_candidates.append(key_tuple)
        sorted_candidates.sort()

        # 最小集合化：親優先。親が見つかったら子を落とす／既存の子も取り除いて親に置換。
        kept: List[Tuple[str, str]] = []  # [(orig_abs, canon_abs)]
        for _depth, _length, canon_abs, rep_orig in sorted_candidates:
            # 既存 kept の「親／子」関係を判定
            is_child_of_existing: bool = False
            _parent_index_to_remove: List[int] = []  # 常に空（親置換は子置換の逆）だが型の明示を徹底
            _i: int
            pair: Tuple[str, str]
            for _i, pair in enumerate(kept):
                _kept_orig, kept_canon = pair
                # すでに親がある => 今回は子なのでスキップ
                common1: str = ""
                try:
                    common1 = os.path.commonpath([canon_abs, kept_canon])
                except ValueError:
                    common1 = ""
                if common1 == kept_canon and canon_abs != kept_canon:
                    is_child_of_existing = True
                    break
            if is_child_of_existing:
                continue

            # 既存 kept に「今回が親で、既存が子」のものがあれば、それらを外す
            new_kept: List[Tuple[str, str]] = []
            for pair in kept:
                _kept_orig2: str
                kept_canon2: str
                _kept_orig2, kept_canon2 = pair
                common2: str = ""
                try:
                    common2 = os.path.commonpath([canon_abs, kept_canon2])
                except ValueError:
                    common2 = ""
                if common2 == canon_abs and canon_abs != kept_canon2:
                    # 既存は子 => 捨てる（親で置換する）
                    continue
                new_kept.append(pair)
            kept = new_kept
            kept.append((rep_orig, canon_abs))

        # 出力は「元の絶対パス」の集合（ソートは入力非依存になるが安定）
        result: List[str] = [orig for (orig, _c) in kept]
        return result

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
            # 入力は「解決済みトークンの abs_root 群」のみ（entries は使わない）
            pack_roots_abs_list: List[str] = list(meta.get("pack_roots_abs", []))  # type: ignore[list-item]
            dedup_abs: List[str] = _dedup_roots_for_pack_abs(pack_roots_abs_list)
            pack_srcs: List[Path] = [Path(p) for p in dedup_abs]
            push_one_map[host] = _make_push_one_pack(
                ssh=meta["ssh"],  # type: ignore[arg-type]
                sftp=meta["sftp"],  # type: ignore[arg-type]
                pack_srcs=pack_srcs,
                dest_abs_root=str(meta["dest_abs_root"]),
                sudo_extract=bool(meta["sudo_extract"]),
                follow_symlinks=bool(meta["follow_symlinks"]),
                target_user=str(meta["target_user"]),
                selinux_mode=meta["selinux_mode"],  # type: ignore[arg-type]
                _timeout=timeout_f,
                host=host,
                remote_rel_map=meta["remote_rel_map"],  # type: ignore[arg-type]
            )
        else:
            push_one_map[host] = _make_push_one_sftp(
                ssh=meta["ssh"],  # type: ignore[arg-type]
                sftp=meta["sftp"],  # type: ignore[arg-type]
                dest_abs_root=str(meta["dest_abs_root"]),
                ssh_user=str(meta["ssh_user"]),
                target_user=str(meta["target_user"]),
                host=host,
                remote_rel_map=meta["remote_rel_map"],  # type: ignore[arg-type]
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
