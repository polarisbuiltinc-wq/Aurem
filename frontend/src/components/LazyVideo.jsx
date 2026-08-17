/**
 * LazyVideo.jsx — Iter 391 · viewport-lazy demo video
 *
 * Purpose
 * -------
 * PageSpeed Insights Mobile flagged 45 MB of demo videos (5×~9-12 MB
 * each) on the Landing page as "Enormous network payload". Even
 * `preload="metadata"` fetches a first-frame + moov atom immediately
 * on page load, which serializes with the LCP paint on Slow 4G and
 * ballooned mobile LCP to 8.0 s.
 *
 * This component defers the entire <video> element until the card
 * scrolls within a generous rootMargin of the viewport, then swaps
 * in the real <video> with `preload="none"` so the file is fetched
 * only when the user actually taps play. Before that, we render a
 * tinted placeholder that matches the exact CSS shape (`video-thumb`
 * wrapper already sets aspect-ratio in Landing.css) so there is
 * ZERO layout shift when the video hydrates.
 *
 * Guardrails
 * ---------
 *   • `IntersectionObserver` is unavailable in some very old browsers
 *     — fall through to eager render (same as before) so the videos
 *     still work.
 *   • `rootMargin: "200px"` gives ~one-viewport of lead-time so the
 *     browser has network budget to fetch by the time the user
 *     actually reaches the card.
 *   • `preload="none"` means TAPPING play triggers the load; the
 *     browser will then request the file with Range headers.
 */
import { useEffect, useRef, useState } from "react";

export default function LazyVideo({ src, tint = "", "data-testid": testId }) {
  const ref = useRef(null);
  const [inView, setInView] = useState(false);

  useEffect(() => {
    if (!ref.current) return;
    if (typeof IntersectionObserver === "undefined") {
      setInView(true);
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setInView(true);
            io.disconnect();
            break;
          }
        }
      },
      { rootMargin: "200px 0px" },
    );
    io.observe(ref.current);
    return () => io.disconnect();
  }, []);

  return (
    <div ref={ref} className={`video-thumb ${tint}`} data-testid={testId}>
      {inView ? (
        <video
          src={src}
          controls
          playsInline
          preload="none"
          poster=""
        />
      ) : (
        // Placeholder respects the parent's aspect-ratio so no CLS.
        // Solid tint from the parent class is enough; no children.
        <div aria-hidden="true" style={{ width: "100%", height: "100%" }} />
      )}
    </div>
  );
}
