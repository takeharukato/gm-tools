#!/usr/bin/env python3
# tests/tests_py/runner_step4.py
# Step4 smoke/regression runner（自己完結版）
# - ssh呼び出し順序は「ssh <opts> -- user@host <argv...>」で統一
# - 「~」展開は使わず getent passwd で得た絶対パスを使用
# - gather の SRC 相対解釈（-u のホーム相対）
# - scatter の DEST は「絶対パス推奨」。相対はリモート HOME 基準に解決（dry-run も受理）
# - Ubuntu 側の SELinux=auto は「対応していなければ成功扱いでスキップ」
# - Alma 側は getenforce の結果を報告
from __future__ import annotations

import json
import os
import shutil
import shlex
import subprocess
import tempfile
import fnmatch
import pathlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, IO
from ._local_types import Config as CommonConfig
from .test_common_config import load_config_from_env as load_common_config


def _safe_rmtree_abs(path_abs: str, *, ensure_under: Optional[str] = None) -> None:
    """
    安全な rmtree:
      - 実体がディレクトリであること（シンボリックリンクは拒否）
      - ensure_under が指定されていれば、その配下に限定
    """
    p: str = os.path.abspath(path_abs)
    if ensure_under is not None:
        base: str = os.path.abspath(ensure_under)
        if not (p == base or p.startswith(base + os.sep)):
            return  # 外側は触らない
    if not os.path.exists(p):
        return
    if os.path.islink(p):
        # 誤爆防止のためシンボリックリンクは削除しない
        return
    if os.path.isdir(p):
        shutil.rmtree(p, ignore_errors=True)

def cleanup_local_temps(cfg: Config) -> None:
    """
    テストで作成するローカル一時ディレクトリを削除する。
      - cfg.local_root (= _tmp_test_local/)
      - カレント配下の相対ソース用一時: nf_rel/, nonpack_rel_dir/, sc_layout_rel_src/
    """
    cwd: str = os.getcwd()
    # _tmp_test_local は cwd 配下に作る運用なので、念のため ensure_under=cwd を指定
    _safe_rmtree_abs(cfg.local_root, ensure_under=cwd)
    rel_dirs = ["nf_rel", "nonpack_rel_dir", "sc_layout_rel_src"]
    for d in rel_dirs:
        abs_path: str = os.path.join(cwd, d)
        _safe_rmtree_abs(abs_path, ensure_under=cwd)


def snapshot_scatter_dest_verbose(cfg: Config, host: str, dest_abs: str,
                                  expected_paths: Optional[List[str]] = None) -> Dict[str, str]:
    """
    /tmp/gm_scatter_layout_dest を **絶対パス固定** で観測し、
    迷子を防ぐためのメタ情報も採取する。
    """
    parts:List[str] = []
    # 1) 実行系メタ
    meta_cmd = r'''
set -u
echo "[whoami] $(whoami)"
echo "[pwd]    $(pwd)"
echo "[home]   $HOME"
echo "[uname]  $(uname -a)"
echo "[umask]  $(umask)"
    '''.strip()
    r_meta = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "bash", "-lc", meta_cmd)
    parts.append(r_meta.stdout or "")

    # 2) DEST 自体の解決と stat
    cmd2 = f'''
set -u
DEST={shlex.quote(dest_abs)}
echo "[dest.raw] $DEST"
echo "[dest.realpath] $(realpath -m "$DEST" 2>/dev/null || echo '(no realpath)')"
if [ -e "$DEST" ]; then
  echo "[dest.stat] $(stat -c '%U:%G %a %F' "$DEST" 2>/dev/null || echo '(stat-ng)')"
else
  echo "[dest.stat] (missing)"
fi
    '''.strip()
    r2 = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "bash", "-lc", cmd2)
    parts.append(r2.stdout or "")

    # 3) ツリーと find
    cmd3 = f'''
set -u
DEST={shlex.quote(dest_abs)}
echo "[tree]"
if command -v tree >/dev/null 2>&1; then tree -a "$DEST" || true; else echo "(tree not installed)"; fi
echo "[find]"
find "$DEST" -maxdepth 8 -printf '%y %p -> %l\\n' 2>&1 || true
    '''.strip()
    r3 = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "bash", "-lc", cmd3)
    parts.append(r3.stdout or "")

    # 4) 期待パスの実在性チェック（任意）
    check_block = ""
    if expected_paths:
        q = " ".join(shlex.quote(p) for p in expected_paths)
        cmd4 = f'''
set -u
for P in {q}; do
    test -f "$P"; rc=$?; printf "[check] %s : rc=%d\n" "$P" "$rc"
done
        '''.strip()
        r4 = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "bash", "-lc", cmd4)
        check_block = r4.stdout or ""
        parts.append(check_block)

    out = {
        "meta": parts[0],
        "dest": parts[1],
        "layout": parts[2],
        "checks": check_block,
    }
    return out

# =========================
# 共有ヘルパ（外部モジュール不要）
# =========================

def assert_rc(name: str, rc: int, *, expect_zero: bool = True) -> None:
    """rc を検証（ゼロ期待がデフォルト）"""
    ok: bool = (rc == 0) if expect_zero else (rc != 0)
    if not ok:
        raise AssertionError(f"{name}: expected rc={'0' if expect_zero else '!=0'} but got {rc}")


def _clear_dir(path: str, *, ensure_under: Optional[str] = None) -> None:
    """
    path を一度まるごと消してから作り直す。
    - ensure_under: 指定されたベース配下でしか削除しない安全装置
    - シンボリックリンクの削除は拒否（誤爆防止）
    """
    p: str = os.path.abspath(path)

    if ensure_under is not None:
        base: str = os.path.abspath(ensure_under)
        if not (p == base or p.startswith(base + os.sep)):
            raise AssertionError(f"refuse to clear outside base: {p} (base={base})")

    if os.path.islink(p):
        raise AssertionError(f"refuse to clear symlink path: {p}")

    if os.path.exists(p):
        shutil.rmtree(p)

    os.makedirs(p, exist_ok=True)

def _ssh_base_argv(port: int, strict: bool) -> List[str]:
    """ssh のベース引数（オプションのみ）"""
    argv: List[str] = ["ssh", "-p", str(port), "-o", f"StrictHostKeyChecking={'yes' if strict else 'no'}"]
    return argv


def ssh_do(ssh_user: str, host: str, port: int, strict: bool, *remote_argv: str) -> subprocess.CompletedProcess[str]:
    """
    外側シェルを挟まず、リモートでコマンド＋引数のみ実行。
    形 : ssh <opts> -- user@host <argv...>
    """
    argv_list: List[str] = list(remote_argv)
    argv: List[str] = _ssh_base_argv(port, strict) + ["--", f"{ssh_user}@{host}"] + argv_list
    if os.environ.get("VERBOSE", "0") == "1":
        debug_msg: str = f"[DEBUG] ssh_do: {argv!r}"
        print(debug_msg)
    completed: subprocess.CompletedProcess[str] = subprocess.run(argv, capture_output=True, text=True)
    return completed


def ssh_sudo(ssh_user: str, host: str, port: int, strict: bool, *remote_argv: str) -> subprocess.CompletedProcess[str]:
    """
    sudo -n を付与して実行。
    形 : ssh <opts> -- user@host sudo -n <argv...>
    """
    argv_list: List[str] = list(remote_argv)
    argv: List[str] = _ssh_base_argv(port, strict) + ["--", f"{ssh_user}@{host}", "sudo", "-n"] + argv_list
    if os.environ.get("VERBOSE", "0") == "1":
        debug_msg: str = f"[DEBUG] ssh_sudo: {argv!r}"
        print(debug_msg)
    completed: subprocess.CompletedProcess[str] = subprocess.run(argv, capture_output=True, text=True)
    return completed


def pipe_to_tee(ssh_user: str, host: str, port: int, strict: bool, path: str, *, content: str, sudo: bool) -> subprocess.CompletedProcess[str]:
    """
    標準入力で渡した content をリモートの tee に流し込む。
    形 : ssh <opts> -- user@host [sudo -n] tee -- <path>
    """
    argv: List[str] = _ssh_base_argv(port, strict) + ["--", f"{ssh_user}@{host}"]
    if sudo:
        argv += ["sudo", "-n"]
    argv += ["tee", "--", path]
    if os.environ.get("VERBOSE", "0") == "1":
        printable: List[str] = list(argv)
        debug_msg: str = f"[DEBUG] pipe_to_tee: {printable}  (len={len(argv) - (3 if sudo else 2)})"
        print(debug_msg)
    completed: subprocess.CompletedProcess[str] = subprocess.run(argv, input=content, capture_output=True, text=True)
    return completed


# =========================
# 設定読み込み
# =========================

@dataclass(frozen=True)
class Config:
    ssh_user: str
    target_user: str
    ssh_port: int
    ssh_strict: bool

    remote_dest_root: str
    local_root: str

    hosts_both: List[str]
    host_ubuntu: str
    host_alma: str

    gm_gather_cmd: List[str]
    gm_scatter_cmd: List[str]

    verbose: bool

def _walk_find_first(root: str, *, name: Optional[str] = None,
                     pattern: Optional[str] = None) -> Optional[str]:
    """
    ローカルの出力ツリーを走査し、最初に一致したパスを返す。
    - name: 完全一致名（例: 'l.txt'）
    - pattern: グロブ（例: '**/src/l.txt'）
    戻り値は絶対パス。見つからなければ None。
    """
    root_path: pathlib.Path = pathlib.Path(root)
    if pattern:
        # '**' を含むグロブ探索
        for p in root_path.rglob('*'):
            p: pathlib.Path
            try:
                rel: str = str(p.relative_to(root_path))
            except Exception as _ex:
                _ex: Exception
                rel = str(p)
            if fnmatch.fnmatch(rel, pattern):
                resolved: str = str(p.resolve())
                return resolved
        return None
    if name:
        for p in root_path.rglob(name):
            p: pathlib.Path
            resolved: str = str(p.resolve())
            return resolved
    return None

def load_config_from_env() -> Config:
    """
    Step4 用の Config を環境変数から構築する。

    役割分担:
      - ssh_user / target_user / ssh_port / remote_dest_root /
        gm_gather_cmd / gm_scatter_cmd / local_work_root の解釈は
        test_common_config.load_config_from_env() に委譲する。
      - hosts_both / host_ubuntu / host_alma / verbose の扱いは、
        現行 Step4 の実装と同じロジック・デフォルトを維持する。
      - local_root は絶対パスとして扱う（従来通り）。
    """
    # 共通 Config（tests/tests_py/_local_types.Config）をまず取得。
    # clear_local_root=True により、LOCAL_WORK_ROOT 相当ディレクトリは
    # 一度削除されてから作り直される。
    base_cfg: CommonConfig = load_common_config(clear_local_root=True)

    # Step4 では local_root は絶対パスで扱う
    local_root: str = os.path.abspath(base_cfg.local_work_root)

    # ssh_strict は Step4 では bool で扱うので、"yes"/"no" 文字列から変換
    ssh_strict_env: str = base_cfg.ssh_strict
    ssh_strict: bool = (ssh_strict_env.lower() == "yes")

    # hosts_both / host_ubuntu / host_alma / verbose は
    # これまでの Step4 実装と同じロジックをそのまま維持する
    hosts_both_raw: List[str] = shlex.split(os.environ.get("HOSTS_BOTH", "localhost"))
    hosts_both: List[str] = []
    i: int = 0
    n: int = len(hosts_both_raw)
    while i < n:
        h_item: str = hosts_both_raw[i]
        if h_item:
            hosts_both.append(h_item)
        i += 1

    host_ubuntu: str = os.environ.get("HOST_UBUNTU", "localhost")
    host_alma: str = os.environ.get("HOST_ALMA", "vmlinux4.local")

    # VERBOSE は "0"/"1" で解釈（デフォルト "0"）
    verbose: bool = (os.environ.get("VERBOSE", "0") == "1")

    cfg: Config = Config(
        ssh_user=base_cfg.ssh_user,
        target_user=base_cfg.target_user,
        ssh_port=base_cfg.ssh_port,
        ssh_strict=ssh_strict,
        remote_dest_root=base_cfg.remote_dest_root,
        local_root=local_root,
        hosts_both=hosts_both,
        host_ubuntu=host_ubuntu,
        host_alma=host_alma,
        gm_gather_cmd=base_cfg.gm_gather_cmd,
        gm_scatter_cmd=base_cfg.gm_scatter_cmd,
        verbose=verbose,
    )
    return cfg

def print_env(cfg: Config) -> None:
    msg1: str = f"[env] SSH_USER={cfg.ssh_user} HOSTS_BOTH={' '.join(cfg.hosts_both)}"
    msg2: str = f"[env] GM_GATHER_CMD='{shlex.join(cfg.gm_gather_cmd)}'"
    msg3: str = f"[env] GM_SCATTER_CMD='{shlex.join(cfg.gm_scatter_cmd)}'"
    print(msg1)
    print(msg2)
    print(msg3)


# =========================
# ローカル実行ヘルパ
# =========================

@dataclass(frozen=True)
class LocalRun:
    rc: int
    stdout: str
    stderr: str

def _as_posix_rel(path_abs: str) -> str:
    """
    絶対パスをリモート展開用の相対表記へ正規化する:
      - OS 区切りを '/' に統一
      - 先頭の '/' はすべて除去
      - 末尾のスラッシュ有無は入力を尊重（存在すれば保持）
        例:
          '/tmp/a/b/'        -> 'tmp/a/b/'
          'C:\\work\\x\\y'   -> 'C/work/x/y'
    """
    s0: str = path_abs.replace("\\", "/")
    had_trailing: bool = s0.endswith("/")
    s: str = s0.lstrip("/")
    if had_trailing and not s.endswith("/"):
        s = s + "/"
    return s

