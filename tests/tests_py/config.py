from __future__ import annotations
import os, shlex
from typing import List
from .types import Config
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
