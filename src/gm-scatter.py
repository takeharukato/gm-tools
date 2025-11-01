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
import gettext
import locale

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

APPNAME = "gm-tools"               # textdomain
# スクリプト同梱の ./locale を優先。インストール形態では /usr/share/locale 等が使われるため
# gettext 側の検索 ( translation(..., fallback=True) ) に任せつつ, ローカル同梱も見えるようにする。
LOCALEDIR = (Path(__file__).resolve().parent / "locale")  # ./locale/

# 初期値 ( 未初期化時の保険 )
_ = gettext.gettext
ngettext = gettext.ngettext
DEFAULT_ENCODING = locale.getpreferredencoding(False)
current_encoding = DEFAULT_ENCODING


# ---- インポート必須モジュール ( Paramiko )  ----
try:
    import paramiko  # type: ignore
except Exception as _e:
    print(_("This script requires 'paramiko'. Install via OS package (python3-paramiko) or pip."), file=sys.stderr)
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

# ------------------------- I18N setup -------------------------
def setup_i18n(lang: Optional[str] = None) -> None:
    """
    機能概要:
      指定された言語コード ( 未指定なら環境のロケール ) に基づき, gettext を初期化する。
      翻訳辞書は ./locale, /usr/share/locale, /usr/local/share/locale 等から探索し,
      見つからない場合は NullTranslations を用いる。副作用として `_`, `ngettext`,
      `current_encoding` を設定する。

    引数:
      lang (Optional[str]): 言語コード ( 例: 'ja', 'en_US' ) 。None の場合は
        環境変数 LANGUAGE / LC_ALL / LC_MESSAGES などに従う。

    返り値:
      None: 戻り値はない ( 副作用として翻訳関数・エンコーディングを設定 ) 。

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
                print(_("[i18n] skip {loc}: {etype}: {msg}").format(loc=locdir, etype=type(e).__name__, msg=e), file=sys.stderr)
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

# ========= 汎用ユーティリティ =========

def is_windows() -> bool:
    """
    機能概要:
      実行環境が Windows かどうかを判定する。

    引数:
      なし

    返り値:
      bool: Windows の場合 True, その他 OS の場合 False。

    生成値:
      なし
    """
    return os.name == "nt" or platform.system().lower().startswith("win")


def to_posix_rel(rel: str) -> str:
    """
    機能概要:
      相対パス文字列を POSIX 形式 ( '/' 区切り ) へ変換する。

    引数:
      rel (str): 相対パス文字列。OS 既定セパレータ ( Windows の '\\\\' 等 ) を含み得る。

    返り値:
      str: '/' 区切りに正規化した相対パス。入力に '\\\\' が含まれなければ原文を返す。

    生成値:
      なし
    """
    return rel.replace("\\", "/") if "\\" in rel else rel


def safe_relpath_for_transfer(abs_path: str, base: str) -> Optional[str]:
    """
    機能概要:
      `base` を基点として `abs_path` の相対名を作成し, 送信に安全かを検証する。
      先頭 '/' の禁止, '..' による上位逸脱を防止し, POSIX 区切りに正規化する。

    引数:
      abs_path (str): 絶対パスのローカルファイル/ディレクトリ。
      base (str): 相対化の基点となる絶対パス。

    返り値:
      Optional[str]: 安全と判断される相対名 ( POSIX 形式 ) 。安全でない場合は None。

    生成値:
      なし
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
    """
    機能概要:
      `roots_abs` の中で `abs_path` に最長前方一致する root を返す。該当が無い場合は '/' を返す。

    引数:
      abs_path (str): 対象の絶対パス。
      roots_abs (List[str]): 探索候補の root ディレクトリ ( 絶対パス ) 一覧。

    返り値:
      str: 最長一致した root。見つからない場合は '/'。

    生成値:
      なし
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
    """
    機能概要:
      ホストファイルを読み取り, ホスト名 ( 1 行 1 エントリ ) のリストを返す。
      空行と '#' から始まる行は無視し, {TAB or 空白}+ '#' 以降はコメントとして除去する。

    引数:
      path (str): ホストファイルのパス。

    返り値:
      List[str]: 抽出されたホスト名の一覧 ( 出現順 ) 。

    生成値:
      なし
    """

    hosts: List[str] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            if s.startswith("#"):
                continue
            # タブ や 空白文字 に続く # 以降をコメント扱い
            m = re.search(r"\s+#", s)
            if m:
                s = s[: m.start()].rstrip()
            if s:
                hosts.append(s)
    return hosts


# ========= SSH / SFTP =========

def ssh_open(cfg: SSHConfig) -> paramiko.SSHClient:
    """
    機能概要:
      `cfg` に基づいて Paramiko の SSHClient を生成し, 接続済みクライアントを返す。
      厳格ホスト鍵チェックの有無は `cfg.strict_host_key_checking` に従う。

    引数:
      cfg (SSHConfig): 接続先ホスト名, ポート, ユーザ, 鍵/パスワード, タイムアウト,
        厳格ホスト鍵チェック可否を含む設定。

    返り値:
      paramiko.SSHClient: 接続済み SSH クライアント。

    生成値:
      なし
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
    """
    機能概要:
      リモートホスト上で `cmd` を実行し, 終了コード・標準出力・標準エラーを取得する。
      ノンブロッキング読み取りで断片を収集し, `timeout` 超過時はチャネルを閉じて
      部分出力のヘッドを含む TimeoutError を送出する。

    引数:
      ssh (paramiko.SSHClient): 接続済み SSH クライアント。
      cmd (str): 実行するシェルコマンド文字列。リモート側の PATH/シェルに依存。
      timeout (float): コマンドの総タイムアウト秒数 ( 開始〜終了まで ) 。

    返り値:
      Tuple[int, bytes, bytes]: (exit_code, stdout_bytes, stderr_bytes)。
        - exit_code (int): コマンドの終了コード。
        - stdout_bytes (bytes): 受信した標準出力のバイト列。
        - stderr_bytes (bytes): 受信した標準エラーのバイト列。

    生成値:
      なし
    """

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
                    _("command timed out after {sec:.1f}s: {cmd}\n[stdout(head)] {out}\n[stderr(head)] {err}").format(
                        sec=timeout, cmd=cmd, out=_head(partial_out), err=_head(partial_err)
                    )
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
    """
    機能概要:
      リモートで `getent passwd account` を参照し, HOME ディレクトリを取得する。
      解決できない場合は 'root' なら '/root', それ以外は '/home/{account}' を慣習値として返す。

    引数:
      ssh (paramiko.SSHClient): 接続済み SSH クライアント。
      account (str): HOME を解決したいアカウント名。
      timeout (float): コマンドのタイムアウト秒数。

    返り値:
      str: HOME ディレクトリの絶対パス。

    生成値:
      なし
    """

    rc, out, _ = run_cmd(ssh, f"getent passwd {shlex.quote(account)} | cut -d: -f6", timeout)
    if rc == 0:
        home = out.decode().strip()
        if home.startswith("/") and len(home) > 1:
            return home
    return "/root" if account == "root" else f"/home/{account}"


