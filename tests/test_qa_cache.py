"""窄接缝:在线问答答档缓存的命中 / 失效 / 统计(T15,不触网)。"""

from __future__ import annotations

from app.domain.qa import Citation, GenerationUsage, GeneratorKind, QueryLog
from app.services.qa_cache import AnswerCache


def _log(question: str = "入职多久可以申请年假?") -> QueryLog:
    return QueryLog(
        trace_id="trace-1",
        question=question,
        answer="入职满一年后可以申请年假。",
        refused=False,
        top_k=3,
        min_score=0.35,
        latency_ms=12.5,
        generator=GeneratorKind.RULE_BASED,
        token_usage=GenerationUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


def _citation(chunk_id: str = "chk-1") -> Citation:
    return Citation(index=1, chunk_id=chunk_id, document_id="doc-1", score=0.9)


def _cache(**overrides: object) -> AnswerCache:
    values: dict = {"ttl_seconds": 300.0, "maxsize": 8}
    values.update(overrides)
    return AnswerCache(**values)  # type: ignore[arg-type]


def _key(cache: AnswerCache, name: str = "年假怎么算?") -> str:
    return cache.make_key(
        question=name, top_k=3, min_score=0.35, filters={}, generator="rule"
    )


def test_normalize_question_collapses_whitespace_and_case():
    long_in = "  年假   多久  可以    申请?\t"
    assert AnswerCache.normalize_question(long_in) == "年假 多久 可以 申请?"
    assert AnswerCache.normalize_question("Annual Leave?") == "annual leave?"


def test_key_varies_with_every_process_dimension():
    cache = _cache()
    base = dict(top_k=3, min_score=0.35, filters={}, generator="rule_based")
    same = dict(base, top_k=3, min_score=0.35, filters={}, generator="rule_based")

    key_default = cache.make_key(question="年假怎么算?", **base)  # type: ignore[arg-type]
    assert cache.make_key(question="年假怎么算?", **same) == key_default  # type: ignore[arg-type] # 同口径同键
    assert cache.make_key(question="病假怎么算?", **base) != key_default  # type: ignore[arg-type]
    # 同题不同过程口径(过滤 / top_k / 阈值 / 生成器)都不共享答档
    variants = [
        dict(base, filters={"tenant": "acme"}),
        dict(base, top_k=5),
        dict(base, min_score=0.5),
        dict(base, generator="llm"),
    ]
    for variant in variants:
        assert cache.make_key(question="年假怎么算?", **variant) != key_default  # type: ignore[arg-type]


def test_roundtrip_hit_counts_and_hit_rate():
    cache = _cache()
    key = _key(cache)
    log = _log()

    cache.put(key, log=log, citations=[_citation()])
    entry = cache.get(key)

    assert entry is not None
    assert entry.log.answer == "入职满一年后可以申请年假。"
    assert [c.chunk_id for c in entry.citations] == ["chk-1"]

    cache.get(key)
    stats = cache.stats()
    assert (stats.hits, stats.misses) == (2, 0)
    assert stats.hit_rate == 1.0


def test_missing_key_counts_as_miss():
    cache = _cache()
    assert cache.get("no-such-key") is None
    assert cache.stats().misses == 1
    assert cache.stats().hit_rate == 0.0


def test_ttl_expiry_forces_miss():
    class FakeClock:
        def __init__(self) -> None:
            self.now = 0.0

        def __call__(self) -> float:
            return self.now

    clock = FakeClock()
    cache = _cache(ttl_seconds=60.0, clock=clock)
    key = _key(cache)
    cache.put(key, log=_log(), citations=[])

    clock.now = 59.9
    assert cache.get(key) is not None
    clock.now = 60.0
    assert cache.get(key) is None


def test_maxsize_evicts_oldest_entries():
    cache = _cache(maxsize=2)

    cache.put(_key(cache, "甲"), log=_log("甲"), citations=[])
    cache.put(_key(cache, "乙"), log=_log("乙"), citations=[])
    cache.put(_key(cache, "丙"), log=_log("丙"), citations=[])

    assert cache.get(_key(cache, "甲")) is None  # 最旧条目先淘汰
    assert cache.get(_key(cache, "乙")) is not None
    assert cache.get(_key(cache, "丙")) is not None


def test_hit_refreshes_recency_against_eviction():
    cache = _cache(maxsize=2)

    cache.put(_key(cache, "甲"), log=_log("甲"), citations=[])
    cache.put(_key(cache, "乙"), log=_log("乙"), citations=[])
    assert cache.get(_key(cache, "甲")) is not None  # 命中把"甲"抬回最新
    cache.put(_key(cache, "丙"), log=_log("丙"), citations=[])

    assert cache.get(_key(cache, "甲")) is not None
    assert cache.get(_key(cache, "乙")) is None  # 没被命中过的"乙"退位


def test_clear_drops_all_entries_and_reports_evictions():
    cache = _cache()
    key = _key(cache)
    cache.put(key, log=_log(), citations=[_citation()])

    assert cache.clear(reason="document_embedded") == 1
    assert cache.get(key) is None
