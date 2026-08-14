#!/usr/bin/env python3
"""
构建脚本：编排爬虫 → AI 评估 → 生成前端数据
在 GitHub Actions 中按顺序执行，产出 gh-pages 部署所需文件
"""

import json
import os
import sys
import shutil
from datetime import datetime

# ==================== 配置 ====================

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
BUILD_DIR = os.path.join(PROJECT_ROOT, "_site")

RAW_ARTICLES_FILE = os.path.join(DATA_DIR, "raw_articles.json")
SCORED_ARTICLES_FILE = os.path.join(DATA_DIR, "scored_articles.json")
NEWS_JSON_FILE = os.path.join(DATA_DIR, "news.json")

# 父级分组（与 crawler.py 保持一致）
NODE_GROUPS = {
    "SCFAs": ["Butyrate", "Propionate", "Acetate", "Branched SCFAs"],
    "Vitamin B Family": ["Vitamin B12", "Folate/B9", "Riboflavin/B2", "Biotin/B7",
                          "B-Vitamins (B1/B3/B5/B6)"],
    "Fat-Soluble Vitamins": ["Vitamin A/Retinoic Acid", "Vitamin D"],
    "Gut Strains": ["Phascolarctobacterium", "Lactobacillus", "Bifidobacterium",
                    "Bacteroides", "Clostridium", "Prevotella",
                    "Akkermansia", "Faecalibacterium"],
}


def add_parent_nodes(nodes: list[str]) -> list[str]:
    """给定细粒度节点列表，自动补入父级分组标签"""
    expanded = list(nodes)
    for parent, children in NODE_GROUPS.items():
        if any(child in nodes for child in children) and parent not in expanded:
            expanded.append(parent)
    return expanded
KNOWLEDGE_BASE_FILE = os.path.join(DATA_DIR, "knowledge_base.json")


def is_duplicate(new_title: str, existing: list[dict]) -> bool:
    """Check if a new paper duplicates an existing entry (by title similarity)"""
    new_key = "".join(c.lower() for c in new_title if c.isalnum())[:80]
    for entry in existing:
        exist_key = "".join(c.lower() for c in entry.get("title", "") if c.isalnum())[:80]
        if new_key == exist_key:
            return True
        # Also check for high substring overlap
        if len(new_key) > 40 and len(exist_key) > 40:
            shorter = min(new_key, exist_key, key=len)
            longer = max(new_key, exist_key, key=len)
            if shorter in longer:
                return True
    return False


# ==================== 构建逻辑 ====================

def load_json(filepath: str) -> dict | list:
    """Safely load a JSON file"""
    if not os.path.exists(filepath):
        print(f"  WARNING: file not found: {filepath}")
        return {}
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def build_news_data(scored_articles: list[dict]) -> list[dict]:
    """
    Flatten nested evaluation structure -> frontend-friendly flat fields
    Exclude papers marked should_include=false by the evaluator
    Supports BOTH old field names (effectiveness/safety/coupling/measurement_depth 0-5)
    and new unified field names (forward_pathway/reverse_pathway/coupling_depth/measurement_depth with 0-4/0-2).
    """
    papers = []
    for art in scored_articles:
        ev = art.get("evaluation", art.get("scores", {}))
        if ev.get("should_include") is False:
            continue
        nodes = ev.get("nodes", art.get("nodes", []))
        if not nodes or nodes == ["Unclassified"]:
            continue

        # Resolve field names: new unified names take precedence, fall back to old names
        fwd = ev.get("forward_pathway", ev.get("effectiveness", 0))
        rev = ev.get("reverse_pathway", ev.get("safety", 0))
        coup = ev.get("coupling_depth", ev.get("coupling", 0))
        depth = ev.get("measurement_depth", 0)  # new: 0-2; old: 0-5

        papers.append({
            "id": art.get("id", ""),
            "title": art.get("title", ""),
            "url": art.get("url", ""),
            "abstract": art.get("abstract", "")[:500],
            "journal": art.get("journal", art.get("source", "")),
            "doi": art.get("doi", ""),
            "pmid": art.get("pmid", ""),
            "firstAuthor": art.get("first_author", ""),
            "pubDate": art.get("pub_date", ""),
            "links": art.get("links", []),
            "source": art.get("source", ""),
            # Evidence framework — unified names
            "evidenceLevel": ev.get("evidence_level", "L1a"),
            "evidenceJustification": ev.get("evidence_justification", ""),
            "forwardPathway": fwd,
            "reversePathway": rev,
            "couplingDepth": coup,
            "measurementDepth": depth,
            "totalScore": ev.get("total_score", 0),
            # Legacy compatibility aliases
            "effectiveness": fwd,
            "safety": rev,
            "coupling": coup,
            # Justifications
            "forwardJustification": ev.get("forward_justification", ""),
            "reverseJustification": ev.get("reverse_justification", ""),
            # Compartment tracking (new)
            "compartmentsCovered": ev.get("compartments_covered", []),
            # Framework alignment (new)
            "frameworkAlignment": ev.get("framework_alignment", ""),
            # Priority (new, from evaluator)
            "researchPriority": ev.get("research_priority", "N/A"),
            # Other
            "journalQuality": ev.get("journal_quality", "unknown"),
            "modelSystem": ev.get("model_system", ""),
            "porcineRelevant": ev.get("porcine_relevant", False),
            "keyLimitation": ev.get("key_limitation", ""),
            "nodes": ev.get("nodes", art.get("nodes", [])),
            "summary": ev.get("summary", ""),
            "evalMethod": ev.get("eval_method", "local"),
            "evalModel": ev.get("eval_model", ""),
        })

    level_order = {"L4": 9, "L3.5": 8, "L3": 7, "L2b": 6, "L2a": 5, "L1b": 4, "L1a": 3, "L1": 2, "L0": 1}
    papers.sort(key=lambda p: (
        -level_order.get(p["evidenceLevel"], 1),
        -p["totalScore"],
    ))
    return papers


