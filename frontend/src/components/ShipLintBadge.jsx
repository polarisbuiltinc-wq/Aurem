/**
 * components/ShipLintBadge.jsx — Iter 47
 *
 * Pre-flight lint indicator next to "Ship via CTO" button. Calls a
 * cheap backend endpoint that runs design_linter + Vanguard 007 scanner
 * on the handoff BRIEF text itself (not the generated code — that's
 * checked again server-side at commit time).
 *
 * Renders one of:
 *   - clean ✓        (green)
 *   - 2 warnings ⚠️  (amber)
 *   - blocked ⛔     (red)
 *
 * Falls back to nothing if the endpoint fails — never blocks the UI.
 */
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Chip } from "./Chip";
import { isChipV2Enabled } from "../lib/chipFlag";

export default function ShipLintBadge({ brief, testidSuffix }) {
  const [state, setState] = useState({ loading: true, result: null, error: null });

  useEffect(() => {
    let alive = true;
    if (!brief) {
      setState({ loading: false, result: null, error: null });
      return;
    }
    (async () => {
      try {
        const r = await api.post("/lint/preview", { brief });
        if (!alive) return;
        setState({ loading: false, result: r.data, error: null });
      } catch (e) {
        if (!alive) return;
        setState({ loading: false, result: null, error: e?.message || "lint failed" });
      }
    })();
    return () => { alive = false; };
  }, [brief]);

  if (state.loading || state.error || !state.result) return null;
  const r = state.result;
  let bg = "rgba(34, 197, 94, 0.15)";
  let fg = "#22c55e";
  let border = "rgba(34, 197, 94, 0.35)";
  let label = "clean ✓";
  let title = "Brief passes all linter rules.";
  let tone = "success";

  if (r.blocked) {
    // 2026-08-27 · Phase E E1 — was #ef4444 (4.24:1, below AA 4.5:1
    // text). chip-error's #fca5a5 (8.40:1) fixes this when the v2
    // primitive is on; the legacy inline fallback below is also
    // bumped to the same fixed color so non-allowlisted users get the
    // contrast fix too, even without the chip-shape migration.
    bg = "rgba(239, 68, 68, 0.15)";
    fg = "#fca5a5";
    border = "rgba(239, 68, 68, 0.4)";
    label = "blocked ⛔";
    title = (r.block_reasons || []).slice(0, 3).join("\n") || "Linter blocked this brief.";
    tone = "error";
  } else if (r.warnings && r.warnings > 0) {
    bg = "rgba(245, 158, 11, 0.15)";
    fg = "#f59e0b";
    border = "rgba(245, 158, 11, 0.4)";
    label = `${r.warnings} warning${r.warnings === 1 ? "" : "s"} ⚠️`;
    title = (r.warning_list || []).slice(0, 3).join("\n") || "Non-blocking issues found.";
    tone = "warn";
  }

  const testId = `ship-lint-badge-${testidSuffix}`;

  if (isChipV2Enabled()) {
    return <Chip size="sm" tone={tone} testId={testId} title={title}>{label}</Chip>;
  }

  return (
    <span
      data-testid={testId}
      title={title}
      className="chip chip-sm"
      style={{
        background: bg,
        color: fg,
        border: `1px solid ${border}`,
      }}
    >
      {label}
    </span>
  );
}
