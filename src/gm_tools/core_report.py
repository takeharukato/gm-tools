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

"""転送処理の結果を集約・照会するレポートユーティリティ群。

gather/scatter の計画フェーズおよび転送フェーズで発生した各項目を追跡し、
失敗のグルーピングなどレポート用途の抽出を提供する。必要に応じて Null Object
として扱える ``NullTransferReport`` も備える。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, Iterator, List, Optional


@dataclass(frozen=True)
class TransferItem:
    """転送対象の状態を保持するデータクラス。

    Attributes:
        host (str): 転送対象が属するホスト名。
        remote_path (str): リモート側の対象パス。
        phase (str): ``"plan"`` もしくは ``"transfer"`` を表すフェーズ種別。
        status (str): ``"planned"`` ``"dropped"`` ``"done"`` ``"failed"`` のいずれか。
        reason (Optional[str]): ``dropped``/``failed`` の理由。指定が無い場合は ``None``。
        local_path (Optional[str]): ローカル側パス。必要な場合のみ格納。

    Examples:
        >>> item = TransferItem(host='node1', remote_path='/var/log/app.log', phase='plan', status='planned')
        >>> item.status
        'planned'
    """

    host: str
    remote_path: str
    phase: str
    status: str
    reason: Optional[str] = None
    local_path: Optional[str] = None


@dataclass
class TransferReport:
    """転送計画および実行結果を蓄積するレポートクラス。

    Attributes:
        items (List[TransferItem]): 登録済みの転送項目一覧。

    Examples:
        >>> report = TransferReport()
        >>> report.add('node1', TransferItem('node1', '/var/log/app.log', 'plan', 'planned'))
        >>> len(report.items)
        1
    """

    items: List[TransferItem] = field(default_factory=list)  # type: ignore

    def add(self, host: str, item: TransferItem) -> None:
        """転送項目をレポートへ追加する。

        Args:
            host (str): 項目の属するホスト名。 ``item.host`` と一致していることが望ましい。
            item (TransferItem): 追加したい転送項目。

        Examples:
            >>> report = TransferReport()
            >>> transfer = TransferItem('node1', '/var/log/app.log', 'plan', 'planned')
            >>> report.add('node1', transfer)
            >>> report.items[0].remote_path
            '/var/log/app.log'
        """

        self.items.append(item)

    # ---- 抽出ユーティリティ ----
    def iter_phase(self, phase: str) -> Iterable[TransferItem]:
        """指定フェーズに一致する項目を順次返す。

        Args:
            phase (str): 取得したいフェーズ名 ( ``"plan"`` / ``"transfer"`` など )。

        Yields:
            TransferItem: 条件に一致した転送項目。

        Examples:
            >>> report = TransferReport()
            >>> report.add('node1', TransferItem('node1', '/var/log/app.log', 'plan', 'planned'))
            >>> report.add('node1', TransferItem('node1', '/var/tmp/app.log', 'transfer', 'done'))
            >>> [item.remote_path for item in report.iter_phase('plan')]
            ['/var/log/app.log']
        """

        return (it for it in self.items if it.phase == phase)

    def planned(self) -> List[TransferItem]:
        """計画フェーズで ``planned`` 状態の項目だけを取得する。

        Returns:
            List[TransferItem]: 該当する転送項目のリスト。

        Examples:
            >>> report = TransferReport()
            >>> report.add('node1', TransferItem('node1', '/var/log/a.log', 'plan', 'planned'))
            >>> report.add('node1', TransferItem('node1', '/var/log/b.log', 'plan', 'dropped'))
            >>> [item.remote_path for item in report.planned()]
            ['/var/log/a.log']
        """

        return [it for it in self.items if it.phase == "plan" and it.status == "planned"]

    def dropped(self) -> List[TransferItem]:
        """計画フェーズで ``dropped`` 状態の項目を取得する。

        Returns:
            List[TransferItem]: 該当する転送項目のリスト。

        Examples:
            >>> report = TransferReport()
            >>> report.add('node1', TransferItem('node1', '/var/log/a.log', 'plan', 'dropped'))
            >>> report.add('node1', TransferItem('node1', '/var/log/b.log', 'plan', 'planned'))
            >>> [item.remote_path for item in report.dropped()]
            ['/var/log/a.log']
        """

        return [it for it in self.items if it.phase == "plan" and it.status == "dropped"]

    def failed(self) -> List[TransferItem]:
        """転送フェーズで ``failed`` 状態の項目を取得する。

        Returns:
            List[TransferItem]: 失敗した転送項目のリスト。

        Examples:
            >>> report = TransferReport()
            >>> report.add('node1', TransferItem('node1', '/var/log/a.log', 'transfer', 'failed'))
            >>> report.add('node1', TransferItem('node1', '/var/log/b.log', 'transfer', 'done'))
            >>> [item.remote_path for item in report.failed()]
            ['/var/log/a.log']
        """

        return [it for it in self.items if it.phase == "transfer" and it.status == "failed"]

    def group_failures_by_path(self) -> Dict[str, List[str]]:
        """失敗した項目をリモートパスごとにホスト一覧へ集計する。

        Returns:
            Dict[str, List[str]]: ``remote_path`` をキーに、失敗したホストを配列でまとめた辞書。

        Examples:
            >>> report = TransferReport()
            >>> report.add('node1', TransferItem('node1', '/var/log/a.log', 'transfer', 'failed'))
            >>> report.add('node2', TransferItem('node2', '/var/log/a.log', 'transfer', 'failed'))
            >>> report.group_failures_by_path()
            {'/var/log/a.log': ['node1', 'node2']}
        """

        g: Dict[str, List[str]] = {}
        for it in self.failed():
            g.setdefault(it.remote_path, []).append(it.host)
        return g


class NullTransferReport(TransferReport):
    """記録を保持しない Null Object 実装の転送レポート。

    ログ集約を別経路に任せたい場合やメモリ使用量を抑えたい場面で利用できる。抽出系
    メソッドはすべて空の結果を返す。
    """

    # items は使わないが、親クラスとの互換性維持のため属性は保持する
    def __init__(self) -> None:
        """空の項目リストで初期化する。"""

        self.items = []  # type: ignore[assignment]

    def add(self, host: str, item: TransferItem) -> None:  # type: ignore[override]
        """項目を追加しても何も行わない。

        Args:
            host (str): 無視されるホスト名。
            item (TransferItem): 無視される転送項目。

        Examples:
            >>> report = NullTransferReport()
            >>> report.add('node1', TransferItem('node1', '/var/log/a.log', 'plan', 'planned'))
            >>> report.items
            []
        """

        return

    def iter_phase(self, phase: str) -> Iterable[TransferItem]:  # type: ignore[override]
        """指定フェーズに関係なく空のジェネレータを返す。

        Args:
            phase (str): 無視されるフェーズ名。

        Yields:
            TransferItem: 実際には要素を生成しない。

        Examples:
            >>> report = NullTransferReport()
            >>> list(report.iter_phase('plan'))
            []
        """

        def _empty() -> Iterator[TransferItem]:
            if False:
                yield  # pragma: no cover

        return _empty()

    def planned(self) -> List[TransferItem]:  # type: ignore[override]
        """常に空リストを返す。

        Returns:
            List[TransferItem]: 空リスト。
        """

        return []

    def dropped(self) -> List[TransferItem]:  # type: ignore[override]
        """常に空リストを返す。

        Returns:
            List[TransferItem]: 空リスト。
        """

        return []

    def failed(self) -> List[TransferItem]:  # type: ignore[override]
        """常に空リストを返す。

        Returns:
            List[TransferItem]: 空リスト。
        """

        return []

    def group_failures_by_path(self) -> Dict[str, List[str]]:  # type: ignore[override]
        """常に空辞書を返す。

        Returns:
            Dict[str, List[str]]: 空辞書。
        """

        return {}