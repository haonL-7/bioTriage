"""账户页与静态资源回归覆盖（与后端 GET /account、/assets 挂载配套）。

为什么放在黑盒套件里：这两个路由是"登录后落地页"的唯一入口，
一旦 /assets 挂载或 account.html 的读取路径写错，表现为线上点了登录就 404/500，
而 API 层用例全绿 —— 所以必须由 HTTP 层直接盯住。
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from conftest import snippet

ACCOUNT_PAGE = "/account"
ASSETS = ("/assets/account.js", "/assets/account.css")


def test_account_page_served(client):
    """契约：GET /account 200 且页面引用 /assets/account.js。"""
    response = client.get(ACCOUNT_PAGE)
    assert response.status_code == 200, (
        f"契约：GET /account 必须返回账户页（200），实际 {response.status_code}；body={snippet(response)}"
    )
    assert "account.js" in response.text, (
        "契约：账户页必须引用 /assets/account.js，实际页面里找不到 account.js；"
        f"body 片段={snippet(response, 300)}"
    )


def test_account_js_served_with_javascript_content_type(client):
    """契约：GET /assets/account.js 200 且 Content-Type 含 javascript。"""
    response = client.get("/assets/account.js")
    assert response.status_code == 200, (
        f"契约：GET /assets/account.js 应 200（/assets 静态挂载），实际 {response.status_code}；"
        f"body={snippet(response)}"
    )
    content_type = response.headers.get("content-type", "").lower()
    assert "javascript" in content_type, (
        f"契约：account.js 的 Content-Type 应含 javascript，实际 {content_type!r}"
    )
    assert response.content.strip(), "契约：account.js 内容不能为空"


def test_account_css_served(client):
    """契约：GET /assets/account.css 200。"""
    response = client.get("/assets/account.css")
    assert response.status_code == 200, (
        f"契约：GET /assets/account.css 应 200（/assets 静态挂载），实际 {response.status_code}；"
        f"body={snippet(response)}"
    )
    assert response.content.strip(), "契约：account.css 内容不能为空"


def test_account_redirects_when_account_html_missing(api_module, client_of, monkeypatch):
    """契约：account.html 不存在时 GET /account 必须 302 到 /（不能 500/404）。

    **不碰任何真实文件**：实现每次请求都读取模块全局 ACCOUNT_HTML_PATH（未缓存），
    因此把它 monkeypatch 到一个不存在的路径，就等价于"页面文件缺失"。
    monkeypatch 在用例结束自动复原，仓库里不会留临时文件、也不会改写版本控制里的
    account.html（旧写法 rename 真文件，进程一中断站点就坏），因此在只读工作区也能跑。
    """
    repo_root = Path(api_module.__file__).resolve().parent
    missing_path = repo_root / "account.html.__missing_on_purpose__"
    assert not missing_path.exists(), (
        f"测试前置：{missing_path} 竟然存在，无法构造「页面缺失」场景"
    )
    monkeypatch.setattr(api_module, "ACCOUNT_HTML_PATH", str(missing_path))

    client = client_of(api_module, ip="198.51.100.66")
    response = client.get(ACCOUNT_PAGE)

    assert response.status_code == 302, (
        "契约：account.html 缺失时 GET /account 应 302 回首页（不能 500/404），"
        f"实际 {response.status_code}；body={snippet(response)}"
    )
    location = response.headers.get("location", "")
    assert urlparse(location).path == "/", (
        f"契约：account.html 缺失时应 302 到 /，实际 Location={location!r}"
    )
