"""
益生菌/代谢物证据评估系统 - FastAPI 后端
集成 DeepSeek API 进行 AI 增强分析
"""
import json
import os
import time
from collections import defaultdict
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from typing import Optional
from openai import OpenAI

# ==================== 速率限制 ====================

# Per-IP rate limiter: max 10 AI analysis calls per IP per day
# (the expensive DeepSeek endpoint)
_ai_rate_window = 86400  # 24 hours in seconds
_ai_rate_limit = 10       # max calls per window
_ai_usage = defaultdict(list)  # IP → [timestamps]


def _check_ai_rate(ip: str) -> tuple[bool, int]:
    """Returns (allowed, remaining_calls). Cleans expired entries."""
    now = time.time()
    window_start = now - _ai_rate_window
    _ai_usage[ip] = [t for t in _ai_usage[ip] if t > window_start]
    used = len(_ai_usage[ip])
    remaining = max(0, _ai_rate_limit - used)
    return (used < _ai_rate_limit, remaining)


def _record_ai_call(ip: str):
    _ai_usage[ip].append(time.time())


# Per-IP general rate limiter: 60 requests per minute for all endpoints
_general_rate_window = 60
_general_rate_limit = 60
_general_usage = defaultdict(list)


def _check_general_rate(ip: str) -> bool:
    now = time.time()
    window_start = now - _general_rate_window
    _general_usage[ip] = [t for t in _general_usage[ip] if t > window_start]
    return len(_general_usage[ip]) < _general_rate_limit


def _record_general_call(ip: str):
    _general_usage[ip].append(time.time())


# ==================== 初始化 FastAPI 应用 ====================
app = FastAPI(
    title="证据评估系统 API",
    description="益生菌/代谢物证据等级评估 — 本地知识库 + DeepSeek AI 增强",
    version="2.0.0"
)

# ⚠️  CORS: Only allow your own domains, not "*"
ALLOWED_ORIGINS = [
    "https://haonl-7.github.io",
    "https://haonl-7.github.io/bioTriage",
    "http://localhost:3000",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)

# ==================== 初始化 DeepSeek 客户端 ====================
# 优先读环境变量；本地开发若未设置，则从同目录 .env 读取（.env 已被 .gitignore 忽略，不会提交）
if not os.getenv("DEEPSEEK_API_KEY"):
    _env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(_env_path):
        with open(_env_path, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line.startswith("DEEPSEEK_API_KEY=") and not _line.startswith("#"):
                    os.environ["DEEPSEEK_API_KEY"] = _line.split("=", 1)[1].strip()
                    break

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
if not DEEPSEEK_API_KEY:
    raise RuntimeError(
        "DEEPSEEK_API_KEY environment variable is required. "
        "Set it via: export DEEPSEEK_API_KEY=your-key-here"
    )

deepseek_client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com",
)

# ==================== 加载本地知识库 ====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KB_PATH = os.path.join(BASE_DIR, "knowledge_base.json")

try:
    with open(KB_PATH, "r", encoding="utf-8") as f:
        KNOWLEDGE_BASE = json.load(f)
    prob_count = len(KNOWLEDGE_BASE.get("probiotics", []))
    meta_count = len(KNOWLEDGE_BASE.get("metabolites", []))
    print(f"[启动] 知识库加载成功: {prob_count} 益生菌 + {meta_count} 代谢物")
except FileNotFoundError:
    print(f"[警告] 未找到知识库文件 {KB_PATH}")
    KNOWLEDGE_BASE = {"probiotics": [], "metabolites": []}
except json.JSONDecodeError as e:
    print(f"[错误] 知识库 JSON 解析失败: {e}")
    KNOWLEDGE_BASE = {"probiotics": [], "metabolites": []}


# ==================== 加载学术助手语料 ====================
DOCS_PATH = os.path.join(BASE_DIR, "documents.json")
try:
    with open(DOCS_PATH, "r", encoding="utf-8") as f:
        DOCUMENTS = json.load(f).get("documents", [])
    print(f"[启动] 学术助手语料加载成功: {len(DOCUMENTS)} 篇文献")
except FileNotFoundError:
    print(f"[警告] 未找到语料文件 {DOCS_PATH}")
    DOCUMENTS = []
except json.JSONDecodeError as e:
    print(f"[错误] 语料 JSON 解析失败: {e}")
    DOCUMENTS = []


