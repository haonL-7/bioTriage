#!/usr/bin/env python3
"""
Lightweight broad crawler — PubMed E-utilities primary, Semantic Scholar supplementary.
Target: free full-text articles across bioinformatics channels.
"""
import json, os, sys, time, hashlib
from datetime import datetime, timedelta
from urllib.parse import quote
from xml.etree import ElementTree as ET
import requests
import feedparser
from bs4 import BeautifulSoup

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ai-news", "data")
HEADERS = {"User-Agent": "BioTriage-Curator/1.0 (mailto:research@example.com)"}
TIMEOUT = 30
SEARCH_DAYS = 30

PUBMED_SEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_FETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

# PubMed-optimized queries — free full text filter applied automatically
CHANNEL_QUERIES = {
    "computational-genomics": [
        '((genomic*[Title/Abstract] OR genome[Title/Abstract]) AND (computational[Title/Abstract] OR bioinformatic*[Title/Abstract] OR algorithm[Title/Abstract]) AND (variant[Title/Abstract] OR assembly[Title/Abstract] OR annotation[Title/Abstract] OR GWAS[Title/Abstract]))',
        '((epigenomic*[Title/Abstract] OR epigenetic*[Title/Abstract]) AND (methylation[Title/Abstract] OR histone[Title/Abstract] OR chromatin[Title/Abstract]) AND (computational[Title/Abstract] OR tool[Title/Abstract] OR pipeline[Title/Abstract]))',
    ],
    "metagenomics-informatics": [
        '((metagenomic*[Title/Abstract] OR metagenome[Title/Abstract]) AND (assembly[Title/Abstract] OR binning[Title/Abstract] OR taxonomic[Title/Abstract] OR functional annotation[Title/Abstract]))',
        '((microbiome[Title/Abstract] OR microbiota[Title/Abstract]) AND (bioinformatic*[Title/Abstract] OR computational[Title/Abstract] OR method[Title/Abstract] OR tool[Title/Abstract]))',
    ],
    "structural-bioinformatics": [
        '((protein structure[Title/Abstract] OR AlphaFold[Title/Abstract] OR molecular dynamics[Title/Abstract] OR docking[Title/Abstract]) AND (prediction[Title/Abstract] OR simulation[Title/Abstract] OR computational[Title/Abstract]))',
        '((structure-based[Title/Abstract] OR rational design[Title/Abstract]) AND (protein[Title/Abstract] OR enzyme[Title/Abstract]))',
    ],
    "systems-biology": [
        '((gene regulatory network[Title/Abstract] OR GRN[Title/Abstract]) AND (inference[Title/Abstract] OR reconstruction[Title/Abstract] OR modeling[Title/Abstract]))',
        '((flux balance[Title/Abstract] OR FBA[Title/Abstract] OR metabolic model[Title/Abstract]) AND (genome-scale[Title/Abstract] OR constraint-based[Title/Abstract]))',
        '((network biology[Title/Abstract] OR systems biology[Title/Abstract]) AND (computational[Title/Abstract] OR model[Title/Abstract] OR analysis[Title/Abstract]) AND (pathway[Title/Abstract] OR interactome[Title/Abstract] OR multi-omics[Title/Abstract]))',
    ],
    "ml-biology": [
        '((machine learning[Title/Abstract] OR deep learning[Title/Abstract] OR graph neural network[Title/Abstract]) AND (protein[Title/Abstract] OR genomic*[Title/Abstract] OR sequence[Title/Abstract]) AND (method[Title/Abstract] OR tool[Title/Abstract] OR model[Title/Abstract]))',
        '((language model[Title/Abstract] OR LLM[Title/Abstract] OR transformer[Title/Abstract]) AND (protein[Title/Abstract] OR DNA[Title/Abstract] OR RNA[Title/Abstract] OR biological[Title/Abstract]))',
    ],
    "single-cell-omics": [
        '((single-cell[Title/Abstract] OR single cell[Title/Abstract]) AND (RNA-seq[Title/Abstract] OR ATAC-seq[Title/Abstract] OR multiome[Title/Abstract]) AND (method[Title/Abstract] OR tool[Title/Abstract] OR algorithm[Title/Abstract]))',
        '((spatial transcriptomic*[Title/Abstract] OR spatial genomic*[Title/Abstract]) AND (computational[Title/Abstract] OR analysis[Title/Abstract] OR method[Title/Abstract]))',
    ],
    "databases-knowledge-graphs": [
        '((biological database[Title/Abstract] OR knowledge graph[Title/Abstract] OR ontology[Title/Abstract]) AND (resource[Title/Abstract] OR update[Title/Abstract] OR release[Title/Abstract]))',
        '((FAIR data[Title/Abstract] OR data integration[Title/Abstract]) AND (biology[Title/Abstract] OR life science*[Title/Abstract]))',
    ],
    "ai-drug-discovery": [
        '((drug discovery[Title/Abstract] OR virtual screening[Title/Abstract]) AND (machine learning[Title/Abstract] OR deep learning[Title/Abstract] OR AI[Title/Abstract]))',
        '((ADMET[Title/Abstract] OR drug repurposing[Title/Abstract]) AND (prediction[Title/Abstract] OR computational[Title/Abstract]))',
    ],
}

