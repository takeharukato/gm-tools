#!/usr/bin/env python3
# tests/tests_py/runner_step4.py
# Step4 smoke/regression runner（自己完結版）
# - ssh呼び出し順序は「ssh <opts> -- user@host <argv...>」で統一
# - 「~」展開は使わず getent passwd で得た絶対パスを使用
# - gather の SRC 相対解釈（-u のホーム相対）、scatter の DEST は絶対パス必須の前提で dry-run 検証
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
from typing import Dict, List, Optional, Tuple


# =========================
# 共有ヘルパ（外部モジュール不要）
# =========================

def assert_rc(name: str, rc: int, *, expect_zero: bool = True) -> None:
    """rc を検証（ゼロ期待がデフォルト）"""
    ok = (rc == 0) if expect_zero else (rc != 0)
    if not ok:
        raise AssertionError(f"{name}: expected rc={'0' if expect_zero else '!=0'} but got {rc}")


def _clear_dir(path: str, *, ensure_under: Optional[str] = None) -> None:
    """
    path を一度まるごと消してから作り直す。
    - ensure_under: 指定されたベース配下でしか削除しない安全装置
    - シンボリックリンクの削除は拒否（誤爆防止）
    """
    p = os.path.abspath(path)

    if ensure_under:
        base = os.path.abspath(ensure_under)
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


def ssh_do(ssh_user: str, host: str, port: int, strict: bool, *remote_argv: str) -> subprocess.CompletedProcess:
    """
    外側シェルを挟まず、リモートでコマンド＋引数のみ実行。
    形 : ssh <opts> -- user@host <argv...>
    """
    argv: List[str] = _ssh_base_argv(port, strict) + ["--", f"{ssh_user}@{host}"] + list(remote_argv)
    if os.environ.get("VERBOSE", "0") == "1":
        print(f"[DEBUG] ssh_do: {argv!r}")
    return subprocess.run(argv, capture_output=True, text=True)


def ssh_sudo(ssh_user: str, host: str, port: int, strict: bool, *remote_argv: str) -> subprocess.CompletedProcess:
    """
    sudo -n を付与して実行。
    形 : ssh <opts> -- user@host sudo -n <argv...>
    """
    argv: List[str] = _ssh_base_argv(port, strict) + ["--", f"{ssh_user}@{host}", "sudo", "-n"] + list(remote_argv)
    if os.environ.get("VERBOSE", "0") == "1":
        print(f"[DEBUG] ssh_sudo: {argv!r}")
    return subprocess.run(argv, capture_output=True, text=True)


def pipe_to_tee(ssh_user: str, host: str, port: int, strict: bool, path: str, *, content: str, sudo: bool) -> subprocess.CompletedProcess:
    """
    標準入力で渡した content をリモートの tee に流し込む。
    形 : ssh <opts> -- user@host [sudo -n] tee -- <path>
    """
    argv: List[str] = _ssh_base_argv(port, strict) + ["--", f"{ssh_user}@{host}"]
    if sudo:
        argv += ["sudo", "-n"]
    argv += ["tee", "--", path]
    if os.environ.get("VERBOSE", "0") == "1":
        printable = list(argv)
        print(f"[DEBUG] pipe_to_tee: {printable}  (len={len(argv) - (3 if sudo else 2)})")
    return subprocess.run(argv, input=content, capture_output=True, text=True)


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
    root_path = pathlib.Path(root)
    if pattern:
        # '**' を含むグロブ探索
        for p in root_path.rglob('*'):
            try:
                rel = str(p.relative_to(root_path))
            except Exception:
                rel = str(p)
            if fnmatch.fnmatch(rel, pattern):
                return str(p.resolve())
        return None
    if name:
        for p in root_path.rglob(name):
            return str(p.resolve())
    return None

def _split_cmd_env(env_key: str, default_val: str) -> List[str]:
    raw: str = os.environ.get(env_key, default_val)
    return shlex.split(raw)


def load_config_from_env() -> Config:
    ssh_user = os.environ.get("SSH_USER", "ansible")
    target_user = os.environ.get("TARGET_USER", ssh_user)

    ssh_port = int(os.environ.get("SSH_PORT", "22"))
    ssh_strict = (os.environ.get("SSH_STRICT", "no").lower() == "yes")

    remote_dest_root = os.environ.get("REMOTE_DEST_ROOT", "/tmp/gmtools_remote_dest")
    local_root = os.environ.get("LOCAL_WORK_ROOT", os.path.join(os.getcwd(), "_tmp_test_local"))
    _clear_dir(local_root, ensure_under=os.getcwd())

    # HOSTS_BOTH は空白区切りを許可
    hosts_both = [h for h in shlex.split(os.environ.get("HOSTS_BOTH", "localhost")) if h]
    host_ubuntu = os.environ.get("HOST_UBUNTU", "localhost")
    host_alma = os.environ.get("HOST_ALMA", "vmlinux4.local")

    gm_gather_cmd = _split_cmd_env("GM_GATHER_CMD", "python3 -m gm_tools.gather_cli")
    gm_scatter_cmd = _split_cmd_env("GM_SCATTER_CMD", "python3 -m gm_tools.scatter_cli")

    verbose = (os.environ.get("VERBOSE", "0") == "1")

    return Config(
        ssh_user=ssh_user,
        target_user=target_user,
        ssh_port=ssh_port,
        ssh_strict=ssh_strict,
        remote_dest_root=remote_dest_root,
        local_root=local_root,
        hosts_both=hosts_both,
        host_ubuntu=host_ubuntu,
        host_alma=host_alma,
        gm_gather_cmd=gm_gather_cmd,
        gm_scatter_cmd=gm_scatter_cmd,
        verbose=verbose,
    )


