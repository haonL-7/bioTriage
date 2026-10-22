"""GitHub Pages 账户页（Bearer token 模式）契约。

背景：腾讯云 SCF 默认域名禁止渲染 HTML（平台强制 Content-Disposition: attachment），
账户页因此搬到 GitHub Pages，认证从 cookie 换成 Authorization: Bearer。

覆盖：
* mode=pages 登录往返：callback 302 到 PAGES_ACCOUNT_URL，token 在 URL fragment 里，且不下发 bt_session；
* Bearer 能访问 /api/auth/me 与 /api/me/stars（GET / POST / DELETE）；
* 伪造 / 过期 / 已 logout 的 token -> 401 且 auth=invalid_token；
* 跨站预检（Origin=https://haonl-7.github.io, DELETE, Authorization）被允许；
* 回归：不带 Authorization 时 cookie 会话照旧工作。
"""

from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from conftest import (
    NAME_USER_A,
    ORCID_SANDBOX,
    ORCID_USER_A,
    SESSION_COOKIE,
    send_callback,
    snippet,
    star_id,
    start_login,
)

PAGES_ACCOUNT_URL = "https://haonl-7.github.io/bioTriage/account/"
PAGES_ORIGIN = "https://haonl-7.github.io"
INVALID_TOKEN_CODE = "invalid_token"


# ==================== 工具 ====================


def pages_login(client, mode: str = "pages") -> dict:
    """GET /api/auth/login?mode=pages，返回 state 与跳转信息（含 state cookie）。"""
    response = client.get("/api/auth/login", params={"mode": mode})
    assert response.status_code == 302, (
        f"契约：GET /api/auth/login?mode={mode} 必须 302 到 ORCID，实际 {response.status_code}；body={snippet(response)}"
    )
    location = response.headers.get("location", "")
    url = urlparse(location)
    assert f"{url.scheme}://{url.netloc}" == ORCID_SANDBOX, (
        f"契约：mode=pages 也必须跳 ORCID_BASE_URL={ORCID_SANDBOX}，实际 Location={location!r}"
    )
    query = parse_qs(url.query, keep_blank_values=True)
    assert query.get("state"), f"契约：login 必须带 state 参数，实际 query={url.query!r}"
    assert response.headers.get_list("set-cookie"), "契约：login 必须把 state 写入短时 cookie，但响应没有 Set-Cookie"
    return {"state": query["state"][0], "response": response}


def pages_callback(client, state_value: str, code: str = "fake-authorization-code"):
    return client.get("/api/auth/callback", params={"code": code, "state": state_value})


def issue_token(client) -> str:
    """走完 mode=pages 登录往返，从 Location fragment 里取出 Bearer token。"""
    info = pages_login(client)
    response = pages_callback(client, info["state"])
    assert response.status_code == 302, (
        f"契约：mode=pages 的 callback 必须 302 回 GitHub Pages，实际 {response.status_code}；body={snippet(response)}"
    )
    location = response.headers.get("location", "")
    fragment = urlparse(location).fragment
    token = parse_qs(fragment).get("t", [""])[0]
    assert token, f"契约：callback 应把 token 放在 URL fragment #t=<token>，实际 Location={location!r}"
    return token


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def assert_invalid_token(response, label: str) -> None:
    assert response.status_code == 401, (
        f"契约：{label} 必须 401，实际 {response.status_code}；body={snippet(response)}"
    )
    body = response.json()
    assert body.get("auth") == INVALID_TOKEN_CODE, (
        f"契约：{label} 的 401 响应体需含 auth={INVALID_TOKEN_CODE!r}，实际 body={snippet(response)}"
    )
    assert body.get("detail"), f"契约：{label} 的 401 响应体需含非空 detail，实际 body={snippet(response)}"


def expire_all_tokens(db_path) -> None:
    """把该用户所有 token 的 expires_at 改成 31 天前（不依赖哈希算法，只看契约列名）。"""
    connection = sqlite3.connect(str(db_path))
    try:
        row = connection.execute("SELECT expires_at FROM auth_tokens LIMIT 1").fetchone()
        assert row is not None, "契约：mode=pages 登录后 auth_tokens 表必须有记录"
        current = row[0]
        past = datetime.now() - timedelta(days=31)
        if isinstance(current, str):
            value = past.strftime("%Y-%m-%d %H:%M:%S")
        elif isinstance(current, int):
            value = int(past.timestamp())
        else:
            value = float(past.timestamp())
        connection.execute("UPDATE auth_tokens SET expires_at = ?", (value,))
        connection.commit()
        rows = connection.execute("SELECT expires_at FROM auth_tokens").fetchall()
        assert rows, "契约：expires_at 更新后 auth_tokens 不应为空"
    finally:
        connection.close()


