"""统一配置:环境变量驱动,密钥只走环境变量 / .env。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    @field_validator("siliconflow_api_key", mode="before")
    @classmethod
    def _blank_key_is_unset(cls, value: object) -> object:
        """空字符串等同"未配置",避免把空 key 当成有效凭据。"""
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @property
    def upstream_configured(self) -> bool:
        return bool(self.siliconflow_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
