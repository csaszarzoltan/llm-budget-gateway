# Migration to 0.8.0

The release is additive. Existing proxy, control and intelligence APIs are unchanged. Configure `GATEWAY_OPERATIONS_API_KEY`; without it, protected operations routes return 503 rather than starting insecurely. Run the operations application separately on port 8003. Back up `operations.db` before host migration. Prompt versions are immutable and intentionally have no update endpoint.
