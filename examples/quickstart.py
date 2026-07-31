"""Gateway quickstart: the full HTTP surface against a fake provider.

Boots ``create_app()`` with a mocked provider (no API key, no network)
and walks every documented status code: health, models, auth (401),
unknown model (404), hard budget (412), rate limit (429), streaming SSE,
embeddings, provider timeout (502) — then inspects the SQLite cost ledger
that the gateway wrote.

The mock provider is a stand-in for litellm: swap in real
``OPENAI_API_KEY``-style env vars and remove the ``_patch_provider`` step
to run against a live upstream.

Usage:
    .venv/bin/python examples/quickstart.py
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from llm_budget_gateway.config import Settings
from llm_budget_gateway.cost_tracking import CostStore
from llm_budget_gateway.main import create_app


class _FakeResponse:
    """Object-shaped provider response (mirrors litellm ModelResponse).

    ``GatewayProxy.forward`` reads usage via attributes and serializes the
    body via ``model_dump()`` — a plain dict response would zero out the
    usage (dict keys are not attributes) and record $0 spend.
    """

    def __init__(self, body: dict) -> None:
        usage = body.get("usage")
        self.usage = SimpleNamespace(**usage) if usage else None
        self._body = body
        self.model = body["model"]
        self.status_code = 200
        self.headers = {}

    def model_dump(self) -> dict:
        return self._body

BUDGETS_YAML = """\
scopes:
  - scope: {kind: key, key: "key1"}
    soft_limit: 0.005      # alert only (never blocks)
    hard_limit: 0.01       # USD -> reject with 412 once spend >= limit
    window: "30s"
  - scope: {kind: key, key: "key2"}
    rpm_limit: 1           # 1 request per 30s -> second call gets 429
    window: "30s"
  - scope: {kind: key, key: "key3"}
    window: "30s"          # unlimited: stream + embeddings + timeout demo
