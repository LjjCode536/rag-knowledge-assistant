"""structlog 结构化日志配置。"""

from __future__ import annotations

import contextlib
import logging
import sys

import structlog

from app.core.config import Settings


def _ensure_utf8_stdout() -> None:
    """Windows 控制台默认 GBK,中文日志会变成 "?";尽量切到 UTF-8。"""
    stream = sys.stdout
    reconfigure = getattr(stream, "reconfigure", None)
    encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
    if reconfigure is not None and encoding != "utf8":
        with contextlib.suppress(ValueError, OSError):
            reconfigure(encoding="utf-8")


def configure_logging(settings: Settings) -> None:
    """configure structlog + stdlib logging;JSON 供生产/CI,console 供本地。"""
    _ensure_utf8_stdout()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
        force=True,
    )

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if settings.log_format == "json":
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer(
            ensure_ascii=False,
        )
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=False)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        # 不绑定具体 stdout:始终写向当前 sys.stdout(测试捕获、容器日志都正确)
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )
