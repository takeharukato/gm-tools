## tests/Makefile.am

## tests_py 以下やシェルスクリプトなど、配布に含めたいファイル群
## 実際のファイル構成に合わせて必要に応じて追加してください。
EXTRA_DIST = \
	run_step4.sh \
	run_step5.sh \
	run_step6.sh \
	tests_env.sh.sample \
	tests_py/runner_step4.py \
	tests_py/runner_step5.py \
	tests_py/runner_step6.py

## automake の標準 TESTS 機構は使わず、check-local で run_step*.sh を回す
TESTS =

.PHONY: coverage

## ----------------------------------------
## make check: run_step*.sh をまとめて実行
## ----------------------------------------
check-local:
	@set -e; \
	if ls $(srcdir)/run_step*.sh >/dev/null 2>&1; then \
	  for t in $(srcdir)/run_step*.sh; do \
	    echo "Running $$t"; \
	    /usr/bin/env bash "$$t" || exit 1; \
	  done; \
	else \
	  echo "No run_step*.sh tests found under $(srcdir)"; \
	fi

## ----------------------------------------
## make coverage: Python カバレッジ計測
## - coverage モジュールが無ければスキップして正常終了
## - tests_env.sh があれば読み込んでから runner_step*.py を実行
## ----------------------------------------
coverage:
	@echo "Checking for Python coverage module..."
	@if $(PYTHON) -m coverage --version >/dev/null 2>&1; then \
	  echo "coverage module found; running coverage for gm_tools (Step4/Step5)"; \
	  if test -f ./tests_env.sh; then \
	    echo "Sourcing ./tests_env.sh"; \
	    . ./tests_env.sh; \
	  fi; \
	  rm -f .coverage; \
	  PYTHONPATH="$(top_srcdir)/src" $(PYTHON) -m coverage run --source=gm_tools \
	    $(srcdir)/tests_py/runner_step4.py; \
	  PYTHONPATH="$(top_srcdir)/src" $(PYTHON) -m coverage run --source=gm_tools --append \
	    $(srcdir)/tests_py/runner_step5.py; \
	  PYTHONPATH="$(top_srcdir)/src" $(PYTHON) -m coverage run --source=gm_tools --append \
	    $(srcdir)/tests_py/runner_step6.py; \
	  $(PYTHON) -m coverage report; \
	else \
	  echo "coverage module not available; skipping coverage"; \
	fi
