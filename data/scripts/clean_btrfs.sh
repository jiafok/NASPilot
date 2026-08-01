#!/bin/bash

# 定义子卷目录
SUBVOL_DIR="/volume1/@docker/btrfs/subvolumes/"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 超时设置（秒）
TIMEOUT_SECONDS=300  # 5分钟
TIMEOUT_EXECUTE=true  # 超时后自动执行删除

# 全局变量
declare -A subvol_map
ALL_SUBVOLS=()
USED_SUBVOLS=()
ORPHAN_SUBVOLS=()
OLD_SUBVOLS=()

# 兼容Synology的日期计算函数
calculate_days_ago() {
    local days=$1
    # 使用date命令计算days天前的时间戳（兼容Synology）
    if date --version 2>&1 | grep -q GNU; then
        # GNU date (Linux)
        date -d "$days days ago" +%s
    else
        # BusyBox date (Synology)
        current=$(date +%s)
        echo $((current - days * 86400))
    fi
}

# 计算30天和60天前的时间戳（兼容Synology）
THIRTY_DAYS_AGO=$(calculate_days_ago 30)
SIXTY_DAYS_AGO=$(calculate_days_ago 60)

# 超时处理函数
timeout_handler() {
    echo -e "\n\n${YELLOW}⚠️  超过${TIMEOUT_SECONDS}秒无操作，将自动执行清理任务...${NC}"
    
    if [ "$TIMEOUT_EXECUTE" = true ]; then
        # 确保已扫描子卷
        if [ ${#ALL_SUBVOLS[@]} -eq 0 ]; then
            echo -e "${YELLOW}正在扫描子卷信息...${NC}"
            scan_subvolumes_silent
        fi
        
        # 执行自动清理
        echo -e "\n${RED}=== 自动清理：删除超过60天的孤儿子卷 ===${NC}"
        auto_delete_old_subvols 60 "$SIXTY_DAYS_AGO"
    fi
    
    echo -e "\n${YELLOW}脚本自动退出${NC}"
    exit 0
}

# 静默扫描子卷（用于超时自动执行）
scan_subvolumes_silent() {
    # 获取所有子卷ID和路径
    ALL_SUBVOLS=($(sudo btrfs subvolume list /volume1 | grep '@docker/btrfs/subvolumes/' | grep -oP '(?<=subvolumes/)[0-9a-f]+(-init)?' | sort -u))

    # 获取Docker容器和镜像的ID
    USED_IDS=$( (docker ps -aq; docker images -aq) | xargs -I {} docker inspect -f '{{.Id}} {{.Id | substr 0 12}}' {} 2>/dev/null | tr ' ' '\n' | sort -u )
    GRAPH_IDS=$( (docker ps -aq; docker images -aq) | xargs -I {} docker inspect -f '{{.GraphDriver.Data}}' {} 2>/dev/null | grep -oP '[0-9a-f]{64}|[0-9a-f]{12}' | sort -u )
    ALL_USED_IDS=$(echo -e "$USED_IDS\n$GRAPH_IDS" | sort -u)

    # 构建子卷映射表
    for subvol in "${ALL_SUBVOLS[@]}"; do
        subvol_path="${SUBVOL_DIR}/${subvol}"
        if [ -d "$subvol_path" ]; then
            change_time=$(stat -c "%y" "$subvol_path" 2>/dev/null | cut -d' ' -f1-2 | sed 's/\..*//')
            
            # 兼容Synology的时间戳转换
            if date --version 2>&1 | grep -q GNU; then
                time_stamp=$(date -d "$change_time" +%s 2>/dev/null)
            else
                # BusyBox date
                time_stamp=$(date -j -f "%Y-%m-%d %H:%M:%S" "$change_time" "+%s" 2>/dev/null)
            fi
            
            clean_id=${subvol%-init}
            short_id=${clean_id:0:12}
            size=$(sudo du -s "$subvol_path" 2>/dev/null | awk '{print $1}')
            subvol_map[$subvol]="$change_time|$time_stamp|$clean_id|$short_id|$size"
        fi
    done

    # 分类子卷
    for subvol in "${!subvol_map[@]}"; do
        IFS='|' read -r change_time time_stamp clean_id short_id size <<< "${subvol_map[$subvol]}"
        
        if ! echo "$ALL_USED_IDS" | grep -q -E "^($clean_id|$short_id)$"; then
            ORPHAN_SUBVOLS+=("$subvol")
        fi
    done
}

# 显示菜单（带超时）
show_menu() {
    echo -e "\n${YELLOW}=== Docker Btrfs 子卷管理工具 ===${NC}"
    echo -e "${BLUE}⚠️  ${TIMEOUT_SECONDS}秒内无操作将自动清理并退出${NC}"
    echo "1. 扫描子卷状态"
    echo "2. 列出所有孤儿子卷"
    echo "3. 删除超过60天的孤儿子卷"
    echo "4. 删除超过30天的孤儿子卷"
    echo "5. 手动选择删除子卷"
    echo "6. 退出"
    echo
    
    # 设置超时
    timeout=$TIMEOUT_SECONDS
    while [ $timeout -gt 0 ]; do
        echo -ne "${YELLOW}请选择操作 [1-6] (剩余${timeout}秒): ${NC}\r"
        
        # 使用read的超时选项
        if read -t 1 -n 1 -r choice; then
            echo
            # 清除超时倒计时
            echo -ne "\033[K"
            break
        fi
        
        timeout=$((timeout - 1))
        
        # 超时处理
        if [ $timeout -eq 0 ]; then
            echo
            timeout_handler
        fi
    done
}

# 扫描子卷
scan_subvolumes() {
    echo -e "${YELLOW}=== Docker Btrfs 子卷状态分析 ===${NC}\n"

    # 获取所有子卷ID和路径
    echo -e "${YELLOW}正在扫描所有子卷...${NC}"
    ALL_SUBVOLS=($(sudo btrfs subvolume list /volume1 | grep '@docker/btrfs/subvolumes/' | grep -oP '(?<=subvolumes/)[0-9a-f]+(-init)?' | sort -u))

    echo -e "${YELLOW}找到 ${#ALL_SUBVOLS[@]} 个子卷${NC}"
    echo -e "${YELLOW}正在获取Docker使用的ID...${NC}"

    # 获取Docker容器和镜像的ID
    USED_IDS=$( (docker ps -aq; docker images -aq) | xargs -I {} docker inspect -f '{{.Id}} {{.Id | substr 0 12}}' {} 2>/dev/null | tr ' ' '\n' | sort -u )
    GRAPH_IDS=$( (docker ps -aq; docker images -aq) | xargs -I {} docker inspect -f '{{.GraphDriver.Data}}' {} 2>/dev/null | grep -oP '[0-9a-f]{64}|[0-9a-f]{12}' | sort -u )
    ALL_USED_IDS=$(echo -e "$USED_IDS\n$GRAPH_IDS" | sort -u)

    # 构建子卷映射表
    echo -e "${YELLOW}正在收集子卷信息...${NC}"
    for subvol in "${ALL_SUBVOLS[@]}"; do
        subvol_path="${SUBVOL_DIR}/${subvol}"
        if [ -d "$subvol_path" ]; then
            change_time=$(stat -c "%y" "$subvol_path" 2>/dev/null | cut -d' ' -f1-2 | sed 's/\..*//')
            
            # 兼容Synology的时间戳转换
            if date --version 2>&1 | grep -q GNU; then
                time_stamp=$(date -d "$change_time" +%s 2>/dev/null)
            else
                time_stamp=$(date -j -f "%Y-%m-%d %H:%M:%S" "$change_time" "+%s" 2>/dev/null)
            fi
            
            clean_id=${subvol%-init}
            short_id=${clean_id:0:12}
            size=$(sudo du -s "$subvol_path" 2>/dev/null | awk '{print $1}')
            subvol_map[$subvol]="$change_time|$time_stamp|$clean_id|$short_id|$size"
        fi
    done

    # 分类子卷
    echo -e "\n${GREEN}=== 正在使用的子卷 ===${NC}"
    found_used=0
    for subvol in "${!subvol_map[@]}"; do
        IFS='|' read -r change_time time_stamp clean_id short_id size <<< "${subvol_map[$subvol]}"
        
        if echo "$ALL_USED_IDS" | grep -q -E "^($clean_id|$short_id)$"; then
            USED_SUBVOLS+=("$subvol")
            size_human=$(numfmt --to=iec --suffix=B $((size * 1024)) 2>/dev/null || echo "$((size / 1024)) MB")
            echo -e "${GREEN}✓${NC} $subvol\t${YELLOW}$change_time${NC}\t$size_human"
            found_used=1
        fi
    done

    if [ $found_used -eq 0 ]; then
        echo -e "${YELLOW}⚠️  未检测到被Docker使用的子卷（Synology特性）${NC}"
        echo -e "${YELLOW}   最近创建的子卷（按时间排序）：${NC}"
        for subvol in "${!subvol_map[@]}"; do
            IFS='|' read -r change_time time_stamp clean_id short_id size <<< "${subvol_map[$subvol]}"
            echo "$time_stamp|$change_time|$subvol"
        done | sort -nr | head -5 | while read -r line; do
            timestamp=$(echo "$line" | cut -d'|' -f1)
            change_time=$(echo "$line" | cut -d'|' -f2)
            name_part=$(echo "$line" | cut -d'|' -f3-)
            echo -e "${BLUE}*${NC} $name_part\t${YELLOW}$change_time${NC}"
        done
    fi

    # 收集孤儿子卷
    echo -e "\n${RED}=== 孤立子卷（可删除）===${NC}"
    ORPHAN_SUBVOLS=()
    OLD_SUBVOLS=()
    current_timestamp=$(date +%s)
    
    for subvol in "${!subvol_map[@]}"; do
        IFS='|' read -r change_time time_stamp clean_id short_id size <<< "${subvol_map[$subvol]}"
        
        if ! echo "$ALL_USED_IDS" | grep -q -E "^($clean_id|$short_id)$"; then
            ORPHAN_SUBVOLS+=("$subvol")
            size_human=$(numfmt --to=iec --suffix=B $((size * 1024)) 2>/dev/null || echo "$((size / 1024)) MB")
            
            if [ -n "$time_stamp" ] && [ "$time_stamp" -lt "$THIRTY_DAYS_AGO" ]; then
                OLD_SUBVOLS+=("$subvol")
                echo -e "${RED}✗${NC} $subvol\t${YELLOW}$change_time${NC}\t$size_human\t${RED}[可安全删除]${NC}"
            else
                echo -e "${RED}?${NC} $subvol\t${YELLOW}$change_time${NC}\t$size_human\t${YELLOW}[较新，建议保留]${NC}"
            fi
        fi
    done

    if [ ${#ORPHAN_SUBVOLS[@]} -eq 0 ]; then
        echo -e "${YELLOW}ℹ️  未发现孤立子卷${NC}"
    fi

    # 统计信息
    echo -e "\n${YELLOW}=== 统计信息 ===${NC}"
    echo -e "总子卷数: ${#ALL_SUBVOLS[@]}"
    echo -e "正在使用: ${#USED_SUBVOLS[@]} ${GREEN}(安全保留)${NC}"
    echo -e "孤立子卷: ${#ORPHAN_SUBVOLS[@]} ${RED}(可清理)${NC}"
    echo -e "超过30天: ${#OLD_SUBVOLS[@]} ${RED}(推荐删除)${NC}"

    # 计算空间
    if [ ${#ORPHAN_SUBVOLS[@]} -gt 0 ]; then
        total_size=0
        old_size=0
        
        for subvol in "${ORPHAN_SUBVOLS[@]}"; do
            IFS='|' read -r change_time time_stamp clean_id short_id size <<< "${subvol_map[$subvol]}"
            total_size=$((total_size + size))
            
            if [[ " ${OLD_SUBVOLS[@]} " =~ " $subvol " ]]; then
                old_size=$((old_size + size))
            fi
        done
        
        total_size_human=$(numfmt --to=iec --suffix=B $((total_size * 1024)) 2>/dev/null || echo "$((total_size / 1024)) MB")
        old_size_human=$(numfmt --to=iec --suffix=B $((old_size * 1024)) 2>/dev/null || echo "$((old_size / 1024)) MB")
        
        echo -e "孤立子卷总大小: $total_size_human"
        echo -e "可安全释放空间: $old_size_human"
    fi
}

# 列出所有孤儿子卷
list_orphans() {
    if [ ${#ORPHAN_SUBVOLS[@]} -eq 0 ] && [ ${#ALL_SUBVOLS[@]} -eq 0 ]; then
        scan_subvolumes
    fi
    
    echo -e "\n${RED}=== 所有孤立子卷列表 ===${NC}"
    index=1
    for subvol in "${ORPHAN_SUBVOLS[@]}"; do
        IFS='|' read -r change_time time_stamp clean_id short_id size <<< "${subvol_map[$subvol]}"
        size_human=$(numfmt --to=iec --suffix=B $((size * 1024)) 2>/dev/null || echo "$((size / 1024)) MB")
        
        if [ -n "$time_stamp" ] && [ "$time_stamp" -lt "$THIRTY_DAYS_AGO" ]; then
            echo -e "$index. ${RED}✗${NC} $subvol\t${YELLOW}$change_time${NC}\t$size_human\t${RED}[可安全删除]${NC}"
        else
            echo -e "$index. ${RED}?${NC} $subvol\t${YELLOW}$change_time${NC}\t$size_human\t${YELLOW}[较新]${NC}"
        fi
        index=$((index + 1))
    done
}

# 删除超过指定天数的子卷（交互式）
delete_old_subvols() {
    local days=$1
    local threshold=$2
    local count=0
    local freed_size=0

    echo -e "\n${RED}=== 删除超过${days}天的孤儿子卷 ===${NC}"
    
    # 收集要删除的子卷
    TO_DELETE=()
    for subvol in "${ORPHAN_SUBVOLS[@]}"; do
        IFS='|' read -r change_time time_stamp clean_id short_id size <<< "${subvol_map[$subvol]}"
        
        if [ -n "$time_stamp" ] && [ "$time_stamp" -lt "$threshold" ]; then
            TO_DELETE+=("$subvol")
        fi
    done

    if [ ${#TO_DELETE[@]} -eq 0 ]; then
        echo -e "${YELLOW}ℹ️  没有超过${days}天的孤儿子卷需要删除${NC}"
        return
    fi

    echo -e "\n将删除以下 ${#TO_DELETE[@]} 个子卷："
    for subvol in "${TO_DELETE[@]}"; do
        IFS='|' read -r change_time time_stamp clean_id short_id size <<< "${subvol_map[$subvol]}"
        size_human=$(numfmt --to=iec --suffix=B $((size * 1024)) 2>/dev/null || echo "$((size / 1024)) MB")
        echo -e "${RED}✗${NC} $subvol\t${YELLOW}$change_time${NC}\t$size_human"
    done

    read -p $'\n⚠️  确定要删除这些子卷吗？这将永久删除数据！(y/N) ' -n 1 -r
    echo
    
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo -e "\n开始删除..."
        for subvol in "${TO_DELETE[@]}"; do
            subvol_path="${SUBVOL_DIR}/${subvol}"
            echo -n "删除 $subvol..."
            
            if sudo btrfs subvolume delete "$subvol_path" >/dev/null 2>&1; then
                echo -e " ${GREEN}成功${NC}"
                count=$((count + 1))
                IFS='|' read -r change_time time_stamp clean_id short_id size <<< "${subvol_map[$subvol]}"
                freed_size=$((freed_size + size))
            else
                echo -e " ${RED}失败${NC}"
            fi
        done
        
        freed_size_human=$(numfmt --to=iec --suffix=B $((freed_size * 1024)) 2>/dev/null || echo "$((freed_size / 1024)) MB")
        echo -e "\n✅ 删除完成！成功删除 $count 个子卷，释放空间: $freed_size_human"
    else
        echo -e "\n${YELLOW}取消删除操作${NC}"
    fi
}

# 自动删除超过指定天数的子卷（无交互）
auto_delete_old_subvols() {
    local days=$1
    local threshold=$2
    local count=0
    local freed_size=0

    # 收集要删除的子卷
    TO_DELETE=()
    for subvol in "${ORPHAN_SUBVOLS[@]}"; do
        IFS='|' read -r change_time time_stamp clean_id short_id size <<< "${subvol_map[$subvol]}"
        
        if [ -n "$time_stamp" ] && [ "$time_stamp" -lt "$threshold" ]; then
            TO_DELETE+=("$subvol")
        fi
    done

    if [ ${#TO_DELETE[@]} -eq 0 ]; then
        echo -e "${YELLOW}ℹ️  没有超过${days}天的孤儿子卷需要删除${NC}"
        return
    fi

    echo -e "将自动删除以下 ${#TO_DELETE[@]} 个子卷："
    for subvol in "${TO_DELETE[@]}"; do
        IFS='|' read -r change_time time_stamp clean_id short_id size <<< "${subvol_map[$subvol]}"
        size_human=$(numfmt --to=iec --suffix=B $((size * 1024)) 2>/dev/null || echo "$((size / 1024)) MB")
        echo -e "${RED}✗${NC} $subvol\t${YELLOW}$change_time${NC}\t$size_human"
    done

    echo -e "\n开始自动删除..."
    for subvol in "${TO_DELETE[@]}"; do
        subvol_path="${SUBVOL_DIR}/${subvol}"
        echo -n "删除 $subvol..."
        
        if sudo btrfs subvolume delete "$subvol_path" >/dev/null 2>&1; then
            echo -e " ${GREEN}成功${NC}"
            count=$((count + 1))
            IFS='|' read -r change_time time_stamp clean_id short_id size <<< "${subvol_map[$subvol]}"
            freed_size=$((freed_size + size))
        else
            echo -e " ${RED}失败${NC}"
        fi
    done
    
    freed_size_human=$(numfmt --to=iec --suffix=B $((freed_size * 1024)) 2>/dev/null || echo "$((freed_size / 1024)) MB")
    echo -e "\n✅ 自动清理完成！成功删除 $count 个子卷，释放空间: $freed_size_human"
}

# 手动选择删除子卷
manual_delete() {
    if [ ${#ORPHAN_SUBVOLS[@]} -eq 0 ] && [ ${#ALL_SUBVOLS[@]} -eq 0 ]; then
        scan_subvolumes
    fi
    
    list_orphans
    
    if [ ${#ORPHAN_SUBVOLS[@]} -eq 0 ]; then
        return
    fi
    
    echo
    read -p "请输入要删除的子卷序号（多个序号用空格分隔，如: 1 3 5）: " selections
    
    TO_DELETE=()
    for selection in $selections; do
        if [[ "$selection" =~ ^[0-9]+$ ]] && [ "$selection" -ge 1 ] && [ "$selection" -le "${#ORPHAN_SUBVOLS[@]}" ]; then
            index=$((selection - 1))
            TO_DELETE+=("${ORPHAN_SUBVOLS[$index]}")
        fi
    done

    if [ ${#TO_DELETE[@]} -eq 0 ]; then
        echo -e "${YELLOW}无效的选择${NC}"
        return
    fi

    echo -e "\n将删除以下子卷："
    for subvol in "${TO_DELETE[@]}"; do
        IFS='|' read -r change_time time_stamp clean_id short_id size <<< "${subvol_map[$subvol]}"
        size_human=$(numfmt --to=iec --suffix=B $((size * 1024)) 2>/dev/null || echo "$((size / 1024)) MB")
        echo -e "${RED}✗${NC} $subvol\t${YELLOW}$change_time${NC}\t$size_human"
    done

    read -p $'\n⚠️  确定要删除这些子卷吗？这将永久删除数据！(y/N) ' -n 1 -r
    echo
    
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo -e "\n开始删除..."
        count=0
        freed_size=0
        
        for subvol in "${TO_DELETE[@]}"; do
            subvol_path="${SUBVOL_DIR}/${subvol}"
            echo -n "删除 $subvol..."
            
            if sudo btrfs subvolume delete "$subvol_path" >/dev/null 2>&1; then
                echo -e " ${GREEN}成功${NC}"
                count=$((count + 1))
                IFS='|' read -r change_time time_stamp clean_id short_id size <<< "${subvol_map[$subvol]}"
                freed_size=$((freed_size + size))
            else
                echo -e " ${RED}失败${NC}"
            fi
        done
        
        freed_size_human=$(numfmt --to=iec --suffix=B $((freed_size * 1024)) 2>/dev/null || echo "$((freed_size / 1024)) MB")
        echo -e "\n✅ 删除完成！成功删除 $count 个子卷，释放空间: $freed_size_human"
    else
        echo -e "\n${YELLOW}取消删除操作${NC}"
    fi
}

# 主程序
echo -e "${YELLOW}=== Docker Btrfs 子卷管理工具（自动清理模式）===${NC}"
echo -e "${BLUE}脚本将在${TIMEOUT_SECONDS}秒无操作后自动清理超过60天的子卷${NC}"

# 初始扫描
scan_subvolumes_silent

while true; do
    show_menu
    
    case $choice in
        1)
            scan_subvolumes
            ;;
        2)
            list_orphans
            ;;
        3)
            if [ ${#ORPHAN_SUBVOLS[@]} -eq 0 ] && [ ${#ALL_SUBVOLS[@]} -eq 0 ]; then
                scan_subvolumes
            fi
            delete_old_subvols 60 "$SIXTY_DAYS_AGO"
            ;;
        4)
            if [ ${#ORPHAN_SUBVOLS[@]} -eq 0 ] && [ ${#ALL_SUBVOLS[@]} -eq 0 ]; then
                scan_subvolumes
            fi
            delete_old_subvols 30 "$THIRTY_DAYS_AGO"
            ;;
        5)
            manual_delete
            ;;
        6)
            echo -e "\n${YELLOW}退出程序${NC}"
            exit 0
            ;;
        *)
            echo -e "\n${RED}无效的选择，请重新输入${NC}"
            ;;
    esac
    
    echo -e "\n${BLUE}按Enter键继续...${NC}"
    read -r
done
