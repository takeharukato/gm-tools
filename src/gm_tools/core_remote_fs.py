#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import posixpath
import stat
from typing import Any, Iterator, Literal

try:
    import paramiko  # type: ignore
except Exception as e:
    raise RuntimeError("Paramiko is required: pip install paramiko") from e


def _sftp_attr_mode(st: Any) -> int:
    m_raw: Any = getattr(st, "st_mode", 0)
    return int(m_raw) if m_raw is not None else 0


def sftp_kind(sftp: "paramiko.SFTPClient", path: str) -> Literal["missing", "dir", "file", "symlink", "other"]:
    try:
        st: Any = sftp.lstat(path)
    except IOError:
        return "missing"
    mode: int = _sftp_attr_mode(st)
    if mode != 0:
        if stat.S_ISLNK(mode):
            return "symlink"
        if stat.S_ISDIR(mode):
            return "dir"
        if stat.S_ISREG(mode):
            return "file"
        return "other"
    try:
        st2: Any = sftp.stat(path)
        mode2: int = _sftp_attr_mode(st2)
        if mode2 != 0:
            if stat.S_ISDIR(mode2):
                return "dir"
            if stat.S_ISREG(mode2):
                return "file"
            return "other"
    except Exception:
        pass
    return "other"


def sftp_isdir(sftp: "paramiko.SFTPClient", path: str) -> bool:
    return sftp_kind(sftp, path) == "dir"

def sftp_isfile(sftp: "paramiko.SFTPClient", path: str) -> bool:
    return sftp_kind(sftp, path) == "file"

def sftp_islink(sftp: "paramiko.SFTPClient", path: str) -> bool:
    return sftp_kind(sftp, path) == "symlink"

def sftp_exists(sftp: "paramiko.SFTPClient", path: str) -> bool:
    return sftp_kind(sftp, path) != "missing"


def remote_walk_files(sftp: "paramiko.SFTPClient", root: str) -> Iterator[str]:
    """
    Recursively walk remote 'root' and yield regular files (links whose targets are files included).
    """
    stack: list[str] = [posixpath.normpath(root)]
    while stack:
        cur: str = stack.pop()
        try:
            entries: list[Any] = sftp.listdir_attr(cur)
        except IOError:
            continue
        for ent in entries:
            name: str = ent.filename
            if name in (".", ".."):
                continue
            ap: str = posixpath.normpath(f"{cur}/{name}")
            mode: int = _sftp_attr_mode(ent)
            if mode != 0 and stat.S_ISDIR(mode):
                stack.append(ap)
                continue
            if mode != 0 and stat.S_ISREG(mode):
                yield ap
                continue
            if mode != 0 and stat.S_ISLNK(mode):
                try:
                    st2: Any = sftp.stat(ap)
                    if stat.S_ISREG(_sftp_attr_mode(st2)):
                        yield ap
                except Exception:
                    pass


def sftp_put_one(sftp: "paramiko.SFTPClient", local_path: str, remote_path: str) -> None:
    """
    Simple wrapper for a single file upload (PUT).
    """
    sftp.put(local_path, remote_path)
