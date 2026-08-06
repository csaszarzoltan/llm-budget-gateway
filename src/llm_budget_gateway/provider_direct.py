"""Direct OpenAI-compatible provider transport (no litellm).

Resolves a requested model to a configured provider endpoint (base URL +
API key from the gateway environment) and forwards chat-completion,
legacy-completion and embedding bodies as plain HTTP calls. This is the
replacement for the previous litellm-based ``forward`` path: the gateway
owns provider connectivity itself, so no third-party SDK is required and
every provider the gateway is configured with is called directly.

Security model (unchanged from the litellm era):

- Client bodies are allow-listed by the caller; provider credentials and
  endpoint overrides never come from the client body.
- API keys are read from the gateway process environment at request time
  (never logged, never returned through the product API).
- Only HTTP(S) base URLs are accepted.

Provider registry shape (``GATEWAY_PROVIDER_REGISTRY`` JSON)::

    {
      "opencode-zen": {
        "base_url": "https://opencode.ai/zen/v1",
        "api_key_env": "OPENCODE_ZEN_API_KEY",
        "auth": "bearer",            // bearer | x-api-key | query
        "models": ["mimo-v2.5-free", "mimo-v2.5"]
      },
      ...
    }

``auth`` selects how the API key is presented:

- ``bearer`` -> ``Authorization: Bearer <key>`` (default)
- ``x-api-key`` -> ``x-api-key: <key>`` (Anthropic-style)
- ``query`` -> ``?key=<key>`` on every request URL (Gemini-style)

A model resolves to the first provider whose ``models`` list contains it
(exact match). Unknown models raise ``UnknownModelError`` (the proxy maps
that to HTTP 404).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

#: Client body fields allowed through to the upstream provider. Everything
#: else is dropped — provider credentials and endpoint overrides must come
#: from gateway config/env only, never from the client body (SSRF +
#: cost-bypass prevention).
_FORWARD_ALLOWLIST = frozenset(
    {
        "model",
        "messages",
        "prompt",
        "input",
        "stream",
        "stream_options",
        "temperature",
        "top_p",
        "n",
        "max_tokens",
        "max_completion_tokens",
        "stop",
        "presence_penalty",
        "frequency_penalty",
        "logit_bias",
        "user",
        "seed",
        "tools",
        "tool_choice",
        "response_format",
        "functions",
        "function_call",
        "suffix",
        "echo",
        "best_of",
        "logprobs",
        "top_logprobs",
        "encoding_format",
        "dimensions",
        "quality",
        "modalities",
        "audio",
        "parallel_tool_calls",
    }
)


class ProviderConfigError(Exception):
    """Raised when the provider registry configuration is invalid."""


class UnknownModelError(Exception):
    """Raised when a requested model is not configured on any provider."""


class UpstreamProviderError(Exception):
    """Raised when the upstream provider returns a non-2xx response.

    Carries the provider status code, the redacted status message and the
    raw response body (truncated) so callers can surface the real reason
    (e.g. "context length exceeded", "missing field role") instead of a
    generic 502 — the body is provider-owned error detail, not a secret.
    """

    def __init__(
        self,
        status_code: int,
        message: str,
        body: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = str(body)[:2000]


@dataclass(frozen=True)
class ProviderEndpoint:
    """One configured provider connection."""

    name: str
    base_url: str
    api_key_env: str
    auth: str = "bearer"
    models: tuple[str, ...] = field(default_factory=tuple)
    api_key_value: str | None = None  # direct key (vault), bypasses env
    user_agent: str | None = None  # client-emulation User-Agent for upstream
    extra_body: dict[str, Any] | None = None  # provider-level body merge

    def api_key(self) -> str:
        """Read the API key from the vault value or the environment."""
        if self.api_key_value:
            return self.api_key_value
        value = os.environ.get(self.api_key_env, "")
        if not value:
            raise ProviderConfigError(
                f"provider '{self.name}': env var {self.api_key_env} is not set"
            )
        return value

    def headers(self) -> dict[str, str]:
        """Auth headers for this endpoint."""
        key = self.api_key()
        if self.auth == "x-api-key":
            headers = {"x-api-key": key}
        elif self.auth == "query":
            headers = {}
        else:
            headers = {"Authorization": f"Bearer {key}"}
        # Client emulation: some gateways (e.g. opencode.ai/zen) serve their
        # own CLI with a larger context window than generic httpx clients.
        # Setting the upstream-expected User-Agent unlocks the same limits.
        if self.user_agent:
            headers["User-Agent"] = self.user_agent
        return headers

    def url(self, path: str) -> str:
        """Absolute URL for ``path`` (e.g. ``/chat/completions``)."""
        base = self.base_url.rstrip("/")
        return f"{base}{path}"


class DirectProviderClient:
    """Resolves models to configured providers and forwards HTTP calls.

    One async httpx client per instance (shared connection pool); callers
    are expected to close it via ``aclose()`` when done.
    """

    def __init__(
        self,
        registry: dict[str, dict[str, Any]] | None = None,
        *,
        timeout: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._registry: dict[str, ProviderEndpoint] = {}
        self._timeout = timeout
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None
        self._model_index: dict[str, ProviderEndpoint] = {}
        self._load_registry(registry or {})

    def _load_registry(self, registry: dict[str, dict[str, Any]]) -> None:
        """Build the endpoint map and the model -> endpoint index."""
        for name, raw in registry.items():
            if not isinstance(raw, dict):
                raise ProviderConfigError(
                    f"provider '{name}': registry entry must be an object"
                )
            base_url = str(raw.get("base_url", "")).rstrip("/")
            if not base_url.startswith(("https://", "http://")):
                raise ProviderConfigError(
                    f"provider '{name}': base_url must be HTTP(S), got {base_url!r}"
                )
            api_key_env = str(raw.get("api_key_env", "")).strip()
            if not api_key_env:
                raise ProviderConfigError(
                    f"provider '{name}': api_key_env is required"
                )
            auth = str(raw.get("auth", "bearer"))
            if auth not in {"bearer", "x-api-key", "query"}:
                raise ProviderConfigError(
                    f"provider '{name}': unsupported auth type {auth!r}"
                )
            models_raw = raw.get("models", [])
            if not isinstance(models_raw, list):
                raise ProviderConfigError(
                    f"provider '{name}': models must be a list"
                )
            models = tuple(str(m) for m in models_raw)
            endpoint = ProviderEndpoint(
                name=name,
                base_url=base_url,
                api_key_env=api_key_env,
                auth=auth,
                models=models,
                api_key_value=raw.get("api_key") or None,
                user_agent=raw.get("user_agent") or None,
                extra_body=raw.get("extra_body") or None,
            )
            self._registry[name] = endpoint
            for model in models:
                # Flat name: first provider in registry order wins. Duplicate
                # flat names are common across catalogs (e.g. mimo-v2.5 on
                # opencode-go and xiaomi); the provider-qualified alias below
                # is the disambiguation mechanism.
                if model not in self._model_index:
                    self._model_index[model] = endpoint
                # Provider-qualified alias ``@slug/model`` lets a route pin a
                # model to one provider even when the flat name exists on
                # several (e.g. mimo-v2.5 on opencode-go and xiaomi).
                self._model_index[f"@{name}/{model}"] = endpoint

    @classmethod
    def from_env(
        cls,
        env: dict[str, str] | None = None,
        *,
        timeout: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> "DirectProviderClient":
        """Build a client from ``GATEWAY_PROVIDER_REGISTRY`` in the env.

        ``env`` defaults to ``os.environ``; a dict can be passed in tests.
        An empty/missing registry is valid (no providers configured).
        """
        source = env if env is not None else os.environ
        raw_json = source.get("GATEWAY_PROVIDER_REGISTRY", "")
        registry: dict[str, dict[str, Any]] = {}
        if raw_json.strip():
            try:
                parsed = json.loads(raw_json)
            except json.JSONDecodeError as exc:
                raise ProviderConfigError(
                    f"GATEWAY_PROVIDER_REGISTRY is not valid JSON: {exc}"
                ) from exc
            if not isinstance(parsed, dict):
                raise ProviderConfigError(
                    "GATEWAY_PROVIDER_REGISTRY must be a JSON object"
                )
            registry = parsed
        return cls(registry, timeout=timeout, client=client)

    def resolve(self, model: str) -> ProviderEndpoint:
        """Return the endpoint configured to serve ``model``."""
        endpoint = self._model_index.get(model)
        if endpoint is None:
            raise UnknownModelError(f"unknown model: {model}")
        return endpoint

    @property
    def models(self) -> list[str]:
        """Every model name configured across all providers."""
        return sorted(self._model_index)

    @property
    def registry(self) -> dict[str, ProviderEndpoint]:
        return dict(self._registry)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _request_url(self, endpoint: ProviderEndpoint, kind: str) -> str:
        """URL for a request kind (``chat``, ``completion``, ``embedding``)."""
        path = {
            "chat": "/chat/completions",
            "completion": "/completions",
            "embedding": "/embeddings",
        }[kind]
        url = endpoint.url(path)
        if endpoint.auth == "query":
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}key={endpoint.api_key()}"
        return url

    async def forward(
        self,
        model: str,
        body: dict[str, Any],
        *,
        kind: str = "chat",
    ) -> tuple[int, dict[str, Any], str]:
        """Forward an allow-listed body to the provider for ``model``.

        Returns ``(status_code, response_json, served_model)``. Raises
        ``UnknownModelError`` for unconfigured models and
        ``UpstreamProviderError`` for non-2xx upstream responses (the proxy
        maps that to HTTP 502). Streaming bodies are NOT handled here — use
        ``forward_stream`` for ``stream: true`` requests.
        """
        endpoint = self.resolve(model)
        url = self._request_url(endpoint, kind)
        payload = {k: v for k, v in body.items() if k in _FORWARD_ALLOWLIST}
        # Provider-qualified aliases (@slug/model) select the endpoint, but
        # the upstream always receives the bare model name.
        payload["model"] = model.split("/", 1)[1] if model.startswith("@") else model
        # Provider-level extra body (e.g. DeepInfra "flex": true) — config is
        # authoritative over anything the client sent.
        if endpoint.extra_body:
            payload.update(endpoint.extra_body)
        try:
            response = await self._client.post(
                url, json=payload, headers=endpoint.headers()
            )
        except httpx.TimeoutException as exc:
            raise UpstreamProviderError(502, f"upstream provider timed out: {endpoint.name}") from exc
        except httpx.HTTPError as exc:
            raise UpstreamProviderError(502, f"upstream provider error: {endpoint.name}") from exc
        if response.status_code >= 400:
            raise UpstreamProviderError(
                response.status_code,
                f"upstream provider error: {endpoint.name} (HTTP {response.status_code})",
                body=response.text[:2000],
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise UpstreamProviderError(
                502, f"upstream provider returned invalid JSON: {endpoint.name}"
            ) from exc
        served = data.get("model") if isinstance(data, dict) else None
        return response.status_code, data, served or model

    async def forward_stream(
        self,
        model: str,
        body: dict[str, Any],
        *,
        kind: str = "chat",
    ) -> tuple[int, list[dict[str, Any]], str]:
        """Forward a streaming request, returning drained SSE chunks.

        The upstream SSE stream is fully drained (bounded by the client
        timeout) so cost accounting stays exact; each ``data:`` JSON payload
        becomes one dict in the returned list. Returns
        ``(status_code, chunks, served_model)``.
        """
        endpoint = self.resolve(model)
        url = self._request_url(endpoint, kind)
        payload = {k: v for k, v in body.items() if k in _FORWARD_ALLOWLIST}
        # Provider-qualified aliases (@slug/model) select the endpoint, but
        # the upstream always receives the bare model name.
        payload["model"] = model.split("/", 1)[1] if model.startswith("@") else model
        payload["stream"] = True
        # Provider-level extra body (e.g. DeepInfra "flex": true) — config is
        # authoritative over anything the client sent.
        if endpoint.extra_body:
            payload.update(endpoint.extra_body)
        chunks: list[dict[str, Any]] = []
        served: str = model
        try:
            async with self._client.stream(
                "POST", url, json=payload, headers=endpoint.headers()
            ) as response:
                if response.status_code >= 400:
                    body_text = ""
                    try:
                        body_text = (await response.aread()).decode(
                            "utf-8", errors="replace"
                        )[:2000]
                    except Exception:
                        body_text = ""
                    raise UpstreamProviderError(
                        response.status_code,
                        f"upstream provider error: {endpoint.name} (HTTP {response.status_code})",
                        body=body_text,
                    )
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(chunk, dict):
                        chunks.append(chunk)
                        if chunk.get("model"):
                            served = chunk["model"]
        except httpx.TimeoutException as exc:
            raise UpstreamProviderError(502, f"upstream provider timed out: {endpoint.name}") from exc
        except httpx.HTTPError as exc:
            raise UpstreamProviderError(502, f"upstream provider error: {endpoint.name}") from exc
        return response.status_code, chunks, served
