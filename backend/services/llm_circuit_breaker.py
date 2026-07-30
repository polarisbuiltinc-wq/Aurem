"""Thin shim — admin_bin.py's Bin panel probes this module for LLM
breaker state. Backed by the central Guard 17 registry (retry_guard)."""
from services.retry_guard import get_breaker


def get_breaker_state() -> str:
    return get_breaker("openrouter").state
