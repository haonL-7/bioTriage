"""数据库不可达时的降级契约（D4 回归保护）。

两种失效形态都要覆盖，且都不许冒 500：

1. **本机存储打不开**：DATABASE_URL 指向一个"是目录而不是文件"的 sqlite 路径
   （sqlite3 报 unable to open database file）。瞬时失败、零网络、结果确定。
2. **生产形态**：Postgres 主机不可达（10.255.255.1，libpq connect_timeout=5s）。

为什么常规用例用第 1 种而不是直接上 10.255.255.1：两者对外的可观测契约完全一致
（实测两边都是 / 200、/api/knowledge_base 200、/api/auth/me 401、/api/me/stars 401、
POST /api/me/stars 503、/api/me/quota 503，body 均含 database:"unavailable"），
但 Postgres 不可达时每次 ensure_schema 重试都要等满 connect_timeout（5s），
import 本身还要 ~8s，4 个用例会凭空多出 ~50s。因此：
  * 常规回归用例用"存储打不开"（毫秒级）；
  * "Postgres 不可达"单独成一个用例（test_postgres_unreachable_degrades_the_same_way），
    跑得快时可以 pytest -k "not postgres" 跳过它。
"""

from __future__ import annotations

import sys

import pytest

from conftest import BASE_ENV, import_app, snippet

# 私网不可路由地址：libpq 会等满 connect_timeout(5s) 后失败，与线上"数据库挂起/不可达"同形
UNREACHABLE_POSTGRES = "postgresql://user:pass@10.255.255.1:5432/nodb"

DB_UNAVAILABLE = "unavailable"


def _configure_env(monkey: pytest.MonkeyPatch, database_url: str) -> None:
    """按 conftest 的 app_env 口径配一套完整环境，只把 DATABASE_URL 换成坏地址。"""
    for key, value in BASE_ENV.items():
        monkey.setenv(key, value)
    monkey.setenv("SESSION_SECRET", "test-session-secret-not-for-production")
    monkey.setenv("DATABASE_URL", database_url)


def _schema_ready_probe():
    """取 accounts.db 暴露的只读探针 schema_ready（契约允许的观测点）。

    在 import 之后立刻取到函数对象本身，这样后续别的用例重新 import 把
    accounts.* 从 sys.modules 里换掉时，这里读到的仍然是本次那个模块的状态。
    """
    try:
        from accounts import db as accounts_db
    except Exception:  # 实现若挪走了这个模块，退化成纯黑盒断言
        return None
    probe = getattr(accounts_db, "schema_ready", None)
    return probe if callable(probe) else None


def _psycopg2_modules() -> dict:
    """当前 sys.modules 里的 psycopg2 及其子模块。"""
    return {k: v for k, v in sys.modules.items() if k == "psycopg2" or k.startswith("psycopg2.")}


def _restore_psycopg2(snapshot: dict) -> None:
    """把本用例引入的 psycopg2 模块摘掉。

    目的：test_config_and_schema.py::test_sqlite_mode_does_not_import_psycopg2 断言
    "sqlite 模式下进程内不该出现 psycopg2"；本文件唯一会 import psycopg2 的是
    Postgres 不可达用例，用完必须复原，否则两个文件谁先跑谁就会让另一个假失败。
    """
    for key in [k for k in sys.modules if k == "psycopg2" or k.startswith("psycopg2.")]:
        if key not in snapshot:
            del sys.modules[key]


def _json(response) -> dict:
    try:
        return response.json()
    except Exception:
        raise AssertionError(
            f"契约：降级响应必须是 JSON，实际 {response.headers.get('content-type')!r} body={snippet(response)}"
        )


def _assert_db_unavailable(response, expected_status: int, label: str) -> None:
    assert response.status_code == expected_status, (
        f"契约：库不可用时 {label} 必须 {expected_status}（不得 500），"
        f"实际 {response.status_code}；body={snippet(response)}"
    )
    assert response.status_code != 500, f"契约：库不可用时 {label} 绝不能 500；body={snippet(response)}"
    body = _json(response)
    assert body.get("database") == DB_UNAVAILABLE, (
        f"契约：库不可用时 {label} 的 body 必须含 database:\"unavailable\"，"
        f"实际 {body!r}；body={snippet(response)}"
    )
    assert body.get("detail"), f"契约：库不可用时 {label} 的 body 必须带可读 detail，实际 {body!r}"


@pytest.fixture(scope="module")
def degraded_app(tmp_path_factory):
    """一个"数据库永远打不开"的应用实例（模块内共享，避免重复付出 import 成本）。"""
    # 目标路径是一个目录：sqlite 无法把它当数据库文件打开 -> 等效于数据库不可达
    blocker_dir = tmp_path_factory.mktemp("dbdown")
    database_url = "sqlite:///" + blocker_dir.as_posix()

    monkey = pytest.MonkeyPatch()
    _configure_env(monkey, database_url)
    try:
        module = import_app()  # 不抛异常即已满足"库不可达时 import 成功"
        yield {
            "module": module,
            "schema_ready": _schema_ready_probe(),
            "url": database_url,
        }
    finally:
        monkey.undo()