def session_cookie_headers(response) -> list:
    return [h for h in response.headers.get_list("set-cookie") if h.lower().startswith(SESSION_COOKIE + "=")]


# ==================== 1. mode=pages 登录往返 ====================


def test_pages_login_redirects_token_in_fragment_without_cookie(client):
    """契约：callback 302 到 PAGES_ACCOUNT_URL，#t=<token>，且不下发 bt_session。"""
    info = pages_login(client)
    response = pages_callback(client, info["state"])
    assert response.status_code == 302, (
        f"契约：mode=pages 的 callback 必须 302 到 PAGES_ACCOUNT_URL，实际 {response.status_code}；"
        f"body={snippet(response)}"
    )

    location = response.headers.get("location", "")
    url = urlparse(location)
    expected = urlparse(PAGES_ACCOUNT_URL.rstrip("/"))
    assert f"{url.scheme}://{url.netloc}" == f"{expected.scheme}://{expected.netloc}", (
        f"契约：应 302 到 PAGES_ACCOUNT_URL={PAGES_ACCOUNT_URL}，实际 Location={location!r}"
    )
    assert url.path.rstrip("/") == expected.path, (
        f"契约：应 302 到 PAGES_ACCOUNT_URL 的路径 {expected.path!r}，实际 {url.path!r}"
    )
    fragment = parse_qs(url.fragment)
    token = fragment.get("t", [""])[0]
    assert token, f"契约：token 必须放在 URL fragment 的 t 参数里（#t=<token>），实际 Location={location!r}"
    assert len(token) >= 32, f"契约：token 应为 secrets.token_urlsafe(32)（>=43 字符），实际 len={len(token)}"
    assert "?" not in url.fragment and token not in url.query, (
        f"契约：token 不得出现在 query string 里（会进 Referer / 日志），实际 Location={location!r}"
    )

    assert not session_cookie_headers(response), (
        f"契约：mode=pages 回调不设置会话 cookie（保持干净），实际 Set-Cookie={response.headers.get_list('set-cookie')}"
    )
    assert SESSION_COOKIE not in client.cookies, (
        f"契约：mode=pages 登录后客户端不应持有 {SESSION_COOKIE}，实际 cookie jar={dict(client.cookies)}"
    )


def test_pages_token_authenticates_me(client_of, api_module):
    """契约：fragment 里的 token 直接用 Authorization: Bearer 可取到自己的身份。"""
    token = issue_token(client_of(api_module, ip="203.0.113.21"))

    # 换一个全新 client（空 cookie jar）——证明身份完全来自 Bearer
    api_client = client_of(api_module, ip="203.0.113.22", base_url="https://testserver")
    response = api_client.get("/api/auth/me", headers=bearer(token))
    assert response.status_code == 200, (
        f"契约：Bearer 调用 /api/auth/me 必须 200，实际 {response.status_code}；body={snippet(response)}"
    )
    body = response.json()
    assert body.get("authenticated") is True, (
        f"契约：Bearer 应被识别为已登录，实际 body={snippet(response)}"
    )
    user = body.get("user") or {}
    assert user.get("orcid_id") == ORCID_USER_A, (
        f"契约：Bearer 应解析出 token 对应的 orcid_id={ORCID_USER_A}，实际 user={snippet(response)}"
    )
    assert user.get("name") == NAME_USER_A, f"契约：user.name 应来自 profile，实际 user={snippet(response)}"


# ==================== 2. Bearer 访问 /api/me/stars ====================


