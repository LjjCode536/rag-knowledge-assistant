"""统一配置:环境变量驱动,密钥只走环境变量 / .env。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.domain.chunks import ChunkingStrategy


class Settings(BaseSettings):
    """应用配置。

    所有字段均可用环境变量覆盖(大小写不敏感),`.env` 仅作本地开发便利,
    永不入库。未配置密钥时服务仍可启动,上游调用返回 UPSTREAM_NOT_CONFIGURED。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ===== 应用 =====
    app_name: str = "rag-knowledge-assistant"
    app_version: str = "0.1.0"
    environment: Literal["local", "test", "production"] = "local"

    # ===== 日志 =====
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    # ===== 硅基流动(OpenAI 兼容)=====
    siliconflow_api_key: str | None = None
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"

    # ===== 模型(只出现在配置里)=====
    llm_model: str = "Qwen/Qwen3-8B"
    embedding_model: str = "BAAI/bge-m3"
    rerank_model: str = "BAAI/bge-reranker-v2-m3"

    # ===== 上游调用策略 =====
    llm_timeout_seconds: float = Field(default=30.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_retry_backoff_seconds: float = Field(default=0.5, ge=0)

    # ===== 文档入库 =====
    # 上传文件落盘目录(运行时产物,已被 gitignore;不落库)
    upload_dir: Path = Path("data/uploaded_docs")
    # 离线入库形态(见 ADR-0012):True = 上传只登记档案与落盘,解析 → 切片 →
    # 向量化转后台任务(FastAPI BackgroundTasks 起步),上传立即返回任务句柄
    # (状态机位置可查);False = 请求内同步完成解析(此前同步形态,
    # 切分参数排查 / 评估器固定口径用)。两条形态共用同一服务,行为一致。
    ingestion_async: bool = True

    # ===== 文档切片 =====
    # 默认切分策略;每次切片请求可覆盖请求端参数,即时生效
    chunk_strategy: ChunkingStrategy = ChunkingStrategy.TABLE_AWARE
    chunk_size: int = Field(default=500, ge=1)
    chunk_overlap: int = Field(default=50, ge=0)
    # 质量告警阈值:短于该值的切片触发 CHUNK_TOO_SHORT;超过 chunk_size 触发 CHUNK_TOO_LONG
    chunk_min_chars: int = Field(default=20, ge=1)

    # ===== 在线问答 =====
    # 默认 top-k 与拒答阈值;最高分低于阈值即拒答(检索低分兜底)
    qa_top_k: int = Field(default=3, ge=1, le=50)
    qa_min_score: float = Field(default=0.35, ge=-1.0, le=1.0)
    # 回答生成器种类:rule = 规则版基线(不触网);llm = 真实 LLM 有据生成
    # (默认 Qwen/Qwen3-8B,llm_model 可换;需 SILICONFLOW_API_KEY,
    # 上游超时 / 限流统一转为 UPSTREAM_* 错误码)
    qa_generator: Literal["rule", "llm"] = "llm"

    # ===== 向量库后端(M3 三级存储:memory → chroma → pgvector)=====
    # memory = 进程内存基线(默认,重启即失忆,测试 / CI 指标口径);
    # chroma = Chroma 持久向量库(教材第 42 章),行为与基线一致,
    # 仅余同分顺序与 float32 精度两类可解释差异;
    # pgvector = PostgreSQL 16 + pgvector 生产持久化(教材第 42 / 45 章生产表),
    # 六张表(documents / chunks / embeddings / query_logs / feedbacks /
    # evaluation_runs)全部落地 psycopg 连接串指向的数据库,仓储与向量库
    # 同一切换;必须配置 database_url,缺了在配置期直接拒绝,绝不静默回退。
    vector_store: Literal["memory", "chroma", "pgvector"] = "memory"
    # chroma 数据目录:一个目录一份标签,重启不重嵌(运行时产物,已被 gitignore)
    chroma_path: Path = Path("data/chroma")

    # ===== 生产持久化(pgvector 模式)=====
    # PostgreSQL 连接串,与 compose 的 DATABASE_URL 同一形态:
    # postgresql://rag:<密码>@<主机>:5432/rag
    database_url: str | None = None

    # ===== 高级检索(教材第 43 章 RetrievalConfig 的逐项开关模式)=====
    # 默认组合以 T07 消融数据为准(docs/iterations/0002-t07-advanced-retrieval):
    # 重排 + 查询改写 Recall@3 0.7917 → 1.0000 / MRR 0.7500 → 1.0000,且拒答
    # 正确率保持 1.0。MMR(-0.042 Recall)与多查询扩展(与改写打平但多耗上游)
    # 不进默认;混合检索单独开启会破坏拒答阈值语义(消融 B 组合),列为需重排
    # 校准分数的增强项。
    use_query_rewrite: bool = True
    use_multi_query: bool = False
    use_hybrid: bool = False
    use_mmr: bool = False
    use_rerank: bool = True
    # 融合权重:同一切片在两通道归一分上加权;两权重不可同时为 0
    retrieval_vector_weight: float = Field(default=0.7, ge=0, le=1)
    retrieval_keyword_weight: float = Field(default=0.3, ge=0, le=1)
    # 候选池:重排 / MMR 需要远多于 top_k 的候选(先召回合成再精选)
    retrieval_candidate_k: int = Field(default=30, ge=1, le=200)
    # 多查询扩展生成的变体查询数量(不含原查询)
    retrieval_multi_query_count: int = Field(default=3, ge=1, le=10)
    # MMR 相关性 / 多样性权衡(1 = 纯相关性,0 = 纯多样性)
    retrieval_mmr_lambda: float = Field(default=0.7, ge=0, le=1)

    @field_validator("siliconflow_api_key", mode="before")
    @classmethod
    def _blank_key_is_unset(cls, value: object) -> object:
        """空字符串等同"未配置",避免把空 key 当成有效凭据。"""
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("retrieval_keyword_weight", mode="after")
    @classmethod
    def _fusion_weights_not_both_zero(cls, value: float, info) -> float:
        """双通道权重同时为 0 时融合无意义;在配置期直接拒绝。"""
        if info.field_name == "retrieval_keyword_weight":
            vector_weight = info.data.get("retrieval_vector_weight")
            if vector_weight is not None and vector_weight == 0 and value == 0:
                raise ValueError(
                    "retrieval_vector_weight 与 retrieval_keyword_weight 不可同时为 0"
                )
        return value

    @model_validator(mode="after")
    def _pgvector_requires_database_url(self) -> Settings:
        """pgvector 模式必须显式给出连接串:生产持久化不得静默降级为内存态。"""
        if self.vector_store == "pgvector" and not (self.database_url or "").strip():
            raise ValueError(
                "vector_store=pgvector 需要配置 database_url"
                "(postgresql://<用户>:<密码>@<主机>:5432/<库>)"
            )
        return self

    @property
    def upstream_configured(self) -> bool:
        return bool(self.siliconflow_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
