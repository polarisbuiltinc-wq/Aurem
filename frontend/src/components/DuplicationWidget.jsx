/**
 * components/DuplicationWidget.jsx — Code duplication scan (2026-08-26)
 *
 * Thin consumer of GET /admin/duplication (services/duplication_scanner.py,
 * jscpd). Phase 3a research: no existing duplication tool in this repo;
 * this is the real, live, no-invented-numbers version of that gap.
 */
import { useCallback, useEffect } from "react";
import { api } from "../lib/api";
import { useAsyncState } from "../hooks/useAsyncState";

const C = {
  panel:  "#101013",
  border: "rgba(255,255,255,0.10)",
  text:   "#e5e5e5",
  faint:  "#5f5f5f",
  dim:    "#8a8a8a",
  amber:  "#f5a524",
  red:    "#ef4444",
  green:  "#22c55e",
  mono:   "SFMono-Regular, Menlo, Consolas, monospace",
};

function pctColor(pct) {
  if (pct >= 8) return C.red;
  if (pct >= 3) return C.amber;
  return C.green;
}

export default function DuplicationWidget() {
  // 65s margin over duplication_scanner.py's 60s subprocess timeout
  // (cold `npx jscpd` fetch on a fresh env can be slow; ~2s once cached).
  const { data, isProcessing, isFailed, run } = useAsyncState(65000);

  const load = useCallback(() => {
    return run(() => api.get("/admin/duplication").then((r) => r.data));
  }, [run]);

  useEffect(() => { load(); }, [load]);

  return (
    <div data-testid="duplication-widget" style={{
      background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10,
      padding: 16, fontFamily: C.mono,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
        <span style={{ color: C.text, fontSize: 13, fontWeight: 600 }}>Code Duplication (jscpd)</span>
        <button data-testid="duplication-refresh-button" onClick={load}
          style={{ background: "transparent", border: `1px solid ${C.border}`, color: C.dim,
                   borderRadius: 6, padding: "2px 8px", fontSize: 11, cursor: "pointer" }}>
          rescan
        </button>
      </div>

      {isProcessing && !data && (
        <div data-testid="duplication-loading" style={{ color: C.dim, fontSize: 12 }}>Running jscpd…</div>
      )}
      {isFailed && (
        <div data-testid="duplication-error" style={{ color: C.red, fontSize: 12 }}>Could not load duplication scan.</div>
      )}
      {data && data.ok === false && (
        <div data-testid="duplication-unavailable" style={{ color: C.dim, fontSize: 12 }}>
          {data.reason || "unavailable"}
        </div>
      )}
      {data && data.ok && (
        <>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 8 }}>
            <span data-testid="duplication-pct" style={{ color: pctColor(data.duplication_pct), fontSize: 22, fontWeight: 700 }}>
              {data.duplication_pct}%
            </span>
            <span style={{ color: C.faint, fontSize: 11 }}>
              {data.duplicated_lines} / {data.total_lines} lines · {data.clone_count} clones · {data.files_scanned} files
            </span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4, maxHeight: 160, overflowY: "auto" }}>
            {data.top_clusters.slice(0, 8).map((c, i) => (
              <div key={i} data-testid={`duplication-cluster-${i}`}
                   style={{ fontSize: 11, color: C.dim, display: "flex", justifyContent: "space-between" }}>
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: "75%" }}
                      title={`${c.file_a} \u2194 ${c.file_b}`}>
                  {c.file_a} ↔ {c.file_b}
                </span>
                <span>{c.lines}L</span>
              </div>
            ))}
            {data.top_clusters.length === 0 && (
              <div style={{ color: C.dim, fontSize: 12 }}>No duplicate clusters above the threshold.</div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
