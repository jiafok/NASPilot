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

import json
import logging
from dataclasses import dataclass
from collections.abc import AsyncIterator
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
_CHAT_COMPLETIONS_SUFFIX = "/chat/completions"


def _chat_completions_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.lower().endswith(_CHAT_COMPLETIONS_SUFFIX):
        return base
    return f"{base}{_CHAT_COMPLETIONS_SUFFIX}"


def _describe_http_error(status: int, body: str) -> str:
    """Turn a provider HTTP error into one actionable line.

    The provider's own message is what separates "API key invalid" from "model
    not available", so it has to reach the UI instead of a bare
    ``Client error '401 Unauthorized'``.
    """
    message = ""
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or "").strip()
            message = str(error.get("message") or "").strip()
            if code and message:
                message = f"{message}（code {code}）"
            elif not message:
                message = code
        elif isinstance(error, str):
            message = error
        if not message:
            message = str(payload.get("message") or payload.get("msg") or "")
    if not message:
        message = (body or "").strip()[:200] or "无响应内容"

    hints = {
        401: "API Key 无效或已过期（智谱 Key 形如 id.secret，约 49 位，不要只粘贴前半段）",
        403: "API Key 无权访问该模型",
        404: "接口路径或模型不存在（Base URL 不要附加 /chat/completions）",
        429: "触发限流或额度用尽",
    }
    hint = hints.get(status)
    headline = f"HTTP {status} —— {hint}" if hint else f"HTTP {status}"
    return f"{headline}；服务端返回：{message}"


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


#: Strings a JS front-end sends when a form field was never filled in. They are
#: stored verbatim, and treating them as real values made `is_configured()` say
#: yes while every call failed with "URL is missing an http:// protocol".
_SENTINELS = {"undefined", "null", "none", "nan", "false", "0"}


def _clean(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in _SENTINELS else text


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

    api_key = _clean(db_values.get(KEY_API_KEY)) or _clean(settings.OPENAI_API_KEY)
    base_url = (
        _clean(db_values.get(KEY_BASE_URL))
        or _clean(settings.OPENAI_BASE_URL)
        or "https://api.openai.com/v1"
    )
    model = _clean(db_values.get(KEY_MODEL)) or _clean(settings.OPENAI_MODEL) or "gpt-4o-mini"
    if not base_url.startswith(("http://", "https://")):
        # A stored value without a scheme can never work; fall back rather than
        # failing every request with UnsupportedProtocol.
        base_url = "https://api.openai.com/v1"
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
            _chat_completions_url(cfg.base_url),
            headers={
                "Authorization": f"Bearer {cfg.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if resp.status_code >= 400:
            raise RuntimeError(_describe_http_error(resp.status_code, resp.text))
        data = resp.json()

    try:
        return str(data["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError) as exc:  # pragma: no cover - bad gateway
        raise RuntimeError(f"AI 返回格式异常: {data}") from exc


async def stream_chat(
    messages: Iterable[dict[str, str]],
    *,
    system: str | None = None,
    db: AsyncSession | None = None,
    temperature: float = 0.3,
    max_tokens: int = 1024,
    timeout: float = DEFAULT_TIMEOUT,
    config: "AIConfig | None" = None,
) -> AsyncIterator[str]:
    """Yield answer fragments as the model produces them.

    The non-streaming ``chat`` leaves the UI on a blank screen for the whole
    generation (3-15s for a tutor answer), which reads as a freeze. Server-sent
    events fix that by showing the first words within a second.

    A provider that ignores ``stream=true`` still works: the whole body comes
    back as one JSON document, which is emitted as a single fragment.

    ``config`` lets the caller resolve settings *before* it releases its
    database session: a streaming response outlives the request handler, and
    the session is not usable inside the generator.
    """
    cfg = config or await resolve_config(db)
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
        "stream": True,
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "POST",
            _chat_completions_url(cfg.base_url),
            headers={
                "Authorization": f"Bearer {cfg.api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            json=payload,
        ) as resp:
            if resp.status_code >= 400:
                # A streaming body has not been read yet, and the provider
                # explains the failure in it.
                await resp.aread()
                raise RuntimeError(_describe_http_error(resp.status_code, resp.text))
            raw_lines: list[str] = []
            async for line in resp.aiter_lines():
                if not line:
                    continue
                chunk = line.strip()
                if chunk.startswith("data:"):
                    chunk = chunk[5:].strip()
                if chunk == "[DONE]":
                    break
                if not chunk:
                    continue
                if not chunk.startswith("{"):
                    # Provider ignored stream=true and sent plain JSON.
                    raw_lines.append(chunk)
                    continue
                try:
                    data = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                text = _delta_text(data)
                if text:
                    yield text

            if raw_lines:
                try:
                    data = json.loads("\n".join(raw_lines))
                except json.JSONDecodeError:
                    data = None
                if isinstance(data, dict):
                    text = _delta_text(data, allow_full=True)
                    if text:
                        yield text


def _delta_text(data: dict[str, Any], *, allow_full: bool = False) -> str:
    """Pull the incremental text out of one streaming chunk."""
    try:
        choice = data["choices"][0]
    except (KeyError, IndexError, TypeError):
        return ""
    if not isinstance(choice, dict):
        return ""
    delta = choice.get("delta")
    if isinstance(delta, dict) and isinstance(delta.get("content"), str):
        return delta["content"]
    if allow_full:
        message = choice.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return message["content"]
    return ""


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
    "stream_chat",
    "is_configured",
    "resolve_config",
]
