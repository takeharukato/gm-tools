#!/usr/bin/env python3
# tests/tests_py/runner_step5.py
# Step5 parallel scatter/gather runner（自己完結版）
# - ssh 呼び出し順序は runner_step4.py と同様に
#     ssh <opts> -- user@host <argv...>
# - scatter/gather 双方について、--pack 有無と -j 並列数の違いで
#   結果レイアウトが変化しないことを検証する
# - 結果比較は「ホストごとの DEST 配下ツリー + ファイルハッシュ」で行う
from __future__ import annotations

import hashlib
import os
import shlex
from typing import Dict, List, Tuple, IO, TypeAlias, Callable
# =========================
# 型エイリアス
# =========================


from ._local_types import Config, CaseResult, CommandResult, LocalRun
from .asserts import assert_rc
from .test_common_config import load_config_from_env, print_env, resolve_parallel_pair_from_env
from .test_common_runner import run_cases
from .test_common_cleanup import safe_rmtree_abs
from .test_common_ssh import (
    ssh_run as _ssh_run_common,
    ssh_run_sudo as _ssh_run_sudo_common,
)
from .test_common_snapshot import (
    snapshot_scatter_dest_verbose as _snapshot_scatter_dest_verbose,
)
from .test_common_local import cleanup_local_temps as _cleanup_local_temps
from .test_common_local import run_local_with_argv as _run_local_argv
from .test_common_hosts import write_temp_hosts as _write_temp_hosts

# =========================
# 型エイリアス
# =========================

EntryValue: TypeAlias = Tuple[str, str]          # (kind: "f"/"d"/..., digest or "-")
PerHostState: TypeAlias = Dict[str, EntryValue]  # rel_path -> EntryValue
AllHostsState: TypeAlias = Dict[str, PerHostState]


def cleanup_local_temps(cfg: Config) -> None:
    # Step5 は local_root のみ対象
    _cleanup_local_temps(cfg)


# =========================



## run_local_with_argv/LocalRun は共有実装を使用


# _write_temp_hosts は共有実装に委譲（互換エイリアス）


## moved to test_common_config.resolve_parallel_pair_from_env


def _hash_file_sha256(path: str) -> str:
    """
    ローカルファイル path の sha256 ハッシュ値（16進文字列）を返す。
    """
    h: hashlib._Hash = hashlib.sha256() # type: ignore
    buf_size: int = 1024 * 1024
    with open(path, "rb") as rf:
        while True:
            chunk: bytes = rf.read(buf_size)
            if not chunk:
                break
            h.update(chunk)
    digest: str = h.hexdigest()
    return digest


def _all_hosts_state_snapshot(states: AllHostsState) -> Dict[str, List[str]]:
    """
    AllHostsState から host ごとの find スナップショット（kind\\tpath）一覧を生成する。
    """
    snapshot: Dict[str, List[str]] = {}
    for host, per_state in states.items():
        lines: List[str] = []
        for rel, (kind, _digest) in per_state.items():
            # sha256 はテキスト上はノイズになるので省略し、kind+rel だけを並べる
            lines.append(f"{kind}\t{rel}")
        lines.sort()
        snapshot[host] = lines
    return snapshot


## moved to test_common_config.print_env


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

        r_sudo_v: CommandResult = _ssh_run_common(cfg, host, ["sudo", "-V"])
        assert_rc(f"{host}: sudo present", r_sudo_v.rc, expect_zero=True)

        r_sudo_true: CommandResult = _ssh_run_common(cfg, host, ["sudo", "-n", "true"])
        assert_rc(f"{host}: sudo -n true", r_sudo_true.rc, expect_zero=True)

        r_mkdir: CommandResult = _ssh_run_sudo_common(cfg, host, [
            "mkdir", "-p", "--", cfg.remote_dest_root
        ])
        assert_rc(f"{host}: mkdir remote_dest_root", r_mkdir.rc, expect_zero=True)

        r_chown: CommandResult = _ssh_run_sudo_common(cfg, host, [
            "chown", "-R", "--", f"{cfg.target_user}:{cfg.target_user}", cfg.remote_dest_root
        ])
        assert_rc(f"{host}: chown remote_dest_root", r_chown.rc, expect_zero=True)

        i += 1

    # ローカル作業ルートをクリア
    cwd: str = os.getcwd()
    safe_rmtree_abs(cfg.local_root, ensure_under=cwd)
    os.makedirs(cfg.local_root, exist_ok=True)


