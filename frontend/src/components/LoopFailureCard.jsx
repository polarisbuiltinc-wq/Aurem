/**
 * LoopFailureCard.jsx — Iter 362 · Bug (Verify failure surfacing).
 *
 * Founder-reported (P1): "When Verify fails after exhausting self-heal
 * attempts, the chat UI only shows a generic 'Verify failed after 2
 * attempts' message. The user has zero information to diagnose or
 * manually fix — they can't retry with a more specific instruction."
 *
 * This card surfaces the actual lint/type-check errors + failing
 * files from the terminal FAILED event so the user can either:
 *   (a) fix the file directly, or
 *   (b) send ORA a follow-up like "add the validation right after
 *       the secret check on line 42".
 *
 * The card is presentational — its job is to make invisible failure
 * data visible. It renders when `open` is true; parent (ChatPanel)
 * decides when to open/close based on the terminal FAILED SSE event.
 */
import React, { useState } from "react";
import {
  AlertTriangle, Copy, ChevronDown, ChevronUp, FileCode, LinkIcon,
} from "lucide-react";
import { translateGithubConnectError } from "../lib/githubConnectErrors";

function shortenErr(s) {
  if (typeof s !== "string") return "";
  // Trim leading whitespace/newlines and cap at 240 chars per line —
  // the raw ruff/eslint output can be verbose; we want scannable rows.
  const t = s.trim();
  return t.length > 240 ? t.slice(0, 237) + "…" : t;
}

