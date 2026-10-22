"""收藏与评估历史契约（Acceptance Criteria 4/5/6）。"""

from __future__ import annotations

import json

from conftest import NAME_USER_A, NAME_USER_B, ORCID_USER_A, ORCID_USER_B, snippet, star_id


def _stars(client):
    response = client.get("/api/me/stars")
    assert response.status_code == 200, (
        f"契约：GET /api/me/stars 已登录时应 200，实际 {response.status_code}；body={snippet(response)}"
    )
    body = response.json()
    assert isinstance(body.get("items"), list), (
        f"契约：GET /api/me/stars 响应需为 {{\"items\": [...]}}，"
        f"实际 {json.dumps(body, ensure_ascii=False)[:300]}"
    )
    return body["items"]


def test_stars_get_requires_auth(client):
    """验收 4：未登录 GET /api/me/stars -> 401。"""
    response = client.get("/api/me/stars")
    assert response.status_code == 401, (
        f"契约：未登录访问 /api/me/stars 必须 401，实际 {response.status_code}；body={snippet(response)}"
    )


def test_stars_post_requires_auth(client):
    """契约：未登录 POST /api/me/stars -> 401。"""
    response = client.post("/api/me/stars", json={"kind": "doi", "doi": "10.1000/anonymous"})
    assert response.status_code == 401, (
        f"契约：未登录 POST /api/me/stars 必须 401，实际 {response.status_code}；body={snippet(response)}"
    )


def test_evaluations_requires_auth(client):
    """契约：未登录 GET /api/me/evaluations -> 401。"""
    response = client.get("/api/me/evaluations")
    assert response.status_code == 401, (
        f"契约：未登录访问 /api/me/evaluations 必须 401，实际 {response.status_code}；body={snippet(response)}"
    )


def test_stars_crud_roundtrip(client, login):
    """验收 6：POST 201 -> GET 含该项 -> DELETE 204 -> GET 不含。"""
    login(client)
    created = client.post(
        "/api/me/stars",
        json={"kind": "doi", "doi": "10.1234/biotriage.test", "title": "黑盒验收用标题"},
    )
    assert created.status_code == 201, (
        f"契约：POST /api/me/stars 成功应 201，实际 {created.status_code}；body={snippet(created)}"
    )
    created_body = created.json()
    assert isinstance(created_body.get("item"), dict), (
        f"契约：POST /api/me/stars 响应需为 {{\"item\": {{...}}}}，"
        f"实际 {json.dumps(created_body, ensure_ascii=False)[:300]}"
    )
    item = created_body["item"]
    item_id = star_id(item)
    assert item.get("kind") == "doi", f"契约：item 应回显 kind，实际 {json.dumps(item, ensure_ascii=False)[:300]}"

    listed = _stars(client)
    assert any(star_id(entry) == item_id for entry in listed), (
        f"契约：GET /api/me/stars 应包含刚收藏的 id={item_id}，"
        f"实际 items={json.dumps(listed, ensure_ascii=False)[:400]}"
    )

    deleted = client.delete(f"/api/me/stars/{item_id}")
    assert deleted.status_code == 204, (
        f"契约：DELETE /api/me/stars/{{id}} 成功应 204，实际 {deleted.status_code}；body={snippet(deleted)}"
    )
    assert not deleted.content, f"契约：204 响应不应带 body，实际 {deleted.content[:200]!r}"

    after = _stars(client)
    assert not any(star_id(entry) == item_id for entry in after), (
        f"契约：删除后 GET /api/me/stars 不应再含 id={item_id}，"
        f"实际 items={json.dumps(after, ensure_ascii=False)[:400]}"
    )


def test_delete_other_users_star_returns_404(client_of, api_module, login_as):
    """验收 5：用户 A 的收藏，用户 B 删除必须 404（不是 403/204），且该收藏仍在。"""
    client_a = client_of(api_module, ip="203.0.113.11")
    client_b = client_of(api_module, ip="203.0.113.12")

    login_as(client_a, ORCID_USER_A, NAME_USER_A)
    created = client_a.post("/api/me/stars", json={"kind": "doi", "doi": "10.1234/owner.a"})
    assert created.status_code == 201, (
        f"契约：POST /api/me/stars 成功应 201，实际 {created.status_code}；body={snippet(created)}"
    )
    item_id = star_id(created.json()["item"])

    login_as(client_b, ORCID_USER_B, NAME_USER_B)
    forbidden = client_b.delete(f"/api/me/stars/{item_id}")
    assert forbidden.status_code == 404, (
        f"契约：删除他人收藏必须 404（不得 403 或 204），实际 {forbidden.status_code}；"
        f"body={snippet(forbidden)}"
    )

    still_there = _stars(client_a)
    assert any(star_id(entry) == item_id for entry in still_there), (
        f"契约：他人越权删除不得影响所有者的收藏，"
        f"实际 items={json.dumps(still_there, ensure_ascii=False)[:400]}"
    )
    assert not any(star_id(entry) == item_id for entry in _stars(client_b)), (
        "契约：用户 B 的收藏列表不应出现用户 A 的收藏"
    )


def test_evaluations_lists_items_when_logged_in(client, login):
    """契约：已登录 GET /api/me/evaluations -> 200 {"items": [...]}。"""
    login(client)
    response = client.get("/api/me/evaluations")
    assert response.status_code == 200, (
        f"契约：已登录 GET /api/me/evaluations 应 200，实际 {response.status_code}；body={snippet(response)}"
    )
    body = response.json()
    assert isinstance(body.get("items"), list), (
        f"契约：GET /api/me/evaluations 响应需为 {{\"items\": [...]}}，"
        f"实际 {json.dumps(body, ensure_ascii=False)[:300]}"
    )
