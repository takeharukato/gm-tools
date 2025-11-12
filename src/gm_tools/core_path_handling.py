#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import os
from pathlib import Path
import pathlib
from typing import Optional
from dataclasses import dataclass

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
    """
    リモート絶対パスを「ローカル保存用の相対パス」に変換する。
    - バックスラッシュ => スラッシュ正規化
    - 先頭が 1 本の '/' の場合は相対化 ( 先頭 '_' は付けない )
    - 先頭が '//' ( 2 本以上 ) の場合は, 先頭に '_' を 1 つ立てて空セグメントを保持
       ( WindowsのUNC ( Universal Naming Convention ) パス由来の可能性を考慮 )
    - Windows ドライブレター 'X:/' は最初の ':/ ' を '_/' に置換 ( 例: 'C:/foo'  =>  'C_/foo' )
    - 中間の空セグメント ( '//' ) は '_' に置換
    - 末尾の '/' は無視 ( 末尾 '_' は付けない )
    - 禁止記号 ( <>:"\\|?* ) は '_' に置換
    - 各コンポーネント末尾のスペース・ドットは削除
    - Windows 予約デバイス名 ( CON, PRN, AUX, NUL, COM1..9, LPT1..9 ) は安全化
    - すべて空になった場合は '_' を返す
    例:
      '/etc/hosts'         -> 'etc/hosts'
      '//a//b/'            -> '_/a/_/b'
      'C:/Windows/System/' -> 'C_/Windows/System'
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
    リモートSRCトークンを「絶対パスパターン」に正規化する。
      - '~/...'        : <home_abs_for_tilde>/... に展開
      - '/...'         : そのまま ( UNIX絶対 )
      - 'X:\\...'等    : 'X:/...' に正規化 ( Windows絶対 )
      - 上記以外 ( 相対 ) : <home_abs_for_tilde>/<rel> に連結し, HOME逸脱 ( '..' ) を禁止
    安全化:
      - 区切りは '\\' => '/' に統一
      - 先頭 './' を折り畳み, '//' を1本化
      - 相対トークン中の path セグメントに '..' が含まれる場合は ValueError
         ( HOME逸脱の恐れがあるため )
    備考:
      - 正規表現メタは tail 側で評価されるため, root 部は HOME配下に固定される。
    """
    s = src
    # 1) tilde-self（tail を壊さない：そのまま連結）
    if s.startswith('~/'):
        return home_abs_for_tilde.rstrip('/') + '/' + s[2:]
    # 2) Windows drive abs
    if re.match(r'^[A-Za-z]:[\\/]', s):
        drive = s[:2]                # 'C:'
        rest = s[2:].replace('\\', '/')
        return drive + rest          # 'C:/...'
    # 3) UNIX abs（そのまま返す：tail の regex を保存）
    if s.startswith('/'):
        return s

    # 4) relative : anchor to home_abs_for_tilde
    #    正規表現 tail を壊さないため、最初のメタ以降は **無改変**で保持し、
    #    メタ以前（パス部）のみを正規化する。
    m = CORE_PATH_HANDLING_REGEX_META_COMPILED.search(s)
    cut = m.start() if m else len(s)
    head_rel_raw = s[:cut]          # パス部（相対）
    tail_rel_raw = s[cut:]          # 正規表現部（無改変）

    # パス部の正規化：'./' 折り畳み、'//' 1 本化、'\'
    head_rel = head_rel_raw.replace('\\', '/')
    head_rel = RE_REL_LEADING_DOT.sub("", head_rel)
    head_rel = RE_MULTI_SLASH.sub("/", head_rel).lstrip("/")

    # '..' による HOME 脱出を禁止
    parts = [p for p in head_rel.split('/') if p not in ("", ".")]
    for pseg in parts:
        if pseg == "..":
            raise ValueError(f"Relative SRC escapes HOME: {src!r}")
    head_clean = "/".join(parts)

    # 連結（head が空なら HOME 直下に tail をぶら下げる）
    base = (home_abs_for_tilde.rstrip('/') or '/')
    if head_clean:
        return base + '/' + head_clean + tail_rel_raw
    else:
        return base + tail_rel_raw

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
    raw: str

@dataclass(frozen=True)
class ScatterResolvedToken:
    abs_root: str     # 列挙に使う絶対パス ( ローカル )
    rel_root: str     # DEST 配下のレイアウト根 ( 正規化済 )
    is_absolute: bool

# ============================================================
# Primitive checks / expand
# ============================================================
def is_unix_abs(p: str) -> bool:
    return p.startswith(SEP_POSIX)

def is_unc(p: str) -> bool:
    return p.startswith(UNC_PREFIX)

def is_tilde_user_form(p: str) -> bool:
    # ~user または ~user/ を検出
    return bool(TILDE_USER_RE.match(p))

def is_tilde_self_form(p: str) -> bool:
    return bool(RE_TILDE_SELF.match(p))

def scatter_expand_tilde_for_exec_user(raw: str) -> str:
    """
    scatter における ~/ 展開は『コマンド実行ユーザの HOME』で行う。
     ~user/ は仕様上受理しない。~ ( 単独 ) も HOME に展開する
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
    """
    DEST 配下で用いる相対パス表記の正規化。
      - '\\' を '/' に統一
      - 先頭 './' を折り畳み, '//' を 1 本化
      - 先頭の '/' を除去 ( 絶対化の禁止 )
      - スタック方式で '..' を評価し, CWD 外への脱出のみ拒否
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
    """
    絶対パスから DEST 用 relpath を生成。
      - UNIX / : 先頭 '/' を除去
      - Windows ドライブ: 'C:\\x\\y' -> 'C/x/y'
      - UNC '\\\\srv\\share\\p' -> 'srv/share/p'
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
    """
    与えられた文字列が正規表現として解釈される可能性が高いかの軽量判定。
    シェルのグロブではなく, scatter がサポートする「regex 指定」を壊さない目的で使用する。
    - いずれかのメタ文字を含む場合は True とみなす ( 保守的な判定 ) 。
    """
    c: str = ""
    found: bool = False
    for c in CORE_PATH_HANDLING_REGEX_META_CHARS:
        has_char: bool = (c in text)
        if has_char:
            found = True
            break
    return found
