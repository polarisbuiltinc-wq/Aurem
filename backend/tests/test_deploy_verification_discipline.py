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


def test_checklist_has_mandatory_sha_hard_stop_step():
    """The permanent-fix step added on 2026-02-12 after Incident 2:
    every deploy dispatch MUST be followed by a SHA-in-vs-SHA-out
    check, and any mismatch is a HARD STOP. This test guards
    that the step:
      (a) is called out as MANDATORY / PERMANENT, not situational
      (b) explicitly says STOP / do not dispatch on mismatch
      (c) contains a bash example of the comparison
      (d) preserves the 'verification-by-luck is not verification'
          principle so a future agent doesn't relax it
    """
    src = open(CHECKLIST_PATH).read()

    # (a) — the step must self-identify as mandatory + permanent.
    assert "MANDATORY PERMANENT STEP" in src or \
           "MANDATORY, PERMANENT STEP" in src, (
        "SHA hard-stop step must self-identify as a "
        "MANDATORY PERMANENT STEP. If a future refactor "
        "renamed it to 'recommended' or 'situational', "
        "restore the language."
    )

    # (b) — mismatch must trigger STOP + do-not-dispatch + report.
    for token in ("HARD STOP", "Do NOT dispatch", "Report to founder"):
        assert token in src, (
            f"Hard-stop guard token {token!r} missing from "
            "checklist — the abort semantics are what makes "
            "the step non-optional. Restore it."
        )

    # (c) — bash example of the SHA comparison must be present.
    assert 'EXPECTED_PREFIX=${EXPECTED_SHA:0:12}' in src or \
           'EXPECTED_PREFIX=${EXPECTED_SHA' in src, (
        "The SHA comparison bash example must be present — "
        "otherwise the step becomes a suggestion rather than "
        "an executable procedure."
    )

    # (d) — the rationale ('verification-by-luck is not
    # verification') must survive so future agents don't
    # relax it after a stretch of good deploys.
    assert "Verification-by-luck is not verification" in src, (
        "The rationale sentence must survive verbatim. It's "
        "the principle that stops a future agent from arguing "
        "'we haven't hit a mismatch in a while, this is "
        "over-engineered'."
    )
