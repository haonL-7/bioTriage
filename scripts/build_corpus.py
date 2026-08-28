"""
build_corpus.py — 生成 BioTriage 基础 RAG 语料 documents.json

来源：BioTriage 星级收藏（Starred Collection）7 篇奠基论文：
      PubMed abstract + 站点星级评述（role）。
铁律：全部为已核验公开文献；不含任何未发表手稿/毕设内容（defer）。
      role 字段直接复用站点已公开的星级评述文本，不与未发表方案内部细节耦合。

用法：
    python scripts/build_corpus.py            # 输出到 bioTriage/documents.json
    python scripts/build_corpus.py --out X    # 自定义输出路径
"""
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
import requests

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)  # bioTriage/
DEFAULT_OUT = os.path.join(ROOT, "documents.json")

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

# ---- 星级收藏 7 篇（对应 biotriage.html Starred Collection，2026-08 复核）----
# role = 站点已公开的星级评述（starN-sum），用于检索时标注论文在项目中的定位。
STARS = [
    {
        "id": "star-1-lan2025",
        "cite": ("Lan Q, Liufu S, et al. Gut-resident Phascolarctobacterium succinatutens "
                 "decreases fat accumulation via MYC-driven epigenetic regulation of arginine "
                 "biosynthesis. npj Biofilms and Microbiomes, 2025;11:150."),
        "pmid": "40753182",
        "doi": "10.1038/s41522-025-00792-w",
        "year": 2025,
        "journal": "npj Biofilms and Microbiomes",
        "role": ("Establishes the succinate-utilizer → propionate → reduced backfat axis in "
                 "living pigs, the direct precedent for guild-level phenotyping in livestock."),
    },
    {
        "id": "star-2-gardiner2004",
        "cite": ("Gardiner GE, Casey PG, et al. Relative ability of orally administered "
                 "Lactobacillus murinus to predominate and persist in the porcine "
                 "gastrointestinal tract. Applied and Environmental Microbiology, 2004."),
        "pmid": "15066778",
        "doi": "",
        "year": 2004,
        "journal": "Applied and Environmental Microbiology",
        "role": ("Classic evidence for transient (peak-then-decline) colonization of probiotic "
                 "lactobacilli in the pig gut; a key design reference for sampling timepoints."),
    },
    {
        "id": "star-3-suo2012",
        "cite": ("Suo C, et al. Effects of Lactobacillus plantarum ZJ316 on pig growth and "
                 "pork quality. BMC Veterinary Research, 2012."),
        "pmid": "22731747",
        "doi": "",
        "year": 2012,
        "journal": "BMC Veterinary Research",
        "role": ("150 weaned piglets, 60 days, 1×10⁹ CFU/d; dose–response benchmark for "
                 "L. plantarum feeding trials."),
    },
    {
        "id": "star-4-zhang2019",
        "cite": ("Zhang D, Liu H, et al. Fecal microbiota and its correlation with fatty acids "
                 "and free amino acids metabolism in piglets after a Lactobacillus strain oral "
                 "administration. Frontiers in Microbiology, 2019."),
        "pmid": "31040835",
        "doi": "",
        "year": 2019,
        "journal": "Frontiers in Microbiology",
        "role": ("L. reuteri ZLR003 in piglets (V3–V4 16S); a worked example of "
                 "microbiota–SCFA–serum-metabolite correlation analysis."),
    },
    {
        "id": "star-5-yu2024",
        "cite": ("Yu J, et al. Dietary supplementation with Lactiplantibacillus plantarum P-8 "
                 "improves the growth performance and gut microbiome of weaned piglets. "
                 "Microbiology Spectrum, 2024."),
        "pmid": "38169289",
        "doi": "",
        "year": 2024,
        "journal": "Microbiology Spectrum",
        "role": ("Recent commercial-strain trial data; current growth-performance and microbiome "
                 "benchmarks for L. plantarum in piglets."),
    },
    {
        "id": "star-6-kim2021",
        "cite": ("Kim D, Min Y, et al. Multi-Probiotic Lactobacillus Supplementation Improves "
                 "Liver Function and Reduces Cholesterol Levels in Jeju Native Pigs. "
                 "Animals, 2021;11:2309."),
        "pmid": "34438766",
        "doi": "10.3390/ani11082309",
        "year": 2021,
        "journal": "Animals",
        "role": ("Three months of multi-probiotic Lactobacillus feeding in local-breed pigs: "
                 "ALP, GGT and BUN decreased, ALT unchanged, tissue sections normal — the "
                 "safety endorsement for Lactobacillus in local pig breeds."),
    },
    {
        "id": "star-7-hou2011",
        "cite": ("Hou CL, Ji HF, Zhou YX. Effects of Lactobacillus plantarum preparations on "
                 "growth performance and biochemical indices in weaned piglets. (in Chinese). "
                 "饲料研究 (Feed Research), 2011(12): 14–16."),
        "pmid": "",
        "doi": "",
        "year": 2011,
        "journal": "饲料研究 (Feed Research)",
        "role": ("Pig-source L. plantarum in 72 weaned piglets: growth, gut microbiota and serum "
                 "biochemistry — domestic safety evidence complementing Kim 2021."),
    },
]


def fetch_abstract(pmid: str, timeout: int = 20) -> str:
    """从 PubMed efetch 拉取摘要纯文本。失败返回空串。"""
    if not pmid:
        return ""
    try:
        r = requests.get(
            EUTILS,
            params={
                "db": "pubmed",
                "id": pmid,
                "retmode": "xml",
                "rettype": "abstract",
            },
            headers={"User-Agent": "BioTriageCorpus/1.0"},
            timeout=timeout,
        )
        r.raise_for_status()
        root = ET.fromstring(r.content)
        texts = []
        for at in root.iter("AbstractText"):
            texts.append("".join(at.itertext()).strip())
        return "\n".join(t for t in texts if t)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] PMID {pmid} 摘要获取失败: {e}")
        return ""


def build() -> list:
    docs = []
    for s in STARS:
        abstract = fetch_abstract(s["pmid"])
        passage = abstract
        if s["role"]:
            passage = (passage + "\n\n" if passage else "") + f"[Project role] {s['role']}"
        docs.append({
            "id": s["id"],
            "source": "starred",
            "cite": s["cite"],
            "title": s["cite"].split(".")[0].strip(),
            "pmid": s["pmid"],
            "doi": s["doi"],
            "year": s["year"],
            "journal": s["journal"],
            "role": s["role"],
            "has_abstract": bool(abstract),
            "passage": passage,
            "category": "starred_foundation",
        })
        print(f"[ok] {s['id']}  abstract_len={len(abstract)}")
        time.sleep(0.35)  # NCBI 限速：每请求间隔
    return docs


def main() -> int:
    import time  # noqa: PLC0415
    out = DEFAULT_OUT
    if "--out" in sys.argv:
        i = sys.argv.index("--out")
        out = sys.argv[i + 1]

    docs = build()
    payload = {
        "version": "2026-08-18",
        "note": ("基础语料：BioTriage 星级收藏 7 篇奠基论文（PubMed abstract + 星级评述）。"
                 "全部为已核验公开文献；不含未发表手稿/毕设内容（defer）。"),
        "documents": docs,
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\n已生成 {out}: {len(docs)} 篇文档")
    return 0


if __name__ == "__main__":
    sys.exit(main())
