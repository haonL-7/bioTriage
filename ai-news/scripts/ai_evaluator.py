#!/usr/bin/env python3
"""
文献评估脚本 — 统一证据评估框架（对齐两篇手稿）
- 益生菌：六级证据体系 (L1a-L4, 含 Level 3.5) — Liu & Feng, "Tiered Evidence Framework"
- 代谢物：四维矩阵评分 (Forward/Reverse/Coupling/Depth) — Liu & Feng, "Compartmentalized Co-metabolism"
- 区室化追踪：Luminal / Epithelial / Microenvironment
使用 DeepSeek API 为主，GLM-4-Flash 为备用，本地规则为最终降级
"""

import json
import os
import sys
import time
from datetime import datetime
from typing import Optional

# ==================== 配置 ====================

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
RAW_ARTICLES_FILE = os.path.join(DATA_DIR, "raw_articles.json")
SCORED_ARTICLES_FILE = os.path.join(DATA_DIR, "scored_articles.json")

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"

GLM_API_KEY = os.environ.get("GLM_API_KEY", "")
GLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
GLM_MODEL = "glm-4-flash"

MAX_ARTICLES_PER_RUN = int(os.environ.get("MAX_ARTICLES", "30"))

# 第二次独立评分（AIHOT 思路：两次独立评分、取和过门槛）。
# 两次 total_score 之和低于此值 → 判定 should_include=False。
SECOND_OPINION_MIN_SUM = int(os.environ.get("SECOND_OPINION_MIN_SUM", "6"))

# ==================== 评估 Prompt ====================

