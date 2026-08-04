"""
Focused runtime probe for the founder-reported global self-heal cap bug.

It runs LoopEngine._do_verify() with synthetic failing verify/heal functions
so no LLM, GitHub, or Mongo service is needed. Contract under test:
after MAX_SELF_HEALS=2 total failed heals, the loop must be terminal FAILED,
not PAUSED_FOR_USER.
"""
from __future__ import annotations

import asyncio
import sys
import types

sys.path.insert(0, "/app/backend")


class FakeCollection:
    def __init__(self):
        self.rows = []

    async def update_one(self, *args, **kwargs):
        self.rows.append(("update_one", args, kwargs))

    async def insert_one(self, doc):
        self.rows.append(("insert_one", doc))

    async def find_one(self, *args, **kwargs):
        return None


class FakeDB:
    def __init__(self):
        self.loop_sessions = FakeCollection()
        self.loop_errors = FakeCollection()
        self.loop_events = FakeCollection()
        self.loop_backups = FakeCollection()
        self.dev_users = FakeCollection()
        self.chat_sessions = FakeCollection()


async def always_failing_verify(file_objs):
    return {
        "ok": False,
        "results": [
            {
                "path": f["path"],
                "ok": False,
                "stdout": "E999 synthetic lint failure",
                "stderr": "",
            }
            for f in file_objs
        ],
        "errors": [f"{f['path']}: E999 synthetic lint failure" for f in file_objs],
    }


class FakeHealer:
    async def heal(self, **kwargs):
        # Return changed-but-still-invalid content; verify stays failing.
        return {"status": "retry", "output": "still invalid"}


class FakeParliament:
    def __init__(self, db=None):
        self.healer = FakeHealer()


async def main():
    loop_verify = types.ModuleType("services.loop_verify")
    loop_verify.verify_files = always_failing_verify
    loop_verify.self_heal = lambda *a, **k: None
    sys.modules["services.loop_verify"] = loop_verify

    parliament_mod = types.ModuleType("core.parliament")
    parliament_mod.Parliament = FakeParliament
    sys.modules["core.parliament"] = parliament_mod

    from services.loop_engine import LoopEngine, LoopState, MAX_SELF_HEALS

    engine = LoopEngine(
        FakeDB(),
        loop_id="probe_loop_global_heal_cap",
        user_id="probe_user",
        project_id="probe_project",
        user_message="synthetic failing task",
    )
    engine.context["submitted_files"] = [{"path": "bad.py", "content": "bad"}]

    await engine._do_verify()

    events = []
    while not engine.queue.empty():
        events.append(await engine.queue.get())

    print("MAX_SELF_HEALS=", MAX_SELF_HEALS)
    print("final_state=", engine.state.value)
    print("final_phase=", engine.phase)
    print("total_heal_attempts=", engine.context.get("total_heal_attempts"))
    print("events=")
    for ev in events:
        print({
            "state": ev.get("state"),
            "phase": ev.get("phase"),
            "message": ev.get("message"),
            "data": ev.get("data"),
            "requires_user_action": ev.get("requires_user_action"),
        })

    assert engine.context.get("total_heal_attempts") == MAX_SELF_HEALS
    assert engine.state == LoopState.FAILED, (
        "BUG STILL PRESENT: after exactly MAX_SELF_HEALS failed heals, "
        f"expected FAILED but got {engine.state.value}"
    )


if __name__ == "__main__":
    asyncio.run(main())