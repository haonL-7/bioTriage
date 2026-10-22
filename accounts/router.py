"""账户端点：ORCID 登录/会话、收藏、评估历史、AI 配额。

api.py 通过 accounts 包导出复用 load_user / describe_quota / reserve_quota /
record_evaluation，配额改造因此不需要在 api.py 内重复实现。
"""
import functools
import hashlib
import json
import os
import secrets
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import orcid, session
from .db import account_db_status, ensure_schema, get_db
from .models import AuthToken, Evaluation, Star, UsageCounter, User, utcnow

router = APIRouter(prefix="/api")

DEFAULT_QUOTA_ANON = 1
DEFAULT_QUOTA_PER_USER = 5
STAR_LIST_LIMIT = 200
EVALUATION_LIST_LIMIT = 50

# GitHub Pages 账户页：token 通过 URL fragment（#t=）交付，不出现在 query/Referer 里
PAGES_ACCOUNT_URL_DEFAULT = "https://haonl-7.github.io/bioTriage/account/"
PAGES_MODE = "pages"

TIER_ANON = "anon"
TIER_USER = "user"


# ==================== 配额 ====================

def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name) or default))
    except ValueError:
        return default


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _current_day() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _resets_at() -> str:
    """下一个 UTC 零点（ISO 8601，带 Z）。"""
    tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)
    return f"{tomorrow.isoformat()}T00:00:00Z"


def _quota_identity(request: Request, user: User | None) -> tuple[str, str, int]:
    if user is not None:
        return f"{TIER_USER}:{user.orcid_id}", TIER_USER, _int_env("AI_DAILY_QUOTA_PER_USER", DEFAULT_QUOTA_PER_USER)
    return f"{TIER_ANON}:{_client_ip(request)}", TIER_ANON, _int_env("AI_DAILY_QUOTA_ANON", DEFAULT_QUOTA_ANON)


def _usage(db: Session, key: str, day: str) -> int:
    row = db.get(UsageCounter, (key, day))
    return row.count if row is not None else 0


def _quota_payload(tier: str, limit: int, used: int) -> dict:
    return {
        "tier": tier,
        "limit": limit,
        "used": used,
        "remaining": max(0, limit - used),
        "resets_at": _resets_at(),
    }


def describe_quota(db: Session, request: Request, user: User | None = None) -> dict:
    key, tier, limit = _quota_identity(request, user)
    return _quota_payload(tier, limit, _usage(db, key, _current_day()))


def reserve_quota(db: Session, request: Request, user: User | None = None) -> tuple[bool, dict]:
    """占用一次 AI 配额：返回 (allowed, quota)。超额时不计数。"""
    key, tier, limit = _quota_identity(request, user)
    day = _current_day()
    used = _usage(db, key, day)
    if used >= limit:
        return False, _quota_payload(tier, limit, used)
    row = db.get(UsageCounter, (key, day))
    if row is None:
        db.add(UsageCounter(key=key, day=day, count=1))
    else:
        row.count = used + 1
    db.commit()
    return True, _quota_payload(tier, limit, used + 1)


# 数据库不可用时的进程内兜底计数（按 IP、与匿名档同额），保证 AI 端点不被数据库拖死
_offline_hits: dict[str, list[float]] = {}


def _offline_quota(ip: str) -> tuple[bool, dict]:
    limit = _int_env("AI_DAILY_QUOTA_ANON", DEFAULT_QUOTA_ANON)
    now = time.time()
    hits = [t for t in _offline_hits.get(ip, []) if t > now - 86400]
    if len(hits) >= limit:
        _offline_hits[ip] = hits
        return False, _quota_payload(TIER_ANON, limit, len(hits))
    hits.append(now)
    _offline_hits[ip] = hits
    return True, _quota_payload(TIER_ANON, limit, len(hits))


