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
テストで用いる環境依存のデフォルト定数を定義します ( 実行時に環境から評価 ) 。
"""
from __future__ import annotations

# SSH 関連のデフォルト値
SSH_PORT_DEFAULT: int = int(__import__("os").environ.get("SSH_PORT", "22"))
SSH_STRICT_DEFAULT: str = __import__("os").environ.get("SSH_STRICT", "no")

# パス関連
REMOTE_DEST_ROOT_DEFAULT: str = __import__("os").environ.get("REMOTE_DEST_ROOT", "/tmp/gmtools_remote_dest")
LOCAL_WORK_ROOT_DEFAULT: str = __import__("os").environ.get("LOCAL_WORK_ROOT", "./_tmp_test_local")

# ユーザ / ホスト関連
SSH_USER_DEFAULT: str = __import__("os").environ.get("SSH_USER", "ansible")
TARGET_USER_DEFAULT: str = __import__("os").environ.get("TARGET_USER", "ansible")
HOSTS_BOTH_DEFAULT: str = __import__("os").environ.get("HOSTS_BOTH", "localhost vmlinux4.local")
HOST_UBUNTU_DEFAULT: str = __import__("os").environ.get("HOST_UBUNTU", "localhost")
HOST_ALMA_DEFAULT: str = __import__("os").environ.get("HOST_ALMA", "vmlinux4.local")

# gm コマンド ( shlex 分割済み )
GM_GATHER_CMD_DEFAULT: str = __import__("os").environ.get("GM_GATHER_CMD", "python3 -m gm_tools.gather_cli")
GM_SCATTER_CMD_DEFAULT: str = __import__("os").environ.get("GM_SCATTER_CMD", "python3 -m gm_tools.scatter_cli")

# 挙動設定
VERBOSE_DEFAULT: bool = __import__("os").environ.get("VERBOSE", "1") == "1"

# 並列度
PARALLEL_DEFAULT: int = int(__import__("os").environ.get("PARALLEL", "2"))
