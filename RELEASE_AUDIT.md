# NASPilot Release Audit — RELEASE_AUDIT.md

审计日期：2026-08-01  
审计范围：全项目代码、治理文档、前端页面、后端 API、部署物  
审计方法：逐文件阅读 + 治理基线交叉验证 + 运行态问题汇总  
目标：判断当前版本是否满足 V1 发布条件

---

## 1. Product Audit

### Current State
NASPilot 产品定位为"All-in-One NAS Automation Platform"，核心使命 NAS Monitoring / Automation / Operations。治理体系已冻结，5 份治理文档（product-principles、product-owner、architect、developer、reviewer）和 FINAL_INFORMATION_ARCHITECTURE.md 构成基线。

产品当前交付：
- 8 个一级模块：Dashboard、Task Center、Integration Tools、Container Manager、File Manager、Log Center、AI Assistant、System Settings
- 8 个内置集成工具：PT RSS、AList Upload、Cloudflare DDNS、Cloudflare Pages、Docker Backup、Log Cleanup、Btrfs Cleanup、Rclone Mount
- 任务系统（Task CRUD + 执行历史 + Cron 调度）
- 容器管理（列表、日志、终端、批量操作）
- 文件浏览、结构化日志、通知系统、AI 助手
- Observability 层（统一执行结果模型 + 跨域总览 API）

### Risks
1. **产品范围过度扩张（Medium）**：当前已覆盖 8 个模块 + 8 个工具 + AI + Observability，但部分能力深度不足（如 Btrfs Cleanup 和 Rclone Mount 仅有后端插件，无前端页面）。
2. **用户画像偏移（Low）**：产品仍面向高技术水平用户（需理解 Docker、Cron、JSON 配置），普通 NAS 用户上手门槛较高。

### Findings
| # | 发现 | 严重程度 |
|---|------|---------|
| 1.1 | 治理基线完整且冻结，产品边界明确 | — (正向) |
| 1.2 | 8 条产品原则全覆盖，有逐条检查清单 | — (正向) |
| 1.3 | btrfs_cleanup 和 rclone_mount 两个工具无前端页面，在集成工具列表中不可见 | Medium |
| 1.4 | Cloudflare DDNS 页面无独立导航入口（仅通过 /applications/cloudflare-ddns 访问），在 MainLayout 集成工具子菜单下缺失 | Medium |
| 1.5 | 默认管理员密码 `admin123` 和 SECRET_KEY `change-me-in-production-please` 硬编码在 config.py 中 | High |
| 1.6 | 5 个根目录测试/工具脚本（check_backup.py、check_dups.py 等）均硬编码 admin123 凭据 | Low |

### Recommendation
- 为 btrfs_cleanup 和 rclone_mount 补齐基础前端页面（至少状态展示 + 运行入口）
- 将 Cloudflare DDNS 加入 MainLayout 集成工具子菜单
- 将 SECRET_KEY 默认值从 `change-me-in-production-please` 改为必须从环境变量注入（无默认值即崩溃）
- 将根目录散落脚本移入 `scripts/` 或删除

---

## 2. Navigation Audit

### Current State
导航结构已收敛至 8 个一级入口，集成工具作为 `/applications` 的子菜单（5 个子项可见），旧版 `/tools/*` 路由保留为 Navigate 重定向。MainLayout 的 selectedKey 映射覆盖了旧路径。

### Risks
1. **Cloudflare DDNS 入口缺失（Medium）**：MainLayout 子菜单只列出 5 个工具（PT RSS、AList Upload、Cloudflare Pages、Docker Backup、Log Cleanup），缺少 cloudflare-ddns。
2. **btrfs_cleanup 和 rclone_mount 无子菜单项（Low）**：两个已注册后端插件无前端入口。

