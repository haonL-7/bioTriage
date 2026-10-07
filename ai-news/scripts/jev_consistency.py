#!/usr/bin/env python3
"""
Jev 一致性对比脚本（§5.1 —— 只做小样本对比 + 评估报告，不替换 ai_evaluator）

目的：把 ai_evaluator 已经落库的评分结果，与 TypeSafe Jev（System One 判别模型）的
Score / Choice / Noul 原语输出做小样本一致性 / 准确率对比，产出一份评估报告，
供你决定是否在评分步把 LLM 打分换成 Jev（先双跑、后替换）。

设计约束（严格）：
- 不替换 ai_evaluator：本脚本是独立的一次性离线对比工具，只读 scored_articles.json，不回写。
- 不安装任何 SDK / 不安装 Raven：直接用 requests 调 https://api.typesafe.ai/v1/systemone。
- 无 TYPESAFE_API_KEY 时进入 dry-run：用 ai_evaluator.evaluate_second_local 作占位"第二来源"，
  使指标计算代码路径可完整跑通；报告会显式标注"Jev 未调用，以下为占位本地基线"。

输出（写入 ai-news/data/）：
  jev_consistency.json       逐条对比明细 + 一致性指标
  jev_consistency_report.md  人读评估报告

用法：
  python jev_consistency.py [--sample 10] [--seed 42] [--out-dir data]
"""

import argparse
import json
import os
import sys

# 让本脚本可以独立运行（同目录 import ai_evaluator）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests  # noqa: E402

import ai_evaluator  # noqa: E402  （仅复用 evaluate_second_local 作 dry-run 基线，不触碰其评分逻辑）

# ==================== 配置 ====================

JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = os.environ.get("JEV_MODEL", "jev-latest")
TYPESAFE_API_KEY = os.environ.get("TYPESAFE_API_KEY", "")
# 直连计费需要此请求头；设为空串可关闭（例如走网关时）
TYPESAFE_BILLING = os.environ.get("TYPESAFE_BILLING", "enabled")

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
SCORED_ARTICLES_FILE = os.path.join(DATA_DIR, "scored_articles.json")

# 证据等级 Choice 的选项（与 ai_evaluator 的六层框架对齐）
EVIDENCE_LEVEL_OPTIONS = {
    "L0": "No relevant mechanistic evidence for the functional claim in any model system",
    "L1a": "Mechanistic: in vitro pure-culture / ex vivo, or indirect from same-genus heterologous species",
    "L1b": "Association inference: genus-level co-occurrence or abundance-phenotype correlation",
    "L2a": "Effector molecule validated in target host WITHOUT strain-specific attribution",
    "L2b": "Direct in vivo target-host validation, strain-targeted, partially validated mechanism",
    "L3": "Causal mono-colonization in murine models with human-derived strains",
    "L3.5": "Mono-colonization with human-derived strains in a porcine model",
    "L4": "Mono-colonization in target host with target-host-derived strain + independent replication",
}

# 四维矩阵 Score 的等级描述（低→高）
MATRIX_LEVELS_4 = [
    "no evidence / pure association",
    "association inferred from correlation or co-occurrence",
    "in vitro model validation",
    "in vivo model validation",
    "causal validation with independent replication",
]
MATRIX_LEVELS_2 = [
    "single compartment, single timepoint",
    "multiple compartments OR multiple timepoints (not both)",
    "multiple compartments AND multiple timepoints",
]


# ==================== 归一化：把 ai_evaluator 落库分统一到 0-4 / 0-2 标尺 ====================

def _clamp(v, lo, hi):
    try:
        return max(lo, min(hi, int(round(float(v)))))
    except (TypeError, ValueError):
        return lo


def normalize_ai_scores(article: dict) -> dict:
    """从 scored_articles.json 的一篇里抽出可比字段；兼容新旧字段名与 0-5/0-4 标尺。"""
    ev = article.get("evaluation") or article.get("scores") or {}
    fwd = ev.get("forward_pathway", ev.get("effectiveness", 0))
    rev = ev.get("reverse_pathway", ev.get("safety", 0))
    coup = ev.get("coupling_depth", ev.get("coupling", 0))
    depth = ev.get("measurement_depth", 0)
    return {
        "evidence_level": ev.get("evidence_level"),
        "forward": _clamp(fwd, 0, 4),
        "reverse": _clamp(rev, 0, 4),
        "coupling": _clamp(coup, 0, 4),
        "depth": _clamp(depth, 0, 2),
        "total_score": _clamp(ev.get("total_score", 0), 0, 14),
        "include": bool(ev.get("should_include", True)),
        "confidence": None,
    }


