# -*- coding:utf-8 -*-
from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import List, Literal, Optional, Set, Tuple, Any
from .core_ssh import SSHClientLike


# NOTE:
# "bash -lc" 経由で実行されるシェルコマンド ( sudoの有無を問わず ) の前に
# 設定されるPATH環境変数の設定値。
# FreeBSD系システムでは portsからインストールされたコマンドが,
# /usr/local/bin, /usr/local/sbin に配置されることを想定して,
# /usr/sbin, /usr/bin, /sbin, /bin よりも先に, /usr/local/sbin, /usr/local/bin を
# 検索するようにしている。
# セキュリティの観点から, 既存のPATH環境変数は, システム標準コマンドディレクトリ
# よりも後に選択されるようにしている。
DEFAULT_PATH_EXPORT: str = (
    "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"
)

TarFlavor = Literal["gnu", "bsdtar", "unknown"]

# === Timeouts (seconds) used by remote ops ===
DEFAULT_TIMEOUT_SCATTER: float = 60.0
TAR_DETECT_TIMEOUT: float = 10.0
MKDIR_TIMEOUT: float = 60.0


@dataclass(frozen=True)
class CmdFlavor:
    """
    リモートホスト上の 'tar' 実装の種別を表す。
    """
    tar: TarFlavor


def _exec_simple(ssh: SSHClientLike, cmd: str, timeout: Optional[float] = None) -> Tuple[int, str, str]:
    """
    依存の少ない実行ヘルパ。stdout/err を全読みして (rc, out, err) を返す。
    """
    _stdin: Any
    stdout: Any
    stderr: Any
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


