# ORCID 个人账户系统 — 接入与部署说明

> 状态：契约已冻结（端点 / 配额 / 会话 / 持久化），本文只讲“怎么开通、怎么填、怎么切环境”。
> 谁读它：负责部署的同学 + 负责实现账户系统（accounts/）的同学。

---

## 1. 这套系统在做什么

bioTriage 增加个人账户，用来：

* 用 **ORCID iD 登录**（ORCID OIDC，scope 仅 `openid`，不读取任何敏感数据）；
* 保存 **收藏**（stars）与 **AI 分析历史**（evaluations）；
* 用 **每日配额** 给昂贵的 `/api/ai_analyze` 设闸：匿名 1 次/IP/天，ORCID 登录 5 次/人/天。

匿名用户不受影响，仍然可以直接用全部只读接口（60 次/分钟/IP 的通用限流保持不变）。

---

## 2. 在 ORCID 开发者后台注册一个免费 client（约 5 分钟）

1. 用你自己的 ORCID iD 登录 <https://orcid.org>（没有就免费注册，个人注册永久免费）。
2. 打开开发者工具页：<https://orcid.org/developer-tools>（生产）。
   沙箱对应页面是 <https://sandbox.orcid.org/developer-tools>。
3. 点 **"Create a new application"**（注册新应用），填：

   | 字段 | 填什么 |
   | --- | --- |
   | Name / 应用名 | `bioTriage`（随意，仅自己可见） |
   | Website URL | 你的站点首页，例如 `https://<你的域名>/` |
   | Description | 一句话，例如 “Evidence triage for probiotics/metabolites; ORCID login only.” |
   | **Redirect URIs** | `https://<你的域名>/api/auth/callback`（见下方“回调地址”一节，可加多条） |

4. 保存后，页面会给出 **Client ID** 和 **Client Secret** —— 这两项就是要填进环境变量的
   `ORCID_CLIENT_ID` / `ORCID_CLIENT_SECRET`。
   **Secret 只显示一次**，丢了就重新生成一份。

> 免费额度说明：注册 client 本身免费；ORCID 的公共 API（Public API）对个人开发者免费开放，
> 只在登录瞬间被调用两次（`/oauth/token` 换 token + `/oauth/userinfo` 取 profile），
> 远低于其速率限制。ORCID 的速率上限与可用 scope 随 API 等级（Public / Member）不同，
> 以官方文档为准：<https://info.orcid.org/documentation/apis/rate-limits/> 。
> 本系统只用公共 API 就够：**不需要 ORCID 机构会员**，也不需要付费。

### 回调地址（Redirect URI）怎么填

* 必须 **逐字符** 等于 `APP_BASE_URL` + `/api/auth/callback`，例如
  `APP_BASE_URL=https://biotriage.example.com` → 回调
  `https://biotriage.example.com/api/auth/callback`。
* 末尾 **不要** 加斜杠，不要写 `localhost` 以外的端口差异；ORCID 是精确字符串匹配，
  差一个字符就会报 `invalid_redirect_uri`。
* 本地开发要在 ORCID 里另外注册一条 `http://127.0.0.1:8000/api/auth/callback`
  （同一 client 可以登记多条 Redirect URI）。
* 改了域名 → 必须回到开发者后台同步修改，否则登录会在授权页直接失败。

---

## 3. 沙箱（sandbox）与生产（production）的切换

只有一个开关：`ORCID_BASE_URL`。

| 环境 | ORCID_BASE_URL | 说明 |
| --- | --- | --- |
| 生产 | `https://orcid.org` | 真实 ORCID iD 登录 |
| 沙箱 / 联调 | `https://sandbox.orcid.org` | **默认值**；沙箱 iD 与生产 iD 不互通 |
| 自动化测试 | `https://sandbox.orcid.org` + `BT_TESTING=1` | 完全不发网络请求，见第 6 节 |

端点在两种环境下是同一套（只是域名不同）：

```
authorization_endpoint = <ORCID_BASE_URL>/oauth/authorize
token_endpoint        = <ORCID_BASE_URL>/oauth/token        (client_secret_post)
userinfo_endpoint     = <ORCID_BASE_URL>/oauth/userinfo     (Bearer)
```

注意：

* **沙箱的 client 只对沙箱有效**。生产 client 与沙箱 client 是两份不同的 id/secret，
  不要互相拷贝，否则报 `invalid_client`。
