import argparse
from datetime import timedelta
import time

from sqlalchemy import func, select, update

from .ai_service import analyze_bookmark
from .crawler import crawl_url
from .db import SessionLocal
from .media_service import cache_media
from .models import AIRoute, Bookmark, Job, JobBatch, SearchIndex, utcnow
from .search_service import index_bookmark
from .services import audit, queue_job


def apply_if_unlocked(bookmark: Bookmark, field: str, value):
    if field not in set(bookmark.manual_override_fields or []) and value not in (None, "", [], {}):
        setattr(bookmark, field, value)


def process_crawl(db, job: Job, bookmark: Bookmark):
    result = crawl_url(bookmark.url, bookmark.etag, bookmark.last_modified)
    if not result.get("not_modified"):
        apply_if_unlocked(bookmark, "title", result.get("title"))
        apply_if_unlocked(bookmark, "description", result.get("description"))
        apply_if_unlocked(bookmark, "favicon", result.get("favicon"))
        apply_if_unlocked(bookmark, "image", result.get("image"))
        apply_if_unlocked(bookmark, "outline", result.get("outline"))
        bookmark.content_hash = result.get("content_hash", "")
        bookmark.etag = result.get("etag", "")
        bookmark.last_modified = result.get("last_modified", "")
        job.payload = {**(job.payload or {}), "page_text": result.get("text", "")}
    bookmark.is_crawled = True
    bookmark.crawl_status = "success"
    bookmark.crawl_error = ""
    bookmark.last_crawled_at = utcnow()
    queue_job(db, bookmark.id, "media_cache")
    queue_job(db, bookmark.id, "search_index")


def process_ai(db, job: Job, bookmark: Bookmark):
    crawl_job = db.scalar(
        select(Job).where(Job.bookmark_id == bookmark.id, Job.job_type == "crawl").order_by(Job.created_at.desc())
    )
    page_text = (crawl_job.payload or {}).get("page_text", "") if crawl_job else ""
    result = analyze_bookmark(db, bookmark, page_text)
    if "category" not in set(bookmark.manual_override_fields or []):
        bookmark.ai_category = str(result.get("category", ""))[:200]
    if "tags" not in set(bookmark.manual_override_fields or []):
        bookmark.tags = result.get("tags", [])
    if "importance" not in set(bookmark.manual_override_fields or []):
        bookmark.ai_importance = result.get("importance", "normal")
    apply_if_unlocked(bookmark, "summary", str(result.get("summary", "")))
    apply_if_unlocked(bookmark, "outline", result.get("outline", []))
    apply_if_unlocked(bookmark, "keywords", result.get("keywords", []))
    apply_if_unlocked(bookmark, "key_info", result.get("key_info", {}))
    bookmark.is_ai_classified = True
    bookmark.ai_status = "success"
    bookmark.ai_error = ""
    bookmark.last_ai_processed_at = utcnow()
    queue_job(db, bookmark.id, "search_index")


def process_media(db, _job: Job, bookmark: Bookmark):
    cache_media(db, bookmark, "favicon")
    cache_media(db, bookmark, "image")


def finish_batch_if_complete(db, batch_id: str | None):
    if not batch_id:
        return
    active = db.scalar(
        select(func.count(Job.id)).where(Job.batch_id == batch_id, Job.status.in_(["queued", "running", "blocked"]))
    )
    if not active:
        batch = db.get(JobBatch, batch_id)
        if batch and not batch.finished_at:
            batch.finished_at = utcnow()
            if batch.job_type == "search_index":
                route = db.get(AIRoute, "embedding")
                failed = db.scalar(
                    select(func.count(Job.id)).where(Job.batch_id == batch_id, Job.status == "failed")
                )
                if route and route.model_id and not failed:
                    db.execute(
                        SearchIndex.__table__.update()
                        .where(SearchIndex.model_id != route.model_id)
                        .values(active=False)
                    )


def process_one() -> bool:
    with SessionLocal() as db:
        stale_before = utcnow() - timedelta(minutes=30)
        db.execute(
            update(Job)
            .where(Job.status == "running", Job.started_at < stale_before)
            .values(status="queued", error="任务执行超时，已自动回收")
        )
        candidate_id = db.scalar(select(Job.id).where(Job.status == "queued").order_by(Job.created_at).limit(1))
        if not candidate_id:
            db.commit()
            return False
        claimed_id = db.scalar(
            update(Job)
            .where(Job.id == candidate_id, Job.status == "queued")
            .values(status="running", started_at=utcnow(), attempts=Job.attempts + 1)
            .returning(Job.id)
        )
        db.commit()
        if not claimed_id:
            return True
        job = db.get(Job, claimed_id)
        bookmark = db.get(Bookmark, job.bookmark_id) if job.bookmark_id else None
        try:
            if not bookmark:
                raise ValueError("任务关联的收藏不存在")
            if job.job_type == "crawl":
                process_crawl(db, job, bookmark)
            elif job.job_type == "ai":
                process_ai(db, job, bookmark)
            elif job.job_type == "search_index":
                index_bookmark(db, bookmark)
            elif job.job_type == "media_cache":
                process_media(db, job, bookmark)
            else:
                raise ValueError(f"未知任务类型：{job.job_type}")
            job.status = "success"
            job.finished_at = utcnow()
            audit(db, f"{job.job_type}_success", f"{job.job_type} 处理成功", bookmark_id=bookmark.id)
        except Exception as exc:
            job.error = str(exc)[:4000]
            if job.job_type in {"ai", "search_index"} and "尚未配置" in job.error:
                job.status = "blocked"
            else:
                job.status = "queued" if job.attempts < job.max_attempts else "failed"
            job.finished_at = utcnow()
            if bookmark:
                if job.job_type == "crawl":
                    bookmark.crawl_status = "failed"
                    bookmark.crawl_error = job.error
                elif job.job_type == "ai":
                    bookmark.ai_status = "failed"
                    bookmark.ai_error = job.error
            audit(db, f"{job.job_type}_failed", f"{job.job_type} 处理失败", level="error", error=job.error)
        finish_batch_if_complete(db, job.batch_id)
        db.commit()
        return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    while True:
        processed = process_one()
        if args.once:
            break
        if not processed:
            time.sleep(3)


if __name__ == "__main__":
    main()
