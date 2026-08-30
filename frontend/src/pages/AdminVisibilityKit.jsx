/**
 * AdminVisibilityKit.jsx — Visibility Kit admin tile (spec §6).
 * Per-project kit status/score/applied items + pass rate. Citation
 * section is an HONEST PLACEHOLDER (A7 day-14 recheck not wired this
 * round, founder's explicit call) — never fabricated numbers.
 */
import { useEffect, useState, useCallback } from "react";
import { Loader2, RefreshCw } from "lucide-react";
import { api } from "../lib/api";

export default function AdminVisibilityKit() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/admin/visibility-kit/dashboard");
      setData(data);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <div data-testid="admin-visibility-kit-dashboard" style={{ padding: 24, color: "#F5F5F5" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <h2 style={{ fontFamily: "Jost, sans-serif", fontSize: 18, fontWeight: 600, margin: 0 }}>
          Visibility Kit
        </h2>
        <button data-testid="admin-kit-refresh" onClick={load}
          style={{ background: "transparent", border: "1px solid #333", color: "#F5F5F5",
            borderRadius: 8, padding: "6px 12px", fontSize: 12.5, cursor: "pointer",
            display: "flex", alignItems: "center", gap: 6 }}>
          <RefreshCw size={13} /> Refresh
        </button>
      </div>

      {loading && !data ? (
        <Loader2 className="animate-spin" size={20} />
      ) : (
        <>
          <div style={{ display: "flex", gap: 24, marginBottom: 20 }}>
            <Stat label="Projects scanned" value={data?.total_projects_scanned ?? 0} />
            <Stat label="Pass rate" value={`${data?.pass_rate_pct ?? 0}%`} />
          </div>

          <div data-testid="admin-kit-citation-section" style={{
            padding: 14, background: "#161616", border: "1px solid #222",
            borderRadius: 10, marginBottom: 20, fontSize: 13, color: "#9A9A93",
          }}>
            {data?.citation_data?.available
              ? JSON.stringify(data.citation_data)
              : (data?.citation_data?.message || "No citation data yet.")}
          </div>

          <table style={{ width: "100%", fontSize: 13, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", color: "#9A9A93", borderBottom: "1px solid #222" }}>
                <th style={{ padding: "6px 8px" }}>User</th>
                <th style={{ padding: "6px 8px" }}>Repo</th>
                <th style={{ padding: "6px 8px" }}>Score</th>
                <th style={{ padding: "6px 8px" }}>Applied</th>
                <th style={{ padding: "6px 8px" }}>Status</th>
              </tr>
            </thead>
            <tbody>
              {(data?.projects || []).map((p) => (
                <tr key={p.project_id} data-testid={`admin-kit-row-${p.project_id}`}
                  style={{ borderBottom: "1px solid #1E1E1E" }}>
                  <td style={{ padding: "8px" }}>{p.user_email}</td>
                  <td style={{ padding: "8px" }}>{p.repo}</td>
                  <td style={{ padding: "8px" }}>{p.score}/100</td>
                  <td style={{ padding: "8px" }}>{p.applied_items.join(", ") || "—"}</td>
                  <td style={{ padding: "8px" }}>{p.live ? "Live" : "Pending"}</td>
                </tr>
              ))}
              {!data?.projects?.length && (
                <tr><td colSpan={5} style={{ padding: 12, color: "#9A9A93" }}>No projects scanned yet.</td></tr>
              )}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div>
      <div style={{ fontSize: 22, fontWeight: 700 }}>{value}</div>
      <div style={{ fontSize: 12, color: "#9A9A93" }}>{label}</div>
    </div>
  );
}
