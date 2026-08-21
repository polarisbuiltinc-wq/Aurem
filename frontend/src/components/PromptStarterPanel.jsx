/**
 * PromptStarterPanel.jsx — guided "what do I even type?" panel shown
 * above the composer only before a project's very first real message.
 *
 * 2026-08-22 — v1: 5 static example categories (replaced 3-pill
 * FirstMessageChips). Clicking a card pre-fills the composer (via
 * `onPick`) — it never auto-sends, the user still reviews/edits.
 *
 * 2026-08-22 — v2: personalized + rotating cards.
 *   - Pool is fetched from GET /findings/starter-suggestions, which
 *     turns real critical/high findings (the same ones Vanguard/QA
 *     scans already found) into plain-English prompts, padded with
 *     the 5 generic categories so the pool never runs dry.
 *   - Auto-rotate: if the composer is still empty, one random visible
 *     card is swapped for an unseen one from the pool every ~18s —
 *     "random change if user not clicking".
 *   - Used-swap: clicking a card pre-fills the composer AND that
 *     card is instantly replaced by a fresh suggestion so the same
 *     "used" card never lingers.
 *   - Disappears entirely once the first real message is sent
 *     (parent still gates on `messages.length <= 1` — unchanged).
 */
import React, { useState, useEffect, useRef, useCallback } from "react";
import { Sparkles, Bug, CheckCircle2, Zap, ShieldCheck } from "lucide-react";
import { api } from "../lib/api";

const ICONS = { sparkles: Sparkles, bug: Bug, check: CheckCircle2, zap: Zap, shield: ShieldCheck };

const FALLBACK_POOL = [
  { slug: "build-something-new", icon_hint: "sparkles", label: "Build something new", example: "I want a contact form on my website" },
  { slug: "somethings-broken", icon_hint: "bug", label: "Something's broken", example: "The login button doesn't work, please fix it" },
  { slug: "check-everything-working", icon_hint: "check", label: "Check everything is working", example: "Check my whole website for any bugs" },
  { slug: "add-new-feature", icon_hint: "zap", label: "Add a new feature", example: "Let users upload a profile photo" },
  { slug: "security-check", icon_hint: "shield", label: "Security check", example: "Check my code for any security problems" },
  // 2026-08-22 — extra alternates so the panel still has something to
  // rotate/swap to even when the backend pool is exhausted or offline.
  { slug: "build-something-new-alt", icon_hint: "sparkles", label: "Build something new", example: "Add a newsletter signup box to my homepage" },
  { slug: "somethings-broken-alt", icon_hint: "bug", label: "Something's broken", example: "My site shows an error page, can you fix it?" },
  { slug: "check-everything-working-alt", icon_hint: "check", label: "Check everything is working", example: "Scan my project for any broken links or errors" },
  { slug: "add-new-feature-alt", icon_hint: "zap", label: "Add a new feature", example: "Add a dark mode toggle to my site" },
  { slug: "security-check-alt", icon_hint: "shield", label: "Security check", example: "Make sure my users' passwords are stored safely" },
];

const VISIBLE_COUNT = 5;
const ROTATE_MS = 18000;

