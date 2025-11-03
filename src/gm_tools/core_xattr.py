# -*- coding:utf-8 -*-
from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Optional, Tuple, List

try:
    import paramiko  # type: ignore
except Exception as e:
    raise RuntimeError("Paramiko is required: pip install paramiko") from e

from .core_cmd_flavor import run_remote_cmd_capture


def _run0(ssh: "paramiko.SSHClient", cmd: str, use_sudo: bool, timeout: float = 30.0) -> Tuple[int, str, str]:
    return run_remote_cmd_capture(ssh, (["sudo"] if use_sudo else []) + ["bash", "-lc", cmd], timeout=timeout)


@dataclass
class RemoteToolCaps:
    has_getfacl: bool
    has_setfacl: bool
    has_getfattr: bool
    has_setfattr: bool
    has_stat: bool
    has_chmod: bool
    has_chown: bool


@dataclass
class SavedMeta:
    owner: Optional[str]
    group: Optional[str]
    mode_oct: Optional[str]          # e.g. '0644'
    acl_file: Optional[str]          # remote tmp file path (if saved)
    xattr_file: Optional[str]        # remote tmp file path (if saved)


def detect_remote_tools(ssh: "paramiko.SSHClient", use_sudo: bool) -> RemoteToolCaps:
    def ok(c: str) -> bool:
        rc, _, _ = _run0(ssh, f"command -v {c} >/dev/null 2>&1", use_sudo, 5.0)
        return rc == 0

    return RemoteToolCaps(
        has_getfacl=ok("getfacl"),
        has_setfacl=ok("setfacl"),
        has_getfattr=ok("getfattr"),
        has_setfattr=ok("setfattr"),
        has_stat=ok("stat"),
        has_chmod=ok("chmod"),
        has_chown=ok("chown"),
    )


def resolve_primary_group(ssh: "paramiko.SSHClient", user: str, use_sudo: bool) -> Optional[str]:
    rc, out, _ = _run0(ssh, f"id -gn {shlex.quote(user)}", use_sudo, 10.0)
    s: str = out.strip()
    return s if (rc == 0 and s) else None


def snapshot_meta(
    ssh: "paramiko.SSHClient",
    rtmp_meta_dir: str,
    abs_path: str,
    caps: RemoteToolCaps,
    use_sudo: bool,
    timeout: float = 30.0,
) -> SavedMeta:
    q: str = shlex.quote(abs_path)
    owner: Optional[str] = None
    group: Optional[str] = None
    mode_oct: Optional[str] = None
    acl_file: Optional[str] = None
    xattr_file: Optional[str] = None

    if caps.has_stat:
        rc, out, _ = _run0(ssh, f"stat -c '%U:%G:%a' {q}", use_sudo, timeout)
        if rc == 0:
            s: str = out.strip()
            parts: List[str] = s.split(":")
            if len(parts) == 3:
                owner, group, mode_oct = parts[0], parts[1], parts[2]

    if caps.has_getfacl:
        rc_mk, out_mk, _ = _run0(ssh, f"mktemp {shlex.quote(rtmp_meta_dir)}/acl.XXXXXX", use_sudo, 5.0)
        if rc_mk == 0:
            acl_file = out_mk.strip()
            _run0(ssh, f"getfacl --absolute-names {q} > {shlex.quote(acl_file)}", use_sudo, timeout)

    if caps.has_getfattr:
        rc_mk2, out_mk2, _ = _run0(ssh, f"mktemp {shlex.quote(rtmp_meta_dir)}/xattr.XXXXXX", use_sudo, 5.0)
        if rc_mk2 == 0:
            xattr_file = out_mk2.strip()
            _run0(ssh, f"getfattr -d --absolute-names {q} > {shlex.quote(xattr_file)} 2>/dev/null || true", use_sudo, timeout)

    return SavedMeta(owner=owner, group=group, mode_oct=mode_oct, acl_file=acl_file, xattr_file=xattr_file)


def restore_meta(
    ssh: "paramiko.SSHClient",
    abs_path: str,
    meta: SavedMeta,
    caps: RemoteToolCaps,
    use_sudo: bool,
    timeout: float = 60.0,
) -> None:
    q: str = shlex.quote(abs_path)
    if caps.has_chown and meta.owner and meta.group:
        _run0(ssh, f"chown -h {shlex.quote(meta.owner)}:{shlex.quote(meta.group)} {q}", use_sudo, timeout)
    if caps.has_chmod and meta.mode_oct:
        _run0(ssh, f"chmod {meta.mode_oct} {q}", use_sudo, timeout)
    if caps.has_setfacl and meta.acl_file:
        _run0(ssh, f"setfacl --restore={shlex.quote(meta.acl_file)}", use_sudo, timeout)
    if caps.has_setfattr and meta.xattr_file:
        _run0(ssh, f"setfattr --restore={shlex.quote(meta.xattr_file)} 2>/dev/null || true", use_sudo, timeout)
