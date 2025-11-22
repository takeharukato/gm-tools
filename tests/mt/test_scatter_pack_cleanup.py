#
# src/で,
#
# PYTHONPATH=. python3 -m pytest -q ../tests/test_scatter_pack_cleanup.py
#
"""
Unit tests for pack upload cleanup ensuring local archives are removed.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, List, Optional, Tuple
from unittest.mock import MagicMock

import pytest

import gm_tools.scatter_cli as scatter_cli


@pytest.mark.parametrize("raises", [False, True])
def test_make_push_one_pack_removes_local_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raises: bool) -> None:
    """Verify that `_make_push_one_pack` always deletes the local archive and temp directory."""
    pack_root: Path = tmp_path / "src"
    pack_root.mkdir()
    remote_rel_map = {str(pack_root.resolve()): "payload"}

    created_tar_paths: List[str] = []
    created_tmp_dirs: List[str] = []

    def fake_local_pack_paths_to_tmp(
        paths: Iterable[str],
        follow_symlinks: bool,
        arcnames: Optional[List[str]] = None,
    ) -> Tuple[str, List[str]]:
        tmp_dir: str = tempfile.mkdtemp(prefix="gm-tools-pack-", dir=str(tmp_path))
        tar_path: str = os.path.join(tmp_dir, "bundle.tar.gz")
        with open(tar_path, "wb") as fh:
            fh.write(b"tar")
        created_tar_paths.append(tar_path)
        created_tmp_dirs.append(tmp_dir)
        arc_list: List[str] = list(arcnames) if arcnames is not None else []
        return tar_path, arc_list

    upload_calls: List[str] = []

    def fake_upload_pack_and_extract(
        ssh: MagicMock,
        sftp: MagicMock,
        tar_path: str,
        dest_abs_root: str,
        sudo_extract: bool,
        host: str,
        report: Any,
        dry_run: bool,
        *,
        target_user: Optional[str] = None,
        selinux_mode: str = "auto",
    ) -> None:
        upload_calls.append(tar_path)
        assert os.path.exists(tar_path)
        if raises:
            raise RuntimeError("upload failed")

    monkeypatch.setattr(scatter_cli, "local_pack_paths_to_tmp", fake_local_pack_paths_to_tmp)
    monkeypatch.setattr(scatter_cli, "upload_pack_and_extract", fake_upload_pack_and_extract)

    make_push_one_pack = getattr(scatter_cli, "_make_push_one_pack")

    push_one = make_push_one_pack(
        ssh=MagicMock(),
        sftp=MagicMock(),
        pack_srcs=[pack_root],
        dest_abs_root="/dest",
        sudo_extract=False,
        follow_symlinks=False,
        target_user="demo",
        selinux_mode="auto",
        _timeout=30.0,
        host="host-a",
        remote_rel_map=remote_rel_map,
    )

    if raises:
        with pytest.raises(RuntimeError):
            push_one(MagicMock(), pack_root, "/dest", False)
    else:
        push_one(MagicMock(), pack_root, "/dest", False)

    # Second invocation should be a no-op regardless of the first call outcome.
    push_one(MagicMock(), pack_root, "/dest", False)

    assert created_tar_paths, "expected a local archive to be created"  # ensure test exercised the path
    assert len(upload_calls) == 1

    for tar_path in created_tar_paths:
        assert not Path(tar_path).exists(), "local archive should be deleted"
    for tmp_dir in created_tmp_dirs:
        assert not Path(tmp_dir).exists(), "temporary directory should be deleted"

    # No additional archives should be created after the first invocation.
    assert len(created_tar_paths) == 1
