from __future__ import annotations
import subprocess
from typing import List
from ._local_types import CommandResult, Config

def _base_ssh_args(cfg: Config, host: str) -> List[str]:
    return [
        "ssh",
        "-o", f"StrictHostKeyChecking={cfg.ssh_strict}",
        "-p", str(cfg.ssh_port),
        f"{cfg.ssh_user}@{host}",
    ]

def run_remote(cfg: Config, host: str, argv: List[str]) -> CommandResult:
    cmd = _base_ssh_args(cfg, host) + ["--"] + argv
    p = subprocess.run(cmd, capture_output=True, text=True)
    return CommandResult(p.returncode, p.stdout, p.stderr)

def run_sudo(cfg: Config, host: str, argv: List[str]) -> CommandResult:
    cmd = _base_ssh_args(cfg, host) + ["--"] + ["sudo", "-n"] + argv
    p = subprocess.run(cmd, capture_output=True, text=True)
    return CommandResult(p.returncode, p.stdout, p.stderr)

def pipe_to_tee(cfg: Config, host: str, path: str, content: str, sudo: bool) -> CommandResult:
    base = _base_ssh_args(cfg, host)
    tee_argv = ["tee", path]
    cmd = base + ["--"] + (["sudo", "-n"] if sudo else []) + tee_argv
    p = subprocess.run(cmd, input=content, capture_output=True, text=True)
    return CommandResult(p.returncode, p.stdout, p.stderr)
