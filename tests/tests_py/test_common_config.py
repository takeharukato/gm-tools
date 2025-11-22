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
"""
環境変数からの設定生成, 設定スナップショット, 環境情報の出力ユーティリティ。

Notes:
    - `_clear_dir` は CWD 配下限定で安全に削除/再作成します。
    - `print_env` は安定化された表示形式を出力します。
"""
# gm-tools-tests-20251116/tests_py/test_common_config.py

from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path
from typing import List, Dict, Tuple, Optional

from ._local_types import Config, ConfigSnapshot
from .constants import (
    SSH_PORT_DEFAULT,
    SSH_STRICT_DEFAULT,
    REMOTE_DEST_ROOT_DEFAULT,
    LOCAL_WORK_ROOT_DEFAULT,
    SSH_USER_DEFAULT,
    TARGET_USER_DEFAULT,
    HOSTS_BOTH_DEFAULT,
    HOST_UBUNTU_DEFAULT,
    HOST_ALMA_DEFAULT,
    GM_GATHER_CMD_DEFAULT,
    GM_SCATTER_CMD_DEFAULT,
    PARALLEL_DEFAULT,
    VERBOSE_DEFAULT,
)


def _split_cmd_env(env_name: str, default: str) -> List[str]:
    """
    環境変数の値を `shlex.split` で分割し配列化します。未設定時は `default` を使用します。

    Args:
        env_name (str): 環境変数名。
        default (str): デフォルトのコマンド文字列。

    Returns:
        List[str]: 分割された引数配列。
    """
    value: str = os.environ.get(env_name, default)
    return shlex.split(value)


def _clear_dir(path_str: str) -> None:
    """
    指定ディレクトリを一度まるごと削除してから作り直します。

    Args:
        path_str (str): 対象ディレクトリのパス。
    Notes:
        - 安全装置: CWD 配下のパスのみ削除対象。シンボリックリンクは削除しません。
        - 既存がファイルの場合は削除後にディレクトリを作成します。
    """
    p: Path = Path(path_str).resolve()
    base: Path = Path(os.getcwd()).resolve()

    # base 配下以外は触らない
    try:
        p.relative_to(base)
    except ValueError:
        return

    if p.exists():
        if p.is_symlink():
            # 誤爆防止のためシンボリックリンクは削除対象外
            return
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            # ファイルだった場合はいったん削除してディレクトリを作る
            try:
                p.unlink()
            except OSError:
                pass

    p.mkdir(parents=True, exist_ok=True)


def load_config_from_env(*, clear_local_root: bool = True) -> Config:
    """
    環境変数から `Config` を構築する共通入口です。

    Args:
        clear_local_root (bool): True の場合, `local_work_root` を削除して作り直します。

    Returns:
        Config: 実行時構成オブジェクト。

    Notes:
        - False の場合, `local_work_root` の削除は行いません ( 中身を保持 ) 。
    """
    # SSH / ユーザ
    ssh_user: str = os.environ.get("SSH_USER", SSH_USER_DEFAULT)
    # TARGET_USER 未指定時は ssh_user にフォールバック
    target_user_env: str | None = os.environ.get("TARGET_USER")
    target_user: str = target_user_env if target_user_env is not None else os.environ.get(
        "TARGET_USER", ssh_user if TARGET_USER_DEFAULT == SSH_USER_DEFAULT else TARGET_USER_DEFAULT
    )

    ssh_port: int = int(os.environ.get("SSH_PORT", str(SSH_PORT_DEFAULT)))
    ssh_strict_env: str = os.environ.get("SSH_STRICT", SSH_STRICT_DEFAULT)

    # パス
    remote_dest_root: str = os.environ.get("REMOTE_DEST_ROOT", REMOTE_DEST_ROOT_DEFAULT)
    local_work_root: str = os.environ.get("LOCAL_WORK_ROOT", LOCAL_WORK_ROOT_DEFAULT)

    # clear_local_root 指定に応じてローカル作業ディレクトリを初期化
    if clear_local_root:
        _clear_dir(local_work_root)

    # ホスト群
    hosts_both_raw: str = os.environ.get("HOSTS_BOTH", HOSTS_BOTH_DEFAULT)
    hosts_both_list: List[str] = shlex.split(hosts_both_raw)
    hosts_both: List[str] = [h for h in hosts_both_list if h]

    host_ubuntu: str = os.environ.get("HOST_UBUNTU", HOST_UBUNTU_DEFAULT)
    host_alma: str = os.environ.get("HOST_ALMA", HOST_ALMA_DEFAULT)

    # gm-gather / gm-scatter
    gm_gather_cmd: List[str] = _split_cmd_env("GM_GATHER_CMD", GM_GATHER_CMD_DEFAULT)
    gm_scatter_cmd: List[str] = _split_cmd_env("GM_SCATTER_CMD", GM_SCATTER_CMD_DEFAULT)

    # 挙動
    parallel: int = int(os.environ.get("PARALLEL", str(PARALLEL_DEFAULT)))
    verbose_env: str = os.environ.get("VERBOSE", "1" if VERBOSE_DEFAULT else "0")
    verbose: bool = (verbose_env == "1")

    cfg: Config = Config(
        ssh_user=ssh_user,
        target_user=target_user,
        hosts_both=hosts_both,
        host_ubuntu=host_ubuntu,
        host_alma=host_alma,
        ssh_port=ssh_port,
        ssh_strict=ssh_strict_env,
        ssh_strict_bool=(ssh_strict_env.lower() in ["yes", "true", "1"]),
        remote_dest_root=remote_dest_root,
        local_work_root=local_work_root,
        local_root=local_work_root,
        gm_gather_cmd=gm_gather_cmd,
        gm_scatter_cmd=gm_scatter_cmd,
        verbose=verbose,
        parallel=parallel,
    )

    return cfg