EVALUATION_PROMPT = """You are a senior researcher in gut microbiome and host-microbe co-metabolism. Evaluate the following research paper using a rigorous, publication-aligned evidence evaluation framework.

This evaluation system is based on two methodological frameworks:
- Liu H, Feng W. "Compartmentalized Co-metabolism in the Porcine Gut: Systematic Evidence Mapping and a Theoretical Framework for Epigenetic–Microbiome Interactions" — defines the Compartmentalized Co-metabolism Interaction Network Model and Four-Dimensional Matrix Rating System.
- Liu H, Feng W. "A Tiered Evidence Framework for Assessing Translational Readiness of Candidate Probiotics in Livestock: A Systematic Evidence Mapping with Methodological Development" — defines the six-tier evidence hierarchy (L1a–L4) with strain-specificity and target-host validation dimensions.

---

## Part A: Evidence Level (choose the HIGHEST applicable level)

### For PROBIOTIC/STRAIN entries — use the Six-Tier Evidence Framework:

| Level | Definition |
|-------|-----------|
| **L0** | No relevant evidence exists for this functional claim in any model system. |
| **L1a** | Mechanistic evidence: in vitro pure-culture metabolic assays, ex vivo tissue/cell-based experiments, indirect mechanistic evidence from heterologous species within the same genus. |
| **L1b** | Association inference: genus-level co-occurrence network analyses, abundance-phenotype statistical associations, metagenomic functional inferences. |
| **L2a** | Moderate inference — effector molecule validated in target host: direct validation of the effector molecule (e.g., propionate) in a porcine/large-animal model, with mechanistic pathway data, but WITHOUT strain-specific attribution. |
| **L2b** | Direct in vivo validation in porcine/large-animal model for at least one direction, with partially validated mechanism. Distinct from L2a in that the intervention is strain-targeted (not just metabolite supplementation). |
| **L3** | Strong inference: causal validation through mono-colonization with the target bacterium in murine models using human-derived strains. |
| **L3.5** | Transitional validation: mono-colonization using human-derived strains in a porcine model. Deconvolves host-species transfer from strain-origin transfer. |
| **L4** | Established evidence: mono-colonization in the target host using a target-host-derived strain, with independent replication. For metabolically dependent taxa, requires demonstration of adequate substrate availability. |

**Critical distinction**: L1–L3 address mechanistic plausibility under controlled conditions; L4 addresses translational efficacy under ecologically realistic conditions. These are qualitatively different questions.

### For METABOLITE entries — use the Four-Dimensional Matrix evidence tier per dimension:

- **Level 0**: No evidence for this dimension in any model system.
- **Level 1**: Association inferred from co-occurrence network analysis or statistical correlation in porcine gut data.
- **Level 2a**: In vitro porcine model validation (e.g., porcine ileal organoids, IPEC-J2 cells).
- **Level 2b**: In vivo porcine model validation (e.g., oral metabolite supplementation) demonstrating functional effects.
- **Level 3**: Causal validation in murine models (mono-colonization, gene knockout) confirming the causal chain.
- **Level 4**: Causal validation in porcine models with independent replication by a separate laboratory.

---

## Part B: The Compartmentalized Co-metabolism Model

The gut is divided into three functionally distinct compartments. Evaluate which compartments this paper measures:

1. **Luminal Compartment**: Site of microbial metabolism. Metabolites at millimolar concentrations (butyrate ~4–20 mM). Timescale: minutes to hours.
2. **Epithelial Compartment**: Site of host epigenetic regulation. Intracellular metabolite concentrations substantially lower than luminal (~3 orders of magnitude). Carrier-mediated transport (e.g., MCT1). Timescale: hours to days.
3. **Microenvironment Compartment**: Site of feedback pathways. Host-secreted immune factors, pH, oxygen, mucus layer. Timescale: days to weeks.

A molecule has DIFFERENT concentrations, functions, and dynamics in each compartment. Interactions occur only at compartment interfaces.

---

## Part C: Four-Dimensional Bidirectional Matrix

### Dimension I — Forward Pathway (Microbe → Host): score 0–4
How strongly does this paper demonstrate that gut microbes/metabolites CAUSE changes in the host?
- **0**: No forward evidence (pure association, review, or no data).
- **1**: Association inferred from co-occurrence networks or statistical correlation in porcine gut data.
- **2a**: In vitro porcine model validation (organoids, IPEC-J2 cells) with specific metabolite-to-epigenetic-mark linkage.
- **2b**: In vivo porcine model validation (oral metabolite supplementation) demonstrating regulation of host epigenetic marks or physiological outcomes.
- **3**: Causal validation in murine models (mono-colonization, gene knockout) confirming the forward pathway causal chain.
- **4**: Causal validation in porcine models with independent replication by a separate laboratory.

### Dimension II — Reverse Pathway (Host → Microbiome): score 0–4
How strongly does this paper demonstrate that host factors CAUSE changes in gut microbial community/metabolism?
- **0**: No reverse evidence whatsoever.
- **1**: Association inferred from host genetic-microbiome correlation studies, maternal nutritional programming, or germ-free comparisons.
- **2a**: In vitro porcine model validation demonstrating host epigenetic changes alter microbial community composition or function.
- **2b**: In vivo porcine model validation (HDAC inhibitor treatment, dietary methyl-donor manipulation) demonstrating host-to-microbiome causal effects.
- **3**: Causal validation in murine models (SIRT1, HDAC6, Tet2, or KDM5 knockout) confirming the reverse pathway.
- **4**: Causal validation in porcine models with independent replication by a separate laboratory.

### Dimension III — Coupling Verification Depth: score 0–4
Are forward AND reverse pathways measured in the SAME experimental system?
- **0**: Neither pathway verified for this node in any model system; or only one direction measured.
- **1**: Forward and reverse pathways independently verified, but NOT within the same experimental system.
- **2**: Forward and reverse effects simultaneously observed within the same experiment, but without interventional proof of causal connection.
- **3**: Intervention experiment demonstrates that blocking one pathway affects the other, establishing causal coupling.
- **4**: Cross-scale coupling verified: simultaneous multi-compartment, multi-timepoint measurement demonstrating that forward effects temporally precede reverse effects.

### Dimension IV — Measurement Depth: score 0–2 (THREE levels only)
- **0**: Single compartment measured at a single timepoint. No simultaneous measurement across compartments.
- **1**: Multiple compartments measured OR multiple timepoints sampled (but not both).
- **2**: Multiple compartments measured AND multiple timepoints sampled within a single experimental system.

**NOTE**: Dimension IV is scored 0–2, not 0–4. This reflects the current technological ceiling — no published study has achieved Level 2.

---

## Part D: Compartment Coverage
Which gut compartments does this paper's measurements cover? Select ALL that apply:
- `luminal` — if luminal/fecal metabolite concentrations were measured
- `epithelial` — if epithelial/cellular epigenetic or transcriptomic marks were measured
- `microenvironment` — if immune factors, pH, oxygen, or mucus were measured

---

## Part E: Node Relevance
Which co-metabolism nodes does this paper contribute to?

Metabolite nodes: Butyrate, Propionate, Acetate, Branched SCFAs, Bile Acids, Tryptophan Metabolites, Polyamines, Vitamin B12, Folate/B9, Riboflavin/B2, Biotin/B7, Vitamin A/Retinoic Acid, Vitamin D, B-Vitamins (B1/B3/B5/B6), Lactate, Succinate, GABA/Glutamate

Strain/Taxa nodes: Phascolarctobacterium, Lactobacillus, Bifidobacterium, Bacteroides, Clostridium, Prevotella, Akkermansia, Faecalibacterium

---

## Part F: Research Priority
Assign a research priority based on the feasibility-importance matrix:
- **P1**: High feasibility + High importance (e.g., butyrate at lumen-epithelium interface). Timeline: 2–3 yr.
- **P2**: Medium feasibility + High importance (e.g., bile acids, tryptophan metabolites).
- **P3**: Low feasibility + Medium importance (e.g., polyamines, vitamin B12).
- **N/A**: If the paper does not directly address a translational research gap.

---

## Output Format
Return ONLY a valid JSON object (no markdown fences, no other text):

{
  "entry_type": "probiotics or metabolites",
  "evidence_level": "L0 | L1a | L1b | L2a | L2b | L3 | L3.5 | L4",

  "forward_pathway": 0,
  "reverse_pathway": 0,
  "coupling_depth": 0,
  "measurement_depth": 0,

  "total_score": 0,

  "compartments_covered": ["luminal"],

  "evidence_justification": "one sentence explaining the assigned evidence level",
  "forward_justification": "one sentence on forward pathway evidence and what level it achieves",
  "reverse_justification": "one sentence on reverse pathway evidence — explicitly state if absent",

  "journal_quality": "high | medium | low",
  "model_system": "e.g., mouse mono-colonization, porcine dietary intervention, Caco-2 cells",
  "porcine_relevant": true,
  "key_limitation": "one sentence — specify which direction (forward/reverse) or compartment is the bottleneck",
  "research_priority": "P1 | P2 | P3 | N/A",

  "nodes": ["Node1", "Node2"],
  "summary": "3–4 sentence summary: (1) new forward-pathway data, (2) reverse-pathway evidence if any, (3) coupling status, (4) key evidence gap",

  "framework_alignment": "Which of the two source frameworks this paper primarily maps to: 'Tiered Evidence (MS2)' for probiotics, 'Compartmentalized Co-metabolism (MS1)' for metabolites, or 'Both'",

  "should_include": true
}

**IMPORTANT**:
- `forward_pathway`, `reverse_pathway`, `coupling_depth` are scored 0–4.
- `measurement_depth` is scored 0–2 (THREE levels).
- `total_score` = forward_pathway + reverse_pathway + coupling_depth + measurement_depth (max 14).
- `compartments_covered` MUST be an array of strings from: "luminal", "epithelial", "microenvironment".
- `research_priority` is required. Use N/A if not applicable.
- `framework_alignment` is required — cite which manuscript framework(s) this paper engages with.

CRITICAL: Set "should_include" to FALSE if:
- The paper only tangentially mentions a metabolite without studying its mechanistic role
- Non-gut system (skin, lung, ocean, soil, etc.)
- Pure clinical/epidemiological study with no mechanistic insight
- Host metabolism without any microbial component
- Does not contribute new data to the co-metabolism framework

Be selective. Only include papers that genuinely advance understanding of microbial metabolite-host interactions in the gut context.

---

## Paper to Evaluate
Title: {title}
Journal: {journal}
Source: {source}
Authors (first): {first_author}
Abstract: {abstract}
Query Node: {query_node}
"""


