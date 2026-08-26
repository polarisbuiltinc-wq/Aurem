/**
 * Chip.jsx — Phase E · E2 (2026-08-27)
 *
 * Single shared primitive for every chip/pill/badge in the app. Reads
 * ALL sizing/tone from the `.chip`/`.chip-{size}`/`.chip-{tone}` token
 * classes in index.css (Phase E · E1) — no component may hardcode its
 * own height/padding/font/color. Tones mirror WorkCard's state-tone
 * model (blue→info, green→success, amber→warn, red→error, grey→neutral)
 * so color stays defined in exactly one place.
 *
 * Behind feature flag `workcard_chip_v2` (default OFF, allowlist
 * [test_admin_001]) — existing per-component inline chips are
 * untouched until a caller explicitly opts in via <Chip>.
 */
import React from "react";

const TONE_CLASS = {
  neutral: "chip-neutral",
  info:    "chip-info",
  success: "chip-success",
  warn:    "chip-warn",
  error:   "chip-error",
};

export function Chip({
  size = "sm", tone = "neutral", icon: Icon, children,
  interactive = false, onClick, title, testId, className = "",
  iconSize,
}) {
  const cls = [
    "chip",
    size === "md" ? "chip-md" : "chip-sm",
    TONE_CLASS[tone] || TONE_CLASS.neutral,
    interactive ? "chip-interactive" : "",
    className,
  ].filter(Boolean).join(" ");
  const Tag = interactive ? "button" : "span";
  return (
    <Tag
      type={interactive ? "button" : undefined}
      className={cls}
      onClick={interactive ? onClick : undefined}
      title={title}
      data-testid={testId}
    >
      {Icon && <Icon size={iconSize || (size === "md" ? 13 : 11)} />}
      {children}
    </Tag>
  );
}

/**
 * <ChipRow> — E3 count-cap rule. Renders at most `max` chips, then a
 * "+N more" chip (same <Chip> primitive) that expands inline on
 * click/hover to reveal the rest — never a modal.
 */
export function ChipRow({ children, max = 4, statusRow = false, testId }) {
  const [expanded, setExpanded] = React.useState(false);
  const items = React.Children.toArray(children).filter(Boolean);
  const visible = expanded ? items : items.slice(0, max);
  const hiddenCount = items.length - max;
  return (
    <div
      className={`chip-row${statusRow ? " chip-row-status" : ""}`}
      data-testid={testId}
    >
      {visible}
      {!expanded && hiddenCount > 0 && (
        <Chip
          size="sm" tone="neutral" interactive
          testId={testId ? `${testId}-more` : "chip-row-more"}
          className="chip-more"
          onClick={() => setExpanded(true)}
          title={`Show ${hiddenCount} more`}
        >
          +{hiddenCount} more
        </Chip>
      )}
    </div>
  );
}

/**
 * <GroupChip> — E3 group-merge rule. Collapses N related same-family
 * result chips (e.g. verify's regex/ai-review/sandbox sub-checks) into
 * ONE expandable chip. Click/hover reveals the sub-results inline.
 */
export function GroupChip({ label, passCount, total, tone = "success",
                             icon: Icon, items, testId }) {
  const [open, setOpen] = React.useState(false);
  return (
    <span style={{ display: "inline-flex", flexDirection: "column", gap: 4 }}>
      <Chip
        size="sm" tone={tone} icon={Icon} interactive
        testId={testId}
        onClick={() => setOpen((o) => !o)}
        title={items?.map((i) => i.label).join(", ")}
      >
        {label} {passCount}/{total} · details{open ? "▾" : "▸"}
      </Chip>
      {open && Array.isArray(items) && (
        <span
          className="chip-row"
          data-testid={testId ? `${testId}-detail` : undefined}
          style={{ paddingLeft: 4 }}
        >
          {items.map((it, i) => (
            <Chip key={i} size="sm" tone={it.tone || "neutral"}>
              {it.label}
            </Chip>
          ))}
        </span>
      )}
    </span>
  );
}