# ==================== Jev 调用（真实模式） ====================

def build_jev_payload(article: dict) -> dict:
    """构造 /v1/systemone 请求体：state + 类型化 questions（Choice/Score/Noul）。

    字段名以 TypeSafe Jev 公开契约为准（POST /v1/systemone，Bearer 认证）；
    如上游字段有变动，call_jev 会抛错并被上层捕获 → 回退 dry-run，不影响报告生成。
    """
    state = {
        "title": article.get("title", ""),
        "journal": article.get("journal", ""),
        "source": article.get("source", ""),
        "abstract": (article.get("abstract") or "")[:2000],
    }
    questions = {
        "evidence_level": {
            "type": "choice",
            "question": "Which evidence tier best describes this paper's mechanistic evidence?",
            "criteria": EVIDENCE_LEVEL_OPTIONS,
        },
        "forward": {
            "type": "score",
            "question": "How strongly does this paper demonstrate microbe/metabolite -> host causation (forward pathway)?",
            "criteria": MATRIX_LEVELS_4,
        },
        "reverse": {
            "type": "score",
            "question": "How strongly does this paper demonstrate host -> microbiome causation (reverse pathway)?",
            "criteria": MATRIX_LEVELS_4,
        },
        "coupling": {
            "type": "score",
            "question": "Are forward AND reverse verified in the same experimental system?",
            "criteria": MATRIX_LEVELS_4,
        },
        "depth": {
            "type": "score",
            "question": "What compartment x timepoint coverage does this paper achieve?",
            "criteria": MATRIX_LEVELS_2,
        },
        "include": {
            "type": "noul",
            "question": "This paper genuinely advances mechanistic understanding of microbial metabolite-host interactions in the gut.",
        },
    }
    return {"state": state, "model": JEV_MODEL, "questions": questions}


def call_jev(payload: dict, api_key: str) -> dict:
    """POST /v1/systemone，返回原始 JSON。

    直连计费需带 X-Typesafe-Billing 请求头（TYPESAFE_BILLING 设为空可关闭）。
    4xx/5xx 抛带响应体的错误，便于看清「余额不足 / 未启用计费」等原因。
    """
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if TYPESAFE_BILLING:
        headers["X-Typesafe-Billing"] = TYPESAFE_BILLING
    resp = requests.post(JEV_ENDPOINT, headers=headers, json=payload, timeout=60)
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {(resp.text or '')[:200]}")
    return resp.json()


def _get_answer(response: dict, qid: str) -> dict:
    """防御式地从 Jev 响应里取某个 question 的 answer，兼容几种可能的包装键。"""
    for wrapper in ("answers", "questions", "results"):
        node = response.get(wrapper)
        if isinstance(node, dict) and qid in node:
            return node[qid]
    # 兜底：响应本身就可能是一个 {qid: answer} 的映射
    if isinstance(response, dict) and qid in response:
        return response[qid]
    return {}


def _score_to_int(answer: dict) -> int:
    """Score 原语返回 probability-weighted mean（可能是小数 0-4），四舍五入到 0-4。"""
    s = answer.get("score")
    try:
        return int(round(float(s)))
    except (TypeError, ValueError):
        return 0


def jev_evaluate(article: dict, api_key: str) -> dict:
    """真实 Jev 评估：Choice(证据等级) + Score(四维) + Noul(入选)，归一化到可比字段。"""
    response = call_jev(build_jev_payload(article), api_key)

    lv_answer = _get_answer(response, "evidence_level")
    fwd = _score_to_int(_get_answer(response, "forward"))
    rev = _score_to_int(_get_answer(response, "reverse"))
    coup = _score_to_int(_get_answer(response, "coupling"))
    depth = _score_to_int(_get_answer(response, "depth"))
    include_answer = _get_answer(response, "include")

    confidences = []
    for qid in ("evidence_level", "forward", "reverse", "coupling", "depth"):
        c = _get_answer(response, qid).get("confidence")
        if isinstance(c, (int, float)):
            confidences.append(float(c))

    noul = include_answer.get("noul")
    include = (float(noul) >= 0.5) if isinstance(noul, (int, float)) else True

    return {
        "evidence_level": lv_answer.get("choice"),
        "forward": _clamp(fwd, 0, 4),
        "reverse": _clamp(rev, 0, 4),
        "coupling": _clamp(coup, 0, 4),
        "depth": _clamp(depth, 0, 2),
        "total_score": _clamp(fwd + rev + coup + depth, 0, 14),
        "include": include,
        "confidence": round(sum(confidences) / len(confidences), 3) if confidences else None,
    }


