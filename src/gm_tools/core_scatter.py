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

# === Timeouts (seconds) ===
TAR_DETECT_TIMEOUT: float = 10.0
PREFLIGHT_TEST_TIMEOUT: float = 60.0
EXTRACT_TIMEOUT_NEW: float = 180.0
EXTRACT_TIMEOUT_EXIST: float = 180.0
CHECK_REGFILE_TIMEOUT: float = 30.0
OVERWRITE_TIMEOUT: float = 120.0

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

    def _sftp_write_text(path: str, content: str) -> None:
        # UTF-8 で members file を配置（バイナリモードで明示）
        f: "paramiko.SFTPFile" = sftp.file(path, mode="wb")
        try:
            f.write(content.encode("utf-8"))
        finally:
            f.close()

    # --- cleanup 対象を先に None 初期化（分岐で束縛されない経路があるため） ---
    members_file_new: Optional[str] = None
    members_file_exist: Optional[str] = None
    rtmp_exist: Optional[str] = None

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
        report.add(host, TransferItem(host=host, remote_path=dest_abs_root, phase="transfer", status="failed", reason=f"E_MKDIR_DEST: {str(_ex)}"))
        return

    # sudo が必要な場合は sudo を先頭につける
    # preflight（tar flavor 検知／NEW/EXIST 仕分けのログ）
    try:
        cmd_flavor: CmdFlavor = detect_tar_flavor_remote(ssh,timeout=TAR_DETECT_TIMEOUT)
        flavor: TarFlavor = cmd_flavor.tar
    except Exception as _ex:
        report.add(host, TransferItem(host=host, remote_path=dest_abs_root, phase="transfer", status="failed", reason=f"E_TAR_DETECT: {str(_ex)}"))
        return

    # tar のローカルメンバー一覧（相対パス）を得て、DEST 直下に置く前提でそのまま仕分け
    try:
        rel_files: List[str]; empty_dirs: List[str]
        rel_files, empty_dirs = list_tar_members_local(tar_path)
    except Exception as _ex:
        report.add(host, TransferItem(host=host, remote_path=dest_abs_root, phase="transfer", status="failed", reason=f"E_TAR_LIST: {str(_ex)}"))
        return

    try:
        exist_set, new_set = split_exist_new_by_remote_presence(
            ssh,
            dest_abs_root,
            rel_files,
            use_sudo=sudo_extract,
            timeout=PREFLIGHT_TEST_TIMEOUT,
        )

        try:
            print(f"[preflight] host={host} tar={flavor} EXIST={len(exist_set)} NEW={len(new_set)} DEST={dest_abs_root}")
        except Exception:
            pass
    except Exception as _ex:
        report.add(host, TransferItem(host=host, remote_path=dest_abs_root, phase="transfer", status="failed", reason=f"E_PREFLIGHT: {str(_ex)}"))
        return

    # 空ディレクトリの作成（属性変更は行わない）。EXIST/NEW 仕分けには影響しない。
    for _d in empty_dirs:
        try:
            remote_mkdir_p(ssh, os.path.join(dest_abs_root, _d), use_sudo=sudo_extract)
        except Exception as _ex:
            report.add(
                host,
                TransferItem(host=host, remote_path=os.path.join(dest_abs_root, _d), phase="transfer", status="failed", reason=f"E_MKDIR_EMPTY: {str(_ex)}"),
            )
            return

    # === Step3 実動作 ===
    #  1) NEW セットのみを DEST に抽出（-T list）
    #  2) EXIST セットは別 tmp に抽出 → 内容のみ既存へ上書き（属性は変更しない）

    # NEW セット抽出
    if new_set:
        members_file_new = f"{rtmp}/members.new.txt"
        # list_tar_members_local は相対パス（アーカイブ内の名前）なので、そのまま 1 行 1 メンバーで書く
        _sftp_write_text(members_file_new, "\n".join(sorted(new_set)) + "\n")
        try:
            extract_cmd_new: List[str] = build_tar_extract_cmd(
                flavor=flavor,
                dest_abs=dest_abs_root,
                tar_gz_path=remote_tar,
                use_sudo=sudo_extract,
                members_file=members_file_new,
            )
        except Exception as _ex:
            report.add(host, TransferItem(host=host, remote_path=dest_abs_root, phase="transfer", status="failed", reason=f"E_BUILD_EXTRACT_NEW: {str(_ex)}"))
            return
        rc_n, out_n, err_n = run_remote_cmd_capture(ssh, extract_cmd_new, timeout=EXTRACT_TIMEOUT_NEW)
        if rc_n != 0:
            reason_n: str = (err_n.strip() or out_n.strip() or f"E_EXTRACT_NEW: rc={rc_n}")
            report.add(host, TransferItem(host=host, remote_path=f"{dest_abs_root}/...", phase="transfer", status="failed", reason=reason_n))
            return

    # EXIST セットの内容置換（通常ファイルのみ）
    if exist_set:
        # まず EXIST のみを別 tmp へ抽出
        _stdin2, stdout2, _stderr2 = ssh.exec_command("mktemp -d /tmp/gm-exist.XXXXXXXX")
        rtmp_exist = stdout2.read().decode().strip() or "/tmp"
        members_file_exist = f"{rtmp}/members.exist.txt"
        _sftp_write_text(members_file_exist, "\n".join(sorted(exist_set)) + "\n")
        try:
            extract_cmd_exist: List[str] = build_tar_extract_cmd(
                flavor=flavor,
                dest_abs=rtmp_exist,
                tar_gz_path=remote_tar,
                use_sudo=False, # tmpへの展開にsudo不要
                members_file=members_file_exist,
            )
        except Exception as _ex:
            report.add(host, TransferItem(host=host, remote_path=dest_abs_root, phase="transfer", status="failed", reason=f"E_BUILD_EXTRACT_EXIST: {str(_ex)}"))
            return
        rc_e, out_e, err_e = run_remote_cmd_capture(ssh, extract_cmd_exist, timeout=EXTRACT_TIMEOUT_EXIST)
        if rc_e != 0:
            reason_e: str = (err_e.strip() or out_e.strip() or f"E_EXTRACT_EXIST_TMP: rc={rc_e}")
            report.add(host, TransferItem(host=host, remote_path=f"{dest_abs_root}/...", phase="transfer", status="failed", reason=reason_e))
            return

        # 置換ループ（通常ファイルのみ）。属性保持のため、ファイル自体は置き換えず、中身だけ上書き。
        # - 既存がディレクトリ/シンボリックリンク/デバイス等の場合はスキップ（設計通り）
        # - 失敗時は当該パスで failed を記録しつつ続行（ホスト全体は可能なら継続）
        for rel in sorted(exist_set):
            # 安全な相対 → 絶対
            src_tmp: str = os.path.join(rtmp_exist, rel)
            dst_abs: str = os.path.join(dest_abs_root, rel)

            # ファイル種別確認：通常ファイルだけ扱う（シェルの [ -f ] 判定）
            # sudo が必要な場合があるので、確認も sudo 経由で行う。
            # 1) src_tmp は通常ファイルであること
            # 2) dst_abs は通常ファイルであること（存在は EXSIT 前提）
            check_cmd: List[str] = (["sudo"] if sudo_extract else []) + [
                "bash", "-lc",
                shlex.quote(f'[ -f "{src_tmp}" ] && [ -f "{dst_abs}" ]')
            ]
            rc_c, _o_c, _e_c = run_remote_cmd_capture(ssh, check_cmd, timeout=CHECK_REGFILE_TIMEOUT)
            if rc_c != 0:
                report.add(host, TransferItem(host=host, remote_path=dst_abs, phase="transfer", status="failed", reason="skip exist: non-regular file (src or dst)"))
                continue

            # 中身のみ置換（truncate+write）。属性（owner/group/mode/xattr/ACL/SELinux）は変更しない。
            # - シンプルに cat > で十分（inode は不変、mtime/ctime は変わり得る：仕様通り）。
            # - sudo が要れば sudo 経由。
            # - エスケープは安全のため shlex.quote 済みの一括シェルで実行。
            overwrite_cmd_str: str = f'cat {shlex.quote(src_tmp)} > {shlex.quote(dst_abs)}'
            overwrite_cmd: List[str] = (["sudo"] if sudo_extract else []) + ["bash", "-lc", shlex.quote(overwrite_cmd_str)]
            rc_w, _o_w, err_w = run_remote_cmd_capture(ssh, overwrite_cmd, timeout=OVERWRITE_TIMEOUT)
            if rc_w != 0:
                report.add(host, TransferItem(host=host, remote_path=dst_abs, phase="transfer", status="failed", reason=(err_w.strip() or "E_OVERWRITE")))
            else:
                report.add(host, TransferItem(host=host, remote_path=dst_abs, phase="transfer", status="done"))

    # すべて成功していれば DEST/... 全体に done を一つ付ける
    report.add(host, TransferItem(host=host, remote_path=f"{dest_abs_root}/...", phase="transfer", status="done"))

    # 後始末
    ssh.exec_command(f"rm -f {shlex.quote(remote_tar)} || true")

    # tmp は積極削除しなくてもよいが、痕跡を減らす
    if members_file_new is not None:
        ssh.exec_command(f"rm -f {shlex.quote(members_file_new)} || true")
    if members_file_exist is not None:
        ssh.exec_command(f"rm -f {shlex.quote(members_file_exist)} || true")
    if rtmp_exist is not None:
        ssh.exec_command(f"rm -rf {shlex.quote(rtmp_exist)} || true")

    # mktemp -d で作成した rtmp 自体も掃除（失敗は無視）
    ssh.exec_command(f"rm -rf {shlex.quote(rtmp)} || true")

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