def ensure_remote_dir(ssh: paramiko.SSHClient, path: str, use_sudo: bool, timeout: float) -> None:
    """
    機能概要:
      リモートで `mkdir -p -- {path}` を実行し, ディレクトリを作成する。
      `use_sudo=True` の場合は `sudo -n` を付与する。失敗時は RuntimeError を送出。

    引数:
      ssh (paramiko.SSHClient): 接続済み SSH クライアント。
      path (str): 作成対象ディレクトリ ( POSIX パス ) 。最終的に `posixpath.normpath` で正規化。
      use_sudo (bool): sudo を付与する場合 True。
      timeout (float): コマンドのタイムアウト秒数。

    返り値:
      None: 戻り値なし ( 失敗時は例外(RuntimeError)送出 )。

    生成値:
      なし
    """

    path = posixpath.normpath(path)
    cmd = f"{'sudo -n ' if use_sudo else ''}mkdir -p -- {shlex.quote(path)}"
    rc, _out, err = run_cmd(ssh, cmd, timeout)
    if rc != 0:
        msg = err.decode(errors="ignore") or "<no stderr>"
        raise RuntimeError(_("mkdir failed for {path}: {msg}").format(path=path, msg=msg))

def sftp_mkdirs(sftp: paramiko.SFTPClient, dest_dir: str) -> None:
    """
    機能概要:
      SFTP API を用いて `dest_dir` までの各階層を順次 stat / mkdir し, mkdir -p 相当の作成を行う。

    引数:
      sftp (paramiko.SFTPClient): 接続済み SFTP クライアント。
      dest_dir (str): 作成対象のディレクトリ ( POSIX パス ) 。

    返り値:
      None

    生成値:
      なし
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
    """
    機能概要:
      リモートホストが SELinux の基本操作に対応可能かを判定する。
      `/sys/fs/selinux` の存在または `selinuxfs` のマウント, かつ `restorecon` の存在を要件とする。

    引数:
      ssh (paramiko.SSHClient): 接続済み SSH クライアント。
      timeout (float): コマンドのタイムアウト秒数。

    返り値:
      bool: 上記の条件を満たす場合 True, 満たさない場合 False。

    生成値:
      なし
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
    機能概要:
      リモートホスト上の `tar` 実装を判別し, GNU か BSD かを返す。
      `tar --version` もしくは `tar --help` の先頭行から "GNU tar" / "bsdtar" /
      "libarchive" / "FreeBSD" 等の文字列を手掛かりに分類する。判別不能な場合は BSD を既定とする。

    引数:
      ssh (paramiko.SSHClient): 接続済み SSH クライアント。
      timeout (float): コマンド実行のタイムアウト秒数。

    返り値:
      RemoteTarFlavor: GNU または BSD を表す列挙値。

    生成値:
      なし
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
    """
    機能概要:
      `shutil.which` の薄いラッパ。実行ファイルが PATH から解決できるかを確認する。

    引数:
      exe (str): 実行ファイル名 ( 拡張子含む場合あり ) 。

    返り値:
      Optional[str]: 見つかった場合はフルパス。見つからない場合は None。

    生成値:
      なし
    """
    from shutil import which as _which
    return _which(exe)

def require_local_tar_when_preserve(preserve_owner: bool, preserve_acls: bool, preserve_xattrs: bool, preserve_perms: bool) -> None:
    """
    機能概要:
      `--pack` 時に preserve-* ( owner/acls/xattrs/perms ) のいずれかが有効であることを前提に,
      ローカル外部 `tar` コマンドの存在を要求する。見つからない場合はエラーメッセージを出力して
      EXIT_INVALID_ARGS で終了する ( SystemExit ) 。

    引数:
      preserve_owner (bool): 所有者保持が必要か ( True/False ) 。
      preserve_acls (bool): ACL 保持が必要か ( True/False ) 。
      preserve_xattrs (bool): 拡張属性保持が必要か ( True/False ) 。
      preserve_perms (bool): パーミッション保持が必要か ( True/False ) 。

    返り値:
      None: 戻り値なし ( エラー時は SystemExit 送出 ) 。

    生成値:
      なし
    """

    need = preserve_owner or preserve_acls or preserve_xattrs or preserve_perms
    if not need:
        return
    tar_cmd = "tar.exe" if is_windows() else "tar"
    found = shutil_which(tar_cmd)
    if not found:
        print(_("[FATAL] External 'tar' is required for --pack with any --preserve-* options, but not found."), file=sys.stderr)
        sys.exit(EXIT_INVALID_ARGS)

# ========= 能力確認 ( リモート / ローカル補助 )  =========

def check_remote_tool(ssh: "paramiko.SSHClient", tool: str, timeout: float) -> bool:
    """
    機能概要:
      `command -v {tool}` を実行して, リモートにツールが存在するかどうかを確認する。

    引数:
      ssh (paramiko.SSHClient): 接続済み SSH クライアント。
      tool (str): 確認したい実行ファイル名。
      timeout (float): コマンドのタイムアウト秒数。

    返り値:
      bool: 存在すれば True, 存在しなければ False。

    生成値:
      なし
    """

    rc, _o, _e = run_cmd(ssh, f"command -v {shlex.quote(tool)} >/dev/null 2>&1", timeout)
    return rc == 0

def check_remote_xattr_capable(ssh: "paramiko.SSHClient", timeout: float) -> bool:
    """
    機能概要:
      リモートで `setfattr` が使用可能かを確認し, xattr 復元の最低限の対応可否を判断する
    引数:
      ssh (paramiko.SSHClient): 接続済み SSH クライアント。
      timeout (float): コマンドのタイムアウト秒数。

    返り値:
      bool: 存在すれば True, 存在しなければ False。

    生成値:
      なし
    """

    return check_remote_tool(ssh, "setfattr", timeout)


# ========= ローカル探索 / パターン適用 =========

def compile_many(patterns: List[str], flags: int) -> List[Pattern[str]]:
    """
    機能概要:
      正規表現文字列リストを `re.compile` でまとめてコンパイルする。

    引数:
      patterns (List[str]): 正規表現パターンの文字列配列。
      flags (int): `re.IGNORECASE` 等のフラグ。

    返り値:
      List[Pattern[str]]: コンパイル済み正規表現オブジェクトのリスト。

    生成値:
      なし
    """

    return [re.compile(p, flags) for p in patterns]


