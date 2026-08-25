/**
 * draftReviewHelpers.js — file-bucketing + toggle-button style helpers
 * for DraftReview.jsx. Extracted (2026-08-27, mechanical split — no
 * behaviour change) to keep that page under the platform's file-size
 * guard.
 */
import { Palette, Database, Settings2, FileText } from "lucide-react";

/** Rule-based classification of a scaffolded path → a friendly bucket. */
export function bucketFor(path) {
  const p = (path || "").toLowerCase();
  if (p.startsWith("ui/") || p.endsWith(".jsx") || p.endsWith(".tsx") ||
      p.endsWith(".html") || p.endsWith(".vue") || p.endsWith(".css"))
    return "frontend";
  if (p.includes("db") || p.includes("schema") || p.includes(".sql") ||
      p.includes("model") || p.includes("aurem_db_client"))
    return "database";
  if (p === "readme.md") return "readme";
  return "logic";
}

export const BUCKETS = [
  { key: "frontend", label: "Frontend",       Icon: Palette   },
  { key: "database", label: "Database",       Icon: Database  },
  { key: "logic",    label: "Logic & Config", Icon: Settings2 },
  { key: "readme",   label: "Overview",       Icon: FileText  },
];

export function toggleBtn(active) {
  return {
    display: "inline-flex", alignItems: "center", gap: 6,
    padding: "6px 14px", borderRadius: 999,
    background: active ? "#FFFFFF" : "transparent",
    color: active ? "#1C1C19" : "#6B6B63",
    border: "none", cursor: "pointer",
    fontSize: 13, fontWeight: 600, fontFamily: "inherit",
    boxShadow: active ? "0 1px 3px rgba(28,28,25,0.10)" : "none",
    transition: "background 200ms ease, color 200ms ease",
  };
}
