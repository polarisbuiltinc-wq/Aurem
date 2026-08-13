/**
 * RenderedMessage.jsx — Parse assistant text and render code fences via
 * Monaco (CodeBlock), plain prose as-is.
 *
 * Iter 148 — pulled out of MessageBubble so the bubble file doesn't grow
 * past the 50-line/component soft-limit. Handles:
 *   - ```lang \n code \n ``` blocks → CodeBlock
 *   - ```lang filename.ext \n code \n ``` → CodeBlock with filename
 *   - ```aurem-handoff blocks are LEFT INTACT (not parsed here) because
 *     MessageBubble has its own handoff-brief renderer + ShipDialog
 *     wiring; we only operate on text BEFORE the handoff fence
 *     (handled by the caller, see MessageBubble usage).
 *   - inline `code` spans (single backtick) → <code> with monospace
 *     styling
 *   - Iter 212m-12 — line-leading [ISSUE]/[FIX]/[OK] tags → color
 *     pills (red / green / blue) so non-technical founders can
 *     scan findings, fixes and OK-confirmations at a glance.
 *   - everything else → plain text with pre-wrap
 */
import React, { useMemo } from "react";
import CodeBlock from "./CodeBlock";

const FENCE_RE = /```([a-zA-Z0-9_+\-.]*)?[ \t]*([^\n`]*)\n([\s\S]*?)```/g;

// Iter 212m-105 — Internal orchestrator markers that must NEVER reach
// the bubble. Same list mirrored in pytest test_iter212m105_sanitize.
// `INTERNAL_FENCES` covers BOTH legacy + future tool-protocol fences.
const INTERNAL_FENCES = new Set([
  "tool_call", "tool_calls", "tool_use", "tool_result", "tool_results",
  "tool_response", "function_call", "function_result", "function_response",
  "system", "system_prompt", "internal", "orchestrator", "scratchpad",
  "thinking", "chain_of_thought",
  // Iter 388h — vendor-prefixed variants some upstream models emit
  // (LongCat, Claude, Qwen, etc.). Same protocol shape, just with a
  // provider prefix. Added after a prod user reported seeing
  // "<longcat_tool_call>read_repo_file …" leak into a chat bubble
  // during Prompt-mode replies. Kept alongside a generic-suffix
  // regex below to cover future providers we haven't seen yet.
  "longcat_tool_call", "longcat_tool_calls", "longcat_tool_use",
  "longcat_tool_result", "longcat_tool_results", "longcat_tool_response",
  "longcat_function_call", "longcat_function_result",
  "longcat_function_response", "longcat_thinking",
  "claude_tool_call", "claude_tool_use", "claude_tool_result",
  "qwen_tool_call", "qwen_tool_result",
  "gpt_tool_call", "gpt_tool_result",
]);

function sanitizeForDisplay(raw) {
  if (!raw) return "";
  let out = raw;
  // 1. Strip LOOP_PHASE:plan / :execute / :verify / :scan / :ship prefixes
  //    (the leading metadata token send() injects, never user-visible).
  out = out.replace(/^LOOP_PHASE:[a-z_]+\s*\n+/i, "");
  // 2. Strip the [Working on project: …] context preamble we auto-prepend
  //    on every prompt — internal scoping context, not content.
  out = out.replace(/^\[Working on project:[^\]]+\]\s*\n+/i, "");
  // 3. Strip XML-style internal tags the model sometimes emits when it
  //    bypasses the OpenAI function-calling protocol:
  //      <tool_call>{"name":"…"}</tool_call>
  //      <tool_result>{…}</tool_result>
  //      <thinking>…</thinking> etc.
  //    Iter 212m-106 — added after a prod user reported seeing
  //    "<tool_call>read_repo_file {"path":"…"}" leak into a chat bubble.
  //    Iter 388h — widen the tag alternation to also catch vendor-
  //    prefixed variants: `<longcat_tool_call>…`, `<claude_tool_use>…`,
  //    `<qwen_tool_result>…`, etc. Match anything ending in
  //    `_tool_call|_tool_use|_tool_result|_tool_response|
  //    _function_call|_function_result|_function_response|_thinking|
  //    _chain_of_thought` regardless of the vendor prefix.
  const _INTERNAL_TAG_RE =
    /(?:tool_call|tool_calls|tool_use|tool_result|tool_results|tool_response|function_call|function_result|function_response|thinking|chain_of_thought|scratchpad|internal|system|system_prompt|orchestrator|[a-z0-9]+_tool_call|[a-z0-9]+_tool_calls|[a-z0-9]+_tool_use|[a-z0-9]+_tool_result|[a-z0-9]+_tool_results|[a-z0-9]+_tool_response|[a-z0-9]+_function_call|[a-z0-9]+_function_result|[a-z0-9]+_function_response|[a-z0-9]+_thinking|[a-z0-9]+_chain_of_thought)/
      .source;
  out = out.replace(
    new RegExp(`<\\s*(${_INTERNAL_TAG_RE})\\b[^>]*>[\\s\\S]*?<\\s*\\/\\s*\\1\\s*>`, "gi"),
    "",
  );
  // 3b. Some models leave an opening tag without a closing one when the
  //     stream is cut. Strip orphan opens too so we don't render
  //     "<tool_call>{..." raw.
  out = out.replace(
    new RegExp(`<\\s*(${_INTERNAL_TAG_RE})\\b[^>]*>[\\s\\S]*$`, "gi"),
    "",
  );
  // 4. Strip any fenced block whose lang label is in the internal set.
  //    The fence regex must match the same shape as FENCE_RE above.
  out = out.replace(
    /```([a-zA-Z0-9_+\-.]*)[ \t]*[^\n`]*\n[\s\S]*?```/g,
    (match, lang) => INTERNAL_FENCES.has((lang || "").toLowerCase().trim()) ? "" : match,
  );
  // 5. Collapse any 3+ consecutive newlines left over from the strips
  //    so the bubble doesn't have giant gaps where the fences used to be.
  out = out.replace(/\n{3,}/g, "\n\n");
  return out.trim() ? out : "";
}

// Iter 212m-12 — line-leading color tags. Case-insensitive. The
// matching tag is stripped from the displayed text; the rest of
// the line is wrapped in a tinted pill so the user sees ONE clean
// "🔴 it's broken because…" / "🟢 fix this…" / "🔵 already fine"
// row instead of dense paragraphs. Common aliases supported too
// (Issue:/Problem:/Bug:/Error: → red; Fix:/Action:/TODO: → green;
//  OK:/Working:/Looks good: → blue) so older ORA responses still
// colorize without the assistant having to relearn the tag scheme.
const TAG_RULES = [
  {
    kind: "issue",
    re: /^(\s*)(\[ISSUE\]|Issue:|Problem:|Bug:|Error:)\s*/i,
    color:      "var(--danger)",
    background: "rgba(239, 68, 68, 0.08)",
    border:     "rgba(239, 68, 68, 0.35)",
    label:      "ISSUE",
  },
  {
    kind: "fix",
    re: /^(\s*)(\[FIX\]|Fix:|Action:|TODO:|To fix:)\s*/i,
    color:      "rgb(74, 222, 128)",
    background: "rgba(74, 222, 128, 0.08)",
    border:     "rgba(74, 222, 128, 0.35)",
    label:      "FIX",
  },
  {
    kind: "ok",
    re: /^(\s*)(\[OK\]|OK:|Working:|Looks good:|Already fine:|Verified:)\s*/i,
    color:      "rgb(96, 165, 250)",
    background: "rgba(96, 165, 250, 0.08)",
    border:     "rgba(96, 165, 250, 0.35)",
    label:      "OK",
  },
];

function splitFences(text) {
  if (!text) return [];
  const out = [];
  let lastIdx = 0;
  let m;
  // Reset regex state per call (global regex).
  FENCE_RE.lastIndex = 0;
  while ((m = FENCE_RE.exec(text)) !== null) {
    if (m.index > lastIdx) {
      out.push({ kind: "text", value: text.slice(lastIdx, m.index) });
    }
    out.push({
      kind: "code",
      lang: (m[1] || "").trim() || "plaintext",
      filename: (m[2] || "").trim() || null,
      code: m[3].replace(/\n$/, ""),
    });
    lastIdx = m.index + m[0].length;
  }
  if (lastIdx < text.length) {
    out.push({ kind: "text", value: text.slice(lastIdx) });
  }
  return out;
}

// Inline single-backtick code spans inside a plain-text segment.
function renderInline(text, keyPrefix) {
  if (!text) return null;
  // Iter 388t — Bug 21 follow-up.  Table-cell contents (and any other
  // inline text) need to parse **bold** in addition to `code`.  The
  // combined splitter captures either `code` or **bold** spans so we
  // don't recursively re-parse cells that mix both.  Everything else
  // falls through as a plain fragment.
  const parts = text.split(/(`[^`\n]+`|\*\*[^*\n][^*\n]*\*\*)/g);
  return parts.map((p, i) => {
    if (p.startsWith("`") && p.endsWith("`") && p.length > 2) {
      return (
        <code key={`${keyPrefix}-${i}`} style={{
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: 13,
          padding: "1px 5px",
          borderRadius: 3,
          background: "rgba(255,255,255,0.06)",
          color: "var(--accent-2)",
        }}>
          {p.slice(1, -1)}
        </code>
      );
    }
    if (p.startsWith("**") && p.endsWith("**") && p.length > 4) {
      return (
        <strong key={`${keyPrefix}-${i}`} style={{ fontWeight: 700 }}>
          {p.slice(2, -2)}
        </strong>
      );
    }
    return <React.Fragment key={`${keyPrefix}-${i}`}>{p}</React.Fragment>;
  });
}

