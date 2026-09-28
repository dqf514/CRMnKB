from pydantic import BaseModel, Field, field_validator

# 中国大陆手机号格式（为后续手机号登录/微信绑定预留的账号标识）
CN_MOBILE_PATTERN = r"^1[3-9]\d{9}$"


def _normalize_phone(v: object) -> object:
    """空串/纯空白归一为 None（清除手机号），其余去首尾空格。"""
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


class UserOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    tenant_id: int
    username: str
    name: str
    role: str
    email: str | None = None
    phone: str | None = None
    avatar_url: str | None = None


class LoginRequest(BaseModel):
    username: str
    password: str
    # 同步 App 等长期在线客户端：true 时签发 30 天长效 token（默认 12 小时）
    long_lived: bool = False


class SsoExchangeRequest(BaseModel):
    code: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class ProfileUpdate(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = Field(None, pattern=CN_MOBILE_PATTERN)
    avatar_url: str | None = None

    _norm_phone = field_validator("phone", mode="before")(_normalize_phone)


class PasswordChange(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8)


class SmsCodeRequest(BaseModel):
    """发送短信登录验证码。"""

    phone: str = Field(pattern=CN_MOBILE_PATTERN)


class PhoneLoginRequest(BaseModel):
    """手机号 + 验证码登录（验证码校验逻辑在 services/login_channels.py）。"""

    phone: str = Field(pattern=CN_MOBILE_PATTERN)
    code: str = Field(min_length=4, max_length=8, pattern=r"^\d+$")
    long_lived: bool = False