* 沙箱账号（`sandbox.orcid.org` 上注册的 iD）与生产账号数据完全隔离，测试期造的账号不要指望在生产存在。
* 沙箱偶尔会维护，联调失败先看 <https://sandbox.orcid.org> 是否能打开。

---

## 4. 环境变量一览

| 变量 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- |
| `ORCID_CLIENT_ID` | 生产必填 | 空 | ORCID 开发者后台给的 Client ID |
| `ORCID_CLIENT_SECRET` | 生产必填 | 空 | 同一处的 Client Secret（只显示一次） |
| `ORCID_BASE_URL` | 否 | `https://sandbox.orcid.org` | 切换沙箱/生产的唯一开关 |
| `APP_BASE_URL` | 生产必填 | 空 | 站点对外地址，用来拼 `redirect_uri` 与判断 `Secure` cookie |
| `SESSION_SECRET` | 生产必填 | 空 | 会话 cookie 的 HMAC 密钥，至少 32 字节随机值 |
| `DATABASE_URL` | 否 | `sqlite:///./biotriage.db` | 生产建议用托管 Postgres |
| `AI_DAILY_QUOTA_PER_USER` | 否 | `5` | 登录用户每天 `/api/ai_analyze` 次数 |
| `AI_DAILY_QUOTA_ANON` | 否 | `1` | 匿名每 IP 每天次数 |
| `SESSION_MAX_AGE` | 否 | `2592000` | 会话有效期（秒），30 天 |

**全部缺失时必须能安全启动**（不崩溃）；此时登录端点返回明确错误信息，而不是抛 500 堆积栈。

生成 `SESSION_SECRET`：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Render 上可以直接用 `generateValue: true`（见 `render.yaml`），不必手工贴明文。

---

## 5. 端点契约（实现方与测试方共同遵守）

```
GET  /api/auth/login      302 → <ORCID_BASE_URL>/oauth/authorize?client_id&response_type=code&scope=openid&redirect_uri&state
                          state 写入短时 cookie
GET  /api/auth/callback   校验 state → 换 token → 取 profile → upsert 用户 → 建会话 → 302 /account
POST /api/auth/logout     200 {"ok": true}，清 cookie
GET  /api/auth/me         200 {"authenticated": bool, "user": {"orcid_id","name","created_at"}|null, "quota": {...}}
GET  /api/me/stars        200 {"items": [...]}      未登录 401
POST /api/me/stars        201 {"item": {...}}       body {"kind","doi"|"title","payload"?}；未登录 401
DELETE /api/me/stars/{id} 204；删别人的收藏必须 404（不是 403）
GET  /api/me/evaluations  200 {"items": [...]}      未登录 401
GET  /api/me/quota        200 {"tier","limit","used","remaining","resets_at"}
```

### 会话 cookie

* 名字 `bt_session`；值 = `base64(payload)` + `"."` + `HMAC-SHA256(SESSION_SECRET)`
  （只用标准库 `hmac`/`hashlib`/`base64`/`json`，不引第三方库）。
* payload 含 `orcid_id` 与 `exp`（`exp = now + SESSION_MAX_AGE`）。
* 属性：`HttpOnly`、`SameSite=Lax`、`Path=/`；`APP_BASE_URL` 以 `https` 开头时加 `Secure`。

### 配额

* 匿名 = `AI_DAILY_QUOTA_ANON`（默认 1）次/IP/天；登录 = `AI_DAILY_QUOTA_PER_USER`（默认 5）次/人/天。
* 超额必须 `429`，body 至少含 `detail` / `tier` / `limit` / `used` / `remaining` / `resets_at`。
* 其它端点维持 60 次/分钟/IP 的通用限流。

### 持久化

SQLAlchemy 2.x 声明式；启动时 `create_all`。表：`users` / `stars` / `evaluations` / `usage_counters`。
`psycopg2` 是惰性依赖：只有 `DATABASE_URL` 指向 postgres 时才需要安装，SQLite 环境下不要 import。

---

