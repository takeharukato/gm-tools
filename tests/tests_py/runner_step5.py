#!/usr/bin/env python3
# tests/tests_py/runner_step5.py
# Step5 parallel scatter runner（自己完結版）
# - ssh 呼び出し順序は runner_step4.py と同様に
#     ssh <opts> -- user@host <argv...>
# - gather は使わず、scatter のみを対象とする
# - --pack 有無と -j 並列数の違いで結果レイアウトが変化しないことを検証する
# - 結果比較は「ホストごとの DEST 配下ツリー + ファイルハッシュ」で行う
from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, IO, TypeAlias


# =========================
# 型エイリアス
# =========================

EntryValue: TypeAlias = Tuple[str, str]          # (kind: "f"/"d"/..., digest or "-")
PerHostState: TypeAlias = Dict[str, EntryValue]  # rel_path -> EntryValue
AllHostsState: TypeAlias = Dict[str, PerHostState]


# =========================
# 安全なローカル掃除
# =========================

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
            return
    if not os.path.exists(p):
        return
    if os.path.islink(p):
        return
    if os.path.isdir(p):
        import shutil as _shutil  # 局所 import（型アノテーション不要）
        _shutil.rmtree(p, ignore_errors=True)


def cleanup_local_temps(cfg: Config) -> None:
    """
    テストで作成するローカル一時ディレクトリを削除する。
      - cfg.local_root (= _tmp_test_local/)
    """
    cwd: str = os.getcwd()
    _safe_rmtree_abs(cfg.local_root, ensure_under=cwd)


# =========================
# 共有ヘルパ
# =========================

def assert_rc(name: str, rc: int, *, expect_zero: bool = True) -> None:
    """rc を検証（ゼロ期待がデフォルト）"""
    ok: bool = (rc == 0) if expect_zero else (rc != 0)
    if not ok:
        msg: str = f"{name}: expected rc={'0' if expect_zero else '!=0'} but got {rc}"
        raise AssertionError(msg)


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
    debug_flag: str = os.environ.get("VERBOSE", "0")
    if debug_flag == "1":
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
    debug_flag: str = os.environ.get("VERBOSE", "0")
    if debug_flag == "1":
        debug_msg: str = f"[DEBUG] ssh_sudo: {argv!r}"
        print(debug_msg)
    completed: subprocess.CompletedProcess[str] = subprocess.run(argv, capture_output=True, text=True)
    return completed


@dataclass(frozen=True)
class LocalRun:
    rc: int
    stdout: str
    stderr: str


def _run_local_argv(argv: List[str]) -> LocalRun:
    debug_flag: str = os.environ.get("VERBOSE", "0")
    if debug_flag == "1":
        dbg: str = f"[DEBUG] _run_local_argv argv: {shlex.join(argv)}"
        print(dbg)
    proc: subprocess.CompletedProcess[str] = subprocess.run(argv, capture_output=True, text=True)
    run: LocalRun = LocalRun(proc.returncode, proc.stdout or "", proc.stderr or "")
    return run


def _write_temp_hosts(hosts: List[str]) -> str:
    fd: int
    path: str
    fd, path = tempfile.mkstemp(prefix="hosts_", text=True)
    os.close(fd)
    f: IO[str]
    i: int = 0
    n: int = len(hosts)
    with open(path, "w", encoding="utf-8") as f:
        while i < n:
            h: str = hosts[i]
            _ = f.write(h + "\n")
            i += 1
    return path


def _as_posix_rel(path_abs: str) -> str:
    """
    絶対パスを scatter 用 DEST 直下の相対表現に正規化する。
      - 区切りを '/' に統一
      - 先頭の '/' を除去
      - 末尾スラッシュ有無は入力を尊重
    """
    s0: str = path_abs.replace("\\", "/")
    had_trailing: bool = s0.endswith("/")
    s: str = s0.lstrip("/")
    if had_trailing and not s.endswith("/"):
        s = s + "/"
    return s


# =========================
# 設定
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

    gm_scatter_cmd: List[str]

    verbose: bool


def _split_cmd_env(env_key: str, default_val: str) -> List[str]:
    raw: str = os.environ.get(env_key, default_val)
    parts: List[str] = shlex.split(raw)
    return parts


