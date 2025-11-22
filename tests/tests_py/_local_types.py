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
テスト実行に関わるデータ構造 ( dataclass / TypedDict ) を定義します。

Notes:
- `Config` は実行時構成, `CaseResult` はテストケース結果, `SummaryDict` はサマリのJSON表現です。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Any, TypedDict
@dataclass(frozen=True)
class LocalRun:
    """ローカル実行結果を表すコンテナ。

    Attributes:
        rc (int): プロセスの終了コード。
        stdout (str): 標準出力。
        stderr (str): 標準エラー出力。
    """
    rc: int
    stdout: str
    stderr: str

@dataclass
class CommandResult:
    """コマンド実行結果を表すコンテナ ( API/SSH 実行の戻り値など ) 。

    Attributes:
        rc (int): プロセスの終了コード。
        stdout (str): 標準出力。
        stderr (str): 標準エラー出力。
    """
    rc: int
    stdout: str
    stderr: str

@dataclass
class HostConfig:
    """ホストの SELinux 関連メタ情報。

    Attributes:
        name (str): ホスト名。
        is_selinux_supported (bool): SELinux コマンドの利用可否。
        selinux_mode (str): SELinux モード ( "Enforcing"/"Permissive"/"Disabled"/"Unknown" ) 。
    """
    name: str
    is_selinux_supported: bool
    selinux_mode: str  # "Enforcing" | "Permissive" | "Disabled" | "Unknown"

@dataclass
class Config:
    """テスト全体の構成情報を表します。

    Attributes:
        ssh_user (str): SSH 接続ユーザ。
        target_user (str): 対象操作ユーザ。
        hosts_both (List[str]): テスト対象ホスト一覧。
        host_ubuntu (str): 代表 Ubuntu ホスト。
        host_alma (str): 代表 AlmaLinux ホスト。
        ssh_port (int): SSH ポート番号。
        ssh_strict (str): StrictHostKeyChecking の設定文字列。
        ssh_strict_bool (bool): 上記のブール正規化。
        remote_dest_root (str): リモートの出力ルート。
        local_work_root (str): ローカルの作業ルート。
        local_root (str): ローカルの作業ルート ( 互換エイリアス ) 。
        gm_gather_cmd (List[str]): gather CLI コマンド ( 分割済 ) 。
        gm_scatter_cmd (List[str]): scatter CLI コマンド ( 分割済 ) 。
        verbose (bool): 冗長出力フラグ。
        parallel (int): 並列度。
    """
    ssh_user: str
    target_user: str
    hosts_both: List[str]
    host_ubuntu: str
    host_alma: str
    ssh_port: int
    ssh_strict: str
    ssh_strict_bool: bool
    remote_dest_root: str
    local_work_root: str
    local_root: str
    gm_gather_cmd: List[str]
    gm_scatter_cmd: List[str]
    verbose: bool
    parallel: int

@dataclass
class CaseResult:
    """単一テストケースの結果を表すコンテナ。

    Attributes:
        name (str): ケース名。
        passed (bool): 合格フラグ。
        skipped (bool): スキップフラグ。
        reason (str): 失敗/スキップ理由。
        details (Dict[str, Any]): 付随メタ情報。
    """
    name: str
    passed: bool
    skipped: bool = False
    reason: str = ""
    details: Dict[str, Any] = field(default_factory=dict) # type: ignore

    def to_dict(self) -> "SummaryResultEntry":
        """JSON 出力用の辞書に変換します。

        Returns:
            SummaryResultEntry: ケース結果の辞書表現。
        """
        return {
            "name": self.name,
            "passed": self.passed,
            "skipped": self.skipped,
            "reason": self.reason,
            "details": self.details,
        }


class SummaryResultEntry(TypedDict):
    """ケース結果の JSON スキーマを表す TypedDict。

    Attributes:
        name (str): ケース名。
        passed (bool): 合格フラグ。
        skipped (bool): スキップフラグ。
        reason (str): 失敗/スキップ理由。
        details (Dict[str, Any]): 付随メタ情報。
    """
    name: str
    passed: bool
    skipped: bool
    reason: str
    details: Dict[str, Any]


class ConfigSnapshot(TypedDict):
    """`Config` のスナップショット表現。シリアライズ前提の簡易形。

    Attributes:
        ssh_user (str): SSH 接続ユーザ。
        target_user (str): 対象操作ユーザ。
        hosts_both (List[str]): テスト対象ホスト一覧。
        host_ubuntu (str): 代表 Ubuntu ホスト。
        host_alma (str): 代表 AlmaLinux ホスト。
        ssh_port (int): SSH ポート番号。
        ssh_strict (str): StrictHostKeyChecking の設定文字列。
        ssh_strict_bool (bool): StrictHostKeyChecking のブール正規化。
        remote_dest_root (str): リモートの出力ルート。
        local_work_root (str): ローカルの作業ルート。
        local_root (str): ローカルの作業ルート ( 互換エイリアス ) 。
        gm_gather_cmd (List[str]): gather CLI コマンド ( 分割済 ) 。
        gm_scatter_cmd (List[str]): scatter CLI コマンド ( 分割済 ) 。
        verbose (bool): 冗長出力フラグ。
        parallel (int): 並列度。
    """
    ssh_user: str
    target_user: str
    hosts_both: List[str]
    host_ubuntu: str
    host_alma: str
    ssh_port: int
    ssh_strict: str
    ssh_strict_bool: bool
    remote_dest_root: str
    local_work_root: str
    local_root: str
    gm_gather_cmd: List[str]
    gm_scatter_cmd: List[str]
    verbose: bool
    parallel: int


class SummaryDict(TypedDict):
    """ランナー実行サマリの JSON スキーマ。

    Attributes:
        version (int): スキーマバージョン。
        timestamp (str): 実行日時。
        step (int): ステップ番号。
        config (ConfigSnapshot): 収集時の構成スナップショット。
        results (List[SummaryResultEntry]): テスト結果一覧。
    """
    version: int
    timestamp: str
    step: int
    config: ConfigSnapshot
    results: List[SummaryResultEntry]
