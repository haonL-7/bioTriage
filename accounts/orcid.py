"""ORCID OIDC 客户端（authorization code + openid scope，client_secret_post）。"""
import json
import os
from urllib.parse import urlencode

import requests

DEFAULT_BASE_URL = "https://sandbox.orcid.org"
TIMEOUT = 15


class OrcidError(Exception):
    """ORCID 交互失败（配置缺失或远端错误）。"""


def base_url() -> str:
    return (os.getenv("ORCID_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def client_id() -> str:
    return (os.getenv("ORCID_CLIENT_ID") or "").strip()


def client_secret() -> str:
    return (os.getenv("ORCID_CLIENT_SECRET") or "").strip()


def authorization_endpoint() -> str:
    return f"{base_url()}/oauth/authorize"


def token_endpoint() -> str:
    return f"{base_url()}/oauth/token"


def userinfo_endpoint() -> str:
    return f"{base_url()}/oauth/userinfo"


def fake_profile() -> dict | None:
    """测试钩子：BT_TESTING=1 且 BT_FAKE_ORCID 给出 profile 时不发起任何网络请求。"""
    if os.getenv("BT_TESTING") != "1":
        return None
    raw = os.getenv("BT_FAKE_ORCID")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OrcidError(f"BT_FAKE_ORCID 不是合法 JSON：{exc}") from exc
    return _normalise(data)


def build_authorize_url(state: str, redirect_uri: str) -> str:
    if not client_id():
        raise OrcidError("ORCID 登录未配置：请设置 ORCID_CLIENT_ID / ORCID_CLIENT_SECRET")
    params = {
        "client_id": client_id(),
        "response_type": "code",
        "scope": "openid",
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return f"{authorization_endpoint()}?{urlencode(params)}"


def exchange_code(code: str, redirect_uri: str) -> str:
    """用授权码换 access_token（grant_type=authorization_code）。"""
    if not (client_id() and client_secret()):
        raise OrcidError("ORCID 登录未配置：请设置 ORCID_CLIENT_ID / ORCID_CLIENT_SECRET")
    try:
        response = requests.post(
            token_endpoint(),
            data={
                "client_id": client_id(),
                "client_secret": client_secret(),
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise OrcidError(f"token 端点请求失败：{exc}") from exc
    if response.status_code != 200:
        raise OrcidError(f"token 端点返回 HTTP {response.status_code}: {response.text[:200]}")
    try:
        access_token = response.json().get("access_token")
    except ValueError as exc:
        raise OrcidError("token 端点返回的不是合法 JSON") from exc
    if not access_token:
        raise OrcidError("token 响应缺少 access_token")
    return access_token


def fetch_profile(access_token: str) -> dict:
    """Bearer 调用 userinfo，取出 sub/name/given_name/family_name。"""
    try:
        response = requests.get(
            userinfo_endpoint(),
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise OrcidError(f"userinfo 端点请求失败：{exc}") from exc
    if response.status_code != 200:
        raise OrcidError(f"userinfo 端点返回 HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError as exc:
        raise OrcidError("userinfo 端点返回的不是合法 JSON") from exc
    return _normalise(data)


def _normalise(data: dict) -> dict:
    orcid_id = (data.get("sub") or data.get("orcid") or "").strip()
    if not orcid_id:
        raise OrcidError("ORCID profile 缺少 sub（ORCID iD）")
    given = (data.get("given_name") or "").strip()
    family = (data.get("family_name") or "").strip()
    name = (data.get("name") or "").strip() or " ".join(p for p in (given, family) if p) or orcid_id
    return {"orcid_id": orcid_id, "name": name, "given_name": given, "family_name": family}
