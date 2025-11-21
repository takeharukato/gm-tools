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

"""gather/scatter で共有するパス正規化ユーティリティ群。

ホームディレクトリ展開, 正規表現を含む SRC トークンの解釈, Windows/UNIX の
パス差異吸収などをまとめて提供する。CLI から直接利用する際は, このモジュール
を import して個別関数を呼び出す。

Examples:
    >>> normalize_rel_for_dest('foo/../bar/baz')
    'bar/baz'
    >>> is_windows_abs('C:/Windows')
    True
    >>> dest_rel_from_abs('/var/log/syslog')
    'var/log/syslog'
"""

from __future__ import annotations

import os
import pathlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# === Constants (shared) ===
TILDE_USER_RE: re.Pattern[str] = re.compile(r"^~([A-Za-z0-9._-]+)(?:$|[\\/])")
HOME_DETECT_CMD_FMT: str = "getent passwd {user} | cut -d: -f6"
HOME_FALLBACK_ROOT: str = "/root"
HOME_FALLBACK_PREFIX: str = "/home"

# === Constants (local) ===
# エスケープされていないこれらの文字が現れた時点で「ここから正規表現」
# Windows のファイル名に使えない文字 ( 予約文字 )
#   https://learn.microsoft.com/windows/win32/fileio/naming-a-file
WINDOWS_FORBIDDEN_CHARS = '<>:"\\|?*'
WINDOWS_TRAILING_STRIP = ' .'  # 末尾のスペース・ドットは不可
WINDOWS_ABS_RE: re.Pattern[str] = re.compile(r"^[A-Za-z]:[\\/]")
WINDOWS_FORBIDDEN_RE = re.compile(r'[<>:"\\|?*]')
# Windows 予約デバイス名 ( 拡張子が付いていても不可 ) の回避用
WINDOWS_RESERVED_DEVICES = {
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}

CORE_PATH_HANDLING_REGEX_META_CHARS = r'^$*+?{}[]\|()'  # '.' を除外
CORE_PATH_HANDLING_REGEX_META_SET = set(CORE_PATH_HANDLING_REGEX_META_CHARS)
CORE_PATH_HANDLING_REGEX_META_COMPILED = re.compile(rf"[{re.escape(CORE_PATH_HANDLING_REGEX_META_CHARS)}]")

# ============================================================
# Constants (即値の一元化・再利用)
# ============================================================
SEP_POSIX: str = "/"
SEP_WIN: str = "\\"
UNC_PREFIX: str = r"\\"

# scatter/gather 共通で使える正規表現 ( 命名は衝突しないように接頭辞を付与 )
RE_TILDE_SELF: "re.Pattern[str]" = re.compile(r"^~(?:[/\\]|$)")
RE_REL_LEADING_DOT: "re.Pattern[str]" = re.compile(r"^(?:\./)+")
RE_MULTI_SLASH: "re.Pattern[str]" = re.compile(r"/+")

# tar / path
TAR_MODE_W_GZ: str = "w:gz"
TAR_MODE_R_GZ: str = "r:gz"
DEST_PATH_SEP: str = "/"

