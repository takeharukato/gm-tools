#!/usr/bin/env python3
# tests/tests_py/runner_step4.py
# Step4 smoke/regression runner（共通フレームワーク統合版）
# - ssh呼び出し順序は「ssh <opts> -- user@host <argv...>」で統一
# - 「~」展開は使わず getent passwd で得た絶対パスを使用
# - gather の SRC 相対解釈（-u のホーム相対）
# - scatter の DEST は「絶対パス推奨」。相対はリモート HOME 基準に解決（dry-run も受理）
# - Ubuntu 側の SELinux=auto は「対応していなければ成功扱いでスキップ」
# - Alma 側は getenforce の結果を報告
from __future__ import annotations

import os
import shlex
from typing import Dict, List, Optional, Tuple, IO, Callable, Any, Union
from ._local_types import Config, CaseResult, CommandResult, LocalRun
from .asserts import assert_rc  # 共有のassertを使用
from .test_common_config import load_config_from_env as load_common_config, print_env
from .test_common_runner import run_cases
from .test_common_cleanup import create_clean_dir
from .test_common_ssh import (
    ssh_run as _ssh_run_common,
    ssh_run_sudo as _ssh_run_sudo_common,
    ssh_pipe_to_tee as _ssh_pipe_to_tee_common,
    ssh_get_remote_home,
)
from .test_common_snapshot import (
    local_find_tree as _local_find_tree,
    remote_find_tree_script as _remote_find_tree_script,
    snapshot_scatter_dest_verbose as _snapshot_scatter_dest_verbose,
)
from .test_common_local import cleanup_local_temps as _cleanup_local_temps
from .test_common_local import run_local_with_argv as _run_local_argv
from .test_common_paths import walk_find_first, as_posix_rel

from .test_common_hosts import write_temp_hosts as _write_temp_hosts

def cleanup_local_temps(cfg: Config) -> None:
    # 共有のクリーンアップ関数に委譲（Step4は相対作業ディレクトリも併せて削除）
    _cleanup_local_temps(cfg, rel_dirs=["nf_rel", "nonpack_rel_dir", "sc_layout_rel_src"])


## moved to test_common_snapshot.snapshot_scatter_dest_verbose

# =========================
# 共有ヘルパ（外部モジュール不要）
# =========================

# asserts.assert_rc を使用するためローカル定義を削除


## moved to test_common_cleanup.create_clean_dir

## 共有 SSH ラッパを直接使用する。



# =========================
# 設定読み込み
# =========================

# NOTE: Config は tests/tests_py/_local_types.py からインポートした共有定義を使用する。

## _walk_find_first は test_common_paths.walk_find_first を使用

def load_config_from_env() -> Config:
        """共有ローダをそのまま利用して Config を取得 (Step4 用)。"""
        cfg: Config = load_common_config(clear_local_root=True)
        return cfg

## moved to test_common_config.print_env

# =========================
# 前処理と素材作成
# =========================

## moved to test_common_ssh.ssh_get_remote_home


def _prepare_remote_sample_tree(cfg: Config, host: str, user: str, rel_root_name: str) -> None:
    """
    <user のホーム>/rel_root_name/src に a.txt と dir1/b.txt を作る。
    """
    home: str = ssh_get_remote_home(cfg, host, user)
    abs_root: str = os.path.join(home, rel_root_name)
    src_dir: str = os.path.join(abs_root, "src")
    dir1: str = os.path.join(src_dir, "dir1")

    r1: CommandResult = _ssh_run_common(cfg, host, ["mkdir", "-p", "--", src_dir])
    assert_rc(f"{host}: mkdir -p {src_dir}", r1.rc, expect_zero=True)
    r2: CommandResult = _ssh_run_sudo_common(cfg, host, ["chown", "-R", "--", f"{user}:{user}", abs_root])
    assert_rc(f"{host}: chown -R {user}:{user} {abs_root}", r2.rc, expect_zero=True)
    r3: CommandResult = _ssh_run_common(cfg, host, ["mkdir", "-p", "--", dir1])
    assert_rc(f"{host}: mkdir -p {dir1}", r3.rc, expect_zero=True)
    r4: CommandResult = _ssh_run_sudo_common(cfg, host, ["chown", "-R", "--", f"{user}:{user}", dir1])
    assert_rc(f"{host}: chown -R {user}:{user} {dir1}", r4.rc, expect_zero=True)

    a_txt: str = os.path.join(src_dir, "a.txt")
    b_txt: str = os.path.join(dir1, "b.txt")
    r5: CommandResult = _ssh_pipe_to_tee_common(cfg, host, a_txt, "A\n", sudo=False)
    assert_rc(f"{host}: tee {a_txt}", r5.rc, expect_zero=True)
    r6: CommandResult = _ssh_pipe_to_tee_common(cfg, host, b_txt, "B\n", sudo=False)
    assert_rc(f"{host}: tee {b_txt}", r6.rc, expect_zero=True)


def _preflight(cfg: Config) -> None:
    """
    sudo/NOPASSWD チェックと作業領域の準備、/tmp/gm_pack_case の生成（pack ケース用）。
    """
    i: int = 0
    n: int = len(cfg.hosts_both)
    while i < n:
        h: str = cfg.hosts_both[i]
        r: CommandResult = _ssh_run_common(cfg, h, ["sudo", "-V"])
        assert_rc(f"{h}: sudo present", r.rc, expect_zero=True)

        r2: CommandResult = _ssh_run_sudo_common(cfg, h, ["true"])
        assert_rc(f"{h}: sudo -n true", r2.rc, expect_zero=True)

        r3: CommandResult = _ssh_run_sudo_common(cfg, h, ["mkdir", "-p", "--", cfg.remote_dest_root])
        assert_rc(f"{h}: ensure remote_dest_root", r3.rc, expect_zero=True)

        r4: CommandResult = _ssh_run_sudo_common(
            cfg, h,
            ["chown", "-R", "--", f"{cfg.target_user}:{cfg.target_user}", cfg.remote_dest_root]
        )
        assert_rc(f"{h}: chown remote_dest_root", r4.rc, expect_zero=True)
        i += 1

    i2: int = 0
    n2: int = len(cfg.hosts_both)
    while i2 < n2:
        h2: str = cfg.hosts_both[i2]
        _ = _ssh_run_sudo_common(cfg, h2, ["rm", "-rf", "--", "/tmp/gm_pack_case"])
        r1: CommandResult = _ssh_run_sudo_common(cfg, h2, ["mkdir", "-p", "--", "/tmp/gm_pack_case"])
        assert_rc(f"{h2}: mkdir pack_case", r1.rc, expect_zero=True)
        r2: CommandResult = _ssh_pipe_to_tee_common(
            cfg, h2,
            "/tmp/gm_pack_case/secret.txt", "secret\n", sudo=True
        )
        assert_rc(f"{h2}: create secret.txt", r2.rc, expect_zero=True)
        r3: CommandResult = _ssh_run_sudo_common(
            cfg, h2,
            ["ln", "-sf", "--", "/tmp/gm_pack_case/secret.txt", "/tmp/gm_pack_case/secret.link"]
        )
        assert_rc(f"{h2}: ln secret.link", r3.rc, expect_zero=True)
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
    r: CommandResult = _ssh_run_common(cfg, host, ["getenforce"])
    if r.rc != 0:
        return (False, "")
    mode: str = (r.stdout or "").strip()
    if not mode or mode.lower() == "disabled":
        return (False, "")
    return (True, mode)


def case_selinux_auto_ubuntu_skip(cfg: Config) -> CaseResult:
    """
    Ubuntu 側（SELinux 非対応）で --selinux auto を指定した scatter の dry-run を成功として扱い、
    ただし「対応していないため skip」判定を併記する。
    """
    name: str = "selinux_auto_ubuntu_skip"
    available: Tuple[bool, str]
    available = is_selinux_available(cfg, cfg.host_ubuntu)
    empty_src_dir: str = os.path.join(cfg.local_root, "empty_src")
    _ = create_clean_dir(empty_src_dir, ensure_under=cfg.local_root)

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

    return CaseResult(
        name=name,
        passed=ok,
        skipped=not available[0],
        reason="" if ok else (run.stderr or "").strip(),
        details={},
    )