// Iter 388q — Bug 21 fix.  Minimal GFM pipe-table renderer.  The
// codebase deliberately doesn't ship react-markdown / remark-gfm to
// keep the bundle small, but that meant `| Col | Col |` tables from
// the LLM landed as raw pipe-text in the bubble.  Detect a table
// block (header line + separator row + N body rows) and render an
// actual `<table>`.  Everything else falls back to the original
// line-by-line renderer below so plain prose, pill tags, and inline
// code all keep working unchanged.
const _TABLE_ROW_RE = /^\s*\|(.+)\|\s*$/;
const _TABLE_SEP_RE = /^\s*\|\s*:?-{2,}:?(?:\s*\|\s*:?-{2,}:?)+\s*\|?\s*$/;
function _parseTableRow(line) {
  // Strip leading/trailing pipe, split by unescaped `|`.
  const inner = line.trim().replace(/^\|/, "").replace(/\|\s*$/, "");
  return inner.split("|").map((c) => c.trim());
}
function _extractTables(text) {
  // Returns an array of { kind: "text"|"table", value } segments.
  const lines = text.split("\n");
  const out = [];
  let buf = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const isHeader = _TABLE_ROW_RE.test(line);
    const next = lines[i + 1] || "";
    if (isHeader && _TABLE_SEP_RE.test(next)) {
      // We found a header + separator — collect body rows.
      if (buf.length) {
        out.push({ kind: "text", value: buf.join("\n") });
        buf = [];
      }
      const header = _parseTableRow(line);
      i += 2;
      const rows = [];
      while (i < lines.length && _TABLE_ROW_RE.test(lines[i])) {
        rows.push(_parseTableRow(lines[i]));
        i += 1;
      }
      out.push({ kind: "table", value: { header, rows } });
      continue;
    }
    buf.push(line);
    i += 1;
  }
  if (buf.length) out.push({ kind: "text", value: buf.join("\n") });
  return out;
}
function TableSegment({ header, rows, keyPrefix }) {
  return (
    <table
      data-testid="rendered-md-table"
      style={{
        borderCollapse: "collapse",
        margin: "10px 0",
        fontSize: 13,
        width: "100%",
        border: "1px solid rgba(255,255,255,0.12)",
      }}
    >
      <thead>
        <tr>
          {header.map((h, i) => (
            <th
              key={`${keyPrefix}-h${i}`}
              style={{
                padding: "6px 10px",
                textAlign: "left",
                background: "rgba(255,255,255,0.06)",
                border: "1px solid rgba(255,255,255,0.12)",
                fontWeight: 700,
                fontSize: 12,
                letterSpacing: "0.02em",
              }}
            >
              {renderInline(h, `${keyPrefix}-h${i}-i`)}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, ri) => (
          <tr key={`${keyPrefix}-r${ri}`}>
            {row.map((cell, ci) => (
              <td
                key={`${keyPrefix}-r${ri}-c${ci}`}
                style={{
                  padding: "6px 10px",
                  border: "1px solid rgba(255,255,255,0.10)",
                  verticalAlign: "top",
                }}
              >
                {renderInline(cell, `${keyPrefix}-r${ri}-c${ci}-i`)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// Iter 212m-12 — wrap each text segment's lines in optional color
// pills. Lines without a tag are passed through to the existing
// inline-code renderer untouched.
function renderTextSegment(text, keyPrefix) {
  if (!text) return null;
  // Iter 388q — Bug 21 fix.  Extract GFM pipe-tables into their own
  // <table> render, and pass the rest through the existing per-line
  // pill/tag renderer.  Tables must be split BEFORE the split("\n")
  // pipeline below or the header + separator row would land in
  // separate line-fragments and lose their table shape.
  const segments = _extractTables(text);
  const hasTable = segments.some((s) => s.kind === "table");
  if (hasTable) {
    return segments.map((s, si) => {
      if (s.kind === "table") {
        return (
          <TableSegment
            key={`${keyPrefix}-t${si}`}
            header={s.value.header}
            rows={s.value.rows}
            keyPrefix={`${keyPrefix}-t${si}`}
          />
        );
      }
      // Recurse into the plain-text branch below via a single call
      // so we don't duplicate the line-pill logic.
      return (
        <React.Fragment key={`${keyPrefix}-txt${si}`}>
          {_renderTextSegmentLines(s.value, `${keyPrefix}-txt${si}`)}
        </React.Fragment>
      );
    });
  }
  return _renderTextSegmentLines(text, keyPrefix);
}

function _renderTextSegmentLines(text, keyPrefix) {
  if (!text) return null;
  const lines = text.split("\n");
  return lines.map((line, li) => {
    const rule = TAG_RULES.find((r) => r.re.test(line));
    if (!rule) {
      return (
        <React.Fragment key={`${keyPrefix}-l${li}`}>
          {renderInline(line, `${keyPrefix}-l${li}`)}
          {li < lines.length - 1 ? "\n" : null}
        </React.Fragment>
      );
    }
    const stripped = line.replace(rule.re, "$1");
    return (
      <React.Fragment key={`${keyPrefix}-l${li}`}>
        <span
          data-testid={`ora-line-${rule.kind}`}
          style={{
            display: "inline-block",
            padding: "2px 8px",
            margin: "2px 0",
            borderRadius: 4,
            background: rule.background,
            border: `1px solid ${rule.border}`,
            color: rule.color,
          }}
        >
          <span style={{
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: 10,
            fontWeight: 700,
            letterSpacing: "0.08em",
            marginRight: 8,
            opacity: 0.85,
          }}>{rule.label}</span>
          <span style={{ color: "var(--text)" }}>
            {renderInline(stripped, `${keyPrefix}-l${li}-i`)}
          </span>
        </span>
        {li < lines.length - 1 ? "\n" : null}
      </React.Fragment>
    );
  });
}

export default function RenderedMessage({ text }) {
  // Iter 212m-105 — Strip internal orchestrator markers before render.
  // Tool-call / tool-result / system_prompt fences and LOOP_PHASE
  // prefixes are MACHINE-only and were leaking into the chat bubble as
  // raw "```tool_call …```" code blocks. Sanitize at the boundary.
  const cleaned = useMemo(() => sanitizeForDisplay(text || ""), [text]);
  const segments = useMemo(() => splitFences(cleaned), [cleaned]);
  // Iter 388m — Bug 9 fix.  If the raw message was ENTIRELY internal
  // tool-call XML (some upstream models emit `<longcat_tool_call>…`
  // with no user-facing prose), `cleaned` collapses to empty and the
  // bubble showed only the attribution footer — the founder saw a
  // ghost message with no content on reload.  Detect that case and
  // render a subtle italic placeholder so the user knows the turn
  // completed with no visible reply, and can rephrase.  A legitimately
  // empty `text` (never happens for a persisted assistant row, but
  // guard anyway) still renders the empty span below so we don't spam
  // the UI with placeholders.
  const originalHadBody = !!(text && text.trim().length);
  const isStrippedToNothing = originalHadBody && !cleaned.trim();
  if (isStrippedToNothing) {
    return (
      <span
        data-testid="rendered-message-empty-placeholder"
        style={{
          fontStyle: "italic",
          opacity: 0.55,
          fontSize: 13,
        }}
      >
        (assistant emitted an internal tool call with no visible reply — try rephrasing)
      </span>
    );
  }
  if (segments.length === 0) {
    return (
      <span style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
        {renderTextSegment(cleaned, "root")}
      </span>
    );
  }
  return (
    <div data-testid="rendered-message">
      {segments.map((s, i) => {
        if (s.kind === "code") {
          // Leave aurem-handoff fences as plain text — caller renders them.
          if (s.lang === "aurem-handoff") {
            return (
              <pre key={i} style={{
                whiteSpace: "pre-wrap", margin: "8px 0",
                fontFamily: "'JetBrains Mono', monospace", fontSize: 13,
              }}>{"```aurem-handoff\n" + s.code + "\n```"}</pre>
            );
          }
          return (
            <CodeBlock
              key={i}
              language={s.lang}
              code={s.code}
              filename={s.filename}
            />
          );
        }
        return (
          <span key={i} style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
            {renderTextSegment(s.value, `t${i}`)}
          </span>
        );
      })}
    </div>
  );
}