## 6. 自动化测试怎么跑（不发真实请求）

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt -r requirements-dev.txt   # Windows
.venv/Scripts/python -m pytest tests -v
```

测试钩子：`BT_TESTING=1` 且设置 `BT_FAKE_ORCID={"sub":"0000-0000-0000-0000","name":"..."}` 时，
`/api/auth/callback` **不发任何网络请求**，直接采用该 profile。生产路径不受影响（不设该变量即可）。

测试套件同时封死 socket 建连，任何真实网络调用都会让用例直接失败。

---

## 7. 部署清单（Render 示例，见仓库根 render.yaml）

1. Render 新建 Web Service（Python），`buildCommand` = `pip install -r requirements.txt`，
   `startCommand` = `uvicorn api:app --host 0.0.0.0 --port $PORT`。
2. 添加托管 Postgres（`render.yaml` 里的 `databases:` 段），`DATABASE_URL` 由 Render 注入；
   `DATABASE_URL` 是 postgres 时，运行时需额外安装驱动（`psycopg2-binary`），
   **仅在需要 Postgres 时才加**，SQLite 环境不要装。
3. 三个密钥变量一律走 Dashboard / `sync: false` / `generateValue: true`，**不要写进仓库**：
   `ORCID_CLIENT_SECRET`、`SESSION_SECRET`、`DEEPSEEK_API_KEY`。
4. 部署完成后拿到正式域名，回填 `APP_BASE_URL`，并回 ORCID 开发者后台把 Redirect URI 改成
   `https://<域名>/api/auth/callback`。
5. 自查：浏览器打开 `/api/auth/me` 应返回 `{"authenticated": false, ...}`；
   点登录应跳到 `https://orcid.org/oauth/authorize?...`；登录后 `/account` 能看到自己的 ORCID iD。

---

## 8. 常见错误对照表

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 授权页报 `invalid_redirect_uri` | Redirect URI 与 `APP_BASE_URL + /api/auth/callback` 不完全一致 | 逐字符比对，注意 http/https、有无斜杠 |
| 回调报 `invalid_client` | id/secret 与环境不匹配（沙箱 client 用在生产域名上） | 换回对应环境的 client；检查 `ORCID_BASE_URL` |
| 回调 400 state 不匹配 | 用户在同一浏览器开了两个登录标签，或 state cookie 过期 | 重新点登录；不要并发登录 |
| 回调 400 `invalid_grant` | code 过期（一次性、约 1 分钟内有效）或重复使用 | 让用户重新登录，别刷新回调 URL |
| 登录后 `/api/auth/me` 仍 false | cookie 未回传：跨站域名 / 用了 `http` 访问 https 站点 / `APP_BASE_URL` 与真实域名不一致 | 对齐 `APP_BASE_URL`，同域访问 |
| AI 分析一直 429 | 配额用尽（匿名 1 次/天，登录 5 次/天） | 看 `resets_at`；登录或用别的网络 |
| 启动报 sqlalchemy / psycopg2 缺失 | `DATABASE_URL` 是 postgres 但没装驱动 | 安装 `psycopg2-binary`，或改回 SQLite |

---

## 9. 隐私与合规提示

* 只请求 `openid` scope，只保存 ORCID iD、显示名与登录时间；不存邮箱、不存论文列表。
* 用户要求删除账户时：删 `users` 行 + 该 `user_orcid` 的 `stars` / `evaluations` 即可完成数据清理。
* 会话是无状态 HMAC cookie：轮换 `SESSION_SECRET` 等于强制所有用户重新登录（这是应急手段）。

---

## 10. 反向代理与真实客户端 IP（`--proxy-headers`）

**为什么必须开**：生产形态是 Render（或任何托管平台）的**边缘代理终结 TLS** 后再转发到 uvicorn。
uvicorn 默认只信任来自 `127.0.0.1` 的转发头，因此在容器里读 `request.client.host` 拿到的是**代理地址**，
所有匿名访客会被看成同一个 IP —— 后果不是"少记账"而是**整站匿名额度退化成 1 次/天**：
`/api/ai_analyze` 的匿名配额（`AI_DAILY_QUOTA_ANON`）与 60 次/分钟通用限流会按"一个 IP"算，
第一个匿名用户用完后，全站其他匿名用户当天全部 429。

启动命令必须是（Procfile 的 `web` 行与 `render.yaml` 的 `startCommand` **两处逐字一致**）：

```bash
uvicorn api:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips=*
```

* `--proxy-headers`：让 uvicorn 采信 `X-Forwarded-For` / `X-Forwarded-Proto`，
  `request.client.host` 才会是真实访客 IP（也才会正确判断 `https`）。
* `--forwarded-allow-ips=*`：**无条件**信任这些头 —— 这是它的代价，见下面的信任边界。

