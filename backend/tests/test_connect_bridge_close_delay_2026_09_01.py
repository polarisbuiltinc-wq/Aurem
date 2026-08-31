"""
tests/test_connect_bridge_close_delay_2026_09_01.py

Connect-flow investigation, Item C — the success-bridge popup used to
close itself at 400ms, which could preempt the server's own repo-list
reconciliation (self-heal, ~10s) and made a real success look
unfinished. t_connect_state_does_not_close_at_400ms: the bridge must
give the parent + server real time to converge before closing.
"""
from routers.github_app import _BRIDGE_HTML


def test_t_connect_state_does_not_close_at_400ms():
    assert "window.close()" in _BRIDGE_HTML
    marker = "try { window.close(); } catch (e) {} }, "
    idx = _BRIDGE_HTML.index(marker) + len(marker)
    delay_str = _BRIDGE_HTML[idx:idx + 10].split(")")[0]
    delay_ms = int(delay_str)
    assert delay_ms >= 10_000, (
        f"bridge closes the success popup after only {delay_ms}ms — "
        f"too fast for the server's repo-list self-heal to catch up"
    )
    assert delay_ms <= 15_000
