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
from dataclasses import dataclass
from typing import Iterable, List, Tuple, Optional, Set, Dict

try:
    import paramiko  # type: ignore
except Exception as e:
    raise RuntimeError("Paramiko is required: pip install paramiko") from e

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

# Step4 分離モジュール ( 新規 )
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

def _dbg_log(msg: str) -> None:
    if _DEBUG:
        try:
            print(msg)
        except Exception:
            pass


@dataclass
class ScatterOpts:
    dest_abs_root: str
    pack: bool = False
    follow_symlinks: bool = False
    dry_run: bool = False
    sudo_extract: bool = False  # (ssh_user != user) and pack のとき True
    ssh_user: Optional[str] = None
    local_user: Optional[str] = None
    # Step4 追加 ( 既存呼び出し互換性を壊さない既定あり )
    target_user: Optional[str] = None          # --user の解決結果
    selinux_mode: SelinuxMode = "auto"         # --selinux {auto,policy,ignore}  ( pack 経路のみ )


# --- Path helpers (末尾スラッシュ正規化) ------------------------------------
def _norm_noslash(path: str) -> str:
    """ファイル/汎用パス用 : root('/')以外の末尾'/'を除去。"""
    return path if path == "/" else path.rstrip("/")

def _as_dirpath(path: str) -> str:
    """ディレクトリ専用 : root('/')以外は必ず末尾'/'を付与。"""
    if path == "/":
        return "/"
    p = path.rstrip("/")
    return p + "/" if p else "/"

def _join_norm(*parts: str) -> str:
    """join後に _norm_noslash ( ファイル/汎用パス用 ) 。"""
    return _norm_noslash(os.path.join(*parts))

def _join_dir(*parts: str) -> str:
    """join後に _as_dirpath ( ディレクトリ用 ) 。"""
    return _as_dirpath(os.path.join(*parts))

