# tests/tests_py/test_common_json.py
# JSON summary の統一実装
#
# Version: 1 (2025-11-16)
#
# このモジュールは Step4/5/6 runner に共通の
# JSON 形式 (schema version 1) を生成する責務を持つ。
#

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List

from test_common_config import snapshot_config

# ---------------------------------------------------------
# CaseResult dataclass
# ---------------------------------------------------------

@dataclass
class CaseResult:
    """
    個々のテストケースの結果を保持する構造体。
    runner 側ではなく、case_xxx 関数がこれを返す。
    """
    name: str
    status: str            # "passed" | "failed" | "skipped"
    reason: str = ""
    details: Dict[str, Any] = field(default_factory=dict) # type: ignore


# ---------------------------------------------------------
# Summary 生成
# ---------------------------------------------------------

def _make_timestamp() -> str:
    """ISO-8601（TZ付き）で timestamp を生成"""
    return datetime.now(timezone.utc).astimezone().isoformat()


def make_summary(
    *,
    step_number: int,
    cfg: Any,
    results: List[CaseResult],
    version: int = 1,
) -> Dict[str, Any]:
    """
    統一 JSON summary (v1) を dict として構築する。
    """

    # Config snapshot → dict 化
    # dataclass の可能性が高いため asdict() は不要
    # __dict__ の shallow copy で十分
    cfg_dict : Dict[str, Any] = snapshot_config(cfg)

    # runner 全体の summary
    summary: Dict[str, Any] = {
        "version": version,
        "timestamp": _make_timestamp(),
        "step": step_number,
        "config": cfg_dict,
        "results": [
            {
                "name": r.name,
                "status": r.status,
                "reason": r.reason,
                "details": r.details,
            }
            for r in results
        ],
    }

    return summary


# ---------------------------------------------------------
# JSON serialization & printing
# ---------------------------------------------------------

def serialize_summary(summary: Dict[str, Any]) -> str:
    """
    JSON テキストとしてエンコードする。
    ランナーはこの JSON テキストを print するだけ。
    """
    return json.dumps(
        summary,
        indent=2,
        ensure_ascii=False,
    )


def print_summary(summary: Dict[str, Any]) -> None:
    """
    標準出力に JSON summary を出す（runner 用）。
    """
    print(serialize_summary(summary))
