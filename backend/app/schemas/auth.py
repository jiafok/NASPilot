"""Auth schemas — login request, token response."""

from datetime import datetime

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    #: Seconds until ``access_token`` expires (clients refresh before this).
    expires_in: int
    #: Long-lived credential for ``POST /auth/refresh``. Rotated on every use.
    refresh_token: str | None = None
    #: Seconds until ``refresh_token`` expires.
    refresh_expires_in: int | None = None
    user: "UserOut"


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=8, max_length=256)


class LogoutRequest(BaseModel):
    #: Optional so a client can log out an expired session without holding one.
    refresh_token: str | None = Field(None, max_length=256)


class UserOut(BaseModel):
    id: int
    username: str
    email: str | None = None
    display_name: str | None = None
    avatar_url: str | None = None
    is_active: bool
    is_admin: bool
    last_login_at: datetime | None = None

    model_config = {"from_attributes": True}


TokenResponse.model_rebuild()
