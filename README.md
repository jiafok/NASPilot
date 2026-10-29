# NASPilot

**All-in-One NAS Automation Platform**

> 一站式 NAS 自动化运维平台 — 将分散的脚本、Cron 任务统一管理，通过 Web UI 管理一切。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![React 18](https://img.shields.io/badge/React-18-61DAFB.svg)](https://react.dev/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg)](https://www.docker.com/)

---

## 🌟 简介

NASPilot 是一个面向 **NAS 用户** 和 **HomeLab 用户** 的自动化运维平台。它将常见的独立脚本（PT RSS 下载、AList 上传、Cloudflare DDNS、Docker 备份、Cron 调度）整合为统一平台，通过 Web UI 集中管理，无需 SSH、无需编辑 YAML。

### 核心能力

| 模块 | 功能 |
|------|------|
| 📊 Dashboard | CPU/内存/磁盘/Docker/qB/AList 实时监控 |
| ⏰ Task Center | 可视化 Cron 替代方案，支持 Shell/Python/Docker 任务 |
| 🎬 PT RSS | RSS 订阅、qBittorrent 集成、Free 检测、做种策略、空间管理 |
| 📁 AList Upload | 自动扫描、规则匹配、上传历史 |
| 🌐 Cloudflare DDNS | IPv4/IPv6 自动更新、多域名管理 |
| 🐳 Docker Backup | 容器配置 + 数据卷备份、自动恢复 |
| 🔔 Notification Center | 飞书 / 企业微信 / Telegram / 邮件 |
| 🔌 Plugin System | 全模块插件化，支持安装/升级/启用/禁用/卸载 |
| 📜 Log Center | 在线查看、搜索、下载、自动清理 |
| 🤖 AI Assistant | 自然语言运维诊断和修复建议 |

---

## 🚀 快速开始

### 一键 Docker 部署（免 clone，推荐）

```bash
mkdir -p naspilot && cd naspilot
curl -fsSL https://raw.githubusercontent.com/jiafok/NASPilot/main/docker-compose.yml -o docker-compose.yml
curl -fsSL https://raw.githubusercontent.com/jiafok/NASPilot/main/.env.example -o .env
# 按需修改 .env（端口 / 媒体路径），密钥会自动生成
docker compose up -d
```

访问 `http://<NAS-IP>:8080`，默认账号 `admin / admin123`。

### 媒体下载中心部署（单 YAML，含 yt-dlp + ffmpeg）

`docker-compose.deploy.yml` 会从源码构建镜像，`yt-dlp` 与 `ffmpeg` 已烘进镜像，容器内**无需再装任何东西**。

```bash
# .env 可选：不建也能跑，全部有默认值
cp .env.deploy.example .env
# 可选：NASPILOT_PORT / NAS_MEDIA_ROOT / MEDIA_PROXY_URL
docker compose -f docker-compose.deploy.yml up -d --build
```

访问 `http://<NAS-IP>:8082` → 「媒体中心」。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `NASPILOT_PORT` | `8082` | 宿主机端口 |
| `NASPILOT_DATA` | `./data` | 数据库/日志目录 |
| `NAS_MEDIA_ROOT` | `./data/media` | 媒体根目录（NAS 上建议绝对路径，如 `/volume1/media`）；容器内自动使用其 `Video/` 与 `Audio/` 子目录 |

> 媒体目录也可在**「系统设置 → 媒体」**里直接改（存数据库，立即生效，无需重建容器）：
> 那里填的是容器内路径，需与 compose 的挂载目标对应。
| `MEDIA_PROXY_URL` | 空 | 全局代理（`http://`/`socks5://`）；也可在单个订阅里单独设置 |
| `EMBY_URL` / `JELLYFIN_URL` / `AUDIOBOOKSHELF_URL` | 空 | 填了会在历史记录里生成播放器直达链接 |

镜像约 **590MB**（Debian python-slim + 静态 ffmpeg + 依赖），`yt-dlp`/`ffmpeg` 已内置。
默认不装 node（省 ~280MB）；若 yt-dlp 提示需要 JS 运行时，用
`NASPILOT_WITH_NODE=true`（或 `--build-arg WITH_NODE=true`）重新构建。

自检：`curl -H "Authorization: Bearer <管理员访问令牌>" http://<NAS-IP>:8082/health/media` —— `status: ok` 表示 yt-dlp/ffmpeg 就绪；`network.providers` 显示各站点连通性。媒体诊断仅向管理员开放。

> 需要代理整机出网时，把 `docker-compose.deploy.yml` 末尾注释的 gluetun 段打开，并把 `network_mode` 改为 `service:gluetun`。

### 从源码运行

```bash
# 后端
cd backend
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000

# 前端
cd frontend
npm install
npm run dev
```

---

## 📚 英语学习中心

NASPilot 内置的英语学习服务端模块（EnglishFlow 客户端专用）：
资源扫描、课程/课时元数据、字幕解析、生词提取、学习进度、AI 老师与媒体流。

- 直接复用媒体库：媒体在 `data/media`（容器内 `/app/data/media`），媒体中心下载的文件自动成为课时，无需单独目录或额外挂载
- 目录约定：`data/media/<频道>/<课程>/<课时>.mp4|.mp3`（同名 `.srt`/`.vtt` 自动绑定）
- 网页入口：菜单「英语学习」
- 客户端接口：`/api/english/*`，媒体 `/api/media/{lessonId}`
- 设计与接口：`docs/english-learning-center/`

### Android 学习 App

Android 端用 Capacitor 封装现有 React PWA，直接复用 `/m` 界面，不维护第二套 UI。在 `frontend/` 执行 `npm run cap:sync:android` 同步资源，再用 `npm run cap:open:android` 在 Android Studio 打开，或 `npm run cap:run:android` 部署到连接的设备。构建需要 Android Studio、JDK 21 和 Android SDK。

首次启动输入手机可访问的 NAS 地址，例如 `http://192.168.1.100:8082` 或 HTTPS 域名；App 会测试公开目录接口后保存，也可在「我的 → NAS 连接地址」修改。地址保存在 Capacitor Preferences。Android WebView 支持可信局域网 HTTP；互联网访问请使用 HTTPS 或 VPN。后端 CORS 默认允许 Capacitor Android/iOS origin。

### 学习账号

- 在登录页选择「创建学习账号」，或访问 `http://<NAS-IP>:8082/register`。默认通过管理员发放的邀请码注册，无需购买短信服务。用户名为 3–64 位字母、数字、下划线、点或短横线，密码至少 8 位。
- 管理员进入「系统设置 → 注册与套餐 → 邀请码注册」，点击「生成邀请码」并复制给用户。每码只能注册一次，可设置 1–365 天有效期（默认 7 天）、查看使用状态；撤销未使用码或清理过期码会直接删除记录，已使用码在对应账号删除时一并清理。完整码仅生成时展示，数据库只保存摘要；注册失败不会消耗邀请码，删除账号也不会恢复已使用的码。邀请码不能保证一人一号，应仅向可信用户发放。
- 自助注册的账号固定为普通学习账号，登录后进入 `/m`。系统设置、下载管理、文件、日志和容器终端均要求管理员权限，普通账号不能通过直接访问 API 绕过限制。
- 管理员保留 NAS 后台入口，也可以访问 `/m` 学习。请仅向可信用户开放注册入口；此版本不包含邮箱验证。
- 管理员可在后台「用户管理」（`/users`）搜索、新增或删除账号。新增默认为学习用户，也可指定管理员；禁止删除当前登录账号或最后一个有效管理员。删除会清理该账号的学习数据和登录会话，不删除共享课程或媒体文件；删除后用户名可重新使用，旧令牌不会恢复访问。
- 「系统设置 → 注册与套餐」统一维护邀请码注册开关、可选的阿里云短信配置、套餐价格和管理员联系方式。开启短信注册需配置 AccessKey、短信签名及验证码模板（变量 `code`）；未配置短信不影响邀请码注册。短信方式需验证中国大陆手机号（+86），每个手机号仅限一次，删除账号仍保留号码占用记录。不存在测试验证码放行模式；验证码 5 分钟有效、单次使用、最多错误 5 次，发送按号码和来源 IP 限流。密钥不回显。
- 新建学习账号获得 3 天试用；升级前的账号保留原访问权限，管理员始终不受期限限制。到期后仍能登录查看 `/membership` 的套餐和联系方式，但英语 API、AI 和媒体流由服务端拒绝访问。
- 支持包月、包季、包年、永久四类套餐，默认不启用、不预设售价。当前使用人工收款，管理员确认收款后在用户管理中开通或续期，不包含微信/支付宝自动支付。月/季/年按自然月计算，未到期续费顺延；永久不设到期日。开通时记录价格快照、操作人和备注，后续调价不会改写历史记录。
- 首页显示精选/继续学习、学习概览和课程封面网格。课程封面优先使用课程文件夹中的图片（如 `cover.jpg`、`folder.jpg`），没有时使用第一课的图片；更换封面后由管理员重新扫描媒体库。
- 课程详情的「课时排序」支持课程顺序、名称、时长和难度排序，仅改变当前列表展示，不修改源文件或其他用户的数据。

## 🏗️ 架构概览

```
NASPilot/
├── backend/                 # FastAPI 后端
│   ├── app/
│   │   ├── api/             # REST API 路由
│   │   ├── core/            # 配置、安全、数据库
│   │   ├── models/          # SQLAlchemy 模型
│   │   ├── schemas/         # Pydantic 模型
│   │   ├── services/        # 业务逻辑
│   │   ├── plugins/         # 插件框架 + 内置插件
│   │   ├── scheduler/       # APScheduler 任务调度
│   │   └── main.py
│   └── tests/
├── frontend/               # React 前端
│   ├── src/
│   │   ├── components/      # 通用组件
│   │   ├── pages/           # 页面
│   │   ├── services/        # API 调用
│   │   └── stores/          # 状态管理
│   └── ...
├── docker/                 # Docker 构建文件
├── docs/                   # 文档
└── docker-compose.yml      # 部署编排
```

**技术栈：** FastAPI · SQLAlchemy · APScheduler · SQLite · React 18 · Ant Design 5 · Vite · Docker

---

## 📋 开发路线图

- **V1.0** — 用户登录 · Dashboard · Task Center · PT RSS · 飞书通知
- **V2.0** — AList Upload · Cloudflare DDNS · Docker Backup
- **V3.0** — Plugin Center · 应用市场
- **V4.0** — AI Assistant
- **V5.0** — 多用户 · 权限管理 · 团队协作

---

## 📄 License

MIT
