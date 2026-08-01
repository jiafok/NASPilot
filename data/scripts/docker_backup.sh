#!/bin/bash
# 描述：备份群晖所有Docker容器配置及数据卷
# 用法：sudo ./docker_backup.sh /path/to/backup_folder

# 检查root权限
if [ "$(id -u)" -ne 0 ]; then
  echo "请使用root或sudo运行此脚本！"
  exit 1
fi

# 参数检查
BACKUP_DIR="${1:-/volume1/docker_backup}"
if [ ! -d "$BACKUP_DIR" ]; then
  mkdir -p "$BACKUP_DIR"
fi

# 备份元数据
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_PATH="$BACKUP_DIR/docker_backup_$TIMESTAMP"
mkdir -p "$BACKUP_PATH"

# 1. 备份所有容器配置
echo "➤ 备份容器列表..."
docker ps -a --format '{{.Names}}' > "$BACKUP_PATH/container_list.txt"

# 2. 备份每个容器的配置
echo "➤ 导出容器配置..."
while read -r name; do
  echo "正在备份容器: $name"
  docker inspect "$name" > "$BACKUP_PATH/${name}_inspect.json"
  docker export "$name" -o "$BACKUP_PATH/${name}_container.tar"
done < "$BACKUP_PATH/container_list.txt"

# 3. 备份所有卷数据
echo "➤ 备份数据卷..."
docker volume ls -q | while read -r volume; do
  echo "正在备份卷: $volume"
  docker run --rm -v "$volume:/volume" -v "$BACKUP_PATH:/backup" \
    alpine tar czf "/backup/${volume}_backup.tar.gz" -C /volume .
done

# 4. 备份Docker网络配置
echo "➤ 备份网络配置..."
docker network inspect $(docker network ls -q) > "$BACKUP_PATH/network_config.json"

# 5. 生成还原脚本
cat > "$BACKUP_PATH/restore_docker.sh" << 'EOF'
#!/bin/bash
# Docker还原脚本（需sudo运行）
BACKUP_DIR=$(dirname "$0")
cd "$BACKUP_DIR" || exit 1

# 还原数据卷
for vol_file in *_backup.tar.gz; do
  vol_name=${vol_file%_backup.tar.gz}
  echo "正在还原卷: $vol_name"
  docker volume create "$vol_name"
  docker run --rm -v "$vol_name:/volume" -v "$(pwd):/backup" \
    alpine tar xzf "/backup/$vol_file" -C /volume
done

# 还原容器
while read -r name; do
  echo "正在导入容器: $name"
  docker import "${name}_container.tar" "temp_${name}:restored"
  docker create --name "$name" $(docker inspect -f '{{range .Config.Env}} -e {{.}}{{end}} {{range .Mounts}} -v {{.Name}}:{{.Destination}}{{end}}' "$name") "temp_${name}:restored"
done < "container_list.txt"

# 还原网络配置（如有自定义网络）
if [ -f "network_config.json" ]; then
  echo "➤ 还原网络配置..."
  jq -c '.[]' network_config.json | while read -r net; do
    net_name=$(echo "$net" | jq -r '.Name')
    if [ "$net_name" != "bridge" ] && [ "$net_name" != "host" ] && [ "$net_name" != "none" ]; then
      docker network create "$net_name"
    fi
  done
fi

echo "✅ 还原完成！请手动启动容器：docker start <容器名>"
EOF

chmod +x "$BACKUP_PATH/restore_docker.sh"

# 压缩备份文件
echo "➤ 压缩备份文件..."
tar czf "$BACKUP_DIR/docker_backup_$TIMESTAMP.tgz" -C "$BACKUP_PATH" .
rm -rf "$BACKUP_PATH"

echo "✅ 备份完成！文件保存在: $BACKUP_DIR/docker_backup_$TIMESTAMP.tgz"