# ==================== 工具函数 ====================

def search_knowledge(query: str) -> Optional[dict]:
    """
    在知识库中搜索匹配 query 的条目
    支持按菌株名、代谢物名、功能声称匹配（不区分大小写）
    """
    if not KNOWLEDGE_BASE:
        return None

    query_lower = query.strip().lower()
    best_match = None
    best_score = 0

    # 搜索 probiotics 和 metabolites 两个类别
    for category in ["probiotics", "metabolites"]:
        for item in KNOWLEDGE_BASE.get(category, []):
            score = 0
            name_cn = item.get("name", "").lower()
            name_en = item.get("name_en", "").lower()
            summary = item.get("summary", "").lower()
            summary_en = item.get("summary_en", "").lower()

            # 中文名称精确匹配
            if query_lower in name_cn:
                score += 5
            # 英文名称匹配
            if query_lower in name_en:
                score += 3
            # 摘要中包含关键词
            if query_lower in summary:
                score += 2
            if query_lower in summary_en:
                score += 1
            # Token 级匹配（处理多词查询）
            query_tokens = query_lower.split()
            for token in query_tokens:
                if token in name_cn or token in name_en:
                    score += 2

            if score > best_score:
                best_score = score
                best_match = {**item, "category": category}

    return best_match


def build_kb_context() -> str:
    """
    将本地知识库的关键信息构建为注入 DeepSeek 系统提示的上下文
    """
    lines = ["## 本地知识库已收录条目\n"]
    for cat, label in [("probiotics", "益生菌"), ("metabolites", "代谢物")]:
        lines.append(f"### {label}")
        for item in KNOWLEDGE_BASE.get(cat, []):
            lines.append(f"- **{item['name']}** ({item.get('name_en', '')})")
            lines.append(f"  证据等级: {item.get('evidence_level', 'N/A')}")
            scores = item.get("scores", {})
            lines.append(f"  评分: 有效性={scores.get('effectiveness','?')}, 安全性={scores.get('safety','?')}, 可及性={scores.get('accessibility','?')}, 证据强度={scores.get('evidence_strength','?')}")
            lines.append(f"  摘要: {item.get('summary', '')[:200]}...")
            lines.append("")
    return "\n".join(lines)


# ==================== DeepSeek AI 分析 ====================

