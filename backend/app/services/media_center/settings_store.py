"""Runtime-configurable media paths.

The download directories used to be env-only, which meant a NAS user had to
edit `docker-compose.yml` (and recreate the container) just to change where
files land. This module makes the root a *setting* instead, so it can be
changed from the UI / API while the container keeps running.

Resolution order, highest priority first::

    per-directory override   DB setting MEDIA_VIDEO_DIR / MEDIA_AUDIO_DIR
                             (falls back to the same-named env var)
    root                     DB setting MEDIA_ROOT
                             (falls back to env MEDIA_ROOT, then
                             <DATA_DIR>/media — i.e. the same volume as the
                             database, so one ``./data`` mount covers both)
    final path               <root>/Video  and  <root>/Audio

Everything is cached in memory so the hot download path never touches the
database; `load()` refreshes the cache at startup and after a settings change.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

#: Setting keys understood by this module.
KEY_ROOT = "MEDIA_ROOT"
KEY_VIDEO = "MEDIA_VIDEO_DIR"
KEY_AUDIO = "MEDIA_AUDIO_DIR"

#: Legacy container-side root. Databases created before the media folder moved
#: into the data volume still point here; `load()` migrates them.
LEGACY_ROOT = Path("/Media")


def default_root() -> Path:
    """Root when nothing is configured: ``<DATA_DIR>/media``.

    Deriving it from ``DATA_DIR`` is what makes the deployment a single volume:
    ``./data`` is mounted at ``/app/data``, so media lands in
    ``/app/data/media`` with no extra mount and no extra setting.
    """
    base = Path(settings.DATA_DIR)
    try:
        base = base.resolve()
    except OSError:  # pragma: no cover - resolve rarely fails
        base = base.absolute()
    return base / "media"

#: Seeded rows so the values are editable in the UI without SQL.
#: (key, value, value_type, description)
SEEDS: tuple[tuple[str, str, str, str], ...] = (
    (
        KEY_ROOT,
        "",  # empty = <DATA_DIR>/media (filled in by load())
        "string",
        "媒体下载根目录（容器内路径）。留空 = 数据目录下的 media/，"
        "与数据库同一个挂载卷。其下自动使用 Video/ 与 Audio/。",
    ),
    (
        KEY_VIDEO,
        "",
        "string",
        "可选：单独指定视频目录。留空则使用 <MEDIA_ROOT>/Video。",
    ),
    (
        KEY_AUDIO,
        "",
        "string",
        "可选：单独指定音频目录。留空则使用 <MEDIA_ROOT>/Audio。",
    ),
)

_cache: dict[str, str] = {}
_loaded = False


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _db_value(key: str) -> str:
    """Last value read from the settings table (empty when unset)."""
    return _clean(_cache.get(key))


def _env_value(name: str) -> str:
    return _clean(os.environ.get(name))


def _resolve_root() -> Path:
    for candidate in (_db_value(KEY_ROOT), _env_value(KEY_ROOT)):
        if candidate:
            return Path(candidate)
    configured = str(settings.MEDIA_ROOT or "").strip()
    if configured:
        return Path(configured)
    return default_root()


def _resolve_dir(key: str, env_name: str, leaf: str) -> Path:
    for candidate in (_db_value(key), _env_value(env_name)):
        if candidate:
            return Path(candidate)
    return _resolve_root() / leaf


def root() -> Path:
    """Media root: parent of the Video/ and Audio/ directories."""
    return _resolve_root()


def video_dir() -> Path:
    return _resolve_dir(KEY_VIDEO, "MEDIA_VIDEO_DIR", "Video")


def audio_dir() -> Path:
    return _resolve_dir(KEY_AUDIO, "MEDIA_AUDIO_DIR", "Audio")


def dir_for_mode(media_mode: str | None, audio_only: bool = False) -> Path:
    """Pick the directory for a subscription's output mode."""
    if audio_only or (media_mode or "video") == "audio":
        return audio_dir()
    return video_dir()


def snapshot() -> dict[str, str]:
    """Human-readable current values (used by the API / diagnostics)."""
    return {
        "root": str(root()),
        "video": str(video_dir()),
        "audio": str(audio_dir()),
        "source": "settings" if _db_value(KEY_ROOT) else ("env" if _env_value(KEY_ROOT) else "default"),
    }


