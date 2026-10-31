"""在线问答答档缓存(T15):相同问题的答档短暂复用,降低延迟与上游消耗。

进程内 TTL + 容量上限;键覆盖"影响一次回答的一切过程口径"——归一化问题、
top_k、拒答阈值、检索前过滤与生成器种类。知识库内容变化(向量化 / 退位 /
删除)即整体清空,答档不落后于检索面。

LRU + TTL 的淘汰机制由 cachetools 的 TTLCache 承担(不自己造轮子);
键的构成口径、整体失效时机与命中统计是本项目的领域契约。
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import structlog
from cachetools import TTLCache

from app.domain.qa import Citation, QueryLog

logger = structlog.get_logger("qa_cache")

ClockFn = Callable[[], float]


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """一条答档快照:完成的查询日志 + 对外引用。"""

    log: QueryLog
    citations: tuple[Citation, ...]


@dataclass(frozen=True, slots=True)
class AnswerCacheStats:
    """命中率统计:随命中 / 未命中事件与 /health 一并下发。"""

    hits: int = 0
    misses: int = 0
    hit_rate: float | None = None


class AnswerCache:
    """归一化问题 → 答档的进程内缓存(LRU 容量上限 + TTL 过期)。"""

    def __init__(
        self,
        *,
        ttl_seconds: float,
        maxsize: int,
        clock: ClockFn = time.monotonic,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._maxsize = maxsize
        # TTLCache 负责 LRU 淘汰与 TTL 过期;timer 注入让时钟可离线控制
        self._entries: TTLCache[str, CacheEntry] = TTLCache(
            maxsize=maxsize, ttl=ttl_seconds, timer=clock
        )
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    @staticmethod
    def normalize_question(question: str) -> str:
        """同题归一化:空白折叠、大小写无关,不改变词语本身。"""
        return " ".join(question.split()).casefold()

    def make_key(
        self,
        *,
        question: str,
        top_k: int,
        min_score: float,
        filters: dict[str, str],
        generator: str,
    ) -> str:
        """缓存键:对答案有影响的过程口径全部入键,换来键宽有限(定长摘要)。"""
        canonical = json.dumps(
            {
                "question": self.normalize_question(question),
                "top_k": top_k,
                "min_score": min_score,
                "generator": generator,
                "filters": dict(sorted(filters.items())),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def get(self, key: str) -> CacheEntry | None:
        """命中返回条目(顺带刷新 LRU 位次),未命中 / 过期计入 miss。"""
        with self._lock:
            try:
                entry = self._entries[key]
            except KeyError:
                self._misses += 1
                return None
            self._hits += 1
            return entry

    def put(self, key: str, *, log: QueryLog, citations: list[Citation]) -> None:
        """写入答档:超出容量时冻结最旧条目。"""
        with self._lock:
            self._entries[key] = CacheEntry(log=log, citations=tuple(citations))

    def clear(self, *, reason: str) -> int:
        """知识库检索面变化时整体失效;返回被清掉的条目数便于日志留痕。"""
        with self._lock:
            evicted = len(self._entries)
            self._entries.clear()
        if evicted:
            logger.info("qa_cache_invalidated", reason=reason, evicted=evicted)
        return evicted

    def stats(self) -> AnswerCacheStats:
        """累计命中 / 未命中与命中率(暂无请求时 None)。"""
        with self._lock:
            total = self._hits + self._misses
            rate = round(self._hits / total, 4) if total else None
            return AnswerCacheStats(hits=self._hits, misses=self._misses, hit_rate=rate)
