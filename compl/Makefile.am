## インストール先
bashcompletiondir = $(BASH_COMPLETION_DIR)
zshcompletiondir  = $(ZSH_COMPLETION_DIR)

## インストールする最終ファイル名
bashcompletion_DATA = gm-gather gm-scatter
zshcompletion_DATA  = _gm-gather _gm-scatter

## ビルド時に拡張子付きソースから拡張子なしにコピー
BUILT_SOURCES = $(bashcompletion_DATA) $(zshcompletion_DATA)
CLEANFILES    = $(BUILT_SOURCES)

## 配布物には拡張子付きのソースを入れる
EXTRA_DIST = gm-gather.bash gm-scatter.bash _gm-gather.zsh _gm-scatter.zsh

## 生成ルール
gm-gather: gm-gather.bash
	$(AM_V_GEN)cp $< $@

gm-scatter: gm-scatter.bash
	$(AM_V_GEN)cp $< $@

_gm-gather: _gm-gather.zsh
	$(AM_V_GEN)cp $< $@

_gm-scatter: _gm-scatter.zsh
	$(AM_V_GEN)cp $< $@
