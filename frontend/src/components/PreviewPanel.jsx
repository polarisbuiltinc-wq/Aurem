/**
 * PreviewPanel.jsx — Right-side live preview for code blocks parsed from
 * the latest assistant message.
 *
 *   - HTML/CSS/JS → rendered inside a sandboxed iframe via `srcDoc`
 *   - JSX/React   → transpiled at runtime via @babel/standalone (loaded
 *                    inside the iframe so it never touches the host page)
 *   - Other langs (python, bash, …) → syntax-coloured code viewer
 *
 * The iframe is sandboxed with `allow-scripts` only — no same-origin —
 * so any code rendered here cannot read parent state.
 */
import React, { useEffect, useMemo, useState } from "react";
import { X, Copy, RefreshCw, Code2, Eye, ExternalLink, Loader2, Rocket } from "lucide-react";
import { toast } from "./Toast";
import { api } from "../lib/api";
import DeployPanel from "./DeployPanel";

const RENDERABLE = new Set([
  "html", "htm",
  "jsx", "tsx",
  "js", "javascript",
  "live_url",  // iframe of an arbitrary preview URL
]);

function filename(block, idx) {
  if (!block) return `file_${idx}`;
  if (block.label) {
    // For codebase tabs, show only the basename so 20+ tabs fit
    // without each being squished to a single character. The full
    // path is preserved in the tab's `title` tooltip.
    const parts = block.label.split("/");
    return parts[parts.length - 1] || block.label;
  }
  const l = block.lang.toLowerCase();
  if (l === "live_url") return "Live Site";
  if (l === "jsx" || l === "tsx") return `App.${l}`;
  if (l === "html" || l === "htm") return "index.html";
  if (l === "css") return "styles.css";
  if (l === "js" || l === "javascript") return "script.js";
  if (l === "python" || l === "py") return "main.py";
  if (l === "yaml" || l === "yml") return "config.yml";
  if (l === "json") return "data.json";
  if (l === "bash" || l === "sh") return "script.sh";
  return `file_${idx}.${l}`;
}

function buildIframeDoc(block) {
  const code = block?.code || "";
  const l = (block?.lang || "").toLowerCase();
  // Direct HTML
  if (l === "html" || l === "htm") return code;
  // JSX — load React + Babel inside the iframe
  if (l === "jsx" || l === "tsx") {
    // Strip ES module syntax so we can run with `new Function()` after Babel