def _sanitize_remote_abs_for_local(remote_abs_path: str) -> str:
    """リモート絶対パスをローカル保存用の相対パスに変換する。

    変換時は以下のルールで安全化する。

    - バックスラッシュをスラッシュに統一する。
    - 先頭スラッシュ 1 本は単純に除去し, "//" 以上は先頭に ``"_"`` を立てる。
    - Windows ドライブレター ``"X:/"`` は ``"X_/"`` に置き換える。
    - 中間の空セグメント ``"//"`` は ``"_"`` に置換する。
    - 末尾の ``"/"`` は除去し, 末尾に ``"_"`` は付けない。
    - 禁止記号 ( ``<>:"\\|?*`` ) を ``"_"`` に置換し, 末尾のスペース・ドットを削る。
    - Windows 予約デバイス名は ``"_"`` を接頭して安全化する。
    - すべて空になった場合は ``"_"`` を返す。

    Args:
        remote_abs_path (str): リモート側での絶対パス表現。

    Returns:
        str: DEST 直下で用いる相対パス。

    Examples:
        >>> _sanitize_remote_abs_for_local('/etc/hosts')
        'etc/hosts'
        >>> _sanitize_remote_abs_for_local('//srv//share')
        '_/srv/_/share'
        >>> _sanitize_remote_abs_for_local('C:/Windows/System32/')
        'C_/Windows/System32'
    """
    p = remote_abs_path.replace("\\", "/")

    # 'X:/...'  =>  'X_/...' ( 絶対ドライブパスのみ )
    if re.match(r'^[A-Za-z]:/', p):
        p = p.replace(":/", "_/", 1)

    # 先頭・末尾スラッシュの情報を保持
    leading_slashes = len(p) - len(p.lstrip("/"))
    had_trailing_slash = p.endswith("/")

    # 先頭スラッシュはすべて剥がして相対化 ( UNC 由来は後段で '_' を立てて表現 )
    rel = p.lstrip("/")

    parts_raw = rel.split("/")

    # 末尾スラッシュに由来する空要素は「すべて」捨てる ( 末尾 '_' は作らない )
    # 例: "C:/path///" や "/a/b//" などでも末尾に '_' を作らない
    if had_trailing_slash:
        while parts_raw and parts_raw[-1] == "":
            parts_raw.pop()

    out_parts: list[str] = []

    # 先頭が '//' 以上なら, 空セグメントの存在を先頭 '_' で保持
    if leading_slashes >= 2:
        out_parts.append("_")

    for comp in parts_raw:
        if comp == "":
            # 中間の '//'  =>  '_' を保持 ( UNC など空セグメントの情報保存 )
            out_parts.append("_")
            continue
        if comp == ".":
            # no-op
            continue
        if comp == "..":
            # 絶対パス基準なのでルートより上は不可。
            # 先頭の UNC 空セグメント('_')は「ポップ対象にしない」ことでルート超えを防ぐ。
            if out_parts and out_parts[-1] != "_":
                out_parts.pop()
            # それ以外 ( ポップできない＝ルート超え要求 ) は無視して落とす
            continue

        # 禁止記号を '_' に
        comp = WINDOWS_FORBIDDEN_RE.sub("_", comp)
        # 末尾スペース/ドットを削除
        comp = comp.rstrip(WINDOWS_TRAILING_STRIP)

        # Windows 予約デバイス名 ( 拡張子付きでもベース名が一致すれば不可 )
        base_name = comp.split(".", 1)[0].upper()
        if base_name in WINDOWS_RESERVED_DEVICES:
            comp = "_" + (comp or "_")

        out_parts.append(comp or "_")

    # すべて空だった場合 ( 例: "/" のみ ) も '_' を返す
    if not out_parts:
        out_parts.append("_")

    return "/".join(out_parts)

# --------------------------------------------------------------------
# 絶対パス判定/リモートホームディレクトリ検出
# --------------------------------------------------------------------
def is_windows_abs(p: str) -> bool:
    """Windows 形式の絶対パスかどうかを判定する。

    Args:
        p (str): 判定対象のパス文字列。

    Returns:
        bool: Windows のドライブレターで始まる場合は ``True``。

    Examples:
        >>> is_windows_abs('C:/Windows')
        True
        >>> is_windows_abs('/etc/passwd')
        False
    """
    return bool(WINDOWS_ABS_RE.match(p))

def is_abs_path(p: str) -> bool:
    """プラットフォーム非依存で絶対パスかどうかを判定する。

    Args:
        p (str): 判定対象のパス文字列。

    Returns:
        bool: UNIX 形式または Windows 形式の絶対パスなら ``True``。

    Examples:
        >>> is_abs_path('/var/log')
        True
        >>> is_abs_path('relative/path')
        False
        >>> is_abs_path('D:/data')
        True
    """
    return p.startswith("/") or is_windows_abs(p)

