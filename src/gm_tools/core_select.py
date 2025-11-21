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
"""Gather/Scatter の計画構築と候補列挙を担うモジュールです。

転送対象一件を表すデータ要素(``PlanEntry``)と, それらを安定した順序で保持するコンテナ(``Plan``)を介して,
転送対象の総数 (``total``) を事前に固定します。``total`` はログメッセージで進行状況を報告
する際の基礎情報となり, 実際の I/O 処理 ( ``core_pull`` や ``core_scatter`` など ) から参照され
ます。

このモジュール自体はファイル転送を行わず, SFTP および SSH を利用した候補列挙とローカル
ファイルシステムの走査に責務を限定しています。リモート列挙には ``core_remote_fs``
(SFTP) や ``core_ssh`` (SSH) を取り込み, 転送対象に 1 から始まるシーケンス番号を付与する
メソッド(``iter_seq``)を通じて決定的な順序を維持します。

gather/scatter CLI からは計画フェーズで利用され, 確定した転送対象の件数と順序を実行系に
渡す役割を担います。
"""

from __future__ import annotations

import fnmatch
import shlex
import os
import re
import logging

from dataclasses import dataclass
from pathlib import Path

from typing import Callable, Iterable, Iterator, List, Optional, Sequence, Tuple, Set
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    ############ 型チェッカー向けのダミー定義 ( 実行時には評価されない )  ############
    from gettext import gettext as _

from .core_remote_fs import sftp_exists, sftp_isdir, sftp_isfile, sftp_islink
from .core_ssh import DEFAULT_TIMEOUT, SSHClientLike, SFTPClientLike
from .core_cmd_flavor import run_remote_cmd_capture
from .core_path_handling import (
    split_src_to_root_and_tail_regex,
    is_abs_path,
    normalize_src_abs,
    looks_like_regex,
)

# ---- Logging setup -----------------------------------------------------------

_LOG = logging.getLogger(__name__)

# ---- Data model --------------------------------------------------------------

@dataclass
class PlanEntry:
    """Gather/Scatter の計画内で 1 件の転送対象を保持するレコードです。

    Attributes:
        path (Path): 対象ファイルやディレクトリを指すローカル絶対パス, またはリモート列挙時の代理パス。
        relpath (str): ``DEST`` 配下に配置するときの相対パスで, アーカイブ内部パスとしても利用されます。
        is_dir (bool): 対象がディレクトリであれば ``True``。
        remote_root (str): リモート側で ``relpath`` を結合する POSIX ルート。``""`` は従来互換値です。
        remote_abs (str): リモート絶対パスを再計算せず再利用するためのキャッシュ領域。
        remote_rel (str): ``remote_root`` からの相対パスを再計算せず再利用するためのキャッシュ領域。

    Examples:
        >>> from pathlib import Path
        >>> from gm_tools.core_select import PlanEntry
        >>> entry = PlanEntry(path=Path('/tmp/demo.txt'), relpath='demo.txt', is_dir=False)
        >>> (entry.path.name, entry.is_dir)
        ('demo.txt', False)
    """
    path: Path
    relpath: str
    is_dir: bool
    remote_root: str = ""
    remote_abs: str = ""
    remote_rel: str = ""

@dataclass
class Plan:
    """転送対象レコードを決定的な順序で保持し, 進行レポートを安定化させるコンテナです。

    `build_plan_from_paths()` などの生成処理で相対パスと種別 ( ディレクトリ優先 ) のキーを使って
    並べ替え済みのリストを受け取り, どの環境でも同じ順番で巡回できるようにします。これにより
    ログや試行回数のカウントが一貫し, 再試行時にも同じ順番で処理対象が提示されます。

    Attributes:
        entries (List[PlanEntry]): 計画確定済みの転送対象レコード一覧。

    Examples:
        >>> from pathlib import Path
        >>> from gm_tools.core_select import Plan, PlanEntry
        >>> plan = Plan(entries=[PlanEntry(Path('/tmp/a'), 'a', False)])
        >>> len(plan)
        1
    """
    entries: List[PlanEntry]

    def __len__(self) -> int:
        return len(self.entries)

    def iter_seq(self) -> Iterator[Tuple[int, PlanEntry]]:
        """1 から始まる連番と転送対象レコードを組にして順に返します。

        Returns:
            Iterator[Tuple[int, PlanEntry]]: 件番号と対応するレコードを順に返すイテレータ。

        Examples:
            >>> from pathlib import Path
            >>> from gm_tools.core_select import Plan, PlanEntry
            >>> plan = Plan(entries=[PlanEntry(Path('/tmp/a'), 'a', False)])
            >>> list(plan.iter_seq())
            [(1, PlanEntry(path=PosixPath('/tmp/a'), relpath='a', is_dir=False, remote_root='', remote_abs='', remote_rel=''))]
        """
        for i, e in enumerate(self.entries, start=1):
            yield i, e


# ---- Helpers ----------------------------------------------------------------

