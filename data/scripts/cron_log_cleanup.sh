#!/bin/sh
set -e

LOG_DIR="/logs"
MAX_SIZE=$((256 * 1024))  # 5MB

echo "[CLEANUP] start $(date '+%F %T')"

# 1️⃣ 清理 30 天未写入的废弃日志
find "$LOG_DIR" -type f -name "*.log" -mtime +30 -print -delete

# 2️⃣ 限制所有活跃日志大小
for f in "$LOG_DIR"/*.log; do
  [ -f "$f" ] || continue
  size=$(stat -c %s "$f")
  if [ "$size" -gt "$MAX_SIZE" ]; then
    echo "[CLEANUP] truncate $f (size=$size)"
    tail -n 2000 "$f" > "$f.tmp" && mv "$f.tmp" "$f"
  fi
done

echo "[CLEANUP] done $(date '+%F %T')"