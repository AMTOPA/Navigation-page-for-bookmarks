import time

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from .ai_service import ensure_legacy_provider, test_model_connection
from .db import get_db
from .models import AISearchCache, AdminUser, AIModel, AIProvider, AIRoute, Bookmark, SearchIndex, SearchQueryCache, UserSession
from .schemas import AccountUpdate, ModelCreate, ModelTestRequest, ModelUpdate, ProviderCreate, ProviderTestRequest, ProviderUpdate, RouteUpdate
from .security import decrypt_value, encrypt_value, hash_password, require_session, require_session_write, verify_password
from .services import audit, create_job_batch, queue_job


router = APIRouter(prefix="/api")


def provider_dict(provider: AIProvider) -> dict:
    return {
        "id": provider.id,
        "name": provider.name,
        "base_url": provider.base_url,
        "enabled": provider.enabled,
        "api_key_configured": bool(provider.encrypted_api_key),
        "models": [
            {
                "id": model.id,
                "display_name": model.display_name,
                "model_name": model.model_name,
                "capabilities": model.capabilities or [],
                "enabled": model.enabled,
            }
            for model in provider.models
        ],
    }


@router.get("/settings/providers")
def list_providers(_principal: dict = Depends(require_session), db: Session = Depends(get_db)):
    ensure_legacy_provider(db)
    providers = db.scalars(select(AIProvider).options(selectinload(AIProvider.models)).order_by(AIProvider.name)).all()
    routes = {route.task_type: route.model_id for route in db.scalars(select(AIRoute)).all()}
    return {"providers": [provider_dict(item) for item in providers], "routes": routes}


@router.post("/settings/providers")
def create_provider(payload: ProviderCreate, principal: dict = Depends(require_session_write), db: Session = Depends(get_db)):
    if db.scalar(select(AIProvider.id).where(AIProvider.name == payload.name)):
        raise HTTPException(409, "供应商名称已存在")
    item = AIProvider(
        name=payload.name,
        base_url=payload.base_url.rstrip("/"),
        encrypted_api_key=encrypt_value(payload.api_key) if payload.api_key else "",
        enabled=payload.enabled,
    )
    db.add(item)
    audit(db, "provider_created", "创建 AI 供应商", actor=principal["id"], provider=payload.name)
    db.commit()
    return {"id": item.id}


@router.patch("/settings/providers/{provider_id}")
def update_provider(provider_id: str, payload: ProviderUpdate, principal: dict = Depends(require_session_write), db: Session = Depends(get_db)):
    item = db.get(AIProvider, provider_id)
    if not item:
        raise HTTPException(404, "供应商不存在")
    values = payload.model_dump(exclude_unset=True)
    if "api_key" in values:
        key = values.pop("api_key")
        if key and key != "********":
            item.encrypted_api_key = encrypt_value(key)
    for key, value in values.items():
        setattr(item, key, value.rstrip("/") if key == "base_url" else value)
    audit(db, "provider_updated", "更新 AI 供应商", actor=principal["id"], provider_id=provider_id)
    db.commit()
    return {"ok": True}


@router.delete("/settings/providers/{provider_id}")
def delete_provider(provider_id: str, principal: dict = Depends(require_session_write), db: Session = Depends(get_db)):
    item = db.scalar(select(AIProvider).where(AIProvider.id == provider_id).options(selectinload(AIProvider.models)))
    if not item:
        raise HTTPException(404, "供应商不存在")
    model_ids = [model.id for model in item.models]
    if model_ids and db.scalar(select(AIRoute.task_type).where(AIRoute.model_id.in_(model_ids)).limit(1)):
        raise HTTPException(409, "该供应商仍被任务路由使用，请先切换模型")
    if model_ids and db.scalar(select(SearchIndex.id).where(SearchIndex.model_id.in_(model_ids)).limit(1)):
        if db.scalar(select(SearchIndex.id).where(SearchIndex.model_id.in_(model_ids), SearchIndex.active.is_(True)).limit(1)):
            raise HTTPException(409, "该供应商仍有活动搜索索引，请先切换模型并完成重建")
        db.execute(delete(SearchQueryCache).where(SearchQueryCache.model_id.in_(model_ids)))
        db.execute(
            delete(AISearchCache).where(
                AISearchCache.model_id.in_(model_ids) | AISearchCache.embedding_model_id.in_(model_ids)
            )
        )
        db.execute(delete(SearchIndex).where(SearchIndex.model_id.in_(model_ids)))
    db.delete(item)
    audit(db, "provider_deleted", "删除 AI 供应商", actor=principal["id"], provider_id=provider_id)
    db.commit()
    return {"ok": True}


@router.post("/settings/models")
def create_model(payload: ModelCreate, principal: dict = Depends(require_session_write), db: Session = Depends(get_db)):
    if not db.get(AIProvider, payload.provider_id):
        raise HTTPException(404, "供应商不存在")
    item = AIModel(**payload.model_dump())
    db.add(item)
    audit(db, "model_created", "创建 AI 模型", actor=principal["id"], model=payload.model_name)
    db.commit()
    return {"id": item.id}


@router.patch("/settings/models/{model_id}")
def update_model(model_id: str, payload: ModelUpdate, principal: dict = Depends(require_session_write), db: Session = Depends(get_db)):
    item = db.get(AIModel, model_id)
    if not item:
        raise HTTPException(404, "模型不存在")
    old_model_name = item.model_name
    embedding_route = db.get(AIRoute, "embedding")
    new_values = payload.model_dump(exclude_unset=True)
    if (
        embedding_route
        and embedding_route.model_id == item.id
        and "model_name" in new_values
        and new_values["model_name"] != old_model_name
    ):
        raise HTTPException(409, "当前搜索模型不能直接改名；请新增模型并切换搜索路由，以保证旧索引持续可用")
    for key, value in new_values.items():
        setattr(item, key, value)
    audit(db, "model_updated", "更新 AI 模型", actor=principal["id"], model_id=model_id)
    db.commit()
    return {"ok": True}


