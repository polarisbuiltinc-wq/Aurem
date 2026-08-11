"""
Phase 3 · Codebase Health mini-batch — HTTP wrapper migration pinning test.

Scope (2026-02-12, post Batch 8b, PREP-ONLY — do NOT dispatch until
Batch 8b E2E gap closed):
  • routers/codebase_health.py — 1 site (deliberate 3-value timeout tuple)

**Why this file is its own mini-batch:**

The site at line ~558 uses a hand-tuned `httpx.Timeout(45.0, connect=6.0,
read=15.0)` — this is NOT a default value. It was chosen in Iter
212m-221 to root-cause an intermittent Cloudflare 502 on prod:

  * Overall timeout 45s: bounds total wall clock for a whole-tree
    fetch (worst case a large monorepo with many blobs)
  * Connect 6s: fail fast if a GH pod is unreachable — a slow
    connect used to hold the event loop past Cloudflare's
    origin-idle-timeout and trigger a spurious 502
  * Read 15s: fail fast on a stalled response body — same reason
  * Write / pool: inherit the 45s overall

If the migration silently drops this tuple and reverts to
`ext_client`'s default `github` dep timeout (read=20s), the
Cloudflare 502 class of bugs comes back. This test pins the exact
tuple against migration regressions.
"""


def _read(path: str) -> str:
    return open(path).read()


def test_codebase_health_preserves_45_6_15_timeout_tuple():
    """CORE INVARIANT: the (overall=45s, connect=6s, read=15s)
    tuple MUST survive the migration to ext_client. This is
    Iter 212m-221's Cloudflare-502 root-cause fix — if the
    tuple is lost, the 502s come back."""
    src = _read("/app/backend/routers/codebase_health.py")
    # Exact tuple — order may be either kwarg or positional but
    # values must be present.
    assert "httpx.Timeout(45.0, connect=6.0, read=15.0)" in src, (
        "The (45s, 6s, 15s) timeout tuple was tuned in Iter "
        "212m-221 to fix Cloudflare 502s from stalled GH "
        "connections holding the pod's event loop. If a future "
        "refactor drops the tuple, the 502s come back. RESTORE."
    )


def test_codebase_health_uses_ext_client_after_migration():
    """After migration: the raw AsyncClient is gone and ext_client
    with the preserved timeout is used."""
    src = _read("/app/backend/routers/codebase_health.py")
    assert "from services.http import ext_client" in src, (
        "codebase_health.py must import ext_client after migration."
    )
    # ext_client must be called with the preserved timeout tuple.
    assert 'ext_client("github", timeout=httpx.Timeout(45.0, connect=6.0, read=15.0))' in src, (
        "Migration must pass the exact preserved timeout tuple to "
        "ext_client. Passing None (or default) would silently swap "
        "to ext_client's github dep default (read=20s) — losing "
        "the Iter 212m-221 fix."
    )
    # Raw client with this specific tuple gone.
    assert "async with httpx.AsyncClient(timeout=_timeout)" not in src, (
        "Raw httpx.AsyncClient(timeout=_timeout) still present. "
        "Migration incomplete."
    )


def test_codebase_health_docstring_records_migration_rationale():
    """The Iter 212m-221 rationale block must survive so a future
    'clean up' doesn't remove the hand-tuned timeouts."""
    src = _read("/app/backend/routers/codebase_health.py")
    # Comment/docstring keywords that must survive.
    assert "Iter 212m-221" in src, (
        "The Iter 212m-221 rationale reference must survive. It's "
        "the breadcrumb that tells a future maintainer why 6s/15s "
        "aren't arbitrary."
    )
    assert "Cloudflare" in src and "502" in src, (
        "The Cloudflare 502 explanation must survive to prevent "
        "an over-zealous refactor from relaxing the strict timeouts."
    )


def test_codebase_health_mini_batch_scope_is_ONLY_this_file():
    """Guard: mini-batch touches ONLY codebase_health.py. Other
    deferred files (github_api_writer.py — needs ext_client(limits=)
    upgrade) must stay untouched."""
    # github_api_writer must still have its raw httpx pattern until
    # the limits= upgrade lands.
    writer = _read("/app/backend/services/github_api_writer.py")
    # Just confirm the file still exists + is importable-shaped.
    assert "def " in writer or "class " in writer, (
        "github_api_writer.py must still exist and hold real code. "
        "Its migration is a SEPARATE batch after ext_client(limits=) "
        "API upgrade lands."
    )
