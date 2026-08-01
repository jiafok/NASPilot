"""Auth service — login, token generation, initial admin bootstrap."""

from datetime import datetime, timezone
import logging
import secrets
import string

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import create_access_token, hash_password, verify_password
from app.models import User
from app.schemas.auth import LoginRequest, TokenResponse, UserOut

logger = logging.getLogger("naspilot.auth")


def _generate_password(length: int = 16) -> str:
    """Generate a secure random password."""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


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
    """Create or sync initial admin user on every startup.

    Password resolution (in order):
    1. FIRST_ADMIN_PASSWORD env var — explicit password
    2. Random generation — printed ONCE to startup log
    """
    admin_password = settings.FIRST_ADMIN_PASSWORD
    password_source = "env"
    if not admin_password:
        admin_password = _generate_password()
        password_source = "random"
        logger.warning("=" * 60)
        logger.warning("FIRST_ADMIN_PASSWORD not set — generated random password:")
        logger.warning("  Username: %s", settings.INITIAL_ADMIN_USER)
        logger.warning("  Password: %s", admin_password)
        logger.warning("  Save this password! It will NOT be printed again.")
        logger.warning("=" * 60)

    result = await db.execute(select(User).where(User.username == settings.INITIAL_ADMIN_USER))
    admin = result.scalar_one_or_none()

    if admin is None:
        # First run — create admin
        admin = User(
            username=settings.INITIAL_ADMIN_USER,
            hashed_password=hash_password(admin_password),
            is_active=True,
            is_admin=True,
            display_name="Administrator",
        )
        db.add(admin)
        await db.commit()
        logger.info("Admin user created: %s (password source: %s)", settings.INITIAL_ADMIN_USER, password_source)
    elif password_source == "env" and not verify_password(admin_password, admin.hashed_password):
        # FIRST_ADMIN_PASSWORD changed in env — sync it
        admin.hashed_password = hash_password(admin_password)
        await db.commit()
        logger.info("Admin password synced from FIRST_ADMIN_PASSWORD env var")
