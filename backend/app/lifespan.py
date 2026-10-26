"""Simple context guard so the lifespan block below stays compact."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from app.core.config import settings
from app.core.database import async_session_factory
from app.core.logging_config import setup_logging
from app.plugins.registry import registry
from app.scheduler.scheduler_service import shutdown_scheduler, start_scheduler
from app.services.auth_service import bootstrap_admin
from app.services.media.ytdlp import ensure_dependencies

# Strong references for fire-and-forget startup tasks
_startup_tasks: set = set()

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application startup / shutdown lifecycle."""
    setup_logging()
    # ── Startup ────────────────────────────────────────────────────────
    from app.core.database import init_db

    await init_db()

    # Create initial admin user
    async with async_session_factory() as db:
        await bootstrap_admin(db)

    # Register builtin plugins
    registry.load_builtin()

    # Sync builtin plugins to database (so API /plugins returns them)
    await registry.sync_builtin_to_db()

    # Start scheduler
    await start_scheduler()

    # Media Download Center — re-register per-subscription schedules
    from app.services.media_center.migrate_legacy import migrate_legacy_youtube
    from app.services.media_center.scheduler import sync_all_schedules

    # One-time (idempotent) move of legacy YouTube rows into the unified center
    await migrate_legacy_youtube()
    await sync_all_schedules()

    # Start real-time metrics collector
    from app.core.metrics import start_collector
    start_collector()

    # Media Subscription Center — detect yt-dlp/ffmpeg (auto-installs yt-dlp
    # when missing). Runs in the background so a slow pip never blocks boot.
    import asyncio

    _startup_tasks.add(asyncio.create_task(ensure_dependencies()))

    yield

    # ── Shutdown ──────────────────────────────────────────────────────
    await shutdown_scheduler()
