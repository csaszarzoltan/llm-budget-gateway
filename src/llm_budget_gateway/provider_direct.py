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
import sqlite3
import threading
import time
from collections.abc import AsyncIterator
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


def _responses_to_chat(data: dict[str, Any], fallback_model: str) -> dict[str, Any]:
    """Map a Codex /Responses object onto chat-completions JSON.

    Text comes from output items of type ``message`` (content parts of type
    ``output_text``); usage maps input/output/total → prompt/completion/
    total tokens; finish_reason mirrors ``status``.
    """
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for item in data.get("output", []) or []:
        itype = item.get("type")
        if itype == "message":
            for part in item.get("content", []) or []:
                if part.get("type") == "output_text" and part.get("text"):
                    text_parts.append(str(part["text"]))
        elif itype == "function_call":
            # Codex / Responses function_call → chat tool_calls
            tool_calls.append(
                {
                    "id": str(item.get("call_id") or item.get("id") or f"call_{len(tool_calls)}"),
                    "type": "function",
                    "function": {
                        "name": str(item.get("name") or ""),
                        "arguments": str(item.get("arguments") or "{}"),
                    },
                }
            )
        elif itype == "tool_call":
            tool_calls.append(
                {
                    "id": str(item.get("call_id") or item.get("id") or f"call_{len(tool_calls)}"),
                    "type": "function",
                    "function": {
                        "name": str(item.get("name") or ""),
                        "arguments": str(item.get("arguments") or "{}"),
                    },
                }
            )
    usage_raw = data.get("usage") or {}
    status = data.get("status", "completed")
    # If we have tool calls, finish is tool_calls, else map status
    if tool_calls:
        finish = "tool_calls"
    else:
        finish = "stop" if status == "completed" else ("length" if status == "incomplete" else status)
    msg: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts)}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {
        "id": data.get("id", ""),
        "object": "chat.completion",
        "created": data.get("created_at") or 0,
        "model": data.get("model") or fallback_model,
        "choices": [
            {
                "index": 0,
                "message": msg,
                "finish_reason": finish,
            }
        ],
        "usage": {
            "prompt_tokens": int(usage_raw.get("input_tokens", 0) or 0),
            "completion_tokens": int(usage_raw.get("output_tokens", 0) or 0),
            "total_tokens": int(
                usage_raw.get("total_tokens", 0)
                or (usage_raw.get("input_tokens", 0) or 0)
                + (usage_raw.get("output_tokens", 0) or 0)
            ),
        },
        "_responses_status": status,
    }


def _chat_tools_to_responses(tools: Any) -> list[dict[str, Any]]:
    """Translate chat-completions tools to Responses API shape."""
    out: list[dict[str, Any]] = []
    if not isinstance(tools, list):
        return out
    for t in tools:
        if not isinstance(t, dict):
            continue
        if t.get("type") == "function" and isinstance(t.get("function"), dict):
            fn = t["function"]
            out.append(
                {
                    "type": "function",
                    "name": str(fn.get("name") or ""),
                    "description": str(fn.get("description") or ""),
                    "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
                }
            )
        elif "name" in t:
            # Already responses shape
            out.append(t)
    return out


def _chat_tool_choice_to_responses(choice: Any) -> Any:
    """Translate chat tool_choice to Responses shape."""
    if choice is None:
        return None
    if isinstance(choice, str):
        return choice
    if not isinstance(choice, dict):
        return choice
    if choice.get("type") == "function" and isinstance(choice.get("function"), dict):
        return {"type": "function", "name": str(choice["function"].get("name") or "")}
    return choice


_REASONING_ECHO_MODEL_SUBS = ("deepseek", "kimi", "mimo")


def _model_needs_reasoning_echo(model: str) -> bool:
    """True when the resolved model belongs to a reasoning-echo family.

    DeepSeek v4 thinking, Kimi / Moonshot thinking and Xiaomi MiMo thinking
    all reject replays of assistant turns that omit ``reasoning_content``
    (HTTP 400: "The `reasoning_content` in the thinking mode must be passed
    back to the API"). Clients that talk to a gateway route (e.g. Hermes
    using a route name like ``hermes-default`` instead of the serving model
    name) strip the field because they cannot see the model behind the
    route — so the gateway re-pads assistant turns with a single space,
    the same convention the Hermes agent uses for this class of providers.
    """
    lowered = (model or "").lower()
    return any(sub in lowered for sub in _REASONING_ECHO_MODEL_SUBS)


def _pad_reasoning_content(messages: Any) -> None:
    """Mutate ``messages`` in place: pad assistant turns lacking the field."""
    if not isinstance(messages, list):
        return
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "assistant":
            continue
        existing = msg.get("reasoning_content")
        if existing is None:
            msg["reasoning_content"] = " "


