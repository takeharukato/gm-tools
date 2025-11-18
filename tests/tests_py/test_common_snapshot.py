import shlex
import subprocess
from typing import Dict, List, Optional, Union
from .test_common_ssh import ssh_run_raw as _ssh_run_raw


# raw ssh 実行は共有ヘルパに委譲する（Config 非依存のためスナップショット用途に最適）


def remote_find_tree_script(
    ssh_user: str,
    host: str,
    port: int,
    strict: Union[bool, str],
    base: str,
    *,
    maxdepth: int = 6,
) -> Dict[str, str]:
    q: str = shlex.quote(base)
    md: int = max(1, int(maxdepth))
    script: str = f"""
set -eu
base={q}
echo "[pwd] $(pwd)"
echo "[ls.base]"; ls -la -- "$base" 2>/dev/null || true
echo "[find.base]"; LC_ALL=C find "$base" -maxdepth {md} -printf '%y\t%p\n' 2>/dev/null | LC_ALL=C sort || true
echo "[tree.base]"; (tree -a -L {md} -- "$base" 2>/dev/null || true)
""".strip()
    p = _ssh_run_raw(ssh_user, host, port, strict, "bash", "-lc", script)
    return {"rc": str(p.returncode), "stdout": p.stdout or "", "stderr": p.stderr or ""}


def snapshot_scatter_dest_verbose(
    ssh_user: str,
    host: str,
    port: int,
    strict: Union[bool, str],
    dest_abs: str,
    *,
    expected_paths: Optional[List[str]] = None,
    maxdepth: int = 8,
) -> Dict[str, str]:
    parts: List[str] = []

    # 1) 実行系メタ
    meta_cmd: str = r"""
set -u
echo "[whoami] $(whoami)"
echo "[pwd]    $(pwd)"
echo "[home]   $HOME"
echo "[uname]  $(uname -a)"
echo "[umask]  $(umask)"
""".strip()
    r_meta = _ssh_run_raw(ssh_user, host, port, strict, "bash", "-lc", meta_cmd)
    parts.append(r_meta.stdout or "")

    # 2) DEST 自体の解決と stat
    cmd2: str = f"""
set -u
DEST={shlex.quote(dest_abs)}
echo "[dest.raw] $DEST"
echo "[dest.realpath] $(realpath -m \"$DEST\" 2>/dev/null || echo '(no realpath)')"
if [ -e "$DEST" ]; then
  echo "[dest.stat] $(stat -c '%U:%G %a %F' \"$DEST\" 2>/dev/null || echo '(stat-ng)')"
else
  echo "[dest.stat] (missing)"
fi
""".strip()
    r2 = _ssh_run_raw(ssh_user, host, port, strict, "bash", "-lc", cmd2)
    parts.append(r2.stdout or "")

    # 3) ツリーと find
    r3 = remote_find_tree_script(ssh_user, host, port, strict, dest_abs, maxdepth=maxdepth)
    parts.append(r3.get("stdout", ""))

    # 4) 期待パス存在チェック
    check_block: str = ""
    if expected_paths:
        q: str = " ".join(shlex.quote(p) for p in expected_paths)
        cmd4: str = f"""
set -u
for P in {q}; do
    test -f "$P"; rc=$?; printf "[check] %s : rc=%d\n" "$P" "$rc"
done
""".strip()
        r4 = _ssh_run_raw(ssh_user, host, port, strict, "bash", "-lc", cmd4)
        check_block = r4.stdout or ""
        parts.append(check_block)

    out: Dict[str, str] = {
        "meta": parts[0] if len(parts) > 0 else "",
        "dest": parts[1] if len(parts) > 1 else "",
        "layout": parts[2] if len(parts) > 2 else "",
        "checks": check_block,
    }
    return out
# ローカルディレクトリの find/tree スナップショットを取得
def local_find_tree(path_dir: str, maxdepth: Optional[int] = None) -> Dict[str, str]:
    out: Dict[str, str] = {"find": "", "tree": ""}

    q = shlex.quote(path_dir)

    find_cmd = [
        "bash",
        "-lc",
        (
            f"LC_ALL=C find {q} "
            + (f"-maxdepth {int(maxdepth)} " if maxdepth is not None else "")
            + "-printf '%y %p -> %l\\n'"
        ),
    ]
    try:
        r_find = subprocess.run(
            find_cmd,
            check=False,
            capture_output=True,
            text=True,
        )
        out["find"] = (r_find.stdout or "")
    except Exception as e:
        out["find"] = f"(find failed: {e})\n"

    tree_cmd = ["bash", "-lc", f"tree -a {q}"]
    try:
        r_tree = subprocess.run(
            tree_cmd,
            check=False,
            capture_output=True,
            text=True,
        )
        out["tree"] = (r_tree.stdout or "") if r_tree.returncode == 0 else "(tree not available or failed)\n"
    except Exception:
        out["tree"] = "(tree not available or failed)\n"

    return out


# リモート絶対パス base の実体を『base固定で』観測するスナップショット。
# - pwd / ls -la -- <base>
# - find <base> -maxdepth N -printf "%y %p -> %l\n"
# - tree -a <base>（無ければ代替出力）
