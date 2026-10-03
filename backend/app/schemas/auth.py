import json

from pydantic import BaseModel, Field, RootModel, field_validator

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
    # 首登强制改密标记（种子 admin 等为 True；前端登录后拦截到改密页）
    must_change_password: bool = False


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
    # 不接受 avatar_url：头像只能走 POST /auth/avatar 上传端点（含 MIME/魔数校验），
    # 直接传 URL 会绕过校验并可指向任意地址；pydantic 默认忽略未知字段（静默丢弃）。

    _norm_phone = field_validator("phone", mode="before")(_normalize_phone)


class PasswordChange(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8)


_PREFERENCES_MAX_BYTES = 16 * 1024


class PreferencesUpdate(RootModel[dict]):
    """用户偏好设置：任意 JSON 对象，序列化后体积上限 16KB（防 JSONB 无限膨胀）。"""

    @field_validator("root")
    @classmethod
    def _check_size(cls, v: dict) -> dict:
        size = len(json.dumps(v, ensure_ascii=False).encode("utf-8"))
        if size > _PREFERENCES_MAX_BYTES:
            raise ValueError(f"preferences 体积超限（>{_PREFERENCES_MAX_BYTES // 1024}KB）")
        return v


class SmsCodeRequest(BaseModel):
    """发送短信登录验证码。"""

    phone: str = Field(pattern=CN_MOBILE_PATTERN)


class PhoneLoginRequest(BaseModel):
    """手机号 + 验证码登录（验证码校验逻辑在 services/login_channels.py）。"""

    phone: str = Field(pattern=CN_MOBILE_PATTERN)
    code: str = Field(min_length=4, max_length=8, pattern=r"^\d+$")
    long_lived: bool = False
