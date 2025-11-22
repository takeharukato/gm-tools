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
"""SELinux 機能検出とラベル復元を扱うリモート実行ヘルパーモジュールである。

gm-scatter CLI の ``--selinux`` オプションで利用され, リモートホストが SELinux 対応かどうかの
判定と ``restorecon`` コマンドの実行を統一的に提供する。
"""

from __future__ import annotations

import shlex
from typing import Iterable, Literal, List, Set

from .core_ssh import SSHClientLike
from .core_cmd_flavor import run_remote_cmd_capture

SelinuxMode = Literal["auto", "policy", "ignore"]

# --- 定数 (秒・サイズ) ---
_SELINUX_DETECT_TIMEOUT: float = 5.0
_RESTORECON_TIMEOUT: float = 180.0
# "Argument list too long" を避けるため保守的な値を指定する。
_RESTORECON_BATCH_SIZE: int = 64

# --- コマンド文字列とフラグ  ---
_SELINUX_FS_TEST_CMD: str = "test -d /sys/fs/selinux"
_SELINUX_MOUNT_CHECK_CMD: str = "mount | grep -q selinuxfs"
_RESTORECON_CHECK_CMD: str = "command -v restorecon >/dev/null 2>&1"
_RESTORECON_FLAGS: str = "-RF"

def detect_selinux_capable(ssh: SSHClientLike) -> bool:
    """リモートホストが SELinux ラベル復元に対応しているかを検査する。

    gm-scatter CLI で ``restorecon`` を呼び出す前段階として利用し, 最小限のリモートコマンド
    実行でホストの機能可否を判定する。以下の条件をすべて満たした場合に対応しているとみなす。

    - ``/sys/fs/selinux`` ディレクトリが存在する, または ``mount`` 出力に ``selinuxfs`` が含まれる
    - ``restorecon`` コマンドが PATH 上で検出できる

    Args:
        ssh (SSHClientLike): ``run_remote_cmd_capture()`` 互換の ``exec_command`` を提供するクライアント。

    Returns:
        bool: 判定に成功した場合は ``True``, 条件を満たせない場合は ``False``。

    Examples:
        >>> from unittest.mock import patch
        >>> def fake_run(_ssh, argv, timeout):
        ...     cmd = " ".join(argv)
        ...     if "mount" in cmd:
        ...         return 0, "selinuxfs on /sys/fs/selinux", ""
        ...     return 0, "", ""
        >>> with patch('gm_tools.core_selinux.run_remote_cmd_capture', fake_run):
        ...     detect_selinux_capable(object())
        True
    """

    rc_fs1: int
    _o1: str
    _e1: str
    rc_fs1, _o1, _e1 = run_remote_cmd_capture(
        ssh, ["bash", "-lc", _SELINUX_FS_TEST_CMD], timeout=_SELINUX_DETECT_TIMEOUT
    )
    if rc_fs1 != 0:
        rc_fs2: int
        _o2: str
        _e2: str
        rc_fs2, _o2, _e2 = run_remote_cmd_capture(
            ssh, ["bash", "-lc", _SELINUX_MOUNT_CHECK_CMD], timeout=_SELINUX_DETECT_TIMEOUT
        )
        if rc_fs2 != 0:
            return False

    rc_cmd: int
    _o3: str
    _e3: str
    rc_cmd, _o3, _e3 = run_remote_cmd_capture(
        ssh, ["bash", "-lc", _RESTORECON_CHECK_CMD], timeout=_SELINUX_DETECT_TIMEOUT
    )
    if rc_cmd != 0:
        return False

    return True


def restorecon_recursive_if_needed(
    *,
    ssh: SSHClientLike,
    paths: Iterable[str],
    mode: SelinuxMode,
    selinux_capable: bool,
    use_sudo: bool,
) -> None:
    """必要に応じて ``restorecon -RF`` をバッチ実行し SELinux ラベルを復元する。

    gm-scatter の配置処理が ``mode`` に応じてラベリングを要求する場合に呼び出され, 以下の挙動を取る。

    - ``mode == "ignore"``: 何も実施せず即座に処理を終了する。
    - ``mode == "auto"``: ``selinux_capable`` が ``True`` のときのみ ``restorecon`` を実行する。
    - ``mode == "policy"``: ``selinux_capable`` が ``False`` の場合は ``RuntimeError`` を送出する。

    対象パスは新規または上書き対象に限定されている想定であり, 空文字や重複を除外したうえで,
    引数数が多くなり過ぎないようバッチ分割して ``restorecon`` を実行する。

    Args:
        ssh (SSHClientLike): ``restorecon`` を実行するリモートホストへのクライアント。
        paths (Iterable[str]): ラベル復元候補のリモート絶対パス列。
        mode (SelinuxMode): ユーザー指定 ``--selinux`` モード。
        selinux_capable (bool): :func:`detect_selinux_capable` の判定結果。
        use_sudo (bool): sudo 経由で ``restorecon`` を実行する場合は ``True``。

    Raises:
        RuntimeError: ``mode == "policy"`` かつ ``selinux_capable`` が ``False`` の場合,
            または ``restorecon`` 実行が失敗した場合に送出する。

    Examples:
        >>> from unittest.mock import patch
        >>> calls = []
        >>> def fake_run(_ssh, argv, timeout):
        ...     calls.append((argv, timeout))
        ...     return 0, "", ""
        >>> with patch('gm_tools.core_selinux.run_remote_cmd_capture', fake_run):
        ...     restorecon_recursive_if_needed(
        ...         ssh=object(),
        ...         paths=["/tmp/demo", "/tmp/demo"],
        ...         mode="auto",
        ...         selinux_capable=True,
        ...         use_sudo=False,
        ...     )
        >>> len(calls)
        1
    """
    if mode == "ignore":
        return

    if not selinux_capable:
        if mode == "policy":
            raise RuntimeError("SELinux policy enforcement requested but host is not SELinux-capable")
        # auto かつ非対応は黙ってスキップ
        return

    # 実行
    # 正規化 : 空文字除外・重複排除・安定順序
    norm: List[str] = []
    seen: Set[str] = set()
    p: str
    for p in paths:
        s: str = str(p).strip()
        if not s:
            continue
        if s in seen:
            continue
        seen.add(s)
        norm.append(s)

    if not norm:
        return

    # まとめて 1 回で実行 ( 長い場合でも restorecon は複数引数を受ける )
    # -R ( 再帰 ) , -F ( 強制再ラベル )
    sudo_argv_prefix: List[str] = ["sudo", "-n"] if use_sudo else []
    # バッチ実行 ( 引数超過・ARG_MAX 回避 )
    i: int
    for i in range(0, len(norm), _RESTORECON_BATCH_SIZE):
        chunk: List[str] = norm[i : i + _RESTORECON_BATCH_SIZE]
        q_paths: List[str] = [shlex.quote(p) for p in chunk]
        cmd: str = f"restorecon {_RESTORECON_FLAGS} -- " + " ".join(q_paths)
        rc: int; _out: str; err: str
        rc, _out, err = run_remote_cmd_capture(
            ssh, sudo_argv_prefix + ["bash", "-lc", cmd], timeout=_RESTORECON_TIMEOUT
        )
        if rc != 0:
            # policy/auto いずれでも restorecon 失敗は中断させる ( 上位で failed 記録 )
            emsg: str = (err.strip() or f"restorecon failed (rc={rc}) on batch starting with: {chunk[0]}")
            raise RuntimeError(emsg)

    return