SYSTEM_PROMPT = """你是一个专业的猪肠道微生物组与表观遗传学证据评估助手。
你的任务是基于用户输入的益生菌、代谢物或其他关键词，结合本地知识库和你的专业知识，对证据进行结构化评估。

## 证据等级标准（参照已发表论文的四级框架）

**益生菌功能声称（4级）：**
- Level 1：关联推断（共现网络分析、丰度-表型统计关联、体外实验、同属异种间接证据）
- Level 2：中度推断（猪模型中直接验证了效应分子功能，附机制通路数据，但不区分菌株来源）
- Level 3：强推断（小鼠单菌定植因果验证，人源菌株）
- Level 4：确立证据（猪源菌株在断奶仔猪中的单菌定植实验，经独立重复证实）

**代谢物四维矩阵（双向通路评估）：**
- 正向通路（Forward）：Level 0(无证据) → Level 1(关联推断/体外) → Level 2a(猪体外) → Level 2b(猪体内) → Level 3(因果验证) → Level 4(跨代证据)
- 反向通路（Reverse）：同上分级
- 耦合验证（Coupling）：Level 0(无) → Level 1(独立验证) → Level 2(同实验观察) → Level 3(干预因果) → Level 4(跨尺度耦合)
- 测量深度（Measurement）：Level 0(单隔室单时间点) → Level 1(多隔室或多时间点) → Level 2(多隔室+多时间点+多组学)

## 输出格式

你必须仅输出一个合法的 JSON 对象，不要包含任何其他文字、代码块标记或解释：

{
  "name": "中文名称",
  "name_en": "英文名称",
  "type": "probiotics 或 metabolites",
  "evidence_level": "证据等级，如 Level 2b",
  "scores": {
    "effectiveness": 0到5的整数,
    "safety": 0到5的整数,
    "accessibility": 0到5的整数,
    "evidence_strength": 0到5的整数
  },
  "summary": "基于现有科学证据的中文摘要（200-400字），概述验证状态和主要证据缺口",
  "summary_en": "English summary of verification status and key evidence gaps",
  "key_references": ["与评估相关的关键文献描述", "格式：Author (Year) Key Finding"],
  "research_priority": "P1/P2/P3 或 N/A（仅代谢物适用）",
  "confidence": "high / medium / low（此评估的信心水平）"
}

## 文献来源质量限制
评估时必须严格遵守以下文献筛选标准：
- 仅引用经同行评审（peer-reviewed）的正式出版物
- 排除以下来源：预印本服务器（bioRxiv、arXiv 等）、掠夺性期刊（predatory journals）、会议摘要、学位论文、未发表数据
- 优先引用以下高可信度期刊来源：Nature 系列、Science 系列、Cell 系列、Gut、Gut Microbes、Microbiome、ISME Journal、mBio、mSystems、Applied and Environmental Microbiology、Journal of Animal Science and Biotechnology、Animal Microbiome 等主流微生物组/动物科学期刊
- 若某一结论仅来自低可信度来源（影响因子 < 2 或未被 SCI 收录），必须在 summary 中明确标注"该结论基于较低质量证据来源"
- 若无法找到高质量来源支持某一结论，应在该维度评分上保守赋值为 0 或 1，并在 summary 中如实说明证据不足
- 引用格式统一为：Author et al. (Year) Journal Abbreviation, Key Finding. PMID: xxxxxxxx（如有）

## 注意事项
1. 优先使用本地知识库中的已有数据
2. 如果用户查询的条目未收录，基于你的专业知识给出合理评估，并将 confidence 设为 "low"
3. 评分（0-5）应保守赋值：0=无证据，1=关联推断，2=初步验证，3=直接验证，4=强因果验证，5=确立+独立重复
4. summary 应明确区分"已有验证"和"证据缺口"
5. 如果查询的是甲基化/表观遗传/屏障功能等通路机制，请将其映射到共代谢框架中评估
6. 如查询内容超出你的知识范围或仅有低质量文献支持，confidence 必须设为 "low"，并在 summary 开头注明"[注意：该评估基于有限的高质量证据]" """


def analyze_with_deepseek(question: str, kb_context: str) -> dict:
    """
    调用 DeepSeek API 进行证据评估分析
    先注入本地知识库上下文，让 DeepSeek 综合本地数据 + 自身知识给出评估
    """
    user_message = f"""## 用户查询
{question}

{kb_context}

请基于以上本地知识库数据和你的专业知识，对该查询进行结构化证据评估。直接输出 JSON，不要包含任何其他文字。"""

    try:
        response = deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.3,   # 低温度保证输出稳定性
            max_tokens=2048,
            extra_body={
                "enable_search": True,   # 启用 DeepSeek 联网搜索
            },
        )

        raw = response.choices[0].message.content.strip()

        # 清理可能的 markdown 代码块标记
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()
        # 有时 DeepSeek 返回 ```json ... ``` 格式
        if raw.startswith("json"):
            raw = raw[4:].strip()

        result = json.loads(raw)

        # 确保必需字段存在
        result.setdefault("type", "unknown")
        result.setdefault("confidence", "medium")
        result.setdefault("key_references", [])
        result.setdefault("research_priority", "N/A")
        result.setdefault("scores", {
            "effectiveness": 0,
            "safety": 0,
            "accessibility": 0,
            "evidence_strength": 0
        })

        return result

    except json.JSONDecodeError as e:
        print(f"[DeepSeek] JSON 解析失败: {e}")
        print(f"[DeepSeek] 原始响应: {raw[:500] if 'raw' in dir() else 'N/A'}")
        return {
            "name": question,
            "name_en": question,
            "type": "unknown",
            "evidence_level": "N/A",
            "scores": {"effectiveness": 0, "safety": 0, "accessibility": 0, "evidence_strength": 0},
            "summary": f"AI 分析结果解析失败，请稍后重试。原始响应片段：{raw[:200] if 'raw' in dir() else '无输出'}",
            "summary_en": "AI analysis parse error.",
            "key_references": [],
            "research_priority": "N/A",
            "confidence": "low",
            "error": str(e)
        }
    except Exception as e:
        print(f"[DeepSeek] API 调用失败: {e}")
        return {
            "name": question,
            "name_en": question,
            "type": "unknown",
            "evidence_level": "N/A",
            "scores": {"effectiveness": 0, "safety": 0, "accessibility": 0, "evidence_strength": 0},
            "summary": f"DeepSeek API 调用失败：{str(e)}",
            "summary_en": f"DeepSeek API error: {str(e)}",
            "key_references": [],
            "research_priority": "N/A",
            "confidence": "low",
            "error": str(e)
        }


