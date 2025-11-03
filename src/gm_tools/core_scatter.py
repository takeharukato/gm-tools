# -*- coding:utf-8 -*-
from __future__ import annotations

import os
import tarfile
import tempfile
import shlex
from dataclasses import dataclass
from typing import Iterable, List, Tuple, Optional

try:
    import paramiko  # type: ignore
except Exception as e:
    raise RuntimeError("Paramiko is required: pip install paramiko") from e

from .core_report import (
    TransferReport,
    TransferItem
)

from .core_archive import (
    list_tar_members_local,
)

from .core_cmd_flavor import (
    CmdFlavor,
    TarFlavor,
    remote_mkdir_p,
    detect_tar_flavor_remote,
    build_tar_extract_cmd,
    run_remote_cmd_capture,
    split_exist_new_by_remote_presence,
)

@dataclass
class ScatterOpts:
    dest_abs_root: str
    pack: bool = False
    follow_symlinks: bool = False
    dry_run: bool = False
    sudo_extract: bool = False  # (ssh_user != user) and pack のとき True
    ssh_user: Optional[str] = None
    local_user: Optional[str] = None

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

            # アーカイブ内のパス名は「先頭スラッシュを落とした絶対パス」とする
            #   /tmp/gmtest/sub/b.txt -> tmp/gmtest/sub/b.txt
            arcname: str = ap.lstrip(os.sep)
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
    作成済みtar.gzをリモート一時領域へアップロードし、DEST/ 以下に展開する。
    レイアウト:
        DEST/<local_abs_without_leading_slash>

    ここでは GNU tar 固有の --transform は使わず、
    1) DEST を mkdir -p
    2) tar -xzf payload.tar.gz -C DEST
    で展開する。これは GNU tar / bsdtar の共通オプションで動く。
    """

    planned_item: TransferItem = TransferItem(host=host,
        remote_path=f"{dest_abs_root}/...",
        phase="plan",
        status="planned")
    report.add(host, planned_item)

    if dry_run:
        return

    # リモートで一時ディレクトリを作成して、そこに tar.gz を置く
    _stdin, stdout, _stderr = ssh.exec_command("mktemp -d /tmp/gm-scatter.XXXXXXXX")
    rtmp: str = stdout.read().decode().strip() or "/tmp"
    remote_tar: str = f"{rtmp}/payload.tar.gz"

    sftp.put(tar_path, remote_tar)

    # 展開先 DEST を作成
    # mkdir -p をリモートホストで実行
    # sudo 経路を統一ラッパで実施。失敗時は例外で即中断
    try:
        remote_mkdir_p(ssh, dest_abs_root, use_sudo=sudo_extract)
    except Exception as _ex:
        report.add(host, TransferItem(host=host, remote_path=dest_abs_root, phase="transfer", status="failed", reason=str(_ex)))
        return

    # sudo が必要な場合は sudo を先頭につける
    # --- Step2: preflight（tar flavor 検知／NEW/EXIST 仕分けのログのみ） ---
    try:
        cmd_flavor: CmdFlavor = detect_tar_flavor_remote(ssh)
        flavor: TarFlavor = cmd_flavor.tar
    except Exception as _ex:
        report.add(host, TransferItem(host=host, remote_path=dest_abs_root, phase="transfer", status="failed", reason=f"tar flavor detect failed: {str(_ex)}"))
        return

    # tar のローカルメンバー一覧（相対パス）を得て、DEST 直下に置く前提でそのまま仕分け
    try:
        rel_members: List[str] = list_tar_members_local(tar_path)
    except Exception as _ex:
        report.add(host, TransferItem(host=host, remote_path=dest_abs_root, phase="transfer", status="failed", reason=f"tar list failed: {str(_ex)}"))
        return

    try:
        exist_set, new_set = split_exist_new_by_remote_presence(
            ssh,
            dest_abs_root,
            rel_members,
            use_sudo=sudo_extract,
            timeout=60.0,
        )
        # 仕分け結果はログ出力のみ（動作は従来どおり）
        try:
            print(f"[preflight] host={host} tar={flavor} EXIST={len(exist_set)} NEW={len(new_set)} DEST={dest_abs_root}")
        except Exception:
            pass
    except Exception as _ex:
        report.add(host, TransferItem(host=host, remote_path=dest_abs_root, phase="transfer", status="failed", reason=f"preflight failed: {str(_ex)}"))
        return

    # --- Step2: GNU/bsdtar 抽出コマンドの切替＆rcで厳密評価 ---
    try:
        extract_cmd: List[str] = build_tar_extract_cmd(
            flavor=flavor,
            dest_abs=dest_abs_root,
            tar_gz_path=remote_tar,
            use_sudo=sudo_extract,
        )
    except Exception as _ex:
        report.add(host, TransferItem(host=host, remote_path=dest_abs_root, phase="transfer", status="failed", reason=f"build extract cmd failed: {str(_ex)}"))
        return

    rc2, out2, err2 = run_remote_cmd_capture(ssh, extract_cmd, timeout=120.0)
    if rc2 != 0:
        reason: str = (err2.strip() or out2.strip() or f"extract failed rc={rc2}")
        report.add(
            host,
            TransferItem(
                host=host,
                remote_path=f"{dest_abs_root}/...",
                phase="transfer",
                status="failed",
                reason=reason,
            ),
        )
    else:
        report.add(
            host,
            TransferItem(
                host=host,
                remote_path=f"{dest_abs_root}/...",
                phase="transfer",
                status="done",
            ),
        )

    # 後始末
    ssh.exec_command(f"rm -f {shlex.quote(remote_tar)} || true")


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
    レイアウト:
        DEST/<local_abs_without_leading_slash>
    """
    ap: str = os.path.abspath(local_abs)
    rel: str = ap.lstrip(os.sep)
    rpath: str = os.path.join(dest_abs_root, rel)

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
    # mkdir -p をリモートホストで実行
    # sudo 経路を統一ラッパで実施。失敗時は例外で即中断
    try:
        remote_mkdir_p(ssh, rdir, use_sudo=sudo_mkdir)
    except Exception as _ex:
        report.add(host, TransferItem(host=host, remote_path=dest_abs_root, phase="transfer", status="failed", reason=str(_ex)))
        return


    if os.path.isdir(ap):
        for root, _dirs, files in os.walk(ap):
            root_str: str = str(root)
            # root_str が ap 自身なら rel のまま、それ以外なら ap からの相対パスを連結
            sub_rel: str = os.path.join(rel, os.path.relpath(root_str, ap)) if root_str != ap else rel
            rr: str = os.path.join(dest_abs_root, sub_rel)
            # mkdir -p をリモートホストで実行
            # sudo 経路を統一ラッパで実施。失敗時は例外で即中断
            try:
                remote_mkdir_p(ssh, rr, use_sudo=sudo_mkdir)
            except Exception as _ex:
                report.add(host, TransferItem(host=host, remote_path=dest_abs_root, phase="transfer", status="failed", reason=str(_ex)))
                return

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
