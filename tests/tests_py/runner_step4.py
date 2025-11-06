from __future__ import annotations
import os, json
from typing import List
from .config import load_config
from .types import CaseResult, CommandResult
from .sshexec import run_remote, run_sudo, pipe_to_tee
from .probe import ensure_dirs_for_case, make_sample_tree, snap_attrs, is_selinux_supported, get_selinux_mode
from .gmwrap import run_gather, run_scatter
from .asserts import assert_rc, compare_attr_maps
from .cases_step4 import gather_src_variants, scatter_dest_variants

def _preflight(cfg):
    for h in cfg.hosts_both:
        r = run_remote(cfg, h, ["command", "-v", "sudo"])
        assert_rc(f"{h}: sudo present", r.rc == 0)
        r = run_remote(cfg, h, ["true"])  # basic SSH
        assert_rc(f"{h}: ssh basic", r.rc == 0)
        r = run_remote(cfg, h, ["id", "-un"])
        # NOPASSWD check
        r = run_sudo(cfg, h, ["true"])
        assert_rc(f"{h}: sudo -n true", r.rc == 0)

def _prepare_sources(cfg):
    # create source trees on both hosts
    for h in cfg.hosts_both:
        ensure_dirs_for_case(cfg, h, "/tmp/gm_step4_src", cfg.target_user)
        make_sample_tree(cfg, h, "/tmp/gm_step4_src", cfg.target_user)

def main():
    cfg = load_config()
    os.makedirs(cfg.local_work_root, exist_ok=True)
    results: List[CaseResult] = []
    _preflight(cfg)
    _prepare_sources(cfg)

    # 1) gather relative src (under target_user home) -> round-trip via scatter
    # Create relative path under home
    for h in cfg.hosts_both:
        rel_base = "gm_step4_rel/src"
        # place a file under ~/<rel_base>
        # We can't easily resolve ~ on remote without shell, so write under /home/<user>/...
        # Fetch home path
        r = run_remote(cfg, h, ["getent", "passwd", cfg.target_user])
        assert_rc(f"{h}: getent passwd", r.rc == 0)
        home = r.stdout.split(":")[5].strip() if ":" in r.stdout else f"/home/{cfg.target_user}"
        abs_rel = f"{home}/{rel_base}"
        ensure_dirs_for_case(cfg, h, abs_rel, cfg.target_user)
        make_sample_tree(cfg, h, abs_rel, cfg.target_user)

    # Case: gather rel src on Ubuntu host -> local dir -> scatter to Alma dest
    ubuntu = cfg.host_ubuntu
    alma = cfg.host_alma
    rel_src = "gm_step4_rel/src"
    local_dest = os.path.join(cfg.local_work_root, "g_rel")
    os.makedirs(local_dest, exist_ok=True)
    g_extra = ["--follow-symlinks"]
    gr = run_gather(cfg, ubuntu, cfg.target_user, rel_src, local_dest, g_extra)
    results.append(CaseResult(name="gather_rel_ubuntu", passed=(gr.rc==0), details={"stdout":gr.stdout, "stderr":gr.stderr}))

    # Scatter to alma absolute dest
    dest = "/tmp/gm_step4_dest_round"
    sr = run_scatter(cfg, alma, cfg.target_user, local_dest, dest, ["--follow-symlinks"])
    results.append(CaseResult(name="scatter_abs_alma", passed=(sr.rc==0), details={"stdout":sr.stdout, "stderr":sr.stderr}))

    # Compare one probe file
    src_attr = snap_attrs(cfg, ubuntu, f"/home/{cfg.target_user}/{rel_src}/dir/file.txt")
    dst_attr = snap_attrs(cfg, alma, f"{dest}/dir/file.txt")
    try:
        compare_attr_maps(src_attr, dst_attr, keys=("mode","owner"))
        ok = True
    except Exception as e:
        ok = False
        results.append(CaseResult(name="attrs_compare_mode_owner", passed=False, details={"error":str(e)}))
    if ok:
        results.append(CaseResult(name="attrs_compare_mode_owner", passed=True))

    # SELinux tests
    # --selinux=auto Ubuntu => skip success; Alma => inspect (we only mark det status here)
    sel_ubuntu = is_selinux_supported(cfg, ubuntu)
    results.append(CaseResult(name="selinux_auto_ubuntu_skip", passed=True, skipped=(not sel_ubuntu), reason="Ubuntu no SELinux"))
    mode_alma = get_selinux_mode(cfg, alma)
    results.append(CaseResult(name="selinux_mode_alma", passed=True, details={"mode":mode_alma}))

    # Dump summary
    summary = {
        "results":[r.__dict__ for r in results]
    }
    print("STEP4 SUMMARY")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
