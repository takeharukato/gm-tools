# -*- mode: python; coding: utf-8; line-endings: unix -*-
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2025 TAKEHARU KATO
#
# This file is distributed under the two-clause BSD license.
# For the full text of the license, see the LICENSE file in the project root directory.
# このファイルは2条項BSDライセンスの下で配布されています。
# ライセンス全文はプロジェクト直下の LICENSE を参照してください。
#
# OpenAI's ChatGPT partially generated this code.
# Author has modified some parts.
# OpenAIのChatGPTがこのコードの一部を生成しました。
# 著者が修正している部分があります。

"""gm-scatter CLI の制御フローと補助処理をまとめたモジュールである。

ローカルファイルを複数ホストへ展開するための引数解析, 計画生成,
SFTP/pack 経路の転送ファクトリ, GracefulStop を用いた協調停止などを提供する。

Examples:
    >>> from gm_tools import scatter_cli  # doctest: +SKIP
    >>> hasattr(scatter_cli, "main")  # doctest: +SKIP
    True  # doctest: +SKIP
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import logging
import shutil
from argparse import BooleanOptionalAction, Namespace
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Final, Sequence, Set
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # 型チェッカー向けのダミー定義 ( 実行時には評価されない )
    from gettext import gettext as _

from .core_common import get_host_list_from_hostfile
from .core_report import NullTransferReport
from .core_constants import (
    DEFAULT_HOSTS_FILE,
    DEFAULT_PARALLEL_HOSTS,
    EXIT_ERR_ARGS,
    EXIT_ERR_NO_HOSTS,
    EXIT_OK,
)
from .core_cli_support import validate_cli_positional_args
from .core_logging import HostLogAggregator, init_logging, shutdown_logging
from .core_path_handling import (
    is_local_abs,  # type: ignore[unused-ignore] 使わないが将来の整合のため保持
    is_windows_abs,
    is_bare_tilde,
    tilde_username,
    # 相対/絶対 SRC の正規化とレイアウト算出に利用
    ScatterSrcToken,
    ScatterResolvedToken,
    resolve_token_for_scatter,
    scatter_expand_tilde_for_exec_user,
    normalize_rel_for_dest,
    split_src_to_root_and_tail_regex,
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
from .core_i18n import setup_gettext
from .core_signal_handling import (
    GracefulStop,
    register_signal_handlers,
)

# Null Object: 読み捨て用のレポートシンク
# None は使わず常に TransferReport を渡す
_NULL_REPORT: Final[NullTransferReport] = NullTransferReport()

def build_parser() -> argparse.ArgumentParser:
    """gm-scatter 用の引数パーサを構築する。

    Returns:
        argparse.ArgumentParser: コマンドライン引数を解析する ``ArgumentParser``。

    Examples:
        >>> parser = build_parser()  # doctest: +SKIP
        >>> isinstance(parser, argparse.ArgumentParser)  # doctest: +SKIP
        True  # doctest: +SKIP
    """
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        prog="gm-scatter",
        description=_("gm-scatter: upload local files to remote DEST.\n"
        "Usage: gm-scatter [SRC ...] DEST\n"
        "  - SRC:\n"
        "      * '/...': absolute on local (UNIX)\n"
        "      * 'X:/...': absolute on local (Windows)\n"
        "      * '~/...': expanded by current user's HOME on local\n"
        "      * RELATIVE: relative path from current directory\n"
        "    The portion after the root is treated as a regex path.\n"
        "    e.g., '/etc/hosts' (literal), '/var/log/.*\\.log' (regex), '~/foo/.*', 'var/log/.*'.\n"
        "  - DEST: remote directory where files are stored as DEST/...\n"
        "Remote layout: DEST/<rel>/...  where rel =\n"
        "  - for absolute SRC: <local_abs_without_leading_slash>\n"
        "  - for relative SRC: the original relative path"),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    # 位置引数: SRC ... DEST
    parser.add_argument("src", nargs="+", help=_("Local SRC paths (abs or rel)"))
    parser.add_argument("dest", help=_("Remote DEST ( supports /abs, ~/, and relative-from-remote-home )"))

    # SSH
    parser.add_argument(
        "-H", "--hosts", default=DEFAULT_HOSTS_FILE, help=_("Hosts file. Default: %(default)s.")
    )
    parser.add_argument(
        "-u", "--user", default=getpass.getuser(), help=_("Target account semantics on remote.")
    )
    parser.add_argument(
        "-s", "--ssh-user", default=None, help=_("SSH login user. Default: same as --user.")
    )
    parser.add_argument(
        "-P", "--port", type=int, default=DEFAULT_SSH_PORT, help=_("SSH port. Default: %(default)s.")
    )
    parser.add_argument("-K", "--key", default=None, help=_("SSH private key file."))
    parser.add_argument("-W", "--password", default=None, help=_("SSH password (not recommended)."))
    parser.add_argument(
        "-T",
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=_("SSH/command timeout seconds. Default: %(default)s."),
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
        help=_("Parallel hosts (not parallel per-host). Default: %(default)s."),
    )
    parser.add_argument("-n", "--dry-run", action="store_true", help=_("Show plan only; do not upload."))
    parser.add_argument("-v", "--verbose", action="store_true", help=_("Verbose logs."))
    parser.add_argument("--pack", action="store_true", help=_("Pack locally (tar.gz) then extract remotely."))
    parser.add_argument("--follow-symlinks", action="store_true", help=_("When packing, dereference symlinks."))

    # tri-state: True (--sudo-extract), False (--no-sudo-extract), None (auto)
    parser.add_argument(
        "-x",
        "--sudo-extract",
        action=BooleanOptionalAction,
        default=None,
        help=_("Force sudo for remote mkdir/extract when packing (use --no-sudo-extract to force off). Omitted = auto."),
    )
    # SELinux は pack 経路のみで使用
    parser.add_argument(
        "--selinux",
        choices=["auto", "policy", "ignore"],
        default="auto",
        help=_("SELinux label restore policy (pack path only). Default: %(default)s."),
    )
    return parser


def _resolve_remote_dest(dest_raw: str, remote_home: str) -> Tuple[str, Optional[str]]:
    """DEST トークンを scatter 仕様に沿って絶対パスへ解決する。

    - ``/`` で始まる場合はそのまま絶対パスとして扱う。
    - Windows 形式 ``X:/`` または ``X:\\`` で始まる場合も絶対パスとして返す。
    - ``~/`` で始まる場合は ``remote_home`` へ連結する。
    - 素の ``~`` はサポート外としてエラーメッセージを返す。
    - ``~user`` 形式もサポート外としてエラーメッセージを返す。
    - 上記に該当しない場合は ``remote_home`` からの相対パスとして扱う。

    Args:
        dest_raw (str): CLI で受け取った DEST 文字列。
        remote_home (str): リモートユーザーのホームディレクトリ絶対パス。

    Returns:
        Tuple[str, Optional[str]]: ``(絶対パス, エラーメッセージまたはNone)`` の組。

    Examples:
        >>> _resolve_remote_dest('/srv/data', '/home/demo')
        ('/srv/data', None)
        >>> _resolve_remote_dest('~/logs', '/home/demo')
        ('/home/demo/logs', None)
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
        return "", _("tilde with username is not supported")
    if is_bare_tilde(d):
        # 素の "~" は非対応
        return "", _("bare tilde is not allowed")
    if d.startswith("~/"):
        tail: str = d[1:].lstrip("/\\")
        return (remote_home if not tail else f"{remote_home}/{tail}"), None
    # 相対
    return f"{remote_home}/{d}", None

