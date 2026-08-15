"""
Iter 390 — Developer-only rollout, /choose-track removed.

Contract locked in place:
  1. Every new /auth/signup dev_users row MUST include
     `track: "developer"` + `track_updated_at`. No new user starts
     with a null/missing track (avoids showing the legacy nudge or
     needing a backfill on next boot).
  2. The Google OAuth new-user path and the GitHub OAuth new-user
     path insert the same defaults.
  3. The startup backfill still exists as a safety net; verify it
     stays untouched (any regression that touches it should be
     intentional and caught here).

Read-only source assertions — no live DB, no live routes.
"""
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
AUTH_PY = BACKEND / "routers" / "auth.py"
GITHUB_OAUTH_PY = BACKEND / "routers" / "github_oauth.py"
MAIN_PY = BACKEND / "main.py"


def _read(p):
    return p.read_text(encoding="utf-8")


def test_signup_defaults_track_to_developer():
    src = _read(AUTH_PY)
    # Locate the /signup handler.
    marker = "async def signup("
    idx = src.index(marker)
    # Find the dev_users.insert_one call inside this handler.
    insert_idx = src.index("dev_users.insert_one", idx)
    # Grab the next ~40 lines (the insert dict).
    block = src[insert_idx:insert_idx + 2000]
    assert '"track":              "developer"' in block, (
        "Iter 390: /signup dev_users.insert_one must default track to 'developer'"
    )
    assert '"track_updated_at":   _now_ts' in block, (
        "Iter 390: /signup insert must also set track_updated_at"
    )


def test_google_oauth_defaults_track_to_developer():
    src = _read(AUTH_PY)
    # Google OAuth session handler exists further down.
    marker = "async def google_session("
    if marker not in src:
        # Older name — try the endpoint decorator instead.
        marker = "/google/session"
    idx = src.index(marker)
    insert_idx = src.index("dev_users.insert_one", idx)
    block = src[insert_idx:insert_idx + 2000]
    assert '"track":              "developer"' in block, (
        "Iter 390: Google OAuth new-user insert must default track to 'developer'"
    )


def test_github_oauth_defaults_track_to_developer():
    src = _read(GITHUB_OAUTH_PY)
    insert_idx = src.index("dev_users.insert_one")
    block = src[insert_idx:insert_idx + 2500]
    assert '"track":              "developer"' in block, (
        "Iter 390: GitHub OAuth new-user insert must default track to 'developer'"
    )


def test_backfill_safety_net_still_present():
    """Startup backfill must remain — defensive against any legacy
    null-track row that predates Iter 390 or slips through a future
    insert path."""
    src = _read(MAIN_PY)
    assert "_backfill_dev_users_track" in src
    assert '"$set": {"track": "developer"' in src


def test_set_track_endpoint_still_registered():
    """Settings TrackSwitcher still needs /auth/set-track for legacy
    Personal Track opt-in/out (Option B: infra kept, only signup step
    removed). This test guards against accidental removal in a future
    aggressive cleanup pass."""
    src = _read(AUTH_PY)
    assert '@router.post("/set-track")' in src
    assert "async def set_track(" in src
