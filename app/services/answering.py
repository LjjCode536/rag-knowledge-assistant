"""在线问答编排:检索 → 低分兜底 → 上下文构建 → 有据回答 → 查询日志。

主链路只依赖领域契约与窄接缝:检索服务、生成器、文档 / 查询日志仓储。
资料不足时统一拒答,绝不把低分命中包装成答案;每次问答(含拒答)都留日志。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import structlog

from app.core.config import Settings
from app.domain.documents import DocumentRecord
from app.domain.qa import (
    REFUSAL_ANSWER,
    AnswerContext,
    Citation,
    ContextSource,
    QueryHit,
    QueryLog,
    RefusalReason,
)
from app.domain.repositories import DocumentRepository, QueryLogRepository
from app.domain.retrieval import ChunkFilter, ScoredChunk
from app.services.generation import AnswerGenerator
from app.services.retrieval import RetrievalService

logger = structlog.get_logger("answering")


@dataclass(slots=True)
class AnswerRunResult:
    """一次问答的结果:查询日志 + 对外引用。"""

    log: QueryLog
    citations: list[Citation]


class AnswerService:
    """把用户问题变成有据回答,并保证每次问答都有可追溯日志。"""

    def __init__(
        self,
        *,
        retrieval_service: RetrievalService,
        document_repository: DocumentRepository,
        query_log_repository: QueryLogRepository,
        generator: AnswerGenerator,
        settings: Settings,
    ) -> None:
        self._retrieval = retrieval_service
        self._documents = document_repository
        self._query_logs = query_log_repository
        self._generator = generator
        self._settings = settings

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
        started = time.perf_counter()

        hits = await self._retrieval.search(
            question, top_k=effective_top_k, filters=filters
        )
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
        if reason is None:
            answer = (await self._generator.generate(question, context=context)).strip()
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
            filters=(filters or ChunkFilter()).effective(),
            top_k=effective_top_k,
            min_score=min_score,
            latency_ms=latency_ms,
        )
        self._query_logs.add(log)
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
        )
        return AnswerRunResult(log=log, citations=citations)

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
