# gm-tools-tests-20251116/tests_py/test_common_config.py

from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

from ._local_types import Config
from .constants import (
    SSH_PORT_DEFAULT,
    SSH_STRICT_DEFAULT,
    REMOTE_DEST_ROOT_DEFAULT,
    LOCAL_WORK_ROOT_DEFAULT,
    SSH_USER_DEFAULT,
    TARGET_USER_DEFAULT,
    HOSTS_BOTH_DEFAULT,
    HOST_UBUNTU_DEFAULT,
    HOST_ALMA_DEFAULT,
    GM_GATHER_CMD_DEFAULT,
    GM_SCATTER_CMD_DEFAULT,
    PARALLEL_DEFAULT,
    VERBOSE_DEFAULT,
)


def _split_cmd_env(env_name: str, default: str) -> List[str]:
    """
    環境変数 env_name を shlex.split して List[str] にするヘルパ。
    未設定時は default を split する。
    """
    value: str = os.environ.get(env_name, default)
    return shlex.split(value)


def _clear_dir(path_str: str) -> None:
    """
    path_str で指定されたディレクトリを一度まるごと削除してから作り直す。

    安全装置:
      - カレントディレクトリ配下 (cwd) のパスのみ削除対象とする。
      - シンボリックリンクは削除しない。
    """
    p: Path = Path(path_str).resolve()
    base: Path = Path(os.getcwd()).resolve()

    # base 配下以外は触らない
    try:
        p.relative_to(base)
    except ValueError:
        return

    if p.exists():
        if p.is_symlink():
            # 誤爆防止のためシンボリックリンクは削除対象外
            return
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            # ファイルだった場合はいったん削除してディレクトリを作る
            try:
                p.unlink()
            except OSError:
                pass

    p.mkdir(parents=True, exist_ok=True)


def load_config_from_env(*, clear_local_root: bool = True) -> Config:
    """
    環境変数から Config(_local_types.Config) を構築する共通入口。

    - clear_local_root=True の場合:
        - cfg.local_work_root を一度削除してから作り直す。
    - clear_local_root=False の場合:
        - cfg.local_work_root の削除は行わない（存在していれば中身は保持）。
    """
    # SSH / ユーザ
    ssh_user: str = os.environ.get("SSH_USER", SSH_USER_DEFAULT)
    # TARGET_USER 未指定時は ssh_user にフォールバック
    target_user_env: str | None = os.environ.get("TARGET_USER")
    target_user: str = target_user_env if target_user_env is not None else os.environ.get(
        "TARGET_USER", ssh_user if TARGET_USER_DEFAULT == SSH_USER_DEFAULT else TARGET_USER_DEFAULT
    )

    ssh_port: int = int(os.environ.get("SSH_PORT", str(SSH_PORT_DEFAULT)))
    ssh_strict_env: str = os.environ.get("SSH_STRICT", SSH_STRICT_DEFAULT)

    # パス
    remote_dest_root: str = os.environ.get("REMOTE_DEST_ROOT", REMOTE_DEST_ROOT_DEFAULT)
    local_work_root: str = os.environ.get("LOCAL_WORK_ROOT", LOCAL_WORK_ROOT_DEFAULT)

    # clear_local_root 指定に応じてローカル作業ディレクトリを初期化
    if clear_local_root:
        _clear_dir(local_work_root)

    # ホスト群
    hosts_both_raw: str = os.environ.get("HOSTS_BOTH", HOSTS_BOTH_DEFAULT)
    hosts_both_list: List[str] = shlex.split(hosts_both_raw)
    hosts_both: List[str] = [h for h in hosts_both_list if h]

    host_ubuntu: str = os.environ.get("HOST_UBUNTU", HOST_UBUNTU_DEFAULT)
    host_alma: str = os.environ.get("HOST_ALMA", HOST_ALMA_DEFAULT)

    # gm-gather / gm-scatter
    gm_gather_cmd: List[str] = _split_cmd_env("GM_GATHER_CMD", GM_GATHER_CMD_DEFAULT)
    gm_scatter_cmd: List[str] = _split_cmd_env("GM_SCATTER_CMD", GM_SCATTER_CMD_DEFAULT)

    # 挙動
    parallel: int = int(os.environ.get("PARALLEL", str(PARALLEL_DEFAULT)))
    verbose_env: str = os.environ.get("VERBOSE", "1" if VERBOSE_DEFAULT else "0")
    verbose: bool = (verbose_env == "1")

    cfg: Config = Config(
        ssh_user=ssh_user,
        target_user=target_user,
        hosts_both=hosts_both,
        host_ubuntu=host_ubuntu,
        host_alma=host_alma,
        ssh_port=ssh_port,
        ssh_strict=ssh_strict_env,
        ssh_strict_bool=(ssh_strict_env.lower() in ["yes", "true", "1"]),
        remote_dest_root=remote_dest_root,
        local_work_root=local_work_root,
        local_root=local_work_root,
        gm_gather_cmd=gm_gather_cmd,
        gm_scatter_cmd=gm_scatter_cmd,
        verbose=verbose,
        parallel=parallel,
    )

    return cfg