def is_local_abs(p: str) -> bool:
    """ローカルパスを絶対パスとして扱うかを判定する。

    Args:
        p (str): 判定対象のパス文字列。

    Returns:
        bool: 絶対パスと解釈できる場合は ``True``。

    Examples:
        >>> is_local_abs('/tmp')
        True
        >>> is_local_abs('downloads/file.txt')
        False
    """
    return is_abs_path(p)


def tilde_username(s: str) -> Optional[str]:
    """チルダ表記に含まれるユーザー名を抽出する。

    Args:
        s (str): 判定対象の文字列。

    Returns:
        Optional[str]: ``"~user"`` 形式であれば ``"user"``, 該当しない場合は ``None``。

    Examples:
        >>> tilde_username('~alice/project')
        'alice'
        >>> tilde_username('~/project')
        >>> tilde_username('~')
        >>> tilde_username('plain')
    """
    m = TILDE_USER_RE.match(s)
    return m.group(1) if m else None

# --------------------------------------------------------------------
# ローカル保存パスユーティリティ
# --------------------------------------------------------------------
def ensure_local_dir(path: str) -> None:
    """指定ディレクトリを親ごと作成する。

    Args:
        path (str): 作成したいディレクトリの絶対または相対パス。

    Raises:
        OSError: 生成に失敗した場合。

    Examples:
        >>> import os
        >>> import tempfile
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     target = os.path.join(tmp, 'nested/dir')
        ...     ensure_local_dir(target)
        ...     os.path.isdir(target)
        True
    """
    Path(path).mkdir(parents=True, exist_ok=True)

def local_path_for_download(dest_dir: str, host: str, remote_abs_path: str) -> str:
    """リモート絶対パスをローカル保存パスへマッピングする。

    ここで言う ``DEST`` は gm-gather CLI の位置引数 ``DEST`` で指定されるローカル保存
    ルートを指す。

    保存先は ``DEST/<HOST>/<relative>`` の形式に正規化される。
    例えば ``dest_dir="/tmp/out"``,  ``host="node1"``,  ``remote_abs_path="/etc/hosts"``
    の場合は ``"/tmp/out/node1/etc/hosts"`` を返す。

    Args:
        dest_dir (str): ダウンロード先のベースディレクトリ。
        host (str): ログ表示などに使うホスト名。
        remote_abs_path (str): リモート環境上での絶対パス。

    Returns:
        str: 正規化済みのローカル保存パス。

    Examples:
        >>> local_path_for_download('/tmp/out', 'node1', '/etc/hosts')
        '/tmp/out/node1/etc/hosts'
        >>> local_path_for_download('/tmp/out', 'node1', 'C:/Windows/System32')
        '/tmp/out/node1/C_/Windows/System32'
    """
    # Windows を含むクロスプラットフォームで安全な相対化
    rel_safe = _sanitize_remote_abs_for_local(remote_abs_path)
    return os.path.join(dest_dir, host, rel_safe)