def _run_local_argv(argv: List[str], *, input_text: Optional[str] = None) -> LocalRun:
    if os.environ.get("VERBOSE", "0") == "1":
        dbg: str = f"[DEBUG] _run_local_argv argv: {shlex.join(argv)}"
        print(dbg)
    p: subprocess.CompletedProcess[str] = subprocess.run(argv, input=input_text, capture_output=True, text=True)
    run: LocalRun = LocalRun(p.returncode, p.stdout, p.stderr)
    return run


def _write_temp_hosts(hosts: List[str]) -> str:
    fd: int
    path: str
    fd, path = tempfile.mkstemp(prefix="hosts_", text=True)
    os.close(fd)
    f: IO[str]
    with open(path, "w", encoding="utf-8") as f:
        i: int = 0
        m: int = len(hosts)
        while i < m:
            h: str = hosts[i]
            _ = f.write(h + "\n")
            i += 1
    return path


# =========================
# 前処理と素材作成
# =========================

def _get_remote_home(cfg: Config, host: str, user: str) -> str:
    r: subprocess.CompletedProcess[str] = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "getent", "passwd", user)
    assert_rc(f"{host}: getent passwd {user}", r.returncode, expect_zero=True)
    line: str = (r.stdout or "").splitlines()[0]
    parts: List[str] = line.strip().split(":")
    if len(parts) < 6:
        raise AssertionError(f"{host}: invalid passwd entry for {user}: {line!r}")
    home: str = parts[5]
    if not home.startswith("/"):
        raise AssertionError(f"{host}: bad home path for {user}: {home!r}")
    return home


def _prepare_remote_sample_tree(cfg: Config, host: str, user: str, rel_root_name: str) -> None:
    """
    <user のホーム>/rel_root_name/src に a.txt と dir1/b.txt を作る。
    """
    home: str = _get_remote_home(cfg, host, user)
    abs_root: str = os.path.join(home, rel_root_name)
    src_dir: str = os.path.join(abs_root, "src")
    dir1: str = os.path.join(src_dir, "dir1")

    r1: subprocess.CompletedProcess[str] = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "mkdir", "-p", "--", src_dir)
    assert_rc(f"{host}: mkdir -p {src_dir}", r1.returncode, expect_zero=True)
    r2: subprocess.CompletedProcess[str] = ssh_sudo(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "chown", "-R", "--", f"{user}:{user}", abs_root)
    assert_rc(f"{host}: chown -R {user}:{user} {abs_root}", r2.returncode, expect_zero=True)
    r3: subprocess.CompletedProcess[str] = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "mkdir", "-p", "--", dir1)
    assert_rc(f"{host}: mkdir -p {dir1}", r3.returncode, expect_zero=True)
    r4: subprocess.CompletedProcess[str] = ssh_sudo(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "chown", "-R", "--", f"{user}:{user}", dir1)
    assert_rc(f"{host}: chown -R {user}:{user} {dir1}", r4.returncode, expect_zero=True)

    a_txt: str = os.path.join(src_dir, "a.txt")
    b_txt: str = os.path.join(dir1, "b.txt")
    r5: subprocess.CompletedProcess[str] = pipe_to_tee(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, a_txt, content="A\n", sudo=False)
    assert_rc(f"{host}: tee {a_txt}", r5.returncode, expect_zero=True)
    r6: subprocess.CompletedProcess[str] = pipe_to_tee(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, b_txt, content="B\n", sudo=False)
    assert_rc(f"{host}: tee {b_txt}", r6.returncode, expect_zero=True)


def _preflight(cfg: Config) -> None:
    """
    sudo/NOPASSWD チェックと作業領域の準備、/tmp/gm_pack_case の生成（pack ケース用）。
    """
    i: int = 0
    n: int = len(cfg.hosts_both)
    while i < n:
        h: str = cfg.hosts_both[i]
        r: subprocess.CompletedProcess[str] = ssh_do(cfg.ssh_user, h, cfg.ssh_port, cfg.ssh_strict, "sudo", "-V")
        assert_rc(f"{h}: sudo present", r.returncode, expect_zero=True)

        r2: subprocess.CompletedProcess[str] = ssh_sudo(cfg.ssh_user, h, cfg.ssh_port, cfg.ssh_strict, "true")
        assert_rc(f"{h}: sudo -n true", r2.returncode, expect_zero=True)

        r3: subprocess.CompletedProcess[str] = ssh_sudo(cfg.ssh_user, h, cfg.ssh_port, cfg.ssh_strict, "mkdir", "-p", "--", cfg.remote_dest_root)
        assert_rc(f"{h}: ensure remote_dest_root", r3.returncode, expect_zero=True)

        r4: subprocess.CompletedProcess[str] = ssh_sudo(
            cfg.ssh_user, h, cfg.ssh_port, cfg.ssh_strict,
            "chown", "-R", "--", f"{cfg.target_user}:{cfg.target_user}", cfg.remote_dest_root
        )
        assert_rc(f"{h}: chown remote_dest_root", r4.returncode, expect_zero=True)
        i += 1

    i2: int = 0
    n2: int = len(cfg.hosts_both)
    while i2 < n2:
        h2: str = cfg.hosts_both[i2]
        _ = ssh_sudo(cfg.ssh_user, h2, cfg.ssh_port, cfg.ssh_strict, "rm", "-rf", "--", "/tmp/gm_pack_case")
        r1: subprocess.CompletedProcess[str] = ssh_sudo(cfg.ssh_user, h2, cfg.ssh_port, cfg.ssh_strict, "mkdir", "-p", "--", "/tmp/gm_pack_case")
        assert_rc(f"{h2}: mkdir pack_case", r1.returncode, expect_zero=True)
        r2: subprocess.CompletedProcess[str] = pipe_to_tee(
            cfg.ssh_user, h2, cfg.ssh_port, cfg.ssh_strict,
            "/tmp/gm_pack_case/secret.txt", content="secret\n", sudo=True
        )
        assert_rc(f"{h2}: create secret.txt", r2.returncode, expect_zero=True)
        r3: subprocess.CompletedProcess[str] = ssh_sudo(
            cfg.ssh_user, h2, cfg.ssh_port, cfg.ssh_strict,
            "ln", "-sf", "--", "/tmp/gm_pack_case/secret.txt", "/tmp/gm_pack_case/secret.link"
        )
        assert_rc(f"{h2}: ln secret.link", r3.returncode, expect_zero=True)
        i2 += 1


# =========================
# SELinux 検出
# =========================

def is_selinux_available(cfg: Config, host: str) -> Tuple[bool, str]:
    """
    SELinux 可否とモード:
      - getenforce が失敗 or 空文字 or "Disabled": (False, "")
      - それ以外（Permissive/Enforcing）: (True, mode)
    """
    r: subprocess.CompletedProcess[str] = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "getenforce")
    if r.returncode != 0:
        return (False, "")
    mode: str = (r.stdout or "").strip()
    if not mode or mode.lower() == "disabled":
        return (False, "")
    return (True, mode)


def case_selinux_auto_ubuntu_skip(cfg: Config) -> Dict[str, object]:
    """
    Ubuntu 側（SELinux 非対応）で --selinux auto を指定した scatter の dry-run を成功として扱い、
    ただし「対応していないため skip」判定を併記する。
    """
    name: str = "selinux_auto_ubuntu_skip"
    available: Tuple[bool, str]
    available = is_selinux_available(cfg, cfg.host_ubuntu)
    empty_src_dir: str = os.path.join(cfg.local_root, "empty_src")
    _ = _clear_dir(empty_src_dir, ensure_under=cfg.local_root)

    hosts_tmp: str = _write_temp_hosts([cfg.host_ubuntu])
    dest: str = os.path.join(cfg.remote_dest_root, "gm_step4_selinux_skip")
    argv: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_tmp, "-u", cfg.target_user]
        + ["-n"]
        + (["-v"] if cfg.verbose else [])
        + ["--selinux", "auto", "--", empty_src_dir, dest]
    )
    run: LocalRun = _run_local_argv(argv)
    ok: bool = (run.rc == 0)

    return {
        "name": name,
        "passed": ok,
        "skipped": (not available[0]),
        "reason": "" if ok else (run.stderr or "").strip(),
        "details": {},
    }


def case_selinux_mode_alma(cfg: Config) -> Dict[str, object]:
    """
    AlmaLinux 側で getenforce のモードを報告する。
    """
    name: str = "selinux_mode_alma"
    available: Tuple[bool, str] = is_selinux_available(cfg, cfg.host_alma)
    ok: bool = available[0] and (available[1] != "")
    return {
        "name": name,
        "passed": ok,
        "skipped": False,
        "reason": "" if ok else "getenforce not available",
        "details": {"mode": available[1]},
    }


# =========================
# パス解釈（roundtrip）ケース
# =========================

def case_path_semantics(cfg: Config) -> Dict[str, object]:
    """
    gather: (Ubuntu) ~/<rel_root>/src (ターゲットユーザのホームディレクトリ絶対パス)→ ローカル
    scatter: (Alma)   ローカル → /tmp/gm_step4_dest_round（絶対パス）
    いずれも --follow-symlinks と -n（dry-run）で Plan の生成だけを確認。
    """
    name: str = "path_semantics_roundtrip"

    rel_root: str = "gm_step4_rel"
    _ = _prepare_remote_sample_tree(cfg, cfg.host_ubuntu, cfg.target_user, rel_root)

    local_rel_out: str = os.path.join(cfg.local_root, "g_rel")
    _ = _clear_dir(local_rel_out, ensure_under=cfg.local_root)
    hosts_gather: str = _write_temp_hosts([cfg.host_ubuntu])
    argv_g: List[str] = (
        cfg.gm_gather_cmd
        + ["-H", hosts_gather, "-u", cfg.target_user]
        + ["-n"]
        + (["-v"] if cfg.verbose else [])
        + ["--follow-symlinks", "--", f"~/{rel_root}/src", local_rel_out]
    )
    run_g: LocalRun = _run_local_argv(argv_g)
    ok_g: bool = (run_g.rc == 0)

    scatter_dest: str = "/tmp/gm_step4_dest_round"
    hosts_scatter: str = _write_temp_hosts([cfg.host_alma])
    argv_s: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_scatter, "-u", cfg.target_user]
        + ["-n"]
        + (["-v"] if cfg.verbose else [])
        + ["--follow-symlinks", "--", local_rel_out, scatter_dest]
    )
    run_s: LocalRun = _run_local_argv(argv_s)
    ok_s: bool = (run_s.rc == 0)

    passed: bool = ok_g and ok_s
    reason: str = "" if passed else f"gather_rc={run_g.rc}, scatter_rc={run_s.rc}"

    details: Dict[str, object] = {
        "gather": {"rc": run_g.rc, "stdout": run_g.stdout, "stderr": run_g.stderr},
        "scatter": {"rc": run_s.rc, "stdout": run_s.stdout, "stderr": run_s.stderr},
    }
    return {
        "name": name,
        "passed": passed,
        "skipped": False,
        "reason": reason,
        "details": details,
    }

# ---------------------------------------------------------------------------
# 1) gather の SRC が「/」始まりの絶対パスで受理される（dry-run）
# ---------------------------------------------------------------------------

def case_gather_src_abs_slash_ok(cfg: Config) -> Dict[str, object]:
    name: str = "gather_src_abs_slash_ok"
    ubuntu: str = cfg.host_ubuntu
    user: str = cfg.target_user

    home: str = _get_remote_home(cfg, ubuntu, user)
    abs_root: str = os.path.join(home, "gm_step4_abs")
    src_dir: str = os.path.join(abs_root, "src")
    _ = ssh_do(cfg.ssh_user, ubuntu, cfg.ssh_port, cfg.ssh_strict, "mkdir", "-p", "--", src_dir)
    _ = ssh_sudo(cfg.ssh_user, ubuntu, cfg.ssh_port, cfg.ssh_strict, "chown", "-R", "--", f"{user}:{user}", abs_root)
    _ = pipe_to_tee(cfg.ssh_user, ubuntu, cfg.ssh_port, cfg.ssh_strict, os.path.join(src_dir, "a.txt"), content="A\n", sudo=False)

    hosts_path: str = _write_temp_hosts([ubuntu])
    local_out: str = os.path.join(cfg.local_root, "g_abs_slash")
    _ = _clear_dir(local_out, ensure_under=cfg.local_root)

    argv: List[str] = (
        cfg.gm_gather_cmd
        + ["-H", hosts_path, "-u", user, "-n"]
        + (["-v"] if cfg.verbose else [])
        + ["--follow-symlinks", "--", src_dir, local_out]
    )
    run: LocalRun = _run_local_argv(argv)
    ok: bool = (run.rc == 0)

    return {
        "name": name,
        "passed": ok,
        "skipped": False,
        "reason": "" if ok else run.stderr.strip(),
        "details": {"rc": run.rc, "stdout": run.stdout, "stderr": run.stderr},
    }


# ---------------------------------------------------------------------------
# 2) gather の SRC が「~/...」で受理され、ホームに展開される（dry-run）
# ---------------------------------------------------------------------------

