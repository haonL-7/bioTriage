# -*- coding: utf-8 -*-
"""从站点构建产物生成检索语料 corpus/<domain>.json（三级池 + 质量分）。

    python scripts/build_rag_corpus.py --dry-run   只统计
    python scripts/build_rag_corpus.py             写 corpus/
"""
import argparse
import datetime
import glob
import hashlib
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, 'ai-news', '_site')
OUT = os.path.join(ROOT, 'corpus')
NOISE = re.compile(r'erratum|corrigendum|correction|retract|editorial|comment on|reply to|book review|in this issue', re.I)
MON = {m: i + 1 for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'])}
MIN_QUALITY = 30


def parse_date(value):
    s = str(value or '')[:11]
    m = re.match(r'^(\d{4})-(\w{3})-(\d{1,2})', s)
    if m and m.group(2).lower() in MON:
        try:
            return datetime.date(int(m.group(1)), MON[m.group(2).lower()], int(m.group(3)))
        except ValueError:
            return None
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})', s)
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def quality(doc, bonus):
    score = 50
    score += 15 if len(doc['abstract']) >= 200 else -20
    score += 10 if (doc.get('doi') or doc.get('pmid') or doc.get('url')) else -25
    score += bonus
    score -= 40 if NOISE.search(doc['title']) else 0
    return max(0, min(100, score))


def pool_of(ingested, today):
    age = (today - ingested).days
    return 'hot' if age <= 30 else ('warm' if age <= 180 else 'archive')


def _previous_ingested():
    try:
        with open(os.path.join(OUT, 'meta.json'), encoding='utf-8') as f:
            return json.load(f).get('ingested') or {}
    except (OSError, ValueError):
        return {}


def build(write=True, today=None):
    today = today or datetime.date.today()
    previous = _previous_ingested()
    docs = {}

    def add(domain, row, abstract_key='abstract', date_key='pub_date', source='channel'):
        title = str(row.get('title') or '').strip()
        if not title:
            return
        abstract = str(row.get(abstract_key) or '').strip()[:2000]
        url = str(row.get('url') or '')
        doi = str(row.get('doi') or '')
        pmid = str(row.get('pmid') or '')
        authors = str(row.get('first_author') or row.get('firstAuthor') or row.get('cite') or '').strip()
        pub = parse_date(row.get(date_key))
        key = re.sub(r'\W+', '', title.lower())[:80] or hashlib.md5(title.encode('utf-8')).hexdigest()
        ingested = parse_date(previous.get(key)) or pub or today
        bonus = {'High': 15, 'Medium': 5, 'Low': -8}.get(str(row.get('significance')), 0)
        candidate = {
            'id': key, 'domain': domain, 'source': source, 'title': title, 'abstract': abstract,
            'authors': authors[:160],
            'tags': [t for t in (row.get('tags') or []) if isinstance(t, str)],
            'journal': str(row.get('journal') or ''), 'doi': doi, 'pmid': pmid, 'url': url,
            'evidence_level': str(row.get('evidenceLevel') or ''),
            'year': pub.year if pub else None,
            'ingested_at': ingested.isoformat(), 'pool': pool_of(ingested, today),
        }
        candidate['quality'] = quality(candidate, bonus)
        existing = docs.get(key)
        if existing is None or candidate['quality'] > existing['quality']:
            docs[key] = candidate

    for path in sorted(glob.glob(os.path.join(SITE, '*', 'data', 'papers.json'))):
        domain = os.path.basename(os.path.dirname(os.path.dirname(path)))
        with open(path, encoding='utf-8') as f:
            for row in json.load(f).get('papers') or []:
                add(domain, row)
    evidence_path = os.path.join(SITE, 'evidence', 'data', 'news.json')
    if os.path.isfile(evidence_path):
        with open(evidence_path, encoding='utf-8') as f:
            for row in json.load(f).get('papers') or []:
                add('evidence', row, date_key='pubDate', source='evidence')
    legacy_path = os.path.join(ROOT, 'documents.json')
    if os.path.isfile(legacy_path):
        with open(legacy_path, encoding='utf-8') as f:
            for row in json.load(f).get('documents') or []:
                add('gut-legacy', {'title': row.get('title'), 'passage': row.get('passage'),
                                   'cite': row.get('cite')}, abstract_key='passage', source='legacy')

    kept = [d for d in docs.values() if d['quality'] >= MIN_QUALITY]
    by_domain = {}
    for doc in kept:
        by_domain.setdefault(doc['domain'], []).append(doc)
    stats = {
        'generated_at': today.isoformat(), 'documents': len(kept),
        'dropped_low_quality': len(docs) - len(kept),
        'pools': {p: sum(1 for d in kept if d['pool'] == p) for p in ('hot', 'warm', 'archive')},
        'domains': {k: len(v) for k, v in sorted(by_domain.items())},
    }
    if write:
        os.makedirs(OUT, exist_ok=True)
        for name in os.listdir(OUT):
            if name.endswith('.json'):
                os.remove(os.path.join(OUT, name))
        for domain, items in by_domain.items():
            path = os.path.join(OUT, '%s.json' % re.sub(r'[^A-Za-z0-9_-]', '_', domain))
            with open(path, 'w', encoding='utf-8') as f:
                json.dump({'domain': domain, 'documents': items}, f, ensure_ascii=False, indent=1)
        with open(os.path.join(OUT, 'meta.json'), 'w', encoding='utf-8') as f:
            json.dump({'stats': stats, 'ingested': {d['id']: d['ingested_at'] for d in kept}},
                      f, ensure_ascii=False, indent=1)
    return stats, kept


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    stats, _ = build(write=not args.dry_run)
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    print('DRY-RUN' if args.dry_run else 'WRITTEN -> %s' % OUT)


if __name__ == '__main__':
    main()
