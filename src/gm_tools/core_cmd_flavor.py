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

"""リモートコマンド実行と tar フレーバ判定のラッパーを提供するモジュール。

リモートホスト上で ``tar`` の実装種別を検出し、``sudo`` や ``PATH`` の違いを
吸収したコマンド実行ヘルパーを提供する。

Examples:
    >>> from gm_tools.core_cmd_flavor import parse_tar_t_list_to_relpaths
    >>> parse_tar_t_list_to_relpaths("foo/\nbar/\nfile.txt\n")
    ['foo', 'bar', 'file.txt']
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Any, List, Literal, Optional, Set, Tuple

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
    """リモートホスト上の ``tar`` 実装種別を保持するデータクラス。

    Attributes:
        tar (TarFlavor): 判定済みの ``tar`` フレーバ。
    """
    tar: TarFlavor


def _exec_simple(ssh: SSHClientLike, cmd: str, timeout: Optional[float] = None) -> Tuple[int, str, str]:
    """依存の少ないリモートコマンド実行ヘルパー。

    指定したコマンドを SSH 経由で実行し、標準出力と標準エラーをすべて読み取って
    戻り値コードとともに返す。読み取り後は可能な範囲でチャネルをクローズする。

    Args:
        ssh (SSHClientLike): コマンドを実行する SSH クライアント互換オブジェクト。
        cmd (str): リモートで実行するシェル文字列。
        timeout (Optional[float]): SSH 側のタイムアウト秒。 ``None`` で未指定。

    Returns:
        Tuple[int, str, str]: 戻り値コード、標準出力文字列、標準エラー文字列。

    Examples:
        >>> class _DummyChannel:
        ...     def __init__(self) -> None:
        ...         self.channel = self
        ...     def read(self) -> bytes:
        ...         return b""
        ...     def close(self) -> None:
        ...         return None
        ...     def recv_exit_status(self) -> int:
        ...         return 0
        >>> class _DummySSH:
        ...     def exec_command(self, *_args, **_kwargs):
        ...         ch = _DummyChannel()
        ...         return ch, ch, ch
        >>> _exec_simple(_DummySSH(), "true")
        (0, '', '')
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
    """リモートホストで ``tar`` の実装種別を判定する。

    ``tar --version`` と ``tar --help`` の出力を解析して GNU tar、bsdtar、
    未知のいずれかを推定する。

    Args:
        ssh (SSHClientLike): コマンドを実行する SSH クライアント互換オブジェクト。
        timeout (float): リモートコマンド実行のタイムアウト秒。

    Returns:
        CmdFlavor: 判定結果を格納したデータクラス。

    Examples:
        >>> class _DummySSHFlavor:
        ...     def __init__(self, text: str) -> None:
        ...         self._text = text
        ...     def exec_command(self, *_args, **_kwargs):
        ...         class _Channel:
        ...             def __init__(self, data: str) -> None:
        ...                 self._data = data
        ...                 self.channel = self
        ...             def read(self) -> bytes:
        ...                 return self._data.encode()
        ...             def close(self) -> None:
        ...                 return None
        ...             def recv_exit_status(self) -> int:
        ...                 return 0
        ...         ch = _Channel(self._text)
        ...         return ch, ch, ch
        >>> detect_tar_flavor_remote(_DummySSHFlavor('tar (GNU tar)'))
        CmdFlavor(tar='gnu')
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
    """tar アーカイブを展開するコマンド引数を生成する。

    - GNU tar と bsdtar 共通で ``-xzf`` と ``-C`` を利用する。
    - 抽出メンバーを限定する場合は ``-T <members_file>`` を追加する。
      ``members_file`` は改行区切りの相対パス列で、アーカイブ内パスと一致させる。

    Args:
        flavor (TarFlavor): 判定済みの ``tar`` フレーバ。現状は将来拡張のため受け取る。
        dest_abs (str): 展開先ディレクトリの絶対パス。
        tar_gz_path (str): 展開対象の ``.tar.gz`` ファイルパス。
        use_sudo (bool): ``True`` のとき ``sudo -n`` を argv 先頭へ付与する。
        members_file (Optional[str]): メンバー限定抽出時に利用するファイルパス。

    Returns:
        List[str]: 実行用の argv 形式コマンド列。

    Examples:
        >>> build_tar_extract_cmd(
        ...     flavor='gnu',
        ...     dest_abs='/tmp/dest',
        ...     tar_gz_path='/tmp/src.tar.gz',
        ...     use_sudo=False,
        ... )
        ['tar', '-xzf', '/tmp/src.tar.gz', '-C', '/tmp/dest']
    """
    _ = flavor  # 現状は共通オプションで対応。分岐時の将来拡張用に受け取る。

    base: List[str] = ["sudo", "-n"] if use_sudo else []
    argv: List[str] = base + ["tar", "-xzf", tar_gz_path, "-C", dest_abs]

    has_members_file: bool = members_file is not None and len(members_file) > 0
    if has_members_file:
        argv += ["-T", members_file if members_file is not None else ""]

    return argv


def build_tar_list_cmd(*, tar_gz_path: str, use_sudo: bool) -> List[str]:
    """``tar -tzf`` のコマンド引数を生成する。

    Args:
        tar_gz_path (str): 列挙対象の ``.tar.gz`` ファイルパス。
        use_sudo (bool): ``True`` のとき ``sudo -n`` を前置する。

    Returns:
        List[str]: ``tar -tzf`` を実行する argv 形式のコマンド列。

    Examples:
        >>> build_tar_list_cmd(tar_gz_path='/tmp/src.tar.gz', use_sudo=True)
        ['sudo', '-n', 'tar', '-tzf', '/tmp/src.tar.gz']
    """
    sudo_prefix: List[str] = ["sudo", "-n"] if use_sudo else []
    cmd: List[str] = sudo_prefix + ["tar", "-tzf", tar_gz_path]
    return cmd


def _inject_path_for_bash_argv(cmd_argv: List[str]) -> List[str]:
    """``bash -lc`` 形式の argv に ``DEFAULT_PATH_EXPORT`` を注入する。

    - ``['bash', '-lc', <cmd>, ...]`` と ``['sudo', ..., 'bash', '-lc', <cmd>, ...]`` のみ対象。
    - 対象外の argv はコピーを返し、引数順序は維持する。

    Args:
        cmd_argv (List[str]): 変換対象のコマンド引数列。

    Returns:
        List[str]: 必要に応じて ``DEFAULT_PATH_EXPORT`` を先頭へ挿入した新しい argv。

    Examples:
        >>> _inject_path_for_bash_argv(['bash', '-lc', 'echo 1'])
        ['bash', '-lc', 'PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH echo 1']
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
    """リモートで argv 形式コマンドを安全に実行し結果を取得する。

    ``bash -lc`` 形式の場合は ``DEFAULT_PATH_EXPORT`` を注入し、それ以外は argv を
    変更せず ``shlex.join`` で結合する。

    Args:
        ssh (SSHClientLike): コマンドを実行する SSH クライアント互換オブジェクト。
        cmd_argv (List[str]): 実行したい argv 形式コマンド列。
        timeout (float): リモートコマンド実行のタイムアウト秒。

    Returns:
        Tuple[int, str, str]: 戻り値コード、標準出力文字列、標準エラー文字列。

    Examples:
        >>> ssh = ...  # SSHClientLike を準備する
        >>> run_remote_cmd_capture(ssh, ['bash', '-lc', 'true'])  # doctest: +SKIP
        (0, '', '')
    """
    safe_argv: List[str] = _inject_path_for_bash_argv(cmd_argv)
    cmd_str: str = shlex.join(safe_argv)

    rc: int
    out: str
    err: str
    rc, out, err = _exec_simple(ssh, cmd_str, timeout=timeout)

    return rc, out, err


