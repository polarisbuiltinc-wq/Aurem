import React from "react";
import { render, screen, act } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { SelfHealIndicator } from "../LoopActionCards.jsx";

describe("SelfHealIndicator — backend counter + per-attempt timer", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders 1/2 then 2/2 and resets a live seconds timer for the new attempt", () => {
    vi.useFakeTimers();
    vi.setSystemTime(100_000);

    const { rerender } = render(
      <SelfHealIndicator visible attempt={1} max={2} startedAt={Date.now()} />,
    );

    expect(screen.getByTestId("self-heal-indicator").textContent).toContain("1/2");
    expect(screen.getByTestId("self-heal-timer").textContent).toBe("0s");

    act(() => {
      vi.advanceTimersByTime(1_100);
    });
    expect(screen.getByTestId("self-heal-timer").textContent).toBe("1s");

    const secondAttemptStartedAt = Date.now();
    rerender(
      <SelfHealIndicator visible attempt={2} max={2} startedAt={secondAttemptStartedAt} />,
    );

    expect(screen.getByTestId("self-heal-indicator").textContent).toContain("2/2");
    expect(screen.getByTestId("self-heal-timer").textContent).toBe("0s");

    act(() => {
      vi.advanceTimersByTime(1_000);
    });
    expect(screen.getByTestId("self-heal-timer").textContent).toBe("1s");
  });
});