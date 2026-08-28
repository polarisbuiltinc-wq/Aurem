/**
 * Shell.legacy_sidebar_delete_confirm.test.jsx — testing_agent finding
 * (iteration_385, MED) fixed 2026-08-28.
 *
 * Shell.jsx's legacy sidebar "Recent Chats" rail had its own delete
 * button calling deleteSession() directly with no confirm — a second
 * copy of the exact P0-2 data-loss bug already fixed on
 * SessionSwitcher. Locks the same themed-modal fix here.
 */
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../lib/api", () => ({
  api: { get: vi.fn(() => Promise.resolve({ data: {} })), delete: vi.fn(() => Promise.resolve({})) },
  getUser: () => ({ email: "t@t.com", is_admin: false }),
  getToken: () => "tok",
  logout: vi.fn(),
  healthApi: vi.fn(() => Promise.resolve({ ok: true })),
  newSessionId: () => "new-session-id",
  setUser: vi.fn(),
}));
vi.mock("../../lib/cacheCleaner", () => ({ clearUICacheAndReload: vi.fn() }));

import Shell, { fetchSessionsShared } from "../Shell.jsx";

describe("Shell.jsx legacy sidebar — delete confirm (data-loss fix parity)", () => {
  it("delete-session-* button exists and is wired to open a confirm modal, not deleteSession directly", () => {
    // Source-level lock (component has heavy runtime deps for full
    // mount) — same convention as MessageBubble's pure-export tests.
    const fs = require("node:fs");
    const src = fs.readFileSync(
      require("node:path").resolve(__dirname, "..", "Shell.jsx"),
      "utf-8"
    );
    expect(src).toMatch(/onClick=\{\(e\) => \{ e\.stopPropagation\(\); setPendingDeleteLegacy/);
    expect(src).not.toMatch(/onClick=\{\(e\) => deleteSession\(e, s\.session_id\)\}/);
    expect(src).toMatch(/<DeleteChatConfirmModal/);
  });
});