def case_selinux_mode_alma(cfg: Config) -> CaseResult:
    """
    AlmaLinux 側で getenforce のモードを報告する。
    """
    name: str = "selinux_mode_alma"
    available: Tuple[bool, str] = is_selinux_available(cfg, cfg.host_alma)
    ok: bool = available[0] and (available[1] != "")
    return CaseResult(
        name=name,
        passed=ok,
        skipped=False,
        reason="" if ok else "getenforce not available",
        details={"mode": available[1]},
    )


# =========================
# パス解釈（roundtrip）ケース
# =========================

def case_path_semantics(cfg: Config) -> CaseResult:
    """
    gather: (Ubuntu) ~/<rel_root>/src (ターゲットユーザのホームディレクトリ絶対パス)→ ローカル
    scatter: (Alma)   ローカル → /tmp/gm_step4_dest_round（絶対パス）
    いずれも --follow-symlinks と -n（dry-run）で Plan の生成だけを確認。
    """
    name: str = "path_semantics_roundtrip"

    rel_root: str = "gm_step4_rel"
    _ = _prepare_remote_sample_tree(cfg, cfg.host_ubuntu, cfg.target_user, rel_root)

    local_rel_out: str = os.path.join(cfg.local_root, "g_rel")
    _ = create_clean_dir(local_rel_out, ensure_under=cfg.local_root)
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
    return CaseResult(
        name=name,
        passed=passed,
        skipped=False,
        reason=reason,
        details=details,
    )

# ---------------------------------------------------------------------------
# 1) gather の SRC が「/」始まりの絶対パスで受理される（dry-run）
# ---------------------------------------------------------------------------

def case_gather_src_abs_slash_ok(cfg: Config) -> CaseResult:
    name: str = "gather_src_abs_slash_ok"
    ubuntu: str = cfg.host_ubuntu
    user: str = cfg.target_user

    home: str = ssh_get_remote_home(cfg, ubuntu, user)
    abs_root: str = os.path.join(home, "gm_step4_abs")
    src_dir: str = os.path.join(abs_root, "src")
    _ = _ssh_run_common(cfg, ubuntu, ["mkdir", "-p", "--", src_dir])
    _ = _ssh_run_sudo_common(cfg, ubuntu, ["chown", "-R", "--", f"{user}:{user}", abs_root])
    _ = _ssh_pipe_to_tee_common(cfg, ubuntu, os.path.join(src_dir, "a.txt"), "A\n", sudo=False)

    hosts_path: str = _write_temp_hosts([ubuntu])
    local_out: str = os.path.join(cfg.local_root, "g_abs_slash")
    _ = create_clean_dir(local_out, ensure_under=cfg.local_root)

    argv: List[str] = (
        cfg.gm_gather_cmd
        + ["-H", hosts_path, "-u", user, "-n"]
        + (["-v"] if cfg.verbose else [])
        + ["--follow-symlinks", "--", src_dir, local_out]
    )
    run: LocalRun = _run_local_argv(argv)
    ok: bool = (run.rc == 0)

    return CaseResult(
        name=name,
        passed=ok,
        skipped=False,
        reason="" if ok else run.stderr.strip(),
        details={"rc": run.rc, "stdout": run.stdout, "stderr": run.stderr},
    )


# ---------------------------------------------------------------------------
# 2) gather の SRC が「~/...」で受理され、ホームに展開される（dry-run）
# ---------------------------------------------------------------------------

def case_gather_src_abs_tilde_ok(cfg: Config) -> CaseResult:
    name: str = "gather_src_abs_tilde_ok"
    ubuntu: str = cfg.host_ubuntu
    user: str = cfg.target_user

    home: str = ssh_get_remote_home(cfg, ubuntu, user)
    abs_root: str = os.path.join(home, "gm_step4_tilde")
    src_dir: str = os.path.join(abs_root, "src")
    _ = _ssh_run_common(cfg, ubuntu, ["mkdir", "-p", "--", src_dir])
    _ = _ssh_run_sudo_common(cfg, ubuntu, ["chown", "-R", "--", f"{user}:{user}", abs_root])
    _ = _ssh_pipe_to_tee_common(cfg, ubuntu, os.path.join(src_dir, "b.txt"), "B\n", sudo=False)

    hosts_path: str = _write_temp_hosts([ubuntu])
    local_out: str = os.path.join(cfg.local_root, "g_abs_tilde")
    _ = create_clean_dir(local_out, ensure_under=cfg.local_root)

    tilde_src: str = f"~/gm_step4_tilde/src"
    argv: List[str] = (
        cfg.gm_gather_cmd
        + ["-H", hosts_path, "-u", user, "-n"]
        + (["-v"] if cfg.verbose else [])
        + ["--follow-symlinks", "--", tilde_src, local_out]
    )
    run: LocalRun = _run_local_argv(argv)
    ok: bool = (run.rc == 0)

    return CaseResult(
        name=name,
        passed=ok,
        skipped=False,
        reason="" if ok else run.stderr.strip(),
        details={"rc": run.rc, "stdout": run.stdout, "stderr": run.stderr},
    )


# ---------------------------------------------------------------------------
# 3) gather の SRC が 相対パスの場合、-u のホーム相対として解釈される
# ---------------------------------------------------------------------------

def case_gather_src_rel_home_ok(cfg: Config) -> CaseResult:
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
    _ = create_clean_dir(local_out, ensure_under=cfg.local_root)

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

    return CaseResult(
        name=name,
        passed=passed,
        skipped=False,
        reason=reason,
        details={"rc": run.rc, "stdout": run.stdout, "stderr": run.stderr, "argv": " ".join(argv)},
    )

# ---------------------------------------------------------------------------
# 4) gather の SRC が ~user/...（他人のホーム相対）はエラー
# ---------------------------------------------------------------------------

def case_gather_src_tilde_user_error(cfg: Config) -> CaseResult:
    name: str = "gather_src_tilde_user_error"
    ubuntu: str = cfg.host_ubuntu
    user: str = cfg.target_user

    hosts_path: str = _write_temp_hosts([ubuntu])
    local_out: str = os.path.join(cfg.local_root, "g_tilde_user_err")
    _ = create_clean_dir(local_out, ensure_under=cfg.local_root)

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

    return CaseResult(
        name=name,
        passed=ok,
        skipped=False,
        reason="" if ok else "gather accepted ~user unexpectedly",
        details={"rc": run.rc, "stdout": run.stdout, "stderr": run.stderr},
    )


# ---------------------------------------------------------------------------
# 5) scatter の DEST 相対→remote_home 展開（--pack / 非dry-run）
# ---------------------------------------------------------------------------

def case_scatter_dest_relative_ok_to_home(cfg: Config) -> CaseResult:
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
    _ = create_clean_dir(local_src, ensure_under=cfg.local_root)
    wf: IO[str]
    with open(os.path.join(local_src, "x.txt"), "w", encoding="utf-8") as wf:
        _ = wf.write("X\n")
    # 期待は「SRC を絶対指定した場合のレイアウト」なので、実行引数も絶対パスで渡す
    local_src_abs: str = os.path.abspath(local_src)
    abs_local_src_rel: str = as_posix_rel(local_src_abs)

    home: str = ssh_get_remote_home(cfg, alma, user)
    dest_rel: str = "relative/dest"
    expected_remote_x: str = os.path.join(home, dest_rel, abs_local_src_rel, "x.txt")

    _ = _ssh_run_sudo_common(cfg, alma, ["rm", "-rf", "--", os.path.join(home, dest_rel)])

    hosts_path: str = _write_temp_hosts([alma])

    argv: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "--pack"]
        + (["-v"] if cfg.verbose else [])
        + ["--follow-symlinks", "--", local_src_abs, dest_rel]
    )
    run: LocalRun = _run_local_argv(argv)

    r_isfile: CommandResult = _ssh_run_common(cfg, alma, ["test", "-f", expected_remote_x])
    r_cat: CommandResult = _ssh_run_common(cfg, alma, ["cat", expected_remote_x])

    passed: bool = (run.rc == 0 and r_isfile.rc == 0 and (r_cat.stdout or "").strip() == "X")
    reason: str = "" if passed else (
        f"rc={run.rc}, isfile_rc={r_isfile.rc}, exp={expected_remote_x!r}, content={r_cat.stdout!r}"
    )

    return CaseResult(
        name=name,
        passed=passed,
        skipped=False,
        reason=reason,
        details={
            "rc": run.rc,
            "expected_remote_x": expected_remote_x,
            "argv": " ".join(shlex.quote(a) for a in argv),
        },
    )


