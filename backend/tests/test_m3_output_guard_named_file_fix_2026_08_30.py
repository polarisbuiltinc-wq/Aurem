"""
tests/test_m3_output_guard_named_file_fix_2026_08_30.py — M3 fix
(2026-08-30, founder-directed, root cause found during M2's fence-rate
retest). `services/output_guard.py`'s bare-file-path redaction used to
be a blanket strip (any file path -> "a project file"), even for a
file the USER themselves explicitly named in the current turn — this
produced self-contradictory replies ("the file `a project file` does
not exist") and contributed to a real ship-fence miss.

Fix: context-aware exemption — a file named in the user's OWN message
this turn is exempt from the redaction; every other path (anything the
model surfaces on its own, never mentioned by the user) is still
redacted, unchanged. Secret/token-shaped strings are UNCONDITIONALLY
still redacted regardless of this exemption (independent check).

Named tests:
  t_output_guard_keeps_user_named_file
  t_output_guard_still_redacts_secrets
  t_output_guard_fenced_reply_survives
"""
from __future__ import annotations

from services.output_guard import extract_named_files, strip_machinery_leak


def test_t_output_guard_keeps_user_named_file():
    user_prompt = "Can you check README.md and tell me if it's out of date?"
    named = extract_named_files(user_prompt)
    assert "README.md" in named

    reply = "I checked README.md and it looks current, no changes needed."
    clean, stripped = strip_machinery_leak(
        reply, universal_only=False, user_named_files=named,
    )
    assert "README.md" in clean
    assert "a project file" not in clean


def test_t_output_guard_redacts_unnamed_file():
    """Regression guard: a path the model surfaces on its own — never
    mentioned by the user — is still redacted, unchanged behavior."""
    user_prompt = "What does this repo do?"
    named = extract_named_files(user_prompt)
    reply = "I found the issue in services/response_confidence.py."
    clean, stripped = strip_machinery_leak(
        reply, universal_only=False, user_named_files=named,
    )
    assert "response_confidence.py" not in clean
    assert "a project file" in clean
    assert stripped is True


def test_t_output_guard_still_redacts_secrets():
    """A secret/token-shaped string is redacted regardless of the
    user-named-file exemption — independent check, never bypassed."""
    reply = "Found a leaked key: AKIAABCDEFGHIJKLMNOP in your config."
    clean, stripped = strip_machinery_leak(
        reply, universal_only=True, user_named_files={"config.py"},
    )
    assert "AKIAABCDEFGHIJKLMNOP" not in clean
    assert "[redacted credential]" in clean
    assert stripped is True

    reply2 = "Your DB string is mongodb+srv://user:pass@cluster0.example.com/db"
    clean2, stripped2 = strip_machinery_leak(reply2, universal_only=True)
    assert "mongodb+srv://" not in clean2
    assert "[redacted connection string]" in clean2


def test_t_output_guard_fenced_reply_survives():
    """A reply that both names a user-requested file AND carries a
    real ```aurem-handoff fence must keep the fence syntactically
    intact (the guard never runs on fenced content at all per
    routers/chat.py's `"aurem-handoff" not in content` gate — this
    proves strip_machinery_leak itself doesn't mangle fence markup if
    ever called on it directly)."""
    user_prompt = "ship a fix to README.md"
    named = extract_named_files(user_prompt)
    reply = (
        "Updated README.md.\n"
        "```aurem-handoff\n"
        '{"file": "README.md", "diff": "+ fixed"}\n'
        "```"
    )
    clean, _ = strip_machinery_leak(reply, universal_only=False, user_named_files=named)
    assert "```aurem-handoff" in clean
    assert '"file": "README.md"' in clean
    assert "README.md" in clean  # user-named file kept real in the prose line too


def test_t_output_guard_m3_e2e_combined_no_llm_no_network():
    """M3 close-out — one pure string-in/string-out test proving all
    three M3 guarantees TOGETHER in a single reply, deterministically
    (zero LLM, zero network — a real model/mock-chat round-trip is
    NOT required to prove this fix, only to exercise it live). This
    closes the E2E evidence gap noted in the M2 retest: the earlier
    live mock-chat check didn't happen to produce a reply containing
    README.md or a secret, so it proved nothing either way about this
    fix specifically. This test constructs that exact reply directly."""
    user_prompt = "Can you update README.md and check config.py for me?"
    named = extract_named_files(user_prompt)
    assert "README.md" in named

    reply = (
        "Updated README.md. While I was in there I noticed a leaked "
        "credential: AKIAABCDEFGHIJKLMNOP — you should rotate that.\n"
        "```aurem-handoff\n"
        '{"file": "README.md", "diff": "+ fixed typo"}\n'
        "```"
    )
    clean, stripped = strip_machinery_leak(reply, universal_only=False, user_named_files=named)

    # (a) the user-named file survives by its real name
    assert "README.md" in clean
    assert "a project file" not in clean
    # (b) the secret-shaped token is redacted regardless of (a)
    assert "AKIAABCDEFGHIJKLMNOP" not in clean
    assert "[redacted credential]" in clean
    # (c) the aurem-handoff fence block is intact after the guard runs
    assert "```aurem-handoff" in clean
    assert '"file": "README.md"' in clean
    assert stripped is True
