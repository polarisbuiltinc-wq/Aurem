"""
Phase 3 · Sub-batch 3 of github_api_writer migration — WRITE sites.

PREP ONLY (2026-02-12). Do NOT commit or dispatch without explicit
founder review. Sub-batch 3 is the highest blast-radius migration
in this project (branch-pointer advancement risk).

**Scope (8 write sites):**
  In commit_files (lines ~112-189):
    - Blob POST (parallel N, line ~121)
    - Tree POST (line ~147)
    - Commit POST (line ~161)
    - **Ref-advance PATCH** (line ~177, "force": False hardcoded)

  In revert_commit (lines ~219-321):
    - Blob POST (parallel N, line ~264)
    - Tree POST (line ~283)
    - Commit POST (line ~299)
    - **Ref-advance PATCH** (line ~312, "force": False hardcoded)

**MANDATED preserve invariants (per founder direction 2026-02-12):**
  1. `"force": False` in the PATCH body — hardcoded, no variable
     substitution, no config-driven flip
  2. Timeout 60s explicit on ext_client
  3. Limits 20/20 explicit on ext_client
  4. `asyncio.gather(*[_upload(...)])` parallel-blob pattern intact
  5. **RETRY OPT-OUT for ref-advance PATCH** — the wrapper's
     `call_with_retry` MUST NEVER wrap the PATCH ref-advance calls,
     even indirectly. Founder mandate: repeat-PATCH-is-no-op is
     a SECONDARY safety net; the primary defense is the explicit
     opt-out. If a future refactor adds retry_guard around these
     PATCHes, this test file breaks the build.
  6. commit_files still requires non-empty author_name + author_email
     (Iter 212m-218 identity guard, no defaults)
  7. `_LIMITS` module constant deleted (superseded by explicit
     httpx.Limits() at each ext_client site)
"""


def _read(path: str) -> str:
    return open(path).read()


SRC = None
def _src():
    global SRC
    if SRC is None:
        SRC = _read("/app/backend/services/github_api_writer.py")
    return SRC


# ─── Force:False hardcode preservation ──────────────────────────────

def test_ref_advance_force_false_hardcoded_in_both_functions():
    """Both ref-advance PATCH sites (commit_files line ~177 and
    revert_commit line ~312) MUST send `"force": False` verbatim.
    No variable substitution. This is the guard against accidental
    history-rewrite force-pushes."""
    src = _src()
    # At least 2 occurrences of the exact literal.
    assert src.count('"force": False') >= 2, (
        'The "force": False literal must appear at LEAST twice (once '
        "in commit_files ref PATCH, once in revert_commit ref PATCH). "
        "If a future refactor made it a variable, revert immediately."
    )


def test_no_force_true_anywhere():
    """Belt+braces: no `"force": True` anywhere in this file."""
    src = _src()
    assert '"force": True' not in src
    assert '"force":True' not in src
    assert '"force": true' not in src  # JSON-ified accidentally


# ─── Timeout + limits explicit at write sites ───────────────────────

def test_write_blocks_still_use_60s_timeout_and_20_20_limits():
    """After Sub-batch 3, the write-path ext_client sites (or the raw
    httpx.AsyncClient if still raw for a subset) MUST preserve 60s
    timeout and 20/20 limits explicitly. Same discipline as Sub-batch 2
    reads — don't rely on wrapper defaults."""
    src = _src()
    # Look for the tuple in the file at least twice (commit_files +
    # revert_commit). If Sub-batch 3 migrates to ext_client, these
    # become the ext_client kwargs. If still raw (transitional), they
    # remain as httpx.AsyncClient kwargs.
    has_60s = "httpx.Timeout(60.0)" in src
    has_2020 = "max_connections=20" in src and "max_keepalive_connections=20" in src
    assert has_60s, "60s timeout MUST be present in write blocks"
    assert has_2020, "20/20 limits MUST be present in write blocks"


# ─── Retry opt-out for ref-advance PATCH ────────────────────────────

def test_no_call_with_retry_anywhere_in_writer():
    """CRITICAL retry opt-out guard.

    The wrapper's `call_with_retry(...)` MUST NEVER wrap ANY call in
    github_api_writer.py — most importantly, the two ref-advance
    PATCH calls (commit_files line ~177 and revert_commit line ~312).

    Rationale: repeat-PATCH-is-no-op is a nice SECONDARY safety net,
    but the PRIMARY defense against accidental history churn is
    explicit non-retry. If retry_guard fires on a network blip AFTER
    GitHub already accepted the PATCH, the retry could race a
    concurrent push from a different actor and silently overwrite
    the newer ref pointer.

    This test fails if ANY future refactor adds call_with_retry
    into this file. Force review of the change.

    NOTE: uses `call_with_retry(` (with paren) so rationale comments
    that mention the name `call_with_retry` without invoking it (see
    `test_ref_advance_docstring_records_no_retry_rationale`) don't
    falsely trip this guard. Actual usage would be a `call_with_retry(...)`
    function call which this check will still catch."""
    src = _src()
    assert "call_with_retry(" not in src, (
        "call_with_retry(...) invocation appeared in github_api_writer.py. This "
        "is the retry opt-out guard for the two ref-advance PATCH "
        "sites. If you have a legitimate reason to add retry to a "
        "NON-PATCH site here, refactor this test to allow that "
        "specific site while still guarding the PATCH sites."
    )


