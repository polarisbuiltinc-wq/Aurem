/**
 * SessionSwitcher.delete_confirm.test.jsx — Round-2 PR (P0-2).
 *
 * Locks the fix for the "no confirm, no undo" one-click chat-delete
 * bug: SessionSwitcher's trash icon now opens a themed
 * DeleteChatConfirmModal instead of calling deleteSession directly.
 */
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

const deleteSession = vi.fn();
const refreshSessions = vi.fn();
const openSession = vi.fn();
const startNewSession = vi.fn();

vi.mock("../Shell", () => ({
  useChatSession: () => ({
    sessionId: "s1",
    sessions: [
      { session_id: "s1", title: "First chat", updated_at: Date.now() / 1000 },
    ],
    refreshSessions,
    openSession,
    deleteSession,
    startNewSession,
  }),
}));

import SessionSwitcher from "../SessionSwitcher.jsx";

describe("SessionSwitcher — delete confirm (Round-2 P0-2)", () => {
  it("t_delete_confirm_shown: clicking trash opens the themed confirm dialog, not window.confirm", () => {
    render(<SessionSwitcher />);
    fireEvent.click(screen.getByTestId("session-switcher-btn"));
    fireEvent.click(screen.getByTestId("session-switcher-delete-s1"));
    expect(screen.getByTestId("delete-chat-confirm-modal")).toBeInTheDocument();
    expect(screen.getByText(/Delete this chat\?/i)).toBeInTheDocument();
    expect(screen.getByText(/can't be undone/i)).toBeInTheDocument();
    // Deletion has NOT happened yet — confirm is required first.
    expect(deleteSession).not.toHaveBeenCalled();
  });

  it("t_delete_cancel_keeps_session: Cancel closes the dialog and never calls deleteSession", () => {
    render(<SessionSwitcher />);
    fireEvent.click(screen.getByTestId("session-switcher-btn"));
    fireEvent.click(screen.getByTestId("session-switcher-delete-s1"));
    fireEvent.click(screen.getByTestId("delete-chat-confirm-cancel"));
    expect(screen.queryByTestId("delete-chat-confirm-modal")).toBeNull();
    expect(deleteSession).not.toHaveBeenCalled();
  });

  it("t_delete_confirm_deletes: confirming calls deleteSession with the right session id and closes the dialog", () => {
    render(<SessionSwitcher />);
    fireEvent.click(screen.getByTestId("session-switcher-btn"));
    fireEvent.click(screen.getByTestId("session-switcher-delete-s1"));
    fireEvent.click(screen.getByTestId("delete-chat-confirm-approve"));
    expect(deleteSession).toHaveBeenCalledTimes(1);
    expect(deleteSession.mock.calls[0][1]).toBe("s1");
    expect(screen.queryByTestId("delete-chat-confirm-modal")).toBeNull();
  });
});
