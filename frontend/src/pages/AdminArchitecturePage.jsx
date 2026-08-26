/**
 * pages/AdminArchitecturePage.jsx — Admin "Architecture" tab.
 *
 * 2026-08-27 · Admin Compact M6 — extracted verbatim from Admin.jsx's
 * inline Architecture() (+ its two private sub-tiles PersonaQualityTile
 * and CodeSurfaceLive) so this tab code-splits into its own chunk.
 * SupervisedTasksTile / IntentGateTile / LearningHealthTile stay in
 * Admin.jsx (they're `export`ed there and imported directly by a test
 * file) — imported back in here unchanged.
 */
import React, { useState, useEffect } from "react";
import { api } from "../lib/api";
import {
  Card, Badge,
  SupervisedTasksTile, IntentGateTile, LearningHealthTile,
} from "./Admin";

export default function AdminArchitecturePage() {
  const [d, setD] = useState(null);
  useEffect(() => {
    api.get("/admin/architecture").then((r) => setD(r.data)).catch(() => {});
  }, []);
  if (!d) return <div style={{ padding: 24, color: "var(--text-faint)" }}>Loading…</div>;
  // Iter 64 — sort: live → degraded → unreachable so green stays on top
  const order = { live: 0, degraded: 1, unreachable: 2, down: 3 };
  const services = Object.entries(d.services).sort(
    ([, a], [, b]) => (order[a.status] ?? 9) - (order[b.status] ?? 9)
  );
  return (
    <div style={{ padding: 24 }}>
      <PersonaQualityTile />
      <LearningHealthTile />
      <IntentGateTile />
      <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                    color: "var(--text-faint)", margin: "0 0 8px" }}>External services</h3>
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
        gap: 12, marginBottom: 18,
      }}>
        {services.map(([name, info]) => (
          <Card key={name} style={{ padding: 14 }}>
            <div style={{ display: "flex", justifyContent: "space-between",
                          alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <b style={{ fontSize: 13, overflowWrap: "anywhere" }}>{name}</b>
              <Badge color={
                info.status === "live" ? "var(--ok)" :
                info.status === "degraded" ? "var(--warn, #ffc560)" :
                "var(--danger)"
              }>
                {info.status}
              </Badge>
            </div>
            <div style={{ fontSize: 11, color: "var(--text-faint)", marginTop: 6,
                           fontFamily: "'JetBrains Mono', monospace" }}>
              {info.latency_ms != null ? `${info.latency_ms}ms` : "—"}
              {info.note ? ` · ${info.note}` : ""}
            </div>
          </Card>
        ))}
      </div>
      <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                    color: "var(--text-faint)", margin: "0 0 8px" }}>Integrations</h3>
      <Card style={{ padding: 14 }}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          {Object.entries(d.integrations).map(([k, v]) => (
            <Badge key={k} color={v ? "var(--ok)" : "var(--text-faint)"}>
              {k} · {v ? "OK" : "missing"}
            </Badge>
          ))}
        </div>
        {d.note && (
          <div style={{ marginTop: 12, fontSize: 11, color: "var(--text-dim)", lineHeight: 1.6 }}>
            {d.note}
          </div>
        )}
      </Card>

      <h3 style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase",
                    color: "var(--text-faint)", margin: "22px 0 8px" }}>
        Code surface · routers · services · pages
      </h3>
      <CodeSurfaceLive />
      <SupervisedTasksTile />
    </div>
  );
}

