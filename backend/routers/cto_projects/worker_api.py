"""
routers/cto_projects/worker_api.py — AUREM CTO Projects.
API-only task worker: reads target files via GitHub REST, runs AI
codegen (single or parallel agents), hallucination/syntax/lint gates,
Vanguard verify (+ one auto-fix retry), commits atomically via the
GitHub Data API, then post-push verifies + persists rich diff data.

Kept as ONE cohesive ~1200-line module per the founder's explicit
2026-09 CTO-split ruling — this is a single linear pipeline, not
several glued-together responsibilities; splitting it further would
chase a line-count target, not a real seam. No function here exceeds
~350 lines (`_run_task_via_api` is the one long, cohesive pipeline —
same "cohesive > line-count" exception already approved for
`_advisor_panel` in chat/worker.py).

Split from the former monolithic routers/cto_projects.py on
2026-09-08 (responsibility-based extraction, no logic change). Uses
`_pkg.<name>` for anything patched at the package level by the
existing test suite (`get_db`, `call_llm`, `gh_api_commit`,
`gh_api_fetch_file`, `_log`, `_set_status`, `_GIT_AVAILABLE`) — see
preview.py's module docstring for why.
"""
import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

from core.errors import PushFailedError
from services.cto_projects_helpers import (
    _load_design_system, _TRUNCATION_PATTERNS, _retry, _emit,
)

import routers.cto_projects as _pkg
from . import router

logger = logging.getLogger(__name__)


async def _run_task(task_id, proj, task, files, context, user_token,
                     maxx_mode: bool = False, resume_edits: Optional[dict] = None):
    """Dispatcher — picks the `git`-binary worker when available,
    else falls back to the pure-API worker. Always via `_pkg.*` so
    tests that patch `routers.cto_projects._run_task_with_git` /
    `_run_task_via_api` correctly intercept the call regardless of
    which submodule this dispatcher itself lives in."""
    if _pkg._GIT_AVAILABLE:
        return await _pkg._run_task_with_git(
            task_id, proj, task, files, context, user_token, maxx_mode, resume_edits,
        )
    return await _pkg._run_task_via_api(
        task_id, proj, task, files, context, user_token, maxx_mode, resume_edits,
    )


def _frontend_subset(edits: dict[str, str]) -> dict[str, str]:
    """Pick the files we persist on the task doc for the right-side
    `<PreviewPane />` to render after ship.

    Iter 169 — expanded beyond pure frontend files. Previously only
    html/css/js/jsx/ts/tsx were kept, which meant a backend-only ship
    (e.g. a Python services edit) left the user's "</> Code" button
    pointing at literally nothing. Now we also keep `.py`, `.json`,
    `.yaml`, `.yml`, `.md`, `.sql`, `.sh`, `.toml`, and `.env.example`
    so backend ships show their actual code, not just the live URL.

    Cap: 12 files × 32 KB each = ~384 KB max stored per task. Anything
    bigger gets dropped (the user can still view the live URL)."""
    out: dict[str, str] = {}
    _ALLOWED_EXT = (
        ".html", ".css", ".js", ".jsx", ".ts", ".tsx",
        ".py", ".json", ".yaml", ".yml", ".md",
        ".sql", ".sh", ".toml",
    )
    for path, body in (edits or {}).items():
        if not isinstance(body, str):
            continue
        path_l = path.lower()
        if path_l.endswith(".env.example"):
            pass  # explicit allow
        elif not path_l.endswith(_ALLOWED_EXT):
            continue
        if len(body) > 32_000:
            continue
        out[path] = body
        if len(out) >= 12:
            break
    return out


# ── Models ───────────────────────────────────────────────────────────────
_AI_SYS = (
    "You are AUREM — a senior engineer who SHIPS production-grade code.\n"
    "\n"
    "OUTPUT CONTRACT (NON-NEGOTIABLE):\n"
    "  Line 1 must be exactly:  SUMMARY: <one line, <=120 chars>\n"
    "  Then, for EVERY file you change, output:\n"
    "    FILE: <relative/path/from/repo/root>\n"
    "    ```\n"
    "    <COMPLETE final file content — every single line, top to bottom>\n"
    "    ```\n"
    "  Use as many FILE blocks as needed. Nothing else outside SUMMARY + FILE blocks.\n"
    "\n"
    "HARD RULES — violations cause the commit to be REJECTED by the verifier:\n"
    "  1. Each FILE block MUST contain the complete final file, not a diff,\n"
    "     not a patch, not a snippet, not ellipses. Write every line, every\n"
    "     import, every closing brace, end-to-end.\n"
    "  2. NEVER use placeholder comments like '// ... rest of file ...',\n"
    "     '/* existing code */', '# ... unchanged ...', '... (truncated)',\n"
    "     '<keep the rest>', or any synonym. If you cannot fit the whole\n"
    "     file, split the task — do NOT abbreviate.\n"
    "  3. If editing a file you were shown, preserve everything you did\n"
    "     not intend to change. Copy lines verbatim if needed.\n"
    "  4. Do not invent file paths. Only emit paths that exist in the\n"
    "     context, OR paths you genuinely want to create.\n"
    "  5. Tests, configs and docs that need to change MUST also be emitted\n"
    "     as FILE blocks — do not just describe them in prose.\n"
    "  6. NO prose, NO markdown headings, NO 'Here is the change…' lines\n"
    "     outside the SUMMARY + FILE blocks.\n"
    "\n"
    "QUALITY BAR:\n"
    "  • Match the existing project's conventions (naming, indentation,\n"
    "    quote style, import order) exactly — you were shown those files.\n"
    "  • Prefer minimal, surgical edits over large refactors unless the\n"
    "    task explicitly asks for one.\n"
    "  • If the task is ambiguous, make the most defensible choice and\n"
    "    mention the tradeoff in the SUMMARY line."
)


_AI_SYS = _AI_SYS + _load_design_system()
def _looks_truncated(path: str, body: str) -> Optional[str]:
    """Return a human reason if `body` looks like an AI-truncated edit,
    else None. Run on every FILE block before we push."""
    if not body or not body.strip():
        return "empty file body"
    low = body.lower()
    for pat in _TRUNCATION_PATTERNS:
        if pat.lower() in low:
            return f"contains placeholder '{pat}'"
    # Very short edits to non-trivial files are suspicious too — but we
    # only flag them when the body has fewer than 3 non-blank lines AND
    # the extension suggests code (not config/markdown).
    non_blank = sum(1 for ln in body.splitlines() if ln.strip())
    is_codey = path.endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java"))
    if is_codey and non_blank < 3:
        return f"only {non_blank} non-blank lines for a code file"
    return None
async def _persist_push_failed(task_id: str, e: "PushFailedError") -> str:
    """Ship/Commit Robustness · 2026-08-26 — extracted from the
    `_run_task_via_api` commit try/except so this exact persistence
    logic (real SHA + push_failed=True, never "nothing was
    committed") is independently unit-testable without standing up
    the whole ~1000-line worker. Returns the built error string."""
    err = f"Commit {e.commit_sha[:7]} created but push failed: {e.reason}"
    await _pkg._log(task_id, f"🚫 {err}", "error")
    await _pkg._set_status(task_id, status="failed", error=err[:2000],
                      commit_sha=e.commit_sha, push_failed=True,
                      completed_at=time.time())
    return err