# ==================== 学术助手 RAG（检索 + DeepSeek 接地问答） ====================
import re

# 中文术语 → 英文同义词映射（轻量双语检索：中文提问也能命中英文摘要）
_RAG_SYNONYMS = {
    "琥珀酸利用菌": ["succinate-utilizer", "succinatutens", "phascolarctobacterium"],
    "琥珀酸": ["succinate", "succinic"],
    "丙酸": ["propionate", "propionic"],
    "背膘": ["backfat", "fat accumulation", "fat deposition"],
    "植物乳杆菌": ["lactobacillus plantarum", "l. plantarum", "lactiplantibacillus plantarum", "lactiplantibacillus"],
    "乳酸菌": ["lactobacillus", "lactobacilli"],
    "断奶": ["wean", "weaned", "weaning"],
    "仔猪": ["piglet", "piglets"],
    "猪": ["pig", "pigs", "porcine", "swine"],
    "肠道": ["gut", "intestinal", "gastrointestinal"],
    "定植": ["colonization", "colonisation", "persist", "predominate"],
    "生长性能": ["growth performance", "average daily gain", "weight gain"],
    "猪肉品质": ["pork quality", "meat quality"],
    "肝脏": ["liver", "hepatic"],
    "胆固醇": ["cholesterol"],
    "饲喂": ["dietary", "feeding", "fed", "supplementation", "administered"],
    "代谢": ["metabolism", "metabolic", "metabolite"],
    "表观遗传": ["epigenetic"],
    "炎症": ["inflammat", "tlr4"],
    "脂肪酸": ["fatty acid", "scfa", "short-chain fatty acid"],
    "氨基酸": ["amino acid", "arginine"],
    "腹泻": ["diarrhea"],
    "死亡率": ["mortality"],
    "日增重": ["daily weight gain", "average daily gain"],
    "饲料转化": ["feed conversion", "fcr", "food conversion"],
}


def _retrieve_chunks(query: str, top_k: int = 3) -> list:
    """
    轻量双语词频检索：对每篇 doc 的 cite+title+passage 做 token 重叠计数，取 top_k。
    中文提问先经 _RAG_SYNONYMS 映射到英文同义词，英文提问直接用词级 token。
    不引入 torch / sentence-transformers / 向量库。
    """
    if not DOCUMENTS:
        return []
    q = (query or "").strip().lower()
    if not q:
        return []

    terms = set()
    for cn, ens in _RAG_SYNONYMS.items():
        if cn in q:
            terms.update(ens)
    for tok in re.findall(r"[a-z0-9][a-z0-9\-]*", q):
        if len(tok) >= 3:
            terms.add(tok)

    if not terms:
        return []

    scored = []
    for doc in DOCUMENTS:
        blob = " ".join([
            str(doc.get("cite", "")),
            str(doc.get("title", "")),
            str(doc.get("passage", "")),
        ]).lower()
        score = 0
        for t in terms:
            if t in blob:
                score += max(1, len(t) // 4)
        if score > 0:
            scored.append((score, doc))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [doc for _, doc in scored[:top_k]]


def _is_chinese(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)


RAG_SYSTEM_PROMPT = """你是一个学术文献问答助手，服务于一个共享的科研课题组。你只能依据用户消息中提供的"检索片段"回答问题。

## 硬性规则
1. 只基于提供的检索片段回答；禁止编造任何数据、结论或参考文献。
2. 如果检索片段中没有与问题相关的信息，直接回复"暂无相关资料"，不要延伸、猜测或补充。
3. 不得自行生成参考文献；只能引用检索片段中出现的文献标题与来源。
4. 回答语言必须与用户问题的语言一致（用户用中文则用中文，用英文则用英文）。
5. 回答末尾用一行标注信息来源文献（作者 + 年份 + 期刊）。PMID/DOI 只在检索片段明确给出时才照抄，绝不编造。
6. 回答要简洁、学术化；不要使用表情符号或多余的语气词。"""


def rag_answer(question: str, chunks: list) -> tuple:
    """调用 DeepSeek，基于检索片段生成带来源的答案。返回 (answer, sources)。"""
    context_lines = []
    for i, doc in enumerate(chunks, 1):
        cite = doc.get("cite", "")
        passage = doc.get("passage", "")
        ids = []
        if doc.get("pmid"):
            ids.append(f"PMID: {doc['pmid']}")
        if doc.get("doi"):
            ids.append(f"DOI: {doc['doi']}")
        id_str = (" | " + " | ".join(ids)) if ids else ""
        context_lines.append(f"[{i}] {cite}{id_str}\n    {passage[:1600]}")
    context = "\n\n".join(context_lines)

    user_message = f"""## 用户问题
{question}

## 检索片段
{context}

请基于以上检索片段回答问题。"""

    try:
        response = deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": RAG_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.2,
            max_tokens=1024,
        )
        answer = response.choices[0].message.content.strip()
    except Exception as e:
        print(f"[RAG] DeepSeek API 调用失败: {e}")
        answer = f"AI 服务暂时不可用，请稍后重试。（错误：{e}）"

    sources = []
    for doc in chunks:
        src = {
            "cite": doc.get("cite", ""),
            "title": doc.get("title", ""),
            "year": doc.get("year", ""),
            "journal": doc.get("journal", ""),
        }
        if doc.get("pmid"):
            src["pmid"] = doc["pmid"]
        if doc.get("doi"):
            src["doi"] = doc["doi"]
        sources.append(src)

    return answer, sources


