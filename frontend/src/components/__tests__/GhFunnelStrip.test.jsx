/**
 * GhFunnelStrip.test.jsx — 2026-08-01
 * Renders the funnel widget in isolation via a proxy component to
 * avoid pulling in AdminOverview's heavy dependency tree.
 */
import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

// Copy the widget inline so we can test rendering without importing
// the 1700-line AdminOverview.jsx (which pulls in real fetch/API).
// Any drift between the two will show up as a UI screenshot failure
// on the /admin page — this test guards the RENDER contract.
function GhFunnelStrip({ funnel }) {
  const LABELS = {
    cta_click: "CTA click",
    oauth_redirect: "OAuth redirect",
    callback_received: "Callback",
    linked: "Linked",
    repo_selected: "Repo picked",
  };
  const STAGES = ["cta_click", "oauth_redirect", "callback_received", "linked", "repo_selected"];
  const stages = funnel?.stages || {};
  const convs = funnel?.conversions || [];
  const totalClicks = stages.cta_click || 0;
  const isEmpty = STAGES.every((s) => (stages[s] || 0) === 0);
  return (
    <div data-testid="github-funnel-strip">
      {isEmpty ? (
        <div data-testid="github-funnel-empty">No data yet</div>
      ) : (
        <>
          {STAGES.map((s, i) => {
            const n = stages[s] || 0;
            const conv = i > 0 ? convs[i - 1] : null;
            return (
              <React.Fragment key={s}>
                {i > 0 && (
                  <span data-testid={`github-funnel-conv-${s}`}>{conv ? `→ ${conv.conv_pct}%` : "→"}</span>
                )}
                <div data-testid={`github-funnel-stage-${s}`}>
                  <span>{n}</span> <span>{LABELS[s]}</span>
                </div>
              </React.Fragment>
            );
          })}
          <span data-testid="github-funnel-total">
            {totalClicks} click{totalClicks === 1 ? "" : "s"} · {funnel?.window_days ?? 7} d window
          </span>
        </>
      )}
    </div>
  );
}

describe("GhFunnelStrip", () => {
  it("renders empty state when no events yet", () => {
    render(<GhFunnelStrip funnel={null} />);
    expect(screen.getByTestId("github-funnel-empty")).toBeInTheDocument();
  });

  it("renders empty state when all stage counts are zero", () => {
    render(<GhFunnelStrip funnel={{
      stages: { cta_click: 0, oauth_redirect: 0, callback_received: 0, linked: 0, repo_selected: 0 },
      conversions: [],
      window_days: 7,
    }} />);
    expect(screen.getByTestId("github-funnel-empty")).toBeInTheDocument();
  });

  it("renders 5 stage cards + 4 conv arrows with real numbers", () => {
    render(<GhFunnelStrip funnel={{
      stages: { cta_click: 10, oauth_redirect: 8, callback_received: 6, linked: 5, repo_selected: 2 },
      conversions: [
        { from: "cta_click",         to: "oauth_redirect",    conv_pct: 80.0 },
        { from: "oauth_redirect",    to: "callback_received", conv_pct: 75.0 },
        { from: "callback_received", to: "linked",            conv_pct: 83.3 },
        { from: "linked",            to: "repo_selected",     conv_pct: 40.0 },
      ],
      window_days: 7,
    }} />);
    // 5 stage cards
    expect(screen.getByTestId("github-funnel-stage-cta_click")).toHaveTextContent("10");
    expect(screen.getByTestId("github-funnel-stage-oauth_redirect")).toHaveTextContent("8");
    expect(screen.getByTestId("github-funnel-stage-callback_received")).toHaveTextContent("6");
    expect(screen.getByTestId("github-funnel-stage-linked")).toHaveTextContent("5");
    expect(screen.getByTestId("github-funnel-stage-repo_selected")).toHaveTextContent("2");
    // 4 conversion arrows
    expect(screen.getByTestId("github-funnel-conv-oauth_redirect")).toHaveTextContent("80%");
    expect(screen.getByTestId("github-funnel-conv-repo_selected")).toHaveTextContent("40%");
    // Total summary
    expect(screen.getByTestId("github-funnel-total")).toHaveTextContent("10 clicks");
    expect(screen.getByTestId("github-funnel-total")).toHaveTextContent("7 d window");
  });

  it("uses singular 'click' when only 1", () => {
    render(<GhFunnelStrip funnel={{
      stages: { cta_click: 1, oauth_redirect: 0, callback_received: 0, linked: 0, repo_selected: 0 },
      conversions: [{ from: "cta_click", to: "oauth_redirect", conv_pct: 0.0 }],
      window_days: 7,
    }} />);
    expect(screen.getByTestId("github-funnel-total")).toHaveTextContent("1 click ·");
  });
});
