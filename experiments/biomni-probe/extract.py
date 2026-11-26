#!/usr/bin/env python3
"""从已发布的频道数据里抽取待对比论文，生成 papers.json / papers.md。

用法：
    python extract.py            # 默认每频道取 1 篇、共 6 篇
    python extract.py --per 2    # 每频道 2 篇
"""
import argparse
import glob
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SITE = os.path.join(ROOT, "ai-news", "_site")


def load_channels():
    out = []
    for path in sorted(glob.glob(os.path.join(SITE, "*", "data", "papers.json"))):
        slug = os.path.basename(os.path.dirname(os.path.dirname(path)))
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        papers = data.get("papers") or data.get("items") or []
        if papers:
            out.append((slug, papers))
    return out


def pick(papers, n):
    """按 significance 分层抽样，保证三个等级都有样本。"""
    buckets = {}
    for p in papers:
        buckets.setdefault(p.get("significance") or "Unknown", []).append(p)
    chosen, i = [], 0
    while len(chosen) < n and any(len(v) > 0 for v in buckets.values()):
        for key in ("High", "Medium", "Low"):
            bucket = buckets.get(key) or []
            if bucket:
                chosen.append(bucket.pop(0))
                if len(chosen) >= n:
                    break
        i += 1
        if i > 50:
            break
    return chosen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per", type=int, default=1, help="每个频道抽取篇数")
    args = ap.parse_args()

    rows = []
    for slug, papers in load_channels():
        for p in pick(papers, args.per):
            rows.append({
                "channel": slug,
                "id": p.get("id"),
                "title": p.get("title"),
                "journal": p.get("journal"),
                "pub_date": p.get("pub_date"),
                "doi": p.get("doi"),
                "url": p.get("url"),
                "abstract": p.get("abstract"),
                "bioTriage": {
                    "summary": p.get("summary"),
                    "significance": p.get("significance"),
                    "tags": p.get("tags") or [],
                },
            })

    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "papers.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

    md = ["# Biomni 对比样本", ""]
    for i, r in enumerate(rows, 1):
        md += [
            "## %d. [%s] %s" % (i, r["bioTriage"]["significance"] or "-", r["title"]),
            "",
            "- 频道：%s" % r["channel"],
            "- 期刊：%s ｜ %s" % (r["journal"], r["pub_date"]),
            "- DOI：%s" % (r["doi"] or "-"),
            "- bioTriage 分级：**%s**" % (r["bioTriage"]["significance"] or "-"),
            "- bioTriage 摘要：%s" % (r["bioTriage"]["summary"] or "-"),
            "",
        ]
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "papers.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    print("已抽取 %d 篇 → papers.json / papers.md" % len(rows))


if __name__ == "__main__":
    main()
