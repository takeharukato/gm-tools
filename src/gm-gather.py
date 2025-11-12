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
import signal
import tarfile
import tempfile
import traceback
import getpass
import random
import gettext
import locale
from pathlib import Path

from dataclasses import dataclass
from typing import List, Tuple, Optional, Iterable, Set, Dict, Union
from typing import Pattern

# デフォルト値定義
# SSHポート
DEFAULT_SSH_PORT = 22
# 並行実行数
DEFAULT_PARALLEL = 4
# SSH/SFTP タイムアウト秒数
DEFAULT_TIMEOUT = 30.0
# ソケットタイムアウト秒数
DEFAULT_SOCKET_TIMEOUT: float = 60.0

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

# I18N 定義

APPNAME = "gm-tools"               # textdomain
# スクリプト同梱の ./locale を優先。インストール形態では /usr/share/locale 等が使われるため
# gettext 側の検索 ( translation(..., fallback=True) ) に任せつつ, ローカル同梱も見えるようにする。
LOCALEDIR = (Path(__file__).resolve().parent / "locale")  # ./locale/

# 初期値 ( 未初期化時の保険 )
_ = gettext.gettext
ngettext = gettext.ngettext
DEFAULT_ENCODING = locale.getpreferredencoding(False)
current_encoding = DEFAULT_ENCODING

# モジュール読み込みチェック
try:
    import paramiko
except ImportError:
    print(_("This script requires 'paramiko'. Install via OS package (python3-paramiko) or pip."), file=sys.stderr)
    sys.exit(EXIT_CODE_MODULE_MISSING)

# ------------------------- I18N setup -------------------------
def setup_i18n(lang: Optional[str] = None) -> None:
    """
    機能概要:
      指定言語または環境のロケール設定に基づき, gettext による国際化 (I18N) を初期化する。

    引数:
      lang (Optional[str]): 言語コード ( 例: 'ja', 'en_US' ) 。None の場合は環境変数 LANGUAGE/LC_ALL 等に従う。

    返り値:
      None: 返却値はない ( 副作用としてグローバル関数 _ / ngettext と current_encoding を設定 ) 。

    生成値:
      なし
    """

    global _, ngettext, current_encoding

    # 1) OS ロケールを有効化 ( 失敗しても続行 )
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass

    # 2) 言語選択 : 引数優先, 無指定時は環境変数 ( LANGUAGE/LC_ALL/… ) に従う
    languages = [lang] if lang else None

    # 3) 翻訳辞書の探索候補
    #    - スクリプト同梱 ./locale
    #    - /usr/share/locale, /usr/local/share/locale
    #    - None ( 標準の検索パスを使う )
    candidates: List[Optional[str]] = []
    try:
        if LOCALEDIR.exists():
            candidates.append(str(LOCALEDIR))
    except Exception:
        pass
    for d in ("/usr/share/locale", "/usr/local/share/locale"):
        try:
            if Path(d).exists():
                candidates.append(d)
        except Exception:
            pass
    candidates.append(None)

    # 4) 翻訳辞書をロード ( 見つからなければ NullTranslations )
    trans: Optional[gettext.NullTranslations] = None
    debug_i18n = os.environ.get("GM_I18N_DEBUG") == "1"
    for locdir in candidates:
        try:
            if locdir is None:
                trans = gettext.translation(APPNAME, languages=languages, fallback=False)
            else:
                trans = gettext.translation(APPNAME, localedir=locdir, languages=languages, fallback=False)
            break
        except FileNotFoundError:
            # この候補には .mo が無い  =>  次へ
            continue
        except Exception as e:
            if debug_i18n:
                print(f"[i18n] skip {locdir}: {type(e).__name__}: {e}", file=sys.stderr)
            # 破損 .mo 等の異常でも次候補へフォールバック
            continue
    if trans is None:
        trans = gettext.NullTranslations()

    # 5) グローバルにバインド
    _ = trans.gettext
    ngettext = trans.ngettext

    # 6) 表示用エンコーディング ( 推奨 API  =>  フォールバック )
    try:
        current_encoding = locale.getencoding()  # Py3.11+
    except AttributeError:
        current_encoding = locale.getpreferredencoding(False)

