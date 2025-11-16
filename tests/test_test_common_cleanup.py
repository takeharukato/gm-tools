# gm-tools-tests-20251116/test_test_common_cleanup.py
#
# src/ で:
#
#   PYTHONPATH=. python3 -m pytest -q ../tests/test_test_common_cleanup.py
#

from __future__ import annotations

from dataclasses import dataclass

import pytest  # type: ignore

from tests_py.test_common_cleanup import (
    _safe_rmtree_abs, # type: ignore
    _clear_dir, # type: ignore
    init_local_work_root,
    cleanup_local_work_root,
    cleanup_extra_local_dirs,
)


@dataclass
class DummyConfig:
    """
    test_common_cleanup のテスト用ダミー Config。
    local_work_root 属性だけあれば十分。
    """
    local_work_root: str


def test_safe_rmtree_respects_ensure_under(tmp_path): # type: ignore
    base = tmp_path / "base" # type: ignore
    base.mkdir() # type: ignore
    victim = base / "victim" # type: ignore
    victim.mkdir() # type: ignore
    outsider = tmp_path / "outsider" # type: ignore
    outsider.mkdir() # type: ignore

    _safe_rmtree_abs(victim, ensure_under=base) # type: ignore
    # base 配下なので削除される
    assert not victim.exists() # type: ignore

    _safe_rmtree_abs(outsider, ensure_under=base) # type: ignore
    # base 配下ではないので削除されない
    assert outsider.exists() # type: ignore


def test_safe_rmtree_ignores_symlink(tmp_path): # type: ignore
    base = tmp_path # type: ignore
    real_dir = base / "real" # type: ignore
    real_dir.mkdir() # type: ignore
    link = base / "link" # type: ignore
    link.symlink_to(real_dir) # type: ignore
    _safe_rmtree_abs(link, ensure_under=base) # type: ignore
    # シンボリックリンクは削除されない
    assert link.exists() # type: ignore
    assert real_dir.exists() # type: ignore


def test_clear_dir_removes_and_recreates(tmp_path): # type: ignore
    base = tmp_path # type: ignore
    target = base / "target" # type: ignore
    target.mkdir() # type: ignore
    marker = target / "keep_me" # type: ignore
    marker.write_text("x") # type: ignore

    _clear_dir(target, ensure_under=base) # type: ignore
    assert target.exists() # type: ignore
    assert target.is_dir() # type: ignore
    # 中身は削除されている
    assert list(target.iterdir()) == [] # type: ignore


def test_init_and_cleanup_local_work_root(tmp_path, monkeypatch): # type: ignore
    monkeypatch.chdir(tmp_path) # type: ignore
    local_root = tmp_path / "_tmp_test_local" # type: ignore
    cfg = DummyConfig(local_work_root=str(local_root)) # type: ignore

    # init でディレクトリが作られる
    init_local_work_root(cfg, ensure_under_cwd=True) # type: ignore
    assert local_root.exists() # type: ignore

    # マーカーを作ってから cleanup で削除される
    marker = local_root / "keep_me" # type: ignore
    marker.write_text("x") # type: ignore
    cleanup_local_work_root(cfg, ensure_under_cwd=True) # type: ignore
    assert not local_root.exists() # type: ignore


def test_cleanup_extra_local_dirs(tmp_path, monkeypatch): # type: ignore
    monkeypatch.chdir(tmp_path) # type: ignore
    (tmp_path / "nf_rel").mkdir() # type: ignore
    (tmp_path / "other_rel").mkdir() # type: ignore

    cleanup_extra_local_dirs(["nf_rel"], ensure_under_cwd=True) # type: ignore
    assert not (tmp_path / "nf_rel").exists() # type: ignore
    assert (tmp_path / "other_rel").exists() # type: ignore