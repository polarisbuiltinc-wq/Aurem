/**
 * LiveBusinessIntelligence.jsx — 2026-02-18
 *
 * Self-contained BI panel that pulls live Stripe subscription data
 * (MRR / ARR / active-subs / churn) + inference-cost aggregates from
 * `ora_chat_usage` + the shared budget tracker.  Extracted from
 * AdminFinancials.jsx so it can be mounted inside AdminCockpit as
 * the single source of truth — no more two dashboards showing
 * conflicting numbers for the same underlying data.
 *
 * Owns its own data-fetch + reconcile handler so the parent page
 * doesn't need to know anything about the BI contract.
 *
 * Data source (single):
 *   GET  /admin/bi/summary          — live payload
 *   POST /admin/payments/reconcile  — orphan cleanup (button)
 *
 * No-hallucination rule: any zero-value card labeled explicitly as
 * "No data yet" (not $0.00) when the backend confirms the source is
 * healthy but empty; error state shows the API error verbatim.
 */
import React, { useEffect, useState, useCallback } from "react";
import {
  LineChart, Line, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Cell,
} from "recharts";
import { api, getToken } from "../lib/api";
import { cleanErr } from "../lib/cleanErr";

function MetricCard({ label, value, sub, tone, testid }) {
  const colors = {
    danger: "#ff6b6b", warn: "#ffb454", ok: "#6dd4a1", neutral: "var(--text)",
  };
  return (
    <div data-testid={`metric-${testid}`} style={{
      padding: "16px 16px 14px",
      background: "var(--panel)",
      border: "1px solid var(--border)",
      borderRadius: 8,
      display: "flex", flexDirection: "column", gap: 4,
    }}>
      <div style={{
        fontSize: 11, color: "var(--text-dim)",
        letterSpacing: ".04em", textTransform: "uppercase",
      }}>{label}</div>
      <div style={{
        fontSize: 22, fontWeight: 600,
        color: colors[tone] || colors.neutral, marginTop: 2,
      }}>{value}</div>
      {sub && <div style={{ fontSize: 10, color: "var(--text-faint)", marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

function dollars(n, signed = false) {
  const v = Number(n) || 0;
  const abs = Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 });
  if (signed && v < 0) return `-$${abs}`;
  if (signed && v > 0) return `+$${abs}`;
  return `$${abs}`;
}

export default function LiveBusinessIntelligence() {
  const [bi, setBi] = useState(null);
  const [biErr, setBiErr] = useState("");
  const [reconciling, setReconciling] = useState(false);
  const [reconcileMsg, setReconcileMsg] = useState("");

  const loadBi = useCallback(async () => {
    setBiErr("");
    try {
      const r = await api.get("/admin/bi/summary", {
        headers: { Authorization: `Bearer ${getToken()}` },
      });
      setBi(r.data);
    } catch (e) {
      setBiErr(cleanErr(e, "Failed to load BI cockpit."));
    }
  }, []);

  const reconcile = useCallback(async () => {
    setReconciling(true);
    setReconcileMsg("");
    try {
      const r = await api.post("/admin/payments/reconcile", {}, {
        headers: { Authorization: `Bearer ${getToken()}` },
      });
      const c = r.data?.counts || {};
      setReconcileMsg(
        `Scanned ${r.data?.scanned || 0} · paid=${c.paid || 0} expired=${c.expired || 0} open=${c.open || 0} error=${c.error || 0}`
      );
      await loadBi();
    } catch (e) {
      setReconcileMsg(cleanErr(e, "Reconcile failed."));
    } finally {
      setReconciling(false);
    }
  }, [loadBi]);

  useEffect(() => { loadBi(); }, [loadBi]);

  if (biErr) {
    return (
      <div data-testid="bi-error" style={{
        fontSize: 12, padding: "10px 14px", marginBottom: 24,
        background: "rgba(255,107,107,0.06)",
        border: "1px solid rgba(255,107,107,0.2)",
        color: "var(--danger)", borderRadius: 5,
      }}>
        BI cockpit: {biErr}
        <button
          data-testid="bi-retry-btn"
          onClick={loadBi}
          style={{
            marginLeft: 10, padding: "4px 10px",
            background: "transparent", border: "1px solid var(--border)",
            borderRadius: 4, color: "var(--text-dim)", cursor: "pointer",
            fontSize: 11,
          }}
        >Retry</button>
      </div>
    );
  }
  if (!bi) {
    return (
      <div data-testid="bi-loading" style={{
        fontSize: 12, color: "var(--text-dim)", marginBottom: 24,
      }}>Loading BI cockpit…</div>
    );
  }

  const s = bi.stripe || {};
  const i = bi.inference || {};
  const budget = i.budget || {};
  const mrr = Number(s.mrr_usd || 0);
  const arr = Number(s.arr_usd || 0);
  const activeSubs = Number(s.active_subs || 0);
  const monthInfer = Number(i.month_usd || 0);
  const projInfer = Number(bi.projected_month_infer_usd || 0);
  const netMargin = Number(bi.net_margin_usd || 0);
  const netMarginPct = Number(bi.net_margin_pct || 0);

  const stripeStatusTone =
    s.status === "ok" ? "#6dd4a1" :
    s.status === "missing_key" ? "#ffb454" :
    "#ff6b6b";

  const budgetTone =
    budget.mode === "normal"          ? "ok" :
    budget.mode === "warning"         ? "warn" :
    budget.mode === "economy"         ? "warn" :
    budget.mode === "spike_hard_stop" ? "danger" :
    "neutral";

  const series = (i.daily_series_30d || []).map(d => ({
    day: (d.day || "").slice(5),
    cost: Number(d.cost || 0),
    calls: d.calls,
  }));
  const byModel = (i.by_model || []).map(m => ({
    model: (m.model || "").split("/").slice(-1)[0].slice(0, 22),
    cost: Number(m.cost || 0),
    calls: m.calls,
  }));
  const modelColors = ["#6dd4a1", "#5eb3f5", "#ffb454", "#a78bfa", "#ff6b6b", "#f472b6"];

  return (
    <section data-testid="bi-cockpit" style={{
      padding: "20px 20px 24px",
      background: "linear-gradient(180deg, rgba(94,179,245,0.03) 0%, rgba(94,179,245,0.00) 60%)",
      border: "1px solid var(--border)",
      borderRadius: 10, marginTop: 14,
    }}>
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "center", marginBottom: 16, flexWrap: "wrap", gap: 10,
      }}>
        <div>
          <h2 style={{
            fontSize: 16, fontWeight: 500, margin: 0,
            letterSpacing: "-.01em",
          }}>Live Business Intelligence</h2>
          <div style={{ fontSize: 11, color: "var(--text-faint)", marginTop: 4 }}>
            Real Stripe · real inference cost from{" "}
            <code>ora_chat_usage</code> + <code>customer_chat_cost</code> · Slice A
            <span style={{
              marginLeft: 10, padding: "2px 8px",
              background: "var(--panel)",
              border: `1px solid ${stripeStatusTone}`,
              borderRadius: 10, color: stripeStatusTone,
              fontSize: 10, letterSpacing: ".04em", textTransform: "uppercase",
            }} data-testid="bi-stripe-status-badge">
              Stripe · {s.status || "?"} · {s.mode || "?"}
            </span>
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button
            data-testid="bi-reload-btn"
            onClick={loadBi}
            style={{
              padding: "6px 12px", fontSize: 11,
              background: "transparent", color: "var(--text-dim)",
              border: "1px solid var(--border)", borderRadius: 5,
              cursor: "pointer",
            }}
          >↻ Refresh BI</button>
          <button
            data-testid="reconcile-orphans-btn"
            onClick={reconcile}
            disabled={reconciling}
            style={{
              padding: "6px 12px", fontSize: 11,
              background: "transparent", color: "var(--accent, #5eb3f5)",
              border: "1px solid var(--accent, #5eb3f5)", borderRadius: 5,
              cursor: reconciling ? "wait" : "pointer",
              opacity: reconciling ? 0.6 : 1,
            }}
            title="Pull every non-paid cto_payments row from Stripe and sync the real status. Safe — never overwrites paid rows."
          >
            {reconciling ? "Reconciling…" : "🧹 Reconcile Orphans"}
          </button>
        </div>
      </div>

      {reconcileMsg && (
        <div data-testid="reconcile-msg" style={{
          fontSize: 11, padding: "8px 12px", marginBottom: 14,
          background: "var(--panel)", border: "1px solid var(--border)",
          borderRadius: 5, color: "var(--text-dim)",
          fontFamily: "ui-monospace, monospace",
        }}>
          {reconcileMsg}
        </div>
      )}

      {/* Stripe row */}
      <div style={{
        display: "grid", gridTemplateColumns: "repeat(5, 1fr)",
        gap: 12, marginBottom: 12,
      }}>
        <MetricCard testid="bi-mrr" label="MRR (live)"
          value={mrr > 0 ? dollars(mrr) : (s.status === "ok" ? "No data yet" : "—")}
          tone={mrr > 0 ? "ok" : "neutral"}
          sub={s.status === "ok" ? "Stripe subscriptions" : (s.error || "not configured")} />
        <MetricCard testid="bi-arr" label="ARR"
          value={arr > 0 ? dollars(arr) : "—"}
          tone={arr > 0 ? "ok" : "neutral"}
          sub="MRR × 12" />
        <MetricCard testid="bi-active-subs" label="Active subs"
          value={activeSubs}
          tone={activeSubs > 0 ? "ok" : "neutral"}
          sub={`+ ${s.trialing_subs || 0} trialing · ${s.past_due_subs || 0} past-due`} />
        <MetricCard testid="bi-new-30d" label="New (30d)"
          value={s.new_30d || 0}
          tone={(s.new_30d || 0) > 0 ? "ok" : "neutral"}
          sub="signups → paid" />
        <MetricCard testid="bi-canceled-30d" label="Canceled (30d)"
          value={s.canceled_30d || 0}
          tone={(s.canceled_30d || 0) > 0 ? "warn" : "neutral"}
          sub="churn events" />
      </div>

      {/* Inference row */}
      <div style={{
        display: "grid", gridTemplateColumns: "repeat(4, 1fr)",
        gap: 12, marginBottom: 18,
      }}>
        <MetricCard testid="bi-infer-today" label="Infer today (combined)"
          value={`$${Number(i.today_usd || 0).toFixed(4)}`}
          tone="neutral"
          sub={`admin tool $${Number(i.admin_tool_today_usd || 0).toFixed(4)} · customer chat $${Number(i.customer_chat_today_usd || 0).toFixed(4)}`} />
        <MetricCard testid="bi-infer-month" label="Infer month-to-date (combined)"
          value={`$${monthInfer.toFixed(4)}`}
          tone="neutral"
          sub={`admin tool $${Number(i.admin_tool_month_usd || 0).toFixed(4)} · customer chat $${Number(i.customer_chat_month_usd || 0).toFixed(4)}`} />
        <MetricCard testid="bi-budget-mode" label="Admin-tool budget mode"
          value={(budget.mode || "unknown").replace("_", " ")}
          tone={budgetTone}
          sub={budget.mode === "normal" ? "healthy (admin-tool cap only)" :
               budget.mode === "warning" ? "70%+ of daily cap (admin-tool)" :
               budget.mode === "economy" ? "forced GLM-5.2 route" :
               budget.mode === "spike_hard_stop" ? "chat blocked" : ""} />
        <MetricCard testid="bi-net-margin" label="Net margin (proj.)"
          value={mrr > 0 ? dollars(netMargin, true) : "—"}
          tone={mrr === 0 ? "neutral" : netMargin > 0 ? "ok" : "danger"}
          sub={mrr > 0 ? `${netMarginPct}% · MRR - proj. month infer $${projInfer}` :
               "needs paying customers"} />
      </div>

      {/* Charts */}
      <div style={{
        display: "grid", gridTemplateColumns: "1.4fr 1fr",
        gap: 14,
      }}>
        <div data-testid="bi-cost-chart" style={{
          padding: "16px 18px", background: "var(--panel)",
          border: "1px solid var(--border)", borderRadius: 8,
        }}>
          <h3 style={{
            fontSize: 11, color: "var(--text-dim)",
            letterSpacing: ".08em", textTransform: "uppercase",
            margin: "0 0 10px",
          }}>Inference cost — last 30 days</h3>
          {series.length === 0 ? (
            <div style={{ fontSize: 12, color: "var(--text-faint)", padding: "18px 0" }}>
              No LLM calls in the last 30 days.
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={200}>
              <LineChart data={series} margin={{ top: 4, right: 10, left: -10, bottom: 0 }}>
                <CartesianGrid stroke="rgba(255,255,255,0.05)" vertical={false} />
                <XAxis dataKey="day" stroke="var(--text-faint)" fontSize={10} />
                <YAxis stroke="var(--text-faint)" fontSize={10}
                       tickFormatter={(v) => `$${v.toFixed(v < 1 ? 3 : 2)}`} />
                <Tooltip
                  contentStyle={{
                    background: "var(--panel)", border: "1px solid var(--border)",
                    fontSize: 11, borderRadius: 6,
                  }}
                  formatter={(v, name) => name === "cost" ? [`$${Number(v).toFixed(4)}`, "Cost"] : [v, name]}
                />
                <Line type="monotone" dataKey="cost" stroke="#5eb3f5"
                      strokeWidth={2} dot={{ r: 2 }} />
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>

        <div data-testid="bi-model-chart" style={{
          padding: "16px 18px", background: "var(--panel)",
          border: "1px solid var(--border)", borderRadius: 8,
        }}>
          <h3 style={{
            fontSize: 11, color: "var(--text-dim)",
            letterSpacing: ".08em", textTransform: "uppercase",
            margin: "0 0 10px",
          }}>Cost by model (30d)</h3>
          {byModel.length === 0 ? (
            <div style={{ fontSize: 12, color: "var(--text-faint)", padding: "18px 0" }}>
              No LLM calls yet.
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={byModel} margin={{ top: 4, right: 10, left: -10, bottom: 0 }}>
                <CartesianGrid stroke="rgba(255,255,255,0.05)" vertical={false} />
                <XAxis dataKey="model" stroke="var(--text-faint)" fontSize={9}
                       interval={0} angle={-20} textAnchor="end" height={50} />
                <YAxis stroke="var(--text-faint)" fontSize={10}
                       tickFormatter={(v) => `$${v.toFixed(v < 1 ? 3 : 2)}`} />
                <Tooltip
                  contentStyle={{
                    background: "var(--panel)", border: "1px solid var(--border)",
                    fontSize: 11, borderRadius: 6,
                  }}
                  formatter={(v) => [`$${Number(v).toFixed(4)}`, "Cost"]}
                />
                <Bar dataKey="cost">
                  {byModel.map((_, idx) => (
                    <Cell key={idx} fill={modelColors[idx % modelColors.length]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>
    </section>
  );
}
