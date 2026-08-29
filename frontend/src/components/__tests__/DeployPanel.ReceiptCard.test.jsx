/**
 * DeployPanel.ReceiptCard.test.jsx — receipt UI polish (2026-08-30):
 * the Deploy panel's receipt view shows BOTH stored screenshots side
 * by side, labelled "Full page" and "Viewport".
 *   t_receipt_shows_both_shots — both images render with their labels
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("../../lib/api", () => ({ api: { get: vi.fn() } }));

import { ReceiptCard } from "../DeployPanel.jsx";
import { api } from "../../lib/api";

beforeEach(() => {
  vi.clearAllMocks();
  global.URL.createObjectURL = vi.fn(() => "blob:mock-url");
  global.URL.revokeObjectURL = vi.fn();
});

describe("Receipt UI polish · ReceiptCard", () => {
  it("t_receipt_shows_both_shots — viewport + full page shots render side by side, labelled", async () => {
    api.get.mockResolvedValue({ data: new Blob(["fake-jpeg"]) });
    render(
      <ReceiptCard
        verified={true}
        verifyNote={null}
        verifyUrl="https://example.com"
        receiptKey="viewport-key"
        fullpageReceiptKey="fullpage-key"
        runId="run1"
      />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("deploy-receipt-shots")).toBeInTheDocument();
    });
    expect(screen.getByTestId("deploy-receipt-shot-viewport")).toHaveTextContent("Viewport");
    expect(screen.getByTestId("deploy-receipt-shot-fullpage")).toHaveTextContent("Full page");
    expect(screen.getByTestId("deploy-receipt-screenshot")).toBeInTheDocument();
    expect(screen.getByTestId("deploy-receipt-screenshot-fullpage")).toBeInTheDocument();
    expect(api.get).toHaveBeenCalledWith("/deploy/runs/run1/receipt",
      { params: { variant: "viewport" }, responseType: "blob" });
    expect(api.get).toHaveBeenCalledWith("/deploy/runs/run1/receipt",
      { params: { variant: "fullpage" }, responseType: "blob" });
  });

  it("shows only the viewport shot when no fullpage key exists (old runs, back-compat)", async () => {
    api.get.mockResolvedValue({ data: new Blob(["fake-jpeg"]) });
    render(
      <ReceiptCard
        verified={true}
        verifyNote={null}
        verifyUrl="https://example.com"
        receiptKey="viewport-key"
        fullpageReceiptKey={null}
        runId="run2"
      />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("deploy-receipt-shot-viewport")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("deploy-receipt-shot-fullpage")).toBeNull();
  });
});
