# APP_FLOW — real navigation & journeys (living doc)

Last updated: 2026-06-30 (Iter 358).

## Canonical navigation: RailShell (Iter 356)
`frontend/src/components/nav/RailShell.jsx` — 56px icon rail, always
visible on every viewport. 5 sections, each opens a flyout panel
(closes on Escape / outside click / route change):

1. **Chat** — Repositories group (project switcher; selecting a repo
   closes the flyout) + Recent chats (server-side filtered: our own
   `prod-e2e-*` E2E sessions are excluded) + New chat.
2. **Ship** — Deploy (/deploy), Domain (/domain), Automations
   (/automations), Ship Wall (/wall).
3. **Insights** — Analytics (/analytics), Tokens (/tokens), Wrapped
   (/wrapped).
4. **Settings** — Profile / Plans & usage / Integrations / Vault
   (/settings?tab=…) + IDE setup MCP (/integrations, has back button).
5. **Admin** (founder-only) — Overview (/admin), Financials, Users,
   Suggestions, API keys (/admin/*). Admin has a "back to app" link.

Standalone pages (Settings window, Integrations) carry their own back
navigation. Legacy `Shell.jsx` still provides ChatSession context and
the old sidebar for some pages — full dead-code removal is Phase 4,
deferred until founder verifies the unified nav in production.

## Primary user journey (developer track)
signup → NewUserWizard (3 steps, inline GitHub OAuth) → connect repo →
chat (mode auto-classified) → task or Loop Mode run
(Plan → Execute → Verify → Security scan → Ship) → commit/PR lands →
Ship Wall + Wrapped + "ships this week" chip update
(`/wrapped/me?period=this_week` counts cto_tasks AND loop_sessions).

## Public/marketing flow
Landing (/) — social proof strip renders live /usage/public/stats →
/pricing → /compare hub → /vs/{devin,cursor,github-copilot,
replit-agent,windsurf} (single data source src/data/competitors.js,
FAQPage JSON-LD, prerendered static snapshots in dist for crawlers) →
/signup. Also /wall, /wrapped, /why-ora, llms.txt + llms-full.txt.

## Admin flows (founder)
/admin Overview: build badge (`build <sha> · env · uptime · GitHub
sync state`), critical alerts banner (topup_alerts — integration +
github_sync alerts, email via Resend), features live status, funnel.
/admin/qa: QA health, test counts; REGRESSION GUARDS section planned
(ships last in guards charter). Financials, Users, Suggestions,
API keys as rail items.