# =========================
# サンプル SRC 構築（scatter 用）
# =========================

def _prepare_local_parallel_sources(cfg: Config) -> List[str]:
    """
    並列 scatter 用のローカル検体ファイルを作成し、その絶対パス一覧を返す。
      - すべて「ファイル SRC」とする（ディレクトリ SRC は使用しない）
    """
    src_root: str = os.path.join(cfg.local_root, "step5_parallel_src")
    safe_rmtree_abs(src_root, ensure_under=cfg.local_root)
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
        _ = _ssh_run_sudo_common(cfg, host, ["rm", "-rf", "--", dest_abs])
        r_mkdir: CommandResult = _ssh_run_sudo_common(
            cfg, host, ["mkdir", "-p", "--", dest_abs]
        )
        assert_rc(f"{host}: mkdir dest_abs", r_mkdir.rc, expect_zero=True)
        r_chown: CommandResult = _ssh_run_sudo_common(
            cfg, host, ["chown", "-R", "--", f"{cfg.target_user}:{cfg.target_user}", dest_abs]
        )
        assert_rc(f"{host}: chown dest_abs", r_chown.rc, expect_zero=True)
        i += 1
    return dest_abs


# =========================
# サンプル SRC 構築（gather 用）
# =========================

def _prepare_remote_parallel_sources(cfg: Config) -> str:
    """
    並列 gather 用のリモート検体ツリーを各ホスト上に作成し、その SRC ルートパスを返す。
      - 各ホスト上に remote_src_root/dir1, remote_src_root/dir2 を作成し、
        a.txt, b.txt, c.txt を scatter 用と同じ内容で作成する。
    """
    remote_src_root: str = os.path.join(cfg.remote_dest_root, "step5_gather_src")
    i: int = 0
    n: int = len(cfg.hosts_both)
    while i < n:
        host: str = cfg.hosts_both[i]
        _ = _ssh_run_sudo_common(cfg, host, ["rm", "-rf", "--", remote_src_root])
        r_mkdir_root: CommandResult = _ssh_run_sudo_common(
            cfg, host, ["mkdir", "-p", "--", remote_src_root]
        )
        assert_rc(f"{host}: mkdir remote_src_root", r_mkdir_root.rc, expect_zero=True)
        r_chown_root: CommandResult = _ssh_run_sudo_common(
            cfg, host, ["chown", "-R", "--", f"{cfg.target_user}:{cfg.target_user}", remote_src_root]
        )
        assert_rc(f"{host}: chown remote_src_root", r_chown_root.rc, expect_zero=True)

        # dir1, dir2 とファイルを作成（target_user として）
        script_lines: List[str] = []
        root_q: str = shlex.quote(remote_src_root)
        script_lines.append("set -eu")
        script_lines.append(f"root={root_q}")
        script_lines.append('mkdir -p "$root/dir1" "$root/dir2"')
        script_lines.append('printf \'A\n\' > "$root/dir1/a.txt"')
        script_lines.append('printf \'B\n\' > "$root/dir1/b.txt"')
        script_lines.append('printf \'C\n\' > "$root/dir2/c.txt"')
        script: str = "\n".join(script_lines)

        r_populate: CommandResult = _ssh_run_common(
            cfg, host, ["bash", "-lc", script]
        )
        assert_rc(f"{host}: populate remote_src_root", r_populate.rc, expect_zero=True)

        i += 1

    return remote_src_root


# =========================
# リモート状態採取（scatter/gather 用）
# =========================

