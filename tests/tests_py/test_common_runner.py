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
共通 runner framework

    - runner の共通処理
    - case_xxx の例外捕捉
    - CaseResult 化
    - cleanup の呼び出し
    - JSON summary 出力
"""

from __future__ import annotations

from typing import Callable, List, Tuple, Any, Iterable, cast

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

_AUTH_FAILURE_MARKERS: Tuple[str, ...] = (
    "No authentication methods available",
    "Permission denied (publickey)",
    "Authentication failed",
)
"""SSH 認証失敗を検出する際に探す典型的なエラーメッセージ群。

ログや `CaseResult.details` にこれらの断片が含まれていれば, 認証経路が使えないと見なして
スキップ扱いに切り替える。例外型ではなく文字列で判定するのは SSH 実装ごとの差異を吸収する
ための互換性設計。"""


def _iter_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, dict):
        for v in cast(dict[Any, Any], value).values():
            yield from _iter_strings(v)
        return
    if isinstance(value, (list, tuple, set)):
        for v in cast(Iterable[Any], value):
            yield from _iter_strings(v)


def _should_mark_as_auth_skip(result: CaseResult) -> bool:
    if result.passed or result.skipped:
        return False
    for text in _iter_strings(result.details):
        for marker in _AUTH_FAILURE_MARKERS:
            if marker in text:
                return True
    for marker in _AUTH_FAILURE_MARKERS:
        if marker in result.reason:
            return True
    return False

def _run_case_safely(
    case_name: str,
    cfg: Config,
    case_func: Callable[[Config], Any],
) -> CaseResult:
    """
    テストケースを安全に実行し, 例外も失敗として `CaseResult` にカプセル化して返します。

    Args:
        case_name (str): ケース名 ( 表示や識別に使用 ) 。
        cfg (Config): ケースへ渡す構成オブジェクト。
        case_func (Callable[[Config], Any]): ケース本体の呼び出し可能オブジェクト。

    Returns:
        CaseResult: 実行結果。非 `CaseResult` が返却された場合は失敗として扱います。
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
        # 例外  =>  failed として CaseResult を構築
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
    runner 共通処理を実行します。各ケースの安全実行, メタ情報付与,
    cleanup, サマリ生成と出力までを行います。

    Args:
        step_number (int): 実行ステップ番号 ( 4/5/6 など ) 。
        cfg (Config): 実行用構成オブジェクト。
        cases (List[Tuple[str, Callable[[Config], CaseResult]]]): (ケース名, ケース関数) の配列。
    Returns:
        SummaryDict: 実行サマリ ( JSON 互換ディクショナリ ) 。

    Notes:
        - summary の標準出力, ファイル書き出し, テスト一時領域の cleanup を伴います。
    """
    results: List[CaseResult] = []

    for case_name, case_func in cases:
        r = _run_case_safely(case_name, cfg, case_func)
        if _should_mark_as_auth_skip(r):
            # 認証失敗メッセージが含まれているケースは「環境未整備によるスキップ」と扱い,
            # 結果を skipped に上書きして統計から除外。理由と詳細に固定文字列を入れて
            # リポート集計側で自動判定できるようにしている。
            r.skipped = True
            r.reason = "ssh authentication unavailable"
            r.details.setdefault("skip_cause", "ssh_auth_unavailable")
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

    # cleanup ( Step4/5/6 共通 )
    cleanup_test_temp(cfg)

    # Runner は print だけ行う
    print_summary(summary)
    written_path: str = write_summary_file(summary, step=step_number)
    print(f"[summary-file] path={written_path}")
    print_human_summary(summary)

    return summary
