# Task 1 + Task 2 (Section 2 A1-A4) Read-Only Audit — 2026-08-27

Scope: read-only. No files modified, no flags changed, no builds run.

## TASK 1 — Design Tokens + Color Ratio + Contrast

### T1 — Token inventory (CONFIRMED, file:line)
Two parallel CSS-variable systems coexist in `frontend/src/index.css`:
- Legacy `:root` (main app) — `index.css:120-152` (dark), overridden `html[data-theme="light"]:159-172`.
- `.ds2-root` (v2 dashboard) — `index.css:11-52` (dark), `.ds2-root[data-theme="light"]:59-92`.
`tailwind.config.js:6-13` keeps a THIRD set (`cto-bg`, `cto-panel`, etc., legacy/unused-most-places) alongside the ds2 semantic tokens (`:17-37`) that just re-point to the CSS vars.

**Verdict: PARTIALLY MANAGED.** Named tokens exist for base surfaces (bg/panel/border/text) but the ~115 distinct raw hex literals found inline across chat-surface components (below) prove badge/pill/status colors are NOT drawn from the token system — every card invents its own palette (`WorkCard.jsx:18-23`, `LoopStatusChip.jsx:60-69`, `LiveTaskPopup.jsx:36-44`, etc.).

### Raw hex inventory (CONFIRMED — methodology disclosed)
Scanned 3 disclosed file sets (chat=41 files, task-run/cards=15 files, admin=18 files; full list in `/app/memory/audit/contrast_audit.py` sibling script). Regex `#[0-9a-f]{3,6}`.

| Surface | Files | Distinct hex | Occurrences | Top 5 |
|---|---|---|---|---|
| Chat | 41 | 115 | 363 | #ff6608(31) #ef4444(19) #22c55e(18) #0a0a0a(17) #f59e0b(11) |
| Task-run/cards | 15 | 73 | 228 | #fca5a5(19) #86efac(16) #94a3b8(13) #7dd3fc(12) #4ade80(9) |
| Admin | 18 | 83 | 332 | #ff8a2a(36) #22c55e(17) #f59e0b(14) #888(13) #ff8a8a(11) |