def test_bearer_reads_writes_and_deletes_stars(client_of, api_module):
    """契约：Bearer 能 GET / POST / DELETE /api/me/stars（不用任何 cookie）。"""
    token = issue_token(client_of(api_module, ip="203.0.113.31"))
    api_client = client_of(api_module, ip="203.0.113.32", base_url="https://testserver")

    listed = api_client.get("/api/me/stars", headers=bearer(token))
    assert listed.status_code == 200, (
        f"契约：Bearer GET /api/me/stars 必须 200，实际 {listed.status_code}；body={snippet(listed)}"
    )
    assert isinstance(listed.json().get("items"), list), (
        f"契约：GET /api/me/stars 响应需为 {{\"items\": [...]}}，实际 {snippet(listed)}"
    )

    created = api_client.post(
        "/api/me/stars",
        headers=bearer(token),
        json={"kind": "doi", "doi": "10.1234/biotriage.bearer", "title": "Bearer 验收标题"},
    )
    assert created.status_code == 201, (
        f"契约：Bearer POST /api/me/stars 必须 201，实际 {created.status_code}；body={snippet(created)}"
    )
    item = created.json().get("item") or {}
    item_id = star_id(item)
    assert item_id, f"契约：POST /api/me/stars 需返回可寻址的 item.id，实际 {snippet(created)}"

    after = api_client.get("/api/me/stars", headers=bearer(token)).json().get("items") or []
    assert any(st.get("id") == item_id for st in after), (
        f"契约：Bearer 写入的收藏应能被 Bearer 读到，期望含 id={item_id}，实际 {after!r}"[:400]
    )

    deleted = api_client.delete(f"/api/me/stars/{item_id}", headers=bearer(token))
    assert deleted.status_code == 204, (
        f"契约：Bearer DELETE /api/me/stars/{{id}} 必须 204，实际 {deleted.status_code}；body={snippet(deleted)}"
    )
    remaining = api_client.get("/api/me/stars", headers=bearer(token)).json().get("items") or []
    assert all(st.get("id") != item_id for st in remaining), (
        f"契约：删除后 Bearer GET /api/me/stars 不应再含 id={item_id}，实际 {remaining!r}"[:400]
    )


# ==================== 3. 无效 / 过期 / 已撤销 token ====================


def test_forged_token_is_401_invalid_token(client):
    """契约：伪造 token -> 401 {"auth": "invalid_token"}。"""
    forged = secrets.token_urlsafe(32)
    response = client.get("/api/auth/me", headers=bearer(forged))
    assert_invalid_token(response, "伪造 token 调用 /api/auth/me")

    stars = client.get("/api/me/stars", headers=bearer(forged))
    assert_invalid_token(stars, "伪造 token 调用 /api/me/stars")


def test_expired_token_is_401_invalid_token(client_of, api_module, app_env):
    """契约：过期 token -> 401 {"auth": "invalid_token"}。"""
    token = issue_token(client_of(api_module, ip="203.0.113.41"))
    api_client = client_of(api_module, ip="203.0.113.42", base_url="https://testserver")
    assert api_client.get("/api/auth/me", headers=bearer(token)).status_code == 200, (
        "测试前提：token 刚签发时必须可用（若此处失败，属于测试环境问题）"
    )

    expire_all_tokens(app_env["db_path"])

    response = api_client.get("/api/auth/me", headers=bearer(token))
    assert_invalid_token(response, "过期 token")


def test_revoked_token_is_401_invalid_token(client_of, api_module):
    """契约：POST /api/auth/logout（Bearer）撤销 token，之后同一 token -> 401。"""
    page_client = client_of(api_module, ip="203.0.113.51")
    token = issue_token(page_client)
    api_client = client_of(api_module, ip="203.0.113.52", base_url="https://testserver")

    assert api_client.get("/api/auth/me", headers=bearer(token)).status_code == 200, (
        "测试前提：token 刚签发时必须可用（若此处失败，属于测试环境问题）"
    )

    logout = api_client.post("/api/auth/logout", headers=bearer(token))
    assert logout.status_code == 200, (
        f"契约：Bearer POST /api/auth/logout 必须 200，实际 {logout.status_code}；body={snippet(logout)}"
    )
    assert logout.json().get("ok") is True, f"契约：logout 响应体应为 {{\"ok\": true}}，实际 {snippet(logout)}"

    assert_invalid_token(api_client.get("/api/auth/me", headers=bearer(token)), "已撤销 token")
    assert_invalid_token(
        api_client.get("/api/me/stars", headers=bearer(token)), "已撤销 token 访问 /api/me/stars"
    )


# ==================== 4. CORS 预检（跨站必过） ====================


