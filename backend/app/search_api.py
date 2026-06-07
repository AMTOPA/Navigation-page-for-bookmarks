import hashlib
import json
from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .ai_service import get_routed_model, stream_chat_completion
from .db import SessionLocal, get_db
from .models import AISearchCache, Bookmark, Job, JobBatch, utcnow
from .schemas import AISearchRequest
from .search_service import current_index_revision, hybrid_search, search_status
from .security import require_read, require_session, require_session_write
from .services import create_job_batch, queue_job


router = APIRouter(prefix="/api")


@router.get("/search")
def search(
    q: str = Query(min_length=1, max_length=500),
    mode: str = Query(default="auto", pattern="^(auto|lexical|semantic)$"),
    limit: int = Query(default=40, ge=1, le=100),
    group_by: str = Query(default="", pattern="^(|category|folder|importance)$"),
    group: str = Query(default="", max_length=300),
    _principal: dict = Depends(require_read),
    db: Session = Depends(get_db),
):
    result = hybrid_search(db, q, mode=mode, limit=limit, group_by=group_by, group=group)
    db.commit()
    return result


def _event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(jsonable_encoder(data), ensure_ascii=False)}\n\n"


def _ai_cache_key(
    query: str,
    context: str,
    group_by: str,
    group: str,
    model_id: str,
    embedding_id: str,
    revision: str,
) -> str:
    value = "\n".join((query.strip().lower(), context.strip(), group_by, group, model_id, embedding_id, revision))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@router.post("/ai-search/stream")
def ai_search_stream(
    payload: AISearchRequest,
    _principal: dict = Depends(require_read),
    db: Session = Depends(get_db),
):
    provider, model = get_routed_model(db, "ai_search")
    try:
        _embedding_provider, embedding_model = get_routed_model(db, "embedding")
        embedding_id = embedding_model.id
    except ValueError:
        embedding_id = ""
    revision = current_index_revision(db)
    key = _ai_cache_key(
        payload.query,
        payload.context,
        payload.group_by,
        payload.group,
        model.id,
        embedding_id,
        revision,
    )
    cached = db.scalar(
        select(AISearchCache).where(
            AISearchCache.cache_key == key,
            AISearchCache.expires_at > utcnow(),
        )
    )
    result = hybrid_search(
        db,
        payload.query,
        mode="semantic",
        limit=payload.limit,
        group_by=payload.group_by if payload.group else "",
        group=payload.group,
    )
    db.commit()
    result_ids = [item["id"] for item in result["items"]]
    suggestions = list(
        dict.fromkeys(
            [
                item["final_category"]
                for item in result["items"]
                if item.get("final_category") and item["final_category"] != "未分类"
            ]
            + [
                tag
                for item in result["items"][:8]
                for tag in item.get("tags", [])[:2]
            ]
        )
    )[:6]

    async def generate():
        yield _event(
            "results",
            {
                "items": result["items"],
                "suggestions": cached.suggestions if cached else suggestions,
                "semantic_used": result["semantic_used"],
                "query_cache_hit": result["query_cache_hit"],
                "answer_cache_hit": bool(cached),
            },
        )
        if cached:
            yield _event("token", {"text": cached.answer})
            yield _event("done", {"cached": True})
            return
        context_rows = "\n".join(
            f"- {item['title']} | 分类：{item['final_category']} | "
            f"标签：{'、'.join(item.get('tags', []))} | "
            f"简介：{(item.get('description') or item.get('summary') or '')[:300]} | URL：{item['url']}"
            for item in result["items"][:20]
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "你是私人收藏导航的搜索助手。只能根据给出的收藏候选回答，"
                    "先概括最相关内容，再指出用户可如何缩小范围。不要虚构不存在的链接，"
                    "不要输出冗长文章，使用简洁中文。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"用户要找：{payload.query}\n"
                    f"补充描述：{payload.context or '无'}\n"
                    f"限定组别：{payload.group or '全部'}\n"
                    f"语义检索候选：\n{context_rows or '没有找到候选'}"
                ),
            },
        ]
        answer_parts = []
        try:
            async for token in stream_chat_completion(provider, model, messages):
                answer_parts.append(token)
                yield _event("token", {"text": token})
            answer = "".join(answer_parts).strip()
            with SessionLocal() as cache_db:
                cache_db.execute(delete(AISearchCache).where(AISearchCache.cache_key == key))
                cache_db.add(
                    AISearchCache(
                        cache_key=key,
                        normalized_query=payload.query.strip().lower(),
                        group_filter=payload.group,
                        model_id=model.id,
                        embedding_model_id=embedding_id or None,
                        index_revision=revision,
                        answer=answer,
                        suggestions=suggestions,
                        result_ids=result_ids,
                        expires_at=utcnow() + timedelta(days=30),
                    )
                )
                cache_db.commit()
            yield _event("done", {"cached": False})
        except Exception as exc:
            yield _event("error", {"message": f"AI 搜索暂不可用：{str(exc)[:200]}"})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/search/status")
def status(_principal: dict = Depends(require_session), db: Session = Depends(get_db)):
    return search_status(db)


@router.post("/search/rebuild")
def rebuild(principal: dict = Depends(require_session_write), db: Session = Depends(get_db)):
    batch = create_job_batch(db, "重建全部搜索索引", "search_index", principal["id"])
    ids = db.scalars(select(Bookmark.id).where(Bookmark.deleted_at.is_(None))).all()
    for bookmark_id in ids:
        queue_job(db, bookmark_id, "search_index", force=True, batch_id=batch.id)
    db.commit()
    return {"ok": True, "batch_id": batch.id, "count": len(ids)}


@router.get("/jobs/progress")
def progress(_principal: dict = Depends(require_read), db: Session = Depends(get_db)):
    batches = db.scalars(select(JobBatch).order_by(JobBatch.created_at.desc()).limit(8)).all()
    result = []
    for batch in batches:
        counts = dict(
            db.execute(
                select(Job.status, func.count(Job.id)).where(Job.batch_id == batch.id).group_by(Job.status)
            ).all()
        )
        total = sum(counts.values())
        completed = counts.get("success", 0) + counts.get("failed", 0)
        result.append(
            {
                "id": batch.id,
                "name": batch.name,
                "job_type": batch.job_type,
                "total": total,
                "queued": counts.get("queued", 0),
                "running": counts.get("running", 0),
                "success": counts.get("success", 0),
                "failed": counts.get("failed", 0),
                "blocked": counts.get("blocked", 0),
                "percent": round(completed * 100 / total) if total else 100,
                "created_at": batch.created_at,
            }
        )
    unbatched = dict(
        db.execute(
            select(Job.status, func.count(Job.id)).where(Job.batch_id.is_(None)).group_by(Job.status)
        ).all()
    )
    return {"batches": result, "unbatched": unbatched}
