"""
test_iter212m106_real_ship_and_sanitizer.py — Iter 212m-106

Locks the three P0 fixes from the user's prod test report:

1. Loop engine `_do_ship` actually calls `commit_files()` and only
   marks the loop COMPLETED after GitHub returns a real commit_sha.
   No more "phase_b_stub: True" silent no-ops.

2. RenderedMessage sanitizer strips BOTH fenced (```tool_call ```) AND
   XML-tag (<tool_call>...</tool_call>) internal markers.

3. ChatPanel dispatches `aurem:open-ship-modal` with the real
   commit_sha when the loop emits state=completed, phase=ship.

4. ShipConfirmModal handles the new `kind: "shipped" | "failed"` event
   payloads (jumps straight to shipped/error phase instead of the
   confirm phase).
"""
from pathlib import Path


def _read_fe(rel: str) -> str:
    return Path(f"/app/frontend/src/{rel}").read_text(encoding="utf-8")


def _read_be(rel: str) -> str:
    return Path(f"/app/backend/{rel}").read_text(encoding="utf-8")


def test_loop_ship_calls_real_github_commit_files():
    src = _read_be("services/loop_engine.py")
    # The legacy stub assignment must be gone. Comment / docstring
    # mentions of the historical "phase_b_stub" flag are fine.
    assert '"phase_b_stub": True' not in src, (
        "Loop ship still assigns the legacy phase_b_stub flag — the "
        "Phase C wiring (Iter 212m-106) was reverted or never landed."
    )
    # Must import the real writer
    assert "from services.github_api_writer import commit_files" in src
    # Must fall back to OAuth access_token if project github_token missing
    assert "github" in src and "access_token" in src
    # Must persist the commit metadata
    assert '"commit_sha":' in src or "commit_sha=" in src
    # Empty-files case must NOT fake a ship — must pause for user
    assert "Nothing to ship" in src
    # Failure helper exists for missing repo / bad creds / API errors
    assert "_fail_ship" in src


def test_sanitizer_strips_xml_style_tool_call_tags():
    src = _read_fe("components/RenderedMessage.jsx")
    # The XML-tag regex must include the user-reported leak: tool_call.
    # Confirm the closing-tag pattern and an orphan-open fallback both
    # exist (the orphan path catches partial streams).
    assert "tool_call" in src
    # The matched/closed regex
    assert "<\\s*(tool_call" in src
    # The orphan-open fallback regex (no closing tag)
    occurrences = src.count("<\\s*(tool_call")
    assert occurrences >= 2, (
        "Expected BOTH paired + orphan-open XML-tag strips for "
        "robust handling of cut streams."
    )


def test_chatpanel_dispatches_ship_modal_with_real_sha():
    src = _read_fe("components/ChatPanel.jsx")
    # Session G · Bucket A — Iter 328 hotfix replaced the completed+ship
    # ShipSuccessModal path with an inline LoopLiveFeed "Shipped {sha}"
    # affordance. The failed+ship modal branch remains (there's no
    # inline failure UX yet). New invariants:
    #   • `aurem:open-ship-modal` custom event still exists.
    #   • It ONLY fires for failed+ship (kind: "failed") — the
    #     shipped success path no longer emits a modal.
    #   • `aurem:shipped` event now carries the terminal success.
    assert 'aurem:open-ship-modal' in src
    assert 'state === "failed" && phase === "ship"' in src
    assert 'kind: "failed"' in src or '"failed"' in src
    # The inline replacement for the success path must exist.
    assert 'aurem:shipped' in src, (
        "Iter 328 hotfix moved the shipped success from a modal to an "
        "inline LoopLiveFeed row driven by 'aurem:shipped' custom "
        "event — that event must be dispatched."
    )
    # And the LEGACY completed+ship modal branch must be GONE (dead
    # code is worse than no code — a bug or a merge could reintroduce
    # a duplicate modal).
    assert (
        'state === "completed" && phase === "ship"'
        not in src
    ) or (
        'window.dispatchEvent(new CustomEvent("aurem:open-ship-modal"'
        not in src.split('state === "completed" && phase === "ship"')[-1].split("if (state ===")[0]
    ), (
        "The completed+ship branch that dispatches open-ship-modal "
        "was intentionally removed in Iter 328 hotfix — a "
        "reintroduction would cause a duplicate modal on top of the "
        "inline shipped-row."
    )


def test_ship_modal_handles_post_loop_payloads():
    src = _read_fe("components/ShipConfirmModal.jsx")
    # Iter 212m-106 — new `kind: "shipped" | "failed"` event branches.
    assert 'd.kind === "shipped"' in src
    assert 'd.kind === "failed"' in src
    # Shipped path jumps straight to the shipped phase
    assert 'setPhase("shipped")' in src
    # Failed path jumps to error phase
    assert 'setPhase("error")' in src


def test_send_button_has_explicit_onclick():
    """BUG 4 — mouse click must reliably trigger send() even when
    the form's implicit submit chain misbehaves (Safari overflow
    container quirk).

    Session G · Bucket A — Iter 212m-x cleaned up the defensive
    `currentTarget.disabled` guard because the button element is
    always guaranteed to have the disabled attribute via a react
    prop now; the callsite is `onClick={() => send()}` at line
    ~4517. Invariant preserved: the send button MUST have an
    explicit `onClick` calling `send()` so the pointer-driven path
    is always available."""
    src = _read_fe("components/ChatPanel.jsx")
    # Send button MUST have explicit onClick calling send() — this
    # is the Safari-overflow workaround, semantically preserved.
    import re
    m = re.search(r"onClick=\{\(\)\s*=>\s*send\(\s*[^)]*\)\s*\}", src)
    assert m is not None, (
        "The chat-send button must have an explicit onClick={() => "
        "send()} handler — Safari's overflow-container quirk breaks "
        "the implicit form-submit chain otherwise (see Iter 212m-106 "
        "BUG 4 for the original report)."
    )


def test_composer_placeholder_is_unified():
    """BUG 7 — both prompt and loop modes use the same placeholder."""
    src = _read_fe("components/ChatPanel.jsx")
    # Session G · Bucket A — the exact string was extended from
    # "Ask ORA to build, fix, or scan..." → "Ask ORA to build, fix,
    # or scan… (type / for scan commands)" (unicode ellipsis + a
    # slash-command hint). Same UNIFICATION intent; substring check
    # tolerates the extension without loosening the "unified across
    # modes" invariant. The legacy separate-per-mode string must
    # still be absent.
    assert "Ask ORA to build, fix, or scan" in src
    # The old "Describe the feature / fix" string is gone.
    assert "Describe the feature / fix" not in src
