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
# 修改 .env 中 SECRET_KEY 和 ADMIN_PASSWORD
docker compose up -d
```

访问 `http://<NAS-IP>:8080`，默认账号 `admin / admin123`。

### 媒体下载中心部署（单 YAML，含 yt-dlp + ffmpeg）

`docker-compose.deploy.yml` 会从源码构建镜像，`yt-dlp` 与 `ffmpeg` 已烘进镜像，容器内**无需再装任何东西**。

```bash
cp .env.deploy.example .env
# 必改：SECRET_KEY（openssl rand -hex 32）
# 可选：NASPILOT_PORT / NAS_MEDIA_VIDEO / NAS_MEDIA_AUDIO / MEDIA_PROXY_URL
docker compose -f docker-compose.deploy.yml up -d --build
```

访问 `http://<NAS-IP>:8082` → 「媒体中心」。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `NASPILOT_PORT` | `8082` | 宿主机端口 |
| `NASPILOT_DATA` | `./data` | 数据库/日志目录 |
| `NAS_MEDIA_VIDEO` | `./data/media/Video` | 视频落盘目录（NAS 上建议绝对路径，如 `/volume1/media/Video`） |
| `NAS_MEDIA_AUDIO` | `./data/media/Audio` | 音频/播客落盘目录 |
| `MEDIA_PROXY_URL` | 空 | 全局代理（`http://`/`socks5://`）；也可在单个订阅里单独设置 |
| `EMBY_URL` / `JELLYFIN_URL` / `AUDIOBOOKSHELF_URL` | 空 | 填了会在历史记录里生成播放器直达链接 |

镜像约 **590MB**（Debian python-slim + 静态 ffmpeg + 依赖），`yt-dlp`/`ffmpeg` 已内置。
默认不装 node（省 ~280MB）；若 yt-dlp 提示需要 JS 运行时，用
`NASPILOT_WITH_NODE=true`（或 `--build-arg WITH_NODE=true`）重新构建。

自检：`curl http://<NAS-IP>:8082/health/media` —— `status: ok` 表示 yt-dlp/ffmpeg 就绪；`network.providers` 显示各站点连通性。

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
