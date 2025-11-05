# smoke_step3.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import os, sys, getpass, tempfile
import paramiko

# プロジェクトルートを import パスに追加
sys.path.insert(0, os.path.abspath("."))

from gm_tools.core_scatter import (
    ScatterOpts,
    local_pack_paths_to_tmp,
    upload_pack_and_extract,
)
from gm_tools.core_cmd_flavor import exec_remote
from gm_tools.core_archive import list_tar_members_local

HOST = "localhost"
PORT = 22
USER = getpass.getuser()
DEST = "/tmp/gm_dest_step3"
SRC = "/tmp/gm_src_step3"

def ensure_remote_clean(ssh):
    exec_remote(ssh, f"rm -rf {DEST}", use_sudo=False, timeout=30.0)
    rc, out, err = exec_remote(ssh, f"mkdir -p {DEST}", use_sudo=False, timeout=30.0)
    if rc != 0:
        raise RuntimeError(f"prepare DEST failed: {err}")

def ls_remote(ssh, path):
    rc, out, err = exec_remote(ssh, f"bash -lc 'ls -la {path} || true'", use_sudo=False, timeout=30.0)
    print(f"[remote ls] {path}\n{out}")

def ssh_connect():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, PORT, USER)  # 既存の鍵認証を利用
    return c

def _upload_bytes(sftp: "paramiko.SFTPClient", remote_path: str, data: bytes) -> None:
    f = sftp.file(remote_path, mode="wb")
    try:
        f.write(data)
    finally:
        f.close()

def _show_set_diff(title: str, a_name: str, a: set[str], b_name: str, b: set[str]) -> None:
    only_a = sorted(a - b)
    only_b = sorted(b - a)
    print(f"[diff:{title}] {a_name}={len(a)} {b_name}={len(b)}  only_{a_name}-not-{b_name}={len(only_a)}  only_{b_name}-not-{a_name}={len(only_b)}")
    if only_a:
        print(f"  + only in {a_name}:")
        for x in only_a:
            print(f"    - {x}")
    if only_b:
        print(f"  + only in {b_name}:")
        for x in only_b:
            print(f"    - {x}")

def _remote_mktempdir(ssh) -> str:
    rc, out, err = exec_remote(ssh, "mktemp -d /tmp/s3_diag.XXXXXXXX", use_sudo=False, timeout=30.0)
    if rc != 0:
        raise RuntimeError(f"mktemp failed: {err}")
    return out.strip() or "/tmp"

