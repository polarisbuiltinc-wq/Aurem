"""
services/faithfulness_judge.py — LLM-as-judge faithfulness check.

Extracted from services/reasoning_evals.py (2026-08-27, mechanical
split — no behaviour change) to keep that module under the platform's
file-size guard. Re-exported from `services.reasoning_evals` so all
existing import sites (`from services.reasoning_evals import
llm_faithfulness_check`) keep working unchanged.
"""
from __future__ import annotations

import json
import os
import re
import uuid

_JUDGE_SYSTEM = (
    "You are a strict FAITHFULNESS judge. Given a SOURCE document "
    "and an OUTPUT that claims to summarise/derive from it, decide "
    "whether every factual claim in the OUTPUT is directly supported "
    "by the SOURCE.\n\n"
    "Rules:\n"
    "  - A claim is FAITHFUL only if the source contains it explicitly "
    "or implies it via a straightforward inference.\n"
    "  - A claim is UNFAITHFUL if the output invented information "
    "(paths, function names, versions, quantities) that the source "
    "does not contain.\n"
    "  - Style/opinion words that add no facts are neutral — ignore.\n\n"
    "Respond with a SINGLE JSON object, no prose, matching this "
    "exact shape:\n"
    "  {\"verdict\": \"faithful\" | \"unfaithful\", "
    "\"unsupported_claims\": [<string>, ...], "
    "\"reasoning\": \"<one-sentence explanation>\"}"
)


async def llm_faithfulness_check(output: str,
                                    source: str,
                                    *,
                                    model: str = "claude-sonnet-4-5") -> dict:
    """Ask a Claude judge whether `output` is faithful to `source`.

    Returns {ok, verdict, unsupported_claims, reasoning, raw_response}.
    `ok` is True iff verdict == "faithful". `raw_response` is included
    so tests can print it on failure.

    Cost: ~1 message per call. Uses the Emergent LLM key from
    `EMERGENT_LLM_KEY`. Requires the key to be set; returns
    `{"ok": False, "verdict": "error", ...}` if not.

    SSOT-refactor (Feb 2026) — default model is Anthropic-native
    `claude-sonnet-4-5` (not the OpenRouter dotted `4.5`) because this
    call uses `emergentintegrations.LlmChat.with_model("anthropic",…)`
    which requires Anthropic's dash-date format. Fixed the earlier
    hard-coded `claude-sonnet-4-6` typo which never existed.
    """
    key = os.environ.get("EMERGENT_LLM_KEY")
    if not key:
        return {
            "ok": False,
            "verdict": "error",
            "unsupported_claims": [],
            "reasoning": "EMERGENT_LLM_KEY not set in env",
            "raw_response": None,
        }

    # Lazy import so a test-suite without the library still runs the
    # deterministic evaluators above.
    from emergentintegrations.llm.chat import LlmChat, UserMessage

    prompt = (
        f"SOURCE:\n<<<\n{source}\n>>>\n\n"
        f"OUTPUT (to judge):\n<<<\n{output}\n>>>\n\n"
        f"Return the JSON verdict now."
    )
    chat = (
        LlmChat(
            api_key=key,
            session_id=f"faithfulness-{uuid.uuid4().hex[:8]}",
            system_message=_JUDGE_SYSTEM,
        )
        .with_model("anthropic", model)
    )
    try:
        raw = await chat.send_message(UserMessage(text=prompt))
    except Exception as e:                                    # noqa: BLE001
        return {
            "ok": False,
            "verdict": "error",
            "unsupported_claims": [],
            "reasoning": f"judge call failed: {e!r}",
            "raw_response": None,
        }

    # Extract JSON from the raw response (may be wrapped in prose /
    # markdown fences — be permissive).
    match = re.search(r"\{[^{}]*\"verdict\"[^{}]*\}", raw, re.DOTALL)
    if not match:
        return {
            "ok": False,
            "verdict": "unparseable",
            "unsupported_claims": [],
            "reasoning": "judge returned no JSON block",
            "raw_response": raw,
        }
    try:
        j = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {
            "ok": False,
            "verdict": "unparseable",
            "unsupported_claims": [],
            "reasoning": "judge JSON did not parse",
            "raw_response": raw,
        }

    verdict = str(j.get("verdict", "")).lower().strip()
    return {
        "ok":                  (verdict == "faithful"),
        "verdict":             verdict,
        "unsupported_claims":  j.get("unsupported_claims") or [],
        "reasoning":           j.get("reasoning") or "",
        "raw_response":        raw,
    }