def local_pack_paths_to_tmp(paths: Iterable[str], follow_symlinks: bool) -> Tuple[str, List[str]]:
    """
    指定パス群を tar.gz に固めて一時ディレクトリへ作成する。
    follow_symlinks=True の場合はシンボルリンクを実体へ解決する ( deref ) 。
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
                # 存在しない ( かつリンクでもない ) ものはスキップ
                continue
            if follow_symlinks and islink:
                deref.append(ap)

            # アーカイブ内のパス名は「先頭スラッシュを落とした絶対パス」とする
            #   /tmp/gmtest/sub/b.txt -> tmp/gmtest/sub/b.txt
            arcname: str = ap.lstrip(os.sep)
            tar.add(ap, arcname=arcname, recursive=True)

    return tar_path, deref


def write_members_file(
    sftp: "paramiko.SFTPClient",
    remote_path: str,
    members: Iterable[str],
    *,
    normalize_paths: bool = True,
) -> None:
    """
    tar -T で参照するメンバーリストを生成・書き出す。
    ポリシー:
      - 重複排除し、ASCII順に安定ソート
      - 各行はアーカイブ内部の相対パス ( 先頭'./'や'/'は除去 )
      - 相対パスは大文字小文字を区別し、アーカイブ内部の名前と完全に一致しなければならない
      - バックスラッシュはスラッシュに統一 ( 将来的なWindows対応のため )
      - 行区切りは LF('\n')。末尾にも LF('\n') を付与
      - CRLF/CR 混入は _sftp_write_text() 側で LF('\n') 正規化することを前提とする

    normalize_paths=False の場合は、与えられた文字列をそのまま並べ替え・連結のみ行う。
    """
    def _normalize_members_content(s: str) -> bytes:
        return s.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")

    def _sftp_write_text(path: str, content: str) -> None:
        data: bytes = _normalize_members_content(content)
        f: "paramiko.SFTPFile" = sftp.file(path, mode="wb")
        try:
            f.write(data)
        finally:
            f.close()

    canon: List[str] = []
    seen: Set[str] = set()
    for m in members:
        s: str = str(m)
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

    text: str = "\n".join(sorted(canon)) + "\n"
    _sftp_write_text(remote_path, text)


def upload_pack_and_extract(
    ssh: "paramiko.SSHClient",
    sftp: "paramiko.SFTPClient",
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
    作成済み tar.gz をリモート一時領域へアップロードし、DEST/ 以下に展開する。
    レイアウト: DEST/<local_abs_without_leading_slash>

    GNU tar 固有の --transform は使わず、
      1) DEST を mkdir -p
      2) tar -xzf payload.tar.gz -C DEST
     ( GNU tar / bsdtar 共通オプション )
    """
    # 事前初期化 ( locals() ガード禁止方針 )
    members_file_new: Optional[str] = None
    members_file_exist: Optional[str] = None
    rtmp_exist: Optional[str] = None
    rtmp: Optional[str] = None
    rtmp_meta: Optional[str] = None
    primary_group: Optional[str] = None

    # 能力検査
    selinux_capable: bool = detect_selinux_capable(ssh)
    acl_ok: bool = check_acl_tools_available(ssh)
    xattr_ok: bool = check_xattr_tools_available(ssh)

    # target_user の primary group を一度だけ取得 ( sudo_extract のときだけ意味がある )
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

    # リモートで一時ディレクトリを作成して、そこに tar.gz を置く ( PATH注入のためrun_remote経由 )
    rc_mk, out_mk, _ = run_remote_cmd_capture(
        ssh, ["bash", "-lc", "mktemp -d /tmp/gm-scatter.XXXXXXXX"], timeout=PREFLIGHT_TEST_TIMEOUT)
    rtmp = (out_mk.strip() if rc_mk == 0 else "/tmp")
    remote_tar: str = f"{rtmp}/payload.tar.gz"

    sftp.put(tar_path, remote_tar)

    # 展開先 DEST を作成
    # mkdir -p をリモートホストで実行
    # sudo 経路を統一ラッパで実施。失敗時は例外で即中断
    try:
        remote_mkdir_p(ssh, dest_abs_root, use_sudo=sudo_extract)
    except Exception as _ex:
        report.add(host, TransferItem(host=host, remote_path=dest_abs_root, phase="transfer", status="failed", reason=f"E_MKDIR_DEST: {str(_ex)}"))
        # 後始末
        run_remote_cmd_capture(ssh, ["bash","-lc", f"rm -f {shlex.quote(remote_tar)} || true"], timeout=CHECK_REGFILE_TIMEOUT)
        if rtmp:
            run_remote_cmd_capture(ssh, ["bash","-lc", f"rm -rf {shlex.quote(rtmp)} || true"], timeout=CHECK_REGFILE_TIMEOUT)
        return

    # sudo が必要な場合は sudo を先頭につける
    # preflight ( tar flavor 検知／NEW/EXIST 仕分けのログ )
    try:
        cmd_flavor: CmdFlavor = detect_tar_flavor_remote(ssh, timeout=TAR_DETECT_TIMEOUT)
        flavor: TarFlavor = cmd_flavor.tar
    except Exception as _ex:
        report.add(host, TransferItem(host=host, remote_path=dest_abs_root, phase="transfer", status="failed", reason=f"E_TAR_DETECT: {str(_ex)}"))
        run_remote_cmd_capture(ssh, ["bash","-lc", f"rm -f {shlex.quote(remote_tar)} || true"], timeout=CHECK_REGFILE_TIMEOUT)
        if rtmp:
            run_remote_cmd_capture(ssh, ["bash","-lc", f"rm -rf {shlex.quote(rtmp)} || true"], timeout=CHECK_REGFILE_TIMEOUT)
        return

    # tar のローカルメンバー一覧 ( 相対パス ) を得る
    try:
        rel_files, empty_dirs = list_tar_members_local(tar_path)
    except Exception as _ex:
        report.add(host, TransferItem(host=host, remote_path=dest_abs_root, phase="transfer", status="failed", reason=f"E_TAR_LIST: {str(_ex)}"))
        run_remote_cmd_capture(ssh, ["bash","-lc", f"rm -f {shlex.quote(remote_tar)} || true"], timeout=CHECK_REGFILE_TIMEOUT)
        if rtmp:
            run_remote_cmd_capture(ssh, ["bash","-lc", f"rm -rf {shlex.quote(rtmp)} || true"], timeout=CHECK_REGFILE_TIMEOUT)
        return

    # EXIST/NEW 仕分け
    try:
        exist_set, new_set = split_exist_new_by_remote_presence(
            ssh,
            dest_abs_root,
            rel_files,
            use_sudo=sudo_extract,
            timeout=PREFLIGHT_TEST_TIMEOUT,
        )

        _dbg_log(f"[preflight] host={host} tar={flavor} EXIST={len(exist_set)} NEW={len(new_set)} DEST={dest_abs_root}")

    except Exception as _ex:
        report.add(host, TransferItem(host=host, remote_path=dest_abs_root, phase="transfer", status="failed", reason=f"E_PREFLIGHT: {str(_ex)}"))
        run_remote_cmd_capture(ssh, ["bash","-lc", f"rm -f {shlex.quote(remote_tar)} || true"], timeout=CHECK_REGFILE_TIMEOUT)
        if rtmp:
            run_remote_cmd_capture(ssh, ["bash","-lc", f"rm -rf {shlex.quote(rtmp)} || true"], timeout=CHECK_REGFILE_TIMEOUT)
        return

    # 空ディレクトリ作成 ( 属性は変更しない )
    empty_dir_created: Dict[str, bool] = {}
    for _d in empty_dirs:
        _d_norm = _d.rstrip("/")
        _dst_dir = os.path.join(dest_abs_root, _d_norm)
        # 事前に存在確認して「新規作成か」を判定
        rc_pre, _o_pre, _e_pre = run_remote_cmd_capture(
            ssh, ["bash", "-lc", f"test -d {shlex.quote(_dst_dir)}"], timeout=CHECK_REGFILE_TIMEOUT
        )
        preexists: bool = (rc_pre == 0)
        try:
            remote_mkdir_p(ssh, _dst_dir, use_sudo=sudo_extract)
        except Exception as _ex:
            report.add(
                host,
                TransferItem(host=host, remote_path=_dst_dir, phase="transfer", status="failed", reason=f"E_MKDIR_EMPTY: {str(_ex)}"),
            )
            run_remote_cmd_capture(ssh, ["bash","-lc", f"rm -f {shlex.quote(remote_tar)} || true"], timeout=CHECK_REGFILE_TIMEOUT)
            if rtmp:
                run_remote_cmd_capture(ssh, ["bash","-lc", f"rm -rf {shlex.quote(rtmp)} || true"], timeout=CHECK_REGFILE_TIMEOUT)
            return

        # 新規作成フラグを正規化キーで保持 ( 末尾'/'差異を吸収 )
        empty_dir_created[_d_norm] = (not preexists)

    # 空ディレクトリの所有権調整 ( sudo_extract && target_user の場合 )
    # 新規に作った空ディレクトリのみ chownの対象とする ( 既存ディレクトリは触らない )
    if empty_dirs and sudo_extract and target_user and primary_group:
        for _d in empty_dirs:
            _d_norm = _d.rstrip("/")
            if not empty_dir_created.get(_d_norm, False):
                continue
            _dst_dir = os.path.join(dest_abs_root, _d_norm)
            try:
                # mode=None で chmod は行わない ( umask/既定に任せる )
                chown_chmod(ssh, _dst_dir, owner=target_user, group=primary_group, mode=None, use_sudo=True)
            except Exception as _ex:
                report.add(host, TransferItem(host=host, remote_path=_dst_dir, phase="transfer", status="failed", reason=f"E_CHOWN_EMPTYDIR: {str(_ex)}"))

    # メンバーファイルを作成し, 以下を実施
    #  1) NEW セットのみを DEST に抽出 ( -T list )
    #  2) EXIST セットは別 tmp に抽出  =>  内容のみ既存へ上書き ( 属性は変更しない )

    # NEW セット抽出
    if new_set:
        members_file_new = f"{rtmp}/members.new.txt"
        # list_tar_members_local は相対パス ( アーカイブ内の名前 ) 、
        # LF 終端 ( 最後も LF ) を約束し、内部で CR/LF を正規化して書き出す
        write_members_file(sftp, members_file_new, new_set)
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
            # cleanup は後半でまとめて実施するためここでは実施していない
            return

        rc_n, out_n, err_n = run_remote_cmd_capture(ssh, extract_cmd_new, timeout=EXTRACT_TIMEOUT_NEW)
        if rc_n != 0:
            reason_n: str = (err_n.strip() or out_n.strip() or f"E_EXTRACT_NEW: rc={rc_n}")
            report.add(host, TransferItem(host=host, remote_path=f"{dest_abs_root}/...", phase="transfer", status="failed", reason=reason_n))
            # cleanup は後半でまとめて実施するためここでは実施していない
            return

        # NEW の chown ( sudo 経路のみ ) 。chmod はしない。
        if sudo_extract and target_user:
            # primary group 取得
            rc_g, out_g, _ = run_remote_cmd_capture(ssh, (["bash", "-lc", f"id -gn {shlex.quote(target_user)}"]), timeout=CHECK_REGFILE_TIMEOUT)
            if rc_g == 0:
                primary_group = out_g.strip()
                # -T list で列挙済みの NEW のみ chown  ( リンクはない想定だが -h で安全側に設定する )
                for rel in sorted(new_set):
                    dst_abs_new: str = os.path.join(dest_abs_root, rel)
                    try:
                        # mode=None で chmod は行わないよう指示する (chown のみ実施)
                        chown_chmod(ssh, dst_abs_new, owner=target_user, group=primary_group, mode=None, use_sudo=True)
                    except Exception as _ex:
                        report.add(host, TransferItem(host=host, remote_path=dst_abs_new, phase="transfer", status="failed", reason=f"E_CHOWN_NEW: {str(_ex)}"))

    # EXIST: tmp に抽出して cat > 上書き ( 属性保持 ) 。SFTP とは違い、原則として所有権/モード/ACL/xattr は変化しない想定だが、
    # “復元”を仕様で明示されたため、キャプチャ => 上書き => 復元を実施する。
    if exist_set:
        # まず EXIST のみを別 tmp へ抽出
        rc_mk2, out_mk2, _ = run_remote_cmd_capture(
            ssh, ["bash","-lc","mktemp -d /tmp/gm-exist.XXXXXXXX"], timeout=PREFLIGHT_TEST_TIMEOUT)
        rtmp_exist = (out_mk2.strip() if rc_mk2 == 0 else "/tmp")
        members_file_exist = f"{rtmp}/members.exist.txt"

        # list_tar_members_local は相対パス ( アーカイブ内の名前 ) 、
        # LF 終端 ( 最後も LF ) を約束し、内部で CR/LF を正規化して書き出す
        write_members_file(sftp, members_file_exist, exist_set)

        extract_cmd_exist: List[str] = build_tar_extract_cmd(
            flavor=flavor,
            dest_abs=rtmp_exist,
            tar_gz_path=remote_tar,
            use_sudo=False, # tmpへの展開にsudo不要
            members_file=members_file_exist,
        )
        rc_e, out_e, err_e = run_remote_cmd_capture(ssh, extract_cmd_exist, timeout=EXTRACT_TIMEOUT_EXIST)
        if rc_e != 0:
            reason_e: str = (err_e.strip() or out_e.strip() or f"E_EXTRACT_EXIST_TMP: rc={rc_e}")
            report.add(host, TransferItem(host=host, remote_path=f"{dest_abs_root}/...", phase="transfer", status="failed", reason=reason_e))
            return

        # メタ保存ディレクトリ
        rc_mkm, out_mkm, _ = run_remote_cmd_capture(
            ssh, ["bash","-lc","mktemp -d /tmp/gm-meta.XXXXXXXX"], timeout=PREFLIGHT_TEST_TIMEOUT)
        rtmp_meta = (out_mkm.strip() if rc_mkm == 0 else "/tmp")

        # 抽出確認とメタキャプチャ
        meta_map: Dict[str, Dict[str, Optional[str]]] = {}
        for rel in sorted(exist_set):
            src_tmp_chk: str = os.path.join(rtmp_exist, rel)
            qsrc_chk: str = shlex.quote(src_tmp_chk)
            rc_chk, _o_chk, _e_chk = run_remote_cmd_capture(
                ssh,
                (["bash", "-lc", f"test -f {qsrc_chk} || (echo '__MISSING__'; ls -ld {qsrc_chk} 2>/dev/null || true; echo '__PARENT__'; ls -ld $(dirname {qsrc_chk}) 2>/dev/null || true; exit 1)"]),
                timeout=CHECK_REGFILE_TIMEOUT,
            )
            if rc_chk != 0:
                report.add(
                    host,
                    TransferItem(
                        host=host,
                        remote_path=f"{dest_abs_root}/...",
                        phase="transfer",
                        status="failed",
                        reason=f"E_EXIST_EXTRACT_MISSING: {rel}",
                    ),
                )
                return

            # 上書き対象の現在メタをキャプチャ ( 復元用 )
            dst_abs: str = os.path.join(dest_abs_root, rel)
            owner, group, mode = stat_owner_group_mode(ssh, dst_abs, use_sudo=sudo_extract)
            acl_dump: Optional[str] = capture_acl_dump(ssh, dst_abs, rtmp_meta, use_sudo=sudo_extract) if acl_ok else None
            xat_dump: Optional[str] = capture_xattr_dump(ssh, dst_abs, rtmp_meta, use_sudo=sudo_extract) if xattr_ok else None
            # meta_map は値を str として保持する。
            # mode はゼロ詰め 8進文字列 ( 例: 0o644 -> "0644" ) で保存して曖昧性を排除する。
            meta_map[rel] = {
                "owner": owner,
                "group": group,
                "mode": format(mode & 0o7777, "04o"),
                "acl": acl_dump,
                "xattr": xat_dump,
            }

        # 中身のみ上書きして復元
        for rel in sorted(exist_set):
            src_tmp: str = os.path.join(rtmp_exist, rel)
            dst_abs: str = os.path.join(dest_abs_root, rel)

            # 上書き ( sudo の要否に合わせる ) 。
            # リダイレクトはシェルで解釈させる必要があるため、全体は生文字列で渡し、個々のパスを quote 済みにする
            overwrite_cmd_str: str = f"cat {shlex.quote(src_tmp)} > {shlex.quote(dst_abs)}"
            overwrite_cmd: List[str] = (["sudo","-n"] if sudo_extract else []) + ["bash", "-lc", overwrite_cmd_str]
            rc_w, _o_w, err_w = run_remote_cmd_capture(ssh, overwrite_cmd, timeout=OVERWRITE_TIMEOUT)
            if rc_w != 0:
                report.add(host, TransferItem(host=host, remote_path=dst_abs, phase="transfer", status="failed", reason=(err_w.strip() or "E_OVERWRITE")))
                continue

            # 復元 ( EXIST のみ ) 。sudo が必要。
            if sudo_extract:
                meta = meta_map.get(rel, {})
                try:
                    owner_s: str = str(meta.get("owner", ""))  # 空なら chown は内部でスキップする実装でも可
                    group_s: str = str(meta.get("group", ""))
                    raw_mode: Optional[str] = meta.get("mode")  # "0644" などの 8進文字列
                    if isinstance(raw_mode, str) and raw_mode.strip():
                        try:
                            mode_i: Optional[int] = int(raw_mode, 8)  # 8進 -> int
                        except ValueError:
                            mode_i = None
                    else:
                        mode_i = None
                    chown_chmod(ssh, dst_abs, owner=owner_s, group=group_s, mode=mode_i, use_sudo=True)
                except Exception as _ex:
                    report.add(host, TransferItem(host=host, remote_path=dst_abs, phase="transfer", status="failed", reason=f"E_RESTORE_META: {str(_ex)}"))

                # ACL/xattr 復元 ( ツール存在時のみ )
                acl_dump = meta.get("acl")
                if acl_dump:
                    try:
                        restore_acl_dump(ssh, acl_dump, use_sudo=True)
                    except Exception as _ex:
                        report.add(host, TransferItem(host=host, remote_path=dst_abs, phase="transfer", status="failed", reason=f"E_RESTORE_ACL: {str(_ex)}"))
                x_dump = meta.get("xattr")
                if x_dump:
                    try:
                        restore_xattr_dump(ssh, x_dump, use_sudo=True)
                    except Exception as _ex:
                        report.add(host, TransferItem(host=host, remote_path=dst_abs, phase="transfer", status="failed", reason=f"E_RESTORE_XATTR: {str(_ex)}"))

            report.add(host, TransferItem(host=host, remote_path=dst_abs, phase="transfer", status="done"))

    # NEW ファイルと空ディレクトリの SELinux restorecon ( pack 経路のみ )
    #  - new_set が空でも empty_dirs があれば対象に含める
    #  - ディレクトリも restorecon 対象に含めることで、/etc などのラベル要求にも対応

    if selinux_capable:
        paths_restorecon: List[str] = []
        if new_set:
            # NEWファイル自身
            new_files = [os.path.join(dest_abs_root, rel) for rel in sorted(new_set)]
            paths_restorecon.extend(new_files)
            # 親ディレクトリも対象に追加 ( 重複排除 )
            parent_dirs = {os.path.dirname(p) for p in new_files}
            paths_restorecon.extend(sorted(parent_dirs))
        if empty_dirs:
            # empty_dirs は末尾'/'の有無が混在していても安全化して扱うために,
            # rstrip("/") してから結合する
            # これは, list_tar_members_local の仕様変更時でも(末尾スラッシュなしが
            # 空ディレクトリの判定基準でなくなった場合でも)動作可能なようにするための措置である。
            paths_restorecon.extend(
                os.path.join(dest_abs_root, d.rstrip("/")) for d in sorted(empty_dirs)
            )

        if paths_restorecon:
            try:
                restorecon_recursive_if_needed(
                    ssh=ssh,
                    paths=paths_restorecon,
                    mode=selinux_mode,
                    selinux_capable=selinux_capable,
                    use_sudo=sudo_extract,
                )
            except Exception as _ex:
                # policy で非対応は内部で例外化される契約 ( 全体中断 ) 。auto/ignore は発生しない想定。
                report.add(host, TransferItem(host=host, remote_path=f"{dest_abs_root}/...", phase="transfer", status="failed", reason=f"E_SELINUX_RESTORECON: {str(_ex)}"))
                return

    # すべて成功していれば DEST/... に done を一つ
    report.add(host, TransferItem(host=host, remote_path=f"{dest_abs_root}/...", phase="transfer", status="done"))

    # 後始末
    run_remote_cmd_capture(ssh, ["bash","-lc", f"rm -f {shlex.quote(remote_tar)} || true"], timeout=CHECK_REGFILE_TIMEOUT)

    # tmp は積極削除しなくてもよいが、痕跡を減らす
    if members_file_new is not None:
        run_remote_cmd_capture(ssh, ["bash","-lc", f"rm -f {shlex.quote(members_file_new)} || true"], timeout=CHECK_REGFILE_TIMEOUT)
    if members_file_exist is not None:
        run_remote_cmd_capture(ssh, ["bash","-lc", f"rm -f {shlex.quote(members_file_exist)} || true"], timeout=CHECK_REGFILE_TIMEOUT)
    if rtmp_exist is not None:
        run_remote_cmd_capture(ssh, ["bash","-lc", f"rm -rf {shlex.quote(rtmp_exist)} || true"], timeout=CHECK_REGFILE_TIMEOUT)
    if rtmp_meta is not None:
        run_remote_cmd_capture(ssh, ["bash","-lc", f"rm -rf {shlex.quote(rtmp_meta)} || true"], timeout=CHECK_REGFILE_TIMEOUT)

    # mktemp -d で作成した rtmp 自体も掃除 ( 失敗は無視 )
    run_remote_cmd_capture(ssh, ["bash","-lc", f"rm -rf {shlex.quote(rtmp)} || true"], timeout=CHECK_REGFILE_TIMEOUT)

