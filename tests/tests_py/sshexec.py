from __future__ import annotations
import subprocess
from typing import List, Union
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

def ssh_run_raw(
    ssh_user: str,
    host: str,
    port: int,
    strict: Union[bool, str],
    *remote_argv: str,
) -> subprocess.CompletedProcess[str]:
    """
    素の ssh を直接叩くヘルパ（Config 非依存）。
    - 戻り値は subprocess.CompletedProcess[str]
    - strict が bool の場合は yes/no に正規化。str の場合はそのまま使用。
    用途: スナップショット採取など、cfg に依存したくない場面。
    """
    strict_str: str = strict if isinstance(strict, str) else ("yes" if strict else "no")
    argv: List[str] = [
        "ssh",
        "-p",
        str(port),
        "-o",
        f"StrictHostKeyChecking={strict_str}",
        "--",
        f"{ssh_user}@{host}",
    ] + list(remote_argv)
    return subprocess.run(argv, capture_output=True, text=True)
