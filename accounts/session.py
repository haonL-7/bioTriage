"""会话 cookie：base64(payload) + "." + HMAC-SHA256(SESSION_SECRET)。

仅使用标准库（hmac/hashlib/base64/json），不引入额外认证库。
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone

COOKIE_NAME = "bt_session"
STATE_COOKIE_NAME = "bt_state"
# 契约未固定 state cookie 名，两个名字同时下发并同时接受，便于第三方测试对接
STATE_COOKIE_ALIAS = "state"
STATE_MAX_AGE = 600
DEFAULT_MAX_AGE = 2592000

# Bearer token（GitHub Pages 账户页）：32 字节熵、30 天有效，只保存 sha256 哈希
TOKEN_BYTES = 32
TOKEN_TTL_SECONDS = 30 * 86400
PAGES_MODE = "pages"

# SESSION_SECRET 缺失时的进程内临时密钥：保证可安全启动，重启后旧会话失效
_EPHEMERAL_SECRET = secrets.token_hex(32)


# 形如 secret=xxx / token: yyy / code=zzz 的值一律打码，避免敏感值进入响应或日志
_SECRET_PATTERN = re.compile(
    r"(?i)([\"']?\b(?:client_secret|client_id|secret|password|access_token|refresh_token|id_token|token|authorization|code)\b[\"']?\s*[=:]\s*[\"']?)"
    r"([^\s,;&\"')]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
# 数据库报错里的 user "xxx" 也是身份信息
_USER_PATTERN = re.compile(r'(?i)\buser\s+"[^"]*"')
# 主机名/IP 也算连接信息：一律抹掉，避免驱动异常把数据库地址带进响应
_HOST_PATTERN = re.compile(
    r"\b\d{1,3}(?:\.\d{1,3}){3}\b"
    r"|\b(?:[A-Za-z0-9_%+-]+\.)*[A-Za-z0-9-]+\.(?:com|org|net|io|tech|cn|local|internal)\b",
)


def redact(text: str, limit: int = 300) -> str:
    """对文本做关键词脱敏并截断，用于日志与错误响应。"""
    cleaned = _BEARER_PATTERN.sub("Bearer ***", str(text))
    cleaned = _SECRET_PATTERN.sub(lambda match: f"{match.group(1)}***", cleaned)
    cleaned = _HOST_PATTERN.sub("***", cleaned)
    cleaned = _USER_PATTERN.sub('user "***"', cleaned)
    return cleaned[:limit]


def redact_exception(exc: BaseException, limit: int = 300) -> str:
    """只保留异常类名 + 脱敏后的消息（不含堆栈）。"""
    return redact(f"{type(exc).__name__}: {exc}", limit)


def session_max_age() -> int:
    try:
        return int(os.getenv("SESSION_MAX_AGE") or DEFAULT_MAX_AGE)
    except ValueError:
        return DEFAULT_MAX_AGE


def _secret() -> bytes:
    return (os.getenv("SESSION_SECRET") or _EPHEMERAL_SECRET).encode("utf-8")


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(payload: dict) -> str:
    body = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def unsign(token: str) -> dict | None:
    if not token or "." not in token:
        return None
    body, _, signature = token.rpartition(".")
    expected = hmac.new(_secret(), body.encode("ascii", "ignore"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        payload = json.loads(_b64decode(body))
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def create_session_token(orcid_id: str) -> str:
    return sign({"orcid_id": orcid_id, "exp": int(time.time()) + session_max_age()})


def read_session_token(token: str | None) -> str | None:
    """返回 cookie 中的 orcid_id；签名不符或已过期返回 None。"""
    payload = unsign(token or "")
    if not payload:
        return None
    orcid_id = payload.get("orcid_id")
    exp = payload.get("exp")
    if not isinstance(orcid_id, str) or not isinstance(exp, int) or exp < int(time.time()):
        return None
    return orcid_id


def cookie_secure() -> bool:
    return (os.getenv("APP_BASE_URL") or "").strip().lower().startswith("https")


def set_session_cookie(response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=session_max_age(),
        path="/",
        httponly=True,
        samesite="lax",
        secure=cookie_secure(),
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=cookie_secure(),
    )


# ==================== OAuth state ====================
# cookie 流程仍是随机串 + state cookie 比对；pages 模式额外把 mode 编进 state 并附 HMAC，
# 这样跨站跳转即使 state cookie 被浏览器拦截，state 自身仍可验证（防登录 CSRF）。

def _state_signature(nonce: str, mode: str) -> str:
    return hmac.new(_secret(), f"{nonce}.{mode}".encode("utf-8"), hashlib.sha256).hexdigest()


def create_state(mode: str | None = None) -> str:
    nonce = secrets.token_urlsafe(24)
    if not mode:
        return nonce
    return f"{nonce}.{mode}.{_state_signature(nonce, mode)}"


def read_state_mode(state: str | None) -> str | None:
    """返回 state 自我声明的 mode；签名不符（或无 mode）返回 None。"""
    parts = (state or "").split(".")
    if len(parts) != 3:
        return None
    nonce, mode, signature = parts
    if not hmac.compare_digest(_state_signature(nonce, mode), signature):
        return None
    return mode or None


# ==================== Bearer token ====================

def bearer_token(authorization: str | None) -> str | None:
    """从 Authorization 头取出 Bearer 明文；不是 Bearer 方案时返回 None。"""
    scheme, _, value = (authorization or "").partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """入库只存 sha256(token)；数据库泄露不等于 token 泄露。"""
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def token_matches(stored_hash: str, token: str) -> bool:
    return hmac.compare_digest(stored_hash or "", hash_token(token))


def to_utc(value: datetime | None) -> datetime | None:
    """SQLite 会把 DateTime(timezone=True) 读成 naive，统一按 UTC 解释后再比较。"""
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def token_expired(expires_at: datetime | None) -> bool:
    deadline = to_utc(expires_at)
    return deadline is None or deadline <= datetime.now(timezone.utc)
