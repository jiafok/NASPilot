"""Auth endpoints — login, refresh, logout, me."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import CurrentUser
from app.models import User
from app.schemas.auth import LoginRequest, LogoutRequest, RefreshRequest, TokenResponse, UserOut
from app.schemas.user import PasswordChange
from app.services import auth_service
from app.services.auth_service import authenticate, refresh_session, revoke_refresh_token
from app.core.security import hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse, summary="Login")
async def login(
    creds: LoginRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user_agent: Annotated[str | None, Header(alias="User-Agent")] = None,
):
    """Authenticate and return an access token + a long-lived refresh token."""
    result = await authenticate(db, creds, device=user_agent)
    if not result:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
    return result


@router.post("/refresh", response_model=TokenResponse, summary="Exchange a refresh token")
async def refresh(
    body: RefreshRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user_agent: Annotated[str | None, Header(alias="User-Agent")] = None,
):
    """Rotate a refresh token into a fresh access/refresh pair.

    Clients should call this *before* ``expires_in`` elapses. The presented
    token is invalidated immediately, so the response must be persisted.
    """
    result = await refresh_session(db, body.refresh_token, device=user_agent)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token 无效或已过期"
        )
    return result


@router.post("/logout", summary="Revoke a refresh token")
async def logout(
    body: LogoutRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Best-effort session revocation. Always succeeds, even for unknown tokens."""
    if body.refresh_token:
        await revoke_refresh_token(db, body.refresh_token)
    return {"message": "Logged out"}


@router.get("/me", response_model=UserOut, summary="Get current user")
async def me(user: CurrentUser):
    return user


@router.post("/change-password", summary="Change password")
async def change_password(
    body: PasswordChange,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if not verify_password(body.old_password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Old password incorrect")
    user.hashed_password = hash_password(body.new_password)
    # Every existing session belongs to the old password; drop them so a
    # password change actually locks other devices out.
    revoked = await auth_service.revoke_all_for_user(db, user.id)
    return {"message": "Password changed", "sessions_revoked": revoked}
