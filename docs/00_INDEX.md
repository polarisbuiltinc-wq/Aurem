# AUREM / ORA — Internal Product Docs Index

Living documents describing the AUREM CTO product itself (NOT a
user-generated project). Every fact traces to the current codebase/DB.
Update incrementally — do not regenerate from memory.

| Doc | What it covers |
|---|---|
| [PRD.md](./PRD.md) | What ORA is today: value prop, users, features, pricing, honest gaps |
| [TRD.md](./TRD.md) | Actual stack, LLM chain, integrations, deployment model |
| [APP_FLOW.md](./APP_FLOW.md) | RailShell navigation, real user journeys, admin flows |
| [UI_UX_BRIEF.md](./UI_UX_BRIEF.md) | Design tokens in use, fonts, canonical nav pattern |
| [SCHEMA.md](./SCHEMA.md) | MongoDB collections actually in use and what each stores |
| [IMPLEMENTATION_STATUS.md](./IMPLEMENTATION_STATUS.md) | Shipped / in-progress / deferred (living status) |
| [REGRESSION_GUARDS.md](./REGRESSION_GUARDS.md) | 21 guards: what each protects, live status location, RED/STALE runbook |

**Deploy checklist rule** (also printed by `scripts/predeploy_gate.sh`):
if a change affects PRD / TRD / App Flow / UI-UX / Schema — update the
relevant doc in the same change.

Last index update: 2026-06-30 (Iter 358).
