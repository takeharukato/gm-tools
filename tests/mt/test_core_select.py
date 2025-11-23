# -*- coding: utf-8 -*-
# SPDX-License-Identifier: BSD-2-Clause
"""gm_tools.core_select の浅い正規表現最適化を検証するユニットテスト。

目的:
    - `_enumerate_via_sftp_walk` が「正規表現 tail にスラッシュを含まない場合」に限り
      SFTP の `listdir` だけで候補列挙を完結させる最適化を持つ。その挙動が回帰しないことを確認する。

テスト内容:
    - `test_enumerate_shallow_regex_prefers_listing`
        `remote_walk_files` をモックして呼び出されないことを確認しつつ, `listdir` の結果から
        通常ファイルのみ抽出できるかを検証する。
    - `test_enumerate_shallow_regex_includes_symlinks`
        シンボリックリンクを含めるモードで, `listdir` 結果からリンクも返却対象に含めることを確かめる。
"""

from __future__ import annotations

from typing import Callable, List, cast

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from pytest import MonkeyPatch

import gm_tools.core_select as core_select


class DummySFTP:
    def __init__(self, names: List[str]) -> None:
        self._names = list(names)
        self.listdir_calls: List[str] = []

    def listdir(self, path: str) -> List[str]:
        self.listdir_calls.append(path)
        return list(self._names)

    def open(self, _path: str, _mode: str = "r") -> object:
        raise NotImplementedError

    def put(self, _localpath: str, _remotepath: str) -> None:
        raise NotImplementedError

    def get(self, _remotepath: str, _localpath: str) -> None:
        raise NotImplementedError

    def stat(self, _path: str) -> object:
        raise NotImplementedError

    def lstat(self, _path: str) -> object:
        raise NotImplementedError

    def close(self) -> None:
        pass


def _make_basic_patches(monkeypatch: MonkeyPatch) -> None:
    def _normalize(src: str, home_abs_for_tilde: str) -> str:
        return src

    def _is_abs(path: str) -> bool:
        return True

    def _split(src: str) -> tuple[str, str]:
        return ("/home/demo", r"\.zsh.*")

    def _exists(_cli: object, _path: str) -> bool:
        return True

    def _isdir(_cli: object, path: str) -> bool:
        return path == "/home/demo"

    monkeypatch.setattr(core_select, "normalize_src_abs", _normalize)
    monkeypatch.setattr(core_select, "is_abs_path", _is_abs)
    monkeypatch.setattr(core_select, "split_src_to_root_and_tail_regex", _split)
    monkeypatch.setattr(core_select, "sftp_exists", _exists)
    monkeypatch.setattr(core_select, "sftp_isdir", _isdir)


def test_enumerate_shallow_regex_prefers_listing(monkeypatch: MonkeyPatch) -> None:
    dummy = DummySFTP([".zshrc", "notes.txt"])
    _make_basic_patches(monkeypatch)

    def _looks_like(src: str) -> bool:
        return True

    def _isfile(_cli: object, path: str) -> bool:
        return path.endswith(".zshrc")

    def _islink(_cli: object, path: str) -> bool:
        return False

    def _no_walk(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("recursive walk should not run")

    monkeypatch.setattr(core_select, "looks_like_regex", _looks_like)
    monkeypatch.setattr(core_select, "sftp_isfile", _isfile)
    monkeypatch.setattr(core_select, "sftp_islink", _islink)
    monkeypatch.setattr(core_select, "remote_walk_files", _no_walk)
    enumerate_walk = cast(Callable[..., List[str]], getattr(core_select, "_enumerate_via_sftp_walk"))
    result = enumerate_walk(
        dummy,
        ["/home/demo/.zsh.*"],
        "/home/demo",
        verbose=False,
        include_symlinks=False,
    )
    assert result == ["/home/demo/.zshrc"]
    assert dummy.listdir_calls == ["/home/demo"]


def test_enumerate_shallow_regex_includes_symlinks(monkeypatch: MonkeyPatch) -> None:
    dummy = DummySFTP([".zshrc", ".zshrc.link"])
    _make_basic_patches(monkeypatch)

    def _looks_like(src: str) -> bool:
        return True

    def _isfile(_cli: object, path: str) -> bool:
        return path.endswith(".zshrc") and not path.endswith(".zshrc.link")

    def _islink(_cli: object, path: str) -> bool:
        return path.endswith(".zshrc.link")

    def _no_walk(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("recursive walk should not run")

    monkeypatch.setattr(core_select, "looks_like_regex", _looks_like)
    monkeypatch.setattr(core_select, "sftp_isfile", _isfile)
    monkeypatch.setattr(core_select, "sftp_islink", _islink)
    monkeypatch.setattr(core_select, "remote_walk_files", _no_walk)
    enumerate_walk = cast(Callable[..., List[str]], getattr(core_select, "_enumerate_via_sftp_walk"))
    result = enumerate_walk(
        dummy,
        ["/home/demo/.zsh.*"],
        "/home/demo",
        verbose=False,
        include_symlinks=True,
    )
    assert result == ["/home/demo/.zshrc", "/home/demo/.zshrc.link"]
    assert dummy.listdir_calls == ["/home/demo"]
