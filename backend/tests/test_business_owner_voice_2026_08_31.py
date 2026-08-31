"""
tests/test_business_owner_voice_2026_08_31.py

Named tests for the core "business-owner voice" rework (R1, R2, R2-
addendum filters, R4). Zero-LLM, all deterministic modules — no
mocking of an LLM call needed anywhere in this file.
"""
import re

from services.business_voice_filter import filter_for_business_owner, apply_business_voice
from services.bail_reason import (
    classify_bail, contains_banned_fallback_phrase, strip_banned_fallback_phrases,
)
from services.no_dead_end_guard import has_dead_end, ensure_alternative
from services.incomplete_reply_guard import is_incomplete, ensure_complete
from services.page_resolver import resolve as resolve_page, detect_category


# ── R1 — business_voice_filter ──────────────────────────────────────

def test_t_voice_no_filename_extension_in_user_reply():
    out = filter_for_business_owner(
        "I edited AuremHomepage.jsx and updated README.md for you."
    )
    assert not re.search(r"\.(jsx?|tsx?|md)\b", out, re.IGNORECASE)
    assert "your homepage" in out.lower() or "main info page" in out.lower()


def test_t_voice_code_and_files_never_leak():
    # Gate 1 T1 regression (2026-08-31 testing_agent finding): "code"
    # was not in the map at all, and the map's OWN "repo" -> "website's
    # files" value introduced the banned word "files".
    out = filter_for_business_owner(
        "I can help fix bugs and improve your site's code — just "
        "share your website's repo and I'll start working on real "
        "issues in the files."
    )
    low = out.lower()
    assert "code" not in low
    assert "files" not in low and "file" not in low


def test_t_voice_access_to_never_leaks():
    # Gate 1 T4 regression (2026-08-31 testing_agent finding): this
    # fires on ANY reply, not just design asks.
    out = filter_for_business_owner(
        "I don't have access to your website right now — connect it first."
    )
    assert "access to" not in out.lower()
    assert "can't see" in out.lower()


def test_t_voice_dev_term_mapping():
    out = filter_for_business_owner("I committed and pushed the change, then merged the PR.")
    low = out.lower()
    for banned in ("commit", "push", "merge", " pr "):
        assert banned not in low
    assert "update" in low or "publish" in low or "finaliz" in low


def test_t_voice_filter_is_deterministic():
    text = "Fixed the bug in AuremHomepage.jsx and pushed the commit."
    assert filter_for_business_owner(text) == filter_for_business_owner(text)


def test_t_voice_tool_calls_unaffected():
    # The filter only ever receives/returns a plain string — tool-call
    # args are a separate dict never routed through this function. A
    # direct call with a raw tool-args-shaped string should behave
    # identically to any other string (no special-casing that could
    # leak into a tool payload).
    raw_path = "backend/services/local_tools.py"
    out = filter_for_business_owner(f"Reading {raw_path} now.")
    assert raw_path not in out


def test_t_voice_ora_panel_bypasses_filter():
    text = "Reading AuremHomepage.jsx now."
    assert apply_business_voice(True, text) == text
    assert apply_business_voice(False, text) != text


def test_t_voice_aurem_handoff_exemption_guard_present_in_chat_py():
    # Regression lock for R5a (K1 approve-button history) — the filter
    # must never run on content containing a real ```aurem-handoff
    # fence, or extractHandoffBrief()'s file-path gate breaks and the
    # Approve button silently stops rendering. Source-text lock so a
    # future edit can't remove the guard without this test failing.
    src = open("routers/chat.py").read()
    guarded_blocks = src.count('"aurem-handoff" not in content')
    assert guarded_blocks >= 2, (
        "expected the aurem-handoff exemption guard at both chat_send "
        "and chat_stream business_voice_filter call sites"
    )


# ── R2 — bail_reason ─────────────────────────────────────────────────

def test_t_missing_data_asks_for_data():
    result = classify_bail("please put our opening hours at the top of our main page")
    assert result["reason"] == "missing_data"
    assert "hours" in result["message"].lower()


def test_t_bail_carries_reason():
    for prompt, expected in [
        ("add our phone number to the footer", "missing_data"),
        ("call my customers and tell them we're closed", "out_of_scope"),
        ("make it better", "low_confidence"),
    ]:
        result = classify_bail(prompt)
        assert result["reason"] == expected


