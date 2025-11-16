# gm-tools-tests-20251116/tests_py/test_common_runner_util.py

from __future__ import annotations

from typing import Callable, Dict, List, Sequence
import json
import traceback

from ._local_types import CaseResult

ResultDict = Dict[str, object]


def exc_repr(exc: BaseException) -> str:
    """
    例外を人間可読な文字列に変換する。
    Step6._run_case_safely で用いている形式と概ね同等の情報を含める想定。
    """
    return "".join(traceback.format_exception_only(type(exc), exc)).strip()


def run_case_safely(name: str, func: Callable[[], None]) -> CaseResult:
    """
    個々のテストケースを例外安全に実行し、CaseResult にマッピングする。
    """
    try:
        func()
        return CaseResult(
            name=name,
            passed=True,
            skipped=False,
            reason="",
            details={},
        )
    except Exception as exc:
        return CaseResult(
            name=name,
            passed=False,
            skipped=False,
            reason=type(exc).__name__,
            details={"exc": exc_repr(exc)},
        )


def case_result_to_dict(cr: CaseResult) -> ResultDict:
    """
    CaseResult を JSON 用 dict に変換する。
    Step4/5/6 が吐く results[i] のキー構造に合わせる。
    """
    return {
        "name": cr.name,
        "passed": cr.passed,
        "skipped": cr.skipped,
        "reason": cr.reason,
        "details": cr.details,
    }


def append_case_result(results: List[ResultDict], cr: CaseResult) -> None:
    """
    results リストへ 1 ケース分追加するユーティリティ。
    """
    results.append(case_result_to_dict(cr))


def print_summary(label: str, results: Sequence[ResultDict]) -> None:
    """
    STEPX SUMMARY のラベルと JSON を出力する共通ユーティリティ。
    JSON フォーマット（indent / ensure_ascii）は現状の runner と同一。
    """
    print(label)
    summary = json.dumps({"results": list(results)}, indent=2, ensure_ascii=False)
    print(summary)