def local_baseline(article: dict) -> dict:
    """dry-run 占位：复用 ai_evaluator 的独立第二意见（换信号源的怀疑审稿人启发式）。"""
    s = ai_evaluator.evaluate_second_local(article)
    return {
        "evidence_level": None,  # 本地基线不产出证据等级 Choice
        "forward": _clamp(s.get("forward_pathway", 0), 0, 4),
        "reverse": _clamp(s.get("reverse_pathway", 0), 0, 4),
        "coupling": _clamp(s.get("coupling_depth", 0), 0, 4),
        "depth": _clamp(s.get("measurement_depth", 0), 0, 2),
        "total_score": _clamp(s.get("total_score", 0), 0, 14),
        "include": bool(s.get("include", True)),
        "confidence": s.get("confidence"),
    }


# ==================== 一致性指标 ====================

def _mae(a, b):
    if not a:
        return None
    return round(sum(abs(x - y) for x, y in zip(a, b)) / len(a), 3)


def _pearson(x, y):
    n = len(x)
    if n < 2:
        return None
    mx, my = sum(x) / n, sum(y) / n
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    dx = sum((xi - mx) ** 2 for xi in x) ** 0.5
    dy = sum((yi - my) ** 2 for yi in y) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return round(num / (dx * dy), 3)


def cohens_kappa(a, b):
    n = len(a)
    if n == 0:
        return 0.0
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pt, qt = sum(a) / n, sum(b) / n
    pe = pt * qt + (1 - pt) * (1 - qt)
    if abs(pe - 1.0) < 1e-9:
        return 1.0
    return round((po - pe) / (1 - pe), 3)


def compute_metrics(ai_rows, jev_rows):
    """ai_rows / jev_rows 为两组同长度、同字段的归一化 dict 列表。"""
    dims = ["forward", "reverse", "coupling", "depth"]
    metrics = {"n": len(ai_rows)}
    for d in dims:
        a = [r[d] for r in ai_rows]
        b = [r[d] for r in jev_rows]
        metrics[d] = {"mae": _mae(a, b), "r": _pearson(a, b)}
    metrics["total_score"] = {
        "mae": _mae([r["total_score"] for r in ai_rows], [r["total_score"] for r in jev_rows]),
        "r": _pearson([r["total_score"] for r in ai_rows], [r["total_score"] for r in jev_rows]),
    }
    ai_inc = [r["include"] for r in ai_rows]
    jev_inc = [r["include"] for r in jev_rows]
    metrics["include"] = {
        "agreement": round(sum(1 for x, y in zip(ai_inc, jev_inc) if x == y) / len(ai_inc), 3) if ai_inc else None,
        "cohens_kappa": cohens_kappa(ai_inc, jev_inc),
    }
    # 证据等级只在双方都有时才比（dry-run 里 jev 侧为 None）
    lv_pairs = [(r["evidence_level"], j["evidence_level"]) for r, j in zip(ai_rows, jev_rows)
                if r["evidence_level"] and j["evidence_level"]]
    metrics["evidence_level"] = {
        "compared": len(lv_pairs),
        "agreement": round(sum(1 for a, b in lv_pairs if a == b) / len(lv_pairs), 3) if lv_pairs else None,
    }
    confs = [r["confidence"] for r in jev_rows if r.get("confidence") is not None]
    metrics["confidence"] = {
        "n": len(confs),
        "mean": round(sum(confs) / len(confs), 3) if confs else None,
    }
    return metrics


# ==================== 报告 ====================

