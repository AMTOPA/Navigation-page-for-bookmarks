import hashlib
import math
import struct
from datetime import timedelta

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.orm import Session, selectinload

from .ai_service import embedding, get_routed_model
from .models import Bookmark, BookmarkSource, SearchIndex, SearchQueryCache, utcnow
from .services import bookmark_compact_dict


INDEX_VERSION = 1


def searchable_text(bookmark: Bookmark) -> str:
    values = [
        bookmark.title,
        bookmark.url,
        bookmark.final_category,
        " ".join(bookmark.tags or []),
        bookmark.description,
        bookmark.summary,
        " ".join(bookmark.keywords or []),
        bookmark.notes,
    ]
    return "\n".join(value.strip() for value in values if value and value.strip())[:30_000]


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def pack_vector(values: list[float]) -> bytes:
    return struct.pack(f"<{len(values)}f", *values)


def unpack_vector(value: bytes) -> tuple[float, ...]:
    return struct.unpack(f"<{len(value) // 4}f", value)


def cosine(left, right) -> float:
    if len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norm if norm else 0.0


def ensure_fts(db: Session) -> None:
    db.execute(
        text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS bookmark_fts USING fts5("
            "bookmark_id UNINDEXED, title, url, category, tags, description, summary, keywords, notes)"
        )
    )
    try:
        db.execute(
            text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS bookmark_fts_trigram USING fts5("
                "bookmark_id UNINDEXED, content, tokenize='trigram')"
            )
        )
    except Exception:
        pass


def update_fts(db: Session, bookmark: Bookmark) -> None:
    ensure_fts(db)
    db.execute(text("DELETE FROM bookmark_fts WHERE bookmark_id = :id"), {"id": bookmark.id})
    try:
        db.execute(text("DELETE FROM bookmark_fts_trigram WHERE bookmark_id = :id"), {"id": bookmark.id})
    except Exception:
        pass
    if bookmark.deleted_at:
        return
    db.execute(
        text(
            "INSERT INTO bookmark_fts(bookmark_id,title,url,category,tags,description,summary,keywords,notes) "
            "VALUES (:id,:title,:url,:category,:tags,:description,:summary,:keywords,:notes)"
        ),
        {
            "id": bookmark.id,
            "title": bookmark.title,
            "url": bookmark.url,
            "category": bookmark.final_category,
            "tags": " ".join(bookmark.tags or []),
            "description": bookmark.description,
            "summary": bookmark.summary,
            "keywords": " ".join(bookmark.keywords or []),
            "notes": bookmark.notes,
        },
    )
    try:
        db.execute(
            text("INSERT INTO bookmark_fts_trigram(bookmark_id,content) VALUES (:id,:content)"),
            {"id": bookmark.id, "content": searchable_text(bookmark)},
        )
    except Exception:
        pass


def index_bookmark(db: Session, bookmark: Bookmark) -> dict:
    update_fts(db, bookmark)
    if bookmark.deleted_at:
        db.execute(
            SearchIndex.__table__.update()
            .where(SearchIndex.bookmark_id == bookmark.id)
            .values(active=False)
        )
        return {"fts": True, "embedding": False, "deleted": True}
    try:
        provider, model = get_routed_model(db, "embedding")
    except ValueError:
        return {"fts": True, "embedding": False, "reason": "未配置 Embedding 模型"}
    content = searchable_text(bookmark)
    digest = text_hash(content)
    existing = db.scalar(
        select(SearchIndex).where(
            SearchIndex.bookmark_id == bookmark.id,
            SearchIndex.model_id == model.id,
            SearchIndex.index_version == INDEX_VERSION,
        )
    )
    if existing and existing.text_hash == digest and existing.active:
        return {"fts": True, "embedding": True, "unchanged": True}
    vector = embedding(provider, model, [content])[0]
    if not existing:
        existing = SearchIndex(
            bookmark_id=bookmark.id,
            model_id=model.id,
            text_hash=digest,
            vector=pack_vector(vector),
            dimensions=len(vector),
            index_version=INDEX_VERSION,
        )
        db.add(existing)
    else:
        existing.text_hash = digest
        existing.vector = pack_vector(vector)
        existing.dimensions = len(vector)
        existing.active = True
        existing.error = ""
    return {"fts": True, "embedding": True, "dimensions": len(vector)}


