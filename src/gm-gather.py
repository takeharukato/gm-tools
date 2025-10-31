#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Copyright 2025 Takeharu KATO.
# SPDX-License-Identifier: BSD-2-Clause
# Notes: Portions of this codebase were initially drafted with ChatGPT assistance.
#
"""
使用方法: gm-gather.py [-h] [--user USER] [--hosts HOSTS] [--ssh-user SSH_USER][--pattern-abs PATTERN_ABS] [--pattern-rel PATTERN_REL][--parallel PARALLEL] [--ignore-case][--roots [ROOTS ...]] [--port PORT] [--key KEY][--password PASSWORD] [--timeout TIMEOUT][--strict-host-key-checking] [--pack] [--one-archive][--dry-run] [--verbose]dest正規表現によるリモートファイル収集 ( 絶対パス・相対パスパターン対応 ) 。位置引数:destローカル保存先ディレクトリ。オプション:
  --help, -h
    ヘルプメッセージを表示して終了
  --user USER, -u
    USERターゲットアカウント ( '~'解決に使用 )  ( デフォルト: 現在のユーザー ) 。
  --hosts HOSTS, -H
    ホストファイル ( デフォルト: hostfile ) 。
  --ssh-user SSH_USER, -s SSH_USER
    SSHログインユーザー ( デフォルト: --userと同じ ) 。
  --pattern-abs PATTERN_ABS, -a PATTERN_ABS
    絶対リモートパス用の正規表現。繰り返し指定可。
  --pattern-rel PATTERN_REL, -r PATTERN_REL
    各 --roots エントリに対する相対パス用正規表現。複数指定可。
  --parallel PARALLEL, -j PARALLEL
    同時実行ホスト数 ( デフォルト: 4 ) 。
  --ignore-case, -i
    正規表現を re.IGNORECASE でコンパイル。
  --roots [ROOTS ...], -R [ROOTS ...]
    検索ルート (デフォルト: '~' => USER の ホームディレクトリ)。絶対パスを使用。複数指定可。
  --port PORT, -P PORT
    SSH ポート (デフォルト: 22)。
  --key KEY, -K KEY
    SSH秘密鍵ファイル。
  --password PASSWORD, -W PASSWORD
    SSHパスワード ( 推奨されません ) 。
  --timeout TIMEOUT, -T TIMEOUT
    SSH/コマンドのタイムアウト ( 秒単位, デフォルト: 30 ) 。
  --strict-host-key-checking, -S
    厳格なホスト鍵チェックを有効化 ( デフォルト無効 ) 。
  --pack
    ログインユーザと異なるユーザ権限でファイルを収集する場合, または, --pack が明示指定されている場合は,
    リモートホスト上でtarコマンドを使用して圧縮し, その後ダウンロードして展開します (リモート圧縮)。
  --one-archive
    リモート圧縮時に, 全てのルートディレクトリを単一のtarファイルに結合します
    ( 相対パス名が重複すると衝突する可能性があります ) 。
    注: one-archive=false の場合でも, 各 ROOT のアーカイブを <dest>/<host>/rel/ に順次展開するため,
        異なる ROOT に同じ相対パスが存在すると後から展開されたファイルで上書きされます。
  --dry-run, -n
    一致する項目のみを一覧表示し, ダウンロードは行いません。
  --verbose, -v
    詳細ログを出力します。

- ホストファイルの形式:
  1行に1ホスト名を記載する。
  '#'でコメント開始,  空白またはタブの後に'#'がある場合 (例:' #'), それ以降をコメントとして扱う。
- SSHログインアカウント(SSH_USER)と, ファイル収集時に使用するアカウント(USER)とが異なる場合は,
  '--ssh-user'にSSHログインアカウントを指定し, '--user'にファイル収集時に使用するアカウントを指定する。
- SSH_USER と USER とが異なる場合, ファイルのリスト表示/パッキングには sudo (-n) が使用されます。
- 正規表現で指定可能なパスの種類は2つ:
  --pattern-abs 正規表現 : 絶対リモートパスに適用 ( 例: '^/etc/.*\\.yaml$' )
  --pattern-rel 正規表現 : 各 --roots エントリに対する相対パスに適用 (例: '^\\.git/config$')
  いずれか一方, または, 両方を複数回指定可能。いずれかのパターンに一致すればファイルはマッチする。
- ダウンロードのレイアウト:
  - 相対パス一致の場合, <dest>/<host>/rel/<一致したルートからの相対パス>
  - 絶対パスのみ一致の場合, <dest>/<host>/abs/<先頭の '/' を除いた絶対パス>
     <dest> は, dest位置引数で指定したローカルの保存先ディレクトリ。
     <host> は, ホストファイル中に記載されたホスト名。

終了コード定義:
 - 0 正常終了
 - 1 ファイル未取得
 - 2 部分的にファイル取得
 - 3 一致するファイルなし
 - 4 モジュール未インストール
 - 5 無効な引数

依存パッケージ:
  - Paramiko (python3-paramiko パッケージ (AlmaLinux/Ubuntu) または pip install paramiko)

Usage: gm-gather.py [-h] [--user USER] [--hosts HOSTS] [--ssh-user SSH_USER] [--pattern-abs PATTERN_ABS] [--pattern-rel PATTERN_REL] [--parallel PARALLEL] [--ignore-case][--roots [ROOTS ...]] [--port PORT] [--key KEY][--password PASSWORD] [--timeout TIMEOUT][--strict-host-key-checking] [--pack] [--one-archive][--dry-run] [--verbose]dest Remote file collection using regular expressions (supports absolute and relative path patterns). Position argument: dest Local destination directory. Options:
--help, -h
  Display help message and exit
--user USER, -u
  USER Target account (used for ‘~’ resolution) (default: current user).
--hosts HOSTS, -H
  Hosts file (default: hostfile).
--ssh-user SSH_USER, -s SSH_USER
  SSH login user (default: same as --user).
--pattern-abs PATTERN_ABS, -a PATTERN_ABS
  Regular expression for absolute remote paths. Can be specified multiple times.
--pattern-rel PATTERN_REL, -r PATTERN_REL
  Regular expression for relative paths for each --roots entry. Multiple entries allowed.
--parallel PARALLEL, -j PARALLEL
  Number of hosts to run concurrently (default: 4).
--ignore-case, -i
  Compile regular expressions with re.IGNORECASE.
--roots [ROOTS ...], -R [ROOTS ...]
  Search roots (default: ‘~’ => USER's home directory). Use absolute paths. Multiple entries allowed.
--port PORT, -P PORT
  SSH port (default: 22).
--key KEY, -K KEY
  SSH private key file.
--password PASSWORD, -W PASSWORD
  SSH password (not recommended).
--timeout TIMEOUT, -T TIMEOUT
  SSH/command timeout (in seconds, default: 30).
--strict-host-key-checking, -S
  Enable strict host key checking (disabled by default).
--pack
  When collecting files with user permissions different from the login user, or when --pack is explicitly specified,
  the tar command is used on the remote host to compress the files, which are then downloaded and extracted.
--one-archive
  Combines all root directories into a single tar file during remote compression (conflicts may occur if relative pathnames duplicate).
--dry-run, -n
  List only matching items; do not download.
--verbose, -v
  Output detailed logs.

- Host file format:
One hostname per line.
‘#’ starts a comment; if ‘#’ follows a space (‘ #’), everything after is treated as a comment.
- If the SSH login account (SSH_USER) differs from the account used for file collection (USER),
specify the SSH login account with ‘--ssh-user’ and the file collection account with ‘--user’.
- When SSH_USER and USER differ, sudo (-n) is used for listing/packing files.
- Two types of paths can be specified with regular expressions:
  --pattern-abs regular expression: Applies to absolute remote paths (e.g., ‘^/etc/.*\\.yaml$’)
  --pattern-rel regular expression: Applies to relative paths for each --roots entry (e.g., ‘^\\.git/config$’)
  You can specify either one, both, or multiple times. A file matches if it fits any pattern.
- Download layout:
- For relative path matches: <dest>/<host>/rel/<relative path from the matched root>
- For absolute path matches only: <dest>/<host>/abs/<absolute path without leading ‘/’>
  <dest> is the local destination directory specified by the `dest` positional argument.
  <host> is the hostname listed in the host file.

Exit code definitions:
- 0 Normal exit
- 1 File not retrieved
- 2 Partial file retrieval
- 3 No matching files
- 4 Module not installed
- 5 Invalid argument

Dependencies:
  - Paramiko (python3-paramiko package (AlmaLinux/Ubuntu) or pip install paramiko)
"""

