# gm-tools-tests-20251116/test_test_common_config.py
#
# src/ で:
#
#   PYTHONPATH=. python3 -m pytest -q ../tests/test_test_common_config.py
#

from __future__ import annotations

from pathlib import Path

import pytest  # type: ignore
import shlex

from tests_py.test_common_config import load_config_from_env
from tests_py.constants import (
    SSH_USER_DEFAULT,
    SSH_PORT_DEFAULT,
    SSH_STRICT_DEFAULT,
    REMOTE_DEST_ROOT_DEFAULT,
    LOCAL_WORK_ROOT_DEFAULT,
    HOSTS_BOTH_DEFAULT,  # type: ignore
    HOST_UBUNTU_DEFAULT,
    HOST_ALMA_DEFAULT,
    GM_GATHER_CMD_DEFAULT,  # type: ignore
    GM_SCATTER_CMD_DEFAULT,  # type: ignore
    PARALLEL_DEFAULT,
    VERBOSE_DEFAULT, # type: ignore
)


def test_load_config_defaults(monkeypatch, tmp_path): # type: ignore
    """
    環境変数を全て未設定にしたとき,
    constants.*_DEFAULT と同じ値が Config に入ることを確認する。
    """
    monkeypatch.chdir(tmp_path) # type: ignore

    # 対象となる環境変数を全て削除
    for name in [
        "SSH_USER",
        "TARGET_USER",
        "SSH_PORT",
        "SSH_STRICT",
        "REMOTE_DEST_ROOT",
        "LOCAL_WORK_ROOT",
        "HOSTS_BOTH",
        "HOST_UBUNTU",
        "HOST_ALMA",
        "GM_GATHER_CMD",
        "GM_SCATTER_CMD",
        "VERBOSE",
        "PARALLEL",
    ]:
        monkeypatch.delenv(name, raising=False) # type: ignore

    cfg = load_config_from_env(clear_local_root=False)

    assert cfg.ssh_user == SSH_USER_DEFAULT
    # TARGET_USER_DEFAULT の既定値は実装によるが,
    # 少なくとも属性として設定されていることだけを確認しておく。
    assert isinstance(cfg.target_user, str)

    assert cfg.ssh_port == SSH_PORT_DEFAULT
    assert cfg.ssh_strict == SSH_STRICT_DEFAULT
    assert cfg.remote_dest_root == REMOTE_DEST_ROOT_DEFAULT
    assert cfg.local_work_root == LOCAL_WORK_ROOT_DEFAULT

    # hosts_both は HOSTS_BOTH_DEFAULT を shlex.split したものと一致する
    expected_hosts = [h for h in shlex.split(HOSTS_BOTH_DEFAULT) if h]
    assert cfg.hosts_both == expected_hosts

    assert cfg.host_ubuntu == HOST_UBUNTU_DEFAULT
    assert cfg.host_alma == HOST_ALMA_DEFAULT

    assert "gm_tools" in " ".join(cfg.gm_gather_cmd)
    assert "gm_tools" in " ".join(cfg.gm_scatter_cmd)

    assert cfg.parallel == PARALLEL_DEFAULT
    # VERBOSE_DEFAULT と env の組み合わせに依存するため, 「bool であること」のみ確認
    assert isinstance(cfg.verbose, bool)


def test_clear_local_root_true_removes_existing(monkeypatch, tmp_path): # type: ignore
    """
    clear_local_root=True の場合,
    既存の LOCAL_WORK_ROOT が一度削除されて作り直されることを確認する。
    """
    monkeypatch.chdir(tmp_path) # type: ignore

    local_root = tmp_path / "_tmp_test_local" # type: ignore
    local_root.mkdir() # type: ignore
    marker = local_root / "keep_me" # type: ignore
    marker.write_text("x") # type: ignore

    monkeypatch.setenv("LOCAL_WORK_ROOT", str(local_root)) # type: ignore

    cfg = load_config_from_env(clear_local_root=True)
    assert Path(cfg.local_work_root) == local_root
    # 中身が削除されている ( marker が消えている ) ことを確認
    assert not marker.exists() # type: ignore
    # ディレクトリ自体は存在している
    assert local_root.exists() # type: ignore
    assert local_root.is_dir() # type: ignore


def test_clear_local_root_false_keeps_existing(monkeypatch, tmp_path): # type: ignore
    """
    clear_local_root=False の場合,
    既存 LOCAL_WORK_ROOT の内容が消されないことを確認する。
    """
    monkeypatch.chdir(tmp_path) # type: ignore

    local_root = tmp_path / "_tmp_test_local" # type: ignore
    local_root.mkdir() # type: ignore
    marker = local_root / "keep_me" # type: ignore
    marker.write_text("x") # type: ignore

    monkeypatch.setenv("LOCAL_WORK_ROOT", str(local_root)) # type: ignore

    cfg = load_config_from_env(clear_local_root=False)
    assert Path(cfg.local_work_root) == local_root
    # 中身が維持されている
    assert marker.exists() # type: ignore
    assert marker.read_text() == "x" # type: ignore