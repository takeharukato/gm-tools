from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Any

@dataclass
class CommandResult:
    rc: int
    stdout: str
    stderr: str

@dataclass
class HostConfig:
    name: str
    is_selinux_supported: bool
    selinux_mode: str  # "Enforcing" | "Permissive" | "Disabled" | "Unknown"

@dataclass
class Config:
    ssh_user: str
    target_user: str
    hosts_both: List[str]
    host_ubuntu: str
    host_alma: str
    ssh_port: int
    ssh_strict: str
    ssh_strict_bool: bool
    remote_dest_root: str
    local_work_root: str
    local_root: str
    gm_gather_cmd: List[str]
    gm_scatter_cmd: List[str]
    verbose: bool
    parallel: int

@dataclass
class CaseResult:
    name: str
    passed: bool
    skipped: bool = False
    reason: str = ""
    details: Dict[str, Any] = field(default_factory=dict) # type: ignore

    def to_dict(self) -> Dict[str, Any]:
        """JSON 出力用の dict に変換する。"""
        return {
            "name": self.name,
            "passed": self.passed,
            "skipped": self.skipped,
            "reason": self.reason,
            "details": self.details,
        }