# ---------------------------------------------------------------------------
# 6) scatter の DEST 絶対/~/Windows 変種（--pack / 非dry-run）
# ---------------------------------------------------------------------------

def case_scatter_dest_abs_variants(cfg: Config) -> CaseResult:
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
    _ = create_clean_dir(local_src, ensure_under=cfg.local_root)
    wf: IO[str]
    with open(os.path.join(local_src, "x.txt"), "w", encoding="utf-8") as wf:
        _ = wf.write("X\n")
    # 期待は「SRC を絶対指定した場合のレイアウト」なので、実行引数も絶対パスで渡す
    local_src_abs: str = os.path.abspath(local_src)
    abs_local_rel: str = as_posix_rel(local_src_abs)

    hosts_path: str = _write_temp_hosts([alma])

    dest_ok: str = os.path.join(cfg.remote_dest_root, "dest_abs_ok")
    dest_tilde: str = "~/dest_tilde"
    dest_win: str = "C:\\dest_win"

    argv_ok: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "--pack"]
        + (["-v"] if cfg.verbose else [])
        + ["--", local_src_abs, dest_ok]
    )
    run_ok: LocalRun = _run_local_argv(argv_ok)
    exp_ok: str = os.path.join(dest_ok, abs_local_rel, "x.txt")
    r_ok_isfile: CommandResult = _ssh_run_common(cfg, alma, ["test", "-f", exp_ok])

    argv_tilde: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "--pack"]
        + (["-v"] if cfg.verbose else [])
        + ["--", local_src_abs, dest_tilde]
    )
    run_tilde: LocalRun = _run_local_argv(argv_tilde)
    home: str = ssh_get_remote_home(cfg, alma, user)
    exp_tilde: str = os.path.join(home, "dest_tilde", abs_local_rel, "x.txt")
    r_tilde_isfile: CommandResult = _ssh_run_common(cfg, alma, ["test", "-f", exp_tilde])

    argv_win: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "--pack"]
        + (["-v"] if cfg.verbose else [])
        + ["--", local_src_abs, dest_win]
    )
    run_win: LocalRun = _run_local_argv(argv_win)

    ok_branch: bool = (run_ok.rc == 0 and r_ok_isfile.rc == 0)
    tilde_branch: bool = (run_tilde.rc == 0 and r_tilde_isfile.rc == 0)
    win_branch_rc_only: bool = (run_win.rc == 0)

    passed: bool = (ok_branch and tilde_branch and win_branch_rc_only)
    reason: str = "" if passed else (
        f"/abs(rc={run_ok.rc}, isfile_rc={r_ok_isfile.rc}, exp_ok={exp_ok}); "
        f"~/ (rc={run_tilde.rc}, isfile_rc={r_tilde_isfile.rc}, exp_tilde={exp_tilde}); "
        f"win(rc={run_win.rc})"
    )

    return CaseResult(
        name=name,
        passed=passed,
        skipped=False,
        reason=reason,
        details={
            "dest_ok_rc": run_ok.rc,
            "dest_tilde_rc": run_tilde.rc,
            "dest_win_rc": run_win.rc,
            "exp_ok": exp_ok,
            "exp_tilde": exp_tilde,
            "stdout_ok": run_ok.stdout, "stderr_ok": run_ok.stderr,
            "stdout_tilde": run_tilde.stdout, "stderr_tilde": run_tilde.stderr,
            "stdout_win": run_win.stdout, "stderr_win": run_win.stderr,
        },
    )

# ---------------------------------------------------------------------------
# 7) gather --follow-symlinks 有無で結果差（非dry-run）
# ---------------------------------------------------------------------------

def case_gather_follow_symlinks_files(cfg: Config) -> CaseResult:
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

    home: str = ssh_get_remote_home(cfg, ubuntu, user)
    abs_root: str = os.path.join(home, "gm_step4_follow_src")
    src_dir: str = os.path.join(abs_root, "src")
    src_dir = src_dir.rstrip('/') + '/'
    file_path: str = os.path.join(src_dir, "f.txt")
    link_path: str = os.path.join(src_dir, "l.txt")

    _ = _ssh_run_common(cfg, ubuntu, ["mkdir", "-p", "--", src_dir])
    _ = _ssh_run_sudo_common(cfg, ubuntu, ["chown", "-R", "--", f"{user}:{user}", abs_root])
    _ = _ssh_pipe_to_tee_common(cfg, ubuntu, file_path, "Z\n", sudo=False)
    _ = _ssh_run_common(cfg, ubuntu, ["ln", "-sf", "--", "f.txt", link_path])

    hosts_path: str = _write_temp_hosts([ubuntu])

    out_no: str = os.path.join(cfg.local_root, "g_follow_no")
    out_yes: str = os.path.join(cfg.local_root, "g_follow_yes")
    _ = create_clean_dir(out_no, ensure_under=cfg.local_root)
    _ = create_clean_dir(out_yes, ensure_under=cfg.local_root)

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

    snap_no: Dict[str, str] = _local_find_tree(out_no, maxdepth=6)
    snap_yes: Dict[str, str] = _local_find_tree(out_yes, maxdepth=6)

    def _find_first(base: str, patterns: List[str]) -> Optional[str]:
        idx: int = 0
        total: int = len(patterns)
        while idx < total:
            pat: str = patterns[idx]
            p: Optional[str] = walk_find_first(base, pattern=pat)
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

    return CaseResult(
        name=name,
        passed=passed,
        skipped=False,
        reason=reason,
        details={
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
    )


# 8) scatter --follow-symlinks 有無で結果差（--pack + 展開動作）

def case_scatter_follow_symlinks_files(cfg: Config) -> CaseResult:
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
    _ = create_clean_dir(local_src, ensure_under=cfg.local_root)
    file_local: str = os.path.join(local_src, "f.txt")
    link_local: str = os.path.join(local_src, "l.txt")
    wf: IO[str]
    with open(file_local, "w", encoding="utf-8") as wf:
        _ = wf.write("Q\n")
    if os.path.lexists(link_local):
        os.unlink(link_local)
    os.symlink("f.txt", link_local)

    # 期待は「SRC を絶対指定した場合のレイアウト」なので、実行引数も絶対パスで渡す
    local_src_abs: str = os.path.abspath(local_src)
    abs_local_src: str = as_posix_rel(local_src_abs)

    dest_no: str = os.path.join(cfg.remote_dest_root, "s_follow_no")
    dest_yes: str = os.path.join(cfg.remote_dest_root, "s_follow_yes")
    _ = _ssh_run_sudo_common(cfg, alma, ["rm", "-rf", "--", dest_no, dest_yes])
    _ = _ssh_run_sudo_common(cfg, alma, ["mkdir", "-p", "--", dest_no, dest_yes])
    _ = _ssh_run_sudo_common(cfg, alma, ["chown", "-R", "--", f"{user}:{user}", cfg.remote_dest_root])

    hosts_path: str = _write_temp_hosts([alma])

    argv_no: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "--pack"]
        + (["-v"] if cfg.verbose else [])
        + ["--", local_src_abs, dest_no]
    )
    run_no: LocalRun = _run_local_argv(argv_no)

    argv_yes: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "--pack", "--follow-symlinks"]
        + (["-v"] if cfg.verbose else [])
        + ["--", local_src_abs, dest_yes]
    )
    run_yes: LocalRun = _run_local_argv(argv_yes)

    remote_no_l: str = os.path.join(dest_no, abs_local_src, "l.txt")
    remote_yes_l: str = os.path.join(dest_yes, abs_local_src, "l.txt")

    r_no_exists: CommandResult = _ssh_run_common(cfg, alma, ["test", "-e", remote_no_l])

    r_yes_is_file: CommandResult = _ssh_run_common(
        cfg, alma, ["test", "-f", remote_yes_l]
    )
    r_yes_cat: CommandResult = _ssh_run_common(
        cfg, alma, ["cat", remote_yes_l]
    )

    passed: bool = (
        run_no.rc == 0
        and run_yes.rc == 0
        and r_no_exists.rc != 0
        and r_yes_is_file.rc == 0
        and (r_yes_cat.stdout or "").strip() == "Q"
    )
    reason: str = "" if passed else (
        f"scatter rc no/yes=({run_no.rc}/{run_yes.rc}), "
        f"no_exists_rc={r_no_exists.rc}, file_rc={r_yes_is_file.rc}, "
        f"content={r_yes_cat.stdout!r}, paths(no={remote_no_l}, yes={remote_yes_l})"
    )

    return CaseResult(
        name=name,
        passed=passed,
        skipped=False,
        reason=reason,
        details={
            "no_rc": run_no.rc,
            "yes_rc": run_yes.rc,
            "remote_no_l": remote_no_l,
            "remote_yes_l": remote_yes_l,
        },
    )


