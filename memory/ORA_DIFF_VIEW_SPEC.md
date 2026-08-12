# ORA Chat — Collapsible Diff View Spec

**Status**: Backlog · P1
**Created**: 2026-02-12 (post-Iter 388d)
**Updated**: 2026-02-12 (Iter 388f — Iter 114 reuse audit)
**Scope target**: Developer Track only. Personal Track keeps current text bubbles.

## 🔄 Relationship to Iter 114 (LiveTaskPopup) — READ FIRST

**These are DIFFERENT features but share reusable machinery.**

| | Iter 114 (already live) | This spec |
|---|---|---|
| Location | Floating side popup, `position: fixed` | Inline chat bubble in flow |
| Trigger | Async CTO task dispatched | ORA edits file during a chat turn |
| Data | Polls `GET /cto/tasks/{id}` every 2s | Structured `m.diff` streamed with message |
| Diff format | Single old/new pair per file | Full unified-diff hunks with line numbers |
| Gutter columns | ❌ | ✅ |
| Command exec bar | ❌ | ✅ |

**Existing assets to REUSE (do NOT rebuild):**
- `backend/services/task_diff.py::build_files_changed` — extend it to emit full unified-diff hunks alongside the existing `old_value`/`new_value` single-pair output. Same function powers both popup + inline bubble.
- `backend/services/task_diff.py::shape_vanguard_findings` — reuse as-is for the Vanguard row inside the diff bubble.
- Red/green palette from `LiveTaskPopup.jsx` (`#ff6b6b` / `#6dd4a1`) — reuse for line backgrounds.
- Backend tests `test_iter114_live_popup_data.py` — extend, don't parallel-create.

**Scope after reuse**: ~700 LOC → ~400 LOC (frontend `EditedFileBubble.jsx` + `CommandExecutionBar.jsx` + gutter renderer, plus one backend helper extension).

## Confirmed existing behavior (audited 2026-02-12)
- Fenced code blocks (` ```lang … ``` `) → Monaco syntax highlighting via `RenderedMessage`
- Mermaid diagrams → SVG via `MermaidBlock`
- HTML+CSS blocks → live iframe preview (right pane)
- Long assistant replies auto-collapse to one-line preview (`CollapsibleReply`)
- Loop-progress → `LoopProgressBubble` one-line summary
- **NO** collapsible "Edited /path/to/file" file-diff bubble
- **NO** red/green line-level diff colors
- **NO** old-line# / new-line# gutter columns
- **NO** command-execution bar (✓ status + truncated cmd + expand)

## Feature contract (matches attached founder screenshot)

### 1. `EditedFileBubble` component (new)
- Header: chevron (▸/▾) + "Edited " + full file path in monospace + right-aligned collapse toggle
- Body when expanded: unified-diff renderer with 2 gutter columns (old#, new#), red/green line backgrounds, colored ± markers
- Body when collapsed: hidden; header remains clickable
- Timestamp + copy button at bottom-right of expanded body
- Trigger: assistant message with a new `m.diff` payload `{ path, hunks: [{old_start, new_start, lines: [{tag: '+'|'-'|' ', text, old_n, new_n}]}] }`

### 2. `CommandExecutionBar` component (new)
- Compact one-line bar: `✓ $ cd /app/backend && python3 -m pytest tests/test_...` (truncated with `…`)
- Right side: expand `▸` toggle → shows full stdout+stderr
- Status icon: `✓` green (exit 0) / `✗` red (non-zero) / `⏳` amber (running)
- Trigger: assistant message with a new `m.exec` payload `{ cmd, exit_code, stdout, stderr, ran_at }`

### 3. Backend contract additions
- Extend the ORA chat message schema (`cto_ora_messages` or wherever assistant turns are stored) to carry optional `diff` and `exec` payloads alongside `content`.
- New helper in `services/ora_chat/tool_output_wrapper.py` (new file) that converts `/read`/`/edit`/`/run` slash-command results into these structured payloads.

### 4. Track-gate
- Read `dev_users.track` for the requesting user
- Personal Track (`track` starts with `"personal"`) → strip `m.diff` / `m.exec` on the way out, render as plain text bubble
- Developer Track (or unknown) → full diff/exec UI

### 5. Visual reference
See screenshot attached in founder message dated 2026-02-12. Match:
- Purple/pink accent for "Edited" text
- Dark background inside the diff card
- Red bg for `-` lines, green bg for `+` lines (approx `rgba(239,68,68,0.15)` / `rgba(34,197,94,0.15)`)
- Old-line# in dim gray, new-line# in dim gray with `+`/`−` prefix
- Right-side minimap column with red/green speckles

### 6. Tests
- Unit test the diff-parser (`parse_unified_diff → EditedFileBubble props`)
- Snapshot test the collapsed vs expanded render
- Regression: ensure Personal Track user does NOT see the diff UI (plain text fallback)

### 7. Non-goals
- Do NOT touch the AUREM CTO user chat (`/chat/send`) — the "confusing raw diffs for non-devs" concern applies there too.
- Do NOT try to render diffs client-side by re-parsing the assistant's markdown. Backend must emit structured payloads.

## Estimated scope
- Frontend: `EditedFileBubble.jsx`, `CommandExecutionBar.jsx`, diff-parser util (~350 LOC)
- Backend: schema addition + wrapper service (~150 LOC)
- Tests: ~200 LOC
- Total: ~700 LOC, 1-2 supervised sessions
