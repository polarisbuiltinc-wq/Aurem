/**
 * ChatPanel.ltp_show_once_2026_08_30.test.jsx
 *
 * Founder report: "ye screen jb bhe new login ya refresh hota hai aa
 * jati hai" — the floating "ORA done" popup (LiveTaskPopup) reopened
 * on every fresh login/page refresh because the effect that surfaces
 * it fires whenever `latestAssistant.shipped_task_id` is set — which
 * is also true right after chat history reloads from the backend on
 * mount, even for a task that finished long ago.
 *
 * Source-level lock-in (ChatPanel is too heavy to mount without full
 * SSE/Mongo fixtures — same approach as ChatPanel.loop_chip_reset).
 */
import fs from "fs";
import path from "path";
import { describe, it, expect } from "vitest";

describe("ChatPanel — LiveTaskPopup shows only once per task_id", () => {
  const src = fs.readFileSync(
    path.resolve(__dirname, "../ChatPanel.jsx"),
    "utf-8",
  );

  it("persists seen task ids in localStorage (survives refresh/login)", () => {
    expect(src).toContain('_LTP_SEEN_KEY = "ora_ltp_seen_ids"');
    expect(src).toContain("function _isTaskPopupSeen(taskId)");
    expect(src).toContain("function _markTaskPopupSeen(taskId)");
  });

  it("gates the history-driven effect on _isTaskPopupSeen before reopening", () => {
    expect(src).toMatch(
      /const taskId = latestAssistant\?\.shipped_task_id;\n\s*if \(!taskId\) return;/,
    );
    // the seen-check must sit between the taskId guard and the effect
    // that actually opens the SSE stream / surfaces the popup.
    const guardIdx = src.indexOf("const taskId = latestAssistant?.shipped_task_id;");
    const nextEffectIdx = src.indexOf("_markTaskPopupSeen(taskId);\n    setLivePopupTaskId(taskId);");
    const seenCheckIdx = src.indexOf("if (_isTaskPopupSeen(taskId)) return;");
    expect(seenCheckIdx).toBeGreaterThan(guardIdx);
    expect(seenCheckIdx).toBeLessThan(nextEffectIdx);
  });

  it("marks the task seen the moment it is first surfaced from history", () => {
    expect(src).toMatch(/_markTaskPopupSeen\(taskId\);\s*\n\s*setLivePopupTaskId\(taskId\);/);
  });

  it("also gates the live ora-task-handoff window-event path", () => {
    expect(src).toMatch(
      /if \(tid && !_isTaskPopupSeen\(tid\)\) \{\s*\n\s*_markTaskPopupSeen\(tid\);\s*\n\s*setLivePopupTaskId\(tid\);/,
    );
  });
});