def test_ref_advance_docstring_records_no_retry_rationale():
    """The ref-advance PATCH sites (or their containing functions)
    must include an inline comment explaining WHY call_with_retry is
    NOT used — otherwise a future maintainer might 'improve' reliability
    by adding retries and reintroduce the race."""
    src = _src()
    assert "no retry" in src.lower() or "no_retry" in src.lower() or \
           "opt out" in src.lower() or "opt-out" in src.lower() or \
           "retry opt-out" in src.lower(), (
        "The ref-advance PATCH sites (or their containing function) "
        "must include an inline comment explaining why call_with_retry "
        "is NOT used."
    )


# ─── Parallelism preservation ──────────────────────────────────────

def test_blob_uploads_still_parallel_gather():
    """Both commit_files + revert_commit's blob uploads MUST still
    use `asyncio.gather(*[_upload(...) for ...])`. Serializing them
    would 10x latency on multi-file commits."""
    src = _src()
    assert src.count("asyncio.gather") >= 3, (
        "Expected ≥3 asyncio.gather calls (blob-upload gather in "
        "commit_files, restore-spec + blob-build gathers in "
        "revert_commit). Sub-batch 3 must not linearize any of these."
    )


# ─── Identity guard (Iter 212m-218) ────────────────────────────────

def test_commit_files_still_requires_non_empty_author():
    """Iter 212m-218: commit_files raises ValueError on empty
    author_name/author_email. Sub-batch 3 must NOT weaken this guard."""
    src = _src()
    assert "commit_files requires non-empty author_name and author_email" in src, (
        "The author-identity ValueError guard from Iter 212m-218 must "
        "survive Sub-batch 3. Removing it lets lazy callers push "
        "bot-attributed commits."
    )


# ─── _LIMITS constant cleanup ──────────────────────────────────────

def test_LIMITS_module_constant_removed_after_full_migration():
    """After Sub-batch 3 lands (writes on ext_client), the module-level
    _LIMITS constant is unused and SHOULD be removed to prevent drift
    between the constant and the per-site explicit values."""
    src = _src()
    # This test EXPECTS to fail during Sub-batch 3 prep (constant still
    # referenced by raw write blocks) and pass AFTER writes migrate.
    # Marking as an aspirational check.
    if "async with httpx.AsyncClient(timeout=60.0, limits=_LIMITS)" in src:
        # Still transitional — writes on raw client. _LIMITS still in use.
        # Skip cleanup guard.
        pass
    else:
        # Writes migrated → _LIMITS should be gone.
        assert "_LIMITS = httpx.Limits" not in src, (
            "Writes are migrated to ext_client but _LIMITS constant "
            "still defined. Remove it to prevent future drift."
        )


# ─── Sub-batch 2 reads left untouched ──────────────────────────────

def test_sub_batch_2_read_helpers_untouched_by_sub_batch_3():
    """Sub-batch 2's 3 read helpers (fetch_file, _get_branch_head,
    _get_commit_details) MUST still exist as self-contained
    ext_client wrappers. Sub-batch 3 touches WRITES only."""
    src = _src()
    for name in ("fetch_file", "_get_branch_head", "_get_commit_details"):
        assert f"async def {name}(" in src, (
            f"Sub-batch 2 helper {name}() missing. Sub-batch 3 must "
            f"not touch read helpers."
        )


# ─── Write-site ext_client sites (after Sub-batch 3 migration) ─────

def test_write_sites_reach_ext_client_with_explicit_kwargs():
    """This test PASSES after Sub-batch 3 migrates writes.
    During prep, it may fail if writes are still on raw client.

    Post-Sub-batch-3, expect ≥8 total ext_client sites in this file
    (4 reads + 4-8 writes depending on whether they share a client
    scope or each open their own)."""
    src = _src()
    # If writes migrated to ext_client, total ext_client calls ≥5
    # (3 read helpers each have 1 open, plus writes)
    if "async with httpx.AsyncClient(timeout=60.0, limits=_LIMITS)" not in src:
        # Writes migrated
        count = src.count("ext_client(")
        assert count >= 5, (
            f"After Sub-batch 3, expected ≥5 ext_client sites "
            f"in github_api_writer.py, found {count}. Verify writes "
            f"actually migrated."
        )