# ==================== 第二次独立评分 Prompt ====================
# AIHOT 思路：两次独立评分、取和过门槛。用不同提示词 + 更高温度（或换备用模型），
# 降低单次 LLM 打分幻觉/不一致。第一轮完整证据框架是"主评"（决定等级/理由/摘要），
# 第二轮换成更窄的"入选可信度复核"，只重新判四维 + 一个 include 结论 + 置信度。

SECOND_OPINION_PROMPT = """You are an independent, skeptical peer reviewer doing a SECOND, deliberately re-worded pass on a paper that has already been scored once. Do NOT assume the first score is correct — re-assess from scratch, narrowly focused on inclusion confidence.

Re-score the SAME four dimensions on the SAME scale as the primary review:
- forward_pathway (0-4): strength of microbe/metabolite -> host causal evidence.
- reverse_pathway (0-4): strength of host -> microbiome causal evidence.
- coupling_depth (0-4): whether forward AND reverse are verified in the SAME experimental system.
- measurement_depth (0-2): compartment x timepoint coverage.

Also give:
- confidence (0.0-1.0): how sure you are of this second score.
- include (true/false): your independent judgment on whether this paper genuinely advances mechanistic understanding of microbial metabolite-host interactions in the gut.
- one_line: one sentence of independent rationale.

Return ONLY valid JSON (no fences, no other text):
{
  "forward_pathway": 0, "reverse_pathway": 0, "coupling_depth": 0, "measurement_depth": 0,
  "total_score": 0, "confidence": 0.0, "include": true, "one_line": "..."
}

total_score = forward_pathway + reverse_pathway + coupling_depth + measurement_depth (max 14).

---

## Paper to re-review
Title: {title}
Journal: {journal}
Source: {source}
First author: {first_author}
Abstract: {abstract}
"""


