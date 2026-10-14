# LLM Budget Gateway

A local-first, OpenAI-compatible AI gateway that enforces spending budgets, routes requests across models with automatic fallback, and includes a web cockpit to see and control it all.

Point your existing OpenAI SDK or tool at the gateway. Each request is checked against your budgets, recorded in a local ledger, and sent to the provider you configured. Provider keys stay on your server.

## Features

- **Budget enforcement:** set spending limits per key, user or team. A request that would exceed a hard budget is refused with HTTP 412 before it reaches the provider.
- **Logical routes and fallback:** call a route by name instead of a provider model. Routes have immutable versions, weighted splits and fallback chains, so a failing provider can fall through to the next target.
- **Cockpit:** a web UI for routes, providers, activity traces, usage and cost, alerts and governance.
- **Safety tools:** a Runaway Cost Firewall that fails closed before an agent exceeds its cost, token, tool-call or time limits; an Agent Runtime Governor that detects repeated actions and intent drift; a Provider Compatibility Lab; and an incident timeline built from real routing decisions.
- **Opt-in request features:** exact-response cache (`X-Gateway-Cache: 1`), PII redaction (`X-Gateway-Redact-Pii: 1`) and cost-aware routing (`metadata.cost_aware`).
- **Observability:** request, cost, latency and success summaries, and trace export as OpenTelemetry/OpenInference-shaped documents.
- **Governance:** MCP tool governance with per-tool budgets and audit trails, versioned prompts with A/B assignment, quality gates, and budget alerts by webhook, Slack, Telegram or email.

## Quick start

**Requirements:** Python 3.11+, [uv](https://docs.astral.sh/uv/), and Node.js 20+ (to build the cockpit).

```bash
git clone https://github.com/csaszarzoltan/llm-budget-gateway.git
cd llm-budget-gateway
uv sync --extra dev --frozen

# Git checkouts do not include the built cockpit (ui/dist is not tracked)
cd ui && npm ci && npm run build && cd ..

uv run gateway-system
```

This starts the proxy on `http://127.0.0.1:8000` and the cockpit on `http://127.0.0.1:8013/cockpit`, and opens the cockpit in your browser. To run the same system without opening a browser:

```bash
uv run uvicorn llm_budget_gateway.system_launcher:create_system_app --factory --host 127.0.0.1 --port 8013
```

Check readiness with `curl -s http://127.0.0.1:8013/v1/system/status`.

## Use it from your code

Start the proxy with at least one client key. Client keys map to internal key ids. Provider credentials stay on the server, either as environment variables or as encrypted connections added in the cockpit:

```bash
export GATEWAY_VIRTUAL_KEYS='{"sk-local-123":"my-app"}'
export OPENAI_API_KEY="sk-..."   # or any other provider key LiteLLM supports
uv run uvicorn llm_budget_gateway.main:create_app --factory --host 127.0.0.1 --port 8000
```

Send a request with curl:

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-local-123" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello!"}]}'
```

Or point the OpenAI Python SDK at it:

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="sk-local-123")
reply = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(reply.choices[0].message.content)
```

Responses follow the provider's shape. Errors use these status codes:

| Status | Meaning |
|---|---|
| 401 | Missing or unknown client key |
| 404 | Unknown model |
| 412 | Hard budget exhausted |
| 429 | Rate ceiling reached |
| 502 | Upstream provider failed |

Full details: [Proxy setup](docs/proxy-setup.md#http-semantics).

## Configuration

Settings are read from environment variables with the `GATEWAY_` prefix. The most common ones:

| Variable | Purpose |
|---|---|
| `GATEWAY_VIRTUAL_KEYS` | JSON map of client API keys to internal key ids |
| `GATEWAY_BUDGET_CONFIG_PATH` | Budget YAML file (default `budgets.yaml`); start from `examples/budgets.example.yaml` |
| `GATEWAY_DATABASE_URL` | SQLite ledger location |
| `GATEWAY_ENABLE_SATELLITES` | Set to `1` to also start the legacy standalone services |

The full reference is in [Configuration](docs/CONFIGURATION.md), and [`.env.example`](.env.example) is a template.

## Development

```bash
uv run pytest -q
uv run ruff check src tests examples
cd ui && npm test && npm run build
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow, code conventions and commit format. Engineering standards are in [docs/engineering-standards.md](docs/engineering-standards.md), decision records in [docs/decisions/](docs/decisions/), and specifications in [docs/specs/](docs/specs/).

To build a release archive (after building the cockpit):

```bash
uv run python scripts/build_release.py dist/llm-budget-gateway.zip
```

The builder fails if `ui/dist` is missing. It excludes virtual environments, node modules, caches, databases, logs, generated keys and TypeScript build metadata.

## Documentation

- [Getting started](docs/getting-started.md)
- [Proxy setup and HTTP semantics](docs/proxy-setup.md)
- [Configuration reference](docs/CONFIGURATION.md)
- [Budget configuration](docs/budget-configuration.md) and [cost tracking](docs/cost-tracking.md)
- [Logical routing API](docs/logical-routing-api.md), [fallback chains](docs/fallback-chains.md) and [Route Studio](docs/route-studio.md)
- [Provider connections](docs/provider-connections-13.2.md)
- [Safety operations API](docs/api/safety-operations-api.md)
- [MCP governance](docs/mcp-governance.md)
- [Alert notifications](docs/alert-notifications.md)
- [Architecture](docs/ARCHITECTURE.md) and [deployment](docs/DEPLOYMENT.md)
- [Feature history](docs/feature-history.md) (per-release notes) and [CHANGELOG](CHANGELOG.md)

## Security notes

- Provider credentials stay on the server. Client requests cannot override provider keys, base URLs or headers.
- Databases, logs, `.env` files and the provider master key are git-ignored. Keep them out of commits.
- Services bind to `127.0.0.1` by default. Read [deployment](docs/DEPLOYMENT.md) before exposing them on a network.
- SQLite is the default store and is meant for a single node. Check the production-readiness docs before running several instances.
- MCP governance is single-tenant. Do not expose it as a multi-tenant service without tenant-partitioned storage.

## License

MIT. See [LICENSE](LICENSE).
