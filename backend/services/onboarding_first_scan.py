"""services/onboarding_first_scan.py — Onboarding Step 4 · S-B (2026-08-26).

The first-scan aha. Fired once per USER (the aha is for their FIRST
repo only — dedup via `dev_users.first_scan_at`, the same pattern
already used for `first_chat_at`/`first_loop_at` elsewhere in this
codebase), immediately after a project is successfully connected.

Reuses `services.seo.orchestrator.run_seo_fixes()` directly — NOT
`routers.founder_offer`'s claim/confirm flow, which is coupled to the
promotional "500 spots" counter (a marketing campaign, not a product
feature). The onboarding aha and the founder's promo share the ENGINE
(`run_seo_fixes`) but have separate business logic, per founder
decision (c). `routers/founder_offer.py` is untouched.

Cheap tier only (C6): `alt_provider` is set to the existing
deterministic filename-based fallback (`services.seo.image_alts.
_fallback_from_src`) instead of the default LLM alt-text generator —
no LLM calls in the first scan.

Deterministic patches only. If `run_seo_fixes` ever generates
LLM-written content (e.g. a custom per-page meta description instead
of a default), THIS FLOW MUST BE ROUTED THROUGH THE cto_tasks
Plan->Build->Verify pipeline (C1) — a deterministic-patch-only
approval preview is not sufficient for LLM-generated code/content.
Do not silently keep using this direct-commit path if that changes.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

STATUS_SCANNING = "scanning"
STATUS_READY = "ready"
STATUS_CLEAN = "clean"
STATUS_SKIPPED = "skipped"
STATUS_ERROR = "error"

_STILL_SCANNING_AFTER_S = 15.0
_TOP_N_CARDS = 5


async def _alt_provider(src: str, _page_context: str) -> str:
    from services.seo.image_alts import _fallback_from_src
    return _fallback_from_src(src)


def _default_title_description(proj: dict) -> tuple[str, str]:
    """The auto-triggered scan has no user-supplied title/description
    (that's a founder_offer.py claim-flow input, not something we have
    at connect time) — without SOME value, `patch_meta_tags` has
    nothing to inject and the "missing meta description" finding
    would never actually be true. Derive a reasonable default from
    the repo name, same spirit as the repo-name-derived commit
    identity already used elsewhere in this codebase."""
    repo = (proj.get("github_repo") or "your site").replace("-", " ").replace("_", " ").strip()
    title = repo.title() or "Home"
    return title, f"{title} — built and maintained with AUREM."


async def trigger_first_scan(*, db, user_id: str, project_id: str) -> None:
    """Fire-and-forget. Never raises — same error-swallowing contract
    as `services/project_onboarding_scan.py::run_onboarding_scan`
    (the sibling background scan already wired at this exact call
    site for the Prompt Starter panel)."""
    try:
        dev_user = await db.dev_users.find_one({"user_id": user_id})
        if dev_user and dev_user.get("first_scan_at"):
            return  # S-B edge case: "second repo" — aha is FIRST repo only.
        await db.dev_users.update_one(
            {"user_id": user_id},
            {"$set": {"first_scan_at": datetime.now(timezone.utc)}},
            upsert=True,
        )

        from services.signup_guards import (
            emit_first_scan_started, emit_first_scan_completed,
        )
        await emit_first_scan_started(db, user_id=user_id, project_id=project_id)
        await db.first_scan_results.update_one(
            {"project_id": project_id},
            {"$set": {"project_id": project_id, "user_id": user_id,
                      "status": STATUS_SCANNING,
                      "started_at": datetime.now(timezone.utc)}},
            upsert=True,
        )

        t0 = time.monotonic()
        proj = await db.cto_projects.find_one({"project_id": project_id}) or {}
        title, description = _default_title_description(proj)
        from services.seo.orchestrator import SeoOptions, run_seo_fixes
        result = await run_seo_fixes(
            user_id=user_id, project_id=project_id,
            options=SeoOptions(plan="swift", dry_run=True,
                               title=title, description=description,
                               alt_provider=_alt_provider),
        )
        duration_ms = round((time.monotonic() - t0) * 1000, 1)

        if not result.get("ok") or result.get("errors"):
            await db.first_scan_results.update_one(
                {"project_id": project_id},
                {"$set": {"status": STATUS_ERROR,
                          "error": "; ".join(result.get("errors") or ["scan failed"])[:300],
                          "completed_at": datetime.now(timezone.utc)}},
            )
            return

        patches = result.get("patches") or []
        # Edge case: non-web repo — robots.txt/sitemap.xml are created
        # unconditionally by run_seo_fixes() regardless of whether any
        # HTML page exists (orchestrator.py:204-228 doesn't gate on
        # html_paths). A backend-only repo with zero HTML files would
        # otherwise get a nonsensical "missing robots.txt" finding. If
        # NO patch touched an actual HTML file, this isn't a web repo
        # — drop the robots/sitemap-only patches and fall through to
        # "clean" (which the results endpoint renders as the
        # non-web-repo / empty-repo message, not a fake finding).
        html_touched = any(p.get("path", "").lower().endswith((".html", ".htm"))
                           for p in patches)
        if not html_touched:
            patches = []
        if not patches:
            await db.first_scan_results.update_one(
                {"project_id": project_id},
                {"$set": {"status": STATUS_CLEAN, "findings_count": 0,
                          "completed_at": datetime.now(timezone.utc)}},
            )
            await emit_first_scan_completed(
                db, user_id=user_id, project_id=project_id,
                findings_count=0, scan_duration_ms=duration_ms,
            )
            return

        from services.seo.finding_translator import translate_patches
        cards = translate_patches(patches)
        shown, more = cards[:_TOP_N_CARDS], max(0, len(cards) - _TOP_N_CARDS)

        await db.first_scan_results.update_one(
            {"project_id": project_id},
            {"$set": {"status": STATUS_READY, "cards": shown,
                      "more_count": more, "findings_count": len(cards),
                      "scan_duration_ms": duration_ms,
                      "completed_at": datetime.now(timezone.utc)}},
        )
        await emit_first_scan_completed(
            db, user_id=user_id, project_id=project_id,
            findings_count=len(cards), scan_duration_ms=duration_ms,
            top_category="seo",
        )
    except Exception as e:                                       # noqa: BLE001
        logger.warning("[first-scan] failed for project=%s: %r", project_id, e)
        try:
            await db.first_scan_results.update_one(
                {"project_id": project_id},
                {"$set": {"status": STATUS_ERROR, "error": repr(e)[:300],
                          "completed_at": datetime.now(timezone.utc)}},
                upsert=True,
            )
        except Exception:
            pass


def is_still_scanning_slow(started_at) -> bool:
    if not started_at:
        return False
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - started_at).total_seconds() > _STILL_SCANNING_AFTER_S
