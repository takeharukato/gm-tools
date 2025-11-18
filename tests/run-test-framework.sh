#!/usr/bin/env bash
PYTHONPATH=. python3 -m pytest -q ../tests/test_test_common_cleanup.py
PYTHONPATH=. python3 -m pytest -q ../tests/test_test_common_config.py
PYTHONPATH=. python3 -m pytest -q ../tests/test_test_common_ssh.py
