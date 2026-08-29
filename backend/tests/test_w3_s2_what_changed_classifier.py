"""
tests/test_w3_s2_what_changed_classifier.py — Overnight loop W3-S2
(2026-08-29). Deterministic (0-LLM) classifier used by
GET /cto/projects/{id}/what-changed.
"""
from services.preview_capture import classify_changed_file, summarise_change_classification


def test_classify_ui_paths():
    assert classify_changed_file("frontend/src/pages/Signup.jsx") == "ui"
    assert classify_changed_file("frontend/src/components/Nav.jsx") == "ui"


def test_classify_server_paths():
    assert classify_changed_file("backend/routers/chat.py") == "server"
    assert classify_changed_file("backend/migrations/0001_init.sql") == "server"


def test_classify_other():
    assert classify_changed_file("README.md") == "other"


def test_summary_no_changes():
    s = summarise_change_classification([])
    assert s["headline"] == "No changes yet."
    assert s["n_files"] == 0


def test_summary_never_hides_server_impact_even_with_ui_files():
    s = summarise_change_classification([
        "frontend/src/pages/Signup.jsx", "backend/routers/auth.py",
    ])
    assert s["has_server"] is True
    assert "server" in s["headline"].lower()
    assert s["n_files"] == 2


def test_summary_pure_ui():
    s = summarise_change_classification(["frontend/src/components/Btn.jsx"])
    assert s["has_server"] is False
    assert "customer-facing" in s["headline"]
