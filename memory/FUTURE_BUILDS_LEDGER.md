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
