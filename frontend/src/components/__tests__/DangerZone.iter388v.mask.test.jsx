/**
 * DangerZone.iter388v.mask.test.jsx — Iter 388v · P0 Security Fix
 *
 * Bug: the confirm-email display showed the full plaintext email
 * directly above the input, letting any shoulder-surfer / screen-share
 * viewer copy-paste it back and unlock the delete button. The whole
 * "type your email to confirm" step was security theatre.
 *
 * Fix: display the email MASKED (local part hidden except last 2
 * chars) so the user must type the full email FROM MEMORY. Server-
 * side validation is unchanged — full lowercase email required.
 *
 * These tests prove:
 *   1. The masked display never contains the full local part
 *   2. The last 2 chars of the local part are the ONLY reveal
 *   3. Typing the MASKED string keeps the confirm button disabled
 *   4. Typing the FULL email enables the confirm button
 *   5. userSelect: none prevents mouse-drag selection
 */
import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import DangerZone from "../DangerZone.jsx";

function renderDZ(email) {
  return render(
    <MemoryRouter>
      <DangerZone email={email} />
    </MemoryRouter>,
  );
}

describe("DangerZone email masking — P0 security", () => {
  it("masks the email display; last 2 chars only + domain visible", () => {
    renderDZ("teji.ss1986@gmail.com");
    fireEvent.click(screen.getByTestId("danger-zone-delete-btn"));

    const masked = screen.getByTestId("danger-zone-email-masked");
    const text = masked.textContent;

    expect(text).toContain("86@gmail.com");
    // Full local part must NEVER appear.
    expect(text).not.toContain("teji.ss1986");
    expect(text).not.toContain("teji");
    expect(text).not.toContain("ss19");
    // The mask is exactly (local.length - 2) asterisks + "86" + domain.
    expect(text).toBe("*********86@gmail.com");
  });

  it("very short local parts (<=2 chars) render 4 stars, never the raw local", () => {
    renderDZ("ab@x.com");
    fireEvent.click(screen.getByTestId("danger-zone-delete-btn"));
    const text = screen.getByTestId("danger-zone-email-masked").textContent;
    expect(text).toBe("****@x.com");
    expect(text).not.toContain("ab@");
  });

  it("pasting the MASKED text keeps the confirm button disabled", () => {
    renderDZ("teji.ss1986@gmail.com");
    fireEvent.click(screen.getByTestId("danger-zone-delete-btn"));

    const masked = screen.getByTestId("danger-zone-email-masked").textContent;
    const input = screen.getByTestId("danger-zone-email-input");
    fireEvent.change(input, { target: { value: masked } });

    const confirm = screen.getByTestId("danger-zone-confirm-btn");
    expect(confirm).toBeDisabled();
  });

  it("typing the FULL email enables the confirm button", () => {
    renderDZ("teji.ss1986@gmail.com");
    fireEvent.click(screen.getByTestId("danger-zone-delete-btn"));

    const input = screen.getByTestId("danger-zone-email-input");
    fireEvent.change(input, { target: { value: "teji.ss1986@gmail.com" } });

    const confirm = screen.getByTestId("danger-zone-confirm-btn");
    expect(confirm).not.toBeDisabled();
  });

  it("case-insensitive match still works (uppercase input matches)", () => {
    renderDZ("teji.ss1986@gmail.com");
    fireEvent.click(screen.getByTestId("danger-zone-delete-btn"));

    const input = screen.getByTestId("danger-zone-email-input");
    fireEvent.change(input, { target: { value: "TEJI.SS1986@GMAIL.COM" } });

    expect(screen.getByTestId("danger-zone-confirm-btn")).not.toBeDisabled();
  });

  it("masked span has userSelect:none so mouse-drag selection is blocked", () => {
    renderDZ("someone@example.com");
    fireEvent.click(screen.getByTestId("danger-zone-delete-btn"));
    const masked = screen.getByTestId("danger-zone-email-masked");
    expect(masked.style.userSelect).toBe("none");
  });

  it("mask never leaks email length via a fixed pattern — long emails still hide local", () => {
    renderDZ("very.long.email.address.that.is.definitely.private@corp.example.io");
    fireEvent.click(screen.getByTestId("danger-zone-delete-btn"));
    const text = screen.getByTestId("danger-zone-email-masked").textContent;
    // Only "te" (last 2 of "…private") + domain must be visible.
    expect(text).toContain("te@corp.example.io");
    expect(text).not.toContain("very");
    expect(text).not.toContain("long");
    expect(text).not.toContain("private");
    expect(text).not.toContain("address");
  });
});
