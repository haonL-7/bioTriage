"""配置健壮性与持久化契约（Acceptance Criteria 9/10）。

* 缺 SESSION_SECRET 时应用仍可导入、仍可服务；
* 全缺 ORCID/会话配置时 login 返回明确错误而不是 500 崩溃；
* 启动后四张表存在、唯一索引到位、DATABASE_URL 缺省回退 sqlite:///./biotriage.db；
* SQLite 路径不牵出 psycopg2。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys

from conftest import import_app, snippet, table_names

EXPECTED_TABLES = {"users", "stars", "evaluations", "usage_counters"}

EXPECTED_COLUMNS = {
    "users": {"orcid_id", "name", "created_at"},
    "stars": {"id", "user_orcid", "kind", "doi", "title", "payload_json", "created_at"},
    "evaluations": {"id", "user_orcid", "input_hash", "request_json", "response_json", "created_at"},
    "usage_counters": {"key", "day", "count"},
}


def _columns(connection, table):
    return {row[1] for row in connection.execute(f"PRAGMA table_info('{table}')").fetchall()}


def _unique_index_columns(connection, table):
    """返回该表上所有唯一索引的列组合（含表级 UNIQUE / 列级 UNIQUE 自动索引）。"""
    result = []
    for row in connection.execute(f"PRAGMA index_list('{table}')").fetchall():
        name, is_unique = row[1], row[2]
        if not is_unique:
            continue
        columns = [info[2] for info in connection.execute(f"PRAGMA index_info('{name}')").fetchall()]
        result.append(tuple(columns))
    return result


def test_app_imports_without_session_secret(base_env):
    """验收 9：环境里没有 SESSION_SECRET 时，import api 不得崩溃，且仍能服务未登录请求。"""
    assert "SESSION_SECRET" not in os.environ, "测试环境错误：base_env 未能移除 SESSION_SECRET"
    module = import_app()  # 不得抛异常
    assert hasattr(module, "app"), "契约：缺少 SESSION_SECRET 时 api 模块仍须导出 app"

    from fastapi.testclient import TestClient

    with TestClient(module.app) as client:
        response = client.get("/api/auth/me")
    assert response.status_code == 200, (
        f"契约：缺少 SESSION_SECRET 时 /api/auth/me 仍应可用（未登录 => authenticated=false），"
        f"实际 {response.status_code}；body={snippet(response)}"
    )
    assert response.json().get("authenticated") is False, (
        f"契约：无 SESSION_SECRET 时未登录请求应为 authenticated=false，实际 {snippet(response)}"
    )


def test_login_without_orcid_config_returns_explicit_error(base_env, client_of, monkeypatch):
    """契约：ORCID/会话配置全缺时应用可启动，登录端点返回明确错误信息而不是崩溃。"""
    for key in ("ORCID_CLIENT_ID", "ORCID_CLIENT_SECRET", "SESSION_SECRET"):
        monkeypatch.delenv(key, raising=False)
    module = import_app()
    client = client_of(module, ip="198.51.100.77")

    response = client.get("/api/auth/login")
    assert response.status_code in (400, 500, 503), (
        f"契约：缺 ORCID 配置时 /api/auth/login 应返回明确错误状态（400/500/503），"
        f"实际 {response.status_code}；body={snippet(response)}"
    )
    text = response.text.lower()
    assert text.strip(), "契约：缺配置时登录端点必须返回明确错误信息，实际响应体为空"
    keywords = ("orcid", "session", "config", "env", "client", "配置", "未配置")
    assert any(word in text for word in keywords), (
        "契约：错误信息应说明缺失的是哪种配置（例如 ORCID_CLIENT_ID / SESSION_SECRET），"
        f"实际 body={snippet(response)}"
    )


def test_tables_created_on_startup(client, app_env):
    """验收 10：启动后 users / stars / evaluations / usage_counters 四张表都存在。"""
    client.get("/api/knowledge_base")  # 触发 lifespan startup
    names = table_names(app_env["db_path"])
    missing = EXPECTED_TABLES - names
    assert not missing, (
        f"契约：启动时 create_all 应建出 {sorted(EXPECTED_TABLES)}，缺少 {sorted(missing)}；"
        f"实际表={sorted(names)}"
    )


def test_schema_columns_and_unique_indexes(client, app_env):
    """契约：四张表的列 + evaluations.input_hash、usage_counters(key,day) 唯一索引。"""
    client.get("/api/knowledge_base")
    connection = sqlite3.connect(str(app_env["db_path"]))
    try:
        for table, expected in EXPECTED_COLUMNS.items():
            actual = _columns(connection, table)
            missing = expected - actual
            assert not missing, (
                f"契约：{table} 表缺列 {sorted(missing)}；实际列={sorted(actual)}"
            )
        evaluation_keys = _unique_index_columns(connection, "evaluations")
        assert ("input_hash",) in evaluation_keys, (
            f"契约：evaluations.input_hash 必须有唯一索引，实际唯一索引={evaluation_keys}"
        )
        counter_keys = _unique_index_columns(connection, "usage_counters")
        assert ("key", "day") in counter_keys or ("day", "key") in counter_keys, (
            f"契约：usage_counters 必须有 (key, day) 唯一索引，实际唯一索引={counter_keys}"
        )
    finally:
        connection.close()


def test_defaults_to_sqlite_file_when_database_url_unset(base_env, client_of, tmp_path, monkeypatch):
    """契约：DATABASE_URL 未设置时回退 sqlite:///./biotriage.db（相对当前工作目录）。"""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    module = import_app()
    client = client_of(module, ip="198.51.100.78")
    client.get("/api/knowledge_base")

    expected = tmp_path / "biotriage.db"
    assert expected.exists(), (
        "契约：DATABASE_URL 未设置时应回退 sqlite:///./biotriage.db，"
        f"实际 {tmp_path} 下未生成 biotriage.db，目录内容={sorted(p.name for p in tmp_path.iterdir())}"
    )


def test_sqlite_mode_does_not_import_psycopg2(client):
    """契约：psycopg2 必须惰性依赖，SQLite 模式下不得被导入（否则测试机需装 psycopg2）。"""
    client.get("/api/knowledge_base")
    assert "psycopg2" not in sys.modules, (
        "契约：DATABASE_URL 为 sqlite 时不得导入 psycopg2（惰性依赖），实际 psycopg2 已被导入"
    )