def parse_tar_t_list_to_relpaths(listing_text: str) -> List[str]:
    """``tar -tzf`` の出力を相対パス配列に正規化する。

    空行を除外し、ディレクトリエントリ末尾の ``/`` を削除した形で返す。

    Args:
        listing_text (str): ``tar -tzf`` などの列挙結果文字列。

    Returns:
        List[str]: 正規化した相対パス一覧。

    Examples:
        >>> parse_tar_t_list_to_relpaths('dir/\nfile.txt\n')
        ['dir', 'file.txt']
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
    """リモートで任意のコマンドを実行し結果を取得する。

    - ``use_sudo`` が ``True`` の場合は常に ``sudo -n`` を前置する。
    - ``bash`` を経由するコマンドは :func:`run_remote_cmd_capture` を利用する。
    - 非シェルコマンドを安全に実行するため、``PATH`` の注入は行わない。環境変数が必要な場合は
            ``bash`` 経路を利用する。

    Args:
        ssh (SSHClientLike): コマンドを実行する SSH クライアント互換オブジェクト。
        cmd (str): リモートで実行するシェル文字列。
        use_sudo (bool): ``True`` のとき ``sudo -n`` を付与する。
        timeout (Optional[float]): SSH 実行のタイムアウト秒。 ``None`` で未指定。

    Returns:
        Tuple[int, str, str]: 戻り値コード、標準出力文字列、標準エラー文字列。

    Examples:
        >>> ssh = ...  # SSHClientLike を準備する
        >>> exec_remote(ssh, 'true')  # doctest: +SKIP
        (0, '', '')
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
    """``test -e`` でリモートパスの存在有無を確認する。

    Args:
        ssh (SSHClientLike): コマンドを実行する SSH クライアント互換オブジェクト。
        path (str): 存在確認したいリモートパス。
        use_sudo (bool): ``True`` のとき ``sudo -n`` を前置する。
        timeout (float): SSH 実行のタイムアウト秒。

    Returns:
        bool: パスが存在する場合は ``True``、存在しない場合は ``False``。

    Examples:
        >>> ssh = ...  # SSHClientLike を準備する
        >>> remote_path_exists(ssh, '/tmp/example', use_sudo=False)  # doctest: +SKIP
        True
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
    """リモートで ``mkdir -p`` を実行してディレクトリを作成する。

    Args:
        ssh (SSHClientLike): コマンドを実行する SSH クライアント互換オブジェクト。
        path (str): 作成したいリモートパス。
        use_sudo (bool): ``True`` のとき ``sudo -n`` を前置する。
        timeout (float): SSH 実行のタイムアウト秒。

    Raises:
        RuntimeError: ``mkdir -p`` が失敗した場合。リターンコードと stderr を含む。

    Examples:
        >>> ssh = ...  # SSHClientLike を準備する
        >>> remote_mkdir_p(ssh, '/tmp/example', use_sudo=False)  # doctest: +SKIP
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
    """リモートの相対パス群を存在グループと新規グループへ分類する。

    ``test -e`` を用いて判定し、存在するパスは ``exist_set``、存在しないパスは
    ``new_set`` として返す。``sudo`` 実行時に権限拒否が発生した場合は例外を送出する。

    Args:
        ssh (SSHClientLike): コマンドを実行する SSH クライアント互換オブジェクト。
        dest_abs (str): 判定対象のベースディレクトリ絶対パス。
        rel_paths (List[str]): 判定したい相対パス一覧。
        use_sudo (bool): ``True`` のとき ``sudo -n`` を前置する。
        timeout (float): SSH 実行のタイムアウト秒。

    Returns:
        Tuple[Set[str], Set[str]]: ``(exist_set, new_set)`` のタプル。

    Raises:
        RuntimeError: ``sudo`` 実行時に権限拒否が判明した場合。

    Examples:
        >>> ssh = ...  # SSHClientLike を準備する
        >>> split_exist_new_by_remote_presence(
        ...     ssh,
        ...     '/tmp',
        ...     ['a.txt', 'b.txt'],
        ... )  # doctest: +SKIP
        (set(), {'a.txt', 'b.txt'})
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