def _strip_tool_choice_for_thinking(payload: dict[str, Any] | None) -> None:
    """Drop ``tool_choice`` for reasoning-echo thinking models (in place).

    Console Go (opencode.ai zen/go) and other DeepSeek/Kimi/MiMo thinking
    endpoints reject an explicit ``tool_choice`` with HTTP 400
    ("Thinking mode does not support this tool_choice"). The reasoning model
    decides tool calls on its own from the ``tools`` list, so the explicit
    choice is both unsupported and unnecessary — we drop it while keeping
    ``tools`` intact. Only applied when the model is a thinking family.
    """
    if not isinstance(payload, dict):
        return
    if "tool_choice" not in payload:
        return
    if not _model_needs_reasoning_echo(payload.get("model", "")):
        return
    payload.pop("tool_choice", None)


def _normalize_tool_name(name: Any) -> str:
    """Replace characters forbidden by strict upstream tool-name patterns.

    Console Go (opencode.ai zen/go) rejects tool/function names that do not
    match ``^[a-zA-Z0-9_-]+$``; Hermes ships MCP-style names such as
    ``default_api:kanban_show`` (colon is not in the pattern). The gateway
    rewrites the payload with ``_`` and restores the original name on the
    response so clients keep working with their native names.
    """
    if not isinstance(name, str) or ":" not in name:
        return name
    return name.replace(":", "_")


def _collect_tool_name_map(payload: dict[str, Any] | None) -> dict[str, str]:
    """Normalize tool/function names in the payload; return rewritten -> original.

    Touches ``tools[].function.name``, ``messages[].tool_calls[].function.name``
    and ``function_call.name`` (legacy). Names without a colon are left alone.
    """
    mapping: dict[str, str] = {}
    if not isinstance(payload, dict):
        return mapping

    def _rewrite(name: Any) -> str | None:
        if not isinstance(name, str) or ":" not in name:
            return None
        new = name.replace(":", "_")
        mapping.setdefault(new, name)
        return new

    for tool in payload.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function")
        if isinstance(fn, dict) and isinstance(fn.get("name"), str):
            new = _rewrite(fn["name"])
            if new is not None:
                fn["name"] = new

    fc = payload.get("function_call")
    if isinstance(fc, dict) and isinstance(fc.get("name"), str):
        new = _rewrite(fc["name"])
        if new is not None:
            fc["name"] = new

    for msg in payload.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        for tc in msg.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function")
            if isinstance(fn, dict) and isinstance(fn.get("name"), str):
                new = _rewrite(fn["name"])
                if new is not None:
                    fn["name"] = new
    return mapping


def _restore_tool_names(data: Any, mapping: dict[str, str]) -> None:
    """Restore original tool/function names on a response body or stream chunk."""
    if not isinstance(data, dict) or not mapping:
        return
    for choice in data.get("choices", []):
        if not isinstance(choice, dict):
            continue
        msg = choice.get("message") or choice.get("delta") or {}
        if not isinstance(msg, dict):
            continue
        for tc in msg.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function")
            if isinstance(fn, dict) and isinstance(fn.get("name"), str):
                original = mapping.get(fn["name"])
                if original is not None:
                    fn["name"] = original


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
    api_mode: str = "chat_completions"  # or "codex_responses" (POST /responses)
    min_output_tokens: int | None = None  # clamp for reasoning models (e.g. 4096 for muse)

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


# Per-model fallback when provider has no explicit min_output_tokens.
# Reasoning models (muse, R1, O1/O3, thinking) need budget or they
# truncate to 0 output (seen: max_output_tokens=200 → incomplete empty).
REASONING_MIN_TOKENS: dict[str, int] = {
    "muse": 8192,
    "r1": 4096,
    "reasoning": 4096,
    "thinking": 4096,
    "o1": 4096,
    "o3": 4096,
}