def build_stats(papers: list[dict], eval_stats: dict) -> dict:
    """Generate stats from paper list, using unified field names"""
    now = datetime.now()
    node_counter = {}
    journal_counter = {}
    level_counter = {}
    total_fwd = total_rev = total_coup = total_dep = 0

    for p in papers:
        for node in p.get("nodes", []):
            node_counter[node] = node_counter.get(node, 0) + 1
        jn = p.get("journal", "Unknown")
        journal_counter[jn] = journal_counter.get(jn, 0) + 1
        lv = p.get("evidenceLevel", "L1a")
        level_counter[lv] = level_counter.get(lv, 0) + 1
        total_fwd += p.get("forwardPathway", p.get("effectiveness", 0))
        total_rev += p.get("reversePathway", p.get("safety", 0))
        total_coup += p.get("couplingDepth", p.get("coupling", 0))
        total_dep += p.get("measurementDepth", 0)

    n = max(len(papers), 1)
    return {
        "updated_at": now.isoformat(),
        "updated_at_human": now.strftime("%Y-%m-%d %H:%M"),
        "total_papers": len(papers),
        "avg_forward_pathway": round(total_fwd / n, 1),
        "avg_reverse_pathway": round(total_rev / n, 1),
        "avg_coupling_depth": round(total_coup / n, 1),
        "avg_measurement_depth": round(total_dep / n, 1),
        # Legacy keys
        "avg_effectiveness": round(total_fwd / n, 1),
        "avg_safety": round(total_rev / n, 1),
        "avg_coupling": round(total_coup / n, 1),
        "avg_forward": round(total_fwd / n, 1),
        "avg_reverse": round(total_rev / n, 1),
        "evidence_levels": level_counter,
        "node_distribution": node_counter,
        "journal_distribution": journal_counter,
        "eval_methods": eval_stats,
    }


def create_build_dir():
    """创建构建输出目录。BioTriage 首页在根目录，Evidence Monitor 在 /evidence/ 子目录。"""
    if os.path.exists(BUILD_DIR):
        shutil.rmtree(BUILD_DIR)
    os.makedirs(BUILD_DIR, exist_ok=True)
    # Evidence monitor goes to /evidence/
    evidence_dir = os.path.join(BUILD_DIR, "evidence")
    os.makedirs(evidence_dir, exist_ok=True)
    return evidence_dir


def copy_frontend_files(evidence_dir: str):
    """复制前端文件到 /evidence/ 构建目录"""
    src_path = SRC_DIR

    for filename in os.listdir(src_path):
        src_file = os.path.join(src_path, filename)
        dst_file = os.path.join(evidence_dir, filename)
        if os.path.isfile(src_file):
            shutil.copy2(src_file, dst_file)

    print(f"  Copied frontend files to {evidence_dir}")