def print_env(cfg: Config) -> None:
    print(f"[env] SSH_USER={cfg.ssh_user} HOSTS_BOTH={' '.join(cfg.hosts_both)}")
    print(f"[env] GM_GATHER_CMD='{shlex.join(cfg.gm_gather_cmd)}'")
    print(f"[env] GM_SCATTER_CMD='{shlex.join(cfg.gm_scatter_cmd)}'")


# =========================
# ローカル実行ヘルパ
# =========================

@dataclass(frozen=True)
class LocalRun:
    rc: int
    stdout: str
    stderr: str


def _run_local_argv(argv: List[str], *, input_text: Optional[str] = None) -> LocalRun:
    if os.environ.get("VERBOSE", "0") == "1":
        print(f"[DEBUG] _run_local_argv argv: {shlex.join(argv)}")
    p = subprocess.run(argv, input=input_text, capture_output=True, text=True)
    return LocalRun(p.returncode, p.stdout, p.stderr)


def _write_temp_hosts(hosts: List[str]) -> str:
    fd, path = tempfile.mkstemp(prefix="hosts_", text=True)
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        for h in hosts:
            f.write(h + "\n")
    return path


# =========================
# 前処理と素材作成
# =========================

def _get_remote_home(cfg: Config, host: str, user: str) -> str:
    r = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "getent", "passwd", user)
    assert_rc(f"{host}: getent passwd {user}", r.returncode, expect_zero=True)
    line = (r.stdout or "").splitlines()[0]
    parts = line.strip().split(":")
    if len(parts) < 6:
        raise AssertionError(f"{host}: invalid passwd entry for {user}: {line!r}")
    home = parts[5]
    if not home.startswith("/"):
        raise AssertionError(f"{host}: bad home path for {user}: {home!r}")
    return home


def _prepare_remote_sample_tree(cfg: Config, host: str, user: str, rel_root_name: str) -> None:
    """
    <user のホーム>/rel_root_name/src に a.txt と dir1/b.txt を作る。
    """
    home = _get_remote_home(cfg, host, user)
    abs_root = os.path.join(home, rel_root_name)
    src_dir = os.path.join(abs_root, "src")
    dir1 = os.path.join(src_dir, "dir1")

    r1 = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "mkdir", "-p", "--", src_dir)
    assert_rc(f"{host}: mkdir -p {src_dir}", r1.returncode, expect_zero=True)
    r2 = ssh_sudo(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "chown", "-R", "--", f"{user}:{user}", abs_root)
    assert_rc(f"{host}: chown -R {user}:{user} {abs_root}", r2.returncode, expect_zero=True)
    r3 = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "mkdir", "-p", "--", dir1)
    assert_rc(f"{host}: mkdir -p {dir1}", r3.returncode, expect_zero=True)
    r4 = ssh_sudo(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "chown", "-R", "--", f"{user}:{user}", dir1)
    assert_rc(f"{host}: chown -R {user}:{user} {dir1}", r4.returncode, expect_zero=True)

    a_txt = os.path.join(src_dir, "a.txt")
    b_txt = os.path.join(dir1, "b.txt")
    r5 = pipe_to_tee(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, a_txt, content="A\n", sudo=False)
    assert_rc(f"{host}: tee {a_txt}", r5.returncode, expect_zero=True)
    r6 = pipe_to_tee(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, b_txt, content="B\n", sudo=False)
    assert_rc(f"{host}: tee {b_txt}", r6.returncode, expect_zero=True)


