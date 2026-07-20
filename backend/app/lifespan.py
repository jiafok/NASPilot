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

    # Start scheduler
    await start_scheduler()

    yield

    # ── Shutdown ──────────────────────────────────────────────────────
    await shutdown_scheduler()
