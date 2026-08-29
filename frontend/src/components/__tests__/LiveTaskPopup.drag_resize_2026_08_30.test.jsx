/**
 * LiveTaskPopup.drag_resize_2026_08_30.test.jsx
 *
 * Founder request: "isa moveable bnao user khi bhe iski location ans
 * size low save kr ske" — the popup must be draggable + resizable,
 * and remember its position/size (persisted in localStorage) across
 * remounts (new tasks, page refresh).
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { api } from "../../lib/api";
import LiveTaskPopup from "../LiveTaskPopup.jsx";

vi.mock("../../lib/api", () => ({ api: { get: vi.fn() } }));

const RUNNING_TASK = {
  status: "running",
  steps: [{ kind: "phase_read", step: "Reading repo", ts: 1 }],
  files_changed: [], vanguard_findings: [], files_read: [],
};

const GEOM_KEY = "ora_ltp_geometry";

beforeEach(() => {
  localStorage.clear();
  api.get.mockResolvedValue({ data: { task: RUNNING_TASK } });
  // jsdom doesn't implement layout — stub a real-ish bounding rect so
  // drag/resize math has something to work with.
  Element.prototype.getBoundingClientRect = () => ({
    left: 24, top: 400, right: 404, bottom: 600, width: 380, height: 200,
  });
});

describe("2026-08-30 · LiveTaskPopup drag + resize + persisted geometry", () => {
  it("renders a drag handle on the header and a resize handle at the corner", async () => {
    render(<LiveTaskPopup taskId="t1" onClose={() => {}} onDone={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("live-task-popup")).toBeInTheDocument());
    expect(screen.getByTestId("ltp-drag-handle")).toBeInTheDocument();
    expect(screen.getByTestId("ltp-resize-handle")).toBeInTheDocument();
  });

  it("dragging the header moves the popup and persists the new position", async () => {
    render(<LiveTaskPopup taskId="t1" onClose={() => {}} onDone={() => {}} />);
    const root = await screen.findByTestId("live-task-popup");
    const handle = screen.getByTestId("ltp-drag-handle");

    fireEvent.mouseDown(handle, { clientX: 100, clientY: 400 });
    fireEvent.mouseMove(window, { clientX: 160, clientY: 460 }); // +60, +60
    fireEvent.mouseUp(window);

    await waitFor(() => {
      expect(root.style.left).toBe("84px"); // 24 + 60
      expect(root.style.top).toBe("460px"); // 400 + 60
    });
    const saved = JSON.parse(localStorage.getItem(GEOM_KEY));
    expect(saved.left).toBe(84);
    expect(saved.top).toBe(460);
  });

  it("dragging the resize handle grows the popup and persists the new size", async () => {
    render(<LiveTaskPopup taskId="t1" onClose={() => {}} onDone={() => {}} />);
    const root = await screen.findByTestId("live-task-popup");
    const grip = screen.getByTestId("ltp-resize-handle");

    fireEvent.mouseDown(grip, { clientX: 404, clientY: 600 });
    fireEvent.mouseMove(window, { clientX: 454, clientY: 650 }); // +50, +50
    fireEvent.mouseUp(window);

    await waitFor(() => {
      expect(root.style.width).toBe("430px");
      expect(root.style.height).toBe("250px");
    });
    const saved = JSON.parse(localStorage.getItem(GEOM_KEY));
    expect(saved.width).toBe(430);
    expect(saved.height).toBe(250);
  });

  it("clamps resize to the minimum size instead of collapsing", async () => {
    render(<LiveTaskPopup taskId="t1" onClose={() => {}} onDone={() => {}} />);
    const root = await screen.findByTestId("live-task-popup");
    const grip = screen.getByTestId("ltp-resize-handle");

    fireEvent.mouseDown(grip, { clientX: 404, clientY: 600 });
    fireEvent.mouseMove(window, { clientX: -900, clientY: -900 }); // shrink hard
    fireEvent.mouseUp(window);

    await waitFor(() => {
      expect(Number(root.style.width.replace("px", ""))).toBeGreaterThanOrEqual(300);
      expect(Number(root.style.height.replace("px", ""))).toBeGreaterThanOrEqual(160);
    });
  });

  it("restores a previously-saved geometry on the next mount (remembers per-user position)", async () => {
    localStorage.setItem(GEOM_KEY, JSON.stringify({ left: 200, top: 120, width: 500, height: 300 }));
    render(<LiveTaskPopup taskId="t2" onClose={() => {}} onDone={() => {}} />);
    const root = await screen.findByTestId("live-task-popup");
    expect(root.style.left).toBe("200px");
    expect(root.style.top).toBe("120px");
    expect(root.style.width).toBe("500px");
    expect(root.style.height).toBe("300px");
  });

  it("uses the original bottom-left default when no geometry was ever saved", async () => {
    render(<LiveTaskPopup taskId="t3" onClose={() => {}} onDone={() => {}} />);
    const root = await screen.findByTestId("live-task-popup");
    expect(root.style.left).toBe("24px");
    expect(root.style.bottom).toBe("96px");
  });
});
