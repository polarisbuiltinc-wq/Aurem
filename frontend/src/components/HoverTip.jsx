/**
 * HoverTip.jsx — 2026-02-18
 *
 * Zero-dep, CSS-only rich tooltip.  Wraps an interactive element and
 * shows an instant-appearing pill (150ms fade) with the tooltip text
 * on hover / focus.  Preferred over the browser-native `title=""`
 * attribute for:
 *   - Instant appearance (native title has a ~500ms OS delay)
 *   - Rich styling that matches the composer aesthetic
 *   - Multi-line explanatory text without being cut off by the OS
 *
 * Kept intentionally minimal — no portals, no dynamic positioning
 * (uses static top / bottom placement), no arrow polygons.  If we
 * need positioning heuristics later we can swap for radix-ui's
 * @radix-ui/react-tooltip in one place without changing call sites.
 *
 * Usage:
 *   <HoverTip content="Explains the button" placement="top">
 *     <button>Click me</button>
 *   </HoverTip>
 *
 * Notes on accessibility: the wrapping <span> is `role="group"` and
 * the tooltip node is `role="tooltip"` with an id linked via
 * `aria-describedby` on the child (single child assumed).  Screen
 * readers read the tooltip after the child's label — same order as
 * radix.
 */
import React, { useId, useRef, useState, cloneElement } from "react";

export default function HoverTip({
  content,
  placement = "top",   // 'top' | 'bottom'
  children,
  maxWidth = 260,
  delay = 80,          // ms — very short, we want instant feedback
}) {
  const [open, setOpen] = useState(false);
  const timerRef = useRef(null);
  const tipId = useId();

  const show = () => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setOpen(true), delay);
  };
  const hide = () => {
    if (timerRef.current) clearTimeout(timerRef.current);
    setOpen(false);
  };

  // Clone the single child so we can inject aria-describedby without
  // adding an extra wrapper element to the tab order.
  const child = React.Children.only(children);
  const enhanced = cloneElement(child, {
    "aria-describedby": open ? tipId : undefined,
    // Preserve any existing handlers on the child.
    onMouseEnter: (e) => { child.props.onMouseEnter?.(e); show(); },
    onMouseLeave: (e) => { child.props.onMouseLeave?.(e); hide(); },
    onFocus:      (e) => { child.props.onFocus?.(e);      show(); },
    onBlur:       (e) => { child.props.onBlur?.(e);       hide(); },
  });

  const tipStyle = {
    position:      "absolute",
    left:          "50%",
    transform:     "translateX(-50%)",
    [placement === "top" ? "bottom" : "top"]: "calc(100% + 6px)",
    zIndex:        1000,
    padding:       "6px 10px",
    background:    "var(--panel-3, #14161c)",
    border:        "1px solid var(--border-strong, rgba(255,255,255,0.14))",
    borderRadius:  6,
    color:         "var(--text, #d8dae0)",
    fontSize:      11,
    lineHeight:    1.4,
    fontWeight:    400,
    letterSpacing: 0,
    textTransform: "none",
    fontFamily:    "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
    maxWidth,
    minWidth:      120,
    textAlign:     "center",
    whiteSpace:    "normal",
    wordBreak:     "break-word",
    pointerEvents: "none",
    opacity:       open ? 1 : 0,
    transform_:    open ? "translateX(-50%) translateY(0)"
                        : `translateX(-50%) translateY(${placement === "top" ? 2 : -2}px)`,
    transition:    "opacity 140ms ease, transform 140ms ease",
    boxShadow:     "0 4px 12px rgba(0,0,0,0.4)",
  };
  // React can't have two `transform` keys, so merge manually.
  tipStyle.transform = tipStyle.transform_;
  delete tipStyle.transform_;

  return (
    <span
      role="group"
      style={{ position: "relative", display: "inline-flex", alignItems: "center" }}
    >
      {enhanced}
      {content && (
        <span
          id={tipId}
          role="tooltip"
          data-testid="hover-tip"
          data-open={open ? "1" : "0"}
          style={tipStyle}
        >
          {content}
        </span>
      )}
    </span>
  );
}
