# ARCHITECTURE_CORRECTION_REPORT

日期：2026-07-30
阶段：Architecture Correction
结论：**PASS**（文档层面纠偏已完成）

## 1. 删除的模块

| 模块 | 原因 | 处置方式 |
|------|------|---------|
| Plugin Center（插件中心） | 与集成工具职责重叠 | 删除独立页面入口；插件管理收纳至系统设置 > Integrations |

## 2. 修正的模块

| 模块 | 旧定义 | 新定义 |
|------|--------|--------|
| Automation Center | 模糊的自动化入口 | **任务中心**：用户自定义任务（Shell/Python/Bash/Cron），用户创建/维护/运行，不承载产品功能 |
| Application Center | 模糊的应用入口 | **集成工具**：NASPilot 内置产品能力（PT RSS、Alist Upload、Cloudflare DDNS/Pages、Docker Backup、日志清理、Btrfs Cleanup、Rclone Mount），产品功能、独立配置、运行状态/历史/日志/立即执行 |
| 容器管理 | 未明确定义边界 | 明确 Docker 生态管理，容器应用（DeepTutor/qBittorrent/Jellyfin 等）属于容器域，不属于集成工具或任务中心 |
| 系统设置 | 未包含插件管理 | 新增 Settings > Integrations，承载插件生命周期管理（启用/禁用/安装/升级/删除） |

## 3. 修改的文档列表

| 文档 | 操作 | 说明 |
|------|------|------|
| NAVIGATION_AUDIT_REPORT.md | 新建 | 当前导航结构审计，发现 2 严重 + 1 严重 + 8 重复入口 + 1 映射不精确 |
| FINAL_INFORMATION_ARCHITECTURE.md | 新建 | 8 模块最终 IA，定义每个模块的职责边界、页面归属、跨域规则 |
| ARCHITECTURE_CORRECTION_REPORT.md | 新建 | 本文档 |
| docs/governance/product-owner.md | 修正 | 路线图轴更新：移除 "Application Center 建设"，改为 "任务中心 建设" + "集成工具 建设" |
| docs/governance/architect.md | 修正 | IA 定义更新为 8 模块，声明插件管理收纳至 Settings > Integrations，引用 FINAL_INFORMATION_ARCHITECTURE.md |
| docs/governance/developer.md | 无需修改 | 无 Plugin Center 引用 |
| docs/governance/reviewer.md | 无需修改 | 无 Plugin Center 引用 |
| docs/governance/naspilot-product-principles.md | 无需修改 | 无 Plugin Center 引用，8 条原则无需变更 |
| NASPILOT_INFORMATION_ARCHITECTURE.md | 归档 | 替换为归档声明，指向 FINAL_INFORMATION_ARCHITECTURE.md |
| DEVELOPMENT_WORKFLOW.md | 不存在 | 该文件未创建，无需修改（已记录） |

## 4. 职责重叠审计

### 4.1 Plugin Center vs Integration Tools

| 维度 | Plugin Center（旧） | Integration Tools（新） |
|------|-------------------|----------------------|
| 内容 | 插件元数据列表 | 产品能力状态 + 配置入口 + 运行历史 |
| 操作 | 启用/禁用/安装/删除 | 配置、运行、查看结果 |
| 用户场景 | 管理员管理插件 | 用户使用产品功能 |

**结论：职责重叠已消除。** 插件管理（CRUD）移入 Settings > Integrations，集成工具页专注产品能力使用。

### 4.2 Task Center vs Integration Tools

| 维度 | 任务中心 | 集成工具 |
|------|---------|---------|
| 创建者 | 用户 | 官方 |
| 内容 | Shell/Python/Bash/Cron | PT RSS/Alist/Cloudflare 等产品能力 |
| 维护者 | 用户 | NASPilot 官方 |
| 可配置性 | 完全自由 | 参数化配置 |

**结论：职责清晰，无重叠。**

### 4.3 Container vs Integration Tools

