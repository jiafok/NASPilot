#!/bin/sh
set -e

# === 基本环境 ===
export PYTHONUNBUFFERED=1
export HOME=/tmp       # 防止写用户目录
export TZ=Asia/Shanghai

# === 防止并发运行（非常重要） ===
LOCK="/tmp/pt_rss_auto.lock"
exec 9>"$LOCK" || exit 1
flock -n 9 || {
  echo "[SKIP] pt-rss-auto already running"
  exit 0
}

# === 执行 ===
exec python3 /scripts/pt_rss_auto.py
