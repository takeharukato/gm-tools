#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
import traceback
from dataclasses import dataclass
from typing import List, Optional, Pattern, Set

from .core_ssh import SSHConfig, ssh_open, run_cmd, DEFAULT_SSH_PORT, DEFAULT_TIMEOUT # type: ignore
from .core_remote_fs import (
    remote_walk_files,
    sftp_exists,
    sftp_isdir,
    sftp_isfile,
)
from .core_path_handling import local_path_for_download, ensure_local_dir
from .core_archive import download_and_extract_tar

try:
    import paramiko  # type: ignore
except Exception as e:
    raise RuntimeError("Paramiko is required: pip install paramiko") from e


@dataclass
class HostResult:
    host: str
    downloaded: int
    warnings: List[str]
    errors: List[str]


def compile_many(patterns: List[str], flags: int) -> List[Pattern[str]]:
    return [re.compile(p, flags) for p in patterns]


def match_any_rel_under(abs_path: str, roots: List[str], rel_regexes: List[Pattern[str]]) -> bool:
    if not rel_regexes:
        return False
    for rt in roots:
        r: str = rt.rstrip("/")
        if not r:
            continue
        if abs_path == r or abs_path.startswith(r + "/"):
            rel: str = abs_path[len(r):].lstrip("/")
            if any(rx.search(rel) for rx in rel_regexes):
                return True
    return False


def enumerate_candidates(
    sftp: "paramiko.SFTPClient",
    explicit_sources: List[str],
    roots: List[str],
    pat_abs: List[Pattern[str]],
    pat_rel: List[Pattern[str]],
    verbose: bool,
) -> List[str]:
    cands: Set[str] = set()

    for src in explicit_sources:
        if src and src.startswith("/"):
            cands.add(src)
        elif verbose and src:
            print(f"[Warning] skip non-absolute src: {src}")

    scan_roots: List[str] = []
    if roots:
        scan_roots.extend(roots)
    else:
        parents: Set[str] = {os.path.dirname(p) for p in explicit_sources if p.startswith("/") and len(p) > 1}
        scan_roots.extend(sorted(parents))

    if pat_abs and scan_roots:
        for rt in scan_roots:
            if not rt.startswith("/"):
                continue
            if not sftp_exists(sftp, rt) or not sftp_isdir(sftp, rt):
                continue
            for ap in remote_walk_files(sftp, rt):
                if any(rx.search(ap) for rx in pat_abs):
                    cands.add(ap)

    for rt in roots:
        if not rt or not rt.startswith("/"):
            continue
        if not sftp_exists(sftp, rt) or not sftp_isdir(sftp, rt):
            continue
        for ap in remote_walk_files(sftp, rt):
            if any(rx.search(ap) for rx in pat_abs):
                cands.add(ap)
            rel: str = ap[len(rt):].lstrip("/")
            if any(rx.search(rel) for rx in pat_rel):
                cands.add(ap)

    out: List[str] = sorted(cands)
    if verbose:
        print(f"[debug] candidates (remote): {len(out)}")
    return out


def download_one(sftp: "paramiko.SFTPClient", remote_abs_path: str, dest_dir: str, host: str) -> None:
    local_abs: str = local_path_for_download(dest_dir, host, remote_abs_path)
    ensure_local_dir(os.path.dirname(local_abs))
    sftp.get(remote_abs_path, local_abs)


def worker(
    host: str,
    dest_local: str,
    explicit_sources: List[str],
    roots: List[str],
    pat_abs: List[Pattern[str]],
    pat_rel: List[Pattern[str]],
    ssh_user: str,
    port: int,
    key: Optional[str],
    password: Optional[str],
    timeout: float,
    strict: bool,
    dry_run: bool,
    verbose: bool,
    *,
    pack_remote: bool = False,
    one_archive: bool = False,
) -> HostResult:
    downloaded: int = 0
    warnings: List[str] = []
    errors: List[str] = []
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

        cands: List[str] = enumerate_candidates(sftp, explicit_sources, roots, pat_abs, pat_rel, verbose)

        if dry_run:
            print(f"[{host}] DRY-RUN download: files={len(cands)}")
            if verbose:
                for p in cands:
                    lp = local_path_for_download(dest_local, host, p)
                    print(f"[plan] {host}:{p} -> {lp}")
            return HostResult(host=host, downloaded=0, warnings=warnings, errors=errors)

        # pack_remote: tar.gz on remote then one-shot download+extract
        if pack_remote and cands:
            # group by roots -> in this simplified refactor, just one tar created via shell tar
            # build list file and tar it on remote
            import random
            import shlex
            CHUNK: int = 200
            sudo: str = ""  # gather は sudo 前提なし（必要であれば将来拡張）
            ident: str = f"/tmp/collect_{os.getpid()}_{random.randint(10**6,10**7-1)}"
            tar_path: str = f"{ident}.tar"

            first: bool = True
            clean: List[str] = [p for p in cands if "\n" not in p]
            for i in range(0, len(clean), CHUNK):
                chunk: List[str] = clean[i:i + CHUNK]
                list_ident: str = f"/tmp/collect_list_{os.getpid()}_{random.randint(10**6,10**7-1)}.lst"
                list_content: str = "\n".join(chunk) + "\n"
                delim: str = f"__GG_{os.getpid()}_{random.randint(10**6,10**7-1)}__"
                rc, _, err = run_cmd(
                    ssh, f"{sudo}sh -c 'LC_ALL=C cat > {shlex.quote(list_ident)} <<\"{delim}\"\n{list_content}{delim}'", timeout
                )
                if rc != 0:
                    raise RuntimeError(f"prepare list failed: {err.decode(errors='ignore')}")
                op: str = "c" if first else "r"
                rc, _, err = run_cmd(
                    ssh, f"{sudo}sh -c 'LC_ALL=C tar -P -{op}f {shlex.quote(tar_path)} -T {shlex.quote(list_ident)} && rm -f {shlex.quote(list_ident)}'", timeout
                )
                if rc != 0:
                    raise RuntimeError(f"tar failed: {err.decode(errors='ignore')}")
                first = False

            rc, _, err = run_cmd(ssh, f"{sudo}gzip -f {shlex.quote(tar_path)}", timeout)
            if rc != 0:
                raise RuntimeError(f"gzip failed: {err.decode(errors='ignore')}")

            extracted: int = download_and_extract_tar(
                sftp=sftp,
                remote_tar_gz=f"{tar_path}.gz",
                extract_base=dest_local,
                subdir=os.path.join(host),  # 直下に展開（abs/relサブ階層は不要）
                verbose=verbose,
            )
            downloaded += extracted
            # cleanup
            run_cmd(ssh, f"{sudo}rm -f {shlex.quote(tar_path)}.gz", timeout)

        else:
            # normal: file-by-file get
            for rp in cands:
                if not sftp_exists(sftp, rp):
                    warnings.append(f"not found (skip): {rp}")
                    continue
                if sftp_isdir(sftp, rp):
                    continue
                if not sftp_isfile(sftp, rp):
                    continue
                download_one(sftp, rp, dest_local, host)
                downloaded += 1
                if verbose:
                    lp = local_path_for_download(dest_local, host, rp)
                    print(f"[get] {host}:{rp} -> {lp}")

        print(f"[{host}] downloaded: {downloaded}")
        for w in warnings:
            print(f"[{host}] Warning: {w}")
        for er in errors:
            print(f"[{host}] Error: {er}")

    except Exception as e:
        if verbose:
            traceback.print_exc()
        errors.append(f"{type(e).__name__}: {e}")
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

    return HostResult(host=host, downloaded=downloaded, warnings=warnings, errors=errors)
