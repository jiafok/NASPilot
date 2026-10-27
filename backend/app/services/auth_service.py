"""Auth service — login, token generation, initial admin bootstrap."""

from datetime import datetime, timezone
import logging
import secrets
import string

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import (
    create_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    refresh_token_expiry,
    verify_password,
)
from app.models import RefreshToken, User
from app.schemas.auth import LoginRequest, TokenResponse, UserOut

logger = logging.getLogger("naspilot.auth")


def _generate_password(length: int = 16) -> str:
    """Generate a secure random password."""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def _issue_refresh_token(
    db: AsyncSession, user_id: int, *, device: str | None = None
) -> tuple[str, RefreshToken]:
    """Create and persist a refresh token, returning the raw value once."""
    raw, hashed = new_refresh_token()
    row = RefreshToken(
        user_id=user_id,
        token_hash=hashed,
        expires_at=refresh_token_expiry(),
        device=(device or "")[:128] or None,
    )
    db.add(row)
    return raw, row


def _as_token_response(user: User, raw_refresh: str) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(
            user.id, {"username": user.username, "admin": user.is_admin}
        ),
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        refresh_token=raw_refresh,
        refresh_expires_in=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        user=UserOut.model_validate(user),
    )


async def authenticate(
    db: AsyncSession, creds: LoginRequest, *, device: str | None = None
) -> TokenResponse | None:
    """Verify credentials and return an access token plus a refresh token."""
    result = await db.execute(select(User).where(User.username == creds.username))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        return None
    if not verify_password(creds.password, user.hashed_password):
        return None
    user.last_login_at = datetime.now(timezone.utc)
    raw, _row = await _issue_refresh_token(db, user.id, device=device)
    await db.commit()
    return _as_token_response(user, raw)


async def refresh_session(
    db: AsyncSession, refresh_token: str, *, device: str | None = None
) -> TokenResponse | None:
    """Exchange a refresh token for a new access/refresh pair (rotating).

    Rotation means the presented token is revoked in the same transaction that
    creates its replacement, so a leaked token is usable at most once. A
    revoked token being presented again is therefore a signal that two parties
    hold the same credential; all that user's sessions are dropped.
    """
    from app.core.database import begin_write

    await begin_write(db)
    digest = hash_refresh_token(refresh_token)
    row = (
        await db.execute(select(RefreshToken).where(RefreshToken.token_hash == digest))
    ).scalar_one_or_none()
    if row is None:
        return None

    now = datetime.now(timezone.utc)
    expires = row.expires_at
    if expires is not None and expires.tzinfo is None:
        # SQLite has no timezone type; stored values are UTC wall clock.
        expires = expires.replace(tzinfo=timezone.utc)

    if row.revoked_at is not None:
        # Reuse of an already-rotated token: assume compromise.
        await db.execute(
            RefreshToken.__table__.update()
            .where(RefreshToken.user_id == row.user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        await db.commit()
        logger.warning("Refresh token reuse detected for user %s — all sessions revoked", row.user_id)
        return None

    if expires is None or expires <= now:
        row.revoked_at = now
        await db.commit()
        return None

    user = await db.get(User, row.user_id)
    if user is None or not user.is_active:
        return None

    row.revoked_at = now
    row.last_used_at = now
    raw, _new = await _issue_refresh_token(db, user.id, device=device or row.device)
    await db.commit()
    return _as_token_response(user, raw)


async def revoke_refresh_token(db: AsyncSession, refresh_token: str) -> bool:
    """Revoke one refresh token. Returns True when a live token was revoked."""
    from app.core.database import begin_write

    await begin_write(db)
    digest = hash_refresh_token(refresh_token)
    row = (
        await db.execute(select(RefreshToken).where(RefreshToken.token_hash == digest))
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        await db.commit()
        return False
    row.revoked_at = datetime.now(timezone.utc)
    await db.commit()
    return True


async def revoke_all_for_user(db: AsyncSession, user_id: int) -> int:
    """Drop every live session for a user (used after a password change)."""
    result = await db.execute(
        RefreshToken.__table__.update()
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )
    await db.commit()
    return int(result.rowcount or 0)


async def bootstrap_admin(db: AsyncSession) -> None:
    """Create or sync initial admin user on every startup.

    Password resolution (in order):
    1. FIRST_ADMIN_PASSWORD env var — explicit password (synced on change)
    2. Random generation — only when admin does NOT exist yet (first run)

    If the admin already exists and FIRST_ADMIN_PASSWORD is empty, the existing
    password is left untouched and no random password is generated/printed.
    """
    result = await db.execute(select(User).where(User.username == settings.INITIAL_ADMIN_USER))
    admin = result.scalar_one_or_none()

    admin_password = settings.FIRST_ADMIN_PASSWORD

    if admin is None:
        # First run — create admin
        password_source = "env" if admin_password else "random"
        if not admin_password:
            admin_password = _generate_password()
            logger.warning("=" * 60)
            logger.warning("FIRST_ADMIN_PASSWORD not set — generated random password:")
            logger.warning("  Username: %s", settings.INITIAL_ADMIN_USER)
            logger.warning("  Password: %s", admin_password)
            logger.warning("  Save this password! It will NOT be printed again.")
            logger.warning("=" * 60)

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
    elif admin_password and not verify_password(admin_password, admin.hashed_password):
        # FIRST_ADMIN_PASSWORD changed in env — sync it
        admin.hashed_password = hash_password(admin_password)
        await db.commit()
        logger.info("Admin password synced from FIRST_ADMIN_PASSWORD env var")
