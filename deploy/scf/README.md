# 腾讯云云函数（SCF Web 函数）代码包

生产后端跑在腾讯云**云函数 Web 函数**上，发布方式是"本地打 zip → 控制台上传"。
本目录就是这条路径的全部配置，与 render.yaml（未使用的另一条路径）互不影响。

| 文件 | 作用 |
| --- | --- |
| `scf_bootstrap` | 平台入口脚本：把 `./pkg` 加进 PYTHONPATH，用运行时自带 Python 启动 `uvicorn api:app`，监听 `0.0.0.0:9000`（LF 行尾、可执行位 0755） |
| `build-zip.ps1` | Windows 打包脚本（主用）：交叉安装 manylinux wheel + 打 zip，保留 scf_bootstrap 的可执行位 |
| `build-zip.sh` | Linux / WSL 打包脚本（备用），行为一致 |
| `build/` | 生成目录（业务文件 + `pkg/` 依赖），每次构建前清空 |
| `../../dist/scf-biotriage-py310.zip` | 默认产物 |

---

## 1. 打包

Windows（PowerShell 5.1 或 7 均可）：

~~~powershell
# 先看清单，不动任何文件
powershell -NoProfile -ExecutionPolicy Bypass -File deploy/scf/build-zip.ps1 -DryRun

# 正式构建（运行时是 3.9 就传 -PythonVersion 3.9）
powershell -NoProfile -ExecutionPolicy Bypass -File deploy/scf/build-zip.ps1 -PythonVersion 3.10
~~~

Linux / WSL：

~~~bash
./deploy/scf/build-zip.sh                 # PYTHON_VERSION=3.10 默认
PYTHON_VERSION=3.9 ./deploy/scf/build-zip.sh
./deploy/scf/build-zip.sh --dry-run
~~~

**为什么必须交叉安装**：在 Windows 上直接 `pip install` 会装出 Windows 二进制
（`*.pyd` / `*.dll`，例如 pydantic_core、psycopg2、lxml），上传到 Linux 运行时后
`import` 立刻报 `invalid ELF header`。所以脚本固定用：

~~~text
pip install --target build/pkg --implementation cp --only-binary=:all: --python-version <ver> \
    --platform manylinux2014_x86_64 --platform manylinux_2_17_x86_64 --platform manylinux_2_28_x86_64 \
    -r requirements.txt
~~~

`--only-binary=:all:` 是关键：它禁止源码编译，从而杜绝"静默装上 Windows 二进制"。
脚本在安装后会再查一次 `pkg/` 里有没有 `*.pyd / *.dll / *.lib`，发现即**中止且不产出 zip**。

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `-PythonVersion` | `3.10` | 必须与控制台里函数的运行时版本一致（wheel ABI 前缀 cp310/cp39） |
| `-Platforms` | manylinux2014 / 2_17 / 2_28 | 某个包只有更新的 manylinux tag 时，把对应平台加进来 |
| `-Output` | `dist/scf-biotriage-py<ver>.zip` | 产物路径 |
| `-DryRun` | 关 | 只打印将要打进包的文件清单 + 将执行的 pip 命令 |
| `-SkipDeps` | 关 | 复用已有 `build/pkg`（离线调试 / 拆层时用） |
| `-SizeWarnMb` | `40` | 体积提示阈值，仅提示不阻断 |

打包后自查（任选其一）：

~~~bash
python -c "import zipfile;z=zipfile.ZipFile('dist/scf-biotriage-py310.zip');[print(n) for n in z.namelist()[:20]];print('entries=',len(z.namelist()))"
unzip -l dist/scf-biotriage-py310.zip | head -30
~~~

必须看到的条目：`api.py`、`accounts/router.py`、`account.html`、`assets/account.js`、
`scf_bootstrap`、以及 `pkg/` 下的依赖。**不应**出现 `tests/`、`.venv`、`__pycache__`、`*.db`。

---

## 2. 控制台上传

