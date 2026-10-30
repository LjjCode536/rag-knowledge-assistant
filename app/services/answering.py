"""在线问答编排:检索 → 低分兜底 → 上下文构建 → 有据回答 → 查询日志。

主链路只依赖领域契约与窄接缝:检索服务、生成器、文档 / 查询日志仓储。
资料不足时统一拒答,绝不把低分命中包装成答案;每次问答(含拒答)都留日志。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import structlog

from app.core.config import Settings
from app.domain.documents import DocumentRecord
from app.domain.qa import (
    REFUSAL_ANSWER,
    AnswerContext,
    Citation,
    ContextSource,
    GenerationUsage,
    QueryHit,
    QueryLog,
    RefusalReason,
)
from app.domain.repositories import DocumentRepository, QueryLogRepository
from app.domain.retrieval import ChunkFilter, ScoredChunk
from app.services.generation import AnswerGenerator
from app.services.qa_cache import AnswerCache, CacheEntry
from app.services.retrieval import RetrievalService

logger = structlog.get_logger("answering")


@dataclass(slots=True)
class AnswerRunResult:
    """一次问答的结果:查询日志 + 对外引用;命中缓存时 cache_hit 为真。"""

    log: QueryLog
    citations: list[Citation]
    cache_hit: bool = False


class AnswerService:
    """把用户问题变成有据回答,并保证每次问答都有可追溯日志。

    相同问题(归一化同题)在缓存 TTL 内直接复用答档(T15):生成链路整段
    跳过,延迟显著降低、上游零消耗;命中仍各留一条查询日志,命中 / 未命中
    情况在结构日志中可见。
    """

    def __init__(
        self,
        *,
        retrieval_service: RetrievalService,
        document_repository: DocumentRepository,
        query_log_repository: QueryLogRepository,
        generator: AnswerGenerator,
        settings: Settings,
        answer_cache: AnswerCache | None = None,
    ) -> None:
        self._retrieval = retrieval_service
        self._documents = document_repository
        self._query_logs = query_log_repository
        self._generator = generator
        self._settings = settings
        self._cache = answer_cache

    async def ask(
        self,
        question: str,
        *,
        top_k: int | None = None,
        filters: ChunkFilter | None = None,
        trace_id: str,
    ) -> AnswerRunResult:
        """问答一次:top-k 检索、低分拒答、编号上下文与引用,全程留痕。"""
        effective_top_k = top_k if top_k is not None else self._settings.qa_top_k
        min_score = self._settings.qa_min_score
        filters_effective = (filters or ChunkFilter()).effective()
        started = time.perf_counter()

        cache_key = self._cache_key(
            question=question,
            top_k=effective_top_k,
            min_score=min_score,
            filters_effective=filters_effective,
        )
        if cache_key is not None:
            entry = self._cache.get(cache_key)
            if entry is None:
                stats = self._cache.stats()
                logger.info(
                    "qa_cache_miss",
                    trace_id=trace_id,
                    question_chars=len(question),
                    cache_hits=stats.hits,
                    cache_misses=stats.misses,
                    cache_hit_rate=stats.hit_rate,
                )
            else:
                return self._answered_from_cache(
                    entry, question=question, trace_id=trace_id, started=started
                )

        outcome = await self._retrieval.retrieve(
            question, top_k=effective_top_k, filters=filters
        )
        hits = outcome.hits
        accepted = [hit for hit in hits if hit.score >= min_score]
        sources = self._sources_of(accepted)
        context = AnswerContext(
            text=build_context(sources) if sources else "",
            sources=sources,
        )

        reason: RefusalReason | None = None
        if not hits:
            reason = RefusalReason.NO_HITS
        elif not accepted:
            reason = RefusalReason.LOW_SCORE

        answer = REFUSAL_ANSWER
        usage = GenerationUsage()
        if reason is None:
            generated = await self._generator.generate(question, context=context)
            usage = generated.usage
            answer = generated.text.strip()
            if not answer or answer == REFUSAL_ANSWER:
                answer = REFUSAL_ANSWER
                reason = RefusalReason.GENERATOR_REFUSED

        refused = reason is not None
        citations = [] if refused else [source.citation for source in sources]
        used = set() if refused else {hit.chunk.chunk_id for hit in accepted}
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        log = QueryLog(
            trace_id=trace_id,
            question=question,
            answer=answer,
            refused=refused,
            refusal_reason=reason,
            hits=_query_hits(hits, used=used),
            filters=filters_effective,
            top_k=effective_top_k,
            min_score=min_score,
            latency_ms=latency_ms,
            generator=self._generator.kind,
            token_usage=usage,
            rewritten_question=outcome.rewritten_question,
            expanded_questions=outcome.expanded_questions,
        )
        self._query_logs.add(log)
        if cache_key is not None:
            self._cache.put(cache_key, log=log, citations=citations)
        logger.info(
            "qa_completed",
            query_id=log.query_id,
            question_chars=len(question),
            refused=refused,
            refusal_reason=reason.value if reason else None,
            top_k=effective_top_k,
            filters=log.filters,
            hits=len(hits),
            used_sources=len(citations),
            best_score=hits[0].score if hits else None,
            min_score=min_score,
            latency_ms=latency_ms,
            generator=self._generator.kind.value,
            total_tokens=usage.total_tokens,
            rewritten_question=outcome.rewritten_question,
            expanded_queries=len(outcome.expanded_questions),
            cache_hit=False,
        )
        return AnswerRunResult(log=log, citations=citations)

    def _answered_from_cache(
        self,
        entry: CacheEntry,
        *,
        question: str,
        trace_id: str,
        started: float,
    ) -> AnswerRunResult:
        """命中答档:复制成新的查询日志留痕,再测延迟,生成链路整段跳过。"""
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        log = entry.log.model_copy(
            deep=True,
            update={
                "query_id": f"qry_{uuid4().hex[:12]}",
                "trace_id": trace_id,
                # 留痕以本次请求为准:问题原文记调用方的,答档只贡献答案与证据
                "question": question,
                "latency_ms": latency_ms,
                "created_at": datetime.now(UTC),
            },
        )
        self._query_logs.add(log)
        citations = [citation.model_copy(deep=True) for citation in entry.citations]
        stats = self._cache.stats()
        logger.info(
            "qa_cache_hit",
            query_id=log.query_id,
            latency_ms=latency_ms,
            cached_answer_chars=len(log.answer),
            cache_hits=stats.hits,
            cache_misses=stats.misses,
            cache_hit_rate=stats.hit_rate,
        )
        return AnswerRunResult(log=log, citations=citations, cache_hit=True)

    def _cache_key(
        self,
        *,
        question: str,
        top_k: int,
        min_score: float,
        filters_effective: dict[str, str],
    ) -> str | None:
        """缓存键=None 表示缓存未启用;键覆盖归一化问题与全部过程口径。"""
        if self._cache is None:
            return None
        return self._cache.make_key(
            question=question,
            top_k=top_k,
            min_score=min_score,
            filters=filters_effective,
            generator=self._generator.kind.value,
        )

    def _sources_of(self, hits: list[ScoredChunk]) -> list[ContextSource]:
        """把命中转成编号来源;同一文档的文件名只查一次。"""
        records: dict[str, DocumentRecord | None] = {}
        sources: list[ContextSource] = []
        for index, hit in enumerate(hits, start=1):
            document_id = hit.chunk.document_id
            if document_id not in records:
                records[document_id] = self._documents.get(document_id)
            record = records[document_id]
            citation = Citation(
                index=index,
                chunk_id=hit.chunk.chunk_id,
                document_id=document_id,
                file_name=record.file_name if record else None,
                title=hit.chunk.title,
                page_number=hit.chunk.page_number,
                score=round(hit.score, 6),
            )
            sources.append(ContextSource(citation=citation, content=hit.chunk.content))
        return sources


def build_context(sources: list[ContextSource]) -> str:
    """编号资料文本:每条带文档 / 标题 / 页码,后接切片原文。"""
    blocks: list[str] = []
    for source in sources:
        citation = source.citation
        locator = [f"文档:{citation.file_name or citation.document_id}"]
        if citation.title:
            locator.append(f"标题:{citation.title}")
        if citation.page_number is not None:
            locator.append(f"页码:{citation.page_number}")
        blocks.append(
            f"[{citation.index}] {' | '.join(locator)}\n{source.content.strip()}"
        )
    return "\n\n".join(blocks)


def _query_hits(hits: list[ScoredChunk], *, used: set[str]) -> list[QueryHit]:
    """检索命中留档:保留原始排名与分数,并标记是否进入回答证据。"""
    return [
        QueryHit(
            rank=rank,
            chunk_id=hit.chunk.chunk_id,
            document_id=hit.chunk.document_id,
            title=hit.chunk.title,
            page_number=hit.chunk.page_number,
            score=round(hit.score, 6),
            used=hit.chunk.chunk_id in used,
        )
        for rank, hit in enumerate(hits, start=1)
    ]