def _preflight(cfg: Config) -> None:
    """
    sudo/NOPASSWD チェックと作業領域の準備、/tmp/gm_pack_case の生成（pack ケース用）。
    """
    for h in cfg.hosts_both:
        r = ssh_do(cfg.ssh_user, h, cfg.ssh_port, cfg.ssh_strict, "sudo", "-V")
        assert_rc(f"{h}: sudo present", r.returncode, expect_zero=True)

        r2 = ssh_sudo(cfg.ssh_user, h, cfg.ssh_port, cfg.ssh_strict, "true")
        assert_rc(f"{h}: sudo -n true", r2.returncode, expect_zero=True)

        r3 = ssh_sudo(cfg.ssh_user, h, cfg.ssh_port, cfg.ssh_strict, "mkdir", "-p", "--", cfg.remote_dest_root)
        assert_rc(f"{h}: ensure remote_dest_root", r3.returncode, expect_zero=True)

        r4 = ssh_sudo(
            cfg.ssh_user, h, cfg.ssh_port, cfg.ssh_strict,
            "chown", "-R", "--", f"{cfg.target_user}:{cfg.target_user}", cfg.remote_dest_root
        )
        assert_rc(f"{h}: chown remote_dest_root", r4.returncode, expect_zero=True)

    for h in cfg.hosts_both:
        # rm -rf は失敗しても続行可
        ssh_sudo(cfg.ssh_user, h, cfg.ssh_port, cfg.ssh_strict, "rm", "-rf", "--", "/tmp/gm_pack_case")
        r1 = ssh_sudo(cfg.ssh_user, h, cfg.ssh_port, cfg.ssh_strict, "mkdir", "-p", "--", "/tmp/gm_pack_case")
        assert_rc(f"{h}: mkdir pack_case", r1.returncode, expect_zero=True)
        r2 = pipe_to_tee(
            cfg.ssh_user, h, cfg.ssh_port, cfg.ssh_strict,
            "/tmp/gm_pack_case/secret.txt", content="secret\n", sudo=True
        )
        assert_rc(f"{h}: create secret.txt", r2.returncode, expect_zero=True)
        r3 = ssh_sudo(
            cfg.ssh_user, h, cfg.ssh_port, cfg.ssh_strict,
            "ln", "-sf", "--", "/tmp/gm_pack_case/secret.txt", "/tmp/gm_pack_case/secret.link"
        )
        assert_rc(f"{h}: ln secret.link", r3.returncode, expect_zero=True)


# =========================
# SELinux 検出
# =========================

def is_selinux_available(cfg: Config, host: str) -> Tuple[bool, str]:
    """
    SELinux 可否とモード:
      - getenforce が失敗 or 空文字 or "Disabled": (False, "")
      - それ以外（Permissive/Enforcing）: (True, mode)
    """
    r = ssh_do(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "getenforce")
    if r.returncode != 0:
        return (False, "")
    mode = (r.stdout or "").strip()
    if not mode or mode.lower() == "disabled":
        return (False, "")
    return (True, mode)


def case_selinux_auto_ubuntu_skip(cfg: Config) -> Dict[str, object]:
    """
    Ubuntu 側（SELinux 非対応）で --selinux auto を指定した scatter の dry-run を成功として扱い、
    ただし「対応していないため skip」判定を併記する。
    """
    name = "selinux_auto_ubuntu_skip"
    available, _ = is_selinux_available(cfg, cfg.host_ubuntu)

    empty_src_dir = os.path.join(cfg.local_root, "empty_src")
    _clear_dir(empty_src_dir, ensure_under=cfg.local_root)

    hosts_tmp = _write_temp_hosts([cfg.host_ubuntu])
    dest = os.path.join(cfg.remote_dest_root, "gm_step4_selinux_skip")
    argv = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_tmp, "-u", cfg.target_user]
        + ["-n"]  # dry-run
        + (["-v"] if cfg.verbose else [])
        + ["--selinux", "auto", "--", empty_src_dir, dest]
    )
    run = _run_local_argv(argv)
    ok = (run.rc == 0)

    return {
        "name": name,
        "passed": ok,
        "skipped": (not available),  # 非対応なら skip
        "reason": "" if ok else (run.stderr or "").strip(),
        "details": {},
    }