# ---------------------------------------------------------------------------
# 11) scatter --pack + ユーザ展開（所有者がユーザ）
# ---------------------------------------------------------------------------

def case_scatter_pack_extract_user(cfg: Config) -> CaseResult:
    name: str = "scatter_pack_extract_user"
    alma: str = cfg.host_alma
    user: str = cfg.target_user

    local_src: str = os.path.join(cfg.local_root, "s_pack_user_src")
    _ = create_clean_dir(local_src, ensure_under=cfg.local_root)
    wf: IO[str]
    with open(os.path.join(local_src, "u.txt"), "w", encoding="utf-8") as wf:
        _ = wf.write("U\n")

    dest_dir: str = os.path.join(cfg.remote_dest_root, "s_pack_user_dest")
    _ = _ssh_run_sudo_common(cfg, alma, ["rm", "-rf", "--", dest_dir])
    _ = _ssh_run_sudo_common(cfg, alma, ["mkdir", "-p", "--", dest_dir])
    _ = _ssh_run_sudo_common(cfg, alma, ["chown", "-R", "--", f"{user}:{user}", dest_dir])

    hosts_path: str = _write_temp_hosts([alma])
    # 期待は絶対SRCのレイアウトなので、実行引数も絶対にする
    local_src_abs: str = os.path.abspath(local_src)
    argv: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "--pack"]
        + (["-v"] if cfg.verbose else [])
        + ["--", local_src_abs, dest_dir]
    )
    run: LocalRun = _run_local_argv(argv)

    rel_from_root: str = as_posix_rel(local_src_abs)
    remote_file: str = os.path.join(dest_dir, rel_from_root, "u.txt")
    r_stat_u: CommandResult = _ssh_run_common(
        cfg, alma, ["stat", "-c", "%U:%G", remote_file]
    )
    owner: str = (r_stat_u.stdout or "").strip()

    passed: bool = (run.rc == 0 and r_stat_u.rc == 0 and owner == f"{user}:{user}")
    reason: str = "" if passed else f"rc={run.rc}, owner={owner!r}"

    return CaseResult(name=name, passed=passed, skipped=False, reason=reason, details={"rc": run.rc, "owner": owner})

# 12) scatter --pack --sudo-extract（未存在 → ユーザ権限で作成される仕様）

def case_scatter_pack_extract_sudo(cfg: Config) -> CaseResult:
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
    _ = create_clean_dir(local_src, ensure_under=cfg.local_root)
    wf: IO[str]
    with open(os.path.join(local_src, "r.txt"), "w", encoding="utf-8") as wf:
        _ = wf.write("R\n")
    abs_local_src: str = as_posix_rel(os.path.abspath(local_src))

    dest_dir: str = os.path.join(cfg.remote_dest_root, "s_pack_sudo_dest")
    _ = _ssh_run_sudo_common(cfg, alma, ["rm", "-rf", "--", dest_dir])
    _ = _ssh_run_sudo_common(cfg, alma, ["mkdir", "-p", "--", dest_dir])
    _ = _ssh_run_sudo_common(cfg, alma, ["chown", "-R", "--", "root:root", dest_dir])
    _ = _ssh_run_sudo_common(cfg, alma, ["chmod", "0700", "--", dest_dir])

    hosts_path: str = _write_temp_hosts([alma])

    # 期待は絶対SRCのレイアウトなので、実行引数も絶対にする
    local_src_abs: str = os.path.abspath(local_src)
    argv: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "--pack", "--sudo-extract"]
        + (["-v"] if cfg.verbose else [])
        + ["--", local_src_abs, dest_dir]
    )
    run: LocalRun = _run_local_argv(argv)

    remote_r: str = os.path.join(dest_dir, abs_local_src, "r.txt")
    r_stat: CommandResult = _ssh_run_sudo_common(
        cfg, alma, ["stat", "-c", "%U:%G", remote_r]
    )
    owner: str = (r_stat.stdout or "").strip()

    passed: bool = (run.rc == 0 and r_stat.rc == 0 and owner == f"{user}:{user}")
    reason: str = "" if passed else (
        f"rc={run.rc}, stat_rc={r_stat.rc}, owner={owner!r}, path={remote_r}"
    )

    return CaseResult(name=name, passed=passed, skipped=False, reason=reason, details={"rc": run.rc, "owner": owner, "remote_r": remote_r})

# 12b) scatter --pack --sudo-extract（既存ファイルあり → root 展開されることを検証）

def case_scatter_pack_extract_sudo_existing_root(cfg: Config) -> CaseResult:
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
    _ = create_clean_dir(local_src, ensure_under=cfg.local_root)
    wf: IO[str]
    with open(os.path.join(local_src, "r.txt"), "w", encoding="utf-8") as wf:
        _ = wf.write("R2\n")
    abs_local_src: str = as_posix_rel(os.path.abspath(local_src))

    dest_dir: str = os.path.join(cfg.remote_dest_root, "s_pack_sudo_exist_dest")
    _ = _ssh_run_sudo_common(cfg, alma, ["rm", "-rf", "--", dest_dir])
    _ = _ssh_run_sudo_common(cfg, alma, ["mkdir", "-p", "--", dest_dir])
    _ = _ssh_run_sudo_common(cfg, alma, ["chown", "-R", "--", "root:root", dest_dir])
    _ = _ssh_run_sudo_common(cfg, alma, ["chmod", "0700", "--", dest_dir])

    remote_dir_for_file: str = os.path.join(dest_dir, abs_local_src)
    _ = _ssh_run_sudo_common(cfg, alma, ["mkdir", "-p", "--", remote_dir_for_file])
    remote_r: str = os.path.join(remote_dir_for_file, "r.txt")
    _ = _ssh_pipe_to_tee_common(cfg, alma, remote_r, "PRE\n", sudo=True)
    _ = _ssh_run_sudo_common(cfg, alma, ["chown", "--", "root:root", remote_r])

    hosts_path: str = _write_temp_hosts([alma])

    # 期待は絶対SRCのレイアウトなので、実行引数も絶対にする
    local_src_abs: str = os.path.abspath(local_src)
    argv: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "--pack", "--sudo-extract"]
        + (["-v"] if cfg.verbose else [])
        + ["--", local_src_abs, dest_dir]
    )
    run: LocalRun = _run_local_argv(argv)

    r_stat: CommandResult = _ssh_run_sudo_common(
        cfg, alma, ["stat", "-c", "%U:%G", remote_r]
    )
    owner: str = (r_stat.stdout or "").strip()

    r_cat: CommandResult = _ssh_run_sudo_common(
        cfg, alma, ["cat", remote_r]
    )
    content_after: str = (r_cat.stdout or "")

    passed: bool = (run.rc == 0 and r_stat.rc == 0 and owner == "root:root")
    reason: str = "" if passed else (
        f"rc={run.rc}, stat_rc={r_stat.rc}, owner={owner!r}, content={content_after!r}, path={remote_r}"
    )

    return CaseResult(name=name, passed=passed, skipped=False, reason=reason, details={"rc": run.rc, "owner": owner, "remote_r": remote_r, "content_after": content_after})

