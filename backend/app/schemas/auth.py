from pydantic import BaseModel, Field


class UserOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    tenant_id: int
    username: str
    name: str
    role: str
    email: str | None = None
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
    avatar_url: str | None = None


class PasswordChange(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8)