**信任边界（读这一段再改配置）**：`*` 意味着"谁都能声称自己是任意 IP"。
只有在**服务绝不可能被直连**时才安全：Render 的公网入口只有它的边缘代理，容器不暴露裸端口，
所以 `*` 不会带来伪造风险。一旦出现任何直连路径（自建 Nginx 之外的裸跑 uvicorn、平台内网其它服务、
IP 直接访问、本地压测脚本），客户端就能自带 `X-Forwarded-For: 1.2.3.4` 绕过配额 —— 此时必须收紧。

**换成自建反代时怎么改**（把 `*` 换成精确清单）：

```bash
# 例：只有同机 Nginx 与内网 10.0.0.0/8 能转发
uvicorn api:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips=127.0.0.1,10.0.0.0/8
```

* uvicorn 接受逗号分隔的 IP / CIDR 列表；填**代理自身**的地址，不要填访客网段。
* 同时在反代侧**覆盖而不是追加**转发头（Nginx：`proxy_set_header X-Forwarded-For $remote_addr;`），
  避免客户端自带的 XFF 被透传。
* 直接对外监听时不要开 `--proxy-headers`（宁可让 `request.client.host` 是直连 IP）。

**改完怎么验**：

1. 两个不同网络（或一个用手机热点）各调一次 `/api/ai_analyze`，看 429 body 的 `used` 是否各自独立计数；
2. 或临时打印 `request.client.host`，确认拿到的是访客 IP 而非常量代理 IP；
3. `GET /api/me/quota` 的 `used` 应随不同来源 IP 分别增长。
---

## 11. 腾讯云 SCF（Web 函数）部署（deploy/scf/）

生产实际跑在**腾讯云云函数 Web 函数**上，打包与发布细节集中在
`deploy/scf/README.md`（含打包脚本参数、控制台步骤、运行时版本核对、环境变量清单、改用「层」的做法、回滚方式）。
这里只写与账号系统直接相关的三条：

* **启动方式**：函数读代码包根目录的 `scf_bootstrap`，由它把 `./pkg` 加入 PYTHONPATH 并启动
  `uvicorn api:app --host 0.0.0.0 --port 9000`（Web 函数固定监听 9000，不能改端口）。
* **⚠️ SQLite 在云函数下不可用**：代码目录只读、只有 `/tmp` 可写且随实例销毁，缺省的
  `sqlite:///./biotriage.db` 会让会话 / 收藏 / 评估历史 / 配额计数全部不可靠。
  **必须用外部 Postgres**：`DATABASE_URL=postgresql://user:pass@host:5432/db`
  （驱动 `psycopg2-binary` 已随 `requirements.txt` 打进包）。
* **生产必配**：`ORCID_BASE_URL=https://orcid.org`、`APP_BASE_URL=https://<函数公网域名>`、
  `SESSION_SECRET`（缺省时每个实例用随机密钥，用户登录态会随机失效）、`DEEPSEEK_API_KEY`；
  并且**不要**设置 `BT_TESTING` / `BT_FAKE_ORCID`（那等于开后门）。
  ORCID 后台登记的 Redirect URI 必须逐字符等于 `APP_BASE_URL + /api/auth/callback`。

---

## 12. 数据库不可用时的降级语义

本节是**实测行为**（不是设计意图），用于校正上面 §5 的端点契约表：账户数据库不可达时，
系统只降级、不崩站，但**账户读接口的状态码与契约文本不同（401 而非 200）**，按文本复核会判成不符合，
所以单列一节把口径钉死。

### 12.1 只读接口不受影响（仍 200）

数据库不可用时下列端点照常 200，返回内容与库健康时一致（它们只读本地知识库/页面，不碰账户库）：

* `GET /`
* `GET /api/knowledge_base`
* `GET /api/evaluate_tier`
* `GET /api/evaluate_matrix`
* `GET /api/experiment_advice`

### 12.2 账户「读」接口 -> 401（不是 200 + authenticated:false）

| 端点 | 库不可用时 | body |
| --- | --- | --- |
| `GET /api/auth/me` | **401** | `{"detail": "...", "database": "unavailable"}` |
| `GET /api/me/stars` | **401** | 同上 |
| `GET /api/me/evaluations` | **401** | 同上 |