def case_gather_src_abs_tilde_ok(cfg: Config) -> Dict[str, object]:
    name: str = "gather_src_abs_tilde_ok"
    ubuntu: str = cfg.host_ubuntu
    user: str = cfg.target_user

    home: str = _get_remote_home(cfg, ubuntu, user)
    abs_root: str = os.path.join(home, "gm_step4_tilde")
    src_dir: str = os.path.join(abs_root, "src")
    _ = ssh_do(cfg.ssh_user, ubuntu, cfg.ssh_port, cfg.ssh_strict, "mkdir", "-p", "--", src_dir)
    _ = ssh_sudo(cfg.ssh_user, ubuntu, cfg.ssh_port, cfg.ssh_strict, "chown", "-R", "--", f"{user}:{user}", abs_root)
    _ = pipe_to_tee(cfg.ssh_user, ubuntu, cfg.ssh_port, cfg.ssh_strict, os.path.join(src_dir, "b.txt"), content="B\n", sudo=False)

    hosts_path: str = _write_temp_hosts([ubuntu])
    local_out: str = os.path.join(cfg.local_root, "g_abs_tilde")
    _ = _clear_dir(local_out, ensure_under=cfg.local_root)

    tilde_src: str = f"~/gm_step4_tilde/src"
    argv: List[str] = (
        cfg.gm_gather_cmd
        + ["-H", hosts_path, "-u", user, "-n"]
        + (["-v"] if cfg.verbose else [])
        + ["--follow-symlinks", "--", tilde_src, local_out]
    )
    run: LocalRun = _run_local_argv(argv)
    ok: bool = (run.rc == 0)

    return {
        "name": name,
        "passed": ok,
        "skipped": False,
        "reason": "" if ok else run.stderr.strip(),
        "details": {"rc": run.rc, "stdout": run.stdout, "stderr": run.stderr},
    }


# ---------------------------------------------------------------------------
# 3) gather の SRC が 相対パスの場合、-u のホーム相対として解釈される
# ---------------------------------------------------------------------------

def case_gather_src_rel_home_ok(cfg: Config) -> Dict[str, object]:
    """
    仕様: 相対SRCは -u のリモートHOME基準で受理される（HOME逸脱不可）。
    dry-runで rc==0 を確認する。
    """
    name: str = "gather_src_rel_home_ok"
    ubuntu: str = cfg.host_ubuntu
    user: str = cfg.target_user

    _ = _prepare_remote_sample_tree(cfg, ubuntu, user, "gm_step4_rel_home")

    hosts_path: str = _write_temp_hosts([ubuntu])
    local_out: str = os.path.join(cfg.local_root, "g_rel_ok")
    _ = _clear_dir(local_out, ensure_under=cfg.local_root)

    rel_src: str = "gm_step4_rel_home/src"
    argv: List[str] = (
        cfg.gm_gather_cmd
        + ["-H", hosts_path, "-u", user, "-n"]
        + (["-v"] if cfg.verbose else [])
        + ["--follow-symlinks", "--", rel_src, local_out]
    )
    run: LocalRun = _run_local_argv(argv)
    passed: bool = (run.rc == 0)
    reason: str = "" if passed else f"rc={run.rc}"

    return {
        "name": name,
        "passed": passed,
        "skipped": False,
        "reason": reason,
        "details": {"rc": run.rc, "stdout": run.stdout, "stderr": run.stderr, "argv": " ".join(argv)},
    }

# ---------------------------------------------------------------------------
# 4) gather の SRC が ~user/...（他人のホーム相対）はエラー
# ---------------------------------------------------------------------------

def case_gather_src_tilde_user_error(cfg: Config) -> Dict[str, object]:
    name: str = "gather_src_tilde_user_error"
    ubuntu: str = cfg.host_ubuntu
    user: str = cfg.target_user

    hosts_path: str = _write_temp_hosts([ubuntu])
    local_out: str = os.path.join(cfg.local_root, "g_tilde_user_err")
    _ = _clear_dir(local_out, ensure_under=cfg.local_root)

    other_user: str = "root" if user != "root" else "nobody"
    src_bad: str = f"~{other_user}/some/where"
    argv: List[str] = (
        cfg.gm_gather_cmd
        + ["-H", hosts_path, "-u", user, "-n"]
        + (["-v"] if cfg.verbose else [])
        + ["--follow-symlinks", "--", src_bad, local_out]
    )
    run: LocalRun = _run_local_argv(argv)
    ok: bool = (run.rc != 0)

    return {
        "name": name,
        "passed": ok,
        "skipped": False,
        "reason": "" if ok else "gather accepted ~user unexpectedly",
        "details": {"rc": run.rc, "stdout": run.stdout, "stderr": run.stderr},
    }


# ---------------------------------------------------------------------------
# 5) scatter の DEST 相対→remote_home 展開（--pack / 非dry-run）
# ---------------------------------------------------------------------------

def case_scatter_dest_relative_ok_to_home(cfg: Config) -> Dict[str, object]:
    """
    目的:
      scatter の DEST が相対指定のとき、ターゲットユーザの remote_home 配下へ
      解決されることを検証（--pack）。
    仕様整合:
      - DEST="relative/dest" の場合、実体は
         <remote_home>/relative/dest/<abs_without_leading_of_local_src>/x.txt
        に作成される。
    """
    name: str = "scatter_dest_relative_ok_to_home"
    alma: str = cfg.host_alma
    user: str = cfg.target_user

    local_src: str = os.path.join(cfg.local_root, "s_dest_rel_src")
    _ = _clear_dir(local_src, ensure_under=cfg.local_root)
    wf: IO[str]
    with open(os.path.join(local_src, "x.txt"), "w", encoding="utf-8") as wf:
        _ = wf.write("X\n")
    abs_local_src_rel: str = _as_posix_rel(os.path.abspath(local_src))

    home: str = _get_remote_home(cfg, alma, user)
    dest_rel: str = "relative/dest"
    expected_remote_x: str = os.path.join(home, dest_rel, abs_local_src_rel, "x.txt")

    _ = ssh_sudo(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "rm", "-rf", "--",
                 os.path.join(home, dest_rel))

    hosts_path: str = _write_temp_hosts([alma])

    argv: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "--pack"]
        + (["-v"] if cfg.verbose else [])
        + ["--follow-symlinks", "--", local_src, dest_rel]
    )
    run: LocalRun = _run_local_argv(argv)

    r_isfile: subprocess.CompletedProcess[str] = ssh_do(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "test", "-f", expected_remote_x)
    r_cat: subprocess.CompletedProcess[str] = ssh_do(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "cat", expected_remote_x)

    passed: bool = (run.rc == 0 and r_isfile.returncode == 0 and (r_cat.stdout or "").strip() == "X")
    reason: str = "" if passed else (
        f"rc={run.rc}, isfile_rc={r_isfile.returncode}, exp={expected_remote_x!r}, content={r_cat.stdout!r}"
    )

    return {
        "name": name,
        "passed": passed,
        "skipped": False,
        "reason": reason,
        "details": {
            "rc": run.rc,
            "expected_remote_x": expected_remote_x,
            "argv": " ".join(shlex.quote(a) for a in argv),
        },
    }


# ---------------------------------------------------------------------------
# 6) scatter の DEST 絶対/~/Windows 変種（--pack / 非dry-run）
# ---------------------------------------------------------------------------

def case_scatter_dest_abs_variants(cfg: Config) -> Dict[str, object]:
    """
    仕様整合チェック:
      - /abs         : 絶対パス -> そのまま DEST 配下に展開される
      - ~/...        : remote_home へ展開される
      - Windows 風   : “Windows 絶対”として扱われ rc==0 となる（レイアウトは実装依存）
    検証方針:
      - /abs と ~/: 期待パスに x.txt があることまで確認
      - Windows 風: rc==0 のみ確認（実装依存のため位置は検証しない）
    """
    name: str = "scatter_dest_abs_variants"
    alma: str = cfg.host_alma
    user: str = cfg.target_user

    local_src: str = os.path.join(cfg.local_root, "s_abs_variants_src")
    _ = _clear_dir(local_src, ensure_under=cfg.local_root)
    wf: IO[str]
    with open(os.path.join(local_src, "x.txt"), "w", encoding="utf-8") as wf:
        _ = wf.write("X\n")
    abs_local_rel: str = _as_posix_rel(os.path.abspath(local_src))

    hosts_path: str = _write_temp_hosts([alma])

    dest_ok: str = os.path.join(cfg.remote_dest_root, "dest_abs_ok")
    dest_tilde: str = "~/dest_tilde"
    dest_win: str = "C:\\dest_win"

    argv_ok: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "--pack"]
        + (["-v"] if cfg.verbose else [])
        + ["--", local_src, dest_ok]
    )
    run_ok: LocalRun = _run_local_argv(argv_ok)
    exp_ok: str = os.path.join(dest_ok, abs_local_rel, "x.txt")
    r_ok_isfile: subprocess.CompletedProcess[str] = ssh_do(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "test", "-f", exp_ok)

    argv_tilde: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "--pack"]
        + (["-v"] if cfg.verbose else [])
        + ["--", local_src, dest_tilde]
    )
    run_tilde: LocalRun = _run_local_argv(argv_tilde)
    home: str = _get_remote_home(cfg, alma, user)
    exp_tilde: str = os.path.join(home, "dest_tilde", abs_local_rel, "x.txt")
    r_tilde_isfile: subprocess.CompletedProcess[str] = ssh_do(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "test", "-f", exp_tilde)

    argv_win: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "--pack"]
        + (["-v"] if cfg.verbose else [])
        + ["--", local_src, dest_win]
    )
    run_win: LocalRun = _run_local_argv(argv_win)

    ok_branch: bool = (run_ok.rc == 0 and r_ok_isfile.returncode == 0)
    tilde_branch: bool = (run_tilde.rc == 0 and r_tilde_isfile.returncode == 0)
    win_branch_rc_only: bool = (run_win.rc == 0)

    passed: bool = (ok_branch and tilde_branch and win_branch_rc_only)
    reason: str = "" if passed else (
        f"/abs(rc={run_ok.rc}, isfile_rc={r_ok_isfile.returncode}, exp_ok={exp_ok}); "
        f"~/ (rc={run_tilde.rc}, isfile_rc={r_tilde_isfile.returncode}, exp_tilde={exp_tilde}); "
        f"win(rc={run_win.rc})"
    )

    return {
        "name": name,
        "passed": passed,
        "skipped": False,
        "reason": reason,
        "details": {
            "dest_ok_rc": run_ok.rc,
            "dest_tilde_rc": run_tilde.rc,
            "dest_win_rc": run_win.rc,
            "exp_ok": exp_ok,
            "exp_tilde": exp_tilde,
            "stdout_ok": run_ok.stdout, "stderr_ok": run_ok.stderr,
            "stdout_tilde": run_tilde.stdout, "stderr_tilde": run_tilde.stderr,
            "stdout_win": run_win.stdout, "stderr_win": run_win.stderr,
        },
    }

# ---------------------------------------------------------------------------
# 7) gather --follow-symlinks 有無で結果差（非dry-run）
# ---------------------------------------------------------------------------

