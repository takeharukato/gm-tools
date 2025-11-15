from __future__ import annotations
import os, shlex, shutil
from typing import Optional
from typing import List
from ._local_types import Config
from .constants import (
    SSH_PORT_DEFAULT, SSH_STRICT_DEFAULT,
    REMOTE_DEST_ROOT_DEFAULT, LOCAL_WORK_ROOT_DEFAULT,
    SSH_USER_DEFAULT, TARGET_USER_DEFAULT,
    HOSTS_BOTH_DEFAULT, HOST_UBUNTU_DEFAULT, HOST_ALMA_DEFAULT,
    GM_GATHER_CMD_DEFAULT, GM_SCATTER_CMD_DEFAULT,
    PARALLEL_DEFAULT
)

def _split_cmd(cmd: str) -> List[str]:
    return shlex.split(cmd)

def _split_cmd_env(env_key: str, default_val: str) -> List[str]:
    raw: str = os.environ.get(env_key, default_val)
    parts: List[str] = shlex.split(raw)
    return parts

def _clear_dir(path: str, *, ensure_under: Optional[str] = None) -> None:
    """
    path を一度まるごと消してから作り直す。
    - ensure_under: 指定されたベース配下でしか削除しない安全装置
    - シンボリックリンクの削除は拒否（誤爆防止）
    """
    p: str = os.path.abspath(path)

    if ensure_under is not None:
        base: str = os.path.abspath(ensure_under)
        if not (p == base or p.startswith(base + os.sep)):
            raise AssertionError(f"refuse to clear outside base: {p} (base={base})")

    if os.path.islink(p):
        raise AssertionError(f"refuse to clear symlink path: {p}")

    if os.path.exists(p):
        shutil.rmtree(p)

    os.makedirs(p, exist_ok=True)

def load_config() -> Config:
    hosts_both = os.environ.get("HOSTS_BOTH", HOSTS_BOTH_DEFAULT).split()
    return Config(
        ssh_user=os.environ.get("SSH_USER", SSH_USER_DEFAULT),
        target_user=os.environ.get("TARGET_USER", TARGET_USER_DEFAULT),
        hosts_both=hosts_both,
        host_ubuntu=os.environ.get("HOST_UBUNTU", HOST_UBUNTU_DEFAULT),
        host_alma=os.environ.get("HOST_ALMA", HOST_ALMA_DEFAULT),
        ssh_port=int(os.environ.get("SSH_PORT", SSH_PORT_DEFAULT)),
        ssh_strict=os.environ.get("SSH_STRICT", SSH_STRICT_DEFAULT),
        remote_dest_root=os.environ.get("REMOTE_DEST_ROOT", REMOTE_DEST_ROOT_DEFAULT),
        local_work_root=os.environ.get("LOCAL_WORK_ROOT", LOCAL_WORK_ROOT_DEFAULT),
        gm_gather_cmd=_split_cmd(os.environ.get("GM_GATHER_CMD", GM_GATHER_CMD_DEFAULT)),
        gm_scatter_cmd=_split_cmd(os.environ.get("GM_SCATTER_CMD", GM_SCATTER_CMD_DEFAULT)),
        verbose=os.environ.get("VERBOSE", "1") == "1",
        parallel=int(os.environ.get("PARALLEL", PARALLEL_DEFAULT)),
    )

def load_config_from_env() -> Config:
    ssh_user: str = os.environ.get("SSH_USER", "ansible")
    target_user: str = os.environ.get("TARGET_USER", ssh_user)

    ssh_port: int = int(os.environ.get("SSH_PORT", "22"))
    ssh_strict_env: str = os.environ.get("SSH_STRICT", "no")
    _ssh_strict: bool = (ssh_strict_env.lower() == "yes")

    remote_dest_root: str = os.environ.get("REMOTE_DEST_ROOT", "/tmp/gmtools_remote_dest")
    local_root: str = os.environ.get("LOCAL_WORK_ROOT", os.path.join(os.getcwd(), "_tmp_test_local"))
    _clear_dir(local_root, ensure_under=os.getcwd())

    hosts_both_raw: List[str] = shlex.split(os.environ.get("HOSTS_BOTH", "localhost"))
    hosts_both: List[str] = []
    i: int = 0
    n: int = len(hosts_both_raw)
    while i < n:
        h_item: str = hosts_both_raw[i]
        if h_item:
            hosts_both.append(h_item)
        i += 1

    host_ubuntu: str = os.environ.get("HOST_UBUNTU", "localhost")
    host_alma: str = os.environ.get("HOST_ALMA", "vmlinux4.local")

    gm_gather_cmd: List[str] = _split_cmd_env("GM_GATHER_CMD", "python3 -m gm_tools.gather_cli")
    gm_scatter_cmd: List[str] = _split_cmd_env("GM_SCATTER_CMD", "python3 -m gm_tools.scatter_cli")

    verbose: bool = (os.environ.get("VERBOSE", "0") == "1")

    cfg: Config = Config(
        ssh_user=ssh_user,
        target_user=target_user,
        ssh_port=ssh_port,
        ssh_strict=ssh_strict_env,
        remote_dest_root=remote_dest_root,
        hosts_both=hosts_both,
        host_ubuntu=host_ubuntu,
        host_alma=host_alma,
        gm_gather_cmd=gm_gather_cmd,
        gm_scatter_cmd=gm_scatter_cmd,
        local_work_root=os.environ.get("LOCAL_WORK_ROOT", LOCAL_WORK_ROOT_DEFAULT),
        parallel=int(os.environ.get("PARALLEL", PARALLEL_DEFAULT)),
        verbose=verbose,
    )
    return cfg
