"""管理员功能契约：ADMIN_ORCIDS 白名单 / 封禁 / 配额覆盖 / token 撤销 / 概览 / 轻量迁移。

黑盒纪律沿用 conftest：只通过 fastapi.testclient.TestClient 打 api.app，
不 import accounts 内部模块；迁移用例用标准库 sqlite3 直接检查库产物
（与 conftest.table_names 同一手法，看的是"启动后数据库长什么样"，不是实现细节）。

失败信息里出现「契约」= 权威契约未被实现满足；出现「测试」= 测试自身环境问题。
"""

from __future__ import annotations

import json
import sqlite3
from urllib.parse import parse_qs, urlparse

import pytest

from conftest import (
    NAME_USER_A,
    ORCID_USER_A,
    assert_quota_error_body,
    import_app,
    send_callback,
    snippet,
    start_login,
)

ADMIN_ORCID = "0009-0008-1764-0908"
NON_ADMIN_ORCID = ORCID_USER_A  # 0000-0002-1825-0097
NAME_ADMIN = "Admin Operator"
# ORCID 校验位可以是 X：用来验证白名单的大小写容忍（X / x 必须等价）
CASE_ORCID = "0000-0002-1694-233X"
NAME_CASE = "Case Tester"

USERS_PATH = "/api/admin/users"
OVERVIEW_PATH = "/api/admin/overview"
PATCH_PATH = "/api/admin/users/{orcid_id}"
REVOKE_PATH = "/api/admin/users/{orcid_id}/revoke-tokens"
AI_PATH = "/api/ai_analyze"
QUERY = "乳酸菌 GG 对腹泻的证据等级"

AI_DAILY_QUOTA_PER_USER = 5  # conftest BASE_ENV 的值，也是契约里的全局默认

USER_ITEM_FIELDS = (
    "orcid_id",
    "name",
    "created_at",
    "is_banned",
    "quota_limit",
    "used_today",
    "effective_limit",
)


# ==================== 夹具与工具 ====================


@pytest.fixture()
def app_factory(app_env, monkeypatch):
    """按给定 ADMIN_ORCIDS 重新导入 api。

    admin_orcids=None -> 显式删除该变量（契约：未设置 = 没有任何管理员）。
    传字符串 -> 原样写入（含前后空格 / 大小写，交给被测实现自己归一化）。
    """

    def _make(admin_orcids=None):
        if admin_orcids is None:
            monkeypatch.delenv("ADMIN_ORCIDS", raising=False)
        else:
            monkeypatch.setenv("ADMIN_ORCIDS", admin_orcids)
        return import_app()

    return _make


def cookie_login(client, monkeypatch, orcid_id=NON_ADMIN_ORCID, name=NAME_USER_A):
    """走完 cookie 登录往返（BT_TESTING=1 + BT_FAKE_ORCID），返回 callback 响应。"""
    monkeypatch.setenv("BT_FAKE_ORCID", json.dumps({"sub": orcid_id, "name": name}))
    state_value = start_login(client)["state"]
    return send_callback(client, state_value)


def login_ok(client, monkeypatch, orcid_id=NON_ADMIN_ORCID, name=NAME_USER_A):
    """登录并断言契约里的成功路径（302 /account）。"""
    response = cookie_login(client, monkeypatch, orcid_id, name)
    assert response.status_code == 302, (
        f"契约：callback 成功后应 302 到 /account，实际 {response.status_code}；body={snippet(response)}"
    )
    location = response.headers.get("location", "")
    assert urlparse(location).path == "/account", (
        f"契约：callback 成功后应 302 到 /account，实际 Location={location!r}"
    )
    return response


def pages_token(client, monkeypatch, orcid_id=NON_ADMIN_ORCID, name=NAME_USER_A) -> str:
    """走 mode=pages 往返，取回 Bearer token 明文（在 URL fragment 里）。"""
    monkeypatch.setenv("BT_FAKE_ORCID", json.dumps({"sub": orcid_id, "name": name}))
    response = client.get("/api/auth/login", params={"mode": "pages"})
    assert response.status_code == 302, (
        f"契约：GET /api/auth/login?mode=pages 必须 302，实际 {response.status_code}；body={snippet(response)}"
    )
    state_value = parse_qs(urlparse(response.headers.get("location", "")).query).get("state", [""])[0]
    assert state_value, "契约：login 必须带 state 参数，实际 Location 里没有 state"
    callback = client.get("/api/auth/callback", params={"code": "fake-authorization-code", "state": state_value})
    assert callback.status_code == 302, (
        f"契约：mode=pages 的 callback 必须 302 回账户页，实际 {callback.status_code}；body={snippet(callback)}"
    )
    fragment = urlparse(callback.headers.get("location", "")).fragment
    token = parse_qs(fragment).get("t", [""])[0]
    assert token, f"契约：callback 应把 token 放在 URL fragment #t=<token>，实际 Location={fragment!r}"
    return token


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def me(client, **kwargs) -> dict:
    response = client.get("/api/auth/me", **kwargs)
    assert response.status_code == 200, (
        f"契约：GET /api/auth/me 应 200，实际 {response.status_code}；body={snippet(response)}"
    )
    return response.json()


