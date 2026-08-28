import { describe, it, expect } from "vitest";
import { MUTATION_VERBS } from "../MessageBubble";

// 2026-08-28 · P0 hotfix regression — the backend persona
// (services/orchestrator.py) now tells the LLM to use "revert"
// explicitly when proposing a rollback. If this frontend whitelist
// ever drifts out of sync with the backend one again, the Approve
// button silently fails to render for rollback requests — the exact
// production bug this test locks in.
describe("MUTATION_VERBS stays in sync with backend revert/rollback support", () => {
  it("recognizes revert/rollback/undo/restore as valid mutation verbs", () => {
    expect(MUTATION_VERBS.test("Revert README.md to the version before commit abc123")).toBe(true);
    expect(MUTATION_VERBS.test("I'll rollback the last commit on main")).toBe(true);
    expect(MUTATION_VERBS.test("Undo the change made in the previous ship")).toBe(true);
    expect(MUTATION_VERBS.test("Restore the original content of package.json")).toBe(true);
  });

  it("still rejects a purely read-only line with no mutation verb", () => {
    expect(MUTATION_VERBS.test("Inspect the current state of README.md")).toBe(false);
  });
});