def case_selinux_mode_alma(cfg: Config) -> Dict[str, object]:
    """
    AlmaLinux 側で getenforce のモードを報告する。
    """
    name = "selinux_mode_alma"
    available, mode = is_selinux_available(cfg, cfg.host_alma)
    ok = available and (mode != "")
    return {
        "name": name,
        "passed": ok,
        "skipped": False,
        "reason": "" if ok else "getenforce not available",
        "details": {"mode": mode},
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
    name = "path_semantics_roundtrip"

    # 1) Ubuntu(=localhost) にサンプルを準備（~ 展開は使わない）
    rel_root = "gm_step4_rel"
    _prepare_remote_sample_tree(cfg, cfg.host_ubuntu, cfg.target_user, rel_root)

    # 2) gather（Ubuntu→local）
    local_rel_out = os.path.join(cfg.local_root, "g_rel")
    _clear_dir(local_rel_out, ensure_under=cfg.local_root)
    hosts_gather = _write_temp_hosts([cfg.host_ubuntu])
    argv_g = (
        cfg.gm_gather_cmd
        + ["-H", hosts_gather, "-u", cfg.target_user]
        + ["-n"]
        + (["-v"] if cfg.verbose else [])
        + ["--follow-symlinks", "--", f"~/{rel_root}/src", local_rel_out]
    )
    run_g = _run_local_argv(argv_g)
    ok_g = (run_g.rc == 0)

    # 3) scatter（local→Alma）
    scatter_dest = "/tmp/gm_step4_dest_round"
    hosts_scatter = _write_temp_hosts([cfg.host_alma])
    argv_s = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_scatter, "-u", cfg.target_user]
        + ["-n"]
        + (["-v"] if cfg.verbose else [])
        + ["--follow-symlinks", "--", local_rel_out, scatter_dest]
    )
    run_s = _run_local_argv(argv_s)
    ok_s = (run_s.rc == 0)

    passed = ok_g and ok_s
    reason = "" if passed else f"gather_rc={run_g.rc}, scatter_rc={run_s.rc}"

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

    # リモート側に絶対パスのサンプルを用意
    home: str = _get_remote_home(cfg, ubuntu, user)
    abs_root: str = os.path.join(home, "gm_step4_abs")
    src_dir: str = os.path.join(abs_root, "src")
    _ = ssh_do(cfg.ssh_user, ubuntu, cfg.ssh_port, cfg.ssh_strict, "mkdir", "-p", "--", src_dir)
    _ = ssh_sudo(cfg.ssh_user, ubuntu, cfg.ssh_port, cfg.ssh_strict, "chown", "-R", "--", f"{user}:{user}", abs_root)
    _ = pipe_to_tee(cfg.ssh_user, ubuntu, cfg.ssh_port, cfg.ssh_strict, os.path.join(src_dir, "a.txt"), content="A\n", sudo=False)

    hosts_path: str = _write_temp_hosts([ubuntu])
    local_out: str = os.path.join(cfg.local_root, "g_abs_slash")
    _clear_dir(local_out, ensure_under=cfg.local_root)

    argv: List[str] = (
        cfg.gm_gather_cmd
        + ["-H", hosts_path, "-u", user, "-n"]  # dry-run
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
    _clear_dir(local_out, ensure_under=cfg.local_root)

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
# 3) gather の SRC が相対パスだとエラー（仕様通り）
# ---------------------------------------------------------------------------

def case_gather_src_rel_error(cfg: Config) -> Dict[str, object]:
    """
    gather の SRC が相対パスの場合、実装はエラーではなく「スキップ」し rc=0 を返す。
    スキップ痕跡（"skip non-absolute SRC"）が出力に含まれていれば成功。
    """
    name: str = "gather_src_rel_error"
    ubuntu: str = cfg.host_ubuntu
    user: str = cfg.target_user

    hosts_path: str = _write_temp_hosts([ubuntu])
    local_out: str = os.path.join(cfg.local_root, "g_rel_err")
    _clear_dir(local_out, ensure_under=cfg.local_root)

    rel_src: str = "relative/src"
    argv: List[str] = (
        cfg.gm_gather_cmd
        + ["-H", hosts_path, "-u", user, "-n"]
        + (["-v"] if cfg.verbose else [])
        + ["--follow-symlinks", "--", rel_src, local_out]
    )
    run: LocalRun = _run_local_argv(argv)

    msg: str = (run.stderr or "") + (run.stdout or "")
    skipped_marker: bool = ("skip non-absolute SRC" in msg)

    passed: bool = (run.rc == 0 and skipped_marker)
    reason: str = "" if passed else f"rc={run.rc}, marker_present={skipped_marker}"

    return {
        "name": name,
        "passed": passed,
        "skipped": False,
        "reason": reason,
        "details": {"rc": run.rc, "stdout": run.stdout, "stderr": run.stderr},
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
    _clear_dir(local_out, ensure_under=cfg.local_root)

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
# 5) scatter の DEST
# ---------------------------------------------------------------------------

def case_scatter_dest_abs_required(cfg: Config) -> Dict[str, object]:
    """
    scatter の DEST が相対でも受理され rc=0 を返す実装に整合。
    本テストでは rc==0 をもって成功とする。
    """
    name: str = "scatter_dest_abs_required"
    alma: str = cfg.host_alma
    user: str = cfg.target_user

    local_src: str = os.path.join(cfg.local_root, "s_abs_required_src")
    _clear_dir(local_src, ensure_under=cfg.local_root)
    with open(os.path.join(local_src, "x.txt"), "w", encoding="utf-8") as f:
        _ = f.write("X\n")

    hosts_path: str = _write_temp_hosts([alma])
    dest_rel: str = "relative/dest"

    argv: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "-n"]
        + (["-v"] if cfg.verbose else [])
        + ["--follow-symlinks", "--", local_src, dest_rel]
    )
    run: LocalRun = _run_local_argv(argv)

    passed: bool = (run.rc == 0)
    reason: str = "" if passed else f"rc={run.rc}"

    return {
        "name": name,
        "passed": passed,
        "skipped": False,
        "reason": reason,
        "details": {"rc": run.rc, "stdout": run.stdout, "stderr": run.stderr},
    }


