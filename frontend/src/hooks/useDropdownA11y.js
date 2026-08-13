/**
 * useDropdownA11y — Iter 388t · Bug 28 · WCAG 2.1.1 keyboard navigation.
 *
 * Reusable hook for custom (non-native <select>) dropdowns / listboxes /
 * menus.  Handles:
 *   • ArrowDown / ArrowUp — move focus/highlight through items
 *   • Home / End — jump to first / last item
 *   • Enter / Space — activate the highlighted item (calls onSelect)
 *   • Escape — close the dropdown (calls onClose)
 *   • Type-ahead (single char) — jump to first item whose label starts with the char
 *
 * The hook is UNCONTROLLED — it returns `{ activeIndex, setActiveIndex,
 * onKeyDown }` and the caller wires them to their own component.  This
 * keeps the hook agnostic about how items are stored / rendered.
 *
 * Usage:
 *   const items = [{ id, label }, ...];
 *   const { activeIndex, setActiveIndex, onKeyDown } = useDropdownA11y({
 *     items, isOpen, onSelect: (item) => doThing(item), onClose,
 *   });
 *   <div onKeyDown={onKeyDown}>
 *     {items.map((it, i) => (
 *       <button
 *         key={it.id}
 *         aria-selected={i === activeIndex}
 *         className={i === activeIndex ? "is-active" : ""}
 *       > {it.label} </button>
 *     ))}
 *   </div>
 */
import { useState, useCallback, useEffect } from "react";

export function useDropdownA11y({
  items,
  isOpen,
  onSelect,
  onClose,
  labelKey = "label",     // property on each item used for type-ahead
}) {
  const [activeIndex, setActiveIndex] = useState(0);
  const count = items?.length || 0;

  // Reset the highlight to the first item every time the menu opens
  // so the user's cursor doesn't land on a stale row from a previous
  // open (would be confusing on keyboard-only workflows).
  useEffect(() => {
    if (isOpen) setActiveIndex(0);
  }, [isOpen]);

  const onKeyDown = useCallback((e) => {
    if (!isOpen || count === 0) return;
    const key = e.key;

    if (key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => (i + 1) % count);
      return;
    }
    if (key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => (i - 1 + count) % count);
      return;
    }
    if (key === "Home") {
      e.preventDefault();
      setActiveIndex(0);
      return;
    }
    if (key === "End") {
      e.preventDefault();
      setActiveIndex(count - 1);
      return;
    }
    if (key === "Enter" || key === " ") {
      e.preventDefault();
      const target = items[activeIndex];
      if (target != null) onSelect?.(target, activeIndex);
      return;
    }
    if (key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      onClose?.();
      return;
    }
    // Single-character type-ahead — jumps to first item whose label
    // starts with that character (case-insensitive).  Useful for long
    // lists (e.g. slash-command menu with 20+ entries).
    if (key.length === 1 && /\S/.test(key)) {
      const ch = key.toLowerCase();
      const start = (activeIndex + 1) % count;
      for (let step = 0; step < count; step++) {
        const idx = (start + step) % count;
        const label = String(items[idx]?.[labelKey] ?? "").toLowerCase();
        if (label.startsWith(ch)) {
          setActiveIndex(idx);
          return;
        }
      }
    }
  }, [isOpen, count, items, activeIndex, onSelect, onClose, labelKey]);

  return { activeIndex, setActiveIndex, onKeyDown };
}

export default useDropdownA11y;
