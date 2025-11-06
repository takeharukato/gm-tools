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
import shlex
import subprocess
import tempfile
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


def _ssh_base_argv(port: int, strict: bool) -> List[str]:
    """ssh のベース引数（オプションのみ）"""
    argv: List[str] = ["ssh", "-p", str(port), "-o", f"StrictHostKeyChecking={'yes' if strict else 'no'}"]
    return argv


def ssh_do(ssh_user: str, host: str, port: int, strict: bool, *remote_argv: str) -> subprocess.CompletedProcess:
    """
    外側シェルを挟まず、リモートでコマンド＋引数のみ実行。
    形：ssh <opts> -- user@host <argv...>
    """
    argv: List[str] = _ssh_base_argv(port, strict) + ["--", f"{ssh_user}@{host}"] + list(remote_argv)
    if os.environ.get("VERBOSE", "0") == "1":
        print(f"[DEBUG] ssh_do: {argv!r}")
    return subprocess.run(argv, capture_output=True, text=True)


def ssh_sudo(ssh_user: str, host: str, port: int, strict: bool, *remote_argv: str) -> subprocess.CompletedProcess:
    """
    sudo -n を付与して実行。
    形：ssh <opts> -- user@host sudo -n <argv...>
    """
    argv: List[str] = _ssh_base_argv(port, strict) + ["--", f"{ssh_user}@{host}", "sudo", "-n"] + list(remote_argv)
    if os.environ.get("VERBOSE", "0") == "1":
        print(f"[DEBUG] ssh_sudo: {argv!r}")
    return subprocess.run(argv, capture_output=True, text=True)


def pipe_to_tee(ssh_user: str, host: str, port: int, strict: bool, path: str, *, content: str, sudo: bool) -> subprocess.CompletedProcess:
    """
    標準入力で渡した content をリモートの tee に流し込む。
    形：ssh <opts> -- user@host [sudo -n] tee -- <path>
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
    os.makedirs(local_root, exist_ok=True)

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
    os.makedirs(empty_src_dir, exist_ok=True)

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
    gather: (Ubuntu) ~/gm_step4_rel/src（相対＝-u のホーム相対）→ ローカル
    scatter: (Alma)   ローカル → /tmp/gm_step4_dest_round（絶対パス）
    いずれも --follow-symlinks と -n（dry-run）で Plan の生成だけを確認。
    """
    name = "path_semantics_roundtrip"

    # 1) Ubuntu(=localhost) にサンプルを準備（~ 展開は使わない）
    rel_root = "gm_step4_rel"
    _prepare_remote_sample_tree(cfg, cfg.host_ubuntu, cfg.target_user, rel_root)

    # 2) gather（Ubuntu→local）
    local_rel_out = os.path.join(cfg.local_root, "g_rel")
    os.makedirs(local_rel_out, exist_ok=True)
    hosts_gather = _write_temp_hosts([cfg.host_ubuntu])
    argv_g = (
        cfg.gm_gather_cmd
        + ["-H", hosts_gather, "-u", cfg.target_user]
        + ["-n"]
        + (["-v"] if cfg.verbose else [])
        + ["--follow-symlinks", "--", "tmp/gm_step4_rel/src", local_rel_out]
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

    print("STEP4 SUMMARY")
    print(json.dumps({"results": results}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