import argparse
import concurrent.futures
import os
import re
import shlex
import socket
import sys
import tarfile
import tempfile
import traceback
import getpass
import random
from dataclasses import dataclass
from typing import List, Tuple, Optional, Iterable, Set, Dict, Union
from typing import Pattern

# デフォルト値定義
# SSHポート
DEFAULT_SSH_PORT = 22
# 並行実行数
DEFAULT_PARALLEL = 4
# タイムアウト秒数
DEFAULT_TIMEOUT = 30.0  # seconds

# 終了コード定義
# 0: 正常終了
# 1: ファイル未取得
# 2: 部分的にファイル取得
# 3: 一致するファイルなし
# 4: モジュール未インストール
# 5: 無効な引数
EXIT_CODE_SUCCESS = 0
EXIT_CODE_NO_FETCH = 1
EXIT_CODE_PARTIAL_FETCH = 2
EXIT_CODE_NO_MATCH = 3
EXIT_CODE_MODULE_MISSING = 4
EXIT_CODE_INVALID_ARGS = 5

try:
    import paramiko
except ImportError:
    print("This script requires 'paramiko'. Install via OS package (python3-paramiko) or pip.", file=sys.stderr)
    sys.exit(EXIT_CODE_MODULE_MISSING)


# ------------------------- Tar helpers -------------------------
def safe_extract(tf: tarfile.TarFile, base_dir: str) -> int:
    """
    安全な展開: 先頭の'/'除去 & 正規化し, '..' で外へ出るパスや絶対パスを拒否。
    シンボリックリンクは展開しません。
    """
    cnt = 0
    for m in tf.getmembers():
        # 正規化 ( 先頭'/'除去 ) とパストラバーサル防止
        name_norm = os.path.normpath(m.name.lstrip("/"))
        if name_norm.startswith(".." + os.sep) or os.path.isabs(name_norm):
            continue
        # 危険なリンクや特殊ファイルは展開しない
        if m.issym() or m.islnk():
            continue
        if not (m.isdir() or m.isfile()):
            continue
        m.name = name_norm
        tf.extract(m, path=base_dir)
        if m.isfile():
            cnt += 1
    return cnt

