/**
 * AdminMaintenance.jsx — 2026-08.
 * /admin/maintenance — planned-maintenance toggle + outage incident
 * tracker. Standalone page (same pattern as AdminQADashboard) so it
 * doesn't fight the Admin.jsx shell's internal switch statement.
 */
import React, { useEffect, useState, useCallback } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, Wrench, AlertTriangle, Loader2 } from "lucide-react";
import { api } from "../lib/api";
import { toast } from "../components/Toast";

const THRESHOLD_PRESETS = [15, 30, 60];

function Card({ children, style }) {
  return (
    <div style={{ background: "#0f0f0f", border: "1px solid #262626", borderRadius: 12, padding: 20, ...style }}>
      {children}
    </div>
  );
}

function fmtDuration(s) {
  if (s == null) return "—";
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.round(s / 60)}m ${Math.round(s % 60)}s`;
  return `${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m`;
}

function ago(iso) {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (isNaN(t)) return "—";
  const s = Math.floor((Date.now() - t) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export default function AdminMaintenance() {
  const [settings, setSettings] = useState(null);
  const [incidents, setIncidents] = useState([]);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [draft, setDraft] = useState({ message: "", window: "", threshold: 30 });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [s, i] = await Promise.all([
        api.get("/admin/maintenance"),
        api.get("/admin/maintenance/incidents"),
      ]);
      setSettings(s.data);
      setDraft({
        message: s.data.message || "",
        window: s.data.window || "",
        threshold: s.data.outage_threshold_s || 30,
      });
      setIncidents(i.data.incidents || []);
      setStats(i.data.stats || null);
    } catch (e) {
      toast({ message: e?.response?.data?.detail || "Failed to load maintenance state", kind: "error" });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function toggleManual() {
    setSaving(true);
    try {
      const r = await api.post("/admin/maintenance/settings", {
        manual_enabled: !settings.manual_enabled,
        message: draft.message,
        window: draft.window,
      });
      setSettings(r.data);
      toast({
        message: r.data.manual_enabled ? "Maintenance mode ON — visitors now see the maintenance screen" : "Maintenance mode OFF",
        kind: r.data.manual_enabled ? "info" : "success",
      });
    } catch (e) {
      toast({ message: e?.response?.data?.detail || "Failed to toggle", kind: "error" });
    } finally {
      setSaving(false);
    }
  }

  async function saveDetails() {
    setSaving(true);
    try {
      const r = await api.post("/admin/maintenance/settings", {
        message: draft.message,
        window: draft.window,
        outage_threshold_s: draft.threshold,
      });
      setSettings(r.data);
      toast({ message: "Saved", kind: "success" });
    } catch (e) {
      toast({ message: e?.response?.data?.detail || "Save failed", kind: "error" });
    } finally {
      setSaving(false);
    }
  }

  if (loading && !settings) {
    return (
      <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", background: "#0a0a0a", color: "#666" }}>
        <Loader2 size={18} className="spin" />
      </div>
    );
  }

  return (
    <div style={{ minHeight: "100vh", background: "#0a0a0a", color: "#e5e5e5", fontFamily: "system-ui, -apple-system, sans-serif", padding: "28px 24px 60px" }}>
      <div style={{ maxWidth: 900, margin: "0 auto" }}>
        <Link to="/admin/cockpit" data-testid="maintenance-back-link"
              style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "#888", fontSize: 12, textDecoration: "none", marginBottom: 18 }}>
          <ArrowLeft size={12} /> Cockpit
        </Link>

        <h1 style={{ fontSize: 20, fontWeight: 700, display: "flex", alignItems: "center", gap: 10, margin: "0 0 4px" }}>
          <Wrench size={18} color="#ff8a2a" /> System Maintenance
        </h1>
        <p style={{ fontSize: 12.5, color: "#888", margin: "0 0 22px" }}>
          Manual planned-maintenance toggle + automatic outage tracker (deploy restarts &amp; crashes).
        </p>

        {/* Manual toggle */}
        <Card style={{ marginBottom: 18 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
            <div>
              <div style={{ fontSize: 13, fontWeight: 600 }}>Planned maintenance mode</div>
              <div style={{ fontSize: 11.5, color: "#777", marginTop: 3 }}>
                When ON, every visitor (except admins) sees the maintenance screen immediately.
              </div>
            </div>
            <button
              data-testid="maintenance-toggle-btn"
              onClick={toggleManual}
              disabled={saving}
              style={{
                padding: "8px 18px", borderRadius: 999, fontSize: 12, fontWeight: 700,
                border: `1px solid ${settings.manual_enabled ? "#f87171" : "#3ECF8E"}`,
                background: settings.manual_enabled ? "rgba(248,113,113,0.12)" : "rgba(62,207,142,0.10)",
                color: settings.manual_enabled ? "#f87171" : "#3ECF8E",
                cursor: saving ? "wait" : "pointer",
              }}
            >
              {settings.manual_enabled ? "ON — click to turn off" : "OFF — click to turn on"}
            </button>
          </div>

          <label style={{ fontSize: 11, color: "#888", display: "block", marginBottom: 4, textTransform: "uppercase", letterSpacing: "0.06em" }}>
            Message shown to visitors
          </label>
          <textarea
            data-testid="maintenance-message-input"
            value={draft.message}
            onChange={(e) => setDraft((d) => ({ ...d, message: e.target.value }))}
            rows={2}
            placeholder="We're deploying an update. Back in a few minutes."
            style={{ width: "100%", background: "#181818", border: "1px solid #2a2a2a", borderRadius: 8, color: "#e5e5e5", fontSize: 12.5, padding: "8px 10px", marginBottom: 12, resize: "vertical" }}
          />

          <label style={{ fontSize: 11, color: "#888", display: "block", marginBottom: 4, textTransform: "uppercase", letterSpacing: "0.06em" }}>
            Deployment window (free text — for your own reference / visitor note)
          </label>
          <input
            data-testid="maintenance-window-input"
            value={draft.window}
            onChange={(e) => setDraft((d) => ({ ...d, window: e.target.value }))}
            placeholder="e.g. Deploys usually run Sun 2-4am IST"
            style={{ width: "100%", background: "#181818", border: "1px solid #2a2a2a", borderRadius: 8, color: "#e5e5e5", fontSize: 12.5, padding: "8px 10px", marginBottom: 12 }}
          />

          <button
            data-testid="maintenance-save-details-btn"
            onClick={saveDetails}
            disabled={saving}
            style={{ padding: "7px 16px", fontSize: 12, borderRadius: 6, border: "1px solid #333", background: "#181818", color: "#ccc", cursor: saving ? "wait" : "pointer" }}
          >
            {saving ? "Saving…" : "Save message / window"}
          </button>
        </Card>

        {/* Outage detection threshold */}
        <Card style={{ marginBottom: 18 }}>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>Auto-outage detection threshold</div>
          <div style={{ fontSize: 11.5, color: "#777", marginBottom: 12 }}>
            If the backend is unreachable longer than this on boot (deploy restart / crash), it's logged below as an outage.
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            {THRESHOLD_PRESETS.map((p) => (
              <button
                key={p}
                data-testid={`maintenance-threshold-preset-${p}`}
                onClick={() => setDraft((d) => ({ ...d, threshold: p }))}
                style={{
                  padding: "6px 14px", borderRadius: 999, fontSize: 11.5, fontWeight: 600,
                  border: `1px solid ${draft.threshold === p ? "#ff8a2a" : "#2a2a2a"}`,
                  background: draft.threshold === p ? "rgba(255,138,42,0.12)" : "#181818",
                  color: draft.threshold === p ? "#ff8a2a" : "#999", cursor: "pointer",
                }}
              >
                {p}s
              </button>
            ))}
            <input
              data-testid="maintenance-threshold-custom-input"
              type="number"
              min={5}
              max={600}
              value={draft.threshold}
              onChange={(e) => setDraft((d) => ({ ...d, threshold: parseInt(e.target.value, 10) || 30 }))}
              style={{ width: 90, background: "#181818", border: "1px solid #2a2a2a", borderRadius: 8, color: "#e5e5e5", fontSize: 12.5, padding: "6px 10px" }}
            />
            <span style={{ fontSize: 11.5, color: "#666" }}>seconds (custom)</span>
            <button
              data-testid="maintenance-save-threshold-btn"
              onClick={saveDetails}
              disabled={saving}
              style={{ padding: "7px 16px", fontSize: 12, borderRadius: 6, border: "1px solid #333", background: "#181818", color: "#ccc", cursor: saving ? "wait" : "pointer", marginLeft: "auto" }}
            >
              {saving ? "Saving…" : "Save threshold"}
            </button>
          </div>
        </Card>

        {/* Incident tracker */}
        <Card>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
            <div style={{ fontSize: 13, fontWeight: 600, display: "flex", alignItems: "center", gap: 8 }}>
              <AlertTriangle size={14} color="#facc15" /> Outage incidents
            </div>
            <div data-testid="maintenance-incidents-summary" style={{ fontSize: 11.5, color: "#888" }}>
              {stats ? `${stats.count_30d} in last 30d · ${fmtDuration(stats.total_downtime_s_30d)} total` : "—"}
            </div>
          </div>
          <div style={{ marginTop: 14 }}>
            {incidents.length === 0 ? (
              <div style={{ fontSize: 12, color: "#666", padding: "10px 0" }}>No outages logged yet.</div>
            ) : (
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                  <thead>
                    <tr>
                      {["Started", "Duration", "Reason", "Resolved"].map((h) => (
                        <th key={h} style={{ textAlign: "left", padding: "6px 10px", fontSize: 10, color: "#666", textTransform: "uppercase", borderBottom: "1px solid #262626" }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {incidents.map((inc) => (
                      <tr key={inc.incident_id} data-testid={`maintenance-incident-row-${inc.incident_id}`}>
                        <td style={{ padding: "8px 10px", borderBottom: "1px solid #1a1a1a" }} title={inc.started_at}>{ago(inc.started_at)}</td>
                        <td style={{ padding: "8px 10px", borderBottom: "1px solid #1a1a1a", fontFamily: "monospace" }}>{fmtDuration(inc.duration_s)}</td>
                        <td style={{ padding: "8px 10px", borderBottom: "1px solid #1a1a1a", color: "#999" }}>{inc.detail || inc.reason}</td>
                        <td style={{ padding: "8px 10px", borderBottom: "1px solid #1a1a1a" }}>
                          <span style={{ color: inc.resolved ? "#3ECF8E" : "#f87171" }}>{inc.resolved ? "resolved" : "open"}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </Card>
      </div>
    </div>
  );
}
