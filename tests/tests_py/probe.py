from __future__ import annotations
from typing import Dict
from ._local_types import Config
from .sshexec import run_remote, run_sudo

def is_selinux_supported(cfg: Config, host: str) -> bool:
    r = run_remote(cfg, host, ["command", "-v", "getenforce"])
    return r.rc == 0

def get_selinux_mode(cfg: Config, host: str) -> str:
    if not is_selinux_supported(cfg, host):
        return "Disabled"
    r = run_remote(cfg, host, ["getenforce"])
    if r.rc != 0:
        return "Unknown"
    return r.stdout.strip()

def ensure_dirs_for_case(cfg: Config, host: str, base: str, owner_user: str) -> None:
    run_sudo(cfg, host, ["rm", "-rf", "--", base])
    run_sudo(cfg, host, ["mkdir", "-p", "--", base])
    run_sudo(cfg, host, ["chown", "-R", "--", f"{owner_user}:{owner_user}", base])

def make_sample_tree(cfg: Config, host: str, base: str, user: str) -> None:
    # Create files, symlink, chmod, xattr, ACL
    run_sudo(cfg, host, ["mkdir", "-p", "--", f"{base}/dir/sub"])
    run_sudo(cfg, host, ["chown", "-R", "--", f"{user}:{user}", base])
    run_sudo(cfg, host, ["chmod", "0750", "--", f"{base}/dir"])
    # file content
    pipe_to = f"{base}/dir/file.txt"
    from .sshexec import pipe_to_tee
    pipe_to_tee(cfg, host, pipe_to, "hello\n", sudo=True)
    run_sudo(cfg, host, ["ln", "-sf", "--", f"{base}/dir/file.txt", f"{base}/dir/link.ln"])
    # xattr
    run_sudo(cfg, host, ["setfattr", "-n", "user.k", "-v", "v", "--", pipe_to])
    # ACL (best effort)
    run_sudo(cfg, host, ["setfacl", "-m", f"u:{user}:r--", "--", pipe_to])

def snap_attrs(cfg: Config, host: str, path: str) -> Dict[str,str]:
    out: Dict[str,str] = {}
    r1 = run_remote(cfg, host, ["stat", "-c", "%U:%G", "--", path]); out["owner"]=r1.stdout.strip()
    r2 = run_remote(cfg, host, ["stat", "-c", "%a", "--", path]); out["mode"]=r2.stdout.strip()
    r3 = run_remote(cfg, host, ["getfattr", "-d", "-m", "-", "--", path]); out["xattr"]=r3.stdout.strip()
    r4 = run_remote(cfg, host, ["getfacl", "-p", "--", path]); out["acl"]=r4.stdout.strip()
    r5 = run_remote(cfg, host, ["ls", "-Zd", "--", path]); out["selinux"]=r5.stdout.strip()
    return out