# ==================== 全局中间件：通用速率限制 ====================

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    ip = request.client.host if request.client else "unknown"

    # Skip rate limit for static root path
    if request.url.path == "/":
        return await call_next(request)

    if not _check_general_rate(ip):
        return JSONResponse(
            status_code=429,
            content={
                "success": False,
                "error": "请求过于频繁，请稍后再试（每分钟最多 60 次请求）",
                "retry_after_seconds": 60,
            },
            headers={"Retry-After": "60"},
        )

    _record_general_call(ip)
    return await call_next(request)


# ==================== 全局异常处理 ====================

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": f"服务器内部错误：{str(exc)}",
            "detail": "请检查请求参数或联系管理员"
        }
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": f"请求错误（{exc.status_code}）：{exc.detail}",
        }
    )


# 读取 index.html 内容（模块加载时缓存）
INDEX_HTML_PATH = os.path.join(BASE_DIR, "index.html")
try:
    with open(INDEX_HTML_PATH, "r", encoding="utf-8") as f:
        INDEX_HTML = f.read()
    print(f"[启动] 前端页面加载成功 ({len(INDEX_HTML)} 字符)")
except FileNotFoundError:
    INDEX_HTML = None
    print("[警告] 未找到 index.html，根路径将返回 JSON")

# ==================== API 接口 ====================

@app.get("/")
async def root():
    """
    根路径：返回前端页面（生产）或 API 状态（开发/无前端文件时）
    """
    if INDEX_HTML:
        return HTMLResponse(content=INDEX_HTML)
    return {
        "service": "证据评估系统 API v2.0",
        "status": "运行中",
        "features": ["本地知识库检索", "DeepSeek AI 增强分析", "四维矩阵评分"],
        "knowledge_base": {
            "probiotics": len(KNOWLEDGE_BASE.get("probiotics", [])),
            "metabolites": len(KNOWLEDGE_BASE.get("metabolites", []))
        }
    }


@app.get("/api/evaluate_tier")
async def evaluate_tier(query: str = Query(..., description="搜索关键词")):
    """证据等级评估接口 — 从本地知识库匹配"""
    result = search_knowledge(query)

    if result:
        return {
            "success": True,
            "query": query,
            "tier": result.get("evidence_level", "N/A"),
            "strain": result.get("name", ""),
            "metabolite": result.get("name", ""),
            "claim": result.get("summary", "")[:100],
            "category": result.get("category", ""),
            "source": "知识库匹配"
        }
    else:
        return {
            "success": True,
            "query": query,
            "tier": "L4",
            "strain": query,
            "metabolite": "",
            "claim": "",
            "category": "",
            "source": "默认评级（知识库未匹配）",
            "note": "该条目尚未收录，建议使用 AI 分析获取评估"
        }