1. 云函数控制台 → 函数管理 → 代码管理 → **上传 zip**（本地文件），选刚生成的 zip。
2. 函数类型必须是 **Web 函数**；运行时选 **Python 3.10**（与 `-PythonVersion` 一致）。
   入口/启动脚本用包根目录的 `scf_bootstrap`（Web 函数固定读它）。
3. **端口固定 9000**：Web 函数要求服务监听 `0.0.0.0:9000`，脚本里已写死，不要改成 80/8080。
4. 执行超时与内存：AI 分析端点 `/api/ai_analyze` 是同步阻塞的远程调用，建议超时 **≥ 30 秒**、
   内存 **≥ 256 MB**（内存太小会让 C 扩展 OOM）。
5. 配置环境变量（第 4 节清单）。`DEEPSEEK_API_KEY` 缺失时，`api.py` 在 import 阶段就抛错，函数会直接启动失败。
6. 发布后验证：
   - `GET https://<id>.ap-<region>.tencentscf.com/api/auth/me` → `{"authenticated": false, ...}`
   - `GET .../api/auth/login` → 302 跳到 ORCID 授权页
   - `GET .../account` → 200 账户页

---

## 3. 运行时版本怎么核对

* 控制台：函数配置 → 基本信息 → **运行环境 / 运行时**，确认是 Python 3.10。
* 本地：打包时传同一个 `-PythonVersion`（决定 wheel 的 cp3xx ABI）。
* 云端：`scf_bootstrap` 启动时会打印一行
  `[scf_bootstrap] using /var/lang/python3.10/bin/python3 3.10.4`，
  在「日志查询」里能看到 —— 这就是**最终选用的解释器绝对路径与现实版本**。
  选择顺序：`SCF_PYTHON_BIN`（强制，最高优先级）→ 固定候选
  （`/var/lang/python3.10/bin/python3`、`/var/lang/python3.9/bin/python3`、`python3.10`、`python3.9`、
  `python3`、`/var/lang/python3/bin/python3`）→ **目录扫描兜底**
  （`/var/lang/python*/bin/python3`、`/var/lang/python3*/bin/python3`、`/opt/python*/bin/python3`、
  `/usr/local/bin/python3*`、`/usr/bin/python3.[0-9]*`，命中候选按版本从高到低排序）。
  **每个候选都会打一行日志（含实际版本与接受/拒绝原因）**，低于 3.10 的一律拒绝。
  注意 `/var/lang/python3` 是基础镜像的系统 Python **3.6**，只作为最后兜底且必然被拒。
  扫描模式可用 `SCF_PYTHON_SCAN_PATTERNS` 覆盖（非默认镜像布局/自测时才需要）。
* 版本不一致的典型报错：`invalid ELF header` / `SyntaxError: future feature annotations is not defined` /
  `ModuleNotFoundError: pydantic_core._pydantic_core` / `No module named 'uvicorn'`。
  **先按 8.1 核对运行时版本**（多半是运行时不是 3.10），确认版本对得上再怀疑包本身。

---

## 4. 环境变量清单（与代码逐条对齐，来源：grep api.py 与 accounts/ 的 os.getenv）

