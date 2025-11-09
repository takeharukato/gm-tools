#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

# === Constants (shared) ===
TILDE_USER_RE: re.Pattern[str] = re.compile(r"^~([^/\\]+)(?:$|[\\/])")
HOME_DETECT_CMD_FMT: str = "getent passwd {user} | cut -d: -f6"
HOME_FALLBACK_ROOT: str = "/root"
HOME_FALLBACK_PREFIX: str = "/home"

# === Constants (local) ===
# エスケープされていないこれらの文字が現れた時点で「ここから正規表現」
# Windows のファイル名に使えない文字（予約文字）
#   https://learn.microsoft.com/windows/win32/fileio/naming-a-file
WINDOWS_FORBIDDEN_CHARS = '<>:"\\|?*'
WINDOWS_TRAILING_STRIP = ' .'  # 末尾のスペース・ドットは不可
WINDOWS_ABS_RE: re.Pattern[str] = re.compile(r"^[A-Za-z]:[\\/]")
WINDOWS_FORBIDDEN_RE = re.compile(r'[<>:"\\|?*]')
# Windows 予約デバイス名（拡張子が付いていても不可）の回避用
WINDOWS_RESERVED_DEVICES = {
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}

CORE_PATH_HANDLING_REGEX_META_CHARS = r'.^$*+?{}[]\|()'
CORE_PATH_HANDLING_REGEX_META_SET = set(CORE_PATH_HANDLING_REGEX_META_CHARS)  # 逆スラッシュ自体は「エスケープ」を示すので除外
CORE_PATH_HANDLING_REGEX_META_COMPILED = re.compile(rf"[{re.escape(CORE_PATH_HANDLING_REGEX_META_CHARS)}]")

def _sanitize_remote_abs_for_local(remote_abs_path: str) -> str:
    """
    リモート絶対パスを「ローカル保存用の相対パス」に変換する。
    - バックスラッシュ→スラッシュ正規化
    - 先頭が 1 本の '/' の場合は相対化（先頭 '_' は付けない）
    - 先頭が '//'（2 本以上）の場合は、先頭に '_' を 1 つ立てて空セグメントを保持
       ( WindowsのUNC（Universal Naming Convention）パス由来の可能性を考慮 )
    - Windows ドライブレター 'X:/' は最初の ':/ ' を '_/' に置換（例: 'C:/foo' → 'C_/foo'）
    - 中間の空セグメント（'//'）は '_' に置換
    - 末尾の '/' は無視（末尾 '_' は付けない）
    - 禁止記号（<>:"\\|?*）は '_' に置換
    - 各コンポーネント末尾のスペース・ドットは削除
    - Windows 予約デバイス名（CON, PRN, AUX, NUL, COM1..9, LPT1..9）は安全化
    - すべて空になった場合は '_' を返す
    例:
      '/etc/hosts'         -> 'etc/hosts'
      '//a//b/'            -> '_/a/_/b'
      'C:/Windows/System/' -> 'C_/Windows/System'
    """
    p = remote_abs_path.replace("\\", "/")

    # 'X:/...' → 'X_/...'（絶対ドライブパスのみ）
    if re.match(r'^[A-Za-z]:/', p):
        p = p.replace(":/", "_/", 1)

    # 先頭・末尾スラッシュの情報を保持
    leading_slashes = len(p) - len(p.lstrip("/"))
    had_trailing_slash = p.endswith("/")

    # 先頭スラッシュはすべて剥がして相対化（UNC 由来は後段で '_' を立てて表現）
    rel = p.lstrip("/")

    parts_raw = rel.split("/")

    # 末尾スラッシュに由来する空要素は「すべて」捨てる（末尾 '_' は作らない）
    # 例: "C:/path///" や "/a/b//" などでも末尾に '_' を作らない
    if had_trailing_slash:
        while parts_raw and parts_raw[-1] == "":
            parts_raw.pop()

    out_parts: list[str] = []

    # 先頭が '//' 以上なら、空セグメントの存在を先頭 '_' で保持
    if leading_slashes >= 2:
        out_parts.append("_")

    for comp in parts_raw:
        if comp == "":
            # 中間の '//' → '_'
            out_parts.append("_")
            continue

        # 禁止記号を '_' に
        comp = WINDOWS_FORBIDDEN_RE.sub("_", comp)
        # 末尾スペース/ドットを削除
        comp = comp.rstrip(WINDOWS_TRAILING_STRIP)

        # Windows 予約デバイス名（拡張子付きでもベース名が一致すれば不可）
        base_name = comp.split(".", 1)[0].upper()
        if base_name in WINDOWS_RESERVED_DEVICES:
            comp = "_" + (comp or "_")

        out_parts.append(comp or "_")

    # すべて空だった場合（例: "/" のみ）も '_' を返す
    if not out_parts:
        out_parts.append("_")

    return "/".join(out_parts)

