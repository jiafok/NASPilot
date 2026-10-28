"""Auth schemas — login request, token response."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class AccountRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
    password: str = Field(..., min_length=8, max_length=64)
    display_name: str | None = Field(None, max_length=128)

    model_config = {"extra": "forbid"}

    @field_validator("password")
    @classmethod
    def validate_password_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("密码的 UTF-8 编码长度不能超过 72 字节")
        return value


class RegisterRequest(AccountRequest):
    phone: str | None = Field(None, min_length=11, max_length=20)
    verification_code: str | None = Field(None, pattern=r"^[0-9]{6}$")
    invitation_code: str | None = Field(None, min_length=16, max_length=128)

    @model_validator(mode="after")
    def registration_credential(self):
        if self.invitation_code:
            if self.phone or self.verification_code:
                raise ValueError("请选择邀请码或手机号中的一种注册方式")
        elif not self.phone or not self.verification_code:
            raise ValueError("请提供邀请码，或手机号与短信验证码")
        return self


class SmsCodeRequest(BaseModel):
    phone: str = Field(..., min_length=11, max_length=20)


class MembershipStatus(BaseModel):
    plan: str
    can_learn: bool
    expires_at: datetime | None = None
    days_remaining: int | None = None


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
    membership: MembershipStatus | None = None
    phone: str | None = None

    model_config = {"from_attributes": True}


TokenResponse.model_rebuild()
