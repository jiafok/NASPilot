# ============================================================
# NASPilot — Production Dockerfile
# 3-stage build: frontend → deps → final runtime (slim)
#
# 体积策略（1.41GB → 约 0.59GB）：
#   1. 运行阶段默认不装 node/npm。前端在 stage 1 已构建好，
#      运行时只有 uvicorn。仅当需要 yt-dlp 的 JS 运行时(YouTube 签名)时
#      用 --build-arg WITH_NODE=true 打开。
#   2. ffmpeg/ffprobe 使用官方静态构建（按 TARGETARCH 选 amd64/arm64/armhf），
#      Debian 的 ffmpeg 会拖进 libsdl2 → libgbm1 → libgl1-mesa-dri
#      → mesa-libgallium → libllvm19（约 200MB）+ libflite1/libcodec2/
#      libx265/libz3 等我们完全用不到的东西。
# ============================================================

# ── Stage 1: Frontend build ───────────────────────────────
# Pinned to BUILDPLATFORM: the bundle is arch-independent, so building it once
# on the native arch avoids running npm/vite under QEMU for the arm64 leg.
FROM --platform=$BUILDPLATFORM node:22-alpine AS frontend-builder

WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build


# ── Stage 2: Python deps (pre-installed, cached, cleaned) ─
FROM python:3.11-slim AS backend-deps

COPY backend/requirements.lock.txt ./
RUN pip install --no-cache-dir --no-compile -r requirements.lock.txt

# yt-dlp is intentionally unpinned: YouTube extractor fixes ship weekly.
# ffmpeg (final stage) handles merging, subtitle/thumbnail conversion and MP3.
RUN pip install --no-cache-dir --no-compile --upgrade yt-dlp && \
    find /usr/local/lib/python3.11/site-packages/ -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null ; \
    find /usr/local/lib/python3.11/site-packages/ -type f -name '*.pyc' -delete ; \
    find /usr/local/lib/python3.11/site-packages/ -type d -name tests -prune -exec rm -rf {} + 2>/dev/null ; \
    find /usr/local/lib/python3.11/site-packages/ -type d -name test -prune -exec rm -rf {} + 2>/dev/null ; \
    true


# ── Stage 3: ffmpeg + ffprobe (static binaries) ───────────
# Multi-arch aware: CI builds linux/amd64 + linux/arm64, so the download must
# follow TARGETARCH (a BuildKit automatic platform arg).
FROM --platform=$BUILDPLATFORM python:3.11-slim AS ffmpeg-static

ARG TARGETARCH
ARG FFMPEG_BASE=https://johnvansickle.com/ffmpeg/releases

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl xz-utils && \
    rm -rf /var/lib/apt/lists/* && \
    case "${TARGETARCH:-amd64}" in \
        amd64) FF_ARCH=amd64 ;; \
        arm64) FF_ARCH=arm64 ;; \
        arm)   FF_ARCH=armhf ;; \
        *)     echo "unsupported TARGETARCH=${TARGETARCH}" >&2 ; exit 1 ;; \
    esac && \
    echo "downloading static ffmpeg for ${FF_ARCH}" && \
    curl -fsSL --retry 3 -o /tmp/ff.tar.xz "${FFMPEG_BASE}/ffmpeg-release-${FF_ARCH}-static.tar.xz" && \
    mkdir -p /opt/ffmpeg && \
    tar -xJf /tmp/ff.tar.xz -C /opt/ffmpeg --strip-components=1 && \
    rm -f /tmp/ff.tar.xz && \
    chmod +x /opt/ffmpeg/ffmpeg /opt/ffmpeg/ffprobe && \
    ls -lh /opt/ffmpeg/ffmpeg /opt/ffmpeg/ffprobe


# ── Stage 4: Final runtime (trimmed) ──────────────────────
FROM python:3.11-slim

ARG VERSION=dev
ARG WITH_NODE=false

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl tzdata && \
    rm -rf /var/lib/apt/lists/*

# Optional: JS runtime for yt-dlp YouTube signature solving (~280MB).
# Off by default; enable with --build-arg WITH_NODE=true
RUN if [ "$WITH_NODE" = "true" ]; then \
        apt-get update && \
        apt-get install -y --no-install-recommends nodejs && \
        rm -rf /var/lib/apt/lists/* ; \
    fi

# ffmpeg / ffprobe — static, no shared-library dependency chain
COPY --from=ffmpeg-static /opt/ffmpeg/ffmpeg /opt/ffmpeg/ffprobe /usr/local/bin/

# Copy pre-built site-packages (cleaned)
COPY --from=backend-deps /usr/local/lib/python3.11/site-packages/ /usr/local/lib/python3.11/site-packages/
COPY --from=backend-deps /usr/local/bin/ /usr/local/bin/

# Create non-root user
RUN groupadd -r naspilot -g 1000 && \
    useradd -r -g naspilot -u 1000 -d /app naspilot && \
    mkdir -p /app/data /app/logs && \
    chown -R naspilot:naspilot /app

WORKDIR /app

# App code — LAST layer (only this changes on most pushes)
COPY backend/ ./backend/
COPY --from=frontend-builder /src/dist/ ./backend/frontend/dist/

LABEL org.opencontainers.image.title="NASPilot" \
      org.opencontainers.image.description="All-in-One NAS Automation Platform" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.vendor="NASPilot" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/jiafok/NASPilot"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/backend

EXPOSE 8082

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:8082/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8082", "--timeout-keep-alive", "30"]