def iter_local_files_under(roots: List[str], follow_symlinks: bool) -> Iterator[str]:
    """
    機能概要:
      `roots` 配下の通常ファイルを再帰的に列挙する。`roots` 要素がファイルならそのまま列挙する。

    引数:
      roots (List[str]): 探索対象の絶対パス ( ファイル/ディレクトリ ) 一覧。
      follow_symlinks (bool): ディレクトリ走査時にシンボリックリンクを辿る場合 True。

    返り値:
      Iterator[str]: 戻り値としてはイテレータを返す ( for で使用 ) 。

    生成値:
      str: 見つかった各ファイルの「絶対パス」文字列。
    """
    for r in roots:
        if os.path.isfile(r):
            yield os.path.abspath(r)
            continue
        for root, _dirs, files in os.walk(r, followlinks=follow_symlinks):
            for name in files:
                yield os.path.abspath(os.path.join(root, name))


def match_any_abs(abs_path: str, abs_regexes: List[Pattern[str]]) -> bool:
    """
    機能概要:
      絶対パス `abs_path` が `abs_regexes` のいずれかにマッチするかを判定する。

    引数:
      abs_path (str): 対象の絶対パス。
      abs_regexes (List[Pattern[str]]): 絶対パス向け正規表現のリスト。

    返り値:
      bool: 1つ以上にマッチすれば True, どれにもマッチしなければ False。

    生成値:
      なし
    """

    return any(rx.search(abs_path) for rx in abs_regexes)


def iter_relatives(abs_path: str, roots: List[str]) -> Iterator[Tuple[str, str]]:
    """
    機能概要:
      `abs_path` に対して, `roots` の各要素を起点にとった相対パスを求め,
      適用可能な (root, rel) の組を列挙する。

    引数:
      abs_path (str): 対象の絶対パス。
      roots (List[str]): 相対化の起点候補 ( 絶対パス ) 一覧。

    返り値:
      Iterator[Tuple[str, str]]: 戻り値としてはイテレータを返す。

    生成値:
      Tuple[str, str]: (root, rel) のタプル。`rel` は OS 区切りのまま ( 後段で POSIX 化する前段階 ) 。
    """

    for root in roots:
        root_norm = root.rstrip(os.sep)
        if not root_norm:
            continue
        if abs_path == root_norm or abs_path.startswith(root_norm + os.sep):
            rel = abs_path[len(root_norm) + (0 if abs_path == root_norm else 1):]
            yield (root_norm, rel)