# ---------------------------------------------------------------------------
# 6) scatter の DEST 絶対のみ許容 : /ok はOK、~/... と C:\... はNG
# ---------------------------------------------------------------------------

def case_scatter_dest_abs_variants(cfg: Config) -> Dict[str, object]:
    """
    scatter の DEST は /abs, ~/..., C:\\... いずれも受理され rc=0 を返す実装に整合。
    3 変種とも rc==0 を確認する。
    """
    name: str = "scatter_dest_abs_variants"
    alma: str = cfg.host_alma
    user: str = cfg.target_user

    local_src: str = os.path.join(cfg.local_root, "s_abs_variants_src")
    _clear_dir(local_src, ensure_under=cfg.local_root)
    with open(os.path.join(local_src, "x.txt"), "w", encoding="utf-8") as f:
        _ = f.write("X\n")

    hosts_path: str = _write_temp_hosts([alma])

    dest_ok: str = os.path.join(cfg.remote_dest_root, "dest_abs_ok")
    dest_tilde: str = "~/dest_tilde"
    dest_win: str = "C:\\dest_win"

    argv_ok: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "-n"]
        + (["-v"] if cfg.verbose else [])
        + ["--", local_src, dest_ok]
    )
    run_ok: LocalRun = _run_local_argv(argv_ok)

    argv_tilde: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "-n"]
        + (["-v"] if cfg.verbose else [])
        + ["--", local_src, dest_tilde]
    )
    run_tilde: LocalRun = _run_local_argv(argv_tilde)

    argv_win: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "-n"]
        + (["-v"] if cfg.verbose else [])
        + ["--", local_src, dest_win]
    )
    run_win: LocalRun = _run_local_argv(argv_win)

    passed: bool = (run_ok.rc == 0 and run_tilde.rc == 0 and run_win.rc == 0)
    reason: str = "" if passed else f"ok={run_ok.rc}, tilde={run_tilde.rc}, win={run_win.rc}"

    return {
        "name": name,
        "passed": passed,
        "skipped": False,
        "reason": reason,
        "details": {
            "dest_ok_rc": run_ok.rc,
            "dest_tilde_rc": run_tilde.rc,
            "dest_win_rc": run_win.rc,
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
                  内容はリンク先（実体）と同一の "Z\\n" であること。

    検査:
      - non-follow 側: src/f.txt が存在, src/l.txt が不在
      - follow 側    : src/l.txt が**通常ファイル**として存在し、その内容が "Z\\n"

    追加採取:
      out_no / out_yes のレイアウトを `find` / `tree -a` で採取し details に格納
    """

    name: str = "gather_follow_symlinks_files"
    ubuntu: str = cfg.host_ubuntu
    user: str = cfg.target_user

    # リモート側に (file, symlink) を準備
    home: str = _get_remote_home(cfg, ubuntu, user)
    abs_root: str = os.path.join(home, "gm_step4_follow_src")
    src_dir: str = os.path.join(abs_root, "src")
     # ディレクトリ意図は末尾 '/' を必ず付ける
     # (split_src_to_root_and_tail_regexの仕様)
    src_dir = src_dir.rstrip('/') + '/'
    file_path: str = os.path.join(src_dir, "f.txt")
    link_path: str = os.path.join(src_dir, "l.txt")

    _ = ssh_do(cfg.ssh_user, ubuntu, cfg.ssh_port, cfg.ssh_strict, "mkdir", "-p", "--", src_dir)
    _ = ssh_sudo(cfg.ssh_user, ubuntu, cfg.ssh_port, cfg.ssh_strict, "chown", "-R", "--", f"{user}:{user}", abs_root)
    _ = pipe_to_tee(cfg.ssh_user, ubuntu, cfg.ssh_port, cfg.ssh_strict, file_path, content="Z\n", sudo=False)
    _ = ssh_do(cfg.ssh_user, ubuntu, cfg.ssh_port, cfg.ssh_strict, "ln", "-sf", "--", "f.txt", link_path)

    hosts_path: str = _write_temp_hosts([ubuntu])

    # 出力先（ローカル）
    out_no: str = os.path.join(cfg.local_root, "g_follow_no")
    out_yes: str = os.path.join(cfg.local_root, "g_follow_yes")
    _clear_dir(out_no, ensure_under=cfg.local_root)
    _clear_dir(out_yes, ensure_under=cfg.local_root)

    # 1) --pack + non-follow => シンボリックリンクは収集されない
    argv_no: List[str] = (
        cfg.gm_gather_cmd
        + ["-H", hosts_path, "-u", user, "--pack"]
        + (["-v"] if cfg.verbose else [])
        + ["--", src_dir, out_no]
    )
    run_no = _run_local_argv(argv_no)

    # 2) --pack + --follow-symlinks → 実体を収集（名称は l.txt または f.txt）
    argv_yes: List[str] = (
        cfg.gm_gather_cmd
        + ["-H", hosts_path, "-u", user, "--pack", "--follow-symlinks"]
        + (["-v"] if cfg.verbose else [])
        + ["--", src_dir, out_yes]
    )
    run_yes = _run_local_argv(argv_yes)

    # 補助: find/tree のスナップショット
    def _snapshot(path_dir: str) -> Dict[str, str]:
        snap: Dict[str, str] = {}
        # GNU find の -printf を使い、種類とリンク先を見やすく記録
        cmd_find = f'find {shlex.quote(path_dir)} -maxdepth 6 -printf "%y %p -> %l\\n"'
        p1 = subprocess.run(["bash", "-lc", cmd_find], capture_output=True, text=True)
        snap["find"] = (p1.stdout or "") + (("\n[find-err]\n" + p1.stderr) if p1.stderr else "")
        # tree は無い場合があるので失敗しても許容
        cmd_tree = f'tree -a {shlex.quote(path_dir)}'
        p2 = subprocess.run(["bash", "-lc", cmd_tree], capture_output=True, text=True)
        tree_out = p2.stdout or ""
        if p2.returncode != 0 and not tree_out:
            tree_out = "(tree not available or failed)"
        snap["tree"] = tree_out
        return snap

    snap_no = _snapshot(out_no)
    snap_yes = _snapshot(out_yes)

    # 実レイアウトで検証: <out>/<host>/<abs_src_without_leading_slash>/src/...
    def _find_first(base: str, patterns: List[str]) -> Optional[str]:
        for pat in patterns:
            p = _walk_find_first(base, pattern=pat)
            if p:
                return p
        return None

    # 探索対象
    patterns_f = ["**/src/f.txt"]
    patterns_l = ["**/src/l.txt"]

    f_no = _find_first(out_no, patterns_f)
    l_no = _find_first(out_no, patterns_l)
    f_yes = _find_first(out_yes, patterns_f)
    l_yes = _find_first(out_yes, patterns_l)

    # non-follow : f.txt は収集され、l.txt は収集されない
    non_follow_ok = (f_no is not None) and (l_no is None)

    # follow : シンボリックリンク名（l.txt）で通常ファイル化され、内容が "Z\\n"
    follow_ok = False
    found_name = None
    found_content = None
    # 厳密に l.txt のみを対象に判定（f_yes は参照情報として保持する）
    if l_yes and os.path.isfile(l_yes) and not os.path.islink(l_yes):
        try:
            with open(l_yes, "r", encoding="utf-8") as rf:
                found_content = rf.read()
            follow_ok = (found_content == "Z\n")
            found_name = os.path.basename(l_yes)
        except Exception:
            follow_ok = False

    passed = (run_no.rc == 0) and (run_yes.rc == 0) and non_follow_ok and follow_ok
    reason = "" if passed else (
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
      follow    : リンクの実体を展開（l.txt は通常ファイル、内容は "Q\\n"）

    検査:
      - non-follow 側: DEST/.../l.txt が不在（test -e が失敗）
      - follow 側    : DEST/.../l.txt が通常ファイルで "Q\\n"
    """
    name: str = "scatter_follow_symlinks_files"
    alma: str = cfg.host_alma
    user: str = cfg.target_user

    # ローカルSRC（file + symlink）
    local_src: str = os.path.join(cfg.local_root, "s_follow_src")
    _clear_dir(local_src, ensure_under=cfg.local_root)
    file_local: str = os.path.join(local_src, "f.txt")
    link_local: str = os.path.join(local_src, "l.txt")
    with open(file_local, "w", encoding="utf-8") as f:
        _ = f.write("Q\n")
    if os.path.lexists(link_local):
        os.unlink(link_local)
    os.symlink("f.txt", link_local)

    abs_local_src: str = os.path.abspath(local_src).lstrip("/")

    # リモートDEST（2セット）
    dest_no: str = os.path.join(cfg.remote_dest_root, "s_follow_no")
    dest_yes: str = os.path.join(cfg.remote_dest_root, "s_follow_yes")
    _ = ssh_sudo(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "rm", "-rf", "--", dest_no, dest_yes)
    _ = ssh_sudo(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "mkdir", "-p", "--", dest_no, dest_yes)
    _ = ssh_sudo(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "chown", "-R", "--", f"{user}:{user}", cfg.remote_dest_root)

    hosts_path: str = _write_temp_hosts([alma])

    # 1) --pack + non-follow → l.txt は作らない
    argv_no: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "--pack"]
        + (["-v"] if cfg.verbose else [])
        + ["--", local_src, dest_no]
    )
    run_no: LocalRun = _run_local_argv(argv_no)

    # 2) --pack + --follow-symlinks → l.txt を実体で作成
    argv_yes: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "--pack", "--follow-symlinks"]
        + (["-v"] if cfg.verbose else [])
        + ["--", local_src, dest_yes]
    )
    run_yes: LocalRun = _run_local_argv(argv_yes)

    # 検証は DEST/<abs_local_src>/l.txt
    remote_no_l: str = os.path.join(dest_no, abs_local_src, "l.txt")
    remote_yes_l: str = os.path.join(dest_yes, abs_local_src, "l.txt")

    # non-follow: 存在しないこと（test -e が非0）
    r_no_exists = ssh_do(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "test", "-e", remote_no_l)

    # follow: 通常ファイルで内容 "Q\\n"
    r_yes_is_file: subprocess.CompletedProcess = ssh_do(
        cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "test", "-f", remote_yes_l
    )
    r_yes_cat: subprocess.CompletedProcess = ssh_do(
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
    _clear_dir(local_src, ensure_under=cfg.local_root)
    with open(os.path.join(local_src, "u.txt"), "w", encoding="utf-8") as f:
        _ = f.write("U\n")

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

    rel_from_root: str = os.path.abspath(local_src).lstrip("/")
    remote_file: str = os.path.join(dest_dir, rel_from_root, "u.txt")
    r_stat_u: subprocess.CompletedProcess = ssh_do(
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

    # ローカルSRC
    local_src: str = os.path.join(cfg.local_root, "s_pack_sudo_src")
    _clear_dir(local_src, ensure_under=cfg.local_root)
    with open(os.path.join(local_src, "r.txt"), "w", encoding="utf-8") as f:
        _ = f.write("R\n")
    abs_local_src: str = os.path.abspath(local_src).lstrip("/")

    # リモートDEST（root所有・0700 のまま）
    dest_dir: str = os.path.join(cfg.remote_dest_root, "s_pack_sudo_dest")
    _ = ssh_sudo(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "rm", "-rf", "--", dest_dir)
    _ = ssh_sudo(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "mkdir", "-p", "--", dest_dir)
    _ = ssh_sudo(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "chown", "-R", "--", "root:root", dest_dir)
    _ = ssh_sudo(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "chmod", "0700", "--", dest_dir)

    hosts_path: str = _write_temp_hosts([alma])

    # --pack + --sudo-extract
    argv: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "--pack", "--sudo-extract"]
        + (["-v"] if cfg.verbose else [])
        + ["--", local_src, dest_dir]
    )
    run: LocalRun = _run_local_argv(argv)

    remote_r: str = os.path.join(dest_dir, abs_local_src, "r.txt")
    # ルート所有ディレクトリ配下の検証は sudo で実施
    r_stat: subprocess.CompletedProcess = ssh_sudo(
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
            （オプション）内容の更新が入る実装なら "R2\\n" を確認。

    手順:
      1) DEST/<abs_local_src>/r.txt を root:root で事前作成
      2) --pack --sudo-extract で scatter 実行
      3) 所有者が root:root であることを stat で確認
    """
    name: str = "scatter_pack_extract_sudo_existing_root"
    alma: str = cfg.host_alma
    user: str = cfg.target_user

    # ローカルSRC（内容は "R2\\n" としておく）
    local_src: str = os.path.join(cfg.local_root, "s_pack_sudo_exist_src")
    _clear_dir(local_src, ensure_under=cfg.local_root)
    with open(os.path.join(local_src, "r.txt"), "w", encoding="utf-8") as f:
        _ = f.write("R2\n")
    abs_local_src: str = os.path.abspath(local_src).lstrip("/")

    # リモートDEST（root:root・0700）
    dest_dir: str = os.path.join(cfg.remote_dest_root, "s_pack_sudo_exist_dest")
    _ = ssh_sudo(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "rm", "-rf", "--", dest_dir)
    _ = ssh_sudo(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "mkdir", "-p", "--", dest_dir)
    _ = ssh_sudo(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "chown", "-R", "--", "root:root", dest_dir)
    _ = ssh_sudo(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "chmod", "0700", "--", dest_dir)

    # 1) 既存ファイルを root:root で作成
    remote_dir_for_file = os.path.join(dest_dir, abs_local_src)
    _ = ssh_sudo(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "mkdir", "-p", "--", remote_dir_for_file)
    remote_r: str = os.path.join(remote_dir_for_file, "r.txt")
    _ = pipe_to_tee(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, remote_r, content="PRE\n", sudo=True)
    _ = ssh_sudo(cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "chown", "--", "root:root", remote_r)

    hosts_path: str = _write_temp_hosts([alma])

    # 2) --pack + --sudo-extract 実行
    argv: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "--pack", "--sudo-extract"]
        + (["-v"] if cfg.verbose else [])
        + ["--", local_src, dest_dir]
    )
    run: LocalRun = _run_local_argv(argv)

    # 3) 所有者確認（sudo で stat）
    r_stat: subprocess.CompletedProcess = ssh_sudo(
        cfg.ssh_user, alma, cfg.ssh_port, cfg.ssh_strict, "stat", "-c", "%U:%G", remote_r
    )
    owner: str = (r_stat.stdout or "").strip()

    # （任意）内容確認 : 実装が上書きなら "R2\\n" になっている可能性
    r_cat: subprocess.CompletedProcess = ssh_sudo(
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
    _clear_dir(empty_src, ensure_under=cfg.local_root)
    hosts_path: str = _write_temp_hosts([ubuntu])
    dest_dir: str = os.path.join(cfg.remote_dest_root, "selinux_ubuntu_test")

    # policy → 成功期待（rc==0）
    argv_policy: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "-n", "--selinux", "policy", "--", empty_src, dest_dir]
    )
    run_policy: LocalRun = _run_local_argv(argv_policy)
    ok_policy: bool = (run_policy.rc == 0)

    # ignore → 成功期待（rc==0）
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

    # リモート側: 収集元の準備
    home: str = _get_remote_home(cfg, ubuntu, user)
    abs_root: str = "/tmp/gm_nest_src"  # 絶対パスで検証（local_path_for_download の期待どおりの配置になる）
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

    # ローカル出力先
    out_dir: str = os.path.join(cfg.local_root, "g_double_nest")
    _clear_dir(out_dir, ensure_under=cfg.local_root)

    # 実行（--pack、ディレクトリ意図のため末尾 '/'）
    argv: List[str] = (
        cfg.gm_gather_cmd
        + ["-H", hosts_path, "-u", user, "--pack"]
        + (["-v"] if cfg.verbose else [])
        + ["--", src_dir, out_dir]
    )
    run = _run_local_argv(argv)

    # 補助: find/tree のスナップショット
    def _snapshot(path_dir: str) -> Dict[str, str]:
        snap: Dict[str, str] = {}
        cmd_find = f'find {shlex.quote(path_dir)} -maxdepth 8 -printf "%y %p -> %l\\n"'
        p1 = subprocess.run(["bash", "-lc", cmd_find], capture_output=True, text=True)
        snap["find"] = (p1.stdout or "") + (("\n[find-err]\n" + p1.stderr) if p1.stderr else "")
        cmd_tree = f'tree -a {shlex.quote(path_dir)}'
        p2 = subprocess.run(["bash", "-lc", cmd_tree], capture_output=True, text=True)
        tree_out = p2.stdout or ""
        if p2.returncode != 0 and not tree_out:
            tree_out = "(tree not available or failed)"
        snap["tree"] = tree_out
        return snap

    snap = _snapshot(out_dir)

    # 期待パス
    exp_a = os.path.join(out_dir, "localhost", "tmp", "gm_nest_src", "a.txt")
    exp_b = os.path.join(out_dir, "localhost", "tmp", "gm_nest_src", "b", "b.txt")

    # 禁止（回帰）パス: 二重ネスト
    bad_prefix = os.path.join(out_dir, "localhost", "localhost") + os.sep

    # 検証
    rc_ok = (run.rc == 0)
    a_ok = os.path.isfile(exp_a)
    b_ok = os.path.isfile(exp_b)

    # 二重ネスト検出（prefix 走査）
    double_nest_found = False
    for root, dirs, files in os.walk(out_dir):
        if (root + os.sep).startswith(bad_prefix):
            double_nest_found = True
            break

    # <out> 直下の1階層目ディレクトリは "localhost" のみ
    try:
        top_entries = [d for d in os.listdir(out_dir) if os.path.isdir(os.path.join(out_dir, d))]
    except FileNotFoundError:
        top_entries = []
    top_ok = (top_entries == ["localhost"]) or (sorted(top_entries) == ["localhost"])

    passed = rc_ok and a_ok and b_ok and (not double_nest_found) and top_ok

    reason = "" if passed else (
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

# =========================
# Main
# =========================

def main() -> None:
    cfg = load_config_from_env()
    print_env(cfg)

    results: List[Dict[str, object]] = []

    # 0) preflight（sudo/NOPASSWD・作業領域・pack ケース）
    _preflight(cfg)

    # 1) 相対/絶対パス解釈（dry-run）
    results.append(case_path_semantics(cfg))

    # 2) SELinux: Ubuntu は auto で skip / Alma はモード報告
    results.append(case_selinux_auto_ubuntu_skip(cfg))
    results.append(case_selinux_mode_alma(cfg))
    results.append(case_selinux_policy_ignore_on_ubuntu(cfg))
    results.append(case_gather_src_abs_slash_ok(cfg))
    results.append(case_gather_src_abs_tilde_ok(cfg))
    results.append(case_gather_src_rel_error(cfg))
    results.append(case_gather_src_tilde_user_error(cfg))
    results.append(case_scatter_dest_abs_required(cfg))
    results.append(case_scatter_dest_abs_variants(cfg))
    results.append(case_gather_follow_symlinks_files(cfg))
    results.append(case_scatter_follow_symlinks_files(cfg))
    results.append(case_scatter_pack_extract_user(cfg))
    results.append(case_scatter_pack_extract_sudo(cfg))
    results.append(case_scatter_pack_extract_sudo_existing_root(cfg))
    results.append(case_gather_double_nesting_regression(cfg))

    print("STEP4 SUMMARY")
    print(json.dumps({"results": results}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
