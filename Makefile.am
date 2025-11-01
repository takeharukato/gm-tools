## サブディレクトリのみ
SUBDIRS = src compl scripts docs locale samples
# 配布物
EXTRA_DIST=requirements.txt LICENSE autogen.sh

# Install the top-level license into $(docdir) (defaults to $(datarootdir)/doc/$(PACKAGE))
dist_doc_DATA = LICENSE



## 便利ターゲットを置きたい場合はここに追加できます。
.PHONY: cloc gtags doxygen api-doc gen-pot
CLOC               ?= cloc
CLOC_EXCLUDE_DIRS  ?= .git,autom4te.cache,build-aux,.venv,.vscode
CLOC_NOT_MATCH_F   ?= '(^|/)(configure|Makefile\.in|config\.h\.in|aclocal\.m4|ltmain\.sh|config\.guess|config\.sub|config\.status|compile|depcomp|install-sh|missing|stamp-h1|libtool)$$'

GTAGS ?= gtags
RM    ?= rm -f

# gettextのpotファイルを生成する
gen-pot:
	${top_srcdir}/scripts/gen-pot.sh

cloc:
	$(CLOC) . --by-file \
	  --exclude-dir=$(CLOC_EXCLUDE_DIRS) \
	  --not-match-f=$(CLOC_NOT_MATCH_F) \
	  --exclude-ext=md,pu,html \
	  --exclude-lang=PlantUML,Markdown,make,html

gtags:
	$(RM) GPATH GRTAGS GSYMS GTAGS
	$(GTAGS) -v

doxygen:
	@$(MAKE) -C doxygen || true

api-doc:
	@mkdir -p sphinx
	@sphinx-apidoc -F -o sphinx . || true
	@$(MAKE) -C sphinx html || true