def match_any_rel(abs_path: str, roots: List[str], rel_regexes: List[Pattern[str]]) -> bool:
    """
    機能概要:
      `roots` からの相対パスに変換した文字列が `rel_regexes` のいずれかにマッチするか判定する。

    引数:
      abs_path (str): 対象の絶対パス。
      roots (List[str]): 相対化の起点候補のリスト。
      rel_regexes (List[Pattern[str]]): 相対パス向け正規表現のリスト。

    返り値:
      bool: マッチがあれば True, なければ False。

    生成値:
      なし
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
    """
    機能概要:
      絶対/相対パターンに基づき `roots` 配下からファイルを選定し, 送信用の
      {abs -> rel(POSIX)} マップとヒットした絶対パス一覧を返す。
      相対化時には安全性 ( 先頭 '/' 禁止, '..' 逸脱禁止 ) を検査する。

    引数:
      roots (List[str]): 探索 root ( 絶対パス ) 一覧。
      pat_abs (List[Pattern[str]]): 絶対パス向けの正規表現。
      pat_rel (List[Pattern[str]]): 各 root からの相対パス向けの正規表現。
      follow_symlinks (bool): ディレクトリ探索時にシンボリックリンクを辿る場合 True。
      verbose (bool): 走査件数などのログを標準出力へ出す場合 True。

    返り値:
      Tuple[Dict[str, str], List[str]]:
        - 第1要素 (Dict[str, str]): {abs_path -> rel_posix} の対応表。
        - 第2要素 (List[str]): マッチした絶対パス一覧 ( 昇順 ) 。

    生成値:
      なし
    """

    files = list(iter_local_files_under(roots, follow_symlinks=follow_symlinks))
    if verbose:
        print(_("[local] scanned {n} files under {roots}").format(n=len(files), roots=", ".join(roots)))
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
            print(_("[Error] unsafe relative path derived: {src} -> {rel}").format(src=p, rel=rel_posix), file=sys.stderr)
            continue
        rel_map[p] = rel_posix
        hits.add(p)
    return rel_map, sorted(hits)


# ========= アーカイブ ( ローカル作成 / リモート展開 )  =========

def make_python_tar_gz_from_relmap(rel_map: Dict[str, str], verbose: bool) -> str:
    """
    機能概要:
      Python 標準ライブラリ `tarfile` を用いて, `rel_map` に基づく tar.gz を作成する。
      メタ情報 ( ACL/xattr/所有者/一部パーミッション ) の保持は保証しない。
      シンボリックリンクはリンクエントリとして格納する。

    引数:
      rel_map (Dict[str, str]): {abs_path -> rel_posix} の対応表。
      verbose (bool): 進行ログを出力する場合 True。

    返り値:
      str: 生成された一時アーカイブ ( tar.gz ) のローカルファイルパス。

    生成値:
      なし
    """

    fd, tmp = tempfile.mkstemp(prefix="gm_scatter_", suffix=".tar.gz")
    os.close(fd)
    if verbose:
        print(_("[local] pack (python) -> {path}").format(path=tmp))
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
    """
    機能概要:
      ローカルの `tar` 実装を推定し, GNU か BSD かを返す。`tar --version` の先頭行に
      "GNU tar" があれば GNU, "bsdtar" / "libarchive" があれば BSD。Windows も BSD 扱い。
      判別不能な場合は BSD を既定とする。

    引数:
      timeout (float): `--version` 呼び出しのタイムアウト秒数。

    返り値:
      RemoteTarFlavor: GNU または BSD。

    生成値:
      なし
    """

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
    """
    rel_map (abs -> rel) を正しくアーカイブ名に反映するため、テンポラリの
    ステージングディレクトリ配下に 'rel' 階層を作成し、通常ファイルは
    可能ならハードリンク、シンボリックリンクは symlink を再現してから
    外部 tar で一括圧縮する。

    引数:
      rel_map (Dict[str, str]): {abs_path -> rel_posix} の対応表。
      preserve_owner (bool): 抽出時に所有者を復元するか（格納時は GNU/bsdtar 依存）。
      preserve_acls (bool): ACL を格納/復元するか（GNU tar では --acls）。
      preserve_xattrs (bool): xattr を格納/復元するか（GNU tar では --xattrs）。
      preserve_perms (bool): パーミッションを可能な範囲で保持するか。
      verbose (bool): 実行ログ出力。

    返り値:
      str: 生成された tar.gz のローカルパス。
    """
    import errno
    import shutil
    tar_cmd = "tar.exe" if is_windows() else "tar"

    # 出力ファイルとステージングディレクトリを用意
    # mkstemp() は FD を返すので必ずクローズしてから tar に渡す
    # ( Windows のロック回避, Linux/FreeBSDでのファイル記述子リーク防止 )
    _fd, _tmp = tempfile.mkstemp(prefix="gm_scatter_", suffix=".tar.gz")
    try:
        os.close(_fd)
    except Exception:
        pass
    out_path = Path(_tmp)

    staging_dir = Path(tempfile.mkdtemp(prefix="gm_scatter_stage_"))
    try:
        def _stage_file(src_abs: str, rel_path: str) -> None:
            rel_p = staging_dir / rel_path
            rel_p.parent.mkdir(parents=True, exist_ok=True)
            try:
                if os.path.islink(src_abs):
                    target = os.readlink(src_abs)
                    try:
                        os.symlink(target, rel_p)
                    except FileExistsError:
                        pass
                else:
                    try:
                        os.link(src_abs, rel_p)
                    except OSError as e:
                        if e.errno in (errno.EXDEV, errno.EPERM, errno.EISDIR):
                            # EISDIR は通常ここに来ないが保険
                            shutil.copy2(src_abs, rel_p, follow_symlinks=False)
                        else:
                            raise
            except Exception as e:
                print(_("[local] Error staging {src} -> {dst}: {msg}").format(src=src_abs, dst=str(rel_p), msg=e), file=sys.stderr)

        def _stage_empty_dir(rel_dir: str) -> None:
            try:
                (staging_dir / rel_dir).mkdir(parents=True, exist_ok=True)
            except Exception as e:
                print(_("[local] Error creating empty dir {dst}: {msg}").format(dst=str(staging_dir / rel_dir), msg=e), file=sys.stderr)

        # 1) rel 階層をステージングに再現（ディレクトリは再帰的に展開）
        for ap, rel in rel_map.items():
            try:
                if os.path.isdir(ap) and not os.path.islink(ap):
                    # ディレクトリ：中身を再帰的に反映。空ディレクトリも作成。
                    had_entry = False
                    for root, _dirs, files in os.walk(ap, followlinks=False):
                        # 空ディレクトリ対応
                        rel_under = os.path.relpath(root, ap).replace(os.sep, "/")
                        dir_rel = rel if rel_under in (".", "") else f"{rel}/{rel_under}"
                        _stage_empty_dir(dir_rel)
                        if files:
                            had_entry = True
                        for name in files:
                            src_abs = os.path.join(root, name)
                            under = os.path.relpath(src_abs, ap).replace(os.sep, "/")
                            _stage_file(src_abs, f"{rel}/{under}")
                    if not had_entry:
                        # 完全に空だった場合もディレクトリそのものを作る
                        _stage_empty_dir(rel)
                else:
                    # 通常ファイル or シンボリックリンク
                    _stage_file(ap, rel)
            except Exception as e:
                print(_("[local] Error staging {src} -> {dst}: {msg}").format(src=ap, dst=str(staging_dir / rel), msg=e), file=sys.stderr)

        # 2) 外部 tar で固める（GNU tar なら --acls/--xattrs を付与）
        cmd: List[str] = [tar_cmd, "-czf", str(out_path)]
        local_flavor = probe_local_tar_flavor()

        if local_flavor is RemoteTarFlavor.GNU:
            if preserve_acls:
                cmd.append("--acls")
            if preserve_xattrs:
                cmd.append("--xattrs")
        else:
            if (preserve_acls or preserve_xattrs):
                print(_("[local] Warning: The behavior of storing/restoring ACLs/extended attributes in local bsdtar/libarchive is environment-dependent."),
                      file=sys.stderr)

        # NOTE: --preserve-permissions/-p は「抽出時」のオプション。
        # 作成時に指定すると, エラーになる実装があるため付与しない。
        cmd += ["-C", str(staging_dir), "."]

        if verbose:
            print(_("[local] exec: {cmd}").format(cmd=" ".join(shlex.quote(x) for x in cmd)))

        subprocess.check_call(cmd)
        return str(out_path)

    finally:
        # ステージング削除（失敗は無視）
        try:
            shutil.rmtree(staging_dir, ignore_errors=True)
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
    """
    機能概要:
      ローカルの `local_tgz` を一時パスへ SFTP でアップロードし, リモートで `tar` を用いて
      `remote_dest` 直下へ展開する。`preserve-*` の指定に応じて tar のオプションを構成し,
      非 GNU tar の場合は無視される可能性がある ( 呼出側で警告/エラー補足 ) 。
      展開後は一時ファイルを削除する。

    引数:
      ssh (paramiko.SSHClient): 接続済み SSH クライアント。
      sftp (paramiko.SFTPClient): 接続済み SFTP クライアント。
      local_tgz (str): ローカル tar.gz パス。
      remote_dest (str): 展開先ディレクトリ ( POSIX パス ) 。
      use_sudo (bool): 抽出コマンドに `sudo -n` を付与する場合 True。
      preserve_owner (bool): 所有者/グループを復元するか。
      preserve_acls (bool): ACL を復元するか。
      preserve_xattrs (bool): xattr を復元するか。
      preserve_perms (bool): パーミッションを復元するか ( `-p` ) 。
      timeout (float): コマンドのタイムアウト秒数。
      verbose (bool): 進行ログを出力する場合 True。
      flavor (RemoteTarFlavor): リモートの tar フレーバ ( GNU/bsd ) 。

    返り値:
      int: 処理カウント ( 1 アーカイブ展開を 1 として計上 ) 。

    生成値:
      なし
    """

    ident = posixpath.join("/tmp", f"gm_scatter_{uuid4().hex}_{os.getpid()}.tar.gz")

    # 先に tar の存在を確認 ( アップロードの無駄を避ける )
    rc_tar, _o, _e = run_cmd(ssh, "command -v tar >/dev/null 2>&1", timeout)
    if rc_tar != 0:
        raise RuntimeError(_("remote host has no 'tar' in PATH"))

    # 実装種別 ( GNU/bsd ) は呼び出し元で判別済み
    if verbose:
        print(_("[remote] detected tar flavor: {kind}").format(kind=flavor.kind))

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
        print(_("[remote] extract: {cmd}").format(cmd=cmd))

    try:
        rc, _o, err = run_cmd(ssh, cmd, timeout)
        if rc != 0:
            raise RuntimeError(_("remote unpack failed: {msg}").format(msg=err.decode(errors='ignore')))
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
    """
    機能概要:
      SFTP で `{abs -> rel}` の対応に従ってファイルを個別転送する。メタ情報保持は行わない。
      `empty_dirs` が与えられた場合は, 相対パスの空ディレクトリも事前作成する。

    引数:
      sftp (paramiko.SFTPClient): 接続済み SFTP クライアント。
      rel_map (Dict[str, str]): {abs_path -> rel_posix} の対応表。
      remote_dest (str): 配置先の基点ディレクトリ ( POSIX ) 。
      verbose (bool): put/mkdir のログを出力する場合 True。
      empty_dirs (Optional[Set[str]]): 先に作成すべき空ディレクトリの相対パス集合。

    返り値:
      int: 実行件数 ( 空ディレクトリ作成 + ファイル put の合計 ) 。

    生成値:
      なし
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
                print(_("[Error] unsafe dir rel (leading '/'): {drel}").format(drel=drel), file=sys.stderr)
                continue
            rdir = posixpath.normpath(f"{remote_dest}/{drel}")
            try:
                ensure = rdir  # ensure_dir はパスが既存でも OK
                try:
                    sftp.stat(ensure)
                except IOError:
                    sftp_mkdirs(sftp, ensure)
                if verbose:
                    print(_("[mkdir(empty)] {dir}").format(dir=rdir))
                count += 1
            except Exception as e:
                print(_("[Error] mkdir(empty) failed: {dir}: {msg}").format(dir=rdir, msg=e), file=sys.stderr)

    # ファイルを put する
    for apath, rel in rel_map.items():
        if rel.startswith("/"):
            print(_("[Error] unsafe rel (leading '/'): {rel}").format(rel=rel), file=sys.stderr)
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
            print(_("[put][{tag}] {src} -> {dst}").format(tag=tag, src=apath, dst=rpath))

        try:
            if os.path.islink(apath):
                # symlink はまずリンクとして作成を試みる（サーバが許可しない場合あり）
                try:
                    target = os.readlink(apath)
                    # Paramiko SFTP に symlink が無い実装もあるので getattr で確認
                    if hasattr(sftp, "symlink"):
                        sftp.symlink(target, rpath)
                        count += 1
                    else:
                        raise NotImplementedError("SFTP symlink not supported by client")
                except Exception as _e:
                    print(_("[Warning] SFTP symlink unsupported/failed; skipped link (use --pack to preserve): {src}").format(src=apath), file=sys.stderr)
            else:
                sftp.put(apath, rpath)
                count += 1
        except Exception as e:
            print(_("[Error] sftp.put failed: {src} -> {dst}: {msg}").format(src=apath, dst=rpath, msg=e), file=sys.stderr)

    return count