def sftp_put_one(
    ssh: "paramiko.SSHClient",
    sftp: "paramiko.SFTPClient",
    local_abs: str,
    dest_abs_root: str,
    host: str,
    report: TransferReport,
    dry_run: bool,
    sudo_mkdir: bool = False,
    *,
    # Step4 追加 : EXIST 上書き時の復元に利用
    enable_restore_meta: bool = True,
) -> None:
    """
    単一のファイル ( またはディレクトリ ) を逐次 SFTP で配置する。
    シンボルリンクは無視 ( dropped ) する。
    レイアウト:
        DEST/<local_abs_without_leading_slash>
    """
    # 能力 ( SFTP 経路では毎回チェックしてもよいが、ここで 1 回 )
    acl_ok: bool = check_acl_tools_available(ssh)
    xattr_ok: bool = check_xattr_tools_available(ssh)

    # メタ保存先 ( EXIST で復元に使う )
    rtmp_meta: Optional[str] = None
    if enable_restore_meta and (acl_ok or xattr_ok or sudo_mkdir):
        rc_mkm2, out_mkm2, _ = run_remote_cmd_capture(
            ssh, ["bash","-lc","mktemp -d /tmp/gm-meta.XXXXXXXX"], timeout=PREFLIGHT_TEST_TIMEOUT)
        rtmp_meta = (out_mkm2.strip() if rc_mkm2 == 0 else "/tmp")

    # ローカル入力の末尾'/'正規化 ( / は除外 )
    ap_raw: str = os.path.abspath(local_abs)
    ap: str = _norm_noslash(ap_raw)
    rel: str = ap.lstrip(os.sep)
    # 転送先のベースパス ( 末尾'/'に依存しない )
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
        )
    )
    if dry_run:
        return

    # 親ディレクトリの作成
    # 親 mkdir -p は ssh 経由で実施
    #

    # rpathは正規化済み(末尾'/'なし)のため
    # _as_dirpath で末尾に'/'を付与する
    rdir: str = _as_dirpath(os.path.dirname(rpath))

    # mkdir -p をリモートホストで実行
    # sudo 経路を統一ラッパで実施。失敗時は例外で即中断
    try:
        remote_mkdir_p(ssh, rdir, use_sudo=sudo_mkdir)
    except Exception as _ex:
        report.add(host, TransferItem(host=host, remote_path=dest_abs_root, phase="transfer", status="failed", reason=str(_ex)))
        return

    if os.path.isdir(ap):
        for root, _dirs, files in os.walk(ap):
            # ここで必ず root_str を定義して以降で使用する
            root_str: str = os.path.abspath(root)
            # root_str が ap 自身なら rel のまま、それ以外なら ap からの相対パスを連結
            sub_rel: str = os.path.join(rel, os.path.relpath(root_str, ap)) if root_str != ap else rel
            # ディレクトリとして扱う経路は末尾'/'を強制
            rr: str = _join_dir(dest_abs_root, sub_rel) if sub_rel else _as_dirpath(dest_abs_root)

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
                    # inner_dst はファイルパス ( 末尾'/'なし ) 。rr は dir 末尾'/'保証済み。
                    inner_dst: str = _join_norm(rr, fn)
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
                dst: str = _join_norm(rr, fn)
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
            remote_path=_norm_noslash(rpath),
            host=host,
            report=report,
            sudo_needed=sudo_mkdir,
            acl_ok=acl_ok,
            xattr_ok=xattr_ok,
            rtmp_meta=rtmp_meta,
        )

    # 後始末
    if rtmp_meta is not None:
        run_remote_cmd_capture(ssh, ["bash","-lc", f"rm -rf {shlex.quote(rtmp_meta)} || true"], timeout=CHECK_REGFILE_TIMEOUT)

def _sftp_put_with_exist_restore(
    *,
    ssh: "paramiko.SSHClient",
    sftp: "paramiko.SFTPClient",
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
    - 既存の場合は上書き前に owner/group/mode/ACL/xattr をキャプチャし、上書き後に復元 ( コマンドがある範囲 ) 。
    - 新規の場合はそのまま put ( SFTP 経路では SELinux は非対応・chown/chmod は行わない ) 。
    """
    # 既存判定
    qdst: str = shlex.quote(remote_path)
    rc_ex, _o_ex, _e_ex = run_remote_cmd_capture(ssh, (["bash", "-lc", f"test -e {qdst}"]), timeout=CHECK_REGFILE_TIMEOUT)
    exist_before: bool = (rc_ex == 0)

    # メタの事前キャプチャ ( EXIST のみ )
    owner: Optional[str] = None
    group: Optional[str] = None
    mode: Optional[int] = None
    acl_dump: Optional[str] = None
    xat_dump: Optional[str] = None

    if exist_before and sudo_needed:
        owner, group, mode = stat_owner_group_mode(ssh, remote_path, use_sudo=True)
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
