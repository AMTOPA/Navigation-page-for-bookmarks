import json
import re
import time

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .models import AIModel, AIProvider, AIRoute, Setting
from .security import decrypt_value


def parse_json_object(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("AI 未返回 JSON 对象")
    value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("AI 返回格式错误")
    return value


def ensure_legacy_provider(db: Session) -> None:
    if db.scalar(select(AIProvider.id).limit(1)):
        ai_route = db.get(AIRoute, "ai_search")
        if not ai_route or not ai_route.model_id:
            default_route = db.get(AIRoute, "default")
            if default_route and default_route.model_id:
                if ai_route:
                    ai_route.model_id = default_route.model_id
                else:
                    db.add(AIRoute(task_type="ai_search", model_id=default_route.model_id))
                db.commit()
        return
    values = {item.key: item.value for item in db.scalars(select(Setting)).all()}
    encrypted_key = values.get("ai_api_key", "")
    provider = AIProvider(
        name="默认供应商",
        base_url=values.get("ai_base_url", "https://open.bigmodel.cn/api/paas/v4"),
        encrypted_api_key=encrypted_key,
    )
    db.add(provider)
    db.flush()
    model = AIModel(
        provider_id=provider.id,
        display_name=values.get("ai_model", "glm-4-flash"),
        model_name=values.get("ai_model", "glm-4-flash"),
        capabilities=["chat", "classification", "summary"],
    )
    db.add(model)
    db.flush()
    for task_type in ("default", "classification", "summary", "ai_search"):
        route = db.get(AIRoute, task_type)
        if route:
            if not route.model_id:
                route.model_id = model.id
        else:
            db.add(AIRoute(task_type=task_type, model_id=model.id))
    db.commit()


def get_routed_model(db: Session, task_type: str) -> tuple[AIProvider, AIModel]:
    ensure_legacy_provider(db)
    route = db.get(AIRoute, task_type)
    if task_type == "embedding" and (not route or not route.model_id):
        raise ValueError("尚未配置 Embedding 模型")
    if not route or not route.model_id:
        route = db.get(AIRoute, "default")
    if not route or not route.model_id:
        raise ValueError(f"尚未配置 {task_type} 模型")
    model = db.scalar(
        select(AIModel).where(AIModel.id == route.model_id).options(selectinload(AIModel.provider))
    )
    if not model or not model.enabled or not model.provider.enabled:
        raise ValueError(f"{task_type} 模型已停用或不存在")
    required = {
        "embedding": "embedding",
        "classification": "classification",
        "summary": "summary",
        "ai_search": "chat",
    }.get(task_type)
    if required and required not in (model.capabilities or []):
        raise ValueError(f"所选模型不支持 {required} 能力")
    return model.provider, model


def provider_api_key(provider: AIProvider) -> str:
    return decrypt_value(provider.encrypted_api_key) if provider.encrypted_api_key else ""


def chat_completion(provider: AIProvider, model: AIModel, messages: list[dict], json_mode: bool = False) -> dict:
    api_key = provider_api_key(provider)
    if not api_key:
        raise ValueError("尚未配置 AI API Key")
    payload = {"model": model.model_name, "messages": messages, "temperature": 0.2}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    response = httpx.post(
        provider.base_url.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


async def stream_chat_completion(provider: AIProvider, model: AIModel, messages: list[dict]):
    api_key = provider_api_key(provider)
    if not api_key:
        raise ValueError("尚未配置 AI API Key")
    payload = {
        "model": model.model_name,
        "messages": messages,
        "temperature": 0.2,
        "stream": True,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        async with client.stream(
            "POST",
            provider.base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    value = json.loads(data)
                    content = value["choices"][0].get("delta", {}).get("content", "")
                except (KeyError, IndexError, TypeError, json.JSONDecodeError):
                    continue
                if content:
                    yield content


def embedding(provider: AIProvider, model: AIModel, texts: list[str]) -> list[list[float]]:
    api_key = provider_api_key(provider)
    if not api_key:
        raise ValueError("尚未配置 Embedding API Key")
    response = httpx.post(
        provider.base_url.rstrip("/") + "/embeddings",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": model.model_name, "input": texts},
        timeout=60,
    )
    response.raise_for_status()
    values = sorted(response.json().get("data", []), key=lambda item: item.get("index", 0))
    vectors = [item.get("embedding") for item in values]
    if len(vectors) != len(texts) or any(not isinstance(value, list) or not value for value in vectors):
        raise ValueError("Embedding 接口返回格式错误")
    return vectors


def test_model_connection(provider: AIProvider, model: AIModel) -> dict:
    started = time.perf_counter()
    if "embedding" in (model.capabilities or []):
        vectors = embedding(provider, model, ["连接测试"])
        details = {"dimensions": len(vectors[0])}
    else:
        result = chat_completion(
            provider,
            model,
            [{"role": "user", "content": "请只回复 OK"}],
        )
        details = {"response": str(result["choices"][0]["message"]["content"])[:80]}
    return {"ok": True, "latency_ms": round((time.perf_counter() - started) * 1000), **details}


def analyze_bookmark(db: Session, bookmark, page_text: str = "") -> dict:
    provider, model = get_routed_model(db, "classification")
    prompt = f"""
请分析这个收藏链接，并只返回一个 JSON 对象。字段：
category: 简洁中文分类；
tags: 最多 8 个字符串；
importance: high|medium|normal|low；
summary: 150 字以内摘要；
outline: 字符串数组；
keywords: 字符串数组；
key_info: 对象，可包含 start_date、deadline、event_date、target_users、contact、application_url、requirements。
标题：{bookmark.title}
URL：{bookmark.url}
网页描述：{bookmark.description}
正文（可能截断）：{page_text[:30000]}
"""
    result = chat_completion(
        provider,
        model,
        [
            {"role": "system", "content": "你是书签整理助手，必须输出合法 JSON。"},
            {"role": "user", "content": prompt},
        ],
        json_mode=True,
    )
    value = parse_json_object(result["choices"][0]["message"]["content"])
    if value.get("importance") not in {"high", "medium", "normal", "low"}:
        value["importance"] = "normal"
    for field in ("tags", "outline", "keywords"):
        if not isinstance(value.get(field), list):
            value[field] = []
        value[field] = [str(item)[:200] for item in value[field]][:30]
    if not isinstance(value.get("key_info"), dict):
        value["key_info"] = {}
    return value
