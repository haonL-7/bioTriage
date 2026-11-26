#!/usr/bin/env python3
"""对比 Biomni 与 bioTriage 的分级结果。

先手工（或脚本）把 Biomni 的输出整理成 biomni_out.json：
    [{"id": "59d004ef34e0", "significance": "High", "summary": "...", "evidence": "..."}]

然后：
    python compare.py
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
TIERS = ["Low", "Medium", "High"]


def load(name, default=None):
    path = os.path.join(HERE, name)
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def norm(s):
    s = str(s or "").strip().capitalize()
    return s if s in TIERS else "Unknown"


def kappa(pairs):
    """Cohen's kappa（有序三分类，当作名义变量计算）。"""
    if not pairs:
        return None
    labels = TIERS + ["Unknown"]
    n = len(pairs)
    agree = sum(1 for a, b in pairs if a == b) / n
    pa = {l: sum(1 for a, _ in pairs if a == l) / n for l in labels}
    pb = {l: sum(1 for _, b in pairs if b == l) / n for l in labels}
    expected = sum(pa[l] * pb[l] for l in labels)
    return None if expected >= 1 else (agree - expected) / (1 - expected)


def main():
    ours = load("papers.json", [])
    theirs = load("biomni_out.json")
    if not theirs:
        print("缺少 biomni_out.json：请先把 Biomni 的输出按 README 的格式整理进去")
        return

    by_id = {r["id"]: r for r in ours}
    pairs, rows = [], []
    for item in theirs:
        rec = by_id.get(item.get("id"))
        if not rec:
            continue
        a = norm(rec["bioTriage"]["significance"])
        b = norm(item.get("significance"))
        pairs.append((a, b))
        rows.append((rec["title"], a, b, item.get("evidence", "")))

    exact = sum(1 for a, b in pairs if a == b)
    near = sum(1 for a, b in pairs if abs(TIERS.index(a) - TIERS.index(b)) <= 1
               if a in TIERS and b in TIERS)
    k = kappa(pairs)

    lines = ["# Biomni vs bioTriage 分级对比", "",
             "| 指标 | 值 |", "| --- | --- |",
             "| 可比样本 | %d |" % len(pairs),
             "| 完全一致 | %d（%.0f%%） |" % (exact, 100.0 * exact / len(pairs) if pairs else 0),
             "| 相差一档以内 | %d（%.0f%%） |" % (near, 100.0 * near / len(pairs) if pairs else 0),
             "| Cohen's kappa | %s |" % ("%.2f" % k if k is not None else "n/a"),
             "", "## 逐条", "",
             "| 标题 | bioTriage | Biomni | Biomni 依据 |", "| --- | --- | --- | --- |"]
    for title, a, b, ev in rows:
        lines.append("| %s | %s | %s | %s |" % (
            (title or "")[:70].replace("|", "/"), a, b, (ev or "")[:60].replace("|", "/")))

    out = os.path.join(HERE, "report.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("已生成 report.md（%d 条样本）" % len(pairs))


if __name__ == "__main__":
    main()
