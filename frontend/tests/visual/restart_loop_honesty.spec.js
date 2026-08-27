/**
 * tests/visual/restart_loop_honesty.spec.js — Restart-loop honesty
 * real-browser proof (2026-08-27, Refinement 2).
 *
 * Drives a REAL loop through the live preview app (login → Loop mode
 * → plan → let it expire → click the real Restart button → assert
 * the SAME loop_id is reused (not a fresh one) → assert the plan
 * gate re-presents → approve for real → assert the engine actually
 * advances past awaiting_confirmation (real forward progress, not
 * just the card reappearing).
 *
 * Requires backend/.env LOOP_AWAITING_CONFIRM_MAX_S set short
 * (test-scoped override, removed after this proof — see PRD).
 */
import { test, expect } from "@playwright/test";
import fs from "fs";
import path from "path";

function readBackendUrl() {
  if (process.env.REACT_APP_BACKEND_URL) return process.env.REACT_APP_BACKEND_URL;
  const envFile = fs.readFileSync(path.join(__dirname, "../../.env"), "utf8");
  const m = envFile.match(/^REACT_APP_BACKEND_URL=(.+)$/m);
  return m ? m[1].trim() : "";
}

const BACKEND = readBackendUrl();
const EMAIL = "test@aurem.dev";
const PASSWORD = "AuremTest2026!";
const PROJECT_ID = "p_68dfb110b1";

test.describe("Restart-loop honesty — real resume proof", () => {
  test("Restart re-presents the SAME loop_id and drives real forward progress", async ({ page }) => {
    test.setTimeout(280_000);

    await page.goto("/login");
    await page.getByTestId("login-email").fill(EMAIL);
    await page.getByTestId("login-password").fill(PASSWORD);
    await page.getByTestId("login-submit").click();
    await page.waitForURL(/dashboard/i, { timeout: 20000 });

    // Pin the active project to a known-healthy GitHub-App-connected
    // project BEFORE ChatPanel mounts for real — avoids landing on an
    // unrelated/broken-installation project.
    await page.evaluate((pid) => localStorage.setItem("aurem_active_project", pid), PROJECT_ID);
    await page.reload();
    await expect(page.getByTestId("chat-input")).toBeVisible({ timeout: 20000 });
    await expect(page.getByTestId("active-project-chip-name")).toHaveText(/phaseA-test-clean/i, { timeout: 20000 });
    // Let Shell.jsx's async session-adopt-or-mint settle (avoids typing
    // into the textarea right before a sessionId-driven remount wipes it).
    await page.waitForTimeout(4000);

    // Switch to Loop mode: collapsed pill → pick Pro → pick Loop sub-choice.
    await page.getByTestId("ds2-mode-collapsed").click();
    await page.getByTestId("ds2-mode-pro").click();
    await page.getByTestId("ds2-exec-loop").click();

    const startPromise = page.waitForResponse(
      (r) => r.url().includes("/loop/start") && r.request().method() === "POST"
    );
    await page.getByTestId("chat-input").fill(
      "Add a one-line comment at the top of README.md describing this repo."
    );
    await expect(page.getByTestId("chat-send")).not.toHaveAttribute("aria-disabled", "true", { timeout: 25000 });
    await page.getByTestId("chat-send").click();
    const startResp = await startPromise;
    const startJson = await startResp.json();
    const originalLoopId = startJson.loop_id;
    expect(originalLoopId, "loop_id returned by /loop/start").toBeTruthy();

    await expect(page.getByTestId("plan-approval-card")).toBeVisible({ timeout: 60000 });

    // Wait for the server-side sweep to expire this awaiting_confirmation
    // session (test-scoped short TTL; housekeeping tick is 60s).
    await expect(page.getByTestId("loop-expired-card")).toBeVisible({ timeout: 110000 });

    const restartConfirmPromise = page.waitForResponse(
      (r) => /\/loop\/[^/]+\/confirm$/.test(r.url()) && r.request().method() === "POST"
    );
    await page.getByTestId("loop-expired-restart-btn").click();
    const restartResp = await restartConfirmPromise;
    const restartLoopId = restartResp.url().match(/\/loop\/([^/]+)\/confirm/)[1];

    expect(restartLoopId, "Restart must reuse the SAME loop_id, not spawn a fresh one")
      .toBe(originalLoopId);

    // The exact pending-decision card re-presents (plan gate, not a
    // generic message) — proves context/plan was preserved, not discarded.
    await expect(page.getByTestId("plan-approval-card")).toBeVisible({ timeout: 30000 });

    const secondConfirmPromise = page.waitForResponse(
      (r) => /\/loop\/[^/]+\/confirm$/.test(r.url()) && r.request().method() === "POST"
    );
    await page.getByTestId("plan-approve-btn").click();
    const secondResp = await secondConfirmPromise;
    const secondLoopId = secondResp.url().match(/\/loop\/([^/]+)\/confirm/)[1];
    expect(secondLoopId, "Real approval after revival must act on the same loop_id")
      .toBe(originalLoopId);

    // Real forward engine activity: poll status until it moves past
    // awaiting_confirmation (not just the card disappearing client-side).
    const token = await page.evaluate(() => localStorage.getItem("aurem_token"));
    let lastState = null;
    let progressed = false;
    for (let i = 0; i < 40; i++) {
      const statusResp = await page.request.get(
        `${BACKEND}/api/aurem-dev/loop/${originalLoopId}/status`,
        { headers: { Authorization: `Bearer ${token}` } }
      );
      if (statusResp.ok()) {
        try {
          const j = JSON.parse(await statusResp.text());
          lastState = j.state;
          if (lastState && lastState !== "awaiting_confirmation") {
            progressed = true;
            break;
          }
        } catch { /* transient non-JSON response — retry */ }
      }
      await page.waitForTimeout(3000);
    }
    console.log(`[restart-honesty] original=${originalLoopId} restart_used=${restartLoopId} final_state=${lastState}`);
    expect(progressed, `engine must advance past awaiting_confirmation, last observed state=${lastState}`)
      .toBeTruthy();

    // Cleanup — cancel so no live loop/lock is left behind by this proof.
    await page.request.post(
      `${BACKEND}/api/aurem-dev/loop/${originalLoopId}/cancel`,
      { headers: { Authorization: `Bearer ${token}` }, data: {} }
    ).catch(() => {});
  });
});