@app.get("/api/evaluate_matrix")
async def evaluate_matrix(query: str = Query(..., description="搜索关键词")):
    """四维评分接口"""
    result = search_knowledge(query)

    if result and "scores" in result:
        scores = result["scores"]
        return {
            "success": True,
            "query": query,
            "strain": result.get("name", ""),
            "metabolite": result.get("name", ""),
            "category": result.get("category", ""),
            "matrix": {
                "正向": scores.get("effectiveness", 0) / 5,    # 归一化到 0-1
                "反向": scores.get("safety", 0) / 5,
                "耦合": scores.get("accessibility", 0) / 5,
                "测量深度": scores.get("evidence_strength", 0) / 5
            },
            "scores_raw": scores,
            "source": "知识库匹配"
        }
    elif result:
        return {
            "success": True,
            "query": query,
            "strain": result.get("name", ""),
            "metabolite": result.get("name", ""),
            "category": result.get("category", ""),
            "matrix": {"正向": 0, "反向": 0, "耦合": 0, "测量深度": 0},
            "scores_raw": {},
            "source": "知识库匹配（评分缺失）"
        }
    else:
        return {
            "success": True,
            "query": query,
            "strain": query,
            "metabolite": "",
            "category": "",
            "matrix": {"正向": 0, "反向": 0, "耦合": 0, "测量深度": 0},
            "scores_raw": {},
            "source": "默认评分（知识库未匹配）"
        }


@app.get("/api/experiment_advice")
async def experiment_advice(query: str = Query(..., description="搜索关键词")):
    """实验建议接口"""
    result = search_knowledge(query)

    common_advice = [
        "体外发酵实验：使用模拟结肠发酵系统，检测目标代谢物产量变化",
        "16S rRNA 基因测序：确认菌株在复杂菌群中的相对丰度",
        "靶向代谢组学（LC-MS/MS）：精确定量目标短链脂肪酸",
        "Caco-2 / IPEC-J2 细胞模型：评估代谢物对肠上皮屏障功能的影响",
        "动物模型验证：使用无菌小鼠定植菌株，检测肠道屏障指标"
    ]

    if result:
        evidence = result.get("evidence_level", "")
        category = result.get("category", "")

        if "Level 4" in evidence:
            specific_advice = [
                "该条目已达到 Level 4（确立证据），可作为猪用益生菌开发的候选菌株",
                "建议开展大规模田间试验验证其在不同猪群中的效果一致性",
                "可考虑商业化开发：菌剂制备工艺优化、稳定性评估、剂量标准化"
            ]
        elif "Level 3" in evidence or "Level 2b" in evidence:
            specific_advice = [
                "当前证据较强，但核心缺口是猪模型中的菌株特异性因果验证",
                "最高优先级：分离猪源菌株 + 断奶仔猪单菌定植实验（填补 Level 4 空白）",
                "补充猪肠道多隔室（肠腔/上皮/微环境）代谢物浓度同步测量",
                "验证核心代谢基因（如 scpA/scpB/scpC）在猪源菌株中的保守性"
            ]
        elif "Level 2" in evidence or "Level 2a" in evidence:
            specific_advice = [
                "当前为中度证据（Level 2），猪模型中已有间接验证但缺乏菌株特异性因果证据",
                "建议开展猪模型直接因果验证：单一代谢物干预 + 表观遗传标记测量",
                "设计跨隔室采样方案（肠腔内容物 + 上皮组织 + 微环境参数）",
                "考虑从菌株功能验证升级为合成微生态体系验证"
            ]
        elif "Level 1" in evidence:
            specific_advice = [
                "当前证据较弱（Level 1），仅有关联推断或跨物种外推",
                "优先从体外实验开始：代谢通路酶活验证、底物转化效率测定",
                "猪模型基线数据建立：该代谢物在猪肠道不同隔室的浓度范围",
                "使用同位素示踪法确认代谢物由目标菌株/通路产生"
            ]
        else:
            specific_advice = [
                "当前证据不足，建议开展探索性研究",
                "首先进行菌株全基因组测序，注释代谢相关基因簇",
                "通过共培养实验验证菌株是否能够产生预期代谢物",
                "参考已发表的系统证据映射文献，设计靶向代谢组学检测方案"
            ]

        return {
            "success": True,
            "query": query,
            "strain": result.get("name", ""),
            "category": category,
            "evidence_level": evidence,
            "advice": specific_advice + common_advice,
            "source": "知识库匹配"
        }
    else:
        return {
            "success": True,
            "query": query,
            "strain": query,
            "category": "",
            "evidence_level": "L4",
            "advice": [
                "该条目暂未收录，建议从基础实验开始验证",
                "进行菌株全基因组测序，挖掘代谢相关基因簇",
                "查阅该条目所属类群的已知代谢功能文献",
                "开展非靶向代谢组学，发现潜在的代谢产物",
                "可使用「AI 深度分析」功能获取基于现有文献的初步评估"
            ] + common_advice,
            "source": "默认建议（知识库未匹配）"
        }


