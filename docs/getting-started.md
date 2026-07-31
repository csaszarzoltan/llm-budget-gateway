# Getting Started

Boot the gateway, issue your first request, and verify the budget and
fallback layers work. This guide assumes a checkout of the repository.

## 1. Install

```bash
git clone https://github.com/csaszarzoltan/llm-budget-gateway.git
cd llm-budget-gateway
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Requires Python 3.11+. Runtime dependencies are pinned in
`pyproject.toml` (FastAPI, uvicorn, pydantic v2, litellm <2, PyYAML).

## 2. Configure

Minimum viable configuration — a virtual API key (map a key your
clients will send to an internal scope id):

```bash
export GATEWAY_VIRTUAL_KEYS='{"sk-test-123":"key1"}'
```

Provider credentials come from the gateway process environment (standard
LiteLLM vars), never from client requests:

```bash
export OPENAI_API_KEY="sk-..."
```

Optional: copy the example budget file to the default path so the
enforcement layer has something to enforce:

```bash
cp examples/budgets.example.yaml budgets.yaml
```

See [Proxy Setup](proxy-setup.md) for the full environment reference and
[Budget Configuration](budget-configuration.md) for the YAML shape.

## 3. Run

```bash
.venv/bin/uvicorn llm_budget_gateway.main:create_app --factory --port 8000
```

The app is an app factory, so the `--factory` flag is required.

## 4. Verify

```bash
curl -s http://localhost:8000/health
# {"status":"ok"}
```

## 5. First request

```bash
curl -s http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-test-123" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello!"}]}'
```

The response is provider-shaped (drop-in replacement for calling the
provider directly). Wrong or missing keys get a 401, unknown models a
404, exhausted budgets a 412, exhausted rate ceilings a 429, and a dead
upstream a 502 — see [Proxy Setup → HTTP semantics](proxy-setup.md#http-semantics).

## 6. See it working end-to-end

No API key, no network needed:

```bash
.venv/bin/python examples/quickstart.py
```

walks every status code against a fake provider and then prints the
SQLite cost ledger the gateway wrote.

## Next steps

- [Proxy Setup](proxy-setup.md) — all endpoints, env vars, security model
- [Cost Tracking](cost-tracking.md) — how spend is priced and stored
- [Budget Configuration](budget-configuration.md) — scopes, limits, windows
- [Fallback Chains](fallback-chains.md) — automatic model failover
