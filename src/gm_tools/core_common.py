#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# cspell:ignore hostfile
from __future__ import annotations

import re
from typing import List

def parse_hosts_file(path: str) -> List[str]:
    """
    Read a hosts file (UTF-8), ignoring blank lines and comments.
    Inline comments starting with a space followed by '#' are stripped.
    """
    hosts: List[str] = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            s: str = raw.strip()
            if not s or s.startswith("#"):
                continue
            s = re.split(r"\s+#", s, 1)[0].strip()
            if s:
                hosts.append(s)
    return hosts
