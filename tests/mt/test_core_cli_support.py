#
# src/ で:
#
#   PYTHONPATH=. python3 -m pytest -q ../tests/mt/test_core_cli_support.py
#

from __future__ import annotations

import getpass
import socket

import pytest  # type: ignore

from gm_tools.core_cli_support import validate_hosts_connectivity


def _ensure_local_ssh_available() -> None:
    """localhost:22 への TCP 接続確認。利用不可ならテストを skip する。"""

    try:
        with socket.create_connection(("localhost", 22), timeout=2.0):
            return
    except OSError as exc:  # pragma: no cover - failure path triggers skip
        pytest.skip(f"localhost:22 へ接続できません: {exc}")

def test_validate_hosts_connectivity_localhost() -> None:
    _ensure_local_ssh_available()

    current_user: str = getpass.getuser()
    result = validate_hosts_connectivity(
        ["localhost"],
        ssh_user=current_user,
        port=22,
        key_filename=None,
        password=None,
        timeout=10.0,
        strict_host_key_checking=False,
        debug_print=False,
    )

    assert result.reachable_hosts == ["localhost"]
    assert result.unreachable_hosts == []
    assert result.errors == {}
    assert result.has_failures() is False