def run_once():
    ssh = ssh_connect()
    sftp = ssh.open_sftp()
    try:
        ensure_remote_clean(ssh)

        # pack 作成 ( ローカル )
        tgz_path, _deref = local_pack_paths_to_tmp([SRC], follow_symlinks=False)
        print(f"[local] created pack: {tgz_path}")

        # --- 追加1: アーカイブのメンバー列挙 ( ローカル )  ---
        rel_files, empty_dirs = list_tar_members_local(tgz_path)
        print("[diag] list_tar_members_local: regular_files:")
        for p in rel_files:
            print("   -", p)
        print("[diag] list_tar_members_local: empty_dirs:")
        for d in empty_dirs:
            print("   -", d)

        # --- 追加2: リモートで tar -tzf の生出力を取得して比較 ---
        rtmp = _remote_mktempdir(ssh)
        rtgz = f"{rtmp}/payload.tar.gz"
        sftp.put(tgz_path, rtgz)

        rc, out, err = exec_remote(ssh, f"tar -tzf {rtgz}", use_sudo=False, timeout=30.0)
        if rc != 0:
            raise RuntimeError(f"tar -tzf failed: {err}")
        listing = [ln.strip() for ln in out.splitlines() if ln.strip()]
        # 末尾の / は除去 ( ディレクトリエントリ )
        tzf_norm = []
        for s in listing:
            if s.endswith("/"):
                s = s[:-1]
            tzf_norm.append(s)
        set_local = set(rel_files)
        set_tzf = set(tzf_norm)
        print(f"[diag] tar -tzf listed {len(set_tzf)} entries (normalized)")
        _show_set_diff("tzf-vs-local", "local", set_local, "tzf", set_tzf)

        # --- 追加3: -T 抽出の直接検証 ( rtmp2 に展開 )  ---
        rtmp2 = _remote_mktempdir(ssh)
        # members ファイルを3種類用意: (A)そのまま, (B) './' 付与, (C) CRLF 終端 ( 悪条件 )
        members_A = "\n".join(sorted(rel_files)) + "\n"
        with_dot = []
        for p in sorted(rel_files):
            # すでに './' で始まらないものにだけ付ける ( 二重防止 )
            with_dot.append(p if p.startswith("./") else f"./{p}")
        members_B = "\n".join(with_dot) + "\n"
        members_C = members_A.replace("\n", "\r\n")

        rA = f"{rtmp}/members.A.txt"
        rB = f"{rtmp}/members.B_dot.txt"
        rC = f"{rtmp}/members.C_crlf.txt"
        _upload_bytes(sftp, rA, members_A.encode("utf-8"))
        _upload_bytes(sftp, rB, members_B.encode("utf-8"))
        _upload_bytes(sftp, rC, members_C.encode("utf-8"))

        # (A) そのまま
        rcA, outA, errA = exec_remote(
            ssh, f"tar -xzf {rtgz} -C {rtmp2} -T {rA}", use_sudo=False, timeout=60.0
        )
        print(f"[diag] extract -T (A raw) rc={rcA}")
        if rcA != 0:
            print(f"[diag]  (A) err: {errA.strip()}")
        # (B) ./ 付与
        rcB, outB, errB = exec_remote(
            ssh, f"tar -xzf {rtgz} -C {rtmp2} -T {rB}", use_sudo=False, timeout=60.0
        )
        print(f"[diag] extract -T (B ./prefix) rc={rcB}")
        if rcB != 0:
            print(f"[diag]  (B) err: {errB.strip()}")
        # (C) CRLF 終端
        rcC, outC, errC = exec_remote(
            ssh, f"tar -xzf {rtgz} -C {rtmp2} -T {rC}", use_sudo=False, timeout=60.0
        )
        print(f"[diag] extract -T (C CRLF) rc={rcC}")
        if rcC != 0:
            print(f"[diag]  (C) err: {errC.strip()}")

        # 抽出結果の可視化 ( どのパスが出力されたかを評価 )
        rc_find, out_find, _ = exec_remote(
            ssh, f"bash -lc 'cd {rtmp2} && find . -type f -o -type d | sort'", use_sudo=False, timeout=30.0
        )
        print(f"[diag] extracted tree under {rtmp2}:\n{out_find}")

        # 期待ファイルが作られているか ( A => OK であるべき )
        # A/B/C どれで作られたかに関わらず, find の結果で存在を判断
        expected = [
            f"{rtmp2}/tmp/gm_src_step3/dirA/a.txt",
            f"{rtmp2}/tmp/gm_src_step3/dirB/sub/b.txt",
        ]
        for p in expected:
            rc_e, _, _ = exec_remote(ssh, f"test -f {p}", use_sudo=False, timeout=10.0)
            print(f"[diag] expect file exists? {p}: {'YES' if rc_e==0 else 'NO'}")

        # --- 既存の本体テスト ( pack 経由の通常動作 )  ---
        opts = ScatterOpts(
            dest_abs_root=DEST,
            pack=True,
            follow_symlinks=False,
            dry_run=False,
            sudo_extract=False,
            ssh_user=USER,
            local_user=USER,
        )
        upload_pack_and_extract(
            ssh=ssh,
            sftp=sftp,
            tar_path=tgz_path,
            dest_abs_root=opts.dest_abs_root,
            sudo_extract=opts.sudo_extract,
            host=HOST,
            report=DummyReport(),
            dry_run=opts.dry_run,
        )

        # 結果確認
        for p in [
            f"{DEST}/tmp/gm_src_step3/dirA/a.txt",
            f"{DEST}/tmp/gm_src_step3/dirB/sub/b.txt",
            f"{DEST}/tmp/gm_src_step3/empty1",      # 空ディレクトリ
            f"{DEST}/tmp/gm_src_step3/empty2/nested" # 空ディレクトリ
        ]:
            rc, out, err = exec_remote(ssh, f"bash -lc 'test -e {p}'", use_sudo=False, timeout=30.0)
            assert rc == 0, f"missing after extract: {p}"

        # シンボリックリンクは「dropped」想定 ( ファイルとしては存在しない )
        for p in [
            f"{DEST}/tmp/gm_src_step3/link_to_file",
            f"{DEST}/tmp/gm_src_step3/link_to_dir",
        ]:
            rc, out, err = exec_remote(ssh, f"bash -lc 'test -e {p}'", use_sudo=False, timeout=30.0)
            assert rc != 0, f"symlink appears (should be dropped): {p}"

        # 内容チェック ( 簡易 )
        rc, out, _ = exec_remote(ssh, f"bash -lc 'cat {DEST}/tmp/gm_src_step3/dirA/a.txt'", use_sudo=False, timeout=30.0)
        assert out.strip() == "hello A", "content mismatch for a.txt"

        print("[OK] first run passed")
        ls_remote(ssh, f"{DEST}/tmp/gm_src_step3")

        # === Overwrite 検証 ===
        # 1回目の後でローカルSRCを更新し, 新しいpackを作って2回目で上書きされることを確認
        rc, _, _ = exec_remote(ssh, f"bash -lc 'echo hello A v2 > {SRC}/dirA/a.txt && echo \"data v2\" > {SRC}/dirB/sub/b.txt'", use_sudo=False, timeout=30.0)
        assert rc == 0, "failed to mutate local SRC for overwrite test"
        tgz_path2, _ = local_pack_paths_to_tmp([SRC], follow_symlinks=False)
        upload_pack_and_extract(
            ssh=ssh,
            sftp=sftp,
            tar_path=tgz_path2,
            dest_abs_root=opts.dest_abs_root,
            sudo_extract=opts.sudo_extract,
            host=HOST,
            report=DummyReport(),
            dry_run=opts.dry_run,
        )

        # 上書き結果の内容確認
        rc, out, _ = exec_remote(ssh, f"bash -lc 'cat {DEST}/tmp/gm_src_step3/dirA/a.txt'", use_sudo=False, timeout=30.0)
        assert out.strip() == "hello A v2", "overwrite content mismatch for a.txt"
        rc, out, _ = exec_remote(ssh, f"bash -lc 'cat {DEST}/tmp/gm_src_step3/dirB/sub/b.txt'", use_sudo=False, timeout=30.0)
        assert out.strip() == "data v2", "overwrite content mismatch for b.txt"
        print("[OK] second run (overwrite verified) passed")

        # 後片付け ( 診断領域 )
        exec_remote(ssh, f"rm -rf {rtmp} {rtmp2}", use_sudo=False, timeout=30.0)

    finally:
        try:
            sftp.close()
        except Exception:
            pass
        ssh.close()

class DummyReport:
    # TransferReport 互換の最小ダミー実装 ( コンソールに流すだけ )
    def add(self, host, item):
        # item は TransferItem 想定。最低限表示して観察。
        try:
            print(f"[report] host={host} phase={getattr(item,'phase',None)} status={getattr(item,'status',None)} path={getattr(item,'remote_path',None)} reason={getattr(item,'reason',None)}")
        except Exception:
            pass

if __name__ == "__main__":
    run_once()