# --------------------------------------------------------------------
# SRC 正規化 / ルート分割
# --------------------------------------------------------------------
def normalize_src_abs(src: str, *, home_abs_for_tilde: str) -> str:
    """SRC トークンを絶対パスパターンへ正規化する。

    変換時は次のルールを適用する。
    - ``"~/"`` から始まる場合は ``ホームディレクトリの絶対パス`` に接続する。
    - ``"/"`` で始まる UNIX 絶対パスはそのまま。
    - Windows ドライブレター始まりは ``"C:/"`` 形式に統一する。
    - 相対表現は ``ホームディレクトリの絶対パス`` 配下へ連結し,  ``".."`` による逸脱を禁止する。
    - バックスラッシュや ``"./"``,  ``"//"`` は正規化するが, 正規表現メタ部は保持する。

    安全化:
        - ``".."`` セグメントは事前に検出し, ホームディレクトリの絶対パスからの逸脱を防ぐ。
        - Windows ドライブレターのバリエーションは ``"C:/"`` 形式へ統一し, パスの揺らぎを抑える。

    備考:
        - 正規表現メタ文字以降の tail 部は無改変で返すため, 呼び出し元でのパターン解釈がそのまま維持される。
        - ``ホームディレクトリの絶対パス`` は CLI 側で安全に決定済みであり, 本関数は追加のファイル存在確認を行わない。

    例えば ``src="~/logs/*.log"``,  ``ホームディレクトリの絶対パス="/home/collect"`` の場合は
    ``"/home/collect/logs/*.log"`` に展開される。また Windows 形式の
    ``"C\\Temp\\*.txt"`` は ``"C:/Temp/*.txt"`` へと正規化される。

    Args:
        src (str): ユーザー入力の SRC トークン。
        home_abs_for_tilde (str): ``"~/"`` 指定時に展開するホームディレクトリの絶対パス。

    Returns:
        str: 正規化済みの絶対パスパターン。

    Raises:
        ValueError: ``".."`` によりホームを逸脱する恐れがある場合。

    Examples:
        >>> normalize_src_abs('~/data', home_abs_for_tilde='/home/user')
        '/home/user/data'
        >>> normalize_src_abs('logs/.*', home_abs_for_tilde='/home/user')
        '/home/user/logs/.*'
        >>> normalize_src_abs('C\\\\Temp', home_abs_for_tilde='/home/user')
        'C:/Temp'
    """
    s = src
    # 1) tilde-self ( tail を壊さない : そのまま連結 )
    if s.startswith('~/'):
        return home_abs_for_tilde.rstrip('/') + '/' + s[2:]
    # 2) Windows drive abs
    if re.match(r'^[A-Za-z]:[\\/]', s):
        drive = s[:2]                # 'C:'
        rest = s[2:].replace('\\', '/')
        return drive + rest          # 'C:/...'
    # 3) UNIX abs ( そのまま返す : tail の regex を保存 )
    if s.startswith('/'):
        return s

    # 4) relative : anchor to home_abs_for_tilde
    #    正規表現 tail を壊さないため, 最初のメタ以降は **無改変**で保持し,
    #    メタ以前 ( パス部 ) のみを正規化する。
    m = CORE_PATH_HANDLING_REGEX_META_COMPILED.search(s)
    cut = m.start() if m else len(s)
    head_rel_raw = s[:cut]          # パス部 ( 相対 )
    tail_rel_raw = s[cut:]          # 正規表現部 ( 無改変 )

    # パス部の正規化 : './' 折り畳み, '//' 1 本化, '\'
    head_rel = head_rel_raw.replace('\\', '/')
    head_rel = RE_REL_LEADING_DOT.sub("", head_rel)
    head_rel = RE_MULTI_SLASH.sub("/", head_rel).lstrip("/")

    # '..' による HOME 脱出を禁止
    parts = [p for p in head_rel.split('/') if p not in ("", ".")]
    for pseg in parts:
        if pseg == "..":
            raise ValueError(f"Relative SRC escapes HOME: {src!r}")
    head_clean = "/".join(parts)

    # 連結 ( head が空なら HOME 直下に tail をぶら下げる )
    base = (home_abs_for_tilde.rstrip('/') or '/')
    if head_clean:
        return base + '/' + head_clean + tail_rel_raw
    else:
        return base + tail_rel_raw

