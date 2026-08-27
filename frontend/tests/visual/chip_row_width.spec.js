/**
 * tests/visual/chip_row_width.spec.js — Phase E · 3-viewport chip-row
 * overflow proof (2026-08-27).
 *
 * Renders the worst-case dense chip row (`/dev/visual?state=chip-row-dense`,
 * VisualFixtures.jsx) inside the REAL `[data-testid="chat-panel"]` /
 * `[data-testid="chat-form"].glass-composer` containers at 360, 768 and
 * 1440px viewports and MEASURES (not source-reads) that the chip row
 * never exceeds the composer's own content width and never causes
 * horizontal page overflow. Screenshot captured at each viewport for
 * visual record alongside the measured pixel assertions.
 */
import { test, expect } from "@playwright/test";

const VIEWPORTS = [
  { name: "mobile-360",  width: 360,  height: 800 },
  { name: "tablet-768",  width: 768,  height: 1024 },
  { name: "desktop-1440", width: 1440, height: 900 },
];

test.describe("Chip row width — 3-viewport overflow proof (Phase E)", () => {
  for (const vp of VIEWPORTS) {
    test(`${vp.name}: chip row ≤ composer width, no overflow`, async ({ page }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await page.goto(`/dev/visual?state=chip-row-dense`, { waitUntil: "domcontentloaded" });
      await page.addStyleTag({
        content: `*, *::before, *::after { animation-duration: 0s !important; transition-duration: 0s !important; }`,
      });
      await page.waitForLoadState("networkidle");

      const composer = page.getByTestId("chat-form");
      const chipRow = page.getByTestId("chip-row-dense");
      await expect(composer).toBeVisible();
      await expect(chipRow).toBeVisible();

      const measurements = await page.evaluate(() => {
        const composerEl = document.querySelector('[data-testid="chat-form"]');
        const rowEl = document.querySelector('[data-testid="chip-row-dense"]');
        const composerRect = composerEl.getBoundingClientRect();
        const rowRect = rowEl.getBoundingClientRect();
        return {
          composerContentWidth: composerEl.clientWidth,
          composerScrollWidth: composerEl.scrollWidth,
          rowWidth: rowRect.width,
          rowScrollWidth: rowEl.scrollWidth,
          docScrollWidth: document.documentElement.scrollWidth,
          viewportWidth: window.innerWidth,
        };
      });

      // eslint-disable-next-line no-console
      console.log(`[${vp.name}]`, JSON.stringify(measurements));

      // 1) Chip row never wider than the composer's own content box.
      expect(measurements.rowWidth).toBeLessThanOrEqual(measurements.composerContentWidth + 1);
      // 2) Composer itself never scrolls internally (chips wrap, not overflow).
      expect(measurements.composerScrollWidth).toBeLessThanOrEqual(measurements.composerContentWidth + 1);
      // 3) No page-level horizontal overflow at this viewport.
      expect(measurements.docScrollWidth).toBeLessThanOrEqual(measurements.viewportWidth + 1);

      await expect(page.getByTestId("visual-fixture-stage")).toHaveScreenshot(
        `chip-row-dense-${vp.name}.png`,
        { maxDiffPixelRatio: 0.02, animations: "disabled" }
      );
    });
  }
});
