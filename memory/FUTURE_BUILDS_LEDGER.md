# AUREM — Future Builds Ledger

**Single source of truth for "save it for future" items.**
Founder-facing. Numbered. Cross-referenced with full design docs when they exist.

---

## How this file works (rules for future agents)

1. **When founder says "save it for future"** → append the item to `## 📋 Future Builds` section with the next available number, one-line summary, priority tag, and (if a detailed design doc exists) a link to it.
2. **When a build is completed and verified** → cut the item from `## 📋 Future Builds`, paste into `## ✅ Shipped Features` with the same number preserved (never re-use numbers), add ship date, add commit/iter ref.
3. **Never delete numbers.** Cancelled items go to `## ❌ Cancelled / Rejected` with a one-line reason, number preserved.
4. **Priority tags:** 🔴 P0 (revenue/stability blocker) · 🟠 P1 (soon) · 🟡 P2 (nice-to-have) · ⚪ P3 (someday).
5. **Trigger column** = the concrete signal that should promote this item from backlog to active build (e.g. "founder green-light", "first user complaint", "10+ paying users", etc.).

---

## 📋 Future Builds

| # | Feature | Priority | Trigger to start | Design doc |
|---|---|---|---|---|
| 1 | **Object-storage / CDN pattern for user media** — persist ORA-generated images + optional chat-upload archive. Two build routes specced (Emergent-managed vs GridFS-own). | 🟠 P1 | Founder green-light OR first user report that a generated-image link went dead in their gallery. | [`GRIDFS_MEDIA_STORAGE_DESIGN.md`](./GRIDFS_MEDIA_STORAGE_DESIGN.md) |
| 2 | **DB Schema Normalization Audit (A- → A)** — Full collection ownership map (`DB_SCHEMA_MAP.md`), flag denormalized fields (e.g. user email cached in multiple docs), write dedupe cleanup script. ~2h. | 🟡 P2 | After migration framework lands so schema changes can be tracked. | *TBD — no design doc yet* |
| 3 | **Index Registry Consolidation (A → A polish)** — Merge 71 scattered `create_index` calls into single declarative source-of-truth. Add `scripts/audit_indexes.py` using `$indexStats` to find dead/duplicate indexes. ~2h. | 🟡 P2 | After migration framework so index changes are versioned. | *TBD — no design doc yet* |
| 4 | **Query Optimization Observability (B- → A)** — Slow-query middleware (>500ms → Sentry breadcrumb), P95 latency logging on hot endpoints, `db.setProfilingLevel` toggle, weekly slow-query digest. ~4h. | 🟡 P2 | Founder notices perf degradation OR after 100+ paying users. | *TBD — no design doc yet* |
| 5 | **Backup Hardening (D+ → A) 🔴 CRITICAL** — Current `mongodump` writes to `/tmp/backups/` which is **ephemeral pod disk** (wiped on pod restart). Add offsite destination (Emergent-managed / R2 / S3 / encrypted GitHub tarball — founder to choose), weekly restore-drill script, Sentry alert on 2+ consecutive fails, encryption-at-rest. ~5h. | 🔴 **P0 (silent data-loss risk)** | Founder picks backup destination (a/b/c/d from the plan). Until then, current `/tmp/` backup runs but is unreliable. | *TBD — spec once destination chosen* |

---

## ✅ Shipped Features

*(Empty — nothing shipped from this ledger yet. Historical shipped work lives in `PRD.md` + `CHANGELOG.md`.)*

| # | Feature | Shipped on | Commit / Iter ref |
|---|---|---|---|

---

## ❌ Cancelled / Rejected

*(Empty.)*

| # | Feature | Rejected on | Reason |
|---|---|---|---|

---

**Last updated:** 2026-02 · Founder created this ledger via instruction "save all future builds in a file … with listing numbers".
