"""One-use registration codes; codes and credentials never leave the sender."""

import asyncio
import hashlib
import hmac
import json
import re
import secrets
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import begin_write
from app.models import RegisteredPhone, SmsChallenge
from app.services.membership_service import now_utc, settings_for, utc


def normalize_phone(value: str) -> str:
    value = re.sub(r"[\s()\-]", "", value)
    if value.startswith("0086"):
        value = value[4:]
    elif value.startswith("+86"):
        value = value[3:]
    if not re.fullmatch(r"1[3-9][0-9]{9}", value):
        raise HTTPException(status_code=422, detail="请输入有效的中国大陆手机号（+86）")
    return "+86" + value


def _digest(value: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()


async def dispatch_sms(config, phone: str, code: str) -> None:
    def send():
        from alibabacloud_dysmsapi20170525.client import Client
        from alibabacloud_dysmsapi20170525.models import SendSmsRequest
        from alibabacloud_tea_openapi.models import Config
        from darabonba.runtime import RuntimeOptions

        client = Client(Config(access_key_id=config.sms_access_key_id.strip(), access_key_secret=config.sms_access_key_secret,
                               endpoint="dysmsapi.aliyuncs.com", connect_timeout=5000, read_timeout=10000))
        response = client.send_sms_with_options(
            SendSmsRequest(phone_numbers=phone[3:], sign_name=config.sms_sign_name.strip(),
                           template_code=config.sms_template_code.strip(), template_param=json.dumps({"code": code})),
            RuntimeOptions(autoretry=False, connect_timeout=5000, read_timeout=10000),
        )
        if response.body is None or response.body.code != "OK":
            raise RuntimeError("SMS provider rejected request")

    await asyncio.to_thread(send)


async def issue_code(db: AsyncSession, phone: str, ip: str) -> dict:
    phone = normalize_phone(phone)
    config = await settings_for(db)
    if not config.sms_enabled or not all((config.sms_access_key_id, config.sms_access_key_secret, config.sms_sign_name, config.sms_template_code)):
        raise HTTPException(status_code=503, detail="短信注册尚未配置，请联系管理员")
    await begin_write(db)
    if await db.get(RegisteredPhone, phone):
        await db.rollback()
        raise HTTPException(status_code=409, detail="该手机号已注册，不能重复领取试用")
    now = now_utc()
    ip_hash = _digest("ip:" + ip)
    recent = (await db.execute(select(func.count()).select_from(SmsChallenge).where(
        or_(SmsChallenge.phone == phone, SmsChallenge.ip_hash == ip_hash), SmsChallenge.sent_at > now - timedelta(seconds=60),
    ))).scalar() or 0
    phone_daily = (await db.execute(select(func.count()).select_from(SmsChallenge).where(
        SmsChallenge.phone == phone, SmsChallenge.sent_at > now - timedelta(days=1),
    ))).scalar() or 0
    ip_daily = (await db.execute(select(func.count()).select_from(SmsChallenge).where(
        SmsChallenge.ip_hash == ip_hash, SmsChallenge.sent_at > now - timedelta(days=1),
    ))).scalar() or 0
    if recent or phone_daily >= 5 or ip_daily >= 30:
        await db.rollback()
        raise HTTPException(status_code=429, detail="发送过于频繁，请稍后再试", headers={"Retry-After": "60"})
    identifier = uuid4().hex
    code = f"{secrets.randbelow(1000000):06d}"
    db.add(SmsChallenge(id=identifier, phone=phone, ip_hash=ip_hash, code_hash=_digest(identifier + ":" + code),
                        sent_at=now, expires_at=now + timedelta(minutes=5), state="pending", attempts=0))
    await db.commit()
    try:
        await dispatch_sms(config, phone, code)
    except Exception:
        await begin_write(db)
        await db.execute(update(SmsChallenge).where(SmsChallenge.id == identifier).values(state="failed"))
        await db.commit()
        raise HTTPException(status_code=503, detail="短信发送失败，请联系管理员检查签名、模板或短信额度") from None
    await begin_write(db)
    await db.execute(update(SmsChallenge).where(SmsChallenge.id == identifier).values(state="sent"))
    await db.commit()
    return {"message": "验证码已发送", "retry_after": 60, "expires_in": 300}


async def consume_code(db: AsyncSession, phone: str, code: str) -> str:
    phone = normalize_phone(phone)
    if await db.get(RegisteredPhone, phone):
        raise HTTPException(status_code=409, detail="该手机号已注册，不能重复领取试用")
    challenge = (await db.execute(select(SmsChallenge).where(SmsChallenge.phone == phone).order_by(SmsChallenge.sent_at.desc()).limit(1))).scalar_one_or_none()
    if challenge is None or challenge.state != "sent" or challenge.used_at or utc(challenge.expires_at) <= now_utc() or challenge.attempts >= 5:
        raise HTTPException(status_code=400, detail="验证码无效或已过期，请重新获取")
    if not hmac.compare_digest(challenge.code_hash, _digest(challenge.id + ":" + code)):
        challenge.attempts += 1
        await db.commit()
        raise HTTPException(status_code=400, detail="验证码不正确")
    challenge.used_at = now_utc()
    return phone