# ------------------------- SSH helpers -------------------------

@dataclass
class SSHConfig:
    host: str
    port: int
    ssh_user: str
    key_filename: Optional[str]
    password: Optional[str]
    timeout: float
    strict_host_key_checking: bool  # default False (AutoAdd)


def ssh_open(cfg: SSHConfig) -> paramiko.SSHClient:
    cli = paramiko.SSHClient()
    # Strict host key checking disabled by default
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
    _stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read()
    err = stderr.read()
    rc = stdout.channel.recv_exit_status()
    return rc, out, err


def remote_home_of_account(ssh: paramiko.SSHClient, account: str, timeout: float) -> str:
    rc, out, _ = run_cmd(ssh, f"getent passwd {shlex.quote(account)} | cut -d: -f6", timeout)
    if rc == 0:
        home = out.decode().strip()
        if home.startswith("/") and len(home) > 1:
            return home
    return "/root" if account == "root" else f"/home/{account}"


# ------------------------- Host list -------------------------

def parse_hosts_file(path: str) -> List[str]:
    hosts: List[str] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            # 空白 ( スペース/タブ等 ) に続く '#' 以降はコメント
            s = re.split(r"\s+#", s, 1)[0].strip()
            hosts.append(s)
    return hosts


# ------------------------- File listing & packing -------------------------

def list_files_remote(ssh: paramiko.SSHClient, roots: List[str], use_sudo: bool, timeout: float) -> List[str]:
    if not roots:
        raise ValueError("roots must not be empty")
    qroots = " ".join(shlex.quote(r) for r in roots)
    base_cmd = f"LC_ALL=C find {qroots} -xdev -type f -print0 2>/dev/null"
    cmd = f"sudo -n {base_cmd}" if use_sudo else base_cmd
    rc, out, err = run_cmd(ssh, f"{cmd}", timeout)
    if rc != 0:
        raise RuntimeError(f"find failed (rc={rc}): {err.decode(errors='ignore')}")
    if not out:
        return []
    return [p for p in out.decode("utf-8", errors="").split("\x00") if p]

def _best_root_for(abs_path: str, roots: List[str]) -> str:
    """abs_path に最も長く一致する root を返す ( なければ空文字ではなく '/' を返す ) 。"""
    best = ""
    for r in roots:
        rn = r.rstrip("/") or "/"
        pref = "/" if rn == "/" else rn + "/"
        if abs_path == rn or abs_path.startswith(pref):
            if len(rn) > len(best):
                best = rn
    return best or "/"

def _group_hits_by_root(hits: Iterable[str], roots: List[str]) -> Dict[str, List[str]]:
    """絶対パスのヒット集合を, root ごとの相対パス配列に束ねる。"""
    grouped: Dict[str, List[str]] = {}
    for p in sorted(hits):
        base = _best_root_for(p, roots)
        rel = p[len(base):].lstrip("/") if base != "/" else p.lstrip("/")
        grouped.setdefault(base, []).append(rel or ".")
    return grouped

