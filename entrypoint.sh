#!/bin/bash
set -e

CONFIG_DIR="${JAVSP_CONFIG_DIR:-/config}"
mkdir -p "$CONFIG_DIR" "$CONFIG_DIR/logs"

# MakeMKV key: 用户可把有效 key 放到 /config/keydata.txt (蓝光解密需要)
# 若不存在, MakeMKV 仍可完整处理 DVD (无加密); 蓝光会提示需要 key
if [ -f "$CONFIG_DIR/keydata.txt" ]; then
    cp -f "$CONFIG_DIR/keydata.txt" /root/.MakeMKV/settings.conf 2>/dev/null || true
    mkdir -p /root/.MakeMKV
    cp -f "$CONFIG_DIR/keydata.txt" /root/.MakeMKV/settings.conf 2>/dev/null || true
fi
# 允许通过环境变量直接注入 key (整行 key 字符串)
if [ -n "$MAKEMKV_KEY" ]; then
    mkdir -p /root/.MakeMKV
    printf 'app_Key = "%s"\n' "$MAKEMKV_KEY" > /root/.MakeMKV/settings.conf
fi

export JIS_CONFIG_DIR="$CONFIG_DIR"
export JIS_LOGS="$CONFIG_DIR/logs"
export JIS_PORT="${WEBUI_PORT:-8088}"

echo "============================================"
echo " ISO Splitter starting"
echo " config: $CONFIG_DIR"
echo " port  : $JIS_PORT"
echo " makemkv: $(makemkvcon -r info 2>/dev/null | head -1 || echo 'available')"
echo "============================================"

cd /opt/iso-splitter
exec python3 webui/app.py
