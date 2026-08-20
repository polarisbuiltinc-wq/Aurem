# G6 — Duplicate-Detection Sweep Scope (2026-08-20)

Founder-approved scope for this pass: **3 collections only**. Broader
sweep across all ~130 collections is a separate future task.

## Checked and already had proper unique constraints (no action needed)
- `dev_users` — `email_1`, `user_id_1`, `uniq_email`
- `chat_sessions` — session-id unique index present
- `api_keys` — key-hash unique index present
- `cto_projects` — project-key unique index present

## Real gaps found and fixed this pass
| Collection | Field | Why it's a true-duplicate key | Fix |
|---|---|---|---|
| `email_verifications` | `token` | Single-use verification token (`uuid4().hex`), previously only de-duped by app-level `update_many` invalidation logic, no DB backstop | Added `unique=True` index `uniq_token` (`main.py` startup) |
| `oauth_states` | `state` | CSRF state nonce for OAuth 2.1/GitHub OAuth flows, previously no DB-level constraint | Added `unique=True` index `uniq_state` |
| `oauth_codes` | `code` | Short-lived PKCE auth code (already TTL-purged via `expires_at`), previously no uniqueness backstop during its live window | Added `unique=True` index `uniq_code` |

## Not in scope for this pass (future sweep candidates)
`onboarding_emails` has a compound index (`user_id, campaign, stage`) that
is intentionally **not** unique (campaigns can resend by design) — not a
gap, just noted for the future sweep to avoid re-flagging it. The other
~120+ collections were not individually audited this pass.