def case_gather_follow_symlinks_files(cfg: Config) -> Dict[str, object]:
    """
    仕様:
      --pack 時のみ有効。
      non-follow: src/f.txt は収集され、src/l.txt は収集されない。
      follow    : シンボリックリンク名（l.txt）のまま**通常ファイル**として収集され、
                  内容はリンク先（実体）と同一の "Z\n" であること。

    検査:
      - non-follow 側: src/f.txt が存在, src/l.txt が不在
      - follow 側    : src/l.txt が**通常ファイル**として存在し、その内容が "Z\n"

    追加採取:
      out_no / out_yes のレイアウトを `find` / `tree -a` で採取し details に格納
    """

    name: str = "gather_follow_symlinks_files"
    ubuntu: str = cfg.host_ubuntu
    user: str = cfg.target_user

    home: str = _get_remote_home(cfg, ubuntu, user)
    abs_root: str = os.path.join(home, "gm_step4_follow_src")
    src_dir: str = os.path.join(abs_root, "src")
    src_dir = src_dir.rstrip('/') + '/'
    file_path: str = os.path.join(src_dir, "f.txt")
    link_path: str = os.path.join(src_dir, "l.txt")

    _ = ssh_do(cfg.ssh_user, ubuntu, cfg.ssh_port, cfg.ssh_strict, "mkdir", "-p", "--", src_dir)
    _ = ssh_sudo(cfg.ssh_user, ubuntu, cfg.ssh_port, cfg.ssh_strict, "chown", "-R", "--", f"{user}:{user}", abs_root)
    _ = pipe_to_tee(cfg.ssh_user, ubuntu, cfg.ssh_port, cfg.ssh_strict, file_path, content="Z\n", sudo=False)
    _ = ssh_do(cfg.ssh_user, ubuntu, cfg.ssh_port, cfg.ssh_strict, "ln", "-sf", "--", "f.txt", link_path)

    hosts_path: str = _write_temp_hosts([ubuntu])

    out_no: str = os.path.join(cfg.local_root, "g_follow_no")
    out_yes: str = os.path.join(cfg.local_root, "g_follow_yes")
    _ = _clear_dir(out_no, ensure_under=cfg.local_root)
    _ = _clear_dir(out_yes, ensure_under=cfg.local_root)

    argv_no: List[str] = (
        cfg.gm_gather_cmd
        + ["-H", hosts_path, "-u", user, "--pack"]
        + (["-v"] if cfg.verbose else [])
        + ["--", src_dir, out_no]
    )
    run_no: LocalRun = _run_local_argv(argv_no)

    argv_yes: List[str] = (
        cfg.gm_gather_cmd
        + ["-H", hosts_path, "-u", user, "--pack", "--follow-symlinks"]
        + (["-v"] if cfg.verbose else [])
        + ["--", src_dir, out_yes]
    )
    run_yes: LocalRun = _run_local_argv(argv_yes)

    def _snapshot(path_dir: str) -> Dict[str, str]:
        snap: Dict[str, str] = {}
        cmd_find: str = f'find {shlex.quote(path_dir)} -maxdepth 6 -printf "%y %p -> %l\\n"'
        p1: subprocess.CompletedProcess[str] = subprocess.run(["bash", "-lc", cmd_find], capture_output=True, text=True)
        snap["find"] = (p1.stdout or "") + (("\n[find-err]\n" + p1.stderr) if p1.stderr else "")
        cmd_tree: str = f'tree -a {shlex.quote(path_dir)}'
        p2: subprocess.CompletedProcess[str] = subprocess.run(["bash", "-lc", cmd_tree], capture_output=True, text=True)
        tree_out: str = p2.stdout or ""
        if p2.returncode != 0 and not tree_out:
            tree_out = "(tree not available or failed)"
        snap["tree"] = tree_out
        return snap

    snap_no: Dict[str, str] = _snapshot(out_no)
    snap_yes: Dict[str, str] = _snapshot(out_yes)

    def _find_first(base: str, patterns: List[str]) -> Optional[str]:
        idx: int = 0
        total: int = len(patterns)
        while idx < total:
            pat: str = patterns[idx]
            p: Optional[str] = _walk_find_first(base, pattern=pat)
            if p:
                return p
            idx += 1
        return None

    patterns_f: List[str] = ["**/src/f.txt"]
    patterns_l: List[str] = ["**/src/l.txt"]

    f_no: Optional[str] = _find_first(out_no, patterns_f)
    l_no: Optional[str] = _find_first(out_no, patterns_l)
    f_yes: Optional[str] = _find_first(out_yes, patterns_f)
    l_yes: Optional[str] = _find_first(out_yes, patterns_l)

    non_follow_ok: bool = (f_no is not None) and (l_no is None)

    follow_ok: bool = False
    found_name: Optional[str] = None
    found_content: Optional[str] = None
    if l_yes and os.path.isfile(l_yes) and not os.path.islink(l_yes):
        try:
            rf: IO[str]
            with open(l_yes, "r", encoding="utf-8") as rf:
                found_content = rf.read()
            follow_ok = (found_content == "Z\n")
            found_name = os.path.basename(l_yes)
        except Exception as _e:
            _e: Exception
            follow_ok = False

    passed: bool = (run_no.rc == 0) and (run_yes.rc == 0) and non_follow_ok and follow_ok
    reason: str = "" if passed else (
        f"no(rc={run_no.rc}, f_present={f_no is not None}, l_absent={l_no is None}); "
        f"yes(rc={run_yes.rc}, l_path={l_yes!r}, "
        f"l_is_file={bool(l_yes and os.path.isfile(l_yes))}, "
        f"l_is_symlink={bool(l_yes and os.path.islink(l_yes))}, "
        f"name={found_name!r}, content={found_content!r})"
    )

    return {
        "name": name,
        "passed": passed,
        "skipped": False,
        "reason": reason,
        "details": {
            "no_rc": run_no.rc, "yes_rc": run_yes.rc,
            "found_no_f": f_no, "found_no_l": l_no,
            "found_yes_f": f_yes, "found_yes_l": l_yes,
            "found_yes_name": found_name,
            "found_yes_content": found_content,
            "snapshot_no_find": snap_no.get("find", ""), "snapshot_no_tree": snap_no.get("tree", ""),
            "snapshot_yes_find": snap_yes.get("find", ""), "snapshot_yes_tree": snap_yes.get("tree", ""),
            "argv_no": " ".join(shlex.quote(a) for a in argv_no),
            "argv_yes": " ".join(shlex.quote(a) for a in argv_yes),
        },
    }


# 8) scatter --follow-symlinks 有無で結果差（--pack + 展開動作）

def case_scatter_follow_symlinks_files(cfg: Config) -> Dict[str, object]:
    """
    仕様:
      non-follow: シンボリックリンクは展開しない（= l.txt は作られない）
      follow    : リンクの実体を展開（l.txt は通常ファイル、内容は "Q\n"）

    検査:
      - non-follow 側: DEST/.../l.txt が不在（test -e が失敗）
      - follow 側    : DEST/.../l.txt が通常ファイルで "Q\n"
    """
    name: str = "scatter_follow_symlinks_files"
    alma: str = cfg.host_alma
    user: str = cfg.target_user

    local_src: str = os.path.join(cfg.local_root, "s_follow_src")
    _ = _clear_dir(local_src, ensure_under=cfg.local_root)
    file_local: str = os.path.join(local_src, "f.txt")
    link_local: str = os.path.join(local_src, "l.txt")
    wf: IO[str]
    with open(file_local, "w", encoding="utf-8") as wf:
        _ = wf.write("Q\n")
    if os.path.lexists(link_local):
        os.unlink(link_local)
    os.symlink("f.txt", link_local)

    abs_local_src: str = _as_posix_rel(os.path.abspath(local_src))

    dest_no: str = os.path.join(cfg.remote_dest_root, "s_follow_no")
    dest_yes: str = os.path.join(cfg.remote_dest_root, "s_follow_yes")
    _ = ssh_sudo(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "rm", "-rf", "--", dest_no, dest_yes)
    _ = ssh_sudo(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "mkdir", "-p", "--", dest_no, dest_yes)
    _ = ssh_sudo(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "chown", "-R", "--", f"{user}:{user}", cfg.remote_dest_root)

    hosts_path: str = _write_temp_hosts([alma])

    argv_no: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "--pack"]
        + (["-v"] if cfg.verbose else [])
        + ["--", local_src, dest_no]
    )
    run_no: LocalRun = _run_local_argv(argv_no)

    argv_yes: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "--pack", "--follow-symlinks"]
        + (["-v"] if cfg.verbose else [])
        + ["--", local_src, dest_yes]
    )
    run_yes: LocalRun = _run_local_argv(argv_yes)

    remote_no_l: str = os.path.join(dest_no, abs_local_src, "l.txt")
    remote_yes_l: str = os.path.join(dest_yes, abs_local_src, "l.txt")

    r_no_exists: subprocess.CompletedProcess[str] = ssh_do(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "test", "-e", remote_no_l)

    r_yes_is_file: subprocess.CompletedProcess[str] = ssh_do(
        cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "test", "-f", remote_yes_l
    )
    r_yes_cat: subprocess.CompletedProcess[str] = ssh_do(
        cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "cat", remote_yes_l
    )

    passed: bool = (
        run_no.rc == 0
        and run_yes.rc == 0
        and r_no_exists.returncode != 0
        and r_yes_is_file.returncode == 0
        and (r_yes_cat.stdout or "").strip() == "Q"
    )
    reason: str = "" if passed else (
        f"scatter rc no/yes=({run_no.rc}/{run_yes.rc}), "
        f"no_exists_rc={r_no_exists.returncode}, file_rc={r_yes_is_file.returncode}, "
        f"content={r_yes_cat.stdout!r}, paths(no={remote_no_l}, yes={remote_yes_l})"
    )

    return {
        "name": name,
        "passed": passed,
        "skipped": False,
        "reason": reason,
        "details": {
            "no_rc": run_no.rc, "yes_rc": run_yes.rc,
            "remote_no_l": remote_no_l, "remote_yes_l": remote_yes_l,
        },
    }


# ---------------------------------------------------------------------------
# 11) scatter --pack + ユーザ展開（所有者がユーザ）
# ---------------------------------------------------------------------------

def case_scatter_pack_extract_user(cfg: Config) -> Dict[str, object]:
    name: str = "scatter_pack_extract_user"
    alma: str = cfg.host_alma
    user: str = cfg.target_user

    local_src: str = os.path.join(cfg.local_root, "s_pack_user_src")
    _ = _clear_dir(local_src, ensure_under=cfg.local_root)
    wf: IO[str]
    with open(os.path.join(local_src, "u.txt"), "w", encoding="utf-8") as wf:
        _ = wf.write("U\n")

    dest_dir: str = os.path.join(cfg.remote_dest_root, "s_pack_user_dest")
    _ = ssh_sudo(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "rm", "-rf", "--", dest_dir)
    _ = ssh_sudo(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "mkdir", "-p", "--", dest_dir)
    _ = ssh_sudo(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "chown", "-R", "--", f"{user}:{user}", dest_dir)

    hosts_path: str = _write_temp_hosts([alma])
    argv: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "--pack"]
        + (["-v"] if cfg.verbose else [])
        + ["--", local_src, dest_dir]
    )
    run: LocalRun = _run_local_argv(argv)

    rel_from_root: str = _as_posix_rel(os.path.abspath(local_src))
    remote_file: str = os.path.join(dest_dir, rel_from_root, "u.txt")
    r_stat_u: subprocess.CompletedProcess[str] = ssh_do(
        cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "stat", "-c", "%U:%G", remote_file
    )
    owner: str = (r_stat_u.stdout or "").strip()

    passed: bool = (run.rc == 0 and r_stat_u.returncode == 0 and owner == f"{user}:{user}")
    reason: str = "" if passed else f"rc={run.rc}, owner={owner!r}"

    return {"name": name, "passed": passed, "skipped": False, "reason": reason, "details": {"rc": run.rc, "owner": owner}}

# 12) scatter --pack --sudo-extract（未存在 → ユーザ権限で作成される仕様）

def case_scatter_pack_extract_sudo(cfg: Config) -> Dict[str, object]:
    """
    仕様（ご提示）:
      既存ファイルが『なければ』ユーザ権限で作成される。
      よって、--sudo-extract 指定でも新規作成物は <user>:<user> になる。

    検査:
      - DEST は root:root・0700 のまま
      - 展開後の r.txt が <user>:<user>
    """
    name: str = "scatter_pack_extract_sudo"
    alma: str = cfg.host_alma
    user: str = cfg.target_user

    local_src: str = os.path.join(cfg.local_root, "s_pack_sudo_src")
    _ = _clear_dir(local_src, ensure_under=cfg.local_root)
    wf: IO[str]
    with open(os.path.join(local_src, "r.txt"), "w", encoding="utf-8") as wf:
        _ = wf.write("R\n")
    abs_local_src: str = _as_posix_rel(os.path.abspath(local_src))

    dest_dir: str = os.path.join(cfg.remote_dest_root, "s_pack_sudo_dest")
    _ = ssh_sudo(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "rm", "-rf", "--", dest_dir)
    _ = ssh_sudo(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "mkdir", "-p", "--", dest_dir)
    _ = ssh_sudo(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "chown", "-R", "--", "root:root", dest_dir)
    _ = ssh_sudo(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "chmod", "0700", "--", dest_dir)

    hosts_path: str = _write_temp_hosts([alma])

    argv: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "--pack", "--sudo-extract"]
        + (["-v"] if cfg.verbose else [])
        + ["--", local_src, dest_dir]
    )
    run: LocalRun = _run_local_argv(argv)

    remote_r: str = os.path.join(dest_dir, abs_local_src, "r.txt")
    r_stat: subprocess.CompletedProcess[str] = ssh_sudo(
        cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "stat", "-c", "%U:%G", remote_r
    )
    owner: str = (r_stat.stdout or "").strip()

    passed: bool = (run.rc == 0 and r_stat.returncode == 0 and owner == f"{user}:{user}")
    reason: str = "" if passed else (
        f"rc={run.rc}, stat_rc={r_stat.returncode}, owner={owner!r}, path={remote_r}"
    )

    return {
        "name": name,
        "passed": passed,
        "skipped": False,
        "reason": reason,
        "details": {"rc": run.rc, "owner": owner, "remote_r": remote_r},
    }

# 12b) scatter --pack --sudo-extract（既存ファイルあり → root 展開されることを検証）

def case_scatter_pack_extract_sudo_existing_root(cfg: Config) -> Dict[str, object]:
    """
    追加ケース:
      既存ファイルが『ある』場合に --sudo-extract で root 展開されることを検証。
      期待: 既存の r.txt は root:root のまま（または root に設定される）。
            （オプション）内容の更新が入る実装なら "R2\n" を確認。

    手順:
      1) DEST/<abs_local_src>/r.txt を root:root で事前作成
      2) --pack --sudo-extract で scatter 実行
      3) 所有者が root:root であることを stat で確認
    """
    name: str = "scatter_pack_extract_sudo_existing_root"
    alma: str = cfg.host_alma
    user: str = cfg.target_user

    local_src: str = os.path.join(cfg.local_root, "s_pack_sudo_exist_src")
    _ = _clear_dir(local_src, ensure_under=cfg.local_root)
    wf: IO[str]
    with open(os.path.join(local_src, "r.txt"), "w", encoding="utf-8") as wf:
        _ = wf.write("R2\n")
    abs_local_src: str = _as_posix_rel(os.path.abspath(local_src))

    dest_dir: str = os.path.join(cfg.remote_dest_root, "s_pack_sudo_exist_dest")
    _ = ssh_sudo(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "rm", "-rf", "--", dest_dir)
    _ = ssh_sudo(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "mkdir", "-p", "--", dest_dir)
    _ = ssh_sudo(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "chown", "-R", "--", "root:root", dest_dir)
    _ = ssh_sudo(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "chmod", "0700", "--", dest_dir)

    remote_dir_for_file: str = os.path.join(dest_dir, abs_local_src)
    _ = ssh_sudo(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "mkdir", "-p", "--", remote_dir_for_file)
    remote_r: str = os.path.join(remote_dir_for_file, "r.txt")
    _ = pipe_to_tee(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, remote_r, content="PRE\n", sudo=True)
    _ = ssh_sudo(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "chown", "--", "root:root", remote_r)

    hosts_path: str = _write_temp_hosts([alma])

    argv: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "--pack", "--sudo-extract"]
        + (["-v"] if cfg.verbose else [])
        + ["--", local_src, dest_dir]
    )
    run: LocalRun = _run_local_argv(argv)

    r_stat: subprocess.CompletedProcess[str] = ssh_sudo(
        cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "stat", "-c", "%U:%G", remote_r
    )
    owner: str = (r_stat.stdout or "").strip()

    r_cat: subprocess.CompletedProcess[str] = ssh_sudo(
        cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "cat", remote_r
    )
    content_after: str = (r_cat.stdout or "")

    passed: bool = (run.rc == 0 and r_stat.returncode == 0 and owner == "root:root")
    reason: str = "" if passed else (
        f"rc={run.rc}, stat_rc={r_stat.returncode}, owner={owner!r}, content={content_after!r}, path={remote_r}"
    )

    return {
        "name": name,
        "passed": passed,
        "skipped": False,
        "reason": reason,
        "details": {"rc": run.rc, "owner": owner, "remote_r": remote_r, "content_after": content_after},
    }

