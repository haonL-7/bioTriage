"""bioTriage 个人账户系统 —— 黑盒验收测试的公共夹具。

三条纪律：
1. 黑盒：只通过 fastapi.testclient.TestClient 打 api.app，不 import accounts 内部模块；
2. 隔离：每个测试一份独立临时 SQLite（tmp_path）+ 一份重新 import 的 api 模块；
3. 零真实网络：登录用 BT_TESTING=1 + BT_FAKE_ORCID 驱动，并在整个测试会话内封死 socket 建连。

失败信息里出现“契约”字样 = 权威契约未被实现满足；出现“测试”字样 = 测试自身环境问题。
"""

from __future__ import annotations

import importlib
import json
import socket
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---- 测试身份（BT_FAKE_ORCID 的 sub 就是 ORCID iD） ----
ORCID_USER_A = "0000-0002-1825-0097"
ORCID_USER_B = "0000-0001-5109-3700"
NAME_USER_A = "Josiah Carberry"
NAME_USER_B = "Alice Tester"

ORCID_SANDBOX = "https://sandbox.orcid.org"
APP_BASE_URL = "http://testserver"
DEFAULT_IP = "203.0.113.10"

SESSION_COOKIE = "bt_session"
SESSION_MAX_AGE = 2592000

# 契约要求的 429 配额错误体字段
QUOTA_ERROR_KEYS = ("detail", "tier", "limit", "used", "remaining", "resets_at")
# 契约未规定 tier 的取值，这里给出可接受的同义词集合
TIER_ALIASES = {
    "anon": {"anon", "anonymous", "guest"},
    "user": {"user", "orcid", "member", "account"},
}

BASE_ENV = {
    # api.py 模块级依赖，测试中永不真正调用外部模型
    "DEEPSEEK_API_KEY": "sk-test-placeholder-not-a-real-key",
    # 登录钩子：BT_TESTING=1 时回调直接采用这份 profile
    "BT_TESTING": "1",
    "BT_FAKE_ORCID": json.dumps({"sub": ORCID_USER_A, "name": NAME_USER_A}),
    # ORCID OIDC
    "ORCID_CLIENT_ID": "APP-TESTCLIENT0001",
    "ORCID_CLIENT_SECRET": "test-orcid-client-secret",
    "ORCID_BASE_URL": ORCID_SANDBOX,
    "APP_BASE_URL": APP_BASE_URL,
    # 配额 / 会话
    "AI_DAILY_QUOTA_PER_USER": "5",
    "AI_DAILY_QUOTA_ANON": "1",
    "SESSION_MAX_AGE": str(SESSION_MAX_AGE),
}


# ==================== 环境与模块隔离 ====================


def purge_app_modules() -> None:
    """丢掉已加载的 api / accounts 模块，使下一次 import 重新读取环境变量与 DATABASE_URL。"""
    for name in [m for m in list(sys.modules) if m == "api" or m == "accounts" or m.startswith("accounts.")]:
        del sys.modules[name]


def import_app():
    """按当前环境变量重新导入被测应用模块（黑盒：只取 api.app）。"""
    purge_app_modules()
    return importlib.import_module("api")


@pytest.fixture(scope="session", autouse=True)
def deny_outbound_network():
    """整个测试会话禁止真实网络：任何 socket 建连立即失败。

    BT_FAKE_ORCID 路径本就不发网络请求；外部模型调用会在端点的 try/except 内降级，
    因此封死 socket 只用来证明“测试期间零网络”，不会让用例假失败。
    """

    # 只放行回环地址：asyncio(Proactor) 用 socketpair 自建管道，Windows 下会 connect 127.0.0.1，
    # 真实网络（ORCID / 模型 API）一律不可能是回环，所以这个白名单既安全又必要。
    loopback = {"127.0.0.1", "::1", "localhost", "0.0.0.0", ""}
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_create_connection = socket.create_connection

    def _is_loopback(address) -> bool:
        host = address[0] if isinstance(address, tuple) and address else address
        if not isinstance(host, str):
            return False
        return host in loopback or host.startswith("127.")

    def _denied(*_args, **_kwargs):
        raise RuntimeError("黑盒测试期间禁止真实网络请求（检测到 socket 建连尝试）")

    def _guarded_connect(self, address):
        if _is_loopback(address):
            return original_connect(self, address)
        return _denied(address)

    def _guarded_connect_ex(self, address):
        if _is_loopback(address):
            return original_connect_ex(self, address)
        return _denied(address)

    def _guarded_create_connection(address, *args, **kwargs):
        if _is_loopback(address):
            return original_create_connection(address, *args, **kwargs)
        return _denied(address)

    monkey = pytest.MonkeyPatch()
    monkey.setattr(socket.socket, "connect", _guarded_connect)
    monkey.setattr(socket.socket, "connect_ex", _guarded_connect_ex)
    monkey.setattr(socket, "create_connection", _guarded_create_connection)
    try:
        yield
    finally:
        monkey.undo()