| 变量 | 必填 | 默认 | 代码位置 | 说明 |
| --- | --- | --- | --- | --- |
| `DEEPSEEK_API_KEY` | **是** | 无 | api.py:80 | 缺失时 import 阶段直接 RuntimeError，函数启动失败 |
| `DATABASE_URL` | **是**（生产必须 Postgres） | `sqlite:///./biotriage.db` | accounts/db.py:25 | 云函数没有持久磁盘，见第 5 节；写成 `postgresql://user:pass@host:5432/db` 即可，代码会自动改成 `postgresql+psycopg2://` |
| `ORCID_CLIENT_ID` | **是** | 空 | accounts/orcid.py:21 | ORCID 开发者后台的 Client ID |
| `ORCID_CLIENT_SECRET` | **是** | 空 | accounts/orcid.py:25 | 同上，Secret 只显示一次 |
| `ORCID_BASE_URL` | 建议显式设 | `https://sandbox.orcid.org` | accounts/orcid.py:17 | **生产必须设 `https://orcid.org`**，否则会用沙箱（沙箱 iD 与生产不互通） |
| `APP_BASE_URL` | **是** | 空 | accounts/router.py:113、accounts/session.py:80 | 必须是函数公网域名 `https://1477715351-0lu4gk6z6i.ap-chengdu.tencentscf.com`；决定 `redirect_uri` 与 cookie 的 `Secure` |
| `SESSION_SECRET` | **是** | 进程内随机密钥 | accounts/session.py:32 | 缺失时每次冷启动/每个实例密钥都不同 → 用户登录态随机失效 |
| `SESSION_MAX_AGE` | 否 | `2592000` | accounts/session.py:26 | 会话有效期（秒） |
| `AI_DAILY_QUOTA_PER_USER` | 否 | `5` | accounts/router.py:58 | 登录用户每天 AI 分析次数 |
| `AI_DAILY_QUOTA_ANON` | 否 | `1` | accounts/router.py:59 | 匿名每 IP 每天次数 |
| `BT_TESTING` | **生产禁止设置** | 未设置 | accounts/orcid.py:42 | 测试钩子；设为 `1` 配合下一项可伪造登录 |
| `BT_FAKE_ORCID` | **生产禁止设置** | 未设置 | accounts/orcid.py:44 | 测试用假 profile |

另需在 ORCID 开发者后台把 Redirect URI 登记为 `APP_BASE_URL + /api/auth/callback`
（本函数即 `https://1477715351-0lu4gk6z6i.ap-chengdu.tencentscf.com/api/auth/callback`），逐字符匹配。

---

## 5. SQLite 在云函数下不可用

* 云函数的代码目录是**只读**的，只有 `/tmp` 可写，且 `/tmp` 随实例销毁、不跨实例共享。
* 因此缺省 `sqlite:///./biotriage.db` 在云端要么写失败（只读目录），要么每次冷启动重置、
  多实例各写各的 —— 会话、收藏、评估历史、配额计数全部不可靠。
* **生产必须外接 Postgres**：`DATABASE_URL=postgresql://...`，驱动 `psycopg2-binary` 已随 requirements.txt 打进包。
* 连接数要算：云函数实例数 × 每实例连接池 ≤ 数据库上限。建议小池（1~2）或接数据库代理；
  配额计数是"读-加-写"，高并发下会有轻微超额，可接受但不建议把 `AI_DAILY_QUOTA_*` 设得很紧。

---

## 6. 体积限制与改用「层」

* SCF 对**代码包**和**层**都有体积上限，且会随平台策略调整；
  **具体数值以上传时控制台的提示与腾讯云当期文档为准**，本脚本只做提示（`-SizeWarnMb` 默认 40 MB，仅告警不阻断）。
* 超限时把依赖拆到**层（Layer）**，代码包只留业务代码：

  1. 代码包：`build-zip.ps1 -SkipDeps`（或构建后删掉 `build/pkg`）→ 只含 api.py/accounts/前端文件/scf_bootstrap。
  2. 层：另建一个目录 `layer/python`，用同样的交叉安装参数把依赖装进去
     （`pip install --target layer/python --platform manylinux2014_x86_64 --python-version 3.10 --only-binary=:all: -r requirements.txt`），
     再把 `layer/python` 打成层 zip（层内保留 `python/` 这一级目录）。
  3. 控制台建层 → 在函数「层管理」里挂载该层。
  4. `scf_bootstrap` 里把层的挂载路径加进 PYTHONPATH，例如
     `export PYTHONPATH="/opt/python:$APP_DIR/pkg:$APP_DIR"`。
     **层的实际挂载路径是否已自动进 `sys.path`，以当期文档为准**；显式加 PYTHONPATH 最稳。
  5. 层版本更新后需要重新发布函数版本才生效（控制台会提示）。
* 另一个减重方向：确认是否真需要 `uvicorn[standard]`（它带 httptools/uvloop 等二进制），
  纯 `uvicorn` 在云函数单实例场景通常够用；改动 `requirements.txt` 属于后端负责人的边界。