export default function LoopFailureCard({
  phase,
  reason,
  failedFiles = [],
  errors = [],
  maxSelfHeals,
}) {
  const [expanded, setExpanded] = useState(true);
  const [copied, setCopied] = useState(false);

  // 2026-08 hardening (F4) — plain-language, actionable message for
  // the 3 confirmed common GitHub connection failures.
  const ghError = translateGithubConnectError(reason);

  const hasDetail = (failedFiles && failedFiles.length > 0)
                 || (errors && errors.length > 0);

  const handleCopy = async () => {
    const payload = [
      `Loop failed at phase: ${phase || "?"}`,
      reason ? `Reason: ${reason}` : "",
      failedFiles.length ? `Failing files:\n${failedFiles.map((f) => "  - " + f).join("\n")}` : "",
      errors.length ? `Errors:\n${errors.map((e) => "  " + e).join("\n")}` : "",
    ].filter(Boolean).join("\n\n");
    try {
      await navigator.clipboard.writeText(payload);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch { /* clipboard unavailable in some contexts */ }
  };

  return (
    <div
      data-testid="loop-failure-card"
      data-phase={phase || ""}
      role="region"
      aria-label={`Loop failed at ${phase || "unknown"} phase`}
      style={{
        margin: "10px 12px",
        padding: 14,
        background: "linear-gradient(135deg, rgba(239,68,68,0.10), rgba(220,38,38,0.04))",
        border: "1px solid rgba(239, 68, 68, 0.4)",
        borderRadius: 12,
        display: "flex", flexDirection: "column", gap: 10,
        fontFamily: "'JetBrains Mono', monospace",
        boxShadow: "0 0 28px -10px rgba(239, 68, 68, 0.45)",
      }}
    >
      <div style={{
        display: "flex", alignItems: "center", gap: 8,
      }}>
        <AlertTriangle size={14} color="#f87171" strokeWidth={2.5} />
        <strong
          data-testid="loop-failure-title"
          style={{
            fontSize: 12, color: "#fca5a5", letterSpacing: 0.4,
            textTransform: "uppercase",
          }}
        >
          {ghError
            ? ghError.title
            : phase === "verify"
            ? `Verify failed${
                typeof maxSelfHeals === "number"
                  ? ` after ${maxSelfHeals} self-heal attempts`
                  : ""}`
            : `${(phase || "Pipeline").replace(/^[a-z]/, (c) => c.toUpperCase())} failed`}
        </strong>
        <span style={{ flex: 1 }} />
        {hasDetail && (
          <button
            type="button"
            data-testid="loop-failure-toggle"
            onClick={() => setExpanded((v) => !v)}
            aria-label={expanded ? "Hide details" : "Show details"}
            style={{
              background: "transparent", border: "none",
              color: "#c2c9d6", cursor: "pointer", padding: 2,
              display: "inline-flex",
            }}
          >
            {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>
        )}
      </div>

      {reason && (
        <div
          data-testid="loop-failure-reason"
          style={{
            fontSize: 11.5, color: "#c2c9d6", lineHeight: 1.55,
          }}
        >
          {ghError ? ghError.message : reason}
        </div>
      )}

      {ghError && (
        <button
          type="button"
          data-testid="loop-failure-github-action"
          onClick={() => window.dispatchEvent(new CustomEvent("aurem:open-connect-repo"))}
          style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            alignSelf: "flex-start",
            padding: "7px 14px",
            background: "#f87171", color: "#1a1a1a",
            border: "none", borderRadius: 8,
            fontSize: 11.5, fontWeight: 700,
            cursor: "pointer",
          }}
        >
          <LinkIcon size={12} strokeWidth={2.5} />
          {ghError.actionLabel}
        </button>
      )}

      {hasDetail && expanded && (
        <>
          {failedFiles && failedFiles.length > 0 && (
            <div data-testid="loop-failure-files">
              <div style={{
                fontSize: 10, color: "#9aa3b2",
                textTransform: "uppercase", letterSpacing: 0.6,
                marginBottom: 4,
              }}>
                Failing files ({failedFiles.length})
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                {failedFiles.map((f) => (
                  <div key={f}
                       data-testid={`loop-failure-file-${f}`}
                       style={{
                         display: "inline-flex", alignItems: "center",
                         gap: 6, fontSize: 11, color: "#e6ebf3",
                       }}>
                    <FileCode size={11} color="#f87171" strokeWidth={2.5} />
                    <code>{f}</code>
                  </div>
                ))}
              </div>
            </div>
          )}
          {errors && errors.length > 0 && (
            <div data-testid="loop-failure-errors">
              <div style={{
                fontSize: 10, color: "#9aa3b2",
                textTransform: "uppercase", letterSpacing: 0.6,
                marginBottom: 4,
              }}>
                Lint / type errors ({errors.length})
              </div>
              <div style={{
                display: "flex", flexDirection: "column", gap: 2,
                background: "rgba(0,0,0,0.35)",
                border: "1px solid rgba(255,255,255,0.06)",
                borderRadius: 6,
                padding: "8px 10px",
                maxHeight: 220, overflow: "auto",
                fontFamily: "'JetBrains Mono', ui-monospace, monospace",
                fontSize: 10.5, color: "#e6ebf3",
                whiteSpace: "pre-wrap", wordBreak: "break-word",
              }}>
                {errors.slice(0, 25).map((e, i) => (
                  <div
                    key={i}
                    data-testid={`loop-failure-error-${i}`}
                    style={{
                      color: "#fca5a5",
                      borderBottom: i < Math.min(errors.length, 25) - 1
                        ? "1px dashed rgba(255,255,255,0.06)" : "none",
                      paddingBottom: 3, marginBottom: 3,
                    }}
                  >
                    {shortenErr(e)}
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}

      {hasDetail && (
        <div style={{
          display: "flex", gap: 8, alignItems: "center",
          marginTop: 4,
        }}>
          <button
            type="button"
            data-testid="loop-failure-copy"
            onClick={handleCopy}
            style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              padding: "5px 10px",
              background: "transparent",
              color: "#c2c9d6",
              border: "1px solid rgba(255,255,255,0.15)",
              borderRadius: 6,
              fontSize: 10.5, fontWeight: 500,
              cursor: "pointer",
              textTransform: "uppercase", letterSpacing: 0.04,
            }}
          >
            <Copy size={11} strokeWidth={2.5} />
            {copied ? "Copied" : "Copy details"}
          </button>
          <span style={{
            fontSize: 10.5, color: "#8b949e",
          }}>
            {phase === "verify"
              ? "Fix manually and re-run, or send a follow-up like: “add the validation right after the existing secret check”."
              : "Read the errors above; then either fix manually or refine your task."}
          </span>
        </div>
      )}
    </div>
  );
}
