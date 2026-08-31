/**
 * pages/AdminSelfBugs.jsx — Item 3 (2026-08-31)
 *
 * READ-ONLY self-repair dashboard for P7's structured bug ledger
 * (`ora_self_bugs` + the learned-recurrence counter). Fetches from
 * `GET /admin/self-bugs/list`. No mutate/decide action anywhere on
 * this page — the ledger is the audit trail, not a queue to clear.
 */
import React, { useCallback, useEffect, useState } from "react";
import { api, getToken } from "../lib/api";

const SEVERITY_COLOR = {
  high: "#ef4444",
  low: "#f59e0b",
};

export default function AdminSelfBugs() {
  const [type, setType] = useState("");
  const [types, setTypes] = useState([]);
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const h = { Authorization: `Bearer ${getToken()}` };
      const q = type ? `?type=${type}` : "";
      const r = await api.get(`/admin/self-bugs/list${q}`, { headers: h });
      setRows(r.data?.self_bugs || []);
      setTypes(r.data?.types || []);
    } catch (e) {
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [type]);

  useEffect(() => { load(); }, [load]);

  return (
    <div style={{ padding: "24px 20px", maxWidth: 1100 }}
         data-testid="admin-self-bugs-panel">
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 18 }}>
        <div>
          <h1 style={{ fontSize: 20, fontWeight: 600, margin: 0, color: "var(--text)" }}>
            ORA Self-Repair Log
          </h1>
          <p style={{ fontSize: 12, color: "var(--text-faint)", margin: "4px 0 0" }}>
            Read-only — bugs ORA found in its OWN UI/replies, not the user's website.
            Sorted by recurrence, so "this keeps happening" bubbles to the top.
          </p>
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          <button
            onClick={() => setType("")}
            data-testid="admin-self-bugs-filter-all"
            style={{
              padding: "5px 12px", fontSize: 11,
              background: type === "" ? "var(--accent-2)" : "var(--panel-2)",
              color: type === "" ? "#000" : "var(--text-dim)",
              border: "1px solid var(--border)", borderRadius: 4,
              cursor: "pointer", textTransform: "uppercase", letterSpacing: 0.6,
            }}>
            All
          </button>
          {types.map((t) => (
            <button
              key={t}
              onClick={() => setType(t)}
              data-testid={`admin-self-bugs-filter-${t}`}
              style={{
                padding: "5px 12px", fontSize: 11,
                background: type === t ? "var(--accent-2)" : "var(--panel-2)",
                color: type === t ? "#000" : "var(--text-dim)",
                border: "1px solid var(--border)", borderRadius: 4,
                cursor: "pointer", textTransform: "uppercase", letterSpacing: 0.6,
              }}>
              {t.replace(/_/g, " ")}
            </button>
          ))}
        </div>
      </div>

      {loading && (
        <div style={{ color: "var(--text-faint)", fontSize: 13, padding: "24px 0" }}>
          Loading self-repair log…
        </div>
      )}

      {!loading && rows.length === 0 && (
        <div data-testid="admin-self-bugs-empty"
             style={{ color: "var(--text-faint)", fontSize: 13, padding: "24px 0" }}>
          No self-bugs logged yet.
        </div>
      )}

      {!loading && rows.length > 0 && (
        <table data-testid="admin-self-bugs-table" style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ textAlign: "left", color: "var(--text-faint)", borderBottom: "1px solid var(--border)" }}>
              <th style={{ padding: "6px 8px" }}>Type</th>
              <th style={{ padding: "6px 8px" }}>What the user saw</th>
              <th style={{ padding: "6px 8px" }}>Likely cause</th>
              <th style={{ padding: "6px 8px" }}>Severity</th>
              <th style={{ padding: "6px 8px" }}>Times seen</th>
              <th style={{ padding: "6px 8px" }}>Last seen</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} data-testid={`admin-self-bugs-row-${i}`}
                  style={{ borderBottom: "1px solid var(--border)" }}>
                <td style={{ padding: "8px" }}>{r.type}</td>
                <td style={{ padding: "8px", color: "var(--text-dim)" }}>{r.what_user_saw}</td>
                <td style={{ padding: "8px", color: "var(--text-dim)" }}>{r.likely_cause}</td>
                <td style={{ padding: "8px" }}>
                  <span style={{ color: SEVERITY_COLOR[r.severity] || "var(--text-dim)" }}>
                    {r.severity}
                  </span>
                </td>
                <td style={{ padding: "8px", fontWeight: r.times_seen > 1 ? 700 : 400 }}
                    data-testid={`admin-self-bugs-times-seen-${i}`}>
                  {r.times_seen}
                </td>
                <td style={{ padding: "8px", color: "var(--text-faint)" }}>
                  {r.last_seen ? new Date(r.last_seen).toLocaleString() : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
