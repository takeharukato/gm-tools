# -*- coding:utf-8 -*-
#
# 環境変数:
#   GM_SCATTER_DEBUG=1|true|yes|on
#     診断用ログ ( tzf 比較や -T 検証等 ) を出力する ( 既定: 抑制 )
from __future__ import annotations

import os
import tarfile
import tempfile
import shlex
import logging
import posixpath

from dataclasses import dataclass
from typing import Iterable, List, Tuple, Optional, Set, Dict

from .core_ssh import SSHClientLike, SFTPClientLike, SFTPFileLike
from .core_path_handling import dest_rel_from_abs
from .core_report import (
    TransferReport,
    TransferItem,
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

from .core_selinux import (
    SelinuxMode,                  # Literal["auto","policy","ignore"]
    detect_selinux_capable,       # (ssh) -> bool
    restorecon_recursive_if_needed,  # (ssh, paths, mode) -> None (必要時のみ実行)
)

from .core_xattr import (
    check_acl_tools_available,     # (ssh) -> bool
    check_xattr_tools_available,   # (ssh) -> bool
    stat_owner_group_mode,         # (ssh, path, use_sudo) -> Tuple[str,str,int]
    chown_chmod,                   # (ssh, path, owner, group, mode, use_sudo) -> None
    capture_acl_dump,              # (ssh, path, dump_dir, use_sudo) -> Optional[str]
    restore_acl_dump,              # (ssh, dump_file, use_sudo) -> None
    capture_xattr_dump,            # (ssh, path, dump_dir, use_sudo) -> Optional[str]
    restore_xattr_dump,            # (ssh, dump_file, use_sudo) -> None
)

# === Timeouts (seconds) ===
TAR_DETECT_TIMEOUT: float = 10.0
PREFLIGHT_TEST_TIMEOUT: float = 60.0
EXTRACT_TIMEOUT_NEW: float = 180.0
EXTRACT_TIMEOUT_EXIST: float = 180.0
CHECK_REGFILE_TIMEOUT: float = 30.0
OVERWRITE_TIMEOUT: float = 120.0

# === internal debug log function ===

# デバッグフラグ
_DEBUG: bool = str(os.environ.get("GM_SCATTER_DEBUG", "")).lower() in ("1", "true", "yes", "on")

_LOG: logging.Logger = logging.getLogger(__name__)

def _dbg_log(msg: str) -> None:
    if _DEBUG:
        _LOG.debug(msg)


# ------------------------
# Remote path normalizers
# ------------------------
def _normalize_remote_rel_file(rel: Optional[str]) -> str:
    """
    remote_rel を「DEST からの相対“ファイル”パス」として扱い、安全化する。
      - None/空なら "" を返す（呼び出し側で basename(local) にフォールバック可）
      - '\\' を '/' に統一
      - 先頭の '/' は除去（絶対化の禁止）
      - './' の折り畳み、'//' の除去
      - スタック方式で '..' を評価 : ベースより上に出る場合のみ拒否
      - 正常時は正規化済みの相対パスを返す（内部 '..' は解消される）
    """
    rel_in: Optional[str] = rel
    if not rel_in:
        return ""
    r: str = str(rel_in).replace("\\", "/")
    while r.startswith("/"):
        r = r[1:]
    # 分割してスタックで '..' を評価
    parts: List[str] = [p for p in r.split("/") if p not in ("", ".")]
    stack: List[str] = []
    p: str
    for p in parts:
        if p == "..":
            if not stack:
                # ベースより外に出る
                raise ValueError(f"dangerous relative path: {rel_in}")
            stack.pop()
        else:
            stack.append(p)
    joined: str = "/".join(stack)
    return joined


def _posix_join(*parts: str) -> str:
    out: str = ""
    p: str
    for p in parts:
        out = posixpath.join(out, p) if out else p
    return out

@dataclass
class ScatterOpts:
    dest_abs_root: str
    pack: bool = False
    follow_symlinks: bool = False
    dry_run: bool = False
    sudo_extract: bool = False  # (ssh_user != user) and pack のとき True
    ssh_user: Optional[str] = None
    local_user: Optional[str] = None
    target_user: Optional[str] = None          # --user の解決結果
    selinux_mode: SelinuxMode = "auto"         # --selinux {auto,policy,ignore}  ( pack 経路のみ )


# --- Path helpers (末尾スラッシュ正規化) ------------------------------------
def _norm_noslash(path: str) -> str:
    """ファイル/汎用パス用 : root('/')以外の末尾'/'を除去。"""
    s: str = path
    return s if s == "/" else s.rstrip("/")

def local_pack_paths_to_tmp(
    paths: Iterable[str],
    follow_symlinks: bool,
    arcnames: Optional[List[str]] = None,
) -> Tuple[str, List[str]]:
    """
    与えられたパス群から 重複を除去し, follow_symlinks に応じてシンボリックリンクを処理したうえで,
    tar.gz アーカイブを作成する。
      - arcnames が与えられた場合は、各要素を「DEST からの相対パス」として正規化
        （'\\' => '/'、先頭'/'除去、'..' 脱出拒否、'./' 折り畳み）
      - 同一 arcname は重複排除
    """

    def _filter(ti: tarfile.TarInfo) -> Optional[tarfile.TarInfo]:
        # follow_symlinks=False の場合は、シンボリックリンクのみ除外する。
        # ハードリンクを除外すると、同一内容を別パスに展開するべきエントリが欠落し得る。
        ti_in: tarfile.TarInfo = ti
        if not follow_symlinks and ti_in.issym():
            return None
        return ti_in

    tmpdir: str = tempfile.mkdtemp(prefix="gm_scatter_pack_")
    tar_path: str = os.path.join(tmpdir, "payload.tar.gz")
    in_paths: List[str] = list(paths)
    in_arcnames: Optional[List[str]] = list(arcnames) if arcnames is not None else None
    abs_paths: List[str] = [os.path.abspath(p) for p in in_paths]
    kept_idx: List[int] = []
    kept_roots: List[str] = []
    i: int
    ap0: str
    for i, ap0 in enumerate(abs_paths):
        apn: str = os.path.normpath(ap0)
        skip: bool = False
        root: str
        for root in kept_roots:
            try:
                common: str = os.path.commonpath([apn, root])
            except ValueError:
                common = ""
            if common == root:
                skip = True
                break
        if skip:
            continue
        kept_idx.append(i)
        kept_roots.append(apn)
    plist: List[str] = [in_paths[i] for i in kept_idx]
    alist: Optional[List[str]] = [in_arcnames[i] for i in kept_idx] if in_arcnames is not None else None

    # arcnames 正規化と重複排除（与えられている場合）
    if alist is not None:
        seen_arc: Set[str] = set()
        kept_pairs: List[Tuple[str, str]] = []
        for p, a in zip(plist, alist):
            na: str = _normalize_remote_rel_file(a)
            # 空（=呼び出し側が basename にフォールバックする意図）も許すが、
            # 重複判定のキーとしては空文字もそのまま扱う。
            if na in seen_arc:
                continue
            seen_arc.add(na)
            kept_pairs.append((p, na))
        if kept_pairs:
            plist = [p for p, _ in kept_pairs]
            alist = [na for _, na in kept_pairs]
        else:
            # 全て重複で消えた場合は、両者とも空に揃える
            plist, alist = [], []

    added: List[str] = []

    with tarfile.open(tar_path, mode="w:gz", dereference=follow_symlinks) as tf:
        idx: int = 0
        p_item: str = ""
        for idx, p_item in enumerate(plist):
            ap: str = os.path.abspath(p_item)
            if (not follow_symlinks) and os.path.islink(ap):
                continue
            if alist is not None:
                # arcnames 指定時：'' を正当な値として許容（src='/' を意味）
                arc_in: str = alist[idx]
                arc_norm: str = _normalize_remote_rel_file(arc_in)
                arcname: str = arc_norm  # '' 可
            else:
                # arcnames 未指定時のみ従来ロジック
                arc_tmp: str
                if os.path.isabs(ap):
                    arc_tmp = dest_rel_from_abs(ap)   # 絶対は '//' 除去・先頭 '/' 除去済み
                else:
                    arc_tmp = p_item.replace("\\", "/")
                arc_norm2: str = _normalize_remote_rel_file(arc_tmp)
                if not arc_norm2:
                    # 未指定系でのみ basename フォールバックを適用（'/' などを 'etc' 等にしない）
                    arc_norm2 = os.path.basename(ap).replace("\\", "/")
                arcname: str = arc_norm2

            # ここで arcname == '' の場合、tarfile はトップのディレクトリエントリ '' と
            # その直下（例: 'etc', 'var', ...）を格納する。
            # 抽出側は相対名で扱うため問題なし（'' エントリはディレクトリであり isfile() では弾かれる）。
            tf.add(ap, arcname=arcname, recursive=True, filter=_filter)
            added.append(arcname)

    return tar_path, added

def write_members_file(
    sftp: SFTPClientLike,
    remote_path: str,
    members: Iterable[str],
    *,
    normalize_paths: bool = True,
    preserve_order: bool = False,   # 追加: True で入力順を保持（ソートしない）
) -> None:
    """
    tar -T で参照するメンバーリストを生成・書き出す。
    ポリシー:
      - 重複排除し, ASCII順に安定ソート
      - 各行はアーカイブ内部の相対パス ( 先頭'./'や'/'は除去 )
      - 相対パスは大文字小文字を区別し, アーカイブ内部の名前と完全に一致しなければならない
      - バックスラッシュはスラッシュに統一 ( 将来的なWindows対応のため )
      - 行区切りは LF('\n')。末尾にも LF('\n') を付与
      - CRLF/CR 混入は _sftp_write_text() 側で LF('\n') 正規化することを前提とする

    normalize_paths=False の場合は, 与えられた文字列をそのまま並べ替え・連結のみ行う。
    preserve_order=True の場合は, ソートせずに与えられた順序を保持する。
    """
    def _normalize_members_content(s: str) -> bytes:
        return s.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")

    def _sftp_write_text(path: str, content: str) -> None:
        data: bytes = _normalize_members_content(content)
        f: SFTPFileLike = sftp.open(path, mode="wb")
        try:
            written: int = f.write(data)
            _ = written
        finally:
            f.close()

    canon: List[str] = []
    seen: Set[str] = set()
    m: str
    s: str
    for m in members:
        s = str(m)
        if normalize_paths:
            s = s.replace("\\", "/")
            while s.startswith("./"):
                s = s[2:]
            s = s.lstrip("/")
        if not s:
            continue
        if s in seen:
            continue
        seen.add(s)
        canon.append(s)

    lines: List[str] = canon if preserve_order else sorted(canon)
    text: str = "\n".join(lines) + "\n"
    _sftp_write_text(remote_path, text)
    return

def upload_pack_and_extract(
    ssh: SSHClientLike,
    sftp: SFTPClientLike,
    tar_path: str,
    dest_abs_root: str,
    sudo_extract: bool,
    host: str,
    report: TransferReport,
    dry_run: bool,
    *,
    target_user: Optional[str] = None,
    selinux_mode: SelinuxMode = "auto",
) -> None:
    """
    作成済み tar.gz をリモート一時領域へアップロードし, DEST/ 以下に展開する。
    レイアウト: DEST/<archive internal paths>
    """
    # 事前初期化
    members_file_new: Optional[str] = None
    members_file_exist: Optional[str] = None
    rtmp_exist: Optional[str] = None
    rtmp_meta: Optional[str] = None
    primary_group: Optional[str] = None
    rtmp_exist_created: bool = False
    rtmp_meta_created: bool = False
    rtmp_created: bool = False

    # 能力検査
    selinux_capable: bool = detect_selinux_capable(ssh)
    acl_ok: bool = check_acl_tools_available(ssh)
    xattr_ok: bool = check_xattr_tools_available(ssh)

    # target_user の primary group
    if sudo_extract and target_user:
        rc_gid, out_gid, _ = run_remote_cmd_capture(
            ssh, ["bash", "-lc", f"id -gn {shlex.quote(target_user)}"], timeout=CHECK_REGFILE_TIMEOUT
        )
        primary_group = out_gid.strip() if rc_gid == 0 else None

    planned_item: TransferItem = TransferItem(
        host=host, remote_path=f"{dest_abs_root}/...", phase="plan", status="planned"
    )
    report.add(host, planned_item)

    if dry_run:
        return

    # リモート一時ディレクトリ
    rc_mk, out_mk, _ = run_remote_cmd_capture(
        ssh, ["bash", "-lc", "mktemp -d /tmp/gm-scatter.XXXXXXXX"], timeout=PREFLIGHT_TEST_TIMEOUT
    )
    rtmp: str = (out_mk.strip() if rc_mk == 0 else "/tmp")
    rtmp_created = (rc_mk == 0)
    _uid = getattr(os, "getuid", lambda: 0)()
    remote_tar: str = f"{rtmp}/payload.{os.getpid()}.{_uid}.tar.gz"

    # 一時物の一括掃除（存在しなくてもエラーにしない）
    def _cleanup_all() -> None:
        try:
            run_remote_cmd_capture(ssh, ["bash","-lc", f"rm -f {shlex.quote(remote_tar)} || true"], timeout=CHECK_REGFILE_TIMEOUT)
            if members_file_new is not None:
                run_remote_cmd_capture(ssh, ["bash","-lc", f"rm -f {shlex.quote(members_file_new)} || true"], timeout=CHECK_REGFILE_TIMEOUT)
            if members_file_exist is not None:
                run_remote_cmd_capture(ssh, ["bash","-lc", f"rm -f {shlex.quote(members_file_exist)} || true"], timeout=CHECK_REGFILE_TIMEOUT)
            if rtmp_exist is not None and rtmp_exist_created:
                run_remote_cmd_capture(ssh, ["bash","-lc", f"rm -rf {shlex.quote(rtmp_exist)} || true"], timeout=CHECK_REGFILE_TIMEOUT)
            if rtmp_meta is not None and rtmp_meta_created:
                run_remote_cmd_capture(ssh, ["bash","-lc", f"rm -rf {shlex.quote(rtmp_meta)} || true"], timeout=CHECK_REGFILE_TIMEOUT)
            if rtmp and rtmp_created:
                run_remote_cmd_capture(ssh, ["bash","-lc", f"rm -rf {shlex.quote(rtmp)} || true"], timeout=CHECK_REGFILE_TIMEOUT)
        except Exception:
            pass

    # ここから先を 1 本の try/finally でガード
    try:
        # 1) tar.gz をアップロード
        try:
            sftp.put(tar_path, remote_tar)
        except Exception as exc:
            report.add(
                host,
                TransferItem(
                    host=host, remote_path=f"{dest_abs_root}/...",
                    phase="transfer", status="failed",
                    reason=f"E_UPLOAD_TAR: {str(exc)}",
                ),
            )
            return

        # 2) DEST を作成
        try:
            remote_mkdir_p(ssh, dest_abs_root, use_sudo=sudo_extract)
        except Exception as _ex:
            report.add(host, TransferItem(host=host, remote_path=dest_abs_root, phase="transfer", status="failed", reason=f"E_MKDIR_DEST: {str(_ex)}"))
            return

        # 3) tar flavor 検出
        try:
            cmd_flavor: CmdFlavor = detect_tar_flavor_remote(ssh, timeout=TAR_DETECT_TIMEOUT)
            flavor: TarFlavor = cmd_flavor.tar
        except Exception as _ex:
            report.add(host, TransferItem(host=host, remote_path=dest_abs_root, phase="transfer", status="failed", reason=f"E_TAR_DETECT: {str(_ex)}"))
            return

        # 4) ローカルアーカイブのメンバー一覧
        try:
            rel_files, empty_dirs = list_tar_members_local(tar_path)
        except Exception as _ex:
            report.add(host, TransferItem(host=host, remote_path=dest_abs_root, phase="transfer", status="failed", reason=f"E_TAR_LIST: {str(_ex)}"))
            return

        # 5) EXIST/NEW 仕分け
        try:
            exist_set, new_set = split_exist_new_by_remote_presence(
                ssh, dest_abs_root, rel_files, use_sudo=sudo_extract, timeout=PREFLIGHT_TEST_TIMEOUT,
            )
            _dbg_log(f"[preflight] host={host} tar={flavor} EXIST={len(exist_set)} NEW={len(new_set)} DEST={dest_abs_root}")
        except Exception as _ex:
            report.add(host, TransferItem(host=host, remote_path=dest_abs_root, phase="transfer", status="failed", reason=f"E_PREFLIGHT: {str(_ex)}"))
            return

        # アーカイブ順
        rel_order: List[str] = list(rel_files)
        exist_list: List[str] = [r for r in rel_order if r in exist_set]
        new_list:   List[str] = [r for r in rel_order if r in new_set]

        # 6) 空ディレクトリを作成（属性は変更しない）
        empty_dir_created: Dict[str, bool] = {}
        for _d in empty_dirs:
            _d_norm: str = _d.rstrip("/")
            _dst_dir: str = posixpath.join(dest_abs_root, _d_norm)
            rc_pre, _, _ = run_remote_cmd_capture(
                ssh, ["bash", "-lc", f"test -d {shlex.quote(_dst_dir)}"], timeout=CHECK_REGFILE_TIMEOUT
            )
            preexists: bool = (rc_pre == 0)
            try:
                remote_mkdir_p(ssh, _dst_dir, use_sudo=sudo_extract)
            except Exception as _ex:
                report.add(host, TransferItem(host=host, remote_path=_dst_dir, phase="transfer", status="failed", reason=f"E_MKDIR_EMPTY: {str(_ex)}"))
                return
            empty_dir_created[_d_norm] = (not preexists)

        # 新規作成ディレクトリの chown（sudo_extract かつ target_user）
        if empty_dirs and sudo_extract and target_user is not None and primary_group is not None:
            for _d2 in empty_dirs:
                _d_norm2: str = _d2.rstrip("/")
                if not empty_dir_created.get(_d_norm2, False):
                    continue
                _dst_dir2: str = posixpath.join(dest_abs_root, _d_norm2)
                try:
                    chown_chmod(ssh, _dst_dir2, owner=target_user, group=primary_group, mode=None, use_sudo=True)
                except Exception as _ex:
                    report.add(host, TransferItem(host=host, remote_path=_dst_dir2, phase="transfer", status="failed", reason=f"E_CHOWN_EMPTYDIR: {str(_ex)}"))

        # 7) NEW を -T 抽出
        if new_list:
            members_file_new = f"{rtmp}/members.new.txt"
            try:
                write_members_file(sftp, members_file_new, new_list, preserve_order=True)
            except Exception as _ex:
                report.add(host, TransferItem(host=host, remote_path=f"{dest_abs_root}/...", phase="transfer", status="failed", reason=f"E_MEMBERS_NEW_WRITE: {str(_ex)}"))
                return

            try:
                extract_cmd_new: List[str] = build_tar_extract_cmd(
                    flavor=flavor, dest_abs=dest_abs_root, tar_gz_path=remote_tar,
                    use_sudo=sudo_extract, members_file=members_file_new,
                )
            except Exception as _ex:
                report.add(host, TransferItem(host=host, remote_path=dest_abs_root, phase="transfer", status="failed", reason=f"E_BUILD_EXTRACT_NEW: {str(_ex)}"))
                return

            rc_n, out_n, err_n = run_remote_cmd_capture(ssh, extract_cmd_new, timeout=EXTRACT_TIMEOUT_NEW)
            if rc_n != 0:
                reason_n: str = (err_n.strip() or out_n.strip() or f"E_EXTRACT_NEW: rc={rc_n}")
                report.add(host, TransferItem(host=host, remote_path=f"{dest_abs_root}/...", phase="transfer", status="failed", reason=reason_n))
                return

            # NEW の chown（sudo 経路のみ）: ファイル本体 + 親ディレクトリも整える
            if sudo_extract and target_user is not None and primary_group is not None:
                # 1) ファイル本体
                for rel in new_list:
                    dst_abs_new: str = posixpath.join(dest_abs_root, rel)
                    try:
                        chown_chmod(ssh, dst_abs_new, owner=target_user, group=primary_group, mode=None, use_sudo=True)
                    except Exception as _ex:
                        report.add(host, TransferItem(host=host, remote_path=dst_abs_new, phase="transfer", status="failed", reason=f"E_CHOWN_NEW: {str(_ex)}"))

                # 2) 親ディレクトリ（重複除外）: dest_abs_root 自体は除外
                try:
                    parent_dirs: Set[str] = { posixpath.dirname(posixpath.join(dest_abs_root, rel)) for rel in new_list }
                    root_norm: str = dest_abs_root.rstrip("/")
                    parent_dirs = { pd for pd in parent_dirs if pd.rstrip("/") != root_norm }
                    for pd in sorted(parent_dirs):
                        chown_chmod(ssh, pd, owner=target_user, group=primary_group, mode=None, use_sudo=True)
                except Exception as _ex:
                    report.add(host, TransferItem(host=host, remote_path=f"{dest_abs_root}/...", phase="transfer", status="failed", reason=f"E_CHOWN_PARENTDIRS: {str(_ex)}"))

        # 8) EXIST を tmp に抽出 -> 中身上書き -> メタ復元
        if exist_list:
            rc_mk2, out_mk2, _ = run_remote_cmd_capture(
                ssh, ["bash","-lc","mktemp -d /tmp/gm-exist.XXXXXXXX"], timeout=PREFLIGHT_TEST_TIMEOUT)
            rtmp_exist = (out_mk2.strip() if rc_mk2 == 0 else "/tmp")
            rtmp_exist_created = (rc_mk2 == 0)

            members_file_exist = f"{rtmp}/members.exist.txt"
            try:
                write_members_file(sftp, members_file_exist, exist_list, preserve_order=True)
            except Exception as _ex:
                report.add(host, TransferItem(host=host, remote_path=f"{dest_abs_root}/...", phase="transfer", status="failed", reason=f"E_MEMBERS_EXIST_WRITE: {str(_ex)}"))
                return

            extract_cmd_exist: List[str] = build_tar_extract_cmd(
                flavor=flavor, dest_abs=rtmp_exist, tar_gz_path=remote_tar,
                use_sudo=False, members_file=members_file_exist,
            )
            rc_e, out_e, err_e = run_remote_cmd_capture(ssh, extract_cmd_exist, timeout=EXTRACT_TIMEOUT_EXIST)
            if rc_e != 0:
                reason_e: str = (err_e.strip() or out_e.strip() or f"E_EXTRACT_EXIST_TMP: rc={rc_e}")
                report.add(host, TransferItem(host=host, remote_path=f"{dest_abs_root}/...", phase="transfer", status="failed", reason=reason_e))
                return

            rc_mkm, out_mkm, _ = run_remote_cmd_capture(
                ssh, ["bash","-lc","mktemp -d /tmp/gm-meta.XXXXXXXX"], timeout=PREFLIGHT_TEST_TIMEOUT)
            rtmp_meta = (out_mkm.strip() if rc_mkm == 0 else None)
            rtmp_meta_created = (rc_mkm == 0)

            meta_map: Dict[str, Dict[str, Optional[str]]] = {}
            for rel_exist in exist_list:
                src_tmp_chk: str = posixpath.join(rtmp_exist, rel_exist)
                qsrc_chk: str = shlex.quote(src_tmp_chk)
                rc_chk, _, _ = run_remote_cmd_capture(
                    ssh,
                    ["bash", "-lc", f"test -f {qsrc_chk} || (echo '__MISSING__'; ls -ld {qsrc_chk} 2>/dev/null || true; echo '__PARENT__'; ls -ld $(dirname {qsrc_chk}) 2>/dev/null || true; exit 1)"],
                    timeout=CHECK_REGFILE_TIMEOUT,
                )
                if rc_chk != 0:
                    report.add(host, TransferItem(host=host, remote_path=f"{dest_abs_root}/...", phase="transfer", status="failed", reason=f"E_EXIST_EXTRACT_MISSING: {rel_exist}"))
                    return

                dst_abs: str = posixpath.join(dest_abs_root, rel_exist)

                owner_now, group_now, mode_now = stat_owner_group_mode(ssh, dst_abs, use_sudo=sudo_extract)
                acl_dump: Optional[str] = capture_acl_dump(ssh, dst_abs, rtmp_meta, use_sudo=sudo_extract) if (acl_ok and rtmp_meta is not None) else None
                xat_dump: Optional[str] = capture_xattr_dump(ssh, dst_abs, rtmp_meta, use_sudo=sudo_extract) if (xattr_ok and rtmp_meta is not None) else None

                meta_map[rel_exist] = {
                    "owner": owner_now,
                    "group": group_now,
                    "mode": format(mode_now & 0o7777, "04o"),
                    "acl": acl_dump,
                    "xattr": xat_dump,
                }

            for rel_over in exist_list:
                src_tmp: str = posixpath.join(rtmp_exist, rel_over)
                dst_abs2: str = posixpath.join(dest_abs_root, rel_over)

                # 一時ファイル名は raw で組み立て、挿入時にクォートする
                tmp_dest_raw: str = f"{dst_abs2}.gm-tmp.$$"
                overwrite_cmd: List[str] = (["sudo","-n"] if sudo_extract else []) + [
                    "bash", "-lc",
                    f"set -euo pipefail; "
                    f"cat {shlex.quote(src_tmp)} > {shlex.quote(tmp_dest_raw)}; "
                    f"(touch -r {shlex.quote(src_tmp)} {shlex.quote(tmp_dest_raw)} || true); "
                    f"mv -f {shlex.quote(tmp_dest_raw)} {shlex.quote(dst_abs2)}"
                ]

                rc_w, _, err_w = run_remote_cmd_capture(ssh, overwrite_cmd, timeout=OVERWRITE_TIMEOUT)
                if rc_w != 0:
                    report.add(host, TransferItem(host=host, remote_path=dst_abs2, phase="transfer", status="failed", reason=(err_w.strip() or "E_OVERWRITE")))
                    _LOG.error(f"[overwrite error] host={host} dst={dst_abs2} rc={rc_w} err={err_w.strip()}")
                    continue

                if sudo_extract:
                    meta = meta_map.get(rel_over, {})
                    try:
                        owner_s: str = str(meta.get("owner", ""))
                        group_s: str = str(meta.get("group", ""))
                        raw_mode: Optional[str] = meta.get("mode")
                        mode_i: Optional[int] = int(raw_mode, 8) if isinstance(raw_mode, str) and raw_mode.strip() else None
                        chown_chmod(ssh, dst_abs2, owner=owner_s, group=group_s, mode=mode_i, use_sudo=True)
                    except Exception as _ex:
                        report.add(host, TransferItem(host=host, remote_path=dst_abs2, phase="transfer", status="failed", reason=f"E_RESTORE_META: {str(_ex)}"))

                    acl_dump2: Optional[str] = meta.get("acl")
                    if acl_dump2:
                        try:
                            restore_acl_dump(ssh, acl_dump2, use_sudo=True)
                        except Exception as _ex:
                            report.add(host, TransferItem(host=host, remote_path=dst_abs2, phase="transfer", status="failed", reason=f"E_RESTORE_ACL: {str(_ex)}"))
                    x_dump2: Optional[str] = meta.get("xattr")
                    if x_dump2:
                        try:
                            restore_xattr_dump(ssh, x_dump2, use_sudo=True)
                        except Exception as _ex:
                            report.add(host, TransferItem(host=host, remote_path=dst_abs2, phase="transfer", status="failed", reason=f"E_RESTORE_XATTR: {str(_ex)}"))

                report.add(host, TransferItem(host=host, remote_path=dst_abs2, phase="transfer", status="done"))

        # 9) SELinux restorecon（必要時）
        if selinux_capable:
            paths_restorecon: List[str] = []

            if new_list:
                new_files: List[str] = [posixpath.join(dest_abs_root, rel) for rel in new_list]
                paths_restorecon.extend(new_files)
                parent_dirs: Set[str] = {posixpath.dirname(p) for p in new_files}
                root_norm: str = dest_abs_root.rstrip("/")
                parent_dirs = {d for d in parent_dirs if d.rstrip("/") != root_norm}
                paths_restorecon.extend(sorted(parent_dirs))

            if empty_dirs:
                for d_item in sorted(empty_dirs):
                    paths_restorecon.append(posixpath.join(dest_abs_root, d_item.rstrip("/")))
            if paths_restorecon:
                try:
                    restorecon_recursive_if_needed(
                        ssh=ssh, paths=paths_restorecon, mode=selinux_mode,
                        selinux_capable=selinux_capable, use_sudo=sudo_extract,
                    )
                except Exception as _ex:
                    report.add(host, TransferItem(host=host, remote_path=f"{dest_abs_root}/...", phase="transfer", status="failed", reason=f"E_SELINUX_RESTORECON: {str(_ex)}"))
                    return

        # 10) 完了
        report.add(host, TransferItem(host=host, remote_path=f"{dest_abs_root}/...", phase="transfer", status="done"))
        return

    finally:
        _cleanup_all()


def sftp_put_one(
    ssh: SSHClientLike,
    sftp: SFTPClientLike,
    local_abs: str,
    dest_abs_root: str,
    host: str,
    report: TransferReport,
    dry_run: bool,
    sudo_mkdir: bool = False,
    *,
    # EXIST 上書き時の復元に利用
    enable_restore_meta: bool = True,
    # 配置レイアウトの上書き (相対 SRC 用)。例: "projA/file.txt"
    remote_rel: Optional[str] = None,
) -> None:
    """
    単一のファイル ( またはディレクトリ ) を逐次 SFTP で配置する。
    シンボルリンクは無視 ( dropped ) する。
    レイアウト:
        既定: DEST/<local_abs_without_leading_slash>
        remote_rel 指定時: DEST/<remote_rel>  （<remote_rel> は“ファイル”パスとして扱う）
    """
    # 能力 ( SFTP 経路では毎回チェックしてもよいが, ここで 1 回 )
    acl_ok: bool = check_acl_tools_available(ssh)
    xattr_ok: bool = check_xattr_tools_available(ssh)

    # rtmp_meta は「早期 return が起こらない位置」まで作成を遅延する
    rtmp_meta: Optional[str] = None
    rtmp_meta_created: bool = False
    def _cleanup_meta() -> None:
        if rtmp_meta and rtmp_meta_created:
            try:
                run_remote_cmd_capture(ssh, ["bash","-lc", f"rm -rf {shlex.quote(rtmp_meta)} || true"], timeout=CHECK_REGFILE_TIMEOUT)
            except Exception:
                pass

    # ローカル入力の末尾'/'正規化 ( / は除外 )
    was_rel_input: bool = not os.path.isabs(local_abs)
    ap_raw: str = os.path.abspath(local_abs)
    ap: str = _norm_noslash(ap_raw)
    # 既定レイアウト: <local_abs_without_leading_slash>
    # <local_abs_without_leading_slash> を POSIXパスに正規化
    if was_rel_input:
        # 入力が相対だった場合はそのまま POSIX 化
        rel_default: str = local_abs.replace("\\", "/")
    else:
        # 絶対は pack 側と同じ規則で "C/path" 等に統一
        rel_default: str = dest_rel_from_abs(ap)

    # remote_rel が与えられていればそれを“ファイル”パスとして厳密化
    if remote_rel is not None and str(remote_rel).strip():
        rel_effective: str = _normalize_remote_rel_file(remote_rel)
        if not rel_effective:
            # 空に潰れた場合は basename にフォールバック
            rel_effective = os.path.basename(ap).replace("\\", "/")
    else:
        rel_effective = _normalize_remote_rel_file(rel_default) or os.path.basename(ap).replace("\\", "/")

    # 転送先のベースパス ( 末尾'/'に依存しない )
    rpath: str = _posix_join(dest_abs_root.rstrip("/"), rel_effective)

    # symlink は送らない (この時点では, rtmp_meta未作成のためクリーンナップ不要)
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
        )
    )
    if dry_run:
        # この時点では, rtmp_meta未作成のためクリーンナップ不要
        return

    # 親ディレクトリの作成
    # 親 mkdir -p は ssh 経由で実施
    #

    # rpath は正規化済み(末尾'/'なし)。mkdir は末尾 '/' 不要。
    rdir: str = posixpath.dirname(rpath)

    # mkdir -p をリモートホストで実行
    # sudo 経路を統一ラッパで実施。失敗時は例外で即中断
    try:
        remote_mkdir_p(ssh, rdir, use_sudo=sudo_mkdir)
    except Exception as _ex:
        report.add(host, TransferItem(host=host, remote_path=dest_abs_root, phase="transfer", status="failed", reason=str(_ex)))
        return
    # sudo_mkdir 経路では SFTP 書込可否を事前検査（sudo できないため）
    if sudo_mkdir:
        rc_w, _, _ = run_remote_cmd_capture(
           ssh, ["bash","-lc", f"test -w {shlex.quote(rdir)}"], timeout=CHECK_REGFILE_TIMEOUT
        )
        if rc_w != 0:
            report.add(
                host,
                TransferItem(
                    host=host, remote_path=rdir, phase="transfer", status="failed",
                    reason="E_SFTP_DIR_NOT_WRITABLE: target dir not writable by SSH user; use --pack or pre-adjust perms",
                ),
            )
            return

    # ここから先は実作業フェーズ : rtmp_meta を作成
    if enable_restore_meta and sudo_mkdir and (acl_ok or xattr_ok):
        rc_mkm2: int
        out_mkm2: str
        _err_mkm2: str
        rc_mkm2, out_mkm2, _err_mkm2 = run_remote_cmd_capture(
            ssh, ["bash","-lc","mktemp -d /tmp/gm-meta.XXXXXXXX"], timeout=PREFLIGHT_TEST_TIMEOUT)
        rtmp_meta = (out_mkm2.strip() if rc_mkm2 == 0 else None)
        rtmp_meta_created = (rc_mkm2 == 0)

    root: str
    _dirs: List[str]
    files: List[str]

    if os.path.isdir(ap):
        for root, _dirs, files in os.walk(ap, followlinks=False):
            # ここで必ず root_str を定義して以降で使用する
            root_str: str = os.path.abspath(root)
            # root_str が ap 自身なら rel_effective のまま、
            # それ以外なら ap からの相対パスを連結
            if root_str != ap:
                sub_rel: str = os.path.join(rel_effective, os.path.relpath(root_str, ap))
            else:
                sub_rel = rel_effective

            sub_rel = _normalize_remote_rel_file(sub_rel) if sub_rel else ""
            # ディレクトリとして扱う経路（末尾 '/' 付与は mkdir 側では不要）
            rr: str = _posix_join(dest_abs_root.rstrip("/"), sub_rel) if sub_rel else dest_abs_root.rstrip("/")

            # mkdir -p をリモートホストで実行
            # sudo 経路を統一ラッパで実施。失敗時は例外で即中断
            try:
                remote_mkdir_p(ssh, rr, use_sudo=sudo_mkdir)
            except Exception as _ex:
                report.add(host, TransferItem(host=host, remote_path=dest_abs_root, phase="transfer", status="failed", reason=str(_ex)))
                _cleanup_meta()
                return
            if sudo_mkdir:
                rc_w2, _, _ = run_remote_cmd_capture(
                    ssh, ["bash","-lc", f"test -w {shlex.quote(rr)}"], timeout=CHECK_REGFILE_TIMEOUT
                )
                if rc_w2 != 0:
                    report.add(
                        host,
                        TransferItem(
                            host=host, remote_path=rr, phase="transfer", status="failed",
                            reason="E_SFTP_DIR_NOT_WRITABLE: target dir not writable by SSH user; use --pack or pre-adjust perms",
                        ),
                    )
                    _cleanup_meta()
                    return

            fn: str
            for fn in files:
                lp: str = os.path.join(root_str, fn)
                if os.path.islink(lp):
                    # inner_dst はファイルパス ( 末尾'/'なし ) 。rr は dir 末尾'/'保証済み。
                    inner_dst: str = _posix_join(rr, fn)
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

                # put 先はファイルパス ( 末尾'/'なし )
                dst: str = _posix_join(rr, fn)
                _sftp_put_with_exist_restore(
                    ssh=ssh,
                    sftp=sftp,
                    local_path=lp,
                    remote_path=dst,
                    host=host,
                    report=report,
                    sudo_needed=sudo_mkdir,  # mkdir と同一条件
                    acl_ok=acl_ok,
                    xattr_ok=xattr_ok,
                    rtmp_meta=rtmp_meta,
                )
    else:
        _sftp_put_with_exist_restore(
            ssh=ssh,
            sftp=sftp,
            local_path=ap,
            # 単体ファイル : rpath はファイルパスとして末尾'/'なしで渡す
            remote_path=rpath,
            host=host,
            report=report,
            sudo_needed=sudo_mkdir,
            acl_ok=acl_ok,
            xattr_ok=xattr_ok,
            rtmp_meta=rtmp_meta,
        )

    # 後始末
    _cleanup_meta()
    return

