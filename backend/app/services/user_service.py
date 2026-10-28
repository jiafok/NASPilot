"""Administrator account creation and deletion."""

import asyncio

from fastapi import HTTPException
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import begin_write
from app.core.security import hash_password
from app.models import RegisteredPhone, RegistrationInvite, Setting, User
from app.schemas.auth import AccountRequest, RegisterRequest, UserOut
from app.schemas.user import UserCreate
from app.services import invitation_service, membership_service, sms_service


_USER_ID_HIGH_WATER = "AUTH_USER_ID_HIGH_WATER"


async def _reserve_user_id(db: AsyncSession, minimum: int = 0) -> int:
    highest = (await db.execute(select(func.max(User.id)))).scalar() or 0
    counter = await db.get(Setting, _USER_ID_HIGH_WATER)
    next_id = max(highest, int(counter.value) if counter else 0, minimum) + 1
    if counter is None:
        db.add(Setting(key=_USER_ID_HIGH_WATER, value=str(next_id), value_type="int", category="internal"))
    else:
        counter.value = str(next_id)
    return next_id


async def create_user(db: AsyncSession, body: AccountRequest | UserCreate, *, admin: bool = False) -> User:
    hashed_password = await asyncio.to_thread(hash_password, body.password)
    await begin_write(db)
    if (await db.execute(select(User.id).where(User.username == body.username))).scalar_one_or_none() is not None:
        await db.rollback()
        raise HTTPException(status_code=409, detail="用户名已存在")
    email = body.email if isinstance(body, UserCreate) else None
    if email and (await db.execute(select(User.id).where(User.email == email))).scalar_one_or_none() is not None:
        await db.rollback()
        raise HTTPException(status_code=409, detail="邮箱已被使用")
    phone = None
    invite = None
    if isinstance(body, RegisterRequest):
        if body.invitation_code:
            invite = await invitation_service.consume(db, body.invitation_code)
        else:
            if not body.phone or not body.verification_code:
                raise HTTPException(status_code=400, detail="请提供手机号与验证码")
            phone = await sms_service.consume_code(db, body.phone, body.verification_code)
    user = User(
        id=await _reserve_user_id(db),
        username=body.username,
        display_name=(body.display_name or body.username).strip(),
        email=email or None,
        hashed_password=hashed_password,
        is_admin=admin,
        is_active=True,
    )
    db.add(user)
    try:
        await db.flush()
        if invite is not None:
            invite.used_by = user.id
            invite.used_by_username = user.username
        if phone:
            db.add(RegisteredPhone(phone=phone, user_id=user.id, verified_at=membership_service.now_utc()))
        if not admin:
            db.add(membership_service.trial_for(user.id))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="用户名或邮箱已存在") from None
    await db.refresh(user)
    return user


async def delete_user(db: AsyncSession, user_id: int, actor_id: int) -> None:
    if user_id == actor_id:
        raise HTTPException(status_code=400, detail="不能删除当前登录的账号")
    await begin_write(db)
    user = await db.get(User, user_id)
    if user is None:
        await db.rollback()
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.is_admin and user.is_active:
        count = (await db.execute(select(func.count()).select_from(User).where(User.is_admin.is_(True), User.is_active.is_(True)))).scalar() or 0
        if count <= 1:
            await db.rollback()
            raise HTTPException(status_code=400, detail="不能删除最后一个有效管理员")
    await _reserve_user_id(db, minimum=user.id)
    await db.execute(delete(RegistrationInvite).where(RegistrationInvite.used_by == user_id))
    await db.execute(delete(User).where(User.id == user_id))
    await db.commit()


async def list_users(db: AsyncSession, *, q: str = "", page: int = 1, page_size: int = 20) -> dict:
    statement = select(User)
    if q.strip():
        value = f"%{q.strip()}%"
        statement = statement.where(or_(User.username.ilike(value), User.display_name.ilike(value), User.email.ilike(value)))
    total = (await db.execute(select(func.count()).select_from(statement.subquery()))).scalar() or 0
    rows = (await db.execute(statement.order_by(User.id.asc()).offset((page - 1) * page_size).limit(page_size))).scalars().all()
    items = []
    for user in rows:
        phone = (await db.execute(select(RegisteredPhone.phone).where(RegisteredPhone.user_id == user.id))).scalar_one_or_none()
        items.append({**UserOut.model_validate(user).model_dump(),
                      "phone": phone[:6] + "****" + phone[-4:] if phone else None,
                      "membership": await membership_service.status_for(db, user)})
    return {"items": items, "total": total, "page": page, "page_size": page_size}