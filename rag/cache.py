# -*- coding: utf-8 -*-
"""学术助手答案缓存：库优先（复用 accounts/db.py），DB 不可用时降级进程内 LRU。

键 = 归一化问题 + 频道范围 + 语料指纹，语料一刷新旧答案自动失效。
只缓存成功答案；命中不计入配额。
"""
import collections
import datetime
import hashlib
import json
import threading

TTL_DAYS = 7
MAX_ROWS = 5000
MEM_MAX = 200

_memory = collections.OrderedDict()
_lock = threading.Lock()
_ready = None
_writes = 0
_MODEL = None

try:
    from sqlalchemy import Column, DateTime, Integer, String, Text

    from accounts.db import Base

    class RagAnswerCache(Base):
        __tablename__ = 'rag_answer_cache'

        key = Column(String(64), primary_key=True)
        question = Column(Text)
        scope = Column(String(240))
        answer = Column(Text)
        sources_json = Column(Text)
        corpus_fp = Column(String(40))
        created_at = Column(DateTime, index=True)
        last_hit_at = Column(DateTime, index=True)
        hits = Column(Integer, default=0)

    _MODEL = RagAnswerCache
except Exception:
    _MODEL = None


def normalize(text):
    s = str(text or '').strip().lower()
    for src, dst in (('\u3000', ' '), ('\uff1f', '?'), ('\uff0c', ','), ('\u3002', '.'), ('\uff1a', ':')):
        s = s.replace(src, dst)
    s = ' '.join(s.split())
    return s.rstrip(' ?.,')


def make_key(question, domains, fingerprint):
    scope = ','.join(sorted(d.strip() for d in domains)) if domains else 'all'
    raw = '%s|%s|%s' % (normalize(question), scope, fingerprint or '-')
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()


def _ensure_db():
    global _ready
    if _ready is not None:
        return _ready
    if _MODEL is None:
        print('[RAG] 答案缓存使用进程内内存（sqlalchemy / accounts 不可用）')
        _ready = False
        return False
    try:
        from accounts.db import Base as _Base, get_engine
        _Base.metadata.create_all(get_engine())
        _ready = True
    except Exception as e:
        print('[RAG] 答案缓存降级为内存: %s' % e)
        _ready = False
    return _ready


def _mem_get(key):
    with _lock:
        item = _memory.get(key)
        if not item:
            return None
        if (datetime.datetime.utcnow() - item['stored_at']).days > TTL_DAYS:
            _memory.pop(key, None)
            return None
        _memory.move_to_end(key)
        return item


def _mem_put(key, payload):
    with _lock:
        _memory[key] = payload
        _memory.move_to_end(key)
        while len(_memory) > MEM_MAX:
            _memory.popitem(last=False)


def get(key):
    if not key:
        return None
    if _ensure_db():
        try:
            from accounts.db import session_scope
            with session_scope() as session:
                row = session.get(_MODEL, key)
                if row is None:
                    return None
                if (datetime.datetime.utcnow() - (row.created_at or datetime.datetime.utcnow())).days > TTL_DAYS:
                    session.delete(row)
                    return None
                row.hits = (row.hits or 0) + 1
                row.last_hit_at = datetime.datetime.utcnow()
                sources = json.loads(row.sources_json or '[]')
                return {'answer': row.answer or '', 'sources': sources, 'retrieved': len(sources)}
        except Exception as e:
            print('[RAG] 缓存读取失败，降级内存: %s' % e)
    item = _mem_get(key)
    return item['payload'] if item else None


def put(key, question, domains, answer, sources, fingerprint, retrieved=0):
    global _writes
    if not key or not answer:
        return
    lowered = answer.lower()
    for marker in ('服务暂时不可用', '请稍后重试', 'unavailable', 'temporarily'):
        if marker in answer or marker in lowered:
            return
    payload = {'answer': answer, 'sources': sources or [], 'retrieved': retrieved}
    scope = ','.join(sorted(domains)) if domains else 'all'
    if _ensure_db():
        try:
            from accounts.db import session_scope
            now = datetime.datetime.utcnow()
            with session_scope() as session:
                row = session.get(_MODEL, key)
                if row is None:
                    session.add(_MODEL(key=key, question=question[:2000], scope=scope, answer=answer,
                                       sources_json=json.dumps(sources or [], ensure_ascii=False),
                                       corpus_fp=fingerprint or '', created_at=now, last_hit_at=now, hits=0))
                else:
                    row.answer = answer
                    row.sources_json = json.dumps(sources or [], ensure_ascii=False)
                    row.corpus_fp = fingerprint or ''
                    row.created_at = now
            _mem_put(key, {'payload': payload, 'stored_at': now})
            _writes += 1
            if _writes % 100 == 0:
                evict()
            return
        except Exception as e:
            print('[RAG] 缓存写入失败，降级内存: %s' % e)
    _mem_put(key, {'payload': payload, 'stored_at': datetime.datetime.utcnow()})


def evict():
    if not _ensure_db():
        return 0
    try:
        from accounts.db import session_scope
        from sqlalchemy import func, select
        with session_scope() as session:
            total = session.scalar(select(func.count()).select_from(_MODEL)) or 0
            if total <= MAX_ROWS:
                return 0
            stale = session.scalars(
                select(_MODEL.key).order_by(_MODEL.last_hit_at.asc()).limit(total - MAX_ROWS)).all()
            for k in stale:
                row = session.get(_MODEL, k)
                if row is not None:
                    session.delete(row)
            return len(stale)
    except Exception as e:
        print('[RAG] 缓存淘汰失败: %s' % e)
        return 0
