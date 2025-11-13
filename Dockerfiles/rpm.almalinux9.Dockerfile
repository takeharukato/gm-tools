# AlmaLinux 9 ベースの RPM ビルドコンテナ
FROM almalinux:9

# 必要なツール類をインストール
RUN dnf -y update && \
    dnf -y install \
        rpm-build \
        make \
        gcc \
        git \
        tar \
        gzip \
        xz \
        python3 \
        python3-devel \
        python3-pip \
        gettext \
        autoconf \
        automake \
        libtool && \
    dnf clean all

# ビルド用ユーザ (必要なら追加)
RUN useradd -m builder
USER builder
WORKDIR /home/builder/build

# ホスト側からソースツリーをマウントして使う想定
# (make rpm ターゲットで -v オプションを指定)
#
# コンテナ内では:
#   /src  : gm-tools のソースツリー
#   /dist : 出力 RPM を置くディレクトリ
#
# entrypoint-rpm.sh で:
#   cd /src
#   ./autogen.sh
#   ./configure
#   make
#   make rpm (あるいは rpmbuild 呼び出し) を実行する

# エントリスクリプトをコピー (リポジトリ内のファイルを使う場合)
COPY entrypoint-rpm.sh /usr/local/bin/entrypoint-rpm.sh
RUN chmod +x /usr/local/bin/entrypoint-rpm.sh

ENTRYPOINT ["/usr/local/bin/entrypoint-rpm.sh"]
