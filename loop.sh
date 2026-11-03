#!/bin/sh
# AI 视界：每 20 分钟刷新一轮
mkdir -p /data/home/airadar
while true; do
  python3 -u /data/home/airadar/airadar.py >> /data/home/airadar/loop.log 2>&1
  sleep 1200
done