def pack_matches_remote_bsdtar(
    ssh: paramiko.SSHClient,
    hits_by_root: Dict[str, List[str]],
    use_sudo: bool,
    timeout: float,
    one_archive: bool,
    verbose: bool,
) -> List[str]:
    """
    リモートホストの tar をリモートで実行し, .tar.gz を作る。
    一部のアーカイバで, --transformで圧縮された.tar.gzの展開に失敗する問題を回避する目的から,
    --transform等のGNU tar固有のオプションは使用しない (bsdtarを想定)。
    - root ごとに `cd <root> ; tar -cf|-rf <tmp>.tar <rel...>` を繰り返し, 最後に gzip -f。
    - one_archive=True の場合は全 root を 1 つの .tar に追記してから gzip。
    戻り値: リモートに生成された .tar.gz パスのリスト。
    """

    sudo = "sudo -n " if use_sudo else ""
    CHUNK = 200  # 引数長対策：ファイルが多いときは分割して -rf 追記
    tgzs: List[str] = []

    if one_archive:
        ident = f"/tmp/collect_{os.getpid()}_{random.randint(10**6,10**7-1)}"
        tar_path = f"{ident}.tar"
        first = True
        for root, rels in hits_by_root.items():
            if not rels:
                continue
            # 改行を含むパスは除外 ( ヒアドキュメント安全性のため )
            clean = [p for p in rels if "\n" not in p]
            for i in range(0, len(clean), CHUNK):
                chunk = clean[i:i+CHUNK]
                # リストファイルを使って tar -T で投入 ( 改行区切り )
                list_ident = f"/tmp/collect_list_{os.getpid()}_{random.randint(10**6,10**7-1)}.lst"
                list_content = "\n".join(chunk) + "\n"
                rc, _, err = run_cmd(ssh, f"{sudo}sh -c 'LC_ALL=C cd {shlex.quote(root)} && cat > {shlex.quote(list_ident)} <<\"EOF\"\n{list_content}EOF'", timeout)
                if rc != 0:
                    raise RuntimeError(f"prepare list failed: {err.decode(errors='ignore')}")
                op = "c" if first else "r"
                cmd = f"{sudo}sh -c 'LC_ALL=C cd {shlex.quote(root)} && tar -{op}f {shlex.quote(tar_path)} -T {shlex.quote(list_ident)} && rm -f {shlex.quote(list_ident)}'"
                rc, _, err = run_cmd(ssh, cmd, timeout)
                if rc != 0:
                    raise RuntimeError(f"tar failed: {err.decode(errors='ignore')}")
                first = False
        rc, _, err = run_cmd(ssh, f"{sudo}gzip -f {shlex.quote(tar_path)}", timeout)
        if rc != 0:
            raise RuntimeError(f"gzip failed: {err.decode(errors='ignore')}")
        tgzs.append(f"{tar_path}.gz")
        return tgzs

    # root ごとに個別アーカイブ
    for root, rels in hits_by_root.items():
        if not rels:
            continue
        ident = f"/tmp/collect_{os.getpid()}_{random.randint(10**6,10**7-1)}"
        tar_path = f"{ident}.tar"
        first = True
        clean = [p for p in rels if "\n" not in p]
        for i in range(0, len(clean), CHUNK):
            chunk = clean[i:i+CHUNK]
            list_ident = f"/tmp/collect_list_{os.getpid()}_{random.randint(10**6,10**7-1)}.lst"
            list_content = "\n".join(chunk) + "\n"
            rc, _, err = run_cmd(ssh, f"{sudo}sh -c 'LC_ALL=C cd {shlex.quote(root)} && cat > {shlex.quote(list_ident)} <<\"EOF\"\n{list_content}EOF'", timeout)
            if rc != 0:
                raise RuntimeError(f"prepare list failed: {err.decode(errors='ignore')}")
            op = "c" if first else "r"
            cmd = f"{sudo}sh -c 'LC_ALL=C cd {shlex.quote(root)} && tar -{op}f {shlex.quote(tar_path)} -T {shlex.quote(list_ident)} && rm -f {shlex.quote(list_ident)}'"
            rc, _, err = run_cmd(ssh, cmd, timeout)
            if rc != 0:
                raise RuntimeError(f"tar failed: {err.decode(errors='ignore')}")
            first = False
        rc, _, err = run_cmd(ssh, f"{sudo}gzip -f {shlex.quote(tar_path)}", timeout)
        if rc != 0:
            raise RuntimeError(f"gzip failed: {err.decode(errors='ignore')}")
        tgzs.append(f"{tar_path}.gz")
    return tgzs