def _make_id(title, source):
    return hashlib.sha256(f"{title}|{source}".encode()).hexdigest()[:12]

def _clean_html(text):
    if not text: return ""
    return " ".join(BeautifulSoup(text, "html.parser").get_text(separator=" ", strip=True).split())

# ---- PubMed E-utilities (primary source) ----

def search_pubmed(query: str, max_results: int = 30) -> list[str]:
    """Search PubMed, return PMIDs. Filter for free full text."""
    full_query = f"{query} AND (free full text[sb])"
    params = {
        "db": "pubmed", "term": full_query,
        "retmax": max_results, "retmode": "json", "sort": "date",
        "datetype": "pdat",
        "mindate": (datetime.now() - timedelta(days=SEARCH_DAYS)).strftime("%Y/%m/%d"),
        "maxdate": datetime.now().strftime("%Y/%m/%d"),
    }
    try:
        resp = requests.get(PUBMED_SEARCH, params=params, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json().get("esearchresult", {}).get("idlist", [])
    except Exception as e:
        print(f"    PubMed search: {e}")
        return []

def fetch_pubmed_details(pmids: list[str]) -> list[dict]:
    """Fetch article details from PubMed by PMID."""
    if not pmids: return []
    params = {"db": "pubmed", "id": ",".join(pmids), "retmode": "xml", "rettype": "abstract"}
    try:
        resp = requests.get(PUBMED_FETCH, params=params, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        articles = []
        for art_elem in root.findall(".//PubmedArticle"):
            try:
                med = art_elem.find(".//MedlineCitation")
                article = med.find(".//Article")
                if article is None: continue
                title_elem = article.find(".//ArticleTitle")
                title = "".join(title_elem.itertext()) if title_elem is not None else ""
                abst_elem = article.find(".//Abstract/AbstractText")
                abstract = "".join(abst_elem.itertext()) if abst_elem is not None else ""
                jn_elem = article.find(".//Journal/Title")
                journal = jn_elem.text if jn_elem is not None else ""
                doi_elem = article.find(".//ELocationID[@EIdType='doi']")
                doi = doi_elem.text if doi_elem is not None else ""
                pmid = med.find(".//PMID").text if med.find(".//PMID") is not None else ""
                pub_date_elem = article.find(".//Journal/JournalIssue/PubDate")
                y = pub_date_elem.findtext("Year", "") if pub_date_elem is not None else ""
                m = pub_date_elem.findtext("Month", "") if pub_date_elem is not None else ""
                d = pub_date_elem.findtext("Day", "01") if pub_date_elem is not None else ""
                pub_date = f"{y}-{m}-{d}" if y else ""
                authors = []
                for au in article.findall(".//AuthorList/Author"):
                    ln = au.findtext("LastName", "")
                    ini = au.findtext("Initials", "")
                    if ln: authors.append(f"{ln} {ini}")
                url = f"https://doi.org/{quote(doi, safe='')}" if doi else f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
                links = []
                if doi: links.append({"type": "doi", "label": "DOI", "url": url})
                if pmid: links.append({"type": "pubmed", "label": "PubMed", "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"})
                articles.append({
                    "id": _make_id(title, "pubmed"),
                    "title": title.strip(), "abstract": abstract.strip()[:600],
                    "journal": journal, "doi": doi, "pmid": pmid,
                    "first_author": authors[0] if authors else "",
                    "pub_date": pub_date, "source": "PubMed",
                    "url": url, "links": links,
                })
            except Exception:
                continue
        return articles
    except Exception as e:
        print(f"    PubMed fetch: {e}")
        return []

# ---- Supplementary sources ----

def fetch_semantic_scholar(query: str, max_results: int = 15) -> list:
    """Supplementary papers from Semantic Scholar."""
    articles = []
    base = "https://api.semanticscholar.org/graph/v1/paper/search"
    params = {"query": query.replace("[Title/Abstract]", ""), "limit": max_results,
              "fields": "title,abstract,year,externalIds,journal,publicationDate,authors,openAccessPdf"}
    try:
        resp = requests.get(base, params=params, headers=HEADERS, timeout=TIMEOUT)
        if resp.status_code == 429: time.sleep(5); return []
        resp.raise_for_status()
        for item in resp.json().get("data", []):
            title = (item.get("title") or "").strip()
            abstract = (item.get("abstract") or "")[:600]
            if not title: continue
            doi = (item.get("externalIds") or {}).get("DOI", "")
            journal = (item.get("journal") or {}).get("name", "") if item.get("journal") else ""
            pub_date = str(item.get("year", ""))
            url = f"https://doi.org/{quote(doi, safe='')}" if doi else ""
            if not url: continue
            articles.append({
                "id": _make_id(title, "s2"), "title": title[:300], "abstract": abstract,
                "journal": journal, "doi": doi, "pmid": "", "first_author": "",
                "pub_date": pub_date, "source": "Semantic Scholar", "url": url,
                "links": [{"type": "doi", "label": "DOI", "url": url}],
            })
    except Exception as e:
        print(f"    S2: {e}")
    return articles

def crawl_channel(channel_key: str, max_pubmed_per_query: int = 30) -> list:
    """Primary: PubMed free full text. Supplementary: Semantic Scholar."""
    queries = CHANNEL_QUERIES.get(channel_key, [])
    if not queries:
        print(f"  Unknown channel: {channel_key}")
        return []

    all_articles = []
    for q in queries:
        # PubMed primary
        pmids = search_pubmed(q, max_pubmed_per_query)
        if pmids:
            details = fetch_pubmed_details(pmids)
            all_articles.extend(details)
            print(f"    PubMed: {len(details)} papers")
        else:
            print(f"    PubMed: 0 papers")
        time.sleep(0.4)

    # Semantic Scholar supplementary (just first query, simplified)
    if queries:
        s2_papers = fetch_semantic_scholar(queries[0], 10)
        all_articles.extend(s2_papers)
        print(f"    S2: {len(s2_papers)} papers")

    # Deduplicate
    seen = set()
    unique = []
    for a in all_articles:
        key = a["title"][:80].lower()
        h = hashlib.md5(key.encode()).hexdigest()
        if h not in seen:
            seen.add(h)
            unique.append(a)

    unique.sort(key=lambda a: a.get("pub_date", ""), reverse=True)
    return unique

if __name__ == "__main__":
    ch = sys.argv[1] if len(sys.argv) > 1 else "computational-genomics"
    papers = crawl_channel(ch)
    print(f"\nTotal: {len(papers)} papers")
    for p in papers[:5]:
        print(f"  [{p['source']}] {p['title'][:70]}")
        print(f"    {p['journal'][:50]} | {p['pub_date']}")