def test_cors_preflight_allows_pages_origin_with_authorization(client):
    """契约：Origin=https://haonl-7.github.io + DELETE + Authorization 的预检必须放行。"""
    response = client.options(
        "/api/me/stars/1",
        headers={
            "Origin": PAGES_ORIGIN,
            "Access-Control-Request-Method": "DELETE",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert response.status_code in (200, 204), (
        f"契约：CORS 预检应 200/204，实际 {response.status_code}；body={snippet(response)}"
    )
    allow_origin = response.headers.get("access-control-allow-origin", "")
    assert allow_origin in (PAGES_ORIGIN, "*"), (
        f"契约：allow_origins 必须含 {PAGES_ORIGIN}，实际 Access-Control-Allow-Origin={allow_origin!r}"
    )
    allow_methods = (response.headers.get("access-control-allow-methods") or "").upper()
    assert "DELETE" in allow_methods, (
        f"契约：allow_methods 必须含 DELETE，实际 Access-Control-Allow-Methods={allow_methods!r}"
    )
    allow_headers = (response.headers.get("access-control-allow-headers") or "").lower()
    assert "authorization" in allow_headers, (
        f"契约：allow_headers 必须含 Authorization，实际 Access-Control-Allow-Headers={allow_headers!r}"
    )


def test_actual_bearer_request_carries_cors_header(client_of, api_module):
    """契约：真实跨站 GET 也要带 Access-Control-Allow-Origin（否则浏览器读不到响应体）。"""
    token = issue_token(client_of(api_module, ip="203.0.113.61"))
    api_client = client_of(api_module, ip="203.0.113.62", base_url="https://testserver")
    response = api_client.get(
        "/api/me/stars", headers={**bearer(token), "Origin": PAGES_ORIGIN}
    )
    assert response.status_code == 200, (
        f"契约：带 Origin 的 Bearer GET /api/me/stars 应 200，实际 {response.status_code}；body={snippet(response)}"
    )
    allow_origin = response.headers.get("access-control-allow-origin", "")
    assert allow_origin in (PAGES_ORIGIN, "*"), (
        f"契约：真实请求必须返回 Access-Control-Allow-Origin={PAGES_ORIGIN}，实际 {allow_origin!r}"
    )


# ==================== 5. 回归：cookie 流程不受影响 ====================


def test_cookie_session_still_works_without_bearer(client, login):
    """回归：不带 Authorization 时，cookie 登录流程与 /api/me/stars 行为不变。"""
    info = start_login(client)  # 不带 mode：现有 cookie 流程
    callback = send_callback(client, info["state"])
    assert callback.status_code == 302, (
        f"契约：cookie 流程 callback 必须 302，实际 {callback.status_code}；body={snippet(callback)}"
    )
    assert urlparse(callback.headers.get("location", "")).path == "/account", (
        f"契约：cookie 流程仍应 302 到 /account（原行为不得回归），"
        f"实际 Location={callback.headers.get('location')!r}"
    )
    assert SESSION_COOKIE in client.cookies, (
        f"契约：cookie 流程仍必须下发 {SESSION_COOKIE}，实际 cookie jar={dict(client.cookies)}"
    )

    me = client.get("/api/auth/me")
    assert me.status_code == 200, f"契约：cookie 会话 /api/auth/me 应 200，实际 {me.status_code}"
    body = me.json()
    assert body.get("authenticated") is True, f"契约：cookie 会话应已登录，实际 body={snippet(me)}"
    assert (body.get("user") or {}).get("orcid_id") == ORCID_USER_A, (
        f"契约：cookie 会话应解析出 {ORCID_USER_A}，实际 body={snippet(me)}"
    )

    stars = client.get("/api/me/stars")
    assert stars.status_code == 200, (
        f"契约：cookie 会话 GET /api/me/stars 应 200，实际 {stars.status_code}；body={snippet(stars)}"
    )


def test_anonymous_me_stays_200_without_token(client):
    """回归：无 token、无 cookie 时 /api/auth/me 仍返回 200 + authenticated=false（原有语义）。"""
    response = client.get("/api/auth/me")
    assert response.status_code == 200, (
        f"契约：未登录 /api/auth/me 应 200，实际 {response.status_code}；body={snippet(response)}"
    )
    assert response.json().get("authenticated") is False, (
        f"契约：未登录 authenticated 必须 false，实际 body={snippet(response)}"
    )