def _norm_paths(paths: Iterable[Path]) -> List[Path]:
    """可能な限りシンボリックリンク解決後の実体パスへ変換し, それが失敗した場合は単純な絶対パス化で補完します。

    Args:
        paths (Iterable[Path]): 正規化したいパスの列挙。相対・絶対・未存在を混在させて構いません。

    Returns:
        List[Path]: シンボリックリンク解決に成功した場合はその実体への絶対パス, 失敗した場合は ``absolute()`` による絶対パス。

    Examples:
        >>> from pathlib import Path
        >>> from gm_tools.core_select import _norm_paths
        >>> tmp = Path('.')
        >>> normalized = _norm_paths([tmp])
        >>> normalized[0].is_absolute()
        True
    """
    out: List[Path] = []
    for p0 in paths:
        p = Path(p0)
        try:
            out.append(p.resolve())
        except Exception:
            # resolve() が失敗した場合は absolute() で補います。
            out.append(p.absolute())
    return out


def _make_exclude(globs: Optional[Sequence[str]]) -> Optional[Callable[[str], bool]]:
    """POSIX 形式の文字列に対してグロブ除外判定を行う述語を生成します。

    gather/scatter CLI には ``--exclude`` オプションが存在しませんが, テストや
    モジュール内の一部ユーティリティではプログラム側から除外フィルタを指定
    するためにこのヘルパーを利用します。

    Args:
        globs (Optional[Sequence[str]]): 除外したいグロブパターン。``None`` や空要素は無視します。

    Returns:
        Optional[Callable[[str], bool]]: グロブが設定されていれば除外判定関数, なければ ``None``。

    Examples:
        >>> pred = _make_exclude(['*.tmp'])
        >>> bool(pred('archive.tmp')) if pred else False
        True
    """
    if not globs:
        return None
    patterns = [g for g in globs if g]
    if not patterns:
        return None

    def _pred(s: str) -> bool:
        for g in patterns:
            if fnmatch.fnmatch(s, g):
                return True
        return False

    return _pred

def _walk_including_dirs(root: Path, *, follow_symlinks: bool) -> Iterator[Tuple[Path, bool]]:
    """ルート自身を含めてディレクトリ配下を走査し, パスと種別を返します。

    Notes:
        - ルートがディレクトリでなければ, そのパス単体だけを返して終了します。
        - ``follow_symlinks`` の値を ``os.walk`` の ``followlinks`` にそのまま渡します。
        - 列挙順はファイルシステム依存なので, 呼び出し側で安定化したい場合は並べ替えを行ってください。

    Args:
        root (Path): 走査を開始する基点パス。
        follow_symlinks (bool): ``os.walk`` にシンボリックリンク追跡を許可するかどうか。

    Yields:
        Tuple[Path, bool]: 各要素は ``(パス, is_dir)``。ディレクトリ項目は ``is_dir=True``,
            通常ファイルやその他のエントリは ``is_dir=False`` で返します。

    Examples:
        >>> import tempfile
        >>> from pathlib import Path
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     base = Path(tmp)
        ...     (base / 'sub').mkdir()
        ...     (base / 'sub' / 'f.txt').write_text('x', encoding='utf-8')
        ...     results = list(_walk_including_dirs(base, follow_symlinks=False))
        >>> any(not is_dir and p.name == 'f.txt' for p, is_dir in results)
        True
        >>> any(is_dir and p == base for p, is_dir in results)
        True
    """
    # ルート自体を先に返します。
    yield (root, True) if root.is_dir() else (root, False)

    if root.is_dir():
        for dirpath, dirnames, filenames in os.walk(str(root), followlinks=bool(follow_symlinks)):
            d = Path(dirpath)
            # ディレクトリを列挙します。
            for dn in dirnames:
                yield (d / dn, True)
            # ファイルを列挙します。
            for fn in filenames:
                yield (d / fn, False)


def _relpath_for(base: Optional[Path], p: Path) -> str:
    """基準ディレクトリからの相対パスを計算します。

    Args:
        base (Optional[Path]): 相対パス算出に用いる基準。``None`` ならファイル名のみを返します。
        p (Path): 対象の絶対または相対パス。

    Returns:
        str: ``base`` を基準とした相対表現。

    Examples:
        >>> from pathlib import Path
        >>> from gm_tools.core_select import _relpath_for
        >>> base = Path('/tmp/demo')
        >>> child = base / 'data.txt'
        >>> _relpath_for(base, child)
        'data.txt'
        >>> _relpath_for(None, child)
        'data.txt'
    """
    if base is None:
        return p.name
    try:
        return str(p.resolve().relative_to(base.resolve()))
    except Exception:
        # Fallback to name when not under base
        return p.name


# ---- Public API --------------------------------------------------------------

