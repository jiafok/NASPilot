# ============================================================
# NASPilot — Production Dockerfile
# 3-stage build (frontend → deps → final), Immich-style
# Layer strategy: deps locked → only app code changes on push
# ============================================================

# ── Stage 1: Frontend build ───────────────────────────────
FROM node:22-alpine AS frontend-builder

WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build


# ── Stage 2: Python deps (pre-installed, cached forever) ──
FROM python:3.12-slim AS backend-deps

RUN --mount=type=cache,target=/var/lib/apt,sharing=locked \
    --mount=type=cache,target=/var/cache/apt,sharing=locked \
    apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl tzdata && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN groupadd -r naspilot -g 1000 && \
    useradd -r -g naspilot -u 1000 -d /app naspilot && \
    mkdir -p /app/data /app/logs && \
    chown -R naspilot:naspilot /app

COPY backend/requirements.lock.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir -r requirements.lock.txt


# ── Stage 3: Final runtime (only app code, smallest layer) ─
FROM python:3.12-slim

ARG VERSION=dev
ARG BUILD_DATE
ARG VCS_REF

RUN --mount=type=cache,target=/var/lib/apt,sharing=locked \
    --mount=type=cache,target=/var/cache/apt,sharing=locked \
    apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl tzdata && \
    rm -rf /var/lib/apt/lists/*

# Pull pre-built packages & user from stage 2
COPY --from=backend-deps /usr/local/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/
COPY --from=backend-deps /usr/local/bin/ /usr/local/bin/
COPY --from=backend-deps /etc/group /etc/passwd /etc/shadow /etc/
COPY --from=backend-deps /app/data /app/data
COPY --from=backend-deps /app/logs /app/logs

WORKDIR /app

# ── App code (LAST — only this changes on most pushes) ──
COPY backend/ ./backend/
COPY --from=frontend-builder /src/dist/ ./backend/frontend/dist/

LABEL org.opencontainers.image.title="NASPilot" \
      org.opencontainers.image.description="All-in-One NAS Automation Platform" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.vendor="NASPilot" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/jiafok/NASPilot"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/backend

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

USER naspilot

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