async def load(db: Any = None) -> None:
    """Load (and seed) the media path settings.

    Safe to call repeatedly. Failures are logged and ignored: the env/config
    defaults still apply, so a broken settings table can never stop downloads.
    """
    global _loaded

    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models import Setting

    owns_session = db is None
    session = db
    try:
        if owns_session:
            session = async_session_factory()
            await session.__aenter__()

        rows = await session.execute(
            select(Setting).where(Setting.key.in_((KEY_ROOT, KEY_VIDEO, KEY_AUDIO)))
        )
        found = {s.key: s for s in rows.scalars().all()}

        for key, value, value_type, description in SEEDS:
            if key not in found:
                session.add(
                    Setting(
                        key=key,
                        value=value,
                        value_type=value_type,
                        description=description,
                        category="general",
                    )
                )
                found[key] = None  # type: ignore[assignment]

        await session.commit()

        _cache.clear()
        for key in (KEY_ROOT, KEY_VIDEO, KEY_AUDIO):
            row = found.get(key)
            _cache[key] = _clean(getattr(row, "value", "") if row else "")

        await _migrate_legacy_root(session, found)

        _loaded = True
        logger.info("Media paths resolved: %s", snapshot())
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not load media path settings, using defaults: %s", exc)
    finally:
        if owns_session and session is not None:
            await session.__aexit__(None, None, None)


async def _migrate_legacy_root(session: Any, found: dict[str, Any]) -> None:
    """Point a pre-existing database at the new default root.

    Older deployments mounted the media folder at ``/Media`` and stored that
    path in the settings table, in subscriptions and on every lesson row. Once
    the folder lives inside the data volume (``/app/data/media``) those paths
    are dead, so every ``/Media`` prefix has to be rewritten — otherwise the
    library silently shows up empty after the upgrade.
    """
    legacy = str(LEGACY_ROOT)
    target = default_root()

    current = _db_value(KEY_ROOT)
    points_at_legacy = (not current) or current == legacy or current.startswith(legacy + "/")
    if not points_at_legacy:
        return
    if Path(legacy).exists():
        # The old mount is still there; do not touch a working setup.
        return

    logger.info("Migrating media paths %s -> %s", legacy, target)

    # 1) the settings row itself
    if KEY_ROOT in found and getattr(found[KEY_ROOT], "value", None) is not None:
        found[KEY_ROOT].value = str(target)
    _cache[KEY_ROOT] = str(target)

    # 2) every stored absolute path
    replacements = (
        ("settings", "value"),
        ("media_subscriptions", "download_path"),
        ("media_download_history", "file_path"),
        ("courses", "directory"),
        ("courses", "cover_path"),
        ("lessons", "file_path"),
        ("lessons", "subtitle_path"),
        ("lessons", "cover_path"),
    )
    from sqlalchemy import text

    for table, column in replacements:
        try:
            await session.execute(
                text(
                    f"UPDATE {table} SET {column} = :new || substr({column}, :cut) "
                    f"WHERE {column} = :old OR {column} LIKE :like"
                ),
                {
                    "new": str(target),
                    "cut": len(legacy) + 1,
                    "old": legacy,
                    "like": legacy + "/%",
                },
            )
        except Exception as exc:  # table may not exist yet on a fresh install
            logger.debug("Skip %s.%s during media path migration: %s", table, column, exc)

    await session.commit()


async def update(key: str, value: str, db: Any = None) -> dict[str, str]:
    """Persist one of the media path settings and refresh the cache."""
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models import Setting

    if key not in (KEY_ROOT, KEY_VIDEO, KEY_AUDIO):
        raise ValueError(f"unsupported media setting: {key}")

    cleaned = _clean(value)
    if key == KEY_ROOT and not cleaned:
        raise ValueError("MEDIA_ROOT 不能为空")

    owns_session = db is None
    session = db
    try:
        if owns_session:
            session = async_session_factory()
            await session.__aenter__()

        result = await session.execute(select(Setting).where(Setting.key == key))
        row = result.scalar_one_or_none()
        if row is None:
            row = Setting(key=key, value=cleaned, value_type="string", category="general")
            session.add(row)
        else:
            row.value = cleaned
        await session.commit()
        _cache[key] = cleaned
    finally:
        if owns_session and session is not None:
            await session.__aexit__(None, None, None)

    return snapshot()


def ensure_dirs() -> list[str]:
    """Create the resolved directories; returns the ones created."""
    created: list[str] = []
    for path in (video_dir(), audio_dir()):
        if not path.is_dir():
            try:
                path.mkdir(parents=True, exist_ok=True)
                created.append(str(path))
            except OSError as exc:  # pragma: no cover - permission issues
                logger.warning("Cannot create media dir %s: %s", path, exc)
    return created


def is_loaded() -> bool:
    return _loaded


__all__ = [
    "LEGACY_ROOT",
    "KEY_AUDIO",
    "KEY_ROOT",
    "KEY_VIDEO",
    "audio_dir",
    "default_root",
    "dir_for_mode",
    "ensure_dirs",
    "is_loaded",
    "load",
    "root",
    "snapshot",
    "update",
    "video_dir",
]