def _rel_base_from_abs_for_dest(base_abs: str) -> str:
    """正規表現 SRC からリモート相対基点を導出する。

    解決済みの絶対パス ``base_abs`` から先頭の区切りと区切り文字種を統一し,
    Windows のドライブレターは保持したまま, リモート配置規則 ``DEST/<rel>/...`` に適合する
    相対パス ``<rel>`` を返す。
    主な変換例は次のとおり:

    - ``/foo/bar`` は ``"foo/bar"`` へ変換する。
    - ``\\foo\\bar`` は ``"foo/bar"`` へ変換する。
    - ``C:\\foo\\bar`` は ``"C:/foo/bar"`` へ変換する。

    Args:
        base_abs (str): 正規表現展開後の絶対パス。

    Returns:
        str: リモート配置用の正規化された相対パス。

    Examples:
        >>> _rel_base_from_abs_for_dest('/foo/bar')
        'foo/bar'
        >>> _rel_base_from_abs_for_dest('C:/foo/bar')
        'C:/foo/bar'
    """
    base_abs_str: str = str(base_abs)
    normalized: str = base_abs_str.replace("\\", "/")
    # 先頭のスラッシュ 1 つだけを除去 ( Windows ドライブレターは残す )
    stripped: str = normalized[1:] if normalized.startswith("/") else normalized
    rel_base: str = normalize_rel_for_dest(stripped)
    return rel_base


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
    """単一ホスト向けの転送計画(``Plan``)とメタ情報を構築する。

    Args:
        host (str): 対象ホスト名。
        dest_remote_raw (str): CLI から受け取った DEST トークン。
        srcs_raw (List[str]): CLI で受け取った SRC トークン列。
        ssh_user (str): SSH 接続に利用するユーザー名。
        target_user (str): 転送先の所有者として想定するユーザー名。
        port (int): SSH ポート番号。
        key (Optional[str]): 秘密鍵ファイルパス。
        password (Optional[str]): パスワード認証文字列。
        timeout (float): SSH/SFTP 操作のタイムアウト秒数。
        strict (bool): ホストキー検証を厳格化する場合は ``True``。
        pack (bool): ``--pack`` 指定時は ``True``。
        follow_symlinks (bool): ``--follow-symlinks`` 指定時は ``True``。
        sudo_extract_flag (Optional[bool]): ``--sudo-extract`` の明示指定。``None`` は自動判定。
        selinux_mode (SelinuxMode): ``--selinux`` オプションで選択されたモード。
        verbose (bool): 詳細ログを有効化する場合は ``True``。

    Returns:
        Tuple[Plan, Dict[str, object]]: 構築した転送計画(``Plan``)と付随メタ情報の組。

    Raises:
        SystemExit: DEST/SRC の検証に失敗した場合。

    Examples:
        >>> plan, meta = _build_plan_for_host(  # doctest: +SKIP
        ...     host='example',
        ...     dest_remote_raw='/remote/dest',
        ...     srcs_raw=['/tmp/data.txt'],
        ...     ssh_user='demo',
        ...     target_user='demo',
        ...     port=22,
        ...     key=None,
        ...     password=None,
        ...     timeout=30.0,
        ...     strict=False,
        ...     pack=False,
        ...     follow_symlinks=False,
        ...     sudo_extract_flag=None,
        ...     selinux_mode='auto',
        ...     verbose=False,
        ... )
        >>> isinstance(plan.entries, list)  # doctest: +SKIP
        True  # doctest: +SKIP
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
        print(_(dest_err), file=sys.stderr)
        raise SystemExit(EXIT_ERR_ARGS)

    # sudo-extract ( 三値 None=auto ) : auto は (--pack かつ ssh_user != target_user) で True
    auto_sudo: bool = bool(pack) and (ssh_user != target_user)
    sudo_extract: bool = auto_sudo if (sudo_extract_flag is None) else bool(sudo_extract_flag)

    # NOTE: validate_cli_positional_args() で tilde 系の禁止入力は事前に排除済み。
    #       ここでは正規化済みの srcs_raw をそのまま利用する。

    # SRC を scatter 仕様に基づいて解決 ( ~/ は実行ユーザ HOME 展開, 相対は cwd 起点 )
    # 元トークン (ScatterSrcToken) と解決結果 (ScatterResolvedToken) をペアで保持する
    tokens_and_resolved: List[Tuple[ScatterSrcToken, ScatterResolvedToken]] = []
    s_in: str
    tok: ScatterSrcToken
    res: ScatterResolvedToken
    for s_in in srcs_raw:
        tok = ScatterSrcToken(raw=str(s_in))
        res = resolve_token_for_scatter(tok, cwd=os.getcwd())
        tokens_and_resolved.append((tok, res))

    # 候補列挙とリモート配置 rel の算出
    #  - 絶対 SRC: rel_root は <local_abs_without_leading_slash>
    #  - 相対 SRC: rel_root は <指定された相対パス> ( 正規化済 )
    entries: List[PlanEntry] = []
    remote_rel_map: Dict[str, str] = {}
    pack_roots_abs_local: List[str] = []
    pair: Tuple[ScatterSrcToken, ScatterResolvedToken]
    for pair in tokens_and_resolved:
        tok = pair[0]
        res = pair[1]

        # 列挙に使うトークンと「相対計算の起点(base_abs)」を決定
        token_for_enum: str = scatter_expand_tilde_for_exec_user(tok.raw)
        is_regex: bool = looks_like_regex(token_for_enum.replace("\\", "/"))

        # 正規表現なら split_src_to_root_and_tail_regex で root を起点にする
        if is_regex:
            abs_norm: str = os.path.abspath(token_for_enum)
            base_split: Tuple[str, Optional[str]] = split_src_to_root_and_tail_regex(abs_norm)
            base_abs: str = base_split[0]
            _tail_re: Optional[str] = base_split[1]
        else:
            base_abs: str = os.path.abspath(res.abs_root)

        # rel_root の決定:
        #   - 正規表現 SRC: base_abs から算出 ( 正規表現テキストを含めない )
        #   - リテラル SRC: 従来どおり resolve_token_for_scatter の res.rel_root を使用
        rel_base: str = _rel_base_from_abs_for_dest(base_abs) if is_regex else str(res.rel_root)

        # pack ルートが候補列挙に出ない実装に備えて, 先にマップだけは保証しておく ( 起点で登録 )
        remote_rel_map[base_abs] = rel_base
        pack_roots_abs_local.append(base_abs)
        cand_list: List[str] = list(enumerate_candidates_local([token_for_enum]))
        p: str
        for p in cand_list:
            st_is_dir: bool = os.path.isdir(p)
            if (not pack) and st_is_dir:
                continue
            # inner_rel: abs_root からの相対。ルート自身は追加なし ( =空 ) 。
            try:
                inner_rel_raw: str = os.path.relpath(p, start=base_abs)
            except ValueError:
                inner_rel_raw = ""
            if inner_rel_raw in (".", "\\", ""):
                inner_rel_raw = ""

            inner_rel: str = normalize_rel_for_dest(inner_rel_raw)
            remote_rel: str = (
                rel_base if not inner_rel else normalize_rel_for_dest(f"{rel_base}/{inner_rel}")
            )
            # ルックアップの安定化のため絶対パスキーで登録
            key_abs: str = os.path.abspath(p)
            remote_rel_map[key_abs] = remote_rel
            entries.append(PlanEntry(path=Path(p), relpath=remote_rel, is_dir=st_is_dir))

    plan: Plan = Plan(entries=entries)
    # --pack 用のルートは, 正規表現なら split で得た root, リテラルなら従来の abs を使用
    pack_roots_abs: List[str] = [os.path.abspath(p) for p in pack_roots_abs_local]

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
        # pack ルート ( 解決済みトークンの abs_root のみ )
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
    """SFTP 経由で単一ファイルを逐次アップロードするクロージャを生成する。

    Args:
        ssh (SSHClientLike): リモート側で ``mkdir`` や ``chown`` を実行する SSH クライアント。
        sftp (SFTPClientLike): 転送に利用する SFTP クライアント。
        dest_abs_root (str): リモート DEST の絶対パス。
        ssh_user (str): SSH 接続に使用するユーザー名。
        target_user (str): 転送先の所有者として想定するユーザー名。
        host (str): ログ出力で使用するホスト名。
        remote_rel_map (Dict[str, str]): ローカル絶対パスとリモート相対パスの対応表。

    Returns:
        PushOne: 転送対象一件を表すデータ要素(``PlanEntry``) を受け取り, SFTP で 1 件ずつアップロードするクロージャ。

    Examples:
        >>> from unittest.mock import MagicMock  # doctest: +SKIP
        >>> push_one = _make_push_one_sftp(  # doctest: +SKIP
        ...     ssh=MagicMock(),
        ...     sftp=MagicMock(),
        ...     dest_abs_root='/remote/dest',
        ...     ssh_user='demo',
        ...     target_user='demo',
        ...     host='example',
        ...     remote_rel_map={},
        ... )
        >>> callable(push_one)  # doctest: +SKIP
        True  # doctest: +SKIP
    """
    def _push_one(_sftp: SFTPClientLike, local_path: Path, _remote_root: str, is_dir: bool) -> None:
        """ローカルファイル 1 件をリモートホストへアップロードする。

        Args:
            _sftp (SFTPClientLike): SFTP PUT を発行するクライアント。
            local_path (Path): アップロード対象のローカル絶対パス。
            _remote_root (str): 呼び出し側が提示するリモートルート ( 非 pack 経路では未使用 ) 。
            is_dir (bool): 対象がディレクトリであれば ``True``。

        Returns:
            None: 返り値はありません。

        Examples:
            >>> from pathlib import Path  # doctest: +SKIP
            >>> isinstance(Path('/tmp/example'), Path)  # doctest: +SKIP
            True  # doctest: +SKIP
        """
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
    """``--pack`` オプション指定時に利用する ホストごとに1 度だけ実行されるアップロード用のクロージャを生成する。
        生成したクロージャは以下の処理を行う。

        1. ローカルホストでアーカイブを作成する。
        2. SFTP 経由でリモートホストにアーカイブをアップロードする。
        3. リモートホストでアーカイブを解凍し, アーカイブ解凍の成否に依らず, リモートに残ったアーカイブファイルを削除する。
        4. アップロードに利用したローカルの一時アーカイブと一時ディレクトリを削除する。

    Args:
        ssh (SSHClientLike): リモートで展開処理を実行する SSH クライアント。
        sftp (SFTPClientLike): アーカイブファイルを送信する SFTP クライアント。
        pack_srcs (List[Path]): パック対象となるローカルファイルのパスのリスト。
        dest_abs_root (str): リモート DEST の絶対パス。
        sudo_extract (bool): リモート解凍時に ``sudo`` を利用する場合は ``True``。
        follow_symlinks (bool): パック時にシンボリックリンクを辿る場合は ``True``。
        target_user (str): 転送先ファイルの所有者として想定するユーザー名。
        selinux_mode (SelinuxMode): SELinux ラベル復元モード。
        _timeout (float): 将来のリトライ制御に備えたタイムアウト値。
        host (str): ログで使用するホスト名。
        remote_rel_map (Dict[str, str]): パック対象絶対パスからリモート相対パスへの対応。

    Returns:
        PushOne: 転送対象一件を表すデータ要素(``PlanEntry``) の最初の呼び出しで pack/upload/extract を実行するクロージャ。

    Examples:
        >>> from unittest.mock import MagicMock  # doctest: +SKIP
        >>> push_one = _make_push_one_pack(  # doctest: +SKIP
        ...     ssh=MagicMock(),
        ...     sftp=MagicMock(),
        ...     pack_srcs=[],
        ...     dest_abs_root='/remote/dest',
        ...     sudo_extract=False,
        ...     follow_symlinks=False,
        ...     target_user='demo',
        ...     selinux_mode='auto',
        ...     _timeout=30.0,
        ...     host='example',
        ...     remote_rel_map={},
        ... )
        >>> callable(push_one)  # doctest: +SKIP
        True  # doctest: +SKIP
    """
    state: Dict[str, bool] = {"ran": False}

    def _push_one(_sftp: SFTPClientLike, _local_path: Path, _remote_root: str, _is_dir: bool) -> None:
        """--pack オプション指定時に 1 度だけアーカイブ転送と解凍を実行する。

        Args:
            _sftp (SFTPClientLike): アーカイブファイルを送信するクライアント。
            _local_path (Path): 転送対象一件を表すデータ要素(``PlanEntry``) が持つローカルパス ( pack 経路では未使用 ) 。
            _remote_root (str): 転送対象一件を表すデータ要素(``PlanEntry``) が持つリモートルート ( 未使用 ) 。
            _is_dir (bool): エントリがディレクトリかどうか ( 未使用 ) 。

        Returns:
            None: 返り値はありません。

        Raises:
            ValueError: ``remote_rel_map`` に pack ルートのキーが存在しない場合。

        Examples:
            >>> state = {'ran': False}  # doctest: +SKIP
            >>> bool(state['ran'])  # doctest: +SKIP
            False  # doctest: +SKIP
        """
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
        tar_tmp_dir: str = os.path.dirname(tar_path)
        try:
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
        finally:
            try:
                os.remove(tar_path)
            except FileNotFoundError:
                pass
            except OSError:
                pass
            # temp ディレクトリにはアーカイブ以外存在しない想定のため丸ごと削除
            shutil.rmtree(tar_tmp_dir, ignore_errors=True)

    return _push_one


def main() -> None:
    """gm-scatter CLI のエントリーポイントを実装する。

    Returns:
        None: 正常終了時は ``EXIT_OK`` コードでプロセスを終了する。

    Raises:
        SystemExit: 引数検証エラーやホスト未検出時に適切な終了コードで終了する。

    Examples:
        >>> import sys  # doctest: +SKIP
        >>> if '--help' in sys.argv:  # doctest: +SKIP
        ...     pass  # CLI 実行では ``main()`` を呼び出す。
    """

    def _normalize_pack_srcs(srcs: Sequence[str]) -> List[str]:
        """pack 用 SRC を安全な表記へ揃える。

        安全な表記は次の手順で決定する。

        1. 正規表現入力であると判定した場合は一切変更せず (末尾スラッシュも付与せず)そのまま返す。
        2. 末尾がすでに区切り文字 (  ``/`` または ``os.sep``  ) で終わる入力もそのまま返す。
        3. リテラル入力で実体を確認できる場合のみ, 実体がディレクトリであれば末尾に ``/`` を付与する。
           例外や権限エラーなどで確認できない場合は安全側として付与しない。

        これにより正規表現を壊さず, リテラルディレクトリのみを明示的に指定できる安全な SRC 表記を保つ。

        Args:
            srcs (Sequence[str]): CLI から受け取った元の SRC トークン列。

        Returns:
            List[str]: pack 処理に適した SRC 表記のリスト。

        Examples:
            >>> _normalize_pack_srcs(['foo', 'bar/'])  # doctest: +SKIP
            ['foo/', 'bar/']  # doctest: +SKIP
        """
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

    # 1. 国際化初期化
    setup_gettext()

    # 2. 引数解析
    parser: argparse.ArgumentParser = build_parser()
    args: Namespace = parser.parse_args()

    validation = validate_cli_positional_args(
        src_tokens=args.src,
        dest_token=args.dest,
        allow_src_bare_tilde=False,
        allow_src_tilde_username=False,
        allow_dest_bare_tilde=False,
        allow_dest_tilde_username=False,
        exit_code_default=EXIT_ERR_ARGS,
    )
    if validation.has_error():
        if validation.error_message:
            print(validation.error_message, file=sys.stderr)
        sys.exit(validation.exit_code)

    args.src = validation.normalized_srcs
    args.dest = validation.normalized_dest

    # ホストファイル解析
    try:
        hosts: List[str] = get_host_list_from_hostfile(str(args.hosts))

    except FileNotFoundError:
        # ホストファイルが存在しない場合
        sys.exit(EXIT_ERR_ARGS)
    except OSError:
        # ホストファイルの読み取りに失敗した場合
        sys.exit(EXIT_ERR_ARGS)
    except ValueError:
        # ホストファイル内にホスト名が記載されていない場合
        sys.exit(EXIT_ERR_NO_HOSTS)

    finally:
        pass

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
        """ホスト名に対応する SSH 接続を返す。

        Args:
            host (str): 接続済みホスト名。

        Returns:
            SSHClientLike: 事前に ``_build_plan_for_host`` で確立した SSH クライアント。
        """
        return meta_per_host[host]["ssh"]  # type: ignore[return-value]

    def _open_sftp(ssh: SSHClientLike) -> SFTPClientLike:
        """SSH クライアントに紐付く SFTP 接続を返す。

        Args:
            ssh (SSHClientLike): 取得元となる SSH クライアント。

        Returns:
            SFTPClientLike: ``ssh`` と同じ接続から生成した SFTP クライアント。
        """
        for _h, m in meta_per_host.items():
            if m["ssh"] is ssh:
                return m["sftp"]  # type: ignore[return-value]
        # fallback ( 通常到達しない )
        return list(meta_per_host.values())[0]["sftp"]  # type: ignore[index, return-value]

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

            # 重要: pack 入力は「ベースディレクトリ群」ではなく
            #       「Plan.entries の実マッチ結果 ( ファイル/ディレクトリ ) 群」を用いる。
            #       これにより正規表現でヒットした個々のエントリが確実にアーカイブへ入る。
            plan_for_host: Optional[Plan] = plan_per_host.get(host)
            if plan_for_host is None:
                plan_for_host = Plan(entries=[])

            # Plan.entries から絶対パス ( 順序保持・重複排除 ) を作成
            entry_abs_list: List[str] = []
            seen_abs: Set[str] = set()
            e: PlanEntry
            p_abs: str
            for e in plan_for_host.entries:
                p_abs = os.path.abspath(str(e.path))
                if p_abs in seen_abs:
                    continue
                seen_abs.add(p_abs)
                entry_abs_list.append(p_abs)

            pack_srcs: List[Path] = [Path(p) for p in entry_abs_list]

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

    # 並行実行: DEST/<local_abs_without_leading_slash> へ配置
    gs: GracefulStop = GracefulStop()
    register_signal_handlers(gs)
    exit_code: int = run_parallel(
        hosts=hosts,
        plan_per_host=plan_per_host,
        remote_root=str(args.dest), # 実質未使用: push_one_map がすべての転送先のルートを保持する
        src_root=Path("."),  # 未使用 ( PlanEntry.path を直接参照 )
        parallel=max(1, int(args.parallel)),
        verbose=bool(args.verbose),
        abort_event=None,  # run_parallel 側で GracefulStop を利用
        open_ssh=_open_ssh,       # type: SSHFactory
        open_sftp=_open_sftp,     # type: SFTPFactory
        push_one=lambda s, lp, rr, d: None,  # 既定は未使用 ( push_one_map を利用 )
        push_one_map=push_one_map,
        join_host_dir=False,      # HOST サブディレクトリは作らない
        remote_removers=None,
        do_cleanup_local=False,
        do_cleanup_remote=False,
        graceful_stop=gs,
        register_signals=False,
    )


    # ユーザ向けの中断メッセージ
    if gs.abort_event.is_set():
        logger: logging.Logger = logging.getLogger("gm_tools.scatter_cli")
        logger.warning(
            _(
                "Interrupt requested; cancelling remaining transfers "
                "(some remote hosts may see partial files or directories)."
            )
        )

    sys.exit(exit_code)

if __name__ == "__main__":
    finalize_sockets()
    main()
