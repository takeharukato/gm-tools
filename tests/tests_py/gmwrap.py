from __future__ import annotations
import subprocess, shlex, tempfile
from typing import List
from ._local_types import CommandResult, Config

def _run_local_argv(argv: List[str]) -> CommandResult:
    print("[DEBUG] _run_local_argv argv:",
      " ".join(shlex.quote(x) for x in argv), flush=True)
    p = subprocess.run(argv, capture_output=True, text=True)
    return CommandResult(p.returncode, p.stdout, p.stderr)

def run_gather(cfg: Config, host: str, user: str, src: str, dest: str, extra: List[str]) -> CommandResult:
    argv: List[str] = list(cfg.gm_gather_cmd) + ["-u", user, "-n"]
    argv: List[str] = list(cfg.gm_scatter_cmd) + ["-u", user, "-n"]
    # 単一ホストでも hosts ファイルを作って -H に渡す
    hf = tempfile.NamedTemporaryFile(mode="w", delete=False)
    try:
        hf.write(host + "\n")
        hf.flush()
        hosts_file = hf.name
    finally:
        hf.close()
    argv += ["-H", hosts_file]
    if cfg.verbose: argv.append("-v")
    argv += extra + ["--", src, dest]
    return _run_local_argv(argv)

def run_scatter(cfg: Config, host: str, user: str, src: str, dest: str, extra: List[str]) -> CommandResult:
    argv: List[str] = list(cfg.gm_scatter_cmd) + ["-u", user, "-n"]
    # 単一ホストでも hosts ファイルを作って -H に渡す
    hf = tempfile.NamedTemporaryFile(mode="w", delete=False)
    try:
        hf.write(host + "\n")
        hf.flush()
        hosts_file = hf.name
    finally:
        hf.close()
    argv += ["-H", hosts_file]
    if cfg.verbose: argv.append("-v")
    argv += extra + ["--", src, dest]
    return _run_local_argv(argv)