def split_src_to_root_and_tail_regex(abs_path: str) -> tuple[str, str]:
    """絶対パスパターンを列挙用の root と tail に分割する。

    メタ文字を含まない場合は ``root, '^basename$'`` を返し, メタ文字を含む場合は
    最初のメタ文字直前でディレクトリを分離した上で, 残りを tail として返却する。

    ルール:
        - Windows ドライブ始まり ``"C:/"`` は ``root="C:/"`` に固定し, 残りを tail とする。
        - メタ文字を含まないリテラルは末尾の ``"/"`` で分割し, basename に ``^...$`` を付与する。
        - メタ文字を含む場合は最初のメタの直前で ``root`` と ``tail`` を分離し, tail は無改変で返す。

    注意:
        - 入力は ``normalize_src_abs()`` 済みであることを前提としており, 本関数は追加の正規化を行わない。
        - ``tail`` が空の場合は ``r".*"`` を返して「ディレクトリ直下すべて」を表現する。

    Args:
        abs_path (str): ``normalize_src_abs`` 済みの絶対パスパターン。

    Returns:
        tuple[str, str]: 探索ルートと tail 正規表現のペア。

    Raises:
        ValueError: パターンが空や ``"/"`` のみである場合。

    Examples:
        >>> split_src_to_root_and_tail_regex('/etc/hosts')
        ('/etc', '^hosts$')
        >>> split_src_to_root_and_tail_regex('/var/log/.*')
        ('/var/log', '.*')
        >>> split_src_to_root_and_tail_regex('C:/Windows/.+\\.log')
        ('C:/Windows', '.+\\.log')
    """
    if not abs_path or abs_path == "/":
        raise ValueError("invalid absolute path pattern")

    # --- Special-case: Windows drive absolute path 'C:/...' --------------
    # OS 非依存で一貫して 'C:/' をルート扱いする。os.path 依存の dirname/basename を避ける。
    if re.match(r'^[A-Za-z]:/', abs_path):
        drive_root = abs_path[:3]  # 'C:/'
        tail_full = abs_path[3:]
        if not tail_full:
            # 'C:/' 意図: 直下すべて
            return (drive_root, r".*")
        m_drive = CORE_PATH_HANDLING_REGEX_META_COMPILED.search(tail_full)
        if m_drive is None:
            # リテラル一致
            return (drive_root, "^" + re.escape(tail_full) + "$")
        # 正規表現を tail にそのまま保持
        return (drive_root, tail_full)


    m = CORE_PATH_HANDLING_REGEX_META_COMPILED.search(abs_path)
    if m is None:
        # pure literal

        # OS 非依存化のため, 最後の '/' で分割 ( posix ルールに固定 )
        slash_pos = abs_path.rfind("/")
        if slash_pos < 0:
            root = "/"
            base = abs_path
        else:
            root = abs_path[:slash_pos] or "/"
            base = abs_path[slash_pos + 1 :]

        if not base:
            # '/etc/' や 'C:/' のようなディレクトリ意図は root を保持し, 配下すべてを意味する
            root_keep = abs_path if re.match(r'^[A-Za-z]:/$', abs_path) else (abs_path.rstrip("/") or "/")
            return (root_keep, r".*")
        # 相対名に対する厳密一致
        return (root, "^" + re.escape(base) + "$")

    # regex case: メタの直前にある最後の '/' を探す
    slash_pos = abs_path.rfind("/", 0, m.start())
    if slash_pos < 0:
        # 先頭にメタ, あるいは '/' より前にメタが無い  =>  root は '/' に倒す
        root = "/"
        tail = abs_path.lstrip("/")
    else:
        root = abs_path[:slash_pos] or "/"
        tail = abs_path[slash_pos + 1 :]
    if not tail:
        # root 直下全体
        return (root, r".*")
    return (root, tail)

# ============================================================
# Dataclasses for scatter use
# ============================================================
@dataclass(frozen=True)
class ScatterSrcToken:
    """scatter で解決前の SRC トークンを保持するデータクラス。"""

    raw: str  # ユーザー入力そのままの SRC トークン文字列

@dataclass(frozen=True)
class ScatterResolvedToken:
    """scatter で解決済み SRC トークンを保持するデータクラス。"""

    abs_root: str     # 列挙に使う絶対パス ( ローカル )
    rel_root: str     # DEST 配下のレイアウト根 ( 正規化済 )
    is_absolute: bool  # ユーザー指定が絶対パスだったかどうかの判定結果