def _collect_remote_state_for_host(cfg: Config, host: str, dest_abs: str) -> PerHostState:
    """
    指定ホスト上の DEST(または SRC) 配下を走査し、
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
        'find "$root" -mindepth 1 -printf \'%y\t%P\n\' | LC_ALL=C sort | '
        'while IFS=$\'\t\' read -r kind rel; do'
    )
    lines_script.append('  if [ "$kind" = "f" ]; then')
    lines_script.append(
        '    sum=$(sha256sum "$root/$rel" | awk \'{print $1}\')'
    )
    lines_script.append('  else')
    lines_script.append('    sum="-"')
    lines_script.append('  fi')
    lines_script.append('  printf \'%s\t%s\t%s\n\' "$kind" "$rel" "$sum"')
    lines_script.append('done')
    script: str = "\n".join(lines_script)

    proc: CommandResult = _ssh_run_common(
        cfg, host, ["bash", "-lc", script]
    )
    if proc.rc != 0:
        name: str = f"{host}: snapshot DEST={dest_abs}"
        assert_rc(name, proc.rc, expect_zero=True)

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


# =========================
# ローカル状態採取（gather 用）
# =========================

def _collect_local_state_all_hosts(dest_abs: str, hosts: List[str]) -> AllHostsState:
    """
    gather で作成されたローカル DEST 配下を走査し、
      host -> (rel_path -> (kind, digest))
    の辞書として返す。
      - 各 host ごとに DEST/host をルートとみなす
      - kind: 'f'/'d'/...（os.walk 結果から組み立て）
      - digest: kind='f' の場合 sha256、それ以外は "-"
    """
    states: AllHostsState = {}
    host_idx: int = 0
    host_n: int = len(hosts)
    while host_idx < host_n:
        host: str = hosts[host_idx]
        host_root: str = os.path.join(dest_abs, host)
        per_state: PerHostState = {}

        if os.path.isdir(host_root):
            for root, dirs, files in os.walk(host_root):
                root_abs: str = root
                # ディレクトリ
                d_idx: int = 0
                d_n: int = len(dirs)
                while d_idx < d_n:
                    dname: str = dirs[d_idx]
                    dpath_abs: str = os.path.join(root_abs, dname)
                    rel_path: str = os.path.relpath(dpath_abs, host_root)
                    kind_d: str = "d"
                    entry_d: EntryValue = (kind_d, "-")
                    per_state[rel_path] = entry_d
                    d_idx += 1
                # ファイル
                f_idx: int = 0
                f_n: int = len(files)
                while f_idx < f_n:
                    fname: str = files[f_idx]
                    fpath_abs: str = os.path.join(root_abs, fname)
                    rel_path_f: str = os.path.relpath(fpath_abs, host_root)
                    kind_f: str = "f"
                    digest_f: str = _hash_file_sha256(fpath_abs)
                    entry_f: EntryValue = (kind_f, digest_f)
                    per_state[rel_path_f] = entry_f
                    f_idx += 1

        states[host] = per_state
        host_idx += 1

    return states


# =========================
# 状態比較
# =========================

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
    base_cmd: List[str] = list(cfg.gm_scatter_cmd)
    argv = base_cmd + ["-H", hosts_path, "-u", cfg.target_user, "-j", str(parallel)]

    if pack:
        argv.append("--pack")

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
# gather 実行ヘルパ
# =========================

def _run_gather_parallel(
    cfg: Config,
    remote_src_root: str,
    dest_rel: str,
    *,
    pack: bool,
    parallel: int,
) -> Tuple[LocalRun, AllHostsState]:
    """
    指定リモート SRC ルートをローカル DEST に gather し、その後 DEST の状態を採取して返す。
      - pack=True の場合は --pack を付与
      - -j parallel を指定
      - SRC はディレクトリ 1 個（remote_src_root）のみを指定
    """
    # ローカル DEST をクリアして作成
    dest_abs: str = os.path.join(cfg.local_root, dest_rel)
    cwd: str = os.getcwd()
    safe_rmtree_abs(dest_abs, ensure_under=cwd)
    os.makedirs(dest_abs, exist_ok=True)

    hosts_path: str = _write_temp_hosts(cfg.hosts_both)

    argv: List[str] = []
    base_cmd: List[str] = list(cfg.gm_gather_cmd)
    argv = base_cmd + ["-H", hosts_path, "-u", cfg.target_user, "-j", str(parallel)]

    if pack:
        argv.append("--pack")

    if cfg.verbose:
        argv.append("-v")

    argv.append("--")
    # Step4 と同一仕様：
    # gather の SRC は「末尾スラッシュを含まないディレクトリパス」
    # remote_src_root が '/path/to/src/' のように末尾 '/' を含む場合は除去する
    src_remote: str = remote_src_root.rstrip("/")

    argv.append(src_remote)
    argv.append(dest_abs)

    run: LocalRun = _run_local_argv(argv)
    # 正常終了であることを確認
    assert_rc("gather run", run.rc, expect_zero=True)

    states: AllHostsState = _collect_local_state_all_hosts(dest_abs, cfg.hosts_both)
    return run, states


# =========================
# テストケース（scatter）
# =========================

def case_scatter_parallel_nonpack_layout_stable(cfg: Config) -> CaseResult:
    """
    目的:
      非 pack（デフォルト SFTP 経路）で -j=1 と -j>1 を変えても、
      各ホストの DEST 配下レイアウトとファイル内容が変化しないことを検証する。
    """
    name: str = "scatter_parallel_nonpack_layout_stable"

    src_files: List[str] = _prepare_local_parallel_sources(cfg)

    j1: int
    j2: int
    j1, j2 = resolve_parallel_pair_from_env()

    dest_rel_j1: str = "step5_scatter_nonpack_j1"
    dest_rel_j2: str = "step5_scatter_nonpack_j2"

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

    # 参考: DEST 側の詳細スナップショット（1ホスト分、診断用。合否には影響しない）
    ref_host: str = cfg.hosts_both[0] if cfg.hosts_both else "localhost"
    dest_abs_j1: str = os.path.join(cfg.remote_dest_root, dest_rel_j1)
    dest_abs_j2: str = os.path.join(cfg.remote_dest_root, dest_rel_j2)
    # 共有の詳細スナップショット（whoami/stat/find/tree/存在チェックは find/tree のみ）
    dest_j1_verbose = _snapshot_scatter_dest_verbose(
        cfg.ssh_user, ref_host, cfg.ssh_port, cfg.ssh_strict_bool, dest_abs_j1, maxdepth=6
    )
    dest_j2_verbose = _snapshot_scatter_dest_verbose(
        cfg.ssh_user, ref_host, cfg.ssh_port, cfg.ssh_strict_bool, dest_abs_j2, maxdepth=6
    )

    details: Dict[str, object] = {
        "j1": j1,
        "j2": j2,
        "dest_j1": dest_abs_j1,
        "dest_j2": dest_abs_j2,
        "state_j1_hosts": sorted(list(state_j1.keys())),
        "state_j2_hosts": sorted(list(state_j2.keys())),
        "run_j1_rc": run_j1.rc,
        "run_j1_stdout": run_j1.stdout,
        "run_j1_stderr": run_j1.stderr,
        "run_j2_rc": run_j2.rc,
        "run_j2_stdout": run_j2.stdout,
        "run_j2_stderr": run_j2.stderr,
        "dest_j1_verbose_snapshot": dest_j1_verbose,
        "dest_j2_verbose_snapshot": dest_j2_verbose,
    }

    return CaseResult(
        name=name,
        passed=passed,
        skipped=False,
        reason=reason,
        details=details,
    )

def case_scatter_parallel_pack_layout_stable(cfg: Config) -> CaseResult:
    """
    目的:
      --pack 経路で -j=1 と -j>1 を変えても、
      各ホストの DEST 配下レイアウトとファイル内容が変化しないことを検証する。
    """
    name: str = "scatter_parallel_pack_layout_stable"

    src_files: List[str] = _prepare_local_parallel_sources(cfg)

    j1: int
    j2: int
    j1, j2 = resolve_parallel_pair_from_env()

    dest_rel_j1: str = "step5_scatter_pack_j1"
    dest_rel_j2: str = "step5_scatter_pack_j2"

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

    # 参考: DEST 側の詳細スナップショット（1ホスト分、診断用）
    ref_host: str = cfg.hosts_both[0] if cfg.hosts_both else "localhost"
    dest_abs_j1: str = os.path.join(cfg.remote_dest_root, dest_rel_j1)
    dest_abs_j2: str = os.path.join(cfg.remote_dest_root, dest_rel_j2)
    dest_j1_verbose = _snapshot_scatter_dest_verbose(
        cfg.ssh_user, ref_host, cfg.ssh_port, cfg.ssh_strict_bool, dest_abs_j1, maxdepth=6
    )
    dest_j2_verbose = _snapshot_scatter_dest_verbose(
        cfg.ssh_user, ref_host, cfg.ssh_port, cfg.ssh_strict_bool, dest_abs_j2, maxdepth=6
    )

    details: Dict[str, object] = {
        "j1": j1,
        "j2": j2,
        "dest_j1": dest_abs_j1,
        "dest_j2": dest_abs_j2,
        "state_j1_hosts": sorted(list(state_j1.keys())),
        "state_j2_hosts": sorted(list(state_j2.keys())),
        "run_j1_rc": run_j1.rc,
        "run_j1_stdout": run_j1.stdout,
        "run_j1_stderr": run_j1.stderr,
        "run_j2_rc": run_j2.rc,
        "run_j2_stdout": run_j2.stdout,
        "run_j2_stderr": run_j2.stderr,
        "dest_j1_verbose_snapshot": dest_j1_verbose,
        "dest_j2_verbose_snapshot": dest_j2_verbose,
    }

    return CaseResult(
        name=name,
        passed=passed,
        skipped=False,
        reason=reason,
        details=details,
    )

# =========================
# テストケース（gather）
# =========================

def case_gather_parallel_nonpack_layout_stable(cfg: Config) -> CaseResult:
    """
    目的:
      非 pack 経路で -j=1 と -j>1 を変えても、
      ローカル DEST 配下レイアウトとファイル内容が変化しないことを検証する。
      あわせて remote_src_root および各 DEST の find スナップショットを採取する。
    """
    name: str = "gather_parallel_nonpack_layout_stable"

    remote_src_root: str = _prepare_remote_parallel_sources(cfg)
    remote_src_states: AllHostsState = _collect_remote_state_all_hosts(cfg, remote_src_root)
    remote_src_snapshot: Dict[str, List[str]] = _all_hosts_state_snapshot(remote_src_states)
    # 参考スナップショット（1台分、詳細 verbose）を共有ヘルパで採取
    ref_host: str = cfg.hosts_both[0] if cfg.hosts_both else "localhost"
    remote_src_verbose = _snapshot_scatter_dest_verbose(
        cfg.ssh_user, ref_host, cfg.ssh_port, cfg.ssh_strict_bool, remote_src_root, maxdepth=6
    )

    j1: int
    j2: int
    j1, j2 = resolve_parallel_pair_from_env()

    dest_rel_j1: str = "step5_gather_nonpack_j1"
    dest_rel_j2: str = "step5_gather_nonpack_j2"

    run_j1: LocalRun
    state_j1: AllHostsState
    run_j1, state_j1 = _run_gather_parallel(
        cfg, remote_src_root, dest_rel_j1, pack=False, parallel=j1
    )

    run_j2: LocalRun
    state_j2: AllHostsState
    run_j2, state_j2 = _run_gather_parallel(
        cfg, remote_src_root, dest_rel_j2, pack=False, parallel=j2
    )

    same: bool
    reason_diff: str
    same, reason_diff = _compare_host_states(state_j1, state_j2)

    passed: bool = same
    reason: str = "" if passed else f"layout/content differs between j={j1} and j={j2}: {reason_diff}"

    dest_j1_snapshot: Dict[str, List[str]] = _all_hosts_state_snapshot(state_j1)
    dest_j2_snapshot: Dict[str, List[str]] = _all_hosts_state_snapshot(state_j2)

    details: Dict[str, object] = {
        "j1": j1,
        "j2": j2,
        "remote_src_root": remote_src_root,
        "remote_src_snapshot": remote_src_snapshot,
        "remote_src_verbose_snapshot": remote_src_verbose,
        "dest_j1": os.path.join(cfg.local_root, dest_rel_j1),
        "dest_j2": os.path.join(cfg.local_root, dest_rel_j2),
        "dest_j1_snapshot": dest_j1_snapshot,
        "dest_j2_snapshot": dest_j2_snapshot,
        "state_j1_hosts": sorted(list(state_j1.keys())),
        "state_j2_hosts": sorted(list(state_j2.keys())),
        "run_j1_rc": run_j1.rc,
        "run_j1_stdout": run_j1.stdout,
        "run_j1_stderr": run_j1.stderr,
        "run_j2_rc": run_j2.rc,
        "run_j2_stdout": run_j2.stdout,
        "run_j2_stderr": run_j2.stderr,
    }

    return CaseResult(
        name=name,
        passed=passed,
        skipped=False,
        reason=reason,
        details=details,
    )

def case_gather_parallel_pack_layout_stable(cfg: Config) -> CaseResult:
    """
    目的:
      --pack 経路で -j=1 と -j>1 を変えても、
      ローカル DEST 配下レイアウトとファイル内容が変化しないことを検証する。
      あわせて remote_src_root および各 DEST の find スナップショットを採取する。
    """
    name: str = "gather_parallel_pack_layout_stable"

    remote_src_root: str = _prepare_remote_parallel_sources(cfg)
    remote_src_states: AllHostsState = _collect_remote_state_all_hosts(cfg, remote_src_root)
    remote_src_snapshot: Dict[str, List[str]] = _all_hosts_state_snapshot(remote_src_states)
    # 参考スナップショット（1台分、詳細 verbose）を共有ヘルパで採取
    ref_host: str = cfg.hosts_both[0] if cfg.hosts_both else "localhost"
    remote_src_verbose = _snapshot_scatter_dest_verbose(
        cfg.ssh_user, ref_host, cfg.ssh_port, cfg.ssh_strict_bool, remote_src_root, maxdepth=6
    )

    j1: int
    j2: int
    j1, j2 = resolve_parallel_pair_from_env()

    dest_rel_j1: str = "step5_gather_pack_j1"
    dest_rel_j2: str = "step5_gather_pack_j2"

    run_j1: LocalRun
    state_j1: AllHostsState
    run_j1, state_j1 = _run_gather_parallel(
        cfg, remote_src_root, dest_rel_j1, pack=True, parallel=j1
    )

    run_j2: LocalRun
    state_j2: AllHostsState
    run_j2, state_j2 = _run_gather_parallel(
        cfg, remote_src_root, dest_rel_j2, pack=True, parallel=j2
    )

    same: bool
    reason_diff: str
    same, reason_diff = _compare_host_states(state_j1, state_j2)

    passed: bool = same
    reason: str = "" if passed else f"layout/content differs between j={j1} and j={j2}: {reason_diff}"

    dest_j1_snapshot: Dict[str, List[str]] = _all_hosts_state_snapshot(state_j1)
    dest_j2_snapshot: Dict[str, List[str]] = _all_hosts_state_snapshot(state_j2)

    details: Dict[str, object] = {
        "j1": j1,
        "j2": j2,
        "remote_src_root": remote_src_root,
        "remote_src_snapshot": remote_src_snapshot,
        "remote_src_verbose_snapshot": remote_src_verbose,
        "dest_j1": os.path.join(cfg.local_root, dest_rel_j1),
        "dest_j2": os.path.join(cfg.local_root, dest_rel_j2),
        "dest_j1_snapshot": dest_j1_snapshot,
        "dest_j2_snapshot": dest_j2_snapshot,
        "state_j1_hosts": sorted(list(state_j1.keys())),
        "state_j2_hosts": sorted(list(state_j2.keys())),
        "run_j1_rc": run_j1.rc,
        "run_j1_stdout": run_j1.stdout,
        "run_j1_stderr": run_j1.stderr,
        "run_j2_rc": run_j2.rc,
        "run_j2_stdout": run_j2.stdout,
        "run_j2_stderr": run_j2.stderr,
    }

    return CaseResult(
        name=name,
        passed=passed,
        skipped=False,
        reason=reason,
        details=details,
    )

# =========================
# Main
# =========================

def main() -> None:
    # 共通 Config を使用する。Step5 では local_work_root を毎回クリアしたいので
    # clear_local_root=True を指定する。
    cfg: Config = load_config_from_env(clear_local_root=True)

    _ = print_env(cfg)

    try:
        _preflight_result = _preflight(cfg)

        cases: List[Tuple[str, Callable[[Config], CaseResult]]] = [
            ("scatter_parallel_nonpack_layout_stable", case_scatter_parallel_nonpack_layout_stable),
            ("scatter_parallel_pack_layout_stable", case_scatter_parallel_pack_layout_stable),
            ("gather_parallel_nonpack_layout_stable", case_gather_parallel_nonpack_layout_stable),
            ("gather_parallel_pack_layout_stable", case_gather_parallel_pack_layout_stable),
        ]

        # 共通ランナーに実行と JSON summary 出力を委譲
        _run_case_results = run_cases(step_number=5, cfg=cfg, cases=cases)
    finally:
        # Step5 ではローカル一時ディレクトリを runner 側で確実に削除する
        cleanup_local_temps(cfg)

if __name__ == "__main__":
    main()
