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
安全なディレクトリ削除/再作成ユーティリティ。

Notes:
    - best-effort 方針を取り, 一部の例外は握りつぶします。
"""
from typing import Optional, Any, Union
import os
import shutil
from pathlib import Path
from .test_common_paths import ensure_under as _ensure_under

def safe_rmtree_abs(path: Union[str, Path], *, ensure_under: Optional[Union[str, Path]] = None) -> None:
    """
    絶対パスに対してのみ安全に rmtree を実行します ( 公開関数 ) 。

    Args:
        path (Union[str, Path]): 削除対象のパス。
        ensure_under (Optional[Union[str, Path]]): 指定時は, この配下の削除のみ許可します。

    Notes:
        - `path` が symlink の場合は削除しません。
        - `path` が存在しない場合は何もしません。
        - `path` が相対の場合は何もしません ( 安全性のため ) 。
        - 削除時の例外は握りつぶします ( best-effort ) 。
    """
    try:
        # 文字列化して安全確認 ( ensure_under は文字列パスで評価 )
        p_str: str = os.path.abspath(str(path))
        if ensure_under is not None:
            base_str: str = os.path.abspath(str(ensure_under))
            if not _ensure_under(base_str, p_str):
                return

        p = Path(p_str)

        # 絶対パスでなければ何もしない ( 安全 )
        if not p.is_absolute():
            return

        # symlink は削除しない ( 非常に危険なため除外 )
        if p.is_symlink():
            return

        # 存在しなければ何もしない
        if not p.exists():
            return

        shutil.rmtree(p, ignore_errors=True)

    except Exception:
        # cleanup は best-effort とする
        pass


def cleanup_dir(path: Union[str, Path]) -> None:
    """
    任意パスを安全に削除するラッパーです。

    Args:
        path (Union[str, Path]): 削除対象のパス。
    """
    safe_rmtree_abs(path)


def create_clean_dir(path: str, *, ensure_under: Optional[str] = None) -> None:
    """
    ディレクトリ内容を安全にクリアし, 存在しなければ作成します。

    Args:
        path (str): 対象ディレクトリ。
        ensure_under (Optional[str]): 指定時は, この配下のみ動作します。

    Notes:
        - 内部で `cleanup_dir` に委譲して削除後, 空ディレクトリを再作成します。
        - 失敗時の例外は握りつぶします ( テストユーティリティのため ) 。
    """
    try:
        p: str = os.path.abspath(path)
        if ensure_under is not None and not _ensure_under(ensure_under, p):
            return
        if os.path.exists(p):
            cleanup_dir(p)
        os.makedirs(p, exist_ok=True)
    except Exception:
        # テストユーティリティのため, 失敗時は握りつぶす
        pass


def cleanup_test_temp(cfg: Any) -> None:
    """
    `cfg.test_temp_root` で指定されたテスト用ディレクトリを削除します。

    Args:
        cfg (Any): `test_temp_root` 属性を持つ可能性のあるオブジェクト。

    Notes:
        - `test_common_config` が `test_temp_root` を設定していることを前提とします。
    """
    if hasattr(cfg, "test_temp_root"):
        cleanup_dir(Path(cfg.test_temp_root))
