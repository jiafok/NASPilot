"""Media server integration — Emby / Jellyfin / Audiobookshelf.

We cannot index the libraries remotely without credentials, so this module
provides what is actually useful and safe:

* recommended library roots (auto-generated from our directory layout)
* deep-link builders so the UI can jump straight to the library page
"""

from __future__ import annotations

from urllib.parse import quote

from app.core.config import settings
from app.services.media_center import settings_store
from app.services.media_center.paths import base_root, output_dir


def _server_config() -> list[dict]:
    # Library roots follow the runtime media settings, not hardcoded paths
    video = str(settings_store.video_dir())
    audio = str(settings_store.audio_dir())
    return [
        {
            "kind": "emby",
            "label": "Emby",
            "base_url": (settings.EMBY_URL or "").strip() or None,
            "path": "/web/index.html#!/video",
            "library_paths": [video, audio],
        },
        {
            "kind": "jellyfin",
            "label": "Jellyfin",
            "base_url": (settings.JELLYFIN_URL or "").strip() or None,
            "path": "/web/index.html#!/home",
            "library_paths": [video, audio],
        },
        {
            "kind": "audiobookshelf",
            "label": "Audiobookshelf",
            "base_url": (settings.AUDIOBOOKSHELF_URL or "").strip() or None,
            "path": "/library",
            "library_paths": [audio],
        },
    ]


def describe() -> list[dict]:
    """Library suggestions + sample deep links for each configured server."""
    out: list[dict] = []
    for cfg in _server_config():
        base = cfg["base_url"]
        configured = bool(base)
        links: list[dict] = []
        if configured:
            root = base.rstrip("/")
            links.append({"label": "打开媒体库", "url": root + cfg["path"]})
            search_terms = ["English", "Podcast"]
            links.append(
                {
                    "label": "搜索媒体",
                    "url": f"{root}/web/index.html#!/search.html?query={quote(search_terms[0])}"
                    if cfg["kind"] in ("emby", "jellyfin")
                    else f"{root}/library",
                }
            )
        out.append(
            {
                "kind": cfg["kind"],
                "configured": configured,
                "base_url": base,
                "library_paths": cfg["library_paths"],
                "sample_links": links,
            }
        )
    return out


def links_for_path(file_path: str | None) -> list[dict]:
    """Deep links that filter a media server by the item's parent folder."""
    if not file_path:
        return []
    import os

    folder = os.path.dirname(file_path)
    name = quote(os.path.basename(folder) or "")
    out: list[dict] = []
    for cfg in _server_config():
        base = cfg["base_url"]
        if not base:
            continue
        root = base.rstrip("/")
        if cfg["kind"] in ("emby", "jellyfin"):
            out.append(
                {
                    "kind": cfg["kind"],
                    "label": f"在 {cfg['label']} 搜索「{os.path.basename(folder)}」",
                    "url": f"{root}/web/index.html#!/search.html?query={name}",
                }
            )
        else:
            out.append(
                {"kind": cfg["kind"], "label": f"在 {cfg['label']} 打开", "url": f"{root}/library"}
            )
    return out


def suggested_root(subscription) -> str:
    """Suggested library root for one subscription (for the UI hint)."""
    return str(base_root(subscription))


def example_tree(subscription, provider: str, channel: str, playlist: str | None) -> str:
    """Human readable example of where files will land."""
    directory = output_dir(subscription, provider, channel, playlist)
    return str(directory)


__all__ = ["describe", "example_tree", "links_for_path", "suggested_root"]
