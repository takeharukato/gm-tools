#!/usr/bin/env python3
# tests/tests_py/runner_step6.py
# Step6: GracefulStop / signal / parallel 統合テスト runner（ひな形版）

from __future__ import annotations

import json
from typing import Dict, List

from ._local_types import Config
from .config import load_config_from_env

def _make_skip_result(name: str, reason: str) -> Dict[str, object]:
    """
    Step6 実装前のプレースホルダとして、skipped なテスト結果を生成する。
    """
    result: Dict[str, object] = {
        "name": name,
        "passed": False,
        "skipped": True,
        "reason": reason,
        "details": {},
    }
    return result


def case_step6_placeholder(cfg: Config) -> Dict[str, object]:
    """
    目的:
      Step6 runner の骨格が正しく動作することのみを確認するプレースホルダ。
    期待:
      - JSON 形式の結果が 1 件出力される。
      - passed=False, skipped=True, reason にプレースホルダである旨が入る。
    """
    # cfg は将来的に GracefulStop + parallel.execute のテストに利用する想定だが、
    # ひな形段階では参照のみで実質的には使わない。
    _cfg_debug: str = f"Step6 placeholder using hosts_both={cfg.hosts_both}"
    # _cfg_debug は現状ログ出力等には使わないが、型アノテーション方針に従い保持しておく。
    reason: str = (
        "Step6 placeholder: GracefulStop + parallel.execute の結合テストは "
        "まだ実装されていません。"
    )
    result: Dict[str, object] = _make_skip_result(
        name="step6_placeholder", reason=reason
    )
    return result


def main() -> None:
    """
    Config をロードし、Step6 用テストケース群を実行して結果を JSON で出力する。
    現段階ではプレースホルダケースのみを実行する。
    """
    cfg: Config = load_config_from_env()
    results: List[Dict[str, object]] = []
    try:
        placeholder_result: Dict[str, object] = case_step6_placeholder(cfg)
        results.append(placeholder_result)

        print("STEP6 SUMMARY")
        summary: str = json.dumps(
            {"results": results},
            indent=2,
            ensure_ascii=False,
        )
        print(summary)
    finally:
        # Step4/5 と同様、本来はここでローカル一時ディレクトリの掃除を行う想定。
        # ひな形段階では、まだ Step6 専用のローカル一時ディレクトリを使っていないため
        # 実処理は入れない。
        no_cleanup_required: bool = True
        _no_cleanup_required_debug: bool = no_cleanup_required
        # 上記 2 変数は型アノテーション徹底のためだけに定義している。
        # 将来ローカル一時ディレクトリを導入したら、適切な cleanup 処理に置き換える。


if __name__ == "__main__":
    main()
