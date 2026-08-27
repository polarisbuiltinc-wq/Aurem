"""
test_iter2026_08_27_founder_offer_stale_preview_reap.py

Founder-reported: the "X spots remaining" counter must reflect real
usage, not abandoned previews. `/founder-offer/claim` reserves a spot
immediately (dry-run preview) — a user who previews and never confirms
(nor explicitly cancels) used to hold that spot forever with nothing
actually fixed, silently draining the promo pool.

Fix: `_reap_stale_previews()` expires `preview` claims older than
`_PREVIEW_EXPIRY_MINUTES` and restores their spot. Called on every
`/status` read (self-heals over time) and before `/claim` allocates a
new spot. Expired claims are excluded from `_user_claims()` so the
user's per-repo/per-user cap isn't held hostage by an abandoned claim.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from routers import founder_offer as fo


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def sort(self, *_a, **_k):
        return self

    async def to_list(self, length=None):
        return list(self._rows)[: length or len(self._rows)]


class _ClaimsColl:
    def __init__(self, rows):
        self.rows = rows

    def find(self, query):
        out = []
        for r in self.rows:
            ok = True
            for k, v in query.items():
                if isinstance(v, dict) and "$lt" in v:
                    if not (r.get(k) and r[k] < v["$lt"]):
                        ok = False
                elif isinstance(v, dict) and "$nin" in v:
                    if r.get(k) in v["$nin"]:
                        ok = False
                else:
                    if r.get(k) != v:
                        ok = False
            if ok:
                out.append(r)
        return _Cursor(out)

    async def find_one_and_update(self, query, update):
        for r in self.rows:
            if all(r.get(k) == v for k, v in query.items()):
                r.update(update.get("$set", {}))
                return r
        return None

    async def find_one(self, query):
        for r in self.rows:
            if all(r.get(k) == v for k, v in query.items()):
                return r
        return None

    async def insert_one(self, doc):
        self.rows.append(doc)


class _OfferColl:
    def __init__(self, doc):
        self.doc = doc
        self.dec_calls = 0

    async def update_one(self, query, update):
        if "spots_claimed" in query and isinstance(query["spots_claimed"], dict):
            if not (self.doc["spots_claimed"] > query["spots_claimed"].get("$gt", -1)):
                return
        inc = update.get("$inc", {})
        if "spots_claimed" in inc:
            self.doc["spots_claimed"] += inc["spots_claimed"]
            self.dec_calls += 1

    async def find_one_and_update(self, query, update, return_document=True):
        if "$setOnInsert" in update:
            return self.doc
        if "$expr" in query and self.doc["spots_claimed"] >= self.doc["total_spots"]:
            return None
        inc = update.get("$inc", {})
        if "spots_claimed" in inc:
            self.doc["spots_claimed"] += inc["spots_claimed"]
        return self.doc

    async def find_one(self, query):
        return self.doc


class _FakeDb:
    def __init__(self, claims_rows, offer_doc):
        self.user_seo_claims = _ClaimsColl(claims_rows)
        self.founder_offer = _OfferColl(offer_doc)


@pytest.mark.asyncio
async def test_reap_expires_stale_preview_and_restores_spot():
    old = datetime.now(timezone.utc) - timedelta(minutes=45)
    claims = [{"claim_id": "c1", "user_id": "u1", "repo_id": "r1",
               "fix_status": "preview", "created_at": old}]
    db = _FakeDb(claims, {"_id": "global", "total_spots": 500, "spots_claimed": 1,
                           "is_active": True})

    await fo._reap_stale_previews(db)

    assert claims[0]["fix_status"] == "expired"
    assert db.founder_offer.doc["spots_claimed"] == 0


@pytest.mark.asyncio
async def test_reap_leaves_fresh_preview_untouched():
    fresh = datetime.now(timezone.utc) - timedelta(minutes=5)
    claims = [{"claim_id": "c2", "user_id": "u1", "repo_id": "r1",
               "fix_status": "preview", "created_at": fresh}]
    db = _FakeDb(claims, {"_id": "global", "total_spots": 500, "spots_claimed": 1,
                           "is_active": True})

    await fo._reap_stale_previews(db)

    assert claims[0]["fix_status"] == "preview"
    assert db.founder_offer.doc["spots_claimed"] == 1


@pytest.mark.asyncio
async def test_reap_leaves_confirmed_completed_claims_untouched():
    old = datetime.now(timezone.utc) - timedelta(minutes=45)
    claims = [{"claim_id": "c3", "user_id": "u1", "repo_id": "r1",
               "fix_status": "completed", "created_at": old}]
    db = _FakeDb(claims, {"_id": "global", "total_spots": 500, "spots_claimed": 1,
                           "is_active": True})

    await fo._reap_stale_previews(db)

    assert claims[0]["fix_status"] == "completed"
    assert db.founder_offer.doc["spots_claimed"] == 1


@pytest.mark.asyncio
async def test_user_claims_excludes_expired_and_cancelled():
    claims = [
        {"claim_id": "c1", "user_id": "u1", "repo_id": "r1", "fix_status": "expired"},
        {"claim_id": "c2", "user_id": "u1", "repo_id": "r2", "fix_status": "cancelled"},
        {"claim_id": "c3", "user_id": "u1", "repo_id": "r3", "fix_status": "completed"},
    ]
    db = _FakeDb(claims, {"_id": "global", "total_spots": 500, "spots_claimed": 3,
                           "is_active": True})

    result = await fo._user_claims(db, "u1")

    assert len(result) == 1
    assert result[0]["claim_id"] == "c3"


@pytest.mark.asyncio
async def test_user_can_reclaim_repo_after_expiry():
    """A user whose preview expired isn't blocked from retrying the
    same repo — the expired claim shouldn't match `_find_existing_claim`
    (it only ever inspects `_user_claims()` output, which now excludes
    expired rows)."""
    claims = [{"claim_id": "c1", "user_id": "u1", "repo_id": "r1",
               "fix_status": "expired"}]
    db = _FakeDb(claims, {"_id": "global", "total_spots": 500, "spots_claimed": 0,
                           "is_active": True})

    existing = await fo._user_claims(db, "u1")
    assert fo._find_existing_claim(existing, "r1") is None
