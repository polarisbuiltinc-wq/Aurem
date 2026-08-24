"""Verification for the low_confidence backfill script (2026-08-25)."""
from __future__ import annotations

import asyncio
import os
import sys

import pytest
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

# Ensure backend on path + env loaded
BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
load_dotenv(os.path.join(BACKEND_DIR, ".env"))

from scripts.backfill_low_confidence_council_logs_2026_08_25 import run as backfill_run  # noqa: E402
from services.response_confidence import FALLBACK_MESSAGE, response_seems_mismatched  # noqa: E402
from services import ora_council_retriever  # noqa: E402


def _db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client, client[os.environ["DB_NAME"]]


def test_dry_run_is_idempotent_no_new_matches():
    """Re-running dry-run must find 0 new matches (idempotency)."""
    res = asyncio.run(backfill_run(apply=False))
    assert res["dry_run"] is True
    assert res["total_flagged"] == 0, f"Expected 0 new matches on re-run, got: {res}"
    assert res["fallback_message_matches"] == 0
    assert res["mismatch_detector_matches"] == 0


def test_dry_run_does_not_write_to_db():
    """count_documents({low_confidence:True}) must not change across a dry-run."""
    async def _check():
        client, db = _db()
        try:
            before = await db["ora_council_logs"].count_documents({"low_confidence": True})
            await backfill_run(apply=False)
            after = await db["ora_council_logs"].count_documents({"low_confidence": True})
            return before, after
        finally:
            client.close()

    before, after = asyncio.run(_check())
    assert before == after, f"Dry-run mutated DB! before={before} after={after}"


def test_backfilled_rows_have_both_flags_and_are_genuine():
    """The 6 rows main agent flagged must have low_confidence + low_confidence_backfilled
    AND each must independently re-match one of the two detection signals."""
    async def _check():
        client, db = _db()
        try:
            docs = await db["ora_council_logs"].find(
                {"low_confidence_backfilled": True}
            ).to_list(length=1000)
            return docs
        finally:
            client.close()

    docs = asyncio.run(_check())
    assert len(docs) >= 1, "No backfilled docs found — main agent said 6 were flagged"
    print(f"\nFound {len(docs)} backfilled rows")

    fallback_ct = 0
    mismatch_ct = 0
    for d in docs:
        assert d.get("low_confidence") is True, f"Doc {d.get('_id')} missing low_confidence=True"
        assert d.get("low_confidence_backfilled") is True
        um = d.get("user_message") or ""
        fo = d.get("final_output") or ""
        is_fb = fo.strip() == FALLBACK_MESSAGE.strip()
        is_mm = (not is_fb) and response_seems_mismatched(um, fo)
        assert is_fb or is_mm, (
            f"FALSE POSITIVE: doc {d.get('_id')} matches neither signal.\n"
            f"user_message={um!r}\nfinal_output={fo[:200]!r}"
        )
        if is_fb:
            fallback_ct += 1
        else:
            mismatch_ct += 1
    print(f"Signal breakdown: fallback={fallback_ct}, mismatch={mismatch_ct}")


def test_quality_filter_excludes_all_backfilled():
    """_quality_filter must reject every backfilled doc."""
    async def _fetch():
        client, db = _db()
        try:
            return await db["ora_council_logs"].find(
                {"low_confidence_backfilled": True}
            ).to_list(length=1000)
        finally:
            client.close()

    docs = asyncio.run(_fetch())
    assert docs, "no backfilled docs to test"
    qf = ora_council_retriever._quality_filter
    for d in docs:
        assert qf(d) is False, f"_quality_filter allowed a backfilled doc: {d.get('_id')}"


def test_retriever_stats_sane():
    """Sanity check corpus size and modes after rebuilding index in this process."""
    async def _build_and_stat():
        client, db = _db()
        try:
            await ora_council_retriever._rebuild_index(db)
        finally:
            client.close()
        return ora_council_retriever.get_retriever_stats()

    stats = asyncio.run(_build_and_stat())
    print(f"\nretriever stats: {stats}")
    assert isinstance(stats, dict)
    corpus_rows = stats.get("corpus_rows") or stats.get("total_docs") or 0
    assert corpus_rows and corpus_rows > 0, f"empty corpus: {stats}"
    modes = stats.get("modes_indexed") or []
    assert modes, f"no modes indexed: {stats}"


def test_backfilled_ids_not_in_retriever_corpus():
    """After _rebuild_index(), backfilled docs must not appear in the in-memory corpus."""
    async def _fetch():
        client, db = _db()
        try:
            docs = await db["ora_council_logs"].find(
                {"low_confidence_backfilled": True}, {"_id": 1}
            ).to_list(length=1000)
            return {str(d["_id"]) for d in docs}
        finally:
            client.close()

    backfilled_ids = asyncio.run(_fetch())
    assert backfilled_ids

    # Try to introspect the in-memory corpus (best-effort — attribute names may vary)
    mod = ora_council_retriever
    candidates = [a for a in dir(mod) if "corpus" in a.lower() or "index" in a.lower()]
    print(f"\nRetriever internal attrs (corpus/index): {candidates}")
    # Whatever internal store exists, none of the backfilled _ids should be there.
    found_leak = []
    for attr in candidates:
        val = getattr(mod, attr, None)
        try:
            s = repr(val)
        except Exception:
            continue
        for _id in backfilled_ids:
            if _id in s:
                found_leak.append((attr, _id))
    assert not found_leak, f"Backfilled ids still in corpus: {found_leak}"
