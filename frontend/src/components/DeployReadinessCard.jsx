/**
 * DeployReadinessCard.jsx — Option A (2026-08-24).
 * ADVISORY card: compares workspace SHA vs GitHub main and shows the
 * CI + Quality Gate conclusion for that exact SHA (Rule C). It cannot
 * block the Emergent Deploy button — platform has no deploy gate.
 */
import React from "react";

const chip = (ok, label) => (
  <span
    data-testid={`deploy-readiness-chip-${label.toLowerCase().replace(/\s+/g, "-")}`}
    style={{
      padding: "2px 8px", borderRadius: 3, fontSize: 10, fontWeight: 700,
      fontFamily: "'JetBrains Mono', monospace", letterSpacing: "0.04em",
      color: ok === true ? "#4ade80" : ok === false ? "#f87171" : "#fbbf24",
      background: ok === true ? "rgba(74,222,128,0.08)" : ok === false ? "rgba(248,113,113,0.08)" : "rgba(251,191,36,0.08)",
      border: `1px solid ${ok === true ? "#4ade8040" : ok === false ? "#f8717140" : "#fbbf2440"}`,
    }}
  >
    {label}
  </span>
);

export const DeployReadinessCard = ({ data }) => {
  if (!data) return null;
  const ready = data.verdict === "ready";
  const ws = data.workspace || {};
  const remote = data.remote || {};
  const ci = (remote.checks || {}).ci;
  const qg = (remote.checks || {}).quality_gate;
  const concl = (c) => (c ? c.conclusion || c.status || "—" : "not run");
  return (
    <div
      data-testid="deploy-readiness-card"
      style={{
        marginBottom: 14, padding: "10px 14px",
        background: "var(--panel-2)", border: `1px solid ${ready ? "#4ade8050" : "#f8717150"}`,
        borderRadius: 4, fontSize: 11, color: "var(--text-dim)",
        fontFamily: "'JetBrains Mono', monospace",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <span
          data-testid="deploy-readiness-verdict"
          style={{ fontWeight: 800, fontSize: 12, letterSpacing: "0.06em",
                   color: ready ? "#4ade80" : "#f87171" }}
        >
          {ready ? "DEPLOY READY" : "NOT DEPLOY-READY"}
        </span>
        <span style={{ opacity: 0.55, fontSize: 9 }}>(advisory — Rule C, cannot block the Deploy button)</span>
        <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
          {chip(data.workspace_matches_remote, ws.short_sha && remote.short_sha
            ? `ws ${ws.short_sha} ${data.workspace_matches_remote ? "=" : "≠"} gh ${remote.short_sha}`
            : "sha unknown")}
          {chip(ci ? ci.conclusion === "success" : null, `CI ${concl(ci)}`)}
          {chip(qg ? qg.conclusion === "success" : null, `QG ${concl(qg)}`)}
        </span>
      </div>
      {!ready && (data.reasons || []).length > 0 && (
        <ul data-testid="deploy-readiness-reasons" style={{ margin: "8px 0 0", paddingLeft: 18 }}>
          {data.reasons.map((r, i) => (
            <li key={i} style={{ color: "#fbbf24", marginBottom: 2 }}>{r}</li>
          ))}
        </ul>
      )}
    </div>
  );
};
