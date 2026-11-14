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

    async def on_startup(self) -> None:
        """Called for every enabled instance when the application boots.

        Default is a no-op; plugins that own long-lived external state (a FUSE
        mount, a listener) override it to restore that state. Raising is
        tolerated — one broken plugin must not block the whole boot.
        """
        return None

    async def notify(self, title: str, message: str, level: str = "info") -> bool:
        """Send a notification through the Notification Center's default channel.
        
        Plugins call this instead of configuring their own webhook.
        Configure channels once in Notification Center UI.
        """
        from app.core.database import async_session_factory
        from app.models.notification import NotificationChannel
        from app.services.notification_service import send_notification
        from sqlalchemy import select

        notif_logger = logging.getLogger("naspilot.plugins")
        try:
            async with async_session_factory() as db:
                result = await db.execute(
                    select(NotificationChannel).where(
                        NotificationChannel.enabled.is_(True),
                        NotificationChannel.is_default.is_(True),
                    )
                )
                channels = result.scalars().all()
                if not channels:
                    notif_logger.info("通知跳过: 无默认渠道 — %s", title)
                    return False
                notif_logger.info("插件通知: %s — %s", title, message[:200])
                for ch in channels:
                    await send_notification(db, ch, title, message, level, event_type="plugin")
                return True
        except Exception:
            notif_logger.exception("Failed to send plugin notification")
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

    async def run_startup_hooks(self) -> None:
        """Call ``on_startup`` on every enabled builtin instance.

        Plugins that own external state (the FUSE mount is the reason this
        exists) need a chance to rebuild it after a container restart. Runs
        one instance at a time and never lets a failure propagate: a broken
        mount must not stop the web UI from coming up.
        """
        import asyncio

        from sqlalchemy import select

        from app.core.database import async_session_factory
        from app.models import Plugin, PluginInstance

        try:
            async with async_session_factory() as db:
                result = await db.execute(
                    select(PluginInstance, Plugin)
                    .join(Plugin, Plugin.id == PluginInstance.plugin_id)
                    .where(
                        PluginInstance.enabled.is_(True),
                        Plugin.enabled.is_(True),
                    )
                    .order_by(PluginInstance.id)
                )
                rows = result.all()
        except Exception:
            logger.exception("Could not load plugin instances for startup hooks")
            return

        for instance, plugin in rows:
            plugin_cls = self._metadata.get(plugin.slug)
            if plugin_cls is None:
                continue
            hook = getattr(plugin_cls, "on_startup", None)
            if hook is None or hook is PluginBase.on_startup:
                continue  # inherited no-op, nothing to do
            try:
                runtime = plugin_cls(instance.config or {})
                await asyncio.wait_for(runtime.on_startup(), timeout=60)
            except asyncio.TimeoutError:
                logger.warning(
                    "Plugin startup hook timed out: %s (%s)", plugin.slug, instance.name
                )
            except Exception:
                logger.exception(
                    "Plugin startup hook failed: %s (%s)", plugin.slug, instance.name
                )


# Global registry instance
registry = PluginRegistry()
