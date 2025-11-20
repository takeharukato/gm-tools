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
"""ACL/xattr 情報をリモートで取得・復元するためのユーティリティ群です。

ACL と拡張属性 (xattr) を `getfacl`/`setfacl` および `getfattr`/`setfattr` コマンドで操作し、
現在の所有者・グループ・モードを取得/復元する共通処理も提供します。各関数は Paramiko 互換
SSH クライアントを介してコマンドを実行し、ダンプファイルを作成/適用するライフサイクルを
簡潔に扱えるようにします。
"""

from __future__ import annotations

import shlex
from typing import Optional, Tuple, List

from .core_ssh import SSHClientLike
from .core_cmd_flavor import run_remote_cmd_capture

# === constants (no Final, to match project style) ===
CMD_CHECK_TIMEOUT: float = 10.0
STAT_TIMEOUT: float = 10.0
CHOWN_CHMOD_TIMEOUT: float = 15.0
DUMP_TIMEOUT: float = 15.0
RESTORE_TIMEOUT: float = 20.0
ACL_CMDS: Tuple[str, str] = ("getfacl", "setfacl")
XATTR_CMDS: Tuple[str, str] = ("getfattr", "setfattr")
ACL_MKTEMP_TEMPLATE: str = "acl.XXXXXX"
XATTR_MKTEMP_TEMPLATE: str = "xattr.XXXXXX"

def check_acl_tools_available(ssh: SSHClientLike) -> bool:
    """ACL 操作用コマンドが両方利用可能かを確認します。

    Args:
        ssh (SSHClientLike): リモートコマンドを実行するための Paramiko 互換クライアント。

    Returns:
        bool: ``getfacl`` と ``setfacl`` の両方が見つかれば ``True``、不足していれば ``False``。

    Examples:
        >>> from unittest.mock import patch
        >>> def fake_run(_ssh, argv, timeout):
        ...     return (0, "", "")
        >>> with patch('gm_tools.core_xattr.run_remote_cmd_capture', side_effect=fake_run):
        ...     check_acl_tools_available(object())
        True
    """

    c: str
    for c in ACL_CMDS:
        rc: int
        _out: str
        _err: str
        rc, _out, _err = run_remote_cmd_capture(
            ssh, ["bash", "-lc", f"command -v {shlex.quote(c)} >/dev/null 2>&1"],
            timeout=CMD_CHECK_TIMEOUT
        )
        if rc != 0:
            return False
    return True


def check_xattr_tools_available(ssh: SSHClientLike) -> bool:
    """xattr 操作用コマンドが両方利用可能かを確認します。

    Args:
        ssh (SSHClientLike): リモートコマンドを実行するための Paramiko 互換クライアント。

    Returns:
        bool: ``getfattr`` と ``setfattr`` の両方が見つかれば ``True``、不足していれば ``False``。

    Examples:
        >>> from unittest.mock import patch
        >>> def fake_run(_ssh, argv, timeout):
        ...     return (0, "", "")
        >>> with patch('gm_tools.core_xattr.run_remote_cmd_capture', side_effect=fake_run):
        ...     check_xattr_tools_available(object())
        True
    """

    c: str
    for c in XATTR_CMDS:
        rc: int
        _out: str
        _err: str
        rc, _out, _err = run_remote_cmd_capture(
            ssh, ["bash", "-lc", f"command -v {shlex.quote(c)} >/dev/null 2>&1"],
            timeout=CMD_CHECK_TIMEOUT
        )
        if rc != 0:
            return False
    return True


def stat_owner_group_mode(ssh: SSHClientLike, path: str, *, use_sudo: bool) -> Tuple[str, str, int]:
    """パスの所有者・グループ・モード(8進数)を取得します。

    ``stat`` コマンドを ``stat -c '%U:%G:%a'`` 形式で呼び出し、
    リモートホスト上の対象パスから所有者・グループ名およびモード(8進数)を取得します。

    Args:
        ssh (SSHClientLike): リモートで ``stat`` を実行する Paramiko 互換クライアント。
        path (str): リモートホスト上で調査する絶対パスまたは相対パス。
        use_sudo (bool): ``True`` の場合は ``sudo`` 経由で ``stat`` を実行します。

    Returns:
        Tuple[str, str, int]: ``(owner, group, mode)`` のタプル。モードは 8 進数を整数に変換した値。

    Examples:
        >>> from unittest.mock import patch
        >>> def fake_run(_ssh, argv, timeout):
        ...     return (0, 'root:wheel:755\n', '')
        >>> with patch('gm_tools.core_xattr.run_remote_cmd_capture', side_effect=fake_run):
        ...     stat_owner_group_mode(object(), '/tmp/demo', use_sudo=False)
        ('root', 'wheel', 493)
    """
    q: str = shlex.quote(path)

    # ロケールの影響を避けるため LC_ALL=C を明示
    argv: List[str] = (
        ["sudo", "-n", "bash", "-lc", f"LC_ALL=C stat -c '%U:%G:%a' {q}"]
        if use_sudo else
        ["bash", "-lc", f"LC_ALL=C stat -c '%U:%G:%a' {q}"]
    )
    rc: int
    out: str
    _err: str
    rc, out, _err = run_remote_cmd_capture(
        ssh, argv, timeout=STAT_TIMEOUT
    )
    if rc != 0:
        # 取得失敗時は空を返すが, 呼び出し側で None 判定するより簡潔に, 既定値を返す
        #  ( 呼出側では空の場合 chown/chmod をスキップする設計 )
        return ("", "", 0)
    s: str = out.strip()
    # 例: "root:root:644"
    parts: List[str] = s.split(":")
    if len(parts) != 3:
        return ("", "", 0)
    owner: str = parts[0]
    group: str = parts[1]
    try:
        mode_int: int = int(parts[2], 8)
    except Exception:
        mode_int = 0
    return (owner, group, mode_int)


