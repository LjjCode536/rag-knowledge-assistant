"""评估编排:黄金数据集加载,以及经 HTTP 主缝驱动检索指标(教材第 44 章)。

评估器不绕过 API:上传 / 切片 / 向量化 / 检索都走用户实际使用的端点。
默认注入确定性向量替身,因而无密钥环境也能复现;真实上游模式只是换客户端。
指标口径在 :mod:`app.domain.evaluation` 唯一定义,本模块只负责编排与报告。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx
import yaml

from app.domain.chunks import ChunkingStrategy
from app.domain.evaluation import (
    CaseResult,
    EvaluationReport,
    ExpectedChunk,
    ExpectedChunkRef,
    GoldenCase,
    GoldenDataset,
    RetrievalMetrics,
    RetrievedChunk,
    hit_rate_at_k,
    mrr,
    ndcg_at_k,
    recall_at_k,
)
from app.domain.retrieval import ChunkFilter

# 成败判定与在线问答共用同一阈值口径;报告里会写明本次使用的值。
SCHEMA_VERSION = 1
DEFAULT_RUN_NAME = "T06 检索指标基线"
DEFAULT_EMBEDDER_LABEL = "deterministic-lexical-1024d"


class EvaluationDatasetError(Exception):
    """黄金数据集自身不合法:锚点找不到、命中多个切片等。"""


class EvaluationApiError(Exception):
    """评估过程调用 API 失败:把信封里的错误码与文案带出来,便于定位。"""


@dataclass(frozen=True, slots=True)
class EvaluationConfig:
    """一次评估的固定参数;写进报告,保证前后可对比。"""

    k: int = 3
    top_k: int | None = None
    min_score: float = 0.35
    chunk_strategy: ChunkingStrategy | None = None
    chunk_size: int | None = None
    chunk_overlap: int | None = None
    embedder: str = DEFAULT_EMBEDDER_LABEL
    mode: str = "in-process"
    command: str = "uv run python scripts/evaluate.py"
    run_name: str = DEFAULT_RUN_NAME


@dataclass(slots=True)
class _CorpusIndex:
    # 文档名 -> 该文档的切片;document_names 反向映射 document_id -> 文档名
    documents: dict[str, list[dict[str, Any]]]
    document_names: dict[str, str]
    chunk_strategy: str
    chunk_size: int
    chunk_overlap: int

    def document_of(self, document_id: str) -> str:
        return self.document_names.get(document_id, document_id)

    def chunks_of(self, corpus_name: str) -> list[dict[str, Any]]:
        chunks = self.documents.get(corpus_name)
        if chunks is None:
            raise EvaluationDatasetError(f"语料未完成索引:{corpus_name}")
        return chunks


def load_golden_dataset(path: Path) -> GoldenDataset:
    """读取并校验版本化 YAML;结构错误直接失败,不带病跑评估。"""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return GoldenDataset.model_validate(raw)


class EvaluationService:
    """把黄金数据集经 API 入库并逐条检索,产出可追溯的指标报告。"""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        dataset: GoldenDataset,
        dataset_dir: Path,
        config: EvaluationConfig,
    ) -> None:
        self._client = client
        self._dataset = dataset
        self._dataset_dir = dataset_dir
        self._config = config
        self._top_k = config.top_k if config.top_k is not None else config.k

    async def run(self) -> EvaluationReport:
        """完整跑一轮:语料入库 → 逐用例检索 → 汇总 → 报告。"""
        index = await self._index_corpus()
        results = [await self._evaluate_case(case, index) for case in self._dataset.cases]
        answerable = [result for result in results if not result.should_refuse]
        summary = _aggregate(answerable)
        categories = _aggregate_by_category(answerable)
        category_case_counts: dict[str, int] = {}
        for result in results:
            key = result.category.value
            category_case_counts[key] = category_case_counts.get(key, 0) + 1
        refusal_results = [result for result in results if result.should_refuse]
        permission_results = [result for result in results if result.permission_ok is not None]
        return EvaluationReport(
            schema_version=SCHEMA_VERSION,
            run_name=self._config.run_name,
            dataset_name=self._dataset.name,
            dataset_version=self._dataset.version,
            k=self._config.k,
            top_k=self._top_k,
            min_score=self._config.min_score,
            chunk_strategy=index.chunk_strategy,
            chunk_size=index.chunk_size,
            chunk_overlap=index.chunk_overlap,
            embedder=self._config.embedder,
            mode=self._config.mode,
            command=self._config.command,
            cases_total=len(results),
            summary=summary,
            categories=categories,
            category_case_counts=category_case_counts,
            refusal_accuracy=_mean_bool(
                [result.refusal_ok for result in refusal_results if result.refusal_ok is not None]
            ),
            permission_pass_rate=_mean_bool(
                [
                    result.permission_ok
                    for result in permission_results
                    if result.permission_ok is not None
                ]
            ),
            case_results=results,
        )

    async def _index_corpus(self) -> _CorpusIndex:
        """按语料清单上传、切片、向量化;缺一步都不进入评估。"""
        documents: dict[str, list[dict[str, Any]]] = {}
        document_names: dict[str, str] = {}
        chunk_strategy: str | None = None
        chunk_size: int | None = None
        chunk_overlap: int | None = None

        for corpus in self._dataset.corpus:
            path = self._dataset_dir / corpus.path
            if not path.is_file():
                raise EvaluationDatasetError(f"语料文件不存在:{path}")
            document = await self._post(
                "/api/v1/documents",
                files={"file": (path.name, path.read_bytes(), "text/markdown")},
                data={
                    "tenant": corpus.tenant,
                    "department": corpus.department or "",
                    "doc_type": corpus.doc_type or "",
                    "version": corpus.version,
                },
            )
            document_id = document["id"]
            chunk_request: dict[str, Any] = {}
            if self._config.chunk_strategy is not None:
                chunk_request["strategy"] = self._config.chunk_strategy.value
            if self._config.chunk_size is not None:
                chunk_request["chunk_size"] = self._config.chunk_size
            if self._config.chunk_overlap is not None:
                chunk_request["chunk_overlap"] = self._config.chunk_overlap
            chunk_run = await self._post(
                f"/api/v1/documents/{document_id}/chunks", json=chunk_request
            )
            await self._post(f"/api/v1/documents/{document_id}/embeddings")

            effective = _effective_chunking(chunk_run)
            if chunk_strategy is None:
                chunk_strategy, chunk_size, chunk_overlap = effective
            elif effective != (chunk_strategy, chunk_size, chunk_overlap):
                raise EvaluationDatasetError(
                    "语料文档使用了不一致的切分参数,指标不可横向比较"
                )

            documents[corpus.file_name] = list(chunk_run["chunks"])
            document_names[document_id] = corpus.file_name

        assert chunk_strategy is not None and chunk_size is not None
        assert chunk_overlap is not None
        return _CorpusIndex(
            documents=documents,
            document_names=document_names,
            chunk_strategy=chunk_strategy,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

    async def _evaluate_case(self, case: GoldenCase, index: _CorpusIndex) -> CaseResult:
        expected = [self._resolve_expected(case, item, index) for item in case.expected_chunks]
        hits = await self._search(case.question, case.filters)
        retrieved = _retrieved(hits, index)
        expected_ids = [item.chunk_id for item in expected]
        retrieved_ids = [item.chunk_id for item in retrieved]

        if case.should_refuse:
            recall = mrr_value = ndcg = hit_rate = None
            refusal_ok = not any(
                result_hit["score"] >= self._config.min_score for result_hit in hits
            )
        else:
            recall = recall_at_k(expected_ids, retrieved_ids, k=self._config.k)
            mrr_value = mrr(expected_ids, retrieved_ids, k=self._config.k)
            ndcg = ndcg_at_k(expected_ids, retrieved_ids, k=self._config.k)
            hit_rate = hit_rate_at_k(expected_ids, retrieved_ids, k=self._config.k)
            refusal_ok = None

        permission_ok: bool | None = None
        forbidden_best_rank: int | None = None
        if case.forbidden_documents:
            forbidden_ids = {
                chunk["chunk_id"]
                for document in case.forbidden_documents
                for chunk in index.chunks_of(document)
            }
            permission_ok = not any(item.chunk_id in forbidden_ids for item in retrieved)
            # 无过滤对照:证明禁读文档确实会被检索到,权限通过不是因为语料无关
            control_hits = await self._search(case.question, None)
            forbidden_best_rank = next(
                (
                    rank
                    for rank, hit in enumerate(control_hits, start=1)
                    if hit["chunk"]["chunk_id"] in forbidden_ids
                ),
                None,
            )

        return CaseResult(
            case_id=case.id,
            question=case.question,
            category=case.category,
            difficulty=case.difficulty,
            should_refuse=case.should_refuse,
            filters=case.filters.effective(),
            expected=expected,
            retrieved=retrieved,
            recall_at_k=_round(recall),
            mrr=_round(mrr_value),
            ndcg_at_k=_round(ndcg),
            hit_rate=_round(hit_rate),
            refusal_ok=refusal_ok,
            permission_ok=permission_ok,
            forbidden_best_rank=forbidden_best_rank,
            notes=case.notes,
        )

    def _resolve_expected(
        self, case: GoldenCase, expected: ExpectedChunk, index: _CorpusIndex
    ) -> ExpectedChunkRef:
        """把「文档 + 锚点」解析成唯一真实切片;不唯一即数据集缺陷。"""
        matches = [
            chunk
            for chunk in index.chunks_of(expected.document)
            if expected.anchor in chunk["content"]
        ]
        if len(matches) != 1:
            raise EvaluationDatasetError(
                f"用例 {case.id} 的预期锚点「{expected.anchor}」在 {expected.document} "
                f"命中 {len(matches)} 个切片,要求恰好 1 个"
            )
        chunk = matches[0]
        return ExpectedChunkRef(
            document=expected.document,
            anchor=expected.anchor,
            chunk_id=chunk["chunk_id"],
            index=chunk["index"],
            title=chunk.get("title"),
        )

    async def _search(
        self, question: str, filters: ChunkFilter | None
    ) -> list[dict[str, Any]]:
        effective = filters.effective() if filters is not None else {}
        data = await self._post(
            "/api/v1/search",
            json={
                "query": question,
                "top_k": self._top_k,
                "filters": effective or None,
            },
        )
        return list(data["hits"])

    async def _post(self, url: str, **kwargs: Any) -> dict[str, Any]:
        response = await self._client.post(url, **kwargs)
        try:
            payload = response.json()
        except ValueError:
            raise EvaluationApiError(
                f"POST {url} 返回非 JSON 响应(HTTP {response.status_code})"
            ) from None
        if response.status_code >= 400:
            detail = (
                f"{payload.get('code')} {payload.get('message')} "
                f"{payload.get('details') or ''}"
            ).strip()
            raise EvaluationApiError(
                f"POST {url} 失败(HTTP {response.status_code}):{detail}"
            )
        data = payload.get("data")
        if data is None:
            raise EvaluationApiError(f"POST {url} 成功响应缺少 data 字段")
        return data


def _effective_chunking(chunk_run: dict[str, Any]) -> tuple[str, int, int]:
    return (
        str(chunk_run["strategy"]),
        int(chunk_run["chunk_size"]),
        int(chunk_run["chunk_overlap"]),
    )


def _retrieved(hits: list[dict[str, Any]], index: _CorpusIndex) -> list[RetrievedChunk]:
    return [
        RetrievedChunk(
            rank=rank,
            chunk_id=hit["chunk"]["chunk_id"],
            document=index.document_of(hit["chunk"]["document_id"]),
            index=int(hit["chunk"]["index"]),
            title=hit["chunk"].get("title"),
            score=round(float(hit["score"]), 6),
        )
        for rank, hit in enumerate(hits, start=1)
    ]


def _aggregate(results: list[CaseResult]) -> RetrievalMetrics:
    return RetrievalMetrics(
        recall_at_k=_mean([result.recall_at_k for result in results]),
        mrr=_mean([result.mrr for result in results]),
        ndcg_at_k=_mean([result.ndcg_at_k for result in results]),
        hit_rate=_mean([result.hit_rate for result in results]),
        cases=len(results),
    )


def _aggregate_by_category(results: list[CaseResult]) -> dict[str, RetrievalMetrics]:
    grouped: dict[str, list[CaseResult]] = {}
    for result in results:
        grouped.setdefault(result.category.value, []).append(result)
    return {category: _aggregate(grouped[category]) for category in sorted(grouped)}


def _mean(values: list[float | None]) -> float:
    present = [value for value in values if value is not None]
    return _round(sum(present) / len(present)) if present else 0.0


def _mean_bool(values: list[bool]) -> float | None:
    return _round(sum(1 for value in values if value) / len(values)) if values else None


def _round(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def render_markdown(report: EvaluationReport) -> str:
    """人读报告:配置、摘要、分品类、逐用例与后续对比约定。"""
    lines: list[str] = [
        f"# {report.run_name}:{report.dataset_name} v{report.dataset_version}",
        "",
        "> 由 `scripts/evaluate.py` 生成。检索指标只依赖切片编号与排序,不依赖生成模型;",
        "> 默认注入确定性向量替身,可在无密钥环境(CI)复现。",
        "> 报告用「文档#index + 锚点」定位切片;随机 chunk_id 不写入报告,"
        "跨运行 diff 只反映指标变化。",
        "",
        "## 评估配置",
        "",
        "| 配置 | 值 |",
        "| --- | --- |",
        f"| 数据集 | `{report.dataset_name}` v{report.dataset_version} |",
        f"| 用例总数 | {report.cases_total} |",
        f"| 可回答 / 拒答用例 | {report.summary.cases} / "
        f"{sum(1 for result in report.case_results if result.should_refuse)} |",
        f"| 权限边界用例 | "
        f"{sum(1 for result in report.case_results if result.permission_ok is not None)} |",
        f"| K / top_k | {report.k} / {report.top_k} |",
        f"| 拒答阈值 min_score | {report.min_score} |",
        f"| 切分策略 / 尺寸 / 重叠 | {report.chunk_strategy} / "
        f"{report.chunk_size} / {report.chunk_overlap} |",
        f"| 向量来源 | `{report.embedder}` |",
        f"| 运行模式 | `{report.mode}` |",
        f"| 复现命令 | `{report.command}` |",
        "",
        "## 摘要",
        "",
        "| 指标 | 数值 | 口径 |",
        "| --- | --- | --- |",
        f"| Recall@{report.k} | {report.summary.recall_at_k:.4f} | 可回答用例均值 |",
        f"| MRR | {report.summary.mrr:.4f} | 可回答用例均值 |",
        f"| NDCG@{report.k} | {report.summary.ndcg_at_k:.4f} | 可回答用例均值 |",
        f"| HitRate@{report.k} | {report.summary.hit_rate:.4f} | 可回答用例均值 |",
        f"| 拒答正确率(检索口径) | {_format_optional(report.refusal_accuracy)} | "
        f"拒答用例均值 |",
        f"| 权限边界通过率 | {_format_optional(report.permission_pass_rate)} | 权限用例均值 |",
        "",
        f"共 {report.cases_total} 条用例;拒答用例不参与 Recall / MRR / NDCG 均值。",
        "拒答正确率在 T06 只按检索分数与 `qa_min_score` 判定;生成器主动拒答、"
        "忠实度与引用准确率留待 T09。",
        "",
        "## 分品类指标",
        "",
        f"| 品类 | 可回答 / 全部 | Recall@{report.k} | MRR | NDCG@{report.k} | "
        f"HitRate@{report.k} |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for category in sorted(set(report.categories) | set(report.category_case_counts)):
        metrics = report.categories.get(category)
        total = report.category_case_counts.get(category, metrics.cases if metrics else 0)
        if metrics is None:
            lines.append(f"| {category} | 0 / {total} | — | — | — | — |")
        else:
            lines.append(
                f"| {category} | {metrics.cases} / {total} | {metrics.recall_at_k:.4f} | "
                f"{metrics.mrr:.4f} | {metrics.ndcg_at_k:.4f} | {metrics.hit_rate:.4f} |"
            )
    lines.append("")
    lines.append(
        "`unknown` 等只有拒答用例的品类不参与检索均值,单独计入拒答正确率。"
    )

    failures = _failures(report)
    lines.extend(["", "## 未满分用例速览", ""])
    if failures:
        lines.extend(["| 用例 | 品类 | 现象 |", "| --- | --- | --- |"])
        for result, reason in failures:
            lines.append(f"| `{result.case_id}` | {result.category.value} | {reason} |")
    else:
        lines.append("本轮所有用例满分。")

    lines.extend(["", "## 逐用例结果", ""])
    for result in report.case_results:
        lines.extend(_render_case(report, result))

    lines.extend(
        [
            "## 与后续迭代对比的约定",
            "",
            "- 对比时必须固定数据集版本、`K`、切分参数与向量后端;任何一项变化都要在报告中注明。",
            "- 报告同时产出 Markdown(人读)与 JSON(CI / 门禁);指标字段名保持不变。",
            "- 后续迭代在 `docs/iterations/` 递增编号,并附「基线 vs 本次」差值表。",
            "- 无密钥环境默认使用 `deterministic-lexical-1024d`;真实上游模式需写明 embedder,"
            "不与替身基线混比。",
            "",
        ]
    )
    return "\n".join(lines)


def render_json(report: EvaluationReport) -> str:
    """机器可读报告:CI 门禁与后续对比直接消费同一份数字。"""
    return (
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    )


def _render_case(report: EvaluationReport, result: CaseResult) -> list[str]:
    lines = [
        f"### `{result.case_id}` · {result.category.value} · {result.difficulty.value}",
        "",
        f"- 问题:{result.question}",
    ]
    filters = ", ".join(f"{key}={value}" for key, value in sorted(result.filters.items()))
    if filters:
        lines.append(f"- 过滤:{filters}")
    if result.expected:
        for expected in result.expected:
            title = f" · {expected.title}" if expected.title else ""
            lines.append(
                f"- 预期:`{expected.document}`{title}「{expected.anchor}」"
                f" → `{expected.key}`"
            )
    else:
        lines.append("- 预期:无(应拒答)")
    if result.retrieved:
        lines.append("- 检索:")
        expected_ids = {item.chunk_id for item in result.expected}
        for hit in result.retrieved:
            mark = " ✅" if hit.chunk_id in expected_ids else ""
            title = f" · {hit.title}" if hit.title else ""
            lines.append(
                f"  {hit.rank}. `{hit.key}`{title} · score={hit.score:.6f}{mark}"
            )
    else:
        lines.append("- 检索:无命中")

    if result.should_refuse:
        best = result.retrieved[0].score if result.retrieved else 0.0
        if result.refusal_ok:
            lines.append(
                f"- 指标:拒答正确(最高分 {best:.6f} < 阈值 {report.min_score})"
            )
        else:
            lines.append(
                f"- 指标:拒答失败(最高分 {best:.6f} ≥ 阈值 {report.min_score})"
            )
    else:
        lines.append(
            f"- 指标:Recall@{report.k}={result.recall_at_k:.4f} · "
            f"MRR={result.mrr:.4f} · NDCG@{report.k}={result.ndcg_at_k:.4f} · "
            f"HitRate@{report.k}={result.hit_rate:.4f}"
        )
    if result.permission_ok is not None:
        control = (
            f"无过滤对照中禁读文档最高排名 {result.forbidden_best_rank}"
            if result.forbidden_best_rank is not None
            else "无过滤对照中禁读文档未进入 top-k"
        )
        verdict = "通过" if result.permission_ok else "失败:返回了禁读文档"
        lines.append(f"- 权限边界:{verdict};{control}")
    notes = f"- 备注:{result.notes}" if result.notes else ""
    if notes:
        lines.append(notes)
    lines.append(f"- 结论:{_verdict(result)}")
    lines.append("")
    return lines


class _Outcome(StrEnum):
    """单条用例的结论码:结论文案、失败清单与报告渲染共用一个判定。"""

    REFUSAL_OK = "refusal_ok"
    REFUSAL_FAIL = "refusal_fail"
    PERMISSION_FAIL = "permission_fail"
    FULL_HIT = "full_hit"
    PARTIAL_HIT = "partial_hit"
    MISS = "miss"


def _outcome(result: CaseResult) -> _Outcome:
    if result.should_refuse:
        return _Outcome.REFUSAL_OK if result.refusal_ok else _Outcome.REFUSAL_FAIL
    if result.permission_ok is False:
        return _Outcome.PERMISSION_FAIL
    if result.recall_at_k == 1.0 and result.mrr == 1.0:
        return _Outcome.FULL_HIT
    if result.recall_at_k and result.recall_at_k > 0:
        return _Outcome.PARTIAL_HIT
    return _Outcome.MISS


_VERDICTS = {
    _Outcome.REFUSAL_OK: "✅ 正确拒答",
    _Outcome.REFUSAL_FAIL: "❌ 本应拒答但命中了高相关切片",
    _Outcome.PERMISSION_FAIL: "❌ 权限边界被跨越",
    _Outcome.FULL_HIT: "✅ 完全命中",
    _Outcome.PARTIAL_HIT: "△ 部分命中",
    _Outcome.MISS: "❌ 未命中",
}


def _verdict(result: CaseResult) -> str:
    return _VERDICTS[_outcome(result)]


def _failures(report: EvaluationReport) -> list[tuple[CaseResult, str]]:
    failures: list[tuple[CaseResult, str]] = []
    for result in report.case_results:
        outcome = _outcome(result)
        if outcome is _Outcome.REFUSAL_OK or outcome is _Outcome.FULL_HIT:
            continue
        if outcome is _Outcome.REFUSAL_FAIL:
            reason = "应拒答但出现高分命中"
        elif outcome is _Outcome.PERMISSION_FAIL:
            reason = "返回了禁读文档"
        elif outcome is _Outcome.MISS:
            reason = f"Recall@{report.k}={result.recall_at_k:.4f}"
        else:
            reason = f"命中但排序次优(MRR={result.mrr:.4f})"
        failures.append((result, reason))
    return failures


def _format_optional(value: float | None) -> str:
    return "—" if value is None else f"{value:.4f}"


__all__ = [
    "EvaluationApiError",
    "EvaluationConfig",
    "EvaluationDatasetError",
    "EvaluationService",
    "load_golden_dataset",
    "render_json",
    "render_markdown",
]
