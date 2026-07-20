"""SQLAlchemy ORM models — database table definitions for NASPilot.

ER Diagram (simplified):

    User ──< ApiKey
      │
      └──< Task ──< TaskExecution
            │
            └── (references PluginInstance)

    Plugin ──< PluginInstance ──< PluginInstanceConfig

    Setting (KV store)
    NotificationChannel ──< NotificationRecord
    LogEntry
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base


class TimestampMixin:
    """Adds created_at / updated_at to a model."""

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ══════════════════════════════════════════════════════════════════════
#  User & Auth
# ══════════════════════════════════════════════════════════════════════


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(128))
    avatar_url: Mapped[str | None] = mapped_column(String(512))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    preferences: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict)

    api_keys: Mapped[list["ApiKey"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class ApiKey(TimestampMixin, Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(16), index=True)  # first 8 chars for lookup
    hashed_key: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(back_populates="api_keys")


# ══════════════════════════════════════════════════════════════════════
#  Task System (replaces cron)
# ══════════════════════════════════════════════════════════════════════


class Task(TimestampMixin, Base):
    """A scheduled task — replaces individual cron entries."""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # shell | python | docker
    command: Mapped[str] = mapped_column(Text, nullable=False)  # script path or inline command
    args: Mapped[str | None] = mapped_column(Text)  # JSON array of args
    cron_expr: Mapped[str | None] = mapped_column(String(128))  # cron-style expression
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1", index=True)
    timeout: Mapped[int] = mapped_column(Integer, default=3600)  # seconds, 0 = no timeout
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    retry_delay: Mapped[int] = mapped_column(Integer, default=60)  # seconds
    working_dir: Mapped[str | None] = mapped_column(String(512))
    env_vars: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict)  # environment variables
    plugin_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("plugins.id", ondelete="SET NULL"), nullable=True, index=True
    )  # linked plugin if any
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    executions: Mapped[list["TaskExecution"]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="TaskExecution.id.desc()"
    )

    plugin: Mapped["Plugin | None"] = relationship(back_populates="tasks")


class TaskExecution(Base):
    """Record of a single task execution."""

    __tablename__ = "task_executions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending|running|success|failed|timeout|cancelled
    exit_code: Mapped[int | None] = mapped_column(Integer)
    stdout: Mapped[str | None] = mapped_column(Text)
    stderr: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    triggered_by: Mapped[str | None] = mapped_column(String(64))  # scheduler | manual | retry
    error_message: Mapped[str | None] = mapped_column(Text)

    task: Mapped["Task"] = relationship(back_populates="executions")


# ══════════════════════════════════════════════════════════════════════
#  Plugin System
# ══════════════════════════════════════════════════════════════════════


class Plugin(TimestampMixin, Base):
    """Installed plugin metadata."""

    __tablename__ = "plugins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    author: Mapped[str | None] = mapped_column(String(128))
    homepage: Mapped[str | None] = mapped_column(String(512))
    icon: Mapped[str | None] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(32), default="general", index=True)  # pt | storage | network | system | ai
    entrypoint: Mapped[str] = mapped_column(String(256), nullable=False)  # python module path
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1", index=True)
    installed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    config_schema: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # JSON schema for plugin config

    instances: Mapped[list["PluginInstance"]] = relationship(back_populates="plugin", cascade="all, delete-orphan")
    tasks: Mapped[list["Task"]] = relationship(back_populates="plugin")


class PluginInstance(TimestampMixin, Base):
    """An instance of a plugin with its own configuration."""

    __tablename__ = "plugin_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plugin_id: Mapped[int] = mapped_column(ForeignKey("plugins.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")

    plugin: Mapped["Plugin"] = relationship(back_populates="instances")


# ══════════════════════════════════════════════════════════════════════
#  Notification
# ══════════════════════════════════════════════════════════════════════


class NotificationChannel(TimestampMixin, Base):
    """A configured notification channel (feishu, telegram, etc.)."""

    __tablename__ = "notification_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # feishu | wechat_work | telegram | email
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # webhook, secret, token, etc.
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1", index=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class NotificationRecord(Base):
    """Log of every notification sent."""

    __tablename__ = "notification_records"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    channel_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("notification_channels.id", ondelete="SET NULL"), index=True
    )
    channel_type: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(256))
    message: Mapped[str] = mapped_column(Text)
    level: Mapped[str] = mapped_column(String(16), default="info", index=True)  # info|warn|error|success
    event_type: Mapped[str | None] = mapped_column(String(64), index=True)  # task_success|task_failed|pt_added|daily_summary
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending|sent|failed
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


# ══════════════════════════════════════════════════════════════════════
#  Log Center
# ══════════════════════════════════════════════════════════════════════


class LogEntry(Base):
    """Structured log entry stored in DB for search/filter."""

    __tablename__ = "log_entries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    logger: Mapped[str] = mapped_column(String(128), default="root", index=True)
    level: Mapped[str] = mapped_column(String(16), default="INFO", index=True)
    source: Mapped[str | None] = mapped_column(String(64), index=True)  # system|scheduler|plugin:<slug>|task:<id>
    message: Mapped[str] = mapped_column(Text)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON)


# ══════════════════════════════════════════════════════════════════════
#  System Settings (KV store)
# ══════════════════════════════════════════════════════════════════════


class Setting(TimestampMixin, Base):
    """Arbitrary key-value settings store."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    value_type: Mapped[str] = mapped_column(String(16), default="string")  # string|int|bool|json
    description: Mapped[str | None] = mapped_column(String(512))
    category: Mapped[str] = mapped_column(String(64), default="general", index=True)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")  # readable without auth
