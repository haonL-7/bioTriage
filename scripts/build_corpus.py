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


# ---- 2026-09 会话新核验（松辽黑/宏基因组/方法学/八眉），全部 PubMed 一手核验 ----
VERIFIED = [
    {"id": "ver-kumar2025", "cite": "Kumar ST, et al. Breed-specific differences of gut microbiota and metabolomic insights into fat deposition and meat quality in Chinese Songliao Black Pig and Large White x Landrace Pig Breeds. BMC Microbiology, 2025;25:334.", "pmid": "40426050", "doi": "10.1186/s12866-025-04051-y", "year": 2025, "journal": "BMC Microbiology", "role": "Songliao Black vs Large White x Landrace, ileum/cecum/rectum 16S + PICRUSt2-predicted functions + metabolome; n=5/group; the 16S-only precedent that a shotgun functional layer would complement."},
    {"id": "ver-ma2024", "cite": "Ma L, et al. Clostridium butyricum and carbohydrate active enzymes contribute to the reduced fat deposition in pigs. iMeta, 2024;3(1):e160.", "pmid": "38868506", "doi": "10.1002/imt2.160", "year": 2024, "journal": "iMeta", "role": "Jinhua pigs; CAZyme (GH13) + butyrate (buk/ptb) linked to reduced fat deposition; mouse and pig validation."},
    {"id": "ver-zha2024", "cite": "Zha A, et al. Gut Bifidobacterium pseudocatenulatum protects against fat deposition by enhancing secondary bile acid biosynthesis. iMeta, 2024;3(6):e261.", "pmid": "39742294", "doi": "10.1002/imt2.261", "year": 2024, "journal": "iMeta", "role": "Secondary bile acid pathway linking a gut microbe to reduced fat deposition (Yin Y co-corresponding)."},
    {"id": "ver-bai2025", "cite": "Bai X, et al. Gut Metagenome Reveals the Microbiome Signatures in Tibetan and Black Pigs. Animals, 2025;15(5):753.", "pmid": "40076036", "doi": "10.3390/ani15050753", "year": 2025, "journal": "Animals", "role": "Shotgun metagenome of local Chinese pig breeds; cross-breed comparison template."},
    {"id": "ver-nearing2022", "cite": "Nearing JT, et al. Microbiome differential abundance methods produce different results across 38 datasets. Nature Communications, 2022;13:342.", "pmid": "35039521", "doi": "10.1038/s41467-022-28034-z", "year": 2022, "journal": "Nature Communications", "role": "Benchmark of 14 differential-abundance methods; ALDEx2/ANCOM-II most concordant but lower power."},
    {"id": "ver-thorsen2016", "cite": "Thorsen J, et al. Large-scale benchmarking reveals false discoveries and count transformation sensitivity in 16S rRNA gene amplicon data analysis methods used in microbiome studies. Microbiome, 2016;4:62.", "pmid": "27884206", "doi": "10.1186/s40168-016-0208-8", "year": 2016, "journal": "Microbiome", "role": "High false-positive methods tend to have the best detection power."},
    {"id": "ver-hawinkel2019", "cite": "Hawinkel S, et al. A broken promise: microbiome differential abundance methods do not control the false discovery rate. Briefings in Bioinformatics, 2019;20(1):210-221.", "pmid": "28968702", "doi": "10.1093/bib/bbx104", "year": 2019, "journal": "Briefings in Bioinformatics", "role": "Excess of false discoveries across differential-abundance methods."},
    {"id": "ver-gaire2025", "cite": "Gaire TN, et al. The impact of pooling on the observed microbiome profile of preweaned piglet feces. FEMS Microbiology Ecology, 2025;101(6):fiaf058.", "pmid": "40478755", "doi": "10.1093/femsec/fiaf058", "year": 2025, "journal": "FEMS Microbiology Ecology", "role": "Pooled fecal samples comparable to individual at group level but underestimate low-abundance/low-prevalence taxa."},
    {"id": "ver-weinroth2022", "cite": "Weinroth MD, et al. Considerations and best practices in animal science 16S ribosomal RNA gene sequencing microbiome studies. Journal of Animal Science, 2022;100(2):skab346.", "pmid": "35106579", "doi": "10.1093/jas/skab346", "year": 2022, "journal": "Journal of Animal Science", "role": "Animal-science 16S best-practices review (design, sample size, pooling)."},
    {"id": "ver-sze2016", "cite": "Sze MA, Schloss PD. Looking for a Signal in the Noise: Revisiting Obesity and the Microbiome. mBio, 2016;7(4):e01018-16.", "pmid": "27555308", "doi": "10.1128/mBio.01018-16", "year": 2016, "journal": "mBio", "role": "Microbiome-phenotype associations are weak, confounded by large interpersonal variation and insufficient sample size."},
    {"id": "ver-chen2024-lp", "cite": "Chen J, et al. Mixed Bacillus subtilis and Lactiplantibacillus plantarum-fermented feed improves gut microbiota and immunity of Bamei piglet. Frontiers in Microbiology, 2024;15:1442373.", "pmid": "39268530", "doi": "10.3389/fmicb.2024.1442373", "year": 2024, "journal": "Frontiers in Microbiology", "role": "L. plantarum + B. subtilis fermented feed in Bamei piglets; LP x Bamei 16S niche already occupied."},
    {"id": "ver-zhang2024-prob", "cite": "Zhang M, et al. Effects of Probiotic-Fermented Feed on the Growth Profile, Immune Functions, and Intestinal Microbiota of Bamei Piglets. Animals, 2024;14(4):647.", "pmid": "38396614", "doi": "10.3390/ani14040647", "year": 2024, "journal": "Animals", "role": "Probiotic fermented feed in Bamei piglets."},
    {"id": "ver-jin2020", "cite": "Jin J, et al. Jejunal inflammatory cytokines, barrier proteins and microbiome-metabolome responses to early supplementary feeding of Bamei suckling piglets. BMC Microbiology, 2020;20:169.", "pmid": "32552686", "doi": "10.1186/s12866-020-01847-y", "year": 2020, "journal": "BMC Microbiology", "role": "Bamei suckling piglet jejunal microbiome-metabolome."},
    {"id": "ver-jin2019", "cite": "Jin J, et al. Effects of Maternal Low-Protein Diet on Microbiota Structure and Function in the Jejunum of Huzhu Bamei Suckling Piglets. Animals, 2019;9(10):713.", "pmid": "31547553", "doi": "10.3390/ani9100713", "year": 2019, "journal": "Animals", "role": "Huzhu Bamei piglet jejunal microbiota."},
    {"id": "ver-wang2020", "cite": "Wang D, et al. Effects of Dietary Protein Levels on Bamei Pig Intestinal Colony Compositional Traits. BioMed Research International, 2020;2020:2610431.", "pmid": "33294435", "doi": "10.1155/2020/2610431", "year": 2020, "journal": "BioMed Research International", "role": "Dietary protein effects on Bamei pig intestinal microbiota."},
    {"id": "ver-chen2022-lab", "cite": "Chen J, et al. Bacteriocin-Producing Lactic Acid Bacteria Strains with Antimicrobial Activity Screened from Bamei Pig Feces. Foods, 2022;11(5):709.", "pmid": "35267342", "doi": "10.3390/foods11050709", "year": 2022, "journal": "Foods", "role": "Bacteriocin-producing LAB isolated from Bamei pig feces."},
    {"id": "ver-tang2024", "cite": "Tang X, et al. Multi-Omics Analysis Reveals Dietary Fiber's Impact on Growth, Slaughter Performance, and Gut Microbiome in Durco x Bamei Crossbred Pig. Microorganisms, 2024;12(8):1674.", "pmid": "39203515", "doi": "10.3390/microorganisms12081674", "year": 2024, "journal": "Microorganisms", "role": "Fiber yields succinate-producing bacteria + TCA cycle + slaughter traits in Durco x Bamei."},
    {"id": "ver-wu2022", "cite": "Wu G, et al. Gastrointestinal Tract and Dietary Fiber Driven Alterations of Gut Microbiota and Metabolites in Durco x Bamei Crossbred Pigs. Frontiers in Nutrition, 2022;8:806646.", "pmid": "35155525", "doi": "10.3389/fnut.2021.806646", "year": 2022, "journal": "Frontiers in Nutrition", "role": "Durco x Bamei gut segment microbiota and metabolites."},
    {"id": "ver-tang2021", "cite": "Tang X, et al. Dietary Fiber Influences Bacterial Community Assembly Processes in the Gut Microbiota of Durco x Bamei Crossbred Pig. Frontiers in Microbiology, 2021;12:688554.", "pmid": "34956107", "doi": "10.3389/fmicb.2021.688554", "year": 2021, "journal": "Frontiers in Microbiology", "role": "Durco x Bamei community assembly processes."},
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
    jobs = ([(x, "starred_foundation", "starred") for x in STARS]
            + [(x, "verified_literature", "verified") for x in VERIFIED])
    for s, cat, src in jobs:
        abstract = fetch_abstract(s["pmid"])
        passage = abstract
        if s["role"]:
            passage = (passage + "\n\n" if passage else "") + f"[Project role] {s['role']}"
        docs.append({
            "id": s["id"],
            "source": src,
            "cite": s["cite"],
            "title": s["cite"].split(".")[0].strip(),
            "pmid": s["pmid"],
            "doi": s.get("doi", ""),
            "year": s["year"],
            "journal": s["journal"],
            "role": s["role"],
            "has_abstract": bool(abstract),
            "passage": passage,
            "category": cat,
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
        "version": "2026-09-30",
        "note": ("基础语料：BioTriage 星级收藏 7 篇奠基论文（starred_foundation）"
                 "+ 2026-09 会话新核验 19 篇（verified_literature；PubMed abstract + 项目定位评述）。"
                 "全部为已核验公开文献；不含未发表手稿/毕设内容（defer）。"),
        "documents": docs,
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\n已生成 {out}: {len(docs)} 篇文档")
    return 0


if __name__ == "__main__":
    sys.exit(main())
