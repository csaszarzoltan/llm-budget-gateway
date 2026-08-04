# Priority Route Builder 11.2

## Product design

The builder uses an ordered-card model rather than forcing first-time users into a general graph editor. Priority is visible and editable through move controls, while every target exposes the policy that directly affects its eligibility. Advanced failure and capability settings are progressively disclosed.

This follows current gateway patterns without copying a competitor UI: Cloudflare represents dynamic routing as versioned start, condition, percentage, budget/rate, model, and end nodes; Portkey permits nested conditional, load-balancing, and fallback strategies; LiteLLM groups multiple deployments behind aliases and combines load balancing, cooldowns, health checks, retries, and fallbacks.

Primary references:

- https://developers.cloudflare.com/ai-gateway/features/dynamic-routing/
- https://docs.portkey.ai/docs/guides/use-cases/combining-routing-strategies
- https://docs.portkey.ai/docs/product/ai-gateway/load-balancing
- https://docs.litellm.ai/docs/routing-load-balancing

## Evaluation order

For every target in ascending priority:

1. enabled state;
2. target-local weekday and timezone window;
3. target-local calendar-month budget;
4. latest health state;
5. required capabilities.

All eligible targets form the fallback attempt order. The first is selected. A runtime failure advances only when its status occurs in the current target's `fallback_statuses` list.

Overnight windows are supported. For example, `18:00` to `08:00` is active when local time is at or after 18:00 or before 08:00.

## API

- `POST /v1/admin/priority-routes`
- `GET /v1/admin/priority-routes`
- `GET /v1/admin/priority-routes/{route_id}`
- `PUT /v1/admin/priority-routes/{route_id}`
- `POST /v1/admin/priority-routes/{route_id}/publish`
- `POST /v1/admin/priority-routes/{route_id}/simulate`

A target requires `model`, unique positive `priority`, `timezone`, `days`, `start`, `end`, positive `monthly_budget`, `enabled`, retry-safe `fallback_statuses`, and `required_capabilities`.
