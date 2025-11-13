# Ubuntu 24.04 ベースの DEB ビルドコンテナ
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install -y \
        build-essential \
        devscripts \
        debhelper \
        dh-python \
        python3 \
        python3-dev \
        python3-pip \
        python3-setuptools \
        git \
        tar \
        gzip \
        xz-utils \
        autoconf \
        automake \
        gettext \
        pkg-config && \
    rm -rf /var/lib/apt/lists/*

# ビルド用ユーザ
RUN useradd -m builder
USER builder
WORKDIR /home/builder/build

# /src: ソースツリー, /dist: 出力 .deb を置くディレクトリ をマウントする想定
COPY entrypoint-deb.sh /usr/local/bin/entrypoint-deb.sh
RUN chmod +x /usr/local/bin/entrypoint-deb.sh

ENTRYPOINT ["/usr/local/bin/entrypoint-deb.sh"]