def build_plan_from_paths(
    sources: Iterable[Path],
    *,
    base_dir: Optional[Path] = None,
    exclude: Optional[Sequence[str]] = None,
    follow_symlinks: bool = True,
) -> Plan:
    """ローカルパス群を走査して決定的な転送計画を構築します。

    Args:
        sources (Iterable[Path]): 収集対象のパス列。ファイルとディレクトリを混在させても構いません。
        base_dir (Optional[Path]): 相対パスを算出する基準ディレクトリ。未指定なら各 ``src`` ごとに基準を変えます。
        exclude (Optional[Sequence[str]]): POSIX 形式のグロブで除外したい相対パスパターン。
            gather/scatter CLI には ``--exclude`` オプションは無く, テストや内部的な
            呼び出しでフィルタを掛けたい場合にのみ用います。
        follow_symlinks (bool): ディレクトリ走査時にシンボリックリンクを辿る場合は ``True``。

    Returns:
        Plan: 検出した転送対象レコードを安定順序で保持する計画オブジェクト。

    Examples:
        >>> import tempfile
        >>> from pathlib import Path
        >>> from gm_tools.core_select import build_plan_from_paths
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     root = Path(tmp)
        ...     (root / 'a.txt').write_text('x', encoding='utf-8')
        ...     plan = build_plan_from_paths([root / 'a.txt'], base_dir=root)
        >>> len(plan)
        1
        >>> plan.entries[0].relpath
        'a.txt'
    """
    srcs = _norm_paths(sources)
    pred = _make_exclude(exclude)

    entries: List[PlanEntry] = []
    for src in sorted(srcs, key=lambda p: str(p)):
        for path, is_dir in _walk_including_dirs(src, follow_symlinks=follow_symlinks):
            rel = _relpath_for(base_dir or src, path)
            if pred is not None and pred(rel):
                continue
            entries.append(PlanEntry(path=path, relpath=rel, is_dir=bool(is_dir)))

    # 安定順序: relpath -> 種別の優先度 (ディレクトリ先行) で並べ替えます。
    entries.sort(key=lambda e: (e.relpath, 0 if e.is_dir else 1))
    return Plan(entries=entries)


def build_plan_from_manifest(
    items: Sequence[Tuple[str, bool]],
    *,
    base_dir: Optional[Path] = None,
) -> Plan:
    """リスト化されたマニフェスト ( ``(relpath, is_dir)`` タプル列 ) から転送計画を生成します。

    ``items`` には ``(relpath, is_dir)`` 形式のタプルを列挙します。``relpath`` は
    ``DEST`` 配下での配置先やアーカイブ内パスとして扱われる相対パス文字列で,
    ``is_dir`` はそのエントリがディレクトリであるかを示す真偽値です。``base_dir``
    が指定されている場合は ``relpath`` をそのディレクトリに結合して転送対象一件を表す
    データ要素(``PlanEntry``)の ``path`` フィールドを絶対化し, 未指定の場合は ``relpath`` を
    ``Path`` 化した値を
    そのまま ``path`` に設定します。

    Args:
        items (Sequence[Tuple[str, bool]]): ``(相対パス, ディレクトリか)`` の列。
        base_dir (Optional[Path]): ``Path`` を絶対化したい場合に与える基準ディレクトリ。

    Returns:
        Plan: マニフェスト順を保持した転送対象レコードのコンテナ。

    Examples:
        >>> from gm_tools.core_select import build_plan_from_manifest
        >>> plan = build_plan_from_manifest([('data/file.txt', False)])
        >>> (plan.entries[0].relpath, plan.entries[0].is_dir)
        ('data/file.txt', False)
    """
    entries: List[PlanEntry] = []
    for rel, is_dir in items:
        p = Path(rel) if base_dir is None else (Path(base_dir) / rel)
        entries.append(PlanEntry(path=p, relpath=rel, is_dir=bool(is_dir)))
    # マニフェスト順は維持しつつ, 入力の真偽値を正規化します。
    return Plan(entries=entries)


def total_of(plan: Plan) -> int:
    """転送計画に含まれるエントリ数を返します。

    Args:
        plan (Plan): 件数を調べたい転送計画。

    Returns:
        int: 安定順序で数えたエントリ件数。

    Examples:
        >>> from pathlib import Path
        >>> from gm_tools.core_select import Plan, PlanEntry, total_of
        >>> plan = Plan(entries=[PlanEntry(Path('/tmp/a'), 'a', False)])
        >>> total_of(plan)
        1
    """
    return len(plan)


def iter_with_seq(plan: Plan) -> Iterator[Tuple[int, PlanEntry]]:
    """シーケンス番号付きで転送対象一件を表すデータ要素(``PlanEntry``)を順に返すヘルパーです。

    Args:
        plan (Plan): 列挙したい転送計画。

    Returns:
        Iterator[Tuple[int, PlanEntry]]: 件番号と転送対象レコードを順番に提供するイテレータ。

    Examples:
        >>> from pathlib import Path
        >>> from gm_tools.core_select import Plan, PlanEntry, iter_with_seq
        >>> plan = Plan(entries=[PlanEntry(Path('/tmp/a'), 'a', False)])
        >>> list(iter_with_seq(plan))
        [(1, PlanEntry(path=PosixPath('/tmp/a'), relpath='a', is_dir=False, remote_root='', remote_abs='', remote_rel=''))]
    """
    return plan.iter_seq()

