# -*- coding:utf-8 -*-
from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import List, Optional, Tuple, Literal

try:
    import paramiko  # type: ignore
except Exception as e:
    raise RuntimeError("Paramiko is required: pip install paramiko") from e


SelinuxMode = Literal["auto", "policy", "ignore"]


@dataclass(frozen=True)
class SelinuxSupport:
    """
    SELinux 対応可否の判定結果。
    """
    supported: bool
    reason: Optional[str] = None


def _exec_simple(ssh: "paramiko.SSHClient", cmd: str, timeout: Optional[float] = None) -> Tuple[int, str, str]:
    """
    依存の少ない実行ヘルパ。stdout/err を全読みして (rc, out, err) を返す。
    """
    _stdin: "paramiko.ChannelFile"
    stdout: "paramiko.ChannelFile"
    stderr: "paramiko.ChannelFile"
    _stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out_s: str = stdout.read().decode(errors="ignore")
    err_s: str = stderr.read().decode(errors="ignore")
    rc: int = stdout.channel.recv_exit_status()
    try:
        stdout.close()
        stderr.close()
        _stdin.close()
    except Exception:
        pass
    return rc, out_s, err_s


def detect_selinux_supported_remote(ssh: "paramiko.SSHClient", *, timeout: float = 10.0) -> SelinuxSupport:
    """
    以下両方を満たすと 'supported=True':
      1) /sys/fs/selinux が存在 もしくは selinuxfs がマウントされている
      2) restorecon コマンドが存在
    """
    rc1: int
    _out1: str
    _err1: str
    rc1, _out1, _err1 = _exec_simple(
        ssh,
        r"""test -d /sys/fs/selinux || mount | grep -q selinuxfs""",
        timeout=timeout,
    )
    rc2: int
    _out2: str
    _err2: str
    rc2, _out2, _err2 = _exec_simple(
        ssh,
        r"""command -v restorecon >/dev/null 2>&1""",
        timeout=timeout,
    )

    supported: bool = (rc1 == 0) and (rc2 == 0)
    reason: Optional[str] = None
    if not supported:
        reason = f"probe1_rc={rc1}, probe2_rc={rc2}"
    return SelinuxSupport(supported=supported, reason=reason)


def restorecon_newset_remote(
    ssh: "paramiko.SSHClient",
    *,
    dest_abs: str,
    new_rel_paths: List[str],
    mode: SelinuxMode,
    selinux_supported: bool,
    use_sudo: bool,
    dry_run: bool,
    timeout: float = 120.0,
) -> None:
    """
    NEW_SET（新規作成された相対パス群）に対してのみ restorecon を実行する。
    - mode='auto'   : selinux_supported=True の場合のみ実行。False なら何もしない。
    - mode='policy' : selinux_supported=False の場合はエラー（例外）を送出。True なら実行。
    - mode='ignore' : 何もしない。
    """
    if mode == "ignore":
        return

    if not selinux_supported:
        if mode == "policy":
            raise RuntimeError("SELinux is not supported on remote host (mode=policy).")
        # auto: 対応不可なら黙ってスキップ
        return

    if not new_rel_paths:
        return

    # まとめて DEST を対象にするよりも NEW_SET のみ対象にする。処理コスト低減と既存ラベル保護のため。
    for rp in new_rel_paths:
        rp_str: str = rp
        abs_path: str = f"{dest_abs.rstrip('/')}/{rp_str}"
        prefix: str = "sudo " if use_sudo else ""
        cmd: str = f"""{prefix}restorecon -RF {shlex.quote(abs_path)}"""
        if dry_run:
            # dry-run は実コマンドを投げない
            continue
        rc: int
        out: str
        err: str
        rc, out, err = _exec_simple(ssh, cmd, timeout=timeout)
        if rc != 0:
            # エラーだが致命化はしない（方針によりここで raise も可）
            # 呼び出し側が TransferReport に失敗を記録するのが望ましい。
            # ここでは例外を投げず継続。
            _ = (out, err)  # 型のために束縛
            # ログ出力は呼び出し側に委ねる
            pass
