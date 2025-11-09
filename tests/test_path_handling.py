#
# src/で,
#
# PYTHONPATH=. python3 -m pytest -q ../tests/test_path_handling.py
#
import re
from gm_tools.core_path_handling import (
    _sanitize_remote_abs_for_local,
    split_src_to_root_and_tail_regex,
    is_abs_path,
)
def test_posix_simple_abs():
    assert _sanitize_remote_abs_for_local("/etc/hosts") == "etc/hosts"

def test_unc_preserve_double_slash_and_empties():
    # 先頭 '//' は 1 本だけ落ちる -> 先頭空セグメントが '_' で残る
    assert _sanitize_remote_abs_for_local("//server/share") == "_/server/share"

def test_multiple_slashes_not_collapsed_and_trailing_ignored():
    # 連続 '/' は畳まない -> 空セグメントは '_' に
    # 末尾 '/' 由来の最後の空は無視
    assert _sanitize_remote_abs_for_local("//a//b/") == "_/a/_/b"

def test_windows_drive_letter_to_C_():
    assert _sanitize_remote_abs_for_local("C:/Windows/System32") == "C_/Windows/System32"

def test_backslashes_to_slashes_windows_style():
    assert _sanitize_remote_abs_for_local(r"C:\Users\name\Desktop\\") == "C_/Users/name/Desktop"

def test_forbidden_chars_and_trailing_strip():
    assert _sanitize_remote_abs_for_local("/dir/na:me.txt") == "dir/na_me.txt"
    assert _sanitize_remote_abs_for_local("/dir/file. ") == "dir/file"

def test_all_slashes_retreat_to_underscore():
    assert _sanitize_remote_abs_for_local("/") == "_"
    assert _sanitize_remote_abs_for_local("///") == "_"

def test_internal_empty_segment_becomes_underscore():
    assert _sanitize_remote_abs_for_local("/a//b") == "a/_/b"

def test_reserved_device_names_safe():
    # 小文字でも予約名として安全化（ケース保持）
    assert _sanitize_remote_abs_for_local("/com1/lpt9") == "_com1/_lpt9"
    # 拡張子付きでもベース名が予約名なら安全化する
    assert _sanitize_remote_abs_for_local("/CON.txt") == "_CON.txt"
    assert _sanitize_remote_abs_for_local("/nul.log") == "_nul.log"
    assert _sanitize_remote_abs_for_local("/myCON/file") == "myCON/file"

def test_component_becomes_empty_after_strip():
    # 'name. ' -> 末尾ドット/スペース削除で 'name'
    assert _sanitize_remote_abs_for_local("/dir/name. ") == "dir/name"
    # comp が全部ドットとスペースで出来ている場合は空→'_' に
    assert _sanitize_remote_abs_for_local("/dir/ . ") == "dir/_"

def test_backslashes_and_trailing_double_slash():
    # 末尾の空セグメントは連続で全て無視（末尾 '_' は付与しない）
    assert _sanitize_remote_abs_for_local(r"C:\Users\name\Desktop\\") == "C_/Users/name/Desktop"