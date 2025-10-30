# -*- mode: makefile-gmake; coding:utf-8 -*-
#
# python用Makefile
#
# Copyright 2019 Takeharu KATO
#
CLOC=cloc
GCOV=gcov
GCOV_OPTS=-l
SED=sed
SORT=sort
GTAGS=gtags
CP=cp
RM=rm -f
LN=ln
MKDIR=mkdir
LCOV=lcov
GENHTML=genhtml
TAR=gtar
GIT=git
GTAGS_CLEAN_FILES=GPATH GRTAGS GSYMS GTAGS
TEX_CLEAN_FILES=*.aux *.bbl *.blg *.dvi *.log *.toc \
	*.ptb *.tod *.fls *.fdb-latexmk *.lof
CLEAN_FILES=
DIST_CLEAN_FILES=\\\#* *~ .*.sw* .config* ..config.* *.log *.tar.gz *.tgz \
	${GTAGS_CLEAN_FILES} *.img
DOXYGEN_ARCHIVE_NAME=$(addprefix $(basename $(notdir $(realpath ${top})))-, $(shell date +%F))

.PHONY: clean distclean gtags dist doxygen cloc cloc-common cloc-doc-common

cloc: cloc-common

#
# 行カウントツールcloc
#
# --exclude-langに指定する言語名は, 自分たちが使用している言語を元に
# cloc  --show-lang
# の出力の1カラム目を参照して除外する言語を指定する。
#

# プログラム行数
# カレントディレクトリ配下のプログラム行数をサブディレクトリを含めて数える
#
# PlantUML,Markdown, shell, Makefileを除いて行数を数える
# python, jsonを含める
#
cloc-common:
	${CLOC} . --by-file --exclude-ext=md,sh,pu,html --exclude-lang=PlantUML,Markdown,shell,make,html


# 文書ファイルの行数を数える
# カレントディレクトリ配下の文書ファイルの行数をサブディレクトリを含めて数える
#
# python, json, shell, Makefileを除いて行数を数える
# PlantUML,Markdownを対象とする
#
cloc-doc-common:
	${CLOC} . --by-file --exclude-ext=py,sh --exclude-lang=python,json,shell,make

api-doc:
	@mkdir -p sphinx
	@sphinx-apidoc -F -o sphinx .
	@make -C sphinx html

doxygen:
	${MAKE} -C doxygen

clean:
	for dir in ${cleandirs} ; do \
	${MAKE} -C $${dir} clean ;\
	done
	${RM} ${CLEAN_FILES} *.tmp *.iso

distclean:clean
	for dir in ${distcleandirs} ; do \
	${MAKE} -C $${dir} distclean ;\
	done
	${RM} ${DIST_CLEAN_FILES}

dist:
	${RM} ${ARCHIVE_NAME}.tar.gz
	${GIT} archive HEAD --format=tar.gz > ${ARCHIVE_NAME}.tar.gz

gtags:
	${RM} ${GTAGS_CLEAN_FILES}
	${GTAGS} -v