def ai_call(client, attempt: int = 1):
    return client.get(AI_PATH, params={"query": f"{QUERY} #{attempt}"})


def assert_forbidden(response, label: str) -> None:
    """契约：未授权访问 /api/admin/* -> 403 {"detail":"需要管理员权限","auth":"forbidden"}。"""
    assert response.status_code == 403, (
        f"契约：{label} 在无权访问时必须 403（不是 401/200），实际 {response.status_code}；"
        f"body={snippet(response)}"
    )
    body = response.json()
    assert body.get("auth") == "forbidden", (
        f"契约：{label} 的 403 响应体需含 auth=\"forbidden\"，实际 body={snippet(response)}"
    )
    detail = str(body.get("detail") or "")
    assert "管理员权限" in detail, (
        f"契约：{label} 的 403 detail 应为「需要管理员权限」，实际 detail={detail!r}"
    )


def assert_invalid_token(response, label: str) -> None:
    """契约：被封禁用户的 token 视同无效 -> 401 {"detail":"...","auth":"invalid_token"}。"""
    assert response.status_code == 401, (
        f"契约：{label} 必须 401，实际 {response.status_code}；body={snippet(response)}"
    )
    body = response.json()
    assert body.get("auth") == "invalid_token", (
        f"契约：{label} 的 401 响应体需含 auth=\"invalid_token\"，实际 body={snippet(response)}"
    )
    assert body.get("detail"), f"契约：{label} 的 401 响应体需含非空 detail，实际 body={snippet(response)}"


def assert_user_item(item: dict, label: str) -> None:
    missing = [key for key in USER_ITEM_FIELDS if key not in item]
    assert not missing, (
        f"契约：{label} 的用户条目需含 {list(USER_ITEM_FIELDS)}，缺少 {missing}；"
        f"实际 item={json.dumps(item, ensure_ascii=False)[:400]}"
    )


def find_user(items, orcid_id: str) -> dict:
    for item in items:
        if str(item.get("orcid_id", "")).lower() == orcid_id.lower():
            return item
    listed = json.dumps(items, ensure_ascii=False)[:400]
    raise AssertionError(f"契约：GET {USERS_PATH} 的 items 应包含用户 {orcid_id}，实际 items={listed}")


def list_users(admin, *, limit: int | None = None, offset: int | None = None) -> dict:
    params = {}
    if limit is not None:
        params["limit"] = limit
    if offset is not None:
        params["offset"] = offset
    response = admin.get(USERS_PATH, params=params or None)
    assert response.status_code == 200, (
        f"契约：GET {USERS_PATH} 管理员应 200，实际 {response.status_code}；body={snippet(response)}"
    )
    body = response.json()
    assert isinstance(body.get("items"), list), (
        f"契约：GET {USERS_PATH} 需返回 items 列表，实际 body={json.dumps(body, ensure_ascii=False)[:400]}"
    )
    assert isinstance(body.get("total"), int), (
        f"契约：GET {USERS_PATH} 需返回整数 total，实际 body={json.dumps(body, ensure_ascii=False)[:400]}"
    )
    return body


def patch_user(admin, orcid_id: str, payload: dict):
    return admin.patch(PATCH_PATH.format(orcid_id=orcid_id), json=payload)


def patch_user_ok(admin, orcid_id: str, payload: dict) -> dict:
    response = patch_user(admin, orcid_id, payload)
    assert response.status_code == 200, (
        f"契约：PATCH {PATCH_PATH.format(orcid_id=orcid_id)} body={payload} 管理员应 200，"
        f"实际 {response.status_code}；body={snippet(response)}"
    )
    body = response.json()
    assert body.get("ok") is True, (
        f"契约：PATCH 成功需返回 {{\"ok\":true,\"user\":{{...}}}}，实际 body={snippet(response)}"
    )
    user = body.get("user")
    assert isinstance(user, dict), (
        f"契约：PATCH 成功需返回 user 对象，实际 body={snippet(response)}"
    )
    assert_user_item(user, "PATCH 响应的 user")
    return user


