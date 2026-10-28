"""Administrator-generated one-use learner registration invitations."""

import hashlib
import hmac
import secrets
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import begin_write
from app.models import RegistrationInvite
from app.services.membership_service import now_utc, settings_for, utc


def invitation_hash(code: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), ("invite:" + code.strip().upper()).encode(), hashlib.sha256).hexdigest()


async def generate(db: AsyncSession, actor_id: int, valid_days: int) -> dict:
    code = "EF-" + secrets.token_hex(16).upper()
    now = now_utc()
    invite = RegistrationInvite(id=uuid4().hex, code_hash=invitation_hash(code), code_hint=code[:8] + "..." + code[-4:],
                                created_by=actor_id, created_at=now, expires_at=now + timedelta(days=valid_days))
    await begin_write(db)
    db.add(invite)
    await db.commit()
    return {"id": invite.id, "code": code, "expires_at": invite.expires_at}


async def list_invites(db: AsyncSession, page: int, page_size: int) -> dict:
    from sqlalchemy import func

    total = (await db.execute(select(func.count()).select_from(RegistrationInvite))).scalar() or 0
    rows = (await db.execute(select(RegistrationInvite).order_by(RegistrationInvite.created_at.desc(), RegistrationInvite.id)
                            .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    now = now_utc()
    return {"total": total, "items": [
        {"id": row.id, "code_hint": row.code_hint, "created_at": utc(row.created_at), "expires_at": utc(row.expires_at),
         "used_by_username": row.used_by_username, "used_at": utc(row.used_at) if row.used_at else None,
         "status": ("used" if row.used_by is not None else "orphaned") if row.used_at else "revoked" if row.revoked_at else "expired" if utc(row.expires_at) <= now else "available"}
        for row in rows
    ]}


async def consume(db: AsyncSession, code: str) -> RegistrationInvite:
    config = await settings_for(db)
    if not config.invite_enabled:
        raise HTTPException(status_code=403, detail="邀请码注册暂未开放，请联系管理员")
    invite = (await db.execute(select(RegistrationInvite).where(RegistrationInvite.code_hash == invitation_hash(code)))).scalar_one_or_none()
    if invite is None or invite.used_at or invite.revoked_at or utc(invite.expires_at) <= now_utc():
        raise HTTPException(status_code=400, detail="邀请码无效、已使用或已过期，请联系管理员")
    invite.used_at = now_utc()
    return invite


async def delete_invite(db: AsyncSession, identifier: str) -> None:
    await begin_write(db)
    invite = await db.get(RegistrationInvite, identifier)
    if invite is None:
        await db.rollback()
        raise HTTPException(status_code=404, detail="邀请码不存在")
    if invite.used_at and invite.used_by is not None:
        await db.rollback()
        raise HTTPException(status_code=400, detail="邀请码已使用，请删除对应账号后清理记录")
    await db.delete(invite)
    await db.commit()