#!/bin/bash
# =========================================================
# Docker 应用核心数据统一备份脚本（最终定稿版）
# - 不依赖 docker-compose.yml 的位置
# - 自动发现应用
# - 排除 media / download / cache
# - v2raya 采用白名单备份
# - 所有应用 → 一个压缩包
# =========================================================

set -e

# ===== 基本路径配置 =====
DOCKER_ROOT="/volume1/docker"
BACKUP_ROOT="/volumeUSB1/usbshare/docker_backup"
DATE=$(date +%Y%m%d_%H%M%S)

TMP_DIR="$BACKUP_ROOT/docker_all_core_$DATE"
ARCHIVE="$BACKUP_ROOT/docker_all_core_$DATE.tgz"

# ===== 识别“这是应用”的核心目录名 =====
DATA_DIR_NAMES=(config data conf db)

# ===== rsync 通用参数（兼容 USB / FAT / NTFS）=====
RSYNC_OPTS=(
  -r
  --ignore-errors
  --iconv=utf-8,utf-8
  --no-owner
  --no-group
  --no-perms
  --no-times

  # ========= 明确不要的（黑名单）=========

  # 大数据目录：只保留目录结构，不拷内容
  --exclude 'media/***'
  --exclude 'downloads/***'
  --exclude 'download/***'
  --exclude 'movies/***'
  --exclude 'tv/***'
  --exclude 'music/***'

  # 缓存 / 临时 / 日志（不管在哪一层）
  --exclude '*/cache/***'
  --exclude '*/tmp/***'
  --exclude '*/temp/***'
  --exclude '*/logs/***'
  --exclude '*/transcode/***'

  # Emby / Jellyfin 图片缓存
  --exclude '*/imagecache/***'
  --exclude '*/metadata/library/**/imagecache/***'
)

# ===== v2raya 白名单文件（只备这些）=====
V2RAYA_FILES=(
  "config.json"
  "subscribe.json"
  "subscribe*.json"
  "routing*.json"
)

mkdir -p "$TMP_DIR"

echo "📦 开始 Docker 应用核心数据备份"
echo "📂 扫描目录: $DOCKER_ROOT"
echo "📦 生成文件: $ARCHIVE"
echo

# =========================================================
# 主循环：遍历 docker 根目录
# =========================================================
for APP_DIR in "$DOCKER_ROOT"/*; do
  [ -d "$APP_DIR" ] || continue

  APP_NAME=$(basename "$APP_DIR")

  # ---- 判断是否“像一个应用”（是否包含核心目录）----
  HAS_DATA=false
  for d in "${DATA_DIR_NAMES[@]}"; do
    if [ -d "$APP_DIR/$d" ]; then
      HAS_DATA=true
      break
    fi
  done

  if ! $HAS_DATA; then
    echo "⏭️  跳过（无核心数据）: $APP_NAME"
    continue
  fi

  echo "🔹 收集应用数据: $APP_NAME"
  APP_DEST="$TMP_DIR/$APP_NAME"
  mkdir -p "$APP_DEST"

  # ---- 复制核心数据 ----
  # ===== v2raya 特殊处理 =====
  if [ "$APP_NAME" = "v2raya" ]; then
    mkdir -p "$APP_DEST/config"
    for f in "${V2RAYA_FILES[@]}"; do
      if ls "$APP_DIR/config/$f" &>/dev/null; then
        for realfile in "$APP_DIR/config"/$f; do
          echo "      ↳ v2raya 配置文件: $(basename "$realfile")"
          cp "$realfile" "$APP_DEST/config/"
        done
      fi
    done
  else
    rsync "${RSYNC_OPTS[@]}" "$APP_DIR/" "$APP_DEST/"
  fi

  # ---- 顺带带上 compose / .env（有就备，没有拉倒）----
  # 备份 docker compose 文件（支持多种命名）
  for COMPOSE_FILE in \
    docker-compose.yml \
    docker-compose.yaml \
    compose.yml \
    compose.yaml; do

    if [ -f "$APP_DIR/$COMPOSE_FILE" ]; then
      echo "   📄 备份 compose 文件: $COMPOSE_FILE"
      cp "$APP_DIR/$COMPOSE_FILE" "$APP_DEST/"
    fi

  done

  [ -f "$APP_DIR/.env" ] && cp "$APP_DIR/.env" "$APP_DEST/"

  echo
done

# =========================================================
# 打包收尾
# =========================================================
cd "$BACKUP_ROOT"
tar czf "$ARCHIVE" "$(basename "$TMP_DIR")" || true
rm -rf "$TMP_DIR"

echo "✅ ✅ ✅ 备份完成：$ARCHIVE"

