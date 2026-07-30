# TRD — AUREM CTO technical requirements (living doc)

Last updated: 2026-06-30 (Iter 358).

## Stack (actual)
- **Backend**: FastAPI (Python 3.11), Motor async MongoDB driver,
  Server-Sent Events for chat + loop streams, custom JWT auth with
  brute-force protection, NoSQL-injection ASGI middleware.
- **Frontend**: React 19 + Vite, react-router SPA, Tailwind present but
  most product surfaces use inline style objects; vitest + Playwright.
- **DB**: MongoDB (101 collections as of Iter 358 — see SCHEMA.md),
  ~30 indexes, connection pool maxPoolSize=50.
- **Tests**: ~3.7k backend pytest + ~237 vitest run by
  `scripts/predeploy_gate.sh` (blocking) + qa_matrix regression locks.

## LLM chain (real, source: backend/services/llm.py + llms.txt)
- Primary routing via **OpenRouter** (GLM, DeepSeek V3/v4-flash,
  Claude Sonnet 4.5 for review/Vanguard).
- **4-hop fallback**: OpenRouter → DeepSeek direct API
  (api.deepseek.com, separate billing) → free-model chain → Groq
  emergency. Fallback trips on statuses 402/404/408/425/429/5xx.
- Mode routing: Swift (cheapest), Pro (GLM writes + Sonnet reviews),
  Maxx (Sonnet writes + parallel verify), D debug, E audit, F engage.

## Integrations (live)
- **GitHub**: REST API + OAuth app + PATs; PATs encrypted at rest with
  HKDF-Fernet (per-tenant derived keys). Direct commit + PR delivery.
- **Stripe**: 4-tier subscriptions, webhook ledger sync (payments.py),
  abandoned-checkout handling.
- **Vercel**: MCP tool for deploy/preview flows.
- **Tavily** (web search) + **Firecrawl** (scrape) — currently alerting
  (credits/key pending founder action).
- **Sentry** backend monitoring (DSN set), **Resend** email
  (alerts + onboarding), **E2B** sandbox for Vanguard verify.
- **MCP 2.4 server** at /api/aurem-dev/mcp (Cursor/Claude Desktop).

## Deployment model — READ THIS, it caused confusion once
- Work happens in the **Emergent workspace** (`/app`). Preview URL is
  the dev environment.
- **Production deploys FROM THE WORKSPACE** via the Emergent deployer
  (`bash scripts/predeploy_gate.sh` MUST pass first) → auremcto.com.
- **"Save to GitHub" is MANUAL and user-only.** There is NO auto-sync.
  The GitHub repo (polarisbuiltinc-wq/auremdev) goes stale unless the
  founder clicks Save to GitHub after work sessions/deploys.
  Guard 8 (services/github_sync.py) watches this: Admin Overview build
  badge shows "GitHub sync: X behind"; >48h gap → RED alert.
  Requires GITHUB_ACTIONS_TOKEN + GITHUB_REPO env (pending).
- `tjsandhu/aurem` is a **connected dogfood project inside the
  product**, NOT the product repo.
- Frontend build: `yarn build` = `vite build && node
  scripts/seo-prerender.mjs` (writes static SEO snapshots for /vs/* +
  /compare into dist/).

## Environment rules
- Backend on :8001 (supervisor), frontend :3000; all API routes under
  `/api`; frontend uses REACT_APP_BACKEND_URL; Mongo via MONGO_URL +
  DB_NAME only. Secrets only in .env, never in code.
