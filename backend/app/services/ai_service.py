"""Unified AI client for NASPilot.

Replaces the ad-hoc `httpx` call that used to live inside `api/v1/ai.py`, so
every AI feature (the ops assistant, the English tutor, future modules) shares
one implementation. No provider SDK is vendored — this is a thin OpenAI-
compatible chat client, which is what Ollama / DeepSeek / Qwen / Gemini
gateways also speak.

Credentials resolution order:

1. `settings` table (`AI_API_KEY` / `AI_BASE_URL` / `AI_MODEL`) — the values
   the admin edits in「系统设置 → AI」. Before this module existed those rows
   were saved but never read.
2. Environment (`OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

logger = logging.getLogger("naspilot.ai")

#: Keys the settings UI writes.
KEY_API_KEY = "AI_API_KEY"
KEY_BASE_URL = "AI_BASE_URL"
KEY_MODEL = "AI_MODEL"

DEFAULT_TIMEOUT = 90.0


class AIUnavailable(RuntimeError):
    """Raised when no API key is configured."""


@dataclass(frozen=True)
class AIConfig:
    api_key: str
    base_url: str
    model: str

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


def _clean(value: Any) -> str:
    return str(value or "").strip()


async def _db_settings(db: AsyncSession) -> dict[str, str]:
    """Read the AI keys from the settings table (empty dict on any failure)."""
    try:
        from app.models import Setting

        rows = await db.execute(
            select(Setting).where(Setting.key.in_((KEY_API_KEY, KEY_BASE_URL, KEY_MODEL)))
        )
        return {s.key: _clean(s.value) for s in rows.scalars().all()}
    except Exception:  # pragma: no cover - defensive (table may not exist yet)
        logger.debug("Could not read AI settings from DB", exc_info=True)
        return {}


async def resolve_config(db: AsyncSession | None = None) -> AIConfig:
    """Build the effective AI configuration (DB first, then environment)."""
    db_values: dict[str, str] = {}
    if db is not None:
        db_values = await _db_settings(db)

    api_key = db_values.get(KEY_API_KEY) or _clean(settings.OPENAI_API_KEY)
    base_url = (
        db_values.get(KEY_BASE_URL)
        or _clean(settings.OPENAI_BASE_URL)
        or "https://api.openai.com/v1"
    )
    model = db_values.get(KEY_MODEL) or _clean(settings.OPENAI_MODEL) or "gpt-4o-mini"
    return AIConfig(api_key=api_key, base_url=base_url.rstrip("/"), model=model)


async def is_configured(db: AsyncSession | None = None) -> bool:
    return (await resolve_config(db)).configured


def _messages(system: str | None, messages: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if system:
        out.append({"role": "system", "content": system})
    out.extend({"role": m["role"], "content": m["content"]} for m in messages)
    return out


async def chat(
    messages: Iterable[dict[str, str]],
    *,
    system: str | None = None,
    db: AsyncSession | None = None,
    temperature: float = 0.3,
    max_tokens: int = 1024,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """One-shot chat completion. Raises :class:`AIUnavailable` when unconfigured."""
    cfg = await resolve_config(db)
    if not cfg.configured:
        raise AIUnavailable(
            "AI 未配置。请在「系统设置 → AI」填写 API Key，"
            "或设置环境变量 OPENAI_API_KEY。"
        )

    payload = {
        "model": cfg.model,
        "messages": _messages(system, messages),
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{cfg.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {cfg.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    try:
        return str(data["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError) as exc:  # pragma: no cover - bad gateway
        raise RuntimeError(f"AI 返回格式异常: {data}") from exc


async def ask(
    question: str,
    *,
    system: str | None = None,
    db: AsyncSession | None = None,
    **kwargs: Any,
) -> str:
    """Convenience wrapper for a single user question."""
    return await chat([{"role": "user", "content": question}], system=system, db=db, **kwargs)


__all__ = [
    "AIUnavailable",
    "AIConfig",
    "KEY_API_KEY",
    "KEY_BASE_URL",
    "KEY_MODEL",
    "ask",
    "chat",
    "is_configured",
    "resolve_config",
]