@pytest.fixture()
def base_env(tmp_path, monkeypatch):
    """一套可安全启动的环境变量（不含 SESSION_SECRET，由 app_env 补上）。"""
    db_path = tmp_path / "biotriage-test.db"
    env = dict(BASE_ENV)
    env["DATABASE_URL"] = "sqlite:///" + db_path.as_posix()
    # base_env 必须真的没有 SESSION_SECRET（验收 9 依赖这个前提）
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return {"env": env, "db_path": db_path}


@pytest.fixture()
def app_env(base_env, monkeypatch):
    """完整可用环境：在 base_env 之上给出 SESSION_SECRET。"""
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret-not-for-production")
    base_env["env"]["SESSION_SECRET"] = "test-session-secret-not-for-production"
    return base_env


@pytest.fixture()
def api_module(app_env):
    """按 app_env 重新导入的 api 模块对象。"""
    return import_app()


# ==================== TestClient ====================


@pytest.fixture()
def client_of():
    """构造并托管 TestClient；每个 client 有独立 cookie jar 与独立来源 IP。"""
    created = []

    def _make(module, *, ip=DEFAULT_IP, base_url=APP_BASE_URL):
        client = TestClient(
            module.app,
            base_url=base_url,
            follow_redirects=False,  # 契约里的 302 需要被观察到
            client=(ip, 51234),
        )
        client.__enter__()  # 触发 lifespan startup
        created.append(client)
        return client

    try:
        yield _make
    finally:
        for client in created:
            client.__exit__(None, None, None)


@pytest.fixture()
def client(client_of, api_module):
    return client_of(api_module)


@pytest.fixture()
def client_factory(client_of, api_module):
    """同一 app、不同来源 IP / base_url 的多个客户端（用于多用户与配额隔离）。"""

    def _make(*, ip=DEFAULT_IP, base_url=APP_BASE_URL):
        return client_of(api_module, ip=ip, base_url=base_url)

    return _make


# ==================== 登录流程（黑盒） ====================


def snippet(response, size=400) -> str:
    text = response.text if hasattr(response, "text") else str(response)
    return text[:size]


def start_login(client) -> dict:
    """GET /api/auth/login：断言授权跳转参数，返回 state。"""
    resp = client.get("/api/auth/login")
    assert resp.status_code == 302, (
        f"契约：GET /api/auth/login 必须 302 到 ORCID 授权页，实际 {resp.status_code}；body={snippet(resp)}"
    )
    location = resp.headers.get("location", "")
    url = urlparse(location)
    assert f"{url.scheme}://{url.netloc}" == ORCID_SANDBOX, (
        f"契约：login 应跳转到 ORCID_BASE_URL={ORCID_SANDBOX}，实际 Location={location!r}"
    )
    assert url.path == "/oauth/authorize", (
        f"契约：authorize_endpoint 为 <ORCID_BASE_URL>/oauth/authorize，实际 path={url.path!r}"
    )
    query = parse_qs(url.query, keep_blank_values=True)
    for key in ("client_id", "response_type", "scope", "redirect_uri", "state"):
        assert query.get(key), f"契约：authorize 跳转缺少参数 {key}，实际 query={url.query!r}"
    assert query["response_type"] == ["code"], f"契约：response_type 必须为 code，实际 {query['response_type']}"
    assert query["scope"] == ["openid"], f"契约：scope 仅 openid，实际 {query['scope']}"
    redirect_uri = query["redirect_uri"][0]
    assert redirect_uri.startswith(APP_BASE_URL), (
        f"契约：redirect_uri 必须由 APP_BASE_URL={APP_BASE_URL} 派生，实际 {redirect_uri!r}"
    )
    assert urlparse(redirect_uri).path == "/api/auth/callback", (
        f"契约：回调地址应为 /api/auth/callback，实际 {urlparse(redirect_uri).path!r}"
    )
    if not resp.headers.get_list("set-cookie"):
        raise AssertionError("契约：login 必须把 state 写入短时 cookie，但响应没有任何 Set-Cookie")
    return {"state": query["state"][0], "location": location, "query": query, "response": resp}


def send_callback(client, state_value: str, code: str = "fake-authorization-code"):
    """GET /api/auth/callback?code=...&state=..."""
    return client.get("/api/auth/callback", params={"code": code, "state": state_value})


def me_json(client) -> dict:
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200, (
        f"契约：GET /api/auth/me 必须 200，实际 {resp.status_code}；body={snippet(resp)}"
    )
    body = resp.json()
    assert isinstance(body.get("authenticated"), bool), (
        f"契约：/api/auth/me 需含布尔字段 authenticated，实际 body={json.dumps(body, ensure_ascii=False)[:400]}"
    )
    assert "user" in body, f"契约：/api/auth/me 需含 user 字段，实际 body={json.dumps(body, ensure_ascii=False)[:400]}"
    assert "quota" in body, f"契约：/api/auth/me 需含 quota 字段，实际 body={json.dumps(body, ensure_ascii=False)[:400]}"
    return body


def session_cookie_value(client) -> str:
    value = client.cookies.get(SESSION_COOKIE)
    assert value, f"契约：登录成功后应下发 cookie {SESSION_COOKIE}，实际 cookie jar={dict(client.cookies)}"
    return value


