"""User schemas."""

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.auth import UserOut


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=6, max_length=128)
    email: str | None = None
    display_name: str | None = None
    is_admin: bool = False


class UserUpdate(BaseModel):
    email: str | None = None
    display_name: str | None = None
    avatar_url: str | None = None
    is_active: bool | None = None
    is_admin: bool | None = None
    preferences: dict | None = None


class PasswordChange(BaseModel):
    old_password: str
    new_password: str = Field(..., min_length=6, max_length=128)


__all__ = ["UserOut", "UserCreate", "UserUpdate", "PasswordChange"]