def revoke_tokens(admin, orcid_id: str) -> dict:
    response = admin.post(REVOKE_PATH.format(orcid_id=orcid_id))
    assert response.status_code == 200, (
        f"契约：POST {REVOKE_PATH.format(orcid_id=orcid_id)} 管理员应 200，实际 {response.status_code}；"
        f"body={snippet(response)}"
    )
    body = response.json()
    assert body.get("ok") is True, (
        f"契约：撤销 token 成功需返回 {{\"ok\":true,\"revoked\":N}}，实际 body={snippet(response)}"
    )
    assert isinstance(body.get("revoked"), int), (
        f"契约：撤销 token 需返回整数 revoked，实际 body={snippet(response)}"
    )
    return body


def overview(admin) -> dict:
    response = admin.get(OVERVIEW_PATH)
    assert response.status_code == 200, (
        f"契约：GET {OVERVIEW_PATH} 管理员应 200，实际 {response.status_code}；body={snippet(response)}"
    )
    body = response.json()
    missing = [key for key in ("total_users", "banned_users", "ai_calls_today", "admins", "now_utc") if key not in body]
    assert not missing, (
        f"契约：GET {OVERVIEW_PATH} 需含 total_users/banned_users/ai_calls_today/admins/now_utc，"
        f"缺少 {missing}；实际 body={json.dumps(body, ensure_ascii=False)[:400]}"
    )
    return body


# ==================== 1. 管理员身份判定 ====================


def test_admin_endpoints_403_when_admin_orcids_unset(app_factory, client_of, monkeypatch):
    """契约：ADMIN_ORCIDS 未设置 -> 没有任何管理员（不得默认放行任何人）。"""
    api = app_factory(None)
    client = client_of(api, ip="198.51.100.71")
    login_ok(client, monkeypatch, NON_ADMIN_ORCID, NAME_USER_A)

    me_body = me(client)
    assert me_body.get("is_admin") is False, (
        "契约：ADMIN_ORCIDS 未设置时 /api/auth/me 的 is_admin 必须为 false，"
        f"实际 body={json.dumps(me_body, ensure_ascii=False)[:400]}"
    )
    assert_forbidden(client.get(USERS_PATH), "GET /api/admin/users")
    assert_forbidden(client.get(OVERVIEW_PATH), "GET /api/admin/overview")
    assert_forbidden(patch_user(client, NON_ADMIN_ORCID, {"banned": False}), "PATCH /api/admin/users/{orcid_id}")
    assert_forbidden(client.post(REVOKE_PATH.format(orcid_id=NON_ADMIN_ORCID)), "POST /api/admin/users/{orcid_id}/revoke-tokens")


def test_admin_endpoints_403_when_caller_not_in_whitelist(app_factory, client_of, monkeypatch):
    """契约：ADMIN_ORCIDS 设了，但当前登录用户不在名单 -> 403。"""
    api = app_factory(ADMIN_ORCID)
    client = client_of(api, ip="198.51.100.72")
    login_ok(client, monkeypatch, NON_ADMIN_ORCID, NAME_USER_A)

    me_body = me(client)
    assert me_body.get("is_admin") is False, (
        "契约：不在 ADMIN_ORCIDS 名单内的登录用户 is_admin 必须为 false，"
        f"实际 body={json.dumps(me_body, ensure_ascii=False)[:400]}"
    )
    assert_forbidden(client.get(USERS_PATH), "GET /api/admin/users")
    assert_forbidden(client.get(OVERVIEW_PATH), "GET /api/admin/overview")
    assert_forbidden(patch_user(client, NON_ADMIN_ORCID, {"quota_limit": 3}), "PATCH /api/admin/users/{orcid_id}")
    assert_forbidden(client.post(REVOKE_PATH.format(orcid_id=NON_ADMIN_ORCID)), "POST /api/admin/users/{orcid_id}/revoke-tokens")


def test_admin_endpoints_403_for_anonymous_caller(app_factory, client_of):
    """契约：管理员端点一律要求管理员身份，未登录同样 403 forbidden（不是 401/200）。"""
    api = app_factory(ADMIN_ORCID)
    client = client_of(api, ip="198.51.100.73")

    assert_forbidden(client.get(USERS_PATH), "未登录 GET /api/admin/users")
    assert_forbidden(client.get(OVERVIEW_PATH), "未登录 GET /api/admin/overview")


def test_whitelist_tolerates_spaces_and_case(app_factory, client_of, monkeypatch):
    """契约：ADMIN_ORCIDS 解析要容忍前后空格与大小写（X/x 等价）。"""
    api = app_factory(f"  {NON_ADMIN_ORCID.lower()} , {CASE_ORCID.lower()}  ")
    client = client_of(api, ip="198.51.100.74")
    login_ok(client, monkeypatch, CASE_ORCID, NAME_CASE)

    me_body = me(client)
    assert me_body.get("is_admin") is True, (
        f"契约：ADMIN_ORCIDS=\"  {CASE_ORCID.lower()} \" 应让 {CASE_ORCID} 成为管理员（容忍空格与大小写），"
        f"实际 is_admin={me_body.get('is_admin')!r}；body={json.dumps(me_body, ensure_ascii=False)[:400]}"
    )
    body = list_users(client, limit=5, offset=0)
    assert body["total"] >= 1


