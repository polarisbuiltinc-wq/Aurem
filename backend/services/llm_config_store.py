"""
services/llm_config_store.py — Admin self-serve LLM provider settings.

Any model/vendor (Qwen, Qwen-VL, Gemini, OpenAI, ...) becomes a data
entry the admin manages in Settings — zero code, zero deploy, zero
restart. Resolution priority (per role) lives in
`ora_chat_v2/llm_client.py::_resolve`:

    MOCK_LLM=true  -> mock (always wins, for tests)
  else active config for role -> this store
  else env fallback (LLM_BASE_URL/LLM_API_KEY/LLM_MODEL, or the
       LLM_VISION_* equivalents)

Secrets are Fernet-encrypted at rest (`LLM_KEY_ENCRYPTION_KEY`, base64
32-byte key). If that env var is unset, every write/decrypt fails
closed with a clear admin-facing message — a plaintext key is never
persisted or run as a fallback.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

VALID_ROLES = ("chat", "vision", "any")
_CACHE_TTL_S = 60
# Per-role resolved-config cache; `ts` is shared so any write can force
# an immediate refresh on the next read from any role (bump = zero it).
_cache: dict = {"chat": None, "vision": None, "ts": 0.0}


class EncryptionNotConfigured(Exception):
    pass


def _fernet():
    import os
    from cryptography.fernet import Fernet
    key = (os.getenv("LLM_KEY_ENCRYPTION_KEY") or "").strip()
    if not key:
        raise EncryptionNotConfigured(
            "LLM_KEY_ENCRYPTION_KEY is not set on the server — admin-managed "
            "LLM keys are disabled until this exists (base64, 32 bytes; "
            "generate with `Fernet.generate_key()`). Env-based LLM_* vars "
            "still work as the fallback."
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as e:                                        # noqa: BLE001
        raise EncryptionNotConfigured(
            f"LLM_KEY_ENCRYPTION_KEY is set but invalid: {type(e).__name__}"
        ) from e


def encrypt_key(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode())


def decrypt_key(ciphertext) -> str:
    raw = bytes(ciphertext) if not isinstance(ciphertext, bytes) else ciphertext
    return _fernet().decrypt(raw).decode()


def key_hint(plaintext: str) -> str:
    tail = plaintext[-4:] if len(plaintext) >= 4 else plaintext
    return f"…{tail}"


def _bump_cache() -> None:
    _cache["ts"] = 0.0


def _public(row: dict, by_role: dict) -> dict:
    """Never includes api_key_enc — list/read responses are secret-free."""
    cid = row.get("config_id")
    return {
        "id": cid, "config_id": cid,
        "label": row.get("label"), "role": row.get("role"),
        "base_url": row.get("base_url"), "model": row.get("model"),
        "key_hint": row.get("key_hint"), "params": row.get("params") or {},
        "created_at": row.get("created_at"), "updated_at": row.get("updated_at"),
        "is_active_per_role": [r for r, v in by_role.items() if v == cid],
    }


async def _active_map(db) -> dict:
    doc = await db.llm_active.find_one({"_id": "singleton"})
    by_role = (doc or {}).get("active_by_role") or {}
    return {"chat": by_role.get("chat"), "vision": by_role.get("vision")}


async def list_configs(db) -> list:
    by_role = await _active_map(db)
    cur = db.llm_configs.find({}, {"_id": 0}).sort("created_at", -1)
    return [_public(row, by_role) async for row in cur]


async def create_config(db, *, label: str, role: str, base_url: str,
                          model: str, api_key: str, params: Optional[dict] = None) -> dict:
    if role not in VALID_ROLES:
        raise ValueError("invalid_role")
    if not (label and base_url and model and api_key):
        raise ValueError("missing_required_field")
    doc = {
        "config_id": uuid.uuid4().hex,
        "label": label, "role": role, "base_url": base_url, "model": model,
        "api_key_enc": encrypt_key(api_key), "key_hint": key_hint(api_key),
        "params": params or {},
        "created_at": time.time(), "updated_at": time.time(),
    }
    await db.llm_configs.insert_one(doc)
    return _public(doc, await _active_map(db))


async def update_config(db, config_id: str, *, label=None, role=None,
                          base_url=None, model=None, api_key=None,
                          params=None) -> Optional[dict]:
    existing = await db.llm_configs.find_one({"config_id": config_id})
    if not existing:
        return None
    if role is not None and role not in VALID_ROLES:
        raise ValueError("invalid_role")
    updates = {"updated_at": time.time()}
    if label is not None:
        updates["label"] = label
    if role is not None:
        updates["role"] = role
    if base_url is not None:
        updates["base_url"] = base_url
    if model is not None:
        updates["model"] = model
    if params is not None:
        updates["params"] = params
    # Empty/None api_key = KEEP CURRENT KEY. Only a non-empty new value
    # rotates it — never blow away a working key on a blank edit.
    if api_key:
        updates["api_key_enc"] = encrypt_key(api_key)
        updates["key_hint"] = key_hint(api_key)
    await db.llm_configs.update_one({"config_id": config_id}, {"$set": updates})
    _bump_cache()
    row = await db.llm_configs.find_one({"config_id": config_id})
    return _public(row, await _active_map(db))


async def delete_config(db, config_id: str) -> bool:
    res = await db.llm_configs.delete_one({"config_id": config_id})
    if res.deleted_count:
        by_role = await _active_map(db)
        changed = {r: v for r, v in by_role.items() if v != config_id}
        if changed != by_role:
            await db.llm_active.update_one(
                {"_id": "singleton"}, {"$set": {"active_by_role": changed}},
                upsert=True)
        _bump_cache()
    return bool(res.deleted_count)


async def set_active(db, config_id: str, role: str) -> dict:
    if role not in VALID_ROLES:
        raise ValueError("invalid_role")
    cfg = await db.llm_configs.find_one({"config_id": config_id})
    if not cfg:
        raise ValueError("config_not_found")
    if cfg["role"] != "any" and cfg["role"] != role and role != "any":
        raise ValueError("role_mismatch")
    by_role = await _active_map(db)
    # 'any' fills both slots; a specific role only fills that one —
    # at most one active config per role, activating de-activates
    # whatever was there before.
    roles_to_set = ["chat", "vision"] if role == "any" else [role]
    for r in roles_to_set:
        by_role[r] = config_id
    await db.llm_active.update_one(
        {"_id": "singleton"}, {"$set": {"active_by_role": by_role}}, upsert=True)
    _bump_cache()
    return by_role


async def get_active_config(db, role: str) -> Optional[dict]:
    """Cached ~60s. Returns a decrypted-ready dict or None (env fallback
    applies at the call site)."""
    now = time.time()
    if now - _cache.get("ts", 0.0) < _CACHE_TTL_S and role in ("chat", "vision"):
        cached = _cache.get(role)
        if cached is not None:
            return cached
    by_role = await _active_map(db)
    cid = by_role.get(role)
    result = None
    if cid:
        cfg = await db.llm_configs.find_one({"config_id": cid})
        if cfg:
            try:
                result = {
                    "config_id": cfg["config_id"], "label": cfg["label"],
                    "base_url": cfg["base_url"], "model": cfg["model"],
                    "api_key": decrypt_key(cfg["api_key_enc"]),
                    "params": cfg.get("params") or {},
                }
            except EncryptionNotConfigured as e:
                logger.error("llm_config_store: decrypt failed, falling back to env: %s", e)
    _cache[role] = result
    _cache["ts"] = now
    return result


async def test_config(cfg: dict) -> dict:
    """One minimal real call (max_tokens=1, prompt 'hi') against the
    config's OWN base_url/model/key. Never logs or returns the key —
    on failure, only a human-categorized reason."""
    import httpx
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"{cfg['base_url'].rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {cfg['api_key']}"},
                json={"model": cfg["model"],
                      "messages": [{"role": "user", "content": "hi"}],
                      "max_tokens": 1},
            )
        latency_ms = int((time.time() - t0) * 1000)
        if r.status_code == 200:
            data = r.json()
            out_tokens = (data.get("usage") or {}).get("completion_tokens")
            return {"ok": True, "latency_ms": latency_ms, "out_tokens": out_tokens}
        reason = ("auth" if r.status_code in (401, 403)
                  else "model_not_found" if r.status_code == 404
                  else f"http_{r.status_code}")
        return {"ok": False, "latency_ms": latency_ms, "error": reason}
    except httpx.TimeoutException:
        return {"ok": False, "latency_ms": int((time.time() - t0) * 1000),
                "error": "network_timeout"}
    except Exception:                                              # noqa: BLE001
        return {"ok": False, "latency_ms": int((time.time() - t0) * 1000),
                "error": "network_error"}
