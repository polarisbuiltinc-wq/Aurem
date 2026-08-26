/**
 * TaskProgressCard.jsx — Live commit / Ship task progress card.
 *
 * Renders one of three states for a CTO worker task:
 *   1. Running   — animated stage label + last 2 worker steps
 *   2. Failed    — red card with Retry button + truncated error text
 *   3. Success   — green card with commit SHA link, files changed,
 *                  "View diff" + "Rollback" actions.
 *
 * Props:
 *   taskId      (string)   — CTO task UUID
 *   task        (object?)  — polled task row {status, steps, commit_sha, …}
 *   project     (object?)  — {github_owner, github_repo, branch}
 *   onRollback  (fn)       — fired when user confirms rollback
 *
 * Iter 62: extracted from ChatPanel.jsx as part of the P1 split.
 */
import React, { useState } from "react";
import { Loader2, ExternalLink, Undo2 } from "lucide-react";
import { api } from "../lib/api";
import { toast } from "./Toast";
import { SupportButton } from "./SupportPopup";

const STAGES = [
  { key: "pulling", label: "Cloning…",          icon: "📡" },
  { key: "reading", label: "Reading files…",    icon: "📄" },
  { key: "fixing",  label: "AI thinking…",      icon: "🧠" },
  { key: "pushing", label: "Writing & pushing…", icon: "🚀" },
  { key: "done",    label: "Pushed",             icon: "✅" },
];

