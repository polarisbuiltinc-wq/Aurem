/**
 * PreferredSourceButton.test.jsx — Visibility Kit Phase A / A5 (2026-08-28).
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { PreferredSourceButton } from "../PreferredSourceButton.jsx";
import * as analytics from "../../lib/analytics";

describe("PreferredSourceButton", () => {
  beforeEach(() => {
    document.head.querySelectorAll("script").forEach((s) => s.remove());
  });

  it("t_always_renders_visible_deeplink_fallback", () => {
    render(<PreferredSourceButton />);
    const link = screen.getByTestId("preferred-source-deeplink");
    expect(link.getAttribute("href")).toBe("https://www.google.com/preferences/source?q=auremcto.com");
  });

  it("t_widget_script_loaded_idempotently", () => {
    const { unmount } = render(<PreferredSourceButton />);
    unmount();
    render(<PreferredSourceButton />);
    const scripts = document.head.querySelectorAll('script[src="https://news.google.com/swg/js/v1/publisher.js"]');
    expect(scripts.length).toBe(1);
  });

  it("t_deeplink_click_tracks_analytics", () => {
    const spyClicked = vi.spyOn(analytics, "trackPreferredSourceClicked");
    render(<PreferredSourceButton />);
    fireEvent.click(screen.getByTestId("preferred-source-deeplink"));
    expect(spyClicked).toHaveBeenCalled();
  });

  it("t_never_implies_ranking_boost_in_copy", () => {
    render(<PreferredSourceButton />);
    const text = screen.getByTestId("preferred-source-button").textContent.toLowerCase();
    expect(text).not.toMatch(/boost your ranking|rank higher|guaranteed/);
  });
});
