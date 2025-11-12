from __future__ import annotations

# SSH defaults
SSH_PORT_DEFAULT: int = int(__import__("os").environ.get("SSH_PORT", "22"))
SSH_STRICT_DEFAULT: str = __import__("os").environ.get("SSH_STRICT", "no")

# Paths
REMOTE_DEST_ROOT_DEFAULT: str = __import__("os").environ.get("REMOTE_DEST_ROOT", "/tmp/gmtools_remote_dest")
LOCAL_WORK_ROOT_DEFAULT: str = __import__("os").environ.get("LOCAL_WORK_ROOT", "./_tmp_test_local")

# Users / Hosts
SSH_USER_DEFAULT: str = __import__("os").environ.get("SSH_USER", "ansible")
TARGET_USER_DEFAULT: str = __import__("os").environ.get("TARGET_USER", "ansible")
HOSTS_BOTH_DEFAULT: str = __import__("os").environ.get("HOSTS_BOTH", "localhost vmlinux4.local")
HOST_UBUNTU_DEFAULT: str = __import__("os").environ.get("HOST_UBUNTU", "localhost")
HOST_ALMA_DEFAULT: str = __import__("os").environ.get("HOST_ALMA", "vmlinux4.local")

# gm commands (split by shlex)
GM_GATHER_CMD_DEFAULT: str = __import__("os").environ.get("GM_GATHER_CMD", "python3 -m gm_tools.gather_cli")
GM_SCATTER_CMD_DEFAULT: str = __import__("os").environ.get("GM_SCATTER_CMD", "python3 -m gm_tools.scatter_cli")

# Behavior
VERBOSE_DEFAULT: bool = __import__("os").environ.get("VERBOSE", "1") == "1"

# Step5
PARALLEL_DEFAULT: int = int(__import__("os").environ.get("PARALLEL", "2"))
