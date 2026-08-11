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
    """Guard that the pre-dispatch commands + rules stay in the
    file. Rewritten 2026-02-12 after Emergent Support confirmed
    the pipeline is 'snapshot at build-start' (no SHA pinning),
    so the pre-dispatch discipline shifted from 'lock the SHA'
    to 'ensure intended commits land + no build in-flight'."""
    src = open(CHECKLIST_PATH).read()
    for token in (
        "git status --short",
        "git log --oneline",
        "emergent__send_to_deployer",
        # New (post-Support) rules:
        "no deploy is currently in-flight",  # don't dispatch while one is building
        "all INTENDED commits exist",         # ensure commits land pre-dispatch
        "Manage Publishes",                    # source of truth
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


def test_checklist_records_incidents_that_caused_it():
    """Historical log must survive — future agents need to know
    WHY the discipline exists so they don't relax it as
    'ceremony'."""
    src = open(CHECKLIST_PATH).read()
    # Historical log section present.
    assert "Historical log" in src
    # Incident types recorded (rewritten 2026-02-12 to cover all
    # three race patterns seen that day).
    assert "no-op" in src.lower()
    assert "snapshot-at-build-start race" in src.lower() or \
           "pipeline built commit ahead" in src.lower()


def test_checklist_documents_pipeline_model_and_source_of_truth():
    """After Emergent Support confirmed the pipeline is
    'snapshot at build-start' (no SHA pinning), the checklist
    MUST document this so future agents don't waste hours
    debugging SHA-in vs SHA-out races that are expected behavior.

    Also pins:
      • Manage Publishes → Overview as primary source of truth
        (not /version, which has the BUILD_INFO.txt lag issue)
      • The three channels that can mutate HEAD or trigger deploy
    """
    src = open(CHECKLIST_PATH).read()

    # Pipeline model must be documented.
    assert "snapshot at build-start" in src, (
        "Checklist must state the pipeline is 'snapshot at "
        "build-start' — this is what Emergent Support confirmed "
        "on 2026-02-12. Future agents will re-hit the same race "
        "confusion without this line."
    )

    # Manage Publishes must be called out as primary signal.
    assert "Manage Publishes" in src and "source of truth" in src.lower(), (
        "Manage Publishes → Overview must be documented as the "
        "primary source of truth for what shipped. /version is "
        "unreliable due to BUILD_INFO.txt lag (see Iter 314 fix)."
    )

    # All three channels documented.
    for channel_marker in (
        "Channel",
        "session-fork bookkeeping",
        "finish` auto-commit",
        "manual UI deploy",
    ):
        assert channel_marker in src, (
            f"Channel documentation token {channel_marker!r} "
            "missing. All three ways HEAD/deploy can be mutated "
            "must be spelled out or agents will be surprised by "
            "off-band changes to HEAD."
        )
