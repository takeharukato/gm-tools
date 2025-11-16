# tests/tests_py/test_common_runner.py
# Step7: Common runner framework
#
# - Step4/Step5/Step6 runner の共通処理
# - case_xxx の例外捕捉
# - CaseResult 化
# - cleanup の呼び出し
# - JSON summary 出力
#

from __future__ import annotations

from typing import Any, Callable, Dict, List, Tuple

from .test_common_json import CaseResult, make_summary, print_summary
from .test_common_cleanup import cleanup_test_temp

def _run_case_safely(
    case_name: str,
    cfg: Any,
    case_func: Callable[[Any], CaseResult],
) -> CaseResult:
    """
    テストケースを安全に実行し、例外も CaseResult として返す。
    """
    try:
        result = case_func(cfg)
        # case_func が CaseResult を返さなかった場合の保険
        if not isinstance(result, CaseResult): # type: ignore
            return CaseResult(
                name=case_name,
                status="failed",
                reason=f"case returned non-CaseResult: {type(result)!r}",
                details={},
            )
        return result

    except Exception as e:
        # 例外 → failed として CaseResult を構築
        return CaseResult(
            name=case_name,
            status="failed",
            reason=f"case raised exception: {e!r}",
            details={"exception_repr": repr(e)},
        )


def run_cases(
    *,
    step_number: int,
    cfg: Any,
    cases: List[Tuple[str, Callable[[Any], CaseResult]]],
) -> Dict[str, Any]:
    """
    Step4/5/6 runner の共通処理：

    - 各ケースの安全実行
    - cleanup 実行
    - JSON summary の生成
    - summary dict を返す（print は外部から可能）
    """
    results: List[CaseResult] = []

    for case_name, case_func in cases:
        r = _run_case_safely(case_name, cfg, case_func)
        results.append(r)

    # summary を生成
    summary = make_summary(
        step_number=step_number,
        cfg=cfg,
        results=results,
    )

    # cleanup（Step4/5/6 共通）
    cleanup_test_temp(cfg)

    # Runner は print だけ行う
    print_summary(summary)

    return summary