# ==================== 2. 用户列表 / used_today / effective_limit ====================


def test_admin_users_list_reports_used_today_and_effective_limit(app_factory, client_of, monkeypatch):
    """契约：GET /api/admin/users 列出用户，并给出 used_today 与 effective_limit。"""
    api = app_factory(ADMIN_ORCID)
    admin = client_of(api, ip="198.51.100.75")
    login_ok(admin, monkeypatch, ADMIN_ORCID, NAME_ADMIN)

    user = client_of(api, ip="198.51.100.76")
    login_ok(user, monkeypatch, NON_ADMIN_ORCID, NAME_USER_A)
    first = ai_call(user, 1)
    assert first.status_code != 429, (
        f"契约：默认配额 {AI_DAILY_QUOTA_PER_USER} 次/天，第 1 次 /api/ai_analyze 不应 429，"
        f"实际 {first.status_code}；body={snippet(first)}"
    )

    body = list_users(admin, limit=50, offset=0)
    item = find_user(body["items"], NON_ADMIN_ORCID)
    assert_user_item(item, "GET /api/admin/users")
    assert item["used_today"] == 1, (
        f"契约：used_today 应等于该用户今日（UTC）已用 AI 次数 1，实际 {item['used_today']!r}；"
        f"item={json.dumps(item, ensure_ascii=False)[:400]}"
    )
    assert item["effective_limit"] == AI_DAILY_QUOTA_PER_USER, (
        f"契约：quota_limit 为 NULL 时 effective_limit 应取全局默认 {AI_DAILY_QUOTA_PER_USER}，"
        f"实际 {item['effective_limit']!r}；item={json.dumps(item, ensure_ascii=False)[:400]}"
    )
    assert item["quota_limit"] is None, (
        f"契约：未单独设置配额的用户 quota_limit 应为 null，实际 {item['quota_limit']!r}"
    )
    assert item["is_banned"] is False, (
        f"契约：正常用户 is_banned 应为 false，实际 {item['is_banned']!r}"
    )
    assert body["total"] == 2, (
        f"契约：total 应为库内用户总数（管理员 + 普通用户 = 2），实际 {body['total']!r}；"
        f"items={json.dumps(body['items'], ensure_ascii=False)[:400]}"
    )

    paged = list_users(admin, limit=1, offset=0)
    assert len(paged["items"]) == 1, (
        f"契约：limit=1 时最多返回 1 条，实际 {len(paged['items'])} 条；"
        f"items={json.dumps(paged['items'], ensure_ascii=False)[:400]}"
    )
    assert paged["total"] == 2, (
        f"契约：total 是不受 limit 影响的总数，expected 2，实际 {paged['total']!r}"
    )


# ==================== 3. 配额覆盖 ====================


def test_patch_quota_limit_overrides_and_null_restores_default(app_factory, client_of, monkeypatch):
    """契约：PATCH quota_limit 生效（个人上限覆盖全局默认），置 null 回落默认。"""
    api = app_factory(ADMIN_ORCID)
    admin = client_of(api, ip="198.51.100.77")
    login_ok(admin, monkeypatch, ADMIN_ORCID, NAME_ADMIN)
    user = client_of(api, ip="198.51.100.78")
    login_ok(user, monkeypatch, NON_ADMIN_ORCID, NAME_USER_A)

    patched = patch_user_ok(admin, NON_ADMIN_ORCID, {"quota_limit": 2})
    assert patched["quota_limit"] == 2, (
        f"契约：PATCH quota_limit=2 后 user.quota_limit 应为 2，实际 {patched['quota_limit']!r}"
    )
    assert patched["effective_limit"] == 2, (
        f"契约：PATCH quota_limit=2 后 effective_limit 应为 2，实际 {patched['effective_limit']!r}"
    )

    quota = user.get("/api/me/quota")
    assert quota.status_code == 200, (
        f"契约：GET /api/me/quota 应 200，实际 {quota.status_code}；body={snippet(quota)}"
    )
    assert quota.json().get("limit") == 2, (
        f"契约：个人上限 2 生效时 /api/me/quota 的 limit 应为 2，实际 {quota.json().get('limit')!r}；"
        f"body={snippet(quota)}"
    )

    for attempt in (1, 2):
        response = ai_call(user, attempt)
        assert response.status_code != 429, (
            f"契约：个人上限 2 时第 {attempt} 次 /api/ai_analyze 不应 429，实际 {response.status_code}；"
            f"body={snippet(response)}"
        )
    third = ai_call(user, 3)
    assert third.status_code == 429, (
        f"契约：个人上限 2 时第 3 次 /api/ai_analyze 必须 429，实际 {third.status_code}；body={snippet(third)}"
    )
    assert_quota_error_body(third.json(), tier_group="user", limit=2)

    restored = patch_user_ok(admin, NON_ADMIN_ORCID, {"quota_limit": None})
    assert restored["quota_limit"] is None, (
        f"契约：PATCH quota_limit=null 后 user.quota_limit 应为 null，实际 {restored['quota_limit']!r}"
    )
    assert restored["effective_limit"] == AI_DAILY_QUOTA_PER_USER, (
        f"契约：quota_limit=null 后 effective_limit 应回落全局默认 {AI_DAILY_QUOTA_PER_USER}，"
        f"实际 {restored['effective_limit']!r}"
    )
    quota_after = user.get("/api/me/quota").json()
    assert quota_after.get("limit") == AI_DAILY_QUOTA_PER_USER, (
        f"契约：quota_limit=null 后 /api/me/quota 的 limit 应回落到 {AI_DAILY_QUOTA_PER_USER}，"
        f"实际 {quota_after.get('limit')!r}"
    )

    recovered = ai_call(user, 4)
    assert recovered.status_code != 429, (
        "契约：配额从 2 恢复到默认 5 后，今日已用 2 次的用户第 3 次调用不应再被限额，"
        f"实际 {recovered.status_code}；body={snippet(recovered)}"
    )