# ---------------------------------------------------------------------------
# 13) Ubuntu: --selinux policy はエラー、--selinux ignore は成功（dry-run）
# ---------------------------------------------------------------------------

def case_selinux_policy_ignore_on_ubuntu(cfg: Config) -> Dict[str, object]:
    """
    Ubuntu 側（SELinux 非対応）で --selinux {policy,ignore} を指定した scatter の dry-run は、
    いずれも rc=0 で成功扱い（実装準拠）。
    """
    name: str = "selinux_policy_ignore_on_ubuntu"
    ubuntu: str = cfg.host_ubuntu
    user: str = cfg.target_user

    empty_src: str = os.path.join(cfg.local_root, "empty_src_selinux")
    _ = _clear_dir(empty_src, ensure_under=cfg.local_root)
    hosts_path: str = _write_temp_hosts([ubuntu])
    dest_dir: str = os.path.join(cfg.remote_dest_root, "selinux_ubuntu_test")

    argv_policy: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "-n", "--selinux", "policy", "--", empty_src, dest_dir]
    )
    run_policy: LocalRun = _run_local_argv(argv_policy)
    ok_policy: bool = (run_policy.rc == 0)

    argv_ignore: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "-n", "--selinux", "ignore", "--", empty_src, dest_dir]
    )
    run_ignore: LocalRun = _run_local_argv(argv_ignore)
    ok_ignore: bool = (run_ignore.rc == 0)

    passed: bool = (ok_policy and ok_ignore)
    reason: str = "" if passed else f"policy_rc={run_policy.rc}, ignore_rc={run_ignore.rc}"

    return {
        "name": name,
        "passed": passed,
        "skipped": False,
        "reason": reason,
        "details": {
            "policy_rc": run_policy.rc,
            "ignore_rc": run_ignore.rc,
            "policy_stdout": run_policy.stdout,
            "policy_stderr": run_policy.stderr,
            "ignore_stdout": run_ignore.stdout,
            "ignore_stderr": run_ignore.stderr,
        },
    }

# ---------------------------------------------------------------------------
# 14) gather の二重ネスト回帰検証
# ---------------------------------------------------------------------------

def case_gather_double_nesting_regression(cfg: Config) -> Dict[str, object]:
    """
    目的:
      gather の展開先レイアウトが DEST/<HOST>/<abs_without_leading_slash>/... であることを検証し、
      DEST/<HOST>/<HOST>/... のような「二重ネスト」が発生しないことを回帰テストする。

    前提:
      - 現行実装の local_path_for_download(...) に従い、絶対パスの収集結果は
        <dest>/<host>/<abs_without_leading_slash>/... に配置される。
      - ここでは Ubuntu ホスト (cfg.host_ubuntu) 上に /tmp/gm_nest_src を作り、
        その配下 (a.txt, b/b.txt) を --pack で収集する。
      - SRC はディレクトリ意図のため末尾 '/' を必ず付与する。

    検査:
      1) 期待するパス:
         <out>/localhost/tmp/gm_nest_src/a.txt
         <out>/localhost/tmp/gm_nest_src/b/b.txt
      2) 禁止するパス（回帰確認）:
         <out>/localhost/localhost/...
      3) <out> 直下の1階層目ディレクトリは "localhost" のみであること

    追加採取:
      find / tree -a によるレイアウトのスナップショットを details に格納
    """

    name: str = "gather_double_nesting_regression"
    ubuntu: str = cfg.host_ubuntu
    user: str = cfg.target_user

    abs_root: str = "/tmp/gm_nest_src"
    src_dir: str = abs_root.rstrip('/') + '/'
    file_a: str = os.path.join(src_dir, "a.txt")
    file_bdir: str = os.path.join(src_dir, "b")
    file_b: str = os.path.join(file_bdir, "b.txt")

    _ = ssh_do(cfg.ssh_user, ubuntu, cfg.ssh_port, cfg.ssh_strict, "rm", "-rf", "--", abs_root)
    _ = ssh_do(cfg.ssh_user, ubuntu, cfg.ssh_port, cfg.ssh_strict, "mkdir", "-p", "--", file_bdir)
    _ = ssh_sudo(cfg.ssh_user, ubuntu, cfg.ssh_port, cfg.ssh_strict, "chown", "-R", "--", f"{user}:{user}", abs_root)
    _ = pipe_to_tee(cfg.ssh_user, ubuntu, cfg.ssh_port, cfg.ssh_strict, file_a, content="A\n", sudo=False)
    _ = pipe_to_tee(cfg.ssh_user, ubuntu, cfg.ssh_port, cfg.ssh_strict, file_b, content="B\n", sudo=False)

    hosts_path: str = _write_temp_hosts([ubuntu])

    out_dir: str = os.path.join(cfg.local_root, "g_double_nest")
    _ = _clear_dir(out_dir, ensure_under=cfg.local_root)

    argv: List[str] = (
        cfg.gm_gather_cmd
        + ["-H", hosts_path, "-u", user, "--pack"]
        + (["-v"] if cfg.verbose else [])
        + ["--", src_dir, out_dir]
    )
    run: LocalRun = _run_local_argv(argv)

    def _snapshot(path_dir: str) -> Dict[str, str]:
        snap: Dict[str, str] = {}
        cmd_find: str = f'find {shlex.quote(path_dir)} -maxdepth 8 -printf "%y %p -> %l\\n"'
        p1: subprocess.CompletedProcess[str] = subprocess.run(["bash", "-lc", cmd_find], capture_output=True, text=True)
        snap["find"] = (p1.stdout or "") + (("\n[find-err]\n" + p1.stderr) if p1.stderr else "")
        cmd_tree: str = f'tree -a {shlex.quote(path_dir)}'
        p2: subprocess.CompletedProcess[str] = subprocess.run(["bash", "-lc", cmd_tree], capture_output=True, text=True)
        tree_out: str = p2.stdout or ""
        if p2.returncode != 0 and not tree_out:
            tree_out = "(tree not available or failed)"
        snap["tree"] = tree_out
        return snap

    snap: Dict[str, str] = _snapshot(out_dir)

    host_label: str = cfg.host_ubuntu
    exp_a: str = os.path.join(out_dir, host_label, "tmp", "gm_nest_src", "a.txt")
    exp_b: str = os.path.join(out_dir, host_label, "tmp", "gm_nest_src", "b", "b.txt")
    bad_prefix: str = os.path.join(out_dir, host_label, host_label) + os.sep

    rc_ok: bool = (run.rc == 0)
    a_ok: bool = os.path.isfile(exp_a)
    b_ok: bool = os.path.isfile(exp_b)

    double_nest_found: bool = False
    walk_list: List[Tuple[str, List[str], List[str]]] = list(os.walk(out_dir))
    w_i: int = 0
    w_n: int = len(walk_list)
    while w_i < w_n:
        triple: Tuple[str, List[str], List[str]] = walk_list[w_i]
        root: str = triple[0]
        if (root + os.sep).startswith(bad_prefix):
            double_nest_found = True
            break
        w_i += 1

    try:
        top_entries_listdir: List[str] = os.listdir(out_dir)
    except FileNotFoundError:
        top_entries_listdir = []
    top_entries: List[str] = []
    d_idx: int = 0
    d_total: int = len(top_entries_listdir)
    while d_idx < d_total:
        d_name: str = top_entries_listdir[d_idx]
        if os.path.isdir(os.path.join(out_dir, d_name)):
            top_entries.append(d_name)
        d_idx += 1
    top_ok: bool = (top_entries == [host_label]) or (sorted(top_entries) == [host_label])

    passed: bool = rc_ok and a_ok and b_ok and (not double_nest_found) and top_ok

    reason: str = "" if passed else (
        f"rc={run.rc}, a_ok={a_ok}, b_ok={b_ok}, "
        f"double_nest_found={double_nest_found}, top_entries={top_entries}"
    )

    return {
        "name": name,
        "passed": passed,
        "skipped": False,
        "reason": reason,
        "details": {
            "rc": run.rc,
            "argv": " ".join(shlex.quote(a) for a in argv),
            "expected_a": exp_a,
            "expected_b": exp_b,
            "double_nest_bad_prefix": bad_prefix,
            "top_entries": top_entries,
            "snapshot_find": snap.get("find", ""),
            "snapshot_tree": snap.get("tree", ""),
        },
    }

def _remote_script_snapshot(ssh_user: str, host: str, port: int, strict: bool, base: str) -> Dict[str, str]:
    """
    リモート絶対パス base の実体を『base固定で』観測するスナップショット。
    - pwd（安全のため明示出力）
    - ls -la -- <base>
    - find <base> -maxdepth 8 -printf "%y %p -> %l\n"
    - tree -a <base>（無ければ代替出力）
    """
    snap: Dict[str, str] = {}
    script: str = "\n".join([
        "set -e",
        "echo '[[pwd]]'; pwd || true",
        f"echo '[[ls -la {shlex.quote(base)}]]'; ls -la -- {shlex.quote(base)} || true",
        f"echo '[[find {shlex.quote(base)}]]'; find {shlex.quote(base)} -maxdepth 8 -printf '%y %p -> %l\\n' || true",
        f"echo '[[tree -a {shlex.quote(base)}]]'; tree -a {shlex.quote(base)} || echo '(tree not available or failed)'",
    ])
    p = ssh_do(ssh_user, host, port, strict, "bash", "-lc", script)
    snap["stdout"] = p.stdout or ""
    snap["stderr"] = p.stderr or ""
    snap["rc"] = str(p.returncode)
    return snap

