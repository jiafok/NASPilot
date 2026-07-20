"""Notification service — send messages to various channels."""

import logging
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import NotificationChannel, NotificationRecord

logger = logging.getLogger("naspilot.notification")


def _safe_json(resp: httpx.Response) -> dict[str, Any]:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


async def _send_feishu(config: dict[str, Any], title: str, message: str) -> tuple[bool, str | None]:
    webhook = config.get("webhook", "")
    if not webhook:
        return False, "No webhook configured"
    body = {
        "msg_type": "text",
        "content": {"text": f"{title}\n\n{message}"},
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(webhook, json=body)
        data = resp.json()
        if data.get("code", 0) != 0:
            return False, data.get("msg", "Unknown error")
        return True, None


async def _send_wechat_work(config: dict[str, Any], title: str, message: str) -> tuple[bool, str | None]:
    webhook = config.get("webhook", "")
    if not webhook:
        return False, "No webhook configured"
    body = {
        "msgtype": "text",
        "text": {"content": f"{title}\n\n{message}"},
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(webhook, json=body)
        data = _safe_json(resp)
        if data.get("errcode", 0) != 0:
            return False, data.get("errmsg", "Unknown error")
        return True, None


async def _send_telegram(config: dict[str, Any], title: str, message: str) -> tuple[bool, str | None]:
    token = config.get("bot_token", "")
    chat_id = config.get("chat_id", "")
    if not token or not chat_id:
        return False, "Missing bot_token or chat_id"

    text = f"{title}\n\n{message}"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True})
        data = _safe_json(resp)
        if not data.get("ok", False):
            return False, data.get("description", "Unknown error")
        return True, None


_ASYNC_SENDERS = {
    "feishu": _send_feishu,
    "wechat_work": _send_wechat_work,
    "telegram": _send_telegram,
}


async def send_notification(
    db: AsyncSession,
    channel: NotificationChannel,
    title: str,
    message: str,
    level: str = "info",
    event_type: str | None = None,
) -> NotificationRecord:
    """Send a notification through a channel and record the result."""
    record = NotificationRecord(
        channel_id=channel.id,
        channel_type=channel.channel_type,
        title=title,
        message=message,
        level=level,
        event_type=event_type,
        status="pending",
    )
    db.add(record)
    await db.flush()

    sender = _ASYNC_SENDERS.get(channel.channel_type)
    if not sender:
        record.status = "failed"
        record.error_message = f"Unsupported channel type: {channel.channel_type}"
        await db.commit()
        return record

    try:
        ok, err = await sender(channel.config, title, message)
        record.status = "sent" if ok else "failed"
        record.error_message = err
    except Exception as e:
        record.status = "failed"
        record.error_message = str(e)
        logger.exception("Notification send error")
    await db.commit()
    return record


async def notify_default_channels(
    db: AsyncSession,
    title: str,
    message: str,
    level: str = "info",
    event_type: str | None = None,
) -> list[NotificationRecord]:
    """Send to all enabled default channels."""
    result = await db.execute(
        select(NotificationChannel).where(NotificationChannel.is_default.is_(True), NotificationChannel.enabled.is_(True))
    )
    channels = result.scalars().all()
    records = []
    for ch in channels:
        rec = await send_notification(db, ch, title, message, level, event_type)
        records.append(rec)
    return records