# -----------------------------------------------------------------------------
# enumerate_candidates_for_host
# -----------------------------------------------------------------------------
# 役割:
#   - SRC の正規表現/リテラルを, root/tail に分解し,
#   - (A) pack_remote and use_sudo: sudo でサーバ側を python/os.walk 走査
#   - (B) それ以外: SFTP で root を走査して tail_re を適用
#   - ここでは「候補列挙」のみを担い, 存在/型の最終確認は呼び出し側に委譲
#
def remote_walk_files(sftp_client: SFTPClientLike, root: str, *, include_symlinks: bool = False) -> Iterator[str]:
    """SFTP クライアントを用いてルート配下を再帰列挙します。

    Args:
        sftp_client (SFTPClientLike): ``listdir`` を提供する SFTP クライアント互換オブジェクト。
        root (str): 走査を開始するリモート絶対パス。呼び出し元で ``sftp_isdir`` を用いた
            操作対象ディレクトリの存在確認を済ませていることを前提とします。
        include_symlinks (bool): ``True`` ならシンボリックリンクも対象に含めます。

    Yields:
        str: 見つかったファイルもしくは ( オプションで ) シンボリックリンクの絶対パス。

    Examples:
        >>> from unittest.mock import patch
        >>> class Dummy:
        ...     def __init__(self):
        ...         self._map = {'/root': ['d', 'f.txt'], '/root/d': []}
        ...     def listdir(self, path):
        ...         return self._map.get(path, [])
        >>> dummy = Dummy()
        >>> def fake_isdir(_cli, path):
        ...     return path in {'/root', '/root/d'}
        >>> def fake_isfile(_cli, path):
        ...     return path == '/root/f.txt'
        >>> def fake_islink(_cli, path):
        ...     return False
        >>> with patch('gm_tools.core_select.sftp_isdir', fake_isdir), \
        ...      patch('gm_tools.core_select.sftp_isfile', fake_isfile), \
        ...      patch('gm_tools.core_select.sftp_islink', fake_islink):
        ...     list(remote_walk_files(dummy, '/root'))
        ['/root/f.txt']
    """
    stack: List[str] = [root]
    while stack:
        d = stack.pop()
        try:
            # listdir で子候補を取得。型判定は sftp_isdir/sftp_isfile に任せます。
            names = sftp_client.listdir(d)
        except Exception:
            continue
        for name in names:
            ap = d + ("" if d.endswith("/") else "/") + name
            try:
                if sftp_isdir(sftp_client, ap):
                    stack.append(ap)
                elif include_symlinks and sftp_islink(sftp_client, ap):
                    yield ap
                elif sftp_isfile(sftp_client, ap):
                    yield ap
                else:
                    # シンボリックリンクやデバイスはここでは採用しません ( 呼び出し側で判断 ) 。
                    pass
            except Exception:
                # ベストエフォート ( 権限等で失敗する可能性がある )
                continue