# ------------------------- Tar helpers -------------------------
def safe_extract(tf: tarfile.TarFile, base_dir: str) -> int:
    """
    機能概要:
      tar アーカイブを安全に展開する。先頭の '/' を除去し正規化した上で, パストラバーサル ( '..' や絶対パス ) ,
      シンボリックリンク／ハードリンク等の危険要素を排除して, 通常ファイルとディレクトリのみを展開する。

    引数:
      tf (tarfile.TarFile): 展開対象となる tarfile オブジェクト ( 読み取りモードでオープン済み ) 。
      base_dir (str): 展開先ディレクトリの絶対または相対パス。存在しない場合は呼び出し側で作成しておくこと。

    返り値:
      int: 展開された通常ファイル ( isfile ) の件数。

    生成値:
      なし
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
    """
    機能概要:
      Paramiko を用いて SSH 接続を確立し, SSHClient を返す。ホスト鍵検証の有無は設定で制御する。

    引数:
      cfg (SSHConfig): 接続先ホスト名, ポート, ユーザー, 鍵ファイル, パスワード, タイムアウト,
                       厳格なホスト鍵チェック有無を格納した設定データクラス。

    返り値:
      paramiko.SSHClient: 接続済みの SSH クライアント。

    生成値:
      なし
    """

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
    """
    機能概要:
      既存の SSH 接続上でコマンドを実行し, 終了コード・標準出力・標準エラーを取得する。

    引数:
      ssh (paramiko.SSHClient): 接続済み SSH クライアント。
      cmd (str): リモートで実行するシェルコマンド文字列。
      timeout (float): コマンド実行タイムアウト ( 秒 ) 。

    返り値:
      Tuple[int, bytes, bytes]: (rc, stdout_bytes, stderr_bytes) のタプル。
        rc は終了ステータス ( int ) , stdout_bytes および stderr_bytes は生のバイト列。

    生成値:
      なし
    """

    _stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read()
    err = stderr.read()
    rc = stdout.channel.recv_exit_status()
    return rc, out, err


def remote_home_of_account(ssh: paramiko.SSHClient, account: str, timeout: float) -> str:
    """
    機能概要:
      指定アカウントのホームディレクトリをリモートで解決する。getent で取得できない場合は一般的な既定値にフォールバックする。

    引数:
      ssh (paramiko.SSHClient): 接続済み SSH クライアント。
      account (str): 対象アカウント名 ( 例: 'root', 'tkato' ) 。
      timeout (float): コマンド実行タイムアウト ( 秒 ) 。

    返り値:
      str: 解決されたホームディレクトリの絶対パス ( 例: '/root', '/home/USER' ) 。

    生成値:
      なし
    """

    rc, out, _ = run_cmd(ssh, f"getent passwd {shlex.quote(account)} | cut -d: -f6", timeout)
    if rc == 0:
        home = out.decode().strip()
        if home.startswith("/") and len(home) > 1:
            return home
    return "/root" if account == "root" else f"/home/{account}"


# ------------------------- Host list -------------------------

def parse_hosts_file(path: str) -> List[str]:
    """
    機能概要:
      ホストファイルを読み取り, コメントや空行を除外してホスト名 ( 1行1ホスト ) を抽出する。
      行内の空白に続く '#' 以降はコメントとして除去する。

    引数:
      path (str): ホストファイルへのパス ( UTF-8 テキスト ) 。

    返り値:
      List[str]: 抽出されたホスト名 ( FQDN または IP 等 ) のリスト。順序はファイル順。

    生成値:
      なし
    """

    hosts: List[str] = []
    try:
        f = open(path, encoding="utf-8")
    except FileNotFoundError:
        raise FileNotFoundError(f"Hosts file not found: {path}")
    with f:
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
    """
    機能概要:
      指定 root ディレクトリ群直下 ( -xdev でファイルシステムを跨がない ) を find で走査し, 通常ファイル一覧を取得する。
      必要に応じて sudo -n を付与して権限昇格する。

    引数:
      ssh (paramiko.SSHClient): 接続済み SSH クライアント。
      roots (List[str]): 走査対象の絶対パスのリスト ( 例: ['/etc', '/var/log'] ) 。空は不可。
      use_sudo (bool): sudo -n を使用して実行する場合は True。
      timeout (float): コマンド実行タイムアウト ( 秒 ) 。

    返り値:
      List[str]: NUL 区切りで受け取った絶対パスをデコードしたファイルパスのリスト。

    生成値:
      なし
    """

    if not roots:
        raise ValueError("roots must not be empty")
    qroots = " ".join(shlex.quote(r) for r in roots)
    base_cmd = f"LC_ALL=C find {qroots} -xdev -type f -print0 2>/dev/null"
    cmd = f"sudo -n {base_cmd}" if use_sudo else base_cmd
    rc, out, err = run_cmd(ssh, f"{cmd}", timeout)
    # find の “軽微な失敗” (例: Permission denied) で rc=1 になる実装がある
    if rc not in (0, 1):
        raise RuntimeError(f"find failed (rc={rc}): {err.decode(errors='ignore')}")
    if not out:
        return []
    return [p for p in out.decode("utf-8", errors="surrogateescape").split("\x00") if p]

def _best_root_for(abs_path: str, roots: List[str]) -> str:
    """
    機能概要:
      与えられた絶対パスに対して, roots の中で最長一致する root を返す。該当がなければ '/' を返す。

    引数:
      abs_path (str): 対象の絶対パス。
      roots (List[str]): root 候補のリスト ( 絶対パス ) 。

    返り値:
      str: 最長一致した root ( なければ '/' ) 。

    生成値:
      なし
    """

    best = ""
    for r in roots:
        rn = r.rstrip("/") or "/"
        pref = "/" if rn == "/" else rn + "/"
        if abs_path == rn or abs_path.startswith(pref):
            if len(rn) > len(best):
                best = rn
    return best or "/"

def _group_hits_by_root(hits: Iterable[str], roots: List[str]) -> Dict[str, List[str]]:
    """
    機能概要:
      絶対パスのヒット集合を, 最長一致した root ごとに相対パスへ変換してグルーピングする。

    引数:
      hits (Iterable[str]): 絶対パスのヒット群。
      roots (List[str]): root 候補のリスト ( 絶対パス ) 。

    返り値:
      Dict[str, List[str]]: {root: [relative_path, ...]} の辞書。relative_path は root からの相対パス ( 空は '.' ) 。

    生成値:
      なし
    """

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
    機能概要:
      リモートホスト上で bsdtar/GNU tar を用い, 相対パスヒット群を root 単位 ( または単一アーカイブ ) で .tar.gz 化する。
      --transform など GNU tar 固有オプションは用いず, 互換性を優先する。

    引数:
      ssh (paramiko.SSHClient): 接続済み SSH クライアント。
      hits_by_root (Dict[str, List[str]]): {root: [relative_path, ...]} 形式の入力。
      use_sudo (bool): sudo -n を使用する場合は True。
      timeout (float): コマンド実行タイムアウト ( 秒 ) 。
      one_archive (bool): すべての root を単一 .tar に集約する場合は True。
      verbose (bool): 進捗を詳細出力する場合は True。

    返り値:
      List[str]: リモートに生成された .tar.gz の絶対パス一覧。

    生成値:
      なし
    """

    sudo = "sudo -n " if use_sudo else ""
    CHUNK = 200  # 引数長対策 : ファイルが多いときは分割して -rf 追記
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
                delim = f"__GG_{os.getpid()}_{random.randint(10**6,10**7-1)}__"
                rc, _, err = run_cmd(ssh, f"{sudo}sh -c 'LC_ALL=C cd {shlex.quote(root)} && cat > {shlex.quote(list_ident)} <<\"{delim}\"\n{list_content}{delim}'", timeout)
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
            delim = f"__GG_{os.getpid()}_{random.randint(10**6,10**7-1)}__"
            rc, _, err = run_cmd(ssh, f"{sudo}sh -c 'LC_ALL=C cd {shlex.quote(root)} && cat > {shlex.quote(list_ident)} <<\"{delim}\"\n{list_content}{delim}'", timeout)
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
    機能概要:
      絶対パスでヒットしたファイル群を, リモート側で tar -P を使って .tar.gz 化する。
      要求に応じて単一アーカイブまたは分割アーカイブを生成する。

    引数:
      ssh (paramiko.SSHClient): 接続済み SSH クライアント。
      abs_hits (List[str]): 絶対パスのヒット一覧。
      use_sudo (bool): sudo -n を使用する場合は True。
      timeout (float): コマンド実行タイムアウト ( 秒 ) 。
      one_archive (bool): 単一 .tar.gz に集約する場合は True。
      verbose (bool): 進捗を詳細出力する場合は True。

    返り値:
      List[str]: リモートに生成された .tar.gz の絶対パス一覧。

    生成値:
      なし
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
            delim = f"__GG_{os.getpid()}_{random.randint(10**6,10**7-1)}__"
            rc, _, err = run_cmd(
                ssh,
                f"{sudo}sh -c 'LC_ALL=C cat > {shlex.quote(list_ident)} <<\"{delim}\"\n{list_content}{delim}'",
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
            delim = f"__GG_{os.getpid()}_{random.randint(10**6,10**7-1)}__"
            rc, _, err = run_cmd(
                ssh,
                f"{sudo}sh -c 'LC_ALL=C cat > {shlex.quote(list_ident)} <<\"{delim}\"\n{list_content}{delim}'",
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
    """
    機能概要:
      リモートの .tar.gz を SFTP でダウンロードし, 一時ファイルに保存後, safe_extract を用いて展開する。
      展開先は <dest_root>/<host>/ ( host に 'hostname/rel' や 'hostname/abs' を含めて階層化する ) 。

    引数:
      sftp (paramiko.SFTPClient): 接続済み SFTP クライアント。
      remote_tar_path (str): リモート側の .tar.gz の絶対パス。
      host (str): 展開先のサブディレクトリ ( 例: 'hostA/rel' や 'hostA/abs' ) 。
      dest_root (str): ローカルの保存ルートディレクトリ。
      verbose (bool): ダウンロードや展開のログ出力を行う場合は True。

    返り値:
      int: 展開された通常ファイル数。

    生成値:
      なし
    """

    # host 引数が "hostname/rel" や "hostname/abs" を含む想定
    os.makedirs(os.path.join(dest_root, host), exist_ok=True)
    # 一時ファイルの prefix にパス区切りが入らないように正規化
    safe_prefix = host.replace(os.sep, "_")
    with tempfile.NamedTemporaryFile(prefix=f"{safe_prefix}_", suffix=".tar.gz", delete=False) as tmpf:
        local_tar = tmpf.name
    try:
        if verbose:
            print(_("Downloading {remote} -> {local}").format(remote=remote_tar_path, local=local_tar))
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
    """
    機能概要:
      正規表現パターンの配列を指定フラグでコンパイルし, Pattern の配列を返す。

    引数:
      patterns (List[str]): コンパイル対象の正規表現パターン文字列のリスト。
      flags (int): re.compile に渡すフラグ ( 例: re.IGNORECASE ) 。

    返り値:
      List[Pattern[str]]: コンパイル済み正規表現オブジェクトのリスト。

    生成値:
      なし
    """

    out: List[Pattern[str]] = []
    for p in patterns:
        out.append(re.compile(p, flags))
    return out


def match_any_abs(abs_path: str, abs_regexes: List[Pattern[str]]) -> bool:
    """
    機能概要:
      絶対パス文字列が, 与えられた絶対パス向け正規表現のいずれかにマッチするか判定する。

    引数:
      abs_path (str): 対象の絶対パス。
      abs_regexes (List[Pattern[str]]): 絶対パス用のコンパイル済み正規表現一覧。

    返り値:
      bool: いずれかにマッチすれば True, しなければ False。

    生成値:
      なし
    """

    return any(r.search(abs_path) for r in abs_regexes)


def iter_relatives(abs_path: str, roots: List[str]) -> Iterable[Tuple[str, str]]:
    """
    機能概要:
      与えられた絶対パスに対し, roots の各要素が接頭辞として一致する場合に (root, rel_path) を順次生成する。
      ここで rel_path は root からの相対パス ( abs_path == root の場合は '' を生成 ) 。

    引数:
      abs_path (str): 対象の絶対パス ( POSIX 形式前提 ) 。
      roots (List[str]): 比較対象の root 候補 ( POSIX 形式の絶対パス ) 。

    返り値:
      Iterable[Tuple[str, str]]: ジェネレーターを返す ( ループで消費する想定 ) 。

    生成値:
      Tuple[str, str]: 一致した (root, rel_path) のペアを逐次 yield する。
    """

    for root in roots:
        root_norm = root.rstrip("/")
        if root_norm == "":
            continue
        if abs_path == root_norm or abs_path.startswith(root_norm + "/"):
            rel = abs_path[len(root_norm) + (0 if abs_path == root_norm else 1):]
            yield (root_norm, rel)


def match_any_rel(abs_path: str, roots: List[str], rel_regexes: List[Pattern[str]]) -> bool:
    """
    機能概要:
      abs_path を roots からの相対パスへ写像し, いずれかの相対パスが rel_regexes のいずれかにマッチするかを判定する。

    引数:
      abs_path (str): 対象の絶対パス。
      roots (List[str]): root 候補のリスト ( 相対化のベース ) 。
      rel_regexes (List[Pattern[str]]): 相対パス用のコンパイル済み正規表現一覧。

    返り値:
      bool: 相対パスのいずれかがマッチすれば True, しなければ False。

    生成値:
      なし
    """

    if not rel_regexes:
        return False
    for _, rel in iter_relatives(abs_path, roots):
        if any(r.search(rel) for r in rel_regexes):
            return True
    return False


# ------------------------- Per-host processing -------------------------
def _precheck_remote_tools(ssh: paramiko.SSHClient, use_sudo: bool, timeout: float) -> None:
    """
    機能概要:
      リモート側で tar と gzip の存在, 必要に応じて sudo -n 実行可否を事前確認する。満たさない場合は例外を送出する。

    引数:
      ssh (paramiko.SSHClient): 接続済み SSH クライアント。
      use_sudo (bool): sudo -n の確認を行うかどうか。
      timeout (float): コマンド実行タイムアウト ( 秒 ) 。

    返り値:
      None: 返却値はない ( エラー時は例外 ) 。

    生成値:
      なし
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
    機能概要:
      Paramiko の SSHClient または SFTPClient に対して close() を安全に呼び出し,
      例外を無視して静かにリソースを解放する。

    引数:
      obj (Optional[Union[paramiko.SSHClient, paramiko.SFTPClient]]): クローズ対象。None の場合は何もしない。

    返り値:
      None: 返却値はない。

    生成値:
      なし
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
    """
    機能概要:
      単一ホストに対してファイル一覧取得, パターンマッチ, 必要に応じたリモート tar 圧縮＋ダウンロード＋展開,
      または直接 SFTP ダウンロードを行い, 結果を返す。

    引数:
      host (str): 対象ホスト名または IP。
      account (str): ファイル収集対象のアカウント名 ( '~' 解決に使用 ) 。
      ssh_user (str): SSH ログインユーザー名。
      roots (List[str]): 検索ルート ( '~' を含む場合は account の HOME に解決 ) 。
      abs_regexes (List[Pattern[str]]): 絶対パス用の正規表現。
      rel_regexes (List[Pattern[str]]): 相対パス用の正規表現。
      dest_root (str): ローカル保存先ルートディレクトリ。
      port (int): SSH ポート番号。
      key (Optional[str]): SSH 秘密鍵ファイルパス。None の場合は未指定。
      password (Optional[str]): SSH パスワード。None の場合は未指定。
      timeout (float): SSH/コマンドのタイムアウト ( 秒 ) 。
      strict (bool): 厳格なホスト鍵チェックを有効化するかどうか。
      dry_run (bool): ダウンロードを行わず一致項目の列挙のみを行う場合は True。
      verbose (bool): 詳細ログを出力する場合は True。
      pack_remote (bool): リモートで tar 生成してから取得する場合は True。
      one_archive (bool): 複数 root を単一アーカイブにまとめる場合は True。

    返り値:
      Tuple[str, int, int, Optional[str]]:
        (host, matched, downloaded, err) を返す。
          host (str): 対象ホスト。
          matched (int): マッチしたファイル総数。
          downloaded (int): ダウンロード ( 展開 ) したファイル総数。
          err (Optional[str]): エラーがあれば "TypeError: ..." のようなメッセージ, なければ None。

    生成値:
      なし
    """

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
        print("Config:%s" % cfg)
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

        # まず一覧を取得 ( ここでは tar/gzip は不要 )
        files = list_files_remote(ssh, actual_roots, use_sudo, timeout)
        if verbose:
            print(_("Scanned {count} files under {roots} (sudo={use_sudo})").format(
                count=len(files),
                roots=", ".join(actual_roots),
                use_sudo=use_sudo,
            ))

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
            print(_("rel_hits={count} abs_hits={count2} total={total}").format(
                count=len(rel_only_hits),
                count2=len(abs_only_hits),
                total=matched
            ))
            for p in list(hits)[:20]:
                print(_("match: {path}").format(path=p))
            if matched > 20:
                print(_("[{host}] ... ({count} more)").format(host=host, count=matched-20))

        if dry_run or matched == 0:
            return host, matched, 0, None

        # sudo が必要, または --pack 指定時は, リモートで tar を作成してからダウンロード
        if (use_sudo or pack_remote) and (rel_only_hits or abs_only_hits):

            # 実際に pack する直前にだけ前提チェック
            _precheck_remote_tools(ssh, use_sudo=use_sudo, timeout=timeout)

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
                # rel が空なら basename を採用して保存
                if not rel:
                    rel = os.path.basename(abs_path)
                local_path = os.path.join(dest_root, host, "rel", rel)
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
    """
    機能概要:
      コマンドライン引数を解析し, 並列実行で各ホストの処理 ( process_host ) を実行する。
      実行結果を集計してサマリを表示し, 状況に応じた終了コードを返す。

    引数:
      なし ( argparse により sys.argv から取得 ) 。

    返り値:
      None: 関数自体は値を返さない ( 最終的に sys.exit によりプロセス終了 ) 。

    生成値:
      なし
    """

    setup_i18n() # 国際化セットアップ

    ap = argparse.ArgumentParser(description=_("Collect remote files by regex (absolute & relative patterns supported)."))
    ap.add_argument("dest", help=_("Local destination directory."))
    ap.add_argument("--user", "-u", default=getpass.getuser(), help=_("Target account (used to resolve ~) (default: current user)."))
    ap.add_argument("--ssh-user", "-s", help=_("SSH login user (default: same as --user)."))
    ap.add_argument("--pattern-abs", "-a", action="append", default=[], help=_("Regex for ABSOLUTE remote paths. Can be repeated."))
    ap.add_argument("--pattern-rel", "-r", action="append", default=[], help=_("Regex for paths RELATIVE to each --roots entry. Can be repeated."))
    ap.add_argument("--parallel", "-j", type=int, default=DEFAULT_PARALLEL, help=_("Concurrent hosts (default: {DEFAULT_PARALLEL}).").format(DEFAULT_PARALLEL=DEFAULT_PARALLEL))
    ap.add_argument("--ignore-case", "-i", action="store_true", help=_("Compile regexes with re.IGNORECASE."))
    ap.add_argument("--hosts", "-H", default="hostfile", help=_("Hosts file (default: hostfile)."))
    ap.add_argument("--roots", "-R", nargs="*", default=["~"], help=_('Search roots (default: "~" => ACCOUNT\'s HOME). Use absolute paths; multiple allowed.'))
    ap.add_argument("--port", "-P", type=int, default=DEFAULT_SSH_PORT, help=_("SSH port (default: {DEFAULT_SSH_PORT}).").format(DEFAULT_SSH_PORT=DEFAULT_SSH_PORT))
    ap.add_argument("--key", "-K", default=None, help=_("SSH private key file."))
    ap.add_argument("--password", "-W", default=None, help=_("SSH password (not recommended)."))
    ap.add_argument("--timeout", "-T", type=float, default=DEFAULT_TIMEOUT, help=_("SSH/command timeout seconds (default: {DEFAULT_TIMEOUT}).").format(DEFAULT_TIMEOUT=DEFAULT_TIMEOUT))
    ap.add_argument("--strict-host-key-checking", "-S", action="store_true", help=_("Enable strict host key checking (off by default)."))
    ap.add_argument("--pack", action="store_true",
                    help=_("Pack on the REMOTE host using tar command, then download and extract."))
    ap.add_argument("--one-archive", action="store_true",
                    help=_("When packing remotely, combine all roots into a SINGLE tar (may collide if relative names overlap)."))
    ap.add_argument("--dry-run", "-n", action="store_true", help=_("List matches only; do not download."))
    ap.add_argument("--verbose", "-v", action="store_true", help=_("Verbose logs."))
    args = ap.parse_args()

    if not args.pattern_abs and not args.pattern_rel:
        # 少なくとも1つ以上の相対・絶対いずれかのパターン指定が必要
        print(_("At least one of --pattern-abs or --pattern-rel must be specified."), file=sys.stderr)
        sys.exit(EXIT_CODE_INVALID_ARGS)

    if not args.user:
        print(_("Cannot determine target account. Please specify --user."), file=sys.stderr)
        sys.exit(EXIT_CODE_INVALID_ARGS)

    flags = re.IGNORECASE if args.ignore_case else 0
    try:
        abs_regexes = compile_many(args.pattern_abs, flags)
        rel_regexes = compile_many(args.pattern_rel, flags)
    except re.error as e:
        print(_("Invalid regex: {err}").format(err=str(e)), file=sys.stderr)
        sys.exit(EXIT_CODE_INVALID_ARGS)

    hosts = parse_hosts_file(args.hosts)
    if not hosts:
        print(_("No hosts found in hosts file."), file=sys.stderr)
        sys.exit(EXIT_CODE_INVALID_ARGS)

    os.makedirs(args.dest, exist_ok=True)
    ssh_user = args.ssh_user or args.user

    print(_("Targets: {length} host(s)").format(length=len(hosts)))
    print(_("SSH user: {user}  Account: {account}").format(user=ssh_user, account=args.user))
    print(_("Roots: {roots}").format(roots=', '.join(args.roots)))
    if args.pattern_abs:
        print(_("ABS patterns: {patterns}").format(patterns=args.pattern_abs))
    if args.pattern_rel:
        print(_("REL patterns: {patterns}").format(patterns=args.pattern_rel))
    print(_("Dest: {dest}  Parallel: {parallel}  Dry-run: {dry_run}").format(
        dest=os.path.abspath(args.dest),
        parallel=args.parallel,
        dry_run=args.dry_run
    ))

    results:List[Tuple[str,int,int,Optional[str]]] = []
    errors:List[Tuple[str,Optional[str]]] = []

    # Ctrl-C (SIGINT) を即時反映
    signal.signal(signal.SIGINT, signal.default_int_handler)
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

        try:
            for fut in concurrent.futures.as_completed(futs):
                host, matched, downloaded, err = fut.result()
                results.append((host, matched, downloaded, err))
                if err:
                    errors.append((host, err))
                    print(_("[{host}] ERROR: {err}").format(host=host, err=err), file=sys.stderr)
                else:
                    action = "listed" if args.dry_run else "downloaded"
                    print(_("[{host}] matches: {matched}, {action}: {downloaded}").format(
                        host=host, matched=matched, action=action, downloaded=downloaded))
        except KeyboardInterrupt:
            # 実行中のジョブを可能な限りキャンセルして即時終了
            for f in futs:
                f.cancel()
            try:
                # Python 3.9+ で cancel_futures を有効化
                ex.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                # 古い Python ではオプション未対応
                ex.shutdown(wait=False)
            total_downloaded = sum(d for _, _, d, _ in results)
            print(_("\nInterrupted by user (Ctrl-C)"), file=sys.stderr)
            sys.exit(EXIT_CODE_PARTIAL_FETCH if total_downloaded > 0 else EXIT_CODE_NO_FETCH)
    total_matched = sum(m for _, m, _, _ in results)
    total_downloaded = sum(d for _, _, d, _ in results)
    print("\n")
    print(_("=== Summary ==="))
    print(_("Hosts processed: {count} / {total}").format(count=len(results), total=len(hosts)))
    print(_("Total matches:   {count}").format(count=total_matched))
    print(_("Total {action}: {count}").format(action='listed' if args.dry_run else 'downloaded', count=total_downloaded))

    if errors:
        print("\n")
        print(_("Errors:"))
        for host, err in errors:
            print(_(" - {host}: {err}").format(host=host, err=err))
        # 1: エラー発生, かつ, 1件も取得できず
        # 2: エラーはあったが一部は取得できた
        sys.exit(EXIT_CODE_NO_FETCH if total_downloaded == 0 else EXIT_CODE_PARTIAL_FETCH)
    # エラー無しだが, 全体でヒットゼロを明示的に区別
    if total_matched == 0:
        sys.exit(EXIT_CODE_NO_MATCH)  # “No matches”
    # 正常終了
    sys.exit(EXIT_CODE_SUCCESS)

if __name__ == "__main__":
    socket.setdefaulttimeout(DEFAULT_SOCKET_TIMEOUT)
    main()
