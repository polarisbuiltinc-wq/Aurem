/**
 * EditedFileBubble.jsx — Iter 388g
 *
 * Collapsible per-file diff bubble for ORA chat replies (Path A).
 * Renders the `edited_files` SSE payload emitted by the backend:
 *   { path: string, hunks: [
 *       { old_start, new_start, lines: [
 *           { tag: ' '|'+'|'-', text, old_n, new_n }, ...
 *       ]}
 *   ]}
 *
 * Layout matches the founder's target screenshot:
 *   • Header row: chevron ▸/▾ + "Edited " + file path (monospace)
 *   • Body: unified-diff table with TWO gutter columns (old#, new#)
 *   • Red background for `-` lines, green background for `+` lines
 *   • Context (` `) rows: transparent bg, dim gutters
 *
 * v1 keeps things simple:
 *   • No minimap (backlog)
 *   • No syntax highlight inside the diff (backlog — hooks up to
 *     the existing Monaco/Streamdown code-fence infra when we
 *     want to gild this later)
 *   • Collapsed by default when hunks > 3 (avoid a wall of green
 *     on a big new-file diff); expanded by default for small edits.
 */
import React, { useState } from "react";
import { ChevronRight, ChevronDown, FileText } from "lucide-react";

const PAL = {
  bg:            "#0F1218",
  border:        "#2A2E36",
  ink:           "#E8E6DE",
  dim:           "#7A7E88",
  chevron:       "#B5B0A1",
  headerHover:   "rgba(255,255,255,0.03)",
  addBg:         "rgba(34,197,94,0.14)",
  addGutter:     "rgba(34,197,94,0.22)",
  addInk:        "#7DE0A6",
  delBg:         "rgba(239,68,68,0.14)",
  delGutter:     "rgba(239,68,68,0.22)",
  delInk:        "#F79797",
  ctxInk:        "#B5B0A1",
  gutterInk:     "#5A5E68",
};

function DiffLine({ tag, text, old_n, new_n }) {
  const isAdd = tag === "+";
  const isDel = tag === "-";
  const rowStyle = {
    display: "grid",
    gridTemplateColumns: "40px 40px 14px 1fr",
    background: isAdd ? PAL.addBg : isDel ? PAL.delBg : "transparent",
    fontFamily: "ui-monospace, 'JetBrains Mono', Menlo, monospace",
    fontSize: 12,
    lineHeight: "18px",
  };
  const gutterBase = {
    padding: "0 6px",
    textAlign: "right",
    color: PAL.gutterInk,
    userSelect: "none",
    borderRight: `1px solid ${PAL.border}`,
    background: isAdd
      ? PAL.addGutter
      : isDel
      ? PAL.delGutter
      : "transparent",
  };
  const markStyle = {
    padding: "0 4px",
    color: isAdd ? PAL.addInk : isDel ? PAL.delInk : PAL.dim,
    fontWeight: 600,
    userSelect: "none",
  };
  const textStyle = {
    padding: "0 8px",
    color: isAdd ? PAL.addInk : isDel ? PAL.delInk : PAL.ctxInk,
    whiteSpace: "pre",
    overflowX: "auto",
  };
  return (
    <div style={rowStyle} data-testid={`diff-line-${tag === "+" ? "add" : tag === "-" ? "del" : "ctx"}`}>
      <span style={gutterBase}>{old_n ?? ""}</span>
      <span style={gutterBase}>{new_n ?? ""}</span>
      <span style={markStyle}>{tag === " " ? "" : tag}</span>
      <span style={textStyle}>{text}</span>
    </div>
  );
}

export default function EditedFileBubble({ file }) {
  const path = file?.path || "";
  const hunks = Array.isArray(file?.hunks) ? file.hunks : [];
  const totalLines = hunks.reduce((n, h) => n + (h.lines?.length || 0), 0);
  // Auto-collapse when very large (>3 hunks OR >120 diff lines).
  const [open, setOpen] = useState(
    !(hunks.length > 3 || totalLines > 120)
  );

  return (
    <div
      data-testid="ora-edited-file-bubble"
      style={{
        margin: "10px 0",
        border: `1px solid ${PAL.border}`,
        borderRadius: 8,
        background: PAL.bg,
        overflow: "hidden",
      }}
    >
      {/* Header row — clickable, chevron toggles body */}
      <button
        type="button"
        data-testid="ora-edited-file-toggle"
        onClick={() => setOpen((v) => !v)}
        style={{
          width: "100%",
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "8px 12px",
          background: "transparent",
          border: "none",
          borderBottom: open ? `1px solid ${PAL.border}` : "none",
          color: PAL.ink,
          cursor: "pointer",
          fontFamily: "ui-monospace, 'JetBrains Mono', Menlo, monospace",
          fontSize: 12,
          textAlign: "left",
        }}
        onMouseEnter={(e) => (e.currentTarget.style.background = PAL.headerHover)}
        onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
      >
        {open ? (
          <ChevronDown size={14} color={PAL.chevron} />
        ) : (
          <ChevronRight size={14} color={PAL.chevron} />
        )}
        <FileText size={13} color={PAL.dim} />
        <span style={{ color: PAL.dim }}>Edited</span>
        <span data-testid="ora-edited-file-path" style={{ color: PAL.ink, fontWeight: 500 }}>
          {path}
        </span>
        <span style={{ marginLeft: "auto", color: PAL.dim, fontSize: 11 }}>
          {hunks.length} hunk{hunks.length === 1 ? "" : "s"} · {totalLines} lines
        </span>
      </button>

      {/* Body — hunks */}
      {open && (
        <div data-testid="ora-edited-file-body">
          {hunks.map((h, hi) => (
            <div key={hi}>
              {hi > 0 && (
                <div
                  style={{
                    padding: "2px 8px",
                    color: PAL.gutterInk,
                    fontSize: 10,
                    fontFamily: "ui-monospace, monospace",
                    background: "rgba(255,255,255,0.02)",
                    borderTop: `1px solid ${PAL.border}`,
                    borderBottom: `1px solid ${PAL.border}`,
                  }}
                >
                  @@ -{h.old_start} +{h.new_start} @@
                </div>
              )}
              {(h.lines || []).map((l, li) => (
                <DiffLine
                  key={`${hi}-${li}`}
                  tag={l.tag}
                  text={l.text}
                  old_n={l.old_n}
                  new_n={l.new_n}
                />
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
