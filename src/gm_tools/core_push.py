#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Optional, Tuple
from .core_ssh import SSHConfig, ssh_open
from .core_remote_fs import sftp_put_one

try:
    import paramiko  # type: ignore
except Exception as e:
    raise RuntimeError("Paramiko is required: pip install paramiko") from e


def push_file_to_host(
    host: str,
    ssh_user: str,
    port: int,
    key: Optional[str],
    password: Optional[str],
    timeout: float,
    strict: bool,
    local_path: str,
    remote_path: str,
    verbose: bool = False,
) -> Tuple[str, bool, Optional[str]]:
    ssh: Optional[paramiko.SSHClient] = None
    sftp: Optional[paramiko.SFTPClient] = None
    try:
        cfg = SSHConfig(
            host=host,
            port=port,
            ssh_user=ssh_user,
            key_filename=key,
            password=password,
            timeout=timeout,
            strict_host_key_checking=strict,
        )
        ssh = ssh_open(cfg)
        sftp = ssh.open_sftp()
        sftp_put_one(sftp, local_path, remote_path)
        if verbose:
            print(f"[put] {local_path} -> {host}:{remote_path}")
        return host, True, None
    except Exception as e:
        return host, False, f"{type(e).__name__}: {e}"
    finally:
        try:
            if sftp is not None:
                sftp.close()
        except Exception:
            pass
        try:
            if ssh is not None:
                ssh.close()
        except Exception:
            pass
