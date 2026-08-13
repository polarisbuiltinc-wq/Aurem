"""
Iter 388y — Admin Panel Payments Accuracy fix (Item #35 · P0 slice).

Bugs closed:
  1. token-pnl endpoint returned `revenue_month:0, stripe_fees:0,
     net_revenue:0, net_profit:-ai_cost, margin_pct:0` — all
     hardcoded regardless of real Mongo state.
  2. overview-metrics used `status IN [paid,complete,completed,
     succeeded]` while list_payments used `payment_status='paid'` —
     two admin cards were on different SoTs, could disagree by
     an entire refund.
  3. list_payments computed total_revenue by summing amount over the
     visible-page 100 rows only — lifetime revenue silently truncated
     once we crossed 100 paid txns.
  4. Cost-per-1k table was 2024-era (deepseek $0.30 / maxx $0.65 /
     groq $0.03) with no rates for Claude Sonnet 5 / GPT-5.2 /
     Gemini 3 / glm-5.2 — unknown-agent calls silently priced at
     DeepSeek rate, inflating cost estimates.

Fix: single source of truth is `payment_status='paid'`, revenue
computed via aggregate on the WHOLE collection, cost table refreshed
with 2026 rates.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import MagicMock


def _override_admin_dep(app):
    """Bypass router-level require_admin_dep gate for tests."""
    from cto_services.auth import require_admin_dep
    app.dependency_overrides[require_admin_dep] = lambda: None
    return require_admin_dep


class _FakeAggCursor:
    def __init__(self, docs): self._docs = list(docs)
    def __aiter__(self): return self
    async def __anext__(self):
        if not self._docs:
            raise StopAsyncIteration
        return self._docs.pop(0)


class _FakeFindCursor:
    def __init__(self, docs): self._docs = list(docs)
    def sort(self, *_a, **_k): return self
    def limit(self, n): self._docs = self._docs[:n]; return self
    async def to_list(self, n): return self._docs[:n]


class _FakeCollection:
    """Just enough shape to serve the aggregate+find calls hit by
    token_pnl / list_payments / overview-metrics."""
    def __init__(self, rows):
        self.rows = list(rows)

    async def count_documents(self, filter_):
        # Simple filter: only support {"payment_status":"paid"} +
        # {"status":"done"} + {"updated_at":{"$gte":X}} etc.  For this
        # test we only need "always 0 or len".
        matched = 0
        for r in self.rows:
            ok = True
            for k, v in filter_.items():
                if isinstance(v, dict):
                    if "$gte" in v and r.get(k, 0) < v["$gte"]:
                        ok = False; break
                    if "$in" in v and r.get(k) not in v["$in"]:
                        ok = False; break
                elif r.get(k) != v:
                    ok = False; break
            if ok:
                matched += 1
        return matched

    def aggregate(self, pipeline):
        # Interpret only the "$match then $group sum amount" shape used
        # by our endpoints.  Match filter is in pipeline[0]["$match"].
        match = (pipeline[0] or {}).get("$match", {}) if pipeline else {}
        group = next((s.get("$group") for s in pipeline if "$group" in s), {})

        def matches(r):
            for k, v in match.items():
                if isinstance(v, dict):
                    if "$gte" in v and r.get(k, 0) < v["$gte"]:
                        return False
                    if "$in" in v and r.get(k) not in v["$in"]:
                        return False
                elif r.get(k) != v:
                    return False
            return True

        filtered = [r for r in self.rows if matches(r)]
        result = {"_id": None, "sum": 0, "n": 0}
        for r in filtered:
            result["sum"] += float(r.get("amount") or 0)
            result["n"]   += 1
            # tokens / agent-grouped aggregate (token_pnl by agent)
            if group.get("_id") == "$agent_used":
                # multi-bucket; emit one doc per unique agent
                pass
        # If the group key is $agent_used, return one doc per agent.
        if group.get("_id") == "$agent_used":
            buckets = {}
            for r in filtered:
                a = r.get("agent_used") or "deepseek"
                buckets.setdefault(a, 0)
                buckets[a] += int(r.get("tokens_used") or 0)
            return _FakeAggCursor([{"_id": a, "tokens": t}
                                    for a, t in buckets.items()])
        return _FakeAggCursor([result] if filtered else [])

    def find(self, filter_, projection=None):
        return _FakeFindCursor(list(self.rows))


class _FakeDB:
    def __init__(self, seed):
        self._collections = {
            "cto_payments": _FakeCollection(seed.get("cto_payments", [])),
            "cto_tasks":    _FakeCollection(seed.get("cto_tasks", [])),
            "chat_sessions": _FakeCollection(seed.get("chat_sessions", [])),
        }

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self._collections:
            return self._collections[name]
        raise AttributeError(name)


@pytest.mark.asyncio
async def test_token_pnl_reports_real_revenue_from_paid_only(monkeypatch):
    """Revenue must reflect ONLY payment_status='paid' rows, NOT the
    various `status` values (checkout-session state)."""
    from main import app

    now = 1_800_000_000
    month_ago = now - 30 * 86400
    seed = {"cto_payments": [
        # paid, in-window — SHOULD count
        {"amount": 29.0, "payment_status": "paid", "status": "complete",
         "created_at": now - 86400},
        {"amount": 49.0, "payment_status": "paid", "status": "complete",
         "created_at": now - 2 * 86400},
        # complete session but payment NOT paid — MUST NOT count
        {"amount": 99.0, "payment_status": "expired", "status": "complete",
         "created_at": now - 86400},
        {"amount": 199.0, "payment_status": "pending", "status": "open",
         "created_at": now - 86400},
        # paid but out-of-window — MUST NOT count in month total
        {"amount": 999.0, "payment_status": "paid", "status": "complete",
         "created_at": month_ago - 86400},
    ]}
    fake_db = _FakeDB(seed)

    async def fake_admin(authorization=None):
        return {"user_id": "admin", "email": "a@x", "is_admin": True}

    monkeypatch.setattr("routers.admin_analytics._require_admin", fake_admin)
    dep = _override_admin_dep(app)
    monkeypatch.setattr("routers.admin_analytics.require_db", lambda: fake_db)
    monkeypatch.setattr("time.time", lambda: now)

    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as ac:
        r = await ac.get("/api/aurem-dev/admin/token-pnl",
                         headers={"Authorization": "Bearer x"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["revenue_month"] == 78.0   # 29 + 49 (paid, in window)
    assert body["paid_txn_month"] == 2
    # Fee estimate: 2.9% of 78 + $0.30 * 2 = $2.262 + $0.60 = $2.86
    assert body["stripe_fees"] == pytest.approx(2.86, abs=0.01)
    assert body["net_revenue"] == pytest.approx(75.14, abs=0.01)
    # margin_pct > 0 proves it's not the hardcoded-0 anymore
    assert body["margin_pct"] > 0


@pytest.mark.asyncio
async def test_token_pnl_returns_zero_revenue_when_no_paid_rows(monkeypatch):
    """When 0 paid rows exist, all revenue fields are honestly 0 —
    NOT the old hardcoded 0 that lied when paid rows did exist."""
    from main import app

    seed = {"cto_payments": [
        {"amount": 29.0, "payment_status": "pending", "status": "open",
         "created_at": 1_800_000_000 - 86400},
    ]}
    fake_db = _FakeDB(seed)

    async def fake_admin(authorization=None):
        return {"user_id": "admin", "email": "a@x"}
    monkeypatch.setattr("routers.admin_analytics._require_admin", fake_admin)
    dep = _override_admin_dep(app)
    monkeypatch.setattr("routers.admin_analytics.require_db", lambda: fake_db)
    monkeypatch.setattr("time.time", lambda: 1_800_000_000)

    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as ac:
        r = await ac.get("/api/aurem-dev/admin/token-pnl",
                         headers={"Authorization": "Bearer x"})
    body = r.json()
    assert body["revenue_month"] == 0.0
    assert body["stripe_fees"] == 0.0
    assert body["paid_txn_month"] == 0
    assert body["margin_pct"] == 0.0


@pytest.mark.asyncio
async def test_list_payments_lifetime_revenue_survives_100_row_cap(monkeypatch):
    """total_revenue must aggregate over ALL paid rows, not just the
    100-row visible page — bug was silent truncation at row 101."""
    from main import app

    seed = {"cto_payments":
        # 105 paid rows @ $10 each — the visible page (100) sums to
        # $1000, but lifetime revenue MUST be $1050.
        [{"amount": 10.0, "payment_status": "paid",
          "created_at": 1_800_000_000 - i} for i in range(105)]
        # + 5 pending rows that MUST NOT contribute
        + [{"amount": 999.0, "payment_status": "pending",
            "created_at": 1_800_000_000 - i} for i in range(5)]
    }
    fake_db = _FakeDB(seed)

    async def fake_admin(authorization=None):
        return {"user_id": "admin", "email": "a@x"}
    monkeypatch.setattr("routers.admin_payments._require_admin", fake_admin)
    dep = _override_admin_dep(app)
    monkeypatch.setattr("routers.admin_payments.require_db", lambda: fake_db)

    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as ac:
        r = await ac.get("/api/aurem-dev/admin/payments",
                         headers={"Authorization": "Bearer x"})
    body = r.json()
    assert body["total_revenue"] == 1050.0
    assert body["total_paid_count"] == 105
    assert body["count"] == 100        # visible page cap unchanged


@pytest.mark.asyncio
async def test_stripe_configured_reads_env_not_hardcoded(monkeypatch):
    """stripe_configured must reflect actual STRIPE_API_KEY presence,
    not the previous hardcoded False."""
    from main import app

    fake_db = _FakeDB({"cto_payments": []})
    async def fake_admin(authorization=None):
        return {"user_id": "admin", "email": "a@x"}
    monkeypatch.setattr("routers.admin_analytics._require_admin", fake_admin)
    dep = _override_admin_dep(app)
    monkeypatch.setattr("routers.admin_analytics.require_db", lambda: fake_db)
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_dummy")
    monkeypatch.setattr("time.time", lambda: 1_800_000_000)

    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as ac:
        r = await ac.get("/api/aurem-dev/admin/token-pnl",
                         headers={"Authorization": "Bearer x"})
    assert r.json()["stripe_configured"] is True

    monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as ac:
        r = await ac.get("/api/aurem-dev/admin/token-pnl",
                         headers={"Authorization": "Bearer x"})
    assert r.json()["stripe_configured"] is False
