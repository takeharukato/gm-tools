from __future__ import annotations
import subprocess, shlex
from typing import List
from .types import CommandResult, Config

def _run_local_argv(argv: List[str]) -> CommandResult:
    p = subprocess.run(argv, capture_output=True, text=True)
    return CommandResult(p.returncode, p.stdout, p.stderr)

def run_gather(cfg: Config, host: str, user: str, src: str, dest: str, extra: List[str]) -> CommandResult:
    argv: List[str] = list(cfg.gm_gather_cmd) + ["-h", host, "-u", user, "-n"]
    if cfg.verbose: argv.append("-v")
    argv += extra + ["--", src, dest]
    return _run_local_argv(argv)

def run_scatter(cfg: Config, host: str, user: str, src: str, dest: str, extra: List[str]) -> CommandResult:
    argv: List[str] = list(cfg.gm_scatter_cmd) + ["-h", host, "-u", user, "-n"]
    if cfg.verbose: argv.append("-v")
    argv += extra + ["--", src, dest]
    return _run_local_argv(argv)
