# -*- coding: utf-8 -*-
"""检索核心：词干/短语倒排 + 双语查询扩展 + 三级语料池回落（纯标准库）"""
import collections
import json
import math
import os
import re

from .lexicon import LEXICON, ZH_QUERY_FILLER

STOP = set((
    'the a an of and or to in for with on by from at as is are was were be been this that these those '
    'we our their its it not no but also using used use study studies results conclusion background '
    'methods objective new based via towards toward can could may might here data'
).split())

POOL_WEIGHT = {'hot': 1.2, 'warm': 1.0, 'archive': 0.75}


def stem(word):
    if len(word) > 5 and word.endswith('ing'):
        return word[:-3]
    if len(word) > 4 and (word.endswith('ies') or word.endswith('ses')):
        return word[:-2]
    if len(word) > 3 and word.endswith('s') and not word.endswith('ss'):
        return word[:-1]
    return word


def en_tokens(text):
    words = [stem(w) for w in re.findall(r'[a-zA-Z][a-zA-Z0-9\-]{2,}', str(text or '').lower()) if w not in STOP]
    out = list(words)
    for i in range(len(words) - 1):
        out.append(words[i] + ' ' + words[i + 1])
    return out


def expand(query, primary=2.0, secondary=1.2):
    """中文概念 -> 英文检索词。

    同一英文词被多个中文概念命中时取最大权重而不是累加（否则 ontology 之类会被
    重复计权，把 knowledge graph 的研究压到下面）；每个概念的首个英文词为主词，
    权重高于同义次词，避免"命中一个强词"输给"命中多个弱词"。
    """
    low = str(query or '').lower()
    for filler in ZH_QUERY_FILLER:
        low = low.replace(filler, ' ')
    weights = {}
    for cn, ens in LEXICON.items():
        if cn in low:
            for i, english in enumerate(ens):
                weights[english] = max(weights.get(english, 0.0), primary if i == 0 else secondary)
    for token in en_tokens(query):
        weights[token] = max(weights.get(token, 0.0), 1.0)
    out = []
    for term, weight in weights.items():
        key = ' '.join(stem(w) for w in term.split()) if ' ' in term else stem(term)
        out.append((key, weight))
    return out


class RagIndex(object):
    def __init__(self, docs):
        self.docs = [d for d in docs if d.get('title')]
        self.by_id = {}
        self.post = collections.defaultdict(dict)
        df = collections.Counter()
        for doc in self.docs:
            did = doc['id']
            self.by_id[did] = doc
            fields = (
                (3.0, collections.Counter(en_tokens(doc.get('title')))),
                (2.0, collections.Counter(en_tokens(' '.join(doc.get('tags') or [])))),
                (1.0, collections.Counter(en_tokens(doc.get('abstract')))),
            )
            for field_weight, counts in fields:
                for term, count in counts.items():
                    w = (1.5 if ' ' in term else 1.0) * field_weight * (1 + math.log(1 + count))
                    self.post[term][did] = self.post[term].get(did, 0.0) + w
            for counts in (c for _, c in fields):
                for term in counts:
                    df[term] += 1
        self.df = df
        self.n = max(1, len(self.docs))

    def search(self, query, domains=None, top_k=3):
        scored = collections.defaultdict(float)
        for term, qweight in expand(query):
            postings = self.post.get(term)
            if not postings:
                continue
            idf = math.log(1 + self.n / self.df[term])
            for did, weight in postings.items():
                scored[did] += qweight * weight * idf
        allow = set(domains) if domains else None
        ranked = []
        for did, score in scored.items():
            doc = self.by_id[did]
            if allow and doc.get('domain') not in allow:
                continue
            ranked.append((score * POOL_WEIGHT.get(doc.get('pool', 'warm'), 1.0), doc))
        ranked.sort(key=lambda x: -x[0])
        return ranked[:top_k]

    def search_with_fallback(self, query, domains=None, top_k=6, min_hits=3):
        hits = self.search(query, domains=domains, top_k=top_k)
        live = [h for h in hits if h[1].get('pool') in ('hot', 'warm')]
        if len(live) >= min_hits:
            return live
        return hits or live


def load_corpus(corpus_dir):
    docs = []
    if not os.path.isdir(corpus_dir):
        return docs
    for name in sorted(os.listdir(corpus_dir)):
        if not name.endswith('.json') or name == 'meta.json':
            continue
        try:
            with open(os.path.join(corpus_dir, name), encoding='utf-8') as f:
                docs.extend(json.load(f).get('documents') or [])
        except (ValueError, OSError):
            continue
    return docs