原因：登录态本身存在账户库里，库不可用时**无法证实**会话是否有效，所以按"未认证"处理（fail-closed），
而不是假装 `authenticated:false` 再返回 200。前端 `assets/account.js` 的既有约定是
"401 一律展示未登录态"，因此 UI 行为正常（显示未登录，不弹错）。
**这是一处与 §5 端点契约文本的已知偏离**：契约写的是 `200 + authenticated:bool`，
降级路径上改为 401；常规（库健康）路径仍是 200。

### 12.3 需要写库 / 需要账户数据的接口 -> 503

| 端点 | 库不可用时 | body |
| --- | --- | --- |
| `GET /api/me/quota` | **503** | `{"detail": "...", "database": "unavailable"}` |
| `POST /api/me/stars` | **503** | 同上 |
| `DELETE /api/me/stars/{id}` | **503** | 同上（注意：库健康时"删别人的收藏"是 404，这里 503 优先） |
| `GET /api/auth/callback` | **503** | 同上（换 token 后无法 upsert 用户/建会话，只能明确拒绝） |

### 12.4 任何账户端点都不得返回未捕获的 500

账户端点在调用前先试一次"惰性建表"，失败即按上面的语义返回 401/503；
运行期数据库报错也统一被兜住（不冒 500 堆栈）。**上表状态码是可预期的降级结果，不是故障**。

### 12.5 /api/ai_analyze：配额记账失败时 fail-open（有意取舍）

* 库不可用时配额记账失败，`/api/ai_analyze` **不报错、不返回 503**，而是**退回进程内按 IP 计数**，
  仍受 `AI_DAILY_QUOTA_ANON`（默认 1）约束：实测第 1 次 200、第 2 次 429
  （`{"detail": ..., "tier": "anon", "limit": 1, ...}`）。
* 代价与风险：进程内计数**在多实例 / 冷启动后会重置**，且登录用户与匿名用户在这条路径上都按 IP 计
  （登录身份认不出来）。也就是说"数据库挂了 = 匿名配额暂时变成按实例计"，会多花一点 DeepSeek 费用。
* 这是**有意的取舍**：AI 分析是本站核心功能，宁可短时间内放宽配额，也不让整站在数据库故障时不可用。
  若你更希望"库挂了就拒绝 AI 分析"，那是策略变更，需要同时改实现与本节。

### 12.6 冷启动会被初始化阻塞（预期）

数据库不可达时，import 阶段的一次建表尝试要等满 TCP 超时：实测 **约 8 秒**
（`connect_timeout=5` + 一次重试），之后每个"需要账户库"的请求还会各自再等约 5 秒才降级。
因此故障期间**冷启动首访变慢是预期行为**，不是卡死；库恢复后下一次账户请求会自动重试初始化并恢复正常
（无需重启实例）。

### 12.7 回归保障

上述行为由 `tests/test_db_degraded.py` 覆盖（5 个用例：import 成功 + schema_ready=False、
只读接口 200、账户端点 401/503 且 body 含 `database:"unavailable"`、AI 分析不 500、
以及一条真实的 Postgres 不可达 TCP 超时用例）。赶时间可以用
`pytest -k "not postgres"` 跳过真实 TCP 超时那条（它每条碰库请求要等 5 秒）。

## 13. GitHub Pages 账户页（Bearer token 模式）

### 13.1 为什么账户页不在后端域名下

腾讯云 SCF **默认域名**（`*.tencentscf.com`）禁止渲染 HTML：平台在网关层对响应强制
`Content-Disposition: attachment`，应用层无法覆盖。结果就是账户页在那个域名下点开只会**下载**一个 HTML 文件。
所以现在的分工是：

| 角色 | 位置 |
| --- | --- |
| 账户页（静态） | GitHub Pages — `https://haonl-7.github.io/bioTriage/account/` |
| 后端 API | SCF — `https://1477715351-0lu4gk6z6i.ap-chengdu.tencentscf.com` |

跨站认证用 **Bearer token**，不再依赖会话 cookie：第三方 cookie 在 Safari / Chrome 的跨站策略下本来就不可靠，
而 token 走 `Authorization` 头，行为确定。

### 13.2 登录流程（`mode=pages`）

