"""
routers/admin_llm_config.py — Admin self-serve LLM provider settings.

  GET    /admin/llm/configs
  POST   /admin/llm/configs
  PUT    /admin/llm/configs/{id}
  DELETE /admin/llm/configs/{id}
  POST   /admin/llm/configs/{id}/set-active
  POST   /admin/llm/configs/{id}/test

All admin-guarded. Secrets are Fernet-encrypted at rest
(services/llm_config_store.py) — never returned, logged, or included
in any error body. See ROADMAP.md / CHANGELOG.md for the wiring into
services/ora_chat_v2/llm_client.py's resolution priority.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from cto_services.auth import require_admin
from cto_services.db import get_db
from services import llm_config_store as store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/llm/configs", tags=["Admin · LLM Settings"])


class CreateConfigBody(BaseModel):
    label: str
    role: str
    base_url: str
    model: str
    api_key: str
    params: Optional[dict] = None


class UpdateConfigBody(BaseModel):
    label: Optional[str] = None
    role: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None  # null/omitted = keep current key
    params: Optional[dict] = None


class SetActiveBody(BaseModel):
    role: str  # 'chat' | 'vision' | 'any'


@router.get("")
async def list_configs(authorization: Optional[str] = Header(None)):
    await require_admin(authorization)
    db = get_db()
    return {"ok": True, "configs": await store.list_configs(db)}


@router.post("")
async def create_config(body: CreateConfigBody,
                         authorization: Optional[str] = Header(None)):
    await require_admin(authorization)
    if body.role not in store.VALID_ROLES:
        raise HTTPException(400, "role must be one of: chat, vision, any")
    db = get_db()
    try:
        cfg = await store.create_config(
            db, label=body.label, role=body.role, base_url=body.base_url,
            model=body.model, api_key=body.api_key, params=body.params)
    except store.EncryptionNotConfigured as e:
        raise HTTPException(503, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "config": cfg}


@router.put("/{config_id}")
async def update_config(config_id: str, body: UpdateConfigBody,
                         authorization: Optional[str] = Header(None)):
    await require_admin(authorization)
    db = get_db()
    try:
        cfg = await store.update_config(
            db, config_id, label=body.label, role=body.role,
            base_url=body.base_url, model=body.model,
            api_key=body.api_key, params=body.params)
    except store.EncryptionNotConfigured as e:
        raise HTTPException(503, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if cfg is None:
        raise HTTPException(404, "config not found")
    return {"ok": True, "config": cfg}


@router.delete("/{config_id}")
async def delete_config(config_id: str,
                         authorization: Optional[str] = Header(None)):
    await require_admin(authorization)
    db = get_db()
    ok = await store.delete_config(db, config_id)
    if not ok:
        raise HTTPException(404, "config not found")
    return {"ok": True}


@router.post("/{config_id}/set-active")
async def set_active_config(config_id: str, body: SetActiveBody,
                             authorization: Optional[str] = Header(None)):
    await require_admin(authorization)
    db = get_db()
    try:
        by_role = await store.set_active(db, config_id, body.role)
    except ValueError as e:
        code = 404 if str(e) == "config_not_found" else 400
        raise HTTPException(code, str(e)) from e
    return {"ok": True, "active_by_role": by_role}


@router.post("/{config_id}/test")
async def test_config(config_id: str,
                       authorization: Optional[str] = Header(None)):
    await require_admin(authorization)
    db = get_db()
    row = await db.llm_configs.find_one({"config_id": config_id})
    if not row:
        raise HTTPException(404, "config not found")
    try:
        decrypted_key = store.decrypt_key(row["api_key_enc"])
    except store.EncryptionNotConfigured as e:
        raise HTTPException(503, str(e)) from e
    result = await store.test_config({
        "base_url": row["base_url"], "model": row["model"], "api_key": decrypted_key,
    })
    return result
