"""
tests/test_w3_s1p4_url_autodetect.py — Overnight loop W3-S1-P4
(2026-08-29). Deterministic (0-LLM) live-URL detector used by
GET /cto/projects/{id}/detect-live-url.
"""
import json

from services.preview_capture import detect_live_url_from_config


def test_package_json_homepage():
    content = json.dumps({"name": "app", "homepage": "https://myapp.example.com/"})
    assert detect_live_url_from_config("package.json", content) == "https://myapp.example.com"


def test_package_json_no_homepage():
    content = json.dumps({"name": "app"})
    assert detect_live_url_from_config("package.json", content) == ""


def test_vercel_json_alias():
    content = json.dumps({"alias": ["myapp.vercel.app"]})
    assert detect_live_url_from_config("vercel.json", content) == "https://myapp.vercel.app"


def test_vercel_json_no_alias():
    content = json.dumps({"builds": []})
    assert detect_live_url_from_config("vercel.json", content) == ""


def test_netlify_toml_url_found():
    content = '[build]\ncommand = "yarn build"\n# https://myapp.netlify.app is live\n'
    assert detect_live_url_from_config("netlify.toml", content) == "https://myapp.netlify.app"


def test_netlify_toml_no_url():
    content = '[build]\ncommand = "yarn build"\n'
    assert detect_live_url_from_config("netlify.toml", content) == ""


def test_unknown_file_returns_empty():
    assert detect_live_url_from_config("readme.md", "https://example.com") == ""


def test_malformed_json_never_raises():
    assert detect_live_url_from_config("package.json", "{not json") == ""
    assert detect_live_url_from_config("vercel.json", "{not json") == ""