export default function PromptStarterPanel({ onPick, projectId, inputEmpty = true }) {
  const [visible, setVisible] = useState(FALLBACK_POOL.slice(0, VISIBLE_COUNT));
  const poolRef = useRef([]); // remaining unseen suggestions, not yet shown
  const shownSlugsRef = useRef(new Set(FALLBACK_POOL.slice(0, VISIBLE_COUNT).map((c) => c.slug)));

  useEffect(() => {
    let cancelled = false;
    if (!projectId) return;
    api.get(`/findings/starter-suggestions?project_id=${encodeURIComponent(projectId)}&limit=20`)
      .then((res) => {
        if (cancelled) return;
        const suggestions = res?.data?.suggestions;
        if (!Array.isArray(suggestions) || suggestions.length < VISIBLE_COUNT) return;
        const shown = suggestions.slice(0, VISIBLE_COUNT);
        poolRef.current = suggestions.slice(VISIBLE_COUNT);
        shownSlugsRef.current = new Set(shown.map((c) => c.slug));
        setVisible(shown);
      })
      .catch(() => { /* keep FALLBACK_POOL — never break the empty state */ });
    return () => { cancelled = true; };
  }, [projectId]);

  const nextFromPool = useCallback(() => {
    // Prefer an unseen pool item; recycle the fallback pool (skipping
    // anything currently on screen) once the fetched pool is empty.
    let candidate = poolRef.current.shift();
    if (!candidate) {
      candidate = FALLBACK_POOL.find((c) => !shownSlugsRef.current.has(c.slug))
        || FALLBACK_POOL[Math.floor(Math.random() * FALLBACK_POOL.length)];
    }
    return candidate;
  }, []);

  const swapCard = useCallback((idx) => {
    setVisible((cur) => {
      const replacement = nextFromPool();
      if (!replacement) return cur;
      shownSlugsRef.current.delete(cur[idx]?.slug);
      shownSlugsRef.current.add(replacement.slug);
      const next = cur.slice();
      next[idx] = replacement;
      return next;
    });
  }, [nextFromPool]);

  // Auto-rotate one random card every ROTATE_MS while the composer is
  // still empty (i.e. the user hasn't started typing/hasn't clicked).
  useEffect(() => {
    if (!inputEmpty) return;
    const t = setInterval(() => {
      swapCard(Math.floor(Math.random() * VISIBLE_COUNT));
    }, ROTATE_MS);
    return () => clearInterval(t);
  }, [inputEmpty, swapCard]);

  const handlePick = (idx, example) => {
    onPick(example);
    swapCard(idx); // instantly replace the just-used card
  };

  return (
    <div data-testid="prompt-starter-panel" style={{ marginBottom: 10 }}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(15.5rem, 1fr))",
          gap: 8,
        }}
      >
        {visible.map(({ slug, icon_hint, label, example, personalized }, idx) => {
          const Icon = ICONS[icon_hint] || Sparkles;
          return (
            <button
              key={`${slug}-${idx}`}
              type="button"
              data-testid={`prompt-starter-card-${slug}`}
              onClick={() => handlePick(idx, example)}
              title={`Click to try: "${example}"`}
              style={{
                display: "flex", flexDirection: "column", gap: 4,
                alignItems: "flex-start", textAlign: "left",
                padding: "10px 12px", borderRadius: 12,
                color: "#ffb37a",
                background: "rgba(255,102,8,0.06)",
                border: "1px solid rgba(255,102,8,0.22)",
                cursor: "pointer",
                transition: "background 0.15s ease, border-color 0.15s ease, transform 0.1s ease",
                minWidth: 0,
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = "rgba(255,102,8,0.13)";
                e.currentTarget.style.borderColor = "rgba(255,102,8,0.4)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = "rgba(255,102,8,0.06)";
                e.currentTarget.style.borderColor = "rgba(255,102,8,0.22)";
              }}
              onMouseDown={(e) => { e.currentTarget.style.transform = "scale(0.98)"; }}
              onMouseUp={(e) => { e.currentTarget.style.transform = "scale(1)"; }}
            >
              <span style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0, width: "100%" }}>
                <Icon size={14} strokeWidth={2.2} style={{ flexShrink: 0 }} />
                <span style={{
                  fontSize: 13, fontWeight: 600,
                  color: "var(--text, #e4e6eb)",
                  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                }}>
                  {label}
                </span>
                {personalized && (
                  <span
                    data-testid={`prompt-starter-card-${slug}-personalized-badge`}
                    title="Found in your connected repo"
                    style={{
                      marginLeft: "auto", flexShrink: 0, fontSize: 9, fontWeight: 700,
                      letterSpacing: "0.04em", color: "#4ade80",
                      background: "rgba(74,222,128,0.12)", borderRadius: 4,
                      padding: "1px 5px",
                    }}
                  >
                    FROM YOUR REPO
                  </span>
                )}
              </span>
              <span style={{
                fontSize: 12, lineHeight: 1.35,
                color: "var(--text-dim, #9aa0aa)",
                fontStyle: "italic",
                overflowWrap: "break-word",
              }}>
                &ldquo;{example}&rdquo;
              </span>
            </button>
          );
        })}
      </div>
      <div
        data-testid="prompt-starter-security-note"
        style={{
          display: "flex", alignItems: "center", gap: 6,
          marginTop: 8, padding: "0 2px",
          fontSize: 11, color: "var(--text-faint, #6a6f78)",
        }}
      >
        <ShieldCheck size={12} strokeWidth={2} style={{ flexShrink: 0, color: "#4ade80" }} />
        <span>
          Don&apos;t worry about phrasing it perfectly — just describe what you want in plain
          English. AUREM writes secure code by default; every change is scanned by Vanguard
          before it ships.
        </span>
      </div>
    </div>
  );
}