def pack_abs_hits_remote_bsdtar(
    ssh: paramiko.SSHClient,
    abs_hits: List[str],
    use_sudo: bool,
    timeout: float,
    one_archive: bool,
    verbose: bool,
) -> List[str]:
    """
    絶対パスヒットを, リモートの tar ( GNU tar / bsdtar ) で tar.gz 化。
    - 絶対名を保持するため tar に -P ( --absolute-paths ) を使用。
    - one_archive=True なら 1つの .tar.gz に集約。
    - one_archive=False なら CHUNK ごとに複数の .tar.gz を生成して返す。
    戻り値: リモートに生成された .tar.gz パスのリスト。
    """
    sudo = "sudo -n " if use_sudo else ""
    CHUNK = 200
    tgzs: List[str] = []

    if not abs_hits:
        return tgzs

    # 改行を含むパスは除外 ( ヒアドキュメント安全性のため )
    clean = [p for p in abs_hits if "\n" not in p]

    if one_archive:
        # 単一アーカイブに集約
        ident = f"/tmp/collect_abs_{os.getpid()}_{random.randint(10**6,10**7-1)}"
        tar_path = f"{ident}.tar"
        first = True

        for i in range(0, len(clean), CHUNK):
            chunk = clean[i:i + CHUNK]
            list_ident = f"/tmp/collect_list_{os.getpid()}_{random.randint(10**6,10**7-1)}.lst"
            list_content = "\n".join(chunk) + "\n"

            # リストファイル投入
            rc, _, err = run_cmd(
                ssh,
                f"{sudo}sh -c 'LC_ALL=C cat > {shlex.quote(list_ident)} <<\"EOF\"\n{list_content}EOF'",
                timeout,
            )
            if rc != 0:
                raise RuntimeError(f"prepare abs list failed: {err.decode(errors='ignore')}")

            # 1回目は -c, 以降は -r で追記
            op = "c" if first else "r"
            cmd = (
                f"{sudo}sh -c 'LC_ALL=C tar -P -{op}f {shlex.quote(tar_path)} "
                f"-T {shlex.quote(list_ident)} && rm -f {shlex.quote(list_ident)}'"
            )
            rc, _, err = run_cmd(ssh, cmd, timeout)
            if rc != 0:
                raise RuntimeError(f"tar(abs) failed: {err.decode(errors='ignore')}")
            first = False

        # gzip 圧縮
        rc, _, err = run_cmd(ssh, f"{sudo}gzip -f {shlex.quote(tar_path)}", timeout)
        if rc != 0:
            raise RuntimeError(f"gzip(abs) failed: {err.decode(errors='ignore')}")
        tgzs.append(f"{tar_path}.gz")

    else:
        # 複数アーカイブ ( CHUNK 単位 )
        part = 0
        for i in range(0, len(clean), CHUNK):
            chunk = clean[i:i + CHUNK]
            base = f"/tmp/collect_abs_{os.getpid()}_{random.randint(10**6,10**7-1)}_{part}"
            tar_path = f"{base}.tar"
            list_ident = f"{base}.lst"
            list_content = "\n".join(chunk) + "\n"

            # リストファイル投入
            rc, _, err = run_cmd(
                ssh,
                f"{sudo}sh -c 'LC_ALL=C cat > {shlex.quote(list_ident)} <<\"EOF\"\n{list_content}EOF'",
                timeout,
            )
            if rc != 0:
                raise RuntimeError(f"prepare abs list failed: {err.decode(errors='ignore')}")

            # 個別に tar を作る ( 毎回 -c )
            cmd = (
                f"{sudo}sh -c 'LC_ALL=C tar -P -cf {shlex.quote(tar_path)} "
                f"-T {shlex.quote(list_ident)} && rm -f {shlex.quote(list_ident)}'"
            )
            rc, _, err = run_cmd(ssh, cmd, timeout)
            if rc != 0:
                raise RuntimeError(f"tar(abs) failed: {err.decode(errors='ignore')}")

            # gzip 圧縮
            rc, _, err = run_cmd(ssh, f"{sudo}gzip -f {shlex.quote(tar_path)}", timeout)
            if rc != 0:
                raise RuntimeError(f"gzip(abs) failed: {err.decode(errors='ignore')}")

            tgzs.append(f"{tar_path}.gz")
            part += 1

    return tgzs

def download_and_extract(
    sftp: paramiko.SFTPClient,
    remote_tar_path: str,
    host: str,
    dest_root: str,
    verbose: bool,
) -> int:
    # host 引数が "hostname/rel" や "hostname/abs" を含む想定
    os.makedirs(os.path.join(dest_root, host), exist_ok=True)
    # 一時ファイルの prefix にパス区切りが入らないように正規化
    safe_prefix = host.replace(os.sep, "_")
    with tempfile.NamedTemporaryFile(prefix=f"{safe_prefix}_", suffix=".tar.gz", delete=False) as tmpf:
        local_tar = tmpf.name
    try:
        if verbose:
            print(f"[{host}] downloading {remote_tar_path} -> {local_tar}")
        sftp.get(remote_tar_path, local_tar)

        count = 0
        with tarfile.open(local_tar, mode="r:gz") as tf:
            base = os.path.join(dest_root, host)
            count = safe_extract(tf, base)
            return count
    finally:
        try: os.remove(local_tar)
        except Exception: pass


# ------------------------- Matching helpers -------------------------

def compile_many(patterns: List[str], flags: int) -> List[Pattern[str]]:
    out: List[Pattern[str]] = []
    for p in patterns:
        out.append(re.compile(p, flags))
    return out