def chown_chmod(
    ssh: SSHClientLike,
    path: str,
    *,
    owner: Optional[str],
    group: Optional[str],
    mode: Optional[int],
    use_sudo: bool,
) -> None:
    """所有者・グループ・モードを必要に応じて復元します。

    Args:
        ssh (SSHClientLike): ``chown``/``chmod`` をリモート実行する Paramiko 互換クライアント。
        path (str): 属性を復元するファイルまたはディレクトリのパス。
        owner (Optional[str]): 新しい所有者。``None`` または空文字を指定した場合は, 復元処理を行いません。
        group (Optional[str]): 新しいグループ。``None`` または空文字を指定した場合は, 復元処理を行いません。
        mode (Optional[int]): 適用するモード値 (8 進想定)。``None`` または ``0`` を指定した場合は, 復元処理を行いません。
        use_sudo (bool): ``True`` の場合 ``sudo`` 経由でコマンドを実行します。

    Examples:
        >>> from unittest.mock import patch
        >>> executed = []
        >>> def fake_run(_ssh, argv, timeout):
        ...     executed.append(' '.join(argv))
        ...     return (0, '', '')
        >>> with patch('gm_tools.core_xattr.run_remote_cmd_capture', side_effect=fake_run):
        ...     chown_chmod(object(), '/tmp/demo', owner='alice', group='staff', mode=0o640, use_sudo=False)
        >>> bool(executed)
        True
    """
    q: str = shlex.quote(path)
    sudo_argv_prefix: List[str] = ["sudo", "-n"] if use_sudo else []

    if owner or group:
        # chown [-h] owner:group path
        # owner と group の両方が空でなければ owner:group, 片方のみならその書式で
        spec: str
        if owner and group:
            spec = f"{owner}:{group}"
        elif owner and not group:
            spec = owner
        elif (not owner) and group:
            spec = f":{group}"
        else:
            spec = ""
        if spec:
            _rc1: int
            _o1: str
            _e1: str
            _rc1, _o1, _e1 = run_remote_cmd_capture(
                ssh,
                sudo_argv_prefix + ["bash", "-lc", f"chown -h {shlex.quote(spec)} {q} || true"],
                timeout=CHOWN_CHMOD_TIMEOUT,
            )

    if mode is not None and mode != 0:
        # chmod は 8 進を4桁相当に整形 ( ACL/特殊ビットは OS が解釈 )
        # 例: 0o644 -> "0644"
        try:
            masked: int = int(mode) & 0o7777
        except Exception:
            masked = 0
        mstr: str = format(masked, "04o")
        _rc2: int
        _o2: str
        _e2: str
        _rc2, _o2, _e2 = run_remote_cmd_capture(
            ssh,
            sudo_argv_prefix + ["bash", "-lc", f"chmod {shlex.quote(mstr)} {q} || true"],
            timeout=CHOWN_CHMOD_TIMEOUT,
        )


