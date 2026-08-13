/**
 * Iter 388o — Bug 10 regression tests
 *
 * Bug 10: Previously `<Route path="*">` in App.jsx silently redirected
 * every broken URL to `/`.  SEO-hostile (Google indexed the homepage
 * under every stale link) and UX-hostile (founder couldn't tell the
 * link was broken).
 *
 * Fix: dedicated NotFound page + mutation of the robots meta tag to
 * `noindex, follow` while the 404 is mounted, restored on unmount.
 */
import { describe, it, expect, beforeEach } from "vitest";
import React from "react";
import { render, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import NotFound from "../NotFound.jsx";

describe("Bug 10 — NotFound page behaviour", () => {
  beforeEach(() => {
    cleanup();
    // Reset head — remove any lingering robots meta from prior tests.
    document.head
      .querySelectorAll('meta[name="robots"]')
      .forEach((m) => m.remove());
    document.title = "AUREM";
  });

  it("renders a 404 page with a testid the QA harness can pin to", () => {
    const { getByTestId } = render(
      <MemoryRouter initialEntries={["/random-bad-path-xyz"]}>
        <NotFound />
      </MemoryRouter>,
    );
    expect(getByTestId("not-found-page")).toBeTruthy();
    expect(getByTestId("not-found-home-link")).toBeTruthy();
    expect(getByTestId("not-found-dashboard-link")).toBeTruthy();
  });

  it("echoes the exact broken URL back to the founder", () => {
    const { getByTestId } = render(
      <MemoryRouter initialEntries={["/foo/bar/baz-quux"]}>
        <NotFound />
      </MemoryRouter>,
    );
    expect(getByTestId("not-found-path").textContent).toBe(
      "/foo/bar/baz-quux",
    );
  });

  it("sets the document title to the 404 marker while mounted", () => {
    render(
      <MemoryRouter initialEntries={["/broken"]}>
        <NotFound />
      </MemoryRouter>,
    );
    expect(document.title).toMatch(/404/);
  });

  it("mutates an EXISTING robots meta tag to noindex", () => {
    // Simulate the site's index.html shipping a permissive robots meta.
    const preset = document.createElement("meta");
    preset.name = "robots";
    preset.content = "index, follow, max-snippet:-1";
    document.head.appendChild(preset);
    render(
      <MemoryRouter initialEntries={["/broken"]}>
        <NotFound />
      </MemoryRouter>,
    );
    const meta = document.head.querySelector('meta[name="robots"]');
    expect(meta.getAttribute("content")).toBe("noindex, follow");
  });

  it("restores the original robots meta content on unmount", () => {
    const preset = document.createElement("meta");
    preset.name = "robots";
    preset.content = "index, follow";
    document.head.appendChild(preset);
    const { unmount } = render(
      <MemoryRouter initialEntries={["/broken"]}>
        <NotFound />
      </MemoryRouter>,
    );
    expect(document.head.querySelector('meta[name="robots"]').getAttribute("content"))
      .toBe("noindex, follow");
    unmount();
    expect(document.head.querySelector('meta[name="robots"]').getAttribute("content"))
      .toBe("index, follow");
  });

  it("appends a robots meta if none existed, then removes it on unmount", () => {
    // No robots meta present initially.
    expect(document.head.querySelector('meta[name="robots"]')).toBeNull();
    const { unmount } = render(
      <MemoryRouter initialEntries={["/broken"]}>
        <NotFound />
      </MemoryRouter>,
    );
    expect(document.head.querySelector('meta[name="robots"]').getAttribute("content"))
      .toBe("noindex, follow");
    unmount();
    expect(document.head.querySelector('meta[name="robots"]')).toBeNull();
  });
});
