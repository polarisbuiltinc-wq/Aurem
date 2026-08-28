"""
services/visibility/apply.py — Visibility Kit apply orchestrator (spec §5).

detect → generate (R6/R7 checks) → REUSE the P6 branch+open-PR
mechanism verbatim (services/loop_safety.py + services/github_api_writer.py,
the exact primitives the T7 ship-via-PR drill already proved live) →
PR body per R14 → upsert VisibilityState + VisibilityApplication.

v1 scope (2026-08-28): implements the 3 fully-deterministic, zero-LLM
AUTO items — ai_crawler_policy, structured_data, sitemap_auto (R9/R15:
"thin implementation" — these need no LLM call at all). `preferred_sources`
and `llms_txt` generators are NOT yet built; requesting them returns
them in `not_implemented`, never silently drops them (R2 — no silent
failures). Advisory items (f, g) are never applied — always excluded,
per R3.
"""
from __future__ import annotations

import time

from services.github_api_writer import commit_files, fetch_file
from services.git_identity import resolve_git_identity
from services.loop_safety import create_or_reuse_branch, open_draft_pr, ship_branch_name
from services.visibility import robots as robots_gen
from services.visibility import schema as schema_gen
from services.visibility import sitemap as sitemap_gen
from services.visibility.detect import detect_framework

IMPLEMENTED_AUTO_ITEMS = {"ai_crawler_policy", "structured_data", "sitemap_auto"}
NOT_YET_IMPLEMENTED = {"preferred_sources", "llms_txt"}
ADVISORY_ITEMS = {"answer_blocks", "image_quick_wins"}


async def apply_visibility_kit(
    db, *, project: dict, requested_items: list[str], token: str,
    scan_urls: list[dict], site_meta: dict, bot_policy: dict, force: bool = False,
) -> dict:
    owner = project.get("github_owner") or project.get("owner")
    repo = project.get("github_repo") or project.get("repo")
    base_branch = project.get("branch") or "main"
    project_id = project["project_id"]
    user_id = project["user_id"]

    advisory_skipped = sorted(set(requested_items) & ADVISORY_ITEMS)
    not_implemented = sorted(set(requested_items) & NOT_YET_IMPLEMENTED)
    to_apply = sorted(set(requested_items) & IMPLEMENTED_AUTO_ITEMS)
    if not to_apply:
        return {"ok": False, "error": "no_implemented_items_in_request",
                "advisory_skipped": advisory_skipped, "not_implemented": not_implemented}

    framework, unknown_fallback = detect_framework(
        site_meta.get("file_tree") or [], site_meta.get("package_json"),
    )

    files: dict[str, str] = {}
    item_conflicts: dict[str, str] = {}  # item_key -> reason, for filtering + PR-body notes
    scan_date = time.strftime("%Y-%m-%d", time.gmtime())

    if "ai_crawler_policy" in to_apply:
        existing = await fetch_file(owner, repo, "robots.txt", base_branch, token)
        if existing and robots_gen._START not in existing and not force:
            item_conflicts["ai_crawler_policy"] = "robots.txt already exists without an AUREM block"
        else:
            files["robots.txt"] = robots_gen.apply_managed_block(existing, bot_policy)

    if "structured_data" in to_apply:
        html_path = site_meta.get("html_entry_path") or "index.html"
        existing_html = await fetch_file(owner, repo, html_path, base_branch, token)
        if existing_html is None:
            item_conflicts["structured_data"] = f"{html_path} not found in this repo"
        elif schema_gen._START not in existing_html and "</head>" not in existing_html and not force:
            item_conflicts["structured_data"] = f"{html_path} has no </head> to inject into"
        else:
            if "</head>" in existing_html and schema_gen._START not in existing_html:
                block = schema_gen.render_json_ld(site_meta["schema"])
                new_html = existing_html.replace("</head>", block + "</head>", 1)
            else:
                new_html = schema_gen.apply_managed_block(existing_html, site_meta["schema"])
            files[html_path] = new_html

    if "sitemap_auto" in to_apply:
        existing_sitemap = await fetch_file(owner, repo, "sitemap.xml", base_branch, token)
        files["sitemap.xml"] = sitemap_gen.merge_lastmod(existing_sitemap, scan_urls, scan_date)

    conflicts = list(item_conflicts.values())
    if not files:
        return {"ok": False, "error": "all_conflicted", "conflicts": conflicts,
                "advisory_skipped": advisory_skipped, "not_implemented": not_implemented}

    branch = ship_branch_name(f"visibility-kit-{scan_date.replace('-', '')}")
    ok, err = await create_or_reuse_branch(
        owner=owner, repo=repo, base_branch=base_branch, new_branch=branch, token=token,
    )
    if not ok:
        return {"ok": False, "error": f"branch_create_failed_{err}"}

    author_name, author_email = await resolve_git_identity(db, user_id)
    applied_names = sorted(set(to_apply) - set(item_conflicts.keys()))
    commit_msg = f"Visibility Kit ({', '.join(applied_names)}) — by AUREM"
    result = await commit_files(
        owner, repo, branch, token, files, commit_msg,
        author_name=author_name, author_email=author_email,
    )

    body = _render_pr_body(applied_names, advisory_skipped, list(files.keys()), branch)
    pr_url, pr_err = await open_draft_pr(
        owner=owner, repo=repo, head_branch=branch, base_branch=base_branch,
        title=f"Visibility Kit ({', '.join(applied_names)}) — by AUREM",
        body=body, token=token,
    )
    if not pr_url:
        return {"ok": False, "error": f"pr_open_failed_{pr_err}", "branch": branch}

    pr_number = None
    if pr_url and "/pull/" in pr_url:
        pr_number = int(pr_url.rsplit("/", 1)[-1])

    now = time.time()
    for item_key in applied_names:
        await db.visibility_state.update_one(
            {"project_id": project_id, "item_id": item_key},
            {"$set": {
                "status": "pr_created", "detected_framework": framework,
                "detail": {"pr_url": pr_url, "branch": branch},
                "updated_at": now,
            }},
            upsert=True,
        )
    await db.visibility_applications.insert_one({
        "project_id": project_id, "branch": branch, "pr_number": pr_number,
        "pr_url": pr_url, "items": applied_names, "status": "open",
        "created_at": now,
    })

    return {
        "ok": True, "branch": branch, "pr_url": pr_url, "pr_number": pr_number,
        "items_covered": applied_names, "advisory_skipped": advisory_skipped,
        "not_implemented": not_implemented, "conflicts": conflicts,
        "detected_framework": framework, "unknown_framework_fallback": unknown_fallback,
    }


def _render_pr_body(applied: list[str], advisory: list[str], paths: list[str], branch: str) -> str:
    changes = "\n".join(f"- {p} (managed block)" for p in paths)
    advisory_line = (f"Advisory (not applied): {advisory} → see report" if advisory else "")
    return (
        f"## Visibility Kit ({', '.join(applied)}) — by AUREM\n"
        f"Adds: {applied}\n"
        f"{advisory_line}\n"
        f"### Changes\n{changes}\n"
        f"### Revert\ngh branch delete {branch}\n"
        f"_Created by AUREM. No user copy was modified. "
        f"Training-bot choices per settings._\n"
    )