def capture_acl_dump(
    ssh: SSHClientLike,
    path: str,
    dump_dir: str,
    *,
    use_sudo: bool,
) -> Optional[str]:
    """ファイルの Access Control List (ACL) を ``getfacl`` の出力形式でダンプし, リモートホスト上に保存します。

    - ``mktemp`` を用いて ``{dump_dir}/{ACL_MKTEMP_TEMPLATE}`` 形式の一時ファイルを確保したうえで,
      ``getfacl`` に ``-p``/``--absolute-names`` を付与して絶対パスを保持したダンプを作成します。
      このダンプは ``setfacl --restore`` で直接復元できる内容です。
    - ``mktemp`` や ``getfacl`` の呼び出しが失敗した場合 (コマンドがリモートホストに無い, 実行権限が無い, 対象パスにアクセスできない等),
       例外ではなく ``None`` を返して呼び出し側に処理を委ねます(ダンプファイルの削除は呼び出し側の責任で実施する想定です)。
    - 復元後に ``rm`` などでダンプファイルを削除する責務を呼び出し側が負います。
    - ``getfacl`` が利用可能であることを :func:`check_acl_tools_available` などで事前に確認してから呼び出してください。

    Args:
        ssh (SSHClientLike): ``getfacl`` を実行する Paramiko 互換クライアント。
        path (str): ACL を抽出する対象パス。
        dump_dir (str): ``mktemp`` でダンプファイルを生成するディレクトリ。
        use_sudo (bool): ``True`` の場合 ``sudo`` 経由で ``getfacl`` を実行します。

    Returns:
        Optional[str]: ダンプファイルの絶対パス。失敗時は ``None``。

    Examples:
        >>> from unittest.mock import patch
        >>> responses = [(0, '/tmp/acl.ABC\n', ''), (0, '', '')]
        >>> def fake_run(_ssh, argv, timeout):
        ...     return responses.pop(0)
        >>> with patch('gm_tools.core_xattr.run_remote_cmd_capture', side_effect=fake_run):
        ...     capture_acl_dump(object(), '/remote/file', '/tmp', use_sudo=False)
        '/tmp/acl.ABC'
    """
    qpath: str = shlex.quote(path)
    qdir: str = shlex.quote(dump_dir)
    sudo_argv_prefix: List[str] = ["sudo", "-n"] if use_sudo else []
    # ダンプ先ファイル
    rc: int
    out: str
    _e: str
    rc, out, _e = run_remote_cmd_capture(
        ssh, ["bash", "-lc", f"mktemp {qdir}/{ACL_MKTEMP_TEMPLATE}"], timeout=CMD_CHECK_TIMEOUT
    )
    if rc != 0:
        return None
    dump_path: str = out.strip()
    qdump: str = shlex.quote(dump_path)

    # getfacl: -p ( 絶対パスを保持 )  or --absolute-names
    rc2: int
    _o2: str
    _e2: str
    rc2, _o2, _e2 = run_remote_cmd_capture(
        ssh,
        sudo_argv_prefix
        + ["bash", "-lc",
           f"getfacl -p -- {qpath} > {qdump} 2>/dev/null || getfacl --absolute-names -- {qpath} > {qdump} 2>/dev/null"],
        timeout=DUMP_TIMEOUT,
    )
    if rc2 != 0:
        # ダンプ失敗時は None
        return None
    return dump_path


def restore_acl_dump(
    ssh: SSHClientLike,
    dump_file: str,
    *,
    use_sudo: bool,
) -> None:
    """ダンプファイルを ``setfacl --restore`` で適用します。

    - 復元後に ``rm`` などでダンプファイルを削除する責務を呼び出し側が負います。
    - :func:`check_acl_tools_available` を用いて``getfacl``/``setfacl`` の両方がが利用可能であることを
      事前に確認してから呼び出してください。

    Args:
        ssh (SSHClientLike): ``setfacl`` を実行する Paramiko 互換クライアント。
        dump_file (str): ``capture_acl_dump`` が生成したダンプファイルのパス。
        use_sudo (bool): ``True`` の場合 ``sudo`` 経由で ``setfacl`` を実行します。

    Examples:
        >>> from unittest.mock import patch
        >>> executed = []
        >>> def fake_run(_ssh, argv, timeout):
        ...     executed.append(' '.join(argv))
        ...     return (0, '', '')
        >>> with patch('gm_tools.core_xattr.run_remote_cmd_capture', side_effect=fake_run):
        ...     restore_acl_dump(object(), '/tmp/acl.ABC', use_sudo=False)
        >>> bool(executed)
        True
    """
    qdump: str = shlex.quote(dump_file)
    argv: List[str] = (
        ["sudo", "-n", "bash", "-lc", f"setfacl --restore={qdump} || true"]
        if use_sudo else
        ["bash", "-lc", f"setfacl --restore={qdump} || true"]
    )
    _rc: int
    _o: str
    _e: str
    _rc, _o, _e = run_remote_cmd_capture(ssh, argv, timeout=RESTORE_TIMEOUT)