---

## 7. 回滚

1. **发版本再切流量**：每次上传后在「版本管理」发布一个版本，出问题就选中上一版本回滚/切换流量。
2. **留 zip**：`dist/` 下保留历史包（建议改名为 `scf-biotriage-py310-2026xxxx.zip`），可随时重新上传。
3. **配置回滚**：环境变量改动同样要记下来（尤其 `APP_BASE_URL` / `ORCID_BASE_URL` / `SESSION_SECRET`），
   换 `SESSION_SECRET` 等于强制所有用户重新登录。
4. **数据不回滚**：本系统只用 `create_all`（不删列），代码回退一般不会破坏数据；
   但如果做过手工删表/改表，回滚代码也救不回数据，务必先备份数据库。

---

## 8. 常见错误对照表

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `invalid ELF header` | 包里是 Windows wheel，**或**运行时 Python 版本低于 pkg 的 cp310 wheel | 见下面 **8.1**：先核对运行时版本，再确认不是手工 `pip install -t` 混进了 Windows 二进制 |
| `SyntaxError: future feature annotations is not defined` | 解释器版本太低（这句话是 Python 3.6 报的）——运行时/解释器与 pkg 里 cp310 wheel 不匹配 | 见下面 **8.1** |
| 平台报「启动失败 / scf_bootstrap: permission denied」 | zip 里 scf_bootstrap 丢了可执行位（Compress-Archive 会丢） | 用 `build-zip.ps1`（它显式写入 0755）；Linux 侧确保 `chmod +x` 后再 zip |
| `scf_bootstrap: bad interpreter` / `$'` 之类怪错 | 文件是 CRLF | 存成 LF（脚本会检测并中止） |
| 函数起来但所有请求 502 | 没监听 `0.0.0.0:9000` | 用本目录 scf_bootstrap，不要自己写端口 |
| `/api/auth/me` 404 | 包里的 api.py 是旧版（没有 accounts 路由） | 重新打包；确认条目里有 `accounts/router.py` |
| 启动即失败、日志里 `DEEPSEEK_API_KEY` | 环境变量没配 | 控制台补 `DEEPSEEK_API_KEY` |
| 登录成功但一刷新就掉 | 没配 `SESSION_SECRET`（每实例随机密钥） | 配一个固定的 32+ 字节随机值 |
| 回调报 `invalid_redirect_uri` | `APP_BASE_URL` 与 ORCID 后台登记不一致 | 两处改成同一个 `https://.../api/auth/callback` |
| 所有匿名用户共用 1 次额度 | 前置代理终结 TLS，没开 `--proxy-headers` | 本目录 scf_bootstrap 已内置（原理见 docs/ORCID账号系统.md §10） |
| 数据重启就没了 | 用了 SQLite | 换外部 Postgres（第 5 节） |
| 启动即 ModuleNotFoundError: exceptiongroup（其它本不该缺的包同理） | pip 的 --python-version 不改变环境标记解析，python_version < 3.11 这类条件依赖被静默跳过 | 见 **第 10 节**（补丁依赖 + 打包后依赖审计） |

---

### 8.1 报 future feature annotations is not defined 或 invalid ELF header —— 运行时/解释器版本不对

这两个报错是**同一个根因**：pkg/ 里的依赖是按 `cp310 manylinux` 交叉安装的
（uvicorn / pydantic 2 / SQLAlchemy 2 也都要求 3.7+），而函数实际用的解释器不是 3.10。
实测事故现场长这样（腾讯云 SCF 返回 443）：

~~~text
1 code exit unexpected: Traceback (most recent call last):
  File "/var/lang/python3/lib/python3.6/runpy.py", line 183, in _run_module_as_main
  File "/var/user/pkg/uvicorn/__init__.py", line 1, in <module>
  File "/var/user/pkg/uvicorn/config.py", line 1
    from __future__ import annotations
SyntaxError: future feature annotations is not defined
~~~

