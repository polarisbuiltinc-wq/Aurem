"""Regression — P3 ("Show the Outcome, Never the Engine"), 2026-08-27.

P3a: "ORA remembers this" chip — plain-wording trust indicator on the
explain branch (rides `explain_plain_english_v1`, no sibling flag —
documented choice), shown only when `council_recalled > 0`.

P3b: Quiet Leak Digest — weekly, reuses the existing audit-spine
collections (`ora_audit`, `loop_run_log`) + `daily_digest.py`'s
Resend helper.

source_of_truth: this is a brand-contract surface — "show a trust
outcome, not engine internals" — same governance tier as the other
P0-era brand-contract tests.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]


def _chatpanel_src() -> str:
    path = _REPO / "frontend" / "src" / "components" / "ChatPanel.jsx"
    if not path.is_file():
        path = _REPO.parent / "app" / "frontend" / "src" / "components" / "ChatPanel.jsx"
    return path.read_text()


# ── P3a — "ORA remembers this" chip ─────────────────────────────────────
@pytest.mark.source_of_truth
class TestOraRemembersChip:
    def test_ora_remembers_this_is_plain_wording(self):
        """The plain chip must say 'ORA remembers this' — no count, no
        'council'/'RAG'/'few-shot' engine wording anywhere near it.
        Scoped to the actual rendered <div>...</div> (not the design
        comment above it, which legitimately NAMES the banned words
        for documentation purposes)."""
        src = _chatpanel_src()
        idx = src.find("ora-remembers-chip-")
        assert idx > -1, "P3a chip data-testid not found in ChatPanel.jsx"
        div_end = src.find("</div>", idx)
        assert div_end > -1
        block = src[idx: div_end + len("</div>")]
        assert "ORA remembers this" in block
        for banned in ("council", "rag", "few-shot", "self-learning", "retriever"):
            assert banned not in block.lower(), (
                f"P3a chip block leaked engine wording: {banned!r}"
            )

    def test_chip_gated_on_plain_english_contract_and_recall_count(self):
        """Structural proof both conditions gate the SAME block: the
        plain-english flag AND councilRecalled > 0 — not just one."""
        src = _chatpanel_src()
        idx = src.find("ora-remembers-chip-")
        assert idx > -1
        preceding = src[max(0, idx - 500): idx]
        assert "m.plainEnglishContractActive" in preceding
        assert "(m.councilRecalled || 0) > 0" in preceding

    def test_absent_when_recall_count_is_zero(self):
        """Both the P3a chip AND the legacy detailed caption share the
        same `(m.councilRecalled || 0) > 0` guard, so recall_count==0
        renders NEITHER — verified structurally (no JS runtime here)."""
        src = _chatpanel_src()
        chip_idx = src.find("ora-remembers-chip-")
        legacy_idx = src.find("council-recall-caption-")
        assert chip_idx > -1 and legacy_idx > -1
        for idx in (chip_idx, legacy_idx):
            preceding = src[max(0, idx - 500): idx]
            assert "(m.councilRecalled || 0) > 0" in preceding

    def test_legacy_caption_only_renders_when_plain_english_not_active(self):
        """The original Iter 212m-78 detailed caption must NOT double-
        render alongside the new plain chip on the same turn."""
        src = _chatpanel_src()
        legacy_idx = src.find("council-recall-caption-")
        assert legacy_idx > -1
        preceding = src[max(0, legacy_idx - 500): legacy_idx]
        assert "!m.plainEnglishContractActive" in preceding

    def test_ondone_wires_plain_english_contract_active_from_backend(self):
        """The SSE done-frame field `plain_english_contract_active`
        must actually reach `m.plainEnglishContractActive` — otherwise
        the chip can never gate correctly."""
        src = _chatpanel_src()
        assert "plainEnglishContractActive: !!d.plain_english_contract_active" in src

    def test_chip_has_fade_in_animation_class(self):
        """2026-08-27 chip polish — the chip carries the fade-in class,
        and the div is still the one gated on both conditions (not a
        stray/duplicate div elsewhere)."""
        src = _chatpanel_src()
        idx = src.find("ora-remembers-chip-")
        assert idx > -1
        div_end = src.find("</div>", idx)
        block = src[max(0, idx - 200): div_end]
        assert 'className="aurem-remembers-chip"' in block

    def test_fade_in_animation_respects_reduced_motion(self):
        """The CSS animation must have a `prefers-reduced-motion:
        reduce` override that disables it — no forced motion for
        users who opted out at the OS level."""
        css_path = _REPO / "frontend" / "src" / "index.css"
        css = css_path.read_text()
        idx = css.find(".aurem-remembers-chip")
        assert idx > -1, "fade-in class not defined in index.css"
        assert "animation: aurem-remembers-fade-in" in css[idx: idx + 200]
        reduced_idx = css.find("prefers-reduced-motion", idx)
        assert reduced_idx > -1
        reduced_block = css[reduced_idx: reduced_idx + 200]
        assert ".aurem-remembers-chip" in reduced_block
        assert "animation: none" in reduced_block

    def test_fade_in_duration_is_subtle_not_flashy(self):
        """Founder asked for ~200-300ms, nothing flashy — guard against
        a future edit accidentally making this a long/looping animation."""
        css_path = _REPO / "frontend" / "src" / "index.css"
        css = css_path.read_text()
        idx = css.find("@keyframes aurem-remembers-fade-in")
        assert idx > -1
        rule_block = css[idx: css.find(".aurem-remembers-chip {", idx) + 200]
        match = re.search(r"aurem-remembers-fade-in\s+(\d+)ms", rule_block)
        assert match, "could not find animation duration"
        duration_ms = int(match.group(1))
        assert 150 <= duration_ms <= 400, f"duration {duration_ms}ms is outside the subtle 150-400ms range"
        assert "infinite" not in rule_block, "chip fade-in must play once, not loop"


# ── P3b — Quiet Leak Digest ──────────────────────────────────────────────
class _FakeColl:
    def __init__(self, docs):
        self._docs = docs

    async def count_documents(self, query):
        field_map = {
            "extra.leak_stripped": lambda d: (d.get("extra") or {}).get("leak_stripped") is True,
            "extra.recall_candidate": lambda d: (d.get("extra") or {}).get("recall_candidate") is True,
            "kind": lambda d: d.get("kind") == query.get("kind"),
        }
        n = 0
        for d in self._docs:
            ok = True
            for key, pred in field_map.items():
                if key in query and not pred(d):
                    ok = False
                    break
            if not ok:
                continue
            ts_field = "timestamp" if "timestamp" in d else "created_at"
            ts_query = query.get(ts_field) or query.get("created_at") or query.get("timestamp")
            if ts_query:
                ts = d.get(ts_field)
                if isinstance(ts, datetime) and isinstance(ts_query.get("$gte"), str):
                    continue  # type mismatch guard, shouldn't happen with correct fixtures
                if "$gte" in ts_query and ts < ts_query["$gte"]:
                    continue
                if "$lt" in ts_query and ts >= ts_query["$lt"]:
                    continue
            n += 1
        return n


class _FakeDB:
    def __init__(self, ora_audit_docs, loop_run_log_docs):
        self.ora_audit = _FakeColl(ora_audit_docs)
        self.loop_run_log = _FakeColl(loop_run_log_docs)


def _iso(dt):
    return dt.isoformat()


class TestQuietLeakDigest:
    def test_counts_split_correctly_into_this_week_vs_last_week(self):
        from services.leak_digest import build_leak_digest

        now = datetime.now(timezone.utc)
        ora_docs = [
            {"extra": {"leak_stripped": True}, "timestamp": _iso(now - timedelta(days=2))},
            {"extra": {"leak_stripped": True}, "timestamp": _iso(now - timedelta(days=3))},
            {"extra": {"leak_stripped": True}, "timestamp": _iso(now - timedelta(days=10))},  # last week
            {"extra": {"recall_candidate": True}, "timestamp": _iso(now - timedelta(days=1))},
        ]
        loop_docs = [
            {"kind": "internal_fault_not_user", "created_at": now - timedelta(days=1)},
        ]
        db = _FakeDB(ora_docs, loop_docs)
        d = asyncio.run(build_leak_digest(db))
        assert d["leak_stripped"]["this_week"] == 2
        assert d["leak_stripped"]["last_week"] == 1
        assert d["recall_candidate"]["this_week"] == 1
        assert d["internal_fault"]["this_week"] == 1

    def test_spike_flag_fires_above_3x_week_over_week(self):
        from services.leak_digest import build_leak_digest

        now = datetime.now(timezone.utc)
        ora_docs = (
            [{"extra": {"leak_stripped": True}, "timestamp": _iso(now - timedelta(days=1))}
             for _ in range(4)]
            + [{"extra": {"leak_stripped": True}, "timestamp": _iso(now - timedelta(days=10))}]
        )
        db = _FakeDB(ora_docs, [])
        d = asyncio.run(build_leak_digest(db))
        assert d["spike_flag"] is not None
        assert "up" in d["spike_flag"]

    def test_no_spike_flag_when_last_week_was_zero(self):
        """Going from 0 → 1 is a first occurrence, not a 'spike' —
        must not trigger unnecessary urgency (founder's explicit note)."""
        from services.leak_digest import build_leak_digest

        now = datetime.now(timezone.utc)
        ora_docs = [{"extra": {"leak_stripped": True}, "timestamp": _iso(now - timedelta(days=1))}]
        db = _FakeDB(ora_docs, [])
        d = asyncio.run(build_leak_digest(db))
        assert d["spike_flag"] is None

    def test_render_text_is_plain_and_three_to_five_lines(self):
        from services.leak_digest import build_leak_digest, _render_text

        now = datetime.now(timezone.utc)
        ora_docs = [
            {"extra": {"leak_stripped": True}, "timestamp": _iso(now - timedelta(days=1))}
            for _ in range(4)
        ] + [
            {"extra": {"recall_candidate": True}, "timestamp": _iso(now - timedelta(days=1))}
            for _ in range(12)
        ]
        db = _FakeDB(ora_docs, [])
        d = asyncio.run(build_leak_digest(db))
        body = _render_text(d)
        lines = [l for l in body.split("\n") if l.strip()]
        assert 3 <= len(lines) <= 5
        assert "4" in body and "12" in body
        for banned in ("council_recalled", "internal_fault_not_user", "ora_audit", "loop_run_log"):
            assert banned not in body

    def test_run_once_reuses_daily_digest_send_helper_not_duplicated(self):
        """Reuse proof: _run_once imports `_send_via_resend` from
        daily_digest.py rather than re-implementing a Resend call."""
        import inspect
        from services import leak_digest
        src = inspect.getsource(leak_digest._run_once)
        assert "from services.daily_digest import _send_via_resend" in src