"""


#: Shorthand message list used across the request examples below.
CHAT = [{"role": "user", "content": "hi"}]


async def _fake_chunks(body: dict):
    """SSE-shaped async generator: two content deltas + a usage tail chunk.

    Chunks are object-shaped (like litellm ModelResponse chunks) so the
    gateway can read ``.usage`` attributes and serialize via ``model_dump``.
    """
    yield _FakeChunk(
        body["model"],
        [{"delta": {"role": "assistant", "content": "Hel"}, "finish_reason": None}],
    )
    yield _FakeChunk(
        body["model"],
        [{"delta": {"content": "lo!"}, "finish_reason": "stop"}],
        usage={"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
    )


class _FakeChunk:
    """Object-shaped stream chunk (mirrors a litellm ModelResponse chunk)."""

    def __init__(self, model: str, choices: list, usage: dict | None = None) -> None:
        self.model = model
        self.choices = choices
        self.usage = SimpleNamespace(**usage) if usage else None
        self._body = {"model": model, "choices": choices}
        if usage:
            self._body["usage"] = usage

    def model_dump(self) -> dict:
        return self._body


async def _fake_acompletion(**kwargs):
    """Stand-in for litellm.acompletion: dict-shaped chat/completion body."""
    messages = kwargs.get("messages") or []
    if messages and messages[0].get("content") == "SLEEP":
        # Hang forever; GATEWAY_PROVIDER_TIMEOUT (0.05s) cuts it -> 502.
        await asyncio.sleep(3600)
    if kwargs.get("stream"):
        return _fake_chunks(kwargs)
    return _FakeResponse(
        {
            "id": "chatcmpl-fake",
            "object": "chat.completion",
            "model": kwargs["model"],
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Hello from the fake provider!",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 500,
                "total_tokens": 1500,
            },
        }
    )


async def _fake_aembedding(**kwargs):
    """Stand-in for litellm.aembedding."""
    return _FakeResponse(
        {
            "object": "list",
            "data": [{"embedding": [0.1, 0.2], "index": 0}],
            "usage": {"prompt_tokens": 7, "total_tokens": 7},
            "model": kwargs["model"],
        }
    )


def _show(label: str, resp: httpx.Response) -> None:
    content_type = resp.headers.get("content-type", "")
    body = resp.text if "event-stream" in content_type else resp.text[:400]
    print(f"  {resp.status_code}  {label}")
    print(f"       {body[:300].strip()!r}")


async def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="gateway-quickstart-"))
    try:
        budgets = tmp / "budgets.yaml"
        budgets.write_text(BUDGETS_YAML)

        settings = Settings(
            database_url=f"sqlite:///{tmp / 'gateway.db'}",
            budget_config_path=str(budgets),
            provider_timeout=0.05,
            virtual_keys={
                "sk-key1": "key1",
                "sk-key2": "key2",
                "sk-key3": "key3",
            },
            user_header_mappings={"X-Team-Id": "team"},
            pricing_overrides={
                "gpt-4o": {
                    "input_cost_per_million": 5.0,
                    "output_cost_per_million": 15.0,
                },
            },
        )
        app = create_app(settings)

        # Mock the provider layer (litellm) — swap for real env keys to go live.
        import litellm

        litellm.acompletion = _fake_acompletion  # type: ignore[assignment]
        litellm.aembedding = _fake_aembedding  # type: ignore[assignment]

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://gateway", timeout=10.0
        ) as client:
            print("== health ==")
            _show("GET /health", await client.get("/health"))

            print("== models ==")
            resp = await client.get("/v1/models")
            models = resp.json()["data"]
            print(f"  {resp.status_code}  GET /v1/models -> {len(models)} models")
            print(f"       first 3: {[m['id'] for m in models[:3]]}")

            print("== auth: no key -> 401 ==")
            _show(
                "POST /v1/chat/completions (no Authorization)",
                await client.post(
                    "/v1/chat/completions",
                    json={"model": "gpt-4o", "messages": CHAT},
                ),
            )

            print("== unknown model -> 404 ==")
            _show(
                "POST /v1/chat/completions (model=no-such-model)",
                await client.post(
                    "/v1/chat/completions",
                    json={"model": "no-such-model", "messages": CHAT},
                    headers={"Authorization": "Bearer sk-key1"},
                ),
            )

            print("== happy path: key1 chat -> 200 ==")
            _show(
                "POST /v1/chat/completions (sk-key1)",
                await client.post(
                    "/v1/chat/completions",
                    json={"model": "gpt-4o", "messages": CHAT},
                    headers={"Authorization": "Bearer sk-key1", "X-Team-Id": "eng"},
                ),
            )

            print("== hard budget: key1 second call -> 412 ==")
            _show(
                "POST /v1/chat/completions (sk-key1 again)",
                await client.post(
                    "/v1/chat/completions",
                    json={"model": "gpt-4o", "messages": CHAT},
                    headers={"Authorization": "Bearer sk-key1"},
                ),
            )

            print("== rate limit: key2 second call -> 429 ==")
            await client.post(
                "/v1/chat/completions",
                json={"model": "gpt-4o", "messages": CHAT},
                headers={"Authorization": "Bearer sk-key2"},
            )
            _show(
                "POST /v1/chat/completions (sk-key2 again)",
                await client.post(
                    "/v1/chat/completions",
                    json={"model": "gpt-4o", "messages": CHAT},
                    headers={"Authorization": "Bearer sk-key2"},
                ),
            )

            print("== streaming: key3 stream=true -> 200 SSE ==")
            stream_resp = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": CHAT,
                    "stream": True,
                },
                headers={"Authorization": "Bearer sk-key3"},
            )
            print(
                f"  {stream_resp.status_code}  "
                f"content-type: {stream_resp.headers['content-type']}"
            )
            for line in stream_resp.text.splitlines():
                if line.startswith("data: "):
                    print(f"       {line}")

            print("== embeddings: key3 -> 200 ==")
            _show(
                "POST /v1/embeddings (sk-key3)",
                await client.post(
                    "/v1/embeddings",
                    json={"model": "text-embedding-3-small", "input": ["hello"]},
                    headers={"Authorization": "Bearer sk-key3"},
                ),
            )

            print("== provider timeout: key3 SLEEP -> 502 ==")
            _show(
                "POST /v1/chat/completions (content=SLEEP, 50ms timeout)",
                await client.post(
                    "/v1/chat/completions",
                    json={
                        "model": "gpt-4o",
                        "messages": [{"role": "user", "content": "SLEEP"}],
                    },
                    headers={"Authorization": "Bearer sk-key3"},
                ),
            )

        print("== cost ledger (SQLite WAL) ==")
        store = CostStore(str(tmp / "gateway.db"))
        import time

        now = int(time.time())
        for scope in ("key:key1", "key:key2", "key:key3", "global:default"):
            spend = store.spend_since(scope, now - 3600)
            print(f"  spend_since({scope!r}, now-3600) = ${spend:.6f}")
        store.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
