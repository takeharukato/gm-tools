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
Parallel gather/scatter ログの解析・整形・集計ヘルパ。
"""
# tests/tests_py/test_common_logs.py
# Parallel gather/scatter log formatting & summarizing helpers

from __future__ import annotations

import re
from typing import Any, Dict, List

_PAIR_RE = re.compile(r'(\w+)="([^"]*)"')

def parse_keyval_line(raw: str) -> Dict[str, str]:
    """
    ログ行から `key="value"` のペアを抽出して辞書化します。

    Args:
    - raw (str): 入力ログ行。

    Returns:
    - Dict[str, str]: パース結果。該当なしの場合は空辞書。
    """
    matches = _PAIR_RE.findall(raw)
    if not matches:
        return {}
    return {k: v for k, v in matches}

def is_parallel_log_line(raw: str) -> bool:
    """
    簡易ヒューリスティック: `' level='` を含み、かつ `key="value"` 形式のペアが 1 つ以上ある行を並列ログとみなします。

    Args:
    - raw (str): 入力ログ行。

    Returns:
    - bool: 並列ログとみなせる場合は True。
    """
    if " level=" not in raw:
        return False
    return bool(_PAIR_RE.search(raw))

def format_parallel_log_line(raw: str) -> str:
    """
    gather/scatter の並列ログ行を要約した短い文字列へ正規化します。

    Args:
    - raw (str): 入力ログ行。

    Returns:
    - str: 正規化後の簡潔な表現。
    """
    kv = parse_keyval_line(raw)
    if not kv:
        return raw
    parts: List[str] = []
    ts = kv.get("timestamp")
    if ts:
        parts.append(f"ts={ts}")
    lvl = kv.get("level")
    if lvl:
        parts.append(f"lvl={lvl}")
    op = kv.get("op")
    if op:
        parts.append(f"op={op}")
    host = kv.get("host")
    if host:
        parts.append(f"host={host}")
    phase = kv.get("phase")
    if phase:
        parts.append(f"phase={phase}")
    processed = kv.get("processed")
    total = kv.get("total")
    if processed or total:
        parts.append(f"proc={processed or '-'}" + f"/{total or '-'}")
    warnings = kv.get("warnings")
    if warnings:
        parts.append(f"warn={warnings}")
    errors = kv.get("errors")
    if errors:
        parts.append(f"err={errors}")
    duration = kv.get("duration")
    if duration:
        parts.append(f"dur={duration}")
    msg = kv.get("msg")
    if msg:
        parts.append(f"msg={msg}")
    return " ".join(parts)

def summarize_parallel_logs(lines: List[str]) -> Dict[str, Any]:
    """
    `level=` を含む行を対象に集計します（非該当行は無視）。

    Args:
    - lines (List[str]): ログ行の配列。

    Returns:
    - Dict[str, Any]: 総行数、対象行数、レベル別/ホスト別件数、警告/エラー合計など。
    """
    level_counts: Dict[str, int] = {}
    host_counts: Dict[str, int] = {}
    warnings_total = 0
    errors_total = 0
    error_hosts: Dict[str, int] = {}
    total_considered = 0
    for raw in lines:
        if not is_parallel_log_line(raw):
            continue
        kv = parse_keyval_line(raw)
        total_considered += 1
        lvl = kv.get("level")
        if lvl:
            level_counts[lvl] = level_counts.get(lvl, 0) + 1
        host = kv.get("host")
        if host:
            host_counts[host] = host_counts.get(host, 0) + 1
        w = kv.get("warnings")
        if w and w.isdigit():
            warnings_total += int(w)
        e = kv.get("errors")
        if e and e.isdigit():
            errors_total += int(e)
            if host:
                error_hosts[host] = error_hosts.get(host, 0) + int(e)
    return {
        "total_lines": len(lines),
        "considered_lines": total_considered,
        "levels": level_counts,
        "hosts": host_counts,
        "warnings_total": warnings_total,
        "errors_total": errors_total,
        "error_hosts": error_hosts,
    }