def _enumerate_via_sftp_walk(
    sftp_client: SFTPClientLike,
    resolved_srcs: List[str],
    home_abs: str,
    verbose: bool,
    *,
    include_symlinks: bool,
) -> List[str]:
    """SFTP 経由で候補パスを列挙します。

    Args:
        sftp_client (SFTPClientLike): Paramiko 互換の SFTP クライアント。
        resolved_srcs (List[str]): HOME 展開済みの SRC 文字列群。
        home_abs (str): ``~`` を絶対化するときに用いるホームディレクトリ。
        verbose (bool): 詳細ログを出す場合は ``True``。
        include_symlinks (bool): シンボリックリンクを候補に含めたい場合は ``True``。

    Returns:
        List[str]: 正規化済みの候補絶対パス。重複は除外済みです。

    Examples:
        >>> from unittest.mock import patch
        >>> class Dummy:
        ...     def listdir(self, path):
        ...         return []
        >>> dummy = Dummy()
        >>> def fake_normalize(src, home_abs_for_tilde):
        ...     return src
        >>> def fake_is_abs(path):
        ...     return True
        >>> def fake_looks_like_regex(_src):
        ...     return False
        >>> def fake_split(_src):
        ...     return ('/root', r'file\\.txt')
        >>> def fake_exists(_cli, path):
        ...     return path == '/root'
        >>> def fake_isdir(_cli, path):
        ...     return path == '/root'
        >>> def fake_isfile(_cli, path):
        ...     return path == '/root/file.txt'
        >>> def fake_islink(_cli, path):
        ...     return False
        >>> def fake_walk(_cli, root, include_symlinks=False):
        ...     return ['/root/file.txt']
        >>> with patch('gm_tools.core_select.normalize_src_abs', fake_normalize), \
        ...      patch('gm_tools.core_select.is_abs_path', fake_is_abs), \
        ...      patch('gm_tools.core_select.looks_like_regex', fake_looks_like_regex), \
        ...      patch('gm_tools.core_select.split_src_to_root_and_tail_regex', fake_split), \
        ...      patch('gm_tools.core_select.sftp_exists', fake_exists), \
        ...      patch('gm_tools.core_select.sftp_isdir', fake_isdir), \
        ...      patch('gm_tools.core_select.sftp_isfile', fake_isfile), \
        ...      patch('gm_tools.core_select.sftp_islink', fake_islink), \
        ...      patch('gm_tools.core_select.remote_walk_files', fake_walk):
        ...     _enumerate_via_sftp_walk(dummy, ['/root/file.txt'], '/home/demo', verbose=False, include_symlinks=False)
        ['/root/file.txt']
    """
    candidates: Set[str] = set()

    for src in resolved_srcs:
        try:
            abs_norm = normalize_src_abs(src, home_abs_for_tilde=home_abs)
        except ValueError as e:
            _LOG.warning(_("reject SRC outside HOME (relative not confined): %s (%s)") % (src, e))
            continue
        # ここまでで相対はHOME基準に絶対化済
        is_abs = is_abs_path(abs_norm)
        if not is_abs:
            _LOG.warning(_("skip non-absolute SRC after normalization (unexpected): %s") % (src) )
            continue

        # ------------------------------------------------------------------
        # Directory-SRC (リテラルディレクトリ指定) の先行判定
        # 条件:
        #   - looks_like_regex(src) == False  (正規表現扱いではない)
        #   - abs_norm がディレクトリ (remote 側で sftp_isdir)
        # 動作:
        #   abs_norm 配下の通常ファイルを列挙し candidates に追加。
        #   ディレクトリ自身は candidates に含めない。
        # ------------------------------------------------------------------
        if not looks_like_regex(src):
            try:
                if sftp_isdir(sftp_client, abs_norm) and not sftp_isfile(sftp_client, abs_norm):
                    _LOG.debug(
                        "Directory-SRC literal detected via abs path: src=%s abs_norm=%s",
                        src,
                        abs_norm,
                    )
                    for ap in remote_walk_files(sftp_client, abs_norm, include_symlinks=False):
                        if sftp_isfile(sftp_client, ap):
                            candidates.add(ap)
                    # この SRC については通常の正規表現ロジックには進まない
                    continue
            except Exception as e:
                _LOG.warning(_("directory-SRC detection failed for %s (%s)") % (src, e))


        try:
            root, tail_re = split_src_to_root_and_tail_regex(abs_norm)
        except ValueError as e:
            _LOG.warning(_("bad SRC pattern %s: %s") % (src, e))
            continue

        if not sftp_exists(sftp_client, root) or not sftp_isdir(sftp_client, root):
            _LOG.debug("skip missing/non-dir root: %s", root)
            continue

        pattern = tail_re if tail_re else r".*"
        try:
            rx = re.compile(pattern)
        except re.error as e:
            _LOG.warning(_("bad regex for %s: %s") % (src, e))
            continue

        for ap in remote_walk_files(sftp_client, root, include_symlinks=include_symlinks):
            # root からの相対
            rel = ap[len(root) :].lstrip("/\\")
            if rx.search(rel):
                candidates.add(ap)

    out = sorted(candidates)
    _LOG.debug("candidates (remote): %d", len(out))
    return out