# ---------------------------------------------------------------------------
# 13) Ubuntu: --selinux policy はエラー、--selinux ignore は成功（dry-run）
# ---------------------------------------------------------------------------

def case_selinux_policy_ignore_on_ubuntu(cfg: Config) -> CaseResult:
    """
    Ubuntu 側（SELinux 非対応）で --selinux {policy,ignore} を指定した scatter の dry-run は、
    いずれも rc=0 で成功扱い（実装準拠）。
    """
    name: str = "selinux_policy_ignore_on_ubuntu"
    ubuntu: str = cfg.host_ubuntu
    user: str = cfg.target_user

    empty_src: str = os.path.join(cfg.local_root, "empty_src_selinux")
    _ = create_clean_dir(empty_src, ensure_under=cfg.local_root)
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

    return CaseResult(name=name, passed=passed, skipped=False, reason=reason, details={
        "policy_rc": run_policy.rc,
        "ignore_rc": run_ignore.rc,
        "policy_stdout": run_policy.stdout,
        "policy_stderr": run_policy.stderr,
        "ignore_stdout": run_ignore.stdout,
        "ignore_stderr": run_ignore.stderr,
    })

# ---------------------------------------------------------------------------
# 14) gather の二重ネスト回帰検証
# ---------------------------------------------------------------------------

def case_gather_double_nesting_regression(cfg: Config) -> CaseResult:
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

    _ = _ssh_run_common(cfg, ubuntu, ["rm", "-rf", "--", abs_root])
    _ = _ssh_run_common(cfg, ubuntu, ["mkdir", "-p", "--", file_bdir])
    _ = _ssh_run_sudo_common(cfg, ubuntu, ["chown", "-R", "--", f"{user}:{user}", abs_root])
    _ = _ssh_pipe_to_tee_common(cfg, ubuntu, file_a, "A\n", sudo=False)
    _ = _ssh_pipe_to_tee_common(cfg, ubuntu, file_b, "B\n", sudo=False)

    hosts_path: str = _write_temp_hosts([ubuntu])

    out_dir: str = os.path.join(cfg.local_root, "g_double_nest")
    _ = create_clean_dir(out_dir, ensure_under=cfg.local_root)

    argv: List[str] = (
        cfg.gm_gather_cmd
        + ["-H", hosts_path, "-u", user, "--pack"]
        + (["-v"] if cfg.verbose else [])
        + ["--", src_dir, out_dir]
    )
    run: LocalRun = _run_local_argv(argv)

    snap: Dict[str, str] = _local_find_tree(out_dir)

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

    return CaseResult(
        name=name,
        passed=passed,
        skipped=False,
        reason=reason,
        details={
            "rc": run.rc,
            "argv": " ".join(shlex.quote(a) for a in argv),
            "expected_a": exp_a,
            "expected_b": exp_b,
            "double_nest_bad_prefix": bad_prefix,
            "top_entries": top_entries,
            "snapshot_find": snap.get("find", ""),
            "snapshot_tree": snap.get("tree", ""),
        },
    )

def _remote_script_snapshot(ssh_user: str, host: str, port: int, strict: Union[bool, str], base: str) -> Dict[str, str]:
    """共有ヘルパに委譲して、リモートの find/tree スナップショットを取得する。"""
    return _remote_find_tree_script(ssh_user, host, port, strict, base, maxdepth=8)

def case_scatter_src_path_layout_semantics(cfg: Config) -> CaseResult:
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
    _ = _ssh_run_common(cfg, ubuntu, ["rm", "-rf", "--", dest_abs])
    _ = _ssh_run_common(cfg, ubuntu, ["mkdir", "-p", "--", dest_abs])
    _ = _ssh_run_sudo_common(cfg, ubuntu, ["chown", "-R", "--", f"{user}:{user}", dest_abs])

    hosts_path: str = _write_temp_hosts([ubuntu])

    argv_abs: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "--pack"]
        + (["-v"] if cfg.verbose else [])
        + ["--", abs_src_dir, dest_abs]
    )
    run_abs: LocalRun = _run_local_argv(argv_abs)

    abs_without_leading: str = as_posix_rel(abs_src_dir)
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

    snap = _snapshot_scatter_dest_verbose(
        cfg.ssh_user, ubuntu, cfg.ssh_port, cfg.ssh_strict,
        dest_abs, expected_paths=[exp_abs_a, exp_abs_b, exp_rel_a, exp_rel_b]
    )

    def _remote_is_file(path_abs: str) -> bool:
        r: CommandResult = _ssh_run_common(cfg, ubuntu, ["test", "-f", path_abs])
        return r.rc == 0

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

    return CaseResult(
        name=name,
        passed=passed,
        skipped=False,
        reason=reason,
        details={
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
    )
def case_scatter_dest_relative_to_remote_home(cfg: Config) -> CaseResult:
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

    home: str = ssh_get_remote_home(cfg, host, user)
    abs_without: str = abs_src.lstrip(os.sep)
    exp: str = os.path.join(home, "gm_rel_dest", abs_without, "a.txt")

    r_isfile: CommandResult = _ssh_run_common(cfg, host, ["test", "-f", exp])
    r_cat: CommandResult = _ssh_run_common(cfg, host, ["cat", exp])

    passed: bool = (run.rc == 0 and r_isfile.rc == 0 and (r_cat.stdout or "").strip() == "A")
    reason: str = "" if passed else f"rc={run.rc}, isfile_rc={r_isfile.rc}, exp={exp!r}, content={r_cat.stdout!r}"
    return CaseResult(
        name=name,
        passed=passed,
        skipped=False,
        reason=reason,
        details={"rc": run.rc, "exp": exp, "argv": " ".join(shlex.quote(a) for a in argv)},
    )



def case_scatter_dest_tilde_username_rejected(cfg: Config) -> CaseResult:
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
    _ = create_clean_dir(src_dir, ensure_under=cfg.local_root)
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
    return CaseResult(
        name=name,
        passed=passed,
        skipped=False,
        reason=reason,
        details={"rc": run.rc, "stderr_stdout": out_all, "argv": " ".join(shlex.quote(a) for a in argv)},
    )



def case_scatter_nonpack_file_only_layout(cfg: Config) -> CaseResult:
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

    _ = create_clean_dir(abs_dir, ensure_under=cfg.local_root)
    _ = create_clean_dir(ign_dir, ensure_under=cfg.local_root)
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
    _ = _ssh_run_common(cfg, host, ["rm", "-rf", "--", dest_abs])
    _ = _ssh_run_common(cfg, host, ["mkdir", "-p", "--", dest_abs])
    _ = _ssh_run_sudo_common(cfg, host, ["chown", "-R", "--", f"{user}:{user}", dest_abs])

    hosts_path: str = _write_temp_hosts([host])
    abs_file_abs: str = os.path.abspath(abs_file)
    ign_dir_abs: str = os.path.abspath(ign_dir)
    argv: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user]
        + (["-v"] if cfg.verbose else [])
        + ["--", abs_file_abs, rel_file, ign_dir_abs, dest_abs]
    )
    run: LocalRun = _run_local_argv(argv)

    exp_abs: str = os.path.join(dest_abs, os.path.abspath(abs_file).lstrip(os.sep))
    exp_rel: str = os.path.join(dest_abs, rel_file)
    exp_ign: str = os.path.join(dest_abs, os.path.abspath(ign_dir).lstrip(os.sep))

    r_abs: CommandResult = _ssh_run_common(cfg, host, ["test", "-f", exp_abs])
    r_rel: CommandResult = _ssh_run_common(cfg, host, ["test", "-f", exp_rel])
    r_ign: CommandResult = _ssh_run_common(cfg, host, ["test", "-e", exp_ign])

    passed: bool = (run.rc == 0 and r_abs.rc == 0 and r_rel.rc == 0 and r_ign.rc != 0)
    reason: str = "" if passed else f"rc={run.rc}, abs={r_abs.rc}, rel={r_rel.rc}, ign_exists_rc={r_ign.rc}"
    return CaseResult(
        name=name,
        passed=passed,
        skipped=False,
        reason=reason,
        details={"rc": run.rc, "exp_abs": exp_abs, "exp_rel": exp_rel, "exp_ign": exp_ign,
                 "argv": " ".join(shlex.quote(a) for a in argv)},
    )