注意 `/var/lang/python3` 是**基础镜像的系统 Python 3.6**，不是函数运行时；直接用它会得到上面这段。

**怎么改（控制台操作位置）**：

1. 云函数控制台 → 你的函数 → **函数配置** → **基本信息** → 右上角「编辑」。
2. 找到 **运行环境 / 运行时**，选 **Python 3.10**（与打包时的 `-PythonVersion 3.10` 一致）→ 保存。
3. **重新上传/发布**一次代码包（换运行时后必须重新部署，旧实例不会自动换解释器）。
4. 发布完看一次日志（见下），确认 `using` 那行已经是 3.10。

**怎么确认（看日志里的 using 行）**：`scf_bootstrap` 会把每个候选都打一行，最后打一行最终选用：

~~~text
[scf_bootstrap] 开始选择解释器：要求 >= 3.10
[scf_bootstrap] 候选 /var/lang/python3.10/bin/python3 -> 未找到或不可执行（跳过）
[scf_bootstrap] 候选 /var/lang/python3.9/bin/python3 -> 未找到或不可执行（跳过）
[scf_bootstrap] 候选 python3.10 -> 未找到或不可执行（跳过）
[scf_bootstrap] 候选 python3.9 -> 未找到或不可执行（跳过）
[scf_bootstrap] 候选 python3 -> 未找到或不可执行（跳过）
[scf_bootstrap] 候选 /var/lang/python3/bin/python3 -> 3.6.15 拒绝（要求 >= 3.10）
[scf_bootstrap] 固定候选都不满足，开始扫描：/var/lang/python*/bin/python3 ...
[scf_bootstrap] 扫描到 /var/lang/python3.10/bin/python3 -> 3.10.4（候选，参与按版本从高到低排序）
[scf_bootstrap] 扫描到 /var/lang/python3.6/bin/python3 -> 3.6.15（候选，参与按版本从高到低排序）
[scf_bootstrap] 扫描 /var/lang/python3.10/bin/python3 -> 3.10.4 接受
[scf_bootstrap] using /var/lang/python3.10/bin/python3 3.10.4
[scf_bootstrap] cwd=/var/user PYTHONPATH=/var/user/pkg:/var/user
~~~

* 最后那行 `using <path> <version>` 是**最终选用的解释器绝对路径与实际版本**，必须是 `3.10.x`（或更高）。
* 每一条 `候选 …` / `扫描 …` 日志都带版本与"接受/拒绝/未找到"原因 ——
  下次线上排查可以直接从日志里挑一个版本 >= 3.10 的绝对路径，填到 `SCF_PYTHON_BIN`。
* 一个满足版本的都没有时，函数会**直接以非 0 退出**并打印全部候选，而不是静默用 3.6 起服务；
  错误信息里给了两条可操作指令（改运行时 / 设 `SCF_PYTHON_BIN`）。
* **强制指定是硬约束**：一旦设了 `SCF_PYTHON_BIN` 而它版本不够（或路径不对），
  脚本会立刻报错退出，**不会**偷偷换用别的解释器 —— 这样"我明明指定了却没生效"不会发生。

**别搞错方向**：这个错不是"包打坏了"，重打包解决不了；先确认运行时版本，再确认包是用 `-PythonVersion 3.10` 打的。

---

## 9. 不要做的事

* 不要把 `.venv/`、`tests/`、`*.db`、`deploy/scf/build/` 打进包（脚本已排除）。
* 不要在包里放任何密钥文件；密钥一律走控制台环境变量。
* 不要手工 `pip install -t` 到包目录（一定会中招 Windows 二进制）。
* 不要设置 `BT_TESTING` / `BT_FAKE_ORCID`（等于开后门）。

---

## 10. 依赖审计与条件依赖（为什么包里会缺 exceptiongroup）

### 10.1 线上症状

上传后函数起不来（443），日志里是：

~~~text
File "/var/user/pkg/anyio/_core/_exceptions.py", line 9, in <module>
  from exceptiongroup import ...
