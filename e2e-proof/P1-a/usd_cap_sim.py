"""
P1-a — USD cap cost simulation (R8 prep). NO real tokens spent, NO real
provider call made. Standalone harness, not part of the app; proof
artifact for /app/e2e-proof/P1-a/usd-cap-sim.log.
"""
import asyncio
import os
import sys
import uuid
import logging

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")


async def main():
    from motor.motor_asyncio import AsyncIOMotorClient
    from cto_services.db import set_db
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    set_db(db)

    from services import llm_rate_table, llm_usd_cap
    from services.ora_chat_v2 import llm_client

    uid = f"p1a-sim-{uuid.uuid4().hex[:8]}"
    await db.dev_users.insert_one({"user_id": uid, "tier": "free", "email": f"{uid}@sim.local"})

    print("=== P1-a: USD cap cost simulation (NO real tokens, NO real provider call) ===")
    rates = await llm_rate_table.get_rate_table(db)
    print(f"[1] Rate table loaded: qwen3.8-27b={rates['qwen3.8-27b']} qwen3.7-plus={rates['qwen3.7-plus']}")
    print("    Source: Alibaba Cloud Model Studio (DashScope) international pricing, cited 2026-08-28 (services/llm_rate_table.py)")

    await llm_usd_cap.set_usd_caps(db, per_plan={"free": 0.01})
    print("[2] Set free-tier cap to a tiny $0.01/mo for this simulation")

    await llm_usd_cap.record_usd_spend(
        db, user_id=uid, model="qwen3.8-27b", input_tokens=20000, output_tokens=2000, cost_usd=0.01)
    print("[3] Simulated a prior real usage row this month totalling exactly the $0.01 cap (no provider call made — direct ledger write)")

    async def fake_resolve(_db, role):
        return {"base_url": "https://example.invalid", "api_key": "sk-sim",
                "model": "qwen3.8-27b", "label": "sim", "source": "env"}
    llm_client._resolve = fake_resolve
    llm_client.is_mock = lambda: False

    class BoomClient:
        def __init__(self, *a, **kw):
            raise AssertionError("PROVIDER CLIENT CONSTRUCTED — CAP FAILED TO BLOCK")
    import openai
    openai.AsyncOpenAI = BoomClient

    print("[4] Calling the REAL stream_chat() choke point (MOCK_LLM forced off for this sim only) with a Free user already at cap...")
    events = []
    async for evt in llm_client.stream_chat(messages=[{"role": "user", "content": "hello"}], db=db, user_id=uid):
        events.append(evt)
    print(f"[5] Events received: {events}")
    err = next((e for e in events if e.get("type") == "error"), None)
    assert err is not None, "FAIL: no error event — cap did not block"
    assert err["error"] == "monthly_limit_reached"
    assert err["detail"] == "Monthly limit reached — upgrade to continue."
    print("[6] PASS — provider was never constructed (AssertionError would have fired above if it had been).")
    print(f"[7] PASS — human-readable block message confirmed: \"{err['detail']}\"")

    rows = await db[llm_usd_cap.LEDGER_COLLECTION].count_documents({"user_id": uid})
    assert rows == 1, f"expected exactly 1 ledger row (the pre-seeded one), got {rows} — zero tokens spent after block"
    print(f"[8] PASS — ledger row count for this user after the blocked call = {rows} (unchanged, zero new spend recorded)")

    events2 = await db.guardrail_events.find({"user_id": uid, "event": "GW_BLOCK_COST"}).to_list(length=10)
    assert len(events2) == 1
    print(f"[9] PASS — GW_BLOCK_COST guardrail event logged: {events2[0]['cap_kind']} cap_usd={events2[0]['cap_usd']} spend_usd={events2[0]['spend_usd']}")

    await db.dev_users.delete_one({"user_id": uid})
    await db[llm_usd_cap.LEDGER_COLLECTION].delete_many({"user_id": uid})
    await db.guardrail_events.delete_many({"user_id": uid})
    await llm_usd_cap.set_usd_caps(db, per_plan=dict(llm_usd_cap.DEFAULT_PER_PLAN_CAPS_USD))
    print("[10] Cleanup done, caps reset to defaults.")
    print("=== SIMULATION RESULT: PASS — pre-call USD cap blocks correctly, zero real tokens spent, human message shown, GW_BLOCK_COST logged ===")


asyncio.run(main())