def _enumerate_via_remote_walk_with_sudo(
    ssh: SSHClientLike,          # paramiko.SSHClient 想定 ( 型固定しない )
    resolved_srcs: List[str],
    home_abs: str,
    verbose: bool,
    *,
    include_symlinks: bool,
) -> List[str]:
    """sudo 可能なリモート歩査で SRC パターンに合致するパスを列挙する。

    sudo 経路で ``python3/os.walk`` を実行して SRC パターンを評価し, 必要に応じて
    非 sudo での再試行も行う。ディレクトリ指定の SRC と正規表現 SRC の双方へ対応
    し, シンボリックリンク列挙可否は ``include_symlinks`` で制御する。

    Args:
        ssh (SSHClientLike): コマンド実行に用いる SSH クライアント互換オブジェクト。
        resolved_srcs (List[str]): HOME 展開済みの SRC 文字列配列。
        home_abs (str): ``~`` 展開に使用するホームディレクトリの絶対パス。
        verbose (bool): 詳細ログを出力したい場合は ``True``。
        include_symlinks (bool): ``True`` のときシンボリックリンクも候補へ含める。

    Returns:
        List[str]: SRC 条件を満たしたリモートパス一覧 ( 昇順ソート済み )。

    Examples:
        .. doctest::
            >>> from unittest.mock import MagicMock  # doctest: +SKIP
            >>> ssh = MagicMock()  # doctest: +SKIP
            >>> ssh.exec_command.return_value = (MagicMock(), MagicMock(), MagicMock())  # doctest: +SKIP
            >>> _enumerate_via_remote_walk_with_sudo(  # doctest: +SKIP
            ...     ssh=ssh,
            ...     resolved_srcs=['/var/log'],
            ...     home_abs='/home/demo',
            ...     verbose=False,
            ...     include_symlinks=False,
            ... )
            ['/var/log/syslog', '/var/log/auth.log']
    """
    acc: Set[str] = set()


    # 以下の環境変数を用いて, を用いてリモート側の Python スクリプトに探索範囲とマッチ条件を渡し,
    # ``os.walk`` を介して ``GM_ROOT`` 配下の通常ファイルを列挙します。
    #
    #   - ``GM_ROOT``  ( 探索ルートの絶対パス )
    #   - ``GM_PAT``  ( ルートからの相対パスに適用する正規表現文字列 )
    #   - ``GM_INC_LINKS``  ( シンボリックリンクを含めるなら ``1``, それ以外は ``0`` )
    #
    # 実行結果は以下のようになります。
    #
    #  - ``GM_PAT`` にマッチした場合に絶対パスを 1 行ずつ標準出力へ書き出します。
    #  - ``GM_INC_LINKS`` が ``1`` のときは, シンボリックリンクも候補に含めるようにし, 呼び出し側で ``include_symlinks`` を ``True`` にすることで制御します。
    #  - sudo実行が失敗した場合は sudo なしで同じスクリプトを再実行し, いずれかが成功すればその結果を採用します。
    #
    py_script = r"""
import os, re, sys
root = os.environ.get("GM_ROOT", "")
pat  = os.environ.get("GM_PAT", ".*")
want_links = os.environ.get("GM_INC_LINKS", "0") == "1"
rx = re.compile(pat)
for dp, _dirs, files in os.walk(root, followlinks=False):
    rel = os.path.relpath(dp, root)
    rel = "" if rel == "." else rel
    for fn in files:
        rp = fn if not rel else rel + "/" + fn
        if rx.search(rp):
            sys.stdout.write(os.path.join(root, rp) + "\n")
            sys.stdout.flush()
        if want_links:
            ap = os.path.join(dp, fn)
            if os.path.islink(ap):
                rp = fn if not rel else rel + "/" + fn
                if rx.search(rp):
                    sys.stdout.write(os.path.join(root, rp) + "\n")
                    sys.stdout.flush()
""".strip()

    for src in resolved_srcs:
        try:
            abs_norm = normalize_src_abs(src, home_abs_for_tilde=home_abs)
        except ValueError as e:
            _LOG.warning(_("reject SRC outside HOME (relative not confined): %s (%s)") % (src, e))
            continue
        is_abs = is_abs_path(abs_norm)
        if not is_abs:
            _LOG.warning(_("skip non-absolute SRC after normalization (unexpected): %s") % (src))
            continue

        # ------------------------------------------------------------------
        # Directory-SRC (リテラルディレクトリ指定) の先行判定 (sudo 経路)
        # 条件:
        #   - looks_like_regex(src) == False
        #   - abs_norm がディレクトリ (sudo / 非 sudo いずれかの test -d が成功)
        # 動作:
        #   abs_norm 配下の通常ファイルを python3/os.walk で列挙し acc に追加。
        # ------------------------------------------------------------------
        if not looks_like_regex(src):
            check_dir = (
                f"sudo -n test -d {shlex.quote(abs_norm)} "
                f"|| test -d {shlex.quote(abs_norm)}"
            )
            rc_dir, _out_dir, _err_dir = run_remote_cmd_capture(
                ssh, ["bash", "-lc", check_dir], timeout=DEFAULT_TIMEOUT
            )
            if rc_dir == 0:
                _LOG.debug(
                    "Directory-SRC literal detected (sudo-walk) via abs path: src=%s abs_norm=%s",
                    src,
                    abs_norm,
                )

                py_script_dir = r"""
import os, sys
root = os.environ.get("GM_ROOT", "")
for dp, _dirs, files in os.walk(root, followlinks=False):
    for fn in files:
        ap = os.path.join(dp, fn)
        sys.stdout.write(ap + "\n")
        sys.stdout.flush()
""".strip()

                # sudo または非 sudo のいずれかで歩ければ採用
                cmd_sudo = (
                    "sudo -n env GM_ROOT=" + shlex.quote(abs_norm) +
                    " python3 - <<'PY'\n" + py_script_dir + "\nPY"
                )
                rc_walk, out, _err_walk = run_remote_cmd_capture(
                    ssh, ["bash", "-lc", cmd_sudo], timeout=DEFAULT_TIMEOUT
                )
                if rc_walk != 0:
                    cmd_nonsudo = (
                        "env GM_ROOT=" + shlex.quote(abs_norm) +
                        " python3 - <<'PY'\n" + py_script_dir + "\nPY"
                    )
                    rc2, out2, _err2 = run_remote_cmd_capture(
                        ssh, ["bash", "-lc", cmd_nonsudo], timeout=DEFAULT_TIMEOUT
                    )
                    if rc2 != 0:
                        _LOG.warning(_("directory-SRC sudo-walk failed at root=%s") % (abs_norm))
                        continue
                    out = out2

                for line in (out or "").splitlines():
                    p = line.strip()
                    if p:
                        acc.add(p)
                # この SRC については通常の正規表現ロジックには進まない
                continue
        # ------------------------------------------------------------------
        # ここからは「正規表現 SRC」または「非ディレクトリのリテラル SRC」
        # として, 従来通り root/tail_re を使った列挙を行う。
        # ------------------------------------------------------------------
        try:
            root, tail_re = split_src_to_root_and_tail_regex(abs_norm)
        except ValueError as e:
            _LOG.warning(_("bad SRC pattern %s: %s") % (src, e))
            continue

        # root ディレクトリの存在確認 ( sudo/非 sudo のどちらかで通ればOK )
        check_root = f"sudo -n test -d {shlex.quote(root)} || test -d {shlex.quote(root)}"
        rc, _out, _err = run_remote_cmd_capture(ssh, ["bash", "-lc", check_root], timeout=DEFAULT_TIMEOUT)
        if rc != 0:
            _LOG.debug("skip missing/non-dir root: %s", root)
            continue

        pat = tail_re if tail_re else r".*"

        # sudo -n で試行
        cmd_sudo = (
            "sudo -n env "
            f"GM_ROOT={shlex.quote(root)} GM_PAT={shlex.quote(pat)} GM_INC_LINKS={'1' if include_symlinks else '0'} "
            "python3 - <<'PY'\n" + py_script + "\nPY"
        )
        rc, out, err = run_remote_cmd_capture(ssh, ["bash", "-lc", cmd_sudo], timeout=DEFAULT_TIMEOUT)

        if rc != 0:
            # 非 sudo フォールバック
            cmd_nonsudo = (
                "env "
                f"GM_ROOT={shlex.quote(root)} GM_PAT={shlex.quote(pat)} GM_INC_LINKS={'1' if include_symlinks else '0'} "
                "python3 - <<'PY'\n" + py_script + "\nPY"
            )
            rc2, out2, err2 = run_remote_cmd_capture(ssh, ["bash", "-lc", cmd_nonsudo], timeout=DEFAULT_TIMEOUT)
            if rc2 != 0:
                reason = (err or err2 or "").strip()
                _LOG.debug("remote walk failed at root=%s: %s", root, reason)
                continue
            out = out2

        for line in (out or "").splitlines():
            p = line.strip()
            if p:
                acc.add(p)

    out = sorted(acc)
    _LOG.debug("candidates (remote/sudo-walk): %d", len(out))
    return out

