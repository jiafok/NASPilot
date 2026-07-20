"""Auth service — login, token generation, initial admin bootstrap."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import create_access_token, hash_password, verify_password
from app.models import User
from app.schemas.auth import LoginRequest, TokenResponse, UserOut


async def authenticate(db: AsyncSession, creds: LoginRequest) -> TokenResponse | None:
    """Verify credentials and return JWT token + user info."""
    result = await db.execute(select(User).where(User.username == creds.username))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        return None
    if not verify_password(creds.password, user.hashed_password):
        return None
    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()
    token = create_access_token(user.id, {"username": user.username, "admin": user.is_admin})
    return TokenResponse(
        access_token=token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserOut.model_validate(user),
    )


async def bootstrap_admin(db: AsyncSession) -> None:
    """Create or sync initial admin user on every startup."""
    result = await db.execute(select(User).where(User.username == settings.INITIAL_ADMIN_USER))
    admin = result.scalar_one_or_none()

    if admin is None:
        # First run — create admin
        admin = User(
            username=settings.INITIAL_ADMIN_USER,
            hashed_password=hash_password(settings.INITIAL_ADMIN_PASSWORD),
            is_active=True,
            is_admin=True,
            display_name="Administrator",
        )
        db.add(admin)
        await db.commit()
    elif not verify_password(settings.INITIAL_ADMIN_PASSWORD, admin.hashed_password):
        # ADMIN_PASSWORD changed in env — sync it
        admin.hashed_password = hash_password(settings.INITIAL_ADMIN_PASSWORD)
        await db.commit()