def _reasoning_min(bare: str, endpoint: ProviderEndpoint) -> int:
    """Effective min_output_tokens for (model, endpoint). Provider wins over pattern."""
    if endpoint.min_output_tokens is not None:
        try:
            v = int(endpoint.min_output_tokens)
            return v if v > 0 else 0
        except Exception:
            return 0
    low = bare.lower()
    for pat, val in REASONING_MIN_TOKENS.items():
        if pat in low:
            return val
    return 0


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
        signature_db_path: str | None = None,
    ) -> None:
        self._registry: dict[str, ProviderEndpoint] = {}
        self._timeout = timeout
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None
        self._model_index: dict[str, ProviderEndpoint] = {}
        self._thought_signatures: dict[str, str] = {}
        # fallback index: (fn_name, arguments) -> signature for clients that
        # rewrite tool_call ids (Hermes deterministic/codex ids) and cannot
        # be matched by the provider-assigned id.
        self._thought_signatures_by_fn: dict[tuple[str, str], str] = {}
        # Gemini thought_signatures must survive a gateway restart — Hermes
        # persists tool_calls without extra_content, so the signature is the
        # only thing that keeps the replay valid. Persist to the gateway DB.
        self._sig_lock = threading.Lock()
        self._sig_db: sqlite3.Connection | None = None
        if signature_db_path:
            try:
                db = sqlite3.connect(signature_db_path, check_same_thread=False)
                db.execute(
                    "CREATE TABLE IF NOT EXISTS thought_signatures("
                    "id TEXT, fn_name TEXT, arguments TEXT, signature TEXT NOT NULL,"
                    "created_at REAL NOT NULL,"
                    "PRIMARY KEY(id, fn_name, arguments))"
                )
                db.commit()
                self._sig_db = db
            except sqlite3.Error:
                logger.exception("failed to open thought_signature db")
        self._load_registry(registry or {})

    def reload_registry(self, registry: dict[str, dict[str, Any]]) -> None:
        """Hot-reload the provider registry without losing state.

        Used after sync-models adds/removes discovered models so the running
        proxy does not need a restart. Validates into a staging map first —
        on failure the live index is untouched. The HTTP client and the
        persisted thought_signature DB/locks are kept.
        """
        # Validate into a transient client so errors do not mutate self.
        try:
            tmp = DirectProviderClient.__new__(DirectProviderClient)
            tmp._registry = {}  # type: ignore[attr-defined]
            tmp._model_index = {}  # type: ignore[attr-defined]
            tmp._timeout = self._timeout  # type: ignore[attr-defined]
            tmp._load_registry(registry)
        except Exception:
            raise
        # Replace only the index — keep client + signature state.
        self._registry = tmp._registry  # type: ignore[attr-defined]
        self._model_index = tmp._model_index  # type: ignore[attr-defined]

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
                api_mode=str(raw.get("api_mode") or "chat_completions"),
                min_output_tokens=(
                    None
                    if raw.get("min_output_tokens") is None or str(raw.get("min_output_tokens")).strip() == ""
                    else int(str(raw.get("min_output_tokens")).strip())
                    if str(raw.get("min_output_tokens")).strip().lstrip("-").isdigit()
                    else None
                ),
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
    ) -> DirectProviderClient:
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

    def _reassemble_and_capture(self, chunks: list[dict[str, Any]]) -> None:
        """Merge streaming tool_call deltas per index, then capture signatures.

        Gemini's OpenAI-compatible streaming endpoint emits each tool_call as
        several chunks: the first carries ``id`` + ``extra_content.google.
        thought_signature`` (with empty/partial ``arguments``), and later
        chunks append ``arguments`` fragments. Per-chunk capture keys the
        signature under a partial arguments string, so a replay carrying the
        full arguments misses the (fn, arguments) index and Gemini rejects
        it with HTTP 400 "missing thought_signature". This reassembles the
        fragments (indexed by ``delta.tool_calls[].index``) and captures the
        completed tool_calls so the persisted index is replay-complete.
        """
        merged: dict[int, dict[str, Any]] = {}
        order: list[int] = []
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            for choice in chunk.get("choices", []):
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta")
                if not isinstance(delta, dict):
                    continue
                for tc in delta.get("tool_calls") or []:
                    if not isinstance(tc, dict):
                        continue
                    idx = tc.get("index")
                    try:
                        idx = int(idx) if idx is not None else None
                    except (TypeError, ValueError):
                        idx = None
                    if idx is None:
                        continue
                    bucket = merged.setdefault(idx, {})
                    if idx not in order:
                        order.append(idx)
                    # id / type / extra_content appear only on the first delta
                    # of a tool_call — later deltas carry only index + args.
                    for key in ("id", "type", "extra_content"):
                        if tc.get(key) is not None and bucket.get(key) is None:
                            bucket[key] = tc[key]
                    fn = tc.get("function")
                    if isinstance(fn, dict):
                        existing_fn = bucket.setdefault("function", {})
                        if isinstance(existing_fn, dict):
                            if fn.get("name") is not None:
                                existing_fn["name"] = fn["name"]
                            # arguments arrive in fragments — concatenate.
                            if isinstance(fn.get("arguments"), str):
                                existing_fn["arguments"] = (
                                    str(existing_fn.get("arguments", ""))
                                    + fn["arguments"]
                                )
        if not merged:
            return
        synthetic = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            merged[idx]
                            for idx in order
                            if merged[idx].get("function", {}).get("arguments")
                        ]
                    }
                }
            ]
        }
        self._capture_thought_signatures(synthetic)

    def _capture_thought_signatures(self, data: Any) -> None:
        """Persist Gemini tool-call thought_signatures by tool_call id.

        Gemini (OpenAI-compat endpoint) returns ``extra_content.google.
        thought_signature`` on each tool_call; the replay of that assistant
        turn must carry the signature back or the API rejects it with HTTP
        400 ("Function call is missing a thought_signature in functionCall
        parts"). Clients routing through a route name strip the field, so
        the gateway stores id -> signature and re-attaches it on the next
        outbound call. Entries are bounded and time-boxed.
        """
        if not isinstance(data, dict):
            return
        for choice in data.get("choices", []):
            if not isinstance(choice, dict):
                continue
            msg = choice.get("message") or choice.get("delta") or {}
            if not isinstance(msg, dict):
                continue
            for tc in msg.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                extra = tc.get("extra_content") or {}
                google = extra.get("google") or {} if isinstance(extra, dict) else {}
                sig = google.get("thought_signature") if isinstance(google, dict) else None
                tc_id = tc.get("id")
                if sig and isinstance(tc_id, str):
                    self._thought_signatures[tc_id] = sig
                if sig:
                    fn = (tc.get("function") or {}).get("name")
                    args = (tc.get("function") or {}).get("arguments")
                    if isinstance(fn, str) and isinstance(args, str):
                        self._thought_signatures_by_fn[(fn, args)] = sig
                        self._persist_signature(tc_id if isinstance(tc_id, str) else "", fn, args, sig)
        if len(self._thought_signatures) > 512 or len(self._thought_signatures_by_fn) > 512:
            # bound memory: keep the most recent half
            self._thought_signatures = dict(
                list(self._thought_signatures.items())[-256:]
            )
            self._thought_signatures_by_fn = dict(
                list(self._thought_signatures_by_fn.items())[-256:]
            )

    def _persist_signature(
        self, tc_id: str, fn_name: str, arguments: str, signature: str
    ) -> None:
        """Write one signature to the restart-surviving store (best effort)."""
        db = self._sig_db
        if db is None:
            return
        try:
            with self._sig_lock:
                db.execute(
                    "INSERT OR REPLACE INTO thought_signatures"
                    "(id, fn_name, arguments, signature, created_at) VALUES(?,?,?,?,?)",
                    (tc_id, fn_name, arguments, signature, time.time()),
                )
                db.commit()
        except sqlite3.Error:
            logger.exception("failed to persist thought_signature")

    def _lookup_signature(self, tc_id: str, fn_name: str, arguments: str) -> str | None:
        """Check the memory index first, then the persisted store."""
        sig = self._thought_signatures.get(tc_id) if tc_id else None
        if sig is None and fn_name and arguments:
            sig = self._thought_signatures_by_fn.get((fn_name, arguments))
            # The outbound tool-name rewriter maps "ns:fn" -> "ns_fn"; the
            # captured signature is keyed under the REWRITTEN name (that is
            # what the provider echoed back). Replays arrive with the original
            # colon form, so also try the rewritten variant before falling
            # back to the persisted store.
            if sig is None and ":" in fn_name:
                sig = self._thought_signatures_by_fn.get(
                    (fn_name.replace(":", "_"), arguments)
                )
        if sig is None:
            sig = self._sig_lookup_raw(tc_id, fn_name, arguments)
        if sig is None:
            sig = self._sig_lookup_json(tc_id, fn_name, arguments)
        return sig

    def _sig_lookup_raw(self, tc_id: str, fn_name: str, arguments: str) -> str | None:
        """Persisted-store lookup by exact id / fn+arguments match.

        Returns the matching signature (caching it in memory) or None when
        there is no hit. Mirrors the memory-index logic: the rewritten
        (underscore) tool-name variant is tried for colon-form names.
        """
        if self._sig_db is None:
            return None
        try:
            with self._sig_lock:
                row = self._sig_db.execute(
                    "SELECT signature FROM thought_signatures WHERE id=?",
                    (tc_id,),
                ).fetchone()
                if row is None and fn_name and arguments:
                    row = self._sig_db.execute(
                        "SELECT signature FROM thought_signatures"
                        " WHERE fn_name=? AND arguments=?"
                        " ORDER BY created_at DESC LIMIT 1",
                        (fn_name, arguments),
                    ).fetchone()
                    if row is None and ":" in fn_name:
                        row = self._sig_db.execute(
                            "SELECT signature FROM thought_signatures"
                            " WHERE fn_name=? AND arguments=?"
                            " ORDER BY created_at DESC LIMIT 1",
                            (fn_name.replace(":", "_"), arguments),
                        ).fetchone()
            if row is not None:
                sig = str(row[0])
                if tc_id:
                    self._thought_signatures[tc_id] = sig
                if fn_name and arguments:
                    self._thought_signatures_by_fn[(fn_name, arguments)] = sig
                return sig
        except sqlite3.Error:
            logger.exception("failed to read thought_signature")
        return None

    def _sig_lookup_json(self, tc_id: str, fn_name: str, arguments: str) -> str | None:
        """JSON-normalized fallback: compare parsed argument payloads.

        Clients (Hermes) may reserialize the arguments string with different
        spacing or key order than the provider echoed ({"board": "default"}
        vs {"board":"default"}). A raw string match then misses even though
        the semantic payload is identical — compare parsed JSON instead.
        """
        if not (fn_name and arguments and self._sig_db is not None):
            return None
        try:
            parsed = json.loads(arguments)
        except (TypeError, ValueError):
            return None
        if not isinstance(parsed, (dict, list)):
            return None
        try:
            with self._sig_lock:
                rows = self._sig_db.execute(
                    "SELECT fn_name, arguments, signature FROM thought_signatures"
                    " WHERE fn_name IN (?, ?)"
                    " ORDER BY created_at DESC LIMIT 50",
                    (fn_name, fn_name.replace(":", "_") if ":" in fn_name else fn_name),
                ).fetchall()
            for _db_fn, db_args, db_sig in rows:
                try:
                    if json.loads(db_args) == parsed:
                        sig = str(db_sig)
                        if tc_id:
                            self._thought_signatures[tc_id] = sig
                        if fn_name and arguments:
                            self._thought_signatures_by_fn[(fn_name, arguments)] = sig
                        return sig
                except (TypeError, ValueError):
                    continue
        except sqlite3.Error:
            logger.exception("failed to read thought_signature (json fallback)")
        return None

    def _restore_thought_signatures(self, messages: Any) -> None:
        """Re-attach stored Gemini thought_signatures to assistant turns."""
        if not isinstance(messages, list):
            return
        if (
            not self._thought_signatures
            and not self._thought_signatures_by_fn
            and self._sig_db is None
        ):
            return
        for msg in messages:
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                continue
            for tc in msg.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                if tc.get("extra_content"):
                    continue
                fn = (tc.get("function") or {}).get("name")
                args = (tc.get("function") or {}).get("arguments")
                sig = self._lookup_signature(
                    str(tc.get("id") or ""),
                    fn if isinstance(fn, str) else "",
                    args if isinstance(args, str) else "",
                )
                if not sig:
                    continue
                extra = tc.setdefault("extra_content", {})
                if not isinstance(extra, dict):
                    extra = {}
                    tc["extra_content"] = extra
                google = extra.setdefault("google", {})
                if not isinstance(google, dict):
                    google = {}
                    extra["google"] = google
                google["thought_signature"] = sig

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
        if kind == "chat" and getattr(endpoint, "api_mode", "") == "codex_responses":
            return await self._forward_responses(endpoint, model, body)
        url = self._request_url(endpoint, kind)
        payload = {k: v for k, v in body.items() if k in _FORWARD_ALLOWLIST}
        # Provider-qualified aliases (@slug/model) select the endpoint, but
        # the upstream always receives the bare model name.
        payload["model"] = model.split("/", 1)[1] if model.startswith("@") else model
        # Provider-level extra body (e.g. DeepInfra "flex": true) — config is
        # authoritative over anything the client sent.
        if endpoint.extra_body:
            payload.update(endpoint.extra_body)
        logger.debug(
            "direct forward %s -> %s extra_body=%s payload_keys=%s",
            model, endpoint.name, endpoint.extra_body, sorted(payload),
        )
        # DeepSeek/Kimi/MiMo thinking mode: assistant turns must carry
        # reasoning_content or the upstream rejects with HTTP 400. Clients
        # routing via a route name (not the model name) may strip it.
        if _model_needs_reasoning_echo(payload.get("model", "")):
            _pad_reasoning_content(payload.get("messages"))
            # Reasoning models decide tool calls on their own — an explicit
            # tool_choice is rejected (HTTP 400) by thinking endpoints.
            _strip_tool_choice_for_thinking(payload)
        # Gemini thought_signature: re-attach stored signatures so replayed
        # function calls are not rejected with HTTP 400.
        self._restore_thought_signatures(payload.get("messages"))
        # Strict upstreams (Console Go) reject ':' in tool names — rewrite
        # and restore on the response.
        tool_name_map = _collect_tool_name_map(payload)
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
        # Gemini: persist thought_signatures from this turn's tool calls.
        self._capture_thought_signatures(data)
        # Restore original (colon-qualified) tool names for the client.
        _restore_tool_names(data, tool_name_map)
        served = data.get("model") if isinstance(data, dict) else None
        return response.status_code, data, served or model

    async def _forward_responses(
        self,
        endpoint: ProviderEndpoint,
        model: str,
        body: dict[str, Any],
    ) -> tuple[int, dict[str, Any], str]:
        """Chat-completions in → Codex /Responses API out (translated).

        Some zen models (muse-spark family) only serve the Responses API;
        their chat-completions shim returns HTTP 500. Translate the OpenAI
        chat request to ``POST {base}/responses`` and map the response back
        to chat-completions JSON so callers see one shape.
        """
        bare = model.split("/", 1)[1] if model.startswith("@") else model
        messages = body.get("messages", [])
        input_items: list[dict[str, Any]] = []
        system_text = "\n\n".join(
            m["content"] for m in messages if m.get("role") == "system"
            and isinstance(m.get("content"), str)
        )
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            if role == "system":
                continue  # folded into `instructions`
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts = []
                for part in content:
                    if isinstance(part, dict):
                        parts.append(str(part.get("text") or ""))
                    else:
                        parts.append(str(part))
                text = "\n".join(parts)
            else:
                text = "" if content is None else json.dumps(content)
            is_assistant = role == "assistant"
            input_items.append(
                {
                    "role": "assistant" if is_assistant else "user",
                    # Responses API: assistant history uses `output_text`.
                    "content": [
                        {"type": "output_text" if is_assistant else "input_text", "text": text}
                    ],
                }
            )
        payload: dict[str, Any] = {
            "model": bare,
            "input": input_items,
            "stream": bool(body.get("stream")),
        }
        if system_text:
            payload["instructions"] = system_text
        max_tokens = body.get("max_completion_tokens") or body.get("max_tokens")
        if max_tokens:
            v = int(max_tokens)
            eff = _reasoning_min(bare, endpoint)
            if eff and v < eff:
                v = eff
            payload["max_output_tokens"] = v
        for src, dst in (("temperature", "temperature"), ("top_p", "top_p")):
            if body.get(src) is not None:
                payload[dst] = body[src]
        # Tools: translate chat shape to Responses shape
        if body.get("tools"):
            payload["tools"] = _chat_tools_to_responses(body["tools"])
        if body.get("tool_choice") is not None:
            tc = _chat_tool_choice_to_responses(body["tool_choice"])
            if tc is not None:
                payload["tool_choice"] = tc
        if endpoint.extra_body:
            payload.update(endpoint.extra_body)
        url = endpoint.url("/responses")
        headers = endpoint.headers()
        try:
            response = await self._client.post(url, json=payload, headers=headers)
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
        return response.status_code, _responses_to_chat(data, bare), data.get("model") or bare

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
        # DeepSeek/Kimi/MiMo thinking mode: pad assistant turns (see forward).
        if _model_needs_reasoning_echo(payload.get("model", "")):
            _pad_reasoning_content(payload.get("messages"))
            _strip_tool_choice_for_thinking(payload)
        # Gemini thought_signature: re-attach stored signatures (see forward).
        self._restore_thought_signatures(payload.get("messages"))
        # Strict upstreams (Console Go) reject ':' in tool names — rewrite
        # and restore on each stream chunk.
        tool_name_map = _collect_tool_name_map(payload)
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
                        # Gemini streaming: tool_calls arrive in deltas; the
                        # final tool_calls chunk carries the signature.
                        self._capture_thought_signatures(chunk)
                        # Restore original (colon-qualified) tool names.
                        _restore_tool_names(chunk, tool_name_map)
                # Gemini streams tool_calls as deltas: the FIRST chunk carries
                # id + signature (with empty/partial arguments), later chunks
                # append the arguments in fragments. Capturing per-chunk keys
                # the signature under a partial arguments string, so a replay
                # with the FULL arguments misses the lookup → Gemini 400.
                # Reassemble the deltas per index and capture the completed
                # tool_calls so the (fn, arguments) index is complete.
                self._reassemble_and_capture(chunks)
        except httpx.TimeoutException as exc:
            raise UpstreamProviderError(502, f"upstream provider timed out: {endpoint.name}") from exc
        except httpx.HTTPError as exc:
            raise UpstreamProviderError(502, f"upstream provider error: {endpoint.name}") from exc
        return response.status_code, chunks, served

    def _responses_input_from_messages(
        self, body: dict[str, Any]
    ) -> tuple[str, list[dict[str, Any]]]:
        """Shared chat-messages → (instructions, input items) translation."""
        messages = body.get("messages", [])
        system_parts: list[str] = []
        input_items: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            # Tool result messages (role=tool) → function_call_output
            if role == "tool":
                call_id = str(msg.get("tool_call_id") or msg.get("call_id") or "")
                if isinstance(content, str):
                    out_text = content
                elif isinstance(content, list):
                    out_text = "\n".join(str(p.get("text") or "") if isinstance(p, dict) else str(p) for p in content)
                else:
                    out_text = "" if content is None else __import__("json").dumps(content)
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": out_text,
                    }
                )
                continue
            # Assistant messages with tool_calls → function_call items
            if role == "assistant" and msg.get("tool_calls"):
                for tc in msg.get("tool_calls") or []:
                    if not isinstance(tc, dict):
                        continue
                    fn = tc.get("function") or {}
                    input_items.append(
                        {
                            "type": "function_call",
                            "call_id": str(tc.get("id") or ""),
                            "name": str(fn.get("name") or ""),
                            "arguments": str(fn.get("arguments") or "{}"),
                        }
                    )
                # If assistant also has text, keep it as output_text
                if isinstance(content, str) and content.strip():
                    text = content
                elif isinstance(content, list):
                    text = "\n".join(str(p.get("text") or "") if isinstance(p, dict) else str(p) for p in content)
                else:
                    text = "" if content is None else __import__("json").dumps(content)
                if text.strip():
                    input_items.append(
                        {
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": text}],
                        }
                    )
                continue
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts = []
                for part in content:
                    if isinstance(part, dict):
                        parts.append(str(part.get("text") or ""))
                    else:
                        parts.append(str(part))
                text = "\n".join(parts)
            else:
                text = "" if content is None else json.dumps(content)
            if role == "system":
                system_parts.append(text)
                continue
            is_assistant = role == "assistant"
            input_items.append(
                {
                    "role": "assistant" if is_assistant else "user",
                    # Responses API: assistant history uses `output_text`;
                    # `input_text` there is an invalid_request_error.
                    "content": [
                        {"type": "output_text" if is_assistant else "input_text", "text": text}
                    ],
                }
            )
        return "\n\n".join(system_parts), input_items

    async def _stream_responses(
        self,
        endpoint: ProviderEndpoint,
        model: str,
        body: dict[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        """Streaming variant of ``_forward_responses``.

        POSTs to ``{base}/responses`` with ``stream: true`` and translates the
        Responses SSE event stream into chat-completions chunks on the fly:
        ``response.output_text.delta`` → delta.content,
        ``response.completed`` → final chunk with usage + finish_reason.
        """
        bare = model.split("/", 1)[1] if model.startswith("@") else model
        instructions, input_items = self._responses_input_from_messages(body)
        payload: dict[str, Any] = {
            "model": bare,
            "input": input_items,
            "stream": True,
        }
        if instructions:
            payload["instructions"] = instructions
        max_tokens = body.get("max_completion_tokens") or body.get("max_tokens")
        if max_tokens:
            v = int(max_tokens)
            eff = _reasoning_min(bare, endpoint)
            if eff and v < eff:
                v = eff
            payload["max_output_tokens"] = v
        for src in ("temperature", "top_p"):
            if body.get(src) is not None:
                payload[src] = body[src]
        if body.get("tools"):
            payload["tools"] = _chat_tools_to_responses(body["tools"])
        if body.get("tool_choice") is not None:
            tc = _chat_tool_choice_to_responses(body["tool_choice"])
            if tc is not None:
                payload["tool_choice"] = tc
        if endpoint.extra_body:
            payload.update(endpoint.extra_body)
        url = endpoint.url("/responses")
        index = 0
        chunk_id = ""
        usage_raw: dict[str, Any] = {}
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
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    etype = event.get("type", "")
                    if etype == "response.output_text.delta":
                        chunk_id = chunk_id or str(event.get("item_id", ""))
                        delta = str(event.get("delta", ""))
                        if not delta:
                            continue
                        yield {
                            "id": chunk_id or f"resp-{index}",
                            "object": "chat.completion.chunk",
                            "model": bare,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"content": delta},
                                    "finish_reason": None,
                                }
                            ],
                        }
                        index += 1
                    elif etype in ("response.function_call.delta", "response.tool_call.delta"):
                        # Streaming function call arguments
                        yield {
                            "id": str(event.get("item_id") or f"resp-{index}"),
                            "object": "chat.completion.chunk",
                            "model": bare,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {
                                        "tool_calls": [
                                            {
                                                "index": 0,
                                                "id": str(event.get("item_id") or event.get("call_id") or f"call_{index}"),
                                                "type": "function",
                                                "function": {
                                                    "name": str(event.get("name") or ""),
                                                    "arguments": str(event.get("delta") or event.get("arguments") or ""),
                                                },
                                            }
                                        ]
                                    },
                                    "finish_reason": None,
                                }
                            ],
                        }
                        index += 1
                    elif etype in ("response.completed", "response.incomplete"):
                        resp = event.get("response", {}) or {}
                        if etype == "response.incomplete" and not resp:
                            resp = event
                        usage_raw = resp.get("usage", {}) or {}
                        status = str(resp.get("status", "")) if isinstance(resp, dict) else ""
                        incomplete = resp.get("incomplete_details") if isinstance(resp, dict) else None
                        # Check if response contains function calls → tool_calls finish
                        has_fn = False
                        for it in resp.get("output", []) or []:
                            if isinstance(it, dict) and it.get("type") in ("function_call", "tool_call"):
                                has_fn = True
                                break
                        if has_fn:
                            finish = "tool_calls"
                        else:
                            finish = "length" if (etype == "response.incomplete" or status == "incomplete" or incomplete) else "stop"
                        # If there are function calls, emit them as tool_calls deltas before final
                        for it in resp.get("output", []) or []:
                            if isinstance(it, dict) and it.get("type") in ("function_call", "tool_call"):
                                yield {
                                    "id": str(resp.get("id", "")) or f"resp-{index}",
                                    "object": "chat.completion.chunk",
                                    "model": str(resp.get("model", "")) or bare,
                                    "choices": [
                                        {
                                            "index": 0,
                                            "delta": {
                                                "tool_calls": [
                                                    {
                                                        "id": str(it.get("call_id") or it.get("id") or f"call_{index}"),
                                                        "type": "function",
                                                        "function": {
                                                            "name": str(it.get("name") or ""),
                                                            "arguments": str(it.get("arguments") or "{}"),
                                                        },
                                                    }
                                                ]
                                            },
                                            "finish_reason": None,
                                        }
                                    ],
                                }
                                index += 1
                        yield {
                            "id": str(resp.get("id", "")) or f"resp-{index}",
                            "object": "chat.completion.chunk",
                            "model": str(resp.get("model", "")) or bare,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {},
                                    "finish_reason": finish,
                                }
                            ],
                            "usage": {
                                "prompt_tokens": int(usage_raw.get("input_tokens", 0) or 0),
                                "completion_tokens": int(usage_raw.get("output_tokens", 0) or 0),
                                "total_tokens": int(usage_raw.get("total_tokens", 0) or 0),
                            },
                        }
                    elif etype == "response.failed":
                        resp = event.get("response", {}) or {}
                        err = resp.get("error") or event.get("error") or {}
                        msg = err.get("message") if isinstance(err, dict) else str(err)
                        raise UpstreamProviderError(
                            502,
                            f"upstream provider error: {endpoint.name} (response.failed: {msg})",
                            body=str(event)[:2000],
                        )
        except httpx.TimeoutException as exc:
            raise UpstreamProviderError(502, f"upstream provider timed out: {endpoint.name}") from exc
        except httpx.HTTPError as exc:
            raise UpstreamProviderError(502, f"upstream provider error: {endpoint.name}") from exc

    async def stream_chunks(
        self,
        model: str,
        body: dict[str, Any],
        *,
        kind: str = "chat",
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream a request chunk-by-chunk as an async generator.

        Unlike ``forward_stream`` (which drains the whole stream before
        returning), this yields each SSE chunk the moment it arrives, so the
        client sees tokens as they are generated. The httpx client timeout
        covers connect + the gap between chunks. On completion the generator
        raises ``StopAsyncIteration`` naturally.

        Gemini thought_signatures are captured per chunk and reassembled at
        the end (same as ``forward_stream``). Tool names are restored per
        chunk.
        """
        endpoint = self.resolve(model)
        if kind == "chat" and getattr(endpoint, "api_mode", "") == "codex_responses":
            async for chunk in self._stream_responses(endpoint, model, body):
                yield chunk
            return
        url = self._request_url(endpoint, kind)
        payload = {k: v for k, v in body.items() if k in _FORWARD_ALLOWLIST}
        payload["model"] = model.split("/", 1)[1] if model.startswith("@") else model
        payload["stream"] = True
        if endpoint.extra_body:
            payload.update(endpoint.extra_body)
        if _model_needs_reasoning_echo(payload.get("model", "")):
            _pad_reasoning_content(payload.get("messages"))
            _strip_tool_choice_for_thinking(payload)
        self._restore_thought_signatures(payload.get("messages"))
        tool_name_map = _collect_tool_name_map(payload)
        chunks: list[dict[str, Any]] = []
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
                    if not isinstance(chunk, dict):
                        continue
                    chunks.append(chunk)
                    self._capture_thought_signatures(chunk)
                    _restore_tool_names(chunk, tool_name_map)
                    yield chunk
                self._reassemble_and_capture(chunks)
        except httpx.TimeoutException as exc:
            raise UpstreamProviderError(502, f"upstream provider timed out: {endpoint.name}") from exc
        except httpx.HTTPError as exc:
            raise UpstreamProviderError(502, f"upstream provider error: {endpoint.name}") from exc

