# NAVIGATION_AUDIT_REPORT

## 审计日期
2026-07-30

## 审计范围
- frontend/src/layouts/MainLayout.tsx（主导航菜单）
- frontend/src/App.tsx（路由映射）
- frontend/src/pages/*（页面实现）

## 1. 当前导航结构

| 序号 | 一级导航标签 | key | 路由 | 渲染组件 | 分类 |
|------|-------------|-----|-------|---------|------|
| 1 | 仪表盘 (Dashboard) | / | / | Dashboard | 聚合视图 |
| 2 | Automation Center | /automation | /automation | TaskList | 任务 |
| 3 | Application Center | /applications | /applications | PluginList | 插件 |
| 4 | 容器管理 | /containers | /containers | ContainerManager | 容器 |
| 5 | File Manager | /files | /files | FileBrowser | 文件 |
| 6 | 日志中心 | /logs | /logs | LogCenter | 日志 |
| 7 | 系统设置 | /settings | /settings | SystemSettings | 系统 |
| 8 | AI 助手 | /ai | /ai | AIAssistant | AI |

### 兼容性路由（仅跳转，无独立入口）

| 路由 | 跳转目标 | 说明 |
|------|---------|------|
| /tasks | → /automation | 旧任务路径兼容 |
| /plugins | → /applications | 旧插件路径兼容 |
| /notifications | → selectedKey=/settings | 通知收纳至设置域 |
| /tools/pt-rss | 直渲染 PT_RSS | 旧工具路径兼容 |
| /tools/alist | 直渲染 AlistUpload | 旧工具路径兼容 |
| /tools/cloudflare | 直渲染 CloudflarePages | 旧工具路径兼容 |
| /tools/cloudflare-ddns | 直渲染 CloudflareDDNSPage | 旧工具路径兼容 |
| /tools/docker-backup | 直渲染 DockerBackup | 旧工具路径兼容 |
| /tools/containers | 直渲染 ContainerManager | 重复入口 |
| /tools/log-cleanup | 直渲染 LogCleanup | 旧工具路径兼容 |
| /tools/file-browser | 直渲染 FileBrowser | 重复入口 |

### 应用别名路由

| 路由 | 渲染组件 |
|------|---------|
| /applications/pt-rss | PT_RSS |
| /applications/alist-upload | AlistUpload |
| /applications/docker-backup | DockerBackup |
| /applications/cloudflare-ddns | CloudflareDDNSPage |
| /applications/cloudflare-pages | CloudflarePages |
| /applications/log-cleanup | LogCleanup |

## 2. 发现的问题

### 问题 1：命名与职责不匹配（严重）

**Automation Center → TaskList**
- 标签 "Automation Center" 暗示自动化能力，但实际渲染的是 TaskList（用户自定义任务）。
- 任务中心（Task Center）的职责是用户创建/管理 shell/python/bash 脚本与定时任务。
- 当前标签误导用户预期。

**Application Center → PluginList**
- 标签 "Application Center" 暗示应用管理，但实际渲染的是 PluginList（插件列表）。
- 根据最新架构决策，此处应为"集成工具"（Integration Tools），展示 NASPilot 内置产品能力（PT RSS、Alist Upload、Cloudflare DDNS 等）。

### 问题 2：插件中心与集成工具职责重叠（严重）

- PluginList 组件既作为 /applications 的主内容，又作为插件总列表。
- 架构决议明确：删除独立 Plugin Center。
- 插件管理（启用/禁用/安装/升级/删除）应收纳至 Settings > Integrations。
- /applications 应展示集成工具的"运行状态、配置入口、执行历史"，而非插件元数据列表。

### 问题 3：双重入口（中等）

| 能力 | 主入口 | 重复入口 |
|------|--------|---------|
| 容器管理 | /containers | /tools/containers |
| 文件浏览 | /files | /tools/file-browser |
| Cloudflare Pages | /tools/cloudflare | /applications/cloudflare-pages |
| Cloudflare DDNS | /tools/cloudflare-ddns | /applications/cloudflare-ddns |
| PT RSS | /tools/pt-rss | /applications/pt-rss |
| AList Upload | /tools/alist | /applications/alist-upload |
| Docker Backup | /tools/docker-backup | /applications/docker-backup |
| Log Cleanup | /tools/log-cleanup | /applications/log-cleanup |

- /tools/* 与 /applications/* 形成完整的平行入口，违反 Principle 7（No Duplicate Entry）。

### 问题 4：PluginList 职责模糊（中等）

- PluginList 当前既是"应用列表"又是"插件管理"。
- 根据新架构：
  - 集成工具页（/applications）：展示产品能力、运行状态、配置、执行
  - 插件管理（Settings > Integrations）：启用/禁用/安装/升级/删除

### 问题 5：selectedKey 映射不精确（轻微）

```ts
if (p.startsWith('/settings') || p.startsWith('/notifications') || p.startsWith('/plugins')) return '/settings';
```
- /plugins 已重定向到 /applications，但 selectedKey 仍映射到 /settings。
- 这导致用户点击旧书签 /plugins 时，高亮显示"系统设置"而非实际渲染页面的归属。

## 3. 目标导航结构（强制）

根据最新架构决议，目标一级导航为：

| 序号 | 一级导航 | key | 职责 |
|------|---------|-----|------|
| 1 | Dashboard | / | 跨域运维总览 |
| 2 | 任务中心 | /automation | 用户自定义任务（Shell/Python/Bash/Cron） |
| 3 | 集成工具 | /applications | NASPilot 内置产品能力（8 个官方工具） |
| 4 | 容器管理 | /containers | Docker 生态管理 |
| 5 | File Manager | /files | 文件浏览与管理 |
| 6 | 日志中心 | /logs | 结构化/原始日志查询 |
| 7 | AI 助手 | /ai | AI 辅助诊断与对话 |
| 8 | 系统设置 | /settings | 全局设置 + 插件管理（Settings > Integrations） |

## 4. 需要执行的修正清单

### 4.1 导航标签修正（前端代码，不在本次范围，仅记录）

- "Automation Center" → "任务中心"
- "Application Center" → "集成工具"

### 4.2 路由精简（前端代码，不在本次范围，仅记录）

- 删除 /tools/* 别名路由，统一使用 /applications/*（集成工具页作为承载）
- 删除 /tools/containers，仅保留 /containers
- 删除 /tools/file-browser，仅保留 /files

### 4.3 文档修正（本次执行）

- [x] 以下文档需移除 "Plugin Center" / "Application Center" / "Automation Center" 相关过时描述
- [x] 更新为：任务中心 / 集成工具 / 容器管理 / File Manager / 日志中心 / AI 助手 / 系统设置

### 4.4 新增文档

- FINAL_INFORMATION_ARCHITECTURE.md（定义 8 模块边界）
- ARCHITECTURE_CORRECTION_REPORT.md（冲突审计）

## 5. 审计结论

当前导航存在：
- 2 处严重命名/职责不匹配
- 1 处严重职责重叠（Plugin Center vs Integration Tools）
- 8 处双重入口
- 1 处 selectedKey 映射不精确

整体评级：**REWORK REQUIRED**

审计完成，进入 Task 2（文档修正）与 Task 3（最终 IA）。
