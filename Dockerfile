# ============ 前端构建阶段 ============
FROM node:20-alpine AS frontend-builder
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --legacy-peer-deps
COPY frontend/ ./
RUN npm run build

# ============ 后端运行阶段 ============
FROM python:3.11-slim

LABEL org.opencontainers.image.title="NASPilot"
LABEL org.opencontainers.image.description="All-in-One NAS Automation Platform"
LABEL org.opencontainers.image.version="1.0.0"

# 安装运行时依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates cron \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 安装 Python 依赖
COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir \
    fastapi[standard] uvicorn[standard] \
    sqlalchemy[asyncio] aiosqlite \
    "passlib[bcrypt]" "bcrypt<4.1" \
    python-jose[cryptography] \
    python-multipart pydantic-settings \
    pyyaml apscheduler httpx psutil \
    docker websockets python-dateutil \
    requests feedparser alembic

# 复制后端代码（保持 backend/ 目录结构，与本地一致）
COPY backend/ ./backend/

# 复制前端构建产物
COPY --from=frontend-builder /frontend/dist/ ./backend/frontend/dist/

# 创建数据目录
RUN mkdir -p /app/data /app/logs

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app/backend

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
