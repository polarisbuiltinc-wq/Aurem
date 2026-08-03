/**
 * PreviewPanel.live_url_fallback.test.jsx — Feb 2026
 *
 * Founder-reported bug: The Preview tab's "Live Site" view showed
 * the raw URL as text instead of an embedded iframe.
 *
 * Root cause: The synthetic-live-block generator inside PreviewPanel
 * only looked at `activeProject.preview_url` (user-supplied). For
 * Personal Track builds Vercel auto-populates `activeProject.live_url`
 * and never sets `preview_url` — so the synthetic block was never
 * created and the panel fell through to a codebase file or raw text.
 *
 * Fix: fall back through both fields, trim whitespace, and validate
 * the scheme. This test locks in that contract.
 */
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("../../lib/api", () => ({
  api: { get: vi.fn(async () => ({ data: {} })) },
}));

import PreviewPanel from "../PreviewPanel.jsx";

describe("PreviewPanel — Live Site iframe synthesis", () => {
  it("synthesises a live_url block from activeProject.live_url when preview_url is missing", () => {
    render(
      <PreviewPanel
        blocks={[]}
        activeProject={{
          project_id: "pt_test123",
          live_url:   "https://my-app.vercel.app",
          // preview_url intentionally missing — Vercel-deployed
          // Personal Track projects only carry live_url.
        }}
        onClose={() => {}}
      />,
    );
    // The Live Site tab must exist and the iframe must render with
    // the live_url as its src (not a <pre> with the URL as text).
    const iframe = screen.getByTestId("preview-iframe-live");
    expect(iframe).toBeTruthy();
    expect(iframe.getAttribute("src")).toBe("https://my-app.vercel.app");
  });

  it("prefers preview_url over live_url when both are set", () => {
    render(
      <PreviewPanel
        blocks={[]}
        activeProject={{
          project_id:  "pt_test123",
          preview_url: "https://user-set.example.com",
          live_url:    "https://vercel-auto.vercel.app",
        }}
        onClose={() => {}}
      />,
    );
    const iframe = screen.getByTestId("preview-iframe-live");
    expect(iframe.getAttribute("src")).toBe("https://user-set.example.com");
  });

  it("does NOT synthesise a live block for an invalid/blank URL", () => {
    render(
      <PreviewPanel
        blocks={[{ lang: "html", code: "<h1>hi</h1>" }]}
        activeProject={{
          project_id:  "pt_test123",
          preview_url: "   ",   // whitespace-only — must not iframe
          live_url:    "not-a-url",
        }}
        onClose={() => {}}
      />,
    );
    // No live iframe should exist — only the html preview.
    expect(screen.queryByTestId("preview-iframe-live")).toBeNull();
  });

  it("trims whitespace on the URL before handing it to the iframe", () => {
    render(
      <PreviewPanel
        blocks={[]}
        activeProject={{
          project_id: "pt_test123",
          live_url:   "  https://trimmed.example.com  \n",
        }}
        onClose={() => {}}
      />,
    );
    const iframe = screen.getByTestId("preview-iframe-live");
    expect(iframe.getAttribute("src")).toBe("https://trimmed.example.com");
  });
});