def enumerate_candidates_for_host(
    ssh: SSHClientLike,          # paramiko.SSHClient 想定 (型固定しない)
    sftp_client: SFTPClientLike, # paramiko.SFTPClient (型固定しない)
    resolved_srcs: List[str],
    home_abs: str,
    *,
    use_sudo: bool,
    pack_remote: bool,
    follow_symlinks: bool,
    verbose: bool,
) -> List[str]:
    """リモートホスト上での候補を列挙する。

    gm-gather CLI がリモート SRC の候補を収集する際に利用され, 引数と処理の対応は次の通りです。

    - ``pack_remote`` と ``use_sudo`` を共に ``True`` にすると, sudo で Python スクリプトを実行して候補を収集します。
    - 上記以外の組み合わせでは, ``_enumerate_via_sftp_walk()`` を利用し, SFTP でルートディレクトリを走査して候補を収集します。

    Args:
        ssh (SSHClientLike): sudo を伴う走査時に利用する SSH クライアント。
        sftp_client (SFTPClientLike): 非 sudo 経路での SFTP 走査に用いるクライアント。
        resolved_srcs (List[str]): HOME 展開済み SRC 文字列。
        home_abs (str): ``~`` 展開に利用するホームディレクトリ。
        use_sudo (bool): sudo での走査を許可する場合は ``True``。
        pack_remote (bool): リモートでの ``--pack`` 動作を行う場合は ``True``。
        follow_symlinks (bool): リンク先を辿りたい場合は ``True``。
        verbose (bool): 詳細ログを有効化する場合は ``True``。

    Returns:
        List[str]: リモート側で抽出された候補絶対パス。

    Examples:
        >>> from unittest.mock import patch
        >>> ssh = object()
        >>> sftp = object()
        >>> with patch('gm_tools.core_select._enumerate_via_remote_walk_with_sudo', return_value=['/remote/a']) as patched:
        ...     enumerate_candidates_for_host(ssh, sftp, ['SRC'], '/home/demo', use_sudo=True, pack_remote=True, follow_symlinks=False, verbose=False)
        ['/remote/a']
        >>> patched.assert_called_once()
        >>> with patch('gm_tools.core_select._enumerate_via_sftp_walk', return_value=['/remote/b']) as patched2:
        ...     enumerate_candidates_for_host(ssh, sftp, ['SRC'], '/home/demo', use_sudo=False, pack_remote=False, follow_symlinks=False, verbose=False)
        ['/remote/b']
        >>> patched2.assert_called_once()
    """
    include_symlinks = bool(pack_remote)
    if pack_remote and use_sudo:
        return _enumerate_via_remote_walk_with_sudo(
            ssh, resolved_srcs, home_abs, verbose, include_symlinks=include_symlinks
        )
    return _enumerate_via_sftp_walk(
        sftp_client, resolved_srcs, home_abs, verbose, include_symlinks=include_symlinks
    )

