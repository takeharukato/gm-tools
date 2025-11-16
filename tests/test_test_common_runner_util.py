# gm-tools-tests-20251116/test_test_common_runner_util.py
#
# src/ で:
#
#   PYTHONPATH=. python3 -m pytest -q ../tests/test_test_common_runner_util.py
#

from __future__ import annotations

import json

import pytest  # type: ignore

from tests_py._local_types import CaseResult
from tests_py.test_common_runner_util import (
    run_case_safely,
    case_result_to_dict,
    append_case_result,
    print_summary,
)


def test_run_case_safely_success():
    called = {}

    def func():
        called["ok"] = True

    cr = run_case_safely("case1", func)

    assert called["ok"] is True
    assert isinstance(cr, CaseResult)
    assert cr.name == "case1"
    assert cr.passed is True
    assert cr.skipped is False
    assert cr.reason == ""
    assert cr.details == {}


def test_run_case_safely_exception():
    def func():
        raise ValueError("bad")

    cr = run_case_safely("case2", func)

    assert cr.passed is False
    assert cr.skipped is False
    assert cr.reason == "ValueError"
    assert "ValueError" in cr.details["exc"]
    assert "bad" in cr.details["exc"]


def test_case_result_to_dict_and_append():
    cr = CaseResult(name="c", passed=True, skipped=False, reason="", details={"k": "v"})
    d = case_result_to_dict(cr)
    assert d["name"] == "c"
    assert d["passed"] is True
    assert d["skipped"] is False
    assert d["reason"] == ""
    assert d["details"] == {"k": "v"}

    results = []
    append_case_result(results, cr) # type: ignore
    assert results == [d]


def test_print_summary_writes_label_and_json(capsys): # type: ignore
    results = [ # type: ignore
        {"name": "c1", "passed": True, "skipped": False, "reason": "", "details": {}},
        {"name": "c2", "passed": False, "skipped": False, "reason": "Err", "details": {"exc": "Err"}},
    ] # type: ignore

    print_summary("STEPX SUMMARY", results) # type: ignore
    captured = capsys.readouterr()  # type: ignore

    lines = captured.out.strip().splitlines() # type: ignore
    assert lines[0] == "STEPX SUMMARY"

    # 2 行目以降は JSON であることだけ確認（strict な構造チェックは json.loads に任せる）
    json_text = "\n".join(lines[1:]) # type: ignore
    data = json.loads(json_text)
    assert "results" in data # type: ignore
    assert data["results"] == results # type: ignore