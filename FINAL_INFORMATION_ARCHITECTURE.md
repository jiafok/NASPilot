# NASPilot Final Information Architecture (V1)

版本：1.0.0
批准日期：2026-07-30
状态：强制执行

## 概述

本文档定义 NASPilot V1 最终信息架构，包含 8 个一级模块的职责边界、页面归属、跨域规则。本文档是导航设计、页面开发、路由配置的唯一权威来源。

## 模块一：Dashboard

### 定位
运维中心（Operations Center），不是菜单页。

### 职责
- 跨域状态聚合（Task / Integration / Container / File）
- 异常与风险优先展示
- 最近事件时间线
- 失败任务/异常容器/待处理动作摘要
- 系统资源实时概览（CPU/MEM/DISK）

### 页面
- `/` Dashboard

### 数据源
- Task 域：最近执行状态、失败数、成功率
- Integration 域：各工具最近运行状态
- Container 域：运行/停止/异常容器数
- File 域：存储使用率、最近变更数

### 边界
- 不承载具体配置
- 不承载执行操作
- 所有操作入口指向对应域页面

---

## 模块二：任务中心（Task Center）

### 定位
用户自定义任务的全生命周期管理。

### 职责
- 创建/编辑/删除用户任务
- 支持任务类型：Shell 脚本、Python 脚本、Bash 脚本、自定义命令
- 定时调度（Cron）
- 手动立即执行
- 执行历史与日志
- 任务状态监控

### 页面
- `/automation` TaskList（任务列表）
- 任务详情/执行历史（内联或独立路由）

### 特点
- **用户创建、用户维护、用户运行**
- **不承载产品功能**
- 产品功能归属"集成工具"

### 数据模型
- Task（任务定义）
- TaskExecution（执行记录）

### 边界
- 不包含 PT RSS / Alist Upload 等产品内置能力
- 不包含 Docker 容器操作
- 不包含文件管理操作

---

## 模块三：集成工具（Integration Tools）

### 定位
NASPilot 官方内置产品能力的统一入口。

### 职责
- 展示所有内置工具及其运行状态
- 每个工具的独立配置页
- 运行历史与执行日志
- 手动立即执行
- 工具级别的调度配置

### 内置工具清单（V1）

| 工具 | Slug | 分类 | 说明 |
|------|------|------|------|
| PT RSS 自动下载 | pt_rss | pt | RSS 订阅、qBittorrent 集成、Free 检测 |
| AList 自动上传 | alist_upload | storage | 本地扫描、规则匹配、自动上传 |
| Cloudflare DDNS | cloudflare_ddns | network | IPv4/IPv6 DDNS 更新、Zone 管理 |
| Cloudflare Pages Deploy | cloudflare_pages | network | 首页面板生成与部署 |
| Docker App Backup | docker_backup | system | Docker 应用配置备份 |
| 日志清理 | log_cleanup | system | 过期日志删除、超大日志截断 |
| Btrfs Subvolume Cleanup | btrfs_cleanup | system | 孤儿 Btrfs 子卷清理 |
| Rclone Mount | rclone_mount | storage | Alist 远程 FUSE 挂载 |

### 页面
- `/applications` 集成工具列表（状态总览）
- `/applications/pt-rss` PT RSS 配置与执行
- `/applications/alist-upload` AList Upload 配置与执行
- `/applications/docker-backup` Docker Backup 配置与执行
- `/applications/cloudflare-ddns` Cloudflare DDNS 配置与执行
- `/applications/cloudflare-pages` Cloudflare Pages 配置与执行
- `/applications/log-cleanup` 日志清理配置与执行

### 特点
- **产品功能，官方维护**
- **独立配置**
- **运行状态、运行历史、执行日志、立即执行**

### 边界
- 不包含用户自定义任务（归属任务中心）
- 不包含 Docker 容器操作（归属容器管理）
- 插件管理操作归属系统设置 > Integrations

---

## 模块四：容器管理（Container Management）

### 定位
Docker 生态全生命周期管理。

### 职责
- 容器列表与状态（运行/停止/异常）
- 容器资源统计（CPU/MEM/NET/IO）
- 容器生命周期操作（启动/停止/重启/删除）
- 容器日志查看
- 容器内命令执行
- 自动发现 Docker Compose / Stack

### 页面
- `/containers` ContainerManager

