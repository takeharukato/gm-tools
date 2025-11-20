#!/usr/bin/env bash
# -*- mode: bash; coding: utf-8; line-endings: unix -*-
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

set -euo pipefail

# autogen.sh が置かれているディレクトリ (ソースツリーのトップ)へ移動
srcdir=$(
  CDPATH= cd -- "$(dirname "$0")" && pwd
)
cd "$srcdir"

if [ ! -f configure.ac ]; then
  echo "autogen.sh: error: configure.ac not found in ${srcdir}" >&2
  exit 1
fi

echo "==> Running autoreconf to generate configure and related files..."
autoreconf --install --force

echo "==> Done. Now run ./configure (with any options you need), then make."