def snapshot_config(cfg: Config) -> ConfigSnapshot:
    """
    `Config` を JSON 互換の `ConfigSnapshot` にスナップショットします。

    Args:
        cfg (Config): 変換対象の構成。

    Returns:
        ConfigSnapshot: スナップショット辞書。

    Notes:
        - list/dict フィールドは shallow copy を行います。
        - `Config` にフィールドが増えた場合はここをメンテナンスします。
    """
    result: ConfigSnapshot = {
        "ssh_user": cfg.ssh_user,
        "target_user": cfg.target_user,
        "hosts_both": list(cfg.hosts_both),
        "host_ubuntu": cfg.host_ubuntu,
        "host_alma": cfg.host_alma,
        "ssh_port": cfg.ssh_port,
        "ssh_strict": cfg.ssh_strict,
        "ssh_strict_bool": cfg.ssh_strict_bool,
        "remote_dest_root": cfg.remote_dest_root,
        "local_work_root": cfg.local_work_root,
        "local_root": cfg.local_root,
        "gm_gather_cmd": cfg.gm_gather_cmd,
        "gm_scatter_cmd": cfg.gm_scatter_cmd,
        "verbose": cfg.verbose,
        "parallel": cfg.parallel,
    }
    return result


def resolve_parallel_pair_from_env() -> Tuple[int, int]:
    """
    並列度を環境変数から解決します。

    Returns:
        Tuple[int, int]: `(j1, j2)` の並列度ペア。`GM_PARALLEL` 設定時は `(1, GM_PARALLEL)`, 未設定時は `(1, 4)`。
    """
    gm_par_raw: str = os.environ.get("GM_PARALLEL", "").strip()
    if gm_par_raw:
        j2: int = int(gm_par_raw)
        j1: int = 1
        return j1, j2
    j1_default: int = 1
    j2_default: int = 4
    return j1_default, j2_default


def print_env(cfg: Config, *, extra: Optional[Dict[str, str]] = None) -> None:
    """
    環境情報を安定した形式で出力します ( Step5 仕様に準拠 ) 。

    Args:
        cfg (Config): 実行時構成。
        extra (Optional[Dict[str, str]]): 追加出力する key/value ( 辞書順で出力 )  。
    """
    j1, j2 = resolve_parallel_pair_from_env()
    msg1 = f"[env] SSH_USER={cfg.ssh_user} HOSTS_BOTH={' '.join(cfg.hosts_both)} PARALLEL={j1}/{j2}"
    msg2 = f"[env] GM_GATHER_CMD='{shlex.join(cfg.gm_gather_cmd)}'"
    msg3 = f"[env] GM_SCATTER_CMD='{shlex.join(cfg.gm_scatter_cmd)}'"
    print(msg1)
    print(msg2)
    print(msg3)
    if extra:
        for k in sorted(extra.keys()):
            v = extra[k]
            print(f"[env] {k}={v}")