def load_config_from_env() -> Config:
    ssh_user: str = os.environ.get("SSH_USER", "ansible")
    target_user: str = os.environ.get("TARGET_USER", ssh_user)

    ssh_port_str: str = os.environ.get("SSH_PORT", "22")
    ssh_port: int = int(ssh_port_str)
    ssh_strict: bool = (os.environ.get("SSH_STRICT", "no").lower() == "yes")

    remote_dest_root: str = os.environ.get("REMOTE_DEST_ROOT", "/tmp/gmtools_remote_dest")
    local_root: str = os.environ.get("LOCAL_WORK_ROOT", os.path.join(os.getcwd(), "_tmp_test_local"))

    hosts_both_raw: str = os.environ.get("HOSTS_BOTH", "localhost")
    hosts_both_list: List[str] = shlex.split(hosts_both_raw)
    hosts_both: List[str] = []
    i: int = 0
    n: int = len(hosts_both_list)
    while i < n:
        h_item: str = hosts_both_list[i]
        if h_item:
            hosts_both.append(h_item)
        i += 1

    host_ubuntu: str = os.environ.get("HOST_UBUNTU", "localhost")
    host_alma: str = os.environ.get("HOST_ALMA", "vmlinux4.local")

    gm_scatter_cmd: List[str] = _split_cmd_env("GM_SCATTER_CMD", "python3 -m gm_tools.scatter_cli")

    verbose: bool = (os.environ.get("VERBOSE", "0") == "1")

    cfg: Config = Config(
        ssh_user=ssh_user,
        target_user=target_user,
        ssh_port=ssh_port,
        ssh_strict=ssh_strict,
        remote_dest_root=remote_dest_root,
        local_root=local_root,
        hosts_both=hosts_both,
        host_ubuntu=host_ubuntu,
        host_alma=host_alma,
        gm_scatter_cmd=gm_scatter_cmd,
        verbose=verbose,
    )
    return cfg


def print_env(cfg: Config) -> None:
    msg1: str = f"[env] SSH_USER={cfg.ssh_user} HOSTS_BOTH={' '.join(cfg.hosts_both)}"
    msg2: str = f"[env] GM_SCATTER_CMD='{shlex.join(cfg.gm_scatter_cmd)}'"
    print(msg1)
    print(msg2)


# =========================
# 前処理
# =========================

def _preflight(cfg: Config) -> None:
    """
    sudo/NOPASSWD チェックと remote_dest_root の作成。
    """
    i: int = 0
    n: int = len(cfg.hosts_both)
    while i < n:
        host: str = cfg.hosts_both[i]

        r_sudo_v: subprocess.CompletedProcess[str] = ssh_do(
            cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "sudo", "-V"
        )
        assert_rc(f"{host}: sudo present", r_sudo_v.returncode, expect_zero=True)

        r_sudo_true: subprocess.CompletedProcess[str] = ssh_do(
            cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "sudo", "-n", "true"
        )
        assert_rc(f"{host}: sudo -n true", r_sudo_true.returncode, expect_zero=True)

        r_mkdir: subprocess.CompletedProcess[str] = ssh_sudo(
            cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict,
            "mkdir", "-p", "--", cfg.remote_dest_root
        )
        assert_rc(f"{host}: mkdir remote_dest_root", r_mkdir.returncode, expect_zero=True)

        r_chown: subprocess.CompletedProcess[str] = ssh_sudo(
            cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict,
            "chown", "-R", "--", f"{cfg.target_user}:{cfg.target_user}", cfg.remote_dest_root
        )
        assert_rc(f"{host}: chown remote_dest_root", r_chown.returncode, expect_zero=True)

        i += 1

    # ローカル作業ルートをクリア
    cwd: str = os.getcwd()
    _safe_rmtree_abs(cfg.local_root, ensure_under=cwd)
    os.makedirs(cfg.local_root, exist_ok=True)


# =========================
# サンプル SRC 構築
# =========================

