from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator


PlanName = Literal["monthly", "quarterly", "yearly", "lifetime"]


class PlanPrice(BaseModel):
    enabled: bool = False
    price: Decimal = Field(Decimal("0.00"), ge=0, le=999999, decimal_places=2)


class AccessSettings(BaseModel):
    invite_enabled: bool = True
    sms_enabled: bool = False
    sms_access_key_id: str = Field("", max_length=128)
    sms_access_key_secret: str | None = Field(None, max_length=256)
    clear_sms_secret: bool = False
    sms_sign_name: str = Field("", max_length=100)
    sms_template_code: str = Field("", max_length=100)
    contact_text: str = Field("", max_length=500)
    contact_url: str = Field("", max_length=1000)
    plans: dict[PlanName, PlanPrice] = Field(default_factory=lambda: {
        "monthly": PlanPrice(), "quarterly": PlanPrice(), "yearly": PlanPrice(), "lifetime": PlanPrice(),
    })

    @field_validator("plans")
    @classmethod
    def all_plans_present(cls, value):
        if set(value) != {"monthly", "quarterly", "yearly", "lifetime"}:
            raise ValueError("必须配置月、季、年和永久四种套餐")
        return value

    @field_validator("contact_url")
    @classmethod
    def safe_contact_url(cls, value: str) -> str:
        value = value.strip()
        if value and not value.startswith(("https://", "http://")):
            raise ValueError("联系链接必须以 http:// 或 https:// 开头")
        return value


class GrantRequest(BaseModel):
    plan: PlanName
    confirmed: Literal[True]
    note: str = Field("", max_length=500)


class InviteRequest(BaseModel):
    valid_days: int = Field(7, ge=1, le=365)