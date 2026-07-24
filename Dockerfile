# ============================================================
# NASPilot — Production Dockerfile
# 3-stage build: frontend → deps → final (optimized)
# ============================================================

# ── Stage 1: Frontend build ───────────────────────────────
FROM node:22-alpine AS frontend-builder

WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build


# ── Stage 2: Python deps (pre-installed, cached, cleaned) ─
FROM python:3.11-slim AS backend-deps

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl tzdata && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN groupadd -r naspilot -g 1000 && \
    useradd -r -g naspilot -u 1000 -d /app naspilot && \
    mkdir -p /app/data /app/logs && \
    chown -R naspilot:naspilot /app

COPY backend/requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt && \
    # Strip ~40-60MB of junk from site-packages
    find /usr/local/lib/python3.11/site-packages/ -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; \
    find /usr/local/lib/python3.11/site-packages/ -type f -name '*.pyc' -delete; \
    find /usr/local/lib/python3.11/site-packages/ -type d -name '*.dist-info' -exec rm -rf {} + 2>/dev/null; \
    find /usr/local/lib/python3.11/site-packages/ -type d -name tests -exec rm -rf {} + 2>/dev/null; \
    true


# ── Stage 3: Final runtime (trimmed) ──────────────────────
FROM python:3.11-slim

ARG VERSION=dev

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl tzdata && \
    rm -rf /var/lib/apt/lists/*

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

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--limit-max-requests", "5000", "--timeout-keep-alive", "30"]
