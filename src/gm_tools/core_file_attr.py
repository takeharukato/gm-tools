# -*- coding:utf-8 -*-
from __future__ import annotations

import os
import pwd
import grp
import stat
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class FileAttrs:
    """
    適用したい属性の指定（None は「変更しない」）。
    """
    mode: Optional[int] = None   # 例: 0o644
    uid: Optional[int] = None
    gid: Optional[int] = None


@dataclass(frozen=True)
class ApplyResult:
    """
    apply_attrs_best_effort の結果サマリ。
    """
    path: str
    changed_mode: bool
    changed_owner: bool
    changed_group: bool
    error: Optional[str] = None


def get_current_attrs(path: str) -> FileAttrs:
    """
    現在のモード/UID/GID を返す。
    """
    st: os.stat_result = os.stat(path, follow_symlinks=False)
    mode_now: int = stat.S_IMODE(st.st_mode)
    uid_now: int = st.st_uid
    gid_now: int = st.st_gid
    return FileAttrs(mode=mode_now, uid=uid_now, gid=gid_now)


def resolve_user(user: Optional[int | str | None]) -> Optional[int]:
    """
    ユーザー指定（数値UID or 名前 or None）を数値UIDに解決。解決不可なら None。
    """
    if user is None:
        return None
    if isinstance(user, int):
        return user
    # 名前指定
    try:
        pw: pwd.struct_passwd = pwd.getpwnam(user)
        return int(pw.pw_uid)
    except Exception:
        return None


def resolve_group(group: Optional[int | str | None]) -> Optional[int]:
    """
    グループ指定（数値GID or 名前 or None）を数値GIDに解決。解決不可なら None。
    """
    if group is None:
        return None
    if isinstance(group, int):
        return group
    try:
        gr: grp.struct_group = grp.getgrnam(group)
        return int(gr.gr_gid)
    except Exception:
        return None


def apply_attrs_best_effort(path: str, attrs: FileAttrs) -> ApplyResult:
    """
    可能な範囲で mode / uid / gid を適用する。
    - どれかの適用で例外が起きても残りは試す（best-effort）
    - 失敗した理由は error に格納（最後のエラーを記録）
    """
    changed_mode: bool = False
    changed_owner: bool = False
    changed_group: bool = False
    last_err: Optional[str] = None

    # mode
    if attrs.mode is not None:
        try:
            os.chmod(path, int(attrs.mode), follow_symlinks=False)
            changed_mode = True
        except Exception as ex1:
            last_err = f"chmod failed: {ex1!s}"

    # owner/group
    uid_target: int = -1
    gid_target: int = -1
    need_chown: bool = False
    if attrs.uid is not None:
        uid_target = int(attrs.uid)
        need_chown = True
    if attrs.gid is not None:
        gid_target = int(attrs.gid)
        need_chown = True

    if need_chown:
        try:
            os.chown(path, uid_target if uid_target >= 0 else -1, gid_target if gid_target >= 0 else -1, follow_symlinks=False)
            if attrs.uid is not None:
                changed_owner = True
            if attrs.gid is not None:
                changed_group = True
        except Exception as ex2:
            last_err = f"chown failed: {ex2!s}"

    return ApplyResult(
        path=path,
        changed_mode=changed_mode,
        changed_owner=changed_owner,
        changed_group=changed_group,
        error=last_err,
    )
