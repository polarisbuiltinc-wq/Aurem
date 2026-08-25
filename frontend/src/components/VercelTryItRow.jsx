/**
 * VercelTryItRow.jsx — "try a tool" dropdown row for VercelCard.
 * Extracted from VercelCard.jsx (2026-08-27, mechanical split — no
 * behaviour change) to keep that file under the platform's file-size
 * guard.
 */
import React from "react";
import { Play } from "lucide-react";

export function VercelTryItRow({ tools, tool, setTool, args, setArgs, busy, runTool, err, result }) {
  return (
    <div data-testid="vercel-try-it"
         style={{ marginTop: 12, paddingTop: 12,
                  borderTop: "1px solid var(--border)" }}>
      <div style={{
        fontSize: 10, color: "var(--text-faint)",
        textTransform: "uppercase", letterSpacing: "0.15em",
        marginBottom: 8, fontFamily: "'JetBrains Mono', monospace",
      }}>try a tool</div>
      <div style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
        <select
          data-testid="vercel-tool-select"
          value={tool}
          onChange={(e) => setTool(e.target.value)}
          style={{
            flex: "0 0 240px", padding: "8px 10px", fontSize: 12,
            background: "var(--bg-2, rgba(255,255,255,0.04))",
            color: "var(--text)", border: "1px solid var(--border)",
            borderRadius: 4, fontFamily: "'JetBrains Mono', monospace",
          }}
        >
          {tools.map((t) => (
            <option key={t.name} value={t.name}>{t.name}</option>
          ))}
        </select>
        <input
          data-testid="vercel-tool-args"
          value={args}
          onChange={(e) => setArgs(e.target.value)}
          placeholder='{"limit": 5}'
          style={{
            flex: 1, padding: "8px 10px", fontSize: 12,
            background: "var(--bg-2, rgba(255,255,255,0.04))",
            color: "var(--text)", border: "1px solid var(--border)",
            borderRadius: 4, fontFamily: "'JetBrains Mono', monospace",
          }}
        />
        <button
          data-testid="vercel-tool-run"
          onClick={runTool}
          disabled={busy}
          style={{
            padding: "8px 14px", fontSize: 12,
            background: "var(--accent, #FF6608)",
            color: "#111", border: 0, borderRadius: 4,
            cursor: busy ? "wait" : "pointer", fontWeight: 600,
            display: "inline-flex", alignItems: "center", gap: 6,
            opacity: busy ? 0.6 : 1,
          }}
        >
          <Play size={12} /> {busy ? "Running…" : "Run"}
        </button>
      </div>
      {err && (
        <div style={{
          marginTop: 8, fontSize: 11, color: "#ef4444",
          fontFamily: "'JetBrains Mono', monospace",
        }}>⚠ {err}</div>
      )}
      {result && (
        <pre data-testid="vercel-tool-result" style={{
          marginTop: 10, maxHeight: 280, overflow: "auto",
          padding: 12, borderRadius: 4, fontSize: 11,
          background: "var(--bg, #0c0c0c)", color: "var(--text-dim)",
          border: "1px solid var(--border)",
          fontFamily: "'JetBrains Mono', monospace",
          whiteSpace: "pre-wrap",
        }}>{JSON.stringify(result?.data, null, 2)}</pre>
      )}
    </div>
  );
}