def lexical_ids(db: Session, query: str, limit: int) -> list[tuple[str, float]]:
    ensure_fts(db)
    safe_query = " ".join(f'"{token.replace(chr(34), "")}"' for token in query.split() if token)
    if not safe_query:
        return []
    rows = []
    compact_query = query.strip()
    if len(compact_query) >= 3:
        try:
            rows = db.execute(
                text(
                    "SELECT bookmark_id, bm25(bookmark_fts_trigram) AS rank "
                    "FROM bookmark_fts_trigram WHERE content MATCH :query ORDER BY rank LIMIT :limit"
                ),
                {"query": f'"{compact_query.replace(chr(34), "")}"', "limit": limit},
            ).all()
        except Exception:
            rows = []
    try:
        if not rows:
            rows = db.execute(
                text(
                    "SELECT bookmark_id, bm25(bookmark_fts, 0, 5, 3, 2, 2, 1, 1, 1, 1) AS rank "
                    "FROM bookmark_fts WHERE bookmark_fts MATCH :query ORDER BY rank LIMIT :limit"
                ),
                {"query": safe_query, "limit": limit},
            ).all()
    except Exception:
        pass
    results = [(row[0], 1.0 / (1.0 + abs(float(row[1])))) for row in rows]
    existing_ids = {item[0] for item in results}
    if len(results) < limit:
        pattern = f"%{query.strip()}%"
        fallback_ids = db.scalars(
            select(Bookmark.id)
            .where(
                Bookmark.deleted_at.is_(None),
                or_(
                    Bookmark.title.ilike(pattern),
                    Bookmark.url.ilike(pattern),
                    Bookmark.description.ilike(pattern),
                    Bookmark.manual_category.ilike(pattern),
                    Bookmark.ai_category.ilike(pattern),
                    Bookmark.summary.ilike(pattern),
                    Bookmark.notes.ilike(pattern),
                ),
            )
            .limit(limit)
        ).all()
        for bookmark_id in fallback_ids:
            if bookmark_id not in existing_ids:
                results.append((bookmark_id, 0.65))
    return results[:limit]


def query_vector(db: Session, query: str, model_id: str, provider, model):
    digest = text_hash(query.strip().lower())
    cached = db.scalar(
        select(SearchQueryCache).where(
            SearchQueryCache.query_hash == digest,
            SearchQueryCache.model_id == model_id,
            SearchQueryCache.expires_at > utcnow(),
        )
    )
    if cached:
        return unpack_vector(cached.vector), True
    vector = embedding(provider, model, [query])[0]
    db.execute(
        delete(SearchQueryCache).where(
            SearchQueryCache.query_hash == digest,
            SearchQueryCache.model_id == model_id,
        )
    )
    db.add(
        SearchQueryCache(
            query_hash=digest,
            model_id=model_id,
            vector=pack_vector(vector),
            dimensions=len(vector),
            expires_at=utcnow() + timedelta(days=30),
        )
    )
    return vector, False


def _matches_group(bookmark: Bookmark, group_by: str, group: str) -> bool:
    if not group:
        return True
    if group_by == "category":
        return bookmark.final_category == group
    if group_by == "importance":
        return bookmark.importance == group
    if group_by == "folder":
        return any(source.is_active and source.folder_path == group for source in bookmark.sources)
    return True


def current_index_revision(db: Session) -> str:
    count, latest = db.execute(
        select(func.count(Bookmark.id), func.max(Bookmark.updated_at)).where(Bookmark.deleted_at.is_(None))
    ).one()
    source_latest = db.scalar(
        select(func.max(BookmarkSource.updated_at))
        .join(Bookmark, Bookmark.id == BookmarkSource.bookmark_id)
        .where(Bookmark.deleted_at.is_(None), BookmarkSource.is_active.is_(True))
    )
    return text_hash(f"{count}:{latest}:{source_latest}")[:24]


