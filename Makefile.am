# -*- mode: makefile; coding: utf-8; line-endings: unix -*-
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2025 TAKEHARU KATO
#
# This file is distributed under the two-clause BSD license.
# For the full text of the license, see the LICENSE file in the project root directory.
# このファイルは2条項BSDライセンスの下で配布されています。
# ライセンス全文はプロジェクト直下の LICENSE を参照してください。
#
# OpenAI's ChatGPT partially generated this code.
# Author has modified some parts.
# OpenAIのChatGPTがこのコードの一部を生成しました。
# 著者が修正している部分があります。
#
## Top-level Makefile.am for gm-tools

.PHONY: cloc coverage docs rpm deb

## ビルド対象のサブディレクトリ
## gettext を含む po を先頭付近に置いておく
SUBDIRS = \
	po \
	src \
	compl \
	config \
	docs \
	tests \
	debug-patches \
	scripts

## 配布アーカイブに含めたい追加ファイル・ディレクトリ
EXTRA_DIST = \
	autogen.sh \
	requirements.txt \
	Readme.md \
	LICENSE	\
	debian \
	rpm/gm-tools.spec \
	docs/sphinx

# Dockerfiles
EXTRA_DIST += \
    Dockerfiles/Makefile.am \
    Dockerfiles/rpm.almalinux9.Dockerfile \
    Dockerfiles/deb.ubuntu24.04.Dockerfile \
    Dockerfiles/entrypoint-rpm.sh \
    Dockerfiles/entrypoint-deb.sh

## ----------------------------------------
## Lines of Code 集計 (make cloc)
## ----------------------------------------
cloc:
	@if test "x$(CLOC)" = "x:"; then \
	  echo "cloc not found; skipping line count"; \
	else \
	  echo "Running cloc on src/ tests/ compl/ scripts/ ..."; \
	  $(CLOC) src tests compl scripts; \
	fi

## ----------------------------------------
## Python coverage (make coverage)
## 実処理は tests/Makefile.am の coverage ターゲットに委譲
## ----------------------------------------
coverage:
	$(MAKE) -C tests coverage

## ----------------------------------------
## Sphinx ドキュメント生成 (make docs)
## docs/sphinx 以下の Sphinx プロジェクトを HTML ビルドする
## ----------------------------------------
docs:
	@if test "x$(SPHINXBUILD)" = "x:"; then \
	  echo "sphinx-build not found; skipping Sphinx docs (docs/sphinx)"; \
	else \
	  echo "Building Sphinx docs under docs/sphinx/_build/html"; \
	  $(SPHINXBUILD) -b html docs/sphinx docs/sphinx/_build/html; \
	fi

## docker コマンド (必要なら環境変数で上書き可能)
DOCKER ?= docker

RPM_SUBDIR = Dockerfiles
DEB_SUBDIR = Dockerfiles

rpm:
	$(MAKE) -C $(RPM_SUBDIR) rpm

deb:
	$(MAKE) -C $(DEB_SUBDIR) deb
