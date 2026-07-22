"""Async SQLAlchemy database session and engine."""

import logging

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {},
)

async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    """Declarative base for all models."""

    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an async session."""
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Create all tables and apply migrations on first start / dev mode."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Fix legacy SQLite tables with non-autoincrement PKs (from old BigInteger model)
    await _migrate_sqlite_log_entries()


async def _migrate_sqlite_log_entries() -> None:
    """Recreate log_entries table if it has a non-autoincrement PK.

    SQLite only auto-increments ``INTEGER PRIMARY KEY`` columns.
    The old ``BigInteger`` model left a non-autoincrement column,
    causing ``NOT NULL constraint failed: log_entries.id`` on every INSERT.
    This migration drops & recreates the table (log data is ephemeral).
    """
    import sqlalchemy as sa

    if "sqlite" not in settings.DATABASE_URL:
        return

    logger = logging.getLogger("naspilot.db")
    try:
        async with engine.begin() as conn:
            # Check if table exists and has correct schema
            result = await conn.execute(
                sa.text("SELECT sql FROM sqlite_master WHERE type='table' AND name='log_entries'")
            )
            row = result.fetchone()
            if row and "AUTOINCREMENT" not in (row[0] or ""):
                logger.warning("log_entries has non-autoincrement PK — migrating...")
                await conn.execute(sa.text("DROP TABLE IF EXISTS log_entries_new"))
                await conn.execute(sa.text(
                    "CREATE TABLE log_entries_new ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "timestamp DATETIME,"
                    "logger VARCHAR(128),"
                    "level VARCHAR(16),"
                    "source VARCHAR(64),"
                    "message TEXT,"
                    "extra JSON"
                    ")"
                ))
                try:
                    await conn.execute(sa.text(
                        "INSERT INTO log_entries_new SELECT id,timestamp,logger,level,source,message,extra FROM log_entries"
                    ))
                except Exception:
                    pass
                await conn.execute(sa.text("DROP TABLE IF EXISTS log_entries"))
                await conn.execute(sa.text("ALTER TABLE log_entries_new RENAME TO log_entries"))
                logger.info("log_entries migration complete")
    except Exception:
        logger.exception("log_entries migration failed (non-fatal)")