# --------------------------------------------------------------------
# 絶対パス判定/リモートホームディレクトリ検出
# --------------------------------------------------------------------
def is_windows_abs(p: str) -> bool:
    # Windows ドライブレター始まり ( C:\ など ) を絶対パスとして判定する。
    return bool(WINDOWS_ABS_RE.match(p))

def is_abs_path(p: str) -> bool:
    """
    実行 OS に依らず, UNIX の '/' 始まり, または Windows ドライブレター始まりを絶対と見なす。
    """
    return p.startswith("/") or is_windows_abs(p)

def is_local_abs(p: str) -> bool:
    """
    ローカルパスが, 実行 OS に依らず, UNIX の '/' 始まり, または Windows ドライブレター始まりを絶対と見なす。
    """
    return is_abs_path(p)


def tilde_username(s: str) -> Optional[str]:
    """
    '~user' または '~user/...' の 'user' を返す。'~'・'~/' は None ( 対象外 ) 。
    Windows/UNIX 共通で '/' と '\\' を区切りとして扱う。
    """
    if s == "~" or s.startswith("~/"):
        return None
    m = TILDE_USER_RE.match(s)
    return m.group(1) if m else None

# --------------------------------------------------------------------
# ローカル保存パスユーティリティ
# --------------------------------------------------------------------
def ensure_local_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)

def local_path_for_download(dest_dir: str, host: str, remote_abs_path: str) -> str:
    """
    リモートの絶対パス remote_abs_path を, ローカルの保存先パスにマッピングする。

    保存先は DEST/<HOST>/<remote_abs_path_without_leading_slash> の形に正規化する。
    例:
        dest_dir = "/tmp/out"
        host     = "node1"
        remote_abs_path = "/etc/hosts"
      -> "/tmp/out/node1/etc/hosts"

    つまり gather は /etc/hosts を取得した場合,
    /tmp/out/node1/etc/hosts に保存する, というルールになる。
    """
    # Windows を含むクロスプラットフォームで安全な相対化
    rel_safe = _sanitize_remote_abs_for_local(remote_abs_path)
    return os.path.join(dest_dir, host, rel_safe)

# --------------------------------------------------------------------
# SRC 正規化 / ルート分割
# --------------------------------------------------------------------
def normalize_src_abs(src: str, *, home_abs_for_tilde: str) -> str:
    """
    - '~/...' をホームディレクトリに展開
    - 'C:\\...' を 'C:/...' に正規化
    - それ以外はそのまま返す
    """
    if src.startswith('~/'):
        return home_abs_for_tilde.rstrip('/') + '/' + src[2:]
    if re.match(r'^[A-Za-z]:[\\/]', src):
        drive = src[:2]          # 'C:'
        rest = src[2:].replace('\\', '/')
        return drive + rest      # 'C:/...'
    return src

def split_src_to_root_and_tail_regex(abs_path: str) -> tuple[str, str]:
    """
    与えられた絶対パス ( 正規表現メタを含む可能性あり ) を (root, tail_re) に分解する。
    - ルール:
      * メタ文字が無い場合: root=dirname(abs_path), tail_re='^basename$' ( 相対名への厳密一致 )
      * メタ文字がある場合: 最初のメタ文字の直前の '/' までを root とし, 以降 ( 先頭'/'除去 ) を tail_re とする
    - いずれの場合も root はディレクトリを指すことを意図
    注意: abs_pathは, 必ず normalize_src_absを通して,
          Windows の バックスラッシュ '\' を '/' 正規化していることを
          前提とする。
    """
    if not abs_path or abs_path == "/":
        raise ValueError("invalid absolute path pattern")

    m = CORE_PATH_HANDLING_REGEX_META_COMPILED.search(abs_path)
    if m is None:
        # pure literal
        root = os.path.dirname(abs_path) or "/"
        base = os.path.basename(abs_path)
        if not base:
            # '/etc/' や 'C:/' のようなディレクトリ意図は root を保持し、配下すべてを意味する
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