1. 账户页「使用 ORCID 登录」→ 打开后端 `GET /api/auth/login?mode=pages`。
2. 后端 302 到 ORCID 授权页，**state 里编码 `mode=pages`**（这是两条流程的唯一分叉点）。
3. ORCID 回到 `GET /api/auth/callback?code=...&state=...`；后端若读到 `state.mode == "pages"`：
   * 签发 `secrets.token_urlsafe(32)`；
   * 在 `auth_tokens` 表里存 **`sha256(token)` 哈希**（明文 token 从不落库）+ `user_orcid` + `created_at` + `expires_at`（30 天）；
   * **302 到 `PAGES_ACCOUNT_URL`**，token 放在 **URL fragment** 里：`.../account/#t=<token>`；
   * **不设置 `bt_session` cookie**（保持干净，也避免跨站 cookie 的歧义）。
4. 账户页从 `location.hash` 取出 `t` → 存 `sessionStorage("bt_token")` → 用 `history.replaceState` 把 hash 从地址栏抹掉。
5. 之后所有 API 调用都带 `Authorization: Bearer <token>`，且 **`credentials: "omit"`**（一个 cookie 都不用）。

用 fragment 而不是 query 的原因：fragment 不会随 HTTP 请求发出，不进服务端访问日志，也不会通过 `Referer` 泄漏给第三方。

### 13.3 认证解析顺序与状态码

* 后端先看 `Authorization: Bearer <token>`，**没有或解析不出才回退** cookie `bt_session`（原 cookie 流程行为不变）。
* token 无效 / 过期 / 已撤销 → `401 {"detail": "...", "auth": "invalid_token"}`（`auth` 字段便于前端判断"该清 token 了"）。
* 未登录访问 `/api/auth/me` 仍然是 **200 + `authenticated: false`**（这是既有语义，不为 Bearer 改动）。
* 账户页收到 401 即清掉 `sessionStorage` 里的 token，回到未登录态（显示登录按钮）。

### 13.4 token 存在哪里、什么时候过期

* 服务端：`auth_tokens(id, token_hash UNIQUE, user_orcid, created_at, expires_at, revoked_at)`，
  继承 `Base`、随 `create_all` 建表；只存 sha256 哈希，库被读走也拿不到可用 token。
* 有效期 **30 天**（`expires_at = created_at + 30d`）。
* 浏览器端：只在 `sessionStorage`（关掉标签页即失效），**不写 localStorage**——避免长期驻留的 XSS 面。

### 13.5 怎么撤销

`POST /api/auth/logout`：带 `Authorization: Bearer` 时把该 token 记 `revoked_at` 并返回 `{"ok": true}`，
之后同一个 token 一律 401 `invalid_token`；cookie 流程的 logout 行为不变（清 cookie）。
需要在服务端批量失效时，直接对 `auth_tokens` 里对应行写 `revoked_at`，或把 `expires_at` 改到过去即可——
两条路径都不需要重启服务（每次请求都查库）。

### 13.6 CORS：把 Pages 放进来

跨站调用必须显式放行 Pages 的 origin：

* `allow_origins` 含 `https://haonl-7.github.io`（保留原有本地开发 origin）；
* `allow_methods = ["GET", "POST", "DELETE", "OPTIONS"]`；
* `allow_headers = ["Authorization", "Content-Type", "Accept"]`；
* `allow_credentials` 保持 `true`（cookie 老流程还要用）。

注意不要写成 `allow_origins=["*"] + allow_credentials=True`：浏览器会直接拒绝这种组合。

### 13.7 环境变量 `PAGES_ACCOUNT_URL`

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `PAGES_ACCOUNT_URL` | `https://haonl-7.github.io/bioTriage/account/` | `mode=pages` 回调 302 的目标；换域名 / 换仓库路径时只改这里 |

它只影响"登录成功后往哪里跳"；改了它要同时确认 GitHub Pages 上确实部署了账户页（见 13.8）。

### 13.8 发布与回归测试

* 发布：`ai-news/scripts/build.py` 的 `deploy_account_page()` 把**仓库根** `account/` 复制到
  `_site/account/`，由 Pages 构建发出去；`account/` 不存在时只打印 WARNING、不让构建失败
  （没有账户页也照常发布其它页面）。
* 测试：`tests/test_bearer_auth.py` 覆盖 mode=pages 往返（302 到 `PAGES_ACCOUNT_URL` + `#t=` + 不下发
  `bt_session`）、Bearer 读写删收藏、伪造 / 过期 / 已撤销 token 的 401 `invalid_token`、CORS 预检，
  以及"不带 Authorization 时 cookie 流程照旧"的回归。
  跑法：`python -m pytest tests/test_bearer_auth.py -q`。