### Findings
| # | 发现 | 严重程度 |
|---|------|---------|
| 2.1 | 8 个一级模块入口清晰，单一入口原则基本落实 | — (正向) |
| 2.2 | /tools/* 旧路由已全部转为重定向，Bookmark 兼容 | — (正向) |
| 2.3 | Cloudflare DDNS 在 App.tsx 有路由（/applications/cloudflare-ddns），但在 MainLayout 集成工具子菜单中缺失 | Medium |
| 2.4 | Application Center / 集成工具的命名已统一 | — (正向) |
| 2.5 | 通知中心收纳至 Settings 域，通过 selectedKey 映射处理，但无独立一级入口，也不在主菜单可见 | Low |

### Recommendation
- MainLayout 集成工具子菜单补齐 cloudflare-ddns 项
- 为 btrfs_cleanup 和 rclone_mount 添加子菜单入口（配合前端页面补齐）

---

## 3. Dashboard Audit

### Current State
Dashboard 已从旧版四卡片 + ResourceMonitor 重构为 8 模块：System Health、Container Health、Task Health、Storage Health、Recent Events、Failed Tasks、Warning Containers、Quick Actions。数据源为 `/api/v1/observability/overview` + `/api/v1/observability/executions/unified` + `/api/v1/system/stats` + `/api/v1/tasks/executions` + `/api/v1/system/docker/containers`。

### Risks
1. **Docker 不可用时状态仍可读（Medium）**：Dashboard 有 dockerUnavailable 降级提示，但 Container Health 卡片会显示"0 运行中"而非"N/A"，可能被误读。
2. **Recent Events 在 Docker 不可用时空（Low）**：容器域事件缺失使时间线不完整。

### Findings
| # | 发现 | 严重程度 |
|---|------|---------|
| 3.1 | Status First / Result First / Risk First 原则已落实 | — (正向) |
| 3.2 | Risk First 提示面板存在，可显示失败数、容器异常、磁盘预警 | — (正向) |
| 3.3 | 8 个 Product Principles 要求的 30 秒判断指标均有对应卡片 | — (正向) |
| 3.4 | Failed Tasks 列表可正确追溯至具体任务名和时间 | — (正向) |
| 3.5 | Quick Actions 提供任务中心/容器/日志清理/日志中心/刷新快捷入口 | — (正向) |
| 3.6 | Docker 不可用时，Container Health 显示 0 而非 N/A，可能与实际状态混淆 | Low |
| 3.7 | Storage Health 仅在磁盘 >= 85% 时告警，不展示 IO 吞吐趋势 | Low |

### Recommendation
- Docker 不可用时将 Container Health 的值显示为 "N/A" 而非 0
- 可考虑在 Storage Health 追加磁盘 IO 指标（已有 metrics API 数据可用）

---

## 4. Application Audit (Integration Tools)

### Current State
8 个后端插件均已注册。前端有 6 个独立工具页面（PT_RSS、AlistUpload、CloudflarePages、CloudflareDDNSPage、DockerBackup、LogCleanup），均使用 PluginConfigForm 共享组件。组件提供：运行状态、最后执行时间、最近执行结果、成功次数、失败次数、配置入口、运行入口、Execution Logs 入口、运行历史面板。

### Risks
1. **btrfs_cleanup 和 rclone_mount 无前端页面（High）**：用户无法通过 UI 触发或监控这两个工具。
2. **运行状态显示可能不准确（Medium）**：PluginConfigForm 的"运行状态"字段来自 run_history[0].status，如历史为空则回退为 `idle`，不反映真实调度或进程状态。

### Findings
| # | 发现 | 严重程度 |
|---|------|---------|
| 4.1 | 所有已有工具页均展示状态/最后执行/结果/成功/失败 5 项关键信息 | — (正向) |
| 4.2 | 运行历史面板始终可见，无数据时显示"暂无运行历史" | — (正向) |
| 4.3 | Execution Logs 按钮直达日志中心对应 source 过滤 | — (正向) |
| 4.4 | btrfs_cleanup 后端插件已实现，无对应前端页面 | High |
| 4.5 | rclone_mount 后端插件已实现，无对应前端页面 | High |
| 4.6 | 各工具页独立调用 `/plugins` + `/plugins/{id}/instances`，存在 N+1 请求 | Low |

### Recommendation
- 为 btrfs_cleanup 和 rclone_mount 补齐前端页面（复用 PluginConfigForm）
- 提升缓存粒度：将 5 个工具的 instances 请求合并为后端批量接口
- 在 run_history 为空时，增加从 scheduler 查询"是否已调度"的兜底逻辑

---

## 5. Observability Audit

### Current State
后端已实现统一 Observability 层：
- `GET /api/v1/observability/overview`：四域总览（Task/Application/Container/File）
- `GET /api/v1/observability/executions/unified`：统一执行结果流
- 统一结果模型：domain / status / counters / failure_reasons / evidence_refs
- 支持 24-168 小时时间窗口

前端 Dashboard 已接入 observability API。

### Risks
1. **Application 域数据依赖 run_history 字符串解析（Medium）**：`_normalize_app_status` 和 `_to_counters` 依赖 run_history 中 summary 字段可能为 dict 或 JSON 字符串两种形态，JSON 解析失败时静默降级。
2. **Container 域数据依赖 Docker API（Medium）**：Docker 不可用时容器域完全缺失。

### Findings
| # | 发现 | 严重程度 |
|---|------|---------|
| 5.1 | Observability 层架构设计完整，PO 视角文档对齐 | — (正向) |
| 5.2 | 统一执行结果模型覆盖 task/application/container/file 四域 | — (正向) |
| 5.3 | File 域仅从 alist_upload 投影，未涵盖 log_cleanup 等文件操作 | Low |
| 5.4 | 后端 observability API 无缓存，每次 Dashboard 刷新触发全量计算 | Low |
| 5.5 | 前端 Recent Events 表取前 30 条，无分页或时间筛选 | Low |

### Recommendation
- 扩展 File 域数据源，将 log_cleanup 的删除/截断结果纳入 File 域
- 为 observability API 添加 Redis/内存缓存（TTL 30s）
- 前端 Recent Events 增加时间范围筛选

---

## 6. Container Audit

### Current State
- `GET /api/v1/system/docker/containers`：容器列表（状态、镜像、端口、Stack 标签）
- `GET /api/v1/system/docker/stats`：容器资源统计（CPU/Memory/Network/IO/PIDs）
- 容器日志、交互式终端（xterm + WebSocket）、批量生命周期操作
- 前端 ContainerManager 提供搜索、过滤、进度条可视化

### Risks
1. **Docker 强依赖（Critical）**：所有容器功能依赖 Docker socket。Windows 开发环境下完全不可用，可能导致集成测试覆盖缺失。
2. **Stats 零值问题（Medium）**：Synology 等平台 Docker stats 单次采样可能返回零值。后端已做双采样兜底，但尚未在生产环境验证。

### Findings
| # | 发现 | 严重程度 |
|---|------|---------|
| 6.1 | 容器列表功能完整：列表、搜索、状态过滤、批量操作 | — (正向) |
| 6.2 | 交互式终端使用 xterm + WebSocket，体验接近原生 | — (正向) |
| 6.3 | Docker socket 在 docker-compose.yml 中以 rw 挂载，权限过大 | Medium |
| 6.4 | 容器资源统计 CPU 计算使用差值公式（cpu_delta/sys_delta × cpu_count），在 precpu_stats 为空的场景返回 0 | Medium |
| 6.5 | 无 Docker Compose 级别操作（compose up/down/pull），仅容器级 | Low |
| 6.6 | 无跨主机 Docker 管理 | Low |

### Recommendation
- 将 docker-compose.yml 中 Docker socket 挂载从 `rw` 降为 `ro`（除非确认交互终端需要写权限）
- 增加 stats 计算的单元测试（使用 mock Docker stats JSON 进行回归验证）
- 增加 Docker 连接失败的启动时预检日志（当前仅在 API 调用时返回 503）

---

## 7. File Audit

### Current State
- `GET /api/v1/files/list`：文件浏览（基于安全根目录配置）
- 文本文件在线查看、二进制文件下载
- 前端 FileBrowser 页面（/files）

### Risks
1. **功能深度不足（Medium）**：当前仅支持浏览+下载，无上传、删除、重命名、权限管理。
2. **安全根目录依赖配置（Medium）**：FILES_ROOT 配置错误可能导致越权访问或空列表。

### Findings
| # | 发现 | 严重程度 |
|---|------|---------|
| 7.1 | File 域已作为一级模块独立存在，符合 Principle 4 | — (正向) |
| 7.2 | 当前功能为基础浏览，不支持 CRUD 操作 | Medium |
| 7.3 | 无文件变更历史或审计日志 | Low |
| 7.4 | 与集成工具（AList Upload）的联动仅通过 Observability 层间接关联 | Low |

### Recommendation
- V1 发布可接受当前基础浏览能力，但需在发布说明中标注为 "Basic"
- 增加 FILES_ROOT 配置校验与启动预检

---

## 8. Security Audit

### Current State
- JWT 认证（HS256 + 24h 过期）
- bcrypt 密码哈希
- OAuth2PasswordBearer token 提取
- WebSocket 认证通过 query param token
- CORS 允许所有来源（`allow_origins=["*"]`）
- 所有 API 端点要求认证（除 `/api/v1/auth/login` 和 `/api/health`）

### Risks
1. **默认凭据硬编码（Critical）**：`SECRET_KEY` 默认为 `change-me-in-production-please`，`INITIAL_ADMIN_PASSWORD` 默认为 `admin123`。如果用户未通过环境变量覆盖，JWT 签名可被任何人伪造，管理员账户可被接管。
2. **CORS 全放开（High）**：`allow_origins=["*"]` 配合 `allow_credentials=True` 允许任意域携带凭据请求，存在 CSRF 风险。
3. **Docker socket rw 挂载（High）**：容器内可通过 Docker API 执行任意宿主机命令，等同于 root 权限。
4. **HS256 对称密钥（Medium）**：SECRET_KEY 泄露后所有已签发 token 可被伪造。生产环境应支持 RS256 或环境变量强制注入。
5. **无速率限制（Medium）**：登录端点无 bruteforce 防护，可被暴力破解。

### Findings
| # | 发现 | 严重程度 |
|---|------|---------|
| 8.1 | 认证体系完整（JWT + bcrypt + 会话过期） | — (正向) |
| 8.2 | SECRET_KEY 硬编码默认值 | Critical |
| 8.3 | INITIAL_ADMIN_PASSWORD 硬编码默认值 | Critical |
| 8.4 | CORS allow_origins=["*"] + allow_credentials=True | High |
| 8.5 | Docker socket rw 挂载 | High |
| 8.6 | 登录端点无速率限制 | Medium |
| 8.7 | WebSocket token 通过 URL query param 传递，可能被日志/代理记录 | Medium |
| 8.8 | 根目录 5 个散落脚本硬编码 admin123 | Low |
| 8.9 | 无 HTTPS 强制（依赖反向代理） | Low |
| 8.10 | API 响应中未设置安全头（HSTS、CSP、X-Content-Type-Options） | Low |

### Recommendation
- **阻断项**：删除 `SECRET_KEY` 默认值，改为启动时检查环境变量，不存在则拒绝启动
- **阻断项**：删除 `INITIAL_ADMIN_PASSWORD` 默认值，同上
- CORS 改为仅允许配置列表 + 禁止 `allow_credentials=True` 与 `*` 同时使用
- Docker socket 挂载降为 `ro`（如交互终端不支持则文档说明风险）
- 登录端点增加速率限制（如 5 次/分钟/IP）
- 清理根目录散落脚本中的硬编码凭据

---

## 9. Release Readiness Assessment

### Current State
- 前端构建通过（`npm run build` 成功）
- 后端语法检查通过（`py_compile` 无错误）
- Dockerfile 三阶段构建（前端构建 → Python deps → 最终运行时）
- docker-compose.yml 就绪（含健康检查、日志轮转、网络隔离）
- 非致命启动错误：VACUUM 报错（每次启动出现，不阻断服务）
- Windows 开发环境无法运行 Docker 相关功能
- 无 CI/CD 流水线
- 无自动化测试套件
- 无版本号管理策略（硬编码 1.0.0）
- 无数据库迁移框架（依赖 `Base.metadata.create_all`）

### Findings
| # | 发现 | 严重程度 |
|---|------|---------|
| 9.1 | 前端构建稳定，无 TypeScript/Vite 阻塞错误 | — (正向) |
| 9.2 | Docker 镜像构建链路完整（三阶段 + healthcheck） | — (正向) |
| 9.3 | 启动时 VACUUM 报错（非致命，但每次启动出现） | Low |
| 9.4 | 无数据库迁移框架（Alembic），仅 `create_all` | Medium |
| 9.5 | 无 CI/CD 流水线（无自动化构建、测试、镜像发布） | Medium |
| 9.6 | 无单元测试和集成测试 | High |
| 9.7 | 无版本管理策略（硬编码 1.0.0，无 changelog） | Low |
| 9.8 | Docker 相关功能在 Windows 不可测试 | Medium |
| 9.9 | 项目根目录存在大量治理文档（17+ .md 文件），但无 README 导航索引 | Low |
| 9.10 | LICENSE 文件存在（MIT） | — (正向) |

### Recommendation
- 修复 VACUUM 启动报错（在 `_cleanup_log_spam` 中增加 autocommit 或 connection-level 处理）
- 集成 Alembic 进行数据库迁移管理
- 建立最小 CI（GitHub Actions：lint + build + docker build）
- 为核心模块（auth、tasks、plugins）增加单元测试
- 清理或归档根目录治理文档，README 增加文档索引

---

## Final Verdict

### REJECT

**判定依据**：
存在 3 个 Critical 级安全问题（SECRET_KEY 硬编码、管理员密码硬编码、CORS 配置不安全），在解决之前不具备发布条件。

### 发布条件
满足以下条件后可重新评估为 PASS_WITH_RISK：
1. SECRET_KEY 无默认值（强制环境变量注入）
2. INITIAL_ADMIN_PASSWORD 无默认值或首次启动强制修改
3. CORS 不再使用 `*` + `credentials=True` 组合
4. Docker socket 挂载评估并文档化风险

---

## Top 10 Release Blockers

| # | 类别 | 描述 | 严重程度 |
|---|------|------|---------|
| 1 | Security | SECRET_KEY 硬编码默认值 `change-me-in-production-please`，任何人可伪造 JWT | **Critical** |
| 2 | Security | INITIAL_ADMIN_PASSWORD 硬编码默认值 `admin123`，默认管理员账户可被接管 | **Critical** |
| 3 | Security | CORS `allow_origins=["*"]` + `allow_credentials=True` 组合不安全，存在 CSRF 风险 | **High** |
| 4 | Security | Docker socket 以 `rw` 挂载，容器逃逸可获取宿主机 root 权限 | **High** |
| 5 | Application | btrfs_cleanup 和 rclone_mount 两个已注册工具完全无前端页面，用户不可见、不可操作 | **High** |
| 6 | Navigation | Cloudflare DDNS 在 MainLayout 集成工具子菜单中缺失，只能通过直接 URL 访问 | **Medium** |
| 7 | Quality | 无任何自动化测试（单元测试、集成测试），回归风险极高 | **High** |
| 8 | Container | Docker stats 在 Synology 等平台可能持续返回 0，新双采样方案未经验证 | **Medium** |
| 9 | Infrastructure | 无 CI/CD 流水线，构建和部署依赖手动操作 | **Medium** |
| 10 | Data | 无数据库迁移框架，schema 变更风险不可控 | **Medium** |

---

## 审计签署

| 角色 | 签名 | 日期 |
|------|------|------|
| Release Reviewer | — | 2026-08-01 |
| Product Owner | 待签 | — |
| Architect | 待签 | — |
