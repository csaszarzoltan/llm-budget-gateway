# Preflight cost estimation

`POST /v1/cost-estimates` calculates a conservative request-cost estimate without contacting an LLM provider or storing prompt content. It uses the same configured pricing map as billing and the gateway token heuristic.

```bash
curl -s http://localhost:8000/v1/cost-estimates \
  -H "Authorization: Bearer sk-test-123" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"Hello"}],"max_completion_tokens":500}'
```

The response includes estimated input tokens, the requested maximum output tokens, separate input/output costs, an upper-bound total, currency, and `pricing_known`. A false `pricing_known` value prevents callers from mistaking an unconfigured model's zero price for a free request. Estimates never reserve budget and may differ from provider tokenization or actual output length.