def case_scatter_mixed_sources_two_hosts(cfg: Config) -> CaseResult:
    """
    目的:
      複数ホスト一括 scatter（--pack）の回帰。
      hosts ファイルに Ubuntu/Alma を同時に渡し、両方で所定パスに展開されること。
    """
    name: str = "scatter_mixed_sources_two_hosts"

    ubuntu: str = cfg.host_ubuntu
    alma: str = cfg.host_alma
    user: str = cfg.target_user

    src_a: str = os.path.join(cfg.local_root, "mix_src_a")
    src_b: str = os.path.join(cfg.local_root, "mix_src_b")
    src_a = src_a.rstrip(os.sep) + os.sep
    src_b = src_b.rstrip(os.sep) + os.sep
    _ = os.makedirs(src_a, exist_ok=True)
    _ = os.makedirs(src_b, exist_ok=True)
    with open(os.path.join(src_a, "a.txt"), "w", encoding="utf-8") as wf:
        _ = wf.write("A\n")
    with open(os.path.join(src_b, "b.txt"), "w", encoding="utf-8") as wf:
        _ = wf.write("B\n")

    hosts_path: str = _write_temp_hosts([ubuntu, alma])
    dest_abs: str = "/tmp/gm_scatter_mixed_hosts"
    for h in (ubuntu, alma):
        _ = _ssh_run_common(cfg, h, ["rm", "-rf", "--", dest_abs])
        _ = _ssh_run_common(cfg, h, ["mkdir", "-p", "--", dest_abs])
        _ = _ssh_run_sudo_common(cfg, h, ["chown", "-R", "--", f"{user}:{user}", dest_abs])

    src_a_abs: str = os.path.abspath(src_a)
    if not src_a_abs.endswith(os.sep):
        src_a_abs = src_a_abs + os.sep
    src_b_abs: str = os.path.abspath(src_b)
    if not src_b_abs.endswith(os.sep):
        src_b_abs = src_b_abs + os.sep

    argv: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "--pack"]
        + (["-v"] if cfg.verbose else [])
        + ["--", src_a_abs, src_b_abs, dest_abs]
    )
    run: LocalRun = _run_local_argv(argv)

    def _rel(src_dir_abs: str) -> str:
        return as_posix_rel(src_dir_abs)

    rel_a: str = _rel(src_a_abs)
    rel_b: str = _rel(src_b_abs)

    def _remote_path(src_rel: str, fname: str) -> str:
        return os.path.join(dest_abs, src_rel, fname)

    ok_all: bool = True
    for h in (ubuntu, alma):
        for (src_rel, fname) in ((rel_a, "a.txt"), (rel_b, "b.txt")):
            remote_path: str = _remote_path(src_rel, fname)
            r: CommandResult = _ssh_run_common(cfg, h, ["test", "-f", remote_path])
            if r.rc != 0:
                ok_all = False

    passed: bool = (run.rc == 0 and ok_all)
    reason: str = "" if passed else f"rc={run.rc}, ok_all={ok_all}"
    return CaseResult(
        name=name,
        passed=passed,
        skipped=False,
        reason=reason,
        details={
            "rc": run.rc,
            "argv": " ".join(shlex.quote(a) for a in argv),
            "rel_a": rel_a,
            "rel_b": rel_b,
        },
    )

def case_scatter_pack_dedup_roots(cfg: Config) -> CaseResult:
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
    _ = _ssh_run_common(cfg, host, ["rm", "-rf", "--", dest_abs])
    _ = _ssh_run_common(cfg, host, ["mkdir", "-p", "--", dest_abs])
    _ = _ssh_run_sudo_common(cfg, host, ["chown", "-R", "--", f"{user}:{user}", dest_abs])

    hosts_path: str = _write_temp_hosts([host])
    # ホスト行数（診断用）
    _hosts_cnt: int = 0
    try:
        with open(hosts_path, "r", encoding="utf-8") as hf:
            _hosts_cnt = sum(1 for _ in hf if _.strip())
    except Exception:
        _hosts_cnt = -1

    dup_root_abs: str = os.path.abspath(dup_root)
    if not dup_root_abs.endswith(os.sep):
        dup_root_abs = dup_root_abs + os.sep
    argv: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "--pack"]
        + (["-v"] if cfg.verbose else [])
        + ["--", dup_root_abs, os.path.join(dup_root_abs, "sub"), dest_abs]
    )
    run: LocalRun = _run_local_argv(argv)

    # OS 非依存の相対化（先頭スラッシュ除去 + 区切り '/' 化）で一貫性を確保
    dup_root_rel: str = as_posix_rel(dup_root_abs)
    base: str = os.path.join(dest_abs, dup_root_rel)
    exp: str = os.path.join(base, "sub", "n.txt")

    # 期待パス(exp)の存在を確認したうえで、「exp 以外の n.txt が無い」ことを確認
    q_dest = shlex.quote(dest_abs)
    q_exp  = shlex.quote(exp)

    cmd_total  = f'LC_ALL=C find {q_dest} -type f -name n.txt -printf "%p\\n" | wc -l'
    cmd_others = f'LC_ALL=C find {q_dest} -type f -name n.txt ! -path {q_exp} -printf "%p\\n" | wc -l'
    cmd_list   = f'LC_ALL=C find {q_dest} -type f -name n.txt -printf "%p\\n" | sort'

    r_total  = _ssh_run_common(cfg, host, ["bash", "-lc", cmd_total])
    r_others = _ssh_run_common(cfg, host, ["bash", "-lc", cmd_others])
    r_list   = _ssh_run_common(cfg, host, ["bash", "-lc", cmd_list])

    def _to_int(s: str) -> int:
        try:
            return int((s or "0").strip())
        except Exception:
            return -1

    cnt_total: int = _to_int(r_total.stdout)
    cnt_others: int = _to_int(r_others.stdout)

    # DEST全体のスナップショット（HOMEではなくDEST固定）
    snap_dest: Dict[str, str] = _remote_script_snapshot(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, dest_abs)

    r_isfile: CommandResult = _ssh_run_common(cfg, host, ["test", "-f", exp])

    # 合否: 実行成功 + 期待ファイルが存在 + 期待以外の n.txt が 0 件
    passed: bool = (run.rc == 0 and r_isfile.rc == 0 and cnt_others == 0)
    reason: str = "" if passed else (
        f"rc={run.rc}, isfile_rc={r_isfile.rc}, "
        f"total_n_txt={cnt_total}, others_except_exp={cnt_others}, exp={exp}"
    )

    return CaseResult(
        name=name,
        passed=passed,
        skipped=False,
        reason=reason,
        details={
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
    )


def case_scatter_nonpack_same_basename_collision_free(cfg: Config) -> CaseResult:
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
    _ = create_clean_dir(dir_abs, ensure_under=cfg.local_root)
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
    _ = _ssh_run_common(cfg, host, ["rm", "-rf", "--", dest_abs])
    _ = _ssh_run_common(cfg, host, ["mkdir", "-p", "--", dest_abs])
    _ = _ssh_run_sudo_common(cfg, host, ["chown", "-R", "--", f"{user}:{user}", dest_abs])

    hosts_path: str = _write_temp_hosts([host])
    f_abs_abs: str = os.path.abspath(f_abs)
    argv: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user]
        + (["-v"] if cfg.verbose else [])
        + ["--", f_abs_abs, f_rel, dest_abs]
    )
    run: LocalRun = _run_local_argv(argv)

    exp_abs: str = os.path.join(dest_abs, os.path.abspath(f_abs).lstrip(os.sep))
    exp_rel: str = os.path.join(dest_abs, f_rel)

    r1: CommandResult = _ssh_run_common(cfg, host, ["test", "-f", exp_abs])
    r2: CommandResult = _ssh_run_common(cfg, host, ["test", "-f", exp_rel])
    c1: CommandResult = _ssh_run_common(cfg, host, ["cat", exp_abs])
    c2: CommandResult = _ssh_run_common(cfg, host, ["cat", exp_rel])

    passed: bool = (run.rc == 0 and r1.rc == 0 and r2.rc == 0
              and (c1.stdout or "").strip() == "ABS" and (c2.stdout or "").strip() == "REL")
    reason: str = "" if passed else f"rc={run.rc}, r1={r1.rc}, r2={r2.rc}, c1={c1.stdout!r}, c2={c2.stdout!r}"
    return CaseResult(
        name=name,
        passed=passed,
        skipped=False,
        reason=reason,
        details={"rc": run.rc, "exp_abs": exp_abs, "exp_rel": exp_rel,
                 "argv": " ".join(shlex.quote(a) for a in argv)},
    )