# ============================================================
# Primitive checks / expand
# ============================================================
def is_unix_abs(p: str) -> bool:
    """UNIX 形式の絶対パスかどうかを判定する。

    Args:
        p (str): 判定対象のパス文字列。

    Returns:
        bool: ``"/"`` 始まりなら ``True``。

    Examples:
        >>> is_unix_abs('/var/log')
        True
        >>> is_unix_abs('var/log')
        False
    """
    return p.startswith(SEP_POSIX)

def is_unc(p: str) -> bool:
    """UNC (Windows ネットワークパス) 形式かを判定する。

    Args:
        p (str): 判定対象のパス文字列。

    Returns:
        bool: ``"\\\\"`` で始まる場合は ``True``。

    Examples:
        >>> is_unc('\\\\server\\share')
        True
        >>> is_unc('C:/Windows')
        False
    """
    return p.startswith(UNC_PREFIX)

def is_tilde_user_form(p: str) -> bool:
    """``~user`` 形式かどうかを判定する。

    Args:
        p (str): 判定対象の文字列。

    Returns:
        bool: ``"~name"`` 形式であれば ``True``。

    Examples:
        >>> is_tilde_user_form('~alice/docs')
        True
        >>> is_tilde_user_form('~/docs')
        False
    """
    return bool(TILDE_USER_RE.match(p))

def is_tilde_self_form(p: str) -> bool:
    """``~/`` 形式かどうかを判定する。

    Args:
        p (str): 判定対象の文字列。

    Returns:
        bool: ``"~/"`` または ``"~"`` 単独なら ``True``。

    Examples:
        >>> is_tilde_self_form('~/logs')
        True
        >>> is_tilde_self_form('~alice/logs')
        False
    """
    return bool(RE_TILDE_SELF.match(p))

def is_bare_tilde(value: str) -> bool:
    """素の ``"~"`` 指定かどうかを判定する。

    Args:
        value (str): 判定対象の文字列。

    Returns:
        bool: 空白を除いた結果が ``"~"`` のみなら ``True``。

    Examples:
        >>> is_bare_tilde('~')
        True
        >>> is_bare_tilde(' ~/ ')
        True
        >>> is_bare_tilde('~/data')
        False
    """
    return value.strip() == "~"

def scatter_expand_tilde_for_exec_user(raw: str) -> str:
    """scatter 用に ``~/`` を実行ユーザーの HOME に展開する。

    注意:
        - ``"~user"`` 形式は scatter の仕様上サポートしないため, 本関数は ``ValueError`` を送出して早期に拒否する。
        - 本関数では, 素の ``"~"`` も ``os.path.expanduser('~')`` により展開され, 結果的に ``"~/"`` 指定と同じホームディレクトリへ解決される。
        - 素の ``"~"`` の扱いをホームディレクトリに変換しない場合の処理は本関数外で実施すること。

    Args:
        raw (str): 展開前のパス文字列。

    Returns:
        str: 展開後のパス文字列。

    Raises:
        ValueError: ``"~user"`` 形式が渡された場合。

    Examples:
        >>> import os
        >>> expanded = scatter_expand_tilde_for_exec_user('~/project')
        >>> expanded.startswith(os.path.expanduser('~'))
        True
        >>> scatter_expand_tilde_for_exec_user('/abs/path')
        '/abs/path'
        >>> scatter_expand_tilde_for_exec_user('~alice/docs')
        Traceback (most recent call last):
        ...
        ValueError: ~user/ form is not accepted in scatter: '~alice/docs'
    """
    s: str = raw
    if is_tilde_user_form(s):
        raise ValueError(f"~user/ form is not accepted in scatter: {s!r}")
    if is_tilde_self_form(s):
        expanded: str = os.path.expanduser(s)
        return expanded
    return s

