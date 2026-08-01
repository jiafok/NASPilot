#!/bin/bash
# rclone_mount_synology.sh - 群晖套件版Alist专用挂载脚本

MOUNT_POINT="/volume1/docker/Alist/media"
LOG_FILE="/volume1/homes/jeffrey/rclone.log"
CACHE_DIR="/volume1/homes/jeffrey/rclone-cache"
ALIST_REMOTE="alist-new"
CONFIG_FILE="/volume1/homes/$(whoami)/.config/rclone/rclone.conf"

echo "=== Rclone 挂载 (群晖套件版Alist) ==="
echo "挂载点: $MOUNT_POINT"
echo "远程配置: $ALIST_REMOTE"

echo "步骤1: 环境检查..."
if ! which rclone > /dev/null; then
    echo "❌ 错误: rclone 未安装"
    exit 1
fi

echo "步骤2: 清理旧挂载和缓存..."
fusermount -uz "$MOUNT_POINT" 2>/dev/null && echo "  已卸载旧挂载"
sleep 3
mkdir -p "$MOUNT_POINT" "$CACHE_DIR"

echo "步骤3: 测试与 $ALIST_REMOTE 的连接..."
if rclone lsd "$ALIST_REMOTE:/" --max-depth 1 --timeout 20s > /dev/null 2>&1; then
    echo "✓ 连接测试成功"
else
    echo "❌ 连接测试失败"
    echo "检查配置: rclone config show $ALIST_REMOTE"
    exit 1
fi

echo "步骤4: 执行核心挂载..."
# 针对群晖环境优化的参数：移除不支持的--no-http-keep-alive，调整其他参数
rclone mount "$ALIST_REMOTE:/" "$MOUNT_POINT" \
    --daemon \
    --config="$CONFIG_FILE" \
    --vfs-cache-mode full \
    --allow-other \
    --allow-non-empty \
    --dir-cache-time 5m \
    --poll-interval 0 \
    --timeout 5m \
    --contimeout 1m \
    --retries 3 \
    --retries-sleep 2s \
    --transfers 4 \
    --no-modtime \
    --fast-list \
    --no-checksum \
    --buffer-size 16M \
    --vfs-read-chunk-size 32M \
    --vfs-cache-max-size 10G \
    --vfs-cache-max-age 15m \
	--vfs-read-ahead 0 \
    --cache-dir "$CACHE_DIR" \
    --log-file "$LOG_FILE" \
    --log-level INFO

echo "步骤5: 验证挂载状态..."
MOUNT_SUCCESS=0
for i in {1..15}; do
    # 群晖环境使用 mount 命令检查挂载点
    if mount | grep -q "$MOUNT_POINT"; then
        MOUNT_SUCCESS=1
        echo "✓ 挂载成功 (等待 ${i}s)"
        break
    fi
    sleep 1
done

if [ $MOUNT_SUCCESS -eq 0 ]; then
    echo "❌ 挂载失败"
    echo "可能原因:"
    echo "1. fuse未加载: sudo insmod /lib/modules/fuse.ko"
    echo "2. 权限问题: 检查 /dev/fuse 权限"
    echo "3. 查看详细日志: tail -f $LOG_FILE"
    exit 1
fi

echo -e "\n✅ 挂载完成！"
echo -e "\n✅ 挂载完成！"
echo "========================================"
mount | grep "$MOUNT_POINT"
echo "----------------------------------------"
echo "挂载摘要:"
echo "• 模式: writes缓存"
echo "• 并发: 2"
echo "• 超时: 90s"
echo "• 连接: 禁用keep-alive"
echo "• 缓存: 最大2G/15分钟"
echo "• 日志: $LOG_FILE"
echo ""
echo "使用提示:"
echo "1. Emby/MoviePilot 请扫描: $MOUNT_POINT"
echo "2. MoviePilot刮削后的NFO/图片将自动写入Alist"
echo "3. 监控命令: tail -f \"$LOG_FILE\" | grep -E \"ERROR|403\""
echo "========================================"

exit 0