def case_scatter_src_path_layout_semantics(cfg: Config) -> Dict[str, object]:
    """
    目的:
      scatter のレイアウト仕様を検証する回帰テスト。
        - SRC が絶対パス指定の場合    : DEST/<local_abs_without_leading_slash>
        - SRC が相対パス指定の場合    : DEST/<指定された相対パス>
      を、それぞれ実ファイルの生成位置で確認する。

    前提:
      - scatter は --pack 経路で実行する。
      - DEST はリモート絶対パス。ここでは /tmp/gm_scatter_layout_dest を使用する。
      - 相対 SRC は「テスト実行時のカレントディレクトリ」からの相対とする。
      - ディレクトリ意図は末尾 '/' を付与する。

    検査:
      [絶対]  DEST/<abs_without_leading_slash>/{a.txt, sub/b.txt} が通常ファイルで存在
      [相対]  DEST/<relative_specified>/{a.txt, sub/b.txt} が通常ファイルで存在
      いずれも rc=0 で終了すること。

    追加採取:
      リモート DEST 配下の find / tree -a のスナップショットを details に格納
    """
    name: str = "scatter_src_path_layout_semantics"
    ubuntu: str = cfg.host_ubuntu
    user: str = cfg.target_user

    abs_src_dir: str = os.path.join(cfg.local_root, "sc_layout_abs_src")
    abs_src_dir = abs_src_dir.rstrip(os.sep) + os.sep
    _ = os.makedirs(os.path.join(abs_src_dir, "sub"), exist_ok=True)
    wf: IO[str]
    with open(os.path.join(abs_src_dir, "a.txt"), "w", encoding="utf-8") as wf:
        _ = wf.write("A\n")
    with open(os.path.join(abs_src_dir, "sub", "b.txt"), "w", encoding="utf-8") as wf:
        _ = wf.write("B\n")

    rel_src_basename: str = "sc_layout_rel_src"
    cwd: str = os.getcwd()
    rel_src_dir_abs: str = os.path.join(cwd, rel_src_basename)
    rel_src_dir_rel: str = rel_src_basename + os.sep
    _ = os.makedirs(os.path.join(rel_src_dir_abs, "sub"), exist_ok=True)
    with open(os.path.join(rel_src_dir_abs, "a.txt"), "w", encoding="utf-8") as wf:
        _ = wf.write("A\n")
    with open(os.path.join(rel_src_dir_abs, "sub", "b.txt"), "w", encoding="utf-8") as wf:
        _ = wf.write("B\n")

    dest_abs: str = "/tmp/gm_scatter_layout_dest"
    _ = ssh_do(cfg.ssh_user, ubuntu, cfg.ssh_port, cfg.ssh_strict, "rm", "-rf", "--", dest_abs)
    _ = ssh_do(cfg.ssh_user, ubuntu, cfg.ssh_port, cfg.ssh_strict, "mkdir", "-p", "--", dest_abs)
    _ = ssh_sudo(cfg.ssh_user, ubuntu, cfg.ssh_port, cfg.ssh_strict,
                 "chown", "-R", "--", f"{user}:{user}", dest_abs)

    hosts_path: str = _write_temp_hosts([ubuntu])

    argv_abs: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "--pack"]
        + (["-v"] if cfg.verbose else [])
        + ["--", abs_src_dir, dest_abs]
    )
    run_abs: LocalRun = _run_local_argv(argv_abs)

    abs_without_leading: str = _as_posix_rel(abs_src_dir)
    exp_abs_a: str = os.path.join(dest_abs, abs_without_leading, "a.txt")
    exp_abs_b: str = os.path.join(dest_abs, abs_without_leading, "sub", "b.txt")

    argv_rel: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "--pack"]
        + (["-v"] if cfg.verbose else [])
        + ["--", rel_src_dir_rel, dest_abs]
    )
    run_rel: LocalRun = _run_local_argv(argv_rel)

    exp_rel_a: str = os.path.join(dest_abs, rel_src_basename, "a.txt")
    exp_rel_b: str = os.path.join(dest_abs, rel_src_basename, "sub", "b.txt")

    # DEST 配下のみを確実に観測する（HOMEを走査しない）
    snap_dest: Dict[str, str] = _remote_script_snapshot(cfg.ssh_user, ubuntu, cfg.ssh_port, cfg.ssh_strict, dest_abs)

    snap = snapshot_scatter_dest_verbose(
        cfg, ubuntu, dest_abs,
        expected_paths=[exp_abs_a, exp_abs_b, exp_rel_a, exp_rel_b]
    )

    def _remote_is_file(path_abs: str) -> bool:
        # シェル経由を避け、終了コードのみで判定
        r: subprocess.CompletedProcess[str] = ssh_do(
            cfg.ssh_user, ubuntu, cfg.ssh_port, cfg.ssh_strict, "test", "-f", path_abs
        )
        return r.returncode == 0

    abs_ok: bool = _remote_is_file(exp_abs_a) and _remote_is_file(exp_abs_b)
    rel_ok: bool = _remote_is_file(exp_rel_a) and _remote_is_file(exp_rel_b)

    rc_ok: bool = (run_abs.rc == 0) and (run_rel.rc == 0)
    passed: bool = rc_ok and abs_ok and rel_ok

    reason: str = "" if passed else (
        f"rc_abs={run_abs.rc}, rc_rel={run_rel.rc}, "
        f"abs_ok={abs_ok}, rel_ok={rel_ok}, "
        f"exp_abs_a={exp_abs_a}, exp_abs_b={exp_abs_b}, "
        f"exp_rel_a={exp_rel_a}, exp_rel_b={exp_rel_b}"
    )

    return {
        "name": name,
        "passed": passed,
        "skipped": False,
        "reason": reason,
        "details": {
            "argv_abs": " ".join(shlex.quote(a) for a in argv_abs),
            "argv_rel": " ".join(shlex.quote(a) for a in argv_rel),
            "rc_abs": run_abs.rc,
            "rc_rel": run_rel.rc,
            "exp_abs_a": exp_abs_a,
            "exp_abs_b": exp_abs_b,
            "exp_rel_a": exp_rel_a,
            "exp_rel_b": exp_rel_b,
            "snapshot_dest_stdout": snap_dest.get("stdout", ""),
            "snapshot_dest_stderr": snap_dest.get("stderr", ""),
            "snapshot_dest_rc": snap_dest.get("rc", ""),
            "scatter_dest_verbose_snapshot": snap,
        },
    }

# =========================
# 追加テスト（関数のみ）
# =========================

def case_scatter_dest_relative_to_remote_home(cfg: Config) -> Dict[str, object]:
    """
    目的:
      scatter の DEST が「相対」のとき、ターゲットユーザの remote_home 配下に
      解決されることを検証（--pack）。
    期待:
      DEST="gm_rel_dest" とすると、展開先が
        <remote_home>/gm_rel_dest/<abs_without_leading>/a.txt
      に配置される。
    """
    name: str = "scatter_dest_relative_to_remote_home"
    host: str = cfg.host_ubuntu
    user: str = cfg.target_user

    abs_src: str = os.path.join(cfg.local_root, "sc_relhome_abs_src")
    abs_src = abs_src.rstrip(os.sep) + os.sep
    _ = os.makedirs(abs_src, exist_ok=True)
    wf: IO[str]
    with open(os.path.join(abs_src, "a.txt"), "w", encoding="utf-8") as wf:
        _ = wf.write("A\n")

    dest_rel: str = "gm_rel_dest"

    hosts_path: str = _write_temp_hosts([host])
    argv: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "--pack"]
        + (["-v"] if cfg.verbose else [])
        + ["--", abs_src, dest_rel]
    )
    run: LocalRun = _run_local_argv(argv)

    home: str = _get_remote_home(cfg, host, user)
    abs_without: str = abs_src.lstrip(os.sep)
    exp: str = os.path.join(home, "gm_rel_dest", abs_without, "a.txt")

    r_isfile: subprocess.CompletedProcess[str] = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict,
                      "test", "-f", exp)
    r_cat: subprocess.CompletedProcess[str] = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict,
                   "cat", exp)

    passed: bool = (run.rc == 0 and r_isfile.returncode == 0 and (r_cat.stdout or "").strip() == "A")
    reason: str = "" if passed else f"rc={run.rc}, isfile_rc={r_isfile.returncode}, exp={exp!r}, content={r_cat.stdout!r}"
    return {"name": name, "passed": passed, "skipped": False, "reason": reason,
            "details": {"rc": run.rc, "exp": exp, "argv": " ".join(shlex.quote(a) for a in argv)}}



def case_scatter_dest_tilde_username_rejected(cfg: Config) -> Dict[str, object]:
    """
    目的:
      scatter の DEST に ~user 形式が来た場合にエラー（rc!=0）になることを検証（dry-run）。
    期待:
      rc!=0 かつ エラーメッセージに "tilde with username is not supported" を含む。
    """
    name: str = "scatter_dest_tilde_username_rejected"
    host: str = cfg.host_alma
    user: str = cfg.target_user

    src_dir: str = os.path.join(cfg.local_root, "sc_tilde_user_src")
    _ = _clear_dir(src_dir, ensure_under=cfg.local_root)
    wf: IO[str]
    with open(os.path.join(src_dir, "x.txt"), "w", encoding="utf-8") as wf:
        _ = wf.write("X\n")

    other: str = "root" if user != "root" else "nobody"
    dest_bad: str = f"~{other}/somewhere"

    hosts_path: str = _write_temp_hosts([host])
    argv: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "-n"]
        + (["-v"] if cfg.verbose else [])
        + ["--", src_dir, dest_bad]
    )
    run: LocalRun = _run_local_argv(argv)
    out_all: str = (run.stderr or "") + (run.stdout or "")
    marker: bool = "tilde with username is not supported" in out_all

    passed: bool = (run.rc != 0 and marker)
    reason: str = "" if passed else f"rc={run.rc}, marker={marker}, stderr+stdout={out_all!r}"
    return {"name": name, "passed": passed, "skipped": False, "reason": reason,
            "details": {"rc": run.rc, "stderr_stdout": out_all, "argv": " ".join(shlex.quote(a) for a in argv)}}



def case_scatter_nonpack_file_only_layout(cfg: Config) -> Dict[str, object]:
    """
    目的:
      非 pack（SFTP逐次PUT）のレイアウトと挙動を検証。
        - ファイル SRC は転送される。
        - ディレクトリ SRC はスキップされる（実装で continue）。
        - 絶対ファイル SRC: DEST/<abs_without_leading>/…
        - 相対ファイル SRC: DEST/<指定相対>/…
    """
    name: str = "scatter_nonpack_file_only_layout"
    host: str = cfg.host_ubuntu
    user: str = cfg.target_user

    abs_dir: str = os.path.join(cfg.local_root, "nonpack_abs_dir")
    rel_dir: str = "nonpack_rel_dir"
    ign_dir: str = os.path.join(cfg.local_root, "nonpack_ignored_dir")

    _ = _clear_dir(abs_dir, ensure_under=cfg.local_root)
    _ = _clear_dir(ign_dir, ensure_under=cfg.local_root)
    _ = os.makedirs(rel_dir, exist_ok=True)

    abs_file: str = os.path.join(abs_dir, "a.txt")
    rel_file: str = os.path.join(rel_dir, "b.txt")
    ign_file: str = os.path.join(ign_dir, "c.txt")

    wf: IO[str]
    with open(abs_file, "w", encoding="utf-8") as wf:
        _ = wf.write("A\n")
    with open(rel_file, "w", encoding="utf-8") as wf:
        _ = wf.write("B\n")
    with open(ign_file, "w", encoding="utf-8") as wf:
        _ = wf.write("C\n")

    dest_abs: str = "/tmp/gm_scatter_nonpack_dest"
    _ = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "rm", "-rf", "--", dest_abs)
    _ = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "mkdir", "-p", "--", dest_abs)
    _ = ssh_sudo(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict,
                 "chown", "-R", "--", f"{user}:{user}", dest_abs)

    hosts_path: str = _write_temp_hosts([host])
    argv: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user]
        + (["-v"] if cfg.verbose else [])
        + ["--", abs_file, rel_file, ign_dir, dest_abs]
    )
    run: LocalRun = _run_local_argv(argv)

    exp_abs: str = os.path.join(dest_abs, os.path.abspath(abs_file).lstrip(os.sep))
    exp_rel: str = os.path.join(dest_abs, rel_file)
    exp_ign: str = os.path.join(dest_abs, os.path.abspath(ign_dir).lstrip(os.sep))

    r_abs: subprocess.CompletedProcess[str] = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "test", "-f", exp_abs)
    r_rel: subprocess.CompletedProcess[str] = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "test", "-f", exp_rel)
    r_ign: subprocess.CompletedProcess[str] = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "test", "-e", exp_ign)

    passed: bool = (run.rc == 0 and r_abs.returncode == 0 and r_rel.returncode == 0 and r_ign.returncode != 0)
    reason: str = "" if passed else f"rc={run.rc}, abs={r_abs.returncode}, rel={r_rel.returncode}, ign_exists_rc={r_ign.returncode}"
    return {"name": name, "passed": passed, "skipped": False, "reason": reason,
            "details": {"rc": run.rc, "exp_abs": exp_abs, "exp_rel": exp_rel, "exp_ign": exp_ign,
                        "argv": " ".join(shlex.quote(a) for a in argv)}}



def case_scatter_mixed_sources_two_hosts(cfg: Config) -> Dict[str, object]:
    """
    目的:
      複数ホスト一括 scatter（--pack）の回帰。
      hosts ファイルに Ubuntu/Alma を同時に渡し、両方で所定パスに展開されること。
    """
    name: str = "scatter_mixed_sources_two_hosts"

    src_a: str = os.path.join(cfg.local_root, "mix_src_a")
    src_a = src_a.rstrip(os.sep) + os.sep
    src_b: str = os.path.join(cfg.local_root, "mix_src_b")
    src_b = src_b.rstrip(os.sep) + os.sep
    _ = os.makedirs(src_a, exist_ok=True)
    _ = os.makedirs(src_b, exist_ok=True)
    wf: IO[str]
    with open(os.path.join(src_a, "a.txt"), "w", encoding="utf-8") as wf:
        _ = wf.write("A\n")
    with open(os.path.join(src_b, "b.txt"), "w", encoding="utf-8") as wf:
        _ = wf.write("B\n")

    hosts_path: str = _write_temp_hosts([cfg.host_ubuntu, cfg.host_alma])
    dest_abs: str = "/tmp/gm_scatter_mixed_hosts"
    i: int = 0
    targets: List[str] = [cfg.host_ubuntu, cfg.host_alma]
    while i < len(targets):
        h: str = targets[i]
        _ = ssh_do(cfg.ssh_user, h, cfg.ssh_port, cfg.ssh_strict, "rm", "-rf", "--", dest_abs)
        _ = ssh_do(cfg.ssh_user, h, cfg.ssh_port, cfg.ssh_strict, "mkdir", "-p", "--", dest_abs)
        _ = ssh_sudo(cfg.ssh_user, h, cfg.ssh_port, cfg.ssh_strict,
                     "chown", "-R", "--", f"{cfg.target_user}:{cfg.target_user}", dest_abs)
        i += 1

    argv: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", cfg.target_user, "--pack"]
        + (["-v"] if cfg.verbose else [])
        + ["--", src_a, src_b, dest_abs]
    )
    run: LocalRun = _run_local_argv(argv)

    def _ok_on(h: str) -> Tuple[bool, str]:
        a_path: str = os.path.join(dest_abs, src_a.lstrip(os.sep), "a.txt")
        b_path: str = os.path.join(dest_abs, src_b.lstrip(os.sep), "b.txt")
        r1: subprocess.CompletedProcess[str] = ssh_do(cfg.ssh_user, h, cfg.ssh_port, cfg.ssh_strict, "test", "-f", a_path)
        r2: subprocess.CompletedProcess[str] = ssh_do(cfg.ssh_user, h, cfg.ssh_port, cfg.ssh_strict, "test", "-f", b_path)
        ok: bool = (r1.returncode == 0 and r2.returncode == 0)
        return ok, f"{h}: a_rc={r1.returncode}, b_rc={r2.returncode}, a={a_path}, b={b_path}"

    ok_u: Tuple[bool, str] = _ok_on(cfg.host_ubuntu)
    ok_a: Tuple[bool, str] = _ok_on(cfg.host_alma)

    passed: bool = (run.rc == 0 and ok_u[0] and ok_a[0])
    reason: str = "" if passed else f"rc={run.rc}; {ok_u[1]}; {ok_a[1]}"
    return {"name": name, "passed": passed, "skipped": False, "reason": reason,
            "details": {"rc": run.rc, "rep_ubuntu": ok_u[1], "rep_alma": ok_a[1],
                        "argv": " ".join(shlex.quote(a) for a in argv)}}

