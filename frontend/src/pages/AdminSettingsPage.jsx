/**
 * pages/AdminSettingsPage.jsx — Admin "Settings" tab.
 *
 * 2026-08-27 · Admin Compact M6 — extracted verbatim from Admin.jsx's
 * inline SettingsPage() plus its 4 private config sub-cards
 * (StripeApiKeyCard, StripePriceIdsCard, GitHubAppConfigCard,
 * ThinkingHintsConfigCard) — together the single largest chunk in the
 * admin panel (~1000 lines) — so this tab code-splits into its own
 * chunk instead of always shipping in the main bundle. Behavior is
 * unchanged; only the module boundary moved.
 */
import React, { useState, useEffect } from "react";
import { Brain } from "lucide-react";
import { api } from "../lib/api";
import { toast } from "../components/Toast";
import AuremAdminPanel from "../components/AuremAdminPanel";
import AdminThinkingHints from "../components/AdminThinkingHints";
import TwoFactorCard from "../components/TwoFactorCard";
import { Card, Badge } from "./Admin";

export default function AdminSettingsPage() {
  const [s, setS] = useState(null);
  const [busy, setBusy] = useState(false);
  const [upgrading, setUpgrading] = useState(null);  // tier id while in flight
  useEffect(() => {
    api.get("/admin/settings").then((r) => setS(r.data)).catch(() => {});
    // After Stripe redirect, poll status once
    const params = new URLSearchParams(window.location.search);
    const sid = params.get("session_id");
    if (sid) {
      api.get(`/payments/status/${sid}`).then((r) => {
        if (r.data.payment_status === "paid") {
          toast({ message: `Upgraded to ${r.data.tier} ✓`, kind: "success" });
        } else {
          toast({ message: `Payment ${r.data.payment_status}`, kind: "info" });
        }
        window.history.replaceState({}, "", "/admin");
      }).catch(() => {});
    }
  }, []);

  function upgrade(tier) {
    setUpgrading(tier);
    api.post("/payments/checkout", {
      tier,
      origin_url: window.location.origin,
    })
      .then((r) => { window.location.href = r.data.url; })
      .catch((e) => {
        toast({ message: e?.response?.data?.detail || "Could not start checkout", kind: "error" });
        setUpgrading(null);
      });
  }

  if (!s) return <div style={{ padding: 24, color: "var(--text-faint)" }}>Loading…</div>;

  async function save() {
    setBusy(true);
    try {
      await api.post("/admin/settings", s);
      toast({ message: "Settings saved", kind: "success" });
    } catch (e) {
      toast({ message: e?.response?.data?.detail || "Save failed", kind: "error" });
    } finally { setBusy(false); }
  }

  return (
    <div style={{ padding: 24, maxWidth: 960 }}>
      <h3 style={{ fontSize: 13, margin: "0 0 14px" }}>Upgrade your plan</h3>

      {/* Monthly (4 tiers: Free is display-only, Starter/Pro/Team are clickable) */}
      <div style={{ fontSize: 11, color: "var(--text-faint)",
                     textTransform: "uppercase", letterSpacing: ".08em",
                     margin: "0 0 8px" }}>Monthly</div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)",
                     gap: 10, marginBottom: 18 }}>
        {[
          { id: "free",    label: "Free",    price: "$0/mo",  clickable: false },
          { id: "starter", label: "Starter", price: "$9/mo",  clickable: true  },
          { id: "pro",     label: "Pro",     price: "$19/mo", clickable: true  },
          { id: "team",    label: "Team",    price: "$49/mo", clickable: true  },
        ].map((p) => (
          <Card key={p.id} style={{ padding: 14 }}>
            <div style={{ fontSize: 14, fontWeight: 600 }}>{p.label}</div>
            <div style={{ fontSize: 11, color: "var(--text-faint)",
                          marginBottom: 10 }}>{p.price}</div>
            {p.clickable ? (
              <button
                data-testid={`upgrade-${p.id}`}
                onClick={() => upgrade(p.id)}
                disabled={upgrading === p.id}
                className="btn-primary"
                style={{ width: "100%" }}>
                {upgrading === p.id ? "redirecting…" : `Upgrade → ${p.label}`}
              </button>
            ) : (
              <button
                disabled
                className="btn-primary"
                style={{ width: "100%", opacity: 0.4, cursor: "not-allowed" }}>
                Current baseline
              </button>
            )}
          </Card>
        ))}
      </div>

      {/* Annual (3 tiers, 20% discount) */}
      <div style={{ fontSize: 11, color: "var(--text-faint)",
                     textTransform: "uppercase", letterSpacing: ".08em",
                     margin: "0 0 8px" }}>Annual · 20% off</div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)",
                     gap: 10, marginBottom: 20 }}>
        {[
          { id: "starter_annual", label: "Starter", price: "$86.40/yr"  },
          { id: "pro_annual",     label: "Pro",     price: "$182.40/yr" },
          { id: "team_annual",    label: "Team",    price: "$470.40/yr" },
        ].map((p) => (
          <Card key={p.id} style={{ padding: 14 }}>
            <div style={{ fontSize: 14, fontWeight: 600 }}>{p.label}</div>
            <div style={{ fontSize: 11, color: "var(--text-faint)",
                          marginBottom: 10 }}>{p.price}</div>
            <button
              data-testid={`upgrade-${p.id}`}
              onClick={() => upgrade(p.id)}
              disabled={upgrading === p.id}
              className="btn-primary"
              style={{ width: "100%" }}>
              {upgrading === p.id ? "redirecting…" : `Upgrade → ${p.label} annual`}
            </button>
          </Card>
        ))}
      </div>

      <h3 style={{ fontSize: 13, margin: "20px 0 14px" }}>Token limits per plan</h3>
      {["free", "starter", "pro", "team"].map((plan) => (
        <div key={plan} style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 10 }}>
          <span style={{ width: 80, textTransform: "capitalize", fontSize: 12 }}>{plan}</span>
          <input
            data-testid={`admin-limit-${plan}`}
            type="number" className="input"
            value={s.token_limits?.[plan] || 0}
            onChange={(e) => setS({
              ...s, token_limits: { ...s.token_limits, [plan]: +e.target.value }
            })} />
        </div>
      ))}
      <h3 style={{ fontSize: 13, margin: "20px 0 14px" }}>Pricing ($/mo)</h3>
      {["free", "starter", "pro", "team"].map((plan) => (
        <div key={plan} style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 10 }}>
          <span style={{ width: 80, textTransform: "capitalize", fontSize: 12 }}>{plan}</span>
          <input
            data-testid={`admin-price-${plan}`}
            type="number" className="input"
            value={s.pricing?.[plan] || 0}
            onChange={(e) => setS({
              ...s, pricing: { ...s.pricing, [plan]: +e.target.value }
            })} />
        </div>
      ))}
      <button
        data-testid="admin-settings-save"
        onClick={save} disabled={busy}
        className="btn-primary" style={{ marginTop: 14 }}>
        {busy ? "Saving…" : "Save settings"}
      </button>

      {/* Iter 212m-20 — Admin 2FA enrollment card. Place this BEFORE
          the Stripe card so a brand-new admin is nudged toward the
          security best practice first. */}
      <TwoFactorCard />

      {/* Iter 191 — Stripe API key card with edit/save + live ping
          (green/red status light, account info, error reason). */}
      <StripeApiKeyCard />
      <StripePriceIdsCard />
      <GitHubAppConfigCard />

      {/* Iter 158 — thinking-hint manager (tier-aware upsell pills
          shown next to the chat spinner). Full CRUD + global toggle
          + delay slider. */}
      <ThinkingHintsConfigCard />
      <AdminThinkingHints />

      {/* Iter 195 — ORA Council moved into Settings (was its own
          sidebar tab). Council settings live alongside other admin
          tunables (Stripe key, thinking hints) so configuration
          surfaces are in one place. */}
      <div style={{ marginTop: 28, paddingTop: 20,
                     borderTop: "1px solid var(--line, rgba(255,255,255,0.06))" }}>
        <h3 style={{ fontSize: 13, margin: "0 0 14px",
                      display: "flex", alignItems: "center", gap: 8 }}>
          <Brain size={14} style={{ color: "var(--accent, #ff8a2a)" }} />
          ORA Council
        </h3>
        <AuremAdminPanel />
      </div>
    </div>
  );
}