def reserve_ai_quota(db: Session | None, request: Request) -> tuple[User | None, bool, dict]:
    """AI 分析配额入口：数据库可用时走两档计数，不可用时退回进程内计数，永不抛异常。"""
    try:
        if not ensure_schema():
            raise RuntimeError("账户数据库不可用")
        user = load_user(request, db)
        allowed, quota = reserve_quota(db, request, user)
        return user, allowed, quota
    except Exception as exc:  # noqa: BLE001 - AI 端点不能因为账户库故障而 5xx
        print(f"[警告] AI 配额退回进程内按 IP 计数（{type(exc).__name__}: {exc}）")
        allowed, quota = _offline_quota(_client_ip(request))
        return None, allowed, quota


# ==================== 会话 ====================

def _user_from_bearer(db: Session, token: str) -> User | None:
    """按 sha256(token) 查表：已撤销/已过期/用户已删除一律视为未登录。"""
    digest = session.hash_token(token)
    row = db.scalar(select(AuthToken).where(AuthToken.token_hash == digest))
    if row is None or not session.token_matches(row.token_hash, token):
        return None
    if row.revoked_at is not None or session.token_expired(row.expires_at):
        return None
    return db.get(User, row.user_orcid)


def resolve_user(request: Request, db: Session) -> tuple[User | None, bool]:
    """认证解析顺序：Authorization: Bearer 优先，其次回退 bt_session cookie。

    返回 (user, token_invalid)。带了 Bearer 却无效时不再回退 cookie ——
    否则一个被撤销的 token 会被浏览器残留的 cookie 悄悄"救活"。
    """
    token = session.bearer_token(request.headers.get("authorization"))
    if token is not None:
        user = _user_from_bearer(db, token)
        return user, user is None
    orcid_id = session.read_session_token(request.cookies.get(session.COOKIE_NAME))
    if not orcid_id:
        return None, False
    return db.get(User, orcid_id), False


def load_user(request: Request, db: Session) -> User | None:
    """解析当前用户（Bearer 或 bt_session cookie）；未登录返回 None。"""
    return resolve_user(request, db)[0]


def _client_ip_key(request: Request) -> str:
    return _client_ip(request)


def _redirect_uri(request: Request) -> str:
    base = (os.getenv("APP_BASE_URL") or "").strip().rstrip("/")
    if base:
        return f"{base}/api/auth/callback"
    return str(request.base_url).rstrip("/") + "/api/auth/callback"


def _unauthorized(invalid_token: bool = False) -> JSONResponse:
    if not invalid_token:
        return JSONResponse(status_code=401, content={"detail": "未登录", "authenticated": False})
    return JSONResponse(
        status_code=401,
        content={"detail": "登录令牌无效、已过期或已撤销，请重新登录", "auth": "invalid_token"},
    )


def _pages_account_url() -> str:
    base = (os.getenv("PAGES_ACCOUNT_URL") or "").strip() or PAGES_ACCOUNT_URL_DEFAULT
    return base.rstrip("/") + "/"


def _issue_token(db: Session, orcid_id: str) -> str:
    """签发 Bearer token：明文只回给调用方一次，库里只留 sha256。"""
    token = session.new_token()
    expires_at = utcnow() + timedelta(seconds=session.TOKEN_TTL_SECONDS)
    db.add(AuthToken(token_hash=session.hash_token(token), user_orcid=orcid_id, expires_at=expires_at))
    db.commit()
    return token


def _db_unavailable(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail, "database": "unavailable"},
    )