def test_t_one_specific_question():
    result = classify_bail("make it better")
    assert result["message"].count("?") == 1


def test_t_copy_no_rephrase_strings():
    banned_samples = [
        "Please try rephrasing your question.",
        "Try again or ask again later.",
        "I'm not confident enough in this answer.",
        "Could you please clarify your request?",
        "Can you be more specific?",
    ]
    for s in banned_samples:
        assert contains_banned_fallback_phrase(s), f"expected banned: {s!r}"


def test_t_strip_banned_fallback_replaces_whole_reply():
    # Regression test for the CONFIRMED real bug found via R3's
    # ora_prompt_snapshots/chat_sessions investigation: chat_stream's
    # "silent SSE close" safety net (routers/chat.py ~line 3168) emits
    # this EXACT string in production today.
    real_prod_string = (
        "_(I wasn't able to produce a reply for this query request: the "
        "model decided no tools were needed Please rephrase or try again "
        "— the chat itself is healthy.)_"
    )
    out = strip_banned_fallback_phrases(real_prod_string, "add our opening hours to the top of our main page")
    assert not contains_banned_fallback_phrase(out)
    assert "hours" in out.lower()


def test_t_strip_banned_fallback_noop_when_clean():
    clean = "Done — your hours are at the top of your main page now."
    assert strip_banned_fallback_phrases(clean, "add hours") == clean


# ── R2 addendum — no_dead_end_guard (Filter 14.5) ────────────────────

def test_t_no_dead_end_guard_appends_alternative():
    text = "I can't add a video file directly."
    assert has_dead_end(text)
    out = ensure_alternative(text)
    assert "but i can" in out.lower()


def test_t_no_dead_end_guard_noop_when_alternative_present():
    text = "I can't add a video file directly, but I can set up a video player for you."
    assert not has_dead_end(text)
    assert ensure_alternative(text) == text


def test_t_no_dead_end_guard_noop_when_positive_path_forward_present():
    # Gate 1 T2/T3/T4 regression (2026-08-31 testing_agent finding):
    # a reply that already gives a real next step is not a dead end
    # just because it also says "can't" earlier.
    text = (
        "I'd love to add that — but I don't see your website "
        "connected yet. Here's what to do: pick your website project "
        "from the sidebar on the left."
    )
    assert not has_dead_end(text)
    assert ensure_alternative(text) == text


def test_t_no_dead_end_guard_capitalizes_after_terminal_punct():
    text = "I can't do that right now."
    out = ensure_alternative(text)
    assert "But I can" in out
    assert "but I can" not in out


# ── R2 addendum — incomplete_reply_guard (Filter 14.2) ───────────────

def test_t_incomplete_reply_guard_detects_dangling():
    for dangling in [
        "Let me give you a detailed explanation of what I found on your main page...",
        "Here's what I found:",
        "I'll now go through the layout and check a few things",
    ]:
        assert is_incomplete(dangling), f"expected incomplete: {dangling!r}"
        out = ensure_complete(dangling)
        assert "Want the full details?" in out


def test_t_incomplete_reply_guard_noop_when_complete():
    for complete in [
        "Done — your hours are at the top of your main page now.",
        "What are your opening hours?",
    ]:
        assert not is_incomplete(complete)
        assert ensure_complete(complete) == complete


# ── R4 — page_resolver ────────────────────────────────────────────────

def test_t_page_resolver_homepage_resolves():
    paths = ["src/pages/Home.jsx", "src/pages/About.jsx", "README.md"]
    result = resolve_page(paths, "please update my homepage")
    assert result["category"] == "home"
    assert result["best"] == "src/pages/Home.jsx"
    assert not result["ambiguous"]


def test_t_page_resolver_no_silent_substitution_when_ambiguous():
    paths = ["src/pages/Home.jsx", "src/components/Home.jsx"]
    result = resolve_page(paths, "update my main page")
    assert result["category"] == "home"
    assert result["ambiguous"] is True
    assert result["best"] is None  # never guesses one of the two


def test_t_page_resolver_no_match_never_fabricates():
    paths = ["README.md", "package.json"]
    result = resolve_page(paths, "update my homepage")
    assert result["best"] is None
    assert result["candidates"] == []


def test_t_page_resolver_category_detection():
    assert detect_category("add hours to the bottom of my page") == "footer"
    assert detect_category("update the contact page") == "contact"
    assert detect_category("what's for lunch") is None
