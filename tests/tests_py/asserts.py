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
テスト用のアサーションユーティリティ。終了コード検証と属性比較を提供します。
"""
from __future__ import annotations

from typing import Dict, Iterable, Any, List

def assert_rc(name: str, rc: int, expect_zero: bool = True) -> None:
    """
    終了コードが期待条件に一致することを検証します。

    Args:
        name (str): 対象名 ( エラーメッセージ用 ) 。
        rc (int): 実際の終了コード。
        expect_zero (bool): True の場合 0 を期待, False の場合は非 0 を期待。

    Raises:
        AssertionError: 期待と異なる場合。
    """
    if expect_zero and rc != 0:
        raise AssertionError(f"{name}: expected rc=0 but got {rc}")
    if not expect_zero and rc == 0:
        raise AssertionError(f"{name}: expected non-zero rc but got 0")

def _norm_mode(m: Any) -> str:
    """
    モード値を 3 桁の 8 進文字列 ( 例: '640' ) へ正規化します。

    Args:
        m (Any): モード表現。

    Returns:
        str: 正規化された 3 桁 8 進文字列 ( 失敗時は入力の文字列表現 ) 。
    """
    if m is None:
        return ""
    try:
        s = str(m).strip()
        # If it's like '0640' or '640' or '0o640'
        if s.startswith("0o"):
            val = int(s, 8)
        elif s.startswith("0") and s != "0":
            val = int(s, 8)
        else:
            # if looks like decimal but is actually already octal text like '640'
            # try int as base 8 if it consists of 0-7 digits
            if all(ch in "01234567" for ch in s):
                val = int(s, 8)
            else:
                # could be 'drwxr-xr--' -> convert rough to octal bits  (owner/group/other)
                perms = s[-9:]
                bits = 0
                mapping = {'r':4,'w':2,'x':1,'-':0}
                for i, ch in enumerate(perms):
                    if ch in mapping:
                        # which triad?
                        tri = i // 3
                        shift = (2 - (i % 3))
                        bits += mapping[ch] << ( (2 - tri) * 3 + (2 - shift) )  # not exact, but try
                val = bits
        return f"{val:03o}"[-3:]
    except Exception:
        return str(m)

def _norm_owner(o: Any) -> str:
    """
    所有者表現を 'user:group' 形式へ正規化します。

    Args:
        o (Any): 所有者表現。

    Returns:
        str: 正規化後の所有者。
    """
    if o is None:
        return ""
    s = str(o).strip()
    # Common forms: 'user:group', 'user group', 'uid:gid'
    s = s.replace(" ", ":")
    # collapse multiple ':'
    while "::" in s:
        s = s.replace("::", ":")
    return s

def compare_attr_maps(src: Dict[str, Any],
                      dst: Dict[str, Any],
                      keys: Iterable[str] = ("mode", "owner")) -> None:
    """
    2 つの属性マップを比較し, 差異があれば詳細を含めて失敗させます。

    Args:
        src (Dict[str, Any]): 期待側の属性マップ。
        dst (Dict[str, Any]): 実測側の属性マップ。
        keys (Iterable[str]): 比較対象キー ( 既定は 'mode' と 'owner' ) 。

    Raises:
        AssertionError: 差分が検出された場合 ( 詳細メッセージ付き ) 。
    """
    diffs: List[str] = []
    for k in keys:
        s = src.get(k, None)
        d = dst.get(k, None)
        if k == "mode":
            s_n = _norm_mode(s)
            d_n = _norm_mode(d)
            if s_n != d_n:
                diffs.append(f"mode: expected={s_n!r} got={d_n!r} (raw src={s!r}, dst={d!r})")
        elif k == "owner":
            s_n = _norm_owner(s)
            d_n = _norm_owner(d)
            if s_n != d_n:
                diffs.append(f"owner: expected={s_n!r} got={d_n!r} (raw src={s!r}, dst={d!r})")
        else:
            if s != d:
                diffs.append(f"{k}: expected={s!r} got={d!r}")
    if diffs:
        raise AssertionError("attribute mismatch: " + "; ".join(diffs))
