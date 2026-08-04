# Unified Gateway Console 7.1

The Unified Gateway Console is a responsive, dependency-free browser application that makes every gateway workspace and capability discoverable from one place.

## Experience design

- A persistent left navigation groups workspaces by user goal: Core, FinOps, Operations, Quality, Security, Administration, Platform, Agents, Governance and Delivery.
- Global search finds both workspaces and individual capabilities.
- The command palette opens with `Ctrl+K` or `Command+K`.
- Workspace cards expose health, frequently used actions, each local dashboard and OpenAPI documentation.
- The Universal API Runner can call a named capability or a custom path with GET, POST, PUT or DELETE.
- The runner accepts tenant ID, base URL, bearer key and JSON body, and can generate a cURL command.
- Service health checks inspect every local app independently.
- Light and dark themes, keyboard focus, skip navigation, reduced motion and responsive reflow are built in.

## Privacy and security

The console is presentation-only and has no credential database. Bearer keys are retained only in browser `sessionStorage` for the selected workspace and are sent directly to that service. The page does not embed credentials or load third-party scripts, fonts or styles.

Cross-origin browser calls require the target service or deployment proxy to allow the console origin. In production, place the console and APIs behind the same trusted reverse proxy and existing authentication layer.

## Run

```bash
uvicorn llm_budget_gateway.console_api:create_console_app --factory --port 8013
```

Open:

```text
http://localhost:8013/console
```

The machine-readable catalog is available at `GET /v1/console/catalog` and liveness at `GET /health`.

## Supported workspaces

The catalog covers the core Gateway, Control Center, Intelligence, Operations, Quality, Security, Resilience, Optimization, Collaboration, Platform, AgentOps, Fleet Governance, Assurance, Delivery and Scale services.
