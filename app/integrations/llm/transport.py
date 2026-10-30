"""上游 HTTP 传输层:可注入的窄接口,常规测试用假传输层替身。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx


@dataclass(frozen=True, slots=True)
class TransportResponse:
    """与 httpx 解耦的响应快照。

    ``headers`` 供上游指令类头部(如 429 的 Retry-After)透传给客户端策略,
    按需填充;测试替身与旧调用可继续省略。
    """

    status_code: int
    body: dict[str, Any] | None
    headers: Mapping[str, str] | None = None


class UpstreamTransport(Protocol):
    async def post(
        self,
        url: str,
        *,
        json: Mapping[str, Any],
        headers: Mapping[str, str],
        timeout: float,
    ) -> TransportResponse: ...


def _decode_body(response: httpx.Response) -> dict[str, Any] | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


class HttpxTransport:
    """真实传输层。httpx 的异常向上抛出,由客户端统一映射为 AppError。

    AsyncClient 懒创建:健康检查等不触网的路径不为 SSL 上下文付启动成本。
    """

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._owns_client = client is None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient()
        return self._client

    async def post(
        self,
        url: str,
        *,
        json: Mapping[str, Any],
        headers: Mapping[str, str],
        timeout: float,
    ) -> TransportResponse:
        response = await self._get_client().post(
            url,
            json=json,
            headers=headers,
            timeout=timeout,
        )
        return TransportResponse(
            status_code=response.status_code,
            body=_decode_body(response),
            headers=dict(response.headers),
        )

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
