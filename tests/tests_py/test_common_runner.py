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

from typing import Callable, List, Tuple, Any

from ._local_types import (
    Config,
    CaseResult,
    SummaryDict,
)
from .test_common_json import (
    make_summary,
    print_summary,
    write_summary_file,
    print_human_summary,
)
from .test_common_cleanup import cleanup_test_temp
from . import test_common_hosts as _hosts_mod

def _run_case_safely(
    case_name: str,
    cfg: Config,
    case_func: Callable[[Config], Any],
) -> CaseResult:
    """
    テストケースを安全に実行し、例外も CaseResult として返す。
    """
    try:
        result = case_func(cfg)
        # case_func が CaseResult を返さなかった場合の保険
        if not isinstance(result, CaseResult):
            return CaseResult(
                name=case_name,
                passed=False,
                skipped=False,
                reason=f"case returned non-CaseResult: {type(result)!r}",
                details={
                    "returned_type": type(result).__name__,
                },
            )
        return result

    except Exception as e:
        # 例外 → failed として CaseResult を構築
        return CaseResult(
            name=case_name,
            passed=False,
            skipped=False,
            reason=f"case raised exception: {e!r}",
            details={"exception_repr": repr(e)},
        )


def run_cases(
    *,
    step_number: int,
    cfg: Config,
    cases: List[Tuple[str, Callable[[Config], CaseResult]]],
) -> SummaryDict:
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
        # 追加メタ: preflight / preparation コマンドを各ケース details に付与
        try:
            pf = getattr(cfg, "extra_preflight_cmds", None)
            if pf:
                r.details.setdefault("preflight_cmds", list(pf))
            prep = getattr(cfg, "extra_prep_cmds", None)
            if prep:
                r.details.setdefault("prep_cmds", list(prep))
            try:
                hosts_files = _hosts_mod.get_created_hosts_files()
                if hosts_files:
                    r.details.setdefault("hosts_files", list(hosts_files))
            except Exception:
                pass
        except Exception:
            # details 付与はベストエフォート
            pass
        results.append(r)

    # summary を生成
    summary: SummaryDict = make_summary(
        step_number=step_number,
        cfg=cfg,
        results=results,
    )

    # cleanup（Step4/5/6 共通）
    cleanup_test_temp(cfg)

    # Runner は print だけ行う
    print_summary(summary)
    written_path: str = write_summary_file(summary, step=step_number)
    print(f"[summary-file] path={written_path}")
    print_human_summary(summary)

    return summary