def render_report(metrics, method, sample_n, out_dir):
    lines = []
    lines.append("# Jev 一致性对比评估报告（小样本，不替换 ai_evaluator）\n")
    lines.append(f"- 对比方法：{method}")
    lines.append(f"- 样本量：{sample_n}")
    lines.append(f"- 对比对象：ai_evaluator 已落库评分 vs Jev Score/Choice/Noul 输出\n")
    lines.append("> 说明：本报告仅用于「是否值得把 LLM 打分换成 Jev」的决策，不改动 ai_evaluator。\n")

    lines.append("## 四维矩阵 + 总分（MAE 越低越好，r 越接近 1 越一致）\n")
    lines.append("| 维度 | MAE | Pearson r |")
    lines.append("|---|---|---|")
    for d in ["forward", "reverse", "coupling", "depth"]:
        m = metrics[d]
        lines.append(f"| {d} | {m['mae']} | {m['r']} |")
    lines.append(f"| **total_score** | {metrics['total_score']['mae']} | {metrics['total_score']['r']} |\n")

    lines.append("## 入选判定（should_include）\n")
    inc = metrics["include"]
    lines.append(f"- 一致率：{inc['agreement']}")
    lines.append(f"- Cohen's kappa：{inc['cohens_kappa']}\n")

    lines.append("## 证据等级（Choice，仅双方都产出时可比）\n")
    lv = metrics["evidence_level"]
    lines.append(f"- 可比条数：{lv['compared']}")
    lines.append(f"- 一致率：{lv['agreement']}\n")

    lines.append("## Jev 置信度\n")
    c = metrics["confidence"]
    lines.append(f"- 有效条数：{c['n']}，均值：{c['mean']}\n")

    lines.append("## 建议的决策规则（待你拍板）\n")
    lines.append("- 若 total_score 的 r ≥ 0.8 且 include 的 kappa ≥ 0.6：可考虑在评分步接入 Jev Score/Choice，保留原 LLM 作回退（双跑）。")
    lines.append("- 若达不到上述门槛：维持现状（ai_evaluator 的六层框架 + 四维矩阵）。")
    lines.append("- 无论结果如何，先小样本双跑、看漂移，再决定是否全面替换。\n")

    return "\n".join(lines)


# ==================== 主入口 ====================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=10)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out-dir", default=DATA_DIR)
    args = ap.parse_args()

    if not os.path.exists(SCORED_ARTICLES_FILE):
        print(f"  scored_articles.json 不存在：{SCORED_ARTICLES_FILE}")
        return 1

    scored = json.load(open(SCORED_ARTICLES_FILE, encoding="utf-8"))
    # 确定性小样本
    scored_sorted = sorted(scored, key=lambda a: a.get("id", ""))
    sample = scored_sorted[: args.sample]

    use_jev = bool(TYPESAFE_API_KEY)
    method = "Jev (POST /v1/systemone)" if use_jev else "dry-run 占位本地基线（Jev 未调用，无 TYPESAFE_API_KEY）"

    ai_rows, jev_rows, details = [], [], []
    jev_ok = 0
    for art in sample:
        ai_row = normalize_ai_scores(art)
        if use_jev:
            try:
                jev_row = jev_evaluate(art, TYPESAFE_API_KEY)
                jev_ok += 1
            except Exception as e:
                print(f"    Jev 调用失败（回退本地基线）：{str(e)[:160]}")
                jev_row = local_baseline(art)
        else:
            jev_row = local_baseline(art)
        ai_rows.append(ai_row)
        jev_rows.append(jev_row)
        details.append({
            "id": art.get("id", ""),
            "title": art.get("title", "")[:80],
            "ai_evaluator": ai_row,
            "jev": jev_row,
        })

    if use_jev and jev_ok == 0:
        method += "（⚠ 全部调用失败，已回退本地基线；请检查 key 余额/计费）"
        print("\n  ⚠ Jev 全部调用失败，已回退本地基线。请检查 key 余额或是否启用计费。\n")
    elif use_jev:
        print(f"    Jev 成功调用 {jev_ok}/{len(sample)} 条")

    metrics = compute_metrics(ai_rows, jev_rows)

    os.makedirs(args.out_dir, exist_ok=True)
    out_json = {"method": method, "sample_n": len(sample), "metrics": metrics, "details": details}
    with open(os.path.join(args.out_dir, "jev_consistency.json"), "w", encoding="utf-8") as f:
        json.dump(out_json, f, ensure_ascii=False, indent=2)
    with open(os.path.join(args.out_dir, "jev_consistency_report.md"), "w", encoding="utf-8") as f:
        f.write(render_report(metrics, method, len(sample), args.out_dir))

    print("=" * 60)
    print("  Jev 一致性对比（小样本）")
    print(f"  Method : {method}")
    print(f"  Sample : {len(sample)}")
    print(f"  total_score MAE={metrics['total_score']['mae']}  r={metrics['total_score']['r']}")
    print(f"  include  kappa={metrics['include']['cohens_kappa']}  agreement={metrics['include']['agreement']}")
    print(f"  输出    : {os.path.join(args.out_dir, 'jev_consistency_report.md')}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
