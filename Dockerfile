# ISO Splitter - 光盘 ISO 识别/分割/刮削 WebUI
# 基于 Ubuntu 24.04, 内置 MakeMKV(无损 remux, 保留音轨/字幕/章节) + ffmpeg + Python Flask
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    MAKEKV_VER=2.0.0 \
    WEBUI_PORT=8088

# 换国内 apt 源加速(用 http, 因为基础镜像还没有 ca-certificates)
RUN sed -i 's|http://archive.ubuntu.com/ubuntu|http://mirrors.aliyun.com/ubuntu|g; s|http://security.ubuntu.com/ubuntu|http://mirrors.aliyun.com/ubuntu|g' /etc/apt/sources.list.d/ubuntu.sources 2>/dev/null || \
    sed -i 's|http://archive.ubuntu.com/ubuntu|http://mirrors.aliyun.com/ubuntu|g; s|http://security.ubuntu.com/ubuntu|http://mirrors.aliyun.com/ubuntu|g' /etc/apt/sources.list

# 基础依赖 + MakeMKV 编译依赖 + ffmpeg (ffprobe 包含在 ffmpeg 包中)
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv \
        ffmpeg \
        curl wget ca-certificates \
        build-essential pkg-config \
        libc6-dev libssl-dev libexpat1-dev \
        libavcodec-dev libavformat-dev libavutil-dev \
        qtbase5-dev libgl1-mesa-dev \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# 编译安装 MakeMKV (oss -> bin)
RUN cd /tmp \
    && wget -q "https://www.makemkv.com/download/makemkv-oss-${MAKEKV_VER}.tar.gz" -O oss.tgz \
    && wget -q "https://www.makemkv.com/download/makemkv-bin-${MAKEKV_VER}.tar.gz" -O bin.tgz \
    && tar xzf oss.tgz && tar xzf bin.tgz \
    && cd /tmp/makemkv-oss-${MAKEKV_VER} \
    && ./configure && make -j"$(nproc)" && make install \
    && cd /tmp/makemkv-bin-${MAKEKV_VER} \
    && mkdir -p tmp && echo accepted > tmp/eula_accepted \
    && make -j"$(nproc)" && make install \
    && cd / && rm -rf /tmp/oss.tgz /tmp/bin.tgz /tmp/makemkv-oss-${MAKEKV_VER} /tmp/makemkv-bin-${MAKEKV_VER} \
    && ldconfig \
    && test -x /usr/bin/makemkvcon

# Python 依赖(用阿里云 pypi 源加速)
RUN pip3 install --no-cache-dir --break-system-packages \
        -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com \
        flask requests pyyaml

WORKDIR /opt/iso-splitter
COPY webui /opt/iso-splitter/webui
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# /config 持久化配置, /media 及任意目录由用户挂载
VOLUME ["/config"]
EXPOSE 8088

ENTRYPOINT ["/entrypoint.sh"]