def hybrid_search(
    db: Session,
    query: str,
    mode: str = "auto",
    limit: int = 40,
    group_by: str = "",
    group: str = "",
) -> dict:
    candidate_limit = max(limit * 4, 100) if group else limit
    lexical = lexical_ids(db, query, candidate_limit)
    use_semantic = mode == "semantic" or (mode == "auto" and len(lexical) < 3)
    scores = {bookmark_id: score * 0.55 for bookmark_id, score in lexical}
    semantic_used = False
    cache_hit = False
    error = ""
    if use_semantic:
        try:
            provider, model = get_routed_model(db, "embedding")
            total_bookmarks = db.scalar(
                select(func.count(Bookmark.id)).where(Bookmark.deleted_at.is_(None))
            ) or 0
            index_count = db.scalar(
                select(func.count(func.distinct(SearchIndex.bookmark_id))).where(
                    SearchIndex.model_id == model.id,
                    SearchIndex.active.is_(True),
                )
            )
            if index_count < total_bookmarks:
                fallback_model_id = db.scalar(
                    select(SearchIndex.model_id)
                    .where(SearchIndex.active.is_(True))
                    .group_by(SearchIndex.model_id)
                    .order_by(func.count(SearchIndex.id).desc())
                    .limit(1)
                )
                if fallback_model_id and fallback_model_id != model.id:
                    from .models import AIModel
                    fallback_model = db.scalar(
                        select(AIModel).where(AIModel.id == fallback_model_id).options(selectinload(AIModel.provider))
                    )
                    if fallback_model and fallback_model.enabled and fallback_model.provider.enabled:
                        model = fallback_model
                        provider = fallback_model.provider
            vector, cache_hit = query_vector(db, query, model.id, provider, model)
            indexes = db.scalars(
                select(SearchIndex).where(
                    SearchIndex.model_id == model.id,
                    SearchIndex.active.is_(True),
                    SearchIndex.index_version == INDEX_VERSION,
                )
            ).all()
            for item in indexes:
                similarity = cosine(vector, unpack_vector(item.vector))
                if similarity > 0.15:
                    scores[item.bookmark_id] = scores.get(item.bookmark_id, 0) + similarity * 0.75
            semantic_used = True
        except Exception as exc:
            error = str(exc)[:300]
    ordered_ids = [item[0] for item in sorted(scores.items(), key=lambda item: item[1], reverse=True)[:candidate_limit]]
    if not ordered_ids:
        return {"items": [], "semantic_used": semantic_used, "query_cache_hit": cache_hit, "fallback_error": error}
    bookmarks = db.scalars(
        select(Bookmark).where(Bookmark.id.in_(ordered_ids), Bookmark.deleted_at.is_(None)).options(selectinload(Bookmark.sources))
    ).all()
    by_id = {item.id: item for item in bookmarks}
    selected = [
        by_id[item_id]
        for item_id in ordered_ids
        if item_id in by_id and _matches_group(by_id[item_id], group_by, group)
    ][:limit]
    return {
        "items": [bookmark_compact_dict(item) for item in selected],
        "semantic_used": semantic_used,
        "query_cache_hit": cache_hit,
        "fallback_error": error,
        "index_revision": current_index_revision(db),
    }


def search_status(db: Session) -> dict:
    ensure_fts(db)
    total = db.scalar(select(func.count(Bookmark.id)).where(Bookmark.deleted_at.is_(None))) or 0
    fts_indexed = db.execute(text("SELECT count(*) FROM bookmark_fts")).scalar_one()
    indexed = db.scalar(
        select(func.count(func.distinct(SearchIndex.bookmark_id))).where(SearchIndex.active.is_(True))
    ) or 0
    failed = db.scalar(select(func.count(SearchIndex.id)).where(SearchIndex.error != "")) or 0
    latest = db.scalar(select(func.max(SearchIndex.updated_at)))
    try:
        _provider, model = get_routed_model(db, "embedding")
        embedding_configured = True
        model_name = model.display_name or model.model_name
    except ValueError:
        embedding_configured = False
        model_name = ""
    return {
        "total": total,
        "fts_indexed": fts_indexed,
        "indexed": indexed,
        "pending": max(total - indexed, 0) if embedding_configured else 0,
        "failed": failed,
        "embedding_configured": embedding_configured,
        "model_name": model_name,
        "last_updated_at": latest,
    }