function FailedCard({ taskId, task, project, onOpenLivePopup }) {
  const [retrying, setRetrying] = useState(false);
  async function retry() {
    if (retrying) return;
    setRetrying(true);
    try {
      const r = await api.post(`/cto/tasks/${taskId}/retry`, {});
      const newTaskId = r?.data?.task_id;
      if (newTaskId) {
        toast({ message: "Re-queued", kind: "success" });
      } else {
        // 2026-08-23 — if the API ever stops returning task_id, fail
        // LOUDLY instead of silently recreating the exact "re-queued
        // but nothing visibly happens" bug this fix was for.
        toast({ message: "Re-queued, but couldn't open live progress "
          + "— refresh to check status", kind: "error" });
      }
      // 2026-08-23 — BUG FIX (founder-reported): the retry DID work on
      // the backend (a real new task was queued + a background worker
      // scheduled), but nothing here ever showed the user its progress
      // — the UI kept displaying this same old FAILED card forever,
      // looking exactly like "re-queued but doing nothing". Open the
      // live progress popup for the NEW task so retries are visibly
      // alive.
      if (newTaskId) onOpenLivePopup?.(newTaskId);
    } catch (e) {
      toast({
        message: e?.response?.data?.detail || "Retry failed",
        kind: "error",
      });
    } finally {
      setRetrying(false);
    }
  }
  // Iter 212m-12 — show the friendly translation when the backend
  // has populated it, falling back to the raw error otherwise. The
  // raw error is collapsed behind a "Show technical details"
  // toggle so non-technical founders aren't staring at stack
  // traces.
  const [showRaw, setShowRaw] = useState(false);
  const plain = task.error_plain || "";
  const steps = Array.isArray(task.error_steps) ? task.error_steps : [];
  const suggestion = task.error_suggestion || "";
  // 2026-08-25 — Phase 3.5: a task that keeps failing with the SAME
  // signature on the SAME project is a deterministic code bug, not a
  // transient blip — blind Retry can never fix it. Surface that
  // distinction instead of offering an identical Retry for every
  // failure kind.
  const repeatCount = task.failure_repeat_count || 0;
  const isRepeat = repeatCount >= 2;
  const isDeterministic = task.error_category === "internal";

  return (
    <div data-testid={`ship-status-${taskId}`} role="status" aria-live="polite" style={{
      padding: "10px 12px",
      background: "rgba(255,107,107,0.06)",
      border: "1px solid rgba(255,107,107,0.3)",
      borderRadius: 4,
      fontSize: 12, color: "var(--danger)",
      fontFamily: "'JetBrains Mono', monospace",
      maxWidth: 460,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
        <span>❌ Task failed · <span style={{ opacity: 0.7 }}>{taskId}</span></span>
        <button
          data-testid={`ship-retry-${taskId}`}
          onClick={retry}
          disabled={retrying}
          className="btn-ghost"
          style={{
            padding: "2px 8px", fontSize: 10,
            borderColor: "rgba(255,107,107,0.5)",
            color: "var(--danger)",
          }}
        >
          {retrying ? "Re-queuing…" : "↻ Retry"}
        </button>
      </div>

      {isRepeat && (
        <div
          data-testid={`ship-repeat-warning-${taskId}`}
          style={{
            marginTop: 8, padding: "6px 10px",
            background: "rgba(255,197,96,0.1)",
            border: "1px solid rgba(255,197,96,0.4)",
            borderRadius: 4,
            fontSize: 11, color: "var(--accent-2)",
            fontFamily: "inherit", lineHeight: 1.5,
          }}
        >
          <div>
            ⚠️ This exact failure has happened {repeatCount}× on this project.
            Retrying is unlikely to change the outcome — try rephrasing the
            task, or contact support if it keeps happening.
          </div>
          {/* 2026-08-24 · Guard 22 — Phase 6.1/6.2 blueprint gap: the
              failure-signature detection + repeat-count already
              existed and was already displayed here as plain text.
              The one real missing piece was routing — "contact
              support" was never a real link. Reuses the SAME
              SupportPopup/POST /support/tickets path every other
              support entry point in the app already uses, just
              pre-filled with the real task context so the founder
              doesn't have to ask the user to paste a task ID. */}
          <div style={{ marginTop: 8 }}>
            <SupportButton
              source="task_repeat_failure"
              label="✉ Contact support about this"
              style={{ fontSize: 11, padding: "5px 12px" }}
              initialBody={
                `My task keeps failing the same way (${repeatCount}x) and Retry isn't fixing it.\n\n` +
                `Task ID: ${taskId}\n` +
                (project?.github_owner && project?.github_repo
                  ? `Repo: ${project.github_owner}/${project.github_repo}\n` : "") +
                (task.failure_signature ? `Failure signature: ${task.failure_signature}\n` : "") +
                (plain ? `\nWhat AUREM told me: ${plain}\n` : "") +
                `\n(please add anything else that would help — what you were trying to do, etc.)`
              }
            />
          </div>
        </div>
      )}

      {!isRepeat && isDeterministic && (
        <div
          data-testid={`ship-deterministic-note-${taskId}`}
          style={{
            marginTop: 8, fontSize: 11, color: "var(--text-faint)",
            fontFamily: "inherit", lineHeight: 1.5,
          }}
        >
          This looks like a backend/code issue rather than a network blip —
          Retry may reproduce it.
        </div>
      )}


      {plain && (
        <div
          data-testid={`ship-error-plain-${taskId}`}
          style={{ marginTop: 8, fontSize: 13, color: "var(--text)",
                   lineHeight: 1.55, fontFamily: "inherit" }}
        >
          {plain}
        </div>
      )}

      {steps.length > 0 && (
        <ol
          data-testid={`ship-error-steps-${taskId}`}
          style={{
            margin: "8px 0 4px",
            paddingLeft: 22,
            fontSize: 12,
            color: "var(--text-dim)",
            fontFamily: "inherit",
            lineHeight: 1.55,
          }}
        >
          {steps.map((s, i) => (
            <li key={i} style={{ marginBottom: 3 }}>{s}</li>
          ))}
        </ol>
      )}

      {suggestion && (
        <div
          data-testid={`ship-error-suggestion-${taskId}`}
          style={{
            marginTop: 6, padding: "6px 10px",
            background: "rgba(255,197,96,0.08)",
            border: "1px solid rgba(255,197,96,0.3)",
            borderRadius: 4,
            fontSize: 11, color: "var(--accent-2)",
            fontFamily: "inherit", lineHeight: 1.5,
          }}
        >
          💡 {suggestion}
        </div>
      )}

      {task.error && (
        <div style={{ marginTop: 8 }}>
          {(task.error_code || task.ref_id) && (
            <div
              data-testid={`ship-error-ref-${taskId}`}
              style={{
                fontSize: 10, color: "var(--text-faint)",
                fontFamily: "'JetBrains Mono', monospace",
                marginBottom: 4,
              }}
            >
              {task.error_code && <span>{task.error_code}</span>}
              {task.error_code && task.ref_id && <span> · </span>}
              {task.ref_id && <span>ref: {task.ref_id}</span>}
            </div>
          )}
          <button
            data-testid={`ship-show-raw-${taskId}`}
            onClick={() => setShowRaw((v) => !v)}
            className="btn-ghost"
            style={{
              padding: "2px 6px", fontSize: 10,
              color: "var(--text-faint)",
              borderColor: "var(--border)",
            }}
          >
            {showRaw ? "▾ Hide technical details" : "▸ Show technical details"}
          </button>
          {showRaw && (
            <pre
              data-testid={`ship-error-raw-${taskId}`}
              style={{
                marginTop: 6, padding: "8px 10px",
                background: "var(--bg)", color: "var(--text-faint)",
                border: "1px solid var(--border)", borderRadius: 4,
                fontSize: 10, lineHeight: 1.45,
                whiteSpace: "pre-wrap", wordBreak: "break-word",
                fontFamily: "'JetBrains Mono', monospace",
                maxHeight: 220, overflow: "auto",
              }}
            >
              {String(task.error).slice(0, 1200)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

// 2026-08-26 · Ship/Commit Robustness — a guard firing (e.g. the
// Iter 286 test-file lock) is a SUCCESS of the guard, never a
// failure. Distinct amber/neutral card so it never looks like the
// red FailedCard above.
function BlockedCard({ taskId, task }) {
  const reason = task.blocked_reason || "";
  const paths = Array.isArray(task.blocked_paths) ? task.blocked_paths : [];
  return (
    <div data-testid={`ship-status-${taskId}`} role="status" aria-live="polite" style={{
      padding: "10px 12px",
      background: "rgba(255,197,96,0.08)",
      border: "1px solid rgba(255,197,96,0.35)",
      borderRadius: 4,
      fontSize: 12, color: "var(--accent-2)",
      fontFamily: "'JetBrains Mono', monospace",
      maxWidth: 460,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
        <span>⏸ Awaiting your approval · <span style={{ opacity: 0.7 }}>{taskId}</span></span>
      </div>
      <div data-testid={`ship-blocked-reason-${taskId}`}
           style={{ marginTop: 8, fontSize: 12, color: "var(--text)", lineHeight: 1.5 }}>
        {reason === "test_file_lock"
          ? "This edit touches a test file, so it's held for review before shipping — the fix is ready."
          : (reason || "This change is held for review before shipping.")}
      </div>
      {paths.length > 0 && (
        <div data-testid={`ship-blocked-paths-${taskId}`}
             style={{ marginTop: 6, fontSize: 11, color: "var(--text-dim)" }}>
          {paths.map((p) => `\`${p}\``).join(", ")}
        </div>
      )}
      <div style={{ marginTop: 10 }}>
        {/* Build Prompt v4 · Phase C item 11 — a real actionable route,
            not just static copy. Reuses the SAME cross-component
            signalling pattern activeProject.js already uses
            (window CustomEvent) instead of drilling a callback prop
            through MessageBubble → TaskProgressCard. ChatPanel listens
            for this event and calls the existing handleExecModeChange. */}
        <button
          data-testid={`ship-route-to-loop-${taskId}`}
          onClick={() => window.dispatchEvent(
            new CustomEvent("aurem:route-to-loop", { detail: { taskId } })
          )}
          className="btn-ghost"
          style={{
            padding: "5px 10px", fontSize: 11,
            borderColor: "rgba(255,197,96,0.5)",
            color: "var(--accent-2)",
          }}
        >
          Route via Loop mode
        </button>
      </div>
    </div>
  );
}

export default function TaskProgressCard({ taskId, task, project, onRollback, onOpenLivePopup }) {
  const status = task?.status || "queued";
  const rbStatus = task?.rollback_status;
  const rbRunning = rbStatus === "queued" || rbStatus === "running";

  // While running
  if (!task || (status !== "done" && status !== "failed" && status !== "blocked")) {
    const stageIdx = STAGES.findIndex((s) => s.key === status);
    const current = stageIdx >= 0 ? STAGES[stageIdx] : { icon: "⏳", label: status };
    return (
      <div data-testid={`ship-status-${taskId}`} role="status" aria-live="polite" style={{
        padding: "10px 12px",
        background: "var(--panel-2)",
        border: "1px solid var(--border)",
        borderRadius: 4,
        fontSize: 12, color: "var(--text-dim)",
        fontFamily: "'JetBrains Mono', monospace",
        minWidth: 260,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Loader2 size={12} style={{ animation: "spin 1s linear infinite", color: "var(--info)" }} />
          <span style={{ color: "var(--info)" }}>{current.icon} {current.label}</span>
          <span style={{ marginLeft: "auto", color: "var(--text-faint)", fontSize: 10 }}>{taskId}</span>
        </div>
        {(task?.steps || []).slice(-2).map((s, i) => (
          <div key={i} style={{
            marginTop: 4, fontSize: 10,
            color: s.status === "error" ? "var(--danger)" : "var(--text-faint)",
          }}>
            {s.step}
          </div>
        ))}
      </div>
    );
  }

  // Blocked (guard fired correctly) — own sub-component, never FailedCard
  if (status === "blocked") {
    return <BlockedCard taskId={taskId} task={task} />;
  }

  // Failed — own sub-component so hooks stay top-level
  if (status === "failed") {
    return <FailedCard taskId={taskId} task={task} project={project} onOpenLivePopup={onOpenLivePopup} />;
  }

  // Success
  const sha = task.commit_sha;
  const owner = project?.github_owner;
  const repo = project?.github_repo;
  const commitUrl = sha && owner && repo
    ? `https://github.com/${owner}/${repo}/commit/${sha}`
    : null;

  const files = (task.steps || [])
    .filter((s) => (s.step || "").startsWith("💾"))
    .map((s) => s.step.replace(/^💾\s*/, ""));

  const reverted = !!task.rollback_sha;

  return (
    <div data-testid={`ship-status-${taskId}`} role="status" aria-live="polite" style={{
      padding: "12px 14px",
      background: reverted ? "var(--panel-2)" : "rgba(0, 230, 118, 0.05)",
      border: `1px solid ${reverted ? "var(--border)" : "rgba(0,230,118,0.3)"}`,
      borderRadius: 4,
      fontSize: 12,
      maxWidth: 460,
    }}>
      <div style={{
        display: "flex", alignItems: "center", gap: 8,
        fontFamily: "'JetBrains Mono', monospace",
        color: reverted ? "var(--text-dim)" : "var(--ok)",
        fontWeight: 600,
      }}>
        {reverted ? "↩︎ Reverted" : "✅ Pushed"}
        {sha && (commitUrl ? (
          <a href={commitUrl} target="_blank" rel="noreferrer"
             data-testid={`ship-commit-link-${taskId}`}
             style={{ color: "var(--accent-2)", textDecoration: "none" }}
             title="View commit on GitHub">
            {sha} <ExternalLink size={10} style={{ display: "inline" }} />
          </a>
        ) : <span>{sha}</span>)}
        {task.rollback_sha && (
          <span style={{ color: "var(--text-faint)", marginLeft: 4 }}>
            → {task.rollback_sha}
          </span>
        )}
      </div>

      {task.result && (
        <div style={{ marginTop: 6, color: "var(--text)" }}>{task.result}</div>
      )}

      {files.length > 0 && (
        <div style={{ marginTop: 8, fontSize: 11, color: "var(--text-dim)" }}>
          <div style={{ marginBottom: 2, color: "var(--text-faint)", letterSpacing: "0.05em" }}>
            FILES CHANGED
          </div>
          {files.slice(0, 4).map((f, i) => (
            <div key={i} style={{ fontFamily: "'JetBrains Mono', monospace" }}>
              • {f}
            </div>
          ))}
          {files.length > 4 && (
            <div style={{ color: "var(--text-faint)" }}>+ {files.length - 4} more</div>
          )}
        </div>
      )}

      <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
        {commitUrl && (
          <a href={commitUrl} target="_blank" rel="noreferrer"
             className="btn-ghost"
             style={{ padding: "5px 10px", fontSize: 11, textDecoration: "none" }}>
            <ExternalLink size={11} /> View diff
          </a>
        )}
        {!reverted && !rbRunning && (
          <button
            data-testid={`ship-rollback-${taskId}`}
            onClick={onRollback}
            className="btn-ghost"
            style={{
              padding: "5px 10px", fontSize: 11,
              borderColor: "rgba(255,107,107,0.3)",
              color: "var(--danger)",
            }}
          >
            <Undo2 size={11} /> Rollback
          </button>
        )}
        {rbRunning && (
          <span style={{ fontSize: 11, color: "var(--accent-2)",
                         display: "inline-flex", alignItems: "center", gap: 6 }}>
            <Loader2 size={11} style={{ animation: "spin 1s linear infinite" }} /> reverting…
          </span>
        )}
      </div>
    </div>
  );
}
