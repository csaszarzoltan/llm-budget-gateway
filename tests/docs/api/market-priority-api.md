# Market Priority API 13.5

All endpoints are restricted to local callers because they process operational evidence.

- `POST /v1/console/replay/compare` accepts `baseline` and `candidate` evidence and returns semantic similarity, operational deltas, tool changes, safety-policy change, and an accept/review/reject recommendation.
- `POST /v1/console/governor/evaluate` accepts an approved `intent`, executed `steps`, optional `loop_threshold`, and `approved_actions`; it fails closed on loops, drift, or an unapproved irreversible action.
- `POST /v1/console/contracts` records one measured provider/model capability, timestamp, optional per-million price, and region.
- `GET /v1/console/contracts/{provider_id}` returns the provider's newest-first compatibility and pricing matrix.

The domain interfaces are in `market_priority.py`. The contract catalog uses SQLite and an injectable clock so freshness decisions are deterministic and testable.
