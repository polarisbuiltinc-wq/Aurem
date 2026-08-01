"""
services/llm/_meta.py — Session D · D-part-2
==========================================

`call_llm_with_meta` (Langfuse-traced public entry-point) and its
real body `_call_llm_with_meta_inner` (472 LOC pre-extraction)
moved out of `services/llm/__init__.py` in D-part-2 to shrink the
package root and keep the routing brain in one sibling file.

Re-exported unchanged via `services/llm/__init__.py` so every
caller — orchestrator, chat router, tests — resolves the same
names byte-for-byte.

Monkeypatch contract
--------------------
Callers historically patch `services.llm._call_claude`,
`services.llm._call_deepseek`, `services.llm._call_glm`,
`services.llm._call_longcat`, `services.llm._openrouter_key`.
Those names all live on the `services.llm` package namespace
(either as canonical defs, in `_openrouter_key`'s case, or as
re-exports from `openrouter_providers`).

Because Python name resolution inside a function walks the
function's `__globals__` (= this module's namespace by default),
a naive `from services.llm import _call_claude` at the TOP of
this file would bind the name locally and a runtime
`monkeypatch.setattr(llm_mod, "_call_claude", ...)` would NOT
reach us.

The idiom used below — a single lazy `from services.llm import
...` line at the top of `_call_llm_with_meta_inner`'s body — re-
resolves every dependency on each call via the `services.llm`
package's module dict, so runtime patches take effect. Cost is a
handful of dict lookups per LLM call (LLM roundtrip is
500ms–5s — the import overhead is unmeasurable).
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


async def call_llm_with_meta(system: str, user: str,
                              max_tokens: int = 1500,
                              mode: str = "chat",
                              user_id: Optional[str] = None,
                              review_mode: Optional[str] = None,
                              step_hook=None) -> dict:
    """
    Orchestrator-facing entry point.

    mode="code"  → Claude Sonnet (better code quality, higher token budget)
    mode="chat"  → DeepSeek (fast, cheap)
    mode=other   → DeepSeek

    Iter 94 — Maxx-mode cap (Pro tier = 100/mo):
    If `user_id` is provided and the caller would normally use Claude
    (mode in {code, review}), we first check the user's Maxx budget.
    Capped users transparently fall back to DeepSeek and the response
    includes `maxx_capped=True` + `maxx_remaining=0` so the UI can show
    an upgrade nudge.

    Iter 212m-18 — `review_mode` (Swift/Pro/Maxx) overrides the legacy
    `mode` routing:
      • swift → GLM-5.2 only (no Claude under any circumstance)
      • pro   → GLM-5.2 first; if it returns empty or raises, fall back
                to Claude Sonnet so the user never sees a blank reply
      • maxx  → GLM-5.2 produces the initial response, then Claude is
                given that output with a "Review and improve this
                code:" instruction and the IMPROVED text is what
                ships to the user
    Legacy callers that don't pass `review_mode` keep the original
    behaviour. `step_hook(text, done=False)` is invoked at phase
    boundaries so the chat SSE worker can stream progress frames.

    Iter 212m-118 — `LITELLM_ROUTER_ENABLED=1` short-circuits this
    entire 4-hop chain in favour of the unified litellm.Router (see
    services/llm_router.py). Default OFF so production behaviour is
    unchanged; flip the env var on to migrate.

    Iter 212m-119 — Every call is auto-traced to Langfuse Cloud
    (https://us.cloud.langfuse.com) via services.langfuse_tracing.
    Tracing is silently disabled when LANGFUSE_*_KEY env vars are
    missing; a Langfuse outage never breaks an LLM call.
    """
    # Iter 212m-119 — Langfuse observability wrapper.
    from services.langfuse_tracing import trace_llm_call
    with trace_llm_call(
        name="ora.llm.call_llm_with_meta",
        mode=mode, review_mode=review_mode,
        user_id=user_id,
        system_prompt=system, user_prompt=user,
        extra_metadata={"max_tokens": max_tokens},
    ) as _lf:
        result = await _call_llm_with_meta_inner(
            system=system, user=user, max_tokens=max_tokens,
            mode=mode, user_id=user_id, review_mode=review_mode,
            step_hook=step_hook,
        )
        _lf["success"](result)
        return result


async def _call_llm_with_meta_inner(system: str, user: str,
                                     max_tokens: int = 1500,
                                     mode: str = "chat",
                                     user_id: Optional[str] = None,
                                     review_mode: Optional[str] = None,
                                     step_hook=None) -> dict:
    """Real body of call_llm_with_meta — Iter 212m-119 split for tracing.

    Iter 367 (audit cleanup) — Removed the litellm.Router opt-in path
    (services.llm_router) that was permanently gated behind
    LITELLM_ROUTER_ENABLED=1 — an env flag that was never set anywhere
    in the codebase. The 167-line dead module has been deleted; the
    legacy multi-provider chain below IS the production LLM path.
    """
    # Session D · D-part-2 — lazy imports for monkeypatch-contract
    # preservation. `services.llm.<name> = X` at test-time must reach
    # us; a module-top `from services.llm import ...` would bind the
    # names into _meta.py's namespace and silently bypass the patch.
    from services.llm import (
        _CLAUDE_MODES,
        _CLAUDE_MODEL,
        _GLM_MODEL,
        _LONGCAT_MODEL,
        LONGCAT_ENABLED,
        COUNCIL_B_GLM_ENABLED,
        _call_claude,
        _call_deepseek,
        _call_glm,
        _call_longcat,
        _deepseek_model,
        _openrouter_key,
        cap_for,
        temperature_for,
    )

    temperature = temperature_for(mode)
    actual_tokens = min(max_tokens, cap_for(mode))

    # ── Iter 212m-18 — Review-mode routing (Swift / Pro / Maxx) ─────────
    # Iter 212m-159 — Council A primary swaps GLM-5.2 → LongCat-2.0 when
    # LONGCAT_ENABLED=true AND mode=="code".  Claude rescue path unchanged.
    # Iter 212m-165 — Council B (`mode="analysis"`) and Council C
    # (`mode="write"`) BYPASS the swift/pro/maxx routing entirely so
    # writing tasks land on DeepSeek (cheap + good fit) and analysis
    # tasks land on the GLM-5.2 + DeepSeek rescue chain — not on the
    # GLM-forced swift path.  Legacy callers using mode="code" or
    # mode="chat" with review_mode set are unaffected.
    rm = (review_mode or "").lower().strip()
    if rm in {"swift", "pro", "maxx"} and mode not in {"analysis", "write"}:
        # Maxx-budget gate still applies — Pro/Maxx tiers track Claude
        # usage even when GLM is the primary because Claude is the
        # fallback/reviewer.
        maxx_remaining: Optional[int] = None
        maxx_capped = False
        maxx_overage = False
        if rm in {"pro", "maxx"} and user_id:
            try:
                from services.usage import get_maxx_usage
                u = await get_maxx_usage(user_id)
                maxx_remaining = u.get("remaining")
                if u.get("capped"):
                    tier = u.get("tier", "free")
                    if tier in ("pro", "team", "founder"):
                        maxx_overage = True
                    else:
                        # Free/Starter at cap → degrade Pro/Maxx to Swift
                        # (GLM only) so the chat path never silently
                        # falls back to Claude past the budget.
                        rm = "swift"
                        maxx_capped = True
            except Exception as e:
                logger.warning(f"maxx budget check failed (allowing): {e!r}")

        # Iter 212m-159 — pick Council A primary based on flag + mode.
        use_longcat = LONGCAT_ENABLED and mode == "code"
        primary_model_id   = _LONGCAT_MODEL if use_longcat else _GLM_MODEL
        primary_provider   = "longcat-2.0" if use_longcat else "glm-5.2"
        primary_caller     = _call_longcat if use_longcat else _call_glm

        # Step 1 — primary first.
        if step_hook:
            try:
                step_hook("🤔 Thinking…")
            except Exception:
                pass
        glm_content = ""
        glm_err: Optional[Exception] = None
        try:
            glm_content = await primary_caller(
                system=system, user=user,
                max_tokens=actual_tokens, temperature=temperature,
            )
        except Exception as e:
            glm_err = e
            logger.warning(f"{primary_provider} call raised: {e!r}")

        if rm == "swift":
            return {
                "ok":             True if (glm_content or not glm_err) else False,
                "provider":       primary_provider,
                "content":        glm_content,
                "temperature":    temperature,
                "mode":           mode,
                "review_mode":    "swift",
                "model":          primary_model_id,
                "fallback_chain": [primary_provider],
                "maxx_capped":    maxx_capped,
                "maxx_overage":   False,
                "maxx_remaining": maxx_remaining,
                **({"error": f"{primary_provider} unavailable: {glm_err}"} if glm_err else {}),
            }

        if rm == "pro":
            # GLM ok → use it. Otherwise fall back to Claude so the user
            # never sees an empty reply.
            if glm_content.strip():
                return {
                    "ok":             True,
                    "provider":       primary_provider,
                    "content":        glm_content,
                    "temperature":    temperature,
                    "mode":           mode,
                    "review_mode":    "pro",
                    "model":          primary_model_id,
                    "fallback_chain": [primary_provider],
                    "maxx_capped":    maxx_capped,
                    "maxx_overage":   maxx_overage,
                    "maxx_remaining": maxx_remaining,
                }
            logger.info(
                "Pro mode: %s returned empty (err=%r) — falling back to Claude",
                primary_provider, glm_err,
            )
            if step_hook:
                try:
                    step_hook(f"⚙️ {primary_provider} empty — falling back to Claude…")
                except Exception:
                    pass
            try:
                claude_content = await _call_claude(
                    system=system, user=user,
                    max_tokens=actual_tokens, temperature=temperature,
                )
            except Exception as e:
                logger.error(f"Pro mode: Claude fallback also failed: {e!r}")
                return {
                    "ok": False, "provider": None, "content": "",
                    "temperature": temperature, "mode": mode,
                    "review_mode": "pro",
                    "fallback_chain": [primary_provider, "claude-sonnet"],
                    "maxx_capped":    maxx_capped,
                    "maxx_overage":   maxx_overage,
                    "maxx_remaining": maxx_remaining,
                    "error": f"Both {primary_provider} and Claude unavailable: {e}",
                }
            return {
                "ok":             bool(claude_content.strip()),
                "provider":       "claude-sonnet-pro-fallback",
                "content":        claude_content,
                "temperature":    temperature,
                "mode":           mode,
                "review_mode":    "pro",
                "model":          _CLAUDE_MODEL,
                "fallback_chain": [primary_provider, "claude-sonnet"],
                "maxx_capped":    maxx_capped,
                "maxx_overage":   maxx_overage,
                "maxx_remaining": maxx_remaining,
            }

        # rm == "maxx" — primary produces the draft, Claude reviews+improves.
        if not glm_content.strip():
            # primary gave nothing → Claude has no draft to improve, so just
            # let Claude answer directly (graceful degrade vs hard fail).
            logger.info(
                "Maxx mode: %s empty — Claude answers directly (no review)",
                primary_provider,
            )
            if step_hook:
                try:
                    step_hook(f"⚙️ {primary_provider} empty — Claude answering directly…")
                except Exception:
                    pass
            try:
                claude_content = await _call_claude(
                    system=system, user=user,
                    max_tokens=actual_tokens, temperature=temperature,
                )
            except Exception as e:
                return {
                    "ok": False, "provider": None, "content": "",
                    "temperature": temperature, "mode": mode,
                    "review_mode": "maxx",
                    "fallback_chain": [primary_provider, "claude-sonnet"],
                    "maxx_capped":    maxx_capped,
                    "maxx_overage":   maxx_overage,
                    "maxx_remaining": maxx_remaining,
                    "error": f"{primary_provider} empty and Claude failed: {e}",
                }
            return {
                "ok":             bool(claude_content.strip()),
                "provider":       "claude-sonnet-maxx-direct",
                "content":        claude_content,
                "temperature":    temperature,
                "mode":           mode,
                "review_mode":    "maxx",
                "model":          _CLAUDE_MODEL,
                "fallback_chain": [primary_provider, "claude-sonnet"],
                "maxx_capped":    maxx_capped,
                "maxx_overage":   maxx_overage,
                "maxx_remaining": maxx_remaining,
            }

        if step_hook:
            try:
                step_hook("🔍 Claude reviewing & improving…")
            except Exception:
                pass
        # NB: the original `system` is preserved so Claude keeps the same
        # persona/safety rules. The review instruction lives in the user
        # turn so the orchestrator's tool-call grammar isn't disturbed.
        review_user = (
            "The following is an initial response. Review it for "
            "correctness, hallucinations, and code quality. Improve it "
            "where needed while preserving the same answer structure "
            "(if it contains tool_call code fences, keep them intact). "
            "Return ONLY the improved response — no preamble.\n\n"
            f"---\n{glm_content}\n---"
        )
        try:
            claude_content = await _call_claude(
                system=system, user=review_user,
                max_tokens=actual_tokens, temperature=temperature,
            )
        except Exception as e:
            logger.warning(
                f"Maxx mode: Claude review failed ({e!r}) — returning {primary_provider} draft"
            )
            return {
                "ok":             bool(glm_content.strip()),
                "provider":       f"{primary_provider}-no-review",
                "content":        glm_content,
                "temperature":    temperature,
                "mode":           mode,
                "review_mode":    "maxx",
                "model":          primary_model_id,
                "fallback_chain": [primary_provider],
                "maxx_capped":    maxx_capped,
                "maxx_overage":   maxx_overage,
                "maxx_remaining": maxx_remaining,
                "error": f"Claude review unavailable: {e}",
            }
        if not claude_content.strip():
            # Claude returned empty — keep the primary draft, never blank-ship.
            return {
                "ok":             True,
                "provider":       f"{primary_provider}-no-review",
                "content":        glm_content,
                "temperature":    temperature,
                "mode":           mode,
                "review_mode":    "maxx",
                "model":          primary_model_id,
                "fallback_chain": [primary_provider],
                "maxx_capped":    maxx_capped,
                "maxx_overage":   maxx_overage,
                "maxx_remaining": maxx_remaining,
            }
        # Count this Maxx call against the user's monthly quota.
        if user_id:
            try:
                from services.usage import incr_maxx_usage, get_maxx_usage as _u
                await incr_maxx_usage(user_id)
                fresh = await _u(user_id)
                maxx_remaining = fresh.get("remaining")
            except Exception as e:
                logger.warning(f"maxx counter incr failed: {e!r}")
        return {
            "ok":             True,
            "provider":       f"{primary_provider}+claude-review",
            "content":        claude_content,
            "temperature":    temperature,
            "mode":           mode,
            "review_mode":    "maxx",
            "model":          _CLAUDE_MODEL,
            "fallback_chain": [primary_provider, "claude-sonnet-review"],
            "maxx_capped":    maxx_capped,
            "maxx_overage":   maxx_overage,
            "maxx_remaining": maxx_remaining,
        }

    # ── Iter 212m-159 — Council B "analysis" mode routing ──────────────
    # When COUNCIL_B_GLM_ENABLED, analysis primary = GLM-5.2 (reasoning
    # model) with DeepSeek V3 rescue.  When the flag is OFF, behaves
    # identically to mode="chat" (DeepSeek only) so Council B falls
    # back to legacy behaviour with zero diff.
    if mode == "analysis":
        if not COUNCIL_B_GLM_ENABLED:
            # Pre-V2 behaviour: just DeepSeek (same as mode="chat" path
            # below — fall through by rebranding mode for the legacy
            # selector).
            mode = "chat"
        else:
            try:
                glm_content = await _call_glm(
                    system=system, user=user,
                    max_tokens=actual_tokens, temperature=temperature,
                )
            except Exception as e:
                logger.warning(f"Council B GLM-5.2 raised: {e!r} — using DeepSeek rescue")
                glm_content = ""
            if glm_content.strip():
                return {
                    "ok":             True,
                    "provider":       "glm-5.2",
                    "content":        glm_content,
                    "temperature":    temperature,
                    "mode":           "analysis",
                    "model":          _GLM_MODEL,
                    "fallback_chain": ["glm-5.2"],
                    "maxx_capped":    False,
                    "maxx_overage":   False,
                    "maxx_remaining": None,
                }
            # GLM empty/failure → DeepSeek rescue
            logger.info("Council B: GLM-5.2 empty — falling back to DeepSeek V3")
            try:
                ds_content = await _call_deepseek(
                    messages=[{"role": "user", "content": user}],
                    system=system,
                    max_tokens=actual_tokens, temperature=temperature,
                )
            except Exception as e:
                logger.error(f"Council B: both GLM and DeepSeek failed: {e!r}")
                return {
                    "ok": False, "provider": None, "content": "",
                    "temperature": temperature, "mode": "analysis",
                    "fallback_chain": ["glm-5.2", "deepseek-v3"],
                    "maxx_capped": False, "maxx_overage": False,
                    "maxx_remaining": None,
                    "error": f"Both GLM-5.2 and DeepSeek unavailable: {e}",
                }
            return {
                "ok":             bool(ds_content.strip()),
                "provider":       "deepseek-v3-council-b-rescue",
                "content":        ds_content,
                "temperature":    temperature,
                "mode":           "analysis",
                "model":          _deepseek_model(),
                "fallback_chain": ["glm-5.2", "deepseek-v3"],
                "maxx_capped":    False,
                "maxx_overage":   False,
                "maxx_remaining": None,
            }

    # ── Iter 212m-165 — Council C "write" mode routing ─────────────────
    # Writing tasks (email/copy/draft) → DeepSeek primary, no GLM
    # rescue.  DeepSeek is both cheaper and a better fit for prose
    # than GLM-5.2 (reasoning-heavy).  If DeepSeek fails the call
    # falls back to the legacy DeepSeek + free-OpenRouter walk that
    # `_call_deepseek` already implements, so no extra rescue layer
    # is needed at this level.
    if mode == "write":
        try:
            content = await _call_deepseek(
                messages=[{"role": "user", "content": user}],
                system=system,
                max_tokens=actual_tokens, temperature=temperature,
            )
        except Exception as e:
            logger.error(f"Council C: DeepSeek write failed: {e!r}")
            return {
                "ok": False, "provider": None, "content": "",
                "temperature": temperature, "mode": "write",
                "fallback_chain": ["deepseek-v3"],
                "maxx_capped": False, "maxx_overage": False,
                "maxx_remaining": None,
                "error": f"DeepSeek unavailable: {e}",
            }
        return {
            "ok":             bool((content or "").strip()),
            "provider":       "deepseek-v3-council-c",
            "content":        content or "",
            "temperature":    temperature,
            "mode":           "write",
            "model":          _deepseek_model(),
            "fallback_chain": ["deepseek-v3"],
            "maxx_capped":    False,
            "maxx_overage":   False,
            "maxx_remaining": None,
        }

    # ── Legacy mode routing (unchanged) ─────────────────────────────────
    wants_claude = mode in _CLAUDE_MODES and bool(_openrouter_key())

    # ── Iter 94/101: Maxx-mode budget gate + overage tracking ────────
    # 100-task included monthly. Past that:
    #   • Pro tier: KEEP using Claude (don't degrade UX), track overage
    #     for end-of-month $0.50/task invoice.
    #   • Free/Starter: fall back to DeepSeek (tier has 0 included).
    maxx_capped     = False        # legacy field — true only when we degraded
    maxx_overage    = False        # iter 101 — true when this call is billable overage
    maxx_remaining: Optional[int] = None
    if wants_claude and user_id:
        try:
            from services.usage import get_maxx_usage
            u = await get_maxx_usage(user_id)
            maxx_remaining = u.get("remaining")
            if u.get("capped"):
                tier = u.get("tier", "free")
                if tier in ("pro", "team", "founder"):
                    # Pro+: keep Claude, charge overage (real billing impact).
                    maxx_overage = True
                else:
                    # Free/Starter: degrade to DeepSeek (zero overage policy).
                    maxx_capped = True
                    wants_claude = False
        except Exception as e:
            # Never block on the meter — fall through to whatever was
            # planned. Maxx-cap is a soft commercial guard, not a
            # hard correctness gate.
            logger.warning(f"maxx budget check failed (allowing): {e!r}")

    use_claude = wants_claude
    provider_name = "claude-sonnet-openrouter" if use_claude else "deepseek"

    try:
        if use_claude:
            content = await _call_claude(system, user, actual_tokens, temperature)
            # Count the Claude call against the user's monthly Maxx quota.
            if user_id:
                try:
                    from services.usage import incr_maxx_usage, get_maxx_usage as _u
                    await incr_maxx_usage(user_id)
                    # Recompute remaining so the UI can show "97 left"
                    # without a second DB hit.
                    fresh = await _u(user_id)
                    maxx_remaining = fresh.get("remaining")
                except Exception as e:
                    logger.warning(f"maxx counter incr failed: {e!r}")
        else:
            content = await _call_deepseek(
                messages=[{"role": "user", "content": user}],
                system=system,
                max_tokens=actual_tokens,
                temperature=temperature,
            )
        return {
            "ok":           True,
            "provider":     provider_name,
            "content":      content,
            "temperature":  temperature,
            "mode":         mode,
            "fallback_chain": [provider_name],
            "maxx_capped":    maxx_capped,
            "maxx_overage":   maxx_overage,
            "maxx_remaining": maxx_remaining,
        }
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        logger.error(f"LLM HTTP {status}: {e.response.text[:300]}")
        # Iter 124 — surface a friendly, specific message for rate limits
        # so the UI doesn't say a generic 'API rate limits' line.
        if status == 429:
            err_msg = ("Upstream model is rate-limited right now — I retried "
                       "but couldn't get a slot. Try again in ~10 seconds.")
        elif status in (502, 503, 504):
            err_msg = (f"Upstream model is briefly unavailable (HTTP {status}) "
                       "— try again in a moment.")
        else:
            err_msg = f"LLM unavailable (HTTP {status})"
        return {
            "ok": False, "provider": None, "content": "",
            "temperature": temperature, "mode": mode,
            "fallback_chain": [provider_name],
            "maxx_capped":    maxx_capped,
            "maxx_overage":   maxx_overage,
            "maxx_remaining": maxx_remaining,
            "error": err_msg,
        }
    except Exception as e:
        logger.error(f"LLM call failed: {e!r}")
        return {
            "ok": False, "provider": None, "content": "",
            "temperature": temperature, "mode": mode,
            "fallback_chain": [provider_name],
            "maxx_capped":    maxx_capped,
            "maxx_overage":   maxx_overage,
            "maxx_remaining": maxx_remaining,
            "error": f"LLM unavailable: {e}",
        }
