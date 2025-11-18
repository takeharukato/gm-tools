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
from datetime import datetime, timezone
from typing import Any, Dict, List
from ._local_types import Config, CaseResult, SummaryDict, SummaryResultEntry, ConfigSnapshot
from .test_common_config import snapshot_config


def _make_timestamp() -> str:
    """ISO-8601（TZ付き）で timestamp を生成"""
    return datetime.now(timezone.utc).astimezone().isoformat()


# ---------------------------------------------------------
# Summary 生成
# ---------------------------------------------------------
def make_summary(
    *,
    step_number: int,
    cfg: Config,
    results: List[CaseResult],
    version: int = 1,
) -> SummaryDict:
    """
    統一 JSON summary (v1) を dict として構築する。
    """

    # Config snapshot → dict 化
    # dataclass の可能性が高いため asdict() は不要
    # __dict__ の shallow copy で十分
    cfg_dict: ConfigSnapshot = snapshot_config(cfg)
    # runner 全体の summary
    summary: SummaryDict = {
        "version": version,
        "timestamp": _make_timestamp(),
        "step": step_number,
        "config": cfg_dict,
        "results": [r.to_dict() for r in results],
    }

    return summary


# ---------------------------------------------------------
# JSON serialization & printing
# ---------------------------------------------------------

def serialize_summary(summary: SummaryDict) -> str:
    """
    JSON テキストとしてエンコードする。
    ランナーはこの JSON テキストを print するだけ。
    """
    return json.dumps(
        summary,
        indent=2,
        ensure_ascii=False,
    )


def print_summary(summary: SummaryDict) -> None:
    """
    標準出力に JSON summary を出す（runner 用）。
    """
    print(serialize_summary(summary))


def write_summary_file(summary: SummaryDict, step: int, directory: str = ".") -> str:
    """
    summary_step<step>.json という固定名で JSON ファイルに書き出す。
    失敗しても例外は伝播させずパスを返す（best-effort）。
    """
    import os
    path: str = os.path.join(directory, f"summary_step{step}.json")
    try:
        txt: str = serialize_summary(summary)
        with open(path, "w", encoding="utf-8") as wf:
            wf.write(txt)
    except Exception:
        pass
    return path


def print_human_summary(summary: SummaryDict) -> None:
    """
    人間可読の英語テーブル形式で結果一覧を表示し、失敗ケースは details を JSON で全出力する。
    - Header: Step / counts
    - Table: NAME | RESULT | SKIP | REASON (reason は 80 文字で切り詰め)
    - Fail 詳細: 全 details を pretty-print
    """
    results: List[SummaryResultEntry] = summary["results"]
    step: Any = summary["step"]
    total: int = len(results)
    failed: int = sum(1 for r in results if (not r["passed"] and not r["skipped"]))
    skipped: int = sum(1 for r in results if r["skipped"])
    print(f"Step {step} Summary: {total} cases, {failed} failed, {skipped} skipped")
    names: List[str] = [r["name"] for r in results]
    max_name: int = max([len(n) for n in names], default=4)
    header: str = f"{'NAME'.ljust(max_name)}  RESULT  SKIP  REASON"
    print(header)
    print("-" * len(header))
    def _res(r: SummaryResultEntry) -> str:
        if r["skipped"]:
            return "SKIP"
        return "PASS" if r["passed"] else "FAIL"

    def _is_xskip(r: SummaryResultEntry) -> bool:
        # intended skip detection: details.xskip == True or 'xskip' token in reason
        d: Dict[str, Any] = r["details"]
        if bool(d.get("xskip")):
            return True
        reason = r["reason"].lower()
        return "xskip" in reason

    def _is_untested(r: SummaryResultEntry) -> bool:
        # untested detection: details.untested == True (forward-compatible)
        d: Dict[str, Any] = r["details"]
        return bool(d.get("untested"))

    # Counters
    total: int = len(results)
    pass_count: int = 0
    fail_count: int = 0
    skip_x_count: int = 0
    skip_unintended_count: int = 0
    untested_count: int = 0
    for r in results:
        name: str = r["name"]
        result_str: str = _res(r)
        # classify skip label
        if r["skipped"]:
            if _is_xskip(r):
                skip_label = "xskip"
                skip_x_count += 1
            else:
                skip_label = "skip"
                skip_unintended_count += 1
        else:
            skip_label = "-"
        # counts
        if _is_untested(r):
            untested_count += 1
        elif r["skipped"]:
            # already counted above
            pass
        elif r["passed"]:
            pass_count += 1
        else:
            fail_count += 1
        reason: str = r["reason"]
        if len(reason) > 80:
            reason = reason[:77] + "..."
        line: str = f"{name.ljust(max_name)}  {result_str.ljust(6)}  {skip_label.ljust(5)}  {reason}"
        print(line)
    # Executed = total - untested
    executed_count: int = total - untested_count
    if failed == 0:
        print("All tests passed.")
    # print counters at the end
    print("")
    print("Counts:")
    print(f"  Total: {total}")
    print(f"  Executed: {executed_count}")
    print(f"  Pass: {pass_count}")
    print(f"  Fail: {fail_count}")
    print(f"  Skip (unintended): {skip_unintended_count}")
    print(f"  XSkip (intended): {skip_x_count}")
    print(f"  Untested: {untested_count}")
    print("")
    print("Failed Case Details:")
    print("--------------------")
    for r in results:
        if r["passed"] or r["skipped"]:
            continue
        name = r["name"]
        reason = r["reason"]
        details = r["details"]
        print(f"--- FAIL {name}")
        print(f"Reason: {reason}")
        try:
            details_txt: str = json.dumps(details, indent=2, ensure_ascii=False)
        except Exception:
            details_txt = "(details serialization error)"
        print("Details:")
        print(details_txt)
        print("")