def test_patch_quota_limit_rejects_invalid_values(app_factory, client_of, monkeypatch):
    """契约：quota_limit 必须是 null 或 1..1000 的整数，否则 400。"""
    api = app_factory(ADMIN_ORCID)
    admin = client_of(api, ip="198.51.100.79")
    login_ok(admin, monkeypatch, ADMIN_ORCID, NAME_ADMIN)
    user = client_of(api, ip="198.51.100.80")
    login_ok(user, monkeypatch, NON_ADMIN_ORCID, NAME_USER_A)

    for bad in (0, 1001, "abc"):
        response = patch_user(admin, NON_ADMIN_ORCID, {"quota_limit": bad})
        assert response.status_code == 400, (
            f"契约：quota_limit={bad!r} 是非法值，PATCH 必须 400（不是 200/422），"
            f"实际 {response.status_code}；body={snippet(response)}"
        )

    boundary = patch_user(admin, NON_ADMIN_ORCID, {"quota_limit": 1000})
    assert boundary.status_code == 200, (
        f"契约：quota_limit=1000 是合法上界，PATCH 应 200，实际 {boundary.status_code}；body={snippet(boundary)}"
    )
    assert boundary.json().get("user", {}).get("quota_limit") == 1000, (
        f"契约：quota_limit=1000 应被保存，实际 body={snippet(boundary)}"
    )


# ==================== 4. 封禁语义 ====================