def match_any_abs(abs_path: str, abs_regexes: List[Pattern[str]]) -> bool:
    return any(r.search(abs_path) for r in abs_regexes)


def iter_relatives(abs_path: str, roots: List[str]) -> Iterable[Tuple[str, str]]:
    """
    Yield (root, rel_path) for each root that is a prefix of abs_path.
    Roots and abs_path are POSIX-style.
    """
    for root in roots:
        root_norm = root.rstrip("/")
        if root_norm == "":
            continue
        if abs_path == root_norm or abs_path.startswith(root_norm + "/"):
            rel = abs_path[len(root_norm) + (0 if abs_path == root_norm else 1):]
            yield (root_norm, rel)


def match_any_rel(abs_path: str, roots: List[str], rel_regexes: List[Pattern[str]]) -> bool:
    if not rel_regexes:
        return False
    for _, rel in iter_relatives(abs_path, roots):
        if any(r.search(rel) for r in rel_regexes):
            return True
    return False


# ------------------------- Per-host processing -------------------------
def _precheck_remote_tools(ssh: paramiko.SSHClient, use_sudo: bool, timeout: float) -> None:
    """
    リモートで必要なコマンド群の存在を事前検査。
    - tar, gzip の存在
    - sudo を使う場合は sudo -n 実行可否
    """
    rc, _, err = run_cmd(ssh, "command -v tar >/dev/null 2>&1 && command -v gzip >/dev/null 2>&1", timeout)
    if rc != 0:
        raise RuntimeError(f"Required command(s) not found on remote: tar and/or gzip. {err.decode(errors='ignore')}")
    if use_sudo:
        rc, _, err = run_cmd(ssh, "sudo -n true", timeout)
        if rc != 0:
            raise RuntimeError("sudo -n is not available for this user on remote")

def _close_quietly(obj: Optional[Union[paramiko.SSHClient, paramiko.SFTPClient]]) -> None:
    """
    例外を無視して静かに close する。
    Paramiko の SSHClient / SFTPClient いずれにも対応。
    """
    if obj is None:
        return
    try:
        # SFTPClient/SSHClient は close() を持つ
        close = getattr(obj, "close", None)
        if callable(close):
            close()
    except Exception:
        pass


