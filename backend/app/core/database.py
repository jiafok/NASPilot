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
    # Clean up self-referential log spam from previous sqlalchemy.engine INFO feedback loop
    await _cleanup_log_spam()


async def _migrate_sqlite_autoincrement(table: str, columns: str) -> None:
    """Fix SQLite tables where ``id`` is ``BIGINT`` (does not auto-increment).

    SQLite auto-increments only ``INTEGER PRIMARY KEY`` columns, not ``BIGINT``.
    If the ``id`` column is ``BIGINT``, drop & recreate the table with correct type.
    Tables already using ``INTEGER`` are left untouched.
    """
    import sqlalchemy as sa
    import re

    if "sqlite" not in settings.DATABASE_URL:
        return

    logger = logging.getLogger("naspilot.db")
    try:
        async with engine.begin() as conn:
            # Check if table exists
            result = await conn.execute(
                sa.text(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'")
            )
            if not result.fetchone():
                return

            # Check id column type via PRAGMA
            info = await conn.execute(sa.text(f"PRAGMA table_info('{table}')"))
            id_col = None
            for row in info:
                if row[1] == 'id':  # row[1] = column name
                    id_col = {'type': (row[2] or '').upper(), 'pk': bool(row[5])}
                    break

            if not id_col:
                return

            # SQLite auto-increments INTEGER PRIMARY KEY automatically
            if id_col['type'] == 'INTEGER' and id_col['pk']:
                return  # already correct

            logger.warning("%s has id=%s (pk=%s) — migrating...", table, id_col['type'], id_col['pk'])

            # Build CREATE TABLE with correct id type from sqlite_master
            raw = await conn.execute(
                sa.text(f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{table}'")
            )
            ddl = raw.fetchone()[0] or ""

            # Replace id column definition: id BIGINT NOT NULL → id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT
            new_ddl = re.sub(r'"id"\s+BIGINT\s+NOT\s+NULL', '"id" INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT', ddl, count=1)
            new_ddl = re.sub(r'\bid\s+BIGINT\s+NOT\s+NULL', 'id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT', new_ddl, count=1)
            # Remove the old table-level PRIMARY KEY(id) constraint (handles both quoted & unquoted)
            new_ddl = re.sub(r',\s*PRIMARY KEY\s*\("id"\)', '', new_ddl)
            new_ddl = re.sub(r',\s*PRIMARY KEY\s*\(id\)', '', new_ddl)
            new_ddl = re.sub(r'PRIMARY KEY\s*\("id"\)\s*,?\s*', '', new_ddl)
            new_ddl = re.sub(r'PRIMARY KEY\s*\(id\)\s*,?\s*', '', new_ddl)

            # Rename table in DDL
            new_ddl = new_ddl.replace(f'CREATE TABLE "{table}"', f'CREATE TABLE "{table}_new"')
            new_ddl = new_ddl.replace(f"CREATE TABLE {table}", f"CREATE TABLE {table}_new")

            await conn.execute(sa.text(f"DROP TABLE IF EXISTS {table}_new"))
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


async def _cleanup_log_spam() -> None:
    """Delete all legacy DB log entries — logs now go to file only.

    The log_entries table is kept for schema compatibility but no new rows
    are written. Delete all existing rows to reduce DB file size.
    """
    import sqlalchemy as sa

    if "sqlite" not in settings.DATABASE_URL:
        return

    logger = logging.getLogger("naspilot.db")
    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                sa.text(f"SELECT name FROM sqlite_master WHERE type='table' AND name='log_entries'")
            )
            if not result.fetchone():
                return

            # Delete ALL legacy log entries — logs now go to file only
            result = await conn.execute(sa.text("DELETE FROM log_entries"))
            deleted = result.rowcount
            if deleted > 0:
                logger.info("Deleted %d legacy DB log entries (logs now file-only)", deleted)

            # Vacuum to reclaim disk space
            await conn.execute(sa.text("VACUUM"))
            logger.info("DB vacuumed to reclaim space")
    except Exception:
        logger.exception("Cleanup of legacy log entries failed (non-fatal)")
