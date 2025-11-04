# -*- coding:utf-8 -*-
from __future__ import annotations

import shlex
from typing import Optional, Tuple

try:
    import paramiko  # type: ignore
except Exception as e:
    raise RuntimeError("Paramiko is required: pip install paramiko") from e

from .core_cmd_flavor import run_remote_cmd_capture


def check_acl_tools_available(ssh: "paramiko.SSHClient") -> bool:
    """
    リモートに ACL 操作用ツール (getfacl/setfacl) が存在するかを確認する。
    どちらか一方でも欠ける場合は False。
    """
    cmds = ["getfacl", "setfacl"]
    for c in cmds:
        rc, _out, _err = run_remote_cmd_capture(ssh, (["bash", "-lc", f"command -v {shlex.quote(c)} >/dev/null 2>&1"]), timeout=10.0)
        if rc != 0:
            return False
    return True


def check_xattr_tools_available(ssh: "paramiko.SSHClient") -> bool:
    """
    リモートに xattr 操作用ツール (getfattr/setfattr) が存在するかを確認する。
    どちらか一方でも欠ける場合は False。
    """
    cmds = ["getfattr", "setfattr"]
    for c in cmds:
        rc, _out, _err = run_remote_cmd_capture(ssh, (["bash", "-lc", f"command -v {shlex.quote(c)} >/dev/null 2>&1"]), timeout=10.0)
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
    prefix = "sudo " if use_sudo else ""
    rc, out, _err = run_remote_cmd_capture(
        ssh,
        (["bash", "-lc", f"{prefix}stat -c '%U:%G:%a' {q}"]),
        timeout=10.0,
    )
    if rc != 0:
        # 取得失敗時は空を返すが、呼び出し側で None 判定するより簡潔に、既定値を返す
        # （呼出側では空の場合 chown/chmod をスキップする設計）
        return ("", "", 0)
    s: str = out.strip()
    # 例: "root:root:644"
    parts = s.split(":")
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
    prefix = "sudo " if use_sudo else ""

    if owner or group:
        # chown [-h] owner:group path
        # owner と group の両方が空でなければ owner:group、片方のみならその書式で
        if owner and group:
            spec = f"{owner}:{group}"
        elif owner and not group:
            spec = owner
        elif (not owner) and group:
            spec = f":{group}"
        else:
            spec = ""
        if spec:
            run_remote_cmd_capture(ssh, (["bash", "-lc", f"{prefix}chown -h {shlex.quote(spec)} {q} || true"]), timeout=15.0)

    if mode is not None and mode != 0:
        # chmod は 8 進を4桁相当に整形（ACL/特殊ビットは OS が解釈）
        mstr: str = format(mode, "o")
        run_remote_cmd_capture(ssh, (["bash", "-lc", f"{prefix}chmod {shlex.quote(mstr)} {q} || true"]), timeout=15.0)


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
    prefix = "sudo " if use_sudo else ""
    # ダンプ先ファイル
    rc, out, _ = run_remote_cmd_capture(ssh, (["bash", "-lc", f"mktemp {qdir}/acl.XXXXXX"]), timeout=10.0)
    if rc != 0:
        return None
    dump_path: str = out.strip()
    qdump: str = shlex.quote(dump_path)

    # getfacl: -p（絶対パスを保持） or --absolute-names
    rc2, _o2, _e2 = run_remote_cmd_capture(
        ssh,
        (["bash", "-lc", f"{prefix}getfacl -p -- {qpath} > {qdump} 2>/dev/null || {prefix}getfacl --absolute-names -- {qpath} > {qdump} 2>/dev/null"]),
        timeout=15.0,
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
    prefix = "sudo " if use_sudo else ""
    run_remote_cmd_capture(ssh, (["bash", "-lc", f"{prefix}setfacl --restore={qdump} || true"]), timeout=20.0)


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
    prefix = "sudo " if use_sudo else ""
    rc, out, _ = run_remote_cmd_capture(ssh, (["bash", "-lc", f"mktemp {qdir}/xattr.XXXXXX"]), timeout=10.0)
    if rc != 0:
        return None
    dump_path: str = out.strip()
    qdump: str = shlex.quote(dump_path)

    # getfattr のダンプ形式は setfattr --restore で復元可能
    # -h: シンボリックリンク自体の属性参照（必要に応じ安全側）
    rc2, _o2, _e2 = run_remote_cmd_capture(
        ssh,
        (["bash", "-lc", f"{prefix}getfattr -h --absolute-names -d -- {qpath} > {qdump} 2>/dev/null || {prefix}getfattr --absolute-names -d -- {qpath} > {qdump} 2>/dev/null"]),
        timeout=15.0,
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
    prefix = "sudo " if use_sudo else ""
    run_remote_cmd_capture(ssh, (["bash", "-lc", f"{prefix}setfattr --restore={qdump} || true"]), timeout=20.0)
