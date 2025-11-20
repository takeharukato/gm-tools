## src/Makefile.am

## Python モジュールのインストール先
pythondir = @pythondir@/gm_tools

## インストールする Python ファイル群
## gm_tools/*.py に合わせて一覧を調整。
python_PYTHON = \
    gm_tools/__init__.py \
    gm_tools/core_archive.py \
    gm_tools/core_cmd_flavor.py \
    gm_tools/core_common.py \
    gm_tools/core_constants.py \
    gm_tools/core_file_attr.py \
    gm_tools/core_i18n.py \
    gm_tools/core_logging.py \
    gm_tools/core_path_handling.py \
    gm_tools/core_pull.py \
    gm_tools/core_push.py \
    gm_tools/core_remote_fs.py \
    gm_tools/core_remote_path.py \
    gm_tools/core_report.py \
    gm_tools/core_scatter.py \
    gm_tools/core_select.py \
    gm_tools/core_selinux.py \
    gm_tools/core_signal_handling.py \
    gm_tools/core_ssh.py \
    gm_tools/core_xattr.py \
    gm_tools/gather_cli.py \
    gm_tools/gather_parallel.py \
    gm_tools/scatter_cli.py \
    gm_tools/scatter_parallel.py

## configure で生成される _config.py
## ソースツリーには _config.py.in があり、配布対象だが、
## _config.py 自体はビルド時生成なので nodist 扱いにする
nodist_python_PYTHON = \
	gm_tools/_config.py

## 配布に含めたい追加ファイル
EXTRA_DIST = \
	gm_tools/_config.py.in

## インストールする CLI ラッパースクリプト
bin_SCRIPTS = gm-gather gm-scatter

## 生成物の掃除
CLEANFILES = \
	gm-gather \
	gm-scatter

## ----------------------------------------
## gm-gather / gm-scatter の生成ルール
## /usr/bin/env python3 経由で gm_tools.*_cli.main() を呼び出す
## ----------------------------------------

gm-gather: Makefile
	@echo "Generating $@"
	@{ \
	  echo '#!/usr/bin/env python3'; \
    echo '# -*- mode: python; coding: utf-8; line-endings: unix -*-'; \
    echo '# SPDX-License-Identifier: BSD-2-Clause'; \
    echo '# Copyright (c) 2025 TAKEHARU KATO'; \
    echo '#'; \
    echo '# This file is distributed under the two-clause BSD license.'; \
    echo '# For the full text of the license, see the LICENSE file in the project root directory.'; \
    echo '# このファイルは2条項BSDライセンスの下で配布されています。'; \
    echo '# ライセンス全文はプロジェクト直下の LICENSE を参照してください。'; \
    echo '#'; \
    echo "# OpenAI's ChatGPT partially generated this code."; \
    echo '# Author has modified some parts.'; \
    echo '# OpenAIのChatGPTがこのコードの一部を生成しました。'; \
    echo '# 著者が修正している部分があります。'; \
	  echo ''; \
	  echo 'from gm_tools.gather_cli import main'; \
	  echo ''; \
	  echo 'if __name__ == "__main__":'; \
	  echo '    main()'; \
	} > $@
	@chmod +x $@

gm-scatter: Makefile
	@echo "Generating $@"
	@{ \
	  echo '#!/usr/bin/env python3'; \
    echo '# -*- mode: python; coding: utf-8; line-endings: unix -*-'; \
    echo '# SPDX-License-Identifier: BSD-2-Clause'; \
    echo '# Copyright (c) 2025 TAKEHARU KATO'; \
    echo '#'; \
    echo '# This file is distributed under the two-clause BSD license.'; \
    echo '# For the full text of the license, see the LICENSE file in the project root directory.'; \
    echo '# このファイルは2条項BSDライセンスの下で配布されています。'; \
    echo '# ライセンス全文はプロジェクト直下の LICENSE を参照してください。'; \
    echo '#'; \
    echo "# OpenAI's ChatGPT partially generated this code."; \
    echo '# Author has modified some parts.'; \
    echo '# OpenAIのChatGPTがこのコードの一部を生成しました。'; \
    echo '# 著者が修正している部分があります。'; \
	  echo ''; \
	  echo 'from gm_tools.scatter_cli import main'; \
	  echo ''; \
	  echo 'if __name__ == "__main__":'; \
	  echo '    main()'; \
	} > $@
	@chmod +x $@