def _prepare_local_parallel_sources(cfg: Config) -> List[str]:
    """
    並列 scatter 用のローカル検体ファイルを作成し、その絶対パス一覧を返す。
      - すべて「ファイル SRC」とする（ディレクトリ SRC は使用しない）
    """
    src_root: str = os.path.join(cfg.local_root, "step5_parallel_src")
    _safe_rmtree_abs(src_root, ensure_under=cfg.local_root)
    os.makedirs(src_root, exist_ok=True)

    dir1: str = os.path.join(src_root, "dir1")
    dir2: str = os.path.join(src_root, "dir2")
    os.makedirs(dir1, exist_ok=True)
    os.makedirs(dir2, exist_ok=True)

    file1: str = os.path.join(dir1, "a.txt")
    file2: str = os.path.join(dir1, "b.txt")
    file3: str = os.path.join(dir2, "c.txt")

    wf: IO[str]
    with open(file1, "w", encoding="utf-8") as wf:
        _ = wf.write("A\n")
    with open(file2, "w", encoding="utf-8") as wf:
        _ = wf.write("B\n")
    with open(file3, "w", encoding="utf-8") as wf:
        _ = wf.write("C\n")

    src_files: List[str] = [file1, file2, file3]
    return src_files


def _prepare_remote_dest_for_all_hosts(cfg: Config, dest_rel: str) -> str:
    """
    hosts_both の全ホスト上に DEST を作成し、target_user 所有にする。
    戻り値は DEST の絶対パス。
    """
    dest_abs: str = os.path.join(cfg.remote_dest_root, dest_rel)
    i: int = 0
    n: int = len(cfg.hosts_both)
    while i < n:
        host: str = cfg.hosts_both[i]
        _ = ssh_sudo(cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict, "rm", "-rf", "--", dest_abs)
        r_mkdir: subprocess.CompletedProcess[str] = ssh_sudo(
            cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict,
            "mkdir", "-p", "--", dest_abs
        )
        assert_rc(f"{host}: mkdir dest_abs", r_mkdir.returncode, expect_zero=True)
        r_chown: subprocess.CompletedProcess[str] = ssh_sudo(
            cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict,
            "chown", "-R", "--", f"{cfg.target_user}:{cfg.target_user}", dest_abs
        )
        assert_rc(f"{host}: chown dest_abs", r_chown.returncode, expect_zero=True)
        i += 1
    return dest_abs


# =========================
# リモート状態採取
# =========================

def _collect_remote_state_for_host(cfg: Config, host: str, dest_abs: str) -> PerHostState:
    """
    指定ホスト上の DEST 配下を走査し、
      rel_path -> (kind, digest)
    の辞書として返す。
      - kind: find の %y（'f'=ファイル, 'd'=ディレクトリ, 他）
      - digest: kind='f' のとき sha256sum、それ以外は "-"
    """
    lines_script: List[str] = []
    root_q: str = shlex.quote(dest_abs)
    lines_script.append("set -eu")
    lines_script.append(f"root={root_q}")
    lines_script.append('if [ ! -d "$root" ]; then exit 0; fi')
    lines_script.append(
        'find "$root" -mindepth 1 -printf \'%y\\t%P\\n\' | LC_ALL=C sort | '
        'while IFS=$\'\\t\' read -r kind rel; do'
    )
    lines_script.append('  if [ "$kind" = "f" ]; then')
    # awk の { } は f-string 内なので二重の {{ }} でエスケープ
    lines_script.append(
        '    sum=$(sha256sum "$root/$rel" | awk \'{print $1}\')'
    )
    lines_script.append('  else')
    lines_script.append('    sum="-"')
    lines_script.append('  fi')
    lines_script.append('  printf \'%s\\t%s\\t%s\\n\' "$kind" "$rel" "$sum"')
    lines_script.append('done')
    script: str = "\n".join(lines_script)

    proc: subprocess.CompletedProcess[str] = ssh_do(
        cfg.ssh_user, host, cfg.ssh_port, cfg.ssh_strict,
        "bash", "-lc", script
    )
    if proc.returncode != 0:
        name: str = f"{host}: snapshot DEST={dest_abs}"
        assert_rc(name, proc.returncode, expect_zero=True)

    out: str = proc.stdout or ""
    lines: List[str] = out.splitlines()

    state: PerHostState = {}
    idx: int = 0
    total: int = len(lines)
    while idx < total:
        line: str = lines[idx].rstrip("\n")
        if not line:
            idx += 1
            continue
        parts: List[str] = line.split("\t")
        if len(parts) != 3:
            idx += 1
            continue
        kind: str = parts[0]
        rel: str = parts[1]
        digest: str = parts[2]
        key: str = rel
        value: EntryValue = (kind, digest)
        state[key] = value
        idx += 1

    return state


