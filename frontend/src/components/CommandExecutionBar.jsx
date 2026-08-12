/**
 * CommandExecutionBar.jsx — Iter 388g (v1 minimal)
 *
 * One-line bar for command execution results in ORA chat replies.
 * Renders the `command_exec` SSE payload:
 *   { command: string, exit_code: number, ran_at: number }
 *
 * v1 scope (per founder confirmation):
 *   • ✓ (exit 0) / ✗ (non-zero) status icon
 *   • Monospace `$ cmd` truncated to fit
 *   • NO stdout/stderr expansion — that ships in a future v2 when
 *     the founder actually needs to look at process output inline.
 */
import React from "react";
import { Check, X } from "lucide-react";

const PAL = {
  bg:      "#0F1218",
  border:  "#2A2E36",
  ink:     "#E8E6DE",
  dim:     "#7A7E88",
  ok:      "#7DE0A6",
  err:     "#F79797",
  prompt:  "#B5B0A1",
};

export default function CommandExecutionBar({ command, exit_code }) {
  const ok = Number(exit_code) === 0;
  return (
    <div
      data-testid="ora-command-exec-bar"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        margin: "8px 0",
        padding: "6px 10px",
        border: `1px solid ${PAL.border}`,
        borderRadius: 6,
        background: PAL.bg,
        fontFamily: "ui-monospace, 'JetBrains Mono', Menlo, monospace",
        fontSize: 12,
        color: PAL.ink,
        overflow: "hidden",
      }}
    >
      <span
        data-testid={ok ? "cmd-ok" : "cmd-fail"}
        style={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          width: 16,
          height: 16,
          borderRadius: 3,
          background: ok ? "rgba(34,197,94,0.16)" : "rgba(239,68,68,0.16)",
          color: ok ? PAL.ok : PAL.err,
          flexShrink: 0,
        }}
      >
        {ok ? <Check size={11} strokeWidth={3} /> : <X size={11} strokeWidth={3} />}
      </span>
      <span style={{ color: PAL.prompt, flexShrink: 0 }}>$</span>
      <span
        data-testid="ora-command-exec-cmd"
        style={{
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          minWidth: 0,
          color: PAL.ink,
        }}
        title={command}
      >
        {command}
      </span>
      {!ok && (
        <span style={{ marginLeft: "auto", color: PAL.err, fontSize: 11, flexShrink: 0 }}>
          exit {exit_code}
        </span>
      )}
    </div>
  );
}