def snapshot_config(cfg: Config) -> Dict[str, Any]:
    """
    Config を JSON 互換の dict[str, Any] としてスナップショットするヘルパー。

    - dataclass のフィールドを明示的に写すことで、型チェッカにとっても分かりやすくする
    - list や dict フィールドは shallow copy しておく
    - 将来 Config にフィールドが増えた場合は、ここをメンテナンスする
    """
    result: Dict[str, Any] = {
        "ssh_user": cfg.ssh_user,
        "target_user": cfg.target_user,
        "hosts_both": list(cfg.hosts_both),
        "host_ubuntu": cfg.host_ubuntu,
        "host_alma": cfg.host_alma,
        "ssh_port": cfg.ssh_port,
        "ssh_strict": cfg.ssh_strict,
        "ssh_strict_bool": cfg.ssh_strict_bool,
        "remote_dest_root": cfg.remote_dest_root,
        "local_work_root": cfg.local_work_root,
        "local_root": cfg.local_root,
        "gm_gather_cmd": cfg.gm_gather_cmd,
        "gm_scatter_cmd": cfg.gm_scatter_cmd,
        "verbose": cfg.verbose,
        "parallel": cfg.parallel,
    }
    return result


def resolve_parallel_pair_from_env() -> Tuple[int, int]:
    """
    並列度を環境変数から解決する共通ヘルパ。
      - GM_PARALLEL が設定されていれば (1, GM_PARALLEL)
      - 未設定ならば (1, 4)
    """
    gm_par_raw: str = os.environ.get("GM_PARALLEL", "").strip()
    if gm_par_raw:
        j2: int = int(gm_par_raw)
        j1: int = 1
        return j1, j2
    j1_default: int = 1
    j2_default: int = 4
    return j1_default, j2_default


def print_env(cfg: Config, *, extra: Optional[Dict[str, str]] = None) -> None:
    """
    環境情報を安定した形式で出力する共通関数（Step5 仕様に準拠）。
    既定の3行に加え、extra で任意の key/value を [env] KEY=VALUE として追記可能。
    追加分は key を辞書順にして出力することで安定性を担保する。
    """
    j1, j2 = resolve_parallel_pair_from_env()
    msg1 = f"[env] SSH_USER={cfg.ssh_user} HOSTS_BOTH={' '.join(cfg.hosts_both)} PARALLEL={j1}/{j2}"
    msg2 = f"[env] GM_GATHER_CMD='{shlex.join(cfg.gm_gather_cmd)}'"
    msg3 = f"[env] GM_SCATTER_CMD='{shlex.join(cfg.gm_scatter_cmd)}'"
    print(msg1)
    print(msg2)
    print(msg3)
    if extra:
        for k in sorted(extra.keys()):
            v = extra[k]
            print(f"[env] {k}={v}")
