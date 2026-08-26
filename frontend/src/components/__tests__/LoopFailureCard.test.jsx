/**
 * LoopFailureCard.test.jsx — Iter 362 · Bug (Verify failure surfacing).
 *
 * Locks in the contract:
 *   • When Verify fails after MAX_SELF_HEALS, the card renders the
 *     ACTUAL lint/type errors + failing files (not just a generic
 *     "Verify failed after 2 attempts").
 *   • The card is scoped to a single failure and can be dismissed
 *     via the toggle.
 *   • Copy-details button exists so users can paste errors into a
 *     bug report or a follow-up ORA prompt.
 *   • When the backend hasn't shipped rich data yet (older engine),
 *     the card still renders gracefully with only `reason`.
 */
import React from "react";
import {
  render, screen, fireEvent, cleanup,
} from "@testing-library/react";
import {
  describe, it, expect, afterEach, vi,
} from "vitest";

import LoopFailureCard from "../LoopFailureCard";

describe("LoopFailureCard — Iter 362 Bug fix", () => {
  afterEach(() => cleanup());

  it("renders phase + reason + failing files + errors in one place", () => {
    render(
      <LoopFailureCard
        phase="verify"
        reason="Verify failed after 2 self-heal attempts. Loop halted (terminal)."
        failedFiles={["backend/routers/uptime_webhook_router.py"]}
        errors={[
          "backend/routers/uptime_webhook_router.py:12:5: E402 module level import not at top of file",
          "backend/routers/uptime_webhook_router.py:18:9: F841 local variable 'x' is assigned to but never used",
        ]}
        maxSelfHeals={2}
      />,
    );

    // Title includes phase + attempt count.
    const title = screen.getByTestId("loop-failure-title");
    expect(title.textContent).toMatch(/verify failed/i);
    expect(title.textContent).toMatch(/2 self-heal attempts/i);

    // Reason line surfaces the backend's terminal message.
    const reason = screen.getByTestId("loop-failure-reason");
    expect(reason.textContent).toContain("Verify failed after 2 self-heal attempts");

    // Failing files section lists the specific file.
    expect(
      screen.getByTestId("loop-failure-file-backend/routers/uptime_webhook_router.py"),
    ).toBeInTheDocument();

    // Real lint codes make it into the DOM (this is the whole point).
    const errBlob = screen.getByTestId("loop-failure-errors").textContent;
    expect(errBlob).toMatch(/E402 module level import/);
    expect(errBlob).toMatch(/F841 local variable/);
  });

  it("collapses the detail section when toggle clicked", () => {
    render(
      <LoopFailureCard
        phase="verify"
        reason="Verify failed"
        failedFiles={["a.py"]}
        errors={["a.py:1:1: E999 SyntaxError"]}
        maxSelfHeals={2}
      />,
    );
    // Detail rows visible by default.
    expect(screen.queryByTestId("loop-failure-files")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("loop-failure-toggle"));
    expect(screen.queryByTestId("loop-failure-files")).not.toBeInTheDocument();
  });

  it("still renders gracefully when backend didn't ship data yet", () => {
    // Backwards-compat path: legacy engine on a rolling deploy may
    // emit FAILED without `failed_files`/`errors`. The card should
    // NOT crash — it should render the reason and no detail rows.
    render(<LoopFailureCard phase="verify" reason="Verify failed after 2 attempts" />);
    expect(screen.getByTestId("loop-failure-title")).toBeInTheDocument();
    expect(screen.getByTestId("loop-failure-reason").textContent)
      .toContain("Verify failed after 2 attempts");
    // No detail section, no copy button, no toggle.
    expect(screen.queryByTestId("loop-failure-files")).not.toBeInTheDocument();
    expect(screen.queryByTestId("loop-failure-errors")).not.toBeInTheDocument();
    expect(screen.queryByTestId("loop-failure-copy")).not.toBeInTheDocument();
    expect(screen.queryByTestId("loop-failure-toggle")).not.toBeInTheDocument();
  });

  it("copy-details button exists when detail is available (user can paste to a follow-up)", () => {
    render(
      <LoopFailureCard
        phase="verify"
        reason="Verify failed"
        failedFiles={["a.py"]}
        errors={["a.py:1:1: E999 SyntaxError"]}
        maxSelfHeals={2}
      />,
    );
    expect(screen.getByTestId("loop-failure-copy")).toBeInTheDocument();
  });

  it("truncates a very long single error to keep the card scannable", () => {
    const huge = "a.py:1:1: " + "X".repeat(600);
    render(
      <LoopFailureCard
        phase="verify"
        reason="Verify failed"
        failedFiles={["a.py"]}
        errors={[huge]}
        maxSelfHeals={2}
      />,
    );
    const row = screen.getByTestId("loop-failure-error-0");
    // Must be truncated (240 chars + …).
    expect(row.textContent.length).toBeLessThanOrEqual(241);
    expect(row.textContent.endsWith("…")).toBe(true);
  });

  it("non-verify phase renders a phase-appropriate title", () => {
    render(
      <LoopFailureCard
        phase="ship"
        reason="GitHub push rejected: protected branch"
      />,
    );
    const title = screen.getByTestId("loop-failure-title");
    expect(title.textContent).toMatch(/ship failed/i);
    expect(title.textContent).not.toMatch(/self-heal/i);
  });

  // 2026-08 hardening (F4) — the 3 confirmed common GitHub connection
  // failures must render a plain-language, actionable message + a
  // clickable "reconnect/connect" button instead of the raw backend
  // string with an error code in parentheses.
  it("F4-1: GitHub connection expired (revoked) shows a clear message + Reconnect action", () => {
    render(
      <LoopFailureCard
        phase="plan"
        reason="GitHub App auth failed (app_installation_revoked): App installation revoked."
      />,
    );
    expect(screen.getByTestId("loop-failure-title").textContent)
      .toMatch(/GitHub connection expired/i);
    expect(screen.getByTestId("loop-failure-reason").textContent)
      .not.toMatch(/app_installation_revoked/);
    const action = screen.getByTestId("loop-failure-github-action");
    expect(action.textContent).toMatch(/Reconnect GitHub/i);
  });

  it("F4-2: GitHub App missing shows a Connect action", () => {
    render(
      <LoopFailureCard
        phase="plan"
        reason="GitHub App auth failed (app_installation_missing): No installation found."
      />,
    );
    expect(screen.getByTestId("loop-failure-title").textContent)
      .toMatch(/GitHub not connected/i);
    expect(screen.getByTestId("loop-failure-github-action").textContent)
      .toMatch(/Connect GitHub/i);
  });

  it("F4-3: repo/branch not accessible shows an actionable check-connection message", () => {
    render(
      <LoopFailureCard
        phase="plan"
        reason="GitHub auth preflight failed (repo_not_found_or_no_access). Check the GitHub App installation for this repo, then retry."
      />,
    );
    expect(screen.getByTestId("loop-failure-title").textContent)
      .toMatch(/Repo not found/i);
    expect(screen.getByTestId("loop-failure-github-action")).toBeInTheDocument();
  });

  it("F4: the action button dispatches aurem:open-connect-repo when clicked", () => {
    const handler = vi.fn();
    window.addEventListener("aurem:open-connect-repo", handler);
    render(
      <LoopFailureCard
        phase="plan"
        reason="GitHub App auth failed (app_installation_missing): No installation found."
      />,
    );
    fireEvent.click(screen.getByTestId("loop-failure-github-action"));
    expect(handler).toHaveBeenCalledTimes(1);
    window.removeEventListener("aurem:open-connect-repo", handler);
  });

  it("F4: an unrelated failure reason is NOT mistaken for a GitHub connection error", () => {
    render(
      <LoopFailureCard
        phase="execute"
        reason="Code generation failed: something else entirely"
      />,
    );
    expect(screen.queryByTestId("loop-failure-github-action")).not.toBeInTheDocument();
  });
});