No overlap-dedup between surfaces was applied (a file counted once per surface it's assigned to). This is a fresh count, not a recovery of the prior truncated run's numbers (those are unrecoverable — different tool/session).

### T2 — Screenshot pixel histogram (CONFIRMED, own methodology)
Reused the 3 screenshots already captured in `/root/.emergent/automation_output/20260826_174212/` (1920×1080 JPEGs), downsampled 192×108, classified against named palette-family swatches (nearest-match, threshold-gated):

| View | dark-base/panel | surface/border | orange-accent | other-saturated | green | blue-info | amber | muted-gray | red |
|---|---|---|---|---|---|---|---|---|---|
| Chat/composer | 84.0% | 15.1% | 0.5% | 0.4% | – | – | – | 0.1% | 0.0% |
| Task-run/cards | 85.1% | 14.4% | 0.2% | 0.2% | – | – | – | 0.1% | 0.0% |
| Admin | 92.3% | 5.8% | 0.0% | 0.3% | 0.5% | 0.4% | 0.3% | 0.4% | 0.1% |

**Interpretation:** viewport pixel-area is ~84-92% dark base/panel, ~6-15% surface/border, accents/status colors together <1.5%. This does **not** resemble a literal 60-30-10 split — it reads as a near-monochrome dark shell with sparse accent color, which is a defensible dark-app pattern but not a classic 60-30-10 ratio. No pass/fail — heuristic only, as instructed.

### T3 — Contrast (CONFIRMED — WCAG relative-luminance formula, computed not estimated)
Full script + output: `/app/memory/audit/contrast_audit.py`. 53 pairs computed. **Failures found (P0/P1):**

| Priority | Pair | Ratio | Rule |
|---|---|---|---|
| P1 | `--border` rgba(255,200,120,.10) blended over `--bg` → `#201b18` vs `#07080d` (index.css:132) | **1.17:1** | non-text 3:1 — FAIL |
| P1 | `--border-strong` rgba(255,200,120,.22) blended → `#3e3225` vs `#07080d` (index.css:133) | **1.61:1** | non-text 3:1 — FAIL |
| P1 | `.ds2-root` `--ds2-border #222222` vs `--ds2-bg #0A0A0A` (index.css:15,13) | **1.24:1** | non-text 3:1 — FAIL |
| P1 | `LoopStepBar` neutral/muted `#666` vs card `#161616` (LoopStepBar.jsx:77,302) | 3.15:1 | non-text 3:1 — borderline PASS, but same token fails as **body text** anywhere it's reused |
| P2 | `ShipLintBadge` blocked fg `#ef4444` vs 15%-blended bg (ShipLintBadge.jsx:50-52) | **4.24:1** | text 4.5:1 — FAIL (just under) |

All body/label text pairs checked (18+ combinations across WorkCard, LoopStatusChip, LiveTaskPopup, IntentTierIndicator, ds2 root, legacy root) **PASS AA (4.5:1)**; several miss AAA (7:1) — expected/acceptable for a dark UI, not flagged as a defect.

**Net verdict:** Text contrast is solid across the app. The real gap is **non-text border/divider tokens** — both the legacy `--border`/`--border-strong` alpha tokens AND the ds2 `--ds2-border` solid token render as near-invisible (1.1-1.6:1) hairlines against their own background, well under the 3:1 WCAG non-text minimum. This is a decorative/structural issue, not a readability blocker (no information is border-only), but is a real, reproducible token defect.

### T4 — WorkCard state redundancy (CONFIRMED, file:line)
`WorkCard.jsx` (the actual shared shell, `WorkCard.jsx:17-23`) only defines **5 tones**: `blue, green, amber, red, grey` — there is no literal "queued/running/awaiting_user/done/failed/blocked" enum inside WorkCard itself; those states are mapped by each **caller**. Confirmed caller mapping in `FirstScanCard.jsx`:
- `skipped` → tone=`grey` (FirstScanCard.jsx:147)
- `scanning`/`still_scanning` (timed out) → tone=`amber` (FirstScanCard.jsx:166)
- `scanning`/`still_scanning` (live) → tone=`blue` (FirstScanCard.jsx:180)
- `error` → tone=`red` (FirstScanCard.jsx:194)
- `clean` → tone=`green` (FirstScanCard.jsx:209)
- `ready`+fixed → tone=`green` (FirstScanCard.jsx:233) — **same green as `clean`**, distinguished only by badge text ("Fixed" vs "Clean") + title copy, not color/hue.
- `ready` (unfixed findings) → tone=`blue` (FirstScanCard.jsx:261) — **same blue as "still scanning" live state.**

**Finding:** two pairs of states share identical hue: (`clean` vs `fixed`) both green, and (`scanning-live` vs `findings-ready`) both blue. They are distinguished only by badge text label, not color — acceptable per WCAG (color is never the *only* differentiator, text label always present) but is a genuine "low visual distinctiveness" note for Phase E design if the founder wants each state to have a unique color family.
`ScanStatusStrip.jsx` reuses the same WorkCard shell with tone `red/amber/green` mapped from critical/high/clean counts (ScanStatusStrip.jsx:183) — consistent pattern, no new collision.

---

## TASK 2 — Section 2, A1-A4 chip/pill audit only

### A1 — Chip/pill/badge inventory (CONFIRMED, file:line)
No two chip implementations share a size. Sampled 14 implementations:

| Component | Padding | Font-size | Radius | Height (if fixed) |
|---|---|---|---|---|
| WorkCard badge (WorkCard.jsx:64-77) | 2px 8px | 10px | 999 | auto |
| ShipLintBadge (ShipLintBadge.jsx:63-77) | 3px 8px | 10px | 4 | auto |
| LoopStatusChip stop/done btn (LoopStatusChip.jsx:502-518,536-552) | 3px 10px | 11px | 6 | auto |
| LiveStepFloatingCard phase pill (LiveStepFloatingCard.jsx:122-149) | 3px 7px | 10px | 4 | auto |
| LiveTaskPopup phase chip (LiveTaskPopup.jsx:260-282) | 3px 8px | 11px | 12 | auto |
| TemperatureBadge (TemperatureBadge.jsx:13-28) | 2px 6px | 10px | 999 | auto |
| IntentTierIndicator (IntentTierIndicator.jsx:85-98) | 0 8px | 9px | 999 | **28px fixed** |
| ModeLoopPill collapsed (ModeLoopPill.jsx:156-170) | 5px 12px | 11px | 999 | auto |
| CharCounter (CharCounter.jsx:20-35) | none | 11px | none | auto |
| LoopStepBar retry pill (LoopStepBar.jsx:378-395) | 3px 9px | 10px | 999 | auto |
| ShipPendingCard integrity pill (ShipPendingCard.jsx:56-81) | 4px 10px | 10.5px | 999 | auto |
| ShipPendingCard diff chip (ShipPendingCard.jsx:113-142) | 1px 5px / none | 8.5-9.5px | 3-4 | auto |
| ToolButton icon btn (ToolButton.jsx:32,41) | — | icon 14/15px | 4/8 | **34px fixed, 34/42w** |
| ActionBtn (LoopActionCards.jsx:365-385) | 7px 14px | 11.5px | 8 | auto |

Font sizes alone span **8.5px → 13px** (10 distinct values) with no shared scale variable.

### A2 — Densest row / composer width comparison
**LIKELY, not live-measured** — a live-browser measurement attempt (Playwright via screenshot_tool, logged in with preview credentials `test@aurem.dev`) was made but the app was still on its "LOADING…" splash when the script's evaluate() ran, and the tool does not surface Python `print()`/`page.evaluate()` return values back to this chat (only a screenshot image) — so no numeric readback was retrievable this way. Falling back to **direct CSS-source computation** (deterministic, not estimated):

- Composer width source: `[data-testid="chat-form"].glass-composer { padding: 14px clamp(16px, 17.25%, 240px); }` (`index.css:1142-1144`), inside `[data-testid="chat-panel"] { container-type: inline-size; }` (`index.css:1133-1136`). Container-query overrides: ≤900px → fixed 24px padding, ≤600px → fixed 12px padding (`index.css:1156-1172`).
- **At 1440px viewport** (sidebar 260px expanded, `index.css:791`): chat-panel container ≈ 1180px → padding = 17.25%×1180 = 203.6px/side → **composer content width ≈ 773px**.
- **At 360px viewport** (sidebar off-canvas/drawer, `index.css:920-926`): chat-panel container ≈ 360px → container-query ≤600px fires fixed 12px/side → **composer content width ≈ 336px**.
- Densest row identified structurally: `.composer-toolbar` (`ChatPanel.jsx:5447`) — contains file-input(hidden) + Attach(ToolButton, 42×34) + conditional Ops-History toggle(42×34) + GitHub-status(38×34) + IntentTierIndicator(fixed 28h, variable width) + CharCounter + ModeLoopPill/LoopModeToggle + Send button. On mobile, `index.css:711-735` already strips IntentTierIndicator, CharCounter, graph-toggle, github-status, and the footer caption specifically because this row was documented as overflowing ("Mobile composer declutter", `index.css:707-710`) — i.e. **the codebase's own comments already confirm this row was too dense on ≤768px and a prior iteration hard-removed 4 of ~8 elements** rather than resizing them to a shared scale.

### A3 — Existing shared primitive / size scale
**CONFIRMED: NO.** `ToolButton.jsx` is the only reusable sizing primitive, and it covers icon buttons only (34/42 × 34px). There is no shared `Chip`/`Badge`/`Pill` component or CSS custom-property scale (no `--chip-height`, `--badge-font-size`, etc. exist anywhere in `index.css` or `tailwind.config.js`). Every one of the 14 sampled implementations in A1 hardcodes its own padding/font-size/radius inline. `WorkCard.jsx` is the closest thing to a shared shell but only wraps card-level layout (icon/title/badge/body/meta/actions) — its *inner* badge (`WorkCard.jsx:64-77`) is still one specific hardcoded size, not a reusable size token other components import.

### A4 — Composer width source
**CONFIRMED**, same citation as A2: `index.css:1142-1144` (`[data-testid="chat-form"].glass-composer` padding rule) is the single source of truth, as the code comment at `index.css:1106-1132` explicitly states ("single source of truth for chat horizontal rhythm"). No competing inline width style overrides it except the JS-driven live-popup `paddingRight` exception noted in that same comment block (`index.css:1127-1130`), which only affects right padding when a popup is open.

---

## Summary of defects found (for founder decision, not fixed)
1. **P1** — `--border` / `--border-strong` / `--ds2-border` fail WCAG non-text 3:1 (1.1-1.6:1). Real, reproducible, computed.
2. **P2** — `ShipLintBadge` blocked-state text `#ef4444` at 4.24:1, just under AA text 4.5:1.
3. **Design-debt (not a WCAG fail)** — no shared chip/pill/badge sizing primitive; 14 sampled implementations use 8 distinct font-sizes and inconsistent padding. Composer toolbar row was already patched once (mobile-only element removal) rather than fixed at the source.
4. **Design-debt** — two WorkCard state pairs (`clean`/`fixed`, `scanning`/`findings-ready`) share identical hue, distinguished only by text label.

No code was changed. Awaiting founder decision on whether/how to proceed into Phase E.
