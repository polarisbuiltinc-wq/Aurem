/**
 * Overnight T6/P1e (2026-08-28) — native window.confirm() sweep.
 *
 * Non-ship-flow confirms (Projects "Remove project", Integrations
 * "Reveal API key") replaced with the themed ConfirmModal. Ship/
 * rollback confirms (MessageBubble, ShipConfirmModal, Projects
 * task-rollback) are explicitly OUT of scope — parked under
 * ROADMAP F17 pending the Phase-7 ship-UI unification.
 */
import { readFileSync } from "fs";
import path from "path";
import { describe, it, expect } from "vitest";

const projectsSrc = readFileSync(
  path.resolve(__dirname, "../../pages/Projects.jsx"), "utf-8"
);
const integrationsSrc = readFileSync(
  path.resolve(__dirname, "../../pages/Integrations.jsx"), "utf-8"
);

describe("P1e — Projects.jsx remove-project uses themed confirm", () => {
  it("no longer calls window.confirm for project removal", () => {
    expect(projectsSrc).not.toMatch(/window\.confirm\(`Remove project/);
  });
  it("mounts ConfirmModal with the remove-confirm testid prefix", () => {
    expect(projectsSrc).toContain('testidPrefix="proj-remove-confirm"');
  });
  it("still calls the real DELETE endpoint on confirm (doRemove)", () => {
    expect(projectsSrc).toMatch(/await api\.delete\(`\/cto\/projects\/\$\{project\.project_id\}`\)/);
  });
  it("task-rollback double-confirm is UNCHANGED (parked, ship-flow, F17)", () => {
    // sanity: we did not touch the rollback window.confirm calls
    expect(projectsSrc).toContain("Two-step confirmation");
  });
});

describe("P1e — Integrations.jsx reveal-key uses themed confirm", () => {
  it("no longer calls window.confirm for key reveal", () => {
    expect(integrationsSrc).not.toMatch(/window\.confirm\(\s*\n\s*"Reveal your full API key/);
  });
  it("mounts ConfirmModal for the reveal flow", () => {
    expect(integrationsSrc).toContain("ConfirmModal");
    expect(integrationsSrc).toContain("reveal-confirm");
  });
});
