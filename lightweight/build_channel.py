#!/usr/bin/env python3
"""
Build a lightweight channel page: crawl -> curate -> generate static HTML.
Each channel is a self-contained directory under _site with its own index.html.
"""
import json, os, sys, time, shutil
from datetime import datetime
import sys as _sys
_script_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_script_dir)
_sys.path.insert(0, _project_root)

from lightweight.crawler import crawl_channel, CHANNEL_QUERIES
from lightweight.curator import curate_batch

DATA_DIR = os.path.join(_project_root, "ai-news", "data")
BUILD_DIR = os.path.join(_project_root, "ai-news", "_site")

CHANNEL_META = {
    "computational-genomics": {
        "title": "Computational Genomics & Epigenomics",
        "desc": "AI-curated selection from open-access journals: variant calling, genome assembly, epigenomic profiling, and genotype-phenotype integration.",
        "category": "Bioinformatics & Computational Biology",
    },
    "metagenomics-informatics": {
        "title": "Metagenomics & Microbiome Informatics",
        "desc": "Computational methods for metagenomic assembly, binning, taxonomic profiling, and functional annotation of microbial communities.",
        "category": "Bioinformatics & Computational Biology",
    },
    "structural-bioinformatics": {
        "title": "Structural Bioinformatics & Protein Science",
        "desc": "AI-curated selection: protein structure prediction, molecular dynamics, docking, and structure-based function annotation.",
        "category": "Bioinformatics & Computational Biology",
    },
    "systems-biology": {
        "title": "Systems Biology & Network Analysis",
        "desc": "Gene regulatory network inference, metabolic network modeling (FBA), and multi-omics integration frameworks.",
        "category": "Bioinformatics & Computational Biology",
    },
    "ml-biology": {
        "title": "Machine Learning for Biological Data",
        "desc": "Deep learning, protein language models, GNNs, and foundation models applied to biological sequence and structure.",
        "category": "Bioinformatics & Computational Biology",
    },
    "single-cell-omics": {
        "title": "Single-Cell & Spatial Omics",
        "desc": "Computational methods for scRNA-seq, ATAC-seq, trajectory inference, cell-cell communication, and spatial transcriptomics.",
        "category": "Bioinformatics & Computational Biology",
    },
    "databases-knowledge-graphs": {
        "title": "Biological Databases & Knowledge Graphs",
        "desc": "New database releases, knowledge graph construction, ontology development, and FAIR data infrastructure.",
        "category": "Bioinformatics & Computational Biology",
    },
    "ai-drug-discovery": {
        "title": "AI-Driven Drug Discovery",
        "desc": "Virtual screening, molecular generation, ADMET prediction, and computational drug repurposing from open-access literature.",
        "category": "Bioinformatics & Computational Biology",
    },
}

LIVE_CHANNELS = list(CHANNEL_META.keys())

def build(channel_key: str, max_papers: int = 60, delay: float = 0.5):
    """Full build pipeline for one channel."""
    meta = CHANNEL_META.get(channel_key, {})
    print(f"Building channel: {meta.get('title', channel_key)}")

    # Step 1: Crawl
    print("[1/3] Crawling...")
    papers = crawl_channel(channel_key)
    papers = papers[:max_papers]

    # Step 2: Curate
    print(f"[2/3] Curating {len(papers)} papers...")
    curated = curate_batch(papers, delay=delay)

    # Step 3: Generate HTML
    print("[3/3] Generating page...")
    html = generate_channel_html(channel_key, meta, curated)

    # Output
    channel_dir = os.path.join(BUILD_DIR, channel_key)
    os.makedirs(channel_dir, exist_ok=True)
    index_path = os.path.join(channel_dir, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html)

    # Save data
    os.makedirs(os.path.join(channel_dir, "data"), exist_ok=True)
    with open(os.path.join(channel_dir, "data", "papers.json"), "w", encoding="utf-8") as f:
        json.dump({
            "channel": channel_key,
            "generated_at": datetime.now().isoformat(),
            "count": len(curated),
            "papers": curated,
        }, f, ensure_ascii=False, indent=2)

    print(f"  Done: {channel_dir}/index.html ({len(curated)} papers)")
    return curated

