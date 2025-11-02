from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional


@dataclass(frozen=True)
class TransferItem:
    """
    転送対象ファイルのトラッキング単位
    phase  : "plan" | "transfer"
    status : "planned" | "dropped" | "done" | "failed"
    reason : dropped/failed の理由（任意）
    """
    host: str
    remote_path: str
    phase: str
    status: str
    reason: Optional[str] = None
    local_path: Optional[str] = None


@dataclass
class TransferReport:
    """
    実行全体のレポート（計画～実行までの項目を保持）
    """
    items: List[TransferItem] = field(default_factory=list)  # type: ignore

    def add(self, host: str, item: TransferItem) -> None:
        self.items.append(item)

    # ---- 抽出ユーティリティ ----
    def iter_phase(self, phase: str) -> Iterable[TransferItem]:
        return (it for it in self.items if it.phase == phase)

    def planned(self) -> List[TransferItem]:
        return [it for it in self.items if it.phase == "plan" and it.status == "planned"]

    def dropped(self) -> List[TransferItem]:
        return [it for it in self.items if it.phase == "plan" and it.status == "dropped"]

    def failed(self) -> List[TransferItem]:
        return [it for it in self.items if it.phase == "transfer" and it.status == "failed"]

    def group_failures_by_path(self) -> Dict[str, List[str]]:
        """
        失敗を remote_path ごとにホスト配列へ集計
        """
        g: Dict[str, List[str]] = {}
        for it in self.failed():
            g.setdefault(it.remote_path, []).append(it.host)
        return g