def case_scatter_pack_dedup_roots(cfg: Config) -> Dict[str, object]:
    """
    目的:
      --pack 時の _dedup_roots_for_pack による重複ルート除去を検証。
      親ディレクトリとその子を同時指定しても、展開結果に二重ネストが生じないこと。
    期待:
      DEST/.../dup_root/sub/n.txt が「1個だけ」存在（dup_root/sub/sub/... のような重複は生じない）。
    """
    name: str = "scatter_pack_dedup_roots"
    host: str = cfg.host_ubuntu
    user: str = cfg.target_user

    dup_root: str = os.path.join(cfg.local_root, "dup_root")
    dup_root = dup_root.rstrip(os.sep) + os.sep
    dup_sub: str = os.path.join(dup_root, "sub")
    _ = os.makedirs(dup_sub, exist_ok=True)
    wf: IO[str]
    with open(os.path.join(dup_sub, "n.txt"), "w", encoding="utf-8") as wf:
        _ = wf.write("N\n")

    # テストごとに一意な DEST を使い、前回残骸や並列実行の干渉を排除
    dest_abs: str = f"/tmp/gm_scatter_dedup_dest_{os.getpid()}_{int.from_bytes(os.urandom(2),'big')}"
    _ = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "rm", "-rf", "--", dest_abs)
    _ = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "mkdir", "-p", "--", dest_abs)
    _ = ssh_sudo(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict,
                 "chown", "-R", "--", f"{user}:{user}", dest_abs)

    hosts_path: str = _write_temp_hosts([host])
    # ホスト行数（診断用）
    _hosts_cnt: int = 0
    try:
        with open(hosts_path, "r", encoding="utf-8") as hf:
            _hosts_cnt = sum(1 for _ in hf if _.strip())
    except Exception:
        _hosts_cnt = -1

    argv: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "--pack"]
        + (["-v"] if cfg.verbose else [])
        + ["--", dup_root, os.path.join(dup_root, "sub"), dest_abs]
    )
    run: LocalRun = _run_local_argv(argv)

    # OS 非依存の相対化（先頭スラッシュ除去 + 区切り '/' 化）で一貫性を確保
    dup_root_rel: str = _as_posix_rel(os.path.abspath(dup_root))
    base: str = os.path.join(dest_abs, dup_root_rel)
    exp: str = os.path.join(base, "sub", "n.txt")

    # 期待パス(exp)の存在を確認したうえで、「exp 以外の n.txt が無い」ことを確認
    q_dest = shlex.quote(dest_abs)
    q_exp  = shlex.quote(exp)

    cmd_total  = f'LC_ALL=C find {q_dest} -type f -name n.txt -printf "%p\\n" | wc -l'
    cmd_others = f'LC_ALL=C find {q_dest} -type f -name n.txt ! -path {q_exp} -printf "%p\\n" | wc -l'
    cmd_list   = f'LC_ALL=C find {q_dest} -type f -name n.txt -printf "%p\\n" | sort'

    r_total  = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "bash", "-lc", cmd_total)
    r_others = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "bash", "-lc", cmd_others)
    r_list   = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "bash", "-lc", cmd_list)

    def _to_int(s: str) -> int:
        try:
            return int((s or "0").strip())
        except Exception:
            return -1

    cnt_total: int = _to_int(r_total.stdout)
    cnt_others: int = _to_int(r_others.stdout)

    # DEST全体のスナップショット（HOMEではなくDEST固定）
    snap_dest: Dict[str, str] = _remote_script_snapshot(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, dest_abs)

    r_isfile: subprocess.CompletedProcess[str] = ssh_do(
        cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "test", "-f", exp
    )

    # 合否: 実行成功 + 期待ファイルが存在 + 期待以外の n.txt が 0 件
    passed: bool = (run.rc == 0 and r_isfile.returncode == 0 and cnt_others == 0)
    reason: str = "" if passed else (
        f"rc={run.rc}, isfile_rc={r_isfile.returncode}, "
        f"total_n_txt={cnt_total}, others_except_exp={cnt_others}, exp={exp}"
    )

    return {
        "name": name,
        "passed": passed,
        "skipped": False,
        "reason": reason,
        "details": {
            "rc": run.rc,
            "hosts_count": _hosts_cnt,
            "count_total_n_txt": cnt_total,
            "count_others_except_exp": cnt_others,
            "list_all_n_txt": r_list.stdout or "",
            "exp": exp,
            "argv": " ".join(shlex.quote(a) for a in argv),
            "snapshot_dest_stdout": snap_dest.get("stdout", ""),
            "snapshot_dest_stderr": snap_dest.get("stderr", ""),
            "snapshot_dest_rc": snap_dest.get("rc", ""),
        },
    }


def case_scatter_nonpack_same_basename_collision_free(cfg: Config) -> Dict[str, object]:
    """
    目的:
      非 pack で、同名 basename のファイル（絶対/相対が混在）を同一 DEST に送っても
      リモートで別パスに正しく配置され衝突しないことを検証。
    期待:
      - 絶対 SRC: DEST/<abs_without_leading>/x.txt が存在
      - 相対 SRC: DEST/<given_relative>/x.txt が存在
    """
    name: str = "scatter_nonpack_same_basename_collision_free"
    host: str = cfg.host_ubuntu
    user: str = cfg.target_user

    dir_abs: str = os.path.join(cfg.local_root, "nf_abs")
    _ = _clear_dir(dir_abs, ensure_under=cfg.local_root)
    dir_rel: str = "nf_rel"
    _ = os.makedirs(dir_rel, exist_ok=True)

    f_abs: str = os.path.join(dir_abs, "x.txt")
    f_rel: str = os.path.join(dir_rel, "x.txt")
    wf: IO[str]
    with open(f_abs, "w", encoding="utf-8") as wf:
        _ = wf.write("ABS\n")
    with open(f_rel, "w", encoding="utf-8") as wf:
        _ = wf.write("REL\n")

    dest_abs: str = "/tmp/gm_scatter_nonpack_collision"
    _ = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "rm", "-rf", "--", dest_abs)
    _ = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "mkdir", "-p", "--", dest_abs)
    _ = ssh_sudo(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict,
                 "chown", "-R", "--", f"{user}:{user}", dest_abs)

    hosts_path: str = _write_temp_hosts([host])
    argv: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user]
        + (["-v"] if cfg.verbose else [])
        + ["--", f_abs, f_rel, dest_abs]
    )
    run: LocalRun = _run_local_argv(argv)

    exp_abs: str = os.path.join(dest_abs, os.path.abspath(f_abs).lstrip(os.sep))
    exp_rel: str = os.path.join(dest_abs, f_rel)

    r1: subprocess.CompletedProcess[str] = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "test", "-f", exp_abs)
    r2: subprocess.CompletedProcess[str] = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "test", "-f", exp_rel)
    c1: subprocess.CompletedProcess[str] = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "cat", exp_abs)
    c2: subprocess.CompletedProcess[str] = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "cat", exp_rel)

    passed: bool = (run.rc == 0 and r1.returncode == 0 and r2.returncode == 0
              and (c1.stdout or "").strip() == "ABS" and (c2.stdout or "").strip() == "REL")
    reason: str = "" if passed else f"rc={run.rc}, r1={r1.returncode}, r2={r2.returncode}, c1={c1.stdout!r}, c2={c2.stdout!r}"
    return {"name": name, "passed": passed, "skipped": False, "reason": reason,
            "details": {"rc": run.rc, "exp_abs": exp_abs, "exp_rel": exp_rel,
                        "argv": " ".join(shlex.quote(a) for a in argv)}}

def case_gather_src_regex_absolute(cfg: Config) -> Dict[str, object]:
    """
    目的:
      gather の SRC を正規表現として解釈する（絶対パス）挙動の検証。
      - 例: <abs>/src/dir1/.*\\.txt -> dir1/b.txt のみが対象（a.txt は対象外）
    """
    name: str = "gather_src_regex_absolute"
    host: str = cfg.host_ubuntu
    user: str = cfg.target_user

    # リモートに検体を作成: <home>/gm_step4_regex_abs/src/{a.txt, dir1/b.txt}
    home: str = _get_remote_home(cfg, host, user)
    abs_root: str = os.path.join(home, "gm_step4_regex_abs")
    src_dir: str = os.path.join(abs_root, "src")
    dir1: str = os.path.join(src_dir, "dir1")
    _ = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "rm", "-rf", "--", abs_root)
    _ = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "mkdir", "-p", "--", dir1)
    _ = ssh_sudo(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "chown", "-R", "--", f"{user}:{user}", abs_root)
    _ = pipe_to_tee(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, os.path.join(src_dir, "a.txt"), content="A\n", sudo=False)
    _ = pipe_to_tee(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, os.path.join(dir1, "b.txt"), content="B\n", sudo=False)

    # 正規表現 SRC（絶対）: dir1 配下の *.txt のみ
    pattern: str = os.path.join(src_dir, "dir1") + "/.*\\.txt"

    # ローカル出力先
    out_dir: str = os.path.join(cfg.local_root, "g_regex_abs_out")
    _ = _clear_dir(out_dir, ensure_under=cfg.local_root)

    hosts_path: str = _write_temp_hosts([host])
    argv: List[str] = (
        cfg.gm_gather_cmd
        + ["-H", hosts_path, "-u", user, "--pack"]
        + (["-v"] if cfg.verbose else [])
        + ["--", pattern, out_dir]
    )
    run: LocalRun = _run_local_argv(argv)

    # 期待ローカルパス:
    # <out>/<host>/<abs_without_leading>/src/dir1/b.txt は存在
    # <out>/<host>/<abs_without_leading>/src/a.txt は不在
    host_label: str = host
    exp_b: str = os.path.join(out_dir, host_label, os.path.join(dir1, "b.txt").lstrip(os.sep))
    exp_a: str = os.path.join(out_dir, host_label, os.path.join(src_dir, "a.txt").lstrip(os.sep))

    b_ok: bool = os.path.isfile(exp_b)
    a_ng: bool = not os.path.exists(exp_a)

    passed: bool = (run.rc == 0 and b_ok and a_ng)
    reason: str = "" if passed else f"rc={run.rc}, b_ok={b_ok}, a_ng={a_ng}, exp_b={exp_b}, exp_a={exp_a}"
    return {"name": name, "passed": passed, "skipped": False, "reason": reason,
            "details": {"rc": run.rc, "argv": " ".join(shlex.quote(a) for a in argv),
                        "exp_b": exp_b, "exp_a": exp_a}}


def case_gather_src_regex_relative(cfg: Config) -> Dict[str, object]:
    """
    目的:
      gather の SRC 正規表現（相対パス）挙動の検証（-u の HOME 相対）。
      - 例: gm_step4_regex_rel/src/dir1/.* -> dir1/b.txt のみが対象
    """
    name: str = "gather_src_regex_relative"
    host: str = cfg.host_ubuntu
    user: str = cfg.target_user

    # リモートに検体: <home>/gm_step4_regex_rel/src/{a.txt, dir1/b.txt}
    home: str = _get_remote_home(cfg, host, user)
    rel_top: str = "gm_step4_regex_rel"
    abs_root: str = os.path.join(home, rel_top)
    src_dir: str = os.path.join(abs_root, "src")
    dir1: str = os.path.join(src_dir, "dir1")
    _ = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "rm", "-rf", "--", abs_root)
    _ = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "mkdir", "-p", "--", dir1)
    _ = ssh_sudo(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "chown", "-R", "--", f"{user}:{user}", abs_root)
    _ = pipe_to_tee(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, os.path.join(src_dir, "a.txt"), content="A\n", sudo=False)
    _ = pipe_to_tee(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, os.path.join(dir1, "b.txt"), content="B\n", sudo=False)

    # 正規表現 SRC（相対）: dir1 配下のみ
    pattern_rel: str = f"{rel_top}/src/dir1/.*"

    out_dir: str = os.path.join(cfg.local_root, "g_regex_rel_out")
    _ = _clear_dir(out_dir, ensure_under=cfg.local_root)

    hosts_path: str = _write_temp_hosts([host])
    argv: List[str] = (
        cfg.gm_gather_cmd
        + ["-H", hosts_path, "-u", user, "--pack"]
        + (["-v"] if cfg.verbose else [])
        + ["--", pattern_rel, out_dir]
    )
    run: LocalRun = _run_local_argv(argv)

    host_label: str = host
    # gather のローカル配置は「解決済みの絶対パス」を用いるため、相対SRCでも
    # <out>/<host>/<home>/<rel_top>/... 配下に出力される
    exp_b: str = os.path.join(out_dir, host_label, os.path.join(dir1, "b.txt").lstrip(os.sep))
    exp_a: str = os.path.join(out_dir, host_label, os.path.join(src_dir, "a.txt").lstrip(os.sep))

    b_ok: bool = os.path.isfile(exp_b)
    a_ng: bool = not os.path.exists(exp_a)

    passed: bool = (run.rc == 0 and b_ok and a_ng)
    reason: str = "" if passed else f"rc={run.rc}, b_ok={b_ok}, a_ng={a_ng}, exp_b={exp_b}, exp_a={exp_a}"
    return {"name": name, "passed": passed, "skipped": False, "reason": reason,
            "details": {"rc": run.rc, "argv": " ".join(shlex.quote(a) for a in argv),
                        "exp_b": exp_b, "exp_a": exp_a}}


