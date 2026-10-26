"""Runtime-configurable media paths.

The download directories used to be env-only, which meant a NAS user had to
edit `docker-compose.yml` (and recreate the container) just to change where
files land. This module makes the root a *setting* instead, so it can be
changed from the UI / API while the container keeps running.

Resolution order, highest priority first::

    per-directory override   DB setting MEDIA_VIDEO_DIR / MEDIA_AUDIO_DIR
                             (falls back to the same-named env var)
    root                     DB setting MEDIA_ROOT
                             (falls back to env MEDIA_ROOT, then /Media)
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

#: Default container-side root; `Video/` and `Audio/` live underneath it.
DEFAULT_ROOT = Path("/Media")

#: Seeded rows so the values are editable in the UI without SQL.
#: (key, value, value_type, description)
SEEDS: tuple[tuple[str, str, str, str], ...] = (
    (
        KEY_ROOT,
        str(DEFAULT_ROOT),
        "string",
        "媒体下载根目录（容器内路径）。其下自动使用 Video/ 与 Audio/ 两个子目录，"
        "对应宿主机挂载的目录。改这里即可换存储位置，无需改 compose。",
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
    return Path(settings.MEDIA_ROOT)


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
        _loaded = True
        logger.info("Media paths resolved: %s", snapshot())
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not load media path settings, using defaults: %s", exc)
    finally:
        if owns_session and session is not None:
            await session.__aexit__(None, None, None)


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
    "DEFAULT_ROOT",
    "KEY_AUDIO",
    "KEY_ROOT",
    "KEY_VIDEO",
    "audio_dir",
    "dir_for_mode",
    "ensure_dirs",
    "is_loaded",
    "load",
    "root",
    "snapshot",
    "update",
    "video_dir",
]
