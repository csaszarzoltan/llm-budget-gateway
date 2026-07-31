# Service Homepages 7.3

Every service started from Unified Console now exposes a responsive landing page at the root of its own port.

Examples:

- Gateway: `http://127.0.0.1:8000/`
- Control Center: `http://127.0.0.1:8001/`
- Intelligence: `http://127.0.0.1:8002/`
- Operations: `http://127.0.0.1:8003/`
- Quality: `http://127.0.0.1:8004/`
- Security: `http://127.0.0.1:8005/`
- Resilience: `http://127.0.0.1:8006/`
- Optimization: `http://127.0.0.1:8007/`
- Collaboration: `http://127.0.0.1:8008/`
- Platform: `http://127.0.0.1:8009/`
- AgentOps: `http://127.0.0.1:8010/`
- Fleet Governance: `http://127.0.0.1:8011/`
- Assurance: `http://127.0.0.1:8012/`
- Delivery: `http://127.0.0.1:8014/`
- Scale: `http://127.0.0.1:8015/`

Each generated page provides links to OpenAPI documentation, OpenAPI JSON, health status, Unified Console and the service-specific dashboard when available.

The implementation wraps the original app factory. It adds `/` only when the application does not already define a root route, so existing homepages and every API route remain intact.