# ─────────────────────────────────────────────────────────────────────
# 2026-09-08 — Phase 2 safety fix (+ follow-up mechanical extraction).
#
# Shared safety-critical pipeline stages, used by BOTH
# `_run_task_via_api` and `_run_task_with_git`. Audit finding: the
# git-binary worker had NONE of the hallucination/syntax/lint gates
# the API worker had, even though both had the sensitive-path guard
# and Vanguard verify — closed by making both workers call these same
# gates in the same order. The commit MECHANISM (GitHub Data API vs
# `git` binary) is intentionally left different — only the safety
# STAGES are unified. The gates themselves now live in
# `services/cto_pipeline_steps.py` (moved verbatim, zero logic change)
# so this file doesn't carry their ~230 lines directly.
# ─────────────────────────────────────────────────────────────────────
from services.cto_pipeline_steps import (
    _check_js_syntax, _syntax_errors,
    _run_hallucination_gate, _run_syntax_gate, _run_lint_gate,
)


async def _run_task_via_api(task_id, proj, task, files, context, user_token, maxx_mode: bool = False,
                            resume_edits: Optional[dict] = None):
    """API-only worker — no `git` binary needed. Reads target files from
    GitHub, asks AUREM to generate edits, then commits everything as ONE
    atomic commit via the Git Data API."""
    import httpx
    owner = proj["github_owner"]
    repo = proj["github_repo"]
    branch = proj.get("branch", "main")
    # H3 hardening (2026-08-30, overnight-loop-2 P0) — pin {owner, repo,
    # branch, installation_id} the moment this worker starts (this
    # function runs the whole task — LLM generation + edit application
    # — before it ever writes to GitHub; a project's binding could
    # theoretically change underneath it during that window). Re-
    # asserted right before the real commit below.
    _pin_owner, _pin_repo, _pin_branch = owner, repo, branch
    _pin_installation_id = proj.get("installation_id")
    _pin_project_id = proj.get("project_id")
    _pin_user_id = proj.get("user_id")
    if not user_token:
        await _pkg._set_status(task_id, status="failed",
                          error="No PAT on project — open Edit and add one",
                          completed_at=time.time())
        return

    # Resolve the project owner's current tier ONCE — drives every
    # feature gate downstream (parallel agents, priority queue, etc.).
    user_tier = "free"
    try:
        _db_for_tier = _pkg.get_db()
        if _db_for_tier is not None:
            _u = await _db_for_tier.dev_users.find_one(
                {"user_id": proj.get("user_id")}, {"tier": 1},
            )
            if _u and _u.get("tier"):
                user_tier = _u["tier"]
    except Exception:
        pass

    try:
        await _pkg._set_status(task_id, status="pulling", started_at=time.time())
        await _emit(task_id, "Reading repository files…", kind="phase_read", pct=10)
        await _pkg._log(task_id, f"📡 Reading {owner}/{repo}@{branch} via API…")

        # 1) Read target files (or auto-pick a few likely ones) IN PARALLEL
        await _pkg._set_status(task_id, status="reading")
        target_files = list(files or [])
        if not target_files:
            # Iter 212m-177 — P0-4a: the task text usually NAMES the file
            # ("…in backend/utils/auth.py"). Read THOSE files first —
            # guessing main.py/README.md fed the model zero real context
            # and it hallucinated file content from thin air.
            _mentioned = re.findall(
                r"[\w./\\-]+\.(?:py|jsx?|tsx?|css|json|md|html|yml|yaml|toml|go|rs|java|rb)",
                f"{task}\n{context or ''}",
            )
            target_files = list(dict.fromkeys(_mentioned))
        if not target_files:
            target_files = [
                "main.py", "app.py", "server.py", "index.html",
                "src/App.jsx", "src/main.jsx", "pages/index.js",
                "README.md",
            ]
        fetched = await asyncio.gather(*[
            _pkg.gh_api_fetch_file(owner, repo, f, branch, user_token)
            for f in target_files[:8]
        ])
        contents: dict = {}
        for path, body in zip(target_files[:8], fetched):
            if body is not None:
                contents[path] = body[:10000]
                await _pkg._log(task_id, f"📄 read {path}")
                # When user didn't specify files, keep first 4 hits
                if not files and len(contents) >= 4:
                    break
        # iter 114 — persist files_read for the live popup
        try:
            from services.task_diff import build_files_read
            await _pkg._set_status(task_id, files_read=build_files_read(contents))
        except Exception:
            pass

        # 2) AI codegen — augment the user message with the cached
        # repo index so AUREM sees the project's overall shape, not
        # just the 4-8 explicitly-fetched files. Falls back silently
        # if no index has been built yet for this project.
        await _pkg._set_status(task_id, status="fixing")
        try:
            from services.codebase_indexer import build_context_block
            repo_block = await build_context_block(
                proj.get("user_id", ""), proj.get("project_id", ""),
                max_chars=4500,
            )
            if repo_block:
                await _pkg._log(task_id, "🗂️ injected cached repo index")
        except Exception as _e:
            repo_block = None
        # iter 41 — Brain + Issues context (zero LLM cost, ~350 tokens)
        # iter 169 — switched from V1 (project_brains) to V2
        # (project_brains_v2). V1 retired — same call shape, denser
        # context format via format_brain_for_agent().
        brain_ctx = ""
        issues_ctx = ""
        try:
            from services.project_brain import (
                get_brain_v2, format_brain_for_agent,
            )
            _db = _pkg.get_db()
            if _db is not None:
                _brain = await get_brain_v2(
                    _db, proj.get("project_id", ""), proj.get("user_id"),
                )
                if _brain:
                    brain_ctx = format_brain_for_agent(_brain)
        except Exception as _e:
            brain_ctx = ""
        try:
            from services.github_issues_context import get_relevant_issues_context
            _db = _pkg.get_db()
            if _db is not None and user_token:
                issues_ctx = await get_relevant_issues_context(
                    db=_db, repo_owner=owner, repo_name=repo,
                    github_pat=user_token, task_description=task,
                )
        except Exception:
            issues_ctx = ""
        if brain_ctx:
            await _pkg._log(task_id, "🧠 injected project memory")
        if issues_ctx:
            await _pkg._log(task_id, "📋 injected relevant GitHub issues")

        await _emit(task_id, "ORA thinking…", kind="phase_think", pct=30)
        await _pkg._log(task_id, "🧠 DeepSeek thinking…")
        files_blob = "\n\n".join(
            f"FILE: {p}\n```\n{c}\n```" for p, c in contents.items()
        )
        extra_context_block = ""
        if brain_ctx:
            extra_context_block += f"\n\n[PROJECT MEMORY]\n{brain_ctx}"
        if issues_ctx:
            extra_context_block += f"\n\n[OPEN ISSUES]\n{issues_ctx}"
        # iter 44 — Vanguard skill injection. Pre-warms the AI with
        # battle-tested security patterns matching the task type
        # (auth/api/payments/react/backend). Zero LLM cost.
        try:
            from services.skill_context_injector import build_skill_context
            sk_ctx = build_skill_context(task)
            if sk_ctx:
                extra_context_block += f"\n\n{sk_ctx}"
                await _pkg._log(task_id, "🛡️ Applying security best practices for this task")
        except Exception:
            pass
        # Multi-file task detection — tells ORA to ship everything in
        # one turn instead of stopping after file 1 with "Next:".  Keyword
        # heuristic; the persona itself handles the ≥3-file checklist.
        _multi_file_keywords = (
            "all ", "every ", "each ", "multiple", "scaffold",
            "workers", "pillar", "4 files", "5 files", "3 files",
            "all files", "complete", "full implementation",
        )
        _is_multi = any(kw in task.lower() for kw in _multi_file_keywords)
        _multi_file_instruction = ""
        _promised_files: set[str] = set()
        if _is_multi:
            _multi_file_instruction = (
                "\n\nMULTI-FILE TASK DETECTED: You MUST generate ALL required "
                "files in this single response. Do NOT stop after the first "
                "file. Do NOT say 'Next:' or 'Reply to continue'. Use the "
                "checklist format: [ ] file → [x] done. Ship the complete "
                "implementation in one commit."
            )
            # Structural multi-file contract — extract every concrete file
            # path mentioned in the task/context.  If the LLM later returns
            # an `edits` dict missing any of these, we auto-retry with a
            # very specific "you promised N files, only M arrived" nudge.
            import re as _refm
            _promised_files = set(_refm.findall(
                r"[\w./-]+\.(?:py|jsx?|tsx?|css|json|md|html|yml|yaml)",
                f"{task}\n{context or ''}",
            ))
            if _promised_files:
                db_for_plan = _pkg.get_db()
                if db_for_plan is not None:
                    _plan = [{"file": f, "status": "pending"}
                             for f in sorted(_promised_files)[:12]]
                    await db_for_plan.cto_tasks.update_one(
                        {"task_id": task_id},
                        {"$set": {"task_plan": _plan}},
                    )
                    await _emit(task_id, f"Plan: {len(_plan)} files",
                                kind="task_plan", plan=_plan, pct=18)

        user_msg = (
            f"TASK: {task}\n"
            f"{('CONTEXT: ' + context) if context else ''}\n\n"
            f"Tech: {proj.get('tech_stack','auto')}\n\n"
            f"{repo_block or ''}{extra_context_block}\n\n{files_blob}"
            f"{_multi_file_instruction}"
        )

        # iter 43 — Parallel multi-agent codegen for big tasks.
        # `should_parallelize()` decides automatically based on task scope
        # AND file tree. Single-file or small tasks fall through to the
        # existing single-call path below (which keeps SUMMARY parsing).
        edits: dict[str, str] = {}
        summary = "AI changes"
        parallelized = False
        agents_count = 1
        if resume_edits and resume_edits.get("edits"):
            # 2026-08-27 — checkpoint/resume Phase 2: skip regeneration,
            # reuse the saved edits from the attempt that crashed/failed
            # AFTER generation succeeded but BEFORE the commit.
            edits = dict(resume_edits["edits"])
            summary = resume_edits.get("summary", "AI changes")
            await _pkg._log(
                task_id,
                f"♻️ Reusing {len(edits)} saved file edit(s) from the "
                f"previous attempt — skipping AI regeneration",
                "success",
            )
            await _pkg._set_status(task_id, tokens_used=0,
                              agent_used="resumed_from_checkpoint")
        else:
            try:
                from services.parallel_agents import (
                    should_parallelize, run_parallel_agents, decompose_task,
                )
                from services.subscription_tiers import can_use_feature
                file_tree_hint = list(contents.keys()) + (files or [])
                # Parallel agents are a Pro feature — Free / Starter fall
                # through to the single-agent path (no error, just slower).
                _parallel_allowed = can_use_feature(user_tier, "parallel_agents")
                if should_parallelize(task, file_tree_hint) and _parallel_allowed:
                    # Pre-decompose so we know which agents are about to fire
                    # — that lets the chat bubble render the badges + per-agent
                    # mini progress bars BEFORE the LLM round-trip resolves.
                    _agents_preview = decompose_task(task, f"{owner}/{repo}@{branch}", file_tree_hint)
                    _agent_roles = [a.get("role", "agent") for a in _agents_preview]
                    await _emit(
                        task_id,
                        f"Parallel mode — {len(_agent_roles)} agents working simultaneously",
                        kind="parallel", pct=30,
                        agents=[r.title() for r in _agent_roles],
                    )
                    await _pkg._log(task_id, "⚡ Task is multi-domain — splitting into parallel agents")
                    gen_result = await run_parallel_agents(
                        task_description=user_msg,
                        repo_ctx=f"{owner}/{repo}@{branch}",
                        file_tree=file_tree_hint,
                    )
                    edits = gen_result.get("file_blocks", {}) or {}
                    parallelized = bool(gen_result.get("parallelized"))
                    agents_count = int(gen_result.get("agents_used", 1))
                    if parallelized:
                        # Fan out one terminal frame per agent so the per-agent
                        # mini-bars can settle to ✓ / ✕ in the UI.
                        for r in gen_result.get("agent_results", []):
                            ok = not r.get("error")
                            await _emit(
                                task_id,
                                f"{r.get('role','agent').title()} agent {'done' if ok else 'failed'}",
                                kind="parallel_agent",
                                role=r.get("role", "agent").title(),
                                ok=ok,
                            )
                    if parallelized and edits:
                        summary = f"Parallel codegen ({agents_count} agents) — {task[:120]}"
                        await _pkg._log(task_id,
                                   f"✅ {agents_count} agents merged {len(edits)} file edits",
                                   "success")
            except Exception as _pe:
                from services.error_classifier import classify_error
                _pe_safe = classify_error(_pe)["user_message"]
                await _pkg._log(task_id, f"parallel codegen fell back to single agent: {_pe_safe}", "warning")
                edits = {}
                parallelized = False
                agents_count = 1

            if not edits:
                # Single-agent legacy path — unchanged behaviour for small tasks
                # and as fallback when parallel returned empty.
                reply = await _retry(
                    lambda: _pkg.call_llm(
                        messages=[{"role": "user", "content": user_msg}],
                        system=_AI_SYS, max_tokens=3500, temperature=0.0,
                    ),
                    what="AI codegen", task_id=task_id,
                )
                # Coarse token estimate (chars/4) so P&L has real numbers
                approx_in = (len(_AI_SYS) + len(user_msg)) // 4
                approx_out = len(reply or "") // 4
                await _pkg._set_status(
                    task_id,
                    tokens_used=approx_in + approx_out,
                    agent_used="deepseek",
                )
                summary_m = re.search(r"SUMMARY:\s*(.+)", reply)
                summary = (summary_m.group(1).strip() if summary_m else "AI changes")[:300]
                # Iter 212m-33 — tolerant FILE-block parser (was a rigid
                # single-line regex that silently dropped edits whenever
                # the model deviated by even one whitespace).
                from services.llm_file_parser import parse_file_blocks
                edits.update(parse_file_blocks(reply))
            else:
                # Parallel path produced edits — record token-equivalent + agent name
                await _pkg._set_status(
                    task_id,
                    tokens_used=(len(_AI_SYS) + len(user_msg)) // 4
                                + sum(len(c) for c in edits.values()) // 4,
                    agent_used=f"deepseek-parallel-x{agents_count}",
                )

        if not edits:
            # Iter 212m-177 — P0-4b: NEVER report "done" without a real
            # edit. Fall through to the auto-regenerate gate below which
            # retries once with explicit guidance, then fails loudly.
            await _pkg._log(task_id, "⚠️ AI returned no file edits — auto-retrying", "warning")
        else:
            await _emit(task_id, "Writing files…", kind="phase_write", pct=60)
            await _pkg._log(task_id, f"✏️ {len(edits)} files to update", "success")

        # PRE-PUSH GATE — reject AI output that looks truncated. We'd
        # rather fail loudly here than silently push a half-file that
        # later confuses Claude/users when they scan the repo.
        #
        # Before failing, give the model ONE chance to regenerate with
        # explicit guidance about what went wrong (Pattern #1 deep fix).
        # Without this, an empty-body output sent the user into a manual
        # Retry loop that did nothing different.
        async def _truncation_reasons(blocks: dict) -> list[str]:
            out: list[str] = []
            for path, body in blocks.items():
                reason = _looks_truncated(path, body)
                if reason:
                    out.append(f"{path} — {reason}")
            return out

        bad: list[str] = await _truncation_reasons(edits)
        if (not edits) or (bad and len(bad) == len(edits)):
            # No edits OR every edit was rejected — try once more with a
            # nudge. This is in-task auto-regenerate; the user has not
            # clicked Retry. If THIS also fails we surface an actionable
            # error rather than silently looping.
            await _pkg._log(task_id,
                       "no usable file edits — auto-regenerating with explicit guidance",
                       "warning")
            # Iter 212m-6 — include the exact paths that failed and WHY
            # so the model can target its retry. Previously the generic
            # nudge didn't tell the model which files it screwed up, so
            # it often produced the same broken output.
            _path_feedback = ""
            if bad:
                _path_feedback = (
                    "\n\nPRIOR ATTEMPT FAILED ON THESE FILES — fix each one:\n"
                    + "\n".join(f"  - {b}" for b in bad[:10])
                )
            nudge = (
                "Your previous response contained no usable file changes "
                "(empty file body or no FILE blocks).\n"
                "You MUST output complete file content using this exact format:\n"
                "FILE: <path>\n```\n<complete file body — real code, not "
                "a docstring or `pass`>\n```\n"
                "Do NOT just describe what you would do. Write the actual code."
                + _path_feedback
            )
            reply2 = await _retry(
                lambda: _pkg.call_llm(
                    messages=[{"role": "user", "content": user_msg + "\n\n" + nudge}],
                    system=_AI_SYS, max_tokens=3500, temperature=0.0,
                ),
                what="AI codegen auto-retry", task_id=task_id,
            )
            edits = {}
            # Iter 212m-33 — tolerant FILE-block parser (see above).
            from services.llm_file_parser import parse_file_blocks
            edits.update(parse_file_blocks(reply2))
            bad = await _truncation_reasons(edits)

        if bad:
            err = ("AI returned suspect edits (refusing to push):\n  - "
                   + "\n  - ".join(bad)
                   + "\n\nTry rephrasing: specify which file to edit and "
                     "what to change. Example: 'Edit auth.py and add "
                     "rate limiting to the /login endpoint'.")
            await _pkg._log(task_id, f"🚫 {err}", "error")
            await _pkg._set_status(task_id, status="failed", error=err[:2000],
                              completed_at=time.time())
            return
        if not edits:
            # Iter 212m-177 — P0-4b: the auto-retry ALSO produced nothing.
            # Fail clearly; never mark "done" without a state change.
            err = ("AI produced no file edits after a retry — nothing was "
                   "changed. Rephrase the task naming the exact file, e.g. "
                   "'Edit backend/utils/auth.py and add …'.")
            await _pkg._log(task_id, f"🚫 {err}", "error")
            await _pkg._set_status(task_id, status="failed", error=err,
                              completed_at=time.time())
            return

        # 2026-08-27 — sensitive-path guard (real implementation of
        # GUARDS_CHARTER's G3 PROTECTED_PATHS concept, which was speced
        # but never actually built anywhere — see
        # services/sensitive_path_guard.py docstring). Fires BEFORE the
        # hallucination-gate/Vanguard/commit pipeline so a blocked task
        # never pays for the rest of the pipeline. `allow_sensitive_file_change`
        # is read only from the task record — never from LLM output.
        from services.sensitive_path_guard import find_sensitive_paths
        _sensitive_touched = find_sensitive_paths(edits.keys())
        if _sensitive_touched:
            _db_sp = _pkg.get_db()
            _task_row_sp = (await _db_sp.cto_tasks.find_one(
                {"task_id": task_id}, {"allow_sensitive_file_change": 1, "_id": 0},
            ) or {}) if _db_sp is not None else {}
            if not _task_row_sp.get("allow_sensitive_file_change"):
                err = (
                    "Blocked — this task would modify security-sensitive "
                    f"file(s) {_sensitive_touched} (auth / payments / admin / "
                    "CI-workflow naming pattern). These need a human to "
                    "review the diff before shipping. Rephrase to target a "
                    "different file, or confirm explicitly if this change "
                    "is intentional."
                )
                await _pkg._log(task_id, f"⛔ {err}", "error")
                await _pkg._set_status(task_id, status="failed", error=err,
                                  completed_at=time.time())
                return

        # ── Iter 212m-177 — P0-4a HALLUCINATION GATE (pre-push, before
        # Vanguard). Shared gate — see `_run_hallucination_gate` above
        # (also called by `_run_task_with_git`, 2026-09-08 Phase 2 fix).
        edits, _hallu_err = await _run_hallucination_gate(task_id, edits, contents, user_msg, _AI_SYS)
        if _hallu_err:
            await _pkg._log(task_id, f"🚫 {_hallu_err}", "error")
            await _pkg._set_status(task_id, status="failed", error=_hallu_err[:2000],
                              completed_at=time.time())
            return

        await _emit(task_id, "Running linter…", kind="phase_verify", pct=75)
        await _pkg._log(task_id, f"✅ {len(edits)} file{'' if len(edits) == 1 else 's'} "
                             f"generated cleanly", "success")

        # ── Multi-file contract — verify every file the user promised
        # actually arrived. If something is missing we ask the LLM to
        # fill the gap in one targeted retry and merge the result.
        if _is_multi and _promised_files:
            _delivered = {p.lstrip("./") for p in edits.keys()}
            _missing = {f for f in _promised_files
                        if f.lstrip("./") not in _delivered}
            if _missing and len(_missing) <= 4:
                await _emit(task_id,
                            f"Missing files — regenerating ({len(_missing)})…",
                            pct=77)
                await _pkg._log(task_id,
                           f"⚠️ Multi-file contract: missing "
                           f"{', '.join(sorted(_missing))}", "warning")
                _miss_nudge = (
                    "Your previous response was missing these files that "
                    "the task explicitly references:\n  - "
                    + "\n  - ".join(sorted(_missing))
                    + "\n\nGenerate the COMPLETE content for every missing "
                      "file now, in the same FILE: <path>\n```\n…\n``` format. "
                      "Output every missing file in one response — no "
                      "'Next:', no 'Reply to continue'."
                )
                try:
                    fill = await _retry(
                        lambda: _pkg.call_llm(
                            messages=[{"role": "user",
                                       "content": user_msg + "\n\n" + _miss_nudge}],
                            system=_AI_SYS, max_tokens=3500, temperature=0.0,
                        ),
                        what="multi-file contract retry", task_id=task_id,
                    )
                    # Iter 212m-33 — tolerant FILE-block parser.
                    from services.llm_file_parser import parse_file_blocks
                    edits.update(parse_file_blocks(fill))
                except Exception as _fe:
                    logger.warning("multi-file contract retry soft-failed: %r", _fe)

        # ── Syntax validation — catch broken code before it reaches
        # GitHub. Shared gate — see `_run_syntax_gate` above (also
        # called by `_run_task_with_git`, 2026-09-08 Phase 2 fix).
        edits, _syntax_err = await _run_syntax_gate(task_id, edits, user_msg, _AI_SYS)
        if _syntax_err:
            await _pkg._log(task_id, f"🚫 {_syntax_err}", "error")
            await _pkg._set_status(task_id, status="failed", error=_syntax_err[:2000],
                              completed_at=time.time())
            await _emit(task_id, "Syntax error — task failed",
                        kind="fail", pct=100)
            return

        # ── Sandbox validation (e2b) — runs generated Python in an
        # isolated container so ORA can verify its own code before
        # committing. Silently skipped if E2B_API_KEY isn't set.
        try:
            from services.sandbox_runner import validate_generated_files
            _sandbox = await validate_generated_files(edits, task)
            if not _sandbox.get("skipped"):
                if _sandbox.get("ok"):
                    _passed = (
                        _sandbox.get("checks", {})
                                 .get("tests", {})
                                 .get("passed", 0)
                    )
                    if _passed > 0:
                        await _emit(task_id, f"Sandbox tests passed: {_passed} ✓",
                                    pct=80)
                        await _pkg._log(task_id, f"Sandbox: {_passed} tests passed",
                                   "success")
                else:
                    _tout = ""
                    for _cn, _cr in (_sandbox.get("checks") or {}).items():
                        if not _cr.get("ok"):
                            _tout += (_cr.get("output") or _cr.get("stderr") or "")[:500]
                    if _tout:
                        await _pkg._log(task_id,
                                   f"⚠️ Sandbox flagged failures:\n{_tout[:300]}",
                                   "warning")
        except Exception as _se:
            logger.warning("sandbox validation soft-failed: %r", _se)

        # iter 41 — Design Linter (zero LLM cost, pure regex). Shared
        # gate — see `_run_lint_gate` above (also called by
        # `_run_task_with_git`, 2026-09-08 Phase 2 fix).
        edits, lint_result, _lint_err = await _run_lint_gate(task_id, edits)
        if _lint_err:
            await _pkg._set_status(task_id, status="failed", error=_lint_err[:2000],
                              completed_at=time.time())
            # Council log the blocked attempt
            try:
                from services.ora_council_logger import log_code_task as _log_code
                _db = _pkg.get_db()
                if _db is not None:
                    await _log_code(
                        db=_db, user_message=task,
                        repo_context=f"{owner}/{repo}@{branch}",
                        deepseek_draft=str(edits)[:2000],
                        final_output="[BLOCKED BY LINTER]",
                        correction_applied=False, pass_result=False,
                        lint_blocked=True,
                        lint_issues=lint_result.get("issues", []),
                        task_id=task_id, user_id=proj.get("user_id"),
                        project_id=proj.get("project_id"),
                        maxx_mode=maxx_mode,
                    )
            except Exception:
                pass
            return

        # iter 111 — VANGUARD VERIFY AGENT (separate-agent security pass)
        # ────────────────────────────────────────────────────────────
        # After ORA writes code but BEFORE we commit, run a SECOND
        # independent agent (Claude Sonnet 4.5 via Emergent LLM key) that
        # re-audits the patch for vulnerabilities. Plus if the patch
        # contains executable Python, smoke-import it inside E2B so we
        # catch SyntaxError / ImportError / NameError that the regex AST
        # check can't see. Architecture mirrors Anthropic's
        # defending-code-reference-harness "find → grader → judge"
        # pattern. Both passes must succeed for the commit to proceed.
        try:
            await _pkg._log(task_id, "🛡️ Vanguard verify agent reviewing patch…")
            from services.vanguard_verify_agent import verify_patch
            # Iter 212m-42 — derive the active mode from the task envelope
            # so the per-mode Vanguard config (set from /admin/vanguard)
            # can apply the right severity threshold per Swift / Pro / Maxx.
            # We only carry the maxx_mode boolean at this layer; treat
            # everything else as Swift (the safest, strictest default).
            _vg_mode = "maxx" if maxx_mode else "swift"
            # Iter 212m-132 — Diff-aware verify: pass `contents`
            # (the pre-edit content we fetched from GitHub at the
            # READ phase above) as `base_blocks` so Vanguard only
            # flags vulns on lines the patch ACTUALLY added or
            # modified.  Pre-existing issues in untouched lines are
            # surfaced in `verify_result.regex.skipped_preexisting`
            # for audit but do NOT block the commit.
            verify_result = await verify_patch(
                edits, repo_ctx=f"{owner}/{repo}@{branch}",
                mode=_vg_mode, base_blocks=contents,
            )
            await _pkg._log(task_id, f"🛡️ Verify: {verify_result['summary']}",
                       "info" if verify_result["pass"] else "error")
            if not verify_result["pass"]:
                # 2026-08-25 — Priority 1 (customer-driven correction,
                # PRD "Mode D auto-fix" investigation): a Vanguard/E2B
                # block on the CUSTOMER'S OWN generated code is exactly
                # the class of failure AUREM's agent genuinely CAN
                # diagnose and fix (unlike infra-level bugs in AUREM's
                # own backend, which are out of this agent's
                # jurisdiction — see PRD). ONE automatic self-
                # correction pass: feed the LLM the EXACT findings,
                # regenerate only the affected files, re-verify. Ship
                # if it now passes; otherwise fail exactly as before
                # (translated, never raw), noting the attempt.
                _findings_for_fix = verify_result.get("findings", []) or []
                _e2b_pre = verify_result.get("e2b") or {}
                _fix_lines = [
                    f"- {f.get('file','?')}:{f.get('line','?')} "
                    f"[{f.get('severity','?')}] {f.get('rule', f.get('name','issue'))}: "
                    f"{f.get('message','')[:200]}"
                    for f in _findings_for_fix[:10]
                ]
                if not _e2b_pre.get("pass", True) and not _e2b_pre.get("skipped", True):
                    _fix_lines.append(
                        f"- E2B smoke-import failed: {(_e2b_pre.get('stderr') or '')[:300]}"
                    )
                await _pkg._log(
                    task_id,
                    "🔧 Vanguard/E2B blocked the commit — attempting one "
                    "automatic fix before failing…",
                    "warning",
                )
                _vg_nudge = (
                    "The previous version of your patch was BLOCKED by a "
                    "security/quality review with these EXACT findings:\n"
                    + "\n".join(_fix_lines)
                    + "\n\nFix EVERY finding above. Output the COMPLETE "
                      "corrected file(s) using the same FILE: <path>\n```\n…\n``` "
                      "format. Only re-output files that need the fix."
                )
                verify_result_2 = verify_result
                try:
                    _vg_reply = await _retry(
                        lambda: _pkg.call_llm(
                            messages=[{"role": "user",
                                       "content": user_msg + "\n\n" + _vg_nudge}],
                            system=_AI_SYS, max_tokens=3500, temperature=0.0,
                        ),
                        what="Vanguard auto-fix retry", task_id=task_id,
                    )
                    from services.llm_file_parser import parse_file_blocks as _pfb2
                    _vg_edits = _pfb2(_vg_reply)
                    if _vg_edits:
                        edits.update(_vg_edits)
                        verify_result_2 = await verify_patch(
                            edits, repo_ctx=f"{owner}/{repo}@{branch}",
                            mode=_vg_mode, base_blocks=contents,
                        )
                except Exception as _vgfe:
                    logger.warning("Vanguard auto-fix attempt crashed: %r", _vgfe)

                if verify_result_2["pass"]:
                    await _pkg._log(
                        task_id,
                        "✅ Auto-fix resolved the blocked finding(s) — "
                        "re-verified clean, proceeding to commit.",
                        "success",
                    )
                    verify_result = verify_result_2
                else:
                    # Still blocked after a genuine fix attempt — fail,
                    # same as before, but note the attempt happened.
                    try:
                        from services.vanguard_audit import log_blocked_commit
                        _db = _pkg.get_db()
                        if _db is not None:
                            await log_blocked_commit(
                                _db,
                                user_id=str(proj.get("user_id") or "unknown"),
                                project=f"{owner}/{repo}@{branch}",
                                verify_result=verify_result_2,
                                project_id=str(proj.get("project_id")) if proj.get("project_id") else None,
                                task_id=task_id,
                            )
                    except Exception as _ae:
                        logger.warning("vanguard_audit log failed: %r", _ae)
                    # Surface up to 5 critical/high findings in the log
                    critical = [f for f in verify_result_2.get("findings", [])
                                 if f.get("severity") in ("CRITICAL", "HIGH")][:5]
                    for f in critical:
                        await _pkg._log(
                            task_id,
                            f"  • [{f.get('severity')}] {f.get('file','?')}"
                            f":{f.get('line','?')} — {f.get('rule', f.get('name','issue'))}"
                            f" — {f.get('message','')[:120]}",
                            "error",
                        )
                    await _pkg._set_status(
                        task_id, status="failed",
                        error=("Vanguard verify agent blocked commit "
                               "(auto-fix attempted, still blocked):\n"
                               + verify_result_2.get("summary", ""))[:2000],
                        completed_at=time.time(),
                    )
                    return
        except Exception as _ve:
            # Verify-agent infra error is NOT a security finding — fall
            # through but log loudly so we know it isn't gating commits.
            logger.warning("vanguard verify agent crashed: %r", _ve)
            await _pkg._log(task_id, f"⚠️ Vanguard verify agent crashed: {type(_ve).__name__}", "warning")

        # 2c) TWO-AGENT MAXX (iter 40) — Claude reviews DeepSeek's edits.
        # Gated on per-task `maxx_mode`. On PASS we commit DeepSeek's
        # output as-is. On FAIL we commit Claude's corrected version.
        # Claude outage → defaults to PASS so the pipeline never blocks.
        deepseek_draft = dict(edits)   # snapshot for the council log
        review_result = {"pass": True, "corrected": None, "issues": []}
        if maxx_mode:
            try:
                await _pkg._log(task_id, "🔍 Claude reviewing DeepSeek edits…")
                from services.code_reviewer import review_code_with_claude
                review_result = await review_code_with_claude(
                    file_blocks=edits,
                    user_intent=task,
                    repo_ctx=f"{owner}/{repo}@{branch}",
                )
                if review_result["pass"]:
                    await _pkg._log(task_id, "✅ Claude review: PASS", "success")
                else:
                    n_fixed = len(review_result.get("corrected") or {})
                    await _pkg._log(task_id, f"🩹 Claude review: corrected {n_fixed} file(s)", "warning")
                    edits = review_result["corrected"] or edits
                    await _pkg._set_status(task_id, agent_used="deepseek+claude")
            except Exception as _re:
                from services.error_classifier import classify_error
                _re_safe = classify_error(_re)["user_message"]
                await _pkg._log(task_id, f"⚠️ reviewer error (committing original): {_re_safe}", "warning")
                review_result = {"pass": True, "corrected": None, "issues": []}

        # 2d) Council log — fire-and-forget; never blocks the commit.
        try:
            from services.ora_council_logger import log_code_task
            _db = _pkg.get_db()
            if _db is not None:
                await log_code_task(
                    db=_db,
                    user_message=task,
                    repo_context=f"{owner}/{repo}@{branch}",
                    deepseek_draft=str(deepseek_draft)[:4000],
                    final_output=str(edits)[:4000],
                    correction_applied=not review_result["pass"],
                    pass_result=bool(review_result["pass"]),
                    claude_correction=str(review_result.get("corrected") or "") or None,
                    lint_blocked=False,
                    lint_issues=lint_result.get("issues", []),
                    parallelized=parallelized,
                    agents_used_count=agents_count,
                    task_id=task_id,
                    user_id=proj.get("user_id"),
                    project_id=proj.get("project_id"),
                    maxx_mode=maxx_mode,
                )
        except Exception:
            pass

        # 2026-08-27 — checkpoint/resume Phase 2: persist the final,
        # fully-vetted edits (post hallucination-gate + Vanguard + lint)
        # BEFORE the commit fires. If this task crashes/fails on the
        # commit step itself, a retry within _pkg.PENDING_EDITS_TTL_S reuses
        # this exact content — skipping the LLM codegen call — instead
        # of paying for full regeneration. Best-effort; a failure here
        # must never block the actual commit.
        try:
            _db_pe = _pkg.get_db()
            if _db_pe is not None:
                await _db_pe.cto_tasks.update_one(
                    {"task_id": task_id},
                    {"$set": {"pending_edits": {
                        "edits":        edits,
                        "summary":      summary,
                        "parallelized": parallelized,
                        "agents_count": agents_count,
                        "saved_at":     datetime.now(timezone.utc),
                    }}},
                )
        except Exception as e:                                # noqa: BLE001
            logger.debug("[cto-task %s] pending_edits persist skipped: %r", task_id, e)

        # 3) Commit + push as one atomic API call
        await _pkg._set_status(task_id, status="pushing")
        # Per-file progress frames so the live tape can render the
        # "Writing 2/4 files" mini bar. _pkg.gh_api_commit is atomic on the
        # remote side, so we narrate the writes locally before firing it.
        _file_list = list(edits.keys())
        _total = len(_file_list)
        _db_plan = _pkg.get_db()
        for _i, _fp in enumerate(_file_list, 1):
            await _emit(
                task_id,
                f"Writing file {_i} of {_total}: {_fp}",
                kind="task_state",
                files_done=_i,
                files_total=_total,
                pct=85 + int((_i / max(_total, 1)) * 5),
            )
            # Flip the matching task_plan row → done so the UI's
            # TaskManagementPanel ticks off in real time.
            if _promised_files and _db_plan is not None:
                try:
                    await _db_plan.cto_tasks.update_one(
                        {"task_id": task_id, "task_plan.file": _fp},
                        {"$set": {"task_plan.$.status": "done"}},
                    )
                except Exception:
                    pass
        await _emit(task_id, "Committing to GitHub…", kind="phase_commit", pct=90)

        # ── Iter 286 (Track 0) — test-file lock ──────────────────────
        # ship_code / Mode-C task path previously committed whatever
        # the LLM generated, gated only by Vanguard secrets scan. The
        # loop-pipeline test-file lock (services/loop_diff_classifier)
        # was NOT applied — an MCP client could ship task=`fix the
        # failing test` and the agent would rewrite test_*.py to
        # satisfy the failing case. Same protection Loop mode has:
        # block by default, allow only when the caller sets
        # allow_test_file_change=true (that flag is human-approved
        # in the Loop path; here it must never be self-grantable by
        # the LLM — enforce by only reading it from the top-level
        # task_meta, never from LLM-generated content).
        try:
            from services.loop_diff_classifier import is_test_or_fixture
        except Exception:                        # noqa: BLE001
            is_test_or_fixture = lambda _p: False   # noqa: E731
        # 2026-08-25 · P0 hotfix (root cause of the "'str' object has
        # no attribute 'get'" production crash, task t_4d07055adb99).
        # `edits` is a {path: content} dict — iterating `for e in
        # edits` already yields the path STRING, not a per-file dict.
        # The old code then called .get("path") on that bare string,
        # which is exactly the contract violation this resilience
        # pass targets. Paths are the dict keys directly — no lookup
        # needed at all.
        _test_touched = [p for p in edits if is_test_or_fixture(p or "")]
        # `allow_test_file_change` is read from the task record itself
        # — never from LLM output — so the model cannot self-grant.
        _task_row = await _db_plan.cto_tasks.find_one(
            {"task_id": task_id},
            {"allow_test_file_change": 1, "_id": 0},
        ) or {}
        _allow_tests = bool(_task_row.get("allow_test_file_change"))
        if _test_touched and not _allow_tests:
            _paths = list(_test_touched)
            # 2026-08-27 · P5 (Journey/Intent-Grounding build round) —
            # this used to read like an internal changelog entry
            # ("Loop-pipeline test-file lock is enforced on this path
            # (Iter 286)."). The LOCK BEHAVIOR is correct and unchanged
            # — only the wording changes: one plain-English line, with
            # the exact file paths still available in `blocked_paths`
            # for the frontend to show on expand.
            await _pkg._log(task_id,
                       "⛔ Can't apply this change — "
                       f"{'that file is' if len(_paths) == 1 else 'those files are'} "
                       "locked (test file"
                       f"{'' if len(_paths) == 1 else 's'}). Approve "
                       "it in Loop mode and I'll make the change.",
                       "error")
            await _db_plan.cto_tasks.update_one(
                {"task_id": task_id},
                {"$set": {
                    "status":          "blocked",
                    "blocked_reason":  "test_file_lock",
                    "blocked_paths":   _paths,
                    "updated_at":      time.time(),
                }},
            )
            return {"ok": False, "error": "test_file_lock",
                    "blocked_paths": _paths}

        async def _prog(step: str, status: str = "info"):
            await _pkg._log(task_id, step, status)

        # H3 hardening (2026-08-30, overnight-loop-2 P0) — re-fetch the
        # project's LIVE GitHub binding right before the real commit and
        # assert it still matches what was pinned when this worker
        # started. Mismatch -> ABORT, zero writes, explicit user-visible
        # error (never silently re-target). Same defence as
        # loop_engine.py's confirm_ship() — see
        # REPORT-x1-crossproject.md §W1/H3.
        try:
            _live_proj = await _db_plan.cto_projects.find_one(
                {"project_id": _pin_project_id, "user_id": _pin_user_id},
                {"_id": 0, "github_owner": 1, "github_repo": 1,
                 "branch": 1, "github_branch": 1, "installation_id": 1},
            )
        except Exception as _pin_err:                                # noqa: BLE001
            logger.warning("[%s] repo-pin re-fetch failed: %r", task_id, _pin_err)
            _live_proj = None
        if not _live_proj:
            await _pkg._set_status(task_id, status="failed",
                              error="Your project's GitHub connection could not be "
                                    "verified right before shipping — re-link your "
                                    "repo in Settings and try again. No commit was made.",
                              completed_at=time.time())
            return
        _live_branch = _live_proj.get("branch") or _live_proj.get("github_branch") or "main"
        if (_live_proj.get("github_owner") != _pin_owner
                or _live_proj.get("github_repo") != _pin_repo
                or _live_branch != _pin_branch
                or (_pin_installation_id is not None
                    and _live_proj.get("installation_id") != _pin_installation_id)):
            logger.warning(
                "[%s] SHIP REFUSED — repo pin mismatch. pinned=%s/%s@%s live=%s/%s@%s",
                task_id, _pin_owner, _pin_repo, _pin_branch,
                _live_proj.get("github_owner"), _live_proj.get("github_repo"), _live_branch)
            try:
                from services.trust_surface_events import log_trust_event
                await log_trust_event(
                    _db_plan, "loop_pin_mismatch", user_id=_pin_user_id or "unknown",
                    task_id=task_id, project_id=_pin_project_id or "")
            except Exception:
                pass
            await _pkg._set_status(task_id, status="failed",
                              error="Your project's GitHub connection changed while "
                                    "this task was running — refusing to write to a "
                                    "repo/branch that no longer matches. No commit "
                                    "was made. Re-run the task to ship against the "
                                    "current connection.",
                              completed_at=time.time())
            return

        # 2026-08-28 · P0 hotfix (root cause of the production
        # "commit_files() missing 2 required positional arguments:
        # 'author_email' and 'author_name'" crash). This is the ONLY
        # commit_files() call site in the repo that never resolved a
        # real developer identity first — every other caller (rollback,
        # loop_engine, visibility kit, local_tools) does this same
        # resolve_git_identity() call before committing.
        from services.git_identity import resolve_git_identity
        _author_name, _author_email = await resolve_git_identity(
            _db_plan, proj.get("user_id") or "",
        )
        try:
            result = await _retry(
                lambda: _pkg.gh_api_commit(
                    owner=owner, repo=repo, branch=branch, token=user_token,
                    files=edits,
                    commit_message=f"AUREM: {task[:60]}",
                    author_name=_author_name, author_email=_author_email,
                    progress=_prog,
                ),
                what="GitHub commit", task_id=task_id, attempts=4, base_sleep=2.0,
            )
        except PushFailedError as e:
            # Ship/Commit Robustness · 2026-08-26 — the commit object
            # exists (by SHA) but never reached `branch`'s history.
            # This is NOT "nothing was committed" — surface the real
            # SHA + `push_failed=True` so chat_helpers can render the
            # truth instead of the generic failure message.
            await _persist_push_failed(task_id, e)
            return
        sha = result["sha"]
        commit_full_sha = result.get("full_sha") or sha
        # B1-extend hardening (2026-08-30) — same fix as loop_engine.py's
        # post-ship cache clear, applied here so the direct task-submit
        # path can't leave a stale "disconnected" reading either.
        try:
            from routers.repo_status import invalidate as _invalidate_repo_status
            _invalidate_repo_status(_pin_project_id or "")
        except Exception as _inv_err:                                # noqa: BLE001
            logger.debug("[%s] repo_status invalidate skipped: %r", task_id, _inv_err)

        # POST-PUSH VERIFY — re-fetch every edited file at the new commit's
        # SHA and confirm the remote content equals what we just pushed.
        # This catches:
        #   • branch protection that silently rejected the ref update
        #   • partial / drift writes if a future GitHub API change ever
        #     broke our blob/tree pipeline
        #   • the original user complaint: "Claude says fix isn't in the
        #     repo even though our UI says shipped"
        # The verification proves on every task that the deployed code
        # actually contains the AI's edits — no more silent successes.
        await _pkg._log(task_id, f"🔎 Verifying {len(edits)} file(s) on remote @ {sha}…")

        async def _verify_one(path: str, expected: str) -> tuple[str, bool, str]:
            remote = await _pkg.gh_api_fetch_file(
                owner, repo, path, commit_full_sha, user_token,
            )
            if remote is None:
                return path, False, "remote returned 404"
            # Iter 212m-6 — Normalise line endings (CRLF/CR → LF) and
            # trailing whitespace BEFORE comparing. GitHub sometimes
            # serves files with normalised newlines that differ from
            # what we pushed even though the commit landed correctly.
            # Without this, an otherwise-successful commit gets marked
            # "failed" because the byte-for-byte comparison disagrees
            # on whitespace-only differences.
            def _norm(s: str) -> str:
                return (s or "").replace("\r\n", "\n").replace("\r", "\n").rstrip()
            if _norm(remote) != _norm(expected):
                a, b = expected.splitlines(), remote.splitlines()
                first_diff = next(
                    (i for i in range(min(len(a), len(b))) if a[i] != b[i]),
                    None,
                )
                hint = (f"differs from line {first_diff + 1}" if first_diff is not None
                        else f"length local={len(expected)} remote={len(remote)}")
                return path, False, hint
            return path, True, "ok"

        verify_results = await asyncio.gather(*[
            _verify_one(p, c) for p, c in edits.items()
        ])
        failed = [(p, reason) for p, ok, reason in verify_results if not ok]
        for p, ok, reason in verify_results:
            await _pkg._log(task_id,
                       f"   {'✅' if ok else '❌'} {p} ({reason})",
                       "success" if ok else "error")
        if failed:
            err = "Post-push verification FAILED for: " + ", ".join(
                f"{p} ({r})" for p, r in failed
            )
            await _pkg._log(task_id, f"🚫 {err}", "error")
            await _pkg._set_status(task_id, status="failed", error=err[:2000],
                              commit_sha=sha, verify_failed=True,
                              completed_at=time.time())
            return
        await _pkg._log(task_id,
                   f"✅ Verified {len(edits)} file(s) live on {branch}@{sha}",
                   "success")

        await _pkg._set_status(task_id, status="done", result=summary,
                          commit_sha=sha,
                          files_changed_simple=list(edits.keys()),
                          edits=_frontend_subset(edits),
                          verified=True,
                          completed_at=time.time())
        # iter 114 — rich diff + popup data
        try:
            from services.task_diff import (
                build_files_changed, shape_vanguard_findings,
                build_unified_diff_hunks,
            )
            from services.ora_chat.tool_output_wrapper import wrap_edited_files
            rich_changes = build_files_changed(contents, edits)
            # Overnight T1 (METER) — deterministic diff metrics off the
            # diff the writer already computed above. Zero LLM, zero
            # extra GitHub call.
            from services.ship_meter import compute_meter_fields
            ship_meter = compute_meter_fields(rich_changes)
            findings_clean = shape_vanguard_findings(
                (verify_result.get("findings", []) if "verify_result" in locals() else []),
                status=("blocked" if "verify_result" in locals()
                        and not verify_result.get("pass", True)
                        else "fixed"),
            )
            # Iter 388g — inline diff-view payload for the chat bubble.
            # `build_unified_diff_hunks` produces per-line old_n/new_n
            # gutter columns; `wrap_edited_files` puts it in the SSE
            # shape the EditedFileBubble frontend consumes.  Persisted
            # alongside `files_changed` so `/cto/tasks/{id}` can serve
            # the same data to the chat panel on task-completion.
            hunk_files = []
            for _path, _after in (edits or {}).items():
                _before = (contents or {}).get(_path)
                hunk_files.append({
                    "path":  _path,
                    "hunks": build_unified_diff_hunks(
                        _before, _after, context=2,
                    ),
                })
            edited_files_payload = wrap_edited_files(hunk_files)
            # 2026-08-23 audit fix — `db` was referenced here before its
            # assignment further down (line ~3610), so this whole block
            # always raised UnboundLocalError and was silently swallowed
            # by the enclosing `except Exception as _diff_e`, meaning
            # diff-popup persistence never actually ran on this (HTTP
            # task) path. Resolve `db` locally right here instead.
            db = _pkg.get_db()
            _started = (await db.cto_tasks.find_one(
                {"task_id": task_id}, {"started_at": 1, "_id": 0}
            )) or {}
            elapsed = max(0, int(time.time() - (_started.get("started_at") or time.time())))
            await _pkg._set_status(
                task_id,
                files_changed=rich_changes,
                vanguard_findings=findings_clean,
                edited_files=edited_files_payload,
                time_taken_seconds=elapsed,
                github_url=f"https://github.com/{owner}/{repo}/commit/{commit_full_sha}",
                ship_meter=ship_meter,
            )
        except Exception as _diff_e:
            logger.warning("task_diff/popup persistence failed: %r", _diff_e)
        # Iter 184 — fire a `task_handoff` frame on the task SSE stream
        # immediately before the terminal `done` frame so any client
        # subscribed to /tasks/{task_id}/stream (notably the ChatPanel
        # LiveTaskPopup that auto-attaches when the assistant message
        # carries a `shipped_task_id`) sees the canonical handoff
        # event. chat.py already emits this frame on the chat SSE
        # stream for Mode D→C / ship-shortcut handoffs; mirroring it
        # here covers the HTTP `/tasks/submit` path which never goes
        # through the chat stream — the popup was silently missing
        # for those tasks.
        await _emit(
            task_id, "task_handoff",
            kind="task_handoff",
            project_id=proj.get("project_id"),
            sha=(sha[:7] if sha else ""),
            source="task_worker_done",
        )
        await _emit(task_id, f"Done — {sha[:7]}", kind="done", pct=100)
        db = _pkg.get_db()
        # Iter 167 — post-task scan: regex-only security + import lint
        # on the files ORA just shipped. Fire-and-forget guard so a slow
        # scan never blocks the "done" emit.
        if db is not None:
            try:
                from services.post_task_scanner import scan_changed_files
                _scan_paths = list(edits.keys())
                _scan_issues = await asyncio.wait_for(
                    scan_changed_files(_scan_paths, edits),
                    timeout=5.0,
                )
                if _scan_issues:
                    await db.cto_tasks.update_one(
                        {"task_id": task_id},
                        {"$set": {"post_scan": {
                            "issues":        _scan_issues,
                            "scanned_at":    time.time(),
                            "files_scanned": len(_scan_paths),
                        }}},
                    )
                    for issue in _scan_issues:
                        await _pkg._log(
                            task_id,
                            f"{issue.get('icon','⚠️')} {issue['message']} "
                            f"in {issue['file']}:{issue['line']}",
                            "warn",
                        )
            except Exception as _scan_err:
                logger.debug("post_scan (api path) skipped: %r", _scan_err)
        if db is not None:
            await db.cto_projects.update_one(
                {"project_id": proj["project_id"]},
                {"$inc": {"tasks_done": 1}, "$set": {"last_task": time.time()}},
            )
            # iter 41 — fire-and-forget brain update so ORA remembers what
            # was shipped, what files moved, and any recurring corrections.
            try:
                from services.project_brain import update_brain_after_commit
                asyncio.create_task(update_brain_after_commit(
                    db=db,
                    project_id=proj.get("project_id", ""),
                    task_description=task,
                    files_changed=list(edits.keys()),
                    was_correction_applied=not review_result["pass"],
                    issues_found=review_result.get("issues", []),
                    sha=sha or "",
                ))
            except Exception:
                pass
            # Iter 165 — Brain V2 auto-update. Fire-and-forget so a
            # slow GitHub never blocks task completion. Falls back to
            # full rebuild every FULL_REFRESH_EVERY_N_TASKS tasks.
            try:
                from services.project_brain import update_brain_after_task
                asyncio.create_task(update_brain_after_task(
                    db=db,
                    project_id=proj.get("project_id", ""),
                    user_id=proj.get("user_id"),
                    changed_files=list(edits.keys()),
                    task_id=task_id,
                    github_token=user_token or "",
                    github_owner=proj.get("github_owner", "") or "",
                    github_repo=proj.get("github_repo", "") or "",
                    branch=proj.get("branch", "main") or "main",
                ))
            except Exception as _bv2e:
                logger.warning("brain v2 update skipped: %r", _bv2e)
    except Exception as e:
        logger.exception(f"[cto-task-api {task_id}] failed")
        safe = str(e).replace(user_token or "", "***PAT***")
        # 2026-08-25 — root-cause fix (customer-reported raw Python
        # error exposed in chat: "'str' object has no attribute
        # 'get'"). `safe` (the real exception text) is kept ONLY in
        # the `error` DB field — used by error_translator's plain-
        # English rewrite and the collapsed "Show technical details"
        # toggle. It must NEVER be pushed to `_pkg._log`/`_emit`, which are
        # rendered live, unfiltered, in the chat bubble's worker tape.
        from services.error_classifier import classify_error
        from services.failure_signature import compute_signature, record_and_check
        from core.errors import classify_exception, new_ref_id
        _cat = classify_error(e)["category"]
        _safe_msg = classify_error(e)["user_message"]
        _code = classify_exception(e)
        _ref_id = new_ref_id()
        _sig = compute_signature(proj.get("project_id", ""), task, _cat, safe)
        _sig_info = await record_and_check(
            _pkg.get_db(), project_id=proj.get("project_id", ""), signature=_sig)
        await _pkg._log(task_id, f"❌ {_safe_msg} (ref: {_ref_id})", "error")
        await _pkg._set_status(task_id, status="failed", error=safe,
                          error_category=_cat, error_code=_code.value,
                          ref_id=_ref_id, failure_signature=_sig,
                          failure_repeat_count=_sig_info["repeat_count"],
                          completed_at=time.time())
        await _emit(task_id, f"Failed — {_safe_msg}", kind="fail", pct=100)
        # Iter 48 — background-task crash goes to Sentry (bypasses HTTP
        # middleware so explicit capture needed).
        try:
            import sentry_sdk
            with sentry_sdk.push_scope() as scope:
                scope.set_tag("kind", "cto_task_crash")
                scope.set_tag("task_id", task_id)
                scope.set_tag("project_id", proj.get("project_id", ""))
                scope.set_extra("repo", f"{proj.get('github_owner')}/{proj.get('github_repo')}")
                sentry_sdk.capture_exception(e)
        except Exception:
            pass