def case_gather_src_regex_negative(cfg: Config) -> Dict[str, object]:
    """
    目的:
      誤マッチ防止（アンカー ^/$）の検証（絶対パス）。
      - 例: <abs>/src/^x\\.txt$ -> x.txt のみを許容し、x.txt.bak は除外。
    """
    name: str = "gather_src_regex_negative"
    host: str = cfg.host_ubuntu
    user: str = cfg.target_user

    # リモートに検体: <home>/gm_step4_regex_neg/src/{x.txt, x.txt.bak}
    home: str = _get_remote_home(cfg, host, user)
    abs_root: str = os.path.join(home, "gm_step4_regex_neg")
    src_dir: str = os.path.join(abs_root, "src")
    _ = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "rm", "-rf", "--", abs_root)
    _ = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "mkdir", "-p", "--", src_dir)
    _ = ssh_sudo(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "chown", "-R", "--", f"{user}:{user}", abs_root)
    _ = pipe_to_tee(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, os.path.join(src_dir, "x.txt"), content="X\n", sudo=False)
    _ = pipe_to_tee(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, os.path.join(src_dir, "x.txt.bak"), content="XB\n", sudo=False)

    # アンカー付き SRC: basename 厳密一致のみ
    pattern: str = os.path.join(src_dir, "^x\\.txt$")

    out_dir: str = os.path.join(cfg.local_root, "g_regex_neg_out")
    _ = _clear_dir(out_dir, ensure_under=cfg.local_root)

    hosts_path: str = _write_temp_hosts([host])
    argv: List[str] = (
        cfg.gm_gather_cmd
        + ["-H", hosts_path, "-u", user, "--pack"]
        + (["-v"] if cfg.verbose else [])
        + ["--", pattern, out_dir]
    )
    run: LocalRun = _run_local_argv(argv)

    host_label: str = host
    exp_x: str = os.path.join(out_dir, host_label, os.path.join(src_dir, "x.txt").lstrip(os.sep))
    exp_bak: str = os.path.join(out_dir, host_label, os.path.join(src_dir, "x.txt.bak").lstrip(os.sep))

    ok_x: bool = os.path.isfile(exp_x)
    ng_bak: bool = not os.path.exists(exp_bak)

    passed: bool = (run.rc == 0 and ok_x and ng_bak)
    reason: str = "" if passed else f"rc={run.rc}, ok_x={ok_x}, ng_bak={ng_bak}, exp_x={exp_x}, exp_bak={exp_bak}"
    return {"name": name, "passed": passed, "skipped": False, "reason": reason,
            "details": {"rc": run.rc, "argv": " ".join(shlex.quote(a) for a in argv),
                        "exp_x": exp_x, "exp_bak": exp_bak}}


def case_scatter_src_regex_absolute(cfg: Config) -> Dict[str, object]:
    """
    目的:
      scatter の SRC を正規表現として解釈する（絶対パス）挙動の検証。
      - 例: <abs_src_dir>/sub/.*\\.txt -> sub/b.txt のみが対象
    """
    name: str = "scatter_src_regex_absolute"
    host: str = cfg.host_ubuntu
    user: str = cfg.target_user

    # ローカルに検体を作成
    abs_src_dir: str = os.path.join(cfg.local_root, "sc_regex_abs_src")
    abs_src_dir = abs_src_dir.rstrip(os.sep)
    os.makedirs(os.path.join(abs_src_dir, "sub"), exist_ok=True)
    with open(os.path.join(abs_src_dir, "a.txt"), "w", encoding="utf-8") as wf:
        wf.write("A\n")
    with open(os.path.join(abs_src_dir, "sub", "b.txt"), "w", encoding="utf-8") as wf:
        wf.write("B\n")

    # 正規表現 SRC（絶対）: sub 配下の *.txt のみ
    pattern: str = os.path.join(abs_src_dir, "sub") + "/.*\\.txt"

    dest_abs: str = "/tmp/gm_scatter_regex_abs_dest"
    _ = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "rm", "-rf", "--", dest_abs)
    _ = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "mkdir", "-p", "--", dest_abs)
    _ = ssh_sudo(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "chown", "-R", "--", f"{user}:{user}", dest_abs)

    hosts_path: str = _write_temp_hosts([host])
    argv: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "--pack"]
        + (["-v"] if cfg.verbose else [])
        + ["--", pattern, dest_abs]
    )
    run: LocalRun = _run_local_argv(argv)

    # 期待: sub/b.txt は転送される / a.txt は転送されない
    exp_b: str = os.path.join(dest_abs, os.path.abspath(os.path.join(abs_src_dir, "sub", "b.txt")).lstrip(os.sep))
    exp_a: str = os.path.join(dest_abs, os.path.abspath(os.path.join(abs_src_dir, "a.txt")).lstrip(os.sep))

    rb = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "test", "-f", exp_b)
    ra = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "test", "-f", exp_a)

    passed: bool = (run.rc == 0 and rb.returncode == 0 and ra.returncode != 0)
    reason: str = "" if passed else (
        f"rc={run.rc}, b_rc={rb.returncode}, a_rc={ra.returncode}, exp_b={exp_b}, exp_a={exp_a}"
    )
    return {
        "name": name,
        "passed": passed,
        "skipped": False,
        "reason": reason,
        "details": {"rc": run.rc, "argv": " ".join(shlex.quote(a) for a in argv)},
    }

def case_scatter_src_regex_relative(cfg: Config) -> Dict[str, object]:
    """
    目的:
      scatter の SRC 正規表現（相対パス）挙動の検証。
      - 例: sc_layout_rel_src/sub/.* -> sub/b.txt のみが対象
    """
    name: str = "scatter_src_regex_relative"
    host: str = cfg.host_ubuntu
    user: str = cfg.target_user

    # カレント配下に相対検体を作成
    rel_base: str = "sc_layout_rel_src"
    cwd: str = os.getcwd()
    rel_dir_abs: str = os.path.abspath(os.path.join(cwd, rel_base))
    os.makedirs(os.path.join(rel_dir_abs, "sub"), exist_ok=True)
    with open(os.path.join(rel_dir_abs, "a.txt"), "w", encoding="utf-8") as wf:
        wf.write("A\n")
    with open(os.path.join(rel_dir_abs, "sub", "b.txt"), "w", encoding="utf-8") as wf:
        wf.write("B\n")

    # 正規表現 SRC（相対）: sub 配下のみ
    pattern: str = rel_base + "/sub/.*"

    dest_abs: str = "/tmp/gm_scatter_regex_rel_dest"
    _ = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "rm", "-rf", "--", dest_abs)
    _ = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "mkdir", "-p", "--", dest_abs)
    _ = ssh_sudo(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "chown", "-R", "--", f"{user}:{user}", dest_abs)

    hosts_path: str = _write_temp_hosts([host])
    argv: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "--pack"]
        + (["-v"] if cfg.verbose else [])
        + ["--", pattern, dest_abs]
    )
    run: LocalRun = _run_local_argv(argv)

    # 期待パス: base_abs を起点に絶対→_as_posix_rel()で DEST 直下にぶら下がる
    exp_b: str = os.path.join(dest_abs, _as_posix_rel(os.path.join(rel_dir_abs, "sub", "b.txt")))
    exp_a: str = os.path.join(dest_abs, _as_posix_rel(os.path.join(rel_dir_abs, "a.txt")))

    rb = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "test", "-f", exp_b)
    ra = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "test", "-f", exp_a)

    passed: bool = (run.rc == 0 and rb.returncode == 0 and ra.returncode != 0)
    reason: str = "" if passed else (
        f"rc={run.rc}, b_rc={rb.returncode}, a_rc={ra.returncode}, exp_b={exp_b}, exp_a={exp_a}"
    )

    # === ここから診断用スナップショット採取 ===
    snap_dest: Dict[str, str] = _remote_script_snapshot(
        cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, dest_abs
    )
    # 期待パスの存在チェックとツリー/メタ情報を一括採取（whoami/pwd/umask なども含む）
    snap_verbose: Dict[str, str] = snapshot_scatter_dest_verbose(
        cfg, host, dest_abs, expected_paths=[exp_b, exp_a]
    )
    # === ここまで追記 ===

    return {
        "name": name,
        "passed": passed,
        "skipped": False,
        "reason": reason,
        "details": {
            "rc": run.rc,
            "argv": " ".join(shlex.quote(a) for a in argv),
            "exp_b": exp_b,
            "exp_a": exp_a,
            # 失敗時の深掘りに使えるスナップショット一式
            "snapshot_dest_stdout": snap_dest.get("stdout", ""),
            "snapshot_dest_stderr": snap_dest.get("stderr", ""),
            "snapshot_dest_rc": snap_dest.get("rc", ""),
            "scatter_dest_verbose_snapshot": snap_verbose,
        },
    }

def case_scatter_src_regex_negative(cfg: Config) -> Dict[str, object]:
    """
    目的:
      誤マッチ防止（アンカー ^/$）の検証。厳密一致のみ許容されること。
      - 例: <abs_src_dir>/^x\\.txt$ -> x.txt のみを許容し、x.txt.bak は除外。
    """
    name: str = "scatter_src_regex_negative"
    host: str = cfg.host_ubuntu
    user: str = cfg.target_user

    # 絶対検体: x.txt と x.txt.bak を用意
    abs_src_dir: str = os.path.join(cfg.local_root, "sc_regex_neg_src")
    os.makedirs(abs_src_dir, exist_ok=True)
    with open(os.path.join(abs_src_dir, "x.txt"), "w", encoding="utf-8") as wf:
        wf.write("X\n")
    with open(os.path.join(abs_src_dir, "x.txt.bak"), "w", encoding="utf-8") as wf:
        wf.write("XB\n")

    # アンカー付き（basename 厳密一致）
    pattern: str = os.path.join(abs_src_dir, "^x\\.txt$")

    dest_abs: str = "/tmp/gm_scatter_regex_neg_dest"
    _ = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "rm", "-rf", "--", dest_abs)
    _ = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "mkdir", "-p", "--", dest_abs)
    _ = ssh_sudo(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "chown", "-R", "--", f"{user}:{user}", dest_abs)

    hosts_path: str = _write_temp_hosts([host])
    argv: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "--pack"]
        + (["-v"] if cfg.verbose else [])
        + ["--", pattern, dest_abs]
    )
    run: LocalRun = _run_local_argv(argv)

    # 期待: x.txt は存在 / x.txt.bak は不在
    exp_x: str = os.path.join(dest_abs, os.path.abspath(os.path.join(abs_src_dir, "x.txt")).lstrip(os.sep))
    exp_bak: str = os.path.join(dest_abs, os.path.abspath(os.path.join(abs_src_dir, "x.txt.bak")).lstrip(os.sep))

    r_x = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "test", "-f", exp_x)
    r_bak = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "test", "-f", exp_bak)

    passed: bool = (run.rc == 0 and r_x.returncode == 0 and r_bak.returncode != 0)
    reason: str = "" if passed else (
        f"rc={run.rc}, x_rc={r_x.returncode}, bak_rc={r_bak.returncode}, exp_x={exp_x}, exp_bak={exp_bak}"
    )
    return {
        "name": name,
        "passed": passed,
        "skipped": False,
        "reason": reason,
        "details": {"rc": run.rc, "argv": " ".join(shlex.quote(a) for a in argv)},
    }

# =========================
# Main
# =========================

def main() -> None:
    cfg: Config = load_config_from_env()
    _ = print_env(cfg)

    results: List[Dict[str, object]] = []
    try:
        _ = _preflight(cfg)

        results.append(case_path_semantics(cfg))

        results.append(case_selinux_auto_ubuntu_skip(cfg))
        results.append(case_selinux_mode_alma(cfg))
        results.append(case_selinux_policy_ignore_on_ubuntu(cfg))
        results.append(case_gather_src_abs_slash_ok(cfg))
        results.append(case_gather_src_abs_tilde_ok(cfg))
        results.append(case_gather_src_rel_home_ok(cfg))
        results.append(case_gather_src_tilde_user_error(cfg))
        results.append(case_scatter_dest_relative_ok_to_home(cfg))
        results.append(case_scatter_dest_abs_variants(cfg))
        results.append(case_gather_follow_symlinks_files(cfg))
        results.append(case_scatter_follow_symlinks_files(cfg))
        results.append(case_scatter_pack_extract_user(cfg))
        results.append(case_scatter_pack_extract_sudo(cfg))
        results.append(case_scatter_pack_extract_sudo_existing_root(cfg))
        results.append(case_gather_double_nesting_regression(cfg))
        results.append(case_scatter_src_path_layout_semantics(cfg))
        results.append(case_scatter_dest_relative_to_remote_home(cfg))
        results.append(case_scatter_dest_tilde_username_rejected(cfg))
        results.append(case_scatter_nonpack_file_only_layout(cfg))
        results.append(case_scatter_mixed_sources_two_hosts(cfg))
        results.append(case_scatter_pack_dedup_roots(cfg))
        results.append(case_scatter_nonpack_same_basename_collision_free(cfg))
        results.append(case_gather_src_regex_absolute(cfg))
        results.append(case_gather_src_regex_relative(cfg))
        results.append(case_gather_src_regex_negative(cfg))
        results.append(case_scatter_src_regex_absolute(cfg))
        results.append(case_scatter_src_regex_relative(cfg))
        results.append(case_scatter_src_regex_negative(cfg))

        print("STEP4 SUMMARY")
        summary: str = json.dumps({"results": results}, indent=2, ensure_ascii=False)
        print(summary)
    finally:
        # 例外の有無に関わらずローカル一時ディレクトリを掃除
        cleanup_local_temps(cfg)

if __name__ == "__main__":
    main()