# ============================================================
# DEST レイアウト用の正規化 ( SRC 実解決には適用しない )
# ============================================================
def normalize_rel_for_dest(rel: str) -> str:
    """DEST 配下で利用する相対パスを正規化する。

    ここで言う ``DEST`` は scatter CLI の位置引数 ``DEST`` で指定されるリモート配置ルートを指す。

    - ``"\\"`` を ``"/"`` に統一する。
    - 先頭 ``"./"`` を折り畳み,  ``"//"`` を 1 本化する。
    - 先頭 ``"/"`` を除去し, 絶対パスを相対パスへ変換する。
    - ``".."`` による CWD 逸脱を検出すると ``ValueError`` を送出する。

    Args:
        rel (str): 正規化前の相対パス。

    Returns:
        str: 正規化済みの相対パス。

    Raises:
        ValueError: 基準ディレクトリより上へ出ようとした場合。

    Examples:
        >>> normalize_rel_for_dest('.\\foo/../bar//baz')
        'bar/baz'
        >>> normalize_rel_for_dest('safe/path')
        'safe/path'
        >>> normalize_rel_for_dest('../../escape')
        Traceback (most recent call last):
        ...
        ValueError: dangerous relative path: ../../escape
    """
    r: str = rel.replace(SEP_WIN, SEP_POSIX)
    r = RE_REL_LEADING_DOT.sub("", r)
    r = RE_MULTI_SLASH.sub("/", r)
    r = r.lstrip(SEP_POSIX)

    # スタックで '..' を評価 ( ベースより上に出るとエラー )
    parts = [p for p in r.split(SEP_POSIX) if p not in ("", ".")]
    stack: list[str] = []
    for p in parts:
        if p == "..":
            if not stack:
                raise ValueError(f"dangerous relative path: {rel}")
            stack.pop()
        else:
            stack.append(p)
    return SEP_POSIX.join(stack)

def dest_rel_from_abs(abs_path: str) -> str:
    """絶対パスを DEST 用の相対パスに変換する。

    ここで言う ``DEST`` は scatter CLI の位置引数 ``DEST`` で指定されるリモート配置ルートを指す。

    例:
        - UNIX 形式: ``'/var/log/syslog'`` -> ``'var/log/syslog'``
        - Windows ドライブ形式: ``'C:/Windows/System32'`` -> ``'C/Windows/System32'``
        - UNC 形式: ``'\\srv\\share\\dir'`` -> ``'srv/share/dir'``

    Args:
        abs_path (str): 変換元の絶対パス。

    Returns:
        str: DEST 配下で利用する相対パス。

    Examples:
        >>> dest_rel_from_abs('/var/log/syslog')
        'var/log/syslog'
        >>> dest_rel_from_abs('C:/Windows/System32')
        'C/Windows/System32'
        >>> dest_rel_from_abs('\\\\srv\\share\\dir')
        'srv/share/dir'
    """
    p_raw: str = abs_path
    # Windows drive
    if is_windows_abs(p_raw):
        # drive + tail を POSIX 風にする
        drive: str = p_raw[0].upper()
        tail_raw: str = p_raw[2:]  # 'C:' の次から
        rel: str = f"{drive}/{tail_raw}".replace(SEP_WIN, SEP_POSIX)
        rel = RE_MULTI_SLASH.sub("/", rel)
        rel = rel.lstrip("/")
        return rel
    # UNC
    if is_unc(p_raw):
        rel2: str = p_raw.lstrip(SEP_WIN).replace(SEP_WIN, SEP_POSIX)
        rel2 = RE_MULTI_SLASH.sub("/", rel2)
        rel2 = rel2.lstrip("/")
        return rel2
    # UNIX absolute
    p_posix: str = p_raw.replace(SEP_WIN, SEP_POSIX)
    p_posix = RE_MULTI_SLASH.sub("/", p_posix)
    if p_posix.startswith(SEP_POSIX):
        return p_posix.lstrip(SEP_POSIX)
    return p_posix

