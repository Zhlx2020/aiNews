#!/bin/sh
# 开机自启：只保留 AI 视界（静态服务 + 采集循环）
sleep 25
cd /data/home/airadar 2>/dev/null && setsid python3 serve.py >/dev/null 2>&1 &
setsid sh /data/home/airadar/loop.sh >/dev/null 2>&1 &
echo boot-done >> /data/home/boot.log