def generate_channel_html(channel_key: str, meta: dict, papers: list) -> str:
    """Generate a clean, academic channel page."""
    title = meta.get("title", channel_key)
    desc = meta.get("desc", "")
    category = meta.get("category", "")

    sig_counts = {"High": 0, "Medium": 0, "Low": 0}
    for p in papers:
        s = p.get("significance", "Medium")
        sig_counts[s] = sig_counts.get(s, 0) + 1

    cards = ""
    for p in papers:
        sig = p.get("significance", "Medium")
        sig_class = sig.lower()
        tags_html = "".join(f'<span class="tag">{t}</span>' for t in p.get("tags", [])[:3])
        cards += f'''<article class="paper-item">
            <div class="paper-meta">
                <span class="journal">{p.get("journal", "")}</span>
                <span class="date">{p.get("pub_date", "")}</span>
                <span class="sig sig-{sig_class}">{sig}</span>
            </div>
            <h3 class="paper-title"><a href="{p.get("url", "#")}" target="_blank" rel="noopener">{p.get("title", "")}</a></h3>
            <p class="paper-summary">{p.get("summary", "")}</p>
            <div class="paper-tags">{tags_html}</div>
        </article>'''

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} — BioTriage</title>
<style>
:root {{ --bg: #fff; --text: #1a1a1a; --text-secondary: #4a4a4a; --text-muted: #8c8c8c; --border: #d5d5d5; --accent: #1a5276; --max-width: 800px; --font-serif: 'Georgia','Times New Roman',serif; --font-sans: -apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif; }}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: var(--font-sans); background: var(--bg); color: var(--text); line-height: 1.6; font-size: 15px; }}
.top-bar {{ background: #1a1a1a; color: #ccc; font-size: 12px; padding: 8px 20px; display: flex; justify-content: space-between; }}
.top-bar a {{ color: #fff; text-decoration: none; }}
.container {{ max-width: var(--max-width); margin: 0 auto; padding: 32px 20px 60px; }}
h1 {{ font-family: var(--font-serif); font-size: 24px; font-weight: 400; margin-bottom: 4px; }}
.channel-desc {{ font-size: 14px; color: var(--text-secondary); margin-bottom: 8px; }}
.channel-stats {{ font-size: 12px; color: var(--text-muted); margin-bottom: 28px; }}
.paper-item {{ padding: 20px 0; border-bottom: 1px solid var(--border); }}
.paper-item:first-of-type {{ border-top: 1px solid var(--border); }}
.paper-meta {{ display: flex; gap: 12px; align-items: center; margin-bottom: 6px; font-size: 12px; }}
.paper-meta .journal {{ color: var(--text-muted); font-style: italic; }}
.paper-meta .date {{ color: var(--text-muted); }}
.paper-meta .sig {{ font-size: 10px; font-weight: 600; padding: 2px 8px; text-transform: uppercase; letter-spacing: 0.5px; }}
.sig-high {{ background: #e8f5e9; color: #1a6b3c; }}
.sig-medium {{ background: #f5f5f5; color: #4a4a4a; }}
.sig-low {{ background: #fff5f5; color: #a0a0a0; }}
.paper-title {{ font-family: var(--font-serif); font-size: 15px; font-weight: 400; margin-bottom: 4px; line-height: 1.5; }}
.paper-title a {{ color: var(--text); text-decoration: none; }}
.paper-title a:hover {{ color: var(--accent); text-decoration: underline; }}
.paper-summary {{ font-size: 13px; color: var(--text-secondary); line-height: 1.7; margin-bottom: 6px; }}
.paper-tags {{ display: flex; gap: 6px; flex-wrap: wrap; }}
.tag {{ font-size: 10px; color: var(--accent); background: #eaf0f6; padding: 2px 8px; letter-spacing: 0.3px; }}
.back-link {{ font-size: 12px; color: var(--accent); text-decoration: none; }}
.site-footer {{ max-width: var(--max-width); margin: 0 auto; padding: 24px 20px; border-top: 1px solid var(--border); color: var(--text-muted); font-size: 11px; text-align: center; }}
.site-footer a {{ color: var(--accent); }}
@media (max-width: 640px) {{ h1 {{ font-size: 20px; }} }}
</style>
</head>
<body>
<nav class="top-bar"><a href="../">BioTriage</a><span>Lightweight Curation &mdash; AI selection &amp; significance rating</span></nav>
<div class="container">
<a href="../" class="back-link">&larr; BioTriage</a>
<h1>{title}</h1>
<p class="channel-desc">{desc}</p>
<p class="channel-stats">{len(papers)} papers curated &middot; High: {sig_counts["High"]} &middot; Medium: {sig_counts["Medium"]} &middot; Low: {sig_counts["Low"]} &middot; Updated: {datetime.now().strftime("%Y-%m-%d")}</p>
{cards}
</div>
<footer class="site-footer">
<p>AI-curated by BioTriage. Summaries and significance ratings are AI-generated; verify before citing. &middot; <a href="../">BioTriage Home</a></p>
</footer>
</body>
</html>'''

if __name__ == "__main__":
    ch = _sys.argv[1] if len(_sys.argv) > 1 else "computational-genomics"
    build(ch)
