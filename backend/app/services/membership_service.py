"""Server-enforced learning access and administrator-confirmed plan grants."""

import math
from datetime import datetime, timedelta, timezone

from dateutil.relativedelta import relativedelta
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import begin_write
from app.models import LearningMembership, MembershipGrant, Setting, User
from app.schemas.membership import AccessSettings, GrantRequest


CONFIG_KEY = "LEARNING_ACCESS_CONFIG"
PLAN_LABELS = {"monthly": "包月", "quarterly": "包季", "yearly": "包年", "lifetime": "永久"}
PLAN_MONTHS = {"monthly": 1, "quarterly": 3, "yearly": 12}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def settings_for(db: AsyncSession) -> AccessSettings:
    row = await db.get(Setting, CONFIG_KEY)
    return AccessSettings.model_validate_json(row.value) if row else AccessSettings()


async def admin_settings(db: AsyncSession) -> dict:
    config = await settings_for(db)
    payload = config.model_dump(mode="json", exclude={"sms_access_key_secret", "clear_sms_secret"})
    payload.update({"sms_secret_configured": bool(config.sms_access_key_secret), "trial_days": 3, "sms_provider": "aliyun", "payment_mode": "manual"})
    return payload


async def save_settings(db: AsyncSession, body: AccessSettings) -> dict:
    await begin_write(db)
    current = await settings_for(db)
    secret = None if body.clear_sms_secret else (body.sms_access_key_secret or current.sms_access_key_secret)
    body = body.model_copy(update={"sms_access_key_secret": secret, "clear_sms_secret": False})
    if body.sms_enabled and not all((body.sms_access_key_id.strip(), secret, body.sms_sign_name.strip(), body.sms_template_code.strip())):
        await db.rollback()
        raise HTTPException(status_code=400, detail="启用短信前请完整配置阿里云 AccessKey、短信签名和验证码模板")
    row = await db.get(Setting, CONFIG_KEY)
    if row is None:
        row = Setting(key=CONFIG_KEY, value="", value_type="json", category="access", is_public=False)
        db.add(row)
    row.value = body.model_dump_json()
    await db.commit()
    return await admin_settings(db)


async def catalog(db: AsyncSession) -> dict:
    config = await settings_for(db)
    return {
        "trial_days": 3, "phone_region": "+86", "sms_ready": bool(config.sms_enabled and config.sms_access_key_secret),
        "invite_enabled": config.invite_enabled,
        "payment_mode": "manual", "currency": "CNY", "contact_text": config.contact_text, "contact_url": config.contact_url,
        "plans": [{"id": key, "name": PLAN_LABELS[key], "enabled": value.enabled, "price": str(value.price)} for key, value in config.plans.items()],
    }


async def status_for(db: AsyncSession, user: User) -> dict:
    if user.is_admin:
        return {"plan": "admin", "can_learn": True, "expires_at": None, "days_remaining": None}
    membership = await db.get(LearningMembership, user.id)
    if membership is None:
        return {"plan": "legacy", "can_learn": True, "expires_at": None, "days_remaining": None}
    expiry = utc(membership.expires_at) if membership.expires_at else None
    remaining = (expiry - now_utc()).total_seconds() if expiry else None
    return {
        "plan": membership.plan, "can_learn": remaining is None or remaining > 0,
        "expires_at": expiry, "days_remaining": max(0, math.ceil(remaining / 86400)) if remaining is not None else None,
    }


async def require_access(db: AsyncSession, user: User) -> None:
    if not (await status_for(db, user))["can_learn"]:
        raise HTTPException(status_code=402, detail="学习权限已到期，请联系管理员开通套餐")


def trial_for(user_id: int) -> LearningMembership:
    now = now_utc()
    return LearningMembership(user_id=user_id, plan="trial", starts_at=now, expires_at=now + timedelta(days=3))


async def grant(db: AsyncSession, user_id: int, actor: User, body: GrantRequest) -> dict:
    await begin_write(db)
    user = await db.get(User, user_id)
    if user is None:
        await db.rollback()
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.is_admin:
        await db.rollback()
        raise HTTPException(status_code=400, detail="管理员不需要开通套餐")
    config = await settings_for(db)
    price = config.plans[body.plan]
    if not price.enabled:
        await db.rollback()
        raise HTTPException(status_code=400, detail="该套餐尚未启用，请先在系统设置维护套餐价格")
    membership = await db.get(LearningMembership, user_id)
    if membership and membership.plan == "lifetime" and body.plan != "lifetime":
        await db.rollback()
        raise HTTPException(status_code=400, detail="该用户已拥有永久权限，无需续期")
    now = now_utc()
    start = max(now, utc(membership.expires_at)) if membership and membership.expires_at else now
    expiry = None if body.plan == "lifetime" else start + relativedelta(months=PLAN_MONTHS[body.plan])
    if membership is None:
        membership = LearningMembership(user_id=user.id, plan=body.plan, starts_at=now, expires_at=expiry)
        db.add(membership)
    else:
        membership.plan = body.plan
        membership.starts_at = now
        membership.expires_at = expiry
    db.add(MembershipGrant(user_id=user.id, actor_id=actor.id, username=user.username, plan=body.plan,
                           amount_cents=int(price.price * 100), granted_at=now, expires_at=expiry, note=body.note))
    await db.commit()
    return await status_for(db, user)