# -*- coding:utf-8 -*-
from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Set, List, Optional, Tuple, Literal

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
    dest_abs: str,
    tar_gz_path: str,
    use_sudo: bool,
    members_file: Optional[str] = None,
) -> List[str]:
    """
    tar.gz を dest_abs に展開するコマンド argv を返す。
    - GNU/bsdtar 共通: -xzf, -C
    - メンバー限定抽出: -T <members_file> を使用（GNU/bsdtar ともにサポート）
      （members_file は改行区切りの相対パス列。アーカイブ内パスと一致させる）
    """

    base: List[str] = ["sudo", "-n"] if use_sudo else []
    argv: List[str] = base + ["tar", "-xzf", tar_gz_path, "-C", dest_abs]
    if members_file:
        argv += ["-T", members_file]
    return argv

def build_tar_list_cmd(*, tar_gz_path: str, use_sudo: bool) -> List[str]:
    """
    アーカイブ内パスの列挙（互換動作）。`tar -tzf`。
    """
    sudo_prefix: List[str] = ["sudo", "-n"] if use_sudo else []
    cmd: List[str] = sudo_prefix + ["tar", "-tzf", tar_gz_path]
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

    cmd_str: str = shlex.join(cmd_argv)
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

# === 統一リモート実行ラッパ（sudo 経路の一元化） =========================
def exec_remote(
    ssh: "paramiko.SSHClient",
    cmd: str,
    *,
    use_sudo: bool = False,
    timeout: Optional[float] = None,
) -> Tuple[int, str, str]:
    """
    リモートでコマンドを実行し、(rc, stdout, stderr) を返す。
    use_sudo=True の場合は常に 'sudo -n' を前置（パスワードプロンプト禁止）。
    """
    full_cmd: str = f"sudo -n {cmd}" if use_sudo else cmd
    _stdin, stdout, stderr = ssh.exec_command(full_cmd, timeout=timeout)
    out_s: str = stdout.read().decode(errors="ignore")
    err_s: str = stderr.read().decode(errors="ignore")
    rc: int = stdout.channel.recv_exit_status()

    try:
        # 念のため close 順序は stdout/err を先にする
        stdout.close()
        stderr.close()
        _stdin.close()
    except Exception:
        pass
    return rc, out_s, err_s


def remote_path_exists(
    ssh: "paramiko.SSHClient",
    path: str,
    *,
    use_sudo: bool,
    timeout: float = 60.0,
) -> bool:
    """
    test -e で存在確認。sudo 失敗（rc!=0 かつ 権限由来が明白）の場合は呼び出し側で中断判断可能。
    ここでは True/False のみ返す。
    """
    rc, _out, _err = exec_remote(ssh, f"test -e {shlex.quote(path)}", use_sudo=use_sudo, timeout=timeout)
    return rc == 0


def remote_mkdir_p(
    ssh: "paramiko.SSHClient",
    path: str,
    *,
    use_sudo: bool,
    timeout: float = 60.0,
) -> None:
    """
    mkdir -p を sudo 有無で実行。失敗時は詳細を含む例外を送出（上位で即時中断方針）。
    """
    rc, _out, err = exec_remote(ssh, f"mkdir -p {path}", use_sudo=use_sudo, timeout=timeout)
    if rc != 0:
        raise RuntimeError(
            f"mkdir -p failed (rc={rc}) path={path} sudo={use_sudo}: {err.strip()}"
        )

def split_exist_new_by_remote_presence(
    ssh: "paramiko.SSHClient",
    dest_abs: str,
    rel_paths: List[str],
    *,
    use_sudo: bool = False,
    timeout: float = 60.0,
) -> Tuple[Set[str], Set[str]]:
    """
    既存の関数を sudo 経路対応に更新。
    - DEST 配下の相対パス群について、存在(EXIST) / 新規(NEW) を仕分ける
    - use_sudo=True のとき sudo -n で test を実行
    - sudo 不能や権限エラー時は自動フォールバックしない（上位で方針により即中断）
    """
    exist_set: Set[str] = set()
    new_set: Set[str] = set()
    for rp in rel_paths:
        # 絶対化（DEST + 相対）
        remote_p: str = f"{dest_abs.rstrip('/')}/{rp.lstrip('/')}"
        rc, _out, err = exec_remote(ssh, f"test -e {shlex.quote(remote_p)}", use_sudo=use_sudo, timeout=timeout)
        if rc == 0:
            exist_set.add(rp)
        else:
            # sudo 経路で失敗したが、test -e 不在なのか、sudo 不可なのかは err で判断する。
            # この段階では NEW と仮置きし、上位（policy）で必要に応じてエラーにする。
            # （方針：フォールバックせず、実際の作成/抽出段で mkdir/tar が失敗すれば中断）
            # ただし err が明確な sudo 不可を示す場合は、早期に例外化して中断させる。
            if use_sudo and "sudo" in err.lower() and ("permission" in err.lower() or "not allowed" in err.lower()):
                raise RuntimeError(
                    f"sudo test failed for path={remote_p}: {err.strip()}"
                )
            new_set.add(rp)

    return exist_set, new_set
