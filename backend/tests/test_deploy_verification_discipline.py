"""
Guard the deploy verification discipline.

The checklist is only useful if it stays discoverable. This
test pins:
  1. The checklist file exists at its documented path.
  2. PRD.md points to it in a "MANDATORY READ" section
     at the top (so a fork agent following the standard
     onboarding read hits the pointer before their first
     deploy).
  3. CHANGELOG.md's header explicitly references it.

If any of these break, a future agent might dispatch a
prod deploy without knowing the pre/post-flight discipline —
which is what caused both incidents on 2026-02-12.
"""

import os

CHECKLIST_PATH = "/app/memory/DEPLOY_VERIFICATION_CHECKLIST.md"


def test_checklist_file_exists_at_documented_path():
    assert os.path.isfile(CHECKLIST_PATH), (
        "Deploy verification checklist missing at %s — "
        "future agents will skip the pre/post-flight steps "
        "that were introduced after the 2026-02-12 pipeline "
        "race incidents. Either restore the file or update "
        "every reference to point at the new location."
        % CHECKLIST_PATH
    )


def test_checklist_contains_mandatory_pre_dispatch_steps():
    """Guard that the 4 pre-dispatch commands stay in the file
    (if a future agent 'simplifies' by removing them, that's
    exactly the discipline gap the checklist was created to
    prevent)."""
    src = open(CHECKLIST_PATH).read()
    for token in (
        "git status --short",
        "git log --oneline -1",
        "git diff HEAD",
        "emergent__send_to_deployer",
    ):
        assert token in src, (
            f"Pre-dispatch guard token {token!r} missing from "
            "DEPLOY_VERIFICATION_CHECKLIST.md — pre-dispatch "
            "discipline is incomplete."
        )


def test_prd_points_to_checklist_at_top():
    """PRD is the first file every fork agent reads during
    onboarding. If the checklist pointer isn't near the top,
    the agent's first prod deploy might skip the discipline."""
    src = open("/app/memory/PRD.md").read()
    # Header pointer must appear in the first 40 lines.
    head = "\n".join(src.splitlines()[:40])
    assert "DEPLOY_VERIFICATION_CHECKLIST.md" in head, (
        "PRD.md header must point at "
        "DEPLOY_VERIFICATION_CHECKLIST.md. Fork agents read "
        "PRD.md first — the pointer is the guarantee they "
        "hit the checklist before their first deploy."
    )
    assert "MANDATORY" in head, (
        "The pointer in PRD.md must say MANDATORY. Otherwise "
        "agents may treat it as suggested reading and skip."
    )


def test_changelog_header_references_checklist():
    src = open("/app/memory/CHANGELOG.md").read()
    head = "\n".join(src.splitlines()[:6])
    assert "DEPLOY_VERIFICATION_CHECKLIST.md" in head


def test_checklist_records_the_two_incidents_that_caused_it():
    """Historical log must survive — future agents need to know
    WHY the discipline exists so they don't relax it as
    'ceremony'."""
    src = open(CHECKLIST_PATH).read()
    # Historical log section present.
    assert "Historical log" in src
    # Both incident types recorded.
    assert "no-op" in src.lower()
    assert "pipeline built wrong ref" in src.lower() or \
           "pipeline shipped the wrong thing" in src.lower()
