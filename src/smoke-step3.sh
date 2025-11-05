# ローカル側のテストツリー
SRC=/tmp/gm_src_step3
rm -rf "$SRC"; mkdir -p "$SRC"

# 通常ファイル ( NEW )
mkdir -p "$SRC/dirA"
echo "hello A" > "$SRC/dirA/a.txt"

# 非空ディレクトリ
mkdir -p "$SRC/dirB/sub"
echo "data" > "$SRC/dirB/sub/b.txt"

# 空ディレクトリ ( 重要：これが pack でも作られることを確認 )
mkdir -p "$SRC/empty1"
mkdir -p "$SRC/empty2/nested"
# empty2 自体は空ではない ( nested がある )  =>  empty2 は「空ディレクトリ」には該当しない想定
# nested は空ディレクトリ

# シンボリックリンク ( packではアーカイブに入るが, list_tar_members_local()で非採用 )
ln -s /etc/hosts "$SRC/link_to_file"
ln -s "$SRC/dirA" "$SRC/link_to_dir"

PYTHONPATH=. python3 smoke_step3.py