| 维度 | 容器管理 | 集成工具 |
|------|---------|---------|
| 管理对象 | Docker 容器/Stack | 产品内置能力 |
| 典型实体 | qBittorrent/Jellyfin/Portainer | PT RSS/Alist Upload |
| 操作 | start/stop/restart/logs/exec | 配置/运行/查看历史 |

**结论：职责清晰，无重叠。** 容器内的应用（如 qBittorrent）属于容器域，其运行状态由容器管理负责；PT RSS 等集成工具的配置与执行由集成工具域负责。

## 5. 页面重复审计

| 重复项 | 状态 | 处置 |
|--------|------|------|
| /tools/containers ↔ /containers | 重复 | 保留 /containers，标记 /tools/containers 待下线 |
| /tools/file-browser ↔ /files | 重复 | 保留 /files，标记 /tools/file-browser 待下线 |
| /tools/pt-rss ↔ /applications/pt-rss | 重复 | 保留 /applications/pt-rss，标记 /tools/* 别名待下线 |
| /tools/alist ↔ /applications/alist-upload | 重复 | 同上 |
| /tools/cloudflare ↔ /applications/cloudflare-pages | 重复 | 同上 |
| /tools/cloudflare-ddns ↔ /applications/cloudflare-ddns | 重复 | 同上 |
| /tools/docker-backup ↔ /applications/docker-backup | 重复 | 同上 |
| /tools/log-cleanup ↔ /applications/log-cleanup | 重复 | 同上 |

**统计：8 处双重入口待收敛。** 不在本次文档范围操作，记录为前端代码待办。

## 6. 导航冲突审计

| 冲突项 | 详情 | 状态 |
|--------|------|------|
| /plugins 跳转 + selectedKey 不一致 | /plugins → /applications，但菜单高亮 /settings | 待修正（前端代码） |
| /notifications 无独立入口但菜单仍可触发 | selectedKey 映射到 /settings | 当前可用，无需紧急修正 |
| Cloudflare DDNS vs Cloudflare Pages 共存 | 两个独立工具页，但共享 Cloudflare 品牌 | 已确认不同能力，不重叠 |

## 7. 模块归属错误审计

| 页面/能力 | 当前归属 | 应归属 | 说明 |
|-----------|---------|--------|------|
| PluginList | /applications 主内容 | 集成工具列表（改造后） | 内容从插件元数据变为产品能力状态 |
| NotificationCenter | 旧独立页面 | 系统设置子页 | /notifications 无独立一级入口，selectedKey → /settings |
| LogCleanup | 旧 tools 别名 | 集成工具 | 已通过 /applications/log-cleanup 归属 |
| FileBrowser | 旧 tools 别名 | File Manager | 已通过 /files 归属 |

**结论：无模块归属错误。** 当前路由已通过 Phase 1 修正了归属，剩余 /tools/* 别名属于兼容保留。

## 8. 架构审计结论

### 通过项
- [x] 8 模块职责边界清晰定义
- [x] Plugin Center 与 Integration Tools 重叠已消除
- [x] Task Center 与 Integration Tools 职责已分离
- [x] Container 与 Integration Tools 边界已明确
- [x] 插件管理收纳至 Settings 已声明
- [x] 无模块归属错误

### 待办项（不在本次范围，记录为后续 Phase）
- [ ] 前端导航标签修正：Automation Center → 任务中心，Application Center → 集成工具
- [ ] 前端路由收敛：删除 /tools/* 别名，统一为 /applications/*
- [ ] 前端 selectedKey 映射修正：/plugins 路径高亮修正
- [ ] 集成工具页内容改造：从 PluginList（插件元数据）改为 IntegrationToolsList（产品能力状态 + 配置入口）
- [ ] Settings > Integrations 子页面实现（插件 CRUD 管理）

## 9. 最终裁决

```
结论：PASS

本次 Architecture Correction 在文档层面完成了：
1. 导航结构审计
2. 职责重叠消除
3. 8 模块信息架构固化
4. 全部治理文档同步

未修改任何代码。
未新增任何页面。
未修改 Dashboard。

前端代码修正（导航标签/路由收敛/页面改造）属于后续 Phase 范围，
不在本次 Architecture Correction 执行。
```
