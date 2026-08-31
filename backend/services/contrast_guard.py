"""
services/contrast_guard.py — Item 2 (2026-08-31)

Deterministic WCAG contrast guard. Given a foreground/background hex
pair, computes the WCAG 2.x relative-luminance contrast ratio (pure
math on RGB values — NO browser rendering, NO LLM). Threshold: 4.5:1
(WCAG AA, normal body text).

Honest note: no pre-existing contrast-check utility was found
elsewhere in this codebase (searched services/deploy_verify.py,
services/web_inspect.py, and every services/*.py for "luminance" /
"contrast_ratio" / "wcag" — zero hits) despite the request assuming
one already existed for the deploy-verify work. This module is a
new, from-scratch implementation of the standard WCAG formula, not a
reuse of prior code.

Used by services/local_tools.py::write_repo_file() to check/nudge any
CSS custom-property color pair BEFORE a stylesheet write is
committed — the design capability (P8) can change colors, but it may
never ship a palette that makes text unreadable.
"""
from __future__ import annotations

import re

WCAG_AA_NORMAL_TEXT = 4.5

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
# CSS custom property declarations, e.g. `--text-color: #1a1a1a;`
_CSS_VAR_RE = re.compile(
    r"(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{3,6})\s*;", re.IGNORECASE,
)
_TEXT_ROLE_RE = re.compile(r"text|foreground|\bfg\b|body", re.IGNORECASE)
_BG_ROLE_RE = re.compile(r"background|\bbg\b|surface|panel", re.IGNORECASE)


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        raise ValueError(f"not a valid hex color: {hex_color!r}")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    r, g, b = (max(0, min(255, round(c))) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def _linear_channel(c: int) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    """WCAG relative luminance, pure math, no LLM."""
    r, g, b = hex_to_rgb(hex_color)
    return (
        0.2126 * _linear_channel(r)
        + 0.7152 * _linear_channel(g)
        + 0.0722 * _linear_channel(b)
    )


def contrast_ratio(hex_a: str, hex_b: str) -> float:
    """Deterministic WCAG contrast ratio (1:1 .. 21:1)."""
    la, lb = relative_luminance(hex_a), relative_luminance(hex_b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def is_readable(fg_hex: str, bg_hex: str, threshold: float = WCAG_AA_NORMAL_TEXT) -> bool:
    return contrast_ratio(fg_hex, bg_hex) >= threshold


def _rgb_to_hsl(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    r, g, b = (c / 255.0 for c in rgb)
    mx, mn = max(r, g, b), min(r, g, b)
    l = (mx + mn) / 2.0
    if mx == mn:
        return 0.0, 0.0, l
    d = mx - mn
    s = d / (2.0 - mx - mn) if l > 0.5 else d / (mx + mn)
    if mx == r:
        h = ((g - b) / d) % 6
    elif mx == g:
        h = (b - r) / d + 2
    else:
        h = (r - g) / d + 4
    return h * 60.0, s, l


def _hsl_to_rgb(h: float, s: float, l: float) -> tuple[int, int, int]:
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h / 60.0) % 2 - 1))
    m = l - c / 2
    if h < 60:
        r, g, b = c, x, 0
    elif h < 120:
        r, g, b = x, c, 0
    elif h < 180:
        r, g, b = 0, c, x
    elif h < 240:
        r, g, b = 0, x, c
    elif h < 300:
        r, g, b = x, 0, c
    else:
        r, g, b = c, 0, x
    return round((r + m) * 255), round((g + m) * 255), round((b + m) * 255)


