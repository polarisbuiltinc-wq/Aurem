/**
 * RouteErrorBoundary.jsx — Iter 356b.
 *
 * Wraps the lazy-loaded route tree so a crashed component (failed lazy
 * chunk, transient 429 cascade, render error) NEVER leaves the user on
 * a silent blank page. Shows a minimal retry card instead; retry
 * remounts the tree (and re-attempts the chunk fetch).
 */
import React from "react";

export default class RouteErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error("[route-error-boundary]", error, info?.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div data-testid="route-error-boundary" style={{
        minHeight: "100vh", display: "flex", flexDirection: "column",
        alignItems: "center", justifyContent: "center", gap: 14,
        background: "rgb(7,8,13)", color: "#e2e8f0",
        fontFamily: "'Inter', sans-serif", padding: 24, textAlign: "center",
      }}>
        <div style={{ fontSize: 17, fontWeight: 600 }}>
          Something went wrong loading this page.
        </div>
        <div style={{ fontSize: 13, color: "#94a3b8", maxWidth: 420 }}>
          This is usually a transient network hiccup. Your data is safe.
        </div>
        <div style={{ display: "flex", gap: 10 }}>
          <button data-testid="route-error-retry-btn" type="button"
            onClick={() => this.setState({ error: null })}
            style={{
              padding: "9px 20px", borderRadius: 8, border: "none",
              background: "#ff8a2a", color: "#0b0c10", fontWeight: 600,
              fontSize: 13, cursor: "pointer",
            }}>
            Retry
          </button>
          <button data-testid="route-error-reload-btn" type="button"
            onClick={() => window.location.reload()}
            style={{
              padding: "9px 20px", borderRadius: 8,
              border: "1px solid rgba(255,200,120,.25)",
              background: "transparent", color: "#e2e8f0",
              fontSize: 13, cursor: "pointer",
            }}>
            Reload app
          </button>
        </div>
      </div>
    );
  }
}