// ─── Iter 191 — Stripe API key card ──────────────────────────────────
// Live status indicator (green = key verified via Account.retrieve,
// red = the exact reason returned by Stripe). Edit/Save flow validates
// the new key BEFORE persisting so a broken key can never overwrite a
// working one.
function StripeApiKeyCard() {
  const [data, setData] = useState(null);
  const [editing, setEditing] = useState(false);
  const [newKey, setNewKey] = useState("");
  const [saving, setSaving] = useState(false);

  async function refresh() {
    try {
      const r = await api.get("/admin/stripe-config");
      setData(r.data);
    } catch (e) {
      setData({ configured: false, status: "error",
                error: e?.response?.data?.detail || "Could not load Stripe config" });
    }
  }

  useEffect(() => { refresh(); }, []);

  async function save() {
    if (!newKey.trim()) return;
    setSaving(true);
    try {
      await api.post("/admin/stripe-config", { api_key: newKey.trim() });
      toast({ message: "Stripe key validated & saved ✓", kind: "success" });
      setNewKey("");
      setEditing(false);
      await refresh();
    } catch (e) {
      toast({
        message: e?.response?.data?.detail || "Validation failed",
        kind: "error",
      });
    } finally { setSaving(false); }
  }

  if (!data) {
    return (
      <Card style={{ padding: 18, marginTop: 24 }}>
        <div style={{ color: "var(--text-faint)", fontSize: 12 }}>
          Loading Stripe status…
        </div>
      </Card>
    );
  }

  const ok = data.status === "ok";
  const dot = ok ? "#22c55e" : "#ef4444";
  const dotShadow = ok ? "rgba(34,197,94,0.35)" : "rgba(239,68,68,0.35)";
  const acct = data.account || {};

  return (
    <Card style={{ padding: 18, marginTop: 24 }} data-testid="admin-stripe-card">
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
                    marginBottom: 12, gap: 10 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span
            data-testid="admin-stripe-status-dot"
            style={{
              width: 12, height: 12, borderRadius: "50%",
              background: dot,
              boxShadow: `0 0 0 4px ${dotShadow}, 0 0 12px ${dot}`,
              animation: ok ? "pulseDot 2.4s ease-in-out infinite" : "none",
              flexShrink: 0,
            }}
          />
          <h3 style={{ fontSize: 13, margin: 0 }}>Stripe API key</h3>
          {data.mode && data.mode !== "unknown" && (
            <Badge color={data.mode === "live" ? "var(--ok)" : "var(--warn)"}>
              {data.mode}
            </Badge>
          )}
          {data.source && (
            <span style={{ fontSize: 10, color: "var(--text-faint)",
                            fontFamily: "'JetBrains Mono', monospace",
                            textTransform: "uppercase", letterSpacing: "0.06em" }}>
              source: {data.source}
            </span>
          )}
        </div>
        {!editing && (
          <button
            data-testid="admin-stripe-edit"
            className="btn-secondary"
            style={{ fontSize: 12, padding: "6px 14px" }}
            onClick={() => { setNewKey(""); setEditing(true); }}
          >
            Edit
          </button>
        )}
      </div>

      {!editing && (
        <>
          {ok ? (
            <div data-testid="admin-stripe-ok"
                 style={{
                   padding: "10px 12px", marginBottom: 8,
                   background: "rgba(34,197,94,0.06)",
                   border: "1px solid rgba(34,197,94,0.18)",
                   borderRadius: 8,
                   fontSize: 12, color: "#86efac",
                   fontFamily: "'JetBrains Mono', monospace",
                 }}>
              ● Connected — sk_{data.mode}_…{data.last4}
            </div>
          ) : (
            <div data-testid="admin-stripe-err"
                 style={{
                   padding: "10px 12px", marginBottom: 8,
                   background: "rgba(239,68,68,0.06)",
                   border: "1px solid rgba(239,68,68,0.2)",
                   borderRadius: 8,
                   fontSize: 12, color: "#fca5a5",
                   lineHeight: 1.5,
                 }}>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>● Not working</div>
              <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>
                {data.error || "Unknown error"}
              </div>
            </div>
          )}

          {ok && (
            <div style={{ display: "grid", gridTemplateColumns: "120px 1fr",
                          gap: "6px 14px", fontSize: 11,
                          color: "var(--text-faint)",
                          fontFamily: "'JetBrains Mono', monospace",
                          marginTop: 4 }}>
              <span>Account</span><span style={{ color: "var(--text)" }}>{acct.id || "—"}</span>
              <span>Business</span><span style={{ color: "var(--text)" }}>{acct.business_name || "—"}</span>
              <span>Email</span><span style={{ color: "var(--text)" }}>{acct.email || "—"}</span>
              <span>Country</span><span style={{ color: "var(--text)" }}>{acct.country || "—"}</span>
              <span>Charges</span>
              <span style={{ color: acct.charges_enabled ? "var(--ok)" : "var(--danger)" }}>
                {acct.charges_enabled ? "enabled" : "disabled"}
              </span>
              <span>Payouts</span>
              <span style={{ color: acct.payouts_enabled ? "var(--ok)" : "var(--danger)" }}>
                {acct.payouts_enabled ? "enabled" : "disabled"}
              </span>
            </div>
          )}
        </>
      )}

      {editing && (
        <div style={{ marginTop: 8 }}>
          <label style={{ fontSize: 11, color: "var(--text-faint)",
                          display: "block", marginBottom: 6,
                          fontFamily: "'JetBrains Mono', monospace",
                          textTransform: "uppercase", letterSpacing: "0.08em" }}>
            Paste new key (sk_live_… or sk_test_…)
          </label>
          <input
            data-testid="admin-stripe-key-input"
            className="input"
            type="password"
            value={newKey}
            onChange={(e) => setNewKey(e.target.value)}
            placeholder="sk_live_……"
            autoFocus
            style={{ width: "100%", fontFamily: "'JetBrains Mono', monospace",
                     fontSize: 12 }}
          />
          <div style={{ marginTop: 10, display: "flex", gap: 8 }}>
            <button
              data-testid="admin-stripe-save"
              className="btn-primary"
              onClick={save}
              disabled={saving || !newKey.trim()}
              style={{ fontSize: 12 }}>
              {saving ? "Validating with Stripe…" : "Save"}
            </button>
            <button
              data-testid="admin-stripe-cancel"
              className="btn-secondary"
              onClick={() => { setEditing(false); setNewKey(""); }}
              disabled={saving}
              style={{ fontSize: 12 }}>
              Cancel
            </button>
          </div>
          <div style={{ marginTop: 10, fontSize: 10, color: "var(--text-faint)",
                        lineHeight: 1.5 }}>
            Key is validated via a live <code>Account.retrieve()</code> call
            before saving. If Stripe rejects it, nothing is persisted and the
            old key keeps working.
          </div>
        </div>
      )}

      <style>{`
        @keyframes pulseDot {
          0%, 100% { box-shadow: 0 0 0 4px ${dotShadow}, 0 0 12px ${dot}; }
          50%      { box-shadow: 0 0 0 8px ${dotShadow}, 0 0 18px ${dot}; }
        }
      `}</style>
    </Card>
  );
}