### 自动发现范围
Docker 管理的所有容器，例如：
- DeepTutor
- Open WebUI
- qBittorrent
- Jellyfin
- Portainer
- FileBrowser
- 其他 docker-compose 管理的容器

### 特点
- **容器应用不属于集成工具**
- **容器应用不属于任务中心**
- 容器是独立的 Docker 生态管理域

### 边界
- 不包含非 Docker 进程管理
- 不包含容器内应用配置（应用自身配置走其自有界面）

---

## 模块五：File Manager

### 定位
NAS 文件系统的浏览与管理。

### 职责
- 安全根目录文件浏览
- 文本文件在线查看
- 二进制文件下载
- 文件/目录基本信息展示

### 页面
- `/files` FileBrowser

### 特点
- **File 是核心业务域（Principle 4）**
- 不降级为附属工具入口

### 边界
- 不包含文件上传任务（归属集成工具 - AList Upload）
- 不包含日志文件查看（归属日志中心）

---

## 模块六：日志中心（Log Center）

### 定位
全平台结构化日志查询与分析。

### 职责
- 结构化日志查询（级别/来源/关键词过滤）
- 原始日志查看
- 自动刷新
- 多来源日志聚合（system / scheduler / plugin:* / task:*）

### 页面
- `/logs` LogCenter
- `/logs/full` LogFullPage（独立全屏路由，无需认证侧栏）

### 边界
- 不包含容器日志（归属容器管理）
- 不包含文件浏览（归属 File Manager）

---

## 模块七：AI 助手（AI Assistant）

### 定位
AI 辅助运维诊断与对话。

### 职责
- 自然语言运维问答
- 日志分析与故障诊断辅助
- 平台操作指引

### 页面
- `/ai` AIAssistant

### 特点
- **能力增强层，不替代核心业务域**
- 服务于 Task / Integration / Container / File 域

### 边界
- 不具有直接操作权限（需用户确认）
- 不存储独立业务数据

---

## 模块八：系统设置（System Settings）

### 定位
全局系统配置与插件生命周期管理。

### 职责
- 系统参数配置
- 通知渠道管理
- **插件管理（Settings > Integrations）**
  - 启用/禁用插件
  - 安装/升级/删除插件
  - 插件实例配置

### 页面
- `/settings` SystemSettings
- `/settings/integrations` 插件管理（待实现）

### 特点
- **不再保留独立 Plugin Center**
- 插件管理收纳至系统设置作为子页面

### 边界
- 不包含工具的运行/执行操作（归属集成工具）
- 不包含任务调度配置（归属任务中心）

---

## 跨域规则

### 规则 1：单一入口
- 每个能力只有一个一级导航入口
- 跨域引用使用跳转链接，不创建平行入口

### 规则 2：域归属优先
- 新增页面必须先声明归属哪个模块
- 不允许"孤立工具页"

### 规则 3：导航稳定性
- 一级导航增删必须经过架构评审（ARB）
- 标签变更必须经过 PO 批准

### 规则 4：工具归属
- 用户创建的能力 → 任务中心
- 产品内置的能力 → 集成工具
- Docker 管理 → 容器管理
- 文件操作 → File Manager
- 日志查询 → 日志中心
- 系统配置 → 系统设置
- AI 辅助 → AI 助手

---

## 附录：页面-模块映射总表

| 路由 | 模块 | 页面组件 |
|------|------|---------|
| / | Dashboard | Dashboard |
| /automation | 任务中心 | TaskList |
| /applications | 集成工具 | 工具列表（从 PluginList 迁移） |
| /applications/pt-rss | 集成工具 | PT_RSS |
| /applications/alist-upload | 集成工具 | AlistUpload |
| /applications/docker-backup | 集成工具 | DockerBackup |
| /applications/cloudflare-ddns | 集成工具 | CloudflareDDNSPage |
| /applications/cloudflare-pages | 集成工具 | CloudflarePages |
| /applications/log-cleanup | 集成工具 | LogCleanup |
| /containers | 容器管理 | ContainerManager |
| /files | File Manager | FileBrowser |
| /logs | 日志中心 | LogCenter |
| /logs/full | 日志中心 | LogFullPage |
| /ai | AI 助手 | AIAssistant |
| /settings | 系统设置 | SystemSettings |
| /notifications | 系统设置 | NotificationCenter |

---

## 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.0.0 | 2026-07-30 | 初始发布：8 模块 IA，移除 Plugin Center，重定义 Task/Integration/Container |
