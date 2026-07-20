"""Auth endpoints — login, logout, me."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import CurrentUser
from app.models import User
from app.schemas.auth import LoginRequest, TokenResponse, UserOut
from app.schemas.user import PasswordChange
from app.services.auth_service import authenticate
from app.core.security import hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse, summary="Login")
async def login(creds: LoginRequest, db: Annotated[AsyncSession, Depends(get_db)]):
    """Authenticate and return a JWT bearer token."""
    result = await authenticate(db, creds)
    if not result:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
    return result


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
    await db.commit()
    return {"message": "Password changed"}
