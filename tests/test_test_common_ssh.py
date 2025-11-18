# gm-tools-tests-20251116/test_test_common_ssh.py
#
# src/ で:
#
#   PYTHONPATH=. python3 -m pytest -q ../tests/test_test_common_ssh.py
#

from __future__ import annotations

from dataclasses import dataclass

import pytest  # type: ignore

from tests_py._local_types import CommandResult
from tests_py import sshexec
from tests_py.test_common_ssh import (
    ssh_run,
    ssh_run_sudo,
    ssh_pipe_to_tee,
)


@dataclass
class DummyConfig:
    ssh_user: str = "ansible"
    ssh_port: int = 22
    ssh_strict: str = "no"


def test_ssh_run_delegates_to_sshexec(monkeypatch): # type: ignore
    called = {}

    def fake_run_remote(cfg, host, argv): # type: ignore
        called["cfg"] = cfg
        called["host"] = host
        called["argv"] = argv
        return CommandResult(0, "ok", "")

    monkeypatch.setattr(sshexec, "run_remote", fake_run_remote) # type: ignore

    cfg = DummyConfig()
    res = ssh_run(cfg, "example", ["echo", "hello"]) # type: ignore

    assert res.rc == 0
    assert called["host"] == "example"
    assert called["argv"] == ["echo", "hello"]


def test_ssh_run_sudo_delegates_to_sshexec(monkeypatch): # type: ignore
    called = {}

    def fake_run_sudo(cfg, host, argv): # type: ignore
        called["cfg"] = cfg
        called["host"] = host
        called["argv"] = argv
        return CommandResult(1, "", "err")

    monkeypatch.setattr(sshexec, "run_sudo", fake_run_sudo) # type: ignore

    cfg = DummyConfig()
    res = ssh_run_sudo(cfg, "host2", ["id"]) # type: ignore

    assert res.rc == 1
    assert called["host"] == "host2"
    assert called["argv"] == ["id"]


def test_ssh_pipe_to_tee_delegates_to_sshexec(monkeypatch): # type: ignore
    called = {}

    def fake_pipe_to_tee(cfg, host, path, content, sudo): # type: ignore
        called["cfg"] = cfg
        called["host"] = host
        called["path"] = path
        called["content"] = content
        called["sudo"] = sudo
        return CommandResult(0, "ok", "")

    monkeypatch.setattr(sshexec, "pipe_to_tee", fake_pipe_to_tee) # type: ignore

    cfg = DummyConfig()
    res = ssh_pipe_to_tee(cfg, "host3", "/tmp/file", "hello", sudo=True) # type: ignore

    assert res.rc == 0
    assert called["host"] == "host3"
    assert called["path"] == "/tmp/file"
    assert called["content"] == "hello"
    assert called["sudo"] is True
