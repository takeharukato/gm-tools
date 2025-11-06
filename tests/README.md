# gm_tools Smoke Tests (Step4 & Step5)

This suite provides **regression (Step4)** and **parallel-transfer (Step5)** smoke tests for `gm-gather` and `gm-scatter`.
It assumes two test hosts and two users:

- **Users**
  - `ansible` (SSH public key login, passwordless `sudo`)
  - `root`
- **Hosts**
  - `localhost` (no SELinux)
  - `vmlinux` (SELinux Permissive)

> NOTE: By design, **no `.ssh/config`** is consulted by these scripts. They pass SSH options explicitly.

## Coverage map

### Step4 (permissions/SELinux path)

- Gather: SFTP & `--pack`
- Scatter: SFTP
- `-x/--sudo-collect` for gather (privileged files)
- `--follow-symlinks` with `--pack` for gather
- **Path interpretations**
  - DEST absolute (`/...`), `~/...`, relative (gather only)
  - SRC absolute (`/...`, `~/...`)
  - Scatter SRC relative (S-REL-01) resolved against CWD, uploaded to `DEST/<local_abs_without_leading_slash>`

### Step5 (parallel path)

- Concurrent hosts `-j N` basic success
- Fail-fast/abort is out of scope for smoke level (covered in deeper tests)

## Layout

- `tests/env.sh` — environment and constants you must review
- `tests/lib/ssh.sh` — tiny SSH helpers without using `.ssh/config`
- `tests/hosts/hosts_both` — example hosts file for `-H`
- `tests/run_smoke_step4.sh` — Step4 + S-REL-01
- `tests/run_smoke_step5.sh` — Step5

## Quick start

```bash
# 0) Adjust tests/env.sh to your paths and keys
$ sed -n '1,160p' tests/env.sh

# 1) Export env and run Step4 suite (incl. S-REL-01)
$ source tests/env.sh
$ bash tests/run_smoke_step4.sh

# 2) Step5 (parallel)
$ bash tests/run_smoke_step5.sh
```

## Notes

- The scripts create temp dirs under `tests/_tmp_*` and clean selectively.
- They set `set -euo pipefail` and fail on first error; inspect logs per section.
- Expected paths for scatter follow: `DEST/<local_abs_without_leading_slash>`.
- Expected paths for gather follow: `DEST/<HOST>/...`
- The remote deployment destination defaults to /tmp/gm_scatter_dest (can be changed via env.sh).
- Generated/verified outputs are consolidated under tests/output/.

## Additional Notes

GM_GATHER_CMD / GM_SCATTER_CMD default to gm-gather / gm-scatter.
If not already installed gm-gather / gm-scatter, overwrite them in env.sh as follows:

```:shell
export GM_GATHER_CMD="python -m gm_tools.gather_cli"
export GM_SCATTER_CMD="python -m gm_tools.scatter_cli"
```