def capture_xattr_dump(
    ssh: SSHClientLike,
    path: str,
    dump_dir: str,
    *,
    use_sudo: bool,
) -> Optional[str]:
    """拡張属性を ``getfattr`` の出力形式でダンプし, リモートホスト上に保存します。

    - ``mktemp`` を用いて ``{dump_dir}/{XATTR_MKTEMP_TEMPLATE}`` 形式の一時ファイルを確保し, ``getfattr`` に ``-h`` と ``--absolute-names`` を付けて絶対パスを保持したダンプを作成します。この形式は ``setfattr --restore`` でそのまま復元できます。
    - ``mktemp`` や ``getfacl`` の呼び出しが失敗した場合 (コマンドがリモートホストに無い, 実行権限が無い, 対象パスにアクセスできない等),
       例外ではなく ``None`` を返して呼び出し側に処理を委ねます(ダンプファイルの削除は呼び出し側の責任で実施する想定です)。
    - 復元後に ``rm`` などでダンプファイルを削除する責務を呼び出し側が負います。
    - ``getfattr`` が利用可能であることを :func:`check_xattr_tools_available` などで事前に確認してから呼び出してください。

    Args:
        ssh (SSHClientLike): ``getfattr`` を実行する Paramiko 互換クライアント。
        path (str): xattr を抽出する対象パス。
        dump_dir (str): ``mktemp`` でダンプファイルを生成するディレクトリ。
        use_sudo (bool): ``True`` の場合 ``sudo`` 経由で ``getfattr`` を実行します。

    Returns:
        Optional[str]: ダンプファイルの絶対パス。取得できない場合は ``None``。

    Examples:
        >>> from unittest.mock import patch
        >>> responses = [(0, '/tmp/xattr.ABC\n', ''), (0, '', '')]
        >>> def fake_run(_ssh, argv, timeout):
        ...     return responses.pop(0)
        >>> with patch('gm_tools.core_xattr.run_remote_cmd_capture', side_effect=fake_run):
        ...     capture_xattr_dump(object(), '/remote/file', '/tmp', use_sudo=False)
        '/tmp/xattr.ABC'
    """
    qpath: str = shlex.quote(path)
    qdir: str = shlex.quote(dump_dir)
    rc: int
    out: str
    _e: str
    rc, out, _e = run_remote_cmd_capture(
        ssh, ["bash", "-lc", f"mktemp {qdir}/{XATTR_MKTEMP_TEMPLATE}"], timeout=CMD_CHECK_TIMEOUT
    )
    if rc != 0:
        return None
    dump_path: str = out.strip()
    qdump: str = shlex.quote(dump_path)

    # getfattr のダンプ形式は setfattr --restore で復元可能
    # -h: シンボリックリンク自体の属性参照 ( 必要に応じ安全側 )
    rc2: int
    _o2: str
    _e2: str
    rc2, _o2, _e2 = run_remote_cmd_capture(
        ssh,
        (["sudo", "-n"] if use_sudo else [])
        + ["bash", "-lc",
           f"getfattr -h --absolute-names -d -- {qpath} > {qdump} 2>/dev/null"
           f" || getfattr --absolute-names -d -- {qpath} > {qdump} 2>/dev/null"],
        timeout=DUMP_TIMEOUT,
    )
    if rc2 != 0:
        return None
    return dump_path


def restore_xattr_dump(
    ssh: SSHClientLike,
    dump_file: str,
    *,
    use_sudo: bool,
) -> None:
    """ダンプファイルを ``setfattr --restore`` で適用します。

    - 事前に :func:`check_xattr_tools_available` で``setfattr`` が利用可能であることを確認してから呼び出すようにしてください。
    - 復元後に ``rm`` などでダンプファイルを削除する責務を呼び出し側が負います。

    Args:
        ssh (SSHClientLike): ``setfattr`` を実行する Paramiko 互換クライアント。
        dump_file (str): ``capture_xattr_dump`` が生成したダンプファイルのパス。
        use_sudo (bool): ``True`` の場合 ``sudo`` 経由で ``setfattr`` を実行します。

    Examples:
        >>> from unittest.mock import patch
        >>> executed = []
        >>> def fake_run(_ssh, argv, timeout):
        ...     executed.append(' '.join(argv))
        ...     return (0, '', '')
        >>> with patch('gm_tools.core_xattr.run_remote_cmd_capture', side_effect=fake_run):
        ...     restore_xattr_dump(object(), '/tmp/xattr.ABC', use_sudo=True)
        >>> bool(executed)
        True
    """
    qdump: str = shlex.quote(dump_file)
    argv: List[str] = (
        ["sudo", "-n", "bash", "-lc", f"setfattr --restore={qdump} || true"]
        if use_sudo else
        ["bash", "-lc", f"setfattr --restore={qdump} || true"]
    )
    _rc: int
    _o: str
    _e: str
    _rc, _o, _e = run_remote_cmd_capture(ssh, argv, timeout=RESTORE_TIMEOUT)
