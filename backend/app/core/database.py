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
    await _migrate_sqlite_autoincrement("log_entries", "id,timestamp,logger,level,source,message,extra")
    await _migrate_sqlite_autoincrement("task_executions", "id,task_id,start_time,end_time,status,exit_code,stdout,stderr,duration_ms,triggered_by,error_message")
    await _migrate_sqlite_autoincrement("notification_records", "id,channel_id,channel_type,title,message,level,event_type,status,error_message,created_at")


async def _migrate_sqlite_autoincrement(table: str, columns: str) -> None:
    """Recreate a SQLite table with INTEGER PRIMARY KEY AUTOINCREMENT if needed.

    SQLite only auto-increments ``INTEGER PRIMARY KEY`` / ``INTEGER PRIMARY KEY AUTOINCREMENT``.
    Tables created by the old ``BigInteger`` model lack autoincrement, causing
    ``NOT NULL constraint failed: <table>.id`` on every INSERT.
    """
    import sqlalchemy as sa

    if "sqlite" not in settings.DATABASE_URL:
        return

    logger = logging.getLogger("naspilot.db")
    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                sa.text(f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{table}'")
            )
            row = result.fetchone()
            if not row:
                return  # table doesn't exist yet
            ddl = row[0] or ""
            if "AUTOINCREMENT" in ddl:
                return  # already correct

            logger.warning("%s has non-autoincrement PK — migrating...", table)
            await conn.execute(sa.text(f"DROP TABLE IF EXISTS {table}_new"))
            # Build CREATE TABLE from existing schema, add AUTOINCREMENT
            new_ddl = ddl.replace("INTEGER NOT NULL", "INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT", 1)
            new_ddl = new_ddl.replace(f"CREATE TABLE {table}", f"CREATE TABLE {table}_new")
            await conn.execute(sa.text(new_ddl))
            try:
                await conn.execute(sa.text(f"INSERT INTO {table}_new SELECT {columns} FROM {table}"))
            except Exception:
                pass
            await conn.execute(sa.text(f"DROP TABLE IF EXISTS {table}"))
            await conn.execute(sa.text(f"ALTER TABLE {table}_new RENAME TO {table}"))
            logger.info("%s migration complete", table)
    except Exception:
        logger.exception("%s migration failed (non-fatal)", table)
