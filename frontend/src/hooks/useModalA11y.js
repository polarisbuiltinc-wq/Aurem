/**
 * useModalA11y — Iter 388t · Bug 27 · WCAG 2.1.2/2.4.3 focus management.
 *
 * Reusable hook for modal/dialog accessibility:
 *   • Focus trap — Tab wraps within modal, Shift+Tab wraps backward
 *   • Escape key closes the modal (calls onClose)
 *   • Restores focus to the element that opened the modal on close
 *   • Focuses the first focusable element inside modal on open
 *
 * Usage:
 *   const modalRef = useRef(null);
 *   useModalA11y({ ref: modalRef, isOpen: open, onClose: () => setOpen(false) });
 *   return open && <div ref={modalRef} role="dialog" aria-modal="true">...</div>;
 *
 * Focus trap implementation uses `keydown` on the modal container so any
 * child input/button/etc. still gets its normal key handling; we only
 * intervene on Tab / Shift+Tab to keep focus within the trap.
 */
import { useEffect } from "react";

// Focusable-element selector — matches the WCAG-standard set of
// interactive elements that appear in the sequential focus order.
const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1']):not([disabled])",
].join(",");

function _getFocusables(container) {
  if (!container) return [];
  const nodes = Array.from(container.querySelectorAll(FOCUSABLE_SELECTOR));
  // Skip elements explicitly hidden via `hidden` attribute or inline
  // `display:none`.  We intentionally don't filter on
  // getBoundingClientRect().width/height because jsdom (and some
  // pre-layout mount transitions in real browsers) report zero size
  // for perfectly valid focusable buttons, which would break the
  // Tab-wrap trap.  The selector already excludes tabindex=-1 /
  // disabled controls; hidden attribute + display:none catches the
  // remaining common "invisible but in DOM" cases.
  return nodes.filter((el) => {
    if (el.hasAttribute("hidden")) return false;
    const inline = el.style && el.style.display;
    if (inline === "none") return false;
    return true;
  });
}

export function useModalA11y({ ref, isOpen, onClose, initialFocus = null }) {
  useEffect(() => {
    if (!isOpen) return;
    const container = ref?.current;
    if (!container) return;

    // Remember which element had focus before the modal opened so we
    // can restore it on close (browser back button, screen reader
    // announcement, etc. all rely on this).
    const previouslyFocused = document.activeElement;

    // Move focus into the modal on open.  If the caller passed an
    // explicit `initialFocus` ref, use that; otherwise focus the
    // first focusable child; otherwise focus the container itself
    // (we set tabIndex=-1 on the container implicitly for this).
    const focusables = _getFocusables(container);
    const target = initialFocus?.current || focusables[0] || container;
    // Ensure the container is programmatically focusable if we fall
    // back to focusing it directly.
    if (target === container && !container.hasAttribute("tabindex")) {
      container.setAttribute("tabindex", "-1");
    }
    try { target.focus({ preventScroll: false }); } catch { /* noop */ }

    const onKeyDown = (e) => {
      // Escape → close (only when the modal itself is focused chain).
      if (e.key === "Escape") {
        // Don't hijack Escape if a native <select>/<input> is doing
        // its own thing (e.g. Escape clears an autocomplete).  Most
        // form controls swallow Escape on keydown before it bubbles;
        // if we still see it, the user genuinely wants to close.
        e.stopPropagation();
        onClose?.();
        return;
      }
      // Tab / Shift+Tab → wrap focus inside the modal.
      if (e.key === "Tab") {
        const currentFocusables = _getFocusables(container);
        if (currentFocusables.length === 0) {
          // Nothing to Tab into — swallow so Tab doesn't leak out.
          e.preventDefault();
          return;
        }
        const first = currentFocusables[0];
        const last = currentFocusables[currentFocusables.length - 1];
        const active = document.activeElement;
        if (e.shiftKey && active === first) {
          e.preventDefault();
          try { last.focus(); } catch { /* noop */ }
        } else if (!e.shiftKey && active === last) {
          e.preventDefault();
          try { first.focus(); } catch { /* noop */ }
        }
        // If focus is somewhere in the middle, let default Tab behavior
        // run — the browser handles the sequential focus order.
      }
    };

    container.addEventListener("keydown", onKeyDown);
    return () => {
      container.removeEventListener("keydown", onKeyDown);
      // Restore focus to whatever had it before the modal opened.
      // Wrapped in try/catch in case the element was unmounted.
      try {
        if (previouslyFocused && previouslyFocused.focus) {
          previouslyFocused.focus({ preventScroll: true });
        }
      } catch { /* noop */ }
    };
  }, [isOpen, ref, onClose, initialFocus]);
}

export default useModalA11y;
