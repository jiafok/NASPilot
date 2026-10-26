"""NASPilot — FastAPI application entry point."""

import logging
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.api.v1.media import router as media_router
from app.api.v1.english import (
    alias_router as english_alias_router,
    router as english_router,
    stream_router as english_stream_router,
)
from app.core.config import settings
from app.lifespan import lifespan

logger = logging.getLogger("naspilot")

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="All-in-One NAS Automation Platform",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

# ── CORS ─────────────────────────────────────────────────────────────────
_cors_origins = settings.CORS_ORIGINS
_cors_allow_credentials = True
if "*" in _cors_origins:
    # Security: never combine allow_credentials=True with wildcard origins
    _cors_allow_credentials = False
    logger.warning("CORS: wildcard origin detected — allow_credentials disabled for security")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── REST API ─────────────────────────────────────────────────────────────
app.include_router(api_router)


@app.middleware("http")
async def log_unhandled_exceptions(request: Request, call_next):
    """Mirror unexpected request exceptions into naspilot.log for UI log center visibility."""
    try:
        return await call_next(request)
    except Exception:
        logger.exception("Unhandled exception: %s %s", request.method, request.url.path)
        raise


@app.get("/api/health", tags=["health"])
async def health():
    """Liveness probe."""
    return {"status": "ok", "app": settings.APP_NAME, "version": settings.APP_VERSION}


@app.get("/health/ytdlp", tags=["health"])
async def health_ytdlp():
    """Media dependency probe — reports yt-dlp / ffmpeg availability."""
    from app.services.media_center.ytdlp_core import dependency_health

    return await dependency_health()


@app.get("/health/media", tags=["health"])
async def health_media():
    """Media Download Center health — engines, providers, network, directories."""
    from app.api.v1.media import build_health

    return await build_health()


# Unversioned alias for the Media Download Center (/api/media/...)
app.include_router(media_router, prefix="/api", include_in_schema=False)

# English Learning Center — client contract aliases (/api/english/...)
app.include_router(english_router, prefix="/api", include_in_schema=False)
# Streaming: register AFTER the media router so literal paths like
# /api/media/subscriptions win over the numeric /api/media/{id} alias.
app.include_router(english_stream_router, prefix="/api", include_in_schema=False)
app.include_router(english_alias_router, prefix="/api", include_in_schema=False)


# ── Static frontend + SPA fallback ──────────────────────────────────────
_frontend_dist = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend", "dist")
_index_html = os.path.join(_frontend_dist, "index.html")

if os.path.isfile(_index_html):
    logger.info(f"Frontend dist found: {_frontend_dist}")

    # Serve static assets (js, css, images, etc.)
    app.mount("/assets", StaticFiles(directory=os.path.join(_frontend_dist, "assets")), name="assets")

    @app.get("/{full_path:path}", response_class=HTMLResponse)
    async def spa_fallback(full_path: str):
        """Catch-all: serve index.html for all non-API, non-asset routes (SPA routing)."""
        return FileResponse(_index_html)

else:
    logger.warning(f"Frontend dist NOT found at {_frontend_dist} — SPA disabled")
