"""Plugin framework — base classes, metadata, lifecycle, registry.

Every feature module in NASPilot is a plugin. Plugins register themselves
with the central registry, expose a config schema, and are managed via the
API and Web UI.

Lifecycle: install → enable → configure → run → disable → uninstall
"""

from __future__ import annotations

import importlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("naspilot.plugins")


@dataclass
class PluginMeta:
    """Static metadata describing a plugin."""

    slug: str
    name: str
    description: str = ""
    version: str = "1.0.0"
    author: str = ""
    homepage: str = ""
    icon: str = ""
    category: str = "general"  # pt | storage | network | system | ai
    entrypoint: str = ""  # python module path, e.g. "app.plugins.builtin.pt_rss"


class PluginBase(ABC):
    """Base class all plugins must inherit."""

    META: PluginMeta

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config: dict[str, Any] = config or {}

    @property
    def meta(self) -> PluginMeta:
        return self.META

    @abstractmethod
    async def on_enable(self) -> None:
        """Called when the plugin is enabled (register schedulers, etc.)."""
        ...

    @abstractmethod
    async def on_disable(self) -> None:
        """Called when the plugin is disabled (cleanup resources)."""
        ...

    def get_config_schema(self) -> dict[str, Any]:
        """Return a JSON Schema describing the plugin's config options."""
        return {}

    async def run(self, **kwargs: Any) -> Any:
        """Default entry point for ad-hoc plugin actions."""
        return None

    async def notify(self, title: str, message: str, level: str = "info") -> bool:
        """Send a notification through the Notification Center's default channel.
        
        Plugins call this instead of configuring their own webhook.
        Configure channels once in Notification Center UI.
        """
        try:
            from app.core.database import async_session_factory
            from app.models.notification import NotificationChannel
            from app.services.notification_service import send_notification
            from sqlalchemy import select

            async with async_session_factory() as db:
                result = await db.execute(
                    select(NotificationChannel).where(
                        NotificationChannel.enabled.is_(True),
                        NotificationChannel.is_default.is_(True),
                    )
                )
                channels = result.scalars().all()
                if not channels:
                    return False
                for ch in channels:
                    await send_notification(db, ch, title, message, level, event_type="plugin")
                return True
        except Exception:
            logger = logging.getLogger("naspilot.plugins")
            logger.exception("Failed to send plugin notification")
            return False


class PluginRegistry:
    """Central registry for all plugins — builtin and user-installed."""

    def __init__(self) -> None:
        self._plugins: dict[str, PluginBase] = {}
        self._metadata: dict[str, PluginMeta] = {}

    def register(self, plugin_cls: type[PluginBase]) -> None:
        """Register a plugin class (instantiation deferred until enabled)."""
        meta = plugin_cls.META
        self._metadata[meta.slug] = plugin_cls
        logger.debug(f"Registered plugin: {meta.slug}")

    def enable(self, slug: str) -> PluginBase | None:
        """Instantiate and enable a plugin."""
        plugin_cls = self._metadata.get(slug)
        if not plugin_cls:
            logger.warning(f"Plugin not found: {slug}")
            return None
        instance = plugin_cls()
        self._plugins[slug] = instance
        logger.info(f"Plugin enabled: {slug}")
        return instance

    def disable(self, slug: str) -> None:
        """Disable and unload a plugin."""
        instance = self._plugins.pop(slug, None)
        if instance:
            logger.info(f"Plugin disabled: {slug}")

    def get(self, slug: str) -> PluginBase | None:
        return self._plugins.get(slug)

    def list_all(self) -> list[tuple[str, type[PluginBase]]]:
        return list(self._metadata.items())

    def load_builtin(self) -> None:
        """Auto-discover and register all builtin plugins."""
        builtin_modules = [
            "app.plugins.builtin.pt_rss",
            "app.plugins.builtin.alist_upload",
            "app.plugins.builtin.cloudflare_ddns",
            "app.plugins.builtin.docker_backup",
            "app.plugins.builtin.log_cleanup",
            "app.plugins.builtin.btrfs_cleanup",
            "app.plugins.builtin.rclone_mount",
            "app.plugins.builtin.cf_pages",
        ]
        for mod_path in builtin_modules:
            try:
                module = importlib.import_module(mod_path)
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if (
                        isinstance(attr, type)
                        and issubclass(attr, PluginBase)
                        and attr is not PluginBase
                        and hasattr(attr, "META")
                    ):
                        self.register(attr)
            except Exception:
                logger.exception(f"Failed to load builtin plugin module: {mod_path}")

    async def sync_builtin_to_db(self) -> None:
        """Ensure all registered builtin plugins have a DB row in the plugins table."""
        from app.core.database import async_session_factory
        from app.models import Plugin
        from sqlalchemy import select

        async with async_session_factory() as db:
            for slug, cls in self._metadata.items():
                meta = cls.META
                result = await db.execute(select(Plugin).where(Plugin.slug == slug))
                existing = result.scalar_one_or_none()
                if existing:
                    # Update name/version/description if changed
                    dirty = False
                    if existing.name != meta.name: existing.name = meta.name; dirty = True
                    if existing.version != meta.version: existing.version = meta.version; dirty = True
                    if existing.description != meta.description: existing.description = meta.description; dirty = True
                    if dirty: await db.commit()
                else:
                    p = Plugin(
                        slug=meta.slug,
                        name=meta.name,
                        description=meta.description,
                        version=meta.version,
                        author=meta.author,
                        category=meta.category,
                        entrypoint=meta.entrypoint or f"app.plugins.builtin.{slug}",
                        is_builtin=True,
                        enabled=True,
                    )
                    db.add(p)
                    await db.commit()
                    logger.info(f"Created builtin plugin row: {slug}")


# Global registry instance
registry = PluginRegistry()
