"""
tests/test_connect_bridge_close_delay_2026_09_01.py

Connect-flow investigation, Item C — SUPERSEDED 2026-09-02.

Original premise (this file's history): the bridge popup closed at
400ms, which could preempt the server's repo-list self-heal, so it
was widened to a 12s blocking hold. The founder's 2026-09-02
correction: that 12s hold was itself a hack, not the standard SaaS
pattern — the real false-denied race is already fixed at the root in
`useGitHubConnectStatus.js` (exits on `installation_active`, not on
this popup's timing). So the bridge now only needs a short, standard
"acknowledged" beat before closing — see
tests/test_connect_flow_refinement_2026_09_02.py::
test_t_no_12s_blocking_interstitial for the current assertion.
This file is kept (not deleted) as a pointer for anyone still
grepping the old test name; assertions here now match the current
short-beat behavior instead of the retired 12s hold.
"""
from routers.github_app import _BRIDGE_HTML


def test_bridge_close_delay_is_a_short_beat_not_a_12s_hold():
    assert "window.close()" in _BRIDGE_HTML
    marker = "try { window.close(); } catch (e) {} }, "
    idx = _BRIDGE_HTML.index(marker) + len(marker)
    delay_str = _BRIDGE_HTML[idx:idx + 10].split(")")[0]
    delay_ms = int(delay_str)
    assert delay_ms < 2_000, (
        f"bridge is back to a multi-second blocking hold ({delay_ms}ms) — "
        f"the false-denied race is fixed elsewhere now, this should be short"
    )