def detect_tar_flavor_remote(ssh: SSHClientLike, *, timeout: float = 10.0) -> CmdFlavor:
    """
    リモートで `tar --version` を実行して GNU / bsdtar / unknown を判定する。
    """
    _rc0: int
    out0: str
    err0: str
    _rc0, out0, err0 = _exec_simple(ssh, "tar --version || true", timeout=timeout)

    text: str = (out0 + "\n" + err0).lower()
    flavor: TarFlavor
    if "gnu tar" in text:
        flavor = "gnu"
    elif "bsd tar" in text or "bsdtar" in text or "libarchive" in text:
        flavor = "bsdtar"
    else:
        _rc1: int
        out1: str
        err1: str
        _rc1, out1, err1 = _exec_simple(ssh, "tar --help || true", timeout=timeout)
        text_h: str = (out1 + "\n" + err1).lower()
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
    - メンバー限定抽出: -T <members_file> を使用 ( GNU/bsdtar ともにサポート )
       ( members_file は改行区切りの相対パス列。アーカイブ内パスと一致させる )
    """
    _ = flavor  # 現状は共通オプションで対応。分岐時の将来拡張用に受け取る。

    base: List[str] = ["sudo", "-n"] if use_sudo else []
    argv: List[str] = base + ["tar", "-xzf", tar_gz_path, "-C", dest_abs]

    has_members_file: bool = members_file is not None and len(members_file) > 0
    if has_members_file:
        argv += ["-T", members_file if members_file is not None else ""]

    return argv


def build_tar_list_cmd(*, tar_gz_path: str, use_sudo: bool) -> List[str]:
    """
    アーカイブ内パスの列挙 ( 互換動作 ) 。`tar -tzf`。
    """
    sudo_prefix: List[str] = ["sudo", "-n"] if use_sudo else []
    cmd: List[str] = sudo_prefix + ["tar", "-tzf", tar_gz_path]
    return cmd


def _inject_path_for_bash_argv(cmd_argv: List[str]) -> List[str]:
    """
    ['bash','-lc', ...] 形式, または ['sudo', ...可変..., 'bash','-lc', ...] 形式に対して,
    シェル文字列の先頭へ DEFAULT_PATH_EXPORT を注入する。
    それ以外は argv を変更せず返す。
    """
    n: int = len(cmd_argv)
    idx: int = 0
    sudo_prefix: List[str] = []
    has_sudo: bool = False

    if n >= 1 and cmd_argv[0] == "sudo":
        has_sudo = True
        i: int
        for i in range(0, n):
            arg_i: str = str(cmd_argv[i])
            sudo_prefix.append(arg_i)
            if i + 1 < n and cmd_argv[i + 1] == "bash":
                idx = i + 1
                break
        if idx == 0:
            # sudo はあるが後続が bash ではない  =>  変更しない
            return cmd_argv[:]
    else:
        idx = 0

    is_bash_lc: bool = (n - idx) >= 2 and cmd_argv[idx] == "bash" and cmd_argv[idx + 1] == "-lc"
    if not is_bash_lc:
        return cmd_argv[:]

    # コマンド本体 ( 存在しなければ空文字 )
    orig_cmd: str = str(cmd_argv[idx + 2]) if (n - idx) >= 3 else ""
    injected: str = f"{DEFAULT_PATH_EXPORT} {orig_cmd}".strip()

    rebuilt: List[str] = []
    if has_sudo:
        rebuilt.extend(sudo_prefix)
    rebuilt.append("bash")
    rebuilt.append("-lc")
    rebuilt.append(injected)

    has_tail: bool = (n - idx) > 3
    if has_tail:
        tail: List[str] = [str(x) for x in cmd_argv[(idx + 3):]]
        rebuilt.extend(tail)

    return rebuilt


def run_remote_cmd_capture(
    ssh: SSHClientLike,
    cmd_argv: List[str],
    *,
    timeout: float = 60.0,
) -> Tuple[int, str, str]:
    """
    argv をシェル安全に結合して実行する。
    ['bash','-lc', ...] または ['sudo',...,'bash','-lc', ...] 形式の場合のみ,
    シェル文字列の先頭に DEFAULT_PATH_EXPORT を注入する。
    """
    safe_argv: List[str] = _inject_path_for_bash_argv(cmd_argv)
    cmd_str: str = shlex.join(safe_argv)

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
    line: str
    for line in listing_text.splitlines():
        s: str = line.strip()
        if not s:
            continue
        ends_with_slash: bool = s.endswith("/")
        s2: str = (s[:-1] if ends_with_slash else s)
        rels.append(s2)
    return rels


# === 統一リモート実行ラッパ ( sudo 経路の一元化 )  =========================
def exec_remote(
    ssh: SSHClientLike,
    cmd: str,
    *,
    use_sudo: bool = False,
    timeout: Optional[float] = None,
) -> Tuple[int, str, str]:
    """
    リモートでコマンドを実行し, (rc, stdout, stderr) を返す。
    use_sudo=True の場合は常に 'sudo -n' を前置 ( パスワードプロンプト禁止 ) 。
    PATH の注入は行わない ( 非シェルコマンドもあるため ) 。bash 経路は run_remote_cmd_capture() を利用。
    """
    full_cmd: str = f"sudo -n {cmd}" if use_sudo else cmd

    _stdin: Any
    stdout: Any
    stderr: Any
    _stdin, stdout, stderr = ssh.exec_command(full_cmd, timeout=timeout)

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


def remote_path_exists(
    ssh: SSHClientLike,
    path: str,
    *,
    use_sudo: bool,
    timeout: float = 60.0,
) -> bool:
    """
    test -e で存在確認。sudo 失敗 ( rc!=0 かつ 権限由来が明白 ) の場合は呼び出し側で中断判断可能。
    ここでは True/False のみ返す。
    """
    qpath: str = shlex.quote(path)

    rc: int
    _out: str
    _err: str
    rc, _out, _err = exec_remote(ssh, f"test -e {qpath}", use_sudo=use_sudo, timeout=timeout)

    exists: bool = (rc == 0)
    return exists


def remote_mkdir_p(
    ssh: SSHClientLike,
    path: str,
    *,
    use_sudo: bool,
    timeout: float = MKDIR_TIMEOUT,
) -> None:
    """
    mkdir -p を sudo 有無で実行。失敗時は詳細を含む例外を送出 ( 上位で即時中断方針 ) 。
    """
    qpath: str = shlex.quote(path)

    rc: int
    _out: str
    err: str
    rc, _out, err = exec_remote(ssh, f"mkdir -p {qpath}", use_sudo=use_sudo, timeout=timeout)

    if rc != 0:
        emsg: str = f"E_MKDIR: mkdir -p failed (rc={rc}) path={path} sudo={use_sudo}: {err.strip()}"
        raise RuntimeError(emsg)


def split_exist_new_by_remote_presence(
    ssh: SSHClientLike,
    dest_abs: str,
    rel_paths: List[str],
    *,
    use_sudo: bool = False,
    timeout: float = 60.0,
) -> Tuple[Set[str], Set[str]]:
    """
    DEST 配下の相対パス群について, 存在(EXIST) / 新規(NEW) を仕分ける。
    use_sudo=True のとき sudo -n で test を実行。sudo 不能や権限エラー時は自動フォールバックしない。
    """
    exist_set: Set[str] = set()
    new_set: Set[str] = set()

    rp: str
    for rp in rel_paths:
        rp_s: str = str(rp)
        remote_p: str = f"{dest_abs.rstrip('/')}/{rp_s.lstrip('/')}"
        q_remote_p: str = shlex.quote(remote_p)

        rc: int
        _out: str
        err: str
        rc, _out, err = exec_remote(ssh, f"test -e {q_remote_p}", use_sudo=use_sudo, timeout=timeout)

        if rc == 0:
            _ = err  # 明示的未使用
            exist_set.add(rp_s)
            continue

        err_l: str = err.lower()
        if use_sudo and ("sudo" in err_l) and (("permission" in err_l) or ("not allowed" in err_l)):
            raise RuntimeError(f"E_SUDO_TEST_DENIED: path={remote_p}: {err.strip()}")

        new_set.add(rp_s)

    return exist_set, new_set
