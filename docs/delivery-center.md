# Delivery Center 6.0

Delivery Center makes the gateway's documented production and release practices executable without changing existing proxy routes.

## Capabilities

- Environment readiness checks required configuration names without exposing values.
- Configuration drift reports changed fields and an integrity fingerprint.
- Capacity planning validates RPM and TPM headroom with a configurable reserve.
- Dependency health separates required outages from optional degradation.
- Rollout planning validates increasing canary stages ending at 100%.
- Rollback decisions enforce quality, error-rate, and latency guardrails.
- Observability coverage checks required logs, metrics, and traces.
- Alert-route validation requires supported severities/channels and signed webhooks.
- Runbook coverage requires an owner and recovery steps for every failure mode.
- Release manifests canonicalize artifact hashes and produce a SHA-256 release fingerprint.

## Run and use

```bash
export GATEWAY_DELIVERY_API_KEY='replace-me'
uvicorn llm_budget_gateway.delivery_api:create_delivery_app --factory --port 8014
```

Every `POST /v1/delivery/{capability}` request requires `Authorization: Bearer <key>` and `X-Tenant-Id`. The service is stateless, deterministic, additive, and fail closed.

```bash
curl -s http://localhost:8014/v1/delivery/capacity-plan \
  -H 'Authorization: Bearer replace-me' \
  -H 'X-Tenant-Id: tenant-a' \
  -H 'Content-Type: application/json' \
  -d '{"rpm_limit":120,"tpm_limit":120000,"peak_rpm":90,"peak_tpm":90000,"reserve_ratio":0.2}'
```