# ==================== API 调用 ====================

def evaluate_with_deepseek(article: dict, retries: int = 3) -> Optional[dict]:
    """DeepSeek API 评估"""
    if not DEEPSEEK_API_KEY:
        return None

    from openai import OpenAI
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

    prompt = EVALUATION_PROMPT.format(
        title=article.get("title", ""),
        journal=article.get("journal", ""),
        source=article.get("source", ""),
        first_author=article.get("first_author", ""),
        abstract=article.get("abstract", ""),
        query_node=article.get("query_node", ""),
    )

    for attempt in range(retries + 1):
        try:
            response = client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[
                    {"role": "system", "content": "You are a rigorous academic reviewer. Output ONLY valid JSON, no other text."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=1200,
            )
            return _parse_json(response.choices[0].message.content.strip())
        except Exception as e:
            msg = str(e)
            if any(kw in msg.lower() for kw in ["rate_limit", "429", "503", "overloaded"]):
                time.sleep((attempt + 1) * 4)
            elif attempt < retries:
                time.sleep(2)
            else:
                print(f"    DeepSeek failed: {msg[:100]}")
                return None
    return None


def evaluate_with_glm(article: dict) -> Optional[dict]:
    """GLM 备用评估"""
    if not GLM_API_KEY:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=GLM_API_KEY, base_url=GLM_BASE_URL)

        prompt = EVALUATION_PROMPT.format(
            title=article.get("title", ""),
            journal=article.get("journal", ""),
            source=article.get("source", ""),
            first_author=article.get("first_author", ""),
            abstract=article.get("abstract", ""),
            query_node=article.get("query_node", ""),
        )

        response = client.chat.completions.create(
            model=GLM_MODEL,
            messages=[
                {"role": "system", "content": "You are a rigorous academic reviewer. Output ONLY valid JSON."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=1200,
        )
        return _parse_json(response.choices[0].message.content.strip())
    except Exception as e:
        print(f"    GLM failed: {str(e)[:100]}")
        return None


def evaluate_local(article: dict) -> dict:
    """Local fallback: heuristic bidirectional evaluation aligned with unified framework"""
    jn = article.get("journal", "").lower()
    source = article.get("source", "")
    abstract_len = len(article.get("abstract", ""))
    abstract_lower = article.get("abstract", "").lower()
    title_lower = article.get("title", "").lower()

    top_tier = any(n in jn for n in ["nature", "science", "cell", "lancet", "nejm", "pnas", "elife"])
    good_tier = any(n in jn for n in ["microbiome", "gut microbes", "isme", "mbio", "msystems",
                                       "gastroenterology", "gut", "hepatology", "plos biology",
                                       "embo", "genome biology", "current biology"])

    if top_tier:
        base_level = "L2b"; jq = "high"; fwd_base = 3
    elif good_tier:
        base_level = "L2a"; jq = "high"; fwd_base = 2
    elif source == "bioRxiv":
        base_level = "L1a"; jq = "unverified"; fwd_base = 1
    else:
        base_level = "L1b"; jq = "medium"; fwd_base = 1

    reverse_kw = ["host immune", "host genetics", "diet-induced", "diet induced",
                  "host-microbe feedback", "host-microbiome", "epithelial signaling",
                  "amp secretion", "antimicrobial peptide", "defensin", "reg3",
                  "bile acid synthesis", "fxr", "tgr5", "vdr", "vitamin d receptor",
                  "hdac inhibitor", "methyl-donor", "sirt1", "tet2", "kdm5"]
    has_rev = any(kw in abstract_lower or kw in title_lower for kw in reverse_kw)
    rev_base = max(0, fwd_base - 1) if has_rev else 0

    coup_base = max(0, min(fwd_base, rev_base) - 1) if fwd_base > 0 and rev_base > 0 else 0

    depth_kw = ["time-series", "time series", "longitudinal", "multi-compartment",
                "epithelium", "lamina propria", "portal blood", "multi-timepoint", "spatial"]
    depth_hints = sum(1 for kw in depth_kw if kw in abstract_lower or kw in title_lower)
    meas_base = min(2, depth_hints)

    comps = []
    if any(kw in abstract_lower or kw in title_lower for kw in
           ["luminal", "fecal", "faecal", "stool", "lumen", "cecal", "cecum"]):
        comps.append("luminal")
    if any(kw in abstract_lower or kw in title_lower for kw in
           ["epithelial", "epithelium", "colonocyte", "ipsec-j2", "caco-2",
            "organoid", "hdac", "histone", "acetylation", "methylation", "epigenetic"]):
        comps.append("epithelial")
    if any(kw in abstract_lower or kw in title_lower for kw in
           ["immune", "mucus", "defensin", "antimicrobial peptide",
            "iga", "cytokine", "chemokine", "tight junction", "barrier"]):
        comps.append("microenvironment")

    total = fwd_base + rev_base + coup_base + meas_base
    porc = any(kw in abstract_lower for kw in ["porcine", "pig ", "piglet", "swine"])

    return {
        "entry_type": "unknown",
        "evidence_level": base_level,
        "evidence_justification": f"Local estimation based on journal tier ({jq}). Full AI assessment pending.",
        "forward_pathway": fwd_base, "reverse_pathway": rev_base,
        "coupling_depth": coup_base, "measurement_depth": meas_base,
        "total_score": total,
        "compartments_covered": comps if comps else ["luminal"],
        "forward_justification": f"Local: forward pathway {fwd_base}/4.",
        "reverse_justification": f"Local: reverse pathway {rev_base}/4 {'(hints detected)' if has_rev else '(no evidence)'}.",
        "journal_quality": jq, "model_system": "unknown",
        "porcine_relevant": porc,
        "key_limitation": "Local fallback evaluation. Needs AI assessment for bidirectional and compartment-level scoring.",
        "research_priority": "N/A",
        "nodes": article.get("nodes", []),
        "summary": f"From {article.get('journal', 'unknown journal')}. Fwd: {fwd_base}/4, Rev: {rev_base}/4, Coupling: {coup_base}/4, Depth: {meas_base}/2.",
        "framework_alignment": "Pending AI assessment",
        "should_include": True,
        "eval_method": "local",
    }


# ==================== 第二次独立评分：API 调用 ====================

def _second_opinion_call(client, model: str, prompt: str, temperature: float, retries: int = 2) -> Optional[dict]:
    """对某个 OpenAI 兼容 client 发起第二轮独立评分调用。"""
    for attempt in range(retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You are an independent skeptical reviewer. Output ONLY valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                temperature=temperature,
                max_tokens=600,
            )
            return _parse_json(response.choices[0].message.content.strip())
        except Exception as e:
            msg = str(e)
            if any(kw in msg.lower() for kw in ["rate_limit", "429", "503", "overloaded"]):
                time.sleep((attempt + 1) * 3)
            elif attempt < retries:
                time.sleep(1)
            else:
                print(f"    Second-opinion call failed: {msg[:100]}")
                return None
    return None


def evaluate_second_deepseek(article: dict) -> Optional[dict]:
    """第二轮独立评分（DeepSeek：换 prompt + 更高温度 0.7）。"""
    if not DEEPSEEK_API_KEY:
        return None
    from openai import OpenAI
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    prompt = SECOND_OPINION_PROMPT.format(
        title=article.get("title", ""),
        journal=article.get("journal", ""),
        source=article.get("source", ""),
        first_author=article.get("first_author", ""),
        abstract=article.get("abstract", ""),
    )
    return _second_opinion_call(client, DEEPSEEK_MODEL, prompt, temperature=0.7)


def evaluate_second_glm(article: dict) -> Optional[dict]:
    """第二轮独立评分（GLM 备用，与主评可能用不同模型，天然独立性）。"""
    if not GLM_API_KEY:
        return None
    from openai import OpenAI
    client = OpenAI(api_key=GLM_API_KEY, base_url=GLM_BASE_URL)
    prompt = SECOND_OPINION_PROMPT.format(
        title=article.get("title", ""),
        journal=article.get("journal", ""),
        source=article.get("source", ""),
        first_author=article.get("first_author", ""),
        abstract=article.get("abstract", ""),
    )
    return _second_opinion_call(client, GLM_MODEL, prompt, temperature=0.7)


def evaluate_second_local(article: dict) -> dict:
    """第二轮本地降级：更严苛的"怀疑审稿人"启发式，与 evaluate_local 使用不同的信号源。

    主评（evaluate_local）用期刊档位定基，这里改用显式的「因果语言 vs 关联语言」证据强度，
    反向命中要求更保守（≥2 才给分），从而给出真正独立的第二意见。
    """
    abstract = article.get("abstract", "") or ""
    abstract_lower = abstract.lower()
    title_lower = (article.get("title", "") or "").lower()
    text = abstract_lower + " " + title_lower

    causal_kw = ["mono-colonization", "monocolonization", "knockout", "gene deletion",
                 "intervention", "causal", "causality", "mechanism", "mechanistic",
                 "colonized with", "gnotobiotic", "germ-free"]
    assoc_kw = ["association", "associated", "correlation", "correlat", "cohort",
                "observational", "cross-sectional", "predicted", "inferred", "linked to"]
    has_causal = any(k in text for k in causal_kw)
    has_assoc = any(k in text for k in assoc_kw)

    if has_causal and not has_assoc:
        fwd2 = 3
    elif has_causal:
        fwd2 = 2
    elif has_assoc:
        fwd2 = 1
    else:
        fwd2 = 0

    rev_kw = ["host immune", "host genetics", "diet-induced", "host-microbe", "epithelial signaling",
              "amp secretion", "antimicrobial peptide", "defensin", "reg3",
              "bile acid synthesis", "fxr", "tgr5", "vdr", "vitamin d receptor",
              "hdac inhibitor", "methyl-donor", "sirt1", "tet2", "kdm5", "germ-free"]
    rev_hits = sum(1 for k in rev_kw if k in text)
    rev2 = min(3, rev_hits) if rev_hits >= 2 else (1 if rev_hits == 1 else 0)

    coup2 = 1 if (fwd2 >= 2 and rev2 >= 1) else 0

    depth2 = 1 if any(k in text for k in ["time-series", "longitudinal", "multi-compartment", "multi-timepoint"]) else 0

    total2 = fwd2 + rev2 + coup2 + depth2

    alen = len(abstract)
    confidence = min(0.7, 0.3 + alen / 2000.0)

    return {
        "forward_pathway": fwd2, "reverse_pathway": rev2,
        "coupling_depth": coup2, "measurement_depth": depth2,
        "total_score": total2, "confidence": round(confidence, 2),
        "include": total2 >= 3,
        "one_line": f"Independent local re-review: fwd {fwd2}/4, rev {rev2}/4, coup {coup2}/4, depth {depth2}/2 (confidence {confidence:.2f}).",
        "eval_method": "local",
    }


def combine_scores(primary: dict, second: Optional[dict]) -> dict:
    """AIHOT 思路：两次独立评分取和过门槛，把第二意见与合成分挂回评估结果。

    仅当第二意见可用时才施加门槛；第二意见缺失（API 全部不可用）时跳过，避免误伤。
    """
    if not second:
        primary["scoring_gate"] = {"status": "second_opinion_unavailable"}
        return primary
    p_total = int(primary.get("total_score") or 0)
    s_total = int(second.get("total_score") or 0)
    combined = p_total + s_total
    passed = combined >= SECOND_OPINION_MIN_SUM
    primary["second_opinion"] = second
    primary["combined_score"] = combined
    primary["scoring_gate"] = {
        "min_sum": SECOND_OPINION_MIN_SUM,
        "primary_total": p_total,
        "second_total": s_total,
        "combined": combined,
        "passed": bool(passed),
    }
    if not passed:
        primary["should_include"] = False
    return primary


# ==================== 工具函数 ====================

def _parse_json(text: str) -> Optional[dict]:
    import re
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for pattern in [r"```(?:json)?\s*([\s\S]*?)```", r"\{[\s\S]*\}"]:
        m = re.search(pattern, text)
        if m:
            try:
                return json.loads(m.group(1) if "```" in pattern else m.group(0))
            except json.JSONDecodeError:
                pass
    print(f"    JSON parse failed: {text[:150]}")
    return None


def load_articles(filepath: str) -> list:
    if not os.path.exists(filepath):
        print(f"  File not found: {filepath}")
        return []
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def save_scored(articles: list, filepath: str):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)
    print(f"  Saved: {len(articles)} scored articles")


# ==================== 主入口 ====================

def main():
    print("=" * 60)
    print("  Co-Metabolism Evidence Evaluator")
    print(f"  Framework: 4-Level Evidence + 4-D Matrix")
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  DeepSeek: {'configured' if DEEPSEEK_API_KEY else 'MISSING'}")
    print(f"  GLM fallback: {'configured' if GLM_API_KEY else 'not set'}")
    print("=" * 60)

    articles = load_articles(RAW_ARTICLES_FILE)
    if not articles:
        print("  No articles to evaluate.")
        return 0

    print(f"\n  Loaded: {len(articles)} articles")

    to_evaluate = articles[:MAX_ARTICLES_PER_RUN]
    if len(articles) > MAX_ARTICLES_PER_RUN:
        print(f"  Limiting to first {MAX_ARTICLES_PER_RUN}")

    scored = []
    ds_count, glm_count, local_count = 0, 0, 0
    second_ds, second_glm, second_local, gated_out = 0, 0, 0, 0

    for i, article in enumerate(to_evaluate, 1):
        title_short = article.get("title", "")[:70]
        print(f"  [{i}/{len(to_evaluate)}] {title_short}")

        scores = None
        if DEEPSEEK_API_KEY:
            scores = evaluate_with_deepseek(article)
            if scores:
                ds_count += 1
                scores["eval_method"] = "deepseek"
                scores["eval_model"] = DEEPSEEK_MODEL

        if not scores and GLM_API_KEY:
            scores = evaluate_with_glm(article)
            if scores:
                glm_count += 1
                scores["eval_method"] = "glm"
                scores["eval_model"] = GLM_MODEL

        if not scores:
            scores = evaluate_local(article)
            local_count += 1

        # 第二次独立评分（AIHOT：换 prompt/温度/模型），取两次之和过门槛
        second = None
        if DEEPSEEK_API_KEY:
            second = evaluate_second_deepseek(article)
            if second:
                second_ds += 1
        if not second and GLM_API_KEY:
            second = evaluate_second_glm(article)
            if second:
                second_glm += 1
        if not second:
            second = evaluate_second_local(article)
            second_local += 1

        gate_before = bool(scores.get("should_include", True))
        scores = combine_scores(scores, second)
        if gate_before and not scores.get("should_include", True):
            gated_out += 1

        scored.append({**article, "evaluation": scores})
        if i < len(to_evaluate):
            time.sleep(0.3)

    # 排除 should_include=false 的文章
    scored = [a for a in scored if a.get("evaluation", {}).get("should_include", True)]

    # 排序: 证据等级优先 + 总分降序
    level_order = {"L4": 9, "L3.5": 8, "L3": 7, "L2b": 6, "L2a": 5, "L1b": 4, "L1a": 3, "L1": 2, "L0": 1}
    # Fallback: treat old L1/L2/L3/L4 codes generously
    scored.sort(key=lambda a: (
        -level_order.get(a.get("evaluation", {}).get("evidence_level", "L1"), 2),
        -a.get("evaluation", {}).get("total_score", 0),
    ))

    print(f"\n  === Evaluation Summary ===")
    print(f"  Primary     : DeepSeek {ds_count} | GLM {glm_count} | Local {local_count}")
    print(f"  2nd opinion : DeepSeek {second_ds} | GLM {second_glm} | Local {second_local}")
    print(f"  Gated out (sum-of-two below threshold): {gated_out}")

    level_counts = {}
    for a in scored:
        lv = a.get("evaluation", {}).get("evidence_level", "L1a")
        level_counts[lv] = level_counts.get(lv, 0) + 1
    print(f"  Evidence levels: {dict(sorted(level_counts.items()))}")

    save_scored(scored, SCORED_ARTICLES_FILE)

    with open(os.path.join(DATA_DIR, "eval_stats.txt"), "w") as f:
        f.write(
            f"total={len(scored)},deepseek={ds_count},glm={glm_count},local={local_count},"
            f"second_deepseek={second_ds},second_glm={second_glm},second_local={second_local},"
            f"gated_out={gated_out}"
        )

    return len(scored)


if __name__ == "__main__":
    sys.exit(0 if main() > 0 else 1)
