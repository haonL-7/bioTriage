"""认证与会话契约（Acceptance Criteria 1/2/3 + 会话 cookie 细节）。

覆盖：
* 未登录 /api/auth/me、
* 登录往返（login 302 + state cookie -> callback -> me）、
* state 篡改必须 400 且不建会话、
* logout 清 cookie、
* 会话 cookie 属性与值格式（base64(payload)+"."+HMAC-SHA256）。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qs, urlparse

from conftest import (
    APP_BASE_URL,
    NAME_USER_A,
    ORCID_USER_A,
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    import_app,
    me_json,
    send_callback,
    session_set_cookie_header,
    snippet,
)


def start_login(client, app_base=APP_BASE_URL) -> dict:
    """GET /api/auth/login，逐条核对授权跳转参数，返回 state。"""
    response = client.get("/api/auth/login")
    assert response.status_code == 302, (
        f"契约：GET /api/auth/login 必须 302 到 ORCID 授权页，实际 {response.status_code}；body={snippet(response)}"
    )
    location = response.headers.get("location", "")
    url = urlparse(location)
    assert url.netloc == "sandbox.orcid.org", (
        f"契约：ORCID_BASE_URL=https://sandbox.orcid.org 时 login 应跳转到 sandbox，实际 Location={location!r}"
    )
    assert url.path == "/oauth/authorize", (
        f"契约：authorization_endpoint 为 <ORCID_BASE_URL>/oauth/authorize，实际 path={url.path!r}"
    )
    query = parse_qs(url.query, keep_blank_values=True)
    for key in ("client_id", "response_type", "scope", "redirect_uri", "state"):
        assert query.get(key), f"契约：authorize 跳转缺少参数 {key}，实际 query={url.query!r}"
    assert query["response_type"] == ["code"], f"契约：response_type 必须为 code，实际 {query['response_type']}"
    assert query["scope"] == ["openid"], f"契约：scope 仅 openid，实际 {query['scope']}"
    redirect_uri = query["redirect_uri"][0]
    assert redirect_uri.startswith(app_base), (
        f"契约：redirect_uri 必须由 APP_BASE_URL={app_base} 派生，实际 {redirect_uri!r}"
    )
    assert urlparse(redirect_uri).path == "/api/auth/callback", (
        f"契约：回调地址应为 /api/auth/callback，实际 {urlparse(redirect_uri).path!r}"
    )
    assert response.headers.get_list("set-cookie"), "契约：login 必须把 state 写入短时 cookie，但响应没有 Set-Cookie"
    return {"state": query["state"][0], "location": location, "query": query, "response": response}


def _load_payload(payload_b64: str) -> dict:
    raw = payload_b64.strip()
    padded = raw + "=" * (-len(raw) % 4)
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            return json.loads(decoder(padded))
        except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
            continue
    raise AssertionError(
        f"契约：会话 cookie 前段必须是 base64(JSON payload)，实际无法解码：{payload_b64[:120]!r}"
    )


def _signature_matches(payload_b64: str, signature: str) -> bool:
    """签名可能在 base64(payload) 或 payload 原文上计算，摘要可能是 hex/base64/urlsafe-base64。"""
    secret = os.environ.get("SESSION_SECRET", "").encode()
    if not secret:
        raise AssertionError("测试环境错误：SESSION_SECRET 未设置")
    raw_payload = None
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            raw_payload = decoder(padded)
            break
        except (binascii.Error, ValueError):
            continue

    messages = [payload_b64.encode()]
    if raw_payload is not None:
        messages.append(raw_payload)
    accepted = set()
    for message in messages:
        digest = hmac.new(secret, message, hashlib.sha256).digest()
        accepted.add(digest.hex())
        accepted.add(base64.urlsafe_b64encode(digest).decode().rstrip("="))
        accepted.add(base64.b64encode(digest).decode().rstrip("="))
    return any(signature.rstrip("=") == candidate.rstrip("=") for candidate in accepted)


# ==================== 验收用例 ====================


def test_anon_me_reports_not_authenticated(client):
    """验收 1：未登录 GET /api/auth/me -> authenticated=false。"""
    body = me_json(client)
    assert body["authenticated"] is False, (
        f"契约：未登录时 authenticated 必须为 false，实际 {json.dumps(body, ensure_ascii=False)[:400]}"
    )
    assert body["user"] is None, (
        f"契约：未登录时 user 必须为 null，实际 user={json.dumps(body['user'], ensure_ascii=False)[:300]}"
    )
    assert SESSION_COOKIE not in client.cookies, "契约：未登录的客户端不应持有会话 cookie"


def test_login_roundtrip_authenticates_user(client):
    """验收 2：login 302 + state cookie -> callback -> me.authenticated=true。"""
    info = start_login(client)
    state_cookie_names = [h.split("=", 1)[0].strip() for h in info["response"].headers.get_list("set-cookie")]
    assert state_cookie_names, "契约：login 必须写入 state cookie，但响应没有 Set-Cookie"
    assert SESSION_COOKIE not in state_cookie_names, (
        f"契约：会话 cookie 应在 callback 阶段下发，login 阶段不应下发 {SESSION_COOKIE}"
    )

    callback = send_callback(client, info["state"])
    assert callback.status_code == 302, (
        f"契约：callback 换 token / 取 profile / 建会话后应 302 到 /account，实际 {callback.status_code}；"
        f"body={snippet(callback)}"
    )
    assert urlparse(callback.headers.get("location", "")).path == "/account", (
        f"契约：callback 应 302 到 /account，实际 Location={callback.headers.get('location')!r}"
    )
    assert SESSION_COOKIE in client.cookies, (
        f"契约：callback 必须下发 {SESSION_COOKIE} cookie，实际 cookie jar={dict(client.cookies)}"
    )

    body = me_json(client)
    assert body["authenticated"] is True, (
        f"契约：登录后 authenticated 必须为 true，实际 {json.dumps(body, ensure_ascii=False)[:400]}"
    )
    user = body["user"]
    assert isinstance(user, dict), f"契约：登录后 user 必须是对象，实际 {user!r}"
    for key in ("orcid_id", "name", "created_at"):
        assert key in user, f"契约：user 必须含 {key}，实际 user={json.dumps(user, ensure_ascii=False)[:300]}"
    assert user["orcid_id"] == ORCID_USER_A, (
        f"契约：BT_FAKE_ORCID.sub 即登录后的 orcid_id，期望 {ORCID_USER_A}，实际 {user['orcid_id']!r}"
    )
    assert user["name"] == NAME_USER_A, f"契约：user.name 应来自 profile，实际 {user['name']!r}"
    assert user["created_at"], "契约：user.created_at 不能为空"


def test_callback_rejects_tampered_state(client):
    """验收 3：state 篡改 -> 400 且不下发会话 cookie（CSRF 防护）。"""
    info = start_login(client)
    response = send_callback(client, info["state"] + "tampered")
    assert response.status_code == 400, (
        f"契约：state 不匹配必须 400，实际 {response.status_code}；body={snippet(response)}"
    )
    assert SESSION_COOKIE not in client.cookies, (
        f"契约：state 校验失败不得建立会话，实际下发了 {SESSION_COOKIE}={client.cookies.get(SESSION_COOKIE)!r}"
    )
    body = me_json(client)
    assert body["authenticated"] is False, (
        f"契约：state 篡改后仍应处于未登录状态，实际 {json.dumps(body, ensure_ascii=False)[:300]}"
    )


def test_logout_clears_session(client, login):
    """契约：POST /api/auth/logout -> 200 {"ok": true} 并清除会话 cookie。"""
    login(client)
    response = client.post("/api/auth/logout")
    assert response.status_code == 200, (
        f"契约：logout 必须 200，实际 {response.status_code}；body={snippet(response)}"
    )
    assert response.json().get("ok") is True, (
        f"契约：logout 响应体应为 {{\"ok\": true}}，实际 {snippet(response)}"
    )
    body = me_json(client)
    assert body["authenticated"] is False, (
        f"契约：logout 后必须变为未登录，实际 {json.dumps(body, ensure_ascii=False)[:300]}"
    )
    remaining = client.cookies.get(SESSION_COOKIE)
    assert not remaining, f"契约：logout 应清除 {SESSION_COOKIE}，实际仍持有 {remaining!r}"


def test_session_cookie_attributes(client):
    """契约：bt_session 需 HttpOnly / SameSite=Lax / Path=/；APP_BASE_URL 非 https 时不加 Secure。"""
    info = start_login(client)
    callback = send_callback(client, info["state"])
    header = session_set_cookie_header(callback)
    lowered = header.lower()
    assert "httponly" in lowered, f"契约：会话 cookie 必须 HttpOnly，实际 {header!r}"
    assert "samesite=lax" in lowered, f"契约：会话 cookie 必须 SameSite=Lax，实际 {header!r}"
    assert "path=/" in lowered, f"契约：会话 cookie 必须 Path=/，实际 {header!r}"
    assert "secure" not in lowered, (
        f"契约：APP_BASE_URL={os.environ.get('APP_BASE_URL')} 不以 https 开头，不应加 Secure；实际 {header!r}"
    )


def test_session_cookie_is_secure_when_app_base_url_is_https(app_env, client_of, monkeypatch):
    """契约：APP_BASE_URL 以 https 开头时会话 cookie 必须带 Secure。"""
    https_base = "https://biotriage.example.com"
    monkeypatch.setenv("APP_BASE_URL", https_base)
    # app_env 已注入 ORCID 客户端凭据；重新 import 让 APP_BASE_URL 生效
    module = import_app()
    client = client_of(module, ip="198.51.100.55", base_url="https://testserver")

    info = start_login(client, app_base=https_base)
    callback = send_callback(client, info["state"])
    assert callback.status_code == 302, (
        f"契约：callback 应 302 到 /account，实际 {callback.status_code}；body={snippet(callback)}"
    )
    header = session_set_cookie_header(callback)
    assert "secure" in header.lower(), (
        f"契约：APP_BASE_URL={https_base} 以 https 开头，会话 cookie 必须带 Secure；实际 {header!r}"
    )


def test_session_cookie_payload_format(client):
    """契约：值 = base64(payload)+"."+HMAC-SHA256(SESSION_SECRET)；payload 含 orcid_id 与 exp。"""
    info = start_login(client)
    callback = send_callback(client, info["state"])
    assert callback.status_code == 302, (
        f"契约：callback 应 302 到 /account，实际 {callback.status_code}；body={snippet(callback)}"
    )
    value = client.cookies.get(SESSION_COOKIE)
    assert value, f"契约：callback 后应下发 {SESSION_COOKIE}，实际 cookie jar={dict(client.cookies)}"

    payload_b64, _, signature = value.rpartition(".")
    assert payload_b64 and signature, f"契约：会话值必须形如 base64(payload).HMAC，实际 {value[:80]!r}"

    payload = _load_payload(payload_b64)
    assert payload.get("orcid_id") == ORCID_USER_A, (
        f"契约：会话 payload 必须含 orcid_id={ORCID_USER_A}，实际 {json.dumps(payload, ensure_ascii=False)[:300]}"
    )
    assert "exp" in payload, f"契约：会话 payload 必须含 exp，实际 {json.dumps(payload, ensure_ascii=False)[:300]}"
    expected_exp = time.time() + SESSION_MAX_AGE
    assert abs(float(payload["exp"]) - expected_exp) <= 3600, (
        f"契约：exp 应约为 现在+SESSION_MAX_AGE({SESSION_MAX_AGE})s，实际 exp={payload['exp']!r}"
    )
    assert _signature_matches(payload_b64, signature), (
        "契约：签名须为 HMAC-SHA256(SESSION_SECRET)（在 base64(payload) 或 payload 原文上），"
        f"实际签名 {signature[:40]!r} 不匹配"
    )
