## -*- makefile -*-
## Dockerfiles/Makefile.am for gm-tools
##
## ここでは Docker コンテナを使った RPM / DEB パッケージビルドのみ扱う。
## 通常のビルド (make, make install など) とは完全に独立。

## docker 実行コマンド (必要なら環境変数で上書き可能)
DOCKER ?= docker

## Dockerfile パス (このディレクトリからの相対パス)
RPM_DOCKERFILE = rpm.almalinux9.Dockerfile
DEB_DOCKERFILE = deb.ubuntu24.04.Dockerfile

## 生成する Docker イメージ名
RPM_IMAGE = gm-tools-rpm-build
DEB_IMAGE = gm-tools-deb-build

.PHONY: rpm deb

## ----------------------------------------
## make -C Dockerfile rpm
## ----------------------------------------
rpm:
	@mkdir -p "$(top_builddir)/dist"
	$(DOCKER) build -f $(RPM_DOCKERFILE) -t $(RPM_IMAGE) .
	$(DOCKER) run --rm \
	  -v "$(top_srcdir)":/src \
	  -v "$(top_builddir)/dist":/dist \
	  $(RPM_IMAGE)

## ----------------------------------------
## make -C Dockerfile deb
## ----------------------------------------
deb:
	@mkdir -p "$(top_builddir)/dist"
	$(DOCKER) build -f $(DEB_DOCKERFILE) -t $(DEB_IMAGE) .
	$(DOCKER) run --rm \
	  -v "$(top_srcdir)":/src \
	  -v "$(top_builddir)/dist":/dist \
	  $(DEB_IMAGE)

## 配布アーカイブに含めたい Docker 関連ファイル
EXTRA_DIST = \
	rpm.almalinux9.Dockerfile \
	deb.ubuntu24.04.Dockerfile \
	entrypoint-rpm.sh \
	entrypoint-deb.sh