def _collect_remote_state_all_hosts(cfg: Config, dest_abs: str) -> AllHostsState:
    """
    hosts_both の全ホストについて _collect_remote_state_for_host を呼び、
    host -> PerHostState の辞書を返す。
    """
    states: AllHostsState = {}
    i: int = 0
    n: int = len(cfg.hosts_both)
    while i < n:
        host: str = cfg.hosts_both[i]
        host_state: PerHostState = _collect_remote_state_for_host(cfg, host, dest_abs)
        states[host] = host_state
        i += 1
    return states


def _compare_host_states(lhs: AllHostsState, rhs: AllHostsState) -> Tuple[bool, str]:
    """
    2つの AllHostsState が完全に一致するか判定する。
    一致しない場合は簡易理由文字列を返す。
    """
    lhs_hosts: List[str] = sorted(list(lhs.keys()))
    rhs_hosts: List[str] = sorted(list(rhs.keys()))
    if lhs_hosts != rhs_hosts:
        reason_hosts: str = f"host-set mismatch lhs={lhs_hosts!r} rhs={rhs_hosts!r}"
        return False, reason_hosts

    i: int = 0
    n: int = len(lhs_hosts)
    while i < n:
        host: str = lhs_hosts[i]
        lhs_state: PerHostState = lhs[host]
        rhs_state: PerHostState = rhs[host]
        if lhs_state != rhs_state:
            reason_diff: str = f"state mismatch on host={host}"
            return False, reason_diff
        i += 1

    return True, ""


# =========================
# scatter 実行ヘルパ
# =========================

def _run_scatter_parallel(
    cfg: Config,
    src_files: List[str],
    dest_rel: str,
    *,
    pack: bool,
    parallel: int,
) -> Tuple[LocalRun, AllHostsState]:
    """
    指定 SRC 一覧を指定 DEST に scatter し、その後 DEST の状態を採取して返す。
      - pack=True の場合は --pack を付与
      - -j parallel を指定
    """
    dest_abs: str = _prepare_remote_dest_for_all_hosts(cfg, dest_rel)

    hosts_path: str = _write_temp_hosts(cfg.hosts_both)

    argv: List[str] = []
    _i_opt: int = 0

    base_cmd: List[str] = list(cfg.gm_scatter_cmd)
    argv = base_cmd + ["-H", hosts_path, "-u", cfg.target_user, "-j", str(parallel)]
    _i_opt = len(argv)

    if cfg.verbose:
        argv.append("-v")

    argv.append("--")

    i_src: int = 0
    n_src: int = len(src_files)
    while i_src < n_src:
        src_path: str = src_files[i_src]
        argv.append(src_path)
        i_src += 1

    argv.append(dest_abs)

    run: LocalRun = _run_local_argv(argv)
    # 正常終了であることを確認
    assert_rc("scatter run", run.rc, expect_zero=True)

    states: AllHostsState = _collect_remote_state_all_hosts(cfg, dest_abs)
    return run, states


# =========================
# テストケース
# =========================

