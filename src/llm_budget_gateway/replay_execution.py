"""Explicit, local-gateway production replay execution with measured evidence."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class ReplayRequest:
    """Bounded candidate replay request sent only to the local gateway."""

    request_id: str
    model: str
    messages: tuple[dict[str, str], ...]
    max_completion_tokens: int
    estimated_cost_usd: float


@dataclass(frozen=True)
class ReplayExecution:
    """Measured output from an actually executed candidate request."""

    model: str
    output: str
    tokens: int
    latency_ms: float
    estimated_cost_usd: float


class LocalReplayExecutor:
    """Execute explicit replays through the fixed local OpenAI-compatible gateway."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "http://127.0.0.1:8000",
        transport: httpx.AsyncBaseTransport | None = None,
        clock_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        """Configure a fixed loopback gateway and injectable test transport."""
        if base_url not in {"http://127.0.0.1:8000", "http://localhost:8000"}:
            raise ValueError("replay base URL must be the local gateway")
        self._api_key = api_key
        self._base_url = base_url
        self._transport = transport
        self._clock_ns = clock_ns

    async def execute(self, request: ReplayRequest) -> ReplayExecution:
        """Execute one bounded candidate request and return measured evidence."""
        if not self._api_key.strip():
            raise ValueError("replay API key is not configured")
        if not request.request_id.strip() or not request.model.strip():
            raise ValueError("request_id and model must be non-empty")
        if not request.messages:
            raise ValueError("messages must be non-empty")
        if not 1 <= request.max_completion_tokens <= 8192:
            raise ValueError("max_completion_tokens must be between 1 and 8192")
        if (
            not math.isfinite(request.estimated_cost_usd)
            or request.estimated_cost_usd < 0
        ):
            raise ValueError("estimated replay cost must be finite and non-negative")
        for message in request.messages:
            if (
                message.get("role") not in {"system", "user", "assistant"}
                or not str(message.get("content", "")).strip()
            ):
                raise ValueError(
                    "messages require a supported role and non-empty content"
                )
        started = self._clock_ns()
        async with httpx.AsyncClient(
            base_url=self._base_url, transport=self._transport, timeout=60
        ) as client:
            response = await client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": request.model,
                    "messages": list(request.messages),
                    "max_completion_tokens": request.max_completion_tokens,
                },
            )
        ended = self._clock_ns()
        if response.status_code >= 400:
            raise ValueError(f"provider replay failed with HTTP {response.status_code}")
        body = response.json()
        try:
            output = str(body["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("provider replay returned no assistant output") from exc
        return ReplayExecution(
            str(body.get("model", request.model)),
            output,
            int(body.get("usage", {}).get("total_tokens", 0)),
            (ended - started) / 1_000_000,
            request.estimated_cost_usd,
        )