@router.delete("/settings/models/{model_id}")
def delete_model(model_id: str, principal: dict = Depends(require_session_write), db: Session = Depends(get_db)):
    item = db.get(AIModel, model_id)
    if not item:
        raise HTTPException(404, "模型不存在")
    if db.scalar(select(AIRoute.task_type).where(AIRoute.model_id == model_id).limit(1)):
        raise HTTPException(409, "该模型仍被任务路由使用")
    if db.scalar(select(SearchIndex.id).where(SearchIndex.model_id == model_id, SearchIndex.active.is_(True)).limit(1)):
        raise HTTPException(409, "该模型仍有活动搜索索引")
    db.execute(delete(SearchQueryCache).where(SearchQueryCache.model_id == model_id))
    db.execute(
        delete(AISearchCache).where(
            (AISearchCache.model_id == model_id) | (AISearchCache.embedding_model_id == model_id)
        )
    )
    db.execute(delete(SearchIndex).where(SearchIndex.model_id == model_id))
    db.delete(item)
    audit(db, "model_deleted", "删除 AI 模型", actor=principal["id"], model_id=model_id)
    db.commit()
    return {"ok": True}


@router.put("/settings/routes")
def update_routes(payload: RouteUpdate, principal: dict = Depends(require_session_write), db: Session = Depends(get_db)):
    old_embedding = (db.get(AIRoute, "embedding") or AIRoute()).model_id
    for task_type, model_id in payload.routes.items():
        if model_id and not db.get(AIModel, model_id):
            raise HTTPException(404, f"{task_type} 模型不存在")
        route = db.get(AIRoute, task_type) or AIRoute(task_type=task_type)
        route.model_id = model_id
        db.add(route)
    db.flush()
    new_embedding = (db.get(AIRoute, "embedding") or AIRoute()).model_id
    batch_id = None
    if new_embedding and new_embedding != old_embedding:
        batch = create_job_batch(db, "重建语义搜索索引", "search_index", principal["id"])
        batch_id = batch.id
        for bookmark_id in db.scalars(select(Bookmark.id).where(Bookmark.deleted_at.is_(None))).all():
            queue_job(db, bookmark_id, "search_index", force=True, batch_id=batch.id)
    audit(db, "routes_updated", "更新 AI 任务路由", actor=principal["id"])
    db.commit()
    return {"ok": True, "reindex_batch_id": batch_id}


@router.post("/settings/models/test")
def test_model(payload: ModelTestRequest, _principal: dict = Depends(require_session_write), db: Session = Depends(get_db)):
    provider = db.get(AIProvider, payload.provider_id)
    if not provider:
        raise HTTPException(404, "供应商不存在")
    if payload.api_key and payload.api_key != "********":
        provider.encrypted_api_key = encrypt_value(payload.api_key)
    model = db.get(AIModel, payload.model_id) if payload.model_id else None
    if not model:
        model = AIModel(
            provider_id=provider.id,
            display_name=payload.model_name or "连接测试",
            model_name=payload.model_name or "",
            capabilities=[payload.capability],
        )
        model.provider = provider
    try:
        return test_model_connection(provider, model)
    except Exception as exc:
        raise HTTPException(400, f"连接失败：{str(exc)[:300]}") from None


@router.post("/settings/providers/test")
def test_provider(payload: ProviderTestRequest, _principal: dict = Depends(require_session_write), db: Session = Depends(get_db)):
    provider = db.get(AIProvider, payload.provider_id) if payload.provider_id else None
    base_url = (payload.base_url or (provider.base_url if provider else "")).rstrip("/")
    api_key = payload.api_key
    if (not api_key or api_key == "********") and provider and provider.encrypted_api_key:
        api_key = decrypt_value(provider.encrypted_api_key)
    if not base_url or not api_key:
        raise HTTPException(400, "请填写 Base URL 和 API Key")
    started = time.perf_counter()
    try:
        response = httpx.get(
            base_url + "/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=20,
        )
        response.raise_for_status()
        count = len(response.json().get("data", []))
        return {"ok": True, "latency_ms": round((time.perf_counter() - started) * 1000), "models": count}
    except Exception as exc:
        raise HTTPException(400, f"连接失败：{str(exc)[:300]}") from None


@router.get("/settings/account")
def account(principal: dict = Depends(require_session), db: Session = Depends(get_db)):
    user = db.get(AdminUser, principal["id"])
    return {"username": user.username}


@router.put("/settings/account")
def update_account(payload: AccountUpdate, principal: dict = Depends(require_session_write), db: Session = Depends(get_db)):
    user = db.get(AdminUser, principal["id"])
    if not user or not verify_password(user.password_hash, payload.current_password):
        raise HTTPException(403, "当前密码错误")
    duplicate = db.scalar(select(AdminUser.id).where(AdminUser.username == payload.username, AdminUser.id != user.id))
    if duplicate:
        raise HTTPException(409, "用户名已存在")
    user.username = payload.username
    if payload.new_password:
        user.password_hash = hash_password(payload.new_password)
    db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    audit(db, "account_updated", "更新管理员账号", actor=user.username)
    db.commit()
    return {"ok": True, "relogin": True}