def nudge_to_readable(
    fg_hex: str, bg_hex: str, threshold: float = WCAG_AA_NORMAL_TEXT,
) -> str:
    """Deterministically darkens/lightens `fg_hex` (in HSL lightness
    steps) toward `bg_hex` until the pair clears `threshold`, or gives
    up at pure black/white (returns the closest achievable value —
    every real background has a readable extreme). No LLM, no
    randomness — same (fg, bg, threshold) always returns the same
    nudged color."""
    if is_readable(fg_hex, bg_hex, threshold):
        return fg_hex
    h, s, l = _rgb_to_hsl(hex_to_rgb(fg_hex))
    bg_l = relative_luminance(bg_hex)
    # Light background -> darken the foreground; dark background ->
    # lighten it. Step in 2% lightness increments (deterministic,
    # bounded — 50 steps max covers the full 0..1 range).
    direction = -0.02 if bg_l > 0.5 else 0.02
    candidate = fg_hex
    for _ in range(50):
        l = max(0.0, min(1.0, l + direction))
        candidate = rgb_to_hex(_hsl_to_rgb(h, s, l))
        if is_readable(candidate, bg_hex, threshold):
            return candidate
        if l in (0.0, 1.0):
            break
    return candidate


def check_and_nudge_css(css_text: str, threshold: float = WCAG_AA_NORMAL_TEXT) -> dict:
    """Scans `css_text` for `--var: #hex;` custom properties, pairs
    likely text-role vars against likely background-role vars (by
    name — text/foreground/fg/body vs background/bg/surface/panel),
    and nudges any pair below `threshold`. Returns:
      {content: <possibly-modified css>, adjustments: [ {label,
       text_var, bg_var, before_ratio, after_ratio, original_fg,
       nudged_fg} ], ok: bool}
    A no-op (ok=True, adjustments=[]) when no theme vars are found or
    every pair already clears the threshold — this never fabricates a
    finding."""
    declarations = _CSS_VAR_RE.findall(css_text)
    if not declarations:
        return {"content": css_text, "adjustments": [], "ok": True}
    text_vars = [(name, val) for name, val in declarations if _TEXT_ROLE_RE.search(name)]
    bg_vars = [(name, val) for name, val in declarations if _BG_ROLE_RE.search(name)]
    if not text_vars or not bg_vars:
        return {"content": css_text, "adjustments": [], "ok": True}

    content = css_text
    adjustments = []
    for text_name, text_hex in text_vars:
        for bg_name, bg_hex in bg_vars:
            try:
                before = contrast_ratio(text_hex, bg_hex)
            except ValueError:
                continue
            if before >= threshold:
                continue
            nudged = nudge_to_readable(text_hex, bg_hex, threshold)
            after = contrast_ratio(nudged, bg_hex)
            adjustments.append({
                "label": f"{text_name} on {bg_name}",
                "text_var": text_name, "bg_var": bg_name,
                "before_ratio": round(before, 2), "after_ratio": round(after, 2),
                "original_fg": text_hex, "nudged_fg": nudged,
            })
            if nudged != text_hex:
                content = re.sub(
                    re.escape(text_name) + r"\s*:\s*" + re.escape(text_hex) + r"\s*;",
                    f"{text_name}: {nudged};",
                    content, count=1,
                )
    return {"content": content, "adjustments": adjustments, "ok": not adjustments or all(
        a["after_ratio"] >= threshold for a in adjustments
    )}


def describe_nudge(adjustment: dict) -> str:
    """Plain-English, one-line description of a single contrast nudge
    for a NON-TECHNICAL owner — no 'WCAG'/'luminance'/'token'/var-name
    jargon, ever (Item 2, 2026-08-31 — chat visibility for design
    fixes). Deterministic: same adjustment always produces the same
    sentence, no LLM involved."""
    orig = adjustment.get("original_fg", "")
    nudged = adjustment.get("nudged_fg", "")
    before = adjustment.get("before_ratio", 0)
    after = adjustment.get("after_ratio", 0)
    try:
        direction = "darker" if relative_luminance(nudged) < relative_luminance(orig) else "lighter"
    except ValueError:
        direction = "different"
    return (
        f"I made some text a touch {direction} — it wasn't easy to read "
        f"against its background before (was {before:.1f}:1, now {after:.1f}:1)."
    )