# ========= SELinux restorecon =========

def maybe_run_restorecon(ssh: paramiko.SSHClient, dest_posix: str, selinux_mode: str, timeout: float, use_sudo: bool, verbose: bool) -> Optional[str]:
    """
    機能概要:
      `--selinux` オプションの指定に従って, 必要に応じて `restorecon -RF` を実行する。
      `policy` は厳格 ( 対応不可なら例外 ) , `auto`/`archive` は対応可能なら実行,
      `ignore` は何もしない。失敗時の警告文言を呼び出し側へ返すことがある。

    引数:
      ssh (paramiko.SSHClient): 接続済み SSH クライアント。
      dest_posix (str): 対象のディレクトリ ( POSIX パス ) 。
      selinux_mode (str): "auto" / "policy" / "archive" / "ignore"。
      timeout (float): コマンドのタイムアウト秒数。
      use_sudo (bool): `restorecon` 実行に sudo を付与する場合 True。
      verbose (bool): 実行コマンドのログ表示を行う場合 True。

    返り値:
      Optional[str]: 警告や情報文 ( 問題なければ None ) 。

    生成値:
      なし
    """

    if selinux_mode == "ignore":
        return None
    capable = probe_selinux_capable(ssh, timeout)
    if selinux_mode == "policy":
        if not capable:
            raise RuntimeError(_("SELinux policy required but not available on remote (selinuxfs or restorecon missing)."))
        cmd = f"{'sudo -n ' if use_sudo else ''}restorecon -RF {shlex.quote(dest_posix)}"
        if verbose:
            print(_("[remote] restorecon: {cmd}").format(cmd=cmd))
        rc, _o, err = run_cmd(ssh, cmd, timeout)
        if rc != 0:
            raise RuntimeError(_("restorecon failed: {msg}").format(msg=err.decode(errors='ignore')))
        return None
    if selinux_mode in ("auto", "archive"):
        if capable:
            cmd = f"{'sudo -n ' if use_sudo else ''}restorecon -RF {shlex.quote(dest_posix)}"
            if verbose:
                print(_("[remote] restorecon: {cmd}").format(cmd=cmd))
            rc, _o, err = run_cmd(ssh, cmd, timeout)
            if rc != 0:
                return _("restorecon failed: {msg}").format(msg=err.decode(errors='ignore'))
            return None
        return _("SELinux not supported on remote; skipped restorecon")
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
    """
    機能概要:
      単一ホスト `host` に対する配布処理を実行する。SFTP 逐次転送または
      tar.gz による一括転送 ( --pack ) を選択でき, 必要に応じて preserve-* や
      SELinux ハンドリングを行う。結果は `HostResult` に集約して返す。

    引数:
      host (str): 配布対象ホスト名 ( SSH 接続先 ) 。
      dest (str): リモート配置先の基点ディレクトリ。相対指定時は `account` の HOME 配下。
      rel_map (Dict[str, str]): {abs_path -> rel_posix} の対応表 ( 重複除去済み ) 。
      ssh_user (str): SSH ログインユーザ ( 接続ユーザ ) 。
      account (str): 配置先の意味的アカウント ( HOME 解決等に用いる ) 。
      port (int): SSH ポート番号。
      key (Optional[str]): SSH 秘密鍵ファイルのパス ( 省略可 ) 。
      password (Optional[str]): SSH パスワード ( 推奨しない, 省略可 ) 。
      timeout (float): SSH/リモートコマンドのタイムアウト秒数。
      strict (bool): 厳格ホスト鍵チェックを行う場合 True。
      dry_run (bool): 計画のみを表示して実動しない場合 True。
      verbose (bool): 詳細ログを出力する場合 True。
      pack (bool): 一括アーカイブ転送 ( True ) / 逐次 SFTP ( False ) 。
      preserve_owner (bool): 抽出時に所有者を復元するか。
      preserve_acls (bool): 抽出時に ACL を復元するか。
      preserve_xattrs (bool): 抽出時に xattr を復元するか。
      preserve_perms (bool): 抽出時にパーミッションを復元するか。
      selinux_mode (str): "auto" / "policy" / "archive" / "ignore"。
      request_preserve_owner (bool): ユーザーから preserve-owner が明示要求されたか。
      request_preserve_acls (bool): ユーザーから preserve-acls が明示要求されたか。
      request_preserve_xattrs (bool): ユーザーから preserve-xattrs が明示要求されたか。
      request_preserve_perms (bool): ユーザーから preserve-perms が明示要求されたか。
      empty_dirs (Optional[Set[str]]): SFTP モードで事前作成する空ディレクトリの集合。

    返り値:
      HostResult: 実行結果 ( uploaded 件数, warnings, errors を含む ) 。

    生成値:
      なし
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
            print(_("[{host}] DRY-RUN {mode}: -> {dest} files={n}{extra}").format(
                host=host, mode=mode, dest=remote_dest,
                n=len(rel_map),
                extra=extra
            ))
            return HostResult(host=host, uploaded=0, warnings=warnings, errors=errors)

        try:
            ensure_remote_dir(ssh, remote_dest, use_sudo=use_sudo, timeout=timeout)
        except RuntimeError as e:
            # ここで host / ssh_user / account / use_sudo / dest を付与して再スロー
            raise RuntimeError(_("{msg} (host={host}, ssh_user={ssh_user}, account={account}, use_sudo={use_sudo}, dest={dest}). "
                                "If sudo is required, configure NOPASSWD for the ssh_user on the remote "
                                "or run with --ssh-user equal to --user when appropriate.").format(
                msg=e, host=host, ssh_user=ssh_user, account=account, use_sudo=use_sudo, dest=remote_dest
            ))

        # --- 事前能力チェック / SELinux archive 厳格条件 ---
        # preserve-* の要求有無 ( ユーザーが明示 True か, pack 既定 True 解決後の値 )
        need_preserve = preserve_owner or preserve_acls or preserve_xattrs or preserve_perms
        # SELinux モード archive は pack + xattrs が必須, かつ xattr 書込み能力が必要
        if selinux_mode == "archive":
            if not pack or not preserve_xattrs:
                raise RuntimeError(_("--selinux=archive requires --pack and --preserve-xattrs (both)."))
            if not check_remote_xattr_capable(ssh, timeout):
                raise RuntimeError(_("remote host not xattr-capable for --selinux=archive (setfattr not found)."))

        # ACL/xattr 復元のための補助ツール存在チェック ( 仕様上は警告降格, archive の xattr は厳格 )
        if preserve_acls and not check_remote_tool(ssh, "setfacl", timeout):
            msg = _("ACL: remote 'setfacl' not found; ACL restore may be skipped or incomplete.")
            (errors if request_preserve_acls else warnings).append(msg)
        if preserve_xattrs and not check_remote_tool(ssh, "setfattr", timeout):
            msg = _("xattr: remote 'setfattr' not found")
            if selinux_mode == "archive":
                # archive は厳格
                raise RuntimeError(msg + _(" (required for --selinux=archive)."))
            else:
                (errors if request_preserve_xattrs else warnings).append(msg + _("; xattr restore may be skipped."))

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
                msg = _("{name}: non-GNU tar on remote; option may be ignored.").format(name=name)
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
                warnings.append(_("preserve-owner: may fail without sudo/root privileges."))
            if preserve_perms:
                warnings.append(_("preserve-perms: some permission restorations may fail without sudo/root privileges."))

        note = maybe_run_restorecon(ssh, remote_dest, selinux_mode=selinux_mode, timeout=timeout, use_sudo=use_sudo, verbose=verbose)
        if note:
            warnings.append(note)

    except Exception as e:
        if verbose:
            traceback.print_exc()
        errors.append(_("{etype}: {msg}").format(etype=type(e).__name__, msg=e))
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
    """
    機能概要:
      本ツールのコマンドライン仕様に従って `argparse.ArgumentParser` を生成し, 返す。
      位置引数 `src... dest` と, 選択/SSH/転送/SELinux 関連の各オプションを定義する。

    引数:
      なし

    返り値:
      argparse.ArgumentParser: 解析器インスタンス。

    生成値:
      なし
    """

    ap = argparse.ArgumentParser(
        description=_("Distribute local files/dirs to multiple remote hosts under a destination directory."),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    ap.add_argument("src", nargs="*", help=_("0 or more local files/dirs. Relative src is resolved from the current directory."))
    ap.add_argument("dest", nargs="?", help=_("Remote destination directory. If relative, it is resolved from the remote target account's HOME (i.e., --user). This is independent of the SSH login user (--ssh-user)."))

    # Selection (local)
    ap.add_argument("-a", "--pattern-abs", action="append", default=[], help=_("ABSOLUTE local path regex (repeatable)."))
    ap.add_argument("-r", "--pattern-rel", action="append", default=[], help=_("RELATIVE path regex to each --root (repeatable)."))
    ap.add_argument("-R", "--root", action="append", default=[], help=_("Local search root(s). Default: current directory."))
    ap.add_argument("-i", "--ignore-case", action="store_true", help=_("Compile regexes with IGNORECASE."))

    # Remote & SSH
    ap.add_argument("-H", "--hosts", default="hostfile", help=_("Hosts file. Default: hostfile."))
    ap.add_argument("-u", "--user", default=getpass.getuser(), help=_("Target account semantics on remote. Default: local user."))
    ap.add_argument("-s", "--ssh-user", default=None, help=_("SSH login user. Default: same as --user."))
    ap.add_argument("-P", "--port", type=int, default=DEFAULT_SSH_PORT, help=_("SSH port. Default: {port}.").format(port=DEFAULT_SSH_PORT))
    ap.add_argument("-K", "--key", default=None, help=_("SSH private key file."))
    ap.add_argument("-W", "--password", default=None, help=_("SSH password (not recommended)."))
    ap.add_argument("-T", "--timeout", type=float, default=DEFAULT_TIMEOUT, help=_("SSH/command timeout seconds. Default: {sec}.").format(sec=DEFAULT_TIMEOUT))
    ap.add_argument("-S", "--strict-host-key-checking", action="store_true", help=_("Enable strict host key checking."))

    # Transfer mode
    ap.add_argument("--pack", action="store_true", help=_("Create local tar.gz -> upload -> remote extract."))
    # preserve-* は --pack 時に未指定なら既定 True。BooleanOptionalAction で --no-xxx も受け付ける。
    ap.add_argument("--preserve-perms", action=argparse.BooleanOptionalAction, default=None,
                    help=_("Preserve permissions on extract (tar -p). Use --no-preserve-perms to disable."))
    ap.add_argument("--preserve-owner", action=argparse.BooleanOptionalAction, default=None,
                    help=_("Preserve owner/group on extract (if supported). Use --no-preserve-owner to disable."))
    ap.add_argument("--preserve-acls", action=argparse.BooleanOptionalAction, default=None,
                    help=_("Preserve ACLs on extract (if supported). Use --no-preserve-acls to disable."))
    ap.add_argument("--preserve-xattrs", action=argparse.BooleanOptionalAction, default=None,
                    help=_("Preserve xattrs on extract (if supported). Use --no-preserve-xattrs to disable."))
    ap.add_argument("-j", "--parallel", type=int, default=DEFAULT_PARALLEL, help=_("Parallel hosts. Default: {n}.").format(n=DEFAULT_PARALLEL))
    ap.add_argument("-n", "--dry-run", action="store_true", help=_("Show plan only; do not upload or extract."))
    ap.add_argument("-v", "--verbose", action="store_true", help=_("Verbose logs."))
    ap.add_argument("--follow-symlinks", action="store_true", help=_("Follow symlinks when scanning locals."))
    ap.add_argument("--include-empty-dirs", action="store_true",
                    help=_("SFTP mode: also create empty directories found under explicit src directories."))

    # SELinux
    ap.add_argument("--selinux", choices=["auto", "policy", "archive", "ignore"], default="auto",
                    help=_("SELinux handling mode. Default: auto."))
    return ap


def main() -> None:
    """
    機能概要:
      CLI 引数を解析し, 入力検証, 転送対象ファイル選定, preserve/SELinux の既定値処理を行った上で,
      ホスト並列に配布を実行する。サマリを出力し, エラー/中断時は EXIT_PARTIAL を返す。

    引数:
      なし

    返り値:
      None: 正常終了時は EXIT_SUCCESS でプロセス終了, 部分失敗・中断時は EXIT_PARTIAL で終了。

    生成値:
      なし
    """

    setup_i18n() # 国際化セットアップ

    ap = build_argparser()
    args = ap.parse_args()

    # --- 位置引数 ( 最後が dest ) 解釈 ---
    if args.dest is None:
        if not args.src:
            print(_("dest is required as the last positional argument."), file=sys.stderr)
            sys.exit(EXIT_INVALID_ARGS)
        args.dest = args.src[-1]
        args.src = args.src[:-1]

    dest: str = args.dest
    src_args: List[str] = list(args.src)

    using_patterns: bool = bool(args.pattern_abs or args.pattern_rel or args.root)
    roots_opt: List[str] = args.root if args.root else [os.getcwd()]

    if not dest:
        print(_("dest is required."), file=sys.stderr)
        sys.exit(EXIT_INVALID_ARGS)

    hosts = parse_hosts_file(args.hosts)
    if not hosts:
        print(_("No hosts found in hosts file."), file=sys.stderr)
        sys.exit(EXIT_INVALID_ARGS)

    if len(src_args) == 0 and not (args.pattern_abs or args.pattern_rel):
        print(_("No src and no patterns provided. At least one src or a pattern must be specified."), file=sys.stderr)
        sys.exit(EXIT_NO_TARGETS)

    flags = re.IGNORECASE if args.ignore_case else 0
    try:
        pat_abs = compile_many(args.pattern_abs, flags)
        pat_rel = compile_many(args.pattern_rel, flags)
    except re.error as e:
        print(_("Invalid regex: {msg}").format(msg=e), file=sys.stderr)
        sys.exit(EXIT_INVALID_ARGS)

    rel_map: Dict[str, str] = {}
    collisions:int = 0
    empty_dirs_rel: Set[str] = set()
    seen_abs: Set[str] = set()

    for s in src_args:
        apath = os.path.abspath(s)
        if not os.path.exists(apath) and not os.path.islink(apath):
            print(_("[Warning] src not found (skipped): {src}").format(src=s), file=sys.stderr)
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
                # 非 POSIX 絶対パス ( 例: "C:\\path\\to\\file" ) は
                # ドライブレターをサニタイズして先頭ディレクトリ化する。
                #   "C:\\path\\to\\file" -> "C_/path/to/file"
                _p = apath
                drive = ""
                try:
                    # Windows のドライブレター切り出し
                    import ntpath
                    drive, tail = ntpath.splitdrive(_p)
                    if drive:
                        drive = drive.rstrip(":").upper() + "_"   # "C_"
                        tail = tail.lstrip("\\/")
                        rel = drive + tail.replace("\\", "/")
                    else:
                        # ドライブなしの特殊ケースは basename にフォールバック
                        rel = to_posix_rel(os.path.basename(apath))
                except Exception:
                    rel = to_posix_rel(os.path.basename(apath))

        else:
            rel = to_posix_rel(rel_opt)

        # srcオプションでディレクトリを指定した場合にSFTP転送向けにファイルパスを展開
        # ( --pack なしの場合のみ )
        if os.path.isdir(apath) and not args.pack:
            for root, _dirs, files in os.walk(apath, followlinks=args.follow_symlinks):
                # ディレクトリ側のシンボリックリンクも, --follow-symlinks が無い場合は安全側でスキップ
                if os.path.islink(root) and not args.follow_symlinks:
                    print(_("[Warning] symlinked directory skipped (SFTP mode without --follow-symlinks): {dir}").format(dir=root), file=sys.stderr)
                    continue

                for name in files:
                    f_abs = os.path.abspath(os.path.join(root, name))

                    # 1) 壊れたシンボリックリンクは除外 ( 既存 )
                    if os.path.islink(f_abs) and not os.path.exists(f_abs):
                        print(_("[Warning] broken symlink (skipped): {path}").format(path=f_abs), file=sys.stderr)
                        continue

                    # 1.5) SFTPモードで --follow-symlinks なしなら, ファイル型のシンボリックリンクも除外
                    if os.path.islink(f_abs) and not args.follow_symlinks:
                        print(_("[Warning] symlink (skipped on SFTP without --follow-symlinks): {path}").format(path=f_abs), file=sys.stderr)
                        continue

                    # 2) 通常ファイル/シンボリックリンク以外は除外 ( 既存 )
                    try:
                        st = os.lstat(f_abs)
                        if not stat.S_ISREG(st.st_mode) and not os.path.islink(f_abs):
                            print(_("[Warning] non-regular file (skipped): {path}").format(path=f_abs), file=sys.stderr)
                            continue
                    except FileNotFoundError:
                        print(_("[Warning] vanished during scan (skipped): {path}").format(path=f_abs), file=sys.stderr)
                        continue

                    # apath からの相対を作って rel の下にぶら下げる
                    under = os.path.relpath(f_abs, apath).replace(os.sep, "/")
                    f_rel = to_posix_rel(posixpath.normpath(f"{rel}/{under}"))
                    # 安全チェック
                    if (not f_rel) or f_rel.startswith("/") or f_rel == ".." or f_rel.startswith("../"):
                        print(_("[Error] unsafe derived relative path: {src} -> {rel}").format(src=f_abs, rel=f_rel), file=sys.stderr)
                        continue
                    # 重複チェック
                    if f_abs in seen_abs:
                        print(_("[Warning] duplicate src ignored: {src}").format(src=f_abs), file=sys.stderr)
                        continue

                    # rel の衝突検出（first-wins）: 既に他の abs が同じ rel を占有していればスキップ
                    if f_rel in rel_map.values():
                        # 将来 --verbose 詳細列挙予定（現時点はサマリのみ）
                        # 衝突カウンタは後段でサマリ出力するため、ここでは stderr への詳細出力はしない
                        collisions += 1
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
                        print(_("[Error] unsafe empty dir rel: {root} -> {rel}").format(root=root, rel=d_rel), file=sys.stderr)
            continue

        # 通常 ( ファイル or --pack 時のディレクトリ ) はそのまま登録
        if apath in seen_abs:
            print(_("[Warning] duplicate src ignored: {src}").format(src=apath), file=sys.stderr)
            continue

        # rel の衝突検出（first-wins）
        if rel in rel_map.values():
            collisions += 1
            # 先勝ルール：後続は破棄
        else:
            seen_abs.add(apath)
            rel_map[apath] = rel

    roots_abs = [os.path.abspath(os.path.expanduser(r)) for r in roots_opt]
    for r in roots_abs:
        if not os.path.exists(r):
            print(_("[Error] root not found: {root}").format(root=r), file=sys.stderr)
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
                print(_("[Warning] duplicate (pattern vs src) ignored: {src}").format(src=apath), file=sys.stderr)
                continue
            seen_abs.add(apath)
            rel_map[apath] = rel

    if not rel_map and not (args.include_empty_dirs and empty_dirs_rel and not args.pack):
        print(_("No local files to distribute after selection."), file=sys.stderr)
        sys.exit(EXIT_NO_TARGETS)

    # preserve-* 既定値の決定：
    #   - pack のとき: 未指定(None)  =>  True / 指定(True)  =>  True
    #   - pack でない: すべて False ( SFTP では保持しない )

    # 明示 True を保持 ( 警告/エラーの強度切替に使う )
    user_set_preserve_perms_true  = (args.preserve_perms  is True)
    user_set_preserve_owner_true  = (args.preserve_owner  is True)
    user_set_preserve_acls_true   = (args.preserve_acls   is True)
    user_set_preserve_xattrs_true = (args.preserve_xattrs is True)

    if args.pack:
        # 未指定(None) のとき既定 True, 明示指定があればその値を尊重
        preserve_perms  = True if args.preserve_perms  is None else bool(args.preserve_perms)
        preserve_owner  = True if args.preserve_owner  is None else bool(args.preserve_owner)
        preserve_acls   = True if args.preserve_acls   is None else bool(args.preserve_acls)
        preserve_xattrs = True if args.preserve_xattrs is None else bool(args.preserve_xattrs)
    else:
        preserve_perms = preserve_owner = preserve_acls = preserve_xattrs = False
    if (args.preserve_perms or args.preserve_owner or args.preserve_acls or args.preserve_xattrs) and not args.pack:
        print(_("[Warning] --preserve-* options are ignored without --pack."), file=sys.stderr)

    require_local_tar_when_preserve(preserve_owner, preserve_acls, preserve_xattrs, preserve_perms)

    ssh_user = args.ssh_user or args.user

    print(_("Hosts: {n}  Mode: {mode}  Dest: {dest}").format(n=len(hosts), mode=("PACK" if args.pack else "SFTP"), dest=dest))
    if using_patterns:
        print(_("Select: patterns ({na} abs, {nr} rel) roots={roots}").format(
            na=len(args.pattern_abs), nr=len(args.pattern_rel), roots=", ".join(roots_opt)))
    else:
        print(_("Sources: {n} (explicit)").format(n=len(src_args)))
    print(_("SSH  : ssh-user={ssh_user} user={user} port={port} strict={strict}").format(
        ssh_user=ssh_user, user=args.user, port=args.port, strict=args.strict_host_key_checking))
    print(_("Preserve: perms={perms} owner={owner} acls={acls} xattrs={xattrs}").format(
        perms=preserve_perms, owner=preserve_owner, acls=preserve_acls, xattrs=preserve_xattrs))
    print(_("SELinux: {mode}").format(mode=args.selinux))

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
                    print(_("[{host}] ERROR x{n}").format(host=res.host, n=len(res.errors)))
                else:
                    action = "planned" if args.dry_run else ("packed" if args.pack else "uploaded")
                    print(_("[{host}] {action}: {n}").format(host=res.host, action=action, n=res.uploaded))
                for w in res.warnings:
                    print(_("[{host}] Warning: {msg}").format(host=res.host, msg=w), file=sys.stderr)
                for e in res.errors:
                    print(_("[{host}] Error: {msg}").format(host=res.host, msg=e), file=sys.stderr)
        except KeyboardInterrupt:
            print("\n")
            print(_("[Info] Interrupted by user. Cancelling remaining tasks..."), file=sys.stderr)
            for fut in futs:
                fut.cancel()
            interrupted = True
            # 部分結果でサマリするため, そのまま抜ける

    total_uploaded = sum(r.uploaded for r in results if not r.errors)
    warn_hosts = [r for r in results if r.warnings]
    err_hosts = [r for r in results if r.errors]

    print("\n")
    print(_("=== Summary ==="))
    if collisions > 0:
        print(_("[Warning] Relative path collisions (first-wins policy): {n} skipped").format(n=collisions), file=sys.stderr)
    print(_("Hosts processed: {n}").format(n=len(results)))
    label = _('planned') if args.dry_run else (_('packed(archives)') if args.pack else _('files/dirs created'))
    print(_("Total {label}: {n}").format(label=label, n=total_uploaded))

    if warn_hosts:
        warn_count = sum(len(r.warnings) for r in warn_hosts)
        print(_("Warnings: {cnt} on {hosts} host(s)").format(cnt=warn_count, hosts=len(warn_hosts)))
    if err_hosts:
        err_count = sum(len(r.errors) for r in err_hosts)
        print(_("Errors (continuing): {cnt} on {hosts} host(s)").format(cnt=err_count, hosts=len(err_hosts)))

    if interrupted or err_hosts:
        sys.exit(EXIT_PARTIAL)

    sys.exit(EXIT_SUCCESS)

if __name__ == "__main__":
    socket.setdefaulttimeout(DEFAULT_SOCKET_TIMEOUT)
    main()