def case_gather_src_regex_absolute(cfg: Config) -> CaseResult:
    """
    目的:
      gather の SRC を正規表現として解釈する（絶対パス）挙動の検証。
      - 例: <abs>/src/dir1/.*\\.txt -> dir1/b.txt のみが対象（a.txt は対象外）
    """
    name: str = "gather_src_regex_absolute"
    host: str = cfg.host_ubuntu
    user: str = cfg.target_user

    # リモートに検体を作成: <home>/gm_step4_regex_abs/src/{a.txt, dir1/b.txt}
    home: str = ssh_get_remote_home(cfg, host, user)
    abs_root: str = os.path.join(home, "gm_step4_regex_abs")
    src_dir: str = os.path.join(abs_root, "src")
    dir1: str = os.path.join(src_dir, "dir1")
    _ = _ssh_run_common(cfg, host, ["rm", "-rf", "--", abs_root])
    _ = _ssh_run_common(cfg, host, ["mkdir", "-p", "--", dir1])
    _ = _ssh_run_sudo_common(cfg, host, ["chown", "-R", "--", f"{user}:{user}", abs_root])
    _ = _ssh_pipe_to_tee_common(cfg, host, os.path.join(src_dir, "a.txt"), "A\n", sudo=False)
    _ = _ssh_pipe_to_tee_common(cfg, host, os.path.join(dir1, "b.txt"), "B\n", sudo=False)

    # 正規表現 SRC（絶対）: dir1 配下の *.txt のみ
    pattern: str = os.path.join(src_dir, "dir1") + "/.*\\.txt"

    # ローカル出力先
    out_dir: str = os.path.join(cfg.local_root, "g_regex_abs_out")
    _ = create_clean_dir(out_dir, ensure_under=cfg.local_root)

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
    return CaseResult(
        name=name,
        passed=passed,
        skipped=False,
        reason=reason,
        details={"rc": run.rc, "argv": " ".join(shlex.quote(a) for a in argv),
                 "exp_b": exp_b, "exp_a": exp_a},
    )


def case_gather_src_regex_relative(cfg: Config) -> CaseResult:
    """
    目的:
      gather の SRC 正規表現（相対パス）挙動の検証（-u の HOME 相対）。
      - 例: gm_step4_regex_rel/src/dir1/.* -> dir1/b.txt のみが対象
    """
    name: str = "gather_src_regex_relative"
    host: str = cfg.host_ubuntu
    user: str = cfg.target_user

    # リモートに検体: <home>/gm_step4_regex_rel/src/{a.txt, dir1/b.txt}
    home: str = ssh_get_remote_home(cfg, host, user)
    rel_top: str = "gm_step4_regex_rel"
    abs_root: str = os.path.join(home, rel_top)
    src_dir: str = os.path.join(abs_root, "src")
    dir1: str = os.path.join(src_dir, "dir1")
    _ = _ssh_run_common(cfg, host, ["rm", "-rf", "--", abs_root])
    _ = _ssh_run_common(cfg, host, ["mkdir", "-p", "--", dir1])
    _ = _ssh_run_sudo_common(cfg, host, ["chown", "-R", "--", f"{user}:{user}", abs_root])
    _ = _ssh_pipe_to_tee_common(cfg, host, os.path.join(src_dir, "a.txt"), "A\n", sudo=False)
    _ = _ssh_pipe_to_tee_common(cfg, host, os.path.join(dir1, "b.txt"), "B\n", sudo=False)

    # 正規表現 SRC（相対）: dir1 配下のみ
    pattern_rel: str = f"{rel_top}/src/dir1/.*"

    out_dir: str = os.path.join(cfg.local_root, "g_regex_rel_out")
    _ = create_clean_dir(out_dir, ensure_under=cfg.local_root)

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
    return CaseResult(
        name=name,
        passed=passed,
        skipped=False,
        reason=reason,
        details={"rc": run.rc, "argv": " ".join(shlex.quote(a) for a in argv),
                 "exp_b": exp_b, "exp_a": exp_a},
    )


def case_gather_src_regex_negative(cfg: Config) -> CaseResult:
    """
    目的:
      誤マッチ防止（アンカー ^/$）の検証（絶対パス）。
      - 例: <abs>/src/^x\\.txt$ -> x.txt のみを許容し、x.txt.bak は除外。
    """
    name: str = "gather_src_regex_negative"
    host: str = cfg.host_ubuntu
    user: str = cfg.target_user

    # リモートに検体: <home>/gm_step4_regex_neg/src/{x.txt, x.txt.bak}
    home: str = ssh_get_remote_home(cfg, host, user)
    abs_root: str = os.path.join(home, "gm_step4_regex_neg")
    src_dir: str = os.path.join(abs_root, "src")
    _ = _ssh_run_common(cfg, host, ["rm", "-rf", "--", abs_root])
    _ = _ssh_run_common(cfg, host, ["mkdir", "-p", "--", src_dir])
    _ = _ssh_run_sudo_common(cfg, host, ["chown", "-R", "--", f"{user}:{user}", abs_root])
    _ = _ssh_pipe_to_tee_common(cfg, host, os.path.join(src_dir, "x.txt"), "X\n", sudo=False)
    _ = _ssh_pipe_to_tee_common(cfg, host, os.path.join(src_dir, "x.txt.bak"), "XB\n", sudo=False)

    # アンカー付き SRC: basename 厳密一致のみ
    pattern: str = os.path.join(src_dir, "^x\\.txt$")

    out_dir: str = os.path.join(cfg.local_root, "g_regex_neg_out")
    _ = create_clean_dir(out_dir, ensure_under=cfg.local_root)

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
    return CaseResult(
        name=name,
        passed=passed,
        skipped=False,
        reason=reason,
        details={"rc": run.rc, "argv": " ".join(shlex.quote(a) for a in argv),
                 "exp_x": exp_x, "exp_bak": exp_bak},
    )