# ============================================================
# Relative safety (.. による CWD 超え禁止)
# ============================================================
def validate_relative_token_safe(raw_rel: str, cwd: Optional[str] = None) -> None:
    """相対パスが基準ディレクトリより上へ出ないことを検証する。

    Args:
        raw_rel (str): 検証対象の相対パス。
        cwd (Optional[str]): 基準とするカレントディレクトリ。 ``None`` の場合は ``os.getcwd()``。

    Raises:
        ValueError: 解決結果が基準ディレクトリから外れる場合。

    Examples:
        >>> validate_relative_token_safe('logs/app.log', cwd='/tmp')
        >>> validate_relative_token_safe('../escape', cwd='/tmp')
        Traceback (most recent call last):
        ...
        ValueError: Relative SRC escapes above CWD: '../escape'
    """
    base: str = cwd or os.getcwd()
    base_path: pathlib.Path = pathlib.Path(base)
    target_path: pathlib.Path = (base_path / raw_rel).resolve()
    try:
        _ = target_path.relative_to(base_path)
    except Exception as _e:
        raise ValueError(f"Relative SRC escapes above CWD: {raw_rel!r}")

# ============================================================
# トークン解決 ( scatter 用 )
# ============================================================
def resolve_token_for_scatter(token: ScatterSrcToken, cwd: Optional[str] = None) -> ScatterResolvedToken:
    """scatter 用の SRC トークンを解決して絶対・相対パスを得る。

    Args:
        token (ScatterSrcToken): 解決対象のトークン。
        cwd (Optional[str]): 相対パス解決に使う基準ディレクトリ。 ``None`` なら ``os.getcwd()``。

    Returns:
        ScatterResolvedToken: 絶対パス, DEST 相対パス, 絶対指定かどうかの情報を持つレコード。

    Raises:
        ValueError: ``~user`` 形式や Windows 絶対パスが非対応 OS 上で指定された場合など。

    Examples:
        >>> token = ScatterSrcToken('logs/app.log')
        >>> resolved = resolve_token_for_scatter(token, cwd='/tmp')  # doctest: +SKIP
        >>> resolved.abs_root.startswith('/tmp/logs')  # doctest: +SKIP
        True
        >>> resolved.rel_root  # doctest: +SKIP
        'logs/app.log'
    """
    raw: str = token.raw
    raw2: str = scatter_expand_tilde_for_exec_user(raw)
    # 絶対判定は展開後 ( raw2 ) に対して行う
    win_abs = is_windows_abs(raw2) or is_unc(raw2)
    is_abs: bool = (is_unix_abs(raw2) or win_abs)

    abs_root: str
    rel_root: str

    if is_abs:
        if win_abs and os.name != "nt":
            raise ValueError(f"Windows-absolute path not supported on non-Windows host: {raw2!r}")
        abs_root = os.path.abspath(raw2)
        rel_root = dest_rel_from_abs(abs_root)
        rel_root = normalize_rel_for_dest(rel_root)
    else:
        validate_relative_token_safe(raw2, cwd=cwd)
        abs_root = os.path.abspath(os.path.join(cwd or os.getcwd(), raw2))
        rel_root = normalize_rel_for_dest(raw2)

    return ScatterResolvedToken(abs_root=abs_root, rel_root=rel_root, is_absolute=is_abs)

# 正規表現として解釈される可能性が高いかの軽量判定
# --pack 時の末尾スラッシュ付与から正規表現入力を除外するためにscatter_cli.pyで使用
def looks_like_regex(text: str) -> bool:
    """正規表現メタ文字を含むかの軽量判定を行う。

    ``CORE_PATH_HANDLING_REGEX_META_CHARS`` に含まれるいずれかの文字が ``text`` に
    現れた時点で「正規表現と推測できる」と見なし ``True`` を返し, 1 文字も含まれない
    場合は ``False`` を返す。

    Args:
        text (str): 判定対象の文字列。

    Returns:
        bool: メタ文字を含むと推測できる場合は ``True``。

    Examples:
        >>> looks_like_regex('logs/.*')
        True
        >>> looks_like_regex('plain-text')
        False
    """
    c: str = ""
    found: bool = False
    for c in CORE_PATH_HANDLING_REGEX_META_CHARS:
        has_char: bool = (c in text)
        if has_char:
            found = True
            break
    return found
