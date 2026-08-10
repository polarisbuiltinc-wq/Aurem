"""
services/external_services_registry.py — Iter 123f drift-proof external
services catalog.

Single source of truth for the Architecture tab's "External services"
and "Integrations" cards. Adding a new external dep is ONE entry here,
not two edits across the router file.

A `Service` ties together:
  • display_name   — what the founder sees in the Architecture tab
  • env_keys       — the env vars that MUST be set for this service to be
                     "configured". Missing ANY → integration shows missing.
  • probe_url      — best-effort unauth GET. None → skip probing
                     (e.g. MongoDB, which is checked via the db handle).
  • integration_id — short slug used as the integration card key.
                     Keeps the existing UI keys ("openrouter (deepseek)" etc.)
                     so we don't break the frontend chip text.

Probing rule: if no env_key is set, we DON'T probe — that's the
auto-discovery the founder asked for (no point hitting Sentry if no DSN).
Services WITHOUT env_keys (like GitHub's public API) always probe.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass(frozen=True)
class Service:
    display_name:   str
    integration_id: str
    env_keys:       tuple[str, ...] = field(default_factory=tuple)
    probe_url:      str | None      = None
    always_probe:   bool            = False  # True for public APIs (GitHub etc.)
    # 2026-02-10 — some integrations aren't env-backed (e.g. GitHub App
    # credentials live in Mongo `admin_settings`, not `.env`). When set,
    # `custom_configured` overrides the env-based check entirely: it MUST
    # return True iff the integration is fully wired. Must be a zero-arg
    # callable so it can be evaluated cheaply on every /architecture hit.
    custom_configured: Optional[Callable[[], bool]] = None


# Order = order shown in the External services card (left → right).
REGISTRY: tuple[Service, ...] = (
    Service(
        display_name="GitHub API",
        integration_id="github_oauth",
        env_keys=("GITHUB_OAUTH_CLIENT_ID", "GITHUB_OAUTH_CLIENT_SECRET"),
        probe_url="https://api.github.com",
        always_probe=True,    # public endpoint — probe even without keys
    ),
    Service(
        display_name="OpenRouter",
        integration_id="openrouter (deepseek)",
        env_keys=("OPENROUTER_API_KEY",),
        probe_url="https://openrouter.ai/api/v1/models",
    ),
    Service(
        display_name="Emergent LLM",
        integration_id="emergent_llm (maxx)",
        env_keys=("EMERGENT_LLM_KEY",),
        probe_url=None,    # internal — no public probe URL
    ),
    Service(
        display_name="Anthropic API",
        integration_id="anthropic (claude maxx)",
        env_keys=("ANTHROPIC_API_KEY",),
        probe_url="https://api.anthropic.com/v1/messages",
    ),
    Service(
        display_name="Cloudflare API",
        integration_id="cloudflare_purge",
        env_keys=("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ZONE_ID"),
        probe_url="https://api.cloudflare.com/client/v4/user/tokens/verify",
    ),
    Service(
        display_name="Vercel API",
        integration_id="vercel_deploy_hook",
        env_keys=("VERCEL_API_TOKEN",),
        probe_url="https://api.vercel.com/v2/user",
    ),
    Service(
        display_name="Sentry ingest",
        integration_id="sentry_dsn",
        env_keys=("SENTRY_DSN",),
        probe_url="https://sentry.io/api/0/",
    ),
    Service(
        display_name="Stripe API",
        integration_id="stripe",
        # Codebase reads either STRIPE_API_KEY or STRIPE_SECRET_KEY (see
        # _stripe_key() in routers/payments.py — preserves backwards-compat
        # with older deploys). Treat the integration as "configured" iff
        # EITHER is set to a real (non-placeholder) value.
        env_keys=("STRIPE_API_KEY",),
        probe_url="https://api.stripe.com/v1/",
    ),
    Service(
        display_name="Resend email",
        integration_id="resend (email)",
        env_keys=("RESEND_API_KEY",),
        probe_url=None,     # Resend has no public unauth ping
    ),
    Service(
        display_name="Tavily search",
        integration_id="tavily (web search)",
        env_keys=("TAVILY_API_KEY",),
        probe_url=None,     # Tavily auth-only
    ),
    Service(
        display_name="Firecrawl",
        integration_id="firecrawl (web scrape)",
        env_keys=("FIRECRAWL_API_KEY",),
        probe_url=None,     # auth-only
    ),
    Service(
        display_name="e2b sandbox",
        integration_id="e2b (code exec)",
        env_keys=("E2B_API_KEY",),
        probe_url=None,     # SDK manages connection
    ),
    # 2026-08-01 — AUREM Org GitHub integration. Deactivate-honestly:
    # `services/github_org_client.is_configured()` requires all three
    # env vars set; all three are UNSET in prod → integration surfaces
    # cleanly return `aurem_org_not_configured` from every entry-point
    # AND admin dashboard shows "missing". Fixes the Batch-2 finding
    # that this integration was silently omitted from the /architecture
    # grid (worse than Supabase/Vercel which at least show as missing).
    # No behavior change to callers — this only makes the honest state
    # VISIBLE on the admin dashboard so it can't get quietly enabled
    # halfway (only 1 of 3 keys set) without the founder noticing.
    Service(
        display_name="AUREM Org (GitHub)",
        integration_id="aurem_org_github",
        env_keys=("AUREM_ORG_NAME", "AUREM_ORG_GITHUB_APP_TOKEN",
                  "AUREM_ORG_DEFAULT_BRANCH"),
        probe_url=None,     # no unauth probe URL — auth-only surface
    ),
    # 2026-02-10 — GitHub App (AUREM DevOps, org: AuremHQ). Credentials
    # are DB-backed (`admin_settings._id="github_app_config"`) rather
    # than env-backed, so `is_configured` short-circuits to the runtime
    # cache check. Full live status is on the Settings tab in
    # <GitHubAppConfigCard/>; this entry is what makes the Integrations
    # grid on the Architecture tab accurate.
    Service(
        display_name="GitHub App (AUREM DevOps)",
        integration_id="github_app",
        env_keys=(),        # not env-backed
        probe_url=None,     # auth-only surface (App JWT); live probe
                            # lives in <GitHubAppConfigCard/>
        custom_configured=(
            lambda: _is_github_app_configured()
        ),
    ),
)


def _is_github_app_configured() -> bool:
    """Late import so a registry decode never triggers the config
    module at import time (avoids circulars during pytest collection)."""
    try:
        from services.github_app_config import is_configured as _cfg
        return bool(_cfg())
    except Exception:                                            # noqa: BLE001
        return False


def is_configured(svc: Service) -> bool:
    """A service is configured iff EVERY env_key it declares is set,
    OR — for DB-backed services — the `custom_configured` callable
    returns True."""
    if svc.custom_configured is not None:
        try:
            return bool(svc.custom_configured())
        except Exception:                                        # noqa: BLE001
            return False
    if not svc.env_keys:
        return True   # services with no required keys are always "configured"
    return all(bool(os.getenv(k)) for k in svc.env_keys)


def should_probe(svc: Service) -> bool:
    """We probe only when there's a URL AND either it's a public endpoint
    OR the keys are configured. Saves rate-limit budget on dev environments
    where most keys are deliberately empty."""
    if not svc.probe_url:
        return False
    return svc.always_probe or is_configured(svc)
