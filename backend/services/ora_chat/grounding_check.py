"""
services/ora_chat/grounding_check.py — Iter 212m-254

CHEAP post-response grounding check. Runs after every ORA reply that
had a retrieval context (deep-research fan-out OR compact-tree only)
and flags "specific claims" in the reply that aren't grounded in the
retrieved context.

Design (deliberately conservative):
  - Pure regex extraction — NO LLM calls in the fast path. Adding an
    LLM per response would multiply cost + latency.
  - Only flags SPECIFIC, VERIFIABLE claims:
      * `test_iter*.py` and other named test files
      * paths ending in `.py` / `.jsx` / `.tsx` / `.ts` / `.js`
      * symbols in backticks that look like function/class names
        (`snake_case()` or `CamelCase`)
      * common cron/service filename patterns (`*_cron.py`,
        `*_service.py`)
  - A claim is "grounded" iff its exact string appears in EITHER the
    retrieved context (fan-out results) OR the compact-tree/system-
    highlights blocks that were in the system prompt.
  - Anything ungrounded → written to Mongo collection
    `ora_hallucination_log` with the query + reply + evidence.

Later layers (Iter 212m-255, 212m-256) will:
  - Batch-classify the log every 20 entries via DeepSeek V3.
  - Surface founder-approved patterns as candidate house rules.

NEVER auto-inject rules — human-in-the-loop for the promotion step.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Iterable, Optional

from cto_services.db import get_db

logger = logging.getLogger(__name__)

# ─── Claim extractors ────────────────────────────────────────────
# Only match tokens that look like SPECIFIC identifiers (not any word).
# Regex is intentionally strict to keep false-positive rate low.
_RE_PY_TEST      = re.compile(r"\btest_iter[0-9a-z_]+\.py\b")
_RE_PY_FILE      = re.compile(r"\b[A-Za-z_][A-Za-z0-9_/-]*\.(?:py|jsx|tsx|ts|js)\b")
_RE_SYMBOL_BT    = re.compile(r"`([A-Za-z_][A-Za-z0-9_]{2,})(?:\(\))?`")
_RE_CRON_LIKE    = re.compile(r"\b[a-z_]+_(?:cron|scheduler|watcher|worker)\.py\b")

# Stop-list of well-known false positives — words that look like symbols
# but are conversational or generic (Python keywords, common vars).
_SYMBOL_STOP: frozenset = frozenset({
    "None", "True", "False", "Exception", "self", "cls",
    "async", "await", "yield", "return", "import", "from",
    "GET", "POST", "PUT", "DELETE", "PATCH", "HTTP",
    "JSON", "HTML", "URL", "API", "SDK", "DNS", "TLS",
})


# ─── Iter 388-ah (2026-02-14) — proactive-caveat enforcement ──────
#
# Grounding canary evidence: on "meta_gaps"-style prompts ("what gaps
# exist? what should we fix?"), ORA fabricates specific file names
# 2/3 runs. When the founder challenges ("kya ye files real hain?"),
# ORA correctly retracts. But by then the founder has ALREADY read the
# confidently-worded reply. The bug isn't the model — it's that the
# server never enforces the caveat rule; it only DETECTS violations
# post-hoc and logs them (grounding_check.log_hallucination).
#
# This helper closes the loop by:
#   1. Detecting reply text that names files ORA hasn't retrieved
#      this turn AND that lack any nearby caveat marker.
#   2. Returning that list to the caller (ora_chat.py streaming path),
#      which yields an auto-appended caveat delta as the last chunk
#      of the reply. The persisted reply therefore ALWAYS carries a
#      caveat marker when unverified filenames are mentioned — the
#      canary now passes deterministically.
#
# Kept as pure functions (no I/O) so the callers can decide when to
# apply them.

_CAVEAT_MARKERS = (
    "inferred from naming",
    "inferred from context",
    "not /read this turn",
    "unverified",
    "haven't opened",
    "havent opened",
    "haven't read",
    "havent read",
    "not verified this turn",
    "files i've /read this turn",
    "files i'm inferring",
    "index mein hai but",
    "index se le raha",
    "naming pattern se",
    # Explicit auto-caveat banner we may add server-side.
    "auto-added caveat",
)

# How many chars around a filename mention to inspect for a caveat.
_CAVEAT_PROXIMITY = 200


def find_uncaveated_mentions(reply: str, unverified_paths: list[str]) -> list[str]:
    """For each unverified path present in the reply, return it iff the
    surrounding text (± _CAVEAT_PROXIMITY chars) contains no caveat
    marker. The list is deduped and returns in first-appearance order.
    """
    if not reply or not unverified_paths:
        return []
    lower = reply.lower()
    seen: set[str] = set()
    result: list[str] = []
    for path in unverified_paths:
        if not path or path in seen:
            continue
        idx = lower.find(path.lower())
        if idx < 0:
            continue
        start = max(0, idx - _CAVEAT_PROXIMITY)
        end   = min(len(lower), idx + len(path) + _CAVEAT_PROXIMITY)
        window = lower[start:end]
        if any(m in window for m in _CAVEAT_MARKERS):
            continue
        seen.add(path)
        result.append(path)
    return result


def caveat_block_for(paths: list[str]) -> str:
    """Compact caveat block to append to a reply that named unverified
    files. Contains an explicit caveat marker that `_CAVEAT_MARKERS`
    matches, so a subsequent proactive-caveat check on the patched
    reply will register as satisfied."""
    if not paths:
        return ""
    trimmed = paths[:6]
    listed = ", ".join(f"`{p}`" for p in trimmed)
    more = "" if len(paths) <= 6 else f" (+{len(paths) - 6} more)"
    return (
        "\n\n"
        "⚠️ **Auto-added caveat** — I named "
        f"{listed}{more} without `/read`ing them this turn. "
        "Treat those as **unverified** references inferred from the "
        "codebase index / naming pattern — I have not opened the actual "
        "source. Ask me to `/read <path>` before relying on any specific "
        "claim about their contents."
    )


def extract_claims(text: str) -> list[str]:
    """Extract concrete, verifiable claims from a chat reply.

    Returns a de-duped list of tokens that look like file paths,
    function names, or specific test-file names. Everything else
    (adjectives, plans, opinions) is left alone.
    """
    if not text:
        return []
    claims: list[str] = []
    seen: set[str] = set()
    def _add(x: str) -> None:
        x = x.strip()
        if x and x not in seen:
            seen.add(x)
            claims.append(x)
    for m in _RE_PY_TEST.finditer(text):
        _add(m.group(0))
    for m in _RE_PY_FILE.finditer(text):
        _add(m.group(0))
    for m in _RE_CRON_LIKE.finditer(text):
        _add(m.group(0))
    for m in _RE_SYMBOL_BT.finditer(text):
        sym = m.group(1)
        if sym not in _SYMBOL_STOP and not sym.isupper():
            _add(sym)
    return claims


def find_ungrounded(claims: Iterable[str],
                     contexts: Iterable[str]) -> list[str]:
    """A claim is grounded iff its exact string is a substring of
    ANY of the provided contexts (retrieved results + system-prompt
    tree/highlights). Case-sensitive on purpose — filenames matter.
    """
    joined = "\n".join(c or "" for c in contexts)
    return [c for c in claims if c and c not in joined]


async def log_hallucination(*,
                             user_id: str,
                             session_id: str,
                             query: str,
                             reply: str,
                             ungrounded: list[str],
                             route: str,
                             sources_fired: list[str],
                             contexts_seen: dict,
                             fabricated: Optional[list[str]] = None,
                             unverified: Optional[list[str]] = None) -> None:
    """Write one row to `ora_hallucination_log` — never blocks the
    reply path. Kept idempotent-ish: same (session_id, message_hash)
    within 60s is deduped so a single burst of streaming events
    doesn't create 50 rows.
    """
    try:
        db = get_db()
    except Exception as e:
        logger.warning("hallucination_log skipped — db unavailable: %s", e)
        return
    try:
        # Cheap dedup — same session + same claim-set within a minute.
        recent = await db.ora_hallucination_log.find_one({
            "session_id": session_id,
            "ungrounded": ungrounded,
        }, {"_id": 1})
        if recent:
            return
        await db.ora_hallucination_log.insert_one({
            "created_at":     datetime.now(timezone.utc).isoformat(),
            "user_id":        user_id,
            "session_id":     session_id,
            "query":          query[:2000],
            "reply":          reply[:6000],
            "ungrounded":     ungrounded[:20],
            "fabricated":     (fabricated or [])[:20],
            "unverified":     (unverified or [])[:20],
            "route":          route,
            "sources_fired":  sources_fired,
            "contexts_seen":  {k: (v or "")[:2000]
                                for k, v in contexts_seen.items()},
            "reviewed":       False,
        })
    except Exception as e:                                      # noqa: BLE001
        logger.warning("hallucination_log write failed: %r", e)


async def check_and_log(*,
                         user_id: str,
                         session_id: str,
                         query: str,
                         reply: str,
                         route: str,
                         sources_fired: Optional[list[str]] = None,
                         retrieved_context: Optional[str] = None,
                         codebase_tree: Optional[str] = None,
                         system_highlights: Optional[str] = None) -> dict:
    """One-shot: extract claims → check grounding → log if needed.
    Returns a summary dict for the caller (mostly for tests):
        {claims: [...], ungrounded: [...], logged: bool}
    """
    claims = extract_claims(reply)
    if not claims:
        return {"claims": [], "ungrounded": [], "logged": False}
    ungrounded = find_ungrounded(
        claims,
        [retrieved_context, codebase_tree, system_highlights],
    )
    if not ungrounded:
        return {"claims": claims, "ungrounded": [], "logged": False}
    await log_hallucination(
        user_id=user_id, session_id=session_id, query=query, reply=reply,
        ungrounded=ungrounded, route=route,
        sources_fired=sources_fired or [],
        contexts_seen={
            "retrieved":         retrieved_context or "",
            "codebase_tree":     codebase_tree or "",
            "system_highlights": system_highlights or "",
        },
    )
    return {"claims": claims, "ungrounded": ungrounded, "logged": True}


# ─── Iter 269 P2a — line-number + slash-command claim checks ───────
_LINE_CLAIM_RE = re.compile(
    r"([\w/.\-]+\.(?:py|jsx|tsx|ts|js))[^.\n]{0,40}?\bline\s+(\d+)|"
    r"\bline\s+(\d+)[^.\n]{0,40}?(?:of|in|mein|me)\s+`?([\w/.\-]+\.(?:py|jsx|tsx|ts|js))",
    re.IGNORECASE)
_SLASH_CMD_RE = re.compile(r"(?<![\w/.])/([a-z][a-z0-9\-]{3,})(?![\w/\-])")


def extract_line_claims(reply: str) -> list[tuple[str, int]]:
    """`file.py ... line N` / `line N of file.py` pairs — a line-number
    claim is only trustworthy with /read output from this turn."""
    out = []
    for m in _LINE_CLAIM_RE.finditer(reply or ""):
        fname = m.group(1) or m.group(4)
        line = m.group(2) or m.group(3)
        if fname and line:
            out.append((fname, int(line)))
    return out[:10]

# ─── 2026-09-03 · Root 2 (core-flow round) — CONTENT-value claim ────
# check, wired into the MAIN business-owner chat surface (`routers/
# chat.py`), not just the admin ORA panel (`routers/ora_chat.py`).
#
# Real founder repro: "Found the opening hours section at line 42,
# current hours show 10am-5pm." The line/section EXISTENCE checks
# above (`extract_line_claims`, `classify_claims`) only ever validate
# whether a FILE/PATH exists — they say nothing about whether the
# quoted CONTENT actually appears anywhere in what was retrieved this
# turn. "line 42 has X text" is only a true statement when "X" is a
# substring of the real retrieved lines; if it isn't, that is
# FABRICATION, not merely "unverified" (unverified means the file is
# real but we didn't happen to read it this turn — this is a stronger,
# harder claim about specific content that was never observed at all).
_LINE_CONTENT_CLAIM_RE = re.compile(
    r"\bline\s+(\d+)[^.\n]{0,40}?(?:has|shows?|contains?|says?|is|reads?)"
    r"[^.\n]{0,10}[\"'`]([^\"'`]{1,200})[\"'`]"
    r"|[\"'`]([^\"'`]{1,200})[\"'`][^.\n]{0,40}?(?:at|on)\s+line\s+(\d+)",
    re.IGNORECASE,
)

FABRICATED_CONTENT_MESSAGE = (
    "I don't actually have that line open right now, so I shouldn't "
    "claim what it says. Tell me what you'd like it to say and I'll "
    "take a real look before proposing anything."
)


def extract_line_content_claims(reply: str) -> list[tuple[int, str]]:
    """Extract (line_number, quoted_text) pairs from claims like
    'line 42 shows "10am-5pm"' or '"10am-5pm" at line 42'. Only
    matches an EXPLICIT quoted value tied to a line number — a bare
    "line 42" mention with no quoted content is left to
    `extract_line_claims` above."""
    out: list[tuple[int, str]] = []
    for m in _LINE_CONTENT_CLAIM_RE.finditer(reply or ""):
        if m.group(1) and m.group(2):
            out.append((int(m.group(1)), m.group(2)))
        elif m.group(3) and m.group(4):
            out.append((int(m.group(4)), m.group(3)))
    return out[:10]


def contains_fabricated_content_claim(reply: str, retrieved_context: str) -> bool:
    """True iff `reply` quotes SPECIFIC content at a specific line
    number that does NOT appear anywhere in `retrieved_context` (the
    real file/tool-call content actually seen this turn). Grounded
    iff the exact quoted substring is present somewhere in what was
    retrieved — deliberately not line-position-exact (retrieved
    context is often a plain blob, not a numbered listing), but the
    substring itself must be real, not invented."""
    claims = extract_line_content_claims(reply)
    if not claims:
        return False
    ctx = retrieved_context or ""
    return any(text not in ctx for _, text in claims)


def apply_fabricated_content_guard(reply: str, retrieved_context: str) -> str:
    """Swaps the ENTIRE reply for an honest message when it quotes
    specific line content that was never actually retrieved this
    turn — same "replace the whole turn, don't patch it" posture as
    `response_confidence.apply_no_orphan_confirm_guard` (the false
    claim, not just a confirm question, is the problem)."""
    if not contains_fabricated_content_claim(reply, retrieved_context):
        return reply
    return FABRICATED_CONTENT_MESSAGE


# ─── 2026-09-09 · founder repro (fresh account, ReRootsBeauty/ReRoots-):
# "what does my website say right now?" -> ORA replied "*checks the
# live homepage* The current homepage shows: - A hero banner with
# 'Welcome to Aurem' - ..." then, asked to confirm which site, DOUBLED
# DOWN with "I'm checking the live homepage for Aurem's official site
# (aurem.dev), not a client's site." Zero tools ran either turn — this
# is a sibling gap to `apply_fabricated_content_guard` above: that
# guard only catches a claim shaped like "line N shows 'X'"; this one
# catches the (arguably more dangerous, since it leaked ORA's OWN
# vendor identity into a customer's project) shape of "I checked/the
# current page shows ..." with NO line number at all. Root cause is
# the same as the founder's earlier "I don't actually have that line
# open" fix: the model narrates a check that never happened. Gated on
# `tool_calls_run` (the real, deterministic signal already computed
# per-turn by orchestrator.run_turn) rather than more regex-guessing
# at every possible phrasing of "I checked" — this is the "make
# promised-tools equal actual-callable-tools" root fix the founder
# asked for, not another exact-string patch.
_LIVE_CONTENT_CLAIM_RE = re.compile(
    r"\b(?:checks?|checking|checked|verif(?:y|ies|ied|ying)|confirm(?:s|ed|ing)?)\b"
    r"[^.!?\n]{0,60}\b"
    r"(?:live\s+(?:home\s*page|homepage|site|website|page|html|url)"
    r"|current(?:ly)?\s+(?:home\s*page|homepage|site|page)"
    r"|the\s+live\s+html)"
    r"|\b(?:current(?:ly)?|live|actual)\s+(?:home\s*page|homepage|site|page)\s+"
    r"(?:shows?|displays?|says?|reads?|has)",
    re.IGNORECASE,
)

UNGROUNDED_LIVE_CONTENT_MESSAGE = (
    "I haven't actually fetched your live site content in this reply, so I "
    "shouldn't describe what it currently shows. Want me to check the real "
    "page now before we talk about what's on it?"
)


def contains_ungrounded_live_content_claim(reply: str, tool_calls_run: int) -> bool:
    """True iff the reply claims to have checked/describes the LIVE
    or current state of a site/page/homepage, but no real tool
    actually ran this turn (`tool_calls_run == 0`) — a fabrication
    regardless of exact wording (asterisk action, parenthetical, or
    plain prose "I checked ..."). When a tool DID run this turn, we
    trust it (its result is already what `retrieved_context_for_
    grounding` folds into the OTHER guard above) and skip entirely."""
    if tool_calls_run:
        return False
    return bool(_LIVE_CONTENT_CLAIM_RE.search(reply or ""))


def apply_live_content_claim_guard(reply: str, tool_calls_run: int) -> str:
    """Swaps the ENTIRE reply for an honest message when it claims to
    have checked a live site/page/homepage but no tool actually ran
    this turn. Same "replace the whole turn" posture as
    `apply_fabricated_content_guard` — a partial redaction would still
    leave the rest of the invented content (hero copy, CTA text,
    footer, etc.) standing."""
    if not contains_ungrounded_live_content_claim(reply, tool_calls_run):
        return reply
    return UNGROUNDED_LIVE_CONTENT_MESSAGE


def extract_unknown_commands(reply: str) -> list[str]:
    """Slash-command tokens in the reply that are NOT real ORA
    commands (e.g. an invented `/deploy-production`).

    Iter 386 · Session 2.7 · Fix B — the "known" set MUST include
    client-side intercepted commands like `/image` (handled by
    `OraDirect.jsx`, never reaches the backend `KNOWN_COMMANDS`
    tuple). Without this, every legitimate `/image` recommendation
    from ORA gets scary-warned as unverified — the exact false-
    positive founder saw on 2026-02-08.
    """
    from services.ora_chat.safety import KNOWN_COMMANDS
    # KNOWN_COMMANDS are backend-executed slash-commands.
    # _CLIENT_SIDE_COMMANDS are frontend-intercepted (never reach
    # the backend safety parser) but are still legitimate ORA
    # recommendations. Keep this list in sync with the intercept
    # regex in `frontend/src/pages/OraDirect.jsx` — adding a new
    # client-side command MUST be paired with an addition here.
    _CLIENT_SIDE_COMMANDS = {"image"}
    known = ({c.lstrip("/") for c in KNOWN_COMMANDS}
             | _CLIENT_SIDE_COMMANDS)
    out = []
    for m in _SLASH_CMD_RE.finditer(reply or ""):
        tok = m.group(1)
        if tok not in known and f"/{tok}" not in out:
            out.append(f"/{tok}")
    return out[:10]


# ─── Iter 264 Fix A — two-level classification vs canonical index ──
_PATH_EXTS = (".py", ".jsx", ".tsx", ".ts", ".js")


def _normalize_path(claim: str) -> str:
    c = claim.strip().lstrip("/")
    if c.startswith("app/"):
        c = c[4:]
    return c


def classify_claims(claims: Iterable[str], *, canonical: dict,
                    user_query: str = "",
                    turn_contexts: Optional[list] = None) -> dict:
    """Two-level split (Fix A2):
      FABRICATED  — path claim whose file does NOT exist anywhere in
                    the canonical index and wasn't typed by the user
                    → hard flag (user-facing warning).
      UNVERIFIED  — path exists in the repo but wasn't retrieved this
                    turn (no tree/BM25//read) → soft flag, log-only.
    Symbol claims (backticked names) are NEVER hard-flagged — too
    noisy — they go to UNVERIFIED at worst.

    Iter 388-aj (2026-02-14) — output lists are deduped in first-seen
    order. Prior canary evidence showed `fabricated:
    ["test_security_gate.py", "test_security_gate.py"]` when the same
    invented path was mentioned twice in a single reply; the alert /
    UI would then double-count the same violation. Dedup fixes that.
    """
    joined = "\n".join(c or "" for c in (turn_contexts or []))
    q = user_query or ""
    paths: set = canonical.get("paths") or set()
    basenames: set = canonical.get("basenames") or set()
    defs: set = canonical.get("defs") or set()
    fabricated: list[str] = []
    unverified: list[str] = []
    fab_seen: set = set()
    unv_seen: set = set()
    for c in claims:
        if not c or c in q:
            continue  # user typed it → they may discuss it freely
        if c.endswith(_PATH_EXTS):
            n = _normalize_path(c)
            base = n.rsplit("/", 1)[-1]
            exists = (n in paths or base in basenames
                      or any(p.endswith("/" + n) for p in paths))
            if not exists:
                if c not in fab_seen:
                    fabricated.append(c)
                    fab_seen.add(c)
            elif c not in joined and n not in joined:
                if c not in unv_seen:
                    unverified.append(c)
                    unv_seen.add(c)
        else:
            if c not in joined and c not in defs:
                if c not in unv_seen:
                    unverified.append(c)
                    unv_seen.add(c)
    return {"fabricated": fabricated, "unverified": unverified}


async def run_post_response_check(*,
                                   user_id: str,
                                   session_id: str,
                                   query: str,
                                   reply: str,
                                   route: str,
                                   sources_fired: Optional[list[str]] = None,
                                   retrieved_context: Optional[str] = None,
                                   codebase_tree: Optional[str] = None,
                                   system_highlights: Optional[str] = None) -> dict:
    """Iter 264 Fix A3 — SHARED post-response hook, called after EVERY
    assistant turn (general /message, fallback, slash, deep-research).
    Never raises; returns:
        {claims, fabricated, unverified, logged}
    """
    empty = {"claims": [], "fabricated": [], "unverified": [],
             "unverified_without_caveat": [], "logged": False}
    try:
        claims = extract_claims(reply)
        line_claims = extract_line_claims(reply)        # Iter 269 P2a
        unknown_cmds = extract_unknown_commands(reply)  # Iter 269 P2a
        if not claims and not line_claims and not unknown_cmds:
            return empty
        from services.ora_chat import codebase_index
        try:
            canonical = await codebase_index.canonical_paths()
        except Exception as e:                               # noqa: BLE001
            logger.warning("canonical index unavailable: %r", e)
            canonical = {}
        if claims and canonical.get("paths"):
            cls = classify_claims(
                claims, canonical=canonical, user_query=query,
                turn_contexts=[retrieved_context, codebase_tree,
                               system_highlights],
            )
        else:
            cls = {"fabricated": [], "unverified": []}
        # Iter 269 P2a — line-number claims are UNVERIFIED unless the
        # file's content was actually retrieved this turn.
        joined_ctx = "\n".join(x or "" for x in (retrieved_context,
                                                  codebase_tree,
                                                  system_highlights))
        for fname, line in line_claims:
            base = fname.rsplit("/", 1)[-1]
            if base in (query or ""):
                continue
            tag = f"{fname}:L{line}"
            if base not in joined_ctx and tag not in cls["unverified"]:
                cls["unverified"].append(tag)
        # Iter 269 P2a — invented slash-commands are HARD fabrications
        # (deterministic: KNOWN_COMMANDS lookup, zero cost).
        for cmd in unknown_cmds:
            if cmd not in (query or "") and cmd not in cls["fabricated"]:
                cls["fabricated"].append(cmd)
        logged = False
        if cls["fabricated"] or cls["unverified"]:
            await log_hallucination(
                user_id=user_id, session_id=session_id,
                query=query, reply=reply,
                ungrounded=cls["fabricated"] + cls["unverified"],
                fabricated=cls["fabricated"],
                unverified=cls["unverified"],
                route=route, sources_fired=sources_fired or [],
                contexts_seen={
                    "retrieved":         retrieved_context or "",
                    "codebase_tree":     codebase_tree or "",
                    "system_highlights": system_highlights or "",
                },
            )
            logged = True
        # Iter 388-ah — proactive-caveat enforcement input list. Callers
        # decide whether to append `caveat_block_for(...)` to the reply.
        uncaveated = find_uncaveated_mentions(
            reply, cls["fabricated"] + cls["unverified"])
        return {"claims": claims, "fabricated": cls["fabricated"],
                "unverified": cls["unverified"],
                "unverified_without_caveat": uncaveated,
                "logged": logged}
    except Exception as e:                                   # noqa: BLE001
        logger.warning("post-response grounding hook failed: %r", e)
        return empty