@pytest.fixture()
def degraded_client(client_of, degraded_app):
    return client_of(degraded_app["module"], ip="203.0.113.31")


# ==================== 用例 ====================


def test_import_succeeds_when_db_unreachable(degraded_app, degraded_client):
    """1) 库不可达时 import api 不抛异常，且建表状态为 False（账户端点据此降级）。"""
    module = degraded_app["module"]
    assert hasattr(module, "app"), (
        f"契约：DATABASE_URL={degraded_app['url']} 不可用时 import api 仍须导出 app，"
        "否则整站随数据库一起挂掉"
    )

    probe = degraded_app["schema_ready"]
    if callable(probe):
        assert probe() is False, (
            "契约：建表失败时 schema_ready() 必须为 False（否则账户端点会误判数据库可用、"
            "把异常冒成 500）"
        )
    else:
        # 退化的黑盒断言：拿不到探针时，用"账户端点返回 database:unavailable"反证未就绪
        _assert_db_unavailable(
            degraded_client.post("/api/me/stars", json={"kind": "doi", "doi": "10.1000/probe"}),
            503,
            "POST /api/me/stars",
        )


def test_readonly_endpoints_still_200_when_db_down(degraded_client):
    """2) 库不可达时只读接口不受影响：/ 与 /api/knowledge_base 必须 200。"""
    root = degraded_client.get("/")
    assert root.status_code == 200, (
        f"契约：库不可达时首页仍须 200，实际 {root.status_code}；body={snippet(root)}"
    )

    kb = degraded_client.get("/api/knowledge_base")
    assert kb.status_code == 200, (
        f"契约：库不可达时 /api/knowledge_base 仍须 200，实际 {kb.status_code}；body={snippet(kb)}"
    )
    assert _json(kb), "契约：库不可达时知识库仍应返回内容，实际 body 为空"


def test_account_endpoints_degrade_not_500(degraded_client):
    """3) 账户端点降级：读 401、写 503，body 含 database:unavailable，且一律不是 500。"""
    _assert_db_unavailable(degraded_client.get("/api/auth/me"), 401, "GET /api/auth/me")
    _assert_db_unavailable(degraded_client.get("/api/me/stars"), 401, "GET /api/me/stars")
    _assert_db_unavailable(
        degraded_client.post("/api/me/stars", json={"kind": "doi", "doi": "10.1234/degraded"}),
        503,
        "POST /api/me/stars",
    )
    _assert_db_unavailable(degraded_client.get("/api/me/quota"), 503, "GET /api/me/quota")


def test_ai_analyze_survives_db_outage(degraded_client):
    """4) 库不可达时 /api/ai_analyze 不得 500（配额无法记账时应 fail-open 或明确 429）。"""
    response = degraded_client.get("/api/ai_analyze", params={"query": "乳酸菌 GG 对腹泻的证据等级"})
    assert response.status_code != 500, (
        f"契约：库不可达时 /api/ai_analyze 绝不能 500（配额记账失败不应拖垮 AI 分析）；"
        f"body={snippet(response)}"
    )
    assert response.status_code in (200, 429), (
        f"契约：库不可达时 /api/ai_analyze 只应是 200（fail-open）或 429（明确拒绝），"
        f"实际 {response.status_code}；body={snippet(response)}"
    )


def test_postgres_unreachable_degrades_the_same_way(monkeypatch, client_of):
    """生产形态：Postgres 主机不可达（10.255.255.1）时同样只降级、不 500。

    代价：import 约 8s（connect_timeout=5 + 一次重试），之后每个碰库的请求再加 ~5s。
    """
    psycopg2_snapshot = _psycopg2_modules()
    try:
        _configure_env(monkeypatch, UNREACHABLE_POSTGRES)
        module = import_app()  # 不抛异常即已满足"库不可达时 import 成功"
        probe = _schema_ready_probe()
        client = client_of(module, ip="203.0.113.32")

        if callable(probe):
            assert probe() is False, (
                "契约：Postgres 不可达时 schema_ready() 必须为 False，实际为 True"
            )

        _assert_db_unavailable(client.get("/api/auth/me"), 401, "GET /api/auth/me（postgres 不可达）")
        _assert_db_unavailable(
            client.post("/api/me/stars", json={"kind": "doi", "doi": "10.1234/pgdown"}),
            503,
            "POST /api/me/stars（postgres 不可达）",
        )
    finally:
        # 失败也要复原，别把 psycopg2 留在 sys.modules 里污染其它用例
        _restore_psycopg2(psycopg2_snapshot)