// ─── Session-fork · 2026-02-09 — Stripe Price IDs card ────────────────
// Multi-worker split-brain fix. All 6 price IDs live in Mongo
// (admin_settings._id="stripe_price_ids") and are hydrated into every
// worker at boot. This UI is the ONLY correct place to rotate prices —
// env-panel edits require a full pod recycle and race between workers.
const PRICE_PLAN_LABELS = [
  { id: "starter",        label: "Starter",         interval: "month" },
  { id: "pro",            label: "Pro",             interval: "month" },
  { id: "team",           label: "Team",            interval: "month" },
  { id: "starter_annual", label: "Starter Annual",  interval: "year"  },
  { id: "pro_annual",     label: "Pro Annual",      interval: "year"  },
  { id: "team_annual",    label: "Team Annual",     interval: "year"  },
];

function StripePriceIdsCard() {
  const [data, setData] = useState(null);
  const [editing, setEditing] = useState(false);
  const [inputs, setInputs] = useState({});
  const [saving, setSaving] = useState(false);

  async function refresh() {
    try {
      const r = await api.get("/admin/stripe-prices");
      setData(r.data);
    } catch (e) {
      setData({ plans: {},
                error: e?.response?.data?.detail || "Could not load Stripe prices" });
    }
  }
  useEffect(() => { refresh(); }, []);

  function startEdit() {
    // Pre-populate empty so a paste replaces cleanly.
    setInputs(Object.fromEntries(PRICE_PLAN_LABELS.map(p => [p.id, ""])));
    setEditing(true);
  }

  async function save() {
    const trimmed = Object.fromEntries(
      Object.entries(inputs).map(([k, v]) => [k, (v || "").trim()])
    );
    const anyProvided = Object.values(trimmed).some(v => v);
    if (!anyProvided) {
      toast({ message: "Paste at least one price ID first", kind: "warning" });
      return;
    }
    setSaving(true);
    try {
      const r = await api.post("/admin/stripe-prices", trimmed);
      toast({ message: `Saved ${r.data.saved} price ID(s) ✓`, kind: "success" });
      setEditing(false);
      setInputs({});
      await refresh();
    } catch (e) {
      toast({
        message: e?.response?.data?.detail || "Save failed",
        kind: "error",
      });
    } finally { setSaving(false); }
  }

  if (!data) {
    return (
      <Card style={{ padding: 18, marginTop: 24 }}>
        <div style={{ color: "var(--text-faint)", fontSize: 12 }}>
          Loading Stripe price IDs…
        </div>
      </Card>
    );
  }

  const plans = data.plans || {};
  const anyDbOverride = Object.values(plans).some(p => p.source === "db_override");
  const anyBroken = Object.values(plans).some(p => !p.valid);

  return (
    <div data-testid="admin-stripe-prices-card">
    <Card style={{ padding: 18, marginTop: 16 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
                    marginBottom: 12, gap: 10 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{
            width: 12, height: 12, borderRadius: "50%",
            background: anyBroken ? "#ef4444" : "#22c55e",
            boxShadow: anyBroken
              ? "0 0 0 4px rgba(239,68,68,0.35), 0 0 12px #ef4444"
              : "0 0 0 4px rgba(34,197,94,0.35), 0 0 12px #22c55e",
            flexShrink: 0,
          }} />
          <h3 style={{ fontSize: 13, margin: 0 }}>Stripe Price IDs</h3>
          <Badge color={anyDbOverride ? "var(--ok)" : "var(--warn)"}>
            {anyDbOverride ? "db override" : "env fallback"}
          </Badge>
        </div>
        {!editing && (
          <button
            data-testid="admin-stripe-prices-edit"
            className="btn-secondary"
            style={{ fontSize: 12, padding: "6px 14px" }}
            onClick={startEdit}>
            Edit
          </button>
        )}
      </div>

      {!editing && (
        <>
          <div style={{ padding: "8px 12px", marginBottom: 10,
                        background: "rgba(255,138,42,0.06)",
                        border: "1px solid rgba(255,138,42,0.18)",
                        borderRadius: 8, fontSize: 11,
                        color: "var(--text-faint)", lineHeight: 1.5 }}>
            Store all 6 price IDs here (in Mongo) instead of env vars. This
            eliminates the multi-worker split-brain where different uvicorn
            workers could serve different price IDs after an env-panel edit.
            Values are hot-swapped into every worker on Save + hydrated at boot.
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "160px 100px 90px 1fr",
                        gap: "6px 14px", fontSize: 11,
                        fontFamily: "'JetBrains Mono', monospace" }}>
            <span style={{ color: "var(--text-faint)" }}>PLAN</span>
            <span style={{ color: "var(--text-faint)" }}>SOURCE</span>
            <span style={{ color: "var(--text-faint)" }}>STATUS</span>
            <span style={{ color: "var(--text-faint)" }}>DETAIL</span>
            {PRICE_PLAN_LABELS.map(({ id, label, interval }) => {
              const info = plans[id] || {};
              const src = info.source || "none";
              const valid = info.valid;
              return (
                <React.Fragment key={id}>
                  <span style={{ color: "var(--text)" }}
                        data-testid={`admin-price-plan-${id}`}>
                    {label} <span style={{ color: "var(--text-faint)" }}>· {interval}</span>
                  </span>
                  <span style={{
                    color: src === "db_override" ? "#86efac"
                         : src === "env"         ? "#fbbf24"
                         : "#fca5a5",
                    fontSize: 10, textTransform: "uppercase" }}>
                    {src}
                  </span>
                  <span data-testid={`admin-price-valid-${id}`}
                        style={{ color: valid ? "#86efac" : "#fca5a5" }}>
                    {valid ? "● valid" : "● broken"}
                  </span>
                  <span style={{ color: "var(--text-faint)" }}>
                    {info.last6 ? `…${info.last6}` : "—"}
                    {info.interval && info.interval !== interval && (
                      <span style={{ color: "#fca5a5", marginLeft: 6 }}>
                        (interval={info.interval}, expected {interval})
                      </span>
                    )}
                    {info.error && (
                      <span style={{ color: "#fca5a5", marginLeft: 6 }}>
                        {info.error}
                      </span>
                    )}
                  </span>
                </React.Fragment>
              );
            })}
          </div>
          {data.updated_by && (
            <div style={{ marginTop: 10, fontSize: 10, color: "var(--text-faint)",
                          fontFamily: "'JetBrains Mono', monospace" }}>
              Last updated by {data.updated_by} at{" "}
              {new Date((data.last_updated || 0) * 1000).toISOString()}
            </div>
          )}
        </>
      )}

      {editing && (
        <div style={{ marginTop: 8 }}>
          <div style={{ padding: "8px 12px", marginBottom: 12,
                        background: "rgba(59,130,246,0.06)",
                        border: "1px solid rgba(59,130,246,0.2)",
                        borderRadius: 8, fontSize: 11,
                        color: "#93c5fd", lineHeight: 1.5 }}>
            Paste all 6 LIVE-mode price IDs from Stripe Dashboard → Products.
            Each is validated (recurring + correct interval) before persistence.
            Empty fields fall back to the corresponding STRIPE_*_PRICE_ID env var.
          </div>
          {PRICE_PLAN_LABELS.map(({ id, label, interval }) => (
            <div key={id} style={{ display: "flex", alignItems: "center",
                                    gap: 10, marginBottom: 8 }}>
              <label style={{ width: 160, fontSize: 11,
                              color: "var(--text-faint)",
                              fontFamily: "'JetBrains Mono', monospace",
                              textTransform: "uppercase",
                              letterSpacing: "0.06em" }}>
                {label} · {interval}
              </label>
              <input
                data-testid={`admin-price-input-${id}`}
                className="input"
                value={inputs[id] || ""}
                onChange={(e) => setInputs({ ...inputs, [id]: e.target.value })}
                placeholder={`price_… (${interval}ly)`}
                style={{ flex: 1, fontFamily: "'JetBrains Mono', monospace",
                         fontSize: 12 }} />
            </div>
          ))}
          <div style={{ marginTop: 12, display: "flex", gap: 8 }}>
            <button
              data-testid="admin-stripe-prices-save"
              className="btn-primary"
              onClick={save}
              disabled={saving}
              style={{ fontSize: 12 }}>
              {saving ? "Validating each with Stripe…" : "Save & Hot-swap"}
            </button>
            <button
              data-testid="admin-stripe-prices-cancel"
              className="btn-secondary"
              onClick={() => { setEditing(false); setInputs({}); }}
              disabled={saving}
              style={{ fontSize: 12 }}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </Card>
    </div>
  );
}

// ─── 2026-02-10 — GitHub App credential card ─────────────────────────
// Mirrors the Stripe cards: presence-only summary (never echoes secrets),
// live probe pill, edit modal with 4 paste fields, validates against
// GitHub before persisting. Used to bootstrap the Phase 1.1 service and
// wizard integration that come next.
function GitHubAppConfigCard() {
  const [data, setData] = useState(null);
  const [editing, setEditing] = useState(false);
  const [inputs, setInputs] = useState({
    app_id: "", app_slug: "", private_key: "", webhook_secret: "",
  });
  const [saving, setSaving] = useState(false);

  async function refresh() {
    try {
      const r = await api.get("/admin/github-app-config");
      setData(r.data);
    } catch (e) {
      setData({
        configured: false,
        error: e?.response?.data?.detail || "Could not load GitHub App config",
      });
    }
  }
  useEffect(() => { refresh(); }, []);

  function startEdit() {
    setInputs({
      app_id: data?.app_id || "",
      app_slug: data?.app_slug || "",
      private_key: "",       // never pre-fill secrets
      webhook_secret: "",    // never pre-fill secrets
    });
    setEditing(true);
  }

  async function save() {
    const trimmed = {
      app_id:         (inputs.app_id || "").trim(),
      app_slug:       (inputs.app_slug || "").trim().toLowerCase(),
      private_key:    (inputs.private_key || "").trim(),
      webhook_secret: (inputs.webhook_secret || "").trim(),
    };
    if (!trimmed.app_id || !trimmed.app_slug
        || !trimmed.private_key || !trimmed.webhook_secret) {
      toast({
        message: "All four fields are required — partial configs are refused.",
        kind: "warning",
      });
      return;
    }
    setSaving(true);
    try {
      const r = await api.post("/admin/github-app-config", trimmed);
      toast({
        message: `GitHub App @${r.data.app_slug} validated & saved ✓`,
        kind: "success",
      });
      setEditing(false);
      setInputs({ app_id: "", app_slug: "", private_key: "", webhook_secret: "" });
      await refresh();
    } catch (e) {
      const d = e?.response?.data?.detail;
      const msg = typeof d === "string"
        ? d
        : (d?.message || "Save failed");
      toast({ message: msg, kind: "error" });
    } finally { setSaving(false); }
  }

  if (!data) {
    return (
      <Card style={{ padding: 18, marginTop: 24 }}>
        <div style={{ color: "var(--text-faint)", fontSize: 12 }}>
          Loading GitHub App config…
        </div>
      </Card>
    );
  }

  const live = data.live || {};
  const configured = !!data.configured;
  const healthy = configured && live.ok;

  return (
    <div data-testid="admin-github-app-config-card">
    <Card style={{ padding: 18, marginTop: 16 }}>
      <div style={{ display: "flex", alignItems: "center",
                    justifyContent: "space-between",
                    marginBottom: 12, gap: 10 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{
            width: 12, height: 12, borderRadius: "50%",
            background: !configured ? "#71717a"
                      : healthy      ? "#22c55e"
                      : "#ef4444",
            boxShadow: !configured
              ? "0 0 0 4px rgba(113,113,122,0.25)"
              : healthy
                ? "0 0 0 4px rgba(34,197,94,0.35), 0 0 12px #22c55e"
                : "0 0 0 4px rgba(239,68,68,0.35), 0 0 12px #ef4444",
            flexShrink: 0,
          }} />
          <h3 style={{ fontSize: 13, margin: 0 }}>GitHub App</h3>
          <Badge color={configured ? (healthy ? "var(--ok)" : "var(--danger)") : "var(--warn)"}>
            {!configured ? "not configured"
              : healthy   ? "connected"
              : "invalid"}
          </Badge>
        </div>
        {!editing && (
          <button
            data-testid="admin-github-app-edit"
            className="btn-secondary"
            style={{ fontSize: 12, padding: "6px 14px" }}
            onClick={startEdit}>
            {configured ? "Rotate credentials" : "Paste credentials"}
          </button>
        )}
      </div>

      {!editing && (
        <>
          <div style={{ padding: "8px 12px", marginBottom: 10,
                        background: "rgba(255,138,42,0.06)",
                        border: "1px solid rgba(255,138,42,0.18)",
                        borderRadius: 8, fontSize: 11,
                        color: "var(--text-faint)", lineHeight: 1.5 }}>
            Credentials for the <strong>Aurem GitHub App</strong> (installable
            App, not the OAuth App). Stored in Mongo — every uvicorn worker
            hydrates on boot, POST hot-swaps immediately. The private key is
            never echoed back after paste; only a last-6 fingerprint is shown.
            Once configured, the PAT-vs-App gate in{" "}
            <code style={{ margin: "0 4px", fontSize: 10 }}>
              cto_projects.py::/projects/add
            </code>
            will accept either.
          </div>

          <div style={{ display: "grid",
                        gridTemplateColumns: "160px 1fr",
                        gap: "6px 14px", fontSize: 11,
                        fontFamily: "'JetBrains Mono', monospace" }}>
            <span style={{ color: "var(--text-faint)" }}>APP ID</span>
            <span data-testid="gh-app-appid">
              {data.app_id || <em style={{ color: "var(--text-faint)" }}>—</em>}
            </span>

            <span style={{ color: "var(--text-faint)" }}>SLUG</span>
            <span data-testid="gh-app-slug">
              {data.app_slug
                ? <>
                    {data.app_slug}
                    {data.install_url && (
                      <a href={data.install_url}
                         target="_blank" rel="noopener noreferrer"
                         style={{ marginLeft: 10, color: "var(--accent)",
                                  fontSize: 10 }}>
                        install URL ↗
                      </a>
                    )}
                  </>
                : <em style={{ color: "var(--text-faint)" }}>—</em>}
            </span>

            <span style={{ color: "var(--text-faint)" }}>PRIVATE KEY</span>
            <span style={{ color: data.private_key_last6 ? "var(--text)"
                                                          : "var(--text-faint)" }}>
              {data.private_key_last6
                ? `…${data.private_key_last6} (fingerprint)`
                : "—"}
            </span>

            <span style={{ color: "var(--text-faint)" }}>WEBHOOK SECRET</span>
            <span style={{ color: data.webhook_secret_last4 ? "var(--text)"
                                                             : "var(--text-faint)" }}>
              {data.webhook_secret_last4
                ? `…${data.webhook_secret_last4}`
                : "—"}
            </span>

            <span style={{ color: "var(--text-faint)" }}>LIVE PROBE</span>
            <span data-testid="gh-app-live" style={{
              color: live.ok ? "#86efac" : "#fca5a5",
            }}>
              {live.ok
                ? <>● GitHub returned 200 · App name: <strong>{live.app_name}</strong>
                    {live.owner_login && <> · owner: {live.owner_login} ({live.owner_type})</>}</>
                : <>● {live.error || "not tested"}</>}
            </span>

            {live.ok && live.permissions && (
              <>
                <span style={{ color: "var(--text-faint)" }}>PERMISSIONS</span>
                <span style={{ color: "var(--text-faint)", fontSize: 10 }}>
                  {Object.entries(live.permissions)
                    .map(([k, v]) => `${k}:${v}`).join(" · ") || "—"}
                </span>
                <span style={{ color: "var(--text-faint)" }}>EVENTS</span>
                <span style={{ color: "var(--text-faint)", fontSize: 10 }}>
                  {(live.events || []).join(", ") || "—"}
                </span>
              </>
            )}
          </div>

          {data.updated_by && (
            <div style={{ marginTop: 10, fontSize: 10, color: "var(--text-faint)",
                          fontFamily: "'JetBrains Mono', monospace" }}>
              Last updated by {data.updated_by} at{" "}
              {new Date((data.last_updated || 0) * 1000).toISOString()}
            </div>
          )}
        </>
      )}

      {editing && (
        <div style={{ marginTop: 8 }}>
          <div style={{ padding: "8px 12px", marginBottom: 12,
                        background: "rgba(59,130,246,0.06)",
                        border: "1px solid rgba(59,130,246,0.2)",
                        borderRadius: 8, fontSize: 11,
                        color: "#93c5fd", lineHeight: 1.5 }}>
            Paste all 4 credentials from your GitHub App settings page
            (<code style={{ fontSize: 10 }}>
              github.com/organizations/&lt;org&gt;/settings/apps/&lt;slug&gt;
            </code>).
            The private key + App ID are validated against GitHub
            (<code style={{ fontSize: 10 }}>GET /app</code>) before anything
            is written. Partial configs are refused.
          </div>

          <div style={{ display: "flex", alignItems: "center",
                        gap: 10, marginBottom: 10 }}>
            <label style={{ width: 160, fontSize: 11,
                            color: "var(--text-faint)",
                            fontFamily: "'JetBrains Mono', monospace",
                            textTransform: "uppercase",
                            letterSpacing: "0.06em" }}>
              App ID
            </label>
            <input
              data-testid="admin-gh-app-input-appid"
              className="input"
              inputMode="numeric"
              value={inputs.app_id}
              onChange={(e) => setInputs({ ...inputs, app_id: e.target.value })}
              placeholder="e.g. 12345678 (numeric, from App settings header)"
              style={{ flex: 1, fontFamily: "'JetBrains Mono', monospace",
                       fontSize: 12 }} />
          </div>

          <div style={{ display: "flex", alignItems: "center",
                        gap: 10, marginBottom: 10 }}>
            <label style={{ width: 160, fontSize: 11,
                            color: "var(--text-faint)",
                            fontFamily: "'JetBrains Mono', monospace",
                            textTransform: "uppercase",
                            letterSpacing: "0.06em" }}>
              App slug
            </label>
            <input
              data-testid="admin-gh-app-input-slug"
              className="input"
              value={inputs.app_slug}
              onChange={(e) => setInputs({ ...inputs, app_slug: e.target.value })}
              placeholder="aurem-devops (lowercase, from github.com/apps/<slug>)"
              style={{ flex: 1, fontFamily: "'JetBrains Mono', monospace",
                       fontSize: 12 }} />
          </div>

          <div style={{ display: "flex", alignItems: "flex-start",
                        gap: 10, marginBottom: 10 }}>
            <label style={{ width: 160, fontSize: 11,
                            color: "var(--text-faint)",
                            fontFamily: "'JetBrains Mono', monospace",
                            textTransform: "uppercase",
                            letterSpacing: "0.06em",
                            paddingTop: 8 }}>
              Private key (PEM)
            </label>
            <textarea
              data-testid="admin-gh-app-input-pem"
              className="input"
              value={inputs.private_key}
              onChange={(e) => setInputs({ ...inputs, private_key: e.target.value })}
              placeholder={"-----BEGIN RSA PRIVATE KEY-----\n... paste full PEM ...\n-----END RSA PRIVATE KEY-----"}
              rows={8}
              style={{ flex: 1, fontFamily: "'JetBrains Mono', monospace",
                       fontSize: 11, resize: "vertical", minHeight: 140,
                       whiteSpace: "pre" }} />
          </div>

          <div style={{ display: "flex", alignItems: "center",
                        gap: 10, marginBottom: 12 }}>
            <label style={{ width: 160, fontSize: 11,
                            color: "var(--text-faint)",
                            fontFamily: "'JetBrains Mono', monospace",
                            textTransform: "uppercase",
                            letterSpacing: "0.06em" }}>
              Webhook secret
            </label>
            <input
              data-testid="admin-gh-app-input-webhook"
              className="input"
              type="password"
              autoComplete="off"
              value={inputs.webhook_secret}
              onChange={(e) => setInputs({ ...inputs, webhook_secret: e.target.value })}
              placeholder="opaque random string (min 8 chars)"
              style={{ flex: 1, fontFamily: "'JetBrains Mono', monospace",
                       fontSize: 12 }} />
          </div>

          <div style={{ marginTop: 12, display: "flex", gap: 8 }}>
            <button
              data-testid="admin-gh-app-save"
              className="btn-primary"
              onClick={save}
              disabled={saving}
              style={{ fontSize: 12 }}>
              {saving ? "Validating against GitHub…" : "Validate & Save"}
            </button>
            <button
              data-testid="admin-gh-app-cancel"
              className="btn-secondary"
              onClick={() => {
                setEditing(false);
                setInputs({ app_id: "", app_slug: "", private_key: "", webhook_secret: "" });
              }}
              disabled={saving}
              style={{ fontSize: 12 }}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </Card>
    </div>
  );
}



function ThinkingHintsConfigCard() {
  const [cfg, setCfg] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.get("/admin/thinking-hints-config")
      .then((r) => setCfg({
        enabled: r.data?.enabled ?? true,
        delay_ms: r.data?.delay_ms ?? 600,
      }))
      .catch(() => setCfg({ enabled: true, delay_ms: 600 }));
  }, []);

  if (!cfg) return null;

  async function save() {
    setSaving(true);
    try {
      await api.post("/admin/thinking-hints-config", cfg);
      toast({ message: "Hint config saved", kind: "success" });
    } catch (e) {
      toast({
        message: e?.response?.data?.detail || "Save failed",
        kind: "error",
      });
    } finally { setSaving(false); }
  }

  return (
    <div
      data-testid="hints-config-card"
      style={{
        marginTop: 28, padding: 14, borderRadius: 10,
        background: "rgba(255,255,255,0.02)",
        border: "1px solid var(--border)",
      }}
    >
      <h3 style={{ fontSize: 13, margin: "0 0 6px" }}>
        💡 Thinking-Hint Global Config
      </h3>
      <p style={{ fontSize: 11, color: "var(--text-faint)", margin: "0 0 14px" }}>
        Master kill-switch + delay tuner. Per-hint copy is managed below.
      </p>
      <label style={{
        display: "flex", alignItems: "center", gap: 10, fontSize: 12,
        marginBottom: 14, cursor: "pointer",
      }}>
        <input
          data-testid="hints-config-enabled"
          type="checkbox"
          checked={!!cfg.enabled}
          onChange={(e) => setCfg({ ...cfg, enabled: e.target.checked })}
        />
        Show thinking hints to users
        <span style={{
          marginLeft: 8, fontSize: 10, letterSpacing: "0.1em",
          padding: "2px 8px", borderRadius: 999,
          background: cfg.enabled
            ? "rgba(110, 231, 183, 0.12)"
            : "rgba(255, 80, 80, 0.10)",
          color: cfg.enabled ? "var(--ok, #6ee7b7)" : "var(--danger, #ef4444)",
          border: `1px solid ${cfg.enabled
            ? "rgba(110, 231, 183, 0.35)"
            : "rgba(255, 80, 80, 0.35)"}`,
        }}>
          {cfg.enabled ? "ENABLED" : "DISABLED"}
        </span>
      </label>
      <div style={{ marginBottom: 10 }}>
        <div style={{
          display: "flex", justifyContent: "space-between",
          fontSize: 11, color: "var(--text-dim)", marginBottom: 4,
        }}>
          <span>Delay before hint appears</span>
          <span data-testid="hints-config-delay-value"
                style={{ fontFamily: "'JetBrains Mono', monospace" }}>
            {cfg.delay_ms} ms
          </span>
        </div>
        <input
          data-testid="hints-config-delay"
          type="range"
          min={200} max={5000} step={100}
          value={cfg.delay_ms}
          onChange={(e) => setCfg({ ...cfg, delay_ms: +e.target.value })}
          style={{ width: "100%" }}
        />
        <div style={{
          display: "flex", justifyContent: "space-between",
          fontSize: 10, color: "var(--text-faint)", marginTop: 2,
        }}>
          <span>200ms (instant)</span><span>5000ms (slow)</span>
        </div>
      </div>
      <button
        data-testid="hints-config-save"
        onClick={save} disabled={saving} className="btn-primary"
        style={{ fontSize: 11 }}
      >
        {saving ? "Saving…" : "Save config"}
      </button>
    </div>
  );
}
