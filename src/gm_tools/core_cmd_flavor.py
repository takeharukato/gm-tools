# -*- coding:utf-8 -*-
from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import List, Optional, Tuple, Literal

try:
    import paramiko  # type: ignore
except Exception as e:
    raise RuntimeError("Paramiko is required: pip install paramiko") from e


TarFlavor = Literal["gnu", "bsdtar", "unknown"]


@dataclass(frozen=True)
class CmdFlavor:
    """
    リモートホスト上の 'tar' 実装の種別を表す。
    """
    tar: TarFlavor


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


def detect_tar_flavor_remote(ssh: "paramiko.SSHClient", *, timeout: float = 10.0) -> CmdFlavor:
    """
    リモートで `tar --version` を実行して GNU / bsdtar / unknown を判定する。
    """
    _rc: int
    out: str
    err: str
    _rc, out, err = _exec_simple(ssh, "tar --version || true", timeout=timeout)

    text: str = (out + "\n" + err).lower()
    flavor: TarFlavor
    if "gnu tar" in text:
        flavor = "gnu"
    elif "bsd tar" in text or "bsdtar" in text or "libarchive" in text:
        flavor = "bsdtar"
    else:
        # 一部環境では --version が使えない → 代替検知（ヘルプ文字列など）
        _rc_h: int
        out_h: str
        err_h: str
        _rc_h, out_h, err_h = _exec_simple(ssh, "tar --help || true", timeout=timeout)
        text_h: str = (out_h + "\n" + err_h).lower()
        if "gnu" in text_h:
            flavor = "gnu"
        elif "bsd" in text_h or "libarchive" in text_h:
            flavor = "bsdtar"
        else:
            flavor = "unknown"
    return CmdFlavor(tar=flavor)


def build_tar_extract_cmd(
    *,
    flavor: TarFlavor,
    tar_gz_path: str,
    dest_abs: str,
    use_sudo: bool,
) -> List[str]:
    """
    互換な抽出コマンドを配列で構築する。
    - GNU:    tar -xzf <tar> -C <dest> --no-same-owner --no-same-permissions
    - bsdtar: tar -xzf <tar> -C <dest>   （-p は付けない = 所有/ACL等を積極復元しない）
    - unknown: 同上（最小互換セット）
    """
    cmd: List[str] = []
    sudo_prefix: List[str] = ["sudo"] if use_sudo else []
    quoted_tar: str = shlex.quote(tar_gz_path)
    quoted_dest: str = shlex.quote(dest_abs)

    if flavor == "gnu":
        # 既存メタデータ非破壊のため GNU 拡張で積極適用を抑止
        cmd = sudo_prefix + ["tar", "-xzf", quoted_tar, "-C", quoted_dest, "--no-same-owner", "--no-same-permissions"]
    else:
        # bsdtar / unknown: -p を付与しない（メタデータ復元を抑止）
        cmd = sudo_prefix + ["tar", "-xzf", quoted_tar, "-C", quoted_dest]

    return cmd


def build_tar_list_cmd(*, tar_gz_path: str, use_sudo: bool) -> List[str]:
    """
    アーカイブ内パスの列挙（互換動作）。`tar -tzf`。
    """
    sudo_prefix: List[str] = ["sudo"] if use_sudo else []
    quoted_tar: str = shlex.quote(tar_gz_path)
    cmd: List[str] = sudo_prefix + ["tar", "-tzf", quoted_tar]
    return cmd


def run_remote_cmd_capture(
    ssh: "paramiko.SSHClient",
    cmd_argv: List[str],
    *,
    timeout: float = 60.0,
) -> Tuple[int, str, str]:
    """
    argv をシェル安全に結合して実行する（単純連結）。
    """
    cmd_str: str = " ".join(cmd_argv)
    rc: int
    out: str
    err: str
    rc, out, err = _exec_simple(ssh, cmd_str, timeout=timeout)
    return rc, out, err


def parse_tar_t_list_to_relpaths(listing_text: str) -> List[str]:
    """
    `tar -tzf` の出力を相対パスの配列に正規化。
    ディレクトリ末尾の `/` は除去する。
    """
    rels: List[str] = []
    for line in listing_text.splitlines():
        s: str = line.strip()
        if not s:
            continue
        # tar の listing は相対パスで出る想定
        if s.endswith("/"):
            s = s[:-1]
        rels.append(s)
    return rels


def split_exist_new_by_remote_presence(
    ssh: "paramiko.SSHClient",
    dest_abs: str,
    rel_paths: List[str],
    *,
    use_sudo: bool = False,
    timeout: float = 60.0,
) -> Tuple[List[str], List[str]]:
    """
    リモートの <dest_abs>/<rel> が存在するかをチェックし、(EXIST_SET, NEW_SET) に分割。
    """
    exists: List[str] = []
    news: List[str] = []

    # 一括で test するより、パスの特殊文字考慮や sudo 運用差があるため逐次で安全に。
    for rp in rel_paths:
        rp_str: str = rp
        remote_path: str = f"{dest_abs.rstrip('/')}/{rp_str}"
        _rc: int
        out: str
        _err: str
        prefix: str = "sudo " if use_sudo else ""
        test_cmd: str = f"{prefix}test -e {shlex.quote(remote_path)} && echo YES || echo NO"
        _rc, out, _err = _exec_simple(ssh, test_cmd, timeout=timeout)
        verdict: str = (out.strip() or "NO").upper()
        if verdict == "YES":
            exists.append(rp_str)
        else:
            news.append(rp_str)
    return exists, news