ModuleNotFoundError: No module named 'exceptiongroup'
~~~

意思是：解释器版本已经对了（日志里 `using /var/lang/python310/lib/python3.10 ...`），**但包里少了一个依赖**。

### 10.2 根因：pip 的 --python-version 不改变环境标记的解析

`anyio-4.15.1.dist-info/METADATA` 里写着：

~~~text
Requires-Dist: exceptiongroup>=1.0.2; python_version < "3.11"
Requires-Dist: idna>=2.8
Requires-Dist: typing_extensions>=4.16.0; python_version < "3.15"
~~~

交叉安装时我们告诉 pip 目标平台是 manylinux、目标 Python 是 3.10，但 pip 的 `--platform` / `--python-version` **只影响 wheel 兼容性标签与下载**；
**依赖解析阶段的环境标记（environment marker）仍按当前运行中的解释器评估**。
本机是 3.12/3.14 时 `python_version < "3.11"` 判为**假**，于是 `exceptiongroup` 被静默跳过 —— 而线上运行时是 **3.10**，它真的需要这个包。

### 10.3 脚本怎么处理

* **显式补丁依赖**：两个脚本各有一个命名清晰的变量，把「目标版本需要、但会被本机评估跳过」的包追加进去：

  * `build-zip.ps1`：`$TargetPythonExtraDeps = @('exceptiongroup>=1.0.2')`
  * `build-zip.sh`：`EXTRA_TARGET_DEPS="exceptiongroup>=1.0.2"`

  这些**不要写进 requirements.txt** —— 那是应用运行依赖，Python 3.11+ 根本不需要 `exceptiongroup`。
  以后遇到同类情况（某包只在低版本才需要），往这两个变量里加一条即可。

* **打包后依赖审计**（两个脚本都在打 zip 之前跑）：扫描 `pkg/*.dist-info/METADATA` 的每一条 `Requires-Dist`，按「目标版本」判断 marker 是否成立：

  * 支持 `python_version` / `python_full_version` 的 `< <= == != > >=` 以及 `and` / `or` 组合；
  * `extra == ...` 视为可选依赖、不适用；平台类 marker（`sys_platform` 等）本审计不判定，既不算通过也不算失败；
  * marker 成立但 `pkg` 里没有对应 `*.dist-info` -> **打包失败并打印缺失包名**，绝不产出坏包；
  * 另外用每个 dist-info 的 `RECORD` 抽查文件完整性（例如包目录被误删会被抓出来）。

* 审计通过时打一行摘要（可直接 grep）：

  ~~~text
  [scf] 依赖审计通过：检查 36 个包，条件依赖 145 条，缺失 0（目标 Python 3.10，其中成立 6 条）
  ~~~

  失败时形如：

  ~~~text
  [scf] 缺失条件依赖：exceptiongroup（由 anyio-4.15.1.dist-info 在 Python 3.10 下要求）
  [scf] 依赖审计失败：检查 35 个包，条件依赖 143 条，问题 1 条
  ~~~

### 10.4 以后再遇到 ModuleNotFoundError，按这个顺序查

1. 包到底装了没：`ls -d deploy/scf/build/pkg/<包名>*`（或看 zip 里有没有 `pkg/<包名>/`）。
2. 没装的话先确认它是不是**条件依赖**：`grep -rh ^Requires-Dist deploy/scf/build/pkg/*/METADATA` 里搜包名（典型输出带 `; python_version < "3.11"` 这类 marker）。
3. marker 与目标版本比对：目标 3.10 时 `python_version < "3.11"`、`<= "3.10"`、`python_full_version < "3.11"` 都算**成立**，即必须装。
4. 成立却没装 -> 加到 10.3 的补丁依赖变量里重打包；**不要**改 requirements.txt（会污染 3.11+ 的运行环境）。
5. 重新打包后确认审计那行是 `缺失 0`，再上传。

> 提示：审计只在打包机上跑，它证明的是「包内依赖自洽」，不能替代线上冒烟；上传后仍建议打一次 `/api/auth/me`。

