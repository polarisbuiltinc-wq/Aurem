"""services/http/__init__.py — public exports.

The shared HTTP client layer. Every outbound HTTP call to a
third-party service (GitHub, Resend, Vercel, Supabase, etc.) should
go through `ext_client(dep_name)` so we get uniform:

  - Timeout policy (per-dep tunable, sensible defaults)
  - Retry + circuit-breaker via services.retry_guard
  - Structured error type (`ExternalCallError`) that carries `dep`,
    `status`, and safe `body_snippet` for logs / Sentry / UI
  - Trace-id propagation (X-Request-ID injected on every call)

This module is intentionally small — it's a policy layer on top of
`httpx.AsyncClient`. It doesn't hide httpx; callers can still reach
`client.get / .post / .put / .delete` naturally.
"""
from services.http.client import (
    ExternalCallError,
    ext_client,
    ext_request,
)

__all__ = ["ExternalCallError", "ext_client", "ext_request"]
