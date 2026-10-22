"""配额契约（Acceptance Criteria 7/8）：/api/ai_analyze 的匿名 1 次/天、登录 5 次/天。"""

from __future__ import annotations

import json

from conftest import NAME_USER_A, ORCID_USER_A, assert_quota_error_body, snippet

AI_PATH = "/api/ai_analyze"
QUERY = "乳酸菌 GG 对腹泻的证据等级"


def test_anonymous_ai_analyze_second_call_returns_429(client_factory):
    """验收 7：匿名第 2 次 /api/ai_analyze -> 429（AI_DAILY_QUOTA_ANON 默认 1）。"""
    client = client_factory(ip="198.51.100.21")

    first = client.get(AI_PATH, params={"query": QUERY})
    assert first.status_code != 429, (
        f"契约：匿名配额 AI_DAILY_QUOTA_ANON=1，第 1 次调用不应被限额，实际 {first.status_code}；"
        f"body={snippet(first)}"
    )

    second = client.get(AI_PATH, params={"query": QUERY})
    assert second.status_code == 429, (
        f"契约：匿名第 2 次 /api/ai_analyze 必须 429，实际 {second.status_code}；body={snippet(second)}"
    )
    assert_quota_error_body(second.json(), tier_group="anon", limit=1)


def test_user_ai_analyze_sixth_call_returns_429_with_quota_body(client_factory, login):
    """验收 8：登录用户第 6 次 -> 429，且 body 含 tier/limit/used/remaining/resets_at。"""
    client = client_factory(ip="198.51.100.22")
    login(client, ORCID_USER_A, NAME_USER_A)

    for attempt in range(1, 6):
        response = client.get(AI_PATH, params={"query": f"{QUERY} #{attempt}"})
        assert response.status_code != 429, (
            f"契约：登录用户配额 AI_DAILY_QUOTA_PER_USER=5，第 {attempt} 次不应被限额，"
            f"实际 {response.status_code}；body={snippet(response)}"
        )

    sixth = client.get(AI_PATH, params={"query": f"{QUERY} #6"})
    assert sixth.status_code == 429, (
        f"契约：登录用户第 6 次 /api/ai_analyze 必须 429，实际 {sixth.status_code}；body={snippet(sixth)}"
    )
    assert_quota_error_body(sixth.json(), tier_group="user", limit=5)


def test_quota_endpoint_shape(client, login):
    """契约：GET /api/me/quota -> 200 {tier, limit, used, remaining, resets_at}，登录用户 limit=5。"""
    login(client, ORCID_USER_A, NAME_USER_A)
    response = client.get("/api/me/quota")
    assert response.status_code == 200, (
        f"契约：GET /api/me/quota 应 200，实际 {response.status_code}；body={snippet(response)}"
    )
    body = response.json()
    required = ("tier", "limit", "used", "remaining", "resets_at")
    missing = [key for key in required if key not in body]
    assert not missing, (
        f"契约：/api/me/quota 缺字段 {missing}；实际 body={json.dumps(body, ensure_ascii=False)[:400]}"
    )
    assert body["limit"] == 5, (
        f"契约：登录用户 limit 应为 AI_DAILY_QUOTA_PER_USER=5，实际 {body['limit']}；"
        f"body={json.dumps(body, ensure_ascii=False)[:300]}"
    )
    assert 0 <= int(body["used"]) <= 5, f"契约：used 应在 0..5，实际 {body['used']}"
    assert int(body["remaining"]) == 5 - int(body["used"]), (
        f"契约：remaining 应等于 limit-used，实际 limit={body['limit']} used={body['used']} "
        f"remaining={body['remaining']}"
    )
    assert body["resets_at"], f"契约：resets_at 不能为空，实际 {body['resets_at']!r}"


def test_general_rate_limit_still_60_per_minute(client_factory):
    """契约：非 AI 端点保持 60 次/分钟/IP 的通用限流，不得回归。"""
    client = client_factory(ip="198.51.100.240")
    statuses = [client.get("/api/knowledge_base").status_code for _ in range(61)]

    early_429 = [index for index, status in enumerate(statuses[:60], start=1) if status == 429]
    assert not early_429, (
        f"契约：通用限流为 60 次/分钟/IP，第 1..60 次不应被限流，实际第 {early_429[:5]} 次即 429"
    )
    assert statuses[60] == 429, (
        f"契约：同 IP 第 61 次请求必须 429（60/分钟），实际 {statuses[60]}；"
        f"前后状态码序列尾部={statuses[-6:]}"
    )