def enumerate_candidates_local(paths: Iterable[str]) -> Iterator[str]:
    """ローカルファイルシステム上の候補パスを列挙します。

    gm-scatter CLI がローカル SRC トークンを展開する際に利用され, SRC に含まれる正規表現の
    メタ文字の有無で処理を分岐します。

    - SRC に正規表現のメタ文字が含まれる場合は ``looks_like_regex()`` で検出し,
        ``split_src_to_root_and_tail_regex()`` により走査起点パス ( ``root`` ) と相対パターン
        正規表現 ( ``tail_re`` ) へ分割します。その後は ``root`` 配下を走査して「``root`` を基準にした
        相対パス」に ``re.search(tail_re)`` を適用します。この経路では **通常ファイルのみ** を列挙し,
        ディレクトリは後段処理 ( 明示ディレクトリ指定時 ) に委ねます。
    - 正規表現のメタ文字が含まれない場合は単一のリテラルパスとして扱い, 存在していれば
        ファイル/ディレクトリいずれもそのまま列挙します。存在しない場合は黙って無視します。
    - 相対パスはカレントディレクトリ基準で絶対化し, ``~`` 展開はリテラル/正規表現の
        判定を壊さないタイミングでのみ行います。

    Args:
        paths (Iterable[str]): gm-scatter CLI で指定された SRC 文字列群。

    Yields:
        Iterator[str]: 絶対パス化され, 正規表現やリテラル判定を経た候補。

    Examples:
        >>> import tempfile
        >>> from pathlib import Path
        >>> from gm_tools.core_select import enumerate_candidates_local
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     root = Path(tmp)
        ...     (root / 'sample.txt').write_text('x', encoding='utf-8')
        ...     results = list(enumerate_candidates_local([str(root / 'sample.txt')]))
        >>> any(p.endswith('sample.txt') for p in results)
        True
    """
    seen: Set[str] = set()
    cwd: str = os.getcwd()

    # "~" を含むかどうかの判定ヘルパ ( 展開のタイミングを誤らないため分離 )
    def _looks_tilde(s: str) -> bool:
        s_in: str = s
        return s_in.startswith("~" + os.sep) or s_in == "~" or s_in.startswith("~/")

    for p in paths:
        token: str = p
        # 1) 正規表現のメタ文字の検出は**文字列を壊さず**そのまま判定する
        #    - ここでは expanduser もしない ( "~" を誤解しない )
        has_meta: bool = looks_like_regex(token)

        # 2) 実際にファイル探索に使うための絶対パス化
        #    - "~" はこのタイミングでのみ展開する
        token_expanded: str = os.path.expanduser(token) if _looks_tilde(token) else token
        is_abs: bool = is_abs_path(token_expanded)

        abs_raw: str = token_expanded if is_abs else os.path.abspath(os.path.join(cwd, token_expanded))
        # tail の正規表現を壊さないため, ここでは '\\' => '/' の全体置換は行わない
        abs_norm: str = abs_raw

        if not has_meta:
            ap_exact: str = abs_norm
            if os.path.exists(ap_exact):
                ap_real: str = os.path.abspath(ap_exact)
                if ap_real not in seen:
                    seen.add(ap_real)
                    yield ap_real
            # リテラル指定で存在しなければ何も列挙しない ( 静かに無視 )
            continue

        # --- 正規表現モード ---
        try:
            root: str
            tail_re: str
            # split_src_to_root_and_tail_regex 側で head ( パス ) だけを正規化し,
            # tail ( 正規表現 ) は無改変で保持する
            root, tail_re = split_src_to_root_and_tail_regex(abs_norm)
        except ValueError as _ex:
            _ex_msg: str = str(_ex)
            # 不正トークンは無視 ( gather と整合 )
            continue

        if not os.path.isdir(root):
            # 走査起点がディレクトリでなければ列挙不可
            continue

        pattern_text: str = tail_re if tail_re else r".*"
        try:
            rx: re.Pattern[str] = re.compile(pattern_text)
        except re.error as _re:
            _re_msg: str = str(_re)
            # 不正な正規表現は無視
            continue

        walk_root: str = root
        dirpath: str
        _dirnames: List[str]
        filenames: List[str]
        for dirpath, _dirnames, filenames in os.walk(walk_root, followlinks=False):
            name: str
            for name in filenames:
                ap: str = os.path.join(dirpath, name)
                rel: str = ap[len(walk_root):].lstrip("/\\")
                m: Optional[re.Match[str]] = rx.search(rel)
                if m is not None:
                    ap_abs: str = os.path.abspath(ap)
                    if ap_abs not in seen:
                        seen.add(ap_abs)
                        yield ap_abs

        # ルート自身に対するマッチ ( 空相対にマッチ ) を考慮し, ディレクトリ根を含める
        try:
            root_rel_self: str = ""
            m_root: Optional[re.Match[str]] = rx.search(root_rel_self)
            if m_root is not None:
                root_abs: str = os.path.abspath(root)
                if root_abs not in seen:
                    seen.add(root_abs)
                    yield root_abs
        except Exception as _e:
            _e_msg: str = str(_e)
            # 失敗時は黙って無視 ( 列挙結果に影響なし )
            pass