def process_host(
    host: str,
    account: str,
    ssh_user: str,
    roots: List[str],
    abs_regexes: List[Pattern[str]],
    rel_regexes: List[Pattern[str]],
    dest_root: str,
    port: int,
    key: Optional[str],
    password: Optional[str],
    timeout: float,
    strict: bool,
    dry_run: bool,
    verbose: bool,
    pack_remote: bool,
    one_archive: bool,
) -> Tuple[str, int, int, Optional[str]]:
    matched = 0
    downloaded = 0
    ssh: Optional[paramiko.SSHClient] = None
    sftp: Optional[paramiko.SFTPClient] = None

    try:
        cfg = SSHConfig(
            host=host, port=port, ssh_user=ssh_user,
            key_filename=key, password=password, timeout=timeout,
            strict_host_key_checking=strict,
        )

        # SSH 接続
        ssh = ssh_open(cfg)

        # SFTP オープン ( ここで失敗しても finally で ssh は閉じられる )
        sftp = ssh.open_sftp()

        # Resolve ~ to account's HOME for roots
        if any(r == "~" for r in roots) or not roots:
            home = remote_home_of_account(ssh, account, timeout)
            actual_roots = [home if r == "~" else r for r in (roots or ["~"])]
        else:
            actual_roots = roots

        use_sudo = (ssh_user != account)

        # tar/gzip が必要なのは, 実際にリモートで pack する場合のみ
        if use_sudo or pack_remote:
            _precheck_remote_tools(ssh, use_sudo=use_sudo, timeout=timeout)
        # リスト＆パックに必要な前提を早めにチェック
        files = list_files_remote(ssh, actual_roots, use_sudo, timeout)
        if verbose:
            print(f"[{host}] scanned {len(files)} files under {', '.join(actual_roots)} (sudo={use_sudo})")

        # Build hit sets
        hits: Set[str] = set()
        rel_hits: Set[str] = set()
        abs_hits: Set[str] = set()
        for p in files:
            hit_abs = bool(abs_regexes and match_any_abs(p, abs_regexes))
            hit_rel = bool(rel_regexes and match_any_rel(p, actual_roots, rel_regexes))
            if hit_abs or hit_rel:
                hits.add(p)
            if hit_rel:
                rel_hits.add(p)
            if hit_abs:
                abs_hits.add(p)

        # 「両方ヒット」は relative 優先で保存し, abs は重複保存しない
        abs_only_hits: List[str] = sorted(abs_hits - rel_hits)
        rel_only_hits: List[str] = sorted(rel_hits)
        matched = len(hits)

        if verbose:
            print(f"[{host}] rel_hits={len(rel_only_hits)} abs_hits={len(abs_only_hits)} total={matched}")
            for p in list(hits)[:20]:
                print(f"[{host}] match: {p}")
            if matched > 20:
                print(f"[{host}] ... ({matched-20} more)")

        if dry_run or matched == 0:
            return host, matched, 0, None

        # sudo が必要, または --pack 指定時は, リモートで tar を作成してからダウンロード
        if use_sudo or pack_remote:
            # 相対ヒット
            if rel_only_hits:
                rel_by_root = _group_hits_by_root(rel_only_hits, actual_roots)
                tgz_rel = pack_matches_remote_bsdtar(
                    ssh=ssh,
                    hits_by_root=rel_by_root,
                    use_sudo=use_sudo,
                    timeout=timeout,
                    one_archive=one_archive,
                    verbose=verbose,
                )
                os.makedirs(os.path.join(dest_root, host, "rel"), exist_ok=True)
                for rpath in tgz_rel:
                    downloaded += download_and_extract(
                        sftp=sftp,
                        remote_tar_path=rpath,
                        host=os.path.join(host, "rel"),  # 展開先を rel/ に寄せる
                        dest_root=dest_root,
                        verbose=verbose,
                    )
                    sudo = "sudo -n " if use_sudo else ""
                    run_cmd(ssh, f"{sudo}rm -f {shlex.quote(rpath)}", timeout)

            # 絶対のみヒット
            if abs_only_hits:
                tgz_abs = pack_abs_hits_remote_bsdtar(
                    ssh=ssh,
                    abs_hits=abs_only_hits,
                    use_sudo=use_sudo,
                    timeout=timeout,
                    one_archive=one_archive,
                    verbose=verbose,
                )
                os.makedirs(os.path.join(dest_root, host, "abs"), exist_ok=True)
                for rpath in tgz_abs:
                    # 既存ユーティリティでダウンロード＋展開
                    downloaded += download_and_extract(
                        sftp=sftp,
                        remote_tar_path=rpath,
                        host=os.path.join(host, "abs"),  # 展開先を abs/ に寄せる
                        dest_root=dest_root,
                        verbose=verbose,
                    )
                    sudo = "sudo -n " if use_sudo else ""
                    run_cmd(ssh, f"{sudo}rm -f {shlex.quote(rpath)}", timeout)
        else:
            # 直接 SFTP で個別ファイルを保存
            for abs_path in sorted(rel_only_hits):
                base_root = _best_root_for(abs_path, actual_roots)
                rel = abs_path[len(base_root):].lstrip("/") if base_root != "/" else abs_path.lstrip("/")
                local_path = os.path.join(dest_root, host, "rel", rel or ".")
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                sftp.get(abs_path, local_path)
                downloaded += 1

            for abs_path in abs_only_hits:
                local_path = os.path.join(dest_root, host, "abs", abs_path.lstrip("/"))
                os.makedirs(os.path.dirname(local_path), exist_ok=True
                )
                sftp.get(abs_path, local_path)
                downloaded += 1

        return host, matched, downloaded, None

    except Exception as e:
        if verbose:
            traceback.print_exc()
        return host, matched, downloaded, f"{type(e).__name__}: {e}"

    finally:
        # ここで必ずクローズしてリークを防止
        _close_quietly(sftp)
        _close_quietly(ssh)


# ------------------------- CLI -------------------------

