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

    #: Real files that live at the root of the bundle. Without this the catch-all
    #: below answered index.html for them with `text/html`, which silently broke
    #: the web app manifest and made the service worker refuse to register
    #: ("unsupported MIME type"). Anything not listed still falls through to SPA
    #: routing, which is what deep links need.
    _root_files = {
        "sw.js": "text/javascript",
        "manifest.webmanifest": "application/manifest+json",
        "favicon.svg": "image/svg+xml",
        "pwa-icon.svg": "image/svg+xml",
        "pwa-icon-maskable.svg": "image/svg+xml",
        "icons.svg": "image/svg+xml",
    }

    @app.get("/{full_path:path}", response_class=HTMLResponse)
    async def spa_fallback(full_path: str):
        """Serve a real root file when it exists, otherwise index.html."""
        media_type = _root_files.get(full_path)
        if media_type:
            candidate = os.path.join(_frontend_dist, full_path)
            if os.path.isfile(candidate):
                return FileResponse(candidate, media_type=media_type)
        return FileResponse(_index_html)

else:
    logger.warning(f"Frontend dist NOT found at {_frontend_dist} — SPA disabled")
