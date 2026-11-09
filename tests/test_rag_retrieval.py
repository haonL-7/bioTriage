# -*- coding: utf-8 -*-
"""检索层回归：语料可加载、中英跨学科提问非空、领域命中、无关提问不外溢。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rag.index import RagIndex, expand, load_corpus

CORPUS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'corpus')
CASES = [
    ('单细胞轨迹推断方法', 'single-cell-omics'),
    ('空间转录组去卷积算法', 'single-cell-omics'),
    ('AlphaFold 蛋白结构预测', 'structural-bioinformatics'),
    ('宏基因组组装与分箱', 'metagenomics-informatics'),
    ('知识图谱本体构建', 'databases-knowledge-graphs'),
    ('虚拟筛选与 ADMET 预测', 'ai-drug-discovery'),
    ('基因调控网络推断', 'systems-biology'),
    ('表观遗传甲基化检测工具', 'computational-genomics'),
    ('生酮饮食对肠道菌群的影响', 'evidence'),
    ('single-cell trajectory inference', 'single-cell-omics'),
    ('protein language model benchmark', 'ml-biology'),
    ('metagenomic binning tool', 'metagenomics-informatics'),
    ('variant calling pipeline', 'computational-genomics'),
    ('spatial deconvolution', 'single-cell-omics'),
    ('gut microbiota propionate backfat', 'evidence'),
]


class RetrievalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docs = load_corpus(CORPUS)
        if not cls.docs:
            raise unittest.SkipTest('corpus/ 尚未生成，先跑 scripts/build_rag_corpus.py')
        cls.index = RagIndex(cls.docs)

    def test_corpus_is_multidomain(self):
        domains = {d['domain'] for d in self.docs}
        self.assertGreaterEqual(len(domains), 8, domains)
        self.assertGreater(len(self.docs), 300)

    def test_schema_contract(self):
        required = ('id', 'domain', 'title', 'abstract', 'ingested_at', 'pool', 'quality')
        for doc in self.docs[:50]:
            for key in required:
                self.assertIn(key, doc, doc.get('id'))

    def test_chinese_queries_are_not_empty(self):
        empty = [q for q, _ in CASES if not self.index.search_with_fallback(q, top_k=6)]
        self.assertEqual(empty, [], '以下提问召回为空：%s' % empty)

    def test_domain_hit_rate(self):
        hits = 0
        for query, expected in CASES:
            domains = [d['domain'] for _, d in self.index.search_with_fallback(query, top_k=6)]
            hits += 1 if expected in domains else 0
        self.assertGreaterEqual(hits, int(len(CASES) * 0.75), '领域命中 %d/%d' % (hits, len(CASES)))

    def test_domain_filter(self):
        results = self.index.search_with_fallback('单细胞轨迹推断方法', domains=['single-cell-omics'], top_k=3)
        self.assertTrue(all(d['domain'] == 'single-cell-omics' for _, d in results))

    def test_out_of_scope_returns_nothing(self):
        self.assertEqual(self.index.search_with_fallback('今天天气怎么样', top_k=3), [])

    def test_expand_has_no_duplicate_terms(self):
        """同一英文词被多个中文概念命中时必须去重（曾导致 ontology 重复计权）。"""
        terms = [t for t, _ in expand('知识图谱本体构建')]
        self.assertEqual(len(terms), len(set(terms)), 'expand 返回了重复词：%s' % terms)

    def test_knowledge_graph_ranks_before_ontology(self):
        domains = [d['domain'] for _, d in self.index.search_with_fallback('知识图谱本体构建', top_k=3)]
        self.assertIn('databases-knowledge-graphs', domains, domains)


if __name__ == '__main__':
    unittest.main()