def main():
    """メイン処理
    """

    ap = argparse.ArgumentParser(description="Collect remote files by regex (absolute & relative patterns supported).")
    ap.add_argument("dest", help="Local destination directory.")
    ap.add_argument("--user", "-u", default=getpass.getuser(), help="Target account (used to resolve ~) (default: current user).")
    ap.add_argument("--ssh-user", "-s", help="SSH login user (default: same as --user).")
    ap.add_argument("--pattern-abs", "-a", action="append", default=[], help=r"Regex for ABSOLUTE remote paths. Can be repeated.")
    ap.add_argument("--pattern-rel", "-r", action="append", default=[], help=r"Regex for paths RELATIVE to each --roots entry. Can be repeated.")
    ap.add_argument("--parallel", "-j", type=int, default=DEFAULT_PARALLEL, help=f"Concurrent hosts (default: {DEFAULT_PARALLEL}).")
    ap.add_argument("--ignore-case", "-i", action="store_true", help="Compile regexes with re.IGNORECASE.")
    ap.add_argument("--hosts", "-H", default="hostfile", help="Hosts file (default: hostfile).")
    ap.add_argument("--roots", "-R", nargs="*", default=["~"], help='Search roots (default: "~" => ACCOUNT\'s HOME). Use absolute paths; multiple allowed.')
    ap.add_argument("--port", "-P", type=int, default=DEFAULT_SSH_PORT, help=f"SSH port (default: {DEFAULT_SSH_PORT}).")
    ap.add_argument("--key", "-K", default=None, help="SSH private key file.")
    ap.add_argument("--password", "-W", default=None, help="SSH password (not recommended).")
    ap.add_argument("--timeout", "-T", type=float, default=DEFAULT_TIMEOUT, help=f"SSH/command timeout seconds (default: {DEFAULT_TIMEOUT}).")
    ap.add_argument("--strict-host-key-checking", "-S", action="store_true", help="Enable strict host key checking (off by default).")
    ap.add_argument("--pack", action="store_true",
                    help="Pack on the REMOTE host using tar command, then download and extract.")
    ap.add_argument("--one-archive", action="store_true",
                    help="When packing remotely, combine all roots into a SINGLE tar (may collide if relative names overlap).")
    ap.add_argument("--dry-run", "-n", action="store_true", help="List matches only; do not download.")
    ap.add_argument("--verbose", "-v", action="store_true", help="Verbose logs.")
    args = ap.parse_args()

    if not args.pattern_abs and not args.pattern_rel:
        # 少なくとも1つ以上の相対・絶対いずれかのパターン指定が必要
        print("At least one of --pattern-abs or --pattern-rel must be specified.", file=sys.stderr)
        sys.exit(EXIT_CODE_INVALID_ARGS)

    if not args.user:
        print("Cannot determine target account. Please specify --user.", file=sys.stderr)
        sys.exit(EXIT_CODE_INVALID_ARGS)

    flags = re.IGNORECASE if args.ignore_case else 0
    try:
        abs_regexes = compile_many(args.pattern_abs, flags)
        rel_regexes = compile_many(args.pattern_rel, flags)
    except re.error as e:
        print(f"Invalid regex: {e}", file=sys.stderr)
        sys.exit(EXIT_CODE_INVALID_ARGS)

    hosts = parse_hosts_file(args.hosts)
    if not hosts:
        print("No hosts found in hosts file.", file=sys.stderr)
        sys.exit(EXIT_CODE_INVALID_ARGS)

    os.makedirs(args.dest, exist_ok=True)
    ssh_user = args.ssh_user or args.user

    print(f"Targets: {len(hosts)} host(s)")
    print(f"SSH user: {ssh_user}  Account: {args.user}")
    print(f"Roots: {', '.join(args.roots)}")
    if args.pattern_abs:
        print(f"ABS patterns: {args.pattern_abs}")
    if args.pattern_rel:
        print(f"REL patterns: {args.pattern_rel}")
    print(f"Dest: {os.path.abspath(args.dest)}  Parallel: {args.parallel}  Dry-run: {args.dry_run}")

    results:List[Tuple[str,int,int,Optional[str]]] = []
    errors:List[Tuple[str,Optional[str]]] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.parallel)) as ex:
        futs = [
            ex.submit(
                process_host,
                host=h,
                account=args.user,
                ssh_user=ssh_user,
                roots=args.roots,
                abs_regexes=abs_regexes,
                rel_regexes=rel_regexes,
                dest_root=args.dest,
                port=args.port,
                key=args.key,
                password=args.password,
                timeout=args.timeout,
                strict=args.strict_host_key_checking,
                pack_remote=args.pack,
                one_archive=args.one_archive,
                dry_run=args.dry_run,
                verbose=args.verbose,
            )
            for h in hosts
        ]
        for fut in concurrent.futures.as_completed(futs):
            host, matched, downloaded, err = fut.result()
            results.append((host, matched, downloaded, err))
            if err:
                errors.append((host, err))
                print(f"[{host}] ERROR: {err}", file=sys.stderr)
            else:
                action = "listed" if args.dry_run else "downloaded"
                print(f"[{host}] matches: {matched}, {action}: {downloaded}")

    total_matched = sum(m for _, m, _, _ in results)
    total_downloaded = sum(d for _, _, d, _ in results)
    print("\n=== Summary ===")
    print(f"Hosts processed: {len(results)} / {len(hosts)}")
    print(f"Total matches:   {total_matched}")
    print(f"Total {'listed' if args.dry_run else 'downloaded'}: {total_downloaded}")

    if errors:
        print("\nErrors:")
        for host, err in errors:
            print(f" - {host}: {err}")
        # 1: エラー発生, かつ, 1件も取得できず
        # 2: エラーはあったが一部は取得できた
        sys.exit(EXIT_CODE_NO_FETCH if total_downloaded == 0 else EXIT_CODE_PARTIAL_FETCH)
    # エラー無しだが, 全体でヒットゼロを明示的に区別したい場合
    if total_matched == 0:
        sys.exit(EXIT_CODE_NO_MATCH)  # “No matches”
    # 正常終了
    sys.exit(EXIT_CODE_SUCCESS)

if __name__ == "__main__":
    socket.setdefaulttimeout(60.0)
    main()
