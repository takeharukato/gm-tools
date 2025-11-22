# -*- coding: utf-8 -*-
# SPDX-License-Identifier: BSD-2-Clause
"""gm_tools.core_path_handling の相対 SRC 正規化回りを検証するユニットテスト。

目的:
    - `normalize_src_abs` が相対 SRC をホームディレクトリ配下へ結合する際、正規表現 tail の
      手前にあったセパレータを失わずに維持できることを確認する。

テスト内容:
    - `test_normalize_src_abs_preserves_separator_before_regex_tail`
        `gm_step4_regex_rel/src/dir1/.*` のような入力を与え、出力が `/home/demo/.../dir1/.*` と
        なることで `dir1/.*` のスラッシュが欠落しないことをアサートする。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from gm_tools.core_path_handling import normalize_src_abs


def test_normalize_src_abs_preserves_separator_before_regex_tail() -> None:
    result = normalize_src_abs("gm_step4_regex_rel/src/dir1/.*", home_abs_for_tilde="/home/demo")
    assert result == "/home/demo/gm_step4_regex_rel/src/dir1/.*"