def case_scatter_src_regex_absolute(cfg: Config) -> CaseResult:
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
    _ = _ssh_run_common(cfg, host, ["rm", "-rf", "--", dest_abs])
    _ = _ssh_run_common(cfg, host, ["mkdir", "-p", "--", dest_abs])
    _ = _ssh_run_sudo_common(cfg, host, ["chown", "-R", "--", f"{user}:{user}", dest_abs])

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

    rb: CommandResult = _ssh_run_common(cfg, host, ["test", "-f", exp_b])
    ra: CommandResult = _ssh_run_common(cfg, host, ["test", "-f", exp_a])

    passed: bool = (run.rc == 0 and rb.rc == 0 and ra.rc != 0)
    reason: str = "" if passed else (
        f"rc={run.rc}, b_rc={rb.rc}, a_rc={ra.rc}, exp_b={exp_b}, exp_a={exp_a}"
    )
    return CaseResult(
        name=name,
        passed=passed,
        skipped=False,
        reason=reason,
        details={"rc": run.rc, "argv": " ".join(shlex.quote(a) for a in argv)},
    )

def case_scatter_src_regex_relative(cfg: Config) -> CaseResult:
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
    _ = _ssh_run_common(cfg, host, ["rm", "-rf", "--", dest_abs])
    _ = _ssh_run_common(cfg, host, ["mkdir", "-p", "--", dest_abs])
    _ = _ssh_run_sudo_common(cfg, host, ["chown", "-R", "--", f"{user}:{user}", dest_abs])

    hosts_path: str = _write_temp_hosts([host])
    argv: List[str] = (
        cfg.gm_scatter_cmd
        + ["-H", hosts_path, "-u", user, "--pack"]
        + (["-v"] if cfg.verbose else [])
        + ["--", pattern, dest_abs]
    )
    run: LocalRun = _run_local_argv(argv)

    # 期待パス: base_abs を起点に絶対→_as_posix_rel()で DEST 直下にぶら下がる
    exp_b: str = os.path.join(dest_abs, as_posix_rel(os.path.join(rel_dir_abs, "sub", "b.txt")))
    exp_a: str = os.path.join(dest_abs, as_posix_rel(os.path.join(rel_dir_abs, "a.txt")))

    rb: CommandResult = _ssh_run_common(cfg, host, ["test", "-f", exp_b])
    ra: CommandResult = _ssh_run_common(cfg, host, ["test", "-f", exp_a])

    passed: bool = (run.rc == 0 and rb.rc == 0 and ra.rc != 0)
    reason: str = "" if passed else (
        f"rc={run.rc}, b_rc={rb.rc}, a_rc={ra.rc}, exp_b={exp_b}, exp_a={exp_a}"
    )

    # === ここから診断用スナップショット採取 ===
    snap_dest: Dict[str, str] = _remote_script_snapshot(
        cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, dest_abs
    )
    # 期待パスの存在チェックとツリー/メタ情報を一括採取（whoami/pwd/umask なども含む）
    snap_verbose: Dict[str, str] = _snapshot_scatter_dest_verbose(
        cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, dest_abs,
        expected_paths=[exp_b, exp_a]
    )
    # === ここまで追記 ===

    return CaseResult(
        name=name,
        passed=passed,
        skipped=False,
        reason=reason,
        details={
            "rc": run.rc,
            "argv": " ".join(shlex.quote(a) for a in argv),
            "exp_b": exp_b,
            "exp_a": exp_a,
            "snapshot_dest_stdout": snap_dest.get("stdout", ""),
            "snapshot_dest_stderr": snap_dest.get("stderr", ""),
            "snapshot_dest_rc": snap_dest.get("rc", ""),
            "scatter_dest_verbose_snapshot": snap_verbose,
        },
    )

def case_scatter_src_regex_negative(cfg: Config) -> CaseResult:
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
    _ = _ssh_run_common(cfg, host, ["rm", "-rf", "--", dest_abs])
    _ = _ssh_run_common(cfg, host, ["mkdir", "-p", "--", dest_abs])
    _ = _ssh_run_sudo_common(cfg, host, ["chown", "-R", "--", f"{user}:{user}", dest_abs])

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

    r_x: CommandResult = _ssh_run_common(cfg, host, ["test", "-f", exp_x])
    r_bak: CommandResult = _ssh_run_common(cfg, host, ["test", "-f", exp_bak])

    passed: bool = (run.rc == 0 and r_x.rc == 0 and r_bak.rc != 0)
    reason: str = "" if passed else (
        f"rc={run.rc}, x_rc={r_x.rc}, bak_rc={r_bak.rc}, exp_x={exp_x}, exp_bak={exp_bak}"
    )
    return CaseResult(
        name=name,
        passed=passed,
        skipped=False,
        reason=reason,
        details={"rc": run.rc, "argv": " ".join(shlex.quote(a) for a in argv)},
    )

# =========================
# Main
# =========================

def main() -> int:
    cfg: Config = load_config_from_env()
    _ = print_env(cfg)
    _ = _preflight(cfg)
    cases: List[Tuple[str, Callable[[Config], CaseResult]]] = [
        ("path_semantics_roundtrip", case_path_semantics),
        ("selinux_auto_ubuntu_skip", case_selinux_auto_ubuntu_skip),
        ("selinux_mode_alma", case_selinux_mode_alma),
        ("selinux_policy_ignore_on_ubuntu", case_selinux_policy_ignore_on_ubuntu),
        ("gather_src_abs_slash_ok", case_gather_src_abs_slash_ok),
        ("gather_src_abs_tilde_ok", case_gather_src_abs_tilde_ok),
        ("gather_src_rel_home_ok", case_gather_src_rel_home_ok),
        ("gather_src_tilde_user_error", case_gather_src_tilde_user_error),
        ("scatter_dest_relative_ok_to_home", case_scatter_dest_relative_ok_to_home),
        ("scatter_dest_abs_variants", case_scatter_dest_abs_variants),
        ("gather_follow_symlinks_files", case_gather_follow_symlinks_files),
        ("scatter_follow_symlinks_files", case_scatter_follow_symlinks_files),
        ("scatter_pack_extract_user", case_scatter_pack_extract_user),
        ("scatter_pack_extract_sudo", case_scatter_pack_extract_sudo),
        ("scatter_pack_extract_sudo_existing_root", case_scatter_pack_extract_sudo_existing_root),
        ("gather_double_nesting_regression", case_gather_double_nesting_regression),
        ("scatter_src_path_layout_semantics", case_scatter_src_path_layout_semantics),
        ("scatter_dest_relative_to_remote_home", case_scatter_dest_relative_to_remote_home),
        ("scatter_dest_tilde_username_rejected", case_scatter_dest_tilde_username_rejected),
        ("scatter_nonpack_file_only_layout", case_scatter_nonpack_file_only_layout),
        ("scatter_mixed_sources_two_hosts", case_scatter_mixed_sources_two_hosts),
        ("scatter_pack_dedup_roots", case_scatter_pack_dedup_roots),
        ("scatter_nonpack_same_basename_collision_free", case_scatter_nonpack_same_basename_collision_free),
        ("gather_src_regex_absolute", case_gather_src_regex_absolute),
        ("gather_src_regex_relative", case_gather_src_regex_relative),
        ("gather_src_regex_negative", case_gather_src_regex_negative),
        ("scatter_src_regex_absolute", case_scatter_src_regex_absolute),
        ("scatter_src_regex_relative", case_scatter_src_regex_relative),
        ("scatter_src_regex_negative", case_scatter_src_regex_negative),
    ]
    summary: Dict[str, Any] = run_cases(step_number=4, cfg=cfg, cases=cases)
    cleanup_local_temps(cfg)  # runner 固有クリーンアップ
    # exit code: すべて passed か skipped なら 0、それ以外は 1
    all_ok: bool = all(r["passed"] or r["skipped"] for r in summary.get("results", []))
    return 0 if all_ok else 1

if __name__ == "__main__":
    import sys as _sys
    _sys.exit(main())