def case_scatter_parallel_nonpack_layout_stable(cfg: Config) -> Dict[str, object]:
    """
    目的:
      非 pack（デフォルト SFTP 経路）で -j=1 と -j>1 を変えても、
      各ホストの DEST 配下レイアウトとファイル内容が変化しないことを検証する。
    """
    name: str = "scatter_parallel_nonpack_layout_stable"

    src_files: List[str] = _prepare_local_parallel_sources(cfg)

    j1_str: str = os.environ.get("STEP5_J_NONPACK_1", "1")
    j2_str: str = os.environ.get("STEP5_J_NONPACK_2", "4")
    j1: int = int(j1_str)
    j2: int = int(j2_str)

    dest_rel_j1: str = "step5_nonpack_j1"
    dest_rel_j2: str = "step5_nonpack_j2"

    run_j1: LocalRun
    state_j1: AllHostsState
    run_j1, state_j1 = _run_scatter_parallel(
        cfg, src_files, dest_rel_j1, pack=False, parallel=j1
    )

    run_j2: LocalRun
    state_j2: AllHostsState
    run_j2, state_j2 = _run_scatter_parallel(
        cfg, src_files, dest_rel_j2, pack=False, parallel=j2
    )

    same: bool
    reason_diff: str
    same, reason_diff = _compare_host_states(state_j1, state_j2)

    passed: bool = same
    reason: str = "" if passed else f"layout/content differs between j={j1} and j={j2}: {reason_diff}"

    details: Dict[str, object] = {
        "j1": j1,
        "j2": j2,
        "dest_j1": os.path.join(cfg.remote_dest_root, dest_rel_j1),
        "dest_j2": os.path.join(cfg.remote_dest_root, dest_rel_j2),
        "state_j1_hosts": sorted(list(state_j1.keys())),
        "state_j2_hosts": sorted(list(state_j2.keys())),
        "run_j1_rc": run_j1.rc,
        "run_j1_stdout": run_j1.stdout,
        "run_j1_stderr": run_j1.stderr,
        "run_j2_rc": run_j2.rc,
        "run_j2_stdout": run_j2.stdout,
        "run_j2_stderr": run_j2.stderr,
    }

    result: Dict[str, object] = {
        "name": name,
        "passed": passed,
        "skipped": False,
        "reason": reason,
        "details": details,
    }
    return result


def case_scatter_parallel_pack_layout_stable(cfg: Config) -> Dict[str, object]:
    """
    目的:
      --pack 経路で -j=1 と -j>1 を変えても、
      各ホストの DEST 配下レイアウトとファイル内容が変化しないことを検証する。
    """
    name: str = "scatter_parallel_pack_layout_stable"

    src_files: List[str] = _prepare_local_parallel_sources(cfg)

    j1_str: str = os.environ.get("STEP5_J_PACK_1", "1")
    j2_str: str = os.environ.get("STEP5_J_PACK_2", "4")
    j1: int = int(j1_str)
    j2: int = int(j2_str)

    dest_rel_j1: str = "step5_pack_j1"
    dest_rel_j2: str = "step5_pack_j2"

    run_j1: LocalRun
    state_j1: AllHostsState
    run_j1, state_j1 = _run_scatter_parallel(
        cfg, src_files, dest_rel_j1, pack=True, parallel=j1
    )

    run_j2: LocalRun
    state_j2: AllHostsState
    run_j2, state_j2 = _run_scatter_parallel(
        cfg, src_files, dest_rel_j2, pack=True, parallel=j2
    )

    same: bool
    reason_diff: str
    same, reason_diff = _compare_host_states(state_j1, state_j2)

    passed: bool = same
    reason: str = "" if passed else f"layout/content differs between j={j1} and j={j2}: {reason_diff}"

    details: Dict[str, object] = {
        "j1": j1,
        "j2": j2,
        "dest_j1": os.path.join(cfg.remote_dest_root, dest_rel_j1),
        "dest_j2": os.path.join(cfg.remote_dest_root, dest_rel_j2),
        "state_j1_hosts": sorted(list(state_j1.keys())),
        "state_j2_hosts": sorted(list(state_j2.keys())),
        "run_j1_rc": run_j1.rc,
        "run_j1_stdout": run_j1.stdout,
        "run_j1_stderr": run_j1.stderr,
        "run_j2_rc": run_j2.rc,
        "run_j2_stdout": run_j2.stdout,
        "run_j2_stderr": run_j2.stderr,
    }

    result: Dict[str, object] = {
        "name": name,
        "passed": passed,
        "skipped": False,
        "reason": reason,
        "details": details,
    }
    return result


# =========================
# Main
# =========================

def main() -> None:
    cfg: Config = load_config_from_env()
    _ = print_env(cfg)

    results: List[Dict[str, object]] = []
    try:
        _ = _preflight(cfg)

        results.append(case_scatter_parallel_nonpack_layout_stable(cfg))
        results.append(case_scatter_parallel_pack_layout_stable(cfg))

        print("STEP5 SUMMARY")
        summary: str = json.dumps({"results": results}, indent=2, ensure_ascii=False)
        print(summary)
    finally:
        cleanup_local_temps(cfg)


if __name__ == "__main__":
    main()