def test_ban_revokes_tokens_blocks_callback_and_unban_restores_login(app_factory, client_of, monkeypatch):
    """契约：封禁 -> 旧 token 立刻 401 invalid_token + 重走 callback 403 banned；解封可重新登录。"""
    api = app_factory(ADMIN_ORCID)
    admin = client_of(api, ip="198.51.100.81")
    login_ok(admin, monkeypatch, ADMIN_ORCID, NAME_ADMIN)

    user = client_of(api, ip="198.51.100.82")
    login_ok(user, monkeypatch, NON_ADMIN_ORCID, NAME_USER_A)
    token = pages_token(user, monkeypatch, NON_ADMIN_ORCID, NAME_USER_A)
    before = user.get("/api/auth/me", headers=bearer(token))
    assert before.status_code == 200 and before.json().get("authenticated") is True, (
        f"测试：封禁前 token 应可用（200 + authenticated:true），实际 {before.status_code}；body={snippet(before)}"
    )

    banned = patch_user_ok(admin, NON_ADMIN_ORCID, {"banned": True})
    assert banned["is_banned"] is True, (
        f"契约：PATCH banned=true 后 user.is_banned 应为 true，实际 {banned['is_banned']!r}"
    )

    assert_invalid_token(user.get("/api/auth/me", headers=bearer(token)), "封禁后 token 访问 /api/auth/me")
    assert_invalid_token(user.get("/api/me/quota", headers=bearer(token)), "封禁后 token 访问 /api/me/quota")
    assert_invalid_token(user.get("/api/me/stars", headers=bearer(token)), "封禁后 token 访问 /api/me/stars")

    callback = cookie_login(user, monkeypatch, NON_ADMIN_ORCID, NAME_USER_A)
    assert callback.status_code == 403, (
        "契约：被封禁用户再次走登录回调，callback 必须 403 拒绝，"
        f"实际 {callback.status_code}；body={snippet(callback)}"
    )
    body = callback.json()
    assert body.get("auth") == "banned", (
        f"契约：被封禁用户 callback 的 403 响应体需含 auth=\"banned\"，实际 body={snippet(callback)}"
    )
    assert "封禁" in str(body.get("detail") or ""), (
        f"契约：被封禁用户 callback 的 detail 应为「账号已被封禁」，实际 detail={body.get('detail')!r}"
    )
    set_cookie = [header for header in callback.headers.get_list("set-cookie") if header.lower().startswith("bt_session=")]
    assert not set_cookie, (
        f"契约：被封禁用户 callback 不得签发会话 token，实际 Set-Cookie={set_cookie}"
    )

    unbanned = patch_user_ok(admin, NON_ADMIN_ORCID, {"banned": False})
    assert unbanned["is_banned"] is False, (
        f"契约：PATCH banned=false 后 user.is_banned 应为 false，实际 {unbanned['is_banned']!r}"
    )
    # 解封后旧 token 必须仍然无效：这证明封禁时真的把这枚 token 写进了 revoked_at
    # （而不是只靠 is_banned 在请求时临时挡掉——那样解封就等于把旧 token 全部复活了）。
    assert_invalid_token(
        user.get("/api/auth/me", headers=bearer(token)),
        "解封后封禁前签发的 token（封禁时必须已撤销）",
    )
    login_ok(user, monkeypatch, NON_ADMIN_ORCID, NAME_USER_A)
    after = me(user)
    assert after.get("authenticated") is True, (
        f"契约：解封后应能正常登录，实际 /api/auth/me={json.dumps(after, ensure_ascii=False)[:400]}"
    )
    assert (after.get("user") or {}).get("orcid_id") == NON_ADMIN_ORCID, (
        f"契约：解封后登录身份应为 {NON_ADMIN_ORCID}，实际 user={(after.get('user') or {})}"
    )


def test_ban_invalidates_existing_session_cookie(app_factory, client_of, monkeypatch):
    """契约：被封禁用户「token 视为无效」-> 携带既有会话 cookie 访问 /api/auth/me 也是 401 invalid_token。"""
    api = app_factory(ADMIN_ORCID)
    admin = client_of(api, ip="198.51.100.83")
    login_ok(admin, monkeypatch, ADMIN_ORCID, NAME_ADMIN)
    user = client_of(api, ip="198.51.100.84")
    login_ok(user, monkeypatch, NON_ADMIN_ORCID, NAME_USER_A)

    assert me(user).get("authenticated") is True, "测试：封禁前会话 cookie 应有效"
    patch_user_ok(admin, NON_ADMIN_ORCID, {"banned": True})

    assert_invalid_token(user.get("/api/auth/me"), "封禁后会话 cookie 访问 /api/auth/me")
    assert_invalid_token(user.get("/api/me/quota"), "封禁后会话 cookie 访问 /api/me/quota")


def test_revoke_tokens_invalidates_existing_tokens(app_factory, client_of, monkeypatch):
    """契约：POST revoke-tokens 撤销该用户所有未撤销 token，旧 token 立即 401。"""
    api = app_factory(ADMIN_ORCID)
    admin = client_of(api, ip="198.51.100.85")
    login_ok(admin, monkeypatch, ADMIN_ORCID, NAME_ADMIN)

    user = client_of(api, ip="198.51.100.86")
    login_ok(user, monkeypatch, NON_ADMIN_ORCID, NAME_USER_A)
    first_token = pages_token(user, monkeypatch, NON_ADMIN_ORCID, NAME_USER_A)
    second_token = pages_token(user, monkeypatch, NON_ADMIN_ORCID, NAME_USER_A)
    for label, token in (("第一枚", first_token), ("第二枚", second_token)):
        response = user.get("/api/auth/me", headers=bearer(token))
        assert response.status_code == 200, (
            f"测试：撤销前{label} token 应可用，实际 {response.status_code}；body={snippet(response)}"
        )

    body = revoke_tokens(admin, NON_ADMIN_ORCID)
    assert body["revoked"] == 2, (
        f"契约：该用户有 2 枚未撤销 token，revoked 应为 2，实际 {body['revoked']!r}；body={json.dumps(body, ensure_ascii=False)[:300]}"
    )
    assert_invalid_token(user.get("/api/auth/me", headers=bearer(first_token)), "撤销后第一枚 token")
    assert_invalid_token(user.get("/api/me/quota", headers=bearer(second_token)), "撤销后第二枚 token")

    again = revoke_tokens(admin, NON_ADMIN_ORCID)
    assert again["revoked"] == 0, (
        f"契约：已无可撤销 token 时 revoked 应为 0，实际 {again['revoked']!r}"
    )