@app.get("/api/ai_analyze")
async def ai_analyze(request: Request, query: str = Query(..., description="需要 AI 分析的关键词")):
    """
    AI 深度分析接口 — DeepSeek 增强
    1. 先查本地知识库
    2. 无论是否命中，都调用 DeepSeek 进行增强分析
    3. 返回结构化的证据评估 JSON

    限流：每 IP 每天最多 10 次（DeepSeek API 调用费用较高）
    """
    ip = request.client.host if request.client else "unknown"

    # AI-specific rate check (10/day per IP)
    allowed, remaining = _check_ai_rate(ip)
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={
                "success": False,
                "error": f"AI 分析已达到每日限额（{_ai_rate_limit} 次/天）。请明天再试。",
                "remaining": 0,
                "limit_per_day": _ai_rate_limit,
            },
        )

    _record_ai_call(ip)

    # Step 1: 查询本地知识库
    local_match = search_knowledge(query)

    # Step 2: 构建知识库上下文
    kb_context = build_kb_context()

    # Step 3: 调用 DeepSeek
    ai_result = analyze_with_deepseek(query, kb_context)

    # Step 4: 合并本地命中信息
    return {
        "success": True,
        "query": query,
        "ai_analysis": ai_result,
        "rate_limit": {
            "remaining": remaining - 1,
            "limit_per_day": _ai_rate_limit,
        },
        "local_match": {
            "found": local_match is not None,
            "name": local_match.get("name", "") if local_match else "",
            "name_en": local_match.get("name_en", "") if local_match else "",
            "evidence_level": local_match.get("evidence_level", "") if local_match else "",
            "category": local_match.get("category", "") if local_match else "",
            "scores": local_match.get("scores", {}) if local_match else {},
            "summary": (local_match.get("summary", "")[:300] + "...") if local_match else ""
        },
        "source": "DeepSeek AI + 本地知识库"
    }


@app.get("/api/rag_chat")
async def rag_chat(request: Request, q: str = Query(..., description="用户提问")):
    """
    学术助手 RAG 问答接口
    1. 从 documents.json 语料检索 top_k 相关片段
    2. 注入 DeepSeek，返回带来源引用的回答
    限流：复用 AI 限流（每 IP 每天 10 次，防刷 DeepSeek 额度）
    """
    ip = request.client.host if request.client else "unknown"

    # AI 限流检查
    allowed, remaining = _check_ai_rate(ip)
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={
                "success": False,
                "error": f"学术助手已达到每日限额（{_ai_rate_limit} 次/天）。请明天再试。",
                "remaining": 0,
                "limit_per_day": _ai_rate_limit,
            },
        )

    question = (q or "").strip()
    if not question:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "问题不能为空"},
        )

    chunks = _retrieve_chunks(question, top_k=3)

    # 语料无匹配：直接返回"暂无相关资料"，不调用 DeepSeek（不消耗额度）
    if not chunks:
        no_hit = "暂无相关资料" if _is_chinese(question) else "No relevant material found."
        return {
            "success": True,
            "query": question,
            "answer": no_hit,
            "sources": [],
            "retrieved": 0,
            "rate_limit": {"remaining": remaining, "limit_per_day": _ai_rate_limit},
        }

    _record_ai_call(ip)
    answer, sources = rag_answer(question, chunks)

    return {
        "success": True,
        "query": question,
        "answer": answer,
        "sources": sources,
        "retrieved": len(chunks),
        "rate_limit": {
            "remaining": remaining - 1,
            "limit_per_day": _ai_rate_limit,
        },
    }


