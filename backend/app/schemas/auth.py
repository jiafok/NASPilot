"""Auth schemas — login request, token response."""

from datetime import datetime

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: "UserOut"


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