def write_news_json(news_list: list[dict], stats: dict, evidence_dir: str):
    """将新闻数据和统计写入 /evidence/ 构建目录"""
    payload = {
        "stats": stats,
        "papers": news_list,
    }

    # 写入 /evidence/data/news.json（供 gh-pages 部署）
    build_data_dir = os.path.join(evidence_dir, "data")
    os.makedirs(build_data_dir, exist_ok=True)
    news_json_path = os.path.join(build_data_dir, "news.json")
    with open(news_json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # 同时写入 data 目录（备用）
    with open(NEWS_JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    file_size = os.path.getsize(news_json_path)
    print(f"  Generated /evidence/data/news.json ({file_size / 1024:.1f} KB)")

    # 写入统计文件
    stats_path = os.path.join(DATA_DIR, "stats.json")
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)


def generate_static_html(papers: list[dict], stats: dict):
    """Pre-render paper cards into static HTML using unified framework fields."""
    level_order = {"L4": 9, "L3.5": 8, "L3": 7, "L2b": 6, "L2a": 5, "L1b": 4, "L1a": 3, "L1": 2, "L0": 1}

    cards_html = ""
    for paper in papers:
        lv = paper.get("evidenceLevel", "L1a")
        is_kb = (paper.get("type") or paper.get("source")) == "knowledge_base"
        kb_class = ' kb-entry' if is_kb else ''

        # Badges
        badges = ""
        rp = paper.get("researchPriority", "")
        if rp and rp != "N/A":
            badges += f'<span class="priority-badge priority-{rp.lower()}">{rp}</span>'
        if is_kb:
            badges += '<span class="kb-badge">Curated</span>'

        # Evidence levels line
        ev_parts = []
        if paper.get("porcineEvidenceLevel"):
            ev_parts.append(f'Porcine: {paper["porcineEvidenceLevel"]}')
        if paper.get("murineEvidenceLevel"):
            ev_parts.append(f'Murine: {paper["murineEvidenceLevel"]}')
        ev_line = " | ".join(ev_parts) if ev_parts else paper.get("modelSystem", "")

        # Nodes
        nodes_html = "".join(f'<span class="node-tag">{n}</span>' for n in paper.get("nodes", []))

        # Compartment tags (new)
        comps = paper.get("compartmentsCovered", [])
        comp_html = ""
        if comps:
            comp_icons = {"luminal": "Lumen", "epithelial": "Epi", "microenvironment": "MicroEnv"}
            comp_tags = "".join(
                f'<span class="compartment-tag comp-{c}">{comp_icons.get(c, c)}</span>'
                for c in comps
            )
            comp_html = f'<div class="paper-compartments">{comp_tags}</div>'

        # Matrix bars — unified dimensions with correct scales
        fwd = paper.get("forwardPathway", paper.get("effectiveness", 0))
        rev = paper.get("reversePathway", paper.get("safety", 0))
        coup = paper.get("couplingDepth", paper.get("coupling", 0))
        depth = paper.get("measurementDepth", 0)

        dims = [
            ("forwardPathway", "Forward (Microbe→Host)", fwd, 4, "/4"),
            ("reversePathway", "Reverse (Host→Microbiome)", rev, 4, "/4"),
            ("couplingDepth", "Bidirectional Coupling", coup, 4, "/4"),
            ("measurementDepth", "Measurement Depth", depth, 2, "/2"),
        ]
        matrix_html = ""
        for dim_id, label, val, scale, suffix in dims:
            pct = min(val / max(scale, 1) * 100, 100)
            matrix_html += f'''<div class="matrix-item" data-dim="{dim_id}">
                <span class="matrix-label">{label}</span>
                <span class="matrix-bar"><span class="matrix-fill" style="width:{pct}%"></span></span>
                <span class="matrix-val">{val}{suffix}</span>
            </div>'''

        # Title: clicking opens evaluation detail modal
        paper_id = paper.get("id", "")
        title_text = paper.get("title", "")
        title_html = f'<a href="#" class="paper-title-link" data-paper-id="{paper_id}" onclick="return false">{title_text}</a>'

        # Multi-backup links (only render if there are valid URLs)
        all_links = paper.get("links", [])
        if not all_links:
            url = paper.get("url", "")
            if url:
                all_links = [{"type": "primary", "label": "Source", "url": url}]
        valid_links = [l for l in all_links if l.get("url", "").strip()]
        links_html = ''
        if valid_links:
            links_html = '<div class="paper-links">'
            for link in valid_links:
                link_url = link.get("url", "")
                link_label = link.get("label", "Link")
                link_type = link.get("type", "")
                links_html += f'<a href="{link_url}" target="_blank" rel="noopener noreferrer" class="paper-link paper-link-{link_type}">{link_label}</a> '
            links_html += f'<button class="report-broken-btn" data-paper-id="{paper_id}" title="Report broken link">Report</button>'
            links_html += '</div>'

        cards_html += f'''<article class="paper-card{kb_class}" data-paper-id="{paper_id}">
            <div class="paper-header">
                <div class="paper-source">
                    <span class="paper-journal">{paper.get("journal", "")}</span>
                    <span class="paper-badges">{badges}</span>
                </div>
                <div class="paper-right">
                    <span class="paper-date">{paper.get("pubDate", "")}</span>
                    <span class="level-badge level-{lv.lower()}">{lv}</span>
                </div>
            </div>
            <h2 class="paper-title">{title_html}</h2>
            <p class="paper-evidence-levels">{ev_line}</p>
            <p class="paper-abstract">{paper.get("abstract", "")[:600]}</p>
            {links_html}
            <div class="paper-nodes">{nodes_html}</div>
            {comp_html}
            <div class="paper-matrix">{matrix_html}</div>
            <div class="paper-summary">{paper.get("summary", "")}</div>
            <div class="paper-limitation">{'Limitation: ' + paper["keyLimitation"] if paper.get("keyLimitation") else ''}</div>
            <div class="paper-framework">{'Framework: ' + paper["frameworkAlignment"] if paper.get("frameworkAlignment") and paper["frameworkAlignment"] != "Pending AI assessment" else ''}</div>
        </article>'''

    # Read template and inject cards
    src_index = os.path.join(SRC_DIR, "index.html")
    with open(src_index, "r", encoding="utf-8") as f:
        template = f.read()

    # Embed paper data as inline JSON so modal works before JS loads news.json
    papers_json = json.dumps(papers, ensure_ascii=False)
    papers_script = f'<script>window.__PAPERS__ = {papers_json};</script>'

    feed_html = f'''<div id="feed">
        {cards_html}
    </div>
    {papers_script}'''

    result = template.replace("<!-- FEED_PLACEHOLDER -->", feed_html)
    return result


def create_nojekyll():
    """创建 .nojekyll 文件（告诉 GitHub Pages 不要用 Jekyll 处理）"""
    nojekyll_path = os.path.join(BUILD_DIR, ".nojekyll")
    with open(nojekyll_path, "w") as f:
        f.write("")
    print("  Created .nojekyll")


def print_summary(news_list: list[dict], stats: dict):
    """Print build summary"""
    print("\n" + "=" * 60)
    print("  Build Summary")
    print("=" * 60)
    print(f"   Papers: {stats['total_papers']}")
    print(f"   Avg Forward (Microbe->Host): {stats['avg_forward']}")
    print(f"   Avg Reverse (Host->Microbiome): {stats['avg_reverse']}")
    print(f"   Avg Coupling: {stats['avg_coupling']}")
    print(f"   Avg Meas. Depth: {stats['avg_measurement_depth']}")
    print(f"   Evidence levels: {stats['evidence_levels']}")
    print(f"   Updated: {stats['updated_at_human']}")
    print(f"\n   Top nodes: {dict(sorted(stats['node_distribution'].items(), key=lambda x: -x[1])[:8])}")
    print("=" * 60)


# ==================== 主入口 ====================

def main():
    print("=" * 60)
    print("  Co-Metabolism Evidence Monitor - Build")
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Step 1: Always load the curated knowledge base
    print("\n[1/4] Loading curated knowledge base...")
    kb_entries = load_json(KNOWLEDGE_BASE_FILE)
    if not isinstance(kb_entries, list):
        kb_entries = []
    print(f"  Knowledge base: {len(kb_entries)} curated entries")

    # Step 2: Load daily crawled + AI-evaluated papers
    print("\n[2/4] Loading daily crawled papers...")
    scored = load_json(SCORED_ARTICLES_FILE)
    if not isinstance(scored, list):
        print("  No scored articles, checking raw...")
        scored = load_json(RAW_ARTICLES_FILE)
        if not isinstance(scored, list):
            scored = []

    # Convert crawled papers to frontend format
    new_papers = build_news_data(scored)

    # Deduplicate against knowledge base
    fresh_papers = []
    dup_count = 0
    for paper in new_papers:
        if is_duplicate(paper.get("title", ""), kb_entries):
            dup_count += 1
        else:
            fresh_papers.append(paper)
    print(f"  Crawled: {len(new_papers)} -> {len(fresh_papers)} new (removed {dup_count} duplicates)")

    # Step 3: Merge KB + new papers, apply parent node grouping
    print("\n[3/4] Building static site...")
    # Add parent group nodes to KB entries
    for entry in kb_entries:
        entry["nodes"] = add_parent_nodes(entry.get("nodes", []))
    # Add parent group nodes to crawled papers
    for paper in fresh_papers:
        paper["nodes"] = add_parent_nodes(paper.get("nodes", []))
    all_papers = kb_entries + fresh_papers
    print(f"  Total entries: {len(all_papers)} ({len(kb_entries)} curated + {len(fresh_papers)} new)")

    # Load eval stats
    eval_stats = {}
    stats_file = os.path.join(DATA_DIR, "eval_stats.txt")
    if os.path.exists(stats_file):
        with open(stats_file, "r", encoding="utf-8") as f:
            for pair in f.read().strip().split(","):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    try:
                        eval_stats[k] = int(v)
                    except ValueError:
                        eval_stats[k] = v
    if not eval_stats:
        eval_stats = {"deepseek": 0, "glm": 0, "local": 0, "total": 0}

    stats = build_stats(all_papers, eval_stats)
    evidence_dir = create_build_dir()

    # Copy CSS and JS to /evidence/
    for filename in os.listdir(SRC_DIR):
        src_file = os.path.join(SRC_DIR, filename)
        dst_file = os.path.join(evidence_dir, filename)
        if os.path.isfile(src_file) and not filename.endswith('.html'):
            shutil.copy2(src_file, dst_file)

    # Generate pre-rendered HTML for evidence monitor → /evidence/index.html
    static_html = generate_static_html(all_papers, stats)
    evidence_index_path = os.path.join(evidence_dir, "index.html")
    with open(evidence_index_path, "w", encoding="utf-8") as f:
        f.write(static_html)
    print(f"  Generated /evidence/index.html with {len(all_papers)} pre-rendered papers")

    write_news_json(all_papers, stats, evidence_dir)
    create_nojekyll()

    # Deploy BioTriage main page → /index.html
    biotriage_src = os.path.join(PROJECT_ROOT, "..", "biotriage.html")
    if not os.path.exists(biotriage_src):
        biotriage_src = os.path.join(PROJECT_ROOT, "biotriage.html")
    if os.path.exists(biotriage_src):
        shutil.copy2(biotriage_src, os.path.join(BUILD_DIR, "index.html"))
        print(f"  Deployed BioTriage homepage -> /index.html")
    else:
        print(f"  WARNING: biotriage.html not found at {biotriage_src}")

    # Deploy manuscript sub-pages
    for ms_dir in ["ms1", "ms2"]:
        ms_src = os.path.join(PROJECT_ROOT, "..", ms_dir)
        if not os.path.exists(ms_src):
            ms_src = os.path.join(PROJECT_ROOT, ms_dir)
        if os.path.exists(ms_src):
            ms_dst = os.path.join(BUILD_DIR, ms_dir)
            if os.path.exists(ms_dst):
                shutil.rmtree(ms_dst)
            shutil.copytree(ms_src, ms_dst)
            print(f"  Deployed /{ms_dir}/ manuscript page")

    # Deploy lightweight curation channels (already built by lightweight/build_channel.py)
    lightweight_dir = os.path.join(PROJECT_ROOT, "..", "lightweight")
    if not os.path.exists(lightweight_dir):
        lightweight_dir = os.path.join(PROJECT_ROOT, "lightweight")
    for ch_key in ["computational-genomics"]:
        ch_src = os.path.join(BUILD_DIR, ch_key)
        if os.path.exists(ch_src):
            print(f"  Lightweight channel /{ch_key}/ ready ({' '.join(os.listdir(ch_src))})")
        else:
            print(f"  NOTE: /{ch_key}/ not built yet. Run: python lightweight/build_channel.py {ch_key}")

    # Step 4: Summary
    print("\n[4/4] Build complete!")
    print_summary(all_papers, stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