# ==================== 5. 概览 ====================


def test_overview_counts_users_bans_and_ai_calls(app_factory, client_of, monkeypatch):
    """契约：GET /api/admin/overview 的 total_users / banned_users / ai_calls_today / admins / now_utc 都要对。"""
    api = app_factory(ADMIN_ORCID)
    admin = client_of(api, ip="198.51.100.87")
    login_ok(admin, monkeypatch, ADMIN_ORCID, NAME_ADMIN)
    user = client_of(api, ip="198.51.100.88")
    login_ok(user, monkeypatch, NON_ADMIN_ORCID, NAME_USER_A)
    for attempt in (1, 2):
        response = ai_call(user, attempt)
        assert response.status_code != 429, (
            f"测试：默认配额 {AI_DAILY_QUOTA_PER_USER} 次/天，第 {attempt} 次 AI 调用不应 429，"
            f"实际 {response.status_code}；body={snippet(response)}"
        )

    body = overview(admin)
    assert body["total_users"] == 2, (
        f"契约：total_users 应为库内用户数 2，实际 {body['total_users']!r}；body={json.dumps(body, ensure_ascii=False)[:400]}"
    )
    assert body["banned_users"] == 0, (
        f"契约：banned_users 应为 0，实际 {body['banned_users']!r}"
    )
    assert body["ai_calls_today"] == 2, (
        f"契约：ai_calls_today 应为今日 AI 调用次数总和 2，实际 {body['ai_calls_today']!r}；"
        f"body={json.dumps(body, ensure_ascii=False)[:400]}"
    )
    admins = body["admins"]
    assert isinstance(admins, list) and any(str(item).lower() == ADMIN_ORCID.lower() for item in admins), (
        f"契约：admins 应包含白名单里的 {ADMIN_ORCID}，实际 {admins!r}"
    )
    assert isinstance(body["now_utc"], str) and body["now_utc"], (
        f"契约：now_utc 应为非空 UTC 时间字符串，实际 {body['now_utc']!r}"
    )

    patch_user_ok(admin, NON_ADMIN_ORCID, {"banned": True})
    banned_body = overview(admin)
    assert banned_body["banned_users"] == 1, (
        f"契约：封禁 1 人后 banned_users 应为 1，实际 {banned_body['banned_users']!r}；"
        f"body={json.dumps(banned_body, ensure_ascii=False)[:400]}"
    )
    assert banned_body["total_users"] == 2, (
        f"契约：封禁不改变 total_users（仍为 2），实际 {banned_body['total_users']!r}"
    )


# ==================== 6. 回归：非管理员的 /api/me/* 不受影响 ====================


def test_non_admin_me_endpoints_stay_working(app_factory, client_of, monkeypatch):
    """回归：引入管理员功能后，普通用户的 /api/me/* 与 /api/auth/me 行为不变。"""
    api = app_factory(ADMIN_ORCID)
    user = client_of(api, ip="198.51.100.89")
    login_ok(user, monkeypatch, NON_ADMIN_ORCID, NAME_USER_A)

    body = me(user)
    assert body.get("authenticated") is True, (
        f"契约：普通用户登录后 /api/auth/me 的 authenticated 应为 true，实际 body={snippet(user.get('/api/auth/me'))}"
    )
    assert body.get("is_admin") is False, (
        f"契约：普通用户的 is_admin 应为 false，实际 {body.get('is_admin')!r}"
    )

    quota = user.get("/api/me/quota")
    assert quota.status_code == 200, (
        f"契约：普通用户 GET /api/me/quota 应 200，实际 {quota.status_code}；body={snippet(quota)}"
    )
    assert quota.json().get("limit") == AI_DAILY_QUOTA_PER_USER, (
        f"契约：普通用户限额仍取全局默认 {AI_DAILY_QUOTA_PER_USER}，实际 {quota.json().get('limit')!r}"
    )

    stars = user.get("/api/me/stars")
    assert stars.status_code == 200, (
        f"契约：普通用户 GET /api/me/stars 应 200，实际 {stars.status_code}；body={snippet(stars)}"
    )
    created = user.post("/api/me/stars", json={"kind": "strain", "title": "管理员功能回归用收藏"})
    assert created.status_code == 201, (
        f"契约：普通用户 POST /api/me/stars 应 201，实际 {created.status_code}；body={snippet(created)}"
    )


# ==================== 7. 轻量迁移（旧库 -> 新列，幂等，不丢数据） ====================

LEGACY_ORCID = "0000-0003-1415-9269"
LEGACY_NAME = "Legacy User"
LEGACY_CREATED_AT = "2024-01-01 00:00:00.000000"


