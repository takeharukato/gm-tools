# src/gm_tools/core_scatter.py
# -*- coding:utf-8 -*-
from __future__ import annotations

import os
import tarfile
import tempfile
from dataclasses import dataclass
from typing import Iterable, List, Tuple, Optional

try:
    import paramiko  # type: ignore
except Exception as e:
    raise RuntimeError("Paramiko is required: pip install paramiko") from e

from .core_report import TransferReport, TransferItem


@dataclass
class ScatterOpts:
    dest_abs_root: str
    pack: bool = False
    follow_symlinks: bool = False
    dry_run: bool = False
    sudo_extract: bool = False  # (ssh_user != user) and pack のとき True
    ssh_user: Optional[str] = None
    local_user: Optional[str] = None


def _mkdir_p_remote(ssh: "paramiko.SSHClient", path: str, sudo: bool) -> None:
    cmd: str = ("sudo mkdir -p " + path) if sudo else ("mkdir -p " + path)
    ssh.exec_command(cmd)

def local_pack_paths_to_tmp(paths: Iterable[str], follow_symlinks: bool) -> Tuple[str, List[str]]:
    """
    指定パス群を tar.gz に固めて一時ディレクトリへ作成する。
    follow_symlinks=True の場合はシンボルリンクを実体へ解決する（deref）。
    戻り値: (作成されたtarパス, deref対象となったローカルパス一覧)
    """
    tmpdir: str = tempfile.mkdtemp(prefix="gm-scatter-")
    tar_path: str = os.path.join(tmpdir, "payload.tar.gz")
    deref: List[str] = []

    with tarfile.open(tar_path, mode="w:gz", dereference=follow_symlinks) as tar:
        for p in paths:
            ap: str = os.path.abspath(p)
            exists: bool = os.path.exists(ap)
            islink: bool = os.path.islink(ap)
            if not exists and not islink:
                # 存在しない（かつリンクでもない）ものはスキップ
                continue
            if follow_symlinks and islink:
                deref.append(ap)
            arcname: str = ap.lstrip(os.sep)  # 先頭スラッシュを落として相対名に
            tar.add(ap, arcname=arcname, recursive=True)

    return tar_path, deref

def upload_pack_and_extract(
    ssh: "paramiko.SSHClient",
    sftp: "paramiko.SFTPClient",
    tar_path: str,
    dest_abs_root: str,
    sudo_extract: bool,
    host: str,
    report: TransferReport,
    dry_run: bool,
) -> None:
    """
    作成済みtar.gzをリモート一時領域へアップロードし、DEST/abs/ 以下に展開する。
    """
    planned_item: TransferItem = TransferItem(
        host=host, remote_path=f"{dest_abs_root}/abs/...", phase="plan", status="planned"
    )
    report.add(host, planned_item)

    if dry_run:
        return

    _stdin, stdout, _stderr = ssh.exec_command("mktemp -d /tmp/gm-scatter.XXXXXXXX")
    rtmp: str = stdout.read().decode().strip() or "/tmp"
    remote_tar: str = f"{rtmp}/payload.tar.gz"

    sftp.put(tar_path, remote_tar)

    _mkdir_p_remote(ssh, dest_abs_root, sudo_extract)

    extract_cmd: str = (
        f"cd {dest_abs_root} && "
        + ("sudo " if sudo_extract else "")
        + "tar -xzf "
        + remote_tar
        + " --transform='s,^,abs/,'"
    )
    _c2, _o2, e2 = ssh.exec_command(extract_cmd)
    err: str = e2.read().decode().strip()
    if err:
        report.add(
            host,
            TransferItem(
                host=host,
                remote_path=f"{dest_abs_root}/abs/...",
                phase="transfer",
                status="failed",
                reason=err,
            ),
        )
    else:
        report.add(
            host,
            TransferItem(
                host=host,
                remote_path=f"{dest_abs_root}/abs/...",
                phase="transfer",
                status="done",
            ),
        )

    ssh.exec_command(f"rm -f {remote_tar} || true")


def sftp_put_one(
    ssh: "paramiko.SSHClient",
    sftp: "paramiko.SFTPClient",
    local_abs: str,
    dest_abs_root: str,
    host: str,
    report: TransferReport,
    dry_run: bool,
    sudo_mkdir: bool = False,
) -> None:
    """
    単一のファイル（またはディレクトリ）を逐次SFTPで配置する。
    シンボルリンクは無視（dropped）する。
    """
    ap: str = os.path.abspath(local_abs)
    rel: str = ap.lstrip(os.sep)
    rpath: str = os.path.join(dest_abs_root, "abs", rel)

    # symlink は送らない
    if os.path.islink(ap):
        report.add(
            host,
            TransferItem(
                host=host,
                remote_path=rpath,
                phase="plan",
                status="dropped",
                reason="symlink ignored",
                local_path=ap,
            ),
        )
        return

    # plan
    report.add(
        host,
        TransferItem(
            host=host,
            remote_path=rpath,
            phase="plan",
            status="planned",
            local_path=ap,
        ),
    )
    if dry_run:
        return

    # mkdir -p は ssh 経由で実施
    rdir: str = os.path.dirname(rpath)
    _mkdir_p_remote(ssh, rdir, sudo_mkdir)

    if os.path.isdir(ap):
        for root, _dirs, files in os.walk(ap):
            root_str: str = str(root)
            sub_rel: str = os.path.join(rel, os.path.relpath(root_str, ap)) if root_str != ap else rel
            rr: str = os.path.join(dest_abs_root, "abs", sub_rel)
            _mkdir_p_remote(ssh, rr, sudo_mkdir)
            for fn in files:
                lp: str = os.path.join(root_str, fn)
                if os.path.islink(lp):
                    inner_dst: str = os.path.join(rr, fn)
                    report.add(
                        host,
                        TransferItem(
                            host=host,
                            remote_path=inner_dst,
                            phase="plan",
                            status="dropped",
                            reason="symlink ignored",
                            local_path=lp,
                        ),
                    )
                    continue
                dst: str = os.path.join(rr, fn)
                try:
                    sftp.put(lp, dst)
                    report.add(
                        host,
                        TransferItem(
                            host=host,
                            remote_path=dst,
                            phase="transfer",
                            status="done",
                            local_path=lp,
                        ),
                    )
                except Exception as ex:
                    report.add(
                        host,
                        TransferItem(
                            host=host,
                            remote_path=dst,
                            phase="transfer",
                            status="failed",
                            reason=str(ex),
                            local_path=lp,
                        ),
                    )
    else:
        try:
            sftp.put(ap, rpath)
            report.add(
                host,
                TransferItem(
                    host=host,
                    remote_path=rpath,
                    phase="transfer",
                    status="done",
                    local_path=ap,
                ),
            )
        except Exception as ex:
            report.add(
                host,
                TransferItem(
                    host=host,
                    remote_path=rpath,
                    phase="transfer",
                    status="failed",
                    reason=str(ex),
                    local_path=ap,
                ),
            )
