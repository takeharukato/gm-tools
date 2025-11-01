#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gm-scatter.py : ローカルのファイル/ディレクトリを, 複数のリモートホスト上の dest 配下へ配布するツール。

対応OS ( ローカル実行環境 ) :
  - Windows 11 ( Windows版 Python3 )
  - AlmaLinux 9.0 以降
  - Ubuntu Linux 24.04 以降
  - FreeBSD 14 ( Python は ports 想定, リモートの tar は base の bsdtar 想定 )

主な機能:
  - 明示 src 指定モード と パターン選択モード ( --pattern-abs / --pattern-rel / --root )
  - 逐次 SFTP 配布 ( デフォルト )  と 一括アーカイブ配布 ( --pack )
  - --pack 時のメタデータ保持 ( --preserve-perms/owner/acls/xattrs )
  - SELinux 取り扱い ( --selinux {auto,policy,archive,ignore} )
  - ホスト並列処理 ( --parallel )
  - パス・トラバーサル防止, 重複配布の抑止

仕様は添付の仕様書 gm-scatter-spec.md に準拠する。
"""  # noqa: E501

from __future__ import annotations

import argparse
import concurrent.futures
import getpass
import os
import platform
import posixpath
import re
import shlex
import socket
import subprocess
import sys
import stat
import time
import tarfile
import tempfile
import traceback

from uuid import uuid4
from enum import Enum
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Pattern, Set, Tuple

# ==============================
#  変更時の注意
# ==============================
# - すべての関数に型アノテーションと Docstring を付与すること。
# - 未使用変数には _prefix を付け, 静的解析の警告を抑止すること。
# - 例外は極力握り潰さず, 収集して最終的に集計出力すること。
# - リモートのパスは SFTP 経路では '/' 区切りを用いる ( プロトコル仕様 ) 。
# - 逐次 SFTP ではメタ情報は保持しない。メタ情報が必要な場合は --pack を使用する。


# ========= 定数 / 終了コード =========

DEFAULT_SSH_PORT: int = 22
DEFAULT_PARALLEL: int = 4
DEFAULT_TIMEOUT: float = 30.0
DEFAULT_SOCKET_TIMEOUT: float = 60.0

RUN_CMD_CHUNK_SIZE: int = 32768
RUN_CMD_NONBLOCK_TIMEOUT: float = 0.0
RUN_CMD_POLL_INTERVAL: float = 0.05

EXIT_SUCCESS: int = 0
EXIT_NO_TARGETS: int = 1
EXIT_PARTIAL: int = 2
EXIT_MODULE_MISSING: int = 4
EXIT_INVALID_ARGS: int = 5

# ---- インポート必須モジュール ( Paramiko )  ----
try:
    import paramiko  # type: ignore
except Exception as _e:
    print("This script requires 'paramiko'. Install via OS package (python3-paramiko) or pip.", file=sys.stderr)
    sys.exit(EXIT_MODULE_MISSING)


# ========= データクラス =========

@dataclass
class SSHConfig:
    """SSH 接続設定。

    Attributes:
        host (str): 接続先ホスト名。
        port (int): SSH ポート番号。
        ssh_user (str): SSH ログインユーザー。
        key_filename (Optional[str]): 秘密鍵ファイルパス。
        password (Optional[str]): パスワード ( 非推奨 ) 。
        timeout (float): 接続/認証/バナータイムアウト秒。
        strict_host_key_checking (bool): 厳格ホスト鍵チェックを有効化するか。
    """
    host: str
    port: int
    ssh_user: str
    key_filename: Optional[str]
    password: Optional[str]
    timeout: float
    strict_host_key_checking: bool


@dataclass
class HostResult:
    """ホストごとの実行結果。

    Attributes:
        host (str): ホスト名。
        uploaded (int): 転送/展開したファイル ( またはアーカイブ ) 件数の概数。
        warnings (List[str]): 警告メッセージ一覧。
        errors (List[str]): エラーメッセージ一覧。
    """
    host: str
    uploaded: int
    warnings: List[str]
    errors: List[str]


# ========= 汎用ユーティリティ =========

def is_windows() -> bool:
    """ローカル OS が Windows であるかを返す。

    Returns:
        bool: Windows の場合 True, それ以外は False。
    """
    return os.name == "nt" or platform.system().lower().startswith("win")


def to_posix_rel(rel: str) -> str:
    """相対パス文字列を POSIX 形式 ( '/' 区切り ) へ変換する。

    Args:
        rel (str): 相対パス文字列 ( OS既定セパレータ ) 。

    Returns:
        str: '/' 区切りへ変換した相対パス。
    """
    return rel.replace("\\", "/") if "\\" in rel else rel


def safe_relpath_for_transfer(abs_path: str, base: str) -> Optional[str]:
    """送信用の相対名を生成し, 安全性 ( 先頭セパレータ禁止, '..' 逸脱禁止 ) を検査する。

    Args:
        abs_path (str): ローカルの絶対パス。
        base (str): 相対化の基点ディレクトリの絶対パス。

    Returns:
        Optional[str]: 安全な相対名 ( POSIX 風 ) を返す。安全でない場合は None。
    """
    try:
        rel = os.path.relpath(abs_path, base)
    except ValueError:
        # 異なるドライブ ( Windows ) など
        rel = os.path.basename(abs_path)
    rel = rel.replace(os.sep, "/")
    if not rel or rel.startswith("/"):
        return None
    norm = posixpath.normpath(rel)
    if norm == ".." or norm.startswith("../"):
        return None
    return norm


def best_root_for(abs_path: str, roots_abs: List[str]) -> str:
    """abs_path に最長一致する root を返す。見つからなければ '/' を返す。

    Args:
        abs_path (str): 絶対パス。
        roots_abs (List[str]): 絶対パス化された探索 root 一覧。

    Returns:
        str: 最適 root。
    """
    best = ""
    for r in roots_abs:
        rr = r.rstrip(os.sep) or os.sep
        pref = rr if abs_path == rr else (rr + os.sep)
        if abs_path == rr or abs_path.startswith(pref):
            if len(rr) > len(best):
                best = rr
    return best or os.sep


def parse_hosts_file(path: str) -> List[str]:
    """ホストファイルを読み, ホスト名一覧を返す。

    仕様:
      - 1 行 1 ホスト
      - 空行 / '#' 始まり
      -  ( TAB または空白 ) に続く '#' 以降はコメント

    Args:
        path (str): ホストファイルのパス。

    Returns:
        List[str]: ホスト一覧。
    """
    hosts: List[str] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            if s.startswith("#"):
                continue
            # タブ or 空白 に続く # 以降をコメント扱い
            m = re.search(r"[ \t]#", s)
            if m:
                s = s[: m.start()].rstrip()
            if s:
                hosts.append(s)
    return hosts


# ========= SSH / SFTP =========

def ssh_open(cfg: SSHConfig) -> paramiko.SSHClient:
    """SSH 接続を確立して返す。

    Args:
        cfg (SSHConfig): SSH 接続設定。

    Returns:
        paramiko.SSHClient: 接続済み SSH クライアント。
    """
    cli = paramiko.SSHClient()
    if cfg.strict_host_key_checking:
        cli.load_system_host_keys()
        cli.set_missing_host_key_policy(paramiko.RejectPolicy())
    else:
        cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(
        hostname=cfg.host,
        port=cfg.port,
        username=cfg.ssh_user,
        key_filename=os.path.expanduser(cfg.key_filename) if cfg.key_filename else None,
        password=cfg.password,
        timeout=cfg.timeout,
        banner_timeout=cfg.timeout,
        auth_timeout=cfg.timeout,
        allow_agent=True,
        look_for_keys=True,
    )
    return cli


def run_cmd(ssh: paramiko.SSHClient, cmd: str, timeout: float) -> Tuple[int, bytes, bytes]:
    """リモートでコマンドを実行し, 終了コードと標準出力・標準エラーを返す ( 完了までのタイムアウト付き ) 。"""
    _stdin, stdout, _stderr = ssh.exec_command(cmd, timeout=timeout)  # 開始待ち
    try:
        # 以後このコマンドは標準入力を受け取らない前提
        _stdin.channel.shutdown_write()
    except Exception:
        # 古い Paramiko などで失敗しても無視
        pass

    chan = stdout.channel
    deadline = time.monotonic() + timeout
    out_chunks: List[bytes] = []
    err_chunks: List[bytes] = []
    # ノンブロッキング化
    chan.settimeout(RUN_CMD_NONBLOCK_TIMEOUT)
    while True:
        now = time.monotonic()
        if now >= deadline:
            # タイムアウト：チャネルを閉じ, ここまでの出力を添えて通知
            try:
                chan.close()
            finally:
                partial_out = b"".join(out_chunks)
                partial_err = b"".join(err_chunks)
                # 直感的な短い要約 ( 先頭 4KB まで ) を含める
                def _head(b: bytes, n: int = 4096) -> str:
                    try:
                        return (b[:n].decode(errors="ignore"))
                    except Exception:
                        return ""
                raise TimeoutError(
                    f"command timed out after {timeout:.1f}s: {cmd}\n"
                    f"[stdout(head)] {_head(partial_out)}\n"
                    f"[stderr(head)] {_head(partial_err)}"
                )
        # 読み取り ( 溜まっている分だけ )
        while chan.recv_ready():
            out_chunks.append(chan.recv(RUN_CMD_CHUNK_SIZE))
        while chan.recv_stderr_ready():
            err_chunks.append(chan.recv_stderr(RUN_CMD_CHUNK_SIZE))
        if chan.exit_status_ready():
            # 残りを吐き出して終了
            while chan.recv_ready():
                out_chunks.append(chan.recv(RUN_CMD_CHUNK_SIZE))
            while chan.recv_stderr_ready():
                err_chunks.append(chan.recv_stderr(RUN_CMD_CHUNK_SIZE))
            rc = chan.recv_exit_status()
            return rc, b"".join(out_chunks), b"".join(err_chunks)
        time.sleep(RUN_CMD_POLL_INTERVAL)

def resolve_remote_home(ssh: paramiko.SSHClient, account: str, timeout: float) -> str:
    """リモートの account の HOME を返す。見つからない場合は慣習値を返す。

    Args:
        ssh (paramiko.SSHClient): SSH クライアント。
        account (str): アカウント名。
        timeout (float): タイムアウト秒。

    Returns:
        str: HOME ディレクトリの絶対パス。
    """
    rc, out, _ = run_cmd(ssh, f"getent passwd {shlex.quote(account)} | cut -d: -f6", timeout)
    if rc == 0:
        home = out.decode().strip()
        if home.startswith("/") and len(home) > 1:
            return home
    return "/root" if account == "root" else f"/home/{account}"


def ensure_remote_dir(ssh: paramiko.SSHClient, path: str, use_sudo: bool, timeout: float) -> None:
    """リモートで path を mkdir -p 相当で作成する。

    Args:
        ssh (paramiko.SSHClient): SSH クライアント。
        path (str): 作成するディレクトリ ( POSIX パス ) 。
        use_sudo (bool): sudo -n を付与するか。
        timeout (float): タイムアウト秒。
    """
    path = posixpath.normpath(path)
    cmd = f"{'sudo -n ' if use_sudo else ''}mkdir -p -- {shlex.quote(path)}"
    rc, _out, err = run_cmd(ssh, cmd, timeout)
    if rc != 0:
        msg = err.decode(errors="ignore") or "<no stderr>"
        raise RuntimeError(f"mkdir failed for {path}: {msg}")

def sftp_mkdirs(sftp: paramiko.SFTPClient, dest_dir: str) -> None:
    """SFTP で mkdir -p 相当の動作を行う。

    Args:
        sftp (paramiko.SFTPClient): SFTP クライアント。
        dest_dir (str): 作成するディレクトリ ( POSIX パス ) 。
    """
    made: Set[str] = set()
    parts = dest_dir.split("/")
    cur = ""
    for p in parts:
        if not p:
            continue
        cur = f"{cur}/{p}"
        if cur in made:
            continue
        try:
            sftp.stat(cur)
        except IOError:
            sftp.mkdir(cur)
        made.add(cur)


# ========= SELinux / tar 能力判定 =========

def probe_selinux_capable(ssh: paramiko.SSHClient, timeout: float) -> bool:
    """SELinux 対応可能ホストか判定する。

    条件:
      - test -d /sys/fs/selinux または mount | grep selinuxfs
      - command -v restorecon

    Args:
        ssh (paramiko.SSHClient): SSH クライアント。
        timeout (float): タイムアウト秒。

    Returns:
        bool: 対応可能なら True。
    """
    rc1, _o1, _e1 = run_cmd(ssh, "test -d /sys/fs/selinux || mount | grep -q selinuxfs", timeout)
    rc2, _o2, _e2 = run_cmd(ssh, "command -v restorecon >/dev/null 2>&1", timeout)
    return (rc1 == 0) and (rc2 == 0)


# ---- tar flavor detection / spec --------------------------------------------
class RemoteTarFlavor(Enum):
    """
    リモートホスト上の tar 実装区分と, その機能サポート有無を表す Enum。
    値は 'gnu' / 'bsd' の kind を保持し, 能力はプロパティで参照する。

    - GNU: GNU tar を想定。--same-owner / --acls / --xattrs をサポート。
    - BSD: bsdtar (libarchive) 等を想定。GNU ロングオプションは付与しない。
            ( ACL/xattr の復元はローカル/リモート実装依存だが, ここでは
             GNU 固有オプションを渡さない＝False とする。 )
    """
    GNU = "gnu"
    BSD = "bsd"

    @property
    def kind(self) -> str:
        """実装種別の短い識別子文字列 ( 'gnu' または 'bsd' ) を返す。"""
        return self.value

    @property
    def supports_same_owner(self) -> bool:
        """--same-owner を安全に付与できるか。GNU tar のみ True。"""
        return self is RemoteTarFlavor.GNU

    @property
    def supports_acls(self) -> bool:
        """--acls を安全に付与できるか。GNU tar のみ True。"""
        return self is RemoteTarFlavor.GNU

    @property
    def supports_xattrs(self) -> bool:
        """--xattrs を安全に付与できるか。GNU tar のみ True。"""
        return self is RemoteTarFlavor.GNU

def probe_remote_tar(ssh: paramiko.SSHClient, timeout: float) -> RemoteTarFlavor:
    """
    リモートホスト上の `tar` 実装を判別して返します。

    判別方針:
      1) `tar --version` の先頭行を確認し, "GNU tar" を含めば GNU と判定。
      2) そうでなければ, "bsdtar" や "libarchive", "FreeBSD" を含めば BSD と判定。
      3) `--version` が未対応などで判別できない場合は, `tar --help` を確認。
      4) それでも不明なら, 互換性重視で BSD とみなします ( FreeBSD base tar 等の想定 ) 。

    Args:
        ssh (paramiko.SSHClient): 既に接続済みの SSH クライアント。
        timeout (float): コマンド実行のタイムアウト秒。

    Returns:
        RemoteTarFlavor: GNU もしくは BSD を表す列挙値。
    """
    # 1) --version を試す
    _rc, out, err = run_cmd(ssh, "tar --version 2>&1 | head -n1", timeout)
    first = (out or err).decode(errors="ignore").strip()

    def _classify(line: str) -> Optional[RemoteTarFlavor]:
        s = line.lower()
        if "gnu tar" in s:
            return RemoteTarFlavor.GNU
        if "bsdtar" in s or "libarchive" in s or "freebsd" in s:
            return RemoteTarFlavor.BSD
        return None

    flavor = _classify(first)
    if flavor is not None:
        return flavor

    # 2) フォールバックとして --help を確認
    _rc2, out2, err2 = run_cmd(ssh, "tar --help 2>&1 | head -n2", timeout)
    head2 = (out2 or err2).decode(errors="ignore")
    flavor = _classify(head2)
    if flavor is not None:
        return flavor

    # 3) なお判別不能なら BSD 扱い ( 保守的デフォルト )
    return RemoteTarFlavor.BSD

def shutil_which(exe: str) -> Optional[str]:
    """shutil.which の簡易ラッパ。

    Args:
        exe (str): 実行ファイル名。

    Returns:
        Optional[str]: 見つかった場合はフルパス, 見つからない場合は None。
    """
    from shutil import which as _which
    return _which(exe)


def require_local_tar_when_preserve(preserve_owner: bool, preserve_acls: bool, preserve_xattrs: bool, preserve_perms: bool) -> None:
    """ローカルで --pack かつ preserve-* オプションが有効時, 外部 tar コマンドが必要かを検査し, なければ致命エラー。

    Args:
        preserve_owner (bool): 所有者保持。
        preserve_acls (bool): ACL 保持。
        preserve_xattrs (bool): xattr 保持。
        preserve_perms (bool): パーミッション保持。

    Raises:
        SystemExit: 外部 tar 利用不可の場合はプログラムを終了。
    """
    need = preserve_owner or preserve_acls or preserve_xattrs or preserve_perms
    if not need:
        return
    tar_cmd = "tar.exe" if is_windows() else "tar"
    found = shutil_which(tar_cmd)
    if not found:
        print("[FATAL] External 'tar' is required for --pack with any --preserve-* options, but not found.", file=sys.stderr)
        sys.exit(EXIT_INVALID_ARGS)

# ========= 能力確認 ( リモート / ローカル補助 )  =========

def check_remote_tool(ssh: "paramiko.SSHClient", tool: str, timeout: float) -> bool:
    """リモートに tool が存在するか簡易確認する。"""
    rc, _o, _e = run_cmd(ssh, f"command -v {shlex.quote(tool)} >/dev/null 2>&1", timeout)
    return rc == 0

def check_remote_xattr_capable(ssh: "paramiko.SSHClient", timeout: float) -> bool:
    """リモートが xattr 復元に最低限対応していそうか ( setfattr の有無 ) を確認する。"""
    return check_remote_tool(ssh, "setfattr", timeout)


# ========= ローカル探索 / パターン適用 =========

def compile_many(patterns: List[str], flags: int) -> List[Pattern[str]]:
    """複数の正規表現をコンパイルする。

    Args:
        patterns (List[str]): 正規表現文字列。
        flags (int): re フラグ。

    Returns:
        List[Pattern[str]]: コンパイル済み正規表現。
    """
    return [re.compile(p, flags) for p in patterns]


def iter_local_files_under(roots: List[str], follow_symlinks: bool) -> Iterator[str]:
    """root 群の配下のファイルを再帰列挙する ( ファイル単位 ) 。

    Args:
        roots (List[str]): 探索 root ( 絶対パス ) 。
        follow_symlinks (bool): シンボリックリンクを追随するか。

    Yields:
        str: 見つかったファイルの絶対パス。
    """
    for r in roots:
        if os.path.isfile(r):
            yield os.path.abspath(r)
            continue
        for root, _dirs, files in os.walk(r, followlinks=follow_symlinks):
            for name in files:
                yield os.path.abspath(os.path.join(root, name))


def match_any_abs(abs_path: str, abs_regexes: List[Pattern[str]]) -> bool:
    """絶対パスに対して --pattern-abs を判定する。

    Args:
        abs_path (str): 絶対パス。
        abs_regexes (List[Pattern[str]]): コンパイル済みパターン群。

    Returns:
        bool: いずれかに一致すれば True。
    """
    return any(rx.search(abs_path) for rx in abs_regexes)


def iter_relatives(abs_path: str, roots: List[str]) -> Iterator[Tuple[str, str]]:
    """abs_path を roots のいずれかからの相対に変換したペアを列挙する。

    Args:
        abs_path (str): 絶対パス。
        roots (List[str]): 探索 root ( 絶対 ) 。

    Yields:
        Tuple[str, str]: (root, rel)。
    """
    for root in roots:
        root_norm = root.rstrip(os.sep)
        if not root_norm:
            continue
        if abs_path == root_norm or abs_path.startswith(root_norm + os.sep):
            rel = abs_path[len(root_norm) + (0 if abs_path == root_norm else 1):]
            yield (root_norm, rel)


def match_any_rel(abs_path: str, roots: List[str], rel_regexes: List[Pattern[str]]) -> bool:
    """各 root からの相対に対して --pattern-rel を判定する。

    Args:
        abs_path (str): 絶対パス。
        roots (List[str]): 探索 root ( 絶対 ) 。
        rel_regexes (List[Pattern[str]]): コンパイル済みパターン群。

    Returns:
        bool: いずれかに一致すれば True。
    """
    if not rel_regexes:
        return False
    for _root, rel in iter_relatives(abs_path, roots):
        rel_posix = rel.replace(os.sep, "/")
        if any(rx.search(rel_posix) for rx in rel_regexes):
            return True
    return False


def select_local_by_patterns(roots: List[str],
                             pat_abs: List[Pattern[str]],
                             pat_rel: List[Pattern[str]],
                             follow_symlinks: bool,
                             verbose: bool) -> Tuple[Dict[str, str], List[str]]:
    """パターン指定でローカルファイルを選定し, 送信用相対名マップを返す。

    Args:
        roots (List[str]): 探索 root 群 ( 絶対パス ) 。
        pat_abs (List[Pattern[str]]): 絶対パス用パターン。
        pat_rel (List[Pattern[str]]): 相対パス用パターン。
        follow_symlinks (bool): シンボリックリンクを追随するか。
        verbose (bool): 冗長出力。

    Returns:
        Tuple[Dict[str, str], List[str]]: (abs->rel マップ ( POSIX 形式 ) , ヒット一覧)。
    """
    files = list(iter_local_files_under(roots, follow_symlinks=follow_symlinks))
    if verbose:
        print(f"[local] scanned {len(files)} files under {', '.join(roots)}")
    rel_map: Dict[str, str] = {}
    hits: Set[str] = set()
    for p in files:
        hit_a = bool(pat_abs and match_any_abs(p, pat_abs))
        hit_r = bool(pat_rel and match_any_rel(p, roots, pat_rel))
        if not (hit_a or hit_r):
            continue
        base = best_root_for(p, roots)
        rel = (p[len(base):].lstrip(os.sep)) if base != os.sep else p.lstrip(os.sep)
        # 安全チェック
        rel_posix = to_posix_rel(posixpath.normpath(rel.replace(os.sep, "/")))
        if not rel_posix or rel_posix.startswith("/") or rel_posix == ".." or rel_posix.startswith("../"):
            print(f"[Error] unsafe relative path derived: {p} -> {rel_posix}", file=sys.stderr)
            continue
        rel_map[p] = rel_posix
        hits.add(p)
    return rel_map, sorted(hits)


# ========= アーカイブ ( ローカル作成 / リモート展開 )  =========

def make_python_tar_gz_from_relmap(rel_map: Dict[str, str], verbose: bool) -> str:
    """Python 標準の tarfile で tar.gz を作成 ( メタ情報保持の保証はしない ) 。

    Args:
        rel_map (Dict[str, str]): {abs -> rel(POSIX)}。
        verbose (bool): 冗長出力。

    Returns:
        str: 作成した一時アーカイブのローカルパス。
    """
    fd, tmp = tempfile.mkstemp(prefix="gm_scatter_", suffix=".tar.gz")
    os.close(fd)
    if verbose:
        print(f"[local] pack (python) -> {tmp}")
    with tarfile.open(tmp, mode="w:gz", format=tarfile.PAX_FORMAT) as tf:
        for apath, rel in rel_map.items():
            # リンクはリンクエントリとして封入 ( 標準 tarfile では xattr/ACL は扱えない )
            if os.path.islink(apath):
                ti = tf.gettarinfo(apath, arcname=rel)
                ti.type = tarfile.SYMTYPE
                ti.linkname = os.readlink(apath)
                tf.addfile(ti)
            else:
                tf.add(apath, arcname=rel, recursive=True)
    return tmp


def probe_local_tar_flavor(timeout: float = 5.0) -> RemoteTarFlavor:
    """ローカルの tar 実装 (GNU/bsd) を推定する。"""
    tar_cmd = "tar.exe" if is_windows() else "tar"
    try:
        out = subprocess.check_output([tar_cmd, "--version"], stderr=subprocess.STDOUT, timeout=timeout)
        head = out.decode(errors="ignore").splitlines()[0].lower() if out else ""
    except Exception:
        head = ""
    if "gnu tar" in head:
        return RemoteTarFlavor.GNU
    if "bsdtar" in head or "libarchive" in head or is_windows():
        return RemoteTarFlavor.BSD
    # フォールバック
    return RemoteTarFlavor.BSD

def make_external_tar_gz_from_relmap(
    rel_map: Dict[str, str],
    preserve_owner: bool,
    preserve_acls: bool,
    preserve_xattrs: bool,
    preserve_perms: bool,
    verbose: bool
) -> str:
    """外部 tar コマンドで tar.gz を作成。メタ情報保持が必要な場合はこちらを使用。
    GNU tar の場合は作成時にも --acls/--xattrs を付けて格納する。
    """
    tar_cmd = "tar.exe" if is_windows() else "tar"
    out_path = Path(tempfile.mkstemp(prefix="gm_scatter_", suffix=".tar.gz")[1])

    # 1) manifest ( abs, rel ) を作る
    with tempfile.NamedTemporaryFile("w", delete=False, prefix="gm_manifest_", suffix=".txt", encoding="utf-8") as mf:
        manifest = mf.name
        for ap, rel in rel_map.items():
            mf.write(f"{ap}\t{rel}\n")

    # 2) base ディレクトリごとに rel のリストを作り, 最終的に 1 回の tar 呼び出しで -C / -T を列挙
    listfiles: List[Tuple[str, str]] = []  # [(base, listfile_path)]
    try:
        by_base: Dict[str, List[str]] = {}
        with open(manifest, encoding="utf-8") as rf:
            for ln in rf:
                ln = ln.rstrip("\n")
                if not ln:
                    continue
                ap, rel = ln.split("\t", 1)
                base = os.path.dirname(ap) or "."
                by_base.setdefault(base, []).append(rel)

        for base, rels in by_base.items():
            # listfile は system TMP に作る ( ソースツリーが read-only でも安全 )
            lf = tempfile.NamedTemporaryFile("w", delete=False, prefix="gm_list_", suffix=".txt", encoding="utf-8")
            for rel in rels:
                lf.write(rel + "\n")
            lf.close()
            listfiles.append((base, lf.name))

        # 3) 単発の tar 実行: tar -czf OUT ( -C base1 -T list1 ) ( -C base2 -T list2 ) ...
        cmd: List[str] = [tar_cmd, "-czf", str(out_path)]
        # ローカル tar フレーバに応じて格納オプションを追加 ( GNU のみ確実に効く )
        local_flavor = probe_local_tar_flavor()
        if local_flavor is RemoteTarFlavor.GNU:
            if preserve_acls:
                cmd.append("--acls")
            if preserve_xattrs:
                cmd.append("--xattrs")
        else:
            # 常時通知 ( 暗黙ONでも気づけるように )
            if (preserve_acls or preserve_xattrs):
                print("[local] Warning: The behavior of storing/restoring ACLs/extended attributes in local bsdtar / libarchive is environment-dependent.", file=sys.stderr)
        for base, lfpath in listfiles:
            cmd += ["-C", base, "-T", lfpath]

        if verbose:
            print("[local] exec:", " ".join(shlex.quote(x) for x in cmd))
        subprocess.check_call(cmd)
        return str(out_path)

    finally:
        try:
            os.remove(manifest)
        except Exception:
            pass
        for _base, lfpath in listfiles:
            try:
                os.remove(lfpath)
            except Exception:
                pass

def remote_unpack_tar_gz(ssh: paramiko.SSHClient,
                         sftp: paramiko.SFTPClient,
                         local_tgz: str,
                         remote_dest: str,
                         use_sudo: bool,
                         preserve_owner: bool,
                         preserve_acls: bool,
                         preserve_xattrs: bool,
                         preserve_perms: bool,
                         timeout: float,
                         verbose: bool,
                         flavor: "RemoteTarFlavor") -> int:
    """リモートへ tgz をアップロードし, tar で展開する。

    Args:
        ssh (paramiko.SSHClient): SSH。
        sftp (paramiko.SFTPClient): SFTP。
        local_tgz (str): ローカルの tgz 一時ファイルパス。
        remote_dest (str): リモートの展開先 ( POSIX パス ) 。
        use_sudo (bool): sudo 使用の有無。
        preserve_owner (bool): 所有者復元。
        preserve_acls (bool): ACL 復元。
        preserve_xattrs (bool): xattr 復元。
        preserve_perms (bool): パーミッション復元。
        timeout (float): タイムアウト秒。
        verbose (bool): 冗長出力。

    Returns:
        int: 概算の「1 アーカイブ展開」を 1 として計上して返す。
    """
    ident = posixpath.join("/tmp", f"gm_scatter_{uuid4().hex}_{os.getpid()}.tar.gz")

    # 先に tar の存在を確認 ( アップロードの無駄を避ける )
    rc_tar, _o, _e = run_cmd(ssh, "command -v tar >/dev/null 2>&1", timeout)
    if rc_tar != 0:
        raise RuntimeError("remote host has no 'tar' in PATH")

    # 実装種別 ( GNU/bsd ) は呼び出し元で判別済み
    if verbose:
        print(f"[remote] detected tar flavor: {flavor.kind}")

    sftp.put(local_tgz, ident)
    # 一時アーカイブはデフォルト 600 にしておく ( ログインユーザの umask に依存させない )
    try:
        sftp.chmod(ident, 0o600)
    except Exception:
        pass  # chmod 不可な SFTP 実装でも致命にはしない
    # 長いオプションを先頭側へ, 主動作 ( -xzf ) の前に並べる
    # 古い tar でオプション順序依存がある場合に備えるため
    pre: List[str] = []
    if preserve_perms:
        pre.append("-p")

    if preserve_owner:
        if flavor.supports_same_owner:
            pre.append("--same-owner")
            # UID/GID を名前解決に頼らず数値で適用 ( GNU tar のみ有効 )
            if flavor is RemoteTarFlavor.GNU:
                pre.append("--numeric-owner")
        else:
            # non-GNU: continue without the flag; let caller record warn/error
            pass

    if flavor is RemoteTarFlavor.GNU:
        pre.append("--delay-directory-restore")

    if preserve_acls:
        if flavor.supports_acls:
            pre.append("--acls")
        else:
            pass

    if preserve_xattrs:
        if flavor.supports_xattrs:
            pre.append("--xattrs")
        else:
            pass

    flags: List[str] = [*pre, "-xzf", shlex.quote(ident), "-C", shlex.quote(remote_dest)]
    sudo = "sudo -n " if use_sudo else ""
    cmd = f"{sudo}tar {' '.join(flags)}"

    if verbose:
        print(f"[remote] extract: {cmd}")

    try:
        rc, _o, err = run_cmd(ssh, cmd, timeout)
        if rc != 0:
            raise RuntimeError(f"remote unpack failed: {err.decode(errors='ignore')}")
    finally:
        # 成否に関わらず一時ファイルを削除
        try:
            _rc2, _o2, _e2 = run_cmd(ssh, f"{sudo}rm -f {shlex.quote(ident)}", timeout)
        except Exception:
            pass

    return 1


# ========= 逐次 SFTP put =========
def sftp_put_map(sftp: paramiko.SFTPClient,
                 rel_map: Dict[str, str],
                 remote_dest: str,
                 verbose: bool,
                 empty_dirs: Optional[Set[str]] = None) -> int:
    """SFTP で個別ファイルを put する。メタ情報は保持しない。

    Args:
        sftp (paramiko.SFTPClient): SFTP。
        rel_map (Dict[str, str]): {abs -> rel(POSIX)}。
        remote_dest (str): リモートの基点 ( POSIX パス ) 。
        verbose (bool): 冗長出力。

    Returns:
        int: 実行件数 ( 空ディレクトリ作成 + ファイル put )。
    """
    count = 0
    made: Set[str] = set()

    def ensure_dir(d: str) -> None:
        if d in made:
            return
        try:
            sftp.stat(d)
        except IOError:
            sftp_mkdirs(sftp, d)
        made.add(d)

    # 先に空ディレクトリを作る ( 必要なら )
    if empty_dirs:
        for drel in sorted(empty_dirs):
            if drel.startswith("/"):
                print(f"[Error] unsafe dir rel (leading '/'): {drel}", file=sys.stderr)
                continue
            rdir = posixpath.normpath(f"{remote_dest}/{drel}")
            try:
                ensure = rdir  # ensure_dir はパスが既存でも OK
                try:
                    sftp.stat(ensure)
                except IOError:
                    sftp_mkdirs(sftp, ensure)
                if verbose:
                    print(f"[mkdir(empty)] {rdir}")
                count += 1
            except Exception as e:
                print(f"[Error] mkdir(empty) failed: {rdir}: {e}", file=sys.stderr)

    # ファイルを put する
    for apath, rel in rel_map.items():
        if rel.startswith("/"):
            print(f"[Error] unsafe rel (leading '/'): {rel}", file=sys.stderr)
            continue
        rpath = posixpath.normpath(f"{remote_dest}/{rel}")
        rdir = posixpath.dirname(rpath)
        ensure_dir(rdir)

        existed = True
        try:
            sftp.stat(rpath)
        except IOError:
            existed = False
        if verbose:
            tag = "overwrite" if existed else "new"
            print(f"[put][{tag}] {apath} -> {rpath}")

        try:
            sftp.put(apath, rpath)
            count += 1
        except Exception as e:
            print(f"[Error] sftp.put failed: {apath} -> {rpath}: {e}", file=sys.stderr)
    return count


# ========= SELinux restorecon =========

def maybe_run_restorecon(ssh: paramiko.SSHClient, dest_posix: str, selinux_mode: str, timeout: float, use_sudo: bool, verbose: bool) -> Optional[str]:
    """--selinux の指定に応じて restorecon を実行する。

    Args:
        ssh (paramiko.SSHClient): SSH。
        dest_posix (str): 対象ディレクトリ ( POSIX パス ) 。
        selinux_mode (str): auto/policy/archive/ignore。
        timeout (float): タイムアウト。
        use_sudo (bool): sudo。
        verbose (bool): 冗長。

    Returns:
        Optional[str]: 警告や情報の付記 ( None の場合は特になし ) 。
    """
    if selinux_mode == "ignore":
        return None
    capable = probe_selinux_capable(ssh, timeout)
    if selinux_mode == "policy":
        if not capable:
            raise RuntimeError("SELinux policy required but not available on remote (selinuxfs or restorecon missing).")
        cmd = f"{'sudo -n ' if use_sudo else ''}restorecon -RF {shlex.quote(dest_posix)}"
        if verbose:
            print(f"[remote] restorecon: {cmd}")
        rc, _o, err = run_cmd(ssh, cmd, timeout)
        if rc != 0:
            raise RuntimeError(f"restorecon failed: {err.decode(errors='ignore')}")
        return None
    if selinux_mode in ("auto", "archive"):
        if capable:
            cmd = f"{'sudo -n ' if use_sudo else ''}restorecon -RF {shlex.quote(dest_posix)}"
            if verbose:
                print(f"[remote] restorecon: {cmd}")
            rc, _o, err = run_cmd(ssh, cmd, timeout)
            if rc != 0:
                return f"restorecon failed: {err.decode(errors='ignore')}"
            return None
        return "SELinux not supported on remote; skipped restorecon"
    return None


# ========= ホストごとのメイン処理 =========

def process_host(host: str,
                 dest: str,
                 rel_map: Dict[str, str],
                 ssh_user: str,
                 account: str,
                 port: int,
                 key: Optional[str],
                 password: Optional[str],
                 timeout: float,
                 strict: bool,
                 dry_run: bool,
                 verbose: bool,
                 pack: bool,
                 preserve_owner: bool,
                 preserve_acls: bool,
                 preserve_xattrs: bool,
                 preserve_perms: bool,
                 selinux_mode: str,
                 request_preserve_owner: bool,
                 request_preserve_acls: bool,
                 request_preserve_xattrs: bool,
                 request_preserve_perms: bool,
                 empty_dirs: Optional[Set[str]] = None) -> HostResult:
    """単一ホストの配布処理を実行する。

    Args:
        host (str): ホスト名。
        dest (str): リモートの dest ( 絶対 or 相対 ) 。
        rel_map (Dict[str, str]): {abs -> rel(POSIX)} ( 重複は除去済み ) 。
        ssh_user (str): SSH ログインユーザ。
        account (str): 転送時のアカウント語義 ( HOME 解決等 ) 。
        port (int): SSH ポート。
        key (Optional[str]): 秘密鍵。
        password (Optional[str]): パスワード。
        timeout (float): タイムアウト。
        strict (bool): 厳格ホスト鍵チェック。
        dry_run (bool): ドライラン。
        verbose (bool): 冗長出力。
        pack (bool): --pack。
        preserve_owner (bool): 所有者保持。
        preserve_acls (bool): ACL 保持。
        preserve_xattrs (bool): xattr 保持。
        preserve_perms (bool): パーミッション保持。
        selinux_mode (str): SELinux モード。
        request_preserve_owner (bool): 所有者保持をユーザーが要求したか。
        request_preserve_acls (bool): ACL 保持をユーザーが要求したか。
        request_preserve_xattrs (bool): xattr 保持をユーザーが要求したか。
        request_preserve_perms (bool): パーミッション保持をユーザーが要求したか。
        empty_dirs (Optional[Set[str]]): 空ディレクトリ集合
    Returns:
        HostResult: 結果。
    """
    uploaded = 0
    warnings: List[str] = []
    errors: List[str] = []
    ssh: Optional[paramiko.SSHClient] = None
    sftp: Optional[paramiko.SFTPClient] = None

    try:
        cfg = SSHConfig(
            host=host, port=port, ssh_user=ssh_user,
            key_filename=key, password=password, timeout=timeout,
            strict_host_key_checking=strict,
        )
        ssh = ssh_open(cfg)
        sftp = ssh.open_sftp()

        # dest の解釈：相対なら ssh_user の HOME から
        if dest.startswith("/"):
            remote_dest = posixpath.normpath(dest)
        else:
            home = resolve_remote_home(ssh, account, timeout)
            remote_dest = posixpath.normpath(f"{home}/{dest}")

        use_sudo = (ssh_user != account)

        if dry_run:
            mode = "PACK" if pack else "SFTP"
            extra = f", empty-dirs={len(empty_dirs) if empty_dirs else 0}" if not pack else ""
            print(f"[{host}] DRY-RUN {mode}: -> {remote_dest} files={len(rel_map)}{extra}")
            return HostResult(host=host, uploaded=0, warnings=warnings, errors=errors)

        try:
            ensure_remote_dir(ssh, remote_dest, use_sudo=use_sudo, timeout=timeout)
        except RuntimeError as e:
            # ここで host / ssh_user / account / use_sudo / dest を付与して再スロー
            raise RuntimeError(
                f"{e} (host={host}, ssh_user={ssh_user}, account={account}, "
                f"use_sudo={use_sudo}, dest={remote_dest}). "
                "If sudo is required, configure NOPASSWD for the ssh_user on the remote "
                "or run with --ssh-user equal to --user when appropriate."
            )

        # --- 事前能力チェック / SELinux archive 厳格条件 ---
        # preserve-* の要求有無 ( ユーザーが明示 True か, pack 既定 True 解決後の値 )
        need_preserve = preserve_owner or preserve_acls or preserve_xattrs or preserve_perms

        # SELinux モード archive は pack + xattrs が必須, かつ xattr 書込み能力が必要
        if selinux_mode == "archive":
            if not pack or not preserve_xattrs:
                raise RuntimeError("--selinux=archive requires --pack and --preserve-xattrs (both).")
            if not check_remote_xattr_capable(ssh, timeout):
                raise RuntimeError("remote host not xattr-capable for --selinux=archive (setfattr not found).")

        # ACL/xattr 復元のための補助ツール存在チェック ( 仕様上は警告降格, archive の xattr は厳格 )
        if preserve_acls and not check_remote_tool(ssh, "setfacl", timeout):
            msg = "ACL: remote 'setfacl' not found; ACL restore may be skipped or incomplete."
            (errors if request_preserve_acls else warnings).append(msg)
        if preserve_xattrs and not check_remote_tool(ssh, "setfattr", timeout):
            msg = "xattr: remote 'setfattr' not found"
            if selinux_mode == "archive":
                # archive は厳格
                raise RuntimeError(msg + " (required for --selinux=archive).")
            else:
                (errors if request_preserve_xattrs else warnings).append(msg + "; xattr restore may be skipped.")

        remote_flavor = RemoteTarFlavor.BSD  # デフォルト初期値
        if pack:
            # 事前にリモート tar フレーバを把握 ( 通知用 )
            remote_flavor = probe_remote_tar(ssh, timeout)
            if need_preserve:
                tgz = make_external_tar_gz_from_relmap(
                    rel_map,
                    preserve_owner=preserve_owner,
                    preserve_acls=preserve_acls,
                    preserve_xattrs=preserve_xattrs,
                    preserve_perms=preserve_perms,
                    verbose=verbose,
                )
            else:
                tgz = make_python_tar_gz_from_relmap(rel_map, verbose=verbose)
            try:
                uploaded += remote_unpack_tar_gz(
                    ssh=ssh, sftp=sftp, local_tgz=tgz, remote_dest=remote_dest,
                    use_sudo=use_sudo,
                    preserve_owner=preserve_owner,
                    preserve_acls=preserve_acls,
                    preserve_xattrs=preserve_xattrs,
                    preserve_perms=preserve_perms,
                    timeout=timeout, verbose=verbose,
                    flavor=remote_flavor,
                )
            finally:
                try:
                    os.remove(tgz)
                except Exception:
                    pass
        else:
            uploaded += sftp_put_map(sftp, rel_map, remote_dest, verbose=verbose, empty_dirs=empty_dirs)

        # --- 通知: preserve-* が非 GNU tar で効かない ( 付けられない ) 可能性
        if pack and remote_flavor is not RemoteTarFlavor.GNU:
            def _note(name: str, explicit: bool) -> None:
                msg = f"{name}: non-GNU tar on remote; option may be ignored."
                (errors if explicit else warnings).append(msg)
            if preserve_owner:
                _note("preserve-owner", request_preserve_owner)
            if preserve_acls:
                _note("preserve-acls", request_preserve_acls)
            if preserve_xattrs:
                _note("preserve-xattrs", request_preserve_xattrs)
            if preserve_perms:
                _note("preserve-perms", request_preserve_perms)

        # --- 通知: sudo なしだと preserve の適用に失敗し得る
        if pack and not use_sudo:
            if preserve_owner:
                warnings.append("preserve-owner: may fail without sudo/root privileges.")
            if preserve_perms:
                warnings.append("preserve-perms: some permission restorations may fail without sudo/root privileges.")

        note = maybe_run_restorecon(ssh, remote_dest, selinux_mode=selinux_mode, timeout=timeout, use_sudo=use_sudo, verbose=verbose)
        if note:
            warnings.append(note)

    except Exception as e:
        if verbose:
            traceback.print_exc()
        errors.append(f"{type(e).__name__}: {e}")
    finally:
        try:
            if sftp:
                sftp.close()
        except Exception:
            pass
        try:
            if ssh:
                ssh.close()
        except Exception:
            pass

    return HostResult(host=host, uploaded=uploaded, warnings=warnings, errors=errors)


def build_argparser() -> argparse.ArgumentParser:
    """CLI 引数パーサを構築して返す。

    Returns:
        argparse.ArgumentParser: 引数パーサ。
    """
    ap = argparse.ArgumentParser(
        description="Distribute local files/dirs to multiple remote hosts under a destination directory.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    ap.add_argument("src", nargs="*", help="0 or more local files/dirs. Relative src is resolved from the current directory.")
    ap.add_argument("dest", nargs="?", help="Remote destination directory. If relative, it is resolved from the remote target account's HOME (i.e., --user). This is independent of the SSH login user (--ssh-user).")

    # Selection (local)
    ap.add_argument("-a", "--pattern-abs", action="append", default=[], help="ABSOLUTE local path regex (repeatable).")
    ap.add_argument("-r", "--pattern-rel", action="append", default=[], help="RELATIVE path regex to each --root (repeatable).")
    ap.add_argument("-R", "--root", action="append", default=[], help="Local search root(s). Default: current directory.")
    ap.add_argument("-i", "--ignore-case", action="store_true", help="Compile regexes with IGNORECASE.")

    # Remote & SSH
    ap.add_argument("-H", "--hosts", default="hostfile", help="Hosts file. Default: hostfile.")
    ap.add_argument("-u", "--user", default=getpass.getuser(), help="Target account semantics on remote. Default: local user.")
    ap.add_argument("-s", "--ssh-user", default=None, help="SSH login user. Default: same as --user.")
    ap.add_argument("-P", "--port", type=int, default=DEFAULT_SSH_PORT, help=f"SSH port. Default: {DEFAULT_SSH_PORT}.")
    ap.add_argument("-K", "--key", default=None, help="SSH private key file.")
    ap.add_argument("-W", "--password", default=None, help="SSH password (not recommended).")
    ap.add_argument("-T", "--timeout", type=float, default=DEFAULT_TIMEOUT, help=f"SSH/command timeout seconds. Default: {DEFAULT_TIMEOUT}.")
    ap.add_argument("-S", "--strict-host-key-checking", action="store_true", help="Enable strict host key checking.")

    # Transfer mode
    ap.add_argument("--pack", action="store_true", help="Create local tar.gz -> upload -> remote extract.")
    # preserve-* は --pack 時に未指定なら既定 True。BooleanOptionalAction で --no-xxx も受け付ける。
    ap.add_argument("--preserve-perms", action=argparse.BooleanOptionalAction, default=None,
                    help="Preserve permissions on extract (tar -p). Use --no-preserve-perms to disable.")
    ap.add_argument("--preserve-owner", action=argparse.BooleanOptionalAction, default=None,
                    help="Preserve owner/group on extract (if supported). Use --no-preserve-owner to disable.")
    ap.add_argument("--preserve-acls", action=argparse.BooleanOptionalAction, default=None,
                    help="Preserve ACLs on extract (if supported). Use --no-preserve-acls to disable.")
    ap.add_argument("--preserve-xattrs", action=argparse.BooleanOptionalAction, default=None,
                    help="Preserve xattrs on extract (if supported). Use --no-preserve-xattrs to disable.")
    ap.add_argument("-j", "--parallel", type=int, default=DEFAULT_PARALLEL, help=f"Parallel hosts. Default: {DEFAULT_PARALLEL}.")
    ap.add_argument("-n", "--dry-run", action="store_true", help="Show plan only; do not upload or extract.")
    ap.add_argument("-v", "--verbose", action="store_true", help="Verbose logs.")
    ap.add_argument("--follow-symlinks", action="store_true", help="Follow symlinks when scanning locals.")
    ap.add_argument("--include-empty-dirs", action="store_true",
                    help="SFTP mode: also create empty directories found under explicit src directories.")
    # SELinux
    ap.add_argument("--selinux", choices=["auto", "policy", "archive", "ignore"], default="auto",
                    help="SELinux handling mode. Default: auto.")
    return ap


def main() -> None:
    """エントリポイント。CLI 仕様に従って配布処理を実行する。

    Raises:
        SystemExit: 引数不正や致命的な前提不成立時。
    """
    ap = build_argparser()
    args = ap.parse_args()

    # --- 位置引数 ( 最後が dest ) 解釈 ---
    if args.dest is None:
        if not args.src:
            print("dest is required as the last positional argument.", file=sys.stderr)
            sys.exit(EXIT_INVALID_ARGS)
        args.dest = args.src[-1]
        args.src = args.src[:-1]

    dest: str = args.dest
    src_args: List[str] = list(args.src)

    using_patterns: bool = bool(args.pattern_abs or args.pattern_rel or args.root)
    roots_opt: List[str] = args.root if args.root else [os.getcwd()]

    if not dest:
        print("dest is required.", file=sys.stderr)
        sys.exit(EXIT_INVALID_ARGS)

    hosts = parse_hosts_file(args.hosts)
    if not hosts:
        print("No hosts found in hosts file.", file=sys.stderr)
        sys.exit(EXIT_INVALID_ARGS)

    if len(src_args) == 0 and not (args.pattern_abs or args.pattern_rel):
        print("No src and no patterns provided. At least one src or a pattern must be specified.", file=sys.stderr)
        sys.exit(EXIT_NO_TARGETS)

    flags = re.IGNORECASE if args.ignore_case else 0
    try:
        pat_abs = compile_many(args.pattern_abs, flags)
        pat_rel = compile_many(args.pattern_rel, flags)
    except re.error as e:
        print(f"Invalid regex: {e}", file=sys.stderr)
        sys.exit(EXIT_INVALID_ARGS)

    rel_map: Dict[str, str] = {}
    empty_dirs_rel: Set[str] = set()
    seen_abs: Set[str] = set()

    for s in src_args:
        apath = os.path.abspath(s)
        if not os.path.exists(apath) and not os.path.islink(apath):
            print(f"[Warning] src not found (skipped): {s}", file=sys.stderr)
            continue

        # 絶対指定でも CWD からの安全な相対名に統一 ( Windows の ドライブレター(C:など) 防止 )
        rel_opt = safe_relpath_for_transfer(apath, os.getcwd())
        if rel_opt is None:
            # POSIX 絶対パス ( /から始まる単一ファイル ) の場合は
            # 先頭の'/'を1個だけ剥いだ相対名にする ( 例: /etc/hosts -> etc/hosts ) 。
            # それ以外 ( 例: Windows の C:\... 等 ) は basename にフォールバックする。
            if os.path.isabs(apath) and apath.startswith(os.sep):
                # "/etc/hosts" -> "etc/hosts"
                # lstrip で先頭スラッシュ群を削るが, 通常は1個のみ想定。
                # 空文字列になる場合は, ( "/"単体など ) basename に変換する。
                _rel = apath.lstrip(os.sep)
                if _rel:
                    rel = to_posix_rel(_rel.replace(os.sep, "/"))
                else:
                    rel = to_posix_rel(os.path.basename(apath))
            else:
                # 非 POSIX 絶対パス ( 例: "C:\\path\\to\\file" ) はbasenameに変換する。
                rel = to_posix_rel(os.path.basename(apath))
        else:
            rel = to_posix_rel(rel_opt)

        # srcオプションでディレクトリを指定した場合にSFTP転送向けにファイルパスを展開
        # ( --pack なしの場合のみ )
        if os.path.isdir(apath) and not args.pack:
            for root, _dirs, files in os.walk(apath, followlinks=args.follow_symlinks):
                # ディレクトリ側のシンボリックリンクも, --follow-symlinks が無い場合は安全側でスキップ
                if os.path.islink(root) and not args.follow_symlinks:
                    print(f"[Warning] symlinked directory skipped (SFTP mode without --follow-symlinks): {root}", file=sys.stderr)
                    continue

                for name in files:
                    f_abs = os.path.abspath(os.path.join(root, name))

                    # 1) 壊れたシンボリックリンクは除外 ( 既存 )
                    if os.path.islink(f_abs) and not os.path.exists(f_abs):
                        print(f"[Warning] broken symlink (skipped): {f_abs}", file=sys.stderr)
                        continue

                    # 1.5) SFTPモードで --follow-symlinks なしなら, ファイル型のシンボリックリンクも除外
                    if os.path.islink(f_abs) and not args.follow_symlinks:
                        print(f"[Warning] symlink (skipped on SFTP without --follow-symlinks): {f_abs}", file=sys.stderr)
                        continue

                    # 2) 通常ファイル/シンボリックリンク以外は除外 ( 既存 )
                    try:
                        st = os.lstat(f_abs)
                        if not stat.S_ISREG(st.st_mode) and not os.path.islink(f_abs):
                            print(f"[Warning] non-regular file (skipped): {f_abs}", file=sys.stderr)
                            continue
                    except FileNotFoundError:
                        print(f"[Warning] vanished during scan (skipped): {f_abs}", file=sys.stderr)
                        continue

                    # apath からの相対を作って rel の下にぶら下げる
                    under = os.path.relpath(f_abs, apath).replace(os.sep, "/")
                    f_rel = to_posix_rel(posixpath.normpath(f"{rel}/{under}"))
                    # 安全チェック
                    if (not f_rel) or f_rel.startswith("/") or f_rel == ".." or f_rel.startswith("../"):
                        print(f"[Error] unsafe derived relative path: {f_abs} -> {f_rel}", file=sys.stderr)
                        continue
                    # 重複チェック
                    if f_abs in seen_abs:
                        print(f"[Warning] duplicate src ignored: {f_abs}", file=sys.stderr)
                        continue
                    seen_abs.add(f_abs)
                    rel_map[f_abs] = f_rel
                # 空ディレクトリ作成 ( SFTPのみ, 要求時 )
                if args.include_empty_dirs and not files and not _dirs:
                    # root は apath 配下。apath からの相対を rel の下に付与
                    d_under = os.path.relpath(root, apath).replace(os.sep, "/")
                    d_rel = to_posix_rel(posixpath.normpath(f"{rel}/{d_under}"))
                    if d_rel and not d_rel.startswith("/") and d_rel != ".." and not d_rel.startswith("../"):
                        empty_dirs_rel.add(d_rel)
                    else:
                        print(f"[Error] unsafe empty dir rel: {root} -> {d_rel}", file=sys.stderr)
            continue

        # 通常 ( ファイル or --pack 時のディレクトリ ) はそのまま登録
        if apath in seen_abs:
            print(f"[Warning] duplicate src ignored: {apath}", file=sys.stderr)
            continue
        seen_abs.add(apath)
        rel_map[apath] = rel

    roots_abs = [os.path.abspath(os.path.expanduser(r)) for r in roots_opt]
    for r in roots_abs:
        if not os.path.exists(r):
            print(f"[Error] root not found: {r}", file=sys.stderr)
            sys.exit(EXIT_INVALID_ARGS)

    if using_patterns:
        pat_map, _hits = select_local_by_patterns(
            roots=roots_abs,
            pat_abs=pat_abs,
            pat_rel=pat_rel,
            follow_symlinks=args.follow_symlinks,
            verbose=args.verbose,
        )
        for apath, rel in pat_map.items():
            if apath in seen_abs:
                print(f"[Warning] duplicate (pattern vs src) ignored: {apath}", file=sys.stderr)
                continue
            seen_abs.add(apath)
            rel_map[apath] = rel

    if not rel_map and not (args.include_empty_dirs and empty_dirs_rel and not args.pack):
        print("No local files to distribute after selection.", file=sys.stderr)
        sys.exit(EXIT_NO_TARGETS)

    # preserve-* 既定値の決定：
    #   - pack のとき: 未指定(None)  =>  True / 指定(True)  =>  True
    #   - pack でない: すべて False ( SFTP では保持しない )

    # 明示 True を保持（警告/エラーの強度切替に使う）
    user_set_preserve_perms_true  = (args.preserve_perms  is True)
    user_set_preserve_owner_true  = (args.preserve_owner  is True)
    user_set_preserve_acls_true   = (args.preserve_acls   is True)
    user_set_preserve_xattrs_true = (args.preserve_xattrs is True)

    if args.pack:
        # 未指定(None) のとき既定 True、明示指定があればその値を尊重
        preserve_perms  = True if args.preserve_perms  is None else bool(args.preserve_perms)
        preserve_owner  = True if args.preserve_owner  is None else bool(args.preserve_owner)
        preserve_acls   = True if args.preserve_acls   is None else bool(args.preserve_acls)
        preserve_xattrs = True if args.preserve_xattrs is None else bool(args.preserve_xattrs)
    else:
        preserve_perms = preserve_owner = preserve_acls = preserve_xattrs = False
    if (args.preserve_perms or args.preserve_owner or args.preserve_acls or args.preserve_xattrs) and not args.pack:
        print("[Warning] --preserve-* options are ignored without --pack.", file=sys.stderr)

    require_local_tar_when_preserve(preserve_owner, preserve_acls, preserve_xattrs, preserve_perms)

    ssh_user = args.ssh_user or args.user

    print(f"Hosts: {len(hosts)}  Mode: {'PACK' if args.pack else 'SFTP'}  Dest: {dest}")
    if using_patterns:
        print(f"Select: patterns ({len(args.pattern_abs)} abs, {len(args.pattern_rel)} rel) roots={', '.join(roots_opt)}")
    else:
        print(f"Sources: {len(src_args)} (explicit)")
    print(f"SSH  : ssh-user={ssh_user} user={args.user} port={args.port} strict={args.strict_host_key_checking}")
    print(f"Preserve: perms={preserve_perms} owner={preserve_owner} acls={preserve_acls} xattrs={preserve_xattrs}")
    print(f"SELinux: {args.selinux}")

    results: List[HostResult] = []
    interrupted = False
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.parallel)) as ex:
        futs = [
            ex.submit(
                process_host,
                host=h,
                dest=dest,
                rel_map=rel_map,
                ssh_user=ssh_user,
                account=args.user,
                port=args.port,
                key=args.key,
                password=args.password,
                timeout=args.timeout,
                strict=args.strict_host_key_checking,
                dry_run=args.dry_run,
                verbose=args.verbose,
                pack=args.pack,
                preserve_owner=preserve_owner,
                preserve_acls=preserve_acls,
                preserve_xattrs=preserve_xattrs,
                preserve_perms=preserve_perms,
                selinux_mode=args.selinux,
                request_preserve_owner=user_set_preserve_owner_true,
                request_preserve_acls=user_set_preserve_acls_true,
                request_preserve_xattrs=user_set_preserve_xattrs_true,
                request_preserve_perms=user_set_preserve_perms_true,
                empty_dirs=(empty_dirs_rel if (args.include_empty_dirs and not args.pack) else None),
            )
            for h in hosts
        ]

        try:
            for fut in concurrent.futures.as_completed(futs):
                res = fut.result()
                results.append(res)
                if res.errors:
                    print(f"[{res.host}] ERROR x{len(res.errors)}")
                else:
                    action = "planned" if args.dry_run else ("packed" if args.pack else "uploaded")
                    print(f"[{res.host}] {action}: {res.uploaded}")
                for w in res.warnings:
                    print(f"[{res.host}] Warning: {w}", file=sys.stderr)
                for e in res.errors:
                    print(f"[{res.host}] Error: {e}", file=sys.stderr)
        except KeyboardInterrupt:
            print("\n[Info] Interrupted by user. Cancelling remaining tasks...", file=sys.stderr)
            for fut in futs:
                fut.cancel()
            interrupted = True
            # 部分結果でサマリするため, そのまま抜ける

    total_uploaded = sum(r.uploaded for r in results if not r.errors)
    warn_hosts = [r for r in results if r.warnings]
    err_hosts = [r for r in results if r.errors]

    print("\n=== Summary ===")
    print(f"Hosts processed: {len(results)}")
    label = 'planned' if args.dry_run else ('packed(archives)' if args.pack else 'files/dirs created')
    print(f"Total {label}: {total_uploaded}")
    if warn_hosts:
        warn_count = sum(len(r.warnings) for r in warn_hosts)
        print(f"Warnings: {warn_count} on {len(warn_hosts)} host(s)")
    if err_hosts:
        err_count = sum(len(r.errors) for r in err_hosts)
        print(f"Errors (continuing): {err_count} on {len(err_hosts)} host(s)")

    if interrupted or err_hosts:
        sys.exit(EXIT_PARTIAL)

    sys.exit(EXIT_SUCCESS)


if __name__ == "__main__":
    socket.setdefaulttimeout(DEFAULT_SOCKET_TIMEOUT)
    main()