def _create_legacy_users_table(db_path):
    """构造「改动前」的 users 表：只有 orcid_id / name / created_at 三列，且已有一条数据。"""
    connection = sqlite3.connect(str(db_path))
    try:
        connection.execute(
            "CREATE TABLE users ("
            "orcid_id VARCHAR(64) NOT NULL, "
            "name VARCHAR(255) NOT NULL, "
            "created_at DATETIME NOT NULL, "
            "PRIMARY KEY (orcid_id))"
        )
        connection.execute(
            "INSERT INTO users (orcid_id, name, created_at) VALUES (?, ?, ?)",
            (LEGACY_ORCID, LEGACY_NAME, LEGACY_CREATED_AT),
        )
        connection.commit()
    finally:
        connection.close()


def _columns(db_path) -> dict:
    connection = sqlite3.connect(str(db_path))
    try:
        rows = connection.execute("PRAGMA table_info(users)").fetchall()
    finally:
        connection.close()
    # (cid, name, type, notnull, dflt_value, pk)
    return {row[1]: {"type": row[2], "notnull": row[3], "default": row[4]} for row in rows}


def _users(db_path) -> dict:
    connection = sqlite3.connect(str(db_path))
    try:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT * FROM users").fetchall()
    finally:
        connection.close()
    return {row["orcid_id"]: dict(row) for row in rows}


def test_migration_upgrades_legacy_sqlite_schema_without_losing_data(app_env, app_factory, client_of, monkeypatch, tmp_path):
    """契约：启动时用 inspector 检查缺列并 ALTER TABLE ADD COLUMN；幂等；不得丢既有用户数据。

    这里用手工构造的「旧结构 SQLite 库」（users 只有 3 列 + 1 条数据）启动两次，
    检查新列是否补齐、旧数据是否还在、两次启动之间是否稳定。
    """
    legacy_db = tmp_path / "legacy-schema.db"
    _create_legacy_users_table(legacy_db)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + legacy_db.as_posix())

    def startup(label: str, ip: str):
        """建一个 client（触发 lifespan 启动）+ 打一次账户端点（触发懒加载建表/迁移）。

        本用例只关心"启动后数据库长什么样"，不校验该端点的状态码——
        迁移与 auth_me 是否可用是两件事，耦合会让本用例的失败信息指向错误原因。
        """
        module = app_factory(ADMIN_ORCID)
        client = client_of(module, ip=ip)
        client.get("/api/auth/me")
        return client

    startup("第一次", "198.51.100.90")

    columns = _columns(legacy_db)
    for name in ("is_banned", "quota_limit"):
        assert name in columns, (
            f"契约：轻量迁移必须给 users 补列 {name}（inspector 检查 + ALTER TABLE ADD COLUMN），"
            f"实际列={sorted(columns)}"
        )
    assert columns["is_banned"]["notnull"] == 1, (
        "契约：is_banned 必须是 BOOLEAN NOT NULL DEFAULT false，"
        f"实际 PRAGMA table_info={columns['is_banned']}"
    )
    assert columns["quota_limit"]["notnull"] == 0, (
        f"契约：quota_limit 必须可空（NULL = 用全局默认），实际 PRAGMA table_info={columns['quota_limit']}"
    )

    rows = _users(legacy_db)
    assert LEGACY_ORCID in rows, (
        f"契约：迁移不得丢既有用户数据，实际 users 表主键={sorted(rows)}"
    )
    legacy_row = rows[LEGACY_ORCID]
    assert legacy_row["name"] == LEGACY_NAME, (
        f"契约：迁移不得改既有数据，期望 name={LEGACY_NAME!r}，实际 {legacy_row['name']!r}"
    )
    assert int(legacy_row["is_banned"] or 0) == 0, (
        f"契约：既有用户的 is_banned 应取列默认值 false，实际 {legacy_row['is_banned']!r}"
    )
    assert legacy_row["quota_limit"] is None, (
        f"契约：既有用户的 quota_limit 应取 NULL（= 全局默认），实际 {legacy_row['quota_limit']!r}"
    )

    startup("第二次", "198.51.100.91")
    columns_again = _columns(legacy_db)
    assert columns_again.keys() == columns.keys(), (
        f"契约：迁移必须可重复执行（幂等），两次启动后列集合应一致，"
        f"第一次={sorted(columns)} 第二次={sorted(columns_again)}"
    )
    rows_again = _users(legacy_db)
    assert LEGACY_ORCID in rows_again and rows_again[LEGACY_ORCID]["name"] == LEGACY_NAME, (
        f"契约：第二次启动不得丢既有数据，实际 users={json.dumps({k: dict(v) for k, v in rows_again.items()}, ensure_ascii=False, default=str)[:400]}"
    )
