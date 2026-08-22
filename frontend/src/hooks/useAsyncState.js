/**
 * hooks/useAsyncState.js — 2026-08-24 (Pillar 5, Production-Readiness)
 *
 * Shared async-state primitive: idle / processing / success / failed /
 * timeout. No ghost states — every consumer gets one place to read
 * "what is happening right now" and one place to read the backend's
 * classified error (services/error_classifier.py on the backend
 * attaches `error_category` + `user_message` to every error response;
 * this hook surfaces both instead of a raw axios error object).
 *
 * Usage:
 *   const { state, error, run } = useAsyncState();
 *   const onSave = () => run(() => api.post("/x", body));
 *   // state: "idle" | "processing" | "success" | "failed" | "timeout"
 *   // error: { category, message } | null
 */
import { useCallback, useRef, useState } from "react";

const DEFAULT_TIMEOUT_MS = 30000;

export function useAsyncState(defaultTimeoutMs = DEFAULT_TIMEOUT_MS) {
  const [state, setState] = useState("idle");
  const [error, setError] = useState(null);
  const [data, setData] = useState(null);
  const runIdRef = useRef(0);

  const run = useCallback(async (fn, { timeoutMs = defaultTimeoutMs } = {}) => {
    const runId = ++runIdRef.current;
    setState("processing");
    setError(null);

    let timedOut = false;
    const timer = setTimeout(() => {
      if (runIdRef.current === runId) {
        timedOut = true;
        setState("timeout");
      }
    }, timeoutMs);

    try {
      const result = await fn();
      clearTimeout(timer);
      if (runIdRef.current !== runId || timedOut) return result;
      setData(result);
      setState("success");
      return result;
    } catch (err) {
      clearTimeout(timer);
      if (runIdRef.current !== runId) return undefined;
      const body = err?.response?.data;
      setError({
        category: body?.error_category || "internal",
        message: body?.user_message || body?.detail || "Something went wrong. Please try again.",
      });
      setState("failed");
      throw err;
    }
  }, [defaultTimeoutMs]);

  const reset = useCallback(() => {
    runIdRef.current += 1;
    setState("idle");
    setError(null);
    setData(null);
  }, []);

  return { state, error, data, run, reset,
            isIdle: state === "idle", isProcessing: state === "processing",
            isSuccess: state === "success", isFailed: state === "failed",
            isTimeout: state === "timeout" };
}