def _sftp_put_with_exist_restore(
    *,
    ssh: SSHClientLike,
    sftp: SFTPClientLike,
    local_path: str,
    remote_path: str,
    host: str,
    report: TransferReport,
    sudo_needed: bool,
    acl_ok: bool,
    xattr_ok: bool,
    rtmp_meta: Optional[str],
) -> None:
    """
    SFTP で 1 ファイルを put。
    - 既存の場合は上書き前に owner/group/mode/ACL/xattr をキャプチャし, 上書き後に復元 ( コマンドがある範囲 ) 。
    - 新規の場合はそのまま put ( SFTP 経路では SELinux は非対応・chown/chmod は行わない ) 。
    """
    # 既存判定
    qdst: str = shlex.quote(remote_path)
    rc_ex: int
    _o_ex: str
    _e_ex: str
    rc_ex, _o_ex, _e_ex = run_remote_cmd_capture(ssh, (["bash", "-lc", f"test -e {qdst}"]), timeout=CHECK_REGFILE_TIMEOUT)
    exist_before: bool = (rc_ex == 0)

    # メタの事前キャプチャ ( EXIST のみ )
    owner: Optional[str] = None
    group: Optional[str] = None
    mode: Optional[int] = None
    acl_dump: Optional[str] = None
    xat_dump: Optional[str] = None

    if exist_before and sudo_needed:
        owner_now: str
        group_now: str
        mode_now: int
        owner_now, group_now, mode_now = stat_owner_group_mode(ssh, remote_path, use_sudo=True)
        owner = owner_now
        group = group_now
        mode = mode_now
        if acl_ok and rtmp_meta:
            acl_dump = capture_acl_dump(ssh, remote_path, rtmp_meta, use_sudo=True)
        if xattr_ok and rtmp_meta:
            xat_dump = capture_xattr_dump(ssh, remote_path, rtmp_meta, use_sudo=True)

    # put 実行
    try:
        sftp.put(local_path, remote_path)
        report.add(host, TransferItem(host=host, remote_path=remote_path, phase="transfer", status="done", local_path=local_path))
    except Exception as ex:
        report.add(host, TransferItem(host=host, remote_path=remote_path, phase="transfer", status="failed", reason=str(ex), local_path=local_path))
        return

    # 復元 ( EXIST のみ ) 。sudo が必要。
    if exist_before and sudo_needed:
        try:
            chown_chmod(ssh, remote_path, owner=owner or "", group=group or "", mode=mode, use_sudo=True)
        except Exception as _ex:
            report.add(host, TransferItem(host=host, remote_path=remote_path, phase="transfer", status="failed", reason=f"E_RESTORE_META: {str(_ex)}"))

        if acl_dump:
            try:
                restore_acl_dump(ssh, acl_dump, use_sudo=True)
            except Exception as _ex:
                report.add(host, TransferItem(host=host, remote_path=remote_path, phase="transfer", status="failed", reason=f"E_RESTORE_ACL: {str(_ex)}"))
        if xat_dump:
            try:
                restore_xattr_dump(ssh, xat_dump, use_sudo=True)
            except Exception as _ex:
                report.add(host, TransferItem(host=host, remote_path=remote_path, phase="transfer", status="failed", reason=f"E_RESTORE_XATTR: {str(_ex)}"))