def session_set_cookie_header(response) -> str:
    headers = [h for h in response.headers.get_list("set-cookie") if h.lower().startswith(SESSION_COOKIE + "=")]
    assert headers, (
        f"契约：登录回调必须下发 {SESSION_COOKIE} cookie，实际 Set-Cookie={response.headers.get_list('set-cookie')}"
    )
    return headers[-1]


@pytest.fixture()
def login_as(client_of, api_module, monkeypatch):
    """以指定 ORCID 身份完成登录往返，返回登录后的 /api/auth/me 响应体。

    约定：BT_FAKE_ORCID 在每次回调时读取（多用户场景必须可切换）。
    若实现把它固化在 import 时，则回退为“第二个 app 实例（同一 SQLite）登录 + 注入无状态会话 cookie”。
    """

    def _login(client, orcid_id=ORCID_USER_A, name=NAME_USER_A):
        monkeypatch.setenv("BT_FAKE_ORCID", json.dumps({"sub": orcid_id, "name": name}))
        state_value = start_login(client)["state"]
        response = send_callback(client, state_value)
        assert response.status_code == 302, (
            f"契约：callback 成功后应 302 到 /account，实际 {response.status_code}；body={snippet(response)}"
        )
        location = response.headers.get("location", "")
        assert urlparse(location).path == "/account", (
            f"契约：callback 成功后应 302 到 /account，实际 Location={location!r}"
        )
        me = me_json(client)
        user = me.get("user") or {}
        if me.get("authenticated") and user.get("orcid_id") == orcid_id:
            return me

        other = client_of(import_app(), ip="198.51.100.99")
        state_other = start_login(other)["state"]
        send_callback(other, state_other)
        me_other = me_json(other)
        user_other = me_other.get("user") or {}
        assert me_other.get("authenticated") and user_other.get("orcid_id") == orcid_id, (
            "契约：BT_TESTING=1 + BT_FAKE_ORCID 时回调必须直接采用该 profile（sub 即 orcid_id）；"
            f"期望 orcid_id={orcid_id}，实际登录身份={json.dumps(user_other, ensure_ascii=False)[:300]}"
        )
        client.cookies.set(SESSION_COOKIE, session_cookie_value(other), domain="testserver", path="/")
        me = me_json(client)
        user = me.get("user") or {}
        assert me.get("authenticated") and user.get("orcid_id") == orcid_id, (
            "契约：会话 cookie 应可自证身份（base64(payload)+HMAC），"
            f"注入后实际 body={json.dumps(me, ensure_ascii=False)[:300]}"
        )
        return me

    return _login


@pytest.fixture()
def login(login_as):
    def _login(client, orcid_id=ORCID_USER_A, name=NAME_USER_A):
        return login_as(client, orcid_id, name)

    return _login


# ==================== 契约断言工具 ====================


def star_id(item: dict):
    """从收藏 item 里取出 DELETE /api/me/stars/{id} 需要的 id。"""
    for key in ("id", "star_id", "starId"):
        if key in item:
            return item[key]
    raise AssertionError(
        "契约：POST /api/me/stars 的 item 必须带可寻址的 id（DELETE /api/me/stars/{id} 依赖它），"
        f"实际 item={json.dumps(item, ensure_ascii=False)[:400]}"
    )


def assert_quota_error_body(body: dict, *, tier_group: str, limit: int) -> None:
    """校验 429 配额错误体契约：detail/tier/limit/used/remaining/resets_at。"""
    assert isinstance(body, dict), f"契约：429 响应必须是 JSON 对象，实际 {type(body).__name__}: {str(body)[:300]}"
    missing = [key for key in QUOTA_ERROR_KEYS if key not in body]
    assert not missing, (
        f"契约：429 响应体至少含 {list(QUOTA_ERROR_KEYS)}，缺少 {missing}；"
        f"实际 body={json.dumps(body, ensure_ascii=False)[:400]}"
    )
    assert body["limit"] == limit, (
        f"契约：limit 应为 {limit}，实际 {body['limit']}（body={json.dumps(body, ensure_ascii=False)[:300]}）"
    )
    assert int(body["used"]) >= limit, f"契约：used 应 >= limit={limit}，实际 {body['used']}"
    assert body["remaining"] == 0, f"契约：超额时 remaining 应为 0，实际 {body['remaining']}"
    assert str(body["tier"]).lower() in TIER_ALIASES[tier_group], (
        f"契约未固定 tier 字面量，本测试接受 {sorted(TIER_ALIASES[tier_group])}，实际 tier={body['tier']!r}"
    )
    assert body["resets_at"], f"契约：resets_at 不能为空，实际 {body['resets_at']!r}"
    assert body["detail"], f"契约：detail 不能为空，实际 {body['detail']!r}"


def table_names(db_path: Path) -> set:
    """不依赖 SQLAlchemy，直接用标准库读 SQLite 表清单。"""
    import sqlite3

    assert Path(db_path).exists(), (
        f"契约：启动时 create_all 应在 DATABASE_URL 指向的位置建库，实际文件不存在：{db_path}"
    )
    connection = sqlite3.connect(str(db_path))
    try:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    finally:
        connection.close()
    return {name for (name,) in rows}
