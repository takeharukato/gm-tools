#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
from pathlib import Path

# エスケープされていないこれらの文字が現れた時点で「ここから正規表現」
_REGEX_META = set(r'.^$*+?{}[]\|()')  # 逆スラッシュ自体は「エスケープ」を示すので除外

# --------------------------------------------------------------------
# ローカル保存パスユーティリティ
# --------------------------------------------------------------------
def ensure_local_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)

def local_path_for_download(dest_dir: str, host: str, remote_abs_path: str) -> str:
    """
    保存先は DEST/<HOST>/abs/<remote_abs_path> の形に正規化する
    例) remote_abs_path='/etc/hosts' -> DEST/<HOST>/abs/etc/hosts
    """
    rel = remote_abs_path.lstrip('/').replace('\\', '/')
    return os.path.join(dest_dir, host, "abs", rel)

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

def split_src_to_root_and_tail_regex(abs_src: str) -> tuple[str, str]:
    """
    絶対 SRC を (root, tail_regex) に分割する。
    - root は実在ディレクトリ候補（POSIX なら '/' で始まる部分、Windows なら 'C:/'）
    - tail_regex は root 直下からの相対正規表現
    - 正規表現メタ文字が含まれない場合は、basename を re.escape したものを返す
    - ディレクトリ指定（末尾 '/'）は tail_regex='' とし、「配下すべて」の意味にする
    """
    if re.match(r'^[A-Za-z]:/', abs_src):
        prefix, tail = abs_src[:3], abs_src[3:]     # 'C:/', 'path/...'
        return _split_from_prefix(prefix, tail)
    if abs_src.startswith('/'):
        return _split_from_prefix('/', abs_src[1:])
    raise ValueError("SRC must be an absolute path starting with '/', 'X:/', or '~/'. (got: %r)" % abs_src)

def _normalize_root(root: str) -> str:
    # '/' や 'C:/' の末尾スラッシュは保持し、それ以外は末尾スラッシュを落とす
    if root == '/' or re.match(r'^[A-Za-z]:/$', root):
        return root
    return root.rstrip('/')

def _find_first_regex_start(tail: str) -> tuple[int, int | None]:
    """
    tail 内を走査し、エスケープされていない正規表現メタ文字の最初の位置を返す。
    返り値: (last_slash_index, meta_index or None)
    """
    last_slash = -1
    i = 0
    while i < len(tail):
        ch = tail[i]
        if ch == '/':
            last_slash = i
            i += 1
            continue
        if ch == '\\':  # エスケープはスキップ
            i += 2
            continue
        if ch in _REGEX_META:
            return last_slash, i
        i += 1
    return last_slash, None

def _split_from_prefix(prefix: str, tail: str) -> tuple[str, str]:
    # 末尾が空 or '.' は「そのディレクトリ配下すべて」
    if tail == '' or tail == '.':
        return _normalize_root(prefix), ''

    last_slash, meta_i = _find_first_regex_start(tail)
    if meta_i is not None:
        # 正規表現が現れる直前のセグメントの直前までを root にする
        root = prefix + (tail[:last_slash] if last_slash >= 0 else '')
        if root == '':
            root = prefix
        tail_re = tail[(last_slash + 1) if last_slash >= 0 else 0:]
        return _normalize_root(root), tail_re

    # 正規表現メタが無い → リテラル扱い
    # ディレクトリ指定（末尾 '/'）なら「配下すべて」
    if tail.endswith('/'):
        return _normalize_root(prefix + tail[:-1]), ''

    # ファイル指定：dirname/basename に分け、basename を escape
    slash = tail.rfind('/')
    if slash == -1:
        root = _normalize_root(prefix)
        base = tail
    else:
        root = _normalize_root(prefix + tail[:slash])
        base = tail[slash + 1:]
    return root, re.escape(base)