@app.get("/api/knowledge_base")
async def list_knowledge_base(
    category: Optional[str] = Query(None, description="筛选类别: probiotics / metabolites")
):
    """列出知识库所有条目"""
    if category and category in KNOWLEDGE_BASE:
        return {
            "success": True,
            "category": category,
            "count": len(KNOWLEDGE_BASE[category]),
            "items": [
                {
                    "name": item["name"],
                    "name_en": item.get("name_en", ""),
                    "evidence_level": item.get("evidence_level", ""),
                    "scores": item.get("scores", {})
                }
                for item in KNOWLEDGE_BASE[category]
            ]
        }
    return {
        "success": True,
        "total_probiotics": len(KNOWLEDGE_BASE.get("probiotics", [])),
        "total_metabolites": len(KNOWLEDGE_BASE.get("metabolites", [])),
        "categories": ["probiotics", "metabolites"]
    }


@app.get("/api/doi_abstract")
async def doi_abstract(doi: str = Query(..., description="论文 DOI 号")):
    """DOI 摘要接口 — 对接 CrossRef API（免费，无需 Key）"""
    import requests as req

    # Clean DOI input
    doi = doi.strip()
    if doi.startswith("https://doi.org/"):
        doi = doi.replace("https://doi.org/", "")
    if doi.startswith("http://doi.org/"):
        doi = doi.replace("http://doi.org/", "")

    crossref_url = f"https://api.crossref.org/works/{doi}"
    try:
        resp = req.get(crossref_url, headers={"User-Agent": "EvidenceApp/2.0 (mailto:research@example.com)"}, timeout=10)
        if resp.status_code != 200:
            return {"success": False, "doi": doi, "error": f"CrossRef returned HTTP {resp.status_code}"}

        data = resp.json()
        msg = data.get("message", {})

        # Extract metadata
        title = (msg.get("title") or [""])[0] if msg.get("title") else ""
        abstract_raw = msg.get("abstract", "")
        # Clean HTML tags from abstract
        import re
        abstract = re.sub(r"<[^>]+>", "", abstract_raw) if abstract_raw else ""
        abstract = abstract.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")

        # Authors
        authors = []
        for au in msg.get("author", [])[:10]:
            family = au.get("family", "")
            given = au.get("given", "")
            if family:
                authors.append(f"{family} {given}".strip())

        # Journal info
        journal = ""
        container = msg.get("container-title") or [""]
        if isinstance(container, list) and container:
            journal = container[0]
        pub_year = msg.get("created", {}).get("date-parts", [[None]])[0][0]
        publisher = msg.get("publisher", "")

        return {
            "success": True,
            "doi": doi,
            "title": title,
            "abstract": abstract[:1500] if abstract else "(No abstract available from CrossRef)",
            "authors": authors,
            "first_author": authors[0] if authors else "",
            "journal": journal or publisher,
            "year": pub_year,
            "publisher": publisher,
            "url": f"https://doi.org/{doi}",
            "source": "CrossRef API",
        }
    except Exception as e:
        # Fallback: try PubMed E-utilities as secondary source
        try:
            pubmed_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term={doi}[doi]&retmode=json"
            pr = req.get(pubmed_url, timeout=10)
            idlist = pr.json().get("esearchresult", {}).get("idlist", [])
            if idlist:
                pmid = idlist[0]
                efetch_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id={pmid}&retmode=xml&rettype=abstract"
                import xml.etree.ElementTree as ET
                er = req.get(efetch_url, timeout=10)
                root = ET.fromstring(er.content)
                article = root.find(".//PubmedArticle//Article")
                if article is not None:
                    title_elem = article.find(".//ArticleTitle")
                    title = "".join(title_elem.itertext()) if title_elem is not None else ""
                    abst_elem = article.find(".//Abstract/AbstractText")
                    abstract = "".join(abst_elem.itertext()) if abst_elem is not None else ""
                    jn_elem = article.find(".//Journal/Title")
                    journal = jn_elem.text if jn_elem is not None else ""
                    return {
                        "success": True, "doi": doi,
                        "title": title, "abstract": abstract[:1500],
                        "journal": journal, "url": f"https://doi.org/{doi}",
                        "source": "PubMed E-utilities (CrossRef fallback)",
                    }
        except Exception:
            pass

        return {
            "success": False, "doi": doi,
            "error": f"Failed to resolve DOI via CrossRef or PubMed: {str(e)}",
        }


# ==================== 启动入口 ====================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
