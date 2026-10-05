# 确保 tests 能以 backend 为根导入 app 包（pytest prepend 模式会把本目录加入 sys.path）

# 测试进程统一使用 ≥32 字节的 JWT 密钥：
# - 消除 PyJWT 2.14+ 的 InsecureKeyLengthWarning（默认值 "dev-secret-change-me" 仅 20 字节）；
# - 与生产口径一致（config.validate_jwt_secret 要求非默认值且 ≥32 字符）。
# 直接覆盖 settings 单例最稳妥：conftest 先于所有测试模块导入，
# 且不受开发者本地 backend/.env 里真实密钥的影响（测试内签发/校验令牌自洽即可）。
from app.config import settings

settings.JWT_SECRET = "test-only-jwt-secret-0123456789abcdef"  # 仅测试用，非真实密钥
