# LLM Budget Gateway

A local-first, OpenAI-compatible AI gateway and operations control plane with budget enforcement, logical routing, provider discovery, trace analytics, governance, and a React product cockpit.

## Requirements

- Python 3.11+
- `uv`
- Node.js 20+ and npm when rebuilding the cockpit

## Install

```bash
uv sync --extra dev --frozen
```

The release archive includes the built cockpit in `ui/dist`. To rebuild it:

```bash
cd ui
npm ci
npm test
npm run build
cd ..
```

## Run the complete local product

```bash
uv run gateway-system --no-browser
```

Open `http://127.0.0.1:8013/cockpit`. Remove `--no-browser` to open it automatically.

The launcher owns only child services it starts and exposes readiness at:

```text
GET http://127.0.0.1:8013/v1/system/status
```

## Run only the OpenAI-compatible gateway

```bash
export GATEWAY_VIRTUAL_KEYS='{"sk-test-123":"key1"}'
uv run uvicorn llm_budget_gateway.main:create_app --factory --host 127.0.0.1 --port 8000
```

Verify:

```bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/v1/models
```

Provider credentials are read from the server environment. Client request bodies cannot override provider keys, base URLs, or headers.

## Test and quality gates

```bash
uv run pytest -q
uv run ruff check src tests examples
cd ui && npm test && npm run build
```

## Primary product areas

- **Home:** health, activation, KPIs, attention, active routes, recent decisions
- **Applications:** application identities and stable route assignment
- **Routes:** logical aliases, immutable versions, simulation, publishing, fallback
- **Providers:** encrypted named connections and model discovery
- **Activity:** explainable routing decisions and trace evidence
- **Usage:** request, cost, latency, and success summaries
- **Advanced:** services, nested traces, governance, security, supply chain, and API access

## Key documentation

- [Getting started](docs/getting-started.md)
- [GUI architecture](docs/gui-architecture-12.md)
- [Logical routing API](docs/logical-routing-api.md)
- [Provider connections](docs/provider-connections-13.2.md)
- [Proxy setup](docs/proxy-setup.md)
- [Budget configuration](docs/budget-configuration.md)
- [MCP governance](docs/mcp-governance.md)
- [Market research](research-findings.md)
- [Changelog](CHANGELOG.md)

## Security and deployment notes

- Runtime databases, environment files, logs, provider master keys, virtual environments, and Node modules are excluded from source control.
- SQLite is the default local/single-node store. Review the production-readiness APIs and documentation before multi-instance deployment.
- MCP governance v1 is intentionally single-tenant and must not be exposed as a multi-tenant service without tenant-partitioned persistence.

## License

Add the intended project license before public distribution if the repository does not already carry one.
