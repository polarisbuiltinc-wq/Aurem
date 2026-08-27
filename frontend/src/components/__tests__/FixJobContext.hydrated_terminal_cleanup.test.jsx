/**
 * FixJobContext.hydrated_terminal_cleanup.test.jsx — 2026-08-27
 *
 * Regression test for a founder-reported bug: the "Fixing codebase" /
 * "Fix complete" bar reappeared minutes later, and again after a page
 * refresh or re-login, even though the fix had already committed.
 *
 * Root cause: the SSE "hydrated" handler (fired when the client
 * reconnects to a job that already finished on the backend) set
 * `terminal` correctly but never cleared `LS_JOB_KEY` from
 * localStorage — unlike the normal `done`/`gone` phases. Every future
 * app mount then found the stale key and re-attached to the same
 * job_id, replaying the terminal state.
 */
import React from "react";
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render, act } from "@testing-library/react";
import { FixJobProvider, useFixJob } from "../FixJobContext.jsx";

const LS_JOB_KEY = "aurem_fix_active_job";

class MockEventSource {
  constructor(url) {
    this.url = url;
    this.listeners = {};
    MockEventSource.instances.push(this);
  }
  addEventListener(type, cb) { this.listeners[type] = cb; }
  close() { this.closed = true; }
  emit(type, data) {
    this.listeners[type]?.({ data: JSON.stringify(data) });
  }
}
MockEventSource.instances = [];

function Probe() {
  const { startJob, status, terminal } = useFixJob();
  window.__testFixJob = { startJob, status, terminal };
  return <div data-testid="probe" data-status={status} />;
}

describe("FixJobContext — hydrated-terminal localStorage cleanup", () => {
  let originalEventSource;

  beforeEach(() => {
    localStorage.clear();
    MockEventSource.instances = [];
    originalEventSource = global.EventSource;
    global.EventSource = MockEventSource;
  });

  afterEach(() => {
    global.EventSource = originalEventSource;
  });

  it("clears LS_JOB_KEY when a hydrated event resolves to a terminal (non-running) status", () => {
    render(<FixJobProvider><Probe /></FixJobProvider>);
    act(() => { window.__testFixJob.startJob({ job_id: "job-xyz", total: 3 }); });
    expect(localStorage.getItem(LS_JOB_KEY)).not.toBeNull();

    const es = MockEventSource.instances[MockEventSource.instances.length - 1];
    act(() => {
      es.emit("phase", {
        phase: "hydrated", status: "done",
        completed: 3, failed: 0, total: 3, results: [],
      });
    });

    // Bug fix assertion — this used to stay populated forever, causing
    // the bar to reappear on every subsequent mount/refresh/login.
    expect(localStorage.getItem(LS_JOB_KEY)).toBeNull();
  });

  it("does NOT clear LS_JOB_KEY when hydrated status is still 'running' (job genuinely in-flight)", () => {
    render(<FixJobProvider><Probe /></FixJobProvider>);
    act(() => { window.__testFixJob.startJob({ job_id: "job-abc", total: 3 }); });

    const es = MockEventSource.instances[MockEventSource.instances.length - 1];
    act(() => {
      es.emit("phase", { phase: "hydrated", status: "running", total: 3 });
    });

    expect(localStorage.getItem(LS_JOB_KEY)).not.toBeNull();
  });

  it("still clears LS_JOB_KEY via the normal 'done' phase (no regression)", () => {
    render(<FixJobProvider><Probe /></FixJobProvider>);
    act(() => { window.__testFixJob.startJob({ job_id: "job-done", total: 1 }); });

    const es = MockEventSource.instances[MockEventSource.instances.length - 1];
    act(() => {
      es.emit("phase", { phase: "done", ok: true, completed: 1, failed: 0 });
    });

    expect(localStorage.getItem(LS_JOB_KEY)).toBeNull();
  });
});
