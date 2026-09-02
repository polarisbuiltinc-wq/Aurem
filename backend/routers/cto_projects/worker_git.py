"""
routers/cto_projects/worker_git.py — AUREM CTO Projects.
`git`-binary task worker: clones/pulls, runs AI codegen, hallucination
/syntax/lint gates, Vanguard verify (+ one auto-fix retry), commits +
pushes via the local `git` binary, then persists rich diff data.

Split from the former monolithic routers/cto_projects.py on
2026-09-08 (responsibility-based extraction, no logic change). Uses
`_pkg.<name>` for anything patched at the package level by the
existing test suite (`get_db`, `call_llm`, `_log`, `_set_status`,
`_sh`, `WORKSPACE`) — see preview.py's module docstring for why.
"""
import asyncio
import logging
import re
import shutil
import time
from datetime import datetime, timezone
from typing import Optional

from services.cto_projects_helpers import _retry, _emit
from services.cto_pipeline_steps import (
    _run_hallucination_gate, _run_syntax_gate, _run_lint_gate,
)

import routers.cto_projects as _pkg
from . import router
from .worker_api import _frontend_subset, _AI_SYS

logger = logging.getLogger(__name__)


async def _run_task_with_git(task_id, proj, task, files, context, user_token, maxx_mode: bool = False,
                             resume_edits: Optional[dict] = None):

    def _scrub(s: str) -> str:
        # Defence-in-depth: clone URLs, stderr, and Python tracebacks can
        # all leak the PAT. Scrub every error string before it lands in
        # Mongo or the user's task feed.
        if not s:
            return s
        return s.replace(user_token or "", "***PAT***") if user_token else s

    ws = _pkg.WORKSPACE / task_id
    ws.mkdir(parents=True, exist_ok=True)
    repo_path = ws / "repo"
    owner, repo, branch = proj["github_owner"], proj["github_repo"], proj.get("branch", "main")
    # 2026-08-24 fix — see matching fix in _run_rollback_with_git above:
    # GitHub App installation tokens are the HTTPS PASSWORD (username
    # "x-access-token"), not a username-only PAT-style embed.
    clone_url = (f"https://x-access-token:{user_token}@github.com/{owner}/{repo}.git"
                 if user_token else f"https://github.com/{owner}/{repo}.git")

    try:
        # 1) clone
        await _pkg._set_status(task_id, status="pulling", started_at=time.time())
        await _pkg._log(task_id, f"Cloning {owner}/{repo}@{branch}…")
        # 2026-09-09 — offloaded to a thread: see matching fix + rationale
        # in rollback.py (confirmed root cause of the nginx "/health
        # upstream timed out" bursts that made K8s mark the pod unhealthy
        # mid-deploy — a synchronous git clone/push here blocks the SAME
        # event loop that serves every other request, including /health).
        r = await asyncio.to_thread(
            _pkg._sh, ["git", "clone", "--depth=1", "--branch", branch, clone_url, str(repo_path)],
            cwd=ws, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"git clone failed: {_scrub(r.stderr)[:300]}")
        await _pkg._log(task_id, "✅ Cloned", "success")

        # 2) read target files
        await _pkg._set_status(task_id, status="reading")
        contents = {}
        for f in (files or [])[:6]:
            fp = repo_path / f
            if fp.is_file():
                contents[f] = fp.read_text(errors="replace")[:10000]
                await _pkg._log(task_id, f"📄 read {f}")
        if not contents:
            # auto-pick a few likely files
            for cand in ["main.py", "app.py", "server.py", "index.html",
                         "src/App.jsx", "src/main.jsx", "pages/index.js", "README.md"]:
                fp = repo_path / cand
                if fp.is_file():
                    contents[cand] = fp.read_text(errors="replace")[:10000]
                    if len(contents) >= 4:
                        break

        # 3) ai fix
        await _pkg._set_status(task_id, status="fixing")

        # LOGIC FIX — mirror the API path: inject Project Brain, GitHub
        # Issues, and Vanguard security skills here too. Without this, if
        # `git` ever becomes available in production, Iter 41/42/44
        # features silently vanish on every code task.
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
        except Exception:
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
        try:
            from services.skill_context_injector import build_skill_context
            sk_ctx = build_skill_context(task)
            if sk_ctx:
                extra_context_block += f"\n\n{sk_ctx}"
                await _pkg._log(task_id, "🛡️ Applying security best practices for this task")
        except Exception:
            pass
        user_msg = (
            f"TASK: {task}\n"
            f"{('CONTEXT: ' + context) if context else ''}\n\n"
            f"Tech: {proj.get('tech_stack','auto')}\n\n"
            f"{extra_context_block}\n\n{files_blob}"
        )
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
        else:
            reply = await _retry(
                lambda: _pkg.call_llm(
                    messages=[{"role": "user", "content": user_msg}],
                    system=_AI_SYS, max_tokens=3500, temperature=0.0,
                ),
                what="AI codegen", task_id=task_id,
            )
            summary_m = re.search(r"SUMMARY:\s*(.+)", reply)
            summary = (summary_m.group(1).strip() if summary_m else "AI changes")[:300]
            # Iter 212m-33 — tolerant FILE-block parser.
            from services.llm_file_parser import parse_file_blocks
            edits = parse_file_blocks(reply)
            if not edits:
                # Iter 212m-177 — P0-4b: retry once with explicit guidance,
                # then FAIL — never report success without a real edit.
                await _pkg._log(task_id, "⚠️ AI returned no file edits — auto-retrying", "warning")
                _nudge = (
                    "Your previous response contained no usable file changes.\n"
                    "You MUST output complete file content using this exact "
                    "format:\nFILE: <path>\n```\n<complete file body>\n```\n"
                    "Do NOT just describe what you would do."
                )
                reply = await _retry(
                    lambda: _pkg.call_llm(
                        messages=[{"role": "user",
                                   "content": user_msg + "\n\n" + _nudge}],
                        system=_AI_SYS, max_tokens=3500, temperature=0.0,
                    ),
                    what="AI codegen auto-retry", task_id=task_id,
                )
                edits = parse_file_blocks(reply)
            if not edits:
                err = ("AI produced no file edits after a retry — nothing was "
                       "changed. Rephrase the task naming the exact file, e.g. "
                       "'Edit backend/utils/auth.py and add …'.")
                await _pkg._log(task_id, f"🚫 {err}", "error")
                await _pkg._set_status(task_id, status="failed", error=err,
                                  completed_at=time.time())
                return
            await _pkg._log(task_id, f"✏️ {len(edits)} files to update", "success")

        # 2026-08-27 — sensitive-path guard (real implementation of
        # GUARDS_CHARTER's G3 PROTECTED_PATHS concept, which was speced
        # but never actually built anywhere — see
        # services/sensitive_path_guard.py docstring). Fires BEFORE
        # Vanguard/write/commit so a blocked task never pays for the
        # rest of the pipeline. `allow_sensitive_file_change` is read
        # only from the task record — never from LLM output.
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


        # 2026-09-08 — Phase 2 safety fix. `_run_task_with_git` — the
        # worker that actually runs whenever the `git` binary is
        # present (i.e. the real production runtime path) — previously
        # committed to the customer's real repo WITHOUT the
        # hallucination-gate, syntax-check, or lint-check that
        # `_run_task_via_api` already ran (see PRD.md Phase 2 entry:
        # 0 hits here vs 7/12/17 there, before this fix). These three
        # gates (shared module-level functions, also used by
        # `_run_task_via_api` above) now run identically on both
        # workers, in the same order relative to the sensitive-path
        # guard and Vanguard verify below. Only the COMMIT MECHANISM
        # stays different (git binary here vs the GitHub Data API on
        # the other path) — that difference is intentional, unchanged.
        edits, _hallu_err = await _run_hallucination_gate(task_id, edits, contents, user_msg, _AI_SYS)
        if _hallu_err:
            await _pkg._log(task_id, f"🚫 {_hallu_err}", "error")
            await _pkg._set_status(task_id, status="failed", error=_hallu_err[:2000],
                              completed_at=time.time())
            return

        edits, _syntax_err = await _run_syntax_gate(task_id, edits, user_msg, _AI_SYS)
        if _syntax_err:
            await _pkg._log(task_id, f"🚫 {_syntax_err}", "error")
            await _pkg._set_status(task_id, status="failed", error=_syntax_err[:2000],
                              completed_at=time.time())
            await _emit(task_id, "Syntax error — task failed",
                        kind="fail", pct=100)
            return

        edits, lint_result, _lint_err = await _run_lint_gate(task_id, edits)
        if _lint_err:
            await _pkg._set_status(task_id, status="failed", error=_lint_err[:2000],
                              completed_at=time.time())
            return


        # iter 111 / 2026-08-25 parity fix — the git-subprocess path was
        # MISSING the Vanguard verify agent entirely (it only ran on the
        # git-less API fallback path). Since `_run_task_with_git` is the
        # worker every host with `git` installed actually uses (i.e. the
        # real runtime path), this is where the security gate — and the
        # new auto-fix-and-reverify self-correction loop — must live for
        # the feature to genuinely fire in production, not just on the
        # rarely-used fallback.
        try:
            await _pkg._log(task_id, "🛡️ Vanguard verify agent reviewing patch…")
            from services.vanguard_verify_agent import verify_patch
            _vg_mode = "maxx" if maxx_mode else "swift"
            verify_result = await verify_patch(
                edits, repo_ctx=f"{owner}/{repo}@{branch}",
                mode=_vg_mode, base_blocks=contents,
            )
            await _pkg._log(task_id, f"🛡️ Verify: {verify_result['summary']}",
                       "info" if verify_result["pass"] else "error")
            if not verify_result["pass"]:
                # 2026-08-25 — same genuine self-correction pass as the
                # API path: feed the LLM the EXACT findings, regenerate
                # only the affected files, re-verify once before failing.
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
                    _vg_edits = parse_file_blocks(_vg_reply)
                    if _vg_edits:
                        edits.update(_vg_edits)
                        verify_result_2 = await verify_patch(
                            edits, repo_ctx=f"{owner}/{repo}@{branch}",
                            mode=_vg_mode, base_blocks=contents,
                        )
                except Exception as _vgfe:
                    logger.warning("Vanguard auto-fix attempt (git path) crashed: %r", _vgfe)

                if verify_result_2["pass"]:
                    await _pkg._log(
                        task_id,
                        "✅ Auto-fix resolved the blocked finding(s) — "
                        "re-verified clean, proceeding to commit.",
                        "success",
                    )
                    verify_result = verify_result_2
                else:
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
            logger.warning("vanguard verify agent (git path) crashed: %r", _ve)
            await _pkg._log(task_id, f"⚠️ Vanguard verify agent crashed: {type(_ve).__name__}", "warning")

        # 2026-08-27 — checkpoint/resume Phase 2: persist the final,
        # fully-vetted edits (post Vanguard) BEFORE the write+commit. If
        # this task crashes/fails on the write/commit/push step itself, a
        # retry within PENDING_EDITS_TTL_S reuses this exact content —
        # skipping the LLM codegen call — instead of paying for full
        # regeneration. Best-effort; a failure here must never block the
        # actual write/commit.
        try:
            _db_pe = _pkg.get_db()
            if _db_pe is not None:
                await _db_pe.cto_tasks.update_one(
                    {"task_id": task_id},
                    {"$set": {"pending_edits": {
                        "edits":    edits,
                        "summary":  summary,
                        "saved_at": datetime.now(timezone.utc),
                    }}},
                )
        except Exception as e:                                # noqa: BLE001
            logger.debug("[cto-task %s] pending_edits persist skipped: %r", task_id, e)

        # Guardrail #2 (2026-08 audit remediation, Wave 1 follow-up,
        # 2026-09-08) — the git-binary worker writes to disk + `git
        # commit`/`git push` directly, BYPASSING `github_api_writer.
        # commit_files` (where this same check already lived) entirely.
        # Same deny-list, same warn/block behavior, checked here right
        # before any file touches disk on this path too.
        try:
            from cto_services.db import get_db as _get_guard_db
            _guard_db = _get_guard_db()
        except Exception:                                       # noqa: BLE001
            _guard_db = None
        from services.write_guard import check_write_paths as _check_write_paths
        await _check_write_paths(
            _guard_db, list(edits.keys()), owner=owner, repo=repo, branch=branch,
        )

        # 4) write
        for path, content in edits.items():
            fp = repo_path / path
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content)
            await _pkg._log(task_id, f"💾 {path}")

        # 5) commit + push
        await _pkg._set_status(task_id, status="pushing")
        await asyncio.to_thread(_pkg._sh, ["git", "config", "user.email", "cto@auremcto.com"], repo_path)
        await asyncio.to_thread(_pkg._sh, ["git", "config", "user.name", "AUREM"], repo_path)
        await asyncio.to_thread(_pkg._sh, ["git", "add", "-A"], repo_path)
        cm = await asyncio.to_thread(_pkg._sh, ["git", "commit", "-m", f"AUREM: {task[:60]}"], repo_path)
        if "nothing to commit" in cm.stdout:
            await _pkg._log(task_id, "ℹ️ no diff to commit", "info")
            await _pkg._set_status(task_id, status="done", result=summary,
                              completed_at=time.time())
            return
        push = await asyncio.to_thread(_pkg._sh, ["git", "push", "origin", branch], repo_path, timeout=90)
        if push.returncode != 0:
            raise RuntimeError(f"git push failed: {_scrub(push.stderr)[:300]}")
        sha_r = await asyncio.to_thread(_pkg._sh, ["git", "rev-parse", "--short", "HEAD"], repo_path)
        sha = sha_r.stdout.strip()
        commit_full_sha_r = await asyncio.to_thread(_pkg._sh, ["git", "rev-parse", "HEAD"], repo_path)
        commit_full_sha = commit_full_sha_r.stdout.strip()
        await _pkg._log(task_id, f"🚀 pushed — {sha}", "success")
        await _pkg._set_status(task_id, status="done", result=summary,
                          commit_sha=sha,
                          files_changed_simple=list(edits.keys()),
                          edits=_frontend_subset(edits),
                          completed_at=time.time())
        # Iter 388g-fix — parity with `_run_task_via_api`: persist the
        # rich diff payload (files_changed + unified-diff hunks +
        # github_url + time_taken_seconds) so /cto/tasks/{id} returns
        # the same shape on git-path completions, and the ChatPanel
        # LiveTaskPopup can inject the inline EditedFileBubble via its
        # `onDone` handler. Without this the git-path (real production
        # PAT-connected runs) silently ships tasks with no diff view.
        try:
            from services.task_diff import (
                build_files_changed, shape_vanguard_findings,
                build_unified_diff_hunks,
            )
            from services.ora_chat.tool_output_wrapper import wrap_edited_files
            rich_changes = build_files_changed(contents, edits)
            # Overnight T1 (METER) — deterministic diff metrics off the
            # diff already computed above. Zero LLM, zero extra call.
            from services.ship_meter import compute_meter_fields
            ship_meter = compute_meter_fields(rich_changes)
            findings_clean = shape_vanguard_findings(
                (verify_result.get("findings", []) if "verify_result" in locals() else []),
                status=("blocked" if "verify_result" in locals()
                        and not verify_result.get("pass", True)
                        else "fixed"),
            )
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
            _started = (await _pkg.get_db().cto_tasks.find_one(
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
            logger.warning("task_diff/popup persistence (git path) failed: %r", _diff_e)
        # Mirror API-path SSE frames — task_handoff frame BEFORE the
        # terminal `done` frame so LiveTaskPopup's `onDone` fires with
        # the completed task payload that now carries edited_files.
        await _emit(
            task_id, "task_handoff",
            kind="task_handoff",
            project_id=proj.get("project_id"),
            sha=(sha[:7] if sha else ""),
            source="task_worker_done",
        )
        await _emit(task_id, f"Done — {sha[:7]}", kind="done", pct=100)
        db = _pkg.get_db()
        # Iter 167 — post-task scan on git-path too (parity with API path).
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
                logger.debug("post_scan (git path) skipped: %r", _scan_err)
        if db is not None:
            await db.cto_projects.update_one(
                {"project_id": proj["project_id"]},
                {"$inc": {"tasks_done": 1}, "$set": {"last_task": time.time()}},
            )
            # Brain update on git path too — without this, chat memory
            # only refreshes when the API-path worker is used. API + git
            # workers MUST keep parity, otherwise toggling between them
            # silently loses commit history from the brain.
            try:
                from services.project_brain import update_brain_after_commit
                asyncio.create_task(update_brain_after_commit(
                    db=db,
                    project_id=proj.get("project_id", ""),
                    task_description=task,
                    files_changed=list(edits.keys()),
                    was_correction_applied=False,
                    issues_found=[],
                    sha=sha or "",
                ))
            except Exception:
                pass
            # Iter 165 — Brain V2 auto-update (git path parity).
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
                logger.warning("brain v2 update (git path) skipped: %r", _bv2e)
    except Exception as e:
        logger.exception(f"[cto-task {task_id}] failed")
        # BUG 1 fix — scrub the PAT from the public error string. The API
        # path already does this; the git path was leaking the token
        # through traceback strings into the task feed AND into Mongo.
        safe = _scrub(str(e))
        # 2026-08-25 — same root-cause fix as the API-path handler
        # above: never push the raw exception string to `_pkg._log` (live,
        # unfiltered in the chat bubble) — only into the `error` field.
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
        await _pkg._set_status(task_id, status="failed", error=safe[:2000],
                          error_category=_cat, error_code=_code.value,
                          ref_id=_ref_id, failure_signature=_sig,
                          failure_repeat_count=_sig_info["repeat_count"],
                          completed_at=time.time())
        # Sentry capture for git-path worker crashes too.
        try:
            import sentry_sdk
            with sentry_sdk.push_scope() as scope:
                scope.set_tag("kind", "cto_task_crash")
                scope.set_tag("task_id", task_id)
                scope.set_tag("path", "git")
                scope.set_tag("project_id", proj.get("project_id", ""))
                sentry_sdk.capture_exception(e)
        except Exception:
            pass
    finally:
        shutil.rmtree(ws, ignore_errors=True)