function PersonaQualityTile() {
  const [d, setD] = useState(null);
  useEffect(() => {
    api.get("/admin/eval-quality").then((r) => setD(r.data)).catch(() => {});
  }, []);
  if (!d) return null;
  const t = d.totals || {};
  const latest = d.latest || {};
  const score = latest.total
    ? Math.round(100 * (latest.passed / latest.total))
    : null;
  const blocked = (latest.hard_fails || 0) > 0;
  const color = blocked ? "var(--danger)"
              : score == null ? "var(--text-faint)"
              : score >= 90 ? "var(--ok)"
              : score >= 75 ? "var(--warn, #ffc560)"
              : "var(--danger)";
  return (
    <div data-testid="persona-quality-tile" style={{ marginBottom: 18 }}>
      <h3 style={{ fontSize: 12, letterSpacing: "0.1em",
        textTransform: "uppercase", color: "var(--text-faint)",
        margin: "0 0 8px" }}>Persona Quality Score · last 30 days</h3>
      <Card style={{ padding: 16 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 14, flexWrap: "wrap" }}>
          <div style={{ fontSize: 30, fontWeight: 700, color, fontFamily: "'JetBrains Mono', monospace" }}>
            {score == null ? "—" : `${score}/100`}
          </div>
          <div style={{ fontSize: 11, color: "var(--text-faint)" }}>
            latest: {latest.passed ?? 0}/{latest.total ?? 0} pass ·
            hard fails {latest.hard_fails ?? 0} · runs {t.runs ?? 0}
          </div>
          <div style={{ marginLeft: "auto", display: "flex", gap: 3, alignItems: "flex-end", height: 22 }}>
            {(d.trend || []).slice(-30).map((p, i) => (
              <div key={i} title={`${p.ts} — ${p.score}/100 · ${p.hard_fails} hard fail(s)`}
                style={{
                  width: 5,
                  height: Math.max(3, Math.round((p.score / 100) * 22)),
                  background: p.hard_fails > 0 ? "var(--danger)"
                            : p.score >= 90 ? "var(--ok)"
                            : "var(--warn, #ffc560)",
                  borderRadius: 1,
                }} />
            ))}
          </div>
        </div>
      </Card>
    </div>
  );
}

function CodeSurfaceLive() {
  const [data, setData] = useState(null);
  const [err, setErr]   = useState(null);
  useEffect(() => {
    api.get("/admin/code-surface")
      .then((r) => setData(r.data))
      .catch((e) => setErr(e?.response?.data?.detail || e?.message || "unreachable"));
  }, []);
  if (err) {
    return (
      <div data-testid="arch-code-surface-error" style={{
        padding: 14,
        border: "1px solid rgba(226,75,74,0.3)",
        background: "rgba(226,75,74,0.08)",
        borderRadius: 8,
        color: "var(--text-dim)",
        fontSize: 12,
      }}>
        Code surface unreachable: <code>{err}</code>
      </div>
    );
  }
  if (!data) {
    return (
      <div style={{ padding: 14, color: "var(--text-faint)", fontSize: 12 }}>
        Loading code surface…
      </div>
    );
  }
  const surface = data.surface || {};
  const columns = [
    { key: "routers",    title: "Routers" },
    { key: "services",   title: "Services" },
    { key: "pages",      title: "Pages" },
    { key: "components", title: "Components" },
  ];
  return (
    <>
      <div style={{
        fontSize: 11, color: "var(--text-faint)", marginBottom: 10,
      }}>
        Live · {data.total_files} files across 4 surfaces · auto-walked from disk
        {" · "}drift-proof (no hand-maintained list)
      </div>
      <div data-testid="arch-code-surface" style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
        gap: 12,
      }}>
        {columns.map((col) => {
          const items = surface[col.key] || [];
          return (
            <Card key={col.key} style={{ padding: 14 }}>
              <div style={{
                fontSize: 10, letterSpacing: "0.08em", textTransform: "uppercase",
                color: "var(--accent-2, #ffb347)", marginBottom: 8,
                fontWeight: 600,
              }}>{col.title} · {items.length}</div>
              <ul style={{ listStyle: "none", margin: 0, padding: 0,
                            display: "grid", gap: 4,
                            maxHeight: 360, overflowY: "auto" }}>
                {items.map((it) => (
                  <li key={it.file || it.name}
                      title={it.desc || ""}
                      style={{
                    fontSize: 11.5, color: "var(--text-dim)",
                    fontFamily: "'JetBrains Mono', monospace",
                    display: "flex", justifyContent: "space-between",
                    gap: 8,
                  }}>
                    <span style={{ overflowWrap: "anywhere" }}>{it.file || it.name}</span>
                    {it.lines > 0 && (
                      <span style={{
                        color: "var(--text-faint)", fontSize: 10,
                        whiteSpace: "nowrap", flexShrink: 0,
                      }}>{it.lines}L</span>
                    )}
                  </li>
                ))}
              </ul>
            </Card>
          );
        })}
      </div>
    </>
  );
}
