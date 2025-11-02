#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# cspell:ignore identitiesonly
from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import paramiko  # type: ignore
except Exception as e:
    raise RuntimeError("Paramiko is required: pip install paramiko") from e

DEFAULT_SSH_PORT: int = 22
DEFAULT_TIMEOUT: float = 30.0
DEFAULT_SOCKET_TIMEOUT: float = 60.0


@dataclass
class SSHConfig:
    host: str
    port: int
    ssh_user: str
    key_filename: Optional[str]
    password: Optional[str]
    timeout: float
    strict_host_key_checking: bool


def finalize_sockets(timeout: float = DEFAULT_SOCKET_TIMEOUT) -> None:
    socket.setdefaulttimeout(timeout)


# ---------------- ~/.ssh/config 取込み ----------------

def _read_ssh_config_file() -> Optional[paramiko.SSHConfig]:
    cfg_path: Path = Path.home() / ".ssh" / "config"
    if not cfg_path.exists():
        return None
    with cfg_path.open("r", encoding="utf-8", errors="ignore") as fp:
        return paramiko.SSHConfig.from_file(fp)


def _expand_first_existing(paths: List[str]) -> Optional[str]:
    for p in paths:
        expanded: str = os.path.expanduser(p)
        if os.path.exists(expanded):
            return expanded
    return None


def _merge_with_ssh_config(cfg: SSHConfig) -> Dict[str, Any]:
    """
    Merge SSHConfig with values from ~/.ssh/config (Host, HostName, Port, User,
    IdentityFile, IdentitiesOnly, ProxyCommand). Caller may still override.
    """
    connect: Dict[str, Any] = {
        "hostname": cfg.host,
        "port": cfg.port,
        "username": cfg.ssh_user,
        "key_filename": os.path.expanduser(cfg.key_filename) if cfg.key_filename else None,
        "password": cfg.password,
        "timeout": cfg.timeout,
        "banner_timeout": cfg.timeout,
        "auth_timeout": cfg.timeout,
        "allow_agent": True,
        "look_for_keys": True,
    }

    ssh_cfg = _read_ssh_config_file()
    if ssh_cfg is None:
        return connect

    # Lookup by raw host; paramiko handles pattern matching inside.
    host_cfg = ssh_cfg.lookup(cfg.host)

    # HostName / Port / User
    hostname: Optional[str] = host_cfg.get("hostname")
    if hostname:
        connect["hostname"] = hostname
    port_str: Optional[str] = host_cfg.get("port")
    if port_str:
        try:
            connect["port"] = int(port_str)
        except ValueError:
            pass
    user_cfg: Optional[str] = host_cfg.get("user")
    if user_cfg:
        connect["username"] = user_cfg

    # IdentityFile(s)
    identities_cfg_raw: Optional[List[str]] = host_cfg.get("identityfile")  # type: ignore
    key_list: List[str] = []
    if identities_cfg_raw:
        for k in identities_cfg_raw:
            exp: str = os.path.expanduser(k)
            key_list.append(exp)
    if cfg.key_filename:
        key_list.insert(0, os.path.expanduser(cfg.key_filename))
    # use first existing; when empty -> None (agent/known keys may still work)
    connect["key_filename"] = _expand_first_existing(key_list) if key_list else None

    # IdentitiesOnly
    identities_only: Optional[str] = host_cfg.get("identitiesonly")
    if identities_only and identities_only.lower() in ("yes", "true", "1"):
        connect["allow_agent"] = False
        connect["look_for_keys"] = False

    # ProxyCommand
    proxy_cmd: Optional[str] = host_cfg.get("proxycommand")
    if proxy_cmd:
        # shell=False internally; use ProxyCommand wrapper
        connect["sock"] = paramiko.ProxyCommand(proxy_cmd)

    return connect


def ssh_open(cfg: SSHConfig, *, debug_print: bool = False) -> paramiko.SSHClient:
    cli: paramiko.SSHClient = paramiko.SSHClient()
    if cfg.strict_host_key_checking:
        cli.load_system_host_keys()
        cli.set_missing_host_key_policy(paramiko.RejectPolicy())
    else:
        cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    connect_kwargs: Dict[str, Any] = _merge_with_ssh_config(cfg)

    if debug_print:
        print(
            f"Connecting to {connect_kwargs.get('hostname')}:{connect_kwargs.get('port', DEFAULT_SSH_PORT)} "
            f"as {connect_kwargs.get('username','')} key_list={connect_kwargs.get('key_filename')}"
        )

    cli.connect(**connect_kwargs)
    return cli


def run_cmd(ssh: paramiko.SSHClient, cmd: str, timeout: float) -> Tuple[int, bytes, bytes]:
    _stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out: bytes = stdout.read()
    err: bytes = stderr.read()
    rc: int = stdout.channel.recv_exit_status()
    return rc, out, err


# ---- CLI ヘルパ（共通SSHオプションの追加） ----

def add_ssh_common_args(parser: Any) -> None:
    parser.add_argument("-s", "--ssh-user", default=None, help="SSH login user. Default: same as --user.")
    parser.add_argument("-P", "--port", type=int, default=DEFAULT_SSH_PORT, help=f"SSH port. Default: {DEFAULT_SSH_PORT}.")
    parser.add_argument("-K", "--key", default=None, help="SSH private key file.")
    parser.add_argument("-W", "--password", default=None, help="SSH password (not recommended).")
    parser.add_argument("-T", "--timeout", type=float, default=DEFAULT_TIMEOUT, help=f"SSH/command timeout seconds. Default: {DEFAULT_TIMEOUT}.")
    parser.add_argument("-S", "--strict-host-key-checking", action="store_true", help="Enable strict host key checking.")
