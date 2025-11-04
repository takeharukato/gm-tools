# -*- coding:utf-8 -*-
from __future__ import annotations

import shlex
from typing import Optional, Tuple, List

try:
    import paramiko  # type: ignore
except Exception as e:
    raise RuntimeError("Paramiko is required: pip install paramiko") from e

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

def check_acl_tools_available(ssh: "paramiko.SSHClient") -> bool:
    """
    リモートに ACL 操作用ツール (getfacl/setfacl) が存在するかを確認する。
    どちらか一方でも欠ける場合は False。
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


def check_xattr_tools_available(ssh: "paramiko.SSHClient") -> bool:
    """
    リモートに xattr 操作用ツール (getfattr/setfattr) が存在するかを確認する。
    どちらか一方でも欠ける場合は False。
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


def stat_owner_group_mode(ssh: "paramiko.SSHClient", path: str, *, use_sudo: bool) -> Tuple[str, str, int]:
    """
    対象パスのオーナ、グループ、モード(8進数) を取得する。
    - stat(1) の -c '%U:%G:%a' を利用
    - use_sudo=True の場合は sudo 経由で実行
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
        # 取得失敗時は空を返すが、呼び出し側で None 判定するより簡潔に、既定値を返す
        # （呼出側では空の場合 chown/chmod をスキップする設計）
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
    ssh: "paramiko.SSHClient",
    path: str,
    *,
    owner: Optional[str],
    group: Optional[str],
    mode: Optional[int],
    use_sudo: bool,
) -> None:
    """
    所有者・グループ・モードを復元する共通関数。
    - owner/group は空文字や None の場合はスキップ。
    - mode は None の場合はスキップ。数値は 8 進で解釈・適用（例: 0o644）。
    - use_sudo=True のとき sudo 利用。
    """
    q: str = shlex.quote(path)
    sudo_argv_prefix: List[str] = ["sudo", "-n"] if use_sudo else []

    if owner or group:
        # chown [-h] owner:group path
        # owner と group の両方が空でなければ owner:group、片方のみならその書式で
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
        # chmod は 8 進を4桁相当に整形（ACL/特殊ビットは OS が解釈）
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
    ssh: "paramiko.SSHClient",
    path: str,
    dump_dir: str,
    *,
    use_sudo: bool,
) -> Optional[str]:
    """
    ACL をダンプしてファイルに保存し、そのダンプファイルのパスを返す。
    - setfacl --restore で復元できる形式（getfacl -p または --absolute-names）
    - ツールが無ければ呼出側でこの関数自体を呼ばない設計（check_acl_tools_available）
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

    # getfacl: -p（絶対パスを保持） or --absolute-names
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
    ssh: "paramiko.SSHClient",
    dump_file: str,
    *,
    use_sudo: bool,
) -> None:
    """
    setfacl --restore=<file> で ACL を復元する。
    - dump は capture_acl_dump() が作成したファイルを想定
    - use_sudo=True のとき sudo 利用
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
    ssh: "paramiko.SSHClient",
    path: str,
    dump_dir: str,
    *,
    use_sudo: bool,
) -> Optional[str]:
    """
    xattr をダンプしてファイルに保存し、そのダンプファイルのパスを返す。
    - getfattr -d（ダンプ形式）を利用（--absolute-names で絶対パス）
    - setfattr --restore で復元可能な形式
    - ツールが無ければ呼出側でこの関数自体を呼ばない設計（check_xattr_tools_available）
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
    # -h: シンボリックリンク自体の属性参照（必要に応じ安全側）
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
    ssh: "paramiko.SSHClient",
    dump_file: str,
    *,
    use_sudo: bool,
) -> None:
    """
    setfattr --restore=<file> で xattr を復元する。
    - dump は capture_xattr_dump() が作成したファイルを想定
    - use_sudo=True のとき sudo 利用
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