def account_db_guard(status_code: int, detail: str):
    """账户端点降级：数据库不可用时给可预期状态，绝不冒未捕获的 500 堆栈。

    先尝试一次惰性建表（成功则后续请求不再重复 create_all），
    失败时按端点语义返回 401（登录态无法校验）或 503（需要写库的动作）。
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            if not ensure_schema():
                return _db_unavailable(status_code, detail)
            try:
                return await func(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - 数据库/驱动层错误统一降级
                print(f"[警告] 账户端点 {func.__name__} 数据库操作失败：{type(exc).__name__}: {exc}")
                return _db_unavailable(503, "账户数据库暂不可用，请稍后重试")

        return wrapper

    return decorator


DB_DOWN_UNAUTHENTICATED = "账户数据库暂不可用，无法校验登录状态，请稍后重试"
DB_DOWN_NEEDS_WRITE = "账户数据库暂不可用，暂时无法完成该操作，请稍后重试"


def _public_user(user: User) -> dict:
    return {
        "orcid_id": user.orcid_id,
        "name": user.name or "",
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


def _public_star(star: Star) -> dict:
    payload = None
    if star.payload_json:
        try:
            payload = json.loads(star.payload_json)
        except ValueError:
            payload = None
    return {
        "id": star.id,
        "kind": star.kind,
        "doi": star.doi,
        "title": star.title,
        "payload": payload,
        "created_at": star.created_at.isoformat() if star.created_at else None,
    }


def _public_evaluation(row: Evaluation) -> dict:
    def _loads(raw: str):
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            return None

    return {
        "id": row.id,
        "input_hash": row.input_hash,
        "request": _loads(row.request_json),
        "response": _loads(row.response_json),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


# ==================== 认证端点 ====================

@router.get("/auth/login")
async def auth_login(request: Request, mode: str | None = None):
    """mode=pages：为 GitHub Pages 账户页登录，回调时改发 Bearer token。"""
    state = session.create_state((mode or "").strip().lower() or None)
    try:
        authorize_url = orcid.build_authorize_url(state, _redirect_uri(request))
    except orcid.OrcidError as exc:
        return JSONResponse(status_code=503, content={"detail": str(exc), "configured": False})

    response = RedirectResponse(authorize_url, status_code=302)
    for cookie_name in (session.STATE_COOKIE_NAME, session.STATE_COOKIE_ALIAS):
        response.set_cookie(
            cookie_name,
            state,
            max_age=session.STATE_MAX_AGE,
            path="/",
            httponly=True,
            samesite="lax",
            secure=session.cookie_secure(),
        )
    return response


def _log_auth_step(step: str, ok: bool, started: float) -> None:
    """callback 每一步一行结构化日志（前缀 [auth]），便于将来接日志集后直接定位。"""
    duration = time.perf_counter() - started
    print(f"[auth] step={step} ok={1 if ok else 0} dur={duration:.2f}s")


def _callback_failure(step: str, exc: Exception) -> JSONResponse:
    """把 callback 任意一步的异常转成可读响应（线上唯一可观测手段）。"""
    error_class = type(exc).__name__
    message = session.redact_exception(exc)
    print(f"[auth] step={step} ok=0 error_class={error_class} error={message}")
    return JSONResponse(
        status_code=500,
        content={
            "detail": f"登录回调失败（step={step}）：{error_class}",
            "step": step,
            "error_class": error_class,
            "error": message,
        },
    )


@router.get("/auth/callback")
async def auth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    expected = request.cookies.get(session.STATE_COOKIE_NAME) or request.cookies.get(session.STATE_COOKIE_ALIAS)
    # pages 的 state 自带 HMAC，跨站跳转即使 state cookie 被拦截也能验证；cookie 流程要求 cookie 比对通过
    mode = session.read_state_mode(state)
    cookie_ok = bool(state and expected and secrets.compare_digest(state, expected))
    if not state or not (cookie_ok or mode):
        return JSONResponse(status_code=400, content={"detail": "state 校验失败，请重新登录"})
    pages = mode == PAGES_MODE
    if error:
        return JSONResponse(status_code=400, content={"detail": f"ORCID 授权失败：{error}"})
    if not code:
        return JSONResponse(status_code=400, content={"detail": "缺少 code 参数"})

    # 全流程兜底：任何一步失败都返回带 step 的 500 JSON，绝不把异常冒到平台层
    # （SCF 见到函数抛出会返回 statusCode 443，浏览器显示 ERR_INVALID_RESPONSE）。
    step = "token"
    started = time.perf_counter()
    try:
        profile = orcid.fake_profile()
        if profile is None:
            access_token = orcid.exchange_code(code, _redirect_uri(request))
            _log_auth_step(step, True, started)
            step = "userinfo"
            started = time.perf_counter()
            profile = orcid.fetch_profile(access_token)
        _log_auth_step(step, True, started)

        step = "db_upsert"
        started = time.perf_counter()
        if db is None or not ensure_schema():
            raise RuntimeError("账户数据库不可用，无法写入用户")
        try:
            user = db.get(User, profile["orcid_id"])
            if user is None:
                user = User(orcid_id=profile["orcid_id"], name=profile.get("name") or "")
                db.add(user)
            elif profile.get("name"):
                user.name = profile["name"]
            db.commit()
        except Exception as exc:  # 驱动原文可能含主机/库名/用户名，只暴露类名
            raise RuntimeError(f"写入用户失败（{type(exc).__name__}）") from exc
        _log_auth_step(step, True, started)

        step = "session"
        started = time.perf_counter()
        if pages:
            # 账户页在 GitHub Pages（跨站），cookie 会被浏览器当第三方 cookie 拦掉，改用 Bearer token
            token = _issue_token(db, user.orcid_id)
            response = RedirectResponse(f"{_pages_account_url()}#t={token}", status_code=302)
        else:
            response = RedirectResponse("/account", status_code=302)
            session.set_session_cookie(response, session.create_session_token(user.orcid_id))
        _log_auth_step(step, True, started)

        step = "redirect"
        started = time.perf_counter()
        for cookie_name in (session.STATE_COOKIE_NAME, session.STATE_COOKIE_ALIAS):
            response.delete_cookie(cookie_name, path="/")
        _log_auth_step(step, True, started)
        return response
    except Exception as exc:  # noqa: BLE001 - 任何异常都必须变成可读响应
        return _callback_failure(step, exc)


@router.post("/auth/logout")
async def auth_logout(request: Request, db: Session = Depends(get_db)):
    """Bearer 会话撤销该 token（记 revoked_at）；cookie 会话维持原行为（清 cookie）。"""
    response = JSONResponse({"ok": True})
    token = session.bearer_token(request.headers.get("authorization"))
    if token is not None:
        try:
            if db is not None:
                row = db.scalar(
                    select(AuthToken).where(AuthToken.token_hash == session.hash_token(token))
                )
                if row is not None and row.revoked_at is None:
                    row.revoked_at = utcnow()
                    db.commit()
        except Exception as exc:  # noqa: BLE001 - 撤销失败也不能把 logout 变成 500
            print(f"[警告] token 撤销失败（{type(exc).__name__}）")
    session.clear_session_cookie(response)
    return response


@router.get("/auth/me")
@account_db_guard(401, DB_DOWN_UNAUTHENTICATED)
async def auth_me(request: Request, db: Session = Depends(get_db)):
    user, invalid_token = resolve_user(request, db)
    if invalid_token:
        return _unauthorized(True)
    return {
        "authenticated": user is not None,
        "user": _public_user(user) if user is not None else None,
        "quota": describe_quota(db, request, user),
    }


@router.get("/me/quota")
@account_db_guard(503, DB_DOWN_NEEDS_WRITE)
async def me_quota(request: Request, db: Session = Depends(get_db)):
    user, invalid_token = resolve_user(request, db)
    if invalid_token:
        return _unauthorized(True)
    return describe_quota(db, request, user)


# ==================== 诊断 ====================

@router.get("/health/accounts")
async def accounts_health():
    """只读诊断：回报账户数据库的配置/驱动/建表状态与上一次失败的粗分类。

    只读已记录的状态，不发起任何连接；不返回连接串、host、用户名、库名、
    密码、异常原文或堆栈。
    """
    return account_db_status()


# ==================== 收藏 ====================

class StarIn(BaseModel):
    kind: str
    doi: str | None = None
    title: str | None = None
    payload: dict | None = None


@router.get("/me/stars")
@account_db_guard(401, DB_DOWN_UNAUTHENTICATED)
async def list_stars(request: Request, db: Session = Depends(get_db)):
    user, invalid_token = resolve_user(request, db)
    if user is None:
        return _unauthorized(invalid_token)
    rows = db.scalars(
        select(Star)
        .where(Star.user_orcid == user.orcid_id)
        .order_by(Star.created_at.desc(), Star.id.desc())
        .limit(STAR_LIST_LIMIT)
    ).all()
    return {"items": [_public_star(star) for star in rows]}


@router.post("/me/stars", status_code=201)
@account_db_guard(503, DB_DOWN_NEEDS_WRITE)
async def create_star(body: StarIn, request: Request, db: Session = Depends(get_db)):
    user, invalid_token = resolve_user(request, db)
    if user is None:
        return _unauthorized(invalid_token)
    if not (body.doi or body.title):
        return JSONResponse(status_code=422, content={"detail": "doi 与 title 至少提供一个"})

    star = Star(
        user_orcid=user.orcid_id,
        kind=body.kind,
        doi=body.doi,
        title=body.title,
        payload_json=json.dumps(body.payload, ensure_ascii=False) if body.payload is not None else None,
    )
    db.add(star)
    db.commit()
    db.refresh(star)
    return {"item": _public_star(star)}


@router.delete("/me/stars/{star_id}", status_code=204)
@account_db_guard(503, DB_DOWN_NEEDS_WRITE)
async def delete_star(star_id: int, request: Request, db: Session = Depends(get_db)):
    user, invalid_token = resolve_user(request, db)
    if user is None:
        return _unauthorized(invalid_token)
    star = db.get(Star, star_id)
    # 别人的收藏一律按"不存在"处理，避免泄露存在性
    if star is None or star.user_orcid != user.orcid_id:
        return JSONResponse(status_code=404, content={"detail": "收藏不存在"})
    db.delete(star)
    db.commit()
    return Response(status_code=204)


# ==================== 评估历史 ====================

def record_evaluation(db: Session, user: User | None, request_payload: dict, result: dict) -> None:
    """登录用户的 AI 分析结果入库；input_hash 对 (用户, 请求) 取 sha256 以保持唯一索引成立。"""
    if user is None or db is None:
        return
    if not ensure_schema():
        return
    request_json = json.dumps(request_payload, ensure_ascii=False, sort_keys=True)
    response_json = json.dumps(result, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(f"{user.orcid_id}:{request_json}".encode("utf-8")).hexdigest()

    try:
        row = db.scalar(select(Evaluation).where(Evaluation.input_hash == digest))
        if row is None:
            db.add(
                Evaluation(
                    user_orcid=user.orcid_id,
                    input_hash=digest,
                    request_json=request_json,
                    response_json=response_json,
                )
            )
        else:
            row.response_json = response_json
            row.created_at = utcnow()
        db.commit()
    except Exception as exc:  # noqa: BLE001 - 历史入库失败不影响本次分析结果
        db.rollback()
        print(f"[警告] 评估历史写入失败（{type(exc).__name__}: {exc}），本次分析结果照常返回")


@router.get("/me/evaluations")
@account_db_guard(401, DB_DOWN_UNAUTHENTICATED)
async def list_evaluations(request: Request, db: Session = Depends(get_db)):
    user, invalid_token = resolve_user(request, db)
    if user is None:
        return _unauthorized(invalid_token)
    rows = db.scalars(
        select(Evaluation)
        .where(Evaluation.user_orcid == user.orcid_id)
        .order_by(Evaluation.created_at.desc(), Evaluation.id.desc())
        .limit(EVALUATION_LIST_LIMIT)
    ).all()
    return {"items": [_public_evaluation(row) for row in rows]}
