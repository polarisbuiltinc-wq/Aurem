/**
 * useModalA11y + useDropdownA11y hook tests — Iter 388t · Bug 27/28.
 *
 * Focuses on the pure logic: focus trap wrap around, Escape close,
 * arrow-key navigation, type-ahead.  Rendered with a lightweight
 * harness so we don't depend on any real modal component.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import React, { useRef, useState } from "react";
import useModalA11y from "../../hooks/useModalA11y";
import useDropdownA11y from "../../hooks/useDropdownA11y";

/* ── Bug 27 · useModalA11y ────────────────────────────────────── */

function ModalHarness({ isOpen, onClose }) {
  const ref = useRef(null);
  useModalA11y({ ref, isOpen, onClose });
  if (!isOpen) return null;
  return (
    <div ref={ref} role="dialog" tabIndex={-1} data-testid="harness-modal">
      <button data-testid="btn-a">A</button>
      <button data-testid="btn-b">B</button>
      <button data-testid="btn-c">C</button>
    </div>
  );
}

describe("useModalA11y — Bug 27 focus trap", () => {
  it("closes on Escape via onClose", () => {
    const onClose = vi.fn();
    render(<ModalHarness isOpen={true} onClose={onClose} />);
    const modal = screen.getByTestId("harness-modal");
    fireEvent.keyDown(modal, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("does nothing when isOpen=false", () => {
    const onClose = vi.fn();
    render(<ModalHarness isOpen={false} onClose={onClose} />);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("wraps Tab from last focusable back to first", () => {
    const onClose = vi.fn();
    render(<ModalHarness isOpen={true} onClose={onClose} />);
    const modal = screen.getByTestId("harness-modal");
    const btnA = screen.getByTestId("btn-a");
    const btnC = screen.getByTestId("btn-c");

    btnC.focus();
    expect(document.activeElement).toBe(btnC);

    fireEvent.keyDown(modal, { key: "Tab", shiftKey: false });

    expect(document.activeElement).toBe(btnA);
  });

  it("wraps Shift+Tab from first focusable back to last", () => {
    const onClose = vi.fn();
    render(<ModalHarness isOpen={true} onClose={onClose} />);
    const modal = screen.getByTestId("harness-modal");
    const btnA = screen.getByTestId("btn-a");
    const btnC = screen.getByTestId("btn-c");

    btnA.focus();
    expect(document.activeElement).toBe(btnA);

    fireEvent.keyDown(modal, { key: "Tab", shiftKey: true });

    expect(document.activeElement).toBe(btnC);
  });
});

/* ── Bug 28 · useDropdownA11y ─────────────────────────────────── */

function DropdownHarness({ items, isOpen, onSelect, onClose }) {
  const { activeIndex, onKeyDown } = useDropdownA11y({
    items, isOpen, onSelect, onClose,
  });
  return (
    <div data-testid="dropdown" onKeyDown={onKeyDown} tabIndex={0}>
      {items.map((it, i) => (
        <div
          key={it.id}
          data-testid={`option-${it.id}`}
          data-active={i === activeIndex}
        >
          {it.label}
        </div>
      ))}
    </div>
  );
}

describe("useDropdownA11y — Bug 28 arrow-key nav", () => {
  const items = [
    { id: "a", label: "Alpha" },
    { id: "b", label: "Bravo" },
    { id: "c", label: "Charlie" },
    { id: "d", label: "Delta" },
  ];

  it("ArrowDown advances the active index", () => {
    const { getByTestId } = render(
      <DropdownHarness items={items} isOpen={true} />,
    );
    const d = getByTestId("dropdown");
    d.focus();
    expect(getByTestId("option-a").dataset.active).toBe("true");

    fireEvent.keyDown(d, { key: "ArrowDown" });
    expect(getByTestId("option-b").dataset.active).toBe("true");
  });

  it("ArrowUp wraps around from index 0 to last", () => {
    const { getByTestId } = render(
      <DropdownHarness items={items} isOpen={true} />,
    );
    const d = getByTestId("dropdown");
    fireEvent.keyDown(d, { key: "ArrowUp" });
    expect(getByTestId("option-d").dataset.active).toBe("true");
  });

  it("End jumps to last item, Home returns to first", () => {
    const { getByTestId } = render(
      <DropdownHarness items={items} isOpen={true} />,
    );
    const d = getByTestId("dropdown");
    fireEvent.keyDown(d, { key: "End" });
    expect(getByTestId("option-d").dataset.active).toBe("true");
    fireEvent.keyDown(d, { key: "Home" });
    expect(getByTestId("option-a").dataset.active).toBe("true");
  });

  it("Enter fires onSelect with the currently active item", () => {
    const onSelect = vi.fn();
    const { getByTestId } = render(
      <DropdownHarness items={items} isOpen={true} onSelect={onSelect} />,
    );
    const d = getByTestId("dropdown");
    fireEvent.keyDown(d, { key: "ArrowDown" });
    fireEvent.keyDown(d, { key: "Enter" });
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect.mock.calls[0][0]).toEqual({ id: "b", label: "Bravo" });
  });

  it("Escape fires onClose", () => {
    const onClose = vi.fn();
    const { getByTestId } = render(
      <DropdownHarness items={items} isOpen={true} onClose={onClose} />,
    );
    const d = getByTestId("dropdown");
    fireEvent.keyDown(d, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Type-ahead jumps to first matching item (case-insensitive)", () => {
    const { getByTestId } = render(
      <DropdownHarness items={items} isOpen={true} />,
    );
    const d = getByTestId("dropdown");
    // Currently on 'a' (Alpha).  Typing 'c' should jump to Charlie.
    fireEvent.keyDown(d, { key: "c" });
    expect(getByTestId("option-c").dataset.active).toBe("true");
  });

  it("does nothing when isOpen=false", () => {
    const onSelect = vi.fn();
    const { getByTestId } = render(
      <DropdownHarness items={items} isOpen={false} onSelect={onSelect} />,
    );
    const d = getByTestId("dropdown");
    fireEvent.keyDown(d, { key: "Enter" });
    expect(onSelect).not.toHaveBeenCalled();
  });
});
